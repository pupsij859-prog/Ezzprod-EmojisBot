"""Веб-редактор на сайте: вход через Telegram Login Widget, сессии, CORS, превью своего .tgs и скачивание."""
import gzip
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import catalog
from app.main import create_app
from app.web_login import LoginError, validate_login
from tests.conftest import BOT_TOKEN, dev, make_settings, sign_init_data

SITE = "https://ezzprod.example"


def sign_login(user: dict, bot_token: str = BOT_TOKEN, auth_date: int | None = None) -> dict:
    fields = {**user, "auth_date": auth_date if auth_date is not None else int(time.time())}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hashlib.sha256(bot_token.encode()).digest()
    return {**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()}


USER = {"id": 777, "first_name": "Аня", "username": "anya", "photo_url": "https://t.me/i/userpic/320/a.jpg"}


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def web(tmp_path):
    # без DEV_MODE: проверяем настоящий путь входа, а не обход подписи
    app = create_app(make_settings(tmp_path, dev_mode=False, web_origins=(SITE,)), with_bot=False)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c


def test_login_widget_signature():
    assert validate_login(sign_login(USER), BOT_TOKEN)["id"] == 777
    with pytest.raises(LoginError):  # подделан id
        validate_login({**sign_login(USER), "id": 778}, BOT_TOKEN)
    with pytest.raises(LoginError):  # чужой бот
        validate_login(sign_login(USER, bot_token="999:OTHER"), BOT_TOKEN)
    with pytest.raises(LoginError):  # старше суток
        validate_login(sign_login(USER, auth_date=int(time.time()) - 2 * 86400), BOT_TOKEN)
    with pytest.raises(LoginError):  # лишнее поле не подписано виджетом
        validate_login({**sign_login(USER), "admin": "1"}, BOT_TOKEN)
    with pytest.raises(LoginError):  # подпись Mini App (WebAppData) — не подпись виджета
        validate_login({"hash": "0" * 64, "id": 777, "auth_date": int(time.time())}, BOT_TOKEN)


def test_login_gives_same_account_as_bot(web):
    res = web.post("/api/web/login", json=sign_login(USER))
    assert res.status_code == 200
    token = res.json()["token"]
    me = web.get("/api/me", headers=bearer(token)).json()
    assert me["id"] == 777 and me["username"] == "anya" and me["balance"] == 1  # подарок новичку — один раз
    # тот же человек из Mini App — тот же аккаунт и баланс
    tma = {"Authorization": f"tma {sign_init_data({'id': 777, 'first_name': 'Аня', 'username': 'anya'})}"}
    assert web.get("/api/me", headers=tma).json()["balance"] == 1
    assert web.post("/api/web/login", json=sign_login(USER)).status_code == 200
    assert web.get("/api/me", headers=bearer(token)).json()["balance"] == 1


def test_bad_login_and_sessions(web):
    assert web.post("/api/web/login", json={**sign_login(USER), "id": 1}).status_code == 401
    assert web.get("/api/me", headers=bearer("nope")).status_code == 401
    token = web.post("/api/web/login", json=sign_login(USER)).json()["token"]
    assert web.post("/api/web/logout", headers=bearer(token)).status_code == 200
    assert web.get("/api/me", headers=bearer(token)).status_code == 401


def test_whoami_for_music_server(web):
    assert web.get("/api/web/whoami").status_code == 401
    assert web.get("/api/web/whoami", headers=bearer("nope")).status_code == 401
    token = web.post("/api/web/login", json=sign_login(USER)).json()["token"]
    assert web.get("/api/web/whoami", headers=bearer(token)).json() == {"id": 777}


