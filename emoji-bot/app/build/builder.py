"""Настоящая сборка пака: рендер элементов и публикация набора в Telegram.

Порядок:
1. имя набора фиксируется в БД один раз (ez<id>_<код>_by_<бот>) — повторная сборка продолжает тот же набор;
2. сверка с Telegram: если набор уже есть и в нём больше элементов, чем отмечено в БД (процесс упал между
   «добавили» и «записали»), лишние считаются добавленными — дублей не будет;
3. набора нет → createNewStickerSet с первой пачкой до 50 элементов (TGS); если Telegram отверг какой-то файл,
   создаём с одной и добавляем остальные по одной;
4. остальные элементы — addStickerToSet по одному, прогресс пишется после каждого;
5. retry_after и сетевые сбои — ждём и повторяем; сломанный элемент — пропускаем и возвращаем за него генерацию;
6. сбой всего набора: если что-то уже добавлено — отдаём то, что есть, и возвращаем за остальное;
   если ничего — исключение, PackRunner вернёт все генерации.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from .. import catalog, services, user_fonts
from ..config import Settings
from ..db import Database
from ..packs import BuildResult
from .publish import FIRST_BATCH, ItemError, Publisher, SetLevelError, StickerFile
from .render import ItemSpec, Renderer, logo_vectors

log = logging.getLogger("ezzprod.build")

NETWORK_RETRIES = 4
REQUEST_TIMEOUT_S = 30.0
RENDER_TIMEOUT_S = 60.0
BUILD_TIMEOUT_S = 720.0
RECONCILE_TIMEOUT_S = 10.0
MAX_FLOOD_WAIT_S = 420.0


class BuildFailed(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def make_set_name(pack_id: int, bot_username: str) -> str:
    bot = bot_username.lower()
    name = f"ez{pack_id}_{secrets.token_hex(3)}_by_{bot}"
    if len(name) > 64 or "__" in name:
        raise BuildFailed("set_name")
    return name


def item_emoji(template_id: str) -> str:
    """Эмодзи-ассоциация элемента по категории шаблона (Telegram требует хотя бы одно на элемент)."""
    tpl = catalog.get(template_id)
    return tpl.emoji if tpl else "✨"


class TelegramPackBuilder:
    def __init__(self, db: Database, settings: Settings, renderer: Renderer, publisher: Publisher,
                 bot_username: str, concurrency: int = 1, pause_s: float = 0.05) -> None:
        self.db = db
        self.settings = settings
        self.renderer = renderer
        self.publisher = publisher
        self._bot_username = bot_username
        self._slots = asyncio.Semaphore(concurrency)  # одновременно собираем не больше N паков (лимиты Telegram)
        self._pause = pause_s

    def set_bot_username(self, username: str) -> None:
        self._bot_username = username

    def estimate_seconds(self, count: int) -> float:
        return round(3 + count * 0.7, 1)

    async def build(self, pack_id: int, duration_s: float) -> BuildResult:
        async with self._slots:
            try:
                # Очередь не входит в бюджет: ограничиваем именно занятие слота сборки.
                async with asyncio.timeout(BUILD_TIMEOUT_S):
                    return await self._build(pack_id)
            except (TimeoutError, TelegramNetworkError, TelegramServerError, OSError) as exc:
                code = "build_timeout" if isinstance(exc, TimeoutError) else "telegram_unavailable"
                log.warning("сборка пака %s прервана: %s", pack_id, code)
                return await self._finish_interrupted(pack_id, code)

    # ───────────────────────── сборка ─────────────────────────

    async def _build(self, pack_id: int) -> BuildResult:
        state = await services.build_state(self.db, pack_id)
        if state is None:
            raise BuildFailed("not_processing")
        pack, items = state
        if not self._bot_username:
            raise BuildFailed("bot")
        kind, owner = pack["type"], pack["user_id"]
        params = json.loads(pack["params"])
        logos: dict[str | None, tuple] = {}
        name = await services.set_pack_set_name(self.db, pack_id, make_set_name(pack_id, self._bot_username))

        def spec(item) -> ItemSpec:
            # свои настройки элемента (если есть) поверх общих настроек пака
            own = json.loads(item["params"]) if "params" in item.keys() and item["params"] else {}
            p = {**params, **own}
            logo_id = p.get("logo_id")
            if logo_id not in logos:
                logos[logo_id] = logo_vectors(self.settings.logos_dir, logo_id)
            return ItemSpec(template_id=item["template_id"], kind=kind, text=p.get("text") or "",
                            color=p.get("color"), stroke=p.get("stroke"), logo_vectors=logos[logo_id],
                            size=p.get("size") or 100, edit_json=json.dumps(own.get("edit")) if own.get("edit") else "",
                            font_path=str(user_fonts.font_path(self.settings, p.get("font_id")) or ""),
                            place_json=json.dumps(p["place"]) if p.get("place") else "",
                            grad_json=json.dumps(p["grad"]) if p.get("grad") else "")

        async def render(item) -> StickerFile | None:
            try:
                data = await asyncio.wait_for(asyncio.to_thread(self.renderer.render, spec(item)), RENDER_TIMEOUT_S)
            except Exception as exc:
                log.exception("рендер элемента %s пака %s не удался", item["idx"], pack_id)
                await services.mark_items(self.db, pack_id, [item["idx"]], "failed", f"render: {exc}")
                return None
            return StickerFile(data=data, emoji=item_emoji(item["template_id"]))

        try:
            exists = await self._retry(lambda: self.publisher.count(name))
            pending = [i for i in items if i["status"] == "pending"]
            if exists is not None:
                added = sum(1 for i in items if i["status"] == "added")
                extra = exists - added
                if extra > 0:  # уже в наборе, но не отмечено в БД
                    await services.mark_items(self.db, pack_id, [i["idx"] for i in pending[:extra]], "added")
                    pending = pending[extra:]
            else:
                pending = await self._create(pack, name, kind, owner, pending, render)

            published = await self._added(pack_id)
            for item in pending:
                file = await render(item)
                if file is None:
                    continue
                try:
                    await self._retry(lambda f=file: self.publisher.add(owner, name, kind, f),
                                      confirmed=lambda: self._has_count(name, published + 1))
                except ItemError as exc:
                    await services.mark_items(self.db, pack_id, [item["idx"]], "failed", str(exc))
                    continue
                await services.mark_items(self.db, pack_id, [item["idx"]], "added")
                published += 1
                await asyncio.sleep(self._pause)
        except SetLevelError as exc:
            log.warning("сборка пака %s остановлена: %s", pack_id, exc)
            if await self._added(pack_id) == 0:
                raise BuildFailed(exc.code) from exc
            # набор уже есть: отдаём то, что собрали, остальное — в «не удалось»
            await self._fail_rest(pack_id, exc.code)

        if await self._added(pack_id) == 0:
            raise BuildFailed("nothing_added")
        refunded = await services.refund_failed_items(self.db, pack_id)
        if refunded:
            log.warning("пак %s: %s элементов не собрались, генерации за них возвращены", pack_id, refunded)
        return BuildResult(link=self.publisher.link(name, kind))

    async def _create(self, pack, name, kind, owner, pending, render) -> list:
        """Создаёт набор первой пачкой. → элементы, которые ещё осталось добавить."""
        pack_id = pack["id"]
        batch: list[tuple[object, StickerFile]] = []
        rest = list(pending)
        while rest and len(batch) < FIRST_BATCH:
            item = rest.pop(0)
            file = await render(item)
            if file is not None:
                batch.append((item, file))
        if not batch:
            raise BuildFailed("render")
        try:
            await self._retry(lambda: self.publisher.create(owner, name, pack["title"], kind, [f for _, f in batch]),
                              confirmed=lambda: self._has_count(name, len(batch)))
            await services.mark_items(self.db, pack_id, [i["idx"] for i, _ in batch], "added")
            return rest
        except ItemError:
            pass
        # Telegram отверг какую-то картинку из пачки: создаём набор с одной, остальные пойдут по одной
        for n, (item, file) in enumerate(batch):
            try:
                await self._retry(lambda f=file: self.publisher.create(owner, name, pack["title"], kind, [f]),
                                  confirmed=lambda: self._has_count(name, 1))
            except ItemError as exc:
                await services.mark_items(self.db, pack_id, [item["idx"]], "failed", str(exc))
                continue
            await services.mark_items(self.db, pack_id, [item["idx"]], "added")
            return [i for i, _ in batch[n + 1:]] + rest
        raise BuildFailed("create")

    async def _retry(self, call, *, confirmed=None):
        """После потери ответа сначала сверяем результат мутации, только потом повторяем её."""
        for attempt in range(1, NETWORK_RETRIES + 1):
            try:
                return await asyncio.wait_for(call(), REQUEST_TIMEOUT_S)
            except SetLevelError as exc:
                if exc.code == "set_exists" and confirmed is not None and await confirmed():
                    return None
                raise
            except TelegramRetryAfter as exc:
                if exc.retry_after > MAX_FLOOD_WAIT_S or attempt == NETWORK_RETRIES:
                    raise SetLevelError("telegram_busy") from exc
                log.warning("Telegram просит подождать %s с (попытка %s/%s)", exc.retry_after, attempt, NETWORK_RETRIES)
                await asyncio.sleep(exc.retry_after + 1.0)
            except (TelegramNetworkError, TelegramServerError, TimeoutError, OSError):
                # Ответ мог потеряться уже ПОСЛЕ создания/добавления. Без сверки нельзя
                # ни создавать набор повторно, ни считать загруженный элемент неудачным.
                if confirmed is not None and await confirmed():
                    return None
                if attempt == NETWORK_RETRIES:
                    raise
                await asyncio.sleep(min(2 ** attempt, 20))

    async def _has_count(self, name: str, expected: int) -> bool:
        count = await self._retry(lambda: self.publisher.count(name))
        return count is not None and count >= expected

    async def _finish_interrupted(self, pack_id: int, code: str) -> BuildResult:
        """Возвращаем уже созданную часть вместо потери всего пака при сбое сети/таймауте."""
        state = await services.build_state(self.db, pack_id)
        if state is None:
            raise BuildFailed(code)
        pack, items = state
        name = pack["set_name"]
        if name:
            try:
                count = await asyncio.wait_for(self.publisher.count(name), RECONCILE_TIMEOUT_S)
            except (TelegramNetworkError, TelegramServerError, TelegramRetryAfter, TimeoutError, OSError, SetLevelError):
                count = None  # Сохраняем хотя бы то, что уже подтверждено в нашей БД.
            if count is not None:
                added = sum(i["status"] == "added" for i in items)
                pending = [i["idx"] for i in items if i["status"] == "pending"]
                await services.mark_items(self.db, pack_id, pending[:max(0, count - added)], "added")
        if not name or await self._added(pack_id) == 0:
            raise BuildFailed(code)
        await self._fail_rest(pack_id, code)
        await services.refund_failed_items(self.db, pack_id)
        return BuildResult(link=self.publisher.link(name, pack["type"]))

    async def _added(self, pack_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM pack_items WHERE pack_id=? AND status='added'", (pack_id,))
        return row["n"]

    async def _fail_rest(self, pack_id: int, code: str) -> None:
        rows = await self.db.fetchall(
            "SELECT idx FROM pack_items WHERE pack_id=? AND status='pending'", (pack_id,))
        await services.mark_items(self.db, pack_id, [r["idx"] for r in rows], "failed", code)
