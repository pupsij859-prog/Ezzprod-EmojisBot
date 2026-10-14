"""Настоящая сборка паков: рендер и оркестратор с фальшивым Telegram (без сети)."""
from __future__ import annotations

import gzip
import json

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import GetMe
from PIL import Image, ImageDraw

from app import catalog, fonts, lottie, services, vectorize
from app.build.builder import BuildFailed, TelegramPackBuilder, make_set_name
from app.build.publish import ItemError, SetLevelError, classify
from app.build.render import ItemSpec, TemplateRenderer
from app.packs import PackRunner
from app.notify import NullNotifier
from tests.conftest import make_settings
from tests.test_services import make_user, pay


class FakePublisher:
    """Ведёт себя как Telegram: набор, список элементов, сбои по сценарию."""

    def __init__(self, fail_items=(), set_error_after=None, retry_after_once=False, existing=None):
        self.sets: dict[str, list] = {} if existing is None else existing
        self.fail_items = set(fail_items)          # номера вызовов add/create с одним файлом, которые упадут
        self.set_error_after = set_error_after     # после скольких добавленных — сбой всего набора
        self.retry_after_once = retry_after_once
        self.calls: list[tuple] = []
        self._n = 0

    async def count(self, name):
        return len(self.sets[name]) if name in self.sets else None

    def _check(self, files):
        self._n += 1
        if self.retry_after_once:
            self.retry_after_once = False
            raise TelegramRetryAfter(method=GetMe(), message="Flood", retry_after=0)
        if self.set_error_after is not None and sum(len(v) for v in self.sets.values()) >= self.set_error_after:
            raise SetLevelError("set_full")
        if len(files) == 1 and self._n in self.fail_items:
            raise ItemError("STICKER_PNG_DIMENSIONS")

    async def create(self, owner_id, name, title, kind, files):
        self.calls.append(("create", len(files)))
        self._check(files)
        if name in self.sets:
            raise SetLevelError("set_invalid", "already")
        self.sets[name] = [f.emoji for f in files]

    async def add(self, owner_id, name, kind, file):
        self.calls.append(("add", 1))
        self._check([file])
        self.sets[name].append(file.emoji)

    def link(self, name, kind):
        return f"https://t.me/{'addemoji' if kind == 'emoji' else 'addstickers'}/{name}"


class FastRenderer:
    def render(self, spec):
        if spec.template_id == "classic-013":
            raise ValueError("битый шаблон")
        return b"TGS"


async def make_pack(db, settings, uid=1, n=3, kind="emoji"):
    await make_user(db, settings, uid)
    if n > 1:
        await pay(db, settings, uid, n, n * 6, f"c{uid}-{n}")
    ids = [f"classic-{i:03d}" for i in range(1, n + 1)]
    req = services.validate_pack_request(settings, {"type": kind, "template_ids": ids, "text": "ez"})
    pack, _ = await services.create_pack(db, settings, uid, req, 0)
    return pack


def builder(db, settings, publisher, renderer=None):
    return TelegramPackBuilder(db, settings, renderer or FastRenderer(), publisher, "ezz_test_bot", pause_s=0)


# ───────────────────────── рендер ─────────────────────────

