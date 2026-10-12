"""Бизнес-логика: пользователи, баланс (ledger), паки, рефералы, платежи, стили."""
from __future__ import annotations

import json
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from . import catalog
from .config import Settings
from .db import Database, Tx

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
IDEM_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
# управляющие, bidi и «невидимые» символы, а также запрещённые в XML 1.0 (U+FFFE, U+FFFF)
_CONTROL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁠﻿￾￿]")

DEFAULT_FILL = "#131822"
DEFAULT_STROKE = "#a8c7ef"


class ServiceError(Exception):
    code = "error"


class InsufficientBalance(ServiceError):
    code = "insufficient_balance"

    def __init__(self, need: int) -> None:
        super().__init__(f"need {need} more")
        self.need = need


class ProRequired(ServiceError):
    code = "pro_required"


class ValidationFailed(ServiceError):
    code = "validation"


class RefundError(ServiceError):
    code = "refund_error"


def now() -> int:
    return int(time.time())


# ───────────────────────── пользователи ─────────────────────────

async def _unique_ref_code(t: Tx) -> str:
    for _ in range(10):
        code = secrets.token_hex(4)  # 8 hex-символов: безопасно для ?start=ref_<code>
        if await t.fetchone("SELECT 1 FROM users WHERE ref_code=?", (code,)) is None:
            return code
    raise RuntimeError("не удалось сгенерировать ref_code")


async def _apply(t: Tx, user_id: int, delta: int, reason: str, ref: str) -> bool:
    """Идемпотентно меняет баланс. False — операция с такой (reason, ref) уже была."""
    cur = await t.execute(
        "INSERT OR IGNORE INTO ledger (user_id, delta, reason, ref, created_at) VALUES (?,?,?,?,?)",
        (user_id, delta, reason, ref, now()),
    )
    if cur.rowcount == 0:
        return False
    if delta < 0:
        res = await t.execute(
            "UPDATE users SET balance = balance + ? WHERE id = ? AND balance + ? >= 0",
            (delta, user_id, delta),
        )
        if res.rowcount == 0:
            row = await t.fetchone("SELECT balance FROM users WHERE id=?", (user_id,))
            have = row["balance"] if row else 0
            raise InsufficientBalance(need=-delta - have)
    else:
        await t.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (delta, user_id))
    return True


async def get_user(db: Database, user_id: int):
    return await db.fetchone("SELECT * FROM users WHERE id=?", (user_id,))


async def upsert_user(
    db: Database, settings: Settings, tg: dict, ref_code: str | None = None
) -> tuple[Any, bool]:
    """Создаёт пользователя при первом обращении. Реферал привязывается ТОЛЬКО при создании.

    Обычное чтение (пользователь есть, данные не изменились) не берёт блокировку записи."""
    uid = int(tg["id"])
    username = tg.get("username")
    first = (tg.get("first_name") or "")[:128]
    last = (tg.get("last_name") or "")[:128]

    row = await db.fetchone("SELECT * FROM users WHERE id=?", (uid,))
    if row is not None and (row["username"], row["first_name"], row["last_name"]) == (username, first, last):
        return row, False

    async with db.tx() as t:
        row = await t.fetchone("SELECT * FROM users WHERE id=?", (uid,))
        created = False
        if row is None:
            referred_by = None
            if ref_code:
                ref = await t.fetchone("SELECT id FROM users WHERE ref_code=?", (ref_code,))
                if ref is not None and ref["id"] != uid:
                    referred_by = ref["id"]
            await t.execute(
                "INSERT INTO users (id, username, first_name, last_name, balance, trial_exports_left,"
                " ref_code, referred_by, created_at) VALUES (?,?,?,?,0,1,?,?,?)",
                (uid, username, first, last, await _unique_ref_code(t), referred_by, now()),
            )
            if settings.signup_gens > 0:
                await _apply(t, uid, settings.signup_gens, "signup", str(uid))
            created = True
        elif (row["username"], row["first_name"], row["last_name"]) != (username, first, last):
            await t.execute(
                "UPDATE users SET username=?, first_name=?, last_name=? WHERE id=?",
                (username, first, last, uid),
            )
        row = await t.fetchone("SELECT * FROM users WHERE id=?", (uid,))
    return row, created


async def complete_onboarding(db: Database, user_id: int) -> None:
    async with db.tx() as t:
        await t.execute("UPDATE users SET onboarded=1 WHERE id=?", (user_id,))


