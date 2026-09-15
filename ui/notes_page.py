from __future__ import annotations

from datetime import date
from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs import confirm_delete, show_undo_toast
from ui.reorder import OrderDragHandle, ReorderableColumn
from utils.file_manager import load_inspirations, load_readings, save_inspirations, save_readings
from ui.workflow_dialogs import LinkInspirationDialog, ResearchInboxDialog


READING_STATUSES = ["未阅读", "已阅读"]


class InspirationDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, item: dict | None = None) -> None:
        super().__init__(parent)
        self._item = item or {}
        self.setWindowTitle("编辑灵感" if item else "添加灵感")
        parent_width = parent.width() if parent else 480
        self.setMinimumWidth(350)
        self.setMaximumWidth(max(350, min(520, parent_width - 24)))
        self.resize(max(350, min(460, parent_width - 24)), 260)
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QDialog QLabel { color: #f4f6ff; }
            QDialog QPlainTextEdit { background: #202d4d; color: #ffffff; border: 1px solid #53698f; border-radius: 6px; padding: 7px 9px; }
            QDialog QPlainTextEdit:focus { border-color: #70c9ff; }
            QDialog QDialogButtonBox QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 16px; }
            QDialog QDialogButtonBox QPushButton[text="保存"] { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(11)
        heading = QLabel("科研灵感")
        heading.setObjectName("editorHeading")
        root.addWidget(heading)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("写下一个科研想法……")
        self.text_edit.setPlainText(str(self._item.get("text", "")))
        self.text_edit.setFixedHeight(106)
        root.addWidget(self.text_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        if not self.text_edit.toPlainText().strip():
            QMessageBox.warning(self, "信息不完整", "请写下灵感内容。")
            return
        self.accept()

    def inspiration(self) -> dict:
        return {
            "id": str(self._item.get("id") or uuid4().hex),
            "text": self.text_edit.toPlainText().strip(),
            "created_at": str(self._item.get("created_at", "")) or date.today().isoformat(),
        }


class InspirationRow(QFrame):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    task_requested = Signal(str)
    paper_requested = Signal(str)

    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("noteItemRow")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 9)
        root.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(7)
        bullet = QLabel("○")
        bullet.setObjectName("inspirationBullet")
        bullet.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        top.addWidget(bullet, alignment=Qt.AlignmentFlag.AlignTop)
        text = QLabel(str(item.get("text", "")))
        text.setObjectName("noteItemTitle")
        text.setWordWrap(True)
        text.setMinimumWidth(0)
        text.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(text, 1)
        kind = QLabel("科研灵感")
        kind.setObjectName("inspirationKind")
        kind.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        top.addWidget(kind, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        actions.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        actions.addStretch()
        convert = QPushButton("转为")
        convert.setObjectName("rowButton")
        convert.setMinimumHeight(24)
        convert.setToolTip("转为今日任务或关联到论文")
        menu = QMenu(convert)
        task_action = menu.addAction("转为今日任务")
        task_action.triggered.connect(lambda: self.task_requested.emit(str(item.get("id", ""))))
        paper_action = menu.addAction("转为论文")
        paper_action.triggered.connect(lambda: self.paper_requested.emit(str(item.get("id", ""))))
        convert.setMenu(menu)
        actions.addWidget(convert)
        edit = QPushButton("编辑")
        edit.setObjectName("rowButton")
        edit.setMinimumHeight(24)
        edit.clicked.connect(lambda: self.edit_requested.emit(str(item.get("id", ""))))
        actions.addWidget(edit)
        delete = QPushButton("删除")
        delete.setObjectName("dangerButton")
        delete.setMinimumHeight(24)
        delete.clicked.connect(lambda: self.delete_requested.emit(str(item.get("id", ""))))
        actions.addWidget(delete)
        actions.addWidget(OrderDragHandle(str(item.get("id", "")), "inspirations"))
        root.addLayout(actions)


class InspirationPanel(QFrame):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.items = load_inspirations()
        self.setObjectName("notesCard")
        self._build_ui()
        self._render()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 13)
        root.setSpacing(7)
        header = QHBoxLayout()
        title = QLabel("灵感便签")
        title.setObjectName("cardHeading")
        header.addWidget(title)
        header.addStretch()
        hint = QLabel("科研想法")
        hint.setObjectName("cardHint")
        header.addWidget(hint)
        add = QPushButton("＋ 添加")
        add.setObjectName("subtleButton")
        add.clicked.connect(self._add)
        header.addWidget(add)
        root.addLayout(header)

        self.list_box = ReorderableColumn("inspirations")
        self.list_box.order_changed.connect(self._reorder)
        root.addWidget(self.list_box)

    def _add(self) -> None:
        dialog = InspirationDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.items.insert(0, dialog.inspiration())
        self._save()

    def _render(self) -> None:
        self.list_box.clear_rows()
        if not self.items:
            empty = QLabel("还没有灵感便签。把闪过的研究想法留在这里。")
            empty.setObjectName("cardHint")
            self.list_box.set_placeholder(empty)
            return
        for item in self.items:
            row = InspirationRow(item)
            row.edit_requested.connect(self._edit)
            row.delete_requested.connect(self._remove)
            row.task_requested.connect(self._convert_to_task)
            row.paper_requested.connect(self._convert_to_paper)
            self.list_box.add_row(row, str(item.get("id", "")))

    def reload(self) -> None:
        self.items = load_inspirations()
        self._render()

    def _remove(self, item_id: str | None) -> None:
        index = next((i for i, item in enumerate(self.items) if item.get("id") == item_id), -1)
        if index < 0:
            return
        if not confirm_delete(self, "删除灵感", "这条科研灵感将被永久移除。"):
            return
        removed = dict(self.items[index])
        self.items = [item for item in self.items if item.get("id") != item_id]
        self._save()
        show_undo_toast(
            self,
            "已删除灵感",
            lambda: self._restore_removed(index, removed),
        )

    def _restore_removed(self, index: int, item: dict) -> None:
        if any(str(entry.get("id", "")) == str(item.get("id", "")) for entry in self.items):
            return
        self.items.insert(min(index, len(self.items)), item)
        self._save()

    def _edit(self, item_id: str) -> None:
        index = next((i for i, item in enumerate(self.items) if item.get("id") == item_id), -1)
        if index < 0:
            return
        dialog = InspirationDialog(self, self.items[index])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.items[index] = dialog.inspiration()
        self._save()

    def _convert_to_task(self, item_id: str) -> None:
        item = next((entry for entry in self.items if str(entry.get("id", "")) == item_id), None)
        if item is None:
            return
        dialog = ResearchInboxDialog(self, preset="task", initial_text=str(item.get("text", "")))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.changed.emit()

    def _convert_to_paper(self, item_id: str) -> None:
        index = next((i for i, entry in enumerate(self.items) if str(entry.get("id", "")) == item_id), -1)
        if index < 0:
            return
        dialog = LinkInspirationDialog(self.items[index], self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.remove_source:
            del self.items[index]
            self._save()
        else:
            self.changed.emit()

    def _save(self) -> None:
        save_inspirations(self.items)
        self._render()
        self.changed.emit()

    def _reorder(self, ordered_ids: list[str]) -> None:
        by_id = {str(item.get("id", "")): item for item in self.items}
        self.items = [by_id[item_id] for item_id in ordered_ids if item_id in by_id]
        self._save()


class ReadingDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, item: dict | None = None) -> None:
        super().__init__(parent)
        self._item = item or {}
        self.setWindowTitle("编辑待读" if item else "添加待读")
        parent_width = parent.width() if parent else 480
        self.setMinimumWidth(350)
        self.setMaximumWidth(max(350, min(520, parent_width - 24)))
        self.resize(max(350, min(460, parent_width - 24)), 350)
        self._build_ui()
        if item:
            self._fill(item)

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QDialog QLabel { color: #f4f6ff; }
            QDialog QLineEdit, QDialog QComboBox, QDialog QPlainTextEdit { background: #202d4d; color: #ffffff; border: 1px solid #53698f; border-radius: 6px; padding: 7px 9px; }
            QDialog QLineEdit:focus, QDialog QComboBox:focus, QDialog QPlainTextEdit:focus { border-color: #70c9ff; }
            QDialog QComboBox QAbstractItemView { background: #202d4d; color: #ffffff; selection-background-color: #2e78b7; selection-color: #ffffff; border: 1px solid #70c9ff; outline: 0; }
            QDialog QComboBox QAbstractItemView::item { min-height: 24px; padding: 5px 10px; color: #ffffff; }
            QDialog QComboBox QAbstractItemView::item:selected { background: #2e78b7; color: #ffffff; }
            QDialog QDialogButtonBox QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 16px; }
            QDialog QDialogButtonBox QPushButton[text="保存"] { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(11)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(11)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：Machine learning for SOC mapping")
        form.addRow("标题 *", self.title_edit)
        self.status_combo = QComboBox()
        self.status_combo.addItems(READING_STATUSES)
        form.addRow("状态", self.status_combo)
        self.reason_edit = QPlainTextEdit()
        self.reason_edit.setPlaceholderText("例如：方法可借鉴、与当前研究方向高度相关……")
        self.reason_edit.setFixedHeight(74)
        form.addRow("原因（非必填）", self.reason_edit)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("DOI 或论文链接（可选）")
        form.addRow("来源", self.url_edit)
        root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _fill(self, item: dict) -> None:
        self.title_edit.setText(str(item.get("title", "")))
        self.status_combo.setCurrentText(str(item.get("status", "未阅读")))
        self.reason_edit.setPlainText(str(item.get("reason", "")))
        self.url_edit.setText(str(item.get("url", "")) or str(item.get("doi", "")))

    def _validate_and_accept(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "信息不完整", "请填写标题。")
            return
        self.accept()

    def reading(self) -> dict:
        return {
            "id": str(self._item.get("id") or uuid4().hex),
            "title": self.title_edit.text().strip(),
            "status": self.status_combo.currentText(),
            "reason": self.reason_edit.toPlainText().strip(),
            "url": self.url_edit.text().strip(),
            "doi": str(self._item.get("doi", "")),
            "created_at": str(self._item.get("created_at", "")) or date.today().isoformat(),
            "updated_at": date.today().isoformat(),
        }


class ReadingRow(QFrame):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    status_requested = Signal(str)

    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.setObjectName("noteItemRow")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 9)
        root.setSpacing(4)
        top = QHBoxLayout()
        title = QLabel(str(item.get("title", "")))
        title.setObjectName("noteItemTitle")
        title.setWordWrap(True)
        title.setMinimumWidth(0)
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        top.addWidget(title, 1)
        status = QLabel(str(item.get("status", "未阅读")))
        status.setObjectName("readingDone" if item.get("status") == "已阅读" else "readingUnread")
        status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        top.addWidget(status, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)
        reason = str(item.get("reason", "")).strip()
        if reason:
            reason_label = QLabel(f"原因：{reason}")
            reason_label.setObjectName("readingReason")
            reason_label.setWordWrap(True)
            reason_label.setMinimumWidth(0)
            reason_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            reason_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            root.addWidget(reason_label)
        if item.get("url") or item.get("doi"):
            source = QPushButton("打开来源")
            source.setObjectName("rowButton")
            source.setMinimumHeight(24)
            source.setToolTip(str(item.get("url") or item.get("doi")))
            # The small source button deliberately keeps the reading list a
            # list, rather than turning every paper into a dense citation card.
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl

            source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromUserInput(str(item.get("url") or item.get("doi")))))
            root.addWidget(source, alignment=Qt.AlignmentFlag.AlignLeft)
        actions = QHBoxLayout()
        actions.setSpacing(4)
        actions.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        actions.addStretch()
        toggle = QPushButton("标为未读" if item.get("status") == "已阅读" else "标为已读")
        toggle.setObjectName("rowButton")
        toggle.setMinimumHeight(24)
        toggle.clicked.connect(lambda: self.status_requested.emit(str(item.get("id", ""))))
        actions.addWidget(toggle)
        edit = QPushButton("编辑")
        edit.setObjectName("rowButton")
        edit.setMinimumHeight(24)
        edit.clicked.connect(lambda: self.edit_requested.emit(str(item.get("id", ""))))
        actions.addWidget(edit)
        delete = QPushButton("删除")
        delete.setObjectName("dangerButton")
        delete.setMinimumHeight(24)
        delete.clicked.connect(lambda: self.delete_requested.emit(str(item.get("id", ""))))
        actions.addWidget(delete)
        actions.addWidget(OrderDragHandle(str(item.get("id", "")), "readings"))
        root.addLayout(actions)


class NotesPage(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.readings = load_readings()
        self._build_ui()
        self._render_readings()

    def reload(self) -> None:
        self.inspiration.reload()
        self.readings = load_readings()
        self._render_readings()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("notesContent")
        root = QVBoxLayout(content)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(11)
        title = QLabel("灵感与待读")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        subtitle = QLabel("保留研究灵感，也记录值得回看的论文")
        subtitle.setObjectName("dateLabel")
        root.addWidget(subtitle)

        self.inspiration = InspirationPanel()
        self.inspiration.changed.connect(self.changed)
        root.addWidget(self.inspiration)

        reading_card = QFrame()
        reading_card.setObjectName("notesCard")
        reading_root = QVBoxLayout(reading_card)
        reading_root.setContentsMargins(14, 12, 14, 13)
        reading_root.setSpacing(8)
        header = QHBoxLayout()
        heading = QLabel("值得阅读")
        heading.setObjectName("cardHeading")
        header.addWidget(heading)
        hint = QLabel("论文清单")
        hint.setObjectName("cardHint")
        header.addWidget(hint)
        header.addStretch()
        add = QPushButton("＋ 添加")
        add.setObjectName("subtleButton")
        add.clicked.connect(self._add_reading)
        header.addWidget(add)
        reading_root.addLayout(header)
        self.reading_box = ReorderableColumn("readings")
        self.reading_box.order_changed.connect(self._reorder_readings)
        reading_root.addWidget(self.reading_box)
        root.addWidget(reading_card)
        root.addStretch()

        self.scroll.setWidget(content)
        outer.addWidget(self.scroll)

    def _render_readings(self) -> None:
        self.reading_box.clear_rows()
        if not self.readings:
            empty = QLabel("还没有待读记录。把以后想读的论文放在这里。")
            empty.setObjectName("cardHint")
            self.reading_box.set_placeholder(empty)
            return
        for item in self.readings:
            row = ReadingRow(item)
            row.status_requested.connect(self._toggle_status)
            row.edit_requested.connect(self._edit_reading)
            row.delete_requested.connect(self._delete_reading)
            self.reading_box.add_row(row, str(item.get("id", "")))

    def _find_index(self, item_id: str) -> int:
        return next((index for index, item in enumerate(self.readings) if item.get("id") == item_id), -1)

    def _add_reading(self) -> None:
        dialog = ReadingDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.readings.insert(0, dialog.reading())
            self._save()

    def _edit_reading(self, item_id: str) -> None:
        index = self._find_index(item_id)
        if index < 0:
            return
        dialog = ReadingDialog(self, self.readings[index])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.readings[index] = dialog.reading()
            self._save()

    def _toggle_status(self, item_id: str) -> None:
        index = self._find_index(item_id)
        if index < 0:
            return
        self.readings[index]["status"] = "未阅读" if self.readings[index].get("status") == "已阅读" else "已阅读"
        self._save()

    def _delete_reading(self, item_id: str) -> None:
        index = self._find_index(item_id)
        if index < 0:
            return
        title = self.readings[index].get("title", "这篇论文")
        if not confirm_delete(self, "删除待读记录", f"“{title}”将从待读清单中移除。"):
            return
        removed = dict(self.readings[index])
        del self.readings[index]
        self._save()
        show_undo_toast(
            self,
            "已删除待读",
            lambda: self._restore_reading(index, removed),
        )

    def _restore_reading(self, index: int, item: dict) -> None:
        if any(str(entry.get("id", "")) == str(item.get("id", "")) for entry in self.readings):
            return
        self.readings.insert(min(index, len(self.readings)), item)
        self._save()

    def _save(self) -> None:
        save_readings(self.readings)
        self._render_readings()
        self.changed.emit()

    def _reorder_readings(self, ordered_ids: list[str]) -> None:
        by_id = {str(item.get("id", "")): item for item in self.readings}
        self.readings = [by_id[item_id] for item_id in ordered_ids if item_id in by_id]
        self._save()
