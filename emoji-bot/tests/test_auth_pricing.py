import time

import pytest

from app import pricing
from app.auth import InitDataError, validate_init_data
from app.config import Settings
from tests.conftest import BOT_TOKEN, make_settings, sign_init_data

USER = {"id": 42, "first_name": "Аня", "username": "anya"}


def test_valid_init_data():
    assert validate_init_data(sign_init_data(USER), BOT_TOKEN)["id"] == 42


def test_tampered_init_data_rejected():
    data = sign_init_data(USER).replace("%22id%22%3A42", "%22id%22%3A43")
    with pytest.raises(InitDataError):
        validate_init_data(data, BOT_TOKEN)


def test_wrong_token_rejected():
    with pytest.raises(InitDataError):
        validate_init_data(sign_init_data(USER), "999:OTHER")


def test_expired_and_future_init_data_rejected():
    with pytest.raises(InitDataError):
        validate_init_data(sign_init_data(USER, auth_date=int(time.time()) - 3 * 86400), BOT_TOKEN)
    with pytest.raises(InitDataError):
        validate_init_data(sign_init_data(USER, auth_date=int(time.time()) + 3600), BOT_TOKEN)


def test_missing_hash_rejected():
    with pytest.raises(InitDataError):
        validate_init_data("auth_date=1&user=%7B%7D", BOT_TOKEN)


def test_start_param_is_whitelisted():
    ok = validate_init_data(sign_init_data(USER, start_param="ref_0123abcd"), BOT_TOKEN)
    assert ok["_start_param"] == "ref_0123abcd"
    bad = validate_init_data(sign_init_data(USER, start_param="<script>alert(1)</script>"), BOT_TOKEN)
    assert "_start_param" not in bad


def test_pricing_tiers(tmp_path):
    s = make_settings(tmp_path)  # 6 ⭐ база, −10% от 30, 5 ⭐ от 50
    assert pricing.quote(s, 1).total == 5
    assert pricing.quote(s, 20).total == 100
    assert pricing.quote(s, 30).total == 135          # 150 − 10%
    assert pricing.quote(s, 50).total == 200          # по 4 ₽
    assert pricing.quote(s, 50).discount_pct == 20
    assert pricing.quote(s, 20).stars == 80           # 100 ₽ / 1,25
    assert pricing.quote(s, 1).stars == 4             # 5 ₽ / 1,25
    with pytest.raises(ValueError):
        pricing.quote(s, 0)


def test_pricing_is_monotonic(tmp_path):
    """Больше штук никогда не стоит дешевле меньшего количества (иначе выгоднее брать «с запасом»)."""
    s = make_settings(tmp_path)
    totals = [pricing.quote(s, q).total for q in range(1, s.max_buy_qty + 1)]
    assert all(a <= b for a, b in zip(totals, totals[1:]))
    # там, где «сырая» цена ломала монотонность, действует цена большего пакета
    assert pricing.quote(s, 29).total == 135 and pricing.quote(s, 49).total == 200


def test_pricing_table_matches_quote(tmp_path):
    s = make_settings(tmp_path)
    table = pricing.pricing_table(s)
    assert len(table["totals"]) == s.max_buy_qty + 1
    for qty in (1, 7, 29, 30, 49, 50, 123, s.max_buy_qty):
        assert table["totals"][qty] == pricing.quote(s, qty).total


def test_dev_mode_refuses_public_url(tmp_path):
    with pytest.raises(SystemExit):
        Settings(dev_mode=True, webapp_url="https://abc.trycloudflare.com", data_dir=tmp_path).validate()
    with pytest.raises(SystemExit):
        Settings(dev_mode=True, host="0.0.0.0", data_dir=tmp_path).validate()
    Settings(dev_mode=True, webapp_url="http://localhost:8000", data_dir=tmp_path).validate()  # локально можно
    Settings(dev_mode=False, webapp_url="https://abc.trycloudflare.com", data_dir=tmp_path).validate()
