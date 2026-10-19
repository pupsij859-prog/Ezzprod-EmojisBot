"""Продвинутый редактор (Pro): проверка правок, эффекты в пределах требований Telegram, доступ только в Pro."""
from __future__ import annotations

import copy
import gzip
import json

import pytest

from app import catalog, editor, fonts, lottie, services
from app.build.render import ItemSpec, TemplateRenderer
from tests.conftest import dev

TEMPLATE = "classic-001"


def base_anim(text: str = "ez"):
    anim = lottie.load(catalog.get(TEMPLATE).path)
    return lottie.personalize(anim, color=None, text=fonts.text_drawing(text), logo=None)


# ───────────── проверка того, что прислал клиент ─────────────

def test_clean_edit_keeps_only_real_changes():
    assert editor.clean_edit(None) is None
    assert editor.clean_edit({"fx": {"pixel": {"on": False}, "burst": {"kind": "none"}}}) is None
    e = editor.clean_edit({
        "parts": {"a2.12": {"x": 10, "s": 9999, "anim": "pulse", "fill": "#FF0000"}, "r.1": {"anim": "none"}},
        "colors": {"#AABBCC": "#112233", "#ffffff": "#ffffff"},
        "fx": {"glitch": {"on": True, "count": 99}, "metal": {"on": False}},
    })
    assert e["parts"] == {"a2.12": {"x": 10.0, "s": 500.0, "anim": "pulse", "fill": "#ff0000"}}  # масштаб упёрся в предел
    assert e["colors"] == {"#aabbcc": "#112233"}  # «цвет → тот же цвет» не правка
    assert set(e["fx"]) == {"glitch"} and e["fx"]["glitch"]["count"] == 16 and e["fx"]["glitch"]["c1"] == "#ff1f4b"


@pytest.mark.parametrize("bad", [
    {"parts": {"../x": {}}},
    {"parts": {"r.1": {"anim": "explode"}}},
    {"parts": {"r.1": {"x": "10"}}},
    {"parts": {"r.1": {"x": float("nan")}}},
    {"colors": {"red": "#000000"}},
    {"fx": {"pixel": {"on": True, "shape": "hex"}}},
    {"fx": {"holo": {"on": True, "parts": ["<script>"]}}},
    {"fx": {"laser": {"on": True}}},
    {"script": 1},
    [1, 2],
])
def test_clean_edit_rejects_garbage(bad):
    with pytest.raises(ValueError):
        editor.clean_edit(bad)


# ───────────── части и цвета ─────────────

def test_parts_have_names_and_text_slot():
    parts = editor.parts(base_anim("Kot"), "Kot")
    names = [p["name"] for p in parts]
    assert "«Kot»" in names
    assert len(names) == len(set(names))  # одинаковые названия пронумерованы
    assert all(p["id"] and len(p["bbox"]) == 4 and p["color"].startswith("#") for p in parts)
    assert any("круг" in n for n in names)


def test_colors_are_counted():
    colors = editor.colors(base_anim())
    assert colors and colors == sorted(colors, key=lambda c: -c["count"])
    assert all(c["count"] >= 1 for c in colors)


def test_part_edit_wraps_layer_in_null_and_recolors_only_that_part():
    anim = base_anim()
    part = next(p for p in editor.parts(anim) if p["kind"] == "circle")
    before = editor.colors(anim)
    out = editor.apply(copy.deepcopy(anim), editor.clean_edit({"parts": {part["id"]: {"x": 12, "r": 30, "anim": "spin", "fill": "#ff0000"}}}))
    index = editor._layer_index(out)
    layers, layer = index[part["id"]]
    null = next(l for l in layers if l.get("ind") == layer["parent"])
    assert null["ty"] == 3 and null["ks"]["r"]["a"] == 1  # вращение — на новом родителе, свои ключи слоя целы
    after = {c["hex"]: c["count"] for c in editor.colors(out)}
    assert after.get("#ff0000") and sum(after.values()) == sum(c["count"] for c in before)


def test_color_map_replaces_everywhere():
    anim = base_anim()
    src = editor.colors(anim)[0]["hex"]
    out = editor.apply(copy.deepcopy(anim), {"colors": {src: "#00ff00"}})
    hexes = {c["hex"] for c in editor.colors(out)}
    assert "#00ff00" in hexes and src not in hexes


