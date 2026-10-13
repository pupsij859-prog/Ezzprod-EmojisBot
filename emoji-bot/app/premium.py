"""Премиум-эмодзи Telegram для текстов Mini App (паки Finance Emoji и Kawaii Emoji).

Список лежит в app/data/premium_packs.json: пак → эмодзи (custom_emoji_id + базовый эмодзи).
Статичные превью 128×128 — в webapp/assets/packs/<pak>/<id>.webp; анимированную версию бот скачивает
по ID через app/emoji.EmojiStore (только для ID из этого списка).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "premium_packs.json"


@lru_cache(maxsize=1)
def packs() -> tuple[dict, ...]:
    return tuple(json.loads(DATA.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def _pack_by_emoji() -> dict[str, str]:
    return {item["id"]: p["id"] for p in packs() for item in p["items"]}


def known(emoji_id: str) -> bool:
    return emoji_id in _pack_by_emoji()


def thumb(emoji_id: str) -> str | None:
    pack = _pack_by_emoji().get(emoji_id)
    return f"assets/packs/{pack}/{emoji_id}.webp" if pack else None

