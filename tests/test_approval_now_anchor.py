"""Approval presses consent to the values rendered on the card."""
import json

import pytest

from quam_state_manager.core import approvals
from quam_state_manager.web.app import create_app


@pytest.fixture
def approval_case(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps({"value": 10, "other": 20}), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    inst = tmp_path / "instance"
    app = create_app(testing=True, instance_path=str(inst))
    client = app.test_client()
    assert client.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    chip = client.get("/api/agent/chip").get_json()["chip_key"]
    record = approvals.add(str(inst), chip, kind="writes", node="synthetic", targets=[],
                           writes=[{"path": "value", "old": 1, "new": 30},
                                   {"path": "other", "old": 20, "new": 40}],
                           reason="test", why_held="mode ask-writes", actor="by_agent")
    return app, client, folder, inst, chip, record


def press(case, writes):
    _, client, _, _, _, record = case
    return client.post(f"/api/agent/approvals/{record['id']}/approve",
                       json={"writes": writes}, headers={"X-SM-Actor": "human"})


@pytest.mark.parametrize("seen", [10, 10.000000000001])
def test_seen_value_allows_changed_proposal_and_preserves_provenance(approval_case, seen):
    _, _, folder, inst, chip, record = approval_case
    response = press(approval_case, [{"path": "value", "new": 31, "old": 999, "seen_now": seen}])
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["stage"]["applied"]
    assert json.loads((folder / "state.json").read_text())["value"] == 31
    assert approvals.get(str(inst), chip, record["id"])["writes"][0]["old"] == 1


def test_changed_since_display_refuses_every_row_and_returns_fresh_card(approval_case):
    app, client, folder, inst, chip, record = approval_case
    before = (folder / "state.json").read_bytes()
    response = press(approval_case, [{"path": "other", "new": 40, "seen_now": 20},
                                    {"path": "value", "new": 30, "seen_now": 9}])
    assert response.status_code == 409
    body = response.get_json()
    assert body["error"] == "value changed to 10 since the card showed 9; press again"
    assert body["approval"]["status"] == "pending"
    assert body["approval"]["writes"][0]["now"] == 10
    assert body["approval"]["writes"][0]["now_known"] is True
    assert body["stage"]["staged"] == 0
    assert approvals.get(str(inst), chip, record["id"])["status"] == "pending"
    assert (folder / "state.json").read_bytes() == before
    assert client.get("/api/agent/chip").get_json()["pending"] == 0
    with app.app_context():
        from quam_state_manager.web import routes
        assert routes._active_ctx()["store"].get_value("other") == 20
    assert press(approval_case, [{"path": "value", "new": 30, "seen_now": 10}]).status_code == 200


def test_missing_seen_value_preserves_existing_behavior(approval_case):
    _, _, folder, _, _, _ = approval_case
    response = press(approval_case, [{"path": "value", "new": 30, "old": 999}])
    assert response.status_code == 200, response.get_json()
    assert json.loads((folder / "state.json").read_text())["value"] == 30


def test_seen_value_does_not_allow_an_agent_press(approval_case):
    _, client, _, _, _, record = approval_case
    response = client.post(f"/api/agent/approvals/{record['id']}/approve",
                           json={"writes": [{"path": "value", "new": 30, "seen_now": 10}]},
                           headers={"X-SM-Agent": "agent"})
    assert response.status_code == 403
