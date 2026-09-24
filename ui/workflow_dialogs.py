"""Small action-focused dialogs shared by the desktop research workflow."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.ai_progress import AiProgressPanel
from utils.file_manager import (
    add_inspiration,
    add_todo,
    load_papers,
    load_readings,
    save_papers,
    save_readings,
)
from utils.ai_service import is_deepseek_ready, parse_research_capture_locally, parse_research_capture_with_ai


JOURNAL_STATUSES = ["准备投稿", "投稿中", "外审中", "修改中", "已接收", "已发表", "拒稿"]


def _dialog_style() -> str:
    return """
        QDialog { background: #0e192a; color: #edf4ff; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; }
        QLabel { color: #edf4ff; }
        #workflowTitle { color: #ffffff; font-size: 21px; font-weight: 700; }
        #workflowHint, #workflowMeta { color: #aebbd8; font-size: 11px; }
        #workflowCard { background: #14233a; border: 1px solid #31506f; border-radius: 8px; }
        #workflowHeading { color: #ffffff; font-weight: 700; }
        #workflowAccent { color: #77d8ff; font-size: 12px; }
        #workflowWarning { color: #ffd477; font-size: 12px; }
        QLineEdit, QComboBox, QPlainTextEdit { background: #111f33; color: #f4f8ff; border: 1px solid #365575; border-radius: 7px; padding: 7px 9px; }
        QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border-color: #67c9f7; }
        QComboBox QAbstractItemView { background: #111f33; color: #ffffff; selection-background-color: #2a628f; selection-color: #ffffff; }
        QPushButton { background: #1a304b; color: #e6f0fc; border: 1px solid #385a7d; border-radius: 6px; padding: 6px 10px; }
        QPushButton:hover { background: #26496d; color: #ffffff; }
        #primaryAction { background: #57d89a; color: #082016; border-color: #57d89a; font-weight: 700; }
        #primaryAction:hover { background: #78e8ad; }
        #dangerAction { background: transparent; color: #ffadb5; border: 0; }
        QScrollArea { border: 0; background: transparent; }
    """


def _append_journal_update(journal: dict, status: str, note: str) -> bool:
    """Apply a quick status/update note while preserving the full timeline."""
    today_key = date.today().isoformat()
    previous = str(journal.get("status", "准备投稿"))
    changed = status != previous
    note = note.strip()
    if changed:
        journal["status"] = status
    if changed or note:
        # Keep the original submission date intact.  Quick updates reset only
        # the status clock used by 15-day follow-up reminders.
        journal["status_updated_at"] = today_key
    if note:
        existing = str(journal.get("notes", "")).strip()
        entry = f"{today_key}：{note}"
        journal["notes"] = f"{existing}\n{entry}" if existing else entry
    if changed or note:
        timeline = [entry for entry in journal.get("timeline", []) if isinstance(entry, dict)]
        if timeline and timeline[-1].get("date") == today_key and timeline[-1].get("status") == status:
            if note:
                timeline[-1]["note"] = note
        else:
            timeline.append({"id": uuid4().hex, "date": today_key, "status": status, "note": note})
        journal["timeline"] = timeline
    return changed or bool(note)


class CaptureRecognitionThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, text: str, papers: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._papers = [dict(paper) for paper in papers]

    def run(self) -> None:
        try:
            self.progress.emit("AI 正在识别记录类型与关联论文…", 20)
            result = parse_research_capture_with_ai(self._text, self._papers)
            self.progress.emit("AI 正在整理可编辑草稿…", 92)
            self.completed.emit(result)
        except Exception as error:
            self.failed.emit(str(error))


class ResearchInboxDialog(QDialog):
    """One frictionless entry point for a task, idea, reading or paper update."""

    recorded = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        preset: str = "task",
        initial_text: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("科研收件箱")
        parent_width = parent.width() if parent else 520
        self.setMinimumWidth(360)
        self.setMaximumWidth(max(360, min(560, parent_width - 20)))
        self.resize(max(360, min(500, parent_width - 20)), 470)
        self.setStyleSheet(_dialog_style())
        self._papers: list[dict] = []
        self._capture_worker: CaptureRecognitionThread | None = None
        self._build_ui(initial_text)
        wanted = {"task": 0, "inspiration": 1, "reading": 2, "paper": 3}.get(preset, 0)
        self.kind_combo.setCurrentIndex(wanted)
        self._refresh_papers()
        self._switch_kind()

    def _build_ui(self, initial_text: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)
        title = QLabel("科研收件箱")
        title.setObjectName("workflowTitle")
        root.addWidget(title)
        hint = QLabel("先记下来，再决定它属于今天、灵感、待读，还是一条论文更新。")
        hint.setObjectName("workflowHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        kind_row = QHBoxLayout()
        kind_row.addWidget(QLabel("记录到"))
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("今日任务", "task")
        self.kind_combo.addItem("科研灵感", "inspiration")
        self.kind_combo.addItem("值得阅读", "reading")
        self.kind_combo.addItem("论文更新", "paper")
        self.kind_combo.currentIndexChanged.connect(self._switch_kind)
        kind_row.addWidget(self.kind_combo, 1)
        root.addLayout(kind_row)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("写下一句话，例如“回复 CATENA 审稿意见”或粘贴论文题目……")
        self.text_edit.setPlainText(initial_text)
        self.text_edit.setFixedHeight(92)
        root.addWidget(self.text_edit)

        recognition_row = QHBoxLayout()
        self.recognition_hint = QLabel("可直接写一句话；智能识别只生成草稿，仍由你确认后保存。")
        self.recognition_hint.setObjectName("workflowHint")
        self.recognition_hint.setWordWrap(True)
        recognition_row.addWidget(self.recognition_hint, 1)
        self.recognize_button = QPushButton("智能识别")
        self.recognize_button.setObjectName("primaryAction")
        self.recognize_button.clicked.connect(self._recognize_capture)
        recognition_row.addWidget(self.recognize_button)
        root.addLayout(recognition_row)
        self.ai_progress = AiProgressPanel(object_name="captureAiProgress")
        root.addWidget(self.ai_progress)

        self.detail_stack = QStackedWidget()
        self.detail_stack.addWidget(self._build_task_detail())
        self.detail_stack.addWidget(self._build_inspiration_detail())
        self.detail_stack.addWidget(self._build_reading_detail())
        self.detail_stack.addWidget(self._build_paper_detail())
        root.addWidget(self.detail_stack, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("收入收件箱")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primaryAction")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._record)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_task_detail(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.task_priority = QComboBox()
        self.task_priority.addItem("暂不标记", "")
        self.task_priority.addItem("紧急且重要", "urgent_important")
        self.task_priority.addItem("重要不紧急", "important_not_urgent")
        self.task_priority.addItem("紧急不重要", "urgent_not_important")
        self.task_priority.addItem("不紧急不重要", "not_urgent_not_important")
        form.addRow("优先级", self.task_priority)
        label = QLabel("保存后会进入今天的待办；可在待办页继续调整持续时间和循环。")
        label.setObjectName("workflowHint")
        label.setWordWrap(True)
        form.addRow("", label)
        return page

    @staticmethod
    def _build_inspiration_detail() -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        hint = QLabel("灵感会保留在“灵感与待读”中，之后可一键转为任务或关联论文。")
        hint.setObjectName("workflowHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        return page

    def _build_reading_detail(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.reading_reason = QPlainTextEdit()
        self.reading_reason.setPlaceholderText("为什么值得读（可选）")
        self.reading_reason.setFixedHeight(56)
        form.addRow("原因", self.reading_reason)
        return page

    def _build_paper_detail(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.paper_combo = QComboBox()
        self.paper_combo.currentIndexChanged.connect(self._refresh_journals)
        form.addRow("论文", self.paper_combo)
        self.journal_combo = QComboBox()
        form.addRow("期刊", self.journal_combo)
        self.paper_status = QComboBox()
        self.paper_status.addItems(JOURNAL_STATUSES)
        form.addRow("更新为", self.paper_status)
        self.paper_note = QPlainTextEdit()
        self.paper_note.setPlaceholderText("记录本次来信、决定或下一步（可选）")
        self.paper_note.setFixedHeight(56)
        form.addRow("说明", self.paper_note)
        self.paper_empty_hint = QLabel()
        self.paper_empty_hint.setObjectName("workflowWarning")
        self.paper_empty_hint.setWordWrap(True)
        form.addRow("", self.paper_empty_hint)
        return page

    def _switch_kind(self) -> None:
        self.detail_stack.setCurrentIndex(self.kind_combo.currentIndex())

    def _refresh_papers(self) -> None:
        self._papers = load_papers()
        current_id = self.paper_combo.currentData()
        self.paper_combo.blockSignals(True)
        self.paper_combo.clear()
        for paper in self._papers:
            title = str(paper.get("title", "未命名论文"))
            self.paper_combo.addItem(title if len(title) <= 60 else title[:57] + "…", str(paper.get("id", "")))
        saved_index = self.paper_combo.findData(current_id)
        self.paper_combo.setCurrentIndex(saved_index if saved_index >= 0 else 0)
        self.paper_combo.blockSignals(False)
        self._refresh_journals()

    def _refresh_journals(self) -> None:
        paper_id = str(self.paper_combo.currentData() or "")
        paper = next((item for item in self._papers if str(item.get("id", "")) == paper_id), None)
        journals = paper.get("journals", []) if isinstance(paper, dict) else []
        self.journal_combo.clear()
        for journal in journals:
            name = str(journal.get("name", "未填写期刊"))
            status = str(journal.get("status", "准备投稿"))
            self.journal_combo.addItem(f"{name} · {status}", str(journal.get("id", "")))
        available = bool(journals)
        self.paper_combo.setEnabled(bool(self._papers))
        self.journal_combo.setEnabled(available)
        self.paper_status.setEnabled(available)
        self.paper_note.setEnabled(available)
        self.paper_empty_hint.setText("" if available else "请先在论文行动中心或投稿记录中添加一条期刊经历。")
        if journals:
            current = journals[0]
            self.paper_status.setCurrentText(str(current.get("status", "准备投稿")))

    def _recognize_capture(self) -> None:
        text = self.text_edit.toPlainText().strip()
        if not text:
            self.recognition_hint.setText("请先写下一句话，再识别。")
            return
        self._refresh_papers()
        if self._capture_worker is not None and self._capture_worker.isRunning():
            return
        self.ai_progress.begin("正在检查 AI 配置…")
        if not is_deepseek_ready("quick_capture"):
            self._apply_capture_draft(parse_research_capture_locally(text, self._papers))
            self.ai_progress.complete("本地智能识别完成（未调用 AI）。")
            return
        self.recognize_button.setEnabled(False)
        self.recognize_button.setText("识别中…")
        self.ai_progress.update("DeepSeek 正在识别记录类型与可编辑字段…", 5)
        self.recognition_hint.setText("DeepSeek 正在识别记录类型与可编辑字段…")
        self._capture_worker = CaptureRecognitionThread(text, self._papers, self)
        self._capture_worker.progress.connect(self._capture_progress)
        self._capture_worker.completed.connect(self._apply_capture_draft)
        self._capture_worker.failed.connect(self._recognition_failed)
        self._capture_worker.finished.connect(self._clear_recognition_worker)
        self._capture_worker.start()

    def _apply_capture_draft(self, draft: dict) -> None:
        kind = str(draft.get("kind", "task"))
        index = {"task": 0, "inspiration": 1, "reading": 2, "paper_update": 3}.get(kind, 0)
        self.kind_combo.setCurrentIndex(index)
        content = str(draft.get("content", "")).strip()
        if content:
            self.text_edit.setPlainText(content)
        quadrant = str(draft.get("quadrant", ""))
        task_index = self.task_priority.findData(quadrant)
        if task_index >= 0:
            self.task_priority.setCurrentIndex(task_index)
        reason = str(draft.get("reading_reason", "")).strip()
        if reason:
            self.reading_reason.setPlainText(reason)
        if kind == "paper_update":
            paper_id = str(draft.get("paper_id", ""))
            paper_index = self.paper_combo.findData(paper_id)
            if paper_index >= 0:
                self.paper_combo.setCurrentIndex(paper_index)
                self._refresh_journals()
            journal_id = str(draft.get("journal_id", ""))
            journal_index = self.journal_combo.findData(journal_id)
            if journal_index >= 0:
                self.journal_combo.setCurrentIndex(journal_index)
            status = str(draft.get("status", ""))
            if status in JOURNAL_STATUSES:
                self.paper_status.setCurrentText(status)
            note = str(draft.get("note", "")).strip()
            if note:
                self.paper_note.setPlainText(note)
        mode = str(draft.get("mode", "本地识别"))
        confidence = int(draft.get("confidence", 0) or 0)
        if kind == "paper_update" and not str(draft.get("journal_id", "")):
            self.recognition_hint.setText(f"{mode}：已识别为论文更新，但未能确定期刊，请手动选择后保存。")
        else:
            self.recognition_hint.setText(f"{mode}完成（置信度 {confidence}%）：已填入草稿，保存前可继续修改。")
        self.ai_progress.complete("AI 智能识别完成。")

    def _recognition_failed(self, message: str) -> None:
        fallback = parse_research_capture_locally(self.text_edit.toPlainText(), self._papers)
        self._apply_capture_draft(fallback)
        self.recognition_hint.setText("AI 识别失败，已使用本地识别草稿：" + str(message))
        self.ai_progress.fail("AI 识别失败，已使用本地草稿：" + str(message))

    def _capture_progress(self, message: str, value: int) -> None:
        self.recognition_hint.setText(str(message))
        self.ai_progress.update(str(message), int(value))

    def _clear_recognition_worker(self) -> None:
        if self._capture_worker is not None:
            self._capture_worker.deleteLater()
        self._capture_worker = None
        self.recognize_button.setEnabled(True)
        self.recognize_button.setText("智能识别")

    def _record(self) -> None:
        text = self.text_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "缺少内容", "先写下一句话，再收入收件箱。")
            return
        kind = str(self.kind_combo.currentData())
        today_key = date.today().isoformat()
        if kind == "task":
            add_todo(text, quadrant=str(self.task_priority.currentData() or ""))
            message = "已加入今日任务"
        elif kind == "inspiration":
            add_inspiration(text)
            message = "已保存为科研灵感"
        elif kind == "reading":
            readings = load_readings()
            readings.insert(
                0,
                {
                    "id": uuid4().hex,
                    "title": text,
                    "status": "未阅读",
                    "reason": self.reading_reason.toPlainText().strip(),
                    "created_at": today_key,
                    "updated_at": today_key,
                },
            )
            save_readings(readings)
            message = "已加入待读"
        else:
            paper_id = str(self.paper_combo.currentData() or "")
            journal_id = str(self.journal_combo.currentData() or "")
            paper = next((item for item in self._papers if str(item.get("id", "")) == paper_id), None)
            journal = next(
                (item for item in paper.get("journals", []) if str(item.get("id", "")) == journal_id),
                None,
            ) if paper else None
            if paper is None or journal is None:
                QMessageBox.warning(self, "请选择期刊", "论文更新需要选择一条期刊经历。")
                return
            _append_journal_update(journal, self.paper_status.currentText(), self.paper_note.toPlainText())
            result = save_papers(self._papers)
            if not result.get("saved", False):
                QMessageBox.warning(
                    self,
                    "未保存：存在未来日期",
                    "检测到投稿记录中有晚于今天的日期。请在“论文投稿记录”使用“一键修正为今天”后再保存。",
                )
                return
            if result.get("achievement_count"):
                message = "论文已完整移入成果板块"
            elif result.get("rejection_archive_count"):
                message = "拒稿期刊已移入归档"
            else:
                message = "已写入论文行动记录"
        self.recorded.emit(message)
        self.accept()


class PaperActionCenterDialog(QDialog):
    """A compact decision surface for the next action on one manuscript."""

    changed = Signal()
    edit_paper_requested = Signal(str)

    def __init__(self, paper_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.paper_id = str(paper_id)
        self._papers: list[dict] = []
        self.setWindowTitle("论文行动中心")
        parent_width = parent.width() if parent else 560
        parent_height = parent.height() if parent else 700
        self.setMinimumWidth(370)
        self.setMaximumWidth(max(370, min(620, parent_width - 18)))
        self.resize(max(370, min(540, parent_width - 18)), max(500, min(650, parent_height - 20)))
        self.setStyleSheet(_dialog_style())
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(9)
        self.title_label = QLabel()
        self.title_label.setObjectName("workflowTitle")
        self.title_label.setWordWrap(True)
        root.addWidget(self.title_label)
        self.next_label = QLabel()
        self.next_label.setObjectName("workflowAccent")
        self.next_label.setWordWrap(True)
        root.addWidget(self.next_label)

        self.summary_card = QFrame()
        self.summary_card.setObjectName("workflowCard")
        summary_layout = QVBoxLayout(self.summary_card)
        summary_layout.setContentsMargins(12, 9, 12, 9)
        summary_layout.setSpacing(3)
        self.health_label = QLabel()
        self.health_label.setObjectName("workflowMeta")
        self.health_label.setWordWrap(True)
        summary_layout.addWidget(self.health_label)
        self.ideas_label = QLabel()
        self.ideas_label.setObjectName("workflowMeta")
        self.ideas_label.setWordWrap(True)
        summary_layout.addWidget(self.ideas_label)
        root.addWidget(self.summary_card)

        controls = QFrame()
        controls.setObjectName("workflowCard")
        control_layout = QVBoxLayout(controls)
        control_layout.setContentsMargins(12, 10, 12, 10)
        control_layout.setSpacing(7)
        heading = QLabel("快速更新")
        heading.setObjectName("workflowHeading")
        control_layout.addWidget(heading)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.journal_combo = QComboBox()
        self.journal_combo.currentIndexChanged.connect(self._sync_journal_status)
        form.addRow("当前期刊", self.journal_combo)
        self.status_combo = QComboBox()
        self.status_combo.addItems(JOURNAL_STATUSES)
        form.addRow("更新状态", self.status_combo)
        self.note_edit = QPlainTextEdit()
        self.note_edit.setPlaceholderText("本次来信、决定或下一步（可选）")
        self.note_edit.setFixedHeight(52)
        form.addRow("行动说明", self.note_edit)
        control_layout.addLayout(form)
        quick_actions = QHBoxLayout()
        self.update_button = QPushButton("更新为今天")
        self.update_button.setObjectName("primaryAction")
        self.update_button.clicked.connect(self._save_update)
        quick_actions.addWidget(self.update_button)
        self.task_button = QPushButton("创建今日任务")
        self.task_button.clicked.connect(self._create_task)
        quick_actions.addWidget(self.task_button)
        self.edit_button = QPushButton("完整编辑")
        self.edit_button.clicked.connect(lambda: self.edit_paper_requested.emit(self.paper_id))
        quick_actions.addWidget(self.edit_button)
        control_layout.addLayout(quick_actions)
        root.addWidget(controls)

        history_heading = QLabel("投稿经历与关联灵感")
        history_heading.setObjectName("workflowHeading")
        root.addWidget(history_heading)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_content = QWidget()
        self.history_box = QVBoxLayout(self.history_content)
        self.history_box.setContentsMargins(1, 1, 6, 3)
        self.history_box.setSpacing(6)
        self.history_box.addStretch()
        self.scroll.setWidget(self.history_content)
        root.addWidget(self.scroll, 1)

        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def _paper(self) -> dict | None:
        return next((paper for paper in self._papers if str(paper.get("id", "")) == self.paper_id), None)

    def reload(self) -> None:
        self._papers = load_papers()
        paper = self._paper()
        if paper is None:
            self.title_label.setText("找不到该论文")
            self.next_label.setText("这条记录可能已被删除。")
            self.update_button.setEnabled(False)
            self.task_button.setEnabled(False)
            return
        self.title_label.setText(str(paper.get("title", "未命名论文")))
        self._render_summary(paper)
        self._render_journals(paper)
        self._render_history(paper)

    def _render_summary(self, paper: dict) -> None:
        journals = [item for item in paper.get("journals", []) if isinstance(item, dict)]
        keywords = [item for item in paper.get("keywords", []) if str(item).strip()]
        files = [item for item in paper.get("files", []) if isinstance(item, dict)]
        ideas = [item for item in paper.get("ideas", []) if isinstance(item, dict)]
        missing = []
        if not keywords:
            missing.append("论文关键词")
        if not files:
            missing.append("关联文件")
        self.health_label.setText(
            f"资料完整度：关键词 {len(keywords)} 项 · 文件 {len(files)} 个"
            + (f" · 建议补充{'、'.join(missing)}" if missing else " · 已具备基础资料")
        )
        self.ideas_label.setText(f"关联灵感：{len(ideas)} 条" + (f" · 最近：{ideas[-1].get('text', '')[:40]}" if ideas else ""))
        if not journals:
            self.next_label.setText("下一步：先添加一个候选期刊，论文构想才会进入投稿轨道。")
            return
        candidates: list[tuple[int, str]] = []
        today = date.today()
        for journal in journals:
            status = str(journal.get("status", ""))
            name = str(journal.get("name", "期刊"))
            if status == "修改中":
                due = str(journal.get("revision_due_date", ""))
                if due:
                    try:
                        remain = (date.fromisoformat(due) - today).days
                        candidates.append((100_000 - remain, f"下一步：{name} 回复截止 {due}。"))
                    except ValueError:
                        candidates.append((90_000, f"下一步：处理 {name} 的修改回复。"))
                else:
                    candidates.append((90_000, f"下一步：处理 {name} 的修改回复。"))
            elif status == "准备投稿":
                candidates.append((70_000, f"下一步：确认材料后向 {name} 投稿。"))
            elif status in {"投稿中", "外审中"}:
                try:
                    updated_at = str(journal.get("status_updated_at", "")).strip() or str(journal.get("date", ""))
                    days = (today - date.fromisoformat(updated_at)).days
                except ValueError:
                    days = 0
                candidates.append((days, f"下一步：{name} 目前为{status}，已持续 {max(days, 0)} 天。"))
        self.next_label.setText(max(candidates, default=(0, "当前没有待处理的投稿节点。"))[1])

    def _render_journals(self, paper: dict) -> None:
        journals = [item for item in paper.get("journals", []) if isinstance(item, dict)]
        current = self.journal_combo.currentData()
        self.journal_combo.blockSignals(True)
        self.journal_combo.clear()
        for journal in journals:
            name = str(journal.get("name", "未填写期刊"))
            self.journal_combo.addItem(f"{name} · {journal.get('status', '准备投稿')}", str(journal.get("id", "")))
        position = self.journal_combo.findData(current)
        self.journal_combo.setCurrentIndex(position if position >= 0 else 0)
        self.journal_combo.blockSignals(False)
        enabled = bool(journals)
        self.journal_combo.setEnabled(enabled)
        self.status_combo.setEnabled(enabled)
        self.note_edit.setEnabled(enabled)
        self.update_button.setEnabled(enabled)
        self.task_button.setEnabled(enabled)
        self._sync_journal_status()

    def _current_journal(self) -> dict | None:
        paper = self._paper()
        journal_id = str(self.journal_combo.currentData() or "")
        return next(
            (item for item in paper.get("journals", []) if str(item.get("id", "")) == journal_id),
            None,
        ) if paper else None

    def _sync_journal_status(self) -> None:
        journal = self._current_journal()
        if journal:
            self.status_combo.setCurrentText(str(journal.get("status", "准备投稿")))

    def _render_history(self, paper: dict) -> None:
        while self.history_box.count() > 1:
            child = self.history_box.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        journals = [item for item in paper.get("journals", []) if isinstance(item, dict)]
        if not journals:
            empty = QLabel("还没有期刊经历。点击“完整编辑”添加第一本目标期刊。")
            empty.setObjectName("workflowHint")
            empty.setWordWrap(True)
            self.history_box.insertWidget(0, empty)
        for journal in journals:
            row = QFrame()
            row.setObjectName("workflowCard")
            box = QVBoxLayout(row)
            box.setContentsMargins(10, 8, 10, 8)
            box.setSpacing(2)
            name = QLabel(f"{journal.get('name', '未填写期刊')} · {journal.get('status', '准备投稿')}")
            name.setObjectName("workflowHeading")
            name.setWordWrap(True)
            box.addWidget(name)
            timeline = [item for item in journal.get("timeline", []) if isinstance(item, dict)]
            first = timeline[0].get("date", "") if timeline else journal.get("date", "")
            current_node = str(journal.get("status_updated_at", "")).strip() or str(journal.get("date", ""))
            detail = QLabel(f"首次记录：{first or '未填写'} · 当前节点：{current_node or '未填写'} · 时间线 {len(timeline)} 条")
            detail.setObjectName("workflowMeta")
            detail.setWordWrap(True)
            box.addWidget(detail)
            self.history_box.insertWidget(self.history_box.count() - 1, row)
        for idea in [item for item in paper.get("ideas", []) if isinstance(item, dict)]:
            row = QLabel("○ " + str(idea.get("text", "")))
            row.setObjectName("workflowMeta")
            row.setWordWrap(True)
            self.history_box.insertWidget(self.history_box.count() - 1, row)

    def _save_update(self) -> None:
        journal = self._current_journal()
        if journal is None:
            return
        changed = _append_journal_update(journal, self.status_combo.currentText(), self.note_edit.toPlainText())
        if not changed:
            QMessageBox.information(self, "没有变化", "更改状态或写下行动说明后再保存。")
            return
        result = save_papers(self._papers)
        if not result.get("saved", False):
            QMessageBox.warning(
                self,
                "未保存：存在未来日期",
                "检测到其他投稿记录中有晚于今天的日期。请在“论文投稿记录”使用“一键修正为今天”后再保存。",
            )
            self.reload()
            return
        self.note_edit.clear()
        self.changed.emit()
        lifecycle_message = ""
        if result.get("achievement_count"):
            lifecycle_message = "该论文已完整移入“成果”板块。"
        elif result.get("rejection_archive_count"):
            lifecycle_message = "这条拒稿期刊已移入投稿记录上方的“拒稿归档”。"
        self.reload()
        if lifecycle_message:
            self.next_label.setText(lifecycle_message)

    def _create_task(self) -> None:
        paper = self._paper()
        journal = self._current_journal()
        if paper is None or journal is None:
            return
        status = str(journal.get("status", ""))
        name = str(journal.get("name", "期刊"))
        verb = "回复修改" if status == "修改中" else "准备投稿" if status == "准备投稿" else "跟进状态"
        add_todo(
            f"{verb}：{name}",
            quadrant="urgent_important" if status == "修改中" else "important_not_urgent",
            source={"kind": "paper", "id": str(paper.get("id", "")), "label": str(paper.get("title", ""))},
        )
        self.changed.emit()
        QMessageBox.information(self, "已创建", "已把下一步加入今日任务。")


class LinkInspirationDialog(QDialog):
    """Keep a thought while turning it into a manuscript seed or a paper note."""

    linked = Signal(str, bool)

    def __init__(self, inspiration: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.inspiration = dict(inspiration)
        self._papers = load_papers()
        self.remove_source = False
        self.setWindowTitle("转为论文")
        parent_width = parent.width() if parent else 500
        self.setMinimumWidth(350)
        self.setMaximumWidth(max(350, min(520, parent_width - 24)))
        self.resize(max(350, min(450, parent_width - 24)), 310)
        self.setStyleSheet(_dialog_style())
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)
        title = QLabel("把灵感转为论文")
        title.setObjectName("workflowTitle")
        root.addWidget(title)
        preview = QLabel(str(self.inspiration.get("text", "")))
        preview.setObjectName("workflowHint")
        preview.setWordWrap(True)
        root.addWidget(preview)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.target_combo = QComboBox()
        self.target_combo.addItem("新建论文构想", "__new__")
        for paper in self._papers:
            title_text = str(paper.get("title", "未命名论文"))
            self.target_combo.addItem(title_text if len(title_text) <= 54 else title_text[:51] + "…", str(paper.get("id", "")))
        form.addRow("目标论文", self.target_combo)
        self.keep_checkbox = QCheckBox("保留原灵感便签（推荐）")
        self.keep_checkbox.setChecked(True)
        form.addRow("保留来源", self.keep_checkbox)
        root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("关联论文")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primaryAction")
        buttons.accepted.connect(self._link)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _link(self) -> None:
        target = str(self.target_combo.currentData() or "__new__")
        entry = {
            "id": uuid4().hex,
            "text": str(self.inspiration.get("text", "")).strip(),
            "created_at": date.today().isoformat(),
            "source_inspiration_id": str(self.inspiration.get("id", "")),
        }
        if target == "__new__":
            text = entry["text"]
            title = text[:72] + ("…" if len(text) > 72 else "")
            paper = {
                "id": uuid4().hex,
                "title": title,
                "keywords": [],
                "files": [],
                "ideas": [entry],
                "created_at": date.today().isoformat(),
                "journals": [],
            }
            self._papers.insert(0, paper)
            target = str(paper["id"])
        else:
            paper = next((item for item in self._papers if str(item.get("id", "")) == target), None)
            if paper is None:
                QMessageBox.warning(self, "找不到论文", "请选择一个有效的目标论文。")
                return
            ideas = [item for item in paper.get("ideas", []) if isinstance(item, dict)]
            ideas.append(entry)
            paper["ideas"] = ideas
        result = save_papers(self._papers)
        if not result.get("saved", False):
            QMessageBox.warning(
                self,
                "暂未关联",
                "检测到投稿记录中有晚于今天的日期。请先在“论文投稿记录”一键修正后再关联灵感。",
            )
            return
        self.remove_source = not self.keep_checkbox.isChecked()
        self.linked.emit(target, self.remove_source)
        self.accept()
