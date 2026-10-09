"""S10 walk: what a folder's view leaves out is said once, by name, as spelled.

* The other-chip note ("1 run whose saved chip identity does not match this
  chip's ...") never named the run: it now names the runs (run id + folder
  label, newest first, a few + "and N more"), and a run whose saved state
  could not be read says so.
* The note is said once per surface, never inside every Chip Status tile's
  hover card (pinned in tests/hub_chip_status_selfcheck.cjs); here: once per
  server surface.
* The link dialog counted such a run as matching the chip ("0 belong to other
  chips") while the note said it had another chip's identity: the dialog now
  counts a run the ledger flags CHIP_UNCERTAIN as another chip's.
* The unlinked-folder note printed the data root lowercased (the ledger's
  key); it prints the root as its runs were read. In the redacted report the
  ")" after the hidden path is kept.
"""

from __future__ import annotations

import os
import re
import time
import shutil
from html import unescape
from pathlib import Path

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app
from tests.ledger_fixture import declare_root
from tests.test_hub_other_chip import _open, _seed_run, _state, _write_chip

OTHER = "whose saved chip identity does not match this chip"


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def _drawer(c) -> str:
    return _text(c.get("/field/history?path=qubits.qA1.f_01").get_data(as_text=True))


def test_the_note_names_the_run(tmp_path):
    c = _open(tmp_path, [(31, 7.1e9, "alpha", "010000"), (32, 7.2e9, "beta", "020000")])
    assert ("1 run whose saved chip identity does not match this chip's is not part of this "
            "chip's timeline: #32 in data.") in _drawer(c)


def test_many_runs_name_the_newest_few_and_count_the_rest(tmp_path):
    runs = [(31, 7.1e9, "alpha", "010000")] + [(rid, 7.2e9, "beta", f"0{rid - 30}0000")
                                                for rid in range(32, 37)]
    c = _open(tmp_path, runs)
    assert ("5 runs whose saved chip identity does not match this chip's are not part of this "
            "chip's timeline: #36 in data, #35 in data, #34 in data and 2 more.") in _drawer(c)


def test_a_run_with_no_readable_saved_state_says_so(tmp_path):
    root = tmp_path / "data"
    _seed_run(root, 33, _state(7.3e9, "alpha"), "005000")       # older than #31: not in flight
    folder = next(root.glob("2026-07-29/#33_*"))
    shutil.rmtree(folder / "quam_state")
    for p in (folder / "node.json", folder / "data.json", folder):      # long finished, not in flight
        os.utime(p, (time.time() - 86_400, time.time() - 86_400))
    c = _open(tmp_path, [(31, 7.1e9, "alpha", "010000")])
    assert "#33 in data (saved state unreadable)." in _drawer(c)


def test_the_note_is_said_once_per_surface(tmp_path):
    c = _open(tmp_path, [(31, 7.1e9, "alpha", "010000"), (32, 7.2e9, "beta", "020000")])
    for url in ("/field/history?path=qubits.qA1.f_01", "/state/versions", "/topology/trends?metrics=f_01",
                "/param-history?since=all&props=f_01"):
        body = c.get(url, headers={"HX-Request": "true"}).get_data(as_text=True)
        assert body.count(OTHER) == 1, (url, body.count(OTHER))
    notes = c.get("/topology/metric-meta").get_json()["notes"]
    assert sum(OTHER in n for n in notes) == 1, notes


def _copy_open(tmp_path, c, root):
    """A second folder of the same chip, the data root not linked to it."""
    copy = tmp_path / "chips" / "Copy_B"
    _write_chip(copy, _state(7.1e9, "alpha"))
    assert c.post("/load", data={"folder": str(copy)}).status_code in (200, 302)
    app = c.application
    with app.app_context():
        jobs = routes._alignment_jobs()
        jobs.request(routes._history(), copy, app.config["workspace"], wait_s=10)
        jobs.join()
    return copy


def test_the_link_dialog_counts_another_chips_run_as_the_note_does(tmp_path):
    c = _open(tmp_path, [(31, 7.1e9, "alpha", "010000"), (32, 7.2e9, "beta", "020000")])
    _copy_open(tmp_path, c, tmp_path / "data")
    html = c.get("/hub/link-folder").get_data(as_text=True)
    assert "1 runs match this chip, 1 belong to other chips, 0 unreadable" in html, _text(html)


def test_the_unlinked_note_prints_the_root_as_spelled(tmp_path):
    root = tmp_path / "Data_Root"
    _seed_run(root, 31, _state(7.1e9, "alpha"), "010000")
    live = tmp_path / "chips" / "a"
    _write_chip(live, _state(7.1e9, "alpha"))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    c.post("/workspace/add", data={"folder": str(root)})
    declare_root(c, root)
    _copy_open(tmp_path, c, root)
    spelled = str(root.resolve())
    assert spelled != spelled.lower(), "(the fixture's root has upper-case letters)"
    body = _drawer(c)
    assert f"1 run of a data folder not linked to this folder ({spelled}) is not part" in body, body
    assert spelled.lower() not in body
    report = c.get("/chip-status/report/section/trends?redact=1").get_data(as_text=True)
    assert "of a data folder not linked to this folder ([hidden]) is not part" in _text(report), _text(report)[:800]
