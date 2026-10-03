"""SM as an MCP server for a terminal agent (docs/172).

SM's Agent Setup page writes the client config for you. By hand (B-10,
docs/252): ``run_node`` can block for up to ~28 min (``_WAIT_CAP_S``), and both
CLIs give up on an MCP tool long before that -- Codex after 300 s -- while SM
keeps running the node. Raise the client's tool timeout to 30 min:

    claude mcp add sm -- python -m quam_state_manager.mcp
        # and start claude with MCP_TOOL_TIMEOUT=1800000 (milliseconds)

    # Codex: ~/.codex/config.toml
    [mcp_servers.sm]
    command = "python"
    args = ["-m", "quam_state_manager.mcp"]
    tool_timeout_sec = 1800

A stdio JSON-RPC server, stdlib only (the customer env has no ``mcp``
package), that is a THIN CLIENT of the running State Manager window: every
tool is one HTTP call to ``/api/agent/*`` or to the same routes the GUI
presses. The window owns the working copy; this process owns nothing.

What that buys the person at the terminal: every value the agent reads is
the value SM shows, every edit lands in SM's Review tray where Ctrl+Z and
the Versions panel already know it, and ``apply_to_live`` refuses -- with
the paths -- when a human edited something in the window the agent never
saw (docs/120's gate), instead of forcing.

Protocol surface: initialize, notifications/initialized, ping, tools/list,
tools/call. Newline-delimited JSON on stdio.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from quam_state_manager.core import agent_link

PROTOCOL = "2025-06-18"
SERVER = {"name": "quam-state-manager", "version": "1.0.0"}

_link: agent_link.SMLink | None = None
# docs/120's rule, applied to the agent: a press means what the presser could
# see. The tray count the agent last SAW (via tray / state_edit / undo) is
# what apply_to_live declares -- never a fresh read the agent never looked at.
_seen: int | None = None
# docs/246 A-07: what the agent saw is a picture OF A CHIP. The tray read that
# set _seen also records which chip (working-copy key + fingerprint token) and
# which change set (signature); apply_to_live declares exactly these, never a
# fresh read of whatever chip SM has open at press time.
_seen_key: str | None = None
_seen_token: str | None = None
_seen_sig: str | None = None
_seen_paths: list = []
_chip: str | None = None       # the chip SM last said it had open -- stamped on every answer
_CHIP_PIN = (os.environ.get("SM_CHIP") or "").strip() or None   # docs/173 S5: one bridge, one chip
# docs/246 A-06: the chip_key the pin first matched. A pin by NAME (the display
# name SM's own in-app session writes, or extras.chip_name) can match two
# fridges; once it has matched one, the bridge stays on THAT one.
_pin_key: str | None = None


def _pin_match(chip: dict) -> bool:
    """Does the open chip satisfy SM_CHIP (docs/246 A-06)?

    The pin is checked against the chip's IDENTITY, not its folder name:
    the chip_key (``<parent>-<path hash>``, exact) or the declared
    ``extras.chip_name``. The display name is still accepted -- it is what an
    existing pin, and SM's own in-app session, hold -- but every NAME match
    latches the chip_key it matched, so a second fridge with the same folder
    or declared name, opened later, is refused."""
    global _pin_key
    key = chip.get("chip_key")
    if _CHIP_PIN == key:
        return True
    by_name = _CHIP_PIN in (chip.get("declared_name"), chip.get("name"))
    if not by_name:
        return False
    if _pin_key is None:
        _pin_key = key
        return True
    return key == _pin_key


def _chip_facts() -> dict:
    global _chip
    chip = _ok(*_sm().get("/api/agent/chip"))
    _chip = chip.get("name") if chip.get("loaded") else None
    if _CHIP_PIN and chip.get("loaded") and not _pin_match(chip):
        open_id = chip.get("declared_name") or chip.get("name")
        raise ToolError(json.dumps({
            "refused": "chip_mismatch", "open": open_id, "open_chip_key": chip.get("chip_key"),
            "pinned": _CHIP_PIN, **({"pinned_chip_key": _pin_key} if _pin_key else {}),
            "pin_for_open_chip": chip.get("pin") or chip.get("chip_key"),
            "how": f"this bridge was made for {_CHIP_PIN}; SM has {open_id} ({chip.get('chip_key')}) open. "
                   "Every tool is refused until that chip is open in SM (two fridges, two bridges -- "
                   "never one bridge across both). A pin is matched against the chip's identity: "
                   "its chip_key, or the extras.chip_name it declares."}))
    return chip


def _journal(kind: str, text: str, reason: str | None = None, paths=None) -> None:
    """A line the bridge writes about its OWN acts (the hook does not see
    MCP calls). Best effort: a journal failure never fails the act."""
    try:
        _sm().post_json("/api/agent/journal", {"kind": kind, "text": text, "reason": reason,
                                               "paths": paths or []})
    except Exception:  # noqa: BLE001
        pass


def _sm() -> agent_link.SMLink:
    global _link
    if _link is not None and _link.alive():
        return _link
    _link = agent_link.connect()
    if _link is not None:
        _link.agent_id = _agent_id
    if _link is None:
        raise ToolError("State Manager is not running (or no window has been opened yet). "
                        "Start SM, open the chip, then call again. "
                        f"Looked in {agent_link.instance_dir() / 'instances'}; set SM_URL to override.")
    return _link


class ToolError(Exception):
    pass


def _ok(code: int, body: Any, *, expect=(200,)) -> Any:
    if code in expect:
        return body
    if isinstance(body, dict):
        raise ToolError(json.dumps({"http": code, **body}, default=str))
    text = body if isinstance(body, str) else json.dumps(body, default=str)
    raise ToolError(f"HTTP {code}: {text[:800]}")


# ------------------------------------------------------------------ tools

def t_sm_status(_a: dict) -> Any:
    """The open chip -- and the notes pinned on it (B-01, docs/252), which no
    tool used to return, so a "do not touch" note was invisible to the next
    agent. An older SM without ``/api/agent/notes`` leaves them out."""
    facts = _chip_facts()
    if isinstance(facts, dict) and facts.get("loaded"):
        try:
            code, body = _sm().get("/api/agent/notes")
            if code == 200 and isinstance(body, dict):
                facts = {**facts, "notes": body.get("notes") or []}
        except Exception:  # noqa: BLE001 -- the status never fails on the notes
            pass
    return facts


def t_take_live(_a: dict) -> Any:
    """Pull the chip's live files into SM's working copy. A node the agent ran
    writes state.json itself; SM's user-facing path never adopts that on its
    own (docs/87), so the agent must ask -- and only with an EMPTY tray: a
    pull over staged edits is the human's merge decision, not the agent's."""
    sm = _sm()
    chip = _chip_facts()
    if not chip.get("loaded"):
        raise ToolError("no chip is loaded in SM")
    if int(chip.get("pending") or 0) > 0:
        return {"taken": False, "note": "the tray is not empty -- undo or apply first; a pull over staged "
                                        "edits is a merge only a human should decide in the SM window"}
    if not chip.get("live_diverged"):
        return {"taken": False, "note": "SM already matches the live files"}
    changed, overlap = [], []
    try:
        d = _ok(*sm.get("/api/agent/live-diff"))
        changed = [{"path": c["path"], "old": c.get("working"), "new": c.get("live")} for c in d.get("changed") or []]
        overlap = list(d.get("overlap") or [])
    except ToolError:
        pass
    code, body = sm.post_form("/state/sync", {"mode": "discard"})
    _ok(code, body)
    after = _chip_facts()
    _journal("sm", f"took the live files into SM ({len(changed)} value(s) had moved outside SM)",
             paths=[c["path"] for c in changed[:20]])
    return {"taken": True, "live_diverged": after.get("live_diverged"), "changed": changed[:200],
            "overlap": overlap, "reverted": [],
            "note": "changed = live vs what SM held; overlap = also in the tray (empty: the tray was empty); "
                    "reverted = staged edits dropped (none: take_live needs an empty tray)"}


def t_state_get(a: dict) -> Any:
    return _ok(*_sm().get("/api/agent/state", {"path": a.get("path", "")}))


def t_state_search(a: dict) -> Any:
    return _ok(*_sm().get("/api/search", {"q": a["query"], "limit": int(a.get("limit") or 30)}))


def t_state_edit(a: dict) -> Any:
    """Stage one edit through /field/edit -- the same door as a cell edit.
    A 409 is an OFFER (type fix, FSP compensation) that the agent answers
    by calling again with the named ack; it is returned verbatim."""
    sm = _sm()
    chip = _chip_facts()
    if not chip.get("loaded"):
        raise ToolError("no chip is loaded in SM")
    value = a["value"]
    if not isinstance(value, str):
        value = json.dumps(value)
    form = {"dot_path": a["path"], "value": value, "expect_chip": chip.get("chip_token") or None,
            "type_fix": a.get("type_fix"), "fsp_ack": a.get("fsp_ack")}
    code, body = sm.post_form("/field/edit", form)
    if code == 409:
        if isinstance(body, dict) and (body.get("fsp_compensation") or body.get("type_fix")):
            return {"staged": False, "needs_answer": True, "offer": body,
                    "how": "call state_edit again with type_fix='convert'|'keep' or fsp_ack='comp'|'solo' "
                           "as the offer names"}
        # D-11: any other 409 (the run lock, a chip switch, a pulse-structure
        # refusal) is a refusal, not a question an ack could answer
        return {"staged": False, "refused": body,
                "how": "nothing was staged; read the refusal -- no ack answers it"}
    if code != 200:
        _ok(code, body)
    tray = _tray_seen()
    gid = body.get("group_id") if isinstance(body, dict) else None
    if gid is not None:
        # an FSP compensation bundle (A-03): the FSP and every amplitude, one gid
        entries = [e for e in tray["entries"] if e.get("group") == gid]
    else:
        entries = tray["entries"][-1:]
    for entry in entries:
        _journal("agent", f"staged `{entry['path']}` {entry['old']} -> {entry['new']}",
                 reason=a.get("reason") or "(no reason given)", paths=[entry["path"]])
    out = {"staged": True, "pending": tray["count"], "entry": entries[0] if entries else None,
           "note": "staged in SM's Review tray; nothing reached the chip. Call apply_to_live to write."}
    if len(entries) > 1:
        out["entries"] = entries
        out["note"] = (f"staged {len(entries)} edits as ONE group (the FSP and its compensated amplitudes); "
                       "nothing reached the chip. Call apply_to_live to write.")
    return out


def _tray_seen() -> Any:
    global _seen, _seen_key, _seen_token, _seen_sig, _seen_paths
    tray = _ok(*_sm().get("/api/agent/tray"))
    _seen = int(tray.get("seen_changes") or 0)
    _seen_sig = tray.get("seen_sig") or None
    _seen_paths = [e.get("path") for e in tray.get("entries") or []]
    if "chip_key" in tray or "chip_token" in tray:
        _seen_key = tray.get("chip_key") or None
        _seen_token = tray.get("chip_token") or None
    else:                       # an older SM: the chip read right beside the tray
        facts = _chip_facts()
        _seen_key = facts.get("chip_key") or None
        _seen_token = facts.get("chip_token") or None
    return tray


def t_tray(_a: dict) -> Any:
    return _tray_seen()


def t_undo(_a: dict) -> Any:
    """Undo the agent's own newest staged group. SM refuses (409) a person's
    group and an empty tray (the journal walk writes the live chip) for any
    request carrying X-SM-Agent (docs/246 A-04/A-05)."""
    sm = _sm()
    code, body = sm.post_form("/undo", {})
    if code == 409 and isinstance(body, dict) and body.get("refused"):
        tray = _tray_seen()
        return {"undone": False, "refused": body, "pending": tray.get("count"),
                "how": "nothing was undone. undo_mine undoes only your own rows; a person's edit or an "
                       "applied value is the person's to take back in the SM window."}
    _ok(code, body, expect=(200, 204))
    return _tray_seen()


def t_apply_to_live(_a: dict) -> Any:
    """Push the tray to the chip. Declares what the agent has seen so a human
    edit made in the window since is refused, never silently included."""
    global _seen
    sm = _sm()
    if _seen is None:
        return {"applied": False, "note": "call tray first -- apply writes only what you have seen"}
    chip = _chip_facts()
    if chip.get("live_diverged"):
        return {"applied": False, "refused": {"conflict": "stale_live"},
                "how": "the chip's live files moved outside SM (a node wrote them?). Nothing was written. "
                       "If the tray holds only your edits: undo, take_live, re-stage. Otherwise ask the human."}
    if int(chip.get("pending") or 0) == 0:
        _seen = 0
        return {"applied": False, "note": "nothing staged"}
    declared = _seen
    if _seen_key and chip.get("chip_key") and chip.get("chip_key") != _seen_key:
        # A-07: the chip the agent looked at is not the one open now
        _seen = None
        return {"applied": False,
                "refused": {"conflict": "chip_mismatch", "seen_on": _seen_key, "open": chip.get("chip_key")},
                "how": "another chip is open in SM than the one whose tray you saw. Nothing was written. "
                       "Call tray to look at THIS chip's tray before pressing anything."}
    tray_before = list(_seen_paths)
    form = {"seen_changes": declared, "expect_chip": _seen_token, "expect_chip_key": _seen_key}
    if _seen_sig:
        form["seen_sig"] = _seen_sig
    code, body = sm.post_form("/state/apply-to-live", form)
    if code == 409:
        _seen = None                        # the picture changed; look again before pressing
        if isinstance(body, dict) and body.get("conflict") == "stale_live":
            return {"applied": False, "refused": body,
                    "how": "nothing was written: the live files changed since SM last synced. "
                           "undo, take_live, re-stage -- or ask the human to merge in the SM window."}
        if isinstance(body, dict) and (body.get("conflict") == "chip_mismatch" or body.get("chip_mismatch")):
            # A-07: SM has another chip open than the one this tray was read on
            return {"applied": False, "refused": body,
                    "how": "nothing was written: SM has another chip open than the one whose tray you saw. "
                           "Call sm_status and tray to look at the chip that is open now before pressing."}
        return {"applied": False, "refused": body,
                "how": "a human edited the chip in the SM window since you last looked (paths above). "
                       "Call tray to read the full list, then apply_to_live again if you accept ALL of it, "
                       "or undo yours. Never force."}
    _ok(code, body)
    if not isinstance(body, dict):
        # an htmx fragment on 200 is the window's rendering, not a verdict --
        # believe only the chip's own count
        pass
    after = _chip_facts()
    if int(after.get("pending") or 0) != 0:
        _seen = None
        return {"applied": False, "note": "SM did not clear the tray -- it refused in a way this bridge "
                                          "cannot read; look at the SM window", "pending": after.get("pending")}
    _seen = 0
    paths = list(tray_before)
    _journal("sm", f"applied {len(paths)} edit(s) to the chip", paths=paths)
    return {"applied": True, "pending_after": after.get("pending"), "live_diverged": after.get("live_diverged"),
            "declared_seen": declared, "wrote": paths}


def t_versions(a: dict) -> Any:
    return _ok(*_sm().get("/api/agent/versions", {"n": int(a.get("n") or 30)}))


def t_field_history(a: dict) -> Any:
    return _ok(*_sm().get("/api/agent/field-history", {"path": a["path"]}))


def t_runs(a: dict) -> Any:
    return _ok(*_sm().get("/api/agent/runs", {"n": int(a.get("n") or 20), "experiment": a.get("experiment"),
                                               "qubit": a.get("qubit"), "date": a.get("date")}))


def t_run(a: dict) -> Any:
    return _ok(*_sm().get(f"/api/agent/run/{int(a['run_id'])}"))


def t_diagnostics(_a: dict) -> Any:
    return _ok(*_sm().get("/api/agent/diagnostics"))


def t_check_fit(a: dict) -> Any:
    return _ok(*_sm().get(f"/api/agent/check-fit/{int(a['run_id'])}"))


def t_families(_a: dict) -> Any:
    return _ok(*_sm().get("/api/agent/families"))


def t_family_manual(a: dict) -> Any:
    return _ok(*_sm().get(f"/api/agent/manual/{a['family']}"))


def t_journal_append(a: dict) -> Any:
    return _ok(*_sm().post_json("/api/agent/journal", {
        "kind": "agent", "text": a["text"], "reason": a["reason"],
        "run_id": a.get("run_id"), "paths": a.get("paths") or []}))


def t_journal_read(a: dict) -> Any:
    return _ok(*_sm().get("/api/agent/journal", {"date": a.get("date")}))


def t_run_node(a: dict) -> Any:
    """SM runs the node; the answer is the gate's refusal (data) or the run."""
    body = {"node": a.get("node"), "targets": a.get("targets") or [], "params": a.get("params") or {},
            "reason": a.get("reason"), "timeout_s": a.get("timeout_s"), "wait_s": a.get("wait_s"),
            "approval_id": a.get("approval_id"), "plan_id": a.get("plan_id") or os.environ.get("SM_PLAN"),
            "step": a.get("step")}
    wait_s = min(float(a.get("wait_s") or 240), _WAIT_CAP_S)   # review R4-9: under the CLI's tool timeout
    body["wait_s"] = wait_s
    code, res = _sm().post_json("/api/agent/run-node", body, timeout=wait_s + 60)
    if code == 409 and isinstance(res, dict):
        return {k: v for k, v in res.items() if k != "ok"}
    return _ok(code, res)


