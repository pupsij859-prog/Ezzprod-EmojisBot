"""Настройки, которые админ меняет на лету из чата (/admin), без перезапуска.

Значения из .env — базовые; поверх них лежат правки из таблицы kv («set:<поле>»). Применение — замена
ctx.settings новой копией (Settings неизменяемый), её же получают сборщик и PackRunner.
Здесь же флаги (техработы, скрытые категории), админы из базы, бан и промокоды.
"""
from __future__ import annotations

import dataclasses
import re
import secrets
from dataclasses import dataclass

from . import services
from .context import Context
from .db import Database


@dataclass(frozen=True)
class Tunable:
    key: str            # поле Settings
    label: str
    group: str
    kind: str = "int"   # int | bool | str
    lo: int = 0
    hi: int = 10_000
    hint: str = ""


GROUPS: dict[str, str] = {
    "price": "💰 Цены",
    "gift": "🎁 Подарки и рефералка",
    "limits": "📏 Лимиты",
    "other": "⚙️ Прочее",
}

TUNABLES: tuple[Tunable, ...] = (
    Tunable("price_rub", "Цена генерации, ₽", "price", lo=1, hi=100_000),
    Tunable("bulk_discount_from", "Скидка от, шт.", "price", lo=2, hi=1_000),
    Tunable("bulk_discount_pct", "Размер скидки, %", "price", lo=0, hi=90),
    Tunable("bulk_unit_from", "Опт от, шт.", "price", lo=2, hi=1_000),
    Tunable("bulk_unit_rub", "Оптовая цена, ₽/шт.", "price", lo=1, hi=100_000),
    Tunable("rub_per_star", "Курс: 1 ⭐ = ₽", "price", kind="float", lo=0, hi=1_000,
            hint="по нему сумма в рублях пересчитывается в Звёзды (вверх до целого)"),
    Tunable("max_buy_qty", "Максимум за покупку, шт.", "price", lo=1, hi=5_000),
    Tunable("signup_gens", "Подарок новичку, генераций", "gift", lo=0, hi=100),
    Tunable("referral_reward", "Награда за друга, генераций", "gift", lo=0, hi=100),
    Tunable("referral_daily_cap", "Наград за друзей в сутки", "gift", lo=0, hi=1_000),
    Tunable("referral_lifetime_cap", "Наград за друзей всего", "gift", lo=0, hi=100_000),
    Tunable("pro_min_purchased", "Pro после покупки, генераций", "gift", lo=0, hi=10_000),
    Tunable("emoji_pack_max", "Эмодзи в паке, макс.", "limits", lo=1, hi=200, hint="Telegram: не больше 200"),
    Tunable("sticker_pack_max", "Стикеров в паке, макс.", "limits", lo=1, hi=120, hint="Telegram: не больше 120"),
    Tunable("text_max", "Длина ника, символов", "limits", lo=1, hi=24),
    Tunable("styles_max", "Сохранённых стилей", "limits", lo=0, hi=200),
    Tunable("logos_max", "Логотипов на человека", "limits", lo=1, hi=500),
    Tunable("payments_live", "Приём оплаты Stars", "other", kind="bool",
            hint="в демо-режиме оплату включить нельзя"),
    Tunable("support_username", "Поддержка (@username)", "other", kind="str", hint="пусто — кнопка поддержки скрыта"),
)
BY_KEY: dict[str, Tunable] = {t.key: t for t in TUNABLES}
USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}\Z")
PROMO = re.compile(r"^[A-Z0-9_-]{3,32}\Z")


# ───────────────────────── kv ─────────────────────────

async def kv_get(db: Database, key: str, default: str | None = None) -> str | None:
    row = await db.fetchone("SELECT value FROM kv WHERE key=?", (key,))
    return row["value"] if row else default


