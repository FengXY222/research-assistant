"""1024x768 evidence-led workbench for journal collection calls."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabBar,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from ui.page_kit import ElidedLabel, EllipsisMenu
from utils.special_issue_service import clean_special_issue_scope, special_issue_scope_is_corrupted
from utils.special_issue_policy import evaluate_special_issue, is_ignored, is_read, is_saved, match_for_view, open_eligibility


def _known(value: Any) -> bool:
    return bool(str(value or "").strip()) and str(value).strip().casefold() not in {"unknown", "未知", "待确认", "n/a"}


def _quartile(item: dict[str, Any], kind: str) -> str:
    direct = str(item.get(f"{kind}_quartile", "")).strip().upper()
    if direct:
        return direct.removeprefix("Q").removesuffix("区")
    raw = item.get(kind, {}) if isinstance(item.get(kind), dict) else {}
    if kind == "cas":
        for key in ("cas_upgrade", "cas_basic", "cas_upgrade_small"):
            text = str(raw.get(key, item.get(key, ""))).strip()
            for number in "1234":
                if f"{number}区" in text.replace(" ", ""):
                    return number
    for key in ("quartile", "partition", "zone", "value"):
        text = str(raw.get(key, "")).strip().upper()
        if text:
            for number in "1234":
                if number in text:
                    return number
    metrics = raw.get("metrics", []) if isinstance(raw.get("metrics"), list) else []
    for metric in metrics:
        if isinstance(metric, dict):
            text = str(metric.get("quartile", metric.get("partition", ""))).upper()
            for number in "1234":
                if number in text:
                    return number
    return ""


class SpecialIssueResultRow(QWidget):
    def __init__(self, item: dict[str, Any], *, unknown: bool, match: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.issue_id = str(item.get("id", ""))
        self.setObjectName("specialIssueResultRow")
        self.setFixedHeight(88)
        root = QVBoxLayout(self)
        root.setContentsMargins(9, 7, 9, 7)
        root.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.title_label = ElidedLabel(str(item.get("title", "未命名特刊")))
        self.title_label.setObjectName("specialIssueResultTitle")
        top.addWidget(self.title_label, 1)
        match = match if isinstance(match, dict) else item.get("match", {})
        is_v13 = str(item.get("scoring_version", "")).startswith("special-issue-dual-axis-13")
        if is_v13:
            level = str(item.get("pyramid_level", ""))
            score_text = level or "符合"
        else:
            score_value = match.get("score") if isinstance(match, dict) else None
            score_text = "待评" if score_value is None else str(int(score_value or 0))
        score = QLabel(score_text)
        score.setObjectName("specialIssueResultScore")
        score.setFixedWidth(32)
        score.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        top.addWidget(score)
        root.addLayout(top)
        verification = str(item.get("verification_status", "")).casefold()
        verification_label = str(item.get("verification_label", "")).strip()
        state_label = (
            "已忽略"
            if is_ignored(item)
            else "已收藏"
            if is_saved(item)
            else verification_label
            if verification_label
            else "待核验"
            if verification in {"aggregator_unverified", "temporarily_unavailable", "pending_official"}
            else "推荐"
        )
        journal_text = str(item.get("journal", "未知期刊")).strip() or "未知期刊"
        identity = QHBoxLayout()
        identity.setSpacing(6)
        self.state_label = QLabel(state_label)
        self.state_label.setObjectName("specialIssueResultState")
        self.state_label.setProperty("issueState", state_label)
        self.state_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        identity.addWidget(self.state_label)
        self.journal_label = ElidedLabel(journal_text)
        self.journal_label.setObjectName("specialIssueResultJournal")
        self.journal_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        identity.addWidget(self.journal_label, 1)
        root.addLayout(identity)
        facts = []
        if is_v13:
            facts.append(f"相关 {int(item.get('relevance_score', 0) or 0)} · 机会 {int(item.get('opportunity_score', 0) or 0)}")
            relevance_axis = item.get("relevance_axis", {}) if isinstance(item.get("relevance_axis"), dict) else {}
            opportunity_axis = item.get("opportunity_axis", {}) if isinstance(item.get("opportunity_axis"), dict) else {}
            ai_used = relevance_axis.get("ai_adjustment") is not None or opportunity_axis.get("ai_adjustment") is not None
            facts.append("AI 已参与" if ai_used else "规则评分")
        if unknown:
            facts.append("信息待补")
        facts.extend(
            value
            for value in (
                str(item.get("publisher", "")).strip() if _known(item.get("publisher")) else "",
                f"JCR {_quartile(item, 'jcr')}区" if _quartile(item, "jcr") else "",
                f"中科院 {_quartile(item, 'cas')}区" if _quartile(item, "cas") else "",
                f"截止 {str(item.get('deadline', '待确认'))[:10]}",
            )
            if value
        )
        self.meta = ElidedLabel(" · ".join(facts))
        self.meta.setObjectName("specialIssueResultMeta")
        self.meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.meta)
        self._summary = " · ".join([state_label, journal_text, *facts])

    def summary_text(self) -> str:
        return self._summary


class SpecialIssueDialog(QDialog):
    changed = Signal()
    associate_requested = Signal(str, list)
    path_requested = Signal(str, str)
    task_requested = Signal(str, str)
    status_requested = Signal(str, str)
    library_requested = Signal(str)
    scope_note_requested = Signal(str, str)
    refresh_requested = Signal()
    cancel_refresh_requested = Signal()

    def __init__(
        self,
        store: dict[str, Any],
        items: list[dict[str, Any]],
        papers: list[dict[str, Any]],
        selected_issue_id: str = "",
        parent: QWidget | None = None,
        now_provider=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("specialIssueDialog")
        self.setWindowTitle("特刊征稿工作台")
        self.setFixedSize(1024, 768)
        source_store = store if isinstance(store, dict) else {}
        self._store = {key: value for key, value in source_store.items() if key != "items"}
        self._items = [value for value in items if isinstance(value, dict)]
        self._papers = [value for value in papers if isinstance(value, dict)]
        self._v13 = any(
            str(value.get("scoring_version", "")).startswith("special-issue-dual-axis-13")
            for value in self._items
        )
        self._now_provider = now_provider or datetime.now
        self.visible_issue_ids: list[str] = []
        self._rows: dict[str, SpecialIssueResultRow] = {}
        self._build_ui()
        self._populate_controls()
        self._rebuild_results(selected_issue_id=selected_issue_id)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(7)
        heading = QHBoxLayout()
        heading.setSpacing(7)
        title = QLabel("特刊征稿工作台")
        title.setObjectName("specialWorkbenchTitle")
        heading.addWidget(title)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("specialSearch")
        self.search_input.setPlaceholderText("搜索特刊、期刊或主题")
        self.search_input.setMinimumWidth(190)
        self.search_input.textChanged.connect(self._rebuild_results)
        heading.addWidget(self.search_input, 1)
        self.filter_toggle = QPushButton("筛选")
        self.filter_toggle.setObjectName("specialFilterToggle")
        self.filter_toggle.setCheckable(True)
        self.filter_toggle.toggled.connect(self._toggle_filters)
        heading.addWidget(self.filter_toggle)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setObjectName("specialRefreshButton")
        self.refresh_button.setToolTip("刷新多源征稿与官网核验")
        self.refresh_button.clicked.connect(self._request_refresh)
        heading.addWidget(self.refresh_button)
        self.cancel_refresh_button = QPushButton("取消刷新")
        self.cancel_refresh_button.setObjectName("specialCancelRefreshButton")
        self.cancel_refresh_button.clicked.connect(self._request_cancel_refresh)
        self.cancel_refresh_button.hide()
        heading.addWidget(self.cancel_refresh_button)
        close_button = QPushButton("关闭")
        close_button.setObjectName("subtleButton")
        close_button.clicked.connect(self.close)
        heading.addWidget(close_button)
        root.addLayout(heading)

        self.filter_panel = QFrame()
        self.filter_panel.setObjectName("specialFilterPanel")
        filters = QHBoxLayout(self.filter_panel)
        filters.setContentsMargins(8, 6, 8, 6)
        filters.setSpacing(5)
        filters.addWidget(QLabel("匹配范围"))
        self.profile_scope = QComboBox()
        self.profile_scope.setObjectName("specialProfileScope")
        self.profile_scope.setMinimumWidth(160)
        self.profile_scope.currentIndexChanged.connect(self._rebuild_results)
        filters.addWidget(self.profile_scope, 2)
        self.publisher_filter = self._filter_combo("specialPublisherFilter", filters)
        self.fee_filter = self._filter_combo("specialFeeFilter", filters)
        self.jcr_filter = self._filter_combo("specialJcrFilter", filters)
        self.cas_filter = self._filter_combo("specialCasFilter", filters)
        self.deadline_filter = self._filter_combo("specialDeadlineFilter", filters)
        self.filter_panel.hide()
        root.addWidget(self.filter_panel)

        self.progress = QProgressBar()
        self.progress.setObjectName("specialRefreshProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.hide()
        root.addWidget(self.progress)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("specialWorkbenchSplitter")
        splitter.setChildrenCollapsible(False)
        self.workbench_splitter = splitter
        left = QFrame()
        left.setObjectName("specialResultPane")
        left.setMinimumWidth(350)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 5, 0)
        left_layout.setSpacing(5)
        self.view_tabs = QTabBar()
        self.view_tabs.setObjectName("specialViewTabs")
        self.view_tabs.setUsesScrollButtons(False)
        self.view_tabs.setExpanding(True)
        self.view_tabs.setElideMode(Qt.TextElideMode.ElideNone)
        tabs = [("推荐", "recommended"), ("收藏", "saved"), ("状态变化", "changed"), ("已忽略", "ignored")]
        for label, data in tabs:
            index = self.view_tabs.addTab(label)
            self.view_tabs.setTabData(index, data)
        self.view_tabs.currentChanged.connect(self._rebuild_results)
        left_layout.addWidget(self.view_tabs)
        self.result_list = QListWidget()
        self.result_list.setObjectName("specialResultList")
        self.result_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.result_list.currentItemChanged.connect(self._show_current_detail)
        left_layout.addWidget(self.result_list, 1)
        splitter.addWidget(left)

        detail = QFrame()
        detail.setObjectName("specialDetailPane")
        detail_root = QVBoxLayout(detail)
        detail_root.setContentsMargins(8, 0, 0, 0)
        detail_root.setSpacing(6)
        detail_scroll = QScrollArea()
        detail_scroll.setObjectName("specialDetailScroll")
        detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        detail_scroll.setWidgetResizable(True)
        self.detail_host = QWidget()
        detail_layout = QVBoxLayout(self.detail_host)
        detail_layout.setContentsMargins(2, 0, 5, 4)
        detail_layout.setSpacing(6)
        self.detail_title = QLabel("选择左侧特刊查看详情")
        self.detail_title.setObjectName("specialDetailTitle")
        self.detail_title.setWordWrap(True)
        self.detail_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.detail_title)
        self.empty_detail = QLabel("没有匹配的特刊\n可修改搜索词，或展开“筛选”放宽条件。")
        self.empty_detail.setObjectName("specialEmptyDetail")
        self.empty_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_detail.setWordWrap(True)
        self.empty_detail.hide()
        detail_layout.addWidget(self.empty_detail, 1)
        self.detail_facts = QLabel("")
        self.detail_facts.setObjectName("specialDetailFacts")
        self.detail_facts.setWordWrap(True)
        self.detail_facts.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.detail_facts)
        self.detail_reason = QLabel("")
        self.detail_reason.setObjectName("specialDetailReason")
        self.detail_reason.setWordWrap(True)
        self.detail_reason.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.detail_reason)
        self.scope_label = QLabel("征稿范围")
        self.scope_label.setObjectName("specialDetailSectionTitle")
        detail_layout.addWidget(self.scope_label)
        self.scope_view = QPlainTextEdit()
        self.scope_view.setObjectName("specialScopeText")
        self.scope_view.setReadOnly(True)
        self.scope_view.setMinimumHeight(138)
        self.scope_view.setMaximumHeight(190)
        detail_layout.addWidget(self.scope_view)
        self.note_toggle = QToolButton()
        self.note_toggle.setObjectName("specialNoteToggle")
        self.note_toggle.setText("添加个人判断")
        self.note_toggle.setCheckable(True)
        self.note_toggle.toggled.connect(self._toggle_note)
        detail_layout.addWidget(self.note_toggle)
        self.note_host = QFrame()
        self.note_host.setObjectName("specialNoteHost")
        note_root = QVBoxLayout(self.note_host)
        note_root.setContentsMargins(0, 0, 0, 0)
        note_root.setSpacing(5)
        note_heading = QHBoxLayout()
        note_heading.setSpacing(6)
        note_label = QLabel("我的理解")
        note_label.setObjectName("specialDetailSectionTitle")
        note_heading.addWidget(note_label)
        note_heading.addStretch(1)
        self.save_scope_note_button = QPushButton("保存理解")
        self.save_scope_note_button.setObjectName("specialSaveScopeNoteButton")
        self.save_scope_note_button.clicked.connect(self._request_scope_note)
        note_heading.addWidget(self.save_scope_note_button)
        note_root.addLayout(note_heading)
        self.personal_scope_note = QPlainTextEdit()
        self.personal_scope_note.setObjectName("specialPersonalScopeNote")
        self.personal_scope_note.setPlaceholderText("写下你对该特刊方向、适合稿件或风险的理解")
        self.personal_scope_note.setMinimumHeight(72)
        self.personal_scope_note.setMaximumHeight(112)
        self.personal_scope_note.textChanged.connect(self._sync_scope_note_state)
        note_root.addWidget(self.personal_scope_note)
        self.note_host.hide()
        detail_layout.addWidget(self.note_host)
        self.matched_papers = QLabel("")
        self.matched_papers.setObjectName("specialMatchedPapers")
        self.matched_papers.setWordWrap(True)
        self.matched_papers.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.matched_papers)
        self.evidence_toggle = QToolButton()
        self.evidence_toggle.setObjectName("specialEvidenceToggle")
        self.evidence_toggle.setText("核验依据")
        self.evidence_toggle.setCheckable(True)
        self.evidence_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.evidence_toggle.toggled.connect(self._toggle_evidence)
        detail_layout.addWidget(self.evidence_toggle)
        self.evidence_host = QWidget()
        self.evidence_host.setObjectName("specialVerificationEvidence")
        evidence_layout = QVBoxLayout(self.evidence_host)
        evidence_layout.setContentsMargins(0, 0, 0, 0)
        self.evidence_browser = QTextBrowser()
        self.evidence_browser.setObjectName("specialEvidenceBrowser")
        self.evidence_browser.setOpenExternalLinks(True)
        self.evidence_browser.setMinimumHeight(140)
        evidence_layout.addWidget(self.evidence_browser)
        self.evidence_host.hide()
        detail_layout.addWidget(self.evidence_host)
        detail_layout.addStretch(1)
        detail_scroll.setWidget(self.detail_host)
        detail_root.addWidget(detail_scroll, 1)

        self.issue_actions = QFrame()
        self.issue_actions.setObjectName("specialIssueActions")
        issue_action_line = QHBoxLayout(self.issue_actions)
        issue_action_line.setContentsMargins(8, 6, 8, 6)
        issue_action_line.setSpacing(5)
        self.save_button = self._action_button("收藏特刊", "specialSaveButton", issue_action_line, self._request_save)
        issue_action_line.addStretch(1)
        self.read_button = self._action_button("标为已读", "specialReadButton", issue_action_line, self._request_read)
        self.ignore_button = self._action_button("排除特刊", "specialIgnoreButton", issue_action_line, self._request_ignore)
        self.library_button = self._action_button("加入期刊库", "specialLibraryButton", issue_action_line, self._request_library)
        for button in (self.read_button, self.ignore_button, self.library_button):
            button.hide()
        self.issue_more = EllipsisMenu(tooltip="更多特刊操作", parent=self.issue_actions)
        self.issue_more.setObjectName("specialIssueMore")
        self.issue_more.add_action("标为已读", self.read_button.click)
        self.issue_more.add_action("加入期刊库", self.library_button.click)
        self.issue_more.add_separator()
        self.issue_more.add_action("排除特刊", self.ignore_button.click)
        issue_action_line.addWidget(self.issue_more)
        detail_root.addWidget(self.issue_actions)

        self.paper_actions = QFrame()
        self.paper_actions.setObjectName("specialPaperActions")
        paper_action_root = QHBoxLayout(self.paper_actions)
        paper_action_root.setContentsMargins(8, 6, 8, 6)
        paper_action_root.setSpacing(5)
        self.paper_selector = QComboBox()
        self.paper_selector.setObjectName("specialPaperSelector")
        self.paper_selector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        paper_action_root.addWidget(self.paper_selector, 1)
        self.path_button = self._action_button("加入投稿路径", "specialPathButton", paper_action_root, self._request_path)
        self.associate_button = self._action_button("关联论文", "specialAssociateButton", paper_action_root, self._request_associate)
        self.task_button = self._action_button("创建准备任务", "specialTaskButton", paper_action_root, self._request_task)
        self.associate_button.hide()
        self.task_button.hide()
        self.paper_more = EllipsisMenu(tooltip="更多论文操作", parent=self.paper_actions)
        self.paper_more.setObjectName("specialPaperMore")
        self.paper_more.add_action("标为已读", self.read_button.click)
        self.paper_more.add_action("加入期刊库", self.library_button.click)
        self.paper_more.add_action("仅关联论文", self.associate_button.click)
        self.paper_more.add_action("创建准备任务", self.task_button.click)
        self.paper_more.add_separator()
        self.paper_more.add_action("排除特刊", self.ignore_button.click)
        paper_action_root.addWidget(self.paper_more)
        paper_action_root.insertWidget(0, self.save_button)
        self.issue_actions.hide()
        detail_root.addWidget(self.paper_actions)
        self.action_status = QLabel("")
        self.action_status.setObjectName("specialActionStatus")
        detail_root.addWidget(self.action_status)
        self._detail_content_widgets = [
            self.detail_facts,
            self.detail_reason,
            self.scope_label,
            self.scope_view,
            self.note_toggle,
            self.note_host,
            self.matched_papers,
            self.evidence_toggle,
            self.evidence_host,
        ]
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        splitter.setSizes([380, 600])
        root.addWidget(splitter, 1)

    def _filter_combo(self, object_name: str, layout: QHBoxLayout) -> QComboBox:
        combo = QComboBox()
        combo.setObjectName(object_name)
        combo.setMinimumWidth(92)
        combo.currentIndexChanged.connect(self._rebuild_results)
        layout.addWidget(combo, 1)
        return combo

    def _toggle_filters(self, checked: bool) -> None:
        self.filter_panel.setVisible(bool(checked))
        self.filter_toggle.setText("收起筛选" if checked else "筛选")

    def _toggle_note(self, checked: bool) -> None:
        self.note_host.setVisible(bool(checked))
        self.note_toggle.setText("收起个人判断" if checked else "添加个人判断")
        if checked:
            self.personal_scope_note.setFocus()

    @staticmethod
    def _action_button(text: str, object_name: str, layout: QHBoxLayout, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.clicked.connect(slot)
        layout.addWidget(button)
        return button

    def _populate_controls(self) -> None:
        self.profile_scope.clear()
        self.profile_scope.addItem("综合研究画像", "global")
        self.paper_selector.clear()
        for paper in self._papers:
            paper_id = str(paper.get("id", "")).strip()
            title = str(paper.get("title", "未命名论文")).strip() or "未命名论文"
            if paper_id:
                self.profile_scope.addItem(title, paper_id)
                self.paper_selector.addItem(title, paper_id)
        current_publisher = self.publisher_filter.currentData()
        self.publisher_filter.clear()
        self.publisher_filter.addItem("出版社：不限", "any")
        publishers = sorted({str(value.get("publisher", "")).strip() for value in self._items if _known(value.get("publisher"))}, key=str.casefold)
        for publisher in publishers:
            self.publisher_filter.addItem(publisher, publisher)
        self._restore_combo(self.publisher_filter, current_publisher)
        self._set_options(self.fee_filter, [("费用：不限", "any"), ("不付费", "no_fee"), ("付费", "paid")])
        self._set_options(self.jcr_filter, [("JCR：不限", "any"), *((f"JCR {n}区", n) for n in "1234")])
        self._set_options(self.cas_filter, [("中科院：不限", "any"), *((f"中科院 {n}区", n) for n in "1234")])
        self._set_options(self.deadline_filter, [("截止：不限", "any"), ("30天内", 30), ("90天内", 90), ("180天内", 180)])

    @staticmethod
    def _set_options(combo: QComboBox, options: list[tuple[str, Any]]) -> None:
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for label, value in options:
            combo.addItem(label, value)
        SpecialIssueDialog._restore_combo(combo, current)
        combo.blockSignals(False)

    @staticmethod
    def _restore_combo(combo: QComboBox, value: Any) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def _current_view(self) -> str:
        return str(self.view_tabs.tabData(self.view_tabs.currentIndex()) or "recommended")

    def _filter_item(self, item: dict[str, Any]) -> tuple[bool, int]:
        scope = str(self.profile_scope.currentData() or "global")
        filters = {
            "search": self.search_input.text(),
            "publisher": self.publisher_filter.currentData(),
            "fee": self.fee_filter.currentData(),
            "jcr": self.jcr_filter.currentData(),
            "cas": self.cas_filter.currentData(),
            "deadline": self.deadline_filter.currentData(),
        }
        decision = evaluate_special_issue(
            item,
            now=self._now_provider(),
            paper_id="" if scope == "global" else scope,
            view=self._current_view(),
            filters=filters,
        )
        return bool(decision["visible"]), int(decision["unknown_count"])

    def _rebuild_results(self, *_args, selected_issue_id: str = "") -> None:
        previous = selected_issue_id or self.selected_issue_id()
        rows: list[tuple[tuple, dict[str, Any], dict[str, Any]]] = []
        scope = str(self.profile_scope.currentData() or "global")
        filters = {
            "search": self.search_input.text(), "publisher": self.publisher_filter.currentData(),
            "fee": self.fee_filter.currentData(), "jcr": self.jcr_filter.currentData(),
            "cas": self.cas_filter.currentData(), "deadline": self.deadline_filter.currentData(),
        }
        for item in self._items:
            decision = evaluate_special_issue(
                item, now=self._now_provider(), paper_id="" if scope == "global" else scope,
                view=self._current_view(), filters=filters,
            )
            if not decision["visible"]:
                continue
            rows.append((decision["sort_key"], item, decision))
        rows.sort(key=lambda value: value[0])
        self.result_list.blockSignals(True)
        self.result_list.clear()
        self.visible_issue_ids = []
        self._rows = {}
        selected_row = -1
        for index, (_sort_key, item, decision) in enumerate(rows):
            item_id = str(item.get("id", ""))
            list_item = QListWidgetItem()
            list_item.setData(Qt.ItemDataRole.UserRole, item_id)
            row = SpecialIssueResultRow(item, unknown=bool(decision["unknown_count"]), match=decision["match"])
            list_item.setSizeHint(QSize(0, row.minimumHeight()))
            self.result_list.addItem(list_item)
            self.result_list.setItemWidget(list_item, row)
            self.visible_issue_ids.append(item_id)
            self._rows[item_id] = row
            if item_id == previous:
                selected_row = index
        self.result_list.blockSignals(False)
        if self.result_list.count():
            self.result_list.setCurrentRow(selected_row if selected_row >= 0 else 0)
            self._show_current_detail(self.result_list.currentItem())
        else:
            self._show_empty_detail()

    def row_for_issue(self, issue_id: str) -> SpecialIssueResultRow:
        return self._rows[str(issue_id)]

    def selected_issue_id(self) -> str:
        item = self.result_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""

    def select_issue(self, issue_id: str) -> bool:
        wanted = str(issue_id)
        for index in range(self.result_list.count()):
            item = self.result_list.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole)) == wanted:
                self.result_list.setCurrentRow(index)
                return True
        return False

    def _issue_by_id(self, issue_id: str) -> dict[str, Any] | None:
        return next((value for value in self._items if str(value.get("id", "")) == str(issue_id)), None)

    def _show_current_detail(self, current: QListWidgetItem | None, _previous=None) -> None:
        item = self._issue_by_id(str(current.data(Qt.ItemDataRole.UserRole))) if current is not None else None
        if item is None:
            self._show_empty_detail()
            return
        self.empty_detail.hide()
        for widget in self._detail_content_widgets:
            if widget not in {self.note_host, self.evidence_host}:
                widget.show()
        self.issue_actions.hide()
        self.paper_actions.show()
        self.action_status.show()
        scope = str(self.profile_scope.currentData() or "global")
        match = match_for_view(item, "" if scope == "global" else scope)
        is_v13 = str(item.get("scoring_version", "")).startswith("special-issue-dual-axis-13")
        self.detail_title.setText(str(item.get("title", "未命名特刊")))
        if is_v13:
            relevance = item.get("relevance_axis", {}) if isinstance(item.get("relevance_axis"), dict) else {}
            opportunity = item.get("opportunity_axis", {}) if isinstance(item.get("opportunity_axis"), dict) else {}
            facts = [
                str(item.get("journal", "未知期刊")),
                str(item.get("publisher", "出版社待核验")),
                f"截止 {str(item.get('deadline', '待确认'))[:10]}",
                f"相关 {int(relevance.get('total', 0) or 0)}",
                f"机会 {int(opportunity.get('total', 0) or 0)}",
            ]
            self.detail_facts.setToolTip(
                " · ".join(
                    [
                        f"层级 {str(item.get('pyramid_level', '待分层'))}",
                        str(item.get("verification_label", "")),
                        f"JCR {_quartile(item, 'jcr')}区" if _quartile(item, "jcr") else "JCR 待核验",
                        f"中科院 {_quartile(item, 'cas')}区" if _quartile(item, "cas") else "中科院待核验",
                        f"相关基础 {int(relevance.get('base_score', 0) or 0)}/66，AI {relevance.get('ai_adjustment', '未参与')}",
                        f"机会基础 {int(opportunity.get('base_score', 0) or 0)}/66，AI {opportunity.get('ai_adjustment', '未参与')}",
                    ]
                )
            )
            reasons = [
                *[str(value) for value in relevance.get("reasons", []) if str(value).strip()],
                *[str(value) for value in opportunity.get("reasons", []) if str(value).strip()],
            ]
            reason_text = "；".join(reasons[:4]) or "已通过信息完整性与双轴门槛。"
        else:
            score_value = match.get("score")
            score = "待评估" if score_value is None else str(int(score_value or 0))
            facts = [
                f"匹配 {score}",
                str(item.get("journal", "未知期刊")),
                str(item.get("publisher", "出版社待核验")),
                f"截止 {str(item.get('deadline', '待确认'))[:10]}",
            ]
            self.detail_facts.setToolTip(
                " · ".join(
                    [
                        f"JCR {_quartile(item, 'jcr')}区" if _quartile(item, "jcr") else "JCR 待核验",
                        f"中科院 {_quartile(item, 'cas')}区" if _quartile(item, "cas") else "中科院待核验",
                    ]
                )
            )
            reason_text = str(match.get("reason", "")).strip() or "尚无可用的 AI 推荐理由。"
        facts = [value for value in facts if value]
        self.detail_facts.setText(" · ".join(facts))
        self.detail_reason.setText("推荐理由：" + reason_text)
        raw_scope = str(item.get("scope_text", ""))
        original_scope = clean_special_issue_scope(raw_scope)
        if special_issue_scope_is_corrupted(raw_scope):
            original_scope = ""
        translated_scope = str(item.get("scope_text_zh", "")).strip()
        if translated_scope and original_scope:
            scope_text = f"中文翻译\n{translated_scope}\n\n英文原文\n{original_scope}"
        elif original_scope:
            scope_text = f"英文原文\n{original_scope}\n\n中文翻译正在由内置离线工具补充。"
        else:
            scope_text = "尚未取得完整征稿范围。"
        self.scope_view.setPlainText(scope_text)
        note_text = str(item.get("personal_scope_note", "")).strip()
        self.personal_scope_note.blockSignals(True)
        self.personal_scope_note.setPlainText(note_text)
        self.personal_scope_note.blockSignals(False)
        self.save_scope_note_button.setEnabled(False)
        self.note_toggle.setChecked(bool(note_text))
        self._toggle_note(bool(note_text))
        paper_titles = {str(value.get("id", "")): str(value.get("title", "未命名论文")) for value in self._papers}
        matched = []
        for value in match.get("matched_papers", []) if isinstance(match.get("matched_papers"), list) else []:
            if not isinstance(value, dict):
                continue
            paper_id = str(value.get("paper_id", ""))
            if paper_id in paper_titles:
                matched.append(f"{paper_titles[paper_id]}（{int(value.get('score', 0) or 0)}分：{str(value.get('reason', '')).strip()}）")
        self.matched_papers.setText("适合论文：" + "；".join(matched) if matched else "")
        self.matched_papers.setVisible(bool(matched))
        self.evidence_toggle.setChecked(False)
        self._toggle_evidence(False)
        self._render_evidence(item)
        self._sync_action_state(item)

    def _show_empty_detail(self) -> None:
        self.detail_title.setText("没有符合当前条件的特刊")
        self.empty_detail.show()
        self.detail_facts.clear()
        self.detail_reason.clear()
        self.scope_view.clear()
        self.personal_scope_note.blockSignals(True)
        self.personal_scope_note.clear()
        self.personal_scope_note.blockSignals(False)
        self.save_scope_note_button.setEnabled(False)
        self.matched_papers.clear()
        self.evidence_browser.clear()
        for widget in self._detail_content_widgets:
            widget.hide()
        self.issue_actions.hide()
        self.paper_actions.hide()
        self.action_status.hide()
        for button in (self.read_button, self.save_button, self.ignore_button, self.library_button, self.associate_button, self.path_button, self.task_button):
            button.setEnabled(False)

    def _render_evidence(self, item: dict[str, Any]) -> None:
        lines = [
            f"<b>ISSN</b> {escape('、'.join(str(value) for value in item.get('issns', [])) or '待核验')}",
            f"<b>官网核验</b> {escape(str(item.get('verification_status', '待核验')))} · {escape(str(item.get('official_checked_at', '未检查')))}",
        ]
        official = str(item.get("official_url", "")).strip()
        if official:
            safe = escape(official, quote=True)
            lines.append(f'<b>官方页面</b> <a href="{safe}">{safe}</a>')
        for url in item.get("discovery_urls", []) if isinstance(item.get("discovery_urls"), list) else []:
            safe = escape(str(url), quote=True)
            lines.append(f'<b>发现来源</b> <a href="{safe}">{safe}</a>')
        for evidence in item.get("source_evidence", []) if isinstance(item.get("source_evidence"), list) else []:
            if isinstance(evidence, dict):
                lines.append(f"<b>{escape(str(evidence.get('source', '来源')))}</b> 更新于 {escape(str(evidence.get('fetched_at', '未知')))}")
        history = item.get("deadline_history", []) if isinstance(item.get("deadline_history"), list) else []
        if history:
            lines.append("<b>截止日期历史</b> " + " → ".join(escape(str(value.get("deadline", ""))) for value in history if isinstance(value, dict)))
        self.evidence_browser.setHtml("<br>".join(lines))

    def _sync_action_state(self, item: dict[str, Any]) -> None:
        enabled = bool(str(item.get("id", "")))
        has_paper = self.paper_selector.count() > 0
        if str(item.get("scoring_version", "")).startswith("special-issue-dual-axis-13"):
            open_ok = bool(evaluate_special_issue(item, now=self._now_provider(), view="all")["open"])
        else:
            open_ok, _reason = open_eligibility(item, self._now_provider())
        for button in (self.read_button, self.save_button, self.ignore_button):
            button.setEnabled(enabled)
        self.library_button.setEnabled(enabled)
        self.associate_button.setEnabled(enabled and has_paper)
        self.path_button.setEnabled(enabled and has_paper and open_ok)
        self.task_button.setEnabled(enabled and has_paper and open_ok)
        self.read_button.setText("已读" if is_read(item) else "标为已读")
        self.save_button.setText("取消收藏" if is_saved(item) else "收藏特刊")
        self.ignore_button.setText("恢复特刊" if is_ignored(item) else "排除特刊")
        self.library_button.setText("已在期刊库" if str(item.get("journal_library_id", "")).strip() else "加入期刊库")

    def _sync_scope_note_state(self) -> None:
        item = self._issue_by_id(self.selected_issue_id()) or {}
        saved = str(item.get("personal_scope_note", "")).strip()
        changed = self.personal_scope_note.toPlainText().strip() != saved
        self.save_scope_note_button.setEnabled(bool(item) and changed)
        if changed:
            self.action_status.setText("“我的理解”有未保存修改")

    def _toggle_evidence(self, checked: bool) -> None:
        self.evidence_host.setVisible(bool(checked))
        self.evidence_toggle.setText("收起核验依据" if checked else "核验依据")

    def _request_refresh(self) -> None:
        self.progress.setRange(0, 0)
        self.progress.show()
        self.refresh_button.setEnabled(False)
        self.cancel_refresh_button.show()
        self.refresh_requested.emit()

    def _request_cancel_refresh(self) -> None:
        self.cancel_refresh_button.setEnabled(False)
        self.action_status.setText("正在停止刷新…")
        self.cancel_refresh_requested.emit()

    def set_progress(self, value: int, message: str = "") -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(max(0, min(100, int(value))))
        self.progress.setFormat(message or "%p%")
        self.progress.show()

    def finish_progress(self, message: str = "") -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat(message or "完成")
        self.refresh_button.setEnabled(True)
        self.cancel_refresh_button.setEnabled(True)
        self.cancel_refresh_button.hide()
        self.action_status.setText(message)

    def _selected_action(self) -> tuple[str, str]:
        return self.selected_issue_id(), str(self.paper_selector.currentData() or "")

    def _request_save(self) -> None:
        issue_id = self.selected_issue_id()
        if issue_id:
            item = self._issue_by_id(issue_id) or {}
            self.status_requested.emit(issue_id, "unsaved" if is_saved(item) else "saved")

    def _request_ignore(self) -> None:
        issue_id = self.selected_issue_id()
        if issue_id:
            item = self._issue_by_id(issue_id) or {}
            self.status_requested.emit(issue_id, "restored" if is_ignored(item) else "ignored")

    def _request_read(self) -> None:
        issue_id = self.selected_issue_id()
        if issue_id:
            self.status_requested.emit(issue_id, "read")

    def _request_scope_note(self) -> None:
        issue_id = self.selected_issue_id()
        if issue_id:
            self.scope_note_requested.emit(issue_id, self.personal_scope_note.toPlainText().strip())

    def _request_associate(self) -> None:
        issue_id, paper_id = self._selected_action()
        if issue_id and paper_id:
            self.associate_requested.emit(issue_id, [paper_id])

    def _request_library(self) -> None:
        issue_id = self.selected_issue_id()
        if issue_id:
            self.library_requested.emit(issue_id)

    def _request_path(self) -> None:
        issue_id, paper_id = self._selected_action()
        if issue_id and paper_id:
            self.path_requested.emit(issue_id, paper_id)

    def _request_task(self) -> None:
        issue_id, paper_id = self._selected_action()
        if issue_id and paper_id:
            self.task_requested.emit(issue_id, paper_id)

    def reload_data(self, store: dict[str, Any], items: list[dict[str, Any]], papers: list[dict[str, Any]]) -> None:
        selected = self.selected_issue_id()
        scope = self.profile_scope.currentData()
        paper = self.paper_selector.currentData()
        source_store = store if isinstance(store, dict) else {}
        self._store = {key: value for key, value in source_store.items() if key != "items"}
        self._items = [value for value in items if isinstance(value, dict)]
        self._papers = [value for value in papers if isinstance(value, dict)]
        self._populate_controls()
        self._restore_combo(self.profile_scope, scope)
        self._restore_combo(self.paper_selector, paper)
        self._rebuild_results(selected_issue_id=selected)
