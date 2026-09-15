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
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QCheckBox,
    QButtonGroup,
    QSizePolicy,
)

from ui.dialogs import show_undo_toast
from ui.ai_progress import AiProgressPanel
from ui.research_profile_dialog import ResearchProfileDialog
from ui.frontier_settings_dialog import FrontierSettingsDialog as V115FrontierSettingsDialog

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
from utils.journal_quality import journal_quality_snapshot
from utils.easyscholar_service import is_easyscholar_ready
from utils.journal_health_service import import_frontier_journal
from utils.research_profile_service import normalize_research_profile_v11
from utils.research_profile_repository import (
    apply_organization_transaction,
    load_research_profile,
    save_research_profile,
    should_auto_organize,
    undo_last_organization,
)
from utils.research_signal_service import apply_profile_comment, record_signal
from utils.frontier_service import (
    FRONTIER_ALGORITHM_VERSION,
    NON_JOURNAL_VENUES,
    frontier_cache_is_stale,
    select_daily_recommendations,
    suggest_profile_keywords,
    update_daily_frontier,
    update_daily_frontier_v12,
    partition_frontier_items,
)
from utils.frontier_scoring import select_daily_mix
from utils.frontier_review_service import review_frontier_content
from utils.evidence_cache import EvidenceCache
from utils import file_manager
from utils.frontier_service import merge_frontier_refresh_item


