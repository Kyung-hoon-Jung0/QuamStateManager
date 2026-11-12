"""Agent setup routes (docs/173 S7): ``/api/agent/setup/*``.

Only what this PC still needs is shown; every write is a preview first and a
click second, with a backup beside the file (core/agent_setup.py). The one
live check is ``/test``: a real read-only question, timed, answer verbatim.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from quam_state_manager.web.request_body import json_object, json_text

import quam_state_manager
from quam_state_manager.core import agent_backend as ab
from quam_state_manager.core import agent_setup as st
from quam_state_manager.core import journal as journal_mod
from quam_state_manager.web import agent_api as aa

logger = logging.getLogger(__name__)

setup_bp = Blueprint("agent_setup", __name__, url_prefix="/api/agent/setup")


def _err(msg: str, code: int = 400, **extra):
    return jsonify(ok=False, error=msg, **extra), code


def _r():
    from quam_state_manager.web import routes
    return routes


def _home() -> Path | None:
    """Tests point this at a temp home; production = the user's home."""
    h = current_app.config.get("agent_setup_home")
    return Path(h) if h else None


def _repo_root() -> str:
    return str(Path(quam_state_manager.__file__).resolve().parent.parent)


def _cal_folder() -> str | None:
    f = _r()._calibrations_folder()
    return str(f) if f and Path(f).is_dir() else None


def _global_simulate() -> bool:
    """The Runner's ``global_simulate`` ("Dry run") for the open chip. The
    Experiment Runner page that owns the checkbox is hidden since docs/172,
    so this is the agent cockpit's only window onto it; the agent's run_node
    stamps every run with it (core/agent_runs.py). Unreadable -> the
    scheduler's own default (True: a dry run, the safe side)."""
    try:
        from quam_state_manager.core import scheduler
        return bool(scheduler.load_settings(_r()._sched_inst()).get("global_simulate", True))
    except Exception:  # noqa: BLE001
        return True


def _data_folder_src() -> tuple[str | None, str | None]:
    """ONE data folder for the whole page (decisions D-9): the project's
    storage (``routes._scope_storage_path``) -> the chip's declared
    ``extras.data_folder`` -> the first active dataset root."""
    r = _r()
    try:
        p = r._scope_storage_path(r._active_ctx())
        if p and Path(p).is_dir():
            return str(p), "project"
    except Exception:  # noqa: BLE001
        logger.debug("project storage read failed", exc_info=True)
    store = r._store()
    if store is None:
        return None, None
    try:
        from quam_state_manager.core.history import extras_data_folder
        from quam_state_manager.core import qualibrate_config as qc
        for raw in extras_data_folder(store.state):
            n = qc.native_path(raw)
            if n and n.is_dir():
                return str(n), "chip"
    except Exception:  # noqa: BLE001
        logger.debug("extras.data_folder read failed", exc_info=True)
    try:
        for f in r._dataset_candidate_folders(fast=True):
            if Path(f).is_dir():
                return str(f), "dataset"
    except Exception:  # noqa: BLE001
        pass
    return None, None


def _data_folder() -> str | None:
    return _data_folder_src()[0]


def _same_path(a, b) -> bool:
    if not a or not b:
        return False
    from quam_state_manager.core import calibrations
    return calibrations._norm(a) == calibrations._norm(b)


RUN_COUNT_CAP = 2000


def _count_runs(folder: str | None, cap: int = RUN_COUNT_CAP) -> int:
    """How many run folders (a ``node.json`` in it) sit under *folder*, up to
    three levels deep (project/date/run), capped -- a check, not an index."""
    if not folder or not Path(folder).is_dir():
        return 0
    n = 0
    stack = [(Path(folder), 0)]
    while stack and n < cap:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.name == "node.json" and e.is_file():
                        n += 1
                    elif depth < 3 and e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                        stack.append((Path(e.path), depth + 1))
        except OSError:
            continue
    return n


def run_folder_names(folder: str | None, limit: int = 50) -> list[str]:
    """Names of run folders under *folder* (for comparing the agent's report)."""
    out: list[str] = []
    if not folder or not Path(folder).is_dir():
        return out
    stack = [(Path(folder), 0)]
    while stack and len(out) < limit:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.name == "node.json" and e.is_file():
                        out.append(Path(d).name)
                    elif depth < 3 and e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                        stack.append((Path(e.path), depth + 1))
        except OSError:
            continue
    return out


