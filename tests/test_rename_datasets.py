"""docs/296: after a Re-generate rename, every Datasets surface reads an OLD
run in today's qubit names -- or refuses, never guesses.

The archive: runs #1-#2 on the chip as it was (q1, q2); the chip is rebuilt
with q1 -> q0, q2 -> q1 (the record goes into extras); runs #3-#4 on the
renamed chip. The live chip is the renamed one, so the old run's "q1" is
today's q0 and its "q2" is today's q1.
"""
from __future__ import annotations

import html as html_mod
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quam_state_manager.core import hub, rename_lineage, run_names
from quam_state_manager.core.fit_targets import resolve_fit_targets
from quam_state_manager.core.regen_merge import rebuilt_pairs, rename_plan, source_renames
from quam_state_manager.web import routes as routes_mod

T0 = int(datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1_000_000)
WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
EXP = "08_qubit_spectroscopy"


@pytest.fixture(autouse=True)
def _inline_ledger():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    yield
    hub.set_inline(old)


def chip_state(f01: dict, chain=()) -> dict:
    qs = {q: {"id": q, "f_01": f, "z": {"flux_point": "joint", "joint_offset": 0.01},
              "xy": {"RF_frequency": f, "operations": {"x180": {"amplitude": 0.1}}},
              "resonator": {"f_01": 7.0e9, "RF_frequency": 7.0e9}}
          for q, f in f01.items()}
    s = {"qubits": qs, "qubit_pairs": {}, "extras": {"chip_name": "device"}}
    if chain:
        s["extras"]["qubit_renames"] = list(chain)
    return s


def make_record(old_ids, new_ids, sources):
    old = {"qubits": {q: {"id": q} for q in old_ids}, "qubit_pairs": {}}
    new = {"qubits": {q: {"id": q} for q in new_ids}, "qubit_pairs": {}}
    ren = source_renames(sources, old, new["qubits"])
    tok, pmap, ids = rename_plan(old, None, ren, new, None)
    return rename_lineage.new_record(renames=ren, tokens=tok, pairs=pmap, source_qubits=ids,
                                     source_pairs=[], qubits_after=list(new_ids),
                                     pairs_after=rebuilt_pairs(new, None))


REC = make_record(["q1", "q2"], ["q0", "q1"], {"q0": "q1", "q1": "q2"})


def write_run(root: Path, rid: int, st: dict | None, fit: dict, name: str = EXP) -> Path:
    t_us = T0 + rid * 10_000_000
    when = datetime.fromtimestamp(t_us / 1e6, tz=timezone.utc)
    folder = root / "2026-01-01" / f"#{rid}_{name}_{when.strftime('%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    qubits = sorted(fit)
    body = {"created_at": when.isoformat(), "metadata": {"status": "finished", "name": name},
            "id": rid, "data": {"parameters": {"model": {"qubits": qubits}}}}
    (folder / "node.json").write_text(json.dumps(body), encoding="utf-8")
    (folder / "data.json").write_text(json.dumps({"fit_results": fit}), encoding="utf-8")
    if st is not None:
        (folder / "quam_state").mkdir(exist_ok=True)
        (folder / "quam_state" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (folder / "quam_state" / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    return folder


def make_env(tmp_path, runs: list[tuple], live: dict, *, ledger: bool = True,
             live_wiring: dict | None = None):
    """``runs``: ``[(state or None, fit_results)]`` -> runs #1..#N."""
    data, live_dir = tmp_path / "data", tmp_path / "chips" / "live"
    folders = [write_run(data, i, st, fit) for i, (st, fit) in enumerate(runs, 1)]
    live_dir.mkdir(parents=True)
    live = json.loads(json.dumps(live))
    live["extras"]["data_folder"] = str(data)
    (live_dir / "state.json").write_text(json.dumps(live), encoding="utf-8")
    (live_dir / "wiring.json").write_text(json.dumps(live_wiring or WIRING), encoding="utf-8")
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    app.config["HUB_SYNC_ON_OPEN"] = ledger
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live_dir)}).status_code in (200, 302)
    c.post("/workspace/add", data={"folder": str(data)})
    key = routes_mod._folder_key(data)
    return {"app": app, "client": c, "data": data, "live": live_dir, "folders": folders,
            "uid": lambda rid: f"{key}:{rid}", "key": key}


def ctx_of(env):
    return next(iter(env["app"].config["contexts"].values()))


