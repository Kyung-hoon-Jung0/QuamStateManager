"""docs/262 S1: every caller that compares a run's time with another clock
reads the run's INSTANT (``core/run_time``), never the folder digits read in
the server's zone.

The archive here is a -04:00 lab (the lab-H shape, docs/256: every
``created_at`` carries -04:00 and the folder digits are that lab's wall
clock). The server's zone is FORCED to UTC+09:00 by handing each module
under test a ``datetime`` whose machine zone is +09:00 (Windows has no
``time.tzset``), so the old folder-digit reading is 13 h early on any host;
every expectation is built from the true UTC instant. Where the run_instant
rule itself reads a naive clock in the machine zone (realbackend's
``run_start``), the zone is injected through ``run_time.iso_instant``'s own
``local_tz`` parameter.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import agent_runs, journal, run_time, story, timefmt, value_writer
from quam_state_manager.core.autofit import realbackend
from quam_state_manager.core.dataset import DatasetStore

UTC = timezone.utc
Z9 = timezone(timedelta(hours=9))      # the server's (forced) zone
ZM4 = timezone(timedelta(hours=-4))    # the lab's zone
DAY = "2026-10-02"


class Server9(datetime):
    """``datetime`` whose machine zone is a fixed UTC+09:00: a naive value's
    ``timestamp()`` and a ``fromtimestamp``/``now`` without a zone use +09:00
    instead of the host's zone. Patched into the module under test only."""

    def timestamp(self):
        if self.tzinfo is None:
            return datetime.timestamp(self.replace(tzinfo=Z9))
        return datetime.timestamp(self)

    @classmethod
    def fromtimestamp(cls, t, tz=None):
        if tz is None:
            return datetime.fromtimestamp(t, Z9).replace(tzinfo=None)
        return datetime.fromtimestamp(t, tz)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return datetime.now(Z9).replace(tzinfo=None)
        return datetime.now(tz)


def _epoch(iso: str) -> float:
    """The true instant of an AWARE ISO string (host-independent)."""
    return datetime.fromisoformat(iso).timestamp()


def _wall9(epoch: float) -> datetime:
    """The server's (+09:00) naive wall clock of an instant."""
    return datetime.fromtimestamp(epoch, Z9).replace(tzinfo=None)


def _run(root: Path, rid: int, node: str, hhmmss: str, *, qubits=("q4",),
         run_start: str | None = None, run_end: str | None = None) -> float:
    """A -04:00 run: folder ``<DAY>/#rid_node_HHMMSS`` = the lab's wall clock,
    ``created_at`` = that clock at -04:00. Returns the true epoch of created_at."""
    d = root / DAY / f"#{rid}_{node}_{hhmmss}"
    d.mkdir(parents=True)
    created = f"{DAY}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}-04:00"
    meta = {"name": node, "description": "", "status": "finished"}
    if run_start:
        meta["run_start"] = run_start
    if run_end:
        meta["run_end"] = run_end
    node_json = {"created_at": created, "metadata": meta,
                 "data": {"parameters": {"model": {"qubits": list(qubits)}},
                          "outcomes": {q: "successful" for q in qubits}}}
    (d / "node.json").write_text(json.dumps(node_json), encoding="utf-8")
    (d / "data.json").write_text(json.dumps({"fit_results": {q: {"success": True} for q in qubits},
                                             "figures": {}}), encoding="utf-8")
    return _epoch(created)


@pytest.fixture
def lab(tmp_path):
    """#6 res-spec on q1 with an aware run_start (07:00 folder), #7 power_rabi
    on q4 with NO run_start/run_end (08:30 folder) -- the folder-digit fallback."""
    root = tmp_path / "data"
    t6 = _run(root, 6, "02_resonator_spectroscopy", "070000", qubits=("q1",),
              run_start=f"{DAY}T06:58:00.000-04:00", run_end=f"{DAY}T07:00:00.000-04:00")
    t7 = _run(root, 7, "05_power_rabi", "083000", qubits=("q4",))
    ds = DatasetStore(root)
    inst = tmp_path / "inst"
    inst.mkdir()
    return SimpleNamespace(root=root, ds=ds, inst=inst, t6=t6, t7=t7,
                           t6_start=_epoch(f"{DAY}T06:58:00.000-04:00"))


