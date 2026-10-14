import pytest

from app import services
from tests.conftest import make_settings


def tg(uid, name="u"):
    return {"id": uid, "first_name": name, "username": f"{name}{uid}"}


async def make_user(db, settings, uid, ref=None):
    row, created = await services.upsert_user(db, settings, tg(uid), ref)
    return row, created


def pack_req(settings, ids=("classic-001",), kind="emoji"):
    return services.validate_pack_request(settings, {"type": kind, "template_ids": list(ids), "text": "ez"})


async def new_pack(db, settings, uid, ids=("classic-001",), idem=None):
    pack, _ = await services.create_pack(db, settings, uid, pack_req(settings, ids), 0, idem)
    return pack


async def pay(db, settings, uid, qty, stars, charge):
    pid = await services.create_payment(db, uid, qty, stars)
    return pid, await services.settle_payment(db, settings, pid, charge, stars, uid)


async def test_signup_gift_and_idempotent_upsert(db, settings):
    row, created = await make_user(db, settings, 1)
    assert created and row["balance"] == 1 and row["trial_exports_left"] == 1 and not row["onboarded"]
    row, created = await make_user(db, settings, 1)
    assert not created and row["balance"] == 1  # подарок не начисляется повторно


async def test_referral_binds_only_for_new_users_and_not_self(db, settings):
    a, _ = await make_user(db, settings, 1)
    b, _ = await make_user(db, settings, 2, a["ref_code"])
    assert b["referred_by"] == 1
    again, created = await make_user(db, settings, 1, b["ref_code"])  # существующий не может стать рефералом
    assert not created and again["referred_by"] is None
    c, _ = await make_user(db, settings, 3, "deadbeef")
    assert c["referred_by"] is None


async def test_read_does_not_write_when_nothing_changed(db, settings):
    await make_user(db, settings, 1)
    before = db.conn.total_changes  # счётчик записанных строк на этом соединении
    for _ in range(3):
        await make_user(db, settings, 1)
    assert db.conn.total_changes == before


async def test_create_pack_debits_and_blocks_overdraft(db, settings):
    await make_user(db, settings, 1)
    with pytest.raises(services.InsufficientBalance) as exc:
        await new_pack(db, settings, 1, ["classic-001", "classic-002", "classic-003"])
    assert exc.value.need == 2
    assert (await services.get_user(db, 1))["balance"] == 1  # ничего не списалось
    pack = await new_pack(db, settings, 1)
    assert pack["status"] == "processing" and (await services.get_user(db, 1))["balance"] == 0


async def test_create_pack_is_idempotent_by_key(db, settings):
    await make_user(db, settings, 1)
    first, created = await services.create_pack(db, settings, 1, pack_req(settings), 0, "key-12345678")
    again, created2 = await services.create_pack(db, settings, 1, pack_req(settings), 0, "key-12345678")
    assert created and not created2 and first["id"] == again["id"]
    assert (await services.get_user(db, 1))["balance"] == 0  # списано один раз
    assert len(await services.list_packs(db, 1)) == 1
    with pytest.raises(services.ValidationFailed):
        await services.create_pack(db, settings, 1, pack_req(settings), 0, "short")


async def test_referral_reward_needs_pack_and_purchase_and_is_paid_once(db, settings):
    a, _ = await make_user(db, settings, 1)
    await make_user(db, settings, 2, a["ref_code"])
    p1 = await new_pack(db, settings, 2)
    done = await services.finish_pack(db, settings, p1["id"], "https://t.me/x")
    assert done.reward_user_id is None                      # пак на подарочной генерации — награды нет
    assert (await services.get_user(db, 2))["trial_exports_left"] == 0
    assert await services.finish_pack(db, settings, p1["id"], "https://t.me/x") is None  # повтор — no-op

    _, res = await pay(db, settings, 2, 10, 60, "c-friend")  # первая покупка друга открывает награду
    assert (res.reward_user_id, res.reward_amount) == (1, 3)
    assert (await services.get_user(db, 1))["balance"] == 1 + 3
    _, again = await pay(db, settings, 2, 10, 60, "c-friend-2")
    assert again.reward_user_id is None                      # только один раз
    stats = await services.referral_stats(db, settings, 1)
    assert stats == {"invited": 1, "created_pack": 1, "earned": 3, "reward": 3}


