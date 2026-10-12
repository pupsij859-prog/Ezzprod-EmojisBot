"""Бот на настоящем диспетчере aiogram, но без сети: сессия подменена и записывает вызовы Bot API."""
from __future__ import annotations

import time

import pytest
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import Message, Update

from app import services
from app.bot import TelegramNotifier, create_dispatcher
from app.context import Context
from tests.conftest import make_settings

TOKEN = "123456:TEST-TOKEN-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class FakeSession(AiohttpSession):
    def __init__(self, refund_ok: bool = True):
        super().__init__()
        self.calls: list = []
        self.refund_ok = refund_ok

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        name = type(method).__name__
        if name in ("SendMessage", "SendPhoto"):
            body = {"message_id": 1, "date": int(time.time()), "chat": {"id": getattr(method, "chat_id", 1), "type": "private"}}
            if name == "SendPhoto":
                body["photo"] = [{"file_id": "banner-file-id", "file_unique_id": "u", "width": 1280, "height": 720}]
            return Message.model_validate(body)
        if name == "RefundStarPayment":
            return self.refund_ok
        return True

    def sent(self, kind: str) -> list:
        return [c for c in self.calls if type(c).__name__ == kind]


def user(uid: int, name: str = "Аня") -> dict:
    return {"id": uid, "is_bot": False, "first_name": name, "username": f"u{uid}"}


def message_update(uid: int, **extra) -> Update:
    base = {"message_id": 1, "date": int(time.time()), "chat": {"id": uid, "type": "private"}, "from": user(uid)}
    base.update(extra)
    return Update.model_validate({"update_id": 1, "message": base})


def command(uid: int, text: str) -> Update:
    cmd_len = len(text.split()[0])
    return message_update(uid, text=text, entities=[{"type": "bot_command", "offset": 0, "length": cmd_len}])


@pytest.fixture
async def env(tmp_path, db):
    settings = make_settings(tmp_path, webapp_url="https://app.example.com", admin_ids=(999,),
                             support_username="help_ez", demo_mode=False, payments_live=True)
    session = FakeSession()
    bot = Bot(token=TOKEN, session=session)
    ctx = Context(settings=settings, db=db, builder=None, runner=None)
    ctx.bot = bot
    ctx.notifier = TelegramNotifier(bot, ctx)
    ctx.bot_username = "ezzbot"
    dp = create_dispatcher(ctx)
    yield dp, bot, ctx, session, settings
    await bot.session.close()


async def test_start_sends_banner_with_webapp_button_and_registers_referral(env, db):
    dp, bot, ctx, session, settings = env
    inviter, _ = await services.upsert_user(db, settings, {"id": 1, "first_name": "Boss"})
    await dp.feed_update(bot, command(5, f"/start ref_{inviter['ref_code']}"))

    photos = session.sent("SendPhoto")
    assert len(photos) == 1
    button = photos[0].reply_markup.inline_keyboard[0][0]
    assert button.text == "открыть редактор" and button.web_app.url == "https://app.example.com"
    assert ctx.banner_file_id == "banner-file-id"      # дальше баннер шлётся по file_id
    assert (await services.get_user(db, 5))["referred_by"] == 1

    await dp.feed_update(bot, command(6, "/start"))
    assert session.sent("SendPhoto")[1].photo == "banner-file-id"


async def test_start_ignores_garbage_ref_param(env, db):
    dp, bot, ctx, session, settings = env
    await dp.feed_update(bot, command(7, "/start ref_<script>"))
    assert (await services.get_user(db, 7))["referred_by"] is None


async def test_start_without_https_webapp_explains_what_is_missing(tmp_path, db):
    settings = make_settings(tmp_path, webapp_url="")
    session = FakeSession()
    bot = Bot(token=TOKEN, session=session)
    ctx = Context(settings=settings, db=db, builder=None, runner=None)
    dp = create_dispatcher(ctx)
    await dp.feed_update(bot, command(8, "/start"))
    assert "WEBAPP_URL" in session.sent("SendMessage")[0].text
    await bot.session.close()


