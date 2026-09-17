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
        # docs/191 N01: the day swap now carries the author list out-of-band too,
        # so split the CARDS from the <select> before counting either.
        body, _, oob = html.partition('<select name="author"')
        assert body.count(">unknown<") == 2, "both runs' authors"
        assert ">unknown<" in oob, "and the filter offers it as a choice"
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


class TestTheDayPickerCannotCrashThePage:
    """docs/191 A02 -- every value the page's own date input can produce must
    answer, not 500. Measured before the fix: 1900-01-01 and 9999-12-31 both
    returned 500 with an OSError / OverflowError traceback."""

    @pytest.mark.parametrize("day", ["0001-01-01", "1900-01-01", "1969-12-31",
                                     "1970-01-01", "2099-01-01", "9999-12-31",
                                     "not-a-date", ""])
    def test_every_extreme_day_answers(self, world, day):
        c = world["client"]
        for url in (f"/journal?day={day}", f"/journal/day?day={day}"):
            r = c.get(url, headers={"HX-Request": "true"})
            assert r.status_code == 200, (url, r.status_code)

    def test_the_last_day_of_the_calendar_has_no_next(self, world):
        """`datetime.max + 1 day` has no answer; the arrow points at itself
        rather than raising."""
        r = world["client"].get("/journal/day?day=9999-12-31",
                                headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert b"9999-12-31" in r.data

    def test_an_ordinary_day_still_has_both_neighbours(self, world):
        """The clamp must only bite at the ends of the calendar. The arrows
        live in the page shell, not in the body `/journal/day` swaps."""
        from datetime import datetime, timedelta
        d0 = datetime.strptime(DAY, "%Y-%m-%d")
        prev = (d0 - timedelta(days=1)).strftime("%Y-%m-%d")
        nxt = (d0 + timedelta(days=1)).strftime("%Y-%m-%d")
        html = world["client"].get(f"/journal?day={DAY}",
                                   headers={"HX-Request": "true"}
                                   ).get_data(as_text=True)
        assert prev in html and nxt in html, (prev, nxt)

    def test_the_first_day_of_the_calendar_has_no_previous(self, world):
        html = world["client"].get("/journal?day=0001-01-01",
                                   headers={"HX-Request": "true"}
                                   ).get_data(as_text=True)
        assert "JournalPage.day('0001-01-01')" in html


class TestACorrectionIsNotAnErasure:
    """Round 2 (2026-09-17), found by pressing the form in a real browser: the
    summary invites "Correct the author / add a note", and the form REPLACED the
    whole claim -- so a person fixing a misspelt name lost the sentence they had
    written, with nothing on screen to warn them. Three separate holes: the
    boxes showed nothing, the door deleted an unmentioned note, and the journal
    ended up asserting two different authors for one run at one stamped time."""

    def test_the_boxes_show_what_is_already_recorded(self, world):
        c = world["client"]
        r = c.post("/journal/claim", json={"run_id": 104, "who": "Kyunghoon",
                                          "note": "fridge still warming"}, headers=_H)
        assert r.status_code == 200
        html = c.get(f"/journal?day={DAY}").get_data(as_text=True)
        assert 'class="jr-who" placeholder="your name" value="Kyunghoon"' in html, \
            "the name box must arrive carrying the author it is about to replace"
        assert 'class="jr-note-in" placeholder="optional" value="fridge still warming"' in html, \
            "the note box must arrive carrying the note it is about to replace"

    def test_a_claim_that_says_nothing_about_the_note_keeps_it(self, world):
        c = world["client"]
        c.post("/journal/claim", json={"run_id": 104, "who": "Kyunghoon",
                                       "note": "fridge still warming"}, headers=_H)
        rec = c.post("/journal/claim", json={"run_id": 104, "who": "Jihoon"},
                     headers=_H).get_json()["claim"]
        assert rec["author"] == "human:Jihoon"
        assert rec["note"] == "fridge still warming", \
            "correcting the NAME deleted the note the last claim left"

    def test_an_explicitly_empty_note_still_clears_it(self, world):
        """The other half: a person who empties a box they can SEE means it."""
        c = world["client"]
        c.post("/journal/claim", json={"run_id": 104, "who": "K", "note": "n"}, headers=_H)
        rec = c.post("/journal/claim", json={"run_id": 104, "who": "K", "note": ""},
                     headers=_H).get_json()["claim"]
        assert rec["note"] is None

    def test_the_later_claim_names_the_one_it_corrects(self, world):
        """The journal is append-only and both lines carry the RUN's time, so
        without this the day reads as two people claiming one run with no way to
        tell which was said last."""
        c = world["client"]
        c.post("/journal/claim", json={"run_id": 104, "who": "Kyunghoon"}, headers=_H)
        c.post("/journal/claim", json={"run_id": 104, "who": "Jihoon"}, headers=_H)
        text = journal.read(world["inst"], "chip", DAY)
        lines = [ln for ln in text.splitlines() if "run #104 was run by" in ln]
        assert len(lines) == 2, lines
        assert "corrects an earlier claim of human:Kyunghoon" in lines[-1], lines
        assert "corrects an earlier claim" not in lines[0], "the FIRST claim corrected nothing"

    def test_a_reclaim_by_the_same_person_is_not_a_correction(self, world):
        c = world["client"]
        c.post("/journal/claim", json={"run_id": 104, "who": "Kyunghoon"}, headers=_H)
        c.post("/journal/claim", json={"run_id": 104, "who": "Kyunghoon", "note": "added later"},
               headers=_H)
        text = journal.read(world["inst"], "chip", DAY)
        assert "corrects an earlier claim" not in text, \
            "the same name twice is someone adding a note, not correcting anyone"


class TestTheAgentsJournalDoorAnswersTheDayItWasAsked:
    """The PAGE spells it `day=`; this door spelt it `date=` only, so an agent
    copying the page's own URL was handed TODAY -- and would read an empty
    answer as 'nothing happened that day'."""

    def test_both_spellings_reach_the_same_day(self, world):
        c = world["client"]
        by_date = c.get(f"/api/agent/journal?date={DAY}").get_json()
        by_day = c.get(f"/api/agent/journal?day={DAY}").get_json()
        assert by_date["date"] == DAY and by_day["date"] == DAY
        assert by_day["text"] == by_date["text"] != ""
        assert "rabi left-biased" in by_day["text"]

    def test_neither_spelling_still_means_today(self, world):
        today = datetime.now().strftime("%Y-%m-%d")
        b = world["client"].get("/api/agent/journal").get_json()
        assert b["date"] == today


class TestTheDayNavigationActuallyNavigates:
    """docs/191 N01, found by pressing ‹ four times in Chrome and watching it
    move ONE day. `/journal/day` swapped `#jr-body` only, and the nav lives
    outside it -- so `prev_day`, `next_day`, the `›` disabled state and the
    `today` button were whatever the full page render baked in and never moved
    again. One press and the log was stuck: ‹ went to the same day forever, ›
    stayed disabled, and `today` never appeared at all."""

    def _nav(self, html):
        i = html.find('id="jr-daynav"')
        return html[i:html.find("</span>", i)] if i >= 0 else ""

    def test_the_day_swap_carries_the_nav_with_it(self, world):
        html = world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True)
        nav = self._nav(html)
        assert nav, "the nav must come back with the body, or it cannot move"
        assert 'hx-swap-oob="true"' in nav, "and out-of-band, since the target is #jr-body"

    def test_each_step_hands_back_the_NEXT_pair_of_neighbours(self, world):
        c = world["client"]
        seen = []
        day = DAY
        for _ in range(4):
            nav = self._nav(c.get(f"/journal/day?day={day}").get_data(as_text=True))
            m = re.search(r"JournalPage\.day\('(\d{4}-\d{2}-\d{2})'\)[^>]*title=\"previous day\"", nav)
            if not m:
                m = re.search(r"JournalPage\.day\('(\d{4}-\d{2}-\d{2})'\)", nav)
            assert m, nav
            day = m.group(1)
            seen.append(day)
        assert len(set(seen)) == 4, f"four presses must reach four days, got {seen}"
        assert seen == sorted(seen, reverse=True), seen

    def test_today_appears_when_you_leave_today_and_goes_when_you_return(self, world):
        c = world["client"]
        today = datetime.now().strftime("%Y-%m-%d")
        on_today = self._nav(c.get(f"/journal/day?day={today}").get_data(as_text=True))
        assert ">today<" not in on_today, "there is no 'go to today' when you are on it"
        assert "disabled" in on_today, "and no tomorrow to go to"
        away = self._nav(c.get(f"/journal/day?day={DAY}").get_data(as_text=True))
        assert ">today<" in away, "the way back must appear once you have left"
        assert "disabled" not in away, "and the next day must become reachable"

    def test_the_picker_follows_the_day_it_landed_on(self, world):
        nav = self._nav(world["client"].get(f"/journal/day?day={DAY}").get_data(as_text=True))
        assert f'id="jr-day-pick" value="{DAY}"' in nav

    def test_the_full_page_and_the_swap_render_the_same_nav(self, world):
        """One partial, two callers -- the strip cannot drift between them."""
        c = world["client"]
        full = self._nav(c.get(f"/journal?day={DAY}").get_data(as_text=True))
        swap = self._nav(c.get(f"/journal/day?day={DAY}").get_data(as_text=True))
        assert full and swap
        assert swap.replace(' hx-swap-oob="true"', "") == full, \
            "the only difference may be the out-of-band marker"
