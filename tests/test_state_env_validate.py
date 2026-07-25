"""Tests for core/state_env_validate.analyze_state — the retrospective
state↔env validator — plus its diagnostics bridge and the /diagnostics
integration (env domain, card, probe endpoint's no-spawn contract).

The FP-budget corpus run (zero error-tier findings for a chip validated
against its own writing-generation env) lives in
test_type_corpus_idempotence.py's sibling test below-the-line here since it
shares the golden manifests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import state_env_validate as sev

# reuse the hand-made manifest + state from the type-policy tests
from tests.test_type_policy import MANIFEST, _state  # noqa: E402


def _findings(state, manifest=MANIFEST):
    return sev.analyze_state(state, manifest)["findings"]


def _by_kind(findings):
    out = {}
    for f in findings:
        out.setdefault(f["kind"], []).append(f)
    return out


class TestAnalyzeState:
    def test_clean_state_no_error_tier(self):
        res = sev.analyze_state(_state(), MANIFEST)
        errors = [f for f in res["findings"] if f["severity"] == "error"]
        # the otherlab_tools.Custom node IS an expected unimportable error
        assert all(f["kind"] == "unimportable_class" for f in errors)

    def test_unknown_field_error_aggregated(self):
        state = _state()
        for q in ("qA1",):
            state["qubits"][q]["duration_qubit"] = 1
        state["qubits"]["qA2"] = dict(state["qubits"]["qA1"])
        k = _by_kind(_findings(state))
        recs = k["unknown_field"]
        assert len(recs) == 1                     # aggregated, not per-node
        assert recs[0]["count"] == 2
        assert recs[0]["severity"] == "error"
        assert "AttributeError" in recs[0]["detail"]

    def test_extras_and_operations_children_never_flagged(self):
        state = _state()
        state["qubits"]["qA1"]["extras"]["totally_new"] = [1, 2, 3]
        state["qubits"]["qA1"]["xy"]["operations"]["brand_new_op"] = {
            "no_class_marker": 1}
        kinds = _by_kind(_findings(state))
        paths = [p for f in kinds.get("unknown_field", [])
                 for p in f["example_paths"]]
        assert not any("extras" in p or "brand_new_op" in p for p in paths)

    def test_type_mismatch_is_warning_tier(self):
        state = _state()
        state["qubits"]["qA1"]["f_01"] = "oops-a-string"
        k = _by_kind(_findings(state))
        rec = k["type_mismatch"][0]
        assert rec["severity"] == "warning"

    def test_pointer_and_inferred_refs_pass(self):
        state = _state()
        state["qubits"]["qA1"]["f_01"] = "#./inferred_f01"
        assert "type_mismatch" not in _by_kind(_findings(state))

    def test_missing_required_field(self):
        state = _state()
        del state["qubits"]["qA1"]["xy"]["operations"]["x180"]["amplitude"]
        k = _by_kind(_findings(state))
        rec = k["missing_required"][0]
        assert rec["severity"] == "error" and rec["field"] == "amplitude"

    def test_version_skew_warning(self):
        state = _state()
        state["__package_versions__"] = {"quam": "0.9.9"}
        k = _by_kind(_findings(state))
        rec = k["version_skew"][0]
        assert rec["severity"] == "warning" and "0.9.9" in rec["detail"]

    def test_unimportable_class_pip_hint(self):
        k = _by_kind(_findings(_state()))
        rec = k["unimportable_class"][0]
        assert "pip install otherlab_tools" in rec["fix_hint"]

    def test_types_map_and_resolver_agree(self):
        """The critique's P0 #2 pin: the bulk map and the single-path resolver
        are the SAME resolution — every map entry must round-trip."""
        state = _state()
        res = sev.analyze_state(state, MANIFEST)
        assert res["types"], "types map is empty"
        for path, ts in res["types"].items():
            solo = sev.expected_type_for(path, state, MANIFEST)
            assert solo == ts, f"resolver disagreement at {path}"

    def test_no_manifest_is_empty(self):
        res = sev.analyze_state(_state(), None)
        assert res["findings"] == [] and res["types"] == {}


class TestMemoAndBridge:
    def test_memo_keyed_on_mutation_seq(self):
        from quam_state_manager.core.loader import QuamStore
        store = QuamStore.from_dicts(_state(), {"wiring": {}})
        r1 = sev.analysis_for_store(store, MANIFEST)
        assert sev.analysis_for_store(store, MANIFEST) is r1     # memo hit
        store.mutation_seq += 1
        assert sev.analysis_for_store(store, MANIFEST) is not r1  # invalidated

    def test_to_diag_findings_shape(self):
        state = _state()
        state["qubits"]["qA1"]["duration_qubit"] = 1
        analysis = sev.analyze_state(state, MANIFEST)
        diag = sev.to_diag_findings(analysis, env_label="quam 0.6.0")
        from quam_state_manager.core.diagnostics import domain_of, summarize
        assert diag and all(domain_of(f.category) == "env" for f in diag)
        s = summarize(diag)
        assert s["error"] >= 1
        uf = [f for f in diag if f.category == "env_unknown_field"][0]
        assert uf.jump_path.startswith("qubits.qA1")
        assert "quam 0.6.0" in uf.detail


# ---------------------------------------------------------------------------
# route level
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    from quam_state_manager.core import type_policy as tp
    from quam_state_manager.web.app import create_app
    state = _state()
    state["qubits"]["qA1"]["duration_qubit"] = 5   # unknown field vs MANIFEST
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    with app.app_context():
        ctx = app.config["contexts"][app.config["active_context"]]
        ctx["store"].type_policy = tp.TypePolicy(MANIFEST, {})
    return c


class TestDiagnosticsIntegration:
    def test_env_findings_reach_the_page_and_badge(self, client):
        page = client.get("/diagnostics", headers={"HX-Request": "true"})
        html = page.get_data(as_text=True)
        assert "Environment match" in html
        assert "env_unknown_field" in html or "duration_qubit" in html

    def test_findings_json_carries_env_domain(self, client):
        j = client.get("/diagnostics/findings.json").get_json()
        cats = {f["category"] for f in j.get("findings", [])} if isinstance(j, dict) else set()
        flat = json.dumps(j)
        assert "env_unknown_field" in flat

    def test_env_card_renders(self, client):
        r = client.get("/diagnostics/env-card")
        # no selected env in tests → 204 or the no-env copy
        assert r.status_code in (200, 204)

    def test_env_probe_requires_selected_env(self, client):
        r = client.post("/diagnostics/env-probe")
        assert r.status_code == 400
        assert "environment" in r.get_json()["error"].lower()

    def test_no_subprocess_on_cold_request_paths(self, client, monkeypatch):
        """The cached_only rule: rendering /diagnostics never spawns."""
        from quam_state_manager.core import state_env_schema as ses
        called = []
        monkeypatch.setattr(ses, "_run_script_outcome",
                            lambda *a, **k: called.append(1))
        client.get("/diagnostics", headers={"HX-Request": "true"})
        client.get("/diagnostics/findings.json")
        client.get("/diagnostics/env-card")
        assert not called
