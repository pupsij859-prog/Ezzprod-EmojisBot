"""Оплата. Провайдеры: Telegram Stars, CryptoBot, xRocket."""
from __future__ import annotations

import hashlib
import re
import hmac
import logging
from typing import Any, Protocol

import aiohttp
from aiogram import Bot
from aiogram.types import LabeledPrice

from .config import Settings
from .ru import gens

log = logging.getLogger("ezzprod.payments")


class PaymentProvider(Protocol):
    name: str

    async def create_invoice(self, *, payment_id: str, qty: int, amount: int, **kwargs) -> str | dict:
        """Возвращает ссылку на оплату."""
        ...


class StarsProvider:
    """Telegram Stars: валюта XTR, provider_token не нужен."""

    name = "stars"

    def __init__(self, bot: Bot, brand: str) -> None:
        self._bot = bot
        self._brand = brand

    async def create_invoice(self, *, payment_id: str, qty: int, amount: int, **kwargs) -> str:
        return await self._bot.create_invoice_link(
            title=gens(qty)[:32],
            description=f"Генерации {self._brand}: не сгорают, ник и логотип включены."[:255],
            payload=payment_id,  # 32 hex-символа, лимит Telegram — 128 байт
            currency="XTR",
            prices=[LabeledPrice(label=gens(qty)[:32], amount=amount)],
        )


