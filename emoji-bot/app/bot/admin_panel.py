"""Админка в чате бота: /admin. Видна только админам (ADMIN_IDS из .env и добавленным из админки).

Устроена как в username_bot: главный экран со сводкой и предупреждениями, разделы кнопками, всё редактируется
в том же сообщении. Ввод (число, ID, текст рассылки) — через состояния FSM; «✕ Отмена» возвращает назад.
Деньги и генерации меняются только через сервисы (ledger), как и везде в приложении.
"""
from __future__ import annotations

import asyncio
import csv
import dataclasses
import datetime
import html
import io
import logging
import os
import platform
import tempfile
import time
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from .. import admin, catalog, partners, runtime, services, subscription
from ..context import Context
from . import texts

log = logging.getLogger("ezzprod.admin_chat")

router = Router(name="admin_chat")
PAGE = 8
DAY = 86400
PAID = admin.PAID


async def _only_admins(event, ctx: Context) -> bool:
    user = getattr(event, "from_user", None)
    return user is not None and runtime.is_admin(ctx, user.id)


router.message.filter(_only_admins)
router.callback_query.filter(_only_admins)


class St(StatesGroup):
    find = State()
    give = State()
    grant = State()
    message = State()
    setting = State()
    promo = State()
    promo_field = State()
    broadcast = State()
    bc_button = State()
    add_admin = State()
    custom_text = State()
    custom_button_text = State()
    custom_button_emoji = State()
    link = State()
    link_field = State()


# ───────────────────────── мелочи ─────────────────────────

esc = html.escape


def btn(text: str, cb: str | None = None, *, url: str | None = None, style: str | None = None) -> InlineKeyboardButton:
    """style: primary (синяя), success (зелёная), danger (красная)."""
    extra = {"style": style} if style else {}
    if url:
        return InlineKeyboardButton(text=text, url=url, **extra)
    return InlineKeyboardButton(text=text, callback_data=cb or "a", **extra)


def kb(*rows) -> InlineKeyboardMarkup:
    out = []
    for r in rows:
        if r is None:
            continue
        row = [b for b in (r if isinstance(r, (list, tuple)) else [r]) if b is not None]
        if row:
            out.append(row)
    return InlineKeyboardMarkup(inline_keyboard=out)


def back(cb: str = "a", text: str = "← Админка") -> InlineKeyboardButton:
    return btn(text, cb)


def cancel(cb: str = "a") -> InlineKeyboardMarkup:
    return kb([btn("✕ Отмена", cb)])


