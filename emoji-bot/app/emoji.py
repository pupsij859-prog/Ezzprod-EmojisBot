"""Эмодзи в текстовых блоках-цитатах Mini App: премиум-эмодзи Telegram и рисованные SVG.

Клиент знает только ключи («gift», «warn»…). Для ключа есть либо премиум-эмодзи (custom_emoji_id из
app/data/premium_packs.json: сразу показываем его превью WebP, а анимированную версию бот один раз скачивает
через Bot API и кладёт в папку данных), либо рисованный SVG из webapp/assets/emoji. Обычные эмодзи-символы
не используются: на разных системах они выглядят по-разному.
Публичные ручки отдают файлы только для известных ключей и ID премиум-паков, поэтому через них нельзя
заставить бота ходить в Telegram за произвольными ID.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import time
import zlib
from pathlib import Path

from . import premium
from .config import Settings

log = logging.getLogger("ezzprod.emoji")

# ключ → обычный эмодзи (только подпись для читалок экрана, на экран не выводится)
KEYS: dict[str, str] = {
    "sparkle": "✨", "palette": "🎨", "gift": "🎁", "box": "📤", "friends": "🤝", "crown": "👑",
    "star": "⭐", "warn": "⚠️", "info": "💡", "lock": "🔒", "test": "🧪", "rocket": "🚀",
    "chart": "📊", "money": "💰", "users": "👥", "check": "✅", "clock": "⏱", "pin": "✍️",
    "hand": "☝️", "phone": "📱", "fire": "🔥", "heart": "❤️", "card": "💳",
    "gem": "💎", "oops": "🥲", "party": "🥳", "sad": "😢", "bag": "🛍", "heart_hands": "🫶",
    "cart": "🛒", "chat": "🤗", "docs": "🧾", "question": "❓",
}

# ключ → custom_emoji_id премиум-эмодзи (паки Finance Emoji и Kawaii Emoji).
# Переменная CUSTOM_EMOJI в .env дополняет и перекрывает этот список.
IDS: dict[str, str] = {
    "sparkle": "5463297803235113601", "gift": "5203996991054432397", "box": "5445355530111437729",
    "friends": "5395732581780040886", "crown": "5431505596316665041", "star": "5267500801240092311",
    "info": "5262844652964303985", "rocket": "5195033767969839232", "chart": "5190806721286657692",
    "money": "5287231198098117669", "clock": "5382194935057372936", "pin": "5197269100878907942",
    "hand": "5389062952835886404", "fire": "5389038097860144794", "heart": "5267102644886853973",
    "card": "5445353829304387411",
    "gem": "5235630047959727475", "oops": "5294541369874851363", "party": "5328273248448686763",
    "sad": "5391310895768936514", "bag": "5294167145079395967", "heart_hands": "5397890811436213746",
    "cart": "5400090058030075645",
    "chat": "5456611707386340923", "docs": "5444856076954520455", "question": "5235711188481883685",
}

# ключи, для которых нарисован webapp/assets/emoji/<ключ>.svg (нет пары в премиум-паках)
SVG: frozenset[str] = frozenset({"palette", "warn", "lock", "test", "users", "check", "phone", "cart"})

ID_RE = re.compile(r"^\d{1,24}\Z")
MAX_BYTES = 2 * 1024 * 1024      # распакованный TGS бывает до ~1 МБ, видео-эмодзи до 256 КБ
NEGATIVE_TTL = 600               # после ошибки Telegram не спрашиваем этот ID 10 минут

MEDIA_TYPES = {"json": "application/json", "webm": "video/webm", "webp": "image/webp"}


def ids(settings: Settings) -> dict[str, str]:
    merged = {**IDS, **dict(settings.custom_emoji)}
    return {k: v for k, v in merged.items() if k in KEYS and ID_RE.match(v or "")}


def allowed_id(settings: Settings, emoji_id: str) -> bool:
    """Анимацию отдаём только для премиум-паков и ID, назначенных ключам."""
    return bool(ID_RE.match(emoji_id)) and (premium.known(emoji_id) or emoji_id in ids(settings).values())


def public_map(settings: Settings, bot_ready: bool) -> dict[str, dict]:
    """Для каждого ключа: id (если есть), превью WebP или SVG и признак «можно анимировать»."""
    by_key = ids(settings)
    out = {}
    for key, alt in KEYS.items():
        emoji_id = by_key.get(key)
        out[key] = {
            "alt": alt,
            "id": emoji_id,
            "thumb": premium.thumb(emoji_id) if emoji_id else None,
            "svg": f"assets/emoji/{key}.svg" if key in SVG else None,
            "custom": bool(emoji_id) and bot_ready,
        }
    return out


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
