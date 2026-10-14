"""Шаблоны TGS: поиск места под надпись, перекраска и подстановка ника/логотипа пользователя.

Как устроены шаблоны (папка templates/): анимация Lottie 512×512, 60 к/с, 3 с. Надпись-заглушка «ezzprod» —
группа из букв; у каждой буквы заливка с именем «ezzprod color» (это и есть «цвет надписи»). Бывает несколько
копий надписи в одном шаблоне — меняем все.

Подстановка: буквы-заглушки убираем, на их место (по их общим габаритам, с тем же преобразованием группы)
ставим векторный рисунок — надпись из шрифта (app/fonts.py) и/или трассированный логотип (app/vectorize.py).

Цвета пользователя (как у референса):
- «Заливка» — основной цвет стикера: сплошная заливка, которая занимает наибольшую площадь (синий круг у «TOP»);
- «Обводка» — всё цвета контура (самый толстый цвет обводок, обычно белый): сами контуры, детали и надписи
  этого же цвета, нарисованные заливкой (буквы «TOP», руки, блики-штрихи), и надпись «ezzprod»/ник.
Перекрашиваются все заливки/обводки, близкие по цвету к найденному основному (допуск TOL).
Тот же алгоритм повторён в webapp/js/tgs.js для живого превью — меняй их вместе.
"""
from __future__ import annotations

import copy
import gzip
import json
import math
from functools import lru_cache
from pathlib import Path

SLOT = "ezzprod color"
TG_LIMIT = 64 * 1024          # TGS после gzip — не больше 64 КБ
LOGO_HEIGHT = 1.9             # логотип может быть выше строки надписи во столько раз
GAP = 0.18                    # зазор между логотипом и текстом, доля высоты строки
TEXT_FIT_W = 0.86             # ник занимает до этой доли ширины места надписи…
TEXT_FIT_H = 0.78             # …и до этой доли высоты: короткий ник упирается в высоту и выходит крупнее,
                              # длинный — в ширину и выходит мельче
TEXT_WITH_LOGO = 0.72         # рядом с логотипом ник чуть меньше своей области
SIZE_RANGE = (0.4, 1.6)       # «размер надписи» пользователя: множитель к автоподбору
TOL = 0.06                   # насколько цвет может отличаться от основного, чтобы считаться им же (RGB 0..1)


# ───────────────────────── поиск надписи ─────────────────────────

def _is_letter(item: dict) -> bool:
    return item.get("ty") == "gr" and any(i.get("ty") == "fl" and i.get("nm") == SLOT for i in item.get("it", ()))


def word_groups(anim: dict) -> list[dict]:
    """Группы-надписи: группы, в которых лежат буквы с цветом «ezzprod color»."""
    found: list[dict] = []

    def visit(o) -> None:
        if isinstance(o, dict):
            items = o.get("it") if o.get("ty") == "gr" else None
            if items and any(_is_letter(i) for i in items):
                found.append(o)
                return
            for v in o.values():
                visit(v)
        elif isinstance(o, list):
            for v in o:
                visit(v)

    visit(anim)
    return found


def slot_color(anim: dict) -> list[float] | None:
    """Родной цвет надписи шаблона (RGB 0..1)."""
    for g in word_groups(anim):
        for letter in g["it"]:
            if _is_letter(letter):
                for i in letter["it"]:
                    if i.get("ty") == "fl" and i.get("nm") == SLOT and not i["c"].get("a"):
                        return list(i["c"]["k"][:3])
    return None


def _static(prop: dict, default):
    if not isinstance(prop, dict):
        return default
    k = prop.get("k", default)
    if prop.get("a"):  # анимированное свойство — берём первый ключевой кадр
        if isinstance(k, list):
            k = k[0].get("s", default) if k and isinstance(k[0], dict) else default
        elif not isinstance(k, dict):  # бывает «a: 1» с одним значением-фигурой вместо кадров
            k = default
    return k


