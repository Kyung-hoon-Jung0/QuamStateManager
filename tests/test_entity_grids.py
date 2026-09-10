"""Every collection a chip HAS gets a grid, and wiring.json is a document too.

Customer, 2026-09-10, two asks in one message:

  "live state edit이 twpa를 지원하지 않네? json tree view는 당연히 잘 보이거든?
   지금은 없지만 나중에 qdac도 그렇고.. 이거 adaptive하게 해서 display하게
   할수는 없니??"
  "live state edit에서도 wiring.json 할수있게. Live state edit 오른쪽에 badge
   형태로, state.json | wiring.json 이렇게 두개 버튼"

Live State Edit rendered exactly two grids, one per HARDCODED collection, so a
TWPA pump's amplitude and RF frequency existed on no grid and the search could
not find them. A third hardcoded grid would fix this chip and leave the next
component out again — the docs/94 / docs/126 ① silent-skip shape — so the
collections are DISCOVERED and each one is built by the SAME derivation the
pair grid uses.

What is pinned here is the part that is a decision rather than a rendering:
which shapes count as a collection, that the engine really is one engine, that
the wiring document keeps its pointers as values, and that a grid's cells can
only ever be served to that grid.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import entity_grids
from quam_state_manager.core.pair_columns import (
    derive_entity_columns, derive_pair_columns)
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent


def _chip(tmp_path: Path, *, twpa=True, extra_state=None):
    """A small chip with a qubit, a pair and a TWPA — the customer's shape."""
    state = {
        "qubits": {
            "q1": {"id": "q1", "f_01": 5.0e9,
                   "xy": {"opx_output": "#/wiring/qubits/q1/xy/opx_output",
                          "operations": {"x180": {"amplitude": 0.1}}}},
            "q2": {"id": "q2", "f_01": 5.1e9,
                   "xy": {"opx_output": "#/wiring/qubits/q2/xy/opx_output",
                          "operations": {"x180": {"amplitude": 0.1}}}},
        },
        "qubit_pairs": {"q1-2": {"id": "q1-2", "detuning": 1.0e6}},
        "ports": {"mw_outputs": {"con1": {"1": {
            "2": {"band": 2, "upconverter_frequency": 5.0e9},
            "3": {"band": 2, "upconverter_frequency": 5.1e9}}}}},
        "octaves": {},                      # empty -> no grid
        "mixers": {},                       # empty -> no grid
        "extras": {"chip_name": "t"},       # free-form by policy
        "__package_versions__": {"quam": "0.6.0"},   # dict of STRINGS, not entities
    }
    if twpa:
        state["twpas"] = {"twpa1": {
            "id": "twpa1", "pump_amplitude": 0.48, "pump_frequency": 8.18e9,
            "grid_location": "0,0", "initialization": True,
            "pump": {"RF_frequency": 8.18e9,
                     "opx_output": "#/wiring/twpas/twpa1/p/opx_output",
                     "operations": {"pump": {"amplitude": 1, "length": 20000}}},
        }}
    if extra_state:
        state.update(extra_state)
    wiring = {"wiring": {
        "qubits": {"q1": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}},
                   "q2": {"xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"}}},
        "twpas": {"twpa1": {"p": {"opx_output": "#/ports/mw_outputs/con1/1/2"}}},
    }, "network": {"host": "1.1.1.1"}}
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    return c


