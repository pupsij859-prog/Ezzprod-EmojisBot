"""Пиксельная мозаика: вся картинка — сетка квадратиков с зазорами, как пиксель-арт.

На каждом кадре (с шагом «кадров/с») сцена Lottie разбирается в клетки: в центре каждой клетки считается видимый
цвет с учётом порядка слоёв, прозрачности, градиентов, обводок и обрезки контуров (trim paths). Вместо исходных
слоёв остаётся один слой: по группе на цвет, в группе — прямоугольники-клетки. Если клетка то видна, то нет
(или меняет цвет), её размер переключается удерживаемыми ключами между «клетка» и 0. Так у каждой клетки ровно
один цвет, клетки не накладываются, а файл остаётся маленьким: прямоугольник в TGS — пара чисел.
"""
from __future__ import annotations

import math

from PIL import Image

from . import editor as e
from .lottie import SLOT

ALPHA_MIN = 0.5  # клетка видна, если в её центре картинка непрозрачна хотя бы наполовину


# ───────────────────────── контуры в координатах холста ─────────────────────────

def _flatten(shape: dict, m, step: float) -> tuple[list[tuple[float, float]], bool]:
    v = shape.get("v") or []
    n = len(v)
    if n < 2:
        return [], False
    ins, outs = shape.get("i") or [[0, 0]] * n, shape.get("o") or [[0, 0]] * n
    closed = shape.get("c", True)
    pts: list[tuple[float, float]] = []
    for j in range(n if closed else n - 1):
        k = (j + 1) % n
        a, b = v[j], v[k]
        oj = outs[j] if j < len(outs) else [0, 0]
        ik = ins[k] if k < len(ins) else [0, 0]
        p0, p3 = e._apply(m, a[0], a[1]), e._apply(m, b[0], b[1])
        if not (oj[0] or oj[1] or ik[0] or ik[1]):
            pts.append(p0)
            continue
        p1 = e._apply(m, a[0] + oj[0], a[1] + oj[1])
        p2 = e._apply(m, b[0] + ik[0], b[1] + ik[1])
        length = math.dist(p0, p1) + math.dist(p1, p2) + math.dist(p2, p3)
        steps = max(1, min(24, math.ceil(length / step)))
        pts.extend(e._bez(p0, p1, p2, p3, s / steps) for s in range(steps))
    if not closed:
        pts.append(e._apply(m, v[-1][0], v[-1][1]))
    return pts, closed


def _primitive(it: dict, t: float) -> dict | None:
    (cx, cy), (w, h) = e._vec(it.get("p"), [0, 0], t), e._vec(it.get("s"), [0, 0], t)
    shape = e._primitive_path({"ty": it["ty"], "p": {"k": [cx, cy]}, "s": {"k": [w, h]}})
    if shape and it.get("d") == 3:  # обратное направление — важно для правила заливки
        shape = {"c": True, "v": shape["v"][::-1], "i": [p for p in shape["o"][::-1]], "o": [p for p in shape["i"][::-1]]}
    return shape


def _path(it: dict, m, t: float, step: float):
    shape = e._shape_value(it, t) if it["ty"] == "sh" else _primitive(it, t)
    if not shape:
        return None
    pts, closed = _flatten(shape, m, step)
    return (pts, closed) if len(pts) >= 2 else None


# ── обрезка контуров (trim paths)

def _cut(pts: list, closed: bool, a: float, b: float) -> list:
    """Кусок ломаной между длинами a и b (a < b; у замкнутой b может заходить за полную длину)."""
    seq = pts + [pts[0]] if closed else pts
    lens = [0.0]
    for p, q in zip(seq, seq[1:]):
        lens.append(lens[-1] + math.dist(p, q))
    total = lens[-1]
    if total <= 1e-9:
        return []

    def at(d: float):
        d = max(0.0, min(total, d))
        for j in range(1, len(lens)):
            if lens[j] >= d:
                seg = lens[j] - lens[j - 1]
                k = (d - lens[j - 1]) / seg if seg > 1e-12 else 0
                p, q = seq[j - 1], seq[j]
                return j, (p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k)
        return len(seq) - 1, seq[-1]

    def piece(x: float, y: float) -> list:
        j0, p0 = at(x)
        j1, p1 = at(y)
        return [p0, *seq[j0:j1], p1]

    if closed and b > total:
        return [piece(a, total) + piece(0, b - total)[1:]]
    return [piece(a, min(b, total))]


