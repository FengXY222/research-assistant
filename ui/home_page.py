from __future__ import annotations

from datetime import date
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QFrame,
)

from utils.file_manager import (
    load_achievements,
    load_frontier_data,
    load_inspirations,
    load_papers,
    load_readings,
    load_reminder_state,
    load_todos,
)
from utils.frontier_service import select_daily_recommendations
from utils.submission_reminders import due_ready_submission_reminders, due_submission_reminders


ACTIVE_STATUSES = {"准备投稿", "投稿中", "外审中"}
QUADRANT_ALERTS = {
    "urgent_important": ("red", "紧急且重要"),
    "important_not_urgent": ("yellow", "重要不紧急"),
    "urgent_not_important": ("yellow", "紧急不重要"),
    "not_urgent_not_important": ("green", "不紧急不重要"),
}
ALERT_ORDER = {"red": 0, "yellow": 1, "green": 2}


def _as_iso_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def today_next_actions(
    today: date,
    todos: list[dict],
    papers: list[dict],
    *,
    reminder_state: dict | None = None,
) -> list[dict]:
    """Return the three most actionable existing records for the HOME card.

    The helper deliberately returns routes rather than a second task model: a
    click always enters the user's original todo or multi-journal paper record.
    """
    candidates: dict[str, tuple[int, dict]] = {}

    def add(
        key: str,
        priority: int,
        title: str,
        reason: str,
        route: str,
        *,
        paper_id: str = "",
        journal_id: str = "",
    ) -> None:
        previous = candidates.get(key)
        action = {"title": title, "reason": reason, "route": route, "priority": priority}
        if paper_id:
            action["paper_id"] = paper_id
        if journal_id:
            action["journal_id"] = journal_id
        if previous is None or priority > previous[0]:
            candidates[key] = (priority, action)

    state = reminder_state if isinstance(reminder_state, dict) else {}
    for reminder in due_ready_submission_reminders(papers, state, today):
        journal = str(reminder.get("journal_name", "目标期刊")).strip() or "目标期刊"
        title = str(reminder.get("paper_title", "未命名论文")).strip() or "未命名论文"
        add(
            f"journal:{reminder.get('paper_title', '')}:{journal}",
            860,
            f"{journal} · {title}",
            "今天可投稿",
            "papers",
            paper_id=str(reminder.get("paper_id", "")),
            journal_id=str(reminder.get("journal_id", "")),
        )

    for paper in papers:
        paper_title = str(paper.get("title", "未命名论文")).strip() or "未命名论文"
        paper_id = str(paper.get("id", paper_title))
        journals = paper.get("journals", []) if isinstance(paper.get("journals"), list) else []
        for journal in journals:
            if not isinstance(journal, dict):
                continue
            journal_name = str(journal.get("name", "未填写期刊")).strip() or "未填写期刊"
            journal_id = str(journal.get("id", journal_name))
            key = f"journal:{paper_id}:{journal_id}"
            status = str(journal.get("status", "")).strip()
            deadline = _as_iso_date(journal.get("revision_due_date"))
            if status == "修改中" and deadline is not None:
                remaining = (deadline - today).days
                if remaining < 0:
                    add(
                        key, 1000 + abs(remaining), f"{journal_name} · {paper_title}", f"回复逾期 {abs(remaining)} 天", "papers",
                        paper_id=paper_id, journal_id=journal_id,
                    )
                elif remaining <= 7:
                    timing = "今天截止" if remaining == 0 else f"回复剩余 {remaining} 天"
                    add(
                        key, 950 - remaining, f"{journal_name} · {paper_title}", timing, "papers",
                        paper_id=paper_id, journal_id=journal_id,
                    )

    for reminder in due_submission_reminders(papers, state, today):
        key = f"journal:{reminder.get('paper_id', '')}:{reminder.get('journal_id', '')}"
        journal = str(reminder.get("journal_name", "目标期刊")).strip() or "目标期刊"
        paper_title = str(reminder.get("paper_title", "未命名论文")).strip() or "未命名论文"
        add(
            key,
            700 + int(reminder.get("days", 0) or 0),
            f"{journal} · {paper_title}",
            f"{reminder.get('status', '状态')} 已 {reminder.get('days', 0)} 天未更新",
            "papers",
            paper_id=str(reminder.get("paper_id", "")),
            journal_id=str(reminder.get("journal_id", "")),
        )

    for paper in papers:
        paper_title = str(paper.get("title", "未命名论文")).strip() or "未命名论文"
        paper_id = str(paper.get("id", paper_title))
        journals = paper.get("journals", []) if isinstance(paper.get("journals"), list) else []
        for journal in journals:
            if not isinstance(journal, dict):
                continue
            status = str(journal.get("status", "")).strip()
            if status not in ACTIVE_STATUSES:
                continue
            updated = _as_iso_date(journal.get("status_updated_at")) or _as_iso_date(journal.get("date"))
            if updated is None:
                continue
            days = (today - updated).days
            if days < 30:
                continue
            journal_name = str(journal.get("name", "未填写期刊")).strip() or "未填写期刊"
            journal_id = str(journal.get("id", journal_name))
            add(
                f"journal:{paper_id}:{journal_id}",
                600 + days,
                f"{journal_name} · {paper_title}",
                f"{status} 已持续 {days} 天",
                "papers",
                paper_id=paper_id,
                journal_id=journal_id,
            )

    for task in todos:
        if not isinstance(task, dict) or task.get("done"):
            continue
        title = str(task.get("title", "未命名任务")).strip() or "未命名任务"
        task_id = str(task.get("id", title))
        quadrant = str(task.get("quadrant", "")).strip()
        end = _as_iso_date(task.get("end_date"))
        if end is not None:
            remaining = (end - today).days
            if remaining <= 3:
                timing = "今天截止" if remaining == 0 else f"剩余 {remaining} 天" if remaining > 0 else f"已逾期 {abs(remaining)} 天"
                boost = 25 if quadrant == "urgent_important" else 0
                add(f"todo:{task_id}", 820 + boost - remaining, title, timing, "todo")
        elif quadrant == "urgent_important":
            add(f"todo:{task_id}", 780, title, "紧急且重要，尚未安排日期", "todo")

    return [
        action
        for _priority, action in sorted(
            candidates.values(),
            key=lambda value: (-value[0], value[1]["route"], value[1]["title"].casefold()),
        )[:3]
    ]