def _config_names(entry: dict | None, files: dict | None, project: str | None = None) -> bool | None:
    """Does the raw config TEXT carry the entry's raw value (catches a value
    SM mapped from something the file does not say)? None = nothing to check."""
    if not entry or entry.get("source") not in ("own", "inherited") or not entry.get("raw"):
        return None
    f = ((files or {}).get("project" if entry["source"] == "own" else "root") or {}).get("path")
    try:
        text = Path(f).read_text(encoding="utf-8")
    except (OSError, TypeError):
        return False
    raw = str(entry["raw"])
    forms = {raw, raw.replace(chr(92), chr(92) * 2)}
    if project:
        # review r2 P2-6: a value SM substituted from the lazy project template -- only that exact spelling
        t = raw.replace(project, "${#/qualibrate/project}")
        forms |= {t, t.replace(chr(92), chr(92) * 2)}
    return any(f in text for f in forms)


def paths_report() -> dict:
    """Step 2's rows, their provenance, and SM's own four checks (D-1..D-11)."""
    r = _r()
    ctx = r._active_ctx()
    project = (ctx or {}).get("qualibrate_project")
    cal = r._calibrations(ctx)
    data, data_src = _data_folder_src()
    paths = None
    if project:
        try:
            from quam_state_manager.core import qualibrate_config as qc
            paths = qc.project_paths(project)
        except Exception:  # noqa: BLE001
            paths = None
    live = (ctx or {}).get("live_path") or (ctx or {}).get("path")
    state_entry = (paths or {}).get("state_path")
    checks = []
    if state_entry and state_entry.get("native"):
        checks.append({"key": "state", "ok": _same_path(state_entry["native"], live),
                       "label": "The project's state path is the open chip",
                       "detail": f"project: {state_entry['native']} · open: {live}"})
    else:
        checks.append({"key": "state", "ok": None, "label": "The project's state path is the open chip",
                       "detail": "no project state_path to compare" if project else "no project open"})
    nodes: list[str] = []
    if cal.get("folder"):
        try:
            from quam_state_manager.core import node_scan
            nodes = [i.name for i in node_scan.scan_folder(cal["folder"], instance_path=current_app.instance_path)
                     if i.error is None]
        except Exception:  # noqa: BLE001
            nodes = []
    checks.append({"key": "nodes", "ok": bool(nodes) if cal.get("folder") else None,
                   "label": "The calibrations folder holds node files",
                   "detail": f"{len(nodes)} node files" if cal.get("folder") else "no calibrations folder"})
    nruns = _count_runs(data)
    checks.append({"key": "data", "ok": (nruns > 0) if data else None, "label": "Run folders in the data folder",
                   "detail": (f"holds {nruns}{'+' if nruns >= RUN_COUNT_CAP else ''} run folders" if data
                              else "no data folder")})
    names = [_config_names((paths or {}).get(k), (paths or {}).get("files"), project)
             for k in ("calibration_library", "storage", "state_path")]
    named = [x for x in names if x is not None]
    # review r2 P2-6: the row says what is IN FORCE, not only what the file says
    if project and cal.get("source") == "override":
        cfg_ok, cfg_detail = None, "an override is in force: the calibrations folder is not the config's"
    elif project and not cal.get("folder"):
        cfg_ok, cfg_detail = False, "the config's calibrations folder is not usable here"
    elif named:
        cfg_ok = all(named)
        cfg_detail = "each value read from its file" if cfg_ok else "a value SM shows is not written in its file"
    else:
        cfg_ok, cfg_detail = None, "nothing to compare" if project else "no project open"
    checks.append({"key": "config", "ok": cfg_ok, "label": "The config files name these paths", "detail": cfg_detail})
    return {"project": project, "calibrations": cal, "data_folder": data, "data_source": data_src,
            "state_path": state_entry, "storage": (paths or {}).get("storage"), "files": (paths or {}).get("files"),
            "config_dir": (paths or {}).get("config_dir") or cal.get("config_dir"),
            "live_path": str(live) if live else None, "checks": checks, "nodes": nodes[:20]}


def _python() -> str:
    return sys.executable


