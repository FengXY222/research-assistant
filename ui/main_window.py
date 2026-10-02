from __future__ import annotations

import ctypes
import importlib
import os
import sys
import time
from collections.abc import Callable
from datetime import date, timedelta
from uuid import uuid4

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QParallelAnimationGroup, QPropertyAnimation, Property, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QCursor, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ui.home_page import HomePage
from ui.icons import lucide_icon
from ui.theme import apply_application_theme
from ui.workbench_shell import WorkbenchShell, resolve_route
from utils.app_info import APP_VERSION
from utils.global_hotkey import GlobalHotkeyManager, VK_SPACE
from utils.file_manager import (
    append_runtime_log,
    load_app_settings,
    load_dismissed_reminders,
    load_reminder_state,
    load_papers,
    maybe_create_daily_backup,
    save_app_settings,
    save_dismissed_reminders,
    save_papers,
    save_reminder_state,
)
from utils.submission_reminders import due_ready_submission_reminders, due_submission_reminders
from utils.special_issue_repository import (
    add_special_issue_journal_to_library,
    add_special_issue_to_submission_path,
    associate_special_issue,
    create_special_issue_preparation_task,
    load_special_issue_overview,
    mark_special_issue_notifications_sent,
    set_special_issue_scope_note,
    set_special_issue_status,
)
from utils.window_mode import mode_minimum_size, mode_window_key, normalize_application_mode, should_hide_to_tray


def _main_window_flags(mode: str, always_on_top: bool, click_through: bool) -> Qt.WindowType:
    """Return mode-safe Qt flags without losing the taskbar profile."""
    normalized_mode = normalize_application_mode(mode)
    flags = Qt.WindowType.FramelessWindowHint
    flags |= Qt.WindowType.Tool if normalized_mode == "widget" else Qt.WindowType.Window
    if always_on_top:
        flags |= Qt.WindowType.WindowStaysOnTopHint
    if click_through and normalized_mode == "widget":
        flags |= Qt.WindowType.WindowTransparentForInput
    return flags


class DragBar(QFrame):
    """Borderless title bar that lets the user move the desktop widget."""

    def __init__(self, parent: QMainWindow) -> None:
        super().__init__(parent)
        self._drag_offset = QPoint()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if getattr(self.window(), "widget_locked", False):
                event.ignore()
                return
            self._drag_offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            not getattr(self.window(), "widget_locked", False)
            and event.buttons() & Qt.MouseButton.LeftButton
            and not self._drag_offset.isNull()
        ):
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = QPoint()
        super().mouseReleaseEvent(event)


NAVIGATION_ITEMS = (
    ("home", "home", "概览", "home"),
    ("work", "work", "工作", "list-todo"),
    ("papers", "papers", "论文", "file-text"),
    ("library", "library", "文献", "library-big"),
    ("tools", "tools", "工具", "file-text"),
)

LEGACY_PAGE_ROUTES = {
    0: "home",
    1: "todo",
    2: "papers",
    3: "notes",
    4: "journals",
    5: "frontier",
    6: "achievements",
}

LAZY_PAGE_TYPES = {
    "todo": ("ui.todo_page", "TodoPage"),
    "papers": ("ui.paper_page", "PaperPage"),
    "notes": ("ui.notes_page", "NotesPage"),
    "journals": ("ui.journal_library_page", "JournalLibraryPage"),
    "frontier": ("ui.frontier_page", "DailyFrontierPage"),
    "special_issues": ("ui.special_issue_page", "SpecialIssuePage"),
    "achievements": ("ui.achievements_page", "AchievementsPage"),
    "tools": ("ui.tools_page", "ToolsPage"),
}


class LazyPagePlaceholder(QWidget):
    """Responsive stand-in while a page module is imported in the background."""

    retry_requested = Signal()

    def __init__(self, label: str) -> None:
        super().__init__()
        self.page_label = label
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addStretch(1)
        self.message = QLabel(f"正在准备{label}…")
        self.message.setObjectName("settingsHint")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.message)
        self.retry_button = QPushButton("重试")
        self.retry_button.setObjectName("secondaryButton")
        self.retry_button.setFixedWidth(88)
        self.retry_button.clicked.connect(lambda _checked=False: self.retry_requested.emit())
        self.retry_button.hide()
        layout.addWidget(self.retry_button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch(1)

    def show_waiting(self, message: str) -> None:
        self.message.setText(message)
        self.retry_button.hide()

    def show_error(self, message: str, *, retry: bool = True) -> None:
        self.message.setText(f"暂时无法打开：{message}")
        self.retry_button.setVisible(retry)


class PageImportThread(QThread):
    """Import one optional page module away from the GUI event loop."""

    ready = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, page_key: str, module_name: str, class_name: str) -> None:
        super().__init__()
        self.page_key = page_key
        self.module_name = module_name
        self.class_name = class_name

    def run(self) -> None:
        try:
            module = importlib.import_module(self.module_name)
            self.ready.emit(self.page_key, getattr(module, self.class_name))
        except Exception as error:  # noqa: BLE001 - surface optional page failures in-place
            self.failed.emit(self.page_key, str(error))


class IdleBackupThread(QThread):
    """Create the due snapshot without occupying the GUI thread."""

    completed = Signal()
    failed = Signal(str)

    def __init__(self, settings: dict) -> None:
        super().__init__()
        self.settings = dict(settings)

    def run(self) -> None:
        try:
            maybe_create_daily_backup(
                self.settings,
                cancelled=self.isInterruptionRequested,
            )
            self.completed.emit()
        except InterruptedError:
            return
        except Exception as error:  # noqa: BLE001 - backup failures must not close the app
            self.failed.emit(str(error))


class IdleStorageMaintenanceThread(QThread):
    """Prune and compact SQLite stores only after the application is idle."""

    completed = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            from utils import file_manager
            from utils.database_maintenance import run_database_maintenance

            result = run_database_maintenance(
                file_manager.RESEARCH_INTELLIGENCE_CACHE_FILE,
                file_manager.BUSINESS_DATA_FILE,
                cancelled=self.isInterruptionRequested,
            )
            self.completed.emit(result)
        except InterruptedError:
            return
        except Exception as error:  # noqa: BLE001 - maintenance cannot prevent normal use
            self.failed.emit(str(error))


