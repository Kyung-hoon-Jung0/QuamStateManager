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
    assert "and 1 more row<" in html and "more rows" not in html and 'data-ns-act="reject"' in html
    html = _summary_html(approval_case[0], rows, 0)                   # 12 rows: every one listed
    assert 'data-ns-act="approve"' in html and "Write to chip" in html and "data-ns-review" not in html
    assert html.count("data-seen-path=") == 12
    html = _summary_html(approval_case[0], rows, 3)                   # 15 rows: 12 listed
    assert "and 3 more rows<" in html and "Write to chip" not in html


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
    assert body["error"] == ("1 value of this approval was not on the summary (other); nothing was written. "
                             "Review it in the agent panel")
    assert (folder / "state.json").read_bytes() == before
    assert client.get("/api/agent/chip").get_json()["pending"] == 0
    assert approvals.get(str(inst), chip, record["id"])["status"] == "pending"
    for seen in ([], [{"path": "value", "seen_now": 10}, {"path": "other"}]):     # a bare path shows nothing
        assert _summary_press(approval_case, {"seen": seen}).status_code == 409
    body = _summary_press(approval_case, {"seen": []}).get_json()
    assert body["error"].startswith("2 values of this approval were not on the summary (value, other); "), body
    _assert_plain(body["error"])
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
    # refused by the empty-tray check, which runs after the never-touch read and before a row of
    # this approval was staged (not later by the count at the door, which names "another edit
    # reached"); that the check shares the staging's lock hold is pinned by the next test
    error = response.get_json()["error"]
    assert error == "the tray holds 1 staged edit; apply or discard it in the window first, then approve", error
    _assert_plain(error)
    assert (case[2] / "state.json").read_bytes() == before
    tray = case[1].get("/api/agent/tray").get_json()["entries"]
    assert [(x["path"], x["new"]) for x in tray] == [("third", 99)]      # the other window's edit stays its own



class _LockThatLetsAnEditIn:
    """The store's RLock, except that the first hold the approval's staging takes lets another
    window's edit land just before the lock is taken: the empty-tray check must see it, which it
    does only when the check sits inside that same hold."""

    def __init__(self, real, edit):
        self.real, self.edit, self.fired = real, edit, False

    def __enter__(self):
        import sys
        if not self.fired and sys._getframe(1).f_code.co_name == "_stage_writes":
            self.fired = True
            self.edit()
        return self.real.__enter__()

    def __exit__(self, *exc):
        return self.real.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self.real, name)


def test_the_empty_tray_check_shares_the_staging_lock_hold(tmp_path):
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])
    with case[0].app_context():
        store = _ctx(case)["store"]
        lock = _LockThatLetsAnEditIn(store._lock, lambda: _foreign_edit(case))
        store._lock = lock
    before = (case[2] / "state.json").read_bytes()
    try:
        response = press(case, [{"path": "value", "new": 30, "seen_now": 10}])
    finally:
        store._lock = lock.real
    assert lock.fired
    assert response.status_code == 409, response.get_json()
    # the edit that landed as the staging took the lock is seen by the check inside that hold;
    # a check taken before the hold would let it pass and leave it to the door's count
    assert response.get_json()["error"].startswith("the tray holds 1 staged edit;"), response.get_json()
    assert (case[2] / "state.json").read_bytes() == before
    tray = case[1].get("/api/agent/tray").get_json()["entries"]
    assert [(x["path"], x["new"]) for x in tray] == [("third", 99)]

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


# ---- the take-back never undoes another window's later edit

def _land_after_staging(monkeypatch, edit):
    from quam_state_manager.core import working_copy
    real = working_copy.live_diverged_now
    once = []

    def lands(wc):                              # another window's edit, between the staging and the door
        if not once:                            # once: later reads (the tray) must not edit again
            once.append(1)
            edit()
        return real(wc)
    monkeypatch.setattr(working_copy, "live_diverged_now", lands)


def _ctx(case):
    from quam_state_manager.web import routes
    return routes._active_ctx()