def _trim(paths: list, tm: dict, t: float) -> list:
    s = e._vec(tm.get("s"), [0], t)[0] / 100
    en = e._vec(tm.get("e"), [100], t)[0] / 100
    o = e._vec(tm.get("o"), [0], t)[0] / 360
    if s > en:
        s, en = en, s
    if en - s >= 1 - 1e-6:
        return paths
    if en - s <= 1e-6:
        return []
    out = []
    if tm.get("m") == 2:  # контуры как одна длинная линия
        lengths = []
        for pts, closed in paths:
            seq = pts + [pts[0]] if closed else pts
            lengths.append(sum(math.dist(p, q) for p, q in zip(seq, seq[1:])))
        total = sum(lengths)
        start = ((s + o) % 1) * total
        ranges = [(start, start + (en - s) * total)]
        if ranges[0][1] > total:
            ranges = [(start, total), (0, ranges[0][1] - total)]
        acc = 0.0
        for (pts, closed), ln in zip(paths, lengths):
            for a, b in ranges:
                lo, hi = max(a, acc), min(b, acc + ln)
                if hi - lo > 1e-6:
                    out += [(p, False) for p in _cut(pts, closed, lo - acc, hi - acc) if len(p) >= 2]
            acc += ln
        return out
    for pts, closed in paths:  # каждый контур отдельно
        seq = pts + [pts[0]] if closed else pts
        total = sum(math.dist(p, q) for p, q in zip(seq, seq[1:]))
        a = ((s + o) % 1) * total
        b = a + (en - s) * total
        if b > total and not closed:
            pieces = _cut(pts, False, a, total) + _cut(pts, False, 0, b - total)
        else:
            pieces = _cut(pts, closed, a, b)
        out += [(p, False) for p in pieces if len(p) >= 2]
    return out


# ───────────────────────── клетки под заливкой и обводкой ─────────────────────────

class Grid:
    def __init__(self, grid: int, size: float):
        self.n = grid
        self.cell = size / grid

    def fill(self, paths: list, evenodd: bool) -> list[tuple[int, int]]:
        n, cell = self.n, self.cell
        rows: dict[int, list] = {}
        for pts, _closed in paths:  # Lottie заливает и открытые контуры, замыкая их
            if len(pts) < 3:
                continue
            for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
                if y0 == y1:
                    continue
                lo, hi = (y0, y1) if y0 < y1 else (y1, y0)
                r0, r1 = max(0, math.ceil(lo / cell - 0.5)), min(n, math.ceil(hi / cell - 0.5))
                d = 1 if y1 > y0 else -1
                k = (x1 - x0) / (y1 - y0)
                for r in range(r0, r1):
                    rows.setdefault(r, []).append((x0 + ((r + 0.5) * cell - y0) * k, d))
        out = []
        for r, xs in rows.items():
            xs.sort()
            wind = 0
            for (xa, da), (xb, _db) in zip(xs, xs[1:]):
                wind += da
                if (wind % 2) if evenodd else wind:
                    c0, c1 = max(0, math.ceil(xa / cell - 0.5)), min(n, math.ceil(xb / cell - 0.5))
                    out.extend((c, r) for c in range(c0, c1))
        return out

    def stroke(self, paths: list, half: float) -> set[tuple[int, int]]:
        n, cell = self.n, self.cell
        out: set[tuple[int, int]] = set()
        h2 = half * half
        for pts, closed in paths:
            seq = pts + [pts[0]] if closed else pts
            if len(seq) == 1:
                seq = seq * 2
            for (x0, y0), (x1, y1) in zip(seq, seq[1:]):
                dx, dy = x1 - x0, y1 - y0
                l2 = dx * dx + dy * dy
                c0 = max(0, math.ceil((min(x0, x1) - half) / cell - 0.5))
                c1 = min(n - 1, math.floor((max(x0, x1) + half) / cell - 0.5))
                r0 = max(0, math.ceil((min(y0, y1) - half) / cell - 0.5))
                r1 = min(n - 1, math.floor((max(y0, y1) + half) / cell - 0.5))
                for r in range(r0, r1 + 1):
                    cy = (r + 0.5) * cell
                    for c in range(c0, c1 + 1):
                        if (c, r) in out:
                            continue
                        cx = (c + 0.5) * cell
                        k = 0.0 if l2 < 1e-12 else max(0.0, min(1.0, ((cx - x0) * dx + (cy - y0) * dy) / l2))
                        px, py = x0 + dx * k - cx, y0 + dy * k - cy
                        if px * px + py * py <= h2:
                            out.add((c, r))
        return out


