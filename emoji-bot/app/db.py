"""SQLite: одно соединение, все операции сериализованы блокировкой.

Любое изменение баланса делается ТОЛЬКО внутри `db.tx()` вместе с записью в ledger.
Версия схемы хранится в PRAGMA user_version; новые изменения добавляются в MIGRATIONS.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Iterable

import aiosqlite

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY,
    username            TEXT,
    first_name          TEXT NOT NULL DEFAULT '',
    last_name           TEXT NOT NULL DEFAULT '',
    balance             INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    plan                TEXT NOT NULL DEFAULT 'free',
    trial_exports_left  INTEGER NOT NULL DEFAULT 1,
    onboarded           INTEGER NOT NULL DEFAULT 0,
    ref_code            TEXT NOT NULL UNIQUE,
    referred_by         INTEGER REFERENCES users(id),
    referral_rewarded   INTEGER NOT NULL DEFAULT 0,
    purchased_total     INTEGER NOT NULL DEFAULT 0,
    created_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    delta       INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    ref         TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    UNIQUE (reason, ref)
);
CREATE INDEX IF NOT EXISTS idx_ledger_user_reason ON ledger (user_id, reason, created_at);

CREATE TABLE IF NOT EXISTS packs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    status      TEXT NOT NULL,
    count       INTEGER NOT NULL,
    items       TEXT NOT NULL,
    params      TEXT NOT NULL,
    link        TEXT,
    idem_key    TEXT,
    duration_s  REAL NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL,
    ready_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_packs_user ON packs (user_id, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_packs_idem ON packs (user_id, idem_key) WHERE idem_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS saved_styles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    name        TEXT NOT NULL,
    params      TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_styles_user ON saved_styles (user_id, id DESC);

-- заказ на покупку генераций (создаётся при выпуске счёта)
CREATE TABLE IF NOT EXISTS payments (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    qty         INTEGER NOT NULL,
    stars       INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    charge_id   TEXT UNIQUE,
    created_at  INTEGER NOT NULL,
    paid_at     INTEGER
);

-- каждое реальное поступление денег от провайдера; уникальность по charge_id — защита от повторной доставки
CREATE TABLE IF NOT EXISTS payment_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT NOT NULL,
    charge_id   TEXT NOT NULL,
    payload     TEXT NOT NULL,
    user_id     INTEGER NOT NULL,
    amount      INTEGER NOT NULL,
    currency    TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    UNIQUE (provider, charge_id)
);

CREATE TABLE IF NOT EXISTS logos (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    name        TEXT NOT NULL,
    path        TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logos_user ON logos (user_id);
"""

