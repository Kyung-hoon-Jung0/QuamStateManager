"""docs/94 — the schema-drift healing chain.

A lab migration script swapped pulse classes OUT-OF-BAND while the chip was
open; SM then reported 10× "harvest drift" errors on a healthy chip, and the
Diagnostics Probe button visibly did nothing. Three defects in one chain,
each pinned here:

1. ``_manifest_key`` ignored the manifest's CLASS SET — a successful re-probe
   (same env versions, more classes) could never displace the memoized
   findings, so the Probe button "didn't work".
2. ``_attach_type_policy``'s warm-carry served the previous manifest even
   when the chip's inventory had GROWN past it — guaranteed-stale validation.
   The carry now gates on coverage and kicks the warm re-probe instead.
3. The rebuild-after-replace / reconcile-adopt choke points never re-attached
   or re-probed, so the false errors were sticky for the whole session.
Plus fix 3: findings for a class whose probe is IN FLIGHT downgrade to a
warning that says so.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core import state_env_validate as sev

CW = "quam_config.custom_pulse.CustomReadoutPulse"


def _state_with_custom_class() -> dict:
    return {
        "__class__": "quam_config.my_quam.Quam",
        "qubits": {"q1": {
            "id": "q1", "f_01": 5.0e9,
            "resonator": {"operations": {"readout": {
                "__class__": CW, "amplitude": 0.04, "length": 1000}}},
        }},
        "qubit_pairs": {},
    }


def _manifest(*, with_cw: bool) -> dict:
    entry = {"importable": True, "canonical": None, "bases": [],
             "is_dataclass": True, "fields": {}}
    classes = {"quam_config.my_quam.Quam": dict(entry)}
    if with_cw:
        classes[CW] = dict(entry)
    return {"classes": classes, "versions": {"quam": "0.6.0", "quam_builder": "0.4.0"}}


def _drift(res: dict) -> list:
    return [f for f in (res.get("findings") or []) if f.get("kind") == "unknown_class"]


class TestManifestKeyFoldsClassSet:
    def test_reprobe_with_same_versions_displaces_memoized_findings(self):
        store = QuamStore.from_dicts(_state_with_custom_class(), {"wiring": {}})
        stale = sev.analysis_for_store(store, _manifest(with_cw=False))
        assert _drift(stale), "the stale manifest must produce the drift finding"
        # SAME store, SAME mutation_seq, SAME env versions — only the class
        # set grew (what a successful Probe produces). Must recompute.
        healed = sev.analysis_for_store(store, _manifest(with_cw=True))
        assert not _drift(healed), (
            "a re-probed manifest with the class must clear the finding "
            "without waiting for an unrelated store mutation")


class TestAttachCarryGate:
    def _run_attach(self, monkeypatch, tmp_path, *, prev_manifest):
        from quam_state_manager.web import routes
        from quam_state_manager.core import state_env_schema, type_policy
        store = QuamStore.from_dicts(_state_with_custom_class(), {"wiring": {}})
        prev = types.SimpleNamespace(manifest=prev_manifest,
                                     env_manifest=prev_manifest)
        store.type_policy = prev
        store._type_manifest_env = "X:/env/python.exe"
        ctx = {"store": store, "path": str(tmp_path)}

        monkeypatch.setattr(routes.config_generator, "get_selected_env",
                            lambda inst: "X:/env/python.exe")
        monkeypatch.setattr(state_env_schema, "manifest_for_store",
                            lambda *a, **k: None)          # cache cold
        seen = {}
        monkeypatch.setattr(type_policy, "load_policy",
                            lambda inst, live, manifest: seen.setdefault("manifest", manifest)
                            or types.SimpleNamespace(manifest=manifest, env_manifest=manifest))
        warmed = []
        monkeypatch.setattr(routes, "_warm_state_schema_async",
                            lambda *a, **k: warmed.append(a))
        routes._attach_type_policy(ctx, inst=str(tmp_path / "_inst"))
        return seen.get("manifest"), warmed

    def test_grown_inventory_abstains_and_kicks_the_warm(self, monkeypatch, tmp_path):
        attached, warmed = self._run_attach(
            monkeypatch, tmp_path, prev_manifest=_manifest(with_cw=False))
        assert attached is None, (
            "a manifest that no longer covers the chip must NOT be carried")
        assert warmed, "abstaining must kick the background re-probe"

    def test_covered_inventory_still_carries(self, monkeypatch, tmp_path):
        attached, warmed = self._run_attach(
            monkeypatch, tmp_path, prev_manifest=_manifest(with_cw=True))
        assert attached is not None and CW in attached["classes"], (
            "a covering manifest keeps the docs/79 carry (pip-flip resilience)")
        assert not warmed


class TestChokePointRewarm:
    def test_rebuild_after_replace_reattaches_and_rewarms(self, tmp_path, monkeypatch):
        from quam_state_manager.web.app import create_app
        from quam_state_manager.web import routes
        (tmp_path / "state.json").write_text(
            json.dumps(_state_with_custom_class()), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(
            json.dumps({"wiring": {}, "network": {"host": "10.0.0.1"}}),
            encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        client = app.test_client()
        client.post("/load", data={"folder": str(tmp_path)})
        warmed, attached = [], []
        monkeypatch.setattr(routes, "_warm_state_schema_async",
                            lambda *a, **k: warmed.append(a))
        monkeypatch.setattr(routes, "_attach_type_policy",
                            lambda *a, **k: attached.append(a))
        with app.test_request_context("/"):
            ctx = app.config["contexts"][app.config["active_context"]]
            routes._rebuild_after_working_copy_replaced(ctx)
        assert attached, "the rebuild choke point must re-attach the policy"
        assert warmed, "the rebuild choke point must re-probe the schema"


class TestProbingDowngrade:
    _ANALYSIS = {"findings": [{
        "kind": "unknown_class", "severity": "error", "class": CW,
        "field": None, "count": 10, "example_paths": ["qubits.q1.resonator.operations.readout"],
        "detail": f"{CW} was not probed in the selected env (harvest drift)",
        "fix_hint": "re-probe the environment",
    }]}

    def test_in_flight_probe_downgrades_to_warning(self):
        out = sev.to_diag_findings(self._ANALYSIS, env_label="quam 0.6.0", probing=True)
        assert out[0].severity == "warning"
        assert "probing the environment" in out[0].message

    def test_without_probe_stays_error(self):
        out = sev.to_diag_findings(self._ANALYSIS, env_label="quam 0.6.0")
        assert out[0].severity == "error"
        assert "probing the environment" not in out[0].message
