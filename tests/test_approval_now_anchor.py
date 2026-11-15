"""Approval presses consent to the values rendered on the card."""
import json
from pathlib import Path

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


def _case(tmp_path, state, writes):
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    inst = tmp_path / "instance"
    app = create_app(testing=True, instance_path=str(inst))
    client = app.test_client()
    assert client.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    chip = client.get("/api/agent/chip").get_json()["chip_key"]
    record = approvals.add(str(inst), chip, kind="writes", node="synthetic", targets=[], writes=writes,
                           reason="test", why_held="mode ask-writes", actor="by_agent")
    return app, client, folder, inst, chip, record


def _card_rows(case):
    _, client, _, _, _, record = case
    live = client.get("/api/agent/chat/cards").get_json().get("live") or {}
    card = next(a for a in live.get("approvals") or [] if a["id"] == record["id"])
    return {w["path"]: w for w in card["writes"]}


def _set_in_store(case, path, value):
    app = case[0]
    with app.app_context():
        from quam_state_manager.web import routes
        routes._active_ctx()["store"].merged[path] = value


@pytest.mark.parametrize("stored,new", [(float("nan"), 2.5), ([1.0, float("nan")], [1.0, 2.5])])
def test_a_value_holding_nan_approves_with_the_cards_own_now(tmp_path, stored, new):
    case = _case(tmp_path, {"value": stored}, [{"path": "value", "old": 1, "new": new}])
    row = _card_rows(case)["value"]
    assert row["now_known"] is True
    response = press(case, [{"path": "value", "new": new, "seen_now": row["now"]}])
    assert response.status_code == 200, response.get_json()
    assert json.loads((case[2] / "state.json").read_text())["value"] == new


def test_a_card_that_showed_null_refuses_once_the_leaf_is_set(tmp_path):
    case = _case(tmp_path, {"value": None}, [{"path": "value", "old": None, "new": 2}])
    row = _card_rows(case)["value"]
    assert row["now_known"] is True and row["now"] is None
    _set_in_store(case, "value", 3)
    response = press(case, [{"path": "value", "new": 2, "seen_now": row["now"]}])
    assert response.status_code == 409
    assert response.get_json()["error"] == "value changed to 3 since the card showed null; press again"
    assert approvals.get(str(case[3]), case[4], case[5]["id"])["status"] == "pending"


def test_a_not_set_row_refuses_once_the_leaf_appears(tmp_path):
    case = _case(tmp_path, {"other": 1}, [{"path": "later", "old": 1, "new": 2}])
    row = _card_rows(case)["later"]
    assert row["now_known"] is False
    _set_in_store(case, "later", 5)
    response = press(case, [{"path": "later", "new": 2, "seen_unknown": True}])
    assert response.status_code == 409
    body = response.get_json()
    assert body["error"] == "later changed to 5 since the card showed not set; press again"
    assert body["approval"]["writes"][0]["now"] == 5
    assert approvals.get(str(case[3]), case[4], case[5]["id"])["status"] == "pending"


def test_what_the_card_showed_is_not_kept_in_the_record(approval_case):
    _, _, _, inst, chip, record = approval_case
    response = press(approval_case, [{"path": "value", "new": 30, "seen_now": 10}])
    assert response.status_code == 200, response.get_json()
    for w in approvals.get(str(inst), chip, record["id"])["writes"]:
        assert "seen_now" not in w and "seen_unknown" not in w


@pytest.mark.parametrize("stored,new", [
    (float("inf"), 2.5), (float("-inf"), 2.5), ([1.0, float("-inf")], [1.0, 2.5]),
    ([float("nan"), float("inf")], [1.0, 2.5]), ({"a": float("inf"), "b": 1.0}, {"a": 1.0, "b": 1.0})])
def test_a_value_holding_a_non_finite_number_approves_with_the_cards_own_now(tmp_path, stored, new):
    case = _case(tmp_path, {"value": 0.0}, [{"path": "value", "old": 1, "new": new}])
    _set_in_store(case, "value", stored)       # json.dumps cannot write inf into the fixture file
    row = _card_rows(case)["value"]
    assert row["now_known"] is True
    response = press(case, [{"path": "value", "new": new, "seen_now": row["now"]}])
    assert response.status_code == 200, response.get_json()
    assert json.loads((case[2] / "state.json").read_text())["value"] == new