class ElidedActionButton(QPushButton):
    """Keep an action's time-sensitive reason visible in a narrow widget."""

    def __init__(self, full_text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = str(full_text)
        self.setToolTip(self._full_text)
        self._refresh_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self) -> None:
        available = max(1, self.contentsRect().width() - 16)
        reason, separator, title = self._full_text.partition(" · ")
        metrics = QFontMetrics(self.font())
        if separator:
            prefix_width = metrics.horizontalAdvance(reason + separator)
            ellipsis_width = metrics.horizontalAdvance("…")
            if prefix_width + ellipsis_width <= available:
                rendered_title = metrics.elidedText(title, Qt.TextElideMode.ElideRight, available - prefix_width)
                rendered = reason + separator + rendered_title
            else:
                rendered = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, available)
        else:
            rendered = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, available)
        if self.text() != rendered:
            super().setText(rendered)


class NavigableHomeCard(QFrame):
    """A lightweight overview card with a direct double-click destination."""

    opened = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # A QFrame inside a transparent widget-mode window otherwise may not
        # paint its stylesheet background and bottom border reliably.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseDoubleClickEvent(self, event) -> None:
        self.opened.emit()
        event.accept()


class HomePage(QWidget):
    open_todo = Signal()
    open_papers = Signal()
    open_paper_journal = Signal(str, str)
    open_notes = Signal()
    open_frontier = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("homePage")
        self._preview_adapt_pending = False
        self._preview_inspirations: list[dict] = []
        self._preview_readings: list[dict] = []
        self._metric_cards_stacked: bool | None = None
        self._submission_cards_stacked: bool | None = None
        self._notes_stacked: bool = True
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)

        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("科研概览")
        title.setObjectName("pageTitle")
        self.date_label = QLabel()
        self.date_label.setObjectName("dateLabel")
        title_box.addWidget(title)
        title_box.addWidget(self.date_label)
        heading.addLayout(title_box)
        heading.addStretch()
        refresh = QPushButton("刷新")
        refresh.setObjectName("subtleButton")
        refresh.setToolTip("刷新首页")
        refresh.clicked.connect(self.refresh)
        heading.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignBottom)
        root.addLayout(heading)

        # HOME deliberately owns its own scroll canvas.  In widget mode this
        # keeps the main work surfaces legible instead of compressing every
        # card to fit an arbitrary window height.
        self.dashboard_scroll = QScrollArea()
        self.dashboard_scroll.setObjectName("homeDashboardScroll")
        self.dashboard_scroll.setWidgetResizable(True)
        self.dashboard_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.dashboard_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.dashboard_canvas = QWidget()
        self.dashboard_canvas.setObjectName("homeDashboardCanvas")
        self.dashboard_canvas.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.dashboard_layout = QVBoxLayout(self.dashboard_canvas)
        self.dashboard_layout.setContentsMargins(0, 0, 0, 4)
        self.dashboard_layout.setSpacing(10)
        self.dashboard_scroll.setWidget(self.dashboard_canvas)
        root.addWidget(self.dashboard_scroll, 1)

        # The opening band is intentionally asymmetric: concrete work owns the
        # visual focus, while paper state stays a compact, scan-friendly rail.
        # This keeps a busy task list from turning the status area into a tall,
        # mostly empty card.
        self.metrics_container = QWidget()
        self.metrics_container.setObjectName("homeFocusZone")
        self.metrics_layout = QGridLayout(self.metrics_container)
        self.metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.metrics_layout.setHorizontalSpacing(10)
        self.metrics_layout.setVerticalSpacing(10)
        self.todo_card = self._make_metric_card("今日任务", "—", "查看待办", role="today")
        self.paper_card = self._make_metric_card("论文状态", "—", "查看投稿", role="paper")
        self.todo_card[2].clicked.connect(self.open_todo.emit)
        self.paper_card[2].clicked.connect(self.open_papers.emit)
        self.todo_card[0].opened.connect(self.open_todo.emit)
        self.paper_card[0].opened.connect(self.open_papers.emit)
        self.dashboard_layout.addWidget(self.metrics_container)

        self.today_task_preview = QLabel()
        self.today_task_preview.setObjectName("todayTaskPreview")
        self.today_task_preview.setWordWrap(True)
        self.today_task_preview.setMinimumWidth(0)
        self.today_task_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.today_task_preview.setVisible(False)
        self.todo_card[0].layout().insertWidget(2, self.today_task_preview)

        self.submission_container = QWidget()
        self.submission_container.setObjectName("homeSubmissionZone")
        # Submission action and observation answer consecutive questions. Keep
        # them in one vertical stream instead of forcing two competing cards
        # into the same row on wide windows.
        self.submission_layout = QVBoxLayout(self.submission_container)
        self.submission_layout.setContentsMargins(0, 0, 0, 0)
        self.submission_layout.setSpacing(8)

        self.next_card = QFrame()
        self.next_card.setObjectName("overviewCard")
        self.next_card.setProperty("homeRole", "next")
        self.next_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.next_card.setMinimumHeight(108)
        self.next_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        next_root = QVBoxLayout(self.next_card)
        next_root.setContentsMargins(14, 11, 14, 11)
        next_root.setSpacing(5)
        next_heading = QHBoxLayout()
        next_title = QLabel("投稿推进")
        next_title.setObjectName("cardHeading")
        next_heading.addWidget(next_title)
        next_heading.addStretch(1)
        next_hint = QLabel("需要决策的下一步")
        next_hint.setObjectName("cardHint")
        next_heading.addWidget(next_hint)
        next_root.addLayout(next_heading)
        self.next_actions_box = QVBoxLayout()
        self.next_actions_box.setContentsMargins(0, 0, 0, 0)
        self.next_actions_box.setSpacing(3)
        next_root.addLayout(self.next_actions_box)
        self.next_card.hide()

        self.frontier_card = NavigableHomeCard()
        self.frontier_card.setObjectName("overviewCard")
        self.frontier_card.setProperty("homeRole", "frontier")
        self.frontier_card.setToolTip("双击打开每日前沿")
        self.frontier_card.opened.connect(self.open_frontier.emit)
        self.frontier_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.frontier_card.setMinimumHeight(82)
        frontier_root = QVBoxLayout(self.frontier_card)
        frontier_root.setContentsMargins(14, 11, 14, 11)
        frontier_root.setSpacing(4)
        frontier_title = QLabel("今日科研简报")
        frontier_title.setObjectName("cardHeading")
        frontier_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.frontier_brief_label = QLabel()
        self.frontier_brief_label.setObjectName("frontierBrief")
        self.frontier_brief_label.setWordWrap(True)
        self.frontier_brief_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        frontier_root.addWidget(frontier_title)
        frontier_root.addWidget(self.frontier_brief_label)

        self.recent_card = NavigableHomeCard()
        self.recent_card.setObjectName("overviewCard")
        self.recent_card.setProperty("homeRole", "observation")
        self.recent_card.setToolTip("双击打开论文投稿记录")
        self.recent_card.opened.connect(self.open_papers.emit)
        self.recent_card.setMinimumHeight(0)
        self.recent_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        recent_root = QVBoxLayout(self.recent_card)
        recent_root.setContentsMargins(14, 11, 14, 11)
        recent_root.setSpacing(5)
        recent_title = QLabel("投稿观察")
        recent_title.setObjectName("cardHeading")
        recent_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        recent_root.addWidget(recent_title)
        self.recent_scroll = QScrollArea()
        self.recent_scroll.setWidgetResizable(True)
        self.recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.recent_scroll.setFixedHeight(122)
        recent_content = QWidget()
        recent_content.setObjectName("recentContent")
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(1)
        self.recent_box.setContentsMargins(0, 0, 4, 0)
        self.recent_box.setAlignment(Qt.AlignmentFlag.AlignTop)
        recent_content.setLayout(self.recent_box)
        self.recent_scroll.setWidget(recent_content)
        recent_root.addWidget(self.recent_scroll)
        self.submission_layout.addWidget(self.next_card)
        self.submission_layout.addWidget(self.recent_card)
        self.dashboard_layout.addWidget(self.submission_container)
        self.dashboard_layout.addWidget(self.frontier_card)

        self.preview_row = QWidget()
        self.preview_row.setObjectName("homeKnowledgeZone")
        self.preview_grid = QGridLayout(self.preview_row)
        self.preview_grid.setContentsMargins(0, 0, 0, 0)
        self.preview_grid.setHorizontalSpacing(10)
        self.preview_grid.setVerticalSpacing(10)
        # Keep the public attribute used by older integration code; it is now
        # a grid so narrow widgets can stack the two knowledge previews.
        self.bottom_layout = self.preview_grid
        (
            self.inspiration_preview,
            self.inspiration_box,
            self.inspiration_content,
        ) = self._make_notes_preview("灵感便签", "科研想法")
        (
            self.reading_preview,
            self.reading_box,
            self.reading_content,
        ) = self._make_notes_preview("值得阅读", "待读清单")
        self.inspiration_preview.setToolTip("双击打开灵感与待读")
        self.reading_preview.setToolTip("双击打开灵感与待读")
        self.inspiration_preview.opened.connect(self.open_notes.emit)
        self.reading_preview.opened.connect(self.open_notes.emit)
        self.dashboard_layout.addWidget(self.preview_row)
        self.dashboard_layout.addStretch(1)
        self._layout_metric_cards(force=True)
        self._layout_submission_cards(force=True)
        self._layout_note_previews(force=True)

    def _make_metric_card(self, title: str, value: str, button_text: str, *, role: str):
        card = NavigableHomeCard()
        card.setObjectName("overviewCard")
        visual_role = "focus" if role == "today" else "paper-rail"
        visual_title = "今日任务" if role == "today" else title
        card.setProperty("homeRole", visual_role)
        card.setToolTip(f"双击打开{title}")
        card.setMinimumWidth(0)
        card.setMinimumHeight(156 if role == "today" else 106)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)
        header_widget = QWidget()
        header = QHBoxLayout(header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        heading = QLabel(visual_title)
        heading.setObjectName("cardHeading")
        heading.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        button = QPushButton(button_text)
        button.setObjectName("cardLink")
        button.setMinimumHeight(24)
        button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(button)
        value_label = QLabel(value)
        value_label.setObjectName("metricValue")
        value_label.setMinimumWidth(0)
        value_label.setMinimumHeight(22)
        value_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        value_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(header_widget)
        layout.addWidget(value_label)
        return card, value_label, button

    def _layout_metric_cards(self, *, force: bool = False) -> None:
        """Lay out the top workbench as focus plus a compact status rail."""
        if not hasattr(self, "metrics_layout"):
            return
        stacked = self.contentsRect().width() > 0 and self.contentsRect().width() < 820
        if not force and stacked == self._metric_cards_stacked:
            return
        self._metric_cards_stacked = stacked
        todo, paper = self.todo_card[0], self.paper_card[0]
        self.metrics_layout.removeWidget(todo)
        self.metrics_layout.removeWidget(paper)
        if stacked:
            paper.setMaximumWidth(16777215)
            self.metrics_layout.addWidget(todo, 0, 0)
            self.metrics_layout.addWidget(paper, 1, 0)
            self.metrics_layout.setColumnStretch(0, 1)
            self.metrics_layout.setColumnStretch(1, 0)
            self.metrics_layout.setRowStretch(0, 0)
            self.metrics_layout.setRowStretch(1, 0)
        else:
            paper.setMaximumWidth(352)
            self.metrics_layout.addWidget(todo, 0, 0)
            self.metrics_layout.addWidget(paper, 0, 1, alignment=Qt.AlignmentFlag.AlignTop)
            self.metrics_layout.setColumnStretch(0, 7)
            self.metrics_layout.setColumnStretch(1, 3)
            self.metrics_layout.setRowStretch(0, 0)
        self.metrics_container.updateGeometry()

    def _layout_submission_cards(self, *, force: bool = False) -> None:
        """Keep submission work in one readable, full-width timeline."""
        if not hasattr(self, "submission_layout"):
            return
        next_visible = not self.next_card.isHidden()
        recent_visible = not self.recent_card.isHidden()
        layout_state = (next_visible, recent_visible)
        if not force and layout_state == self._submission_cards_stacked:
            return
        self._submission_cards_stacked = layout_state
        self.submission_container.updateGeometry()

    def _layout_note_previews(self, *, force: bool = False) -> None:
        """Give the knowledge cards a full reading width in small widgets."""
        if not hasattr(self, "preview_grid"):
            return
        stacked = self.contentsRect().width() <= 620
        if not force and stacked == self._notes_stacked:
            return
        self._notes_stacked = stacked
        self.preview_grid.removeWidget(self.inspiration_preview)
        self.preview_grid.removeWidget(self.reading_preview)
        if stacked:
            self.preview_grid.addWidget(self.inspiration_preview, 0, 0)
            self.preview_grid.addWidget(self.reading_preview, 1, 0)
            self.preview_grid.setColumnStretch(0, 1)
            self.preview_grid.setColumnStretch(1, 0)
            self.preview_grid.setRowStretch(0, 0)
            self.preview_grid.setRowStretch(1, 0)
        else:
            self.preview_grid.addWidget(self.inspiration_preview, 0, 0)
            self.preview_grid.addWidget(self.reading_preview, 0, 1)
            self.preview_grid.setColumnStretch(0, 1)
            self.preview_grid.setColumnStretch(1, 1)
            self.preview_grid.setRowStretch(0, 0)
            self.preview_grid.setRowStretch(1, 0)
        self.preview_row.updateGeometry()

    def _make_notes_preview(self, title: str, hint_text: str) -> tuple[QFrame, QVBoxLayout, QWidget]:
        card = NavigableHomeCard()
        card.setObjectName("overviewCard")
        card.setProperty("homeRole", "knowledge")
        card.setMinimumHeight(154)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        root = QVBoxLayout(card)
        root.setContentsMargins(13, 10, 13, 10)
        root.setSpacing(4)

        header_widget = QWidget()
        header_widget.setFixedHeight(18)
        header_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header = QHBoxLayout(header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("cardHeading")
        header.addWidget(heading)
        header.addStretch()
        hint = QLabel(hint_text)
        hint.setObjectName("cardHint")
        header.addWidget(hint)
        root.addWidget(header_widget)

        content_widget = QWidget()
        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(2)
        content.setAlignment(Qt.AlignmentFlag.AlignTop)
        content_widget.setLayout(content)
        # A scroll viewport clips overflowing note lines instead of painting
        # them over the "查看与编辑" button below.  Scrolling stays disabled;
        # the "还有内容未显示" marker already tells the user more exists.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        scroll.setWidget(content_widget)
        root.addWidget(scroll, 1)
        open_button = QPushButton("查看与编辑")
        open_button.setObjectName("cardLink")
        open_button.setMinimumHeight(24)
        open_button.clicked.connect(self.open_notes.emit)
        root.addWidget(open_button, alignment=Qt.AlignmentFlag.AlignLeft)
        return card, content, scroll

    def refresh(self) -> None:
        today = date.today()
        self.date_label.setText(f"{today.year}年{today.month}月{today.day}日")
        todos = load_todos(today)
        done = sum(1 for item in todos if item.get("done"))
        self.todo_card[1].setText(f"{done} / {len(todos)} 已完成")
        self._render_today_tasks(todos)

        papers = load_papers()
        active = self._paper_count(papers, ACTIVE_STATUSES)
        modifying = self._paper_count(papers, {"修改中"})
        published = sum(
            1
            for item in load_achievements()
            if str(item.get("category", "")) == "论文" and str(item.get("status", "")) == "已发表"
        )
        self.paper_card[1].setText(f"投稿中：{active}篇\n修改中：{modifying}篇\n已发表：{published}篇")
        self._refresh_frontier_brief()
        visible_actions = self._render_next_actions(
            today_next_actions(today, todos, papers, reminder_state=load_reminder_state())
        )
        action_journals = {
            str(item.get("journal_id", "")).strip()
            for item in visible_actions
            if str(item.get("route", "")) == "papers" and str(item.get("journal_id", "")).strip()
        }
        self._render_recent_nodes(today, todos, papers, exclude_journal_ids=action_journals)
        self._preview_inspirations = list(reversed(load_inspirations()))[:8]
        self._preview_readings = load_readings()[:8]
        self._request_preview_adaptation()

    def _render_next_actions(self, actions: list[dict]) -> list[dict]:
        while self.next_actions_box.count():
            child = self.next_actions_box.takeAt(0)
            widget = child.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        # Concrete tasks already live in the 今日任务 card.  Keep this compact
        # panel for paper/revision actions so HOME does not repeat the same task
        # in three different places.
        visible_actions = [
            item for item in actions
            if isinstance(item, dict)
            and str(item.get("route", "")) != "todo"
            # A long-running / unupdated submission is an observation, not a
            # second action.  It remains once in 投稿观察; this card keeps only
            # decisions with a concrete next step such as a deadline.
            and "未更新" not in str(item.get("reason", ""))
            and "已持续" not in str(item.get("reason", ""))
        ][:3]
        self.next_card.setHidden(not visible_actions)
        self.submission_container.updateGeometry()
        for action in visible_actions:
            title = str(action.get("title", "待处理事项")).strip() or "待处理事项"
            reason = str(action.get("reason", "")).strip()
            # In a narrow widget the action reason is the time-sensitive part.
            # Lead with it and let Qt elide only the long paper/task title.
            button = ElidedActionButton(f"{reason} · {title}" if reason else title)
            button.setObjectName("nextActionButton")
            button.setToolTip(f"{reason} · {title}" if reason else title)
            button.clicked.connect(lambda _checked=False, value=dict(action): self._open_next_action(value))
            self.next_actions_box.addWidget(button)
        self._layout_submission_cards()
        return visible_actions

    def _open_next_action(self, action: dict | str) -> None:
        if isinstance(action, dict):
            route = str(action.get("route", "home"))
        else:
            route = str(action)
        if route == "todo":
            self.open_todo.emit()
        elif route == "papers":
            paper_id = str(action.get("paper_id", "")) if isinstance(action, dict) else ""
            journal_id = str(action.get("journal_id", "")) if isinstance(action, dict) else ""
            if paper_id and journal_id:
                self.open_paper_journal.emit(paper_id, journal_id)
            else:
                self.open_papers.emit()
        elif route == "notes":
            self.open_notes.emit()
        elif route == "frontier":
            self.open_frontier.emit()

    def _render_today_tasks(self, todos: list[dict]) -> None:
        unfinished = [item for item in todos if isinstance(item, dict) and not item.get("done")]
        if not unfinished:
            self.today_task_preview.setText("今天没有待完成任务")
            self.today_task_preview.setVisible(True)
            self.todo_card[0].updateGeometry()
            return
        lines: list[str] = []
        for item in unfinished[:3]:
            title = str(item.get("title", "未命名任务")).strip() or "未命名任务"
            quadrant = str(item.get("quadrant", "")).strip()
            priority = QUADRANT_ALERTS.get(quadrant, ("", ""))[1]
            lines.append(f"• {title}" + (f" · {priority}" if priority else ""))
        extra = len(unfinished) - len(lines)
        if extra > 0:
            lines.append(f"还有 {extra} 项待办")
        self.today_task_preview.setText("\n".join(lines))
        self.today_task_preview.setVisible(True)
        self.todo_card[0].updateGeometry()
        self.metrics_container.updateGeometry()

    def _refresh_frontier_brief(self) -> None:
        data = load_frontier_data()
        top = select_daily_recommendations(data.get("items", []), data.get("profile", {}))
        if not top:
            self.frontier_brief_label.setText("今日暂无新的匹配论文；可前往“每日前沿”检查更新。")
            return
        journals = [str(item.get("journal", "")).strip() for item in top if str(item.get("priority", "")) in {"必看", "关注"}]
        priority_text = f" · 优先期刊 {len(journals)} 篇" if journals else ""
        scores = [int(item.get("score", 0)) for item in top]
        score_text = f" · 最高 {max(scores)} 分" if scores else ""
        self.frontier_brief_label.setText(f"今日筛出 {len(top)} 篇研究，已按总分排序{score_text}{priority_text}。")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_metric_cards()
        self._layout_submission_cards()
        self._layout_note_previews()
        self._request_preview_adaptation()

    def _request_preview_adaptation(self) -> None:
        """Coalesce resize/data updates until the two preview cards have a real size."""
        if self._preview_adapt_pending:
            return
        self._preview_adapt_pending = True
        QTimer.singleShot(0, self._run_preview_adaptation)

    def _run_preview_adaptation(self) -> None:
        """Ignore a queued layout pass after this page has already closed."""
        try:
            self._adapt_note_previews()
        except RuntimeError:
            return

    def _adapt_note_previews(self) -> None:
        self._preview_adapt_pending = False
        if not hasattr(self, "preview_row"):
            return

        available_width = self.preview_row.contentsRect().width() - self.preview_grid.horizontalSpacing()
        if available_width <= 0:
            return
        line_capacity = self._preview_line_capacity()
        if self._notes_stacked:
            stacked_width = max(40, self.preview_row.contentsRect().width() - 26)
            self._render_preview_items(
                self.inspiration_box,
                self._preview_inspirations,
                stacked_width,
                line_capacity,
                text_key="text",
                empty_text="写下一条研究想法",
            )
            self._render_preview_items(
                self.reading_box,
                self._preview_readings,
                stacked_width,
                line_capacity,
                text_key="title",
                empty_text="保存以后想读的论文",
                reading_marks=True,
            )
            return
        best_ratio = 50
        best_score: tuple[float, int] | None = None
        # Keep both cards recognisable as a pair.  The previous 35%–65% range
        # could make one preview feel like a narrow side note on small widgets.
        # Text still decides the split, but neither panel can dominate the row.
        for ratio in range(42, 59):
            left_width = max(1, int(available_width * ratio / 100))
            right_width = max(1, available_width - left_width)
            inspiration_loss = self._preview_loss(self._preview_inspirations, left_width - 26, "text")
            reading_loss = self._preview_loss(self._preview_readings, right_width - 26, "title")
            inspiration_loss = max(inspiration_loss - line_capacity, 0)
            reading_loss = max(reading_loss - line_capacity, 0)
            # The primary goal is balanced information loss.  A small secondary
            # cost keeps the layout visually stable when both choices are equal.
            score = (abs(inspiration_loss - reading_loss) * 20 + inspiration_loss + reading_loss, abs(ratio - 50))
            if best_score is None or score < best_score:
                best_score = score
                best_ratio = ratio

        self.preview_grid.setColumnStretch(0, best_ratio)
        self.preview_grid.setColumnStretch(1, 100 - best_ratio)
        left_width = max(1, int(available_width * best_ratio / 100))
        right_width = max(1, available_width - left_width)
        self._render_preview_items(
            self.inspiration_box,
            self._preview_inspirations,
            left_width - 26,
            line_capacity,
            text_key="text",
            empty_text="写下一条研究想法",
        )
        self._render_preview_items(
            self.reading_box,
            self._preview_readings,
            right_width - 26,
            line_capacity,
            text_key="title",
            empty_text="保存以后想读的论文",
            reading_marks=True,
        )

    def _preview_line_capacity(self) -> int:
        line_height = max(QFontMetrics(self.font()).lineSpacing(), 14)
        content_height = min(
            self.inspiration_content.viewport().height(),
            self.reading_content.viewport().height(),
        )
        if content_height <= 0:
            content_height = max(28, self.height() // 6)
        return max(1, min(12, content_height // (line_height + 2)))

    def _preview_loss(self, items: list[dict], width: int, text_key: str) -> int:
        if not items:
            return 0
        return sum(
            max(1, len(self._wrap_preview_lines(str(item.get(text_key, "")), width)))
            for item in items
        )

    def _render_preview_items(
        self,
        layout: QVBoxLayout,
        items: list[dict],
        width: int,
        line_budget: int,
        *,
        text_key: str,
        empty_text: str,
        reading_marks: bool = False,
    ) -> None:
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        if not items:
            empty = QLabel(empty_text)
            empty.setObjectName("cardHint")
            layout.addWidget(empty)
            return

        required_lines = sum(
            max(1, len(self._wrap_preview_lines(str(item.get(text_key, "")).strip(), max(40, width))))
            for item in items
            if str(item.get(text_key, "")).strip()
        )
        has_overflow = required_lines > line_budget
        # When content is clipped, reserve a final line to make the loss of
        # information explicit instead of silently cutting off the next note.
        remaining = max(1, line_budget - 1 if has_overflow and line_budget >= 2 else line_budget)
        line_height = max(QFontMetrics(self.font()).lineSpacing(), 14)
        for item in items:
            if remaining <= 0:
                break
            raw_text = str(item.get(text_key, "")).strip()
            if not raw_text:
                continue
            prefix = "✓  " if reading_marks and item.get("status") == "已阅读" else "○  "
            wrapped = self._wrap_preview_lines(raw_text, max(40, width - QFontMetrics(self.font()).horizontalAdvance(prefix)))
            visible_count = min(len(wrapped), remaining)
            visible_lines = list(wrapped[:visible_count])
            if visible_count < len(wrapped):
                visible_lines[-1] = QFontMetrics(self.font()).elidedText(
                    f"{visible_lines[-1]}…", Qt.TextElideMode.ElideRight, max(40, width - QFontMetrics(self.font()).horizontalAdvance(prefix))
                )
            rendered = f"{prefix}{visible_lines[0]}"
            if len(visible_lines) > 1:
                rendered += "\n" + "\n".join(f"   {line}" for line in visible_lines[1:])
            label = QLabel(rendered)
            label.setObjectName("inspirationText")
            label.setWordWrap(False)
            label.setFixedHeight(max(1, visible_count) * line_height)
            layout.addWidget(label)
            remaining -= visible_count

        if has_overflow:
            more = QLabel("还有内容未显示 · 点击查看全部")
            more.setObjectName("cardHint")
            more.setFixedHeight(line_height)
            layout.addWidget(more)

    def _wrap_preview_lines(self, text: str, width: int) -> list[str]:
        """Character-aware wrapping keeps Chinese and long English titles compact."""
        text = " ".join(text.split())
        if not text:
            return [""]
        metrics = QFontMetrics(self.font())
        width = max(40, width)
        lines: list[str] = []
        current = ""
        last_break = -1
        for char in text:
            candidate = current + char
            if metrics.horizontalAdvance(candidate) <= width or not current:
                current = candidate
                if char.isspace():
                    last_break = len(current)
                continue
            if last_break > 0:
                lines.append(current[:last_break].rstrip())
                current = current[last_break:].lstrip() + char
            else:
                lines.append(current)
                current = char
            last_break = len(current) if char.isspace() else -1
        if current:
            lines.append(current.rstrip())
        return [line for line in lines if line] or [""]

    @staticmethod
    def _paper_count(papers: list[dict], statuses: set[str]) -> int:
        return sum(
            1
            for paper in papers
            if any(str(journal.get("status", "")) in statuses for journal in paper.get("journals", []))
        )

    def _render_recent_nodes(
        self,
        today: date,
        todos: list[dict],
        papers: list[dict],
        *,
        exclude_journal_ids: set[str] | None = None,
    ) -> None:
        while self.recent_box.count():
            child = self.recent_box.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        excluded = {str(item).strip() for item in (exclude_journal_ids or set()) if str(item).strip()}
        paper_nodes: list[tuple[str, int, str]] = []
        for paper in papers:
            most_urgent_for_paper: tuple[str, int, str] | None = None
            for journal in paper.get("journals", []):
                if str(journal.get("id", "")).strip() in excluded:
                    continue
                status = str(journal.get("status", ""))
                record_date = str(journal.get("status_updated_at", "")).strip() or str(journal.get("date", ""))
                if status in {"已接收", "已发表", "拒稿"}:
                    continue
                if record_date:
                    try:
                        days = max((today - date.fromisoformat(record_date)).days, 0)
                    except ValueError:
                        days = 0
                else:
                    days = 0
                priority = days
                deadline_text = ""
                if status == "修改中":
                    severity = "red"
                    due_date = str(journal.get("revision_due_date", ""))
                    if due_date:
                        try:
                            remaining = (date.fromisoformat(due_date) - today).days
                        except ValueError:
                            remaining = None
                        if remaining is not None:
                            if remaining < 0:
                                priority = 100000 + abs(remaining)
                                deadline_text = f" · 回复逾期 {abs(remaining)} 天"
                            else:
                                priority = 50000 - remaining
                                deadline_text = " · 截止今天" if remaining == 0 else f" · 回复剩余 {remaining} 天"
                elif status in ACTIVE_STATUSES:
                    severity = "red" if days > 60 else "yellow"
                else:
                    continue
                short_status = "外审" if status == "外审中" else status
                duration = f"已持续{days}天" if record_date else "未填写状态更新时间"
                candidate = (severity, priority, f"{journal.get('name', '期刊')} {short_status} · {duration}{deadline_text}")
                if most_urgent_for_paper is None or self._node_is_more_urgent(candidate, most_urgent_for_paper):
                    most_urgent_for_paper = candidate
            if most_urgent_for_paper:
                paper_nodes.append(most_urgent_for_paper)

        # Tasks are shown concretely in 今日任务 and are deliberately excluded
        # here.  投稿观察 should answer a different question: which submission
        # history needs watching, rather than repeating the same to-do deadline.
        all_nodes = paper_nodes
        red_nodes = sorted(
            (node for node in all_nodes if node[0] == "red"),
            key=lambda value: value[1],
            reverse=True,
        )
        yellow_nodes = sorted(
            (node for node in all_nodes if node[0] == "yellow"),
            key=lambda value: value[1],
            reverse=True,
        )
        green_nodes = sorted(
            (node for node in all_nodes if node[0] == "green"),
            key=lambda value: value[1],
            reverse=True,
        )
        # All red alerts are always shown. If fewer than five are red, fill the
        # remaining places with yellow alerts first, then green ones if needed.
        target_count = min(5, len(all_nodes))
        nodes = list(red_nodes)
        if len(nodes) < target_count:
            nodes.extend(yellow_nodes[: target_count - len(nodes)])
        if len(nodes) < target_count:
            nodes.extend(green_nodes[: target_count - len(nodes)])
        if not nodes:
            # If this panel became empty only because its journal is already
            # the concrete item in 待推进, remove the redundant shell rather
            # than presenting the same submission twice with an empty state.
            if excluded:
                self.recent_card.hide()
                self.submission_container.updateGeometry()
                return
            self.recent_card.show()
            # A fixed 122px list made an empty dashboard look unfinished.
            # Keep just one compact line until there is something actionable.
            self.recent_scroll.setFixedHeight(42)
            label = QLabel("暂无需要特别关注的节点")
            label.setObjectName("nodeText")
            self.recent_box.addWidget(label)
            self.submission_container.updateGeometry()
            return
        self.recent_card.show()
        # Observation is a compact status surface.  It grows for several
        # wrapped nodes but does not keep a five-row blank area for one line.
        line_height = max(QFontMetrics(self.font()).lineSpacing(), 14)
        estimated_lines = sum(max(1, min(2, (len(text) + 45) // 46)) for _severity, _value, text in nodes)
        self.recent_scroll.setFixedHeight(min(122, max(42, 12 + estimated_lines * (line_height + 2))))
        for severity, _value, text in nodes:
            label = QLabel(f"●  {text}")
            label.setObjectName(f"node{severity.title()}")
            label.setWordWrap(True)
            self.recent_box.addWidget(label)
        self.submission_container.updateGeometry()

    @staticmethod
    def _node_is_more_urgent(candidate: tuple[str, int, str], current: tuple[str, int, str]) -> bool:
        candidate_rank = ALERT_ORDER[candidate[0]]
        current_rank = ALERT_ORDER[current[0]]
        return candidate_rank < current_rank or (candidate_rank == current_rank and candidate[1] > current[1])

    @staticmethod
    def _deadline_alert(remaining_days: int) -> str:
        if remaining_days <= 1:
            return "red"
        if remaining_days <= 3:
            return "yellow"
        return "green"