async def test_payment_flow_pre_checkout_then_credit(env, db):
    dp, bot, ctx, session, settings = env
    await services.upsert_user(db, settings, {"id": 5, "first_name": "A"})
    pid = await services.create_payment(db, 5, 20, 120)

    def pre_checkout(amount, uid=5, payload=pid):
        return Update.model_validate({"update_id": 2, "pre_checkout_query": {
            "id": "q1", "from": user(uid), "currency": "XTR", "total_amount": amount, "invoice_payload": payload}})

    await dp.feed_update(bot, pre_checkout(120))
    await dp.feed_update(bot, pre_checkout(119))              # неверная сумма
    await dp.feed_update(bot, pre_checkout(120, uid=6))       # чужой пользователь
    answers = session.sent("AnswerPreCheckoutQuery")
    assert [a.ok for a in answers] == [True, False, False]

    paid = message_update(5, successful_payment={"currency": "XTR", "total_amount": 120, "invoice_payload": pid,
                                                 "telegram_payment_charge_id": "charge-1", "provider_payment_charge_id": ""})
    await dp.feed_update(bot, paid)
    await dp.feed_update(bot, paid)                           # повторная доставка того же апдейта
    assert (await services.get_user(db, 5))["balance"] == 21
    assert len(session.sent("SendMessage")) == 1 and "+20" in session.sent("SendMessage")[0].text


async def test_unmatched_payment_alerts_admin_and_does_not_credit(env, db):
    dp, bot, ctx, session, settings = env
    await services.upsert_user(db, settings, {"id": 5, "first_name": "A"})
    bad = message_update(5, successful_payment={"currency": "XTR", "total_amount": 50, "invoice_payload": "nope",
                                                "telegram_payment_charge_id": "charge-x", "provider_payment_charge_id": ""})
    await dp.feed_update(bot, bad)
    assert (await services.get_user(db, 5))["balance"] == 1
    recipients = [m.chat_id for m in session.sent("SendMessage")]
    assert 999 in recipients and 5 in recipients               # админу алерт, пользователю объяснение


async def test_payments_can_be_disabled_at_pre_checkout(tmp_path, db):
    settings = make_settings(tmp_path, payments_live=False, webapp_url="https://app.example.com")
    session = FakeSession()
    bot = Bot(token=TOKEN, session=session)
    ctx = Context(settings=settings, db=db, builder=None, runner=None)
    dp = create_dispatcher(ctx)
    await services.upsert_user(db, settings, {"id": 5, "first_name": "A"})
    pid = await services.create_payment(db, 5, 20, 120)
    await dp.feed_update(bot, Update.model_validate({"update_id": 2, "pre_checkout_query": {
        "id": "q", "from": user(5), "currency": "XTR", "total_amount": 120, "invoice_payload": pid}}))
    assert session.sent("AnswerPreCheckoutQuery")[0].ok is False
    await bot.session.close()


async def test_refund_is_admin_only_and_works(env, db):
    dp, bot, ctx, session, settings = env
    await services.upsert_user(db, settings, {"id": 5, "first_name": "A"})
    pid = await services.create_payment(db, 5, 20, 120)
    await services.settle_payment(db, settings, pid, "charge-1", 120, 5)

    await dp.feed_update(bot, command(5, "/refund charge-1"))              # обычный пользователь: молчание
    assert session.sent("RefundStarPayment") == [] and session.sent("SendMessage") == []

    await dp.feed_update(bot, command(999, "/refund charge-1"))
    refund = session.sent("RefundStarPayment")[0]
    assert (refund.user_id, refund.telegram_payment_charge_id) == (5, "charge-1")
    assert (await services.get_user(db, 5))["balance"] == 1
    status = (await db.fetchone("SELECT status FROM payment_events WHERE charge_id='charge-1'"))["status"]
    assert status == "refunded"


async def test_refund_rolled_back_if_telegram_refuses(tmp_path, db):
    settings = make_settings(tmp_path, admin_ids=(999,))
    session = FakeSession(refund_ok=False)
    bot = Bot(token=TOKEN, session=session)
    ctx = Context(settings=settings, db=db, builder=None, runner=None)
    ctx.bot = bot
    dp = create_dispatcher(ctx)
    await services.upsert_user(db, settings, {"id": 5, "first_name": "A"})
    pid = await services.create_payment(db, 5, 20, 120)
    await services.settle_payment(db, settings, pid, "charge-1", 120, 5)
    await dp.feed_update(bot, command(999, "/refund charge-1"))
    assert (await services.get_user(db, 5))["balance"] == 21                # генерации вернулись
    status = (await db.fetchone("SELECT status FROM payment_events WHERE charge_id='charge-1'"))["status"]
    assert status == "credited"
    await bot.session.close()


async def test_payment_commands_answer(env):
    dp, bot, ctx, session, settings = env
    for cmd in ("/terms", "/support", "/paysupport"):
        await dp.feed_update(bot, command(5, cmd))
    texts = [m.text for m in session.sent("SendMessage")]
    assert "условия" in texts[0] and "https://app.example.com/docs/terms.html" in texts[0]
    assert "@help_ez" in texts[1] and "@help_ez" in texts[2]
