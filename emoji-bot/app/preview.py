"""Превью шаблонов (заглушки этапа 1): процедурные «3D»-плитки из app/placeholders.py.

Превью зависит только от шаблона и названия бренда (серверные данные), а не от пользовательского ввода:
так публичную ручку нельзя забить бесконечными вариантами, а текст пользователя в SVG не попадает.
Свой текст пользователь видит поверх карточки (рисует клиент).
"""
from __future__ import annotations

from functools import lru_cache

from . import catalog, placeholders


@lru_cache(maxsize=1024)
def render_svg(template: catalog.Template, brand: str = "EzzProd") -> str:
    shape = placeholders.SHAPE_ORDER[template.shape]
    _, fill, stroke = placeholders.PALETTES[template.palette]
    # id градиентов уникален на шаблон: SVG показываются через <img>, но так безопаснее, если их когда-то inline-ят
    return placeholders.svg_tile(shape, fill, stroke, brand[:12], uid=f"t{template.shape}{template.palette}")
