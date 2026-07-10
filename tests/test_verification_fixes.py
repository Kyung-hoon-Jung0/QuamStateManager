"""Fixes from the final multi-role verification round (Generate / Diagnostics /
Instrument Wiring agents): validate_spec hardening, the dangling-pointer
soft/hard split (DragCosine optional-default false positives), instrument
non-object-body 400, and tolerant FEM/port id sorting.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core.config_generator import validate_spec
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core import diagnostics
from quam_state_manager.web import routes as R
from quam_state_manager.web.app import create_app


def _spec(**over):
    s = {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [
            {"con": 1, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}]}],
            "opx_plus": [], "octaves": []},
        "qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "twpas": [],
        "lines": [], "populate": {},
    }
    s.update(over)
    return s


# ── validate_spec hardening (Generate agent P3s) ──────────────────────────
def test_self_coupled_pair_rejected():
    errs = validate_spec(_spec(qubit_pairs=[["q1", "q1"]]))
    assert any("must differ" in e for e in errs), errs


def test_unknown_pair_gate_rejected():
    errs = validate_spec(_spec(pair_gate="; rm -rf /"))
    assert any("pair_gate" in e for e in errs), errs
    assert validate_spec(_spec(pair_gate="cz_tunable")) == []
    assert validate_spec(_spec(pair_gate="cr")) == []


def test_bool_not_accepted_as_int():
    # con: true would have passed (isinstance(True, int)); now rejected.
    s = _spec()
    s["instruments"]["controllers"][0]["con"] = True
    assert any("con: must be an integer" in e for e in validate_spec(s)), validate_spec(s)


# ── Diagnostics: dangling-pointer soft/hard split (P0) ─────────────────────
def _store_with_pointers():
    # x180 EXISTS (so #../x180/missing_field is a SOFT optional-default omission);
    # #../ghost/foo points at a non-existent op (HARD dangling).
    state = {
        "qubits": {
            "q1": {"xy": {"operations": {
                "x180": {"length": 40},
                "x90": {
                    "length": 20,
                    "detuning": "#../x180/detuning",   # soft: x180 exists, detuning omitted
                    "broken": "#../ghost/foo",         # hard: ghost op doesn't exist
                },
            }}},
        },
    }
    return QuamStore.from_dicts(state, {})


def test_soft_pointer_is_advisory_not_error():
    store = _store_with_pointers()
    pw = store.validate_pointers()
    soft = {w.pointer for w in pw if getattr(w, "soft", False)}
    hard = {w.pointer for w in pw if not getattr(w, "soft", False)}
    assert "#../x180/detuning" in soft
    assert "#../ghost/foo" in hard

    findings = diagnostics.lint_state(store)
    dang_err = [f for f in findings if f.category == "dangling_pointer" and f.severity == "error"]
    soft_adv = [f for f in findings if f.category == "pointer_optional_default"]
    # the soft one is a collapsed advisory (no crash banner); the hard one errors
    assert any("ghost" in (f.detail or "") for f in dang_err)
    assert all("x180/detuning" not in (f.detail or "") for f in dang_err)
    assert any(getattr(f, "advisory", False) for f in soft_adv)


# ── Instrument: non-object JSON body → 400, not 500 ────────────────────────
@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.mark.parametrize("url", ["/instrument/preview", "/instrument/compare"])
def test_instrument_non_object_body_is_400(client, url):
    for body in ("[1,2,3]", '"x"', "42"):
        r = client.post(url, data=body, content_type="application/json")
        assert r.status_code != 500, f"{url} {body} → {r.status_code}"


# ── query.py: a non-numeric FEM/port id must not blank the whole diagram ───
def test_instrument_wiring_tolerates_nonnumeric_port():
    from quam_state_manager.core.query import QueryEngine
    # a wiring with a legacy/odd non-digit port id alongside numeric ones
    state = {"qubits": {"q1": {"xy": {
        "opx_output": "#/ports/mw_outputs/con1/1/ABC",
        "operations": {}}}}}
    wiring = {"ports": {"mw_outputs": {"con1": {"1": {"ABC": {}, "2": {}}}}}}
    store = QuamStore.from_dicts(state, wiring)
    # must not raise (was: ValueError from sorted(..., key=int) blanking the grid)
    out = QueryEngine(store).get_instrument_wiring()
    assert isinstance(out, dict)
