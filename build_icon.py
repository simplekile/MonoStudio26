"""
Generate app.ico from monostudio_data/icons/logo.svg for EXE and installer.
Requires: PySide6, Pillow. Run from repo root: python build_icon.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

try:
    from PIL import Image
except ImportError:
    print("Pillow required for ICO output: pip install Pillow")
    sys.exit(1)


def main() -> int:
    repo = Path(__file__).resolve().parent
    logo_svg = repo / "monostudio_data" / "icons" / "logo.svg"
    out_ico = repo / "monostudio_data" / "icons" / "app.ico"

    if not logo_svg.is_file():
        print(f"Logo not found: {logo_svg}")
        return 1

    svg_bytes = logo_svg.read_bytes()
    # Replace currentColor with white so icon is visible on dark/light backgrounds
    svg_text = svg_bytes.decode("utf-8").replace("currentColor", "#ffffff")
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    if not renderer.isValid():
        print("Invalid SVG")
        return 1

    # Multi-size ICO for EXE embedding + Windows Explorer (cache may prefer 256/48/32/16).
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    images = []
    for w, h in sizes:
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        renderer.render(p, QRect(0, 0, w, h))
        p.end()
        # QImage -> PNG bytes -> PIL (avoids ARGB32 byte-order issues)
        buf = QBuffer()
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        buf.close()
        pil = Image.open(io.BytesIO(buf.data().data())).convert("RGBA")
        images.append(pil)

    if not images:
        return 1

    out_ico.parent.mkdir(parents=True, exist_ok=True)
    # Save ICO: first image is base; append smaller sizes
    base = images[0]
    extra = images[1:] if len(images) > 1 else []
    base.save(
        str(out_ico),
        format="ICO",
        sizes=[(w, h) for (w, h) in sizes[: len(images)]],
        append_images=extra,
    )
    print(f"Wrote {out_ico}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
