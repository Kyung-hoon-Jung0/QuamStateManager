"""The agent builds class files and CZ gates through the person's own
functions (``/api/agent/cz-gate/*``, ``/api/agent/class-file/save`` and the
MCP tools on top of them).

Pinned here: the agent's doors and the person's routes run ONE function, so a
broken input gets the same refusal text; the agent's create stages into the
tray under the agent's name and never writes the chip; the chip is written
only by apply_to_live, whose class-file check and unseen-edit gate hold in
auto mode too; outside auto mode the gate waits for a person's Apply (no
approval card); the create card carries the pulses, the check's timeline,
the status line and the restart notice; readonly MCP hides the write tools.

The lab's code is faked (cz_gate_routes.run_check, quam_config_save._run),
as in the person's tests; the chip copies, tokens and doors are real."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import make_response

from quam_state_manager import mcp
from quam_state_manager.core import approvals, class_file_check, limits, working_copy
from quam_state_manager.core import journal as journal_mod
from quam_state_manager.core import quam_config_save as Q
from quam_state_manager.web import agent_api, pulse_class_page
from quam_state_manager.web import cz_gate_routes as czr
from tests.test_cz_gate_routes import CHECK, CREATE, PLAN, _body, _fake, chip, fake  # noqa: F401
from tests.test_quam_config_save_routes import CLASS, QCLASS, _history, _sm_class_body, env  # noqa: F401

AGENT = {"X-SM-Agent": "claude"}
HUMAN = {"X-SM-Actor": "user-a"}
J = {"Accept": "application/json"}
A_PLAN, A_CHECK, A_CREATE = "/api/agent/cz-gate/plan", "/api/agent/cz-gate/check", "/api/agent/cz-gate/create"
A_CLASS = "/api/agent/class-file/save"
PAIR, GATE = "q1-2", "cz_sq"
MPATH = f"qubit_pairs.{PAIR}.macros.{GATE}"


@pytest.fixture(autouse=True)
def _known_fields(monkeypatch):
    """The saved class has the fields of a square pulse (no env probe here)."""
    from quam_state_manager.core import pulse_catalog
    real = pulse_catalog.by_qclass
    monkeypatch.setattr(pulse_catalog, "by_qclass",
                        lambda q: real("SquarePulse") if q == QCLASS else real(q))


def _live_state(a) -> dict:
    return json.loads((Path(a.ctx["working_copy"].live_folder) / "state.json").read_text(encoding="utf-8"))


def _live_bytes(a):
    live = Path(a.ctx["working_copy"].live_folder)
    return (live / "state.json").read_bytes(), (live / "wiring.json").read_bytes()


def _chip_key(a) -> str:
    return working_copy.key_for(a.ctx["path"])


def _auto(a):
    limits.save(a.app.instance_path, _chip_key(a), {"mode": "auto"})


def _journal(a) -> str:
    inst = a.app.instance_path
    return "".join(journal_mod.read(inst, ch) for ch in journal_mod.list_chips(inst))


def _agent_gate(a, body=None):
    """plan -> check -> create through the agent's doors; the create answer."""
    body = body or _body()
    p = a.c.post(A_PLAN, json=body, headers=AGENT)
    assert p.status_code == 200 and p.get_json()["pairs"][0]["ok"], p.get_json()
    j = a.c.post(A_CHECK, json=body, headers=AGENT).get_json()
    assert j["ok"] and j["pairs"][0]["verdict"]["allowed"], j
    assert j["plan_sig"] == p.get_json()["plan_sig"]
    r = a.c.post(A_CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"],
                                 "reason": "a square CZ to compare"}, headers=AGENT)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _agent_apply(a):
    n = len(a.store.change_log)
    return a.c.post("/state/apply-to-live", data={"seen_changes": str(n)}, headers={**AGENT, **J})


def _agent_save_class(a):
    first = a.c.post(A_CLASS, json={"cls": CLASS}, headers=AGENT)
    assert first.status_code == 409 and first.get_json()["confirm"]["folder"] == str(a.folder)
    r = a.c.post(A_CLASS, json={"cls": CLASS, "confirmed_folder": first.get_json()["confirm"]["folder"]},
                 headers=AGENT)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