def test_a_take_back_keeps_another_windows_later_edit_of_the_same_path(tmp_path, monkeypatch):
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])
    _land_after_staging(monkeypatch, lambda: _foreign_edit(case, "value", 99))
    before = (case[2] / "state.json").read_bytes()
    response = press(case, [{"path": "value", "new": 30, "seen_now": 10}])
    assert response.status_code == 409, response.get_json()
    assert "could not be taken back" not in response.get_json()["error"]
    assert (case[2] / "state.json").read_bytes() == before
    tray = case[1].get("/api/agent/tray").get_json()["entries"]
    # the other window's value stays, and its row's "before" is the chip's, never the refused 30
    assert [(x["path"], x["old"], x["new"]) for x in tray] == [("value", 10, 99)]
    with case[0].app_context():
        ctx = _ctx(case)
        assert ctx["store"].get_value("value") == 99
        assert [(e.dot_path, e.old_value, e.new_value) for e in ctx["store"].change_log] == [("value", 10, 99)]
        ctx["modifier"].undo()                  # Ctrl+Z on that row: back to the chip's value
        assert ctx["store"].get_value("value") == 10 and not ctx["store"].change_log
    assert approvals.get(str(case[3]), case[4], case[5]["id"])["status"] == "pending"


def test_a_take_back_of_an_untouched_path_still_reverts_it(tmp_path, monkeypatch):
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])
    _land_after_staging(monkeypatch, lambda: _foreign_edit(case, "third", 99))
    response = press(case, [{"path": "value", "new": 30, "seen_now": 10}])
    assert response.status_code == 409, response.get_json()
    tray = case[1].get("/api/agent/tray").get_json()["entries"]
    assert [(x["path"], x["old"], x["new"]) for x in tray] == [("third", 5, 99)]
    with case[0].app_context():
        assert _ctx(case)["store"].get_value("value") == 10


@pytest.mark.parametrize("edit", ["delete", "change"])
def test_a_created_key_another_window_changed_since_stays_whole(tmp_path, monkeypatch, edit):
    case = _case(tmp_path, {"value": 10, "third": 5},
                 [{"path": "value", "old": 10, "new": 30},
                  {"path": "fresh.x", "old": None, "new": 1, "created": True}])

    def other():
        with case[0].app_context():
            mod = _ctx(case)["modifier"]
            mod.delete_subtree("fresh") if edit == "delete" else mod.set_value("fresh.x", 7)
    _land_after_staging(monkeypatch, other)
    before = (case[2] / "state.json").read_bytes()
    response = press(case, [{"path": "value", "new": 30, "seen_now": 10},
                            {"path": "fresh.x", "new": 1, "seen_unknown": True}])
    assert response.status_code == 409, response.get_json()      # never a 500
    error = response.get_json()["error"]
    _assert_kept_text(error, 1)
    # the count is the tray after the take-back: the created row that stayed + the other window's
    assert "the tray holds 2 staged edits; apply or discard them in the window first" in error, error
    assert (case[2] / "state.json").read_bytes() == before
    with case[0].app_context():
        store = _ctx(case)["store"]
        assert store.get_value("value") == 10                 # the untouched row came back out
        log = [(e.dot_path, e.created, e.deleted) for e in store.change_log]
        # the created key and the other window's edit of it stay whole, as the tray says
        if edit == "delete":
            assert log == [("fresh", True, False), ("fresh", False, True)]
            assert "fresh" not in store.merged
        else:
            assert log == [("fresh", True, False), ("fresh.x", False, False)]
            assert store.get_value("fresh.x") == 7


def test_a_take_back_hands_the_chips_value_to_the_first_later_row(tmp_path, monkeypatch):
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])

    def two_edits():
        _foreign_edit(case, "value", 99)
        _foreign_edit(case, "value", 7)
    _land_after_staging(monkeypatch, two_edits)
    before = (case[2] / "state.json").read_bytes()
    response = press(case, [{"path": "value", "new": 30, "seen_now": 10}])
    assert response.status_code == 409, response.get_json()
    assert "the tray holds 2 staged edits;" in response.get_json()["error"], response.get_json()
    assert (case[2] / "state.json").read_bytes() == before
    tray = case[1].get("/api/agent/tray").get_json()["entries"]
    # the chip's value is the FIRST later row's "before"; the second keeps the first's value
    assert [(x["path"], x["old"], x["new"]) for x in tray] == [("value", 10, 99), ("value", 99, 7)]
    with case[0].app_context():
        ctx = _ctx(case)
        ctx["modifier"].undo()
        ctx["modifier"].undo()                  # two Ctrl+Z: the chip's value, never the refused 30
        assert ctx["store"].get_value("value") == 10 and not ctx["store"].change_log


