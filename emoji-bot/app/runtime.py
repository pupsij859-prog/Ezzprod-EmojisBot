"""Настройки, которые админ меняет на лету из чата (/admin), без перезапуска.

Значения из .env — базовые; поверх них лежат правки из таблицы kv («set:<поле>»). Применение — замена
ctx.settings новой копией (Settings неизменяемый), её же получают сборщик и PackRunner.
Здесь же флаги (техработы, скрытые категории), админы из базы, бан и промокоды.
"""
from __future__ import annotations

import dataclasses
import json
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
    "pay": "💳 Способы оплаты",
    "gift": "🎁 Подарки и рефералка",
    "limits": "📏 Лимиты",
    "other": "⚙️ Прочее",
    "subscription": "🔒 Обязательная подписка",
}

TUNABLES: tuple[Tunable, ...] = (
    Tunable("subscription_required", "Требовать подписку", "subscription", kind="bool"),
    Tunable("subscription_channel", "Канал для подписки", "subscription", kind="channel",
            hint="@username, https://t.me/username или ID -100…; бот должен быть администратором канала"),
    Tunable("subscription_url", "Ссылка на канал", "subscription", kind="telegram_url",
            hint="для публичного канала можно оставить пустой; для закрытого нужна ссылка https://t.me/+…"),
    Tunable("price_rub", "Цена генерации, ₽", "price", lo=1, hi=100_000),
    Tunable("bulk_discount_from", "Скидка от, шт.", "price", lo=2, hi=1_000),
    Tunable("bulk_discount_pct", "Размер скидки, %", "price", lo=0, hi=90),
    Tunable("bulk_unit_from", "Опт от, шт.", "price", lo=2, hi=1_000),
    Tunable("bulk_unit_rub", "Оптовая цена, ₽/шт.", "price", lo=1, hi=100_000),
    Tunable("rub_per_star", "Курс: 1 ⭐ = ₽", "price", kind="float", lo=0, hi=1_000,
            hint="по нему сумма в рублях пересчитывается в Звёзды (вверх до целого)"),
    Tunable("max_buy_qty", "Максимум за покупку, шт.", "price", lo=1, hi=5_000),
    # Способы оплаты
    Tunable("payments_live", "Приём оплаты Stars", "pay", kind="bool",
            hint="в демо-режиме оплату включить нельзя"),
    Tunable("cryptobot_enabled", "Crypto Pay (CryptoBot)", "pay", kind="bool",
            hint="включение приема оплат через CryptoBot"),
    Tunable("cryptobot_token", "CryptoBot API Token", "pay", kind="token",
            hint="токен приложения из @CryptoBot"),
    Tunable("cryptobot_net", "CryptoBot сеть", "pay", kind="str_any",
            hint="mainnet или testnet"),
    Tunable("xrocket_enabled", "xRocket Pay", "pay", kind="bool",
            hint="включение приема оплат через xRocket"),
    Tunable("xrocket_key", "xRocket API Key", "pay", kind="token",
            hint="ключ приложения из @tonRocketBot"),
    Tunable("xrocket_net", "xRocket сеть", "pay", kind="str_any",
            hint="mainnet или testnet"),
    Tunable("rub_per_usdt", "Курс: 1 USDT = ₽", "pay", kind="float", lo=1, hi=10_000,
            hint="курс для пересчета RUB инвойсов в USDT для xRocket"),
    Tunable("signup_gens", "Подарок новичку, генераций", "gift", lo=0, hi=100),
    Tunable("referral_reward", "Награда за друга, генераций", "gift", lo=0, hi=100),
    Tunable("referral_daily_cap", "Наград за друзей в сутки", "gift", lo=0, hi=1_000),
    Tunable("referral_lifetime_cap", "Наград за друзей всего", "gift", lo=0, hi=100_000),
    Tunable("pro_min_purchased", "Pro после покупки, генераций", "gift", lo=0, hi=100_000),
    Tunable("emoji_pack_max", "Эмодзи в паке, макс.", "limits", lo=1, hi=200, hint="Telegram: не больше 200"),
    Tunable("sticker_pack_max", "Стикеров в паке, макс.", "limits", lo=1, hi=120, hint="Telegram: не больше 120"),
    Tunable("text_max", "Длина ника, символов", "limits", lo=1, hi=24),
    Tunable("styles_max", "Сохранённых стилей", "limits", lo=0, hi=200),
    Tunable("logos_max", "Логотипов на человека", "limits", lo=1, hi=500),
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
    if t.kind == "channel":
        if raw in ("", "-"):
            return ""
        name = raw.removeprefix("https://t.me/").removeprefix("http://t.me/").rstrip("/").lstrip("@")
        if re.fullmatch(r"-100[0-9]{5,17}", name):
            return name
        if USERNAME.fullmatch(name):
            return "@" + name
        raise ValueError("нужен @username канала, https://t.me/username или ID вида -1001234567890")
    if t.kind == "telegram_url":
        if raw in ("", "-"):
            return ""
        if re.fullmatch(r"https://t\.me/(?:[A-Za-z][A-Za-z0-9_]{3,31}|\+[A-Za-z0-9_-]+|joinchat/[A-Za-z0-9_-]+)/?", raw):
            return raw
        raise ValueError("нужна ссылка https://t.me/username или https://t.me/+…")
    if t.kind == "bool":
        if raw.lower() in ("1", "да", "вкл", "on", "true"):
            return True
        if raw.lower() in ("0", "нет", "выкл", "off", "false"):
            return False
        raise ValueError("нужно «вкл» или «выкл»")
    if t.kind in ("token", "str_any"):
        val = raw.strip()
        if val.lower() in ("-", "none", "clear", "0", ""):
            return ""
        return val
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
    if t.kind in ("str", "token", "str_any", "channel", "telegram_url"):
        return raw
    if t.kind == "float":
        return float(raw)
    return int(raw)


def show_value(t: Tunable, value) -> str:
    if t.kind == "bool":
        return "вкл" if value else "выкл"
    if t.kind == "token":
        if not value:
            return "—"
        s = str(value)
        return s[:4] + "..." + s[-4:] if len(s) > 10 else "***"
    if t.kind == "str":
        return f"@{value}" if value else "—"
    if t.kind == "str_any":
        return str(value) if value else "—"
    if t.kind in ("channel", "telegram_url"):
        return str(value) if value else "—"
    if t.kind == "float":
        return f"{value:g}".replace(".", ",")
    return str(value)


def base(ctx: Context):
    return ctx.base_settings or ctx.settings


def _check_combo(settings) -> None:
    if settings.subscription_required:
        from .subscription import channel_id, channel_url
        if not channel_id(settings) or not channel_url(settings):
            raise ValueError("сначала укажи канал; для закрытого канала также нужна пригласительная ссылка")
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
    ctx.custom_texts = await load_custom_texts(ctx.db)
    ctx.custom_buttons = await load_custom_buttons(ctx.db)


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
        self.reason = reason   # unknown | used | exhausted | expired | paused | not_new


PROMO_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без похожих O/0, I/1


def clean_code(code: str) -> str:
    return (code or "").strip().upper()


def random_code(prefix: str = "", length: int = 8) -> str:
    return clean_code(prefix) + "".join(secrets.choice(PROMO_ALPHABET) for _ in range(max(4, min(length, 16))))


@dataclass
class PromoDraft:
    """Настройки промокода из конструктора в админке."""
    code: str = ""          # пусто — случайный
    prefix: str = ""        # приставка случайного кода, например «EZZ»
    gens: int = 5           # награда (или нижняя граница случайной)
    gens_max: int = 0       # 0 — награда фиксированная, иначе случайная gens..gens_max
    discount_pct: int = 0   # 0 — нет скидки, 1..99 — скидка в % на покупки
    uses: int = 100         # активаций на код
    days: int = 0           # срок действия, дней (0 — бессрочно)
    only_new: bool = False  # только тем, кто ещё ничего не покупал
    count: int = 1          # сколько разных кодов создать (пачка для раздачи)

    def check(self) -> None:
        if self.code and not PROMO.match(clean_code(self.code)):
            raise ValueError("код: латиница, цифры, _ и -, от 3 до 32 символов")
        if self.prefix and not re.fullmatch(r"[A-Z0-9_-]{1,12}", clean_code(self.prefix)):
            raise ValueError("приставка: латиница, цифры, _ и -, до 12 символов")
        if not 0 <= self.discount_pct <= 99:
            raise ValueError("скидка от 0 до 99%")
        if self.discount_pct == 0 and not 1 <= self.gens <= 10_000:
            raise ValueError("генераций 1–10000 (или укажи процент скидки)")
        if not 0 <= self.gens <= 10_000 or not (self.gens_max == 0 or self.gens < self.gens_max <= 10_000):
            raise ValueError("генераций 0–10000; у случайной награды «от» меньше «до»")
        if not 1 <= self.uses <= 1_000_000:
            raise ValueError("активаций 1–1000000")
        if not 0 <= self.days <= 3650:
            raise ValueError("срок 0–3650 дней")
        if not 1 <= self.count <= 500:
            raise ValueError("кодов за раз 1–500")
        if self.code and self.count > 1:
            raise ValueError("свой код бывает только один: для пачки оставь код случайным")

    def reward_text(self) -> str:
        parts = []
        if self.gens:
            parts.append(f"{self.gens}–{self.gens_max} (случайно)" if self.gens_max else f"{self.gens} генераций")
        if self.discount_pct:
            parts.append(f"скидка {self.discount_pct}%")
        return " + ".join(parts) if parts else "0 генераций"


async def create_promos(db: Database, draft: PromoDraft, by: int) -> list[str]:
    """Создаёт один код или пачку одинаковых по настройкам кодов. → список кодов."""
    draft.check()
    expires = services.now() + draft.days * 86400 if draft.days else None
    batch = secrets.token_hex(4) if draft.count > 1 else None
    codes: list[str] = []
    async with db.tx() as t:
        for _ in range(draft.count):
            for _attempt in range(20):
                code = clean_code(draft.code) or random_code(draft.prefix)
                if not await t.fetchone("SELECT 1 FROM promos WHERE code=?", (code,)):
                    break
                if draft.code:
                    raise ValueError("такой код уже есть")
            else:
                raise ValueError("не получилось придумать свободный код, попробуй другую приставку")
            await t.execute(
                "INSERT INTO promos (code, gens, gens_max, discount_pct, uses_max, expires_at, only_new, batch, created_by, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (code, draft.gens, draft.gens_max or None, draft.discount_pct, draft.uses, expires, int(draft.only_new), batch, by, services.now()))
            codes.append(code)
    return codes


async def create_promo(db: Database, code: str | None, gens: int, uses_max: int, by: int, discount_pct: int = 0) -> str:
    """Быстрое создание одной строкой: КОД ГЕНЕРАЦИЙ АКТИВАЦИЙ."""
    return (await create_promos(db, PromoDraft(code=code or "", gens=gens, uses=uses_max, discount_pct=discount_pct), by))[0]


async def delete_promo(db: Database, code: str) -> None:
    async with db.tx() as t:
        await t.execute("DELETE FROM promos WHERE code=?", (code,))


async def set_promo_active(db: Database, code: str, active: bool) -> None:
    async with db.tx() as t:
        await t.execute("UPDATE promos SET active=? WHERE code=?", (int(active), code))


async def get_promo(db: Database, code: str):
    return await db.fetchone("SELECT * FROM promos WHERE code=?", (clean_code(code),))


async def list_promos(db: Database, offset: int = 0, limit: int = 30) -> list:
    return await db.fetchall("SELECT * FROM promos ORDER BY created_at DESC, code LIMIT ? OFFSET ?", (limit, offset))


async def promo_users(db: Database, code: str, limit: int = 15) -> list:
    return await db.fetchall(
        "SELECT p.user_id, p.gens, p.created_at, u.username, u.first_name, u.last_name FROM promo_uses p"
        " LEFT JOIN users u ON u.id = p.user_id WHERE p.code=? ORDER BY p.created_at DESC LIMIT ?", (code, limit))


def promo_state(promo) -> str:
    """active | paused | expired | exhausted — для списков в админке."""
    if not promo["active"]:
        return "paused"
    if promo["expires_at"] and promo["expires_at"] <= services.now():
        return "expired"
    if promo["uses"] >= promo["uses_max"]:
        return "exhausted"
    return "active"


class PromoRedeemResult:
    def __init__(self, gens: int, balance: int, discount_pct: int = 0) -> None:
        self.gens = gens
        self.balance = balance
        self.discount_pct = discount_pct

    def __iter__(self):
        return iter((self.gens, self.balance))

    def __getitem__(self, index):
        return (self.gens, self.balance, self.discount_pct)[index]

    def __repr__(self) -> str:
        return f"PromoRedeemResult(gens={self.gens}, balance={self.balance}, discount_pct={self.discount_pct})"


async def redeem_promo(db: Database, user_id: int, code: str) -> PromoRedeemResult:
    """Активация: +генерации на баланс (через ledger) и/или скидка на покупки. → PromoRedeemResult."""
    code = clean_code(code)
    if not PROMO.match(code):
        raise PromoError("unknown")
    async with db.tx() as t:
        promo = await t.fetchone("SELECT * FROM promos WHERE code=?", (code,))
        if promo is None:
            raise PromoError("unknown")
        if await t.fetchone("SELECT 1 FROM promo_uses WHERE code=? AND user_id=?", (code, user_id)):
            raise PromoError("used")
        if not promo["active"]:
            raise PromoError("paused")
        if promo["expires_at"] and promo["expires_at"] <= services.now():
            raise PromoError("expired")
        if promo["uses"] >= promo["uses_max"]:
            raise PromoError("exhausted")
        if promo["only_new"]:
            user = await t.fetchone("SELECT purchased_total FROM users WHERE id=?", (user_id,))
            if user is None or user["purchased_total"] > 0:
                raise PromoError("not_new")
        top = promo["gens_max"] or 0
        base_gens = promo["gens"] or 0
        amount = secrets.randbelow(top - base_gens + 1) + base_gens if top > base_gens else base_gens
        disc = promo["discount_pct"] if "discount_pct" in promo.keys() and promo["discount_pct"] else 0
        await t.execute("INSERT INTO promo_uses (code, user_id, created_at, gens, discount_pct) VALUES (?,?,?,?,?)",
                        (code, user_id, services.now(), amount, disc))
        await t.execute("UPDATE promos SET uses = uses + 1 WHERE code=?", (code,))
        if amount > 0:
            await services._apply(t, user_id, amount, "promo", f"{code}:{user_id}")
        if disc > 0:
            await t.execute("UPDATE users SET discount_pct=? WHERE id=?", (disc, user_id))
        row = await t.fetchone("SELECT balance FROM users WHERE id=?", (user_id,))
    return PromoRedeemResult(amount, row["balance"], disc)


# ───────────────────────── кастомные тексты ─────────────────────────

async def load_custom_texts(db: Database) -> dict[str, str]:
    rows = await db.fetchall("SELECT key, value FROM kv WHERE key LIKE 'msg:%'")
    return {r["key"][4:]: r["value"] for r in rows if r["value"]}


async def set_custom_text(db: Database, key: str, text: str | None) -> None:
    await kv_set(db, f"msg:{key}", text)


async def update_custom_text(ctx: Context, key: str, text: str | None) -> None:
    await set_custom_text(ctx.db, key, text)
    if text is None:
        ctx.custom_texts.pop(key, None)
    else:
        ctx.custom_texts[key] = text


# ───────────────────────── кастомные кнопки ─────────────────────────

async def load_custom_buttons(db: Database) -> dict[str, dict]:
    rows = await db.fetchall("SELECT key, value FROM kv WHERE key LIKE 'btn:%'")
    out = {}
    for r in rows:
        val = r["value"]
        if val:
            try:
                out[r["key"][4:]] = json.loads(val)
            except Exception:
                pass
    return out


async def set_custom_button(db: Database, key: str, data: dict | None) -> None:
    val = json.dumps(data, ensure_ascii=False) if data else None
    await kv_set(db, f"btn:{key}", val)


async def update_custom_button(ctx: Context, key: str, data: dict | None) -> None:
    await set_custom_button(ctx.db, key, data)
    if not data:
        ctx.custom_buttons.pop(key, None)
    else:
        ctx.custom_buttons[key] = data