def _gradient_at(paint: dict, t: float):
    """Функция: точка в координатах заливки → цвет градиента."""
    g = paint.get("g") or {}
    count = int(g.get("p") or 0)
    flat = e._vec(g.get("k"), [0.0] * (count * 4), t) if count else []
    stops = sorted((flat[j * 4], flat[j * 4 + 1:j * 4 + 4]) for j in range(count) if len(flat) >= j * 4 + 4)
    if not stops:
        return lambda x, y: [1.0, 1.0, 1.0]
    sx, sy = e._vec(paint.get("s"), [0, 0], t)
    ex, ey = e._vec(paint.get("e"), [0, 0], t)
    radial = paint.get("t") == 2
    dx, dy = ex - sx, ey - sy
    l2 = dx * dx + dy * dy or 1e-9
    radius = math.sqrt(l2)

    def color(x: float, y: float):
        u = math.hypot(x - sx, y - sy) / radius if radial else ((x - sx) * dx + (y - sy) * dy) / l2
        u = max(0.0, min(1.0, u))
        if u <= stops[0][0]:
            return stops[0][1]
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if u <= p1:
                k = (u - p0) / (p1 - p0) if p1 > p0 else 0
                return [a + (b - a) * k for a, b in zip(c0, c1)]
        return stops[-1][1]

    return color


# ───────────────────────── сцена в момент t ─────────────────────────

