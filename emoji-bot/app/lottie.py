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
NICK_LAYER = "ezz nick"         # группа букв ника, который подставил personalize
LOGO_LAYER = "ezz layer"        # группа одного цвета логотипа (у него свои цвета)
TG_LIMIT = 64 * 1024          # TGS после gzip — не больше 64 КБ
LOGO_HEIGHT = 1.9             # логотип может быть выше строки надписи во столько раз
GAP = 0.18                    # зазор между логотипом и текстом, доля высоты строки
TEXT_FIT_W = 0.86             # ник занимает до этой доли ширины места надписи…
TEXT_FIT_H = 0.78             # …и до этой доли высоты: короткий ник упирается в высоту и выходит крупнее,
                              # длинный — в ширину и выходит мельче
TEXT_WITH_LOGO = 0.72         # рядом с логотипом ник чуть меньше своей области
SIZE_RANGE = (0.4, 1.6)       # «размер надписи» пользователя: множитель к автоподбору
PLACE_RANGE = {"x": 100, "y": 100, "r": 180}  # сдвиг надписи, % ширины места надписи; поворот, °
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

def text_fills(o, in_logo: bool = False):
    """Заливки букв надписи (ник или родные буквы «ezzprod») — без цветных слоёв логотипа."""
    if isinstance(o, dict):
        if o.get("ty") == "fl" and o.get("nm") == SLOT and not in_logo:
            yield o
        in_logo = in_logo or (o.get("ty") == "gr" and o.get("nm") == LOGO_LAYER)
        for v in o.values():
            if isinstance(v, (dict, list)):
                yield from text_fills(v, in_logo)
    elif isinstance(o, list):
        for v in o:
            yield from text_fills(v, in_logo)


def _fill(rgb: list[float]) -> dict:
    return {"ty": "fl", "nm": SLOT, "c": {"a": 0, "k": [*rgb[:3], 1]}, "o": {"a": 0, "k": 100}, "r": 1, "bm": 0}


def _transform() -> dict:
    return {"ty": "tr", "p": {"a": 0, "k": [0, 0]}, "a": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [100, 100]},
            "r": {"a": 0, "k": 0}, "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0}, "sa": {"a": 0, "k": 0}}


def _place(drawing: dict, box: tuple[float, float, float, float], text_rgb: list[float],
           grad: dict | None = None) -> list[dict]:
    """Рисунок, вписанный в box с сохранением пропорций и по центру → группы Lottie (grad — см. clean_grad)."""
    x0, y0, x1, y1 = drawing["bbox"]
    bw, bh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    tx0, ty0, tx1, ty1 = box
    k = min((tx1 - tx0) / bw, (ty1 - ty0) / bh)
    ox = (tx0 + tx1) / 2 - (x0 + bw / 2) * k
    oy = (ty0 + ty1) / 2 - (y0 + bh / 2) * k
    fitted = (x0 * k + ox, y0 * k + oy, x1 * k + ox, y1 * k + oy)  # где рисунок оказался: по нему идёт градиент
    on = set(grad["on"]) if grad else set()
    shades = _logo_shades(drawing) if "logo" in on else {}
    groups = []
    for n, layer in enumerate(drawing["layers"]):
        rgb = [c / 255 for c in layer["color"]] if layer.get("color") else text_rgb
        items = []
        for path in layer["paths"]:
            items.append({"ty": "sh", "ks": {"a": 0, "k": {
                "v": [[round(x * k + ox, 2), round(y * k + oy, 2)] for x, y in path["v"]],
                "i": [[round(a * k, 2), round(b * k, 2)] for a, b in path["i"]],
                "o": [[round(a * k, 2), round(b * k, 2)] for a, b in path["o"]],
                "c": True}}})
        nm = LOGO_LAYER if layer.get("color") else NICK_LAYER
        paints = [_fill(rgb)]
        if layer.get("color") and "logo" in on:
            paints.insert(0, _grad_fill(grad, fitted, shades.get(n, 0.5)))
        elif not layer.get("color") and "text" in on:
            paints.insert(0, _grad_fill(grad, fitted))  # поверх заливки ника: она остаётся меткой надписи
        if len(paints) > 1:
            _hide(paints[1])
        groups.append({"ty": "gr", "nm": nm, "it": [*items, *paints, _transform()]})
    return groups[::-1]  # в Lottie первый элемент группы рисуется поверх остальных


