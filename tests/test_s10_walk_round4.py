"""S10 walk, round 4 (the v1.2.0 candidate walked in a real browser again) --
each pin mutation-checked.

P2-4. an apply taken back by Revert last apply is marked "reverted later" in
      EVERY listing of the ledger's states: Versions, State History and the
      Chip Status History drawer (one renderer, ``version_flags``);
P2-1. the Link dialog counts a run with no readable saved state as unreadable
      (the panel's rule, ``hub_lanes``: an error event, or no state);
P2-2. a run card's "could not be read" path names the folder the run is
      under NOW (the N5 rule), never the folder the ledger first read it in;
P2-3. a pointer's excursion / not-kept list shows the VALUE in force through
      the pointer, with the pulse it named as context.
(P1 / P2-10, the wording of the two lists of saved values that are not chip
history, are pinned in tests/test_hub_witness.py.)
"""

from __future__ import annotations

import re
import time

import pytest

from quam_state_manager.core import hub, hub_lanes
from tests.test_hub_drawer import chip_state, make_app, write_chip
from tests.test_hub_folder_view import _apply, _edit, _load

HX = {"HX-Request": "true"}


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


@pytest.fixture
def live_chip(tmp_path, _inline):
    live = tmp_path / "chip" / "quam_state"
    write_chip(live, chip_state(), None)
    app = make_app(tmp_path)
    c = app.test_client()
    _load(c, live)
    c.get("/state/drift")
    return {"app": app, "client": c, "live": live}


# ----------------------------------------------------------------------
# P2-4. "reverted later" in every listing
# ----------------------------------------------------------------------

def _revert_last_apply(env):
    from quam_state_manager.web import routes
    c = env["client"]
    c.post("/state-history/snapshot", headers=HX)        # the state the apply starts from
    time.sleep(1.1)
    _edit(c, "qubits.qA1.T1", "4e-05")
    _apply(c)
    with env["app"].app_context():
        last = dict(routes._active_ctx().get("last_apply") or {})
    assert last.get("pre_ts")
    c.post(f"/state-history/{last['pre_ts']}/stage?from=tray", headers=HX)
    _apply(c)


def _rows(html: str, sep: str) -> list[str]:
    return [text(r) for r in re.split(re.escape(sep) + r'(?=[ "])', html)[1:]]


def test_every_listing_marks_the_reverted_apply(live_chip):
    _revert_last_apply(live_chip)
    c = live_chip["client"]
    listings = {
        "State History": _rows(c.get("/state-history?body=1", headers=HX).get_data(as_text=True),
                               '<div class="sh-entry'),
        "Versions": _rows(c.get("/state/versions", headers=HX).get_data(as_text=True),
                          '<li class="state-version-row'),
        # the Chip Status History drawer (walk round 4: it dropped the flag)
        "History drawer": _rows(c.get("/api/history", headers=HX).get_data(as_text=True),
                                '<div class="history-entry'),
    }
    for name, rows in listings.items():
        applied = [r for r in rows if "Applied to the chip" in r]
        assert applied, (name, [r[:100] for r in rows[:4]])
        assert "reverted later" in applied[0], (name, applied[0][:200])
        reverting = [r for r in rows if "Reverted the last apply" in r]
        assert reverting and "reverted later" not in reverting[0], name


# ----------------------------------------------------------------------
# P2-1 / P2-2: a moved data folder with a run of no readable saved state
# ----------------------------------------------------------------------

@pytest.fixture
def moved_folder(tmp_path):
    """The walk's chip: the ledger read the runs in the data folder they were
    first saved in, then in the folder they moved to; run #33 has no readable
    saved state. Both folders are Datasets roots (the dialog lists those)."""
    import os
    import shutil

    from quam_state_manager.web.app import create_app
    from tests.ledger_fixture import declare_root
    from tests.test_hub_other_chip import _seed_run, _state, _write_chip
    old = tmp_path / "old" / "Data_Root"
    _seed_run(old, 31, _state(7.1e9, "alpha"), "010000")
    _seed_run(old, 32, _state(7.2e9, "alpha"), "020000")
    _seed_run(old, 33, _state(7.3e9, "alpha"), "003000")
    gone = next(old.glob("2026-07-29/#33_*"))
    shutil.rmtree(gone / "quam_state")                           # no readable saved state
    for p in (gone / "node.json", gone / "data.json", gone):
        os.utime(p, (time.time() - 86_400, time.time() - 86_400))
    live = tmp_path / "chips" / "a"
    _write_chip(live, _state(7.2e9, "alpha"))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    c.post("/workspace/add", data={"folder": str(old)})
    declare_root(c, old)
    moved = tmp_path / "moved" / "Data_Root"
    shutil.copytree(old, moved)
    c.post("/workspace/add", data={"folder": str(moved)})
    declare_root(c, moved)                                      # the runs read again, from where they are now
    return {"client": c, "old": old, "moved": moved, "tmp": tmp_path}