def _detect() -> dict:
    from quam_state_manager.web import chat_api
    try:
        det = chat_api._detect_all()
        return {k: v for k, v in det.items() if not k.startswith("_")}
    except Exception:  # noqa: BLE001
        return {}


# Setup steps only a TERMINAL claude/codex needs (step 1 of the setup page, marked optional there).
TERMINAL_STEPS = ("connect_claude", "connect_codex", "allow")

page_bp = Blueprint("agent_setup_page", __name__)


@page_bp.route("/agent/setup")
def setup_page():
    """The setup page (docs/173 §3.5): a shell; agent-setup.js renders it."""
    from flask import render_template
    r = _r()
    tmpl = "_agent_setup.html" if r._is_htmx() else "base.html"   # a history restore gets the page
    return render_template(tmpl, **r._ctx(page="agent_setup"))


@setup_bp.route("", methods=["GET"])
@setup_bp.route("/", methods=["GET"])
def setup_status():
    inst = current_app.instance_path
    cal = _cal_folder()
    s = st.status(inst, home=_home(), cal_folder=cal, python=_python(), repo=_repo_root(), detect=_detect())
    s["instance"] = str(inst)
    s["journal"] = {"root": str(journal_mod.root(inst)), "configured": bool(journal_mod.settings(inst).get("root")),
                    "suggested": str(Path(_data_folder()) / "journal") if _data_folder() else None,
                    "claude_says": journal_mod.settings(inst).get("claude_says")}
    s["data_folder"] = _data_folder()
    try:
        s["paths"] = paths_report()
    except Exception:  # noqa: BLE001
        logger.warning("setup paths report failed", exc_info=True)
        s["paths"] = None
    s["global_simulate"] = _global_simulate()
    s["chip"] = aa._chip_name() if _r()._active_path() else None
    s["hook_command"] = st.hook_command(_python(), str(inst) if _custom_instance(inst) else None)
    todo = []
    if not s["clis"].get("claude", {}).get("found") and not s["clis"].get("codex", {}).get("found"):
        todo.append("install")
    if s["clis"].get("claude", {}).get("found") and not (s["claude"]["mcp"] and s["claude"]["hooks"]):
        todo.append("connect_claude")
    if s["clis"].get("codex", {}).get("found") and not s["codex"]["mcp"]:
        todo.append("connect_codex")
    if cal and not s["claude"]["allow"]:
        todo.append("allow")
    if not cal:
        todo.append("calibrations_folder")
    if not s["journal"]["configured"]:
        todo.append("journal")
    if cal and (not s["context"] or s.get("context_unread")):
        # B-02: a block only in AGENTS.local.md is one Codex never reads -- still to do
        todo.append("context")
    s["todo"] = todo
    # the terminal steps are optional (the agent inside SM works without them): "N left" counts the rest
    s["todo_optional"] = [t for t in todo if t in TERMINAL_STEPS]
    s["todo_required"] = [t for t in todo if t not in TERMINAL_STEPS]
    return jsonify(ok=True, **s)


def _custom_instance(inst) -> bool:
    """Is SM running on an instance dir the hook/bridge would NOT find by
    their default rule? Then the commands carry it explicitly."""
    try:
        from quam_state_manager.core import agent_link
        return Path(str(inst)).resolve() != Path(str(agent_link.instance_dir())).resolve()
    except Exception:  # noqa: BLE001
        return True


def _spec(chip: str | None = None, url: str | None = None) -> dict:
    inst = current_app.instance_path
    return st.mcp_server_spec(_python(), _repo_root(), instance=str(inst) if _custom_instance(inst) else None,
                              chip=chip, url=url)


