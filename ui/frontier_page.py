from __future__ import annotations

from datetime import date, datetime
from copy import deepcopy
from uuid import uuid4

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QSizePolicy,
)

from ui.dialogs import show_undo_toast
from ui.ai_progress import AiProgressPanel
from ui.frontier_settings_dialog import FrontierSettingsDialog as V115FrontierSettingsDialog
from ui.page_kit import ElidedLabel, EllipsisMenu, PageHeader

from utils.ai_service import (
    DeepSeekConfigurationError,
    DeepSeekRequestError,
    get_ai_settings,
    is_deepseek_ready,
    classify_profile_comment_with_ai,
    organize_research_profile_with_ai,
    rerank_frontier_with_ai,
)

from utils.file_manager import (
    commit_frontier_v13_data,
    load_frontier_data,
    load_achievements,
    load_journal_library,
    load_papers,
    load_readings,
    load_inspirations,
    record_frontier_feedback_event,
    save_frontier_data,
    save_inspirations,
    save_journal_library,
    save_readings,
)
from utils.frontier_scoring import apply_frontier_ranking, canonical_text, classify_feedback_locally
from utils.easyscholar_service import is_easyscholar_ready
from utils.journal_health_service import import_frontier_journal
from utils.research_profile_repository import (
    apply_organization_transaction,
    load_research_profile,
    save_research_profile,
    should_auto_organize,
    undo_last_organization,
)
from utils.research_signal_service import apply_profile_comment, record_signal
from utils.frontier_service import (
    NON_JOURNAL_VENUES,
    frontier_cache_is_stale,
    select_daily_recommendations,
    update_daily_frontier,
    update_daily_frontier_v13,
)
from utils.v13_policy import (
    dynamic_pyramid,
    frontier_display_decision,
    score_frontier_item,
    work_fingerprint,
)


_SEEDED_RECALL_LABELS = {
    "semantic_related": "语义相似",
    "citation_network": "引文网络",
    "confirmed_author_team": "确认作者团队",
}


def recall_seed_guidance(outcomes: object) -> str:
    """Explain genuine seed gaps without disguising them as source failures."""

    if not isinstance(outcomes, dict):
        return ""
    missing = [
        label
        for key, label in _SEEDED_RECALL_LABELS.items()
        if isinstance(outcomes.get(key), dict)
        and str(outcomes[key].get("status", "")).strip().upper() == "NO_SEED"
    ]
    if not missing:
        return ""
    return (
        f"{'、'.join(missing)}尚无已确认种子；"
        "在论文上点“相关”可启用语义与引文召回，确认作者身份后可启用作者团队召回"
    )
from utils import file_manager
from utils.frontier_service import merge_frontier_refresh_item