# ───────────────────────── паки ─────────────────────────

def clean_text(value: Any, limit: int) -> str:
    text = _CONTROL.sub("", str(value or "")).strip()
    return text[:limit]


def validate_pack_request(settings: Settings, body: dict) -> dict:
    kind = body.get("type")
    if kind not in ("emoji", "sticker"):
        raise ValidationFailed("type")
    ids = body.get("template_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids):
        raise ValidationFailed("template_ids")
    ids = list(dict.fromkeys(ids))  # без дублей, порядок сохраняем
    limit = settings.emoji_pack_max if kind == "emoji" else settings.sticker_pack_max
    if len(ids) > limit:
        raise ValidationFailed("too_many")
    if not catalog.exists_all(ids):
        raise ValidationFailed("unknown_template")
    fill = body.get("fill") or DEFAULT_FILL
    stroke = body.get("stroke") or DEFAULT_STROKE
    if not (isinstance(fill, str) and HEX_COLOR.match(fill) and isinstance(stroke, str) and HEX_COLOR.match(stroke)):
        raise ValidationFailed("color")
    logo_id = body.get("logo_id")
    if logo_id is not None and not (isinstance(logo_id, str) and re.fullmatch(r"[0-9a-f]{32}", logo_id)):
        raise ValidationFailed("logo")
    return {
        "type": kind,
        "template_ids": ids,
        "text": clean_text(body.get("text"), settings.text_max),
        "logo_id": logo_id,
        "fill": fill.lower(),
        "stroke": stroke.lower(),
    }


async def create_pack(
    db: Database, settings: Settings, user_id: int, req: dict, duration_s: float, idem_key: str | None = None
) -> tuple[dict, bool]:
    """Списывает генерации и создаёт пак в статусе processing (одной транзакцией).

    Повтор запроса с тем же idem_key возвращает уже созданный пак и ничего не списывает.
    Возвращает (пак, создан_ли_сейчас)."""
    if idem_key is not None and not IDEM_KEY.match(idem_key):
        raise ValidationFailed("idempotency_key")
    cost = len(req["template_ids"])
    async with db.tx() as t:
        if idem_key:
            existing = await t.fetchone("SELECT * FROM packs WHERE user_id=? AND idem_key=?", (user_id, idem_key))
            if existing is not None:
                return pack_view(existing), False
        if req["logo_id"]:
            logo = await t.fetchone("SELECT 1 FROM logos WHERE id=? AND user_id=?", (req["logo_id"], user_id))
            if logo is None:
                raise ValidationFailed("logo")
        user = await t.fetchone("SELECT balance FROM users WHERE id=?", (user_id,))
        if user is None:
            raise ValidationFailed("user")
        if user["balance"] < cost:
            raise InsufficientBalance(need=cost - user["balance"])
        suffix = "эмодзи" if req["type"] == "emoji" else "стикеры"
        title = f"{req['text'] or settings.brand_name} · {suffix}"
        cur = await t.execute(
            "INSERT INTO packs (user_id, type, title, status, count, items, params, idem_key, duration_s, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                user_id, req["type"], title, "processing", cost,
                json.dumps(req["template_ids"]),
                json.dumps({k: req[k] for k in ("text", "logo_id", "fill", "stroke")}),
                idem_key, duration_s, now(),
            ),
        )
        pack_id = cur.lastrowid
        await _apply(t, user_id, -cost, "pack", str(pack_id))
        row = await t.fetchone("SELECT * FROM packs WHERE id=?", (pack_id,))
    return pack_view(row), True


def pack_view(row) -> dict:
    items = json.loads(row["items"])
    params = json.loads(row["params"])
    if row["status"] == "processing":
        elapsed = time.time() - row["created_at"]
        progress = min(0.99, elapsed / row["duration_s"]) if row["duration_s"] > 0 else 0.99
    else:
        progress = 1.0 if row["status"] == "ready" else 0.0
    return {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "status": row["status"],
        "count": row["count"],
        "link": row["link"],
        "progress": round(progress, 3),
        "created_at": row["created_at"],
        "preview": items[:4],
        "params": {"text": params.get("text", ""), "fill": params.get("fill"), "stroke": params.get("stroke")},
    }


