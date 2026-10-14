from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.main import create_app  # noqa: E402

BOT_TOKEN = "123456:TEST-TOKEN"


def sign_init_data(user: dict, bot_token: str = BOT_TOKEN, auth_date: int | None = None, **extra) -> str:
    fields = {"auth_date": str(auth_date if auth_date is not None else int(time.time())),
              "query_id": "AAH-test", "user": json.dumps(user, separators=(",", ":")), **extra}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def make_settings(tmp_path: Path, **over) -> Settings:
    base = dict(bot_token=BOT_TOKEN, bot_username="ezz_test_bot", data_dir=tmp_path, dev_mode=True,
                demo_build_min_s=0.05, demo_build_max_s=0.1, brand_name="EzzProd")
    base.update(over)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "t.sqlite3")
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
def client(settings):
    app = create_app(settings, with_bot=False)
    # клиент «с localhost», иначе dev-обход подписи (правильно) не сработает
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        c.app_state = app.state
        yield c


def dev(uid: int = 1001) -> dict:
    return {"X-Dev-User": str(uid)}
