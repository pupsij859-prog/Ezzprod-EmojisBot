"""Админка в чате (/admin) на настоящем диспетчере aiogram, без сети (сессия бота подменена)."""
from __future__ import annotations

import asyncio
import time

import pytest
from aiogram import Bot
from aiogram.types import Update
from fastapi.testclient import TestClient

from app import runtime, services
from app.bot import TelegramNotifier, create_dispatcher
from app.bot import texts
from app.context import Context
from app.main import create_app
from tests.conftest import dev, make_settings
from tests.test_bot import TOKEN, FakeSession, command, message_update, user

ADMIN = 999


def callback(uid: int, data: str) -> Update:
    msg = {"message_id": 7, "date": int(time.time()), "chat": {"id": uid, "type": "private"}, "text": "экран"}
    return Update.model_validate({"update_id": 2, "callback_query": {
        "id": "cb", "from": user(uid), "chat_instance": "x", "data": data, "message": msg}})


def texts_sent(session) -> list[str]:
    return [getattr(c, "text", "") or "" for c in session.calls if type(c).__name__ in ("SendMessage", "EditMessageText")]


@pytest.fixture
async def env(tmp_path, db):
    settings = make_settings(tmp_path, webapp_url="https://app.example.com", admin_ids=(ADMIN,), demo_mode=False,
                             payments_live=True)
    session = FakeSession()
    bot = Bot(token=TOKEN, session=session)
    ctx = Context(settings=settings, db=db, builder=None, runner=None)
    ctx.bot = bot
    ctx.notifier = TelegramNotifier(bot, ctx)
    ctx.bot_username = "ezzbot"
    await runtime.load(ctx)
    dp = create_dispatcher(ctx)
    for uid in (ADMIN, 5, 6, 7):
        await services.upsert_user(db, settings, {"id": uid, "first_name": f"u{uid}", "username": f"u{uid}"})
    yield dp, bot, ctx, session
    await bot.session.close()


async def test_admin_command_only_for_admins(env):
    dp, bot, ctx, session = env
    await dp.feed_update(bot, command(5, "/admin"))
    assert not any("Админка" in t for t in texts_sent(session))
    await dp.feed_update(bot, command(ADMIN, "/admin"))
    assert any("Админка EzzProd" in t for t in texts_sent(session))
    session.calls.clear()
    await dp.feed_update(bot, callback(5, "a:st:1"))       # чужой не может нажать кнопку админки
    assert not texts_sent(session)


SCREENS = ["a", "a:st:1", "a:st:7", "a:st:0", "a:py:all:0", "a:py:review:0", "a:pk:all:0", "a:pk:failed:0", "a:set",
           "a:sg:price", "a:sg:other", "a:se:price_rub", "a:se:rub_per_star", "a:se:payments_live", "a:pr", "a:tp", "a:ad", "a:sy",
           "a:syl", "a:help", "a:u", "a:gv", "a:uc:5", "a:upk:5", "a:upy:5", "a:ul:5", "a:bc", "a:ex", "a:bk", "a:x"]


@pytest.mark.parametrize("data", SCREENS)
async def test_every_screen_opens(env, data):
    dp, bot, ctx, session = env
    await dp.feed_update(bot, callback(ADMIN, data))
    names = {type(c).__name__ for c in session.calls}
    assert names & {"EditMessageText", "SendMessage", "SendDocument", "DeleteMessage", "AnswerCallbackQuery"}, data


async def test_setting_changes_apply_immediately_and_persist(env, db):
    dp, bot, ctx, session = env
    await dp.feed_update(bot, callback(ADMIN, "a:sx:price_rub"))
    await dp.feed_update(bot, message_update(ADMIN, text="abc"))
    assert ctx.settings.price_rub == 5                             # не число — не сохранили
    await dp.feed_update(bot, message_update(ADMIN, text="9"))
    assert ctx.settings.price_rub == 9
    assert await runtime.kv_get(db, "set:price_rub") == "9"
    # после перезапуска правка подхватывается из базы
    fresh = Context(settings=ctx.base_settings, db=db, builder=None, runner=None)
    await runtime.load(fresh)
    assert fresh.settings.price_rub == 9
    await dp.feed_update(bot, callback(ADMIN, "a:sr:price_rub"))
    assert ctx.settings.price_rub == 5 and "price_rub" not in ctx.overrides


