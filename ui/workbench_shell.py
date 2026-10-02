"""Four-workbench composition for the compact v12 desktop widget."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

@dataclass(frozen=True)
class RouteTarget:
    workbench: str
    anchor: str


PRIMARY_WORKBENCHES = ("home", "work", "papers", "library")
ROUTE_ALIASES: dict[str, RouteTarget] = {
    "home": RouteTarget("home", "overview"),
    "work": RouteTarget("work", "tasks"),
    "todo": RouteTarget("work", "tasks"),
    "notes": RouteTarget("work", "notes"),
    "tools": RouteTarget("work", "tools"),
    "papers": RouteTarget("papers", "submissions"),
    "achievements": RouteTarget("papers", "results"),
    "library": RouteTarget("library", "frontier"),
    "journals": RouteTarget("library", "journals"),
    "frontier": RouteTarget("library", "frontier"),
    "special_issues": RouteTarget("library", "special_issues"),
}


def resolve_route(route: str | RouteTarget, anchor: str | None = None) -> RouteTarget:
    """Map any legacy public route to a v11 workbench without a dead end."""
    if isinstance(route, RouteTarget):
        return RouteTarget(route.workbench, anchor or route.anchor)
    key = str(route or "home").strip().casefold()
    target = ROUTE_ALIASES.get(key, ROUTE_ALIASES["home"])
    return RouteTarget(target.workbench, str(anchor or target.anchor))


class WorkbenchShell(QWidget):
    """Own one set of pages and present it as a single compact widget stack."""

    navigation_requested = Signal(str, str)
    route_changed = Signal(str, str)

    _SECTIONS = {
        "work": (("tasks", "今日任务"), ("notes", "灵感与待读"), ("tools", "工具")),
        "papers": (("submissions", "投稿记录"), ("results", "成果")),
        "library": (("frontier", "每日前沿"), ("journals", "期刊库"), ("special_issues", "特刊征稿")),
    }

    def __init__(self, pages: Mapping[str, QWidget], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.pages = dict(pages)
        self._mode = ""
        self._host: QWidget | None = None
        self._primary_stack: QStackedWidget | None = None
        self._inner_stacks: dict[str, tuple[QStackedWidget, dict[str, int]]] = {}
        self._page_slots: dict[str, tuple[QStackedWidget, int]] = {}
        self._software_scroll = None  # Compatibility sentinel for v11 diagnostics.
        self._current = resolve_route("home")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def current_route(self) -> RouteTarget:
        return self._current

    def set_mode(self, mode: str) -> None:
        # Keep the argument for upgrades and old callers, while v12 always
        # builds the same widget hierarchy.
        mode = "widget"
        if mode == self._mode:
            return
        self._detach_pages()
        self._clear_host()
        self._mode = mode
        self._build_widget_shell()
        self.navigate(self._current)

    def _detach_pages(self) -> None:
        for page in self.pages.values():
            parent = page.parentWidget()
            if parent is not None and parent.layout() is not None:
                parent.layout().removeWidget(page)
            page.setParent(None)

    def _clear_host(self) -> None:
        if self._host is None:
            return
        self._layout.removeWidget(self._host)
        self._host.deleteLater()
        self._host = None
        self._primary_stack = None
        self._inner_stacks = {}
        self._page_slots = {}
        self._software_scroll = None

    def _build_widget_shell(self) -> None:
        host = QWidget()
        host.setObjectName("workbenchWidgetHost")
        root = QVBoxLayout(host)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        stack = QStackedWidget()
        stack.setObjectName("workbenchPrimaryStack")
        home_index = stack.addWidget(self.pages["home"])
        self._page_slots["home"] = (stack, home_index)
        for workbench in ("work", "papers", "library"):
            stack.addWidget(self._make_widget_panel(workbench))
        root.addWidget(stack, 1)
        self._primary_stack = stack
        self._host = host
        self._layout.addWidget(host)

    def _make_widget_panel(self, workbench: str) -> QWidget:
        panel = QWidget()
        panel.setObjectName("workbenchPanel")
        root = QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        chips = QFrame()
        chips.setObjectName("workbenchChips")
        chip_layout = QHBoxLayout(chips)
        chip_layout.setContentsMargins(14, 7, 14, 6)
        chip_layout.setSpacing(5)
        inner = QStackedWidget()
        inner.setObjectName("workbenchAnchorStack")
        anchors: dict[str, int] = {}
        page_keys = {
            "tasks": "todo",
            "notes": "notes",
            "tools": "tools",
            "submissions": "papers",
            "results": "achievements",
            "journals": "journals",
            "frontier": "frontier",
            "special_issues": "special_issues",
        }
        for anchor, label in self._SECTIONS[workbench]:
            page_key = page_keys[anchor]
            if page_key not in self.pages:
                continue
            page = self.pages[page_key]
            index = inner.addWidget(page)
            anchors[anchor] = index
            self._page_slots[page_key] = (inner, index)
            chip = QPushButton(label)
            chip.setObjectName("workbenchChip")
            chip.setCheckable(True)
            chip.setProperty("workbench_anchor", anchor)
            chip.setProperty("accent", anchor)
            # The main window owns lazy-page creation, dirty-page refresh and
            # navigation bookkeeping.  Never bypass it from a child tab.
            chip.clicked.connect(
                lambda _checked=False, value=anchor: self.navigation_requested.emit(
                    workbench, value
                )
            )
            chip_layout.addWidget(chip)
        chip_layout.addStretch(1)
        root.addWidget(chips)
        root.addWidget(inner, 1)
        self._inner_stacks[workbench] = (inner, anchors)
        return panel

    def navigate(self, route: str | RouteTarget, anchor: str | None = None) -> RouteTarget:
        target = resolve_route(route, anchor)
        self._current = target
        if self._mode == "widget" and self._primary_stack is not None:
            primary_index = {"home": 0, "work": 1, "papers": 2, "library": 3}[target.workbench]
            self._primary_stack.setCurrentIndex(primary_index)
            if target.workbench in self._inner_stacks:
                inner, anchors = self._inner_stacks[target.workbench]
                inner.setCurrentIndex(anchors.get(target.anchor, 0))
                panel = self._primary_stack.widget(primary_index)
                for button in panel.findChildren(QPushButton):
                    value = str(button.property("workbench_anchor") or "")
                    if value:
                        button.setChecked(value == target.anchor)
        self.route_changed.emit(target.workbench, target.anchor)
        return target

    def replace_page(self, page_key: str, page: QWidget) -> None:
        """Replace a lightweight placeholder without rebuilding the shell."""

        old_page = self.pages.get(page_key)
        self.pages[page_key] = page
        slot = self._page_slots.get(page_key)
        if slot is None:
            return
        stack, index = slot
        was_current = stack.currentWidget() is old_page
        if old_page is not None:
            stack.removeWidget(old_page)
            old_page.setParent(None)
            old_page.deleteLater()
        inserted_index = stack.insertWidget(index, page)
        self._page_slots[page_key] = (stack, inserted_index)
        if was_current:
            stack.setCurrentWidget(page)
