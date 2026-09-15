from __future__ import annotations

from datetime import date
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QDate, QEvent, QTimer, QUrl, Qt, Signal, QThread
from PySide6.QtGui import QDesktopServices, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QDateEdit,
)

from ui.dialogs import confirm_delete, show_undo_toast
from ui.ai_progress import AiProgressPanel
from ui.journal_selection_dialog import JournalSelectionDialog
from ui.reorder import OrderDragHandle, ReorderableColumn
from ui.workflow_dialogs import PaperActionCenterDialog
from utils.ai_service import DeepSeekConfigurationError, fill_paper_record_with_ai, is_deepseek_ready
from utils.file_manager import (
    find_future_paper_date_issues,
    load_app_settings,
    load_frontier_data,
    load_journal_library,
    journal_usage_index,
    load_papers,
    load_rejection_archive,
    repair_future_paper_dates,
    save_journal_library,
    save_papers,
    set_journal_selection_feedback,
)
from utils.journal_selection_service import (
    append_journal_to_submission_path,
    canonical_journal_name,
    external_candidate_to_library_journal,
)


STATUSES = ["准备投稿", "投稿中", "外审中", "修改中", "已接收", "已发表", "拒稿"]
ACTIVE_STATUSES = {"准备投稿", "投稿中", "外审中", "修改中"}


def _future_journal_date_labels(journal: dict) -> list[str]:
    """Return only future submission/status/timeline dates in a journal draft."""
    labels: list[str] = []
    today_key = QDate.currentDate()
    for field, label in (("date", "投稿日期"), ("status_updated_at", "状态更新时间")):
        value = QDate.fromString(str(journal.get(field, "")), "yyyy-MM-dd")
        if value.isValid() and value > today_key:
            labels.append(label)
    timeline = journal.get("timeline", [])
    for event in timeline if isinstance(timeline, list) else []:
        value = QDate.fromString(str(event.get("date", "")), "yyyy-MM-dd") if isinstance(event, dict) else QDate()
        if value.isValid() and value > today_key:
            labels.append("时间线日期")
    return labels


class PaperRecordAiThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, paper: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._paper = dict(paper)

    def run(self) -> None:
        try:
            self.progress.emit("AI 正在读取论文题目、关键词和摘要…", 18)
            result = fill_paper_record_with_ai(self._paper)
            self.progress.emit("AI 正在整理可编辑的研究信息草稿…", 92)
            self.completed.emit(result)
        except Exception as error:  # network/configuration errors are rendered inside the compact dialog
            self.failed.emit(str(error))


class FileAttachmentRow(QFrame):
    remove_requested = Signal(str)

    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.setObjectName("fileAttachmentRow")
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 5, 8, 5)
        root.setSpacing(6)
        icon = QLabel("▣" if item.get("kind") == "folder" else "⌁")
        icon.setObjectName("fileAttachmentIcon")
        root.addWidget(icon)
        name = QLabel(str(item.get("name", "未命名文件")))
        name.setObjectName("fileAttachmentName")
        name.setToolTip(str(item.get("path", "")))
        root.addWidget(name, 1)
        kind = QLabel("文件夹" if item.get("kind") == "folder" else "文件")
        kind.setObjectName("fileAttachmentKind")
        root.addWidget(kind)
        remove = QPushButton("×")
        remove.setObjectName("dangerButton")
        remove.setToolTip("移除关联，不删除本地文件")
        remove.clicked.connect(lambda: self.remove_requested.emit(str(item.get("id", ""))))
        root.addWidget(remove)


