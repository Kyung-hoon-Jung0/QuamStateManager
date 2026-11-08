"""Diagnostics review round: the drive-LO fix says what it moves, a shared
output never gets a conflicting "Set all", every ackable check has a value
source, environment errors are never acknowledged, grouping keeps meaningful
tokens, stale acknowledgements are pruned, and the sidebar dot follows every
state change."""

from __future__ import annotations

import ast
from pathlib import Path

from quam_state_manager.core import diag_ack
from quam_state_manager.core import diagnostics as diag
from quam_state_manager.core.diagnostics import Finding

from tests.test_diag_group_ack import _freq_keys, loaded  # noqa: F401 -- fixture

ROOT = Path(__file__).resolve().parents[1]


def _shared_output_root(dc_a, dc_b):
    """Two readout inputs paired with ONE output (a shared output line)."""
    out = "#/ports/mw_outputs/con1/1/1"
    return {
        "ports": {
            "mw_outputs": {"con1": {"1": {"1": {"upconverter_frequency": 7.0e9}}}},
            "mw_inputs": {"con1": {"1": {"1": {"downconverter_frequency": dc_a},
                                         "2": {"downconverter_frequency": dc_b}}}},
        },
        "qubits": {"q1": {"resonator": {"opx_output": out}},
                   "q2": {"resonator": {"opx_output": out}}},
        "wiring": {"qubits": {
            "q1": {"rr": {"opx_input": "#/ports/mw_inputs/con1/1/1", "opx_output": out}},
            "q2": {"rr": {"opx_input": "#/ports/mw_inputs/con1/1/2", "opx_output": out}}}},
    }


def _warn(root):
    return [f for f in diag._downconverter_findings(root) if f.severity == "warning"]


def test_reverse_lo_fix_names_the_port_and_what_else_moves():
    rev = [x for x in _warn(_shared_output_root(7.1e9, 7.1e9))[0].fixes
           if x["kind"] == "up_to_down"][0]
    assert "changes the drive LO of con1/FEM 1/port 1" in rev["label"]
    assert rev["warns"].startswith("changes the drive LO of con1/FEM 1/port 1 -- also moves: ")
    assert "qubits.q1.resonator" in rev["warns"] and "qubits.q2.resonator" in rev["warns"]