async def get_pack(db: Database, user_id: int, pack_id: int):
    return await db.fetchone("SELECT * FROM packs WHERE id=? AND user_id=?", (pack_id, user_id))


async def list_packs(db: Database, user_id: int) -> list[dict]:
    rows = await db.fetchall("SELECT * FROM packs WHERE user_id=? ORDER BY id DESC LIMIT 100", (user_id,))
    return [pack_view(r) for r in rows]


@dataclass
class FinishResult:
    user_id: int
    pack: dict
    reward_user_id: int | None = None
    reward_amount: int = 0


async def finish_pack(db: Database, settings: Settings, pack_id: int, link: str) -> FinishResult | None:
    """processing → ready, списывает «пробный экспорт», начисляет награду рефереру (один раз)."""
    async with db.tx() as t:
        cur = await t.execute(
            "UPDATE packs SET status='ready', link=?, ready_at=? WHERE id=? AND status='processing'",
            (link, now(), pack_id),
        )
        if cur.rowcount == 0:
            return None
        pack = await t.fetchone("SELECT * FROM packs WHERE id=?", (pack_id,))
        uid = pack["user_id"]
        await t.execute(
            "UPDATE users SET trial_exports_left = MAX(trial_exports_left - 1, 0) WHERE id=?", (uid,)
        )
        result = FinishResult(user_id=uid, pack=pack_view(pack))

        user = await t.fetchone("SELECT referred_by, referral_rewarded FROM users WHERE id=?", (uid,))
        if user["referred_by"] and user["referral_rewarded"] == 0:
            referrer = user["referred_by"]
            today = await t.fetchone(
                "SELECT COUNT(*) AS n FROM ledger WHERE user_id=? AND reason='referral' AND created_at >= ?",
                (referrer, now() - 86400),
            )
            total = await t.fetchone(
                "SELECT COUNT(*) AS n FROM ledger WHERE user_id=? AND reason='referral'", (referrer,)
            )
            if today["n"] >= settings.referral_daily_cap or total["n"] >= settings.referral_lifetime_cap:
                await t.execute("UPDATE users SET referral_rewarded=2 WHERE id=?", (uid,))  # упёрлись в лимит
            else:
                if await _apply(t, referrer, settings.referral_reward, "referral", str(uid)):
                    result.reward_user_id = referrer
                    result.reward_amount = settings.referral_reward
                await t.execute("UPDATE users SET referral_rewarded=1 WHERE id=?", (uid,))
    return result


async def fail_pack(db: Database, pack_id: int) -> int | None:
    """processing → failed и возврат генераций. Возвращает user_id или None."""
    async with db.tx() as t:
        cur = await t.execute("UPDATE packs SET status='failed' WHERE id=? AND status='processing'", (pack_id,))
        if cur.rowcount == 0:
            return None
        pack = await t.fetchone("SELECT user_id, count FROM packs WHERE id=?", (pack_id,))
        await _apply(t, pack["user_id"], pack["count"], "pack_refund", str(pack_id))
        return pack["user_id"]


async def processing_pack_ids(db: Database) -> list[int]:
    rows = await db.fetchall("SELECT id FROM packs WHERE status='processing' ORDER BY id")
    return [r["id"] for r in rows]


# ───────────────────────── рефералы ─────────────────────────

async def referral_stats(db: Database, settings: Settings, user_id: int) -> dict:
    invited = await db.fetchone("SELECT COUNT(*) AS n FROM users WHERE referred_by=?", (user_id,))
    created = await db.fetchone(
        "SELECT COUNT(*) AS n FROM users WHERE referred_by=? AND referral_rewarded IN (1,2)", (user_id,)
    )
    earned = await db.fetchone(
        "SELECT COALESCE(SUM(delta),0) AS n FROM ledger WHERE user_id=? AND reason='referral'", (user_id,)
    )
    return {
        "invited": invited["n"],
        "created_pack": created["n"],
        "earned": earned["n"],
        "reward": settings.referral_reward,
    }


# ───────────────────────── стили (pro) ─────────────────────────

async def list_styles(db: Database, user_id: int) -> list[dict]:
    rows = await db.fetchall("SELECT * FROM saved_styles WHERE user_id=? ORDER BY id DESC", (user_id,))
    return [{"id": r["id"], "name": r["name"], "params": json.loads(r["params"])} for r in rows]


