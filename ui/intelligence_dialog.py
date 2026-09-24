"""Configuration dialog for optional DeepSeek and Clarivate integrations."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.theme import apply_dialog_theme
from utils.easyscholar_service import easyscholar_readiness
from utils.secure_store import SecretStoreError, protect_secret
from utils.source_registry import SOURCE_REGISTRY, masked_source_summary, normalize_source_settings, update_source_secret


class IntelligenceSettingsDialog(QDialog):
    """Keep remote optional integrations explicit, scoped, and locally protected."""

    def __init__(
        self,
        ai: dict | None,
        jcr: dict | None,
        easyscholar: dict | None = None,
        parent: QWidget | None = None,
        *,
        data_sources: dict | None = None,
        embedded: bool = False,
    ) -> None:
        super().__init__(parent)
        self._embedded = bool(embedded)
        if self._embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self._ai = deepcopy(ai if isinstance(ai, dict) else {})
        self._jcr = deepcopy(jcr if isinstance(jcr, dict) else {})
        self._easyscholar = deepcopy(easyscholar if isinstance(easyscholar, dict) else {})
        self._easyscholar_readiness = easyscholar_readiness(self._easyscholar)
        self._data_sources = normalize_source_settings(data_sources)
        self._source_controls: dict[str, dict[str, object]] = {}
        if not self._embedded:
            parent_width = parent.width() if parent else 520
            parent_height = parent.height() if parent else 700
            self.setWindowTitle("增强服务")
            self.setMinimumWidth(360)
            self.setMaximumWidth(max(360, min(540, parent_width - 18)))
            self.resize(max(360, min(500, parent_width - 18)), max(520, min(680, parent_height - 16)))
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0 if self._embedded else 20, 0 if self._embedded else 18, 0 if self._embedded else 20, 0 if self._embedded else 18)
        root.setSpacing(10)
        if not self._embedded:
            title = QLabel("增强服务")
            title.setObjectName("intelligenceTitle")
            root.addWidget(title)
            hint = QLabel(
                "所有密钥只保存在当前 Windows 用户账户中。DeepSeek 用于文字理解与推荐；期刊指标可由 EasyScholar 更新，Clarivate 仍可作为独立核验来源。"
            )
            hint.setObjectName("intelligenceHint")
            hint.setWordWrap(True)
            hint.setMinimumWidth(0)
            hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("intelligenceContent")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content_box = QVBoxLayout(content)
        content_box.setContentsMargins(0, 0, 6, 0)
        content_box.setSpacing(9)

        def card(title_text: str, hint_text: str) -> QVBoxLayout:
            frame = QFrame()
            frame.setObjectName("intelligenceCard")
            box = QVBoxLayout(frame)
            box.setContentsMargins(13, 11, 13, 12)
            box.setSpacing(7)
            title_label = QLabel(title_text)
            title_label.setObjectName("intelligenceCardTitle")
            box.addWidget(title_label)
            detail = QLabel(hint_text)
            detail.setObjectName("intelligenceHint")
            detail.setWordWrap(True)
            detail.setMinimumWidth(0)
            detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            box.addWidget(detail)
            content_box.addWidget(frame)
            return box

        def form_layout() -> QFormLayout:
            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setVerticalSpacing(8)
            return form

        def checkbox_row(text: str) -> tuple[QCheckBox, QWidget]:
            checkbox = QCheckBox()
            checkbox.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
            label = QLabel(text)
            label.setObjectName("intelligenceCheckText")
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            row = QWidget()
            row.setObjectName("intelligenceCheckRow")
            row.setMinimumWidth(0)
            row.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(7)
            layout.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(label, 1, Qt.AlignmentFlag.AlignTop)
            return checkbox, row

        deepseek = card(
            "DeepSeek 智能增强",
            "用于补充期刊研究范围、复核论文选刊与每日前沿。默认模型为 deepseek-v4-flash；可改为 deepseek-v4-pro。",
        )
        deepseek_form = form_layout()
        self.ai_enabled, ai_enabled_row = checkbox_row("启用 DeepSeek API")
        self.ai_enabled.setChecked(bool(self._ai.get("enabled", False)))
        deepseek_form.addRow("服务状态", ai_enabled_row)
        self.ai_base_url = QLineEdit(str(self._ai.get("base_url", "https://api.deepseek.com")))
        self.ai_base_url.setPlaceholderText("https://api.deepseek.com")
        deepseek_form.addRow("API 地址", self.ai_base_url)
        self.ai_model = QComboBox()
        self.ai_model.setEditable(True)
        self.ai_model.addItems(["deepseek-v4-flash", "deepseek-v4-pro"])
        stored_model = str(self._ai.get("model", "deepseek-v4-flash"))
        self.ai_model.setCurrentText(stored_model)
        deepseek_form.addRow("模型", self.ai_model)
        self.ai_key = QLineEdit()
        self.ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key.setPlaceholderText("已保存（留空则保留）" if self._ai.get("api_key_secret") else "填写 DeepSeek API Key")
        deepseek_form.addRow("API Key", self.ai_key)
        self.clear_ai_key, clear_ai_key_row = checkbox_row("清除已保存的 DeepSeek Key")
        deepseek_form.addRow("密钥", clear_ai_key_row)
        deepseek.addLayout(deepseek_form)
        self.ai_journal_enrichment, ai_journal_enrichment_row = checkbox_row("允许 AI 补充期刊方向与选刊提示")
        self.ai_journal_enrichment.setChecked(bool(self._ai.get("journal_enrichment", True)))
        self.ai_journal_recommendation, ai_journal_recommendation_row = checkbox_row("允许 AI 复核“为论文选刊”排序")
        self.ai_journal_recommendation.setChecked(bool(self._ai.get("journal_recommendation", True)))
        self.ai_frontier_rerank, ai_frontier_rerank_row = checkbox_row("允许 AI 复核每日前沿候选")
        self.ai_frontier_rerank.setChecked(bool(self._ai.get("frontier_rerank", True)))
        self.ai_frontier_auto, ai_frontier_auto_row = checkbox_row("每日前沿本地更新后自动调用 AI 复核（会产生 API 费用）")
        self.ai_frontier_auto.setChecked(bool(self._ai.get("auto_frontier_rerank", False)))
        self.ai_profile_update, ai_profile_update_row = checkbox_row("允许 AI 根据成果与前沿反馈校准研究关键词和检索策略")
        self.ai_profile_update.setChecked(bool(self._ai.get("research_profile_update", True)))
        self.ai_profile_auto_achievements, ai_profile_auto_achievements_row = checkbox_row("每天根据成果自动更新研究画像（会产生 API 费用）")
        self.ai_profile_auto_achievements.setChecked(bool(self._ai.get("auto_profile_from_achievements", True)))
        self.ai_profile_read_pdfs, ai_profile_read_pdfs_row = checkbox_row("研究画像更新时全文阅读成果关联 PDF（会发送提取出的文字，扫描版需先 OCR；会增加 API 费用）")
        self.ai_profile_read_pdfs.setChecked(bool(self._ai.get("profile_read_achievement_pdfs", True)))
        self.ai_profile_auto_frontier, ai_profile_auto_frontier_row = checkbox_row("每天根据每日前沿点击偏好更新研究画像（会产生 API 费用）")
        self.ai_profile_auto_frontier.setChecked(bool(self._ai.get("auto_profile_from_frontier", True)))
        self.ai_journal_auto, ai_journal_auto_row = checkbox_row("每天自动补全新加入期刊的方向与选刊信息（会产生 API 费用）")
        self.ai_journal_auto.setChecked(bool(self._ai.get("journal_auto_enrichment", True)))
        self.ai_paper_fill, ai_paper_fill_row = checkbox_row("允许 AI 为论文记录生成关键词与研究摘要草稿")
        self.ai_paper_fill.setChecked(bool(self._ai.get("paper_record_fill", True)))
        self.ai_quick_capture, ai_quick_capture_row = checkbox_row("允许 AI 识别自然语言快速录入")
        self.ai_quick_capture.setChecked(bool(self._ai.get("quick_capture", True)))
        self.ai_local_materials, ai_local_materials_row = checkbox_row("允许 AI 按当前任务检索本地笔记、论文 PDF、投稿记录与审稿意见")
        self.ai_local_materials.setChecked(bool(self._ai.get("local_material_access", True)))
        self.ai_project_isolation, ai_project_isolation_row = checkbox_row("严格按当前项目 / 论文隔离本地资料")
        self.ai_project_isolation.setChecked(bool(self._ai.get("local_material_project_isolation", True)))
        self.ai_material_manifest, ai_material_manifest_row = checkbox_row("保存 AI 资料使用清单（仅类别、记录 ID 与哈希）")
        self.ai_material_manifest.setChecked(bool(self._ai.get("local_material_use_manifest", True)))
        material_log_button = QPushButton("查看最近资料使用清单")
        material_log_button.setObjectName("subtleButton")
        material_log_button.clicked.connect(self._show_material_manifest)
        for row in (
            ai_journal_enrichment_row,
            ai_journal_recommendation_row,
            ai_frontier_rerank_row,
            ai_frontier_auto_row,
            ai_profile_update_row,
            ai_profile_auto_achievements_row,
            ai_profile_read_pdfs_row,
            ai_profile_auto_frontier_row,
            ai_journal_auto_row,
            ai_paper_fill_row,
            ai_quick_capture_row,
            ai_local_materials_row,
            ai_project_isolation_row,
            ai_material_manifest_row,
        ):
            deepseek.addWidget(row)
        deepseek.addWidget(material_log_button)

        jcr = card(
            "Clarivate JCR 核验",
            "Clarivate Journals API 为付费授权服务。请从已订阅的 Developer Portal / Swagger 复制请求地址；地址可包含 {issn} 或 {name}。",
        )
        jcr_form = form_layout()
        self.jcr_enabled, jcr_enabled_row = checkbox_row("启用 Clarivate Journals API")
        self.jcr_enabled.setChecked(bool(self._jcr.get("enabled", False)))
        jcr_form.addRow("服务状态", jcr_enabled_row)
        self.jcr_endpoint = QLineEdit(str(self._jcr.get("endpoint_template", "")))
        self.jcr_endpoint.setPlaceholderText("例如：已授权的 endpoint?issn={issn}")
        jcr_form.addRow("请求地址", self.jcr_endpoint)
        self.jcr_key = QLineEdit()
        self.jcr_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.jcr_key.setPlaceholderText("已保存（留空则保留）" if self._jcr.get("api_key_secret") else "填写 Clarivate API Key")
        jcr_form.addRow("API Key", self.jcr_key)
        self.clear_jcr_key, clear_jcr_key_row = checkbox_row("清除已保存的 Clarivate Key")
        jcr_form.addRow("密钥", clear_jcr_key_row)
        jcr.addLayout(jcr_form)
        jcr_note = QLabel("未配置授权时，期刊会显示“JCR 待核”，不会由 AI 或公开数据库伪造分区。")
        jcr_note.setObjectName("intelligenceHint")
        jcr_note.setWordWrap(True)
        jcr.addWidget(jcr_note)

        easyscholar = card(
            "EasyScholar 期刊指标",
            "按期刊名 / ISSN 更新 JCR 分区、中科院分区、影响因子和收录情况。它是可选数据源；本地手动记录与 Clarivate 核验不会被覆盖。",
        )
        easyscholar_form = form_layout()
        self.easyscholar_enabled, easyscholar_enabled_row = checkbox_row("启用 EasyScholar Open API")
        self.easyscholar_enabled.setChecked(bool(self._easyscholar.get("enabled", False)))
        easyscholar_form.addRow("服务状态", easyscholar_enabled_row)
        self.easyscholar_key = QLineEdit()
        self.easyscholar_key.setEchoMode(QLineEdit.EchoMode.Password)
        if self._easyscholar_readiness.get("needs_reentry") and self._easyscholar.get("secret_key_secret"):
            self.easyscholar_key.setPlaceholderText("当前密钥无法读取，请重新填写")
        else:
            self.easyscholar_key.setPlaceholderText(
                "已保存（留空则保留）" if self._easyscholar.get("secret_key_secret") else "填写 EasyScholar 密钥"
            )
        easyscholar_form.addRow("密钥", self.easyscholar_key)
        self.clear_easyscholar_key, clear_easyscholar_key_row = checkbox_row("清除已保存的 EasyScholar 密钥")
        easyscholar_form.addRow("密钥管理", clear_easyscholar_key_row)
        self.easyscholar_cache_days = QSpinBox()
        self.easyscholar_cache_days.setRange(1, 365)
        self.easyscholar_cache_days.setSuffix(" 天")
        self.easyscholar_cache_days.setValue(int(self._easyscholar.get("cache_days", 30) or 30))
        self.easyscholar_cache_days.setToolTip("同一版本期刊资料在此期限内不重复请求，信息变更时会立即重新检查。")
        easyscholar_form.addRow("缓存期限", self.easyscholar_cache_days)
        easyscholar.addLayout(easyscholar_form)
        last_checked = str(self._easyscholar.get("last_auto_checked", "")).strip()
        easy_note = QLabel(
            str(self._easyscholar_readiness.get("message", ""))
            if self._easyscholar_readiness.get("needs_reentry")
            else f"最近自动检查：{last_checked}" if last_checked
            else "未设置自动检查；可在期刊库“工具”中手动更新。"
        )
        easy_note.setObjectName("intelligenceHint")
        easy_note.setWordWrap(True)
        easyscholar.addWidget(easy_note)

        sources = card(
            "论文与特刊来源接口",
            "六个每日前沿核心来源、四大出版社和第三方特刊来源均在这里开关。可选 Key 只保存加密值；留空会保留原 Key。",
        )
        group_names = {
            "frontier_core": "每日前沿核心",
            "frontier_fallback": "每日前沿回补",
            "frontier_optional": "每日前沿可选",
            "special_official": "特刊官网",
            "special_third_party": "特刊第三方",
        }
        last_group = ""
        for source_id, spec in SOURCE_REGISTRY.items():
            group = str(spec.get("group", ""))
            if group != last_group:
                group_label = QLabel(group_names.get(group, group))
                group_label.setObjectName("intelligenceCardTitle")
                sources.addWidget(group_label)
                last_group = group
            current = self._data_sources[source_id]
            row = QFrame()
            row.setObjectName("intelligenceSourceRow")
            row_box = QVBoxLayout(row)
            row_box.setContentsMargins(8, 6, 8, 6)
            row_box.setSpacing(5)
            enabled = QCheckBox(str(spec.get("name", source_id)))
            enabled.setChecked(bool(current.get("enabled", True)))
            enabled.setToolTip(masked_source_summary(self._data_sources, source_id))
            row_box.addWidget(enabled)
            controls: dict[str, object] = {"enabled": enabled}
            if spec.get("key_mode") != "none":
                key_row = QHBoxLayout()
                key = QLineEdit()
                key.setEchoMode(QLineEdit.EchoMode.Password)
                key.setPlaceholderText(
                    "已保存（留空则保留）"
                    if current.get("api_key_secret")
                    else "API Key（可选）" if spec.get("key_mode") == "optional" else "API Key"
                )
                clear = QCheckBox("清除")
                key_row.addWidget(key, 1)
                key_row.addWidget(clear)
                row_box.addLayout(key_row)
                controls.update({"key": key, "clear": clear})
            if spec.get("contact_email"):
                email = QLineEdit(str(current.get("contact_email", "") or ""))
                email.setPlaceholderText("联系邮箱（Crossref polite pool，建议填写）")
                row_box.addWidget(email)
                controls["contact_email"] = email
            status = QLabel("当前：" + masked_source_summary(self._data_sources, source_id))
            status.setObjectName("intelligenceHint")
            row_box.addWidget(status)
            sources.addWidget(row)
            self._source_controls[source_id] = controls

        content_box.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        if not self._embedded:
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
            buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
            buttons.accepted.connect(self._validate_and_accept)
            buttons.rejected.connect(self.reject)
            root.addWidget(buttons)
        apply_dialog_theme(self)

    def validate(self) -> bool:
        """Validate the visible service configuration without closing an embedded panel."""
        has_ai_key = bool(self.ai_key.text().strip() or (self._ai.get("api_key_secret") and not self.clear_ai_key.isChecked()))
        has_jcr_key = bool(self.jcr_key.text().strip() or (self._jcr.get("api_key_secret") and not self.clear_jcr_key.isChecked()))
        has_easyscholar_key = bool(
            self.easyscholar_key.text().strip()
            or (
                self._easyscholar.get("secret_key_secret")
                and self._easyscholar_readiness.get("ready")
                and not self.clear_easyscholar_key.isChecked()
            )
        )
        if self.ai_enabled.isChecked() and not has_ai_key:
            QMessageBox.warning(self, "缺少 API Key", "启用 DeepSeek 前请填写 API Key，或取消启用。")
            return False
        if self.jcr_enabled.isChecked() and not self.jcr_endpoint.text().strip():
            QMessageBox.warning(self, "缺少 JCR 地址", "请粘贴已授权的 Clarivate Journals API 请求地址，或取消启用。")
            return False
        if self.jcr_enabled.isChecked() and not has_jcr_key:
            QMessageBox.warning(self, "缺少 API Key", "启用 Clarivate JCR 前请填写 API Key，或取消启用。")
            return False
        if self.easyscholar_enabled.isChecked() and not has_easyscholar_key:
            message = (
                "当前保存的 EasyScholar 密钥无法解密，请重新填写后再保存。"
                if self._easyscholar_readiness.get("needs_reentry")
                and self._easyscholar.get("secret_key_secret")
                else "启用 EasyScholar 前请填写密钥，或取消启用。"
            )
            QMessageBox.warning(self, "需要重新填写密钥", message)
            return False
        return True

    def _validate_and_accept(self) -> None:
        if not self.validate():
            return
        self.accept()

    def values(self) -> tuple[dict, dict, dict]:
        ai = dict(self._ai)
        jcr = dict(self._jcr)
        easyscholar = dict(self._easyscholar)
        if self.clear_ai_key.isChecked():
            ai["api_key_secret"] = ""
        elif self.ai_key.text().strip():
            ai["api_key_secret"] = self._protect(self.ai_key.text(), "DeepSeek")
        if self.clear_jcr_key.isChecked():
            jcr["api_key_secret"] = ""
        elif self.jcr_key.text().strip():
            jcr["api_key_secret"] = self._protect(self.jcr_key.text(), "Clarivate")
        if self.clear_easyscholar_key.isChecked():
            easyscholar["secret_key_secret"] = ""
        elif self.easyscholar_key.text().strip():
            easyscholar["secret_key_secret"] = self._protect(self.easyscholar_key.text(), "EasyScholar")
        ai.update(
            {
                "enabled": self.ai_enabled.isChecked(),
                "base_url": self.ai_base_url.text().strip() or "https://api.deepseek.com",
                "model": self.ai_model.currentText().strip() or "deepseek-v4-flash",
                "journal_enrichment": self.ai_journal_enrichment.isChecked(),
                "journal_recommendation": self.ai_journal_recommendation.isChecked(),
                "frontier_rerank": self.ai_frontier_rerank.isChecked(),
                "auto_frontier_rerank": self.ai_frontier_auto.isChecked(),
                "research_profile_update": self.ai_profile_update.isChecked(),
                "auto_profile_from_achievements": self.ai_profile_auto_achievements.isChecked(),
                "profile_read_achievement_pdfs": self.ai_profile_read_pdfs.isChecked(),
                "auto_profile_from_frontier": self.ai_profile_auto_frontier.isChecked(),
                "journal_auto_enrichment": self.ai_journal_auto.isChecked(),
                "paper_record_fill": self.ai_paper_fill.isChecked(),
                "quick_capture": self.ai_quick_capture.isChecked(),
                "local_material_access": self.ai_local_materials.isChecked(),
                "local_material_project_isolation": self.ai_project_isolation.isChecked(),
                "local_material_use_manifest": self.ai_material_manifest.isChecked(),
            }
        )
        jcr.update(
            {
                "enabled": self.jcr_enabled.isChecked(),
                "endpoint_template": self.jcr_endpoint.text().strip(),
            }
        )
        easyscholar.update(
            {
                "enabled": self.easyscholar_enabled.isChecked(),
                "cache_days": self.easyscholar_cache_days.value(),
                "last_auto_checked": str(easyscholar.get("last_auto_checked", "")).strip(),
            }
        )
        return ai, jcr, easyscholar

    def _show_material_manifest(self) -> None:
        from utils.ai_material_context import load_ai_material_manifests

        entries = load_ai_material_manifests(8)
        if not entries:
            QMessageBox.information(self, "AI 资料使用清单", "尚无本地资料被 AI 调用使用。")
            return
        lines: list[str] = []
        for entry in reversed(entries):
            categories = [str(value.get("category", "")) for value in entry.get("materials", []) if isinstance(value, dict)]
            lines.append(
                f"{entry.get('created_at', '')} · {entry.get('task', '')}\n"
                f"论文/项目：{entry.get('paper_id') or entry.get('project_id') or '全局画像'}\n"
                f"资料：{'、'.join(categories) or '无'} · 清单 {entry.get('id', '')}"
            )
        QMessageBox.information(self, "AI 资料使用清单", "\n\n".join(lines))

    def data_sources_values(self) -> dict:
        """Return normalized encrypted source settings without exposing secrets."""
        values = normalize_source_settings(self._data_sources)
        try:
            for source_id, controls in self._source_controls.items():
                enabled = controls["enabled"]
                values[source_id]["enabled"] = bool(enabled.isChecked())
                key = controls.get("key")
                clear = controls.get("clear")
                if clear is not None and clear.isChecked():
                    values = update_source_secret(values, source_id, "")
                elif key is not None and key.text().strip():
                    values = update_source_secret(values, source_id, key.text().strip())
                email = controls.get("contact_email")
                if email is not None:
                    values[source_id]["contact_email"] = email.text().strip()[:254]
        except SecretStoreError as error:
            raise RuntimeError(f"无法加密来源 API Key：{error}") from error
        return normalize_source_settings(values)

    @staticmethod
    def _protect(value: str, service: str) -> str:
        try:
            return protect_secret(value)
        except SecretStoreError as error:
            raise RuntimeError(f"无法保存 {service} API Key：{error}") from error