def test_a_created_scalar_another_window_set_since_stays_whole(tmp_path, monkeypatch):
    case = _case(tmp_path, {"value": 10, "third": 5},
                 [{"path": "fresh", "old": None, "new": 1, "created": True}])
    _land_after_staging(monkeypatch, lambda: _foreign_edit(case, "fresh", 7))
    before = (case[2] / "state.json").read_bytes()
    response = press(case, [{"path": "fresh", "new": 1, "seen_unknown": True}])
    assert response.status_code == 409, response.get_json()
    error = response.get_json()["error"]
    assert "1 value of this approval could not be taken back" in error and "(fresh)" in error
    assert "the tray holds 2 staged edits;" in error, error
    assert (case[2] / "state.json").read_bytes() == before
    with case[0].app_context():
        ctx = _ctx(case)
        store = ctx["store"]
        # the create row stays whole, never read as a plain set "None -> 7"
        log = [(e.dot_path, e.old_value, e.new_value, e.created) for e in store.change_log]
        assert log == [("fresh", None, 1, True), ("fresh", 1, 7, False)]
        assert store.get_value("fresh") == 7
        ctx["modifier"].undo()
        ctx["modifier"].undo()                  # back to no key at all, never "fresh": null
        assert "fresh" not in store.merged and not store.change_log


_CREATED = ["fresh", "spare"]


def _created_key_case(tmp_path, count=1):
    return _case(tmp_path, {"value": 10, "third": 5},
                 [{"path": f"{k}.x", "old": None, "new": 1, "created": True} for k in _CREATED[:count]])


def _other_sets_fresh_x(case, count=1):
    with case[0].app_context():
        for k in _CREATED[:count]:
            _ctx(case)["modifier"].set_value(f"{k}.x", 7)


def _created_press(case, count=1):
    return press(case, [{"path": f"{k}.x", "new": 1, "seen_unknown": True} for k in _CREATED[:count]])


def _kept_text(count):
    if count == 1:
        return ("; 1 value of this approval could not be taken back because another window changed it since "
                "(fresh); a human decides in the window")
    return ("; 2 values of this approval could not be taken back because another window changed them since "
            "(fresh, spare); a human decides in the window")


def _assert_kept_text(error, count):
    assert _kept_text(count) in error, error
    _assert_plain(error)


def _assert_plain(error):
    # one plural style ("1 value" / "N values") and one sentence: no "(s)", no ".;" join
    assert "(s)" not in error and ".;" not in error, error


@pytest.mark.parametrize("count", [1, 2])
def test_a_stale_live_refusal_names_the_rows_that_stayed(tmp_path, monkeypatch, count):
    case = _created_key_case(tmp_path, count)
    from quam_state_manager.core import working_copy
    once = []

    def moved(wc):                              # another window's edit, and the live files moved
        if not once:
            once.append(1)
            _other_sets_fresh_x(case, count)
            return True
        return False
    monkeypatch.setattr(working_copy, "live_diverged_now", moved)
    before = (case[2] / "state.json").read_bytes()
    response = _created_press(case, count)
    error = response.get_json()["stage"]["error"] if response.status_code == 200 else response.get_json()["error"]
    assert error.startswith("stale_live:"), response.get_json()
    _assert_kept_text(error, count)
    assert (case[2] / "state.json").read_bytes() == before


