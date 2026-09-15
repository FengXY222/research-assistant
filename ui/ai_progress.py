"""Small, consistent progress surface for background AI work."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout, QWidget


class AiProgressPanel(QFrame):
    """A compact progress bar that remains readable in widget and dialog modes."""

    def __init__(self, parent: QWidget | None = None, *, object_name: str = "aiProgressPanel") -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setVisible(False)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(), self.sizePolicy().verticalPolicy())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(3)

        self.status = QLabel("")
        self.status.setObjectName("aiProgressStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.progress = QProgressBar()
        self.progress.setObjectName("aiProgressBar")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("%p%")
        self.progress.setMinimumHeight(10)
        layout.addWidget(self.progress)

    def begin(self, message: str = "AI 正在处理…") -> None:
        self.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText(str(message))

    def update(self, message: str = "", value: int | None = None) -> None:
        self.setVisible(True)
        if value is not None:
            self.progress.setValue(max(0, min(100, int(value))))
        if message:
            self.status.setText(str(message))

    def complete(self, message: str = "AI 处理完成。") -> None:
        self.setVisible(True)
        self.progress.setValue(100)
        self.status.setText(str(message))

    def fail(self, message: str) -> None:
        self.setVisible(True)
        self.status.setText(str(message))