@pytest.mark.parametrize("shown,later", [
    ([1.0, 2.0], [1.0, 2.0, 3.0]), ([1.0, 2.0], [1.0]), ({"a": 1.0}, {"a": 1.0, "b": 2.0})])
def test_a_list_or_dict_that_changed_size_since_the_card_refuses(tmp_path, shown, later):
    case = _case(tmp_path, {"value": shown}, [{"path": "value", "old": shown, "new": 7}])
    row = _card_rows(case)["value"]
    _set_in_store(case, "value", later)
    response = press(case, [{"path": "value", "new": 7, "seen_now": row["now"]}])
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["stage"]["seen_now_changed"] is True
    assert approvals.get(str(case[3]), case[4], case[5]["id"])["status"] == "pending"


def test_the_refusal_returns_the_card_in_display_units(tmp_path):
    state = {"qubits": {"q1": {"f_01": 5.0e9}}}
    case = _case(tmp_path, state, [{"path": "qubits.q1.f_01", "old": 4.9e9, "new": 5.2e9}])
    row = _card_rows(case)["qubits.q1.f_01"]
    assert row["display"]["unit"] == "GHz"
    with case[0].app_context():
        from quam_state_manager.web import routes
        routes._active_ctx()["store"].merged["qubits"]["q1"]["f_01"] = 5.1e9
    seen = int(row["now"])                     # the browser's JSON round trip writes 5000000000
    response = press(case, [{"path": "qubits.q1.f_01", "new": 5.2e9, "seen_now": seen}])
    assert response.status_code == 409
    body = response.get_json()
    fresh = body["approval"]["writes"][0]
    assert fresh["now"] == 5.1e9 and fresh["display"] == row["display"]
    assert body["error"] == ("qubits.q1.f_01 changed to 5.1000 GHz since the card showed 5.0000 GHz; "
                             "press again (stored in Hz: now 5100000000, the card's 5000000000)")
    assert {k: v for k, v in fresh.items() if k != "now"} == {k: v for k, v in row.items() if k != "now"}