@setup_bp.route("/connect", methods=["POST"])
def connect():
    """Register SM with a CLI: mcp (both), hooks (claude), allow rules (claude,
    in the calibrations folder). ``apply`` absent = PREVIEW only."""
    data = json_object()
    backend = str(data.get("backend") or "claude").lower()
    if backend not in ab.BACKENDS:
        return _err("backend must be claude or codex")
    apply = bool(data.get("apply"))
    home = _home()
    inst = current_app.instance_path
    cal = _cal_folder()
    chip, url = data.get("chip") or None, None
    if data.get("pinned") is True:
        # Same facts as /api/agent/chip, without an HTTP round trip to ourselves.
        facts = aa.chip().get_json()
        chip = facts.get("pin") if facts.get("loaded") else None
        if not chip:
            return _err("Open a chip before writing a pinned entry.")
        if data.get("expected_pin") and data["expected_pin"] != chip:
            return _err("The open chip changed. Preview the pinned entry again.", 409)
        url = request.host_url.rstrip("/")
    spec = _spec(chip=chip, url=url)
    out: dict = {"ok": True, "backend": backend, "applied": apply, "previews": {}, "writes": {},
                 "pin": chip if url else None}
    want_hooks = data.get("hooks", True)
    want_allow = data.get("allow", True)
    if backend == "claude":
        # every JSON file this press would rewrite, checked before the first write
        files = [st.claude_json_path(home)]
        if want_hooks:
            files.append(st.claude_settings_path(home))
        if want_allow and cal:
            files.append(st.allow_path(cal))
        blockers = st.write_blockers(files)
        if blockers:
            return _err("; ".join(blockers), 409)
        out["previews"]["mcp"] = st.preview_claude_mcp(spec, home)
        if want_hooks:
            cmd = st.hook_command(_python(), str(inst) if _custom_instance(inst) else None)
            out["previews"]["hooks"] = st.preview_claude_hooks(cmd, home)
        if want_allow and cal:
            out["previews"]["allow"] = st.preview_allow(cal)
        if apply:
            out["writes"]["mcp"] = st.write_claude_mcp(spec, home)
            if want_hooks:
                out["writes"]["hooks"] = st.write_claude_hooks(cmd, home)
            if want_allow and cal:
                out["writes"]["allow"] = st.write_allow(cal)
    else:
        out["previews"]["mcp"] = st.preview_codex(spec, home)
        if apply:
            out["writes"]["mcp"] = st.write_codex(spec, home)
    if apply:
        rec = st.load_record(inst)
        connected = dict(rec.get("connected") or {})
        connected[backend] = {"at": time.time(), "by": _r()._request_actor(),
                              "backups": [w.get("backup") for w in out["writes"].values() if w.get("backup")]}
        if "allow" in out["writes"]:
            # docs/247 (A-16): what Disconnect must take back, and where
            connected[backend]["allow_added"] = out["writes"]["allow"].get("added") or []
            connected[backend]["allow_file"] = out["writes"]["allow"].get("file")
        st.save_record(inst, {"connected": connected})
        chip = aa._chip_name() if _r()._active_path() else None
        if chip:
            journal_mod.append(inst, chip, f"{backend} connected to SM by {_r()._request_actor()} "
                                           f"({', '.join(out['writes'])})", kind="sm")
    return jsonify(**out)


@setup_bp.route("/disconnect", methods=["POST"])
def disconnect():
    data = json_object()
    backend = str(data.get("backend") or "claude").lower()
    if backend not in ab.BACKENDS:
        return _err("backend must be claude or codex")
    home = _home()
    inst = current_app.instance_path
    out = {"ok": True, "backend": backend, "removed": {}}
    rec = st.load_record(inst)
    if backend == "claude":
        # docs/247 (A-16/C-14): Disconnect takes back EVERYTHING Connect added -- the allow rules in
        # the calibrations folder too (they were left behind, and the strip still said "allow")
        mine = (rec.get("connected") or {}).get("claude") or {}
        cal = mine.get("allow_file") and str(Path(mine["allow_file"]).parent.parent) or _cal_folder()
        files = [st.claude_json_path(home), st.claude_settings_path(home)]
        if cal:
            files.append(st.allow_path(cal))
        blockers = st.write_blockers(files)
        if blockers:
            return _err("; ".join(blockers), 409)
        out["removed"]["mcp"] = st.remove_claude_mcp(home)
        out["removed"]["hooks"] = st.remove_claude_hooks(home)
        if cal:
            out["removed"]["allow"] = st.remove_allow(cal, mine.get("allow_added") if "allow_added" in mine else None)
    else:
        out["removed"]["mcp"] = st.remove_codex(home)
    connected = dict(rec.get("connected") or {})
    connected.pop(backend, None)
    st.save_record(inst, {"connected": connected})
    chip = aa._chip_name() if _r()._active_path() else None
    if chip:
        journal_mod.append(inst, chip, f"{backend} disconnected from SM by {_r()._request_actor()}", kind="sm")
    return jsonify(**out)