class TestWhatCountsAsACollection:
    """The rule is about SHAPE, never about a component's name — otherwise the
    next component is left out exactly like this one was."""

    @staticmethod
    def _merged(**over):
        base = {"qubits": {"q1": {}}, "qubit_pairs": {"p": {}},
                "ports": {"mw_outputs": {"con1": {}}}, "extras": {"a": 1},
                "wiring": {"qubits": {"q1": {}}}, "network": {"host": "x"},
                "__package_versions__": {"quam": "0.6.0"}}
        base.update(over)
        return base

    def test_a_collection_of_entities_is_found_without_being_named(self):
        got = entity_grids.discover(self._merged(zorblaxes={"z1": {"a": 1}}))
        assert [g["root"] for g in got] == ["zorblaxes"]
        assert got[0]["key"] == "e_zorblaxes" and got[0]["ids"] == ["z1"]

    def test_the_two_that_already_have_a_grid_are_not_repeated(self):
        got = entity_grids.discover(self._merged(twpas={"t1": {"a": 1}}))
        roots = [g["root"] for g in got]
        assert "qubits" not in roots and "qubit_pairs" not in roots
        assert roots == ["twpas"]

    def test_ports_extras_and_the_version_map_are_not_collections(self):
        roots = [g["root"] for g in entity_grids.discover(self._merged())]
        assert roots == [], roots
        # each for its own reason, and none of them a denylist of names:
        assert not entity_grids._is_entity_collection({"quam": "0.6.0"})  # dict of strings
        assert not entity_grids._is_entity_collection({})                 # nothing in it
        assert not entity_grids._is_entity_collection([1, 2])             # not a dict
        assert entity_grids._is_entity_collection({"a": {}, "b": {}})

    def test_an_empty_collection_renders_nothing(self):
        """A heading over an empty table is worse than no heading."""
        assert entity_grids.discover(self._merged(octaves={})) == []

    def test_the_wiring_document_lists_its_own_collections(self):
        got = entity_grids.discover(
            self._merged(wiring={"qubits": {"q1": {}}, "twpas": {"t1": {}}}), "wiring")
        assert [g["root"] for g in got] == ["wiring.qubits", "wiring.twpas"]
        assert all(g["expand_ports"] is False for g in got), (
            "on a wiring grid the port POINTER is the value the file holds and "
            "the thing the user edits — expanding it shows a different document")


class TestOneEngine:
    def test_the_pair_grid_goes_through_the_same_derivation(self, tmp_path):
        """`derive_pair_columns` IS `derive_entity_columns(store, 'qubit_pairs')`.
        Two derivations would drift, and the pair grid is the one with a year of
        pins behind it."""
        c = _chip(tmp_path)
        from quam_state_manager.web import routes
        with c.application.test_request_context():
            store = None
            for ctx in c.application.config["contexts"].values():
                store = ctx.get("store") or store
        assert store is not None
        assert derive_pair_columns(store) == derive_entity_columns(store, "qubit_pairs")

    def test_a_twpa_column_carries_its_real_path(self, tmp_path):
        c = _chip(tmp_path)
        html = c.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        row = re.search(r'data-entity="twpa1"(.*?)</tr>', html, re.S)
        assert row, "the TWPA grid rendered no row"
        assert 'data-dot-path="twpas.twpa1.pump_amplitude"' in row.group(1), (
            "a TWPA cell must commit down the same dot path a qubit cell does — "
            "that is what lets it ride /field/edit-batch with no new code")

    def test_the_unit_guess_is_anchored(self):
        """`grid_location` became an Hz column the first time a collection
        outside qubit_pairs was rendered: `"_lo" in n` matched grid_LOcation.
        No pair leaf happened to contain those letters, so the guess had never
        been wrong before."""
        from quam_state_manager.core.pair_columns import _unit_of
        assert _unit_of("grid_location") == ""
        assert _unit_of("LO_frequency") == "Hz"
        assert _unit_of("xy_lo") == "Hz"


