"""Промокоды v2, ссылки кошельков (мини-приложение вместо чата) и положение надписи."""
from __future__ import annotations

from dataclasses import replace


import pytest

from app import lottie, runtime, services
from tests.conftest import dev, make_settings


# ───────────────────────── промокоды ─────────────────────────

async def _user(db, settings, uid):
    await services.upsert_user(db, settings, {"id": uid, "first_name": f"u{uid}"})


async def test_promo_random_reward_and_limits(tmp_path, db):
    s = make_settings(tmp_path, signup_gens=0)
    for uid in (1, 2, 3):
        await _user(db, s, uid)
    [code] = await runtime.create_promos(db, runtime.PromoDraft(code="LUCKY", gens=5, gens_max=9, uses=2), by=1)
    got, balance = await runtime.redeem_promo(db, 1, "lucky")
    assert 5 <= got <= 9 and balance == got
    with pytest.raises(runtime.PromoError) as e:
        await runtime.redeem_promo(db, 1, code)
    assert e.value.reason == "used"
    await runtime.redeem_promo(db, 2, code)
    with pytest.raises(runtime.PromoError) as e:
        await runtime.redeem_promo(db, 3, code)
    assert e.value.reason == "exhausted"
    rows = await db.fetchall("SELECT gens FROM promo_uses WHERE code=?", (code,))
    assert all(5 <= r["gens"] <= 9 for r in rows)
    assert await services.ledger_mismatches(db) == []


async def test_promo_pause_expiry_and_only_new(tmp_path, db):
    s = make_settings(tmp_path)
    for uid in (1, 2):
        await _user(db, s, uid)
    async with db.tx() as t:
        await t.execute("UPDATE users SET purchased_total=10 WHERE id=2")
    [code] = await runtime.create_promos(db, runtime.PromoDraft(gens=3, uses=10, only_new=True, prefix="ezz"), by=1)
    assert code.startswith("EZZ") and len(code) == 11
    with pytest.raises(runtime.PromoError) as e:
        await runtime.redeem_promo(db, 2, code)   # уже покупал
    assert e.value.reason == "not_new"
    await runtime.set_promo_active(db, code, False)
    with pytest.raises(runtime.PromoError) as e:
        await runtime.redeem_promo(db, 1, code)
    assert e.value.reason == "paused"
    await runtime.set_promo_active(db, code, True)
    async with db.tx() as t:
        await t.execute("UPDATE promos SET expires_at=1 WHERE code=?", (code,))
    with pytest.raises(runtime.PromoError) as e:
        await runtime.redeem_promo(db, 1, code)
    assert e.value.reason == "expired"
    assert runtime.promo_state(await runtime.get_promo(db, code)) == "expired"


async def test_promo_batch_and_validation(db):
    codes = await runtime.create_promos(db, runtime.PromoDraft(gens=1, uses=1, count=25, days=7), by=1)
    assert len(set(codes)) == 25
    rows = await db.fetchall("SELECT DISTINCT batch, expires_at FROM promos")
    assert len(rows) == 1 and rows[0]["batch"] and rows[0]["expires_at"] > services.now()
    for bad in (runtime.PromoDraft(code="X"), runtime.PromoDraft(gens=5, gens_max=5),
                runtime.PromoDraft(code="ABC", count=2), runtime.PromoDraft(uses=0)):
        with pytest.raises(ValueError):
            await runtime.create_promos(db, bad, by=1)


def test_promo_api(client):
    ctx = client.app_state.ctx
    client.get("/api/me", headers=dev(700))
    code =client.portal.call(runtime.create_promo, ctx.db, "WEB10", 10, 5, 1)
    r = client.post("/api/promo", json={"code": "web10"}, headers=dev(700))
    assert r.status_code == 200 and r.json()["gens"] == 10
    r = client.post("/api/promo", json={"code": code}, headers=dev(700))
    assert r.status_code == 400 and r.json()["detail"] == {"code": "promo", "reason": "used"}
    r = client.post("/api/promo", json={"code": "NOPE"}, headers=dev(700))
    assert r.json()["detail"]["reason"] == "unknown"
    for _ in range(10):  # перебор кодов упирается в лимит
        r = client.post("/api/promo", json={"code": "NOPE"}, headers=dev(700))
    assert r.status_code == 429


