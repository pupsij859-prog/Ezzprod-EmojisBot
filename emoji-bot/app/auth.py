"""Проверка подписи initData Telegram Mini App и зависимость FastAPI для авторизации."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request

INIT_DATA_MAX_AGE = 24 * 60 * 60  # initData старше суток не принимаем (рекомендация Telegram)
CLOCK_SKEW = 5 * 60               # допуск на расхождение часов
START_PARAM = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")

_FORWARD_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip")
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class InitDataError(Exception):
    pass


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age: int = INIT_DATA_MAX_AGE,
    now: float | None = None,
) -> dict:
    """Возвращает dict пользователя Telegram или бросает InitDataError."""
    if not init_data or not bot_token:
        raise InitDataError("empty")
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise InitDataError("malformed") from exc

    fields = dict(pairs)
    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise InitDataError("no hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise InitDataError("bad hash")

    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError as exc:
        raise InitDataError("bad auth_date") from exc
    age = (now if now is not None else time.time()) - auth_date
    if age > max_age:
        raise InitDataError("expired")
    if age < -CLOCK_SKEW:
        raise InitDataError("from the future")

    try:
        user = json.loads(fields.get("user", ""))
    except json.JSONDecodeError as exc:
        raise InitDataError("no user") from exc
    if not isinstance(user, dict) or not isinstance(user.get("id"), int):
        raise InitDataError("no user id")
    # start_param тоже подписан Telegram'ом, но значение задаёт любой, кто создал ссылку, — недоверенный ввод
    start_param = fields.get("start_param", "")
    if START_PARAM.match(start_param):
        user["_start_param"] = start_param
    return user


def _dev_bypass_allowed(request: Request) -> bool:
    """Обход подписи — только при DEV_MODE, только с loopback и без прокси-заголовков."""
    settings = request.app.state.settings
    if not settings.dev_mode:
        return False
    client = request.client.host if request.client else ""
    if client not in _LOOPBACK:
        return False
    return not any(h in request.headers for h in _FORWARD_HEADERS)


async def current_user(request: Request) -> dict:
    """Зависимость: возвращает dict Telegram-пользователя (id, first_name, …).
    Mini App шлёт «tma <initData>», сайт (веб-редактор) — «Bearer <токен сессии>» из входа через Telegram."""
    settings = request.app.state.settings

    dev_id = request.headers.get("x-dev-user")
    if dev_id and _dev_bypass_allowed(request):
        try:
            uid = int(dev_id)
        except ValueError:
            raise HTTPException(401, "bad dev user")
        return {"id": uid, "first_name": "dev", "username": f"dev{uid}"}

    header = request.headers.get("authorization", "")
    scheme, _, init_data = header.partition(" ")
    if scheme.lower() == "bearer" and init_data:
        from .web_login import session_user

        user = await session_user(request.app.state.ctx.db, init_data.strip())
        if user is None:
            raise HTTPException(401, "unauthorized")
        return user
    if scheme.lower() != "tma" or not init_data:
        raise HTTPException(401, "unauthorized")
    try:
        return validate_init_data(init_data, settings.bot_token)
    except InitDataError:
        raise HTTPException(401, "unauthorized")
