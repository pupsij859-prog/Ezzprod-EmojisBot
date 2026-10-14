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
from .. import catalog, services, user_fonts
from ..config import Settings
from ..db import Database
from ..packs import BuildResult
from .publish import FIRST_BATCH, ItemError, Publisher, SetLevelError, StickerFile
from .render import ItemSpec, Renderer, logo_vectors

log = logging.getLogger("ezzprod.build")

NETWORK_RETRIES = 4


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
                 bot_username: str, concurrency: int = 2, pause_s: float = 0.05) -> None:
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
            return await self._build(pack_id)

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
                            font_path=str(user_fonts.font_path(self.settings, p.get("font_id")) or ""))

        async def render(item) -> StickerFile | None:
            try:
                data = await asyncio.to_thread(self.renderer.render, spec(item))
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

            for item in pending:
                file = await render(item)
                if file is None:
                    continue
                try:
                    await self._retry(lambda f=file: self.publisher.add(owner, name, kind, f))
                except ItemError as exc:
                    await services.mark_items(self.db, pack_id, [item["idx"]], "failed", str(exc))
                    continue
                await services.mark_items(self.db, pack_id, [item["idx"]], "added")
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
            await self._retry(lambda: self.publisher.create(owner, name, pack["title"], kind, [f for _, f in batch]))
            await services.mark_items(self.db, pack_id, [i["idx"] for i, _ in batch], "added")
            return rest
        except ItemError:
            pass
        # Telegram отверг какую-то картинку из пачки: создаём набор с одной, остальные пойдут по одной
        for n, (item, file) in enumerate(batch):
            try:
                await self._retry(lambda f=file: self.publisher.create(owner, name, pack["title"], kind, [f]))
            except ItemError as exc:
                await services.mark_items(self.db, pack_id, [item["idx"]], "failed", str(exc))
                continue
            await services.mark_items(self.db, pack_id, [item["idx"]], "added")
            return [i for i, _ in batch[n + 1:]] + rest
        raise BuildFailed("create")

    async def _retry(self, call):
        """Повторяет вызов Telegram при retry_after и сетевых сбоях. Ошибки элемента/набора пробрасывает."""
        from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError

        for attempt in range(1, NETWORK_RETRIES + 1):
            try:
                return await call()
            except TelegramRetryAfter as exc:
                log.warning("Telegram просит подождать %s с", exc.retry_after)
                await asyncio.sleep(exc.retry_after + 0.5)
            except (TelegramNetworkError, TelegramServerError):
                if attempt == NETWORK_RETRIES:
                    raise
                await asyncio.sleep(min(2 ** attempt, 20))
        return await call()

    async def _added(self, pack_id: int) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM pack_items WHERE pack_id=? AND status='added'", (pack_id,))
        return row["n"]

    async def _fail_rest(self, pack_id: int, code: str) -> None:
        rows = await self.db.fetchall(
            "SELECT idx FROM pack_items WHERE pack_id=? AND status='pending'", (pack_id,))
        await services.mark_items(self.db, pack_id, [r["idx"] for r in rows], "failed", code)
