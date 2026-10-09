"""S10 walk -- the Versions panel, State History and the History drawer, as a
person met them on a copy of a real chip (a real-browser walk).

Pins, each mutation-checked:

1. a LISTING keeps another folder's rows, labelled (docs/250); its notes say
   they are listed below and are not this folder's changes -- never "not part
   of this folder's timeline" / "no runs" over rows drawn right below -- and
   each such row says on itself why it is there. A value surface keeps its
   wording.
2. ONE count -- the rows the listing lists -- on the top-bar badge, the
   History (N) button, the drawer header, the Versions header and foot and
   State History, read through one fixture; no count (and a backing-off
   re-ask) while the ledger catches up.
3. State History pages: first / prev / next / last, a page box, a page past
   the end shows the last page; the subtitle says recorded states.
4. the Versions rows do not print their version id; tooltips speak plainly.
5. "Try again" on every listing that ends on an unreadable change history,
   the value drawer, Column History, the Param History drawer and the hover
   trends -- one manual press, never an automatic re-ask.
6. the live-change tracker's baseline is a badge on the row whose time is the
   banner's -- never a label, a pin or an Unpin the user did not make.
7. a Take snapshot that adds no row says so.
8. a run's bookmarked capture rides the run's row (final review P1).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_lanes, hub_sync, hub_versions, value_history
from quam_state_manager.core.history import LIVE_BASELINE_LABEL
from quam_state_manager.web import routes
from tests.test_hub_drawer import chip_state, make_app, patch, run, write_chip
from tests.test_hub_folder_view import _apply, _edit, _load, _two

pytestmark = pytest.mark.usefixtures("_inline")
HX = {"HX-Request": "true"}
LISTINGS = ("/state/versions?limit=150", "/state-history?body=1&per_page=500", "/api/history?per_page=0")


@pytest.fixture
def _inline():
    old = hub._PROJECTOR.inline
    hub.set_inline(True)
    hub_lanes.clear_caches()
    yield
    hub.set_inline(old)


def text(html: str) -> str:
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&#39;", "'").strip()


def notes(html: str) -> dict[str, str]:
    return {code: text(body) for code, body in
            re.findall(r'<p class="vh-note[^"]*" data-note="([^"]+)"[^>]*>(.*?)</p>', html, flags=re.S)}


def get(c, url) -> str:
    return c.get(url, headers=HX).get_data(as_text=True)


@pytest.fixture
def b_view(tmp_path):
    """Folder B of a chip whose other folder (A) has a data folder B is not
    linked to: B's listings hold A's runs (unlinked) and A's later write
    (another folder's), labelled."""
    env = _two(tmp_path, b_data=False)
    _load(env["client"], env["b"])
    return env


# ----------------------------------------------------------------------
# 1. the list note and the list agree
# ----------------------------------------------------------------------

def entries(html: str, sep: str) -> list[str]:
    """The rows of a listing: split on the row's own opening tag (``sh-entry``
    rows hold ``sh-entry-main`` / ``-actions`` children)."""
    return re.split(re.escape(sep) + r'(?=[ "])', html)[1:]


SH, SV, HP = '<div class="sh-entry', '<li class="state-version-row', '<div class="history-entry'
FOREIGN = r"not this folder(?:'|&#39;)s"


def _sh_rows(c) -> list[str]:
    return entries(get(c, "/state-history?body=1&per_page=500"), SH)


def test_a_listing_says_its_foreign_rows_are_listed_below(b_view):
    c = b_view["client"]
    rows = _sh_rows(c)
    unlinked = [r for r in rows if "data folder not linked" in r]
    other = [r for r in rows if ">from labA/quam_state<" in r and "while this folder had its own history" in r]
    assert unlinked and other, "fixture: B lists A's runs and A's later write"
    for url in LISTINGS:
        html = get(c, url)
        n = notes(html)
        assert "listed below, labelled" in n["unlinked_roots"], (url, n["unlinked_roots"])
        assert "not part of this folder's timeline" not in text(html), url
        assert re.match(rf"{len(unlinked)} runs? of a data folder", n["unlinked_roots"]), \
            (url, n["unlinked_roots"], len(unlinked))
        assert "listed below, labelled" in n["other_folders"] and \
            n["other_folders"].startswith(f"{len(other)} version"), (url, n["other_folders"], len(other))
        # no "no runs" over the runs drawn below it
        assert "no runs" not in n["no_folder_linked"] and "belong to a data folder not linked" in \
            n["no_folder_linked"], n["no_folder_linked"]
        # every such row says on itself that it is not this folder's, and why
        tags = re.findall(r'<span class="snap-foreign[^"]*" title="([^"]+)">' + FOREIGN + '</span>', html)
        foreign_rows = html.count("data folder not linked</span>") + html.count(
            "while this folder had its own history\">from")
        assert tags and len(tags) == foreign_rows, (url, len(tags), foreign_rows)
        assert all("Listed so the chip" in t and "not part of this folder" in t for t in tags), tags[:2]
    # a VALUE surface keeps its wording: it leaves those runs out
    drawer = get(c, "/field/history?path=qubits.qA1.T1")
    assert "not part of this folder's timeline" in text(drawer)
    assert "listed below" not in text(drawer)


def test_a_listing_says_another_chips_run_is_not_listed(tmp_path):
    from tests.test_hub_other_chip import _open
    c = _open(tmp_path, [(31, 7.1e9, "alpha", "010000"), (32, 7.2e9, "beta", "020000")])
    for url in LISTINGS:
        n = notes(get(c, url))
        assert n["other_chip"] == "1 run whose saved chip identity does not match this chip's is not listed: #32 in data.", \
            (url, n)


# ----------------------------------------------------------------------
# 2. one count, read through every surface
# ----------------------------------------------------------------------

def _num(s: str) -> int:
    return int(s.replace(",", ""))


def _counts(c) -> dict[str, int]:
    out = {}
    chip = get(c, "/state/version")
    out["badge"] = _num(re.search(r'<span class="state-version-count">([\d,]+)</span>', chip).group(1))
    page = get(c, "/topology")
    out["button"] = _num(re.search(r'History \(<span id="history-count">([\d,]+)</span>\)', page).group(1))
    drawer = get(c, "/api/history")
    out["drawer"] = _num(re.search(r'class="version-count[^"]*" data-total="\d+">([\d,]+) versions?<',
                                   drawer).group(1))
    out["drawer_oob"] = _num(re.search(r'<span id="history-count" hx-swap-oob="true">([\d,]+)</span>',
                                       drawer).group(1))
    versions = get(c, "/state/versions")
    out["versions_head"] = _num(re.search(r'class="muted sv-head-count">\d+ of ([\d,]+)<', versions).group(1))
    out["versions_foot"] = _num(re.search(r'sv-kept-note">.*?data-total="\d+">([\d,]+) versions?<', versions,
                                          flags=re.S).group(1))
    sh = get(c, "/state-history?body=1")
    out["state_history"] = _num(re.search(r'class="version-count[^"]*" data-total="\d+">([\d,]+) versions?<',
                                          sh).group(1))
    return out


def test_every_history_surface_shows_the_one_count(b_view):
    c = b_view["client"]
    with b_view["app"].app_context():
        ctx = routes._active_ctx()
        chip = Path(ctx["hub_chip_dir"])
        # an older row too: a run's capture no ledger event holds (the walk's
        # chip had one; the drawer counted it, State History split it off)
        routes._history().check_and_snapshot(ctx["path"], "experiment", experiment_name="ghost",
                                             run_id=999, kind="exp", force=True)
        hub_versions.hashes_for(chip).wait(10)
    rows = _sh_rows(c)
    # the truth, read twice: the rows State History lists, and the ledger itself
    con = sqlite3.connect(f"file:{chip / 'ledger.sqlite'}?mode=ro", uri=True)
    try:
        events = con.execute("SELECT COUNT(*) FROM events WHERE error IS NULL AND (flags & 4) = 0 AND "
                             "((kind IN ('sm_apply','undo','redo','restore','agent','autofit') AND "
                             "status='landed') OR (kind IN ('run','observed') AND state_hash IS NOT NULL))"
                             ).fetchone()[0]
    finally:
        con.close()
    older = sum(1 for r in rows if "sh-older" in r.split(">", 1)[0])
    assert older >= 1, "fixture: an older snapshot row"
    assert len(rows) == events + older, (len(rows), events, older)
    counts = _counts(c)
    assert set(counts.values()) == {len(rows)}, (counts, len(rows))
    sh = text(get(c, "/state-history?body=1"))
    foreign = sum(1 for r in rows if re.search(FOREIGN + "</span>", r))
    assert foreign and f"({foreign} not this folder's · {older} older snapshot" in sh, sh[:300]


def test_no_count_while_the_ledger_catches_up(b_view, monkeypatch):
    c = b_view["client"]
    monkeypatch.setattr(hub_sync, "status", lambda _d: {"state": "building", "done": 1, "total": 3})
    chip = get(c, "/state/version")
    assert "state-version-count" in chip and "&hellip;" in chip
    assert not re.search(r'<span class="state-version-count">[\d,]+</span>', chip), chip
    first = re.search(r'hx-get="/state/version\?wait=(\d+)"\s+hx-trigger="load delay:(\d+)ms"', chip)
    assert first and first.groups() == ("1", "1500"), chip
    later = re.search(r'hx-trigger="load delay:(\d+)ms"', get(c, "/state/version?wait=3")).group(1)
    assert int(later) > 1500, "the re-ask backs off"
    assert 'History (<span id="history-count">&hellip;</span>)' in get(c, "/topology")
    monkeypatch.undo()
    chip = get(c, "/state/version")
    assert re.search(r'<span class="state-version-count">[\d,]+</span>', chip) and "state-version-reask" not in chip


# ----------------------------------------------------------------------
# 3. State History paging and subtitle
# ----------------------------------------------------------------------

def test_state_history_pages_first_prev_next_last_and_jumps(b_view):
    c = b_view["client"]
    n = len(_sh_rows(c))
    assert n >= 3
    one = get(c, "/state-history?per_page=1")
    assert f"Page 1 / {n}" in text(one)
    assert re.search(r'title="First page \(newest\)" disabled>', one)
    assert re.search(r'title="Previous page \(newer\)" disabled>', one)
    assert re.search(rf'hx-get="/state-history\?per_page=1&amp;page={n}"[^>]*>\s*$|'
                     rf'hx-get="/state-history\?per_page=1&amp;page={n}"', one)
    assert re.search(rf'<input type="number" name="page" min="1" max="{n}" value="1"', one)
    assert 'class="history-page-jump" hx-get="/state-history"' in one
    past = get(c, f"/state-history?per_page=1&page={n + 5}")
    assert f"Page {n} / {n}" in text(past) and 'class="sh-entry' in past, "past the end: the last page"
    assert re.search(r'title="Last page \(oldest\)" disabled>', past)
    full = c.get("/state-history").get_data(as_text=True)
    assert "every recorded state of the chip" in full and "wiring.json snapshots" not in full
    drawer = get(c, "/api/history?per_page=1&page=999")
    assert f"Page {n} / {n}" in text(drawer) and "hp-ledger-row" in drawer


# ----------------------------------------------------------------------
# 4. plain words
# ----------------------------------------------------------------------

def test_versions_rows_do_not_print_their_id_and_tooltips_speak_plainly(b_view):
    c = b_view["client"]
    html = get(c, "/state/versions?limit=150")
    rows = entries(html, SV)
    assert rows
    for r in rows:
        assert "_event-" not in text(r.split("</li>")[0]), text(r)[:200]
        assert re.search(r'class="sv-check" value="\d{8}_\d{6}_\d{6}_event-\d+"', r) or "older snapshot" in r
    sh = get(c, "/state-history?body=1")
    assert "Mode 1" not in sh and "Mode 2" not in sh
    assert "the live chip is untouched until you apply" in sh and "can be undone" in sh


# ----------------------------------------------------------------------
# 5. one manual "Try again" wherever the change history could not be read
# ----------------------------------------------------------------------

@pytest.mark.parametrize("url,target", [("/state/versions", "#state-version-panel"),
                                        ("/state-history?body=1", "#state-history-body"),
                                        ("/api/history", "#history-content")])
def test_every_listing_offers_one_manual_try_again(b_view, monkeypatch, url, target):
    def broken(*a, **k):
        raise ValueError("corrupt ledger")
    monkeypatch.setattr(hub_versions, "_version_token", broken)
    html = get(b_view["client"], url)
    assert "The change history file could not be read." in text(html) and "(unreadable)" not in html
    again = re.findall(r'<button type="button" class="btn-sm vh-try-again" hx-get="([^"]+)"\s+'
                       r'hx-target="([^"]+)" hx-swap="innerHTML">Try again</button>', html)
    assert len(again) == 1 and again[0][0].split("?")[0] == url.split("?")[0] and again[0][1] == target, again
    assert "load delay:" not in html and "data-vh-retry=" not in html, "never an automatic re-ask"


def test_every_value_surface_offers_one_manual_try_again(b_view, monkeypatch):
    c = b_view["client"]

    def broken(*a, **k):
        raise ValueError("corrupt ledger")
    monkeypatch.setattr(value_history, "read", broken)
    drawer = get(c, "/field/history?path=qubits.qA1.T1")
    column = c.post("/bulk/column-history", data={"paths": json.dumps({"qA1": "qubits.qA1.T1"}),
                                                  "label": "T1"}).get_data(as_text=True)
    expand = get(c, "/param-history/expand?qubit=qA1&prop=T1")
    sparks = get(c, "/api/topology/sparklines/qA1")
    for name, html in (("drawer", drawer), ("column", column), ("sparklines", sparks)):
        assert 'data-vh-mode="unavailable"' in html, name
        assert html.count('data-vh-try-again="1">Try again</button>') == 1, name
        assert "data-vh-retry=" not in html, name
    assert re.search(r"""onclick='paramHistoryOpenDrawer\("qA1", "T1"\)'>Try again</button>""", expand), \
        expand[-600:]


# ----------------------------------------------------------------------
# 6. the live-change tracker's baseline
# ----------------------------------------------------------------------

@pytest.fixture
def live_chip(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    write_chip(live, chip_state(), None)
    app = make_app(tmp_path)
    c = app.test_client()
    _load(c, live)
    c.get("/state/drift")
    return {"app": app, "client": c, "live": live}


def _baseline_rows(html: str, sep: str) -> list[int]:
    return [i for i, r in enumerate(entries(html, sep)) if 'class="snap-baseline' in r]


def test_the_baseline_is_a_badge_never_a_pin(live_chip):
    c, live = live_chip["client"], live_chip["live"]
    assert c.post("/state-history/snapshot").status_code == 200
    assert c.post("/state/baseline/reset").get_json()["ok"]
    with live_chip["app"].app_context():
        snaps = routes._history().list_snapshots(live)
    assert snaps and not any(s.label == LIVE_BASELINE_LABEL or s.pinned for s in snaps), \
        [(s.label, s.pinned) for s in snaps]
    sh = get(c, "/state-history?body=1")
    assert len(_baseline_rows(sh, SH)) == 1, "one badge"
    assert "Unpin" not in sh and LIVE_BASELINE_LABEL not in sh
    assert "baseline of the live-change tracker" in sh
    assert len(_baseline_rows(get(c, "/state/versions"), SV)) == 1
    assert len(_baseline_rows(get(c, "/api/history"), HP)) == 1


def test_the_baseline_badge_sits_on_the_row_its_time_names(live_chip):
    """A -> apply B -> apply A again: two rows hold A. The tracker's baseline is
    the second A (set by that apply); the badge sits on it, and its time is
    the banner's -- not on the older row that happens to hold equal content."""
    c = live_chip["client"]
    assert c.post("/state-history/snapshot").status_code == 200          # a row holding A
    _edit(c, "qubits.qA1.T1", "4e-05")
    _apply(c)                                                            # B
    _edit(c, "qubits.qA1.T1", "1e-05")
    _apply(c)                                                            # A again: the baseline
    sh = get(c, "/state-history?body=1")
    rows = entries(sh, SH)
    from quam_state_manager.core.working_copy import content_hash
    with live_chip["app"].app_context():
        ctx = routes._active_ctx()
        with ctx["store"]._lock:
            a = content_hash(ctx["store"].state, ctx["store"].wiring)
        refs = [ref for _t, ref in hub_versions.versions_with_content(ctx["hub_chip_dir"], a)]
    holding_a = [i for i, r in enumerate(rows) if any(f'value="{ref}"' in r for ref in refs)]
    assert len(holding_a) >= 2 and holding_a[0] == 0, ("fixture: two rows hold A", holding_a)
    assert _baseline_rows(sh, SH) == [0], "the newest A, set by the last apply"
    row_utc = re.search(r'data-utc="([^"]+)"', rows[0]).group(1)
    banner = get(c, "/state/drift/view?embed=1")
    base_utc = re.search(r'baseline: <span class="ts-local" data-utc="([^"]+)"', banner).group(1)
    from quam_state_manager.core.timefmt import to_utc
    assert abs((to_utc(row_utc) - to_utc(base_utc)).total_seconds()) <= 2, (row_utc, base_utc)


def test_an_old_marker_is_never_a_users_pin(live_chip):
    c, live = live_chip["client"], live_chip["live"]
    with live_chip["app"].app_context():
        hm = routes._history()
        meta = hm.check_and_snapshot(live, "manual", force=True)
        other = hm.check_and_snapshot(live, "manual", force=True)
        for m in (meta, other):                      # what an earlier version wrote
            hm.annotate_snapshot(live, m.timestamp, label=LIVE_BASELINE_LABEL, pinned=True)
    sh = get(c, "/state-history?body=1")
    assert LIVE_BASELINE_LABEL not in sh and "Unpin" not in sh and "&#128204;" not in sh
    # the user pins one: it is theirs now (SM's label goes with the press)
    r = c.post(f"/state-history/{meta.timestamp}/label?body=1", data={"pinned": "1"}, headers=HX)
    assert r.status_code == 200
    with live_chip["app"].app_context():
        hm = routes._history()
        got = {m.timestamp: (m.label, m.pinned) for m in hm.list_snapshots(live)}
        assert got[meta.timestamp] == (None, True)
        hm.set_live_baseline(live, chip_state(), json.loads((live / "wiring.json").read_text()))
        got = {m.timestamp: (m.label, m.pinned) for m in hm.list_snapshots(live)}
    assert got[meta.timestamp] == (None, True), "the user's pin survives the marker release"
    assert got[other.timestamp] == (None, False), "SM's own old marker is released"


# ----------------------------------------------------------------------
# 7. Take snapshot when nothing changed
# ----------------------------------------------------------------------

def test_a_snapshot_that_adds_no_row_says_so(live_chip):
    c, live = live_chip["client"], live_chip["live"]
    c.post("/state-history/snapshot")
    msg = "No change since the last recorded state — nothing new to list."
    again = c.post("/state-history/snapshot", headers=HX).get_data(as_text=True)
    assert msg in again
    drawer = c.post("/api/history/snapshot", headers=HX).get_data(as_text=True)
    assert msg in drawer
    write_chip(live, chip_state(t1=7e-5), None)                          # a change outside SM
    changed = c.post("/state-history/snapshot", headers=HX).get_data(as_text=True)
    assert msg not in changed


# ----------------------------------------------------------------------
# 8. a run's bookmarked capture rides the run's row (final review P1)
# ----------------------------------------------------------------------

def test_a_bookmark_on_a_runs_capture_rides_the_run_row(tmp_path):
    data, live = tmp_path / "data", tmp_path / "chips" / "live"

    def st(t1):
        s = chip_state(t1=t1)
        s["extras"]["data_folder"] = str(data)
        return s
    run(data, 1, st(1.0e-5))
    run(data, 2, st(3.0e-5), patches=[patch("qubits.qA1.T1", 3.0e-5, 1.0e-5)])
    run(data, 3, st(4.0e-5), patches=[patch("qubits.qA1.T1", 4.0e-5, 3.0e-5)])
    write_chip(live, st(3.0e-5), None)
    app = make_app(tmp_path)
    c = app.test_client()
    _load(c, live)
    with app.app_context():
        hm = routes._history()
        m1 = hm.check_and_snapshot(live, "experiment", experiment_name="scan", run_id=2,
                                   experiment_folder_path=str(next(data.rglob("#2_*"))),
                                   kind="exp", force=True)
        hm.annotate_snapshot(live, m1.timestamp, label="GOOD-AFTER-RUN-2", pinned=True)
        hub_versions.hashes_for(Path(routes._active_ctx()["hub_chip_dir"])).wait(10)
    _load(c, live)
    for url in LISTINGS:
        html = get(c, url)
        assert "GOOD-AFTER-RUN-2" in html, url
        sep = {"/state/versions": SV, "/state-history": SH, "/api/history": HP}[url.split("?")[0]]
        (row,) = [r for r in entries(html, sep) if "GOOD-AFTER-RUN-2" in r]
        assert "run #2 scan" in text(row), (url, text(row)[:200])
    sh = get(c, "/state-history?body=1&per_page=500")
    (row,) = [r for r in entries(sh, SH) if "GOOD-AFTER-RUN-2" in r]
    assert f"/state-history/{m1.timestamp}/label?" in row and ">Unpin<" in row, "it can be unpinned"


# ----------------------------------------------------------------------
# 9. layout: the drawer's per-page picker, and a Load that does not jump
# ----------------------------------------------------------------------

def test_load_as_working_state_answers_in_the_status_corner(b_view):
    """The result used to land in the detail pane below the whole list, and
    the page scrolled to its bottom; it answers in the fixed status corner
    (as the Versions panel's Stage does), and its gate waits there with an x."""
    c = b_view["client"]
    sh = get(c, "/state-history?body=1")
    loads = re.findall(r'<button class="outline btn-sm sh-stage"\s+hx-post="([^"]+)"\s+'
                       r'hx-target="([^"]+)"', sh)
    assert loads and all("target=status" in u and t == "#status-bar" for u, t in loads), loads[:2]
    _edit(c, "qubits.qA1.T1", "7e-05")                       # unsaved edits: the gate answers
    url = loads[0][0].replace("&amp;", "&")
    gate = c.post(url, headers=HX)
    body = gate.get_data(as_text=True)
    assert gate.status_code == 409 and 'hx-target="#status-bar"' in body and 'class="toast-x"' in body
    base = (Path(routes.__file__).parent / "templates" / "base.html").read_text(encoding="utf-8")
    fade = base[base.index('if (tid === "status-bar") {'):][:900]
    assert 'classList.contains("sh-confirm")' in fade, "a gate in the status corner is never faded away"


def test_the_drawer_per_page_picker_is_sized_to_its_options():
    css = (Path(routes.__file__).parent / "static" / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.history-toolbar select \{([^}]*)\}", css)
    assert rule and re.search(r"(^|;|\s)width: auto", rule.group(1)) and "margin: 0 0 0 auto" in rule.group(1)
