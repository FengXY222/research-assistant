from __future__ import annotations

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QKeySequenceEdit,
    QSlider,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from utils.autostart import autostart_status, is_autostart_enabled, set_autostart
from utils.app_info import APP_VERSION
from utils.source_registry import normalize_source_settings
from ui.theme import apply_dialog_theme
from utils.file_manager import (
    change_data_location,
    create_backup,
    data_location,
    import_legacy_data_directory,
    list_backups,
    load_app_settings,
    restore_backup,
)


class BackupRestoreDialog(QDialog):
    """Choose a local JSON snapshot and restore it explicitly."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.was_restored = False
        self.setWindowTitle("恢复备份")
        parent_width = parent.width() if parent else 480
        self.setMinimumWidth(340)
        self.setMaximumWidth(max(340, min(480, parent_width - 24)))
        self.resize(max(340, min(430, parent_width - 24)), 230)
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QLabel { color: #f4f6ff; }
            #backupHint { color: #aebbd8; font-size: 11px; }
            QComboBox { background: #202d4d; color: #ffffff; border: 1px solid #53698f; border-radius: 6px; padding: 7px 9px; }
            QComboBox QAbstractItemView { background: #202d4d; color: #ffffff; selection-background-color: #2e78b7; selection-color: #ffffff; border: 1px solid #70c9ff; outline: 0; }
            QComboBox QAbstractItemView::item { min-height: 24px; padding: 5px 10px; color: #ffffff; }
            QComboBox QAbstractItemView::item:selected { background: #2e78b7; color: #ffffff; }
            QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 13px; }
            #restoreButton { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)
        title = QLabel("选择要恢复的备份")
        title.setObjectName("settingsTitle")
        root.addWidget(title)
        self.combo = QComboBox()
        self.backups = list_backups()
        for backup in self.backups:
            kind = "自动" if backup.get("automatic") else "手动"
            label = f"{backup.get('created_at', backup.get('name', ''))} · {kind} · {backup.get('file_count', 0)} 个文件"
            self.combo.addItem(label, backup.get("name"))
        root.addWidget(self.combo)
        hint = QLabel("恢复将替换任务、投稿、期刊库、灵感和待读数据；窗口设置不会被覆盖。")
        hint.setObjectName("backupHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        restore = QPushButton("恢复此备份")
        restore.setObjectName("restoreButton")
        restore.setEnabled(bool(self.backups))
        restore.clicked.connect(self._restore)
        actions.addWidget(cancel)
        actions.addWidget(restore)
        root.addLayout(actions)

    def _restore(self) -> None:
        backup_name = self.combo.currentData()
        if not backup_name:
            return
        answer = QMessageBox.question(self, "确认恢复", "当前本地数据将被备份内容替换，是否继续？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            restore_backup(str(backup_name))
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "恢复失败", str(error))
            return
        self.was_restored = True
        self.accept()


class SettingsDialog(QDialog):
    """Small, readable home for persistent desktop-widget preferences."""

    settings_saved = Signal(dict)
    backup_restored = Signal()
    data_location_changed = Signal()
    theme_previewed = Signal(str, str)
    theme_preview_reverted = Signal(str, str)

    def __init__(self, settings: dict, parent: QWidget | None = None, *, embedded: bool = False) -> None:
        super().__init__(parent)
        self._embedded = bool(embedded)
        if self._embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self._settings = settings
        appearance = settings.get("appearance", {}) if isinstance(settings.get("appearance"), dict) else {}
        self._initial_theme_id = "fog_teal"
        self._initial_density = str(appearance.get("density", "comfortable"))
        self._ai_settings = dict(settings.get("ai", {})) if isinstance(settings.get("ai"), dict) else {}
        self._jcr_settings = dict(settings.get("jcr", {})) if isinstance(settings.get("jcr"), dict) else {}
        self._easyscholar_settings = (
            dict(settings.get("easyscholar", {})) if isinstance(settings.get("easyscholar"), dict) else {}
        )
        self._data_sources_settings = normalize_source_settings(settings.get("data_sources"))
        self._initial_autostart = is_autostart_enabled()
        if not self._embedded:
            self.setWindowTitle("设置")
            parent_width = parent.width() if parent else 480
            parent_height = parent.height() if parent else 720
            self.setMinimumWidth(350)
            self.setMaximumWidth(max(350, min(500, parent_width - 24)))
            self.setMinimumHeight(500)
            self.resize(max(350, min(460, parent_width - 24)), max(500, min(710, parent_height - 12)))
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0 if self._embedded else 22, 0 if self._embedded else 20, 0 if self._embedded else 22, 0 if self._embedded else 18)
        root.setSpacing(10)
        if not self._embedded:
            title = QLabel("设置")
            title.setObjectName("settingsTitle")
            root.addWidget(title)
            hint = QLabel(f"科研助手 v{APP_VERSION} · 窗口、提醒与本地数据都在这里管理。")
            hint.setObjectName("settingsHint")
            hint.setWordWrap(True)
            hint.setMinimumWidth(0)
            hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("settingsContent")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content_root = QVBoxLayout(content)
        content_root.setContentsMargins(0, 0, 6, 0)
        content_root.setSpacing(9)

        def section(title_text: str, hint_text: str) -> QVBoxLayout:
            card = QFrame()
            card.setObjectName("settingsSection")
            box = QVBoxLayout(card)
            box.setContentsMargins(13, 11, 13, 12)
            box.setSpacing(7)
            heading = QLabel(title_text)
            heading.setObjectName("settingsSectionTitle")
            box.addWidget(heading)
            if hint_text:
                section_hint = QLabel(hint_text)
                section_hint.setObjectName("settingsSectionHint")
                section_hint.setWordWrap(True)
                section_hint.setMinimumWidth(0)
                section_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                box.addWidget(section_hint)
            content_root.addWidget(card)
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
            label.setObjectName("settingsCheckText")
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            row = QWidget()
            row.setObjectName("settingsCheckRow")
            row.setMinimumWidth(0)
            row.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(7)
            layout.addWidget(checkbox, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(label, 1, Qt.AlignmentFlag.AlignTop)
            return checkbox, row

        appearance_section = section(
            "外观",
            "统一使用雾青蓝主题，可按屏幕大小调整信息密度。",
        )
        appearance_form = form_layout()
        self.density_selector = QComboBox()
        self.density_selector.addItem("舒适（默认）", "comfortable")
        self.density_selector.addItem("紧凑（更多信息）", "compact")
        density_index = self.density_selector.findData(self._initial_density)
        self.density_selector.setCurrentIndex(density_index if density_index >= 0 else 0)
        appearance_form.addRow("信息密度", self.density_selector)
        self.density_selector.currentIndexChanged.connect(self._preview_theme)
        appearance_section.addLayout(appearance_form)

        window_section = section("窗口与导航", "常用显示项放在一起；鼠标穿透开启后，可双击窗口标题解除。")
        window_form = form_layout()

        opacity_row = QHBoxLayout()
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(20, 100)
        self.opacity_slider.setValue(int(self._settings.get("opacity", 96)))
        self.opacity_value = QLabel()
        self.opacity_value.setMinimumWidth(38)
        self.opacity_slider.valueChanged.connect(lambda _value: self._sync_opacity_control())
        self._sync_opacity_control()
        opacity_row.addWidget(self.opacity_slider, 1)
        opacity_row.addWidget(self.opacity_value)
        window_form.addRow("窗口透明度", opacity_row)

        self.always_on_top, always_on_top_row = checkbox_row("窗口始终置顶")
        self.always_on_top.setChecked(bool(self._settings.get("always_on_top", True)))
        window_form.addRow("窗口层级", always_on_top_row)

        self.close_to_tray, close_to_tray_row = checkbox_row("点击关闭按钮时仅收起到系统托盘，不退出科研助手")
        self.close_to_tray.setChecked(bool(self._settings.get("close_to_tray", False)))
        window_form.addRow("仅最小化不退出", close_to_tray_row)

        self.click_through, click_through_row = checkbox_row("鼠标点击穿透窗口（双击“科研助手”解除）")
        self.click_through.setChecked(bool(self._settings.get("click_through", False)))
        window_form.addRow("鼠标穿透", click_through_row)

        self.sidebar_position = QComboBox()
        self.sidebar_position.addItem("左侧", "left")
        self.sidebar_position.addItem("右侧", "right")
        self.sidebar_position.setCurrentIndex(1 if self._settings.get("sidebar_position") == "right" else 0)
        window_form.addRow("标签位置", self.sidebar_position)

        self.sidebar_auto_hide, sidebar_auto_hide_row = checkbox_row("闲置时自动收起导航")
        self.sidebar_auto_hide.setChecked(bool(self._settings.get("sidebar_auto_hide", True)))
        window_form.addRow("导航自动收起", sidebar_auto_hide_row)

        self.sidebar_collapse_mode = QComboBox()
        self.sidebar_collapse_mode.addItem("收起标签（保留细边触发区）", "labels")
        self.sidebar_collapse_mode.addItem("收起主页面（保留导航栏）", "content")
        self.sidebar_collapse_mode.setCurrentIndex(
            1 if self._settings.get("sidebar_collapse_mode") == "content" else 0
        )
        window_form.addRow("自动收起方式", self.sidebar_collapse_mode)

        self.return_home_when_inactive, return_home_row = checkbox_row("离开窗口 1 分钟后返回首页（阅读时不打断）")
        self.return_home_when_inactive.setChecked(bool(self._settings.get("return_home_when_inactive", True)))
        window_form.addRow("自动返回首页", return_home_row)
        window_section.addLayout(window_form)

        reminder_section = section("提醒与快捷操作", "这些选项不会影响数据保存，可按每天的工作节奏调整。")
        reminder_form = form_layout()

        self.autostart, autostart_row = checkbox_row("登录 Windows 后自动运行科研助手")
        self.autostart.setChecked(self._initial_autostart)
        reminder_form.addRow("开机自启动", autostart_row)
        startup_probe = autostart_status()
        self.autostart_status_label = QLabel(
            "状态：" + str(startup_probe.get("message", "尚未检测"))
        )
        self.autostart_status_label.setObjectName("settingsHint")
        self.autostart_status_label.setWordWrap(True)
        reminder_form.addRow("启动探测", self.autostart_status_label)

        self.ready_submission, ready_submission_row = checkbox_row("准备投稿时显示论文与目标期刊提示")
        self.ready_submission.setChecked(bool(self._settings.get("ready_submission_reminder", True)))
        reminder_form.addRow("投稿提示", ready_submission_row)

        shortcut = self._settings.get("journal_import_shortcut", {})
        shortcut = shortcut if isinstance(shortcut, dict) else {}
        self.journal_import_shortcut_mode = QComboBox()
        self.journal_import_shortcut_mode.addItem("双击 Tab（全局）", "double_tab")
        self.journal_import_shortcut_mode.addItem("全局自定义组合键", "sequence")
        self.journal_import_shortcut_mode.addItem("关闭快捷键", "off")
        stored_mode = str(shortcut.get("mode", "double_tab"))
        index = self.journal_import_shortcut_mode.findData(stored_mode)
        self.journal_import_shortcut_mode.setCurrentIndex(index if index >= 0 else 0)
        self.journal_import_shortcut_sequence = QKeySequenceEdit()
        self.journal_import_shortcut_sequence.setKeySequence(QKeySequence(str(shortcut.get("sequence", "Ctrl+Alt+J"))))
        self.journal_import_shortcut_sequence.setMaximumSequenceLength(1)
        self.journal_import_shortcut_sequence.setClearButtonEnabled(True)
        self.journal_import_shortcut_sequence.setToolTip("选择“全局自定义组合键”后，点击此处并按下新组合键")
        self.journal_import_shortcut_hint = QLabel("科研助手运行时，双击 Tab 或自定义全局组合键均可从任意软件唤起选刊。")
        self.journal_import_shortcut_hint.setObjectName("settingsHint")
        self.journal_import_shortcut_hint.setWordWrap(True)
        shortcut_row = QWidget()
        shortcut_layout = QVBoxLayout(shortcut_row)
        shortcut_layout.setContentsMargins(0, 0, 0, 0)
        shortcut_layout.setSpacing(5)
        shortcut_layout.addWidget(self.journal_import_shortcut_mode)
        shortcut_layout.addWidget(self.journal_import_shortcut_sequence)
        shortcut_layout.addWidget(self.journal_import_shortcut_hint)
        self.journal_import_shortcut_mode.currentIndexChanged.connect(self._update_journal_import_shortcut_state)
        self._update_journal_import_shortcut_state()
        reminder_form.addRow("期刊库导入", shortcut_row)

        visibility_shortcut = self._settings.get("window_visibility_shortcut", {})
        visibility_shortcut = visibility_shortcut if isinstance(visibility_shortcut, dict) else {}
        self.window_visibility_shortcut_mode = QComboBox()
        self.window_visibility_shortcut_mode.addItem("双击空格（全局）", "double_space")
        self.window_visibility_shortcut_mode.addItem("全局自定义组合键", "sequence")
        self.window_visibility_shortcut_mode.addItem("关闭快捷键", "off")
        visibility_mode = str(visibility_shortcut.get("mode", "double_space"))
        visibility_index = self.window_visibility_shortcut_mode.findData(visibility_mode)
        self.window_visibility_shortcut_mode.setCurrentIndex(visibility_index if visibility_index >= 0 else 0)
        self.window_visibility_shortcut_sequence = QKeySequenceEdit()
        self.window_visibility_shortcut_sequence.setKeySequence(
            QKeySequence(str(visibility_shortcut.get("sequence", "Ctrl+Alt+Space")))
        )
        self.window_visibility_shortcut_sequence.setMaximumSequenceLength(1)
        self.window_visibility_shortcut_sequence.setClearButtonEnabled(True)
        self.window_visibility_shortcut_sequence.setToolTip("选择“全局自定义组合键”后，点击此处并按下新组合键")
        self.window_visibility_shortcut_hint = QLabel()
        self.window_visibility_shortcut_hint.setObjectName("settingsHint")
        self.window_visibility_shortcut_hint.setWordWrap(True)
        visibility_row = QWidget()
        visibility_layout = QVBoxLayout(visibility_row)
        visibility_layout.setContentsMargins(0, 0, 0, 0)
        visibility_layout.setSpacing(5)
        visibility_layout.addWidget(self.window_visibility_shortcut_mode)
        visibility_layout.addWidget(self.window_visibility_shortcut_sequence)
        visibility_layout.addWidget(self.window_visibility_shortcut_hint)
        self.window_visibility_shortcut_mode.currentIndexChanged.connect(self._update_window_visibility_shortcut_state)
        self._update_window_visibility_shortcut_state()
        reminder_form.addRow("显示 / 隐藏", visibility_row)

        self.frontier_background_refresh, frontier_background_row = checkbox_row("启动后在后台检查每日前沿更新")
        self.frontier_background_refresh.setChecked(bool(self._settings.get("frontier_background_refresh", True)))
        reminder_form.addRow("每日前沿", frontier_background_row)
        reminder_section.addLayout(reminder_form)

        data_section = section("数据与备份", "所有记录仍保存在本机；导入旧版数据前会先创建当前数据快照。")
        data_form = form_layout()

        self.auto_backup, auto_backup_row = checkbox_row("每天首次打开时自动备份，保留最近 30 份")
        self.auto_backup.setChecked(bool(self._settings.get("auto_backup", False)))
        data_form.addRow("自动备份", auto_backup_row)
        data_section.addLayout(data_form)

        backup_row = QHBoxLayout()
        self.backup_status = QLabel("备份保存在本地数据目录的 backups 文件夹")
        self.backup_status.setObjectName("settingsHint")
        self.backup_status.setWordWrap(True)
        self.backup_status.setMinimumWidth(0)
        self.backup_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        backup_row.addWidget(self.backup_status, 1)
        backup_now = QPushButton("立即备份")
        backup_now.setObjectName("backupButton")
        backup_now.clicked.connect(self._create_backup)
        backup_row.addWidget(backup_now)
        restore = QPushButton("恢复备份")
        restore.setObjectName("backupButton")
        restore.clicked.connect(self._open_restore)
        backup_row.addWidget(restore)
        data_section.addLayout(backup_row)

        data_row = QHBoxLayout()
        self.path_label = QLabel()
        self.path_label.setObjectName("settingsPath")
        self.path_label.setWordWrap(True)
        self.path_label.setMinimumWidth(0)
        self.path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._refresh_data_path_label()
        data_row.addWidget(self.path_label, 1)
        change_data = QPushButton("更改位置")
        change_data.setObjectName("changeDataButton")
        change_data.setToolTip("选择新的数据文件夹，并安全迁移现有数据与备份")
        change_data.clicked.connect(self._change_data_path)
        data_row.addWidget(change_data)
        open_data = QPushButton("打开目录")
        open_data.setObjectName("openDataButton")
        open_data.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_location()))))
        data_row.addWidget(open_data)
        data_section.addLayout(data_row)

        import_row = QHBoxLayout()
        import_hint = QLabel("从 v0.7.7 / v0.7.8 便携版的 data 文件夹接管记录")
        import_hint.setObjectName("settingsHint")
        import_hint.setWordWrap(True)
        import_hint.setMinimumWidth(0)
        import_hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        import_row.addWidget(import_hint, 1)
        import_old = QPushButton("导入旧版数据")
        import_old.setObjectName("importDataButton")
        import_old.clicked.connect(self._import_legacy_data)
        import_row.addWidget(import_old)
        data_section.addLayout(import_row)

        content_root.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        if not self._embedded:
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
            buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
            buttons.accepted.connect(self._save)
            buttons.rejected.connect(self.reject)
            root.addWidget(buttons)
        # Existing versions carried a local navy QSS. Replace it after the
        # controls have been built so this dialog follows the selected theme
        # just like the main workspace and its popup lists.
        apply_dialog_theme(self)

    def _create_backup(self) -> None:
        try:
            backup = create_backup()
        except OSError as error:
            QMessageBox.warning(self, "备份失败", str(error))
            return
        self.backup_status.setText(f"已创建备份：{backup.name}")

    def _open_restore(self) -> None:
        dialog = BackupRestoreDialog(self)
        dialog.exec()
        if dialog.was_restored:
            self.backup_status.setText("已恢复备份，界面数据已重新载入。")
            self.backup_restored.emit()

    def _refresh_data_path_label(self) -> None:
        self.path_label.setText(f"本地数据：{data_location()}")

    def _change_data_path(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择科研助手数据保存文件夹",
            str(data_location()),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected:
            return
        try:
            target = change_data_location(selected)
        except (OSError, ValueError) as error:
            self.backup_status.setText(f"更改保存位置失败：{error}")
            return
        self._refresh_data_path_label()
        self.backup_status.setText(f"数据已迁移到：{target}；旧位置已保留作恢复保障。")
        self.data_location_changed.emit()

    def _import_legacy_data(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择旧版科研助手的 data 文件夹",
            str(data_location().parent),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected:
            return
        answer = QMessageBox.question(
            self,
            "导入旧版数据",
            "所选 data 文件夹中的任务、论文、期刊、灵感、待读和前沿记录将覆盖当前数据。导入前会创建一个本地备份，是否继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            copied = import_legacy_data_directory(selected)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "导入失败", str(error))
            return
        self.backup_status.setText(f"已导入 {copied} 个旧版数据文件；导入前数据已尝试备份。")
        self.data_location_changed.emit()

    def _preview_theme(self) -> None:
        self.theme_previewed.emit(
            "fog_teal",
            str(self.density_selector.currentData()),
        )

    def _sync_opacity_control(self) -> None:
        """Show the live opacity for the single widget surface."""
        self.opacity_slider.setEnabled(True)
        self.opacity_value.setText(f"{self.opacity_slider.value()}%")

    def values(self) -> dict:
        """Return every settings value owned by this dialog for safe merging."""
        return {
            "appearance": {
                "theme_id": "fog_teal",
                "density": str(self.density_selector.currentData()),
            },
            "application_mode": "widget",
            "opacity": self.opacity_slider.value(),
            "always_on_top": self.always_on_top.isChecked(),
            "autostart": self.autostart.isChecked(),
            "close_to_tray": self.close_to_tray.isChecked(),
            "click_through": self.click_through.isChecked(),
            "ready_submission_reminder": self.ready_submission.isChecked(),
            "sidebar_auto_hide": self.sidebar_auto_hide.isChecked(),
            "sidebar_position": str(self.sidebar_position.currentData()),
            "sidebar_collapse_mode": str(self.sidebar_collapse_mode.currentData()),
            "journal_import_shortcut": {
                "mode": str(self.journal_import_shortcut_mode.currentData()),
                "sequence": self.journal_import_shortcut_sequence.keySequence().toString(),
            },
            "window_visibility_shortcut": {
                "mode": str(self.window_visibility_shortcut_mode.currentData()),
                "sequence": self.window_visibility_shortcut_sequence.keySequence().toString(),
            },
            "return_home_when_inactive": self.return_home_when_inactive.isChecked(),
            "frontier_background_refresh": self.frontier_background_refresh.isChecked(),
            "ai": dict(self._ai_settings),
            "jcr": dict(self._jcr_settings),
            "easyscholar": dict(self._easyscholar_settings),
            "data_sources": normalize_source_settings(self._data_sources_settings),
            "auto_backup": self.auto_backup.isChecked(),
            "research": dict(self._settings.get("research", {})),
            "window": dict(self._settings.get("window", {})),
            "widget_window": dict(self._settings.get("widget_window", {})),
            "software_window": dict(self._settings.get("software_window", {})),
        }

    def prepare_save(self) -> bool:
        """Validate general settings and apply the autostart side effect."""
        if self.journal_import_shortcut_mode.currentData() == "sequence" and self.journal_import_shortcut_sequence.keySequence().isEmpty():
            QMessageBox.warning(self, "缺少快捷键", "请按下带 Ctrl、Alt、Shift 或 Win 的组合键，或改为“全局双击 Tab”/“关闭快捷键”。")
            return False
        if self.window_visibility_shortcut_mode.currentData() == "sequence" and self.window_visibility_shortcut_sequence.keySequence().isEmpty():
            QMessageBox.warning(self, "缺少快捷键", "请按下带 Ctrl、Alt、Shift 或 Win 的组合键，或改为“全局双击空格”/“关闭快捷键”。")
            return False
        enabled = self.autostart.isChecked()
        if enabled != self._initial_autostart:
            try:
                startup_probe = set_autostart(enabled)
            except OSError as error:
                QMessageBox.warning(self, "无法更新开机启动", str(error))
                return False
            self.autostart_status_label.setText("状态：" + str(startup_probe.get("message", "已更新")))
            self._initial_autostart = bool(startup_probe.get("enabled"))
        # Daily AI jobs can update this marker while the settings window is
        # open. Keep the newest persisted value when saving unrelated options.
        latest_ai = load_app_settings().get("ai", {})
        if isinstance(latest_ai, dict) and str(latest_ai.get("journal_auto_last_checked", "")).strip():
            self._ai_settings["journal_auto_last_checked"] = str(latest_ai["journal_auto_last_checked"])
        return True

    def _save(self) -> None:
        if not self.prepare_save():
            return
        self.settings_saved.emit(self.values())
        self.accept()

    def reject(self) -> None:
        self.theme_preview_reverted.emit(self._initial_theme_id, self._initial_density)
        super().reject()

    def _update_journal_import_shortcut_state(self) -> None:
        mode = str(self.journal_import_shortcut_mode.currentData())
        custom = mode == "sequence"
        self.journal_import_shortcut_sequence.setEnabled(custom)
        if mode == "double_tab":
            self.journal_import_shortcut_hint.setText("科研助手运行时，在任意前台软件中连按两次 Tab 即可唤起“为论文选刊”。")
        elif custom:
            self.journal_import_shortcut_hint.setText("科研助手运行时，在任意前台软件中都可唤起“为论文选刊”。点击上方输入框后按下带 Ctrl、Alt、Shift 或 Win 的组合键。")
        else:
            self.journal_import_shortcut_hint.setText("已关闭。仍可在论文编辑窗口点击“导入期刊库”。")

    def _update_window_visibility_shortcut_state(self) -> None:
        mode = str(self.window_visibility_shortcut_mode.currentData())
        custom = mode == "sequence"
        self.window_visibility_shortcut_sequence.setEnabled(custom)
        if mode == "double_space":
            self.window_visibility_shortcut_hint.setText(
                "科研助手运行时，在任意前台软件中连按两次空格可显示或隐藏窗口；中文输入法开启时自动禁用。"
            )
        elif custom:
            self.window_visibility_shortcut_hint.setText(
                "科研助手运行时，在任意前台软件中切换显示/隐藏。点击上方输入框后按下带 Ctrl、Alt、Shift 或 Win 的组合键。"
            )
        else:
            self.window_visibility_shortcut_hint.setText("已关闭。仍可通过托盘图标或桌面快捷方式显示窗口。")
