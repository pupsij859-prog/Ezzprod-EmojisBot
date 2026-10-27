"""Продвинутый редактор (Pro): части эмодзи, все цвета, эффекты и проверка под требования Telegram.

Правки пользователя приходят маленьким описанием (edit) и применяются к уже персонализированной анимации
(app/lottie.py → personalize). Один и тот же код строит превью в редакторе (POST /api/render) и файл пака
(app/build/render.py), поэтому превью совпадает с результатом.

edit = {
  "parts":  {"<id части>": {"x", "y", "s", "r", "o", "fill", "anim"}},  # сдвиг, масштаб %, поворот °, прозрачность %
  "colors": {"#исходный": "#новый"},                                       # «все цвета»
  "fx": {"pixel": {...}, "metal": {...}, "glitch": {...}, "holo": {...}, "burst": {...}},
  "add": [{"kind": "square", "fill": "#rrggbb", "round": 0..50}],          # новые фигуры (веб-редактор), части r.9001…
}

Часть эмодзи — слой-фигура Lottie (ty 4) в корне или в прекомпозиции; id «r.<ind>» или «a<номер ассета>.<ind>».
Сдвиг/масштаб/поворот и анимации деталей — через новый нулевой слой-родитель, который вращает и масштабирует
часть вокруг её центра: собственная анимация слоя не трогается.

В TGS нельзя эффекты, маски и выражения, поэтому эффекты собраны из того, что Telegram умеет:
- пикселизация — мозаика из квадратиков с зазорами: видимый цвет в центре каждой клетки на каждом кадре (app/pixel.py);
- блеск металла и голограмма — градиентные заливки с анимированными точками градиента;
- глитч — сдвиги всей картинки, цветные силуэты-копии, полосы и чёрточки в моменты сбоя;
- эффект при показе (burst) — частицы вокруг эмодзи в начале каждого цикла. Эффект «по нажатию» в Telegram
  есть только у премиум-стикеров самого Telegram, ботам он недоступен — поэтому эффект встроен в анимацию.
"""
from __future__ import annotations

import copy
import gzip
import json
import math
import random
import re

from . import lottie
from .lottie import SLOT, TG_LIMIT, _static

HEX = re.compile(r"^#[0-9a-fA-F]{6}\Z")
PID = re.compile(r"^(r|a\d{1,4})\.-?\d{1,7}\Z")

ANIMS = ("none", "appear", "jump", "pulse", "swing", "shake", "float", "spin", "heart")
SHAPES = ("square", "soft", "circle")
PALETTES = {
    "red": ("#ff2d55",),
    "blue": ("#2fa8ff",),
    "purple": ("#9b5cff",),
    "yellow": ("#ffc23d",),
    "green": ("#2fe0a5",),
    "pink": ("#ff5ccf",),
    "rainbow": ("#ff4d6d", "#ffb84d", "#f9f871", "#4dffa6", "#4dc3ff", "#9b6bff"),
}
BURSTS = ("none", "sparks", "hearts", "confetti", "stars", "flash")

# значения по умолчанию и допустимые диапазоны настроек эффектов: (по умолчанию, мин, макс)
FX = {
    # пределы мозаики — под бюджет сложности Telegram (COMPLEXITY): крупнее сетка и чаще кадры он не принимает
    "pixel": {"on": False, "grid": (24, 8, 40), "shape": "square", "fps": (12, 4, 20), "colors": (48, 2, 64),
              "lines": (80, 0, 100), "gap": (14, 0, 35)},
    "metal": {"on": False, "width": (100, 20, 300)},
    "glitch": {"on": False, "count": (6, 1, 16), "len": (100, 20, 300), "shift": (100, 0, 300),
               "stripes": (8, 0, 24), "dashes": (18, 0, 48), "ghost": True, "c1": "#ff1f4b", "c2": "#18e0ff",
               "seed": (1, 0, 99999)},
    "holo": {"on": False, "only": False, "parts": [], "palette": "red", "dir": "cw", "turns": (1, 1, 6),
             "width": (100, 20, 300), "bright": (88, 0, 100), "gloss": True,
             "target": "contour", "dark": True, "tint": None,  # контур / заливки / всё, свой оттенок хрома
             "edge": (10, 0, 32)},  # хромовая кромка по границе стикера, px холста (0 — без кромки)
    "burst": {"kind": "none", "count": (10, 4, 20), "color": None},
}
PART_RANGES = {"x": (-1024, 1024), "y": (-1024, 1024), "s": (0, 500), "r": (-720, 720), "o": (0, 100)}


# ───────────────────────── проверка того, что прислал клиент ─────────────────────────

def _hex(value, allow_none: bool = True) -> str | None:
    if value in (None, "") and allow_none:
        return None
    if not (isinstance(value, str) and HEX.match(value)):
        raise ValueError("color")
    return value.lower()


def _num(value, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("number")
    return round(max(lo, min(hi, float(value))), 2)


def _clean_fx(name: str, raw) -> dict:
    spec = FX[name]
    if not isinstance(raw, dict):
        raise ValueError(name)
    # Незнакомые ключи пропускаем: Mini App и сервер обновляются не одновременно, а правки лежат у пользователя
    # в localStorage — старый или новый ключ не должен ломать превью и сборку.
    out: dict = {}
    for key, default in spec.items():
        value = raw.get(key, default[0] if isinstance(default, tuple) else default)
        if isinstance(default, tuple):
            out[key] = int(_num(value, default[1], default[2]))
        elif isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(key)
            out[key] = value
        elif key in ("c1", "c2", "color", "tint"):
            out[key] = _hex(value)
        elif key == "parts":
            if not isinstance(value, list) or len(value) > 128 or not all(isinstance(p, str) and PID.match(p) for p in value):
                raise ValueError(key)
            out[key] = list(dict.fromkeys(value))
        else:
            allowed = {"shape": SHAPES, "palette": tuple(PALETTES), "dir": ("cw", "ccw"), "kind": BURSTS,
                       "target": ("contour", "fill", "all")}[key]
            if value not in allowed:
                raise ValueError(key)
            out[key] = value
    return out


def clean_edit(raw) -> dict | None:
    """Проверяет edit от клиента. → чистая копия или None (правок нет). Ошибка формата — ValueError."""
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict) or not set(raw) <= {"parts", "colors", "fx", "add"}:
        raise ValueError("edit")
    out: dict = {}
    parts = raw.get("parts") or {}
    if not isinstance(parts, dict) or len(parts) > 128:
        raise ValueError("parts")
    clean_parts = {}
    for pid, p in parts.items():
        if not (isinstance(pid, str) and PID.match(pid)) or not isinstance(p, dict):
            raise ValueError("parts")
        if not set(p) <= {*PART_RANGES, "fill", "anim"}:
            raise ValueError("parts")
        q: dict = {}
        for key, (lo, hi) in PART_RANGES.items():
            if key in p:
                q[key] = _num(p[key], lo, hi)
        if "fill" in p and p["fill"] is not None:
            q["fill"] = _hex(p["fill"])
        if "anim" in p:
            if p["anim"] not in ANIMS:
                raise ValueError("anim")
            if p["anim"] != "none":
                q["anim"] = p["anim"]
        if q:
            clean_parts[pid] = q
    if clean_parts:
        out["parts"] = clean_parts
    colors = raw.get("colors") or {}
    if not isinstance(colors, dict) or len(colors) > 128:
        raise ValueError("colors")
    clean_colors = {_hex(k, False): _hex(v, False) for k, v in colors.items()}
    clean_colors = {k: v for k, v in clean_colors.items() if k != v}
    if clean_colors:
        out["colors"] = clean_colors
    fx = raw.get("fx") or {}
    if not isinstance(fx, dict) or not set(fx) <= set(FX):
        raise ValueError("fx")
    clean_fx = {}
    for name, value in fx.items():
        c = _clean_fx(name, value)
        if c.get("on") or (name == "burst" and c["kind"] != "none"):
            clean_fx[name] = c
    if clean_fx:
        out["fx"] = clean_fx
    add = raw.get("add") or []
    if not isinstance(add, list) or len(add) > ADD_MAX:
        raise ValueError("add")
    clean_add = []
    for shape in add:
        if not isinstance(shape, dict) or shape.get("kind") not in ADD_KINDS or not set(shape) <= {"kind", "fill", "round"}:
            raise ValueError("add")
        clean_add.append({"kind": shape["kind"], "fill": _hex(shape.get("fill")) or "#ffffff",
                          "round": int(_num(shape.get("round", 0), 0, 50))})
    if clean_add:
        out["add"] = clean_add
    return out or None


# ───────────────────────── новые фигуры (веб-редактор: кнопка «квадрат») ─────────────────────────

ADD_KINDS = ("square",)
ADD_MAX = 8
ADD_IND = 9001  # номера слоёв новых фигур: id части «r.9001», «r.9002»… — по ним их двигают и красят как обычные части
ADD_SIZE = 160


