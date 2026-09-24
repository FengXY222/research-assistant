"""Shared building blocks for v13.1 pages (headers, filters, badges, labels).

v13.1 起所有页面级 UI 组件集中在这里，页面文件不再各自复制实现。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QWidget,
)


ACCENT_SECTIONS = frozenset(
    {"home", "frontier", "journals", "special_issues", "papers", "achievements", "todo", "notes"}
)


def _accent(value: str) -> str:
    normalized = str(value or "home").strip().casefold()
    return normalized if normalized in ACCENT_SECTIONS else "home"


def polish(widget: QWidget) -> None:
    """Refresh dynamic-property styling immediately on already visible widgets."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class PageHeader(QFrame):
    """Compact one-line page heading with one main action and one overflow menu."""

    def __init__(
        self,
        title: str,
        *,
        accent: str = "home",
        hint: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")
        self.setProperty("accent", _accent(accent))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.layout_row = QHBoxLayout(self)
        self.layout_row.setContentsMargins(10, 7, 7, 7)
        self.layout_row.setSpacing(6)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("pageHeaderTitle")
        self.layout_row.addWidget(self.title_label)
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("pageHeaderHint")
        self.hint_label.setMinimumWidth(0)
        self.hint_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.hint_label.setVisible(bool(hint))
        self.layout_row.addWidget(self.hint_label, 1)
        self.layout_row.addStretch(1)
        self.primary_button: QPushButton | None = None
        self.more_button: EllipsisMenu | None = None

    def set_hint(self, text: str) -> None:
        self.hint_label.setText(str(text or ""))
        self.hint_label.setVisible(bool(text))

    def add_primary_action(
        self,
        text: str,
        callback: Callable[[], None],
        *,
        tooltip: str = "",
    ) -> QPushButton:
        if self.primary_button is not None:
            self.layout_row.removeWidget(self.primary_button)
            self.primary_button.deleteLater()
        button = QPushButton(text)
        button.setObjectName("accentPrimary")
        button.setProperty("accent", self.property("accent"))
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        insert_at = max(0, self.layout_row.count() - (1 if self.more_button else 0))
        self.layout_row.insertWidget(insert_at, button)
        self.primary_button = button
        return button

    def add_overflow_menu(self, tooltip: str = "更多操作") -> "EllipsisMenu":
        if self.more_button is None:
            self.more_button = EllipsisMenu(tooltip=tooltip, parent=self)
            self.layout_row.addWidget(self.more_button)
        return self.more_button


class FilterBar(QFrame):
    """Single-row search, filters and result count used by list pages."""

    def __init__(self, placeholder: str = "搜索", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("filterBar")
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(6)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(placeholder)
        self.search_edit.setMinimumWidth(0)
        self.search_edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.row.addWidget(self.search_edit, 1)
        self.filters: list[QComboBox] = []
        self.count_label = QLabel()
        self.count_label.setObjectName("filterCount")
        self.count_label.setMinimumWidth(0)
        self.count_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.row.addWidget(self.count_label)

    def add_filter(self, choices: list[str] | tuple[str, ...]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(list(choices))
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(4)
        combo.setMinimumWidth(58)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.row.insertWidget(max(1, self.row.count() - 1), combo)
        self.filters.append(combo)
        return combo

    def set_count(self, text: str) -> None:
        self.count_label.setText(str(text or ""))


class StatusBadge(QLabel):
    """Semantic badge; tone is one of success/info/warning/muted."""

    def __init__(self, text: str = "", tone: str = "muted", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("statusBadge")
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        normalized = str(tone or "muted").strip().casefold()
        self.setProperty("tone", normalized if normalized in {"success", "info", "warning", "muted"} else "muted")
        polish(self)


class EllipsisMenu(QPushButton):
    """Consistent overflow button that keeps secondary actions out of headers."""

    def __init__(self, *, tooltip: str = "更多操作", parent: QWidget | None = None) -> None:
        super().__init__("…", parent)
        self.setObjectName("ellipsisButton")
        self.setToolTip(tooltip)
        self._menu = QMenu(self)
        self.setMenu(self._menu)

    def add_action(self, text: str, callback: Callable[[], None], *, enabled: bool = True) -> QAction:
        action = self._menu.addAction(text)
        action.setEnabled(enabled)
        action.triggered.connect(callback)
        return action

    def add_separator(self) -> QAction:
        return self._menu.addSeparator()


class ElidedLabel(QLabel):
    """A one-line label that elides overflow and keeps the full text in a tooltip.

    统一 v13.1 之前 journal_library_page / special_issue_page /
    special_issue_dialog 三处各自的实现，兼容原全部用法：
    ``set_full_text(text)``、``setText(text)``、``full_text()``。
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._full_text = ""
        self.setWordWrap(False)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self._refresh_text()

    def set_full_text(self, text: str) -> None:
        self.setText(text)

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._refresh_text()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(0, super().minimumSizeHint().height())

    def _refresh_text(self) -> None:
        width = self.contentsRect().width()
        visible = self._full_text if width <= 0 else self.fontMetrics().elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            width,
        )
        QLabel.setText(self, visible)
