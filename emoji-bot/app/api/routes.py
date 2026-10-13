"""HTTP API Mini App. Все ответы — JSON (кроме превью шаблонов)."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .. import admin, catalog, emoji, preview, pricing, services
from ..auth import current_user
from ..context import Context
from ..ratelimit import limited

router = APIRouter(prefix="/api")

REF_PARAM = re.compile(r"^ref_([0-9a-f]{8})$")


def ctx_of(request: Request) -> Context:
    return request.app.state.ctx


async def user_row(request: Request, tg: dict = Depends(current_user)):
    c = ctx_of(request)
    ref = None
    m = REF_PARAM.match(tg.get("_start_param", ""))  # ссылка вида t.me/<bot>?startapp=ref_xxxxxxxx
    if m:
        ref = m.group(1)
    row, _ = await services.upsert_user(c.db, c.settings, tg, ref)
    return row


def _me_payload(c: Context, row) -> dict[str, Any]:
    s = c.settings
    return {
        "id": row["id"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "username": row["username"],
        "balance": row["balance"],
        "plan": row["plan"],
        "purchased_total": row["purchased_total"],
        "pro_min": s.pro_min_purchased,
        "trial_exports_left": row["trial_exports_left"],
        "onboarded": bool(row["onboarded"]),
        "ref_link": c.ref_link(row["ref_code"]),
        "brand": s.brand_name,
        "demo": s.demo_mode,
        "support_url": c.support_url,
        "referral_reward": s.referral_reward,
        "is_admin": admin.is_admin(s, row["id"]),
        "limits": {
            "emoji_pack_max": s.emoji_pack_max,
            "sticker_pack_max": s.sticker_pack_max,
            "text_max": s.text_max,
            "logo_max_bytes": s.logo_max_bytes,
            "styles_max": s.styles_max,
        },
    }


# ───────────── профиль / онбординг ─────────────

@router.get("/me")
async def me(request: Request, row=Depends(user_row)):
    return _me_payload(ctx_of(request), row)


@router.post("/onboarding/complete")
async def onboarding_complete(request: Request, row=Depends(user_row)):
    await services.complete_onboarding(ctx_of(request).db, row["id"])
    return {"ok": True}


# ───────────── каталог ─────────────

@router.get("/categories")
async def categories():
    return {"categories": catalog.categories(), "total": catalog.TOTAL}


@router.get("/templates")
async def templates(category: str = "classic", page: int = 1, size: int = catalog.PAGE_SIZE):
    result = catalog.page(category, page, size)
    if result is None:
        raise HTTPException(404, {"code": "no_category"})
    return result


@router.get("/templates/{template_id}/preview.svg")
async def template_preview(template_id: str, request: Request):
    tpl = catalog.get(template_id)
    if tpl is None:
        raise HTTPException(404, {"code": "no_template"})
    # превью не зависит от пользовательского ввода (никакие query-параметры не учитываются)
    svg = preview.render_svg(tpl, ctx_of(request).settings.brand_name)
    return Response(
        svg,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=86400",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ───────────── логотип ─────────────

@router.post("/logos", status_code=201, dependencies=[Depends(limited("logos", 10, 60))])
async def upload_logo(request: Request, file: UploadFile = File(...), row=Depends(user_row)):
    from ..logos import save_logo

    c = ctx_of(request)
    data = await file.read(c.settings.logo_max_bytes + 1)  # не читаем больше лимита
    return await save_logo(c.db, c.settings, row["id"], file.filename or "logo", data)


@router.delete("/logos/{logo_id}")
async def remove_logo(logo_id: str, request: Request, row=Depends(user_row)):
    from ..logos import delete_logo

    c = ctx_of(request)
    if not re.fullmatch(r"[0-9a-f]{32}", logo_id) or not await delete_logo(c.db, c.settings, row["id"], logo_id):
        raise HTTPException(404, {"code": "no_logo"})
    return {"ok": True}


# ───────────── паки ─────────────

@router.post("/packs", status_code=201, dependencies=[Depends(limited("packs", 20, 60))])
async def create_pack(
    request: Request,
    body: dict = Body(...),
    idempotency_key: str | None = Header(default=None),
    row=Depends(user_row),
):
    c = ctx_of(request)
    req = services.validate_pack_request(c.settings, body)
    duration = c.builder.estimate_seconds(len(req["template_ids"]))
    pack, created = await services.create_pack(c.db, c.settings, row["id"], req, duration, idempotency_key)
    if created:
        c.runner.start(pack["id"], duration)
    return pack


@router.get("/packs")
async def packs(request: Request, row=Depends(user_row)):
    return {"packs": await services.list_packs(ctx_of(request).db, row["id"])}


@router.get("/packs/{pack_id}")
async def pack(pack_id: int, request: Request, row=Depends(user_row)):
    c = ctx_of(request)
    found = await services.get_pack(c.db, row["id"], pack_id)
    if found is None:
        raise HTTPException(404, {"code": "no_pack"})
    return services.pack_view(found)


# ───────────── сохранённые стили (pro) ─────────────

@router.get("/styles")
async def styles(request: Request, row=Depends(user_row)):
    c = ctx_of(request)
    pro = row["plan"] == "pro"
    return {
        "pro": pro,
        "purchased": row["purchased_total"],
        "pro_min": c.settings.pro_min_purchased,
        "styles": await services.list_styles(c.db, row["id"]) if pro else [],
    }


class StyleIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    text: str = Field(default="", max_length=64)
    fill: str = "#131822"
    stroke: str = "#a8c7ef"


@router.post("/styles", status_code=201, dependencies=[Depends(limited("styles", 20, 60))])
async def add_style(body: StyleIn, request: Request, row=Depends(user_row)):
    c = ctx_of(request)
    return await services.add_style(
        c.db, c.settings, row["id"], body.name, {"text": body.text, "fill": body.fill, "stroke": body.stroke}
    )


@router.delete("/styles/{style_id}")
async def delete_style(style_id: int, request: Request, row=Depends(user_row)):
    if not await services.delete_style(ctx_of(request).db, row["id"], style_id):
        raise HTTPException(404, {"code": "no_style"})
    return {"ok": True}


# ───────────── цены и оплата ─────────────

@router.get("/pricing")
async def get_pricing(request: Request):
    return pricing.pricing_table(ctx_of(request).settings)


class InvoiceIn(BaseModel):
    qty: int = Field(ge=1, le=10_000)
    method: str = "stars"


@router.post("/billing/invoice", dependencies=[Depends(limited("invoice", 10, 60))])
async def create_invoice(body: InvoiceIn, request: Request, row=Depends(user_row)):
    c = ctx_of(request)
    if body.qty > c.settings.max_buy_qty:
        raise HTTPException(422, {"code": "validation", "field": "qty"})
    if body.method not in pricing.enabled_methods(c.settings):
        raise HTTPException(503, {"code": "method_unavailable"})
    if c.payments is None or not c.settings.payments_live or c.settings.demo_mode:
        raise HTTPException(503, {"code": "payments_unavailable"})
    q = pricing.quote(c.settings, body.qty)
    payment_id = await services.create_payment(c.db, row["id"], q.qty, q.stars)
    url = await c.payments.create_invoice(payment_id=payment_id, qty=q.qty, amount=q.stars)
    return {"url": url, "payment_id": payment_id, "total": q.total, "stars": q.stars}


@router.get("/billing/payments/{payment_id}")
async def payment_state(payment_id: str, request: Request, row=Depends(user_row)):
    """Клиент опрашивает после закрытия окна оплаты: статус 'paid' значит, что генерации уже начислены."""
    if not re.fullmatch(r"[0-9a-f]{32}", payment_id):
        raise HTTPException(404, {"code": "no_payment"})
    status = await services.payment_status(ctx_of(request).db, row["id"], payment_id)
    if status is None:
        raise HTTPException(404, {"code": "no_payment"})
    return {"status": status}


# ───────────── кастомные эмодзи для текстов ─────────────

@router.get("/emoji")
async def emoji_map(request: Request):
    c = ctx_of(request)
    return {"emoji": emoji.public_map(c.settings, bot_ready=c.bot is not None)}


async def _emoji_response(c: Context, emoji_id: str, thumb: bool):
    if c.emoji_store is None:
        c.emoji_store = emoji.EmojiStore(c.settings.data_dir / "emoji")
    hit = await c.emoji_store.get(c.bot, emoji_id, thumb=thumb)
    if hit is None:
        raise HTTPException(404, {"code": "no_emoji"})
    path, media_type = hit
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=604800"})


@router.get("/emoji/id/{emoji_id}")
async def emoji_by_id(emoji_id: str, request: Request, thumb: bool = False):
    """Анимированный премиум-эмодзи по custom_emoji_id (только из премиум-паков и назначенных ключам)."""
    c = ctx_of(request)
    if not emoji.allowed_id(c.settings, emoji_id):
        raise HTTPException(404, {"code": "no_emoji"})
    return await _emoji_response(c, emoji_id, thumb)


@router.get("/emoji/{key}")
async def emoji_file(key: str, request: Request, thumb: bool = False):
    """Файл кастомного эмодзи по ключу из app/emoji.py: Lottie JSON, WebM или WebP."""
    c = ctx_of(request)
    emoji_id = emoji.ids(c.settings).get(key)
    if emoji_id is None:
        raise HTTPException(404, {"code": "no_emoji"})
    return await _emoji_response(c, emoji_id, thumb)



# ───────────── рефералка ─────────────

@router.get("/referral")
async def referral(request: Request, row=Depends(user_row)):
    c = ctx_of(request)
    stats = await services.referral_stats(c.db, c.settings, row["id"])
    return {"link": c.ref_link(row["ref_code"]), **stats}


# ───────────── обработка ошибок сервисов ─────────────

def install_error_handlers(app) -> None:
    @app.exception_handler(services.InsufficientBalance)
    async def _insufficient(_: Request, exc: services.InsufficientBalance):
        return JSONResponse({"detail": {"code": exc.code, "need": exc.need}}, status_code=402)

    @app.exception_handler(services.ProRequired)
    async def _pro(_: Request, exc: services.ProRequired):
        return JSONResponse({"detail": {"code": exc.code}}, status_code=403)

    @app.exception_handler(services.ValidationFailed)
    async def _invalid(_: Request, exc: services.ValidationFailed):
        return JSONResponse({"detail": {"code": exc.code, "field": str(exc)}}, status_code=422)
