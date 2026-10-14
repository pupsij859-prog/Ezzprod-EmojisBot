"""Свой шрифт пользователя для ника: загрузка, проверка, хранение.

Ник в TGS — контуры букв, поэтому шрифт нужен с контурами TrueType (таблица glyf): обычный .ttf. OpenType
с контурами CFF (.otf) наш разборщик (app/fonts.py) не читает — такой файл отклоняем с понятной ошибкой.
Файл сохраняется как <id>.ttf в папке данных, в БД — владелец и имя для показа. Сборщик и превью рисуют
ник этим шрифтом; буквы, которых в шрифте нет, выходят знаком «?» этого шрифта.
"""
from __future__ import annotations

import asyncio
import re
import struct
import uuid
from pathlib import Path

from . import fonts
from .config import Settings
from .db import Database
from .services import ValidationFailed, clean_text, now

FONT_ID = re.compile(r"^[0-9a-f]{32}\Z")
PROBE = "Ezz Ник 123"


def _check(data: bytes) -> None:
    if data[:4] == b"OTTO":
        raise ValidationFailed("font_otf")  # OpenType с контурами CFF
    if data[:4] in (b"wOFF", b"wOF2"):
        raise ValidationFailed("font_web")
    try:
        font = fonts.TrueTypeFont(data)
        drawn = 0
        for ch in PROBE:
            gid = font.glyph_id(ch)
            if gid and font.contours(gid):
                drawn += 1
    except (fonts.FontError, struct.error, IndexError, ValueError):
        raise ValidationFailed("font_invalid")
    if drawn < 3:
        raise ValidationFailed("font_empty")  # в шрифте нет обычных букв


def font_path(settings: Settings, font_id: str | None) -> Path | None:
    """Путь к файлу шрифта или None (нет id, кривой id или файла уже нет)."""
    if not font_id or not FONT_ID.match(font_id):
        return None
    path = Path(settings.fonts_dir) / f"{font_id}.ttf"
    return path if path.is_file() else None


async def save_font(db: Database, settings: Settings, user_id: int, filename: str, data: bytes) -> dict:
    if len(data) > settings.font_max_bytes:
        raise ValidationFailed("font_too_big")
    count = await db.fetchone("SELECT COUNT(*) AS n FROM fonts WHERE user_id=?", (user_id,))
    if count["n"] >= settings.fonts_max:
        raise ValidationFailed("font_quota")
    await asyncio.to_thread(_check, data)
    font_id = uuid.uuid4().hex
    Path(settings.fonts_dir).mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread((Path(settings.fonts_dir) / f"{font_id}.ttf").write_bytes, data)
    name = clean_text(Path(filename or "font").stem, 48) or "шрифт"
    async with db.tx() as t:
        await t.execute("INSERT INTO fonts (id, user_id, name, created_at) VALUES (?,?,?,?)",
                        (font_id, user_id, name, now()))
    return {"id": font_id, "name": name}


async def owns(db: Database, user_id: int, font_id: str) -> bool:
    row = await db.fetchone("SELECT 1 FROM fonts WHERE id=? AND user_id=?", (font_id, user_id))
    return row is not None


async def delete_font(db: Database, settings: Settings, user_id: int, font_id: str) -> bool:
    async with db.tx() as t:
        row = await t.fetchone("SELECT 1 FROM fonts WHERE id=? AND user_id=?", (font_id, user_id))
        if row is None:
            return False
        await t.execute("DELETE FROM fonts WHERE id=?", (font_id,))
        # шрифт уже в паке: запись убираем (освобождаем квоту), а файл оставляем для сборки
        in_pack = await t.fetchone(
            "SELECT 1 FROM packs WHERE user_id=? AND (json_extract(params, '$.font_id')=? OR id IN "
            "(SELECT pack_id FROM pack_items WHERE json_extract(params, '$.font_id')=?))",
            (user_id, font_id, font_id))
    if in_pack is None:
        try:
            await asyncio.to_thread((Path(settings.fonts_dir) / f"{font_id}.ttf").unlink, True)
        except OSError:
            pass
    return True
