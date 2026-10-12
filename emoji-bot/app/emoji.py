"""Кастомные эмодзи Telegram для текстовых блоков-цитат Mini App.

Клиент знает только ключи («gift», «warn»…). Для каждого ключа есть обычный эмодзи-заглушка и, если задан,
custom_emoji_id. Файл эмодзи бот один раз скачивает через Bot API (getCustomEmojiStickers → getFile)
и кладёт в папку данных; дальше отдаём с диска. Публичная ручка отдаёт только ключи из этого списка,
поэтому через неё нельзя заставить бота ходить в Telegram за произвольными ID.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import time
import zlib
from pathlib import Path

from .config import Settings

log = logging.getLogger("ezzprod.emoji")

# ключ → обычный эмодзи, который показывается, пока нет ID (или бот не запущен)
FALLBACK: dict[str, str] = {
    "sparkle": "✨", "palette": "🎨", "gift": "🎁", "box": "📦", "friends": "🤝", "crown": "👑",
    "star": "⭐", "warn": "⚠️", "info": "💡", "lock": "🔒", "test": "🧪", "rocket": "🚀",
    "chart": "📊", "money": "💰", "users": "👥", "check": "✅", "clock": "⏳", "pin": "📌",
    "hand": "👆", "phone": "📱", "fire": "🔥",
}

# ключ → custom_emoji_id (узнать ID: переслать сообщение с эмодзи боту @idstickerbot).
# Пустая строка — показываем обычный эмодзи. Переменная CUSTOM_EMOJI в .env дополняет и перекрывает этот список.
IDS: dict[str, str] = {key: "" for key in FALLBACK}

ID_RE = re.compile(r"^\d{1,24}$")
MAX_BYTES = 2 * 1024 * 1024      # распакованный TGS бывает до ~1 МБ, видео-эмодзи до 256 КБ
NEGATIVE_TTL = 600               # после ошибки Telegram не спрашиваем этот ID 10 минут

MEDIA_TYPES = {"json": "application/json", "webm": "video/webm", "webp": "image/webp"}


def ids(settings: Settings) -> dict[str, str]:
    merged = {**IDS, **dict(settings.custom_emoji)}
    return {k: v for k, v in merged.items() if k in FALLBACK and ID_RE.match(v or "")}


def public_map(settings: Settings, bot_ready: bool) -> dict[str, dict]:
    """Что отдаём клиенту: заглушку и признак «есть кастомный» для каждого ключа."""
    custom = ids(settings) if bot_ready else {}
    return {k: {"fallback": fb, "custom": k in custom} for k, fb in FALLBACK.items()}


class EmojiStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._locks: dict[str, asyncio.Lock] = {}
        self._failed: dict[str, float] = {}

    def _cached(self, stem: str) -> tuple[Path, str] | None:
        for ext, media in MEDIA_TYPES.items():
            path = self.folder / f"{stem}.{ext}"
            if path.exists():
                return path, media
        return None

    async def get(self, bot, emoji_id: str, thumb: bool = False) -> tuple[Path, str] | None:
        """→ (файл, media type) или None, если эмодзи получить нельзя."""
        stem = f"{emoji_id}.thumb" if thumb else emoji_id
        hit = self._cached(stem)
        if hit is not None:
            return hit
        if bot is None or time.monotonic() - self._failed.get(stem, -NEGATIVE_TTL) < NEGATIVE_TTL:
            return None
        lock = self._locks.setdefault(stem, asyncio.Lock())
        async with lock:
            hit = self._cached(stem)
            if hit is not None:
                return hit
            try:
                return await self._download(bot, emoji_id, stem, thumb)
            except Exception:
                log.warning("не удалось получить кастомный эмодзи %s", emoji_id, exc_info=True)
                self._failed[stem] = time.monotonic()
                return None

    async def _download(self, bot, emoji_id: str, stem: str, thumb: bool) -> tuple[Path, str] | None:
        stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=[emoji_id])
        if not stickers:
            self._failed[stem] = time.monotonic()
            return None
        st = stickers[0]
        if thumb:
            if st.thumbnail is None:
                return None
            file_id, ext = st.thumbnail.file_id, "webp"
        elif st.is_animated:
            file_id, ext = st.file_id, "json"
        elif st.is_video:
            file_id, ext = st.file_id, "webm"
        else:
            file_id, ext = st.file_id, "webp"

        file = await bot.get_file(file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, destination=buf)
        data = buf.getvalue()
        if ext == "json":  # TGS — это Lottie JSON в gzip; распаковываем с ограничением размера
            data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(data, MAX_BYTES + 1)
        if not data or len(data) > MAX_BYTES:
            self._failed[stem] = time.monotonic()
            return None

        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{stem}.{ext}"
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        return path, MEDIA_TYPES[ext]
