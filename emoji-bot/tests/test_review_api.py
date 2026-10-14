"""Ревью-тесты HTTP-слоя и фронтенда (второй проход). Дыры из ревью закрыты; тесты держат их закрытыми."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import dev, make_settings

WEBAPP = Path(__file__).resolve().parent.parent / "webapp"
JS = sorted((WEBAPP / "js").rglob("*.js"))


# ───────────────────────── размер тела ─────────────────────────

def test_chunked_upload_is_limited_before_parsing(client):
    boundary = "xBOUNDARYx"

    def body():
        yield (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.png\"\r\n"
               "Content-Type: image/png\r\n\r\n").encode()
        for _ in range(6):                     # 6 МБ при лимите 3 МБ
            yield b"\0" * (1024 * 1024)
        yield f"\r\n--{boundary}--\r\n".encode()

    r = client.post("/api/logos", content=body(), headers={
        **dev(1), "Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert r.status_code == 413


def test_declared_oversize_upload_is_rejected(client):
    r = client.post("/api/logos", files={"file": ("a.png", b"\0" * (4 * 1024 * 1024), "image/png")}, headers=dev(1))
    assert r.status_code == 413


# ───────────────────────── демо + живые платежи ─────────────────────────

def test_demo_mode_must_not_issue_real_invoices(tmp_path):
    app = create_app(make_settings(tmp_path, demo_mode=True, payments_live=True), with_bot=False)
    with TestClient(app, client=("127.0.0.1", 1)) as c:
        class Fake:
            async def create_invoice(self, **kw):
                return "https://t.me/$real"

        c.app.state.ctx.payments = Fake()
        assert c.post("/api/billing/invoice", json={"qty": 5}, headers=dev(1)).status_code == 503


# ───────────────────────── авторизация ─────────────────────────

def test_dev_header_ignored_when_forwarded_headers_present(client):
    r = client.get("/api/me", headers={**dev(5), "X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 401


def test_dev_header_ignored_from_non_loopback_peer(settings):
    app = create_app(settings, with_bot=False)
    with TestClient(app, client=("203.0.113.5", 1)) as c:
        assert c.get("/api/me", headers=dev(5)).status_code == 401


def test_duplicate_initdata_fields_cannot_swap_identity(client):
    """Подпись считается по dict(pairs): дубль поля `user` меняет строку проверки, поэтому подмена не проходит."""
    from tests.conftest import sign_init_data

    good = sign_init_data({"id": 11, "first_name": "a"})
    forged = good + "&user=%7B%22id%22%3A12%7D"
    assert client.get("/api/me", headers={"Authorization": f"tma {forged}"}).status_code == 401


# ───────────────────────── фронтенд (статические проверки исходников) ─────────────────────────

def test_no_html_injection_sinks_in_frontend():
    sink = re.compile(r"\.innerHTML|\.outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function\(")
    offenders = [f"{p.name}:{i}" for p in JS for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                 if sink.search(line)]
    assert offenders == []


def test_frontend_can_delete_uploaded_logos():
    api = (WEBAPP / "js" / "api.js").read_text(encoding="utf-8")
    assert "/logos/" in api and "DELETE" in api


def test_customize_sheet_unsubscribes_on_close():
    """Экран «Оформление» при закрытии отписывается от всех событий bus и гасит анимации превью."""
    src = (WEBAPP / "js" / "views" / "customize.js").read_text(encoding="utf-8")
    assert "onClose: () => cleanup()" in src
    assert re.search(r"return \(\) => \{ offs\.forEach\(\(off\) => off\(\)\); if \(parts\) parts\.destroy\(\); \}", src)


def test_no_blocking_third_party_stylesheet():
    html = (WEBAPP / "index.html").read_text(encoding="utf-8")
    assert not re.search(r'<link[^>]+rel="stylesheet"[^>]+fonts\.googleapis\.com', html)


def test_html_has_content_security_policy(client):
    r = client.get("/")
    assert "content-security-policy" in {k.lower() for k in r.headers}


def test_static_files_do_not_expose_server_code(client):
    for path in ("/app/config.py", "/../app/config.py", "/.env", "/requirements.txt", "/js/../../.env"):
        assert client.get(path).status_code == 404


def test_user_values_rendered_as_text_only():
    """h() кладёт строки через createTextNode: никаких HTML-вставок даже для children."""
    ui = (WEBAPP / "js" / "ui.js").read_text(encoding="utf-8")
    assert "createTextNode" in ui and "innerHTML" not in ui