@pytest.mark.parametrize("count", [1, 2])
def test_a_door_refusal_names_the_rows_that_stayed(tmp_path, monkeypatch, count):
    case = _created_key_case(tmp_path, count)
    from quam_state_manager.web import agent_api
    real = agent_api._pre_door_state
    once = []

    def lands(r, ctx):                          # another window's edit, after the in-lock check
        if not once:
            once.append(1)
            _other_sets_fresh_x(case, count)
        return real(r, ctx)
    monkeypatch.setattr(agent_api, "_pre_door_state", lands)
    before = (case[2] / "state.json").read_bytes()
    response = _created_press(case, count)
    assert once, "the edit never landed before the door"
    error = response.get_json()["stage"]["error"] if response.status_code == 200 else response.get_json()["error"]
    assert not error.startswith("another edit reached the tray"), error      # the door refused, not the check
    _assert_kept_text(error, count)
    if count == 1:
        door = ("1 edit was made in another State Manager window and is not shown on this screen. "
                "Applying now would write it to the live chip too")
    else:
        door = ("2 edits were made in another State Manager window and are not shown on this screen. "
                "Applying now would write them to the live chip too")
    assert error == door + _kept_text(count), error
    assert (case[2] / "state.json").read_bytes() == before


def test_the_other_window_check_names_two_rows_that_stayed(tmp_path, monkeypatch):
    case = _created_key_case(tmp_path, 2)
    _land_after_staging(monkeypatch, lambda: _other_sets_fresh_x(case, 2))
    before = (case[2] / "state.json").read_bytes()
    response = _created_press(case, 2)
    assert response.status_code == 409, response.get_json()
    error = response.get_json()["error"]
    _assert_kept_text(error, 2)
    assert "the tray holds 4 staged edits; apply or discard them" in error, error
    assert (case[2] / "state.json").read_bytes() == before


def _literal_text(node):
    import ast
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))


def test_a_full_tray_refusal_counts_two_edits(tmp_path, monkeypatch):
    case = _case(tmp_path, {"value": 10, "third": 5, "fourth": 6}, [{"path": "value", "old": 10, "new": 30}])
    from quam_state_manager.core import agent_setup
    real = agent_setup.never_touch_in_force

    def lands(*a, **k):                         # two edits of another window, before the staging
        _foreign_edit(case)
        _foreign_edit(case, "fourth", 98)
        return real(*a, **k)
    monkeypatch.setattr(agent_setup, "never_touch_in_force", lands)
    response = press(case, [{"path": "value", "new": 30, "seen_now": 10}])
    assert response.status_code == 409, response.get_json()
    error = response.get_json()["error"]
    assert error == "the tray holds 2 staged edits; apply or discard them in the window first, then approve", error
    _assert_plain(error)


@pytest.mark.parametrize("count", [1, 2])
def test_an_unstageable_refusal_counts_values_like_the_others(tmp_path, count):
    gone = [{"path": f"gone{i}", "old": i, "new": None, "deleted": True} for i in range(count)]
    case = _case(tmp_path, {"value": 10, "gone0": 0, "gone1": 1}, [{"path": "value", "old": 10, "new": 30}] + gone)
    before = (case[2] / "state.json").read_bytes()
    response = press(case, [{"path": "value", "new": 30, "seen_now": 10}]
                     + [{"path": w["path"], "new": None, "seen_now": w["old"]} for w in gone])
    body = response.get_json()
    error = body["stage"]["error"] if response.status_code == 200 else body["error"]
    want = "1 of 2 values cannot be staged: gone0 (" if count == 1 else "2 of 3 values cannot be staged: gone0 ("
    assert error.startswith(want), body
    _assert_plain(error)
    assert (case[2] / "state.json").read_bytes() == before


def _refusal_functions():
    import ast
    from pathlib import Path
    from quam_state_manager.web import agent_api, routes
    # every function that builds a text an approval press can answer with: the approve door
    # (approvals_decide), the staging and its take-backs, the unstaged list, the tray count,
    # and the apply door it calls (routes: the unseen-edit refusal and the door itself)
    names = {agent_api: {"approvals_decide", "_stage_writes", "_unstaged_message", "_take_back_own",
                         "_take_back_saved", "_refusal_line", "_structural_why", "_count_of", "_then",
                         "_taken_back", "_door_lead_then"},
             routes: {"_unseen_edit_refusal", "state_apply_to_live"}}
    found = {}
    for mod, want in names.items():
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name in want:
                found[fn.name] = fn
    assert set(found) == set().union(*names.values()), sorted(found)
    return found