# ───────────────────────── ссылки кошельков ─────────────────────────

def test_wallet_links_open_mini_app():
    from app import payments

    assert payments.tg_app_link("https://t.me/xrocket?start=inv_AbC12") == "https://t.me/xrocket/app?startapp=inv_AbC12"
    assert payments.tg_app_link("https://example.com/x") == "https://example.com/x"
    assert payments.wallet_app_link({"telegramBotLink": "https://t.me/xrocket?start=inv_1"}) == "https://t.me/xrocket/app?startapp=inv_1"
    assert payments.wallet_app_link({"telegramBotLink": "https://t.me/xrocket?start=inv_1",
                                     "telegramMiniAppLink": "https://t.me/xrocket/app?startapp=x"}) == "https://t.me/xrocket/app?startapp=x"


# ───────────────────────── положение надписи ─────────────────────────

def test_clean_place():
    assert lottie.clean_place(None) is None
    assert lottie.clean_place({"x": 0, "y": 0, "r": 0}) is None
    assert lottie.clean_place({"x": 500, "y": -12.4, "r": -400}) == {"x": 100, "y": -12, "r": -180}
    for bad in ({"z": 1}, {"x": "1"}, {"x": float("nan")}, [1, 2]):
        with pytest.raises(ValueError):
            lottie.clean_place(bad)


def test_place_moves_and_rotates_text():
    from app import catalog, fonts

    tpl = catalog.get("classic-001")
    anim = lottie.load(tpl.path)
    text = fonts.text_drawing("spuff")
    plain = lottie.personalize(anim, color=None, text=text, logo=None)
    moved = lottie.personalize(anim, color=None, text=text, logo=None, place={"x": 20, "y": -10, "r": 15})

    def wrappers(o, out):
        if isinstance(o, dict):
            if o.get("nm") == "ezz text":
                out.append(o)
            for v in o.values():
                wrappers(v, out)
        elif isinstance(o, list):
            for v in o:
                wrappers(v, out)
        return out

    assert wrappers(plain, []) == []
    found = wrappers(moved, [])
    assert found
    tr = found[0]["it"][-1]
    ax, ay = tr["a"]["k"]
    px, py = tr["p"]["k"]
    assert tr["r"]["k"] == 15 and px > ax and py < ay
    assert len(lottie.encode(moved)) <= lottie.TG_LIMIT


def test_place_and_scale_template_letters():
    from app import catalog

    tpl = catalog.get("classic-001")
    anim = lottie.load(tpl.path)
    plain = lottie.personalize(anim, color=None, text=None, logo=None)
    moved = lottie.personalize(anim, color=None, text=None, logo=None, place={"x": 20, "y": -10, "r": 15}, size=1.4)

    def wrappers(o, out):
        if isinstance(o, dict):
            if o.get("nm") == "ezz text":
                out.append(o)
            for v in o.values():
                wrappers(v, out)
        elif isinstance(o, list):
            for v in o:
                wrappers(v, out)
        return out

    assert wrappers(plain, []) == []
    found = wrappers(moved, [])
    assert found
    tr = found[0]["it"][-1]
    ax, ay = tr["a"]["k"]
    px, py = tr["p"]["k"]
    assert tr["r"]["k"] == 15 and px > ax and py < ay
    assert tr["s"]["k"] == [140.0, 140.0]
    assert len(lottie.encode(moved)) <= lottie.TG_LIMIT


