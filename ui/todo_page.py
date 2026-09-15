from __future__ import annotations

from datetime import date
from uuid import uuid4

from PySide6.QtCore import QDate, QMimeData, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs import confirm_delete, show_undo_toast
from ui.reorder import ORDER_MIME, OrderDragHandle, decode_order_payload
from utils.file_manager import (
    cleanup_old_completed_tasks,
    delete_todo,
    load_task_history,
    load_todos,
    mark_todo_migration_checked,
    migrate_todos_to_today,
    pending_todo_migrations,
    save_todos,
    todo_migration_checked,
)


WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

QUADRANTS = {
    "urgent_important": ("紧急且重要", "立即处理", "quadrantRed"),
    "important_not_urgent": ("重要不紧急", "安排计划", "quadrantYellow"),
    "urgent_not_important": ("紧急不重要", "尽快处理", "quadrantYellow"),
    "not_urgent_not_important": ("不紧急不重要", "暂缓处理", "quadrantGreen"),
}
QUADRANT_MIME = "application/x-research-assistant-todo"
TODO_PRIORITY_STYLE = {
    "urgent_important": ("todoRedDot", "priorityRedTag"),
    "important_not_urgent": ("todoYellowDot", "priorityYellowTag"),
    "urgent_not_important": ("todoYellowDot", "priorityYellowTag"),
    "not_urgent_not_important": ("todoGreenDot", "priorityGreenTag"),
}


class TodoDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, item: dict | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("todoDialog")
        self.setWindowTitle("编辑任务" if item else "添加任务")
        self.setMinimumWidth(470)
        self._item = item or {}
        self._build_ui()
        if item:
            self._fill(item)

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QDialog#todoDialog { background: #101a36; color: #f4f6ff; }
            QDialog#todoDialog QLabel { color: #f4f6ff; }
            QDialog#todoDialog #dialogHint { color: #aebbd8; }
            QDialog#todoDialog QLineEdit,
            QDialog#todoDialog QDateEdit {
                background: #202d4d;
                color: #ffffff;
                border: 1px solid #53698f;
                border-radius: 6px;
                padding: 7px 9px;
            }
            QDialog#todoDialog QLineEdit:focus,
            QDialog#todoDialog QDateEdit:focus { border-color: #70c9ff; }
            QDialog#todoDialog QCheckBox { color: #ffffff; spacing: 8px; }
            QDialog#todoDialog QDialogButtonBox QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 16px; }
            QDialog#todoDialog QDialogButtonBox QPushButton:hover { background: #3a527d; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(12)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：修改论文摘要")
        form.addRow("任务名称 *", self.title_edit)

        self.no_schedule_check = QCheckBox("不设时间，持续显示到完成")
        self.no_schedule_check.setToolTip("适合暂时不想安排日期的科研任务；完成前会一直显示在今日待办。")
        self.no_schedule_check.toggled.connect(self._update_schedule_state)
        form.addRow("时间安排", self.no_schedule_check)

        self.start_edit = QDateEdit()
        self.start_edit.setCalendarPopup(True)
        self.start_edit.setDisplayFormat("yyyy-MM-dd")
        self.start_edit.setDate(QDate.currentDate())
        form.addRow("开始日期", self.start_edit)

        self.end_edit = QDateEdit()
        self.end_edit.setCalendarPopup(True)
        self.end_edit.setDisplayFormat("yyyy-MM-dd")
        self.end_edit.setDate(QDate.currentDate())
        form.addRow("结束日期", self.end_edit)

        self.repeat_check = QCheckBox("每天循环执行")
        self.repeat_check.setObjectName("repeatCheck")
        form.addRow("循环设置", self.repeat_check)
        root.addLayout(form)

        self.hint = QLabel()
        self.hint.setObjectName("dialogHint")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)
        self._update_schedule_state()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _fill(self, item: dict) -> None:
        self.title_edit.setText(str(item.get("title", "")))
        self.no_schedule_check.setChecked(str(item.get("schedule_mode", "range")) == "none")
        start = QDate.fromString(str(item.get("start_date", "")), "yyyy-MM-dd")
        end = QDate.fromString(str(item.get("end_date", "")), "yyyy-MM-dd")
        self.start_edit.setDate(start if start.isValid() else QDate.currentDate())
        self.end_edit.setDate(end if end.isValid() else self.start_edit.date())
        self.repeat_check.setChecked(bool(item.get("repeat_daily", False)))
        self._update_schedule_state()

    def _update_schedule_state(self, no_schedule: bool | None = None) -> None:
        """Keep the simple no-date option mutually exclusive with date rules."""
        no_schedule = self.no_schedule_check.isChecked() if no_schedule is None else bool(no_schedule)
        self.start_edit.setEnabled(not no_schedule)
        self.end_edit.setEnabled(not no_schedule)
        self.repeat_check.setEnabled(not no_schedule)
        if no_schedule:
            self.repeat_check.setChecked(False)
            self.hint.setText("不设时间的任务会持续出现在“今日待办”，直到你勾选完成；不会触发次日迁移。")
        else:
            self.hint.setText("任务会在开始日期至结束日期之间显示；勾选“每天循环”后，每天的完成状态分别保存。")

    def _validate_and_accept(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "信息不完整", "请填写任务名称。")
            return
        if not self.no_schedule_check.isChecked() and self.end_edit.date() < self.start_edit.date():
            QMessageBox.warning(self, "日期有误", "结束日期不能早于开始日期。")
            return
        self.accept()

    def todo(self) -> dict:
        no_schedule = self.no_schedule_check.isChecked()
        return {
            "id": self._item.get("id", uuid4().hex),
            "title": self.title_edit.text().strip(),
            "schedule_mode": "none" if no_schedule else "range",
            "created_for": str(self._item.get("created_for", "")).strip() or QDate.currentDate().toString("yyyy-MM-dd"),
            "start_date": "" if no_schedule else self.start_edit.date().toString("yyyy-MM-dd"),
            "end_date": "" if no_schedule else self.end_edit.date().toString("yyyy-MM-dd"),
            "repeat_daily": self.repeat_check.isChecked() and not no_schedule,
            "done_dates": list(self._item.get("done_dates", [])),
            "done": bool(self._item.get("done", False)),
            "quadrant": str(self._item.get("quadrant", "")),
        }