async def add_style(db: Database, settings: Settings, user_id: int, name: str, params: dict) -> dict:
    name = clean_text(name, 32)
    if not name:
        raise ValidationFailed("name")
    fill = params.get("fill") or DEFAULT_FILL
    stroke = params.get("stroke") or DEFAULT_STROKE
    if not (HEX_COLOR.match(str(fill)) and HEX_COLOR.match(str(stroke))):
        raise ValidationFailed("color")
    clean = {"text": clean_text(params.get("text"), settings.text_max), "fill": fill.lower(), "stroke": stroke.lower()}
    async with db.tx() as t:
        user = await t.fetchone("SELECT plan FROM users WHERE id=?", (user_id,))
        if user is None or user["plan"] != "pro":
            raise ProRequired()
        count = await t.fetchone("SELECT COUNT(*) AS n FROM saved_styles WHERE user_id=?", (user_id,))
        if count["n"] >= settings.styles_max:
            raise ValidationFailed("too_many_styles")
        cur = await t.execute(
            "INSERT INTO saved_styles (user_id, name, params, created_at) VALUES (?,?,?,?)",
            (user_id, name, json.dumps(clean), now()),
        )
    return {"id": cur.lastrowid, "name": name, "params": clean}


async def delete_style(db: Database, user_id: int, style_id: int) -> bool:
    async with db.tx() as t:
        cur = await t.execute("DELETE FROM saved_styles WHERE id=? AND user_id=?", (style_id, user_id))
        return cur.rowcount > 0


# ───────────────────────── платежи ─────────────────────────

async def create_payment(db: Database, user_id: int, qty: int, stars: int) -> str:
    payment_id = uuid.uuid4().hex
    async with db.tx() as t:
        await t.execute(
            "INSERT INTO payments (id, user_id, qty, stars, created_at) VALUES (?,?,?,?,?)",
            (payment_id, user_id, qty, stars, now()),
        )
    return payment_id


async def get_payment(db: Database, payment_id: str):
    return await db.fetchone("SELECT * FROM payments WHERE id=?", (payment_id,))


@dataclass
class SettleResult:
    user_id: int
    qty: int
    balance: int
    status: str            # 'credited' | 'needs_review'
    repeat: bool = False   # человек заплатил по тому же счёту ещё раз


def _plan_for(settings: Settings, purchased_total: int) -> str:
    return "pro" if purchased_total >= settings.pro_min_purchased else "free"


async def settle_payment(
    db: Database,
    settings: Settings,
    payment_id: str,
    charge_id: str,
    total_amount: int,
    user_id: int,
    currency: str = "XTR",
    provider: str = "stars",
) -> SettleResult | None:
    """Обрабатывает реальное поступление денег. Никогда не бросает исключений «наружу» после списания.

    - повторная доставка того же платежа (тот же charge_id) → None, ничего не происходит;
    - платёж не сходится с заказом (чужой пользователь, сумма, неизвестный заказ) → событие в статусе
      needs_review, генерации НЕ начисляются, нужен ручной разбор;
    - иначе начисляем qty генераций. Если по одному счёту заплатили второй раз, начисляем повторно
      (деньги получены), ключ идемпотентности — charge_id, а не payload."""
    async with db.tx() as t:
        cur = await t.execute(
            "INSERT OR IGNORE INTO payment_events (provider, charge_id, payload, user_id, amount, currency, status, created_at)"
            " VALUES (?,?,?,?,?,?,'received',?)",
            (provider, charge_id, payment_id, user_id, total_amount, currency, now()),
        )
        if cur.rowcount == 0:
            return None

        order = await t.fetchone("SELECT * FROM payments WHERE id=?", (payment_id,))
        user = await t.fetchone("SELECT balance, purchased_total FROM users WHERE id=?", (user_id,))
        if (
            order is None or user is None or currency != "XTR"
            or order["user_id"] != user_id or order["stars"] != total_amount
        ):
            await t.execute(
                "UPDATE payment_events SET status='needs_review' WHERE provider=? AND charge_id=?", (provider, charge_id)
            )
            return SettleResult(user_id=user_id, qty=0, balance=user["balance"] if user else 0, status="needs_review")

        repeat = order["status"] == "paid"
        if not repeat:
            await t.execute(
                "UPDATE payments SET status='paid', charge_id=?, paid_at=? WHERE id=?", (charge_id, now(), payment_id)
            )
        await _apply(t, user_id, order["qty"], "purchase", charge_id)
        purchased = user["purchased_total"] + order["qty"]
        await t.execute("UPDATE users SET purchased_total=?, plan=? WHERE id=?",
                        (purchased, _plan_for(settings, purchased), user_id))
        await t.execute(
            "UPDATE payment_events SET status=? WHERE provider=? AND charge_id=?",
            ("credited_repeat" if repeat else "credited", provider, charge_id),
        )
        balance = (await t.fetchone("SELECT balance FROM users WHERE id=?", (user_id,)))["balance"]
    return SettleResult(user_id=user_id, qty=order["qty"], balance=balance, status="credited", repeat=repeat)


