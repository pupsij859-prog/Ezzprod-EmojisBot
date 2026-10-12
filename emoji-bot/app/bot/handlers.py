"""Обработчики бота: /start (+реферал), /help, оплата Stars, служебные команды платежей."""
from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)

from .. import admin, services
from ..context import Context
from . import banner, texts

log = logging.getLogger("ezzprod.bot")

router = Router(name="main")
router.message.filter(F.chat.type == "private")

REF_PARAM = re.compile(r"^ref_([0-9a-f]{8})$")


def tg_user(message: Message) -> dict:
    u = message.from_user
    return {"id": u.id, "username": u.username, "first_name": u.first_name or "", "last_name": u.last_name or ""}


def open_keyboard(ctx: Context) -> InlineKeyboardMarkup | None:
    url = ctx.settings.webapp_url
    if not url.startswith("https://"):
        return None  # Telegram принимает web_app-кнопки только с https
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=texts.BTN_OPEN, web_app=WebAppInfo(url=url))]]
    )


@router.message(CommandStart())
async def on_start(message: Message, command: CommandObject, ctx: Context) -> None:
    ref_code = None
    if command.args:
        m = REF_PARAM.match(command.args.strip())
        if m:
            ref_code = m.group(1)
    await services.upsert_user(ctx.db, ctx.settings, tg_user(message), ref_code)

    keyboard = open_keyboard(ctx)
    if keyboard is None:
        await message.answer(texts.start_no_webapp(ctx.settings.brand_name))
        return

    path = banner.get_banner(ctx.settings)
    if path is None:
        await message.answer(texts.help_text(ctx.settings.brand_name), reply_markup=keyboard)
        return
    photo = ctx.banner_file_id or FSInputFile(path)
    sent = await message.answer_photo(photo, reply_markup=keyboard)
    if not ctx.banner_file_id and sent.photo:
        ctx.banner_file_id = sent.photo[-1].file_id  # дальше шлём по file_id, без повторной загрузки


@router.message(Command("help"))
async def on_help(message: Message, ctx: Context) -> None:
    await message.answer(texts.help_text(ctx.settings.brand_name), reply_markup=open_keyboard(ctx))


# ── обязательные для платёжных ботов команды
@router.message(Command("terms"))
async def on_terms(message: Message, ctx: Context) -> None:
    url = ctx.settings.webapp_url
    await message.answer(texts.terms(f"{url}/docs/terms.html" if url.startswith("https://") else None))


@router.message(Command("support"))
async def on_support(message: Message, ctx: Context) -> None:
    await message.answer(texts.support(ctx.settings.support_username or None))


@router.message(Command("paysupport"))
async def on_paysupport(message: Message, ctx: Context) -> None:
    await message.answer(texts.paysupport(ctx.settings.support_username or None))


@router.message(Command("refund"))
async def on_refund(message: Message, command: CommandObject, ctx: Context) -> None:
    """Возврат Stars. Только для ADMIN_IDS; остальным бот не отвечает."""
    if message.from_user.id not in ctx.settings.admin_ids:
        return
    charge_id = (command.args or "").strip()
    if not charge_id:
        await message.answer(texts.REFUND_USAGE)
        return
    try:
        ok, user_id, qty = await admin.refund(ctx, "stars", charge_id)
    except services.RefundError as exc:
        await message.answer(texts.REFUND_ERRORS.get(str(exc), "не получилось начать возврат"))
        return
    if ok:
        await message.answer(texts.REFUND_DONE)
        await admin.notify_refunded(ctx, user_id, qty)
    else:
        await message.answer(texts.REFUND_FAILED)


# ── оплата Stars
@router.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery, ctx: Context) -> None:
    # отвечать нужно за 10 секунд — только быстрая проверка по своей БД
    payment = await services.get_payment(ctx.db, query.invoice_payload)
    ok = (
        ctx.settings.payments_live
        and not ctx.settings.demo_mode
        and payment is not None
        and payment["status"] == "pending"
        and payment["user_id"] == query.from_user.id
        and query.currency == "XTR"
        and payment["stars"] == query.total_amount
    )
    await query.answer(ok=bool(ok), error_message=None if ok else texts.PAYMENT_STALE)


@router.message(F.successful_payment)
async def on_paid(message: Message, ctx: Context) -> None:
    sp = message.successful_payment
    result = await services.settle_payment(
        ctx.db, ctx.settings, sp.invoice_payload, sp.telegram_payment_charge_id, sp.total_amount,
        message.from_user.id, currency=sp.currency,
    )
    if result is None:
        return  # повторная доставка того же платежа
    if result.status == "needs_review":
        # деньги получены, а платёж не сходится с заказом — нужен ручной разбор
        log.critical(
            "ПЛАТЁЖ ТРЕБУЕТ РАЗБОРА: user=%s payload=%s charge=%s amount=%s %s",
            message.from_user.id, sp.invoice_payload, sp.telegram_payment_charge_id, sp.total_amount, sp.currency,
        )
        await ctx.notifier.admin_alert(
            f"платёж требует разбора: charge={sp.telegram_payment_charge_id} user={message.from_user.id} "
            f"сумма={sp.total_amount} {sp.currency}"
        )
        await message.answer(texts.PAYMENT_PROBLEM)
        return
    await ctx.notifier.payment_ok(result.user_id, result.qty, result.balance)


@router.message()
async def on_other(message: Message, ctx: Context) -> None:
    await message.answer(texts.FALLBACK, reply_markup=open_keyboard(ctx))
