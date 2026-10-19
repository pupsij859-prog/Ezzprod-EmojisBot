"""Обязательная подписка: настоящий диспетчер и HTTP API без сети Telegram."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetChatMember

from app import runtime, services, subscription
from app.bot import texts
from app.context import Context
from tests.conftest import dev
from tests.test_admin_chat import ADMIN, callback, env, texts_sent  # noqa: F401
from tests.test_bot import command, message_update


async def enable(ctx):
    await runtime.set_setting(ctx, "subscription_channel", "@ezz_channel")
    await runtime.set_setting(ctx, "subscription_required", True)


def membership(monkeypatch, session, status="left", *, is_member=False, error=False):
    original = session.make_request

    async def request(bot, method, timeout=None):
        if isinstance(method, GetChatMember):
            session.calls.append(method)
            if error:
                raise TelegramNetworkError(method=method, message="offline")
            return SimpleNamespace(status=status, is_member=is_member)
        return await original(bot, method, timeout)

    monkeypatch.setattr(session, "make_request", request)


async def test_start_shows_two_buttons_and_keeps_referral(env, monkeypatch):
    dp, bot, ctx, session = env
    await enable(ctx)
    membership(monkeypatch, session)
    inviter, _ = await services.upsert_user(ctx.db, ctx.settings, {"id": 20})
    await dp.feed_update(bot, command(50, f"/start ref_{inviter['ref_code']}"))
    sent = session.sent("SendMessage")[-1]
    buttons = [b for row in sent.reply_markup.inline_keyboard for b in row]
    assert len(buttons) == 2
    assert buttons[0].url == "https://t.me/ezz_channel"
    assert buttons[1].callback_data == "subscription:check"
    assert all(b.web_app is None for b in buttons)
    assert not session.sent("SendRichMessage")
    assert (await services.get_user(ctx.db, 50))["referred_by"] == 20


@pytest.mark.parametrize("status,is_member,allowed", [
    ("member", False, True), ("administrator", False, True), ("creator", False, True),
    ("restricted", True, True), ("restricted", False, False), ("left", False, False), ("kicked", False, False),
])
async def test_check_button_uses_real_membership(env, monkeypatch, status, is_member, allowed):
    dp, bot, ctx, session = env
    await enable(ctx)
    membership(monkeypatch, session, status, is_member=is_member)
    await dp.feed_update(bot, callback(5, "subscription:check"))
    assert session.sent("GetChatMember")[-1].user_id == 5
    if allowed:
        sent = session.sent("SendMessage")[-1]
        assert sent.text == texts.get_custom_text(ctx, "subscription_ok")
        assert sent.reply_markup.inline_keyboard[0][0].web_app.url == ctx.webapp_url
    else:
        assert not session.sent("SendMessage")
        assert session.sent("AnswerCallbackQuery")[-1].text == texts.get_custom_text(ctx, "subscription_missing")


async def test_unsubscribe_blocks_again_and_disabled_gate_allows_old_button(env, monkeypatch):
    dp, bot, ctx, session = env
    await enable(ctx)
    membership(monkeypatch, session, "member")
    await dp.feed_update(bot, command(5, "/start"))
    assert len(session.sent("SendRichMessage")) == 1
    membership(monkeypatch, session, "left")
    await dp.feed_update(bot, command(5, "/help"))
    assert session.sent("SendMessage")[-1].reply_markup.inline_keyboard[1][0].callback_data == "subscription:check"
    await runtime.set_setting(ctx, "subscription_required", False)
    session.calls.clear()
    await dp.feed_update(bot, callback(5, "subscription:check"))
    assert not session.sent("GetChatMember")
    assert session.sent("SendMessage")[-1].reply_markup.inline_keyboard[0][0].web_app


async def test_error_does_not_grant_access_and_support_still_works(env, monkeypatch):
    dp, bot, ctx, session = env
    await enable(ctx)
    membership(monkeypatch, session, error=True)
    await dp.feed_update(bot, command(5, "/start"))
    assert texts.get_custom_text(ctx, "subscription_error") in texts_sent(session)[-1]
    assert not session.sent("SendRichMessage")
    await dp.feed_update(bot, callback(5, "subscription:check"))
    assert session.sent("AnswerCallbackQuery")[-1].show_alert
    for text in ("/support", "/paysupport", "/terms"):
        await dp.feed_update(bot, command(5, text))
    assert len(session.sent("GetChatMember")) == 2
    await dp.feed_update(bot, command(ADMIN, "/start"))
    assert len(session.sent("GetChatMember")) == 2
    assert session.sent("SendRichMessage")


async def test_promo_cannot_bypass_gate_and_paid_stars_are_credited(env, monkeypatch):
    dp, bot, ctx, session = env
    await enable(ctx)
    membership(monkeypatch, session)
    await runtime.create_promo(ctx.db, "SUBTEST", 5, 10, ADMIN)
    await dp.feed_update(bot, command(5, "/promo SUBTEST"))
    assert (await services.get_user(ctx.db, 5))["balance"] == 1
    pid = await services.create_payment(ctx.db, 5, 20, 120)
    await dp.feed_update(bot, message_update(5, successful_payment={
        "currency": "XTR", "total_amount": 120, "invoice_payload": pid,
        "telegram_payment_charge_id": "sub-charge", "provider_payment_charge_id": "",
    }))
    assert (await services.get_user(ctx.db, 5))["balance"] == 21
    assert len(session.sent("GetChatMember")) == 1


async def test_callback_respects_ban_and_maintenance(env, monkeypatch):
    dp, bot, ctx, session = env
    await enable(ctx)
    membership(monkeypatch, session, "member")
    await runtime.set_banned(ctx.db, 5, True)
    await dp.feed_update(bot, callback(5, "subscription:check"))
    await runtime.set_banned(ctx.db, 5, False)
    await runtime.set_maintenance(ctx, True)
    await dp.feed_update(bot, callback(5, "subscription:check"))
    assert not session.sent("GetChatMember")
    assert not session.sent("SendMessage")


async def test_admin_configures_and_persists_gate_and_texts(env):
    dp, bot, ctx, session = env
    await dp.feed_update(bot, callback(ADMIN, "a:sub"))
    assert "Обязательная подписка" in texts_sent(session)[-1]
    await dp.feed_update(bot, callback(ADMIN, "a:sb:subscription_required"))
    assert not ctx.settings.subscription_required
    assert "сначала укажи канал" in texts_sent(session)[-1]
    await dp.feed_update(bot, callback(ADMIN, "a:sx:subscription_channel"))
    await dp.feed_update(bot, message_update(ADMIN, text="https://t.me/ezz_channel"))
    assert ctx.settings.subscription_channel == "@ezz_channel"
    await dp.feed_update(bot, callback(ADMIN, "a:sb:subscription_required"))
    assert ctx.settings.subscription_required
    for key, value in (("subscription", "Подпишись на {channel}!"), ("subscription_join", "Наш канал")):
        await dp.feed_update(bot, callback(ADMIN, f"a:msge:{key}"))
        await dp.feed_update(bot, message_update(ADMIN, text=value))
    await dp.feed_update(bot, callback(ADMIN, "a:subp"))
    sent = session.sent("SendMessage")[-1]
    assert sent.text == "Подпишись на @ezz_channel!"
    assert sent.reply_markup.inline_keyboard[0][0].text == "Наш канал"
    reloaded = Context(settings=runtime.base(ctx), db=ctx.db, builder=None, runner=None)
    await runtime.load(reloaded)
    assert reloaded.settings.subscription_required
    assert reloaded.settings.subscription_channel == "@ezz_channel"
    assert reloaded.custom_texts["subscription_join"] == "Наш канал"
    await dp.feed_update(bot, callback(ADMIN, "a:msgr:subscription_join"))
    assert "subscription_join" not in ctx.custom_texts


async def test_private_channel_and_invalid_config(env):
    _, _, ctx, _ = env
    with pytest.raises(ValueError):
        await runtime.set_setting(ctx, "subscription_required", True)
    await runtime.set_setting(ctx, "subscription_channel", "-1001234567890")
    with pytest.raises(ValueError):
        await runtime.set_setting(ctx, "subscription_required", True)
    url = "https://t.me/+join_123-abc"
    await runtime.set_setting(ctx, "subscription_url", runtime.parse(runtime.BY_KEY["subscription_url"], url))
    await runtime.set_setting(ctx, "subscription_required", True)
    assert subscription.channel_id(ctx.settings) == -1001234567890
    assert subscription.channel_url(ctx.settings) == url
    with pytest.raises(ValueError):
        await runtime.set_setting(ctx, "subscription_url", "")
    for raw in ("https://evil.test/channel", "https://t.me.evil.test/channel", "javascript:alert(1)"):
        with pytest.raises(ValueError):
            runtime.parse(runtime.BY_KEY["subscription_url"], raw)


def test_mini_app_cannot_bypass_subscription(client):
    ctx = client.app_state.ctx
    ctx.settings = replace(ctx.settings, subscription_required=True, subscription_channel="@ezz_channel")
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status="left")))
    ctx.bot = bot
    blocked = client.get("/api/me", headers=dev())
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["channel_url"] == "https://t.me/ezz_channel"
    assert client.post("/api/styles", headers=dev(), json={"name": "test", "params": {}}).status_code == 403
    bot.get_chat_member.return_value = SimpleNamespace(status="member")
    assert client.get("/api/me", headers=dev()).status_code == 200
    bot.get_chat_member.return_value = SimpleNamespace(status="left")
    assert client.get("/api/me", headers=dev()).status_code == 403
    ctx.bot = None
    assert client.get("/api/me", headers=dev()).status_code == 503
    ctx.settings = replace(ctx.settings, admin_ids=(1001,))
    assert client.get("/api/me", headers=dev()).status_code == 200


def test_mini_app_network_error_is_retryable(client):
    ctx = client.app_state.ctx
    ctx.settings = replace(ctx.settings, subscription_required=True, subscription_channel="@ezz_channel")
    ctx.bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=TelegramNetworkError(
        method=GetChatMember(chat_id="@ezz_channel", user_id=1001), message="offline",
    )))
    response = client.get("/api/me", headers=dev())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "subscription_unavailable"