class CryptoBotProvider:
    """Crypto Pay API (@CryptoBot). Счета в фиатных рублях (оплата любой криптой по курсу)."""

    name = "cryptobot"

    def __init__(self, token: str, net: str = "mainnet", bot_username: str = "") -> None:
        self._token = (token or "").strip()
        self._net = net
        self._bot_username = bot_username
        self._base_url = "https://pay.crypt.bot/api" if net == "mainnet" else "https://testnet-pay.crypt.bot/api"

    @property
    def is_configured(self) -> bool:
        return bool(self._token)

    async def create_invoice(self, *, payment_id: str, qty: int, amount_rub: int, **kwargs) -> dict:
        if not self._token:
            raise RuntimeError("CRYPTOBOT_TOKEN not configured")
        url = f"{self._base_url}/createInvoice"
        payload: dict[str, Any] = {
            "currency_type": "fiat",
            "fiat": "RUB",
            "amount": f"{amount_rub:.2f}",
            "description": f"Генерации: {gens(qty)}"[:255],
            "payload": payment_id,
        }
        if self._bot_username:
            payload["paid_btn_name"] = "openBot"
            payload["paid_btn_url"] = f"https://t.me/{self._bot_username}"

        headers = {
            "Crypto-Pay-API-Token": self._token,
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    log.error("CryptoBot createInvoice error: %s", data)
                    raise RuntimeError(f"CryptoBot error: {data.get('error')}")
                res = data["result"]
                # мини-приложение CryptoBot открывается поверх нашего Mini App; ссылка в чат — только запасная
                pay_url = res.get("mini_app_invoice_url") or res.get("bot_invoice_url") or res.get("pay_url")
                return {
                    "url": pay_url,
                    "external_id": str(res.get("invoice_id")),
                    "data": res,
                }

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        """Проверка подписи вебхука CryptoBot (заголовок crypto-pay-api-signature)."""
        if not self._token or not signature:
            return False
        secret = hashlib.sha256(self._token.encode()).digest()
        calc = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(calc.lower(), signature.lower())

    async def check_invoice(self, invoice_id: str) -> str | None:
        """Проверяет статус счёта через API (paid, active, expired)."""
        if not self._token:
            return None
        url = f"{self._base_url}/getInvoices"
        headers = {"Crypto-Pay-API-Token": self._token, "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json={"invoice_ids": [int(invoice_id)]}, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()
                    if data.get("ok") and data.get("result", {}).get("items"):
                        item = data["result"]["items"][0]
                        return item.get("status")
        except Exception as exc:
            log.warning("CryptoBot check_invoice failed: %s", exc)
        return None


def tg_app_link(link: str) -> str:
    """Ссылка «в чат бота-кошелька» (t.me/<bot>?start=<код>) → та же ссылка в его мини-приложении
    (t.me/<bot>/app?startapp=<код>): счёт открывается поверх Mini App, без перехода в чат."""
    m = re.match(r"^https://t\.me/([A-Za-z0-9_]{4,32})\?start=([A-Za-z0-9_-]{1,64})$", link or "")
    return f"https://t.me/{m.group(1)}/app?startapp={m.group(2)}" if m else (link or "")


def wallet_app_link(links: dict) -> str:
    """Из ссылок счёта xRocket выбираем ссылку на мини-приложение; если её нет — переделываем ссылку на бота."""
    for key, value in links.items():
        k = str(key).lower()
        if isinstance(value, str) and value.startswith("https://t.me/") and ("mini" in k or "webapp" in k or "web_app" in k):
            return value
    return tg_app_link(links.get("telegramBotLink") or links.get("botLink") or "")


class XRocketProvider:
    """xRocket Pay API (@xrocket / Ton Rocket)."""

    name = "xrocket"

    def __init__(self, key: str, net: str = "mainnet", rub_per_usdt: float = 95.0) -> None:
        self._key = (key or "").strip()
        self._net = net
        self._rub_per_usdt = rub_per_usdt or 95.0
        self._base_url = "https://pay.api.xrocket.exchange/api/v1"

    @property
    def is_configured(self) -> bool:
        return bool(self._key)

    async def create_invoice(self, *, payment_id: str, qty: int, amount_rub: int, **kwargs) -> dict:
        if not self._key:
            raise RuntimeError("XROCKET_KEY not configured")
        url = f"{self._base_url}/invoices"
        usdt_amount = max(0.1, round(amount_rub / self._rub_per_usdt, 2))
        payload = {
            "priceCurrency": "USDT",
            "priceAmount": f"{usdt_amount:.2f}",
            "description": f"Генерации: {gens(qty)}"[:255],
            "customData": payment_id,
        }
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Ezzprod/1.0",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    log.error("xRocket create invoice error %s: %s", resp.status, data)
                    raise RuntimeError(f"xRocket error: {data.get('detail') or data.get('title') or data}")
                link = wallet_app_link(data.get("links") or {})
                return {
                    "url": link,
                    "external_id": str(data.get("id")),
                    "data": data,
                }

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        """Проверка подписи вебхука xRocket."""
        if not self._key or not signature:
            return False
        secret = hashlib.sha256(self._key.encode()).digest()
        calc = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(calc.lower(), signature.lower())

    async def check_invoice(self, invoice_id: str) -> str | None:
        if not self._key:
            return None
        url = f"{self._base_url}/invoices?ids%5B%5D={invoice_id}"
        headers = {
            "Authorization": f"Bearer {self._key}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Ezzprod/1.0",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    items = data.get("items") or []
                    if items:
                        return items[0].get("status")
        except Exception as exc:
            log.warning("xRocket check_invoice failed: %s", exc)
        return None


class PaymentHub:
    """Единый хаб платежей: Stars, CryptoBot, xRocket."""

    def __init__(self, settings: Settings, bot: Bot | None = None, bot_username: str = "") -> None:
        self.settings = settings
        self.stars = StarsProvider(bot, settings.brand_name) if bot else None
        self.cryptobot = CryptoBotProvider(settings.cryptobot_token, settings.cryptobot_net, bot_username)
        self.xrocket = XRocketProvider(settings.xrocket_key, settings.xrocket_net, settings.rub_per_usdt)

    def get(self, method: str):
        if method == "stars":
            return self.stars
        if method == "cryptobot":
            return self.cryptobot
        if method == "xrocket":
            return self.xrocket
        return None

    async def create_invoice(
        self, *, method: str, payment_id: str, qty: int, stars: int, amount_rub: int
    ) -> dict:
        provider = self.get(method)
        if provider is None:
            raise ValueError(f"Provider not available for method: {method}")
        if method == "stars":
            url = await provider.create_invoice(payment_id=payment_id, qty=qty, amount=stars)
            return {"url": url, "external_id": None}
        if method in ("cryptobot", "xrocket"):
            return await provider.create_invoice(payment_id=payment_id, qty=qty, amount_rub=amount_rub)
        raise ValueError(f"Unknown method {method}")
