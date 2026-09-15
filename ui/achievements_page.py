from __future__ import annotations

from datetime import date
from uuid import uuid4

from pathlib import Path

from PySide6.QtCore import QDate, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs import confirm_delete, show_undo_toast
from ui.reorder import OrderDragHandle, ReorderableColumn
from utils.file_manager import ACHIEVEMENT_CATEGORIES, load_achievements, save_achievements


def _pdf_links(item: dict) -> list[dict]:
    """Expose explicit and legacy linked PDFs in one compact outcome editor."""
    result: list[dict] = []
    seen: set[str] = set()
    for field in ("pdf_files", "files"):
        entries = item.get(field, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path", "")).strip()
            if not path or Path(path).suffix.casefold() != ".pdf":
                continue
            key = path.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "id": str(entry.get("id") or uuid4().hex),
                    "name": str(entry.get("name", "")).strip() or Path(path).name,
                    "path": path,
                    "kind": "file",
                }
            )
    return result


class AchievementPdfRow(QFrame):
    remove_requested = Signal(str)
    open_requested = Signal(str)

    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("achievementPdfRow")
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 4, 6, 4)
        root.setSpacing(6)
        icon = QLabel("PDF")
        icon.setObjectName("achievementPdfBadge")
        root.addWidget(icon)
        name = QLabel(str(item.get("name", "未命名 PDF")))
        name.setObjectName("achievementPdfName")
        name.setToolTip(str(item.get("path", "")))
        name.setMinimumWidth(0)
        root.addWidget(name, 1)
        open_file = QPushButton("打开")
        open_file.setObjectName("rowButton")
        open_file.setToolTip("打开本地 PDF，不会复制或上传文件")
        open_file.clicked.connect(lambda: self.open_requested.emit(str(item.get("path", ""))))
        root.addWidget(open_file)
        remove = QPushButton("×")
        remove.setObjectName("dangerButton")
        remove.setToolTip("移除 PDF 链接，不删除本地文件")
        remove.clicked.connect(lambda: self.remove_requested.emit(str(item.get("id", ""))))
        root.addWidget(remove)


