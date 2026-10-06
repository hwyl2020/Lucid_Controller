"""Generate the VisionX app icon and brand artwork.

Icon "A · Iris": an Apple-style squircle in HWYL deep navy, rounded orange viewfinder corner
brackets (from the HWYL mark), and a silver aperture iris around a blue glass lens.

Writes:
  app/resources/app.ico        .exe / window icon (16-256 px, PNG-compressed)
  app/resources/app.png        256 px, shown in the app's header
  docs/branding/visionx_icon_1024.png, visionx_wordmark_dark.png, visionx_wordmark_light.png

Run: python -m installer.make_icon   (needs Pillow: requirements-build.txt)
"""

from __future__ import annotations

import math
import struct
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from app import APP_NAME

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "app" / "resources"
BRANDING = ROOT / "docs" / "branding"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

SIZE = 1024  # master render
SS = 2  # supersampling
N = SIZE * SS

# Palette (sRGB): HWYL navy + signal orange, silver blades, blue glass.
NAVY_TOP, NAVY_BOTTOM = (32, 48, 76), (9, 16, 30)
GLOW = (70, 110, 170)
ORANGE_TOP, ORANGE_BOTTOM = (255, 158, 64), (240, 104, 20)
BLADE_SHADES = (((250, 251, 253), (196, 203, 214)), ((224, 229, 237), (160, 170, 186)),
                ((236, 240, 245), (180, 188, 201)))
BLADE_GAP = (24, 36, 58)
GLASS_LIGHT, GLASS_DEEP = (40, 140, 255), (6, 22, 60)
WHITE = (255, 255, 255)


def _rgb(c) -> np.ndarray:
    return np.asarray(c, np.float32) / 255


def _comp(img: np.ndarray, mask: np.ndarray, rgb) -> None:
    """Composite a colour (3,) or image (H,W,3) with an alpha mask over straight-alpha RGBA."""
    a = np.clip(mask, 0, 1)[..., None]
    rgb = np.broadcast_to(np.asarray(rgb, np.float32), img[..., :3].shape)
    img[..., :3] = rgb * a + img[..., :3] * (1 - a)
    img[..., 3:] = a + img[..., 3:] * (1 - a)


def _mask(draw_fn) -> np.ndarray:
    im = Image.new("L", (N, N), 0)
    draw_fn(ImageDraw.Draw(im))
    return np.asarray(im, np.float32) / 255


def _vgrad(top, bottom, y0=0.0, y1=1.0) -> np.ndarray:
    t = np.clip((np.linspace(0, 1, N)[:, None, None] - y0) / (y1 - y0), 0, 1)
    return np.broadcast_to(_rgb(top) * (1 - t) + _rgb(bottom) * t, (N, N, 3))


def _blur(mask: np.ndarray, px: float) -> np.ndarray:
    k = int(px * SS * SIZE / 512) | 1
    return cv2.GaussianBlur(mask, (k, k), 0)


def _shift(mask: np.ndarray, frac: float) -> np.ndarray:
    return np.roll(mask, int(frac * N), axis=0)


def _squircle(inset: float = 0.035, n: float = 5.0) -> np.ndarray:
    c = (np.arange(N) + 0.5) / N * 2 - 1
    x, y = np.meshgrid(c, c)
    r = 1 - inset
    v = (np.abs(x) / r) ** n + (np.abs(y) / r) ** n
    return np.clip((1 - v) / (3.0 / N * n) + 0.5, 0, 1).astype(np.float32)


def _background(img: np.ndarray) -> np.ndarray:
    sq = _squircle()
    _comp(img, sq, _vgrad(NAVY_TOP, NAVY_BOTTOM))
    c = (np.arange(N) + 0.5) / N * 2 - 1
    x, y = np.meshgrid(c, c)
    _comp(img, np.exp(-(x ** 2 + (y + 0.05) ** 2) / 0.35) * 0.22 * sq, _rgb(GLOW))  # soft centre glow
    rim = np.clip(sq - _shift(sq, 0.006), 0, 1) * np.linspace(1, 0, N)[:, None] ** 2
    _comp(img, rim * 0.35, _rgb(WHITE))  # top rim light
    return sq


