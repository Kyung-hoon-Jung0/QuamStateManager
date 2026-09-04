"""The lab's spec thresholds, on disk instead of in one browser (docs/167).

The symptom this exists for, measured on a real 20-qubit chip:

    QUBITS IN SPEC   0/20   (16 warn · 4 fail)

which is not a statement about the device. It is a statement about a comparison
nobody had configured — and the page did not say so. Two halves are pinned
here, and the FIRST is the one that fixes the confusion:

1. the numbers say whose they are;
2. there is one spec per installation instead of one per browser.

The per-chip layer a first draft proposed is deliberately absent, and that is
pinned too: it would add a second place to look when the numbers surprise
somebody, for a symptom caused by labelling alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import chip_health, spec_thresholds
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def inst(tmp_path):
    return str(tmp_path / "inst")


class TestResolve:
    def test_an_empty_store_is_the_defaults_and_says_so(self, inst):
        got = spec_thresholds.resolve(inst)
        assert got["metrics"] == chip_health.DEFAULT_THRESHOLDS
        assert got["edited"] == []
        assert got["source"] == "default"
        assert "not your lab's yet" in got["summary"]

    def test_the_returned_metrics_are_never_the_module_global(self, inst):
        """DEFAULT_THRESHOLDS is shared with make_record, report_card and the
        template context. One caller mutating a returned dict would silently
        re-spec the whole app."""
        got = spec_thresholds.resolve(inst)
        assert got["metrics"] is not chip_health.DEFAULT_THRESHOLDS
        assert got["metrics"]["T1"] is not chip_health.DEFAULT_THRESHOLDS["T1"]
        got["metrics"]["T1"]["warn"] = 1.0
        again = spec_thresholds.resolve(inst)
        assert again["metrics"]["T1"]["warn"] == chip_health.DEFAULT_THRESHOLDS["T1"]["warn"]

    def test_one_edited_metric_reads_as_mixed(self, inst):
        spec_thresholds.save(inst, {"T1": {"warn": 5e-5, "fail": 2e-5}})
        got = spec_thresholds.resolve(inst)
        assert got["source"] == "mixed"
        assert got["edited"] == ["T1"]
        assert got["metrics"]["T1"]["warn"] == 5e-5
        assert got["metrics"]["T2echo"] == chip_health.DEFAULT_THRESHOLDS["T2echo"]
        assert "the rest are SM's defaults" in got["summary"]

    def test_every_metric_edited_reads_as_the_labs(self, inst):
        spec_thresholds.save(inst, {
            k: {"warn": v["warn"] * 1.5, "fail": v["fail"] * 1.5}
            for k, v in chip_health.DEFAULT_THRESHOLDS.items()})
        got = spec_thresholds.resolve(inst)
        assert got["source"] == "lab"
        assert got["summary"] == "your lab's bands"

    def test_direction_and_label_are_not_lab_preferences(self, inst):
        """A lab that flipped `direction` would be renaming the metric, not
        re-specifying it."""
        spec_thresholds.save(inst, {"T1": {"warn": 5e-5, "fail": 2e-5,
                                           "direction": "lower", "label": "nope"}})
        raw = json.loads(spec_thresholds.spec_path(inst).read_text(encoding="utf-8"))
        assert set(raw["thresholds"]["T1"]) == {"warn", "fail"}, raw
        got = spec_thresholds.resolve(inst)
        assert got["metrics"]["T1"]["direction"] == \
            chip_health.DEFAULT_THRESHOLDS["T1"]["direction"]
        assert got["metrics"]["T1"]["label"] == \
            chip_health.DEFAULT_THRESHOLDS["T1"]["label"]

    def test_a_hand_edited_file_cannot_smuggle_junk_past_resolve(self, inst):
        """`save` filters, and so does `resolve`, and the second one is not
        redundant: the sidecar is a JSON file on disk that a person can open.
        A mutation sweep found this untested — every junk case went through
        `save`, so loosening `resolve` changed nothing observable.
        """
        path = spec_thresholds.spec_path(inst)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "thresholds": {
            "T1": {"warn": 5e-5, "fail": "twenty", "direction": "lower",
                   "label": "renamed by hand"},
            "T2echo": {"warn": True},          # bool is not a number here
            "nonsense": {"warn": 1e-5, "fail": 1e-6},
            "T2ramsey": "not even a dict",
        }}), encoding="utf-8")

        got = spec_thresholds.resolve(inst)
        assert got["metrics"]["T1"]["warn"] == 5e-5, "the one good value is kept"
        d = chip_health.DEFAULT_THRESHOLDS
        assert got["metrics"]["T1"]["fail"] == d["T1"]["fail"]
        assert got["metrics"]["T1"]["direction"] == d["T1"]["direction"]
        assert got["metrics"]["T1"]["label"] == d["T1"]["label"]
        assert got["metrics"]["T2echo"] == d["T2echo"], "True is not a threshold"
        assert got["metrics"]["T2ramsey"] == d["T2ramsey"]
        assert "nonsense" not in got["metrics"]
        assert got["edited"] == ["T1"]

    def test_a_corrupt_file_falls_back_to_the_defaults(self, inst):
        path = spec_thresholds.spec_path(inst)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        got = spec_thresholds.resolve(inst)
        assert got["metrics"] == chip_health.DEFAULT_THRESHOLDS
        assert got["source"] == "default"

    def test_junk_values_are_ignored_one_at_a_time(self, inst):
        """A numeric STRING is the interesting case: `float("5e-05")` succeeds,
        so a coercing writer would accept it while a strict one skips it. The
        stored file is checked as well as the resolved view, because `resolve`
        also filters and would hide a `save` that let junk through."""
        spec_thresholds.save(inst, {"T1": {"warn": 5e-5, "fail": "twenty"},
                                    "T2echo": {"warn": "5e-05"},
                                    "nonsense": {"warn": 1}})
        got = spec_thresholds.resolve(inst)
        assert got["metrics"]["T1"]["warn"] == 5e-5
        assert got["metrics"]["T1"]["fail"] == chip_health.DEFAULT_THRESHOLDS["T1"]["fail"]
        assert got["metrics"]["T2echo"] == chip_health.DEFAULT_THRESHOLDS["T2echo"]
        assert "nonsense" not in got["metrics"]

        raw = json.loads(spec_thresholds.spec_path(inst).read_text(encoding="utf-8"))
        assert set(raw["thresholds"]) == {"T1"}, raw
        assert raw["thresholds"]["T1"] == {"warn": 5e-5}


class TestSaveStoresOnlyTheDifference:
    def test_a_band_equal_to_the_default_is_not_stored(self, inst):
        """Storing a full copy would freeze today's defaults into the file, so
        a later correction to a seed value would never reach a lab that once
        pressed Apply — "we use the defaults" quietly becoming "we use August's
        defaults"."""
        spec_thresholds.save(inst, chip_health.DEFAULT_THRESHOLDS)
        raw = json.loads(spec_thresholds.spec_path(inst).read_text(encoding="utf-8"))
        assert raw["thresholds"] == {}
        assert spec_thresholds.resolve(inst)["source"] == "default"

    def test_only_the_moved_bound_is_stored(self, inst):
        base = chip_health.DEFAULT_THRESHOLDS["T1"]
        spec_thresholds.save(inst, {"T1": {"warn": 5e-5, "fail": base["fail"]}})
        raw = json.loads(spec_thresholds.spec_path(inst).read_text(encoding="utf-8"))
        assert raw["thresholds"] == {"T1": {"warn": 5e-5}}

    def test_clear_returns_to_the_defaults(self, inst):
        spec_thresholds.save(inst, {"T1": {"warn": 5e-5}})
        assert spec_thresholds.clear(inst)["source"] == "default"
        assert spec_thresholds.resolve(inst)["metrics"] == chip_health.DEFAULT_THRESHOLDS