def live_state(env) -> dict:
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))


OLD1 = chip_state({"q1": 5.0e9, "q2": 6.0e9})
OLD2 = chip_state({"q1": 5.1e9, "q2": 6.1e9})
NEW3 = chip_state({"q0": 5.1e9, "q1": 6.1e9}, [REC])
NEW4 = chip_state({"q0": 5.2e9, "q1": 6.2e9}, [REC])


@pytest.fixture
def shifted(tmp_path):
    runs = [(OLD1, {"q1": {"frequency": 5.01e9}, "q2": {"frequency": 6.01e9}}),
            (OLD2, {"q1": {"frequency": 5.11e9}, "q2": {"frequency": 6.11e9}}),
            (NEW3, {"q0": {"frequency": 5.12e9}, "q1": {"frequency": 6.12e9}}),
            (NEW4, {"q0": {"frequency": 5.21e9}, "q1": {"frequency": 6.21e9}})]
    return make_env(tmp_path, runs, NEW4)


@pytest.fixture
def plain(tmp_path):
    """A chip that was never renamed."""
    runs = [(OLD1, {"q1": {"frequency": 5.01e9}, "q2": {"frequency": 6.01e9}}),
            (OLD2, {"q1": {"frequency": 5.11e9}, "q2": {"frequency": 6.11e9}})]
    return make_env(tmp_path, runs, OLD2)


def names_for(env, folder):
    with env["app"].test_request_context():
        return routes_mod._dataset_run_names(folder)


def text(html: str) -> str:
    return html_mod.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))


# ---------------------------------------------------------------------------
# a run's era, and the one translation
# ---------------------------------------------------------------------------

class TestTheRunsEra:
    def test_an_old_run_reads_in_todays_names(self, shifted):
        names = names_for(shifted, shifted["folders"][0])
        assert names.src == () and names.dst == (REC["id"],)
        assert names.current("q1") == "q0" and names.current("q2") == "q1"
        assert names.target("qubits.q2.f_01") == "qubits.q1.f_01"

    def test_a_run_of_todays_era_is_the_identity(self, shifted):
        names = names_for(shifted, shifted["folders"][2])
        assert names.identity
        assert names.target("qubits.q1.f_01") == "qubits.q1.f_01"

    def test_the_era_comes_from_the_ledger_when_the_run_file_is_gone(self, shifted):
        import shutil
        old = shifted["folders"][0]
        shutil.rmtree(old / "quam_state")
        rename_lineage._FOLDER_RECORDS.clear()
        assert names_for(shifted, old).src == ()
        new = shifted["folders"][3]
        shutil.rmtree(new / "quam_state")
        assert names_for(shifted, new).src == (REC["id"],)

    def test_a_never_renamed_chip_translates_nothing(self, plain):
        assert names_for(plain, plain["folders"][0]) is None


# ---------------------------------------------------------------------------
# 1. Datasets "Apply fitted value"
# ---------------------------------------------------------------------------

class TestApplyFittedValue:
    def _detail(self, env, rid):
        r = env["client"].get(f"/dataset/{env['uid'](rid)}", headers={"HX-Request": "true"})
        assert r.status_code == 200, r.data[:400]
        return r.data.decode()

    def test_an_old_runs_fit_targets_its_qubit_by_todays_name(self, shifted):
        html = self._detail(shifted, 1)
        paths = dict(re.findall(r'data-fit-path="([^"]+)" data-fit-value="([^"]+)"', html))
        # run #1's q1 is today's q0, its q2 today's q1 -- each value on its own qubit
        assert float(paths["qubits.q0.f_01"]) == 5.01e9
        assert float(paths["qubits.q1.f_01"]) == 6.01e9
        assert "now q0" in html and "now q1" in html

    def test_a_run_of_todays_era_is_unchanged(self, shifted):
        html = self._detail(shifted, 3)
        paths = dict(re.findall(r'data-fit-path="([^"]+)" data-fit-value="([^"]+)"', html))
        assert float(paths["qubits.q0.f_01"]) == 5.12e9
        assert float(paths["qubits.q1.f_01"]) == 6.12e9

    def test_a_qubit_with_no_name_today_is_refused(self, tmp_path):
        old = chip_state({"q1": 5.0e9, "q2": 6.0e9, "q3": 7.0e9})
        env = make_env(tmp_path, [(old, {"q3": {"frequency": 7.01e9},
                                         "q1": {"frequency": 5.01e9}})], NEW4)
        html = self._detail(env, 1)
        assert "qubits.q3.f_01" not in html, "never a guessed target"
        assert 'data-fit-path="qubits.q0.f_01"' in html
        assert "Not applied: q3 of this run has no qubit on the loaded chip" in text(html)

    def test_a_run_whose_era_is_unknown_is_refused(self, tmp_path):
        # no saved state, and no ledger to say which names it used
        env = make_env(tmp_path, [(None, {"q1": {"frequency": 5.01e9}})], NEW4, ledger=False)
        html = self._detail(env, 1)
        assert "data-fit-path=" not in html
        assert "does not say which names it used" in text(html)

    def test_a_never_renamed_chip_renders_as_before(self, plain):
        run = {"experiment_name": EXP, "fit_results": {"q1": {"frequency": 5.0e9}}}
        assert resolve_fit_targets(run) == resolve_fit_targets(run, names=None)
        html = TestApplyFittedValue._detail(self, plain, 1)
        assert 'data-fit-path="qubits.q1.f_01"' in html and 'data-fit-path="qubits.q2.f_01"' in html
        assert "fit-renamed" not in html and "Not applied" not in html