# версия → SQL, который переводит схему с предыдущей версии на эту
MIGRATIONS: dict[int, str] = {
    # v2: настоящая сборка паков — имя набора в Telegram, прогресс по элементам, частичные сбои
    2: """
ALTER TABLE packs ADD COLUMN set_name TEXT;
ALTER TABLE packs ADD COLUMN done INTEGER NOT NULL DEFAULT 0;
ALTER TABLE packs ADD COLUMN failed_items INTEGER NOT NULL DEFAULT 0;
ALTER TABLE packs ADD COLUMN error TEXT;
CREATE TABLE IF NOT EXISTS pack_items (
    pack_id     INTEGER NOT NULL REFERENCES packs(id),
    idx         INTEGER NOT NULL,
    template_id TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | added | failed
    error       TEXT,
    PRIMARY KEY (pack_id, idx)
);
""",
    # v3: админка в чате — настройки на лету, бан, промокоды, админы без перезапуска
    3: """
CREATE TABLE IF NOT EXISTS kv (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
ALTER TABLE users ADD COLUMN banned INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN blocked INTEGER NOT NULL DEFAULT 0;   -- заблокировал бота (для рассылок)
CREATE TABLE IF NOT EXISTS promos (
    code        TEXT PRIMARY KEY,
    gens        INTEGER NOT NULL,
    uses_max    INTEGER NOT NULL,
    uses        INTEGER NOT NULL DEFAULT 0,
    created_by  INTEGER,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS promo_uses (
    code        TEXT NOT NULL,
    user_id     INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (code, user_id)
);
CREATE TABLE IF NOT EXISTS admins (
    user_id     INTEGER PRIMARY KEY,
    added_by    INTEGER,
    created_at  INTEGER NOT NULL
);
""",
    # v4: свои ник/логотип/цвета у отдельного элемента пака (поверх общих настроек пака)
    4: """
ALTER TABLE pack_items ADD COLUMN params TEXT;
""",
    # v5: свои шрифты для ника (app/user_fonts.py)
    5: """
CREATE TABLE IF NOT EXISTS fonts (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    name        TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fonts_user ON fonts (user_id);
""",
    # v6: удаление паков из вкладки (soft delete)
    6: """
ALTER TABLE packs ADD COLUMN deleted_at INTEGER;
CREATE INDEX IF NOT EXISTS idx_packs_user_active ON packs (user_id, deleted_at, id DESC);
""",
    # v7: поддержка мульти-провайдеров оплаты (CryptoBot, xRocket)
    7: """
ALTER TABLE payments ADD COLUMN method TEXT NOT NULL DEFAULT 'stars';
ALTER TABLE payments ADD COLUMN amount_rub INTEGER NOT NULL DEFAULT 0;
ALTER TABLE payments ADD COLUMN external_id TEXT;
""",
    # v8: живой прогресс нельзя вычислять по таймеру демо-сборки.
    8: """
ALTER TABLE packs ADD COLUMN live_build INTEGER NOT NULL DEFAULT 0;
UPDATE packs SET live_build=1 WHERE set_name IS NOT NULL;
""",
    # v9: промокоды с настройками (случайная награда, срок, пауза, только новичкам, пачки кодов)
    9: """
ALTER TABLE promos ADD COLUMN gens_max INTEGER;
ALTER TABLE promos ADD COLUMN expires_at INTEGER;
ALTER TABLE promos ADD COLUMN only_new INTEGER NOT NULL DEFAULT 0;
ALTER TABLE promos ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE promos ADD COLUMN batch TEXT;
ALTER TABLE promo_uses ADD COLUMN gens INTEGER NOT NULL DEFAULT 0;
""",
    # v10: промокоды со скидкой в % (скидка на покупку генераций)
    10: """
ALTER TABLE promos ADD COLUMN discount_pct INTEGER NOT NULL DEFAULT 0;
ALTER TABLE promo_uses ADD COLUMN discount_pct INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN discount_pct INTEGER NOT NULL DEFAULT 0;
""",
    # v11: партнёрские ссылки для рекламы (app/partners.py): бонус новичку, награда партнёру, статистика
    11: """
CREATE TABLE IF NOT EXISTS partner_links (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    owner_id    INTEGER,
    bonus_new   INTEGER NOT NULL DEFAULT 0,
    reward      INTEGER NOT NULL DEFAULT 0,
    reward_on   TEXT NOT NULL DEFAULT 'pack',
    reward_cap  INTEGER NOT NULL DEFAULT 0,
    uses_max    INTEGER NOT NULL DEFAULT 0,
    expires_at  INTEGER,
    active      INTEGER NOT NULL DEFAULT 1,
    clicks      INTEGER NOT NULL DEFAULT 0,
    created_by  INTEGER,
    created_at  INTEGER NOT NULL
);
ALTER TABLE users ADD COLUMN partner TEXT;
ALTER TABLE users ADD COLUMN partner_rewarded INTEGER NOT NULL DEFAULT 2;
CREATE INDEX IF NOT EXISTS idx_users_partner ON users (partner, created_at);
""",
}


class Tx:
    """Обёртка над соединением внутри транзакции (без повторного захвата блокировки)."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Cursor:
        return await self._conn.execute(sql, tuple(params))

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        cur = await self._conn.execute(sql, tuple(params))
        try:
            return await cur.fetchone()
        finally:
            await cur.close()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        cur = await self._conn.execute(sql, tuple(params))
        try:
            return list(await cur.fetchall())
        finally:
            await cur.close()


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None → режим autocommit, транзакциями управляем вручную
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=FULL")  # база с деньгами: надёжность важнее скорости
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._migrate()

    async def _migrate(self) -> None:
        cur = await self.conn.execute("PRAGMA user_version")
        version = (await cur.fetchone())[0]
        await cur.close()
        if version == 0:
            await self.conn.executescript(SCHEMA)
            version = SCHEMA_VERSION
        for target in sorted(v for v in MIGRATIONS if v > version):
            statements = [s.strip() for s in MIGRATIONS[target].strip().split(";") if s.strip()]
            for stmt in statements:
                try:
                    await self.conn.execute(stmt)
                except aiosqlite.OperationalError as e:
                    if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                        continue
                    raise
            version = target
        await self.conn.execute(f"PRAGMA user_version = {int(version)}")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("База данных не подключена")
        return self._conn

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[Tx]:
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield Tx(self.conn)
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            else:
                await self.conn.execute("COMMIT")

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self._lock:
            return await Tx(self.conn).fetchone(sql, params)

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self._lock:
            return await Tx(self.conn).fetchall(sql, params)

    async def backup_to(self, path: Path | str) -> None:
        """Целостная копия базы в файл (VACUUM INTO): не мешает работе, WAL учитывается."""
        async with self._lock:
            await self.conn.execute("VACUUM INTO ?", (str(path),))