class TestTheTwoDocuments:
    def test_the_badges_are_there_and_one_is_lit(self, tmp_path):
        c = _chip(tmp_path)
        html = c.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        # the wrapper is `bulk-docbadges`, so match the anchors exactly
        assert len(re.findall(r'class="bulk-docbadge(?: active)?"', html)) == 2
        assert 'class="bulk-docbadge active"' in html
        assert ">state.json<" in html and ">wiring.json<" in html

    def test_the_wiring_document_renders_its_grids_and_not_the_others(self, tmp_path):
        c = _chip(tmp_path)
        html = c.get("/bulk?doc=wiring", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="bulk-w_qubits-table"' in html
        assert 'id="bulk-w_twpas-table"' in html
        assert 'id="bulk-pair-table"' not in html, (
            "qubit_pairs is a state.json collection; the wiring document has "
            "its own")
        assert 'id="bulk-table"' not in html, (
            "the qubit grid is state.json's; rendering it under the wiring "
            "badge would put one document's values under the other's name")
        # the badge that is lit follows the document
        lit = re.search(r'class="bulk-docbadge active"[^>]*>([a-z.]+)<', html)
        assert lit and lit.group(1) == "wiring.json", html[:0]

    def test_the_wiring_cell_holds_the_pointer(self, tmp_path):
        c = _chip(tmp_path)
        html = c.get("/bulk?doc=wiring", headers={"HX-Request": "true"}).get_data(as_text=True)
        row = re.search(r'data-entity="q1"(.*?)</tr>', html, re.S)
        assert row and "#/ports/mw_outputs/con1/1/2" in row.group(1), (
            "the wiring grid must show the pointer itself, not the port's leaves")

    def test_an_unknown_doc_is_the_state_document(self, tmp_path):
        """A stale or hand-typed link can never render a blank page — and `doc`
        is a CACHE KEY as well as a template flag, so it must collapse to one of
        exactly two values or `?doc=x` and `?doc=y` become two cache entries for
        one page."""
        from quam_state_manager.web.routes import _bulk_doc
        for raw in (None, "", "banana", "state", "STATE", " wiring x"):
            assert _bulk_doc(raw) == "state", raw
        for raw in ("wiring", "WIRING", " wiring "):
            assert _bulk_doc(raw) == "wiring", raw
        c = _chip(tmp_path)
        html = c.get("/bulk?doc=banana", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="bulk-table"' in html


class TestCellsGoOnlyToTheirOwnGrid:
    def test_a_discovered_grid_hydrates_by_its_own_key(self, tmp_path):
        c = _chip(tmp_path)
        html = c.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="bulk-e_twpas-table"' in html
        key = re.search(r'data-col-key="(general__pump_amplitude)"', html)
        assert key, "fixture: the TWPA grid has no pump_amplitude column"
        r = c.get("/bulk/cells?grid=e_twpas&cols=" + key.group(1))
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        assert "twpa1" in r.get_data(as_text=True)

    def test_an_unknown_grid_is_a_400_and_never_a_fallback(self, tmp_path):
        """Serving one grid's cells into another's rows would put wrong numbers
        on screen — the one failure this mechanism must not have (docs/141 4ad).
        """
        c = _chip(tmp_path)
        r = c.get("/bulk/cells?grid=e_nope&cols=x")
        assert r.status_code == 400
        assert "unknown grid" in r.get_data(as_text=True)

    def test_a_chip_without_the_collection_offers_no_grid(self, tmp_path):
        c = _chip(tmp_path, twpa=False)
        html = c.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "bulk-e_twpas-table" not in html
        assert c.get("/bulk/cells?grid=e_twpas&cols=x").status_code == 400


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_entity_grid_selfcheck_passes():
    """Two instances against ONE #bulk-search box, under jsdom.

    The factory's own regression: the mount guarded its listener with a single
    `search._pairBound` flag on the SHARED search element, so whichever grid
    mounted first silenced every other one. A source grep would not catch it --
    the flag was there, it was simply the wrong scope -- so this harness mounts
    two grids and types.
    """
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / "entity_grid_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


class TestOneImplementationManyInstances:
    """`pair-edit.js` became a factory; the pair grid is its first instance.
    A second implementation is how two grids drift."""

    def test_the_factory_and_both_entry_points_exist(self):
        js = (_ROOT / "quam_state_manager/web/static/pair-edit.js").read_text(encoding="utf-8")
        assert "function makeGrid(cfg)" in js
        assert "window.BulkPairEdit = makeGrid({" in js
        assert "window.makeEntityGrid = function" in js

    def test_every_instance_id_is_derived_from_its_prefix(self):
        js = (_ROOT / "quam_state_manager/web/static/pair-edit.js").read_text(encoding="utf-8")
        code = " ".join(ln for ln in js.splitlines()
                        if not ln.lstrip().startswith(("*", "/*", "//")))
        assert "bulk-pair-" not in code, (
            "a hardcoded pair id survives the factory — that element would be "
            "addressed by every instance")
        assert "var P = cfg.prefix;" in code

    def test_the_template_mounts_one_instance_per_discovered_grid(self):
        tpl = (_ROOT / "quam_state_manager/web/templates/_bulkedit.html").read_text(
            encoding="utf-8")
        # docs/148: a string-only pin survives an `if (false)` around the call,
        # so the guard and the call are pinned TOGETHER. (The mount actually
        # running is verified in a real browser — see the commit.)
        assert "if (window.makeEntityGrid) {" in tpl
        i = tpl.index("if (window.makeEntityGrid) {")
        assert "window.makeEntityGrid(" in tpl[i:i + 400]
        assert "_bulk_entity_grid.html" in tpl
        # ...and the pair grid renders through the SAME partial, so the markup
        # cannot drift between the old grid and the new ones.
        assert tpl.count("_bulk_entity_grid.html") >= 2
