from __future__ import annotations

from datetime import date
from uuid import uuid4

from PySide6.QtCore import QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ui.dialogs import confirm_delete, show_undo_toast
from ui.ai_progress import AiProgressPanel
from ui.journal_selection_dialog import JournalSelectionDialog
from ui.reorder import OrderDragHandle, ReorderableColumn
from ui.theme import apply_dialog_theme
from utils.ai_service import (
    DeepSeekConfigurationError,
    DeepSeekRequestError,
    enrich_journals_with_ai,
    get_ai_settings,
    is_deepseek_ready,
    journal_ai_source_signature,
    journal_needs_ai_jcr_estimate,
    journal_needs_ai_enrichment,
    rank_journals_with_ai,
)
from utils.easyscholar_service import (
    EasyScholarConfigurationError,
    EasyScholarRequestError,
    enrich_journals_with_easyscholar,
    get_easyscholar_settings,
    is_easyscholar_ready,
    journal_needs_easyscholar_update,
)
from utils.file_manager import (
    journal_usage_index,
    load_app_settings,
    load_frontier_data,
    load_journal_library,
    load_journal_selection_feedback,
    load_papers,
    set_journal_selection_feedback,
    save_papers,
    save_app_settings,
    save_journal_library,
    sync_journal_library_from_papers,
)
from utils.journal_quality import compact_cas_quartile, compact_metric_line, journal_quality_snapshot
from utils.journal_catalog import merge_land_science_catalog
from utils.journal_health_service import (
    journal_health_status,
    journal_health_targets,
    journal_row_model,
    merge_journal_health_patch,
    publisher_group_key,
)
from utils.journal_selection_service import append_journal_to_submission_path, external_candidate_to_library_journal
from utils.journal_service import enrich_journal_library, journal_needs_metadata_enrichment
from utils.jcr_service import (
    JcrConfigurationError,
    JcrRequestError,
    is_jcr_ready,
    jcr_label,
    primary_jcr_quartile,
    verify_journal_library,
)


def _journal_key(name: str, publisher: str = "") -> str:
    return f"{name.strip().casefold()}|{publisher.strip().casefold()}"


