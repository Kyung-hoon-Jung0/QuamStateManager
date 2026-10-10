"""docs/291: one day of the Calibration log that holds thousands of runs.

A paged day renders its newest rows and sends the rest a slice at a time;
search and the author filter still answer for the whole day; any row a link
names can be reached; a day under a page renders exactly as before; one
build reads each run file's signature once and the zone setting once."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager.core import hub_index, hub_query, hub_rules, project_time, story
from quam_state_manager.core.hub_store import HubStore
from quam_state_manager.web import journal_routes, routes
from tests.test_calibration_log_hub import DAY, _card_html, _run_event, world  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "calibration_log_small_day.html"


def _clock(i: int) -> str:
    return f"{10 + i // 60:02d}:{i % 60:02d}:00"


def _day(world, n, *, targets=lambda i: ("qA1",), node=lambda i: "scan", folders=False):
    """n runs on DAY, one a minute from 10:01, each with a new state."""
    flat = None
    root = Path(world["store"].conn.execute("SELECT path FROM roots").fetchone()[0])
    for i in range(1, n + 1):
        _, flat = _run_event(world, {"v": i, "w": i % 3}, clock=_clock(i), run_id=i, prev=flat,
                             targets=targets(i), node=node(i))
        if folders:
            folder = root / f"{DAY}/#{i}_{node(i)}_{_clock(i).replace(':', '')}"
            folder.mkdir(parents=True)
            (folder / "node.json").write_text(json.dumps({"data": {
                "parameters": {"model": {"qubits": list(targets(i)), "span": i % 2}},
                "outcomes": {t: "successful" for t in targets(i)}}}))
            (folder / "data.json").write_text(json.dumps({"fit_results": {}}))


def _rows(html):
    out = []
    for m in re.finditer(r'<details class="jr-card (jr-run|jr-write)[^>]*>', html):
        tag = m.group(0)
        out.append(re.search(r' id="([^"]+)"', tag).group(1))
    return out


def _more(html, where):
    m = re.search(r'<div class="jr-more-row jr-more-' + where + r'">.*?data-url="([^"]+)"[^>]*>([^<]+)</button>'
                  r' <span class="muted jr-more-note">([^<]+)</span>', html)
    return (m.group(1).replace("&amp;", "&"), m.group(2), m.group(3)) if m else None


@pytest.fixture
def paged(world, monkeypatch):
    monkeypatch.setattr(journal_routes, "PAGE_CARDS", 3)
    monkeypatch.setattr(journal_routes, "LAZY_CARDS", 2)
    return world


def _get(world, url):
    r = world["client"].get(url)
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


# (a) ---------------------------------------------------------------------

def test_a_big_day_renders_only_its_newest_page(paged):
    _day(paged, 8, targets=lambda i: ("qA1", "qA2") if i == 8 else ("qA1",))
    html = _get(paged, f"/journal/day?day={DAY}")
    assert _rows(html) == ["card-6", "card-7", "card-8"]
    assert _more(html, "earlier")[1:] == ("Show 3 earlier", "(5 more on this day)")
    assert _more(html, "later") is None
    assert 'Showing <b class="jr-window-n">3</b> of 8 rows on this day.' in html
    assert 'data-jr-paged="1"' in html and "jr-search-cache" not in html
    # its rows carry no search text: the server answers the search
    assert "data-jr-search" not in _card_html(html, 'id="card-7"')
    # every count, the strip and the instants are the WHOLE day's
    assert 'Runs <span class="jr-sec-count">8</span>' in html and "<b>8</b> runs" in html
    # the strip of more runs than a page arrives after the rows, folded
    assert 'hx-get="/journal/strip?day=2026-10-03&amp;q=&amp;author=" hx-trigger="load"' in html
    assert 'class="jr-pill ' not in html and "loading the strip of 8 runs..." in html
    strip = _get(paged, f"/journal/strip?day={DAY}")
    pills = re.findall(r'class="jr-pill [^"]*" href="#card-(\d+)"', strip)
    assert sorted(set(pills), key=int) == [str(i) for i in range(1, 9)] and len(pills) == 9   # run 8 on two targets
    assert 'jr-digest jr-digest-folded">' in strip and ">all 8 runs</button>" in strip
    deltas = re.search(r'data-jr-ts="([^"]+)"', html).group(1).split(",")
    assert len(deltas) == 8 and all(int(d) == 60_000 for d in deltas[1:])


def test_day_instants_round_up_to_the_millisecond():
    # the page compares them with a visit stamped in whole milliseconds
    cards = [{"ts": 1.0001}, {"ts": 1.0012}, {"ts": None}, {"ts": 2}]
    assert journal_routes._ts_deltas(cards) == "1001,1,-1002,2000"


# (b) ---------------------------------------------------------------------

def _walk(world, html, where):
    """Every row reached by pressing the control at one end, in page order."""
    rows, more, presses = [], _more(html, where), 0
    while more:
        part = _get(world, more[0])
        got = _rows(part)
        rows = got + rows if where == "earlier" else rows + got
        more = _more(part, where)
        presses += 1
    return rows, presses


def test_show_earlier_and_later_give_every_row_once_in_time_order(paged):
    _day(paged, 8)
    every = [f"card-{i}" for i in range(1, 9)]
    html = _get(paged, f"/journal/day?day={DAY}")
    earlier, presses = _walk(paged, html, "earlier")
    assert earlier + _rows(html) == every and presses == 2       # 3 + 3 + 2
    # the second press says what is left
    first = _get(paged, _more(html, "earlier")[0])
    assert _more(first, "earlier")[1:] == ("Show 2 earlier", "(2 more on this day)")
    # from the oldest rows forwards
    html = _get(paged, f"/journal/day?day={DAY}&at=card-1")
    assert _rows(html) == ["card-1", "card-2", "card-3"] and _more(html, "earlier") is None
    later, _ = _walk(paged, html, "later")
    assert _rows(html) + later == every
    assert 'id="card-' not in _get(paged, f"/journal/cards?day={DAY}&before=card-1")


# (c) ---------------------------------------------------------------------

def test_search_and_author_filter_the_whole_day_with_its_totals(paged, monkeypatch):
    _day(paged, 8, targets=lambda i: ("qA2",) if i % 2 == 0 else ("qA1",))
    built = []
    original = story.build_day
    monkeypatch.setattr(story, "build_day", lambda *a, **k: built.append(1) or original(*a, **k))
    _get(paged, f"/journal/day?day={DAY}")
    # a new filter of the open day re-filters its last build
    html = _get(paged, f"/journal/day?day={DAY}&q=qA2&reuse=1")
    assert len(built) == 1
    assert _rows(html) == ["card-4", "card-6", "card-8"]
    assert 'Showing <b class="jr-window-n">3</b> of 4 rows that match.' in html
    assert 'Runs <span class="jr-sec-count">4</span>' in html
    assert _more(html, "earlier")[1:] == ("Show 1 earlier", "(1 more match)")
    assert _walk(paged, html, "earlier")[0] == ["card-2"]
    # the strip holds the matching runs of the whole day, not only the rows shown
    strip = _get(paged, f"/journal/strip?day={DAY}&q=qA2")
    assert re.findall(r'href="#(card-\d+)"', strip) == ["card-2", "card-4", "card-6", "card-8"]
    assert ">all 4 runs</button>" in strip and len(built) == 1
    # a match only among the oldest rows is found
    html = _get(paged, f"/journal/day?day={DAY}&q=%231%20qA1&reuse=1")
    assert _rows(html) == ["card-1"] and "jr-window-n" not in html
    # the author filter, the same way
    story.claim_run(paged["inst"], "chipX", 1, author="human:user-b")
    story.claim_run(paged["inst"], "chipX", 2, author="human:user-b")
    html = _get(paged, f"/journal/day?day={DAY}&author=human:user-b")
    assert _rows(html) == ["card-1", "card-2"] and 'Runs <span class="jr-sec-count">2</span>' in html
    # no match anywhere: the empty line, not a window
    html = _get(paged, f"/journal/day?day={DAY}&q=nothing-matches&reuse=1")
    assert _rows(html) == [] and "Nothing matches the filter." in html and "jr-more-row" not in html


def test_a_filter_without_a_kept_build_builds_the_day(paged, monkeypatch):
    _day(paged, 8)
    built = []
    original = story.build_day
    monkeypatch.setattr(story, "build_day", lambda *a, **k: built.append(1) or original(*a, **k))
    paged["app"].config.get("journal_day_cards", {}).clear()
    html = _get(paged, f"/journal/day?day={DAY}&q=%238&reuse=1")
    assert built == [1] and _rows(html) == ["card-8"]


# (d) ---------------------------------------------------------------------

def test_a_row_outside_the_newest_page_is_reachable(paged):
    _day(paged, 8)
    paged["add"]({"v": 99}, kind="sm_apply", instant=f"{DAY}T10:02:30-04:00")
    write = [i for i in _rows(_get(paged, f"/journal/day?day={DAY}&at=card-1")) if i.startswith("write-")]
    assert write == ["write-event-9"]
    # a jump: the page of rows around it, with the controls both ways
    html = _get(paged, f"/journal/day?day={DAY}&at=card-4")
    assert "card-4" in _rows(html) and _more(html, "earlier") and _more(html, "later")
    # a deep link opens the page on it
    page = _get(paged, f"/journal?day={DAY}&at=write-event-9")
    assert 'id="write-event-9"' in page
    # its body arrives on open, whatever rows the page holds
    body = _get(paged, f"/journal/card?day={DAY}&card=card-2")
    assert '<ul class="jr-changes">' in body
    # a reload of the same day keeps the rows a page shows
    html = _get(paged, f"/journal/day?day={DAY}&from=card-2&to=card-4")
    assert _rows(html) == ["card-2", "write-event-9", "card-3", "card-4"]
    assert _more(html, "earlier")[2] == "(1 more on this day)" and _more(html, "later")[2] == "(4 more on this day)"
    html = _get(paged, f"/journal/day?day={DAY}&from=card-5")
    assert _rows(html) == ["card-5", "card-6", "card-7", "card-8"] and _more(html, "later") is None
    # a row that is not on the day: said, never a guess
    gone = _get(paged, f"/journal/cards?day={DAY}&before=card-99")
    assert "This day changed since it was shown." in gone and 'id="card-' not in gone


# (e) ---------------------------------------------------------------------

def _small_day_fragment(world):
    """A day under a page, normalized: no temp path, no folder hash."""
    _day(world, 5, targets=lambda i: ("qA1", "qA2") if i == 3 else ("qA1",), node=lambda i: "scan" if i % 2 else "echo")
    world["add"]({"v": 9, "w": 0}, kind="sm_apply", instant=f"{DAY}T10:03:30-04:00")
    html = _get(world, f"/journal/day?day={DAY}")
    root = world["store"].conn.execute("SELECT path FROM roots").fetchone()[0]
    for key in {routes._folder_key(root), story._folder_key(root)}:
        html = html.replace(key, "KEY")
    for path in sorted({str(Path(root).parent), str(Path(root).parent).replace("\\", "/")}, key=len, reverse=True):
        html = html.replace(path, "TMP")
    return re.sub(r'TMP[^"<]*', lambda m: m.group(0).replace("\\", "/"), html)


def test_a_day_under_a_page_renders_exactly_as_before(world):
    # P0-1: old -> new, why: the golden was re-written once -- this day's "w"
    # goes 1 -> 2 -> 0 -> 1 with no later read of the chip confirming #2 or #3
    # (an excursion: listed apart, not as changes), and every change that stays
    # unconfirmed carries its verdict; the page's paging is unchanged
    # S10 walk (round 4): re-written once more -- the excursion section's words
    # "never confirmed on the chip ... came back to what the chip held" -> what the
    # records prove ("not confirmed before the value came back"), nothing else
    html = _small_day_fragment(world)
    if os.environ.get("CALIBRATION_LOG_GOLDEN_WRITE") == "1":
        GOLDEN.write_text(html, encoding="utf-8", newline="\n")
    assert html == GOLDEN.read_text(encoding="utf-8"), "a day under a page must render byte for byte as before"
    assert "jr-search-cache" in html and "jr-more-row" not in html and "data-jr-paged" not in html


# (f) ---------------------------------------------------------------------

def test_one_build_reads_each_run_files_signature_once(world, monkeypatch):
    _day(world, 12, folders=True, node=lambda i: "scan" if i % 3 else "echo")
    seen = []
    original = story._file_sig
    monkeypatch.setattr(story, "_file_sig", lambda p: seen.append(os.path.normcase(os.path.normpath(p))) or original(p))
    cards = [c for c in world["build"]()["cards"] if c["kind"] == "run"]
    # the run before each (its "previous") was read too -- through the same stat
    assert sum(1 for c in cards if c.get("prev_run_id")) >= 9
    assert len(seen) == 24 and len(set(seen)) == 24, "one signature per run file per build"


def test_one_build_reads_the_zone_setting_once(world, monkeypatch):
    _day(world, 8)
    project_time.set_zone(world["inst"], "projA", "America/New_York")
    ctx = hub_index.context(world["store"], instance=world["inst"], project="projA")
    reads = []
    original = project_time.display_zone
    monkeypatch.setattr(project_time, "display_zone", lambda *a, **k: reads.append(1) or original(*a, **k))
    data = story.build_day(world["inst"], "chipX", DAY, ds=None, ledger=ctx, with_gates=False)
    assert len([c for c in data["cards"] if c["kind"] == "run"]) == 8
    assert len(reads) == 1, len(reads)
    assert [c["time"] for c in data["cards"]][:2] == ["10:01:00", "10:02:00"]


def _previous_reference(store, eids):
    """docs/281's per-run reading of the previous run, kept as the reference."""
    out = {}
    with hub_index.snapshot(store) as (conn, _index):
        roots = {row[0]: row[1] for row in conn.execute("SELECT root_id, path FROM roots")}
        for eid in eids:
            cur = conn.execute("SELECT root_id, experiment, ord, targets FROM events WHERE eid=?", (eid,)).fetchone()
            if cur is None or cur["root_id"] is None:
                continue
            mine = set(hub_query._target_names(cur["targets"]))
            for row in conn.execute("SELECT run_id, rel_path, targets FROM events WHERE kind='run' AND root_id=? "
                                    "AND experiment=? AND ord<? ORDER BY ord DESC LIMIT 50",
                                    (cur["root_id"], cur["experiment"], cur["ord"])):
                theirs = set(hub_query._target_names(row["targets"]))
                if not mine or not theirs or mine & theirs:
                    out[eid] = (row["run_id"], str(Path(roots[cur["root_id"]]) / (row["rel_path"] or "")))
                    break
    return out


