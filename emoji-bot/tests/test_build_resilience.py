"""Зависания и потеря ответа Telegram после успешной загрузки — без реальной сети."""
import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import GetMe, GetStickerSet

from app import packs, services
from app.build import builder as build_module
from app.build.publish import SetLevelError, TelegramPublisher
from app.notify import NullNotifier
from tests.test_build import FakePublisher, FastRenderer, builder, make_pack


@pytest.fixture
def limits(monkeypatch):
    monkeypatch.setattr(build_module, "REQUEST_TIMEOUT_S", 0.1)
    monkeypatch.setattr(build_module, "RECONCILE_TIMEOUT_S", 0.1)
    monkeypatch.setattr(build_module, "NETWORK_RETRIES", 2)

    async def no_delay(_):
        pass

    monkeypatch.setattr(build_module.asyncio, "sleep", no_delay)


def offline():
    return TelegramNetworkError(method=GetMe(), message="connection lost")


async def test_lost_create_response_is_reconciled_without_recreating(db, settings, limits):
    pack = await make_pack(db, settings, n=3)

    class LostResponse(FakePublisher):
        async def create(self, *args):
            await super().create(*args)
            raise offline()  # Telegram создал набор, клиент не получил подтверждение.

    pub = LostResponse()
    runner = packs.PackRunner(db, settings, builder(db, settings, pub), NullNotifier())
    await runner._run(pack["id"], 0)
    row = await services.get_pack(db, 1, pack["id"])
    assert row["status"] == "ready" and row["done"] == 3
    assert pub.calls == [("create", 3)]
    assert (await services.get_user(db, 1))["balance"] == 1
    assert await services.ledger_mismatches(db) == []


async def test_lost_add_response_does_not_duplicate_sticker(db, settings, monkeypatch, limits):
    monkeypatch.setattr(build_module, "FIRST_BATCH", 1)
    pack = await make_pack(db, settings, n=3)

    class LostResponse(FakePublisher):
        async def add(self, *args):
            await super().add(*args)
            raise offline()

    pub = LostResponse()
    await builder(db, settings, pub).build(pack["id"], 0)
    row = await services.get_pack(db, 1, pack["id"])
    assert row["done"] == 3
    assert len(pub.sets[row["set_name"]]) == 3
    assert pub.calls == [("create", 1), ("add", 1), ("add", 1)]


async def test_create_timeout_after_commit_still_returns_created_pack(db, settings, limits):
    pack = await make_pack(db, settings, n=2)

    class LostResponse(FakePublisher):
        async def create(self, *args):
            await super().create(*args)
            await asyncio.Event().wait()

    pub = LostResponse()
    runner = packs.PackRunner(db, settings, builder(db, settings, pub), NullNotifier())
    await asyncio.wait_for(runner._run(pack["id"], 0), 3)
    row = await services.get_pack(db, 1, pack["id"])
    assert row["status"] == "ready" and row["done"] == 2
    assert pub.calls == [("create", 2)]


async def test_hanging_network_releases_build_slot_and_refunds(db, settings, limits):
    first = await make_pack(db, settings, uid=1, n=1)
    second = await make_pack(db, settings, uid=2, n=1)

    class Hung(FakePublisher):
        async def count(self, name):
            await asyncio.Event().wait()

    b = build_module.TelegramPackBuilder(db, settings, FastRenderer(), Hung(), "ezz_test_bot", concurrency=1)
    runner = packs.PackRunner(db, settings, b, NullNotifier())
    runner.start(first["id"], 0)
    runner.start(second["id"], 0)
    await asyncio.wait_for(asyncio.gather(*runner._tasks), 3)
    assert not runner._running
    for uid, pack in ((1, first), (2, second)):
        row = await services.get_pack(db, uid, pack["id"])
        assert row["status"] == "failed" and row["error"] == "build_timeout"
        assert (await services.get_user(db, uid))["balance"] == 1


async def test_network_outage_midway_returns_partial_pack_and_refunds_rest(db, settings, monkeypatch, limits):
    monkeypatch.setattr(build_module, "FIRST_BATCH", 1)
    pack = await make_pack(db, settings, n=3)

    class Outage(FakePublisher):
        broken = False

        async def count(self, name):
            if self.broken:
                raise offline()
            return await super().count(name)

        async def add(self, *args):
            self.broken = True
            raise offline()

    runner = packs.PackRunner(db, settings, builder(db, settings, Outage()), NullNotifier())
    await runner._run(pack["id"], 0)
    row = await services.get_pack(db, 1, pack["id"])
    assert (row["status"], row["done"], row["failed_items"]) == ("ready", 1, 2)
    assert (await services.get_user(db, 1))["balance"] == 3
    assert await services.ledger_mismatches(db) == []


