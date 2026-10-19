"""Регрессии редактора: корректный спрайт, настоящие клетки и палитра хрома."""
import copy
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from app import catalog, editor, lottie


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def test_icon_sprite_is_valid_xml_and_transport_symbols_are_nonempty():
    path = Path(__file__).resolve().parents[1] / "webapp/assets/icons.svg"
    root = ET.parse(path).getroot()
    symbols = {node.attrib["id"]: node for node in root}
    for name in ("play", "pause", "chev-down", "star", "users"):
        assert len(symbols["i-" + name]) > 0


def pixel_cells(anim):
    """Клетки мозаики: (цвет, прямоугольник) по группам «ezz pixel»."""
    out = []
    for group in walk(anim):
        if group.get("ty") == "gr" and group.get("nm") == "ezz pixel":
            fill = next(i for i in group["it"] if i.get("ty") == "fl")
            out += [(tuple(fill["c"]["k"][:3]), i) for i in group["it"] if i.get("ty") == "rc"]
    return out


def square(x, y, size, rgb, reverse=False):
    v = [[x, y], [x + size, y], [x + size, y + size], [x, y + size]]
    if reverse:
        v.reverse()
    return {"ty": "gr", "it": [{"ty": "sh", "ks": {"a": 0, "k": {"c": True, "v": v, "i": [[0, 0]] * 4, "o": [[0, 0]] * 4}}},
                               editor._fill(rgb), editor._tr()]}


def scene(*shapes):
    return {"w": 512, "h": 512, "ip": 0, "op": 180, "fr": 60, "layers": [{"ty": 4, "ind": 1, "ks": {}, "shapes": list(shapes)}]}


def test_square_pixel_fills_open_nested_paths_and_leaves_cell_gaps():
    shape = {"c": False, "v": [[96, 96], [224, 96], [224, 224], [96, 224]],
             "i": [[0, 0]] * 4, "o": [[0, 0]] * 4}
    nested = {"ty": "gr", "it": [{"ty": "sh", "ks": {"a": 0, "k": shape}}, editor._tr()]}
    out = editor.apply(copy.deepcopy(scene(nested, editor._fill([1, 0, 0]))), editor.clean_edit({"fx": {"pixel": {"on": True, "grid": 32, "gap": 12}}}))
    cells = pixel_cells(out)
    assert len(cells) == 64  # 128 px / клетка 16 px = 8×8
    assert all(rc["s"]["k"] == [14.08, 14.08] for _c, rc in cells)  # зазор 12% клетки
    assert not editor._forbidden(out)


def test_pixel_mosaic_has_one_color_per_cell():
    # красный квадрат поверх синего: под красным синих клеток нет, клетки не накладываются
    out = editor.apply(scene(square(128, 128, 64, [1, 0, 0]), square(96, 96, 128, [0, 0, 1])),
                       editor.clean_edit({"fx": {"pixel": {"on": True, "grid": 32}}}))
    cells = pixel_cells(out)
    places = [tuple(rc["p"]["k"]) for _c, rc in cells]
    assert len(places) == len(set(places)) == 64
    assert sum(1 for c, _rc in cells if c == (1, 0, 0)) == 16
    assert len(out["layers"]) == 1 and out["assets"] == []


def test_pixel_keeps_shape_changes_as_hold_keys_without_duplicate_times():
    a = {"c": True, "v": [[96, 96], [160, 96], [160, 160], [96, 160]], "i": [[0, 0]] * 4, "o": [[0, 0]] * 4}
    b = {**a, "v": [[160, 96], [224, 96], [224, 160], [160, 160]]}
    shape = {"ty": "sh", "ks": {"a": 1, "k": [{"t": 0, "s": [a]}, {"t": 90, "s": [b]}, {"t": 180, "s": [a]}]}}
    out = editor.apply(scene(shape, editor._fill([1, 0, 0])), editor.clean_edit({"fx": {"pixel": {"on": True}}}))
    tracks = [rc["s"]["k"] for _c, rc in pixel_cells(out) if rc["s"]["a"] == 1]
    assert tracks
    assert all(len({key["t"] for key in track}) == len(track) for track in tracks)
    assert any(any(key["s"] == [0, 0] for key in track) and any(key["s"][0] > 0 for key in track) for track in tracks)
    assert all(track[-1]["s"] == track[0]["s"] for track in tracks)  # петля без рывка


