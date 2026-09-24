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

    def test_type_mismatch_is_error_tier(self):
        # QA diagnostics-r2-02 (this pin used to say "warning"): quam's
        # load-time typeguard check raises on a str in a float field -- every
        # node run dies on Quam.load(), exactly like unknown_field. Verified in
        # quam 0.6.0: "Wrong object type found during validation".
        state = _state()
        state["qubits"]["qA1"]["f_01"] = "1.1349480376211927e-05"
        k = _by_kind(_findings(state))
        rec = k["type_mismatch"][0]
        assert rec["severity"] == "error"
        assert "Wrong object type" in rec["detail"]

    @pytest.mark.parametrize("field,value,code", [
        ("confusion_matrix", [[0.9, "x"], [0.1, 0.9]], "element_mismatch"),
        ("confusion_matrix", [0.9, 0.1], "list_shape"),
        ("active", 1, "type_mismatch"),          # int in a bool field
        ("xy", "not-a-pointer", "type_mismatch"),  # str in a PLAIN component
    ])
    def test_load_breaking_codes_are_error_tier(self, field, value, code):
        state = _state()
        state["qubits"]["qA1"][field] = value
        recs = [r for r in _findings(state) if r.get("code") == code]
        assert recs and recs[0]["severity"] == "error", recs

    @pytest.mark.parametrize("field,value,code", [
        ("f_01", True, "bool_in_numeric"),       # quam loads it (verified)
        ("f_01", float("nan"), "non_finite"),    # quam loads it (verified)
        ("length", 40.5, "non_integral_int"),    # quam int()s it (verified)
        ("flux_point", "sideways", "enum_miss"),  # explicit v1 design: advisory
    ])
    def test_codes_that_load_stay_warning_tier(self, field, value, code):
        state = _state()
        state["qubits"]["qA1"][field] = value
        recs = [r for r in _findings(state) if r.get("code") == code]
        assert recs and recs[0]["severity"] == "warning", recs

    def test_component_or_str_union_string_stays_warning(self):
        # the probe collapses Union[Component, str] to `component` (raw keeps
        # the Union); quam's str arm accepts any string there, so it LOADS
        import copy
        man = copy.deepcopy(MANIFEST)
        man["classes"]["q.Transmon"]["fields"]["gate_pulse"] = {
            "type": {"base": "component", "optional": False, "item": None,
                     "enum": None, "union": None, "class": "q.Pulse",
                     "raw": "typing.Union[q.Pulse, str]"},
            "optional": False, "has_default": True, "default": None,
            "default_repr": None, "default_is_reference": False,
            "raw": "typing.Union[q.Pulse, str]"}
        state = _state()
        state["qubits"]["qA1"]["gate_pulse"] = "qA1"
        recs = [r for r in _findings(state, man) if r.get("field") == "gate_pulse"]
        assert recs and recs[0]["severity"] == "warning", recs

    def test_a_text_number_reaches_the_crash_banner_count(self):
        # the chain the banner reads: analyzer -> diagnostics Finding -> summary
        from quam_state_manager.core import diagnostics
        state = _state()
        del state["custom"]                       # drop the fixture's own error
        state["qubits"]["qA1"]["f_01"] = "1.1349480376211927e-05"
        found = sev.to_diag_findings(sev.analyze_state(state, MANIFEST))
        assert diagnostics.summarize(found)["error"] == 1

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

    def test_the_probe_poll_announces_its_finish_once(self, client, monkeypatch):
        """QA F-L: after Re-probe the Types card kept the pre-probe count until
        a reload -- nothing told it the schema had changed. The probe's own
        poll (?poll=1) now says diagnostics-changed when it finds the probe
        done; a plain GET never does (no loop), nor does a poll mid-probe."""
        from quam_state_manager.web import routes
        done = client.get("/diagnostics/env-card?poll=1")
        assert done.status_code == 200
        assert done.headers.get("HX-Trigger") == "diagnostics-changed"
        assert "HX-Trigger" not in client.get("/diagnostics/env-card").headers
        real = routes._env_card_state
        monkeypatch.setattr(routes, "_env_card_state",
                            lambda store: {**real(store), "probing": True})
        busy = client.get("/diagnostics/env-card?poll=1")
        assert "HX-Trigger" not in busy.headers
        # ...and the self-poll it renders mid-probe is the announcing one
        assert 'hx-get="/diagnostics/env-card?poll=1"' in busy.get_data(as_text=True)

    def test_same_quam_version_reads_as_such(self, client):
        from flask import render_template
        app = client.application
        with app.test_request_context():
            html = render_template("_env_schema_changes.html", transition={
                "changed": True, "first": False, "from_label": "quam 0.6.0 · qm 1.3.1",
                "to_label": "quam 0.6.0 · qm 1.4.1", "distance": "same",
                "diff": {"total": 1, "truncated": False, "rows": []}, "sig": "x",
                "from_key": "a", "to_key": "b"}, verdicts={}, rows=[])
        assert "same quam version" in html
        assert "same version step" not in html

    def test_env_findings_follow_the_selected_env(self, client, tmp_path):
        """QA diagnostics-r2-16: the findings (list, findings.json, Types card)
        are verdicts against the env the manifest was probed FROM. Once that
        env is no longer selected, or its interpreter is gone, they are
        withdrawn -- the card already said "no longer exists" beside them --
        and the card links to where the env is reselected."""
        from quam_state_manager.core import config_generator
        app = client.application
        inst = app.instance_path
        py = tmp_path / "envs" / "lab" / "python.exe"
        py.parent.mkdir(parents=True)
        py.write_text("", encoding="utf-8")
        with app.app_context():
            store = app.config["contexts"][app.config["active_context"]]["store"]
        store._type_manifest_env = str(py)       # what every production writer sets

        def flat():
            return json.dumps(client.get("/diagnostics/findings.json").get_json())

        def card():
            return client.get("/diagnostics/types-card").get_data(as_text=True)

        # selected + present: the verdicts stand
        config_generator.set_selected_env(inst, str(py))
        assert "env_unknown_field" in flat()
        mismatch = card()
        assert "match" in mismatch
        # the interpreter is gone (conda env remove / rename): withdrawn
        py.unlink()
        assert "env_unknown_field" not in flat()
        assert "duration_qubit" not in card()
        env_card = client.get("/diagnostics/env-card").get_data(as_text=True)
        assert "no longer exists" in env_card and 'hx-get="/generate"' in env_card
        # a different env selected (settings edited outside SM): withdrawn too
        other = tmp_path / "envs" / "other" / "python.exe"
        other.parent.mkdir(parents=True)
        other.write_text("", encoding="utf-8")
        config_generator.set_selected_env(inst, str(other))
        assert "env_unknown_field" not in flat()
        # back to the probed env: they return (no mutation needed)
        py.write_text("", encoding="utf-8")
        config_generator.set_selected_env(inst, str(py))
        assert "env_unknown_field" in flat()
        assert "duration_qubit" in card()

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