class ElidedLabel(QLabel):
    """A one-line metadata label that preserves the full value in a tooltip."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._refresh_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self) -> None:
        width = max(1, self.contentsRect().width())
        QLabel.setText(self, QFontMetrics(self.font()).elidedText(self._full_text, Qt.TextElideMode.ElideRight, width))


class JournalMetadataThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, journals: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._journals = [dict(item) for item in journals]

    def run(self) -> None:
        try:
            self.completed.emit(enrich_journal_library(self._journals))
        except Exception as error:
            self.failed.emit(str(error))


class JournalJcrThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, journals: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._journals = [dict(item) for item in journals]

    def run(self) -> None:
        try:
            self.completed.emit(verify_journal_library(self._journals))
        except (JcrConfigurationError, JcrRequestError) as error:
            self.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001 - keep failures inside the worker thread
            self.failed.emit(str(error))


class JournalEasyScholarThread(QThread):
    """Fetch optional journal indicators off the UI thread."""

    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, journals: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._journals = [dict(item) for item in journals]

    def run(self) -> None:
        try:
            self.completed.emit(enrich_journals_with_easyscholar(self._journals))
        except (EasyScholarConfigurationError, EasyScholarRequestError) as error:
            self.failed.emit(str(error))
        except Exception:  # noqa: BLE001 - never expose an optional-provider traceback in the UI
            self.failed.emit("EasyScholar 数据未更新，请稍后重试。")


class JournalAiEnrichmentThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, journals: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._journals = [dict(item) for item in journals]

    def run(self) -> None:
        try:
            self.completed.emit(
                enrich_journals_with_ai(
                    self._journals,
                    lambda message, value=0: self.progress.emit(str(message), int(value or 0)),
                )
            )
        except (DeepSeekConfigurationError, DeepSeekRequestError) as error:
            self.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001 - keep UI responsive on unexpected provider failures
            self.failed.emit(str(error))


class JournalPickerAiThread(QThread):
    completed = Signal(dict)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(self, paper: dict, journals: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._paper = dict(paper)
        self._journals = [dict(item) for item in journals]

    def run(self) -> None:
        try:
            self.completed.emit(
                rank_journals_with_ai(
                    self._paper,
                    self._journals,
                    progress=lambda message, value=0: self.progress.emit(str(message), int(value or 0)),
                )
            )
        except (DeepSeekConfigurationError, DeepSeekRequestError) as error:
            self.failed.emit(str(error))
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error))


class JournalLibraryDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, journal: dict | None = None) -> None:
        super().__init__(parent)
        self._journal = journal or {}
        self._jcr_edited = False
        self.delete_requested = False
        self.setWindowTitle("编辑期刊" if journal else "添加期刊")
        parent_width = parent.width() if parent else 520
        parent_height = parent.height() if parent else 700
        self.setMinimumWidth(350)
        self.setMaximumWidth(max(350, min(540, parent_width - 24)))
        self.setMinimumHeight(460)
        self.resize(
            max(350, min(470, parent_width - 24)),
            max(480, min(640, parent_height - 24)),
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(11)
        scroll = QScrollArea()
        scroll.setObjectName("journalEditorScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("journalEditor")
        content_root = QVBoxLayout(content)
        content_root.setContentsMargins(0, 0, 5, 0)
        content_root.setSpacing(0)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：CATENA")
        form.addRow("期刊名称 *", self.name_edit)
        self.publisher_edit = QLineEdit()
        self.publisher_edit.setPlaceholderText("例如：Elsevier")
        form.addRow("出版社", self.publisher_edit)
        self.issn_edit = QLineEdit()
        self.issn_edit.setPlaceholderText("可由“联网补全”自动填写")
        form.addRow("ISSN", self.issn_edit)
        jcr_row = QHBoxLayout()
        jcr_row.setContentsMargins(0, 0, 0, 0)
        jcr_row.setSpacing(7)
        self.jcr_quartile = QComboBox()
        self.jcr_quartile.addItem("未填写", "")
        for quartile in ("Q1", "Q2", "Q3", "Q4"):
            self.jcr_quartile.addItem(quartile, quartile)
        self.jcr_quartile.setToolTip("可手动记录 JCR 分区；Clarivate 核验结果会保留独立来源标记。")
        jcr_row.addWidget(self.jcr_quartile, 1)
        self.jcr_year_edit = QLineEdit()
        self.jcr_year_edit.setPlaceholderText("年份")
        self.jcr_year_edit.setMaximumWidth(82)
        self.jcr_year_edit.setToolTip("可选，例如 2025")
        jcr_row.addWidget(self.jcr_year_edit)
        form.addRow("JCR 分区", jcr_row)
        self.jcr_source_hint = QLabel()
        self.jcr_source_hint.setObjectName("dialogHint")
        self.jcr_source_hint.setWordWrap(True)
        form.addRow("分区来源", self.jcr_source_hint)
        self.easyscholar_hint = QLabel()
        self.easyscholar_hint.setObjectName("dialogHint")
        self.easyscholar_hint.setWordWrap(True)
        self.easyscholar_hint.setToolTip("联网同步的数据仅作查询参考；手动填写 JCR 分区不会覆盖这些原始指标。")
        form.addRow("期刊指标", self.easyscholar_hint)
        self.fields_edit = QLineEdit()
        self.fields_edit.setPlaceholderText("例如：土壤碳、遥感、机器学习（用逗号分隔）")
        form.addRow("方向标签", self.fields_edit)
        self.website_edit = QLineEdit()
        self.website_edit.setPlaceholderText("期刊官网或投稿系统链接（可选）")
        form.addRow("官网链接", self.website_edit)
        self.favorite = QCheckBox("收藏，优先显示")
        form.addRow("个人标记", self.favorite)
        self.frontier_priority = QComboBox()
        self.frontier_priority.addItems(["不订阅", "必看", "关注", "扩展"])
        self.frontier_priority.setToolTip("每日前沿会优先检索“必看”和“关注”的期刊")
        form.addRow("每日前沿", self.frontier_priority)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("记录选刊经验、审稿速度、创新要求、适合的研究方向等")
        self.notes_edit.setFixedHeight(84)
        form.addRow("个人经验", self.notes_edit)
        content_root.addLayout(form)
        content_root.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        button_row = QHBoxLayout()
        if journal:
            delete = QPushButton("删除期刊")
            delete.setObjectName("dangerButton")
            delete.clicked.connect(self._request_delete)
            button_row.addWidget(delete)
        button_row.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        button_row.addWidget(buttons)
        root.addLayout(button_row)
        if journal:
            self._fill(journal)
        else:
            self._update_jcr_source_hint()
            self._update_easyscholar_hint()
        self.jcr_quartile.currentIndexChanged.connect(self._mark_jcr_edited)
        self.jcr_year_edit.textEdited.connect(self._mark_jcr_edited)
        # Editors inherit the current theme instead of repainting a separate
        # legacy navy stylesheet whenever the user opens or saves settings.
        apply_dialog_theme(self)

    def _fill(self, journal: dict) -> None:
        self.name_edit.setText(str(journal.get("name", "")))
        self.publisher_edit.setText(str(journal.get("publisher", "")))
        self.issn_edit.setText(str(journal.get("issn", "")))
        quartile = primary_jcr_quartile(journal)
        self.jcr_quartile.setCurrentIndex(max(0, self.jcr_quartile.findData(quartile)))
        jcr = journal.get("jcr", {})
        metrics = jcr.get("metrics", []) if isinstance(jcr, dict) else []
        year = next(
            (str(metric.get("year", "")) for metric in metrics if isinstance(metric, dict) and metric.get("year")),
            "",
        )
        self.jcr_year_edit.setText(year)
        self._update_jcr_source_hint()
        self._update_easyscholar_hint()
        self.fields_edit.setText(", ".join(str(item) for item in journal.get("fields", []) if str(item).strip()))
        self.website_edit.setText(str(journal.get("website", "")))
        self.favorite.setChecked(bool(journal.get("favorite", False)))
        self.frontier_priority.setCurrentText(str(journal.get("frontier_priority", "不订阅")))
        self.notes_edit.setPlainText(str(journal.get("notes", "")))

    def _mark_jcr_edited(self, *_args) -> None:
        self._jcr_edited = True
        self.jcr_source_hint.setText("保存后将标记为“手动记录”；可继续用 Clarivate 官方接口核验。")

    def _update_jcr_source_hint(self) -> None:
        jcr = self._journal.get("jcr", {})
        jcr = jcr if isinstance(jcr, dict) else {}
        status = str(jcr.get("status", "pending")).strip()
        source = str(jcr.get("source", "")).strip()
        note = str(jcr.get("note", "")).strip()
        confidence = str(jcr.get("confidence", "")).strip()
        if status == "verified":
            if "easyscholar" in source.casefold():
                quartile = primary_jcr_quartile(self._journal)
                text = f"EasyScholar 已同步{f' {quartile}' if quartile else ' JCR 分区'}。"
            else:
                text = f"已由 Clarivate 核验{f' · {source}' if source else ''}。"
        elif status == "ai_estimated":
            text = "DeepSeek AI 估计（待 Clarivate 核验）"
            if confidence:
                text += f" · 置信度{confidence}"
            if note:
                text += f"\n{note}"
        elif status == "manual":
            text = f"手动记录{f' · {source}' if source else ''}。"
        elif status == "not_found":
            text = "官方核验暂未收录；可手动填写或稍后再次核验。"
        else:
            text = "未填写。可手动录入，或在期刊库的“工具”中让 DeepSeek 给出待核验的 AI 估计。"
        self.jcr_source_hint.setText(text)

    def _update_easyscholar_hint(self) -> None:
        """Expose synchronized CAS / impact metrics without making them editable claims."""
        metrics = self._journal.get("easyscholar", {})
        metrics = metrics if isinstance(metrics, dict) else {}
        parts: list[str] = []
        if metrics.get("cas_upgrade"):
            parts.append(f"中科院升级版 {metrics['cas_upgrade']}")
        if metrics.get("cas_upgrade_top"):
            parts.append(f"升级版 Top {metrics['cas_upgrade_top']}")
        if metrics.get("cas_upgrade_small"):
            parts.append(f"升级版小类 {metrics['cas_upgrade_small']}")
        if metrics.get("cas_basic"):
            parts.append(f"基础版 {metrics['cas_basic']}")
        if metrics.get("impact_factor"):
            parts.append(f"影响因子 {metrics['impact_factor']}")
        if metrics.get("impact_factor_5y"):
            parts.append(f"5 年 IF {metrics['impact_factor_5y']}")
        if metrics.get("jci"):
            parts.append(f"JCI {metrics['jci']}")
        if metrics.get("ei"):
            parts.append("EI")
        if metrics.get("esci"):
            parts.append("ESCI")
        if parts:
            source = str(metrics.get("source", "EasyScholar")).strip() or "EasyScholar"
            checked_at = str(metrics.get("checked_at", "")).strip()
            suffix = f" · {checked_at}" if checked_at else ""
            self.easyscholar_hint.setText(f"{source}：" + " · ".join(parts) + suffix)
        else:
            self.easyscholar_hint.setText("尚未同步期刊指标。可在期刊库 → 工具中通过 EasyScholar 更新。")

    def _validate_and_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "信息不完整", "请填写期刊名称。")
            return
        self.accept()

    def _request_delete(self) -> None:
        name = str(self._journal.get("name", "这本期刊"))
        if confirm_delete(self, "删除期刊", f"“{name}”将从期刊库中移除。"):
            self.delete_requested = True
            self.accept()

    def journal(self) -> dict:
        fields = [item.strip() for item in self.fields_edit.text().replace("，", ",").split(",") if item.strip()]
        jcr = dict(self._journal.get("jcr", {}))
        if self._jcr_edited:
            quartile = str(self.jcr_quartile.currentData() or "")
            try:
                year = int(self.jcr_year_edit.text().strip())
            except ValueError:
                year = 0
            jcr = (
                {
                    "status": "manual",
                    "source": "手动记录",
                    "checked_at": date.today().isoformat(),
                    "metrics": [{"quartile": quartile, "year": year, "category": "", "jif": "", "rank": "", "total": ""}],
                }
                if quartile
                else {"status": "pending", "source": "", "checked_at": "", "metrics": []}
            )
        # Retain provider metadata and future fields when the user edits a
        # simple field such as a publisher.  Losing EasyScholar's cache stamp
        # would cause unnecessary paid API refreshes on the next launch.
        result = dict(self._journal)
        result.update(
            {
            "id": str(self._journal.get("id") or uuid4().hex),
            "name": self.name_edit.text().strip(),
            "publisher": self.publisher_edit.text().strip(),
            "issn": self.issn_edit.text().strip(),
            "fields": fields,
            "ai_tags": list(self._journal.get("ai_tags", [])),
            "website": self.website_edit.text().strip(),
            "notes": self.notes_edit.toPlainText().strip(),
            "ai_scope_cn": str(self._journal.get("ai_scope_cn", "")),
            "ai_fit_cn": str(self._journal.get("ai_fit_cn", "")),
            "ai_risks_cn": str(self._journal.get("ai_risks_cn", "")),
            "ai_model": str(self._journal.get("ai_model", "")),
            "ai_updated_at": str(self._journal.get("ai_updated_at", "")),
            "ai_source_signature": str(self._journal.get("ai_source_signature", "")),
            "ai_auto_pending": bool(self._journal.get("ai_auto_pending", False)),
            "jcr": jcr,
            "favorite": self.favorite.isChecked(),
            "frontier_priority": self.frontier_priority.currentText(),
            "metadata_updated_at": str(self._journal.get("metadata_updated_at", "")),
            "metadata_source_signature": str(self._journal.get("metadata_source_signature", "")),
            "created_at": str(self._journal.get("created_at", "")),
            "last_used_at": str(self._journal.get("last_used_at", "")),
            }
        )
        return result


class JournalPickerRow(QFrame):
    selected = Signal(str)
    feedback_requested = Signal(str, str)

    def __init__(
        self,
        journal: dict,
        score: int,
        matches: list[str],
        usage: dict,
        score_reason: str,
        ai_result: dict | None = None,
        feedback_label: str = "",
        rank_index: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pickerJournalRow")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(7)
        if rank_index:
            index = QLabel(f"#{rank_index}")
            index.setObjectName("pickerRank")
            top.addWidget(index, alignment=Qt.AlignmentFlag.AlignTop)
        name = QLabel(str(journal.get("name", "未命名期刊")))
        name.setObjectName("cardTitle")
        name.setWordWrap(True)
        top.addWidget(name, 1)
        rank = QLabel(f"AI {int(ai_result.get('score', 0))}" if ai_result else f"{score} 分")
        rank.setObjectName("journalScore")
        top.addWidget(rank, alignment=Qt.AlignmentFlag.AlignTop)
        choose = QPushButton("选为目标")
        choose.setObjectName("subtleButton")
        choose.clicked.connect(lambda: self.selected.emit(str(journal.get("id", ""))))
        top.addWidget(choose, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(top)

        publisher = str(journal.get("publisher", "")).strip() or "未填写出版社"
        tags = " · ".join(
            str(item) for item in [*journal.get("fields", []), *journal.get("ai_tags", [])] if str(item).strip()
        )
        jcr = jcr_label(journal)
        quality = journal_quality_snapshot(journal)
        metric_line = compact_metric_line(journal)
        meta = QLabel(publisher + (f"  ·  {jcr}" if jcr else "") + (f"  ·  {metric_line}" if metric_line else "") + (f"  ·  {tags}" if tags else ""))
        meta.setObjectName("cardDetail")
        meta.setWordWrap(True)
        meta.setToolTip(" · ".join(part for part in (quality.get("source", ""), quality.get("checked_at", ""), metric_line) if str(part).strip()) or "期刊指标尚未同步")
        root.addWidget(meta)
        if matches:
            match_label = QLabel("匹配：" + " / ".join(matches))
            match_label.setObjectName("journalUsage")
            root.addWidget(match_label)
        reason = QLabel("本地依据：" + score_reason)
        reason.setObjectName("pickerHint")
        reason.setWordWrap(True)
        root.addWidget(reason)
        if feedback_label:
            feedback = QLabel(f"你的反馈：{feedback_label}")
            feedback.setObjectName("journalUsage")
            root.addWidget(feedback)
        if ai_result:
            ai_reason = str(ai_result.get("reason_cn", "")).strip()
            ai_risk = str(ai_result.get("risk_cn", "")).strip()
            ai_text = f"AI 复核 {int(ai_result.get('score', 0))} 分"
            if ai_reason:
                ai_text += "：" + ai_reason
            if ai_risk:
                ai_text += "　风险：" + ai_risk
            ai_label = QLabel(ai_text)
            ai_label.setObjectName("pickerHint")
            ai_label.setWordWrap(True)
            root.addWidget(ai_label)
        if int(usage.get("submission_count", 0)):
            experience = QLabel(
                f"个人记录：投稿 {usage.get('submission_count', 0)} 次 · 最近 {usage.get('last_used_at', '—') or '—'}"
            )
            experience.setObjectName("journalUsage")
            experience.setWordWrap(True)
            root.addWidget(experience)
        feedback_row = QHBoxLayout()
        feedback_row.addWidget(QLabel("对本论文："))
        for label in ("适合", "暂不考虑", "不适合"):
            button = QPushButton(label)
            button.setObjectName("dangerButton" if label == "不适合" else "rowButton")
            button.setToolTip("这会影响这篇论文的后续排序，并轻微学习对应方向偏好")
            button.clicked.connect(lambda _checked=False, value=label: self.feedback_requested.emit(str(journal.get("id", "")), value))
            feedback_row.addWidget(button)
        feedback_row.addStretch()
        root.addLayout(feedback_row)


class JournalPickerDialog(QDialog):
    """Turn the library into a lightweight, local journal selection assistant."""

    def __init__(self, papers: list[dict], journals: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.papers = papers
        self.journals = journals
        self.usage = journal_usage_index(papers)
        self.feedback = load_journal_selection_feedback()
        self._selected_journal_id = ""
        self._ai_results: dict[str, dict] = {}
        self._ai_worker: JournalPickerAiThread | None = None
        self.setWindowTitle("为论文选刊")
        parent_width = parent.width() if parent else 560
        self.setMinimumWidth(390)
        self.setMaximumWidth(max(390, min(720, parent_width - 16)))
        self.resize(max(390, min(620, parent_width - 16)), 620)
        self.setStyleSheet(
            """
            QDialog { background: #111b2d; color: #edf4ff; }
            QLabel { color: #edf4ff; }
            #pickerTitle { color: #ffffff; font-size: 19px; font-weight: 700; }
            #pickerHint { color: #9aacc6; font-size: 11px; }
            QLineEdit, QComboBox { background: #17263d; color: #f2f7ff; border: 1px solid #38506f; border-radius: 7px; padding: 7px 9px; }
            QComboBox QAbstractItemView { background: #17263d; color: #ffffff; selection-background-color: #31577f; selection-color: #ffffff; border: 1px solid #70c9ff; outline: 0; }
            QComboBox QAbstractItemView::item { min-height: 24px; padding: 5px 10px; color: #ffffff; }
            QComboBox QAbstractItemView::item:selected { background: #31577f; color: #ffffff; }
            #pickerJournalRow { background: #182842; border: 1px solid #2e4666; border-radius: 8px; }
            #journalScore { color: #8ad7ff; background: rgba(58,156,221,42); border-radius: 8px; padding: 3px 6px; font-size: 10px; }
            #pickerRank { color: #8aa4c7; font-size: 11px; font-weight: 700; padding-top: 2px; }
            #subtleButton { background: #273a57; color: #e8f2ff; border: 1px solid #3d587e; border-radius: 7px; padding: 6px 8px; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(9)
        title = QLabel("为论文选刊")
        title.setObjectName("pickerTitle")
        root.addWidget(title)
        hint = QLabel("先用可解释的本地分筛选，再由 AI 复核候选。分数用于比较适配度，不代表录用概率。")
        hint.setObjectName("pickerHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.paper_combo = QComboBox()
        for paper in papers:
            self.paper_combo.addItem(str(paper.get("title", "未命名论文")), str(paper.get("id", "")))
        self.paper_combo.currentIndexChanged.connect(self._sync_keywords)
        root.addWidget(self.paper_combo)
        self.keywords_edit = QLineEdit()
        self.keywords_edit.setPlaceholderText("论文关键词，例如：SOC、遥感、机器学习")
        self.keywords_edit.textChanged.connect(self._render)
        root.addWidget(self.keywords_edit)
        self.summary_label = QLabel()
        self.summary_label.setObjectName("pickerHint")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("候选范围"))
        self.candidate_filter = QComboBox()
        self.candidate_filter.addItems(["全部候选", "仅优先期刊", "仅 JCR 已核验", "显示不适合期刊"])
        self.candidate_filter.currentTextChanged.connect(self._render)
        filter_row.addWidget(self.candidate_filter, 1)
        root.addLayout(filter_row)
        self.quality_hint = QLabel()
        self.quality_hint.setObjectName("pickerHint")
        self.quality_hint.setWordWrap(True)
        root.addWidget(self.quality_hint)
        ai_row = QHBoxLayout()
        self.ai_hint = QLabel("可对本地筛选出的候选期刊进行一次 DeepSeek 适配复核。")
        self.ai_hint.setObjectName("pickerHint")
        self.ai_hint.setWordWrap(True)
        ai_row.addWidget(self.ai_hint, 1)
        self.ai_button = QPushButton("AI 复核")
        self.ai_button.setObjectName("subtleButton")
        self.ai_button.setToolTip("发送当前论文摘要和本地前 20 个候选期刊至 DeepSeek 重新排序")
        self.ai_button.clicked.connect(self._run_ai_rerank)
        ai_row.addWidget(self.ai_button)
        root.addLayout(ai_row)
        self.ai_progress = AiProgressPanel(object_name="journalPickerAiProgress")
        root.addWidget(self.ai_progress)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.rows = QVBoxLayout(self.content)
        self.rows.setContentsMargins(1, 1, 5, 1)
        self.rows.setSpacing(6)
        self.rows.addStretch()
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)
        cancel = QPushButton("取消")
        cancel.setObjectName("subtleButton")
        cancel.clicked.connect(self.reject)
        root.addWidget(cancel, alignment=Qt.AlignmentFlag.AlignRight)
        self._sync_keywords()

    def _current_paper(self) -> dict | None:
        paper_id = str(self.paper_combo.currentData())
        return next((item for item in self.papers if str(item.get("id")) == paper_id), None)

    def _sync_keywords(self) -> None:
        paper = self._current_paper()
        if paper:
            self.keywords_edit.blockSignals(True)
            self.keywords_edit.setText(", ".join(str(item) for item in paper.get("keywords", []) if str(item).strip()))
            self.keywords_edit.blockSignals(False)
            summary = str(paper.get("summary", "")).strip()
            self.summary_label.setText(
                "研究摘要：" + (summary[:180] + ("…" if len(summary) > 180 else ""))
                if summary
                else "研究摘要尚未填写：可在论文编辑中补充，或使用“AI 补全”生成草稿。"
            )
        else:
            self.summary_label.setText("")
        self._render()

    def _keyword_terms(self) -> list[str]:
        return [item.strip().casefold() for item in self.keywords_edit.text().replace("，", ",").split(",") if item.strip()]

    def _feedback_label(self, journal: dict) -> str:
        paper = self._current_paper()
        if paper is None:
            return ""
        key = f"{paper.get('id', '')}:{journal.get('id', '')}"
        entry = self.feedback.get("entries", {}).get(key, {})
        return str(entry.get("label", "")) if isinstance(entry, dict) else ""

    def _score(self, journal: dict) -> tuple[int, list[str], str]:
        words = self._keyword_terms()
        searchable = [
            *[str(item).strip() for item in journal.get("fields", []) if str(item).strip()],
            *[str(item).strip() for item in journal.get("ai_tags", []) if str(item).strip()],
            str(journal.get("ai_scope_cn", "")).strip(),
            str(journal.get("ai_fit_cn", "")).strip(),
            str(journal.get("notes", "")).strip(),
        ]
        haystack = " ".join(searchable).casefold()
        matches = [word for word in words if word in haystack]
        priority = str(journal.get("frontier_priority", "不订阅"))
        priority_score = {"必看": 100, "关注": 70, "扩展": 30, "不订阅": 0}.get(priority, 0)
        field_score = min(80, len(matches) * 25)
        key = _journal_key(str(journal.get("name", "")), str(journal.get("publisher", "")))
        usage = self.usage.get(key, {"submission_count": 0, "last_used_at": ""})
        experience_score = min(int(usage.get("submission_count", 0)), 5) * 4
        jcr = journal.get("jcr", {})
        jcr = jcr if isinstance(jcr, dict) else {}
        quartile = primary_jcr_quartile(journal) if jcr.get("status") == "verified" else ""
        jcr_score = {"Q1": 20, "Q2": 14, "Q3": 8, "Q4": 4}.get(quartile, 0)
        feedback_label = self._feedback_label(journal)
        direct_feedback_score = {"适合": 22, "暂不考虑": -8, "不适合": -70}.get(feedback_label, 0)
        weights = self.feedback.get("term_weights", {}) if isinstance(self.feedback.get("term_weights", {}), dict) else {}
        learned_score = max(-30, min(30, sum(int(weights.get(word, 0) or 0) for word in matches)))
        score = priority_score + field_score + experience_score + jcr_score + direct_feedback_score + learned_score
        parts = []
        if priority_score:
            parts.append(f"{priority} +{priority_score}")
        if field_score:
            parts.append(f"关键词 +{field_score}")
        if experience_score:
            parts.append(f"个人经历 +{experience_score}")
        if jcr_score:
            parts.append(f"JCR {quartile} +{jcr_score}")
        if direct_feedback_score:
            parts.append(f"你的反馈 {direct_feedback_score:+d}")
        if learned_score:
            parts.append(f"学习偏好 {learned_score:+d}")
        return score, matches, " + ".join(parts) if parts else "待补充方向或优先级"

    def _ranked(self) -> list[tuple[dict, int, list[str], str]]:
        scope = self.candidate_filter.currentText() if hasattr(self, "candidate_filter") else "全部候选"
        candidates: list[dict] = []
        for journal in self.journals:
            feedback_label = self._feedback_label(journal)
            if scope == "仅优先期刊" and str(journal.get("frontier_priority", "")) not in {"必看", "关注"}:
                continue
            jcr = journal.get("jcr", {})
            jcr = jcr if isinstance(jcr, dict) else {}
            if scope == "仅 JCR 已核验" and str(jcr.get("status", "")) != "verified":
                continue
            if scope == "显示不适合期刊":
                if feedback_label != "不适合":
                    continue
            elif feedback_label == "不适合":
                continue
            candidates.append(journal)
        ranked = [(journal, *self._score(journal)) for journal in candidates]
        ranked.sort(
            key=lambda item: (
                -int(str(item[0].get("id", "")) in self._ai_results),
                -int(self._ai_results.get(str(item[0].get("id", "")), {}).get("score", -1)),
                -item[1],
                str(item[0].get("name", "")).casefold(),
            )
        )
        return ranked

    def _render(self) -> None:
        while self.rows.count() > 1:
            child = self.rows.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        ranked = self._ranked()
        paper = self._current_paper()
        self.quality_hint.setText(
            "提示：这篇论文还没有关键词，排序会更多依赖优先期刊、JCR 与个人经历；建议先补充关键词。"
            if paper and not paper.get("keywords")
            else "评分说明：优先期刊、关键词匹配、已核验 JCR、投稿经历及你的反馈共同决定本地排序。"
        )
        for rank_index, (journal, score, matches, reason) in enumerate(ranked, 1):
            usage = self.usage.get(
                _journal_key(str(journal.get("name", "")), str(journal.get("publisher", ""))),
                {"submission_count": 0, "last_used_at": ""},
            )
            row = JournalPickerRow(
                journal,
                score,
                matches,
                usage,
                reason,
                self._ai_results.get(str(journal.get("id", ""))),
                self._feedback_label(journal),
                rank_index,
            )
            row.selected.connect(self._select)
            row.feedback_requested.connect(self._record_feedback)
            self.rows.insertWidget(self.rows.count() - 1, row)
        if not ranked:
            empty = QLabel("期刊库为空，先添加一条期刊记录。")
            empty.setObjectName("pickerHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rows.insertWidget(0, empty)

    def _run_ai_rerank(self) -> None:
        if self._ai_worker is not None and self._ai_worker.isRunning():
            return
        self.ai_progress.begin("正在检查 AI 配置…")
        self.ai_button.setEnabled(False)
        self.ai_button.setText("AI 复核中…")
        if not is_deepseek_ready("journal_recommendation"):
            self.ai_hint.setText("尚未配置 DeepSeek。请在“设置 → 智能增强与 JCR”填写 API Key 后再试。")
            self.ai_progress.fail("AI 选刊复核未配置，未开始处理。")
            self.ai_button.setEnabled(True)
            self.ai_button.setText("AI 复核")
            return
        paper = self._current_paper()
        if paper is None:
            self.ai_hint.setText("请先选择一篇论文。")
            self.ai_progress.fail("请先选择一篇论文。")
            self.ai_button.setEnabled(False)
            self.ai_button.setText("AI 复核")
            return
        shortlist = [item[0] for item in self._ranked()[:20]]
        self.ai_button.setEnabled(False)
        self.ai_progress.update("DeepSeek 正在复核候选期刊…", 5)
        self.ai_hint.setText("DeepSeek 正在复核候选期刊…")
        self._ai_worker = JournalPickerAiThread(paper, shortlist, self)
        self._ai_worker.progress.connect(self._ai_rerank_progress)
        self._ai_worker.completed.connect(self._ai_rerank_finished)
        self._ai_worker.failed.connect(self._ai_rerank_failed)
        self._ai_worker.finished.connect(self._clear_ai_worker)
        self._ai_worker.start()

    def _ai_rerank_finished(self, result: dict) -> None:
        ranked = result.get("ranked", [])
        self._ai_results = {
            str(item.get("id", "")): dict(item)
            for item in ranked
            if isinstance(item, dict) and str(item.get("id", ""))
        }
        self.ai_hint.setText(f"DeepSeek 已复核 {len(self._ai_results)} 本候选期刊；AI 分数优先、本地分数用于并列排序。")
        self.ai_progress.complete("AI 期刊复核完成。")
        self._render()

    def _ai_rerank_failed(self, message: str) -> None:
        self.ai_hint.setText("AI 复核失败：" + str(message))
        self.ai_progress.fail("AI 复核失败：" + str(message))

    def _ai_rerank_progress(self, message: str, value: int) -> None:
        self.ai_hint.setText(str(message))
        self.ai_progress.update(str(message), int(value))

    def _clear_ai_worker(self) -> None:
        if self._ai_worker is not None:
            self._ai_worker.deleteLater()
        self._ai_worker = None
        self.ai_button.setEnabled(True)
        self.ai_button.setText("AI 复核")

    def _select(self, journal_id: str) -> None:
        # Choosing a target journal is the clearest positive signal.  Store it
        # before closing so the next opening already reflects the preference.
        self._record_feedback(journal_id, "适合", refresh=False)
        self._selected_journal_id = journal_id
        self.accept()

    def _record_feedback(self, journal_id: str, label: str, refresh: bool = True) -> None:
        paper = self._current_paper()
        journal = next((item for item in self.journals if str(item.get("id", "")) == journal_id), None)
        if paper is None or journal is None:
            return
        _score, matches, _reason = self._score(journal)
        try:
            set_journal_selection_feedback(str(paper.get("id", "")), journal_id, label, matches)
        except ValueError:
            return
        self.feedback = load_journal_selection_feedback()
        self.ai_hint.setText(f"已记录“{label}”反馈；后续会在这篇论文及相近关键词的选刊中体现。")
        if refresh:
            self._render()

    def selection(self) -> tuple[str, str]:
        return str(self.paper_combo.currentData()), self._selected_journal_id


class CompactJournalRow(QFrame):
    """One dense row: name stays dominant; infrequent actions live in a menu."""

    edit_requested = Signal(str)
    delete_requested = Signal(str)
    favorite_requested = Signal(str)
    metadata_requested = Signal(str)

    def __init__(self, journal: dict, parent: QWidget | None = None, *, movable: bool = False) -> None:
        super().__init__(parent)
        self.journal = journal
        self.setObjectName("journalLibraryRow")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._name_label: QLabel | None = None
        self._publisher_label: ElidedLabel | None = None
        self._tag_label: ElidedLabel | None = None
        self._compact_columns: bool | None = None
        model = journal_row_model(journal)
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 6, 8, 6)
        root.setSpacing(5)
        name = QLabel(("★ " if journal.get("favorite") else "") + model["name"])
        name.setObjectName("journalCopyName")
        name.setWordWrap(True)
        # A journal title is deliberately allowed to wrap in the small
        # widget.  A large construction-time minimum width would otherwise
        # force a horizontal overflow before the row ever receives its real
        # viewport size.
        name.setMinimumWidth(76)
        name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        name.setToolTip(model["name"])
        name.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        name.setCursor(Qt.CursorShape.IBeamCursor)
        self._name_label = name
        root.addWidget(name, 6)
        publisher = ElidedLabel(model["publisher"])
        publisher.setObjectName("journalPublisher")
        publisher.setMinimumWidth(0)
        publisher.setMaximumWidth(115)
        self._publisher_label = publisher
        root.addWidget(publisher, 2)
        tags = ElidedLabel(model["tags"])
        tags.setObjectName("journalTags")
        tags.setMinimumWidth(0)
        tags.setMaximumWidth(125)
        self._tag_label = tags
        root.addWidget(tags, 2)

        publisher_validation = journal.get("publisher_validation", {})
        publisher_validation = publisher_validation if isinstance(publisher_validation, dict) else {}
        publisher_status = str(publisher_validation.get("status", "")).strip().casefold()
        if not str(journal.get("publisher", "")).strip():
            publisher_mark = "—"
            publisher_status = "unknown"
            publisher_tip = "未填写出版社，暂不进行模糊校验。"
        else:
            publisher_mark = {"match": "✓", "probable": "≈", "mismatch": "!"}.get(publisher_status, "?")
            publisher_tip = str(publisher_validation.get("reason", "出版社尚未通过 Crossref 模糊校验。"))
            if publisher_validation.get("observed"):
                publisher_tip += f"\n外部来源：{publisher_validation.get('observed')}"
        publisher_check = QLabel(publisher_mark)
        publisher_check.setObjectName("journalPublisherCheck")
        publisher_check.setProperty("validation", publisher_status or "unknown")
        publisher_check.setAlignment(Qt.AlignmentFlag.AlignCenter)
        publisher_check.setFixedWidth(28)
        publisher_check.setToolTip(publisher_tip)
        root.addWidget(publisher_check)

        health = journal_health_status(journal)
        quartile = primary_jcr_quartile(journal)
        if health in {"verified", "manual"}:
            jcr_text = quartile or "已核验"
            verification = "verified"
        elif health == "ai_pending_verification":
            jcr_text = f"{quartile}?" if quartile else "待核验"
            verification = "pending"
        elif health == "user_low_quality":
            jcr_text = quartile or "低分区"
            verification = "warning"
        else:
            jcr_text = "待核验"
            verification = "pending"
        jcr = QLabel(jcr_text)
        jcr.setObjectName("journalHealthChip")
        jcr.setProperty("verification", verification)
        jcr.setToolTip(jcr_label(journal))
        jcr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        jcr.setMinimumWidth(54)
        root.addWidget(jcr)

        quality = journal_quality_snapshot(journal)
        cas_upgrade = compact_cas_quartile(quality.get("cas_upgrade", "") or quality.get("cas_basic", ""))
        # CAS is a key comparison field. Keep a stable compact label instead
        # of an ignored/elided metadata column so the division stays visible
        # in a narrow workbench.
        cas_text = f"中科院 {cas_upgrade}" if cas_upgrade else "中科院待核验"
        cas = QLabel(cas_text)
        cas.setObjectName("journalCasBadge")
        cas.setToolTip(str(quality.get("metric_line", "")).strip() or "中科院分区尚未同步")
        cas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cas.setWordWrap(False)
        cas.setFixedWidth(max(82, QFontMetrics(cas.font()).horizontalAdvance(cas_text) + 12))
        cas.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        root.addWidget(cas, 1)

        more = QPushButton("⋯")
        more.setObjectName("rowButton")
        more.setFixedSize(25, 24)
        more.setToolTip("更多操作")
        menu = QMenu(more)
        copy_name = menu.addAction("复制期刊名")
        copy_name.triggered.connect(lambda: QApplication.clipboard().setText(model["name"]))
        menu.addSeparator()
        edit = menu.addAction("编辑")
        edit.triggered.connect(lambda: self.edit_requested.emit(str(journal.get("id", ""))))
        favorite = menu.addAction("取消收藏" if journal.get("favorite") else "收藏")
        favorite.triggered.connect(lambda: self.favorite_requested.emit(str(journal.get("id", ""))))
        update = menu.addAction("更新资料")
        update.triggered.connect(lambda: self.metadata_requested.emit(str(journal.get("id", ""))))
        menu.addSeparator()
        remove = menu.addAction("删除")
        remove.triggered.connect(lambda: self.delete_requested.emit(str(journal.get("id", ""))))
        more.setMenu(menu)
        root.addWidget(more)
        if movable:
            handle = OrderDragHandle(str(journal.get("id", "")), "journals")
            handle.setFixedSize(20, 21)
            handle.setToolTip("拖动调整手动排序")
            root.addWidget(handle)
        self._set_compact_columns(True)

    def _set_compact_columns(self, compact: bool) -> None:
        if self._name_label is None:
            return
        self._compact_columns = compact
        width = max(1, self.width())
        # The fixed metadata badges account for most of a 380 px widget.
        # Keep the selectable title usable, but let it wrap rather than make
        # the entire scroll area wider than its viewport.
        self._name_label.setMinimumWidth(max(76, int(width * (0.28 if compact else 0.50))))
        if self._publisher_label is not None:
            self._publisher_label.setVisible(not compact)
        if self._tag_label is not None:
            self._tag_label.setVisible(not compact)

    def resizeEvent(self, event) -> None:
        if self._name_label is not None:
            compact = self.width() < 520
            self._set_compact_columns(compact)
        super().resizeEvent(event)


class JournalLibraryRow(QFrame):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    favorite_requested = Signal(str)

    def __init__(self, journal: dict, usage: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.journal = journal
        self.setObjectName("journalLibraryRow")
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(4)

        header = QHBoxLayout()
        title = QLabel(f"{'★ ' if journal.get('favorite') else ''}{journal.get('name', '未命名期刊')}")
        title.setObjectName("cardTitle")
        title.setWordWrap(True)
        title.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        title.setCursor(Qt.CursorShape.IBeamCursor)
        header.addWidget(title, 1)
        favorite = QPushButton("★" if journal.get("favorite") else "☆")
        favorite.setObjectName("rowButton")
        favorite.setToolTip("取消收藏" if journal.get("favorite") else "收藏期刊")
        favorite.clicked.connect(lambda: self.favorite_requested.emit(str(journal.get("id", ""))))
        header.addWidget(favorite, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        meta = str(journal.get("publisher", "")).strip() or "未填写出版社"
        fields = journal.get("fields", [])
        if fields:
            meta += "  ·  " + " / ".join(str(item) for item in fields)
        meta_label = QLabel(meta)
        meta_label.setObjectName("cardDetail")
        meta_label.setWordWrap(True)
        root.addWidget(meta_label)

        quality = journal_quality_snapshot(journal)
        metric_line = compact_metric_line(journal) or "指标待核验"
        metric_label = QLabel(metric_line)
        metric_label.setObjectName("journalMetricLine")
        metric_label.setWordWrap(True)
        provenance = " · ".join(
            str(part).strip()
            for part in (quality.get("source", ""), quality.get("checked_at", ""))
            if str(part).strip()
        )
        metric_label.setToolTip(provenance or "指标待核验；可在工具中通过 EasyScholar 更新")
        root.addWidget(metric_label)

        submissions = int(usage.get("submission_count", 0))
        papers = usage.get("paper_titles", [])
        if submissions:
            history = f"投稿记录：{submissions} 次 · 关联论文：{len(papers)} 篇"
            if usage.get("last_used_at"):
                history += f" · 最近投稿：{usage['last_used_at']}"
            history_label = QLabel(history)
            history_label.setObjectName("journalUsage")
            history_label.setWordWrap(True)
            root.addWidget(history_label)
            if papers:
                paper_label = QLabel("关联：" + "；".join(papers[:2]) + (" …" if len(papers) > 2 else ""))
                paper_label.setObjectName("cardDetail")
                paper_label.setWordWrap(True)
                root.addWidget(paper_label)
        else:
            manual = QLabel("自建期刊记录 · 暂无投稿历史")
            manual.setObjectName("cardHint")
            root.addWidget(manual)

        notes = str(journal.get("notes", "")).strip()
        if notes:
            notes_label = QLabel("经验：" + notes)
            notes_label.setObjectName("cardNotes")
            notes_label.setWordWrap(True)
            root.addWidget(notes_label)

        actions = QHBoxLayout()
        website = str(journal.get("website", "")).strip()
        if website:
            open_site = QPushButton("官网")
            open_site.setObjectName("rowButton")
            open_site.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromUserInput(website)))
            actions.addWidget(open_site)
        actions.addStretch()
        edit = QPushButton("编辑")
        edit.setObjectName("rowButton")
        edit.clicked.connect(lambda: self.edit_requested.emit(str(journal.get("id", ""))))
        remove = QPushButton("删除")
        remove.setObjectName("dangerButton")
        remove.clicked.connect(lambda: self.delete_requested.emit(str(journal.get("id", ""))))
        actions.addWidget(edit)
        actions.addWidget(remove)
        root.addLayout(actions)


class JournalLibraryPage(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.journals: list[dict] = []
        self.usage: dict[str, dict] = {}
        self._metadata_worker: JournalMetadataThread | None = None
        self._metadata_target_ids: set[str] | None = None
        self._metadata_purpose = "metadata"
        self._jcr_worker: JournalJcrThread | None = None
        self._jcr_target_ids: set[str] | None = None
        self._easy_worker: JournalEasyScholarThread | None = None
        self._easy_target_ids: set[str] | None = None
        self._easy_automatic = False
        self._ai_worker: JournalAiEnrichmentThread | None = None
        self._ai_automatic = False
        self._library_compact: bool | None = None
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(8)
        self.heading_widget = QWidget()
        self.heading_layout = QGridLayout(self.heading_widget)
        self.heading_layout.setContentsMargins(0, 0, 0, 0)
        self.heading_layout.setHorizontalSpacing(8)
        self.heading_layout.setVerticalSpacing(5)
        self.title_box_widget = QWidget()
        title_box = QVBoxLayout(self.title_box_widget)
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(1)
        title = QLabel("期刊库")
        title.setObjectName("paperPageTitle")
        subtitle = QLabel("自动收录投稿期刊，沉淀自己的选刊经验")
        subtitle.setObjectName("dateLabel")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        self.choose_button = QPushButton("选刊")
        self.choose_button.setObjectName("subtleButton")
        self.choose_button.setToolTip("根据论文关键词、优先级和个人经验选刊")
        self.choose_button.clicked.connect(self._choose_for_paper)
        tools_menu = QMenu(self)
        tools_menu.setObjectName("journalToolsMenu")
        catalog_action = tools_menu.addAction("补充土地科学期刊")
        catalog_action.triggered.connect(self._add_land_science_catalog)
        update_changed_action = tools_menu.addAction("全部更新缺少/变化期刊")
        update_changed_action.triggered.connect(self._update_changed_journals)
        metadata_action = tools_menu.addAction("联网补全期刊信息")
        metadata_action.triggered.connect(lambda: self._enrich_metadata())
        incomplete_action = tools_menu.addAction("补全资料不完整的期刊")
        incomplete_action.triggered.connect(self._enrich_incomplete_metadata)
        publisher_check_action = tools_menu.addAction("模糊校验出版社（Crossref）")
        publisher_check_action.triggered.connect(self._validate_publishers)
        tools_menu.addSeparator()
        jcr_action = tools_menu.addAction("核验当前 JCR 分区（Clarivate）")
        jcr_action.triggered.connect(self._verify_jcr)
        easyscholar_action = tools_menu.addAction("更新期刊指标（EasyScholar）")
        easyscholar_action.triggered.connect(self._update_easyscholar)
        ai_action = tools_menu.addAction("DeepSeek 补全资料与 AI JCR 估计")
        ai_action.triggered.connect(self._enrich_with_ai)
        self.tools_button = QPushButton("工具")
        self.tools_button.setObjectName("subtleButton")
        self.tools_button.setMenu(tools_menu)
        self.add_button = QPushButton("+")
        self.add_button.setObjectName("primaryButton")
        self.add_button.setToolTip("添加期刊")
        self.add_button.clicked.connect(self._add_journal)
        root.addWidget(self.heading_widget)

        self.toolbar_widget = QWidget()
        self.toolbar_layout = QGridLayout(self.toolbar_widget)
        self.toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.toolbar_layout.setHorizontalSpacing(8)
        self.toolbar_layout.setVerticalSpacing(5)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索期刊、出版社、研究方向或个人经验")
        self.search_edit.textChanged.connect(self._render)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(
            ["全部", "已收藏", "已有投稿记录", "待补资料", "JCR 已核验", "JCR 待核验", "JCR Q1", "JCR Q2", "JCR Q3", "JCR Q4"]
        )
        self.filter_combo.currentTextChanged.connect(self._render)
        self.group_combo = QComboBox()
        self.group_combo.addItems(["按出版社", "按标签", "全部期刊"])
        self.group_combo.currentTextChanged.connect(self._render)
        root.addWidget(self.toolbar_widget)
        self.count_label = QLabel()
        self.count_label.setObjectName("sectionLabel")
        root.addWidget(self.count_label)
        self.health_label = QLabel()
        self.health_label.setObjectName("dateLabel")
        self.health_label.setWordWrap(True)
        self.health_label.hide()
        root.addWidget(self.health_label)
        self.notice_label = QLabel()
        self.notice_label.setObjectName("dateLabel")
        self.notice_label.setWordWrap(True)
        self.notice_label.hide()
        root.addWidget(self.notice_label)
        self.ai_progress = AiProgressPanel(object_name="journalLibraryAiProgress")
        root.addWidget(self.ai_progress)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("paperContent")
        self.rows = QVBoxLayout(content)
        # Keep the compact shelf exactly within a narrow scroll viewport; the
        # one-pixel reserve avoids a horizontal bar when the publisher badge
        # is present.
        self.rows.setContentsMargins(2, 2, 5, 10)
        self.rows.setSpacing(8)
        self.rows.addStretch()
        self.scroll.setWidget(content)
        root.addWidget(self.scroll, 1)
        self._adapt_compact_layout(force=True)

    def _adapt_compact_layout(self, *, force: bool = False) -> None:
        """Keep the library usable in the small widget without horizontal drift."""
        compact = self.width() <= 660
        if not force and compact == self._library_compact:
            return
        self._library_compact = compact
        for widget in (self.title_box_widget, self.choose_button, self.tools_button, self.add_button):
            self.heading_layout.removeWidget(widget)
        for widget in (self.search_edit, self.filter_combo, self.group_combo):
            self.toolbar_layout.removeWidget(widget)
        if compact:
            self.heading_layout.addWidget(self.title_box_widget, 0, 0, 1, 3)
            self.heading_layout.addWidget(self.choose_button, 1, 0)
            self.heading_layout.addWidget(self.tools_button, 1, 1)
            self.heading_layout.addWidget(self.add_button, 1, 2)
            self.heading_layout.setColumnStretch(0, 1)
            self.heading_layout.setColumnStretch(1, 0)
            self.heading_layout.setColumnStretch(2, 0)
            self.toolbar_layout.addWidget(self.search_edit, 0, 0, 1, 2)
            self.toolbar_layout.addWidget(self.filter_combo, 1, 0)
            self.toolbar_layout.addWidget(self.group_combo, 1, 1)
            self.toolbar_layout.setColumnStretch(0, 1)
            self.toolbar_layout.setColumnStretch(1, 1)
        else:
            self.heading_layout.addWidget(self.title_box_widget, 0, 0)
            self.heading_layout.addWidget(self.choose_button, 0, 1, alignment=Qt.AlignmentFlag.AlignBottom)
            self.heading_layout.addWidget(self.tools_button, 0, 2, alignment=Qt.AlignmentFlag.AlignBottom)
            self.heading_layout.addWidget(self.add_button, 0, 3, alignment=Qt.AlignmentFlag.AlignBottom)
            self.heading_layout.setColumnStretch(0, 1)
            self.heading_layout.setColumnStretch(1, 0)
            self.heading_layout.setColumnStretch(2, 0)
            self.heading_layout.setColumnStretch(3, 0)
            self.toolbar_layout.addWidget(self.search_edit, 0, 0)
            self.toolbar_layout.addWidget(self.filter_combo, 0, 1)
            self.toolbar_layout.addWidget(self.group_combo, 0, 2)
            self.toolbar_layout.setColumnStretch(0, 1)
            self.toolbar_layout.setColumnStretch(1, 0)
            self.toolbar_layout.setColumnStretch(2, 0)
        self.heading_widget.updateGeometry()
        self.toolbar_widget.updateGeometry()

    def reload(self) -> None:
        sync_journal_library_from_papers(load_papers())
        self.journals = load_journal_library()
        self.usage = journal_usage_index(load_papers())
        self._render()

    def _usage_for(self, journal: dict) -> dict:
        exact = self.usage.get(_journal_key(str(journal.get("name", "")), str(journal.get("publisher", ""))))
        if exact is not None:
            return exact
        name = str(journal.get("name", "")).strip().casefold()
        return next(
            (value for key, value in self.usage.items() if key.split("|", 1)[0] == name),
            {"submission_count": 0, "paper_titles": [], "last_used_at": ""},
        )

    def _filtered(self) -> list[dict]:
        keyword = self.search_edit.text().strip().casefold()
        category = self.filter_combo.currentText()
        result = []
        for journal in self.journals:
            usage = self._usage_for(journal)
            haystack = " ".join(
                [
                    str(journal.get("name", "")),
                    str(journal.get("publisher", "")),
                    str(journal.get("issn", "")),
                    " ".join(str(item) for item in journal.get("fields", [])),
                    " ".join(str(item) for item in journal.get("ai_tags", [])),
                    str(journal.get("ai_scope_cn", "")),
                    str(journal.get("ai_fit_cn", "")),
                    jcr_label(journal),
                    str(journal.get("notes", "")),
                ]
            ).casefold()
            if keyword and keyword not in haystack:
                continue
            if category == "已收藏" and not journal.get("favorite"):
                continue
            if category == "已有投稿记录" and not usage.get("submission_count"):
                continue
            if category == "待补资料" and (
                journal.get("fields")
                and str(journal.get("issn", "")).strip()
                and str(journal.get("website", "")).strip()
            ):
                continue
            jcr = journal.get("jcr", {})
            jcr = jcr if isinstance(jcr, dict) else {}
            if category == "JCR 已核验" and jcr.get("status") != "verified":
                continue
            if category == "JCR 待核验" and jcr.get("status") == "verified":
                continue
            if category.startswith("JCR Q") and primary_jcr_quartile(journal) != category[-2:]:
                continue
            result.append(journal)
        return result

    def _render(self) -> None:
        while self.rows.count() > 1:
            child = self.rows.takeAt(0)
            widget = child.widget()
            if widget:
                # Detach immediately so a just-refreshed page cannot retain
                # stale child size hints until Qt processes deferred deletes.
                widget.setParent(None)
                widget.deleteLater()
        journals = self._filtered()
        self.count_label.setText(f"共 {len(journals)} 本期刊")
        # Row-level green/red status marks communicate verification at the
        # point of use.  Avoid a second prose health summary above the shelf.
        self.health_label.clear()
        allow_manual_order = self.group_combo.currentText() == "全部期刊"
        for group_name, group_journals in self._grouped(journals):
            heading = QLabel(f"{group_name}  ·  {len(group_journals)}")
            heading.setObjectName("journalGroupHeading")
            self.rows.insertWidget(self.rows.count() - 1, heading)
            # A grouped column must size to its rows. Its own trailing stretch
            # previously absorbed the viewport height and created large blank
            # gaps between publisher groups.
            group_rows = ReorderableColumn("journals", include_stretch=False)
            group_rows.setObjectName("journalGroupRows")
            # Theme typography can change a child's construction-time size
            # hint by a few pixels. The shelf owns the available width, so
            # recompute rows inside the viewport instead of widening the
            # scroll content beyond it.
            group_rows.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Minimum)
            if allow_manual_order:
                group_rows.order_changed.connect(self._reorder_journals)
            for journal in group_journals:
                row = CompactJournalRow(journal, movable=allow_manual_order)
                row.edit_requested.connect(self._edit_journal)
                row.delete_requested.connect(self._delete_journal)
                row.favorite_requested.connect(self._toggle_favorite)
                row.metadata_requested.connect(self._request_metadata_for_id)
                group_rows.add_row(row, str(journal.get("id", "")))
            self.rows.insertWidget(self.rows.count() - 1, group_rows)
        if not journals:
            empty = QLabel("期刊库还没有记录。添加期刊，或先在投稿记录中保存一条期刊经历。")
            empty.setObjectName("emptyLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rows.insertWidget(0, empty)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._adapt_compact_layout()

    def _grouped(self, journals: list[dict]) -> list[tuple[str, list[dict]]]:
        mode = self.group_combo.currentText()
        groups: dict[str, list[dict]] = {}
        for journal in journals:
            if mode == "按标签":
                # One journal may have many tags. Showing it in every group
                # makes a 50-journal library look duplicated and makes drag
                # order ambiguous, so use the first tag as its main shelf.
                fields = [str(item).strip() for item in journal.get("fields", []) if str(item).strip()]
                names = [fields[0]] if fields else ["未分类"]
            elif mode == "全部期刊":
                names = ["全部期刊"]
            else:
                names = [publisher_group_key(journal)]
            for name in names:
                groups.setdefault(name, []).append(journal)
        return [
            (
                name,
                list(group),
            )
            for name, group in sorted(groups.items(), key=lambda item: (item[0].startswith("未"), item[0].casefold()))
        ]

    def _find_index(self, journal_id: str) -> int:
        return next((index for index, item in enumerate(self.journals) if str(item.get("id")) == journal_id), -1)

    def _add_journal(self) -> None:
        dialog = JournalLibraryDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            journal = dialog.journal()
            journal["ai_auto_pending"] = True
            self.journals.append(journal)
            self._save()
            self._enrich_metadata({str(journal.get("id", ""))})

    def _show_notice(self, text: str) -> None:
        self.notice_label.setText(text)
        self.notice_label.setVisible(bool(text))

    def _add_land_science_catalog(self) -> None:
        existing_ids = {str(item.get("id", "")) for item in self.journals}
        self.journals, added, enriched = merge_land_science_catalog(self.journals)
        for journal in self.journals:
            if str(journal.get("id", "")) not in existing_ids:
                journal["ai_auto_pending"] = True
        if added or enriched:
            self._save()
        else:
            self._render()
        detail = []
        if added:
            detail.append(f"新增 {added} 本")
        if enriched:
            detail.append(f"补齐 {enriched} 本的标签或 ISSN")
        self._show_notice("已补充土地科学期刊：" + "，".join(detail or ["现有期刊均已包含"]))

    def _request_metadata_for_id(self, journal_id: str) -> None:
        if journal_id:
            self._enrich_metadata({journal_id})

    def _enrich_metadata(self, journal_ids: set[str] | None = None, *, force: bool = False, purpose: str = "metadata") -> None:
        if self._metadata_worker is not None and self._metadata_worker.isRunning():
            return
        if not self.journals:
            self._show_notice("期刊库为空，请先添加期刊或使用“补充土地科学期刊”。")
            return
        requested_ids = {item for item in (journal_ids or set()) if item}
        target = [
            journal
            for journal in self.journals
            if (not requested_ids or str(journal.get("id", "")) in requested_ids)
            and (force or journal_needs_metadata_enrichment(journal))
        ]
        if not target:
            self._show_notice("没有新增、改名或资料不完整的期刊，未联网查询。")
            return
        # The worker only receives the incremental subset, so always merge its
        # response back into the full local library by ID.
        self._metadata_target_ids = {str(journal.get("id", "")) for journal in target}
        self._metadata_purpose = purpose
        self.tools_button.setEnabled(False)
        if purpose == "publisher":
            self._show_notice(f"正在通过 Crossref 模糊校验 {len(target)} 本期刊的出版社…")
        else:
            self._show_notice("正在联网补全规范刊名、出版社与 ISSN…")
        self._metadata_worker = JournalMetadataThread(target, self)
        self._metadata_worker.completed.connect(self._metadata_finished)
        self._metadata_worker.failed.connect(self._metadata_failed)
        self._metadata_worker.finished.connect(self._clear_metadata_worker)
        self._metadata_worker.start()

    def _enrich_incomplete_metadata(self) -> None:
        target = {
            str(journal.get("id", ""))
            for journal in self.journals
            if (
                not journal.get("fields")
                or not str(journal.get("issn", "")).strip()
                or not str(journal.get("website", "")).strip()
            )
        }
        if not target:
            self._show_notice("所有期刊都已具备基础资料。")
            return
        self._enrich_metadata(target)

    def _validate_publishers(self) -> None:
        target = self._filtered()
        if not target:
            self._show_notice("当前筛选下没有期刊可进行出版社校验。")
            return
        target_ids = {str(item.get("id", "")) for item in target if str(item.get("id", "")).strip()}
        # Records without a publisher remain in the result as unknown and are
        # never removed; Crossref simply has nothing to compare for them.
        self._enrich_metadata(target_ids, force=True, purpose="publisher")

    def _update_changed_journals(self) -> None:
        """Run a quota-aware health pass; stable records are explicitly skipped."""
        model = str(get_ai_settings().get("model", "")).strip()
        targets = journal_health_targets(self.journals, model=model, today=date.today().isoformat())
        if not targets:
            self._show_notice("全部期刊均为稳定状态，已跳过联网与 AI 调用。")
            return
        target_ids = {str(item.get("id", "")) for item in targets if str(item.get("id", ""))}
        skipped = max(0, len(self.journals) - len(target_ids))
        self._show_notice(f"将更新 {len(target_ids)} 本资料不全或已变化的期刊；稳定期刊已跳过 {skipped} 本。")
        self._enrich_metadata(target_ids)

    def _metadata_finished(self, result: dict) -> None:
        incoming = list(result.get("journals", self.journals))
        if self._metadata_target_ids:
            by_id = {str(item.get("id", "")): item for item in incoming}
            self.journals = [by_id.get(str(item.get("id", "")), item) for item in self.journals]
        else:
            self.journals = incoming
        save_journal_library(self.journals)
        self.reload()
        self.changed.emit()
        errors = result.get("errors", [])
        if self._metadata_purpose == "publisher":
            message = f"出版社模糊校验完成：处理 {len(incoming)} 本期刊，未匹配或缺失的信息已保留待人工核对"
        else:
            message = f"联网补全完成：更新 {int(result.get('changed', 0))} 本期刊"
        if errors:
            message += f"；{len(errors)} 本暂未可靠匹配，可稍后重试"
        self._show_notice(message)

    def _metadata_failed(self, _message: str) -> None:
        self._show_notice("联网补全失败：请检查网络后重试。")

    def _clear_metadata_worker(self) -> None:
        if self._metadata_worker is not None:
            self._metadata_worker.deleteLater()
        self._metadata_worker = None
        self._metadata_target_ids = None
        self._metadata_purpose = "metadata"
        self._sync_tools_enabled()

    def _verify_jcr(self) -> None:
        if self._jcr_worker is not None and self._jcr_worker.isRunning():
            return
        if not is_jcr_ready():
            self._show_notice("请先在“设置 → 智能增强与 JCR”配置已授权的 Clarivate Journals API。")
            return
        target = self._filtered()
        if not target:
            self._show_notice("当前筛选下没有期刊可核验。")
            return
        self._jcr_target_ids = {str(item.get("id", "")) for item in target if str(item.get("id", ""))}
        self.tools_button.setEnabled(False)
        self._show_notice(f"正在通过 Clarivate 核验 {len(target)} 本期刊的 JCR 分区…")
        self._jcr_worker = JournalJcrThread(target, self)
        self._jcr_worker.completed.connect(self._jcr_finished)
        self._jcr_worker.failed.connect(self._jcr_failed)
        self._jcr_worker.finished.connect(self._clear_jcr_worker)
        self._jcr_worker.start()

    def _jcr_finished(self, result: dict) -> None:
        incoming = {str(item.get("id", "")): item for item in result.get("journals", []) if isinstance(item, dict)}
        if self._jcr_target_ids:
            self.journals = [incoming.get(str(item.get("id", "")), item) for item in self.journals]
        self._save()
        errors = list(result.get("errors", []))
        message = f"JCR 核验完成：更新 {int(result.get('changed', 0))} 本"
        if errors:
            message += f"；{len(errors)} 本未获得可识别分区"
        self._show_notice(message)

    def _jcr_failed(self, message: str) -> None:
        self._show_notice("JCR 核验失败：" + str(message))

    def _clear_jcr_worker(self) -> None:
        if self._jcr_worker is not None:
            self._jcr_worker.deleteLater()
        self._jcr_worker = None
        self._jcr_target_ids = None
        self._sync_tools_enabled()

    def _update_easyscholar(self, journal_ids: set[str] | None = None) -> None:
        """Refresh only changed or stale journal identities from EasyScholar."""
        if self._easy_worker is not None and self._easy_worker.isRunning():
            return
        if not is_easyscholar_ready():
            self._show_notice("请先在“设置 → AI 与期刊数据”填写并启用 EasyScholar 密钥。")
            return
        if not self.journals:
            self._show_notice("期刊库为空，请先添加期刊。")
            return
        settings = get_easyscholar_settings()
        requested_ids = {str(item) for item in (journal_ids or set()) if str(item)}
        target = [
            journal
            for journal in self.journals
            if (not requested_ids or str(journal.get("id", "")) in requested_ids)
            and journal_needs_easyscholar_update(
                journal,
                today=date.today().isoformat(),
                cache_days=int(settings.get("cache_days", 30) or 30),
            )
        ]
        if not target:
            self._show_notice("没有新增、改名或超过缓存期限的期刊，已跳过 EasyScholar 查询。")
            return
        self._start_easyscholar_update(target, automatic=False)

    def auto_update_easyscholar_if_due(self) -> bool:
        """At most once daily, check newly added or stale journal identities."""
        if self._easy_worker is not None and self._easy_worker.isRunning():
            return False
        if not is_easyscholar_ready():
            return False
        settings = load_app_settings()
        stored = dict(settings.get("easyscholar", {})) if isinstance(settings.get("easyscholar", {}), dict) else {}
        today = date.today().isoformat()
        if str(stored.get("last_auto_checked", "")) == today:
            return False
        cache_days = int(stored.get("cache_days", 30) or 30)
        target = [
            journal
            for journal in self.journals
            if journal_needs_easyscholar_update(journal, today=today, cache_days=cache_days)
        ]
        # Persist the daily attempt before any worker starts, preventing two
        # launches from paying for the same unchanged library on one day.
        stored["last_auto_checked"] = today
        settings["easyscholar"] = stored
        save_app_settings(settings)
        if not target:
            return False
        self._start_easyscholar_update(target, automatic=True)
        return True

    def _start_easyscholar_update(self, target: list[dict], *, automatic: bool) -> None:
        if not target:
            return
        self._easy_target_ids = {str(item.get("id", "")) for item in target if str(item.get("id", ""))}
        self._easy_automatic = automatic
        self.tools_button.setEnabled(False)
        prefix = "正在自动更新" if automatic else "正在更新"
        self._show_notice(f"{prefix} {len(target)} 本期刊的 JCR、中科院分区和影响因子（EasyScholar）…")
        self._easy_worker = JournalEasyScholarThread(target, self)
        self._easy_worker.completed.connect(self._easyscholar_finished)
        self._easy_worker.failed.connect(self._easyscholar_failed)
        self._easy_worker.finished.connect(self._clear_easyscholar_worker)
        self._easy_worker.start()

    def _easyscholar_finished(self, result: dict) -> None:
        incoming = {
            str(item.get("id", "")): item
            for item in result.get("journals", [])
            if isinstance(item, dict) and str(item.get("id", ""))
        }
        if self._easy_target_ids:
            self.journals = [incoming.get(str(item.get("id", "")), item) for item in self.journals]
        self._save()
        changed = int(result.get("changed", 0) or 0)
        requested = int(result.get("requested", 0) or 0)
        skipped = int(result.get("skipped", 0) or 0)
        prefix = "已自动检查" if self._easy_automatic else "EasyScholar 更新完成"
        message = f"{prefix}：更新 {changed} / 请求 {requested} 本"
        if skipped:
            message += f"；缓存跳过 {skipped} 本"
        errors = list(result.get("errors", []))
        if errors:
            message += f"；{len(errors)} 本暂未更新"
        self._show_notice(message)

    def _easyscholar_failed(self, _message: str) -> None:
        prefix = "自动期刊指标检查失败" if self._easy_automatic else "EasyScholar 更新失败"
        self._show_notice(prefix + "：请检查网络、密钥或稍后重试。")

    def _clear_easyscholar_worker(self) -> None:
        if self._easy_worker is not None:
            self._easy_worker.deleteLater()
        self._easy_worker = None
        self._easy_target_ids = None
        self._easy_automatic = False
        self._sync_tools_enabled()

    def _enrich_with_ai(self) -> None:
        if self._ai_worker is not None and self._ai_worker.isRunning():
            return
        if not is_deepseek_ready("journal_enrichment"):
            self._show_notice("请先在“设置 → 智能增强与 JCR”配置并启用 DeepSeek。")
            return
        model = str(get_ai_settings().get("model", "")).strip()
        health_ids = {
            str(journal.get("id", ""))
            for journal in journal_health_targets(self.journals, model=model, today=date.today().isoformat())
        }
        target = [
            journal
            for journal in self._filtered()
            if str(journal.get("id", "")) in health_ids or journal_needs_ai_jcr_estimate(journal)
        ]
        if not target:
            self._show_notice("当前筛选下没有资料变化或缺失的期刊，未调用 DeepSeek。")
            return
        self._start_ai_enrichment(target, automatic=False)

    def auto_enrich_new_if_due(self) -> bool:
        """Once a day, enrich only journals deliberately marked as newly added."""
        if self._ai_worker is not None and self._ai_worker.isRunning():
            return False
        ai_settings = get_ai_settings()
        if not ai_settings.get("journal_auto_enrichment", True) or not is_deepseek_ready("journal_enrichment"):
            return False
        settings = load_app_settings()
        stored_ai = dict(settings.get("ai", {})) if isinstance(settings.get("ai", {}), dict) else {}
        today = date.today().isoformat()
        if str(stored_ai.get("journal_auto_last_checked", "")) == today:
            return False
        model = str(ai_settings.get("model", "")).strip()
        pending_ids = {
            str(journal.get("id", ""))
            for journal in journal_health_targets(self.journals, model=model, today=today)
        }
        pending = [
            journal
            for journal in self.journals
            if str(journal.get("id", "")) in pending_ids or journal_needs_ai_enrichment(journal, model) or journal_needs_ai_jcr_estimate(journal)
        ]
        # Mark the daily attempt before starting its background request, so a
        # second app launch cannot accidentally submit the same fresh journals.
        stored_ai["journal_auto_last_checked"] = today
        settings["ai"] = stored_ai
        save_app_settings(settings)
        if not pending:
            return False
        self._start_ai_enrichment(pending, automatic=True)
        return True

    def _start_ai_enrichment(self, target: list[dict], automatic: bool) -> None:
        if not target:
            return
        self._ai_automatic = automatic
        self.tools_button.setEnabled(False)
        self.ai_progress.begin("正在准备期刊资料供 AI 分析…")
        prefix = "正在自动补充" if automatic else "DeepSeek 正在补充"
        self._show_notice(f"{prefix} {len(target)} 本期刊的方向、选刊提示与 AI JCR 估计…")
        self._ai_worker = JournalAiEnrichmentThread(target, self)
        self._ai_worker.progress.connect(self._ai_enrichment_progress)
        self._ai_worker.completed.connect(self._ai_enrichment_finished)
        self._ai_worker.failed.connect(self._ai_enrichment_failed)
        self._ai_worker.finished.connect(self._clear_ai_worker)
        self._ai_worker.start()

    def _ai_enrichment_finished(self, result: dict) -> None:
        updates = {
            str(item.get("id", "")): item
            for item in result.get("updates", [])
            if isinstance(item, dict) and str(item.get("id", ""))
        }
        estimated_jcr = 0
        for journal in self.journals:
            patch = updates.get(str(journal.get("id", "")))
            if patch is None:
                continue
            patch = dict(patch)
            estimate = patch.pop("ai_jcr_estimate", None)
            merged = merge_journal_health_patch(journal, patch)
            journal.clear()
            journal.update(merged)
            current_jcr = journal.get("jcr", {})
            current_jcr = current_jcr if isinstance(current_jcr, dict) else {}
            current_status = str(current_jcr.get("status", "")).strip()
            if isinstance(estimate, dict) and current_status not in {"verified", "manual", "not_found"}:
                quartile = str(estimate.get("quartile", "")).strip().upper()
                if quartile in {"Q1", "Q2", "Q3", "Q4"}:
                    confidence = str(estimate.get("confidence", "low")).strip().casefold()
                    confidence_cn = {"high": "高", "medium": "中", "low": "低"}.get(confidence, "低")
                    journal["jcr"] = {
                        "status": "ai_estimated",
                        "source": "DeepSeek AI 估计（待 Clarivate 核验）",
                        "checked_at": date.today().isoformat(),
                        "confidence": confidence_cn,
                        "note": str(estimate.get("reason", "")).strip(),
                        "metrics": [{"quartile": quartile, "year": 0, "category": ""}],
                    }
                    estimated_jcr += 1
            journal["ai_updated_at"] = date.today().isoformat()
            journal["ai_source_signature"] = journal_ai_source_signature(journal, str(result.get("model", "")).strip())
            journal["ai_auto_pending"] = False
        self._save()
        requested = int(result.get("requested", len(updates)) or 0)
        prefix = "已自动补全" if self._ai_automatic else "DeepSeek 已补充"
        message = f"{prefix} {len(updates)} / {requested} 本期刊"
        if estimated_jcr:
            message += f"；其中 {estimated_jcr} 本新增 AI JCR 估计（待 Clarivate 核验）"
        else:
            message += "；JCR 信息仍需 Clarivate 核验"
        failed_batches = list(result.get("failed_batches", []))
        if failed_batches:
            message += f" {len(failed_batches)} 批未完成，可稍后对当前筛选重试。"
        self._show_notice(message)
        self.ai_progress.complete("AI 期刊资料补充完成。")

    def _ai_enrichment_failed(self, message: str) -> None:
        prefix = "期刊自动补全失败：" if self._ai_automatic else "DeepSeek 补全失败："
        self._show_notice(prefix + str(message))
        self.ai_progress.fail(prefix + str(message))

    def _ai_enrichment_progress(self, message: str, value: int) -> None:
        self._show_notice(str(message))
        self.ai_progress.update(str(message), int(value))

    def _clear_ai_worker(self) -> None:
        if self._ai_worker is not None:
            self._ai_worker.deleteLater()
        self._ai_worker = None
        self._ai_automatic = False
        self._sync_tools_enabled()

    def _sync_tools_enabled(self) -> None:
        busy = any(
            worker is not None and worker.isRunning()
            for worker in (self._metadata_worker, self._jcr_worker, self._easy_worker, self._ai_worker)
        )
        self.tools_button.setEnabled(not busy)

    def _apply_selection_action(self, dialog: JournalSelectionDialog, papers: list[dict], action: str) -> None:
        paper_id, _journal_id = dialog.selection()
        paper_index = next((index for index, item in enumerate(papers) if str(item.get("id")) == paper_id), -1)
        paper = papers[paper_index] if paper_index >= 0 else None
        candidate = dialog.selected_candidate()
        journal = dialog.selected_journal()
        if paper is None or not journal:
            if hasattr(dialog, "mark_action_complete"):
                dialog.mark_action_complete(action, False, "未找到当前论文或期刊，未保存任何数据。")
            return
        if dialog.is_external_selection():
            name_key = str(journal.get("name", "")).strip().casefold()
            publisher_key = str(journal.get("publisher", "")).strip().casefold()
            existing = next(
                (
                    item
                    for item in self.journals
                    if str(item.get("name", "")).strip().casefold() == name_key
                    and str(item.get("publisher", "")).strip().casefold() == publisher_key
                ),
                None,
            )
            if existing is None:
                journal = external_candidate_to_library_journal(candidate)
                self.journals.append(journal)
                self._save()
            else:
                journal = existing
            if action == "import":
                message = f"已快速入库“{journal.get('name', '')}”；期刊指标仍待核验。可继续选择其他推荐。"
                self._show_notice(message)
                if hasattr(dialog, "mark_action_complete"):
                    dialog.mark_action_complete(action, True, message)
                return
        elif action == "import":
            message = "本地期刊已经在期刊库中，无需重复入库。"
            if hasattr(dialog, "mark_action_complete"):
                dialog.mark_action_complete(action, False, message)
            return
        updated_paper = append_journal_to_submission_path(paper, journal)
        if updated_paper == paper:
            message = "该论文已经包含这本期刊的投稿记录。"
            if hasattr(dialog, "mark_action_complete"):
                dialog.mark_action_complete(action, False, message)
            else:
                QMessageBox.information(self, "期刊已存在", message)
            return
        papers[paper_index] = updated_paper
        result = save_papers(papers)
        if not result.get("saved", False):
            message = "检测到投稿记录中有晚于今天的日期，请先修正后再添加期刊。"
            if hasattr(dialog, "mark_action_complete"):
                dialog.mark_action_complete(action, False, message)
            else:
                QMessageBox.warning(self, "暂未添加", message)
            return
        self.reload()
        self.changed.emit()
        message = "已加入该论文的投稿路径：准备投稿。可继续比较其他推荐。"
        self._show_notice(message)
        if hasattr(dialog, "mark_action_complete"):
            dialog.mark_action_complete(action, True, message)

    def _choose_for_paper(self) -> None:
        papers = load_papers()
        if not papers:
            QMessageBox.information(self, "暂无论文", "请先在论文投稿记录中创建一篇论文，再为它选择目标期刊。")
            return
        frontier_data = load_frontier_data()
        profile = frontier_data.get("profile", {}) if isinstance(frontier_data, dict) else {}
        settings = load_app_settings()
        research_settings = settings.get("research", {}) if isinstance(settings, dict) else {}
        dialog = JournalSelectionDialog(
            papers,
            self.journals,
            profile if isinstance(profile, dict) else {},
            self,
            history=journal_usage_index(papers),
            constraints=research_settings if isinstance(research_settings, dict) else {},
        )
        if hasattr(dialog, "action_requested"):
            dialog.action_requested.connect(lambda action, d=dialog: self._apply_selection_action(d, papers, action))
            dialog.exec()
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_selection_action(dialog, papers, dialog.selection_action())

    def _edit_journal(self, journal_id: str) -> None:
        index = self._find_index(journal_id)
        if index < 0:
            return
        dialog = JournalLibraryDialog(self, self.journals[index])
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.delete_requested:
                del self.journals[index]
                self._save()
                return
            journal = dialog.journal()
            self.journals[index] = journal
            self._save()
            self._enrich_metadata({str(journal.get("id", ""))})

    def _toggle_favorite(self, journal_id: str) -> None:
        index = self._find_index(journal_id)
        if index < 0:
            return
        self.journals[index]["favorite"] = not bool(self.journals[index].get("favorite"))
        self._save()

    def _delete_journal(self, journal_id: str) -> None:
        index = self._find_index(journal_id)
        if index < 0:
            return
        journal = self.journals[index]
        if self._usage_for(journal).get("submission_count"):
            QMessageBox.information(self, "保留投稿期刊", "该期刊已关联投稿记录，系统会自动收录它。你可以编辑或取消收藏，但不能从期刊库移除。")
            return
        if not confirm_delete(self, "删除期刊", f"“{journal.get('name', '')}”将从期刊库中移除。"):
            return
        removed = dict(self.journals[index])
        del self.journals[index]
        self._save()
        show_undo_toast(
            self,
            "已删除期刊",
            lambda: self._restore_journal(index, removed),
        )

    def _restore_journal(self, index: int, journal: dict) -> None:
        if any(str(item.get("id", "")) == str(journal.get("id", "")) for item in self.journals):
            return
        self.journals.insert(min(index, len(self.journals)), journal)
        self._save()

    def _save(self) -> None:
        save_journal_library(self.journals)
        self.reload()
        self.changed.emit()

    def _reorder_journals(self, ordered_ids: list[str]) -> None:
        ordered_set = set(ordered_ids)
        by_id = {str(journal.get("id", "")): journal for journal in self.journals}
        ordered_journals = iter([by_id[item_id] for item_id in ordered_ids if item_id in by_id])
        self.journals = [
            next(ordered_journals) if str(journal.get("id", "")) in ordered_set else journal
            for journal in self.journals
        ]
        self._save()
