"""Каталог шаблонов. Этап 1: заглушки — те же категории и счётчики, что у референса (319 шт.)."""
from __future__ import annotations

import math
from dataclasses import dataclass

PAGE_SIZE = 30


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    count: int


CATEGORIES: tuple[Category, ...] = (
    Category("classic", "классика", 144),
    Category("characters", "персонажи", 58),
    Category("gifts", "подарки", 34),
    Category("love", "любовь", 27),
    Category("finance", "финансы", 17),
    Category("misc", "разное", 39),
)

# формы, которые умеет рисовать placeholders.py (порядок = placeholders.SHAPE_ORDER)
SHAPE_NAMES: tuple[str, ...] = (
    "плашка", "круг", "щит", "звезда", "облачко", "флаг", "монета",
    "сердце", "корона", "кубок", "подарок", "ракета", "молния", "капля",
)
PALETTE_COUNT = 10

# форма для обложки категории на главной (первый шаблон этой формы; если такого нет — первый в категории)
COVER_SHAPES: dict[str, str] = {
    "classic": "звезда", "characters": "корона", "gifts": "подарок",
    "love": "сердце", "finance": "монета", "misc": "ракета",
}

_BY_ID_CATEGORY = {c.id: (i, c) for i, c in enumerate(CATEGORIES)}


@dataclass(frozen=True)
class Template:
    id: str
    category: str
    index: int      # 1..count внутри категории
    shape: int
    palette: int

    @property
    def name(self) -> str:
        return f"{SHAPE_NAMES[self.shape]} {self.index}"


def _make(gindex: int, cat: Category, n: int) -> Template:
    # детерминированный «разброс» форм и палитр по сквозному индексу: соседние карточки не повторяются
    shape = gindex % len(SHAPE_NAMES)
    palette = (gindex // len(SHAPE_NAMES) + gindex * 3) % PALETTE_COUNT
    return Template(id=f"{cat.id}-{n:03d}", category=cat.id, index=n, shape=shape, palette=palette)


def _build() -> dict[str, list[Template]]:
    result: dict[str, list[Template]] = {}
    g = 0
    for cat in CATEGORIES:
        items = []
        for n in range(1, cat.count + 1):
            items.append(_make(g, cat, n))
            g += 1
        result[cat.id] = items
    return result


_TEMPLATES: dict[str, list[Template]] = _build()
_BY_TEMPLATE_ID: dict[str, Template] = {t.id: t for items in _TEMPLATES.values() for t in items}

TOTAL = len(_BY_TEMPLATE_ID)


def _cover(category_id: str) -> str:
    items = _TEMPLATES[category_id]
    shape = COVER_SHAPES.get(category_id)
    return next((t.id for t in items if SHAPE_NAMES[t.shape] == shape), items[0].id)


def categories() -> list[dict]:
    # cover — шаблон для плитки категории на главной
    return [{"id": c.id, "name": c.name, "count": c.count, "cover": _cover(c.id)} for c in CATEGORIES]


def get(template_id: str) -> Template | None:
    return _BY_TEMPLATE_ID.get(template_id)


def exists_all(template_ids: list[str]) -> bool:
    return all(t in _BY_TEMPLATE_ID for t in template_ids)


def page(category_id: str, page_no: int, size: int = PAGE_SIZE) -> dict | None:
    """Страница шаблонов категории или None, если категории нет."""
    if category_id not in _BY_ID_CATEGORY:
        return None
    size = max(1, min(size, 60))
    items = _TEMPLATES[category_id]
    pages = max(1, math.ceil(len(items) / size))
    page_no = max(1, min(page_no, pages))
    chunk = items[(page_no - 1) * size : page_no * size]
    return {
        "category": category_id,
        "page": page_no,
        "pages": pages,
        "total": len(items),
        "items": [{"id": t.id, "name": t.name} for t in chunk],
    }
