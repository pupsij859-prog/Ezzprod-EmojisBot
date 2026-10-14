"""Контуры букв из TrueType-шрифта — надпись пользователя в TGS.

В TGS (Lottie для Telegram) нельзя текстовые слои и картинки, поэтому ник превращаем в векторные фигуры
прямо из шрифта. Здесь маленький разборщик TrueType (таблицы cmap, glyf, loca, hmtx), без сторонних
библиотек: квадратичные кривые шрифта переводим в кубические — так их понимает Lottie.

Результат — «векторный рисунок» (см. app/lottie.py): {"bbox": [x0, y0, x1, y1], "layers": [{"color": None, "paths": [...]}]},
координаты в единицах шрифта, ось Y вниз (как в Lottie). color None — цвет надписи шаблона.
"""
from __future__ import annotations

import os
import struct
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# первый найденный жирный шрифт с кириллицей; свой можно положить в assets/fonts/ или указать TEMPLATE_FONT
CANDIDATES = (
    ROOT / "assets" / "fonts" / "template.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    Path("/Library/Fonts/Arial Bold.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
)


class FontError(Exception):
    pass


class TrueTypeFont:
    def __init__(self, data: bytes) -> None:
        self.data = data
        if data[:4] not in (b"\x00\x01\x00\x00", b"true"):
            raise FontError("нужен TrueType-шрифт с таблицей glyf")
        n = struct.unpack_from(">H", data, 4)[0]
        self.tables: dict[str, tuple[int, int]] = {}
        for i in range(n):
            tag, _, off, length = struct.unpack_from(">4sIII", data, 12 + 16 * i)
            self.tables[tag.decode("latin-1")] = (off, length)
        for need in ("head", "cmap", "glyf", "loca", "hmtx", "hhea", "maxp"):
            if need not in self.tables:
                raise FontError(f"в шрифте нет таблицы {need}")
        head = self.tables["head"][0]
        self.units = struct.unpack_from(">H", data, head + 18)[0]
        self._long_loca = struct.unpack_from(">h", data, head + 50)[0] == 1
        hhea = self.tables["hhea"][0]
        self.ascent, self.descent = struct.unpack_from(">hh", data, hhea + 4)
        self._n_hmetrics = struct.unpack_from(">H", data, hhea + 34)[0]
        self._n_glyphs = struct.unpack_from(">H", data, self.tables["maxp"][0] + 4)[0]
        self._cmap = self._read_cmap()

    # ── таблицы

    def _read_cmap(self) -> dict[int, int]:
        base = self.tables["cmap"][0]
        n = struct.unpack_from(">H", self.data, base + 2)[0]
        best = None
        for i in range(n):
            pid, eid, off = struct.unpack_from(">HHI", self.data, base + 4 + 8 * i)
            fmt = struct.unpack_from(">H", self.data, base + off)[0]
            score = {12: 3, 4: 2}.get(fmt, 0) if (pid, eid) in ((3, 1), (3, 10), (0, 3), (0, 4), (0, 6)) else 0
            if score and (best is None or score > best[0]):
                best = (score, base + off, fmt)
        if best is None:
            raise FontError("в шрифте нет юникодной таблицы символов")
        _, off, fmt = best
        out: dict[int, int] = {}
        d = self.data
        if fmt == 4:
            segx2 = struct.unpack_from(">H", d, off + 6)[0]
            ends = off + 14
            starts = ends + segx2 + 2
            deltas = starts + segx2
            ranges = deltas + segx2
            for s in range(segx2 // 2):
                end, start = struct.unpack_from(">H", d, ends + 2 * s)[0], struct.unpack_from(">H", d, starts + 2 * s)[0]
                delta = struct.unpack_from(">h", d, deltas + 2 * s)[0]
                ro = struct.unpack_from(">H", d, ranges + 2 * s)[0]
                for c in range(start, min(end, 0xFFFE) + 1):
                    if ro == 0:
                        g = (c + delta) & 0xFFFF
                    else:
                        g = struct.unpack_from(">H", d, ranges + 2 * s + ro + 2 * (c - start))[0]
                        g = (g + delta) & 0xFFFF if g else 0
                    if g:
                        out[c] = g
        else:  # 12
            groups = struct.unpack_from(">I", d, off + 12)[0]
            for k in range(groups):
                start, end, gid = struct.unpack_from(">III", d, off + 16 + 12 * k)
                for c in range(start, min(end, start + 0x10000) + 1):
                    out[c] = gid + c - start
        return out

    def glyph_id(self, ch: str) -> int:
        return self._cmap.get(ord(ch), 0)

    def advance(self, gid: int) -> int:
        hmtx = self.tables["hmtx"][0]
        i = min(gid, self._n_hmetrics - 1)
        return struct.unpack_from(">H", self.data, hmtx + 4 * i)[0]

    def _glyph_range(self, gid: int) -> tuple[int, int]:
        loca = self.tables["loca"][0]
        if gid >= self._n_glyphs:
            return 0, 0
        if self._long_loca:
            a, b = struct.unpack_from(">II", self.data, loca + 4 * gid)
        else:
            a, b = (x * 2 for x in struct.unpack_from(">HH", self.data, loca + 2 * gid))
        return a, b

    def contours(self, gid: int, depth: int = 0) -> list[list[tuple[float, float, bool]]]:
        """Контуры глифа: списки точек (x, y, на_кривой) в единицах шрифта, ось Y вверх."""
        a, b = self._glyph_range(gid)
        if b <= a or depth > 8:
            return []
        d = self.data
        p = self.tables["glyf"][0] + a
        n_contours = struct.unpack_from(">h", d, p)[0]
        if n_contours < 0:
            return self._composite(p + 10, depth)
        ends = struct.unpack_from(f">{n_contours}H", d, p + 10)
        n_points = ends[-1] + 1 if ends else 0
        q = p + 10 + 2 * n_contours
        q += 2 + struct.unpack_from(">H", d, q)[0]  # инструкции хинтинга пропускаем
        flags: list[int] = []
        while len(flags) < n_points:
            f = d[q]
            q += 1
            flags.append(f)
            if f & 8:
                flags.extend([f] * d[q])
                q += 1
        flags = flags[:n_points]

        def coords(short_bit: int, same_bit: int) -> list[int]:
            nonlocal q
            vals, v = [], 0
            for f in flags:
                if f & short_bit:
                    dv = d[q]
                    q += 1
                    v += dv if f & same_bit else -dv
                elif not f & same_bit:
                    v += struct.unpack_from(">h", d, q)[0]
                    q += 2
                vals.append(v)
            return vals

        xs = coords(2, 16)
        ys = coords(4, 32)
        out, start = [], 0
        for end in ends:
            out.append([(xs[i], ys[i], bool(flags[i] & 1)) for i in range(start, end + 1)])
            start = end + 1
        return out

    def _composite(self, p: int, depth: int) -> list:
        d = self.data
        result = []
        while True:
            flags, gid = struct.unpack_from(">HH", d, p)
            p += 4
            if flags & 1:
                dx, dy = struct.unpack_from(">hh", d, p)
                p += 4
            else:
                dx, dy = struct.unpack_from(">bb", d, p)
                p += 2
            if not flags & 2:
                dx = dy = 0  # привязка по точкам — редкость в обычных шрифтах, ставим без сдвига
            a = dd = 1.0
            b = c = 0.0
            if flags & 8:
                a = dd = struct.unpack_from(">h", d, p)[0] / 16384
                p += 2
            elif flags & 0x40:
                a, dd = (v / 16384 for v in struct.unpack_from(">hh", d, p))
                p += 4
            elif flags & 0x80:
                a, b, c, dd = (v / 16384 for v in struct.unpack_from(">hhhh", d, p))
                p += 8
            for contour in self.contours(gid, depth + 1):
                result.append([(a * x + c * y + dx, b * x + dd * y + dy, on) for x, y, on in contour])
            if not flags & 0x20:
                break
        return result


def _contour_to_path(points: list[tuple[float, float, bool]], ox: float) -> dict | None:
    """Квадратичный контур TrueType → замкнутый кубический путь Lottie {v, i, o, c} (ось Y вниз).

    Касательные в Lottie задаются относительно вершины; Q(p, c, e) = C(p, p + 2/3(c - p), e + 2/3(c - e)).
    """
    if len(points) < 2:
        return None
    pts = list(points)
    k = next((i for i, p in enumerate(pts) if p[2]), None)
    if k is None:  # все точки «вне кривой»: начинаем с середины первой пары
        a, b = pts[0], pts[1]
        pts.insert(1, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, True))
        k = 1
    pts = pts[k:] + pts[:k]
    full: list[tuple[float, float, bool]] = []
    for i, p in enumerate(pts):  # между двумя «off»-точками подразумевается точка на кривой
        full.append(p)
        nxt = pts[(i + 1) % len(pts)]
        if not p[2] and not nxt[2]:
            full.append(((p[0] + nxt[0]) / 2, (p[1] + nxt[1]) / 2, True))
    segs: list[tuple[tuple, tuple | None]] = []
    i, n = 0, len(full)
    while i < n:
        nxt = full[(i + 1) % n]
        if nxt[2]:
            segs.append((full[i], None))
            i += 1
        else:
            segs.append((full[i], nxt))
            i += 2
    m = len(segs)
    v = [[p[0] + ox, -p[1]] for p, _ in segs]
    ins = [[0.0, 0.0] for _ in range(m)]
    outs = [[0.0, 0.0] for _ in range(m)]
    for j, (p, c) in enumerate(segs):
        if c is None:
            continue
        e = segs[(j + 1) % m][0]
        outs[j] = [2 / 3 * (c[0] - p[0]), -2 / 3 * (c[1] - p[1])]
        ins[(j + 1) % m] = [2 / 3 * (c[0] - e[0]), -2 / 3 * (c[1] - e[1])]
    r = lambda pt: [round(pt[0], 1), round(pt[1], 1)]  # noqa: E731
    return {"v": [r(x) for x in v], "i": [r(x) for x in ins], "o": [r(x) for x in outs], "c": True}


