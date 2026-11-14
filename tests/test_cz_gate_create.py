"""The one rule "may this gate be created with this check result": every branch."""
import pytest

from quam_state_manager.core import cz_gate_create as cc
from quam_state_manager.core.cz_gate_build import CZ_GATE

LAB = "lab_pkg.gates.MyGate"


def _entry(ops="pass", load="pass", other="pass", ran=None):
    checks = {"load_config": {"status": load, "message": "load msg"},
              "ops": {"status": ops, "message": "ops msg"},
              "range": {"status": other, "message": "range msg"}}
    if ran is None:
        ran = load in ("pass", "note") and ops in ("pass", "note")
    return {"checks": checks, "ran": ran}


OK = {"status": "ok", "checked": True}


def test_verified():
    assert cc.may_create(OK, _entry(), gate_class=CZ_GATE, layout="known") == (True, cc.VERIFIED, "")
    assert cc.may_create(OK, _entry(), gate_class=LAB, layout="copied") == (True, cc.VERIFIED, "")


@pytest.mark.parametrize("layout,cls", [("known", CZ_GATE), ("copied", LAB)])
def test_a_failed_check_refuses(layout, cls):
    allowed, state, why = cc.may_create(OK, _entry(other="fail"), gate_class=cls, layout=layout)
    assert not allowed and why == "range msg"


def test_failed_gate_run_refuses_the_known_class_too():
    allowed, _, why = cc.may_create(OK, _entry(ops="fail"), gate_class=CZ_GATE, layout="known")
    assert not allowed and why == "ops msg"


def test_invalid_create_refuses():
    res = {"status": "invalid-create", "checked": False, "error": "the path exists"}
    allowed, _, why = cc.may_create(res, None, gate_class=CZ_GATE, layout="known")
    assert not allowed and "path exists" in why


@pytest.mark.parametrize("status", ["no-env", "timeout", "crash", "busy"])
def test_could_not_run_allows_only_a_known_class(status):
    res = {"status": status, "checked": False, "error": "detail"}
    e = _entry(ops="skipped", load="skipped")
    allowed, state, why = cc.may_create(res, e, gate_class=CZ_GATE, layout="known")
    assert allowed and state == cc.NOT_CHECKED and why
    allowed, state, why = cc.may_create(res, e, gate_class=LAB, layout="copied")
    assert not allowed and "copies the layout" in why


def test_chip_itself_does_not_load():
    res = {"status": "ok", "checked": False, "chip_error": "KeyError: x"}
    e = _entry(ops="skipped", load="skipped")
    allowed, state, why = cc.may_create(res, e, gate_class=CZ_GATE, layout="known")
    assert allowed and state == cc.NOT_CHECKED and "KeyError: x" in why
    assert not cc.may_create(res, e, gate_class=LAB, layout="copied")[0]


def test_program_unreadable_is_not_a_verified_run():
    e = _entry(ops="skipped")
    allowed, state, _ = cc.may_create(OK, e, gate_class=CZ_GATE, layout="known")
    assert allowed and state == cc.NOT_CHECKED
    assert not cc.may_create(OK, e, gate_class=LAB, layout="copied")[0]


def test_known_layout_needs_a_known_class():
    res = {"status": "no-env", "checked": False}
    assert not cc.may_create(res, None, gate_class=LAB, layout="known")[0]


def test_the_shared_outcome_cases_decide_the_create():
    import json
    from pathlib import Path
    cases = json.loads((Path(__file__).parent / "fixtures" / "gate_outcome_cases.json")
                       .read_text(encoding="utf-8"))["cases"]
    for name, want in [("checked", True), ("partly", True), ("failed", False), ("none", False)]:
        r = cases[name]["result"]
        # a copied layout: only a gate the lab's code loaded and ran may be written
        got = cc.may_create(r, r["gates"][0], gate_class="AnyClass", layout="copied")
        assert got[0] is want, name