def _brackets(img: np.ndarray, inset=0.20, length=0.17, width=0.052) -> None:
    lo, hi, L, w = inset * N, (1 - inset) * N, length * N, width * N

    def draw(d):
        for cx, cy, dx, dy in ((lo, lo, 1, 1), (hi, lo, -1, 1), (lo, hi, 1, -1), (hi, hi, -1, -1)):
            rc = 0.9 * w  # rounded corner on the centre line
            arc = [(cx + dx * rc - dx * rc * math.cos(t), cy + dy * rc - dy * rc * math.sin(t))
                   for t in np.linspace(0, math.pi / 2, 24)]
            pts = [(cx, cy + dy * L), *arc, (cx + dx * L, cy)]
            # Stroke by stamping discs along the path: smooth round corners and caps.
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                for t in np.linspace(0, 1, max(2, int(math.hypot(x1 - x0, y1 - y0) / 2))):
                    px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
                    d.ellipse((px - w / 2, py - w / 2, px + w / 2, py + w / 2), fill=255)

    m = _mask(draw)
    _comp(img, _shift(_blur(m, 6), 0.008) * 0.45, np.zeros(3))
    _comp(img, m, _vgrad(ORANGE_TOP, ORANGE_BOTTOM, 0.15, 0.85))


def _iris_geometry(radius: float, inner: float, count=6, rotation=-12.0):
    c = N / 2
    rot = math.radians(rotation)
    hexv = [(c + inner * math.cos(rot + 2 * math.pi * i / count), c + inner * math.sin(rot + 2 * math.pi * i / count))
            for i in range(count)]

    def spoke_end(i):  # from vertex i along the edge (i-1 -> i) to the outer circle
        (x0, y0), (x1, y1) = hexv[i - 1], hexv[i]
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy)
        dx, dy = dx / ln, dy / ln
        fx, fy = x1 - c, y1 - c
        b = fx * dx + fy * dy
        t = -b + math.sqrt(b * b - (fx * fx + fy * fy - radius * radius))
        return x1 + dx * t, y1 + dy * t

    ends = [spoke_end(i) for i in range(count)]
    blades = []
    for i in range(count):
        j = (i + 1) % count
        a0 = math.atan2(ends[i][1] - c, ends[i][0] - c)
        a1 = math.atan2(ends[j][1] - c, ends[j][0] - c)
        if a1 < a0:
            a1 += 2 * math.pi
        arc = [(c + radius * math.cos(a), c + radius * math.sin(a)) for a in np.linspace(a0, a1, 48)]
        blades.append([hexv[i], ends[i], *arc, hexv[j]])
    return blades, hexv, ends


def _glass(img: np.ndarray, r: float) -> None:
    c = N / 2
    yy, xx = np.mgrid[0:N, 0:N].astype(np.float32) + 0.5
    m = np.clip((r - np.hypot(xx - c, yy - c)) / SS + 0.5, 0, 1)
    t = np.clip(np.hypot(xx - (c - 0.35 * r), yy - (c - 0.4 * r)) / (1.6 * r), 0, 1)[..., None]
    _comp(img, m, _rgb(GLASS_LIGHT) * (1 - t) + _rgb(GLASS_DEEP) * t)
    for fx, fy, fr, alpha, soft in ((-0.38, -0.38, 0.22, 0.9, 3), (0.35, 0.32, 0.09, 0.5, 2)):  # highlights
        h = np.clip((fr * r - np.hypot(xx - (c + fx * r), yy - (c + fy * r))) / SS + 0.5, 0, 1)
        _comp(img, _blur(h, soft) * alpha, _rgb(WHITE))


