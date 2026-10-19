"""Партнёрские ссылки (app/partners.py): привязка, бонус новичку, награда партнёру, лимиты, статистика, админка."""
from __future__ import annotations

import pytest

from app import partners, services
from tests.test_admin_chat import ADMIN, callback, env, texts_sent  # noqa: F401  (env — фикстура)
from tests.test_bot import command, message_update
from tests.test_services import make_user, new_pack, pay, tg

OWNER = 50


async def link(db, **kw) -> str:
    d = partners.Draft(name="Вася · TikTok", **{"owner_id": OWNER, **kw})
    return await partners.create(db, d, ADMIN)


async def join(db, settings, uid, code, ref=None):
    row, created = await services.upsert_user(db, settings, tg(uid), ref, code)
    return row, created


async def balance(db, uid) -> int:
    return (await services.get_user(db, uid))["balance"]


async def test_bonus_new_and_reward_on_signup(db, settings):
    await make_user(db, settings, OWNER)
    code = await link(db, code="vasya", bonus_new=5, reward=2, reward_on="signup")
    row, created = await join(db, settings, 1, code)
    assert created and row["partner"] == "vasya"
    assert await balance(db, 1) == settings.signup_gens + 5          # обычный подарок + бонус по ссылке
    assert await balance(db, OWNER) == settings.signup_gens + 2      # партнёру сразу за регистрацию
    assert await partners.signup_reward(db, 1) == (OWNER, 2)
    row, created = await join(db, settings, 1, code)                 # повторный вход ничего не даёт
    assert not created and await balance(db, OWNER) == settings.signup_gens + 2


async def test_reward_on_pack_and_purchase(db, settings):
    await make_user(db, settings, OWNER)
    on_pack = await link(db, code="pak", reward=3, reward_on="pack")
    on_buy = await link(db, code="buy", reward=4, reward_on="purchase")
    await join(db, settings, 1, on_pack)
    await join(db, settings, 2, on_buy)
    base = await balance(db, OWNER)
    assert await partners.signup_reward(db, 1) is None
    done = await services.finish_pack(db, settings, (await new_pack(db, settings, 1))["id"], "x")
    assert (done.reward_user_id, done.reward_amount, done.reward_kind) == (OWNER, 3, "partner")
    done2 = await services.finish_pack(db, settings, (await new_pack(db, settings, 2))["id"], "x")
    assert done2.reward_user_id is None                               # этому — только за покупку
    _, res = await pay(db, settings, 2, 10, 60, "c-buy")
    assert (res.reward_user_id, res.reward_amount, res.reward_kind) == (OWNER, 4, "partner")
    _, again = await pay(db, settings, 2, 10, 60, "c-buy-2")
    assert again.reward_user_id is None                               # один раз на человека
    assert await balance(db, OWNER) == base + 3 + 4


async def test_limits_pause_expiry_and_referral_priority(db, settings):
    await make_user(db, settings, OWNER)
    code = await link(db, code="lim", bonus_new=5, uses_max=1, reward=1, reward_on="signup", reward_cap=2)
    for uid in (1, 2, 3):
        await join(db, settings, uid, code)
    assert [await balance(db, u) for u in (1, 2, 3)] == [settings.signup_gens + 5] + [settings.signup_gens] * 2
    st = await partners.stats(db, code)
    assert (st["joined"], st["bonus_users"], st["rewards"], st["pending"]) == (3, 1, 2, 1)  # третий ждёт лимит

    await partners.set_active(db, code, False)                        # пауза: учёт есть, бонусов нет
    row, _ = await join(db, settings, 4, code)
    assert row["partner"] == code and row["partner_rewarded"] == 2 and await balance(db, 4) == settings.signup_gens

    a, _ = await make_user(db, settings, 70)                          # обычная реферальная ссылка важнее
    row, _ = await join(db, settings, 5, "lim", ref=a["ref_code"])
    assert row["referred_by"] == 70 and row["partner"] is None

    exp = await link(db, code="old", bonus_new=3, days=1)
    async with db.tx() as t:
        await t.execute("UPDATE partner_links SET expires_at=? WHERE code=?", (services.now() - 1, "old"))
    row, _ = await join(db, settings, 6, exp)
    assert row["partner"] == "old" and await balance(db, 6) == settings.signup_gens
    assert partners.state(await partners.get(db, "old")) == "expired"


