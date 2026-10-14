"""Админка: статистика, платежи и возвраты, пользователи и ручные начисления, лента паков.

Все изменения баланса идут через ledger (services._apply) в одной транзакции — как и везде в приложении.
"""
from __future__ import annotations

import logging
import uuid

from . import services
from .config import Settings
from .context import Context
from .db import Database

log = logging.getLogger("ezzprod.admin")

DAY = 86400
GRANT_MAX = 10_000

# фильтр → статусы payment_events (None — все)
PAYMENT_FILTERS: dict[str, tuple[str, ...] | None] = {
    "all": None,
    "paid": ("credited", "credited_repeat"),
    "review": ("needs_review",),
    "refunded": ("refunding", "refunded"),
}
PACK_FILTERS: dict[str, tuple[str, ...] | None] = {
    "all": None,
    "processing": ("processing",),
    "ready": ("ready",),
    "failed": ("failed",),
}
PAID = PAYMENT_FILTERS["paid"]


def _in(statuses: tuple[str, ...]) -> str:
    return ",".join("?" * len(statuses))


async def _scalar(db: Database, sql: str, params: tuple = ()) -> int:
    row = await db.fetchone(sql, params)
    return int(row["n"] or 0)


# ───────────────────────── статистика ─────────────────────────

async def stats(db: Database) -> dict:
    now = services.now()
    day, week = now - DAY, now - 7 * DAY
    paid = f"status IN ({_in(PAID)})"

    async def revenue(since: int) -> int:
        return await _scalar(db, f"SELECT COALESCE(SUM(amount),0) AS n FROM payment_events WHERE {paid} AND created_at>=?",
                             (*PAID, since))

    return {
        "users": {
            "total": await _scalar(db, "SELECT COUNT(*) AS n FROM users"),
            "today": await _scalar(db, "SELECT COUNT(*) AS n FROM users WHERE created_at>=?", (day,)),
            "week": await _scalar(db, "SELECT COUNT(*) AS n FROM users WHERE created_at>=?", (week,)),
        },
        "packs": {
            "total": await _scalar(db, "SELECT COUNT(*) AS n FROM packs"),
            "today": await _scalar(db, "SELECT COUNT(*) AS n FROM packs WHERE created_at>=?", (day,)),
            "processing": await _scalar(db, "SELECT COUNT(*) AS n FROM packs WHERE status='processing'"),
            "failed": await _scalar(db, "SELECT COUNT(*) AS n FROM packs WHERE status='failed'"),
        },
        "revenue": {"today": await revenue(day), "week": await revenue(week), "total": await revenue(0)},
        "gens": {
            "sold": await _scalar(db, "SELECT COALESCE(SUM(delta),0) AS n FROM ledger WHERE reason='purchase'"),
            "on_balance": await _scalar(db, "SELECT COALESCE(SUM(balance),0) AS n FROM users"),
        },
        "review": await _scalar(db, "SELECT COUNT(*) AS n FROM payment_events WHERE status='needs_review'"),
    }


# ───────────────────────── платежи ─────────────────────────

async def payments(db: Database, flt: str, limit: int = 50, offset: int = 0) -> dict:
    statuses = PAYMENT_FILTERS[flt]
    where, params = ("WHERE e.status IN (" + _in(statuses) + ")", statuses) if statuses else ("", ())
    rows = await db.fetchall(
        "SELECT e.id, e.provider, e.charge_id, e.user_id, e.amount, e.currency, e.status, e.created_at,"
        " p.qty, u.username, u.first_name, u.last_name "
        "FROM payment_events e LEFT JOIN payments p ON p.id = e.payload LEFT JOIN users u ON u.id = e.user_id "
        f"{where} ORDER BY e.id DESC LIMIT ? OFFSET ?",
        (*params, limit + 1, offset),
    )
    counts = {}
    for key, st in PAYMENT_FILTERS.items():
        sql = "SELECT COUNT(*) AS n FROM payment_events" + (f" WHERE status IN ({_in(st)})" if st else "")
        counts[key] = await _scalar(db, sql, st or ())
    items = [
        {
            "id": r["id"], "provider": r["provider"], "charge_id": r["charge_id"], "user": _who(r, r["user_id"]),
            "amount": r["amount"], "currency": r["currency"], "status": r["status"], "qty": r["qty"],
            "created_at": r["created_at"],
        }
        for r in rows[:limit]
    ]
    return {"items": items, "counts": counts, "more": len(rows) > limit}


