"""Маршруты ТОЛЬКО для локальной разработки. Модуль подключается лишь при DEV_MODE=1 (см. main.create_app),
в проде этих маршрутов в приложении просто нет."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import pricing, services
from .routes import InvoiceIn, ctx_of, user_row

router = APIRouter(prefix="/api/dev")


@router.post("/pay")
async def dev_pay(body: InvoiceIn, request: Request, row=Depends(user_row)):
    """Эмуляция оплаты без Telegram и без денег."""
    c = ctx_of(request)
    if body.qty > c.settings.max_buy_qty:
        raise HTTPException(422, {"code": "validation", "field": "qty"})
    if body.method not in pricing.enabled_methods(c.settings):
        raise HTTPException(503, {"code": "method_unavailable"})
    q = pricing.quote(c.settings, body.qty)
    payment_id = await services.create_payment(c.db, row["id"], q.qty, q.stars)
    result = await services.settle_payment(
        c.db, c.settings, payment_id, f"dev-{uuid.uuid4().hex}", q.stars, row["id"], provider="dev"
    )
    return {"paid": True, "balance": result.balance if result else None}
