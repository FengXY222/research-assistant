"""Small reusable drag handles for manually ordering local lists."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget


ORDER_MIME = "application/x-research-assistant-order"


def decode_order_payload(mime_data: QMimeData) -> tuple[str, str]:
    if not mime_data.hasFormat(ORDER_MIME):
        return "", ""
    try:
        scope, item_id = bytes(mime_data.data(ORDER_MIME)).decode("utf-8").split("\n", 1)
    except (UnicodeDecodeError, ValueError):
        return "", ""
    return scope, item_id


class OrderDragHandle(QPushButton):
    """The dedicated drag handle prevents ordinary row clicks from starting a drag."""

    def __init__(self, item_id: str, scope: str, parent: QWidget | None = None) -> None:
        # Use the familiar bidirectional reorder glyph.  The old text label
        # was both ambiguous and visually noisy in the compact list rows.
        super().__init__("⥮", parent)
        self.item_id = str(item_id)
        self.scope = scope
        self._drag_origin = QPoint()
        self.setObjectName("dragHandle")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("按住拖动排序")
        self.setFixedWidth(24)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and not self._drag_origin.isNull()
            and (event.position().toPoint() - self._drag_origin).manhattanLength() >= 6
        ):
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setData(ORDER_MIME, f"{self.scope}\n{self.item_id}".encode("utf-8"))
            drag.setMimeData(mime_data)
            drag.setPixmap(self.grab())
            drag.setHotSpot(event.position().toPoint())
            drag.exec(Qt.DropAction.MoveAction)
            self._drag_origin = QPoint()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = QPoint()
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()


class ReorderableColumn(QWidget):
    """A compact vertical drop area whose direct row widgets can be reordered."""

    order_changed = Signal(list)

    def __init__(self, scope: str, parent: QWidget | None = None, *, include_stretch: bool = True) -> None:
        super().__init__(parent)
        self.scope = scope
        self._rows: list[QWidget] = []
        self._placeholder: QWidget | None = None
        self.setAcceptDrops(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.rows_layout = QVBoxLayout(self)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(7)
        if include_stretch:
            self.rows_layout.addStretch()

    def add_row(self, row: QWidget, item_id: str) -> None:
        row.setProperty("order_item_id", str(item_id))
        self.rows_layout.insertWidget(len(self._rows), row)
        self._rows.append(row)

    def clear_rows(self) -> None:
        for row in self._rows:
            self.rows_layout.removeWidget(row)
            # Detach immediately.  QList/QScroll based workbenches frequently
            # rebuild after filters and theme changes; leaving a deferred
            # child behind keeps stale geometry in the live widget tree.
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()
        if self._placeholder is not None:
            self.rows_layout.removeWidget(self._placeholder)
            self._placeholder.setParent(None)
            self._placeholder.hide()
            self._placeholder.deleteLater()
            self._placeholder = None

    def set_placeholder(self, widget: QWidget) -> None:
        if self._placeholder is not None:
            self.rows_layout.removeWidget(self._placeholder)
            self._placeholder.hide()
            self._placeholder.deleteLater()
        self._placeholder = widget
        self.rows_layout.insertWidget(0, widget)

    def dragEnterEvent(self, event) -> None:
        scope, _item_id = decode_order_payload(event.mimeData())
        if scope == self.scope:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        scope, _item_id = decode_order_payload(event.mimeData())
        if scope == self.scope:
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        scope, item_id = decode_order_payload(event.mimeData())
        if scope != self.scope:
            event.ignore()
            return
        source_index = next(
            (index for index, row in enumerate(self._rows) if str(row.property("order_item_id")) == item_id),
            -1,
        )
        if source_index < 0:
            event.ignore()
            return
        target_index = len(self._rows)
        for index, row in enumerate(self._rows):
            if event.position().y() < row.geometry().center().y():
                target_index = index
                break
        if target_index > source_index:
            target_index -= 1
        if target_index != source_index:
            row = self._rows.pop(source_index)
            self.rows_layout.removeWidget(row)
            self._rows.insert(target_index, row)
            self.rows_layout.insertWidget(target_index, row)
            self.order_changed.emit([str(row.property("order_item_id")) for row in self._rows])
        event.acceptProposedAction()
