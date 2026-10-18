"""v1.5.1 T1-F4 + U P2-11: a chip is named by its declared name.

The top bar, the Param History chip pill, its alignment banner and the chip
report labelled the chip by its folder ("chip_x") or its history dir key
("quam_states") although ``extras.chip_name`` says what it is; and the banner
"20 are for other chips: <name> (20)" named a DATA FOLDER as a chip (the
grouping is by folder; the hardware fingerprint is what differs -- another
chip, or this one rewired). Now every display site reads ``_chip_label``
(declared name, else the folder rule) and the banner groups "By folder".
The chip gates and tokens keep the folder-derived name.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

NAME = "Device-A"


def _chip(d: Path, *, host="10.1.1.18", name=None) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    state = {"qubits": {q: {"id": q} for q in ("q0", "q1")}, "qubit_pairs": {}}
    if name:
        state["extras"] = {"chip_name": name}
    (d / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps({"network": {"host": host, "cluster_name": "A"}}),
                                   encoding="utf-8")
    return d


def _run(root: Path, rid: int, *, host: str) -> None:
    run = root / "2026-03-01" / f"#{rid}_04_power_rabi_1200{rid:02d}"
    _chip(run / "quam_state", host=host)
    (run / "node.json").write_text(json.dumps({
        "id": rid, "created_at": "2026-03-01T12:00:00+00:00",
        "metadata": {"name": "04_power_rabi", "status": "finished"},
        "data": {"parameters": {"model": {"qubits": ["q0", "q1"]}}}}), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    # the chip's history began before it was named (its dir key is the folder
    # rule's), then the lab declared extras.chip_name -- the real T1 case
    loaded = _chip(tmp_path / "lab" / "quam_states")
    mine, other = tmp_path / "data_mine", tmp_path / "data_rewired"
    for rid in (1, 2):
        _run(mine, rid, host="10.1.1.18")
    for rid in (3, 4, 5):
        _run(other, rid, host="10.9.9.9")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    app.config["ALIGNMENT_WAIT_S"] = 10
    c = app.test_client()
    assert c.post("/load", data={"folder": str(loaded)}).status_code in (200, 302)
    assert c.post("/api/history/snapshot").status_code < 400
    assert c.post("/chip-name/set", data={"name": NAME}).status_code == 200    # the banner's door
    for d in (mine, other):
        assert c.post("/workspace/add", data={"folder": str(d)}).status_code in (200, 302)
    return c


def _text(html: str) -> str:
    return re.sub(r"\s+([.,:])", r"\1", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))).strip()


def test_the_top_bar_names_the_declared_chip(env):
    tray = env.get("/state/tray").get_data(as_text=True)
    m = re.search(r'<span class="state-status-name" data-chip-folder="([^"]*)" title="([^"]*)">([^<]*)</span>', tray)
    assert m, tray
    folder, title, shown = m.groups()
    assert shown == NAME, shown
    assert folder == "lab" and "folder lab" in title, (folder, title)   # the folder rule, kept for the gates


def test_the_param_history_pill_and_banner_name_the_chip_not_the_dir_key(env):
    page = env.get("/param-history", headers={"HX-Request": "true"}).get_data(as_text=True)
    pill = re.search(r'class="phf-chip phf-chip-chip[^"]*"[^>]*>(.*?)</a>', page, re.S)
    assert pill, page
    key = re.search(r'data-loaded-chip-key="([^"]+)"', page).group(1)
    assert key != NAME
    assert _text(pill.group(1)) == chr(0x25cf) + ' ' + NAME + ' 1', _text(pill.group(1))
    assert key not in _text(pill.group(1)), "the history dir key is not the chip's name"


def test_the_alignment_banner_names_the_chip_and_groups_runs_by_folder(env):
    body = env.get("/param-history/alignment?summary_total=0",
                   headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "alignment-yellow" in body, body
    t = _text(body)
    assert f"2 of 5 experiments are for chip {NAME}." in t, t
    assert "are for other chips" not in t, "a data folder is not a chip"
    assert ("3 have a different hardware fingerprint (another chip, or this chip rewired). "
            "By folder: data_rewired (3)") in t, t


def test_the_chip_report_names_the_declared_chip(env):
    html = env.get("/chip-status/report").get_data(as_text=True)
    assert NAME in html
    assert not re.search(r"<title>[^<]*quam_states[^<]*</title>", html)
