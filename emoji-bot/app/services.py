"""Бизнес-логика: пользователи, баланс (ledger), паки, рефералы, платежи, стили."""
from __future__ import annotations

import json
import math
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any

from . import catalog
from .config import Settings
from .db import Database, Tx

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}\Z")
IDEM_KEY = re.compile(r"^[A-Za-z0-9_-]{8,64}\Z")
# управляющие, bidi и «невидимые» символы, а также запрещённые в XML 1.0 (U+FFFE, U+FFFF)
_CONTROL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁠﻿￾￿]")



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
    db: Database, settings: Settings, tg: dict, ref_code: str | None = None, partner: str | None = None
) -> tuple[Any, bool]:
    """Создаёт пользователя при первом обращении. Реферал и партнёрская ссылка (app/partners.py) привязываются
    ТОЛЬКО при создании; награду партнёру за регистрацию для уведомления отдаёт partners.signup_reward.

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
            if partner and referred_by is None:
                from . import partners

                await partners.attach_new_user(t, uid, partner)
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
    color = clean_color(body.get("color"))
    stroke = clean_color(body.get("stroke"))
    logo_id = clean_logo_id(body.get("logo_id"))
    return {
        "type": kind,
        "template_ids": ids,
        "text": clean_text(body.get("text"), settings.text_max),
        "logo_id": logo_id,
        "color": color,
        "stroke": stroke,
        "size": clean_size(body.get("size")),
        "place": clean_place(body.get("place")),
        "grad": clean_grad(body.get("grad")),
        "font_id": clean_font_id(body.get("font_id")),
        "overrides": clean_overrides(settings, body.get("overrides"), ids),
    }


ITEM_KEYS = ("text", "logo_id", "color", "stroke", "size", "place", "grad", "font_id", "edit")
PACK_KEYS = ("text", "logo_id", "color", "stroke", "size", "place", "grad", "font_id")


def clean_size(value) -> int:
    """«Размер надписи», % (100 — автоподбор по длине ника)."""
    if value in (None, ""):
        return 100
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValidationFailed("size")
    return int(max(40, min(160, round(value))))


def clean_place(value) -> dict | None:
    """«Положение надписи»: сдвиг и поворот (app/lottie.py → clean_place)."""
    from . import lottie

    try:
        return lottie.clean_place(value)
    except ValueError as exc:
        raise ValidationFailed("place") from exc


def clean_grad(value) -> dict | None:
    """Градиент на ник, логотип и/или заливку (app/lottie.py → clean_grad)."""
    from . import lottie

    try:
        return lottie.clean_grad(value)
    except ValueError as exc:
        raise ValidationFailed("grad") from exc


def clean_edit(value) -> dict | None:
    """Правки продвинутого редактора (Pro) — см. app/editor.py."""
    from . import editor

    try:
        return editor.clean_edit(value)
    except ValueError as exc:
        raise ValidationFailed("edit") from exc


def has_edits(req: dict) -> bool:
    return any(o.get("edit") for o in (req.get("overrides") or {}).values())


def clean_logo_id(value, field: str = "logo") -> str | None:
    """id загруженного файла (логотипа или шрифта): 32 hex-символа или None."""
    if value in (None, ""):
        return None
    if not (isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value)):
        raise ValidationFailed(field)
    return value


def clean_font_id(value) -> str | None:
    """Шрифт ника: id готового шрифта (app/user_fonts.py → PRESETS), id своего (32 hex) или None — стандартный."""
    from .user_fonts import is_preset

    if isinstance(value, str) and is_preset(value):
        return value
    return clean_logo_id(value, "font")


def clean_overrides(settings: Settings, raw, ids: list[str]) -> dict[str, dict]:
    """Свои настройки отдельных элементов: {template_id: {text?, logo_id?, color?, stroke?, size?, edit?}}.
    Есть ключ — значение заменяет общее (в том числе пустое: «без ника», «родной цвет», «без логотипа»).
    edit — правки продвинутого редактора, только у отдельных элементов (их части у каждого шаблона свои)."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict) or len(raw) > len(ids):
        raise ValidationFailed("overrides")
    out: dict[str, dict] = {}
    for template_id, item in raw.items():
        if template_id not in ids or not isinstance(item, dict) or not set(item) <= set(ITEM_KEYS):
            raise ValidationFailed("overrides")
        clean: dict = {}
        if "text" in item:
            if item["text"] is not None and not isinstance(item["text"], str):
                raise ValidationFailed("overrides")
            clean["text"] = clean_text(item["text"], settings.text_max)
        if "logo_id" in item:
            clean["logo_id"] = clean_logo_id(item["logo_id"])
        if "font_id" in item:
            clean["font_id"] = clean_font_id(item["font_id"])
        for key in ("color", "stroke"):
            if key in item:
                clean[key] = clean_color(item[key])
        if "size" in item:
            clean["size"] = clean_size(item["size"])
        if "place" in item:
            clean["place"] = clean_place(item["place"])
        if "grad" in item:
            clean["grad"] = clean_grad(item["grad"])
        if "edit" in item:
            edit = clean_edit(item["edit"])
            if edit:
                clean["edit"] = edit
        if clean:
            out[template_id] = clean
    return out


