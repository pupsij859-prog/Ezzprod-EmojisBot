"""Градиент на ник, логотип и заливку (app/lottie.py → clean_grad, personalize)."""
import json

import pytest

from app import catalog, fonts, lottie, services
from app.services import ValidationFailed

GRAD = {"a": "#1E6FFF", "b": "#8fd8ff", "angle": 495, "on": ["fill", "text"]}


def test_clean_grad():
    assert lottie.clean_grad(GRAD) == {"a": "#1e6fff", "b": "#8fd8ff", "angle": 135, "on": ["text", "fill"]}
    assert lottie.clean_grad(None) is None
    assert lottie.clean_grad({"a": "#000000", "b": "#ffffff", "on": []}) is None  # никуда — выключен
    for bad in ({"a": "red", "b": "#000000", "on": ["text"]}, {"a": "#000000", "b": "#000000", "on": ["x"]},
                {"a": "#000000", "b": "#000000", "on": ["text"], "angle": "1"}, {"a": "#000000"}, ["text"]):
        with pytest.raises(ValueError):
            lottie.clean_grad(bad)


def _grads(o, found):
    if isinstance(o, dict):
        if o.get("ty") == "gf" and o.get("nm") == lottie.GRAD_NAME:
            found.append(o)
        for v in o.values():
            _grads(v, found)
    elif isinstance(o, list):
        for v in o:
            _grads(v, found)
    return found


def test_personalize_grad_keeps_text_slot():
    anim = lottie.load(catalog.get("classic-031").path)
    grad = lottie.clean_grad({"a": "#000000", "b": "#ffffff", "on": ["text"]})
    out = lottie.personalize(anim, color=None, stroke=None, text=fonts.text_drawing("nick"), logo=None, grad=grad)
    assert _grads(out, [])
    assert lottie.word_groups(out)  # заливка «ezzprod color» на месте: надпись по-прежнему находится
    assert len(lottie.encode(out)) <= lottie.TG_LIMIT
    plain = lottie.personalize(anim, color=None, stroke=None, text=None, logo=None)
    assert not _grads(plain, [])
    # заливка под градиентом прозрачная — иначе её край даёт светлую кайму вокруг букв
    assert all(f["o"]["k"] == 0 for f in lottie.text_fills(out))
    lottie.drop_text_grads(out)
    assert not _grads(out, [])
    assert all(f["o"]["k"] == 100 for f in lottie.text_fills(out))


def test_personalize_grad_on_fill_and_native_letters():
    anim = lottie.load(catalog.get("classic-001").path)
    grad = lottie.clean_grad({"a": "#ff0000", "b": "#0000ff", "angle": 90, "on": ["fill", "text"]})
    out = lottie.personalize(anim, color=None, stroke=None, text=None, logo=None, grad=grad)
    assert len(_grads(out, [])) > 1
    json.dumps(out)


def test_pack_request_accepts_grad(settings):
    req = services.validate_pack_request(settings, {"type": "emoji", "template_ids": ["classic-001"], "grad": GRAD,
                                                    "overrides": {"classic-001": {"grad": None}}})
    assert req["grad"]["on"] == ["text", "fill"]
    assert req["overrides"] == {"classic-001": {"grad": None}}
    with pytest.raises(ValidationFailed):
        services.validate_pack_request(settings, {"type": "emoji", "template_ids": ["classic-001"], "grad": {"a": 1}})


def test_new_templates_have_logo_slot_for_nick():
    """Шаблоны NEW (tools/import_templates.py): логотип «iD» размечен как место под ник."""
    new = [t for t in catalog.by_category("new")] if hasattr(catalog, "by_category") else []
    ids = [t.id for t in new] or [f"new-{n:03d}" for n in range(1, 21)]
    nick = fonts.text_drawing("nick")
    for tid in ids:
        anim = lottie.load(catalog.get(tid).path)
        assert lottie.word_groups(anim), tid
        out = lottie.personalize(anim, color=None, stroke=None, text=nick, logo=None)
        assert any(g.get("nm") == lottie.NICK_LAYER for g in _groups(out)), tid
        assert len(lottie.encode(out)) <= lottie.TG_LIMIT, tid


def _groups(o, found=None):
    found = [] if found is None else found
    if isinstance(o, dict):
        if o.get("ty") == "gr":
            found.append(o)
        for v in o.values():
            _groups(v, found)
    elif isinstance(o, list):
        for v in o:
            _groups(v, found)
    return found