def test_pixel_nonzero_fill_keeps_disjoint_contours_with_opposite_winding():
    def shape(x, reverse=False):
        v = [[x, 96], [x + 64, 96], [x + 64, 160], [x, 160]]
        if reverse:
            v.reverse()
        return {"ty": "sh", "ks": {"k": {"c": True, "v": v, "i": [[0, 0]] * 4, "o": [[0, 0]] * 4}}}
    out = editor.apply(scene(shape(96), shape(224, True), editor._fill([1, 0, 0])), editor.clean_edit({"fx": {"pixel": {"on": True, "grid": 32}}}))
    assert len(pixel_cells(out)) == 32  # обе фигуры, а не только контур с «главным» направлением


def test_holo_custom_tint_has_dark_and_white_reflections():
    anim = lottie.load(catalog.get("classic-001").path)
    edit = editor.clean_edit({"fx": {"holo": {"on": True, "tint": "#0088FF"}}})
    assert edit["fx"]["holo"]["tint"] == "#0088ff"
    out, data = editor.render(anim, edit)
    gradients = [node for node in walk(out) if node.get("nm") == "ezz holo" and node.get("ty") in ("gf", "gs")]
    assert gradients and len(data) <= lottie.TG_LIMIT
    stops = [node["g"]["k"]["k"][n + 1:n + 4] for node in gradients for n in range(0, len(node["g"]["k"]["k"]), 4)]
    assert any(max(rgb) < 0.03 for rgb in stops)
    assert any(min(rgb) > 0.85 for rgb in stops)
    assert any(rgb[2] > 0.9 and rgb[0] < 0.1 for rgb in stops)


def test_pixel_draws_reused_asset_even_when_first_instance_has_zero_scale():
    shape = {"c": True, "v": [[96, 96], [224, 96], [224, 224], [96, 224]], "i": [[0, 0]] * 4, "o": [[0, 0]] * 4}
    anim = {"w": 512, "h": 512, "ip": 0, "op": 180, "fr": 60,
            "layers": [{"ty": 0, "ind": 1, "refId": "reused", "ks": {"s": {"k": [0, 0]}}},
                       {"ty": 0, "ind": 2, "refId": "reused", "ks": {"s": {"k": [100, 100]}}}],
            "assets": [{"id": "reused", "layers": [{"ty": 4, "ind": 1, "ks": {},
                        "shapes": [{"ty": "sh", "ks": {"k": shape}}, editor._fill([1, 0, 0])]}]}]}
    out = editor.apply(anim, editor.clean_edit({"fx": {"pixel": {"on": True, "grid": 32}}}))
    assert len(pixel_cells(out)) == 64


def test_pixel_and_holo_keep_colored_contours_within_tgs_limit():
    anim = lottie.load(catalog.get("classic-001").path)
    edit = editor.clean_edit({"fx": {"pixel": {"on": True}, "holo": {"on": True, "palette": "red"}}})
    out, data = editor.render(anim, edit)
    assert any(node.get("nm") == "ezz holo" for node in walk(out))
    assert len(data) <= lottie.TG_LIMIT and not editor._forbidden(out)


def test_holo_adds_chrome_edge_along_sticker_silhouette():
    anim = lottie.load(catalog.get("classic-001").path)
    out, data = editor.render(anim, editor.clean_edit({"fx": {"holo": {"on": True}}}))
    edge = [L for L in out["layers"] if L.get("refId") == "ezz_holo_edge"]
    assert len(edge) == 1 and out["layers"][-1] is edge[0]  # копия силуэта лежит под картинкой
    strokes = [n for n in walk(out["assets"]) if n.get("nm") == "ezz holo edge"]
    assert strokes and all(n["ty"] == "gs" for n in strokes)
    assert len(data) <= lottie.TG_LIMIT
    checks = {c["id"]: c for c in editor.check(out, data, anim)}
    assert checks["loop"]["ok"] and checks["keys"]["ok"] and checks["forbidden"]["ok"]
    no_edge = editor.render(anim, editor.clean_edit({"fx": {"holo": {"on": True, "edge": 0}}}))[0]
    assert not any(L.get("refId") == "ezz_holo_edge" for L in no_edge["layers"])


def test_clean_edit_ignores_unknown_effect_settings():
    # правки из localStorage старой или новой версии Mini App не должны ломать превью
    edit = editor.clean_edit({"fx": {"pixel": {"on": True, "grid": 24, "dither": 3}, "holo": {"on": True, "shimmer": "x"}}})
    assert edit["fx"]["pixel"]["grid"] == 24 and "dither" not in edit["fx"]["pixel"]
    assert "shimmer" not in edit["fx"]["holo"]
