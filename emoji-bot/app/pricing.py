"""Цены в Stars. Единственное место, где считается стоимость генераций."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Settings


@dataclass(frozen=True)
class Quote:
    qty: int
    total: int          # итого Stars (целое — так требует Telegram)
    unit: float         # средняя цена за штуку
    discount_pct: int   # фактическая скидка относительно базовой цены


def _raw(settings: Settings, qty: int) -> int:
    base = settings.stars_per_gen
    if qty >= settings.bulk_unit_from:
        total = qty * settings.bulk_unit_stars
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


def quote(settings: Settings, qty: int) -> Quote:
    if qty < 1:
        raise ValueError("qty must be >= 1")
    upto = max(qty, settings.max_buy_qty)
    total = _totals(settings, upto)[qty]
    full = qty * settings.stars_per_gen
    discount = round((1 - total / full) * 100) if full else 0
    return Quote(qty=qty, total=total, unit=total / qty, discount_pct=max(0, discount))


def pricing_table(settings: Settings) -> dict:
    """Всё, что нужно клиенту, чтобы показать цены (totals[qty] — точная сумма)."""
    return {
        "currency": "XTR",
        "unit": settings.stars_per_gen,
        "discount_from": settings.bulk_discount_from,
        "discount_pct": settings.bulk_discount_pct,
        "bulk_from": settings.bulk_unit_from,
        "bulk_unit": settings.bulk_unit_stars,
        "max_qty": settings.max_buy_qty,
        "default_qty": 20,
        "totals": _totals(settings, settings.max_buy_qty),
    }
