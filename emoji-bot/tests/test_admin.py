"""Админка Mini App и раздача кастомных эмодзи."""
from __future__ import annotations

import asyncio
import gzip
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import emoji, services
from app.main import create_app
from tests.conftest import dev, make_settings

ADMIN = 1001
USER = 2002


@pytest.fixture
def client(tmp_path):
    app = create_app(make_settings(tmp_path, admin_ids=(ADMIN,)), with_bot=False)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        c.app_state = app.state
        yield c


def ledger_ok(c) -> bool:
    db = c.app_state.ctx.db
    return c.portal.call(services.ledger_mismatches, db) == []


def test_me_reports_admin_flag(client):
    assert client.get("/api/me", headers=dev(ADMIN)).json()["is_admin"] is True
    assert client.get("/api/me", headers=dev(USER)).json()["is_admin"] is False


@pytest.mark.parametrize("path", ["/api/admin/stats", "/api/admin/payments", "/api/admin/users", "/api/admin/packs"])
def test_admin_api_forbidden_for_regular_users(client, path):
    r = client.get(path, headers=dev(USER))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"


def test_grant_goes_through_ledger_and_cannot_overdraw(client):
    client.get("/api/me", headers=dev(USER))  # регистрация, +1 подарок
    r = client.post(f"/api/admin/users/{USER}/grant", json={"delta": 5}, headers=dev(ADMIN))
    assert r.status_code == 200 and r.json()["balance"] == 6
    r = client.post(f"/api/admin/users/{USER}/grant", json={"delta": -100}, headers=dev(ADMIN))
    assert r.status_code == 402
    r = client.post(f"/api/admin/users/{USER}/grant", json={"delta": -2}, headers=dev(ADMIN))
    assert r.json()["balance"] == 4
    assert client.post(f"/api/admin/users/{USER}/grant", json={"delta": 0}, headers=dev(ADMIN)).status_code == 422
    assert client.post("/api/admin/users/999999/grant", json={"delta": 1}, headers=dev(ADMIN)).status_code == 404
    assert ledger_ok(client)

    detail = client.get(f"/api/admin/users/{USER}", headers=dev(ADMIN)).json()
    assert detail["balance"] == 4 and [e["reason"] for e in detail["ledger"]][:2] == ["admin", "admin"]


def test_user_search(client):
    client.get("/api/me", headers=dev(USER))
    found = client.get("/api/admin/users", params={"q": str(USER)}, headers=dev(ADMIN)).json()
    assert [u["id"] for u in found["items"]] == [USER]
    assert client.get("/api/admin/users", params={"q": "%"}, headers=dev(ADMIN)).json()["items"] == []


def test_payments_list_and_dev_refund(client):
    client.post("/api/dev/pay", json={"qty": 10}, headers=dev(USER))
    data = client.get("/api/admin/payments", params={"status": "paid"}, headers=dev(ADMIN)).json()
    assert data["counts"]["paid"] == 1 and len(data["items"]) == 1
    pay = data["items"][0]
    assert pay["qty"] == 10 and pay["user"]["id"] == USER and pay["provider"] == "dev"

    r = client.post(f"/api/admin/payments/{pay['charge_id']}/refund", json={"provider": pay["provider"]}, headers=dev(ADMIN))
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/me", headers=dev(USER)).json()["balance"] == 1  # остался только подарок
    again = client.post(f"/api/admin/payments/{pay['charge_id']}/refund", json={"provider": "dev"}, headers=dev(ADMIN))
    assert again.status_code == 409
    counts = client.get("/api/admin/payments", headers=dev(ADMIN)).json()["counts"]
    assert counts["refunded"] == 1 and counts["paid"] == 0
    assert ledger_ok(client)


def test_stars_refund_without_bot_is_unavailable(client):
    r = client.post("/api/admin/payments/charge-x/refund", json={"provider": "stars"}, headers=dev(ADMIN))
    assert r.status_code == 503


def test_stats_and_packs(client):
    client.post("/api/dev/pay", json={"qty": 3}, headers=dev(USER))
    r = client.post("/api/packs", json={"type": "emoji", "template_ids": ["classic-001"]}, headers=dev(USER))
    assert r.status_code == 201
    stats = client.get("/api/admin/stats", headers=dev(ADMIN)).json()
    assert stats["users"]["total"] == 2 and stats["packs"]["total"] == 1  # пользователь и сам админ
    assert stats["revenue"]["total"] > 0 and stats["gens"]["sold"] == 3
    packs = client.get("/api/admin/packs", headers=dev(ADMIN)).json()
    assert packs["counts"]["all"] == 1 and packs["items"][0]["user"]["id"] == USER
    assert client.get("/api/admin/packs", params={"status": "bogus"}, headers=dev(ADMIN)).status_code == 422


# ───────────────────────── эмодзи ─────────────────────────

def test_emoji_map_falls_back_without_bot(client):
    data = client.get("/api/emoji").json()["emoji"]
    assert data["gift"] == {"fallback": "🎁", "custom": False}
    assert client.get("/api/emoji/gift").status_code == 404
    assert client.get("/api/emoji/not-a-key").status_code == 404


def test_custom_emoji_ids_from_env_are_validated(tmp_path):
    s = make_settings(tmp_path, custom_emoji=(("gift", "123"), ("warn", "abc"), ("nope", "5")))
    assert emoji.ids(s) == {"gift": "123"}
    assert emoji.public_map(s, bot_ready=True)["gift"]["custom"] is True
    assert emoji.public_map(s, bot_ready=False)["gift"]["custom"] is False


class FakeBot:
    def __init__(self, animated: bool):
        self.animated = animated
        self.calls = 0

    async def get_custom_emoji_stickers(self, custom_emoji_ids):
        self.calls += 1
        return [SimpleNamespace(file_id="f1", is_animated=self.animated, is_video=False,
                                thumbnail=SimpleNamespace(file_id="t1"))]

    async def get_file(self, file_id):
        return SimpleNamespace(file_path=f"stickers/{file_id}")

    async def download_file(self, file_path, destination):
        if file_path.endswith("f1") and self.animated:
            destination.write(gzip.compress(json.dumps({"v": "5.5.2", "layers": []}).encode()))
        else:
            destination.write(b"RIFF....WEBP")


def test_emoji_store_downloads_once_and_unpacks_tgs(tmp_path):
    store = emoji.EmojiStore(tmp_path / "emoji")
    bot = FakeBot(animated=True)
    path, media = asyncio.run(store.get(bot, "42"))
    assert media == "application/json" and json.loads(path.read_text())["v"] == "5.5.2"
    asyncio.run(store.get(bot, "42"))
    assert bot.calls == 1  # второй раз — с диска
    thumb, media = asyncio.run(store.get(bot, "42", thumb=True))
    assert media == "image/webp" and thumb.name == "42.thumb.webp"
    assert asyncio.run(store.get(None, "77")) is None