class TodoMigrationDialog(QDialog):
    def __init__(self, items: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.should_migrate = False
        self.setWindowTitle("未完成任务迁移")
        parent_width = parent.width() if parent else 480
        self.setMinimumWidth(350)
        self.setMaximumWidth(max(350, min(500, parent_width - 24)))
        self.resize(max(350, min(440, parent_width - 24)), 260)
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QLabel { color: #f4f6ff; }
            #migrationTitle { color: #ffffff; font-size: 19px; font-weight: 700; }
            #migrationHint { color: #aebbd8; font-size: 12px; }
            #migrationTask { background: #172340; color: #dce8ff; border-radius: 5px; padding: 6px 8px; }
            QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 13px; }
            QPushButton:hover { background: #3a527d; }
            #migrateButton { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 19, 22, 18)
        root.setSpacing(9)
        title = QLabel(f"发现 {len(items)} 个未完成任务")
        title.setObjectName("migrationTitle")
        root.addWidget(title)
        hint = QLabel("这些任务昨天结束但尚未完成。是否转移到今天？")
        hint.setObjectName("migrationHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        for item in items[:4]:
            task = QLabel(f"•  {item.get('title', '')}")
            task.setObjectName("migrationTask")
            task.setWordWrap(True)
            root.addWidget(task)
        if len(items) > 4:
            more = QLabel(f"另有 {len(items) - 4} 个任务")
            more.setObjectName("migrationHint")
            root.addWidget(more)
        actions = QHBoxLayout()
        actions.addStretch()
        skip = QPushButton("暂不迁移")
        skip.clicked.connect(self.reject)
        actions.addWidget(skip)
        migrate = QPushButton("转移到今天")
        migrate.setObjectName("migrateButton")
        migrate.clicked.connect(self._accept_migration)
        actions.addWidget(migrate)
        root.addLayout(actions)

    def _accept_migration(self) -> None:
        self.should_migrate = True
        self.accept()


class QuadrantDropZone(QFrame):
    dropped = Signal(str)
    chosen = Signal(str)

    def __init__(self, key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        title, hint, style_name = QUADRANTS[key]
        self.setObjectName(style_name)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(68)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 11, 13, 10)
        layout.setSpacing(2)
        heading = QLabel(title)
        heading.setObjectName("quadrantHeading")
        layout.addWidget(heading)
        description = QLabel(hint)
        description.setObjectName("quadrantHint")
        layout.addWidget(description)
        layout.addStretch()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(QUADRANT_MIME):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(QUADRANT_MIME):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasFormat(QUADRANT_MIME):
            event.ignore()
            return
        self.dropped.emit(self.key)
        event.acceptProposedAction()

    def choose(self) -> None:
        """Commit a quadrant from the visible surface without requiring a drag."""
        self.chosen.emit(self.key)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.choose()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class QuadrantOverlay(QWidget):
    quadrant_chosen = Signal(str)
    quadrant_selected = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("quadrantOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(5)
        title = QLabel("任务优先级")
        title.setObjectName("quadrantOverlayTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = QLabel("四象限")
        hint.setObjectName("quadrantOverlayHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        grid = QGridLayout()
        grid.setSpacing(7)
        for index, key in enumerate(QUADRANTS):
            zone = QuadrantDropZone(key)
            zone.setMinimumHeight(68)
            zone.setMaximumHeight(82)
            zone.dropped.connect(self.quadrant_chosen)
            zone.chosen.connect(self.quadrant_selected)
            grid.addWidget(zone, index // 2, index % 2)
        layout.addLayout(grid, 1)


class PrioritySelectorButton(QPushButton):
    """Direct priority picker with an optional deliberate drag gesture."""

    long_press_requested = Signal(object)
    drag_requested = Signal(object)
    drag_finished = Signal()

    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self._press_pos: QPoint | None = None
        self._long_press_active = False
        self._drag_started = False
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(550)
        self._hold_timer.timeout.connect(self._begin_long_press)
        self.setMouseTracking(True)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._long_press_active = False
            self._drag_started = False
            self._hold_timer.start()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_pos:
            distance = (event.position().toPoint() - self._press_pos).manhattanLength()
            if self._long_press_active and not self._drag_started and distance > 8:
                self._drag_started = True
                self._hold_timer.stop()
                self.drag_requested.emit(self)
                return
            if not self._long_press_active and distance > 12:
                self._hold_timer.stop()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._hold_timer.stop()
        if self._long_press_active:
            self._press_pos = None
            self._long_press_active = False
            self.unsetCursor()
            event.accept()
            return
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _begin_long_press(self) -> None:
        if self._press_pos is None:
            return
        self._long_press_active = True
        self._drag_started = False
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.long_press_requested.emit(self)

    def begin_quadrant_drag(self) -> None:
        if not self._long_press_active:
            return
        mime_data = QMimeData()
        mime_data.setData(QUADRANT_MIME, str(self.item.get("id", "")).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.setHotSpot(QPoint(22, 18))
        drag.exec(Qt.DropAction.MoveAction)
        self._long_press_active = False
        self._drag_started = False
        self._press_pos = None
        self.unsetCursor()
        self.drag_finished.emit()


class TodoRow(QWidget):
    """Task content row; priority gestures live on the dedicated control only."""

    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item


class ReorderableTodoList(QListWidget):
    order_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event) -> None:
        scope, _item_id = decode_order_payload(event.mimeData())
        if scope == "todos":
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        scope, _item_id = decode_order_payload(event.mimeData())
        if scope == "todos":
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        scope, todo_id = decode_order_payload(event.mimeData())
        if scope != "todos":
            super().dropEvent(event)
            return
        old_index = next(
            (index for index in range(self.count()) if str(self.item(index).data(Qt.ItemDataRole.UserRole)) == todo_id),
            -1,
        )
        if old_index < 0:
            event.ignore()
            return
        target_index = self.count()
        target_item = self.itemAt(event.position().toPoint())
        if target_item is not None:
            target_index = self.row(target_item)
            if event.position().y() > self.visualItemRect(target_item).center().y():
                target_index += 1
        if target_index > old_index:
            target_index -= 1
        if target_index != old_index:
            moving_item = self.item(old_index)
            moving_widget = self.itemWidget(moving_item)
            self.removeItemWidget(moving_item)
            moving_item = self.takeItem(old_index)
            self.insertItem(target_index, moving_item)
            if moving_widget is not None:
                self.setItemWidget(moving_item, moving_widget)
            self.order_changed.emit(
                [str(self.item(index).data(Qt.ItemDataRole.UserRole)) for index in range(self.count())]
            )
        event.acceptProposedAction()


class TodoPage(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current_day = date.today()
        self.items: list[dict] = []
        self.history_expanded = False
        self._dragging_todo_id: str | None = None
        self._pending_quadrant: str | None = None
        self._build_ui()
        cleanup_old_completed_tasks(self.current_day)
        self._load_day(self.current_day)
        self._day_timer = QTimer(self)
        self._day_timer.setInterval(60 * 1000)
        self._day_timer.timeout.connect(self._rollover_if_needed)
        self._day_timer.start()
        QTimer.singleShot(800, self._check_pending_migrations)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("今日待办")
        title.setObjectName("pageTitle")
        title_box.addWidget(title)
        heading.addLayout(title_box)
        heading.addStretch()
        self.summary_label = QLabel()
        self.summary_label.setObjectName("summaryLabel")
        heading.addWidget(self.summary_label, alignment=Qt.AlignmentFlag.AlignBottom)
        root.addLayout(heading)

        add_bar = QHBoxLayout()
        add_bar.setSpacing(6)
        self.input = QLineEdit()
        self.input.setPlaceholderText("添加任务……（回车快速添加，＋打开详细设置）")
        self.input.returnPressed.connect(self._add_todo)
        add_bar.addWidget(self.input)
        add_button = QPushButton("＋")
        add_button.setObjectName("primaryButton")
        add_button.setToolTip("添加任务并设置持续时间 / 循环")
        add_button.clicked.connect(self._add_detailed_todo)
        add_bar.addWidget(add_button)
        root.addLayout(add_bar)

        self.list_widget = ReorderableTodoList()
        self.list_widget.setObjectName("todoList")
        self.list_widget.setSpacing(7)
        self.list_widget.setToolTip("点击任务右下角的“优先级”直接设置；按住该标签后可拖到四象限。")
        self.list_widget.order_changed.connect(self._reorder_active_todos)
        root.addWidget(self.list_widget, 1)

        self.empty_label = QLabel("今天还没有任务，先写下最重要的一件事吧。")
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty_label)

        self.history_toggle = QPushButton()
        self.history_toggle.setObjectName("historyToggle")
        self.history_toggle.setCheckable(True)
        self.history_toggle.clicked.connect(self._toggle_history)
        root.addWidget(self.history_toggle)

        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")
        self.history_list.setSpacing(4)
        self.history_list.setMaximumHeight(175)
        root.addWidget(self.history_list)
        # Keep unused vertical space below the content.  A QVBoxLayout with no
        # explicit sink otherwise distributes it across the heading and empty
        # label, making an empty task day look visually broken.
        self._bottom_spacer_index = root.count()
        root.addStretch(0)

        self.quadrant_overlay = QuadrantOverlay(self)
        self.quadrant_overlay.quadrant_chosen.connect(self._choose_quadrant)
        self.quadrant_overlay.quadrant_selected.connect(self._choose_quadrant_direct)
        self.quadrant_overlay.hide()

    def _load_day(self, day: date) -> None:
        self.current_day = day
        self.items = load_todos(day)
        self._render()

    def reload(self) -> None:
        """Reload local task data after a backup restore."""
        cleanup_old_completed_tasks(date.today())
        self._load_day(date.today())

    def _rollover_if_needed(self) -> None:
        today = date.today()
        if today == self.current_day:
            return
        cleanup_old_completed_tasks(today)
        self._load_day(today)
        self.changed.emit()
        QTimer.singleShot(200, self._check_pending_migrations)

    def _save_current_day(self) -> None:
        save_todos(self.current_day, self.items)

    def _add_todo(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        day_key = self.current_day.isoformat()
        self.items.append(
            {
                "id": uuid4().hex,
                "title": text,
                "schedule_mode": "range",
                "created_for": day_key,
                "start_date": day_key,
                "end_date": day_key,
                "repeat_daily": False,
                "done_dates": [],
                "done": False,
            }
        )
        self.input.clear()
        self._save_current_day()
        self._render()
        self.changed.emit()

    def _add_detailed_todo(self) -> None:
        dialog = TodoDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.items.append(dialog.todo())
            self.input.clear()
            self._save_current_day()
            self._render()
            self.changed.emit()

    def _render(self) -> None:
        # QListWidget.clear() removes the items but can leave custom item
        # widgets parented to the viewport until a later deferred-delete
        # cycle.  Detach them first so repeated task edits do not retain
        # invisible controls (or stale geometry) in the active widget tree.
        for index in range(self.list_widget.count() - 1, -1, -1):
            row_item = self.list_widget.item(index)
            row = self.list_widget.itemWidget(row_item)
            if row is not None:
                self.list_widget.removeItemWidget(row_item)
                row.setParent(None)
                row.deleteLater()
        self.list_widget.clear()
        active_items = [item for item in self.items if not item.get("done")]
        for item in active_items:
            row_item = QListWidgetItem(self.list_widget)
            row_item.setData(Qt.ItemDataRole.UserRole, str(item.get("id", "")))
            # Keep task content and controls on separate lines.  A single
            # horizontal row lets the three tiny icon actions expand like
            # normal buttons and leaves the actual task title unreadable on a
            # narrow desktop widget.
            row_item.setSizeHint(QSize(0, 84))
            row = TodoRow(item)
            layout = QVBoxLayout(row)
            layout.setContentsMargins(10, 5, 8, 5)
            layout.setSpacing(2)

            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(7)
            checkbox = QCheckBox()
            checkbox.setFixedWidth(18)
            checkbox.setChecked(bool(item.get("done", False)))
            checkbox.toggled.connect(lambda checked, todo_id=item.get("id"): self._set_done(todo_id, checked))
            title_row.addWidget(checkbox, alignment=Qt.AlignmentFlag.AlignTop)
            dot = QLabel("●")
            priority_style = TODO_PRIORITY_STYLE.get(str(item.get("quadrant", "")))
            dot.setObjectName(priority_style[0] if priority_style else "todoYellowDot")
            dot.setFixedWidth(10)
            dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            title_row.addWidget(dot, alignment=Qt.AlignmentFlag.AlignTop)

            label = QLabel(str(item.get("title", "")))
            label.setWordWrap(True)
            label.setMaximumHeight(38)
            label.setMinimumWidth(0)
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setToolTip(str(item.get("title", "")))
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            label.setObjectName("todoText")
            title_row.addWidget(label, 1)
            quadrant = str(item.get("quadrant", ""))
            layout.addLayout(title_row)

            controls_row = QHBoxLayout()
            controls_row.setContentsMargins(35, 0, 0, 0)
            controls_row.setSpacing(5)
            if str(item.get("schedule_mode", "range")) == "none":
                meta = "无时间 · 持续显示到完成"
            else:
                start = str(item.get("start_date", ""))
                end = str(item.get("end_date", start))
                period = start if start == end else f"{start} → {end}"
                meta = f"每天 · {period}" if item.get("repeat_daily") else f"持续 · {period}"
            meta_label = QLabel(meta)
            meta_label.setObjectName("todoMeta")
            meta_label.setMinimumWidth(0)
            meta_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            meta_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            meta_label.setToolTip(meta)
            meta_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            controls_row.addWidget(meta_label, 1)

            priority_button = PrioritySelectorButton(item)
            priority_button.setObjectName("todoPriorityButton")
            quadrant_title = QUADRANTS[quadrant][0] if quadrant in QUADRANTS else "未设置"
            priority_button.setText(quadrant_title if quadrant in QUADRANTS else "优先级")
            priority_button.setProperty("priority", quadrant or "unset")
            priority_button.setMinimumWidth(86)
            priority_button.setMaximumWidth(104)
            priority_button.setFixedHeight(24)
            priority_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            priority_button.setToolTip(
                f"优先级：当前象限为“{quadrant_title}”。点击选择；长按此处后可拖到四象限。"
            )
            priority_button.clicked.connect(
                lambda _checked=False, todo_id=item.get("id"), anchor=priority_button: self._show_priority_menu(todo_id, anchor)
            )
            priority_button.long_press_requested.connect(self._show_quadrant_overlay)
            priority_button.drag_requested.connect(self._start_quadrant_drag)
            priority_button.drag_finished.connect(self._finish_quadrant_drag)
            controls_row.addWidget(priority_button)

            # Editing and deletion are secondary task actions.  Keeping both
            # as permanent full buttons competes with the priority control on
            # a sticky-note-sized workbench, so expose them from one compact
            # overflow menu instead.
            more_button = QPushButton("…")
            more_button.setObjectName("rowButton")
            more_button.setFixedSize(26, 24)
            more_button.setToolTip("更多操作：编辑任务、删除任务")
            more_button.clicked.connect(
                lambda _checked=False, todo_id=item.get("id"), anchor=more_button: self._show_todo_actions(todo_id, anchor)
            )
            controls_row.addWidget(more_button)
            order_handle = OrderDragHandle(str(item.get("id", "")), "todos")
            order_handle.setFixedHeight(24)
            controls_row.addWidget(order_handle)
            layout.addLayout(controls_row)
            self.list_widget.setItemWidget(row_item, row)
        has_active = bool(active_items)
        # A hidden widget retains its QBoxLayout stretch factor.  Without
        # resetting it, an empty day leaves a large, non-interactive gap
        # between the heading and the quick-add field.
        layout = self.layout()
        if layout is not None:
            layout.setStretch(layout.indexOf(self.list_widget), 1 if has_active else 0)
            layout.setStretch(self._bottom_spacer_index, 0 if has_active else 1)
        self.empty_label.setText("今天的任务已完成，已归入历史任务。" if self.items else "今天还没有任务，先写下最重要的一件事吧。")
        self.empty_label.setVisible(not has_active)
        self.list_widget.setVisible(has_active)
        done = sum(1 for item in self.items if item.get("done"))
        self.summary_label.setText(f"Done  {done} / {len(self.items)}")
        self._render_history()

    def _render_history(self) -> None:
        history = load_task_history(self.current_day)
        self.history_list.clear()
        for item in history:
            row_item = QListWidgetItem()
            row_item.setSizeHint(QSize(0, 44))
            row_item.setText(f"✓  {item.get('title', '')}\n完成：{item.get('completed_on', '')}")
            self.history_list.addItem(row_item)
        self.history_toggle.setText(
            f"{'收起' if self.history_expanded else '展开'}历史任务  {len(history)} 条"
        )
        self.history_toggle.setVisible(bool(history))
        self.history_toggle.blockSignals(True)
        self.history_toggle.setChecked(self.history_expanded)
        self.history_toggle.blockSignals(False)
        self.history_list.setVisible(self.history_expanded and bool(history))

    def _toggle_history(self, expanded: bool) -> None:
        self.history_expanded = expanded
        self._render_history()

    def _check_pending_migrations(self) -> None:
        if todo_migration_checked(self.current_day):
            return
        pending = pending_todo_migrations(self.current_day)
        if not pending:
            mark_todo_migration_checked(self.current_day)
            return
        dialog = TodoMigrationDialog(pending, self)
        dialog.exec()
        mark_todo_migration_checked(self.current_day)
        if not dialog.should_migrate:
            return
        migrate_todos_to_today([str(item.get("id", "")) for item in pending], self.current_day)
        self._load_day(self.current_day)
        self.changed.emit()

    def _find_index(self, todo_id: str | None) -> int:
        return next((i for i, item in enumerate(self.items) if item.get("id") == todo_id), -1)

    def _set_done(self, todo_id: str | None, checked: bool) -> None:
        index = self._find_index(todo_id)
        if index < 0:
            return
        self.items[index]["done"] = checked
        self._save_current_day()
        self._render()
        self.changed.emit()

    def _show_priority_menu(self, todo_id: str | None, anchor: QWidget) -> None:
        """Offer an obvious one-click alternative to drag-and-drop."""
        if self._find_index(todo_id) < 0:
            return
        menu = QMenu(anchor)
        for key, (title, hint, _style) in QUADRANTS.items():
            action = menu.addAction(f"{title} · {hint}")
            action.setData(key)
        chosen = menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))
        if chosen is None:
            return
        self._set_quadrant(todo_id, str(chosen.data() or ""))

    def _show_todo_actions(self, todo_id: str | None, anchor: QWidget) -> None:
        """Keep destructive task actions available without crowding the row."""
        if self._find_index(todo_id) < 0:
            return
        menu = QMenu(anchor)
        edit_action = menu.addAction("编辑任务")
        delete_action = menu.addAction("删除任务")
        chosen = menu.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))
        if chosen is edit_action:
            self._edit_todo(todo_id)
        elif chosen is delete_action:
            self._delete_todo(todo_id)

    def _show_quadrant_overlay(self, source: PrioritySelectorButton) -> None:
        if self._dragging_todo_id:
            return
        self._dragging_todo_id = str(source.item.get("id", ""))
        self._pending_quadrant = None
        self._position_quadrant_overlay()
        self.quadrant_overlay.show()
        self.quadrant_overlay.raise_()

    def _position_quadrant_overlay(self) -> None:
        popup_width = min(max(270, self.width() - 20), 480)
        popup_height = min(max(224, self.height() - 96), 278)
        x = max(8, (self.width() - popup_width) // 2)
        y = max(8, (self.height() - popup_height) // 2)
        self.quadrant_overlay.setGeometry(x, y, popup_width, popup_height)

    def _start_quadrant_drag(self, source: PrioritySelectorButton) -> None:
        if not source._long_press_active:
            self.quadrant_overlay.hide()
            self._dragging_todo_id = None
            return
        source.begin_quadrant_drag()

    def _choose_quadrant(self, quadrant: str) -> None:
        if quadrant in QUADRANTS:
            self._pending_quadrant = quadrant

    def _choose_quadrant_direct(self, quadrant: str) -> None:
        todo_id = self._dragging_todo_id
        if not todo_id or quadrant not in QUADRANTS:
            return
        self._dismiss_quadrant_overlay()
        self._set_quadrant(todo_id, quadrant)

    def _dismiss_quadrant_overlay(self) -> None:
        self.quadrant_overlay.hide()
        self._dragging_todo_id = None
        self._pending_quadrant = None

    def _finish_quadrant_drag(self) -> None:
        todo_id = self._dragging_todo_id
        quadrant = self._pending_quadrant
        self._dismiss_quadrant_overlay()
        if not todo_id or not quadrant:
            return
        self._set_quadrant(todo_id, quadrant)

    def _set_quadrant(self, todo_id: str | None, quadrant: str) -> None:
        if quadrant not in QUADRANTS:
            return
        index = self._find_index(todo_id)
        if index < 0 or self.items[index].get("quadrant") == quadrant:
            return
        self.items[index]["quadrant"] = quadrant
        self._save_current_day()
        self._render()
        self.changed.emit()

    def _edit_todo(self, todo_id: str | None) -> None:
        index = self._find_index(todo_id)
        if index < 0:
            return
        dialog = TodoDialog(self, self.items[index])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.items[index] = dialog.todo()
            self._save_current_day()
            self._render()
            self.changed.emit()

    def _delete_todo(self, todo_id: str | None) -> None:
        index = self._find_index(todo_id)
        if index < 0:
            return
        if not confirm_delete(self, "删除任务", f"“{self.items[index].get('title', '此任务')}”将被永久删除。"):
            return
        removed = dict(self.items[index])
        delete_todo(str(todo_id))
        del self.items[index]
        self._render()
        self.changed.emit()
        show_undo_toast(self, "任务已删除", lambda: self._restore_deleted_todo(removed, index))

    def _restore_deleted_todo(self, todo: dict, index: int) -> None:
        todo_id = str(todo.get("id", ""))
        if not todo_id or self._find_index(todo_id) >= 0:
            return
        self.items.insert(max(0, min(index, len(self.items))), todo)
        self._save_current_day()
        self._render()
        self.changed.emit()

    def _reorder_active_todos(self, ordered_ids: list[str]) -> None:
        active_by_id = {str(item.get("id", "")): item for item in self.items if not item.get("done")}
        active_items = [active_by_id[item_id] for item_id in ordered_ids if item_id in active_by_id]
        done_items = [item for item in self.items if item.get("done")]
        self.items = [*active_items, *done_items]
        self._save_current_day()
        self._render()
        self.changed.emit()

    def save(self) -> None:
        self._save_current_day()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "quadrant_overlay") and self.quadrant_overlay.isVisible():
            self._position_quadrant_overlay()
