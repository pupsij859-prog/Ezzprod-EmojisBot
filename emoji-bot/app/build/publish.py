"""Публикация пака в Telegram: создание набора и добавление элементов. Шов: Publisher.

Факты Bot API, на которых это держится:
- createNewStickerSet принимает 1–50 начальных элементов, остальные — по одному через addStickerToSet;
- имя набора: латиница/цифры/подчёркивание, начинается с буквы, без «__», ≤ 64 символов, оканчивается на _by_<бот>;
- эмодзи-набор ≤ 200 элементов, стикерный ≤ 120; кастомные эмодзи — sticker_type='custom_emoji';
- ссылка на набор: t.me/addemoji/<name> или t.me/addstickers/<name>.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

FIRST_BATCH = 50


@dataclass(frozen=True)
class StickerFile:
    data: bytes      # TGS (Lottie + gzip)
    emoji: str       # эмодзи-ассоциация элемента (обязательна для Telegram)


class SetLevelError(Exception):
    """Сбой всего набора (нет прав, пользователь не запускал бота, лимит наборов): продолжать бессмысленно."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


class ItemError(Exception):
    """Сбой конкретного элемента (битый файл, неподходящее эмодзи): элемент пропускаем."""


class Publisher(Protocol):
    async def count(self, name: str) -> int | None:
        """Сколько элементов уже в наборе; None — набора нет."""
        ...

    async def create(self, owner_id: int, name: str, title: str, kind: str, files: list[StickerFile]) -> None: ...

    async def add(self, owner_id: int, name: str, kind: str, file: StickerFile) -> None: ...

    def link(self, name: str, kind: str) -> str: ...


# коды ошибок Telegram, после которых весь набор собрать нельзя
_SET_LEVEL = {
    "PEER_ID_INVALID": "start_bot",          # бот не может создать набор для этого пользователя (не нажимал /start)
    "USER_IS_BOT": "start_bot",
    "USER_DEACTIVATED": "user_gone",
    "STICKERSET_INVALID": "set_invalid",
    "STICKERS_TOO_MUCH": "set_full",
    "STICKERSETS_TOO_MUCH": "too_many_sets",
    "STICKERSET_NOT_MODIFIED": "set_invalid",
    "BOT_TOKEN": "bot",
    "NAME_OCCUPIED": "set_exists",
}


def classify(message: str) -> SetLevelError | ItemError:
    upper = (message or "").upper()
    for key, code in _SET_LEVEL.items():
        if key in upper:
            return SetLevelError(code, message)
    return ItemError(message)


class TelegramPublisher:
    def __init__(self, bot) -> None:
        self._bot = bot

    def _input(self, file: StickerFile):
        from aiogram.types import BufferedInputFile, InputSticker

        return InputSticker(sticker=BufferedInputFile(file.data, filename="item.tgs"),
                            format="animated", emoji_list=[file.emoji])

    async def count(self, name: str) -> int | None:
        from aiogram.exceptions import TelegramBadRequest

        try:
            s = await self._bot.get_sticker_set(name=name)
        except TelegramBadRequest as exc:
            if "STICKERSET_INVALID" in str(exc).upper():
                return None
            # Ошибка запроса не означает, что набора нет: повторное создание опасно.
            raise SetLevelError("lookup_failed", str(exc)) from exc
        return len(s.stickers)

    async def create(self, owner_id: int, name: str, title: str, kind: str, files: list[StickerFile]) -> None:
        await self._call(self._bot.create_new_sticker_set(
            user_id=owner_id, name=name, title=title[:64], stickers=[self._input(f) for f in files],
            sticker_type="custom_emoji" if kind == "emoji" else "regular",
        ))

    async def add(self, owner_id: int, name: str, kind: str, file: StickerFile) -> None:
        await self._call(self._bot.add_sticker_to_set(user_id=owner_id, name=name, sticker=self._input(file)))

    @staticmethod
    async def _call(coro) -> None:
        from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

        try:
            await coro
        except TelegramForbiddenError as exc:
            # Отсутствие доступа не исправится заменой файла: не перебираем весь пак.
            raise SetLevelError("forbidden", str(exc)) from exc
        except TelegramBadRequest as exc:
            raise classify(str(exc)) from exc

    def link(self, name: str, kind: str) -> str:
        return f"https://t.me/{'addemoji' if kind == 'emoji' else 'addstickers'}/{name}"
