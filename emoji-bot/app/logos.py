"""Загрузка логотипа: проверка, перекодирование (убирает EXIF и прочий мусор), сохранение."""
from __future__ import annotations

import asyncio
import base64
import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Settings
from .db import Database
from .services import ValidationFailed, clean_text, now

MAX_PIXELS = 12_000_000      # защита от decompression bomb (≈ 48 МБ в RGBA)
MAX_SIDE_STORED = 512
THUMB_SIDE = 96
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}

# одновременно декодируем не больше двух картинок, чтобы не раздувать память
_decode_slots = asyncio.Semaphore(2)


def _process(data: bytes) -> tuple[bytes, str]:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        probe = Image.open(io.BytesIO(data))
        if probe.format not in ALLOWED_FORMATS:
            raise ValidationFailed("logo_format")
        if probe.width * probe.height > MAX_PIXELS:
            raise ValidationFailed("logo_too_big")
        if getattr(probe, "n_frames", 1) > 1:
            raise ValidationFailed("logo_animated")
        probe.verify()
        img = Image.open(io.BytesIO(data))  # verify() делает объект непригодным — открываем заново
        if img.format == "JPEG":
            img.draft("RGB", (MAX_SIDE_STORED * 2, MAX_SIDE_STORED * 2))  # быстрый декод уменьшенной копии
        img.load()
    except ValidationFailed:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError, ValueError):
        raise ValidationFailed("logo_invalid")

    img = ImageOps.exif_transpose(img)  # применяем поворот из EXIF до того, как перекодирование его выбросит
    img = img.convert("RGBA")
    img.thumbnail((MAX_SIDE_STORED, MAX_SIDE_STORED))
    full = io.BytesIO()
    img.save(full, "PNG", optimize=True)

    thumb = img.copy()
    thumb.thumbnail((THUMB_SIDE, THUMB_SIDE))
    tb = io.BytesIO()
    thumb.save(tb, "PNG")
    return full.getvalue(), "data:image/png;base64," + base64.b64encode(tb.getvalue()).decode()


async def save_logo(db: Database, settings: Settings, user_id: int, filename: str, data: bytes) -> dict:
    if len(data) > settings.logo_max_bytes:
        raise ValidationFailed("logo_too_big")
    count = await db.fetchone("SELECT COUNT(*) AS n FROM logos WHERE user_id=?", (user_id,))
    if count["n"] >= settings.logos_max:
        raise ValidationFailed("logo_quota")

    async with _decode_slots:
        png, thumb = await asyncio.to_thread(_process, data)

    logo_id = uuid.uuid4().hex
    settings.logos_dir.mkdir(parents=True, exist_ok=True)
    # в БД храним только имя файла: папка данных может переехать. Имя генерируем сами, путь от клиента не используем.
    file_name = f"{logo_id}.png"
    await asyncio.to_thread((Path(settings.logos_dir) / file_name).write_bytes, png)

    name = clean_text(Path(filename or "logo").name, 48) or "logo"
    async with db.tx() as t:
        await t.execute(
            "INSERT INTO logos (id, user_id, name, path, created_at) VALUES (?,?,?,?,?)",
            (logo_id, user_id, name, file_name, now()),
        )
    return {"id": logo_id, "name": name, "thumb": thumb}


async def delete_logo(db: Database, settings: Settings, user_id: int, logo_id: str) -> bool:
    async with db.tx() as t:
        row = await t.fetchone("SELECT path FROM logos WHERE id=? AND user_id=?", (logo_id, user_id))
        if row is None:
            return False
        await t.execute("DELETE FROM logos WHERE id=?", (logo_id,))
    try:
        await asyncio.to_thread((Path(settings.logos_dir) / row["path"]).unlink, True)
    except OSError:
        pass
    return True
