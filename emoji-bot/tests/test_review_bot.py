"""Ревью-тесты: бот прогоняется через настоящий Dispatcher aiogram 3.31 с фальшивой сессией (без сети)."""
from __future__ import annotations

import datetime as dt
import logging
import time

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerPreCheckoutQuery, SendMessage
from aiogram.types import Chat, Message, Update, User

from app import services
from app.bot import TelegramNotifier, create_dispatcher
from app.context import Context
from app.packs import MockPackBuilder, PackRunner
from tests.conftest import BOT_TOKEN, make_settings
from tests.test_services import make_user, pack_req


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):  # pragma: no cover
        return None

    async def stream_content(self, *a, **k):  # pragma: no cover
        raise NotImplementedError

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=1, date=dt.datetime.now(), chat=Chat(id=method.chat_id, type="private"), text="x")
        return True


def user(uid):
    return User(id=uid, is_bot=False, first_name="u", username=f"u{uid}")


async def make_ctx(db, settings):
    session = FakeSession()
    bot = Bot(BOT_TOKEN, session=session)
    builder = MockPackBuilder(settings)
    runner = PackRunner(db, settings, builder, None)
    ctx = Context(settings=settings, db=db, builder=builder, runner=runner)
    ctx.bot = bot
    ctx.notifier = runner.notifier = TelegramNotifier(bot, ctx)
    return ctx, bot, session


def pre_checkout_update(uid, payload, amount, currency="XTR"):
    return Update.model_validate({
        "update_id": 1,
        "pre_checkout_query": {
            "id": "q1", "from": {"id": uid, "is_bot": False, "first_name": "u"},
            "currency": currency, "total_amount": amount, "invoice_payload": payload,
        },
    })


def paid_update(uid, payload, amount, charge):
    return Update.model_validate({
        "update_id": 2,
        "message": {
            "message_id": 5, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "is_bot": False, "first_name": "u"},
            "successful_payment": {
                "currency": "XTR", "total_amount": amount, "invoice_payload": payload,
                "telegram_payment_charge_id": charge, "provider_payment_charge_id": "",
            },
        },
    })


def text_update(uid, text):
    return Update.model_validate({
        "update_id": 3,
        "message": {
            "message_id": 6, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "is_bot": False, "first_name": "u"}, "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": text.split()[0].__len__()}],
        },
    })


async def test_pre_checkout_rejects_foreign_payer_and_accepts_owner(db, tmp_path):
    s = make_settings(tmp_path, signup_gens=0, demo_mode=False, payments_live=True)
    await make_user(db, s, 1)
    pid = await services.create_payment(db, 1, 10, 60)
    ctx, bot, session = await make_ctx(db, s)
    dp = create_dispatcher(ctx)

    await dp.feed_update(bot, pre_checkout_update(2, pid, 60))      # другой человек оплачивает чужой счёт
    await dp.feed_update(bot, pre_checkout_update(1, pid, 60))      # владелец
    await dp.feed_update(bot, pre_checkout_update(1, pid, 61))      # сумма не совпала
    answers = [c for c in session.calls if isinstance(c, AnswerPreCheckoutQuery)]
    assert [a.ok for a in answers] == [False, True, False]


async def test_successful_payment_is_credited_once_through_dispatcher(db, tmp_path):
    s = make_settings(tmp_path, signup_gens=0)
    await make_user(db, s, 1)
    pid = await services.create_payment(db, 1, 10, 60)
    ctx, bot, session = await make_ctx(db, s)
    dp = create_dispatcher(ctx)

    await dp.feed_update(bot, paid_update(1, pid, 60, "ch1"))
    await dp.feed_update(bot, paid_update(1, pid, 60, "ch1"))  # повторная доставка апдейта
    assert (await services.get_user(db, 1))["balance"] == 10
    assert len([c for c in session.calls if isinstance(c, SendMessage)]) == 1  # одно «оплата прошла»


async def test_successful_payment_from_unknown_user_does_not_crash(db, tmp_path):
    """Платёж от пользователя, которого нет в БД (открыл чужую ссылку): исключения быть не должно."""
    s = make_settings(tmp_path, signup_gens=0)
    await make_user(db, s, 1)
    pid = await services.create_payment(db, 1, 10, 60)
    ctx, bot, session = await make_ctx(db, s)
    dp = create_dispatcher(ctx)
    await dp.feed_update(bot, paid_update(999, pid, 60, "chZ"))
    ev = await db.fetchone("SELECT status FROM payment_events WHERE charge_id='chZ'")
    assert ev["status"] == "needs_review"


async def test_refund_command_is_silent_for_non_admins(db, tmp_path):
    s = make_settings(tmp_path, signup_gens=0, admin_ids=(777,))
    await make_user(db, s, 1)
    ctx, bot, session = await make_ctx(db, s)
    dp = create_dispatcher(ctx)
    await dp.feed_update(bot, text_update(1, "/refund whatever"))
    assert session.calls == []  # обычному пользователю бот не отвечает вообще


async def test_refund_command_failed_telegram_call_reverts(db, tmp_path):
    from aiogram.methods import RefundStarPayment

    s = make_settings(tmp_path, signup_gens=0, admin_ids=(777,))
    await make_user(db, s, 1)
    pid = await services.create_payment(db, 1, 10, 60)
    await services.settle_payment(db, s, pid, "chR", 60, 1)
    ctx, bot, session = await make_ctx(db, s)

    original = session.make_request

    async def fail_refund(bot_, method, timeout=None):
        if isinstance(method, RefundStarPayment):
            session.calls.append(method)
            raise RuntimeError("network down")
        return await original(bot_, method, timeout)

    session.make_request = fail_refund
    dp = create_dispatcher(ctx)
    await dp.feed_update(bot, text_update(777, "/refund chR"))
    assert (await services.get_user(db, 1))["balance"] == 10  # генерации вернулись
    assert any(isinstance(c, RefundStarPayment) for c in session.calls)


# ───────────────────────── логи и токен ─────────────────────────

@pytest.mark.xfail(strict=True, reason="P1: RedactTokens чистит только msg/args; токен в тексте traceback (exc_info) "
                                       "проходит в лог как есть")
def test_token_is_redacted_in_tracebacks():
    import io

    from app.__main__ import RedactTokens

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactTokens())
    handler.setFormatter(logging.Formatter("%(message)s"))
    log = logging.getLogger("review.token")
    log.propagate = False
    log.addHandler(handler)
    token = "123456789:AAH-abcdefghijklmnopqrstuvwxyz012345"
    try:
        raise RuntimeError(f"404, message='Not Found', url='https://api.telegram.org/bot{token}/getMe'")
    except RuntimeError:
        log.exception("сбой запроса к Telegram")
    assert token not in stream.getvalue()


def test_token_is_redacted_in_plain_messages():
    import io

    from app.__main__ import RedactTokens

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactTokens())
    log = logging.getLogger("review.token2")
    log.propagate = False
    log.addHandler(handler)
    token = "123456789:AAH-abcdefghijklmnopqrstuvwxyz012345"
    log.error("url https://api.telegram.org/bot%s/getMe", token)
    log.error(f"url https://api.telegram.org/bot{token}/getMe")
    assert token not in stream.getvalue()
