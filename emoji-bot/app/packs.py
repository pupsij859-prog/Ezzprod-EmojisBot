"""Сборка паков. Шов для следующего этапа: PackBuilder.

Сейчас — MockPackBuilder (имитация: ждём 3–5 секунд и отдаём ссылку-заглушку).
Позже здесь появится реальный билдер: рендер шаблона (текст/логотип/цвета) →
файлы нужного формата → createNewStickerSet / addStickerToSet.

Контракт для реального билдера:
- build() идемпотентен: перед созданием набора проверяет, не существует ли он уже, и продолжает
  с первого невыполненного элемента (recover() после перезапуска вызовет его повторно);
- исключение из build() = сборка не удалась → пользователю возвращаются генерации;
- прогресс считается по сделанной работе (done/total), а не по времени.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Protocol

from . import services
from .config import Settings
from .db import Database
from .notify import Notifier

log = logging.getLogger("ezzprod.packs")

FINISH_RETRIES = 5


@dataclass
class BuildResult:
    link: str


class PackBuilder(Protocol):
    async def build(self, pack_id: int, duration_s: float) -> BuildResult:
        """Собирает пак. Бросает исключение при сбое сборки."""
        ...

    def estimate_seconds(self, count: int) -> float:
        """Сколько примерно займёт сборка (для индикатора прогресса)."""
        ...


class MockPackBuilder:
    def __init__(self, settings: Settings, bot_username: str = "") -> None:
        self._settings = settings
        self._bot_username = bot_username

    def set_bot_username(self, username: str) -> None:
        self._bot_username = username

    def estimate_seconds(self, count: int) -> float:
        s = self._settings
        return round(random.uniform(s.demo_build_min_s, s.demo_build_max_s), 1)

    async def build(self, pack_id: int, duration_s: float) -> BuildResult:
        await asyncio.sleep(max(0.0, duration_s))
        # настоящей ссылки на набор ещё нет — ведём в бота
        target = self._bot_username or "telegram"
        return BuildResult(link=f"https://t.me/{target}")


class PackRunner:
    """Запускает сборку в фоне, переводит пак в ready/failed, шлёт уведомления."""

    def __init__(self, db: Database, settings: Settings, builder: PackBuilder, notifier: Notifier) -> None:
        self.db = db
        self.settings = settings
        self.builder = builder
        self.notifier = notifier
        self._tasks: set[asyncio.Task] = set()

    def start(self, pack_id: int, duration_s: float) -> None:
        task = asyncio.create_task(self._run(pack_id, duration_s), name=f"pack-{pack_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def recover(self) -> None:
        """После перезапуска дособираем зависшие паки (в имитации — сразу)."""
        for pack_id in await services.processing_pack_ids(self.db):
            self.start(pack_id, 0.0)

    async def _run(self, pack_id: int, duration_s: float) -> None:
        # 1) сборка: её сбой — единственная причина вернуть генерации
        try:
            result = await self.builder.build(pack_id, duration_s)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("сборка пака %s не удалась, возвращаем генерации", pack_id)
            user_id = await services.fail_pack(self.db, pack_id)
            if user_id is not None:
                await self._safe(self.notifier.pack_failed, user_id, pack_id)
            return

        # 2) запись результата: если база занята, повторяем; пак остаётся processing и подхватится recover()
        finished = None
        for attempt in range(1, FINISH_RETRIES + 1):
            try:
                finished = await services.finish_pack(self.db, self.settings, pack_id, result.link)
                break
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("не удалось записать результат пака %s (попытка %s)", pack_id, attempt)
                await asyncio.sleep(min(2 ** attempt, 15))
        if finished is None:
            return
        await self._safe(self.notifier.pack_ready, finished.user_id, finished.pack)
        if finished.reward_user_id:
            await self._safe(self.notifier.referral_reward, finished.reward_user_id, finished.reward_amount)

    @staticmethod
    async def _safe(fn, *args) -> None:
        try:
            await fn(*args)
        except Exception:
            log.exception("не удалось отправить уведомление")

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
