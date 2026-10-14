"""Рендер одного элемента пака: шаблон TGS + ник + цвет надписи + логотип → файл .tgs для Telegram.

Требования Telegram к анимированным эмодзи и стикерам: Lottie 512×512, 60 к/с, до 3 с, без картинок и
текстовых слоёв, после gzip — не больше 64 КБ. Шаблоны уже им соответствуют; ник превращаем в контуры букв
(app/fonts.py), логотип — в векторные фигуры (app/vectorize.py). Правки продвинутого редактора (Pro) применяет
app/editor.py. Если элемент не влезает в 64 КБ, пробуем варианты логотипа и эффектов попроще.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .. import catalog, editor, fonts, lottie
from ..logos import load_vectors


class TooBig(Exception):
    pass


@dataclass(frozen=True)
class ItemSpec:
    template_id: str
    kind: str                     # 'emoji' | 'sticker' — формат файла у обоих один (TGS 512)
    text: str
    color: str | None             # заливка '#rrggbb' или None — родной цвет шаблона
    logo_vectors: tuple = field(default=())  # варианты векторного логотипа, от подробного к грубому
    stroke: str | None = None     # обводка '#rrggbb' или None — родной цвет контуров
    size: int = 100               # размер надписи, % (100 — автоподбор)
    edit_json: str = ""           # правки редактора (JSON) или пусто


class Renderer(Protocol):
    def render(self, spec: ItemSpec) -> bytes:
        """Байты .tgs, готовые к загрузке в Telegram. Бросает исключение, если элемент собрать нельзя."""
        ...


class TemplateRenderer:
    def render(self, spec: ItemSpec) -> bytes:
        tpl = catalog.get(spec.template_id)
        if tpl is None:
            raise ValueError(f"unknown template {spec.template_id}")
        anim = lottie.load(tpl.path)
        text = fonts.text_drawing(spec.text) if spec.text else None
        color = lottie.hex_to_rgb(spec.color)
        stroke = lottie.hex_to_rgb(spec.stroke)
        edit = json.loads(spec.edit_json) if spec.edit_json else None
        data = b""
        for logo in (spec.logo_vectors or (None,)):
            base = lottie.personalize(anim, color=color, stroke=stroke, text=text, logo=logo,
                                      size=lottie.text_size(spec.size))
            if edit:
                _, data = editor.render(base, edit)
            else:
                data = lottie.encode(base)
            if len(data) <= lottie.TG_LIMIT:
                return data
        raise TooBig(f"{len(data)} bytes")


def logo_vectors(logos_dir: Path, logo_id: str | None) -> tuple:
    return tuple(load_vectors(logos_dir, logo_id)) if logo_id else ()
