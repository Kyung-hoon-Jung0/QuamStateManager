"""Agent setup routes (docs/173 S7): ``/api/agent/setup/*``.

Only what this PC still needs is shown; every write is a preview first and a
click second, with a backup beside the file (core/agent_setup.py). The one
live check is ``/test``: a real read-only question, timed, answer verbatim.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

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
    try:
        from quam_state_manager.core import scheduler
        f = scheduler.load_settings(_r()._sched_inst()).get("calibrations_folder") or ""
        return str(f) if f and Path(f).is_dir() else None
    except Exception:  # noqa: BLE001
        return None


def _data_folder() -> str | None:
    """The chip's declared data folder (extras.data_folder) else the first
    active dataset root -- the journal's default home is beside it."""
    r = _r()
    store = r._store()
    if store is None:
        return None
    try:
        from quam_state_manager.core.history import extras_data_folder
        from quam_state_manager.core import qualibrate_config as qc
        for raw in extras_data_folder(store.state):
            p = Path(qc._to_native(raw)) if hasattr(qc, "_to_native") else Path(raw)
            if p.is_dir():
                return str(p)
    except Exception:  # noqa: BLE001
        logger.debug("extras.data_folder read failed", exc_info=True)
    try:
        for f in r._dataset_candidate_folders(fast=True):
            if Path(f).is_dir():
                return str(f)
    except Exception:  # noqa: BLE001
        pass
    return None


def _python() -> str:
    return sys.executable


def _detect() -> dict:
    from quam_state_manager.web import chat_api
    try:
        det = chat_api._detect_all()
        return {k: v for k, v in det.items() if not k.startswith("_")}
    except Exception:  # noqa: BLE001
        return {}


page_bp = Blueprint("agent_setup_page", __name__)


@page_bp.route("/agent/setup")
def setup_page():
    """The setup page (docs/173 §3.5): a shell; agent-setup.js renders it."""
    from flask import render_template
    r = _r()
    tmpl = "_agent_setup.html" if request.headers.get("HX-Request") else "base.html"
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
    if cal and not s["context"]:
        todo.append("context")
    s["todo"] = todo
    return jsonify(ok=True, **s)


def _custom_instance(inst) -> bool:
    """Is SM running on an instance dir the hook/bridge would NOT find by
    their default rule? Then the commands carry it explicitly."""
    try:
        from quam_state_manager.core import agent_link
        return Path(str(inst)).resolve() != Path(str(agent_link.instance_dir())).resolve()
    except Exception:  # noqa: BLE001
        return True


def _spec(chip: str | None = None) -> dict:
    inst = current_app.instance_path
    return st.mcp_server_spec(_python(), _repo_root(), instance=str(inst) if _custom_instance(inst) else None, chip=chip)


@setup_bp.route("/connect", methods=["POST"])
def connect():
    """Register SM with a CLI: mcp (both), hooks (claude), allow rules (claude,
    in the calibrations folder). ``apply`` absent = PREVIEW only."""
    data = request.get_json(silent=True) or {}
    backend = str(data.get("backend") or "claude").lower()
    if backend not in ab.BACKENDS:
        return _err("backend must be claude or codex")
    apply = bool(data.get("apply"))
    home = _home()
    inst = current_app.instance_path
    cal = _cal_folder()
    spec = _spec(chip=data.get("chip") or None)
    out: dict = {"ok": True, "backend": backend, "applied": apply, "previews": {}, "writes": {}}
    want_hooks = data.get("hooks", True)
    want_allow = data.get("allow", True)
    if backend == "claude":
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
        st.save_record(inst, {"connected": connected})
        chip = aa._chip_name() if _r()._active_path() else None
        if chip:
            journal_mod.append(inst, chip, f"{backend} connected to SM by {_r()._request_actor()} "
                                           f"({', '.join(out['writes'])})", kind="sm")
    return jsonify(**out)


@setup_bp.route("/disconnect", methods=["POST"])
def disconnect():
    data = request.get_json(silent=True) or {}
    backend = str(data.get("backend") or "claude").lower()
    if backend not in ab.BACKENDS:
        return _err("backend must be claude or codex")
    home = _home()
    inst = current_app.instance_path
    out = {"ok": True, "backend": backend, "removed": {}}
    if backend == "claude":
        out["removed"]["mcp"] = st.remove_claude_mcp(home)
        out["removed"]["hooks"] = st.remove_claude_hooks(home)
    else:
        out["removed"]["mcp"] = st.remove_codex(home)
    rec = st.load_record(inst)
    connected = dict(rec.get("connected") or {})
    connected.pop(backend, None)
    st.save_record(inst, {"connected": connected})
    chip = aa._chip_name() if _r()._active_path() else None
    if chip:
        journal_mod.append(inst, chip, f"{backend} disconnected from SM by {_r()._request_actor()}", kind="sm")
    return jsonify(**out)


