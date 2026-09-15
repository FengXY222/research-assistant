"""Bilingual research-profile workbench for the v12 widget application."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from utils.research_profile_service import (
    confirm_pending_term,
    delete_excluded_term,
    lock_term,
    normalize_research_profile_v12,
    reject_pending_term,
    remove_term,
    unlock_term,
    update_term_fields,
    upsert_excluded_term,
)
from ui.icons import lucide_icon
from ui.motion import animate_widget_enter
from utils.local_translation import translate_profile_keywords
from utils.local_translation_backend import model_available as local_translation_available


_SOURCE_LABELS = {
    "manual": "手动补充",
    "user": "手动维护",
    "user_confirmed": "已确认",
    "user_locked": "手动锁定",
    "user_unlocked": "手动维护",
    "ai": "AI 整理",
    "ai_pending": "AI 建议",
    "ai_pdf": "PDF · AI",
    "ai_paper": "论文 · AI",
    "local_fallback": "本地提取",
    "legacy_primary": "旧版核心词",
    "legacy_secondary": "旧版扩展词",
    "legacy_excluded": "旧版排除词",
    "legacy_negative": "旧版负反馈",
}


class ProfileTranslationThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, profile: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profile = deepcopy(profile)

    def run(self) -> None:
        try:
            self.completed.emit(translate_profile_keywords(self._profile))
        except Exception as error:  # noqa: BLE001 - original profile remains untouched
            self.failed.emit(str(error))


class ResearchProfileDialog(QDialog):
    """Review active, pending and excluded concepts without losing context."""

    def __init__(self, profile: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profile = normalize_research_profile_v12(deepcopy(profile if isinstance(profile, dict) else {}))
        self.setWindowTitle("研究画像 · 科研助手")
        self.setModal(True)
        self.setFixedSize(1024, 768)
        self._entrance_animated = False
        self._translation_worker: ProfileTranslationThread | None = None
        self._build_ui()
        self._render()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(12)
        heading = QVBoxLayout()
        heading.setSpacing(2)
        title = QLabel("研究画像")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("锁定词长期有效；普通词由你和 AI 共同维护，所有新概念先经过待确认区。")
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header.addLayout(heading, 1)
        self.freshness = QLabel()
        self.freshness.setObjectName("profileFreshness")
        self.freshness.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.freshness)
        root.addLayout(header)

        self.settings_tabs = QTabWidget()
        self.settings_tabs.setObjectName("researchSettingsTabs")
        self.settings_tabs.setDocumentMode(True)
        self.settings_tabs.tabBar().setObjectName("researchSettingsTabBar")
        self.settings_tabs.tabBar().setMinimumHeight(42)
        self.settings_tabs.tabBar().setExpanding(False)
        root.addWidget(self.settings_tabs, 1)

        self.keyword_page = QWidget()
        self.keyword_page.setObjectName("profileKeywordPage")
        keyword_page_layout = QVBoxLayout(self.keyword_page)
        keyword_page_layout.setContentsMargins(0, 10, 0, 0)
        keyword_page_layout.setSpacing(9)

        self.workbench_tools_host = QWidget()
        self.workbench_tools_host.setObjectName("profileWorkbenchTools")
        self.workbench_tools_layout = QVBoxLayout(self.workbench_tools_host)
        self.workbench_tools_layout.setContentsMargins(0, 0, 0, 0)
        self.workbench_tools_layout.setSpacing(8)
        keyword_page_layout.addWidget(self.workbench_tools_host)

        self.region_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.region_splitter.setObjectName("profileRegionSplitter")
        self.region_splitter.setChildrenCollapsible(False)
        self.region_splitter.setHandleWidth(6)
        keyword_page_layout.addWidget(self.region_splitter, 1)

        self.active_region, self.active_list_layout, self.active_count = self._make_region(
            "activeTermsRegion",
            "生效关键词",
            "按权重排序，锁定词不会被自动修改",
        )
        self.pending_region, self.pending_list_layout, self.pending_count = self._make_region(
            "pendingTermsRegion",
            "待确认",
            "采纳后才参与文献与特刊匹配",
        )
        self.excluded_region, self.excluded_list_layout, self.excluded_count = self._make_region(
            "excludedTermsRegion",
            "排除关键词",
            "可编辑、锁定或逐项删除",
            with_exclusion_form=True,
        )
        for region in (self.active_region, self.pending_region, self.excluded_region):
            self.region_splitter.addWidget(region)
        self.region_splitter.setStretchFactor(0, 2)
        self.region_splitter.setStretchFactor(1, 1)
        self.region_splitter.setStretchFactor(2, 1)
        self.region_splitter.setSizes([454, 245, 245])
        self.settings_tabs.addTab(self.keyword_page, "关键词工作台")

        self.strategy_page = QWidget()
        self.strategy_page.setObjectName("profileStrategyPage")
        strategy_page_layout = QVBoxLayout(self.strategy_page)
        strategy_page_layout.setContentsMargins(0, 10, 0, 0)
        strategy_scroll = QScrollArea()
        strategy_scroll.setObjectName("profileStrategyScroll")
        strategy_scroll.setWidgetResizable(True)
        strategy_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.strategy_content = QWidget()
        self.strategy_content.setObjectName("profileStrategyContent")
        self.strategy_layout = QVBoxLayout(self.strategy_content)
        self.strategy_layout.setContentsMargins(1, 1, 5, 1)
        self.strategy_layout.setSpacing(9)
        strategy_scroll.setWidget(self.strategy_content)
        strategy_page_layout.addWidget(strategy_scroll)
        self.settings_tabs.addTab(self.strategy_page, "发现策略")

        profile_rule = QFrame()
        profile_rule.setObjectName("profileStrategySection")
        rule_layout = QVBoxLayout(profile_rule)
        rule_layout.setContentsMargins(13, 11, 13, 11)
        rule_layout.setSpacing(6)
        rule_title = QLabel("画像参与规则")
        rule_title.setObjectName("settingsSectionTitle")
        rule_layout.addWidget(rule_title)
        self.filter_q34 = QCheckBox("默认过滤已核验的 Q3/Q4 期刊；未知分区仍会展示")
        self.filter_q34.setChecked(bool(self._profile.get("filter_known_q3_q4", True)))
        rule_layout.addWidget(self.filter_q34)
        self.strategy_layout.addWidget(profile_rule)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self._set_button_icon(buttons.button(QDialogButtonBox.StandardButton.Save), "check")
        self._set_button_icon(buttons.button(QDialogButtonBox.StandardButton.Cancel), "x")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _set_button_icon(self, button: QPushButton, name: str) -> None:
        button.setIcon(lucide_icon(name, self.palette().color(QPalette.ColorRole.ButtonText)))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._entrance_animated:
            return
        self._entrance_animated = True
        QTimer.singleShot(0, lambda: animate_widget_enter(self.settings_tabs, distance=3, duration_ms=160))
        app = QApplication.instance()
        if app is not None and bool(app.property("research_assistant_runtime_ready")):
            QTimer.singleShot(20, self._start_missing_translation)

    def _start_missing_translation(self) -> None:
        if self._translation_worker is not None or not local_translation_available():
            return
        missing = any(
            str(value.get("canonical_en", value.get("text", ""))).strip()
            and not str(value.get("translation_zh", "")).strip()
            for field in ("terms", "pending_terms", "excluded_entries")
            for value in (self._profile.get(field, []) if isinstance(self._profile.get(field), list) else [])
            if isinstance(value, dict)
        )
        if not missing:
            return
        self.freshness.setToolTip("内置 OPUS-MT 正在离线补全缺失的中文翻译")
        worker = ProfileTranslationThread(self._profile, self)
        worker.completed.connect(self._translations_ready)
        worker.finished.connect(self._release_translation_worker)
        self._translation_worker = worker
        worker.start()

    def _translations_ready(self, translated: dict[str, Any]) -> None:
        current = normalize_research_profile_v12(self._profile)
        for field in ("terms", "pending_terms", "excluded_entries"):
            translated_by_id = {
                str(value.get("id", "")): str(value.get("translation_zh", "")).strip()
                for value in translated.get(field, []) if isinstance(value, dict)
            }
            for value in current.get(field, []):
                if isinstance(value, dict) and not str(value.get("translation_zh", "")).strip():
                    value["translation_zh"] = translated_by_id.get(str(value.get("id", "")), "")
        self._profile = normalize_research_profile_v12(current)
        self._render()

    def _release_translation_worker(self) -> None:
        worker = self._translation_worker
        self._translation_worker = None
        if worker is not None:
            worker.deleteLater()

    def _make_region(
        self,
        object_name: str,
        title: str,
        hint: str,
        *,
        with_exclusion_form: bool = False,
    ) -> tuple[QFrame, QVBoxLayout, QLabel]:
        region = QFrame()
        region.setObjectName(object_name)
        region.setProperty("profileRegion", True)
        region.setMinimumWidth(210)
        layout = QVBoxLayout(region)
        layout.setContentsMargins(10, 10, 10, 9)
        layout.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(6)
        name = QLabel(title)
        name.setObjectName("profileRegionHeader")
        count = QLabel("0")
        count.setObjectName("profileRegionCount")
        count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(name)
        header.addStretch(1)
        header.addWidget(count)
        layout.addLayout(header)
        description = QLabel(hint)
        description.setObjectName("profileRegionHint")
        description.setWordWrap(True)
        layout.addWidget(description)

        if with_exclusion_form:
            self.excluded_add_en = QLineEdit()
            self.excluded_add_en.setObjectName("excludedKeywordEdit")
            self.excluded_add_en.setPlaceholderText("英文排除词")
            self.excluded_add_zh = QLineEdit()
            self.excluded_add_zh.setObjectName("excludedTranslationEdit")
            self.excluded_add_zh.setPlaceholderText("中文翻译（可选）")
            add_row = QHBoxLayout()
            add_row.setSpacing(5)
            add_row.addWidget(self.excluded_add_en, 1)
            add = QPushButton("添加")
            add.setObjectName("rowButton")
            self._set_button_icon(add, "check")
            add.clicked.connect(self._add_exclusion)
            add_row.addWidget(add)
            layout.addLayout(add_row)
            layout.addWidget(self.excluded_add_zh)

        scroll = QScrollArea()
        scroll.setObjectName(object_name + "Scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName(object_name + "Content")
        list_layout = QVBoxLayout(content)
        list_layout.setContentsMargins(0, 1, 3, 1)
        list_layout.setSpacing(7)
        list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return region, list_layout, count

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
                continue
            child_layout = item.layout()
            if child_layout is not None:
                ResearchProfileDialog._clear_layout(child_layout)  # type: ignore[arg-type]

    @staticmethod
    def _source_text(term: dict[str, Any]) -> str:
        sources = term.get("sources", [])
        source = str(sources[0] if isinstance(sources, list) and sources else term.get("source", "user"))
        return _SOURCE_LABELS.get(source, source.replace("_", " ") or "来源未知")

    @staticmethod
    def _english_label(term: dict[str, Any]) -> QLabel:
        label = QLabel(str(term.get("canonical_en", term.get("text", "未命名关键词"))))
        label.setObjectName("profileTermName")
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return label

    def _translation_editor(self, term: dict[str, Any], placeholder: str = "中文翻译") -> QLineEdit:
        editor = QLineEdit(str(term.get("translation_zh", "")))
        editor.setObjectName("profileTranslationEdit")
        editor.setPlaceholderText(placeholder)
        editor.setClearButtonEnabled(True)
        editor.setMinimumWidth(0)
        return editor

    def _active_row(self, term: dict[str, Any]) -> QFrame:
        row = QFrame()
        row.setObjectName("profileActiveRow")
        row.setProperty("locked", bool(term.get("locked", False)))
        row.setMinimumHeight(116)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(9, 8, 9, 8)
        layout.setSpacing(5)

        header = QHBoxLayout()
        header.setSpacing(6)
        header.addWidget(self._english_label(term), 1)
        state = QLabel("已锁定" if bool(term.get("locked", False)) else "生效")
        state.setObjectName("profileLocked" if bool(term.get("locked", False)) else "profileActiveState")
        header.addWidget(state)
        layout.addLayout(header)

        translation = self._translation_editor(term)
        term_id = str(term.get("id", ""))
        translation.editingFinished.connect(
            lambda term_id=term_id, editor=translation: self._edit_active_translation(term_id, editor.text())
        )
        layout.addWidget(translation)

        meta = QHBoxLayout()
        meta.setSpacing(5)
        weight_label = QLabel("权重")
        weight_label.setObjectName("profileFieldLabel")
        meta.addWidget(weight_label)
        weight = QSpinBox()
        weight.setObjectName("profileWeightEdit")
        weight.setRange(1, 100)
        weight.setValue(int(term.get("weight", 50)))
        weight.setSuffix("%")
        weight.setEnabled(not bool(term.get("locked", False)))
        weight.editingFinished.connect(
            lambda term_id=term_id, editor=weight: self._edit_active_weight(term_id, editor.value())
        )
        meta.addWidget(weight)
        source = QLabel(self._source_text(term))
        source.setObjectName("profileSource")
        source.setToolTip("；".join(str(value) for value in term.get("evidence", []) if str(value).strip()))
        meta.addWidget(source, 1)
        layout.addLayout(meta)

        actions = QHBoxLayout()
        actions.setSpacing(5)
        actions.addStretch(1)
        locked = bool(term.get("locked", False))
        lock = QPushButton("解锁" if locked else "锁定")
        lock.setObjectName("rowButton")
        self._set_button_icon(lock, "unlock" if locked else "lock")
        lock.clicked.connect(lambda _checked=False, term_id=term_id: self._toggle_lock(term_id))
        actions.addWidget(lock)
        exclude = QPushButton("排除")
        exclude.setObjectName("rowButton")
        self._set_button_icon(exclude, "x")
        exclude.setEnabled(not locked)
        exclude.setToolTip("锁定词需先解锁" if locked else "移入排除关键词")
        exclude.clicked.connect(lambda _checked=False, term_id=term_id: self._exclude_term(term_id))
        actions.addWidget(exclude)
        remove = QPushButton("移除")
        remove.setObjectName("dangerButton")
        self._set_button_icon(remove, "trash-2")
        remove.setEnabled(not locked)
        remove.setToolTip("锁定词需先解锁" if locked else "永久移除并阻止再次自动出现")
        remove.clicked.connect(lambda _checked=False, term_id=term_id: self._remove_term(term_id))
        actions.addWidget(remove)
        layout.addLayout(actions)
        return row

    def _pending_row(self, term: dict[str, Any]) -> QFrame:
        row = QFrame()
        row.setObjectName("profilePendingRow")
        row.setMinimumHeight(122)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        layout.addWidget(self._english_label(term))
        translation = self._translation_editor(term)
        term_id = str(term.get("id", ""))
        translation.editingFinished.connect(
            lambda term_id=term_id, editor=translation: self._edit_pending_translation(term_id, editor.text())
        )
        layout.addWidget(translation)
        meta = QLabel(f"建议权重 {int(term.get('weight', 50))}% · {self._source_text(term)}")
        meta.setObjectName("profileSource")
        meta.setWordWrap(True)
        layout.addWidget(meta)
        actions = QHBoxLayout()
        actions.setSpacing(5)
        accept = QPushButton("采纳")
        accept.setObjectName("rowButton")
        self._set_button_icon(accept, "check")
        accept.clicked.connect(lambda _checked=False, term_id=term_id: self._accept_pending(term_id))
        reject = QPushButton("拒绝")
        reject.setObjectName("dangerButton")
        self._set_button_icon(reject, "x")
        reject.setToolTip("拒绝后永久阻止该词再次自动出现")
        reject.clicked.connect(lambda _checked=False, term_id=term_id: self._reject_pending(term_id))
        actions.addWidget(accept)
        actions.addWidget(reject)
        actions.addStretch(1)
        layout.addLayout(actions)
        return row

    def _excluded_row(self, term: dict[str, Any]) -> QFrame:
        row = QFrame()
        row.setObjectName("profileExcludedRow")
        row.setProperty("locked", bool(term.get("locked", False)))
        row.setMinimumHeight(130)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        term_id = str(term.get("id", ""))
        locked = bool(term.get("locked", False))
        english = QLineEdit(str(term.get("canonical_en", term.get("text", ""))))
        english.setObjectName("profileExcludedEnglishEdit")
        english.setPlaceholderText("英文排除词")
        english.setEnabled(not locked)
        translation = self._translation_editor(term)
        translation.setEnabled(not locked)
        english.editingFinished.connect(
            lambda term_id=term_id, editor=english: self._edit_excluded(term_id, canonical_en=editor.text())
        )
        translation.editingFinished.connect(
            lambda term_id=term_id, editor=translation: self._edit_excluded(term_id, translation_zh=editor.text())
        )
        layout.addWidget(english)
        layout.addWidget(translation)
        meta = QLabel(self._source_text(term))
        meta.setObjectName("profileSource")
        layout.addWidget(meta)
        actions = QHBoxLayout()
        actions.setSpacing(5)
        lock = QPushButton("解锁" if locked else "锁定")
        lock.setObjectName("rowButton")
        self._set_button_icon(lock, "unlock" if locked else "lock")
        lock.clicked.connect(lambda _checked=False, term_id=term_id: self._toggle_excluded_lock(term_id))
        remove = QPushButton("删除")
        remove.setObjectName("dangerButton")
        self._set_button_icon(remove, "trash-2")
        remove.setEnabled(not locked)
        remove.setToolTip("锁定排除词需先解锁" if locked else "删除此排除词")
        remove.clicked.connect(lambda _checked=False, term_id=term_id: self._delete_excluded(term_id))
        actions.addWidget(lock)
        actions.addWidget(remove)
        actions.addStretch(1)
        layout.addLayout(actions)
        return row

    @staticmethod
    def _empty_row(text: str) -> QLabel:
        empty = QLabel(text)
        empty.setObjectName("profileRegionEmpty")
        empty.setWordWrap(True)
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setMinimumHeight(76)
        return empty

    def _render(self) -> None:
        active = sorted(
            [dict(item) for item in self._profile.get("terms", []) if isinstance(item, dict)],
            key=lambda item: (-int(item.get("weight", 1)), str(item.get("canonical_en", "")).casefold()),
        )
        pending = [dict(item) for item in self._profile.get("pending_terms", []) if isinstance(item, dict)]
        excluded = [dict(item) for item in self._profile.get("excluded_entries", []) if isinstance(item, dict)]
        self.active_count.setText(str(len(active)))
        self.pending_count.setText(str(len(pending)))
        self.excluded_count.setText(str(len(excluded)))
        last_update = str(self._profile.get("ai_profile_updated_at", "")).strip()
        self.freshness.setText(
            f"{len(active)} 个生效 · {len(pending)} 个待确认\n最近整理：{last_update or '尚未整理'}"
        )

        for layout in (self.active_list_layout, self.pending_list_layout, self.excluded_list_layout):
            self._clear_layout(layout)
        if active:
            for term in active:
                self.active_list_layout.addWidget(self._active_row(term))
        else:
            self.active_list_layout.addWidget(self._empty_row("尚无生效关键词"))
        if pending:
            for term in pending:
                self.pending_list_layout.addWidget(self._pending_row(term))
        else:
            self.pending_list_layout.addWidget(self._empty_row("没有等待你确认的新词"))
        if excluded:
            for term in excluded:
                self.excluded_list_layout.addWidget(self._excluded_row(term))
        else:
            self.excluded_list_layout.addWidget(self._empty_row("尚未设置排除关键词"))

    def _edit_active_translation(self, term_id: str, translation: str) -> None:
        self._profile = update_term_fields(self._profile, term_id, translation_zh=translation)

    def _edit_active_weight(self, term_id: str, weight: int) -> None:
        self._profile = update_term_fields(self._profile, term_id, weight=weight)

    def _edit_pending_translation(self, term_id: str, translation: str) -> None:
        result = normalize_research_profile_v12(self._profile)
        for term in result.get("pending_terms", []):
            if str(term.get("id", "")) == term_id:
                term["translation_zh"] = str(translation).strip()[:180]
                break
        self._profile = normalize_research_profile_v12(result)

    def _edit_excluded(
        self,
        term_id: str,
        *,
        canonical_en: str | None = None,
        translation_zh: str | None = None,
    ) -> None:
        result = normalize_research_profile_v12(self._profile)
        for term in result.get("excluded_entries", []):
            if str(term.get("id", "")) != term_id or bool(term.get("locked", False)):
                continue
            if canonical_en is not None and canonical_en.strip():
                term["canonical_en"] = canonical_en.strip()[:180]
                term["text"] = term["canonical_en"]
            if translation_zh is not None:
                term["translation_zh"] = translation_zh.strip()[:180]
            break
        self._profile = normalize_research_profile_v12(result)

    def _toggle_lock(self, term_id: str) -> None:
        term = next((item for item in self._profile.get("terms", []) if str(item.get("id", "")) == term_id), {})
        self._profile = unlock_term(self._profile, term_id) if bool(term.get("locked", False)) else lock_term(self._profile, term_id)
        self._render()

    def _toggle_excluded_lock(self, term_id: str) -> None:
        result = normalize_research_profile_v12(self._profile)
        for term in result.get("excluded_entries", []):
            if str(term.get("id", "")) == term_id:
                term["locked"] = not bool(term.get("locked", False))
                term["weight"] = 100 if term["locked"] else 70
                break
        self._profile = normalize_research_profile_v12(result)
        self._render()

    def _remove_term(self, term_id: str) -> None:
        self._profile = remove_term(self._profile, term_id)
        self._render()

    def _exclude_term(self, term_id: str) -> None:
        term = next((item for item in self._profile.get("terms", []) if str(item.get("id", "")) == term_id), None)
        if term is None or bool(term.get("locked", False)):
            return
        self._profile = upsert_excluded_term(
            self._profile,
            canonical_en=str(term.get("canonical_en", "")),
            translation_zh=str(term.get("translation_zh", "")),
            source="user",
        )
        self._render()

    def _add_exclusion(self) -> None:
        canonical_en = self.excluded_add_en.text().strip()
        if not canonical_en:
            return
        self._profile = upsert_excluded_term(
            self._profile,
            canonical_en=canonical_en,
            translation_zh=self.excluded_add_zh.text().strip(),
            source="user",
        )
        self.excluded_add_en.clear()
        self.excluded_add_zh.clear()
        self._render()

    def _delete_excluded(self, term_id: str) -> None:
        self._profile = delete_excluded_term(self._profile, term_id)
        self._render()

    def _accept_pending(self, term_id: str) -> None:
        self._profile = confirm_pending_term(self._profile, term_id)
        self._render()

    def _reject_pending(self, term_id: str) -> None:
        self._profile = reject_pending_term(self._profile, term_id)
        self._render()

    def profile(self) -> dict[str, Any]:
        result = normalize_research_profile_v12(self._profile)
        result["filter_known_q3_q4"] = self.filter_q34.isChecked()
        return result
