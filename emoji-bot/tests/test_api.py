import io
import re
import time

from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from tests.conftest import dev, make_settings, sign_init_data


def png_bytes(size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "#336699").save(buf, "PNG")
    return buf.getvalue()


def wait_ready(client, pack_id, headers, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        pack = client.get(f"/api/packs/{pack_id}", headers=headers).json()
        if pack["status"] != "processing":
            return pack
        time.sleep(0.05)
    raise AssertionError("пак не собрался")


def test_auth_required(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "tma garbage"}).status_code == 401


def test_signed_init_data_works(client):
    init = sign_init_data({"id": 7, "first_name": "Оля", "username": "olya"})
    r = client.get("/api/me", headers={"Authorization": f"tma {init}"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 7 and body["balance"] == 1 and body["onboarded"] is False
    assert re.search(r"\?start=ref_[0-9a-f]{8}$", body["ref_link"])  # непрозрачный код, а не Telegram ID
    assert body["limits"]["text_max"] == 12 and body["brand"] == "EzzProd"
    assert "dev" not in body  # клиенту не сообщаем, включён ли режим разработки


def test_startapp_ref_param_links_referrer_for_new_user_only(client):
    code = client.get("/api/me", headers=dev(1)).json()["ref_link"].split("ref_")[1]
    init = sign_init_data({"id": 2, "first_name": "friend"}, start_param=f"ref_{code}")
    assert client.get("/api/me", headers={"Authorization": f"tma {init}"}).status_code == 200
    assert client.get("/api/referral", headers=dev(1)).json()["invited"] == 1
    # тот же человек с другим кодом ничего не меняет
    code2 = client.get("/api/me", headers=dev(3)).json()["ref_link"].split("ref_")[1]
    init2 = sign_init_data({"id": 2, "first_name": "friend"}, start_param=f"ref_{code2}")
    client.get("/api/me", headers={"Authorization": f"tma {init2}"})
    assert client.get("/api/referral", headers=dev(3)).json()["invited"] == 0


def test_dev_mode_is_structurally_off_in_prod(tmp_path):
    prod = create_app(make_settings(tmp_path / "a", dev_mode=False), with_bot=False)
    with TestClient(prod, client=("127.0.0.1", 1)) as c:
        assert c.get("/api/me", headers=dev()).status_code == 401           # обход не работает
        assert c.post("/api/dev/pay", json={"qty": 1}, headers=dev()).status_code in (404, 405)  # маршрута нет
    # DEV_MODE включён, но запрос пришёл «снаружи» или через прокси/туннель
    app = create_app(make_settings(tmp_path / "b"), with_bot=False)
    with TestClient(app, client=("8.8.8.8", 1)) as c:
        assert c.get("/api/me", headers=dev()).status_code == 401
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        assert c.get("/api/me", headers={**dev(), "X-Forwarded-For": "1.2.3.4"}).status_code == 401
        assert c.get("/api/me", headers=dev()).status_code == 200


def test_onboarding_flag_is_server_side(client):
    h = dev(5)
    assert client.get("/api/me", headers=h).json()["onboarded"] is False
    assert client.post("/api/onboarding/complete", headers=h).status_code == 200
    assert client.get("/api/me", headers=h).json()["onboarded"] is True
    assert client.get("/api/me", headers=dev(6)).json()["onboarded"] is False  # у других — нет


def test_catalog_is_read_from_template_files(client):
    cats = client.get("/api/categories").json()
    assert {c["name"]: c["count"] for c in cats["categories"]} == {
        "NEW": 20, "классика": 144, "персонажи": 58, "подарки": 34, "любовь": 27, "финансы": 17, "особые": 16, "флаги": 8,
        "разное": 39}
    assert cats["total"] == 363
    assert cats["categories"][0]["cover"] == "new-001"  # новые шаблоны — первой категорией
    p1 = client.get("/api/templates?category=other&page=1").json()
    p2 = client.get("/api/templates?category=other&page=2").json()
    assert (p1["pages"], len(p1["items"]), len(p2["items"])) == (2, 30, 9)
    assert not {i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]}
    assert client.get("/api/templates?category=nope").status_code == 404


def test_template_file_is_served_as_gzipped_lottie(client):
    r = client.get("/api/templates/classic-001.tgs")
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    anim = r.json()   # клиент распаковал gzip сам
    assert (anim["w"], anim["h"]) == (512, 512) and "max-age" in r.headers["cache-control"]
    assert client.get("/api/templates/classic-001.tgs", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    assert client.get("/api/templates/nope-001.tgs").status_code == 404
    assert client.get("/api/templates/..%2Fapp.tgs").status_code == 404


def test_static_thumbnail_exists_for_every_template(client):
    from app import catalog

    for cat in catalog.all_categories():
        for n in (1, cat.count):
            assert client.get(f"/assets/thumbs/{cat.id}-{n:03d}.webp").status_code == 200


def test_pack_flow_and_referral(client):
    inviter = dev(100)
    code = client.get("/api/me", headers=inviter).json()["ref_link"].split("ref_")[1]
    from app import services

    ctx = client.app_state.ctx
    client.portal.call(services.upsert_user, ctx.db, ctx.settings, {"id": 200, "first_name": "friend"}, code)
    friend = dev(200)
    assert client.get("/api/me", headers=friend).json()["balance"] == 1

    body = {"type": "emoji", "template_ids": ["classic-001"], "text": "ez", "color": "#131822"}
    r = client.post("/api/packs", json=body, headers=friend)
    assert r.status_code == 201 and r.json()["status"] == "processing"
    assert client.get("/api/me", headers=friend).json()["balance"] == 0
    pack = wait_ready(client, r.json()["id"], friend)
    assert pack["status"] == "ready" and pack["progress"] == 1.0 and pack["link"].startswith("https://")
    assert client.get("/api/me", headers=friend).json()["trial_exports_left"] == 0
    assert [p["id"] for p in client.get("/api/packs", headers=friend).json()["packs"]] == [pack["id"]]
    assert client.get(f"/api/packs/{pack['id']}", headers=inviter).status_code == 404  # чужой пак не виден
    # пак на подарочной генерации награды не даёт: нужен ещё и покупатель
    assert client.get("/api/me", headers=inviter).json()["balance"] == 1
    ref = client.get("/api/referral", headers=inviter).json()
    assert (ref["invited"], ref["created_pack"], ref["earned"]) == (1, 1, 0)
    # друг купил генерации (dev-эмуляция) — пригласившему +3
    assert client.post("/api/dev/pay", json={"qty": 1}, headers=friend).status_code == 200
    assert client.get("/api/me", headers=inviter).json()["balance"] == 4  # 1 подарок + 3 за друга
    ref = client.get("/api/referral", headers=inviter).json()
    assert (ref["invited"], ref["created_pack"], ref["earned"], ref["reward"]) == (1, 1, 3, 3)


def test_pack_idempotency_key(client):
    h = {**dev(250), "Idempotency-Key": "abcdef123456"}
    body = {"type": "emoji", "template_ids": ["classic-001"]}
    a = client.post("/api/packs", json=body, headers=h)
    b = client.post("/api/packs", json=body, headers=h)  # ретрай после потерянного ответа
    assert a.status_code == b.status_code == 201 and a.json()["id"] == b.json()["id"]
    assert client.get("/api/me", headers=dev(250)).json()["balance"] == 0
    assert len(client.get("/api/packs", headers=dev(250)).json()["packs"]) == 1
    assert client.post("/api/packs", json=body, headers={**dev(251), "Idempotency-Key": "x"}).status_code == 422


def test_delete_pack(client):
    user = dev(260)
    body = {"type": "emoji", "template_ids": ["classic-001"]}
    r = client.post("/api/packs", json=body, headers=user)
    assert r.status_code == 201
    pack_id = r.json()["id"]

    # Удаление во время processing запрещено
    del_proc = client.delete(f"/api/packs/{pack_id}", headers=user)
    assert del_proc.status_code == 422

    # Дожидаемся готовности
    pack = wait_ready(client, pack_id, user)
    assert pack["status"] == "ready"

    # Чужой пользователь не может удалить пак
    other = dev(261)
    assert client.delete(f"/api/packs/{pack_id}", headers=other).status_code == 404

    # Успешное удаление своим пользователем
    res = client.delete(f"/api/packs/{pack_id}", headers=user)
    assert res.status_code == 200 and res.json() == {"ok": True}

    # Повторное удаление возвращает 404
    assert client.delete(f"/api/packs/{pack_id}", headers=user).status_code == 404

    # Пак больше не отдаётся в списке и по прямому ID
    assert pack_id not in [p["id"] for p in client.get("/api/packs", headers=user).json()["packs"]]
    assert client.get(f"/api/packs/{pack_id}", headers=user).status_code == 404


def test_pack_requires_balance(client):
    h = dev(300)
    ids = [f"classic-{i:03d}" for i in range(1, 4)]
    r = client.post("/api/packs", json={"type": "emoji", "template_ids": ids}, headers=h)
    assert r.status_code == 402 and r.json()["detail"] == {"code": "insufficient_balance", "need": 2}
    assert client.post("/api/packs", json={"type": "emoji", "template_ids": []}, headers=h).status_code == 422


def test_logo_upload_validation(client):
    h = dev(400)
    ok = client.post("/api/logos", files={"file": ("my logo.png", png_bytes(), "image/png")}, headers=h)
    assert ok.status_code == 201 and ok.json()["thumb"].startswith("data:image/png;base64,") and len(ok.json()["id"]) == 32
    bad = client.post("/api/logos", files={"file": ("x.png", b"not an image", "image/png")}, headers=h)
    assert bad.status_code == 422
    svg = client.post("/api/logos", files={"file": ("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")}, headers=h)
    assert svg.status_code == 422
    huge = client.post("/api/logos", files={"file": ("x.png", b"0" * (3 * 1024 * 1024 + 100), "image/png")}, headers=h)
    assert huge.status_code == 413  # отсекли по Content-Length до разбора тела
    medium = client.post("/api/logos", files={"file": ("x.png", b"0" * (2 * 1024 * 1024 + 10), "image/png")}, headers=h)
    assert medium.status_code == 422
    # анимированные картинки не принимаем
    frames = [Image.new("RGB", (8, 8), c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:])
    gif = client.post("/api/logos", files={"file": ("a.gif", buf.getvalue(), "image/gif")}, headers=h)
    assert gif.status_code == 422
    # логотип можно использовать в паке, чужой — нельзя
    body = {"type": "emoji", "template_ids": ["classic-001"], "logo_id": ok.json()["id"]}
    assert client.post("/api/packs", json=body, headers=h).status_code == 201
    assert client.post("/api/packs", json=body, headers=dev(401)).status_code == 422
    # удалить можно только свой
    assert client.delete(f"/api/logos/{ok.json()['id']}", headers=dev(401)).status_code == 404
    assert client.delete(f"/api/logos/{ok.json()['id']}", headers=h).status_code == 200


def test_logo_exif_orientation_is_applied(client):
    img = Image.new("RGB", (40, 20), "#336699")
    exif = Image.Exif()
    exif[0x0112] = 6  # «повернуть на 90° по часовой»
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    r = client.post("/api/logos", files={"file": ("p.jpg", buf.getvalue(), "image/jpeg")}, headers=dev(410))
    assert r.status_code == 201
    import base64

    thumb = Image.open(io.BytesIO(base64.b64decode(r.json()["thumb"].split(",")[1])))
    assert thumb.height > thumb.width  # 40×20 после поворота стало 20×40


def test_json_body_limit(client):
    big = {"type": "emoji", "template_ids": ["classic-001"] * 20000}
    assert client.post("/api/packs", json=big, headers=dev(420)).status_code == 413


def test_styles_pro_gate_and_purchase(client):
    h = dev(500)
    st = client.get("/api/styles", headers=h).json()
    assert st["pro"] is False and st["styles"] == [] and st["pro_min"] == 20
    r = client.post("/api/styles", json={"name": "мой"}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "pro_required"
    # покупка 20 генераций (dev-эмуляция) открывает pro
    pay = client.post("/api/dev/pay", json={"qty": 20}, headers=h).json()
    assert pay == {"paid": True, "balance": 21}
    assert client.post("/api/styles", json={"name": "мой", "text": "ez"}, headers=h).status_code == 201
    assert len(client.get("/api/styles", headers=h).json()["styles"]) == 1


def test_billing_invoice_and_payment_polling(client):
    h = dev(600)
    assert client.post("/api/billing/invoice", json={"qty": 20}, headers=h).status_code == 503  # провайдера нет

    class Fake:
        name = "fake"
        calls = []

        async def create_invoice(self, *, payment_id, qty, amount):
            self.calls.append((payment_id, qty, amount))
            return f"https://t.me/$fake_{payment_id}"

    fake = Fake()
    client.app_state.ctx.payments = fake
    from dataclasses import replace
    client.app_state.ctx.settings = replace(client.app_state.ctx.settings, demo_mode=False, payments_live=True)
    r = client.post("/api/billing/invoice", json={"qty": 30}, headers=h)
    assert r.status_code == 200 and r.json()["total"] == 135 and r.json()["stars"] == 52 and fake.calls[0][1:] == (30, 52)
    assert client.post("/api/billing/invoice", json={"qty": 5, "method": "sbp"}, headers=h).status_code == 503
    pid = r.json()["payment_id"]
    assert client.get(f"/api/billing/payments/{pid}", headers=h).json() == {"status": "pending"}
    assert client.get(f"/api/billing/payments/{pid}", headers=dev(601)).status_code == 404  # чужой не виден
    assert client.get("/api/billing/payments/not-a-uuid", headers=h).status_code == 404
    assert client.post("/api/billing/invoice", json={"qty": 0}, headers=h).status_code == 422
    assert client.post("/api/billing/invoice", json={"qty": 501}, headers=h).status_code == 422


def test_invoices_can_be_switched_off(tmp_path):
    app = create_app(make_settings(tmp_path, payments_live=False), with_bot=False)
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        class Fake:
            async def create_invoice(self, **kw):
                raise AssertionError("счёт выпускаться не должен")

        c.app.state.ctx.payments = Fake()
        assert c.post("/api/billing/invoice", json={"qty": 5}, headers=dev(1)).status_code == 503


def test_pricing_endpoint(client):
    p = client.get("/api/pricing").json()
    assert p["currency"] == "RUB" and p["totals"][20] == 100 and p["default_qty"] == 20
    assert [m["id"] for m in p["methods"] if m["enabled"]] == ["stars"]


def test_rate_limit(client):
    h = dev(700)
    codes = [client.post("/api/billing/invoice", json={"qty": 1}, headers=h).status_code for _ in range(12)]
    assert 429 in codes


def test_glyphs_return_vector_nick(client):
    h = dev(800)
    r = client.post("/api/glyphs", json={"text": "  Кирилл  "}, headers=h)
    assert r.status_code == 200 and r.json()["text"] == "Кирилл"
    drawing = r.json()["drawing"]
    assert drawing["layers"][0]["color"] is None and len(drawing["layers"][0]["paths"]) >= 6
    assert client.post("/api/glyphs", json={"text": ""}, headers=h).json()["drawing"] is None
    assert client.post("/api/glyphs", json={"text": "x"}).status_code == 401   # только авторизованным


def test_logo_upload_returns_vector_and_owner_can_reload_it(client):
    h = dev(810)
    up = client.post("/api/logos", files={"file": ("l.png", png_bytes(), "image/png")}, headers=h).json()
    vec = up["vector"]
    assert vec["layers"] and vec["layers"][0]["color"] == [51, 102, 153]
    assert client.get(f"/api/logos/{up['id']}/vector", headers=h).json()["vector"] == vec
    assert client.get(f"/api/logos/{up['id']}/vector", headers=dev(811)).status_code == 404   # чужой
    assert client.delete(f"/api/logos/{up['id']}", headers=h).status_code == 200
    assert client.get(f"/api/logos/{up['id']}/vector", headers=h).status_code == 404
