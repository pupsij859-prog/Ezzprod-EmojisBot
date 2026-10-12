"""Запуск: python -m app"""
from __future__ import annotations

import logging
import re

import uvicorn

from .config import Settings, env_file_path
from .main import create_app

_TOKEN = re.compile(r"\b(bot)?\d{6,}:[A-Za-z0-9_-]{20,}\b")


class RedactTokens(logging.Filter):
    """Токен бота = ключ подписи initData. Он не должен попадать в логи (URL запросов к Telegram содержат его)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _TOKEN.sub("<token>", str(record.msg))
        if record.args:
            record.args = tuple(_TOKEN.sub("<token>", a) if isinstance(a, str) else a for a in record.args)
        return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactTokens())
    # заметно меньше шума и никаких URL с токеном
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("aiogram.event").setLevel(logging.INFO)

    settings = Settings.from_env()
    env = env_file_path()
    print(f"\n  {settings.brand_name} bot  ·  http://{settings.host}:{settings.port}")
    print(f"  настройки: {env if env else 'файл .env не найден (используются переменные окружения)'}")
    print(f"  данные:    {settings.data_dir}")
    if not settings.bot_token:
        print("  ! BOT_TOKEN не задан: бот и оплата Stars выключены")
    if settings.webapp_url:
        print(f"  Mini App:  {settings.webapp_url}")
    else:
        print("  ! WEBAPP_URL не задан: для Telegram нужен публичный https-адрес (туннель)")
    print()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