@setup_bp.route("/calibrations", methods=["POST"])
def calibrations_override():
    """Set or clear the person's calibrations-folder override for the scoped
    project (decisions D-3/D-4/D-6). ``folder`` = a folder chosen in the
    folder browser; ``use_legacy`` = take the previous SM setting (one
    click); ``clear`` = use the project value again. Never writes
    ``~/.qualibrate`` and never the scheduler setting."""
    from quam_state_manager.core import calibrations
    r = _r()
    ctx = r._active_ctx()
    project = (ctx or {}).get("qualibrate_project")
    data = json_object()
    if not project:
        return _err("no QUAlibrate project is open — open one on the landing page", 409)
    cal = r._calibrations(ctx)
    cdir = cal.get("config_dir") or ""
    if data.get("clear"):
        calibrations.set_override(current_app.instance_path, project, cdir, None, by=r._request_actor())
        return jsonify(ok=True, calibrations=r._calibrations(ctx))
    if data.get("use_legacy"):
        folder = cal.get("legacy") if cal.get("legacy_ok") else None
        if not folder:
            return _err("there is no previous SM setting to use", 409)
    else:
        folder = json_text(data, "folder")
    if not folder or not Path(folder).expanduser().is_absolute() or not Path(folder).is_dir():
        return _err(f"not an existing folder: {folder or '(empty)'}")
    calibrations.set_override(current_app.instance_path, project, cdir, str(Path(folder)), by=r._request_actor())
    return jsonify(ok=True, calibrations=r._calibrations(ctx))


@setup_bp.route("/journal", methods=["POST"])
def journal_setup():
    """The journal's home: the folder beside the project data (suggested),
    or any folder the lab names. Mandatory (docs/173): an empty root falls
    back to the instance, and the page keeps asking."""
    data = json_object()
    inst = current_app.instance_path
    root = str(data.get("root") or "").strip()
    if not root:
        return _err("root required (the suggested folder is beside the data folder)")
    chip = aa._chip_name() if _r()._active_path() else None
    try:
        if not Path(root).expanduser().is_absolute():
            # v1.3.0: refused BEFORE the mkdir below -- a relative folder is made
            # in the server's working directory, outside every SM folder
            raise ValueError("the journal folder must be an absolute path")
        Path(root).mkdir(parents=True, exist_ok=True)
        # docs/252 (D-08 / C-15): the same move as /api/agent/journal/root -- the day
        # files come along, and both folders say where the log went
        moved = journal_mod.move_root(inst, root, who=_r()._request_actor(), chip=chip)
        p = moved["root"]
    except (OSError, ValueError) as exc:
        return _err(f"cannot use {root}: {exc}")
    if "claude_says" in data:
        journal_mod.set_claude_says(inst, bool(data.get("claude_says")))
    return jsonify(ok=True, root=str(p), claude_says=journal_mod.settings(inst).get("claude_says"),
                   carried=moved.get("carried") or {})


@setup_bp.route("/context", methods=["GET"])
def context_get():
    """The device facts SM can see and the questions it cannot answer."""
    r = _r()
    store = r._store()
    if store is None:
        return _err("open a chip first", 409)
    cal = _cal_folder()
    nodes = []
    if cal:
        try:
            from quam_state_manager.core import node_scan
            nodes = [i.name for i in node_scan.scan_folder(cal, instance_path=current_app.instance_path) if i.error is None]
        except Exception:  # noqa: BLE001
            nodes = []
    facts = st.detect_facts(store.state, store.wiring, nodes)
    return jsonify(ok=True, facts=facts, questions=st.questions(facts), calibrations_folder=cal,
                   data_folder=_data_folder(), written=st.context_written(cal), chip=aa._chip_name(),
                   saved=st.saved_answers(current_app.instance_path, cal))