def stars(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ") + " ⭐"


def dt(ts) -> str:
    return datetime.datetime.fromtimestamp(int(ts or 0)).strftime("%d.%m %H:%M") if ts else "—"


def who(row, uid: int | None = None) -> str:
    uid = uid if uid is not None else row["id"]
    if row is None:
        return f"<code>{uid}</code>"
    name = f"@{esc(row['username'])}" if row["username"] else esc(f"{row['first_name'] or ''} {row['last_name'] or ''}".strip() or "без имени")
    return f"{name} (<code>{uid}</code>)"


async def show(event: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Экран: у кнопки — правим то же сообщение, у команды — шлём новое."""
    if isinstance(event, CallbackQuery) and event.message is not None and getattr(event.message, "text", None) is not None:
        try:
            await event.message.edit_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
            return
        except TelegramBadRequest as err:
            if "not modified" in str(err).lower():
                return
    chat = event.from_user.id if isinstance(event, CallbackQuery) else event.chat.id
    await event.bot.send_message(chat, text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)


async def n(ctx: Context, sql: str, params: tuple = ()) -> int:
    row = await ctx.db.fetchone(sql, params)
    return int((row[0] if row else 0) or 0)


def _in(items) -> str:
    return ",".join("?" * len(items))


# ───────────────────────── главная ─────────────────────────

async def home(event, ctx: Context, state: FSMContext | None = None) -> None:
    if state is not None:
        await state.clear()
    s = ctx.settings
    since = services.now() - DAY
    users = await n(ctx, "SELECT COUNT(*) FROM users")
    new = await n(ctx, "SELECT COUNT(*) FROM users WHERE created_at>=?", (since,))
    banned = await n(ctx, "SELECT COUNT(*) FROM users WHERE banned=1")
    rev_day = await n(ctx, f"SELECT COALESCE(SUM(amount),0) FROM payment_events WHERE status IN ({_in(PAID)}) AND created_at>=?", (*PAID, since))
    rev_all = await n(ctx, f"SELECT COALESCE(SUM(amount),0) FROM payment_events WHERE status IN ({_in(PAID)})", PAID)
    buys_day = await n(ctx, f"SELECT COUNT(*) FROM payment_events WHERE status IN ({_in(PAID)}) AND created_at>=?", (*PAID, since))
    packs_day = await n(ctx, "SELECT COUNT(*) FROM packs WHERE created_at>=?", (since,))
    items_day = await n(ctx, "SELECT COALESCE(SUM(count),0) FROM packs WHERE created_at>=?", (since,))
    processing = await n(ctx, "SELECT COUNT(*) FROM packs WHERE status='processing'")
    failed_day = await n(ctx, "SELECT COUNT(*) FROM packs WHERE status='failed' AND created_at>=?", (since,))
    review = await n(ctx, "SELECT COUNT(*) FROM payment_events WHERE status IN ('needs_review','refunding')")
    on_balance = await n(ctx, "SELECT COALESCE(SUM(balance),0) FROM users")

    warns = []
    if ctx.maintenance:
        warns.append("🔧 Техработы ВКЛ — пользователи не могут пользоваться ботом")
    if ctx.bot is None:
        warns.append("📡 Нет связи с Telegram — бот переподключается")
    if s.demo_mode:
        warns.append("🧪 Демо-режим: паки не публикуются в Telegram")
    elif not s.payments_live:
        warns.append("💳 Приём оплаты выключен — генерации купить нельзя")
    if review:
        warns.append(f"⚠️ Платежей на разборе/возврате: <b>{review}</b>")
    if failed_day:
        warns.append(f"🔴 Неудачных паков за сутки: <b>{failed_day}</b>")
    if not ctx.webapp_url.startswith("https://"):
        warns.append("🔗 Нет https-адреса Mini App — кнопка «открыть» не работает")

    text = (
        "🛠 <b>Админка EzzProd</b>\n\n"
        f"👥 Пользователи: <b>{users}</b> · сегодня +{new}" + (f" · в бане {banned}" if banned else "") + "\n"
        f"💰 За сутки: <b>{stars(rev_day)}</b> · покупок {buys_day} · всего {stars(rev_all)}\n"
        f"🎨 За сутки: паков <b>{packs_day}</b> · элементов {items_day}" + (f" · собирается {processing}" if processing else "") + "\n"
        f"✦ Генераций на балансах: <b>{on_balance}</b>\n"
        f"⚙️ Режим: <b>{'демо' if s.demo_mode else 'настоящие паки'}</b> · оплата <b>{'вкл' if s.payments_live else 'выкл'}</b>"
        f" · цена {s.price_rub} ₽"
    )
    if warns:
        text += "\n\n⚠️ <b>Внимание:</b>\n" + "\n".join(f"• {w}" for w in warns)

    markup = kb(
        [btn("📊 Статистика", "a:st:1"), btn("💰 Продажи", "a:py:all:0")],
        [btn("👤 Найти пользователя", "a:u"), btn("🎁 Выдать генерации", "a:gv", style="success")],
        [btn(f"📦 Паки ({processing} в работе)" if processing else "📦 Паки", "a:pk:all:0"), btn("🎟 Промокоды", "a:pr")],
        [btn("🔗 Партнёрские ссылки", "a:lk")],
        [btn("💲 Цены и лимиты", "a:set"), btn("💬 Тексты сообщений", "a:msgs")],
        [btn("🔘 Кнопки в чате", "a:btns"), btn("🔒 Обязательная подписка", "a:sub")],
        [btn("🧩 Шаблоны", "a:tp"), btn("👑 Админы", "a:ad")],
        [btn("📢 Рассылка", "a:bc"), btn("🖥 Сервер", "a:sy")],
        [btn("💾 Бэкап", "a:bk"), btn("📥 Экспорт", "a:ex")],
        [btn("🔧 Техработы: " + ("ВКЛ" if ctx.maintenance else "выкл"), "a:mt", style="danger" if ctx.maintenance else None),
         btn("❓ Помощь", "a:help")],
        [btn("🔄 Обновить", "a", style="success"), btn("✕ Закрыть", "a:x")],
    )
    await show(event, text, markup)


@router.message(Command("admin"))
async def cmd_admin(message: Message, ctx: Context, state: FSMContext) -> None:
    await home(message, ctx, state)


@router.callback_query(F.data == "a")
async def cb_home(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await home(call, ctx, state)
    await call.answer()


@router.callback_query(F.data == "a:x")
async def cb_close(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await call.message.delete()
    except TelegramBadRequest:
        pass
    await call.answer()


@router.callback_query(F.data == "a:mt")
async def cb_maintenance(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    on = not ctx.maintenance
    await runtime.set_maintenance(ctx, on)
    await home(call, ctx, state)
    await call.answer("Техработы включены: зайти могут только админы" if on else "Техработы выключены", show_alert=True)


@router.callback_query(F.data == "a:help")
async def cb_help(call: CallbackQuery) -> None:
    await show(call, (
        "❓ <b>Что где</b>\n\n"
        "📊 <b>Статистика</b> — пользователи, деньги, паки, популярные шаблоны за сутки/неделю/месяц/всё время.\n"
        "💰 <b>Продажи</b> — все оплаты Stars; из карточки можно вернуть звёзды (генерации спишутся сами).\n"
        "👤 <b>Найти пользователя</b> — по ID, @нику или пересланному сообщению: баланс, покупки, паки; "
        "начислить/списать, Pro, бан, написать от бота.\n"
        "🎁 <b>Выдать генерации</b> — быстро: <code>ID количество</code>.\n"
        "📦 <b>Паки</b> — лента сборок, ошибки, дособрать зависший.\n"
        "🎟 <b>Промокоды</b> — конструктор: свой или случайный код, фиксированная или случайная награда, "
        "число активаций, срок, «только новичкам», пачка кодов файлом. Активация: <code>/promo КОД</code> или в профиле Mini App.\n"
        "💲 <b>Цены и лимиты</b> — цена, скидки, подарки, рефералка, лимиты пака, оплата. Меняется сразу, без перезапуска.\n"
        "📢 <b>Рассылка</b> — любое сообщение (текст, фото, видео) всем или части пользователей, с кнопкой.\n"
        "🧩 <b>Шаблоны</b> — скрыть/показать категорию, перечитать папку templates/.\n"
        "👑 <b>Админы</b> — кто ещё может заходить в /admin.\n"
        "🖥 <b>Сервер</b> — режим, адрес Mini App, база, сверка балансов.\n"
        "💾 <b>Бэкап</b> — копия базы файлом. 📥 <b>Экспорт</b> — пользователи в CSV.\n"
        "🔧 <b>Техработы</b> — закрыть бота и Mini App для всех, кроме админов."
    ), kb([back()]))
    await call.answer()


# ───────────────────────── статистика ─────────────────────────

PERIODS = {"1": ("сутки", DAY), "7": ("неделю", 7 * DAY), "30": ("месяц", 30 * DAY), "0": ("всё время", 0)}


@router.callback_query(F.data.startswith("a:st:"))
async def cb_stats(call: CallbackQuery, ctx: Context) -> None:
    key = call.data.split(":")[2]
    label, span = PERIODS.get(key, PERIODS["1"])
    since = services.now() - span if span else 0
    paid = f"status IN ({_in(PAID)}) AND created_at>=?"
    users_new = await n(ctx, "SELECT COUNT(*) FROM users WHERE created_at>=?", (since,))
    users_all = await n(ctx, "SELECT COUNT(*) FROM users")
    blocked = await n(ctx, "SELECT COUNT(*) FROM users WHERE blocked=1")
    revenue = await n(ctx, f"SELECT COALESCE(SUM(amount),0) FROM payment_events WHERE {paid}", (*PAID, since))
    buys = await n(ctx, f"SELECT COUNT(*) FROM payment_events WHERE {paid}", (*PAID, since))
    payers = await n(ctx, f"SELECT COUNT(DISTINCT user_id) FROM payment_events WHERE {paid}", (*PAID, since))
    sold = await n(ctx, "SELECT COALESCE(SUM(delta),0) FROM ledger WHERE reason='purchase' AND created_at>=?", (since,))
    gifts = await n(ctx, "SELECT COALESCE(SUM(delta),0) FROM ledger WHERE reason IN ('signup','referral','promo','admin') AND delta>0 AND created_at>=?", (since,))
    refunds = await n(ctx, "SELECT COUNT(*) FROM payment_events WHERE status IN ('refunded','refunding') AND created_at>=?", (since,))
    packs = await n(ctx, "SELECT COUNT(*) FROM packs WHERE created_at>=?", (since,))
    emoji_p = await n(ctx, "SELECT COUNT(*) FROM packs WHERE type='emoji' AND created_at>=?", (since,))
    items = await n(ctx, "SELECT COALESCE(SUM(count),0) FROM packs WHERE created_at>=?", (since,))
    failed = await n(ctx, "SELECT COUNT(*) FROM packs WHERE status='failed' AND created_at>=?", (since,))
    makers = await n(ctx, "SELECT COUNT(DISTINCT user_id) FROM packs WHERE created_at>=?", (since,))
    refs = await n(ctx, "SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL AND created_at>=?", (since,))
    top = await ctx.db.fetchall(
        "SELECT i.template_id AS t, COUNT(*) AS c FROM pack_items i JOIN packs p ON p.id=i.pack_id "
        "WHERE p.created_at>=? GROUP BY i.template_id ORDER BY c DESC LIMIT 5", (since,))
    cats = await ctx.db.fetchall(
        "SELECT substr(i.template_id, 1, instr(i.template_id, '-')-1) AS cat, COUNT(*) AS c FROM pack_items i "
        "JOIN packs p ON p.id=i.pack_id WHERE p.created_at>=? GROUP BY cat ORDER BY c DESC LIMIT 5", (since,))
    cat_names = {c.id: c.name for c in catalog.all_categories()}
    conv = f"{payers / users_all * 100:.1f}%" if users_all else "—"
    avg = f"{items / packs:.1f}" if packs else "—"
    text = (
        f"📊 <b>Статистика за {label}</b>\n\n"
        f"👥 Новых: <b>{users_new}</b> (по рефссылкам {refs}) · всего {users_all} · заблокировали бота {blocked}\n\n"
        f"💰 Выручка: <b>{stars(revenue)}</b> · покупок {buys} · покупателей {payers}\n"
        f"✦ Продано генераций: <b>{sold}</b> · подарено {gifts} · возвратов {refunds}\n"
        f"📈 Конверсия в покупку (всего): {conv}\n\n"
        f"🎨 Паков: <b>{packs}</b> (эмодзи {emoji_p} · стикеры {packs - emoji_p}) · элементов {items} · в среднем {avg}\n"
        f"🙋 Делали паки: {makers} · неудачных: {failed}"
    )
    if top:
        text += "\n\n🔥 <b>Популярные шаблоны:</b>\n" + "\n".join(f"• {esc(r['t'])} — {r['c']}" for r in top)
    if cats:
        text += "\n\n🗂 <b>Категории:</b> " + " · ".join(f"{esc(cat_names.get(r['cat'], r['cat'] or '?'))} {r['c']}" for r in cats)
    tabs = [btn(("• " if k == key else "") + v[0], f"a:st:{k}") for k, v in PERIODS.items()]
    await show(call, text, kb(tabs, [back()]))
    await call.answer()


# ───────────────────────── продажи ─────────────────────────

PAY_ICON = {"credited": "✅", "credited_repeat": "✅", "needs_review": "⚠️", "refunding": "⏳", "refunded": "↩️"}
PAY_TABS = {"all": "все", "paid": "оплачены", "review": "разбор", "refunded": "возвраты"}


@router.callback_query(F.data.startswith("a:py:"))
async def cb_payments(call: CallbackQuery, ctx: Context) -> None:
    _, _, flt, off = call.data.split(":")
    flt = flt if flt in admin.PAYMENT_FILTERS else "all"
    offset = max(0, int(off))
    data = await admin.payments(ctx.db, flt, PAGE, offset)
    lines, buttons = [], []
    for p in data["items"]:
        u = p["user"]
        name = f"@{u['username']}" if u["username"] else (u["first_name"] or str(u["id"]))
        lines.append(f"{PAY_ICON.get(p['status'], '·')} <b>#{p['id']}</b> · {dt(p['created_at'])} · {esc(name)} · "
                     f"{stars(p['amount'])} · {p['qty'] or '?'} ген.")
        buttons.append(btn(f"#{p['id']}", f"a:pc:{p['id']}"))
    text = f"💰 <b>Продажи</b> · {PAY_TABS[flt]} ({data['counts'][flt]})\n\n" + ("\n".join(lines) or "Пока пусто.")
    tabs = [btn(("• " if k == flt else "") + f"{v} {data['counts'][k]}", f"a:py:{k}:0") for k, v in PAY_TABS.items()]
    nav = [btn("←", f"a:py:{flt}:{max(0, offset - PAGE)}") if offset else None,
           btn("→", f"a:py:{flt}:{offset + PAGE}") if data["more"] else None]
    await show(call, text, kb(tabs[:2], tabs[2:], buttons[:4], buttons[4:], nav, [back()]))
    await call.answer()


async def _payment(ctx: Context, event_id: int):
    return await ctx.db.fetchone(
        "SELECT e.*, p.qty, u.username, u.first_name, u.last_name FROM payment_events e "
        "LEFT JOIN payments p ON p.id=e.payload LEFT JOIN users u ON u.id=e.user_id WHERE e.id=?", (event_id,))


@router.callback_query(F.data.startswith("a:pc:"))
async def cb_payment_card(call: CallbackQuery, ctx: Context) -> None:
    await payment_card(call, ctx, int(call.data.split(":")[2]))
    await call.answer()


async def payment_card(call: CallbackQuery, ctx: Context, event_id: int, note: str = "") -> None:
    p = await _payment(ctx, event_id)
    if p is None:
        await show(call, "Платёж не найден", kb([back("a:py:all:0", "← Продажи")]))
        return
    text = (
        f"🧾 <b>Платёж #{p['id']}</b> {PAY_ICON.get(p['status'], '')}\n\n"
        f"Пользователь: {who(p, p['user_id'])}\n"
        f"Сумма: <b>{stars(p['amount'])}</b> · генераций {p['qty'] or '?'}\n"
        f"Статус: <code>{esc(p['status'])}</code> · {dt(p['created_at'])}\n"
        f"Провайдер: {esc(p['provider'])}\nCharge ID: <code>{esc(p['charge_id'])}</code>"
    )
    if note:
        text += f"\n\n{note}"
    can_refund = p["status"] in ("credited", "credited_repeat", "needs_review")
    await show(call, text, kb(
        [btn("↩️ Вернуть звёзды", f"a:prq:{event_id}", style="danger")] if can_refund else None,
        [btn("👤 Пользователь", f"a:uc:{p['user_id']}"), back("a:py:all:0", "← Продажи")],
    ))


@router.callback_query(F.data.startswith("a:prq:"))
async def cb_refund_ask(call: CallbackQuery, ctx: Context) -> None:
    event_id = int(call.data.split(":")[2])
    p = await _payment(ctx, event_id)
    if p is None:
        return await call.answer()
    await show(call, (f"↩️ Вернуть <b>{stars(p['amount'])}</b> пользователю {who(p, p['user_id'])}?\n\n"
                      f"С баланса спишется {p['qty'] or '?'} генераций. Если их уже потратили, возврат не пройдёт."),
               kb([btn("Да, вернуть", f"a:prd:{event_id}", style="danger"), btn("Нет", f"a:pc:{event_id}")]))
    await call.answer()


@router.callback_query(F.data.startswith("a:prd:"))
async def cb_refund_do(call: CallbackQuery, ctx: Context) -> None:
    event_id = int(call.data.split(":")[2])
    p = await _payment(ctx, event_id)
    if p is None:
        return await call.answer()
    await call.answer("Возвращаем…")
    try:
        ok, user_id, qty = await admin.refund(ctx, p["provider"], p["charge_id"])
    except services.RefundError as exc:
        reasons = {"no_bot": "бот не подключён к Telegram", "insufficient": "генерации уже потрачены",
                   "already": "уже возвращён", "unknown": "платёж не найден"}
        note = f"❌ Не получилось: {reasons.get(str(exc), str(exc))}"
    else:
        if ok is True:
            await admin.notify_refunded(ctx, user_id, qty)
            note = "✅ Звёзды возвращены, генерации списаны"
        elif ok is False:
            note = "❌ Telegram отказал в возврате — генерации вернули пользователю"
        else:
            note = "⏳ Telegram не ответил: платёж оставлен в статусе «возврат», проверь позже"
    await payment_card(call, ctx, event_id, note)


# ───────────────────────── пользователи ─────────────────────────

@router.callback_query(F.data == "a:u")
async def cb_find(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(St.find)
    await show(call, "👤 Пришли <b>ID</b>, <b>@ник</b> или перешли любое сообщение пользователя.\n"
                     "Можно часть имени — покажу совпадения.", cancel())
    await call.answer()


async def _resolve(ctx: Context, message: Message, query: str | None = None) -> list:
    origin = getattr(message, "forward_origin", None)
    sender = getattr(origin, "sender_user", None) if origin else None
    if sender is not None and query is None:
        row = await services.get_user(ctx.db, sender.id)
        return [row] if row else []
    q = (query if query is not None else message.text or "").strip()
    if not q:
        return []
    if q.lstrip("-").isdigit():
        row = await services.get_user(ctx.db, int(q))
        return [row] if row else []
    if q.startswith("@"):
        rows = await ctx.db.fetchall("SELECT * FROM users WHERE lower(username)=lower(?)", (q[1:],))
        if rows:
            return rows
    return (await admin.users(ctx.db, q, 10, 0))["items"]


@router.message(St.find)
async def msg_find(message: Message, ctx: Context, state: FSMContext) -> None:
    found = await _resolve(ctx, message)
    if not found:
        await message.answer("Никого не нашёл. Пришли ID, @ник или перешли сообщение.", reply_markup=cancel())
        return
    await state.clear()
    if len(found) == 1:
        await user_card(message, ctx, found[0]["id"])
        return
    rows = [[btn(f"{('@' + r['username']) if r['username'] else (r['first_name'] or r['id'])} · {r['id']}", f"a:uc:{r['id']}")]
            for r in found[:10]]
    await message.answer("Нашлось несколько — выбери:", reply_markup=kb(*rows, [back()]))


async def user_card(event, ctx: Context, uid: int, note: str = "") -> None:
    u = await services.get_user(ctx.db, uid)
    if u is None:
        await show(event, "Пользователь не найден", kb([back()]))
        return
    spent = await n(ctx, f"SELECT COALESCE(SUM(amount),0) FROM payment_events WHERE user_id=? AND status IN ({_in(PAID)})", (uid, *PAID))
    buys = await n(ctx, f"SELECT COUNT(*) FROM payment_events WHERE user_id=? AND status IN ({_in(PAID)})", (uid, *PAID))
    packs = await n(ctx, "SELECT COUNT(*) FROM packs WHERE user_id=?", (uid,))
    items = await n(ctx, "SELECT COALESCE(SUM(count),0) FROM packs WHERE user_id=?", (uid,))
    invited = await n(ctx, "SELECT COUNT(*) FROM users WHERE referred_by=?", (uid,))
    status = "🚫 в бане" if u["banned"] else ("💤 заблокировал бота" if u["blocked"] else "✅ активен")
    adm = " · 👑 админ" if runtime.is_admin(ctx, uid) else ""
    text = (
        f"👤 {who(u)}\n"
        f"{esc(u['first_name'] or '')} {esc(u['last_name'] or '')} · с {dt(u['created_at'])} · {status}{adm}\n\n"
        f"✦ Баланс: <b>{u['balance']}</b> · тариф <b>{'Pro' if u['plan'] == 'pro' else 'обычный'}</b>\n"
        f"💰 Покупок: {buys} на {stars(spent)} · куплено генераций {u['purchased_total']}\n"
        f"🎨 Паков: {packs} · элементов {items}\n"
        f"🤝 Пригласил: {invited}" + (f" · пришёл от <code>{u['referred_by']}</code>" if u["referred_by"] else "")
    )
    if "partner" in u.keys() and u["partner"]:
        text += f"\n🔗 Пришёл по партнёрской ссылке <code>{esc(u['partner'])}</code>"
    owned = await partners.owned_by(ctx.db, uid)
    if owned:
        text += "\n🔗 Партнёр: " + ", ".join(f"<code>{esc(r['code'])}</code>" for r in owned)
    if note:
        text += f"\n\n{note}"
    await show(event, text, kb(
        [btn("➕ Начислить", f"a:ug:{uid}:+", style="success"), btn("➖ Списать", f"a:ug:{uid}:-")],
        [btn("💎 Снять Pro" if u["plan"] == "pro" else "💎 Дать Pro", f"a:upro:{uid}"), btn("✉️ Написать", f"a:um:{uid}")],
        [btn("📦 Паки", f"a:upk:{uid}"), btn("🧾 Покупки", f"a:upy:{uid}"), btn("📜 Баланс", f"a:ul:{uid}")],
        [btn("✅ Разбанить", f"a:ub:{uid}") if u["banned"] else btn("🚫 Забанить", f"a:ub:{uid}", style="danger")],
        [btn("🔄 Обновить", f"a:uc:{uid}"), back()],
    ))


@router.callback_query(F.data.startswith("a:uc:"))
async def cb_user_card(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await user_card(call, ctx, int(call.data.split(":")[2]))
    await call.answer()


@router.callback_query(F.data.startswith("a:ub:"))
async def cb_ban(call: CallbackQuery, ctx: Context) -> None:
    uid = int(call.data.split(":")[2])
    u = await services.get_user(ctx.db, uid)
    if u is None:
        return await call.answer()
    if not u["banned"] and runtime.is_admin(ctx, uid):
        return await call.answer("Админа забанить нельзя", show_alert=True)
    await runtime.set_banned(ctx.db, uid, not u["banned"])
    await user_card(call, ctx, uid, "🚫 Забанен: бот и Mini App для него закрыты" if not u["banned"] else "✅ Разбанен")
    await call.answer()


@router.callback_query(F.data.startswith("a:upro:"))
async def cb_pro(call: CallbackQuery, ctx: Context) -> None:
    uid = int(call.data.split(":")[2])
    u = await services.get_user(ctx.db, uid)
    if u is None:
        return await call.answer()
    plan = "free" if u["plan"] == "pro" else "pro"
    await runtime.set_plan(ctx.db, uid, plan)
    await user_card(call, ctx, uid, "💎 Pro выдан" if plan == "pro" else "💎 Pro снят")
    await call.answer()


@router.callback_query(F.data.startswith("a:ug:"))
async def cb_grant_ask(call: CallbackQuery, state: FSMContext) -> None:
    _, _, uid, sign = call.data.split(":")
    await state.set_state(St.grant)
    await state.update_data(uid=int(uid), sign=sign)
    word = "начислить" if sign == "+" else "списать"
    await show(call, f"Сколько генераций {word}? Пришли число.", cancel(f"a:uc:{uid}"))
    await call.answer()


@router.message(St.grant, F.text)
async def msg_grant(message: Message, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    uid, sign = data["uid"], data["sign"]
    raw = message.text.strip()
    if not raw.isdigit() or not 1 <= int(raw) <= admin.GRANT_MAX:
        await message.answer(f"Нужно число от 1 до {admin.GRANT_MAX}", reply_markup=cancel(f"a:uc:{uid}"))
        return
    delta = int(raw) * (1 if sign == "+" else -1)
    try:
        balance = await admin.grant(ctx.db, uid, delta, message.from_user.id)
    except services.InsufficientBalance:
        await message.answer("Столько на балансе нет", reply_markup=cancel(f"a:uc:{uid}"))
        return
    except LookupError:
        await state.clear()
        await message.answer("Пользователь не найден", reply_markup=kb([back()]))
        return
    await state.clear()
    if delta > 0 and ctx.bot is not None:
        try:
            await message.bot.send_message(uid, f"🎁 Тебе начислено {delta} генераций. Баланс: {balance}")
        except Exception:
            pass
    await user_card(message, ctx, uid, f"{'➕' if delta > 0 else '➖'} {abs(delta)} генераций · баланс {balance}")


@router.callback_query(F.data.startswith("a:um:"))
async def cb_msg_ask(call: CallbackQuery, state: FSMContext) -> None:
    uid = int(call.data.split(":")[2])
    await state.set_state(St.message)
    await state.update_data(uid=uid)
    await show(call, "✉️ Пришли сообщение (текст, фото, что угодно) — бот отправит его пользователю от своего имени.",
               cancel(f"a:uc:{uid}"))
    await call.answer()


@router.message(St.message)
async def msg_to_user(message: Message, ctx: Context, state: FSMContext) -> None:
    uid = (await state.get_data())["uid"]
    await state.clear()
    try:
        await message.bot.copy_message(uid, message.chat.id, message.message_id)
        note = "✉️ Отправлено"
    except TelegramForbiddenError:
        await runtime.set_blocked(ctx.db, uid, True)
        note = "💤 Не доставлено: пользователь заблокировал бота"
    except TelegramBadRequest as err:
        note = f"❌ Не доставлено: {esc(str(err))}"
    await user_card(message, ctx, uid, note)


@router.callback_query(F.data.startswith("a:upk:"))
async def cb_user_packs(call: CallbackQuery, ctx: Context) -> None:
    uid = int(call.data.split(":")[2])
    rows = await ctx.db.fetchall("SELECT * FROM packs WHERE user_id=? ORDER BY id DESC LIMIT 15", (uid,))
    lines = [f"{PACK_ICON.get(r['status'], '·')} #{r['id']} · {dt(r['created_at'])} · "
             f"{'эмодзи' if r['type'] == 'emoji' else 'стикеры'} · {r['count']} шт." for r in rows]
    buttons = [btn(f"#{r['id']}", f"a:pkc:{r['id']}") for r in rows[:8]]
    await show(call, "📦 <b>Паки пользователя</b>\n\n" + ("\n".join(lines) or "Паков нет."),
               kb(buttons[:4], buttons[4:], [back(f"a:uc:{uid}", "← Пользователь")]))
    await call.answer()


@router.callback_query(F.data.startswith("a:upy:"))
async def cb_user_payments(call: CallbackQuery, ctx: Context) -> None:
    uid = int(call.data.split(":")[2])
    rows = await ctx.db.fetchall(
        "SELECT e.*, p.qty FROM payment_events e LEFT JOIN payments p ON p.id=e.payload "
        "WHERE e.user_id=? ORDER BY e.id DESC LIMIT 15", (uid,))
    lines = [f"{PAY_ICON.get(r['status'], '·')} #{r['id']} · {dt(r['created_at'])} · {stars(r['amount'])} · {r['qty'] or '?'} ген."
             for r in rows]
    buttons = [btn(f"#{r['id']}", f"a:pc:{r['id']}") for r in rows[:8]]
    await show(call, "🧾 <b>Покупки пользователя</b>\n\n" + ("\n".join(lines) or "Покупок нет."),
               kb(buttons[:4], buttons[4:], [back(f"a:uc:{uid}", "← Пользователь")]))
    await call.answer()


REASONS = {"signup": "подарок новичку", "purchase": "покупка", "pack": "пак", "refund": "возврат", "admin": "админ",
           "referral": "за друга", "partner": "партнёру за приведённого", "partner_bonus": "бонус по ссылке", "promo": "промокод", "pack_refund": "возврат за пак", "refund_revert": "отмена возврата"}


@router.callback_query(F.data.startswith("a:ul:"))
async def cb_user_ledger(call: CallbackQuery, ctx: Context) -> None:
    uid = int(call.data.split(":")[2])
    rows = await ctx.db.fetchall("SELECT * FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 20", (uid,))
    lines = [f"{'+' if r['delta'] > 0 else ''}{r['delta']} · {esc(REASONS.get(r['reason'], r['reason']))} · {dt(r['created_at'])}"
             for r in rows]
    await show(call, "📜 <b>Движение баланса</b>\n\n" + ("\n".join(lines) or "Пусто."),
               kb([back(f"a:uc:{uid}", "← Пользователь")]))
    await call.answer()


# ── быстрая выдача
@router.callback_query(F.data == "a:gv")
async def cb_give(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(St.give)
    await show(call, "🎁 Пришли <code>ID количество</code> или <code>@ник количество</code>.\n"
                     "Например: <code>123456789 10</code>", cancel())
    await call.answer()


@router.message(St.give, F.text)
async def msg_give(message: Message, ctx: Context, state: FSMContext) -> None:
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Формат: <code>ID количество</code>", parse_mode="HTML", reply_markup=cancel())
        return
    found = await _resolve(ctx, message, parts[0])
    if len(found) != 1:
        await message.answer("Пользователь не найден (он должен хотя бы раз открыть бота)", reply_markup=cancel())
        return
    qty = int(parts[1])
    if not 1 <= qty <= admin.GRANT_MAX:
        await message.answer(f"Количество от 1 до {admin.GRANT_MAX}", reply_markup=cancel())
        return
    uid = found[0]["id"]
    balance = await admin.grant(ctx.db, uid, qty, message.from_user.id)
    await state.clear()
    try:
        await message.bot.send_message(uid, f"🎁 Тебе начислено {qty} генераций. Баланс: {balance}")
    except Exception:
        pass
    await user_card(message, ctx, uid, f"🎁 Выдано {qty} генераций · баланс {balance}")


# ───────────────────────── паки ─────────────────────────

PACK_ICON = {"ready": "🟢", "processing": "⏳", "failed": "🔴"}
PACK_TABS = {"all": "все", "processing": "в работе", "ready": "готовы", "failed": "ошибки"}


@router.callback_query(F.data.startswith("a:pk:"))
async def cb_packs(call: CallbackQuery, ctx: Context) -> None:
    _, _, flt, off = call.data.split(":")
    flt = flt if flt in admin.PACK_FILTERS else "all"
    offset = max(0, int(off))
    data = await admin.packs(ctx.db, flt, PAGE, offset)
    lines, buttons = [], []
    for p in data["items"]:
        u = p["user"]
        name = f"@{u['username']}" if u["username"] else (u["first_name"] or str(u["id"]))
        extra = f" · сбоев {p['failed']}" if p.get("failed") else ""
        lines.append(f"{PACK_ICON.get(p['status'], '·')} <b>#{p['id']}</b> · {dt(p['created_at'])} · {esc(name)} · "
                     f"{'эмодзи' if p['type'] == 'emoji' else 'стикеры'} {p['count']}{extra}")
        buttons.append(btn(f"#{p['id']}", f"a:pkc:{p['id']}"))
    text = f"📦 <b>Паки</b> · {PACK_TABS[flt]} ({data['counts'][flt]})\n\n" + ("\n".join(lines) or "Пока пусто.")
    tabs = [btn(("• " if k == flt else "") + f"{v} {data['counts'][k]}", f"a:pk:{k}:0") for k, v in PACK_TABS.items()]
    nav = [btn("←", f"a:pk:{flt}:{max(0, offset - PAGE)}") if offset else None,
           btn("→", f"a:pk:{flt}:{offset + PAGE}") if data["more"] else None]
    await show(call, text, kb(tabs[:2], tabs[2:], buttons[:4], buttons[4:], nav, [back()]))
    await call.answer()


async def pack_card(event, ctx: Context, pack_id: int, note: str = "") -> None:
    row = await ctx.db.fetchone("SELECT p.*, u.username, u.first_name, u.last_name FROM packs p "
                                "LEFT JOIN users u ON u.id=p.user_id WHERE p.id=?", (pack_id,))
    if row is None:
        await show(event, "Пак не найден", kb([back("a:pk:all:0", "← Паки")]))
        return
    v = services.pack_view(row)
    params = v.get("params") or {}
    text = (
        f"📦 <b>Пак #{row['id']}</b> {PACK_ICON.get(row['status'], '')} {esc(row['status'])}\n\n"
        f"Пользователь: {who(row, row['user_id'])}\n"
        f"Тип: {'эмодзи' if row['type'] == 'emoji' else 'стикеры'} · элементов {row['count']} · готово {v['done']}"
        + (f" · сбоев {v['failed']}" if v["failed"] else "") + "\n"
        f"Ник: <b>{esc(params.get('text') or '—')}</b> · заливка {esc(params.get('color') or 'родная')}"
        f" · обводка {esc(params.get('stroke') or 'нет')}\n"
        f"Создан: {dt(row['created_at'])}" + (f" · готов {dt(row['ready_at'])}" if row["ready_at"] else "") + "\n"
        + (f"Набор: <code>{esc(row['set_name'])}</code>\n" if row["set_name"] else "")
        + (f"Ошибка: <code>{esc(row['error'])}</code>\n" if row["error"] else "")
    )
    if note:
        text += f"\n{note}"
    link = row["link"] if (row["link"] or "").startswith("https://") else None
    await show(event, text, kb(
        [btn("🔗 Открыть набор", url=link)] if link else None,
        [btn("▶️ Дособрать сейчас", f"a:pkr:{pack_id}", style="primary")] if row["status"] == "processing" else None,
        [btn("👤 Пользователь", f"a:uc:{row['user_id']}"), back("a:pk:all:0", "← Паки")],
    ))


@router.callback_query(F.data.startswith("a:pkc:"))
async def cb_pack_card(call: CallbackQuery, ctx: Context) -> None:
    await pack_card(call, ctx, int(call.data.split(":")[2]))
    await call.answer()


@router.callback_query(F.data.startswith("a:pkr:"))
async def cb_pack_resume(call: CallbackQuery, ctx: Context) -> None:
    pack_id = int(call.data.split(":")[2])
    ctx.runner.start(pack_id, 0.0)  # уже идущую сборку PackRunner не дублирует
    await pack_card(call, ctx, pack_id, "▶️ Сборка запущена — обнови карточку через минуту")
    await call.answer()


# ───────────────────────── настройки ─────────────────────────

@router.callback_query(F.data.in_({"a:sub", "a:sg:subscription"}))
async def cb_subscription(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    s = ctx.settings
    rows = [[btn(f"{t.label}: {runtime.show_value(t, getattr(s, t.key))}", f"a:se:{t.key}")]
            for t in runtime.TUNABLES if t.group == "subscription"]
    rows += [[btn(texts.MSG_DEFS[key].title, f"a:msg:{key}")] for key in texts.MSG_DEFS if key.startswith("subscription")]
    rows += [
        [btn(f"🔘 Кнопка «{texts.BTN_DEFS['sub_join'].title}»", "a:btn:sub_join")],
        [btn(f"🔘 Кнопка «{texts.BTN_DEFS['sub_check'].title}»", "a:btn:sub_check")],
    ]
    rows += [[btn("👁 Предпросмотр", "a:subp")], [back()]]
    await show(call,
               "🔒 <b>Обязательная подписка</b>\n\n"
               f"Статус: <b>{'включена' if s.subscription_required else 'выключена'}</b>\n\n"
               "1. Добавь бота администратором канала.\n"
               "2. Укажи @username канала или ID -100… для закрытого.\n"
               "3. Для закрытого канала укажи пригласительную ссылку.\n"
               "4. Настрой текст и включи требование подписки.\n\n"
               "До подписки пользователь видит сообщение и две кнопки: канал и проверка. "
               "Админы и поддержка доступны всегда. Изменения сохраняются без перезапуска.", kb(*rows))
    await call.answer()


@router.callback_query(F.data == "a:subp")
async def cb_subscription_preview(call: CallbackQuery, ctx: Context) -> None:
    from .handlers import send_html, subscription_keyboard

    if not subscription.channel_url(ctx.settings):
        await call.answer("Сначала укажи канал и ссылку на него", show_alert=True)
        return
    await call.answer()
    await send_html(call.message, subscription.payload(ctx)["message"], reply_markup=subscription_keyboard(ctx))


@router.callback_query(F.data == "a:set")
async def cb_settings(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    s = ctx.settings
    changed = len(ctx.overrides)
    text = (
        "💲 <b>Цены и лимиты</b>\n\n"
        f"Цена: <b>{s.price_rub} ₽</b> за генерацию · от {s.bulk_discount_from} шт. −{s.bulk_discount_pct}% · "
        f"от {s.bulk_unit_from} шт. по {s.bulk_unit_rub} ₽\n"
        f"Оплата Звёздами: 1 ⭐ = {s.rub_per_star:g} ₽\n"
        f"Подарок новичку: {s.signup_gens} · за друга: {s.referral_reward}\n"
        f"Пак: эмодзи до {s.emoji_pack_max}, стикеров до {s.sticker_pack_max} · ник до {s.text_max} символов\n\n"
        "Изменения действуют сразу, без перезапуска."
        + (f"\nИзменено из админки: <b>{changed}</b>" if changed else "")
    )
    await show(call, text, kb(*[[btn(label, f"a:sg:{key}")] for key, label in runtime.GROUPS.items()], [back()]))
    await call.answer()


@router.callback_query(F.data.startswith("a:sg:"))
async def cb_settings_group(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    group = call.data.split(":")[2]
    rows = []
    for t in runtime.TUNABLES:
        if t.group != group:
            continue
        mark = " ✎" if t.key in ctx.overrides else ""
        rows.append([btn(f"{t.label}: {runtime.show_value(t, getattr(ctx.settings, t.key))}{mark}", f"a:se:{t.key}")])
    await show(call, f"{runtime.GROUPS.get(group, '')}\n\nНажми на параметр, чтобы изменить. ✎ — изменён из админки.",
               kb(*rows, [back("a:set", "← Цены и лимиты")]))
    await call.answer()


async def setting_card(event, ctx: Context, key: str, note: str = "") -> None:
    t = runtime.BY_KEY[key]
    cur = getattr(ctx.settings, key)
    default = getattr(runtime.base(ctx), key)
    text = (f"⚙️ <b>{esc(t.label)}</b>\n\nСейчас: <b>{esc(runtime.show_value(t, cur))}</b>\n"
            f"Из .env: {esc(runtime.show_value(t, default))}")
    if t.kind == "int":
        text += f"\nДопустимо: {t.lo}–{t.hi}"
    if t.hint:
        text += f"\n<i>{esc(t.hint)}</i>"
    if note:
        text += f"\n\n{note}"
    rows = []
    if t.kind == "bool":
        rows.append([btn("Выключить" if cur else "Включить", f"a:sb:{key}", style="danger" if cur else "success")])
    else:
        rows.append([btn("✏️ Изменить", f"a:sx:{key}", style="primary")])
    if key in ctx.overrides:
        rows.append([btn("↺ Как в .env", f"a:sr:{key}")])
    rows.append([back(f"a:sg:{t.group}", "← Назад")])
    await show(event, text, kb(*rows))


@router.callback_query(F.data.startswith("a:se:"))
async def cb_setting(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    key = call.data.split(":")[2]
    if key not in runtime.BY_KEY:
        return await call.answer()
    await setting_card(call, ctx, key)
    await call.answer()


@router.callback_query(F.data.startswith("a:sb:"))
async def cb_setting_toggle(call: CallbackQuery, ctx: Context) -> None:
    key = call.data.split(":")[2]
    try:
        await runtime.set_setting(ctx, key, not getattr(ctx.settings, key))
        note = "✅ Сохранено"
    except ValueError as exc:
        note = f"❌ {esc(str(exc))}"
    await setting_card(call, ctx, key, note)
    await call.answer()


@router.callback_query(F.data.startswith("a:sr:"))
async def cb_setting_reset(call: CallbackQuery, ctx: Context) -> None:
    key = call.data.split(":")[2]
    try:
        await runtime.set_setting(ctx, key, None)
        note = "↺ Вернули значение из .env"
    except ValueError as exc:
        note = f"❌ {esc(str(exc))}"
    await setting_card(call, ctx, key, note)
    await call.answer()


@router.callback_query(F.data.startswith("a:sx:"))
async def cb_setting_edit(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":")[2]
    t = runtime.BY_KEY[key]
    await state.set_state(St.setting)
    await state.update_data(key=key)
    hint = {
        "channel": "Пришли @username канала, https://t.me/username или ID -100…; «-» — очистить.",
        "telegram_url": "Пришли https://t.me/username или пригласительную ссылку https://t.me/+…; «-» — очистить.",
        "str": "Пришли @username или «-», чтобы убрать.",
        "float": "Пришли число, например 1.25",
        "token": "Пришли API токен / ключ или «-», чтобы очистить.",
        "str_any": "Пришли значение (например, mainnet или testnet) или «-», чтобы очистить.",
    }.get(t.kind, f"Пришли число от {t.lo} до {t.hi}.")
    await show(call, f"✏️ <b>{esc(t.label)}</b>\n\n{hint}", cancel(f"a:se:{key}"))
    await call.answer()


@router.message(St.setting, F.text)
async def msg_setting(message: Message, ctx: Context, state: FSMContext) -> None:
    key = (await state.get_data())["key"]
    t = runtime.BY_KEY[key]
    try:
        value = runtime.parse(t, message.text)
        await runtime.set_setting(ctx, key, value)
    except ValueError as exc:
        await message.answer(f"❌ {exc}", reply_markup=cancel(f"a:se:{key}"))
        return
    await state.clear()
    await setting_card(message, ctx, key, "✅ Сохранено, уже действует")


# ───────────────────────── промокоды ─────────────────────────

PROMO_ICON = {"active": "✅", "paused": "⏸", "expired": "⌛", "exhausted": "⛔"}
PROMO_STATE = {"active": "действует", "paused": "на паузе", "expired": "срок вышел", "exhausted": "активации закончились"}
PROMO_PAGE = 8

# поле конструктора → (подсказка для ввода, разбор ответа)
PROMO_FIELDS = {
    "code": "🔤 Пришли свой код (латиница, цифры, _ и -, 3–32 символа).\n«-» — случайный код.",
    "prefix": "🏷 Приставка случайного кода, например <code>EZZ</code> → <code>EZZ7K2QX9M</code>.\n«-» — без приставки.",
    "gens": "🎁 Сколько генераций даёт код:\n• <code>10</code> — ровно 10\n• <code>5-20</code> — случайно от 5 до 20 каждому\n• <code>0</code> — без генераций (только скидка)",
    "discount": "🏷 Процент скидки на покупки (от 1 до 99):\n• <code>20</code> — скидка 20%\n• <code>0</code> или <code>-</code> — без скидки.",
    "uses": "👥 Сколько раз можно активировать каждый код (каждый человек — один раз), например <code>100</code>.",
    "days": "⏳ Сколько дней действует код, например <code>7</code>.\n<code>0</code> — бессрочно.",
    "count": "📦 Сколько разных кодов создать за раз (1–500). Больше одного — пришлю их файлом.",
}


def _draft(data: dict) -> runtime.PromoDraft:
    raw = data.get("draft") or {}
    return runtime.PromoDraft(**{k: v for k, v in raw.items() if k in runtime.PromoDraft.__dataclass_fields__})


async def _save_draft(state: FSMContext, draft: runtime.PromoDraft) -> None:
    await state.update_data(draft=dataclasses.asdict(draft))


def _until(ts) -> str:
    return datetime.datetime.fromtimestamp(int(ts)).strftime("%d.%m.%Y %H:%M") if ts else "бессрочно"


def _promo_line(r) -> str:
    st = runtime.promo_state(r)
    parts = []
    if r["gens"]:
        parts.append(f"+{r['gens']}–{r['gens_max']}" if r["gens_max"] else f"+{r['gens']}")
    if "discount_pct" in r.keys() and r["discount_pct"]:
        parts.append(f"−{r['discount_pct']}%")
    reward = " · ".join(parts) or "0"
    extra = " · 🆕" if r["only_new"] else ""
    return f"{PROMO_ICON[st]} <code>{esc(r['code'])}</code> · {reward} · {r['uses']}/{r['uses_max']}{extra}"


@router.callback_query(F.data == "a:pr")
async def cb_promos(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await promos_screen(call, ctx, 0)
    await call.answer()


@router.callback_query(F.data.startswith("a:prl:"))
async def cb_promos_page(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await promos_screen(call, ctx, int(call.data.split(":")[2] or 0))
    await call.answer()


async def promos_screen(call: CallbackQuery, ctx: Context, offset: int = 0) -> None:
    rows = await runtime.list_promos(ctx.db, offset, PROMO_PAGE + 1)
    more = len(rows) > PROMO_PAGE
    rows = rows[:PROMO_PAGE]
    total = await n(ctx, "SELECT COUNT(*) FROM promos")
    used = await n(ctx, "SELECT COUNT(*) FROM promo_uses")
    given = await n(ctx, "SELECT COALESCE(SUM(delta),0) FROM ledger WHERE reason='promo'")
    text = ("🎟 <b>Промокоды</b>\n"
            f"Кодов: <b>{total}</b> · активаций: <b>{used}</b> · выдано генераций: <b>{given}</b>\n"
            "Пользователь активирует: <code>/promo КОД</code> или в профиле Mini App.\n\n"
            + ("\n".join(_promo_line(r) for r in rows) or "Пока нет ни одного."))
    buttons = [btn(f"{PROMO_ICON[runtime.promo_state(r)]} {r['code']}", f"a:prc:{r['code']}") for r in rows]
    nav = [btn("←", f"a:prl:{max(0, offset - PROMO_PAGE)}") if offset else None,
           btn("→", f"a:prl:{offset + PROMO_PAGE}") if more else None]
    await show(call, text, kb([btn("➕ Создать промокод", "a:prn", style="success")],
                              *[buttons[i:i + 2] for i in range(0, len(buttons), 2)], nav, [back()]))


def _draft_text(d: runtime.PromoDraft, note: str = "") -> str:
    code = f"<code>{esc(d.code)}</code>" if d.code else ("случайный" + (f", приставка <code>{esc(d.prefix)}</code>" if d.prefix else ""))
    return ((f"{note}\n\n" if note else "")
            + "➕ <b>Новый промокод</b>\n\n"
            f"🔤 Код: <b>{code}</b>\n"
            f"🎁 Награда: <b>{d.reward_text()}</b>\n"
            f"🏷 Скидка на покупки: <b>{f'{d.discount_pct}%' if d.discount_pct else 'нет'}</b>\n"
            f"👥 Активаций на код: <b>{d.uses}</b>\n"
            f"⏳ Срок: <b>{f'{d.days} дн.' if d.days else 'бессрочно'}</b>\n"
            f"🆕 Кому: <b>{'только новичкам (без покупок)' if d.only_new else 'всем'}</b>\n"
            f"📦 Кодов за раз: <b>{d.count}</b>\n\n"
            "Нажми на параметр, чтобы поменять. Быстрый способ — одной строкой: "
            "<code>КОД ГЕНЕРАЦИЙ АКТИВАЦИЙ</code> (например <code>EZZ10 10 100</code>) "
            "или со скидкой: <code>КОД СКИДКА% АКТИВАЦИЙ</code> (например <code>SALE20 20% 100</code>).")


def _draft_markup(d: runtime.PromoDraft) -> InlineKeyboardMarkup:
    return kb(
        [btn("🔤 Код", "a:prf:code"), btn("🏷 Приставка", "a:prf:prefix")],
        [btn("🎁 Генерации", "a:prf:gens"), btn(f"🏷 Скидка: {d.discount_pct}%" if d.discount_pct else "🏷 Скидка: нет", "a:prf:discount")],
        [btn("👥 Активации", "a:prf:uses"), btn("⏳ Срок", "a:prf:days")],
        [btn("📦 Сколько кодов", "a:prf:count"), btn("🆕 Только новичкам: " + ("да" if d.only_new else "нет"), "a:prt")],
        [btn("✅ Создать", "a:prok", style="success")],
        [btn("✕ Отмена", "a:pr")],
    )


async def draft_screen(event, state: FSMContext, note: str = "") -> None:
    d = _draft(await state.get_data())
    await state.set_state(St.promo)
    await show(event, _draft_text(d, note), _draft_markup(d))


@router.callback_query(F.data == "a:prn")
async def cb_promo_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _save_draft(state, runtime.PromoDraft())
    await draft_screen(call, state)
    await call.answer()


@router.callback_query(F.data.startswith("a:prf:"))
async def cb_promo_field(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":")[2]
    if key not in PROMO_FIELDS:
        await call.answer()
        return
    await state.update_data(field=key)
    await state.set_state(St.promo_field)
    await show(call, PROMO_FIELDS[key], kb([btn("← К промокоду", "a:prb")]))
    await call.answer()


@router.callback_query(F.data == "a:prb")
async def cb_promo_back(call: CallbackQuery, state: FSMContext) -> None:
    if "draft" not in await state.get_data():
        await _save_draft(state, runtime.PromoDraft())
    await draft_screen(call, state)
    await call.answer()


@router.callback_query(F.data == "a:prt")
async def cb_promo_toggle_new(call: CallbackQuery, state: FSMContext) -> None:
    d = _draft(await state.get_data())
    d.only_new = not d.only_new
    await _save_draft(state, d)
    await draft_screen(call, state)
    await call.answer()


def _apply_field(d: runtime.PromoDraft, key: str, raw: str) -> None:
    raw = raw.strip()
    if key in ("code", "prefix"):
        value = "" if raw == "-" else runtime.clean_code(raw)
        setattr(d, key, value)
        if key == "code" and value:
            d.count = 1
        return
    if key == "discount":
        val_str = raw.rstrip("%").strip()
        if val_str in ("-", "0"):
            d.discount_pct = 0
            return
        if not val_str.isdigit():
            raise ValueError("нужно число процентов (1-99) или 0")
        val = int(val_str)
        if not 1 <= val <= 99:
            raise ValueError("скидка от 1 до 99%")
        d.discount_pct = val
        if d.gens == 5 and d.gens_max == 0:
            d.gens = 0
        return
    if key == "gens":
        if raw in ("0", "-"):
            d.gens, d.gens_max = 0, 0
            return
        lo, sep, hi = raw.replace("–", "-").replace(" ", "").partition("-")
        if not lo.isdigit() or (sep and not hi.isdigit()):
            raise ValueError("нужно число (10) или диапазон (5-20), либо 0")
        d.gens, d.gens_max = int(lo), (int(hi) if sep else 0)
        if d.gens_max and d.gens_max <= d.gens:
            raise ValueError("в диапазоне «от» должно быть меньше «до»")
        return
    if not raw.isdigit():
        raise ValueError("нужно целое число")
    setattr(d, key, int(raw))
    if key == "count" and d.count > 1:
        d.code = ""


@router.message(St.promo_field, F.text)
async def msg_promo_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    d = _draft(data)
    key = data.get("field", "")
    try:
        _apply_field(d, key, message.text)
        d.check()
    except ValueError as exc:
        await message.answer(f"❌ {exc}", reply_markup=kb([btn("← К промокоду", "a:prb")]))
        return
    await _save_draft(state, d)
    await draft_screen(message, state, "✅ Сохранено")


@router.message(St.promo, F.text)
async def msg_promo(message: Message, ctx: Context, state: FSMContext) -> None:
    """Быстрое создание одной строкой: КОД ГЕНЕРАЦИЙ АКТИВАЦИЙ или КОД СКИДКА% АКТИВАЦИЙ."""
    parts = message.text.split()
    if len(parts) == 3 and parts[2].isdigit():
        code_str = None if parts[0] == "-" else parts[0]
        uses_count = int(parts[2])
        p1 = parts[1].strip()
        try:
            if p1.endswith("%") and p1[:-1].isdigit():
                disc = int(p1[:-1])
                code = await runtime.create_promo(ctx.db, code_str, 0, uses_count, message.from_user.id, discount_pct=disc)
                await state.clear()
                await message.answer(f"✅ Промокод <code>{esc(code)}</code> создан: скидка {disc}%, {uses_count} активаций.\n"
                                     f"Активация: <code>/promo {esc(code)}</code>", parse_mode="HTML",
                                     reply_markup=kb([btn("🎟 Открыть", f"a:prc:{code}"), back("a:pr", "← Промокоды")]))
                return
            elif p1.isdigit():
                code = await runtime.create_promo(ctx.db, code_str, int(p1), uses_count, message.from_user.id)
                await state.clear()
                await message.answer(f"✅ Промокод <code>{esc(code)}</code> создан: +{p1} генераций, {uses_count} активаций.\n"
                                     f"Активация: <code>/promo {esc(code)}</code>", parse_mode="HTML",
                                     reply_markup=kb([btn("🎟 Открыть", f"a:prc:{code}"), back("a:pr", "← Промокоды")]))
                return
        except ValueError as exc:
            await message.answer(f"❌ {exc}", reply_markup=kb([btn("← К промокоду", "a:prb")]))
            return
    await message.answer("Формат: КОД ГЕНЕРАЦИЙ АКТИВАЦИЙ (например EZZ10 10 100) или со скидкой (SALE20 20% 100). Или настрой кнопками выше.",
                         reply_markup=kb([btn("← К промокоду", "a:prb")]))


@router.callback_query(F.data == "a:prok")
async def cb_promo_create(call: CallbackQuery, ctx: Context, state: FSMContext, bot: Bot) -> None:
    d = _draft(await state.get_data())
    try:
        codes = await runtime.create_promos(ctx.db, d, call.from_user.id)
    except ValueError as exc:
        await call.answer(f"❌ {exc}", show_alert=True)
        return
    await state.clear()
    summary = (f"{d.reward_text()} · {d.uses} активаций"
               + (f" · {d.days} дн." if d.days else "") + (" · только новичкам" if d.only_new else ""))
    if len(codes) == 1:
        await show(call, f"✅ Промокод <code>{esc(codes[0])}</code> создан\n{summary}\n\n"
                         f"Активация: <code>/promo {esc(codes[0])}</code> или в профиле Mini App.",
                   kb([btn("🎟 Открыть", f"a:prc:{codes[0]}"), back("a:pr", "← Промокоды")]))
    else:
        data = "\n".join(codes).encode()
        await bot.send_document(call.from_user.id, BufferedInputFile(data, filename=f"promo-{len(codes)}.txt"),
                                caption=f"📦 {len(codes)} промокодов: {summary}")
        await show(call, f"✅ Создано кодов: <b>{len(codes)}</b>\n{summary}\n\nСписок — файлом выше. Первые:\n"
                         + "\n".join(f"<code>{esc(c)}</code>" for c in codes[:10]),
                   kb([back("a:pr", "← Промокоды")]))
    await call.answer("Готово")


async def promo_card(call: CallbackQuery, ctx: Context, code: str, note: str = "") -> None:
    r = await runtime.get_promo(ctx.db, code)
    if r is None:
        await promos_screen(call, ctx)
        return
    st = runtime.promo_state(r)
    reward_parts = []
    if r["gens"]:
        reward_parts.append(f"{r['gens']}–{r['gens_max']} (случайно)" if r["gens_max"] else f"{r['gens']} генераций")
    if "discount_pct" in r.keys() and r["discount_pct"]:
        reward_parts.append(f"скидка {r['discount_pct']}%")
    reward = " + ".join(reward_parts) or "0 генераций"
    given = await n(ctx, "SELECT COALESCE(SUM(gens),0) FROM promo_uses WHERE code=?", (code,))
    users = await runtime.promo_users(ctx.db, code)
    lines = [f"• {who(u, u['user_id'])}"
             f" · +{u['gens'] or '?'} · {dt(u['created_at'])}" for u in users]
    text = ((f"{note}\n\n" if note else "")
            + f"🎟 <code>{esc(r['code'])}</code> — {PROMO_ICON[st]} {PROMO_STATE[st]}\n\n"
            f"🎁 Награда: <b>{reward}</b>\n"
            f"👥 Активации: <b>{r['uses']}/{r['uses_max']}</b> · выдано генераций: <b>{given}</b>\n"
            f"⏳ Действует до: <b>{_until(r['expires_at'])}</b>\n"
            f"🆕 Кому: <b>{'только новичкам' if r['only_new'] else 'всем'}</b>\n"
            f"🗓 Создан: {dt(r['created_at'])}" + (f" · пачка <code>{esc(r['batch'])}</code>" if r["batch"] else "")
            + ("\n\n<b>Последние активации:</b>\n" + "\n".join(lines) if lines else ""))
    await show(call, text, kb(
        [btn("▶️ Включить" if not r["active"] else "⏸ На паузу", f"a:prp:{code}"),
         btn("🗑 Удалить", f"a:prz:{code}", style="danger")],
        [back("a:pr", "← Промокоды")]))


@router.callback_query(F.data.startswith("a:prc:"))
async def cb_promo_card(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await promo_card(call, ctx, call.data.split(":", 2)[2])
    await call.answer()


@router.callback_query(F.data.startswith("a:prp:"))
async def cb_promo_pause(call: CallbackQuery, ctx: Context) -> None:
    code = call.data.split(":", 2)[2]
    r = await runtime.get_promo(ctx.db, code)
    if r is not None:
        await runtime.set_promo_active(ctx.db, code, not r["active"])
    await promo_card(call, ctx, code)
    await call.answer("Включён" if r is not None and not r["active"] else "На паузе")


@router.callback_query(F.data.startswith("a:prz:"))  # не a:prd: — это префикс возврата платежа
async def cb_promo_delete_ask(call: CallbackQuery) -> None:
    code = call.data.split(":", 2)[2]
    await show(call, f"🗑 Удалить промокод <code>{esc(code)}</code>? Уже начисленные генерации останутся у людей.",
               kb([btn("🗑 Да, удалить", f"a:prx:{code}", style="danger"), btn("Отмена", f"a:prc:{code}")]))
    await call.answer()


@router.callback_query(F.data.startswith("a:prx:"))
async def cb_promo_delete(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await runtime.delete_promo(ctx.db, call.data.split(":", 2)[2])
    await promos_screen(call, ctx)
    await call.answer("Удалён")


# ───────────────────────── партнёрские ссылки (app/partners.py) ─────────────────────────

LINK_ICON = {"active": "🟢", "paused": "⏸", "expired": "⌛"}
LINK_STATE = {"active": "работает", "paused": "на паузе", "expired": "срок вышел"}
LINK_PAGE = 8
LINK_FIELDS = {
    "name": "📝 <b>Название</b> — для тебя, например <code>Вася · TikTok</code>.",
    "code": ("🔤 <b>Код в ссылке</b>: латиница, цифры и _, от 3 до 32 символов, например <code>vasya</code> → "
             "ссылка <code>?start=p_vasya</code>.\n<code>-</code> — случайный код."),
    "owner": ("👤 <b>Партнёр</b> — кто получает награду: ID, @ник или перешли его сообщение. Он должен хотя бы раз "
              "запустить бота.\n<code>-</code> — без партнёра (только бонус новичкам и статистика)."),
    "bonus": "🎁 <b>Бонус новичку</b>: сколько генераций получает новый пользователь по ссылке (сверх обычного подарка).\n<code>0</code> — без бонуса.",
    "reward": "💎 <b>Награда партнёру</b>: сколько генераций за каждого приведённого.\n<code>0</code> — без награды.",
    "cap": "🔢 <b>Лимит наград партнёру</b>: сколько человек максимум оплатить.\n<code>0</code> — без лимита.",
    "uses": ("👥 <b>Лимит бонусов новичкам</b>: сколько первых новичков получат бонус. Статистика считается по всем.\n"
             "<code>0</code> — без лимита."),
    "days": "⏳ <b>Срок</b>: сколько дней с создания действуют бонус и награда (статистика копится и потом).\n<code>0</code> — бессрочно.",
}
REWARD_ORDER = ("signup", "pack", "purchase")


def _link_draft(data: dict) -> partners.Draft:
    raw = data.get("link_draft") or {}
    return partners.Draft(**{k: v for k, v in raw.items() if k in partners.Draft.__dataclass_fields__})


async def _save_link_draft(state: FSMContext, d: partners.Draft) -> None:
    await state.update_data(link_draft=dataclasses.asdict(d))


def _pct(a: int, b: int) -> str:
    return f"{round(a * 100 / b)}%" if b else "—"


def _rub(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ") + " ₽"


async def links_screen(call: CallbackQuery, ctx: Context, offset: int = 0) -> None:
    rows = await partners.list_links(ctx.db, offset, LINK_PAGE + 1)
    more = len(rows) > LINK_PAGE
    rows = rows[:LINK_PAGE]
    total = await n(ctx, "SELECT COUNT(*) FROM partner_links")
    joined = await n(ctx, "SELECT COUNT(*) FROM users WHERE partner IS NOT NULL")
    joined_day = await n(ctx, "SELECT COUNT(*) FROM users WHERE partner IS NOT NULL AND created_at>=?", (services.now() - DAY,))
    given = await n(ctx, "SELECT COALESCE(SUM(delta),0) FROM ledger WHERE reason IN ('partner','partner_bonus')")
    lines = [f"{LINK_ICON[partners.state(r)]} <b>{esc(r['name'])}</b> · <code>{r['code']}</code> · "
             f"👆{r['clicks']} · 👥{r['joined']}" for r in rows]
    text = ("🔗 <b>Партнёрские ссылки</b>\n"
            "Для рекламы у блогеров и в каналах: у каждой ссылки свой бонус новичкам, награда партнёру и статистика. "
            "Партнёр видит свою статистику командой /partner.\n\n"
            f"Ссылок: <b>{total}</b> · пришло людей: <b>{joined}</b> (за сутки +{joined_day}) · выдано генераций: <b>{given}</b>\n\n"
            + ("\n".join(lines) or "Пока ни одной — создай первую."))
    buttons = [btn(f"{LINK_ICON[partners.state(r)]} {r['name'][:24]}", f"a:lkc:{r['code']}") for r in rows]
    nav = [btn("←", f"a:lkl:{max(0, offset - LINK_PAGE)}") if offset else None,
           btn("→", f"a:lkl:{offset + LINK_PAGE}") if more else None]
    await show(call, text, kb([btn("➕ Создать ссылку", "a:lkn", style="success")],
                              *[buttons[i:i + 2] for i in range(0, len(buttons), 2)], nav, [back()]))


@router.callback_query(F.data == "a:lk")
async def cb_links(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await links_screen(call, ctx)
    await call.answer()


@router.callback_query(F.data.startswith("a:lkl:"))
async def cb_links_page(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await links_screen(call, ctx, int(call.data.split(":")[2] or 0))
    await call.answer()


async def _owner_text(ctx: Context, owner_id: int) -> str:
    if not owner_id:
        return "нет"
    return who(await services.get_user(ctx.db, owner_id), owner_id)


async def link_draft_screen(event, ctx: Context, state: FSMContext, note: str = "") -> None:
    data = await state.get_data()
    d = _link_draft(data)
    editing = data.get("link_edit")
    await state.set_state(St.link)
    code = f"<code>{esc(d.code)}</code>" if d.code else "случайный"
    reward = (f"+{d.reward} {partners.REWARD_ON[d.reward_on]}" + (f", не больше {d.reward_cap} раз" if d.reward_cap else "")
              if d.reward else "нет")
    text = ((f"{note}\n\n" if note else "")
            + (f"✏️ <b>Ссылка <code>{esc(editing)}</code></b>\n\n" if editing else "➕ <b>Новая партнёрская ссылка</b>\n\n")
            + f"📝 Название: <b>{esc(d.name) or '— не задано —'}</b>\n"
            + ("" if editing else f"🔤 Код: <b>{code}</b>\n")
            + f"👤 Партнёр: <b>{await _owner_text(ctx, d.owner_id)}</b>\n"
            f"🎁 Бонус новичку: <b>{f'+{d.bonus_new}' if d.bonus_new else 'нет'}</b>"
            + (f" (первым {d.uses_max})" if d.bonus_new and d.uses_max else "") + "\n"
            f"💎 Награда партнёру: <b>{reward}</b>\n"
            f"⏳ Срок: <b>{f'{d.days} дн.' if d.days else 'бессрочно'}</b>\n\n"
            "Нажми на параметр, чтобы поменять. Быстро: пришли одной строкой "
            "<code>НАЗВАНИЕ</code> — создам ссылку без бонусов, только со статистикой.\n"
            "💡 Награду надёжнее давать «за первый пак» или «за покупку»: регистрации легко накрутить фейками.")
    markup = kb(
        [btn("📝 Название", "a:lkf:name"), None if editing else btn("🔤 Код", "a:lkf:code")],
        [btn("👤 Партнёр", "a:lkf:owner"), btn("🎁 Бонус новичку", "a:lkf:bonus")],
        [btn("💎 Награда партнёру", "a:lkf:reward"), btn(f"⚡ Условие: {partners.REWARD_ON[d.reward_on]}", "a:lkt")],
        [btn("🔢 Лимит наград", "a:lkf:cap"), btn("👥 Лимит бонусов", "a:lkf:uses")],
        [btn("⏳ Срок", "a:lkf:days")],
        [btn("✅ Сохранить" if editing else "✅ Создать", "a:lkok", style="success")],
        [btn("✕ Отмена", f"a:lkc:{editing}" if editing else "a:lk")],
    )
    await show(event, text, markup)


@router.callback_query(F.data == "a:lkn")
async def cb_link_new(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await _save_link_draft(state, partners.Draft())
    await link_draft_screen(call, ctx, state)
    await call.answer()


@router.callback_query(F.data.startswith("a:lke:"))
async def cb_link_edit(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    code = call.data.split(":", 2)[2]
    r = await partners.get(ctx.db, code)
    if r is None:
        await links_screen(call, ctx)
        return await call.answer("Ссылки уже нет")
    await state.clear()
    await state.update_data(link_edit=code)
    await _save_link_draft(state, partners.Draft.from_row(r))
    await link_draft_screen(call, ctx, state)
    await call.answer()


@router.callback_query(F.data == "a:lkb")
async def cb_link_back(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    if "link_draft" not in await state.get_data():
        await _save_link_draft(state, partners.Draft())
    await link_draft_screen(call, ctx, state)
    await call.answer()


@router.callback_query(F.data == "a:lkt")
async def cb_link_reward_on(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    d = _link_draft(await state.get_data())
    d.reward_on = REWARD_ORDER[(REWARD_ORDER.index(d.reward_on) + 1) % len(REWARD_ORDER)]
    await _save_link_draft(state, d)
    await link_draft_screen(call, ctx, state)
    await call.answer(partners.REWARD_ON[d.reward_on])


@router.callback_query(F.data.startswith("a:lkf:"))
async def cb_link_field(call: CallbackQuery, state: FSMContext) -> None:
    key = call.data.split(":")[2]
    if key not in LINK_FIELDS:
        return await call.answer()
    await state.update_data(link_field=key)
    await state.set_state(St.link_field)
    await show(call, LINK_FIELDS[key], kb([btn("← К ссылке", "a:lkb")]))
    await call.answer()


@router.message(St.link_field)
async def msg_link_field(message: Message, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    d = _link_draft(data)
    key = data.get("link_field", "")
    raw = (message.text or "").strip()
    back_kb = kb([btn("← К ссылке", "a:lkb")])
    try:
        if key == "name":
            if not raw or len(raw) > 64:
                raise ValueError("название от 1 до 64 символов")
            d.name = raw
        elif key == "code":
            d.code = "" if raw == "-" else partners.clean_code(raw)
        elif key == "owner":
            if raw == "-":
                d.owner_id = 0
            else:
                found = await _resolve(ctx, message, None if getattr(message, "forward_origin", None) else raw)
                if len(found) != 1:
                    raise ValueError("не нашёл одного пользователя: пришли ID, @ник или перешли его сообщение "
                                     "(он должен хотя бы раз запустить бота)")
                d.owner_id = found[0]["id"]
        else:
            if not raw.isdigit():
                raise ValueError("нужно целое число")
            setattr(d, {"bonus": "bonus_new", "reward": "reward", "cap": "reward_cap", "uses": "uses_max",
                        "days": "days"}[key], int(raw))
        d.check(final=False)
    except (ValueError, KeyError) as exc:
        await message.answer(f"❌ {esc(str(exc))}", parse_mode="HTML", reply_markup=back_kb)
        return
    await _save_link_draft(state, d)
    await link_draft_screen(message, ctx, state, "✅ Сохранено")


@router.message(St.link, F.text)
async def msg_link_quick(message: Message, ctx: Context, state: FSMContext) -> None:
    """Быстрое создание: одна строка — название, без бонусов (только статистика)."""
    if (await state.get_data()).get("link_edit"):
        await message.answer("Чтобы поменять параметр, нажми на него кнопкой выше.", reply_markup=kb([btn("← К ссылке", "a:lkb")]))
        return
    d = _link_draft(await state.get_data())
    d.name = message.text.strip()[:64]
    await _save_link_draft(state, d)
    await _link_save(message, ctx, state, message.from_user.id)


async def _link_save(event, ctx: Context, state: FSMContext, admin_id: int) -> bool:
    data = await state.get_data()
    d = _link_draft(data)
    editing = data.get("link_edit")
    try:
        if editing:
            await partners.update(ctx.db, editing, d)
            code = editing
        else:
            code = await partners.create(ctx.db, d, admin_id)
    except ValueError as exc:
        await link_draft_screen(event, ctx, state, f"❌ {esc(str(exc))}")
        return False
    await state.clear()
    await link_card(event, ctx, code, "✅ Сохранено" if editing else "✅ Ссылка создана — отправь её партнёру")
    return True


@router.callback_query(F.data == "a:lkok")
async def cb_link_save(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    ok = await _link_save(call, ctx, state, call.from_user.id)
    await call.answer("Готово" if ok else "")


def _days_line(by_day: dict) -> str:
    today = services.now() // DAY
    names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    parts = []
    for d in range(today - 6, today + 1):
        wd = datetime.datetime.fromtimestamp(d * DAY + DAY // 2).weekday()
        parts.append(f"{names[wd]} {by_day.get(d, 0)}")
    return " · ".join(parts)


async def link_card(event, ctx: Context, code: str, note: str = "") -> None:
    r = await partners.get(ctx.db, code)
    if r is None:
        if isinstance(event, CallbackQuery):
            await links_screen(event, ctx)
        return
    st = await partners.stats(ctx.db, code)
    stt = partners.state(r)
    bot_link, app_link = partners.links(ctx.bot_username, code)
    reward = (f"+{r['reward']} {partners.REWARD_ON[r['reward_on']]}" + (f", лимит {r['reward_cap']}" if r["reward_cap"] else "")
              if r["reward"] else "нет")
    bonus = (f"+{r['bonus_new']}" + (f", первым {r['uses_max']}" if r["uses_max"] else "")) if r["bonus_new"] else "нет"
    j = st["joined"]
    text = ((f"{note}\n\n" if note else "")
            + f"🔗 <b>{esc(r['name'])}</b> — {LINK_ICON[stt]} {LINK_STATE[stt]}\n"
            + (f"<code>{bot_link}</code>\nсразу в Mini App: <code>{app_link}</code>\n\n" if bot_link
               else "⚠️ имя бота неизвестно — ссылка появится после подключения к Telegram\n\n")
            + f"👤 Партнёр: {await _owner_text(ctx, r['owner_id'] or 0)}\n"
            f"🎁 Бонус новичку: <b>{bonus}</b> · 💎 партнёру: <b>{reward}</b>\n"
            f"⏳ До: <b>{_until(r['expires_at'])}</b> · создана {dt(r['created_at'])}\n\n"
            "📊 <b>Статистика</b>\n"
            f"👆 Переходов: <b>{r['clicks']}</b>\n"
            f"👥 Новых пользователей: <b>{j}</b> ({_pct(j, r['clicks'])} от переходов) · сутки +{st['joined_day']} · неделя +{st['joined_week']}\n"
            f"🎨 Сделали пак: <b>{st['active']}</b> ({_pct(st['active'], j)}) · паков {st['packs']}\n"
            f"💳 Купили: <b>{st['buyers']}</b> ({_pct(st['buyers'], j)}) · покупок {st['buys']} · генераций {st['qty']}\n"
            f"💰 Выручка: <b>{stars(st['stars'])}</b>" + (f" + <b>{_rub(st['rub'])}</b>" if st["rub"] else "") + "\n"
            f"🎁 Бонусов новичкам: {st['bonus_users']} чел. · {st['bonus_given']} ген.\n"
            f"💎 Наград партнёру: {st['rewards']} · {st['reward_given']} ген."
            + (f" · ждут условия: {st['pending']}" if st["pending"] else "") + "\n"
            f"📅 Новые за 7 дней: {_days_line(st['by_day'])}")
    await show(event, text, kb(
        [btn("👥 Кто пришёл", f"a:lku:{code}"), btn("📥 CSV", f"a:lkv:{code}")],
        [btn("✏️ Изменить", f"a:lke:{code}"), btn("▶️ Включить" if not r["active"] else "⏸ Пауза", f"a:lkp:{code}")],
        [btn("🔄 Обновить", f"a:lkc:{code}"), btn("🗑 Удалить", f"a:lkx:{code}", style="danger")],
        [back("a:lk", "← Ссылки")]))


@router.callback_query(F.data.startswith("a:lkc:"))
async def cb_link_card(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await link_card(call, ctx, call.data.split(":", 2)[2])
    await call.answer()


@router.callback_query(F.data.startswith("a:lkp:"))
async def cb_link_pause(call: CallbackQuery, ctx: Context) -> None:
    code = call.data.split(":", 2)[2]
    r = await partners.get(ctx.db, code)
    if r is not None:
        await partners.set_active(ctx.db, code, not r["active"])
    await link_card(call, ctx, code)
    await call.answer("Включена" if r is not None and not r["active"] else "На паузе")


@router.callback_query(F.data.startswith("a:lku:"))
async def cb_link_users(call: CallbackQuery, ctx: Context) -> None:
    code = call.data.split(":", 2)[2]
    rows = await partners.joined_users(ctx.db, code, 20)
    mark = {0: "⏳", 1: "💎", 2: ""}
    lines = [f"• {who(u)} · {dt(u['created_at'])} · паков {u['packs']}"
             + (f" · купил {u['purchased_total']}" if u["purchased_total"] else "") + f" {mark.get(u['partner_rewarded'], '')}"
             for u in rows]
    text = (f"👥 <b>Последние пришедшие по ссылке</b> <code>{esc(code)}</code>\n"
            "💎 — партнёр получил награду · ⏳ — ждёт условия\n\n" + ("\n".join(lines) or "Пока никого."))
    await show(call, text, kb([btn("📥 Все в CSV", f"a:lkv:{code}")], [back(f"a:lkc:{code}", "← К ссылке")]))
    await call.answer()


@router.callback_query(F.data.startswith("a:lkv:"))
async def cb_link_csv(call: CallbackQuery, ctx: Context, bot: Bot) -> None:
    code = call.data.split(":", 2)[2]
    rows = await ctx.db.fetchall(
        "SELECT u.id, u.username, u.first_name, u.created_at, u.balance, u.purchased_total, u.partner_rewarded,"
        " (SELECT COUNT(*) FROM packs p WHERE p.user_id=u.id AND p.status='ready') AS packs"
        " FROM users u WHERE u.partner=? ORDER BY u.created_at", (code,))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "username", "first_name", "joined", "balance", "purchased", "packs", "partner_rewarded"])
    for r in rows:
        w.writerow([r["id"], r["username"] or "", r["first_name"] or "",
                    datetime.datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M"),
                    r["balance"], r["purchased_total"], r["packs"], {0: "ждёт", 1: "да", 2: "нет"}.get(r["partner_rewarded"], "")])
    await bot.send_document(call.from_user.id, BufferedInputFile(buf.getvalue().encode("utf-8-sig"), filename=f"partner-{code}.csv"),
                            caption=f"👥 Пришли по ссылке {code}: {len(rows)}")
    await call.answer("Отправил файлом")


@router.callback_query(F.data.startswith("a:lkx:"))
async def cb_link_delete_ask(call: CallbackQuery, ctx: Context) -> None:
    code = call.data.split(":", 2)[2]
    await show(call, f"🗑 Удалить ссылку <code>{esc(code)}</code>?\n\nОна перестанет работать, ожидающие награды партнёру "
                     "не выдадутся. Уже начисленные генерации останутся. Чтобы просто остановить — лучше «⏸ Пауза».",
               kb([btn("🗑 Да, удалить", f"a:lkz:{code}", style="danger"), btn("Отмена", f"a:lkc:{code}")]))
    await call.answer()


@router.callback_query(F.data.startswith("a:lkz:"))
async def cb_link_delete(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await partners.delete(ctx.db, call.data.split(":", 2)[2])
    await links_screen(call, ctx)
    await call.answer("Удалена")


# ───────────────────────── рассылка ─────────────────────────

SEGMENTS = {
    "all": ("всем", "1=1"),
    "buyers": ("покупателям", "purchased_total > 0"),
    "nobuy": ("без покупок", "purchased_total = 0"),
    "makers": ("сделавшим пак", "id IN (SELECT user_id FROM packs)"),
    "nopack": ("без паков", "id NOT IN (SELECT user_id FROM packs)"),
}
BROADCAST = {"running": False}


@router.callback_query(F.data == "a:bc")
async def cb_broadcast(call: CallbackQuery, state: FSMContext) -> None:
    if BROADCAST["running"]:
        return await call.answer("Рассылка уже идёт — дождись отчёта", show_alert=True)
    await state.set_state(St.broadcast)
    await show(call, "📢 Пришли сообщение для рассылки: текст, фото, видео, гифку — как есть, с оформлением.\n"
                     "Дальше выберешь, кому, и добавишь кнопку.", cancel())
    await call.answer()


async def _bc_preview(target: Message | CallbackQuery, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    seg = data.get("seg", "all")
    where = SEGMENTS[seg][1]
    count = await n(ctx, f"SELECT COUNT(*) FROM users WHERE banned=0 AND blocked=0 AND {where}")
    button = data.get("button")
    text = (f"📢 <b>Рассылка</b>\n\nКому: <b>{SEGMENTS[seg][0]}</b> — {count} чел.\n"
            f"Кнопка: {esc(button[0]) + ' → ' + esc(button[1]) if button else 'нет'}"
            f" · «Открыть приложение»: {'да' if data.get('app') else 'нет'}\n\n"
            "Сообщение выше — так его увидят. Проверь и запускай.")
    segs = [btn(("• " if k == seg else "") + v[0], f"a:bs:{k}") for k, v in SEGMENTS.items()]
    markup = kb(segs[:3], segs[3:],
                [btn("🔗 Кнопка-ссылка", "a:bb"), btn(("✅ " if data.get("app") else "") + "📱 Кнопка приложения", "a:ba")],
                [btn("🧪 Себе", "a:bt"), btn(f"🚀 Отправить {count}", "a:bg", style="success")],
                [btn("✕ Отмена", "a")])
    chat = target.from_user.id
    await target.bot.copy_message(chat, data["chat"], data["msg"], reply_markup=_bc_markup(ctx, data))
    await target.bot.send_message(chat, text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)


def _bc_markup(ctx: Context, data: dict) -> InlineKeyboardMarkup | None:
    rows = []
    if data.get("button"):
        rows.append([InlineKeyboardButton(text=data["button"][0], url=data["button"][1])])
    if data.get("app") and ctx.webapp_url.startswith("https://"):
        rows.append([InlineKeyboardButton(text="открыть EzzProd", web_app=WebAppInfo(url=ctx.webapp_url))])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


@router.message(St.broadcast)
async def msg_broadcast(message: Message, ctx: Context, state: FSMContext) -> None:
    await state.update_data(chat=message.chat.id, msg=message.message_id, seg="all", button=None, app=False)
    await state.set_state(None)
    await _bc_preview(message, ctx, state)


@router.callback_query(F.data.startswith("a:bs:"))
async def cb_bc_segment(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    if "msg" not in await state.get_data():
        return await call.answer("Сначала пришли сообщение", show_alert=True)
    await state.update_data(seg=call.data.split(":")[2])
    await _bc_preview(call, ctx, state)
    await call.answer()


@router.callback_query(F.data == "a:ba")
async def cb_bc_app(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    if "msg" not in data:
        return await call.answer("Сначала пришли сообщение", show_alert=True)
    await state.update_data(app=not data.get("app"))
    await _bc_preview(call, ctx, state)
    await call.answer()


@router.callback_query(F.data == "a:bb")
async def cb_bc_button(call: CallbackQuery, state: FSMContext) -> None:
    if "msg" not in await state.get_data():
        return await call.answer("Сначала пришли сообщение", show_alert=True)
    await state.set_state(St.bc_button)
    await call.message.answer("Пришли кнопку: <code>Текст | https://ссылка</code>\nИли «-», чтобы убрать.",
                              parse_mode="HTML", reply_markup=cancel())
    await call.answer()


@router.message(St.bc_button, F.text)
async def msg_bc_button(message: Message, ctx: Context, state: FSMContext) -> None:
    raw = message.text.strip()
    if raw == "-":
        button = None
    else:
        parts = [p.strip() for p in raw.split("|", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1].startswith(("https://", "http://", "tg://")):
            await message.answer("Формат: Текст | https://ссылка", reply_markup=cancel())
            return
        button = [parts[0][:64], parts[1]]
    await state.update_data(button=button)
    await state.set_state(None)
    await _bc_preview(message, ctx, state)


@router.callback_query(F.data == "a:bt")
async def cb_bc_test(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    if "msg" not in data:
        return await call.answer("Сначала пришли сообщение", show_alert=True)
    await call.bot.copy_message(call.from_user.id, data["chat"], data["msg"], reply_markup=_bc_markup(ctx, data))
    await call.answer("Отправил тебе")


@router.callback_query(F.data == "a:bg")
async def cb_bc_go(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    if "msg" not in data:
        return await call.answer("Сначала пришли сообщение", show_alert=True)
    if BROADCAST["running"]:
        return await call.answer("Рассылка уже идёт", show_alert=True)
    await state.clear()
    where = SEGMENTS[data.get("seg", "all")][1]
    rows = await ctx.db.fetchall(f"SELECT id FROM users WHERE banned=0 AND blocked=0 AND {where} ORDER BY id")
    ids = [r["id"] for r in rows]
    BROADCAST["running"] = True
    asyncio.create_task(_run_broadcast(ctx, call.bot, call.from_user.id, data, ids), name="admin-broadcast")
    await show(call, f"🚀 Рассылка запущена: {len(ids)} получателей. Пришлю отчёт, когда закончу.", kb([back()]))
    await call.answer()


async def _run_broadcast(ctx: Context, bot: Bot, admin_id: int, data: dict, ids: list[int]) -> None:
    sent = blocked = failed = 0
    markup = _bc_markup(ctx, data)
    started = time.time()
    try:
        for uid in ids:
            for _ in range(3):
                try:
                    await bot.copy_message(uid, data["chat"], data["msg"], reply_markup=markup)
                    sent += 1
                    break
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after + 1)
                except TelegramForbiddenError:
                    blocked += 1
                    await runtime.set_blocked(ctx.db, uid, True)
                    break
                except Exception:
                    failed += 1
                    break
            await asyncio.sleep(0.05)  # ~20 сообщений в секунду — в пределах лимитов Telegram
    finally:
        BROADCAST["running"] = False
        took = int(time.time() - started)
        try:
            await bot.send_message(admin_id, (
                f"📢 <b>Рассылка завершена</b> за {took // 60} мин {took % 60} с\n\n"
                f"✅ Доставлено: {sent}\n💤 Заблокировали бота: {blocked}\n❌ Ошибок: {failed}"),
                parse_mode="HTML", reply_markup=kb([back()]))
        except Exception:
            log.exception("не удалось отправить отчёт о рассылке")


# ───────────────────────── шаблоны ─────────────────────────

@router.callback_query(F.data == "a:tp")
async def cb_templates(call: CallbackQuery, ctx: Context) -> None:
    await templates_screen(call, ctx)
    await call.answer()


async def templates_screen(call: CallbackQuery, ctx: Context, note: str = "") -> None:
    cats = catalog.all_categories()
    since = services.now() - 30 * DAY
    used = {r["cat"]: r["c"] for r in await ctx.db.fetchall(
        "SELECT substr(i.template_id, 1, instr(i.template_id, '-')-1) AS cat, COUNT(*) AS c FROM pack_items i "
        "JOIN packs p ON p.id=i.pack_id WHERE p.created_at>=? GROUP BY cat", (since,))}
    lines = [f"{'🙈' if c.id in ctx.hidden_categories else '✅'} <b>{esc(c.name)}</b> · {c.count} шт. · за месяц в паках {used.get(c.id, 0)}"
             for c in cats]
    text = (f"🧩 <b>Шаблоны</b> · всего {catalog.total()} (папка templates/)\n\n" + "\n".join(lines)
            + "\n\nНажми на категорию, чтобы скрыть её из Mini App или вернуть.")
    if note:
        text += f"\n\n{note}"
    toggles = [btn(("🙈 " if c.id in ctx.hidden_categories else "✅ ") + c.name, f"a:th:{c.id}") for c in cats]
    pairs = [toggles[i:i + 2] for i in range(0, len(toggles), 2)]
    await show(call, text, kb(*pairs, [btn("🔄 Перечитать папку", "a:tr")], [back()]))


@router.callback_query(F.data.startswith("a:th:"))
async def cb_template_toggle(call: CallbackQuery, ctx: Context) -> None:
    cat = call.data.split(":")[2]
    if cat not in {c.id for c in catalog.all_categories()}:
        return await call.answer()
    hidden = await runtime.toggle_category(ctx, cat)
    await templates_screen(call, ctx, "🙈 Категория скрыта" if hidden else "✅ Категория снова видна")
    await call.answer()


@router.callback_query(F.data == "a:tr")
async def cb_templates_reload(call: CallbackQuery, ctx: Context) -> None:
    before = catalog.total()
    catalog.reload()
    await templates_screen(call, ctx, f"🔄 Папка перечитана: было {before}, стало {catalog.total()}")
    await call.answer()


# ───────────────────────── админы ─────────────────────────

@router.callback_query(F.data == "a:ad")
async def cb_admins(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await admins_screen(call, ctx)
    await call.answer()


async def admins_screen(call: CallbackQuery, ctx: Context, note: str = "") -> None:
    lines = [f"🔒 <code>{a}</code> — из .env (ADMIN_IDS)" for a in ctx.settings.admin_ids]
    buttons = []
    for uid in sorted(ctx.extra_admins):
        u = await services.get_user(ctx.db, uid)
        lines.append(f"👑 {who(u, uid) if u else f'<code>{uid}</code>'}")
        buttons.append([btn(f"🗑 Убрать {uid}", f"a:adx:{uid}")])
    text = "👑 <b>Админы</b>\n\n" + "\n".join(lines)
    if note:
        text += f"\n\n{note}"
    await show(call, text, kb([btn("➕ Добавить", "a:adn", style="success")], *buttons, [back()]))


@router.callback_query(F.data == "a:adn")
async def cb_admin_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(St.add_admin)
    await show(call, "👑 Пришли ID, @ник или перешли сообщение будущего админа.\n"
                     "Он должен хотя бы раз открыть бота.", cancel("a:ad"))
    await call.answer()


@router.message(St.add_admin)
async def msg_admin_new(message: Message, ctx: Context, state: FSMContext) -> None:
    found = await _resolve(ctx, message)
    if len(found) != 1:
        await message.answer("Не нашёл одного человека. Пришли ID или @ник.", reply_markup=cancel("a:ad"))
        return
    uid = found[0]["id"]
    await runtime.add_admin(ctx, uid, message.from_user.id)
    await state.clear()
    await message.answer(f"✅ {who(found[0])} теперь админ: команда /admin ему доступна.", parse_mode="HTML",
                         reply_markup=kb([back("a:ad", "← Админы")]))


@router.callback_query(F.data.startswith("a:adx:"))
async def cb_admin_remove(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    uid = int(call.data.split(":")[2])
    if uid == call.from_user.id and uid not in ctx.settings.admin_ids:
        return await call.answer("Себя убрать нельзя", show_alert=True)
    await state.clear()
    await runtime.remove_admin(ctx, uid)
    await admins_screen(call, ctx, f"🗑 {uid} больше не админ")
    await call.answer()


# ───────────────────────── сервер ─────────────────────────

def _size(path: str) -> int:
    total = 0
    for p in (path, path + "-wal"):
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return total


@router.callback_query(F.data == "a:sy")
async def cb_system(call: CallbackQuery, ctx: Context) -> None:
    await system_screen(call, ctx)
    await call.answer()


async def system_screen(call: CallbackQuery, ctx: Context, note: str = "") -> None:
    s = ctx.settings
    up = int(time.time() - ctx.started_at)
    text = (
        "🖥 <b>Сервер</b>\n\n"
        f"Бот: <b>@{esc(ctx.bot_username or '—')}</b> · {'🟢 на связи' if ctx.bot else '🔴 нет связи с Telegram'}\n"
        f"Работает: {up // 86400} д {up % 86400 // 3600} ч {up % 3600 // 60} мин\n"
        f"Режим сборки: <b>{'демо (имитация)' if s.demo_mode else 'настоящие наборы Telegram'}</b>\n"
        f"Оплата Stars: <b>{'вкл' if s.payments_live else 'выкл'}</b>\n"
        f"Mini App: {esc(ctx.webapp_url or '—')}\n"
        f"База: {_size(str(ctx.db.path)) / 1048576:.2f} МБ · шаблонов {catalog.total()}\n"
        f"Python {platform.python_version()} · {esc(platform.system())}"
    )
    if note:
        text += f"\n\n{note}"
    await show(call, text, kb(
        [btn("🧾 Сверить балансы", "a:syl"), btn("🔗 Обновить кнопку меню", "a:sym")],
        [btn("🔄 Обновить", "a:sy"), back()],
    ))


@router.callback_query(F.data == "a:syl")
async def cb_system_ledger(call: CallbackQuery, ctx: Context) -> None:
    bad = await services.ledger_mismatches(ctx.db)
    note = "✅ Все балансы совпадают с журналом" if not bad else (
        "❌ Не сходится у: " + ", ".join(f"<code>{uid}</code> ({bal} ≠ {tot})" for uid, bal, tot in bad[:10]))
    await system_screen(call, ctx, note)
    await call.answer()


@router.callback_query(F.data == "a:sym")
async def cb_system_menu(call: CallbackQuery, ctx: Context) -> None:
    from . import set_menu_button

    if ctx.bot is None or not ctx.webapp_url.startswith("https://"):
        await system_screen(call, ctx, "❌ Нет связи с Telegram или https-адреса")
        return await call.answer()
    try:
        await set_menu_button(ctx.bot, ctx.webapp_url)
        note = "✅ Кнопка меню ведёт на текущий адрес"
    except Exception as exc:
        note = f"❌ {esc(str(exc))}"
    await system_screen(call, ctx, note)
    await call.answer()


# ───────────────────────── бэкап и экспорт ─────────────────────────

@router.callback_query(F.data == "a:bk")
async def cb_backup(call: CallbackQuery, ctx: Context, bot: Bot) -> None:
    await call.answer("Готовлю копию…")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    path = Path(tempfile.gettempdir()) / f"ezzprod_{stamp}_{os.getpid()}.db"
    try:
        path.unlink(missing_ok=True)
        await ctx.db.backup_to(path)
        data = await asyncio.to_thread(path.read_bytes)
        await bot.send_document(call.from_user.id, BufferedInputFile(data, filename=f"ezzprod_{stamp}.db"),
                                caption="💾 Копия базы. Храни в надёжном месте: внутри платежи и пользователи.")
    except Exception as exc:
        log.exception("бэкап не удался")
        await bot.send_message(call.from_user.id, f"❌ Бэкап не удался: {esc(str(exc))}", parse_mode="HTML")
    finally:
        path.unlink(missing_ok=True)


@router.callback_query(F.data == "a:ex")
async def cb_export(call: CallbackQuery, ctx: Context, bot: Bot) -> None:
    await call.answer("Собираю CSV…")
    rows = await ctx.db.fetchall(
        "SELECT u.*, (SELECT COUNT(*) FROM packs p WHERE p.user_id=u.id) AS packs, "
        f"(SELECT COALESCE(SUM(amount),0) FROM payment_events e WHERE e.user_id=u.id AND e.status IN ({_in(PAID)})) AS spent "
        "FROM users u ORDER BY u.created_at", PAID)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["id", "username", "first_name", "last_name", "balance", "plan", "purchased", "spent_stars", "packs",
                "referred_by", "banned", "blocked", "created"])
    for r in rows:
        w.writerow([r["id"], r["username"] or "", r["first_name"], r["last_name"], r["balance"], r["plan"],
                    r["purchased_total"], r["spent"], r["packs"], r["referred_by"] or "", r["banned"], r["blocked"],
                    dt(r["created_at"])])
    data = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM — чтобы Excel открыл кириллицу
    await bot.send_document(call.from_user.id, BufferedInputFile(data, filename="ezzprod_users.csv"),
                            caption=f"📥 Пользователи: {len(rows)}")


# ───────────────────────── кастомизация сообщений ─────────────────────────

@router.callback_query(F.data == "a:msgs")
async def cb_messages_list(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    customized_count = len(ctx.custom_texts)
    text = (
        "💬 <b>Кастомизация сообщений бота</b>\n\n"
        "Здесь можно настроить любые стандартные тексты, которые бот отправляет в чат.\n"
        "Поддерживаются переменные в фигурных скобках <code>{переменная}</code> — они подставляются автоматически.\n\n"
        f"Всего сообщений: <b>{len(texts.MSG_DEFS)}</b>"
        + (f" · изменённых: <b>{customized_count}</b>" if customized_count else "")
    )
    rows = []
    for mdef in texts.MSG_DEFS.values():
        mark = " ✎" if mdef.key in ctx.custom_texts else ""
        rows.append([btn(f"{mdef.title}{mark}", f"a:msg:{mdef.key}")])
    rows.append([back()])
    await show(call, text, kb(*rows))
    await call.answer()


async def message_card(event, ctx: Context, key: str, note: str = "") -> None:
    mdef = texts.MSG_DEFS.get(key)
    if mdef is None:
        return
    cur = ctx.custom_texts.get(key)
    is_custom = cur is not None
    active_text = cur if is_custom else mdef.default
    placeholders_str = ", ".join(mdef.placeholders) if mdef.placeholders else "нет"

    text = (
        f"💬 <b>{esc(mdef.title)}</b>\n\n"
        f"<b>Статус:</b> {'✎ изменён' if is_custom else 'стандартный'}\n"
        f"<b>Переменные:</b> <code>{esc(placeholders_str)}</code>\n"
    )
    if mdef.hint:
        text += f"<i>{esc(mdef.hint)}</i>\n\n"
    else:
        text += "\n"

    text += f"<b>Текущий текст:</b>\n<blockquote>{active_text}</blockquote>\n\n"
    if is_custom:
        text += f"<b>Стандартный текст:</b>\n<blockquote>{esc(mdef.default)}</blockquote>\n\n"

    if note:
        text += f"<b>{note}</b>\n\n"

    rows = [
        [btn("✏️ Изменить текст", f"a:msge:{key}", style="primary")],
    ]
    if is_custom:
        rows.append([btn("↺ Сбросить к стандартному", f"a:msgr:{key}")])
    if key.startswith("subscription"):
        rows.append([back("a:sub", "← Обязательная подписка")])
    rows.append([back("a:msgs", "← Тексты сообщений")])
    try:
        await show(event, text, kb(*rows))
    except Exception:
        fallback_text = (
            f"💬 <b>{esc(mdef.title)}</b>\n\n"
            f"<b>Статус:</b> {'✎ изменён' if is_custom else 'стандартный'}\n"
            f"<b>Переменные:</b> <code>{esc(placeholders_str)}</code>\n\n"
            f"<b>Текущий текст:</b>\n<blockquote>{esc(active_text)}</blockquote>\n\n"
        )
        if is_custom:
            fallback_text += f"<b>Стандартный текст:</b>\n<blockquote>{esc(mdef.default)}</blockquote>\n\n"
        if note:
            fallback_text += f"<b>{note}</b>\n\n"
        await show(event, fallback_text, kb(*rows))


@router.callback_query(F.data.startswith("a:msg:"))
async def cb_message_view(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    key = call.data.split(":")[2]
    await message_card(call, ctx, key)
    await call.answer()


@router.callback_query(F.data.startswith("a:msge:"))
async def cb_message_edit(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    key = call.data.split(":")[2]
    mdef = texts.MSG_DEFS.get(key)
    if mdef is None:
        return await call.answer()
    await state.set_state(St.custom_text)
    await state.update_data(key=key)
    placeholders_str = ", ".join(mdef.placeholders) if mdef.placeholders else "нет"
    text = (
        f"✏️ <b>Редактирование текста:</b> {esc(mdef.title)}\n\n"
        f"Доступные переменные: <code>{esc(placeholders_str)}</code>\n\n"
        "Отправь новый текст сообщения следующим сообщением в этот чат.\n"
        "Переменные будут подставлены автоматически при отправке пользователю."
    )
    await show(call, text, cancel(f"a:msg:{key}"))
    await call.answer()


@router.callback_query(F.data.startswith("a:msgr:"))
async def cb_message_reset(call: CallbackQuery, ctx: Context) -> None:
    key = call.data.split(":")[2]
    await runtime.update_custom_text(ctx, key, None)
    await message_card(call, ctx, key, "↺ Сброшено к стандартному тексту")
    await call.answer()


@router.message(St.custom_text)
async def msg_custom_text(message: Message, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("key")
    if not key or key not in texts.MSG_DEFS:
        await state.clear()
        return

    # Для кнопок используем чистый текст без тегов, для сообщений — сохраняем HTML и премиум-эмодзи:
    if key in ("subscription_join", "subscription_check"):
        new_text = (message.text or "").strip()
        bkey = "sub_join" if key == "subscription_join" else "sub_check"
        bcur = dict(ctx.custom_buttons.get(bkey) or {})
        bcur["text"] = new_text
        await runtime.update_custom_button(ctx, bkey, bcur)
    else:
        new_text = (message.html_text or message.text or "").strip()

    if key.startswith("subscription"):
        limit = 64 if key in ("subscription_join", "subscription_check") else 4000
        if not new_text or len(new_text) > limit:
            await message.answer(f"Нужен текст от 1 до {limit} символов.", reply_markup=cancel(f"a:msg:{key}"))
            return

    await runtime.update_custom_text(ctx, key, new_text)
    await state.clear()
    await message_card(message, ctx, key, "✅ Новый текст сохранён и уже действует")


# ───────────────────────── кастомизация кнопок ─────────────────────────

COLOR_NAMES = {
    None: "⚪ Обычный (серый)",
    "default": "⚪ Обычный (серый)",
    "primary": "🔵 Синий (primary)",
    "success": "🟢 Зелёный (success)",
    "danger": "🔴 Красный (danger)",
}


@router.callback_query(F.data == "a:btns")
async def cb_buttons_list(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    customized_count = len(ctx.custom_buttons)
    text = (
        "🔘 <b>Кастомизация кнопок бота</b>\n\n"
        "Здесь можно настроить внешний вид и текст кнопок, которые бот отправляет в чат:\n"
        "• <b>Текст</b> (до 64 символов, любые эмодзи)\n"
        "• <b>Цвет кнопки</b> (обычный, синий primary, зелёный success, красный danger)\n"
        "• <b>Telegram Premium эмодзи</b> (кастомный эмодзи на кнопке)\n\n"
        f"Всего кнопок: <b>{len(texts.BTN_DEFS)}</b>"
        + (f" · изменённых: <b>{customized_count}</b>" if customized_count else "")
    )
    rows = []
    for bdef in texts.BTN_DEFS.values():
        is_mod = bdef.key in ctx.custom_buttons
        mark = " ✎" if is_mod else ""
        rows.append([btn(f"{bdef.title}{mark}", f"a:btn:{bdef.key}")])
    rows.append([back()])
    await show(call, text, kb(*rows))
    await call.answer()


async def button_card(event, ctx: Context, key: str, note: str = "") -> None:
    bdef = texts.BTN_DEFS.get(key)
    if bdef is None:
        return
    cur = ctx.custom_buttons.get(key) or {}
    is_custom = bool(cur)
    active_text = cur.get("text") or bdef.default_text

    raw_style = cur.get("style")
    if raw_style in ("primary", "success", "danger", "default"):
        active_style = raw_style
    else:
        active_style = bdef.default_style if bdef else "default"

    active_emoji = cur.get("icon_custom_emoji_id", bdef.default_emoji_id if bdef else None)

    color_title = COLOR_NAMES.get(active_style, "⚪ Обычный (серый)")
    emoji_str = f"<code>{active_emoji}</code>" if active_emoji else "нет"

    text = (
        f"🔘 <b>{esc(bdef.title)}</b>\n\n"
        f"<b>Статус:</b> {'✎ изменён' if is_custom else 'стандартный'}\n"
        f"<b>Текст кнопки:</b> <code>{esc(active_text)}</code>\n"
        f"<b>Цвет кнопки:</b> {color_title}\n"
        f"<b>Премиум-эмодзи:</b> {emoji_str}\n"
    )
    if bdef.hint:
        text += f"<i>{esc(bdef.hint)}</i>\n\n"
    else:
        text += "\n"

    if is_custom and active_text != bdef.default_text:
        text += f"<b>Стандартный текст:</b> <code>{esc(bdef.default_text)}</code>\n\n"

    if note:
        text += f"<b>{note}</b>\n\n"

    text += "<i>Нажми на превью кнопки ниже для проверки её отображения:</i>"

    def make_preview_markup(with_emoji: bool = True):
        extra = {}
        if active_style in ("primary", "success", "danger"):
            extra["style"] = active_style
        if with_emoji and active_emoji:
            extra["icon_custom_emoji_id"] = str(active_emoji)

        preview_button = InlineKeyboardButton(
            text=f"👁 {active_text}",
            callback_data=f"a:btnpv:{key}",
            **extra
        )
        action_rows = [
            [preview_button],
            [btn("✏️ Изменить текст", f"a:btne:{key}", style="primary"),
             btn("🎨 Цвет кнопки", f"a:btnc:{key}")],
        ]
        if not active_emoji:
            action_rows.append([btn("✨ Задать премиум-эмодзи", f"a:btnem:{key}")])
        else:
            action_rows.append([
                btn("✨ Изменить эмодзи", f"a:btnem:{key}"),
                btn("❌ Убрать эмодзи", f"a:btnremem:{key}"),
            ])

        if is_custom:
            action_rows.append([btn("↺ Сбросить к стандартной", f"a:btnr:{key}")])
        if key in ("sub_join", "sub_check"):
            action_rows.append([back("a:sub", "← Обязательная подписка")])
        action_rows.append([back("a:btns", "← Все кнопки")])
        return kb(*action_rows)

    try:
        await show(event, text, make_preview_markup(with_emoji=True))
    except Exception:
        try:
            fallback_text = text + "\n\n⚠️ <i>(Предпросмотр эмодзи на кнопке временно недоступен)</i>"
            await show(event, fallback_text, make_preview_markup(with_emoji=False))
        except Exception:
            await show(event, esc(text), kb([back("a:btns")]))


@router.callback_query(F.data.startswith("a:btnpv:"))
async def cb_button_preview_click(call: CallbackQuery) -> None:
    await call.answer("Это интерактивное превью кнопки. В чате она выполняет своё действие.", show_alert=True)


@router.callback_query(F.data.startswith("a:btn:"))
async def cb_button_view(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    key = call.data.split(":")[2]
    await button_card(call, ctx, key)
    await call.answer()


@router.callback_query(F.data.startswith("a:btne:"))
async def cb_button_text_prompt(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    key = call.data.split(":")[2]
    bdef = texts.BTN_DEFS.get(key)
    if bdef is None:
        return await call.answer()
    await state.set_state(St.custom_button_text)
    await state.update_data(key=key)
    cur = ctx.custom_buttons.get(key) or {}
    cur_text = cur.get("text") or bdef.default_text

    text = (
        f"✏️ <b>Редактирование текста кнопки:</b> {esc(bdef.title)}\n\n"
        f"Текущий текст: <code>{esc(cur_text)}</code>\n"
        f"Стандартный текст: <code>{esc(bdef.default_text)}</code>\n\n"
        "Отправь новый текст кнопки сообщением в этот чат (от 1 до 64 символов).\n"
        "Можно использовать любые эмодзи."
    )
    await show(call, text, cancel(f"a:btn:{key}"))
    await call.answer()


@router.callback_query(F.data.startswith("a:btnc:"))
async def cb_button_color(call: CallbackQuery, ctx: Context) -> None:
    key = call.data.split(":")[2]
    bdef = texts.BTN_DEFS.get(key)
    if bdef is None:
        return await call.answer()
    cur = ctx.custom_buttons.get(key) or {}
    raw_style = cur.get("style")
    if raw_style in ("primary", "success", "danger", "default"):
        cur_style = raw_style
    else:
        cur_style = bdef.default_style if bdef else "default"

    text = (
        f"🎨 <b>Выбор цвета кнопки:</b> {esc(bdef.title)}\n\n"
        f"Текущий цвет: <b>{COLOR_NAMES.get(cur_style, '⚪ Обычный (серый)')}</b>\n\n"
        "Выберите стиль кнопки в Telegram:"
    )
    rows = [
        [btn("⚪ Обычный (серый)", f"a:btnsetc:{key}:default")],
        [btn("🔵 Синий (primary)", f"a:btnsetc:{key}:primary", style="primary")],
        [btn("🟢 Зелёный (success)", f"a:btnsetc:{key}:success", style="success")],
        [btn("🔴 Красный (danger)", f"a:btnsetc:{key}:danger", style="danger")],
        [back(f"a:btn:{key}", "← Назад к кнопке")],
    ]
    await show(call, text, kb(*rows))
    await call.answer()


@router.callback_query(F.data.startswith("a:btnsetc:"))
async def cb_button_set_color(call: CallbackQuery, ctx: Context) -> None:
    parts = call.data.split(":")
    key = parts[2]
    color = parts[3]
    cur = dict(ctx.custom_buttons.get(key) or {})
    cur["style"] = color

    await runtime.update_custom_button(ctx, key, cur)
    await button_card(call, ctx, key, f"✅ Цвет кнопки изменён на: {COLOR_NAMES.get(color, '⚪ Обычный (серый)')}")
    await call.answer()



@router.callback_query(F.data.startswith("a:btnem:"))
async def cb_button_emoji_prompt(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    key = call.data.split(":")[2]
    bdef = texts.BTN_DEFS.get(key)
    if bdef is None:
        return await call.answer()
    await state.set_state(St.custom_button_emoji)
    await state.update_data(key=key)
    cur = ctx.custom_buttons.get(key) or {}
    cur_emoji = cur.get("icon_custom_emoji_id")

    text = (
        f"✨ <b>Telegram Premium эмодзи для кнопки:</b> {esc(bdef.title)}\n\n"
        + (f"Текущий эмодзи ID: <code>{cur_emoji}</code>\n\n" if cur_emoji else "")
        + "<b>Как установить:</b>\n"
        "1. Если у тебя есть <b>Telegram Premium</b>, просто отправь сообщение с нужным эмодзи — бот сам распознает его <code>custom_emoji_id</code>.\n"
        "2. Или отправь числовой ID кастомного эмодзи (например: <code>5368324170671202286</code>).\n\n"
        "<i>Отправь сообщение в этот чат или нажми «Отмена».</i>"
    )
    await show(call, text, cancel(f"a:btn:{key}"))
    await call.answer()


@router.callback_query(F.data.startswith("a:btnremem:"))
async def cb_button_remove_emoji(call: CallbackQuery, ctx: Context) -> None:
    key = call.data.split(":")[2]
    cur = dict(ctx.custom_buttons.get(key) or {})
    cur.pop("icon_custom_emoji_id", None)
    val = cur if (cur.get("text") or cur.get("style") or cur.get("icon_custom_emoji_id")) else None
    await runtime.update_custom_button(ctx, key, val)
    await button_card(call, ctx, key, "✅ Премиум-эмодзи удалён с кнопки")
    await call.answer()


@router.callback_query(F.data.startswith("a:btnr:"))
async def cb_button_reset(call: CallbackQuery, ctx: Context) -> None:
    key = call.data.split(":")[2]
    await runtime.update_custom_button(ctx, key, None)
    await button_card(call, ctx, key, "↺ Кнопка сброшена к стандартному виду")
    await call.answer()


@router.message(St.custom_button_text)
async def msg_custom_button_text(message: Message, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("key")
    if not key or key not in texts.BTN_DEFS:
        await state.clear()
        return

    raw_text = (message.text or message.caption or "").strip()
    if not raw_text or len(raw_text) > 64:
        await message.answer("⚠️ Текст кнопки должен быть от 1 до 64 символов. Попробуй ещё раз:", reply_markup=cancel(f"a:btn:{key}"))
        return

    cur = dict(ctx.custom_buttons.get(key) or {})
    cur["text"] = raw_text
    await runtime.update_custom_button(ctx, key, cur)
    if key in ("sub_join", "sub_check"):
        tkey = "subscription_join" if key == "sub_join" else "subscription_check"
        await runtime.update_custom_text(ctx, tkey, raw_text)
    await state.clear()
    await button_card(message, ctx, key, "✅ Текст кнопки успешно обновлён!")


@router.message(St.custom_button_emoji)
async def msg_custom_button_emoji(message: Message, ctx: Context, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("key")
    if not key or key not in texts.BTN_DEFS:
        await state.clear()
        return

    emoji_id = None
    entities = message.entities or message.caption_entities or []
    for ent in entities:
        if getattr(ent, "type", None) == "custom_emoji" and getattr(ent, "custom_emoji_id", None):
            emoji_id = str(ent.custom_emoji_id)
            break

    if not emoji_id:
        raw_text = (message.text or message.caption or "").strip()
        if raw_text.isdigit() and len(raw_text) >= 5:
            emoji_id = raw_text

    if not emoji_id:
        await message.answer(
            "⚠️ Не удалось найти кастомный эмодзи.\n\n"
            "Отправь сообщение с <b>Telegram Premium эмодзи</b> или числовой <code>custom_emoji_id</code>:",
            reply_markup=cancel(f"a:btn:{key}"),
            parse_mode="HTML"
        )
        return

    cur = dict(ctx.custom_buttons.get(key) or {})
    cur["icon_custom_emoji_id"] = emoji_id
    await runtime.update_custom_button(ctx, key, cur)
    await state.clear()
    await button_card(message, ctx, key, f"✅ Премиум-эмодзи установлен! (ID: <code>{emoji_id}</code>)")
