"""Every ordered list a ROUTE hands a template counts numerically.

Customer report 2026-09-09 (screenshot): the live-diff review listed
``…weights_imag.1009``, then ``.101``, then ``.1011`` -- a lexicographic sort
of a path whose last segment is a number. The rule the customer stated is for
the whole product: 101 < 1009 < 1010 < 1011, and q2 < q10.

``core.loader.natural_key`` is the ONE helper (digit runs compare as ints).
These pins drive the real routes -- the JSON a door returns, the HTML a page
renders -- so a regression to ``sorted(xs)`` shows up as a wrong order on the
surface the customer actually sees, not as a unit-test on the helper.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_H = {"Origin": "http://localhost"}


# --------------------------------------------------------------- fixtures

def _qubit(qid: str) -> dict:
    return {
        "id": qid,
        "f_01": 6.25e9,
        "T1": 8834,
        "T2ramsey": 1.5e-6,
        "xy": {"RF_frequency": 6.25e9, "operations": {"x180": {"amplitude": 0.1}}},
        "resonator": {
            "RF_frequency": 7.64e9,
            "operations": {"readout": {"amplitude": 0.042, "length": 1000}},
        },
        "z": {"joint_offset": 0.081, "flux_point": "joint"},
    }


def _write_chip(folder: Path, qubit_ids, extra_state=None) -> Path:
    """A minimal loadable chip whose qubit ids are the ones asked for."""
    folder.mkdir(parents=True, exist_ok=True)
    state = {"qubits": {q: _qubit(q) for q in qubit_ids}, "qubit_pairs": {}}
    if extra_state:
        state.update(extra_state)
    wiring = {
        "wiring": {"qubits": {q: {"xy": {"opx_output": "MW-FEM/1/2"}}
                              for q in qubit_ids}},
        "network": {"host": "10.1.1.18"},
    }
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path):
    return create_app(testing=True,
                      instance_path=str(tmp_path / "_app_instance")).test_client()


@pytest.fixture
def chip(tmp_path):
    """q1, q2, q10, q11 -- the ids a plain sort gets wrong."""
    return _write_chip(tmp_path / "chip", ["q1", "q2", "q10", "q11"])


@pytest.fixture
def loaded(client, chip):
    resp = client.post("/load", data={"folder": str(chip)})
    assert resp.status_code in (200, 302)
    return client


def _order(haystack: str, needles) -> list[int]:
    """Where each needle first appears -- a strictly increasing list is the
    order the reader sees."""
    return [haystack.index(n) for n in needles]


def _is_ascending(positions) -> bool:
    return all(a < b for a, b in zip(positions, positions[1:]))


# ------------------------------------------------- the reported bug itself

class TestAgentLiveDiffPaths:
    """agent_api.live_diff -- the customer's exact screenshot, on the JSON
    door. The 300-row cap makes the order load-bearing: a lexicographic sort
    reports a DIFFERENT 300 paths than the numeric one."""

    def test_a_list_index_is_a_number(self, client, tmp_path):
        weights = [0.0] * 1200
        chip = _write_chip(tmp_path / "wchip", ["q1"])
        state = json.loads((chip / "state.json").read_text(encoding="utf-8"))
        state["qubits"]["q1"]["resonator"]["operations"]["readout"][
            "weights_imag"] = list(weights)
        (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
        client.post("/load", data={"folder": str(chip)})

        # An experiment (outside SM) rewrites three elements of the live file.
        live = json.loads((chip / "state.json").read_text(encoding="utf-8"))
        w = live["qubits"]["q1"]["resonator"]["operations"]["readout"]["weights_imag"]
        for i in (101, 1009, 1011):
            w[i] = 0.5
        (chip / "state.json").write_text(json.dumps(live), encoding="utf-8")

        d = client.get("/api/agent/live-diff").get_json()
        assert d["ok"] is True
        tail = [p.rsplit(".", 1)[-1] for p in
                (c["path"] for c in d["changed"]) if "weights_imag" in p]
        assert tail == ["101", "1009", "1011"], tail


# -------------------------------------------------------- the agent's doors

class TestAgentTargetLists:
    """A refusal that NAMES what it knows: both the offered roster and the
    rejected targets it echoes back read q2 before q10."""

    def test_unknown_target_names_the_known_ones_in_order(self, loaded):
        r = loaded.post("/api/agent/plans",
                        json={"title": "t", "steps": [
                            {"node": "05_x", "targets": ["qZ10", "qZ2"],
                             "why": "w"}]},
                        headers=_H)
        body = r.get_json()
        assert r.status_code == 400, body
        assert body["known"][:4] == ["q1", "q2", "q10", "q11"], body["known"]
        assert "['qZ2', 'qZ10']" in body["error"], body["error"]


# ---------------------------------------------------------- folder browser

class TestBrowseListsFoldersNumerically:
    """docs/114's folder picker: run2 before run10, and file rows too."""

    def _seed(self, tmp_path):
        root = tmp_path / "browse"
        for name in ("run1", "run2", "run10", "run11"):
            (root / name).mkdir(parents=True)
        for name in ("cfg1.toml", "cfg2.toml", "cfg10.toml"):
            (root / name).write_text("", encoding="utf-8")
        return root

    def test_directory_rows(self, client, tmp_path):
        root = self._seed(tmp_path)
        dirs = client.get(f"/browse?path={root}").get_json()["dirs"]
        assert [Path(d).name for d in dirs] == ["run1", "run2", "run10", "run11"]

    def test_typed_prefix_suggestions(self, client, tmp_path):
        root = self._seed(tmp_path)
        d = client.get(f"/browse?path={root / 'run'}&complete=1").get_json()
        assert [Path(x).name for x in d["dirs"]] == [
            "run1", "run2", "run10", "run11"]

    def test_config_mode_toml_rows(self, client, tmp_path):
        root = self._seed(tmp_path)
        files = client.get(f"/browse?path={root}&kind=config").get_json()["files"]
        assert [Path(f).name for f in files] == [
            "cfg1.toml", "cfg2.toml", "cfg10.toml"]