# ───────────── эффекты: всё остаётся валидным TGS ─────────────

@pytest.mark.parametrize("fx", [
    {"pixel": {"on": True}},
    {"pixel": {"on": True, "shape": "circle", "fps": 8, "colors": 3}},
    {"metal": {"on": True}},
    {"holo": {"on": True, "palette": "rainbow", "turns": 2}},
    {"glitch": {"on": True}},
    {"burst": {"kind": "hearts"}},
    {"burst": {"kind": "flash"}},
])
def test_effects_render_within_telegram_limits(fx):
    anim = base_anim()
    out, data = editor.render(anim, editor.clean_edit({"fx": fx}))
    assert len(data) <= lottie.TG_LIMIT
    back = json.loads(gzip.decompress(data))
    assert back["w"] == back["h"] == 512 and back["fr"] == 60 and back["op"] - back["ip"] == 180
    checks = {c["id"]: c for c in editor.check(out, data, anim)}
    assert checks["forbidden"]["ok"] and checks["keys"]["ok"] and checks["weight"]["ok"]
    if "pixel" not in fx and "glitch" not in fx:
        assert checks["loop"]["ok"]  # эффекты не добавляют рывка в конце цикла


def test_pixel_reduces_palette_without_shifting_hues():
    anim = base_anim()
    n = len(editor.colors(anim))
    out = editor.apply(copy.deepcopy(anim), editor.clean_edit({"fx": {"pixel": {"on": True, "colors": 64}}}))
    assert {c["hex"] for c in editor.colors(out)} == {c["hex"] for c in editor.colors(anim)}  # цветов хватает — без изменений
    out = editor.apply(copy.deepcopy(anim), editor.clean_edit({"fx": {"pixel": {"on": True, "colors": 2}}}))
    assert len(editor.colors(out)) <= min(2, n)


def test_glitch_is_deterministic_per_seed():
    anim = base_anim()
    a = editor.apply(copy.deepcopy(anim), editor.clean_edit({"fx": {"glitch": {"on": True, "seed": 7}}}))
    b = editor.apply(copy.deepcopy(anim), editor.clean_edit({"fx": {"glitch": {"on": True, "seed": 7}}}))
    c = editor.apply(copy.deepcopy(anim), editor.clean_edit({"fx": {"glitch": {"on": True, "seed": 8}}}))
    assert lottie.encode(a) == lottie.encode(b) != lottie.encode(c)


def test_renderer_applies_edit_and_size():
    spec = dict(template_id=TEMPLATE, kind="emoji", text="Kot", color=None)
    plain = TemplateRenderer().render(ItemSpec(**spec))
    edited = TemplateRenderer().render(ItemSpec(**spec, edit_json=json.dumps(editor.clean_edit({"fx": {"metal": {"on": True}}}))))
    assert plain != edited and b'"gf"' in gzip.decompress(edited)
    small = TemplateRenderer().render(ItemSpec(**spec, size=50))
    assert small != plain


def test_short_nick_is_bigger_than_long_one():
    anim = lottie.load(catalog.get(TEMPLATE).path)
    px = lambda t, size=1.0: editor._text_px(lottie.personalize(anim, color=None, text=fonts.text_drawing(t), logo=None, size=size))  # noqa: E731
    assert px("Kot") > px("JavaVersion")
    assert px("Kot", 0.5) < px("Kot") < px("Kot", 1.4)


def test_holo_on_contour_makes_gradient_strokes_and_dark_base():
    anim = base_anim()
    out, data = editor.render(anim, editor.clean_edit({"fx": {"holo": {"on": True}}}))  # по умолчанию — контур
    raw = json.dumps(out)
    assert '"ty": "gs"' in raw and '"ty": "gf"' not in raw
    assert len(data) <= lottie.TG_LIMIT
    _fill, stroke = lottie.main_colors(anim)
    kept = {c["hex"] for c in editor.colors(out)}
    assert kept <= {"#0a0a0d", editor.to_hex(stroke)} | {c["hex"] for c in editor.colors(anim) if c["hex"] == editor.to_hex(stroke)}
    light = editor.render(anim, editor.clean_edit({"fx": {"holo": {"on": True, "dark": False}}}))[0]
    assert {c["hex"] for c in editor.colors(light)} - {editor.to_hex(stroke)}  # без чёрной основы цвета на месте
    both = json.dumps(editor.render(anim, editor.clean_edit({"fx": {"holo": {"on": True, "target": "all"}}}))[0])
    assert '"ty": "gs"' in both and '"ty": "gf"' in both


