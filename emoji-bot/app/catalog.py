"""Каталог шаблонов: файлы .tgs из папки templates/ (имя вида «<категория>-<номер>.tgs»).

Новый шаблон — просто положить файл в папку и перезапустить бота. Категории без описания ниже тоже
появятся (с названием из имени файла), но в конце списка.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PAGE_SIZE = 30
ROOT = Path(__file__).resolve().parent.parent
FILE_RE = re.compile(r"^([a-z]+)-(\d{3})\.tgs\Z")


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    count: int
    emoji: str


# порядок, название и эмодзи-подпись (её Telegram показывает рядом с элементом пака)
KNOWN: dict[str, tuple[str, str]] = {
    "classic": ("классика", "✨"),
    "characters": ("персонажи", "😎"),
    "gift": ("подарки", "🎁"),
    "love": ("любовь", "❤️"),
    "finance": ("финансы", "💰"),
    "one": ("особые", "🔥"),
    "flags": ("флаги", "🚩"),
    "other": ("разное", "⭐"),
}


@dataclass(frozen=True)
class Template:
    id: str
    category: str
    index: int      # номер в имени файла
    path: Path

    @property
    def name(self) -> str:
        cat = KNOWN.get(self.category)
        return f"{cat[0] if cat else self.category} {self.index}"

    @property
    def emoji(self) -> str:
        return KNOWN.get(self.category, ("", "✨"))[1]


def templates_dir() -> Path:
    return Path(os.environ.get("TEMPLATES_DIR") or ROOT / "templates")


@lru_cache(maxsize=1)
def _scan(folder: str) -> tuple[tuple[Category, ...], dict[str, list[Template]]]:
    by_cat: dict[str, list[Template]] = {}
    base = Path(folder)
    if base.is_dir():
        for f in sorted(base.iterdir()):
            m = FILE_RE.match(f.name)
            if m and f.is_file():
                cat, n = m.group(1), int(m.group(2))
                by_cat.setdefault(cat, []).append(Template(f"{cat}-{n:03d}", cat, n, f))
    order = [c for c in KNOWN if c in by_cat] + sorted(c for c in by_cat if c not in KNOWN)
    cats = tuple(Category(c, KNOWN.get(c, (c, ""))[0], len(by_cat[c]), KNOWN.get(c, ("", "✨"))[1]) for c in order)
    return cats, {c: by_cat[c] for c in order}


def _data():
    return _scan(str(templates_dir()))


def reload() -> None:
    _scan.cache_clear()


def all_categories() -> tuple[Category, ...]:
    return _data()[0]


def total() -> int:
    return sum(c.count for c in all_categories())


def categories() -> list[dict]:
    # cover — шаблон для плитки категории
    items = _data()[1]
    return [{"id": c.id, "name": c.name, "count": c.count, "cover": items[c.id][0].id} for c in all_categories()]


def get(template_id: str) -> Template | None:
    m = re.match(r"^([a-z]+)-(\d{3})\Z", template_id or "")
    if not m:
        return None
    for t in _data()[1].get(m.group(1), ()):
        if t.id == template_id:
            return t
    return None


def exists_all(template_ids: list[str]) -> bool:
    return all(get(t) is not None for t in template_ids)


def page(category_id: str, page_no: int, size: int = PAGE_SIZE) -> dict | None:
    """Страница шаблонов категории или None, если категории нет."""
    items = _data()[1].get(category_id)
    if items is None:
        return None
    size = max(1, min(size, 60))
    pages = max(1, math.ceil(len(items) / size))
    page_no = max(1, min(page_no, pages))
    chunk = items[(page_no - 1) * size: page_no * size]
    return {
        "category": category_id,
        "page": page_no,
        "pages": pages,
        "total": len(items),
        "items": [{"id": t.id, "name": t.name} for t in chunk],
    }
