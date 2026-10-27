"""API веб-редактора на сайте EzzProd: вход через Telegram Login Widget, превью и скачивание своего .tgs,
пак по ссылке. Сайт живёт на другом домене, поэтому CORS открыт только для WEB_ORIGINS (см. main.create_app).
Остальное (шаблоны, шрифты, логотипы, паки, баланс) сайт берёт из обычного API Mini App с токеном сессии."""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import re
import zlib

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .. import catalog, services, web_login
from .routes import ctx_of, render_anim, render_look, render_response, user_row

router = APIRouter(prefix="/api/web")

MAX_TGS_BYTES = 512 * 1024        # свой файл: .tgs (gzip) или Lottie .json
MAX_LOTTIE_BYTES = 2 * 1024 * 1024  # после распаковки
SET_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}\Z")
SET_LINK = re.compile(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(?:addemoji|addstickers)/([A-Za-z0-9_]{1,64})/?\Z")
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{16,200}\Z")


def _client_key(request: Request) -> int:
    host = request.client.host if request.client else ""
    return int(hashlib.sha256(host.encode()).hexdigest()[:12], 16)


@router.get("/config")
async def web_config(request: Request):
    """Что нужно сайту для кнопки «Войти через Telegram»: имя и id бота (id — часть токена до «:», он публичный)."""
    c = ctx_of(request)
    token = c.settings.bot_token
    bot_id = int(token.split(":", 1)[0]) if token and token.split(":", 1)[0].isdigit() else None
    return {
        "bot_username": c.bot_username or c.settings.bot_username or None,
        "bot_id": bot_id,
        "export_price": max(0, c.settings.web_export_price),
        "dev": c.settings.dev_mode,
    }


async def _session_for(c, tg: dict) -> dict:
    row, _created = await services.upsert_user(c.db, c.settings, tg)
    if row["banned"]:
        raise HTTPException(403, {"code": "banned"})
    token, expires = await web_login.create_session(c.db, row["id"], c.settings.web_session_days)
    return {"token": token, "expires_at": expires}


@router.post("/login")
async def web_login_route(request: Request, body: dict = Body(...)):
    """Данные Telegram Login Widget → токен сессии сайта. Аккаунт тот же, что в боте (users.id = Telegram ID)."""
    c = ctx_of(request)
    if not c.limiter.allow("web-login", _client_key(request), 120, 60):
        raise HTTPException(429, {"code": "rate_limited"})
    try:
        tg = web_login.validate_login(body, c.settings.bot_token)
    except web_login.LoginError:
        raise HTTPException(401, {"code": "bad_login"}) from None
    return await _session_for(c, tg)


@router.post("/logout")
async def web_logout(request: Request):
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        await web_login.drop_session(ctx_of(request).db, token.strip())
    return {"ok": True}


# ───────────── свой .tgs: превью и скачивание ─────────────

def parse_lottie(data: bytes) -> dict:
    """Свой файл → Lottie dict. .tgs — gzip, распаковываем с ограничением размера (защита от «zip-бомбы»)."""
    if data[:2] == b"\x1f\x8b":
        try:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            data = d.decompress(data, MAX_LOTTIE_BYTES + 1)
        except zlib.error:
            raise HTTPException(422, {"code": "bad_file"}) from None
        if len(data) > MAX_LOTTIE_BYTES or d.unconsumed_tail:
            raise HTTPException(413, {"code": "too_large"})
    try:
        anim = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, {"code": "bad_file"}) from None
    ok = (isinstance(anim, dict) and isinstance(anim.get("layers"), list)
          and all(isinstance(anim.get(k), (int, float)) and not isinstance(anim.get(k), bool) for k in ("w", "h", "fr", "op")))
    if not ok:
        raise HTTPException(422, {"code": "bad_file"})
    return anim


async def _source(request: Request, params: dict, file: UploadFile | None):
    """Откуда рисуем: шаблон из каталога (template_id) или свой файл. → (функция загрузки, подпись для логов)."""
    from .. import lottie

    if file is not None:
        data = await file.read(MAX_TGS_BYTES + 1)
        if len(data) > MAX_TGS_BYTES:
            raise HTTPException(413, {"code": "too_large"})
        anim = parse_lottie(data)
        return (lambda: json.loads(json.dumps(anim))), "file"
    tpl = catalog.get(params.get("template_id") or "")
    if tpl is None or tpl.category in ctx_of(request).hidden_categories:
        raise HTTPException(404, {"code": "no_template"})
    return (lambda: lottie.load(tpl.path)), tpl.id


def _params(raw: str) -> dict:
    try:
        params = json.loads(raw or "{}")
    except ValueError:
        params = None
    if not isinstance(params, dict):
        raise HTTPException(422, {"code": "validation", "field": "params"})
    return params


