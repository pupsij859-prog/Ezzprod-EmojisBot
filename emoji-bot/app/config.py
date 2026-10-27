"""Настройки приложения. Всё берётся из переменных окружения / файла .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
WEBAPP_DIR = ROOT / "webapp"


def app_data_dir() -> Path:
    # Проект лежит в синхронизируемой OneDrive-папке: там нельзя держать ни SQLite (WAL), ни токен бота.
    # Поэтому на Windows данные и .env живут в %LOCALAPPDATA%.
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "EzzProdBot"
    return ROOT / "data"


def env_file_path() -> Path | None:
    """Где лежит .env: ENV_FILE → папка данных (вне OneDrive) → корень проекта (запасной вариант)."""
    explicit = os.environ.get("ENV_FILE")
    if explicit:
        return Path(explicit)
    for candidate in (app_data_dir() / ".env", ROOT / ".env"):
        if candidate.exists():
            return candidate
    return None


_env = env_file_path()
if _env is not None:
    load_dotenv(_env)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise SystemExit(f"Переменная {name} должна быть целым числом, сейчас: {raw!r}")


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int_list(name: str) -> tuple[int, ...]:
    out = []
    for part in _str(name).replace(";", ",").split(","):
        part = part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                raise SystemExit(f"{name}: ожидается список чисел через запятую, сейчас: {part!r}")
    return tuple(out)


def _pairs(name: str) -> tuple[tuple[str, str], ...]:
    """«key:value, key:value» → ((key, value), ...)."""
    out = []
    for part in _str(name).replace(";", ",").split(","):
        key, sep, value = part.partition(":")
        if not part.strip():
            continue
        if not sep or not key.strip() or not value.strip():
            raise SystemExit(f"{name}: ожидается список вида ключ:значение через запятую, сейчас: {part!r}")
        out.append((key.strip(), value.strip()))
    return tuple(out)


def _origins(name: str) -> tuple[str, ...]:
    """Адреса сайтов для CORS: «https://site.ru, http://localhost:5173» → без «/» на конце, только http(s)."""
    out = []
    for part in _str(name).replace(";", ",").split(","):
        origin = part.strip().rstrip("/")
        if not origin:
            continue
        if not origin.startswith(("https://", "http://")) or origin.count("/") != 2:
            raise SystemExit(f"{name}: ожидается адрес сайта вида https://example.com, сейчас: {part!r}")
        out.append(origin)
    return tuple(out)