_VECTORS = json.loads((Path(__file__).parent / "fixtures" / "card_row_vectors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _VECTORS["rows"], ids=[c["case"] for c in _VECTORS["rows"]])
def test_the_server_writes_a_card_row_as_the_card_does(case):
    from quam_state_manager.core.value_delta import format_card_row
    values = [float(v) if i in case.get("numbers", []) else v for i, v in enumerate(case["values"])]
    assert format_card_row(values, case["display"]) == case["expect"]


@pytest.mark.parametrize("case", _VECTORS["refusals"], ids=[c["case"] for c in _VECTORS["refusals"]])
def test_the_server_refusal_line_is_the_cards_line(case):
    from quam_state_manager.web.agent_api import _refusal_line
    assert _refusal_line(case["path"], case["row"], case["sent"]) == case["expect"]


def _moved_press(tmp_path, path, old, start, later, seen, new):
    parts = path.split(".")
    state: dict = {}
    node = state
    for k in parts[:-1]:
        node = node.setdefault(k, {})
    node[parts[-1]] = start
    case = _case(tmp_path, state, [{"path": path, "old": old, "new": new}])
    row = _card_rows(case)[path]
    with case[0].app_context():
        from quam_state_manager.web import routes
        leaf = routes._active_ctx()["store"].merged
        for k in parts[:-1]:
            leaf = leaf[k]
        leaf[parts[-1]] = later
    response = press(case, [{"path": path, "new": new, "seen_now": seen if seen is not None else row["now"]}])
    assert response.status_code == 409, response.get_json()
    return response.get_json()


def test_a_refusal_in_a_field_stored_in_its_display_unit_has_no_stored_bracket(tmp_path):
    body = _moved_press(tmp_path, "qubits.q1.xy.operations.x180.length", 40, 40, 48, None, 44)
    assert body["error"] == ("qubits.q1.xy.operations.x180.length changed to 48 ns since the card showed "
                             "40 ns; press again")
    assert body["stage"]["path"] == "qubits.q1.xy.operations.x180.length"


def test_a_refusal_writes_an_exact_half_as_the_card_does(tmp_path):
    body = _moved_press(tmp_path, "qubits.q1.z.joint_offset", 0.02, 0.02, 0.03125, None, 0.04)
    assert body["error"] == ("qubits.q1.z.joint_offset changed to 0.0313 V since the card showed "
                             "0.0200 V; press again")


def test_a_refusal_takes_its_decimals_from_the_whole_row(tmp_path):
    body = _moved_press(tmp_path, "qubits.q1.f_01", 5.00004e9, 5.0e9, 5.1e9, 5000000000, 5.2e9)
    assert body["error"] == ("qubits.q1.f_01 changed to 5.10000 GHz since the card showed 5.00000 GHz; "
                             "press again (stored in Hz: now 5100000000, the card's 5000000000)")


def test_the_refusal_line_is_built_from_the_returned_card_row(tmp_path):
    from quam_state_manager.web.agent_api import _refusal_line
    body = _moved_press(tmp_path, "qubits.q1.extras.taps", [1.0, 2.0], [1.0, 2.0], [1.0, 2.5], None, [1.0, 3.0])
    row = body["approval"]["writes"][0]
    assert body["error"] == _refusal_line("qubits.q1.extras.taps", row, {"seen_now": [1.0, 2.0], "new": [1.0, 3.0]})
    assert body["error"] == "qubits.q1.extras.taps changed to [1,2.5] since the card showed [1,2]; press again"


def test_a_string_proposed_into_an_inf_leaf_is_not_text_into_null(tmp_path):
    case = _case(tmp_path, {"value": 0.0}, [{"path": "value", "old": 1, "new": "abc"}])
    _set_in_store(case, "value", float("inf"))
    row = _card_rows(case)["value"]
    assert row["now_known"] is True and row["now"] is None
    assert "text_into_null" not in row and "becomes_ref" not in row
    _set_in_store(case, "value", None)
    assert _card_rows(case)["value"].get("text_into_null") is True       # a real null still says so


def _summary_press(case, body):
    _, client, _, _, _, record = case
    return client.post(f"/api/agent/approvals/{record['id']}/approve", json=body, headers={"X-SM-Actor": "human"})


def test_a_press_naming_no_values_refuses_when_a_listed_now_moved(approval_case):
    response = _summary_press(approval_case, {"seen": [{"path": "value", "seen_now": 9},
                                                       {"path": "other", "seen_now": 20}]})
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert body["stage"]["seen_now_changed"] is True and body["stage"]["path"] == "value"
    assert body["error"] == "value changed to 10 since the summary showed 9; press again"
    assert json.loads((approval_case[2] / "state.json").read_text()) == {"value": 10, "other": 20}
    assert approvals.get(str(approval_case[3]), approval_case[4], approval_case[5]["id"])["status"] == "pending"


def test_a_press_naming_no_values_writes_every_value_when_the_listed_nows_held(approval_case):
    response = _summary_press(approval_case, {"seen": [{"path": "value", "seen_now": 10},
                                                       {"path": "other", "seen_now": 20}]})
    assert response.status_code == 200, response.get_json()
    assert json.loads((approval_case[2] / "state.json").read_text()) == {"value": 30, "other": 40}
    for w in approvals.get(str(approval_case[3]), approval_case[4], approval_case[5]["id"])["writes"]:
        assert "seen_now" not in w and "seen_unknown" not in w


def test_the_summary_rows_carry_the_now_the_approve_door_compares(approval_case):
    app = approval_case[0]
    summary = {"plan": {"id": "pl-x", "title": "t", "mode": "auto", "status": "running", "started_at": 1.0},
               "counts": {}, "applied": [], "failures": [], "halted": [], "plans": [],
               "held": [{"id": "ap-x", "kind": "writes", "status": "pending", "node": "n", "targets": [],
                         "writes": [{"path": "a.b", "old": 1, "new": 2, "now": 5000000000, "now_known": True,
                                     "seen_now": 5000000000},
                                    {"path": "a.c", "old": 1, "new": 2, "now": None, "now_known": False}],
                         "more": 0}]}
    with app.test_request_context("/agent/summary"):
        from flask import render_template
        html = render_template("_agent_summary.html", summary=summary)
    assert "data-seen-path=\"a.b\" data-seen-now='5000000000'" in html
    assert "data-seen-path=\"a.c\" data-seen-unknown=\"1\"" in html
    summary["held"][0]["status"] = "approved"
    with app.test_request_context("/agent/summary"):
        html = render_template("_agent_summary.html", summary=summary)
    assert "data-seen-path" not in html                  # a decided approval has no door to anchor


def test_the_summary_data_maps_a_held_now_to_the_cards_form(approval_case, monkeypatch):
    app = approval_case[0]
    from quam_state_manager.core import agent_overnight
    from quam_state_manager.web import agent_api
    monkeypatch.setattr(agent_overnight, "summary", lambda *a, **k: {"held": [{"writes": [
        {"path": "value", "now": float("inf"), "now_known": True},
        {"path": "other", "now": None, "now_known": False}]}]})
    with app.test_request_context("/agent/summary"):
        monkeypatch.setattr(agent_api, "_reconcile_grant", lambda: None)
        monkeypatch.setattr(agent_api, "_chip_key", lambda: "k")
        out = agent_api.summary_data(None)
    rows = out["held"][0]["writes"]
    assert "seen_now" in rows[0] and rows[0]["seen_now"] is None and "seen_now" not in rows[1]


# ---- the night summary writes only what it listed (contract 7)

def _summary_html(app, writes, more):
    summary = {"plan": {"id": "pl-x", "title": "t", "mode": "auto", "status": "running", "started_at": 1.0},
               "counts": {}, "applied": [], "failures": [], "halted": [], "plans": [],
               "held": [{"id": "ap-x", "kind": "writes", "status": "pending", "node": "n", "targets": [],
                         "writes": writes, "more": more}]}
    with app.test_request_context("/agent/summary"):
        from flask import render_template
        return render_template("_agent_summary.html", summary=summary)


def test_the_summary_offers_no_write_when_it_cannot_list_every_row(approval_case):
    rows = [{"path": f"a.v{i}", "old": 1, "new": 2, "now": 1, "now_known": True, "seen_now": 1} for i in range(12)]
    html = _summary_html(approval_case[0], rows, 1)                   # 13 rows: 12 listed
    assert 'data-ns-act="approve"' not in html and "Write to chip" not in html
    assert "data-ns-review" in html and ">Review in panel</a>" in html and 'href="/agent"' in html
    assert "and 1 more rows" in html and 'data-ns-act="reject"' in html
    html = _summary_html(approval_case[0], rows, 0)                   # 12 rows: every one listed
    assert 'data-ns-act="approve"' in html and "Write to chip" in html and "data-ns-review" not in html
    assert html.count("data-seen-path=") == 12


def test_the_summary_lists_at_most_twelve_rows():
    from quam_state_manager.core import agent_overnight
    rows, more = agent_overnight._rows([{"path": f"a.v{i}", "old": 0, "new": 1} for i in range(13)], None)
    assert len(rows) == 12 and more == 1


def test_a_forged_seen_that_lacks_a_row_writes_nothing(approval_case):
    _, client, folder, inst, chip, record = approval_case
    before = (folder / "state.json").read_bytes()
    response = _summary_press(approval_case, {"seen": [{"path": "value", "seen_now": 10}]})
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert body["unseen"] == ["other"]
    assert body["error"] == ("1 value(s) of this approval were not on the summary (other); nothing was written. "
                             "Review it in the agent panel")
    assert (folder / "state.json").read_bytes() == before
    assert client.get("/api/agent/chip").get_json()["pending"] == 0
    assert approvals.get(str(inst), chip, record["id"])["status"] == "pending"
    for seen in ([], [{"path": "value", "seen_now": 10}, {"path": "other"}]):     # a bare path shows nothing
        assert _summary_press(approval_case, {"seen": seen}).status_code == 409
    assert (folder / "state.json").read_bytes() == before


def test_a_summary_press_of_twelve_listed_rows_writes_them(tmp_path):
    state = {f"v{i}": i for i in range(12)}
    case = _case(tmp_path, state, [{"path": f"v{i}", "old": i, "new": i + 100} for i in range(12)])
    response = _summary_press(case, {"seen": [{"path": f"v{i}", "seen_now": i} for i in range(12)]})
    assert response.status_code == 200, response.get_json()
    assert json.loads((case[2] / "state.json").read_text()) == {f"v{i}": i + 100 for i in range(12)}


def test_a_summary_row_shown_as_unknown_refuses_once_the_leaf_reads(approval_case):
    _, _, folder, inst, chip, record = approval_case
    before = (folder / "state.json").read_bytes()
    response = _summary_press(approval_case, {"seen": [{"path": "value", "seen_unknown": True},
                                                       {"path": "other", "seen_now": 20}]})
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert body["stage"]["seen_now_changed"] is True and body["stage"]["path"] == "value"
    assert body["error"] == "value changed to 10 since the summary showed not set; press again"
    assert (folder / "state.json").read_bytes() == before
    assert approvals.get(str(inst), chip, record["id"])["status"] == "pending"


def test_the_summary_refusal_names_the_summary_and_its_unit(tmp_path, monkeypatch):
    case = _case(tmp_path, {"qubits": {"q1": {"f_01": 5.0e9}}},
                 [{"path": "qubits.q1.f_01", "old": 4.9e9, "new": 5.2e9}])
    from quam_state_manager.core import agent_overnight
    from quam_state_manager.web import agent_api
    with monkeypatch.context() as m, case[0].test_request_context("/agent/summary"):
        m.setattr(agent_overnight, "summary", lambda *a, **k: {"held": [{"writes": [
            {"path": "qubits.q1.f_01", "old": 4.9e9, "new": 5.2e9, "now": 5.0e9, "now_known": True}]}],
            "applied": [{"writes": [{"path": "qubits.q1.f_01", "old": 4.8e9, "new": 4.9e9, "now": 5.0e9,
                                     "now_known": True}]}]})
        m.setattr(agent_api, "_reconcile_grant", lambda: None)
        m.setattr(agent_api, "_chip_key", lambda: "k")
        out = agent_api.summary_data(None)
    held = out["held"][0]["writes"][0]
    assert held["cells"] == {"now": "5.0000 GHz", "old": "4.9000 GHz", "new": "5.2000 GHz"}
    assert out["applied"][0]["writes"][0]["cells"]["old"] == "4.8000 GHz"
    html = _summary_html(case[0], [held], 0)
    assert "<td>4.9000 GHz</td><td>5.2000 GHz</td><td>5.0000 GHz</td>" in html
    _set_in_store(case, "qubits", {"q1": {"f_01": 5.1e9}})
    response = _summary_press(case, {"seen": [{"path": "qubits.q1.f_01", "seen_now": int(held["seen_now"])}]})
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["error"] == (
        "qubits.q1.f_01 changed to 5.1000 GHz since the summary showed 5.0000 GHz; press again "
        "(stored in Hz: now 5100000000, the summary's 5000000000)")


def test_the_summary_without_a_display_unit_shows_the_stored_value(approval_case):
    from quam_state_manager.web.agent_api import _summary_cells
    assert _summary_cells({"path": "value", "old": 1, "new": 30, "now": 10, "now_known": True}) == \
        {"now": "10", "old": "1", "new": "30"}
    assert _summary_cells({"path": "value", "old": "a", "new": None, "now_known": False}) == \
        {"now": "null", "old": '"a"', "new": "null"}


# ---- a refusal line never raises (and so never becomes a 500)

@pytest.mark.parametrize("x", [0.0, -0.0, 5e-324, 1e-300, 2.5e-300, 0.5, 9.99995e7, 99999950.5,
                               3539875255816353643.0, 1e20, 1e21, 1e300, 1.7976931348623157e308,
                               float("nan"), float("inf"), float("-inf")])
def test_js_number_printing_never_raises(x):
    from quam_state_manager.core.value_delta import js_to_exponential, js_to_fixed
    for dp in range(0, 21):
        assert isinstance(js_to_fixed(x, dp), str) and isinstance(js_to_fixed(-x, dp), str)
        assert isinstance(js_to_exponential(x, dp), str) and isinstance(js_to_exponential(-x, dp), str)


def test_js_number_printing_of_non_finite_values_follows_js():
    from quam_state_manager.core.value_delta import js_to_exponential, js_to_fixed
    assert [js_to_fixed(float(v), 2) for v in ("nan", "inf", "-inf")] == ["NaN", "Infinity", "-Infinity"]
    assert [js_to_exponential(float(v), 2) for v in ("nan", "inf", "-inf")] == ["NaN", "Infinity", "-Infinity"]


def test_the_exponent_carries_into_the_next_power():
    from quam_state_manager.core.value_delta import js_to_exponential
    assert js_to_exponential(9.99995e7, 4) == "1.0000e+8"
    assert js_to_exponential(99999950.5, 0) == "1e+8"
    assert js_to_exponential(9.5, 0) == "1e+1"


def test_a_wide_row_refuses_with_its_line_not_a_500(tmp_path):
    body = _moved_press(tmp_path, "qubits.q1.z.joint_offset", 4.98e-241, -0.0, 3539875255816353643.0, None, 0.5)
    assert body["error"] == ("qubits.q1.z.joint_offset changed to 3539875255816353792.0000000000 V since the card "
                             "showed 0.0000000000 V; press again")


def test_a_refusal_line_that_cannot_be_formatted_still_refuses(tmp_path, monkeypatch):
    from quam_state_manager.core import value_delta

    def boom(*a, **k):
        raise ArithmeticError("synthetic")
    monkeypatch.setattr(value_delta, "format_card_row", boom)
    body = _moved_press(tmp_path, "qubits.q1.z.joint_offset", 0.02, 0.02, 0.03, None, 0.04)
    assert body["error"] == "qubits.q1.z.joint_offset changed since the card showed it; press again"
    assert body["approval"]["status"] == "pending"


# ---- dict values: the browser's key order on both lines

def test_the_app_json_provider_sorts_keys_as_the_vectors_assume(approval_case):
    app = approval_case[0]
    text = app.json.dumps({"b": 1, "10": 0, "2": {"z": 1, "a": 2}})
    assert text.index('"10"') < text.index('"2"') < text.index('"b"') and text.index('"a"') < text.index('"z"')


def test_the_server_line_writes_a_dict_in_the_browsers_order():
    from quam_state_manager.core.value_delta import js_json_text
    assert js_json_text({"b": 1, "10": 0, "2": 0}) == '{"2":0,"10":0,"b":1}'
    assert js_json_text({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert js_json_text({"01": 1, "-1": 2, "4294967294": 3, "4294967295": 4}) == \
        '{"4294967294":3,"-1":2,"01":1,"4294967295":4}'


# ---- the tray check and the door's count are taken under the store lock

def _foreign_edit(case, path="third", value=99):
    with case[0].app_context():
        from quam_state_manager.web import routes
        routes._active_ctx()["modifier"].set_value(path, value)


def test_an_edit_landing_before_the_staging_is_never_pushed_under_the_approval(tmp_path, monkeypatch):
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])
    from quam_state_manager.core import agent_setup
    real = agent_setup.never_touch_in_force

    def lands(*a, **k):                         # another window's edit, after the press was checked
        _foreign_edit(case)
        return real(*a, **k)
    monkeypatch.setattr(agent_setup, "never_touch_in_force", lands)
    before = (case[2] / "state.json").read_bytes()
    response = press(case, [{"path": "value", "new": 30, "seen_now": 10}])
    assert response.status_code == 409, response.get_json()
    # refused by the empty-tray check inside the staging lock, before a row of this approval
    # was staged (not later by the count at the door, which names "another edit reached")
    assert response.get_json()["error"].startswith("the tray holds 1 staged edit(s)"), response.get_json()
    assert (case[2] / "state.json").read_bytes() == before
    tray = case[1].get("/api/agent/tray").get_json()["entries"]
    assert [(x["path"], x["new"]) for x in tray] == [("third", 99)]      # the other window's edit stays its own


def test_an_edit_landing_after_the_staging_is_never_pushed_under_the_approval(tmp_path, monkeypatch):
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])
    from quam_state_manager.core import working_copy
    real = working_copy.live_diverged_now

    def lands(wc):                              # another window's edit, between the staging and the door
        _foreign_edit(case)
        return real(wc)
    monkeypatch.setattr(working_copy, "live_diverged_now", lands)
    before = (case[2] / "state.json").read_bytes()
    response = press(case, [{"path": "value", "new": 30, "seen_now": 10}])
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["error"].startswith("another edit reached the tray while this approval was staged")
    assert (case[2] / "state.json").read_bytes() == before
    tray = case[1].get("/api/agent/tray").get_json()["entries"]
    assert [(x["path"], x["new"]) for x in tray] == [("third", 99)]
    with case[0].app_context():
        from quam_state_manager.web import routes
        assert routes._active_ctx()["store"].get_value("value") == 10
    assert approvals.get(str(case[3]), case[4], case[5]["id"])["status"] == "pending"