async def test_flood_wait_cannot_block_worker_for_hours(db, settings, limits):
    pack = await make_pack(db, settings, n=2)

    class Flooded(FakePublisher):
        async def create(self, *args):
            raise TelegramRetryAfter(method=GetMe(), message="Flood", retry_after=3600)

    runner = packs.PackRunner(db, settings, builder(db, settings, Flooded()), NullNotifier())
    await asyncio.wait_for(runner._run(pack["id"], 0), 3)
    row = await services.get_pack(db, 1, pack["id"])
    assert row["status"] == "failed" and row["error"] == "telegram_busy"
    assert (await services.get_user(db, 1))["balance"] == 3


async def test_flood_wait_within_limit_retries_and_succeeds(db, settings, limits):
    pack = await make_pack(db, settings, n=2)
    slept = []

    class FloodOnce(FakePublisher):
        def __init__(self):
            super().__init__()
            self.flooded = True

        async def create(self, *args):
            if self.flooded:
                self.flooded = False
                raise TelegramRetryAfter(method=GetMe(), message="Flood", retry_after=263)
            return await super().create(*args)

    async def fake_sleep(seconds):
        slept.append(seconds)

    from unittest.mock import patch
    with patch.object(build_module.asyncio, "sleep", side_effect=fake_sleep):
        runner = packs.PackRunner(db, settings, builder(db, settings, FloodOnce()), NullNotifier())
        await asyncio.wait_for(runner._run(pack["id"], 0), 3)

    row = await services.get_pack(db, 1, pack["id"])
    assert row["status"] == "ready" and row["done"] == 2
    assert any(abs(s - 264.0) < 0.01 for s in slept)


async def test_slow_render_cannot_stall_pack(db, settings, monkeypatch, limits):
    monkeypatch.setattr(build_module, "RENDER_TIMEOUT_S", 0.01)
    pack = await make_pack(db, settings, n=1)

    class SlowRenderer:
        def render(self, spec):
            time.sleep(0.15)
            return b"TGS"

    runner = packs.PackRunner(db, settings, builder(db, settings, FakePublisher(), SlowRenderer()), NullNotifier())
    await asyncio.wait_for(runner._run(pack["id"], 0), 3)
    assert (await services.get_pack(db, 1, pack["id"]))["status"] == "failed"
    assert (await services.get_user(db, 1))["balance"] == 1


async def test_total_build_deadline_keeps_successful_part(db, settings, monkeypatch, limits):
    monkeypatch.setattr(build_module, "FIRST_BATCH", 1)
    monkeypatch.setattr(build_module, "BUILD_TIMEOUT_S", 0.2)
    monkeypatch.setattr(build_module, "REQUEST_TIMEOUT_S", 10)
    pack = await make_pack(db, settings, n=3)

    class HungAdd(FakePublisher):
        async def add(self, *args):
            await asyncio.Event().wait()

    runner = packs.PackRunner(db, settings, builder(db, settings, HungAdd()), NullNotifier())
    await asyncio.wait_for(runner._run(pack["id"], 0), 3)
    row = await services.get_pack(db, 1, pack["id"])
    assert (row["status"], row["done"], row["failed_items"]) == ("ready", 1, 2)
    assert (await services.get_user(db, 1))["balance"] == 3


async def test_runner_deadline_handles_hung_builder(db, settings, monkeypatch):
    monkeypatch.setattr(packs, "RUN_TIMEOUT_S", 0.05)
    pack = await make_pack(db, settings, n=1)

    class Hung:
        async def build(self, *args):
            await asyncio.Event().wait()

    runner = packs.PackRunner(db, settings, Hung(), NullNotifier())
    runner.start(pack["id"], 0)
    await asyncio.wait_for(asyncio.gather(*runner._tasks), 3)
    assert not runner._running
    row = await services.get_pack(db, 1, pack["id"])
    assert row["status"] == "failed" and row["error"] == "build_timeout"


async def test_shutdown_leaves_pack_recoverable(db, settings):
    pack = await make_pack(db, settings, n=1)

    class Hung:
        async def build(self, *args):
            await asyncio.Event().wait()

    runner = packs.PackRunner(db, settings, Hung(), NullNotifier())
    runner.start(pack["id"], 0)
    await runner.shutdown()
    assert (await services.get_pack(db, 1, pack["id"]))["status"] == "processing"
    assert (await services.get_user(db, 1))["balance"] == 0


