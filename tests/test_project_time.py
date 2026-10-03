"""docs/263 -- the project's time zone, and what SM does when a run's clock
disagrees.

User decisions (binding, 2026-10-03):

* the zone is picked on the project landing ABOVE the env picker (searchable
  IANA, current offset, DST-aware), saved PER PROJECT, only SHOWN on later
  launches; a new project defaults to the last project's zone;
* on a pick SM compares the OS zone with the chosen one (a popup when they
  differ) and shows "now HH:MM -- does it match your watch?" with NTP status;
* ONE setting: the Settings zone (SnapTime/localStorage) merged into it;
* a different zone alone is NOT an error (display in the viewer's zone, the
  recorded time in the tooltip, one quiet note);
* a witness skew >= 30 min is asked ONCE per project (experiment PC wrong /
  this PC wrong / ignore), stored with the measured skew, asked again only
  when the skew changes; a correction only after confirmation, labelled
  "corrected", the original kept;
* under 30 min: never asked, recorded, ONE Diagnostics info line;
* witnesses only from runs SM saw ARRIVE, or folders written IN PLACE
  (docs/256 review note).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quam_state_manager.core import project_time as pt
from quam_state_manager.core import run_arrivals, run_watch, timefmt
from quam_state_manager.core import qualibrate_config as qc
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc


def _us(d: datetime) -> int:
    return int(round(d.timestamp() * 1_000_000))


def _iso(d: datetime, off_h: float) -> str:
    """*d* (aware) written in a fixed offset, the way node.json does."""
    tz = timezone(timedelta(hours=off_h))
    return d.astimezone(tz).isoformat(timespec="seconds")


def _node(created: datetime, off_h: float = 9.0) -> dict:
    s = _iso(created, off_h)
    return {"created_at": s, "metadata": {"run_start": s, "run_end": s, "status": "finished"}}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _chip(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {name: {"id": name}}, "qubit_pairs": {},
         "active_qubit_names": [name]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"network": {"host": "1.1.1.1"}}),
                                        encoding="utf-8")
    return folder


def _live_witnesses(skews_s, start=None, key="r"):
    """Live witnesses whose run clock is *skew* ahead of SM's sight."""
    start = start or datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    out = []
    for i, sk in enumerate(skews_s):
        seen = start + timedelta(minutes=10 * i)
        node = _node(seen + timedelta(seconds=sk))
        w = pt.make_witness(node, f"{key}{i}", src="live",
                            first_seen_utc_us=_us(seen), gap_s=0.5)
        assert w is not None
        out.append(w)
    return out


@pytest.fixture
def inst(tmp_path):
    return tmp_path / "_inst"


@pytest.fixture
def lab(tmp_path, monkeypatch, any_project_env_chosen):
    cfg = tmp_path / ".qualibrate"
    a = _chip(tmp_path / "chips" / "a", "qA1")
    b = _chip(tmp_path / "chips" / "b", "qB1")
    data_a = tmp_path / "data" / "a"
    data_a.mkdir(parents=True)
    _write(cfg / "config.toml", f'''
[qualibrate]
project = "alpha"
version = 5

[quam]
state_path = "{a.as_posix()}"
version = 3
''')
    _write(cfg / "projects" / "alpha" / "config.toml",
           f'[qualibrate.storage]\nlocation = "{data_a.as_posix()}"\n'
           f'[quam]\nstate_path = "{a.as_posix()}"\n')
    _write(cfg / "projects" / "beta" / "config.toml", f'[quam]\nstate_path = "{b.as_posix()}"\n')
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    qc._state_index_cache.clear()
    monkeypatch.setattr(routes, "_warm_state_schema_async", lambda *a, **k: None)
    inst = tmp_path / "_inst"
    app = create_app(testing=True, instance_path=str(inst))
    return {"app": app, "c": app.test_client(), "inst": inst, "cfg": cfg,
            "data_a": data_a, "tmp": tmp_path}