class FrontierSourcesDialog(QDialog):
    """Choose practical public sources and keep optional personal keys local."""

    SOURCE_ROWS = (
        ("crossref", "Crossref", "最新 DOI、期刊与出版日期 · 无需 Key"),
        ("openalex", "OpenAlex", "综合论文与开放元数据 · Key 可选"),
        ("doaj", "DOAJ", "开放获取论文与来源关键词 · 无需 Key"),
        ("semantic_scholar", "Semantic Scholar", "相似论文与摘要 · 建议填写个人 Key"),
        ("arxiv", "arXiv", "预印本补充（遥感 / 方法类）· 无需 Key"),
    )

    def __init__(self, sources: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sources = deepcopy(sources if isinstance(sources, dict) else {})
        self._controls: dict[str, tuple[QCheckBox, QLineEdit]] = {}
        self.setWindowTitle("公开数据源与 API")
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
            QLineEdit { background: #243452; color: #ffffff; border: 1px solid #5d779f; border-radius: 5px; padding: 6px 8px; }
            QLineEdit:focus { border-color: #72d5ff; }
            QDialogButtonBox QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 14px; }
            QDialogButtonBox QPushButton[text="保存"] { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(9)
        title = QLabel("公开数据源与 API")
        title.setObjectName("settingsTitle")
        root.addWidget(title)
        hint = QLabel("Crossref、OpenAlex 与 DOAJ 默认启用。DOAJ 的论文关键词会优先参与匹配；没有来源关键词时才回退到标题与摘要。API Key 仅保存在本机数据文件中。")
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
            enabled.setChecked(bool(current.get("enabled", source_id in {"crossref", "openalex", "doaj"})))
            detail = QLabel(description)
            detail.setObjectName("sourceHint")
            key = QLineEdit(str(current.get("api_key", "")))
            key.setPlaceholderText("API Key（没有可留空）")
            key.setEchoMode(QLineEdit.EchoMode.Password)
            box.addWidget(enabled)
            box.addWidget(detail)
            box.addWidget(key)
            source_rows.addWidget(card)
            self._controls[source_id] = (enabled, key)
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
        return {
            source_id: {"enabled": enabled.isChecked(), "api_key": key.text().strip()}
            for source_id, (enabled, key) in self._controls.items()
        }


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


class FrontierSettingsDialog(QDialog):
    def __init__(self, profile: dict, suggested_terms: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profile = profile
        self._sources = deepcopy(profile.get("sources", {})) if isinstance(profile.get("sources", {}), dict) else {}
        self._suggested_terms = suggested_terms or []
        self.setWindowTitle("每日前沿设置")
        parent_width = parent.width() if parent else 540
        self.setMinimumWidth(370)
        self.setMaximumWidth(max(370, min(560, parent_width - 24)))
        self.resize(max(370, min(500, parent_width - 24)), 650)
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QDialog QLabel, QDialog QCheckBox { color: #f4f6ff; }
            QDialog QPlainTextEdit, QDialog QSpinBox { background: #202d4d; color: #ffffff; border: 1px solid #53698f; border-radius: 6px; padding: 7px 9px; }
            QDialog QPlainTextEdit:focus, QDialog QSpinBox:focus { border-color: #70c9ff; }
            QDialog QComboBox QAbstractItemView { background: #202d4d; color: #ffffff; selection-background-color: #2e78b7; selection-color: #ffffff; border: 1px solid #70c9ff; outline: 0; }
            QDialog QComboBox QAbstractItemView::item { min-height: 24px; padding: 5px 10px; color: #ffffff; }
            QDialog QComboBox QAbstractItemView::item:selected { background: #2e78b7; color: #ffffff; }
            QDialog QPushButton#subtleButton { background: #223456; color: #cae8ff; border: 1px solid #59719a; border-radius: 5px; padding: 6px 11px; }
            QDialog QPushButton#subtleButton:hover { background: #2b426b; }
            QDialog #configSummary { color: #aac7e4; font-size: 10px; padding-left: 2px; }
            QDialog QDialogButtonBox QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 16px; }
            QDialog QDialogButtonBox QPushButton[text="保存"] { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(11)
        hint = QLabel("研究画像：核心主题与关联方法分别填写。候选论文需经过内容审查，单独命中缩写、宽泛方法词或优先期刊不能直接进入推荐。")
        hint.setWordWrap(True)
        hint.setObjectName("formHint")
        root.addWidget(hint)
        controls = QHBoxLayout()
        sources = QPushButton("数据源与 API")
        sources.setObjectName("subtleButton")
        sources.clicked.connect(self._open_sources)
        controls.addWidget(sources)
        journals = QPushButton("优先期刊")
        journals.setObjectName("subtleButton")
        journals.clicked.connect(self._open_journals)
        controls.addWidget(journals)
        controls.addStretch()
        root.addLayout(controls)
        self.source_summary = QLabel()
        self.source_summary.setObjectName("configSummary")
        self.source_summary.setWordWrap(True)
        root.addWidget(self.source_summary)
        self.journal_summary = QLabel()
        self.journal_summary.setObjectName("configSummary")
        self.journal_summary.setWordWrap(True)
        root.addWidget(self.journal_summary)
        self.ai_summary = QLabel()
        self.ai_summary.setObjectName("configSummary")
        self.ai_summary.setWordWrap(True)
        root.addWidget(self.ai_summary)
        self._refresh_configuration_summary()
        self.detect_button = QPushButton("从本地记录识别方向")
        self.detect_button.setObjectName("subtleButton")
        self.detect_button.setToolTip("根据论文、灵感、待读和期刊库补充关联词；不会把宽泛方法词设为核心主题")
        self.detect_button.setEnabled(bool(self._suggested_terms))
        self.detect_button.clicked.connect(self._apply_suggestions)
        root.addWidget(self.detect_button, alignment=Qt.AlignmentFlag.AlignLeft)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(10)
        keyword_columns = QHBoxLayout()
        keyword_columns.setSpacing(9)
        primary_column = QVBoxLayout()
        primary_title = QLabel("一级关键词")
        primary_title.setObjectName("cardHeading")
        primary_hint = QLabel("核心主题；用逗号分隔")
        primary_hint.setObjectName("configSummary")
        self.primary_keywords = QPlainTextEdit(", ".join(str(item) for item in profile.get("primary_keywords", []) if str(item).strip()))
        self.primary_keywords.setPlaceholderText("例如：soil organic carbon, digital soil mapping")
        self.primary_keywords.setFixedHeight(82)
        primary_column.addWidget(primary_title)
        primary_column.addWidget(primary_hint)
        primary_column.addWidget(self.primary_keywords)
        secondary_column = QVBoxLayout()
        secondary_title = QLabel("二级关键词")
        secondary_title.setObjectName("cardHeading")
        secondary_hint = QLabel("方法、尺度或关联主题；用逗号分隔")
        secondary_hint.setObjectName("configSummary")
        self.secondary_keywords = QPlainTextEdit(", ".join(str(item) for item in profile.get("secondary_keywords", []) if str(item).strip()))
        self.secondary_keywords.setPlaceholderText("例如：remote sensing, machine learning")
        self.secondary_keywords.setFixedHeight(82)
        secondary_column.addWidget(secondary_title)
        secondary_column.addWidget(secondary_hint)
        secondary_column.addWidget(self.secondary_keywords)
        keyword_columns.addLayout(primary_column, 1)
        keyword_columns.addLayout(secondary_column, 1)
        root.addLayout(keyword_columns)
        self.negative = QPlainTextEdit(", ".join(str(item) for item in profile.get("negative_keywords", [])))
        self.negative.setPlaceholderText("可选：不希望推送的主题")
        self.negative.setFixedHeight(54)
        form.addRow("排除关键词", self.negative)
        self.daily_limit = QSpinBox()
        self.daily_limit.setRange(1, 12)
        self.daily_limit.setValue(int(profile.get("daily_limit", 5)))
        self.daily_limit.setSuffix(" 篇")
        form.addRow("每日推荐上限", self.daily_limit)
        self.lookback = QSpinBox()
        self.lookback.setRange(1, 30)
        self.lookback.setValue(int(profile.get("lookback_days", 7)))
        self.lookback.setSuffix(" 天")
        form.addRow("检索时间范围", self.lookback)
        self.notify = QCheckBox("发现新推荐时在 Windows 托盘提醒")
        self.notify.setChecked(bool(profile.get("notify", True)))
        form.addRow("通知", self.notify)
        root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _open_sources(self) -> None:
        dialog = FrontierSourcesDialog(self._sources, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._sources = dialog.sources()
            self._refresh_configuration_summary()

    def _open_journals(self) -> None:
        if FrontierJournalPriorityDialog(self).exec() == QDialog.DialogCode.Accepted:
            self._refresh_configuration_summary()

    def _refresh_configuration_summary(self) -> None:
        names = {
            "crossref": "Crossref",
            "openalex": "OpenAlex",
            "doaj": "DOAJ",
            "semantic_scholar": "Semantic Scholar",
            "arxiv": "arXiv",
        }
        enabled = [name for source_id, name in names.items() if bool(self._sources.get(source_id, {}).get("enabled", False))]
        keyed = [name for source_id, name in names.items() if str(self._sources.get(source_id, {}).get("api_key", "")).strip()]
        self.source_summary.setText(
            "当前数据源：" + (" · ".join(enabled) if enabled else "未启用")
            + (f"　|　已配置 Key：{' · '.join(keyed)}" if keyed else "")
        )
        journals = load_journal_library()
        must = sum(1 for journal in journals if journal.get("frontier_priority") == "必看")
        watch = sum(1 for journal in journals if journal.get("frontier_priority") == "关注")
        self.journal_summary.setText(f"当前优先期刊：必看 {must} 本 · 关注 {watch} 本")
        ai_terms = [str(term).strip() for term in self._profile.get("ai_search_terms", []) if str(term).strip()]
        ai_logic = str(self._profile.get("ai_search_logic", "")).strip()
        if ai_terms or ai_logic:
            terms_text = " · ".join(ai_terms[:4])
            suffix = f"　{ai_logic}" if ai_logic else ""
            self.ai_summary.setText(f"AI 检索焦点：{terms_text or '已校准'}{suffix}")
            self.ai_summary.show()
        else:
            self.ai_summary.hide()

    def _apply_suggestions(self) -> None:
        def split_terms(value: str) -> list[str]:
            return [part.strip() for part in value.replace("，", ",").replace("；", ",").replace("\n", ",").split(",") if part.strip()]

        combined: list[str] = []
        seen: set[str] = set()
        for value in [*split_terms(self.secondary_keywords.toPlainText()), *self._suggested_terms]:
            key = value.casefold()
            if key not in seen:
                seen.add(key)
                combined.append(value)
        self.secondary_keywords.setPlainText(", ".join(combined[:24]))
        self.detect_button.setText("已补充到二级关键词")

    def profile(self) -> dict:
        def terms(text: str) -> list[str]:
            values = text.replace("，", ",").replace("；", ",").replace("\n", ",").split(",")
            return [value.strip() for value in values if value.strip()]

        return {
            "version": 6,
            "primary_keywords": terms(self.primary_keywords.toPlainText()),
            "secondary_keywords": terms(self.secondary_keywords.toPlainText()),
            "negative_keywords": terms(self.negative.toPlainText()),
            "ai_search_terms": list(self._profile.get("ai_search_terms", [])),
            "ai_search_logic": str(self._profile.get("ai_search_logic", "")),
            "ai_profile_updated_at": str(self._profile.get("ai_profile_updated_at", "")),
            "ai_profile_attempted_at": str(self._profile.get("ai_profile_attempted_at", "")),
            "ai_profile_model": str(self._profile.get("ai_profile_model", "")),
            "ai_profile_source_signature": str(self._profile.get("ai_profile_source_signature", "")),
            "ai_profile_last_checked_at": str(self._profile.get("ai_profile_last_checked_at", "")),
            "daily_limit": self.daily_limit.value(),
            "lookback_days": self.lookback.value(),
            "notify": self.notify.isChecked(),
            "sources": self._sources,
            "feedback": dict(self._profile.get("feedback", {})),
        }


class FrontierRefreshThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, data: dict, journals: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = data
        self._journals = journals

    def run(self) -> None:
        try:
            self.completed.emit(
                update_daily_frontier_v12(
                    self._data,
                    self._journals,
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
            cache = EvidenceCache(file_manager.RESEARCH_INTELLIGENCE_CACHE_FILE)
            cache.initialize()
            reviewed = review_frontier_content(self._profile, self._items, load_journal_library(), cache=cache,
                progress=lambda message, value=0: self.progress.emit(str(message), int(value or 0)))
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
        total_value = int(item.get("score", 0) or 0)
        content_value = int(item.get("content_score", item.get("ai_score", -1)) or 0)
        score = QLabel(f"综合 {total_value}")
        score.setToolTip("综合分用于排序；内容最低线和 JCR 准入门槛在计分前独立执行")
        score.setObjectName("frontierScore")
        score.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        quality_row.addWidget(score)
        content_score = QLabel(f"内容 {content_value}" if content_value >= 0 else "内容待复核")
        content_score.setObjectName("frontierContentScore")
        content_score.setToolTip("AI 内容相关性分")
        content_score.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        quality_row.addWidget(content_score)
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
            quality_row.addWidget(journal_score)
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
            quality_row.addWidget(jcr_badge)
            cas_upgrade = str(item.get("cas_upgrade", "")).strip()
            cas_badge = QLabel(f"中科院 {cas_upgrade}" if cas_upgrade else "中科院未同步")
            cas_badge.setObjectName("frontierCasBadge")
            cas_badge.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            cas_badge.setToolTip(str(item.get("journal_metric_line", "")).strip() or "期刊指标尚未同步")
            quality_row.addWidget(cas_badge)
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
            for text, slot, tooltip in (
                ("原文", self._open_original, "打开论文原始页面"),
                ("待读", lambda: self.add_reading_requested.emit(str(item.get("id", ""))), "加入待读"),
                ("已读", lambda: self.read_requested.emit(str(item.get("id", ""))), "标记已读"),
                ("相关", lambda: self.relevant_requested.emit(str(item.get("id", ""))), "收藏并增强同类推荐"),
                ("忽略", lambda: self.irrelevant_requested.emit(str(item.get("id", ""))), "隐藏并作为强负样本"),
            ):
                button = QPushButton(text)
                button.setObjectName("dangerButton" if text == "忽略" else "rowButton")
                button.setToolTip(tooltip)
                button.setMinimumWidth(0)
                button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                button.clicked.connect(slot)
                actions.addWidget(button, 1)
            self.details_toggle = QPushButton("详情")
            self.details_toggle.setObjectName("rowButton")
            self.details_toggle.setCheckable(True)
            self.details_toggle.setMinimumWidth(0)
            self.details_toggle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self.details_toggle.toggled.connect(self._toggle_details)
            actions.addWidget(self.details_toggle, 1)
            feedback = QPushButton("评价")
            feedback.setObjectName("rowButton")
            feedback.setCheckable(True)
            feedback.setMinimumWidth(0)
            feedback.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            feedback.toggled.connect(self._toggle_feedback)
            actions.addWidget(feedback, 1)
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.data = self._load_data()
        self._worker: FrontierRefreshThread | None = None
        self._ai_worker: FrontierAiRerankThread | None = None
        self._profile_ai_worker: FrontierProfileAiThread | None = None
        self._comment_ai_worker: FrontierCommentAiThread | None = None
        self._profile_ai_refresh_after = False
        self._profile_ai_source_signature = ""
        self._stream_mode = "journal"
        self._last_regular_filter = "今日推荐"
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
        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.title_label = QLabel("每日前沿")
        self.title_label.setObjectName("paperPageTitle")
        self.title_label.setMinimumWidth(0)
        self.frontier_subtitle = QLabel("真实论文证据 · 长短期画像学习 · 分区核验")
        self.frontier_subtitle.setObjectName("dateLabel")
        self.frontier_subtitle.setMinimumWidth(0)
        self.frontier_subtitle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.frontier_subtitle)
        heading.addLayout(title_box, 1)
        self.settings_button = QPushButton("研究设置")
        self.settings_button.setObjectName("subtleButton")
        self.settings_button.clicked.connect(self._open_settings)
        heading.addWidget(self.settings_button, alignment=Qt.AlignmentFlag.AlignBottom)
        self.profile_update_button = QPushButton("更新画像")
        self.profile_update_button.setObjectName("subtleButton")
        self.profile_update_button.setToolTip("仅在成果、关联 PDF 或前沿点击偏好有变化时调用 DeepSeek")
        self.profile_update_button.clicked.connect(self.update_profile_now)
        heading.addWidget(self.profile_update_button, alignment=Qt.AlignmentFlag.AlignBottom)
        self.ai_button = QPushButton("AI 复核")
        self.ai_button.setObjectName("subtleButton")
        self.ai_button.setToolTip("按完整研究画像复核候选内容；复用有效缓存，每次最多新增审查 80 篇，其余保留待复核")
        self.ai_button.clicked.connect(self._run_ai_rerank)
        heading.addWidget(self.ai_button, alignment=Qt.AlignmentFlag.AlignBottom)
        self.more_actions_button = QPushButton("…")
        self.more_actions_button.setObjectName("iconButton")
        self.more_actions_button.setFixedWidth(36)
        self.more_actions_button.setToolTip("更多前沿操作")
        more_menu = QMenu(self.more_actions_button)
        self._profile_update_action = more_menu.addAction("更新研究画像")
        self._profile_update_action.triggered.connect(self.update_profile_now)
        self._undo_profile_action = more_menu.addAction("撤销今日画像整理")
        self._undo_profile_action.triggered.connect(self.undo_profile_organization)
        self._ai_rerank_action = more_menu.addAction("AI 复核候选论文")
        self._ai_rerank_action.triggered.connect(self._run_ai_rerank)
        self.more_actions_button.setMenu(more_menu)
        heading.addWidget(self.more_actions_button, alignment=Qt.AlignmentFlag.AlignBottom)
        self._refresh_button_full_text = "检查更新"
        self.refresh_button = QPushButton(self._refresh_button_full_text)
        self.refresh_button.setObjectName("primaryButton")
        self.refresh_button.clicked.connect(self.refresh_now)
        heading.addWidget(self.refresh_button, alignment=Qt.AlignmentFlag.AlignBottom)
        root.addLayout(heading)

        stream_row = QHBoxLayout()
        stream_row.setSpacing(5)
        self._stream_group = QButtonGroup(self)
        self._stream_group.setExclusive(True)
        self.frontier_journal_tab = QPushButton("期刊论文")
        self.frontier_journal_tab.setObjectName("frontierJournalTab")
        self.frontier_journal_tab.setCheckable(True)
        self.frontier_journal_tab.setChecked(True)
        self.frontier_journal_tab.clicked.connect(lambda: self._switch_stream("journal"))
        self._stream_group.addButton(self.frontier_journal_tab)
        stream_row.addWidget(self.frontier_journal_tab)
        self.frontier_preprint_tab = QPushButton("前沿预印本")
        self.frontier_preprint_tab.setObjectName("frontierPreprintTab")
        self.frontier_preprint_tab.setCheckable(True)
        self.frontier_preprint_tab.clicked.connect(lambda: self._switch_stream("preprint"))
        self._stream_group.addButton(self.frontier_preprint_tab)
        stream_row.addWidget(self.frontier_preprint_tab)
        stream_row.addStretch(1)
        self.pending_quality_button = QPushButton("待核验 0")
        self.pending_quality_button.setObjectName("frontierPendingQualityButton")
        self.pending_quality_button.setToolTip("分区未知或不是 JCR 一二区的论文不会进入主信息流")
        self.pending_quality_button.clicked.connect(self._open_pending_review)
        stream_row.addWidget(self.pending_quality_button)
        root.addLayout(stream_row)

        filter_row = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setObjectName("sectionLabel")
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        filter_row.addWidget(self.status_label, 1)
        self.filter_combo = QComboBox()
        self.filter_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.filter_combo.setMinimumContentsLength(6)
        self.filter_combo.setMinimumWidth(112)
        self.filter_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.filter_combo.addItems(["今日推荐", "优先期刊", "全部记录", "已加入待读", "已读", "已标记相关", "太泛", "已隐藏", "待内容复核", "分区待核验", "已排除候选"])
        self.filter_combo.currentTextChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self.filter_combo)
        root.addLayout(filter_row)
        self.cache_hint = QLabel()
        self.cache_hint.setObjectName("dateLabel")
        self.cache_hint.setWordWrap(True)
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
        self.profile_update_button.setHidden(compact)
        self.ai_button.setHidden(compact)
        self.more_actions_button.setVisible(compact)
        self.frontier_subtitle.setHidden(compact)
        self.status_label.setMaximumWidth(max(80, self.width() - 150) if compact else 16_777_215)
        self.refresh_button.setText("更新" if compact else self._refresh_button_full_text)
        self.refresh_button.setToolTip(self._refresh_button_full_text)

    def _switch_stream(self, stream: str) -> None:
        self._stream_mode = "preprint" if stream == "preprint" else "journal"
        self.frontier_journal_tab.setChecked(self._stream_mode == "journal")
        self.frontier_preprint_tab.setChecked(self._stream_mode == "preprint")
        if self.filter_combo.currentText() in {"待内容复核", "分区待核验", "已排除候选"}:
            self.filter_combo.setCurrentText(self._last_regular_filter or "今日推荐")
            self.scroll.verticalScrollBar().setValue(0)
            return
        self._render_limit = self._render_batch_size
        self._render()
        self.scroll.verticalScrollBar().setValue(0)

    def _open_pending_review(self) -> None:
        streams = self._stream_partitions()
        self.filter_combo.setCurrentText("待内容复核" if streams["pending_content"] else "分区待核验")

    def _on_filter_changed(self, text: str) -> None:
        if text not in {"待内容复核", "分区待核验", "已排除候选"}:
            self._last_regular_filter = str(text)
        self._render_limit = self._render_batch_size
        self._render()
        self.scroll.verticalScrollBar().setValue(0)

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
            self.refresh_now()

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
            "read": 25,
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
        feedback_values = {"relevant", "irrelevant", "too_broad", "read"}
        status_values = {"liked", "dismissed", "deprioritized", "read"}
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
            QTimer.singleShot(180, self.refresh_now)

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
            QTimer.singleShot(180, self.refresh_now)

    def _clear_profile_ai_worker(self) -> None:
        if self._profile_ai_worker is not None:
            self._profile_ai_worker.deleteLater()
        self._profile_ai_worker = None
        self._profile_ai_refresh_after = False
        self._profile_ai_source_signature = ""
        self.profile_update_button.setEnabled(True)
        self._profile_update_action.setEnabled(True)

    def refresh_now(self) -> None:
        if (self._worker is not None and self._worker.isRunning()) or (self._ai_worker is not None and self._ai_worker.isRunning()):
            return
        self.refresh_button.setEnabled(False)
        self.status_label.setText("正在检查公开论文元数据…")
        if self.ai_progress is not None:
            self.ai_progress.begin("正在准备多源论文检索…")
        refresh_data = deepcopy(self.data)
        refresh_data["authored_papers"] = [dict(item) for item in load_papers() if isinstance(item, dict)]
        self._worker = FrontierRefreshThread(refresh_data, load_journal_library(), self)
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

    def _refresh_finished(self, result: dict) -> None:
        latest = {str(i["id"]): i for i in self.data.get("items", [])}
        current_profile = self.data.get("profile", {})
        self.data = result["data"]
        self.data["profile"] = current_profile
        self.data["items"] = [merge_frontier_refresh_item(i, latest.get(str(i["id"])), date.today().isoformat())
                              for i in self.data.get("items", [])]
        returned_ids = {str(i["id"]) for i in self.data["items"]}
        self.data["items"].extend(dict(i) for key, i in latest.items() if key not in returned_ids
            and i.get("status") in {"saved", "liked", "read", "dismissed", "deprioritized"})
        errors = result.get("errors", [])
        status = "已更新"
        if errors:
            status += f"（{len(errors)} 项检索未完成）"
        self._save(status)
        if self.ai_progress is not None:
            self.ai_progress.complete(str(result.get("brief", "每日前沿已更新")))
        if (
            result.get("visible_count", 0)
            and self.data.get("profile", {}).get("notify", True)
            and self.data.get("last_notified") != date.today().isoformat()
        ):
            self.data["last_notified"] = date.today().isoformat()
            self._persist_data()
            self.daily_ready.emit(int(result.get("visible_count", 0)), str(result.get("brief", "")))

    def _refresh_failed(self, message: str) -> None:
        self.status_label.setText("更新失败：请检查网络后重试")
        if self.ai_progress is not None:
            self.ai_progress.fail("每日前沿更新失败：" + str(message))
        # 失败状态直接留在页面上，避免窄窗口下的系统对话框遮挡内容。

    def _stream_partitions(self) -> dict[str, list[dict]]:
        all_items = [item for item in self.data.get("items", []) if isinstance(item, dict)]
        streams = {"journal": [], "preprint": [], "pending_quality": [], "pending_content": [], "rejected": []}
        for item in all_items:
            decision = item.get("content_decision")
            if decision != "accept":
                if item.get("status") not in {"read", "saved", "liked", "dismissed", "deprioritized"}:
                    streams["rejected" if decision == "reject" else "pending_content"].append(item)
                continue
            state = str(item.get("quality_gate_state", "")).strip()
            if state == "preprint" or item.get("is_preprint"):
                streams["preprint"].append(item)
            elif state != "eligible":
                streams["pending_quality"].append(item)
            else:
                streams["journal"].append(item)
        return streams

    def _visible_items(self) -> list[dict]:
        status = self.filter_combo.currentText()
        streams = self._stream_partitions()
        special = {"待内容复核": "pending_content", "分区待核验": "pending_quality", "已排除候选": "rejected"}
        if status in special:
            return streams[special[status]]
        all_items = list(streams["preprint" if self._stream_mode == "preprint" else "journal"])
        if status in {"已加入待读", "已读", "已标记相关", "太泛", "已隐藏", "全部记录"}:
            all_items = [i for i in self.data.get("items", []) if bool(i.get("is_preprint")) == (self._stream_mode == "preprint")]
        if status == "已隐藏":
            items = [item for item in all_items if item.get("status") == "dismissed"]
        elif status == "太泛":
            items = [item for item in all_items if item.get("status") == "deprioritized"]
        else:
            items = [item for item in all_items if item.get("status") not in {"dismissed", "deprioritized"}]
        items.sort(
            key=lambda item: (
                int(item.get("score", 0)),
                str(item.get("published_date", "")),
                str(item.get("id", "")),
            ),
            reverse=True,
        )
        if status == "已加入待读":
            return [item for item in items if item.get("status") == "saved"]
        if status == "已读":
            return [item for item in items if item.get("status") == "read"]
        if status == "已标记相关":
            return [item for item in items if item.get("status") == "liked"]
        if status == "优先期刊":
            return [
                item
                for item in items
                if item.get("priority") in {"必看", "关注"}
            ]
        if status == "今日推荐":
            try:
                limit = int(self.data.get("profile", {}).get("daily_limit", 5))
            except (TypeError, ValueError):
                limit = 5
            daily_candidates = [
                item
                for item in items
                if str(item.get("status", "new")).strip().casefold() != "read"
            ]
            return select_daily_mix(daily_candidates, limit=limit, minimum_core_ratio=0.5)
        return items

    def _update_cache_hint(self) -> None:
        stale = frontier_cache_is_stale(self.data, load_journal_library())
        if stale:
            self.cache_hint.setText("研究设置或排序规则已变化；当前展示仍是旧缓存。点击“按新规则更新”即可重新计算。")
            self.cache_hint.show()
            self._refresh_button_full_text = "按新规则更新"
        else:
            self.cache_hint.hide()
            self._refresh_button_full_text = "检查更新"
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
        streams = self._stream_partitions()
        self.pending_quality_button.setText(f"待审 {len(streams['pending_content']) + len(streams['pending_quality'])}")
        self.pending_quality_button.setToolTip(f"内容待审 {len(streams['pending_content'])} 篇；分区待核验 {len(streams['pending_quality'])} 篇")
        self.frontier_journal_tab.setText(f"期刊论文 {len(streams['journal'])}")
        self.frontier_preprint_tab.setText(f"前沿预印本 {len(streams['preprint'])}")
        checked = str(self.data.get("last_checked", ""))
        stream_name = "前沿预印本" if self._stream_mode == "preprint" else "期刊论文"
        count_text = f"显示 {len(rendered)}/{len(visible)} 篇" if len(rendered) < len(visible) else f"当前 {len(visible)} 篇"
        self.status_label.setText(f"{stream_name} · {checked or '尚未检查'} · {count_text}")
        self._update_cache_hint()
        for item in rendered:
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
            profile = self.data.get("profile", {}) if isinstance(self.data.get("profile", {}), dict) else {}
            configured = bool(profile.get("_easyscholar_configured", False)) or is_easyscholar_ready()
            message = (
                "暂时没有新的前沿预印本。点击“检查更新”继续从公开来源发现。"
                if self._stream_mode == "preprint"
                else "暂无同时通过内容相关性和期刊分区筛选的论文。"
                if configured and self.filter_combo.currentText() == "今日推荐"
                else "还没有可展示的前沿论文。点击“检查更新”，或先在研究设置中填写关键词。"
            )
            empty = QLabel(message)
            empty.setObjectName("emptyLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rows.insertWidget(0, empty)

    def _load_more(self) -> None:
        scroll_position = self.scroll.verticalScrollBar().value()
        self._render_limit += self._render_batch_size
        self._render()
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(scroll_position))

    def _open_settings(self) -> None:
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
        self._record_research_signal(item_id, "detail_open")
        self._persist_data()

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
        self._record_research_signal(item_id, "read")
        self._save("已移出今日推荐并归入已读，同时同步到阅读记录")

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
