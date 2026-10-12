"""Русские числительные и склонения."""
from __future__ import annotations


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def gens(n: int) -> str:
    """«1 генерация», «3 генерации», «5 генераций»."""
    return f"{n} {plural(n, 'генерация', 'генерации', 'генераций')}"
