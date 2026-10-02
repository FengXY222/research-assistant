"""Keep interactive card widgets only for rows close to the viewport."""
from __future__ import annotations

from copy import deepcopy
from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, Qt, QTimer, Signal, QEvent
from PySide6.QtWidgets import QListView, QAbstractItemView, QLabel
from ui.reorder import decode_order_payload


class CardModel(QAbstractListModel):
    def __init__(self, parent=None, height=180):
        super().__init__(parent)
        self.records = []
        self.heights = {}
        self.default_height = height

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.records):
            return None
        record = self.records[index.row()]
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(100, self.heights.get(str(record.get("id")), self.default_height))
        if role == Qt.ItemDataRole.DisplayRole:
            return str(record.get("title", ""))


class VirtualCardList(QListView):
    order_changed = Signal(list)

    def __init__(self, factory, parent=None, *, height=180, scope=""):
        super().__init__(parent)
        self.factory = factory
        self.scope = scope
        self.cards = {}
        self.card_model = CardModel(self, height)
        self.setModel(self.card_model)
        self.setSpacing(4)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAcceptDrops(bool(scope))
        self._mount_timer = QTimer(self)
        self._mount_timer.setSingleShot(True)
        self._mount_timer.timeout.connect(self.mount_visible)
        self.verticalScrollBar().valueChanged.connect(self.schedule_mount)
        self._empty_label = QLabel(self.viewport())
        self._empty_label.setObjectName("emptyLabel")
        self._empty_label.setWordWrap(True)
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.hide()

    def set_empty_text(self, text):
        self._empty_label.setText(str(text))
        self._empty_label.setGeometry(self.viewport().rect())
        self._empty_label.setVisible(bool(text) and not self.card_model.records)

    def schedule_mount(self, *_args):
        if not self._mount_timer.isActive():
            self._mount_timer.start(0)

    def set_records(self, records):
        records = deepcopy(records)
        live_ids = {str(row.get("id")) for row in records}
        self.card_model.heights = {key: height for key, height in self.card_model.heights.items() if key in live_ids}
        old = self.card_model.records
        same_order = [str(row.get("id")) for row in old] == [str(row.get("id")) for row in records]
        if same_order:
            for row, (before, after) in enumerate(zip(old, records)):
                if before == after:
                    continue
                self._unmount(row)
                self.card_model.records[row] = after
                index = self.card_model.index(row)
                self.card_model.dataChanged.emit(index, index)
        else:
            for row in list(self.cards):
                self._unmount(row)
            self.card_model.beginResetModel()
            self.card_model.records = records
            self.card_model.endResetModel()
        self.doItemsLayout()
        self.mount_visible()
        self._empty_label.setVisible(bool(self._empty_label.text()) and not records)

    def _unmount(self, row):
        widget = self.cards.pop(row, None)
        if widget is not None:
            self.setIndexWidget(self.card_model.index(row), None)
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()

    def card_for_id(self, item_id):
        row = next((index for index, record in enumerate(self.card_model.records) if str(record.get("id")) == str(item_id)), -1)
        if row < 0:
            return None
        self.scrollTo(self.card_model.index(row), QAbstractItemView.ScrollHint.PositionAtTop)
        self.mount_visible()
        return self.cards.get(row)

    def ensureWidgetVisible(self, widget, xmargin=0, ymargin=12):
        position = widget.mapTo(self.viewport(), widget.rect().topLeft())
        bar = self.verticalScrollBar()
        if position.y() < ymargin:
            bar.setValue(bar.value() + position.y() - ymargin)
        elif position.y() + widget.height() > self.viewport().height() - ymargin:
            bar.setValue(bar.value() + position.y() + widget.height() - self.viewport().height() + ymargin)

    def mount_visible(self):
        count = self.card_model.rowCount()
        viewport = self.viewport().rect()
        low, high = 0, count
        while low < high:
            middle = (low + high) // 2
            if self.visualRect(self.card_model.index(middle)).bottom() < 0:
                low = middle + 1
            else:
                high = middle
        first = low
        wanted = set()
        # Walk only visible rows, with a small prefetch margin.
        for row in range(max(0, first - 1), min(count, first + 32)):
            rect = self.visualRect(self.card_model.index(row))
            if rect.top() > viewport.bottom() + self.card_model.default_height:
                break
            if rect.bottom() >= -self.card_model.default_height:
                wanted.add(row)
        for row in set(self.cards) - wanted:
            self._unmount(row)
        for row in sorted(wanted):
            if row not in self.cards:
                widget = self.factory(self.card_model.records[row])
                self.cards[row] = widget
                widget.installEventFilter(self)
                self.setIndexWidget(self.card_model.index(row), widget)
        self._update_heights()

    def _update_heights(self):
        changed = False
        for row, widget in self.cards.items():
            width = max(150, self.viewport().width() - 12)
            layout = widget.layout()
            height = layout.totalHeightForWidth(width) if layout and layout.hasHeightForWidth() else widget.sizeHint().height()
            height = max(60, height)
            key = str(self.card_model.records[row].get("id"))
            if abs(self.card_model.heights.get(key, self.card_model.default_height) - height) > 2:
                self.card_model.heights[key] = height
                changed = True
        if changed:
            self.doItemsLayout()
            self.schedule_mount()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.LayoutRequest:
            self.schedule_mount()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_empty_label"):
            self._empty_label.setGeometry(self.viewport().rect())
        self.schedule_mount()

    def showEvent(self, event):
        super().showEvent(event)
        self.schedule_mount()

    def dragEnterEvent(self, event):
        scope, _ = decode_order_payload(event.mimeData())
        if self.scope and scope == self.scope:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        scope, item_id = decode_order_payload(event.mimeData())
        if scope != self.scope:
            event.ignore()
            return
        ids = [str(row.get("id")) for row in self.card_model.records]
        if item_id not in ids:
            event.ignore()
            return
        target = self.indexAt(event.position().toPoint()).row()
        ids.remove(item_id)
        ids.insert(len(ids) if target < 0 else target, item_id)
        self.order_changed.emit(ids)
        event.acceptProposedAction()