def add_shapes(anim: dict, edit: dict | None) -> dict:
    """Добавляет фигуры из edit["add"] поверх эмодзи (по центру холста) и возвращает анимацию. Повторный вызов
    ничего не дублирует. Дальше это обычные части: сдвиг, масштаб, цвет и анимация — через edit["parts"]."""
    shapes = (edit or {}).get("add") or []
    layers = anim.setdefault("layers", [])
    have = {L.get("ind") for L in layers if isinstance(L, dict)}
    w, h = anim.get("w") or 512, anim.get("h") or 512
    ip, op = anim.get("ip", 0), anim.get("op", 60)
    for i, shape in enumerate(shapes):
        ind = ADD_IND + i
        if ind in have:
            continue
        side = ADD_SIZE * min(w, h) / 512
        rect = _rect(0, 0, side, side, lottie.hex_to_rgb(shape["fill"]))
        rect[0]["it"][0]["r"] = {"a": 0, "k": round(side * shape["round"] / 100, 1)}
        layer = _layer(ind, f"квадрат {i + 1}", rect, ip, op, {"p": {"a": 0, "k": [w / 2, h / 2, 0]}})
        layers.insert(0, layer)  # первый слой Lottie рисуется поверх остальных
    return anim


# ───────────────────────── геометрия ─────────────────────────

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mul(m, n):
    """m∘n: сначала n, потом m. Матрица (a, b, c, d, e, f): x' = a·x + c·y + e, y' = b·x + d·y + f."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B, a * C + c * D, b * C + d * D, a * E + c * F + e, b * E + d * F + f)


def _apply(m, x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _scale(m) -> float:
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2])) or 1e-6


def _inverse(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-9:
        return None
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


def rest_time(anim: dict) -> float:
    """Кадр, в котором меряем геометрию. В кадре 0 у многих шаблонов детали ещё «вылетают» из масштаба 0,
    а ближе к середине цикла начинается вторая половина движения; около трети цикла всё обычно стоит."""
    ip, op = anim.get("ip", 0), anim.get("op", 180)
    return round(ip + (op - ip) * 0.35)


def _vec(prop, default: list[float], t: float | None = None) -> list[float]:
    if _is_anim(prop):
        v = value_at(prop["k"], prop["k"][0]["t"] if t is None else t)
    else:
        v = _static(prop, default)
    if isinstance(v, (int, float)):
        v = [v]
    if not isinstance(v, list):
        v = default
    v = [x if isinstance(x, (int, float)) else 0 for x in v]
    return (v + default[len(v):])[: len(default)]


def _position(tr: dict, t: float | None = None) -> list[float]:
    p = tr.get("p")
    if isinstance(p, dict) and p.get("s"):  # координаты заданы раздельно
        return [_vec(p.get("x"), [0], t)[0], _vec(p.get("y"), [0], t)[0]]
    return _vec(p, [0, 0], t)


def _tr_matrix(tr: dict | None, t: float | None = None):
    """Преобразование слоя/группы Lottie в кадре t (None — первый): перенос·поворот·масштаб·(−якорь)."""
    if not tr:
        return IDENT
    ax, ay = _vec(tr.get("a"), [0, 0], t)
    px, py = _position(tr, t)
    sx, sy = _vec(tr.get("s"), [100, 100], t)
    r = math.radians(_vec(tr.get("r"), [0], t)[0])
    cos, sin = math.cos(r), math.sin(r)
    sx, sy = sx / 100, sy / 100
    return (cos * sx, sin * sx, -sin * sy, cos * sy, px - (cos * sx * ax - sin * sy * ay), py - (sin * sx * ax + cos * sy * ay))


def _shape_value(item: dict, t: float | None = None):
    ks = item.get("ks")
    shape = value_at(ks["k"], ks["k"][0]["t"] if t is None else t) if _is_anim(ks) else _static(ks, None)
    if isinstance(shape, list):
        shape = shape[0] if shape else None
    return shape if isinstance(shape, dict) and shape.get("v") else None


def _points(items: list, m, out: list, t: float | None = None) -> list:
    """Вершины всех фигур (в кадре t) в координатах матрицы m."""
    for it in items:
        if not isinstance(it, dict) or it.get("hd"):
            continue
        ty = it.get("ty")
        if ty == "gr":
            sub = it.get("it") or []
            sub_tr = next((i for i in sub if isinstance(i, dict) and i.get("ty") == "tr"), None)
            _points(sub, _mul(m, _tr_matrix(sub_tr, t)), out, t)
        elif ty == "sh":
            shape = _shape_value(it, t)
            if shape:
                out.extend(_apply(m, v[0], v[1]) for v in shape["v"] if len(v) >= 2)
        elif ty in ("el", "rc"):
            (cx, cy), (w, h) = _vec(it.get("p"), [0, 0], t), _vec(it.get("s"), [0, 0], t)
            out.extend(_apply(m, cx + dx * w / 2, cy + dy * h / 2) for dx in (-1, 1) for dy in (-1, 1))
    return out


def _bbox(points) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _has_slot(o) -> bool:
    if isinstance(o, dict):
        if o.get("ty") == "fl" and o.get("nm") == SLOT:
            return True
        return any(_has_slot(v) for v in o.values() if isinstance(v, (dict, list)))
    if isinstance(o, list):
        return any(_has_slot(v) for v in o)
    return False


def _walk_layers(anim: dict, t: float | None = None):
    """Слои в порядке отрисовки (сверху вниз) с мировыми матрицами в кадре t: (scope, слой, матрица слоя,
    матрица родителя). Прекомпозиция раскрывается на месте; ассет, использованный несколько раз, — по первому
    вхождению."""
    assets = {a.get("id"): (i, a) for i, a in enumerate(anim.get("assets") or []) if isinstance(a, dict) and "layers" in a}
    seen: set[str] = set()

    def comp(scope: str, layers: list, M, depth: int):
        by_ind = {L.get("ind"): L for L in layers if isinstance(L, dict)}
        cache: dict[int, tuple] = {}

        def chain(L, guard=0):
            key = id(L)
            if key not in cache:
                par = by_ind.get(L.get("parent")) if "parent" in L else None
                base = chain(par, guard + 1) if par is not None and par is not L and guard < 32 else M
                cache[key] = _mul(base, _tr_matrix(L.get("ks") or {}, t))
            return cache[key]

        for L in layers:
            if not isinstance(L, dict):
                continue
            par = by_ind.get(L.get("parent")) if "parent" in L else None
            parent_m = chain(par) if par is not None and par is not L else M
            yield scope, L, chain(L), parent_m
            if L.get("ty") == 0 and L.get("refId") in assets and depth < 8:
                i, a = assets[L["refId"]]
                if a["id"] not in seen:
                    seen.add(a["id"])
                    yield from comp(f"a{i}", a["layers"], chain(L), depth + 1)

    yield from comp("r", anim.get("layers") or [], IDENT, 0)


def _layer_index(anim: dict) -> dict[str, tuple[list, dict]]:
    """id части → (список слоёв, где она лежит; слой)."""
    out = {}
    for L in anim.get("layers") or []:
        if isinstance(L, dict) and L.get("ty") == 4:
            out[f"r.{L.get('ind')}"] = (anim["layers"], L)
    for i, a in enumerate(anim.get("assets") or []):
        for L in (a.get("layers") or []) if isinstance(a, dict) else []:
            if isinstance(L, dict) and L.get("ty") == 4:
                out[f"a{i}.{L.get('ind')}"] = (a["layers"], L)
    return out


# ───────────────────────── части эмодзи: список с названиями ─────────────────────────

_WORDS = {  # основа, окончание м. р., окончание ж. р.
    "white": ("бел", "ый", "ая"), "black": ("чёрн", "ый", "ая"), "gray": ("сер", "ый", "ая"),
    "red": ("красн", "ый", "ая"), "orange": ("оранжев", "ый", "ая"), "brown": ("коричнев", "ый", "ая"),
    "yellow": ("жёлт", "ый", "ая"), "green": ("зелён", "ый", "ая"), "cyan": ("голуб", "ой", "ая"),
    "blue": ("син", "ий", "яя"), "violet": ("фиолетов", "ый", "ая"), "pink": ("розов", "ый", "ая"),
}
_NOUNS = {"circle": ("круг", "m"), "square": ("квадрат", "m"), "rect": ("прямоугольник", "m"),
          "line": ("линия", "f"), "shape": ("фигура", "f")}


def color_word(rgb) -> str:
    r, g, b = (max(0.0, min(1.0, float(x))) for x in rgb[:3])
    mx, mn = max(r, g, b), min(r, g, b)
    light = (mx + mn) / 2
    if mx - mn < 0.1:
        return "white" if light > 0.82 else "black" if light < 0.2 else "gray"
    if mx == r:
        hue = ((g - b) / (mx - mn)) % 6
    elif mx == g:
        hue = (b - r) / (mx - mn) + 2
    else:
        hue = (r - g) / (mx - mn) + 4
    hue *= 60
    if hue < 14 or hue >= 340:
        return "brown" if mx < 0.45 else "red"
    if hue < 42:
        return "brown" if mx < 0.6 else "orange"
    if hue < 68:
        return "yellow"
    if hue < 165:
        return "green"
    if hue < 200:
        return "cyan"
    if hue < 250:
        return "blue"
    if hue < 290:
        return "violet"
    return "pink"


def _kind(layer: dict) -> str:
    prims: list[dict] = []
    fills = strokes = 0

    def visit(items):
        nonlocal fills, strokes
        for it in items:
            if not isinstance(it, dict) or it.get("hd"):
                continue
            ty = it.get("ty")
            if ty == "gr":
                visit(it.get("it") or [])
            elif ty in ("sh", "el", "rc", "sr"):
                prims.append(it)
            elif ty in ("fl", "gf"):
                fills += 1
            elif ty in ("st", "gs"):
                strokes += 1

    visit(layer.get("shapes") or [])
    if strokes and not fills:
        return "line"
    if len(prims) != 1:
        return "shape"
    p = prims[0]
    if p["ty"] == "el":
        return "circle"
    box = None
    if p["ty"] == "rc":
        w, h = _vec(p.get("s"), [1, 1])
        box = (0, 0, w, h)
    elif p["ty"] == "sh":
        shape = _shape_value(p)
        if not shape or len(shape["v"]) != 4:
            return "shape"
        box = _bbox([(v[0], v[1]) for v in shape["v"]])
        curved = any(abs(x) > 1e-3 for pair in (shape.get("i") or []) + (shape.get("o") or []) for x in pair[:2])
        if curved:
            w, h = box[2] - box[0], box[3] - box[1]
            return "circle" if h and 0.8 <= w / h <= 1.25 else "shape"
    if box is None:
        return "shape"
    w, h = box[2] - box[0], box[3] - box[1]
    return "square" if h and 0.85 <= w / h <= 1.18 else "rect"


def _where(box, size: float = 512) -> str:
    x0, y0, x1, y1 = box
    if (x1 - x0) * (y1 - y0) > 0.42 * size * size:
        return ""
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    row = "сверху" if cy < size * 0.36 else "снизу" if cy > size * 0.64 else ""
    col = "слева" if cx < size * 0.36 else "справа" if cx > size * 0.64 else ""
    return " ".join(w for w in (row, col) if w) or "в центре"


def to_hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(float(c) * 255))):02x}" for c in rgb[:3])


def parts(anim: dict, text: str = "") -> list[dict]:
    """Части эмодзи сверху вниз: id, название, тип, цвет, габариты на холсте 512 и масштаб пространства родителя
    (сколько пикселей холста в единице, в которой задаётся сдвиг части)."""
    size = float(anim.get("w") or 512)
    out = []
    t = rest_time(anim)
    for scope, L, lm, pm in _walk_layers(anim, t):
        if L.get("ty") != 4 or L.get("hd"):
            continue
        box = _bbox(_points(L.get("shapes") or [], lm, [], t))
        if box is None:
            continue
        is_text = _has_slot(L)
        fill, stroke = lottie.main_colors(L)
        rgb = fill or stroke or [1, 1, 1]
        if is_text:  # у надписи цвет — цвет букв (их заливки помечены «ezzprod color» и в main_colors не входят)
            letter = next((f for f in lottie.text_fills(L) if lottie._solid(f) is not None), None)
            rgb = lottie._solid(letter) if letter is not None else rgb
        kind = "text" if is_text else _kind(L)
        if is_text:
            name = f"«{text}»" if text else "надпись"
        else:
            noun, gender = _NOUNS[kind]
            stem, m, f = _WORDS[color_word(rgb)]
            name = " ".join(w for w in (stem + (m if gender == "m" else f), noun, _where(box, size)) if w)
        out.append({"id": f"{scope}.{L.get('ind')}", "name": name, "kind": kind, "color": to_hex(rgb),
                    "bbox": [round(v, 1) for v in box], "scale": round(_scale(pm), 4)})
    counts: dict[str, int] = {}
    for p in out:
        counts[p["name"]] = counts.get(p["name"], 0) + 1
    seen: dict[str, int] = {}
    for p in out:  # одинаковые названия нумеруем, как в референсе: «белая фигура снизу 1», «… 2»
        if counts[p["name"]] > 1:
            seen[p["name"]] = seen.get(p["name"], 0) + 1
            p["name"] = f"{p['name']} {seen[p['name']]}"
    return out


def _solid_paints(o, kinds=("fl", "st")):
    """Все сплошные заливки/обводки (кроме надписи «ezzprod color»)."""
    if isinstance(o, dict):
        if o.get("ty") in kinds and o.get("nm") != SLOT and lottie._solid(o) is not None:
            yield o
        for v in o.values():
            if isinstance(v, (dict, list)):
                yield from _solid_paints(v, kinds)
    elif isinstance(o, list):
        for v in o:
            yield from _solid_paints(v, kinds)


def colors(anim: dict) -> list[dict]:
    """«Все цвета»: каждый сплошной цвет и в скольких местах он встречается, по убыванию."""
    count: dict[str, int] = {}
    for item in _solid_paints(anim):
        h = to_hex(lottie._solid(item))
        count[h] = count.get(h, 0) + 1
    return [{"hex": h, "count": n} for h, n in sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))]


# ───────────────────────── ключевые кадры ─────────────────────────

def _cubic(u: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """Кривая сглаживания Lottie: по доле времени u → доля значения."""
    if u <= 0 or u >= 1 or (abs(x1 - y1) < 1e-6 and abs(x2 - y2) < 1e-6):
        return min(1.0, max(0.0, u))
    bx = lambda s: 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s * s * x2 + s ** 3  # noqa: E731
    by = lambda s: 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s * s * y2 + s ** 3  # noqa: E731
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if bx(mid) < u:
            lo = mid
        else:
            hi = mid
    return by((lo + hi) / 2)


def _ease(k0: dict, dim: int, u: float) -> float:
    o, i = k0.get("o") or {}, k0.get("i") or {}

    def g(v, default):
        if isinstance(v, list):
            v = v[dim] if dim < len(v) else (v[0] if v else default)
        return float(v) if isinstance(v, (int, float)) else default

    return _cubic(u, g(o.get("x"), 0), g(o.get("y"), 0), g(i.get("x"), 1), g(i.get("y"), 1))


def _lerp_shape(a: dict, b: dict, e: float) -> dict:
    if not (isinstance(a, dict) and isinstance(b, dict)) or len(a.get("v", ())) != len(b.get("v", ())):
        return a
    out = {"c": a.get("c", True)}
    for key in ("v", "i", "o"):
        pa, pb = a.get(key) or [], b.get(key) or []
        out[key] = [[x + (y - x) * e for x, y in zip(p, q)] for p, q in zip(pa, pb)] if len(pa) == len(pb) else pa
    return out


def _lerp(a, b, u: float, k0: dict):
    if isinstance(a, dict):
        return _lerp_shape(a, b, _ease(k0, 0, u))
    if isinstance(a, list) and isinstance(b, list):
        if a and isinstance(a[0], dict):
            return [_lerp_shape(a[0], b[0] if b else None, _ease(k0, 0, u))]
        return [x + (y - x) * _ease(k0, d, u) if isinstance(x, (int, float)) and isinstance(y, (int, float)) else x
                for d, (x, y) in enumerate(zip(a, b))]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * _ease(k0, 0, u)
    return a


def _kval(kfs: list, j: int):
    s = kfs[j].get("s")
    if s is None and j > 0:
        s = kfs[j - 1].get("e")
    return s


def value_at(kfs: list, t: float):
    """Значение анимированного свойства в момент t (сглаживание учитывается, пространственные касательные — нет)."""
    n = len(kfs)
    if n == 1 or t <= kfs[0]["t"]:
        return _kval(kfs, 0)
    for j in range(n - 1):
        k0, t0, t1 = kfs[j], kfs[j]["t"], kfs[j + 1]["t"]
        if t0 <= t < t1:
            a = _kval(kfs, j)
            b = k0.get("e") if k0.get("e") is not None else _kval(kfs, j + 1)
            if k0.get("h") or b is None or t1 <= t0:
                return a
            return _lerp(a, b, (t - t0) / (t1 - t0), k0)
    last = _kval(kfs, n - 1)
    return last if last is not None else kfs[n - 2].get("e")


def _is_anim(node) -> bool:
    """Анимированное свойство: список ключевых кадров {"t": …}. Флаг "a": 1 есть не у всех шаблонов —
    плееры узнают анимацию по самим кадрам, и мы тоже."""
    if not isinstance(node, dict):
        return False
    k = node.get("k")
    return isinstance(k, list) and bool(k) and isinstance(k[0], dict) and isinstance(k[0].get("t"), (int, float))


def _anim_props(o):
    """Все анимированные свойства (словари {"a": 1, "k": [кадры]}) где угодно в анимации."""
    if isinstance(o, dict):
        if _is_anim(o):
            yield o
            return
        for v in o.values():
            if isinstance(v, (dict, list)):
                yield from _anim_props(v)
    elif isinstance(o, list):
        for v in o:
            yield from _anim_props(v)


def _r(v, nd: int = 2):
    if isinstance(v, float):
        return round(v, nd)
    if isinstance(v, list):
        return [_r(x, nd) for x in v]
    if isinstance(v, dict):
        return {k: _r(x, nd) for k, x in v.items()}
    return v


def _as_list(v):
    return v if isinstance(v, list) else [v]


EASE = {"o": {"x": [0.42], "y": [0]}, "i": {"x": [0.58], "y": [1]}}
LINEAR = {"o": {"x": [0], "y": [0]}, "i": {"x": [1], "y": [1]}}


def _keys(frames: list[tuple[float, list]], easing=EASE, hold: bool = False) -> dict:
    """[(кадр, значение)] → анимированное свойство Lottie (если значение одно — статичное)."""
    if len({json.dumps(_r(v, 3)) for _, v in frames}) == 1:
        return {"a": 0, "k": _r(frames[0][1], 3)}
    k = []
    for n, (t, v) in enumerate(frames):
        kf = {"t": round(t, 3), "s": _r(v, 3)}
        if n < len(frames) - 1:
            if hold:
                kf["h"] = 1
            else:
                kf["o"], kf["i"] = copy.deepcopy(easing["o"]), copy.deepcopy(easing["i"])
        k.append(kf)
    return {"a": 1, "k": k}


# ───────────────────────── части: цвет, движение, анимации деталей ─────────────────────────

def _preset(name: str, L: float, amp: float) -> dict[str, list[tuple[float, float]]]:
    """Анимация детали по каналам: dx, dy (единицы родителя), sc (%), rot (°). amp — 1 пиксель холста."""
    if name == "appear":
        return {"sc": [(0, 0), (18, 112), (28, 96), (36, 100), (L, 100)]}
    if name == "jump":
        h = 34 * amp
        hop = lambda s: [(s, 0), (s + 14, -h), (s + 28, 0)]  # noqa: E731
        return {"dy": hop(0) + hop(L / 2) + [(L, 0)]}
    if name == "pulse":
        p = L / 3
        return {"sc": [(p * n + d, v) for n in range(3) for d, v in ((0, 100), (p / 2, 112))] + [(L, 100)]}
    if name == "swing":
        return {"rot": [(0, 0), (L / 4, 10), (3 * L / 4, -10), (L, 0)]}
    if name == "shake":
        a = 8 * amp
        seq = [(0, 0), (3, -a), (6, a), (9, -a), (12, a), (15, -a / 2), (18, a / 2), (21, 0)]
        return {"dx": seq + [(L / 2 + t, v) for t, v in seq] + [(L, 0)]}
    if name == "float":
        return {"dy": [(0, 0), (L / 2, -14 * amp), (L, 0)]}
    if name == "spin":
        return {"rot": [(0, 0), (L, 360)]}
    if name == "heart":
        beat = lambda s: [(s, 100), (s + 8, 118), (s + 16, 100), (s + 24, 110), (s + 36, 100)]  # noqa: E731
        return {"sc": beat(0) + beat(L / 2) + [(L, 100)]}
    return {}


def _channel(points: list[tuple[float, float]], t: float, default: float) -> float:
    if not points:
        return default
    if t <= points[0][0]:
        return points[0][1]
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return v1
            return v0 + (v1 - v0) * _cubic((t - t0) / (t1 - t0), 0.42, 0, 0.58, 1)
    return points[-1][1]


def _scale_opacity(ks: dict, k: float) -> None:
    o = ks.get("o")
    if _is_anim(o):
        for kf in o["k"]:
            for key in ("s", "e"):
                if isinstance(kf.get(key), list):
                    kf[key] = [v * k if isinstance(v, (int, float)) else v for v in kf[key]]
    else:
        v = _vec(o, [100])[0]
        ks["o"] = {"a": 0, "k": round(v * k, 2)}


def _edit_part(anim: dict, layers: list, L: dict, p: dict, amp: float) -> None:
    if p.get("fill"):
        if _has_slot(L):  # надпись: красим буквы (обычная перекраска их пропускает), цвета логотипа не трогаем
            rgb = lottie.hex_to_rgb(p["fill"])
            for f in lottie.text_fills(L):
                f["c"] = {"a": 0, "k": [*rgb, 1]}
            lottie.drop_text_grads(L)  # градиент ника лежит поверх букв — без него виден выбранный цвет
        else:
            fill, stroke = lottie.main_colors(L)
            src = fill or stroke
            if src:
                lottie._recolor(L, ("fl",) if fill else ("st",), src, lottie.hex_to_rgb(p["fill"]))
    if "o" in p and p["o"] != 100:
        _scale_opacity(L.setdefault("ks", {}), p["o"] / 100)
    x, y, s, r, preset = p.get("x", 0), p.get("y", 0), p.get("s", 100), p.get("r", 0), p.get("anim")
    if not (x or y or s != 100 or r or preset):
        return
    t = rest_time(anim)
    box = _bbox(_points(L.get("shapes") or [], _tr_matrix(L.get("ks") or {}, t), [], t))
    if box is None:
        return
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    ip, op = anim.get("ip", 0), anim.get("op", 180)
    ch = _preset(preset, op - ip, amp) if preset else {}
    times = sorted({ip + t for pts in ch.values() for t, _ in pts}) or [ip]
    shift = lambda pts, t: _channel([(ip + a, b) for a, b in pts], t, 0)  # noqa: E731
    p_frames = [(t, [cx + x + shift(ch.get("dx", []), t), cy + y + shift(ch.get("dy", []), t), 0]) for t in times]
    s_frames = [(t, [s * (shift(ch["sc"], t) if "sc" in ch else 100) / 100] * 2 + [100]) for t in times]
    r_frames = [(t, [r + shift(ch.get("rot", []), t)]) for t in times]
    easing = LINEAR if preset == "spin" else EASE
    ind = max((l.get("ind", 0) for l in layers if isinstance(l.get("ind"), int)), default=0) + 1
    null = {"ddd": 0, "ind": ind, "ty": 3, "nm": "ezz part", "sr": 1, "ao": 0, "bm": 0,
            "ip": min(ip, L.get("ip", ip)), "op": max(op, L.get("op", op)), "st": 0,
            "ks": {"o": {"a": 0, "k": 0}, "a": {"a": 0, "k": [round(cx, 2), round(cy, 2), 0]},
                   "p": _keys(p_frames, easing), "s": _keys(s_frames, easing),
                   "r": _keys([(t, v[0]) for t, v in r_frames], easing)}}
    rk = null["ks"]["r"]
    if rk["a"]:
        for kf in rk["k"]:
            kf["s"] = _as_list(kf["s"])
    if "parent" in L:
        null["parent"] = L["parent"]
    L["parent"] = ind
    layers.append(null)


# ───────────────────────── эффекты ─────────────────────────

def _mix(a, b, k: float) -> list[float]:
    return [x + (y - x) * k for x, y in zip(a[:3], b[:3])]


WHITE = [1.0, 1.0, 1.0]


def _group_items(o, found: list) -> list:
    """Все списки элементов (it группы или shapes слоя) — в них лежат заливки вместе с фигурами."""
    if isinstance(o, dict):
        items = o.get("it") if o.get("ty") == "gr" else o.get("shapes") if o.get("ty") == 4 else None
        if isinstance(items, list):
            found.append(items)
        for v in o.values():
            if isinstance(v, (dict, list)):
                _group_items(v, found)
    elif isinstance(o, list):
        for v in o:
            _group_items(v, found)
    return found


def _gradient(paint: dict, stops: list[tuple[float, list[float]]], s, e, name: str) -> dict:
    """Сплошная заливка (fl) → градиентная (gf), обводка (st) → градиентная обводка (gs) с той же толщиной."""
    flat: list[float] = []
    for pos, rgb in sorted(stops, key=lambda x: x[0]):
        flat += [round(max(0.0, min(1.0, pos)), 3), *[round(max(0.0, min(1.0, c)), 3) for c in rgb[:3]]]
    out = {"ty": "gf", "nm": name, "o": paint.get("o", {"a": 0, "k": 100}), "bm": 0, "t": 1,
           "s": s, "e": e, "g": {"p": len(stops), "k": {"a": 0, "k": flat}}}
    if paint.get("ty") == "st":
        out["ty"] = "gs"
        out.update({k: paint[k] for k in ("w", "lc", "lj", "ml", "d") if k in paint})
        out.setdefault("ml", 4)
    else:
        out["r"] = paint.get("r", 1)
    return out


def _gradient_fills(anim: dict, targets, make, kinds: tuple[str, ...] = ("fl",)) -> None:
    """Сплошные заливки (и/или обводки — kinds) → градиентные. targets(id части) решает, менять ли слой;
    make(заливка или обводка, габариты) → gf/gs."""
    for scope, L, _lm, _pm in list(_walk_layers(anim)):
        if L.get("ty") != 4 or not targets(f"{scope}.{L.get('ind')}"):
            continue
        for items in _group_items(L, []):
            if any(isinstance(i, dict) and i.get("ty") == "fl" and i.get("nm") == SLOT for i in items):
                continue  # надпись не трогаем — она должна читаться
            box = _bbox(_points([i for i in items if isinstance(i, dict) and i.get("ty") != "tr"], IDENT, [], rest_time(anim)))
            if box is None:
                continue
            for n, it in enumerate(items):
                if (isinstance(it, dict) and (it.get("ty") in kinds or ("st" in kinds and it.get("nm") == "ezz pixel stroke"))
                        and it.get("nm") != SLOT and lottie._solid(it)):
                    items[n] = make(it, box)


def _vivid_stops(pal: list, w: float, bright: float, gloss: bool) -> list[tuple[float, list[float]]]:
    """Яркая голограмма для контура: тёмный — цвет — светлый — белый блик — светлый — цвет — тёмный, дважды
    по длине градиента (на вращающемся контуре блики бегут с двух сторон)."""
    stops: list[tuple[float, list[float]]] = []
    for half in (0.0, 0.5):
        if len(pal) > 1:  # радуга: оттенки подряд, блик посередине каждой половины
            stops += [(half + 0.5 * n / len(pal), c) for n, c in enumerate(pal)]
            if gloss:
                stops += [(half + 0.25, _mix(_mix(pal[len(pal) // 2], WHITE, 0.5), WHITE, bright))]
            continue
        c = pal[0]
        # Хром: полноценные чёрные и белые отражения с цветным акцентом, а не просто цветная заливка.
        deep, light = [0.015, 0.015, 0.02], _mix(c, WHITE, 0.68)
        stops += [(half, deep), (half + 0.07, _mix(c, deep, 0.45)), (half + 0.13, c),
                  (half + 0.25 - w, light), (half + 0.25 + w, light), (half + 0.39, c), (half + 0.46, deep)]
        stops.append((half + 0.25, _mix(light, WHITE, bright) if gloss else light))
    stops.append((1.0, stops[0][1]))
    return sorted(stops, key=lambda s: s[0])


def _darken_base(anim: dict, targets, main_stroke=None) -> None:
    """«Чёрная основа» под голограммой контура: заливки чернеют, кроме цвета контуров (белые буквы и кольца
    остаются) и надписи. main_stroke — цвет контуров, если он уже не виден по анимации (после мозаики)."""
    if main_stroke is None:
        _main_fill, main_stroke = lottie.main_colors(anim)
    black = [0.04, 0.04, 0.05]
    for scope, L, _lm, _pm in list(_walk_layers(anim)):
        if L.get("ty") != 4 or not targets(f"{scope}.{L.get('ind')}"):
            continue
        for item in _solid_paints(L, ("fl",)):
            rgb = lottie._solid(item)
            if main_stroke and lottie._near(rgb, main_stroke):
                continue
            k = item["c"]["k"]
            item["c"] = {"a": 0, "k": [*black, k[3] if len(k) > 3 else 1]}


def _paint_free(o) -> None:
    """Убирает все заливки и обводки (контуры и трансформации остаются)."""
    if isinstance(o, dict):
        for v in o.values():
            _paint_free(v)
    elif isinstance(o, list):
        o[:] = [v for v in o if not (isinstance(v, dict) and v.get("ty") in ("fl", "st", "gf", "gs"))]
        for v in o:
            _paint_free(v)


def _holo_edge(anim: dict, cfg: dict, stops: list, angles: list) -> None:
    """Хромовая кромка по границе стикера: под картинкой лежит её копия без заливок, обведённая толстой
    переливающейся обводкой. Внутренняя половина обводки скрыта самой картинкой, снаружи остаётся ровная кромка
    шириной cfg["edge"] — она повторяет силуэт эмодзи, какой бы формы он ни был. Градиент общий для всего
    холста, поэтому блик бежит по кромке одним потоком, а не отдельно по каждой детали."""
    W, H = float(anim.get("w") or 512), float(anim.get("h") or 512)
    ip, op = anim.get("ip", 0), anim.get("op", 180)
    t = rest_time(anim)
    anim.setdefault("assets", [])
    layers = _copy_comp(anim, anim["layers"], "_edge")
    R = math.hypot(W, H) / 2
    for _scope, L, lm, _pm in list(_walk_layers({"layers": layers, "assets": anim["assets"]}, t)):
        if L.get("ty") != 4:
            continue
        shapes = L.get("shapes") or []
        _paint_free(shapes)
        if not _points(shapes, IDENT, [], t):
            continue
        inv = _inverse(lm)
        if inv is None:  # деталь в этот момент скрыта масштабом 0 — кромка в её собственных координатах
            box = _bbox(_points(shapes, IDENT, [], t)) or (0, 0, W, H)
            cx, cy, r, k = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, max(1.0, math.hypot(box[2] - box[0], box[3] - box[1]) / 2), 1.0
            to_local = lambda x, y: (x, y)  # noqa: E731
        else:
            cx, cy, r, k = W / 2, H / 2, R, _scale(lm)
            to_local = lambda x, y, inv=inv: _apply(inv, x, y)  # noqa: E731
        s = _keys([(tt, list(to_local(cx - r * math.cos(a), cy - r * math.sin(a)))) for tt, a in angles], LINEAR)
        e = _keys([(tt, list(to_local(cx + r * math.cos(a), cy + r * math.sin(a)))) for tt, a in angles], LINEAR)
        paint = {"ty": "st", "o": {"a": 0, "k": 100}, "w": {"a": 0, "k": round(2 * cfg["edge"] / k, 2)}, "lc": 2, "lj": 2}
        gs = _gradient(paint, stops, s, e, "ezz holo edge")
        shapes.append(gs)
    anim["assets"].append({"id": "ezz_holo_edge", "layers": layers})
    ind = max((l.get("ind", 0) for l in anim["layers"] if isinstance(l.get("ind"), int)), default=0) + 1
    anim["layers"].append({"ddd": 0, "ind": ind, "ty": 0, "nm": "ezz holo edge", "refId": "ezz_holo_edge", "sr": 1, "ao": 0,
                           "w": W, "h": H, "ip": ip, "op": op, "st": 0, "bm": 0,
                           "ks": {"o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0}, "p": {"a": 0, "k": [0, 0, 0]},
                                  "a": {"a": 0, "k": [0, 0, 0]}, "s": {"a": 0, "k": [100, 100, 100]}}})


def _holo(anim: dict, cfg: dict, per_turn: int = 16, edge: bool = True, main_stroke=None) -> None:
    pal = [lottie.hex_to_rgb(cfg["tint"])] if cfg.get("tint") else [lottie.hex_to_rgb(c) for c in PALETTES[cfg["palette"]]]
    ip, op = anim.get("ip", 0), anim.get("op", 180)
    turns, sign = cfg["turns"], (1 if cfg["dir"] == "cw" else -1)
    w = max(0.01, min(0.3, 0.06 * cfg["width"] / 100))
    only = set(cfg["parts"]) if cfg["only"] else None
    targets = (lambda pid: pid in only) if only is not None else (lambda pid: True)
    vivid = _vivid_stops(pal, max(0.01, min(0.1, 0.04 * cfg["width"] / 100)), cfg["bright"] / 100, cfg["gloss"])

    n = per_turn * turns
    ang = [(ip + (op - ip) * j / n, sign * 2 * math.pi * turns * j / n - math.pi / 4) for j in range(n + 1)]

    def rotating(paint, box, stops):
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        R = max(1.0, math.hypot(box[2] - box[0], box[3] - box[1]) / 2)
        s = _keys([(t, [cx - R * math.cos(a), cy - R * math.sin(a)]) for t, a in ang], LINEAR)
        e = _keys([(t, [cx + R * math.cos(a), cy + R * math.sin(a)]) for t, a in ang], LINEAR)
        return _gradient(paint, stops, s, e, "ezz holo")

    if cfg["target"] in ("contour", "all"):
        if cfg["dark"]:
            _darken_base(anim, targets, main_stroke)
        _gradient_fills(anim, targets, lambda st, box: rotating(st, box, vivid), ("st",))
        if edge and cfg["edge"] and only is None:
            _holo_edge(anim, cfg, vivid, ang)
    if cfg["target"] == "contour":
        return

    def make(fill, box):
        base = lottie._solid(fill)
        if len(pal) > 1:
            stops = [(n / len(pal), _mix(base, c, 0.6)) for n, c in enumerate(pal)] + [(1.0, _mix(base, pal[0], 0.6))]
        else:
            c = pal[0]
            stops = [(0, _mix(base, c, 0.35)), (0.25, _mix(base, c, 0.7)), (0.5, _mix(_mix(base, c, 0.7), WHITE, 0.3)),
                     (0.75, _mix(base, c, 0.7)), (1, _mix(base, c, 0.35))]
        if cfg["gloss"]:
            def at(pos):
                for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
                    if p0 <= pos <= p1:
                        return _mix(c0, c1, (pos - p0) / (p1 - p0) if p1 > p0 else 0)
                return stops[-1][1]
            hl = _mix(at(0.5), WHITE, cfg["bright"] / 100)
            stops = [s for s in stops if not 0.5 - w < s[0] < 0.5 + w] + [(0.5 - w, at(0.5 - w)), (0.5, hl), (0.5 + w, at(0.5 + w))]
        return rotating(fill, box, stops)

    _gradient_fills(anim, targets, make)


def _metal(anim: dict, cfg: dict) -> None:
    ip, op = anim.get("ip", 0), anim.get("op", 180)
    w = max(0.02, min(0.4, 0.1 * cfg["width"] / 100))
    d = (math.cos(math.pi / 4), math.sin(math.pi / 4))

    def make(fill, box):
        base = lottie._solid(fill)
        dark = _mix(base, [0, 0, 0], 0.18)
        stops = [(0, base), (0.5 - w, dark), (0.5, _mix(base, WHITE, 0.82)), (0.5 + w, dark), (1, base)]
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        R = max(1.0, math.hypot(box[2] - box[0], box[3] - box[1]) / 2)
        a, b = -1.7 * R, 1.7 * R
        mid = ip + (op - ip) * 0.55
        # блик проходит за 55% цикла и ждёт за краем; к концу цикла незаметно возвращается к началу
        path = [(ip, a), (mid, b), (op, a)]
        s = _keys([(t, [cx + d[0] * (v - R), cy + d[1] * (v - R)]) for t, v in path])
        e = _keys([(t, [cx + d[0] * (v + R), cy + d[1] * (v + R)]) for t, v in path])
        for prop in (s, e):
            prop["k"][1]["h"] = 1
            prop["k"][1].pop("i", None)
            prop["k"][1].pop("o", None)
        return _gradient(fill, stops, s, e, "ezz metal")

    _gradient_fills(anim, lambda pid: True, make)


# ── пикселизация

def _bez(p0, p1, p2, p3, t):
    mt = 1 - t
    return (mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t ** 3 * p3[0],
            mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t ** 3 * p3[1])


def _primitive_path(it: dict) -> dict | None:
    """Эллипс или прямоугольник без анимации → обычный контур (чтобы его можно было пикселизовать)."""
    if any(_is_anim(it.get(k)) for k in ("p", "s", "r")):
        return None
    (cx, cy), (w, h) = _vec(it.get("p"), [0, 0]), _vec(it.get("s"), [0, 0])
    if it["ty"] == "el":
        k = 0.5523
        return {"c": True, "v": [[cx, cy - h / 2], [cx + w / 2, cy], [cx, cy + h / 2], [cx - w / 2, cy]],
                "o": [[w / 2 * k, 0], [0, h / 2 * k], [-w / 2 * k, 0], [0, -h / 2 * k]],
                "i": [[-w / 2 * k, 0], [0, -h / 2 * k], [w / 2 * k, 0], [0, h / 2 * k]]}
    z = [[0, 0]] * 4
    return {"c": True, "v": [[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]],
            "i": z, "o": z}


PIXEL_GRID_MIN = 8


def _pixel(anim: dict, cfg: dict) -> None:
    """Пиксельная мозаика — см. app/pixel.py."""
    from .pixel import mosaic

    mosaic(anim, cfg)


# ── слои-помощники

def _layer(ind: int, nm: str, shapes: list, ip: float, op: float, ks: dict | None = None) -> dict:
    base = {"o": {"a": 0, "k": 100}, "r": {"a": 0, "k": 0}, "p": {"a": 0, "k": [0, 0, 0]},
            "a": {"a": 0, "k": [0, 0, 0]}, "s": {"a": 0, "k": [100, 100, 100]}}
    base.update(ks or {})
    return {"ddd": 0, "ind": ind, "ty": 4, "nm": nm, "sr": 1, "ks": base, "ao": 0, "shapes": shapes,
            "ip": ip, "op": op, "st": 0, "bm": 0}


def _fill(rgb, opacity: float = 100) -> dict:
    return {"ty": "fl", "c": {"a": 0, "k": [*[round(c, 3) for c in rgb[:3]], 1]}, "o": {"a": 0, "k": opacity}, "r": 1, "bm": 0}


def _tr() -> dict:
    return {"ty": "tr", "p": {"a": 0, "k": [0, 0]}, "a": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [100, 100]},
            "r": {"a": 0, "k": 0}, "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0}, "sa": {"a": 0, "k": 0}}


def _rect(x: float, y: float, w: float, h: float, rgb) -> list:
    return [{"ty": "gr", "it": [{"ty": "rc", "d": 1, "s": {"a": 0, "k": [round(w, 1), round(h, 1)]},
                                 "p": {"a": 0, "k": [round(x, 1), round(y, 1)]}, "r": {"a": 0, "k": 0}},
                                _fill(rgb), _tr()]}]


def _poly(points, rgb, curved: list | None = None) -> list:
    z = [[0, 0] for _ in points]
    ins, outs = (curved or (z, z))
    return [{"ty": "gr", "it": [{"ty": "sh", "ks": {"a": 0, "k": {"c": True, "v": [[round(x, 2), round(y, 2)] for x, y in points],
                                                               "i": ins, "o": outs}}},
                                _fill(rgb), _tr()]}]


def _star(n: int, r_out: float, r_in: float):
    return [((r_out if j % 2 == 0 else r_in) * math.sin(math.pi * j / n), -(r_out if j % 2 == 0 else r_in) * math.cos(math.pi * j / n))
            for j in range(2 * n)]


def _heart(size: float):
    # выемка сверху, правая «щека», кончик снизу, левая «щека»
    s = size / 2
    verts = [[0, -0.35 * s], [s, -0.45 * s], [0, 0.95 * s], [-s, -0.45 * s]]
    ins = [[-0.5 * s, -0.6 * s], [0, -0.55 * s], [0.55 * s, -0.25 * s], [-0.2 * s, 0.5 * s]]
    outs = [[0.5 * s, -0.6 * s], [0.2 * s, 0.5 * s], [-0.55 * s, -0.25 * s], [0, -0.55 * s]]
    r2 = lambda q: [round(q[0], 2), round(q[1], 2)]  # noqa: E731
    return verts, [r2(q) for q in ins], [r2(q) for q in outs]


BURST_COLORS = {"sparks": "#ffd23f", "hearts": "#ff3d6e", "stars": "#ffd23f", "flash": "#ffffff"}
CONFETTI = ("#ff4d6d", "#ffd23f", "#4dc3ff", "#4dffa6", "#b06bff")


def _loop(frames: list[tuple[float, list]], op: float, easing=EASE) -> dict:
    """Ключи, которые к концу цикла возвращаются к первому значению — скачком, когда частица уже не видна
    (иначе проверка петли видит рывок)."""
    prop = _keys(frames + [(op, frames[0][1])], easing)
    if prop["a"] and len(prop["k"]) >= 2:
        kf = prop["k"][-2]
        kf["h"] = 1
        kf.pop("i", None)
        kf.pop("o", None)
    return prop


def _burst(anim: dict, cfg: dict) -> None:
    rng = random.Random(f"{cfg['kind']}-{cfg['count']}")
    W, H = float(anim.get("w") or 512), float(anim.get("h") or 512)
    cx, cy = W / 2, H / 2
    ip, op = anim.get("ip", 0), anim.get("op", 180)
    life = min(46.0, (op - ip) * 0.4)
    layers = anim["layers"]
    ind = max((l.get("ind", 0) for l in layers if isinstance(l.get("ind"), int)), default=0) + 1
    new = []
    color = lottie.hex_to_rgb(cfg["color"]) if cfg["color"] else None
    kind, n = cfg["kind"], cfg["count"]
    if kind == "flash":
        rgb = color or WHITE
        ring = [{"ty": "gr", "it": [{"ty": "el", "d": 1, "s": {"a": 0, "k": [W * 0.5, H * 0.5]}, "p": {"a": 0, "k": [0, 0]}},
                                    {"ty": "st", "c": {"a": 0, "k": [*rgb, 1]}, "o": {"a": 0, "k": 100},
                                     "w": _loop([(ip, [26]), (ip + life * 0.7, [2])], op), "lc": 2, "lj": 2, "bm": 0},
                                    _tr()]}]
        new.append(_layer(ind, "ezz flash", ring, ip, op, {
            "p": {"a": 0, "k": [cx, cy, 0]},
            "s": _loop([(ip, [30, 30, 100]), (ip + life * 0.7, [190, 190, 100])], op, {"o": {"x": [0.1], "y": [0.6]}, "i": {"x": [0.3], "y": [1]}}),
            "o": _loop([(ip, [100]), (ip + life * 0.5, [80]), (ip + life * 0.7, [0])], op)}))
        ind += 1
        kind, n = "sparks", max(4, n // 2)
    for j in range(n):
        ang = 2 * math.pi * j / n + rng.uniform(-0.25, 0.25)
        r0, r1 = rng.uniform(0.18, 0.28) * W, rng.uniform(0.38, 0.47) * W
        size = rng.uniform(30, 46) * W / 512  # эмодзи в чате ~20–100 px: частицы мельче не разглядеть
        rgb = color or lottie.hex_to_rgb(BURST_COLORS.get(kind) or CONFETTI[j % len(CONFETTI)])
        if kind == "hearts":
            v, i, o = _heart(size)
            shapes = _poly(v, rgb, [i, o])
        elif kind == "confetti":
            shapes = _rect(0, 0, size, size * 0.5, rgb)
        elif kind == "stars":
            shapes = _poly(_star(5, size / 2, size / 4.6), rgb)
        else:
            shapes = _poly(_star(4, size / 2, size / 7), rgb)
        delay = rng.uniform(0, 6)
        t0, t1 = ip + delay, ip + delay + life
        out = {"o": {"x": [0.15], "y": [0.7]}, "i": {"x": [0.35], "y": [1]}}
        rot0 = rng.uniform(-30, 30)
        spin = rng.uniform(90, 240) * (1 if j % 2 else -1) if kind in ("confetti", "stars") else rng.uniform(-25, 25)
        new.append(_layer(ind, f"ezz {kind}", shapes, ip, op, {
            "p": _loop([(ip, [cx + math.cos(ang) * r0, cy + math.sin(ang) * r0, 0]), (t0, [cx + math.cos(ang) * r0, cy + math.sin(ang) * r0, 0]),
                        (t1, [cx + math.cos(ang) * r1, cy + math.sin(ang) * r1, 0])], op, out),
            "s": _loop([(ip, [0, 0, 100]), (t0, [0, 0, 100]), (t0 + 8, [100, 100, 100]), (t1, [55, 55, 100])], op),
            "o": _loop([(ip, [0]), (t0, [100]), (t0 + life * 0.55, [100]), (t1, [0])], op),
            "r": _loop([(ip, [rot0]), (t0, [rot0]), (t1, [rot0 + spin])], op)}))
        ind += 1
    anim["layers"] = new + layers


# ── глитч

def _recolor_all(o, rgb) -> None:
    if isinstance(o, dict):
        ty = o.get("ty")
        if ty in ("fl", "st") and "c" in o:
            o["c"] = {"a": 0, "k": [*rgb[:3], 1]}
        elif ty in ("gf", "gs"):
            keep = {k: o[k] for k in ("o", "w", "lc", "lj", "ml", "r") if k in o}
            o.clear()
            o.update({"ty": "fl" if ty == "gf" else "st", "c": {"a": 0, "k": [*rgb[:3], 1]}, "bm": 0, **keep})
        for v in o.values():
            if isinstance(v, (dict, list)):
                _recolor_all(v, rgb)
    elif isinstance(o, list):
        for v in o:
            _recolor_all(v, rgb)


def _silhouette(anim: dict, layers: list, suffix: str, rgb) -> list:
    """Копия картинки одним цветом (вложенные прекомпозиции копируются под новыми id)."""
    out = _copy_comp(anim, layers, suffix)
    _recolor_all(out, rgb)
    for a in anim["assets"]:
        if str(a.get("id", "")).endswith(suffix):
            _recolor_all(a["layers"], rgb)
    return out


def _copy_comp(anim: dict, layers: list, suffix: str) -> list:
    """Копия слоёв; вложенные прекомпозиции копируются в анимацию под id с суффиксом."""
    ids = {a["id"]: a for a in anim.get("assets") or [] if isinstance(a, dict) and "layers" in a}
    made: dict[str, str] = {}

    def copy_layers(ls):
        out = copy.deepcopy(ls)
        for L in out:
            if L.get("ty") == 0 and L.get("refId") in ids:
                L["refId"] = copy_asset(L["refId"])
        return out

    def copy_asset(aid: str) -> str:
        if aid not in made:
            new_id = f"{aid}{suffix}"
            made[aid] = new_id
            asset = {k: v for k, v in ids[aid].items() if k != "layers"}
            asset["id"] = new_id
            asset["layers"] = copy_layers(ids[aid]["layers"])
            anim["assets"].append(asset)
        return made[aid]

    return copy_layers(layers)


def _glitch(anim: dict, cfg: dict, ghosts: bool = True) -> None:
    rng = random.Random(cfg["seed"] * 7919 + cfg["count"])
    W, H = float(anim.get("w") or 512), float(anim.get("h") or 512)
    ip, op = anim.get("ip", 0), anim.get("op", 180)
    length = max(2, round(6 * cfg["len"] / 100))
    shift = 30 * cfg["shift"] / 100 * W / 512
    starts = list(range(int(ip), int(op - length), 3))
    rng.shuffle(starts)
    events: list[int] = []
    for t in starts:
        if len(events) >= cfg["count"]:
            break
        if all(abs(t - e) > length + 2 for e in events):
            events.append(t)
    events.sort()
    anim.setdefault("assets", [])
    main_id = "ezz_glitch_main"
    anim["assets"].append({"id": main_id, "layers": anim["layers"]})
    frames = [(ip, [0, 0, 0])]
    for t in events:
        a = rng.uniform(0.5, 1) * shift * rng.choice((-1, 1))
        frames += [(t, [a, 0, 0]), (t + length / 2, [-a * 0.6, rng.uniform(-0.15, 0.15) * shift, 0]), (t + length, [0, 0, 0])]
    frames.append((op, [0, 0, 0]))

    def precomp(ind, ref, p, ip_, op_, opacity=100):
        return {"ddd": 0, "ind": ind, "ty": 0, "nm": "ezz glitch", "refId": ref, "sr": 1, "ao": 0, "w": W, "h": H,
                "ip": ip_, "op": op_, "st": 0, "bm": 0,
                "ks": {"o": {"a": 0, "k": opacity}, "r": {"a": 0, "k": 0}, "p": p,
                       "a": {"a": 0, "k": [0, 0, 0]}, "s": {"a": 0, "k": [100, 100, 100]}}}

    top: list[dict] = []
    ind = 1
    c1, c2 = lottie.hex_to_rgb(cfg["c1"]), lottie.hex_to_rgb(cfg["c2"])
    palette = [c1, c2, WHITE]
    marks = [("stripes", cfg["stripes"]), ("dashes", cfg["dashes"])]
    for kind, total in marks:
        for j in range(total if events else 0):
            t = events[j % len(events)]
            if kind == "stripes":
                w, h = rng.uniform(0.25, 0.75) * W, rng.uniform(3, 12) * H / 512
                x, y = rng.uniform(0.1 * W + w / 2, 0.9 * W - w / 2), rng.uniform(0.12, 0.88) * H
            else:
                w, h = rng.uniform(8, 28) * W / 512, rng.uniform(3, 5) * H / 512
                x, y = rng.uniform(0.12, 0.88) * W, rng.uniform(0.12, 0.88) * H
            top.append(_layer(ind, f"ezz {kind}", _rect(x, y, w, h, rng.choice(palette)), t, t + length))
            ind += 1
    main = precomp(ind, main_id, _keys(frames, hold=True), ip, op)
    ind += 1
    under: list[dict] = []
    if cfg["ghost"] and ghosts and events:
        ids = [f"ezz_ghost_{n}" for n in (1, 2)]
        content = anim["assets"][-1]["layers"]  # только что добавленная прекомпозиция main_id
        for aid, rgb, suffix in zip(ids, (c1, c2), ("_g1", "_g2")):
            anim["assets"].append({"id": aid, "layers": _silhouette(anim, content, suffix, rgb)})
        g = shift * 0.6 + 4 * W / 512
        for t in events:
            for aid, sign in zip(ids, (1, -1)):
                under.append(precomp(ind, aid, {"a": 0, "k": [sign * g, 0, 0]}, t, t + length, 80))
                ind += 1
    anim["layers"] = top + [main] + under


# ───────────────────────── применение ─────────────────────────

def apply(anim: dict, edit: dict | None, *, mark: bool = False, ghosts: bool = True, lite: int = 0,
          grid: int | None = None) -> dict:
    """Применяет edit к анимации (на месте) и возвращает её. mark — подписать слои частей (id в SVG превью)."""
    edit = edit or {}
    add_shapes(anim, edit)
    index = _layer_index(anim)
    if mark:
        for pid, (_layers, L) in index.items():
            L["ln"] = "ezp-" + pid.replace(".", "-")
    mapping = {k: lottie.hex_to_rgb(v) for k, v in (edit.get("colors") or {}).items()}
    if mapping:
        for item in _solid_paints(anim):
            dst = mapping.get(to_hex(lottie._solid(item)))
            if dst:
                k = item["c"]["k"]
                item["c"] = {"a": 0, "k": [*dst, k[3] if len(k) > 3 else 1]}
    world = {f"{s}.{L.get('ind')}": pm for s, L, _lm, pm in _walk_layers(anim, rest_time(anim))}
    for pid, p in (edit.get("parts") or {}).items():
        if pid in index:
            layers, L = index[pid]
            _edit_part(anim, layers, L, p, 1 / _scale(world.get(pid, IDENT)))
    fx = edit.get("fx") or {}
    if "burst" in fx:  # до мозаики: частицы тоже становятся клетками
        _burst(anim, fx["burst"])
    main_stroke = lottie.main_colors(anim)[1] if "pixel" in fx and "holo" in fx else None
    if "pixel" in fx:
        cfg = fx["pixel"]
        if lite:  # реже кадры и меньше цветов — меньше переключений клеток
            cfg = {**cfg, "fps": min(cfg["fps"], 8 if lite == 1 else 5), "colors": min(cfg["colors"], 24 if lite == 1 else 12)}
        if grid:  # сетка крупнее — меньше клеток (см. render: бюджет сложности Telegram)
            cfg = {**cfg, "grid": min(cfg["grid"], grid)}
        _pixel(anim, cfg)
    if "holo" in fx:
        _holo(anim, fx["holo"], 6 if lite else 16, edge="pixel" not in fx and lite < 2, main_stroke=main_stroke)
    if "metal" in fx:
        _metal(anim, fx["metal"])
    if "glitch" in fx:
        _glitch(anim, fx["glitch"], ghosts)
    return anim


def variants(edit: dict | None):
    """Упрощения, если файл не влезает в 64 КБ: как есть → глитч без цветных силуэтов → облегчённые
    пикселизация (реже кадры) и голограмма (меньше ключей)."""
    yield {}
    fx = (edit or {}).get("fx") or {}
    if "glitch" in fx and fx["glitch"]["ghost"]:
        yield {"ghosts": False}
    if "pixel" in fx or "holo" in fx:
        yield {"ghosts": False, "lite": 1}
        yield {"ghosts": False, "lite": 2}  # голограмма без кромки, мозаика ещё реже и беднее цветом


# ───────────────────────── проверка под Telegram ─────────────────────────

def _forbidden(anim: dict) -> list[str]:
    found: set[str] = set()

    def visit(o):
        if isinstance(o, dict):
            if o.get("ty") == 2 or "p" in o and "u" in o and isinstance(o.get("p"), str):
                found.add("картинки")
            if o.get("ty") == 5:
                found.add("текстовые слои")
            if o.get("ef"):
                found.add("эффекты")
            if o.get("masksProperties"):
                found.add("маски")
            if o.get("tt"):
                found.add("подложки")
            if o.get("ddd") == 1:
                found.add("3D")
            if isinstance(o.get("x"), str):
                found.add("выражения")
            for v in o.values():
                if isinstance(v, (dict, list)):
                    visit(v)
        elif isinstance(o, list):
            for v in o:
                visit(v)

    visit(anim)
    return sorted(found)


def _diff(a, b) -> float:
    if isinstance(a, dict) and isinstance(b, dict):
        return max((_diff(a.get(k), b.get(k)) for k in ("v", "i", "o")), default=0.0)
    if isinstance(a, list) and isinstance(b, list):
        return max((_diff(x, y) for x, y in zip(a, b)), default=0.0)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b)
    return 0.0


def _text_px(anim: dict) -> float | None:
    """Высота ника на холсте (px из 512) — по группам, которые подставил personalize."""
    best = None
    t = rest_time(anim)

    def visit(items, m):
        nonlocal best
        for it in items:
            if not isinstance(it, dict) or it.get("ty") != "gr":
                continue
            sub = it.get("it") or []
            tr = next((i for i in sub if isinstance(i, dict) and i.get("ty") == "tr"), None)
            mm = _mul(m, _tr_matrix(tr, t))
            if it.get("nm") in (lottie.NICK_LAYER, lottie.LOGO_LAYER):
                box = _bbox(_points(sub, mm, [], t))
                if box:
                    best = max(best or 0, box[3] - box[1])
            else:
                visit(sub, mm)

    for _s, L, lm, _pm in _walk_layers(anim, t):
        if L.get("ty") == 4:
            visit(L.get("shapes") or [], lm)
    return best


def _keys_state(anim: dict) -> tuple[bool, int]:
    """(ключевые кадры в порядке, сколько свойств в конце цикла не возвращаются к началу)."""
    ip, op = anim.get("ip", 0), anim.get("op", 0)
    ok, jerks = True, 0
    # цветные силуэты глитча и кромка голограммы — копии картинки: их свойства не считаем второй раз
    ghosts = [a for a in anim.get("assets") or [] if isinstance(a, dict) and str(a.get("id", "")).startswith(("ezz_ghost", "ezz_holo_edge"))
              or str(a.get("id", "")).endswith(("_g1", "_g2", "_edge"))]
    scope = [anim.get("layers") or [], *[a for a in anim.get("assets") or [] if a not in ghosts]]
    for node in _anim_props(scope):
        kfs = node["k"]
        ts = [k.get("t") for k in kfs]
        if not all(isinstance(t, (int, float)) for t in ts) or ts != sorted(ts) or _kval(kfs, 0) is None:
            ok = False
            continue
        a, b = value_at(kfs, ip), value_at(kfs, op)
        small = isinstance(a, list) and a and not isinstance(a[0], dict) and all(isinstance(x, (int, float)) and abs(x) <= 1 for x in a)
        if json.dumps(a) != json.dumps(b) and _diff(a, b) > (0.05 if small else 1.5):
            jerks += 1
    return ok, jerks


def check(anim: dict, data: bytes, base: dict | None = None) -> list[dict]:
    """Проверка файла как в референсе: вес, fps, длительность, холст, ключи, петля, ник в чате, запрещённое.
    base — анимация до правок: «рывок» засчитывается, только если его добавили правки (у части шаблонов слои
    заканчиваются не так, как начинаются, но к концу цикла они уже скрыты)."""
    fr, ip, op = anim.get("fr", 0), anim.get("ip", 0), anim.get("op", 0)
    w, h = anim.get("w"), anim.get("h")
    dur = (op - ip) / fr if fr else 0
    keys_ok, jerks = _keys_state(anim)
    jerk = jerks > (_keys_state(base)[1] if base is not None else 0)
    px = _text_px(anim)
    chat = px * 20 / (w or 512) if px else None
    bad = _forbidden(anim)
    over = _overload(anim)
    kb = len(data) / 1024
    return [
        {"id": "weight", "label": "вес после сжатия", "value": f"{kb:.1f} кб / {TG_LIMIT // 1024}".replace(".", ","), "ok": len(data) <= TG_LIMIT},
        {"id": "fps", "label": "fps", "value": f"{fr:g}", "ok": fr == 60},
        {"id": "duration", "label": "длительность", "value": f"{dur:.2f} с".replace(".", ","), "ok": 0 < dur <= 3.0 + 1e-6},
        {"id": "canvas", "label": "холст", "value": f"{w}×{h}", "ok": w == 512 and h == 512},
        {"id": "keys", "label": "ключи", "value": "чисто" if keys_ok else "ошибки", "ok": keys_ok},
        {"id": "loop", "label": "петля", "value": "есть рывок" if jerk else "без рывков", "ok": not jerk},
        {"id": "nick", "label": "ник в чате, 20 px", "ok": chat is None or chat >= 4,
         "value": "нет ника" if chat is None else ("мелко" if chat < 4 else f"{chat:.1f} px".replace(".", ","))},
        {"id": "tg", "label": "сложность для telegram", "value": "в норме" if over <= 1 else f"×{over:.1f} от лимита".replace(".", ","),
         "ok": over <= 1},
        {"id": "forbidden", "label": "запрещённое", "value": ", ".join(bad) if bad else "нет", "ok": not bad},
    ]


# Кроме 64 КБ после сжатия Telegram отклоняет («wrong file type») слишком сложные анимации. Порог не описан;
# подобран загрузкой пробных файлов (uploadStickerFile, 2026-10-17): проходили мозаики до ~23 тыс. объектов JSON,
# ~3,2 тыс. фигур и ~10,6 тыс. ключевых кадров, а 37 тыс. / 4,3 тыс. / 19,5 тыс. — уже нет. Держимся в пределах прошедших.
COMPLEXITY = {"objects": 23000, "shapes": 3200, "keyframes": 10500}


def complexity(anim) -> dict[str, int]:
    """Объекты JSON, фигуры и ключевые кадры анимации — то, по чему Telegram режет сложные стикеры."""
    m = {"objects": 0, "shapes": 0, "keyframes": 0}
    stack = [(anim, False)]
    while stack:
        o, in_shapes = stack.pop()
        if isinstance(o, dict):
            m["objects"] += 1
            if in_shapes and isinstance(o.get("ty"), str):
                m["shapes"] += 1
            if o.get("a") == 1 and isinstance(o.get("k"), list):
                m["keyframes"] += len(o["k"])
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    stack.append((v, in_shapes or k in ("shapes", "it")))
        elif isinstance(o, list):
            stack.extend((v, in_shapes) for v in o if isinstance(v, (dict, list)))
    return m


def _overload(anim: dict) -> float:
    """Во сколько раз анимация сложнее допустимого (≤ 1 — укладывается)."""
    c = complexity(anim)
    return max(c[k] / COMPLEXITY[k] for k in COMPLEXITY)


def fits(anim: dict, data: bytes) -> bool:
    return len(data) <= TG_LIMIT and _overload(anim) <= 1


def render(anim: dict, edit: dict | None, *, mark: bool = False) -> tuple[dict, bytes]:
    """Персонализированная анимация + правки → (анимация, байты .tgs). Подбирает упрощение под требования Telegram:
    64 КБ и бюджет сложности; мозаике, если не хватило облегчений, укрупняет сетку."""
    out, data, opts = anim, b"", {}
    for opts in variants(edit):
        out = apply(copy.deepcopy(anim), edit, mark=mark, **opts)
        data = lottie.encode(out)
        if fits(out, data):
            return out, data
    pixel = ((edit or {}).get("fx") or {}).get("pixel")
    if pixel:
        grid = pixel["grid"]
        for _ in range(4):
            over = max(_overload(out), len(data) / TG_LIMIT)
            # число клеток ~ квадрат сетки; берём с запасом и всегда хотя бы на шаг крупнее
            grid = max(PIXEL_GRID_MIN, min(grid - 4, int(grid / math.sqrt(over) * 0.92)))
            out = apply(copy.deepcopy(anim), edit, mark=mark, **{**opts, "grid": grid})
            data = lottie.encode(out)
            if fits(out, data) or grid == PIXEL_GRID_MIN:
                break
    return out, data


def gzip_json(payload) -> bytes:
    return gzip.compress(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(), compresslevel=6)
