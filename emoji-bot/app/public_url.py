"""Живой адрес Mini App для временного туннеля.

Бесплатные туннели (localhost.run и подобные) при переподключении выдают новый адрес. Скрипт tools/tunnel.py
пишет текущий адрес в файл PUBLIC_URL_FILE, а этот сторож раз в несколько секунд его перечитывает: при смене
обновляет адрес в контексте (кнопки «открыть редактор» в /start) и перевыставляет кнопку меню бота.
В проде с постоянным доменом не нужен: просто задай WEBAPP_URL и не задавай PUBLIC_URL_FILE.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from .context import Context

log = logging.getLogger("ezzprod.public_url")

_URL = re.compile(r"^https://[A-Za-z0-9.-]+\Z")


def read_url(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip().rstrip("/")
    except OSError:
        return None
    return value if _URL.match(value) else None


async def apply_url(ctx: Context, url: str) -> bool:
    """Применяет новый адрес. → True, если адрес действительно сменился."""
    if not url or url == ctx.webapp_url:
        return False
    ctx.public_url = url
    log.warning("адрес Mini App сменился: %s", url)
    if ctx.bot is not None:
        from .bot import set_menu_button

        try:
            await set_menu_button(ctx.bot, url)
        except Exception:
            log.exception("не удалось обновить кнопку меню")
    return True


async def watch_public_url(ctx: Context, path: Path, interval_s: float = 5.0) -> None:
    while True:
        url = read_url(path)
        if url:
            await apply_url(ctx, url)
        await asyncio.sleep(interval_s)
