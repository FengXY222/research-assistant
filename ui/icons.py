"""Load the small, bundled Lucide subset used by the desktop UI."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import sys

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


_VENDORED_ICONS = frozenset(
    {
        "upload",
        "refresh-cw",
        "lock",
        "unlock",
        "trash-2",
        "undo-2",
        "check",
        "x",
        "chevron-down",
        "external-link",
    }
)


def _asset_directory() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return bundle_root / "assets" / "icons" / "lucide"


@lru_cache(maxsize=96)
def _rendered_icon(name: str, color_name: str) -> QIcon:
    path = _asset_directory() / f"{name}.svg"
    if name not in _VENDORED_ICONS or not path.is_file():
        return QIcon()
    if not color_name:
        return QIcon(str(path))
    try:
        svg = path.read_text(encoding="utf-8").replace("currentColor", color_name)
    except (OSError, UnicodeError):
        return QIcon()
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()
    icon = QIcon()
    for size in (16, 20, 24, 32):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def lucide_icon(name: str, color: QColor | str | None = None) -> QIcon:
    """Return a themeable bundled icon, or a null icon for unknown names."""
    normalized = str(name).strip().casefold()
    if normalized not in _VENDORED_ICONS:
        return QIcon()
    if color is None:
        color_name = ""
    else:
        candidate = color if isinstance(color, QColor) else QColor(str(color))
        color_name = candidate.name(QColor.NameFormat.HexRgb) if candidate.isValid() else ""
    return QIcon(_rendered_icon(normalized, color_name))