def test_previous_in_folder_reads_once_and_answers_as_per_run(tmp_path):
    import random
    rnd = random.Random(291)
    with HubStore(tmp_path / "ledger") as store:
        roots = [store.register_root(tmp_path / name, "+00:00") for name in ("a", "b")]
        previous, eids = {}, []
        for i in range(400):
            kind = "run" if rnd.random() < 0.93 else "sm_apply"
            targets = rnd.choice([["qA1"], ["qA2"], ["qA1", "qA2"], [], ["qA3"]])
            fact = {"kind": kind, "t_utc_us": 1_700_000_000_000_000 + i * 60_000_000, "ord": i + 1,
                    "root_id": rnd.choice(roots), "rel_path": f"d/#{i}_x", "run_id": i if kind == "run" else None,
                    "experiment": rnd.choice(["scan", "echo", None]) if kind == "run" else "apply",
                    "targets": json.dumps(targets), "state_hash": str(i), "flags": 0}
            doc = {"v": i}
            flat = hub_rules.flatten(doc)
            eids.append(store.append(fact, hub_rules.diff(previous, flat), doc, flat))
            previous = flat
        # one node in one folder, positions 0..102: a run looks at the 50
        # runs just before it -- position 51 (qA7) does not reach position 0,
        # position 102 (qA6) does reach position 52, its 50th
        seq = ["qA7"] + ["qA9"] * 50 + ["qA7", "qA6"] + ["qA9"] * 49 + ["qA6"]
        sweep = []
        for k, target in enumerate(seq):
            i = 400 + k
            fact = {"kind": "run", "t_utc_us": 1_700_000_000_000_000 + i * 60_000_000, "ord": i + 1,
                    "root_id": roots[0], "rel_path": f"d/#{i}_y", "run_id": i, "experiment": "sweep",
                    "targets": json.dumps([target]), "state_hash": str(i), "flags": 0}
            doc = {"v": i}
            flat = hub_rules.flatten(doc)
            sweep.append(store.append(fact, hub_rules.diff(previous, flat), doc, flat))
            previous = flat
        asked = eids[150:] + sweep + [10**9]
        got = hub_query.previous_in_folder(store, asked)
        want = _previous_reference(store, asked)
        assert got == want and len(want) > 150
        assert sweep[51] not in got
        assert got[sweep[102]][0] == 452 and got[sweep[53]][0] == 450
        # asked alone, a run still reaches back 50 runs
        assert hub_query.previous_in_folder(store, [sweep[102]]) == {sweep[102]: got[sweep[102]]}
        assert hub_query.previous_in_folder(store, [sweep[51]]) == {}


