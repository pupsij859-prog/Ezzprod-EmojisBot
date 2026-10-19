"""Партнёрские ссылки: реклама у блогеров и в каналах со своей ссылкой и статистикой.

Ссылка — t.me/<бот>?start=p_<код> (или t.me/<бот>/app?startapp=p_<код> сразу в Mini App). Кто впервые пришёл
по ней, навсегда помечается кодом ссылки (users.partner) — так считается вся статистика: новички, активные,
покупатели, выручка. Плюс к этому, пока ссылка включена и не истекла:
  • новичок получает bonus_new генераций (не больше uses_max новичков, 0 — без лимита);
  • владелец ссылки (партнёр — тоже пользователь бота) получает reward генераций за каждого приведённого,
    когда тот выполнил условие reward_on: 'signup' — просто зашёл, 'pack' — собрал первый пак, 'purchase' —
    сделал первую покупку. Не больше reward_cap наград (0 — без лимита).
Право на награду фиксируется при регистрации (users.partner_rewarded: 0 — ждёт, 1 — выдана, 2 — не положена),
поэтому человек, пришедший во время акции, засчитается партнёру и после её конца. Генерации — только через
ledger (причины 'partner_bonus' и 'partner', ref = '<код>:<id новичка>'), как и все деньги в приложении.
"""
from __future__ import annotations

import dataclasses
import re
import secrets

from .db import Database, Tx

PARAM = re.compile(r"^p_([a-z0-9_]{3,32})\Z")
CODE = re.compile(r"^[a-z0-9_]{3,32}\Z")
REWARD_ON = {"signup": "за регистрацию", "pack": "за первый пак", "purchase": "за первую покупку"}
DAY = 86400


def now() -> int:
    from .services import now as _now

    return _now()


def code_from_param(param: str | None) -> str | None:
    m = PARAM.match((param or "").strip())
    return m.group(1) if m else None


def clean_code(raw: str) -> str:
    code = (raw or "").strip().lower().replace("-", "_")
    if not CODE.match(code):
        raise ValueError("код: латиница, цифры и _, от 3 до 32 символов")
    return code


def links(bot_username: str, code: str) -> tuple[str | None, str | None]:
    """(ссылка в бота, ссылка сразу в Mini App)."""
    if not bot_username:
        return None, None
    return f"https://t.me/{bot_username}?start=p_{code}", f"https://t.me/{bot_username}/app?startapp=p_{code}"


@dataclasses.dataclass
class Draft:
    name: str = ""
    code: str = ""
    owner_id: int = 0
    bonus_new: int = 0
    reward: int = 0
    reward_on: str = "pack"
    reward_cap: int = 0
    uses_max: int = 0
    days: int = 0

    def check(self, final: bool = True) -> None:
        """final=False — проверка по ходу заполнения: без названия и без связки «награда ↔ партнёр»."""
        if final and not self.name.strip():
            raise ValueError("нужно название")
        if len(self.name) > 64:
            raise ValueError("название до 64 символов")
        if self.code:
            clean_code(self.code)
        if self.reward_on not in REWARD_ON:
            raise ValueError("неизвестное условие награды")
        for key, hi in (("bonus_new", 1000), ("reward", 1000), ("reward_cap", 1_000_000), ("uses_max", 1_000_000),
                        ("days", 3650)):
            v = getattr(self, key)
            if not isinstance(v, int) or not 0 <= v <= hi:
                raise ValueError(f"значение вне диапазона 0–{hi}")
        if final and self.reward and not self.owner_id:
            raise ValueError("награда партнёру есть, а партнёр не указан")

    @classmethod
    def from_row(cls, r) -> "Draft":
        days = 0
        if r["expires_at"]:
            days = max(1, round((r["expires_at"] - r["created_at"]) / DAY))
        return cls(name=r["name"], code=r["code"], owner_id=r["owner_id"] or 0, bonus_new=r["bonus_new"],
                   reward=r["reward"], reward_on=r["reward_on"], reward_cap=r["reward_cap"], uses_max=r["uses_max"],
                   days=days)


# ───────────────────────── хранение ─────────────────────────

async def create(db: Database, d: Draft, created_by: int | None) -> str:
    d.check()
    async with db.tx() as t:
        code = d.code
        if code:
            if await t.fetchone("SELECT 1 FROM partner_links WHERE code=?", (code,)):
                raise ValueError(f"код {code} уже занят")
        else:
            for _ in range(20):
                code = secrets.token_hex(3)
                if await t.fetchone("SELECT 1 FROM partner_links WHERE code=?", (code,)) is None:
                    break
        ts = now()
        await t.execute(
            "INSERT INTO partner_links (code, name, owner_id, bonus_new, reward, reward_on, reward_cap, uses_max,"
            " expires_at, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (code, d.name.strip(), d.owner_id or None, d.bonus_new, d.reward, d.reward_on, d.reward_cap, d.uses_max,
             ts + d.days * DAY if d.days else None, created_by, ts))
    return code


