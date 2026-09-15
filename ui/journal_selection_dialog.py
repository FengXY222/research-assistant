"""Compact AI-led journal-selection workbench."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ui.ai_progress import AiProgressPanel
from ui.theme import apply_dialog_theme
from utils.ai_service import (
    DeepSeekConfigurationError,
    DeepSeekRequestError,
    is_deepseek_ready,
)
from utils.evidence_cache import EvidenceCache
from utils.file_manager import (
    RESEARCH_INTELLIGENCE_CACHE_FILE,
    load_rejection_archive,
    selection_excluded_journal_names,
)
from utils.journal_quality import compact_metric_line, journal_quality_snapshot
from utils.journal_selection_service import (
    canonical_journal_name,
    normalize_selection_requirements,
    rank_ai_first_candidates,
    run_selection_rounds,
)


class JournalSelectionAiThread(QThread):
    """Run one deliberate AI recommendation without blocking the workbench."""

    completed = Signal(dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, paper: dict[str, Any], journals: list[dict[str, Any]], profile: dict[str, Any], requirements: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._paper = deepcopy(paper)
        self._journals = [deepcopy(item) for item in journals if isinstance(item, dict)]
        self._profile = deepcopy(profile)
        self._requirements = deepcopy(requirements)

    def run(self) -> None:
        try:
            cache = EvidenceCache(RESEARCH_INTELLIGENCE_CACHE_FILE)
            cache.initialize()
            result = run_selection_rounds(
                self._paper,
                self._requirements,
                [
                    {"name": name, "journal_name": name}
                    for name in self._requirements.get("rejected_journal_names", [])
                    if str(name).strip()
                ],
                cache=cache,
                progress=lambda message, value=0: self.progress.emit(str(message), int(value or 0)),
            )
            rows = result.get("results", []) if isinstance(result.get("results"), list) else []
            self.completed.emit(
                {
                    **result,
                    "ranked": rows,
                    "external_candidates": [],
                    "ai_qualified_count": len(rows),
                    "verification_mode": "configured" if result.get("verification_configured") else "skipped",
                }
            )
        except (DeepSeekConfigurationError, DeepSeekRequestError) as error:
            self.failed.emit(str(error))
        except Exception:  # noqa: BLE001 - local choices must survive provider errors
            self.failed.emit("AI 主推荐未完成，请稍后重试；已保留本地预览。")


class MultiSelectComboBox(QPushButton):
    """A persistent checkbox menu for JCR and CAS multi-select filters."""

    currentIndexChanged = Signal(int)
    selectionChanged = Signal(list)

    def __init__(self, placeholder: str, values: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._placeholder = placeholder
        self._options: list[tuple[str, QCheckBox]] = []
        self._menu = QMenu(self)
        self._menu.setObjectName("selectionMultiSelectMenu")
        self.setObjectName("selectionMultiSelect")
        self.setMinimumHeight(30)
        self.setToolTip(placeholder)
        for text, value in values:
            option = QCheckBox(text)
            option.setObjectName("selectionMultiSelectOption")
            option.setProperty("selectionValue", str(value))
            option.toggled.connect(self._selection_changed)
            action = QWidgetAction(self._menu)
            action.setDefaultWidget(option)
            self._menu.addAction(action)
            self._options.append((str(value), option))
        self.clicked.connect(self.open_popup)
        self._update_text()

    def open_popup(self) -> None:
        self._menu.setMinimumWidth(max(self.width(), self.minimumSizeHint().width()))
        self._menu.popup(self.mapToGlobal(QPoint(0, self.height())))

    def popup_menu(self) -> QMenu:
        return self._menu

    def option_checkboxes(self) -> list[QCheckBox]:
        return [checkbox for _value, checkbox in self._options]

    def _selection_changed(self, _checked: bool) -> None:
        self._update_text()
        self.currentIndexChanged.emit(len(self.checked_values()))
        self.selectionChanged.emit(self.checked_values())

    def _update_text(self) -> None:
        selected_labels = [checkbox.text() for _value, checkbox in self._options if checkbox.isChecked()]
        text = "、".join(selected_labels) if selected_labels else self._placeholder
        available = max(32, self.contentsRect().width() - 26)
        self.setText(self.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, available))
        self.setToolTip(text)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._update_text()

    def checked_values(self) -> list[str]:
        return [value for value, checkbox in self._options if checkbox.isChecked()]

    def set_checked_values(self, values: list[str]) -> None:
        expected = {str(value) for value in values}
        for value, checkbox in self._options:
            blocked = checkbox.blockSignals(True)
            checkbox.setChecked(value in expected)
            checkbox.blockSignals(blocked)
        self._update_text()


class WrappingRecommendationLabel(QLabel):
    """Use font metrics rather than Qt's unstable narrow-label size hint."""

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API spelling
        return True

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API spelling
        return QSize(0, self.fontMetrics().lineSpacing())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API spelling
        return QSize(180, self.fontMetrics().lineSpacing())

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API spelling
        if width <= 0:
            return self.fontMetrics().lineSpacing()
        flags = Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs
        bounds = self.fontMetrics().boundingRect(QRect(0, 0, width, 0), flags, self.text())
        return max(self.fontMetrics().lineSpacing(), bounds.height())