def test_the_fixture_is_the_13_hour_case(lab):
    """Guard for every pin below: the store dated #7 by its -04:00 created_at,
    and the folder digits read in the +09:00 server zone are 13 h early."""
    info = lab.ds.runs[7]
    assert run_time.instant_of(info) == (int(lab.t7 * 1_000_000), "offset")
    digits = Server9.strptime(f"{info.date} {info.time}", "%Y-%m-%d %H:%M:%S").timestamp()
    assert lab.t7 - digits == 13 * 3600


# ------------------------------------------------------- 1. core/story.py

class TestStory:
    def test_the_day_reads_run_instants(self, lab, tmp_path, monkeypatch):
        """build_day: the attach window, the hook match and the card sort all
        use the run's instant; run_start (aware) still decides the START."""
        from quam_state_manager.core import undo_journal
        from quam_state_manager.core.loader import ChangeEntry
        monkeypatch.setattr(story, "datetime", Server9)
        live = tmp_path / "live"
        live.mkdir()
        e = ChangeEntry("qubits.q4.f_01", 4.80e9, 4.81e9, "state")
        e.actor = "human:user-c"
        undo_journal.append_units(undo_journal.sidecar_path(lab.inst, live),
                                  [undo_journal.make_unit([e], ts=lab.t7 - 3600)])
        journal.append(lab.inst, "chip", "q4 rabi looked off, rerunning", kind="human",
                       when=_wall9(lab.t7 + 30))
        ev = [{"ts": lab.t7 + 20, "hook_event_name": "PostToolUse", "tool_name": "Bash",
               "backend": "claude", "summary": "python calibrations/05_power_rabi.py --qubits q4"}]
        d = story.build_day(lab.inst, "chip", DAY, ds=lab.ds, active_path=str(live),
                            events=ev, with_gates=False)
        c6 = next(c for c in d["cards"] if c.get("run_id") == 6)
        c7 = next(c for c in d["cards"] if c.get("run_id") == 7)
        assert c7["ts"] == pytest.approx(lab.t7, abs=1e-6), "no run_start: the card is the run's instant"
        assert c6["ts"] == pytest.approx(lab.t6_start, abs=1e-6), "an aware run_start stays the START"
        assert c7["time"] == "08:30:00", "the SHOWN time stays the acquisition clock (docs/244)"
        assert [e["text"] for e in c7["journal"]] == ["q4 rabi looked off, rerunning"]
        assert d["loose"] == []
        assert (c7["author"], c7["certainty"]) == ("by_claude", "inferred")
        order = [c.get("run_id") or c["kind"] for c in d["cards"]]
        assert order == [6, "write", 7], order

    def test_start_epoch_rules(self, lab):
        """[derived] the attach window's START: run_start (offset kept; naive =
        machine zone, as before), else the run's instant."""
        row = lab.ds.get_run(7)
        assert story.start_epoch(row, lab.ds) == pytest.approx(lab.t7, abs=1e-6)
        assert story.start_epoch(lab.ds.get_run(6), lab.ds) == pytest.approx(lab.t6_start, abs=1e-6)
        naive = f"{DAY}T08:29:00"
        assert story.start_epoch({"run_start": naive}) == pytest.approx(
            datetime.fromisoformat(naive).timestamp(), abs=1e-6)


# ------------------------------------------------ 2. web/journal_routes.py

class TestJournalClaimLine:
    def test_a_claim_is_filed_at_the_runs_instant_in_the_journal_clock(self, lab, monkeypatch):
        from quam_state_manager.web import journal_routes, routes
        monkeypatch.setattr(journal_routes, "datetime", Server9)
        monkeypatch.setattr(story, "datetime", Server9)
        monkeypatch.setattr(routes, "_dataset_store", lambda: lab.ds)
        # no run_start: the server-local wall clock of the run's INSTANT, not the folder digits
        assert journal_routes._run_when(7) == _wall9(lab.t7) == datetime(2026, 10, 2, 21, 30, 0)
        # an aware run_start: its own instant, in the journal's clock
        assert journal_routes._run_when(6) == _wall9(lab.t6_start)


# ---------------------------------------------------- 3. core/agent_runs.py

