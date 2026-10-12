"""Оплата. Шов: PaymentProvider. Сейчас — Telegram Stars, позже подменим на своё."""
from __future__ import annotations

from typing import Protocol

from aiogram import Bot
from aiogram.types import LabeledPrice

from .ru import gens


class PaymentProvider(Protocol):
    name: str

    async def create_invoice(self, *, payment_id: str, qty: int, amount: int) -> str:
        """Возвращает ссылку на оплату, которую Mini App открывает через WebApp.openInvoice."""
        ...


class StarsProvider:
    """Telegram Stars: валюта XTR, provider_token не нужен."""

    name = "stars"

    def __init__(self, bot: Bot, brand: str) -> None:
        self._bot = bot
        self._brand = brand

    async def create_invoice(self, *, payment_id: str, qty: int, amount: int) -> str:
        return await self._bot.create_invoice_link(
            title=gens(qty)[:32],
            description=f"Генерации {self._brand}: не сгорают, ник и логотип включены."[:255],
            payload=payment_id,  # 32 hex-символа, лимит Telegram — 128 байт
            currency="XTR",
            prices=[LabeledPrice(label=gens(qty)[:32], amount=amount)],
        )