# ------------------------------------------------------------ one function

@pytest.mark.parametrize("person,agent,name", [
    (PLAN, A_PLAN, "plan_answer"), (CHECK, A_CHECK, "check_answer"), (CREATE, A_CREATE, "create_answer")])
def test_the_agent_door_and_the_person_route_run_one_function(chip, monkeypatch, person, agent, name):
    from flask import jsonify
    seen = []

    def one():
        seen.append(name)
        return jsonify(ok=False, error="the shared body answered"), 409
    monkeypatch.setattr(czr, name, one)
    for url, headers in ((person, {}), (agent, AGENT)):
        r = chip.c.post(url, json={"check_token": "t", "plan_sig": "s", **_body()}, headers=headers)
        assert r.status_code == 409 and r.get_json()["error"] == "the shared body answered", url
    assert seen == [name, name]


def test_the_class_file_door_runs_the_person_function(chip, monkeypatch):
    seen = []

    def one(data, actor):
        seen.append((dict(data), actor))
        return {"ok": False, "error": "the shared body answered"}, 409
    monkeypatch.setattr(pulse_class_page, "save_config", one)
    for headers in ({}, AGENT):
        r = chip.c.post("/pulse-class/save-config" if not headers else A_CLASS,
                        json={"cls": "X", "python": "C:/other/python.exe"}, headers=headers)
        assert r.status_code == 409 and r.get_json()["error"] == "the shared body answered"
    assert [s[1] for s in seen] == ["person", "by_claude"]
    assert seen[0][0]["python"] == "C:/other/python.exe"
    assert "python" not in seen[1][0], "the agent's door runs only the chip's chosen env"


# ------------------------------------------------------------ the same refusals

def _both(a, url_person, url_agent, payload):
    rp = a.c.post(url_person, json=payload)
    ra = a.c.post(url_agent, json=payload, headers=AGENT)
    return (rp.status_code, rp.get_json()), (ra.status_code, ra.get_json())


@pytest.mark.parametrize("kind", ["plan", "check"])
def test_no_pair_is_refused_with_the_persons_words(chip, fake, kind):
    urls = (PLAN, A_PLAN) if kind == "plan" else (CHECK, A_CHECK)
    (cp, jp), (ca, ja) = _both(chip, *urls, _body(pairs=[]))
    assert cp == ca == 400 and jp["error"] == ja["error"] == "Pick a pair."


def test_a_bad_class_is_refused_with_the_persons_words(chip, fake):
    (cp, jp), (ca, ja) = _both(chip, PLAN, A_PLAN, _body(cls=""))
    assert cp == ca == 200
    assert jp["pairs"][0]["refusals"] == ja["pairs"][0]["refusals"] and not ja["pairs"][0]["ok"]
    assert ja["pairs"][0]["class_refusals"] == jp["pairs"][0]["class_refusals"] != []
    (cp, jp), (ca, ja) = _both(chip, CHECK, A_CHECK, _body(cls=""))
    assert jp["pairs"][0]["verdict"]["reason"] == ja["pairs"][0]["verdict"]["reason"] != ""
    assert not ja["pairs"][0]["verdict"]["allowed"]


@pytest.mark.parametrize("breakage,words", [
    ("plan_sig", "The inputs changed after the check. Press Check again."),
    ("stale", "The chip changed after the check. Nothing was written. The check runs again."),
    ("archive", "This chip is a read-only archive. Open the live chip to create a gate."),
    ("foreign", "This check was made on another chip. Press Check again on this chip.")])
