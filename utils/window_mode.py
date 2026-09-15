"""Pure window policy for the single v12 desktop-widget experience."""

from __future__ import annotations


def normalize_application_mode(value: object) -> str:
    """Migrate every legacy application-mode value to the v12 widget."""
    return "widget"


def mode_window_key(mode: object) -> str:
    return "widget_window"


def mode_minimum_size(mode: object) -> tuple[int, int]:
    return (400, 480)


def should_hide_to_tray(mode: object) -> bool:
    """The compact widget keeps its established tray behavior."""
    return True