class SpecialIssueActionThread(QThread):
    """Run a potentially large cross-record action outside the GUI thread."""

    completed = Signal()
    failed = Signal(str)

    def __init__(self, action: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._action = action

    def run(self) -> None:
        try:
            self._action()
        except Exception as error:  # noqa: BLE001 - report through the workbench
            self.failed.emit(str(error))
            return
        self.completed.emit()


class VerticalNavButton(QPushButton):
    """Compatibility name for the v13.1 horizontal icon-and-Chinese nav."""

    def __init__(self, text: str, page_index: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.page_index = page_index
        self.setToolTip("切换页面")


class AutoHideSidebar(QFrame):
    """A narrow hover target that can reveal the navigation labels."""

    entered = Signal()
    left = Signal()

    def _get_sidebar_width(self) -> int:
        return self.width()

    def _set_sidebar_width(self, width: int) -> None:
        self.setFixedWidth(max(1, int(width)))

    sidebarWidth = Property(int, _get_sidebar_width, _set_sidebar_width)

    def enterEvent(self, event) -> None:
        self.entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.left.emit()
        super().leaveEvent(event)


class ClickThroughUnlockOverlay(QFrame):
    """The only interactive island while the widget is mouse-transparent."""

    double_clicked = Signal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setObjectName("unlockOverlay")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 4, 7, 4)
        label = QLabel("科研助手")
        label.setObjectName("unlockBrand")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(label)
        self.setStyleSheet(
            """
            QFrame#unlockOverlay { background: rgba(7, 12, 29, 225); border-radius: 4px; }
            QLabel#unlockBrand { color: #ffffff; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 15px; font-weight: 700; }
            """
        )

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_app_settings()
        self._loaded_pages: dict[str, QWidget] = {}
        self._page_placeholders: dict[str, LazyPagePlaceholder] = {}
        self._page_loaders: dict[str, PageImportThread] = {}
        self._page_ready_callbacks: dict[str, list[Callable[[], None]]] = {}
        self._page_load_started: dict[str, float] = {}
        self._page_load_timers: dict[str, QTimer] = {}
        self._sync_page_loads: set[str] = set()
        self._backup_thread: IdleBackupThread | None = None
        self._storage_maintenance_thread: IdleStorageMaintenanceThread | None = None
        self._idle_tasks_running = False
        self._idle_task_queue: list[tuple[str, str]] = []
        self._idle_background_workers: set[QThread] = set()
        self.application_mode = normalize_application_mode(self.settings.get("application_mode", "widget"))
        self.widget_locked = bool(self.settings["window"]["locked"])
        self.setWindowTitle("科研助手")
        self.setWindowFlags(
            _main_window_flags(
                self.application_mode,
                bool(self.settings["always_on_top"]),
                bool(self.settings["click_through"]),
            )
        )
        minimum_width, minimum_height = mode_minimum_size(self.application_mode)
        self.setMinimumSize(minimum_width, minimum_height)
        self._restore_mode_geometry()
        self._apply_mode_window_surface()
        self._edge_margin = 8
        self._resize_edges = Qt.Edges()
        self._resize_start_geometry = QRect()
        self._resize_start_pos = QPoint()
        self._nav_buttons: list[QPushButton] = []
        self._reminder_dialog: QDialog | None = None
        self._special_issue_dialog: SpecialIssueDialog | None = None
        self._special_action_thread: SpecialIssueActionThread | None = None
        self._sidebar_pinned = bool(self.settings.get("sidebar_pinned", False))
        self._sidebar_full_width = 82
        self._sidebar_hidden_width = 6
        self._content_collapsed = False
        self._content_restore_width = max(400, self.width())
        self._sidebar_hide_timer = QTimer(self)
        self._sidebar_hide_timer.setSingleShot(True)
        self._sidebar_hide_timer.setInterval(620)
        self._sidebar_hide_timer.timeout.connect(self._collapse_sidebar)
        self._home_idle_timer = QTimer(self)
        self._home_idle_timer.setSingleShot(True)
        self._home_idle_timer.setInterval(60 * 1000)
        self._home_idle_timer.timeout.connect(self._return_home_after_idle)
        self._maintenance_idle_timer = QTimer(self)
        self._maintenance_idle_timer.setSingleShot(True)
        self._maintenance_idle_timer.setInterval(2 * 60 * 1000)
        self._maintenance_idle_timer.timeout.connect(self._run_idle_maintenance)
        self._hide_to_tray_queued = False
        self._exit_requested = False
        self._dirty_pages: set[str] = set()
        self._build_ui()
        self._sidebar_expanded = True
        self._sidebar_animation = QParallelAnimationGroup(self)
        self._sidebar_width_animation = QPropertyAnimation(self.sidebar, b"sidebarWidth", self)
        self._sidebar_opacity_animation = QPropertyAnimation(self._nav_opacity, b"opacity", self)
        self._sidebar_animation.addAnimation(self._sidebar_width_animation)
        self._sidebar_animation.addAnimation(self._sidebar_opacity_animation)
        self._sidebar_animation.finished.connect(self._finish_sidebar_animation)
        self._unlock_overlay = ClickThroughUnlockOverlay()
        self._unlock_overlay.double_clicked.connect(self._disable_click_through)
        self._apply_styles()
        self._create_tray_icon()
        self._global_hotkey = GlobalHotkeyManager(self._open_global_journal_import)
        self._visibility_hotkey = GlobalHotkeyManager(
            self._toggle_global_visibility,
            hotkey_id=0x4A52,
            double_tap_mode="double_space",
            double_tap_key=VK_SPACE,
            double_tap_label="双击空格",
            disabled_during_ime=True,
            feature_label="显示/隐藏",
        )
        self._apply_global_journal_import_shortcut()
        self._apply_global_visibility_shortcut()
        self._start_reminder_checks()
        self._start_frontier_checks()
        self._start_special_issue_checks()
        QApplication.instance().installEventFilter(self)
        self._schedule_idle_maintenance()
        QTimer.singleShot(0, self._run_initial_navigation_state)
        self._save_failure_keys: tuple[str, ...] = ()
        self._save_watch_timer = QTimer(self)
        self._save_watch_timer.setInterval(1000)
        self._save_watch_timer.timeout.connect(self._check_save_failures)
        self._save_watch_timer.start()
        self._shutdown_waiting = False
        self._shutdown_ready = False

    def _check_save_failures(self) -> None:
        from utils.database_write_queue import WRITE_QUEUE

        keys = tuple(WRITE_QUEUE.failures())
        if keys and keys != self._save_failure_keys:
            self.statusBar().showMessage("有内容保存失败，数据尚未落盘。请检查磁盘空间，点击重试保存。")
            if not hasattr(self, "_retry_save_button"):
                self._retry_save_button = QPushButton("重试保存", self)
                self._retry_save_button.clicked.connect(WRITE_QUEUE.retry_failed)
                self.statusBar().addPermanentWidget(self._retry_save_button)
            self._retry_save_button.show()
        elif not keys and self._save_failure_keys:
            self.statusBar().clearMessage()
            self._retry_save_button.hide()
        self._save_failure_keys = keys

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("windowRoot")
        self.setCentralWidget(root)
        self.shell_layout = QHBoxLayout(root)
        self.shell_layout.setContentsMargins(0, 0, 0, 0)
        self.shell_layout.setSpacing(0)

        self.sidebar = AutoHideSidebar()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(self._sidebar_full_width)
        self.sidebar.entered.connect(self._expand_sidebar)
        self.sidebar.left.connect(self._schedule_sidebar_collapse)
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(7, 14, 7, 12)
        sidebar_layout.setSpacing(5)

        self.nav_container = QWidget()
        self.nav_container.setObjectName("navContainer")
        self.nav_layout = QVBoxLayout(self.nav_container)
        self.nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_layout.setSpacing(5)
        for _key, route, text, icon_name in NAVIGATION_ITEMS:
            button = self._make_nav_button(text, route, icon_name)
            button.clicked.connect(lambda _checked=False, value=route: self.navigate(value))
            button.setMinimumHeight(42)
            self.nav_layout.addWidget(button)
        self.nav_layout.addStretch()
        self.sidebar_pin_button = QPushButton("固定")
        self.sidebar_pin_button.setObjectName("sidebarPinButton")
        self.sidebar_pin_button.setCheckable(True)
        self.sidebar_pin_button.setToolTip("固定展开侧边标签")
        self.sidebar_pin_button.toggled.connect(self._toggle_sidebar_pin)
        self.sidebar_pin_button.blockSignals(True)
        self.sidebar_pin_button.setChecked(self._sidebar_pinned)
        self.sidebar_pin_button.blockSignals(False)
        self.nav_layout.addWidget(self.sidebar_pin_button)
        sidebar_layout.addWidget(self.nav_container, 1)
        self._nav_opacity = QGraphicsOpacityEffect(self.nav_container)
        self._nav_opacity.setOpacity(1.0)
        self.nav_container.setGraphicsEffect(self._nav_opacity)
        self.shell_layout.addWidget(self.sidebar)

        self.content_panel = QWidget()
        content_layout = QVBoxLayout(self.content_panel)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        topbar = DragBar(self)
        topbar.setObjectName("topbar")
        top_layout = QHBoxLayout(topbar)
        top_layout.setContentsMargins(14, 10, 13, 9)
        top_layout.setSpacing(5)

        self.brand_label = QLabel("科研助手")
        self.brand_label.setObjectName("brand")
        self.brand_label.setToolTip(f"科研助手 v{APP_VERSION}")
        self.brand_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        top_layout.addWidget(self.brand_label)
        top_layout.addStretch()

        inbox = QPushButton("+")
        inbox.setObjectName("inboxButton")
        inbox.setFixedWidth(25)
        inbox.setToolTip("科研收件箱：快速记录任务、灵感、待读或论文更新")
        inbox.clicked.connect(self._open_research_inbox)
        top_layout.addWidget(inbox)

        self.lock_button = QPushButton("固定")
        self.lock_button.setCheckable(True)
        self.lock_button.setObjectName("lockButton")
        self.lock_button.setToolTip("固定窗口位置和大小 / 解除固定")
        self.lock_button.toggled.connect(self._toggle_widget_lock)
        self.lock_button.blockSignals(True)
        self.lock_button.setChecked(self.widget_locked)
        self.lock_button.blockSignals(False)
        self.lock_button.setToolTip("窗口已固定，点击解除固定" if self.widget_locked else "固定窗口位置和大小 / 解除固定")
        top_layout.addWidget(self.lock_button)

        settings = QPushButton("设置")
        settings.setObjectName("subtleButton")
        settings.setToolTip("设置")
        settings.clicked.connect(self._open_settings)
        top_layout.addWidget(settings)

        minimize = QPushButton("-")
        minimize.setObjectName("windowButton")
        minimize.setToolTip("收起到系统托盘")
        minimize.setFixedWidth(24)
        minimize.clicked.connect(self._hide_to_tray)
        top_layout.addWidget(minimize)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setToolTip("关闭")
        close.clicked.connect(self.close)
        top_layout.addWidget(close)
        content_layout.addWidget(topbar)

        self.home_page = HomePage()
        self._loaded_pages["home"] = self.home_page
        labels = {
            "todo": "今日任务",
            "papers": "投稿记录",
            "notes": "灵感与待读",
            "journals": "期刊库",
            "frontier": "每日前沿",
            "special_issues": "特刊征稿",
            "achievements": "成果",
            "tools": "工具",
        }
        for page_key, label in labels.items():
            placeholder = LazyPagePlaceholder(label)
            placeholder.retry_requested.connect(
                lambda key=page_key: self._retry_page_load(key)
            )
            self._page_placeholders[page_key] = placeholder
        self.workbench_shell = WorkbenchShell(
            {"home": self.home_page, **self._page_placeholders}
        )
        self.workbench_shell.setObjectName("contentStack")
        self.workbench_shell.navigation_requested.connect(self.navigate)
        self.workbench_shell.set_mode(self.application_mode)
        self.home_page.open_todo.connect(lambda: self.navigate("todo"))
        self.home_page.open_papers.connect(lambda: self.navigate("papers"))
        self.home_page.open_paper_journal.connect(self._reveal_home_paper_journal)
        self.home_page.open_notes.connect(lambda: self.navigate("notes"))
        self.home_page.open_frontier.connect(lambda: self.navigate("frontier"))
        content_layout.addWidget(self.workbench_shell, 1)
        self.shell_layout.addWidget(self.content_panel, 1)
        self.navigate("home")

    def _make_nav_button(self, text: str, page_index: str, icon_name: str = "") -> VerticalNavButton:
        button = VerticalNavButton(text, page_index)
        button.setIcon(lucide_icon(icon_name))
        button.setIconSize(QSize(17, 17))
        button.setCheckable(True)
        button.setObjectName("navButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_buttons.append(button)
        return button

    @property
    def todo_page(self):
        return self._ensure_page_sync("todo")

    @property
    def paper_page(self):
        return self._ensure_page_sync("papers")

    @property
    def notes_page(self):
        return self._ensure_page_sync("notes")

    @property
    def journal_page(self):
        return self._ensure_page_sync("journals")

    @property
    def frontier_page(self):
        return self._ensure_page_sync("frontier")

    @property
    def special_issue_page(self):
        return self._ensure_page_sync("special_issues")

    @property
    def achievements_page(self):
        return self._ensure_page_sync("achievements")

    def _ensure_page_sync(self, page_key: str) -> QWidget:
        """Compatibility path for direct callers; navigation uses async import."""

        existing = self._loaded_pages.get(page_key)
        if existing is not None:
            return existing
        module_name, class_name = LAZY_PAGE_TYPES[page_key]
        page_type = getattr(importlib.import_module(module_name), class_name)
        return self._install_loaded_page(page_key, page_type)

    def _ensure_page_async(self, page_key: str, callback: Callable[[], None] | None = None) -> None:
        if page_key in self._loaded_pages:
            if callback is not None:
                QTimer.singleShot(0, callback)
            return
        if callback is not None:
            self._page_ready_callbacks.setdefault(page_key, []).append(callback)
        existing_loader = self._page_loaders.get(page_key)
        if existing_loader is not None and existing_loader.isRunning():
            return
        if existing_loader is not None:
            self._page_loaders.pop(page_key, None)
        if page_key in self._sync_page_loads:
            return
        self._page_load_started[page_key] = time.perf_counter()
        append_runtime_log("lazy_page_requested", page=page_key)
        placeholder = self._page_placeholders.get(page_key)
        if placeholder is not None:
            placeholder.show_waiting(f"正在准备{placeholder.page_label}…")
        # The results page is small, but it previously depended on a worker
        # signal that could finish silently and leave the placeholder forever.
        # Keep it lazy, then create it on the GUI thread when first requested.
        if page_key == "achievements":
            self._sync_page_loads.add(page_key)
            QTimer.singleShot(0, lambda key=page_key: self._load_page_sync_logged(key))
            return
        module_name, class_name = LAZY_PAGE_TYPES[page_key]
        loader = PageImportThread(page_key, module_name, class_name)
        loader.setParent(self)
        loader.ready.connect(self._page_import_ready)
        loader.failed.connect(self._page_import_failed)
        loader.finished.connect(
            lambda key=page_key, current=loader: self._page_import_finished(key, current)
        )
        loader.finished.connect(loader.deleteLater)
        self._page_loaders[page_key] = loader
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(15 * 1000)
        timer.timeout.connect(lambda key=page_key, current=loader: self._page_import_timeout(key, current))
        self._page_load_timers[page_key] = timer
        timer.start()
        loader.start(QThread.Priority.LowPriority)

    def _load_page_sync_logged(self, page_key: str) -> None:
        try:
            self._ensure_page_sync(page_key)
        except Exception as error:  # noqa: BLE001 - keep navigation available
            self._sync_page_loads.discard(page_key)
            self._page_import_failed(page_key, str(error))
            return
        self._sync_page_loads.discard(page_key)
        elapsed_ms = int((time.perf_counter() - self._page_load_started.pop(page_key, time.perf_counter())) * 1000)
        append_runtime_log("lazy_page_ready", page=page_key, duration_ms=elapsed_ms, mode="gui")
        callbacks = self._page_ready_callbacks.pop(page_key, [])
        for callback in callbacks:
            QTimer.singleShot(0, callback)

    def _page_import_ready(self, page_key: str, page_type: object) -> None:
        self._page_loaders.pop(page_key, None)
        self._stop_page_load_timer(page_key)
        try:
            self._install_loaded_page(page_key, page_type)
        except Exception as error:  # noqa: BLE001 - keep the rest of the shell usable
            self._page_import_failed(page_key, str(error))
            return
        elapsed_ms = int((time.perf_counter() - self._page_load_started.pop(page_key, time.perf_counter())) * 1000)
        append_runtime_log("lazy_page_ready", page=page_key, duration_ms=elapsed_ms, mode="worker-import")
        callbacks = self._page_ready_callbacks.pop(page_key, [])
        for callback in callbacks:
            QTimer.singleShot(0, callback)

    def _page_import_failed(self, page_key: str, message: str) -> None:
        self._page_loaders.pop(page_key, None)
        self._sync_page_loads.discard(page_key)
        self._stop_page_load_timer(page_key)
        self._page_ready_callbacks.pop(page_key, None)
        elapsed_ms = int((time.perf_counter() - self._page_load_started.pop(page_key, time.perf_counter())) * 1000)
        append_runtime_log(
            "lazy_page_failed",
            level="error",
            page=page_key,
            duration_ms=elapsed_ms,
            error=str(message)[:500],
        )
        placeholder = self._page_placeholders.get(page_key)
        if placeholder is not None:
            try:
                placeholder.show_error(message or "页面加载失败")
            except RuntimeError:
                pass
        if self._idle_tasks_running:
            QTimer.singleShot(0, self._run_next_idle_task)

    def _stop_page_load_timer(self, page_key: str) -> None:
        timer = self._page_load_timers.pop(page_key, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def _page_import_timeout(self, page_key: str, loader: PageImportThread) -> None:
        if self._page_loaders.get(page_key) is not loader or page_key in self._loaded_pages:
            return
        append_runtime_log("lazy_page_slow", level="warning", page=page_key, duration_ms=15000)
        placeholder = self._page_placeholders.get(page_key)
        if placeholder is not None:
            placeholder.show_error("加载时间较长，可稍后重试")

    def _page_import_finished(self, page_key: str, loader: PageImportThread) -> None:
        def finalize() -> None:
            if self._page_loaders.get(page_key) is not loader or page_key in self._loaded_pages:
                return
            # Recover the exact state that used to leave "正在准备" on screen
            # forever: the worker ended without delivering ready/failed.
            self._page_loaders.pop(page_key, None)
            self._stop_page_load_timer(page_key)
            append_runtime_log("lazy_page_finished_without_result", level="warning", page=page_key)
            self._load_page_sync_logged(page_key)

        QTimer.singleShot(0, finalize)

    def _retry_page_load(self, page_key: str) -> None:
        loader = self._page_loaders.get(page_key)
        if loader is not None and loader.isRunning():
            placeholder = self._page_placeholders.get(page_key)
            if placeholder is not None:
                placeholder.show_waiting("页面仍在加载，请稍候…")
            return
        self._page_loaders.pop(page_key, None)
        self._page_ready_callbacks.pop(page_key, None)
        self._ensure_page_async(page_key)

    def _install_loaded_page(self, page_key: str, page_type: object) -> QWidget:
        existing = self._loaded_pages.get(page_key)
        if existing is not None:
            return existing
        page = page_type()
        self._loaded_pages[page_key] = page
        self._connect_loaded_page(page_key, page)
        self.workbench_shell.replace_page(page_key, page)
        self._page_placeholders.pop(page_key, None)
        return page

    def _connect_loaded_page(self, page_key: str, page: QWidget) -> None:
        if page_key in {"todo", "notes", "frontier", "achievements"}:
            page.changed.connect(self.home_page.refresh)
        if page_key == "papers":
            page.changed.connect(self._on_paper_data_changed)
        elif page_key == "journals":
            page.changed.connect(self._on_journal_data_changed)
            page.settings_center_requested.connect(self._open_settings)
        elif page_key == "frontier":
            page.daily_ready.connect(self._show_frontier_notification)
            page.open_journal_library.connect(lambda: self.navigate("journals"))
            page.settings_center_requested.connect(self._open_settings)
        elif page_key == "achievements":
            page.profile_update_requested.connect(self._update_profile_from_achievements)
        elif page_key == "special_issues":
            page.open_workbench.connect(self._open_special_issue_workbench)
            page.refresh_progress.connect(self._show_special_issue_progress)
            page.refresh_completed.connect(self._special_issue_refresh_completed)
            page.refresh_failed.connect(self._special_issue_refresh_failed)

    def _run_initial_navigation_state(self) -> None:
        """Drop a queued startup layout pass when the window has closed."""
        try:
            self._apply_initial_navigation_state()
        except RuntimeError:
            return

    def _schedule_idle_maintenance(self, delay_ms: int | None = None) -> None:
        """Postpone disk/network maintenance until the app is not being used."""

        if os.environ.get("RESEARCH_ASSISTANT_DISABLE_BACKGROUND") == "1":
            return
        delay = 2 * 60 * 1000 if delay_ms is None else max(1000, int(delay_ms))
        self._maintenance_idle_timer.start(delay)

    def _application_is_in_use(self) -> bool:
        app = QApplication.instance()
        return bool(
            app is not None
            and app.applicationState() == Qt.ApplicationState.ApplicationActive
            and self.isVisible()
            and not self.isMinimized()
        )

    def _run_idle_maintenance(self) -> None:
        if self._application_is_in_use() or QApplication.activeModalWidget() is not None:
            self._schedule_idle_maintenance(30 * 1000)
            return
        if (
            (self._backup_thread is not None and self._backup_thread.isRunning())
            or (
                self._storage_maintenance_thread is not None
                and self._storage_maintenance_thread.isRunning()
            )
        ):
            self._schedule_idle_maintenance(60 * 1000)
            return
        if self.settings.get("auto_backup", False):
            thread = IdleBackupThread(self.settings)
            thread.setParent(self)
            thread.completed.connect(self._run_idle_storage_maintenance)
            thread.failed.connect(self._idle_backup_failed)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._clear_backup_thread)
            self._backup_thread = thread
            thread.start(QThread.Priority.LowPriority)
            return
        self._run_idle_storage_maintenance()

    def _clear_backup_thread(self) -> None:
        self._backup_thread = None

    def _idle_backup_failed(self, message: str) -> None:
        append_runtime_log("idle_backup_failed", level="warning", error=str(message)[:500])
        self._run_idle_storage_maintenance()

    def _run_idle_storage_maintenance(self) -> None:
        if self._application_is_in_use() or QApplication.activeModalWidget() is not None:
            self._schedule_idle_maintenance(30 * 1000)
            return
        if self._storage_maintenance_thread is not None and self._storage_maintenance_thread.isRunning():
            return
        thread = IdleStorageMaintenanceThread()
        thread.setParent(self)
        thread.completed.connect(self._idle_storage_maintenance_completed)
        thread.failed.connect(self._idle_storage_maintenance_failed)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_storage_maintenance_thread)
        self._storage_maintenance_thread = thread
        append_runtime_log("idle_database_maintenance_started")
        thread.start(QThread.Priority.LowPriority)

    def _clear_storage_maintenance_thread(self) -> None:
        self._storage_maintenance_thread = None

    def _idle_storage_maintenance_completed(self, result: object) -> None:
        values = result if isinstance(result, dict) else {}
        append_runtime_log(
            "idle_database_maintenance_completed",
            expired_source_responses=values.get("expired_source_responses", 0),
            stale_special_discoveries=values.get("stale_special_discoveries", 0),
            legacy_app_tables_dropped=values.get("legacy_app_tables_dropped", 0),
            cache_vacuumed=bool((values.get("cache_storage") or {}).get("vacuumed", False))
            if isinstance(values.get("cache_storage"), dict)
            else False,
        )
        self._run_idle_soft_tasks()

    def _idle_storage_maintenance_failed(self, message: str) -> None:
        append_runtime_log(
            "idle_database_maintenance_failed", level="warning", error=str(message)[:500]
        )
        self._run_idle_soft_tasks()

    def _run_idle_soft_tasks(self) -> None:
        if self._idle_tasks_running:
            return
        self._idle_tasks_running = True
        self._idle_task_queue = [
            ("frontier", "profile"),
            ("journals", "enrich"),
            ("special_issues", "refresh"),
        ]
        if self.settings.get("frontier_background_refresh", True):
            self._idle_task_queue.insert(1, ("frontier", "refresh"))
        self._run_next_idle_task()

    def _run_next_idle_task(self) -> None:
        if self._application_is_in_use() or QApplication.activeModalWidget() is not None:
            self._idle_task_queue.clear()
            self._idle_tasks_running = False
            self._schedule_idle_maintenance(30 * 1000)
            return
        if not self._idle_task_queue:
            self._idle_tasks_running = False
            self._schedule_idle_maintenance(60 * 60 * 1000)
            return
        page_key, action = self._idle_task_queue.pop(0)

        def run_action() -> None:
            if self._application_is_in_use():
                self._idle_task_queue.clear()
                self._idle_tasks_running = False
                self._schedule_idle_maintenance(30 * 1000)
                return
            page = self._loaded_pages.get(page_key)
            before_workers = set(self._page_background_workers(page))
            try:
                if page_key == "frontier" and action == "profile":
                    page.auto_update_profile_if_due(
                        refresh_after=bool(self.settings.get("frontier_background_refresh", True))
                    )
                elif page_key == "frontier":
                    page.auto_refresh_if_due()
                elif page_key == "journals":
                    page.auto_update_easyscholar_if_due()
                    page.auto_enrich_new_if_due()
                    self.settings = load_app_settings()
                elif page_key == "special_issues":
                    page.auto_refresh_if_due()
            except (AttributeError, RuntimeError):
                pass
            started = [
                worker
                for worker in self._page_background_workers(page)
                if worker not in before_workers and worker.isRunning()
            ]
            if started:
                remaining = {worker for worker in started}
                self._idle_background_workers.update(remaining)

                def finished(worker: QThread) -> None:
                    remaining.discard(worker)
                    self._idle_background_workers.discard(worker)
                    if not remaining:
                        QTimer.singleShot(0, self._run_next_idle_task)

                for worker in started:
                    worker.finished.connect(lambda value=worker: finished(value))
            else:
                QTimer.singleShot(1500, self._run_next_idle_task)

        self._ensure_page_async(page_key, run_action)

    @staticmethod
    def _page_background_workers(page: object) -> list[QThread]:
        if page is None:
            return []
        result: list[QThread] = []
        for name in (
            "_worker",
            "_refresh_worker",
            "_profile_ai_worker",
            "_ai_worker",
            "_metadata_worker",
            "_jcr_worker",
            "_easy_worker",
        ):
            worker = getattr(page, name, None)
            if isinstance(worker, QThread):
                result.append(worker)
        return result

    def _cancel_idle_background_work(self) -> None:
        """Yield background CPU, network and storage work to active use."""

        active = self._idle_tasks_running
        self._idle_task_queue.clear()
        self._idle_tasks_running = False
        for thread in (self._backup_thread, self._storage_maintenance_thread):
            if thread is not None and thread.isRunning():
                active = True
                thread.requestInterruption()
        for worker in tuple(self._idle_background_workers):
            if worker.isRunning():
                active = True
                worker.requestInterruption()
        if active:
            append_runtime_log("idle_background_cancelled_for_user_activity")

    def _apply_initial_navigation_state(self) -> None:
        self._set_sidebar_position(self.settings.get("sidebar_position", "left"), persist=False)
        self._set_sidebar_auto_hide(
            bool(self.settings.get("sidebar_auto_hide", True)),
            persist=False,
            immediate=True,
        )

    def _collapse_mode(self) -> str:
        return "content" if self.settings.get("sidebar_collapse_mode") == "content" else "labels"

    def _set_sidebar_position(self, position: str, persist: bool = True) -> None:
        position = "right" if str(position).casefold() == "right" else "left"
        self.shell_layout.removeWidget(self.sidebar)
        self.shell_layout.removeWidget(self.content_panel)
        if position == "right":
            self.shell_layout.addWidget(self.content_panel, 1)
            self.shell_layout.addWidget(self.sidebar)
        else:
            self.shell_layout.addWidget(self.sidebar)
            self.shell_layout.addWidget(self.content_panel, 1)
        self.settings["sidebar_position"] = position
        side = "右侧" if position == "right" else "左侧"
        self.sidebar_pin_button.setToolTip(f"取消固定{side}标签" if self._sidebar_pinned else f"固定展开{side}标签")
        if persist:
            self._save_settings()

    def _collapse_main_content(self) -> None:
        if self._content_collapsed:
            return
        self._content_restore_width = max(400, self.width())
        self._capture_window_dimensions()
        # A content-collapse should be reversible even if the widget is not
        # position-locked.  Persist only the normal dimensions, never the
        # temporary narrow navigation-strip width.
        self._save_settings()
        self._content_collapsed = True
        self.content_panel.hide()
        self.setMinimumWidth(self._sidebar_full_width)
        self.resize(self._sidebar_full_width, self.height())

    def _expand_main_content(self) -> None:
        if not self._content_collapsed:
            return
        stored_width = self.settings.get("window", {}).get("width", 400)
        try:
            stored_width = int(stored_width)
        except (TypeError, ValueError):
            stored_width = 400
        restore_width = max(400, self._content_restore_width, stored_width)
        self._content_collapsed = False
        self.setMinimumWidth(400)
        self.content_panel.show()
        self.resize(restore_width, self.height())
        self._content_restore_width = restore_width
        self._capture_window_dimensions()

    def navigate(self, route: str, anchor: str | None = None) -> None:
        """Navigate legacy callers and new workbench controls through one registry."""
        self._expand_main_content()
        requested = resolve_route(route, anchor)
        page_key = {
            ("work", "tasks"): "todo",
            ("work", "notes"): "notes",
            ("work", "tools"): "tools",
            ("papers", "submissions"): "papers",
            ("papers", "results"): "achievements",
            ("library", "journals"): "journals",
            ("library", "frontier"): "frontier",
            ("library", "special_issues"): "special_issues",
        }.get((requested.workbench, requested.anchor))
        target = self.workbench_shell.navigate(requested)
        if page_key is not None and page_key not in self._loaded_pages:
            self._ensure_page_async(
                page_key,
                lambda selected=requested: self.navigate(selected.workbench, selected.anchor),
            )
            for button in self._nav_buttons:
                button.setChecked(button.page_index == ("tools" if target.anchor == "tools" else target.workbench))
            self._home_idle_timer.start()
            return
        if page_key in self._dirty_pages:
            page = self._loaded_pages[page_key]
            self._dirty_pages.discard(page_key)
            # The current cached model is already visible.  Ask the page to
            # refresh in the background instead of blocking this navigation
            # event with disk I/O, scoring and a complete widget rebuild.
            request_reload = getattr(page, "request_reload", None)
            if callable(request_reload):
                request_reload()
            else:
                QTimer.singleShot(0, page.reload)
        for button in self._nav_buttons:
            button.setChecked(button.page_index == ("tools" if target.anchor == "tools" else target.workbench))
        if target.workbench == "home":
            self._home_idle_timer.stop()
        else:
            self._home_idle_timer.start()

    def _on_paper_data_changed(self) -> None:
        """Refresh dependent pages when they are next opened, not inline."""

        self.home_page.refresh()
        self._check_submission_reminders()
        self._dirty_pages.update({"journals", "achievements"})

    def _on_journal_data_changed(self) -> None:
        """Keep journal edits responsive while preserving dependent results."""

        self.home_page.refresh()
        self._dirty_pages.update({"papers", "frontier"})

    def _open_special_issue_workbench(self, selected_issue_id: str = "") -> None:
        """Open or focus the one large workbench without changing widget mode."""
        from ui.special_issue_dialog import SpecialIssueDialog

        if selected_issue_id:
            try:
                current_store = load_special_issue_overview()
                current_item = next(
                    (value for value in current_store.get("items", []) if str(value.get("id", "")) == str(selected_issue_id)),
                    None,
                )
                if current_item is not None and str(current_item.get("status", "unread")) == "unread":
                    set_special_issue_status(selected_issue_id, "read")
                self._record_special_issue_signal(selected_issue_id, "detail_open")
            except Exception:
                pass
        store = load_special_issue_overview()
        if self._special_issue_dialog is not None:
            try:
                self._special_issue_dialog.reload_data(store, store.get("items", []), load_papers())
                if selected_issue_id:
                    self._special_issue_dialog.select_issue(selected_issue_id)
                self._special_issue_dialog.show()
                self._special_issue_dialog.raise_()
                self._special_issue_dialog.activateWindow()
                return
            except RuntimeError:
                self._special_issue_dialog = None
        dialog = SpecialIssueDialog(store, store.get("items", []), load_papers(), selected_issue_id, self)
        dialog.associate_requested.connect(self._associate_special_issue)
        dialog.path_requested.connect(self._add_special_issue_path)
        dialog.task_requested.connect(self._create_special_issue_task)
        dialog.status_requested.connect(self._set_special_issue_status)
        dialog.library_requested.connect(self._add_special_issue_journal)
        dialog.scope_note_requested.connect(self._save_special_issue_scope_note)
        dialog.refresh_requested.connect(self._refresh_special_issue_workbench)
        dialog.cancel_refresh_requested.connect(self.special_issue_page.cancel_refresh)
        dialog.finished.connect(lambda _result: setattr(self, "_special_issue_dialog", None))
        self._special_issue_dialog = dialog
        dialog.show()

    def _reload_special_issue_surfaces(self, selected_issue_id: str = "", message: str = "") -> None:
        store = load_special_issue_overview()
        page = self.special_issue_page
        apply_store = getattr(page, "_apply_loaded_store", None)
        if callable(apply_store):
            apply_store(store)
        else:
            page.reload()
        dialog = self._special_issue_dialog
        if dialog is not None:
            dialog.reload_data(store, store.get("items", []), load_papers())
            if selected_issue_id:
                dialog.select_issue(selected_issue_id)
            dialog.finish_progress(message or "操作完成")

    def _run_special_issue_action(
        self,
        selected_issue_id: str,
        message: str,
        action,
        on_success: Callable[[], None] | None = None,
    ) -> None:
        dialog = self._special_issue_dialog
        if dialog is not None:
            dialog.set_progress(15, message)

        if self._special_action_thread is not None and self._special_action_thread.isRunning():
            if dialog is not None:
                dialog.finish_progress("另一个特刊操作仍在进行，请稍候。")
            return

        worker = SpecialIssueActionThread(action, self)

        def completed() -> None:
            if on_success is not None:
                on_success()
            self._reload_special_issue_surfaces(selected_issue_id, "已完成，工作台保持打开")

        def failed(message_text: str) -> None:
            current_dialog = self._special_issue_dialog
            if current_dialog is not None:
                current_dialog.finish_progress(f"未完成：{message_text}")

        worker.completed.connect(completed)
        worker.failed.connect(failed)
        worker.finished.connect(self._clear_special_action_thread)
        self._special_action_thread = worker
        worker.start(QThread.Priority.LowPriority)

    def _clear_special_action_thread(self) -> None:
        worker = self._special_action_thread
        self._special_action_thread = None
        if worker is not None:
            worker.deleteLater()

    def _associate_special_issue(self, issue_id: str, paper_ids: list[str]) -> None:
        def action() -> None:
            associate_special_issue(issue_id, paper_ids)
            self._record_special_issue_signal(issue_id, "paper_association")

        self._run_special_issue_action(issue_id, "正在关联论文…", action)

    def _add_special_issue_path(self, issue_id: str, paper_id: str) -> None:
        def on_success() -> None:
            self._dirty_pages.update({"papers", "journals"})
            self.home_page.refresh()

        self._run_special_issue_action(
            issue_id,
            "正在入库并创建投稿候选…",
            lambda: add_special_issue_to_submission_path(issue_id, paper_id),
            on_success,
        )

    def _add_special_issue_journal(self, issue_id: str) -> None:
        def on_success() -> None:
            self._dirty_pages.add("journals")

        self._run_special_issue_action(
            issue_id,
            "正在加入期刊库…",
            lambda: add_special_issue_journal_to_library(issue_id),
            on_success,
        )

    def _create_special_issue_task(self, issue_id: str, paper_id: str) -> None:
        def on_success() -> None:
            self._dirty_pages.add("todo")
            self.home_page.refresh()

        self._run_special_issue_action(
            issue_id,
            "正在创建准备任务…",
            lambda: create_special_issue_preparation_task(issue_id, paper_id),
            on_success,
        )

    def _set_special_issue_status(self, issue_id: str, status: str) -> None:
        def action() -> None:
            set_special_issue_status(issue_id, status)
            signal_type = {"saved": "favorite", "ignored": "ignore", "read": "read"}.get(status)
            if signal_type:
                self._record_special_issue_signal(issue_id, signal_type)

        self._run_special_issue_action(issue_id, "正在保存选择…", action)

    def _save_special_issue_scope_note(self, issue_id: str, note: str) -> None:
        self._run_special_issue_action(
            issue_id,
            "正在保存我的理解…",
            lambda: set_special_issue_scope_note(issue_id, note),
        )

    @staticmethod
    def _record_special_issue_signal(issue_id: str, event_type: str) -> None:
        try:
            from utils.research_profile_repository import load_research_profile, save_research_profile
            from utils.research_signal_service import record_signal

            store = load_special_issue_overview()
            issue = next(
                (value for value in store.get("items", []) if str(value.get("id", "")) == str(issue_id)),
                None,
            )
            if issue is None:
                return
            match = issue.get("match", {}) if isinstance(issue.get("match"), dict) else {}
            updated = record_signal(
                load_research_profile(),
                {
                    "event_type": event_type,
                    "item_id": str(issue_id),
                    "title": str(issue.get("title", "")),
                    "terms": match.get("matched_terms", []),
                    "source": "special_issue",
                    "reason": str(match.get("reason", "")),
                },
            )
            save_research_profile(updated)
        except Exception:
            # Learning is secondary; a completed user action must remain successful.
            return

    def _refresh_special_issue_workbench(self) -> None:
        if not self.special_issue_page.start_refresh(force=True):
            dialog = self._special_issue_dialog
            if dialog is not None:
                dialog.set_progress(5, "已有刷新任务正在运行…")

    def _show_special_issue_progress(self, message: str, value: int) -> None:
        dialog = self._special_issue_dialog
        if dialog is not None:
            dialog.set_progress(value, message)

    def _special_issue_refresh_completed(self, result: dict) -> None:
        stats = result.get("stats", {}) if isinstance(result, dict) else {}
        store = result.get("store", {}) if isinstance(result, dict) else {}
        status = str(store.get("last_refresh_status", "success"))
        message = (
            "刷新已取消，原有数据保持不变"
            if status == "cancelled"
            else f"部分来源未完成，本轮处理 {int(stats.get('items', 0) or 0)} 条征稿"
            if status == "partial"
            else f"刷新完成，本轮处理 {int(stats.get('items', 0) or 0)} 条征稿"
        )
        self._reload_special_issue_surfaces(message=message)
        notifications = result.get("notifications", []) if isinstance(result, dict) else []
        if self.tray_icon:
            delivered_ids: list[str] = []
            for notification in notifications[:4]:
                self.tray_icon.showMessage(
                    str(notification.get("title", "特刊征稿")),
                    str(notification.get("body", "发现特刊更新")),
                    QSystemTrayIcon.MessageIcon.Information,
                    6000,
                )
                delivered_ids.append(str(notification.get("id", "")))
            if len(notifications) > 4:
                self.tray_icon.showMessage(
                    "特刊征稿",
                    f"另有 {len(notifications) - 4} 条更新，可在工作台查看。",
                    QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )
            try:
                mark_special_issue_notifications_sent(delivered_ids)
            except Exception:
                pass

    def _special_issue_refresh_failed(self, message: str) -> None:
        dialog = self._special_issue_dialog
        if dialog is not None:
            dialog.finish_progress(f"刷新未完成：{message}")

    def _start_special_issue_checks(self) -> None:
        # The page owns the 24-hour boundary. MainWindow only asks for work
        # after the user has left the application idle.
        if hasattr(self, "_special_issue_timer") and self._special_issue_timer is not None:
            self._special_issue_timer.stop()
        if os.environ.get("RESEARCH_ASSISTANT_DISABLE_BACKGROUND") == "1":
            self._special_issue_timer = None
            return
        self._special_issue_timer = QTimer(self)
        self._special_issue_timer.setInterval(60 * 60 * 1000)
        self._special_issue_timer.timeout.connect(self._schedule_idle_maintenance)
        self._special_issue_timer.start()

    def _reveal_home_paper_journal(self, paper_id: str, journal_id: str) -> None:
        """Follow a HOME action directly to its owning journal history row."""
        self.navigate("papers")
        self._ensure_page_async(
            "papers",
            lambda: self._loaded_pages["papers"].reveal_journal(paper_id, journal_id),
        )

    def _switch_page(self, index: int | str) -> None:
        """Compatibility adapter for existing reminder, tray and HOME call sites."""
        route = LEGACY_PAGE_ROUTES.get(index, index) if isinstance(index, int) else index
        self.navigate(str(route))

    def _expand_sidebar(self) -> None:
        self._sidebar_hide_timer.stop()
        if self._collapse_mode() == "content":
            self._expand_main_content()
        if self._sidebar_expanded and self.sidebar.width() >= self._sidebar_full_width:
            return
        self._sidebar_expanded = True
        self.nav_container.show()
        self._animate_sidebar(self._sidebar_full_width, 1.0, QEasingCurve.Type.OutCubic)

    def _schedule_sidebar_collapse(self) -> None:
        if not self.settings.get("sidebar_auto_hide", True) or self._sidebar_pinned:
            return
        # In "collapse main page" mode, moving from the navigation strip into
        # the content is still active use.  Wait until the pointer leaves the
        # whole widget before folding the page away.
        if self._collapse_mode() == "content" and self.underMouse():
            return
        self._sidebar_hide_timer.start()

    def _collapse_sidebar(self, force: bool = False, immediate: bool = False) -> None:
        if not self.settings.get("sidebar_auto_hide", True):
            return
        if not force and (self._sidebar_pinned or self.sidebar.underMouse()):
            return
        if self._collapse_mode() == "content":
            self._sidebar_animation.stop()
            self._sidebar_expanded = True
            self.nav_container.show()
            self._nav_opacity.setOpacity(1.0)
            self.sidebar.setFixedWidth(self._sidebar_full_width)
            self._collapse_main_content()
            return
        self._sidebar_expanded = False
        if immediate:
            self._sidebar_animation.stop()
            self._nav_opacity.setOpacity(0.0)
            self.nav_container.hide()
            self.sidebar.setFixedWidth(self._sidebar_hidden_width)
            return
        self._animate_sidebar(self._sidebar_hidden_width, 0.0, QEasingCurve.Type.InCubic)

    def _animate_sidebar(self, target_width: int, target_opacity: float, easing: QEasingCurve.Type) -> None:
        self._sidebar_animation.stop()
        self._sidebar_width_animation.setDuration(190)
        self._sidebar_width_animation.setEasingCurve(QEasingCurve(easing))
        self._sidebar_width_animation.setStartValue(self.sidebar.width())
        self._sidebar_width_animation.setEndValue(target_width)
        self._sidebar_opacity_animation.setDuration(150)
        self._sidebar_opacity_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._sidebar_opacity_animation.setStartValue(self._nav_opacity.opacity())
        self._sidebar_opacity_animation.setEndValue(target_opacity)
        self._sidebar_animation.start()

    def _finish_sidebar_animation(self) -> None:
        if not self._sidebar_expanded:
            self.nav_container.hide()

    def _set_sidebar_auto_hide(self, enabled: bool, persist: bool = True, immediate: bool = False) -> None:
        self.settings["sidebar_auto_hide"] = enabled
        self._sidebar_hide_timer.stop()
        if self._collapse_mode() != "content":
            self._expand_main_content()
        if enabled and not self._sidebar_pinned:
            self._collapse_sidebar(force=True, immediate=immediate)
        else:
            self._expand_main_content()
            self._sidebar_expanded = True
            self.nav_container.show()
            if immediate:
                self._sidebar_animation.stop()
                self._nav_opacity.setOpacity(1.0)
                self.sidebar.setFixedWidth(self._sidebar_full_width)
            else:
                self._animate_sidebar(self._sidebar_full_width, 1.0, QEasingCurve.Type.OutCubic)
        if persist:
            self._save_settings()

    def _toggle_sidebar_pin(self, pinned: bool) -> None:
        self._sidebar_pinned = pinned
        self.settings["sidebar_pinned"] = pinned
        side = "右侧" if self.settings.get("sidebar_position") == "right" else "左侧"
        self.sidebar_pin_button.setToolTip(f"取消固定{side}标签" if pinned else f"固定展开{side}标签")
        if pinned:
            self._expand_sidebar()
        else:
            self._schedule_sidebar_collapse()
        self._save_settings()

    def _return_home_after_idle(self) -> None:
        if self.workbench_shell.current_route.workbench == "home":
            return
        if not self.settings.get("return_home_when_inactive", True):
            return
        if QApplication.activeModalWidget() is not None:
            self._home_idle_timer.start()
            return
        # Do not pull a paper or timeline away while it is being read.  The
        # automatic return applies after the widget has genuinely become idle.
        if self.isActiveWindow() and self.frameGeometry().contains(QCursor.pos()):
            self._home_idle_timer.start()
            return
        self._switch_page(0)

    def _mode_window_profile(self) -> dict:
        key = mode_window_key(self.application_mode)
        profile = self.settings.get(key, {})
        if not isinstance(profile, dict):
            profile = {}
            self.settings[key] = profile
        return profile

    def _restore_mode_geometry(self) -> None:
        window = self._mode_window_profile()
        self.resize(int(window.get("width", self.minimumWidth())), int(window.get("height", self.minimumHeight())))
        should_restore_position = self.application_mode == "software" or self.widget_locked
        if not should_restore_position or window.get("x") is None or window.get("y") is None:
            return
        saved = QRect(int(window["x"]), int(window["y"]), self.width(), self.height())
        screens = QApplication.screens()
        if any(screen.availableGeometry().contains(saved.center()) for screen in screens):
            self.setGeometry(saved)

    def _capture_locked_geometry(self) -> None:
        self._capture_window_dimensions()
        geometry = self.geometry()
        profile = self._mode_window_profile()
        profile.update(
            {
                "x": geometry.x(),
                "y": geometry.y(),
            }
        )
        if self.application_mode == "widget":
            self.settings["window"] = dict(profile)

    def _capture_window_dimensions(self) -> None:
        """Keep the last expanded size separate from the collapsed strip."""
        geometry = self.geometry()
        width = self._content_restore_width if self.application_mode == "widget" and self._content_collapsed else geometry.width()
        minimum_width, minimum_height = mode_minimum_size(self.application_mode)
        profile = self._mode_window_profile()
        profile.update(
            {
                "width": max(minimum_width, int(width)),
                "height": max(minimum_height, int(geometry.height())),
            }
        )
        if self.application_mode == "widget":
            self.settings["window"] = dict(profile)

    def _capture_mode_geometry(self) -> None:
        self._capture_window_dimensions()
        if self.application_mode == "software" or self.widget_locked:
            self._capture_locked_geometry()

    def _apply_mode_window_flags(self) -> None:
        was_visible = self.isVisible()
        self.setWindowFlags(
            _main_window_flags(
                self.application_mode,
                bool(self.settings.get("always_on_top", True)),
                bool(self.settings.get("click_through", False)),
            )
        )
        if was_visible:
            self.show()

    def _apply_mode_window_surface(self) -> None:
        """Keep the full workbench opaque while retaining widget transparency."""
        is_widget = self.application_mode == "widget"
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, is_widget)
        self.setWindowOpacity(self.settings["opacity"] / 100 if is_widget else 1.0)

    def _apply_application_mode(self, mode: str, persist: bool = True) -> None:
        mode = normalize_application_mode(mode)
        if mode == self.application_mode:
            return
        self._capture_mode_geometry()
        self.application_mode = mode
        self.settings["application_mode"] = mode
        profile = self._mode_window_profile()
        self.widget_locked = bool(profile.get("locked", False)) if mode == "widget" else False
        minimum_width, minimum_height = mode_minimum_size(mode)
        self.setMinimumSize(minimum_width, minimum_height)
        self._apply_mode_window_surface()
        self._apply_mode_window_flags()
        self._restore_mode_geometry()
        if hasattr(self, "workbench_shell"):
            self.workbench_shell.set_mode(mode)
            self.navigate(self.workbench_shell.current_route.workbench, self.workbench_shell.current_route.anchor)
        if persist:
            self._save_settings()

    def _save_settings(self) -> None:
        save_app_settings(self.settings)

    def _set_always_on_top(self, checked: bool, persist: bool = True) -> None:
        self.settings["always_on_top"] = checked
        self._apply_mode_window_flags()
        if persist:
            self._save_settings()

    def _toggle_widget_lock(self, locked: bool) -> None:
        """Lock the widget's current geometry against dragging and resizing."""
        if self.application_mode != "widget":
            self.lock_button.blockSignals(True)
            self.lock_button.setChecked(False)
            self.lock_button.blockSignals(False)
            return
        self.widget_locked = locked
        self._resize_edges = Qt.Edges()
        self.unsetCursor()
        self.settings["window"]["locked"] = locked
        if locked:
            self._capture_locked_geometry()
        self._save_settings()
        self.lock_button.setToolTip("窗口已固定，点击解除固定" if locked else "固定窗口位置和大小 / 解除固定")

    def _open_settings(self, section: str = "general") -> None:
        from ui.settings_center import SettingsCenterDialog

        if isinstance(section, bool):
            section = "general"
        dialog = SettingsCenterDialog(
            self.settings,
            self,
            initial_section=str(section),
        )
        dialog.settings_saved.connect(self._apply_settings)
        dialog.theme_previewed.connect(self._apply_theme)
        dialog.theme_preview_reverted.connect(self._apply_theme)
        dialog.backup_restored.connect(self._reload_local_data)
        dialog.data_location_changed.connect(self._reload_local_data)
        dialog.exec()
        frontier_page = self._loaded_pages.get("frontier")
        if frontier_page is not None:
            frontier_page.reload()

    def _open_research_inbox(self) -> None:
        from ui.quick_capture_dialog import QuickCaptureDialog

        dialog = QuickCaptureDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._reload_local_data()

    def _apply_settings(self, updated: dict) -> None:
        """Persist Settings while only touching the subsystems that changed.

        Reapplying window flags, global hooks and timers after every Save made
        a harmless setting edit feel like the application had frozen.  Keep a
        lightweight local snapshot and let each expensive subsystem opt in to
        reconfiguration only when its actual input differs.
        """
        previous = dict(self.settings)
        previous_appearance = previous.get("appearance", {}) if isinstance(previous.get("appearance"), dict) else {}
        previous_research = previous.get("research", {}) if isinstance(previous.get("research"), dict) else {}
        previous_ai = previous.get("ai", {}) if isinstance(previous.get("ai"), dict) else {}
        previous_jcr = previous.get("jcr", {}) if isinstance(previous.get("jcr"), dict) else {}
        previous_easyscholar = previous.get("easyscholar", {}) if isinstance(previous.get("easyscholar"), dict) else {}

        self.settings["opacity"] = int(updated["opacity"])
        self.settings["application_mode"] = normalize_application_mode(updated.get("application_mode", self.application_mode))
        appearance = updated.get("appearance", self.settings.get("appearance", {}))
        appearance = appearance if isinstance(appearance, dict) else {}
        self.settings["appearance"] = {
            "theme_id": "fog_teal",
            "density": str(appearance.get("density", "comfortable")),
        }
        research = updated.get("research", self.settings.get("research", {}))
        if isinstance(research, dict):
            self.settings["research"] = dict(research)
        for profile_key in ("widget_window", "software_window"):
            profile = updated.get(profile_key)
            if isinstance(profile, dict):
                self.settings[profile_key] = dict(profile)
        self.settings["autostart"] = bool(updated["autostart"])
        self.settings["close_to_tray"] = bool(updated.get("close_to_tray", False))
        self.settings["ready_submission_reminder"] = bool(updated["ready_submission_reminder"])
        self.settings["sidebar_position"] = "right" if updated.get("sidebar_position") == "right" else "left"
        self.settings["sidebar_collapse_mode"] = (
            "content" if updated.get("sidebar_collapse_mode") == "content" else "labels"
        )
        shortcut = updated.get("journal_import_shortcut", self.settings.get("journal_import_shortcut", {}))
        self.settings["journal_import_shortcut"] = dict(shortcut) if isinstance(shortcut, dict) else {"mode": "double_tab", "sequence": "Ctrl+Alt+J"}
        visibility_shortcut = updated.get(
            "window_visibility_shortcut",
            self.settings.get("window_visibility_shortcut", {}),
        )
        self.settings["window_visibility_shortcut"] = (
            dict(visibility_shortcut)
            if isinstance(visibility_shortcut, dict)
            else {"mode": "double_space", "sequence": "Ctrl+Alt+Space"}
        )
        self.settings["return_home_when_inactive"] = bool(updated.get("return_home_when_inactive", True))
        self.settings["frontier_background_refresh"] = bool(updated.get("frontier_background_refresh", True))
        self.settings["ai"] = dict(updated.get("ai", self.settings.get("ai", {})))
        self.settings["jcr"] = dict(updated.get("jcr", self.settings.get("jcr", {})))
        self.settings["easyscholar"] = dict(updated.get("easyscholar", self.settings.get("easyscholar", {})))
        self.settings["auto_backup"] = bool(updated["auto_backup"])
        if int(previous.get("opacity", 100) or 100) != self.settings["opacity"]:
            self._apply_mode_window_surface()
        if previous_appearance != self.settings["appearance"]:
            self._apply_theme(
                self.settings["appearance"]["theme_id"],
                self.settings["appearance"]["density"],
            )
        if normalize_application_mode(previous.get("application_mode", self.application_mode)) != self.settings["application_mode"]:
            self._apply_application_mode(self.settings["application_mode"], persist=False)
        if bool(previous.get("always_on_top", False)) != bool(updated["always_on_top"]):
            self._set_always_on_top(bool(updated["always_on_top"]), persist=False)
        if bool(previous.get("click_through", False)) != bool(updated["click_through"]):
            self._set_click_through(bool(updated["click_through"]), persist=False)
        if str(previous.get("sidebar_position", "left")) != self.settings["sidebar_position"]:
            self._set_sidebar_position(self.settings["sidebar_position"], persist=False)
        if bool(previous.get("sidebar_auto_hide", False)) != bool(updated["sidebar_auto_hide"]):
            self._set_sidebar_auto_hide(bool(updated["sidebar_auto_hide"]), persist=False)
        if previous.get("journal_import_shortcut", {}) != self.settings["journal_import_shortcut"]:
            self._apply_global_journal_import_shortcut(notify=True)
        if previous.get("window_visibility_shortcut", {}) != self.settings["window_visibility_shortcut"]:
            self._apply_global_visibility_shortcut(notify=True)
        self._save_settings()
        frontier_configuration_changed = (
            previous_research != self.settings.get("research", {})
            or previous_ai != self.settings.get("ai", {})
            or previous_jcr != self.settings.get("jcr", {})
            or previous_easyscholar != self.settings.get("easyscholar", {})
            or bool(previous.get("frontier_background_refresh", True))
            != self.settings["frontier_background_refresh"]
        )
        if frontier_configuration_changed:
            self._start_frontier_checks()
        if self.settings["auto_backup"] and not bool(previous.get("auto_backup", False)):
            self._schedule_idle_maintenance()
        if bool(previous.get("ready_submission_reminder", False)) != self.settings["ready_submission_reminder"]:
            self._check_submission_reminders()

    def _apply_theme(self, theme_id: str, density: str) -> None:
        application = QApplication.instance()
        if application is not None:
            apply_application_theme(application, theme_id, density)

    def _apply_global_journal_import_shortcut(self, notify: bool = False) -> None:
        status = self._global_hotkey.configure(self.settings.get("journal_import_shortcut", {}))
        self._global_hotkey_status = status
        shortcut = self.settings.get("journal_import_shortcut", {})
        if notify and str(shortcut.get("mode", "")).casefold() != "off" and not status.active:
            QMessageBox.warning(
                self,
                "全局组合键未启用",
                status.message + "。可换用“双击 Tab”或另一组带 Ctrl、Alt、Shift 或 Win 的组合键。",
            )

    def _open_global_journal_import(self) -> None:
        """Handle the Windows-wide hotkey even when another app had focus."""
        modal = QApplication.activeModalWidget()
        if modal is not None and modal.__class__.__name__ == "PaperDialog":
            # When already editing a paper, import straight into it rather
            # than opening a second chooser behind the dialog.
            modal._request_journal_import()
            return
        self.show_and_activate()
        self._switch_page(2)
        self._ensure_page_async(
            "papers",
            lambda: self._loaded_pages["papers"].open_global_journal_import(),
        )

    def _apply_global_visibility_shortcut(self, notify: bool = False) -> None:
        status = self._visibility_hotkey.configure(self.settings.get("window_visibility_shortcut", {}))
        self._visibility_hotkey_status = status
        shortcut = self.settings.get("window_visibility_shortcut", {})
        if notify and str(shortcut.get("mode", "")).casefold() != "off" and not status.active:
            QMessageBox.warning(
                self,
                "显示/隐藏快捷键未启用",
                status.message + "。可换用“双击空格”或另一组带 Ctrl、Alt、Shift 或 Win 的组合键。",
            )

    def _toggle_global_visibility(self) -> None:
        """Show from any foreground app, then hide back to the tray."""
        if self.isVisible() and not self.isMinimized():
            if hasattr(self, "_unlock_overlay"):
                self._unlock_overlay.hide()
            if should_hide_to_tray(self.application_mode):
                self.hide()
            else:
                self.showMinimized()
            return
        self.show_and_activate()

    def _update_profile_from_achievements(self) -> None:
        self._switch_page(5)
        self._ensure_page_async(
            "frontier",
            lambda: self._loaded_pages["frontier"].update_profile_now(),
        )

    def _reload_local_data(self) -> None:
        for page_key in ("todo", "papers", "notes", "journals", "frontier", "achievements", "special_issues", "tools"):
            page = self._loaded_pages.get(page_key)
            if page is not None:
                page.reload()
        self.home_page.refresh()
        self._check_submission_reminders()

    def _set_click_through(self, enabled: bool, persist: bool = True) -> None:
        self.settings["click_through"] = enabled
        self._apply_mode_window_flags()
        if enabled and self.application_mode == "widget":
            self._update_unlock_overlay()
        else:
            self._unlock_overlay.hide()
        if persist:
            self._save_settings()

    def _disable_click_through(self) -> None:
        self._set_click_through(False)

    def _update_unlock_overlay(self) -> None:
        if not self.settings.get("click_through") or not self.isVisible():
            self._unlock_overlay.hide()
            return
        origin = self.brand_label.mapToGlobal(QPoint(-5, -5))
        self._unlock_overlay.setGeometry(
            origin.x(), origin.y(), self.brand_label.width() + 10, self.brand_label.height() + 10
        )
        self._unlock_overlay.show()
        self._unlock_overlay.raise_()

    def _create_tray_icon(self) -> None:
        self.tray_icon: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        menu = QMenu(self)
        show_action = QAction("显示科研助手", self)
        show_action.triggered.connect(self.show_and_activate)
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self._quit_application)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray_icon = QSystemTrayIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self)
        self.tray_icon.setToolTip(f"科研助手 v{APP_VERSION}")
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def _quit_application(self) -> None:
        """Bypass close-to-tray only for the tray menu's explicit Exit."""
        self._exit_requested = True
        self.close()

    def _start_reminder_checks(self) -> None:
        self._reminder_timer = QTimer(self)
        self._reminder_timer.setInterval(60 * 60 * 1000)
        self._reminder_timer.timeout.connect(self._check_submission_reminders)
        self._reminder_timer.start()
        QTimer.singleShot(500, self._check_submission_reminders)

    def _start_frontier_checks(self) -> None:
        if hasattr(self, "_frontier_timer") and self._frontier_timer is not None:
            self._frontier_timer.stop()
        if hasattr(self, "_profile_ai_timer") and self._profile_ai_timer is not None:
            self._profile_ai_timer.stop()
        if hasattr(self, "_journal_ai_timer") and self._journal_ai_timer is not None:
            self._journal_ai_timer.stop()

        # These two opt-in jobs have their own once-per-day guards inside the
        # pages. Keep them separate from public-source refresh so users can
        # still benefit from a local profile update when background searching
        # is disabled.
        self._profile_ai_timer = QTimer(self)
        self._profile_ai_timer.setInterval(60 * 60 * 1000)
        self._profile_ai_timer.timeout.connect(self._schedule_idle_maintenance)
        self._profile_ai_timer.start()
        self._journal_ai_timer = QTimer(self)
        self._journal_ai_timer.setInterval(60 * 60 * 1000)
        self._journal_ai_timer.timeout.connect(self._schedule_idle_maintenance)
        self._journal_ai_timer.start()

        if not self.settings.get("frontier_background_refresh", True):
            self._frontier_timer = None
            return
        self._frontier_timer = QTimer(self)
        self._frontier_timer.setInterval(60 * 60 * 1000)
        self._frontier_timer.timeout.connect(self._schedule_idle_maintenance)
        self._frontier_timer.start()

    def _refresh_frontier_after_startup(self) -> None:
        if self.settings.get("frontier_background_refresh", True):
            self.frontier_page.auto_refresh_if_due()

    def _auto_update_frontier_profile(self) -> None:
        self.frontier_page.auto_update_profile_if_due(
            refresh_after=bool(self.settings.get("frontier_background_refresh", True))
        )

    def _auto_enrich_new_journals(self) -> None:
        self.journal_page.auto_update_easyscholar_if_due()
        self.journal_page.auto_enrich_new_if_due()
        # The page persists its daily-attempt marker before starting network
        # work. Refresh the shared in-memory settings so a later window save
        # cannot overwrite that marker and submit the same batch again today.
        self.settings = load_app_settings()

    def _show_frontier_notification(self, count: int, brief: str) -> None:
        if self.tray_icon:
            self.tray_icon.showMessage(
                "每日前沿",
                brief or f"发现 {count} 篇相关新论文。",
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def _check_submission_reminders(self) -> None:
        if self._reminder_dialog and self._reminder_dialog.isVisible():
            return
        reminders = due_submission_reminders(load_papers(), load_reminder_state())
        if reminders:
            from ui.reminder_dialog import SubmissionReminderDialog

            self._show_reminder_dialog(SubmissionReminderDialog, reminders)
            return
        if not self.settings.get("ready_submission_reminder", True):
            return
        ready_reminders = due_ready_submission_reminders(load_papers(), load_dismissed_reminders())
        if ready_reminders:
            from ui.reminder_dialog import ReadySubmissionDialog

            self._show_reminder_dialog(ReadySubmissionDialog, ready_reminders)

    def _show_reminder_dialog(self, dialog_type, reminders: list[dict]) -> None:
        from ui.reminder_dialog import SubmissionReminderDialog

        dialog = dialog_type(reminders, self)
        if isinstance(dialog, SubmissionReminderDialog):
            dialog.snoozed.connect(self._snooze_submission_reminders)
            dialog.handled.connect(self._mark_submission_reminders_handled)
        else:
            dialog.dismissed.connect(self._dismiss_submission_reminders)
        dialog.finished.connect(self._reminder_dialog_finished)
        self._reminder_dialog = dialog
        dialog.show()
        dialog.adjustSize()
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            dialog.move(available.center() - dialog.rect().center())
        dialog.raise_()
        dialog.activateWindow()

    def _reminder_dialog_finished(self) -> None:
        self._reminder_dialog = None
        QTimer.singleShot(0, self._check_submission_reminders)

    @staticmethod
    def _dismiss_submission_reminders(reminder_ids: list[str]) -> None:
        dismissed = load_dismissed_reminders()
        dismissed.update(str(reminder_id) for reminder_id in reminder_ids)
        save_dismissed_reminders(dismissed)

    @staticmethod
    def _snooze_submission_reminders(reminder_ids: list[str], days: int) -> None:
        state = load_reminder_state()
        until = (date.today() + timedelta(days=max(1, int(days)))).isoformat()
        snoozed = dict(state.get("snoozed_until", {}))
        for reminder_id in reminder_ids:
            if str(reminder_id):
                snoozed[str(reminder_id)] = until
        state["snoozed_until"] = snoozed
        save_reminder_state(state)

    def _mark_submission_reminders_handled(self, reminder_ids: list[str]) -> None:
        """Reset the status-update clock and leave an auditable timeline note."""
        targets: set[tuple[str, str]] = set()
        for reminder_id in reminder_ids:
            parts = str(reminder_id).split("|", 4)
            if len(parts) >= 3 and parts[0] == "status":
                targets.add((parts[1], parts[2]))
        if not targets:
            return
        papers = load_papers()
        today_key = date.today().isoformat()
        changed = 0
        for paper in papers:
            paper_id = str(paper.get("id", ""))
            for journal in paper.get("journals", []):
                journal_id = str(journal.get("id", ""))
                if (paper_id, journal_id) not in targets:
                    continue
                journal["status_updated_at"] = today_key
                status = str(journal.get("status", "准备投稿"))
                timeline = [dict(item) for item in journal.get("timeline", []) if isinstance(item, dict)]
                if timeline and timeline[-1].get("date") == today_key and timeline[-1].get("status") == status:
                    existing = str(timeline[-1].get("note", "")).strip()
                    timeline[-1]["note"] = existing or "提醒已处理"
                else:
                    timeline.append(
                        {"id": uuid4().hex, "date": today_key, "status": status, "note": "提醒已处理"}
                    )
                journal["timeline"] = timeline
                changed += 1
        if not changed:
            return
        saved = save_papers(papers)
        if not saved.get("saved", False):
            # A legacy future date elsewhere is deliberately never overwritten.
            # Surface the recovery route instead of pretending the reminder was handled.
            QMessageBox.warning(
                self,
                "尚未标记为已处理",
                "检测到投稿记录中有晚于今天的历史日期。请先在“论文投稿记录”使用“一键修正为今天”。",
            )
            return
        state = load_reminder_state()
        snoozed = dict(state.get("snoozed_until", {}))
        for reminder_id in reminder_ids:
            snoozed.pop(str(reminder_id), None)
        state["snoozed_until"] = snoozed
        save_reminder_state(state)
        paper_page = self._loaded_pages.get("papers")
        if paper_page is not None:
            paper_page.reload()
        else:
            self._dirty_pages.add("papers")
        self.home_page.refresh()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick}:
            self.show_and_activate()

    def start_in_tray(self) -> None:
        """Start quietly in the Windows notification area, without a taskbar button."""
        if should_hide_to_tray(self.application_mode):
            self._hide_to_tray(notify=False)
        else:
            self.show_and_activate()

    def _hide_to_tray(self, notify: bool = False) -> None:
        """Hide cleanly instead of creating a tiny Tool-window taskbar remnant."""
        self._hide_to_tray_queued = False
        self._capture_mode_geometry()
        self._save_settings()
        if not should_hide_to_tray(self.application_mode):
            self.showMinimized()
            return
        if not self.tray_icon:
            # A tray-less Windows shell still needs a reachable standard window.
            self.showMinimized()
            return
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.hide()
        self._unlock_overlay.hide()
        if notify:
            self.tray_icon.showMessage(
                "科研助手",
                "已收起到系统托盘。双击托盘图标或使用显示快捷键可打开。",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )

    def show_and_activate(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
        self._bring_to_front_on_windows()
        self._update_unlock_overlay()

    def _bring_to_front_on_windows(self) -> None:
        """Make global hotkey restoration reliable across foreground apps.

        Qt's ``raise_`` is intentionally conservative when a different process
        owns the foreground.  The temporary z-order lift is only a fallback;
        it never changes the user's persistent ``always_on_top`` preference.
        """
        if sys.platform != "win32":
            return
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.ShowWindow.restype = ctypes.c_int
            user32.BringWindowToTop.argtypes = [ctypes.c_void_p]
            user32.BringWindowToTop.restype = ctypes.c_int
            user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
            user32.SetForegroundWindow.restype = ctypes.c_int
            user32.GetForegroundWindow.restype = ctypes.c_void_p
            user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            user32.GetWindowThreadProcessId.restype = ctypes.c_uint
            user32.AttachThreadInput.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
            user32.AttachThreadInput.restype = ctypes.c_int
            user32.SetFocus.argtypes = [ctypes.c_void_p]
            user32.SetFocus.restype = ctypes.c_void_p
            kernel32.GetCurrentThreadId.restype = ctypes.c_uint
            hwnd_value = int(self.winId())
            if not hwnd_value:
                return
            hwnd = ctypes.c_void_p(hwnd_value)
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            ]
            user32.SetWindowPos.restype = ctypes.c_int
            sw_restore = 9
            swp_nosize = 0x0001
            swp_nomove = 0x0002
            swp_showwindow = 0x0040
            flags = swp_nosize | swp_nomove | swp_showwindow
            hwnd_topmost = ctypes.c_void_p(-1)
            hwnd_notopmost = ctypes.c_void_p(-2)

            # A passive keyboard hook is invoked while another process still
            # owns the foreground.  Windows intentionally rejects a plain
            # SetForegroundWindow in that situation.  Temporarily sharing the
            # input queues is the supported way to transfer focus for this
            # user-triggered shortcut; it is detached immediately afterwards.
            foreground = user32.GetForegroundWindow()
            foreground_thread = user32.GetWindowThreadProcessId(
                ctypes.c_void_p(int(foreground or 0)), None
            ) if foreground else 0
            current_thread = kernel32.GetCurrentThreadId()
            queues_attached = bool(
                foreground_thread
                and foreground_thread != current_thread
                and user32.AttachThreadInput(foreground_thread, current_thread, 1)
            )
            try:
                user32.ShowWindow(hwnd, sw_restore)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.SetFocus(hwnd)
                foreground = user32.GetForegroundWindow()
                if int(foreground or 0) != hwnd_value:
                    # A brief z-order lift is the last fallback for shells
                    # that still refuse activation.  The saved always-on-top
                    # preference is never changed.
                    user32.SetWindowPos(hwnd, hwnd_topmost, 0, 0, 0, 0, flags)
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                    user32.SetFocus(hwnd)
                    if not self.settings.get("always_on_top", False):
                        user32.SetWindowPos(hwnd, hwnd_notopmost, 0, 0, 0, 0, flags)
            finally:
                if queues_attached:
                    user32.AttachThreadInput(foreground_thread, current_thread, 0)
        except (AttributeError, OSError, TypeError, ValueError):
            # The Qt path above remains a safe fallback on restricted shells.
            return

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (
            not should_hide_to_tray(self.application_mode)
            or event.type() != QEvent.Type.WindowStateChange
            or not self.isMinimized()
            or getattr(self, "_hide_to_tray_queued", False)
        ):
            return
        # Tool windows can otherwise leave a small ghost rectangle around the
        # taskbar. Queue this after Qt completes its state transition.
        self._hide_to_tray_queued = True
        QTimer.singleShot(0, self._hide_to_tray)

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if hasattr(self, "_unlock_overlay"):
            QTimer.singleShot(0, self._update_unlock_overlay)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self._collapse_mode() == "content":
            self._schedule_sidebar_collapse()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        minimum_width, _minimum_height = mode_minimum_size(self.application_mode)
        if not self._content_collapsed and self.width() >= minimum_width:
            self._content_restore_width = self.width()
            self._capture_window_dimensions()
        if hasattr(self, "_unlock_overlay"):
            QTimer.singleShot(0, self._update_unlock_overlay)

    def hideEvent(self, event) -> None:
        if hasattr(self, "_unlock_overlay"):
            self._unlock_overlay.hide()
        super().hideEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "_unlock_overlay"):
            QTimer.singleShot(0, self._update_unlock_overlay)

    def _edges_at(self, global_pos: QPoint) -> Qt.Edges:
        frame = self.frameGeometry()
        edges = Qt.Edges()
        if abs(global_pos.x() - frame.left()) <= self._edge_margin:
            edges |= Qt.Edge.LeftEdge
        if abs(global_pos.x() - frame.right()) <= self._edge_margin:
            edges |= Qt.Edge.RightEdge
        if abs(global_pos.y() - frame.top()) <= self._edge_margin:
            edges |= Qt.Edge.TopEdge
        if abs(global_pos.y() - frame.bottom()) <= self._edge_margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _set_resize_cursor(self, edges: Qt.Edges) -> None:
        diagonal_forward = bool(edges & Qt.Edge.TopEdge and edges & Qt.Edge.LeftEdge) or bool(edges & Qt.Edge.BottomEdge and edges & Qt.Edge.RightEdge)
        diagonal_backward = bool(edges & Qt.Edge.TopEdge and edges & Qt.Edge.RightEdge) or bool(edges & Qt.Edge.BottomEdge and edges & Qt.Edge.LeftEdge)
        if diagonal_forward:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif diagonal_backward:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def _resize_from_global_pos(self, global_pos: QPoint) -> None:
        geometry = QRect(self._resize_start_geometry)
        delta = global_pos - self._resize_start_pos
        min_width = self.minimumWidth()
        min_height = self.minimumHeight()
        if self._resize_edges & Qt.Edge.LeftEdge:
            geometry.setLeft(min(self._resize_start_geometry.left() + delta.x(), self._resize_start_geometry.right() - min_width + 1))
        if self._resize_edges & Qt.Edge.RightEdge:
            geometry.setRight(max(self._resize_start_geometry.right() + delta.x(), self._resize_start_geometry.left() + min_width - 1))
        if self._resize_edges & Qt.Edge.TopEdge:
            geometry.setTop(min(self._resize_start_geometry.top() + delta.y(), self._resize_start_geometry.bottom() - min_height + 1))
        if self._resize_edges & Qt.Edge.BottomEdge:
            geometry.setBottom(max(self._resize_start_geometry.bottom() + delta.y(), self._resize_start_geometry.top() + min_height - 1))
        self.setGeometry(geometry)

    def eventFilter(self, watched, event) -> bool:
        belongs_to_window = self._belongs_to_window(watched)
        interaction_events = {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.KeyPress,
            QEvent.Type.Wheel,
        }
        if belongs_to_window and event.type() in interaction_events:
            self._cancel_idle_background_work()
            self._schedule_idle_maintenance()
            if self.workbench_shell.current_route.workbench != "home":
                self._home_idle_timer.start()
        if not belongs_to_window or not isinstance(event, QMouseEvent):
            return super().eventFilter(watched, event)
        if self.widget_locked:
            if event.type() == QEvent.Type.MouseMove:
                self.unsetCursor()
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseMove:
            position = event.globalPosition().toPoint()
            if self._resize_edges:
                self._resize_from_global_pos(position)
                return True
            self._set_resize_cursor(self._edges_at(position))
        elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            edges = self._edges_at(event.globalPosition().toPoint())
            if edges:
                self._resize_edges = edges
                self._resize_start_geometry = self.geometry()
                self._resize_start_pos = event.globalPosition().toPoint()
                return True
        elif event.type() == QEvent.Type.MouseButtonRelease and self._resize_edges:
            self._resize_edges = Qt.Edges()
            self.unsetCursor()
            return True
        return super().eventFilter(watched, event)

    def _belongs_to_window(self, watched) -> bool:
        widget = watched if isinstance(watched, QWidget) else None
        while widget is not None:
            if widget is self:
                return True
            widget = widget.parentWidget()
        return False

    def _apply_styles(self) -> None:
        # v11 owns visual styling in ``ui.theme``.  The historical stylesheet
        # below is retained temporarily only for selector-reference migration;
        # do not parse/apply it at startup, because doing so adds a visible
        # navy flash and needless work before the selected semantic theme wins.
        self.setStyleSheet("")
        appearance = self.settings.get("appearance", {})
        appearance = appearance if isinstance(appearance, dict) else {}
        self._apply_theme(
            str(appearance.get("theme_id", "fog_teal")),
            str(appearance.get("density", "comfortable")),
        )

    def closeEvent(self, event) -> None:
        loaded_pages = getattr(self, "_loaded_pages", None)
        pages_to_save = (
            (loaded_pages.get("todo"), loaded_pages.get("papers"))
            if isinstance(loaded_pages, dict)
            else (getattr(self, "todo_page", None), getattr(self, "paper_page", None))
        )
        for page in pages_to_save:
            if page is not None:
                page.save()
        if bool(self.settings.get("close_to_tray", False)) and not self._exit_requested:
            event.ignore()
            self._hide_to_tray(notify=True)
            return
        active_threads = [
            self._special_action_thread,
            self._backup_thread,
            self._storage_maintenance_thread,
            *self._idle_background_workers,
            *self._page_loaders.values(),
        ]
        if isinstance(loaded_pages, dict):
            for page in loaded_pages.values():
                active_threads.extend(
                    getattr(page, name, None)
                    for name in (
                        "_load_worker",
                        "_worker",
                        "_refresh_worker",
                        "_profile_ai_worker",
                        "_ai_worker",
                        "_comment_ai_worker",
                        "_metadata_worker",
                        "_jcr_worker",
                        "_easy_worker",
                    )
                )
        def running(thread):
            try:
                return thread is not None and thread.isRunning()
            except RuntimeError:
                return False  # A finished worker may already have been released by Qt.

        for thread in active_threads:
            if running(thread):
                thread.requestInterruption()
        from utils.database_write_queue import WRITE_QUEUE

        if not getattr(self, "_shutdown_ready", False):
            waiting = any(running(thread) for thread in active_threads)
            if waiting or WRITE_QUEUE.pending_count() or WRITE_QUEUE.failures():
                event.ignore()
                if not getattr(self, "_shutdown_waiting", False):
                    self._shutdown_waiting = True
                    self.statusBar().showMessage("正在停止任务并完成保存，完成后自动退出…")
                    self._shutdown_timer = QTimer(self)
                    self._shutdown_timer.setInterval(100)

                    def finish_shutdown() -> None:
                        if any(running(thread) for thread in active_threads):
                            return
                        if WRITE_QUEUE.pending_count():
                            return
                        if WRITE_QUEUE.failures():
                            self._shutdown_timer.stop()
                            self._shutdown_waiting = False
                            self._check_save_failures()
                            return
                        self._shutdown_timer.stop()
                        self._shutdown_ready = True
                        self.close()

                    self._shutdown_timer.timeout.connect(finish_shutdown)
                    self._shutdown_timer.start()
                return
        self._capture_mode_geometry()
        self._save_settings()
        if self.tray_icon:
            self.tray_icon.hide()
        if hasattr(self, "_unlock_overlay"):
            self._unlock_overlay.hide()
        if hasattr(self, "_global_hotkey"):
            self._global_hotkey.close()
        if hasattr(self, "_visibility_hotkey"):
            self._visibility_hotkey.close()
        event.accept()
        # The app normally has no visible taskbar window, so explicitly quit the
        # event loop when the user chooses the close/exit action.
        QApplication.quit()