def _iris(img: np.ndarray, radius_f=0.215, inner_f=0.085) -> None:
    R, r, c = radius_f * N, inner_f * N, N / 2
    disc = _mask(lambda d: d.ellipse((c - R, c - R, c + R, c + R), fill=255))
    _comp(img, _shift(_blur(disc, 14), 0.018) * 0.55, np.zeros(3))  # drop shadow
    blades, hexv, ends = _iris_geometry(R, r)
    for i, poly in enumerate(blades):
        top, bottom = BLADE_SHADES[i % len(BLADE_SHADES)]
        _comp(img, _mask(lambda d, p=poly: d.polygon(p, fill=255)), _vgrad(top, bottom))
    gaps = _mask(lambda d: [d.line([hexv[i], ends[i]], fill=255, width=max(1, int(0.006 * N)))
                            for i in range(len(hexv))])
    _comp(img, gaps * disc, _rgb(BLADE_GAP))
    _glass(img, r * 0.98)


def render_icon() -> Image.Image:
    img = np.zeros((N, N, 4), np.float32)
    sq = _background(img)
    _brackets(img)
    _iris(img)
    img[..., 3] *= sq
    out = cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
    return Image.fromarray((np.clip(out, 0, 1) * 255).round().astype(np.uint8))


def write_ico(master: Image.Image, path: Path) -> None:
    images = []
    for size in ICO_SIZES:
        buf = BytesIO()
        master.resize((size, size), Image.LANCZOS).save(buf, "PNG")
        images.append((size, buf.getvalue()))
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    path.write_bytes(header + entries + blobs)


def wordmark(icon: Image.Image, dark: bool) -> Image.Image:
    """Lockup: icon + the app name (its last letter, the X, in HWYL orange) + "by HWYL"."""
    bg = (10, 16, 28) if dark else (246, 247, 250)
    text, sub = ((245, 247, 250), (140, 152, 172)) if dark else ((16, 24, 40), (110, 120, 138))
    title = ImageFont.truetype(r"C:\Windows\Fonts\seguisb.ttf", 170)
    small = ImageFont.truetype(r"C:\Windows\Fonts\segoeui.ttf", 50)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    title_w = probe.textlength(APP_NAME, font=title)
    ascent, descent = title.getmetrics()
    icon_px, pad, gap = 300, 60, 50
    W, H = int(pad + icon_px + gap + title_w + pad), 420
    im = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(im)
    small_icon = icon.resize((icon_px, icon_px), Image.LANCZOS)
    im.paste(small_icon, (pad, (H - icon_px) // 2), small_icon)
    # Title and tagline centred as a block next to the icon; the tagline sits below the descenders.
    block_h = ascent + descent + 10 + small.getmetrics()[0]
    x, y = pad + icon_px + gap, (H - block_h) // 2 - 10
    head, tail = APP_NAME[:-1], APP_NAME[-1]
    d.text((x, y), head, font=title, fill=text)
    d.text((x + probe.textlength(head, font=title), y), tail, font=title, fill=ORANGE_BOTTOM)
    d.text((x + 8, y + ascent + descent + 10), "by HWYL", font=small, fill=sub)
    return im


def main() -> None:
    RESOURCES.mkdir(parents=True, exist_ok=True)
    BRANDING.mkdir(parents=True, exist_ok=True)
    icon = render_icon()
    write_ico(icon, RESOURCES / "app.ico")
    icon.resize((256, 256), Image.LANCZOS).save(RESOURCES / "app.png")
    slug = APP_NAME.lower()
    icon.save(BRANDING / f"{slug}_icon_1024.png")
    wordmark(icon, dark=True).save(BRANDING / f"{slug}_wordmark_dark.png")
    wordmark(icon, dark=False).save(BRANDING / f"{slug}_wordmark_light.png")
    print(f"wrote {RESOURCES / 'app.ico'}, {RESOURCES / 'app.png'} and {BRANDING}")


if __name__ == "__main__":
    main()