class TestAgentRunsAttribute:
    def test_the_fresh_run_is_attributed(self, lab, monkeypatch):
        monkeypatch.setattr(agent_runs, "datetime", Server9)
        rows = story.with_instants(lab.ds.list_runs(), lab.ds)
        assert rows[0].get("instant_us") == int(lab.t7 * 1_000_000) and rows[0].get("instant_q") == "offset"
        adapter = SimpleNamespace(list_runs=lambda: rows)
        got = agent_runs._attribute(adapter, "05_power_rabi", lab.t7 - 60, poll_s=0.0)
        assert got is not None and got["run_id"] == 7
        # a window that opened after the run: never that run
        assert agent_runs._attribute(adapter, "05_power_rabi", lab.t7 + 60, poll_s=0.0) is None

    def test_the_web_adapter_hands_the_engine_instants(self, lab, tmp_path, monkeypatch):
        """The rows ``_attribute`` receives in production come from
        ``agent_api._run_adapter().list_runs`` -- they must carry the instant,
        or the engine falls back to the folder clock in this machine's zone."""
        from quam_state_manager.web import agent_api as aa
        from quam_state_manager.web.app import create_app
        from tests.test_web import _make_state, _make_wiring
        chip = tmp_path / "chip"
        chip.mkdir()
        (chip / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
        (chip / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
        app.test_client().post("/load", data={"folder": str(chip)})
        monkeypatch.setattr(agent_runs, "datetime", Server9)
        monkeypatch.setattr(aa, "_ds", lambda: lab.ds)
        with app.test_request_context():
            adapter = aa._run_adapter()
        rows = adapter.list_runs()
        assert {r["run_id"]: r.get("instant_us") for r in rows} == {
            7: int(lab.t7 * 1_000_000), 6: int(lab.t6 * 1_000_000)}
        got = agent_runs._attribute(adapter, "05_power_rabi", lab.t7 - 60, poll_s=0.0)
        assert got is not None and got["run_id"] == 7


# ------------------------------------------------------ 4. web/agent_api.py

class TestAgentApi:
    def test_newest_run_since_a_hook(self, lab, monkeypatch):
        from quam_state_manager.web import agent_api as aa
        monkeypatch.setattr(aa, "datetime", Server9)
        monkeypatch.setattr(aa, "_ds", lambda: lab.ds)
        assert aa._newest_run_since(lab.t7 - 60) == 7
        assert aa._newest_run_since(lab.t7 + 60) is None

    def test_human_ran_recently(self, lab, monkeypatch):
        from quam_state_manager.web import agent_api as aa
        monkeypatch.setattr(aa, "datetime", Server9)
        monkeypatch.setattr(aa, "_ds", lambda: lab.ds)
        h = aa._human_ran_recently(lab.t7 + 120, {}, [])
        assert h is not None and h["run_id"] == 7
        assert h["ts"] == pytest.approx(lab.t7, abs=1e-6), "ts is the run's instant (read at ~1616)"
        # 31 min after the run: no longer "recently"
        assert aa._human_ran_recently(lab.t7 + 31 * 60, {}, []) is None


# ------------------------------------------------------- 5. web/chat_api.py

class TestChatAway:
    def test_runs_after_the_session_are_told(self, lab, monkeypatch):
        from quam_state_manager.web import agent_api as aa, chat_api
        monkeypatch.setattr(chat_api, "datetime", Server9)
        monkeypatch.setattr(aa, "_ds", lambda: lab.ds)
        since = lab.t7 - 600
        block = chat_api._away_block({"updated": since})
        head = f"[While you were away since {_wall9(since).strftime('%H:%M')}: 1 run(s)]"
        assert block.startswith(head), block
        assert "#7 05_power_rabi q4" in block and "#6" not in block
        assert chat_api._away_block({"updated": lab.t7 + 60}) == ""


# ------------------------------------------- 6. core/autofit/realbackend.py

def _backend(tmp_path, runs):
    adapter = realbackend.RealAdapter(instance_path=str(tmp_path / "inst"), reconcile=lambda: None,
                                      rescan_and_list_runs=lambda: list(runs), step_timeout_s=5.0)
    return realbackend.RealBackend(adapter, {})


class TestRealBackendRunStart:
    @pytest.fixture(autouse=True)
    def _machine_is_the_lab_pc(self, monkeypatch):
        """SM drives the local scheduler: the node runs on THIS machine, so a
        naive run_start is this machine's wall clock -- injected as -04:00."""
        monkeypatch.setattr(realbackend, "_ATTRIBUTION_POLL_S", 0.0)
        monkeypatch.setattr(run_time, "iso_instant", partial(run_time.iso_instant, local_tz=ZM4))

    def test_parse_ts_follows_the_run_instant_rule(self):
        want = datetime(2026, 10, 2, 12, 30, tzinfo=UTC)
        assert realbackend._parse_ts(f"{DAY}T08:30:00.000-04:00") == want   # offset kept
        assert realbackend._parse_ts(f"{DAY}T08:30:00.000") == want         # naive = machine zone
        assert realbackend._parse_ts(None) is None and realbackend._parse_ts("nope") is None

    def test_a_naive_run_start_after_the_window_is_attributed(self, tmp_path):
        folder = tmp_path / "run"
        folder.mkdir()
        (folder / "node.json").write_text("{}", encoding="utf-8")
        run = SimpleNamespace(experiment_name="08_qubit_spectroscopy", folder_path=folder, run_id=42,
                              fit_results={}, outcomes={}, parameters={}, run_start=f"{DAY}T08:30:00.000")
        be = _backend(tmp_path, [run])
        got = be._attribute("08_qubit_spectroscopy", datetime(2026, 10, 2, 12, 29, tzinfo=UTC))
        assert got is not None and got["run_id"] == 42
        assert be._attribute("08_qubit_spectroscopy", datetime(2026, 10, 2, 12, 31, tzinfo=UTC)) is None


# --------------------------------------------------- 7. core/value_writer.py

class TestValueWriterRunEnd:
    def test_an_offset_run_end_is_the_exact_instant(self):
        got = value_writer._parse_iso(f"{DAY}T09:04:00.250-04:00")
        assert got == datetime(2026, 10, 2, 13, 4, 0, 250000, tzinfo=UTC) and got.tzinfo is not None

    @pytest.mark.parametrize("naive", [f"{DAY}T09:04:00.250", f"{DAY} 09:04:00", f"{DAY}T09:04:00"])
    def test_a_naive_run_end_proves_nothing(self, naive):
        """Provenance: a zone-less clock is never claimed, even though the
        run_instant rule would read it as ``assumed_local``."""
        assert run_time.iso_instant(naive)[1] == "assumed_local"
        assert value_writer._parse_iso(naive) is None

    @pytest.mark.parametrize("text", [f"{DAY}T09:04:00-04:00", f"{DAY}T13:04:00Z", f"{DAY}T13:04:00z",
                                      f" {DAY}T09:04:00.5-04:00 ", f"{DAY} 09:04:00-04:00"])
    def test_run_end_reads_as_run_instant_reads_it(self, text):
        """One reading (docs/262): what value_writer proves is what the
        Datasets table dates -- the same ``timefmt.run_instant`` answer."""
        us, q = timefmt.run_instant(timefmt.node_times(None, text))
        assert q == "offset"
        got = value_writer._parse_iso(text)
        assert got is not None and timefmt._utc_us(got) == us

    def test_run_verdict_uses_the_instant(self):
        """A -04:00 run that saved four minutes AFTER the 13:00Z snapshot
        cannot have written the value that snapshot holds."""
        snap_at = value_writer._parse_ts("20261002_130000")
        ns = {"end": f"{DAY}T09:04:00-04:00", "name": "05_power_rabi"}
        assert value_writer.run_verdict(ns, None, "qubits.q4.no_such_leaf", 1.0, snap_at) == "no"
        ns["end"] = f"{DAY}T08:59:00-04:00"           # saved before it: no time objection
        assert value_writer.run_verdict(ns, None, "qubits.q4.no_such_leaf", 1.0, snap_at) == "unknown"
        ns["end"] = f"{DAY}T09:04:00"                 # zone-less: no time objection either
        assert value_writer.run_verdict(ns, None, "qubits.q4.no_such_leaf", 1.0, snap_at) == "unknown"
