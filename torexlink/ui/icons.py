"""Small inline SVG icon set, rendered without external assets."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 24x24 stroke icons. `{color}` and `{width}` are substituted at render time.
_PATHS: dict[str, str] = {
    "home": '<path d="M3.5 10.5 12 3.5l8.5 7"/><path d="M5.5 9.5v10h13v-10"/><path d="M9.5 19.5v-6h5v6"/>',
    "connect": '<circle cx="6" cy="12" r="2.6"/><circle cx="18" cy="6.5" r="2.6"/><circle cx="18" cy="17.5" r="2.6"/><path d="M8.5 11.2l7-3.6M8.5 12.8l7 3.6"/>',
    "devices": '<rect x="2.8" y="4.5" width="13" height="9.5" rx="1.6"/><path d="M6.5 18.5h6M9.3 14v4.5"/><rect x="15.5" y="9" width="5.7" height="10" rx="1.1"/>',
    "settings": '<path d="M12 15.1a3.1 3.1 0 1 0 0-6.2 3.1 3.1 0 0 0 0 6.2Z"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 8.9 19.3a1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.7 15a1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.7 8.9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.54 1.7 1.7 0 0 0 10 3V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9v.1a1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 1 1 0 4h-.1A1.7 1.7 0 0 0 19.4 15Z"/>',
    "logs": '<path d="M7 4.5h10a2 2 0 0 1 2 2v13H7a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2Z"/><path d="M9 8.5h6M9 12h6M9 15.5h4"/>',
    "pulse": '<path d="M2.5 12h4l2.2-6 4.6 12 2.2-6h6"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.3 4.3l1.4 1.4M18.3 18.3l1.4 1.4M2.5 12h2M19.5 12h2M4.3 19.7l1.4-1.4M18.3 5.7l1.4-1.4"/>',
    "moon": '<path d="M20.3 14.6A8.4 8.4 0 0 1 9.4 3.7 8.5 8.5 0 1 0 20.3 14.6Z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "copy": '<rect x="8.5" y="8.5" width="12" height="12" rx="2"/><path d="M15.5 8.5v-3a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>',
    "check": '<path d="m4.5 12.5 5 5 10-11"/>',
    "check_circle": '<circle cx="12" cy="12" r="9"/><path d="m8 12 2.7 2.7L16.5 9"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "x_circle": '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_right": '<path d="m9 6 6 6-6 6"/>',
    "arrow_right": '<path d="M4 12h16M14 6l6 6-6 6"/>',
    "arrow_left": '<path d="M20 12H4M10 6l-6 6 6 6"/>',
    "refresh": '<path d="M20 7v5h-5"/><path d="M19 12a7.5 7.5 0 1 0-2.2 5.3"/>',
    "shield": '<path d="M12 3.2 20 6v5.5c0 4.7-3.4 8.3-8 9.8-4.6-1.5-8-5.1-8-9.8V6Z"/><path d="m8.5 12 2.2 2.2 4.8-5"/>',
    "lock": '<rect x="4.5" y="10" width="15" height="10.5" rx="2"/><path d="M8 10V7.5a4 4 0 0 1 8 0V10"/><path d="M12 14v2.5"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="2.2"/><path d="M6 10h.01M9 10h.01M12 10h.01M15 10h.01M18 10h.01M6 13h.01M18 13h.01M9 13h6M8 16h8"/>',
    "mouse": '<rect x="7" y="2.8" width="10" height="18.4" rx="5"/><path d="M12 6v4"/>',
    "clipboard": '<rect x="5" y="4.5" width="14" height="16" rx="2"/><path d="M9 4.5V3.2h6v1.3M9 9.5h6M9 13h6M9 16.5h4"/>',
    "wifi": '<path d="M3 8.5a14 14 0 0 1 18 0M6 12a9.5 9.5 0 0 1 12 0M9.2 15.3a5 5 0 0 1 5.6 0"/><circle cx="12" cy="19" r="1" fill="{color}" stroke="none"/>',
    "network": '<rect x="9" y="2.7" width="6" height="5" rx="1"/><rect x="2.8" y="16.3" width="6" height="5" rx="1"/><rect x="15.2" y="16.3" width="6" height="5" rx="1"/><path d="M12 7.7v4.1M5.8 16.3v-2.1a1.5 1.5 0 0 1 1.5-1.5h9.4a1.5 1.5 0 0 1 1.5 1.5v2.1"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3.2 9.5h17.6M3.2 14.5h17.6"/><path d="M12 3a14 14 0 0 1 0 18 14 14 0 0 1 0-18Z"/>',
    "pin": '<path d="m9 3 6 6-1.2 1.2-1.8-.4-3.6 3.6.7 3.2-1.4 1.4-3.1-3.1L.2 18.3l-1.4-1.4 3.6-3.6-.7-3.2L5.3 8.7l-.4-1.8Z" transform="translate(3 2) scale(.82)"/>',
    "scan": '<path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3M7 12h10"/>',
    "qr": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14h1v1M14 20h1M18 18h3v3h-3z"/>',
    "plug": '<path d="M8 3v5M16 3v5M6.5 8h11v3a5.5 5.5 0 0 1-11 0Z"/><path d="M12 16.5V21"/>',
    "power": '<path d="M12 3v9"/><path d="M6.4 6.5a8 8 0 1 0 11.2 0"/>',
    "trash": '<path d="M4 7h16M9 7V4.5h6V7M6.5 7l.8 12.5h9.4L17.5 7M10 10.5v6M14 10.5v6"/>',
    "external": '<path d="M14 4h6v6M20 4l-9 9"/><path d="M19 14v5a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 3 19V6a1.5 1.5 0 0 1 1.5-1.5H10"/>',
    "sparkles": '<path d="M12 3.5 13.8 9l5.5 1.8-5.5 1.8L12 18l-1.8-5.4L4.7 10.8 10.2 9Z"/><path d="M19 3v3M17.5 4.5h3M5 17v3M3.5 18.5h3"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 10.5V16M12 7.5h.01"/>',
    "alert": '<path d="M10.3 4.2 2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
    "eye": '<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z"/><circle cx="12" cy="12" r="2.6"/>',
    "camera": '<path d="M4 7.5h3l1.4-2h7.2l1.4 2h3a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 20 19.5H4A1.5 1.5 0 0 1 2.5 18V9A1.5 1.5 0 0 1 4 7.5Z"/><circle cx="12" cy="13.2" r="3.4"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5M4 16v3a1.5 1.5 0 0 0 1.5 1.5h13A1.5 1.5 0 0 0 20 19v-3"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.3 15.3 5 5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5.3l3.5 2"/>',
    "key": '<circle cx="8" cy="15.5" r="4"/><path d="m11 12.5 8-8M16 7l2.5 2.5M18.5 4.5 21 7"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "laptop": '<rect x="4" y="4" width="16" height="11" rx="1.6"/><path d="M2.5 18.5h19M9 18.5h6"/>',
    "monitor": '<rect x="2.5" y="3.5" width="19" height="13" rx="1.8"/><path d="M8 20.5h8M12 16.5v4"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "folder": '<path d="M3 7.5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    "download": '<path d="M12 3v12M7 10l5 5 5-5M4 18v2.5h16V18"/>',
    "menu": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "arrow_up_right": '<path d="M7 17 17 7M8 7h9v9"/>',
    "link": '<path d="M9.5 14.5 14.5 9.5"/><path d="M7.5 17.5H6a4.5 4.5 0 0 1 0-9h3M16.5 6.5H18a4.5 4.5 0 0 1 0 9h-3"/>',
    "cursor": '<path d="m5 3 14 8.5-6.2 1.2L10 19Z"/>',
    "switch": '<path d="M4 8h13M13 4l4 4-4 4M20 16H7M11 12l-4 4 4 4"/>',
}

LOGO_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#8B7CFF"/>
      <stop offset="1" stop-color="#4FDBBE"/>
    </linearGradient>
  </defs>
  <rect x="3" y="3" width="58" height="58" rx="17" fill="url(#g)"/>
  <path d="M22 20h24M34 20v26" stroke="#0B0E18" stroke-width="6" stroke-linecap="round" opacity=".9"/>
  <circle cx="43" cy="43" r="7" fill="#0B0E18" opacity=".92"/>
  <circle cx="43" cy="43" r="2.6" fill="#58E1C4"/>
</svg>
"""


def _svg(name: str, color: str, stroke: float = 1.7) -> bytes:
    body = _PATHS.get(name)
    if body is None:
        body = _PATHS["info"]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    ).encode()


@lru_cache(maxsize=256)
def pixmap(name: str, color: str, size: int = 20, stroke: float = 1.7) -> QPixmap:
    scale = 2
    image = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_svg(name, color, stroke)))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(scale)
    return result


@lru_cache(maxsize=256)
def icon(name: str, color: str, size: int = 20, stroke: float = 1.7) -> QIcon:
    return QIcon(pixmap(name, color, size, stroke))


@lru_cache(maxsize=16)
def logo_pixmap(size: int = 44) -> QPixmap:
    scale = 2
    image = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(LOGO_SVG.encode("utf-8")))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    result = QPixmap.fromImage(image)
    result.setDevicePixelRatio(scale)
    return result


@lru_cache(maxsize=16)
def logo_icon() -> QIcon:
    return QIcon(logo_pixmap(128))


__all__ = ["icon", "logo_icon", "logo_pixmap", "pixmap"]
