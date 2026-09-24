"""Direct, two-section settings surface for general and enhancement services."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ui.intelligence_dialog import IntelligenceSettingsDialog
from ui.settings_dialog import SettingsDialog
from ui.theme import apply_dialog_theme
from utils.app_info import APP_VERSION
from utils.file_manager import load_app_settings
from utils.source_registry import normalize_source_settings


SECTION_IDS = ("general", "enhancement")
SECTION_LABELS = ("通用设置", "增强服务")


class SettingsCenterDialog(QDialog):
    """One-click settings dialog with both complete forms visible at level one."""

    settings_saved = Signal(dict)
    backup_restored = Signal()
    data_location_changed = Signal()
    theme_previewed = Signal(str, str)
    theme_preview_reverted = Signal(str, str)

    def __init__(
        self,
        settings: dict,
        parent: QWidget | None = None,
        *,
        initial_section: str = "general",
    ) -> None:
        super().__init__(parent)
        self.settings = deepcopy(settings)
        self.setWindowTitle("设置")
        self.setMinimumSize(780, 580)
        self.resize(900, 680)
        self._build_ui()
        self.open_section(initial_section)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)
        title = QLabel(f"设置 · 科研助手 v{APP_VERSION}")
        title.setObjectName("settingsTitle")
        root.addWidget(title)

        body = QHBoxLayout()
        body.setSpacing(10)
        self.navigation = QListWidget()
        self.navigation.setObjectName("settingsCenterNavigation")
        self.navigation.setFixedWidth(142)
        for section_id, label in zip(SECTION_IDS, SECTION_LABELS, strict=True):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, section_id)
            self.navigation.addItem(item)
        body.addWidget(self.navigation)

        self.stack = QStackedWidget()
        self.stack.setObjectName("settingsCenterStack")
        self.general_panel = SettingsDialog(self.settings, self.stack, embedded=True)
        self.general_panel.backup_restored.connect(self.backup_restored.emit)
        self.general_panel.data_location_changed.connect(self.data_location_changed.emit)
        self.general_panel.theme_previewed.connect(self.theme_previewed.emit)
        self.general_panel.theme_preview_reverted.connect(self.theme_preview_reverted.emit)
        self.stack.addWidget(self.general_panel)

        self.enhancement_panel = IntelligenceSettingsDialog(
            self.settings.get("ai", {}),
            self.settings.get("jcr", {}),
            self.settings.get("easyscholar", {}),
            self.stack,
            data_sources=normalize_source_settings(self.settings.get("data_sources")),
            embedded=True,
        )
        self.stack.addWidget(self.enhancement_panel)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        apply_dialog_theme(self)

    def open_section(self, section_id: str) -> None:
        aliases = {
            "appearance": "general",
            "ai": "enhancement",
            "journal_tools": "enhancement",
            "research_profile": "general",
        }
        normalized = aliases.get(str(section_id or "general"), str(section_id or "general"))
        index = SECTION_IDS.index(normalized) if normalized in SECTION_IDS else 0
        self.navigation.setCurrentRow(index)

    def _save(self) -> None:
        if not self.general_panel.prepare_save():
            self.open_section("general")
            return
        if not self.enhancement_panel.validate():
            self.open_section("enhancement")
            return
        try:
            ai, jcr, easyscholar = self.enhancement_panel.values()
            data_sources = self.enhancement_panel.data_sources_values()
        except RuntimeError as error:
            self.open_section("enhancement")
            QMessageBox.warning(self, "无法保存密钥", str(error))
            return

        # A background journal task may finish while this window is open.
        # Preserve that newest checkpoint when saving otherwise unrelated fields.
        latest_ai = load_app_settings().get("ai", {})
        if isinstance(latest_ai, dict) and str(latest_ai.get("journal_auto_last_checked", "")).strip():
            ai["journal_auto_last_checked"] = str(latest_ai["journal_auto_last_checked"])

        updated = self.general_panel.values()
        updated.update(
            {
                "ai": ai,
                "jcr": jcr,
                "easyscholar": easyscholar,
                "data_sources": data_sources,
            }
        )
        self.settings = deepcopy(updated)
        self.general_panel._settings = self.settings
        self.settings_saved.emit(deepcopy(updated))

    def reject(self) -> None:
        density = str(self.settings.get("appearance", {}).get("density", "comfortable"))
        self.theme_preview_reverted.emit("fog_teal", density)
        super().reject()