def _layout(box, text: dict | None, logo: dict | None, size: float = 1.0):
    """Где рисовать логотип и текст внутри места надписи. → [(рисунок, область)]."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if logo and not text:
        lh = min(w, h * LOGO_HEIGHT)
        return [(logo, (x0, cy - lh / 2, x1, cy + lh / 2))]
    if text and not logo:
        hw, hh = w * TEXT_FIT_W / 2, h * TEXT_FIT_H / 2
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
        (text, _shrink((left + side + h * GAP, y0, left + total, y1), TEXT_WITH_LOGO)),
    ]


def text_size(value) -> float:
    """«Размер надписи» в процентах (100 — автоподбор) → множитель в допустимых пределах."""
    try:
        k = float(value) / 100 if value is not None else 1.0
    except (TypeError, ValueError):
        k = 1.0
    return max(SIZE_RANGE[0], min(SIZE_RANGE[1], k))


def clean_place(value) -> dict | None:
    """«Положение надписи» {x, y, r}: сдвиг по x и y в % ширины места надписи (−100…100) и поворот в градусах
    (−180…180). Пусто или всё по нулям → None (надпись по центру, как в шаблоне). ValueError — мусор."""
    if value in (None, {}, ""):
        return None
    if not isinstance(value, dict) or not set(value) <= set(PLACE_RANGE):
        raise ValueError("place")
    out = {}
    for key, lim in PLACE_RANGE.items():
        v = value.get(key, 0)
        if v is None:
            v = 0
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError("place")
        out[key] = int(max(-lim, min(lim, round(v))))
    return out if any(out.values()) else None


def _moved(items: list, box, place: dict | None, size: float = 1.0) -> list:
    """Надпись (группы рисунка) со сдвигом, масштабом и поворотом вокруг её центра — одной группой-обёрткой.
    Сдвиг — в долях ширины места надписи: так он одинаково ощущается в шаблонах разного масштаба."""
    needs_move = bool(place)
    needs_scale = size is not None and abs(size - 1.0) > 1e-4
    if (not needs_move and not needs_scale) or not items:
        return items
    x0, y0, x1, y1 = box
    cx, cy, w = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0
    dx = w * place.get("x", 0) / 100 if place else 0
    dy = w * place.get("y", 0) / 100 if place else 0
    rot = place.get("r", 0) if place else 0
    scale = round(100 * (size if size is not None else 1.0), 2)
    tr = _transform()
    tr["a"]["k"] = [round(cx, 2), round(cy, 2)]
    tr["p"]["k"] = [round(cx + dx, 2), round(cy + dy, 2)]
    tr["r"]["k"] = rot
    tr["s"]["k"] = [scale, scale]
    return [{"ty": "gr", "nm": "ezz text", "it": [*items, tr]}]


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


# ───────────────────────── градиент ─────────────────────────
# grad = {"a": "#rrggbb", "b": "#rrggbb", "angle": 0…359, "on": ["text", "logo", "fill"]}
# angle — как в CSS linear-gradient: 0 — снизу вверх, 90 — слева направо, 180 — сверху вниз.
# Ник и буквы «ezzprod»: градиент кладётся поверх их заливки «ezzprod color» (она остаётся под ним — по ней
# надпись находят перекраска, редактор и эффекты). Логотип: оттенки его слоёв сохраняются (светлые слои —
# светлее, тёмные — темнее), чтобы детали не слились. Заливка: основной цвет стикера → градиент по каждой фигуре.

GRAD_NAME = "ezz grad"
GRAD_TARGETS = ("text", "logo", "fill")


def clean_grad(value) -> dict | None:
    """Градиент из запроса: чистое значение, None (выключен) или ValueError — мусор."""
    if value in (None, {}, ""):
        return None
    if not isinstance(value, dict) or not set(value) <= {"a", "b", "angle", "on"}:
        raise ValueError("grad")
    a, b = value.get("a"), value.get("b")
    if not all(isinstance(c, str) and len(c) == 7 and c[0] == "#" and all(x in "0123456789abcdefABCDEF" for x in c[1:])
               for c in (a, b)):
        raise ValueError("grad")
    angle = value.get("angle", 180)
    if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle):
        raise ValueError("grad")
    on = value.get("on")
    if not isinstance(on, list) or not all(isinstance(t, str) and t in GRAD_TARGETS for t in on):
        raise ValueError("grad")
    on = [t for t in GRAD_TARGETS if t in on]
    if not on:
        return None
    return {"a": a.lower(), "b": b.lower(), "angle": int(round(angle)) % 360, "on": on}


def _grad_points(box, angle: float):
    """Начало и конец линии градиента через центр box под углом angle (как в CSS)."""
    x0, y0, x1, y1 = box
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    rad = math.radians(angle)
    dx, dy = math.sin(rad), -math.cos(rad)
    half = abs(hw * dx) + abs(hh * dy) or 1.0
    return [cx - dx * half, cy - dy * half], [cx + dx * half, cy + dy * half]


def _grad_paint(a: list[float], b: list[float], s, e) -> dict:
    stops = [0, *[round(c, 3) for c in a[:3]], 1, *[round(c, 3) for c in b[:3]]]
    return {"ty": "gf", "nm": GRAD_NAME, "o": {"a": 0, "k": 100}, "r": 1, "bm": 0, "t": 1,
            "s": {"a": 0, "k": [round(s[0], 2), round(s[1], 2)]}, "e": {"a": 0, "k": [round(e[0], 2), round(e[1], 2)]},
            "g": {"p": 2, "k": {"a": 0, "k": stops}}}


def _grad_fill(grad: dict, box, shade: float | None = None) -> dict:
    """Градиентная заливка по box. shade (0…1) — тон слоя логотипа: 0.5 — чистый градиент, ближе к 1 —
    светлее, ближе к 0 — темнее."""
    a, b = hex_to_rgb(grad["a"]), hex_to_rgb(grad["b"])
    if shade is not None:
        a, b = _shade(a, shade), _shade(b, shade)
    s, e = _grad_points(box, grad["angle"])
    return _grad_paint(a, b, s, e)


def _shade(rgb: list[float], t: float) -> list[float]:
    if t >= 0.5:
        k, to = min(0.8, (t - 0.5) * 1.6), (1.0, 1.0, 1.0)
    else:
        k, to = min(0.6, (0.5 - t) * 1.2), (0.0, 0.0, 0.0)
    return [c + (d - c) * k for c, d in zip(rgb[:3], to)]


def _luma(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _logo_shades(drawing: dict) -> dict[int, float]:
    """Тон каждого цветного слоя логотипа: по яркости, растянутой на 0…1 среди слоёв; один тон — 0.5."""
    lum = {n: _luma([c / 255 for c in layer["color"]]) for n, layer in enumerate(drawing["layers"]) if layer.get("color")}
    if not lum:
        return {}
    lo, hi = min(lum.values()), max(lum.values())
    if hi - lo < 0.08:
        return {n: 0.5 for n in lum}
    return {n: (v - lo) / (hi - lo) for n, v in lum.items()}


def _inverse_point(tr: dict | None, x: float, y: float):
    """Точка из пространства родителя в локальное пространство группы с преобразованием tr (обратное _matrix)."""
    if not tr:
        return x, y
    ax, ay = (_static(tr.get("a"), [0, 0]) + [0, 0])[:2]
    px, py = (_static(tr.get("p"), [0, 0]) + [0, 0])[:2]
    sx, sy = (_static(tr.get("s"), [100, 100]) + [100, 100])[:2]
    r = math.radians(_static(tr.get("r"), 0) or 0)
    if not sx or not sy:
        return None
    x, y = x - px, y - py
    cos, sin = math.cos(r), math.sin(r)
    x, y = x * cos + y * sin, -x * sin + y * cos
    return x * 100 / sx + ax, y * 100 / sy + ay


def drop_text_grads(o, in_logo: bool = False) -> None:
    """Убирает градиент с надписи (ника и букв «ezzprod»); у логотипа он остаётся."""
    if isinstance(o, dict):
        in_logo = in_logo or (o.get("ty") == "gr" and o.get("nm") == LOGO_LAYER)
        for v in o.values():
            if isinstance(v, (dict, list)):
                drop_text_grads(v, in_logo)
    elif isinstance(o, list):
        if not in_logo and any(isinstance(v, dict) and v.get("ty") == "gf" and v.get("nm") == GRAD_NAME for v in o):
            slots = [i for i in o if isinstance(i, dict) and i.get("ty") == "fl" and i.get("nm") == SLOT]
            if slots:
                o[:] = [v for v in o if not (isinstance(v, dict) and v.get("ty") == "gf" and v.get("nm") == GRAD_NAME)]
                for i in slots:
                    i["o"] = {"a": 0, "k": 100}  # заливка снова видна
        for v in o:
            drop_text_grads(v, in_logo)


def _grad_letters(group: dict, grad: dict) -> None:
    """Родные буквы «ezzprod»: один градиент на всё слово, пересчитанный в пространство каждой буквы."""
    box = _letters_bbox(group)
    if box is None:
        return
    s, e = _grad_points(box, grad["angle"])
    a, b = hex_to_rgb(grad["a"]), hex_to_rgb(grad["b"])
    for letter in group["it"]:
        if not _is_letter(letter):
            continue
        tr = next((i for i in letter["it"] if i.get("ty") == "tr"), None)
        ls, le = _inverse_point(tr, *s), _inverse_point(tr, *e)
        if ls is None or le is None:
            continue
        n = next(n for n, i in enumerate(letter["it"]) if i.get("ty") == "fl" and i.get("nm") == SLOT)
        _hide(letter["it"][n])
        letter["it"].insert(n, _grad_paint(a, b, ls, le))


def _hide(paint: dict) -> None:
    """Сплошная заливка под градиентом — прозрачная: иначе её сглаженный край светится каймой вокруг букв.
    Сама заливка остаётся: по имени «ezzprod color» надпись находят перекраска, редактор и эффекты."""
    paint["o"] = {"a": 0, "k": 0}


def _points(items: list, f, out: list) -> list:
    """Вершины фигур списка элементов (с вложенными группами и их преобразованиями) — в пространстве f."""
    for i in items:
        if not isinstance(i, dict):
            continue
        if i.get("ty") == "gr":
            sub = i.get("it") or []
            m = _matrix(next((j for j in sub if isinstance(j, dict) and j.get("ty") == "tr"), None))
            _points([j for j in sub if not (isinstance(j, dict) and j.get("ty") == "tr")],
                    lambda x, y, m=m: f(*m(x, y)), out)
        elif i.get("ty") == "sh":
            shape = _static(i.get("ks"), None)
            if isinstance(shape, list):
                shape = shape[0] if shape else None
            for v in (shape.get("v", ()) if isinstance(shape, dict) else ()):
                out.append(f(v[0], v[1]))
    return out


def _grad_body(anim: dict, src: list[float], grad: dict) -> None:
    """Основная заливка стикера (цвет src) → градиент по габаритам каждой фигуры этого цвета."""
    def visit(o) -> None:
        if isinstance(o, dict):
            items = o.get("it") if o.get("ty") == "gr" else o.get("shapes") if o.get("ty") == 4 else None
            if isinstance(items, list) and not any(_is_letter(i) for i in items if isinstance(i, dict)):
                for n, i in enumerate(items):
                    if (isinstance(i, dict) and i.get("ty") == "fl" and i.get("nm") != SLOT
                            and (rgb := _solid(i)) is not None and _near(rgb, src)):
                        pts = _points(items, lambda x, y: (x, y), [])
                        if pts:
                            box = (min(p[0] for p in pts), min(p[1] for p in pts),
                                   max(p[0] for p in pts), max(p[1] for p in pts))
                            paint = _grad_fill(grad, box)
                            paint["o"] = i.get("o", paint["o"])
                            items[n] = paint
            for v in o.values():
                visit(v)
        elif isinstance(o, list):
            for v in o:
                visit(v)
    visit(anim)


def personalize(anim: dict, *, color: list[float] | None, text: dict | None, logo: dict | None,
                stroke: list[float] | None = None, size: float = 1.0, place: dict | None = None,
                grad: dict | None = None) -> dict:
    """Копия анимации: основной цвет стикера (color — «Заливка», None — родной), контуры и надпись
    (stroke — «Обводка», None — родные), ник и/или логотип вместо «ezzprod» (size — множитель размера ника,
    place — сдвиг и поворот надписи, см. clean_place), градиент на ник, логотип и/или заливку (см. clean_grad)."""
    out = copy.deepcopy(anim)
    on = set(grad["on"]) if grad else set()
    if color or stroke or "fill" in on:
        main_fill, main_stroke = main_colors(out)
        if color and main_fill:
            _recolor(out, ("fl",), main_fill, list(color))
        if stroke and main_stroke:  # контуры и всё того же цвета, нарисованное заливкой
            _recolor(out, ("st", "fl"), main_stroke, list(stroke))
        if "fill" in on and main_fill:
            _grad_body(out, list(color) if color else main_fill, grad)
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
            if "text" in on:
                _grad_letters(group, grad)
            has_transform = bool(place) or (size is not None and abs(size - 1.0) > 1e-4)
            if not has_transform:
                continue
            box = _letters_bbox(group)
            if box is None:
                continue
            letters = [i for i in group["it"] if _is_letter(i)]
            drawn = _moved(letters, box, place, size)
            rest = [i for i in group["it"] if not _is_letter(i)]
            tr = [i for i in rest if i.get("ty") == "tr"]
            other = [i for i in rest if i.get("ty") != "tr"]
            group["it"] = other + drawn + tr
            continue
        box = _letters_bbox(group)
        if box is None:
            continue
        drawn = []
        for drawing, area in _layout(box, text, logo):
            drawn += _place(drawing, area, text_rgb, grad)
        drawn = _moved(drawn, box, place, size)
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


# поля, которые Telegram принимает только целыми: «512.0» вместо «512» он отклоняет как «wrong file type»
INT_KEYS = ("w", "h", "ip", "op", "st", "fr", "ind", "parent")


def _ints(o) -> None:
    if isinstance(o, dict):
        for k in INT_KEYS:
            v = o.get(k)
            if isinstance(v, float) and v.is_integer():
                o[k] = int(v)
        for v in o.values():
            if isinstance(v, (dict, list)):
                _ints(v)
    elif isinstance(o, list):
        for v in o:
            if isinstance(v, (dict, list)):
                _ints(v)


def encode(anim: dict) -> bytes:
    """Анимация → байты .tgs (компактный JSON + gzip). Целые поля приводятся к целым (см. INT_KEYS)."""
    _ints(anim)
    raw = json.dumps(anim, separators=(",", ":"), ensure_ascii=False).encode()
    return gzip.compress(raw, compresslevel=9, mtime=0)


def hex_to_rgb(color: str | None) -> list[float] | None:
    if not color:
        return None
    c = color.lstrip("#")
    return [int(c[i: i + 2], 16) / 255 for i in (0, 2, 4)]