async def kv_set(db: Database, key: str, value: str | None) -> None:
    async with db.tx() as t:
        if value is None:
            await t.execute("DELETE FROM kv WHERE key=?", (key,))
        else:
            await t.execute("INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, value))


# ───────────────────────── настройки ─────────────────────────

def parse(t: Tunable, raw: str):
    """Текст админа → значение поля. ValueError с понятным текстом, если не подходит."""
    raw = raw.strip()
    if t.kind == "bool":
        if raw.lower() in ("1", "да", "вкл", "on", "true"):
            return True
        if raw.lower() in ("0", "нет", "выкл", "off", "false"):
            return False
        raise ValueError("нужно «вкл» или «выкл»")
    if t.kind == "str":
        name = raw.lstrip("@")
        if name in ("", "-"):
            return ""
        if not USERNAME.match(name):
            raise ValueError("нужен @username: латиница, цифры и _, 4–32 символа")
        return name
    if t.kind == "float":
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            raise ValueError("нужно число, например 1.25") from None
        if not t.lo < value <= t.hi:
            raise ValueError(f"допустимо больше {t.lo} и до {t.hi}")
        return value
    if not re.fullmatch(r"-?\d{1,7}", raw):
        raise ValueError("нужно целое число")
    value = int(raw)
    if not t.lo <= value <= t.hi:
        raise ValueError(f"допустимо от {t.lo} до {t.hi}")
    return value


def _encode(t: Tunable, value) -> str:
    return ("1" if value else "0") if t.kind == "bool" else str(value)


def _decode(t: Tunable, raw: str):
    if t.kind == "bool":
        return raw == "1"
    if t.kind == "str":
        return raw
    if t.kind == "float":
        return float(raw)
    return int(raw)


def show_value(t: Tunable, value) -> str:
    if t.kind == "bool":
        return "вкл" if value else "выкл"
    if t.kind == "str":
        return f"@{value}" if value else "—"
    if t.kind == "float":
        return f"{value:g}".replace(".", ",")
    return str(value)


def base(ctx: Context):
    return ctx.base_settings or ctx.settings


def _check_combo(settings) -> None:
    if settings.payments_live and settings.demo_mode:
        raise ValueError("сейчас демо-режим сборки: оплату включить нельзя")


def _apply(ctx: Context, overrides: dict) -> None:
    if ctx.base_settings is None:
        ctx.base_settings = ctx.settings
    new = dataclasses.replace(ctx.base_settings, **overrides)
    ctx.settings = new
    ctx.overrides = dict(overrides)
    # сборщик и PackRunner держат свои ссылки на настройки
    if ctx.runner is not None:
        ctx.runner.settings = new
    for holder in (ctx.builder, getattr(ctx.runner, "builder", None)):
        if holder is None:
            continue
        if hasattr(holder, "settings"):
            holder.settings = new
        if hasattr(holder, "_settings"):
            holder._settings = new


async def load(ctx: Context) -> None:
    """При старте: правки настроек, флаги и админы из базы."""
    rows = await ctx.db.fetchall("SELECT key, value FROM kv WHERE key LIKE 'set:%'")
    overrides = {}
    for r in rows:
        t = BY_KEY.get(r["key"][4:])
        if t is not None:
            try:
                overrides[t.key] = _decode(t, r["value"])
            except ValueError:
                continue
    if ctx.base_settings is None:
        ctx.base_settings = ctx.settings
    if overrides.get("payments_live") and ctx.base_settings.demo_mode:
        overrides.pop("payments_live")
    _apply(ctx, overrides)
    ctx.maintenance = (await kv_get(ctx.db, "maintenance")) == "1"
    hidden = await kv_get(ctx.db, "hidden_categories", "")
    ctx.hidden_categories = frozenset(c for c in (hidden or "").split(",") if c)
    rows = await ctx.db.fetchall("SELECT user_id FROM admins")
    ctx.extra_admins = frozenset(r["user_id"] for r in rows)


async def set_setting(ctx: Context, key: str, value) -> None:
    """value None — вернуть значение из .env."""
    t = BY_KEY[key]
    overrides = dict(ctx.overrides)
    if value is None:
        overrides.pop(key, None)
    else:
        overrides[key] = value
    _check_combo(dataclasses.replace(base(ctx), **overrides))
    await kv_set(ctx.db, f"set:{key}", None if value is None else _encode(t, value))
    _apply(ctx, overrides)


# ───────────────────────── флаги ─────────────────────────

async def set_maintenance(ctx: Context, on: bool) -> None:
    await kv_set(ctx.db, "maintenance", "1" if on else None)
    ctx.maintenance = on


async def toggle_category(ctx: Context, category: str) -> bool:
    """→ True, если категория теперь скрыта."""
    hidden = set(ctx.hidden_categories)
    hidden.symmetric_difference_update({category})
    await kv_set(ctx.db, "hidden_categories", ",".join(sorted(hidden)) or None)
    ctx.hidden_categories = frozenset(hidden)
    return category in hidden


# ───────────────────────── админы ─────────────────────────

def is_admin(ctx: Context, user_id: int) -> bool:
    return user_id in ctx.settings.admin_ids or user_id in ctx.extra_admins


async def add_admin(ctx: Context, user_id: int, by: int) -> None:
    async with ctx.db.tx() as t:
        await t.execute("INSERT OR IGNORE INTO admins (user_id, added_by, created_at) VALUES (?,?,?)",
                        (user_id, by, services.now()))
    ctx.extra_admins = ctx.extra_admins | {user_id}


async def remove_admin(ctx: Context, user_id: int) -> None:
    async with ctx.db.tx() as t:
        await t.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    ctx.extra_admins = ctx.extra_admins - {user_id}


# ───────────────────────── пользователи ─────────────────────────

async def set_banned(db: Database, user_id: int, banned: bool) -> None:
    async with db.tx() as t:
        await t.execute("UPDATE users SET banned=? WHERE id=?", (1 if banned else 0, user_id))


async def set_blocked(db: Database, user_id: int, blocked: bool) -> None:
    async with db.tx() as t:
        await t.execute("UPDATE users SET blocked=? WHERE id=?", (1 if blocked else 0, user_id))


async def set_plan(db: Database, user_id: int, plan: str) -> None:
    async with db.tx() as t:
        await t.execute("UPDATE users SET plan=? WHERE id=?", (plan, user_id))


# ───────────────────────── промокоды ─────────────────────────

class PromoError(services.ServiceError):
    code = "promo"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason   # unknown | used | exhausted


def clean_code(code: str) -> str:
    return (code or "").strip().upper()


async def create_promo(db: Database, code: str | None, gens: int, uses_max: int, by: int) -> str:
    code = clean_code(code or "") or secrets.token_hex(4).upper()
    if not PROMO.match(code):
        raise ValueError("код: латиница, цифры, _ и -, от 3 до 32 символов")
    if not 1 <= gens <= 10_000 or not 1 <= uses_max <= 1_000_000:
        raise ValueError("генераций 1–10000, активаций 1–1000000")
    async with db.tx() as t:
        if await t.fetchone("SELECT 1 FROM promos WHERE code=?", (code,)):
            raise ValueError("такой код уже есть")
        await t.execute("INSERT INTO promos (code, gens, uses_max, created_by, created_at) VALUES (?,?,?,?,?)",
                        (code, gens, uses_max, by, services.now()))
    return code


async def delete_promo(db: Database, code: str) -> None:
    async with db.tx() as t:
        await t.execute("DELETE FROM promos WHERE code=?", (code,))


async def list_promos(db: Database) -> list:
    return await db.fetchall("SELECT * FROM promos ORDER BY created_at DESC LIMIT 30")


async def redeem_promo(db: Database, user_id: int, code: str) -> tuple[int, int]:
    """Активация: +генерации на баланс (через ledger). → (сколько начислено, новый баланс)."""
    code = clean_code(code)
    async with db.tx() as t:
        promo = await t.fetchone("SELECT * FROM promos WHERE code=?", (code,))
        if promo is None:
            raise PromoError("unknown")
        if await t.fetchone("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, user_id)):
            raise PromoError("used")
        if promo["uses"] >= promo["uses_max"]:
            raise PromoError("exhausted")
        await t.execute("INSERT INTO promo_uses (code, user_id, created_at) VALUES (?,?,?)", (code, user_id, services.now()))
        await t.execute("UPDATE promos SET uses = uses + 1 WHERE code=?", (code,))
        await services._apply(t, user_id, promo["gens"], "promo", f"{code}:{user_id}")
        row = await t.fetchone("SELECT balance FROM users WHERE id=?", (user_id,))
    return promo["gens"], row["balance"]