# the paged rows' squeeze -------------------------------------------------

def test_a_paged_row_keeps_its_text_and_attribute_values(paged, monkeypatch):
    _day(paged, 8)
    reason = 'drift\n    detail=2 "quoted"'
    original = story._compute_gate
    monkeypatch.setattr(story, "_compute_gate", lambda run: dict(original(run), verdict="fail", reason=reason))
    html = _get(paged, f"/journal/day?day={DAY}")
    row = _card_html(html, 'id="card-8"')
    # the value keeps its line break, its spaces and its "detail=" word
    assert 'own saved state \u2014 drift\n    detail=2 &#34;quoted&#34;">gate fail</span>' in row
    # the gaps between tags are gone
    assert '<summary><span class="jr-time">' in row and "</summary><div" in row


# the client --------------------------------------------------------------

def client_fixture():
    """The real read routes over a synthetic paged day (10 runs, a page of 4),
    for ``calibration_log_big_day_selfcheck.cjs``."""
    import tempfile
    from unittest.mock import patch
    from quam_state_manager.web.app import create_app
    day = "2026-10-04"

    def build(*args, **kwargs):
        cards = [{"kind": "run", "run_id": i, "node": "scan", "family_label": "scan", "family_short": "scan",
                  "targets": [f"qA{1 + i % 2}"], "time": f"12:{i:02d}:00", "ts": 1_000_000.0 + 60 * i,
                  "author": "human:user-a", "certainty": "claimed", "outcome": "ok", "gate": None, "writes": [],
                  "params_diff": [], "because": "", "figure": None, "uid": None, "plan_id": None, "note": "",
                  "duration_s": 1, "folder": "data", "journal": []} for i in range(1, 11)]
        return {"day": day, "chip": "chipX", "cards": cards, "loose": [], "unassigned": [],
                "counts": story._counts(cards), "timeline": story._timeline(cards)}

    with tempfile.TemporaryDirectory() as folder, \
            patch.object(story, "build_day", side_effect=build), \
            patch.object(journal_routes, "PAGE_CARDS", 4), patch.object(journal_routes, "LAZY_CARDS", 2):
        client = create_app(testing=True, instance_path=folder).test_client()
        get = lambda url, **kw: client.get(url, **kw).get_data(as_text=True)  # noqa: E731
        page = get(f"/journal?day={day}", headers={"HX-Request": "true"})
        filtered = get(f"/journal/day?day={day}&q=qA2&reuse=1")
        slices = {}
        for html in (page, filtered):
            while _more(html, "earlier"):
                url = _more(html, "earlier")[0]
                html = slices[url] = get(url)
        return {"day": day, "page": page, "slices": slices, "filtered": filtered,
                "strip": get(f"/journal/strip?day={day}"),
                "at": get(f"/journal/day?day={day}&at=card-1"),
                "ts": [1_000_000.0 + 60 * i for i in range(1, 11)]}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_big_day_client_selfcheck():
    env = {**os.environ, "BIG_DAY_PYTHON": sys.executable}
    result = subprocess.run(["node", str(ROOT / "tests/calibration_log_big_day_selfcheck.cjs")],
                            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if result.returncode == 2:
        pytest.skip("jsdom not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all checks passed (11 pins)" in result.stdout, result.stdout


if __name__ == "__main__":
    os.environ["SM_DISABLE_ENV_WARMUP"] = "1"
    os.environ["QUALIBRATE_CONFIG_FILE"] = str(ROOT / "tmp_cr_audit/log_big_day/missing-config")
    print(json.dumps(client_fixture()))


def test_counts_read_with_thousands_separators(paged, monkeypatch):
    """A paged day says "Showing 300 of 3,000"; its run counts read the same way."""
    # one a SECOND here: the minute clock of _day runs past midnight beyond ~830 runs
    monkeypatch.setitem(globals(), "_clock", lambda i: f"10:{i // 60:02d}:{i % 60:02d}")
    _day(paged, 1001)
    html = _get(paged, f"/journal/day?day={DAY}")
    assert 'Runs <span class="jr-sec-count">1,001</span>' in html and "<b>1,001</b> runs" in html