@router.post("/render")
async def web_render(request: Request, params: str = Form("{}"), file: UploadFile | None = File(None),
                     row=Depends(user_row)):
    """Превью веб-редактора (шаблон или свой файл). Как в паке: правки частей, цвета и эффекты (edit) — только Pro,
    ник, шрифт, логотип и цвет надписи — всем."""
    c = ctx_of(request)
    if not c.limiter.allow("render", row["id"], 600, 60):
        raise HTTPException(429, {"code": "rate_limited"})
    body = _params(params)
    load, label = await _source(request, body, file)
    look = await render_look(c, row["id"], body)
    if look["edit"] and row["plan"] != "pro":
        raise services.ProRequired()
    return await render_response(request, row["id"], load, look, label)


@router.post("/export")
async def web_export(request: Request, params: str = Form("{}"), file: UploadFile | None = File(None),
                     idempotency_key: str | None = Header(default=None), row=Depends(user_row)):
    """«Скачать .tgs»: готовый файл под требования Telegram. Стоит WEB_EXPORT_PRICE генераций, списание — через ledger
    и идемпотентно по Idempotency-Key: повтор с тем же ключом отдаёт файл ещё раз и не списывает второй раз."""
    c = ctx_of(request)
    if not idempotency_key or not services.IDEM_KEY.match(idempotency_key):
        raise HTTPException(422, {"code": "validation", "field": "idempotency_key"})
    if not c.limiter.allow("export", row["id"], 30, 60):
        raise HTTPException(429, {"code": "rate_limited"})
    body = _params(params)
    load, label = await _source(request, body, file)
    look = await render_look(c, row["id"], body)
    if look["edit"] and row["plan"] != "pro":
        raise services.ProRequired()
    try:
        _base, _out, data = await asyncio.to_thread(render_anim, load(), look, mark=False)
    except Exception:
        raise HTTPException(500, {"code": "render_failed"}) from None
    await services.charge_export(c.db, row["id"], c.settings.web_export_price, idempotency_key)
    name = re.sub(r"[^A-Za-z0-9_-]", "", label) or "emoji"
    return Response(data, media_type="application/x-tgsticker",
                    headers={"Content-Disposition": f'attachment; filename="{name}.tgs"', "Cache-Control": "no-store"})


# ───────────── пак по ссылке ─────────────

@router.get("/stickerset")
async def sticker_set(link: str, request: Request, row=Depends(user_row)):
    """Пак эмодзи или стикеров по ссылке t.me/addemoji/… → анимированные (TGS) элементы, их можно открыть в редакторе."""
    c = ctx_of(request)
    m = SET_LINK.match(link.strip())
    name = m.group(1) if m else link.strip()
    if not SET_NAME.match(name):
        raise HTTPException(422, {"code": "validation", "field": "link"})
    if c.bot is None:
        raise HTTPException(503, {"code": "bot_unavailable"})
    if not c.limiter.allow("stickerset", row["id"], 20, 60):
        raise HTTPException(429, {"code": "rate_limited"})
    try:
        st = await c.bot.get_sticker_set(name=name)
    except Exception:
        raise HTTPException(404, {"code": "no_set"}) from None
    items = [{"file_id": s.file_id, "emoji": s.emoji or ""} for s in st.stickers if s.is_animated][:200]
    return {"name": st.name, "title": st.title, "items": items, "skipped": len(st.stickers) - len(items)}


@router.get("/sticker/{file_id}")
async def sticker_file(file_id: str, request: Request, row=Depends(user_row)):
    """Файл элемента пака (.tgs) — как файл шаблона: Lottie JSON с Content-Encoding: gzip."""
    c = ctx_of(request)
    if not FILE_ID.match(file_id):
        raise HTTPException(404, {"code": "no_sticker"})
    if c.bot is None:
        raise HTTPException(503, {"code": "bot_unavailable"})
    if not c.limiter.allow("sticker", row["id"], 240, 60):
        raise HTTPException(429, {"code": "rate_limited"})
    try:
        tg_file = await c.bot.get_file(file_id)
        buf = io.BytesIO()
        await c.bot.download_file(tg_file.file_path, destination=buf)
    except Exception:
        raise HTTPException(404, {"code": "no_sticker"}) from None
    data = buf.getvalue()
    if len(data) > MAX_TGS_BYTES:
        raise HTTPException(413, {"code": "too_large"})
    parse_lottie(data)  # не отдаём то, что редактор всё равно не откроет
    if data[:2] != b"\x1f\x8b":
        data = gzip.compress(data)
    return Response(data, media_type="application/json",
                    headers={"Content-Encoding": "gzip", "Cache-Control": "private, max-age=86400"})