async def test_payments_cannot_be_enabled_in_demo(tmp_path, db):
    ctx = Context(settings=make_settings(tmp_path, demo_mode=True, payments_live=False), db=db, builder=None, runner=None)
    await runtime.load(ctx)
    with pytest.raises(ValueError):
        await runtime.set_setting(ctx, "payments_live", True)
    assert ctx.settings.payments_live is False


async def test_grant_ban_and_maintenance(env, db):
    dp, bot, ctx, session = env
    await dp.feed_update(bot, callback(ADMIN, "a:ug:5:+"))
    await dp.feed_update(bot, message_update(ADMIN, text="7"))
    assert (await services.get_user(db, 5))["balance"] == 8       # 1 подарок + 7
    # бан: бот молчит
    await dp.feed_update(bot, callback(ADMIN, "a:ub:5"))
    assert (await services.get_user(db, 5))["banned"] == 1
    session.calls.clear()
    await dp.feed_update(bot, command(5, "/help"))
    assert not texts_sent(session)
    await dp.feed_update(bot, callback(ADMIN, "a:ub:5"))
    # техработы: обычным — заглушка, админу — как обычно
    await dp.feed_update(bot, callback(ADMIN, "a:mt"))
    assert ctx.maintenance is True
    session.calls.clear()
    await dp.feed_update(bot, command(6, "/help"))
    assert texts_sent(session) == [texts.MAINTENANCE]
    await dp.feed_update(bot, callback(ADMIN, "a:mt"))
    assert ctx.maintenance is False


async def test_promo_codes(env, db):
    dp, bot, ctx, session = env
    await dp.feed_update(bot, callback(ADMIN, "a:prn"))
    await dp.feed_update(bot, message_update(ADMIN, text="ezz 3 1"))
    await dp.feed_update(bot, command(6, "/promo ezz"))
    assert (await services.get_user(db, 6))["balance"] == 4
    await dp.feed_update(bot, command(6, "/promo EZZ"))             # второй раз тот же человек
    await dp.feed_update(bot, command(7, "/promo EZZ"))             # активации закончились
    assert (await services.get_user(db, 6))["balance"] == 4 and (await services.get_user(db, 7))["balance"] == 1
    sent = texts_sent(session)
    assert texts.PROMO_ERRORS["used"] in sent and texts.PROMO_ERRORS["exhausted"] in sent
    assert await services.ledger_mismatches(db) == []


async def test_broadcast_reaches_users_and_skips_banned(env, db):
    dp, bot, ctx, session = env
    await runtime.set_banned(db, 7, True)
    await dp.feed_update(bot, callback(ADMIN, "a:bc"))
    await dp.feed_update(bot, message_update(ADMIN, text="новые шаблоны!"))
    session.calls.clear()
    await dp.feed_update(bot, callback(ADMIN, "a:bg"))
    for _ in range(100):
        if any("Рассылка завершена" in t for t in texts_sent(session)):
            break
        await asyncio.sleep(0.05)
    targets = sorted(c.chat_id for c in session.sent("CopyMessage"))
    assert targets == [5, 6, ADMIN]
    assert any("Доставлено: 3" in t for t in texts_sent(session))


async def test_admin_added_from_chat_gets_access(env):
    dp, bot, ctx, session = env
    await dp.feed_update(bot, callback(ADMIN, "a:adn"))
    await dp.feed_update(bot, message_update(ADMIN, text="@u6"))
    assert runtime.is_admin(ctx, 6)
    session.calls.clear()
    await dp.feed_update(bot, command(6, "/admin"))
    assert any("Админка EzzProd" in t for t in texts_sent(session))


def test_api_respects_ban_maintenance_and_hidden_categories(tmp_path):
    app = create_app(make_settings(tmp_path, admin_ids=(1,)), with_bot=False)
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        ctx = c.app.state.ctx
        assert c.get("/api/me", headers=dev(2)).status_code == 200
        c.portal.call(runtime.set_banned, ctx.db, 2, True)
        assert c.get("/api/me", headers=dev(2)).json()["detail"]["code"] == "banned"
        c.portal.call(runtime.set_maintenance, ctx, True)
        assert c.get("/api/me", headers=dev(3)).status_code == 503
        assert c.get("/api/me", headers=dev(1)).status_code == 200         # админу можно
        c.portal.call(runtime.set_maintenance, ctx, False)
        c.portal.call(runtime.toggle_category, ctx, "flags")
        cats = c.get("/api/categories").json()
        assert "flags" not in {x["id"] for x in cats["categories"]} and cats["total"] == 335
        assert c.get("/api/templates?category=flags").status_code == 404
