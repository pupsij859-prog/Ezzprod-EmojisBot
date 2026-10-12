"""Генератор фоновых SVG: топографические контуры и слоистые хребты (мотив логотипа).
Запуск: python tools/gen_bg.py  → webapp/assets/bg-topo.svg, bg-ridges.svg"""
from __future__ import annotations

import math
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "webapp" / "assets"


def topo(seed: int = 7, w: int = 480, h: int = 520) -> str:
    rnd = random.Random(seed)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" fill="none">']
    centers = [(w * 0.82, h * 0.22, 8), (w * 0.12, h * 0.62, 6), (w * 0.55, h * 0.95, 5)]
    for cx, cy, rings in centers:
        phases = [(k, rnd.uniform(0, math.tau), rnd.uniform(0.04, 0.11) / (k * 0.6)) for k in (2, 3, 5)]
        for i in range(1, rings + 1):
            base = 26 + i * 24
            pts = []
            for s in range(0, 96):
                t = s / 96 * math.tau
                wobble = 1 + sum(a * math.sin(k * t + ph + i * 0.35) for k, ph, a in phases)
                r = base * wobble
                pts.append(f"{cx + r * math.cos(t):.1f},{cy + r * math.sin(t) * 0.82:.1f}")
            major = i % 4 == 0
            parts.append(
                f'<polygon points="{" ".join(pts)}" stroke="#a3a2d6" stroke-opacity="{0.16 if major else 0.075}" '
                f'stroke-width="{1.1 if major else 0.8}" stroke-linejoin="round"/>'
            )
    parts.append("</svg>")
    return "".join(parts)


def _profile(rnd: random.Random, n: int = 129, rough: float = 0.56) -> list[float]:
    """Горный профиль методом срединного смещения: значения 0..1."""
    pts = {0: rnd.random(), n - 1: rnd.random()}
    amp, step = 1.0, n - 1
    while step > 1:
        half = step // 2
        for i in range(half, n - 1, step):
            pts[i] = (pts[i - half] + pts[i + half]) / 2 + rnd.uniform(-amp, amp) * 0.5
        amp *= rough
        step = half
    ys = [pts[i] for i in range(n)]
    lo, hi = min(ys), max(ys)
    return [(y - lo) / (hi - lo or 1) for y in ys]


def ridges(seed: int = 21, w: int = 480, h: int = 260) -> str:
    rnd = random.Random(seed)
    layers = [  # (линия подножия, размах пиков, цвет, прозрачность): дальние светлее, ближние темнее
        (0.62, 70, "#232336", 0.5),
        (0.74, 58, "#1a1a29", 0.7),
        (0.86, 44, "#121220", 0.9),
        (0.96, 26, "#0b0b14", 1.0),
    ]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" preserveAspectRatio="xMidYMax slice">']
    for base, amp, color, op in layers:
        prof = _profile(rnd)
        pts = [(0, h)] + [(i / (len(prof) - 1) * w, h * base - p * amp) for i, p in enumerate(prof)] + [(w, h)]
        d = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        parts.append(f'<polygon points="{d}" fill="{color}" fill-opacity="{op}"/>')
    parts.append("</svg>")
    return "".join(parts)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "bg-topo.svg").write_text(topo(), encoding="utf-8")
    (OUT / "bg-ridges.svg").write_text(ridges(), encoding="utf-8")
    print("готово:", OUT)
