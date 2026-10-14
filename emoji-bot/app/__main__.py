"""Запуск: python -m app"""
from __future__ import annotations

import logging
import re

import uvicorn

from .config import Settings, env_file_path
from .main import create_app

_TOKEN = re.compile(r"(bot)?\d{6,}:[A-Za-z0-9_-]{20,}")
_plain = logging.Formatter()


def _redact(value):
    return _TOKEN.sub("<token>", value) if isinstance(value, str) else value


class RedactTokens(logging.Filter):
    """Токен бота = ключ подписи initData. Он не должен попадать в логи (URL запросов к Telegram содержат его):
    ни в сообщение, ни в аргументы, ни в текст исключения (traceback) или stack_info."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact(str(record.msg))
        if isinstance(record.args, dict):
            record.args = {k: _redact(v) for k, v in record.args.items()}
        elif record.args:
            record.args = tuple(_redact(a) for a in record.args)
        if record.exc_info and not record.exc_text:
            # оформляем traceback заранее и чистим: Formatter возьмёт готовый exc_text, а не соберёт заново
            record.exc_text = _plain.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = _redact(record.exc_text)
        if record.stack_info:
            record.stack_info = _redact(record.stack_info)
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
    # log_config=None: логи uvicorn идут через корневые обработчики с фильтром токенов, а не через свои
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info", log_config=None)


if __name__ == "__main__":
    main()
