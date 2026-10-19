"""Обработчики бота: /start (+реферал), /help, оплата Stars, служебные команды платежей."""
from __future__ import annotations

import asyncio
import html
import logging
import re

from aiogram import BaseMiddleware, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputRichMessage,
    InputRichMessageMedia,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)

from .. import admin, partners, runtime, services, subscription
from ..notify import reward_sender
from ..ru import gens
from ..context import Context
from . import banner, texts

log = logging.getLogger("ezzprod.bot")

router = Router(name="main")
router.message.filter(F.chat.type == "private")


async def send_html(event: Message | CallbackQuery, text: str, reply_markup=None) -> Message:
    target = event.message if isinstance(event, CallbackQuery) else event
    try:
        return await target.answer(text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        return await target.answer(text, reply_markup=reply_markup, parse_mode=None)


class AccessMiddleware(BaseMiddleware):
    """Бан и техработы (включаются в /admin). Оплату пропускаем всегда: деньги уже списаны — зачислить обязаны."""

    async def __call__(self, handler, event: Message, data: dict):
        ctx: Context = data["ctx"]
        user = event.from_user
        if user is None or event.successful_payment is not None or runtime.is_admin(ctx, user.id):
            return await handler(event, data)
        row = await services.get_user(ctx.db, user.id)
        if row is not None and row["banned"]:
            return None  # забаненным молчим
        if ctx.maintenance:
            await send_html(event, texts.maintenance_text(ctx))
            return None
        # /start сначала сохраняет реферальный код. Поддержка доступна до подписки.
        parts = (event.text or "").split(maxsplit=1)
        command = parts[0].split("@")[0].lower() if parts else ""
        if command not in ("/start", "/terms", "/support", "/paysupport", "/refund"):
            if not await require_subscription(event, ctx):
                return None
        return await handler(event, data)


router.message.middleware(AccessMiddleware())

REF_PARAM = re.compile(r"^ref_([0-9a-f]{8})\Z")


def tg_user(message: Message) -> dict:
    u = message.from_user
    return {"id": u.id, "username": u.username, "first_name": u.first_name or "", "last_name": u.last_name or ""}


def open_keyboard(ctx: Context) -> InlineKeyboardMarkup | None:
    url = ctx.webapp_url
    if not url.startswith("https://"):
        return None  # Telegram принимает web_app-кнопки только с https
    return InlineKeyboardMarkup(
        inline_keyboard=[[texts.make_button(ctx, "start_open", web_app_url=url)]]
    )


def welcome_keyboard(ctx: Context, ref_code: str | None) -> InlineKeyboardMarkup | None:
    """Под баннером /start: «Запустить бота» (Mini App) во всю ширину, ниже одним рядом «Поддержка»
    и «Реф. ссылка» (копирует ссылку)."""
    url = ctx.webapp_url
    if not url.startswith("https://"):
        return None
    rows = [[texts.make_button(ctx, "welcome_open", web_app_url=url)]]
    support = ctx.settings.support_username or texts.SUPPORT_DEFAULT
    extra = [texts.make_button(ctx, "welcome_support", url=f"https://t.me/{support}")]
    link = ctx.ref_link(ref_code) if ref_code else None
    if link:
        extra.append(texts.make_button(ctx, "welcome_ref", copy_text=link))
    rows.append(extra)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subscription_keyboard(ctx: Context) -> InlineKeyboardMarkup:
    rows = []
    url = subscription.channel_url(ctx.settings)
    raw_join = texts.get_custom_text(ctx, "subscription_join")
    raw_check = texts.get_custom_text(ctx, "subscription_check")
    join_text = re.sub(r"<[^>]+>", "", raw_join).strip() or None
    check_text = re.sub(r"<[^>]+>", "", raw_check).strip() or None
    if url:
        rows.append([texts.make_button(ctx, "sub_join", url=url, default_text_override=join_text)])
    rows.append([texts.make_button(ctx, "sub_check", callback_data="subscription:check", default_text_override=check_text)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def require_subscription(message: Message, ctx: Context) -> bool:
    try:
        if await subscription.check(ctx, message.from_user.id):
            return True
        unavailable = False
    except subscription.CheckUnavailable:
        unavailable = True
    detail = subscription.payload(ctx, unavailable=unavailable)
    text = detail["message"] + ("\n\n" + detail["error"] if unavailable else "")
    await send_html(message, text, reply_markup=subscription_keyboard(ctx))
    return False


@router.callback_query(F.data == "subscription:check", F.message.chat.type == "private")
async def on_check_subscription(call: CallbackQuery, ctx: Context) -> None:
    row = await services.get_user(ctx.db, call.from_user.id)
    if not runtime.is_admin(ctx, call.from_user.id):
        if row is not None and row["banned"]:
            await call.answer()
            return
        if ctx.maintenance:
            clean_maint = re.sub(r"<[^>]+>", "", texts.maintenance_text(ctx)).strip()
            await call.answer(clean_maint[:200], show_alert=True)
            return
    try:
        subscribed = await subscription.check(ctx, call.from_user.id)
    except subscription.CheckUnavailable:
        clean_err = re.sub(r"<[^>]+>", "", texts.get_custom_text(ctx, "subscription_error")).strip()
        await call.answer(clean_err[:200], show_alert=True)
        return
    if not subscribed:
        clean_miss = re.sub(r"<[^>]+>", "", texts.get_custom_text(ctx, "subscription_missing")).strip()
        await call.answer(clean_miss[:200], show_alert=True)
        return
    await call.answer()
    keyboard = open_keyboard(ctx)
    text = texts.get_custom_text(ctx, "subscription_ok") if keyboard else texts.start_no_webapp(ctx.settings.brand_name)
    # Новое сообщение: повторный клик по старой кнопке тоже остаётся безопасным.
    await send_html(call, text, reply_markup=keyboard)


async def send_rich_welcome(message: Message, ctx: Context, text: str, photo, ref_code: str | None) -> bool:
    """Приветствие rich-сообщением (Bot API 10.1+): баннер, текст и кнопки внутри одного сообщения, как
    в «Статье» Telegram. False — Telegram его не принял (старый клиент API, кривой текст из админки):
    тогда приветствие уйдёт обычным фото с клавиатурой."""
    support = ctx.settings.support_username or texts.SUPPORT_DEFAULT
    link = ctx.ref_link(ref_code) if ref_code else None
    rows = [
        [texts.rich_button(ctx, "welcome_open", "web_app", url=ctx.webapp_url)],
        [texts.rich_button(ctx, "welcome_support", "url", url=f"https://t.me/{support}"),
         *([texts.rich_button(ctx, "welcome_ref", "copy_text", text=link)] if link else [])],
    ]
    rich = InputRichMessage(
        html=texts.rich_welcome(ctx, text, rows),
        media=[InputRichMessageMedia(id=texts.RICH_BANNER_ID, media=InputMediaPhoto(media=photo))],
        skip_entity_detection=True,
    )
    try:
        sent = await message.bot.send_rich_message(chat_id=message.chat.id, rich_message=rich)
    except TelegramBadRequest as exc:
        log.warning("rich-приветствие не отправилось, шлю обычное: %s", exc)
        return False
    if not ctx.banner_file_id and sent.rich_message:
        photo_block = next((b for b in sent.rich_message.blocks or [] if getattr(b, "photo", None)), None)
        if photo_block:
            ctx.banner_file_id = photo_block.photo[-1].file_id  # дальше шлём по file_id, без повторной загрузки
    return True


@router.message(CommandStart())
async def on_start(message: Message, command: CommandObject, ctx: Context) -> None:
    ref_code = partner = None
    if command.args:
        m = REF_PARAM.match(command.args.strip())
        if m:
            ref_code = m.group(1)
        partner = partners.code_from_param(command.args)
        if partner and not await partners.count_click(ctx.db, partner):
            partner = None  # такой ссылки нет
    row, created = await services.upsert_user(ctx.db, ctx.settings, tg_user(message), ref_code, partner)
    if created and partner:
        reward = await partners.signup_reward(ctx.db, row["id"])
        if reward:
            await ctx.notifier.partner_reward(*reward)
    if row["blocked"]:
        await runtime.set_blocked(ctx.db, row["id"], False)  # снова пишет боту — рассылки ему доходят

    if not await require_subscription(message, ctx):
        return
    keyboard = welcome_keyboard(ctx, row["ref_code"])
    if keyboard is None:
        await send_html(message, texts.start_no_webapp(ctx.settings.brand_name))
        return

    name = (message.from_user.first_name or message.from_user.username or "друг").strip()
    caption = texts.render_msg(ctx, "welcome", name=name, brand=ctx.settings.brand_name)
    path = await asyncio.to_thread(banner.get_banner, ctx.settings)  # Pillow не должен блокировать бота
    if path is None:
        await send_html(message, caption, reply_markup=keyboard)
        return
    photo = ctx.banner_file_id or FSInputFile(path)
    if await send_rich_welcome(message, ctx, caption, photo, row["ref_code"]):
        return
    try:
        sent = await message.answer_photo(photo, caption=caption, parse_mode="HTML", reply_markup=keyboard)
    except TelegramBadRequest:
        # кривая разметка в тексте из админки или премиум-эмодзи недоступны — шлём без оформления
        sent = await message.answer_photo(photo, caption=html.unescape(re.sub(r"<[^>]+>", "", caption))[:1024],
                                          parse_mode=None,
                                          reply_markup=keyboard)
    if not ctx.banner_file_id and sent.photo:
        ctx.banner_file_id = sent.photo[-1].file_id  # дальше шлём по file_id, без повторной загрузки


@router.message(Command("help"))
async def on_help(message: Message, ctx: Context) -> None:
    await send_html(message, texts.help_text(ctx.settings.brand_name, ctx=ctx), reply_markup=open_keyboard(ctx))


# ── обязательные для платёжных ботов команды
@router.message(Command("terms"))
async def on_terms(message: Message, ctx: Context) -> None:
    url = ctx.webapp_url
    await send_html(message, texts.terms(f"{url}/docs/terms.html" if url.startswith("https://") else None, ctx=ctx))


@router.message(Command("support"))
async def on_support(message: Message, ctx: Context) -> None:
    await send_html(message, texts.support(ctx.settings.support_username or None, ctx=ctx))


@router.message(Command("paysupport"))
async def on_paysupport(message: Message, ctx: Context) -> None:
    await send_html(message, texts.paysupport(ctx.settings.support_username or None, ctx=ctx))


class PromoInput(StatesGroup):
    code = State()


@router.message(Command("partner"))
async def on_partner(message: Message, ctx: Context) -> None:
    """Статистика для партнёра: его ссылки, сколько людей пришло и сколько он получил."""
    rows = await partners.owned_by(ctx.db, message.from_user.id)
    if not rows:
        await send_html(message, "у тебя пока нет партнёрской ссылки. если хочешь рекламировать бота — напиши в поддержку.")
        return
    blocks = []
    for r in rows[:5]:
        st = await partners.stats(ctx.db, r["code"])
        link, _app = partners.links(ctx.bot_username, r["code"])
        live = partners.state(r) == "active"
        reward = f"\n🎁 тебе: <b>+{r['reward']}</b> {partners.REWARD_ON[r['reward_on']]}" if r["reward"] else ""
        blocks.append(
            f"🔗 <b>{html.escape(r['name'])}</b>{'' if live else ' · <i>выключена</i>'}\n"
            + (f"<code>{link}</code>\n" if link else "")
            + f"👆 переходов: <b>{r['clicks']}</b> · пришло: <b>{st['joined']}</b> (за неделю {st['joined_week']})\n"
            f"🎨 сделали пак: <b>{st['active']}</b> · купили: <b>{st['buyers']}</b>{reward}\n"
            f"✦ получено тобой: <b>{gens(st['reward_given'])}</b>"
            + (f" · ждут условия: {st['pending']}" if st["pending"] else ""))
    await send_html(message, "\n\n".join(blocks))


@router.message(Command("promo"))
async def on_promo(message: Message, command: CommandObject, ctx: Context, state: FSMContext) -> None:
    await services.upsert_user(ctx.db, ctx.settings, tg_user(message))
    code = (command.args or "").strip()
    if not code:
        await state.set_state(PromoInput.code)
        await message.answer(texts.PROMO_ASK)
        return
    await redeem(message, ctx, code)


@router.message(PromoInput.code, F.text & ~F.text.startswith("/"))
async def on_promo_code(message: Message, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await redeem(message, ctx, message.text)


async def redeem(message: Message, ctx: Context, code: str) -> None:
    if not ctx.limiter.allow("promo", message.from_user.id, 8, 60):  # общий лимит с Mini App: перебор кодов
        await message.answer(texts.PROMO_SLOW)
        return
    words = code.split()
    try:
        res = await runtime.redeem_promo(ctx.db, message.from_user.id, words[0] if words else "")
    except runtime.PromoError as exc:
        await message.answer(texts.PROMO_ERRORS.get(exc.reason, texts.PROMO_ERRORS["unknown"]))
        return
    await send_html(message, texts.promo_ok(res.gens, res.balance, res.discount_pct), reply_markup=open_keyboard(ctx))


@router.message(Command("refund"))
async def on_refund(message: Message, command: CommandObject, ctx: Context) -> None:
    """Возврат Stars. Только для админов; остальным бот не отвечает."""
    if not runtime.is_admin(ctx, message.from_user.id):
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
    if ok is True:
        await message.answer(texts.REFUND_DONE)
        await admin.notify_refunded(ctx, user_id, qty)
    elif ok is False:
        await message.answer(texts.REFUND_FAILED)
    else:
        await message.answer(texts.REFUND_UNKNOWN)


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
        await send_html(message, texts.payment_problem_text(ctx))
        return
    await ctx.notifier.payment_ok(result.user_id, result.qty, result.balance)
    if result.reward_user_id:
        await reward_sender(ctx.notifier, result.reward_kind)(result.reward_user_id, result.reward_amount)


@router.message()
async def on_other(message: Message, ctx: Context) -> None:
    await send_html(message, texts.fallback_text(ctx), reply_markup=open_keyboard(ctx))