async def test_referral_reward_when_purchase_comes_first(db, settings):
    a, _ = await make_user(db, settings, 1)
    await make_user(db, settings, 2, a["ref_code"])
    _, res = await pay(db, settings, 2, 10, 60, "c1")
    assert res.reward_user_id is None                        # пака ещё нет
    p = await new_pack(db, settings, 2)
    done = await services.finish_pack(db, settings, p["id"], "x")
    assert (done.reward_user_id, done.reward_amount) == (1, 3)


async def _paying_friend(db, settings, uid, ref_code):
    await make_user(db, settings, uid, ref_code)
    pack = await new_pack(db, settings, uid)
    await services.finish_pack(db, settings, pack["id"], "x")
    _, res = await pay(db, settings, uid, 1, 6, f"c{uid}")
    return res


async def test_referral_caps_and_pending(db, tmp_path):
    settings = make_settings(tmp_path, referral_daily_cap=2, referral_lifetime_cap=100)
    boss, _ = await make_user(db, settings, 1)
    rewarded = 0
    for uid in range(10, 15):
        res = await _paying_friend(db, settings, uid, boss["ref_code"])
        rewarded += 1 if res.reward_user_id else 0
    assert rewarded == 2  # суточный лимит
    assert (await services.referral_stats(db, settings, 1))["created_pack"] == 5
    # упёрлись в лимит — награда не сгорела: остаётся в ожидании (referral_rewarded=0)
    pending = await db.fetchone("SELECT COUNT(*) AS n FROM users WHERE referred_by=1 AND referral_rewarded=0")
    assert pending["n"] == 3

    life = make_settings(tmp_path / "life", referral_daily_cap=100, referral_lifetime_cap=3)
    from app.db import Database

    db2 = Database(life.db_path)
    await db2.connect()
    try:
        boss2, _ = await make_user(db2, life, 1)
        got = 0
        for uid in range(20, 26):
            res = await _paying_friend(db2, life, uid, boss2["ref_code"])
            got += 1 if res.reward_user_id else 0
        assert got == 3  # пожизненный лимит
    finally:
        await db2.close()


async def test_fail_pack_refunds_once(db, settings):
    await make_user(db, settings, 1)
    pack = await new_pack(db, settings, 1)
    assert await services.fail_pack(db, pack["id"]) == 1
    assert (await services.get_user(db, 1))["balance"] == 1
    assert await services.fail_pack(db, pack["id"]) is None
    assert (await services.get_user(db, 1))["balance"] == 1


# ───────────── платежи ─────────────

async def test_payment_credits_once_per_charge(db, settings):
    await make_user(db, settings, 1)
    pid, res = await pay(db, settings, 1, 20, 120, "charge-1")
    assert res.status == "credited" and res.balance == 21 and not res.repeat
    # повторная доставка того же апдейта — no-op
    assert await services.settle_payment(db, settings, pid, "charge-1", 120, 1) is None
    assert (await services.get_user(db, 1))["balance"] == 21
    assert await services.payment_status(db, 1, pid) == "paid"
    assert await services.payment_status(db, 2, pid) is None  # чужой платёж не виден


async def test_second_payment_on_same_invoice_is_credited_not_lost(db, settings):
    """Ссылку на оплату можно оплатить дважды: деньги получены дважды → генерации тоже дважды."""
    await make_user(db, settings, 1)
    pid, first = await pay(db, settings, 1, 20, 120, "charge-1")
    second = await services.settle_payment(db, settings, pid, "charge-2", 120, 1)
    assert second.status == "credited" and second.repeat
    assert (await services.get_user(db, 1))["balance"] == 1 + 20 + 20


async def test_mismatched_payment_goes_to_review_without_crash(db, settings):
    await make_user(db, settings, 1)
    await make_user(db, settings, 2)
    pid = await services.create_payment(db, 1, 20, 120)
    for charge, uid, amount in (("c1", 1, 119), ("c2", 2, 120)):
        res = await services.settle_payment(db, settings, pid, charge, amount, uid)
        assert res.status == "needs_review" and res.qty == 0
    unknown = await services.settle_payment(db, settings, "nope", "c3", 120, 1)
    assert unknown.status == "needs_review"
    assert (await services.get_user(db, 1))["balance"] == 1
    # событие осталось в журнале: деньги не «потерялись без следа»
    rows = await db.fetchall("SELECT status FROM payment_events ORDER BY id")
    assert [r["status"] for r in rows] == ["needs_review"] * 3
    # и повторная доставка не дублируется
    assert await services.settle_payment(db, settings, pid, "c1", 119, 1) is None