async def test_stats_count_buyers_and_revenue(db, settings):
    code = await link(db, code="stat", owner_id=0)
    await join(db, settings, 1, code)
    await join(db, settings, 2, code)
    await make_user(db, settings, 3)                                  # не по ссылке — не считается
    await services.finish_pack(db, settings, (await new_pack(db, settings, 1))["id"], "x")
    await pay(db, settings, 1, 10, 60, "s1")
    await pay(db, settings, 3, 10, 60, "s3")
    st = await partners.stats(db, code)
    assert (st["joined"], st["active"], st["buyers"], st["buys"], st["qty"], st["stars"]) == (2, 1, 1, 1, 10, 60)


def test_draft_validation():
    with pytest.raises(ValueError):
        partners.Draft(name="x", reward=2).check()                   # награда без партнёра
    partners.Draft(name="", reward=2).check(final=False)               # по ходу заполнения — можно
    with pytest.raises(ValueError):
        partners.clean_code("Не латиница")
    assert partners.clean_code("Vasya-TT") == "vasya_tt"
    assert partners.code_from_param("p_vasya") == "vasya" and partners.code_from_param("ref_abcdef12") is None


async def test_start_with_partner_link_counts_click_and_notifies(env):
    dp, bot, ctx, session = env
    await services.upsert_user(ctx.db, ctx.settings, tg(OWNER))
    code = await link(ctx.db, code="blog", bonus_new=2, reward=1, reward_on="signup")
    await dp.feed_update(bot, command(301, f"/start p_{code}"))
    await dp.feed_update(bot, command(301, f"/start p_{code}"))          # повторный старт — ещё переход
    await dp.feed_update(bot, command(302, "/start p_nosuchlink"))      # неизвестная ссылка — просто старт
    r = await partners.get(ctx.db, code)
    assert r["clicks"] == 2
    assert (await services.get_user(ctx.db, 301))["partner"] == code
    assert (await services.get_user(ctx.db, 302))["partner"] is None
    assert any("по твоей ссылке" in t for t in texts_sent(session))  # партнёру пришло уведомление
    session.calls.clear()
    await dp.feed_update(bot, command(OWNER, "/partner"))
    assert any("пришло: <b>1</b>" in t for t in texts_sent(session))


async def test_admin_creates_link_and_sees_card(env):
    dp, bot, ctx, session = env
    await dp.feed_update(bot, callback(ADMIN, "a:lk"))
    assert any("Партнёрские ссылки" in t for t in texts_sent(session))
    await dp.feed_update(bot, callback(ADMIN, "a:lkn"))
    for field, value in (("name", "Канал Пети"), ("code", "petya"), ("owner", "5"), ("bonus", "3"), ("reward", "2")):
        await dp.feed_update(bot, callback(ADMIN, f"a:lkf:{field}"))
        await dp.feed_update(bot, message_update(ADMIN, text=value))
    await dp.feed_update(bot, callback(ADMIN, "a:lkt"))                 # условие: регистрация → пак → покупка
    session.calls.clear()
    await dp.feed_update(bot, callback(ADMIN, "a:lkok"))
    r = await partners.get(ctx.db, "petya")
    assert (r["name"], r["owner_id"], r["bonus_new"], r["reward"], r["reward_on"]) == ("Канал Пети", 5, 3, 2, "purchase")
    card = "\n".join(texts_sent(session))
    assert "https://t.me/ezzbot?start=p_petya" in card and "Статистика" in card
    # пауза и удаление
    await dp.feed_update(bot, callback(ADMIN, "a:lkp:petya"))
    assert not (await partners.get(ctx.db, "petya"))["active"]
    await dp.feed_update(bot, callback(ADMIN, "a:lkz:petya"))
    assert await partners.get(ctx.db, "petya") is None


async def test_promo_delete_button_is_not_eaten_by_refund(env):
    dp, bot, ctx, session = env
    from app import runtime

    code = await runtime.create_promo(ctx.db, "DELME", 1, 5, ADMIN)
    session.calls.clear()
    await dp.feed_update(bot, callback(ADMIN, f"a:prz:{code}"))
    assert any("Удалить промокод" in t for t in texts_sent(session))
