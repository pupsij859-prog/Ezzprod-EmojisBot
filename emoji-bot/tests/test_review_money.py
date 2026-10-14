"""Ревью-тесты на найденные дыры в деньгах и балансе (второй проход).

Найденные дыры исправлены; тесты фиксируют правильное поведение."""
from __future__ import annotations

import pytest

from app import services
from tests.conftest import make_settings
from tests.test_services import make_user, pack_req, pay


# ───────────────────────── возвраты ─────────────────────────

async def test_refund_after_failed_refund_still_debits_generations(db, tmp_path):
    s = make_settings(tmp_path, signup_gens=0)
    await make_user(db, s, 1)
    _, res = await pay(db, s, 1, 10, 60, "c1")
    assert res.balance == 10

    await services.begin_refund(db, s, "c1")          # генерации сняты
    await services.revert_refund(db, s, "c1")         # Telegram не вернул Stars, генерации вернули
    assert (await services.get_user(db, 1))["balance"] == 10

    await services.begin_refund(db, s, "c1")          # админ повторяет /refund
    # Telegram теперь вернёт Stars, а генерации ДОЛЖНЫ быть сняты. Сейчас баланс остаётся 10:
    assert (await services.get_user(db, 1))["balance"] == 0
    assert await services.ledger_mismatches(db) == []


async def test_refund_revert_twice_does_not_double_credit(db, tmp_path):
    """Подтверждено: повторный revert безопасен (статус уже не 'refunding')."""
    s = make_settings(tmp_path, signup_gens=0)
    await make_user(db, s, 1)
    await pay(db, s, 1, 10, 60, "c1")
    await services.begin_refund(db, s, "c1")
    await services.revert_refund(db, s, "c1")
    await services.revert_refund(db, s, "c1")
    assert (await services.get_user(db, 1))["balance"] == 10


# ───────────────────────── рефералка ─────────────────────────

async def test_referral_reward_not_paid_for_gift_funded_one_item_pack(db, tmp_path):
    """Исправлено (P1-3): пак из одного эмодзи на подарочной генерации без покупки награду не приносит."""
    s = make_settings(tmp_path)  # signup_gens=1, reward=3
    referrer, _ = await make_user(db, s, 1)
    await make_user(db, s, 2, referrer["ref_code"])
    pack, _ = await services.create_pack(db, s, 2, pack_req(s), 0)
    res = await services.finish_pack(db, s, pack["id"], "https://t.me/x")
    assert res.reward_user_id is None
    assert (await services.get_user(db, 1))["balance"] == 1
    assert (await services.get_user(db, 2))["purchased_total"] == 0  # приглашённый ничего не покупал


async def test_referral_chain_of_fakes_gives_no_free_generations(db, tmp_path):
    """Исправлено: цепочка фейковых аккаунтов без единой покупки не даёт ни одной реферальной награды."""
    s = make_settings(tmp_path)
    prev, _ = await make_user(db, s, 1)
    for uid in range(2, 12):
        cur, _ = await make_user(db, s, uid, prev["ref_code"])
        pack, _ = await services.create_pack(db, s, uid, pack_req(s), 0)
        await services.finish_pack(db, s, pack["id"], "https://t.me/x")
        prev = cur
    rewards = await db.fetchone("SELECT COALESCE(SUM(delta),0) AS n FROM ledger WHERE reason='referral'")
    assert rewards["n"] == 0


# ───────────────────────── валидация ─────────────────────────

def test_hex_color_and_idempotency_key_reject_trailing_newline(tmp_path):
    s = make_settings(tmp_path)
    with pytest.raises(services.ValidationFailed):
        services.validate_pack_request(s, {"type": "emoji", "template_ids": ["classic-001"], "color": "#aabbcc\n"})
    assert not services.IDEM_KEY.match("abcdefgh\n")


# ───────────────────────── зависшие паки ─────────────────────────

async def test_stuck_pack_is_picked_up_again_by_recover(db, tmp_path, monkeypatch):
    """Исправлено (P2-1): если запись результата падала 5 раз, пак остаётся processing (генерации не теряются),
    а периодический recover() (PackRunner.watch) доводит его до конца, когда база снова доступна."""
    import asyncio

    from app import packs as packs_mod
    from app.notify import NullNotifier

    s = make_settings(tmp_path)
    await make_user(db, s, 1)
    pack, _ = await services.create_pack(db, s, 1, pack_req(s), 0)
    real_finish = services.finish_pack

    async def boom(*a, **k):
        raise RuntimeError("database is locked")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(services, "finish_pack", boom)
    monkeypatch.setattr(packs_mod.asyncio, "sleep", no_sleep)
    runner = packs_mod.PackRunner(db, s, packs_mod.MockPackBuilder(s), NullNotifier())
    await runner._run(pack["id"], 0)
    assert (await services.get_pack(db, 1, pack["id"]))["status"] == "processing"

    monkeypatch.setattr(services, "finish_pack", real_finish)  # база ожила
    monkeypatch.undo()
    await runner.recover()
    await asyncio.gather(*runner._tasks)
    assert (await services.get_pack(db, 1, pack["id"]))["status"] == "ready"


# ───────────────────────── платежи ─────────────────────────

async def test_settle_payment_with_other_users_invoice_goes_to_review(db, tmp_path):
    """Инвойс-ссылку можно переслать: платит другой человек. Деньги получены, генерации НЕ начислены ни ему,
    ни владельцу заказа; нужен ручной разбор (на pre_checkout это отсекается, но не при ретро-сверке)."""
    s = make_settings(tmp_path, signup_gens=0)
    await make_user(db, s, 1)
    await make_user(db, s, 2)
    pid = await services.create_payment(db, 1, 10, 60)
    res = await services.settle_payment(db, s, pid, "cX", 60, 2)
    assert res.status == "needs_review"
    assert (await services.get_user(db, 1))["balance"] == 0
    assert (await services.get_user(db, 2))["balance"] == 0
    ev = await db.fetchone("SELECT status FROM payment_events WHERE charge_id='cX'")
    assert ev["status"] == "needs_review"