async def update(db: Database, code: str, d: Draft) -> None:
    """Меняет настройки ссылки (код не меняется — он уже в рекламе). Срок считается от даты создания."""
    d.check()
    async with db.tx() as t:
        r = await t.fetchone("SELECT created_at FROM partner_links WHERE code=?", (code,))
        if r is None:
            raise ValueError("ссылки уже нет")
        await t.execute(
            "UPDATE partner_links SET name=?, owner_id=?, bonus_new=?, reward=?, reward_on=?, reward_cap=?, uses_max=?,"
            " expires_at=? WHERE code=?",
            (d.name.strip(), d.owner_id or None, d.bonus_new, d.reward, d.reward_on, d.reward_cap, d.uses_max,
             r["created_at"] + d.days * DAY if d.days else None, code))


async def get(db: Database, code: str):
    return await db.fetchone("SELECT * FROM partner_links WHERE code=?", (code,))


async def list_links(db: Database, offset: int = 0, limit: int = 8):
    return await db.fetchall(
        "SELECT l.*, (SELECT COUNT(*) FROM users u WHERE u.partner=l.code) AS joined"
        " FROM partner_links l ORDER BY l.active DESC, l.created_at DESC LIMIT ? OFFSET ?", (limit, offset))


async def owned_by(db: Database, user_id: int):
    return await db.fetchall("SELECT * FROM partner_links WHERE owner_id=? ORDER BY created_at DESC", (user_id,))


async def set_active(db: Database, code: str, active: bool) -> None:
    async with db.tx() as t:
        await t.execute("UPDATE partner_links SET active=? WHERE code=?", (int(active), code))


async def delete(db: Database, code: str) -> None:
    """Удаляет ссылку. Пометка у пришедших пользователей остаётся (их история не теряется), награды не выдаются."""
    async with db.tx() as t:
        await t.execute("DELETE FROM partner_links WHERE code=?", (code,))
        await t.execute("UPDATE users SET partner_rewarded=2 WHERE partner=? AND partner_rewarded=0", (code,))


def state(r) -> str:
    if not r["active"]:
        return "paused"
    if r["expires_at"] and r["expires_at"] <= now():
        return "expired"
    return "active"


async def count_click(db: Database, code: str) -> bool:
    """Переход по ссылке (нажали Start). False — такой ссылки нет."""
    async with db.tx() as t:
        cur = await t.execute("UPDATE partner_links SET clicks = clicks + 1 WHERE code=?", (code,))
        return cur.rowcount > 0


# ───────────────────────── регистрация и награды (внутри транзакций services) ─────────────────────────

async def attach_new_user(t: Tx, uid: int, code: str | None) -> tuple[int, int] | None:
    """Новый пользователь пришёл по ссылке: пометить, выдать бонус новичку и (при reward_on='signup') награду
    партнёру. Возвращает (партнёр, награда), если она выдана сейчас."""
    from .services import _apply

    if not code:
        return None
    link = await t.fetchone("SELECT * FROM partner_links WHERE code=?", (code,))
    if link is None:
        return None
    live = state(link) == "active"
    eligible = live and link["reward"] > 0 and link["owner_id"] and link["owner_id"] != uid
    await t.execute("UPDATE users SET partner=?, partner_rewarded=? WHERE id=?", (code, 0 if eligible else 2, uid))
    if live and link["bonus_new"] > 0:
        given = await t.fetchone(
            "SELECT COUNT(*) AS n FROM ledger WHERE reason='partner_bonus' AND ref LIKE ?", (f"{code}:%",))
        if not link["uses_max"] or given["n"] < link["uses_max"]:
            await _apply(t, uid, link["bonus_new"], "partner_bonus", f"{code}:{uid}")
    if eligible and link["reward_on"] == "signup":
        return await maybe_reward(t, uid)
    return None


