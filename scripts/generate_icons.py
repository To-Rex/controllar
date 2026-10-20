#!/usr/bin/env python3
"""Render the ToRexLink app icon into PNG, ICNS and ICO assets."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "torexlink" / "resources" / "torexlink.svg"
OUT = ROOT / "packaging" / "assets"
RUNTIME_PNG = ROOT / "torexlink" / "resources" / "torexlink.png"


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    OUT.mkdir(parents=True, exist_ok=True)
    renderer = QSvgRenderer(QByteArray(SVG.read_bytes()))
    if not renderer.isValid():
        raise SystemExit("invalid logo SVG")

    def render(size: int) -> QImage:
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        return image

    sizes = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
    rendered: dict[int, QImage] = {}
    for size in sizes:
        image = render(size)
        rendered[size] = image
        image.save(str(OUT / f"ToRexLink-{size}.png"), "PNG")
    rendered[512].save(str(RUNTIME_PNG), "PNG")

    # Pillow is used only for the compact Windows .ico.
    from PIL import Image

    base_icon = Image.open(OUT / "ToRexLink-256.png").convert("RGBA")
    base_icon.save(
        OUT / "ToRexLink.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    if sys.platform == "darwin" and shutil.which("iconutil"):
        iconset = OUT / "ToRexLink.iconset"
        if iconset.exists():
            shutil.rmtree(iconset)
        iconset.mkdir(parents=True)
        mapping = {
            16: ["icon_16x16.png"],
            32: ["icon_16x16@2x.png", "icon_32x32.png"],
            64: ["icon_32x32@2x.png"],
            128: ["icon_128x128.png"],
            256: ["icon_128x128@2x.png", "icon_256x256.png"],
            512: ["icon_256x256@2x.png", "icon_512x512.png"],
            1024: ["icon_512x512@2x.png"],
        }
        for size, names in mapping.items():
            for name in names:
                (OUT / "ToRexLink.iconset" / name).write_bytes((OUT / f"ToRexLink-{size}.png").read_bytes())
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "ToRexLink.icns")],
            check=True,
        )
        shutil.rmtree(iconset)
    else:
        print("iconutil unavailable; .icns was not generated (Windows still gets .ico)")

    print(f"Wrote icons to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
