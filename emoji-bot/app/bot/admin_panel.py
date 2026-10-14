"""Админка в чате бота: /admin. Видна только админам (ADMIN_IDS из .env и добавленным из админки).

Устроена как в username_bot: главный экран со сводкой и предупреждениями, разделы кнопками, всё редактируется
в том же сообщении. Ввод (число, ID, текст рассылки) — через состояния FSM; «✕ Отмена» возвращает назад.
Деньги и генерации меняются только через сервисы (ledger), как и везде в приложении.
"""
from __future__ import annotations

import asyncio
import csv
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

from .. import admin, catalog, runtime, services
from ..context import Context

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
    broadcast = State()
    bc_button = State()
    add_admin = State()


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
        [btn("💲 Цены и лимиты", "a:set"), btn("📢 Рассылка", "a:bc")],
        [btn("🧩 Шаблоны", "a:tp"), btn("👑 Админы", "a:ad")],
        [btn("🖥 Сервер", "a:sy"), btn("💾 Бэкап", "a:bk"), btn("📥 Экспорт", "a:ex")],
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
        "🎟 <b>Промокоды</b> — коды на генерации; пользователь вводит <code>/promo КОД</code>.\n"
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
           "referral": "за друга", "promo": "промокод", "pack_refund": "возврат за пак", "refund_revert": "отмена возврата"}


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
    hint = {"str": "Пришли @username или «-», чтобы убрать.", "float": "Пришли число, например 1.25"}.get(
        t.kind, f"Пришли число от {t.lo} до {t.hi}.")
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

@router.callback_query(F.data == "a:pr")
async def cb_promos(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await promos_screen(call, ctx)
    await call.answer()


async def promos_screen(call: CallbackQuery, ctx: Context) -> None:
    rows = await runtime.list_promos(ctx.db)
    lines = [f"{'✅' if r['uses'] < r['uses_max'] else '⛔'} <code>{esc(r['code'])}</code> · +{r['gens']} ген. · "
             f"{r['uses']}/{r['uses_max']}" for r in rows]
    buttons = [btn(f"🗑 {r['code']}", f"a:prx:{r['code']}") for r in rows[:10]]
    await show(call, "🎟 <b>Промокоды</b>\nПользователь активирует: <code>/promo КОД</code>\n\n"
                     + ("\n".join(lines) or "Пока нет ни одного."),
               kb([btn("➕ Создать", "a:prn", style="success")], buttons[:2], buttons[2:4], buttons[4:6],
                  buttons[6:8], buttons[8:10], [back()]))


@router.callback_query(F.data == "a:prn")
async def cb_promo_new(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(St.promo)
    await show(call, "➕ Пришли <code>КОД ГЕНЕРАЦИЙ АКТИВАЦИЙ</code>\n"
                     "Например: <code>EZZ10 10 100</code> — 10 генераций, 100 человек.\n"
                     "Вместо кода «-» — придумаю сам.", cancel("a:pr"))
    await call.answer()


@router.message(St.promo, F.text)
async def msg_promo(message: Message, ctx: Context, state: FSMContext) -> None:
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("Формат: КОД ГЕНЕРАЦИЙ АКТИВАЦИЙ, например EZZ10 10 100", reply_markup=cancel("a:pr"))
        return
    try:
        code = await runtime.create_promo(ctx.db, None if parts[0] == "-" else parts[0], int(parts[1]), int(parts[2]),
                                          message.from_user.id)
    except ValueError as exc:
        await message.answer(f"❌ {exc}", reply_markup=cancel("a:pr"))
        return
    await state.clear()
    await message.answer(f"✅ Промокод <code>{esc(code)}</code> создан: +{parts[1]} генераций, {parts[2]} активаций.\n"
                         f"Активация: <code>/promo {esc(code)}</code>", parse_mode="HTML",
                         reply_markup=kb([back("a:pr", "← Промокоды")]))


@router.callback_query(F.data.startswith("a:prx:"))
async def cb_promo_delete(call: CallbackQuery, ctx: Context, state: FSMContext) -> None:
    await state.clear()
    await runtime.delete_promo(ctx.db, call.data.split(":", 2)[2])
    await promos_screen(call, ctx)
    await call.answer("Удалён")


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
