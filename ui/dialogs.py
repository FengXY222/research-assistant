"""Readable confirmation dialogs for the dark desktop widget."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget


class UndoToast(QFrame):
    """A short, non-blocking escape hatch after a destructive action."""

    def __init__(self, parent: QWidget, message: str, undo: Callable[[], None]) -> None:
        super().__init__(parent)
        self._undo = undo
        self.setObjectName("undoToast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 7, 8, 7)
        layout.setSpacing(9)
        label = QLabel(message)
        label.setObjectName("undoToastText")
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        button = QPushButton("撤销")
        button.setObjectName("undoToastButton")
        button.clicked.connect(self._restore)
        layout.addWidget(button)
        self.setStyleSheet(
            """
            #undoToast { background: #18304b; border: 1px solid #4d769c; border-radius: 8px; }
            #undoToastText { color: #eef6ff; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 12px; }
            #undoToastButton { background: #57d89a; color: #082016; border: 0; border-radius: 5px; padding: 5px 10px; font-weight: 700; }
            #undoToastButton:hover { background: #7be9ae; }
            """
        )
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(self.close)

    def show_for_a_moment(self) -> None:
        self.adjustSize()
        target = self.parentWidget()
        if target is not None:
            width = min(max(self.sizeHint().width(), 250), max(250, target.width() - 28))
            self.resize(width, self.sizeHint().height())
            self.move(max(14, target.width() - self.width() - 14), max(14, target.height() - self.height() - 14))
        self.show()
        self.raise_()
        self._timer.start()

    def _restore(self) -> None:
        self._timer.stop()
        self._undo()
        self.close()


def show_undo_toast(parent: QWidget, message: str, undo: Callable[[], None]) -> UndoToast:
    target = parent.window() if parent.window() is not None else parent
    existing = target.findChild(UndoToast, "undoToast")
    if existing is not None:
        existing.close()
        existing.deleteLater()
    toast = UndoToast(target, message, undo)
    toast.show_for_a_moment()
    return toast


def confirm_delete(parent: QWidget, title: str, detail: str) -> bool:
    dialog = QMessageBox(parent)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setWindowTitle(title)
    dialog.setText("确认删除？")
    dialog.setInformativeText(detail)
    dialog.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
    dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
    dialog.button(QMessageBox.StandardButton.Yes).setText("删除")
    dialog.button(QMessageBox.StandardButton.Cancel).setText("取消")
    dialog.setStyleSheet(
        """
        QMessageBox { background: #101a2c; color: #edf4ff; min-width: 330px; }
        QMessageBox QLabel { color: #edf4ff; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; }
        QMessageBox QPushButton { background: #263b59; color: #edf4ff; border: 1px solid #405b80; border-radius: 6px; padding: 7px 18px; min-width: 66px; }
        QMessageBox QPushButton:hover { background: #355172; }
        QMessageBox QPushButton[text="删除"] { background: #c94f5c; color: #ffffff; border-color: #df6c77; }
        QMessageBox QPushButton[text="删除"]:hover { background: #e06672; }
        """
    )
    return dialog.exec() == QMessageBox.StandardButton.Yes