class Scene:
    def __init__(self, anim: dict, cfg: dict):
        self.anim = anim
        self.assets = {a.get("id"): a for a in anim.get("assets") or [] if isinstance(a, dict) and "layers" in a}
        size = float(anim.get("w") or 512)
        self.grid = Grid(cfg["grid"], size)
        self.step = self.grid.cell / 2  # точность кривых: полклетки
        # Линия тоньше клетки попадает в центры клеток через раз и рвётся. «Тонкие линии» 100% — линия не тоньше
        # клетки и всегда сплошная, 0% — как в оригинале.
        self.min_half = self.grid.cell * 0.6 * cfg["lines"] / 100

    def frame(self, t: float) -> dict:
        """Клетка → [r, g, b, a, откуда цвет: fill / stroke / text]."""
        self.canvas: dict[tuple[int, int], list] = {}
        self._comp(self.anim.get("layers") or [], e.IDENT, 1.0, t, 0)
        return self.canvas

    def _comp(self, layers: list, base, alpha: float, t: float, depth: int) -> None:
        by_ind = {L.get("ind"): L for L in layers if isinstance(L, dict)}
        cache: dict[int, tuple] = {}

        def matrix(L, guard=0):
            key = id(L)
            if key not in cache:
                par = by_ind.get(L.get("parent")) if "parent" in L else None
                pm = matrix(par, guard + 1) if par is not None and par is not L and guard < 32 else base
                cache[key] = e._mul(pm, e._tr_matrix(L.get("ks") or {}, t - (L.get("st") or 0)))
            return cache[key]

        for L in reversed(layers):  # снизу вверх
            if not isinstance(L, dict) or L.get("hd") or L.get("td") or L.get("ty") not in (0, 1, 4):
                continue
            if not (L.get("ip", -1e9) <= t < L.get("op", 1e9)):
                continue
            lt = t - (L.get("st") or 0)
            a = alpha * e._vec((L.get("ks") or {}).get("o"), [100], lt)[0] / 100
            if a <= 0.004:
                continue
            m = matrix(L)
            if L["ty"] == 4:
                ops, _paths = self._items(L.get("shapes") or [], m, a, lt)
                for op in ops:
                    self._draw(*op)
            elif L["ty"] == 0:
                asset = self.assets.get(L.get("refId"))
                if asset is not None and depth < 8:
                    self._comp(asset["layers"], m, a, lt / (L.get("sr") or 1), depth + 1)
            else:
                w, h = float(L.get("sw") or 0), float(L.get("sh") or 0)
                rect = {"c": True, "v": [[0, 0], [w, 0], [w, h], [0, h]], "i": [[0, 0]] * 4, "o": [[0, 0]] * 4}
                pts, _c = _flatten(rect, m, self.step)
                hexc = str(L.get("sc") or "#000000")
                rgb = [int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)] if len(hexc) == 7 else [0, 0, 0]
                fill = {"ty": "fl", "c": {"k": [*rgb, 1]}, "o": {"k": 100}}
                self._draw(fill, [(pts, True)], m, a, lt)

    def _items(self, items: list, m, alpha: float, t: float):
        """→ (рисование снизу вверх, контуры для заливок уровнем выше)."""
        by_index: dict[int, list] = {}
        paths: list = []
        for n, it in enumerate(items):
            if not isinstance(it, dict) or it.get("hd"):
                continue
            ty = it.get("ty")
            if ty in ("sh", "el", "rc"):
                p = _path(it, m, t, self.step)
                if p:
                    paths.append(p)
            elif ty == "gr":
                sub = it.get("it") or []
                tr = next((i for i in sub if isinstance(i, dict) and i.get("ty") == "tr"), None) or {}
                ga = alpha * e._vec(tr.get("o"), [100], t)[0] / 100
                ops, sub_paths = self._items(sub, e._mul(m, e._tr_matrix(tr, t)), ga, t)
                by_index[n] = ops
                paths.extend(sub_paths)
            elif ty == "tm":
                paths = _trim(paths, it, t)
            elif ty in ("fl", "st", "gf", "gs"):
                by_index[n] = [(it, list(paths), m, alpha, t)]
        ops = []
        for n in sorted(by_index, reverse=True):  # верхний элемент списка рисуется последним
            ops.extend(by_index[n])
        return ops, paths

    def _draw(self, paint: dict, paths: list, m, alpha: float, t: float) -> None:
        if not paths:
            return
        a = alpha * e._vec(paint.get("o"), [100], t)[0] / 100
        if a <= 0.004:
            return
        stroke = paint["ty"] in ("st", "gs")
        if stroke:
            width = e._vec(paint.get("w"), [0], t)[0] * e._scale(m)
            if width <= 0:
                return
            cells = self.grid.stroke(paths, max(width / 2, self.min_half))
        else:
            cells = self.grid.fill(paths, paint.get("r") == 2)
            box = e._bbox([p for pts, _c in paths for p in pts])
            small = box and min(box[2] - box[0], box[3] - box[1]) < self.grid.cell * 1.5
            if self.min_half and (small or paint.get("nm") == SLOT):
                cells = set(cells) | self.grid.stroke(paths, self.min_half * 0.8)
        if not cells:
            return
        if paint["ty"] in ("gf", "gs"):
            inv = e._inverse(m)
            if inv is None:
                return
            grad = _gradient_at(paint, t)
            cell = self.grid.cell
            color = lambda c, r: grad(*e._apply(inv, (c + 0.5) * cell, (r + 0.5) * cell))  # noqa: E731
        else:
            rgb = e._vec(paint.get("c"), [1, 1, 1], t)[:3]
            color = lambda c, r: rgb  # noqa: E731
        kind = "stroke" if stroke else "text" if paint.get("nm") == SLOT else "fill"
        canvas = self.canvas
        for key in cells:
            src = color(*key)
            dst = canvas.get(key)
            if dst is None or a >= 0.999:
                canvas[key] = [src[0], src[1], src[2], a, kind] if dst is None else [src[0], src[1], src[2], 1.0, kind]
                continue
            da = dst[3]
            out = a + da * (1 - a)
            k = a / out
            canvas[key] = [dst[0] + (src[0] - dst[0]) * k, dst[1] + (src[1] - dst[1]) * k, dst[2] + (src[2] - dst[2]) * k,
                           out, kind if a >= 0.5 else dst[4]]