def _is_local_url(url: str) -> bool:
    # сравниваем именно имя хоста: «http://localhost.evil.com» не локальный
    if url == "":
        return True
    from urllib.parse import urlparse

    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class Settings:
    bot_token: str = ""
    bot_username: str = ""          # без @; если пусто — берём через getMe
    webapp_url: str = ""            # публичный https-адрес Mini App
    brand_name: str = "EzzProd"
    support_username: str = ""      # без @; куда ведёт «поддержка»
    subscription_required: bool = False
    subscription_channel: str = ""  # @username или -100… для закрытого канала
    subscription_url: str = ""      # для закрытого канала — пригласительная ссылка
    public_url_file: str = ""       # временный туннель: файл с текущим адресом Mini App (см. tools/tunnel.py)
    admin_ids: tuple[int, ...] = () # кому доступны /refund, алерты по платежам и «Админка» в Mini App
    custom_emoji: tuple[tuple[str, str], ...] = ()  # ключ:custom_emoji_id, дополняет app/emoji.py
    bot_proxy: str = ""             # например socks5://127.0.0.1:1080, если Telegram API недоступен напрямую
    dev_mode: bool = False
    demo_mode: bool = True          # этап 1: сборка паков — имитация
    payments_live: bool = False     # реальные счета включаются только при настоящей сборке паков
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = app_data_dir()

    # веб-редактор на сайте (другой домен): вход через Telegram Login Widget и CORS только для этих адресов
    web_origins: tuple[str, ...] = ()  # например https://ezzprod.31-77-17-164.sslip.io
    web_session_days: int = 30         # сколько живёт вход на сайте
    web_export_price: int = 1          # сколько генераций стоит «скачать .tgs» в веб-редакторе (0 — бесплатно)

    # экономика
    signup_gens: int = 1            # подарок новичку
    price_rub: int = 5              # цена одной генерации в рублях
    bulk_discount_from: int = 30    # от скольки штук скидка
    bulk_discount_pct: int = 10
    bulk_unit_from: int = 50        # от скольки штук фиксированная цена
    bulk_unit_rub: int = 4          # цена за штуку от bulk_unit_from, ₽
    rub_per_star: float = 2.625      # курс для оплаты Звёздами: 1 ⭐ = столько ₽ (цены в 2.1x ниже)
    max_buy_qty: int = 500
    pro_min_purchased: int = 20     # сколько генераций надо купить суммарно, чтобы открылся pro
    referral_reward: int = 3
    referral_daily_cap: int = 10    # макс. наград рефереру в сутки
    referral_lifetime_cap: int = 30 # макс. бесплатных наград рефереру за всё время

    # крипто-платежи
    cryptobot_token: str = ""
    cryptobot_net: str = "mainnet"          # mainnet | testnet
    cryptobot_enabled: bool = False
    xrocket_key: str = ""
    xrocket_net: str = "mainnet"            # mainnet | testnet
    xrocket_enabled: bool = False
    rub_per_usdt: float = 95.0              # курс для xRocket (USDT)

    # лимиты Telegram и продукта
    emoji_pack_max: int = 200
    sticker_pack_max: int = 120
    text_max: int = 12
    styles_max: int = 20
    logos_max: int = 20
    logo_max_bytes: int = 2 * 1024 * 1024
    fonts_max: int = 10
    font_max_bytes: int = int(2.5 * 1024 * 1024)

    # имитация сборки: сколько секунд «собирается» пак
    demo_build_min_s: float = 3.0
    demo_build_max_s: float = 5.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ezzprod.sqlite3"

    @property
    def logos_dir(self) -> Path:
        return self.data_dir / "logos"

    @property
    def fonts_dir(self) -> Path:
        return self.data_dir / "fonts"

    def validate(self) -> None:
        """Опасные сочетания настроек не запускаем."""
        if self.dev_mode and (not _is_local_url(self.webapp_url) or self.host not in ("127.0.0.1", "localhost", "::1")):
            raise SystemExit(
                "DEV_MODE=1 нельзя вместе с публичным WEBAPP_URL или HOST не на localhost: "
                "вход без подписи Telegram стал бы доступен из интернета. "
                "Для работы через туннель поставь DEV_MODE=0."
            )

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            bot_token=_str("BOT_TOKEN"),
            bot_username=_str("BOT_USERNAME").lstrip("@"),
            webapp_url=_str("WEBAPP_URL").rstrip("/"),
            brand_name=_str("BRAND_NAME", "EzzProd") or "EzzProd",
            support_username=_str("SUPPORT_USERNAME").lstrip("@"),
            subscription_required=_bool("SUBSCRIPTION_REQUIRED"),
            subscription_channel=_str("SUBSCRIPTION_CHANNEL"),
            subscription_url=_str("SUBSCRIPTION_URL"),
            public_url_file=_str("PUBLIC_URL_FILE"),
            admin_ids=_int_list("ADMIN_IDS"),
            custom_emoji=_pairs("CUSTOM_EMOJI"),
            bot_proxy=_str("BOT_PROXY"),
            dev_mode=_bool("DEV_MODE", False),
            demo_mode=_bool("DEMO_MODE", True),
            payments_live=_bool("PAYMENTS_LIVE", False),
            host=_str("HOST", "127.0.0.1"),
            port=_int("PORT", 8000),
            data_dir=Path(_str("DATA_DIR")) if _str("DATA_DIR") else app_data_dir(),
            web_origins=_origins("WEB_ORIGINS"),
            web_session_days=_int("WEB_SESSION_DAYS", 30),
            web_export_price=_int("WEB_EXPORT_PRICE", 1),
            signup_gens=_int("SIGNUP_GENS", 1),
            price_rub=_int("PRICE_RUB", 5),
            bulk_discount_from=_int("BULK_DISCOUNT_FROM", 30),
            bulk_discount_pct=_int("BULK_DISCOUNT_PCT", 10),
            bulk_unit_from=_int("BULK_UNIT_FROM", 50),
            bulk_unit_rub=_int("BULK_UNIT_RUB", 4),
            rub_per_star=float(_str("RUB_PER_STAR", "2.625") or 2.625),
            pro_min_purchased=_int("PRO_MIN_PURCHASED", 20),
            referral_reward=_int("REFERRAL_REWARD", 3),
            referral_daily_cap=_int("REFERRAL_DAILY_CAP", 10),
            referral_lifetime_cap=_int("REFERRAL_LIFETIME_CAP", 30),
            cryptobot_token=_str("CRYPTOBOT_TOKEN"),
            cryptobot_net=_str("CRYPTOBOT_NET", "mainnet"),
            cryptobot_enabled=_bool("CRYPTOBOT_ENABLED", False),
            xrocket_key=_str("XROCKET_KEY"),
            xrocket_net=_str("XROCKET_NET", "mainnet"),
            xrocket_enabled=_bool("XROCKET_ENABLED", False),
            rub_per_usdt=float(_str("RUB_PER_USDT", "95.0") or 95.0),
        )
        settings.validate()
        return settings