def _who(row, user_id: int) -> dict:
    return {"id": user_id, "username": row["username"], "first_name": row["first_name"] or "",
            "last_name": row["last_name"] or ""}


async def refund(ctx: Context, provider: str, charge_id: str) -> tuple[bool | None, int, int]:
    """Возврат платежа: снимаем генерации, возвращаем деньги, при отказе Telegram откатываем.
    → (успех, user_id, qty). успех=None — исход неизвестен (сбой сети): платёж остаётся «refunding»,
    генерации не возвращаются автоматически, админу уходит алерт. Ошибки предусловий — services.RefundError."""
    if provider not in ("stars", "dev"):
        raise services.RefundError("unknown")
    if provider == "dev" and not ctx.settings.dev_mode:
        raise services.RefundError("unknown")
    if provider == "stars" and ctx.bot is None:
        raise services.RefundError("no_bot")

    user_id, qty = await services.begin_refund(ctx.db, ctx.settings, charge_id, provider)
    if provider == "dev":  # эмулированная оплата: денег нет, Telegram не вызываем
        ok: bool | None = True
    else:
        ok = await _refund_stars(ctx, user_id, charge_id)
    if ok is True:
        await services.finish_refund(ctx.db, charge_id, provider)
    elif ok is False:
        await services.revert_refund(ctx.db, ctx.settings, charge_id, provider)
    else:
        log.error("исход возврата неизвестен: charge=%s user=%s — нужна ручная проверка", charge_id, user_id)
        await _safe_alert(ctx, f"возврат charge={charge_id} user={user_id}: Telegram не ответил, исход неизвестен. "
                               "платёж оставлен в статусе «возврат», проверь в Stars и повтори или откати вручную")
    return ok, user_id, qty


async def _refund_stars(ctx: Context, user_id: int, charge_id: str) -> bool | None:
    """True — Stars вернулись, False — Telegram явно отказал, None — неизвестно (таймаут, сеть)."""
    from aiogram.exceptions import TelegramAPIError, TelegramNetworkError

    try:
        return bool(await ctx.bot.refund_star_payment(user_id=user_id, telegram_payment_charge_id=charge_id))
    except TelegramNetworkError:
        log.warning("refundStarPayment: сеть, проверяем журнал Stars: charge=%s", charge_id)
    except TelegramAPIError:
        # Telegram ответил ошибкой — возврата точно не было
        log.exception("refundStarPayment отклонён: charge=%s", charge_id)
        return False
    except Exception:
        log.exception("refundStarPayment: неожиданная ошибка, проверяем журнал Stars: charge=%s", charge_id)
    return await _refund_seen(ctx, charge_id)


async def _refund_seen(ctx: Context, charge_id: str, max_pages: int = 20) -> bool | None:
    """Ищет в журнале Stars возврат с тем же id (у возврата id совпадает с исходным платежом)."""
    try:
        offset = 0
        for _ in range(max_pages):
            page = await ctx.bot.get_star_transactions(offset=offset, limit=100)
            items = list(page.transactions or [])
            for tx in items:
                receiver = getattr(tx, "receiver", None)
                if str(tx.id) == charge_id and receiver is not None and getattr(receiver, "type", None) == "user":
                    return True
            if len(items) < 100:
                return False
            offset += len(items)
    except Exception:
        log.exception("не удалось проверить журнал Stars для charge=%s", charge_id)
    return None


async def _safe_alert(ctx: Context, text: str) -> None:
    try:
        await ctx.notifier.admin_alert(text)
    except Exception:
        log.exception("не удалось отправить алерт админу")


