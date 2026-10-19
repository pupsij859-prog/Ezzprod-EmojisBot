"""Импорт сторонних шаблонов TGS в каталог: логотип «iD» становится местом под ник или логотип пользователя.

    python tools/import_templates.py <категория> <файл.tgs> [файл.tgs ...]

Файлы сохраняются как templates/<категория>-NNN.tgs (номера по порядку, после уже существующих), к каждому
рисуется превью webapp/assets/thumbs/<id>.webp.

Логотип автора — группа из трёх фигур: точка, палочка «i» и «D» (по числу вершин: 4/10/25, 4/12/52 или 6/11/35).
Его заливки получают имя «ezzprod color» (app/lottie.py → SLOT): группа логотипа становится надписью. Сам логотип
заменяется родной надписью «Ezz» (NATIVE_TEXT), а вместо неё personalize рисует ник или логотип пользователя
с тем же движением. Если логотип лежит прямо в слое, его
фигуры оборачиваются в группу. Шаблон без логотипа импортируется как есть (ник на нём не появится).
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import fonts, lottie  # noqa: E402
from app.pixel import Scene  # noqa: E402

LOGO_SIGNATURES = ({4, 10, 25}, {4, 12, 52}, {6, 11, 35})
NATIVE_TEXT = "Ezz"  # надпись шаблона по умолчанию вместо логотипа «iD»
THUMB = 192
SUPERSAMPLE = 3  # превью считается в 3 раза крупнее и уменьшается — края сглажены


def _vertices(group: dict) -> int | None:
    shapes = [i for i in group.get("it", []) if isinstance(i, dict) and i.get("ty") == "sh"]
    if len(shapes) != 1:
        return None
    k = shapes[0].get("ks", {}).get("k")
    shape = k[0].get("s") if isinstance(k, list) and k and isinstance(k[0], dict) else k
    if isinstance(shape, list):
        shape = shape[0] if shape else None
    return len(shape["v"]) if isinstance(shape, dict) and shape.get("v") else None


def _paint_rgb(paint: dict | None) -> list[float]:
    """Цвет краски логотипа: сплошной заливки или первой точки градиента (белый, если не понять)."""
    if paint and paint.get("ty") == "fl" and isinstance(paint.get("c", {}).get("k"), list) and not paint["c"].get("a"):
        return [float(x) for x in paint["c"]["k"][:3]]
    stops = paint.get("g", {}).get("k", {}).get("k") if paint else None
    if isinstance(stops, list) and len(stops) >= 4 and all(isinstance(x, (int, float)) for x in stops[1:4]):
        return [float(x) for x in stops[1:4]]
    return [1.0, 1.0, 1.0]


def mark_logos(anim: dict) -> int:
    """Логотипы «iD» → надписи с заливками «ezzprod color». Возвращает, сколько логотипов найдено."""
    found = 0

    def visit(o) -> None:
        nonlocal found
        if isinstance(o, dict):
            key = "it" if o.get("ty") == "gr" else "shapes" if o.get("ty") == 4 else None
            kids = o.get(key) if key else None
            if isinstance(kids, list):
                groups = [g for g in kids if isinstance(g, dict) and g.get("ty") == "gr"]
                letters = [g for g in groups if _vertices(g) is not None]
                # ровно три фигуры логотипа и больше ничего: иначе в «надпись» попала бы чужая картинка
                if len(letters) == 3 and len(groups) == 3 and {_vertices(g) for g in letters} in LOGO_SIGNATURES:
                    shared = next((i for i in kids if isinstance(i, dict) and i.get("ty") in ("fl", "gf")), None)
                    for g in letters:
                        fill = next((i for i in g["it"] if isinstance(i, dict) and i.get("ty") == "fl"), None)
                        if fill is not None:
                            fill["nm"] = lottie.SLOT
                        else:  # одна краска на весь логотип: у буквы своя метка-заливка, невидимая — красит общая
                            mark = {"ty": "fl", "nm": lottie.SLOT, "c": {"a": 0, "k": [*_paint_rgb(shared), 1]},
                                    "o": {"a": 0, "k": 0}, "r": 1, "bm": 0}
                            n = next((k for k, i in enumerate(g["it"]) if isinstance(i, dict) and i.get("ty") == "tr"),
                                     len(g["it"]))
                            g["it"].insert(n, mark)
                    if key == "shapes":  # надпись должна быть группой: оборачиваем фигуры слоя
                        word = {"ty": "gr", "nm": "ezz logo", "it": [*kids, lottie._transform()]}
                        o["shapes"] = [word]
                    found += 1
                    return
            for v in o.values():
                if isinstance(v, (dict, list)):
                    visit(v)
        elif isinstance(o, list):
            for v in o:
                visit(v)

    visit(anim)
    return found


def bake_text(anim: dict, text: str) -> None:
    """Родная надпись вместо логотипа: «iD» → text шрифтом по умолчанию, во всё место логотипа и его цветом.
    Буквы надписи — снова «ezzprod color», так что ник пользователя потом встаёт на их место. Если логотип
    красила общая краска группы (его метки-заливки невидимы), надпись остаётся под ней — с тем же градиентом."""
    drawing = fonts.text_drawing(text)
    for group in lottie.word_groups(anim):
        box = lottie._letters_bbox(group)
        if box is None:
            continue
        slots = [i for L in group["it"] if lottie._is_letter(L) for i in L["it"]
                 if i.get("ty") == "fl" and i.get("nm") == lottie.SLOT]
        hidden = all(isinstance(f.get("o"), dict) and f["o"].get("k") == 0 for f in slots)
        rgb = lottie._solid(slots[0]) if slots and lottie._solid(slots[0]) else [1.0, 1.0, 1.0]
        drawn = lottie._place(drawing, box, rgb)
        if hidden:
            for g in drawn:
                for i in g["it"]:
                    if i.get("ty") == "fl":
                        i["o"] = {"a": 0, "k": 0}
        rest = [i for i in group["it"] if not lottie._is_letter(i)]
        tr = [i for i in rest if i.get("ty") == "tr"]
        other = [i for i in rest if i.get("ty") != "tr"]
        # общая краска рисует то, что перед ней: при невидимых метках надпись ставим до неё
        group["it"] = (drawn + other + tr) if hidden else (other + drawn + tr)


def drop_empty_comps(anim: dict) -> None:
    """То, на чём падает облегчённый lottie-web в Mini App: пустые прекомпозиции (и слои, которые на них ссылаются)
    и фигурные слои без списка фигур."""
    for layers in [anim.get("layers") or [], *[a.get("layers") or [] for a in anim.get("assets") or []]]:
        for L in layers:
            if isinstance(L, dict) and L.get("ty") == 4 and not isinstance(L.get("shapes"), list):
                L["shapes"] = []
    empty = {a.get("id") for a in anim.get("assets") or [] if isinstance(a, dict) and a.get("layers") == []}
    if not empty:
        return
    anim["assets"] = [a for a in anim["assets"] if a.get("id") not in empty]

    def clean(layers: list) -> list:
        return [L for L in layers if not (L.get("ty") == 0 and L.get("refId") in empty)]

    anim["layers"] = clean(anim.get("layers") or [])
    for a in anim.get("assets") or []:
        if "layers" in a:
            a["layers"] = clean(a["layers"])


class MatteScene(Scene):
    """Сцена мозаики (app/pixel.py) с масками слоёв (track matte): слой с «tt» виден только там, где его
    маска — слой над ним с «td». Мозаике маски не нужны, а без них у части шаблонов на превью лезет фон."""

    def _layer_only(self, layers: list, n: int, base, alpha: float, t: float, depth: int) -> dict:
        saved, self.canvas = self.canvas, {}
        only = [dict(L, hd=True) if k != n else {k2: v for k2, v in L.items() if k2 != "td"} for k, L in enumerate(layers)]
        super()._comp(only, base, alpha, t, depth)
        drawn, self.canvas = self.canvas, saved
        return drawn

    def _comp(self, layers: list, base, alpha: float, t: float, depth: int) -> None:
        if not any(isinstance(L, dict) and L.get("tt") for L in layers):
            super()._comp(layers, base, alpha, t, depth)
            return
        for n in range(len(layers) - 1, -1, -1):  # снизу вверх
            L = layers[n]
            if not isinstance(L, dict) or L.get("td"):
                continue
            drawn = self._layer_only(layers, n, base, alpha, t, depth)
            if L.get("tt") and n > 0:
                mask = self._layer_only(layers, n - 1, base, alpha, t, depth)
                inverted = L["tt"] in (2, 4)
                for key, px in drawn.items():
                    m = mask.get(key, [0, 0, 0, 0])[3]
                    px[3] *= (1 - m) if inverted else m
            for key, (r, g, b, a, kind) in drawn.items():
                if a <= 0.004:
                    continue
                dst = self.canvas.get(key)
                if dst is None:
                    self.canvas[key] = [r, g, b, a, kind]
                    continue
                out = a + dst[3] * (1 - a)
                k = a / out
                self.canvas[key] = [dst[0] + (r - dst[0]) * k, dst[1] + (g - dst[1]) * k, dst[2] + (b - dst[2]) * k, out, kind]


def _best_frame(anim: dict) -> float:
    """Кадр для превью: из нескольких моментов цикла — тот, где картинка заполнена сильнее всего
    (в начале цикла детали у многих шаблонов ещё «въезжают» или свёрнуты)."""
    ip, op = float(anim.get("ip", 0)), float(anim.get("op", 180))
    rough = MatteScene(anim, {"grid": 64, "lines": 0})
    frames = [round(ip + (op - ip) * f) for f in (0.35, 0.5, 0.65, 0.8, 0.95)]
    return max(frames, key=lambda t: sum(1 for px in rough.frame(t).values() if px[3] >= 0.5))


def thumbnail(anim: dict, path: Path) -> None:
    """Превью 192×192: самый «полный» кадр цикла, с масками слоёв и сглаживанием."""
    size = THUMB * SUPERSAMPLE
    scene = MatteScene(anim, {"grid": size, "lines": 0})
    cells = scene.frame(_best_frame(anim))
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    for (c, r), (red, green, blue, alpha, _kind) in cells.items():
        if 0 <= c < size and 0 <= r < size and alpha > 0.004:
            px[c, r] = (round(red * 255), round(green * 255), round(blue * 255), round(min(1.0, alpha) * 255))
    img.resize((THUMB, THUMB), Image.LANCZOS).save(path, "WEBP", quality=90, method=6)


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    category, files = sys.argv[1], [Path(f) for f in sys.argv[2:]]
    tdir = ROOT / "templates"
    taken = [int(p.stem.split("-")[1]) for p in tdir.glob(f"{category}-*.tgs")]
    start = (max(taken) if taken else 0) + 1
    for n, src in enumerate(files, start):
        anim = json.loads(gzip.decompress(src.read_bytes()))
        logos = mark_logos(anim)
        if logos:
            bake_text(anim, NATIVE_TEXT)
        drop_empty_comps(anim)
        tid = f"{category}-{n:03d}"
        data = lottie.encode(anim)
        (tdir / f"{tid}.tgs").write_bytes(data)
        thumbnail(anim, ROOT / "webapp" / "assets" / "thumbs" / f"{tid}.webp")
        print(f"{tid} ← {src.name}: логотипов {logos}, {len(data) // 1024} КБ{'  (больше 64 КБ!)' if len(data) > lottie.TG_LIMIT else ''}")


if __name__ == "__main__":
    main()