def clean_color(value) -> str | None:
    """Цвет надписи: '#rrggbb' или None (пусто) — родной цвет каждого шаблона."""
    if value in (None, ""):
        return None
    if not (isinstance(value, str) and HEX_COLOR.match(value)):
        raise ValidationFailed("color")
    return value.lower()


async def _check_owned(t, user_id: int, looks: list[dict]) -> None:
    """Логотипы и свои шрифты в настройках — только свои (готовые шрифты — всем)."""
    from .user_fonts import is_preset

    for logo_id in {lk.get("logo_id") for lk in looks} - {None}:
        if await t.fetchone("SELECT 1 FROM logos WHERE id=? AND user_id=?", (logo_id, user_id)) is None:
            raise ValidationFailed("logo")
    for font_id in {lk.get("font_id") for lk in looks} - {None}:
        if is_preset(font_id):
            continue
        if await t.fetchone("SELECT 1 FROM fonts WHERE id=? AND user_id=?", (font_id, user_id)) is None:
            raise ValidationFailed("font")


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
        overrides = req.get("overrides") or {}
        await _check_owned(t, user_id, [req, *overrides.values()])
        user = await t.fetchone("SELECT balance, plan FROM users WHERE id=?", (user_id,))
        if user is None:
            raise ValidationFailed("user")
        if has_edits(req) and user["plan"] != "pro":  # продвинутые настройки — только в Pro
            raise ProRequired()
        if user["balance"] < cost:
            raise InsufficientBalance(need=cost - user["balance"])
        suffix = "эмодзи" if req["type"] == "emoji" else "стикеры"
        title = f"{req['text'] or settings.brand_name} · {suffix}"
        cur = await t.execute(
            "INSERT INTO packs (user_id, type, title, status, count, items, params, idem_key, duration_s, created_at, live_build)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id, req["type"], title, "processing", cost,
                json.dumps(req["template_ids"]),
                json.dumps({k: req[k] for k in PACK_KEYS if k in req}),
                idem_key, duration_s, now(), int(not settings.demo_mode),
            ),
        )
        pack_id = cur.lastrowid
        for idx, template_id in enumerate(req["template_ids"]):
            own = overrides.get(template_id)
            await t.execute("INSERT INTO pack_items (pack_id, idx, template_id, params) VALUES (?,?,?,?)",
                            (pack_id, idx, template_id, json.dumps(own) if own else None))
        await _apply(t, user_id, -cost, "pack", str(pack_id))
        row = await t.fetchone("SELECT * FROM packs WHERE id=?", (pack_id,))
    return pack_view(row), True