class TestLoadFailureOffersSubfoldersNumerically:
    """docs/114 (#15): a wrong folder offers the subfolders that DO hold a
    chip -- capped at 6, so the order decides which six are offered."""

    def test_candidate_panel(self, client, tmp_path):
        parent = tmp_path / "notachip"
        for name in ("chip1", "chip2", "chip10"):
            _write_chip(parent / name, ["q1"])
        html = client.post("/load", data={"folder": str(parent)}).get_data(as_text=True)
        assert _is_ascending(_order(html, ["chip1", "chip2", "chip10"])), html[:400]


# ------------------------------------------------------------ qubit axes

class TestParamHistoryQubitAxis:
    """The dashboard's row axis is a qubit list the route sorts."""

    def test_selected_qubits_render_in_numeric_order(self, client, chip, tmp_path):
        app = client.application
        hm = app.config["history_manager"]
        hm.check_and_snapshot(chip, trigger="manual", force=True)
        state = json.loads((chip / "state.json").read_text(encoding="utf-8"))
        for q in state["qubits"]:
            state["qubits"][q]["T1"] = 9999
        (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
        hm.check_and_snapshot(chip, trigger="manual", force=True)
        client.post("/load", data={"folder": str(chip)})

        html = client.get(
            "/param-history?props=T1&qubits=q10&qubits=q2&qubits=q11&qubits=q1",
            headers={"HX-Request": "true"}).get_data(as_text=True)
        pos = _order(html, ['data-qubit="q1"', 'data-qubit="q2"',
                            'data-qubit="q10"', 'data-qubit="q11"'])
        assert _is_ascending(pos), pos


class TestColumnHistoryRowAxis:
    """The Column History panel's rows are the grid's rows -- q10 belongs
    after q2 there too (docs/20 v2b)."""

    def test_rows_are_numeric(self, loaded):
        paths = {q: f"qubits.{q}.T1" for q in ("q10", "q2", "q11", "q1")}
        html = loaded.post("/bulk/column-history",
                           data={"grid": "qubit", "label": "T1",
                                 "paths": json.dumps(paths)},
                           headers=_H).get_data(as_text=True)
        pos = _order(html, ['data-row="q1"', 'data-row="q2"',
                            'data-row="q10"', 'data-row="q11"'])
        assert _is_ascending(pos), pos


class TestNotesEntityOrder:
    def test_notes_list_is_numeric(self, loaded):
        for q in ("q10", "q2", "q11", "q1"):
            r = loaded.post("/note",
                            data={"subject": f"qubits.{q}.T1", "text": f"n{q}"},
                            headers=_H)
            assert r.status_code == 200, r.get_data(as_text=True)[:200]
        present = loaded.get("/notes").get_json()["present"]
        assert [p["entity"] for p in present] == [
            "qubits.q1", "qubits.q2", "qubits.q10", "qubits.q11"]


# ------------------------------------------------------------- the datasets

def _seed_run(root: Path, run_id: int, name: str, *, date="2026-05-01",
              qubits=("q1",), tags=None) -> Path:
    run = root / date / f"#{run_id}_{name}_01{run_id:04d}"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00", "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": list(qubits)}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    return run


class TestDatasetsExperimentChips:
    """A node name carries a numeric prefix (2_ramsey, 10_readout): the
    experiment filter must read 2 before 10."""

    def _client(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        root = tmp_path / "ds"
        _seed_run(root, 1, "2_readout_b")
        _seed_run(root, 2, "10_readout_c")
        _seed_run(root, 3, "1_readout_a")
        app.config["dataset_store"] = DatasetStore(root)
        return app.test_client()

    def test_experiment_list_is_numeric(self, tmp_path):
        html = self._client(tmp_path).get("/datasets").get_data(as_text=True)
        chips = re.findall(r'class="exp-chip" data-exp="([^"]+)"', html)
        assert chips == ["1_readout_a", "2_readout_b", "10_readout_c"], chips


class TestTrendsSelectors:
    """/trends' two <select>s are flat lists the route sorts."""

    def test_experiment_and_qubit_options_are_numeric(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        root = tmp_path / "ds"
        _seed_run(root, 1, "2_ramsey", qubits=("q2", "q10"))
        _seed_run(root, 2, "10_readout", qubits=("q1", "q11"))
        _seed_run(root, 3, "1_res_spec", qubits=("q1",))
        app.config["dataset_store"] = DatasetStore(root)
        html = app.test_client().get("/trends").get_data(as_text=True)
        exps = re.findall(r'<option value="([^"]*_[^"]*)">', html)
        assert exps[:3] == ["1_res_spec", "2_ramsey", "10_readout"], exps
        qs = re.findall(r'<option value="(q\d+)">', html)
        assert qs == ["q1", "q2", "q10", "q11"], qs


class TestDatasetTagChips:
    def test_tags_json_is_numeric(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        root = tmp_path / "ds"
        _seed_run(root, 1, "2_ramsey")
        ds = DatasetStore(root)
        app.config["dataset_store"] = ds
        c = app.test_client()
        for t in ("cooldown10", "cooldown2", "cooldown1"):
            ds.add_tag(1, t)
        tags = c.get("/datasets/tags").get_json()["tags"]
        assert tags == ["cooldown1", "cooldown2", "cooldown10"], tags


# --------------------------------------------------------------- compare

class TestCompareQubitAxis:
    def test_two_chips_share_one_numeric_axis(self, client, tmp_path):
        a = _write_chip(tmp_path / "a", ["q2", "q10"])
        b = _write_chip(tmp_path / "b", ["q1", "q11"])
        from quam_state_manager.web import routes as R
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst2"))
        with app.test_request_context():
            _s, _c, _l, names = R._load_compare_stores([str(a), str(b)])
        assert names == ["q1", "q2", "q10", "q11"], names


# ------------------------------------------------------------------ QDAC

class TestQdacCablingRows:
    """The QDAC page's cabling table is keyed by (controller, slot, port) --
    all three STRINGS, so p10 used to sit above p2 (docs/136)."""

    def test_cables_are_listed_by_port_number(self, client, tmp_path):
        from tests.test_qdac_component import _chip
        merged = _chip(qdac_q=(("q1", 13, "ext1", 2), ("q2", 5, "ext2", 10)))
        folder = tmp_path / "qdacchip"
        folder.mkdir()
        state = {k: v for k, v in merged.items() if k != "wiring"}
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "wiring.json").write_text(
            json.dumps({"wiring": merged["wiring"]}), encoding="utf-8")
        client.post("/load", data={"folder": str(folder)})
        html = client.get("/qdac").get_data(as_text=True)
        pos = _order(html, ["con1/fem5/p2", "con1/fem5/p10"])
        assert _is_ascending(pos), pos

    def test_a_conflicting_cable_names_its_exts_in_order(self, client, tmp_path):
        """Two exts on ONE physical port is a wiring error; the ext names it
        prints are ext2 / ext10, not ext10 / ext2."""
        from tests.test_qdac_component import _chip
        merged = _chip(qdac_q=(("q1", 13, "ext10", 1), ("q2", 5, "ext2", 1)))
        folder = tmp_path / "qdacbad"
        folder.mkdir()
        state = {k: v for k, v in merged.items() if k != "wiring"}
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "wiring.json").write_text(
            json.dumps({"wiring": merged["wiring"]}), encoding="utf-8")
        client.post("/load", data={"folder": str(folder)})
        html = client.get("/qdac").get_data(as_text=True)
        assert "ext2 / ext10" in html, html[html.find("qdac-cabling"):][:600]


class TestCrPairTableOrder:
    """A CR chip's pair table groups the two DIRECTIONS of one physical edge
    by their (control, target) membership -- a qubit-name sort (docs/54)."""

    def test_edges_are_grouped_in_numeric_qubit_order(self, client, tmp_path):
        from tests import cr_fixtures
        state, wiring = cr_fixtures.make_flavor_b()
        # q0 -> q10 everywhere (ids, pair ids, pointers) so the chip carries the
        # double-digit id a plain sort gets wrong.
        state = json.loads(re.sub(r"\bq0\b", "q10", json.dumps(state)))
        wiring = json.loads(re.sub(r"\bq0\b", "q10", json.dumps(wiring)))
        folder = tmp_path / "crchip"
        folder.mkdir()
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        client.post("/load", data={"folder": str(folder)})
        html = client.get("/pairs").get_data(as_text=True)
        ids = re.findall(r'data-pair-id="([^"]+)"', html)
        seen, order = set(), []
        for pid in ids:
            if pid not in seen:
                seen.add(pid)
                order.append(pid)
        # (q1,q2) before (q1,q10) -- lexicographically it is the other way round.
        assert order.index("q1-2") < order.index("q10-1"), order


class TestCollectionTagChips:
    def test_tag_chips_are_numeric(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        root = tmp_path / "ds"
        _seed_run(root, 1, "2_ramsey")
        ds = DatasetStore(root)
        for t in ("cooldown10", "cooldown2", "cooldown1"):
            ds.add_tag(1, t)
        app.config["dataset_store"] = ds
        html = app.test_client().get("/collections").get_data(as_text=True)
        chips = re.findall(r'class="tag-chip[^"]*"\s+data-tag="([^"]+)"', html)
        assert chips == ["cooldown1", "cooldown2", "cooldown10"], chips


# ------------------------------------- the route module's own view builders

class TestCompareHubMapEditor:
    """The mapping editor lists entity names for a person to pair up."""

    def test_name_columns_are_numeric(self):
        from quam_state_manager.web import routes as R
        view = {"mapping": {"pairs": {"q10": "b10", "q2": "b2"},
                            "unmatched_a": ["q1", "q11"],
                            "unmatched_b": ["b1", "b11"]}}
        R._hub_map_view(view)
        assert view["map_editor"]["ref_names"] == ["q1", "q2", "q10", "q11"]
        assert view["map_editor"]["other_names"] == ["b1", "b2", "b10", "b11"]


class TestBuildOutputGuardStrayFiles:
    """The refusal names the stray .json files it found -- capped at 20, so
    the order decides which twenty a person is shown."""

    def test_stray_files_are_named_in_numeric_order(self, tmp_path):
        from quam_state_manager.web import routes as R
        out = tmp_path / "out"
        out.mkdir()
        for n in (1, 2, 10, 11):
            (out / f"run{n}.json").write_text("{}", encoding="utf-8")
        guard = R._build_output_guard(str(out))
        assert guard["conflict_files"] == [
            "run1.json", "run2.json", "run10.json", "run11.json"]


class TestDiffRunFigures:
    """The figures tab's column of file names (docs/147)."""

    def test_figure_names_are_numeric(self, tmp_path):
        from types import SimpleNamespace
        from quam_state_manager.web import routes as R
        run = tmp_path / "run"
        run.mkdir()
        for n in (1, 2, 10, 11):
            (run / f"fig{n}.png").write_bytes(b"x")
        names, _why = R._diff_run_figures(
            SimpleNamespace(origin="run", path=str(run / "node.json")))
        assert names == ["fig1.png", "fig2.png", "fig10.png", "fig11.png"]