def test_pack_request_keeps_place(tmp_path):
    s = make_settings(tmp_path)
    req = services.validate_pack_request(s, {"type": "emoji", "template_ids": ["classic-001"], "place": {"x": 10, "y": 0, "r": 0},
                                             "overrides": {"classic-001": {"place": None}}})
    assert req["place"] == {"x": 10, "y": 0, "r": 0} and req["overrides"] == {"classic-001": {"place": None}}
    with pytest.raises(services.ValidationFailed):
        services.validate_pack_request(s, {"type": "emoji", "template_ids": ["classic-001"], "place": {"q": 1}})


# ───────────────────────── обязательная подписка: кэш и сбои ─────────────────────────

def test_subscription_cache_and_transient_errors(client):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import GetChatMember

    ctx = client.app_state.ctx
    ctx.settings = replace(ctx.settings, subscription_required=True, subscription_channel="@ezz_channel")
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")))
    ctx.bot = bot
    h = dev(730)
    assert client.get("/api/me", headers=h).status_code == 200            # старт приложения — свежая проверка
    calls = bot.get_chat_member.await_count
    for _ in range(5):                                                     # обычные запросы — из кэша
        assert client.get("/api/packs", headers=h).status_code == 200
    assert bot.get_chat_member.await_count == calls
    # Telegram на миг не ответил: недавно подписанного не выкидывает на экран подписки
    bot.get_chat_member.side_effect = TelegramNetworkError(method=GetChatMember(chat_id="@ezz_channel", user_id=730), message="429")
    assert client.get("/api/me", headers=h).status_code == 200
    # а того, кого ещё не проверяли, сбой не пускает
    assert client.get("/api/me", headers=dev(731)).status_code == 503
    ctx.bot = None
    ctx.settings = replace(ctx.settings, subscription_required=False)


def test_subscription_alerts_admin_when_bot_lost_rights(client):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import GetChatMember

    ctx = client.app_state.ctx
    ctx.settings = replace(ctx.settings, subscription_required=True, subscription_channel="@ezz_channel")
    ctx.bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=TelegramBadRequest(
        method=GetChatMember(chat_id="@ezz_channel", user_id=740), message="member list is inaccessible")))
    alerts = []

    class Notes:
        async def admin_alert(self, text):
            alerts.append(text)

    old = ctx.notifier
    ctx.notifier = Notes()
    assert client.get("/api/me", headers=dev(740)).status_code == 503
    assert client.get("/api/me", headers=dev(741)).status_code == 503
    ctx.notifier = old
    ctx.bot = None
    ctx.settings = replace(ctx.settings, subscription_required=False)


async def test_promo_discount_percent(tmp_path, db):
    from app import pricing

    s = make_settings(tmp_path, signup_gens=0)
    await _user(db, s, 501)
    # Создаём промокод со скидкой 20%
    [code] = await runtime.create_promos(db, runtime.PromoDraft(code="SALE20", gens=0, discount_pct=20, uses=3), by=1)
    res = await runtime.redeem_promo(db, 501, code)
    assert res.gens == 0
    assert res.discount_pct == 20
    user = await db.fetchone("SELECT discount_pct FROM users WHERE id=501")
    assert user["discount_pct"] == 20

    # Проверяем пересчёт цены
    q_base = pricing.quote(s, 20)
    q_disc = pricing.quote(s, 20, user_discount_pct=user["discount_pct"])
    assert q_disc.total == round(q_base.total * 0.8)
    assert q_disc.total < q_base.total

    # Оплата сбрасывает одноразовую скидку
    pid = await services.create_payment(db, 501, 20, q_disc.stars, method="stars", amount_rub=q_disc.total)
    await services.settle_payment(db, s, pid, "ch_sale20", q_disc.stars, 501, currency="XTR", provider="stars")
    user_after = await db.fetchone("SELECT discount_pct, purchased_total FROM users WHERE id=501")
    assert user_after["discount_pct"] == 0
    assert user_after["purchased_total"] == 20