def pack_view(row) -> dict:
    items = json.loads(row["items"])
    params = json.loads(row["params"])
    keys = row.keys()
    done = row["done"] if "done" in keys else 0
    failed = row["failed_items"] if "failed_items" in keys else 0
    if row["status"] == "processing":
        # Живая сборка, включая очередь, показывает только фактически обработанные элементы.
        by_items = (done + failed) / row["count"] if row["count"] else 0
        elapsed = time.time() - row["created_at"]
        by_time = min(0.95, elapsed / row["duration_s"]) if row["duration_s"] > 0 else 0
        live = ("live_build" in keys and row["live_build"]) or ("set_name" in keys and row["set_name"])
        progress = min(0.99, by_items if live else max(by_items, by_time))
    else:
        progress = 1.0 if row["status"] == "ready" else 0.0
    return {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "status": row["status"],
        "count": row["count"],
        "done": done,
        "failed": failed,                      # элементы, которые не удалось собрать (генерации за них вернулись)
        "error": row["error"] if "error" in keys else None,
        "link": row["link"],
        "progress": round(progress, 3),
        "created_at": row["created_at"],
        "preview": items[:4],
        "items": items,
        "params": {"text": params.get("text", ""), "color": params.get("color"), "stroke": params.get("stroke")},
    }


async def get_pack(db: Database, user_id: int, pack_id: int):
    return await db.fetchone(
        "SELECT * FROM packs WHERE id=? AND user_id=? AND (deleted_at IS NULL OR deleted_at = 0)",
        (pack_id, user_id),
    )


async def list_packs(db: Database, user_id: int) -> list[dict]:
    rows = await db.fetchall(
        "SELECT * FROM packs WHERE user_id=? AND (deleted_at IS NULL OR deleted_at = 0) ORDER BY id DESC LIMIT 100",
        (user_id,),
    )
    return [pack_view(r) for r in rows]


async def delete_pack(db: Database, user_id: int, pack_id: int) -> bool:
    """Удаляет пак из списка пользователя (soft delete). Собирающийся пак удалять нельзя."""
    async with db.tx() as t:
        row = await t.fetchone(
            "SELECT status FROM packs WHERE id=? AND user_id=? AND (deleted_at IS NULL OR deleted_at = 0)",
            (pack_id, user_id),
        )
        if row is None:
            return False
        if row["status"] == "processing":
            raise ValidationFailed("pack_processing")
        await t.execute("UPDATE packs SET deleted_at=? WHERE id=?", (now(), pack_id))
        return True