def test_bw_tool_makes_colors_gray_and_is_idempotent():
    from tools.bw_templates import to_bw

    anim = copy.deepcopy(lottie.load(catalog.get("classic-002").path))
    assert to_bw(anim) is True
    assert all(c["hex"][1:3] == c["hex"][3:5] == c["hex"][5:7] for c in editor.colors(anim))  # r = g = b
    assert to_bw(anim) is False  # второй раз менять нечего


def _float_ints(o, path=""):
    """Поля, которые Telegram принимает только целыми, но записанные дробными (512.0) — он их отклоняет."""
    bad = []
    if isinstance(o, dict):
        bad += [f"{path}/{k}" for k in lottie.INT_KEYS if isinstance(o.get(k), float) and o[k].is_integer()]
        for k, v in o.items():
            bad += _float_ints(v, f"{path}/{k}")
    elif isinstance(o, list):
        for v in o:
            bad += _float_ints(v, path)
    return bad


@pytest.mark.parametrize("fx", [
    {"glitch": {"on": True}},                                   # прекомпозиции глитча: w/h были 512.0
    {"pixel": {"on": True, "grid": 64, "fps": 30, "colors": 64}},  # мозаика: ip/op слоя были 0.0/180.0, и сложность
    {"pixel": {"on": True}, "holo": {"on": True}, "metal": {"on": True}},
])
def test_effects_fit_telegram_rules(fx):
    # пак 11/21/32 на проде: Telegram отвечал «wrong file type» на такие эффекты
    anim = base_anim("EzzProd")
    out, data = editor.render(anim, editor.clean_edit({"fx": fx}))
    saved = json.loads(gzip.decompress(data))
    assert _float_ints(saved) == []
    assert editor.fits(saved, data), editor.complexity(saved)
    assert next(c for c in editor.check(saved, data, anim) if c["id"] == "tg")["ok"]


def test_pixel_ranges_are_clamped_to_telegram_budget():
    e = editor.clean_edit({"fx": {"pixel": {"on": True, "grid": 64, "fps": 30}}})
    assert e["fx"]["pixel"]["grid"] <= 40 and e["fx"]["pixel"]["fps"] <= 20
    assert editor.clean_edit({"fx": {"pixel": {"on": True}}})["fx"]["pixel"]["grid"] == 24


@pytest.mark.parametrize("text", ["Kot", ""])  # ник или родные буквы «ezzprod»
def test_text_part_fill_recolors_letters(text):
    anim = base_anim(text) if text else lottie.load(catalog.get(TEMPLATE).path)
    part = next(p for p in editor.parts(anim, text) if p["kind"] == "text")
    assert part["color"] != "#ff0000"
    out, _ = editor.render(anim, editor.clean_edit({"parts": {part["id"]: {"fill": "#ff0000"}}}))
    letters = list(lottie.text_fills(out))
    assert letters and all(f["c"]["k"][:3] == [1.0, 0.0, 0.0] for f in letters)
    assert next(p for p in editor.parts(out, text) if p["id"] == part["id"])["color"] == "#ff0000"


# ───────────── свой шрифт ника ─────────────

def system_ttf() -> bytes:
    path = next(p for p in fonts.CANDIDATES if p.is_file())
    return path.read_bytes()