# ───────────────────────── палитра ─────────────────────────

def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb[:3])


def _palette(counts: dict[str, int], n: int) -> dict[str, str]:
    """Не больше n цветов (самые частые сохраняются точно, редкие уходят к ближайшим)."""
    if len(counts) <= n:
        return {c: c for c in counts}
    keys = sorted(counts, key=lambda c: -counts[c])
    total = sum(counts.values())
    scale = min(1.0, 60000 / total)
    data = []
    for c in keys:
        rgb = tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
        data += [rgb] * max(1, round(counts[c] * scale))
    img = Image.new("RGB", (len(data), 1))
    img.putdata(data)
    q = img.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: 3 * n]
    centers = [tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)]
    out = {}
    for c in keys:
        rgb = tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
        best = min(centers, key=lambda p: (p[0] - rgb[0]) ** 2 + (p[1] - rgb[1]) ** 2 + (p[2] - rgb[2]) ** 2)
        out[c] = "#%02x%02x%02x" % best
    return out


# ───────────────────────── мозаика ─────────────────────────

def mosaic(anim: dict, cfg: dict) -> None:
    """Заменяет слои анимации мозаикой из клеток (на месте)."""
    fr = float(anim.get("fr") or 60)
    ip, op = float(anim.get("ip", 0)), float(anim.get("op", 180))
    step = max(1.0, fr / cfg["fps"])
    times = [round(ip + j * step, 3) for j in range(max(1, math.ceil((op - ip) / step)))]
    scene = Scene(anim, cfg)
    frames = []
    counts: dict[str, int] = {}
    for t in times:
        cells = {}
        for key, (r, g, b, a, kind) in scene.frame(t).items():
            if a >= ALPHA_MIN:
                h = _hex((r, g, b))
                cells[key] = (h, kind)
                counts[h] = counts.get(h, 0) + 1
        frames.append(cells)
    remap = _palette(counts, min(64, cfg["colors"]))

    cell = scene.grid.cell
    gap = cell * cfg.get("gap", 12) / 200
    side = round(cell - 2 * gap, 2)
    radius = {"square": 0, "soft": round(side * 0.28, 2), "circle": round(side / 2, 2)}[cfg["shape"]]
    tracks: dict[tuple[str, str], dict[tuple[int, int], list[bool]]] = {}
    for j, cells in enumerate(frames):
        for key, (h, kind) in cells.items():
            tracks.setdefault((remap[h], kind), {}).setdefault(key, [False] * len(frames))[j] = True

    groups = []
    for (h, kind), cellmap in sorted(tracks.items(), key=lambda kv: -len(kv[1])):
        items = []
        for (c, r), seen in sorted(cellmap.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            if all(seen):
                size = {"a": 0, "k": [side, side]}
            else:
                keys = []
                for t, on in zip(times, seen):
                    v = [side, side] if on else [0, 0]
                    if not keys or keys[-1]["s"] != v:
                        keys.append({"t": t, "s": v, "h": 1})
                if keys[-1]["s"] != keys[0]["s"]:  # петля: в конце цикла — как в начале
                    keys.append({"t": op, "s": keys[0]["s"]})
                else:
                    keys[-1].pop("h", None)
                size = {"a": 1, "k": keys} if len(keys) > 1 else {"a": 0, "k": keys[0]["s"]}
            items.append({"ty": "rc", "d": 1, "p": {"a": 0, "k": [round((c + 0.5) * cell, 2), round((r + 0.5) * cell, 2)]},
                          "s": size, "r": {"a": 0, "k": radius}})
        rgb = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        fill = e._fill(rgb)
        # голограмма узнаёт клетки контуров по имени; клетки ника — надпись, её эффекты не перекрашивают
        fill["nm"] = {"stroke": "ezz pixel stroke", "text": SLOT}.get(kind, "ezz pixel paint")
        groups.append({"ty": "gr", "nm": "ezz pixel", "it": [*items, fill, e._tr()]})
    anim["layers"] = [e._layer(1, "ezz pixel", groups, ip, op)]
    anim["assets"] = []