async def add_pack_items(
    db: Database,
    settings: Settings,
    user_id: int,
    pack_id: int,
    template_ids: list[str],
    overrides: dict | None = None,
    params: dict | None = None,
) -> dict:
    """Добавляет новые стикеры/эмодзи в существующий пак пользователя и запускает досборку."""
    if not template_ids:
        raise ValidationFailed("template_ids")
    for tid in template_ids:
        if catalog.get(tid) is None:
            raise ValidationFailed("template_ids")

    # настройки новых элементов проверяем так же строго, как при создании пака
    overrides = clean_overrides(settings, overrides, template_ids)
    params = clean_overrides(settings, {"*": params}, ["*"]).get("*") if params else None
    if params and "edit" in params:
        raise ValidationFailed("params")  # правки редактора бывают только у отдельных элементов

    cost = len(template_ids)
    async with db.tx() as t:
        pack = await t.fetchone(
            "SELECT * FROM packs WHERE id=? AND user_id=? AND (deleted_at IS NULL OR deleted_at = 0)",
            (pack_id, user_id),
        )
        if pack is None:
            raise ValidationFailed("pack")
        if pack["status"] == "processing":
            raise ValidationFailed("pack_processing")
        await _check_owned(t, user_id, [*([params] if params else []), *overrides.values()])

        user = await t.fetchone("SELECT balance, plan FROM users WHERE id=?", (user_id,))
        if user is None:
            raise ValidationFailed("user")
        if any(o.get("edit") for o in overrides.values()) and user["plan"] != "pro":
            raise ProRequired()
        if user["balance"] < cost:
            raise InsufficientBalance(need=cost - user["balance"])

        max_limit = settings.emoji_pack_max if pack["type"] == "emoji" else settings.sticker_pack_max
        current_items = json.loads(pack["items"])
        if len(current_items) + cost > max_limit:
            raise ValidationFailed("too_many_items")

        row = await t.fetchone("SELECT COALESCE(MAX(idx), -1) AS max_idx FROM pack_items WHERE pack_id=?", (pack_id,))
        max_idx = row["max_idx"] if row else -1

        await _apply(t, user_id, -cost, "pack_item_add", str(pack_id))

        new_items_list = list(current_items)
        for offset, tid in enumerate(template_ids):
            idx = max_idx + 1 + offset
            own = {**(params or {}), **overrides.get(tid, {})}  # свои настройки элемента поверх общих
            item_params = json.dumps(own) if own else None
            await t.execute(
                "INSERT INTO pack_items (pack_id, idx, template_id, status, params) VALUES (?,?,?,?,?)",
                (pack_id, idx, tid, "pending", item_params),
            )
            new_items_list.append(tid)

        new_duration = round(3 + cost * 0.7, 1)
        await t.execute(
            "UPDATE packs SET items=?, count=?, status='processing', duration_s=?, error=NULL, created_at=? WHERE id=?",
            (json.dumps(new_items_list), len(new_items_list), new_duration, now(), pack_id),
        )
        updated = await t.fetchone("SELECT * FROM packs WHERE id=?", (pack_id,))
    return pack_view(updated)


async def delete_pack_item(
    db: Database, settings: Settings, user_id: int, pack_id: int, template_id: str, bot=None
) -> dict:
    """Удаляет один стикер/эмодзи из пака пользователя."""
    async with db.tx() as t:
        pack = await t.fetchone(
            "SELECT * FROM packs WHERE id=? AND user_id=? AND (deleted_at IS NULL OR deleted_at = 0)",
            (pack_id, user_id),
        )
        if pack is None:
            raise ValidationFailed("pack")
        if pack["status"] == "processing":
            raise ValidationFailed("pack_processing")

        items = json.loads(pack["items"])
        if template_id not in items:
            raise ValidationFailed("item_not_found")
        if len(items) <= 1:
            raise ValidationFailed("cannot_empty_pack")

        idx_to_remove = items.index(template_id)
        items.remove(template_id)

        await t.execute(
            "DELETE FROM pack_items WHERE pack_id=? AND template_id=?",
            (pack_id, template_id),
        )

        await t.execute(
            "UPDATE packs SET items=?, count=? WHERE id=?",
            (json.dumps(items), len(items), pack_id),
        )
        updated = await t.fetchone("SELECT * FROM packs WHERE id=?", (pack_id,))

    if bot and updated.get("set_name"):
        try:
            s_set = await bot.get_sticker_set(name=updated["set_name"])
            if s_set and idx_to_remove < len(s_set.stickers):
                await bot.delete_sticker_from_set(sticker=s_set.stickers[idx_to_remove].file_id)
        except Exception:
            pass

    return pack_view(updated)


@dataclass
class FinishResult:
    user_id: int
    pack: dict
    reward_user_id: int | None = None
    reward_amount: int = 0
    reward_kind: str = "referral"  # 'referral' — за друга, 'partner' — партнёру за приведённого по его ссылке


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
        reward = await _maybe_reward_referrer(t, settings, uid)
        if reward:
            result.reward_user_id, result.reward_amount = reward
        else:
            from . import partners

            reward = await partners.maybe_reward(t, uid)
            if reward:
                result.reward_user_id, result.reward_amount = reward
                result.reward_kind = "partner"
    return result


