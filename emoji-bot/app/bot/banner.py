"""Баннер для /start. Основной — готовая картинка webapp/assets/brand/start-banner.jpg («Добро пожаловать» на фоне
гор). Если её нет, баннер рисуется Pillow: «ДОБРО ПОЖАЛОВАТЬ» на фоне хребтов и знак бренда из аватарки."""
from __future__ import annotations

import hashlib
import logging
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import WEBAPP_DIR, Settings

log = logging.getLogger("ezzprod.banner")

W, H = 1280, 720
BANNER_VERSION = "4"
AVATAR = WEBAPP_DIR / "assets" / "brand" / "avatar-clean.jpg"
START_BANNER = WEBAPP_DIR / "assets" / "brand" / "start-banner.jpg"  # чтобы сменить баннер, замени этот файл

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\bahnschrift.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int):
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            font = ImageFont.truetype(path, size)
            try:
                font.set_variation_by_name("Bold")
            except Exception:
                pass
            return font
    return ImageFont.load_default()


def _gradient() -> Image.Image:
    top, mid, bottom = (18, 18, 26), (10, 10, 13), (6, 6, 8)
    img = Image.new("RGB", (W, H))
    px = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        a, b, u = (top, mid, t / 0.5) if t < 0.5 else (mid, bottom, (t - 0.5) / 0.5)
        px.line([(0, y), (W, y)], fill=tuple(round(a[i] + (b[i] - a[i]) * u) for i in range(3)))
    return img


def _topo(canvas: Image.Image) -> None:
    """Тонкие контуры рельефа (поверх фона, полупрозрачно)."""
    rnd = random.Random(7)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for cx, cy, rings, scale in ((W * 0.86, H * 0.18, 11, 1.7), (W * 0.1, H * 0.55, 8, 1.6), (W * 0.5, H * 1.02, 7, 1.8)):
        phases = [(k, rnd.uniform(0, math.tau), rnd.uniform(0.04, 0.11) / (k * 0.6)) for k in (2, 3, 5)]
        for i in range(1, rings + 1):
            base = (26 + i * 24) * scale
            pts = []
            for s in range(120):
                t = s / 120 * math.tau
                r = base * (1 + sum(a * math.sin(k * t + ph + i * 0.35) for k, ph, a in phases))
                pts.append((cx + r * math.cos(t), cy + r * math.sin(t) * 0.82))
            major = i % 4 == 0
            d.line(pts + [pts[0]], fill=(163, 162, 214, 46 if major else 22), width=2 if major else 1, joint="curve")
    canvas.alpha_composite(layer)


def _profile(rnd: random.Random, n: int = 129, rough: float = 0.56) -> list[float]:
    """Горный профиль методом срединного смещения: значения 0..1."""
    v = [rnd.random(), rnd.random()]
    amp, step = 1.0, n - 1
    pts = {0: v[0], n - 1: v[1]}
    while step > 1:
        half = step // 2
        for i in range(half, n - 1, step):
            pts[i] = (pts[i - half] + pts[i + half]) / 2 + rnd.uniform(-amp, amp) * 0.5
        amp *= rough
        step = half
    ys = [pts[i] for i in range(n)]
    lo, hi = min(ys), max(ys)
    return [(y - lo) / (hi - lo or 1) for y in ys]


def _ridges(canvas: Image.Image) -> None:
    rnd = random.Random(21)
    # (линия подножия, размах пиков, цвет): дальние хребты светлее и бледнее, ближние темнее
    layers = [(0.74, 150, (36, 36, 56, 140)), (0.82, 120, (26, 26, 41, 190)), (0.9, 90, (18, 18, 32, 235)), (0.97, 55, (11, 11, 20, 255))]
    for base, amp, color in layers:
        prof = _profile(rnd)
        pts = [(0, H)] + [(i / (len(prof) - 1) * W, H * base - p * amp) for i, p in enumerate(prof)] + [(W, H)]
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon(pts, fill=color)
        canvas.alpha_composite(layer)


def _spaced(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font, fill, spacing: int) -> None:
    widths = [draw.textlength(ch, font=font) for ch in text]
    x = xy[0] - (sum(widths) + spacing * (len(text) - 1)) / 2
    for ch, w in zip(text, widths):
        draw.text((x, xy[1]), ch, font=font, fill=fill)
        x += w + spacing


def render_banner(brand: str) -> Image.Image:
    canvas = _gradient().convert("RGBA")
    _topo(canvas)
    _ridges(canvas)
    draw = ImageDraw.Draw(canvas)

    # знак бренда: аватарка в скруглённом квадрате с тонкой рамкой
    size = 150
    cx, cy = W // 2, 132
    if AVATAR.exists():
        src = Image.open(AVATAR).convert("RGB")
        side = min(src.size)
        src = src.crop(((src.width - side) // 2, int((src.height - side) * 0.55), (src.width + side) // 2, int((src.height - side) * 0.55) + side)).resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=36, fill=255)
        draw.rounded_rectangle((cx - size // 2 - 3, cy - size // 2 - 3, cx + size // 2 + 3, cy + size // 2 + 3), radius=39, outline=(214, 213, 250, 110), width=2)
        canvas.paste(src, (cx - size // 2, cy - size // 2), mask)

    big = _font(120)
    for text, y in (("ДОБРО", 252), ("ПОЖАЛОВАТЬ", 378)):
        w = draw.textlength(text, font=big)
        draw.text(((W - w) / 2, y), text, font=big, fill="#f2f6fb")
    _spaced(draw, (W / 2, 560), brand.upper(), _font(30), "#9d9cc4", 12)
    return canvas.convert("RGB")


def get_banner(settings: Settings) -> Path | None:
    """Путь к готовому баннеру: картинка из проекта, иначе нарисованный (создаётся при первом обращении и кэшируется)."""
    if START_BANNER.exists():
        return START_BANNER
    key = hashlib.sha1(
        f"{BANNER_VERSION}|{settings.brand_name}|{AVATAR.stat().st_mtime_ns if AVATAR.exists() else 0}".encode()
    ).hexdigest()[:10]
    path = settings.data_dir / f"banner-{key}.jpg"
    if path.exists():
        return path
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        render_banner(settings.brand_name).save(path, "JPEG", quality=92)
        return path
    except Exception:
        log.exception("не удалось собрать баннер")
        return None