def t_run_wait(a: dict) -> Any:
    wait_s = min(float(a.get("wait_s") or 240), _WAIT_CAP_S)
    code, res = _sm().get(f"/api/agent/run/{a.get('key')}", {"wait_s": wait_s}, timeout=wait_s + 60)
    return _ok(code, res)


def t_plan_propose(a: dict) -> Any:
    body = {"title": a.get("title"), "steps": a.get("steps") or [], "why": a.get("why")}
    code, res = _sm().post_json("/api/agent/plans", body)
    return _ok(code, res)


def t_plan_status(a: dict) -> Any:
    pid = a.get("plan_id")
    if pid:
        return _ok(*_sm().get(f"/api/agent/plans/{pid}"))
    return _ok(*_sm().get("/api/agent/plans"))


def t_approvals(_a: dict) -> Any:
    return _ok(*_sm().get("/api/agent/approvals"))


def t_undo_mine(_a: dict) -> Any:
    global _seen
    res = _ok(*_sm().post_json("/api/agent/undo-mine", {}))
    try:
        _tray_seen()            # the new picture, with its chip and signature
    except ToolError:
        _seen = None            # a count with no chip behind it is not a picture: look again
    return res


def t_note_set(a: dict) -> Any:
    # B-04 (docs/252): no author here -- SM signs the note with this bridge's
    # X-SM-Agent (by_claude / by_codex), the name the client gave in initialize
    return _ok(*_sm().post_json("/api/agent/note", {"subject": a["subject"], "text": a["text"]}))