async def _maybe_reward_referrer(t: Tx, settings: Settings, uid: int) -> tuple[int, int] | None:
    """Награда пригласившему — один раз, когда приглашённый И собрал пак, И сделал хотя бы одну покупку.

    Только «собрал пак» фармится бесплатно (пак из 1 эмодзи на подарочной генерации с фейкового аккаунта),
    поэтому нужен реальный покупатель. Вызывается и после сборки пака, и после оплаты — что случится позже.
    Если пригласивший упёрся в суточный/общий лимит, награда не сгорает: выдастся при следующем событии."""
    user = await t.fetchone(
        "SELECT referred_by, referral_rewarded, purchased_total FROM users WHERE id=?", (uid,)
    )
    if user is None or not user["referred_by"] or user["referral_rewarded"] == 1 or user["purchased_total"] <= 0:
        return None
    has_pack = await t.fetchone("SELECT 1 FROM packs WHERE user_id=? AND status='ready' LIMIT 1", (uid,))
    if has_pack is None:
        return None
    referrer = user["referred_by"]
    today = await t.fetchone(
        "SELECT COUNT(*) AS n FROM ledger WHERE user_id=? AND reason='referral' AND created_at >= ?",
        (referrer, now() - 86400),
    )
    total = await t.fetchone("SELECT COUNT(*) AS n FROM ledger WHERE user_id=? AND reason='referral'", (referrer,))
    if today["n"] >= settings.referral_daily_cap or total["n"] >= settings.referral_lifetime_cap:
        return None  # лимит: остаётся в ожидании
    await t.execute("UPDATE users SET referral_rewarded=1 WHERE id=?", (uid,))
    if await _apply(t, referrer, settings.referral_reward, "referral", str(uid)):
        return referrer, settings.referral_reward
    return None


async def fail_pack(db: Database, pack_id: int) -> int | None:
    """processing → failed и возврат генераций. Возвращает user_id или None."""
    async with db.tx() as t:
        cur = await t.execute("UPDATE packs SET status='failed' WHERE id=? AND status='processing'", (pack_id,))
        if cur.rowcount == 0:
            return None
        pack = await t.fetchone("SELECT user_id, count FROM packs WHERE id=?", (pack_id,))
        partial = await t.fetchone(
            "SELECT COALESCE(SUM(delta),0) AS n FROM ledger WHERE reason='pack_partial_refund' AND ref=? AND user_id=?",
            (str(pack_id), pack["user_id"]),
        )
        # Частичный возврат мог пройти до сбоя записи результата; полный возврат дополняет его.
        await _apply(t, pack["user_id"], max(0, pack["count"] - partial["n"]), "pack_refund", str(pack_id))
        return pack["user_id"]


async def fail_pack_with_error(db: Database, pack_id: int, error: str) -> int | None:
    """Как fail_pack, но запоминает код причины (показывается пользователю)."""
    async with db.tx() as t:
        await t.execute("UPDATE packs SET error=? WHERE id=? AND status='processing'", (error[:200], pack_id))
    return await fail_pack(db, pack_id)


# ───────────────────────── сборка по элементам (настоящие наборы Telegram) ─────────────────────────

async def build_state(db: Database, pack_id: int) -> tuple[Any, list[Any]] | None:
    """Пак и его элементы по порядку. None — пака нет или он уже не в сборке."""
    pack = await db.fetchone("SELECT * FROM packs WHERE id=?", (pack_id,))
    if pack is None or pack["status"] != "processing":
        return None
    items = await db.fetchall("SELECT * FROM pack_items WHERE pack_id=? ORDER BY idx", (pack_id,))
    return pack, items


async def set_pack_set_name(db: Database, pack_id: int, set_name: str) -> str:
    """Имя набора фиксируется один раз: при повторной сборке используется уже записанное."""
    async with db.tx() as t:
        await t.execute("UPDATE packs SET set_name=? WHERE id=? AND set_name IS NULL", (set_name, pack_id))
        row = await t.fetchone("SELECT set_name FROM packs WHERE id=?", (pack_id,))
    return row["set_name"]