class FrontierSourcesDialog(QDialog):
    """Choose the six mandatory discovery sources without storing credentials here."""

    SOURCE_ROWS = (
        ("crossref", "Crossref", "最新 DOI、期刊与出版日期 · 无需 Key"),
        ("openalex", "OpenAlex", "综合论文与开放元数据 · Key 可选"),
        ("doaj", "DOAJ", "开放获取论文与来源关键词 · 无需 Key"),
        ("semantic_scholar", "Semantic Scholar", "相似论文与摘要 · Key 可在总设置中配置"),
        ("europe_pmc", "Europe PMC", "生命与环境交叉文献 · 无需 Key"),
        ("arxiv", "arXiv", "预印本补充（遥感 / 方法类）· 无需 Key"),
    )

    def __init__(self, sources: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sources = deepcopy(sources if isinstance(sources, dict) else {})
        self._controls: dict[str, QCheckBox] = {}
        self.setWindowTitle("每日前沿数据源")
        parent_width = parent.width() if parent else 500
        self.setMinimumWidth(360)
        self.setMaximumWidth(max(360, min(560, parent_width - 18)))
        self.resize(max(360, min(500, parent_width - 18)), 520)
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QLabel, QCheckBox { color: #f4f6ff; }
            #sourceHint { color: #aec7e0; font-size: 11px; }
            #sourceCard { background: #1a2946; border: 1px solid #526b91; border-radius: 7px; }
            QDialogButtonBox QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 14px; }
            QDialogButtonBox QPushButton[text="保存"] { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(9)
        title = QLabel("每日前沿六个核心数据源")
        title.setObjectName("settingsTitle")
        root.addWidget(title)
        hint = QLabel("六个核心来源均默认启用并独立验收。需要 Key 的来源请到“总设置 → 智能增强与数据源”配置；密钥只加密保存在本机。")
        hint.setObjectName("sourceHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        source_content = QWidget()
        source_rows = QVBoxLayout(source_content)
        source_rows.setContentsMargins(1, 1, 5, 1)
        source_rows.setSpacing(7)
        for source_id, name, description in self.SOURCE_ROWS:
            current = self._sources.get(source_id, {})
            current = current if isinstance(current, dict) else {}
            card = QFrame()
            card.setObjectName("sourceCard")
            box = QVBoxLayout(card)
            box.setContentsMargins(10, 8, 10, 8)
            box.setSpacing(5)
            enabled = QCheckBox(f"使用 {name}")
            enabled.setChecked(bool(current.get("enabled", True)))
            detail = QLabel(description)
            detail.setObjectName("sourceHint")
            box.addWidget(enabled)
            box.addWidget(detail)
            source_rows.addWidget(card)
            self._controls[source_id] = enabled
        source_rows.addStretch()
        scroll.setWidget(source_content)
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def sources(self) -> dict:
        result: dict[str, dict] = {}
        for source_id, enabled in self._controls.items():
            current = self._sources.get(source_id, {})
            current = current if isinstance(current, dict) else {}
            clean = {key: deepcopy(value) for key, value in current.items() if key != "api_key"}
            clean.update(
                {
                    "enabled": enabled.isChecked(),
                    "credential_ref": f"settings.data_sources.{source_id}",
                }
            )
            result[source_id] = clean
        return result


class FrontierJournalPriorityDialog(QDialog):
    """Compact subscription controls launched from research settings."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.journals = load_journal_library()
        self.controls: list[tuple[dict, QComboBox]] = []
        self.setWindowTitle("优先期刊")
        parent_width = parent.width() if parent else 520
        self.setMinimumWidth(360)
        self.setMaximumWidth(max(360, min(570, parent_width - 18)))
        self.resize(max(360, min(520, parent_width - 18)), 540)
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QLabel { color: #f4f6ff; }
            #journalPriorityCard { background: #1a2946; border: 1px solid #526b91; border-radius: 6px; }
            #journalHint { color: #aec7e0; font-size: 11px; }
            QScrollArea, QScrollArea::viewport { background: #101a36; border: 0; }
            #journalPriorityContent { background: #101a36; }
            QScrollBar:vertical { background: #101a36; width: 7px; margin: 2px 0; }
            QScrollBar::handle:vertical { background: #385373; min-height: 28px; border-radius: 3px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QComboBox { background: #243452; color: #ffffff; border: 1px solid #5d779f; border-radius: 5px; padding: 5px 27px 5px 8px; }
            QComboBox::drop-down { width: 21px; border: 0; border-left: 1px solid #5d779f; border-top-right-radius: 5px; border-bottom-right-radius: 5px; background: #29405f; }
            QComboBox::drop-down:hover { background: #365477; }
            QComboBox::down-arrow { width: 0; height: 0; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid #c7ddf3; margin-right: 6px; }
            QComboBox QAbstractItemView { background: #20314f; color: #ffffff; selection-background-color: #2e78b7; selection-color: #ffffff; border: 1px solid #5d779f; outline: 0; }
            QComboBox QAbstractItemView::item { min-height: 23px; padding: 4px 8px; color: #ffffff; }
            QComboBox QAbstractItemView::item:selected { background: #2e78b7; color: #ffffff; }
            QDialogButtonBox QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 14px; }
            QDialogButtonBox QPushButton[text="保存"] { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(9)
        root.addWidget(QLabel("优先期刊"))
        hint = QLabel("优先期刊会被额外检索和加分，但仍必须满足至少两个关键词的匹配规则。")
        hint.setObjectName("journalHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("journalPriorityContent")
        rows = QVBoxLayout(content)
        rows.setContentsMargins(1, 1, 5, 1)
        rows.setSpacing(6)
        order = {"必看": 0, "关注": 1, "扩展": 2, "不订阅": 3}
        for journal in sorted(self.journals, key=lambda item: (order.get(str(item.get("frontier_priority", "")), 3), str(item.get("name", "")).casefold())):
            card = QFrame()
            card.setObjectName("journalPriorityCard")
            row = QHBoxLayout(card)
            row.setContentsMargins(9, 6, 9, 6)
            name = QLabel(str(journal.get("name", "")))
            name.setWordWrap(True)
            row.addWidget(name, 1)
            combo = QComboBox()
            combo.addItems(["不订阅", "必看", "关注", "扩展"])
            combo.setCurrentText(str(journal.get("frontier_priority", "不订阅")))
            row.addWidget(combo)
            rows.addWidget(card)
            self.controls.append((journal, combo))
        rows.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _save(self) -> None:
        for journal, combo in self.controls:
            journal["frontier_priority"] = combo.currentText()
        save_journal_library(self.journals)
        self.accept()


class FrontierRefreshThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(
        self,
        data: dict,
        journals: list[dict],
        parent: QWidget | None = None,
        *,
        manual: bool = False,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._journals = journals
        self._manual = bool(manual)

    def run(self) -> None:
        try:
            self.completed.emit(
                update_daily_frontier_v13(
                    self._data,
                    self._journals,
                    manual=self._manual,
                    progress=lambda message, value=0: self.progress.emit(str(message), int(value or 0)),
                )
            )
        except Exception as error:
            self.failed.emit(str(error))


class FrontierAiRerankThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, profile: dict, items: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profile = deepcopy(profile)
        self._items = [dict(item) for item in items]

    def run(self) -> None:
        try:
            response = rerank_frontier_with_ai(
                self._profile,
                self._items,
                progress=lambda message, value=0: self.progress.emit(str(message), int(value or 0)),
            )
            patches = {
                str(value.get("id", "")): value
                for value in response.get("ranked", [])
                if isinstance(value, dict) and str(value.get("id", ""))
            }
            journals = load_journal_library()
            journals_by_id = {
                str(value.get("id", "")): value
                for value in journals
                if isinstance(value, dict) and str(value.get("id", ""))
            }
            journals_by_name = {
                canonical_text(value.get("name", "")): value
                for value in journals
                if isinstance(value, dict) and str(value.get("name", "")).strip()
            }
            settings = file_manager.load_app_settings()
            jcr_settings = settings.get("jcr", {}) if isinstance(settings.get("jcr"), dict) else {}
            jcr_available = bool(
                (jcr_settings.get("enabled") and jcr_settings.get("api_key_secret") and jcr_settings.get("endpoint_template"))
                or is_easyscholar_ready()
            )
            reviewed: list[dict] = []
            for raw in self._items:
                patch = patches.get(str(raw.get("id", "")))
                if patch is None:
                    continue
                journal = journals_by_id.get(str(raw.get("library_journal_id", "")))
                if journal is None:
                    journal = journals_by_name.get(canonical_text(raw.get("journal", "")))
                rescored = score_frontier_item(
                    raw,
                    self._profile,
                    journal,
                    today=date.today(),
                    ai_payload=patch.get("ai_axis_payload"),
                )
                rescored["ai_axis_payload"] = deepcopy(patch.get("ai_axis_payload", {}))
                rescored["ai_summary_cn"] = str(patch.get("summary_cn", "")).strip()
                decision = frontier_display_decision(
                    rescored,
                    jcr_service_available=jcr_available,
                    show_preprints=bool(self._profile.get("show_preprints", True)),
                )
                rescored.update(
                    {
                        "candidate_state": decision["state"],
                        "display_reason": decision["reason"],
                        "missing_fields": decision.get("missing_fields", []),
                        "display_label": decision.get("label", ""),
                        "content_decision": "accept" if decision["visible"] else "pending",
                        "quality_gate_state": "preprint" if rescored.get("is_preprint") else "eligible" if decision["visible"] else "withheld",
                    }
                )
                reviewed.append(rescored)
            self.completed.emit({"reviewed": reviewed})
        except (DeepSeekConfigurationError, DeepSeekRequestError) as error:
            self.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001 - provider failures must not terminate the desktop widget
            self.failed.emit(str(error))


class FrontierProfileAiThread(QThread):
    """Run the once-daily profile calibration away from the desktop UI."""

    completed = Signal(dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, profile: dict, evidence: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profile = deepcopy(profile)
        self._evidence = deepcopy(evidence if isinstance(evidence, dict) else {})

    def run(self) -> None:
        try:
            result = organize_research_profile_with_ai(
                self._profile,
                self._evidence,
                lambda message, value=0: self.progress.emit(str(message), int(value or 0)),
            )
            self.completed.emit(result)
        except (DeepSeekConfigurationError, DeepSeekRequestError) as error:
            self.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001 - provider failures must not terminate the desktop widget
            self.failed.emit(str(error))


class FrontierCommentAiThread(QThread):
    """Classify one explicit preference without blocking the compact page."""

    completed = Signal(str, str, dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, item: dict, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item = dict(item)
        self._text = str(text).strip()

    def run(self) -> None:
        try:
            result = classify_profile_comment_with_ai(
                self._text,
                {
                    "id": str(self._item.get("id", "")),
                    "title": str(self._item.get("title", "")),
                    "journal": str(self._item.get("journal", "")),
                    "matched_terms": self._item.get("matched_terms", self._item.get("match_terms", [])),
                },
                lambda message, value=0: self.progress.emit(str(message), int(value or 0)),
            )
            self.completed.emit(str(self._item.get("id", "")), self._text, result)
        except (DeepSeekConfigurationError, DeepSeekRequestError) as error:
            self.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001 - malformed provider output stays away from the profile
            self.failed.emit(str(error))


class FrontierCard(QFrame):
    add_reading_requested = Signal(str)
    add_inspiration_requested = Signal(str)
    relevant_requested = Signal(str)
    irrelevant_requested = Signal(str)
    too_broad_requested = Signal(str)
    read_requested = Signal(str)
    restore_requested = Signal(str)
    feedback_requested = Signal(str, str)
    import_journal_requested = Signal(str)
    detail_opened = Signal(str)

    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item = item
        self.setObjectName("frontierCard")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        summary = QFrame()
        summary.setObjectName("frontierRecommendationRow")
        summary.setMinimumWidth(0)
        summary_box = QVBoxLayout(summary)
        summary_box.setContentsMargins(0, 0, 0, 0)
        summary_box.setSpacing(4)
        title = QLabel(str(item.get("title", "")))
        title.setObjectName("frontierTitle")
        title.setWordWrap(True)
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        summary_box.addWidget(title)

        meta_row = QHBoxLayout()
        meta_row.setSpacing(5)
        meta_parts = [str(item.get("journal", "")).strip(), str(item.get("published_date", "")).strip()]
        meta = QLabel(" · ".join(part for part in meta_parts if part) or "来源待补全")
        meta.setObjectName("cardDetail")
        meta.setWordWrap(True)
        meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        meta_row.addWidget(meta, 1)
        summary_box.addLayout(meta_row)

        quality_row = QHBoxLayout()
        quality_row.setSpacing(5)
        is_preprint = bool(item.get("is_preprint")) or str(item.get("quality_gate_state", "")) == "preprint"
        is_v13 = isinstance(item.get("relevance_axis"), dict) and isinstance(item.get("value_axis"), dict)
        total_value = int(item.get("score", 0) or 0)
        content_value = int(item.get("content_score", item.get("ai_score", -1)) or 0)
        relevance_value = int(item.get("relevance_score", 0) or 0)
        research_value = int(item.get("research_value_score", 0) or 0)
        score_summary = QLabel(
            f"相关 {relevance_value} · 价值 {research_value}"
            if is_v13
            else f"综合 {total_value}" + (f" · 内容 {content_value}" if content_value >= 0 else "")
        )
        score_summary.setObjectName("frontierScoreSummary")
        score_summary.setToolTip("展开更多可查看评分构成、分区和发现来源")
        quality_row.addWidget(score_summary)
        diagnostic_badges: list[QLabel] = []
        score = QLabel(
            f"相关 {relevance_value}"
            if is_v13
            else f"综合 {total_value}"
        )
        score.setToolTip(
            "研究相关轴：本地规则最高 66 分；AI 有有效证据时最多补充 34 分"
            if is_v13
            else "综合分用于排序；内容最低线和 JCR 准入门槛在计分前独立执行"
        )
        score.setObjectName("frontierScore")
        score.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        diagnostic_badges.append(score)
        content_score = QLabel(
            f"价值 {int(item.get('research_value_score', 0) or 0)}"
            if is_v13
            else f"内容 {content_value}" if content_value >= 0 else "内容待复核"
        )
        content_score.setObjectName("frontierContentScore")
        content_score.setToolTip(
            "科研价值轴：本地规则最高 66 分；AI 有有效证据时最多补充 34 分"
            if is_v13
            else "AI 内容相关性分"
        )
        content_score.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        diagnostic_badges.append(content_score)
        if is_v13:
            level = str(item.get("pyramid_level", "")).strip().upper()
            level_badge = QLabel(f"{level}层" if level else "候选")
            level_badge.setObjectName("frontierTierBadge")
            level_badge.setToolTip("A/B/C 是动态金字塔层级，不是固定篇数配额")
            diagnostic_badges.append(level_badge)
            relevance_axis = item.get("relevance_axis", {}) if isinstance(item.get("relevance_axis"), dict) else {}
            value_axis = item.get("value_axis", {}) if isinstance(item.get("value_axis"), dict) else {}
            ai_used = relevance_axis.get("ai_adjustment") is not None or value_axis.get("ai_adjustment") is not None
            ai_badge = QLabel("AI 已参与" if ai_used else "规则评分")
            ai_badge.setObjectName("frontierTierBadge")
            ai_badge.setToolTip("AI 已提供双轴有限调整" if ai_used else "本条没有有效 AI 调整，当前分数按本地规则计算")
            diagnostic_badges.append(ai_badge)
        if is_preprint:
            preprint_badge = QLabel("预印本 · 分区不适用")
            preprint_badge.setObjectName("frontierPreprintBadge")
            preprint_badge.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            quality_row.addWidget(preprint_badge)
        else:
            journal_value = item.get("journal_score")
            journal_score = QLabel(f"期刊 {int(journal_value)}" if journal_value is not None else "期刊待评分")
            journal_score.setObjectName("frontierJournalScore")
            source = str(item.get("journal_score_source", "")).strip()
            journal_score.setToolTip("个人期刊偏好分" + ("；该期刊尚未单独评分，当前使用默认分" if source == "profile_default" else ""))
            journal_score.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            diagnostic_badges.append(journal_score)
            jcr_state = str(item.get("jcr_status", item.get("jcr_state", ""))).strip().casefold()
            quartile = str(item.get("jcr_quartile", "")).strip().upper()
            if quartile and jcr_state in {"verified", "manual", "已核验", "手动"}:
                jcr_text, jcr_object = quartile, "frontierJcrVerified"
            elif jcr_state in {"ai_estimated", "ai 估计·待核验"}:
                jcr_text, jcr_object = "AI 估计", "frontierJcrEstimated"
            else:
                jcr_text, jcr_object = "分区未知", "frontierJcrUnknown"
            jcr_badge = QLabel(jcr_text)
            jcr_badge.setObjectName(jcr_object)
            jcr_badge.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            diagnostic_badges.append(jcr_badge)
            cas_upgrade = str(item.get("cas_upgrade", "")).strip()
            cas_badge = QLabel(f"中科院 {cas_upgrade}" if cas_upgrade else "中科院未同步")
            cas_badge.setObjectName("frontierCasBadge")
            cas_badge.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            cas_badge.setToolTip(str(item.get("journal_metric_line", "")).strip() or "期刊指标尚未同步")
            diagnostic_badges.append(cas_badge)
        quality_row.addStretch(1)
        summary_box.addLayout(quality_row)

        terms = [str(term) for term in item.get("matched_terms", item.get("match_terms", [])) if str(term).strip()]
        fallback_reason = f"与{'、'.join(terms[:3])}相关" if terms else "根据综合研究画像发现"
        reason_text = str(item.get("recommendation_reason", item.get("ai_reason_cn", ""))).strip() or fallback_reason
        if float(item.get("ranking_weight", 1) or 1) < 1:
            reason_text = "低权重探索：" + reason_text
        if str(item.get("recommendation_kind", "")) == "profile_exploration" and "画像" not in reason_text:
            reason_text = "画像探索：" + reason_text
        reason = QLabel(("审查结果：" if item.get("content_decision") in {"reject", "pending"} else "为什么推荐：") + reason_text)
        reason.setObjectName("frontierReason")
        reason.setWordWrap(True)
        reason.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        summary_box.addWidget(reason)
        root.addWidget(summary)

        self.details_panel = QFrame()
        self.details_panel.setObjectName("frontierDetailsPanel")
        details = QVBoxLayout(self.details_panel)
        details.setContentsMargins(7, 6, 7, 6)
        details.setSpacing(3)
        diagnostic_row = QHBoxLayout()
        diagnostic_row.setSpacing(5)
        for badge in diagnostic_badges:
            diagnostic_row.addWidget(badge)
        diagnostic_row.addStretch(1)
        details.addLayout(diagnostic_row)
        if terms:
            matched = QLabel("匹配词：" + " · ".join(terms[:6]))
            matched.setObjectName("frontierMatches")
            matched.setWordWrap(True)
            details.addWidget(matched)
        sources = " · ".join(str(value) for value in item.get("source_names", []) if str(value).strip())
        evidence = QLabel("来源：" + (sources or str(item.get("source", "")).strip() or "本地缓存"))
        evidence.setObjectName("frontierMatches")
        evidence.setWordWrap(True)
        details.addWidget(evidence)
        if is_v13:
            strategies = " · ".join(
                str(value)
                for value in item.get("recall_strategies", [item.get("recall_strategy", "")])
                if str(value).strip()
            )
            discovery = QLabel("发现通道：" + (strategies or "来源回补"))
            discovery.setObjectName("frontierMatches")
            discovery.setWordWrap(True)
            details.addWidget(discovery)
            rel = item.get("relevance_axis", {})
            val = item.get("value_axis", {})
            rel_ai = rel.get("ai_adjustment") if isinstance(rel, dict) else None
            val_ai = val.get("ai_adjustment") if isinstance(val, dict) else None
            scoring = QLabel(
                "评分构成：相关轴规则 "
                f"{int(rel.get('base_score', 0) or 0)}/66 + AI {rel_ai if rel_ai is not None else '未参与'}；"
                "价值轴规则 "
                f"{int(val.get('base_score', 0) or 0)}/66 + AI {val_ai if val_ai is not None else '未参与'}"
            )
            scoring.setObjectName("frontierMatches")
            scoring.setWordWrap(True)
            details.addWidget(scoring)
        self.details_panel.hide()
        root.addWidget(self.details_panel)

        action_strip = QFrame()
        action_strip.setObjectName("frontierActionStrip")
        actions = QHBoxLayout(action_strip)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        if item.get("status") == "dismissed":
            restore = QPushButton("恢复")
            restore.setObjectName("primaryAction")
            restore.clicked.connect(lambda: self.restore_requested.emit(str(item.get("id", ""))))
            actions.addWidget(restore)
            actions.addStretch(1)
        else:
            open_button = QPushButton("打开原文")
            open_button.setObjectName("primaryAction")
            open_button.setToolTip("打开论文原始页面")
            open_button.clicked.connect(self._open_original)
            actions.addWidget(open_button)
            reading_button = QPushButton("加入待读")
            reading_button.setObjectName("rowButton")
            reading_button.setToolTip("加入待读清单")
            reading_button.clicked.connect(lambda: self.add_reading_requested.emit(str(item.get("id", ""))))
            actions.addWidget(reading_button)
            actions.addStretch(1)
            self.details_toggle = QPushButton("详情", action_strip)
            self.details_toggle.setObjectName("frontierHiddenDetailsToggle")
            self.details_toggle.setCheckable(True)
            self.details_toggle.toggled.connect(self._toggle_details)
            self.details_toggle.hide()
            self.feedback_toggle = QPushButton("评价", action_strip)
            self.feedback_toggle.setObjectName("frontierHiddenFeedbackToggle")
            self.feedback_toggle.setCheckable(True)
            self.feedback_toggle.toggled.connect(self._toggle_feedback)
            self.feedback_toggle.hide()
            more = EllipsisMenu(tooltip="更多论文操作", parent=action_strip)
            more.setObjectName("frontierCardMore")
            more.add_action("查看评分与来源", self.details_toggle.toggle)
            more.add_action("标记已读", lambda: self.read_requested.emit(str(item.get("id", ""))))
            more.add_action("标记相关", lambda: self.relevant_requested.emit(str(item.get("id", ""))))
            more.add_action("一句话评价", self.feedback_toggle.toggle)
            more.add_separator()
            more.add_action("忽略这篇", lambda: self.irrelevant_requested.emit(str(item.get("id", ""))))
            actions.addWidget(more)
        root.addWidget(action_strip)

        self.feedback_panel = QFrame()
        self.feedback_panel.setObjectName("frontierFeedbackPanel")
        feedback_box = QHBoxLayout(self.feedback_panel)
        feedback_box.setContentsMargins(6, 5, 6, 5)
        feedback_box.setSpacing(5)
        self.feedback_edit = QPlainTextEdit(str(item.get("one_line_feedback", "")))
        self.feedback_edit.setObjectName("frontierCommentEdit")
        self.feedback_edit.setPlaceholderText("一句话告诉 AI：多推什么，或排除什么")
        self.feedback_edit.setFixedHeight(52)
        submit = QPushButton("提交")
        submit.setObjectName("frontierCommentSubmit")
        submit.clicked.connect(self._submit_feedback)
        feedback_box.addWidget(self.feedback_edit, 1)
        feedback_box.addWidget(submit, alignment=Qt.AlignmentFlag.AlignBottom)
        self.feedback_panel.hide()
        root.addWidget(self.feedback_panel)

    def _toggle_details(self, checked: bool) -> None:
        self.details_toggle.setText("收起" if checked else "详情")
        self.details_panel.setVisible(checked)
        if checked:
            self.detail_opened.emit(str(self.item.get("id", "")))

    def _toggle_feedback(self, checked: bool) -> None:
        self.feedback_panel.setVisible(checked)
        if checked:
            self.feedback_edit.setFocus()

    def _submit_feedback(self) -> None:
        text = self.feedback_edit.toPlainText().strip()
        if text:
            self.feedback_requested.emit(str(self.item.get("id", "")), text)

    def _open_original(self) -> None:
        url = str(self.item.get("url", "")).strip()
        if url:
            QDesktopServices.openUrl(QUrl(url))


class DailyFrontierPage(QWidget):
    changed = Signal()
    daily_ready = Signal(int, str)
    open_journal_library = Signal()
    settings_center_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.data = self._load_data()
        self._worker: FrontierRefreshThread | None = None
        self._ai_worker: FrontierAiRerankThread | None = None
        self._profile_ai_worker: FrontierProfileAiThread | None = None
        self._comment_ai_worker: FrontierCommentAiThread | None = None
        self._pending_auto_ai_rerank = False
        self._profile_ai_refresh_after = False
        self._profile_ai_source_signature = ""
        self._render_batch_size = 24
        self._render_limit = self._render_batch_size
        self._header_width_known = False
        self.ai_progress: AiProgressPanel | None = None
        self._build_ui()
        self._render()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 9)
        root.setSpacing(7)
        header = PageHeader("每日前沿", accent="frontier")
        self.title_label = header.title_label
        self.frontier_subtitle = header.hint_label
        self.settings_button = QPushButton("研究设置")
        self.settings_button.setObjectName("subtleButton")
        self.settings_button.clicked.connect(self._open_settings)
        self.settings_button.hide()
        self.profile_update_button = QPushButton("更新画像")
        self.profile_update_button.setObjectName("subtleButton")
        self.profile_update_button.setToolTip("仅在成果、关联 PDF 或前沿点击偏好有变化时调用 DeepSeek")
        self.profile_update_button.clicked.connect(self.update_profile_now)
        self.profile_update_button.hide()
        self.ai_button = QPushButton("AI 复核")
        self.ai_button.setObjectName("subtleButton")
        self.ai_button.setToolTip("按完整研究画像复核候选内容；复用有效缓存，每次最多新增审查 80 篇，其余保留待复核")
        self.ai_button.clicked.connect(self._run_ai_rerank)
        self.ai_button.hide()
        self._refresh_button_full_text = "检查更新"
        self.refresh_button = header.add_primary_action(self._refresh_button_full_text, self.refresh_now)
        self.more_actions_button = header.add_overflow_menu("更多前沿操作")
        self.more_actions_button.add_action("研究设置", self._open_settings)
        self._profile_update_action = self.more_actions_button.add_action("更新研究画像", self.update_profile_now)
        self._undo_profile_action = self.more_actions_button.add_action("撤销今日画像整理", self.undo_profile_organization)
        self._ai_rerank_action = self.more_actions_button.add_action("AI 复核候选论文", self._run_ai_rerank)
        root.addWidget(header)

        filter_row = QHBoxLayout()
        self.status_label = ElidedLabel()
        self.status_label.setObjectName("sectionLabel")
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        filter_row.addWidget(self.status_label, 1)
        self.filter_combo = QComboBox()
        self.filter_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.filter_combo.setMinimumContentsLength(6)
        self.filter_combo.setMinimumWidth(112)
        self.filter_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.filter_combo.addItems(["今日推荐", "优先期刊", "全部记录", "已加入待读", "已读", "已标记相关", "太泛", "已隐藏"])
        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)
        self.filter_combo.hide()
        root.addLayout(filter_row)
        self.cache_hint = ElidedLabel()
        self.cache_hint.setObjectName("dateLabel")
        self.cache_hint.hide()
        root.addWidget(self.cache_hint)
        self.ai_progress = AiProgressPanel(object_name="frontierRefreshProgress")
        root.addWidget(self.ai_progress)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.content.setObjectName("frontierContent")
        self.rows = QVBoxLayout(self.content)
        self.rows.setContentsMargins(2, 2, 8, 10)
        self.rows.setSpacing(8)
        self.rows.addStretch()
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)
        self._apply_header_density(force_compact=True)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._header_width_known = True
        self._apply_header_density()

    def _apply_header_density(self, *, force_compact: bool = False) -> None:
        """Keep the title and two primary actions usable in a narrow widget."""
        if not hasattr(self, "refresh_button"):
            return
        compact = force_compact or not self._header_width_known or self.width() < 500
        self.profile_update_button.hide()
        self.ai_button.hide()
        self.settings_button.hide()
        self.more_actions_button.show()
        self.frontier_subtitle.setHidden(compact)
        self.status_label.setMaximumWidth(max(80, self.width() - 150) if compact else 16_777_215)
        self.refresh_button.setText("更新" if compact else self._refresh_button_full_text)
        self.refresh_button.setToolTip(self._refresh_button_full_text)

    def _on_filter_changed(self, text: str) -> None:
        self._render_limit = self._render_batch_size
        self._render()
        self.scroll.verticalScrollBar().setValue(0)

    def _select_view(self, name: str) -> None:
        index = self.filter_combo.findText(str(name))
        if index >= 0:
            self.filter_combo.setCurrentIndex(index)

    def reload(self) -> None:
        self.data = self._load_data()
        self._render()

    @staticmethod
    def _load_data() -> dict:
        data = load_frontier_data()
        data["profile"] = load_research_profile()
        data["items"] = apply_frontier_ranking(
            data.get("items", []),
            data.get("profile", {}),
            load_journal_library(),
        )
        return data

    def _persist_data(self) -> None:
        profile = self.data.get("profile", {})
        save_research_profile(profile if isinstance(profile, dict) else {})
        save_frontier_data(self.data)

    def auto_refresh_if_due(self) -> None:
        if self.auto_update_profile_if_due(refresh_after=True):
            return
        if self.data.get("last_checked") != date.today().isoformat() or frontier_cache_is_stale(self.data, load_journal_library()):
            self._start_refresh(manual=False)

    def update_profile_now(self) -> bool:
        """User-facing one-click, incremental research-profile update."""
        return self.auto_update_profile_if_due(refresh_after=False, manual=True)

    def undo_profile_organization(self) -> bool:
        today = date.today().isoformat()
        profile = self.data.get("profile", {})
        profile = profile if isinstance(profile, dict) else {}
        snapshot = profile.get("last_organization_snapshot")
        if not isinstance(snapshot, dict) or str(snapshot.get("date", "")) != today:
            self.status_label.setText("今天还没有可撤销的画像整理。")
            return False
        self.data["profile"] = undo_last_organization(profile, today=today)
        self._save("已撤销今天的画像整理；今天不会再次自动整理")
        return True

    def auto_update_profile_if_due(self, refresh_after: bool = False, manual: bool = False) -> bool:
        """Run the auditable v12 organizer once per day or on demand."""
        if self._profile_ai_worker is not None and self._profile_ai_worker.isRunning():
            self._profile_ai_refresh_after = self._profile_ai_refresh_after or refresh_after
            return True
        settings = get_ai_settings()
        if not is_deepseek_ready("research_profile_update"):
            if manual:
                self.status_label.setText("请先在“研究设置 → 智能增强与 JCR”配置并启用 DeepSeek。")
                if self.ai_progress is not None:
                    self.ai_progress.fail("研究画像 AI 未配置，未开始分析。")
            return False
        profile = self.data.get("profile", {})
        profile = profile if isinstance(profile, dict) else {}
        today = date.today().isoformat()
        if not manual and not should_auto_organize(profile, today=today):
            return False
        achievements = load_achievements() if settings.get("auto_profile_from_achievements", True) else []
        feedback_items = self._profile_feedback_items() if settings.get("auto_profile_from_frontier", True) else []
        evidence = self._build_profile_evidence(achievements, feedback_items)
        if not evidence["papers"] and not evidence["signals"] and not profile.get("terms"):
            if manual:
                self.status_label.setText("还没有可用于整理画像的论文、关键词或明确反馈。")
                if self.ai_progress is not None:
                    self.ai_progress.fail("没有可供 AI 分析的研究证据。")
            return False
        self._profile_ai_refresh_after = refresh_after
        if self.ai_progress is not None:
            self.ai_progress.begin("AI 正在读取长期画像、论文与行为信号…")
        self.status_label.setText("AI 正在整理相近概念、权重和排除词…")
        self.profile_update_button.setEnabled(False)
        self._profile_update_action.setEnabled(False)
        self._profile_ai_worker = FrontierProfileAiThread(profile, evidence, self)
        self._profile_ai_worker.progress.connect(self._profile_ai_progress)
        self._profile_ai_worker.completed.connect(self._profile_ai_finished)
        self._profile_ai_worker.failed.connect(self._profile_ai_failed)
        self._profile_ai_worker.finished.connect(self._clear_profile_ai_worker)
        self._profile_ai_worker.start()
        return True

    @staticmethod
    def _build_profile_evidence(achievements: list[dict], feedback_items: list[dict]) -> dict:
        papers = [dict(item) for item in load_papers() if isinstance(item, dict)]
        known_ids = {str(item.get("id", "")).strip() for item in papers}
        for achievement in achievements:
            if not isinstance(achievement, dict):
                continue
            item_id = str(achievement.get("id", "")).strip()
            if item_id and item_id in known_ids:
                continue
            papers.append(
                {
                    "id": item_id,
                    "title": str(achievement.get("title", achievement.get("name", ""))).strip(),
                    "keywords": achievement.get("keywords", []),
                    "summary": str(
                        achievement.get("summary", achievement.get("abstract", achievement.get("description", "")))
                    ).strip(),
                }
            )
            if item_id:
                known_ids.add(item_id)

        signal_weights = {
            "relevant": 90,
            "liked": 90,
            "saved": 85,
            "dismissed": -100,
            "irrelevant": -100,
            "too_broad": -70,
            "deprioritized": -70,
        }
        signals: list[dict] = []
        for item in feedback_items:
            if not isinstance(item, dict):
                continue
            explicit = str(item.get("one_line_feedback", "")).strip()
            kind = str(item.get("feedback", item.get("status", ""))).strip()
            title = str(item.get("title", "")).strip()
            text = explicit or " · ".join(value for value in (title, kind) if value)
            if not text:
                continue
            signals.append(
                {
                    "kind": "explicit_feedback" if explicit else kind,
                    "text": text,
                    "weight": 100 if explicit else signal_weights.get(kind, 30),
                    "at": str(item.get("feedback_updated_at", item.get("updated_at", ""))).strip(),
                }
            )
        return {"papers": papers, "signals": signals}

    def _profile_feedback_items(self) -> list[dict]:
        feedback_values = {"relevant", "irrelevant", "too_broad"}
        status_values = {"liked", "dismissed", "deprioritized"}
        return [
            item
            for item in self.data.get("items", [])
            if isinstance(item, dict)
            and (
                str(item.get("feedback", "")) in feedback_values
                or str(item.get("status", "")) in status_values
            )
        ]

    @staticmethod
    def _merge_profile_terms(current: object, suggested: object, limit: int) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for values in (current if isinstance(current, list) else [], suggested if isinstance(suggested, list) else []):
            for item in values:
                term = str(item).strip()
                key = term.casefold()
                if term and key not in seen:
                    seen.add(key)
                    merged.append(term)
                if len(merged) >= limit:
                    return merged
        return merged

    def _profile_ai_finished(self, result: dict) -> None:
        today = date.today().isoformat()
        try:
            profile, changes = apply_organization_transaction(
                self.data.get("profile", {}),
                result if isinstance(result, dict) else {},
                today=today,
            )
            profile["ai_profile_updated_at"] = today
            profile["ai_profile_attempted_at"] = today
            profile["ai_profile_last_checked_at"] = today
            self.data["profile"] = profile
            self._save(f"AI 已完成今日画像整理（{len(changes)} 项变更，可撤销）")
        except Exception as error:  # noqa: BLE001 - keep an invalid AI plan away from the saved profile
            self._profile_ai_failed(str(error))
            return
        if self.ai_progress is not None:
            self.ai_progress.complete("AI 研究画像更新完成。")
        if self._profile_ai_refresh_after:
            QTimer.singleShot(180, lambda: self._start_refresh(manual=False))

    def _profile_ai_progress(self, message: str, value: int) -> None:
        if message:
            self.status_label.setText(str(message))
            if self.ai_progress is not None:
                self.ai_progress.update(str(message), int(value))

    def _profile_ai_failed(self, message: str) -> None:
        self.status_label.setText("研究画像自动更新失败：" + str(message))
        if self.ai_progress is not None:
            self.ai_progress.fail("研究画像 AI 失败：" + str(message))
        if self._profile_ai_refresh_after:
            QTimer.singleShot(180, lambda: self._start_refresh(manual=False))

    def _clear_profile_ai_worker(self) -> None:
        if self._profile_ai_worker is not None:
            self._profile_ai_worker.deleteLater()
        self._profile_ai_worker = None
        self._profile_ai_refresh_after = False
        self._profile_ai_source_signature = ""
        self.profile_update_button.setEnabled(True)
        self._profile_update_action.setEnabled(True)

    def refresh_now(self) -> None:
        self._start_refresh(manual=True)

    def _start_refresh(self, *, manual: bool) -> None:
        if (self._worker is not None and self._worker.isRunning()) or (self._ai_worker is not None and self._ai_worker.isRunning()):
            return
        self.refresh_button.setEnabled(False)
        self.status_label.setText("正在检查公开论文元数据…")
        if self.ai_progress is not None:
            self.ai_progress.begin("正在准备多源论文检索…")
        refresh_data = deepcopy(self.data)
        refresh_data["authored_papers"] = [dict(item) for item in load_papers() if isinstance(item, dict)]
        self._worker = FrontierRefreshThread(
            refresh_data,
            load_journal_library(),
            self,
            manual=manual,
        )
        self._worker.progress.connect(self._refresh_progress)
        self._worker.completed.connect(self._refresh_finished)
        self._worker.failed.connect(self._refresh_failed)
        self._worker.finished.connect(self._clear_worker)
        self._worker.start()

    def _refresh_progress(self, message: str, value: int) -> None:
        self.status_label.setText(str(message))
        if self.ai_progress is not None:
            self.ai_progress.update(str(message), int(value))

    def _clear_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None
        self.refresh_button.setEnabled(True)
        if self._pending_auto_ai_rerank:
            self._pending_auto_ai_rerank = False
            QTimer.singleShot(0, self._run_ai_rerank)

    def _refresh_finished(self, result: dict) -> None:
        latest = {str(i["id"]): i for i in self.data.get("items", [])}
        latest_by_fingerprint = {
            work_fingerprint(item): item
            for item in self.data.get("items", [])
            if isinstance(item, dict) and work_fingerprint(item)
        }
        current_profile = self.data.get("profile", {})
        self.data = result["data"]
        self.data["profile"] = current_profile
        self.data["items"] = [
            merge_frontier_refresh_item(
                item,
                latest.get(str(item["id"])) or latest_by_fingerprint.get(work_fingerprint(item)),
                date.today().isoformat(),
            )
            for item in self.data.get("items", [])
        ]
        returned_ids = {str(i["id"]) for i in self.data["items"]}
        if int(self.data.get("algorithm_version", 0) or 0) >= 13:
            deleted = {
                str(value.get("fingerprint", ""))
                for value in self.data.get("fingerprints", [])
                if isinstance(value, dict) and str(value.get("fingerprint", ""))
            }
            self.data["items"].extend(
                dict(item)
                for key, item in latest.items()
                if key not in returned_ids
                and work_fingerprint(item) not in deleted
                and item.get("status") in {"saved", "liked", "locked"}
            )
        else:
            self.data["items"].extend(dict(i) for key, i in latest.items() if key not in returned_ids
                and i.get("status") in {"saved", "liked", "read", "dismissed", "deprioritized"})
        errors = result.get("errors", [])
        status = "已更新"
        if errors:
            failed_sources = {str(value.get("source", "")) for value in errors if isinstance(value, dict)}
            status += f"（{len(failed_sources) or len(errors)} 个来源待补跑）"
        seed_guidance = recall_seed_guidance(result.get("recall_outcomes", {}))
        if seed_guidance:
            status += " · " + seed_guidance
        self.status_label.setToolTip(seed_guidance)
        should_notify = bool(
            result.get("visible_count", 0)
            and self.data.get("profile", {}).get("notify", True)
            and self.data.get("last_notified") != date.today().isoformat()
        )
        if should_notify:
            self.data["last_notified"] = date.today().isoformat()
        if result.get("batch_id") and isinstance(result.get("runtime"), dict) and not result.get("already_succeeded"):
            save_research_profile(current_profile if isinstance(current_profile, dict) else {})
            finalized = commit_frontier_v13_data(
                self.data,
                result["runtime"],
                str(result["batch_id"]),
                complete=not bool(errors),
            )
            result["runtime"] = finalized
            self._render()
            self.status_label.setText(status + " · " + self.status_label.text())
            self.changed.emit()
        elif result.get("already_succeeded"):
            self._render()
            self.status_label.setText(str(result.get("brief", "今天已完成更新")))
        else:
            self._save(status)
        if self.ai_progress is not None:
            self.ai_progress.complete(str(result.get("brief", "每日前沿已更新")))
            QTimer.singleShot(1800, self.ai_progress.hide)
        if should_notify:
            self.daily_ready.emit(int(result.get("visible_count", 0)), str(result.get("brief", "")))
        # v13 performs automatic AI review inside the score stage, before the
        # dynamic pyramid.  The manual button remains available, but scheduling
        # a second post-save review here would restore the old wrong order.
        self._pending_auto_ai_rerank = False

    def _refresh_failed(self, message: str) -> None:
        self.status_label.setText("更新失败：请检查网络后重试")
        if self.ai_progress is not None:
            self.ai_progress.fail("每日前沿更新失败：" + str(message))
        # 失败状态直接留在页面上，避免窄窗口下的系统对话框遮挡内容。

    def _is_v13(self) -> bool:
        return int(self.data.get("algorithm_version", 0) or 0) >= 13

    def _stream_partitions(self) -> dict[str, list[dict]]:
        all_items = [item for item in self.data.get("items", []) if isinstance(item, dict)]
        streams = {"journal": [], "preprint": []}
        for item in all_items:
            if self._is_v13():
                if str(item.get("candidate_state", "")).strip() != "visible":
                    # v13 retains incomplete candidates for enrichment without
                    # exposing a hidden Pending page to the user.
                    continue
            else:
                # Legacy caches stay read-only until the next refresh rebuilds
                # them as v13 data; keep v12's visibility rules so nothing
                # that was hidden (rejected / pending pools) suddenly shows.
                decision = item.get("content_decision")
                if decision == "reject":
                    continue
                if decision != "accept" and item.get("status") not in {
                    "read", "saved", "liked", "dismissed", "deprioritized",
                }:
                    continue
                if not item.get("is_preprint") and str(item.get("quality_gate_state", "")).strip() == "withheld":
                    continue
            streams["preprint" if item.get("is_preprint") else "journal"].append(item)
        return streams

    def _visible_items(self) -> list[dict]:
        status = self.filter_combo.currentText()
        streams = self._stream_partitions()
        all_items = [*streams["journal"], *streams["preprint"]]
        if status == "已隐藏":
            items = [item for item in all_items if item.get("status") == "dismissed"]
        elif status == "太泛":
            items = [item for item in all_items if item.get("status") == "deprioritized"]
        elif status == "已加入待读":
            items = [item for item in all_items if item.get("status") == "saved"]
        elif status == "已读":
            items = [item for item in all_items if item.get("status") == "read"]
        elif status == "已标记相关":
            items = [item for item in all_items if item.get("status") == "liked"]
        elif status == "优先期刊":
            items = [
                item for item in all_items
                if item.get("priority") in {"必看", "关注"}
                and item.get("status") not in {"read", "dismissed", "deprioritized"}
            ]
        elif status == "全部记录":
            items = all_items
        else:
            items = [
                item for item in all_items
                if item.get("status") not in {"read", "dismissed", "deprioritized"}
            ]
        # v13 排序：今日新增在前、A/B/C 层、双轴分。legacy 缓存缺字段时
        # 排序键自然回退（bucket 默认 today、层级默认 9），只读展示即可。
        bucket_order = {"today": 0, "previous_unread": 1}
        level_order = {"A": 0, "B": 1, "C": 2}
        items.sort(
            key=lambda item: (
                bucket_order.get(str(item.get("display_bucket", "today")), 2),
                level_order.get(str(item.get("pyramid_level", "")), 9),
                -min(
                    int(item.get("relevance_score", 0) or 0),
                    int(item.get("research_value_score", 0) or 0),
                ),
                -max(
                    int(item.get("relevance_score", 0) or 0),
                    int(item.get("research_value_score", 0) or 0),
                ),
                str(item.get("first_seen_at", "")),
            )
        )
        return items

    def _update_cache_hint(self) -> None:
        stale = frontier_cache_is_stale(self.data, load_journal_library())
        if stale:
            self._refresh_button_full_text = "按新规则更新"
            self.refresh_button.setToolTip("研究设置或排序规则已变化，点击后按新规则重新计算")
        else:
            self._refresh_button_full_text = "检查更新"
            self.refresh_button.setToolTip("检查并更新今日论文")
        self.cache_hint.hide()
        self._apply_header_density()

    def _brief(self, items: list[dict]) -> str:
        top = select_daily_recommendations(items, self.data.get("profile", {}))
        if not top:
            return "暂未发现今天首次命中的论文。可在研究设置调整关键词、数据源或优先期刊。"
        source_keywords = sum(1 for item in top if item.get("match_source", item.get("match_mode")) == "source_keywords")
        terms = []
        for item in top:
            for term in item.get("matched_terms", item.get("match_terms", [])):
                if term not in terms:
                    terms.append(term)
        result = f"今日筛出 {len(top)} 篇研究，按总分排序"
        if source_keywords:
            result += f"；来源关键词匹配 {source_keywords} 篇"
        if terms:
            result += "；重点涉及 " + "、".join(terms[:3])
        return result + "。"

    def _priority_text(self) -> str:
        journals = load_journal_library()
        groups = {
            "必看": [str(item.get("name", "")).strip() for item in journals if item.get("frontier_priority") == "必看"],
            "关注": [str(item.get("name", "")).strip() for item in journals if item.get("frontier_priority") == "关注"],
        }

        def compact(names: list[str]) -> str:
            visible = [name for name in names if name][:4]
            suffix = f" +{len(names) - len(visible)}" if len(names) > len(visible) else ""
            return " · ".join(visible) + suffix

        parts = [f"必看：{compact(groups['必看'])}" if groups["必看"] else ""]
        if groups["关注"]:
            parts.append(f"关注：{compact(groups['关注'])}")
        return "\n".join(part for part in parts if part) or "尚未设置。可在“研究设置 → 优先期刊”中选择必看或关注。"

    def _render(self) -> None:
        while self.rows.count() > 1:
            child = self.rows.takeAt(0)
            widget = child.widget()
            if widget:
                # Do not retain invisible frontier cards between filtering or
                # refreshes; stale cards can keep outdated size hints alive.
                widget.setParent(None)
                widget.deleteLater()
        visible = self._visible_items()
        rendered = visible[: self._render_limit]
        v13 = self._is_v13()
        checked = str(self.data.get("last_checked", ""))
        count_text = f"显示 {len(rendered)}/{len(visible)} 篇" if len(rendered) < len(visible) else f"当前 {len(visible)} 篇"
        if v13 and self.filter_combo.currentText() == "今日推荐":
            today_count = sum(str(item.get("display_bucket", "today")) == "today" for item in visible)
            unread_count = sum(str(item.get("display_bucket", "")) == "previous_unread" for item in visible)
            self.status_label.setText(f"今日推荐 {len(visible)} 篇 · 新增 {today_count} · 此前未读 {unread_count}")
        else:
            self.status_label.setText(f"{checked or '尚未检查'} · {count_text}")
        self._update_cache_hint()
        previous_group: str | None = None
        for item in rendered:
            if v13 and self.filter_combo.currentText() == "今日推荐":
                bucket = str(item.get("display_bucket", "today"))
                group = bucket
                if group != previous_group:
                    bucket_name = "今日新增" if bucket == "today" else "此前未读"
                    heading = QLabel(bucket_name)
                    heading.setObjectName("frontierPyramidSection")
                    heading.setToolTip("推荐按当天相对价值动态排序，不设固定篇数")
                    self.rows.insertWidget(self.rows.count() - 1, heading)
                    previous_group = group
            card = FrontierCard(item)
            card.add_reading_requested.connect(self._add_to_reading)
            card.add_inspiration_requested.connect(self._add_to_inspiration)
            card.relevant_requested.connect(self._mark_relevant)
            card.irrelevant_requested.connect(self._mark_irrelevant)
            card.too_broad_requested.connect(self._mark_too_broad)
            card.read_requested.connect(self._mark_read)
            card.restore_requested.connect(self._restore_recommendation)
            card.feedback_requested.connect(self._record_feedback)
            card.import_journal_requested.connect(self._import_journal)
            card.detail_opened.connect(self._record_detail_open)
            self.rows.insertWidget(self.rows.count() - 1, card)
        if len(rendered) < len(visible):
            load_more = QPushButton(f"继续加载（已显示 {len(rendered)} / {len(visible)}）")
            load_more.setObjectName("frontierLoadMoreButton")
            load_more.clicked.connect(self._load_more)
            self.rows.insertWidget(self.rows.count() - 1, load_more)
        if not visible:
            self.status_label.hide()
            message = "今天暂无合适的新论文" if v13 and self.filter_combo.currentText() == "今日推荐" else "这里暂时没有论文"
            empty = QLabel(message)
            empty.setObjectName("emptyLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rows.insertWidget(0, empty)
        else:
            self.status_label.show()

    def _load_more(self) -> None:
        scroll_position = self.scroll.verticalScrollBar().value()
        self._render_limit += self._render_batch_size
        self._render()
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll_position))

    def _open_settings(self) -> None:
        self._open_legacy_settings()

    def _open_legacy_settings(self) -> None:
        """Open the research profile directly from Daily Frontier."""
        dialog = V115FrontierSettingsDialog(self.data.get("profile", {}), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.data["profile"] = dialog.profile()
            self.data["items"] = apply_frontier_ranking(
                self.data.get("items", []),
                self.data.get("profile", {}),
                load_journal_library(),
            )
            self._save("研究设置已保存")

    def _find_item(self, item_id: str) -> dict | None:
        return next((item for item in self.data.get("items", []) if str(item.get("id", "")) == item_id), None)

    def _record_research_signal(self, item_id: str, event_type: str) -> None:
        item = self._find_item(item_id)
        if item is None:
            return
        self.data["profile"] = record_signal(
            self.data.get("profile", {}),
            {
                "event_type": event_type,
                "item_id": item_id,
                "title": str(item.get("title", "")),
                "doi": str(item.get("doi", "")),
                "terms": item.get("matched_terms", item.get("match_terms", [])),
                "occurred_at": date.today().isoformat() + "T12:00:00",
                "source": "daily_frontier",
            },
        )

    def _record_detail_open(self, item_id: str) -> None:
        # Opening details is navigation, not preference feedback.  v13 only
        # learns from explicit relevant/favorite/ignore/one-line actions.
        del item_id

    def _add_to_reading(self, item_id: str) -> None:
        item = self._find_item(item_id)
        if item is None:
            return
        readings = load_readings()
        if not any(str(reading.get("title", "")).casefold() == str(item.get("title", "")).casefold() for reading in readings):
            readings.insert(
                0,
                {
                    "id": uuid4().hex,
                    "title": str(item.get("title", "")),
                    "status": "未阅读",
                    "reason": "每日前沿：" + str(item.get("summary_cn", "")),
                },
            )
            save_readings(readings)
        item["status"] = "saved"
        self._record_research_signal(item_id, "favorite")
        self._save("已加入待读")

    def _add_to_inspiration(self, item_id: str) -> None:
        item = self._find_item(item_id)
        if item is None:
            return
        inspirations = load_inspirations()
        text = "前沿论文灵感：" + str(item.get("summary_cn", ""))
        if not any(str(entry.get("text", "")) == text for entry in inspirations):
            inspirations.insert(0, {"id": uuid4().hex, "text": text})
            save_inspirations(inspirations)
        self._record_research_signal(item_id, "favorite")
        self._save("已记为灵感")

    def _record_feedback(self, item_id: str, text: str) -> None:
        """Classify an explicit preference immediately, with visible progress."""
        item = self._find_item(item_id)
        if item is None or not str(text).strip():
            return
        classification = classify_feedback_locally(text)
        if str(classification.get("kind", "")) == "journal_quality":
            self._apply_local_feedback(item_id, text, classification)
            return
        if not is_deepseek_ready("research_profile_update"):
            self._apply_local_feedback(item_id, text, classification)
            if self.ai_progress is not None:
                self.ai_progress.fail("AI 画像未配置；评价已保留，但未自动提取关键词。")
            return
        if self._comment_ai_worker is not None and self._comment_ai_worker.isRunning():
            self.status_label.setText("上一条一句话评价仍在分析，请稍候。")
            return
        if self.ai_progress is not None:
            self.ai_progress.begin("AI 正在理解这条研究偏好…")
        self.status_label.setText("AI 正在实时更新研究画像…")
        self._comment_ai_worker = FrontierCommentAiThread(item, text, self)
        self._comment_ai_worker.progress.connect(self._comment_ai_progress)
        self._comment_ai_worker.completed.connect(self._comment_ai_finished)
        self._comment_ai_worker.failed.connect(self._comment_ai_failed)
        self._comment_ai_worker.finished.connect(self._clear_comment_ai_worker)
        self._comment_ai_worker.start()

    def _apply_local_feedback(self, item_id: str, text: str, classification: dict) -> None:
        self.data = record_frontier_feedback_event(
            self.data,
            item_id,
            "one_line_feedback",
            text,
            classification,
        )
        item = self._find_item(item_id)
        if item is None:
            return
        item["feedback_adjustment"] = int(classification.get("feedback_adjustment", 0) or 0)
        item["feedback"] = str(classification.get("kind", "reading_value"))
        if str(classification.get("kind", "")) == "journal_quality":
            library = load_journal_library()
            journal_key = canonical_text(item.get("journal", ""))
            changed = False
            for journal in library:
                if canonical_text(journal.get("name", "")) == journal_key:
                    journal["user_quality_flag"] = "low"
                    changed = True
                    break
            if changed:
                save_journal_library(library)
            if bool(self.data.get("profile", {}).get("filter_known_q3_q4", True)):
                item["filtered_by_quality"] = True
            self._save("已记录期刊质量反馈；不会降低研究关键词权重")
            return
        self._save("已保存一句话评价")

    def _comment_ai_progress(self, message: str, value: int) -> None:
        self.status_label.setText(str(message))
        if self.ai_progress is not None:
            self.ai_progress.update(str(message), int(value))

    def _comment_ai_finished(self, item_id: str, text: str, result: dict) -> None:
        today = date.today().isoformat()
        try:
            profile, changes = apply_profile_comment(
                self.data.get("profile", {}),
                result if isinstance(result, dict) else {},
                today=today,
            )
        except Exception as error:  # noqa: BLE001 - invalid AI plans must not alter the saved profile
            self._comment_ai_failed(str(error))
            return
        self.data["profile"] = profile
        intent = str(result.get("intent", "ambiguous")).strip()
        compatibility = {
            "kind": {
                "positive": "topic_positive",
                "negative": "topic_negative",
                "ambiguous": "uncertain",
            }.get(intent, "uncertain"),
            "term_weight_delta": 1 if intent == "positive" else -1 if intent == "negative" else 0,
            "quality_flag": "",
            "feedback_adjustment": 15 if intent == "positive" else -15 if intent == "negative" else 0,
            "reason": str(result.get("reason", "")),
        }
        self.data = record_frontier_feedback_event(
            self.data,
            item_id,
            "one_line_feedback",
            text,
            compatibility,
        )
        item = self._find_item(item_id)
        if item is not None:
            item["feedback_adjustment"] = compatibility["feedback_adjustment"]
            item["feedback"] = compatibility["kind"]
            item["feedback_updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._persist_data()
        self._render()
        self.status_label.setText(f"一句话评价已更新画像（{len(changes)} 项变化）")
        if self.ai_progress is not None:
            self.ai_progress.complete("一句话评价已应用；低置信度新词已进入待确认。")

    def _comment_ai_failed(self, message: str) -> None:
        self.status_label.setText("一句话评价分析失败，研究画像未修改。")
        if self.ai_progress is not None:
            self.ai_progress.fail("一句话评价 AI 失败：" + str(message))

    def _clear_comment_ai_worker(self) -> None:
        if self._comment_ai_worker is not None:
            self._comment_ai_worker.deleteLater()
        self._comment_ai_worker = None

    def _import_journal(self, item_id: str) -> None:
        item = self._find_item(item_id)
        if item is None:
            return
        library = load_journal_library()
        updated, created = import_frontier_journal(library, item)
        matching = next(
            (
                journal
                for journal in updated
                if canonical_text(journal.get("name", "")) == canonical_text(item.get("journal", ""))
            ),
            {},
        )
        if matching:
            item["library_journal_id"] = str(matching.get("id", ""))
        if created:
            save_journal_library(updated)
            self._save("已导入期刊库为“扩展”；可在期刊库补充 JCR 与方向")
        else:
            self._save("该期刊已在期刊库中")

    def _adjust_feedback(self, item: dict, delta: int) -> None:
        profile = self.data.setdefault("profile", {})
        feedback = profile.setdefault("feedback", {})
        axis_weights = feedback.setdefault("axis_weights", {})
        if not isinstance(axis_weights, dict):
            axis_weights = {}
            feedback["axis_weights"] = axis_weights
        for axis_id in item.get("matched_axis_ids", []):
            current = int(axis_weights.get(str(axis_id), 0))
            axis_weights[str(axis_id)] = max(-6, min(6, current + delta))
        # v5 research profiles use one primary and one secondary keyword box.
        # Learning the matched terms makes feedback meaningfully affect the
        # next ranking instead of merely shifting every candidate together.
        term_weights = feedback.setdefault("term_weights", {})
        if not isinstance(term_weights, dict):
            term_weights = {}
            feedback["term_weights"] = term_weights
        for term in item.get("match_terms", []):
            key = str(term).strip().casefold()
            if not key:
                continue
            current = int(term_weights.get(key, 0))
            term_weights[key] = max(-6, min(6, current + delta))

    def _mark_relevant(self, item_id: str) -> None:
        item = self._find_item(item_id)
        if item is None:
            return
        previous = str(item.get("feedback", ""))
        if previous != "relevant":
            self._adjust_feedback(item, 1 if previous != "irrelevant" else 2)
        item["feedback"] = "relevant"
        if item.get("status") != "saved":
            item["status"] = "liked"
        self._record_research_signal(item_id, "favorite")
        self._save("已标记相关，后续会稍微优先同类方向")

    def _mark_too_broad(self, item_id: str) -> None:
        item = self._find_item(item_id)
        if item is None:
            return
        previous = str(item.get("feedback", ""))
        previous_status = str(item.get("status", "new"))
        delta = 0
        if previous != "too_broad":
            delta = -1 if previous != "relevant" else -2
            self._adjust_feedback(item, delta)
        item["deprioritized_from_status"] = previous_status if previous_status in {"new", "saved", "liked", "read"} else "new"
        item["deprioritized_from_feedback"] = previous
        item["deprioritized_feedback_delta"] = delta
        item["feedback"] = "too_broad"
        item["status"] = "deprioritized"
        self._record_research_signal(item_id, "ignore")
        self._save("已标记为太泛，后续会降低同类推荐")
        show_undo_toast(self, "已降低该推荐优先级", lambda: self._restore_too_broad(item_id))

    def _restore_too_broad(self, item_id: str) -> None:
        item = self._find_item(item_id)
        if item is None or item.get("status") != "deprioritized":
            return
        delta = int(item.get("deprioritized_feedback_delta", 0) or 0)
        if delta:
            self._adjust_feedback(item, -delta)
        item["status"] = str(item.get("deprioritized_from_status", "")) or "new"
        item["feedback"] = str(item.get("deprioritized_from_feedback", ""))
        item["deprioritized_from_status"] = ""
        item["deprioritized_from_feedback"] = ""
        item["deprioritized_feedback_delta"] = 0
        self._save("已恢复推荐")

    def _mark_read(self, item_id: str) -> None:
        item = self._find_item(item_id)
        if item is None:
            return
        readings = load_readings()
        target = next(
            (reading for reading in readings if str(reading.get("title", "")).casefold() == str(item.get("title", "")).casefold()),
            None,
        )
        if target is None:
            readings.insert(
                0,
                {
                    "id": uuid4().hex,
                    "title": str(item.get("title", "")),
                    "status": "已阅读",
                    "reason": "每日前沿已读：" + str(item.get("summary_cn", "")),
                    "url": str(item.get("url", "")),
                },
            )
        else:
            target["status"] = "已阅读"
        save_readings(readings)
        item["status"] = "read"
        item["feedback"] = "read"
        self._save("已移出每日前沿并同步到阅读记录；阅读动作不会训练偏好")

    def _mark_irrelevant(self, item_id: str) -> None:
        item = self._find_item(item_id)
        if item is None:
            return
        previous = str(item.get("feedback", ""))
        previous_status = str(item.get("status", "new"))
        item["dismissed_from_status"] = previous_status if previous_status in {"new", "saved", "liked", "read", "deprioritized"} else "new"
        item["dismissed_from_feedback"] = previous
        self.data = record_frontier_feedback_event(
            self.data,
            item_id,
            "dismiss",
            "不相关",
            classify_feedback_locally("不相关"),
        )
        item = self._find_item(item_id)
        if item is None:
            return
        item["feedback_adjustment"] = -15
        item["feedback"] = "topic_negative"
        item["status"] = "dismissed"
        self._record_research_signal(item_id, "ignore")
        self._save("已隐藏不相关论文；评价已保留给后续画像整理")
        show_undo_toast(self, "已隐藏该推荐", lambda: self._restore_recommendation(item_id))

    def _run_ai_rerank(self) -> None:
        if (self._ai_worker is not None and self._ai_worker.isRunning()) or (self._worker is not None and self._worker.isRunning()):
            return
        if self.ai_progress is not None:
            self.ai_progress.begin("正在检查 AI 配置…")
        if not is_deepseek_ready("frontier_rerank"):
            self.status_label.setText("尚未配置 DeepSeek。请在“设置 → 智能增强与 JCR”填写 API Key 后再试。")
            if self.ai_progress is not None:
                self.ai_progress.fail("AI 前沿复核未配置，未开始处理。")
            return
        candidates = [
            item
            for item in self.data.get("items", [])
            if item.get("status") == "new"
            and not any(blocked in str(item.get("journal", "")).casefold() for blocked in NON_JOURNAL_VENUES)
        ]
        candidates.sort(
            key=lambda item: (int(item.get("score", 0)), str(item.get("published_date", ""))),
            reverse=True,
        )
        if not candidates:
            self.status_label.setText("没有可供 AI 复核的新论文。请先检查更新。")
            if self.ai_progress is not None:
                self.ai_progress.fail("没有可供 AI 复核的新论文。")
            return
        self.ai_button.setEnabled(False)
        self._ai_rerank_action.setEnabled(False)
        self.ai_button.setText("AI 复核中…")
        if self.ai_progress is not None:
            self.ai_progress.update("DeepSeek 正在复核本地候选论文…", 5)
        self.status_label.setText("DeepSeek 正在复核本地候选论文…")
        profile = deepcopy(self.data.get("profile", {}))
        profile["authored_papers"] = load_papers()
        self._ai_worker = FrontierAiRerankThread(profile, candidates, self)
        self._ai_worker.progress.connect(self._ai_rerank_progress)
        self._ai_worker.completed.connect(self._ai_rerank_finished)
        self._ai_worker.failed.connect(self._ai_rerank_failed)
        self._ai_worker.finished.connect(self._clear_ai_worker)
        self._ai_worker.start()

    def _ai_rerank_finished(self, result: dict) -> None:
        updates = {
            str(item.get("id", "")): item
            for item in result.get("reviewed", [])
            if isinstance(item, dict) and str(item.get("id", ""))
        }
        for index, item in enumerate(self.data.get("items", [])):
            patch = updates.get(str(item.get("id", "")))
            if patch is None:
                continue
            if patch.get("admission_issue") == "reviewer_unavailable" and item.get("content_decision") == "accept":
                continue
            self.data["items"][index] = merge_frontier_refresh_item(patch, item, date.today().isoformat())
        if self._is_v13():
            eligible = [
                item for item in self.data.get("items", [])
                if isinstance(item, dict) and item.get("candidate_state") == "visible"
            ]
            pyramid = dynamic_pyramid(eligible, today=date.today())
            selected = {str(item.get("id", "")): item for item in pyramid}
            for index, item in enumerate(self.data.get("items", [])):
                if not isinstance(item, dict) or item.get("candidate_state") != "visible":
                    continue
                item_id = str(item.get("id", ""))
                if item_id in selected:
                    self.data["items"][index] = selected[item_id]
                elif item.get("status") not in {"saved", "liked", "locked"}:
                    item["candidate_state"] = "retained_unshown"
                    item["display_reason"] = "dual_low_or_outside_pyramid"
                    item["content_decision"] = "pending"
        else:
            self.data["items"] = apply_frontier_ranking(
                self.data.get("items", []),
                self.data.get("profile", {}),
                load_journal_library(),
            )
        self._save(f"DeepSeek 已复核 {len(updates)} 篇候选论文")
        if self.ai_progress is not None:
            self.ai_progress.complete("AI 前沿复核完成。")

    def _ai_rerank_failed(self, message: str) -> None:
        self.status_label.setText("AI 复核失败：" + str(message))
        if self.ai_progress is not None:
            self.ai_progress.fail("AI 前沿复核失败：" + str(message))

    def _ai_rerank_progress(self, message: str, value: int) -> None:
        self.status_label.setText(str(message))
        if self.ai_progress is not None:
            self.ai_progress.update(str(message), int(value))

    def _clear_ai_worker(self) -> None:
        if self._ai_worker is not None:
            self._ai_worker.deleteLater()
        self._ai_worker = None
        self.ai_button.setEnabled(True)
        self.ai_button.setText("AI 复核")
        self._ai_rerank_action.setEnabled(True)

    def _restore_recommendation(self, item_id: str) -> None:
        item = self._find_item(item_id)
        if item is None or item.get("status") != "dismissed":
            return
        item["status"] = str(item.get("dismissed_from_status", "")) or "new"
        item["feedback"] = str(item.get("dismissed_from_feedback", ""))
        item["dismissed_from_status"] = ""
        item["dismissed_from_feedback"] = ""
        item["feedback_adjustment"] = 0
        self._save("已恢复推荐")

    def _save(self, message: str = "") -> None:
        scroll_position = self.scroll.verticalScrollBar().value()
        self._persist_data()
        self._render()

        def restore_scroll_position() -> None:
            bar = self.scroll.verticalScrollBar()
            bar.setValue(min(scroll_position, bar.maximum()))

        QTimer.singleShot(0, restore_scroll_position)
        if message:
            self.status_label.setText(message + " · " + self.status_label.text())
        self.changed.emit()