async def notify_refunded(ctx: Context, user_id: int, qty: int) -> None:
    if ctx.bot is None:
        return
    from .bot.texts import refund_user_notice  # пакет бота тянет хендлеры, а они — этот модуль

    try:
        await ctx.bot.send_message(user_id, refund_user_notice(qty))
    except Exception:
        log.warning("не удалось уведомить пользователя %s о возврате", user_id)


# ───────────────────────── пользователи ─────────────────────────

def _user_view(r) -> dict:
    return {
        "id": r["id"], "username": r["username"], "first_name": r["first_name"] or "", "last_name": r["last_name"] or "",
        "balance": r["balance"], "plan": r["plan"], "purchased_total": r["purchased_total"], "created_at": r["created_at"],
    }


async def users(db: Database, query: str = "", limit: int = 50, offset: int = 0) -> dict:
    q = query.strip()
    if q.isdigit():
        where, params = "WHERE id = ?", (int(q),)
    elif q:
        like = "%" + q.lstrip("@").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where = "WHERE username LIKE ? ESCAPE '\\' OR first_name LIKE ? ESCAPE '\\' OR last_name LIKE ? ESCAPE '\\'"
        params = (like, like, like)
    else:
        where, params = "", ()
    rows = await db.fetchall(f"SELECT * FROM users {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                             (*params, limit + 1, offset))
    total = await _scalar(db, f"SELECT COUNT(*) AS n FROM users {where}", params)
    return {"items": [_user_view(r) for r in rows[:limit]], "total": total, "more": len(rows) > limit}


async def user_detail(db: Database, user_id: int) -> dict | None:
    row = await services.get_user(db, user_id)
    if row is None:
        return None
    ledger = await db.fetchall(
        "SELECT delta, reason, created_at FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 15", (user_id,)
    )
    return {
        **_user_view(row),
        "packs": await _scalar(db, "SELECT COUNT(*) AS n FROM packs WHERE user_id=?", (user_id,)),
        "spent_stars": await _scalar(
            db, f"SELECT COALESCE(SUM(amount),0) AS n FROM payment_events WHERE user_id=? AND status IN ({_in(PAID)})",
            (user_id, *PAID)),
        "invited": await _scalar(db, "SELECT COUNT(*) AS n FROM users WHERE referred_by=?", (user_id,)),
        "ledger": [{"delta": r["delta"], "reason": r["reason"], "created_at": r["created_at"]} for r in ledger],
    }


async def grant(db: Database, user_id: int, delta: int, admin_id: int) -> int:
    """Ручное начисление (delta > 0) или списание (delta < 0). → новый баланс.
    Списать больше, чем есть, нельзя: services.InsufficientBalance."""
    if delta == 0 or abs(delta) > GRANT_MAX:
        raise services.ValidationFailed("delta")
    async with db.tx() as t:
        if await t.fetchone("SELECT id FROM users WHERE id=?", (user_id,)) is None:
            raise LookupError(user_id)
        await services._apply(t, user_id, delta, "admin", f"{admin_id}:{uuid.uuid4().hex}")
        row = await t.fetchone("SELECT balance FROM users WHERE id=?", (user_id,))
    return row["balance"]


# ───────────────────────── паки ─────────────────────────

async def packs(db: Database, flt: str, limit: int = 50, offset: int = 0) -> dict:
    statuses = PACK_FILTERS[flt]
    where, params = ("WHERE p.status IN (" + _in(statuses) + ")", statuses) if statuses else ("", ())
    rows = await db.fetchall(
        "SELECT p.*, u.username, u.first_name, u.last_name FROM packs p LEFT JOIN users u ON u.id = p.user_id "
        f"{where} ORDER BY p.id DESC LIMIT ? OFFSET ?",
        (*params, limit + 1, offset),
    )
    counts = {}
    for key, st in PACK_FILTERS.items():
        sql = "SELECT COUNT(*) AS n FROM packs" + (f" WHERE status IN ({_in(st)})" if st else "")
        counts[key] = await _scalar(db, sql, st or ())
    items = [{**services.pack_view(r), "user": _who(r, r["user_id"])} for r in rows[:limit]]
    return {"items": items, "counts": counts, "more": len(rows) > limit}


def is_admin(settings: Settings, user_id: int) -> bool:
    return user_id in settings.admin_ids