async def mark_items(db: Database, pack_id: int, idxs: list[int], status: str, error: str | None = None) -> None:
    if not idxs:
        return
    async with db.tx() as t:
        for idx in idxs:
            await t.execute("UPDATE pack_items SET status=?, error=? WHERE pack_id=? AND idx=? AND status='pending'",
                            (status, error[:200] if error else None, pack_id, idx))
        await t.execute(
            "UPDATE packs SET done=(SELECT COUNT(*) FROM pack_items WHERE pack_id=? AND status='added'),"
            " failed_items=(SELECT COUNT(*) FROM pack_items WHERE pack_id=? AND status='failed') WHERE id=?",
            (pack_id, pack_id, pack_id),
        )


async def refund_failed_items(db: Database, pack_id: int) -> int:
    """Возвращает генерации за элементы, которые не удалось собрать (один раз на пак). → сколько вернули."""
    async with db.tx() as t:
        pack = await t.fetchone("SELECT user_id FROM packs WHERE id=?", (pack_id,))
        row = await t.fetchone("SELECT COUNT(*) AS n FROM pack_items WHERE pack_id=? AND status='failed'", (pack_id,))
        n = row["n"]
        if pack is None or n == 0:
            return 0
        if not await _apply(t, pack["user_id"], n, "pack_partial_refund", str(pack_id)):
            return 0
    return n


async def processing_pack_ids(db: Database) -> list[int]:
    rows = await db.fetchall("SELECT id FROM packs WHERE status='processing' ORDER BY id")
    return [r["id"] for r in rows]


# ───────────────────────── рефералы ─────────────────────────