class JournalLibraryImportDialog(QDialog):
    """Pick one library journal and prefill a new submission history entry."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._journals = sorted(
            load_journal_library(),
            key=lambda item: (str(item.get("name", "")).casefold(), str(item.get("publisher", "")).casefold()),
        )
        self.setWindowTitle("从期刊库导入")
        parent_width = parent.width() if parent else 500
        self.setMinimumWidth(340)
        self.setMaximumWidth(max(340, min(500, parent_width - 24)))
        self.resize(max(340, min(430, parent_width - 24)), 260)
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QLabel { color: #f4f6ff; }
            #importTitle { color: #ffffff; font-size: 18px; font-weight: 700; }
            #importHint, #importDetail { color: #aebbd8; font-size: 11px; }
            QLineEdit, QComboBox { background: #202d4d; color: #ffffff; border: 1px solid #53698f; border-radius: 6px; padding: 7px 9px; }
            QLineEdit:focus, QComboBox:focus { border-color: #70c9ff; }
            QComboBox QAbstractItemView { background: #202d4d; color: #ffffff; selection-background-color: #2e78b7; selection-color: #ffffff; border: 1px solid #70c9ff; outline: 0; }
            QComboBox QAbstractItemView::item { min-height: 24px; padding: 5px 10px; color: #ffffff; }
            QComboBox QAbstractItemView::item:selected { background: #2e78b7; color: #ffffff; }
            QDialogButtonBox QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 15px; }
            QDialogButtonBox QPushButton[text=\"导入\"] { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(9)
        title = QLabel("从期刊库导入")
        title.setObjectName("importTitle")
        root.addWidget(title)
        hint = QLabel("导入后会新增一条“准备投稿”的期刊经历，可继续编辑投稿日期、状态和备注。")
        hint.setObjectName("importHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索期刊名、出版社或方向标签")
        self.search_edit.textChanged.connect(self._refresh)
        root.addWidget(self.search_edit)
        self.journal_combo = QComboBox()
        self.journal_combo.currentIndexChanged.connect(self._refresh_detail)
        root.addWidget(self.journal_combo)
        self.detail = QLabel()
        self.detail.setObjectName("importDetail")
        self.detail.setWordWrap(True)
        root.addWidget(self.detail)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导入")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_if_selected)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._import_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._refresh()

    def _filtered_journals(self) -> list[dict]:
        keyword = self.search_edit.text().strip().casefold()
        if not keyword:
            return self._journals
        return [
            journal
            for journal in self._journals
            if keyword
            in " ".join(
                [
                    str(journal.get("name", "")),
                    str(journal.get("publisher", "")),
                    " ".join(str(item) for item in journal.get("fields", [])),
                ]
            ).casefold()
        ]

    def _refresh(self) -> None:
        current_id = str(self.journal_combo.currentData())
        self.journal_combo.blockSignals(True)
        self.journal_combo.clear()
        for journal in self._filtered_journals():
            name = str(journal.get("name", "未命名期刊"))
            publisher = str(journal.get("publisher", "未填写出版社"))
            self.journal_combo.addItem(f"{name}  ·  {publisher}", str(journal.get("id", "")))
        restored = self.journal_combo.findData(current_id)
        if restored >= 0:
            self.journal_combo.setCurrentIndex(restored)
        self.journal_combo.blockSignals(False)
        self._import_button.setEnabled(self.journal_combo.count() > 0)
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        journal = self.selected_journal()
        if journal is None:
            self.detail.setText("没有匹配的期刊。可先在期刊库中新增期刊。")
            return
        fields = "、".join(str(item) for item in journal.get("fields", []) if str(item).strip())
        notes = str(journal.get("notes", "")).strip()
        detail = f"方向：{fields or '未分类'}"
        if notes:
            detail += f"\n备注：{notes}"
        self.detail.setText(detail)

    def _accept_if_selected(self) -> None:
        if self.selected_journal() is None:
            QMessageBox.information(self, "没有可导入的期刊", "请先在期刊库中添加期刊，或调整搜索内容。")
            return
        self.accept()

    def selected_journal(self) -> dict | None:
        journal_id = str(self.journal_combo.currentData())
        return next((item for item in self._journals if str(item.get("id", "")) == journal_id), None)


class JournalEditor(QFrame):
    remove_requested = Signal(object)

    def __init__(
        self,
        index: int,
        journal: dict | None = None,
        parent: QWidget | None = None,
        default_status_update_today: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("journalEditor")
        self._journal = journal or {}
        self._loading = True
        self._initial_status = ""
        self._default_status_update_today = default_status_update_today
        self._build_ui(index)
        self._fill(self._journal)
        self._initial_status = self.status_combo.currentText()
        self._loading = False

    def _build_ui(self, index: int) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        self.heading = QLabel()
        self.heading.setObjectName("editorHeading")
        header.addWidget(self.heading)
        header.addStretch()
        self.remove_button = QPushButton("移除这条经历")
        self.remove_button.setObjectName("dangerButton")
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))
        header.addWidget(self.remove_button)
        root.addLayout(header)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(9)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：CATENA")
        form.addRow("期刊名称 *", self.name_edit)
        self.publisher_edit = QLineEdit()
        self.publisher_edit.setPlaceholderText("例如：Elsevier")
        form.addRow("出版社 *", self.publisher_edit)

        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(date.today())
        form.addRow("投稿日期", self.date_edit)
        self.status_combo = QComboBox()
        self.status_combo.addItems(STATUSES)
        form.addRow("当前状态", self.status_combo)
        self.status_updated_edit = QDateEdit()
        self.status_updated_edit.setCalendarPopup(True)
        self.status_updated_edit.setDisplayFormat("yyyy-MM-dd")
        self.status_updated_edit.setDate(date.today())
        form.addRow("状态更新时间", self.status_updated_edit)

        due_row = QHBoxLayout()
        self.revision_due_check = QCheckBox("设置回复截止日")
        self.revision_due_date = QDateEdit()
        self.revision_due_date.setCalendarPopup(True)
        self.revision_due_date.setDisplayFormat("yyyy-MM-dd")
        self.revision_due_date.setDate(QDate.currentDate())
        due_row.addWidget(self.revision_due_check)
        due_row.addWidget(self.revision_due_date, 1)
        form.addRow("回复截止日", due_row)
        self.status_combo.currentTextChanged.connect(self._on_status_changed)
        self.revision_due_check.toggled.connect(self._update_revision_due_state)

        self.result_edit = QLineEdit()
        self.result_edit.setPlaceholderText("例如：大修后接收、拒稿原因等")
        form.addRow("投稿结果", self.result_edit)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("记录审稿速度、选刊经验、修改要点……")
        self.notes_edit.setFixedHeight(62)
        form.addRow("备注", self.notes_edit)
        root.addLayout(form)
        self._set_heading(index)

    def _set_heading(self, index: int) -> None:
        self.heading.setText(f"期刊经历 {index}")

    def _fill(self, journal: dict) -> None:
        self.name_edit.setText(str(journal.get("name", journal.get("journal", ""))))
        self.publisher_edit.setText(str(journal.get("publisher", "")))
        parsed_date = QDate.fromString(str(journal.get("date", "")), "yyyy-MM-dd")
        self.date_edit.setDate(parsed_date if parsed_date.isValid() else QDate.currentDate())
        self.status_combo.setCurrentText(str(journal.get("status", STATUSES[0])))
        updated_date = QDate.fromString(str(journal.get("status_updated_at", "")), "yyyy-MM-dd")
        self.status_updated_edit.setDate(
            updated_date if updated_date.isValid() else (parsed_date if parsed_date.isValid() else QDate.currentDate())
        )
        if self._default_status_update_today:
            # Opening a single-journal update normally means the user is
            # reviewing it today; they may still explicitly backdate it.
            self.status_updated_edit.setDate(QDate.currentDate())
        due_date = QDate.fromString(str(journal.get("revision_due_date", "")), "yyyy-MM-dd")
        self.revision_due_check.setChecked(due_date.isValid() and self.status_combo.currentText() == "修改中")
        self.revision_due_date.setDate(due_date if due_date.isValid() else QDate.currentDate())
        self._update_revision_due_state()
        self.result_edit.setText(str(journal.get("result", "")))
        self.notes_edit.setPlainText(str(journal.get("notes", "")))

    def clear(self) -> None:
        self._journal = {}
        self.name_edit.clear()
        self.publisher_edit.clear()
        self.date_edit.setDate(QDate.currentDate())
        self.status_combo.setCurrentIndex(0)
        self.status_updated_edit.setDate(QDate.currentDate())
        self.revision_due_check.setChecked(False)
        self.revision_due_date.setDate(QDate.currentDate())
        self.result_edit.clear()
        self.notes_edit.clear()
        self._initial_status = self.status_combo.currentText()

    def _on_status_changed(self, *_args) -> None:
        self._update_revision_due_state()
        if not self._loading and self.status_combo.currentText() != self._initial_status:
            # Submission date never moves.  A new status starts a fresh
            # reminder clock today, unless the user intentionally backdates it.
            self.status_updated_edit.setDate(QDate.currentDate())

    def _update_revision_due_state(self, *_args) -> None:
        is_revision = self.status_combo.currentText() == "修改中"
        self.revision_due_check.setEnabled(is_revision)
        if not is_revision:
            self.revision_due_check.setChecked(False)
        self.revision_due_date.setEnabled(is_revision and self.revision_due_check.isChecked())

    def values(self, timeline_note: str = "", record_timeline_event: bool = False) -> dict:
        status = self.status_combo.currentText()
        previous_status = str(self._journal.get("status", ""))
        timeline = [dict(event) for event in self._journal.get("timeline", []) if isinstance(event, dict)]
        submitted_date = self.date_edit.date().toString("yyyy-MM-dd")
        status_updated_at = self.status_updated_edit.date().toString("yyyy-MM-dd")
        record_date = status_updated_at
        if not timeline:
            timeline.append(
                {
                    "id": uuid4().hex,
                    "date": str(self._journal.get("date", "")) or submitted_date or record_date,
                    "status": previous_status or status,
                    "note": "",
                }
            )
        status_changed = bool(previous_status) and status != previous_status
        should_record = record_timeline_event and (status_changed or bool(timeline_note))
        if should_record:
            if timeline and timeline[-1].get("date") == record_date and timeline[-1].get("status") == status:
                if timeline_note:
                    timeline[-1]["note"] = timeline_note
            else:
                timeline.append({"id": uuid4().hex, "date": record_date, "status": status, "note": timeline_note})
        elif status_changed:
            timeline.append({"id": uuid4().hex, "date": record_date, "status": status, "note": ""})
        return {
            "id": self._journal.get("id", uuid4().hex),
            "name": self.name_edit.text().strip(),
            "publisher": self.publisher_edit.text().strip(),
            "date": submitted_date,
            "status_updated_at": status_updated_at,
            "status": status,
            "result": self.result_edit.text().strip(),
            "notes": self.notes_edit.toPlainText().strip(),
            "revision_due_date": (
                self.revision_due_date.date().toString("yyyy-MM-dd")
                if status == "修改中" and self.revision_due_check.isChecked()
                else ""
            ),
            "timeline": timeline,
        }


class PaperDialog(QDialog):
    journal_import_requested = Signal()

    def __init__(self, parent: QWidget | None = None, paper: dict | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("paperDialog")
        self.setWindowTitle("编辑论文记录" if paper else "新增论文记录")
        parent_width = parent.width() if parent else 620
        parent_height = parent.height() if parent else 650
        dialog_width = max(350, min(620, parent_width - 24))
        dialog_height = max(420, min(560, parent_height - 24))
        self.setMinimumSize(350, 420)
        self.setMaximumSize(dialog_width, max(420, parent_height - 12))
        self.resize(dialog_width, dialog_height)
        self.setStyleSheet(
            """
            QDialog#paperDialog { background: #101a36; color: #f4f6ff; }
            QDialog#paperDialog QLabel { color: #f4f6ff; }
            QDialog#paperDialog #formHint { color: #aebbd8; }
            QDialog#paperDialog #sectionHeading, QDialog#paperDialog #editorHeading { color: #ffffff; }
            QDialog#paperDialog QLineEdit,
            QDialog#paperDialog QDateEdit,
            QDialog#paperDialog QComboBox,
            QDialog#paperDialog QPlainTextEdit {
                background: #202d4d;
                color: #ffffff;
                border: 1px solid #53698f;
                border-radius: 6px;
                padding: 7px 9px;
                selection-background-color: #2e78b7;
            }
            QDialog#paperDialog QLineEdit:focus,
            QDialog#paperDialog QDateEdit:focus,
            QDialog#paperDialog QComboBox:focus,
            QDialog#paperDialog QPlainTextEdit:focus { border-color: #70c9ff; }
            QDialog#paperDialog QComboBox QAbstractItemView { background: #202d4d; color: #ffffff; selection-background-color: #2e78b7; selection-color: #ffffff; border: 1px solid #70c9ff; outline: 0; }
            QDialog#paperDialog QComboBox QAbstractItemView::item { min-height: 24px; padding: 5px 10px; color: #ffffff; }
            QDialog#paperDialog QComboBox QAbstractItemView::item:selected { background: #2e78b7; color: #ffffff; }
            QDialog#paperDialog QScrollArea,
            QDialog#paperDialog QAbstractScrollArea::viewport,
            QDialog#paperDialog #journalContainer { background: transparent; border: 0; }
            QDialog#paperDialog #journalEditor { background: #172340; border: 1px solid #42577d; border-radius: 7px; }
            QDialog#paperDialog #fileAttachmentRow { background: #172340; border: 1px solid #42577d; border-radius: 6px; }
            QDialog#paperDialog #fileAttachmentIcon { color: #7dceff; }
            QDialog#paperDialog #fileAttachmentName { color: #e4eefb; font-size: 11px; }
            QDialog#paperDialog #fileAttachmentKind { color: #9aaac1; font-size: 10px; }
            QDialog#paperDialog #subtleButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 6px 9px; }
            QDialog#paperDialog #subtleButton:hover { background: #3a527d; }
            QDialog#paperDialog #dangerButton { background: transparent; color: #ff9e9e; border: 0; }
            QDialog#paperDialog #dangerButton:hover { background: #613c4d; }
            QDialog#paperDialog QDialogButtonBox QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 16px; }
            QDialog#paperDialog QDialogButtonBox QPushButton:hover { background: #3a527d; }
            QDialog#paperDialog QDialogButtonBox QPushButton[text="保存"] { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            """
        )
        self._paper = paper or {}
        self._journal_editors: list[JournalEditor] = []
        self._files: list[dict] = []
        self._ai_worker: PaperRecordAiThread | None = None
        shortcut = load_app_settings().get("journal_import_shortcut", {})
        self._journal_import_shortcut = dict(shortcut) if isinstance(shortcut, dict) else {}
        self._last_tab_time = 0.0
        self._event_filter_installed = False
        self._shortcut_opening = False
        self._build_ui()
        self.journal_import_requested.connect(self._import_from_library)
        self._configure_journal_import_shortcut()
        if paper:
            self._fill(paper)
        else:
            self._add_journal()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：天山山区SOC预测研究")
        form.addRow("论文名称 *", self.title_edit)
        self.keywords_edit = QLineEdit()
        self.keywords_edit.setPlaceholderText("例如：SOC、遥感、机器学习（用于期刊匹配）")
        form.addRow("论文关键词", self.keywords_edit)
        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setPlaceholderText("研究对象、问题、方法或目标（用于 AI 补全和为论文选刊，可选）")
        self.summary_edit.setFixedHeight(66)
        form.addRow("研究摘要", self.summary_edit)
        root.addLayout(form)

        ai_row = QHBoxLayout()
        self.ai_hint = QLabel("可生成可编辑的关键词和研究摘要草稿，不会补造期刊或投稿事实。")
        self.ai_hint.setObjectName("formHint")
        self.ai_hint.setWordWrap(True)
        ai_row.addWidget(self.ai_hint, 1)
        self.ai_fill_button = QPushButton("AI 补全")
        self.ai_fill_button.setObjectName("subtleButton")
        self.ai_fill_button.setToolTip("根据论文题目生成关键词和研究摘要草稿")
        self.ai_fill_button.clicked.connect(self._run_ai_fill)
        ai_row.addWidget(self.ai_fill_button)
        root.addLayout(ai_row)
        self.ai_progress = AiProgressPanel(object_name="paperRecordAiProgress")
        root.addWidget(self.ai_progress)

        files_section = QHBoxLayout()
        files_label = QLabel("关联文件")
        files_label.setObjectName("sectionHeading")
        files_section.addWidget(files_label)
        files_section.addStretch()
        add_file = QPushButton("+ 文件")
        add_file.setObjectName("subtleButton")
        add_file.clicked.connect(self._add_files)
        files_section.addWidget(add_file)
        add_folder = QPushButton("+ 文件夹")
        add_folder.setObjectName("subtleButton")
        add_folder.clicked.connect(self._add_folder)
        files_section.addWidget(add_folder)
        root.addLayout(files_section)
        self.file_box = QVBoxLayout()
        self.file_box.setSpacing(4)
        root.addLayout(self.file_box)

        section = QHBoxLayout()
        label = QLabel("期刊投稿经历")
        label.setObjectName("sectionHeading")
        section.addWidget(label)
        section.addStretch()
        add = QPushButton("＋ 添加期刊经历")
        add.setObjectName("subtleButton")
        add.clicked.connect(self._add_journal)
        section.addWidget(add)
        import_journal = QPushButton("⌁ 导入期刊库")
        import_journal.setObjectName("subtleButton")
        import_journal.setToolTip("从个人期刊库带入一条准备投稿经历")
        import_journal.clicked.connect(self._request_journal_import)
        section.addWidget(import_journal)
        root.addLayout(section)

        self.journal_scroll = QScrollArea()
        self.journal_scroll.setWidgetResizable(True)
        self.journal_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.journal_scroll.setObjectName("editorScroll")
        self.journal_container = QWidget()
        self.journal_container.setObjectName("journalContainer")
        self.journal_layout = QVBoxLayout(self.journal_container)
        self.journal_layout.setContentsMargins(2, 2, 8, 8)
        self.journal_layout.setSpacing(9)
        self.journal_layout.addStretch()
        self.journal_scroll.setWidget(self.journal_container)
        root.addWidget(self.journal_scroll, 1)

        hint = QLabel(f"同一篇论文可添加多条经历；{self._journal_import_shortcut_hint()}。")
        hint.setObjectName("formHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _add_journal(self, journal: dict | None = None) -> JournalEditor:
        editor = JournalEditor(len(self._journal_editors) + 1, journal)
        editor.remove_requested.connect(self._remove_journal)
        self._journal_editors.append(editor)
        self.journal_layout.insertWidget(self.journal_layout.count() - 1, editor)
        self._refresh_numbers()
        return editor

    def _import_from_library(self) -> None:
        dialog = JournalLibraryImportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        library_journal = dialog.selected_journal()
        if library_journal is None:
            return
        imported = {
            "name": str(library_journal.get("name", "")).strip(),
            "publisher": str(library_journal.get("publisher", "")).strip(),
            "date": date.today().isoformat(),
            "status_updated_at": date.today().isoformat(),
            "status": "准备投稿",
            "result": "",
            "notes": str(library_journal.get("notes", "")).strip(),
        }
        editor = self._add_journal(imported)
        QTimer.singleShot(0, lambda: self.journal_scroll.ensureWidgetVisible(editor))

    def _journal_import_shortcut_hint(self) -> str:
        mode = str(self._journal_import_shortcut.get("mode", "double_tab"))
        if mode == "double_tab":
            return "在任意软件中连按 Tab 两次可唤起“为论文选刊”"
        if mode == "sequence":
            sequence = str(self._journal_import_shortcut.get("sequence", "")).strip()
            return f"按 {sequence or '自定义组合键'} 可从任意软件唤起“为论文选刊”"
        return "期刊库导入快捷键已关闭，可点击右上角按钮"

    def _configure_journal_import_shortcut(self) -> None:
        mode = str(self._journal_import_shortcut.get("mode", "double_tab")).casefold()
        # Custom combinations are registered once by the main window through
        # Windows RegisterHotKey.  Keeping a second local QShortcut here would
        # open two pickers when the paper editor is already focused.
        if mode == "double_tab":
            application = QApplication.instance()
            if application is not None:
                application.installEventFilter(self)
                self._event_filter_installed = True

    def _request_journal_import(self) -> None:
        if self._shortcut_opening:
            return
        self._shortcut_opening = True

        def open_importer() -> None:
            self._shortcut_opening = False
            if self.isVisible():
                self.journal_import_requested.emit()

        QTimer.singleShot(0, open_importer)

    def eventFilter(self, watched, event) -> bool:
        if (
            self._event_filter_installed
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and self._event_belongs_to_dialog(watched)
        ):
            mode = str(self._journal_import_shortcut.get("mode", "double_tab")).casefold()
            if mode == "double_tab" and event.key() == Qt.Key.Key_Tab and event.modifiers() == Qt.KeyboardModifier.NoModifier:
                now = monotonic()
                if now - self._last_tab_time <= 0.55:
                    self._last_tab_time = 0.0
                    self._request_journal_import()
                else:
                    self._last_tab_time = now
                # Prevent Qt's focus traversal from consuming the two-key
                # sequence before the importer has a chance to open.
                return True
            self._last_tab_time = 0.0
        return super().eventFilter(watched, event)

    def _event_belongs_to_dialog(self, watched) -> bool:
        return isinstance(watched, QWidget) and watched.window() is self

    def done(self, result: int) -> None:
        if self._event_filter_installed:
            application = QApplication.instance()
            if application is not None:
                application.removeEventFilter(self)
            self._event_filter_installed = False
        super().done(result)

    def _remove_journal(self, editor: JournalEditor) -> None:
        if len(self._journal_editors) == 1:
            editor.clear()
            return
        self._journal_editors.remove(editor)
        self.journal_layout.removeWidget(editor)
        editor.deleteLater()
        self._refresh_numbers()

    def _refresh_numbers(self) -> None:
        for index, editor in enumerate(self._journal_editors, 1):
            editor._set_heading(index)

    def _fill(self, paper: dict) -> None:
        self.title_edit.setText(str(paper.get("title", "")))
        # QLineEdit moves its cursor to the end after setText().  For a long
        # existing title, opening the editor should show its beginning first.
        self.title_edit.setCursorPosition(0)
        self.keywords_edit.setText(", ".join(str(item) for item in paper.get("keywords", []) if str(item).strip()))
        self.summary_edit.setPlainText(str(paper.get("summary", "")))
        self._files = [dict(item) for item in paper.get("files", []) if isinstance(item, dict)]
        self._render_files()
        journals = paper.get("journals", [])
        if not isinstance(journals, list) or not journals:
            journals = [{
                "name": paper.get("journal", ""),
                "publisher": paper.get("publisher", ""),
                "date": paper.get("date", ""),
                "status_updated_at": paper.get("status_updated_at", paper.get("date", "")),
                "status": paper.get("status", STATUSES[0]),
                "result": paper.get("result", ""),
                "notes": paper.get("notes", ""),
            }]
        for journal in journals:
            self._add_journal(journal)

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "关联论文文件")
        for path in paths:
            self._files.append(
                {"id": uuid4().hex, "name": Path(path).name, "path": path, "kind": "file"}
            )
        self._render_files()

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "关联论文文件夹")
        if path:
            self._files.append({"id": uuid4().hex, "name": Path(path).name or path, "path": path, "kind": "folder"})
            self._render_files()

    def _render_files(self) -> None:
        while self.file_box.count():
            child = self.file_box.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        for item in self._files:
            row = FileAttachmentRow(item)
            row.remove_requested.connect(self._remove_file)
            self.file_box.addWidget(row)

    def _remove_file(self, file_id: str) -> None:
        self._files = [item for item in self._files if str(item.get("id")) != file_id]
        self._render_files()

    def _validate_and_accept(self) -> None:
        missing = []
        if not self.title_edit.text().strip():
            missing.append("论文名称")
        for index, editor in enumerate(self._journal_editors, 1):
            if not editor.name_edit.text().strip():
                missing.append(f"第 {index} 条期刊名称")
            if not editor.publisher_edit.text().strip():
                missing.append(f"第 {index} 条出版社")
        if missing:
            QMessageBox.warning(self, "信息不完整", "请填写：" + "、".join(missing))
            return
        future_fields: list[str] = []
        for index, editor in enumerate(self._journal_editors, 1):
            labels = _future_journal_date_labels(editor.values())
            future_fields.extend(f"第 {index} 条{label}" for label in labels)
        if future_fields:
            QMessageBox.warning(
                self,
                "日期不能晚于今天",
                "投稿日期、状态更新时间和时间线日期不能晚于今天。请修正：\n" + "、".join(future_fields[:8]),
            )
            return
        self.accept()

    def _run_ai_fill(self) -> None:
        title = self.title_edit.text().strip()
        if not title:
            self.ai_hint.setText("请先填写论文名称，再生成草稿。")
            return
        if self._ai_worker is not None and self._ai_worker.isRunning():
            return
        self.ai_progress.begin("正在检查 AI 配置…")
        if not is_deepseek_ready("paper_record_fill"):
            self.ai_hint.setText("尚未配置 DeepSeek。可在“设置 → 智能增强与 JCR”启用论文记录补全。")
            self.ai_progress.fail("论文 AI 补全未配置，未开始处理。")
            return
        self.ai_fill_button.setEnabled(False)
        self.ai_fill_button.setText("AI 补全中…")
        self.ai_progress.update("DeepSeek 正在生成可编辑的研究信息草稿…", 5)
        self.ai_hint.setText("DeepSeek 正在生成可编辑的研究信息草稿…")
        self._ai_worker = PaperRecordAiThread(
            {
                "title": title,
                "keywords": [part.strip() for part in self.keywords_edit.text().replace("，", ",").split(",") if part.strip()],
                "summary": self.summary_edit.toPlainText().strip(),
            },
            self,
        )
        self._ai_worker.progress.connect(self._ai_fill_progress)
        self._ai_worker.completed.connect(self._ai_fill_finished)
        self._ai_worker.failed.connect(self._ai_fill_failed)
        self._ai_worker.finished.connect(self._clear_ai_worker)
        self._ai_worker.start()

    def _ai_fill_finished(self, result: dict) -> None:
        existing = [part.strip() for part in self.keywords_edit.text().replace("，", ",").split(",") if part.strip()]
        seen = {part.casefold() for part in existing}
        merged = list(existing)
        for keyword in result.get("keywords", []):
            keyword = str(keyword).strip()
            if keyword and keyword.casefold() not in seen:
                seen.add(keyword.casefold())
                merged.append(keyword)
        if merged:
            self.keywords_edit.setText(", ".join(merged[:12]))
        if not self.summary_edit.toPlainText().strip() and str(result.get("summary", "")).strip():
            self.summary_edit.setPlainText(str(result.get("summary", "")).strip())
        self.ai_hint.setText(str(result.get("record_hint", "已生成草稿，保存前可继续修改。")) or "已生成草稿，保存前可继续修改。")
        self.ai_progress.complete("AI 论文记录补全完成。")

    def _ai_fill_failed(self, message: str) -> None:
        self.ai_hint.setText("AI 补全失败：" + str(message))
        self.ai_progress.fail("AI 论文记录补全失败：" + str(message))

    def _ai_fill_progress(self, message: str, value: int) -> None:
        self.ai_hint.setText(str(message))
        self.ai_progress.update(str(message), int(value))

    def _clear_ai_worker(self) -> None:
        if self._ai_worker is not None:
            self._ai_worker.deleteLater()
        self._ai_worker = None
        self.ai_fill_button.setEnabled(True)
        self.ai_fill_button.setText("AI 补全")

    def paper(self) -> dict:
        keywords = [item.strip() for item in self.keywords_edit.text().replace("，", ",").split(",") if item.strip()]
        return {
            "id": self._paper.get("id", uuid4().hex),
            "title": self.title_edit.text().strip(),
            "keywords": keywords,
            "summary": self.summary_edit.toPlainText().strip(),
            "files": self._files,
            "created_at": str(self._paper.get("created_at", "")) or date.today().isoformat(),
            "journals": [editor.values() for editor in self._journal_editors],
        }