def test_no_approval_refusal_text_says_s_in_brackets():
    # every text these functions can return counts "1 value" / "N values" through `_count_of`;
    # never "value(s)" or "edit(s)". The one "(s)" left is the human's journal line of an
    # approval that went through (approvals_decide), not a refusal: main's wording, kept.
    import ast
    journal = " write(s) from "                  # "{actor} approved {n} write(s) from {card}"
    texts = [(name, _literal_text(n)) for name, fn in _refusal_functions().items() for n in ast.walk(fn)
             if isinstance(n, ast.JoinedStr) or (isinstance(n, ast.Constant) and isinstance(n.value, str))]
    marks = ("of this approval", "the tray holds", "cannot be staged", "made in another")
    assert all(any(m in t for _, t in texts) for m in marks), marks
    assert not [(n, t) for n, t in texts if "(s)" in t.replace(journal, "")], texts
    from quam_state_manager.web import agent_api
    for clause in (agent_api.TAKEN_BACK, agent_api.KEPT_UNVERIFIED):     # the clauses are module constants
        assert "(s)" not in clause and clause.startswith("; "), clause


def _refused_live_write(case, monkeypatch, save_stands):
    """An approval press whose live write is refused by the folder (no write permission).
    save_stands: the door could not put its working-copy save back, so the staging takes the
    approval's values back out of the saved working copy instead of out of the tray."""
    from quam_state_manager.core import working_copy
    from quam_state_manager.web import routes
    live = case[2] / "state.json"

    def refused(*a, **k):
        raise PermissionError(13, "Permission denied", str(live))
    monkeypatch.setattr(working_copy, "apply_to_live", refused)
    if save_stands:
        monkeypatch.setattr(routes, "_press_undo", lambda ctx, press: False)
    before = live.read_bytes()
    response = press(case, [{"path": "value", "new": 30, "seen_now": 10}])
    assert live.read_bytes() == before
    return response


@pytest.mark.parametrize("save_stands", [False, True])
def test_a_refused_live_write_says_the_approvals_values_were_taken_back(tmp_path, monkeypatch, save_stands):
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])
    response = _refused_live_write(case, monkeypatch, save_stands)
    assert response.status_code == 409, response.get_json()
    error = response.get_json()["error"]
    assert error == ("Apply failed: the live folder refused the write (read-only, or no write permission); "
                     "nothing was written, and the approval's values were taken back out of SM"), error
    _assert_plain(error)
    assert case[1].get("/api/agent/tray").get_json()["entries"] == []
    with case[0].app_context():
        assert _ctx(case)["store"].merged["value"] == 10


def _live_write_with_outcome(case, monkeypatch, outcome):
    """An approval press whose live write raises with the door's write outcome ``outcome``
    (routes._write_outcome). "landed" first writes the approval's value to the live file."""
    from quam_state_manager.core import working_copy
    from quam_state_manager.web import routes
    live = case[2] / "state.json"

    def raises(*a, **k):
        if outcome == "landed":
            live.write_text(json.dumps({"value": 30, "third": 5}), encoding="utf-8")
        raise OSError(5, "read-back failed")
    monkeypatch.setattr(working_copy, "apply_to_live", raises)
    monkeypatch.setattr(routes, "_write_outcome", lambda pending: outcome)
    if outcome == "failed":
        # "failed" puts the door's save back; making that unsafe reaches the branch where the save
        # stands. "unknown" and "landed" keep the save on their own (the door never calls it there)
        monkeypatch.setattr(routes, "_press_undo", lambda ctx, press: False)
    return press(case, [{"path": "value", "new": 30, "seen_now": 10}])


_KEPT = ("; the live write could not be verified, so the approval's values stay in SM's working copy until a "
         "person takes live or applies; check the chip")