async def maybe_reward(t: Tx, uid: int) -> tuple[int, int] | None:
    """Награда партнёру, если приведённый выполнил условие ссылки. Один раз на пользователя.
    Вызывается при регистрации, после сборки пака и после оплаты."""
    from .services import _apply

    user = await t.fetchone("SELECT partner, partner_rewarded, purchased_total FROM users WHERE id=?", (uid,))
    if user is None or not user["partner"] or user["partner_rewarded"] != 0:
        return None
    link = await t.fetchone("SELECT * FROM partner_links WHERE code=?", (user["partner"],))
    if link is None or not link["owner_id"] or link["reward"] <= 0:
        return None
    if link["reward_on"] == "pack":
        if await t.fetchone("SELECT 1 FROM packs WHERE user_id=? AND status='ready' LIMIT 1", (uid,)) is None:
            return None
    elif link["reward_on"] == "purchase" and user["purchased_total"] <= 0:
        return None
    if link["reward_cap"]:
        paid = await t.fetchone("SELECT COUNT(*) AS n FROM ledger WHERE reason='partner' AND ref LIKE ?",
                                (f"{link['code']}:%",))
        if paid["n"] >= link["reward_cap"]:
            return None  # лимит наград: ждёт, вдруг лимит поднимут
    if await t.fetchone("SELECT 1 FROM users WHERE id=?", (link["owner_id"],)) is None:
        return None
    await t.execute("UPDATE users SET partner_rewarded=1 WHERE id=?", (uid,))
    if await _apply(t, link["owner_id"], link["reward"], "partner", f"{link['code']}:{uid}"):
        return link["owner_id"], link["reward"]
    return None


async def signup_reward(db: Database, uid: int) -> tuple[int, int] | None:
    """Награда партнёру, выданная при регистрации uid (условие 'signup') — чтобы уведомить партнёра."""
    row = await db.fetchone(
        "SELECT l.user_id, l.delta FROM ledger l JOIN users u ON u.id=? AND l.ref = u.partner || ':' || u.id"
        " WHERE l.reason='partner'", (uid,))
    return (row["user_id"], row["delta"]) if row else None


# ───────────────────────── статистика ─────────────────────────

async def stats(db: Database, code: str) -> dict:
    async def one(sql: str, params: tuple = ()) -> int:
        row = await db.fetchone(sql, params)
        return int((row[0] if row else 0) or 0)

    since_day, since_week = now() - DAY, now() - 7 * DAY
    joined_sql = "SELECT id FROM users WHERE partner=?"
    pay = await db.fetchone(
        "SELECT COUNT(*) AS buys, COUNT(DISTINCT pe.user_id) AS buyers, COALESCE(SUM(p.qty),0) AS qty,"
        " COALESCE(SUM(CASE WHEN pe.provider='stars' THEN pe.amount ELSE 0 END),0) AS stars,"
        " COALESCE(SUM(CASE WHEN pe.provider!='stars' THEN p.amount_rub ELSE 0 END),0) AS rub"
        " FROM payment_events pe LEFT JOIN payments p ON p.id = pe.payload"
        f" WHERE pe.status IN ('credited','credited_repeat') AND pe.user_id IN ({joined_sql})", (code,))
    days = await db.fetchall(
        "SELECT (created_at / 86400) AS d, COUNT(*) AS n FROM users WHERE partner=? AND created_at>=? GROUP BY d",
        (code, now() - 7 * DAY))
    return {
        "joined": await one("SELECT COUNT(*) FROM users WHERE partner=?", (code,)),
        "joined_day": await one("SELECT COUNT(*) FROM users WHERE partner=? AND created_at>=?", (code, since_day)),
        "joined_week": await one("SELECT COUNT(*) FROM users WHERE partner=? AND created_at>=?", (code, since_week)),
        "active": await one(f"SELECT COUNT(DISTINCT user_id) FROM packs WHERE status='ready' AND user_id IN ({joined_sql})",
                            (code,)),
        "packs": await one(f"SELECT COUNT(*) FROM packs WHERE status='ready' AND user_id IN ({joined_sql})", (code,)),
        "buyers": int(pay["buyers"]), "buys": int(pay["buys"]), "qty": int(pay["qty"]),
        "stars": int(pay["stars"]), "rub": int(pay["rub"]),
        "bonus_given": await one("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE reason='partner_bonus' AND ref LIKE ?",
                                 (f"{code}:%",)),
        "bonus_users": await one("SELECT COUNT(*) FROM ledger WHERE reason='partner_bonus' AND ref LIKE ?", (f"{code}:%",)),
        "reward_given": await one("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE reason='partner' AND ref LIKE ?",
                                  (f"{code}:%",)),
        "rewards": await one("SELECT COUNT(*) FROM ledger WHERE reason='partner' AND ref LIKE ?", (f"{code}:%",)),
        "pending": await one("SELECT COUNT(*) FROM users WHERE partner=? AND partner_rewarded=0", (code,)),
        "by_day": {int(r["d"]): int(r["n"]) for r in days},
    }


async def joined_users(db: Database, code: str, limit: int = 15):
    return await db.fetchall(
        "SELECT u.id, u.username, u.first_name, u.last_name, u.created_at, u.purchased_total, u.partner_rewarded,"
        " (SELECT COUNT(*) FROM packs p WHERE p.user_id=u.id AND p.status='ready') AS packs"
        " FROM users u WHERE u.partner=? ORDER BY u.created_at DESC LIMIT ?", (code, limit))