def test_the_link_dialog_counts_the_unreadable_run_as_the_panel_does(moved_folder):
    from tests.test_s10_walk_left_out_notes import _copy_open, _text
    c, moved = moved_folder["client"], moved_folder["moved"]
    _copy_open(moved_folder["tmp"], c, moved)
    panel = _text(c.get("/state/versions", headers=HX).get_data(as_text=True))
    assert "1 more run of it has no readable saved state." in panel, panel
    html = c.get("/hub/link-folder").get_data(as_text=True)
    from quam_state_manager.web import routes
    row = html.split(f'data-root="{routes._hub_root_key(moved)}"', 1)[1].split('class="hub-folder-row"')[0]
    # walk round 4: "2 runs match this chip, 0 belong to other chips, 0 unreadable" beside
    # the panel's "1 more run of it has no readable saved state" -- one rule now
    assert "2 runs match this chip, 0 belong to other chips, 1 unreadable" in _text(row), _text(row)


def test_a_run_card_names_the_file_where_the_run_is_now(moved_folder):
    import os
    c, old, moved = moved_folder["client"], moved_folder["old"], moved_folder["moved"]
    page = c.get("/journal/day?day=2026-07-29").get_data(as_text=True)
    notes = [text(m) for m in re.findall(r"Its saved state could not be read:.*?(?:</|$)", page, re.S)]
    assert notes, text(page)[:600]
    want = os.path.join(str(moved.resolve()), "2026-07-29")
    assert any(want.lower() in n.lower() and "#33_08_qubit_spectroscopy_003000" in n for n in notes), notes
    assert not any(str(old.resolve()).lower() in n.lower() for n in notes), \
        "the card names the folder the runs left (walk round 4)"


# ----------------------------------------------------------------------
# P2-3: a pointer's save, shown as the value it meant
# ----------------------------------------------------------------------

def test_a_pointer_not_kept_reads_as_the_value_in_force(tmp_path, _inline):
    """#2 points qA1's x180 at Gauss; #3, which did not measure qA1, still had
    DragCosine: the amplitude drawer lists #2's save as the amplitude it meant
    (Gauss's 0.3), with the pulse as context -- never "#./x180_Gauss"."""
    from tests.test_hub_witness import run_folder
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    for rid, (st, targets) in enumerate([
            (chip_state(alias="#./x180_DragCosine", amp=0.1), ["qA1", "qA2"]),
            (chip_state(alias="#./x180_Gauss", amp=0.1), ["qA1"]),
            (chip_state(alias="#./x180_DragCosine", amp=0.1, t1=2e-5), ["qA2"])], start=1):
        run_folder(data, rid, st, targets)
    write_chip(live, chip_state(alias="#./x180_DragCosine", amp=0.1, t1=2e-5), data)
    app = make_app(tmp_path)
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    html = c.get("/field/history", query_string={"path": "qubits.qA1.xy.operations.x180.amplitude"}).get_data(as_text=True)
    assert '<details class="vh-not-kept">' in html, text(html)[:500]
    listed = text(html.split('<details class="vh-not-kept">', 1)[1].split("</details>", 1)[0])
    assert ("Saved by run #2 scan: 0.3 via x180_Gauss \u2014 not in the next recorded state "
            "(#3, which did not measure qA1, still had 0.1 via x180_DragCosine)") in listed.replace("&mdash;", "\u2014"), listed
    assert "#./" not in listed


def test_the_hero_trend_percent_stays_on_one_line():
    # P2-6 in the browser (round 4): the one percent rule's tiny form ("+7.5e-07%")
    # wrapped after its "e-" in the hero popup's 4.6em delta column
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    delta = re.search(r"\.topo-spark-delta \{([^}]*)\}", css).group(1)
    assert "white-space: nowrap" in delta
    cols = re.search(r"\.topo-spark-section \.topo-spark-row \{[^}]*grid-template-columns: ([^;]*);", css).group(1)
    assert float(cols.split()[1].rstrip("em")) >= 5.8, cols    # 11 monospace characters at 0.82em
