"""v1.5.1 R3 MINOR 2: an older SM and this one no longer both write the plain
tray checkpoint while this one holds it.

R2 MINOR 3 kept a NEW process off the plain file while an older SM runs. But
a process that already held the plain file kept writing it after an older SM
started on the instance -- and the older SM writes it too, whoever holds it:
the last writer won, and each could restore the other's rows.

The best this side can do: while an older SM runs, this process keeps its
rows in its own file (and takes them out of the plain file when that still
holds exactly what it wrote there), never removes rows the older SM wrote,
and says so once. What the older SM does with the plain file stays out of
reach (it does not know the owner record) -- the note says to close one.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from quam_state_manager.core import pending_tray, safe_io
from tests.test_v151_r2_tray_identity import (  # noqa: F401
    _clear_memo, _main, _rows, bystander, chip)


def _edit(chip, path, value):
    r = chip["c"].post("/field/edit", data={"dot_path": path, "value": value})
    assert r.status_code == 200
    pending_tray.flush_all()


def _ctx(chip):
    app = chip["app"]
    return app.config["contexts"][app.config["active_context"]]


def _older_sm_starts(chip, bystander):
    reg = chip["inst"] / "instances"
    reg.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    safe_io.atomic_write_json(reg / f"{bystander.pid}.json", {
        "pid": bystander.pid, "port": 5999, "started_utc": now, "updated_utc": now,
        "chip_path": str(chip["live"]), "chip_fs_key": "", "chip_name": "",
        "project": "", "roots": []})                      # no tray_protocol: older
    _clear_memo()


def _peers(main):
    return sorted(main.parent.glob(main.stem + ".peer-*.json"))


def test_a_holder_moves_off_the_plain_file_when_an_older_sm_starts(chip, bystander):
    main = _main(chip)
    _edit(chip, "qubits.q1.T1", "1.5e-05")
    assert main.exists() and _rows(main) == ["qubits.q1.T1"], "this process holds the plain file"
    _older_sm_starts(chip, bystander)
    _edit(chip, "qubits.q2.T1", "2.5e-05")
    peers = _peers(main)
    assert [_rows(p) for p in peers] == [["qubits.q1.T1", "qubits.q2.T1"]], (
        f"this process still writes the plain file the older SM writes too: {peers}")
    assert not main.exists(), "its rows stay in the plain file, where the older SM restores them"
    note = (_ctx(chip).get("_tray_recovery_notice") or {}).get("text", "")
    assert f"PID {bystander.pid}" in note and "Close one of them" in note, note


def test_rows_the_older_sm_wrote_are_never_removed(chip, bystander):
    main = _main(chip)
    _edit(chip, "qubits.q1.T1", "1.5e-05")
    _older_sm_starts(chip, bystander)
    # the older SM writes the plain file (its own rows, no holder recorded)
    doc = json.loads(main.read_text(encoding="utf-8"))
    doc["entries"] = [dict(doc["entries"][0], dot_path="qubits.q4.T1", new_value=4.4e-05)]
    safe_io.atomic_write_json(main, doc)
    _edit(chip, "qubits.q2.T1", "2.5e-05")
    assert main.exists() and _rows(main) == ["qubits.q4.T1"], "the older SM's rows were removed"
    # the tray emptied: still not this process's to remove
    for _ in range(3):
        chip["c"].post("/undo", data={"ack_unseen": "1"})
    pending_tray.flush_all()
    assert not _ctx(chip)["store"].change_log
    assert main.exists() and _rows(main) == ["qubits.q4.T1"]