async def test_failure_after_partial_refund_does_not_refund_twice(db, settings):
    pack = await make_pack(db, settings, n=3)
    await services.mark_items(db, pack["id"], [0], "failed")
    assert await services.refund_failed_items(db, pack["id"]) == 1
    await services.fail_pack_with_error(db, pack["id"], "build_failed")
    assert (await services.get_user(db, 1))["balance"] == 4
    assert await services.fail_pack(db, pack["id"]) is None
    assert await services.ledger_mismatches(db) == []


async def test_lookup_error_is_not_mistaken_for_missing_set():
    method = GetStickerSet(name="test_by_bot")
    bot = SimpleNamespace(get_sticker_set=AsyncMock(side_effect=TelegramBadRequest(method=method, message="bad query")))
    with pytest.raises(SetLevelError, match="lookup_failed"):
        await TelegramPublisher(bot).count("test_by_bot")
    bot.get_sticker_set.side_effect = TelegramBadRequest(method=method, message="STICKERSET_INVALID")
    assert await TelegramPublisher(bot).count("test_by_bot") is None


async def test_forbidden_is_a_set_error_instead_of_bad_sticker():
    async def forbidden():
        raise TelegramForbiddenError(method=GetMe(), message="bot was blocked")

    with pytest.raises(SetLevelError, match="forbidden"):
        await TelegramPublisher._call(forbidden())


async def test_retry_limit_does_not_make_an_extra_unprotected_request(db, settings, limits):
    b = builder(db, settings, FakePublisher())
    call = AsyncMock(side_effect=offline())
    with pytest.raises(TelegramNetworkError):
        await b._retry(call)
    assert call.await_count == build_module.NETWORK_RETRIES


async def test_occupied_set_name_is_reconciled_before_giving_up(db, settings, limits):
    pack = await make_pack(db, settings, n=2)

    class Occupied(FakePublisher):
        async def create(self, *args):
            await super().create(*args)
            raise SetLevelError("set_exists")

    pub = Occupied()
    result = await builder(db, settings, pub).build(pack["id"], 0)
    assert result.link.startswith("https://t.me/addemoji/")
    assert pub.calls == [("create", 2)]
    assert (await services.get_pack(db, 1, pack["id"]))["done"] == 2


async def test_live_progress_does_not_jump_to_95_while_queued(db, settings):
    live = replace(settings, demo_mode=False)
    pack = await make_pack(db, live, n=3)
    await db.conn.execute("UPDATE packs SET duration_s=1, created_at=created_at-120 WHERE id=?", (pack["id"],))
    row = await services.get_pack(db, 1, pack["id"])
    assert services.pack_view(row)["progress"] == 0
    await services.mark_items(db, pack["id"], [0], "added")
    assert services.pack_view(await services.get_pack(db, 1, pack["id"]))["progress"] == 0.333


async def test_demo_progress_still_advances_by_time(db, settings):
    pack = await make_pack(db, settings, n=1)
    await db.conn.execute("UPDATE packs SET duration_s=1, created_at=created_at-120 WHERE id=?", (pack["id"],))
    assert services.pack_view(await services.get_pack(db, 1, pack["id"]))["progress"] == 0.95


async def test_schema_upgrade_preserves_existing_packs(tmp_path, monkeypatch):
    from app import db as db_module

    migrations = db_module.MIGRATIONS
    path = tmp_path / "upgrade.sqlite3"
    old = db_module.Database(path)
    monkeypatch.setattr(db_module, "MIGRATIONS", {k: v for k, v in migrations.items() if k <= 7})
    await old.connect()
    # Старый пак без новой колонки: достаточно строки в готовой схеме v7.
    await old.conn.execute("INSERT INTO users(id,first_name,ref_code,created_at) VALUES(1,'test','abcdef01',0)")
    await old.conn.execute(
        "INSERT INTO packs(user_id,type,title,status,count,items,params,created_at,set_name) "
        "VALUES(1,'emoji','test','processing',1,'[\"classic-001\"]','{}',0,'test_by_bot')"
    )
    await old.close()
    monkeypatch.setattr(db_module, "MIGRATIONS", migrations)
    upgraded = db_module.Database(path)
    await upgraded.connect()
    try:
        row = await upgraded.fetchone("SELECT * FROM packs WHERE id=1")
        assert row["status"] == "processing" and row["live_build"] == 1
        assert services.pack_view(row)["progress"] == 0
    finally:
        await upgraded.close()