# ---------------------------------------------------------------------------
# 2. Interactive click contracts and the Data tab's value chip
# ---------------------------------------------------------------------------

def _fig_with(monkeypatch, clickable):
    from quam_state_manager.core import interactive_plots
    monkeypatch.setattr(interactive_plots, "build_interactive_figure",
                        lambda run, key: {"kind": "1d", "title": "t", "data": [], "layout": {},
                                          "clickable": json.loads(json.dumps(clickable))})


def _contract_clickable(state, qname):
    """A real click contract (06 res-vs-flux), baked from the RUN's snapshot."""
    from quam_state_manager.core.interactive_plots import contracts
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    return contracts.flux_absolute_targets(Bundle(run=None, quam_state=state), qname)


class TestInteractiveClick:
    def _plot(self, env, rid):
        r = env["client"].get(f"/dataset/{env['uid'](rid)}/interactive/plot?fig=x")
        assert r.status_code == 200, r.data[:300]
        return r.get_json()["clickable"]

    def test_an_old_runs_contract_writes_its_own_qubit(self, shifted, monkeypatch):
        clk = _contract_clickable(OLD1, "q2")
        assert clk["targets"][0]["path"] == "qubits.q2.z.joint_offset"   # the run's name
        _fig_with(monkeypatch, clk)
        out = self._plot(shifted, 1)
        assert [t["path"] for t in out["targets"]] == ["qubits.q1.z.joint_offset"]
        assert out["names"]["q2"] == "q1" and out["names"]["q1"] == "q0"
        assert out["qubit"] == "q2", "the run's own name still labels the run's fit"
        assert out["refused"] is None

    def test_a_templated_path_is_filled_from_the_names_map(self, shifted, monkeypatch):
        _fig_with(monkeypatch, {"axis": "x", "qubit": "q1", "label": "Set qubit frequency",
                                "targets": [{"path": "qubits.{q}.f_01", "scale": 1e9}]})
        out = self._plot(shifted, 2)
        assert out["targets"][0]["path"] == "qubits.{q}.f_01"
        assert out["names"]["q1"] == "q0"

    def test_a_qubit_with_no_name_today_refuses_the_click(self, tmp_path, monkeypatch):
        old = chip_state({"q1": 5.0e9, "q2": 6.0e9, "q3": 7.0e9})
        env = make_env(tmp_path, [(old, {"q3": {"frequency": 7.01e9}})], NEW4)
        _fig_with(monkeypatch, _contract_clickable(old, "q3"))
        out = self._plot(env, 1)
        assert out["targets"] == []
        assert out["refused"].startswith("Not applied: q3 of this run has no qubit")

    def test_a_coupled_target_with_no_name_today_refuses_the_whole_click(self, tmp_path,
                                                                          monkeypatch):
        old = chip_state({"q1": 5.0e9, "q2": 6.0e9, "q3": 7.0e9})
        env = make_env(tmp_path, [(old, {"q1": {"frequency": 5.01e9}})], NEW4)
        # q1 has a name today (q0); a coupled target names q3, which has none:
        # the click is refused whole, never staged in part
        _fig_with(monkeypatch, {"axis": "x", "qubit": "q1", "label": "x",
                                "targets": [{"path": "qubits.q1.f_01", "scale": 1e9},
                                            {"path": "qubits.q3.z.joint_offset", "scale": 1}]})
        out = self._plot(env, 1)
        assert out["targets"] == []
        assert out["refused"].startswith("Not applied: q3 of this run")

    def test_a_run_of_todays_era_is_untouched(self, shifted, monkeypatch):
        clk = _contract_clickable(NEW3, "q1")
        _fig_with(monkeypatch, clk)
        assert self._plot(shifted, 3) == clk

    def test_a_plain_chip_is_untouched(self, plain, monkeypatch):
        clk = _contract_clickable(OLD1, "q1")
        _fig_with(monkeypatch, clk)
        assert self._plot(plain, 1) == clk