@setup_bp.route("/journal", methods=["POST"])
def journal_setup():
    """The journal's home: the folder beside the project data (suggested),
    or any folder the lab names. Mandatory (docs/173): an empty root falls
    back to the instance, and the page keeps asking."""
    data = request.get_json(silent=True) or {}
    inst = current_app.instance_path
    root = str(data.get("root") or "").strip()
    if not root:
        return _err("root required (the suggested folder is beside the data folder)")
    try:
        Path(root).mkdir(parents=True, exist_ok=True)
        p = journal_mod.set_root(inst, root)
    except OSError as exc:
        return _err(f"cannot use {root}: {exc}")
    if "claude_says" in data:
        journal_mod.set_claude_says(inst, bool(data.get("claude_says")))
    chip = aa._chip_name() if _r()._active_path() else None
    if chip:
        journal_mod.append(inst, chip, f"journal folder set to {p} by {_r()._request_actor()}", kind="sm")
    return jsonify(ok=True, root=str(p), claude_says=journal_mod.settings(inst).get("claude_says"))


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
                   data_folder=_data_folder(), written=st.context_written(cal), chip=aa._chip_name())


@setup_bp.route("/context", methods=["POST"])
def context_post():
    """Write the fenced block into CLAUDE(.local).md / AGENTS(.local).md in
    the calibrations folder. ``apply`` absent = preview (the diff)."""
    r = _r()
    store = r._store()
    if store is None:
        return _err("open a chip first", 409)
    data = request.get_json(silent=True) or {}
    cal = data.get("folder") or _cal_folder()
    if not cal or not Path(cal).is_dir():
        return _err("no calibrations folder (set it in the run environment first)", 409)
    answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
    local = data.get("local", True) is not False
    targets = data.get("targets") or ["claude", "codex"]
    targets = [t for t in targets if t in ("claude", "codex")]
    facts = st.detect_facts(store.state, store.wiring)
    block = st.context_block(facts, answers, chip=aa._chip_name(), data_folder=_data_folder())
    previews = {t: st.preview_context(cal, block, target=t, local=local) for t in targets}
    out = {"ok": True, "block": block, "previews": previews, "applied": bool(data.get("apply")), "writes": {}}
    if data.get("apply"):
        for t in targets:
            out["writes"][t] = st.write_context(cal, block, target=t, local=local)
        st.save_record(current_app.instance_path, {"context": {"at": time.time(), "folder": cal, "local": local,
                                                               "targets": targets, "answers": answers}})
        journal_mod.append(current_app.instance_path, aa._chip_name(),
                           f"lab context written to {', '.join(Path(w['file']).name for w in out['writes'].values())} "
                           f"by {r._request_actor()}", kind="sm")
    return jsonify(**out)


@setup_bp.route("/test", methods=["POST"])
def test_call():
    """A REAL read-only question through the chosen CLI: the elapsed time and
    the answer verbatim (docs/173 §3.5-5). Never the driving session."""
    from quam_state_manager.web import chat_api
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    data = request.get_json(silent=True) or {}
    backend = str(data.get("backend") or "claude").lower()
    if backend not in ab.BACKENDS:
        return _err("backend must be claude or codex")
    chip = aa._chip_name()
    t0 = time.time()
    try:
        from quam_state_manager.core import limits
        mode = limits.load(current_app.instance_path, aa._chip_key()).get("mode") or "ask-writes"
        b = chat_api._build_backend(backend, readonly=True, chip=chip, mode=mode, cwd=chat_api._cwd(),
                                    model=data.get("model"))
        res = chat_api._manager().ask(chip, b, str(data.get("text") or "Call sm_status and answer in one line: which chip is open and how many qubits does it have?"))
    except (ValueError, OSError) as exc:
        return _err(f"could not start {backend}: {exc}", 502)
    aid = res["ask_id"]
    deadline = t0 + float(data.get("timeout_s") or 90)
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
    out = {"ok": True, "backend": backend, "elapsed_s": elapsed, "done": done,
           "answer": (texts[-1] if texts else (result or {}).get("summary")) if done else None,
           "failed": bool(result and result.get("failed")), "error": (result or {}).get("error") if result and result.get("failed") else None,
           "tools": [e.get("tool_name") for e in evs if e.get("hook_event_name") == "PostToolUse"]}
    if not done:
        out["error"] = f"no answer within {int(deadline - t0)} s"
    st.save_record(current_app.instance_path, {"tested": {backend: {"at": time.time(), "elapsed_s": elapsed,
                                                                    "ok": done and not out["failed"]}}})
    return jsonify(**out)
