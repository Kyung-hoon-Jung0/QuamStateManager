"""R3 MINOR 10d: the instance record prefers the declared chip identity."""
from __future__ import annotations

import json
import os

import pytest

from quam_state_manager.core import instances
from tests.test_v151_instance_record import _app, _chip, _record


@pytest.mark.parametrize("name,expected", [("  device-key  ", "device-key"), ("", "chip"), (17, "chip")])
def test_the_record_and_peer_json_prefer_a_valid_declared_name(tmp_path, name, expected):
    chip = _chip(tmp_path / "chip")
    p = chip / "state.json"
    state = json.loads(p.read_text(encoding="utf-8"))
    state["extras"] = {"chip_name": name}
    p.write_text(json.dumps(state), encoding="utf-8")
    inst = tmp_path / "inst"
    app, c = _app(inst)
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    assert _record(inst)["chip_name"] == expected
    peer = next(p for p in instances.peers(inst, include_self=True) if p.pid == os.getpid())
    assert peer.to_dict()["chip_name"] == expected
    # Switching to a different context must publish its own name too.
    other = _chip(tmp_path / "other")
    c.post("/load", data={"folder": str(other)})
    assert _record(inst)["chip_name"] == "other"
    c.post("/load", data={"folder": str(chip)})
    assert _record(inst)["chip_name"] == expected
