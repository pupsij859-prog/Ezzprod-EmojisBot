"""Сверка платежей Stars с журналом транзакций Telegram (getStarTransactions).

Зачем: апдейты `successful_payment` живут у Telegram не дольше суток. Если бот был выключен (ноутбук уснул,
туннель сменил адрес), оплата прошла, а генерации не начислены. Сверка доначисляет пропущенное ТЕМ ЖЕ кодом,
что и обработчик платежа, поэтому двойного начисления не будет (ключ идемпотентности — charge_id)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from . import services
from .context import Context

log = logging.getLogger("ezzprod.reconcile")

PAGE = 100


@dataclass
class StarPayment:
    charge_id: str
    payload: str
    user_id: int
    amount: int


def payment_from_transaction(tx) -> StarPayment | None:
    """Берём только входящие оплаты счетов от пользователей; возвраты, выплаты и прочее пропускаем."""
    source = getattr(tx, "source", None)
    if source is None or getattr(source, "type", None) != "user":
        return None
    if getattr(source, "transaction_type", None) != "invoice_payment":
        return None
    payload = getattr(source, "invoice_payload", None)
    user = getattr(source, "user", None)
    amount = getattr(tx, "amount", 0) or 0
    if not payload or user is None or amount <= 0:
        return None
    return StarPayment(charge_id=str(tx.id), payload=str(payload), user_id=int(user.id), amount=int(amount))


async def reconcile_stars(ctx: Context, max_pages: int = 10) -> int:
    """Возвращает число доначисленных платежей."""
    bot = ctx.bot
    if bot is None:
        return 0
    credited = 0
    offset = 0
    for _ in range(max_pages):
        page = await bot.get_star_transactions(offset=offset, limit=PAGE)
        items = list(page.transactions or [])
        for tx in items:
            payment = payment_from_transaction(tx)
            if payment is None:
                continue
            result = await services.settle_payment(
                ctx.db, ctx.settings, payment.payload, payment.charge_id, payment.amount, payment.user_id
            )
            if result is None:
                continue
            if result.status == "credited":
                credited += 1
                log.warning("доначислен пропущенный платёж %s: user=%s qty=%s", payment.charge_id, payment.user_id, result.qty)
                await _safe(ctx.notifier.payment_ok, result.user_id, result.qty, result.balance)
            else:
                log.error("платёж %s требует ручного разбора: user=%s amount=%s", payment.charge_id, payment.user_id, payment.amount)
                await _safe(
                    ctx.notifier.admin_alert,
                    f"платёж требует разбора: charge={payment.charge_id} user={payment.user_id} сумма={payment.amount} ⭐",
                )
        if len(items) < PAGE:
            break
        offset += len(items)
    return credited


async def reconcile_loop(ctx: Context, interval_s: float = 900.0) -> None:
    while True:
        try:
            await reconcile_stars(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("сверка платежей не удалась")
        await asyncio.sleep(interval_s)


async def _safe(fn, *args) -> None:
    try:
        await fn(*args)
    except Exception:
        log.exception("не удалось отправить уведомление")