def _s(desc: str, **props) -> dict:
    req = [k for k, v in props.items() if v.pop("required", False)]
    return {"description": desc, "inputSchema": {"type": "object", "properties": props, "required": req}}


# docs/173 §3.3: a QUESTION never writes. With SM_MCP_MODE=readonly the bridge
# lists and allows only the read tools -- mechanical, the same for every CLI.
READ_ONLY = os.environ.get("SM_MCP_MODE", "").strip().lower() == "readonly"
WRITE_TOOLS = frozenset({"state_edit", "apply_to_live", "undo", "take_live", "note_set", "journal_append", "run_node",
                         "run_wait", "undo_mine", "plan_propose"})


def _visible_tools() -> dict:
    return {n: t for n, t in TOOLS.items() if not (READ_ONLY and n in WRITE_TOOLS)}


TOOLS: dict[str, tuple[dict, Any]] = {
    "sm_status": (_s("What chip is open in the State Manager, its qubits/pairs, how many edits are staged, "
                     "whether the live files drifted, what the agent hook says is running now, and the `notes` people "
                     "and agents pinned on the chip (read them before changing a value). `pin` is the value "
                     "SM_CHIP should hold to tie this bridge to this chip."), t_sm_status),
    "state_get": (_s("Read one value (raw + pointer-resolved) or list a subtree's keys of the open state.json/wiring.json. "
                     "Path is dotted: qubits.q1.xy.operations.x180.amplitude. Empty path = top-level keys. "
                     "`notes` = the notes pinned on that path, the qubit/pair above it, or anything under it.",
                     path={"type": "string", "required": True}), t_state_get),
    "state_search": (_s("Search every leaf of the open state by key/value text (space = AND, | = OR).",
                        query={"type": "string", "required": True}, limit={"type": "integer"}), t_state_search),
    "state_edit": (_s("STAGE one edit into SM's Review tray (nothing reaches the chip until apply_to_live). "
                      "Value may be a number, string, bool, null, list or dict. A 409 offer (type fix, FSP "
                      "amplitude compensation) is returned for you to answer via type_fix / fsp_ack.",
                      path={"type": "string", "required": True}, value={"required": True},
                      reason={"type": "string", "description": "why this value -- goes into the human's journal"},
                      type_fix={"type": "string", "enum": ["convert", "keep"]},
                      fsp_ack={"type": "string", "enum": ["comp", "solo"]}), t_state_edit),
    "tray": (_s("The staged edits waiting in SM's Review tray (each with its actor: agent or human)."), t_tray),
    "take_live": (_s("Pull the chip's live state.json/wiring.json into SM after a node wrote them. Only with an "
                     "EMPTY tray; refuses otherwise. sm_status / state_get say live_diverged when this is needed."),
                  t_take_live),
    "undo": (_s("Undo the most recent staged group IF it is your own. A person's staged edit and an "
                "already-applied value are refused (they are the person's to take back)."), t_undo),
    "undo_mine": (_s("Undo YOUR OWN staged groups from the top of the tray, stopping at the first human entry."), t_undo_mine),
    "run_node": (_s("Run a calibration node through SM (the ONLY way to run hardware): SM checks the gates "
                    "(a refusal comes back as data with `refused` and `how`), runs the node on a scratch copy "
                    "of the state, and puts what it wrote through the door -- applied at once in auto mode, "
                    "parked for the human's approval otherwise. Blocks up to wait_s; if still running, call "
                    "run_wait with the key. Never retry on hardware_contention or host_unreachable; the result's failure.how says what to tell the human.",
                    node={"type": "string", "required": True, "description": "node name or file stem, e.g. 05_power_rabi"},
                    targets={"type": "array", "items": {"type": "string"}, "required": True},
                    reason={"type": "string", "required": True, "description": "WHY now -- goes into the human's journal"},
                    params={"type": "object", "description": "node parameter overrides (never simulate/targets)"},
                    timeout_s={"type": "number"}, wait_s={"type": "number"},
                    approval_id={"type": "string", "description": "an APPROVED run request id (mode ask-all)"},
                    plan_id={"type": "string"}, step={"type": "integer", "description": "the plan step this run is"}), t_run_node),
    "plan_propose": (_s("Propose a PLAN CARD for the human: steps of {node, targets, params?, why}. Nothing runs "
                        "until a person presses Start on the card; then you are told to go and call run_node "
                        "with plan_id + step. Use this for any instruction that would run hardware.",
                        title={"type": "string", "required": True},
                        steps={"type": "array", "required": True, "items": {"type": "object"}},
                        why={"type": "string"}), t_plan_propose),
    "plan_status": (_s("A plan's card as SM records it (steps, runs, writes, approvals); no id = recent plans.",
                       plan_id={"type": "string"}), t_plan_status),
    "run_wait": (_s("Wait (up to wait_s) for a run_node key to finish and return its result.",
                    key={"type": "string", "required": True}, wait_s={"type": "number"}), t_run_wait),
    "approvals": (_s("Writes/runs of yours waiting for a human's approval, and recent decisions."), t_approvals),
    "apply_to_live": (_s("Write the staged tray to the live state.json/wiring.json through SM's one door. "
                         "Refuses if a human edited something in the SM window you have not seen; never forces."),
                      t_apply_to_live),
    "versions": (_s("Recent state snapshots (versions) of the open chip: when, what triggered them, which run. Each row's source says which folder recorded it (this / run / other / unknown): a row from another folder with the same chip name is not this folder's history.",
                    n={"type": "integer"}), t_versions),
    "field_history": (_s("Change-point history of ONE dotted path across snapshots and runs.",
                         path={"type": "string", "required": True}), t_field_history),
    "runs": (_s("Recent experiment runs in the open dataset folder (newest first). Filter by node name, qubit, date.",
                n={"type": "integer"}, experiment={"type": "string"}, qubit={"type": "string"},
                date={"type": "string"}), t_runs),
    "run": (_s("One run in full: parameters, outcomes, fit results, and the absolute paths of its figures, "
               "node.json, data.json, ds_raw.h5 -- Read the figure PNG yourself to look at it.",
               run_id={"type": "integer", "required": True}), t_run),
    "diagnostics": (_s("SM's lint of the open chip: env-schema mismatches, dangling pointers, type problems, physics checks."),
                    t_diagnostics),
    "check_fit": (_s("Deterministic sanity gates over one saved run's fit: outcome, physical bands, raw-data feature "
                     "presence, metric consistency. Pass/suspect/fail per target with reasons. No model involved.",
                     run_id={"type": "integer", "required": True}), t_check_fit),
    "families": (_s("The calibration families SM knows (node name -> family) and which have a case manual."), t_families),
    "family_manual": (_s("The lab's case manual for a family: each figure shape's geometry, what it means, what to do. "
                         "Qualitative by construction.", family={"type": "string", "required": True}), t_family_manual),
    "journal_append": (_s("Write to the calibration journal SM renders for the human (a plain .md in their folder). "
                          "Call it BEFORE running a node and AFTER deciding: text = what you did/are doing, "
                          "reason = WHY (what you saw, what you expect). Link the run and the paths you touched.",
                          text={"type": "string", "required": True}, reason={"type": "string", "required": True},
                          run_id={"type": "integer"}, paths={"type": "array", "items": {"type": "string"}}),
                       t_journal_append),
    "journal_read": (_s("Read today's (or a given day's) journal for the open chip.",
                        date={"type": "string", "description": "YYYY-MM-DD"}), t_journal_read),
    "note_set": (_s("Pin a note on a qubit, pair or dotted path (shown on its row in SM, and to every agent in "
                    "sm_status / state_get), e.g. why a value was left alone. Signed with your client's name.",
                    subject={"type": "string", "required": True}, text={"type": "string", "required": True}),
                 t_note_set),
}