class TestRoutes:
    @pytest.fixture
    def client(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        return app.test_client()

    def test_the_round_trip(self, client):
        assert client.get("/chip-status/spec").get_json()["source"] == "default"
        r = client.post("/chip-status/spec",
                        data={"metrics": json.dumps({"T1": {"warn": 5e-5, "fail": 2e-5}})})
        assert r.status_code == 200
        assert r.get_json()["spec"]["edited"] == ["T1"]
        assert client.get("/chip-status/spec").get_json()["metrics"]["T1"]["warn"] == 5e-5
        assert client.post("/chip-status/spec/clear").get_json()["spec"]["source"] == "default"

    def test_it_is_shared_across_clients(self, tmp_path):
        """The whole point: two people, one definition of in-spec."""
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        a, b = app.test_client(), app.test_client()
        a.post("/chip-status/spec",
               data={"metrics": json.dumps({"T1": {"warn": 5e-5, "fail": 2e-5}})})
        assert b.get("/chip-status/spec").get_json()["metrics"]["T1"]["warn"] == 5e-5

    def test_junk_is_refused_rather_than_stored(self, client):
        for metrics in ("not json", json.dumps([1, 2, 3])):
            r = client.post("/chip-status/spec", data={"metrics": metrics})
            assert r.status_code == 400
        assert client.get("/chip-status/spec").get_json()["source"] == "default"


class TestTheClientStoppedOwningIt:
    """The half that can be reverted invisibly, so it is pinned explicitly: a
    client that still wrote localStorage would keep the per-browser divergence
    alive underneath a shared file, which is worse than either arrangement."""

    def _js(self) -> str:
        return (_ROOT / "quam_state_manager" / "web" / "static"
                / "chip-status.js").read_text(encoding="utf-8")

    def test_nothing_writes_the_old_key_any_more(self):
        js = self._js()
        assert "_saveThresholds" not in js
        assert "localStorage.setItem(THRESH_KEY" not in js
        # the ONLY setItem left on this subject is the migration marker
        assert js.count("localStorage.setItem(MIGRATED_KEY") == 1

    def test_the_spec_is_posted_to_the_server(self):
        js = self._js()
        assert "'/chip-status/spec'" in js
        assert "'/chip-status/spec/clear'" in js

    def test_pressing_apply_actually_posts(self):
        """Grepping for the URL is not enough: it lives in _postSpec, which a
        commit path could simply stop calling. The pin is on the CALL."""
        js = self._js()
        body = js[js.index("window.applyThresholds = function"):]
        body = body[:body.index("window.toggleThresholdEditor")]
        assert "_postSpec(thresholds)" in body
        assert "localStorage" not in body

    def test_resetting_one_metric_posts_too(self):
        js = self._js()
        body = js[js.index("window.resetMetricThreshold = function"):]
        body = body[:body.index("// ── Cell colour")] if "// ── Cell colour" in body else body[:1200]
        assert "_postSpec(thresholds)" in body

    def test_the_old_key_is_read_exactly_once_to_migrate(self):
        js = self._js()
        assert js.count("localStorage.getItem(THRESH_KEY") == 1
        block = js[js.index("_migrateOnce"):]
        block = block[:block.index("})();")]
        assert "MIGRATED_KEY" in block, "a migration that repeats is not a migration"
        assert "_labSpec.source !== 'default'" in block, (
            "a straggler browser must not overwrite a spec the team already set")

    def test_the_client_resolves_nothing_of_its_own(self):
        """One resolver, on the server. The client copies its answer."""
        js = self._js()
        block = js[js.index("function _loadThresholds"):]
        block = block[:block.index("var thresholds =")]
        assert "_labSpec" in block and "metrics" in block
        assert "localStorage" not in block


class TestTheNumbersSayWhoseTheyAre:
    def _js(self) -> str:
        return (_ROOT / "quam_state_manager" / "web" / "static"
                / "chip-status.js").read_text(encoding="utf-8")

    def test_the_below_spec_tile_names_the_source(self):
        """BOTH branches, not one: a qubit can be below spec at warn level or
        at fail level, and the note is exactly as needed in either."""
        js = self._js()
        assert "function _specNote()" in js
        i = js.index("qubits below spec")
        tile = js[i:i + 900]
        assert tile.count("_specNote()") == 2, (
            "the failing branch and the to-watch branch must both name the "
            f"source (found {tile.count('_specNote()')})")

    def test_it_is_silent_once_the_lab_has_set_its_own_bands(self):
        """At that point the number means exactly what it says, and a note
        would be noise."""
        js = self._js()
        block = js[js.index("function _specNote()"):]
        block = block[:block.index("function buildThresholdEditor")]
        assert "src === 'default'" in block and "src === 'mixed'" in block
        assert "return '';" in block

    def test_the_editor_says_the_spec_is_shared(self):
        js = self._js()
        assert "shared with everyone using this SM" in js
        assert "saved to this browser" not in js


class TestThePageShipsIt:
    def test_chip_status_hands_the_client_the_resolved_spec(self, tmp_path):
        """A mount that received `{}` would silently fall back to SM's seeds
        for the verdicts AND lose the provenance line, with every source pin
        above still green."""
        chip = tmp_path / "quam_state"
        chip.mkdir()
        (chip / "state.json").write_text(json.dumps({
            "qubits": {"qA1": {"id": "qA1", "f_01": 6.2e9, "T1": 1.0e-5}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}), encoding="utf-8")
        (chip / "wiring.json").write_text(
            json.dumps({"wiring": {"qubits": {}}, "network": {"host": "10.1.1.1"}}),
            encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        c.post("/load", data={"folder": str(chip)})
        c.post("/chip-status/spec",
               data={"metrics": json.dumps({"T1": {"warn": 5e-5, "fail": 2e-5}})})
        html = c.get("/topology").get_data(as_text=True)
        assert "labSpec:" in html
        i = html.index("labSpec:")
        block = html[i:i + 1200]
        assert '"source"' in block and "mixed" in block, block[:300]
        assert "5e-05" in block or "5.0e-05" in block or "0.00005" in block, block[:300]


class TestWhatWasDeliberatelyLeftOut:
    def test_there_is_no_per_chip_layer(self):
        """Cut by a design review: a second place to look when the numbers
        surprise somebody, for a symptom caused by labelling alone. A lab that
        genuinely needs two devices' bands can ask, and get the layering shown
        on screen — which is the part that would make it safe."""
        src = (_ROOT / "quam_state_manager" / "core"
               / "spec_thresholds.py").read_text(encoding="utf-8")
        for token in ("chip_key", "resolve_chip_dir", "key_for", "live_folder"):
            assert token not in src.split('"""', 2)[2], token