async def test_pro_unlocks_by_total_purchased(db, tmp_path):
    settings = make_settings(tmp_path, pro_min_purchased=20)
    await make_user(db, settings, 1)
    await pay(db, settings, 1, 10, 60, "c1")
    assert (await services.get_user(db, 1))["plan"] == "free"
    with pytest.raises(services.ProRequired):
        await services.add_style(db, settings, 1, "мой", {"text": "ez"})
    await pay(db, settings, 1, 10, 60, "c2")
    assert (await services.get_user(db, 1))["plan"] == "pro"
    style = await services.add_style(db, settings, 1, "мой", {"text": "ez", "color": "#112233"})
    assert (await services.list_styles(db, 1))[0]["id"] == style["id"]
    assert await services.delete_style(db, 1, style["id"]) and not await services.delete_style(db, 1, style["id"])


async def test_refund_flow(db, tmp_path):
    settings = make_settings(tmp_path, pro_min_purchased=20)
    await make_user(db, settings, 1)
    await pay(db, settings, 1, 20, 120, "c1")
    user_id, qty = await services.begin_refund(db, settings, "c1")
    assert (user_id, qty) == (1, 20)
    u = await services.get_user(db, 1)
    assert u["balance"] == 1 and u["plan"] == "free" and u["purchased_total"] == 0
    await services.finish_refund(db, "c1")
    with pytest.raises(services.RefundError):  # повторный возврат того же платежа
        await services.begin_refund(db, settings, "c1")


async def test_refund_revert_restores_balance(db, tmp_path):
    settings = make_settings(tmp_path, pro_min_purchased=20)
    await make_user(db, settings, 1)
    await pay(db, settings, 1, 20, 120, "c1")
    await services.begin_refund(db, settings, "c1")
    await services.revert_refund(db, settings, "c1")  # Telegram отказал
    u = await services.get_user(db, 1)
    assert u["balance"] == 21 and u["plan"] == "pro" and u["purchased_total"] == 20


async def test_refund_blocked_when_gens_are_spent(db, settings):
    await make_user(db, settings, 1)
    await pay(db, settings, 1, 2, 12, "c1")
    await new_pack(db, settings, 1, ["classic-001", "classic-002", "classic-003"])  # потратили всё
    with pytest.raises(services.RefundError) as exc:
        await services.begin_refund(db, settings, "c1")
    assert str(exc.value) == "spent"
    with pytest.raises(services.RefundError):
        await services.begin_refund(db, settings, "unknown")


async def test_ledger_always_matches_balance(db, settings):
    a, _ = await make_user(db, settings, 1)
    await make_user(db, settings, 2, a["ref_code"])
    await pay(db, settings, 1, 20, 120, "c1")
    p = await new_pack(db, settings, 2)
    await services.finish_pack(db, settings, p["id"], "x")          # награда рефереру
    p2 = await new_pack(db, settings, 1, ["classic-001", "classic-002"])
    await services.fail_pack(db, p2["id"])                           # возврат генераций
    await services.begin_refund(db, settings, "c1")
    assert await services.ledger_mismatches(db) == []


async def test_validation_rules(settings):
    v = services.validate_pack_request
    with pytest.raises(services.ValidationFailed):
        v(settings, {"type": "gif", "template_ids": ["classic-001"]})
    with pytest.raises(services.ValidationFailed):
        v(settings, {"type": "emoji", "template_ids": ["nope-1"]})
    with pytest.raises(services.ValidationFailed):
        v(settings, {"type": "emoji", "template_ids": ["classic-001"], "color": "red"})
    with pytest.raises(services.ValidationFailed):
        v(settings, {"type": "sticker", "template_ids": [f"classic-{i:03d}" for i in range(1, 122)]})  # > 120
    ok = v(settings, {"type": "emoji", "template_ids": ["classic-001", "classic-001"], "text": "  a‮b￾\n "})
    assert ok["template_ids"] == ["classic-001"] and ok["text"] == "ab"
