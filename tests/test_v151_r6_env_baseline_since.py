"""v1.5.1 (r6, P2-12): the environment-schema line says when its baseline was
recorded, never "first baseline recorded ... from now on" on every render.

Diagnostics said "first baseline recorded (quam 0.6.0, quam_builder 0.4.0)
-- an upgrade can be compared against it from now on" on an instance whose
baseline index had held that environment since 2026-09-07:
``env_transition`` reported ``first`` whenever no OTHER environment was on
record. It is still the only one; the answer now carries ``since`` (when it
was first recorded) and the card and the overlay say that.
"""
import json
from pathlib import Path

import pytest

from quam_state_manager.core import state_env_baseline as seb
from quam_state_manager.core import state_env_schema as ses
from quam_state_manager.core import type_policy as tp
from quam_state_manager.web.app import create_app

_GOLDEN = Path(__file__).resolve().parent / "golden"
_SINCE = "2026-09-07T12:00:00+00:00"       # midday UTC: the same date in any zone


@pytest.fixture
def modern():
    path = _GOLDEN / "state_schema_modern.json"
    if not path.exists():
        pytest.skip("golden manifest missing")
    return ses._decorate(json.loads(path.read_text(encoding="utf-8")))


def _recorded_long_ago(inst, manifest):
    seb.record_baseline(inst, manifest)
    index_path = inst / seb.BASELINE_DIRNAME / seb.INDEX_FILENAME
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for e in index["entries"]:
        e["first_seen"] = _SINCE
    index_path.write_text(json.dumps(index), encoding="utf-8")


def _client(tmp_path, manifest, *, recorded):
    inst = tmp_path / "_inst"
    inst.mkdir()
    if recorded:
        _recorded_long_ago(inst, manifest)
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps({"qubits": {}}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(
        {"wiring": {}, "network": {"host": "127.0.0.1"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(inst))
    client = app.test_client()
    client.post("/load", data={"folder": str(chip)})
    ctx = app.config["contexts"][app.config["active_context"]]
    ctx["store"].type_policy = tp.load_policy(inst, chip, manifest)
    return client


def test_the_only_baseline_carries_when_it_was_recorded(tmp_path, modern):
    _recorded_long_ago(tmp_path, modern)
    t = seb.env_transition(tmp_path, modern)
    assert t["first"] is True and t["changed"] is False
    assert t.get("since") == _SINCE


def test_an_environment_not_on_record_has_no_date(tmp_path, modern):
    t = seb.env_transition(tmp_path, modern)
    assert t["first"] is True and t.get("since") == ""


def test_the_card_says_since_when_not_first_recorded(tmp_path, modern):
    html = _client(tmp_path, modern, recorded=True).get(
        "/diagnostics/types-card").get_data(as_text=True)
    assert "first baseline recorded" not in html and "from now on" not in html
    assert "baseline recorded 2026-09-07" in html
    assert "the only environment on record" in html


def test_the_card_never_claims_a_baseline_that_is_not_on_record(tmp_path, modern):
    html = _client(tmp_path, modern, recorded=False).get(
        "/diagnostics/types-card").get_data(as_text=True)
    assert "first baseline recorded" not in html
    assert "no baseline on record for this environment yet" in html


def test_the_overlay_says_since_when(tmp_path, modern):
    html = _client(tmp_path, modern, recorded=True).get(
        "/env-schema/changes").get_data(as_text=True)
    assert "This is the first environment schema" not in html
    assert "This is the only environment schema SM has recorded" in html
    assert "since 2026-09-07" in " ".join(html.split())
