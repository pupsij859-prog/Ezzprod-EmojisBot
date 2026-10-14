"""Логотип-картинка → векторный рисунок для TGS.

TGS не допускает растровых картинок, поэтому загруженный логотип трассируем: сводим к нескольким цветам,
для каждого цвета строим маску и обводим её границы многоугольниками, затем упрощаем (Рамер — Дуглас — Пейкер).
Слои укладываются «стопкой»: нижний — все непрозрачные пиксели основным цветом, каждый следующий —
поверх, поэтому между цветами не бывает щелей.

Результат — тот же формат, что у надписи (app/fonts.py): {"bbox": [...], "layers": [{"color": [r, g, b], "paths": [...]}]},
координаты в пикселях уменьшенной картинки, ось Y вниз.
"""
from __future__ import annotations

from PIL import Image

SIDE = 160            # трассируем на такой стороне: детали логотипа на эмодзи 100 px мельче не видны
MAX_COLORS = 5
MIN_AREA = 6          # контуры меньше стольких пикселей — шум, выбрасываем


def _quantize(img: Image.Image, colors: int) -> tuple[list[list[int]], list[tuple[int, int, int]], int, int]:
    """→ (метки пикселей: -1 — прозрачный, иначе индекс цвета по убыванию площади), палитра, ширина, высота."""
    img = img.convert("RGBA")
    img.thumbnail((SIDE, SIDE), Image.LANCZOS)
    w, h = img.size
    alpha = img.getchannel("A")
    rgb = Image.new("RGB", img.size, (255, 255, 255))
    rgb.paste(img, mask=alpha)
    q = rgb.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = q.getpalette()[: colors * 3]
    idx = list(q.getdata())
    a = list(alpha.getdata())
    counts: dict[int, int] = {}
    for i, v in enumerate(idx):
        if a[i] >= 128:
            counts[v] = counts.get(v, 0) + 1
    order = sorted(counts, key=lambda k: -counts[k])
    remap = {k: n for n, k in enumerate(order)}
    labels = [[-1] * w for _ in range(h)]
    for y in range(h):
        row = labels[y]
        for x in range(w):
            i = y * w + x
            if a[i] >= 128:
                row[x] = remap[idx[i]]
    palette = [tuple(pal[3 * k: 3 * k + 3]) for k in order]
    return labels, palette, w, h


def _loops(mask: list[list[bool]], w: int, h: int) -> list[list[tuple[int, int]]]:
    """Границы области по рёбрам пикселей: замкнутые ломаные в узлах сетки (внешние и дыры)."""
    nxt: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def inside(x: int, y: int) -> bool:
        return 0 <= x < w and 0 <= y < h and mask[y][x]

    def add(a, b):
        nxt.setdefault(a, []).append(b)

    for y in range(h):
        for x in range(w):
            if not mask[y][x]:
                continue
            # обходим так, чтобы область оставалась справа (ось Y вниз)
            if not inside(x, y - 1):
                add((x, y), (x + 1, y))
            if not inside(x + 1, y):
                add((x + 1, y), (x + 1, y + 1))
            if not inside(x, y + 1):
                add((x + 1, y + 1), (x, y + 1))
            if not inside(x - 1, y):
                add((x, y + 1), (x, y))
    loops = []
    while nxt:
        start = next(iter(nxt))
        loop = [start]
        cur = start
        prev_dir = None
        while True:
            outs = nxt[cur]
            if len(outs) == 1:
                b = outs.pop()
            else:  # «седло»: два пикселя касаются углом — поворачиваем направо, контуры не пересекаются
                b = _pick(prev_dir, cur, outs)
                outs.remove(b)
            if not outs:
                del nxt[cur]
            prev_dir = (b[0] - cur[0], b[1] - cur[1])
            cur = b
            if cur == start:
                break
            loop.append(cur)
        loops.append(loop)
    return loops


def _pick(prev_dir, cur, outs):
    if prev_dir is None:
        return outs[0]
    dx, dy = prev_dir
    right = (-dy, dx)
    for b in outs:
        if (b[0] - cur[0], b[1] - cur[1]) == right:
            return b
    return outs[0]


def _area(loop) -> float:
    s = 0.0
    for i, (x1, y1) in enumerate(loop):
        x2, y2 = loop[(i + 1) % len(loop)]
        s += x1 * y2 - x2 * y1
    return s / 2


def _rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = (dx * dx + dy * dy) ** 0.5 or 1e-9
    best, idx = -1.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm
        if d > best:
            best, idx = d, i
    if best <= eps:
        return [points[0], points[-1]]
    left = _rdp(points[: idx + 1], eps)
    return left[:-1] + _rdp(points[idx:], eps)


def _simplify(loop: list[tuple[int, int]], eps: float) -> list[tuple[float, float]]:
    # «лестницу» пикселей сглаживаем средними точками рёбер, затем упрощаем замкнутую ломаную
    mids = [((loop[i][0] + loop[(i + 1) % len(loop)][0]) / 2, (loop[i][1] + loop[(i + 1) % len(loop)][1]) / 2)
            for i in range(len(loop))]
    if len(mids) < 4:
        return mids
    far = max(range(len(mids)), key=lambda i: (mids[i][0] - mids[0][0]) ** 2 + (mids[i][1] - mids[0][1]) ** 2)
    a = _rdp(mids[: far + 1], eps)
    b = _rdp(mids[far:] + [mids[0]], eps)
    return a[:-1] + b[:-1]


def vectorize(img: Image.Image, colors: int = MAX_COLORS, eps: float = 0.7) -> dict | None:
    labels, palette, w, h = _quantize(img, colors)
    if not palette:
        return None
    layers = []
    for k in range(len(palette)):
        # слой k: пиксели цветов k, k+1, … — нижние слои шире верхних, поэтому щелей нет
        mask = [[v >= k for v in row] for row in labels]
        paths = []
        for loop in _loops(mask, w, h):
            if abs(_area(loop)) < MIN_AREA:
                continue
            pts = _simplify(loop, eps)
            if len(pts) < 3:
                continue
            paths.append({"v": [[round(x, 1), round(y, 1)] for x, y in pts],
                          "i": [[0, 0]] * len(pts), "o": [[0, 0]] * len(pts), "c": True})
        if paths:
            layers.append({"color": list(palette[k]), "paths": paths})
    if not layers:
        return None
    xs = [p[0] for layer in layers for path in layer["paths"] for p in path["v"]]
    ys = [p[1] for layer in layers for path in layer["paths"] for p in path["v"]]
    return {"bbox": [min(xs), min(ys), max(xs), max(ys)], "layers": layers}


def size_variants(img: Image.Image) -> list[dict]:
    """Несколько вариантов детализации — от подробного к грубому. Сборщик берёт первый, с которым
    файл укладывается в лимит Telegram (64 КБ)."""
    out = []
    for colors, eps in ((MAX_COLORS, 0.7), (4, 1.1), (3, 1.6), (2, 2.4)):
        v = vectorize(img, colors, eps)
        if v:
            out.append(v)
    return out
