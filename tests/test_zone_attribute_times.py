"""docs/301 (F5): times no script localizes read in the page's zone.

A State History confirm said "Load snapshot 2026-10-07 13:30:24 UTC" while the
list beside it read 22:30, and the Diff workbench's pickers and header showed
the bare UTC stamp with no zone at all -- the same version looked like two.
Attribute text, option labels and the stage message now name the instant in
the zone the page renders in (the project's, else this machine's), offset
included."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.core import project_time
from quam_state_manager.core.timefmt import local_text, to_utc
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

ZONE = "America/Los_Angeles"     # far from any machine this suite runs on


@pytest.fixture
def env(tmp_path):
    chip = tmp_path / "quam_state"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "T1": 1.0e-5}}, "active_qubit_names": ["q1"]}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps({"network": {}, "wiring": {"qubits": {}}}), encoding="utf-8")
    inst = tmp_path / "_i"
    app = create_app(testing=True, instance_path=str(inst))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    assert c.post("/state-history/snapshot").status_code == 200
    with app.app_context():
        ts = routes_mod._history().list_snapshots(chip)[0].timestamp
    return {"client": c, "app": app, "chip": chip, "inst": inst, "ts": ts}


def _set_zone(env):
    project_time.set_zone(env["inst"], "proj", ZONE)


def test_the_diff_picker_names_the_zone(env):
    _set_zone(env)
    html = env["client"].get("/diff").get_data(as_text=True)
    want = local_text(to_utc(env["ts"]), ZONE, seconds=False)
    opts = re.findall(r"<option value=\"hist:[^\"]*\"[^>]*>([^<]*)</option>", html)
    assert any(o.startswith(want + " · ") for o in opts), (want, opts[:4])


def test_the_diff_header_names_the_zone(env):
    _set_zone(env)
    c, ts = env["client"], env["ts"]
    with env["app"].app_context():
        chip_key = Path(routes_mod._history().resolve_chip_dir(env["chip"])[0]).name
    html = c.get(f"/diff?a=hist:{chip_key}/{ts}&b=working:{env['chip']}").get_data(as_text=True)
    want = local_text(to_utc(ts), ZONE)
    labels = re.search(r'<span class="diff-wb-labels muted">(.*?)</span>', html, re.S)
    assert labels and want in labels.group(1), (want, labels and labels.group(1)[:200])
    # the stored stamp's wall clock is not shown as if it were local
    utc_wall = to_utc(ts).strftime("%Y-%m-%d %H:%M:%S")
    assert utc_wall not in labels.group(1), labels.group(1)[:200]


def test_the_stage_confirm_and_message_name_the_zone(env):
    _set_zone(env)
    c, ts = env["client"], env["ts"]
    want = local_text(to_utc(ts), ZONE)
    page = c.get("/state-history").get_data(as_text=True)
    confirms = re.findall(r'hx-confirm="([^"]*)"', page)
    assert any(want in x for x in confirms), (want, confirms[:3])
    assert not any(" UTC?" in x or " UTC now" in x for x in confirms), confirms[:3]
    body = c.post(f"/state-history/{ts}/stage?force=1").get_data(as_text=True)
    assert want in body and "loaded as the working state" in body, body[:400]


def test_no_project_zone_falls_back_to_this_machine_with_the_offset(env):
    page = env["client"].get("/state-history").get_data(as_text=True)
    want = local_text(to_utc(env["ts"]))
    assert want in page and re.search(r"\(UTC([+-]\d{1,2}(:\d{2})?)?\)", want), want
