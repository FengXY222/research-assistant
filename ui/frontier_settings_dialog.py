"""Large, reviewable Daily Frontier settings workbench for v11.5."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.research_profile_dialog import ResearchProfileDialog
from ui.ai_progress import AiProgressPanel
from ui.icons import lucide_icon
from utils.ai_service import (
    DeepSeekConfigurationError,
    DeepSeekRequestError,
    extract_research_keywords_with_ai,
    is_deepseek_ready,
)
from utils.file_manager import load_journal_library, load_papers, save_journal_library
from utils.frontier_scoring import frontier_ranking_settings, journal_preference_score
from utils.journal_quality import journal_quality_snapshot
from utils.pdf_text_service import PdfTextExtractionError, extract_pdf_full_text
from utils.research_profile_service import add_pending_term, canonical_term_key, normalize_research_profile_v11


class PdfKeywordImportThread(QThread):
    """Read a selected PDF away from the settings dialog's event loop."""

    completed = Signal(dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self._path = str(path)

    def run(self) -> None:
        try:
            self.completed.emit(
                extract_pdf_full_text(
                    self._path,
                    progress=lambda message, current, total, _stage: self.progress.emit(
                        str(message),
                        max(1, min(70, int((current / max(1, total)) * 70))),
                    ),
                    cancelled=self.isInterruptionRequested,
                )
            )
        except PdfTextExtractionError as error:
            self.failed.emit(str(error))
        except Exception:  # noqa: BLE001 - desktop users need a short safe message
            self.failed.emit("无法读取 PDF；文件可能损坏或不受支持")


class KeywordExtractionAiThread(QThread):
    """Extract research concepts from a paper/PDF without blocking the dialog."""

    completed = Signal(dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, source: dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self._source = deepcopy(source)

    def run(self) -> None:
        try:
            self.completed.emit(
                extract_research_keywords_with_ai(
                    self._source,
                    lambda message, value=0: self.progress.emit(str(message), int(value or 0)),
                )
            )
        except (DeepSeekConfigurationError, DeepSeekRequestError) as error:
            self.failed.emit(str(error))
        except Exception:  # noqa: BLE001 - keep provider failures inside the dialog
            self.failed.emit("AI 关键词提取未完成，请稍后重试。")


class JournalPriorityBatchEditor(QWidget):
    """Filter, select and classify journals in batches."""

    PRIORITIES = (
        ("必看", "必看", 100),
        ("订阅", "关注", 80),
        ("探索", "扩展", 60),
        ("不订阅", "不订阅", 50),
    )

    def __init__(self, journals: list[dict[str, Any]] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("journalPriorityBatchEditor")
        self._journals = deepcopy(journals if journals is not None else load_journal_library())
        self._dirty = False

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 10, 1, 1)
        root.setSpacing(8)
        heading = QLabel("批量设置期刊优先级")
        heading.setObjectName("settingsSectionTitle")
        root.addWidget(heading)
        hint = QLabel("先筛选并选择一批期刊，再统一设为必看、订阅、探索或不订阅；分类会同步对应期刊分。")
        hint.setObjectName("settingsSectionHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        filters = QHBoxLayout()
        filters.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("journalPrioritySearch")
        self.search_edit.setPlaceholderText("搜索期刊、出版社或 ISSN")
        self.search_edit.setClearButtonEnabled(True)
        filters.addWidget(self.search_edit, 2)
        self.publisher_filter = QComboBox()
        self.publisher_filter.setObjectName("journalPriorityPublisherFilter")
        self.publisher_filter.addItem("出版社：全部", "")
        for publisher in sorted(
            {str(journal.get("publisher", "")).strip() for journal in self._journals if str(journal.get("publisher", "")).strip()},
            key=str.casefold,
        ):
            self.publisher_filter.addItem(publisher, publisher)
        filters.addWidget(self.publisher_filter, 1)
        self.jcr_filter = QComboBox()
        self.jcr_filter.setObjectName("journalPriorityJcrFilter")
        self.jcr_filter.addItem("JCR：全部", "")
        for quartile in ("Q1", "Q2", "Q3", "Q4"):
            self.jcr_filter.addItem(quartile, quartile)
        filters.addWidget(self.jcr_filter)
        self.priority_filter = QComboBox()
        self.priority_filter.setObjectName("journalPriorityCategoryFilter")
        self.priority_filter.addItem("分类：全部", "")
        for label, value, _score in self.PRIORITIES:
            self.priority_filter.addItem(label, value)
        filters.addWidget(self.priority_filter)
        root.addLayout(filters)

        selection = QHBoxLayout()
        selection.setSpacing(6)
        self.select_visible_button = QPushButton("选择当前结果")
        self.select_visible_button.setObjectName("journalPrioritySelectVisible")
        self.select_visible_button.clicked.connect(self._select_visible)
        selection.addWidget(self.select_visible_button)
        self.clear_selection_button = QPushButton("清除选择")
        self.clear_selection_button.setObjectName("journalPriorityClearSelection")
        self.clear_selection_button.clicked.connect(self._clear_selection)
        selection.addWidget(self.clear_selection_button)
        selection.addStretch(1)
        self.must_read_button = self._priority_button("设为必看", "journalPriorityMustRead", "必看", selection)
        self.subscribe_button = self._priority_button("设为订阅", "journalPrioritySubscribe", "关注", selection)
        self.explore_button = self._priority_button("设为探索", "journalPriorityExplore", "扩展", selection)
        self.unsubscribe_button = self._priority_button("设为不订阅", "journalPriorityUnsubscribe", "不订阅", selection)
        root.addLayout(selection)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("frontierSettingsHint")
        root.addWidget(self.summary_label)
        self.table = QTableWidget()
        self.table.setObjectName("journalPriorityTable")
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["选择", "期刊", "分区", "当前分类"])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        root.addWidget(self.table, 1)

        self.search_edit.textChanged.connect(self._apply_filter)
        self.publisher_filter.currentIndexChanged.connect(self._apply_filter)
        self.jcr_filter.currentIndexChanged.connect(self._apply_filter)
        self.priority_filter.currentIndexChanged.connect(self._apply_filter)
        self.table.itemChanged.connect(self._update_summary)
        self._populate_table()

    @classmethod
    def _display_priority(cls, value: str) -> str:
        return next((label for label, stored, _score in cls.PRIORITIES if stored == value), "不订阅")

    def _priority_button(self, text: str, object_name: str, value: str, layout: QHBoxLayout) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.clicked.connect(lambda _checked=False, selected=value: self._apply_priority(selected))
        layout.addWidget(button)
        return button

    def _populate_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._journals))
        for row_index, journal in enumerate(self._journals):
            selector = QTableWidgetItem()
            selector.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            selector.setCheckState(Qt.CheckState.Unchecked)
            selector.setData(Qt.ItemDataRole.UserRole, row_index)
            self.table.setItem(row_index, 0, selector)
            name = str(journal.get("name", "")).strip() or "未命名期刊"
            publisher = str(journal.get("publisher", "")).strip()
            self.table.setItem(row_index, 1, QTableWidgetItem(name + (f"\n{publisher}" if publisher else "")))
            snapshot = journal_quality_snapshot(journal)
            divisions = " · ".join(
                part
                for part in (
                    str(snapshot.get("jcr_quartile", "")).strip() or "JCR 未知",
                    f"中科院 {str(snapshot.get('cas_upgrade', '')).strip()}" if str(snapshot.get("cas_upgrade", "")).strip() else "中科院未知",
                )
                if part
            )
            self.table.setItem(row_index, 2, QTableWidgetItem(divisions))
            self.table.setItem(
                row_index,
                3,
                QTableWidgetItem(self._display_priority(str(journal.get("frontier_priority", "不订阅")))),
            )
            self.table.setRowHeight(row_index, 44)
        self.table.blockSignals(False)
        self._apply_filter()

    def _apply_filter(self) -> None:
        query = " ".join(self.search_edit.text().casefold().split())
        publisher = str(self.publisher_filter.currentData() or "").casefold()
        jcr = str(self.jcr_filter.currentData() or "").upper()
        priority = str(self.priority_filter.currentData() or "")
        for row_index, journal in enumerate(self._journals):
            snapshot = journal_quality_snapshot(journal)
            haystack = " ".join(str(journal.get(field, "")) for field in ("name", "publisher", "issn")).casefold()
            journal_publisher = str(journal.get("publisher", "")).strip().casefold()
            quartile = str(snapshot.get("jcr_quartile", "")).strip().upper()
            journal_priority = str(journal.get("frontier_priority", "不订阅"))
            visible = (
                (not query or query in haystack)
                and (not publisher or journal_publisher == publisher)
                and (not jcr or quartile == jcr)
                and (not priority or journal_priority == priority)
            )
            self.table.setRowHidden(row_index, not visible)
        self._update_summary()

    def _select_visible(self) -> None:
        self.table.blockSignals(True)
        for row_index in range(self.table.rowCount()):
            if not self.table.isRowHidden(row_index):
                self.table.item(row_index, 0).setCheckState(Qt.CheckState.Checked)
        self.table.blockSignals(False)
        self._update_summary()

    def _clear_selection(self) -> None:
        self.table.blockSignals(True)
        for row_index in range(self.table.rowCount()):
            self.table.item(row_index, 0).setCheckState(Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self._update_summary()

    def _selected_indices(self) -> list[int]:
        return [
            int(self.table.item(row_index, 0).data(Qt.ItemDataRole.UserRole))
            for row_index in range(self.table.rowCount())
            if self.table.item(row_index, 0).checkState() == Qt.CheckState.Checked
        ]

    def _apply_priority(self, priority: str) -> None:
        selected = self._selected_indices()
        if not selected:
            self.summary_label.setText("请先选择期刊，或使用“选择当前结果”。")
            return
        score = next(score for _label, stored, score in self.PRIORITIES if stored == priority)
        for source_index in selected:
            self._journals[source_index]["frontier_priority"] = priority
            self._journals[source_index]["frontier_score"] = score
            self.table.item(source_index, 3).setText(self._display_priority(priority))
        self._dirty = True
        self._update_summary(f"已将 {len(selected)} 本期刊设为{self._display_priority(priority)}")

    def _update_summary(self, message: str = "") -> None:
        selected = len(self._selected_indices())
        visible = sum(not self.table.isRowHidden(row) for row in range(self.table.rowCount()))
        prefix = f"{message} · " if message else ""
        self.summary_label.setText(f"{prefix}当前显示 {visible}/{len(self._journals)} 本 · 已选择 {selected} 本")

    def journals_with_priorities(self) -> list[dict[str, Any]]:
        return deepcopy(self._journals)

    def has_changes(self) -> bool:
        return self._dirty

    def replace_journals(self, journals: list[dict[str, Any]]) -> None:
        self._journals = deepcopy(journals)
        self._dirty = False
        self._populate_table()


class JournalPreferenceScoreDialog(QDialog):
    """Searchable journal score table used by the large settings workbench."""

    def __init__(self, journals: list[dict[str, Any]] | None = None, parent=None) -> None:
        super().__init__(parent)
        self._journals = deepcopy(journals if journals is not None else load_journal_library())
        self._score_spins: list[QSpinBox] = []
        self._explicit_rows: list[bool] = []
        self.setWindowTitle("每日前沿 · 管理期刊分")
        self.resize(860, 640)
        self.setMinimumSize(720, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(9)
        title = QLabel("期刊偏好分")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        hint = QLabel("期刊分只调整已通过内容与分区门槛的论文顺序。0 分表示尽量后排，100 分表示最高优先。")
        hint.setObjectName("dialogSubtitle")
        hint.setWordWrap(True)
        root.addWidget(hint)

        tools = QHBoxLayout()
        tools.setSpacing(7)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("journalScoreSearch")
        self.search_edit.setPlaceholderText("搜索期刊、出版社或 ISSN")
        self.search_edit.setClearButtonEnabled(True)
        tools.addWidget(self.search_edit, 1)
        self.filter_combo = QComboBox()
        self.filter_combo.setObjectName("journalScoreFilter")
        self.filter_combo.addItem("全部期刊", "all")
        self.filter_combo.addItem("已评分", "scored")
        self.filter_combo.addItem("未评分", "unscored")
        self.filter_combo.addItem("JCR Q1", "q1")
        self.filter_combo.addItem("JCR Q2", "q2")
        tools.addWidget(self.filter_combo)
        root.addLayout(tools)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("frontierSettingsHint")
        root.addWidget(self.summary_label)
        self.table = QTableWidget(len(self._journals), 4)
        self.table.setObjectName("journalScoreTable")
        self.table.setHorizontalHeaderLabels(["期刊", "分区", "原优先级", "期刊分"])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(3, 110)
        for row_index, journal in enumerate(self._journals):
            snapshot = journal_quality_snapshot(journal)
            name_parts = [str(journal.get("name", "")).strip() or "未命名期刊"]
            secondary = " · ".join(
                part for part in (str(journal.get("publisher", "")).strip(), str(journal.get("issn", "")).strip()) if part
            )
            if secondary:
                name_parts.append(secondary)
            name_item = QTableWidgetItem("\n".join(name_parts))
            name_item.setData(Qt.ItemDataRole.UserRole, row_index)
            self.table.setItem(row_index, 0, name_item)
            divisions = " · ".join(
                part
                for part in (
                    str(snapshot.get("jcr_quartile", "")).strip() or "JCR 未知",
                    ("中科院 " + str(snapshot.get("cas_upgrade", "")).strip()) if str(snapshot.get("cas_upgrade", "")).strip() else "中科院未知",
                )
                if part
            )
            self.table.setItem(row_index, 1, QTableWidgetItem(divisions))
            self.table.setItem(row_index, 2, QTableWidgetItem(str(journal.get("frontier_priority", "不订阅"))))
            score = QSpinBox()
            score.setObjectName("journalPreferenceScoreSpin")
            score.setRange(0, 100)
            score.setSuffix(" 分")
            score.setValue(journal_preference_score(journal, default_score=50))
            score.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setCellWidget(row_index, 3, score)
            self.table.setRowHeight(row_index, 46)
            self._score_spins.append(score)
            self._explicit_rows.append(journal.get("frontier_score") is not None and journal.get("frontier_score") != "")
        root.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.search_edit.textChanged.connect(self._apply_filter)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        self._update_summary()

    def _apply_filter(self) -> None:
        query = " ".join(self.search_edit.text().casefold().split())
        mode = str(self.filter_combo.currentData() or "all")
        for row_index, journal in enumerate(self._journals):
            snapshot = journal_quality_snapshot(journal)
            haystack = " ".join(
                str(journal.get(field, "")) for field in ("name", "publisher", "issn")
            ).casefold()
            quartile = str(snapshot.get("jcr_quartile", "")).strip().upper()
            matches_mode = (
                mode == "all"
                or (mode == "scored" and self._explicit_rows[row_index])
                or (mode == "unscored" and not self._explicit_rows[row_index])
                or (mode == "q1" and quartile == "Q1")
                or (mode == "q2" and quartile == "Q2")
            )
            self.table.setRowHidden(row_index, bool(query and query not in haystack) or not matches_mode)

    def _update_summary(self) -> None:
        scored = sum(self._explicit_rows)
        self.summary_label.setText(f"共 {len(self._journals)} 本期刊 · 已单独评分 {scored} 本 · 未评分沿用旧优先级或默认分")

    def journals_with_scores(self) -> list[dict[str, Any]]:
        result = deepcopy(self._journals)
        for journal, score in zip(result, self._score_spins):
            journal["frontier_score"] = score.value()
        return result

    def _save(self) -> None:
        save_journal_library(self.journals_with_scores())
        self.accept()


class FrontierSettingsDialog(ResearchProfileDialog):
    """A full settings module while retaining the proven profile editor core."""

    def __init__(self, profile: dict[str, Any], parent=None) -> None:
        self._frontier_ui_ready = False
        self._frontier_sections_built = False
        self._keyword_notice = ""
        self._pdf_worker: PdfKeywordImportThread | None = None
        self._keyword_ai_worker: KeywordExtractionAiThread | None = None
        super().__init__(profile, parent)
        self.setWindowTitle("每日前沿 · 研究设置 · 科研助手")
        title = self.findChild(QLabel, "dialogTitle")
        subtitle = self.findChild(QLabel, "dialogSubtitle")
        if title is not None:
            title.setText("每日前沿 · 研究设置")
        if subtitle is not None:
            subtitle.setText("维护研究方向、前沿质量边界与待确认关键词；保存不会直接改写锁定词。")
        # This opens outside the compact widget as a dedicated, stable
        # workspace instead of inheriting the widget's narrow dimensions.
        self.setFixedSize(1024, 768)
        self._frontier_ui_ready = True
        self._inject_frontier_sections()
        self._render()

    def _render(self) -> None:
        super()._render()
        if self._frontier_ui_ready:
            self._inject_frontier_sections()

    @staticmethod
    def _section(title: str, hint: str = "") -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("frontierSettingsSection")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        heading = QLabel(title)
        heading.setObjectName("settingsSectionTitle")
        layout.addWidget(heading)
        if hint:
            label = QLabel(hint)
            label.setObjectName("settingsSectionHint")
            label.setWordWrap(True)
            layout.addWidget(label)
        return frame, layout

    def _inject_frontier_sections(self) -> None:
        if self._frontier_sections_built:
            return
        quality_frame, quality = self._section(
            "每日前沿质量边界",
            "已确认的 JCR Q3/Q4 仅保留去重指纹；缺失信息和未知分区候选继续后台补全，不会以 0 分推送。",
        )
        self.require_q1_q2 = QCheckBox("默认只推荐已核验 JCR Q1 / Q2")
        self.require_q1_q2.setObjectName("requireQ1Q2Check")
        self.require_q1_q2.setChecked(bool(self._profile.get("require_verified_jcr_q1_q2", True)))
        quality.addWidget(self.require_q1_q2)
        self._add_quality_option_row(quality)
        self.retain_unread = QCheckBox("未读论文次日继续保留在每日前沿")
        self.retain_unread.setObjectName("retainUnreadFrontierCheck")
        self.retain_unread.setChecked(bool(self._profile.get("retain_unread", True)))
        self.retain_unread.setToolTip("关闭后，未读卡片次日删除内容，但仍保留去重指纹，今后不会重复推送")
        quality.addWidget(self.retain_unread)
        self.show_preprints = QCheckBox("在同一金字塔中显示预印本")
        self.show_preprints.setObjectName("showFrontierPreprintsCheck")
        self.show_preprints.setChecked(bool(self._profile.get("show_preprints", True)))
        quality.addWidget(self.show_preprints)
        ranking = frontier_ranking_settings(self._profile)
        weight_row = QHBoxLayout()
        weight_row.setSpacing(8)
        weight_label = QLabel("期刊分权重")
        weight_label.setObjectName("settingsFieldLabel")
        weight_row.addWidget(weight_label)
        self.journal_weight_slider = QSlider(Qt.Orientation.Horizontal)
        self.journal_weight_slider.setObjectName("journalWeightSlider")
        self.journal_weight_slider.setRange(0, 50)
        self.journal_weight_slider.setSingleStep(5)
        self.journal_weight_slider.setPageStep(5)
        self.journal_weight_slider.setValue(ranking["journal_weight"])
        self.journal_weight_slider.setToolTip("其余权重自动分配给 AI 内容相关性")
        weight_row.addWidget(self.journal_weight_slider, 1)
        self.journal_weight_spin = QSpinBox()
        self.journal_weight_spin.setObjectName("journalWeightSpin")
        self.journal_weight_spin.setRange(0, 50)
        self.journal_weight_spin.setSuffix("%")
        self.journal_weight_spin.setValue(ranking["journal_weight"])
        self.journal_weight_slider.valueChanged.connect(self.journal_weight_spin.setValue)
        self.journal_weight_spin.valueChanged.connect(self.journal_weight_slider.setValue)
        weight_row.addWidget(self.journal_weight_spin)
        quality.addLayout(weight_row)

        default_row = QHBoxLayout()
        default_row.setSpacing(8)
        default_label = QLabel("未评分期刊")
        default_label.setObjectName("settingsFieldLabel")
        default_row.addWidget(default_label)
        self.default_journal_score_spin = QSpinBox()
        self.default_journal_score_spin.setObjectName("defaultJournalScoreSpin")
        self.default_journal_score_spin.setRange(0, 100)
        self.default_journal_score_spin.setSuffix(" 分")
        self.default_journal_score_spin.setValue(ranking["default_journal_score"])
        default_row.addWidget(self.default_journal_score_spin)
        self.manage_journal_scores_button = QPushButton("管理期刊分")
        self.manage_journal_scores_button.setObjectName("manageJournalScoresButton")
        self.manage_journal_scores_button.setIcon(
            lucide_icon("sliders-horizontal", self.manage_journal_scores_button.palette().color(QPalette.ColorRole.ButtonText))
        )
        self.manage_journal_scores_button.clicked.connect(self._open_journal_scores)
        default_row.addWidget(self.manage_journal_scores_button)
        default_row.addStretch(1)
        quality.addLayout(default_row)
        self.journal_score_summary = QLabel()
        self.journal_score_summary.setObjectName("frontierSettingsHint")
        self.journal_score_summary.setWordWrap(True)
        quality.addWidget(self.journal_score_summary)
        self._refresh_journal_score_summary()
        self.journal_weight_spin.valueChanged.connect(lambda _value: self._refresh_journal_score_summary())

        keyword_frame, keyword_layout = self._section(
            "关键词工作台",
            "手动补充、AI 建议和从论文/PDF 提取的词先进入待确认区；确认后才参与下一次推荐。",
        )
        add_row = QHBoxLayout()
        add_row.setSpacing(7)
        self.manual_keyword_edit = QLineEdit()
        self.manual_keyword_edit.setObjectName("manualKeywordEdit")
        self.manual_keyword_edit.setPlaceholderText("输入关键词，例如 carbon sequestration")
        self.manual_keyword_edit.setMinimumWidth(0)
        self.manual_keyword_edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        add_row.addWidget(self.manual_keyword_edit, 1)
        add_button = QPushButton("放入待确认")
        add_button.setObjectName("addPendingKeywordButton")
        add_button.setIcon(lucide_icon("check", add_button.palette().color(QPalette.ColorRole.ButtonText)))
        add_button.clicked.connect(self._add_manual_keyword)
        add_row.addWidget(add_button)
        keyword_layout.addLayout(add_row)

        paper_label = QLabel("从论文记录提取")
        paper_label.setObjectName("settingsFieldLabel")
        keyword_layout.addWidget(paper_label)
        self._keyword_papers = [dict(item) for item in load_papers() if isinstance(item, dict) and str(item.get("id", "")).strip()]
        self.keyword_paper_combo = QComboBox()
        self.keyword_paper_combo.setObjectName("keywordPaperCombo")
        self.keyword_paper_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.keyword_paper_combo.setMinimumContentsLength(1)
        self.keyword_paper_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        selected_paper_id = str(getattr(self, "_selected_keyword_paper_id", ""))
        if self._keyword_papers:
            for paper in self._keyword_papers:
                self.keyword_paper_combo.addItem(str(paper.get("title", "未命名论文")).strip() or "未命名论文", str(paper.get("id", "")))
            index = self.keyword_paper_combo.findData(selected_paper_id)
            self.keyword_paper_combo.setCurrentIndex(index if index >= 0 else 0)
            self._selected_keyword_paper_id = str(self.keyword_paper_combo.currentData() or "")
            self.keyword_paper_combo.currentIndexChanged.connect(self._remember_selected_keyword_paper)
        else:
            self.keyword_paper_combo.addItem("暂无可提取的论文记录", "")
            self.keyword_paper_combo.setEnabled(False)
        extract = QPushButton("提取候选关键词")
        extract.setObjectName("extractKeywordsButton")
        extract.setIcon(lucide_icon("refresh-cw", extract.palette().color(QPalette.ColorRole.ButtonText)))
        extract.clicked.connect(self._extract_keywords)
        extract.setEnabled(bool(self._keyword_papers))
        self.import_pdf_button = QPushButton("导入 PDF 提取关键词")
        self.import_pdf_button.setObjectName("importPdfKeywordsButton")
        self.import_pdf_button.setIcon(
            lucide_icon("upload", self.import_pdf_button.palette().color(QPalette.ColorRole.ButtonText))
        )
        self.import_pdf_button.setToolTip("从本地 PDF 读取文字并把候选词放入待确认区")
        self.import_pdf_button.clicked.connect(self._import_pdf_keywords)
        source_actions = QHBoxLayout()
        source_actions.setSpacing(7)
        source_actions.addWidget(self.keyword_paper_combo, 1)
        source_actions.addWidget(extract)
        source_actions.addWidget(self.import_pdf_button)
        keyword_layout.addLayout(source_actions)
        self.extract_status = QLabel(self._keyword_notice or "提取结果不会直接覆盖锁定词")
        self.extract_status.setObjectName("frontierSettingsHint")
        self.extract_status.setWordWrap(True)
        keyword_layout.addWidget(self.extract_status)
        self.keyword_progress = AiProgressPanel(object_name="researchKeywordProgress")
        keyword_layout.addWidget(self.keyword_progress)

        source_frame, source_layout = self._section(
            "前沿来源与更新策略",
            "来源请求在后台执行；保存设置不会阻塞窗口，也不会在未变化时重复调用 AI。",
        )
        source_grid = QGridLayout()
        source_grid.setContentsMargins(0, 0, 0, 0)
        source_grid.setHorizontalSpacing(9)
        source_grid.setVerticalSpacing(5)
        self.source_crossref = QCheckBox("Crossref")
        self.source_crossref.setObjectName("frontierSourceCrossref")
        self.source_openalex = QCheckBox("OpenAlex")
        self.source_openalex.setObjectName("frontierSourceOpenAlex")
        self.source_doaj = QCheckBox("DOAJ")
        self.source_doaj.setObjectName("frontierSourceDoaj")
        self.source_semantic_scholar = QCheckBox("Semantic Scholar")
        self.source_semantic_scholar.setObjectName("frontierSourceSemanticScholar")
        self.source_europe_pmc = QCheckBox("Europe PMC")
        self.source_europe_pmc.setObjectName("frontierSourceEuropePmc")
        self.source_arxiv = QCheckBox("arXiv")
        self.source_arxiv.setObjectName("frontierSourceArxiv")
        sources = self._profile.get("sources", {}) if isinstance(self._profile.get("sources", {}), dict) else {}
        self.source_crossref.setChecked(bool(sources.get("crossref", {}).get("enabled", True)))
        self.source_openalex.setChecked(bool(sources.get("openalex", {}).get("enabled", True)))
        self.source_doaj.setChecked(bool(sources.get("doaj", {}).get("enabled", True)))
        self.source_semantic_scholar.setChecked(bool(sources.get("semantic_scholar", {}).get("enabled", True)))
        self.source_europe_pmc.setChecked(bool(sources.get("europe_pmc", {}).get("enabled", True)))
        self.source_arxiv.setChecked(bool(sources.get("arxiv", {}).get("enabled", True)))
        for index, checkbox in enumerate(
            (
                self.source_crossref,
                self.source_openalex,
                self.source_doaj,
                self.source_semantic_scholar,
                self.source_europe_pmc,
                self.source_arxiv,
            )
        ):
            source_grid.addWidget(checkbox, index // 2, index % 2)
        source_grid.setColumnStretch(0, 1)
        source_grid.setColumnStretch(1, 1)
        source_layout.addLayout(source_grid)
        update_label = QLabel("自动更新")
        update_label.setObjectName("settingsFieldLabel")
        source_layout.addWidget(update_label)
        update_row = QHBoxLayout()
        self.frontier_update_frequency = QComboBox()
        self.frontier_update_frequency.setObjectName("frontierUpdateFrequency")
        self.frontier_update_frequency.addItem("每天 08:00", "daily")
        self.frontier_update_frequency.addItem("每周一", "weekly")
        self.frontier_update_frequency.addItem("只手动刷新", "manual")
        current_frequency = str(self._profile.get("frontier_update_frequency", "daily"))
        index = self.frontier_update_frequency.findData(current_frequency)
        self.frontier_update_frequency.setCurrentIndex(index if index >= 0 else 0)
        update_row.addWidget(self.frontier_update_frequency, 1)
        source_layout.addLayout(update_row)
        self.frontier_auto_update = QCheckBox("保存后按规则执行")
        self.frontier_auto_update.setObjectName("frontierAutoUpdate")
        self.frontier_auto_update.setChecked(bool(self._profile.get("frontier_auto_update", True)))
        source_layout.addWidget(self.frontier_auto_update)

        self.workbench_tools_layout.addWidget(keyword_frame)
        self.strategy_layout.addWidget(quality_frame)
        self.strategy_layout.addWidget(source_frame)
        self.strategy_layout.addStretch(1)
        self.journal_priority_page = QWidget()
        self.journal_priority_page.setObjectName("journalPriorityPage")
        journal_priority_layout = QVBoxLayout(self.journal_priority_page)
        journal_priority_layout.setContentsMargins(0, 0, 0, 0)
        self.journal_priority_editor = JournalPriorityBatchEditor(load_journal_library())
        journal_priority_layout.addWidget(self.journal_priority_editor)
        self.settings_tabs.addTab(self.journal_priority_page, "期刊排序")
        self._frontier_sections_built = True

    def _add_quality_option_row(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        hint = QLabel("系统不把未知事实当作 0 分；只有已核验 JCR Q3/Q4 会按本次规则移出内容库。")
        hint.setObjectName("frontierSettingsHint")
        hint.setWordWrap(True)
        row.addWidget(hint, 1)
        layout.addLayout(row)

    def _refresh_journal_score_summary(self) -> None:
        journals = load_journal_library()
        scored = sum(
            1
            for journal in journals
            if isinstance(journal, dict)
            and journal.get("frontier_score") is not None
            and journal.get("frontier_score") != ""
        )
        self.journal_score_summary.setText(
            f"内容分 {100 - self.journal_weight_spin.value()}% · 期刊分 {self.journal_weight_spin.value()}%"
            f" · 已单独评分 {scored}/{len(journals)} 本"
        )

    def _open_journal_scores(self) -> None:
        dialog = JournalPreferenceScoreDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_journal_score_summary()
            self.journal_priority_editor.replace_journals(load_journal_library())

    def _add_manual_keyword(self) -> None:
        text = self.manual_keyword_edit.text().strip()
        if not text:
            self.extract_status.setText("请先输入一个关键词。")
            return
        self._profile = add_pending_term(self._profile, text, source="manual", confidence="high", reason="用户手动补充")
        self._keyword_notice = f"“{text}”已放入待确认区，保存后仍不会自动生效。"
        self._render()

    def _remember_selected_keyword_paper(self) -> None:
        self._selected_keyword_paper_id = str(self.keyword_paper_combo.currentData() or "")

    @staticmethod
    def _paper_keyword_candidates(paper: dict[str, Any]) -> list[str]:
        """Extract conservative candidates from a saved paper record locally."""
        candidates: list[str] = []
        seen: set[str] = set()

        def add(value: object) -> None:
            from utils.ai_service import _clean_research_keyword

            text = _clean_research_keyword(value)
            if not text:
                return
            key = canonical_term_key(text)
            if not key or key in seen:
                return
            seen.add(key)
            candidates.append(text)

        raw_keywords = paper.get("keywords", paper.get("keyword", []))
        values = raw_keywords if isinstance(raw_keywords, list) else re.split(r"[,，;；\n]", str(raw_keywords or ""))
        for value in values:
            add(value)
        for source in (str(paper.get("title", "")), str(paper.get("summary", ""))):
            for phrase in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}(?:\s+[A-Za-z][A-Za-z0-9-]{2,}){0,2}", source):
                add(phrase)
            for phrase in re.findall(r"[\u4e00-\u9fff]{2,14}", source):
                add(phrase)
        return candidates[:10]

    def _extract_keywords(self) -> None:
        paper_id = str(getattr(self, "keyword_paper_combo", None).currentData() or "") if hasattr(self, "keyword_paper_combo") else ""
        paper = next((item for item in getattr(self, "_keyword_papers", []) if str(item.get("id", "")) == paper_id), {})
        if not paper:
            self._keyword_notice = "请先在 PAPERS 中建立包含题目、摘要或关键词的论文记录。"
            self._render()
            return
        title = str(paper.get("title", "未命名论文")).strip() or "未命名论文"
        local_candidates = self._paper_keyword_candidates(paper)
        source = {
            "source_type": "paper_record",
            "title": title,
            "keywords": paper.get("keywords", []),
            "summary": str(paper.get("summary", "")).strip(),
            "source_name": title,
        }
        self._start_keyword_ai(
            source,
            display_name=title,
            source_key="ai_paper",
            reason=f"来自论文《{title[:40]}》",
            fallback=local_candidates,
        )

    def _queue_keyword_candidates(self, values: list[Any], *, source: str, reason: str) -> int:
        added = 0
        for value in values:
            if isinstance(value, dict):
                text = str(value.get("canonical_en", value.get("text", ""))).strip()
                translation = str(value.get("translation_zh", "")).strip()
                weight = int(value.get("weight", 50) or 50)
                confidence = str(value.get("confidence", "medium")).strip()
                evidence = value.get("evidence", [])
            else:
                text = str(value).strip()
                translation = ""
                weight = 50
                confidence = "medium"
                evidence = []
            if not text:
                continue
            before = len(self._profile.get("pending_terms", []))
            self._profile = add_pending_term(
                self._profile,
                text,
                translation_zh=translation,
                weight=weight,
                evidence=evidence,
                source=source,
                confidence=confidence,
                reason=reason,
            )
            if len(self._profile.get("pending_terms", [])) > before:
                added += 1
        return added

    def _import_pdf_keywords(self) -> None:
        """Read a user-selected PDF locally and queue conservative keywords."""
        if self._pdf_worker is not None and self._pdf_worker.isRunning():
            self.extract_status.setText("PDF 正在读取，请稍候。")
            return
        path, _filter = QFileDialog.getOpenFileName(self, "导入 PDF 提取关键词", "", "PDF 文件 (*.pdf)")
        if not path:
            return
        self.keyword_progress.begin("正在读取 PDF 文本…")
        self.extract_status.setText("正在读取 PDF 文本，随后交给 AI 提取研究概念…")
        self.import_pdf_button.setEnabled(False)
        self._pdf_worker = PdfKeywordImportThread(path, self)
        self._pdf_worker.progress.connect(self._pdf_keyword_progress)
        self._pdf_worker.completed.connect(self._pdf_keywords_imported)
        self._pdf_worker.failed.connect(self._pdf_keywords_failed)
        self._pdf_worker.finished.connect(self._clear_pdf_worker)
        self._pdf_worker.start()

    def _pdf_keyword_progress(self, message: str, value: int) -> None:
        self.keyword_progress.update(str(message), int(value))
        self.extract_status.setText(str(message))

    def _pdf_keywords_imported(self, extracted: dict[str, Any]) -> None:
        name = str(extracted.get("name", "未命名 PDF")).strip() or "未命名 PDF"
        text = str(extracted.get("text", ""))[:18000]
        pseudo_paper = {"title": "", "summary": text, "keywords": []}
        candidates = self._paper_keyword_candidates(pseudo_paper)
        source = {
            "source_type": "pdf",
            # A filename is metadata only; never present it as the paper title
            # to the model or local fallback extractor.
            "title": "",
            "keywords": [],
            "summary": text,
            "pages": [dict(page) for page in extracted.get("pages", []) if isinstance(page, dict)],
            "source_name": name,
        }
        self._start_keyword_ai(
            source,
            display_name=name,
            source_key="ai_pdf",
            reason=f"来自 PDF《{name[:40]}》",
            fallback=candidates,
        )

    def _pdf_keywords_failed(self, message: str) -> None:
        self._keyword_notice = f"PDF 未能提取关键词：{message}"
        self.keyword_progress.fail(self._keyword_notice)
        self._render()

    def _clear_pdf_worker(self) -> None:
        if self._pdf_worker is not None:
            self._pdf_worker.deleteLater()
        self._pdf_worker = None

    def _start_keyword_ai(
        self,
        source: dict[str, Any],
        *,
        display_name: str,
        source_key: str,
        reason: str,
        fallback: list[str],
    ) -> None:
        if self._keyword_ai_worker is not None and self._keyword_ai_worker.isRunning():
            self.extract_status.setText("AI 正在提取关键词，请稍候。")
            return
        if not is_deepseek_ready("research_profile_update"):
            added = self._queue_keyword_candidates(fallback, source="local_fallback", reason=reason + "（本地兜底）")
            self._keyword_notice = f"AI 未配置，已用本地规则从《{display_name[:28]}》放入 {added} 个干净候选词；可在设置中启用 DeepSeek。"
            self.keyword_progress.complete("本地关键词提取完成（未调用 AI）。")
            self._render()
            return
        self.import_pdf_button.setEnabled(False)
        self.keyword_progress.begin("AI 正在提取研究关键词…")
        self.extract_status.setText("AI 正在分析研究对象、方法、数据与区域；文件名和 DOI 会被过滤。")
        self._keyword_ai_fallback = list(fallback)
        self._keyword_ai_source_key = source_key
        self._keyword_ai_reason = reason
        self._keyword_ai_display_name = display_name
        self._keyword_ai_worker = KeywordExtractionAiThread(source, self)
        self._keyword_ai_worker.progress.connect(self._keyword_ai_progress)
        self._keyword_ai_worker.completed.connect(self._keyword_ai_finished)
        self._keyword_ai_worker.failed.connect(self._keyword_ai_failed)
        self._keyword_ai_worker.finished.connect(self._clear_keyword_ai_worker)
        self._keyword_ai_worker.start()

    def _keyword_ai_progress(self, message: str, value: int) -> None:
        self.keyword_progress.update(str(message), int(value))
        self.extract_status.setText(str(message))

    def _keyword_ai_finished(self, result: dict[str, Any]) -> None:
        values = result.get("terms", []) if isinstance(result, dict) else []
        values = [dict(item) for item in values if isinstance(item, dict)]
        if not values:
            raw_keywords = result.get("keywords", []) if isinstance(result, dict) else []
            values = [str(item).strip() for item in raw_keywords if str(item).strip()]
        if not values:
            values = list(getattr(self, "_keyword_ai_fallback", []))
        added = self._queue_keyword_candidates(
            values,
            source=str(getattr(self, "_keyword_ai_source_key", "ai")),
            reason=str(getattr(self, "_keyword_ai_reason", "来自 AI 关键词提取")),
        )
        model = str(result.get("model", "")).strip() if isinstance(result, dict) else ""
        reason = str(result.get("reason_cn", "")).strip() if isinstance(result, dict) else ""
        self._keyword_notice = f"AI 已从《{getattr(self, '_keyword_ai_display_name', '文献')[:28]}》提取 {added} 个研究关键词，等待确认。"
        if model:
            self._keyword_notice += f" 模型：{model}。"
        if reason:
            self._keyword_notice += " " + reason[:120]
        self.keyword_progress.complete("AI 关键词提取完成，结果已放入待确认区。")
        self._render()

    def _keyword_ai_failed(self, message: str) -> None:
        fallback = list(getattr(self, "_keyword_ai_fallback", []))
        added = self._queue_keyword_candidates(
            fallback,
            source="local_fallback",
            reason=str(getattr(self, "_keyword_ai_reason", "AI 失败后的本地兜底")) + "（本地兜底）",
        )
        self._keyword_notice = f"AI 关键词提取失败，已用本地规则放入 {added} 个候选词：{message}"
        self.keyword_progress.fail(self._keyword_notice)
        self._render()

    def _clear_keyword_ai_worker(self) -> None:
        if self._keyword_ai_worker is not None:
            self._keyword_ai_worker.deleteLater()
        self._keyword_ai_worker = None
        if hasattr(self, "import_pdf_button"):
            self.import_pdf_button.setEnabled(True)

    def profile(self) -> dict[str, Any]:
        result = super().profile()
        result["require_verified_jcr_q1_q2"] = bool(self.require_q1_q2.isChecked())
        result["retain_unread"] = bool(self.retain_unread.isChecked())
        result["show_preprints"] = bool(self.show_preprints.isChecked())
        result["lookback_days"] = 14
        result["frontier_ranking"] = {
            "journal_weight": self.journal_weight_spin.value(),
            "default_journal_score": self.default_journal_score_spin.value(),
        }
        result["frontier_update_frequency"] = str(self.frontier_update_frequency.currentData() or "daily")
        result["frontier_auto_update"] = bool(self.frontier_auto_update.isChecked())
        sources = result.get("sources", {}) if isinstance(result.get("sources", {}), dict) else {}
        for source_id, checkbox in (
            ("crossref", self.source_crossref),
            ("openalex", self.source_openalex),
            ("doaj", self.source_doaj),
            ("semantic_scholar", self.source_semantic_scholar),
            ("europe_pmc", self.source_europe_pmc),
            ("arxiv", self.source_arxiv),
        ):
            current = sources.get(source_id, {}) if isinstance(sources.get(source_id, {}), dict) else {}
            sources[source_id] = {**current, "enabled": bool(checkbox.isChecked())}
        result["sources"] = sources
        return normalize_research_profile_v11(result)

    def accept(self) -> None:
        if hasattr(self, "journal_priority_editor") and self.journal_priority_editor.has_changes():
            try:
                save_journal_library(self.journal_priority_editor.journals_with_priorities())
            except Exception as error:  # noqa: BLE001 - keep the settings workbench open on save failure
                self.journal_priority_editor.summary_label.setText(f"保存期刊排序失败：{error}")
                return
        super().accept()