def _logo_variants():
    img = Image.new("RGBA", (64, 40), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((4, 4, 60, 36), fill=(200, 30, 30, 255))
    return tuple(vectorize.size_variants(img))


@pytest.mark.parametrize("kind", ["emoji", "sticker"])
def test_render_produces_valid_tgs(kind):
    data = TemplateRenderer().render(ItemSpec("classic-003", kind, "Кирилл", None, _logo_variants(), stroke="#3d6a9e"))
    assert len(data) <= 64 * 1024
    anim = json.loads(gzip.decompress(data))
    assert (anim["w"], anim["h"], anim["fr"]) == (512, 512, 60) and anim["op"] - anim["ip"] <= 180
    raw = json.dumps(anim)
    assert '"ty": 2' not in raw and '"ty": 5' not in raw          # ни картинок, ни текстовых слоёв
    # заглушка «ezzprod» заменена: остались только наши слои, все — цвета пользователя или логотипа
    groups = lottie.word_groups(anim)
    assert groups and all(not any(lottie._is_letter(i) and i.get("nm") != "ezz layer" for i in g["it"]) for g in groups)
    assert [round(c, 3) for c in [0x3d / 255, 0x6a / 255, 0x9e / 255]] in [
        [round(c, 3) for c in i["c"]["k"][:3]] for g in groups for layer in g["it"] if layer.get("nm") == "ezz layer"
        for i in layer["it"] if i.get("ty") == "fl"]


def test_render_without_personalization_keeps_template_and_recolors():
    tpl = catalog.get("classic-001")
    own = TemplateRenderer().render(ItemSpec("classic-001", "emoji", "", None))
    assert json.loads(gzip.decompress(own)) == lottie.load(tpl.path)          # ничего не меняли
    red = json.loads(gzip.decompress(TemplateRenderer().render(ItemSpec("classic-001", "emoji", "", None, stroke="#ff0000"))))
    assert lottie.slot_color(red) == [1.0, 0.0, 0.0]   # надпись красится вместе с обводкой


def test_every_template_has_a_text_slot_and_fits_with_long_nick():
    text = fonts.text_drawing("WWWWWWWWWWWW")
    for cat in catalog.all_categories():
        for n in range(1, cat.count + 1):
            tpl = catalog.get(f"{cat.id}-{n:03d}")
            anim = lottie.load(tpl.path)
            assert lottie.word_groups(anim), tpl.id
            data = lottie.encode(lottie.personalize(anim, color=[1, 0, 0], text=text, logo=None))
            assert len(data) <= lottie.TG_LIMIT, tpl.id


def test_set_name_follows_telegram_rules():
    name = make_set_name(42, "Ezz_Test_Bot")
    assert name.startswith("ez42_") and name.endswith("_by_ezz_test_bot") and len(name) <= 64 and "__" not in name


def test_error_classification():
    assert isinstance(classify("Bad Request: PEER_ID_INVALID"), SetLevelError)
    assert classify("Bad Request: PEER_ID_INVALID").code == "start_bot"
    assert isinstance(classify("Bad Request: STICKER_PNG_DIMENSIONS"), ItemError)


# ───────────────────────── оркестратор ─────────────────────────

async def test_full_build_creates_set_and_reports_progress(db, settings):
    pack = await make_pack(db, settings, n=3)
    pub = FakePublisher()
    result = await builder(db, settings, pub).build(pack["id"], 0)
    row = await services.get_pack(db, 1, pack["id"])
    assert result.link == f"https://t.me/addemoji/{row['set_name']}"
    assert pub.calls == [("create", 3)]                       # первая пачка одним вызовом
    assert pub.sets[row["set_name"]] == ["✨", "✨", "✨"]     # эмодзи-ассоциация категории «классика»
    assert services.pack_view(row)["done"] == 3


async def test_more_than_first_batch_goes_one_by_one(db, tmp_path):
    settings = make_settings(tmp_path)
    pack = await make_pack(db, settings, n=53)
    pub = FakePublisher()

    class AllGood:
        def render(self, spec):
            return b"WEBP"

    await builder(db, settings, pub, AllGood()).build(pack["id"], 0)
    assert pub.calls[0] == ("create", 50) and pub.calls[1:] == [("add", 1)] * 3


async def test_broken_items_are_skipped_and_refunded(db, tmp_path):
    settings = make_settings(tmp_path)
    pack = await make_pack(db, settings, n=14)               # classic-013 не рендерится
    before = (await services.get_user(db, 1))["balance"]
    await builder(db, settings, FakePublisher()).build(pack["id"], 0)
    view = services.pack_view(await services.get_pack(db, 1, pack["id"]))
    assert (view["done"], view["failed"]) == (13, 1)
    assert (await services.get_user(db, 1))["balance"] == before + 1   # за несобранный элемент вернули
    assert await services.ledger_mismatches(db) == []


async def test_rejected_picture_in_first_batch_falls_back(db, tmp_path):
    settings = make_settings(tmp_path)
    pack = await make_pack(db, settings, n=3)
    # пачка из 3 отклонена целиком (вызов 1), первая одиночная тоже (вызов 2) — набор создаётся со второй
    pub = FakePublisher(fail_items={2})

    async def reject_batch(owner_id, name, title, kind, files, orig=pub.create):
        if len(files) > 1:
            pub.calls.append(("create", len(files)))
            pub._n += 1
            raise ItemError("bad file in batch")
        return await orig(owner_id, name, title, kind, files)

    pub.create = reject_batch
    await builder(db, settings, pub).build(pack["id"], 0)
    view = services.pack_view(await services.get_pack(db, 1, pack["id"]))
    assert (view["done"], view["failed"]) == (2, 1)


async def test_retry_after_is_respected(db, settings):
    pack = await make_pack(db, settings, n=2)
    pub = FakePublisher(retry_after_once=True)
    await builder(db, settings, pub).build(pack["id"], 0)
    assert pub.calls.count(("create", 2)) == 2                # повторили после паузы


async def test_resume_does_not_duplicate_items(db, tmp_path):
    """Процесс упал после того, как Telegram добавил элементы, но до записи в БД: дублей быть не должно."""
    settings = make_settings(tmp_path)
    pack = await make_pack(db, settings, n=5)
    name = await services.set_pack_set_name(db, pack["id"], make_set_name(pack["id"], "ezz_test_bot"))
    pub = FakePublisher(existing={name: ["🟦", "🔵"]})        # 2 уже в наборе, в БД — 0
    await builder(db, settings, pub).build(pack["id"], 0)
    assert pub.calls == [("add", 1)] * 3 and len(pub.sets[name]) == 5


async def test_set_level_error_with_nothing_added_fails_whole_pack(db, settings):
    pack = await make_pack(db, settings, n=3)
    runner = PackRunner(db, settings, builder(db, settings, FakePublisher(set_error_after=0)), NullNotifier())
    balance = (await services.get_user(db, 1))["balance"]
    await runner._run(pack["id"], 0)
    row = await services.get_pack(db, 1, pack["id"])
    assert row["status"] == "failed" and row["error"] == "set_full"
    assert (await services.get_user(db, 1))["balance"] == balance + 3


async def test_set_level_error_midway_keeps_partial_set(db, tmp_path):
    settings = make_settings(tmp_path)
    pack = await make_pack(db, settings, n=55)
    pub = FakePublisher(set_error_after=52)                    # создали 50, добавили 2, дальше — сбой набора
    runner = PackRunner(db, settings, builder(db, settings, pub), NullNotifier())
    balance = (await services.get_user(db, 1))["balance"]
    await runner._run(pack["id"], 0)
    view = services.pack_view(await services.get_pack(db, 1, pack["id"]))
    assert view["status"] == "ready" and (view["done"], view["failed"]) == (52, 3)
    assert (await services.get_user(db, 1))["balance"] == balance + 3
    assert await services.ledger_mismatches(db) == []


async def test_not_processing_pack_is_not_built(db, settings):
    pack = await make_pack(db, settings, n=1)
    await services.fail_pack(db, pack["id"])
    with pytest.raises(BuildFailed):
        await builder(db, settings, FakePublisher()).build(pack["id"], 0)


def _paints(anim, kind) -> list:
    found = []

    def visit(o):
        if isinstance(o, dict):
            if o.get("ty") == kind and o.get("nm") != lottie.SLOT and lottie._solid(o) is not None:
                found.append([round(x, 3) for x in lottie._solid(o)])
            for v in o.values():
                visit(v)
        elif isinstance(o, list):
            for v in o:
                visit(v)
    visit(anim)
    return found


def test_main_colors_are_body_fill_and_outline():
    hexes = lambda c: "#%02x%02x%02x" % tuple(round(x * 255) for x in c)  # noqa: E731
    for tid, fill, stroke in [("classic-001", "#315d99", "#ffffff"), ("classic-003", "#315d99", "#ffffff"),
                              ("classic-025", "#7353a4", "#ffffff")]:
        f, st = lottie.main_colors(lottie.load(catalog.get(tid).path))
        assert (hexes(f), hexes(st)) == (fill, stroke), tid


def test_fill_recolors_body_and_stroke_recolors_outline_and_nick():
    blue, white = [0.192, 0.365, 0.6], [1.0, 1.0, 1.0]
    anim = json.loads(gzip.decompress(TemplateRenderer().render(
        ItemSpec("classic-001", "emoji", "Кирилл", "#ff0000", stroke="#000000"))))
    fills, strokes = _paints(anim, "fl"), _paints(anim, "st")
    assert [1.0, 0.0, 0.0] in fills and not any(lottie._near(c, blue) for c in fills)   # синий стал красным
    assert strokes and all(c == [0.0, 0.0, 0.0] for c in strokes if not lottie._near(c, white))
    assert not any(lottie._near(c, white) for c in strokes)                               # белые контуры — чёрные
    nick = [i for g in lottie.word_groups(anim) for layer in g["it"] if layer.get("nm") == "ezz layer"
            for i in layer["it"] if i.get("ty") == "fl"]
    assert nick and all(i["c"]["k"][:3] == [0.0, 0.0, 0.0] for i in nick)                 # ник — цветом обводки
    # без цветов шаблон не меняется
    plain = json.loads(gzip.decompress(TemplateRenderer().render(ItemSpec("classic-001", "emoji", "", None))))
    assert plain == lottie.load(catalog.get("classic-001").path)


async def test_each_item_is_built_with_its_own_settings(db, tmp_path):
    settings = make_settings(tmp_path)
    await make_user(db, settings, 1)
    await pay(db, settings, 1, 3, 18, "c-own")
    req = services.validate_pack_request(settings, {
        "type": "emoji", "template_ids": ["classic-001", "classic-003"], "text": "общий", "color": "#112233",
        "overrides": {"classic-003": {"text": "свой", "stroke": "#ffffff", "color": None}},
    })
    pack, _ = await services.create_pack(db, settings, 1, req, 0)

    seen = {}

    class Capture:
        def render(self, spec):
            seen[spec.template_id] = (spec.text, spec.color, spec.stroke)
            return b"TGS"

    await builder(db, settings, FakePublisher(), Capture()).build(pack["id"], 0)
    assert seen == {"classic-001": ("общий", "#112233", None), "classic-003": ("свой", None, "#ffffff")}
    with pytest.raises(services.ValidationFailed):   # свои настройки — только для выбранных шаблонов
        services.validate_pack_request(settings, {"type": "emoji", "template_ids": ["classic-001"],
                                                  "overrides": {"classic-002": {"text": "x"}}})


def test_pack_request_accepts_stroke(tmp_path):
    s = make_settings(tmp_path)
    req = services.validate_pack_request(s, {"type": "emoji", "template_ids": ["classic-001"], "stroke": "#AABBCC"})
    assert req["stroke"] == "#aabbcc" and req["color"] is None
    with pytest.raises(services.ValidationFailed):
        services.validate_pack_request(s, {"type": "emoji", "template_ids": ["classic-001"], "stroke": "black"})
