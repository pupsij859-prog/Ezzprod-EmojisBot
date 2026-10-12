"""HTTP API «Админки» Mini App. Доступ только у ADMIN_IDS; остальным — 403."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import admin, services
from ..ratelimit import limited
from .routes import ctx_of, user_row

router = APIRouter(prefix="/api/admin")

CHARGE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


async def admin_row(request: Request, row=Depends(user_row)):
    if not admin.is_admin(ctx_of(request).settings, row["id"]):
        raise HTTPException(403, {"code": "forbidden"})
    return row


def _page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, 100)), max(0, offset)


@router.get("/stats")
async def stats(request: Request, _=Depends(admin_row)):
    return await admin.stats(ctx_of(request).db)


@router.get("/payments")
async def payments(request: Request, status: str = "all", limit: int = 50, offset: int = 0, _=Depends(admin_row)):
    if status not in admin.PAYMENT_FILTERS:
        raise HTTPException(422, {"code": "validation", "field": "status"})
    return await admin.payments(ctx_of(request).db, status, *_page(limit, offset))


class RefundIn(BaseModel):
    provider: str = "stars"


@router.post("/payments/{charge_id}/refund", dependencies=[Depends(limited("admin", 30, 60))])
async def refund(charge_id: str, body: RefundIn, request: Request, _=Depends(admin_row)):
    if not CHARGE_ID.match(charge_id):
        raise HTTPException(404, {"code": "no_payment"})
    c = ctx_of(request)
    try:
        ok, user_id, qty = await admin.refund(c, body.provider, charge_id)
    except services.RefundError as exc:
        code = str(exc)
        raise HTTPException(503 if code == "no_bot" else 409, {"code": f"refund_{code}"})
    if ok:
        await admin.notify_refunded(c, user_id, qty)
    return {"ok": ok, "status": "refunded" if ok else "credited", "qty": qty}


@router.get("/users")
async def users(request: Request, q: str = "", limit: int = 50, offset: int = 0, _=Depends(admin_row)):
    return await admin.users(ctx_of(request).db, q[:64], *_page(limit, offset))


@router.get("/users/{user_id}")
async def user(user_id: int, request: Request, _=Depends(admin_row)):
    detail = await admin.user_detail(ctx_of(request).db, user_id)
    if detail is None:
        raise HTTPException(404, {"code": "no_user"})
    return detail


class GrantIn(BaseModel):
    delta: int


@router.post("/users/{user_id}/grant", dependencies=[Depends(limited("admin", 30, 60))])
async def grant(user_id: int, body: GrantIn, request: Request, row=Depends(admin_row)):
    try:
        balance = await admin.grant(ctx_of(request).db, user_id, body.delta, row["id"])
    except LookupError:
        raise HTTPException(404, {"code": "no_user"})
    return {"balance": balance}


@router.get("/packs")
async def packs(request: Request, status: str = "all", limit: int = 50, offset: int = 0, _=Depends(admin_row)):
    if status not in admin.PACK_FILTERS:
        raise HTTPException(422, {"code": "validation", "field": "status"})
    return await admin.packs(ctx_of(request).db, status, *_page(limit, offset))
