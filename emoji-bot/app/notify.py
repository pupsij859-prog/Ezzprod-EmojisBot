"""Уведомления пользователю. Бизнес-логика не знает про Telegram — только про этот интерфейс."""
from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    async def pack_ready(self, user_id: int, pack: dict) -> None: ...
    async def pack_failed(self, user_id: int, pack_id: int) -> None: ...
    async def referral_reward(self, user_id: int, amount: int) -> None: ...
    async def payment_ok(self, user_id: int, qty: int, balance: int) -> None: ...
    async def admin_alert(self, text: str) -> None: ...


class NullNotifier:
    """Ничего не делает (бот не запущен — режим «только веб»)."""

    async def pack_ready(self, user_id: int, pack: dict) -> None:
        return None

    async def pack_failed(self, user_id: int, pack_id: int) -> None:
        return None

    async def referral_reward(self, user_id: int, amount: int) -> None:
        return None

    async def payment_ok(self, user_id: int, qty: int, balance: int) -> None:
        return None

    async def admin_alert(self, text: str) -> None:
        return None
