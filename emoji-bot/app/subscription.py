"""Общая проверка обязательной подписки для бота и Mini App.

Правила: отписался — доступ закрывается; сбой самой проверки доступ не открывает (503, можно повторить).

Что исправлено:
- Mini App спрашивал Telegram (getChatMember) на КАЖДЫЙ свой запрос. Под нагрузкой Telegram отвечал
  ошибкой «слишком часто» (429), и подписанным людям случайно показывался экран «подписка недоступна».
  Теперь свежая проверка — в боте и в /api/me (с него стартует приложение, туда же идёт кнопка
  «Проверить»), а остальные запросы Mini App берут результат из кэша (подписан — 2 минуты, нет — 5 секунд);
- если Telegram на миг не ответил, а человек недавно уже прошёл проверку, его не выкидывает на экран
  подписки;
- бота сняли с админов канала или канал не найден — раньше это молча закрывало приложение всем; теперь
  админу приходит предупреждение (не чаще раза в час).
"""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError

from . import runtime

log = logging.getLogger("ezzprod.subscription")

OK_TTL = 300        # недавний «подписан» выручает при сбое Telegram
OK_REUSE = 120      # «подписан» без нового запроса к Telegram (только запросы Mini App, кроме /api/me)
MISS_TTL = 5        # «не подписан» — несколько секунд
ALERT_EVERY = 3600  # предупреждение админу о сломанной проверке — не чаще раза в час


def _cache(ctx) -> dict:
    cache = getattr(ctx, "_subscription_cache", None)
    if cache is None:
        cache = {}
        try:
            setattr(ctx, "_subscription_cache", cache)
        except AttributeError:
            pass
    return cache


def reset_cache(ctx) -> None:
    _cache(ctx).clear()


async def _alert(ctx, reason: str) -> None:
    now = time.monotonic()
    last = getattr(ctx, "_subscription_alert_at", -ALERT_EVERY)
    if now - last < ALERT_EVERY:
        return
    try:
        setattr(ctx, "_subscription_alert_at", now)
    except AttributeError:
        pass
    log.warning("обязательная подписка не проверяется: %s", reason)
    try:
        await ctx.notifier.admin_alert(
            f"🔒 обязательная подписка не проверяется: {reason}. Пока это так, пользователи видят «проверка недоступна». "
            "Сделай бота администратором канала и проверь канал в /admin → Обязательная подписка.")
    except Exception:  # noqa: BLE001
        pass


class CheckUnavailable(Exception):
    pass


def channel_id(settings) -> str | int | None:
    raw = settings.subscription_channel.strip()
    if not raw:
        return None
    try:
        raw = runtime.parse(runtime.BY_KEY["subscription_channel"], raw)
    except ValueError:
        return None
    return int(raw) if raw.startswith("-100") else raw


def channel_url(settings) -> str:
    if settings.subscription_url:
        try:
            return runtime.parse(runtime.BY_KEY["subscription_url"], settings.subscription_url)
        except ValueError:
            return ""
    channel = channel_id(settings)
    return f"https://t.me/{channel[1:]}" if isinstance(channel, str) else ""


async def check(ctx, user_id: int, *, fresh: bool = True) -> bool:
    """True — подписан (или проверка не нужна). fresh=False — можно взять недавний результат из кэша.
    CheckUnavailable — проверить сейчас нельзя (доступ не даём, можно повторить)."""
    if not ctx.settings.subscription_required or runtime.is_admin(ctx, user_id):
        return True
    channel = channel_id(ctx.settings)
    if not channel or not channel_url(ctx.settings) or ctx.bot is None:
        raise CheckUnavailable
    cache = _cache(ctx)
    key = (str(channel), user_id)
    hit = cache.get(key)
    now = time.monotonic()
    if hit and not fresh and now - hit[1] < (OK_REUSE if hit[0] else MISS_TTL):
        return hit[0]
    try:
        member = await asyncio.wait_for(ctx.bot.get_chat_member(channel, user_id), timeout=8)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        # бот не админ канала / канал не найден: так нельзя проверить никого — нужно вмешательство админа
        await _alert(ctx, f"Telegram не даёт проверить канал {channel} ({type(exc).__name__})")
        raise CheckUnavailable from exc
    except (TelegramAPIError, TimeoutError, OSError) as exc:
        # Не пишем текст сетевой ошибки: он может содержать URL с токеном бота.
        log.warning("проверка подписки недоступна: channel=%s, error=%s", channel, type(exc).__name__)
        if hit and hit[0] and now - hit[1] < OK_TTL:
            return True  # недавно был подписан — не выкидываем из-за сбоя Telegram
        raise CheckUnavailable from exc
    status = getattr(member.status, "value", member.status)
    ok = status in ("creator", "administrator", "member") or (status == "restricted" and bool(getattr(member, "is_member", False)))
    cache[key] = (ok, now)
    if len(cache) > 50_000:
        cache.clear()
    return ok


def payload(ctx, *, unavailable: bool = False) -> dict:
    import re
    from .bot import texts

    channel = str(channel_id(ctx.settings) or "")
    raw_message = texts.render_msg(ctx, "subscription", channel=channel)

    sub_join_cfg = (getattr(ctx, "custom_buttons", None) or {}).get("sub_join") or {}
    sub_check_cfg = (getattr(ctx, "custom_buttons", None) or {}).get("sub_check") or {}

    raw_join = sub_join_cfg.get("text") or texts.get_custom_text(ctx, "subscription_join")
    raw_check = sub_check_cfg.get("text") or texts.get_custom_text(ctx, "subscription_check")
    raw_err = texts.get_custom_text(ctx, "subscription_error" if unavailable else "subscription_missing")
    return {
        "code": "subscription_unavailable" if unavailable else "subscription_required",
        "message": raw_message,
        "clean_message": re.sub(r"<[^>]+>", "", raw_message).strip(),
        "channel_url": channel_url(ctx.settings),
        "join_label": re.sub(r"<[^>]+>", "", raw_join).strip() or "Подписаться на канал",
        "check_label": re.sub(r"<[^>]+>", "", raw_check).strip() or "Проверить подписку",
        "error": re.sub(r"<[^>]+>", "", raw_err).strip(),
    }