def test_reverse_lo_wording_is_on_screen_and_in_the_toast():
    tpl = (ROOT / "quam_state_manager/web/templates/_diagnostics_list.html").read_text(encoding="utf-8")
    assert 'class="diag-fix-warns' in tpl and "data-warns=" in tpl
    js = (ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
    assert 'btn.getAttribute("data-warns")' in js and "_diagLanded(d, warns)" in js


def test_shared_output_same_value_is_one_write():
    rows = _warn(_shared_output_root(7.1e9, 7.1e9))
    assert len(rows) == 2
    g = diag.finding_groups(rows)
    assert len(g) == 1
    kinds = {k["kind"]: k for k in g[0]["bulk"]}
    assert "up_to_down" not in kinds  # one target after dedupe: the per-item button is the action
    assert len(kinds["down_to_up"]["items"]) == 2
    assert not g[0]["bulk_refused"]


def test_shared_output_different_values_get_no_set_all():
    g = diag.finding_groups(_warn(_shared_output_root(7.1e9, 7.2e9)))
    assert len(g) == 1
    assert "up_to_down" not in {k["kind"] for k in g[0]["bulk"]}
    refused = {k["kind"]: k for k in g[0]["bulk_refused"]}
    assert "different values" in refused["up_to_down"]["why"]


def test_server_refuses_a_batch_with_conflicting_values(loaded):  # noqa: F811
    client, store = loaded["client"], loaded["store"]
    items = [f.fixes[0] for f in _freq_keys(loaded)]
    clash = dict(items[0], value=repr(float(items[0]["value"]) + 1.0))
    before = len(store.change_log)
    r = client.post("/diagnostics/apply-fix", json={"items": [items[0], clash]})
    assert r.status_code == 400 and r.get_json()["error_kind"] == "conflicting_values"
    assert len(store.change_log) == before


def test_server_dedupes_identical_items(loaded):  # noqa: F811
    items = [f.fixes[0] for f in _freq_keys(loaded)]
    r = loaded["client"].post("/diagnostics/apply-fix", json={"items": [items[0], dict(items[0])]})
    assert r.status_code == 200 and len(r.get_json()["changed"]) == 1


def test_every_ackable_check_has_a_value_source():
    """Every non-error Finding(...) the linters build names the leaves it read
    (jump_path / fix / value_paths) or its non-chip inputs (values)."""
    bad = []
    for rel in ("quam_state_manager/core/diagnostics.py", "quam_state_manager/web/routes.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name != "Finding":
                continue
            kw = {k.arg: k.value for k in node.keywords}
            sev = node.args[0] if node.args else kw.get("severity")
            if isinstance(sev, ast.Constant) and sev.value == "error":
                continue
            if not set(kw) & {"jump_path", "fix", "fixes", "value_paths", "values"}:
                bad.append(f"{rel}:{node.lineno}")
    assert not bad, bad


def test_a_text_only_finding_is_not_ackable_and_values_drive_the_fingerprint():
    f = Finding("warning", "x_check", "somewhere", "text only")
    assert not diag_ack.ackable(f)
    f.values = {"n": 1}
    assert diag_ack.ackable(f)
    fp1 = diag_ack.fingerprint(f)
    f.values = {"n": 2}
    assert diag_ack.fingerprint(f) != fp1


def test_kind_text_keeps_meaningful_tokens_and_blanks_chip_ids():
    ids = {"q1", "q2", "q1-q2", "con1"}
    t1a = diag._kind_text("q1: T1 (12.5 us) exceeds the bound. See qubits.q1.T1", ids)
    t1b = diag._kind_text("q2: T1 (30 us) exceeds the bound. See qubits.q2.T1", ids)
    t2 = diag._kind_text("q1: T2 (12.5 us) exceeds the bound. See qubits.q1.T2", ids)
    assert t1a == t1b and t1a != t2
    assert diag._kind_text("x90 length", ids) != diag._kind_text("x180 length", ids)
    assert diag._kind_text("q1: bad. Fix A", ids) != diag._kind_text("q1: bad. Fix B", ids)


def test_ack_on_a_place_that_left_the_chip_is_pruned(loaded):  # noqa: F811
    inst, store = loaded["inst"], loaded["store"]
    diag_ack._save(inst, "ck", {"k1": {"fp": "x", "paths": ["qubits.qZZ.f_01"]},
                                "k2": {"fp": "y", "paths": ["qubits.q1.f_01"]},
                                "k3": {"fp": "z", "paths": []}})
    assert diag_ack.prune(inst, "ck", store) == 1
    assert set(diag_ack.load(inst, "ck")) == {"k2", "k3"}
    assert diag_ack.pruned_count(inst, "ck") == 1


def test_env_error_ack_is_never_applied():
    from quam_state_manager.core import state_env_validate as sev
    rec = {"kind": "unknown_field", "class": "a.B", "field": "f", "severity": "error", "detail": "d"}
    key = sev._ack_key(rec)
    out = sev.to_diag_findings({"findings": [rec]}, acknowledged={key: {"detail": "x", "at": 1}})
    assert out[0].severity == "error" and not out[0].acknowledged
    kept, n = sev.unacknowledged_rows([rec], {key: {"detail": "x"}}, "")
    assert n == 0 and len(kept) == 1


def test_env_ack_button_is_hidden_on_errors():
    for name in ("_diagnostics_list.html", "_diagnostics_types.html"):
        tpl = (ROOT / "quam_state_manager/web/templates" / name).read_text(encoding="utf-8")
        before = tpl.split('onclick="envAck(this)"')[0][-900:]
        assert "!= 'error'" in before, name


def test_env_ack_route_refuses_an_error(loaded, monkeypatch):  # noqa: F811
    from quam_state_manager.web import routes
    f = Finding("error", "env_unknown_field", "B.f", "m", ack_key="unknown_field|a.B|f|")
    monkeypatch.setattr(routes, "_env_schema_findings", lambda store, **k: [f])
    monkeypatch.setattr(routes, "_env_ack_key_now", lambda store: "envk")
    r = loaded["client"].post("/env-ack", data={"kind": "unknown_field", "class_path": "a.B",
                                                "field": "f", "detail": "m"})
    assert r.status_code == 409 and r.get_json()["error_kind"] == "error_not_ackable"


def test_sidebar_dots_follow_undo_sync_and_other_windows():
    js = (ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
    i = js.index("var DIAG_DOT_EVENTS")
    block = js[i:js.index("];", i)]
    for ev in ("diagnostics-changed", "cellsReverted", "stateRestored", "liveDriftChanged"):
        assert "'" + ev + "'" in block, ev