def test_cors_only_for_site(web):
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,idempotency-key"}
    ok = web.options("/api/packs", headers={"Origin": SITE, **pre})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == SITE
    bad = web.options("/api/packs", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in bad.headers
    assert web.get("/api/web/config", headers={"Origin": SITE}).headers["access-control-allow-origin"] == SITE
    assert "access-control-allow-origin" not in web.get("/api/web/config", headers={"Origin": "https://evil.example"}).headers


def test_no_cors_without_setting(client):
    res = client.get("/api/web/config", headers={"Origin": SITE})
    assert "access-control-allow-origin" not in res.headers


def test_config_has_bot_id(web):
    cfg = web.get("/api/web/config").json()
    assert cfg["bot_id"] == 123456 and cfg["bot_username"] == "ezz_test_bot"


def _tid() -> str:
    return catalog.page("classic", 1)["items"][0]["id"]


def test_render_own_file_and_template(client):
    h = dev(5001)
    tpl = catalog.get(_tid())
    res = client.post("/api/web/render", data={"params": json.dumps({"text": "anya"})},
                      files={"file": ("my.tgs", tpl.path.read_bytes(), "application/octet-stream")}, headers=h)
    assert res.status_code == 200
    body = res.json()
    assert body["anim"]["layers"] and body["parts"] and body["checks"]
    assert client.post("/api/web/render", data={"params": json.dumps({"template_id": _tid()})}, headers=h).status_code == 200
    # правки частей и эффекты — только Pro, как в паке
    edit = {"fx": {"metal": {"on": True}}}
    res = client.post("/api/web/render", data={"params": json.dumps({"template_id": _tid(), "edit": edit})}, headers=h)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "pro_required"
    bomb = client.post("/api/web/render", files={"file": ("x.tgs", gzip.compress(b"{" * 3_000_000), "x")}, headers=h)
    assert bomb.status_code in (413, 422)
    assert client.post("/api/web/render", files={"file": ("x.tgs", b"not json", "x")}, headers=h).status_code == 422


def test_export_charges_once(client):
    h = dev(5002)
    client.get("/api/me", headers=h)  # 1 генерация в подарок
    form = {"params": json.dumps({"template_id": _tid(), "text": "anya"})}
    assert client.post("/api/web/export", data=form, headers=h).status_code == 422  # нужен Idempotency-Key
    res = client.post("/api/web/export", data=form, headers={**h, "Idempotency-Key": "key-0001"})
    assert res.status_code == 200 and res.content[:2] == b"\x1f\x8b"
    anim = json.loads(gzip.decompress(res.content))
    assert not any(str(L.get("ln", "")).startswith("ezp-") for L in anim["layers"])  # без меток превью
    assert client.get("/api/me", headers=h).json()["balance"] == 0
    again = client.post("/api/web/export", data=form, headers={**h, "Idempotency-Key": "key-0001"})
    assert again.status_code == 200 and client.get("/api/me", headers=h).json()["balance"] == 0
    broke = client.post("/api/web/export", data=form, headers={**h, "Idempotency-Key": "key-0002"})
    assert broke.status_code == 402 and broke.json()["detail"]["code"] == "insufficient_balance"


def test_added_square_is_a_part_in_preview_and_pack():
    from app import editor, lottie

    edit = editor.clean_edit({"add": [{"kind": "square", "fill": "#FF0000", "round": 20}],
                              "parts": {"r.9001": {"x": 40, "s": 150}}})
    assert edit["add"] == [{"kind": "square", "fill": "#ff0000", "round": 20}]
    base = lottie.load(catalog.get(_tid()).path)
    out, _data = editor.render(base, edit)  # так собирает пак (app/build/render.py)
    assert any(L.get("ind") == 9001 for L in out["layers"])
    shown = editor.parts(editor.add_shapes(json.loads(json.dumps(base)), edit))
    assert shown[0]["id"] == "r.9001" and shown[0]["color"] == "#ff0000"
    with pytest.raises(ValueError):
        editor.clean_edit({"add": [{"kind": "circle"}]})


def test_dev_web_login(client):
    token = client.post("/api/dev/web-login", json={"id": 9001}).json()["token"]
    assert client.get("/api/me", headers=bearer(token)).json()["id"] == 9001


def test_stickerset_needs_bot(client):
    res = client.get("/api/web/stickerset", params={"link": "https://t.me/addemoji/SomePack"}, headers=dev())
    assert res.status_code == 503
    bad = client.get("/api/web/stickerset", params={"link": "https://t.me/addemoji/../x"}, headers=dev())
    assert bad.status_code == 422
