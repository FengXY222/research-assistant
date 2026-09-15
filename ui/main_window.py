from __future__ import annotations

import ctypes
import os
import sys
from datetime import date, timedelta
from uuid import uuid4

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QParallelAnimationGroup, QPropertyAnimation, Property, QRect, Qt, QTimer, Signal
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
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ui.paper_page import PaperDialog, PaperPage
from ui.journal_library_page import JournalLibraryPage
from ui.todo_page import TodoPage
from ui.home_page import HomePage
from ui.frontier_page import DailyFrontierPage
from ui.special_issue_page import SpecialIssuePage
from ui.special_issue_dialog import SpecialIssueDialog
from ui.achievements_page import AchievementsPage
from ui.notes_page import NotesPage
from ui.reminder_dialog import ReadySubmissionDialog, SubmissionReminderDialog
from ui.settings_dialog import SettingsDialog
from ui.quick_capture_dialog import QuickCaptureDialog
from ui.theme import apply_application_theme
from ui.workbench_shell import WorkbenchShell
from utils.app_info import APP_VERSION
from utils.global_hotkey import GlobalHotkeyManager, VK_SPACE
from utils.file_manager import (
    load_app_settings,
    load_dismissed_reminders,
    load_reminder_state,
    load_papers,
    maybe_create_daily_backup,
    save_app_settings,
    save_dismissed_reminders,
    save_papers,
    save_reminder_state,
    sync_journal_library_from_papers,
)
from utils.submission_reminders import due_ready_submission_reminders, due_submission_reminders
from utils.special_issue_repository import (
    add_special_issue_journal_to_library,
    add_special_issue_to_submission_path,
    associate_special_issue,
    create_special_issue_preparation_task,
    load_special_issue_store,
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
    ("home", "home", "HOME"),
    ("work", "work", "WORK"),
    ("papers", "papers", "PAPERS"),
    ("library", "library", "LIBRARY"),
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


class VerticalNavButton(QPushButton):
    """A compact sidebar button with a true vertical label."""

    def __init__(self, text: str, page_index: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.page_index = page_index
        self.setToolTip("切换页面")

    def paintEvent(self, event) -> None:
        painter = QStylePainter(self)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        text = option.text
        option.text = ""
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)

        painter.save()
        painter.translate(0, self.height())
        painter.rotate(-90)
        option.rect = QRect(0, 0, self.height(), self.width())
        option.text = text
        painter.drawControl(QStyle.ControlElement.CE_PushButtonLabel, option)
        painter.restore()


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
        try:
            maybe_create_daily_backup(self.settings)
        except OSError:
            pass
        sync_journal_library_from_papers(load_papers())
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
        self._sidebar_pinned = bool(self.settings.get("sidebar_pinned", False))
        self._sidebar_full_width = 52
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
        self._hide_to_tray_queued = False
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
        QTimer.singleShot(0, self._run_initial_navigation_state)

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
        for _key, route, text in NAVIGATION_ITEMS:
            button = self._make_nav_button(text, route)
            button.clicked.connect(lambda _checked=False, value=route: self.navigate(value))
            button.setMinimumHeight(66)
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
        self.todo_page = TodoPage()
        self.paper_page = PaperPage()
        self.notes_page = NotesPage()
        self.journal_page = JournalLibraryPage()
        self.frontier_page = DailyFrontierPage()
        self.special_issue_page = SpecialIssuePage()
        self.achievements_page = AchievementsPage()
        self.workbench_shell = WorkbenchShell(
            {
                "home": self.home_page,
                "todo": self.todo_page,
                "papers": self.paper_page,
                "notes": self.notes_page,
                "journals": self.journal_page,
                "frontier": self.frontier_page,
                "special_issues": self.special_issue_page,
                "achievements": self.achievements_page,
            }
        )
        self.workbench_shell.setObjectName("contentStack")
        self.workbench_shell.set_mode(self.application_mode)
        self.home_page.open_todo.connect(lambda: self.navigate("todo"))
        self.home_page.open_papers.connect(lambda: self.navigate("papers"))
        self.home_page.open_paper_journal.connect(self._reveal_home_paper_journal)
        self.home_page.open_notes.connect(lambda: self.navigate("notes"))
        self.home_page.open_frontier.connect(lambda: self.navigate("frontier"))
        self.todo_page.changed.connect(self.home_page.refresh)
        self.paper_page.changed.connect(self.home_page.refresh)
        self.paper_page.changed.connect(self._check_submission_reminders)
        self.paper_page.changed.connect(self.journal_page.reload)
        self.paper_page.changed.connect(self.achievements_page.reload)
        self.notes_page.changed.connect(self.home_page.refresh)
        self.journal_page.changed.connect(self.home_page.refresh)
        self.journal_page.changed.connect(self.paper_page.reload)
        self.journal_page.changed.connect(self.frontier_page.reload)
        self.frontier_page.changed.connect(self.home_page.refresh)
        self.achievements_page.changed.connect(self.home_page.refresh)
        self.achievements_page.profile_update_requested.connect(self._update_profile_from_achievements)
        self.frontier_page.daily_ready.connect(self._show_frontier_notification)
        self.frontier_page.open_journal_library.connect(lambda: self.navigate("journals"))
        self.special_issue_page.open_workbench.connect(self._open_special_issue_workbench)
        self.special_issue_page.refresh_progress.connect(self._show_special_issue_progress)
        self.special_issue_page.refresh_completed.connect(self._special_issue_refresh_completed)
        self.special_issue_page.refresh_failed.connect(self._special_issue_refresh_failed)
        content_layout.addWidget(self.workbench_shell, 1)
        self.shell_layout.addWidget(self.content_panel, 1)
        self.navigate("home")

    def _make_nav_button(self, text: str, page_index: str) -> VerticalNavButton:
        button = VerticalNavButton(text, page_index)
        button.setCheckable(True)
        button.setObjectName("navButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_buttons.append(button)
        return button

    def _run_initial_navigation_state(self) -> None:
        """Drop a queued startup layout pass when the window has closed."""
        try:
            self._apply_initial_navigation_state()
        except RuntimeError:
            return

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
        target = self.workbench_shell.navigate(route, anchor)
        for button in self._nav_buttons:
            button.setChecked(button.page_index == target.workbench)
        if target.workbench == "home":
            self._home_idle_timer.stop()
        else:
            self._home_idle_timer.start()

    def _open_special_issue_workbench(self, selected_issue_id: str = "") -> None:
        """Open or focus the one large workbench without changing widget mode."""
        if selected_issue_id:
            try:
                current_store = load_special_issue_store()
                current_item = next(
                    (value for value in current_store.get("items", []) if str(value.get("id", "")) == str(selected_issue_id)),
                    None,
                )
                if current_item is not None and str(current_item.get("status", "unread")) == "unread":
                    set_special_issue_status(selected_issue_id, "read")
                self._record_special_issue_signal(selected_issue_id, "detail_open")
            except Exception:
                pass
        store = load_special_issue_store()
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
        store = load_special_issue_store()
        self.special_issue_page.reload()
        dialog = self._special_issue_dialog
        if dialog is not None:
            dialog.reload_data(store, store.get("items", []), load_papers())
            if selected_issue_id:
                dialog.select_issue(selected_issue_id)
            dialog.finish_progress(message or "操作完成")

    def _run_special_issue_action(self, selected_issue_id: str, message: str, action) -> None:
        dialog = self._special_issue_dialog
        if dialog is not None:
            dialog.set_progress(15, message)
        try:
            action()
        except Exception as error:
            if dialog is not None:
                dialog.finish_progress(f"未完成：{error}")
            return
        self._reload_special_issue_surfaces(selected_issue_id, "已完成，工作台保持打开")

    def _associate_special_issue(self, issue_id: str, paper_ids: list[str]) -> None:
        def action() -> None:
            associate_special_issue(issue_id, paper_ids)
            self._record_special_issue_signal(issue_id, "paper_association")

        self._run_special_issue_action(issue_id, "正在关联论文…", action)

    def _add_special_issue_path(self, issue_id: str, paper_id: str) -> None:
        def action() -> None:
            add_special_issue_to_submission_path(issue_id, paper_id)
            self.paper_page.reload()
            self.journal_page.reload()
            self.home_page.refresh()

        self._run_special_issue_action(issue_id, "正在入库并创建投稿候选…", action)

    def _add_special_issue_journal(self, issue_id: str) -> None:
        def action() -> None:
            add_special_issue_journal_to_library(issue_id)
            self.journal_page.reload()

        self._run_special_issue_action(issue_id, "正在加入期刊库…", action)

    def _create_special_issue_task(self, issue_id: str, paper_id: str) -> None:
        def action() -> None:
            create_special_issue_preparation_task(issue_id, paper_id)
            self.todo_page.reload()
            self.home_page.refresh()

        self._run_special_issue_action(issue_id, "正在创建准备任务…", action)

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

            store = load_special_issue_store()
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
        # Run after the compact widget and tray have painted. The page itself
        # owns the 24-hour boundary and prevents duplicate workers.
        if hasattr(self, "_special_issue_timer") and self._special_issue_timer is not None:
            self._special_issue_timer.stop()
        if os.environ.get("RESEARCH_ASSISTANT_DISABLE_BACKGROUND") == "1":
            self._special_issue_timer = None
            return
        self._special_issue_timer = QTimer(self)
        self._special_issue_timer.setInterval(60 * 60 * 1000)
        self._special_issue_timer.timeout.connect(self.special_issue_page.auto_refresh_if_due)
        self._special_issue_timer.start()
        QTimer.singleShot(12000, self.special_issue_page.auto_refresh_if_due)

    def _reveal_home_paper_journal(self, paper_id: str, journal_id: str) -> None:
        """Follow a HOME action directly to its owning journal history row."""
        self.navigate("papers")
        QTimer.singleShot(0, lambda: self.paper_page.reveal_journal(paper_id, journal_id))

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

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        dialog.settings_saved.connect(self._apply_settings)
        dialog.theme_previewed.connect(self._apply_theme)
        dialog.theme_preview_reverted.connect(self._apply_theme)
        dialog.backup_restored.connect(self._reload_local_data)
        dialog.data_location_changed.connect(self._reload_local_data)
        dialog.exec()

    def _open_research_inbox(self) -> None:
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
            "theme_id": str(appearance.get("theme_id", "fog_teal")),
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
            try:
                maybe_create_daily_backup(self.settings)
            except OSError:
                pass
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
        if isinstance(modal, PaperDialog):
            # When already editing a paper, import straight into it rather
            # than opening a second chooser behind the dialog.
            modal._request_journal_import()
            return
        self.show_and_activate()
        self._switch_page(2)
        QTimer.singleShot(160, self.paper_page.open_global_journal_import)

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
        self.frontier_page.update_profile_now()

    def _reload_local_data(self) -> None:
        self.todo_page.reload()
        self.paper_page.reload()
        self.notes_page.reload()
        self.journal_page.reload()
        self.frontier_page.reload()
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
        exit_action.triggered.connect(self.close)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray_icon = QSystemTrayIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self)
        self.tray_icon.setToolTip(f"科研助手 v{APP_VERSION}")
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

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
        self._profile_ai_timer.timeout.connect(self._auto_update_frontier_profile)
        self._profile_ai_timer.start()
        self._journal_ai_timer = QTimer(self)
        self._journal_ai_timer.setInterval(60 * 60 * 1000)
        self._journal_ai_timer.timeout.connect(self._auto_enrich_new_journals)
        self._journal_ai_timer.start()
        QTimer.singleShot(11000, self._auto_update_frontier_profile)
        QTimer.singleShot(13000, self._auto_enrich_new_journals)

        if not self.settings.get("frontier_background_refresh", True):
            self._frontier_timer = None
            return
        self._frontier_timer = QTimer(self)
        self._frontier_timer.setInterval(60 * 60 * 1000)
        self._frontier_timer.timeout.connect(self.frontier_page.auto_refresh_if_due)
        self._frontier_timer.start()
        # Draw the tray/widget and expose cached local data first. Network
        # work must not make a desktop sticky note feel slow at launch.
        QTimer.singleShot(9000, self._refresh_frontier_after_startup)

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
            self._show_reminder_dialog(SubmissionReminderDialog, reminders)
            return
        if not self.settings.get("ready_submission_reminder", True):
            return
        ready_reminders = due_ready_submission_reminders(load_papers(), load_dismissed_reminders())
        if ready_reminders:
            self._show_reminder_dialog(ReadySubmissionDialog, ready_reminders)

    def _show_reminder_dialog(self, dialog_type, reminders: list[dict]) -> None:
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
        self.paper_page.reload()
        self.home_page.refresh()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick}:
            self.show_and_activate()

    def start_in_tray(self) -> None:
        """Start quietly in the Windows notification area, without a taskbar button."""
        if should_hide_to_tray(self.application_mode):
            self._hide_to_tray(notify=True)
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
        if belongs_to_window and self.workbench_shell.current_route.workbench != "home" and event.type() in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.KeyPress,
            QEvent.Type.Wheel,
        }:
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
        return
        self.setStyleSheet(
            """
            QWidget { color: #f4f6ff; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; }
            QMainWindow { background: transparent; }
            #windowRoot { background: rgba(7, 13, 31, 218); border-radius: 2px; }
            #contentStack { background: rgba(7, 13, 31, 218); }
            #sidebar { background: rgba(7, 12, 29, 205); border-right: 1px solid rgba(255,255,255,35); }
            #topbar { background: rgba(7, 12, 29, 195); border-bottom: 1px solid rgba(255,255,255,35); }
            #brand { color: #ffffff; font-size: 15px; font-weight: 700; }
            #topHint, #toolbarLabel { color: rgba(255,255,255,145); font-size: 11px; }
            #navButton { background: transparent; border: 0; border-radius: 4px; padding: 7px 5px; color: rgba(255,255,255,130); font-size: 10px; letter-spacing: 1px; }
            #navButton:hover { background: rgba(255,255,255,28); color: #ffffff; }
            #navButton:checked { color: #ffffff; background: rgba(255,255,255,40); }
            #iconButton, #windowButton, #closeButton { background: transparent; border: 0; border-radius: 4px; padding: 4px 7px; color: #ffffff; font-size: 18px; }
            #lockButton { background: transparent; border: 0; border-radius: 4px; padding: 5px 7px; color: rgba(255,255,255,180); font-size: 11px; }
            #iconButton:hover, #windowButton:hover { background: rgba(255,255,255,35); }
            #iconButton:checked { color: #61e6a1; background: rgba(97,230,161,25); }
            #lockButton:hover { background: rgba(255,255,255,35); color: #ffffff; }
            #lockButton:checked { color: #ffe36a; background: rgba(255,227,106,30); }
            #closeButton:hover { background: rgba(255,80,80,130); }
            QLineEdit, QDateEdit, QComboBox, QPlainTextEdit { background: rgba(255,255,255,24); color: #ffffff; border: 1px solid rgba(255,255,255,50); border-radius: 5px; padding: 7px 9px; selection-background-color: rgba(60,180,255,130); }
            QLineEdit:focus, QDateEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border-color: rgba(90,200,255,180); }
            #primaryButton { background: #30d978; color: #07131f; border: 0; border-radius: 5px; padding: 7px 12px; font-weight: 700; }
            #primaryButton:hover { background: #64f39c; }
            #subtleButton { background: rgba(255,255,255,28); color: rgba(255,255,255,190); border: 0; border-radius: 5px; padding: 6px 9px; }
            #subtleButton:hover { background: rgba(255,255,255,50); color: #ffffff; }
            #todoList { background: transparent; border: 0; outline: 0; }
            #todoList::item { background: rgba(255,255,255,10); border: 0; border-bottom: 1px solid rgba(255,255,255,25); padding: 2px; }
            #todoText { color: rgba(255,255,255,235); font-size: 17px; }
            #doneText { color: rgba(255,255,255,115); text-decoration: line-through; font-size: 17px; }
            #todoDot, #todoYellowDot { color: #ffe451; font-size: 15px; }
            #todoRedDot { color: #ff6f7d; font-size: 15px; }
            #todoGreenDot { color: #61e6a1; font-size: 15px; }
            #doneDot { color: #61e6a1; font-size: 15px; }
            #todoMeta { color: rgba(180,210,245,155); font-size: 10px; }
            #priorityRedTag, #priorityYellowTag, #priorityGreenTag { border-radius: 8px; padding: 3px 6px; font-size: 10px; }
            #priorityRedTag { background: rgba(255,111,125,48); color: #ff98a1; }
            #priorityYellowTag { background: rgba(255,214,107,48); color: #ffd66b; }
            #priorityGreenTag { background: rgba(97,230,161,48); color: #8bf0b7; }
            #historyToggle { background: transparent; color: rgba(210,226,250,185); border: 1px solid rgba(140,190,235,65); border-radius: 5px; padding: 6px 8px; text-align: left; }
            #historyToggle:hover { background: rgba(255,255,255,22); color: #ffffff; }
            #historyList { background: rgba(15,28,54,180); color: rgba(235,242,255,205); border: 1px solid rgba(140,190,235,65); border-radius: 6px; outline: 0; font-size: 11px; }
            #historyList::item { border-bottom: 1px solid rgba(255,255,255,20); padding: 4px 8px; }
            #quadrantOverlay { background: rgba(5, 12, 29, 247); border: 1px solid rgba(140,190,235,125); border-radius: 8px; }
            #quadrantOverlayTitle { color: #ffffff; font-size: 18px; font-weight: 700; }
            #quadrantOverlayHint { color: rgba(210,226,250,170); font-size: 11px; }
            #quadrantRed, #quadrantYellow, #quadrantGreen { border-radius: 7px; }
            #quadrantRed { background: rgba(164, 50, 67, 185); border: 1px solid rgba(255, 132, 142, 210); }
            #quadrantYellow { background: rgba(128, 98, 27, 185); border: 1px solid rgba(255, 214, 107, 210); }
            #quadrantGreen { background: rgba(31, 112, 75, 185); border: 1px solid rgba(105, 232, 162, 210); }
            #quadrantHeading { color: #ffffff; font-size: 15px; font-weight: 700; }
            #quadrantHint { color: rgba(255,255,255,205); font-size: 11px; }
            #rowButton, #dangerButton { background: transparent; border: 0; border-radius: 4px; padding: 4px 6px; color: rgba(255,255,255,150); font-size: 14px; }
            #rowButton:hover { background: rgba(255,255,255,35); color: #ffffff; }
            #dangerButton { color: rgba(255,140,140,185); }
            #dangerButton:hover { background: rgba(255,90,90,80); color: #ffffff; }
            #emptyLabel { color: rgba(255,255,255,135); padding: 30px; }
            #pageTitle { color: #ffffff; font-size: 36px; font-weight: 700; }
            #paperPageTitle { color: #ffffff; font-size: 25px; font-weight: 700; }
            #dateLabel { color: rgba(255,255,255,150); font-size: 13px; }
            #summaryLabel { color: rgba(255,255,255,170); font-size: 27px; font-weight: 300; }
            #overviewCard, #inspirationCard, #notesCard { background: rgba(15, 28, 54, 242); border: 1px solid rgba(140,190,235,85); border-radius: 7px; }
            #cardHeading { color: #ffffff; font-size: 13px; font-weight: 600; }
            #cardHint { color: rgba(180,210,245,155); font-size: 10px; }
            #metricValue { color: #ffffff; font-size: 18px; font-weight: 600; }
            #cardLink { background: transparent; border: 0; padding: 1px 0; color: #80d8ff; font-size: 11px; }
            #cardLink:hover { color: #ffffff; }
            #nodeText { color: rgba(255,255,255,195); font-size: 12px; }
            #nodeRed { color: #ff838b; font-size: 12px; font-weight: 600; }
            #nodeYellow { color: #ffd66b; font-size: 12px; font-weight: 600; }
            #nodeGreen { color: #69e8a2; font-size: 12px; font-weight: 600; }
            #inspirationText { color: rgba(255,255,255,210); font-size: 12px; }
            #inspirationBullet { color: #b9d9ff; font-size: 14px; }
            #miniAddButton, #inspirationRemove { background: transparent; border: 0; color: rgba(255,255,255,170); padding: 3px 6px; }
            #miniAddButton:hover, #inspirationRemove:hover { background: rgba(255,255,255,35); color: #ffffff; }
            #readingRow { background: rgba(24, 42, 75, 245); border: 1px solid rgba(140,190,235,75); border-radius: 6px; }
            #readingTitle { color: #ffffff; font-size: 14px; font-weight: 600; }
            #readingUnread, #readingDone { border-radius: 9px; padding: 4px 8px; font-size: 11px; }
            #readingUnread { background: rgba(255,214,107,45); color: #ffd66b; }
            #readingDone { background: rgba(105,232,162,45); color: #69e8a2; }
            #readingReason { color: rgba(255,255,255,165); font-size: 11px; }
            #sectionLabel, #formHint { color: rgba(255,255,255,140); font-size: 11px; }
            #sectionHeading, #editorHeading { color: #ffffff; font-weight: 600; }
            QAbstractScrollArea, QScrollArea, QAbstractScrollArea::viewport, #editorScroll, #paperContent, #notesContent, #recentContent, #frontierContent, #achievementContent { background: transparent; border: 0; }
            QScrollBar:vertical { background: transparent; width: 7px; margin: 2px; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,55); border-radius: 3px; min-height: 25px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal { background: transparent; height: 7px; margin: 2px; }
            QScrollBar::handle:horizontal { background: rgba(255,255,255,55); border-radius: 3px; min-width: 25px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            #paperCard { background: rgba(15, 28, 54, 242); border: 1px solid rgba(140,190,235,90); border-radius: 7px; }
            #journalEditor, #journalHistory { background: rgba(24, 42, 75, 245); border: 1px solid rgba(140,190,235,75); border-radius: 6px; }
            #journalLibraryRow { background: rgba(15, 28, 54, 242); border: 1px solid rgba(140,190,235,90); border-radius: 7px; }
            #journalUsage { color: #8fe2ff; font-size: 10px; }
            #cardTitle { color: #ffffff; font-size: 13px; font-weight: 700; }
            #cardMeta { color: #80d8ff; font-size: 11px; font-weight: 600; }
            #cardDetail { color: rgba(255,255,255,145); font-size: 10px; }
            #cardNotes { background: rgba(255,255,255,22); color: rgba(255,255,255,175); border-radius: 4px; padding: 6px; font-size: 11px; }
            #historyCount, #statusBadge { background: rgba(60,190,255,50); color: #8fe2ff; border-radius: 9px; padding: 4px 8px; font-size: 11px; }
            QSlider::groove:horizontal { height: 3px; background: rgba(255,255,255,70); border-radius: 2px; }
            QSlider::handle:horizontal { width: 10px; margin: -4px 0; border-radius: 5px; background: #ffffff; }
            QCheckBox::indicator { width: 17px; height: 17px; border: 2px solid rgba(255,255,255,210); border-radius: 9px; background: transparent; }
            QCheckBox::indicator:checked { border-color: #4ce58b; background: #4ce58b; }

            /* Unified compact research-workspace system */
            QWidget { color: #e8eef7; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 12px; }
            QPushButton { background: #172a43; color: #d8e5f5; border: 1px solid #314e70; border-radius: 7px; padding: 5px 8px; font-size: 11px; }
            QPushButton:hover { background: #213b5c; color: #ffffff; border-color: #4d759b; }
            QPushButton:disabled { background: #142238; color: #7690ad; border-color: #263d59; }
            #windowRoot { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #101a2b, stop:1 #0b1321); border: 1px solid #263a55; border-radius: 12px; }
            #contentStack { background: transparent; }
            #sidebar { background: #0a1220; border-right: 1px solid #233650; }
            #topbar { background: #0d1727; border-bottom: 1px solid #233650; }
            #brand { color: #f7fbff; font-size: 14px; font-weight: 700; letter-spacing: 1px; }
            #navButton { color: #8ca1bf; border-radius: 8px; padding: 6px 5px; font-size: 9px; letter-spacing: 1px; }
            #navButton:hover { background: #172842; color: #f5f9ff; }
            #navButton:checked { background: #183354; color: #8cd4ff; }
            #sidebarPinButton { background: #111e31; color: #a7b8cf; border: 1px solid #2c4767; border-radius: 6px; padding: 4px 2px; font-size: 10px; }
            #sidebarPinButton:hover { background: #1b3150; color: #ffffff; }
            #sidebarPinButton:checked { background: #2c2a1e; border-color: #665829; color: #f3d277; }
            #iconButton, #windowButton, #closeButton { color: #aab9cf; border-radius: 6px; padding: 3px 6px; font-size: 16px; }
            #iconButton:hover, #windowButton:hover { background: #1b2c44; color: #ffffff; }
            #closeButton:hover { background: #a84555; color: #ffffff; }
            #lockButton { color: #b6c5d8; border-radius: 6px; padding: 4px 7px; font-size: 10px; }
            #lockButton:hover { background: #1b2c44; color: #ffffff; }
            #lockButton:checked { background: #2b291d; color: #f6d56f; }
            QLineEdit, QDateEdit, QComboBox, QPlainTextEdit { background: #111f33; color: #eff5ff; border: 1px solid #2e4968; border-radius: 8px; padding: 6px 9px; selection-background-color: #2e78a7; }
            QLineEdit:focus, QDateEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border-color: #63c5f4; }
            QComboBox { padding-right: 29px; min-height: 17px; }
            QComboBox::drop-down { width: 24px; border: 0; border-left: 1px solid #2e4968; border-top-right-radius: 8px; border-bottom-right-radius: 8px; background: #172a43; }
            QComboBox::drop-down:hover { background: #203b5d; }
            QComboBox QAbstractItemView { background: #111f33; color: #f5f9ff; border: 1px solid #4a698d; outline: 0; selection-background-color: #2a628f; selection-color: #ffffff; padding: 3px; }
            QComboBox QAbstractItemView::item { min-height: 25px; padding: 5px 10px; color: #eff5ff; }
            QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected { background: #2a628f; color: #ffffff; }
            QMenu { background: #111f33; color: #eef5ff; border: 1px solid #3a5879; padding: 5px; }
            QMenu::item { padding: 7px 18px; border-radius: 5px; }
            QMenu::item:selected { background: #2a628f; color: #ffffff; }
            #primaryButton { background: #57d89a; color: #082016; border: 0; border-radius: 8px; padding: 7px 11px; font-weight: 700; }
            #primaryButton:hover { background: #78e8ad; }
            #primaryAction { background: #245c7c; color: #ddf4ff; border: 1px solid #3f86ad; border-radius: 7px; padding: 4px 8px; font-size: 10px; font-weight: 700; }
            #primaryAction:hover { background: #2e7197; color: #ffffff; }
            #inboxButton { background: #57d89a; color: #082016; border: 0; border-radius: 6px; padding: 0; font-size: 17px; font-weight: 700; }
            #inboxButton:hover { background: #78e8ad; }
            #subtleButton { background: #172a43; color: #d8e5f5; border: 1px solid #314e70; border-radius: 8px; padding: 6px 9px; }
            #subtleButton:hover { background: #213b5c; color: #ffffff; }
            #pageTitle { color: #f7faff; font-size: 26px; font-weight: 700; }
            #paperPageTitle { color: #f7faff; font-size: 22px; font-weight: 700; }
            #dateLabel { color: #91a7c4; font-size: 11px; }
            #summaryLabel { color: #dce7f7; font-size: 20px; font-weight: 400; }
            #overviewCard, #inspirationCard, #notesCard, #paperCard, #journalLibraryRow { background: #121f33; border: 1px solid #294461; border-radius: 11px; }
            #overviewCard:hover, #paperCard:hover, #journalLibraryRow:hover { border-color: #4a739b; }
            #cardHeading { color: #dbe8f7; font-size: 11px; font-weight: 700; }
            #cardHint, #sectionLabel, #formHint { color: #8ea4c0; font-size: 10px; }
            #metricValue { color: #f8fbff; font-size: 17px; font-weight: 600; }
            #cardLink { color: #7ed0fa; font-size: 10px; }
            #cardLink:hover { color: #c7edff; }
            #cardTitle { color: #f1f6ff; font-size: 13px; font-weight: 700; }
            #cardMeta { color: #91d2fb; font-size: 10px; font-weight: 600; }
            #cardDetail { color: #9fb3cd; font-size: 10px; }
            #cardNotes { background: #172a42; color: #c0cede; border-radius: 7px; padding: 5px 7px; font-size: 10px; }
            #journalHistory, #journalEditor, #readingRow, #noteItemRow { background: #162741; border: 1px solid #315070; border-radius: 9px; }
            #rejectionArchiveFrame { background: #231a28; border: 1px solid #684454; border-radius: 9px; }
            QScrollArea#rejectionArchivePanel, QScrollArea#rejectionArchivePanel::viewport, #rejectionArchiveContent { background: transparent; border: 0; }
            #rejectionArchiveRow { background: #302435; border: 1px solid #604657; border-radius: 9px; }
            #rejectionArchiveRow:hover { background: #382a3d; border-color: #966176; }
            #achievementRow { background: #162741; border: 1px solid #315070; border-radius: 9px; }
            #achievementRow:hover { border-color: #4b7199; }
            #archiveHeading { color: #f4dce2; font-size: 11px; font-weight: 700; }
            #archiveCount { background: #4e2d3a; color: #ffd0d8; border-radius: 8px; padding: 2px 6px; font-size: 10px; }
            #rejectionArchiveMarker { color: #ffa5b4; font-size: 15px; font-weight: 700; }
            #rejectionArchiveText { color: #fff1f4; font-size: 12px; font-weight: 700; }
            #achievementTitle { color: #eff5ff; font-size: 12px; font-weight: 700; }
            #rejectionArchiveMeta, #archiveHint { color: #d9bfc6; font-size: 10px; }
            #paperLifecycleHint, #achievementMeta { color: #9fb3cd; font-size: 10px; }
            #paperLifecycleHint { background: #163d2d; color: #9beabf; border-radius: 7px; padding: 5px 7px; }
            #futureDateIssueFrame { background: #3a2630; border: 1px solid #805161; border-radius: 8px; }
            #futureDateIssueLabel { color: #ffd4d9; font-size: 10px; }
            #futureDateRepairButton { background: #e4b35a; color: #1d1708; border: 0; border-radius: 5px; padding: 5px 8px; font-size: 10px; font-weight: 700; }
            #futureDateRepairButton:hover { background: #f6cd75; }
            #achievementType { background: #193552; color: #a9dfff; border-radius: 8px; padding: 3px 7px; font-size: 10px; }
            #noteItemRow:hover { border-color: #4b7199; }
            #noteItemTitle { color: #eff5ff; font-size: 12px; font-weight: 600; }
            #inspirationKind { background: #193552; color: #a9dfff; border-radius: 8px; padding: 3px 7px; font-size: 10px; }
            #journalUsage { color: #8dd4ff; font-size: 10px; }
            #journalGroupHeading { color: #9db8da; font-size: 10px; font-weight: 700; letter-spacing: 1px; padding: 6px 2px 1px; }
            #journalPublisher { color: #99c7ed; font-size: 10px; }
            #journalTags { color: #a9b9ce; font-size: 10px; }
            #journalJcr { background: #233c58; color: #a8dcff; border-radius: 6px; padding: 2px 4px; font-size: 9px; font-weight: 700; }
            #fileAttachmentRow { background: #162741; border: 1px solid #315070; border-radius: 7px; }
            #fileAttachmentIcon { color: #8bd3ff; font-size: 12px; }
            #fileAttachmentName { color: #e2edf9; font-size: 11px; }
            #fileAttachmentKind { color: #8fa7c2; font-size: 10px; }
            #fileOpenButton { background: #193450; color: #a9defb; border: 1px solid #396082; border-radius: 7px; padding: 5px 7px; font-size: 10px; }
            #fileOpenButton:hover { background: #255071; color: #ffffff; }
            #historyCount, #statusBadge { background: #173956; color: #a6ddff; border-radius: 8px; padding: 3px 7px; font-size: 10px; }
            #frontierCard { background: #12233a; border: 1px solid #315476; border-radius: 11px; }
            #frontierCard:hover { border-color: #5a8ab6; }
            #frontierTitle { color: #edf6ff; font-size: 13px; font-weight: 700; }
            #frontierSummary { color: #c8dbeb; font-size: 11px; }
            #frontierMatches { color: #9db6d1; font-size: 10px; }
            #frontierReason, #frontierBrief { color: #b9d8f0; font-size: 11px; }
            #frontierScore { background: #1b4c62; color: #b9f1ff; border-radius: 8px; padding: 3px 7px; font-size: 10px; font-weight: 700; }
            #frontierAi { background: #263f60; color: #b8d8ff; border-radius: 8px; padding: 3px 7px; font-size: 10px; font-weight: 700; }
            #frontierMust, #frontierWatch, #frontierExpand { border-radius: 8px; padding: 3px 7px; font-size: 10px; }
            #frontierMust { background: #3c3420; color: #f5d57e; }
            #frontierWatch { background: #173e5e; color: #9bdcff; }
            #frontierExpand { background: #27354a; color: #becce0; }
            #journalToolsMenu { background: #111f33; color: #e8f1ff; border: 1px solid #3b5d80; padding: 4px; }
            #journalToolsMenu::item { padding: 7px 18px; border-radius: 5px; }
            #journalToolsMenu::item:selected { background: #2a628f; color: #ffffff; }
            #rowButton, #dangerButton { border-radius: 6px; padding: 3px 6px; font-size: 11px; }
            #rowButton { color: #b3c4d9; }
            #rowButton:hover { background: #243e5d; color: #ffffff; }
            #dangerButton { color: #f0a2aa; }
            #dangerButton:hover { background: #65313c; color: #ffffff; }
            #dragHandle { background: transparent; color: #a2bad6; border: 0; border-radius: 6px; padding: 2px 3px; font-size: 16px; }
            #dragHandle:hover { background: #243e5d; color: #ffffff; }
            #dragHandle:pressed { background: #365a80; color: #ffffff; }
            #todoList::item { background: #13233a; border-bottom: 1px solid #263f5d; padding: 3px; }
            #todoText, #doneText { font-size: 14px; }
            #nodeText, #nodeRed, #nodeYellow, #nodeGreen { font-size: 11px; }
            QScrollBar:vertical { width: 6px; margin: 3px; }
            QScrollBar::handle:vertical { background: #476a8d; border-radius: 3px; min-height: 22px; }

            /* Focused workspace palette: one quiet surface scale, one accent. */
            #windowRoot { background: #0d1726; border: 1px solid #2a405c; border-radius: 10px; }
            #contentStack { background: #0d1726; }
            #sidebar { background: #0a1320; border-right: 1px solid #243a55; }
            #topbar { background: #0d1726; border-bottom: 1px solid #243a55; }
            #overviewCard, #inspirationCard, #notesCard, #paperCard, #journalLibraryRow,
            #frontierCard, #sourceCard, #journalPriorityCard, #achievementRow {
                background: #142137; border: 1px solid #2d4766; border-radius: 9px;
            }
            #overviewCard:hover, #paperCard:hover, #journalLibraryRow:hover,
            #frontierCard:hover, #achievementRow:hover { background: #172741; border-color: #49769e; }
            #journalHistory, #journalEditor, #readingRow, #noteItemRow, #fileAttachmentRow,
            #achievementPdfRow { background: #172842; border: 1px solid #345675; border-radius: 8px; }
            #rejectionArchiveFrame { background: #182338; border: 1px solid #5a4d61; border-radius: 9px; }
            #rejectionArchiveRow { background: #202b41; border: 1px solid #594b60; border-radius: 8px; }
            #rejectionArchiveRow:hover { background: #27344d; border-color: #8a6579; }
            #paperLifecycleHint { background: #14372b; color: #a7edc4; }
            #pageTitle { font-size: 24px; }
            #paperPageTitle { font-size: 21px; }
            #cardTitle { font-size: 12px; }
            #cardDetail, #cardHint, #sectionLabel, #formHint, #dateLabel { color: #9bb0c8; }
            #statusBadge, #historyCount { background: #193b58; color: #b5e2ff; border-radius: 7px; padding: 3px 6px; }
            #journalJcr { background: #1d405a; color: #c1e8ff; border-radius: 6px; padding: 2px 5px; }
            #frontierScore { background: #1b5364; color: #c4f8ff; }
            #frontierAi { background: #29405d; color: #c7dcff; }
            #frontierMust { background: #413720; color: #ffe094; }
            #frontierWatch { background: #1b425d; color: #b6e7ff; }
            #frontierExpand { background: #28384d; color: #cfdaea; }
            QToolTip { background: #111d2f; color: #eff6ff; border: 1px solid #486b90; padding: 5px 7px; border-radius: 5px; }
            """
        )
        # Retain the old selector map above only as a migration reference. Its
        # local stylesheet is deliberately cleared so the semantic app theme
        # controls every page, popup and standard widget from one place.
        self.setStyleSheet("")
        appearance = self.settings.get("appearance", {})
        appearance = appearance if isinstance(appearance, dict) else {}
        self._apply_theme(
            str(appearance.get("theme_id", "fog_teal")),
            str(appearance.get("density", "comfortable")),
        )

    def closeEvent(self, event) -> None:
        self.todo_page.save()
        self.paper_page.save()
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