class TestDataTabValueChip:
    def _cube(self, env, rid, monkeypatch):
        from quam_state_manager.core import ndview
        monkeypatch.setattr(ndview, "list_h5_files", lambda folder: ["ds_raw.h5"])
        monkeypatch.setattr(ndview, "build_cube_bytes", lambda path, var: (
            b'{"ok":true}', {"ok": True, "default_view": {"x": "full_freq", "y": None,
                                                          "entity": "qubit"}}))
        r = env["client"].get(f"/dataset/{env['uid'](rid)}/ndview/data?which=ds_raw.h5&var=IQ")
        assert r.status_code == 200
        return json.loads(r.data)["click"]

    def test_an_old_runs_entities_carry_todays_names(self, shifted, monkeypatch):
        click = self._cube(shifted, 1, monkeypatch)
        assert click["names"] == {"q1": "q0", "q2": "q1"}
        assert click["refusals"] == {}
        assert any(c["path"] == "qubits.{q}.f_01" for c in click["candidates"])

    def test_an_entity_with_no_name_today_has_a_refusal(self, tmp_path, monkeypatch):
        old = chip_state({"q1": 5.0e9, "q2": 6.0e9, "q3": 7.0e9})
        env = make_env(tmp_path, [(old, {"q1": {"frequency": 5.0e9}, "q3": {"frequency": 7.0e9}})],
                       NEW4)
        click = self._cube(env, 1, monkeypatch)
        # every id of the run's era is mapped (review: a run whose node.json
        # lists no qubits still labels its data by them)
        assert click["names"] == {"q1": "q0", "q2": "q1", "q3": None}
        assert click["refusals"]["q3"].startswith("Not applied: q3")
        assert "q2" not in click["refusals"]

    def test_todays_era_adds_nothing(self, shifted, monkeypatch):
        assert "names" not in self._cube(shifted, 3, monkeypatch)

    def test_a_plain_chip_adds_nothing(self, plain, monkeypatch):
        assert "names" not in self._cube(plain, 1, monkeypatch)


# ---------------------------------------------------------------------------
# 3. Dataset "Apply to chip" -- the run's state moved forward first
# ---------------------------------------------------------------------------

class TestApplyRunStateToChip:
    def test_an_old_runs_state_lands_on_each_qubits_own_name(self, shifted):
        r = shifted["client"].post(f"/dataset/{shifted['uid'](2)}/load-state?apply=1")
        assert r.status_code == 200, r.data[:600]
        assert "carried into today's names" in text(r.data.decode())
        live = live_state(shifted)
        assert sorted(live["qubits"]) == ["q0", "q1"], "the rename is never undone"
        assert live["qubits"]["q0"]["f_01"] == 5.1e9      # run #2's q1
        assert live["qubits"]["q1"]["f_01"] == 6.1e9      # run #2's q2
        assert rename_lineage.era(live) == (REC["id"],)

    def test_stage_only_moves_it_forward_too(self, shifted):
        r = shifted["client"].post(f"/dataset/{shifted['uid'](1)}/load-state")
        assert r.status_code == 200, r.data[:600]
        merged = ctx_of(shifted)["store"].merged
        assert merged["qubits"]["q1"]["f_01"] == 6.0e9 and "q2" not in merged["qubits"]
        assert live_state(shifted)["qubits"]["q1"]["f_01"] == 6.2e9, "live untouched by a stage"

    def test_a_state_that_cannot_be_moved_forward_is_refused(self, tmp_path):
        other = make_record(["q1", "q2"], ["q0", "q1"], {"q0": "q1", "q1": "q2"})
        assert other["id"] != REC["id"]
        env = make_env(tmp_path, [(chip_state({"q0": 5.0e9, "q1": 6.0e9}, [other]),
                                   {"q0": {"frequency": 5.0e9}})], NEW4)
        before = (env["live"] / "state.json").read_bytes()
        r = env["client"].post(f"/dataset/{env['uid'](1)}/load-state?apply=1&force_chip=1")
        assert r.status_code == 409, r.data[:600]
        assert "Not loaded: this run's qubit names cannot be carried" in text(r.data.decode())
        assert (env["live"] / "state.json").read_bytes() == before
        assert ctx_of(env)["store"].merged["qubits"]["q0"]["f_01"] == 5.2e9

    def test_a_plain_chip_stages_the_run_state_as_it_is(self, plain):
        r = plain["client"].post(f"/dataset/{plain['uid'](1)}/load-state?apply=1")
        assert r.status_code == 200
        assert "today's names" not in r.data.decode()
        live = live_state(plain)
        assert live["qubits"]["q1"]["f_01"] == 5.0e9 and live["qubits"]["q2"]["f_01"] == 6.0e9