# ================================================================ the zone
class TestTheZoneSetting:

    def test_a_pick_is_saved_per_project_and_shown_later(self, inst):
        assert pt.view(inst, "alpha")["state"] == "none"
        pt.set_zone(inst, "alpha", "America/Los_Angeles")
        v = pt.view(inst, "alpha")
        assert (v["zone"], v["state"]) == ("America/Los_Angeles", "picked")
        assert v["offset"] in ("-07:00", "-08:00")          # DST-aware, either season
        # stored beside the env memory, never in ~/.qualibrate
        assert (Path(inst) / "project_time.json").is_file()

    def test_a_new_project_defaults_to_the_last_projects_zone(self, inst):
        pt.set_zone(inst, "alpha", "Asia/Seoul")
        v = pt.view(inst, "beta")
        assert (v["zone"], v["state"], v["from_project"]) == ("Asia/Seoul", "suggested", "alpha")
        # opening beta keeps the default as ITS zone ...
        pt.ensure_default(inst, "beta")
        assert pt.view(inst, "beta")["state"] == "default"
        # ... so a later pick for alpha never moves it
        pt.set_zone(inst, "alpha", "Europe/London")
        assert pt.view(inst, "beta")["zone"] == "Asia/Seoul"
        # and a project never opened now defaults to the newest pick
        assert pt.view(inst, "gamma")["zone"] == "Europe/London"

    def test_ensure_default_never_overrides_a_pick(self, inst):
        pt.set_zone(inst, "alpha", "Asia/Seoul")
        pt.set_zone(inst, "beta", "America/New_York")
        assert pt.ensure_default(inst, "beta") is None
        assert pt.view(inst, "beta")["zone"] == "America/New_York"

    def test_nothing_set_anywhere_means_no_display_zone(self, inst):
        assert pt.ensure_default(inst, "alpha") is None
        assert pt.display_zone(inst, "alpha")["zone"] is None

    def test_a_zone_must_be_a_zone(self, inst):
        with pytest.raises(ValueError):
            pt.set_zone(inst, "alpha", "Not/AZone")
        with pytest.raises(ValueError):
            pt.set_zone(inst, "alpha", "<script>")
        assert pt.valid_zone("UTC") and pt.valid_zone("America/Argentina/Buenos_Aires")

    def test_the_pc_check_answer_is_kept_with_the_pick(self, inst):
        pt.set_zone(inst, "alpha", "Asia/Seoul",
                    os_check={"answer": "view", "os_offset": "-07:00", "os_iana": None})
        oc = pt.view(inst, "alpha")["os_check"]
        assert oc["answer"] == "view" and oc["os_offset"] == "-07:00" and oc["zone"] == "Asia/Seoul"
        with pytest.raises(ValueError):
            pt.set_zone(inst, "alpha", "Asia/Seoul", os_check={"answer": "maybe"})

    def test_the_watch_answer_is_kept(self, inst):
        pt.record_watch(inst, "alpha", "differs", shown="14:05", zone="Asia/Seoul", ntp_synced=False)
        w = pt.view(inst, "alpha")["watch"]
        assert (w["answer"], w["shown"], w["ntp_synced"]) == ("differs", "14:05", False)
        with pytest.raises(ValueError):
            pt.record_watch(inst, "alpha", "dunno")

    def test_a_corrupt_file_is_no_setting_never_an_error(self, inst):
        Path(inst).mkdir(parents=True, exist_ok=True)
        (Path(inst) / pt.FILENAME).write_text("{not json", encoding="utf-8")
        assert pt.view(inst, "alpha")["state"] == "none"
        pt.set_zone(inst, "alpha", "UTC")
        assert pt.view(inst, "alpha")["zone"] == "UTC"

    def test_offsets_and_spans_read_as_english_digits(self):
        assert pt.offset_text("+09:00") == "UTC+9"
        assert pt.offset_text("-03:30") == "UTC-3:30"
        assert pt.offset_text("+00:00") == "UTC"
        assert pt.span_text(3600) == "1 h 00 min"
        assert pt.span_text(-95) == "1 min 35 s"
        assert pt.zone_offset("America/New_York", datetime(2026, 1, 15, tzinfo=UTC)) == "-05:00"
        assert pt.zone_offset("America/New_York", datetime(2026, 7, 15, tzinfo=UTC)) == "-04:00"


# ============================================================ clock status
class TestClockStatus:

    def test_without_clock_health_the_stub_says_unknown(self, monkeypatch):
        monkeypatch.setitem(__import__("sys").modules, "quam_state_manager.core.clock_health", None)
        pt._CLOCK_MEMO.clear()
        st = pt.clock_status(force=True)
        assert st["source"] == "stub" and st["ntp"]["synced"] is None
        assert st["os_zone"]["utc_offset"] and st["now_utc"].endswith("Z")
        assert pt.ntp_text(st).startswith("Time sync: unknown")

    def test_the_agreed_interface_is_read_and_normalized(self, monkeypatch):
        import sys
        import types
        fake = types.ModuleType("quam_state_manager.core.clock_health")
        fake.status = lambda: {"ntp": {"synced": True, "source": "time.windows.com",
                                       "last_sync_utc": "2026-10-03T00:00:00Z",
                                       "offset_s": 0.012, "detail": "ok"},
                               "os_zone": {"iana": "America/Los_Angeles", "utc_offset": "-07:00",
                                           "windows_name": "Pacific Standard Time"}}
        monkeypatch.setitem(sys.modules, "quam_state_manager.core.clock_health", fake)
        pt._CLOCK_MEMO.clear()
        st = pt.clock_status(force=True)
        assert st["source"] == "clock_health"
        assert st["os_zone"] == {"iana": "America/Los_Angeles", "utc_offset": "-07:00",
                                 "windows_name": "Pacific Standard Time"}
        assert pt.ntp_text(st) == "Time sync: on (time.windows.com), off by 0 s"

    def test_a_failing_probe_is_unknown_never_an_error(self, monkeypatch):
        import sys
        import types
        fake = types.ModuleType("quam_state_manager.core.clock_health")

        def boom():
            raise RuntimeError("w32tm hung")
        fake.status = boom
        monkeypatch.setitem(sys.modules, "quam_state_manager.core.clock_health", fake)
        pt._CLOCK_MEMO.clear()
        st = pt.clock_status(force=True)
        assert st["source"] == "stub" and "RuntimeError" in st["ntp"]["detail"]


