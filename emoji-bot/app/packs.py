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
from .notify import Notifier, reward_sender

log = logging.getLogger("ezzprod.packs")

FINISH_RETRIES = 5
RUN_TIMEOUT_S = 1200.0  # включая очередь и ожидание Telegram flood wait
NOTIFY_TIMEOUT_S = 15.0


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
        self._running: set[int] = set()

    def start(self, pack_id: int, duration_s: float) -> None:
        if pack_id in self._running:
            return
        self._running.add(pack_id)
        task = asyncio.create_task(self._run(pack_id, duration_s), name=f"pack-{pack_id}")
        self._tasks.add(task)

        def done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            self._running.discard(pack_id)
            if not t.cancelled() and t.exception() is not None:
                log.error("фоновая задача пака %s завершилась с ошибкой; сторож повторит её", pack_id,
                          exc_info=t.exception())

        task.add_done_callback(done)

    async def recover(self) -> None:
        """Дособираем паки, которые висят в processing без запущенной сборки (после перезапуска или
        если запись результата не удалась несколько раз). В имитации — сразу."""
        for pack_id in await services.processing_pack_ids(self.db):
            self.start(pack_id, 0.0)

    async def watch(self, interval_s: float = 60.0) -> None:
        """Фоновый сторож: периодически подхватывает зависшие паки (иначе они ждали бы перезапуска процесса)."""
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.recover()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("проверка зависших паков не удалась")

    async def _run(self, pack_id: int, duration_s: float) -> None:
        # 1) сборка: её сбой — единственная причина вернуть генерации
        try:
            async with asyncio.timeout(RUN_TIMEOUT_S):
                result = await self.builder.build(pack_id, duration_s)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("сборка пака %s не удалась, возвращаем генерации", pack_id)
            code = "build_timeout" if isinstance(exc, TimeoutError) else getattr(exc, "code", "build_failed")
            user_id = await services.fail_pack_with_error(self.db, pack_id, code)
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
            await self._safe(reward_sender(self.notifier, finished.reward_kind), finished.reward_user_id, finished.reward_amount)

    @staticmethod
    async def _safe(fn, *args) -> None:
        try:
            await asyncio.wait_for(fn(*args), NOTIFY_TIMEOUT_S)
        except Exception:
            log.exception("не удалось отправить уведомление")

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