# ---------------------------------------------------------------------------
# 4. Datasets > Trends -- one series per physical qubit
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_trends():
    from quam_state_manager.core import trend_index as ti
    for m in (ti.INDEX_MEMO, ti.SERIES_MEMO, ti.PARAMS_MEMO, routes_mod._TRENDS_SAME_CHIP):
        m.clear()
    ti._store_names.clear()
    yield ti


def series(env, qubit=None) -> dict:
    q = {"experiment": EXP, "folders": env["key"]}
    if qubit:
        q["qubit"] = qubit
    r = env["client"].get("/trends/series", query_string=q)
    assert r.status_code == 200, r.data[:300]
    return json.loads(r.data)


def by_key(payload) -> dict:
    return {(s["q"], s["m"]): s["v"] for s in payload["series"]}


class TestTrends:
    def test_one_physical_qubit_is_one_series(self, shifted, fresh_trends):
        p = series(shifted)
        assert by_key(p) == {("q0", "frequency"): [5.01e9, 5.11e9, 5.12e9, 5.21e9],
                             ("q1", "frequency"): [6.01e9, 6.11e9, 6.12e9, 6.21e9]}
        assert p["renamed"] == 2 and p["unmatched"] == 0

    def test_the_qubit_filter_and_the_picker_use_todays_names(self, shifted, fresh_trends):
        p = series(shifted, "q0")
        assert [r[0] for r in p["runs"]] == [1, 2, 3, 4]
        assert by_key(p) == {("q0", "frequency"): [5.01e9, 5.11e9, 5.12e9, 5.21e9]}
        html = shifted["client"].get("/trends").data.decode()
        opts = set(re.findall(r'<option value="(q\d+)"', html))
        assert opts == {"q0", "q1"}, opts

    def test_a_name_with_no_match_today_is_counted_not_drawn(self, tmp_path, fresh_trends):
        # the rebuild ADDED a brand-new q3; an older run's q3 (removed before
        # the rebuild) is another physical qubit and never joins its series
        rec = make_record(["q1", "q2"], ["q0", "q1", "q3"], {"q0": "q1", "q1": "q2"})
        old = chip_state({"q1": 5.0e9, "q2": 6.0e9, "q3": 7.0e9})
        new = chip_state({"q0": 5.2e9, "q1": 6.2e9, "q3": 8.0e9}, [rec])
        env = make_env(tmp_path, [(old, {"q1": {"frequency": 5.01e9}, "q3": {"frequency": 7.01e9}}),
                                  (new, {"q0": {"frequency": 5.21e9}, "q3": {"frequency": 8.01e9}})],
                       new)
        p = series(env)
        assert by_key(p) == {("q0", "frequency"): [5.01e9, 5.21e9],
                             ("q3", "frequency"): [None, 8.01e9]}
        assert p["unmatched"] == 1 and p["renamed"] == 0

    def test_a_run_of_an_unknown_rename_is_counted_not_drawn(self, tmp_path, fresh_trends):
        other = make_record(["q1", "q2"], ["q0", "q1"], {"q0": "q1", "q1": "q2"})
        env = make_env(tmp_path, [(chip_state({"q0": 1.0, "q1": 2.0}, [other]),
                                   {"q0": {"frequency": 1.0}}),
                                  (NEW4, {"q0": {"frequency": 5.21e9}})], NEW4)
        p = series(env)
        assert by_key(p) == {("q0", "frequency"): [None, 5.21e9]}
        assert p["unmatched"] == 1

    def test_a_rename_invalidates_every_trends_cache(self, tmp_path, fresh_trends):
        # the chip is used as it was, then the rebuilt state is applied to it
        env = make_env(tmp_path, [(OLD1, {"q1": {"frequency": 5.01e9}, "q2": {"frequency": 6.01e9}}),
                                  (NEW3, {"q0": {"frequency": 5.12e9}, "q1": {"frequency": 6.12e9}})],
                       OLD1)
        before = by_key(series(env))
        assert ("q2", "frequency") in before, "a chip never renamed reads names as spelled"
        r = env["client"].post(f"/dataset/{env['uid'](2)}/load-state?apply=1")
        assert r.status_code == 200, r.data[:400]
        after = series(env)
        assert by_key(after) == {("q0", "frequency"): [5.01e9, 5.12e9],
                                 ("q1", "frequency"): [6.01e9, 6.12e9]}

    def test_a_never_renamed_chip_serves_the_same_payload(self, plain, fresh_trends):
        p = series(plain)
        assert "renamed" not in p and "unmatched" not in p
        assert by_key(p) == {("q1", "frequency"): [5.01e9, 5.11e9],
                             ("q2", "frequency"): [6.01e9, 6.11e9]}
        assert fresh_trends._store_names == {}

    def test_another_chips_folder_is_not_translated(self, shifted, tmp_path, fresh_trends):
        other = tmp_path / "other_data"
        write_run(other, 1, OLD1, {"q1": {"frequency": 9.0e9}})
        shifted["client"].post("/workspace/add", data={"folder": str(other)})
        q = {"experiment": EXP, "folders": routes_mod._folder_key(other)}
        p = json.loads(shifted["client"].get("/trends/series", query_string=q).data)
        assert by_key(p) == {("q1", "frequency"): [9.0e9]} and "renamed" not in p


