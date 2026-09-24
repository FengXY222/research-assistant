"""Restrained Qt motion helpers for dense desktop tool surfaces."""

from __future__ import annotations

import ctypes
import os
import sys

from PySide6.QtCore import QEasingCurve, QPoint, QParallelAnimationGroup, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget


def motion_enabled() -> bool:
    """Respect the explicit app override and the Windows animation preference."""
    override = os.environ.get("RESEARCH_ASSISTANT_REDUCE_MOTION", "").strip().casefold()
    if override in {"1", "true", "yes", "on"}:
        return False
    if override in {"0", "false", "no", "off"}:
        return True
    if sys.platform == "win32":
        animations_enabled = ctypes.c_int(1)
        try:
            ok = ctypes.windll.user32.SystemParametersInfoW(
                0x1042,  # SPI_GETCLIENTAREAANIMATION
                0,
                ctypes.byref(animations_enabled),
                0,
            )
            if ok:
                return bool(animations_enabled.value)
        except (AttributeError, OSError):
            pass
    return True


def animate_widget_enter(
    widget: QWidget,
    *,
    distance: int = 4,
    duration_ms: int = 170,
) -> QParallelAnimationGroup | None:
    """Fade and translate a widget without ever animating its dimensions."""
    if not motion_enabled():
        return None
    previous = getattr(widget, "_v12_enter_animation", None)
    if isinstance(previous, QParallelAnimationGroup):
        previous.stop()

    target = widget.pos()
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    widget.move(target + QPoint(0, max(-12, min(12, int(distance)))))

    group = QParallelAnimationGroup(widget)
    opacity = QPropertyAnimation(effect, b"opacity", group)
    opacity.setStartValue(0.0)
    opacity.setEndValue(1.0)
    opacity.setDuration(max(0, min(500, int(duration_ms))))
    opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
    position = QPropertyAnimation(widget, b"pos", group)
    position.setStartValue(widget.pos())
    position.setEndValue(target)
    position.setDuration(max(0, min(500, int(duration_ms))))
    position.setEasingCurve(QEasingCurve.Type.OutCubic)
    group.addAnimation(opacity)
    group.addAnimation(position)
    setattr(widget, "_v12_enter_animation", group)

    def cleanup() -> None:
        try:
            widget.move(target)
            current = getattr(widget, "_v12_enter_animation", None)
            if current is group:
                setattr(widget, "_v12_enter_animation", None)
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            # Lazy page replacement may retire the placeholder before its
            # restrained entrance animation posts this cleanup callback.
            return

    group.finished.connect(lambda: QTimer.singleShot(0, cleanup))
    group.start()
    return group
