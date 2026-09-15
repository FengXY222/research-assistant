"""Small native UI foundations used by the widget-only v12 surface."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget


def test_selected_lucide_assets_load_as_real_icons() -> None:
    from ui.icons import lucide_icon

    QApplication.instance() or QApplication([])
    for name in ("upload", "refresh-cw", "lock", "unlock", "trash-2", "undo-2", "check", "x"):
        assert not lucide_icon(name, "#227a5b").isNull()
    assert lucide_icon("not-vendored").isNull()


def test_motion_can_be_disabled_and_never_changes_widget_size(monkeypatch) -> None:
    from ui.motion import animate_widget_enter, motion_enabled

    app = QApplication.instance() or QApplication([])
    widget = QWidget()
    widget.resize(240, 120)
    original_size = widget.size()
    monkeypatch.setenv("RESEARCH_ASSISTANT_REDUCE_MOTION", "1")

    assert motion_enabled() is False
    animate_widget_enter(widget)
    app.processEvents()
    assert widget.size() == original_size