class JournalDialog(QDialog):
    """Edit one journal history without opening the whole paper editor."""

    def __init__(self, paper_title: str, journal: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑期刊经历")
        parent_width = parent.width() if parent else 520
        self.setMinimumWidth(350)
        self.setMaximumWidth(max(350, min(520, parent_width - 24)))
        self.resize(max(350, min(460, parent_width - 24)), 540)
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QDialog QLabel { color: #f4f6ff; }
            QDialog QLineEdit, QDialog QDateEdit, QDialog QComboBox, QDialog QPlainTextEdit { background: #202d4d; color: #ffffff; border: 1px solid #53698f; border-radius: 6px; padding: 7px 9px; }
            QDialog QLineEdit:focus, QDialog QDateEdit:focus, QDialog QComboBox:focus, QDialog QPlainTextEdit:focus { border-color: #70c9ff; }
            QDialog QComboBox QAbstractItemView { background: #202d4d; color: #ffffff; selection-background-color: #2e78b7; selection-color: #ffffff; border: 1px solid #70c9ff; outline: 0; }
            QDialog QComboBox QAbstractItemView::item { min-height: 24px; padding: 5px 10px; color: #ffffff; }
            QDialog QComboBox QAbstractItemView::item:selected { background: #2e78b7; color: #ffffff; }
            QDialog #journalEditor { background: #172340; border: 1px solid #42577d; border-radius: 7px; }
            QDialog QDialogButtonBox QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 16px; }
            QDialog QDialogButtonBox QPushButton[text="保存"] { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 17, 20, 17)
        root.setSpacing(9)
        heading = QLabel(f"论文：{paper_title}")
        heading.setObjectName("sectionHeading")
        heading.setWordWrap(True)
        root.addWidget(heading)
        self.editor = JournalEditor(1, journal, default_status_update_today=True)
        self.editor.remove_button.hide()
        root.addWidget(self.editor)
        self.timeline_note = QPlainTextEdit()
        self.timeline_note.setPlaceholderText("本次进展（可选）。打开后状态更新时间默认为今天，也可手动调整。")
        self.timeline_note.setFixedHeight(52)
        root.addWidget(QLabel("本次时间线备注"))
        root.addWidget(self.timeline_note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _validate_and_accept(self) -> None:
        if not self.editor.name_edit.text().strip() or not self.editor.publisher_edit.text().strip():
            QMessageBox.warning(self, "信息不完整", "请填写期刊名称和出版社。")
            return
        labels = _future_journal_date_labels(
            self.editor.values(self.timeline_note.toPlainText().strip(), record_timeline_event=True)
        )
        if labels:
            QMessageBox.warning(
                self,
                "日期不能晚于今天",
                "投稿日期、状态更新时间和时间线日期不能晚于今天。请修正：" + "、".join(labels),
            )
            return
        self.accept()

    def journal(self) -> dict:
        return self.editor.values(
            timeline_note=self.timeline_note.toPlainText().strip(),
            record_timeline_event=True,
        )


class JournalTimelineDialog(QDialog):
    """Read-only visual history for one paper-journal submission path."""

    def __init__(self, paper_title: str, journal: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("投稿时间线")
        parent_width = parent.width() if parent else 520
        self.setMinimumWidth(340)
        self.setMaximumWidth(max(340, min(520, parent_width - 24)))
        self.resize(max(340, min(460, parent_width - 24)), 470)
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QLabel { color: #f4f6ff; }
            #timelineTitle { color: #ffffff; font-size: 18px; font-weight: 700; }
            #timelineDate { color: #80d8ff; font-size: 11px; }
            #timelineStatus { color: #ffffff; font-weight: 700; }
            #timelineNote { color: #b9c8e5; font-size: 11px; }
            #timelineDot { color: #62e6a0; font-size: 16px; }
            #timelineItem { background: #172340; border: 1px solid #42577d; border-radius: 6px; }
            QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 16px; }
            QPushButton:hover { background: #3a527d; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)
        title = QLabel(f"{journal.get('name', '期刊')} · 投稿时间线")
        title.setObjectName("timelineTitle")
        title.setWordWrap(True)
        root.addWidget(title)
        subtitle = QLabel(str(paper_title))
        subtitle.setObjectName("timelineNote")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(0, 0, 5, 0)
        box.setSpacing(7)
        events = [event for event in journal.get("timeline", []) if isinstance(event, dict)]
        if not events:
            events = [{"date": journal.get("date", ""), "status": journal.get("status", ""), "note": ""}]
        for event in sorted(events, key=lambda item: str(item.get("date", ""))):
            item = QFrame()
            item.setObjectName("timelineItem")
            item_root = QHBoxLayout(item)
            item_root.setContentsMargins(10, 8, 10, 8)
            dot = QLabel("●")
            dot.setObjectName("timelineDot")
            item_root.addWidget(dot, alignment=Qt.AlignmentFlag.AlignTop)
            event_box = QVBoxLayout()
            event_box.setSpacing(2)
            date_label = QLabel(str(event.get("date", "")))
            date_label.setObjectName("timelineDate")
            event_box.addWidget(date_label)
            status_label = QLabel(str(event.get("status", "")))
            status_label.setObjectName("timelineStatus")
            event_box.addWidget(status_label)
            note = str(event.get("note", "")).strip()
            if note:
                note_label = QLabel(note)
                note_label.setObjectName("timelineNote")
                note_label.setWordWrap(True)
                event_box.addWidget(note_label)
            item_root.addLayout(event_box, 1)
            box.addWidget(item)
        box.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)


class JournalHistoryRow(QFrame):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    timeline_requested = Signal(str)

    def __init__(self, journal: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("journalHistory")
        self.setProperty("journal_id", str(journal.get("id", "")))
        self.setProperty("attention", False)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 9, 12, 9)
        root.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(7)
        name = QLabel(str(journal.get("name", "未填写期刊")))
        name.setObjectName("cardMeta")
        name.setWordWrap(True)
        name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(name, 1)
        status = QLabel(str(journal.get("status", STATUSES[0])))
        status.setObjectName("statusBadge")
        top.addWidget(status)
        edit = QPushButton("编辑")
        edit.setObjectName("rowButton")
        edit.setToolTip("单独编辑这条期刊经历")
        edit.clicked.connect(lambda: self.edit_requested.emit(str(journal.get("id", ""))))
        top.addWidget(edit)
        delete = QPushButton("删除")
        delete.setObjectName("dangerButton")
        delete.setToolTip("删除这条期刊经历")
        delete.clicked.connect(lambda: self.delete_requested.emit(str(journal.get("id", ""))))
        top.addWidget(delete)
        root.addLayout(top)

        details_text = f"{journal.get('publisher', '')}  ·  投稿：{journal.get('date', '')}"
        status_updated_at = str(journal.get("status_updated_at", "")).strip()
        if status_updated_at and status_updated_at != str(journal.get("date", "")).strip():
            details_text += f"  ·  状态更新：{status_updated_at}"
        due_date = str(journal.get("revision_due_date", ""))
        if str(journal.get("status", "")) == "修改中" and due_date:
            details_text += f"  ·  回复截止：{due_date}"
        details = QLabel(details_text)
        details.setObjectName("cardDetail")
        details.setWordWrap(True)
        root.addWidget(details)

        timeline = QPushButton("时间线")
        timeline.setObjectName("rowButton")
        timeline.setToolTip("查看这条期刊经历的投稿时间线")
        timeline.clicked.connect(lambda: self.timeline_requested.emit(str(journal.get("id", ""))))
        root.addWidget(timeline, alignment=Qt.AlignmentFlag.AlignRight)

        result = str(journal.get("result", "")).strip()
        if result:
            result_label = QLabel(f"结果：{result}")
            result_label.setObjectName("cardDetail")
            result_label.setWordWrap(True)
            root.addWidget(result_label)
        notes = str(journal.get("notes", "")).strip()
        if notes:
            notes_label = QLabel(f"备注：{notes}")
            notes_label.setObjectName("cardNotes")
            notes_label.setWordWrap(True)
            root.addWidget(notes_label)


class PaperCard(QFrame):
    action_requested = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    journal_edit_requested = Signal(str, str)
    journal_delete_requested = Signal(str, str)
    journal_timeline_requested = Signal(str, str)

    def __init__(self, paper: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.paper = paper
        self.setObjectName("paperCard")
        self.setProperty("paper_id", str(paper.get("id", "")))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(13, 10, 11, 9)
        root.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(7)
        title = QLabel(str(self.paper.get("title", "未命名论文")))
        title.setObjectName("cardTitle")
        title.setWordWrap(True)
        title.setMinimumWidth(0)
        title.setMaximumHeight(42)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        title.setToolTip(str(self.paper.get("title", "未命名论文")))
        top.addWidget(title, 1)
        journals = self.paper.get("journals", [])
        current = self._current_journal(journals)
        current_status = QLabel(str(current.get("status", "论文构想")) if current else "论文构想")
        current_status.setObjectName("statusBadge")
        current_status.setToolTip(
            f"当前关注：{current.get('name', '')} · {current.get('status', '')}" if current else "还没有期刊经历"
        )
        top.addWidget(current_status, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)

        tools = QHBoxLayout()
        tools.setSpacing(4)
        count = QLabel(f"{len(journals)} 条期刊经历")
        count.setObjectName("historyCount")
        tools.addWidget(count)
        if current and str(current.get("name", "")).strip():
            current_name = QLabel(f"当前：{current.get('name', '')}")
            current_name.setObjectName("cardDetail")
            current_name.setToolTip(str(current.get("name", "")))
            current_name.setMinimumWidth(0)
            current_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            tools.addWidget(current_name, 1)
        files = self.paper.get("files", [])
        if files:
            file_count = QLabel(f"附件 {len(files)}")
            file_count.setObjectName("historyCount")
            file_count.setToolTip("已关联的本地文件或文件夹")
            tools.addWidget(file_count)
        else:
            tools.addStretch()
        action = QPushButton("处理")
        action.setObjectName("primaryAction")
        action.setToolTip("查看下一步、快速更新状态与关联灵感")
        action.clicked.connect(lambda: self.action_requested.emit(str(self.paper.get("id", ""))))
        tools.addWidget(action)
        self.expand_button = QPushButton("期刊经历")
        self.expand_button.setObjectName("rowButton")
        self.expand_button.setToolTip("展开期刊投稿经历")
        self.expand_button.clicked.connect(self._toggle_journals)
        tools.addWidget(self.expand_button)
        more = QPushButton("…")
        more.setObjectName("rowButton")
        more.setToolTip("更多操作")
        more.setFixedSize(28, 24)
        from PySide6.QtWidgets import QMenu

        menu = QMenu(more)
        edit_action = menu.addAction("编辑论文")
        edit_action.triggered.connect(lambda: self.edit_requested.emit(str(self.paper.get("id", ""))))
        delete_action = menu.addAction("删除论文")
        delete_action.triggered.connect(lambda: self.delete_requested.emit(str(self.paper.get("id", ""))))
        more.setMenu(menu)
        tools.addWidget(more)
        tools.addWidget(OrderDragHandle(str(self.paper.get("id", "")), "papers"))
        root.addLayout(tools)

        self.journal_details = QWidget()
        self._journals_expanded = False
        detail_layout = QVBoxLayout(self.journal_details)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(7)
        self.journal_rows = QWidget()
        journal_rows_layout = QVBoxLayout(self.journal_rows)
        journal_rows_layout.setContentsMargins(0, 0, 0, 0)
        journal_rows_layout.setSpacing(7)
        for journal in journals:
            history_row = JournalHistoryRow(journal)
            history_row.edit_requested.connect(
                lambda journal_id, paper_id=str(self.paper.get("id", "")): self.journal_edit_requested.emit(paper_id, journal_id)
            )
            history_row.delete_requested.connect(
                lambda journal_id, paper_id=str(self.paper.get("id", "")): self.journal_delete_requested.emit(paper_id, journal_id)
            )
            history_row.timeline_requested.connect(
                lambda journal_id, paper_id=str(self.paper.get("id", "")): self.journal_timeline_requested.emit(paper_id, journal_id)
            )
            journal_rows_layout.addWidget(history_row)
        detail_layout.addWidget(self.journal_rows)
        if files:
            file_heading = QLabel("关联文件")
            file_heading.setObjectName("sectionLabel")
            detail_layout.addWidget(file_heading)
            for item in files:
                path = str(item.get("path", ""))
                open_file = QPushButton(f"附件  {item.get('name', path)}")
                open_file.setObjectName("fileOpenButton")
                open_file.setToolTip(path)
                open_file.clicked.connect(lambda _checked=False, target=path: self._open_attachment(target))
                detail_layout.addWidget(open_file, alignment=Qt.AlignmentFlag.AlignLeft)
        self.journal_details.setVisible(False)
        root.addWidget(self.journal_details)

    def _toggle_journals(self) -> None:
        self._journals_expanded = not self._journals_expanded
        self.journal_details.setVisible(self._journals_expanded)
        self.expand_button.setText("收起期刊" if self._journals_expanded else "期刊经历")
        self.expand_button.setToolTip("收起期刊投稿经历" if self._journals_expanded else "展开期刊投稿经历")

    def expand_journals(self) -> None:
        if not self._journals_expanded:
            self._toggle_journals()

    @staticmethod
    def _current_journal(journals: list[dict]) -> dict | None:
        if not journals:
            return None
        active = [
            journal
            for journal in journals
            if str(journal.get("status", "")) in {"准备投稿", "投稿中", "外审中", "修改中"}
        ]
        pool = active or journals
        return max(pool, key=lambda item: str(item.get("date", "")))

    def _open_attachment(self, path: str) -> None:
        if not path or not Path(path).exists():
            QMessageBox.information(self, "文件不可用", "找不到关联路径。文件可能已被移动或删除。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class RejectionArchiveRow(QFrame):
    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("rejectionArchiveRow")
        # QFrame needs this attribute on some Windows styles for its
        # stylesheet background to be painted instead of inheriting the page.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 6, 10, 6)
        root.setSpacing(7)
        marker = QLabel("×")
        marker.setObjectName("rejectionArchiveMarker")
        root.addWidget(marker)
        text = QLabel(f"{item.get('paper_title', '未命名论文')}  ·  {item.get('journal_name', '未填写期刊')}")
        text.setObjectName("rejectionArchiveText")
        text.setWordWrap(True)
        root.addWidget(text, 1)
        archived_date = str(item.get("rejected_date", "")).strip() or str(item.get("archived_at", "")).strip()
        if archived_date:
            meta = QLabel(archived_date)
            meta.setObjectName("rejectionArchiveMeta")
            root.addWidget(meta, alignment=Qt.AlignmentFlag.AlignTop)


class PaperPage(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.papers = load_papers()
        self.rejection_archive = load_rejection_archive()
        self._paper_cards: dict[str, PaperCard] = {}
        self._build_ui()
        self._render()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title = QLabel("论文投稿记录")
        title.setObjectName("paperPageTitle")
        subtitle = QLabel("一篇论文，完整保留所有期刊经历")
        subtitle.setObjectName("dateLabel")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box)
        heading.addStretch()
        self.archive_corner_button = QToolButton()
        self.archive_corner_button.setObjectName("archiveCornerButton")
        self.archive_corner_button.setText("拒稿归档 0")
        self.archive_corner_button.setToolTip("查看已归档的拒稿期刊")
        self.archive_corner_button.clicked.connect(self._show_archive_dialog)
        heading.addWidget(self.archive_corner_button, alignment=Qt.AlignmentFlag.AlignBottom)
        choose = QPushButton("为论文选刊")
        choose.setObjectName("subtleButton")
        choose.setToolTip("综合期刊优先级、关键词、JCR、个人经历和反馈，为已有论文挑选目标期刊")
        choose.clicked.connect(self._choose_journal)
        heading.addWidget(choose, alignment=Qt.AlignmentFlag.AlignBottom)
        add = QPushButton("＋ 新增论文")
        add.setObjectName("primaryButton")
        add.clicked.connect(self._add_paper)
        heading.addWidget(add, alignment=Qt.AlignmentFlag.AlignBottom)
        root.addLayout(heading)

        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索论文、期刊或出版社")
        self.search_edit.textChanged.connect(self._render)
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(QLabel("筛选"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["全部", "投稿中", "已发表", "拒稿"])
        self.filter_combo.currentTextChanged.connect(self._render)
        toolbar.addWidget(self.filter_combo)
        root.addLayout(toolbar)

        self.count_label = QLabel()
        self.count_label.setObjectName("sectionLabel")
        root.addWidget(self.count_label)

        self.lifecycle_label = QLabel()
        self.lifecycle_label.setObjectName("paperLifecycleHint")
        self.lifecycle_label.setWordWrap(True)
        self.lifecycle_label.hide()
        root.addWidget(self.lifecycle_label)

        self.date_issue_frame = QFrame()
        self.date_issue_frame.setObjectName("futureDateIssueFrame")
        self.date_issue_frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        issue_row = QHBoxLayout(self.date_issue_frame)
        issue_row.setContentsMargins(9, 6, 8, 6)
        issue_row.setSpacing(7)
        self.date_issue_label = QLabel()
        self.date_issue_label.setObjectName("futureDateIssueLabel")
        self.date_issue_label.setWordWrap(True)
        issue_row.addWidget(self.date_issue_label, 1)
        self.repair_dates_button = QPushButton("一键修正为今天")
        self.repair_dates_button.setObjectName("futureDateRepairButton")
        self.repair_dates_button.setToolTip("只修正晚于今天的投稿日期、状态更新时间和时间线日期；不会更改截止日或其他记录")
        self.repair_dates_button.clicked.connect(self._repair_future_dates)
        issue_row.addWidget(self.repair_dates_button, alignment=Qt.AlignmentFlag.AlignTop)
        self.date_issue_frame.hide()
        root.addWidget(self.date_issue_frame)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = ReorderableColumn("papers")
        self.content.setObjectName("paperContent")
        self.content.rows_layout.setContentsMargins(2, 2, 8, 10)
        self.content.rows_layout.setSpacing(9)
        self.content.order_changed.connect(self._reorder_papers)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

    def _filtered(self) -> list[dict]:
        keyword = self.search_edit.text().strip().casefold()
        category = self.filter_combo.currentText()
        result = []
        for paper in self.papers:
            journals = paper.get("journals", [])
            haystack = str(paper.get("title", "")) + " " + " ".join(
                f"{journal.get('name', '')} {journal.get('publisher', '')}" for journal in journals
            )
            if keyword and keyword not in haystack.casefold():
                continue
            statuses = {str(journal.get("status", "")) for journal in journals}
            if category == "投稿中" and not statuses.intersection(ACTIVE_STATUSES):
                continue
            if category == "已发表" and "已发表" not in statuses:
                continue
            if category == "拒稿" and "拒稿" not in statuses:
                continue
            result.append(paper)
        return result

    def _render(self) -> None:
        self.content.clear_rows()
        self._paper_cards.clear()
        self._render_future_date_issues()
        self._render_archive()
        records = self._filtered()
        self.count_label.setText(f"共 {len(records)} 篇论文")
        for paper in records:
            card = PaperCard(paper)
            card.action_requested.connect(self.open_action_center)
            card.edit_requested.connect(self._edit_paper)
            card.delete_requested.connect(self._delete_paper)
            card.journal_edit_requested.connect(self._edit_journal)
            card.journal_delete_requested.connect(self._delete_journal)
            card.journal_timeline_requested.connect(self._show_journal_timeline)
            self.content.add_row(card, str(paper.get("id", "")))
            self._paper_cards[str(paper.get("id", ""))] = card

        if not records:
            empty = QLabel("没有找到论文记录。点击右上角，记录你的第一篇论文。")
            empty.setObjectName("emptyLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.content.set_placeholder(empty)

    def _render_future_date_issues(self) -> None:
        issues = find_future_paper_date_issues(self.papers)
        self.date_issue_frame.setVisible(bool(issues))
        if not issues:
            return
        field_counts: dict[str, int] = {}
        for issue in issues:
            label = str(issue.get("label", "日期"))
            field_counts[label] = field_counts.get(label, 0) + 1
        detail = "、".join(f"{label} {count} 项" for label, count in field_counts.items())
        self.date_issue_label.setText(
            f"⚠ 检测到 {len(issues)} 项晚于今天的历史日期（{detail}）。原记录未改动，保存会被保护。"
        )

    def _repair_future_dates(self) -> None:
        result = repair_future_paper_dates(self.papers)
        if not result.get("total"):
            self._render_future_date_issues()
            return
        saved = save_papers(self.papers)
        if not saved.get("saved", False):
            QMessageBox.warning(self, "暂未修正", "仍有无法保存的未来日期，请逐条检查后再试。")
            self.papers = load_papers()
            self._render()
            return
        self.papers = load_papers()
        self.lifecycle_label.setText(
            "已修正未来日期："
            f"投稿 {result['submission_dates']} 项、状态 {result['status_updated_dates']} 项、时间线 {result['timeline_dates']} 项。"
        )
        self.lifecycle_label.show()
        self._render()
        self.changed.emit()

    def _render_archive(self) -> None:
        self.rejection_archive = load_rejection_archive()
        count = len(self.rejection_archive)
        self.archive_corner_button.setText(f"拒稿归档 {count}")
        self.archive_corner_button.setToolTip(
            "查看已归档的拒稿期刊" if count else "暂无拒稿归档；单个期刊改为“拒稿”后会保存在这里"
        )

    def reveal_journal(self, paper_id: str, journal_id: str) -> bool:
        """Reveal a HOME action's exact journal and give it a brief visual pulse."""
        paper_id = str(paper_id or "")
        journal_id = str(journal_id or "")
        if not paper_id or not journal_id:
            return False
        if self.search_edit.text() or self.filter_combo.currentText() != "全部":
            self.search_edit.blockSignals(True)
            self.search_edit.clear()
            self.search_edit.blockSignals(False)
            self.filter_combo.blockSignals(True)
            self.filter_combo.setCurrentText("全部")
            self.filter_combo.blockSignals(False)
            self._render()
        card = self._paper_cards.get(paper_id)
        if card is None:
            self._render()
            card = self._paper_cards.get(paper_id)
        if card is None:
            return False
        card.expand_journals()
        target = next(
            (
                row
                for row in card.findChildren(JournalHistoryRow)
                if str(row.property("journal_id") or "") == journal_id
            ),
            None,
        )
        if target is None:
            return False
        self.scroll.ensureWidgetVisible(target, 12, 16)
        self._set_journal_attention(target, True)
        QTimer.singleShot(220, lambda row=target: self._pulse_journal_attention(row, False, 5))
        return True

    @staticmethod
    def _set_journal_attention(row: JournalHistoryRow, enabled: bool) -> None:
        try:
            row.setProperty("attention", enabled)
            row.style().unpolish(row)
            row.style().polish(row)
            row.update()
        except RuntimeError:
            # The page can be closed while the final pulse timer is pending.
            return

    def _pulse_journal_attention(self, row: JournalHistoryRow, enabled: bool, remaining: int) -> None:
        try:
            if row.parentWidget() is None:
                return
        except RuntimeError:
            return
        self._set_journal_attention(row, enabled)
        if remaining > 0:
            QTimer.singleShot(
                220,
                lambda target=row, next_enabled=not enabled, steps=remaining - 1: self._pulse_journal_attention(
                    target, next_enabled, steps
                ),
            )

    def _show_archive_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("拒稿归档")
        dialog.setMinimumWidth(330)
        dialog.resize(min(max(360, self.width() - 20), 560), min(max(260, self.height() - 16), 440))
        root = QVBoxLayout(dialog)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)
        hint = QLabel("每条记录只归档被拒稿的单个期刊；原论文及其他期刊经历不会删除。")
        hint.setObjectName("archiveHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 3, 0)
        rows.setSpacing(5)
        if self.rejection_archive:
            for item in self.rejection_archive:
                rows.addWidget(RejectionArchiveRow(item))
        else:
            empty = QLabel("暂无拒稿归档。")
            empty.setObjectName("emptyLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rows.addWidget(empty)
        rows.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        close = QPushButton("关闭")
        close.setObjectName("subtleButton")
        close.clicked.connect(dialog.accept)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def _commit_papers(self) -> dict:
        result = save_papers(self.papers)
        if not result.get("saved", False):
            self.papers = load_papers()
            self.lifecycle_label.setText(
                f"检测到 {int(result.get('future_date_issue_count', 0))} 项晚于今天的历史日期；请先使用“一键修正为今天”。"
            )
            self.lifecycle_label.show()
            self._render()
            QMessageBox.warning(
                self,
                "未保存：存在未来日期",
                "投稿日期、状态更新时间或时间线日期不能晚于今天。原有记录已保留，请先点击页面上的“一键修正为今天”。",
            )
            return result
        messages = []
        if result.get("achievement_count"):
            messages.append(f"已将 {result['achievement_count']} 篇论文完整移入成果")
        if result.get("rejection_archive_count"):
            messages.append(f"已将 {result['rejection_archive_count']} 条拒稿期刊移入归档")
        if messages:
            self.lifecycle_label.setText("；".join(messages) + "。")
            self.lifecycle_label.show()
        self._render()
        self.changed.emit()
        return result

    def _add_paper(self) -> None:
        dialog = PaperDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.papers.append(dialog.paper())
            self._commit_papers()

    def _apply_selection_action(self, dialog: JournalSelectionDialog, journals: list[dict], action: str) -> None:
        paper_id, _journal_id = dialog.selection()
        paper_index = self._find_index(paper_id)
        candidate = dialog.selected_candidate()
        journal = dialog.selected_journal()
        if paper_index < 0 or not journal:
            if hasattr(dialog, "mark_action_complete"):
                dialog.mark_action_complete(action, False, "未找到当前论文或期刊，未保存任何数据。")
            return
        if action == "exclude":
            journal_name = str(journal.get("name", candidate.get("journal_name", ""))).strip()
            journal_id = str(
                journal.get("id", candidate.get("journal_id", "name:" + canonical_journal_name(journal_name)))
            ).strip()
            matches = [str(value).strip() for value in candidate.get("matched_terms", []) if str(value).strip()]
            set_journal_selection_feedback(
                paper_id,
                journal_id or "name:" + canonical_journal_name(journal_name),
                "不适合",
                matches,
                journal_name=journal_name,
            )
            dialog.mark_action_complete(
                action,
                True,
                f"已为当前论文排除“{journal_name}”；以后选刊会自动跳过。",
            )
            return
        if dialog.is_external_selection():
            name_key = str(journal.get("name", "")).strip().casefold()
            publisher_key = str(journal.get("publisher", "")).strip().casefold()
            existing = next(
                (
                    item
                    for item in journals
                    if str(item.get("name", "")).strip().casefold() == name_key
                    and str(item.get("publisher", "")).strip().casefold() == publisher_key
                ),
                None,
            )
            if existing is None:
                journal = external_candidate_to_library_journal(candidate)
                journals.append(journal)
                save_journal_library(journals)
            else:
                journal = existing
            if action == "import":
                message = f"“{journal.get('name', '')}”已加入期刊库，资料仍待核验。可继续比较其他推荐。"
                if hasattr(dialog, "mark_action_complete"):
                    dialog.mark_action_complete(action, True, message)
                else:
                    QMessageBox.information(self, "已快速入库", message)
                return
        elif action == "import":
            if hasattr(dialog, "mark_action_complete"):
                dialog.mark_action_complete(action, False, "本地期刊已经在期刊库中，无需重复入库。")
            return
        paper = self.papers[paper_index]
        updated_paper = append_journal_to_submission_path(paper, journal)
        if updated_paper == paper:
            message = "该论文已经包含这本期刊的投稿经历。"
            if hasattr(dialog, "mark_action_complete"):
                dialog.mark_action_complete(action, False, message)
            else:
                QMessageBox.information(self, "期刊已存在", message)
            return
        self.papers[paper_index] = updated_paper
        self._commit_papers()
        message = "已加入论文投稿路径：准备投稿。可继续比较其他推荐。"
        if hasattr(dialog, "mark_action_complete"):
            dialog.mark_action_complete(action, True, message)

    def _choose_journal(self) -> None:
        journals = load_journal_library()
        if not self.papers:
            QMessageBox.information(self, "暂无论文", "请先创建一篇论文，再开始为它选刊。")
            return
        frontier_data = load_frontier_data()
        profile = frontier_data.get("profile", {}) if isinstance(frontier_data, dict) else {}
        settings = load_app_settings()
        research_settings = settings.get("research", {}) if isinstance(settings, dict) else {}
        dialog = JournalSelectionDialog(
            self.papers,
            journals,
            profile if isinstance(profile, dict) else {},
            self,
            history=journal_usage_index(self.papers),
            constraints=research_settings if isinstance(research_settings, dict) else {},
        )
        if hasattr(dialog, "action_requested"):
            dialog.action_requested.connect(lambda action, d=dialog: self._apply_selection_action(d, journals, action))
            dialog.exec()
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_selection_action(dialog, journals, dialog.selection_action())

    def open_global_journal_import(self) -> None:
        """Bring up the target-paper picker after a registered global hotkey."""
        self._choose_journal()

    def open_action_center(self, paper_id: str) -> None:
        """Open the next-step view without making the user hunt through cards."""
        dialog = PaperActionCenterDialog(paper_id, self)
        requested_edit: list[str] = []

        def open_full_editor(target: str) -> None:
            requested_edit.append(target)
            dialog.accept()

        dialog.changed.connect(self._action_center_changed)
        dialog.edit_paper_requested.connect(open_full_editor)
        dialog.exec()
        if requested_edit:
            self._edit_paper(requested_edit[-1])

    def _action_center_changed(self) -> None:
        self.papers = load_papers()
        self._render()
        self.changed.emit()

    def _find_index(self, paper_id: str) -> int:
        return next((i for i, paper in enumerate(self.papers) if paper.get("id") == paper_id), -1)

    def _edit_paper(self, paper_id: str) -> None:
        index = self._find_index(paper_id)
        if index < 0:
            return
        dialog = PaperDialog(self, self.papers[index])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.papers[index] = dialog.paper()
            self._commit_papers()

    def _edit_journal(self, paper_id: str, journal_id: str) -> None:
        paper_index = self._find_index(paper_id)
        if paper_index < 0:
            return
        paper = self.papers[paper_index]
        journals = paper.get("journals", [])
        journal_index = next((i for i, journal in enumerate(journals) if str(journal.get("id")) == journal_id), -1)
        if journal_index < 0:
            return
        dialog = JournalDialog(str(paper.get("title", "未命名论文")), journals[journal_index], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            journals[journal_index] = dialog.journal()
            self._commit_papers()

    def _show_journal_timeline(self, paper_id: str, journal_id: str) -> None:
        paper_index = self._find_index(paper_id)
        if paper_index < 0:
            return
        paper = self.papers[paper_index]
        journal = next(
            (item for item in paper.get("journals", []) if str(item.get("id")) == journal_id),
            None,
        )
        if journal is not None:
            JournalTimelineDialog(str(paper.get("title", "未命名论文")), journal, self).exec()

    def _delete_journal(self, paper_id: str, journal_id: str) -> None:
        paper_index = self._find_index(paper_id)
        if paper_index < 0:
            return
        journals = self.papers[paper_index].get("journals", [])
        journal = next((item for item in journals if str(item.get("id", "")) == journal_id), None)
        if journal is None:
            return
        name = str(journal.get("name", "这条期刊经历"))
        if not confirm_delete(self, "删除期刊经历", f"确定删除“{name}”这条期刊经历吗？"):
            return
        journal_index = next((i for i, item in enumerate(journals) if str(item.get("id", "")) == journal_id), 0)
        removed = dict(journal)
        self.papers[paper_index]["journals"] = [
            item for item in journals if str(item.get("id", "")) != journal_id
        ]
        if not self._commit_papers().get("saved", False):
            return
        show_undo_toast(
            self,
            "已删除期刊经历",
            lambda: self._restore_journal(paper_id, journal_index, removed),
        )

    def _restore_journal(self, paper_id: str, index: int, journal: dict) -> None:
        paper_index = self._find_index(paper_id)
        if paper_index < 0:
            return
        journals = self.papers[paper_index].setdefault("journals", [])
        if any(str(item.get("id", "")) == str(journal.get("id", "")) for item in journals):
            return
        journals.insert(min(index, len(journals)), journal)
        self._commit_papers()

    def _delete_paper(self, paper_id: str) -> None:
        index = self._find_index(paper_id)
        if index < 0:
            return
        paper = self.papers[index]
        if not confirm_delete(self, "删除论文记录", f"“{paper.get('title', '这篇论文')}”及其所有期刊经历将被永久删除。"):
            return
        removed = dict(self.papers[index])
        del self.papers[index]
        if not self._commit_papers().get("saved", False):
            return
        show_undo_toast(
            self,
            "已删除论文记录",
            lambda: self._restore_paper(index, removed),
        )

    def _restore_paper(self, index: int, paper: dict) -> None:
        if any(str(entry.get("id", "")) == str(paper.get("id", "")) for entry in self.papers):
            return
        self.papers.insert(min(index, len(self.papers)), paper)
        self._commit_papers()

    def save(self) -> None:
        # Edits persist immediately.  On app shutdown, never interrupt the
        # user with a modal warning for a legacy future date that they have not
        # chosen to repair yet; the visible banner remains for the next visit.
        if find_future_paper_date_issues(self.papers):
            return
        self._commit_papers()

    def reload(self) -> None:
        self.papers = load_papers()
        self._render()

    def _reorder_papers(self, ordered_ids: list[str]) -> None:
        ordered_set = set(ordered_ids)
        by_id = {str(paper.get("id", "")): paper for paper in self.papers}
        ordered_records = iter([by_id[item_id] for item_id in ordered_ids if item_id in by_id])
        self.papers = [next(ordered_records) if str(paper.get("id", "")) in ordered_set else paper for paper in self.papers]
        self._commit_papers()