# -------------------------------------------------------------- protocol

_agent_id = "agent"
_WAIT_CAP_S = 30 * 60 - 120          # agent_backend.MCP_TOOL_TIMEOUT_S minus a margin (review R4-9)


def _learn_client(params: dict) -> str:
    """Which CLI is on the other end -- the actor SM stamps on every write.
    ``clientInfo.name`` is what the MCP client says about itself."""
    global _agent_id
    name = str(((params or {}).get("clientInfo") or {}).get("name") or "").lower()
    if "claude" in name:
        _agent_id = "claude"
    elif "codex" in name:
        _agent_id = "codex"
    elif name:
        _agent_id = re.sub(r"[^a-z0-9]+", "", name)[:16] or "agent"
    if _link is not None:
        _link.agent_id = _agent_id
    return _agent_id


def _respond(msg_id, result=None, error=None) -> None:
    out: dict = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    sys.stdout.write(json.dumps(out, default=str) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        _learn_client(params)
        _respond(mid, {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                       "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": SERVER,
                       "instructions": ("QUAM State Manager: the chip's state, runs, figures and history, and the "
                                        "ONE safe door to write state. Read with state_get/runs/run; stage with "
                                        "state_edit; write with apply_to_live; keep the human's journal with "
                                        "journal_append (reason required) before every node you run. After a "
                                        "node wrote state.json itself, call take_live before reading again; "
                                        "run check_fit on every finished run. Never edit state.json or "
                                        "wiring.json directly, and never run `python <node>.py` yourself: "
                                        "nodes run only through run_node, after a plan the human started. "
                                        "sm_status and state_get carry the notes people pinned on the chip "
                                        "-- read them before you change a value.")})
    elif method == "notifications/initialized" or (method or "").startswith("notifications/"):
        return
    elif method == "ping":
        _respond(mid, {})
    elif method == "tools/list":
        _respond(mid, {"tools": [{"name": n, **spec} for n, (spec, _) in _visible_tools().items()]})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in _visible_tools():
            if name in TOOLS:
                _respond(mid, {"content": [{"type": "text", "text": f"{name} is not available in a read-only session"}],
                               "isError": True})
                return
            _respond(mid, error={"code": -32601, "message": f"unknown tool {name!r}"})
            return
        try:
            if _CHIP_PIN and name != "sm_status":
                _chip_facts()                    # review R4-1: the pin guards EVERY tool, not four
            result = TOOLS[name][1](args)
            if isinstance(result, dict) and "chip" not in result and _chip:
                result = {"chip": _chip, **result}
            text = json.dumps(result, indent=1, default=str)
            _respond(mid, {"content": [{"type": "text", "text": text}], "isError": False})
        except ToolError as exc:
            _respond(mid, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except (KeyError, TypeError, ValueError) as exc:
            _respond(mid, {"content": [{"type": "text", "text": f"bad arguments: {exc!r}"}], "isError": True})
        except Exception as exc:  # noqa: BLE001 -- a tool crash must not kill the server
            _respond(mid, {"content": [{"type": "text", "text": f"tool failed: {exc!r}"}], "isError": True})
    elif mid is not None:
        _respond(mid, error={"code": -32601, "message": f"method not found: {method}"})


def main() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    stdin = sys.stdin.buffer
    while True:
        line = stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(msg, list):
            for m in msg:
                handle(m)
        elif isinstance(msg, dict):
            handle(msg)


if __name__ == "__main__":
    main()