class AchievementDialog(QDialog):
    """A deliberately small editor for papers, patents, awards and other outputs."""

    def __init__(self, parent: QWidget | None = None, item: dict | None = None) -> None:
        super().__init__(parent)
        self._item = item or {}
        self.setWindowTitle("编辑成果" if item else "新增成果")
        parent_width = parent.width() if parent else 520
        self.setMinimumWidth(360)
        self.setMaximumWidth(max(360, min(560, parent_width - 22)))
        self.resize(max(360, min(490, parent_width - 22)), 500)
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QLabel { color: #f4f6ff; }
            #achievementDialogHint { color: #aebbd8; font-size: 11px; }
            QLineEdit, QComboBox, QDateEdit, QPlainTextEdit { background: #202d4d; color: #ffffff; border: 1px solid #53698f; border-radius: 6px; padding: 7px 9px; }
            QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QPlainTextEdit:focus { border-color: #70c9ff; }
            QComboBox QAbstractItemView { background: #202d4d; color: #ffffff; selection-background-color: #2e78b7; selection-color: #ffffff; border: 1px solid #70c9ff; outline: 0; }
            QComboBox QAbstractItemView::item { min-height: 24px; padding: 5px 10px; color: #ffffff; }
            QDialogButtonBox QPushButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 7px 16px; }
            QDialogButtonBox QPushButton[text="保存"] { background: #31d879; color: #07131f; border-color: #31d879; font-weight: 700; }
            #achievementPdfRow { background: #172340; border: 1px solid #42577d; border-radius: 6px; }
            #achievementPdfBadge { color: #88d4ff; font-size: 10px; font-weight: 700; }
            #achievementPdfName { color: #e4eefb; font-size: 11px; }
            #achievementPdfHint { color: #aebbd8; font-size: 10px; }
            #pdfOpenButton, #pdfAddButton { background: #2b3c60; color: #ffffff; border: 1px solid #59719a; border-radius: 5px; padding: 5px 9px; }
            #pdfOpenButton:hover, #pdfAddButton:hover { background: #3a527d; }
            #pdfScroll, #pdfScroll::viewport, #pdfContent { background: transparent; border: 0; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)
        hint = QLabel("成果可以手动维护；从投稿记录移入的论文会完整保留投稿经历和关联文件。")
        hint.setObjectName("achievementDialogHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setVerticalSpacing(9)
        self.category_combo = QComboBox()
        self.category_combo.addItems(ACHIEVEMENT_CATEGORIES)
        form.addRow("类型", self.category_combo)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("论文、专利、奖项或其他成果名称")
        form.addRow("名称 *", self.title_edit)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())
        form.addRow("日期", self.date_edit)
        self.venue_edit = QLineEdit()
        self.venue_edit.setPlaceholderText("期刊、会议、授权机构或奖项名称（可选）")
        form.addRow("载体", self.venue_edit)
        self.identifier_edit = QLineEdit()
        self.identifier_edit.setPlaceholderText("DOI、专利号、证书号等（可选）")
        form.addRow("编号", self.identifier_edit)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("备注（可选）")
        self.notes_edit.setFixedHeight(76)
        form.addRow("说明", self.notes_edit)
        root.addLayout(form)

        pdf_heading = QHBoxLayout()
        pdf_label = QLabel("全文 PDF")
        pdf_label.setObjectName("sectionHeading")
        pdf_heading.addWidget(pdf_label)
        hint = QLabel("仅链接本地 PDF；更新画像会分段阅读全文，未变化文件复用缓存；扫描版请先 OCR")
        hint.setObjectName("achievementPdfHint")
        hint.setWordWrap(True)
        pdf_heading.addWidget(hint, 1)
        add_pdf = QPushButton("+ 链接 PDF")
        add_pdf.setObjectName("pdfAddButton")
        add_pdf.clicked.connect(self._add_pdf_files)
        pdf_heading.addWidget(add_pdf)
        root.addLayout(pdf_heading)
        self.pdf_scroll = QScrollArea()
        self.pdf_scroll.setObjectName("pdfScroll")
        self.pdf_scroll.setWidgetResizable(True)
        self.pdf_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.pdf_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pdf_scroll.setFixedHeight(62)
        self.pdf_content = QWidget()
        self.pdf_content.setObjectName("pdfContent")
        self.pdf_layout = QVBoxLayout(self.pdf_content)
        self.pdf_layout.setContentsMargins(0, 0, 4, 0)
        self.pdf_layout.setSpacing(4)
        self.pdf_scroll.setWidget(self.pdf_content)
        root.addWidget(self.pdf_scroll)
        if item and str(item.get("source", "")):
            source = QLabel("来源：" + str(item.get("source", "")))
            source.setObjectName("achievementDialogHint")
            root.addWidget(source)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        if item:
            self._fill(item)
        else:
            self._pdf_files: list[dict] = []
            self._render_pdf_files()

    def _fill(self, item: dict) -> None:
        self.category_combo.setCurrentText(str(item.get("category", "其他")))
        self.title_edit.setText(str(item.get("title", "")))
        parsed = QDate.fromString(str(item.get("date", "")), "yyyy-MM-dd")
        self.date_edit.setDate(parsed if parsed.isValid() else QDate.currentDate())
        self.venue_edit.setText(str(item.get("venue", "")))
        self.identifier_edit.setText(str(item.get("identifier", "")))
        self.notes_edit.setPlainText(str(item.get("notes", "")))
        self._pdf_files = _pdf_links(item)
        self._render_pdf_files()

    def _add_pdf_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "链接成果 PDF", "", "PDF 文件 (*.pdf)")
        existing = {str(item.get("path", "")).casefold() for item in self._pdf_files}
        for path in paths:
            key = str(path).casefold()
            if key in existing:
                continue
            existing.add(key)
            self._pdf_files.append({"id": uuid4().hex, "name": Path(path).name, "path": path, "kind": "file"})
        self._render_pdf_files()

    def _render_pdf_files(self) -> None:
        while self.pdf_layout.count():
            child = self.pdf_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        if not self._pdf_files:
            empty = QLabel("未关联 PDF。可随时链接本地全文；不会复制文件。")
            empty.setObjectName("achievementPdfHint")
            self.pdf_layout.addWidget(empty)
            return
        for item in self._pdf_files:
            row = AchievementPdfRow(item)
            row.open_requested.connect(self._open_pdf)
            row.remove_requested.connect(self._remove_pdf)
            self.pdf_layout.addWidget(row)

    def _remove_pdf(self, file_id: str) -> None:
        self._pdf_files = [item for item in self._pdf_files if str(item.get("id", "")) != file_id]
        self._render_pdf_files()

    def _open_pdf(self, path: str) -> None:
        target = Path(path)
        if not target.is_file():
            QMessageBox.warning(self, "文件不可用", "该 PDF 已不存在或已被移动。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _validate_and_accept(self) -> None:
        if not self.title_edit.text().strip():
            QMessageBox.warning(self, "信息不完整", "请填写成果名称。")
            return
        self.accept()

    def achievement(self) -> dict:
        return {
            "id": str(self._item.get("id") or uuid4().hex),
            "category": self.category_combo.currentText(),
            "title": self.title_edit.text().strip(),
            "date": self.date_edit.date().toString("yyyy-MM-dd"),
            "venue": self.venue_edit.text().strip(),
            "status": str(self._item.get("status", "")),
            "identifier": self.identifier_edit.text().strip(),
            "notes": self.notes_edit.toPlainText().strip(),
            "files": list(self._item.get("files", [])),
            "pdf_files": list(self._pdf_files),
            "source": str(self._item.get("source", "手动添加")) or "手动添加",
            "source_paper_id": str(self._item.get("source_paper_id", "")),
            "created_at": str(self._item.get("created_at", "")) or date.today().isoformat(),
            "paper": dict(self._item.get("paper", {})) if isinstance(self._item.get("paper"), dict) else {},
        }


class AchievementRow(QFrame):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    open_pdf_requested = Signal(str)

    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("achievementRow")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 10, 9)
        root.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(7)
        title = QLabel(str(item.get("title", "未命名成果")))
        title.setObjectName("achievementTitle")
        title.setWordWrap(True)
        top.addWidget(title, 1)
        category = QLabel(str(item.get("category", "其他")))
        category.setObjectName("achievementType")
        top.addWidget(category, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)
        meta_values = [str(item.get("venue", "")).strip(), str(item.get("date", "")).strip(), str(item.get("status", "")).strip()]
        meta_values = [value for value in meta_values if value]
        if meta_values:
            meta = QLabel(" · ".join(meta_values))
            meta.setObjectName("achievementMeta")
            meta.setWordWrap(True)
            root.addWidget(meta)
        notes = str(item.get("notes", "")).strip()
        if notes:
            note = QLabel(notes)
            note.setObjectName("achievementMeta")
            note.setWordWrap(True)
            root.addWidget(note)
        actions = QHBoxLayout()
        files = item.get("files", [])
        if isinstance(files, list) and files:
            attachment = QLabel(f"⌁ {len(files)} 个关联文件")
            attachment.setObjectName("achievementMeta")
            actions.addWidget(attachment)
        pdfs = _pdf_links(item)
        if pdfs:
            open_pdf = QPushButton(f"PDF {len(pdfs)}")
            open_pdf.setObjectName("rowButton")
            open_pdf.setToolTip("打开第一个关联 PDF；可在编辑中管理全部 PDF")
            open_pdf.clicked.connect(lambda: self.open_pdf_requested.emit(str(pdfs[0].get("path", ""))))
            actions.addWidget(open_pdf)
        actions.addStretch()
        edit = QPushButton("编辑")
        edit.setObjectName("rowButton")
        edit.clicked.connect(lambda: self.edit_requested.emit(str(item.get("id", ""))))
        actions.addWidget(edit)
        delete = QPushButton("删除")
        delete.setObjectName("dangerButton")
        delete.clicked.connect(lambda: self.delete_requested.emit(str(item.get("id", ""))))
        actions.addWidget(delete)
        actions.addWidget(OrderDragHandle(str(item.get("id", "")), "achievements"))
        root.addLayout(actions)


class AchievementsPage(QWidget):
    changed = Signal()
    profile_update_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.items = load_achievements()
        self._build_ui()
        self._render()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)
        heading = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("成果")
        title.setObjectName("paperPageTitle")
        subtitle = QLabel("论文、专利、奖项与其他成果；投稿接收或发表后会自动归入这里")
        subtitle.setObjectName("dateLabel")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box, 1)
        update_profile = QPushButton("↻ 更新研究画像")
        update_profile.setObjectName("subtleButton")
        update_profile.setToolTip("按成果、关联 PDF 与每日前沿偏好增量更新；无变化时不会调用 DeepSeek")
        update_profile.clicked.connect(self.profile_update_requested.emit)
        heading.addWidget(update_profile, alignment=Qt.AlignmentFlag.AlignTop)
        add = QPushButton("＋ 新增成果")
        add.setObjectName("primaryButton")
        add.clicked.connect(self._add)
        heading.addWidget(add, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(heading)
        controls = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索成果名称、载体或编号")
        self.search_edit.textChanged.connect(self._render)
        controls.addWidget(self.search_edit, 1)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["全部", *ACHIEVEMENT_CATEGORIES])
        self.filter_combo.currentTextChanged.connect(self._render)
        controls.addWidget(self.filter_combo)
        root.addLayout(controls)
        self.count_label = QLabel()
        self.count_label.setObjectName("sectionLabel")
        root.addWidget(self.count_label)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content = ReorderableColumn("achievements")
        self.content.setObjectName("achievementContent")
        self.content.order_changed.connect(self._reorder)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)

    def _filtered(self) -> list[dict]:
        keyword = self.search_edit.text().strip().casefold()
        category = self.filter_combo.currentText()
        result: list[dict] = []
        for item in self.items:
            if category != "全部" and str(item.get("category", "")) != category:
                continue
            haystack = " ".join(str(item.get(key, "")) for key in ("title", "venue", "identifier", "notes"))
            if keyword and keyword not in haystack.casefold():
                continue
            result.append(item)
        return result

    def _render(self) -> None:
        self.content.clear_rows()
        records = self._filtered()
        self.count_label.setText(f"共 {len(records)} 项成果")
        for item in records:
            row = AchievementRow(item)
            row.edit_requested.connect(self._edit)
            row.delete_requested.connect(self._delete)
            row.open_pdf_requested.connect(self._open_pdf)
            self.content.add_row(row, str(item.get("id", "")))
        if not records:
            empty = QLabel("还没有成果记录。论文在投稿记录中变为“已接收”或“已发表”时，会自动完整移入这里。")
            empty.setObjectName("emptyLabel")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.content.set_placeholder(empty)

    def _find_index(self, item_id: str) -> int:
        return next((index for index, item in enumerate(self.items) if str(item.get("id", "")) == item_id), -1)

    def _add(self) -> None:
        dialog = AchievementDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.items.insert(0, dialog.achievement())
            self._save()

    def _edit(self, item_id: str) -> None:
        index = self._find_index(item_id)
        if index < 0:
            return
        dialog = AchievementDialog(self, self.items[index])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.items[index] = dialog.achievement()
            self._save()

    def _delete(self, item_id: str) -> None:
        index = self._find_index(item_id)
        if index < 0:
            return
        item = dict(self.items[index])
        if not confirm_delete(self, "删除成果", f"“{item.get('title', '')}”将从成果板块移除。"):
            return
        del self.items[index]
        self._save()
        show_undo_toast(self, "已删除成果", lambda: self._restore(index, item))

    def _open_pdf(self, path: str) -> None:
        target = Path(path)
        if not target.is_file():
            QMessageBox.warning(self, "文件不可用", "该 PDF 已不存在或已被移动。可在成果“编辑”中重新链接。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _restore(self, index: int, item: dict) -> None:
        if any(str(entry.get("id", "")) == str(item.get("id", "")) for entry in self.items):
            return
        self.items.insert(min(index, len(self.items)), item)
        self._save()

    def _reorder(self, ordered_ids: list[str]) -> None:
        ordered = {item_id: index for index, item_id in enumerate(ordered_ids)}
        self.items.sort(key=lambda item: ordered.get(str(item.get("id", "")), len(ordered)))
        self._save()

    def _save(self) -> None:
        save_achievements(self.items)
        self._render()
        self.changed.emit()

    def reload(self) -> None:
        self.items = load_achievements()
        self._render()