def _sc_rename_after_index(tmp_path, ti) -> bool:
    """The rename translation changes under an index built for the same
    store generation: only the ``names`` key component tells them apart."""
    from quam_state_manager.core.dataset import DatasetStore
    root = tmp_path / "d"
    write_run(root, 1, OLD1, {"q1": {"frequency": 5.0e9}})
    store = DatasetStore(root)
    sel = [("k", store)]
    ti.set_names(sel, None)
    json.loads(ti.series_blob(sel, EXP, None).json_bytes())
    chip = run_names.ChipNames(None, NEW4)
    ti.set_names(sel, {store.instance_seq: run_names.StoreNames(chip)})
    served_ = json.loads(ti.series_blob(sel, EXP, None).json_bytes())
    cold_ = json.loads(json.dumps(ti.cold_series_payload(sel, EXP, None)))
    return served_ != cold_


def test_the_names_key_component_is_load_bearing(tmp_path, fresh_trends, monkeypatch):
    ti = fresh_trends
    assert _sc_rename_after_index(tmp_path / "real", ti) is False
    for m in (ti.INDEX_MEMO, ti.SERIES_MEMO, ti.PARAMS_MEMO):
        m.clear()
    real = ti._key
    monkeypatch.setattr(ti, "_key", lambda **kw: real(**{k: v for k, v in kw.items()
                                                          if k != "names"}))
    assert _sc_rename_after_index(tmp_path / "mut", ti) is True, \
        "dropping 'names' from the keys went unnoticed"


# ---------------------------------------------------------------------------
# 5. Compare's Trend Tracker
# ---------------------------------------------------------------------------

def _trend_chart(env, rids, props=("f_01",)):
    from flask import template_rendered
    seen = {}

    def grab(sender, template, context, **extra):
        if template.name == "_trend_chart.html":
            seen.update(context)
    paths = [str(env["folders"][i - 1] / "quam_state") for i in rids]
    with template_rendered.connected_to(grab, env["app"]):
        r = env["client"].post("/trend/chart", data={"paths": paths, "props": list(props)},
                               headers={"HX-Request": "true"})
    assert r.status_code == 200, r.data[:400]
    return seen, r.data.decode()


