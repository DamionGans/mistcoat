"""Render the app icon at every size each platform needs.

A shield whose lower half dissolves into a pixel grid: the artwork stays whole
to a person, and breaks up for the machine. Drawn with Pillow so the icon has
no external asset dependency and can be regenerated on any OS.

    python tools/make_icons.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def shield_mask(size: int) -> Image.Image:
    s = size
    m = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(m)
    top, mid, bottom = s * 0.14, s * 0.52, s * 0.90
    left, right, cx = s * 0.20, s * 0.80, s * 0.5
    pts = [(cx, top), (right, top + s * 0.08), (right, mid)]
    # Curved lower half, approximated by a polyline.
    for t in np.linspace(0, 1, 24):
        x = right + (cx - right) * t
        y = mid + (bottom - mid) * (t ** 0.75)
        pts.append((x, y))
    for t in np.linspace(0, 1, 24):
        x = cx + (left - cx) * t
        y = bottom + (mid - bottom) * (t ** 1.33)
        pts.append((x, y))
    pts += [(left, top + s * 0.08)]
    d.polygon(pts, fill=255)
    return m


def render(size: int = 1024, tile: bool = True) -> Image.Image:
    """The icon. ``tile=False`` draws the shield alone on transparency, for
    Android adaptive-icon foregrounds (the launcher supplies the shape)."""
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32) / s

    if tile:
        # Rounded-square tile.
        tile_mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(tile_mask).rounded_rectangle((0, 0, s - 1, s - 1),
                                                    radius=int(s * 0.22), fill=255)
        grad = np.dstack([
            30 + 20 * yy, 34 + 26 * yy, 44 + 40 * yy, np.full_like(yy, 255)
        ]).astype(np.uint8)
        img.paste(Image.fromarray(grad, "RGBA"), (0, 0), tile_mask)

    mask = shield_mask(s)
    # Shield body: accent blue gradient.
    body = np.dstack([
        70 + 40 * (1 - yy), 130 + 50 * (1 - yy), 200 + 40 * (1 - yy),
        np.full_like(yy, 255)]).astype(np.uint8)
    body_img = Image.fromarray(body, "RGBA")

    # Lower part breaks into a grid of pixels.
    m = np.asarray(mask).astype(np.float32) / 255.0
    cell = max(2, s // 26)
    rng = np.random.default_rng(4)
    gy, gx = np.mgrid[0:s, 0:s]
    cy, cx = gy // cell, gx // cell
    ncy, ncx = s // cell + 1, s // cell + 1
    keep = rng.random((ncy, ncx))
    frac = (cy * cell / s - 0.50) / 0.42          # 0 at the middle, 1 at the tip
    dissolve = keep[cy, cx] > np.clip(frac, 0, 1) ** 0.8
    gap = ((gx % cell) < max(1, cell // 7)) | ((gy % cell) < max(1, cell // 7))
    lower = gy > s * 0.50
    m = np.where(lower, m * dissolve * (~gap), m)
    body_mask = Image.fromarray((m * 255).astype(np.uint8), "L")

    shadow = body_mask.filter(ImageFilter.GaussianBlur(s * 0.02))
    sh = Image.new("RGBA", (s, s), (0, 0, 0, 120))
    img.paste(sh, (int(s * 0.0), int(s * 0.015)), shadow)
    img.paste(body_img, (0, 0), body_mask)

    # A single brush stroke across the intact upper half.
    stroke = Image.new("L", (s, s), 0)
    sd = ImageDraw.Draw(stroke)
    sd.line([(s * 0.32, s * 0.40), (s * 0.46, s * 0.30), (s * 0.66, s * 0.27)],
            fill=255, width=int(s * 0.045), joint="curve")
    stroke = stroke.filter(ImageFilter.GaussianBlur(s * 0.004))
    img.paste(Image.new("RGBA", (s, s), (240, 244, 250, 255)), (0, 0), stroke)
    return img


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    master = render(1024)
    master.save(ASSETS / "icon.png")
    master.save(ASSETS / "icon.ico",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                       (128, 128), (256, 256)])
    master.save(ASSETS / "icon.icns")
    # Mobile / PWA sizes.
    for px in (192, 512):
        master.resize((px, px), Image.LANCZOS).save(ASSETS / f"icon-{px}.png")
    # Maskable PWA icon: content within the inner 80% safe zone.
    safe = Image.new("RGBA", (512, 512), (30, 34, 44, 255))
    safe.paste(master.resize((410, 410), Image.LANCZOS), (51, 51),
               master.resize((410, 410), Image.LANCZOS))
    safe.save(ASSETS / "icon-maskable-512.png")
    write_mobile(master)
    print("icons written to", ASSETS)


BG = (30, 34, 44, 255)   # matches the tile's top colour


def write_mobile(master: Image.Image) -> None:
    """Android adaptive icons + splash, iOS app icon + splash - if the
    Capacitor projects exist (mobile/android, mobile/ios)."""
    android = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res"
    if android.exists():
        shield = render(1024, tile=False)
        for dpi, px in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96),
                        ("xxhdpi", 144), ("xxxhdpi", 192)):
            d = android / f"mipmap-{dpi}"
            master.resize((px, px), Image.LANCZOS).save(d / "ic_launcher.png")
            # Round icon: same artwork, circle-masked.
            rnd = Image.new("RGBA", (px, px), BG)
            rnd.paste(master.resize((px, px), Image.LANCZOS), (0, 0))
            m = Image.new("L", (px, px), 0)
            ImageDraw.Draw(m).ellipse((0, 0, px - 1, px - 1), fill=255)
            out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
            out.paste(rnd, (0, 0), m)
            out.save(d / "ic_launcher_round.png")
            # Adaptive foreground: 108dp canvas, artwork inside the 66dp safe zone.
            fg_px = int(px * 108 / 48)
            inner = int(fg_px * 0.70)
            fg = Image.new("RGBA", (fg_px, fg_px), (0, 0, 0, 0))
            art = shield.resize((inner, inner), Image.LANCZOS)
            fg.paste(art, ((fg_px - inner) // 2, (fg_px - inner) // 2), art)
            fg.save(d / "ic_launcher_foreground.png")
        (android / "values" / "ic_launcher_background.xml").write_text(
            "\n".join([
                '<?xml version="1.0" encoding="utf-8"?>',
                "<resources>",
                '    <color name="ic_launcher_background">#1E222C</color>',
                "</resources>",
                "",
            ]),
            encoding="utf-8")
        for splash in android.glob("drawable*/splash.png"):
            with Image.open(splash) as im:
                size = im.size
            splash_image(size).save(splash)

    ios = ROOT / "mobile" / "ios" / "App" / "App" / "Assets.xcassets"
    if ios.exists():
        # iOS masks the corners itself and rejects transparency.
        flat = Image.new("RGB", (1024, 1024), BG[:3])
        flat.paste(render(1024, tile=False), (0, 0), render(1024, tile=False))
        flat.save(ios / "AppIcon.appiconset" / "AppIcon-512@2x.png")
        for splash in (ios / "Splash.imageset").glob("*.png"):
            splash_image((2732, 2732)).convert("RGB").save(splash)


def splash_image(size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", (w, h), BG)
    px = max(48, int(min(w, h) * 0.22))
    art = render(1024, tile=False).resize((px, px), Image.LANCZOS)
    img.paste(art, ((w - px) // 2, (h - px) // 2), art)
    return img


if __name__ == "__main__":
    main()