@setup_bp.route("/context", methods=["POST"])
def context_post():
    """Write the fenced block into CLAUDE(.local).md / AGENTS(.local).md in
    the calibrations folder. ``apply`` absent = preview (the diff)."""
    r = _r()
    store = r._store()
    if store is None:
        return _err("open a chip first", 409)
    data = json_object()
    cal = json_text(data, "folder") or _cal_folder()
    if not cal or not Path(cal).is_dir():
        return _err("no calibrations folder (set it in the run environment first)", 409)
    answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    # D-21: the evidence of the answers the person ACCEPTED rides along, kept in the record
    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    evidence = {k: [e for e in v if isinstance(e, dict)][:3] for k, v in evidence.items()
                if isinstance(k, str) and isinstance(v, list) and k in answers}
    nt = answers.get("never_touch")
    if isinstance(nt, str):
        nt = [x.strip() for x in nt.split(",") if x.strip()]
    nt = [str(x).strip() for x in (nt or []) if isinstance(x, (str, int, float)) and str(x).strip()][:200]
    if "never_touch" in answers:
        answers = {**answers, "never_touch": nt}
    local = data.get("local", True) is not False
    targets = data.get("targets") or ["claude", "codex"]
    targets = [t for t in targets if t in ("claude", "codex")]
    facts = st.detect_facts(store.state, store.wiring)
    block = st.context_block(facts, answers, chip=aa._chip_name(), data_folder=_data_folder(), evidence=evidence)
    previews = {t: st.preview_context(cal, block, target=t, local=local) for t in targets}
    out = {"ok": True, "block": block, "previews": previews, "applied": bool(data.get("apply")), "writes": {}}
    if data.get("apply"):
        for t in targets:
            out["writes"][t] = st.write_context(cal, block, target=t, local=local)
        st.save_record(current_app.instance_path, {"context": {"at": time.time(), "folder": cal, "local": local,
                                                               "targets": targets, "answers": answers,
                                                               "answers_evidence": evidence, "never_touch": nt}})
        if "never_touch" in answers:
            k = _ask_key()
            st.save_never_touch(current_app.instance_path, k["project"], k["chip"], nt)
        journal_mod.append(current_app.instance_path, aa._chip_name(),
                           f"lab context written to {', '.join(Path(w['file']).name for w in out['writes'].values())} "
                           f"by {r._request_actor()}", kind="sm")
    return jsonify(**out)


def _ask_and_wait(backend: str, prompt: str, timeout_s: float, model=None) -> dict:
    """ONE read-only ask through the chosen CLI (F-8: ``readonly=True``, the
    ``-ro`` MCP config), waited for here. Both backends, one shape (D-15)."""
    from quam_state_manager.web import chat_api
    chip = aa._chip_name()
    t0 = time.time()
    from quam_state_manager.core import limits
    mode = limits.load(current_app.instance_path, aa._chip_key()).get("mode") or "ask-writes"
    b = chat_api._build_backend(backend, readonly=True, chip=chip, mode=mode, cwd=chat_api._cwd(), model=model)
    res = chat_api._manager().ask(chip, b, prompt)
    aid = res["ask_id"]
    deadline = t0 + float(timeout_s)
    asks = current_app.config.setdefault("agent_asks", {})
    asks.setdefault(aid, [])
    mgr = chat_api._manager()
    while time.time() < deadline:
        evs = list(asks.get(aid) or [])
        alive = mgr.ask_alive(aid)
        if any(e.get("hook_event_name") == "Stop" for e in evs) or alive is False:
            break
        time.sleep(0.25)
    evs = list(asks.get(aid) or [])
    texts = [e.get("text") for e in evs if e.get("hook_event_name") == "Text" and e.get("text")]
    result = next((e for e in reversed(evs) if e.get("hook_event_name") in ("Result", "Error")), None)
    done = any(e.get("hook_event_name") == "Stop" for e in evs)
    elapsed = round(time.time() - t0, 1)
    final = (texts[-1] if texts else (result or {}).get("summary")) if done else None
    out = {"ask_id": aid, "backend": backend, "elapsed_s": elapsed, "done": done, "answer": final,
           "failed": bool(result and result.get("failed")),
           "error": (result or {}).get("error") if result and result.get("failed") else None,
           "tools": [e.get("tool_name") for e in evs if e.get("hook_event_name") == "PostToolUse"]}
    if not done:
        out["error"] = f"no answer within {int(deadline - t0)} s"
    return out


