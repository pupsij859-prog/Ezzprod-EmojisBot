"""Перекраска шаблонов в чёрно-белый стиль: шаблон TGS и его превью (assets/thumbs).

    python tools/bw_templates.py [id шаблона ...]

Без аргументов перекрашивает список BW ниже — шаблоны, которые в своих цветах смотрятся тускло и «грязно»
(серо-коричневые и блёкло-синие). Каждый цвет переводится в серый по яркости с усилением контраста: тёмные
заливки становятся почти чёрными, светлые контуры и надписи — белыми. Повторный запуск ничего не портит:
шаблон, где все цвета уже серые, пропускается.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import catalog  # noqa: E402

BW = [
    "classic-006", "classic-007", "classic-010", "classic-015", "classic-017", "classic-021", "classic-027",
    "classic-028", "classic-030", "classic-033", "classic-036", "classic-037", "classic-041", "classic-043",
    "classic-047", "classic-051", "classic-053", "classic-059", "classic-063", "classic-064", "classic-065",
    "classic-070", "classic-074", "classic-083", "classic-087", "characters-034", "characters-042",
]
CONTRAST = 2.4
PIVOT = 0.55  # середина сдвинута вверх: средние тона уходят в тёмные, основа почти чёрная


def gray(r: float, g: float, b: float) -> float:
    """Яркость (как у Pillow: L = 0.299R + 0.587G + 0.114B) с усилением контраста."""
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    return max(0.0, min(1.0, (luma - PIVOT) * CONTRAST + 0.5))


def _color_list(values: list, start: int, step: int) -> bool:
    """Цвета вида [r, g, b, (a)] подряд с шагом step начиная со start; меняет на месте. → были ли не серые."""
    changed = False
    for i in range(start, len(values) - 2, step):
        r, g, b = values[i:i + 3]
        if not all(isinstance(x, (int, float)) for x in (r, g, b)):
            continue
        changed |= not (r == g == b)
        v = round(gray(r, g, b), 4)
        values[i:i + 3] = [v, v, v]
    return changed


def _paint(prop, gradient_stops: int | None = None) -> bool:
    """Цвет заливки/обводки (c) или стопы градиента (g.k) — статичные и по ключевым кадрам."""
    if not isinstance(prop, dict):
        return False
    k = prop.get("k")
    frames = k if isinstance(k, list) and k and isinstance(k[0], dict) else None
    lists = [f.get(key) for f in frames for key in ("s", "e") if isinstance(f.get(key), list)] if frames else [k]
    changed = False
    for values in lists:
        if not isinstance(values, list):
            continue
        if gradient_stops is None:
            changed |= _color_list(values, 0, 4)
        else:  # [позиция, r, g, b] × n, дальше — прозрачность (её не трогаем)
            changed |= _color_list(values[: gradient_stops * 4], 1, 4)
    return changed


def to_bw(o) -> bool:
    """Все цвета анимации → серые. → были ли цветные."""
    changed = False
    if isinstance(o, dict):
        ty = o.get("ty")
        if ty in ("fl", "st"):
            changed |= _paint(o.get("c"))
        elif ty in ("gf", "gs") and isinstance(o.get("g"), dict):
            changed |= _paint(o["g"].get("k"), int(o["g"].get("p") or 0))
        for v in o.values():
            if isinstance(v, (dict, list)):
                changed |= to_bw(v)
    elif isinstance(o, list):
        for v in o:
            changed |= to_bw(v)
    return changed


def bw_thumb(path: Path) -> None:
    from PIL import Image

    im = Image.open(path).convert("RGBA")
    lut = [round(gray(i / 255, i / 255, i / 255) * 255) for i in range(256)]
    lum = im.convert("L").point(lut)
    out = Image.merge("RGBA", (lum, lum, lum, im.getchannel("A")))
    out.save(path, "WEBP", quality=90, method=6)


def convert(template_id: str) -> str:
    tpl = catalog.get(template_id)
    if tpl is None:
        return "нет такого шаблона"
    anim = json.loads(gzip.decompress(tpl.path.read_bytes()))
    if not to_bw(anim):
        return "уже чёрно-белый"
    raw = json.dumps(anim, separators=(",", ":"), ensure_ascii=False).encode()
    tpl.path.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    thumb = ROOT / "webapp" / "assets" / "thumbs" / f"{template_id}.webp"
    if thumb.is_file():
        bw_thumb(thumb)
    return "готово"


if __name__ == "__main__":
    for tid in sys.argv[1:] or BW:
        print(tid, convert(tid))
