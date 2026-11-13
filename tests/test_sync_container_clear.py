"""A field the user turned from a list into a scalar is not a live change.

The leaf diff flattens containers, so a port filter cleared to None in the
working copy shows up against the live chip as the path "removed" plus each
live tap "added" beneath it. The collision check must read those entries back
into the list and compare it with the value the edit started from: equal
means only the user moved the field. Found adding a qubit on a reused flux
port with its previous line's filters cleared, whose Apply was refused as a
collision although the live files had not changed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from make_synthetic_chip import make_chip  # noqa: E402
from quam_state_manager.core import diagram_add as da  # noqa: E402
from quam_state_manager.core import sync_conflict as sc  # noqa: E402
from quam_state_manager.core.differ import Differ  # noqa: E402
from test_diagram_add_flow import _freed_port, _form  # noqa: E402

IIR = [[0.1, 200.0], [0.05, 1000.0]]


class _E:
    def __init__(self, path, old, new=None):
        self.dot_path, self.old_value, self.new_value = path, old, new


def _verdict(working, live, log):
    entries = Differ().diff((working, {}), (live, {}), ignore_keys=set())
    return sc.classify(live_by_path=sc.live_view(entries), change_log=log)


class TestClassify:
    def test_a_list_cleared_to_none_over_an_unmoved_live_is_mine_only(self):
        v = _verdict({"p": {"f": None}}, {"p": {"f": IIR}}, [_E("p.f", IIR, None)])
        assert v.conflicts == () and "p.f" in v.mine

    def test_a_dict_cleared_to_none_over_an_unmoved_live_is_mine_only(self):
        orig = {"a": 1, "b": [2, 3]}
        v = _verdict({"p": {"f": None}}, {"p": {"f": orig}}, [_E("p.f", orig, None)])
        assert v.conflicts == ()

    def test_the_live_list_moved_is_still_a_collision(self):
        moved = [[0.1, 200.0], [0.05, 999.0]]
        v = _verdict({"p": {"f": None}}, {"p": {"f": moved}}, [_E("p.f", IIR, None)])
        assert v.conflicts == ("p.f",)

    def test_a_live_list_that_grew_is_still_a_collision(self):
        grew = IIR + [[0.02, 5000.0]]
        v = _verdict({"p": {"f": None}}, {"p": {"f": grew}}, [_E("p.f", IIR, None)])
        assert v.conflicts == ("p.f",)

    def test_a_live_list_that_shrank_is_still_a_collision(self):
        v = _verdict({"p": {"f": None}}, {"p": {"f": IIR[:1]}}, [_E("p.f", IIR, None)])
        assert v.conflicts == ("p.f",)

    def test_a_key_the_live_chip_dropped_is_still_a_collision(self):
        v = _verdict({"p": {"f": None}}, {"p": {}}, [_E("p.f", IIR, None)])
        assert v.conflicts == ("p.f",)


@pytest.fixture(autouse=True)
def _env_check_passes(monkeypatch):
    from quam_state_manager.web import diagram_remove_routes as DR
    monkeypatch.setattr(DR, "env_python", lambda: "python")
    monkeypatch.setattr(DR, "run_env_check", lambda py, st, wi: {
        "ok": True, "error": None, "config": {"elements": {}}, "seconds": 0.0})
    DR._VERDICTS.clear()


@pytest.fixture
def chip_env(tmp_path):
    from quam_state_manager.web.app import create_app
    s, w = make_chip(20)
    _freed_port(s, w)
    s.pop("_freed", None)
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(s), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(w), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    ctx = next(iter(app.config["contexts"].values()))
    return {"c": c, "store": ctx["store"], "chip": chip}


def _stage_on_reused_port(env):
    c, st = env["c"], env["store"]
    freed = next(x for x in da.flux_candidates(da.inventory(st.merged), limit=0)
                 if x["carries"])
    d, form = _form(c, st, flux=f"{freed['con']}/{freed['fem']}/{freed['port']}")
    r = c.post("/diagram-add/stage", data=dict(form, dry="0"))
    assert r.status_code == 200, r.data
    return d["name"], (freed["con"], str(freed["fem"]), str(freed["port"]))


def _live_port(env, port):
    s = json.loads((env["chip"] / "state.json").read_text(encoding="utf-8"))
    con, fem, pid = port
    return s, s["ports"]["analog_outputs"][con][fem][pid]


def _apply(c):
    return c.post("/state/sync", data={"mode": "apply", "check_collisions": "1"}).get_json()


def test_the_add_on_a_reused_port_applies_with_its_filters_cleared(chip_env):
    name, port = _stage_on_reused_port(chip_env)
    j = _apply(chip_env["c"])
    assert j["status"] == "ok", j
    s, live = _live_port(chip_env, port)
    assert name in s["qubits"]
    assert live["feedforward_filter"] is None and live["exponential_filter"] is None
    assert live["delay"] == 62


def test_a_real_live_change_of_the_cleared_filter_is_still_a_collision(chip_env):
    _name, port = _stage_on_reused_port(chip_env)
    s, live = _live_port(chip_env, port)
    live["exponential_filter"] = [[0.3, 300.0]]
    (chip_env["chip"] / "state.json").write_text(json.dumps(s), encoding="utf-8")
    j = _apply(chip_env["c"])
    assert j["status"] == "collision", j
    assert any(p.endswith(".exponential_filter") for p in j["paths"])


def test_one_undo_removes_the_whole_add(chip_env):
    name, port = _stage_on_reused_port(chip_env)
    st = chip_env["store"]
    chip_env["c"].post("/undo")
    con, fem, pid = port
    assert name not in st.merged["qubits"]
    assert st.merged["ports"]["analog_outputs"][con][fem][pid]["exponential_filter"] == [
        [0.1, 200.0], [0.05, 1000.0], [0.02, 5000.0]]
    assert not st.change_log