def _rows(ctx):
    return {row["qubit"]: [v["value"] for v in row["values"]] for row in ctx["trend_data"]}


class TestCompareTrendTracker:
    def test_each_run_is_keyed_by_todays_name(self, shifted):
        ctx, html = _trend_chart(shifted, [1, 3])
        assert _rows(ctx) == {"q0": [5.0e9, 5.1e9], "q1": [6.0e9, 6.1e9]}
        assert "shown under today's qubit names" in text(html)

    def test_the_picker_offers_todays_names(self, shifted):
        paths = [str(shifted["folders"][i] / "quam_state") for i in (0, 2)]
        html = shifted["client"].post("/trend", data={"paths": paths},
                                      headers={"HX-Request": "true"}).data.decode()
        assert 'value="q2"' not in html and 'value="q0"' in html

    def test_a_run_that_cannot_be_moved_forward_is_left_out(self, tmp_path):
        other = make_record(["q1", "q2"], ["q0", "q1"], {"q0": "q1", "q1": "q2"})
        env = make_env(tmp_path, [(OLD1, {}), (NEW3, {}),
                                  (chip_state({"q0": 1.0, "q1": 2.0}, [other]), {})], NEW4)
        ctx, html = _trend_chart(env, [1, 2, 3])
        assert _rows(ctx) == {"q0": [5.0e9, 5.1e9], "q1": [6.0e9, 6.1e9]}
        assert "Not shown:" in text(html)

    def test_a_plain_chip_is_unchanged(self, plain):
        ctx, html = _trend_chart(plain, [1, 2])
        assert _rows(ctx) == {"q1": [5.0e9, 5.1e9], "q2": [6.0e9, 6.1e9]}
        assert ctx.get("rename_note") is None and "trend-rename-note" not in html