def test_a_broken_create_is_refused_with_the_persons_words(chip, fake, breakage, words):
    # each door gets its own kind's check: a token is bound to who ran the check
    jp = chip.c.post(CHECK, json=_body()).get_json()
    ja = chip.c.post(A_CHECK, json=_body(), headers=AGENT).get_json()
    payloads = [{"check_token": j["check_token"], "plan_sig": j["plan_sig"]} for j in (jp, ja)]
    if breakage == "plan_sig":
        sig = chip.c.post(PLAN, json=_body(amp=0.3)).get_json()["plan_sig"]
        for p in payloads:
            p["plan_sig"] = sig
    elif breakage == "stale":
        chip.ctx["modifier"].set_value("qubits.q3.z.operations.const.amplitude", 0.11)
    elif breakage == "archive":
        chip.ctx["origin"] = "dataset_archive"
    else:
        for p in payloads:
            czr._TOKENS[p["check_token"]]["store_id"] = -1
    before = chip.snap()
    rp = chip.c.post(CREATE, json=payloads[0])
    ra = chip.c.post(A_CREATE, json=payloads[1], headers=AGENT)
    assert rp.status_code == ra.status_code == 409
    assert rp.get_json()["error"] == ra.get_json()["error"] == words
    assert chip.snap() == before


def test_the_persons_create_route_still_refuses_an_agent(chip, fake):
    j = chip.c.post(A_CHECK, json=_body(), headers=AGENT).get_json()
    chip.app.config["PROVE_CALLERS"] = True
    try:
        r = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]},
                        headers=AGENT)
        assert r.status_code == 403
        r = chip.c.post(A_CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]},
                        headers=AGENT)
        assert r.status_code == 200, r.get_json()
    finally:
        chip.app.config.pop("PROVE_CALLERS", None)


# ------------------------------------------------------------ the check token knows who ran it

def test_an_agent_cannot_create_from_a_persons_check(chip, fake):
    j = chip.c.post(CHECK, json=_body()).get_json()
    assert czr._TOKENS[j["check_token"]]["by"] == "person"
    payload = {"check_token": j["check_token"], "plan_sig": j["plan_sig"]}
    before = chip.snap()
    r = chip.c.post(A_CREATE, json=payload, headers=AGENT)
    assert r.status_code == 409 and r.get_json()["check_again"] is True
    assert r.get_json()["error"] == czr._CHECKED_BY_OTHER["person"]
    assert chip.snap() == before
    r = chip.c.post(CREATE, json=payload)
    assert r.status_code == 200 and r.get_json()["count"] == 1, "the person's own create still works"


def test_a_person_cannot_create_from_an_agents_check(chip, fake):
    j = chip.c.post(A_CHECK, json=_body(), headers=AGENT).get_json()
    assert czr._TOKENS[j["check_token"]]["by"] == "agent"
    payload = {"check_token": j["check_token"], "plan_sig": j["plan_sig"]}
    before = chip.snap()
    r = chip.c.post(CREATE, json=payload)
    assert r.status_code == 409 and r.get_json()["check_again"] is True
    assert r.get_json()["error"] == czr._CHECKED_BY_OTHER["agent"]
    assert chip.snap() == before
    r = chip.c.post(A_CREATE, json=payload, headers=AGENT)
    assert r.status_code == 200 and r.get_json()["count"] == 1, "the agent's own create still works"


def test_a_check_with_no_window_proof_is_not_a_persons(chip, fake):
    """The kind comes from the callers gate: no header and no window cookie
    is not a person."""
    chip.app.config["PROVE_CALLERS"] = True
    try:
        j = chip.c.post(CHECK, json=_body()).get_json()
    finally:
        chip.app.config.pop("PROVE_CALLERS", None)
    assert czr._TOKENS[j["check_token"]]["by"] == "agent"


# ------------------------------------------------------------ the run lock