# ================================================================ routes
class TestLandingAndRoutes:

    def test_the_zone_picker_sits_above_the_env_picker(self, lab):
        pt.set_zone(lab["inst"], "alpha", "Asia/Seoul")
        html = lab["c"].get("/landing/projects").get_data(as_text=True)
        assert 'id="landing-tz"' in html and 'id="landing-env-picker"' in html
        assert html.index('id="landing-tz"') < html.index('id="landing-env-picker"')
        views = json.loads(html.split("data-tz-views>")[1].split("</script>")[0])
        assert views["alpha"]["zone"] == "Asia/Seoul" and views["alpha"]["state"] == "picked"
        assert views["beta"]["state"] == "suggested"
        # each card shows its zone (UTC once, not "UTC UTC")
        assert 'data-tz-card="alpha"' in html and "Seoul UTC+9" in html
        pt.set_zone(lab["inst"], "beta", "UTC")
        html = lab["c"].get("/landing/projects").get_data(as_text=True)
        beta = html[html.index('data-tz-card="beta"'):]
        assert beta[:beta.index("</span>")].split(">")[1].split() == ["tz", "UTC"]

    def test_the_landing_never_probes_the_clock_on_render(self, lab, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("clock probed during a page render")
        monkeypatch.setattr(pt, "clock_status", boom)
        assert lab["c"].get("/").status_code == 200
        assert lab["c"].get("/landing/projects").status_code == 200

    def test_a_pick_posts_and_is_refused_when_wrong(self, lab):
        c = lab["c"]
        assert c.post("/project-time/zone", data={"project": "nope", "zone": "UTC"}).status_code == 404
        assert c.post("/project-time/zone", data={"project": "alpha", "zone": "Mars/Base"}).status_code == 400
        assert c.post("/project-time/zone", data={"project": "alpha", "zone": "UTC",
                                                  "os_answer": "huh"}).status_code == 400
        r = c.post("/project-time/zone", data={"project": "alpha", "zone": "Asia/Seoul",
                                               "os_answer": "view", "os_offset": "-07:00"})
        j = r.get_json()
        assert r.status_code == 200 and j["ok"] and j["view"]["zone"] == "Asia/Seoul"
        assert j["view"]["os_check"]["answer"] == "view"
        r = c.post("/project-time/watch", data={"project": "alpha", "answer": "matches",
                                                "shown": "14:05", "ntp_synced": "true"})
        assert r.get_json()["view"]["watch"]["ntp_synced"] is True

    def test_qualibrate_files_are_never_written(self, lab):
        files = sorted(p for p in lab["cfg"].rglob("*") if p.is_file())
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
        c = lab["c"]
        c.post("/project-time/zone", data={"project": "alpha", "zone": "Asia/Seoul"})
        c.post("/project-time/watch", data={"project": "alpha", "answer": "matches"})
        c.post("/qualibrate/open", data={"project": "beta"})
        after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
        assert before == after
        assert sorted(p for p in lab["cfg"].rglob("*") if p.is_file()) == files

    def test_opening_a_new_project_keeps_the_last_zone_and_every_page_renders_in_it(self, lab):
        c = lab["c"]
        c.post("/project-time/zone", data={"project": "alpha", "zone": "America/Los_Angeles"})
        r = c.post("/qualibrate/open", data={"project": "beta"})
        assert r.status_code in (302, 200)
        assert pt.view(lab["inst"], "beta")["state"] == "default"
        html = c.get("/qubits").get_data(as_text=True)
        assert 'data-sm-zone="America/Los_Angeles"' in html
        assert 'data-sm-project="beta"' in html

    def test_the_settings_zone_is_merged_into_the_project_zone(self, lab):
        c = lab["c"]
        c.post("/project-time/zone", data={"project": "alpha", "zone": "Asia/Seoul"})
        c.post("/qualibrate/open", data={"project": "alpha"})
        html = c.get("/qubits").get_data(as_text=True)
        assert 'id="tz-select"' not in html, "two zone settings again"
        settings = html[html.index('id="tz-current"'):]
        assert settings.split("</span>")[0].endswith("Asia/Seoul (UTC+9)")
        assert 'href="/?landing=1"' in settings[:600] and "Change on Projects" in settings[:600]
        js = (ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        assert "hasAttribute('data-sm-zone')" in js

    def test_the_printable_report_uses_the_project_zone(self):
        d = datetime(2026, 9, 30, 8, 55, 12, tzinfo=UTC)
        assert timefmt.local_text(d, zone="America/Los_Angeles") == "2026-09-30 01:55:12 (UTC-7)"
        assert timefmt.local_text(d, zone="Asia/Seoul") == "2026-09-30 17:55:12 (UTC+9)"

    def test_the_clock_route_answers_with_the_sync_line(self, lab):
        j = lab["c"].get("/project-time/clock").get_json()
        assert "ntp_text" in j and "os_zone" in j and j["now_utc"].endswith("Z")


# ======================================================= the run clock
class TestWitnesses:

    def test_a_different_zone_alone_is_no_skew(self):
        saved = datetime(2026, 9, 30, 8, 55, 12, tzinfo=UTC)
        node = _node(saved, off_h=-7)                  # "01:55:12-07:00"
        w = pt.make_witness(node, "k", src="live", first_seen_utc_us=_us(saved + timedelta(seconds=1)),
                            gap_s=0.5)
        assert abs(w["skew_s"]) <= 1 and w["node_off"] == "-07:00"
        assert pt.summarize([w])["class"] == "small"

    def test_only_a_bounded_first_sight_is_a_witness(self):
        saved = datetime(2026, 9, 30, 8, 55, 12, tzinfo=UTC)
        node = _node(saved)
        assert pt.make_witness(node, "k", src="live", first_seen_utc_us=_us(saved), gap_s=None) is None
        assert pt.make_witness(node, "k", src="live", first_seen_utc_us=_us(saved),
                               gap_s=pt.LIVE_GAP_S + 0.1) is None
        assert pt.make_witness(node, "k", src="live", first_seen_utc_us=None, gap_s=0.5) is None
        assert pt.make_witness(node, "k", src="live", first_seen_utc_us=_us(saved),
                               gap_s=pt.LIVE_GAP_S) is not None

    def test_a_naive_run_clock_is_no_witness(self):
        # seen at the very instant the naive clock reads in this machine's
        # zone, so only the quality guard can refuse it (a far-off first sight
        # would be refused by the 26 h guard instead -- the mutation sweep
        # caught that this pin once passed for the wrong reason)
        node = {"created_at": "2026-09-30T08:55:12"}
        us, q = timefmt.run_instant(node)
        assert q == "assumed_local"
        assert pt.make_witness(node, "k", src="live", first_seen_utc_us=us, gap_s=0.5) is None
        aware = {"created_at": "2026-09-30T08:55:12+00:00"}
        assert pt.make_witness(aware, "k", src="live", first_seen_utc_us=_us(
            datetime(2026, 9, 30, 8, 55, 12, tzinfo=UTC)), gap_s=0.5) is not None

    def test_beyond_any_zone_mistake_is_a_copied_in_run(self):
        saved = datetime(2026, 9, 30, 8, 55, 12, tzinfo=UTC)
        node = _node(saved - timedelta(days=3))
        assert pt.make_witness(node, "k", src="live", first_seen_utc_us=_us(saved), gap_s=0.5) is None

    def test_the_skew_is_signed_run_clock_minus_observer(self):
        ws = _live_witnesses([3600, 3600, 3600])
        assert all(w["skew_s"] == 3600 for w in ws)
        s = pt.summarize(ws)
        assert s["class"] == "ask" and s["skew_s"] == 3600 and s["src"] == "live"

    def test_the_thirty_minute_boundary(self):
        assert pt.summarize(_live_witnesses([1799] * 3))["class"] == "small"
        assert pt.summarize(_live_witnesses([1800] * 3))["class"] == "ask"
        assert pt.summarize(_live_witnesses([-1800] * 3))["class"] == "ask"

    def test_fewer_than_three_agreeing_runs_never_ask(self):
        assert pt.summarize(_live_witnesses([3600, 3600]))["class"] == "unsettled"
        # one copied-in folder among agreeing runs does not block the question
        s = pt.summarize(_live_witnesses([3600, 3600, 10, 3600, 3600]))
        assert s["class"] == "ask" and s["skew_s"] == 3600

    def test_live_witnesses_beat_folder_times(self):
        base = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
        inplace = [pt.make_witness(_node(base + timedelta(hours=i)), f"m{i}", src="in_place",
                                   folder_mtime_utc_us=_us(base + timedelta(hours=i))) for i in range(5)]
        live = _live_witnesses([3600] * 3)
        assert pt.summarize(inplace + live)["src"] == "live"
        assert pt.summarize(inplace + live[:2])["src"] == "in_place"


class TestAskOnce:

    def test_asked_once_stored_and_not_asked_again(self, inst):
        pt.add_witnesses(inst, "alpha", _live_witnesses([3600] * 3))
        cv = pt.clock_view(inst, "alpha")
        assert cv["ask"] and cv["auto_ask"] and cv["ask"]["n"] == 3
        assert cv["ask"]["examples"] and cv["ask"]["skew_text"] == "1 h 00 min"
        assert cv["ask"]["from_key"] == "r0", "the regime starts at its first run, named"
        pt.mark_shown(inst, "alpha", cv["ask"]["skew_s"])
        cv = pt.clock_view(inst, "alpha")
        assert cv["ask"] and not cv["auto_ask"], "shown once; it waits on Diagnostics"
        cv = pt.answer_skew(inst, "alpha", "experiment_pc", 3600)
        assert cv["ask"] is None
        a = cv["answers"][-1]
        assert (a["choice"], a["skew_s"], a["until_us"]) == ("experiment_pc", 3600, None)
        # more runs with the SAME skew: never asked again
        pt.add_witnesses(inst, "alpha", _live_witnesses(
            [3605, 3598, 3601], start=datetime(2026, 10, 2, 9, 0, tzinfo=UTC), key="s"))
        assert pt.clock_view(inst, "alpha")["ask"] is None

    def test_asked_again_when_the_skew_changes(self, inst):
        pt.add_witnesses(inst, "alpha", _live_witnesses([3600] * 3))
        pt.answer_skew(inst, "alpha", "ignore", 3600)
        later = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
        pt.add_witnesses(inst, "alpha", _live_witnesses([7200] * 5, start=later, key="t"))
        cv = pt.clock_view(inst, "alpha")
        assert cv["ask"] and cv["ask"]["skew_s"] == 7200 and cv["auto_ask"]
        assert cv["answers"][0]["until_us"] == _us(later + timedelta(seconds=7200)), \
            "the answered regime ends where the new one starts"

    def test_a_clock_fixed_later_ends_the_correction(self, inst):
        pt.add_witnesses(inst, "alpha", _live_witnesses([3600] * 3))
        cv = pt.answer_skew(inst, "alpha", "experiment_pc", 3600)
        start = cv["answers"][0]["from_us"]
        later = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
        pt.add_witnesses(inst, "alpha", _live_witnesses([2, 1, 3], start=later, key="f"))
        cv = pt.clock_view(inst, "alpha")
        assert cv["ask"] is None and cv["summary"]["class"] == "small"
        end = cv["answers"][0]["until_us"]
        assert end is not None and end > start
        assert pt.correction_for(cv["answers"], start + 1) is not None
        assert pt.correction_for(cv["answers"], end + 1) is None

    def test_a_stale_answer_is_refused(self, inst):
        pt.add_witnesses(inst, "alpha", _live_witnesses([3600] * 3))
        with pytest.raises(ValueError):
            pt.answer_skew(inst, "alpha", "this_pc", 7200)
        with pytest.raises(ValueError):
            pt.answer_skew(inst, "alpha", "both", 3600)

    def test_under_thirty_minutes_is_one_info_line_never_a_question(self, inst):
        pt.add_witnesses(inst, "alpha", _live_witnesses([125, 130, 128]))
        cv = pt.clock_view(inst, "alpha")
        assert cv["ask"] is None and cv["uncertainty_s"] == 128
        line = pt.diagnostics_line(inst, "alpha", "Asia/Seoul")
        assert line["level"] == "info" and not line["ask"]
        assert "within 2 min " in line["text"] and "never asked" in line["text"]

    def test_the_diagnostics_line_says_what_was_answered(self, inst):
        pt.add_witnesses(inst, "alpha", _live_witnesses([3600] * 3))
        line = pt.diagnostics_line(inst, "alpha", None)
        assert line["ask"] and "waiting for your answer" in line["text"]
        assert line["text"].startswith("Run clock: 1 h 00 min ahead of this PC")
        pt.answer_skew(inst, "alpha", "this_pc", 3600)
        line = pt.diagnostics_line(inst, "alpha", None)
        assert not line["ask"] and "this PC's clock is wrong" in line["text"]


class TestCorrection:

    def test_a_measured_skew_reads_to_the_minute(self):
        # the poll gap and whole-second run clocks add a second: 3598.96 s
        # measured is "1 h 00 min" (the real browser walk read "59 min 59 s")
        assert pt.skew_text(3598.96) == "1 h 00 min"
        assert pt.skew_text(-128) == "2 min"
        assert pt.skew_text(42.4) == "42 s"

    def test_a_whole_unit_skew_is_corrected_by_the_exact_unit(self):
        assert pt.correction_amount(3598.96) == 3600.0
        assert pt.correction_amount(-1801.2) == -1800.0
        assert pt.correction_amount(2500.4) == 2500.0, "not a zone step: the measured skew"

    def test_only_the_experiment_pc_answer_corrects_a_run(self):
        t0 = _us(datetime(2026, 10, 1, tzinfo=UTC))
        ans = [{"choice": "experiment_pc", "skew_s": 3600.0, "from_us": t0, "until_us": None}]
        c = pt.correction_for(ans, t0 + 10)
        assert c["delta_s"] == -3600 and c["corrected_us"] == t0 + 10 - 3_600_000_000
        assert pt.correction_for(ans, t0 - 1) is None, "before the witnesses: no evidence, no correction"
        for other in ("this_pc", "ignore"):
            assert pt.correction_for([dict(ans[0], choice=other)], t0 + 10) is None
        s = pt.sm_correction_for([dict(ans[0], choice="this_pc")], t0 + 10)
        assert s["delta_s"] == 3600

    def test_the_run_detail_shows_the_instant_and_a_labelled_correction(self, lab, monkeypatch):
        saved = datetime(2026, 9, 30, 8, 55, 12, tzinfo=UTC)
        run = {"run_end": _iso(saved, -7), "date": "2026-09-30", "time": "01:55:12",
               "folder_path": str(lab["tmp"] / "2026-09-30" / "#1_x_015512")}
        with lab["app"].test_request_context("/"):
            rc = routes._run_clock_view(run)
        assert rc["utc"] == "2026-09-30T08:55:12Z"
        assert (rc["recorded"], rc["recorded_off"], rc["corrected"]) == ("2026-09-30 01:55:12", "UTC-7", None)
        pt.add_witnesses(lab["inst"], "alpha", _live_witnesses([3600] * 3, start=saved - timedelta(hours=2)))
        pt.answer_skew(lab["inst"], "alpha", "experiment_pc", 3600)
        monkeypatch.setattr(routes, "_active_ctx", lambda: {"qualibrate_project": "alpha"})
        with lab["app"].test_request_context("/"):
            rc = routes._run_clock_view(run)
        assert rc["corrected"] == {"utc": "2026-09-30T07:55:12Z", "by": "-1 h 00 min"}
        assert rc["header"] is None, "no zone set anywhere: the header keeps the folder clock"
        pt.set_zone(lab["inst"], "alpha", "America/Los_Angeles")
        with lab["app"].test_request_context("/"):
            rc = routes._run_clock_view(run)
        assert rc["header"] == "2026-09-30 00:55:12 (UTC-7) corrected"
        tpl = (ROOT / "quam_state_manager" / "web" / "templates" / "_dataset_detail.html").read_text(encoding="utf-8")
        assert 'data-corrected-utc="{{ run_clock.corrected.utc }}"' in tpl
        assert "acquisition-PC local" in tpl, "no instant: the folder clock, as before"


# ===================================================== in place or copied
class TestInPlaceArchive:
    """[derived] classify_archive, cross-checked by simulation: an archive
    written in place keeps (run - mtime) constant; a copy does not."""

    @staticmethod
    def _archive(rng, n, skew_s, copied):
        t0 = datetime(2026, 9, 1, tzinfo=UTC)
        runs = sorted(t0 + timedelta(seconds=rng.uniform(0, 3 * 86400)) for _ in range(n))
        copy_at = t0 + timedelta(days=10)
        out = []
        for r in runs:
            if copied:
                mt = copy_at + timedelta(seconds=rng.uniform(0, 300))
            else:
                mt = r - timedelta(seconds=skew_s) + timedelta(seconds=rng.uniform(0, 60))
            out.append((_us(r), _us(mt)))
        return out

    def test_simulation(self):
        rng = random.Random(263)
        wrong = 0
        for trial in range(300):
            skew = rng.choice([0, 3600, -3600, 1800, -1800, 120])
            n = rng.randint(5, 60)
            v = pt.classify_archive(self._archive(rng, n, skew, copied=False))
            if not v["in_place"] or abs(v["skew_s"] - skew + 30) > 31:
                wrong += 1
            c = pt.classify_archive(self._archive(rng, n, skew, copied=True))
            if c["in_place"]:
                wrong += 1
        assert wrong == 0

    def test_too_few_or_too_close_runs_prove_nothing(self):
        t = _us(datetime(2026, 9, 1, tzinfo=UTC))
        assert pt.classify_archive([(t + i, t + i) for i in range(4)])["reason"] == "too_few"
        close = [(t + i * 60_000_000, t + i * 60_000_000) for i in range(10)]
        assert pt.classify_archive(close)["reason"] == "short_span"

    def test_the_status_route_reads_an_in_place_archive(self, lab, monkeypatch):
        from quam_state_manager.core.dataset import DatasetStore
        root = lab["data_a"]
        base = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
        for i in range(8):
            saved = base + timedelta(hours=3 * i)
            loc = saved.astimezone(timezone(timedelta(hours=9)))
            run = root / loc.strftime("%Y-%m-%d") / f"#{i + 1}_node_{loc.strftime('%H%M%S')}"
            _write(run / "node.json", json.dumps(dict(_node(saved, 9), id=i + 1)))
            _write(run / "data.json", "{}")
            mt = (saved - timedelta(hours=1)).timestamp()     # the run clock is 1 h ahead
            os.utime(run, (mt, mt))
        store = DatasetStore(root)
        monkeypatch.setattr(routes, "_active_dataset_stores",
                            lambda **k: [{"path": str(root), "store": store, "key": "a", "label": "a"}])
        monkeypatch.setattr(routes, "_active_ctx", lambda: {"qualibrate_project": "alpha"})
        j = lab["c"].get("/project-time/status").get_json()
        assert j["project"] == "alpha"
        assert j["clock"]["archive"]["in_place"] is True
        ask = j["clock"]["ask"]
        assert ask and ask["src"] == "in_place" and abs(ask["skew_s"] - 3600) < 1 and j["clock"]["auto_ask"]
        assert j["clock"]["run_offsets"] == {"+09:00": 8}
        # shown once, answered, and gone
        c = lab["c"]
        assert c.post("/project-time/skew-shown", data={"skew_s": ask["skew_s"]}).get_json()["ok"]
        assert not c.get("/project-time/status").get_json()["clock"]["auto_ask"]
        r = c.post("/project-time/skew-answer", data={"choice": "ignore", "skew_s": 7200})
        assert r.status_code == 409, "an answer to another skew is refused"
        r = c.post("/project-time/skew-answer", data={"choice": "ignore", "skew_s": ask["skew_s"]})
        assert r.status_code == 200
        assert c.get("/project-time/status").get_json()["clock"]["ask"] is None

    def test_a_copied_archive_is_no_witness(self, lab, monkeypatch):
        from quam_state_manager.core.dataset import DatasetStore
        root = lab["data_a"]
        base = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
        copy_t = time.time()
        for i in range(8):
            saved = base + timedelta(hours=3 * i)
            loc = saved.astimezone(timezone(timedelta(hours=9)))
            run = root / loc.strftime("%Y-%m-%d") / f"#{i + 1}_node_{loc.strftime('%H%M%S')}"
            _write(run / "node.json", json.dumps(dict(_node(saved, 9), id=i + 1)))
            os.utime(run, (copy_t, copy_t))
        store = DatasetStore(root)
        monkeypatch.setattr(routes, "_active_dataset_stores",
                            lambda **k: [{"path": str(root), "store": store, "key": "a", "label": "a"}])
        monkeypatch.setattr(routes, "_active_ctx", lambda: {"qualibrate_project": "alpha"})
        j = lab["c"].get("/project-time/status").get_json()
        assert j["clock"]["archive"]["reason"] == "copied"
        assert j["clock"]["summary"]["class"] == "none" and j["clock"]["ask"] is None


class TestQuietNote:

    def test_another_offset_is_a_note_never_a_warning(self):
        note = pt.zone_note("Asia/Seoul", {"-07:00": 40, "+09:00": 2})
        assert "UTC-7" in note and "Asia/Seoul (UTC+9)" in note and "not an error" in note
        assert pt.zone_note("Asia/Tokyo", {"+09:00": 40}) is None
        assert pt.zone_note("Asia/Seoul", {}) is None


# ======================================================= runs seen arriving
def _run_dir(root: Path, date: str, name: str, node: dict | None = None) -> Path:
    d = root / date / name
    d.mkdir(parents=True)
    if node is not None:
        (d / "node.json").write_text(json.dumps(node), encoding="utf-8")
    return d


class TestArrivals:

    def test_a_run_that_appears_between_two_looks_is_a_witness(self, tmp_path):
        root = tmp_path / "data"
        _run_dir(root, "2026-10-01", "#1_old_090000", {"created_at": "2026-10-01T09:00:00+09:00"})
        clock = {"t": 1_000.0}
        arr = run_arrivals.Arrivals(clock=lambda: clock["t"])
        arr.baseline([str(root)])
        saved = datetime(2026, 10, 1, 1, 0, tzinfo=UTC)
        clock["t"] = saved.timestamp() + 3600 * 0 + 1   # SM sees it 1 s after the run's own claim
        _run_dir(root, "2026-10-01", "#2_new_100000", _node(saved))
        assert arr.on_tick([str(root)], lambda r: 0.5) == 1
        done = arr.complete()
        assert len(done) == 1 and done[0]["root"] == str(root)
        w = done[0]["witness"]
        assert w["src"] == "live" and w["gap_s"] == 0.5 and abs(w["skew_s"] + 1) < 1e-6
        assert w["key"].endswith("::2026-10-01/#2_new_100000") and w["mtime_us"]
        assert arr.pending() == []

    def test_the_old_runs_of_a_first_look_are_never_arrivals(self, tmp_path):
        root = tmp_path / "data"
        _run_dir(root, "2026-10-01", "#1_a_090000", {})
        arr = run_arrivals.Arrivals()
        assert arr.on_tick([str(root)], lambda r: 0.5) == 0     # no baseline yet: this IS the baseline
        _run_dir(root, "2026-10-01", "#2_b_090100", {})
        assert arr.on_tick([str(root)], lambda r: 0.5) == 1

    def test_an_unbounded_sight_is_not_an_arrival(self, tmp_path):
        root = tmp_path / "data"
        _run_dir(root, "2026-10-01", "#1_a_090000", {})
        arr = run_arrivals.Arrivals()
        arr.baseline([str(root)])
        _run_dir(root, "2026-10-01", "#2_b_090100", {})
        assert arr.on_tick([str(root)], lambda r: pt.LIVE_GAP_S + 1) == 0   # SM slept / was closed
        _run_dir(root, "2026-10-01", "#3_c_090200", {})
        assert arr.on_tick([str(root)], lambda r: None) == 0
        assert arr.rejected == 2

    def test_a_burst_of_folders_is_a_copy_not_runs(self, tmp_path):
        root = tmp_path / "data"
        _run_dir(root, "2026-10-01", "#1_a_090000", {})
        arr = run_arrivals.Arrivals()
        arr.baseline([str(root)])
        for i in range(run_arrivals.MAX_PER_TICK + 1):
            _run_dir(root, "2026-10-01", f"#{10 + i}_p_0901{i:02d}", {})
        assert arr.on_tick([str(root)], lambda r: 0.5) == 0

    def test_a_node_not_written_yet_stays_pending_then_expires(self, tmp_path):
        root = tmp_path / "data"
        _run_dir(root, "2026-10-01", "#1_a_090000", {})
        clock = {"t": 1_000.0}
        arr = run_arrivals.Arrivals(clock=lambda: clock["t"])
        arr.baseline([str(root)])
        _run_dir(root, "2026-10-01", "#2_b_090100", None)
        arr.on_tick([str(root)], lambda r: 0.5)
        assert arr.complete() == [] and len(arr.pending()) == 1
        clock["t"] += run_arrivals.PENDING_TTL_S + 1
        assert arr.complete() == [] and arr.pending() == []

    def test_a_new_date_folder_counts_and_a_vanished_one_does_not(self, tmp_path):
        root = tmp_path / "data"
        _run_dir(root, "2026-10-01", "#1_a_235900", {})
        arr = run_arrivals.Arrivals()
        arr.baseline([str(root)])
        _run_dir(root, "2026-10-02", "#2_b_000100", {})
        assert arr.on_tick([str(root)], lambda r: 0.5) == 1
        shutil.rmtree(root / "2026-10-02")
        assert arr.on_tick([str(root)], lambda r: 0.5) == 0

    def test_the_watcher_measures_the_gap_between_good_looks(self, tmp_path):
        root = tmp_path / "data"
        _run_dir(root, "2026-10-01", "#1_a_090000", {})
        w = run_watch.RunWatcher(interval_s=0.05)
        w.set_roots([str(root)])
        assert w.poll_gap(str(root)) is None
        w.poll_once()
        assert w.poll_gap(str(root)) is None
        time.sleep(0.05)
        w.poll_once()
        g = w.poll_gap(str(root))
        assert g is not None and 0.03 < g < 5


class TestLivePathEndToEnd:

    def test_an_arrival_reaches_its_projects_record(self, lab):
        """watcher tick -> arrivals -> the run-ingest step -> project_time,
        filed under the project that owns the root."""
        app, root = lab["app"], lab["data_a"]
        _run_dir(root, "2026-10-01", "#1_a_090000", {"created_at": "2026-10-01T09:00:00+09:00"})
        (Path(lab["inst"])).mkdir(parents=True, exist_ok=True)
        (Path(lab["inst"]) / "project_dataset_roots.json").write_text(
            json.dumps({"alpha": [str(root.resolve())]}), encoding="utf-8")
        arr = run_arrivals.Arrivals()
        app.config["run_arrivals"] = arr
        arr.baseline([str(root)])
        now = datetime.now(UTC)
        for i in range(3):
            _run_dir(root, "2026-10-01", f"#{2 + i}_b_09010{i}",
                     _node(now + timedelta(seconds=3600 + i)))
            arr.on_tick([str(root)], lambda r: 0.5)
        steps = {f.__name__: f for f in routes._ingest_after_steps(app)}
        steps["run_clock"]([str(root)])
        cv = pt.clock_view(lab["inst"], "alpha")
        assert cv["summary"]["n_live"] == 3 and cv["ask"] and cv["ask"]["src"] == "live"
        assert abs(cv["ask"]["skew_s"] - 3600) < 5

    def test_the_watcher_feeds_the_arrival_log_before_the_ingest_kick(self):
        src = (ROOT / "quam_state_manager" / "web" / "routes.py").read_text(encoding="utf-8")
        body = src[src.index("def _run_watcher():"):src.index("def _run_arrivals(app):")]
        assert body.index("_a.on_tick(moved, _w.poll_gap)") < body.index("_run_ingest(app).kick")


# ================================================================= the JS
def test_project_time_selfcheck():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    r = subprocess.run([node, str(ROOT / "tests" / "project_time_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 40, r.stdout


def test_every_page_loads_the_script():
    base = (ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "asset_url('project-time.js')" in base
