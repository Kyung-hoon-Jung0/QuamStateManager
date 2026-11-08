"""Remove a qubit or a pair (core/diagram_remove.py) and the env check every
add / remove passes before it is staged (web/diagram_remove_routes.py), plus
the generated-config read-back (core/diagram_check.py).

The removal is judged on the synthetic chip through the real routes: every
dependent named, one change group, every existing value byte-identical after
one Ctrl+Z, nothing written live. The env check is replaced by a fake that
records what it was asked to load, so the pins can say the RESULT (not the
current chip) is what gets loaded, and that a failing load refuses."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_synthetic_chip import make_chip, write_chip  # noqa: E402
from quam_state_manager.core import diagram_check as dc  # noqa: E402
from quam_state_manager.core import diagram_remove as drm  # noqa: E402


def _merged(n=100):
    s, w = make_chip(n)
    m = dict(s)
    m.update(w)
    return s, w, m


# ------------------------------------------------------------------ the plan

class TestPlan:
    def test_a_qubit_takes_its_pairs_and_their_member_ops_with_it(self):
        _, _, m = _merged()
        pairs = [p for p in m["qubit_pairs"] if "q2" in p.split("-")]
        plan = drm.plan_remove_qubit(m, "q2")
        assert not plan.get("error"), plan.get("error")
        assert sorted(plan["summary"]["pairs_with"]) == sorted(pairs)
        assert "qubits.q2" in plan["deletes"]
        for p in pairs:
            assert f"qubit_pairs.{p}" in plan["deletes"]
        lists = dict(plan["sets"])
        assert "q2" not in lists["active_qubit_names"]
        assert not set(pairs) & set(lists["active_qubit_pair_names"])

    def test_nothing_dangles_after_the_removal(self):
        s, w, m = _merged()
        plan = drm.plan_remove_qubit(m, "q2")
        s2, w2 = drm.apply_to_docs(s, w, plan)
        after = dict(s2)
        after.update(w2)
        refs = []
        from quam_state_manager.core.diagram_add import _abs_target, _pointers
        _pointers(after, (), refs)
        missing = []
        for path, ptr in refs:
            node = after
            for k in _abs_target(ptr, path):
                node = node.get(k) if isinstance(node, dict) else None
            if node is None and "extras" not in path:
                before = m
                for k in _abs_target(ptr, path):
                    before = before.get(k) if isinstance(before, dict) else None
                if before is not None:
                    missing.append((".".join(path), ptr))
        assert missing == []

    def test_a_pointer_the_removal_would_break_refuses_by_name(self):
        _, _, m = _merged()
        m = copy.deepcopy(m)
        m["qubits"]["q5"]["note_ref"] = "#/qubits/q2/xy"
        plan = drm.plan_remove_qubit(m, "q2")
        assert plan["blockers"] and plan["blockers"][0]["path"] == "qubits.q5.note_ref"
        assert "qubits.q5.note_ref" in plan["error"]

    def test_a_note_in_extras_is_never_rewritten(self):
        _, _, m = _merged()
        m = copy.deepcopy(m)
        m["qubits"]["q5"]["extras"] = {"why": "q2 drifts", "ref": "#/qubits/q2/xy"}
        plan = drm.plan_remove_qubit(m, "q2")
        assert not plan["blockers"]
        assert not any(p.startswith("qubits.q5.extras") for p in plan["deletes"])

    def test_a_pair_takes_only_itself(self):
        _, _, m = _merged()
        pid = next(iter(m["qubit_pairs"]))
        plan = drm.plan_remove_pair(m, pid)
        assert not plan.get("error")
        assert plan["summary"]["qubits"] == []
        assert not any(p.startswith("qubits.") and p.count(".") < 3 for p in plan["deletes"])

    def test_the_last_qubit_is_not_removable(self):
        m = {"qubits": {"q1": {}}, "qubit_pairs": {}}
        assert "only qubit" in drm.plan_remove_qubit(m, "q1")["error"]

    def test_ports_the_removal_frees_are_listed_and_kept(self):
        s, w, m = _merged()
        plan = drm.plan_remove_qubit(m, "q2")
        used = set()
        from quam_state_manager.core.diagram_add import _walk_strings
        refs = []
        _walk_strings(m["wiring"]["qubits"]["q2"], refs)
        own = {(r[1], r[2], r[3]) for r in refs if r[0] != "list"}
        freed = {(f["con"], f["fem"], f["port"]) for f in plan["summary"]["freed"]}
        assert freed <= own
        assert not any(p.startswith("ports.") for p in plan["deletes"])
        del used


# -------------------------------------------------------------- the readback

def _cfg(fsp_new=-2, fsp_tpl=0):
    wf = {"type": "constant", "sample": 0.1}
    return {
        "controllers": {"con1": {"fems": {
            "1": {"analog_outputs": {"2": {"full_scale_power_dbm": fsp_tpl, "upconverter_frequency": 5e9},
                                     "7": {"full_scale_power_dbm": fsp_new, "upconverter_frequency": 5e9}}}}}},
        "elements": {
            "qT.xy": {"MWInput": {"port": ["con1", 1, 2], "upconverter": 1},
                      "intermediate_frequency": -1e8, "operations": {"x180": "pT"}},
            "qN.xy": {"MWInput": {"port": ["con1", 1, 7], "upconverter": 1},
                      "intermediate_frequency": -1e8, "operations": {"x180": "pN"}},
        },
        "pulses": {"pT": {"waveforms": {"I": "w", "Q": "z"}}, "pN": {"waveforms": {"I": "w", "Q": "z"}}},
        "waveforms": {"w": wf, "z": {"type": "constant", "sample": 0.0}},
    }


class TestReadback:
    def test_power_is_full_scale_plus_20log_amplitude(self):
        row = dc.readback(_cfg(), ["qN.xy"])[0]
        assert row["port"] == ["con1", 1, 7] and row["rf"] == pytest.approx(4.9e9)
        assert row["ops"][0]["dbm"] == pytest.approx(-2 + 20 * -1)

    def test_a_template_on_a_different_full_scale_port_is_named_with_the_factor(self):
        row = dc.readback(_cfg(), ["qN.xy"], template="qT", new="qN")[0]
        assert row["shift_db"] == pytest.approx(-2.0)
        assert row["shift_factor"] == pytest.approx(10 ** (2 / 20))

    def test_the_same_full_scale_raises_nothing(self):
        row = dc.readback(_cfg(0, 0), ["qN.xy"], template="qT", new="qN")[0]
        assert "shift_db" not in row

    def test_a_port_other_than_the_chosen_one_is_a_problem(self):
        row = dc.readback(_cfg(), ["qN.xy"], expect={"qN.xy": {"port": ["con1", 1, 3], "rf": 4.9e9}})[0]
        assert row["problems"] and "chosen" in row["problems"][0]


# ------------------------------------------------------------------ routes

@pytest.fixture
def env(tmp_path, monkeypatch):
    from quam_state_manager.web import diagram_remove_routes as DR
    from quam_state_manager.web.app import create_app
    chip = write_chip(tmp_path / "chip", 100)
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    ctx = next(iter(app.config["contexts"].values()))
    seen = {"calls": [], "ok": True}

    def fake(py, state, wiring):
        seen["calls"].append((state, wiring))
        if seen["ok"]:
            return {"ok": True, "error": None, "seconds": 0.1,
                    "config": {"elements": {f"{q}.xy": {} for q in state["qubits"]}}}
        return {"ok": False, "error": "TypeError: the env's own words", "config": None, "seconds": 0.1}

    monkeypatch.setattr(DR, "env_python", lambda: "python")
    monkeypatch.setattr(DR, "run_env_check", fake)
    DR._VERDICTS.clear()
    return {"app": app, "c": c, "store": ctx["store"], "chip": chip, "seen": seen}


def _token(c):
    from quam_state_manager.web import routes as R
    with c.application.test_request_context():
        return R._active_chip_token()


def _rm(c, kind="qubit", ent="q2", **kw):
    return dict({"op": "remove", "kind": kind, "id": ent, "expect_chip": _token(c)}, **kw)


class TestRoutes:
    def test_the_panel_names_every_dependent_and_the_freed_ports(self, env):
        html = env["c"].get("/diagram-remove/panel?kind=qubit&id=q2").get_data(as_text=True)
        assert "Pairs removed with it" in html and "q1-q2" in html
        assert "data-cm-rm-freed" in html and 'id="cm-rm-rack"' in html

    def test_the_check_loads_the_result_and_reads_it_back(self, env):
        r = env["c"].post("/diagram-add/check", data=_rm(env["c"]))
        assert r.status_code == 200 and "data-cm-check-ok" in r.get_data(as_text=True)
        state, _ = env["seen"]["calls"][-1]
        assert "q2" not in state["qubits"] and "q3" in state["qubits"]

    def test_a_failing_load_refuses_with_the_envs_words_and_stages_nothing(self, env):
        env["seen"]["ok"] = False
        html = env["c"].post("/diagram-add/check", data=_rm(env["c"])).get_data(as_text=True)
        assert "data-cm-check-fail" in html and "the env&#39;s own words" in html
        r = env["c"].post("/diagram-add/stage", data=_rm(env["c"], dry="0"))
        assert r.status_code == 409 and not env["store"].change_log

    def test_an_explicit_override_stages_after_a_failing_check(self, env):
        env["seen"]["ok"] = False
        r = env["c"].post("/diagram-add/stage", data=_rm(env["c"], dry="0", override="1"))
        assert r.status_code == 200 and "q2" not in env["store"].merged["qubits"]

    def test_a_removal_is_one_undo_group_and_leaves_live_untouched(self, env):
        before = copy.deepcopy(env["store"].merged)
        live = (env["chip"] / "state.json").read_bytes()
        r = env["c"].post("/diagram-add/stage", data=_rm(env["c"], dry="0"))
        assert r.status_code == 200, r.get_data(as_text=True)
        m = env["store"].merged
        assert "q2" not in m["qubits"] and "q2" not in m["active_qubit_names"]
        assert len({e.group_id for e in env["store"].change_log}) == 1
        assert (env["chip"] / "state.json").read_bytes() == live
        env["c"].post("/undo")
        assert env["store"].merged == before

    def test_a_pair_removal_round_trips(self, env):
        pid = next(iter(env["store"].merged["qubit_pairs"]))
        before = copy.deepcopy(env["store"].merged)
        r = env["c"].post("/diagram-add/stage", data=_rm(env["c"], "pair", pid, dry="0"))
        assert r.status_code == 200 and pid not in env["store"].merged["qubit_pairs"]
        env["c"].post("/undo")
        assert env["store"].merged == before

    def test_an_add_stage_also_needs_the_check(self, env):
        from quam_state_manager.core import diagram_add as da
        env["seen"]["ok"] = False
        d = da.qubit_defaults(env["store"].merged)
        j = env["c"].get(f"/api/diagram-add/ports?feedline={d['feedline']}&drive_rf={d['drive_rf']}").get_json()
        dr0, fl0 = j["drive"][0], j["flux"][0]
        form = {"flux": f"{fl0['con']}/{fl0['fem']}/{fl0['port']}", "readout_rf": str(d["readout_rf"]),"kind": "qubit", "name": d["name"], "feedline": d["feedline"],
                "drive_rf": str(d["drive_rf"]), "drive": f"{dr0['con']}/{dr0['fem']}/{dr0['port']}",
                "expect_chip": _token(env["c"]), "dry": "0"}
        r = env["c"].post("/diagram-add/stage", data=form)
        assert r.status_code == 409, r.get_data(as_text=True)
        assert d["name"] not in env["store"].merged["qubits"]
        state, _ = env["seen"]["calls"][-1]
        assert d["name"] in state["qubits"], "the check loads the chip WITH the new qubit"

    def test_the_inspector_offers_remove(self, env):
        html = env["c"].get("/qubit/q2", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "data-cm-rm-open" in html and "/diagram-remove/panel?kind=qubit&id=q2" in html


# ------------------------------------------------- review round (FB-10/11/12)

class TestPreviewFlags:
    def test_every_list_shortened_by_name_is_listed_and_a_pair_like_one_is_flagged(self):
        _, _, m = _merged()
        m = copy.deepcopy(m)
        m["qubits"]["q5"]["partners"] = ["q2", "q7"]      # looks like a membership
        plan = drm.plan_remove_qubit(m, "q2")
        by = {c["path"]: c for c in plan["summary"]["silent"]}
        assert by["active_qubit_names"]["check"] is False
        assert by["qubits.q5.partners"]["check"] is True
        assert "pair membership" in by["qubits.q5.partners"]["why"]

    def test_a_key_named_like_the_id_among_other_keys_is_flagged(self):
        _, _, m = _merged()
        m = copy.deepcopy(m)
        m["qubits"]["q5"]["table"] = {"q2": 1, "gain": 2}
        plan = drm.plan_remove_qubit(m, "q2")
        by = {c["path"]: c for c in plan["summary"]["silent"]}
        assert by["qubits.q5.table.q2"]["what"] == "deleted key" and by["qubits.q5.table.q2"]["check"]

    def test_an_extras_pointer_into_the_removed_qubit_is_listed_as_dangling(self):
        _, _, m = _merged()
        m = copy.deepcopy(m)
        m["qubits"]["q5"]["extras"] = {"ref": "#/qubits/q2/xy"}
        plan = drm.plan_remove_qubit(m, "q2")
        assert plan["summary"]["extras_dangle"] == [
            {"path": "qubits.q5.extras.ref", "pointer": "#/qubits/q2/xy"}]
        assert not plan["blockers"]

    def test_also_paths_join_the_deletes(self):
        _, _, m = _merged()
        q = "q5"
        op = next(iter(m["qubits"][q]["xy"]["operations"]))
        path = f"qubits.{q}.xy.operations.{op}"
        plan = drm.plan_remove_qubit(m, "q2", [path, "qubits.nope"])
        assert path in plan["deletes"] and plan["summary"]["also"] == [path]


class TestPeak:
    def test_constant_i_with_arbitrary_q_is_a_per_sample_hypot(self):
        cfg = {"pulses": {"p": {"waveforms": {"I": "c", "Q": "a"}}},
               "waveforms": {"c": {"type": "constant", "sample": 0.3},
                             "a": {"type": "arbitrary", "samples": [0.0, 0.4, -0.1]}}}
        assert dc.op_peak(cfg, "p") == pytest.approx(0.5)
        cfg["pulses"]["p"]["waveforms"] = {"I": "a", "Q": "c"}
        assert dc.op_peak(cfg, "p") == pytest.approx(0.5)

    def test_a_shift_on_equal_full_scale_ports_is_not_blamed_on_full_scale(self):
        cfg = _cfg(0, 0)
        cfg["waveforms"]["w2"] = {"type": "constant", "sample": 0.05}
        cfg["pulses"]["pN"] = {"waveforms": {"I": "w2", "Q": "z"}}
        row = dc.readback(cfg, ["qN.xy"], template="qT", new="qN")[0]
        assert row["shift_db"] == pytest.approx(20 * __import__("math").log10(0.5))
        assert row["shift_fsp"] is False
        assert dc.readback(_cfg(), ["qN.xy"], template="qT", new="qN")[0]["shift_fsp"] is True


def _saved_bytes(store, folder):
    from quam_state_manager.core.saver import Saver
    folder.mkdir(parents=True, exist_ok=True)
    Saver(store, backup_retention=0).save(folder)
    return (folder / "state.json").read_bytes(), (folder / "wiring.json").read_bytes()


class TestReviewRoutes:
    @pytest.mark.parametrize("kind", ["qubit", "pair"])
    def test_one_undo_gives_back_the_same_bytes(self, env, tmp_path, kind):
        ent = "q2" if kind == "qubit" else next(iter(env["store"].merged["qubit_pairs"]))
        before = _saved_bytes(env["store"], tmp_path / "a")
        r = env["c"].post("/diagram-add/stage", data=_rm(env["c"], kind, ent, dry="0"))
        assert r.status_code == 200, r.get_data(as_text=True)
        # (no save in between: a save closes the change log)
        assert ent not in env["store"].merged["qubits" if kind == "qubit" else "qubit_pairs"]
        env["c"].post("/undo")
        assert _saved_bytes(env["store"], tmp_path / "b") == before

    def test_the_lab_check_sees_every_delete_and_its_refusal_offers_delete_together(
            self, env, monkeypatch):
        from quam_state_manager.web import routes as R
        asked = {}

        def refuse(store, writes, **kw):
            asked["writes"] = list(writes)
            if any(p.startswith("qubit_pairs.") and v is R._LAB_DELETE for p, v in writes) \
                    and not any(p == "qubits.q5.extra_op" for p, _ in writes):
                return ("your lab's code refused", [0])
            return None
        monkeypatch.setattr(R, "_lab_write_refusal", refuse)
        monkeypatch.setattr(R, "_lab_delete_also", lambda s, d, i=None: ["qubits.q5.xy"])
        plan = drm.plan_remove_qubit(env["store"].merged, "q2")
        r = env["c"].post("/diagram-add/stage", data=_rm(env["c"], dry="0"))
        assert r.status_code == 400
        html = r.get_data(as_text=True)
        assert "data-cm-rm-together" in html and "qubits.q5.xy" in html
        dels = {p for p, v in asked["writes"] if v is R._LAB_DELETE}
        assert dels == set(plan["deletes"])
        assert not env["store"].change_log

    def test_the_final_button_repeats_the_auto_sync_note(self, env):
        html = env["c"].post("/diagram-add/check", data=_rm(env["c"])).get_data(as_text=True)
        assert "data-cm-final-autosync" in html and "goes to the tray" in html

    def test_a_removed_pairs_elements_left_in_the_config_are_named(self):
        from quam_state_manager.web import diagram_remove_routes as DR
        cfg = {"elements": {"q1.xy": {}, "coupler_q1-q2": {}, "q1-q2.cz": {}}}
        plan = {"summary": {"op": "remove", "qubits": [], "pairs": ["q1-q2"]}}
        assert DR._left(cfg, plan) == ["coupler_q1-q2", "q1-q2.cz"]

    def test_the_verdict_key_names_the_env(self, env, monkeypatch):
        from quam_state_manager.web import diagram_remove_routes as DR
        plan = drm.plan_remove_qubit(env["store"].merged, "q2")
        with env["app"].test_request_context():
            k1 = DR.plan_key(env["store"], plan)
            monkeypatch.setattr(DR, "env_python", lambda: "other")
            assert DR.plan_key(env["store"], plan) != k1


class TestWarmCheck:
    def test_the_warm_worker_answers_and_cold_runs_only_without_it(self, monkeypatch):
        from quam_state_manager.core import config_generator, lab_waveform
        from quam_state_manager.web import diagram_remove_routes as DR
        cold = []
        monkeypatch.setattr(config_generator, "run_config_preview",
                            lambda py, f, strict: cold.append(f) or
                            {"ok": True, "result": {"config": {"elements": {"c": {}}}}})
        seen = {}

        def warm(py, folder):
            seen["state"] = (Path(folder) / "state.json").read_text(encoding="utf-8")
            return {"ok": True, "error": None, "config": {"elements": {"w": {}}}}
        monkeypatch.setattr(lab_waveform, "warm_config", warm)
        v = DR.run_env_check("py", {"qubits": {"q1": {}}}, {})
        assert v["worker"] == "warm" and v["config"] == {"elements": {"w": {}}} and not cold
        assert '"q1"' in seen["state"]
        monkeypatch.setattr(lab_waveform, "warm_config", lambda py, f: None)
        v = DR.run_env_check("py", {"qubits": {}}, {})
        assert v["worker"] == "cold" and len(cold) == 1

    def test_a_warm_failure_carries_the_envs_words(self, monkeypatch):
        from quam_state_manager.core import lab_waveform
        from quam_state_manager.web import diagram_remove_routes as DR
        monkeypatch.setattr(lab_waveform, "warm_config",
                            lambda py, f: {"ok": False, "error": "KeyError: 'xy'", "config": None})
        v = DR.run_env_check("py", {}, {})
        assert not v["ok"] and v["error"] == "KeyError: 'xy'" and v["worker"] == "warm"
