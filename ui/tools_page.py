"""Lightweight host for optional, independently managed user tools."""
from __future__ import annotations
import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox, QDialog,
    QFormLayout, QLineEdit, QSpinBox, QDialogButtonBox, QProgressBar, QCheckBox)

from utils import file_manager
from utils.pdf_translation_controller import TranslationController
from utils.secure_store import protect_secret
from ui.page_kit import PageHeader


STATUS = {"queued": "排队", "running": "翻译中", "cancelling": "正在停止", "cancelled": "已取消",
          "interrupted": "已中断，可重试", "failed": "失败，可重试", "completed": "完成"}
STAGES = {"Parse PDF and Create Intermediate Representation": "解析论文",
    "DetectScannedFile": "检查扫描页面", "Parse Page Layout": "识别版式", "Parse Paragraphs": "整理段落",
    "Parse Formulas and Styles": "识别公式与样式", "Automatic Term Extraction": "提取术语",
    "Translate Paragraphs": "翻译正文", "Typesetting": "排版译文", "Add Fonts": "嵌入字体",
    "Generate drawing instructions": "生成页面", "Subset font": "整理字体", "Save PDF": "保存 PDF"}


class ToolsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.translation = TranslationController(Path(file_manager.DATA_DIR) / "pdf-translator", self)
        self._job_order = ()
        layout = QVBoxLayout(self)
        header = PageHeader("PDF 翻译", accent="todo")
        header.add_primary_action("添加 PDF", self._add)
        layout.addWidget(header)
        hint = QLabel("保留论文版式，生成中文与双语 PDF。一次翻译一篇，其余排队；切换页面不会中断。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        bar = QHBoxLayout()
        for text, callback in (("继续队列", self.translation.start), ("翻译设置", self._settings)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            bar.addWidget(button)
        layout.addLayout(bar)
        self.pages = QLineEdit()
        self.pages.setPlaceholderText("页码：留空翻译全文，或填写 1-3,5")
        layout.addWidget(self.pages)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)
        self.stage = QLabel("添加论文即可开始；模型仅在翻译进程中加载。")
        self.stage.setWordWrap(True)
        layout.addWidget(self.stage)
        layout.addWidget(QLabel("翻译队列 · 论文 / 状态 / 进度（最近 100 条）"))
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("pdfTranslationQueue")
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().hide()
        self.table.setHorizontalHeaderLabels(["论文", "状态", "进度"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._selection)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        for text, callback in (("取消选中任务", self._cancel), ("重试选中任务", self._retry), ("打开结果文件夹", self._open)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.detail = QLabel("取消先请求停止，超时后清理该任务进程树。中断任务保留参数和翻译缓存。")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        self.translation.changed.connect(self.reload)
        self.translation.progress.connect(self._progress)
        self.translation.notice.connect(lambda text: QMessageBox.warning(self, "PDF 翻译", text))
        self.reload()

    def _id(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def reload(self):
        root = file_manager.DATA_DIR / "pdf-translator"
        if self.translation.store.root.resolve() != root.resolve():
            self.translation.relocate(root)
        selected = self._id()
        jobs = self.translation.store.rows()
        order = tuple(job["id"] for job in jobs)
        rebuild = order != self._job_order
        self.table.blockSignals(True)
        if rebuild:
            self.table.setRowCount(len(jobs))
        for row, job in enumerate(jobs):
            if rebuild:
                params = json.loads(job["params"])
                title = QTableWidgetItem(Path(params["input"]).name)
                title.setData(Qt.ItemDataRole.UserRole, job["id"])
                title.setToolTip(params["input"] + "\n页码：" + (params.get("pages") or "全文"))
                self.table.setItem(row, 0, title)
            for column, text in ((1, STATUS.get(job["status"], job["status"])), (2, f'{job["progress"]:.0f}%')):
                item = self.table.item(row, column)
                if item is None:
                    self.table.setItem(row, column, QTableWidgetItem(text))
                elif item.text() != text:
                    item.setText(text)
            if rebuild and job["id"] == selected:
                self.table.selectRow(row)
        self._job_order = order
        self.table.blockSignals(False)
        self._selection()

    def _progress(self, event):
        self.progress_bar.setValue(int(float(event.get("overall_progress", 0) or 0)))
        stage = str(event.get("stage", "翻译中"))
        self.stage.setText(STAGES.get(stage, stage))

    def _selection(self):
        job = self.translation.store.get(self._id()) if self._id() else None
        if job:
            self.detail.setText(job["error"] or ("已完成，可打开结果文件夹。" if job["status"] == "completed" else "任务参数已保存；重试会复用引擎缓存，不代表精确续接原进度。"))

    def _add(self):
        if not self.translation.store.settings():
            if not self._settings():
                return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择待翻译论文", "", "PDF (*.pdf)")
        for path in paths:
            try:
                self.translation.enqueue(path, pages=self.pages.text().strip())
            except Exception as error:
                QMessageBox.warning(self, "无法添加", str(error))
                break

    def _cancel(self):
        if self._id():
            self.translation.cancel(self._id())

    def _retry(self):
        if self._id():
            self.translation.retry(self._id())

    def _open(self):
        job = self.translation.store.get(self._id()) if self._id() else None
        if job:
            path = Path(json.loads(job["params"])["output"])
            if path.is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _settings(self):
        values = self.translation.store.settings()
        if not values:
            from utils.ai_service import get_ai_settings
            values = get_ai_settings()
        dialog = QDialog(self)
        dialog.setWindowTitle("独立 PDF 翻译设置")
        layout = QFormLayout(dialog)
        hint = QLabel("独立于主软件的 API 限流和空闲任务管理。任务仅在添加论文或继续队列后运行。")
        hint.setWordWrap(True)
        layout.addRow(hint)
        url = QLineEdit(values.get("base_url", "https://api.deepseek.com"))
        model = QLineEdit(values.get("model", "deepseek-chat"))
        key = QLineEdit()
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText("留空保留已有密钥" if values.get("api_key_secret") else "填写 API 密钥")
        qps, workers = QSpinBox(), QSpinBox()
        qps.setRange(1, 4)
        workers.setRange(1, 4)
        qps.setValue(int(values.get("qps", 2)))
        workers.setValue(int(values.get("workers", 2)))
        proxy = QCheckBox("使用系统代理（默认直连）")
        proxy.setChecked(bool(values.get("use_system_proxy", False)))
        backup_outputs = QCheckBox("自动备份包含翻译结果 PDF（备份会更大）")
        backup_outputs.setChecked(bool(values.get("backup_outputs", False)))
        for label, widget in (("API 地址", url), ("模型", model), ("密钥", key), ("独立请求速率 / 秒", qps), ("单文档并发", workers)):
            layout.addRow(label, widget)
        layout.addRow(proxy)
        layout.addRow(backup_outputs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        try:
            endpoint = url.text().strip().rstrip("/")
            if endpoint.endswith("/chat/completions"):
                endpoint = endpoint[:-len("/chat/completions")]
            if not endpoint.startswith(("https://", "http://")) or not model.text().strip():
                raise ValueError("请填写有效的 API 地址和模型。")
            secret = protect_secret(key.text()) if key.text().strip() else values.get("api_key_secret", "")
            if not secret:
                raise ValueError("请填写翻译密钥。")
            self.translation.store.save_settings({"base_url": endpoint, "model": model.text().strip(), "api_key_secret": secret,
                "qps": qps.value(), "workers": workers.value(), "lang_in": "en", "lang_out": "zh-CN", "use_system_proxy": proxy.isChecked(),
                "backup_outputs": backup_outputs.isChecked()})
            return True
        except Exception as error:
            QMessageBox.warning(self, "翻译设置", str(error))
            return False