@setup_bp.route("/test", methods=["POST"])
def test_call():
    """A REAL read-only question through the chosen CLI: the elapsed time and
    the answer verbatim (docs/173 §3.5-5). Never the driving session."""
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    data = json_object()
    backend = str(data.get("backend") or "claude").lower()
    if backend not in ab.BACKENDS:
        return _err("backend must be claude or codex")
    try:
        out = _ask_and_wait(backend, str(data.get("text") or "Call sm_status and answer in one line: which chip is open and how many qubits does it have?"),
                            float(data.get("timeout_s") or 90), model=data.get("model"))
    except (ValueError, OSError) as exc:
        return _err(f"could not start {backend}: {exc}", 502)
    out.pop("ask_id", None)
    st.save_record(current_app.instance_path, {"tested": {backend: {"at": time.time(), "elapsed_s": out["elapsed_s"],
                                                                    "ok": out["done"] and not out["failed"]}}})
    return jsonify(ok=True, **out)


# ------------------------------------------------ Confirm / Investigate by agent

ASK_TIMEOUT_S = 120          # D-15: one timeout for both backends
ASK_KINDS = ("confirm", "investigate")


def _ask_key() -> dict:
    """What a stored result belongs to (D-23)."""
    r = _r()
    ctx = r._active_ctx()
    return {"project": (ctx or {}).get("qualibrate_project"), "chip": aa._chip_key() if r._active_path() else None,
            "calibrations": _cal_folder()}


def _roots() -> dict:
    """The folders an agent may cite (D-17)."""
    r = _r()
    ctx = r._active_ctx()
    cal = r._calibrations(ctx)
    live = (ctx or {}).get("live_path") or (ctx or {}).get("path")
    return {"calibrations": cal.get("folder"), "config": cal.get("config_dir"),
            "state": str(live) if live else None, "data": _data_folder()}


def _state_lookup():
    store = _r()._store()
    if store is None:
        return None
    def look(path: str):
        v = store.get_value(path)
        return v
    return look


def _store_result(kind: str, rec: dict) -> dict:
    res = current_app.config.setdefault("setup_ask_results", {})
    res[kind] = rec
    return rec


def _with_current(rec: dict | None) -> dict | None:
    if not rec:
        return None
    k = _ask_key()
    from quam_state_manager.core import calibrations
    same = (rec.get("key", {}).get("project") == k["project"] and rec.get("key", {}).get("chip") == k["chip"]
            and calibrations._norm(rec.get("key", {}).get("calibrations") or "") == calibrations._norm(k["calibrations"] or ""))
    return {**rec, "current": bool(same)}


def _ask_slot(kind: str):
    """One ask per page at a time (D-23): a held slot answers 409."""
    import threading
    lock = current_app.config.setdefault("setup_ask_lock", threading.Lock())
    busy = current_app.config.setdefault("setup_ask_busy", {})
    with lock:
        if busy.get("kind"):
            return None
        busy["kind"] = kind
        busy["at"] = time.time()
    return busy


def _release(busy: dict) -> None:
    busy.pop("kind", None)


@setup_bp.route("/asks", methods=["GET"])
def asks_get():
    res = current_app.config.get("setup_ask_results") or {}
    busy = current_app.config.get("setup_ask_busy") or {}
    return jsonify(ok=True, running=busy.get("kind"), **{k: _with_current(res.get(k)) for k in ASK_KINDS})


def _sm_values(P: dict) -> dict:
    from quam_state_manager.web import chat_api
    store = _r()._store()
    cal = P.get("calibrations") or {}
    entry = cal.get("entry") or {}
    nodes = []
    if cal.get("folder"):
        try:
            from quam_state_manager.core import node_scan
            nodes = [i.name for i in node_scan.scan_folder(cal["folder"], instance_path=current_app.instance_path)
                     if i.error is None]
        except Exception:  # noqa: BLE001
            nodes = []
    return {"chip_name": aa._chip_name(), "n_qubits": len((store.state.get("qubits") or {})) if store else None,
            "cwd": chat_api._cwd(), "config_file": ((P.get("files") or {}).get("root") or {}).get("path"),
            "state_path_in_config": (P.get("state_path") or {}).get("native"),
            "calibrations_folder_in_config": entry.get("native"),
            "storage_in_config": (P.get("storage") or {}).get("native"),
            "node_files": nodes, "run_folders": run_folder_names(P.get("data_folder"), limit=500)}


