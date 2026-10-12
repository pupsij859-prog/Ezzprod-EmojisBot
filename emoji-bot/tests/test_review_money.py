"""Ревью-тесты на найденные дыры в деньгах и балансе (второй проход).

Тесты, помеченные xfail(strict=True), описывают ЕЩЁ НЕ исправленные дыры: пока дыра есть, они «ожидаемо падают»,
а когда её починят, strict-режим заставит снять метку. Остальные тесты фиксируют подтверждённое поведение."""
from __future__ import annotations

import pytest

from app import services
from tests.conftest import make_settings
from tests.test_services import make_user, pack_req, pay


# ───────────────────────── возвраты ─────────────────────────

@pytest.mark.xfail(strict=True, reason="P0: refund -> revert -> refund: второй begin_refund не снимает генерации "
                                       "(ledger UNIQUE(reason, ref) молча отбрасывает вторую запись refund)")
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

async def test_referral_reward_is_paid_for_gift_funded_one_item_pack(db, tmp_path):
    """Подтверждено (P1, остаток P0-9): награда выдаётся за первый пак из ОДНОГО эмодзи на подарочной генерации,
    то есть никаких денег приглашённый не платил. Цепочка фейковых аккаунтов даёт бесплатные генерации."""
    s = make_settings(tmp_path)  # signup_gens=1, reward=3
    referrer, _ = await make_user(db, s, 1)
    friend, _ = await make_user(db, s, 2, referrer["ref_code"])
    pack, _ = await services.create_pack(db, s, 2, pack_req(s), 0)
    res = await services.finish_pack(db, s, pack["id"], "https://t.me/x")
    assert res.reward_user_id == 1 and res.reward_amount == 3
    assert (await services.get_user(db, 1))["balance"] == 1 + 3
    assert (await services.get_user(db, 2))["purchased_total"] == 0  # приглашённый ничего не покупал


async def test_referral_chain_multiplies_free_generations(db, tmp_path):
    """Каждый фейк-аккаунт и рефереры, и приглашённые: из N аккаунтов получаем ~4*N бесплатных генераций."""
    s = make_settings(tmp_path)
    prev, _ = await make_user(db, s, 1)
    total_free = 1
    for uid in range(2, 12):
        cur, _ = await make_user(db, s, uid, prev["ref_code"])
        total_free += 1
        pack, _ = await services.create_pack(db, s, uid, pack_req(s), 0)
        await services.finish_pack(db, s, pack["id"], "https://t.me/x")
        prev = cur
    # 10 фейков: 10 подарков + 10*3 наград, у реальных денег 0
    mine = await db.fetchone("SELECT COALESCE(SUM(delta),0) AS n FROM ledger WHERE reason IN ('signup','referral')")
    assert mine["n"] == 1 + 10 * 1 + 10 * 3 - 0
    assert total_free == 11


# ───────────────────────── валидация ─────────────────────────

@pytest.mark.xfail(strict=True, reason="P2: re.match с '$' пропускает завершающий перевод строки в цвете/ключе")
def test_hex_color_and_idempotency_key_reject_trailing_newline(tmp_path):
    s = make_settings(tmp_path)
    with pytest.raises(services.ValidationFailed):
        services.validate_pack_request(s, {"type": "emoji", "template_ids": ["classic-001"], "fill": "#aabbcc\n"})
    assert not services.IDEM_KEY.match("abcdefgh\n")


# ───────────────────────── зависшие паки ─────────────────────────

async def test_pack_stays_processing_forever_if_finish_keeps_failing(db, tmp_path, monkeypatch):
    """Подтверждено (P2): после 5 неудачных finish_pack пак остаётся processing до перезапуска процесса,
    а progress в API навсегда 0.99 (recover() вызывается только на старте)."""
    from app import packs as packs_mod
    from app.notify import NullNotifier

    s = make_settings(tmp_path)
    await make_user(db, s, 1)
    pack, _ = await services.create_pack(db, s, 1, pack_req(s), 0)

    async def boom(*a, **k):
        raise RuntimeError("database is locked")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(services, "finish_pack", boom)
    monkeypatch.setattr(packs_mod.asyncio, "sleep", no_sleep)
    runner = packs_mod.PackRunner(db, s, packs_mod.MockPackBuilder(s), NullNotifier())
    await runner._run(pack["id"], 0)
    row = await services.get_pack(db, 1, pack["id"])
    assert row["status"] == "processing"
    assert services.pack_view(row)["progress"] == 0.99


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
