"""Общий контекст приложения: им пользуются и FastAPI-роуты, и обработчики бота."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings
from .db import Database
from .notify import Notifier, NullNotifier
from .packs import PackBuilder, PackRunner
from .payments import PaymentProvider
from .ratelimit import RateLimiter


@dataclass
class Context:
    settings: Settings
    db: Database
    builder: PackBuilder
    runner: PackRunner
    notifier: Notifier = field(default_factory=NullNotifier)
    payments: PaymentProvider | None = None
    limiter: RateLimiter = field(default_factory=RateLimiter)
    bot_username: str = ""
    bot: object | None = None  # aiogram.Bot, если бот запущен
    banner_file_id: str | None = None  # file_id баннера после первой отправки
    emoji_store: object | None = None  # app.emoji.EmojiStore, создаётся при первом запросе эмодзи

    def ref_link(self, ref_code: str) -> str:
        return f"https://t.me/{self.bot_username or 'your_bot'}?start=ref_{ref_code}"

    @property
    def support_url(self) -> str | None:
        name = self.settings.support_username
        return f"https://t.me/{name}" if name else None