@setup_bp.route("/confirm", methods=["POST"])
def confirm():
    """Confirm by agent (D-11..D-15): SM computes its own values; the agent,
    read-only, REPORTS what it sees from its seat; SM compares and checks
    every citation. Writes nothing (D-22)."""
    from quam_state_manager.core import agent_checks as ac
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    data = json_object()
    backend = str(data.get("backend") or "claude").lower()
    if backend not in ab.BACKENDS:
        return _err("backend must be claude or codex")
    busy = _ask_slot("confirm")
    if busy is None:
        return _err("another agent check is running on this page", 409, busy=True)
    try:
        P = paths_report()
        key = _ask_key()
        sm = _sm_values(P)
        try:
            out = _ask_and_wait(backend, ac.confirm_prompt(P.get("config_dir"), P.get("project")), ASK_TIMEOUT_S,
                                model=data.get("model"))
        except (ValueError, OSError) as exc:
            return _err(f"could not start {backend}: {exc}", 502)
        rec = {"kind": "confirm", "backend": backend, "at": time.time(), "key": key, "elapsed_s": out["elapsed_s"],
               "done": out["done"], "failed": out["failed"], "error": out["error"], "tools": out["tools"]}
        if out["done"] and not out["failed"]:
            parsed, why = ac.parse_answer(out["answer"])
            if parsed is None:
                rec.update(parse_error=why, raw=(out["answer"] or "")[:8000])
            else:
                roots = _roots()
                rec.update(ac.compare_confirm(parsed, sm, roots=[roots["calibrations"], roots["config"], roots["state"]],
                                              exist_roots=[roots["data"]], state_lookup=_state_lookup()))
        elif out["answer"]:
            rec["raw"] = str(out["answer"])[:8000]
        st.save_record(current_app.instance_path, {"tested": {backend: {"at": time.time(), "elapsed_s": out["elapsed_s"],
                                                                        "ok": out["done"] and not out["failed"]}}})
        _store_result("confirm", rec)
        return jsonify(ok=True, **_with_current(rec))
    finally:
        _release(busy)


@setup_bp.route("/investigate", methods=["POST"])
def investigate():
    """Investigate by agent (D-16..D-21): one read-only ask carrying the
    question schema. Every answer comes back PROPOSED with its citations
    checked by SM; nothing is saved here (the person's Write context does)."""
    from quam_state_manager.core import agent_checks as ac, node_scan
    r = _r()
    store = r._store()
    if store is None:
        return _err("open a chip first", 409)
    data = json_object()
    backend = str(data.get("backend") or "claude").lower()
    if backend not in ab.BACKENDS:
        return _err("backend must be claude or codex")
    busy = _ask_slot("investigate")
    if busy is None:
        return _err("another agent check is running on this page", 409, busy=True)
    try:
        cal = _cal_folder()
        nodes = []
        if cal:
            try:
                nodes = [i.name for i in node_scan.scan_folder(cal, instance_path=current_app.instance_path) if i.error is None]
            except Exception:  # noqa: BLE001
                nodes = []
        qs = st.questions(st.detect_facts(store.state, store.wiring, nodes))
        roots = _roots()
        key = _ask_key()
        try:
            out = _ask_and_wait(backend, ac.investigate_prompt(qs, roots), ASK_TIMEOUT_S, model=data.get("model"))
        except (ValueError, OSError) as exc:
            return _err(f"could not start {backend}: {exc}", 502)
        rec = {"kind": "investigate", "backend": backend, "at": time.time(), "key": key, "elapsed_s": out["elapsed_s"],
               "done": out["done"], "failed": out["failed"], "error": out["error"], "tools": out["tools"],
               "question_ids": [q["id"] for q in qs]}
        if out["done"] and not out["failed"]:
            parsed, why = ac.parse_answer(out["answer"])
            if parsed is None:
                rec.update(parse_error=why, raw=(out["answer"] or "")[:8000])
            else:
                rec.update(ac.vet_investigate(parsed, qs, roots=[roots["calibrations"], roots["config"], roots["state"]],
                                              exist_roots=[roots["data"]], state_lookup=_state_lookup()))
                rec["raw"] = (out["answer"] or "")[:8000]     # the agent's own words, one click away
        elif out["answer"]:
            rec["raw"] = str(out["answer"])[:8000]
        _store_result("investigate", rec)
        return jsonify(ok=True, **_with_current(rec))
    finally:
        _release(busy)