def test_font_upload_glyphs_and_pack(client):
    h = dev(930)
    client.post("/api/dev/pay", json={"qty": 2}, headers=h)
    r = client.post("/api/fonts", files={"file": ("Мой шрифт.ttf", system_ttf(), "font/ttf")}, headers=h)
    assert r.status_code == 201, r.text
    font = r.json()
    assert font["name"] == "Мой шрифт" and len(font["id"]) == 32
    plain = client.post("/api/glyphs", json={"text": "Kot"}, headers=h).json()["drawing"]
    own = client.post("/api/glyphs", json={"text": "Kot", "font_id": font["id"]}, headers=h).json()["drawing"]
    assert plain and own
    # чужой шрифт нельзя ни в превью, ни в пак
    assert client.post("/api/glyphs", json={"text": "Kot", "font_id": font["id"]}, headers=dev(931)).status_code == 404
    body = {"type": "emoji", "template_ids": [TEMPLATE], "text": "Kot", "font_id": font["id"]}
    r = client.post("/api/packs", json=body, headers=dev(931))
    assert r.status_code in (402, 422)
    assert client.post("/api/packs", json=body, headers=h).status_code == 201
    assert client.delete(f"/api/fonts/{font['id']}", headers=h).json() == {"ok": True}


def test_preset_fonts_all_draw_cyrillic_and_latin():
    from app import user_fonts

    assert len(user_fonts.PRESETS) == 15
    for p in user_fonts.presets():
        path = user_fonts.PRESET_DIR / f"{p['id']}.ttf"
        assert path.is_file(), p["id"]
        user_fonts._check(path.read_bytes())  # та же проверка, что у загруженных шрифтов
        font = fonts.TrueTypeFont(path.read_bytes())
        assert all(font.glyph_id(ch) for ch in "AzЖя09"), p["id"]


def test_preset_font_list_glyphs_and_pack(client):
    h = dev(932)
    client.post("/api/dev/pay", json={"qty": 2}, headers=h)
    r = client.get("/api/fonts", headers=h).json()
    assert len(r["presets"]) == 15 and r["own"] == [] and r["max"] >= 5
    font_id = r["presets"][0]["id"]
    plain = client.post("/api/glyphs", json={"text": "Kot"}, headers=h).json()["drawing"]
    preset = client.post("/api/glyphs", json={"text": "Kot", "font_id": font_id}, headers=h).json()["drawing"]
    assert preset and preset != plain
    # готовый шрифт доступен всем; выдуманный id — нет
    assert client.post("/api/glyphs", json={"text": "Kot", "font_id": "nosuchfont"}, headers=h).status_code in (404, 422)
    body = {"type": "emoji", "template_ids": [TEMPLATE], "text": "Kot", "font_id": font_id,
            "overrides": {TEMPLATE: {"font_id": "pacifico"}}}
    r = client.post("/api/packs", json=body, headers=h)
    assert r.status_code == 201, r.text
    # готовые шрифты не удаляются и не попадают в список своих
    assert client.delete(f"/api/fonts/{font_id}", headers=h).status_code == 404
    up = client.post("/api/fonts", files={"file": ("Мой.ttf", system_ttf(), "font/ttf")}, headers=h).json()
    assert client.get("/api/fonts", headers=h).json()["own"] == [{"id": up["id"], "name": "Мой"}]


def test_add_items_checks_fonts_and_cleans_params(client):
    h = dev(933)
    client.post("/api/dev/pay", json={"qty": 6}, headers=h)
    from tests.test_api import wait_ready

    pack_id = client.post("/api/packs", json={"type": "emoji", "template_ids": [TEMPLATE], "text": "Kot"}, headers=h).json()["id"]
    assert wait_ready(client, pack_id, h)["status"] == "ready"
    other = client.post("/api/fonts", files={"file": ("x.ttf", system_ttf(), "font/ttf")}, headers=dev(934)).json()
    add = lambda body: client.post(f"/api/packs/{pack_id}/items", json={"template_ids": ["classic-002"], **body}, headers=h)  # noqa: E731
    field = lambda r: (r.status_code, (r.json().get("detail") or {}).get("field") if r.status_code == 422 else None)  # noqa: E731
    # чужой шрифт, кривой id и правки редактора без Pro — отказ, генерации не списываются
    assert field(add({"params": {"font_id": other["id"]}})) == (422, "font")
    assert field(add({"overrides": {"classic-002": {"font_id": other["id"]}}})) == (422, "font")
    assert field(add({"params": {"font_id": "../../x"}}))[0] == 422
    assert add({"overrides": {"classic-002": {"edit": {"colors": {"#aabbcc": "#112233"}}}}}).status_code == 403
    balance = client.get("/api/me", headers=h).json()["balance"]
    # готовый шрифт и свои настройки поверх общих — проходят
    r = add({"params": {"text": "Kot", "font_id": "lobster"}, "overrides": {"classic-002": {"color": "#ff0000"}}})
    assert r.status_code == 200, r.text
    assert client.get("/api/me", headers=h).json()["balance"] == balance - 1