def _matrix(tr: dict | None):
    """Преобразование группы Lottie (якорь, позиция, масштаб, поворот) как функция точки."""
    if not tr:
        return lambda x, y: (x, y)
    ax, ay = (_static(tr.get("a"), [0, 0]) + [0, 0])[:2]
    px, py = (_static(tr.get("p"), [0, 0]) + [0, 0])[:2]
    sx, sy = (_static(tr.get("s"), [100, 100]) + [100, 100])[:2]
    r = math.radians(_static(tr.get("r"), 0) or 0)
    cos, sin = math.cos(r), math.sin(r)

    def f(x, y):
        x, y = (x - ax) * sx / 100, (y - ay) * sy / 100
        return x * cos - y * sin + px, x * sin + y * cos + py
    return f


def _letters_bbox(group: dict) -> tuple[float, float, float, float] | None:
    xs, ys = [], []
    for letter in group["it"]:
        if not _is_letter(letter):
            continue
        tr = next((i for i in letter["it"] if i.get("ty") == "tr"), None)
        f = _matrix(tr)
        for i in letter["it"]:
            if i.get("ty") != "sh":
                continue
            shape = _static(i.get("ks"), None)
            if isinstance(shape, list):  # у анимированной фигуры кадр хранит список из одной фигуры
                shape = shape[0] if shape else None
            if not isinstance(shape, dict):
                continue
            for v in shape.get("v", ()):
                x, y = f(v[0], v[1])
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


# ───────────────────────── векторный рисунок → фигуры Lottie ─────────────────────────

def _fill(rgb: list[float]) -> dict:
    return {"ty": "fl", "nm": SLOT, "c": {"a": 0, "k": [*rgb[:3], 1]}, "o": {"a": 0, "k": 100}, "r": 1, "bm": 0}


def _transform() -> dict:
    return {"ty": "tr", "p": {"a": 0, "k": [0, 0]}, "a": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [100, 100]},
            "r": {"a": 0, "k": 0}, "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0}, "sa": {"a": 0, "k": 0}}


def _place(drawing: dict, box: tuple[float, float, float, float], text_rgb: list[float]) -> list[dict]:
    """Рисунок, вписанный в box с сохранением пропорций и по центру → группы Lottie."""
    x0, y0, x1, y1 = drawing["bbox"]
    bw, bh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    tx0, ty0, tx1, ty1 = box
    k = min((tx1 - tx0) / bw, (ty1 - ty0) / bh)
    ox = (tx0 + tx1) / 2 - (x0 + bw / 2) * k
    oy = (ty0 + ty1) / 2 - (y0 + bh / 2) * k
    groups = []
    for layer in drawing["layers"]:
        rgb = [c / 255 for c in layer["color"]] if layer.get("color") else text_rgb
        items = []
        for path in layer["paths"]:
            items.append({"ty": "sh", "ks": {"a": 0, "k": {
                "v": [[round(x * k + ox, 2), round(y * k + oy, 2)] for x, y in path["v"]],
                "i": [[round(a * k, 2), round(b * k, 2)] for a, b in path["i"]],
                "o": [[round(a * k, 2), round(b * k, 2)] for a, b in path["o"]],
                "c": True}}})
        groups.append({"ty": "gr", "nm": "ezz layer", "it": [*items, _fill(rgb), _transform()]})
    return groups[::-1]  # в Lottie первый элемент группы рисуется поверх остальных


