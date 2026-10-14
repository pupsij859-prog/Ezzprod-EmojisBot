"""Общий контекст приложения: им пользуются и FastAPI-роуты, и обработчики бота."""
from __future__ import annotations

import time
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

    # адрес Mini App, полученный на лету (временный туннель переподключился и сменил адрес —
    # см. app/public_url.py и tools/tunnel.py). Пусто — используется WEBAPP_URL из настроек.
    public_url: str = ""

    # админка в чате (app/runtime.py): настройки из .env и правки поверх них, флаги, админы из базы
    base_settings: Settings | None = None
    overrides: dict = field(default_factory=dict)
    maintenance: bool = False
    hidden_categories: frozenset = frozenset()
    extra_admins: frozenset = frozenset()
    started_at: float = field(default_factory=time.time)

    @property
    def webapp_url(self) -> str:
        return self.public_url or self.settings.webapp_url

    def ref_link(self, ref_code: str) -> str | None:
        # без имени бота ссылки нет: «t.me/your_bot» вёл бы в чужого бота
        if not self.bot_username:
            return None
        return f"https://t.me/{self.bot_username}?start=ref_{ref_code}"

    @property
    def support_url(self) -> str | None:
        name = self.settings.support_username
        return f"https://t.me/{name}" if name else None
