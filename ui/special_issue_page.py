"""Compact LIBRARY preview for saved and high-match journal calls."""

from __future__ import annotations

from datetime import date, datetime, time
import os
from typing import Callable

from ui.page_kit import ElidedLabel, PageHeader

from PySide6.QtCore import QSize, QThread, Qt, Signal, QTimer
from PySide6.QtGui import QFontMetrics, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from utils.special_issue_repository import load_special_issue_overview
from utils.special_issue_service import refresh_special_issues, special_issue_refresh_due
from utils.journal_quality import compact_cas_quartile, journal_quality_snapshot
from utils.special_issue_policy import evaluate_special_issue, is_read, is_saved


class SpecialIssueRefreshThread(QThread):
    progress = Signal(str, int)
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, force: bool = False) -> None:
        super().__init__(parent)
        self.force = force

    def run(self) -> None:
        try:
            result = refresh_special_issues(
                progress=self.progress.emit,
                force=self.force,
                cancelled=self.isInterruptionRequested,
            )
        except Exception as error:  # noqa: BLE001 - surface a readable background error
            self.failed.emit(str(error))
            return
        self.completed.emit(result)


class SpecialIssueLoadThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        try:
            result = load_special_issue_overview()
        except Exception as error:  # noqa: BLE001 - existing preview stays usable
            if not self.isInterruptionRequested():
                self.failed.emit(str(error))
            return
        if not self.isInterruptionRequested():
            self.completed.emit(result)


