"""Re-generate review and result wording.

- The two timing questions arrive as separate ``questions`` (question + one
  explanation line each); only the ones that apply.
- A moved value in a result sentence uses the one before/after rendering
  (value_delta.describe_moved), never an ASCII arrow; a missing or
  non-numeric old value gives no arrow and no invented delta.
- A Re-generate keeps the source chip's cores: a spec that asked for shared
  cores gets one sentence in the core layout block saying why; a spec that did
  not gets none.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core import regenerate, value_delta
from tests.test_regen_mw_band_shift import _chip, _route


# -- describe_moved ---------------------------------------------------------

@pytest.mark.parametrize("old,new,want", [
    (32, 52, "32 → 52 ns (Δ +20)"),
    (52, 52, "52 ns, unchanged"),
    (None, 52, "now 52 ns"),
    ("abc", 52, "now 52 ns"),
    (True, 52, "now 52 ns"),
])
def test_describe_moved(old, new, want):
    assert value_delta.describe_moved(old, new, "ns") == want


def test_describe_moved_never_ascii_arrow():
    for old, new in ((1, 2), (None, 2), (2, 2), (1.5, "x")):
        assert "->" not in value_delta.describe_moved(old, new, "ns")


# -- kept_cores_note: the enumeration -----------------------------------------

@pytest.mark.parametrize("spec,want", [
    ({"populate": {"options": {"pin_cores": True}}}, True),
    ({"populate": {"options": {"pin_cores": None}}}, True),      # absent / null is on (the build's rule)
    ({"populate": {"options": {}}}, True),
    ({}, True),
    ({"populate": {"options": {"pin_cores": False}}}, False),
    (None, False),
])
def test_kept_cores_note(spec, want):
    got = regenerate.kept_cores_note(spec)
    assert (got == regenerate.KEPT_CORES_NOTE) if want else got is None


def test_kept_cores_note_text_is_clean():
    t = regenerate.KEPT_CORES_NOTE
    assert t[0].isupper() and "->" not in t and " -- " not in t


# -- route: questions ---------------------------------------------------------

def test_two_questions_are_separate(tmp_path, monkeypatch):
    state, wiring = _chip()
    state["ports"]["analog_outputs"]["con1"]["3"]["9"]["delay"] = 0
    client, body = _route(tmp_path, monkeypatch, (state, wiring))
    res = client.post("/regenerate/build", json=body).get_json()
    qs = res["questions"]
    assert [q["kind"] for q in qs] == ["coupler_delay", "mw_shift"]
    for q in qs:
        assert q["question"].endswith("?") and q["question"].count("?") == 1
        assert q["why"] and "?" not in q["why"]


def test_one_question_when_only_the_shift_applies(tmp_path, monkeypatch):
    client, body = _route(tmp_path, monkeypatch, _chip())
    res = client.post("/regenerate/build", json=body).get_json()
    assert [q["kind"] for q in res["questions"]] == ["mw_shift"]


# -- route: moved values and the kept-cores sentence --------------------------

def test_shift_result_uses_the_delta_rendering(tmp_path, monkeypatch):
    client, body = _route(tmp_path, monkeypatch, _chip(ro_band=3, tof=32))
    body["mw_shift_ack"] = "shift"
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True
    text = " ".join(res["result"]["warnings"])
    assert "Time of flight: q1 32 → 52 ns (Δ +20)" in text
    assert "->" not in text


@pytest.mark.parametrize("pin,want", [(True, True), (False, False)])
def test_route_kept_cores_sentence(tmp_path, monkeypatch, pin, want):
    client, body = _route(tmp_path, monkeypatch, _chip())
    body["mw_shift_ack"] = "keep"
    body["spec"]["populate"] = {"options": {"pin_cores": pin}}
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True
    cl = res.get("core_layout")
    warnings = " ".join(res["result"].get("warnings") or [])
    shown = (isinstance(cl, dict) and cl.get("kept_note") == regenerate.KEPT_CORES_NOTE) \
        or regenerate.KEPT_CORES_NOTE in warnings
    assert shown is want
