"""docs/173 S1: the calibration story -- one card per run, whoever ran it.

The spine is the DatasetStore (a real one over a synthetic qualibrate
folder), journal lines attach by ``#run`` or by time window + target,
authors follow the four-rung ladder (claimed > SM-ran > hook-inferred >
unknown), the gate is anchored on the run's own saved state and cached per
GATES_REV, and write cards come from undo-journal units with per-entry
actors.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager.core import journal, story
from quam_state_manager.core.dataset import DatasetStore

DAY = "2026-09-06"


def _run_folder(root: Path, run_id: int, node: str, hms: str, *, qubits, outcomes=None, params=None,
                start=None, end=None, state=None, figure=True) -> Path:
    d = root / DAY / f"#{run_id}_{node}_{hms.replace(':', '')}"
    d.mkdir(parents=True)
    t = datetime.strptime(f"{DAY} {hms}", "%Y-%m-%d %H:%M:%S")
    meta = {"name": node, "description": "", "run_start": start or t.isoformat(timespec="milliseconds"),
            "run_end": end or t.isoformat(timespec="milliseconds"), "status": "finished"}
    p = {"qubits": list(qubits)}
    p.update(params or {})
    node_json = {"metadata": meta, "data": {"parameters": {"model": p},
                                             "outcomes": outcomes or {q: "successful" for q in qubits}}}
    (d / "node.json").write_text(json.dumps(node_json), encoding="utf-8")
    (d / "data.json").write_text(json.dumps({"fit_results": {q: {"success": True} for q in qubits},
                                             "figures": {"fig": "fig.png"} if figure else {}}), encoding="utf-8")
    if figure:
        (d / "fig.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    if state is not None:
        (d / "quam_state").mkdir()
        (d / "quam_state" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return d


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "data"
    _run_folder(root, 101, "02_resonator_spectroscopy", "09:12:00", qubits=["q1"])
    _run_folder(root, 102, "02_resonator_spectroscopy", "09:30:00", qubits=["q3"],
                params={"num_averages": 100})
    _run_folder(root, 103, "02_resonator_spectroscopy", "09:41:00", qubits=["q3"],
                params={"num_averages": 400}, outcomes={"q3": "failed"})
    _run_folder(root, 104, "05_power_rabi", "14:05:00", qubits=["q4"],
                start=f"{DAY}T14:05:00", end=f"{DAY}T14:09:00", state={"qubits": {"q4": {"f_01": 4.8e9}}})
    ds = DatasetStore(root)
    inst = tmp_path / "inst"
    inst.mkdir()
    return {"root": root, "ds": ds, "inst": inst}


class TestJournalParsing:
    def test_entries_run_tokens_paths_and_because(self, tmp_path):
        journal.append(tmp_path, "c", "staged `qubits.q4.f_01` 4.80 -> 4.81", kind="agent",
                       reason="rabi left-biased", run_id=104, paths=["qubits.q4.f_01"],
                       when=datetime.strptime(f"{DAY} 14:10:00", "%Y-%m-%d %H:%M:%S"))
        journal.append(tmp_path, "c", "ran `05_power_rabi`", kind="hook",
                       when=datetime.strptime(f"{DAY} 14:09:30", "%Y-%m-%d %H:%M:%S"))
        es = story.parse_journal(journal.read(tmp_path, "c", DAY), DAY)
        assert [e["kind"] for e in es] == ["agent", "hook"]
        a = es[0]
        assert a["run_id"] == 104 and a["paths"] == ["qubits.q4.f_01"] and a["because"] == "rabi left-biased"
        assert a["text"].startswith("staged `qubits.q4.f_01`") and "run #" not in a["text"]
        assert a["time"] == "14:10:00" and a["ts"] > 0
        assert es[1]["run_id"] is None and es[1]["text"] == "ran `05_power_rabi`"

    def test_multiline_text_and_no_entries(self):
        assert story.parse_journal("# c — d\n\nnot a bullet\n", DAY) == []
        es = story.parse_journal("- **01:00:00** `human` first line\n  second line\n", DAY)
        assert es[0]["text"] == "first line\nsecond line"


class TestCardsFromTheStore:
    def test_every_run_is_a_card_even_with_an_empty_journal(self, world):
        d = story.build_day(world["inst"], "chip", DAY, ds=world["ds"], with_gates=False)
        runs = [c for c in d["cards"] if c["kind"] == "run"]
        assert [c["run_id"] for c in runs] == [101, 102, 103, 104]
        assert all(c["author"] == "unknown" and c["certainty"] == "none" for c in runs), \
            "no hook, no SM run, no claim: the author is unknown -- never 'qualibrate' by assumption"
        assert runs[0]["family_label"]
        assert runs[0]["figure"] and runs[0]["figure"] == runs[0]["figures"][0], \
            "the store's own figure name (its data.json key), resolvable by get_figure_path"
        assert world["ds"].get_figure_path(101, runs[0]["figure"]) is not None
        assert d["counts"]["runs"] == 4 and d["counts"]["failed"] == 1

    def test_outcome_and_params_diff_vs_previous_same_node(self, world):
        d = story.build_day(world["inst"], "chip", DAY, ds=world["ds"], with_gates=False)
        by = {c["run_id"]: c for c in d["cards"] if c["kind"] == "run"}
        assert by[103]["outcome"] == "failed" and by[102]["outcome"] == "ok"
        assert by[103]["prev_run_id"] == 102
        assert by[103]["params_diff"] == [{"key": "num_averages", "old": 100, "new": 400}]

    def test_digest_groups_by_target_in_time_order(self, world):
        d = story.build_day(world["inst"], "chip", DAY, ds=world["ds"], with_gates=False)
        assert list(d["digest"]) == ["q1", "q3", "q4"]
        assert [x["run_id"] for x in d["digest"]["q3"]] == [102, 103]
        assert d["digest"]["q3"][1]["outcome"] == "failed"


class TestJournalAttachment:
    def test_run_token_wins_and_time_window_catches_the_rest(self, world):
        inst = world["inst"]
        when = lambda hms: datetime.strptime(f"{DAY} {hms}", "%Y-%m-%d %H:%M:%S")
        journal.append(inst, "chip", "set amp", kind="agent", reason="rabi says so", run_id=104, when=when("14:12:00"))
        journal.append(inst, "chip", "ran `05_power_rabi` on q4", kind="hook", when=when("14:09:30"))   # in window, names q4
        journal.append(inst, "chip", "ran `T1` on q9", kind="hook", when=when("14:08:00"))              # in window, wrong target
        journal.append(inst, "chip", "unrelated at noon", kind="human", when=when("12:00:00"))          # no window
        d = story.build_day(inst, "chip", DAY, ds=world["ds"], with_gates=False)
        c = next(x for x in d["cards"] if x.get("run_id") == 104)
        texts = [e["text"] for e in c["journal"]]
        assert "set amp" in texts and "ran `05_power_rabi` on q4" in texts
        assert "ran `T1` on q9" not in texts
        assert c["because"] == "rabi says so"
        assert [e["text"] for e in d["loose"]] == ["ran `T1` on q9", "unrelated at noon"]

    def test_because_falls_back_to_the_agent_line_text(self, world):
        inst = world["inst"]
        journal.append(inst, "chip", "took the amplitude from the fit", kind="agent", reason="x",
                       run_id=101, when=datetime.strptime(f"{DAY} 09:13:00", "%Y-%m-%d %H:%M:%S"))
        d = story.build_day(inst, "chip", DAY, ds=world["ds"], with_gates=False)
        c = next(x for x in d["cards"] if x.get("run_id") == 101)
        assert c["because"] == "x"


class TestTheAuthorLadder:
    def test_sm_ran_it_is_certain(self, world):
        story.record_agent_run(world["inst"], {"run_id": 104, "plan_id": "p1", "backend": "codex", "node": "05_power_rabi"})
        d = story.build_day(world["inst"], "chip", DAY, ds=world["ds"], with_gates=False)
        c = next(x for x in d["cards"] if x.get("run_id") == 104)
        assert c["author"] == "by_codex" and c["certainty"] == "certain" and c["plan_id"] == "p1"

    def test_a_hook_event_in_the_window_infers_the_agent(self, world):
        ts = datetime.strptime(f"{DAY} 14:09:10", "%Y-%m-%d %H:%M:%S").timestamp()
        ev = [{"ts": ts, "hook_event_name": "PostToolUse", "tool_name": "Bash", "backend": "claude",
               "summary": "python calibrations/05_power_rabi.py --qubits q4"}]
        d = story.build_day(world["inst"], "chip", DAY, ds=world["ds"], events=ev, with_gates=False)
        c = next(x for x in d["cards"] if x.get("run_id") == 104)
        assert c["author"] == "by_claude" and c["certainty"] == "inferred"
        # the same event names ANOTHER node: no inference
        ev[0]["summary"] = "python 08_qubit_spectroscopy.py"
        d = story.build_day(world["inst"], "chip", DAY, ds=world["ds"], events=ev, with_gates=False)
        assert next(x for x in d["cards"] if x.get("run_id") == 104)["author"] == "unknown"

    def test_a_human_claim_beats_everything(self, world):
        story.record_agent_run(world["inst"], {"run_id": 104, "plan_id": "p1", "backend": "codex"})
        story.claim_run(world["inst"], "chip", 104, author="human:박OO", note="I ran it")
        d = story.build_day(world["inst"], "chip", DAY, ds=world["ds"], with_gates=False)
        c = next(x for x in d["cards"] if x.get("run_id") == 104)
        assert c["author"] == "human:박OO" and c["certainty"] == "claimed" and c["note"] == "I ran it"


class TestTheGate:
    def test_anchor_is_the_runs_own_state_and_the_cache_is_keyed_by_rev(self, world, monkeypatch):
        calls = []

        def fake(run):
            calls.append(run["run_id"])
            return {"verdict": "pass", "reason": "", "family": "power_rabi", "anchor": "run"}
        d = story.build_day(world["inst"], "chip", DAY, ds=world["ds"], gate_compute=fake)
        c = next(x for x in d["cards"] if x.get("run_id") == 104)
        assert c["gate"]["verdict"] == "pass" and c["gate"]["rev"] == story.GATES_REV
        story.build_day(world["inst"], "chip", DAY, ds=world["ds"], gate_compute=fake)
        assert calls.count(104) == 1, "cached"
        monkeypatch.setattr(story, "GATES_REV", "next")
        story.build_day(world["inst"], "chip", DAY, ds=world["ds"], gate_compute=fake)
        assert calls.count(104) == 2, "a new gates revision recomputes"

    def test_no_state_in_the_run_folder_is_unknown_not_a_verdict(self, world):
        run = world["ds"].get_run(101)
        g = story._compute_gate(run)
        assert g["verdict"] == "unknown" and "anchor" in g["reason"]

    def test_real_gate_on_the_runs_own_state(self, world):
        run = world["ds"].get_run(104)
        assert story.run_anchor_state(run) == {"qubits": {"q4": {"f_01": 4.8e9}}}
        g = story._compute_gate(run)
        assert g["verdict"] in ("pass", "suspect", "fail") and g["anchor"] == "run"
        assert "q4" in g["per_target"]

    def test_walk_follows_a_pointer(self):
        doc = {"a": {"b": "#/c/d"}, "c": {"d": 7}}
        assert story._walk(doc, "a.b") == 7
        with pytest.raises(KeyError):
            story._walk(doc, "a.zz")


class TestWriteCards:
    def test_units_of_the_day_carry_per_entry_actors(self, world, tmp_path):
        from quam_state_manager.core import undo_journal
        from quam_state_manager.core.loader import ChangeEntry
        live = tmp_path / "live"
        live.mkdir()
        e1 = ChangeEntry("qubits.q4.f_01", 4.80e9, 4.81e9, "state")
        e1.actor = "by_claude"
        e2 = ChangeEntry("qubits.q4.xy.operations.x180.amplitude", 0.31, 0.29, "state")
        e2.actor = "human:이OO"
        ts = datetime.strptime(f"{DAY} 14:22:00", "%Y-%m-%d %H:%M:%S").timestamp()
        u = undo_journal.make_unit([e1], ts=ts, meta={"plan_id": "p1"})
        u2 = undo_journal.make_unit([e2], ts=ts + 60)
        undo_journal.append_units(undo_journal.sidecar_path(world["inst"], live), [u, u2])
        d = story.build_day(world["inst"], "chip", DAY, ds=world["ds"], active_path=str(live), with_gates=False)
        writes = [c for c in d["cards"] if c["kind"] == "write"]
        assert [w["author"] for w in writes] == ["by_claude", "human:이OO"]
        assert writes[0]["plan_id"] == "p1" and writes[0]["entries"][0]["actor"] == "by_claude"
        assert d["counts"]["writes"] == 2
        assert d["counts"]["biggest_write"]["path"] == "qubits.q4.f_01"
        # cards interleave by time: the 14:22 write sits after the 14:05 run
        order = [(c["kind"], c.get("run_id") or c.get("id")) for c in d["cards"]]
        assert order.index(("run", 104)) < order.index(("write", writes[0]["id"]))