async def test_font_quota_holds_under_parallel_uploads(db, tmp_path):
    import asyncio

    from app import services, user_fonts
    from tests.conftest import make_settings

    settings = make_settings(tmp_path, fonts_max=3)
    await services.upsert_user(db, settings, {"id": 7, "first_name": "q"}, None)
    res = await asyncio.gather(*(user_fonts.save_font(db, settings, 7, "f.ttf", system_ttf()) for _ in range(6)),
                               return_exceptions=True)
    assert sum(isinstance(r, dict) for r in res) == 3
    assert len(await user_fonts.list_fonts(db, 7)) == 3
    assert len(list((tmp_path / "fonts").glob("*.ttf"))) == 3  # лишние файлы удалены


@pytest.mark.parametrize("data, field", [
    (b"OTTO" + b"\0" * 64, "font_otf"),
    (b"wOFF" + b"\0" * 64, "font_web"),
    (b"not a font at all", "font_invalid"),
])
def test_font_upload_rejects_unsupported(client, data, field):
    r = client.post("/api/fonts", files={"file": ("x.ttf", data, "font/ttf")}, headers=dev(940))
    assert r.status_code == 422 and r.json()["detail"]["field"] == field


def test_renderer_uses_own_font(tmp_path):
    path = tmp_path / "f.ttf"
    path.write_bytes(system_ttf())
    spec = dict(template_id=TEMPLATE, kind="emoji", text="Kot", color=None)
    assert TemplateRenderer().render(ItemSpec(**spec, font_path=str(path)))
    assert fonts.text_drawing("Kot", str(tmp_path / "нет.ttf")) == fonts.text_drawing("Kot")  # файла нет — стандартный


# ───────────── доступ: только Pro ─────────────

def test_render_endpoint_is_pro_only(client):
    h = dev(910)
    body = {"template_id": TEMPLATE, "text": "Kot", "edit": {"fx": {"metal": {"on": True}}}}
    r = client.post("/api/render", json=body, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "pro_required"
    client.post("/api/dev/pay", json={"qty": 20}, headers=h)
    r = client.post("/api/render", json=body, headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["anim"]["layers"] and data["parts"] and data["colors"]
    assert {c["id"] for c in data["checks"]} >= {"weight", "fps", "loop", "forbidden"}
    assert any(l.get("ln", "").startswith("ezp-") for l in data["anim"]["layers"] + [
        l for a in data["anim"].get("assets", []) for l in a.get("layers", [])])
    assert client.post("/api/render", json={**body, "edit": {"fx": {"laser": {}}}}, headers=h).status_code == 422
    assert client.post("/api/render", json={**body, "template_id": "nope-001"}, headers=h).status_code == 404


def test_pack_with_edits_requires_pro(client):
    h = dev(920)
    client.post("/api/dev/pay", json={"qty": 5}, headers=h)
    body = {"type": "emoji", "template_ids": [TEMPLATE], "text": "ez", "size": 130,
            "overrides": {TEMPLATE: {"edit": {"fx": {"glitch": {"on": True}}}}}}
    r = client.post("/api/packs", json=body, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "pro_required"
    client.post("/api/dev/pay", json={"qty": 20}, headers=h)
    assert client.post("/api/packs", json=body, headers=h).status_code == 201


def test_validate_pack_request_keeps_size_and_edit(settings):
    req = services.validate_pack_request(settings, {
        "type": "emoji", "template_ids": [TEMPLATE], "size": 999,
        "overrides": {TEMPLATE: {"size": 55, "edit": {"parts": {"r.1": {"o": 50}}}}}})
    assert req["size"] == 160
    assert req["overrides"][TEMPLATE] == {"size": 55, "edit": {"parts": {"r.1": {"o": 50.0}}}}
    assert services.has_edits(req)
    with pytest.raises(services.ValidationFailed):
        services.validate_pack_request(settings, {"type": "emoji", "template_ids": [TEMPLATE], "size": "big"})