def test_a_running_queue_locks_the_agents_check_and_create(chip, fake, monkeypatch):
    from quam_state_manager.core import scheduler
    ja = chip.c.post(A_CHECK, json=_body(), headers=AGENT).get_json()
    jp = chip.c.post(CHECK, json=_body()).get_json()
    monkeypatch.setattr(scheduler, "is_active", lambda _inst: True)
    before = chip.snap()
    calls = [(A_CHECK, _body(), AGENT),
             (A_CREATE, {"check_token": ja["check_token"], "plan_sig": ja["plan_sig"]}, AGENT),
             (CHECK, _body(), {}),
             (CREATE, {"check_token": jp["check_token"], "plan_sig": jp["plan_sig"]}, {})]
    for url, body, headers in calls:
        r = chip.c.post(url, json=body, headers=headers)
        assert r.status_code == 409 and r.get_json()["error"] == "scheduler_running", url
    assert chip.snap() == before
    r = chip.c.post(A_PLAN, json=_body(), headers=AGENT)
    assert r.status_code == 200 and r.get_json()["pairs"][0]["ok"], "the plan writes nothing and still answers"


# ------------------------------------------------------------ stage only, the agent's name

def test_the_agents_create_stages_under_its_name_and_never_writes_live(chip, fake):
    live = _live_bytes(chip)
    out = _agent_gate(chip)
    assert out["count"] == 1 and "tray_html" not in out
    group = [e for e in chip.store.change_log if e.group_id == out["group"]]
    assert group and {e.actor for e in group} == {"by_claude"}
    assert _live_bytes(chip) == live
    text = _journal(chip)
    assert f"staged the CZ gate {PAIR} / {GATE} in the tray" in text and "a square CZ to compare" in text


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_the_journal_line_of_an_agents_create_names_its_cli(chip, fake, cli):
    hdr = {"X-SM-Agent": cli}
    j = chip.c.post(A_CHECK, json=_body(), headers=hdr).get_json()
    r = chip.c.post(A_CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]},
                    headers=hdr)
    assert r.status_code == 200, r.get_json()
    assert f"`by_{cli}` staged the CZ gate {PAIR} / {GATE}" in _journal(chip)


def test_a_persons_create_still_names_the_person(chip, fake):
    j = chip.c.post(CHECK, json=_body()).get_json()
    out = chip.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]}).get_json()
    assert {e.actor for e in chip.store.change_log if e.group_id == out["group"]} == {"human"}


def test_outside_auto_mode_the_gate_waits_for_the_persons_apply(chip, fake):
    live = _live_bytes(chip)
    out = _agent_gate(chip)
    assert out["mode"] != "auto" and out["waits"] == agent_api.GATE_WAITS_PERSON
    r = _agent_apply(chip)
    assert r.status_code == 409 and r.get_json()["conflict"] == "agent_structural_rows"
    assert _live_bytes(chip) == live
    assert approvals.pending(chip.app.instance_path, _chip_key(chip)) == [], "no approval card"
    assert any(e.dot_path == MPATH for e in chip.store.change_log), "the gate stays in the tray"
    n = len(chip.store.change_log)
    r = chip.c.post("/state/apply-to-live", data={"seen_changes": str(n)}, headers={**HUMAN, **J})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert GATE in _live_state(chip)["qubit_pairs"][PAIR]["macros"]


def test_a_persons_unseen_edit_refuses_the_agents_apply_in_auto_mode(chip, fake):
    _auto(chip)
    live = _live_bytes(chip)
    _agent_gate(chip)
    seen = len(chip.store.change_log)
    r = chip.c.post("/field/edit", data={"dot_path": "qubits.q3.z.operations.const.amplitude",
                                         "value": "0.12"}, headers=HUMAN)
    assert r.status_code == 200
    r = chip.c.post("/state/apply-to-live", data={"seen_changes": str(seen)}, headers={**AGENT, **J})
    assert r.status_code == 409, r.get_data(as_text=True)[:300]
    assert _live_bytes(chip) == live


# ------------------------------------------------------------ the full flow with a class file

@pytest.fixture
def lab(env, monkeypatch):  # noqa: F811
    """The class-file env plus a fake check runner and the chip's env for the live check."""
    czr_fake = _fake()
    monkeypatch.setattr(czr, "run_check", czr_fake)
    monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
    monkeypatch.setattr(class_file_check, "_env_source", lambda folder: "C:/fake/python.exe")
    env.fake = czr_fake
    return env