class SpecialIssuePreviewRow(QFrame):
    clicked = Signal(str)

    def __init__(self, item: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.issue_id = str(item.get("id", ""))
        self.setObjectName("specialIssuePreviewRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(62)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root = QVBoxLayout(self)
        root.setContentsMargins(9, 6, 9, 6)
        root.setSpacing(2)
        heading = QHBoxLayout()
        heading.setSpacing(6)
        title = ElidedLabel(str(item.get("title", "未命名特刊")))
        title.setObjectName("specialIssuePreviewTitle")
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        heading.addWidget(title, 1)
        is_v13 = str(item.get("scoring_version", "")).startswith("special-issue-dual-axis-13")
        if is_v13:
            relevance = int(item.get("relevance_score", 0) or 0)
            opportunity = int(item.get("opportunity_score", 0) or 0)
            level = str(item.get("pyramid_level", ""))
            relevance_axis = item.get("relevance_axis", {}) if isinstance(item.get("relevance_axis"), dict) else {}
            opportunity_axis = item.get("opportunity_axis", {}) if isinstance(item.get("opportunity_axis"), dict) else {}
            ai_used = relevance_axis.get("ai_adjustment") is not None or opportunity_axis.get("ai_adjustment") is not None
            score_text = f"{level} · 相关{relevance} / 机会{opportunity}" if level else f"相关{relevance} / 机会{opportunity}"
            score_label_tip = "AI 已参与双轴评分" if ai_used else "AI 未参与，本条按规则评分"
        else:
            score_text = str(int((item.get("match") or {}).get("score", 0) or 0))
            score_label_tip = "旧版匹配分"
        score_label = QLabel(score_text)
        score_label.setObjectName("specialIssuePreviewScore")
        score_label.setToolTip(score_label_tip)
        score_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        score_label.setFixedWidth(132 if is_v13 else 28)
        heading.addWidget(score_label)
        root.addLayout(heading)
        journal = str(item.get("journal", "未知期刊")).strip() or "未知期刊"
        deadline = str(item.get("deadline", "待确认")).strip() or "待确认"
        publisher = str(item.get("publisher", "")).strip()
        publisher = "" if publisher.casefold() == "unknown" else publisher
        quality = journal_quality_snapshot(
            {
                "jcr": item.get("jcr", {}),
                "easyscholar": item.get("cas", {}),
            }
        )
        jcr = str(quality.get("jcr_quartile", "")).strip()
        cas = compact_cas_quartile(quality.get("cas_upgrade", "") or quality.get("cas_basic", ""))
        meta = ElidedLabel(
            " · ".join(
                value
                for value in (
                    journal,
                    publisher,
                    f"JCR {jcr}" if jcr else "",
                    f"中科院 {cas}" if cas else "",
                    f"截止 {deadline}",
                )
                if value
            )
        )
        meta.setObjectName("specialIssuePreviewMeta")
        root.addWidget(meta)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.issue_id)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SpecialIssuePage(QWidget):
    """A widget-sized triage surface; detailed work stays in the large dialog."""

    open_workbench = Signal(str)
    changed = Signal()
    refresh_progress = Signal(str, int)
    refresh_completed = Signal(dict)
    refresh_failed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        today_provider: Callable[[], date] = date.today,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("specialIssuePage")
        self._today_provider = today_provider
        self.preview_issue_ids: list[str] = []
        self.preview_rows: list[SpecialIssuePreviewRow] = []
        self._refresh_worker: SpecialIssueRefreshThread | None = None
        self._load_worker: SpecialIssueLoadThread | None = None
        self._loaded = False
        self._build_ui()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._loaded:
            if self.window() is self:
                self.reload()
                return
            if os.environ.get("RESEARCH_ASSISTANT_DISABLE_BACKGROUND") == "1":
                self.reload()
            else:
                self.request_reload()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 9)
        root.setSpacing(7)
        header = PageHeader("特刊征稿", accent="special_issues")
        self.refresh_status = header.hint_label
        self.open_button = header.add_primary_action(
            "打开特刊工作台",
            lambda: self.open_workbench.emit(""),
            tooltip="打开完整特刊征稿工作台",
        )
        self.open_button.setObjectName("specialIssueOpenWorkbench")
        self.open_button.setProperty("accent", "special_issues")
        self.open_button.setMinimumHeight(28)
        root.addWidget(header)

        summary = QFrame()
        summary.setObjectName("specialIssueSummary")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(8, 6, 8, 6)
        summary_layout.setSpacing(4)
        self.unread_value = self._metric(summary_layout, "未读", "specialIssueUnreadCount")
        self.saved_value = self._metric(summary_layout, "收藏", "specialIssueSavedCount")
        self.deadline_value = self._metric(summary_layout, "最近截止", "specialIssueNearestDeadline", stretch=2)
        root.addWidget(summary)

        scroll = QScrollArea()
        scroll.setObjectName("specialIssuePreviewScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_host = QWidget()
        self.preview_host.setObjectName("specialIssuePreviewHost")
        self.preview_layout = QVBoxLayout(self.preview_host)
        self.preview_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_layout.setSpacing(3)
        scroll.setWidget(self.preview_host)
        root.addWidget(scroll, 1)


    @staticmethod
    def _metric(layout: QHBoxLayout, label: str, object_name: str, *, stretch: int = 1) -> QLabel:
        host = QWidget()
        host.setObjectName("specialIssueMetric")
        column = QVBoxLayout(host)
        column.setContentsMargins(4, 0, 4, 0)
        column.setSpacing(0)
        value = QLabel("0")
        value.setObjectName(object_name)
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption = QLabel(label)
        caption.setObjectName("specialIssueMetricLabel")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(value)
        column.addWidget(caption)
        layout.addWidget(host, stretch)
        return value

    def reload(self) -> None:
        try:
            store = load_special_issue_overview()
        except Exception:
            store = {"items": []}
        self._apply_loaded_store(store)

    def request_reload(self) -> None:
        if self._load_worker is not None and self._load_worker.isRunning():
            return
        worker = SpecialIssueLoadThread(self)
        worker.completed.connect(self._apply_loaded_store)
        worker.failed.connect(lambda message: self.refresh_status.setText(f"后台载入失败：{message}"))
        worker.finished.connect(self._release_load_worker)
        self._load_worker = worker
        worker.start(QThread.Priority.LowPriority)

    def _release_load_worker(self) -> None:
        worker = self._load_worker
        self._load_worker = None
        if worker is not None:
            worker.deleteLater()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        waiting = False
        for worker in (self._load_worker, self._refresh_worker):
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                waiting = True
        if waiting:
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        super().closeEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._load_worker is not None and self._load_worker.isRunning():
            self._load_worker.requestInterruption()
        super().hideEvent(event)

    def _apply_loaded_store(self, store: object) -> None:
        store = store if isinstance(store, dict) else {"items": []}
        self._loaded = True
        items = [value for value in store.get("items", []) if isinstance(value, dict)]
        now = datetime.combine(self._today_provider(), time(hour=12))
        saved = [value for value in items if evaluate_special_issue(value, now=now, view="saved")["visible"]]
        recommended_with_decisions = [
            (evaluate_special_issue(value, now=now, view="recommended"), value)
            for value in items
        ]
        recommended_with_decisions = [value for value in recommended_with_decisions if value[0]["visible"]]
        saved.sort(key=lambda value: (str(value.get("deadline", "9999-99-99")), -int((value.get("match") or {}).get("rank_score", (value.get("match") or {}).get("score", 0)) or 0)))
        recommended_with_decisions.sort(key=lambda value: value[0]["sort_key"])
        recommended = [value for _decision, value in recommended_with_decisions]
        unread = sum(1 for value in recommended if not is_read(value))
        self.unread_value.setText(str(unread))
        self.saved_value.setText(str(len(saved)))
        self.deadline_value.setText(self._nearest_deadline_text([*saved, *recommended]))
        self._rebuild_preview(saved, recommended)

    def is_refresh_running(self) -> bool:
        return self._refresh_worker is not None and self._refresh_worker.isRunning()

    def start_refresh(self, *, force: bool = False) -> bool:
        if self.is_refresh_running():
            return False
        store = load_special_issue_overview()
        if not force:
            from datetime import datetime

            if not special_issue_refresh_due(store, now=datetime.now()):
                return False
        self.refresh_status.setText("正在刷新…")
        worker = SpecialIssueRefreshThread(self, force=force)
        worker.progress.connect(self._on_refresh_progress)
        worker.completed.connect(self._on_refresh_completed)
        worker.failed.connect(self._on_refresh_failed)
        worker.finished.connect(self._release_refresh_worker)
        self._refresh_worker = worker
        worker.start()
        return True

    def auto_refresh_if_due(self) -> bool:
        return self.start_refresh(force=False)

    def cancel_refresh(self) -> bool:
        worker = self._refresh_worker
        if worker is None or not worker.isRunning():
            return False
        worker.requestInterruption()
        self.refresh_status.setText("正在停止…")
        return True

    def _on_refresh_progress(self, message: str, value: int) -> None:
        self.refresh_status.setText(message)
        self.refresh_progress.emit(message, value)

    def _on_refresh_completed(self, result: dict) -> None:
        stats = result.get("stats", {}) if isinstance(result, dict) else {}
        store = result.get("store", {}) if isinstance(result, dict) else {}
        status = str(store.get("last_refresh_status", "success"))
        summary = (
            f"现有 {int(stats.get('items', 0) or 0)} 条，"
            f"其中 {int(stats.get('eligible', 0) or 0)} 条符合条件"
        )
        self.refresh_status.setText(
            "已取消" if status == "cancelled" else f"部分完成 · {summary}" if status == "partial" else summary
        )
        self.reload()
        self.changed.emit()
        self.refresh_completed.emit(result)

    def _on_refresh_failed(self, message: str) -> None:
        self.refresh_status.setText("刷新失败")
        self.refresh_failed.emit(str(message))

    def _release_refresh_worker(self) -> None:
        worker = self._refresh_worker
        self._refresh_worker = None
        if worker is not None:
            worker.deleteLater()

    def _nearest_deadline_text(self, items: list[dict]) -> str:
        today = self._today_provider()
        days: list[int] = []
        for item in items:
            try:
                remaining = (date.fromisoformat(str(item.get("deadline", ""))[:10]) - today).days
            except ValueError:
                continue
            if remaining >= 0:
                days.append(remaining)
        return f"{min(days)} 天" if days else "待发现"

    def _rebuild_preview(self, saved: list[dict], recommended: list[dict]) -> None:
        while self.preview_layout.count():
            child = self.preview_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        self.preview_issue_ids = []
        self.preview_rows = []
        self._add_section("已收藏", saved, "specialIssueSavedList")
        self._add_section("符合条件", recommended, "specialIssueRecommendedList")
        if not saved and not recommended:
            empty = QLabel("暂时没有达到推送门槛的特刊")
            empty.setObjectName("specialIssueEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(96)
            self.preview_layout.addWidget(empty)
        self.preview_layout.addStretch(1)

    def _add_section(self, title: str, items: list[dict], object_name: str) -> None:
        if not items:
            return
        heading = QLabel(title)
        heading.setObjectName("specialIssueSectionTitle")
        self.preview_layout.addWidget(heading)
        container = QWidget()
        container.setObjectName(object_name)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        for item in items:
            row = SpecialIssuePreviewRow(item)
            row.clicked.connect(self.open_workbench)
            layout.addWidget(row)
            self.preview_issue_ids.append(row.issue_id)
            self.preview_rows.append(row)
        self.preview_layout.addWidget(container)
