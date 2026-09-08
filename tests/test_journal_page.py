"""docs/173 S2: the Calibration log page -- the story, rendered.

A real DatasetStore over a synthetic qualibrate folder is the app's active
store; the page renders every run as a card, filters by author/target
without re-rendering the header, lets a person claim a run, shows the raw
.md, moves unassigned lines under the chip on a click, and takes the
Experiment Runner's slot in the sidebar with Param History beneath it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager.core import journal
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web.app import create_app
from tests.test_story import DAY, _run_folder

ROOT = Path(__file__).resolve().parent.parent
_H = {"Origin": "http://localhost"}


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "data"
    _run_folder(root, 101, "02_resonator_spectroscopy", "09:12:00", qubits=["q1"])
    _run_folder(root, 104, "05_power_rabi", "14:05:00", qubits=["q4"], outcomes={"q4": "failed"},
                state={"qubits": {"q4": {"f_01": 4.8e9}}})
    inst = tmp_path / "inst"
    app = create_app(testing=True, instance_path=str(inst))
    app.config["dataset_store"] = DatasetStore(root)
    journal.append(inst, "chip", "set amp", kind="agent", reason="rabi left-biased", run_id=104,
                   paths=["qubits.q4.f_01"], when=datetime.strptime(f"{DAY} 14:12:00", "%Y-%m-%d %H:%M:%S"))
    return {"app": app, "client": app.test_client(), "inst": inst, "root": root}


class TestThePage:
    def test_full_page_and_partial_render_the_cards(self, world):
        c = world["client"]
        html = c.get(f"/journal?day={DAY}").get_data(as_text=True)
        assert 'id="card-101"' in html and 'id="card-104"' in html
        assert "rabi left-biased" in html and 'href="/dataset/by-run/104"' in html
        assert "✗ failed" in html and "jr-digest" in html
        assert "journal.js" in html, "the page bundle is emitted"
        part = c.get(f"/journal?day={DAY}", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "<html" not in part and 'id="jr-body"' in part

    def test_the_page_reads_at_a_glance(self, world):
        """Customer feedback 2026-09-08 ('is this readable by a person?'): the
        tagline no longer sits inside the h2, the day's numbers are stat pills,
        the strip is segments of numbered chips under a blue section header,
        and each run row is a grid of cells."""
        c = world["client"]
        html = c.get(f"/journal?day={DAY}").get_data(as_text=True)
        h2 = re.search(r"<h2>(.*?)</h2>", html, re.S).group(1)
        assert "every run, whoever ran it" not in h2 and 'class="muted jr-tagline">every run, whoever ran it' in html
        body = c.get(f"/journal/day?day={DAY}").get_data(as_text=True)
        assert 'class="jr-stat jr-stat-day">' in body and re.search(r'<span class="jr-stat"><b>2</b> runs</span>', body)
        assert "Per-target timeline" in body and 'class="jr-sec-title"' in body
        assert re.search(r'<a class="jr-pill jr-out-\w+" href="#card-104"[^>]*>104</a>', body), "a run is a numbered chip"
        assert '<span class="jr-seg-fam">' in body, "the family is named once per segment"
        assert '<span class="jr-sec-title">Runs <span class="jr-sec-count">2</span>' in body
        card = body[body.index('id="card-104"'):]
        assert '<span class="jr-pills">' in card and '<span class="jr-who-cell">' in card, "the row is cells in columns"

    def test_the_because_section_exists_apart_from_the_journal_lines(self, world):
        html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
        assert re.search(r'<p class="jr-because">\s*rabi left-biased\s*</p>', html)
        card101 = html[html.index('id="card-101"'):html.index('id="card-104"')]
        assert "not recorded" in card101, "a run with no reason says so"

    def test_a_hook_event_in_the_window_names_the_agent_on_the_card(self, world):
        c = world["client"]
        ts = datetime.strptime(f"{DAY} 14:09:10", "%Y-%m-%d %H:%M:%S").timestamp()
        r = c.post("/api/agent/event", json={"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "t1",
                                             "session_id": "s", "backend": "claude", "ts": ts,
                                             "summary": "python calibrations/05_power_rabi.py --qubits q4"}, headers=_H)
        assert r.status_code == 200
        html = c.get(f"/journal/day?day={DAY}").get_data(as_text=True)
        card104 = html[html.index('id="card-104"'):]
        assert 'title="inferred">by_claude</span>' in card104
        card101 = html[html.index('id="card-101"'):html.index('id="card-104"')]
        assert ">unknown<" in card101

    def test_filters_swap_only_the_body(self, world):
        c = world["client"]
        body = c.get(f"/journal/day?day={DAY}&q=q4").get_data(as_text=True)
        assert 'id="card-104"' in body and 'id="card-101"' not in body
        assert 'id="jr-filters"' not in body, "the body partial carries no header"
        body = c.get(f"/journal/day?day={DAY}&author=by_").get_data(as_text=True)
        assert "Nothing matches the filter" in body

    def test_the_author_is_unknown_until_someone_claims(self, world):
        c = world["client"]
        html = c.get(f"/journal/day?day={DAY}").get_data(as_text=True)
        assert html.count(">unknown<") == 2
        r = c.post("/journal/claim", json={"run_id": 101, "who": "박OO", "note": "mine"}, headers=_H)
        assert r.status_code == 200 and r.get_json()["claim"]["author"] == "human:박OO"
        html = c.get(f"/journal/day?day={DAY}").get_data(as_text=True)
        assert "human:박OO" in html and "<b>Note:</b> mine" in html
        assert "run #101 was run by human:박OO: mine" in journal.read(world["inst"], "chip", DAY), \
            "the claim is itself a journal line"
        assert c.post("/journal/claim", json={"run_id": "x"}, headers=_H).status_code == 400

    def test_raw_view_renders_the_md_with_links(self, world):
        html = world["client"].get(f"/journal/raw?day={DAY}").get_data(as_text=True)
        assert 'class="jr-run"' in html and "by-run/104" in html and "<script" not in html

    def test_unassigned_lines_are_offered_not_merged(self, world):
        c, inst = world["client"], world["inst"]
        journal.append(inst, "unassigned", "ran `05_power_rabi`", kind="hook",
                       when=datetime.strptime(f"{DAY} 03:00:00", "%Y-%m-%d %H:%M:%S"))
        html = c.get(f"/journal/day?day={DAY}").get_data(as_text=True)
        assert "1 journal line from a session that named no chip" in html
        assert "03:00:00" in html
        r = c.post("/journal/adopt", json={"day": DAY}, headers=_H)
        assert r.get_json()["moved"] == 1
        assert not journal.day_file(inst, "unassigned", DAY).exists()
        text = journal.read(inst, "chip", DAY)
        assert "ran `05_power_rabi`" in text and text.count("# chip") == 1
        html = c.get(f"/journal/day?day={DAY}").get_data(as_text=True)
        assert "named no chip" not in html

    def test_a_bad_day_falls_back_to_today(self, world):
        html = world["client"].get("/journal?day=nope").get_data(as_text=True)
        assert datetime.now().strftime("%Y-%m-%d") in html

    def test_no_dataset_is_said_not_crashed(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
        html = app.test_client().get("/journal").get_data(as_text=True)
        assert "No dataset folder is open" in html


class TestTheSidebar:
    def test_calibration_log_is_top_level_with_both_histories_beneath(self, world):
        """Customer feedback 2026-09-08: the Agent entry sits right above the
        Calibration log, and State History + Param History are its sub-items
        (State History used to stand alone above it)."""
        html = world["client"].get("/").get_data(as_text=True)
        i_sub = html.index('id="journal-subnav"')
        above = html[i_sub - 2500:i_sub]          # the sidebar just above the Calibration log entry
        below = html[i_sub:i_sub + 1200]          # its own sub-list
        assert 'href="/journal"' in above and 'href="/agent"' in above, "the Agent entry is right above the Calibration log"
        assert above.index('href="/agent"') < above.index('href="/journal"')
        assert 'href="/state-history"' not in above, "State History no longer stands alone above the Calibration log"
        assert 'href="/state-history"' in below and 'href="/param-history"' in below, "both histories are its sub-items"
        assert below.index('href="/state-history"') < below.index('href="/param-history"')
        assert 'id="state-history-subnav"' not in html
        assert html.count(">Calibration log</a>") == 1
        assert (above + below).count('href="/state-history"') == 1, "State History appears once in the nav around the Calibration log"
        pal = re.search(r'<script id="cmd-palette-data"[^>]*>(.*?)</script>', html, re.S).group(1)
        labels = [e["label"] for e in json.loads(pal)["pages"]]
        assert "Calibration log" in labels

    def test_the_page_marks_itself_active(self, world):
        html = world["client"].get("/journal").get_data(as_text=True)
        assert re.search(r'href="/journal"[^>]*class="active"', html)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_page_js_under_jsdom():
    r = subprocess.run(["node", str(ROOT / "tests" / "journal_page_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