async def referral_stats(db: Database, settings: Settings, user_id: int) -> dict:
    invited = await db.fetchone("SELECT COUNT(*) AS n FROM users WHERE referred_by=?", (user_id,))
    created = await db.fetchone(
        "SELECT COUNT(*) AS n FROM users u WHERE u.referred_by=? "
        "AND EXISTS (SELECT 1 FROM packs p WHERE p.user_id=u.id AND p.status='ready')", (user_id,)
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
    clean = {"text": clean_text(params.get("text"), settings.text_max), "color": clean_color(params.get("color")),
             "stroke": clean_color(params.get("stroke"))}
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


# ───────────────────────── скачивание .tgs (веб-редактор) ─────────────────────────

async def charge_export(db: Database, user_id: int, price: int, idem_key: str) -> bool:
    """Списывает цену «скачать .tgs» через ledger. Ключ идемпотентности — часть ref: повтор не списывает второй раз.
    → True, если списали сейчас. Не хватает генераций — InsufficientBalance."""
    if price <= 0:
        return False
    if not IDEM_KEY.match(idem_key):
        raise ValidationFailed("idempotency_key")
    async with db.tx() as t:
        return await _apply(t, user_id, -price, "export", f"{user_id}:{idem_key}")


# ───────────────────────── платежи ─────────────────────────

async def create_payment(
    db: Database,
    user_id: int,
    qty: int,
    stars: int,
    method: str = "stars",
    amount_rub: int = 0,
    external_id: str | None = None,
) -> str:
    payment_id = uuid.uuid4().hex
    async with db.tx() as t:
        await t.execute(
            "INSERT INTO payments (id, user_id, qty, stars, method, amount_rub, external_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (payment_id, user_id, qty, stars, method, amount_rub, external_id, now()),
        )
    return payment_id


async def set_payment_external_id(db: Database, payment_id: str, external_id: str) -> None:
    async with db.tx() as t:
        await t.execute("UPDATE payments SET external_id=? WHERE id=?", (external_id, payment_id))


async def get_payment(db: Database, payment_id: str):
    return await db.fetchone("SELECT * FROM payments WHERE id=?", (payment_id,))


@dataclass
class SettleResult:
    user_id: int
    qty: int
    balance: int
    status: str            # 'credited' | 'needs_review'
    repeat: bool = False   # человек заплатил по тому же счёту ещё раз
    reward_user_id: int | None = None  # первая покупка приглашённого открыла награду пригласившему
    reward_amount: int = 0
    reward_kind: str = "referral"      # или 'partner' — награда владельцу партнёрской ссылки


def _plan_for(settings: Settings, purchased_total: int) -> str:
    return "pro" if purchased_total >= settings.pro_min_purchased else "free"


async def settle_payment(
    db: Database,
    settings: Settings,
    payment_id: str,
    charge_id: str,
    total_amount: int | float,
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
            (provider, charge_id, payment_id, user_id, int(total_amount) if isinstance(total_amount, (int, float)) else 0, currency, now()),
        )
        if cur.rowcount == 0:
            return None

        order = await t.fetchone("SELECT * FROM payments WHERE id=?", (payment_id,))
        user = await t.fetchone("SELECT balance, purchased_total FROM users WHERE id=?", (user_id,))
        valid = False
        if order is not None and user is not None and order["user_id"] == user_id:
            if provider == "stars":
                valid = (currency == "XTR" and int(order["stars"]) == int(total_amount))
            elif provider in ("cryptobot", "xrocket", "dev"):
                valid = True

        if not valid:
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
        await t.execute("UPDATE users SET purchased_total=?, plan=?, discount_pct=0 WHERE id=?",
                        (purchased, _plan_for(settings, purchased), user_id))
        await t.execute(
            "UPDATE payment_events SET status=? WHERE provider=? AND charge_id=?",
            ("credited_repeat" if repeat else "credited", provider, charge_id),
        )
        reward = await _maybe_reward_referrer(t, settings, user_id)
        kind = "referral"
        if not reward:
            from . import partners

            reward, kind = await partners.maybe_reward(t, user_id), "partner"
        balance = (await t.fetchone("SELECT balance FROM users WHERE id=?", (user_id,)))["balance"]
    result = SettleResult(user_id=user_id, qty=order["qty"], balance=balance, status="credited", repeat=repeat)
    if reward:
        result.reward_user_id, result.reward_amount = reward
        result.reward_kind = kind
    return result


async def payment_status(db: Database, user_id: int, payment_id: str) -> str | None:
    """pending | paid — для опроса клиентом после закрытия окна оплаты."""
    row = await db.fetchone("SELECT status FROM payments WHERE id=? AND user_id=?", (payment_id, user_id))
    return row["status"] if row else None


# ───────────────────────── возвраты (вызывает администратор) ─────────────────────────

def _attempt_ref(charge_id: str, attempt: int) -> str:
    # первая попытка — просто charge_id (совместимо с уже записанным журналом), дальше charge_id:N
    return charge_id if attempt <= 1 else f"{charge_id}:{attempt}"


async def _refund_attempts(t: Tx, charge_id: str) -> int:
    prefix = f"{charge_id}:"
    row = await t.fetchone(
        "SELECT COUNT(*) AS n FROM ledger WHERE reason='refund' AND (ref=? OR substr(ref, 1, ?)=?)",
        (charge_id, len(prefix), prefix),
    )
    return row["n"]


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
        # каждая попытка возврата — отдельная запись журнала: после «Telegram отказал → revert» повторный
        # /refund обязан снова снять генерации (UNIQUE(reason, ref) иначе молча проглотил бы вторую запись)
        attempt = await _refund_attempts(t, charge_id) + 1
        try:
            await _apply(t, ev["user_id"], -qty, "refund", _attempt_ref(charge_id, attempt))
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
        attempt = await _refund_attempts(t, charge_id)  # откатываем именно последнюю попытку
        await _apply(t, ev["user_id"], order["qty"], "refund_revert", _attempt_ref(charge_id, attempt))
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
