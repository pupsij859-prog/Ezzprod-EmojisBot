"""Цены. Единственное место, где считается стоимость генераций.

Базовая валюта — рубли. При оплате Звёздами сумма пересчитывается по курсу RUB_PER_STAR
и округляется вверх (Telegram принимает только целые Stars).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Settings

# способы оплаты: id → (название, подпись). Работают только те, у которых есть провайдер (см. enabled_methods)
METHODS: tuple[tuple[str, str, str], ...] = (
    ("stars", "Звёзды", ""),
    ("xrocket", "xRocket", "USDT, TON и другие монеты"),
    ("cryptobot", "CryptoBot", "Оплата криптовалютой"),
    ("sbp", "СБП", "Через приложение банка"),
)


@dataclass(frozen=True)
class Quote:
    qty: int
    total: int          # итого, ₽
    unit: float         # средняя цена за штуку, ₽
    discount_pct: int   # фактическая скидка относительно базовой цены
    stars: int          # та же сумма в Stars (для оплаты Звёздами)


def stars_for(settings: Settings, rub: int) -> int:
    return max(1, math.ceil(round(rub / settings.rub_per_star, 6)))


def _raw(settings: Settings, qty: int) -> int:
    base = settings.price_rub
    if qty >= settings.bulk_unit_from:
        total = qty * settings.bulk_unit_rub
    elif qty >= settings.bulk_discount_from:
        total = round(qty * base * (100 - settings.bulk_discount_pct) / 100)
    else:
        total = qty * base
    return max(1, total)


def _totals(settings: Settings, upto: int) -> list[int]:
    """totals[q] для q = 0..upto. Цена монотонна: больше штук никогда не стоит дешевле меньшего количества
    (иначе выгоднее купить 30, чем 29, и честная таблица скидок превращается в ловушку)."""
    raw = [0] + [_raw(settings, q) for q in range(1, upto + 1)]
    for q in range(upto - 1, 0, -1):
        raw[q] = min(raw[q], raw[q + 1])
    return raw


def quote(settings: Settings, qty: int, user_discount_pct: int = 0) -> Quote:
    if qty < 1:
        raise ValueError("qty must be >= 1")
    upto = max(qty, settings.max_buy_qty)
    total = _totals(settings, upto)[qty]
    if user_discount_pct > 0:
        pct = max(0, min(99, int(user_discount_pct)))
        total = max(1, round(total * (100 - pct) / 100))
    full = qty * settings.price_rub
    discount = round((1 - total / full) * 100) if full else 0
    return Quote(qty=qty, total=total, unit=total / qty, discount_pct=max(0, discount), stars=stars_for(settings, total))


def enabled_methods(settings: Settings) -> set[str]:
    enabled = {"stars"}
    if settings.xrocket_enabled:
        enabled.add("xrocket")
    if settings.cryptobot_enabled:
        enabled.add("cryptobot")
    return enabled


def pricing_table(settings: Settings) -> dict:
    """Всё, что нужно клиенту, чтобы показать цены (totals[qty] — точная сумма в ₽)."""
    rate = f"{settings.rub_per_star:g}".replace(".", ",")
    enabled = enabled_methods(settings)
    totals = _totals(settings, settings.max_buy_qty)
    return {
        "currency": "RUB",
        "unit": settings.price_rub,
        "discount_from": settings.bulk_discount_from,
        "discount_pct": settings.bulk_discount_pct,
        "bulk_from": settings.bulk_unit_from,
        "bulk_unit": settings.bulk_unit_rub,
        "rub_per_star": settings.rub_per_star,
        "max_qty": settings.max_buy_qty,
        "default_qty": 20,
        "totals": totals,
        "stars_totals": [stars_for(settings, t) for t in totals],
        "methods": [
            {
                "id": mid,
                "title": title,
                "sub": sub or (f"1 звезда = {rate} ₽" if mid == "stars" else ("USDT, TON и др. крипта" if mid == "cryptobot" else "USDT, TON и чеки")),
                "enabled": mid in enabled,
            }
            for mid, title, sub in METHODS
        ],
    }
