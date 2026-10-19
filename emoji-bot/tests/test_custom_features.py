import hashlib
import hmac
import json
import pytest
from app import payments, runtime, services
from app.bot import texts
from app.context import Context
from app.db import Database


@pytest.mark.asyncio
async def test_custom_texts_rendering_and_safe_dict():
    # SafeDict tests
    sd = texts.SafeDict(brand="MyBrand")
    assert "{brand} bot {unknown}".format_map(sd) == "MyBrand bot {unknown}"

    # Default render
    res = texts.help_text("EzzProd")
    assert "EzzProd" in res

    # Custom override in Context
    ctx = Context(settings=None, db=None, builder=None, runner=None)
    ctx.custom_texts["start"] = "Добро пожаловать в {brand}! Крутой бот."
    res = texts.help_text("EzzProd", ctx=ctx)
    assert res == "Добро пожаловать в EzzProd! Крутой бот."

    # Custom override with Telegram Premium emoji
    ctx.custom_texts["pack_ready"] = '<tg-emoji emoji-id="5463297803235113601">✨</tg-emoji> Пак {name} готов!{details}'
    res = texts.pack_ready("Cool<Pack>", False, ctx=ctx)
    assert '<tg-emoji emoji-id="5463297803235113601">✨</tg-emoji>' in res
    assert "Cool&lt;Pack&gt;" in res


@pytest.mark.asyncio
async def test_custom_texts_db_persistence(tmp_path):
    db_file = tmp_path / "test.db"
    db = Database(db_file)
    await db.connect()

    try:
        # Check initial
        loaded = await runtime.load_custom_texts(db)
        assert loaded == {}

        # Save custom text
        await runtime.set_custom_text(db, "start", "Привет от {brand}")
        loaded = await runtime.load_custom_texts(db)
        assert loaded == {"start": "Привет от {brand}"}

        # Reset custom text
        await runtime.set_custom_text(db, "start", None)
        loaded = await runtime.load_custom_texts(db)
        assert loaded == {}
    finally:
        await db.close()


def test_payment_webhook_signatures():
    # CryptoBot signature verification
    cb = payments.CryptoBotProvider(token="test_cb_token", net="testnet")
    body = b'{"update_id": 1, "update_type": "invoice_paid"}'
    secret = hashlib.sha256(b"test_cb_token").digest()
    sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
    assert cb.verify_webhook(body, sig) is True
    assert cb.verify_webhook(body, "bad_sig") is False

    # xRocket signature verification
    xr = payments.XRocketProvider(key="test_xr_key", net="testnet", rub_per_usdt=100.0)
    xr_secret = hashlib.sha256(b"test_xr_key").digest()
    xr_sig = hmac.new(xr_secret, body, hashlib.sha256).hexdigest()
    assert xr.verify_webhook(body, xr_sig) is True
    assert xr.verify_webhook(body, "wrong_signature") is False


@pytest.mark.asyncio
async def test_custom_buttons_rendering_and_persistence(tmp_path):
    db_file = tmp_path / "btn_test.db"
    db = Database(db_file)
    await db.connect()

    try:
        ctx = Context(settings=None, db=db, builder=None, runner=None)

        # 1. Default button (pack_ready_add has default_style="primary")
        b = texts.make_button(ctx, "pack_ready_add", url="https://t.me/addstickers/test")
        assert b.text == "✨ добавить в Telegram"
        assert b.url == "https://t.me/addstickers/test"
        assert getattr(b, "style", None) == "primary"
        assert getattr(b, "icon_custom_emoji_id", None) is None

        # Button with None default style
        b_share = texts.make_button(ctx, "pack_ready_share", url="https://t.me/share")
        assert getattr(b_share, "style", None) is None

        # 2. Set custom text, style, and icon_custom_emoji_id
        cfg = {
            "text": "🌟 Забрать стикеры!",
            "style": "success",
            "icon_custom_emoji_id": "5368324170671202286",
        }
        await runtime.set_custom_button(db, "pack_ready_add", cfg)
        loaded = await runtime.load_custom_buttons(db)
        assert "pack_ready_add" in loaded
        assert loaded["pack_ready_add"] == cfg

        # 3. Update runtime context
        await runtime.update_custom_button(ctx, "pack_ready_add", cfg)
        assert ctx.custom_buttons["pack_ready_add"] == cfg

        # 4. Render with custom config
        b_custom = texts.make_button(ctx, "pack_ready_add", url="https://t.me/addstickers/test")
        assert b_custom.text == "🌟 Забрать стикеры!"
        assert getattr(b_custom, "style", None) == "success"
        assert str(getattr(b_custom, "icon_custom_emoji_id", None)) == "5368324170671202286"

        # 5. Reset button
        await runtime.update_custom_button(ctx, "pack_ready_add", None)
        assert "pack_ready_add" not in ctx.custom_buttons
        loaded_after_reset = await runtime.load_custom_buttons(db)
        assert "pack_ready_add" not in loaded_after_reset

        b_reset = texts.make_button(ctx, "pack_ready_add", url="https://t.me/addstickers/test")
        assert b_reset.text == "✨ добавить в Telegram"
        assert getattr(b_reset, "style", None) == "primary"

        # 6. Fallback test for subscription buttons: legacy custom_texts -> custom_buttons
        ctx.custom_texts["subscription_join"] = "Канал проекта"
        b_sub = texts.make_button(ctx, "sub_join", url="https://t.me/mychannel")
        assert b_sub.text == "Канал проекта"

        # If custom_buttons has an explicit text, it takes precedence
        ctx.custom_buttons["sub_join"] = {"text": "Войти в секретный канал", "style": "primary"}
        b_sub_override = texts.make_button(ctx, "sub_join", url="https://t.me/mychannel")
        assert b_sub_override.text == "Войти в секретный канал"
        assert getattr(b_sub_override, "style", None) == "primary"

        # Test explicit default/gray style override
        ctx.custom_buttons["pack_ready_add"] = {"style": "default"}
        b_gray = texts.make_button(ctx, "pack_ready_add", url="https://t.me/addstickers/test")
        assert getattr(b_gray, "style", None) is None

        # Test danger/red style
        ctx.custom_buttons["sub_check"] = {"style": "danger"}
        b_danger = texts.make_button(ctx, "sub_check", callback_data="subscription:check")
        assert getattr(b_danger, "style", None) == "danger"
    finally:
        await db.close()