@pytest.mark.parametrize("outcome, text, sm_value, chip_value", [
    ("failed", "Apply failed: the live chip was not written; nothing was written, and the approval's values were "
               "taken back out of SM", 10, 10),
    ("unknown", "Apply is UNVERIFIED: SM cannot tell whether the live chip holds the write \u2014 "
                "[Errno 5] read-back failed" + _KEPT, 30, 10),
    ("landed", "Apply is UNVERIFIED: the write reached the live chip, but SM could not finish confirming it \u2014 "
               "[Errno 5] read-back failed" + _KEPT, 30, 30),
])
def test_a_live_write_after_the_save_says_what_is_known_per_outcome(tmp_path, monkeypatch, outcome, text,
                                                                    sm_value, chip_value):
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])
    response = _live_write_with_outcome(case, monkeypatch, outcome)
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert body["error"] == text, body["error"]
    _assert_plain(body["error"])
    if outcome != "failed":
        # maybe written: never "nothing was written", and the values stay where the chip may hold them
        assert "nothing was written" not in body["error"] and "taken back" not in body["error"]
        assert body["stage"]["saved_in_working_copy"] is True
        assert body["stage"]["write_outcome"] == outcome
    else:
        assert "saved_in_working_copy" not in body["stage"]
    assert body["how"] == "the approval stays pending; see the error"
    assert approvals.get(str(case[3]), case[4], case[5]["id"])["status"] == "pending"
    assert case[1].get("/api/agent/tray").get_json()["entries"] == []
    with case[0].app_context():
        assert _ctx(case)["store"].merged["value"] == sm_value
    assert json.loads((case[2] / "state.json").read_text(encoding="utf-8")) == {"value": chip_value, "third": 5}


def test_the_human_apply_press_text_carries_no_approval_clause(tmp_path, monkeypatch):
    """The door's own text for a person's Apply press is the door's, whatever the outcome."""
    from quam_state_manager.core import working_copy
    from quam_state_manager.web import routes
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])

    def raises(*a, **k):
        raise OSError(5, "read-back failed")
    monkeypatch.setattr(working_copy, "apply_to_live", raises)
    monkeypatch.setattr(routes, "_write_outcome", lambda pending: "unknown")
    client = case[1]
    assert client.post("/field/edit", data={"dot_path": "value", "value": "30"}).status_code == 200
    response = client.post("/state/apply-to-live", data={"seen_changes": "1"},
                           headers={"Accept": "application/json"})
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["message"] == ("Apply is UNVERIFIED: SM cannot tell whether the live chip holds "
                                              "the write \u2014 [Errno 5] read-back failed Your edits are kept.")
    assert response.get_json()["write_outcome"] == "unknown"


def _first_refused_then_second(tmp_path, monkeypatch, outcome, reject_first):
    """Approval A (``value`` 10 -> 30) is refused at its live write with the door's write outcome
    ``outcome``; then, with the live write working again, approval B (``third`` 5 -> 7) is pressed.
    Returns B's response and the case."""
    from quam_state_manager.core import working_copy
    from quam_state_manager.web import routes
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])
    live = case[2] / "state.json"
    armed = [True]
    real_apply, real_outcome = working_copy.apply_to_live, routes._write_outcome

    def first_raises(*a, **k):
        if not armed[0]:
            return real_apply(*a, **k)
        if outcome == "landed":
            live.write_text(json.dumps({"value": 30, "third": 5}), encoding="utf-8")
        raise OSError(5, "read-back failed")
    monkeypatch.setattr(working_copy, "apply_to_live", first_raises)
    monkeypatch.setattr(routes, "_write_outcome", lambda pending: outcome if armed[0] else real_outcome(pending))
    first = press(case, [{"path": "value", "new": 30, "seen_now": 10}])
    assert first.status_code == 409, first.get_json()
    armed[0] = False
    client = case[1]
    if reject_first:
        assert client.post(f"/api/agent/approvals/{case[5]['id']}/reject", json={},
                           headers={"X-SM-Actor": "human"}).status_code == 200
    second = approvals.add(str(case[3]), case[4], kind="writes", node="synthetic", targets=[],
                           writes=[{"path": "third", "old": 5, "new": 7}], reason="test",
                           why_held="mode ask-writes", actor="by_agent")
    response = client.post(f"/api/agent/approvals/{second['id']}/approve",
                           json={"writes": [{"path": "third", "new": 7, "seen_now": 5}]},
                           headers={"X-SM-Actor": "human"})
    return response, case