class RecommendationRow(QWidget):
    """A concise AI recommendation: title, score, reason, then one action."""

    height_changed = Signal()

    def __init__(
        self,
        candidate: dict[str, Any],
        action: Callable[[], None],
        parent: QWidget | None = None,
        *,
        library_action: Callable[[], None] | None = None,
        exclude_action: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("selectionRecommendationRow")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 9, 10, 9)
        self._layout.setSpacing(5)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.title_label = WrappingRecommendationLabel(str(candidate.get("journal_name", "未命名期刊")))
        self.title_label.setObjectName("selectionRowTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setMinimumWidth(0)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title_label.setToolTip(self.title_label.text())
        self.title_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.title_label.setCursor(Qt.CursorShape.IBeamCursor)
        self.score_label = QLabel(f"AI 总分\n{int(candidate.get('ai_total_score', candidate.get('total_score', 0)) or 0)}")
        self.score_label.setObjectName("selectionRowScore")
        self.score_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self.score_label.setMinimumWidth(78)
        self.score_label.setMaximumWidth(86)
        self.score_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.reason_label = WrappingRecommendationLabel("推荐理由 · " + (str(candidate.get("reason_cn", "")).strip() or "本地筛选结果，等待 AI 结合摘要复核。"))
        self.reason_label.setObjectName("selectionRowReason")
        self.reason_label.setWordWrap(True)
        self.reason_label.setMinimumWidth(0)
        self.reason_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.summary_fact_label = WrappingRecommendationLabel(self._summary_fact_line(candidate))
        self.summary_fact_label.setObjectName("selectionRowSummaryFacts")
        self.summary_fact_label.setWordWrap(True)
        self.summary_fact_label.setMinimumWidth(0)
        self.summary_fact_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        # Keep the underlying facts and risks available to older integrations,
        # but do not turn an AI-led result into a dense metadata card.
        self.fact_label = QLabel(self._fact_line(candidate))
        self.fact_label.setObjectName("selectionRowFacts")
        self.fact_label.setWordWrap(True)
        risk = str(candidate.get("risk_cn", "")).strip()
        if not risk:
            risks = [str(item).strip() for item in candidate.get("risks", []) if str(item).strip()]
            risk = risks[0] if risks else ""
        self.risk_label = QLabel("风险 · " + risk)
        self.risk_label.setObjectName("selectionRowRisk")
        self.risk_label.setWordWrap(True)
        self.evidence_label = WrappingRecommendationLabel(self._evidence_text(candidate))
        self.evidence_label.setObjectName("selectionEvidenceBody")
        self.evidence_label.setWordWrap(True)
        self.evidence_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.fact_label.hide()
        self.risk_label.hide()
        self.evidence_label.hide()
        self.action_button = QPushButton("已加入投稿路径" if candidate.get("path_added") else "加入投稿路径")
        self.action_button.setObjectName("selectionPathButton")
        self.action_button.setMinimumWidth(78)
        self.action_button.setToolTip("将此期刊加入论文投稿路径")
        self.action_button.clicked.connect(action)
        if candidate.get("path_added"):
            self.action_button.setEnabled(False)

        self.library_button: QPushButton | None = None
        if candidate.get("is_external"):
            self.library_button = QPushButton("已加入期刊库" if candidate.get("library_imported") else "加入期刊库")
            self.library_button.setObjectName("selectionLibraryButton")
            self.library_button.setMinimumWidth(78)
            self.library_button.setToolTip("将 AI 扩展期刊保存到本地期刊库；不会关闭选刊工作台")
            self.library_button.clicked.connect(library_action or (lambda: None))
            if candidate.get("library_imported"):
                self.library_button.setEnabled(False)

        self.exclude_button = QPushButton("排除")
        self.exclude_button.setObjectName("selectionExcludeButton")
        self.exclude_button.setToolTip("仅对当前论文排除此期刊；下次选刊会自动跳过")
        self.exclude_button.clicked.connect(exclude_action or (lambda: None))

        header.addWidget(self.title_label, 1)
        header.addWidget(self.score_label)
        self._layout.addLayout(header)
        self._layout.addWidget(self.reason_label)
        self._layout.addWidget(self.summary_fact_label)
        self._layout.addWidget(self.fact_label)
        self._layout.addWidget(self.risk_label)
        self._layout.addWidget(self.evidence_label)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self.evidence_button = QPushButton("核验依据")
        self.evidence_button.setObjectName("selectionEvidenceToggle")
        self.evidence_button.setCheckable(True)
        self.evidence_button.setToolTip("展开相似论文、期刊身份、ISSN、分区来源与更新时间")
        self.evidence_button.toggled.connect(self._set_evidence_visible)
        actions.addWidget(self.evidence_button)
        actions.addStretch(1)
        actions.addWidget(self.exclude_button)
        if self.library_button is not None:
            actions.addWidget(self.library_button)
        actions.addWidget(self.action_button)
        self._layout.addLayout(actions)

    def _set_evidence_visible(self, visible: bool) -> None:
        self.fact_label.setVisible(visible)
        self.risk_label.setVisible(visible and self.risk_label.text().strip() not in {"", "风险 ·"})
        self.evidence_label.setVisible(visible)
        self.evidence_button.setText("收起依据" if visible else "核验依据")
        self.updateGeometry()
        self.height_changed.emit()

    @staticmethod
    def _label_height(label: QLabel, width: int) -> int:
        required = label.heightForWidth(max(1, width))
        return max(label.fontMetrics().lineSpacing(), label.minimumSizeHint().height(), required)

    def preferred_height(self, width: int) -> int:
        margins = self._layout.contentsMargins()
        content_width = max(120, width - margins.left() - margins.right())
        title_width = max(72, content_width - self.score_label.maximumWidth() - 8)
        title_height = self._label_height(self.title_label, title_width)
        reason_height = self._label_height(self.reason_label, content_width)
        summary_height = self._label_height(self.summary_fact_label, content_width)
        # Nested Qt box layouts do not reliably bubble a word-wrapped label's
        # height-for-width to the QListWidget item.  Pin only the current
        # minimums so the row can still grow, but never clip the wrapped text.
        self.title_label.setMinimumHeight(title_height)
        self.reason_label.setMinimumHeight(reason_height)
        self.summary_fact_label.setMinimumHeight(summary_height)
        rows = [
            max(title_height, self.score_label.sizeHint().height()),
            reason_height,
            summary_height,
        ]
        for label in (self.fact_label, self.risk_label, self.evidence_label):
            if label.isVisible():
                height = self._label_height(label, content_width)
                label.setMinimumHeight(height)
                rows.append(height)
        rows.append(max(self.action_button.sizeHint().height(), self.evidence_button.sizeHint().height()))
        visible_rows = [height for height in rows if height > 0]
        return margins.top() + margins.bottom() + sum(visible_rows) + self._layout.spacing() * max(0, len(visible_rows) - 1)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API spelling
        width = self.width() if self.width() > 0 else 520
        return QSize(width, self.preferred_height(width))

    @staticmethod
    def _fact_line(candidate: dict[str, Any]) -> str:
        if candidate.get("is_external"):
            identity = candidate.get("identity_evidence", {})
            if isinstance(identity, dict) and identity.get("verified"):
                return "库外期刊 · 身份已核验"
            return "AI 扩展候选 · 待核验"
        journal = candidate.get("journal", {})
        quality = journal_quality_snapshot(journal if isinstance(journal, dict) else {})
        facts = [str(quality.get("metric_line", "")).strip()]
        fee_labels = {"hybrid": "费用：可选付费/不付费", "apc": "费用：付费", "subscription": "费用：不付费"}
        fee_text = fee_labels.get(str(candidate.get("fee_mode", "")), "费用待核验")
        if candidate.get("fee_pending_verification"):
            fee_text = "费用待核验"
        facts.append(fee_text)
        return " · ".join(part for part in facts if part) or "本地资料待补充"

    @staticmethod
    def _summary_fact_line(candidate: dict[str, Any]) -> str:
        journal = candidate.get("journal", {}) if isinstance(candidate.get("journal"), dict) else {}
        publisher = str(candidate.get("publisher", journal.get("publisher", ""))).strip() or "出版社待核验"
        metric = compact_metric_line(journal) or "分区待核验"
        fee_mode = str(candidate.get("fee_mode", journal.get("fee_mode", ""))).strip().casefold()
        fee_text = {
            "hybrid": "可付费/不付费",
            "apc": "付费",
            "paid": "付费",
            "subscription": "不付费",
            "no_fee": "不付费",
        }.get(fee_mode, "费用待核验")
        speed = str(candidate.get("estimated_speed_text", "")).strip()
        if not speed:
            try:
                low = int(candidate.get("estimated_decision_days_min", 0) or 0)
                high = int(candidate.get("estimated_decision_days_max", 0) or 0)
            except (TypeError, ValueError):
                low = high = 0
            speed = f"预计 {low}-{high} 天" if low and high else "预计速度待核验"
        return " · ".join((publisher, metric, fee_text, speed))

    @staticmethod
    def _evidence_text(candidate: dict[str, Any]) -> str:
        journal = candidate.get("journal", {}) if isinstance(candidate.get("journal"), dict) else {}
        identity = candidate.get("identity_evidence", journal.get("identity_evidence", {}))
        identity = identity if isinstance(identity, dict) else {}
        issns = identity.get("issns", journal.get("issns", journal.get("issn", [])))
        if isinstance(issns, str):
            issns = [issns]
        issn_text = "、".join(str(value).strip() for value in issns if str(value).strip()) or "待核验"
        sources = identity.get("source_names", identity.get("sources", []))
        if isinstance(sources, str):
            sources = [sources]
        source_text = "、".join(str(value).strip() for value in sources if str(value).strip()) or "待核验"
        scope = str(
            journal.get("aims_scope", journal.get("official_scope", journal.get("ai_scope_cn", "")))
        ).strip()
        similar = candidate.get("similar_papers", journal.get("similar_papers", []))
        paper_titles = [
            str(item.get("title", "")).strip()
            for item in similar if isinstance(item, dict) and str(item.get("title", "")).strip()
        ][:3]
        quality = journal_quality_snapshot(journal)
        metric = str(quality.get("metric_line", "")).strip() or "分区待核验"
        official_url = str(identity.get("official_url", journal.get("website", ""))).strip() or "待核验"
        updated = str(
            candidate.get("verified_at", identity.get("verified_at", journal.get("metadata_updated_at", "")))
        ).strip() or "待核验"
        lines = [
            f"身份来源 · {source_text}",
            f"ISSN · {issn_text}",
            f"官方主页 · {official_url}",
            f"EasyScholar · {metric}",
            "官方 Aims & Scope · " + (scope[:420] if scope else "尚未获取，投稿前以官网为准"),
            "相似论文 · " + ("；".join(paper_titles) if paper_titles else "暂无可展示证据"),
            f"数据更新时间 · {updated}",
        ]
        return "\n".join(lines)


class JournalSelectionDialog(QDialog):
    """AI-led journal selection with a usable local fallback at every state."""

    # Parent pages persist the selected candidate in their own data store.
    # Emitting instead of accepting keeps this full workbench open after an
    # import/path action so the user can compare the remaining recommendations.
    action_requested = Signal(str)

    def __init__(self, papers: list[dict[str, Any]], journals: list[dict[str, Any]], profile: dict[str, Any], parent: QWidget | None = None, *, history: dict[str, Any] | None = None, constraints: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self._papers = [deepcopy(item) for item in papers if isinstance(item, dict) and str(item.get("id", "")).strip()]
        self._journals = [deepcopy(item) for item in journals if isinstance(item, dict) and str(item.get("id", "")).strip()]
        self._profile = deepcopy(profile if isinstance(profile, dict) else {})
        self._history = deepcopy(history if isinstance(history, dict) else {})
        self._constraints = normalize_selection_requirements(constraints)
        self._ai_result: dict[str, Any] | None = None
        self._candidates: list[dict[str, Any]] = []
        self._ai_worker: JournalSelectionAiThread | None = None
        self._selection_action = "path"
        self._selected_candidate_id = ""

        self.setWindowTitle("为论文选刊 · 科研助手")
        self.setModal(True)
        # Opening from the compact widget must still reveal the full decision
        # surface.  This is deliberately a separate 1024×768 dialog rather
        # than another squeezed mini-panel inside the widget.
        self.setFixedSize(1024, 768)

        self._build_ui()
        self._refresh_candidates(reset_ai=True)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        title = QLabel("为论文选刊")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("先从相似真实论文发现期刊，再核验身份、硬条件和主题契合度；AI 只为已核验候选评分。")
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        # The upper pane remains bounded while results stay visible below.
        # Its summary and hard conditions sit side-by-side so every condition
        # is readable at the initial 1024×768 size.
        self.selection_splitter = QSplitter(Qt.Orientation.Vertical)
        self.selection_splitter.setObjectName("selectionWorkbenchSplitter")
        self.selection_splitter.setChildrenCollapsible(False)
        self.controls_scroll = QScrollArea()
        self.controls_scroll.setObjectName("selectionControlsScroll")
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls = QWidget()
        controls.setObjectName("selectionControlsPanel")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(1, 1, 4, 1)
        controls_layout.setSpacing(8)

        self.ai_status = QLabel("已拒稿、身份、出版社、已配置分区和主题最低线是硬条件；费用与时效只参与排序。")
        self.ai_status.setObjectName("selectionAiStatus")
        self.ai_status.setWordWrap(True)
        controls_layout.addWidget(self.ai_status)

        self.ai_progress = AiProgressPanel(object_name="selectionProgress")
        # Keep the historical attribute for integrations/tests that inspect
        # the raw bar directly.
        self.selection_progress = self.ai_progress.progress
        self.selection_progress.setObjectName("selectionProgressBar")
        controls_layout.addWidget(self.ai_progress)

        paper_row = QHBoxLayout()
        paper_row.setSpacing(7)
        paper_row.addWidget(QLabel("论文"))
        self.paper_combo = QComboBox()
        self.paper_combo.setObjectName("selectionPaperCombo")
        for paper in self._papers:
            self.paper_combo.addItem(str(paper.get("title", "未命名论文")).strip() or "未命名论文", str(paper.get("id", "")))
        self.paper_combo.currentIndexChanged.connect(lambda _index: self._refresh_candidates(reset_ai=True))
        paper_row.addWidget(self.paper_combo, 1)
        controls_layout.addLayout(paper_row)

        self.summary_scroll = QScrollArea()
        self.summary_scroll.setObjectName("selectionSummaryScroll")
        self.summary_scroll.setWidgetResizable(True)
        # Keep the long abstract in a compact scrollable preview so the
        # AI progress and recommendation pane remain visible in the fixed
        # 1024×768 decision workbench.
        self.summary_scroll.setMaximumHeight(150)
        self.summary_scroll.setMinimumHeight(126)
        self.summary_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.summary_preview = QLabel()
        self.summary_preview.setObjectName("selectionSummaryPreview")
        self.summary_preview.setWordWrap(True)
        self.summary_preview.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.summary_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.summary_scroll.setWidget(self.summary_preview)

        filter_frame = QFrame()
        filter_frame.setObjectName("selectionFilterSurface")
        filter_layout = QVBoxLayout(filter_frame)
        filter_layout.setContentsMargins(10, 9, 10, 9)
        filter_layout.setSpacing(7)
        filter_label = QLabel("投稿条件")
        filter_label.setObjectName("sectionLabel")
        filter_hint = QLabel("身份无法核验不展示；出版社与已配置分区必须符合。费用和时效只改变排序，不淘汰候选。")
        filter_hint.setObjectName("cardHint")
        filter_hint.setWordWrap(True)
        filter_layout.addWidget(filter_label)
        filter_layout.addWidget(filter_hint)
        self.filters_grid = QGridLayout()
        self.filters_grid.setContentsMargins(0, 0, 0, 0)
        self.filters_grid.setHorizontalSpacing(8)
        self.filters_grid.setVerticalSpacing(7)
        filter_layout.addLayout(self.filters_grid)

        self.publisher_combo = QComboBox()
        self.publisher_combo.setObjectName("selectionPublisher")
        self.publisher_combo.addItem("不限", "")
        publishers = sorted(
            {
                *{"Elsevier", "Springer Nature", "Wiley", "Taylor & Francis", "SAGE", "MDPI"},
                *{str(journal.get("publisher", "")).strip() for journal in self._journals if str(journal.get("publisher", "")).strip()},
            }
        )
        for publisher in publishers:
            self.publisher_combo.addItem(publisher, publisher)
        self._set_combo_data(self.publisher_combo, self._constraints["publishers"][0] if self._constraints["publishers"] else "")
        self.fee_combo = QComboBox()
        self.fee_combo.setObjectName("selectionFeeMode")
        self.fee_combo.addItem("不限", "")
        self.fee_combo.addItem("不付费", "no_fee")
        self.fee_combo.addItem("付费", "paid")
        self._set_combo_data(self.fee_combo, self._constraints["fee_modes"][0] if self._constraints["fee_modes"] else "")
        self.jcr_quartile_combo = MultiSelectComboBox("JCR 不限", [(f"Q{number}", f"Q{number}") for number in range(1, 5)])
        self.jcr_quartile_combo.setObjectName("selectionJcrMulti")
        self.jcr_quartile_combo.set_checked_values(self._constraints["jcr_quartiles"])
        self.cas_quartile_combo = MultiSelectComboBox("中科院不限", [(f"{number}区", str(number)) for number in range(1, 5)])
        self.cas_quartile_combo.setObjectName("selectionCasMulti")
        self.cas_quartile_combo.set_checked_values(self._constraints["cas_quartiles"])
        self.oa_mode = QComboBox()
        self.oa_mode.addItem("不限", "any")
        self.oa_mode.addItem("优先 OA", "prefer")
        self.oa_mode.addItem("仅 OA", "require")
        self._set_combo_data(self.oa_mode, self._constraints["oa_mode"])
        self.quartile_target = QComboBox()
        self.quartile_target.addItem("不限", "any")
        self.quartile_target.addItem("优先 Q1", "q1")
        self.quartile_target.addItem("优先 Q1-Q2", "q1_q2")
        self._set_combo_data(self.quartile_target, self._constraints["quartile_target"])
        self.speed_priority = QComboBox()
        self.speed_priority.setObjectName("selectionSpeedPriority")
        self.speed_priority.addItem("常规", "standard")
        self.speed_priority.addItem("时效优先", "priority")
        self.speed_priority.addItem("尽快见刊", "urgent")
        self._set_combo_data(self.speed_priority, self._constraints["speed_priority"])
        self.fit_strictness = QComboBox()
        self.fit_strictness.setObjectName("selectionFitStrictness")
        self.fit_strictness.addItem("宽松 · 55分", "lenient")
        self.fit_strictness.addItem("均衡 · 65分", "balanced")
        self.fit_strictness.addItem("严格 · 75分", "strict")
        self._set_combo_data(self.fit_strictness, self._constraints["fit_strictness"])
        self.filter_q34 = QCheckBox("过滤已知 Q3/Q4")
        self.filter_q34.setChecked(self._constraints["filter_known_q3_q4"])
        # Keep retired controls as hidden compatibility properties for old
        # integrations, but do not expose the former OA/目标分区 fields in
        # the new workbench.
        self.oa_mode.hide()
        self.quartile_target.hide()
        self.filter_q34.hide()
        rows = [
            ("出版社", self.publisher_combo),
            ("费用", self.fee_combo),
            ("发表时效", self.speed_priority),
            ("主题最低线", self.fit_strictness),
            ("JCR 分区", self.jcr_quartile_combo),
            ("中科院分区", self.cas_quartile_combo),
        ]
        self._filter_fields = [self._make_filter_field(label, control) for label, control in rows]
        self._relayout_filter_fields()
        for control in (self.publisher_combo, self.fee_combo, self.jcr_quartile_combo, self.cas_quartile_combo, self.speed_priority, self.fit_strictness):
            control.currentIndexChanged.connect(lambda _index: self._refresh_candidates(reset_ai=True))

        selection_body = QHBoxLayout()
        selection_body.setContentsMargins(0, 0, 0, 0)
        selection_body.setSpacing(10)
        selection_body.addWidget(self.summary_scroll, 4)
        selection_body.addWidget(filter_frame, 6)
        controls_layout.addLayout(selection_body)

        self.controls_scroll.setWidget(controls)
        self.selection_splitter.addWidget(self.controls_scroll)

        result_panel = QWidget()
        result_layout = QVBoxLayout(result_panel)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(6)
        result_heading = QHBoxLayout()
        self.candidate_heading = QLabel("AI 推荐")
        self.candidate_heading.setObjectName("sectionLabel")
        result_heading.addWidget(self.candidate_heading)
        result_heading.addStretch(1)
        self.candidate_hint = QLabel("点击开始后显示核验通过的结果")
        self.candidate_hint.setObjectName("cardHint")
        result_heading.addWidget(self.candidate_hint)
        result_layout.addLayout(result_heading)
        self.candidate_list = QListWidget()
        self.candidate_list.setObjectName("selectionResults")
        self.candidate_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.candidate_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.candidate_list.setMinimumHeight(180)
        self.candidate_list.currentRowChanged.connect(self._show_candidate_detail)
        self.empty_results_label = QLabel("尚未生成推荐")
        self.empty_results_label.setObjectName("selectionEmptyState")
        self.empty_results_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_results_label.setWordWrap(True)
        result_layout.addWidget(self.empty_results_label, 1)
        result_layout.addWidget(self.candidate_list)
        self.selection_splitter.addWidget(result_panel)
        root.addWidget(self.selection_splitter, 1)

        # Hidden compatibility state for callers/tests from the retired detail pane.
        self.detail_title = QLabel("选择一个期刊", self)
        self.selection_metric_line = QLabel("指标待核验", self)
        self.selection_metric_line.setObjectName("selectionMetricLine")
        self.score_equation = QLabel("本地预览：AI 总分已按当前筛选计算。", self)
        self.detail_body = QLabel("", self)
        for widget in (self.detail_title, self.selection_metric_line, self.score_equation, self.detail_body):
            widget.hide()

        actions = QHBoxLayout()
        actions.setSpacing(7)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        self.quick_import_button = QPushButton("快速入库")
        self.quick_import_button.setObjectName("subtleButton")
        self.quick_import_button.setToolTip("保存到期刊库，保留为待核验资料。")
        self.quick_import_button.clicked.connect(self._quick_import)
        self.quick_import_button.hide()
        self.ai_button = QPushButton("开始 AI 选刊")
        self.ai_button.setObjectName("selectionStartButton")
        self.ai_button.setToolTip("按出版社与分区硬条件多轮发现期刊，并用 EasyScholar 核验分区。")
        self.ai_button.clicked.connect(self._run_ai_recommendation)
        self.add_button = QPushButton("加入投稿路径")
        self.add_button.setObjectName("primaryButton")
        self.add_button.clicked.connect(self._accept_path)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        actions.addWidget(self.quick_import_button)
        actions.addWidget(self.ai_button)
        actions.addWidget(self.add_button)
        root.addLayout(actions)
        apply_dialog_theme(self)
        QTimer.singleShot(0, self._reflow_workbench)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    @staticmethod
    def _make_filter_field(label: str, control: QWidget) -> QWidget:
        field = QWidget()
        field.setObjectName("selectionFilterField")
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        heading = QLabel(label)
        heading.setObjectName("selectionFilterLabel")
        layout.addWidget(heading)
        layout.addWidget(control)
        return field

    def _relayout_filter_fields(self) -> None:
        if not hasattr(self, "filters_grid"):
            return
        while self.filters_grid.count():
            self.filters_grid.takeAt(0)
        if self.width() >= 800 and len(self._filter_fields) == 6:
            # A 3 + 3 arrangement keeps every hard condition visible in the
            # initial 1024×768 desktop workbench.
            for index, field in enumerate(self._filter_fields):
                row, column = divmod(index, 3)
                self.filters_grid.addWidget(field, row, column)
            for column in range(3):
                self.filters_grid.setColumnStretch(column, 1)
            return
        columns = 2 if self.width() >= 560 else 1
        for index, field in enumerate(self._filter_fields):
            row, column = divmod(index, columns)
            self.filters_grid.addWidget(field, row, column)
            self.filters_grid.setColumnStretch(column, 1)

    def _reflow_workbench(self) -> None:
        if not hasattr(self, "selection_splitter"):
            return
        self._relayout_filter_fields()
        available = self.selection_splitter.height()
        if available > 0:
            result_height = max(180, int(available * 0.56))
            control_height = max(140, available - result_height)
            if control_height + result_height > available:
                control_height = max(80, available - result_height)
            self.selection_splitter.setSizes([control_height, result_height])
        self._fit_recommendation_rows()

    def _selected_paper(self) -> dict[str, Any]:
        paper_id = str(self.paper_combo.currentData() or "")
        return next((item for item in self._papers if str(item.get("id", "")) == paper_id), {})

    def _current_requirements(self) -> dict[str, Any]:
        publisher = str(self.publisher_combo.currentData() or "").strip()
        fee = str(self.fee_combo.currentData() or "").strip()
        return normalize_selection_requirements({
            "speed_priority": self.speed_priority.currentData(),
            "fit_strictness": self.fit_strictness.currentData(),
            "filter_known_q3_q4": False,
            "publishers": [publisher] if publisher else [],
            "fee_modes": [fee] if fee else [],
            "jcr_quartiles": self.jcr_quartile_combo.checked_values(),
            "cas_quartiles": self.cas_quartile_combo.checked_values(),
            "rejected_journal_names": self._rejected_journal_names(self._selected_paper()),
        })

    @staticmethod
    def _rejected_journal_names(paper: dict[str, Any]) -> list[str]:
        journals = paper.get("journals", []) if isinstance(paper, dict) else []
        names: list[str] = []
        seen: set[str] = set()
        for journal in journals if isinstance(journals, list) else []:
            if not isinstance(journal, dict) or str(journal.get("status", "")).strip() != "拒稿":
                continue
            name = str(journal.get("name", "")).strip()
            key = canonical_journal_name(name)
            if name and key not in seen:
                seen.add(key)
                names.append(name)
        paper_id = str(paper.get("id", "")).strip() if isinstance(paper, dict) else ""
        paper_title = canonical_journal_name(paper.get("title", "")) if isinstance(paper, dict) else ""
        for entry in load_rejection_archive():
            if not isinstance(entry, dict):
                continue
            archived_id = str(entry.get("paper_id", "")).strip()
            archived_title = canonical_journal_name(entry.get("paper_title", ""))
            if paper_id and archived_id:
                same_paper = paper_id == archived_id
            else:
                same_paper = bool(paper_title and archived_title and paper_title == archived_title)
            if not same_paper:
                continue
            name = str(entry.get("journal_name", entry.get("journal", ""))).strip()
            key = canonical_journal_name(name)
            if name and key and key not in seen:
                seen.add(key)
                names.append(name)
        if paper_id:
            for name in selection_excluded_journal_names(paper_id):
                key = canonical_journal_name(name)
                if key and key not in seen:
                    seen.add(key)
                    names.append(name)
        return names

    def _refresh_candidates(self, *, reset_ai: bool = False) -> None:
        if reset_ai:
            self._ai_result = None
            self._candidates = []
            self._selected_candidate_id = ""
            self.ai_status.setText("条件已更新。开始后先从相似真实论文发现期刊，再分轮补充、核验和评分。")
        paper = self._selected_paper()
        summary = str(paper.get("summary", "")).strip()
        preview = "研究摘要：" + summary if summary else "研究摘要：尚未填写。AI 可结合题目和关键词预览，但建议先补充摘要。"
        self.summary_preview.setText(preview)
        self.summary_preview.setMinimumHeight(min(420, max(58, 30 + (len(preview) // 26) * 16)))
        current_id = self._selected_candidate_id
        if self._ai_result:
            ranked = self._ai_result.get("ranked", []) if isinstance(self._ai_result.get("ranked", []), list) else []
            external = self._ai_result.get("external_candidates", []) if isinstance(self._ai_result.get("external_candidates", []), list) else []
            self._candidates = [deepcopy(item) for item in [*ranked, *external] if isinstance(item, dict)]
        else:
            self._candidates = []
        self._render_candidate_list(current_id)

    @staticmethod
    def _candidate_key(candidate: dict[str, Any]) -> str:
        return str(candidate.get("result_id", candidate.get("journal_id", ""))).strip()

    def _render_candidate_list(self, preferred_id: str = "") -> None:
        self.candidate_list.blockSignals(True)
        self.candidate_list.clear()
        selected_row = -1
        for index, candidate in enumerate(self._candidates):
            candidate_key = self._candidate_key(candidate)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, candidate_key)
            item.setToolTip(str(candidate.get("journal_name", "")))
            self.candidate_list.addItem(item)
            row = RecommendationRow(
                candidate,
                lambda _checked=False, result_id=candidate_key: self._act_on_candidate(result_id),
                self.candidate_list,
                library_action=lambda _checked=False, result_id=candidate_key: self._import_candidate(result_id),
                exclude_action=lambda _checked=False, result_id=candidate_key: self._exclude_candidate(result_id),
            )
            row.height_changed.connect(self._fit_recommendation_rows)
            item.setSizeHint(QSize(0, row.sizeHint().height()))
            self.candidate_list.setItemWidget(item, row)
            if candidate_key == preferred_id:
                selected_row = index
        self.candidate_list.blockSignals(False)
        self.candidate_heading.setText(f"AI 推荐结果 · {len(self._candidates)} 本" if self._ai_result else "AI 推荐结果")
        has_results = bool(self.candidate_list.count())
        self.candidate_list.setVisible(has_results)
        self.empty_results_label.setVisible(not has_results)
        if has_results:
            self.candidate_list.setCurrentRow(max(0, selected_row))
        else:
            self._show_candidate_detail(-1)
        self.ai_button.setEnabled(bool(self._selected_paper()) and self._ai_worker is None)
        self.add_button.setEnabled(bool(self._current_candidate()) and bool(self._selected_paper()) and bool(self._ai_result))
        QTimer.singleShot(0, self._fit_recommendation_rows)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        QTimer.singleShot(0, self._reflow_workbench)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        QTimer.singleShot(0, self._reflow_workbench)

    def _fit_recommendation_rows(self) -> None:
        width = self.candidate_list.viewport().width()
        if width <= 0:
            return
        for index in range(self.candidate_list.count()):
            item = self.candidate_list.item(index)
            row = self.candidate_list.itemWidget(item)
            if row is None:
                continue
            row.setFixedWidth(width)
            height = max(row.preferred_height(width), row.minimumSizeHint().height()) if isinstance(row, RecommendationRow) else row.sizeHint().height()
            row.setMinimumHeight(height)
            item.setSizeHint(QSize(width, height))
        self.candidate_list.doItemsLayout()

    def _select_candidate(self, result_id: str) -> None:
        for index, candidate in enumerate(self._candidates):
            if self._candidate_key(candidate) == result_id:
                self._selected_candidate_id = result_id
                self.candidate_list.setCurrentRow(index)
                return

    def _act_on_candidate(self, result_id: str) -> None:
        self._select_candidate(result_id)
        self._accept_path()

    def _exclude_candidate(self, result_id: str) -> None:
        self._select_candidate(result_id)
        self._emit_action("exclude")

    def _current_candidate(self) -> dict[str, Any]:
        if self._selected_candidate_id:
            chosen = next((item for item in self._candidates if self._candidate_key(item) == self._selected_candidate_id), None)
            if isinstance(chosen, dict):
                return chosen
        row = self.candidate_list.currentRow()
        return self._candidates[row] if 0 <= row < len(self._candidates) else {}

    def _show_candidate_detail(self, _row: int) -> None:
        if 0 <= _row < len(self._candidates):
            self._selected_candidate_id = self._candidate_key(self._candidates[_row])
        candidate = self._current_candidate()
        if not candidate:
            self.detail_title.setText("没有符合当前条件的期刊")
            self.selection_metric_line.setText("指标待核验")
            self.score_equation.setText("尚未开始 AI 选刊")
            self.detail_body.setText("先确认筛选条件，再开始多轮 AI 选刊。")
            self.quick_import_button.hide()
            self.add_button.setEnabled(False)
            return
        external = bool(candidate.get("is_external", False))
        self.detail_title.setText(str(candidate.get("journal_name", "未命名期刊")))
        quality = journal_quality_snapshot(candidate.get("journal", {}) if isinstance(candidate.get("journal", {}), dict) else {})
        metric_line = str(quality.get("metric_line", "")).strip()
        self.selection_metric_line.setText(metric_line or "指标待核验")
        self.score_equation.setText(f"AI 总分 {int(candidate.get('ai_total_score', candidate.get('total_score', 0)) or 0)}")
        details = ["推荐理由\n" + (str(candidate.get("reason_cn", "")).strip() or "本地筛选结果，等待 AI 结合摘要复核。")]
        if external:
            details.append("AI 扩展候选：已完成 EasyScholar 分区核验；投稿前仍请以期刊官网的费用和时效说明为准。" if metric_line else "AI 扩展候选：JCR、OA 与处理周期均待核验。")
        risk = str(candidate.get("risk_cn", "")).strip()
        if risk:
            details.append("风险\n" + risk)
        verification = str(candidate.get("verification_reason", "")).strip()
        if verification and verification != risk:
            details.append("核验状态\n" + verification)
        self.detail_body.setText("\n\n".join(details))
        self.quick_import_button.setHidden(not external)
        if external:
            imported = bool(candidate.get("library_imported"))
            self.quick_import_button.setText("已加入期刊库" if imported else "快速入库")
            self.quick_import_button.setEnabled(not imported)
        path_added = bool(candidate.get("path_added"))
        self.add_button.setText(
            "已入库并加入投稿路径" if external and path_added else
            "已加入投稿路径" if path_added else
            "入库并加入投稿路径" if external else
            "加入投稿路径"
        )
        self.add_button.setEnabled(not path_added)

    def _filter_rejected_candidates(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Defensively remove rejected names even when a provider bypasses its prompt."""
        rejected = {
            canonical_journal_name(value)
            for value in self._current_requirements().get("rejected_journal_names", [])
            if canonical_journal_name(value)
        }
        if not rejected:
            return candidates
        result: list[dict[str, Any]] = []
        for candidate in candidates:
            journal = candidate.get("journal", {}) if isinstance(candidate.get("journal", {}), dict) else {}
            name = str(candidate.get("journal_name", candidate.get("name", journal.get("name", "")))).strip()
            if name and canonical_journal_name(name) in rejected:
                continue
            result.append(candidate)
        return result

    def apply_ai_recommendation(self, result: dict[str, Any]) -> None:
        """Apply a verified iterative result or a compatible legacy payload."""
        current_id = self._candidate_key(self._current_candidate())
        raw = deepcopy(result if isinstance(result, dict) else {})
        v12_results = raw.get("results", []) if isinstance(raw.get("results", []), list) else []
        if v12_results:
            raw["ranked"] = v12_results
            raw["external_candidates"] = []
        ranked = raw.get("ranked", []) if isinstance(raw.get("ranked", []), list) else []
        external = raw.get("external_candidates", []) if isinstance(raw.get("external_candidates", []), list) else []
        ranked = self._filter_rejected_candidates([item for item in ranked if isinstance(item, dict)])
        external = self._filter_rejected_candidates([item for item in external if isinstance(item, dict)])
        raw["ranked"] = ranked
        raw["external_candidates"] = external
        raw["ai_qualified_count"] = len(ranked) + len(external)
        direct_candidates = (
            bool(ranked or external) or "verified_count" in raw or "rounds" in raw
        ) and all(
            isinstance(item, dict) and ("journal" in item or "journal_name" in item)
            for item in [*ranked, *external]
        )
        if direct_candidates:
            self._ai_result = raw
        else:
            # Older callers pass a provider patch keyed by journal id.  Keep
            # that contract without reintroducing an initial local preview.
            candidates = rank_ai_first_candidates(
                self._selected_paper(),
                self._journals,
                self._profile,
                self._history,
                self._current_requirements(),
                recommendation=raw,
            )
            self._ai_result = {"ranked": candidates, "external_candidates": []}
        self._refresh_candidates(reset_ai=False)
        if current_id:
            self._render_candidate_list(current_id)

    def apply_ai_patch(self, patch: dict[str, Any]) -> None:
        """Compatibility bridge for older callers that supplied score adjustments."""
        ranked = []
        for item in patch.get("ranked", []) if isinstance(patch, dict) else []:
            if not isinstance(item, dict):
                continue
            try:
                fit_score = max(0, min(100, 70 + int(item.get("adjustment", 0))))
            except (TypeError, ValueError):
                fit_score = 70
            ranked.append({"id": item.get("id", ""), "fit_score": fit_score, "reason_cn": item.get("reason_cn", "")})
        self.apply_ai_recommendation({"ranked": ranked, "external_candidates": []})

    def _run_ai_recommendation(self) -> None:
        if self._ai_worker is not None and self._ai_worker.isRunning():
            self.ai_status.setText("AI 正在分轮寻找并核验期刊，请稍候。")
            self.ai_progress.update("AI 正在分轮寻找并核验期刊，请稍候。", self.selection_progress.value())
            return
        # Surface feedback before readiness checks so a click can never look
        # like a no-op, even when the provider is not configured.
        self.ai_progress.begin("正在检查 AI 配置…")
        self.ai_button.setEnabled(False)
        self.ai_button.setText("AI 选刊中…")
        if not is_deepseek_ready("journal_recommendation"):
            self.ai_status.setText("AI 尚未配置：请在设置中配置 AI 后再开始选刊。")
            self.ai_progress.fail("AI 尚未配置，未开始联网选刊。")
            self.ai_button.setText("开始 AI 选刊")
            self.ai_button.setEnabled(bool(self._selected_paper()))
            return
        paper = self._selected_paper()
        if not paper:
            self.ai_status.setText("请选择论文后再运行 AI 主推荐。")
            self.ai_progress.fail("请选择论文后再运行 AI 主推荐。")
            self.ai_button.setText("开始 AI 选刊")
            self.ai_button.setEnabled(False)
            return
        self.add_button.setEnabled(False)
        self.quick_import_button.hide()
        self._candidates = []
        self._selected_candidate_id = ""
        self._render_candidate_list()
        self.ai_button.setEnabled(False)
        self.ai_progress.update("正在准备论文，并连接 OpenAlex、Semantic Scholar 与 Crossref。", 4)
        self.ai_status.setText("正在准备论文，并连接 OpenAlex、Semantic Scholar 与 Crossref。")
        self._ai_worker = JournalSelectionAiThread(paper, self._journals, self._profile, self._current_requirements(), self)
        self._ai_worker.completed.connect(self._ai_recommendation_finished)
        self._ai_worker.failed.connect(self._ai_recommendation_failed)
        self._ai_worker.progress.connect(self._ai_recommendation_progress)
        self._ai_worker.finished.connect(self._clear_ai_worker)
        self._ai_worker.start()

    def _ai_recommendation_progress(self, message: str, value: int) -> None:
        self.ai_progress.update(str(message), max(0, min(100, int(value))))
        self.ai_status.setText(str(message))

    def _ai_recommendation_finished(self, result: dict[str, Any]) -> None:
        self.ai_progress.complete("AI 正在收尾并整理结果…")
        error = str(result.get("error_cn", "")).strip() if isinstance(result, dict) else ""
        self.apply_ai_recommendation(result)
        if error:
            self.ai_status.setText(error)
            return
        fallback = str(result.get("fallback_reason_cn", "")).strip()
        try:
            ai_count = max(0, int(result.get("ai_qualified_count", len(result.get("ranked", [])) + len(result.get("external_candidates", [])))))
        except (TypeError, ValueError):
            ai_count = 0
        verification_mode = str(result.get("verification_mode", "")).strip().casefold()
        if verification_mode == "skipped":
            message = f"选刊完成：保留 {ai_count} 本候选；期刊身份已核验，未配置 EasyScholar，因此没有执行分区硬筛。"
        elif ai_count:
            message = f"选刊完成：{ai_count} 本期刊通过身份、出版社、分区和主题最低线。"
        else:
            message = "本轮没有期刊通过全部硬条件：身份、出版社、分区和主题最低线。可放宽一个条件后重试。"
        if fallback:
            message += " " + fallback
        self.ai_status.setText(message)

    def _ai_recommendation_failed(self, message: str) -> None:
        self.ai_progress.fail("AI 选刊未完成：" + str(message))
        self.ai_status.setText("AI 选刊未完成：" + str(message))

    def _clear_ai_worker(self) -> None:
        if self._ai_worker is not None:
            self._ai_worker.deleteLater()
        self._ai_worker = None
        self.ai_button.setEnabled(bool(self._selected_paper()))
        self.ai_button.setText("开始 AI 选刊")

    def _emit_action(self, action: str) -> None:
        candidate = self._current_candidate()
        if not candidate:
            self.ai_status.setText("当前没有可操作的期刊。")
            return
        if action == "import" and candidate.get("library_imported"):
            self.ai_status.setText("这本期刊已经在期刊库中。")
            return
        if action == "path" and candidate.get("path_added"):
            self.ai_status.setText("这本期刊已经加入投稿路径。")
            return
        self._selection_action = action
        self.ai_status.setText("正在保存本次操作，完成后仍可继续比较推荐结果…")
        self.action_requested.emit(action)

    def _import_candidate(self, result_id: str) -> None:
        self._select_candidate(result_id)
        self._quick_import()

    def _accept_path(self) -> None:
        if not self._current_candidate():
            self.ai_status.setText("当前没有可加入投稿路径的期刊。")
            return
        self._emit_action("path")

    def _quick_import(self) -> None:
        if not self.is_external_selection():
            self.ai_status.setText("请选择待核验的 AI 扩展候选后快速入库。")
            return
        self._emit_action("import")

    def mark_action_complete(self, action: str, success: bool, message: str) -> None:
        """Reflect a parent-page persistence result without closing the dialog."""
        candidate_id = self._candidate_key(self._current_candidate())
        candidate = next(
            (item for item in self._candidates if self._candidate_key(item) == candidate_id),
            None,
        )
        if success and isinstance(candidate, dict):
            if action == "import":
                candidate["library_imported"] = True
            elif action == "path":
                candidate["path_added"] = True
                if candidate.get("is_external"):
                    candidate["library_imported"] = True
            elif action == "exclude":
                self._candidates = [
                    item for item in self._candidates if self._candidate_key(item) != candidate_id
                ]
                candidate_id = ""
        self.ai_status.setText(str(message))
        self._render_candidate_list(candidate_id)

    def selection(self) -> tuple[str, str]:
        return str(self.paper_combo.currentData() or ""), str(self._current_candidate().get("journal_id", ""))

    def selected_journal(self) -> dict[str, Any]:
        journal = self._current_candidate().get("journal", {})
        return deepcopy(journal) if isinstance(journal, dict) else {}

    def selected_candidate(self) -> dict[str, Any]:
        return deepcopy(self._current_candidate())

    def is_external_selection(self) -> bool:
        return bool(self._current_candidate().get("is_external", False))

    def selection_action(self) -> str:
        return self._selection_action
