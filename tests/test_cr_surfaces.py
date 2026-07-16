"""Corpus invariants: every surface must render/behave on every CR schema
flavor (docs/54) — and the CZ reference must stay untouched.

Parametrized over the committed synthetic corpus (tests/cr_fixtures.py):
flavor A (lo_if / dedicated ports), flavor B (rf / shared ports, both
directions), sparse B (CR_state serialization), flavor C (provisional tip),
with-ZZ B, and the CZ reference. Real-artifact twins live in
tests/test_cr_real_artifacts.py (path-gated).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

sys.path.insert(0, str(Path(__file__).parent))
from cr_fixtures import (  # noqa: E402
    make_cz_reference,
    make_flavor_a,
    make_flavor_b,
    make_flavor_c,
    write_folder,
)

_MAKERS = {
    "flavor_a": make_flavor_a,
    "flavor_b": lambda: make_flavor_b(),
    "flavor_b_sparse": lambda: make_flavor_b(sparse=True),
    "flavor_b_zz": lambda: make_flavor_b(with_zz=True),
    "flavor_c": make_flavor_c,
    "cz_reference": make_cz_reference,
}


@pytest.fixture(params=list(_MAKERS))
def chip(request, tmp_path):
    state, wiring = _MAKERS[request.param]()
    folder = write_folder(tmp_path / "chip", state, wiring)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    resp = client.post("/load", data={"folder": str(folder)})
    assert resp.status_code in (200, 302), request.param
    return {"client": client, "state": state, "kind": request.param}


_PAIR_OF = {
    "flavor_a": "q0-1", "flavor_b": "q0-1", "flavor_b_sparse": "q0-1",
    "flavor_b_zz": "q0-1", "flavor_c": "q0-1", "cz_reference": "q0-q1",
}


def test_every_surface_renders(chip):
    c = chip["client"]
    pid = _PAIR_OF[chip["kind"]]
    for url in ("/qubits", "/pairs", f"/pair/{pid}", "/qubit/q0",
                "/bulk", "/pulses", "/api/topology", "/instrument",
                f"/pair/{pid}/gate/new"):
        resp = c.get(url, headers={"HX-Request": "true"})
        assert resp.status_code == 200, f"{chip['kind']}: {url} -> {resp.status_code}"


def test_no_phantom_editable_sections(chip):
    """Every editable row in the DYNAMIC CR/ZZ/gate sections must point at a
    path that exists in the state (the phantom-Cr regression class — Apply
    would 400). Scoped to the cr_semantics-built sections: the legacy static
    map intentionally shows absent-but-editable rows like moving_qubit
    (pre-existing behavior, out of scope here)."""
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.web import routes
    from quam_state_manager.core.query import QueryEngine

    state = chip["state"]
    pid = _PAIR_OF[chip["kind"]]
    # rebuild a store directly (the route already rendered 200 above)
    store = QuamStore.from_dicts(state, {"wiring": {}, "network": {}})
    engine = QueryEngine(store)
    sections = routes._build_pair_sections(pid, engine.get_pair(pid), store)
    names = [s["name"] for s in sections]
    assert "Cr" not in names                    # the original phantom section
    dyn = ("Cross Resonance", "ZZ Drive", "XY Detuned")
    for sec in sections:
        if sec["name"] not in dyn and not sec["name"].endswith("Gate"):
            continue
        for prop in sec["props"]:
            if not prop["editable"] or not prop["dot_path"]:
                continue
            store.get_value(prop["dot_path"])   # raises KeyError if phantom
    if chip["kind"] != "cz_reference":
        assert "Cross Resonance" in names


def test_pulses_rows_match_channels(chip):
    from quam_state_manager.core.pulse_index import PAIR_PULSE_CHANNELS, list_pulses

    state = chip["state"]
    rows = list_pulses(state)
    pair_drive_rows = [r for r in rows if r["channel"] in PAIR_PULSE_CHANNELS]
    if chip["kind"] == "cz_reference":
        assert pair_drive_rows == []
    else:
        assert pair_drive_rows, "CR chips must expose their drive pulses"
        for r in pair_drive_rows:
            assert r["path"].split(".")[3] == "operations"


def test_topology_edges_flavor_aware(chip):
    c = chip["client"]
    topo = c.get("/api/topology").get_json()
    edges = {e["pair_id"]: e for e in topo["edges"]}
    pid = _PAIR_OF[chip["kind"]]
    e = edges[pid]
    if chip["kind"] == "cz_reference":
        assert e["gate_kind"] == "cz" and e["directed"] is False
        assert topo["summary"]["gate_vocab"] == "CZ"
    else:
        assert e["gate_kind"] == "cr" and e["directed"] is True
        assert topo["summary"]["gate_vocab"] == "CR"
        assert e["edge_key"] == sorted([e["source"], e["target"]])
    if chip["kind"] == "flavor_a":
        # channel bell_state_fidelity feeds the edge metric (macro fid is null)
        assert edges["q1-2"]["cz_fidelity"] == 0.93
        assert edges["q1-2"]["fidelity_source"] == "channel"


def test_add_gate_form_arch_gated(chip):
    c = chip["client"]
    pid = _PAIR_OF[chip["kind"]]
    html = c.get(f"/pair/{pid}/gate/new").data.decode()
    # assert on the OPTION values — the template's static preview JS always
    # contains every branch's literal text regardless of gating
    if chip["kind"] == "cz_reference":
        assert 'value="cz_unipolar"' in html
        assert 'value="cr_gate"' not in html
    else:
        assert 'value="cr_gate"' in html
        assert 'value="cz_unipolar"' not in html
        if chip["kind"] == "flavor_b_zz":
            assert 'value="stark_cz"' in html
        else:
            assert 'value="stark_cz"' not in html


def test_diagnostics_flag_only_the_invalid_pair(chip):
    """The deliberately-invalid q2-1 pair (|IF| = 450 MHz) warns; every other
    pair on every flavor is silent (the zero-false-positive rule)."""
    from quam_state_manager.core import diagnostics
    from quam_state_manager.core.loader import QuamStore

    state, wiring = _MAKERS[chip["kind"]]()
    store = QuamStore.from_dicts(state, wiring)
    findings = [f for f in diagnostics.lint_state(store)
                if f.category.startswith("pair_drive_if")]
    if chip["kind"] in ("flavor_b", "flavor_b_sparse", "flavor_b_zz"):
        assert [f.jump_path for f in findings] == \
            ["qubit_pairs.q2-1.cross_resonance.intermediate_frequency"]
        assert findings[0].severity == "warning"      # 450 MHz: soft tier
    else:
        assert findings == []