@pytest.mark.parametrize("reject_first", [False, True])
@pytest.mark.parametrize("outcome", ["failed", "none", "unknown", "landed"])
def test_a_later_approval_never_writes_an_earlier_refused_approvals_values(tmp_path, monkeypatch, outcome,
                                                                          reject_first):
    response, case = _first_refused_then_second(tmp_path, monkeypatch, outcome, reject_first)
    chip = json.loads((case[2] / "state.json").read_text(encoding="utf-8"))
    if outcome in ("failed", "none"):
        # A's values were taken back out of SM: B writes only its own row
        assert response.status_code == 200, response.get_json()
        assert chip == {"value": 10, "third": 7}
        return
    # maybe written: A's values stay in SM, so B must not carry them to the chip under its own name
    assert response.status_code == 409, response.get_json()
    error = response.get_json()["error"]
    assert error.startswith("stale_live: "), error
    _assert_plain(error)
    assert response.get_json()["how"] == "the approval stays pending; take live, then approve again"
    assert chip == ({"value": 30, "third": 5} if outcome == "landed" else {"value": 10, "third": 5})
    with case[0].app_context():
        ctx = _ctx(case)
        assert ctx["store"].merged["third"] == 5            # B's own row was taken back
        assert ctx["store"].merged["value"] == 30           # A's value stays, as SM shows it
        if outcome == "unknown":
            assert ctx["live_diverged"] is True             # the person sees it and decides
    assert case[1].get("/api/agent/tray").get_json()["entries"] == []


def test_an_unverified_write_names_why_a_later_approval_is_refused(tmp_path, monkeypatch):
    response, _ = _first_refused_then_second(tmp_path, monkeypatch, "unknown", True)
    assert response.get_json()["error"] == (
        "stale_live: SM cannot tell whether the live chip matches its last sync (an earlier live write could "
        "not be verified); take live first"), response.get_json()["error"]


@pytest.mark.parametrize("outcome", ["landed", "unknown"])
def test_an_unexpected_error_after_the_live_write_keeps_the_values(tmp_path, monkeypatch, outcome):
    """The door's unexpected-error exit (500) may follow the write itself: the approval reads it as
    "unknown", so SM never shows 10 while the chip holds 30."""
    from quam_state_manager.core import working_copy
    from quam_state_manager.web import routes
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])
    live = case[2] / "state.json"

    def writes_then_breaks(*a, **k):
        live.write_text(json.dumps({"value": 30, "third": 5}), encoding="utf-8")
        raise RuntimeError("bookkeeping broke")
    monkeypatch.setattr(working_copy, "apply_to_live", writes_then_breaks)
    monkeypatch.setattr(routes, "_write_outcome", lambda pending: outcome)
    response = press(case, [{"path": "value", "new": 30, "seen_now": 10}])
    assert response.status_code == 409, response.get_json()
    body = response.get_json()
    assert body["error"] == "Apply to live failed unexpectedly: RuntimeError: bookkeeping broke" + _KEPT, body["error"]
    _assert_plain(body["error"])
    assert body["stage"]["saved_in_working_copy"] is True
    assert body["stage"]["write_outcome"] == "unknown"
    assert approvals.get(str(case[3]), case[4], case[5]["id"])["status"] == "pending"
    with case[0].app_context():
        assert _ctx(case)["store"].merged["value"] == 30
        assert _ctx(case)["live_diverged"] is True
    assert json.loads(live.read_text(encoding="utf-8")) == {"value": 30, "third": 5}


def test_the_doors_unexpected_error_hands_a_json_caller_the_write_outcome(tmp_path, monkeypatch):
    from quam_state_manager.core import working_copy
    from quam_state_manager.web import routes
    case = _case(tmp_path, {"value": 10, "third": 5}, [{"path": "value", "old": 10, "new": 30}])

    def breaks(*a, **k):
        raise RuntimeError("bookkeeping broke")
    monkeypatch.setattr(working_copy, "apply_to_live", breaks)
    monkeypatch.setattr(routes, "_write_outcome", lambda pending: "landed")
    client = case[1]
    assert client.post("/field/edit", data={"dot_path": "value", "value": "30"}).status_code == 200
    response = client.post("/state/apply-to-live", data={"seen_changes": "1"},
                           headers={"Accept": "application/json"})
    assert response.status_code == 500
    body = response.get_json()
    assert (body["ok"], body["write_outcome"]) == (False, "landed"), body
    assert body["message"] == "Apply to live failed unexpectedly: RuntimeError: bookkeeping broke"