def test_class_file_then_gate_then_auto_apply_reaches_the_live_chip(lab):
    saved = _agent_save_class(lab)
    assert saved["restart"] == Q.RESTART_NEW
    rows = _history(lab)
    assert rows[-1]["actor"] == "by_claude" and rows[-1]["path"] == str(lab.folder / f"{CLASS}.py")
    assert Q.RESTART_NEW in _journal(lab), "the person reads the restart notice in the journal"
    _auto(lab)
    out = _agent_gate(lab, _sm_class_body())
    assert out["mode"] == "auto" and out["waits"] == agent_api.GATE_WAITS_AUTO
    card = out["cards"][0]
    assert card["pair"] == PAIR and card["gate"] == GATE and card["macro"] == MPATH
    assert card["pulses"] and MPATH not in card["pulses"]
    assert all(p.startswith("qubits.") or p.startswith("qubit_pairs.") for p in card["pulses"])
    assert card["timeline"]["data"]["lanes"][0]["key"] == "q1.z"
    assert PAIR in card["timeline"]["figure"]
    assert "class file present" in card["status_line"]
    assert card["class_files"] == [f"{CLASS}.py"]
    assert card["restart"] == [{"file": f"{CLASS}.py", "notice": Q.RESTART_NEW}]
    r = _agent_apply(lab)
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    macro = _live_state(lab)["qubit_pairs"][PAIR]["macros"][GATE]
    assert macro["extras"]["sm_class_files"][0]["file"] == f"{CLASS}.py"
    assert approvals.pending(lab.app.instance_path, _chip_key(lab)) == []


def test_the_agents_class_save_takes_no_folder(lab, monkeypatch):
    other = lab.folder.parent.parent / "elsewhere" / "quam_config"
    other.mkdir(parents=True)
    real, seen = pulse_class_page.save_config, []

    def spy(data, actor):
        seen.append(dict(data))
        return real(data, actor)
    monkeypatch.setattr(pulse_class_page, "save_config", spy)
    for payload in ({"cls": CLASS, "folder": str(other)},
                    {"cls": CLASS, "folder": str(other), "confirmed_folder": str(other)}):
        r = lab.c.post(A_CLASS, json=payload, headers=AGENT)
        assert r.status_code == 409 and r.get_json()["error"] == agent_api.CLASS_FOLDER_REFUSAL
        assert r.get_json()["refused"] == "folder_is_the_persons"
    monkeypatch.setattr(mcp, "_link", ClientLink(lab.c))
    out = mcp.t_class_file_save({"cls": CLASS, "folder": str(other)})
    assert out["error"] == agent_api.CLASS_FOLDER_REFUSAL, "the bridge passes the folder on to be refused"
    assert seen == [], "a payload folder never reaches the save"
    assert list(other.iterdir()) == [] and not (lab.folder / f"{CLASS}.py").exists()
    saved = _agent_save_class(lab)
    assert saved["target"] == str(lab.folder / f"{CLASS}.py") and list(other.iterdir()) == []
    assert seen and all("folder" not in d for d in seen)
    assert f"`by_claude` saved the class file quam_config/{CLASS}.py" in _journal(lab)


def test_the_class_save_journal_line_names_codex(lab):
    hdr = {"X-SM-Agent": "codex"}
    first = lab.c.post(A_CLASS, json={"cls": CLASS}, headers=hdr).get_json()
    r = lab.c.post(A_CLASS, json={"cls": CLASS, "confirmed_folder": first["confirm"]["folder"]},
                   headers=hdr)
    assert r.status_code == 200, r.get_json()
    assert f"`by_codex` saved the class file quam_config/{CLASS}.py" in _journal(lab)


def test_a_changed_class_file_refuses_the_agents_apply_in_auto_mode(lab):
    _agent_save_class(lab)
    _auto(lab)
    _agent_gate(lab, _sm_class_body())
    target = lab.folder / f"{CLASS}.py"
    target.write_bytes(target.read_bytes() + b"# edited\n")
    live = _live_bytes(lab)
    r = _agent_apply(lab)
    assert r.status_code == 409 and "Not written to the live chip" in r.get_json()["detail"]
    assert f"{CLASS}.py" in r.get_json()["detail"] and "changed" in r.get_json()["detail"]
    assert _live_bytes(lab) == live


