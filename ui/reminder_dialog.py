from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class SubmissionReminderDialog(QDialog):
    """Actionable 15-day status reminders that never become permanent noise."""

    snoozed = Signal(list, int)
    handled = Signal(list)

    def __init__(self, reminders: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._reminders = {str(item.get("id", "")): dict(item) for item in reminders if str(item.get("id", ""))}
        self._rows: dict[str, QFrame] = {}
        self._close_emitted = False
        self.setWindowTitle("投稿状态提醒")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(420)
        self.setMaximumWidth(560)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QLabel { color: #f4f6ff; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; }
            #reminderTitle { font-size: 21px; font-weight: 700; color: #ffffff; }
            #reminderHint { color: #aebbd8; font-size: 12px; }
            #reminderEntry { background: #172340; border: 1px solid #50668c; border-radius: 7px; }
            #reminderPaper { color: #ffffff; font-size: 14px; font-weight: 600; }
            #reminderMeta { color: #88dfff; font-size: 12px; }
            #reminderDays { color: #ffd66b; font-size: 16px; font-weight: 700; }
            #reminderLater { background: #263b5b; color: #dbe8fb; border: 1px solid #557199; border-radius: 5px; padding: 5px 9px; }
            #reminderDone { background: #39d783; color: #061a11; border: 1px solid #39d783; border-radius: 5px; padding: 5px 9px; font-weight: 700; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 19)
        root.setSpacing(10)

        title = QLabel("投稿状态提醒")
        title.setObjectName("reminderTitle")
        root.addWidget(title)
        hint = QLabel("以下期刊的当前状态已超过 15 天未更新。可稍后提醒，或确认已处理来重置本轮提醒。")
        hint.setObjectName("reminderHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.entries_layout = QVBoxLayout()
        self.entries_layout.setSpacing(8)
        root.addLayout(self.entries_layout)
        for reminder in self._reminders.values():
            self._add_entry(reminder)

    def _add_entry(self, reminder: dict) -> None:
        reminder_id = str(reminder.get("id", ""))
        if not reminder_id:
            return
        entry = QFrame()
        entry.setObjectName("reminderEntry")
        layout = QVBoxLayout(entry)
        layout.setContentsMargins(13, 10, 13, 10)
        layout.setSpacing(3)
        paper = QLabel(f"论文：{reminder['paper_title']}")
        paper.setObjectName("reminderPaper")
        paper.setWordWrap(True)
        layout.addWidget(paper)
        meta = QLabel(f"期刊：{reminder['journal_name']}  ·  当前状态：{reminder['status']}")
        meta.setObjectName("reminderMeta")
        meta.setWordWrap(True)
        layout.addWidget(meta)
        days = QLabel(f"当前状态已 {reminder['days']} 天未更新")
        days.setObjectName("reminderDays")
        layout.addWidget(days)
        actions = QHBoxLayout()
        actions.addStretch()
        later = QPushButton("稍后 3 天")
        later.setObjectName("reminderLater")
        later.clicked.connect(lambda _checked=False, item_id=reminder_id: self._snooze(item_id, 3))
        actions.addWidget(later)
        done = QPushButton("已处理")
        done.setObjectName("reminderDone")
        done.setToolTip("将状态更新时间记为今天，并留下“提醒已处理”时间线节点")
        done.clicked.connect(lambda _checked=False, item_id=reminder_id: self._mark_handled(item_id))
        actions.addWidget(done)
        layout.addLayout(actions)
        self.entries_layout.addWidget(entry)
        self._rows[reminder_id] = entry

    def _remove_entry(self, reminder_id: str) -> None:
        self._reminders.pop(reminder_id, None)
        row = self._rows.pop(reminder_id, None)
        if row is not None:
            self.entries_layout.removeWidget(row)
            row.deleteLater()
        if not self._reminders:
            self._close_emitted = True
            self.accept()

    def _snooze(self, reminder_id: str, days: int) -> None:
        if reminder_id not in self._reminders:
            return
        self.snoozed.emit([reminder_id], days)
        self._remove_entry(reminder_id)

    def _mark_handled(self, reminder_id: str) -> None:
        if reminder_id not in self._reminders:
            return
        self.handled.emit([reminder_id])
        self._remove_entry(reminder_id)

    def closeEvent(self, event) -> None:
        # Closing a reminder is a gentle one-day snooze, never a permanent
        # dismissal.  It prevents the dialog reopening in a loop while still
        # keeping follow-up visible tomorrow.
        if not self._close_emitted and self._reminders:
            self.snoozed.emit(list(self._reminders), 1)
        self._close_emitted = True
        super().closeEvent(event)


class ReadySubmissionDialog(QDialog):
    """Topmost daily prompt listing papers and journals ready to submit."""

    dismissed = Signal(list)

    def __init__(self, reminders: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._reminders = reminders
        self.setWindowTitle("准备投稿提示")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(420)
        self.setMaximumWidth(560)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QDialog { background: #101a36; color: #f4f6ff; }
            QLabel { color: #f4f6ff; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; }
            #readyTitle { font-size: 21px; font-weight: 700; color: #ffffff; }
            #readyHint { color: #aebbd8; font-size: 12px; }
            #readyEntry { background: #172340; border: 1px solid #50668c; border-radius: 7px; }
            #readyPaper { color: #ffffff; font-size: 14px; font-weight: 600; }
            #readyJournal { color: #88dfff; font-size: 13px; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 19)
        root.setSpacing(10)
        title = QLabel("准备投稿提示")
        title.setObjectName("readyTitle")
        root.addWidget(title)
        hint = QLabel("以下论文已标记为“准备投稿”。请确认目标期刊与投稿材料；点击右上角 × 可关闭今日提示。")
        hint.setObjectName("readyHint")
        hint.setWordWrap(True)
        root.addWidget(hint)
        for reminder in self._reminders:
            entry = QFrame()
            entry.setObjectName("readyEntry")
            layout = QVBoxLayout(entry)
            layout.setContentsMargins(13, 10, 13, 10)
            layout.setSpacing(3)
            paper = QLabel(f"论文：{reminder['paper_title']}")
            paper.setObjectName("readyPaper")
            paper.setWordWrap(True)
            layout.addWidget(paper)
            journal = QLabel(f"建议投稿期刊：{reminder['journal_name']}")
            journal.setObjectName("readyJournal")
            journal.setWordWrap(True)
            layout.addWidget(journal)
            publisher = str(reminder.get("publisher", "")).strip()
            if publisher:
                publisher_label = QLabel(f"出版社：{publisher}")
                publisher_label.setObjectName("readyHint")
                layout.addWidget(publisher_label)
            root.addWidget(entry)

    def closeEvent(self, event) -> None:
        self.dismissed.emit([str(item["id"]) for item in self._reminders])
        super().closeEvent(event)