async def payment_status(db: Database, user_id: int, payment_id: str) -> str | None:
    """pending | paid — для опроса клиентом после закрытия окна оплаты."""
    row = await db.fetchone("SELECT status FROM payments WHERE id=? AND user_id=?", (payment_id, user_id))
    return row["status"] if row else None


# ───────────────────────── возвраты (вызывает администратор) ─────────────────────────

async def begin_refund(db: Database, settings: Settings, charge_id: str, provider: str = "stars") -> tuple[int, int]:
    """Снимает начисленные генерации и помечает платёж «refunding». Возвращает (user_id, qty).
    Если генерации уже потрачены, возврат невозможен автоматически: RefundError('spent')."""
    async with db.tx() as t:
        ev = await t.fetchone("SELECT * FROM payment_events WHERE provider=? AND charge_id=?", (provider, charge_id))
        if ev is None:
            raise RefundError("unknown")
        if ev["status"] not in ("credited", "credited_repeat"):
            raise RefundError("bad_status")
        order = await t.fetchone("SELECT qty FROM payments WHERE id=?", (ev["payload"],))
        if order is None:
            raise RefundError("unknown")
        qty = order["qty"]
        try:
            await _apply(t, ev["user_id"], -qty, "refund", charge_id)
        except InsufficientBalance:
            raise RefundError("spent")
        user = await t.fetchone("SELECT purchased_total FROM users WHERE id=?", (ev["user_id"],))
        purchased = max(0, user["purchased_total"] - qty)
        await t.execute("UPDATE users SET purchased_total=?, plan=? WHERE id=?",
                        (purchased, _plan_for(settings, purchased), ev["user_id"]))
        await t.execute("UPDATE payment_events SET status='refunding' WHERE id=?", (ev["id"],))
    return ev["user_id"], qty


async def finish_refund(db: Database, charge_id: str, provider: str = "stars") -> None:
    async with db.tx() as t:
        await t.execute("UPDATE payment_events SET status='refunded' WHERE provider=? AND charge_id=?", (provider, charge_id))


async def revert_refund(db: Database, settings: Settings, charge_id: str, provider: str = "stars") -> None:
    """Telegram не вернул деньги — возвращаем пользователю снятые генерации."""
    async with db.tx() as t:
        ev = await t.fetchone("SELECT * FROM payment_events WHERE provider=? AND charge_id=?", (provider, charge_id))
        if ev is None or ev["status"] != "refunding":
            return
        order = await t.fetchone("SELECT qty FROM payments WHERE id=?", (ev["payload"],))
        await _apply(t, ev["user_id"], order["qty"], "refund_revert", charge_id)
        user = await t.fetchone("SELECT purchased_total FROM users WHERE id=?", (ev["user_id"],))
        purchased = user["purchased_total"] + order["qty"]
        await t.execute("UPDATE users SET purchased_total=?, plan=? WHERE id=?",
                        (purchased, _plan_for(settings, purchased), ev["user_id"]))
        await t.execute("UPDATE payment_events SET status='credited' WHERE id=?", (ev["id"],))


# ───────────────────────── проверка согласованности ─────────────────────────

async def ledger_mismatches(db: Database) -> list[tuple[int, int, int]]:
    """Пользователи, у которых баланс не равен сумме журнала: [(user_id, balance, ledger_sum)]. Должно быть пусто."""
    rows = await db.fetchall(
        "SELECT u.id AS id, u.balance AS balance, COALESCE(SUM(l.delta), 0) AS total "
        "FROM users u LEFT JOIN ledger l ON l.user_id = u.id GROUP BY u.id HAVING u.balance != total"
    )
    return [(r["id"], r["balance"], r["total"]) for r in rows]