# ------------------------------------------------------------ MCP tools

class ClientLink:
    """The bridge's link, answered by the real Flask app as the agent."""

    def __init__(self, client):
        self.c = client
        self.calls = []

    def alive(self):
        return True

    @staticmethod
    def _out(r):
        return r.status_code, (r.get_json(silent=True) if r.is_json else r.get_data(as_text=True))

    def get(self, path, params=None, **_kw):
        self.calls.append(("GET", path))
        return self._out(self.c.get(path, query_string=params or {}, headers={**AGENT, **J}))

    def post_form(self, path, data, **_kw):
        self.calls.append(("POST", path))
        return self._out(self.c.post(path, data=data, headers={**AGENT, **J}))

    def post_json(self, path, data, **_kw):
        self.calls.append(("POSTJ", path))
        return self._out(self.c.post(path, json=data, headers=AGENT))


@pytest.fixture
def bridge(chip, monkeypatch):
    link = ClientLink(chip.c)
    monkeypatch.setattr(mcp, "_link", link)
    monkeypatch.setattr(mcp, "_chip", None)
    for k, v in {"_seen": None, "_seen_key": None, "_seen_token": None, "_seen_sig": None,
                 "_seen_paths": [], "_pin_key": None}.items():
        monkeypatch.setattr(mcp, k, v)
    return link


def test_the_mcp_tools_plan_check_create_and_the_apply_waits(chip, fake, bridge):
    live = _live_bytes(chip)
    refused = mcp.t_cz_gate_plan(_body(pairs=[]))
    assert refused == {"ok": False, "error": "Pick a pair."}, "a refusal is data with the dialog's words"
    j = mcp.t_cz_gate_check(_body())
    assert j["pairs"][0]["verdict"]["allowed"]
    out = mcp.t_cz_gate_create({"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert out["ok"] and out["cards"][0]["macro"] == MPATH
    assert mcp._seen == len(chip.store.change_log), "the bridge read the tray it will declare"
    res = mcp.t_apply_to_live({})
    assert res["applied"] is False and res["refused"]["conflict"] == "agent_structural_rows"
    assert "person's Apply" in res["how"]
    assert _live_bytes(chip) == live
    assert ("POSTJ", A_CREATE) in bridge.calls


def test_the_mcp_class_file_tool_carries_the_restart_notice(lab, monkeypatch):
    monkeypatch.setattr(mcp, "_link", ClientLink(lab.c))
    first = mcp.t_class_file_save({"cls": CLASS})
    assert first["confirm"]["folder"] == str(lab.folder)
    out = mcp.t_class_file_save({"cls": CLASS, "confirmed_folder": first["confirm"]["folder"]})
    assert out["ok"] and out["restart"] == Q.RESTART_NEW


def test_readonly_mcp_hides_the_writing_gate_tools(monkeypatch):
    names = {"cz_gate_check", "cz_gate_create", "class_file_save"}
    assert names <= set(mcp.TOOLS) and names <= mcp.WRITE_TOOLS
    assert "cz_gate_plan" in mcp.TOOLS and "cz_gate_plan" not in mcp.WRITE_TOOLS
    monkeypatch.setattr(mcp, "READ_ONLY", True)
    visible = set(mcp._visible_tools())
    assert not (names & visible) and "cz_gate_plan" in visible


def test_the_door_answer_helper_reads_a_tuple_and_a_response(chip):
    from flask import jsonify
    with chip.app.test_request_context("/"):
        assert agent_api._answer_of((jsonify(ok=False, error="x"), 409)) == ({"ok": False, "error": "x"}, 409)
        assert agent_api._answer_of(make_response(jsonify(ok=True))) == ({"ok": True}, 200)
