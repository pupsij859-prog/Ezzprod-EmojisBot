"""HTTP API Mini App. Все ответы — JSON (кроме превью шаблонов)."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .. import catalog, emoji, partners, pricing, runtime, services, subscription
from ..auth import current_user
from ..context import Context
from ..ratelimit import limited

router = APIRouter(prefix="/api")
log = logging.getLogger("ezzprod")

REF_PARAM = re.compile(r"^ref_([0-9a-f]{8})\Z")


def ctx_of(request: Request) -> Context:
    return request.app.state.ctx


async def user_row(request: Request, tg: dict = Depends(current_user)):
    """Пользователь + бан/техработы + обязательная подписка (недавний результат проверки — из кэша)."""
    return await _user(request, tg, check_subscription=True, fresh=False)


async def user_row_fresh(request: Request, tg: dict = Depends(current_user)):
    """Как user_row, но подписку спрашиваем у Telegram заново: старт приложения и кнопка «Проверить»."""
    return await _user(request, tg, check_subscription=True, fresh=True)


async def user_row_any(request: Request, tg: dict = Depends(current_user)):
    """Как user_row, но без обязательной подписки: статус уже оплаченного счёта нужен всегда."""
    return await _user(request, tg, check_subscription=False, fresh=False)


async def _user(request: Request, tg: dict, *, check_subscription: bool, fresh: bool):
    c = ctx_of(request)
    ref = None
    m = REF_PARAM.match(tg.get("_start_param", ""))  # ссылка вида t.me/<bot>?startapp=ref_xxxxxxxx
    if m:
        ref = m.group(1)
    partner = partners.code_from_param(tg.get("_start_param"))  # партнёрская: startapp=p_<код>
    row, created = await services.upsert_user(c.db, c.settings, tg, ref, partner)
    if created and partner:
        # переход засчитываем только новому: start_param приходит с каждым запросом сессии Mini App
        await partners.count_click(c.db, partner)
        reward = await partners.signup_reward(c.db, row["id"])
        if reward:
            await c.notifier.partner_reward(*reward)
    if not runtime.is_admin(c, row["id"]):
        if row["banned"]:
            raise HTTPException(403, {"code": "banned"})
        if c.maintenance:
            raise HTTPException(503, {"code": "maintenance"})
        if not check_subscription:
            return row
        try:
            subscribed = await subscription.check(c, row["id"], fresh=fresh)
        except subscription.CheckUnavailable:
            raise HTTPException(503, subscription.payload(c, unavailable=True)) from None
        if not subscribed:
            raise HTTPException(403, subscription.payload(c))
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
        "is_admin": runtime.is_admin(c, row["id"]),
        "discount_pct": row["discount_pct"] if "discount_pct" in row.keys() else 0,
        "limits": {
            "emoji_pack_max": s.emoji_pack_max,
            "sticker_pack_max": s.sticker_pack_max,
            "text_max": s.text_max,
            "logo_max_bytes": s.logo_max_bytes,
            "font_max_bytes": s.font_max_bytes,
            "styles_max": s.styles_max,
        },
    }


# ───────────── профиль / онбординг ─────────────

@router.get("/me")
async def me(request: Request, row=Depends(user_row_fresh)):
    return _me_payload(ctx_of(request), row)


@router.post("/onboarding/complete")
async def onboarding_complete(request: Request, row=Depends(user_row)):
    await services.complete_onboarding(ctx_of(request).db, row["id"])
    return {"ok": True}


# ───────────── промокоды ─────────────

class PromoIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)


@router.post("/promo", dependencies=[Depends(limited("promo", 8, 60))])
async def redeem_promo(body: PromoIn, request: Request, row=Depends(user_row)):
    try:
        res = await runtime.redeem_promo(ctx_of(request).db, row["id"], body.code)
    except runtime.PromoError as exc:
        raise HTTPException(400, {"code": "promo", "reason": exc.reason}) from None
    return {"gens": res.gens, "balance": res.balance, "discount_pct": res.discount_pct}


# ───────────── каталог ─────────────

@router.get("/categories")
async def categories(request: Request):
    hidden = ctx_of(request).hidden_categories  # скрытые из админки
    cats = [c for c in catalog.categories() if c["id"] not in hidden]
    return {"categories": cats, "total": sum(c["count"] for c in cats)}


@router.get("/templates")
async def templates(request: Request, category: str = "classic", page: int = 1, size: int = catalog.PAGE_SIZE):
    result = None if category in ctx_of(request).hidden_categories else catalog.page(category, page, size)
    if result is None:
        raise HTTPException(404, {"code": "no_category"})
    return result


@router.get("/templates/{template_id}.tgs")
async def template_file(template_id: str, request: Request):
    """Файл шаблона для превью в Mini App: Lottie JSON, сжатый gzip (это и есть .tgs).
    Отдаём как JSON с Content-Encoding: gzip — браузер распакует сам."""
    tpl = catalog.get(template_id)
    if tpl is None:
        raise HTTPException(404, {"code": "no_template"})
    stat = tpl.path.stat()
    etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
    headers = {"Cache-Control": "public, max-age=86400", "ETag": etag, "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    data = tpl.path.read_bytes()
    return Response(data, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})


class GlyphsIn(BaseModel):
    text: str = Field(default="", max_length=64)
    font_id: str | None = Field(default=None, max_length=32)


async def own_font_path(c: Context, user_id: int, font_id: str | None):
    """Путь к шрифту ника (готовому или своему) или None (стандартный). Чужой или неизвестный id — 404."""
    from .. import user_fonts

    font_id = services.clean_font_id(font_id)
    if font_id is None:
        return None
    path = user_fonts.font_path(c.settings, font_id) if await user_fonts.owns(c.db, user_id, font_id) else None
    if path is None:
        raise HTTPException(404, {"code": "no_font"})
    return path


@router.post("/glyphs", dependencies=[Depends(limited("glyphs", 240, 60))])
async def glyphs(body: GlyphsIn, request: Request, row=Depends(user_row)):
    """Ник как векторный рисунок (контуры букв) — его живое превью подставляет в шаблоны вместо «ezzprod».
    Тот же рисунок сборщик кладёт в настоящие TGS, поэтому превью совпадает с результатом."""
    from .. import fonts

    c = ctx_of(request)
    text = services.clean_text(body.text, c.settings.text_max)
    path = await own_font_path(c, row["id"], body.font_id)
    drawing = await asyncio.to_thread(fonts.text_drawing, text, path) if text else None
    return {"text": text, "drawing": drawing}


# ───────────── свой шрифт ника ─────────────

@router.get("/fonts")
async def list_fonts(request: Request, row=Depends(user_row)):
    """Витрина шрифтов: готовые (для всех) и свои загруженные; max — сколько своих можно держать."""
    from .. import user_fonts

    c = ctx_of(request)
    return {"presets": user_fonts.presets(), "own": await user_fonts.list_fonts(c.db, row["id"]), "max": c.settings.fonts_max}


@router.post("/fonts", status_code=201, dependencies=[Depends(limited("fonts", 10, 60))])
async def upload_font(request: Request, file: UploadFile = File(...), row=Depends(user_row)):
    from ..user_fonts import save_font

    c = ctx_of(request)
    data = await file.read(c.settings.font_max_bytes + 1)  # не читаем больше лимита
    return await save_font(c.db, c.settings, row["id"], file.filename or "font", data)


@router.delete("/fonts/{font_id}")
async def remove_font(font_id: str, request: Request, row=Depends(user_row)):
    from ..user_fonts import delete_font

    c = ctx_of(request)
    if not re.fullmatch(r"[0-9a-f]{32}", font_id) or not await delete_font(c.db, c.settings, row["id"], font_id):
        raise HTTPException(404, {"code": "no_font"})
    return {"ok": True}


# Превью редактора считается в потоке. Пока пользователь двигает ползунок, клиент отменяет прошлые запросы, но поток
# отменить нельзя: без очереди старые расчёты копятся и новое превью ждёт их все. Поэтому рендеры одного пользователя
# идут по одному, а запрос, который клиент уже бросил или который обогнал более новый, не считается вовсе.
_render_locks: dict[int, asyncio.Lock] = {}
_render_latest: dict[int, int] = {}


@router.post("/render", dependencies=[Depends(limited("render", 600, 60))])
async def render_preview(request: Request, body: dict = Body(...), row=Depends(user_row)):
    """Продвинутый редактор (Pro): шаблон с ником, логотипом, цветами и правками → готовая анимация
    (тем же кодом, что собирает пак), список частей эмодзи, все цвета и проверка под требования Telegram."""
    from .. import editor, fonts, lottie
    from ..logos import load_vectors

    c = ctx_of(request)
    if row["plan"] != "pro":
        raise services.ProRequired()
    tpl = catalog.get(body.get("template_id") or "")
    if tpl is None:
        raise HTTPException(404, {"code": "no_template"})
    text = services.clean_text(body.get("text"), c.settings.text_max)
    color, stroke = services.clean_color(body.get("color")), services.clean_color(body.get("stroke"))
    size = services.clean_size(body.get("size"))
    place = services.clean_place(body.get("place"))
    grad = services.clean_grad(body.get("grad"))
    edit = services.clean_edit(body.get("edit"))
    font = await own_font_path(c, row["id"], body.get("font_id"))
    logo = None
    logo_id = services.clean_logo_id(body.get("logo_id"))
    if logo_id:
        owned = await c.db.fetchone("SELECT 1 FROM logos WHERE id=? AND user_id=?", (logo_id, row["id"]))
        variants = load_vectors(c.settings.logos_dir, logo_id) if owned else []
        logo = variants[0] if variants else None

    def build() -> bytes:
        drawing = fonts.text_drawing(text, font) if text else None
        base = lottie.personalize(lottie.load(tpl.path), color=lottie.hex_to_rgb(color), stroke=lottie.hex_to_rgb(stroke),
                                  text=drawing, logo=logo, size=lottie.text_size(size), place=place,
                                  grad=grad)
        out, data = editor.render(base, edit, mark=True)
        return editor.gzip_json({
            "anim": out,
            "parts": editor.parts(base, text),
            "colors": editor.colors(base),
            "checks": editor.check(out, data, base),
        })

    uid = row["id"]
    mine = _render_latest[uid] = _render_latest.get(uid, 0) + 1
    lock = _render_locks.setdefault(uid, asyncio.Lock())
    async with lock:
        if mine != _render_latest.get(uid) or await request.is_disconnected():
            return Response(status_code=204)  # клиенту этот ответ уже не нужен
        try:
            payload = await asyncio.to_thread(build)
        except Exception:
            log.exception("render failed: %s %s", tpl.id, edit)
            raise HTTPException(500, {"code": "render_failed"}) from None
    return Response(payload, media_type="application/json",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


# ───────────── логотип ─────────────

@router.post("/logos", status_code=201, dependencies=[Depends(limited("logos", 10, 60))])
async def upload_logo(request: Request, file: UploadFile = File(...), row=Depends(user_row)):
    from ..logos import save_logo

    c = ctx_of(request)
    data = await file.read(c.settings.logo_max_bytes + 1)  # не читаем больше лимита
    return await save_logo(c.db, c.settings, row["id"], file.filename or "logo", data)


@router.get("/logos/{logo_id}/vector")
async def logo_vector(logo_id: str, request: Request, row=Depends(user_row)):
    """Векторный логотип для живого превью (после перезапуска Mini App)."""
    from ..logos import load_vectors

    c = ctx_of(request)
    if not re.fullmatch(r"[0-9a-f]{32}", logo_id):
        raise HTTPException(404, {"code": "no_logo"})
    owned = await c.db.fetchone("SELECT 1 FROM logos WHERE id=? AND user_id=?", (logo_id, row["id"]))
    variants = load_vectors(c.settings.logos_dir, logo_id) if owned else []
    if not variants:
        raise HTTPException(404, {"code": "no_logo"})
    return {"vector": variants[0]}


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


@router.delete("/packs/{pack_id}")
async def delete_pack(pack_id: int, request: Request, row=Depends(user_row)):
    c = ctx_of(request)
    if not await services.delete_pack(c.db, row["id"], pack_id):
        raise HTTPException(404, {"code": "no_pack"})
    return {"ok": True}


class AddPackItemsIn(BaseModel):
    template_ids: list[str] = Field(min_length=1, max_length=100)
    overrides: dict[str, Any] | None = None
    params: dict[str, Any] | None = None


@router.post("/packs/{pack_id}/items", dependencies=[Depends(limited("packs", 20, 60))])
async def add_pack_items(pack_id: int, body: AddPackItemsIn, request: Request, row=Depends(user_row)):
    c = ctx_of(request)
    pack = await services.add_pack_items(
        c.db, c.settings, row["id"], pack_id, body.template_ids, overrides=body.overrides, params=body.params
    )
    duration = c.builder.estimate_seconds(len(body.template_ids))
    c.runner.start(pack_id, duration)
    return pack


@router.delete("/packs/{pack_id}/items/{template_id}")
async def delete_pack_item(pack_id: int, template_id: str, request: Request, row=Depends(user_row)):
    c = ctx_of(request)
    bot = getattr(c, "bot", None)
    return await services.delete_pack_item(c.db, c.settings, row["id"], pack_id, template_id, bot=bot)


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
    color: str | None = None   # заливка надписи; None — родной цвет шаблонов
    stroke: str | None = None  # обводка надписи; None — без обводки


@router.post("/styles", status_code=201, dependencies=[Depends(limited("styles", 20, 60))])
async def add_style(body: StyleIn, request: Request, row=Depends(user_row)):
    c = ctx_of(request)
    return await services.add_style(
        c.db, c.settings, row["id"], body.name, {"text": body.text, "color": body.color, "stroke": body.stroke}
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
    disc = row["discount_pct"] if "discount_pct" in row.keys() else 0
    q = pricing.quote(c.settings, body.qty, user_discount_pct=disc)
    payment_id = await services.create_payment(
        c.db, row["id"], q.qty, q.stars, method=body.method, amount_rub=q.total
    )
    if hasattr(c.payments, "create_invoice"):
        try:
            res = await c.payments.create_invoice(
                method=body.method, payment_id=payment_id, qty=q.qty, stars=q.stars, amount_rub=q.total
            )
        except TypeError:
            res = await c.payments.create_invoice(payment_id=payment_id, qty=q.qty, amount=q.stars)
        except RuntimeError as exc:
            log.warning("create_invoice error: %s", exc)
            raise HTTPException(503, {"code": "method_unavailable", "detail": str(exc)})
        url = res["url"] if isinstance(res, dict) else res
        ext_id = res.get("external_id") if isinstance(res, dict) else None
        if ext_id:
            await services.set_payment_external_id(c.db, payment_id, ext_id)
    else:
        # запасной случай (если подсунут старый провайдер напрямую)
        url = await c.payments.create_invoice(payment_id=payment_id, qty=q.qty, amount=q.stars)
    return {"url": url, "payment_id": payment_id, "method": body.method, "total": q.total, "stars": q.stars}


@router.post("/billing/webhook/cryptobot")
async def cryptobot_webhook(request: Request):
    """Вебхук от Crypto Pay (@CryptoBot) при оплате счёта."""
    c = ctx_of(request)
    hub = c.payments
    provider = getattr(hub, "cryptobot", None)
    if provider is None or not getattr(provider, "is_configured", False):
        raise HTTPException(503, "CryptoBot not configured")
    sig = request.headers.get("crypto-pay-api-signature", "")
    body_bytes = await request.body()
    if not provider.verify_webhook(body_bytes, sig):
        raise HTTPException(403, "Invalid signature")
    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "Bad JSON")
    if data.get("update_type") == "invoice_paid":
        inv = data.get("payload", {})
        payment_id = inv.get("payload")
        invoice_id = str(inv.get("invoice_id"))
        amount = inv.get("amount", 0)
        currency = inv.get("fiat") or inv.get("paid_asset") or "RUB"
        if payment_id:
            order = await services.get_payment(c.db, payment_id)
            if order:
                result = await services.settle_payment(
                    c.db, c.settings, payment_id, f"cb_{invoice_id}", amount, order["user_id"],
                    currency=currency, provider="cryptobot",
                )
                if result and result.status == "credited" and c.notifier:
                    await c.notifier.payment_ok(result.user_id, result.qty, result.balance)
    return {"ok": True}


@router.post("/billing/webhook/xrocket")
async def xrocket_webhook(request: Request):
    """Вебхук от xRocket Pay (@xrocket / Ton Rocket)."""
    c = ctx_of(request)
    hub = c.payments
    provider = getattr(hub, "xrocket", None)
    if provider is None or not getattr(provider, "is_configured", False):
        raise HTTPException(503, "xRocket not configured")
    sig = request.headers.get("rocket-pay-signature", "")
    body_bytes = await request.body()
    if not provider.verify_webhook(body_bytes, sig):
        raise HTTPException(403, "Invalid signature")
    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "Bad JSON")
    if data.get("type") in ("invoicePay", "invoice_paid"):
        inv = data.get("data", {})
        payment_id = inv.get("payload") or inv.get("customData")
        invoice_id = str(inv.get("id"))
        amount = inv.get("amount", 0)
        currency = inv.get("currency") or "USDT"
        if payment_id:
            order = await services.get_payment(c.db, payment_id)
            if order:
                result = await services.settle_payment(
                    c.db, c.settings, payment_id, f"xr_{invoice_id}", amount, order["user_id"],
                    currency=currency, provider="xrocket",
                )
                if result and result.status == "credited" and c.notifier:
                    await c.notifier.payment_ok(result.user_id, result.qty, result.balance)
    return {"ok": True}


@router.get("/billing/payments/{payment_id}")
async def payment_state(payment_id: str, request: Request, row=Depends(user_row_any)):
    """Клиент опрашивает после закрытия окна оплаты: статус 'paid' значит, что генерации уже начислены."""
    if not re.fullmatch(r"[0-9a-f]{32}", payment_id):
        raise HTTPException(404, {"code": "no_payment"})
    c = ctx_of(request)
    status = await services.payment_status(c.db, row["id"], payment_id)
    if status is None:
        raise HTTPException(404, {"code": "no_payment"})
    # Фоновая сверка: если вебхук задержался, опрашиваем провайдера напрямую
    if status == "pending" and c.payments:
        order = await services.get_payment(c.db, payment_id)
        if order and "external_id" in order.keys() and order["external_id"]:
            ext_id = order["external_id"]
            method = order["method"] if "method" in order.keys() else "stars"
            amount_rub = order["amount_rub"] if "amount_rub" in order.keys() else 0
            if method == "cryptobot" and hasattr(c.payments, "cryptobot") and c.payments.cryptobot:
                inv_status = await c.payments.cryptobot.check_invoice(ext_id)
                if inv_status == "paid":
                    result = await services.settle_payment(
                        c.db, c.settings, payment_id, f"cb_{ext_id}", amount_rub,
                        row["id"], currency="RUB", provider="cryptobot"
                    )
                    if result and result.status == "credited":
                        status = "paid"
                        if c.notifier:
                            await c.notifier.payment_ok(result.user_id, result.qty, result.balance)
            elif method == "xrocket" and hasattr(c.payments, "xrocket") and c.payments.xrocket:
                inv_status = await c.payments.xrocket.check_invoice(ext_id)
                if inv_status == "paid":
                    result = await services.settle_payment(
                        c.db, c.settings, payment_id, f"xr_{ext_id}", amount_rub,
                        row["id"], currency="USDT", provider="xrocket"
                    )
                    if result and result.status == "credited":
                        status = "paid"
                        if c.notifier:
                            await c.notifier.payment_ok(result.user_id, result.qty, result.balance)
    return {"status": status}


# ───────────── кастомные эмодзи для текстов ─────────────

@router.get("/emoji")
async def emoji_map(request: Request):
    c = ctx_of(request)
    return {"emoji": emoji.public_map(c.settings, bot_ready=c.bot is not None)}


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


async def _emoji_response(c: Context, emoji_id: str, thumb: bool):
    if c.emoji_store is None:
        c.emoji_store = emoji.EmojiStore(c.settings.data_dir / "emoji")
    hit = await c.emoji_store.get(c.bot, emoji_id, thumb=thumb)
    if hit is None:
        raise HTTPException(404, {"code": "no_emoji"})
    path, media_type = hit
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=604800"})


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