def test_the_clients_fill_todays_name_or_refuse():
    """The REAL app.js click handler and ndview.js value chip, driven under
    jsdom by ``tests/rename_click_selfcheck.cjs``."""
    import subprocess
    root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(["node", str(root / "tests" / "rename_click_selfcheck.cjs")],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(root),
                          timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") == 15, proc.stdout


def test_a_never_renamed_chip_reads_no_run_era(plain, fresh_trends, monkeypatch):
    """A chip with no rename record pays nothing: no ledger era read, no run
    state opened for its era, on any Datasets surface."""
    from quam_state_manager.core import value_history

    def boom(*a, **k):
        raise AssertionError("a run era was read for a chip that was never renamed")
    from quam_state_manager.core import run_names
    # every door a run's era or identity is read through (review: the pin
    # once blocked only run_eras while the code called run_eras_known)
    for mod, name in ((value_history, "run_eras"), (value_history, "run_eras_known"),
                      (value_history, "run_chip_uncertain"), (value_history, "run_era"),
                      (rename_lineage, "folder_era"), (run_names, "_saved_pair")):
        monkeypatch.setattr(mod, name, boom)
    c, uid = plain["client"], plain["uid"](1)
    assert c.get(f"/dataset/{uid}", headers={"HX-Request": "true"}).status_code == 200
    _fig_with(monkeypatch, _contract_clickable(OLD1, "q1"))
    assert c.get(f"/dataset/{uid}/interactive/plot?fig=x").status_code == 200
    assert series(plain)["series"]
    assert c.get("/trends").status_code == 200
    _trend_chart(plain, [1, 2])
    assert c.post(f"/dataset/{uid}/load-state?apply=1").status_code == 200


# ---------------------------------------------------------------------------
# 3b. "Apply selected to chip" -- each picked field on its own qubit's name
# ---------------------------------------------------------------------------

def _pick(env, rid, paths, which="state"):
    r = env["client"].post(f"/dataset/{env['uid'](rid)}/apply-selected/preview",
                           json={"file": which, "paths": paths})
    assert r.status_code == 200, r.data[:400]
    d = r.get_json()
    return d, {row["path"]: row for row in d["rows"]}


class TestApplySelected:
    def test_an_old_runs_fields_are_written_under_todays_names(self, shifted):
        d, rows = _pick(shifted, 1, ["qubits.q2.f_01", "qubits.q1.xy.RF_frequency"])
        assert rows["qubits.q1.f_01"]["new"] == 6.0e9          # run #1's q2 is today's q1
        assert rows["qubits.q1.f_01"]["from"] == "qubits.q2.f_01"
        assert rows["qubits.q1.f_01"]["old"] == 6.2e9 and rows["qubits.q1.f_01"]["status"] == "change"
        assert rows["qubits.q0.xy.RF_frequency"]["new"] == 5.0e9
        assert "qubits.q2.f_01" not in rows
        assert "q2 → q1" in d["renamed"]

    def test_a_wiring_field_follows_its_qubit_too(self, tmp_path):
        old = chip_state({"q1": 5.0e9, "q2": 6.0e9, "q3": 7.0e9})
        env = make_env(tmp_path, [(old, {"q1": {"frequency": 5.0e9}})], NEW4)
        run_wiring = env["folders"][0] / "quam_state" / "wiring.json"
        run_wiring.write_text(json.dumps({**WIRING, "wiring": {"qubits": {
            "q1": {"z": {"opx_output": "#/ports/a/1"}}, "q2": {"z": {"opx_output": "#/ports/a/2"}},
            "q3": {"z": {"opx_output": "#/ports/a/3"}}}}}), encoding="utf-8")
        _d, rows = _pick(env, 1, ["wiring.qubits.q2.z.opx_output", "wiring.qubits.q3.z.opx_output"],
                         which="wiring")
        assert sorted(rows) == ["wiring.qubits.q1.z.opx_output", "wiring.qubits.q3.z.opx_output"]
        assert rows["wiring.qubits.q1.z.opx_output"]["new"] == "#/ports/a/2"
        assert rows["wiring.qubits.q3.z.opx_output"]["status"] == "skip", "q3 has no qubit today"

    def test_a_field_with_no_qubit_today_is_skipped_with_the_reason(self, tmp_path):
        old = chip_state({"q1": 5.0e9, "q2": 6.0e9, "q3": 7.0e9})
        env = make_env(tmp_path, [(old, {"q1": {"frequency": 5.0e9}})], NEW4)
        _d, rows = _pick(env, 1, ["qubits.q3.f_01"])
        assert rows["qubits.q3.f_01"]["status"] == "skip"
        assert rows["qubits.q3.f_01"]["reason"].startswith("Not applied: q3 of this run")

    def test_a_qubit_new_in_the_rebuild_never_takes_an_old_qubits_field(self, tmp_path):
        # the rebuild ADDED a brand-new q3; the run's q3 is the old chip's q3,
        # removed before the rebuild -- another physical qubit of the same name
        rec = make_record(["q1", "q2"], ["q0", "q1", "q3"], {"q0": "q1", "q1": "q2"})
        old = chip_state({"q1": 5.0e9, "q2": 6.0e9, "q3": 7.0e9})
        new = chip_state({"q0": 5.2e9, "q1": 6.2e9, "q3": 8.0e9}, [rec])
        ports = {"wiring": {"qubits": {q: {"z": {"delay": 16}} for q in ("q0", "q1", "q3")}}}
        env = make_env(tmp_path, [(old, {"q3": {"frequency": 7.01e9}})], new,
                       live_wiring={**WIRING, **ports})
        (env["folders"][0] / "quam_state" / "wiring.json").write_text(json.dumps(
            {**WIRING, "wiring": {"qubits": {"q3": {"z": {"delay": 24}}}}}), encoding="utf-8")
        _d, rows = _pick(env, 1, ["qubits.q3.f_01"])
        assert rows["qubits.q3.f_01"]["status"] == "skip"
        _d, rows = _pick(env, 1, ["wiring.qubits.q3.z.delay"], which="wiring")
        assert rows["wiring.qubits.q3.z.delay"]["status"] == "skip"
        assert rows["wiring.qubits.q3.z.delay"]["reason"].startswith("Not applied: q3")
        html = env["client"].get(f"/dataset/{env['uid'](1)}", headers={"HX-Request": "true"}).data
        assert b'data-fit-path="qubits.q3.f_01"' not in html

    def test_todays_era_and_a_plain_chip_are_unchanged(self, shifted):
        d, rows = _pick(shifted, 3, ["qubits.q1.f_01"])
        assert list(rows) == ["qubits.q1.f_01"] and "from" not in rows["qubits.q1.f_01"]
        assert "renamed" not in d

    def test_a_plain_chip_previews_the_runs_paths(self, plain):
        d, rows = _pick(plain, 1, ["qubits.q2.f_01"])
        assert rows["qubits.q2.f_01"]["new"] == 6.0e9 and "renamed" not in d