def _layout(box, text: dict | None, logo: dict | None, size: float = 1.0):
    """Где рисовать логотип и текст внутри места надписи. → [(рисунок, область)].
    size — множитель размера ника (1 — автоподбор по длине)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if logo and not text:
        lh = min(w, h * LOGO_HEIGHT)
        return [(logo, (x0, cy - lh / 2, x1, cy + lh / 2))]
    if text and not logo:
        hw, hh = w * TEXT_FIT_W * size / 2, h * TEXT_FIT_H * size / 2
        return [(text, (cx - hw, cy - hh, cx + hw, cy + hh))]
    # логотип слева квадратом, текст справа — вместе по центру места надписи
    side = min(h * LOGO_HEIGHT * 0.8, w * 0.42)
    tb = text["bbox"]
    t_aspect = (tb[2] - tb[0]) / max(tb[3] - tb[1], 1e-6)
    tw = min(w - side - h * GAP, h * t_aspect)
    total = side + h * GAP + tw
    left = cx - total / 2
    return [
        (logo, (left, cy - side / 2, left + side, cy + side / 2)),
        (text, _shrink((left + side + h * GAP, y0, left + total, y1), TEXT_WITH_LOGO * size)),
    ]


def text_size(value) -> float:
    """«Размер надписи» в процентах (100 — автоподбор) → множитель в допустимых пределах."""
    try:
        k = float(value) / 100 if value is not None else 1.0
    except (TypeError, ValueError):
        k = 1.0
    return max(SIZE_RANGE[0], min(SIZE_RANGE[1], k))


def _shrink(box, k: float):
    """Та же область, уменьшенная в k раз вокруг центра."""
    x0, y0, x1, y1 = box
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) * k / 2, (y1 - y0) * k / 2
    return cx - hw, cy - hh, cx + hw, cy + hh


# ───────────────────────── основные цвета шаблона ─────────────────────────

def _solid(item: dict) -> list[float] | None:
    """Цвет сплошной заливки/обводки (RGB 0..1) или None, если он анимирован или задан иначе."""
    c = item.get("c")
    if not isinstance(c, dict) or c.get("a"):
        return None
    k = c.get("k")
    if not isinstance(k, list) or len(k) < 3 or not all(isinstance(x, (int, float)) for x in k[:3]):
        return None
    return [float(x) for x in k[:3]]


def _area(items: list) -> float:
    """Примерная площадь фигур группы (по вершинам путей, первый кадр, вложенные группы тоже)."""
    total = 0.0
    for i in items:
        if not isinstance(i, dict):
            continue
        if i.get("ty") == "gr":
            total += _area(i.get("it") or [])
        elif i.get("ty") == "sh":
            shape = _static(i.get("ks"), None)
            if isinstance(shape, list):
                shape = shape[0] if shape else None
            pts = shape.get("v") if isinstance(shape, dict) else None
            if pts and len(pts) >= 3:
                # площадь многоугольника по вершинам (формула шнурка): тонкие штрихи почти ничего не весят
                total += abs(sum(pts[n][0] * pts[n - 1][1] - pts[n - 1][0] * pts[n][1] for n in range(len(pts)))) / 2
    return total


def main_colors(anim: dict) -> tuple[list[float] | None, list[float] | None]:
    """(основной цвет заливки, основной цвет обводки). Заливка — по площади, обводка — по суммарной толщине.
    Надпись «ezzprod color» в расчёт не входит."""
    fills: dict[tuple, list] = {}
    strokes: dict[tuple, list] = {}

    def visit(o) -> None:
        if isinstance(o, dict):
            items = o.get("it") if o.get("ty") == "gr" else o.get("shapes") if o.get("ty") == 4 else None
            if isinstance(items, list):
                paints = [i for i in items if isinstance(i, dict) and i.get("ty") in ("fl", "st")
                          and i.get("nm") != SLOT and _solid(i) is not None]
                # видна только верхняя заливка группы (в Lottie первая рисуется поверх остальных)
                top_fill = next((i for i in paints if i["ty"] == "fl"), None)
                if top_fill is not None:
                    rgb = _solid(top_fill)
                    fills.setdefault(tuple(round(x, 2) for x in rgb), [rgb, 0.0])[1] += _area(items)
                for i in paints:
                    if i["ty"] == "st":  # обводки — по толщине: внешний широкий контур важнее тонких линий
                        rgb = _solid(i)
                        width = _static(i.get("w"), 1)
                        width = float(width) if isinstance(width, (int, float)) else 1.0
                        strokes.setdefault(tuple(round(x, 2) for x in rgb), [rgb, 0.0])[1] += max(width, 0.5)
            for v in o.values():
                visit(v)
        elif isinstance(o, list):
            for v in o:
                visit(v)

    visit(anim)
    pick = lambda d: max(d.values(), key=lambda e: e[1])[0] if d else None  # noqa: E731
    return pick(fills), pick(strokes)


def _near(a: list[float], b: list[float]) -> bool:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5 <= TOL


def _recolor(anim: dict, kinds: tuple[str, ...], src: list[float], dst: list[float]) -> None:
    """Все сплошные заливки ('fl') и/или обводки ('st') цвета src → dst. Надпись «ezzprod» — отдельно."""
    def visit(o) -> None:
        if isinstance(o, dict):
            if o.get("ty") in kinds and o.get("nm") != SLOT:
                rgb = _solid(o)
                if rgb is not None and _near(rgb, src):
                    k = o["c"]["k"]
                    o["c"] = {"a": 0, "k": [*dst[:3], k[3] if len(k) > 3 else 1]}
            for v in o.values():
                visit(v)
        elif isinstance(o, list):
            for v in o:
                visit(v)
    visit(anim)


def personalize(anim: dict, *, color: list[float] | None, text: dict | None, logo: dict | None,
                stroke: list[float] | None = None, size: float = 1.0) -> dict:
    """Копия анимации: основной цвет стикера (color — «Заливка», None — родной), контуры и надпись
    (stroke — «Обводка», None — родные), ник и/или логотип вместо «ezzprod» (size — множитель размера ника)."""
    out = copy.deepcopy(anim)
    if color or stroke:
        main_fill, main_stroke = main_colors(out)
        if color and main_fill:
            _recolor(out, ("fl",), main_fill, list(color))
        if stroke and main_stroke:  # контуры и всё того же цвета, нарисованное заливкой
            _recolor(out, ("st", "fl"), main_stroke, list(stroke))
    base = slot_color(out) or [1, 1, 1]
    text_rgb = list(stroke) if stroke else base  # надпись красится вместе с обводкой
    for group in word_groups(out):
        if text is None and logo is None:
            if stroke:
                for letter in group["it"]:
                    if _is_letter(letter):
                        for i in letter["it"]:
                            if i.get("ty") == "fl" and i.get("nm") == SLOT:
                                i["c"] = {"a": 0, "k": [*text_rgb[:3], 1]}
            continue
        box = _letters_bbox(group)
        if box is None:
            continue
        drawn = []
        for drawing, area in _layout(box, text, logo, size):
            drawn += _place(drawing, area, text_rgb)
        rest = [i for i in group["it"] if not _is_letter(i)]
        tr = [i for i in rest if i.get("ty") == "tr"]
        other = [i for i in rest if i.get("ty") != "tr"]
        group["it"] = other + drawn + tr  # «tr» группы обязан быть последним
    return out


# ───────────────────────── файлы ─────────────────────────

@lru_cache(maxsize=64)
def _load(path: str, mtime: float) -> dict:
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def load(path: Path) -> dict:
    """Шаблон как словарь (кэш по пути и времени изменения). Не изменяй результат — делай копию."""
    return _load(str(path), path.stat().st_mtime)


def encode(anim: dict) -> bytes:
    """Анимация → байты .tgs (компактный JSON + gzip)."""
    raw = json.dumps(anim, separators=(",", ":"), ensure_ascii=False).encode()
    return gzip.compress(raw, compresslevel=9, mtime=0)


def hex_to_rgb(color: str | None) -> list[float] | None:
    if not color:
        return None
    c = color.lstrip("#")
    return [int(c[i: i + 2], 16) / 255 for i in (0, 2, 4)]
