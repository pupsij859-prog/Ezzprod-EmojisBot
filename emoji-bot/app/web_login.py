"""Вход на сайте (веб-редактор EzzProd) через Telegram Login Widget и сессии сайта.

Виджет отдаёт поля пользователя и hash. Проверка (https://core.telegram.org/widgets/login#checking-authorization):
секрет = sha256(BOT_TOKEN), строка проверки — отсортированные «ключ=значение» через \\n без hash,
hash = hex(HMAC-SHA256(секрет, строка)). auth_date не старше суток.

После проверки сайт получает токен сессии и шлёт его как «Authorization: Bearer <токен>». В базе лежит только
sha256 токена: утёкшая база не даёт войти. Пользователь — тот же users.id (Telegram ID), что и в боте.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from .db import Database

LOGIN_MAX_AGE = 24 * 60 * 60  # данные виджета старше суток не принимаем
CLOCK_SKEW = 5 * 60
# поля, которые присылает виджет; всё остальное в подписи не участвует и не принимается
FIELDS = ("id", "first_name", "last_name", "username", "photo_url", "auth_date", "hash")


class LoginError(Exception):
    pass


def validate_login(data: dict, bot_token: str, *, max_age: int = LOGIN_MAX_AGE, now: float | None = None) -> dict:
    """Поля виджета → dict пользователя Telegram (id, first_name, last_name, username) или LoginError."""
    if not bot_token or not isinstance(data, dict) or not data:
        raise LoginError("empty")
    if not set(data) <= set(FIELDS):
        raise LoginError("unknown field")
    fields = {k: v for k, v in data.items() if v is not None}
    if not all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in fields.values()):
        raise LoginError("malformed")
    received = fields.pop("hash", None)
    if not isinstance(received, str) or not received:
        raise LoginError("no hash")
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hashlib.sha256(bot_token.encode()).digest()
    calculated = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received.lower()):
        raise LoginError("bad hash")
    try:
        auth_date = int(fields.get("auth_date", 0))
        uid = int(fields["id"])
    except (KeyError, ValueError) as exc:
        raise LoginError("bad fields") from exc
    age = (now if now is not None else time.time()) - auth_date
    if age > max_age:
        raise LoginError("expired")
    if age < -CLOCK_SKEW:
        raise LoginError("from the future")
    if uid <= 0:
        raise LoginError("bad id")
    return {
        "id": uid,
        "first_name": str(fields.get("first_name", "")),
        "last_name": str(fields.get("last_name", "")),
        "username": str(fields["username"]) if fields.get("username") else None,
    }


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(db: Database, user_id: int, days: int) -> tuple[str, int]:
    """Новая сессия сайта → (токен, когда истекает). Заодно чистит истёкшие сессии этого пользователя."""
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    expires = now + max(1, days) * 24 * 60 * 60
    async with db.tx() as t:
        await t.execute("DELETE FROM web_sessions WHERE user_id=? AND expires_at<=?", (user_id, now))
        await t.execute("INSERT INTO web_sessions (token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                        (_hash(token), user_id, now, expires))
    return token, expires


async def session_user(db: Database, token: str) -> dict | None:
    """Пользователь по токену сессии (в том же виде, что из initData) или None — нет, истекла."""
    if not token or len(token) > 128:
        return None
    row = await db.fetchone(
        "SELECT u.id, u.first_name, u.last_name, u.username FROM web_sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash=? AND s.expires_at>?",
        (_hash(token), int(time.time())),
    )
    if row is None:
        return None
    return {"id": row["id"], "first_name": row["first_name"], "last_name": row["last_name"], "username": row["username"]}


async def drop_session(db: Database, token: str) -> None:
    async with db.tx() as t:
        await t.execute("DELETE FROM web_sessions WHERE token_hash=?", (_hash(token),))