@lru_cache(maxsize=4)
def load_font(path: str | None = None) -> TrueTypeFont:
    explicit = path or os.environ.get("TEMPLATE_FONT", "")
    for candidate in ([Path(explicit)] if explicit else []) + list(CANDIDATES):
        try:
            if candidate.is_file():
                return TrueTypeFont(candidate.read_bytes())
        except (OSError, FontError, struct.error):
            continue
    raise FontError("не найден шрифт для надписей: положи TrueType-шрифт в assets/fonts/template.ttf")


@lru_cache(maxsize=2048)
def _text_drawing(text: str) -> tuple:
    font = load_font()
    paths, x = [], 0.0
    fallback = font.glyph_id("?")
    for ch in text:
        gid = font.glyph_id(ch) or (0 if ch.isspace() else fallback)
        for contour in font.contours(gid):
            path = _contour_to_path(contour, x)
            if path:
                paths.append(path)
        x += font.advance(gid)
    if not paths:
        return ()
    xs = [pt[0] for p in paths for pt in p["v"]]
    ys = [pt[1] for p in paths for pt in p["v"]]
    return (tuple(paths), (min(xs), min(ys), max(xs), max(ys)))


def text_drawing(text: str) -> dict | None:
    """Надпись как векторный рисунок (None — пусто). Цвет None: красится цветом надписи шаблона."""
    text = " ".join(text.split())
    if not text:
        return None
    res = _text_drawing(text)
    if not res:
        return None
    paths, bbox = res
    return {"bbox": list(bbox), "layers": [{"color": None, "paths": [dict(p) for p in paths]}]}
