"""Simulate a node page: scan, start / status / stop, the stored script.

One click builds the picked calibration node on a temporary chip copy and
simulates it on the cloud simulator only (core/nodesim_click.py). The working
copy, the live chip files and the library are never written. One run per
window (``X-SM-Window-ID`` header, or ``window`` in the body / query).
"""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request

from quam_state_manager.core import chip_copy, node_scan
from quam_state_manager.core import nodesim_click as click
from quam_state_manager.web import request_body

nodesim_bp = Blueprint("nodesim", __name__)


def _r():
    from quam_state_manager.web import routes  # noqa: PLC0415 - avoid an import cycle
    return routes


def manager() -> click.Manager:
    m = current_app.config.get("nodesim_manager")
    if m is None:
        m = current_app.config["nodesim_manager"] = click.Manager(current_app.instance_path)
    return m


def _window(data: dict | None = None) -> str:
    return (request.headers.get("X-SM-Window-ID") or (data or {}).get("window")
            or request.args.get("window") or "")


def _saas():
    """The cloud simulator login ladder of the port test (env first, else saved)."""
    from quam_state_manager.web.port_test_routes import _saas_credentials
    return _saas_credentials()


def _env():
    """``(python, env name)`` of the chip's env; python is None when SM may not run it."""
    from quam_state_manager.core import config_generator
    from quam_state_manager.web.diagram_remove_routes import env_name, env_python
    python = env_python()
    if python and not config_generator.known_interpreter(python, current_app.instance_path):
        python = None
    return python, env_name(python)


def node_key(folder, value) -> str | None:
    """The calibrations-relative posix path of a node file, or None when it is not inside the folder.

    Accepts what a link carries: a bare name, a relative path (a node in a subfolder, as the
    Scheduler library lists it) or an absolute path under the folder. ``..`` never leaves it.
    """
    if not folder or not isinstance(value, str) or not value.strip() or not value.endswith(".py"):
        return None
    root = Path(folder).resolve()
    raw = Path(value.strip().replace("\\", "/"))
    try:
        rel = (raw if raw.is_absolute() else root / raw).resolve().relative_to(root)
    except (ValueError, OSError):
        return None
    return rel.as_posix() if rel.parts else None


def _row(info, folder) -> dict:
    reason = click.refusal(info)
    row = info.to_dict()
    estimates = {str(w): click.estimate(info.file, w) for w in click.WINDOWS_US}
    # ``file`` is the calibrations-relative name the start body sends back (never a library path)
    key = node_key(folder, str(info.file)) or Path(info.file).name
    row.update({"file": key, "file_name": Path(info.file).name,
                "refused": bool(reason), "reason": reason,
                "refused_line": click.REFUSED_LIST_LINE if reason else None,
                "estimates": estimates,
                "estimate_lines": {k: v["line"] for k, v in estimates.items()}})
    return row


def _context(want: str | None = None) -> dict:
    """The page's facts: chip, env, library, QOP, the scanned nodes and targets.

    ``want`` (a ``?node=`` value) adds that node when it sits in a subfolder of the library, which
    the folder scan does not list.
    """
    r = _r()
    ctx = r._active_ctx() or {}
    store = r._store() if ctx.get("type", "quam") == "quam" else None
    folder = r._calibrations_folder(ctx)
    library = click.library_root(folder)
    python, env = _env()
    saas = _saas()
    items = node_scan.scan_folder(folder, instance_path=current_app.instance_path) if folder else []
    nodes = []
    for info in items:
        if info.kind == "other":
            continue
        nodes.append(_row(info, folder))
    key = node_key(folder, want)
    if key and not any(n["file"] == key for n in nodes) and (Path(folder) / key).is_file():
        info = node_scan.scan_file(Path(folder) / key)
        if info.kind != "other":
            nodes.append(_row(info, folder))
    merged = getattr(store, "merged", None) or {}
    qubits = store.qubit_names if store else []
    pairs = store.qubit_pair_names if store else []
    active_q = [q for q in merged.get("active_qubit_names") or [] if q in qubits] or qubits[:1]
    active_p = [p for p in merged.get("active_qubit_pair_names") or [] if p in pairs] or pairs[:1]
    chip = getattr(getattr(store, "folder_path", None), "name", None) if store else None
    lib_text = str(library) if library else None
    qop = (saas or {}).get("version")
    return {"chip": chip, "env": env, "python": bool(python), "library": lib_text,
            "context": {"chip": chip, "env": env or None, "library": lib_text, "qop": qop},
            "calibrations": folder or None, "qop": qop,
            "saas_configured": bool(saas), "items": nodes, "qubits": qubits, "pairs": pairs,
            "default_qubit": active_q[0] if active_q else None,
            "default_pair": active_p[0] if active_p else None,
            "active_qubits": active_q, "active_pairs": active_p,
            "selected_qubit": _selected_qubit(qubits),
            "windows_us": list(click.WINDOWS_US), "default_window_us": click.DEFAULT_WINDOW_US}


def _selected_qubit(qubits: list):
    """The qubit selected in the sidebar (the ``qubit`` query argument), when on this chip."""
    q = request.args.get("qubit") or ""
    return q if q in qubits else None


@nodesim_bp.route("/nodesim")
def nodesim_page():
    r = _r()
    want = request.args.get("node") or ""
    facts = _context(want)
    key = node_key(facts.get("calibrations"), want)
    return render_template("nodesim.html", **r._ctx(page="nodesim", ns=facts,
                                                     ns_node=key or want,
                                                     ns_partial=r._is_htmx()))


@nodesim_bp.route("/api/nodesim/nodes")
def nodesim_nodes():
    return jsonify(ok=True, **_context(request.args.get("node")))


@nodesim_bp.route("/api/nodesim/start", methods=["POST"])
def nodesim_start():
    try:
        data = request_body.json_object({})
    except request_body.BadBody as exc:
        return jsonify(ok=False, error=str(exc)), 400
    r = _r()
    ctx = r._active_ctx() or {}
    store = r._store()
    if store is None:
        return jsonify(ok=False, **click.answer("failed", message="Open a chip first.")), 200
    folder = r._calibrations_folder(ctx)
    library = click.library_root(folder)
    python, env = _env()
    node = data.get("node") if isinstance(data.get("node"), str) else ""
    info = None
    key = node_key(folder, node)
    if key:
        path = Path(folder) / key
        info = node_scan.scan_file(path) if path.is_file() else None
    targets = data.get("targets")
    choices = list(store.qubit_names) + list(store.qubit_pair_names)
    live = getattr(ctx.get("working_copy"), "live_folder", None) or ctx.get("path")
    try:
        ans = manager().start(_window(data), python=python, env_name=env, library=library, info=info,
                              targets=targets, choices=choices, window_us=data.get("window_us"),
                              saas=_saas(), store=store, live_folder=live)
    except (chip_copy.CopyError, chip_copy.CreateError) as exc:
        ans = click.answer("failed", message=str(exc))
    ok = ans["state"] in ("running", "starting", "done")
    return jsonify(ok=ok, chip_seq=getattr(store, "mutation_seq", None), **ans), 200


def _seq():
    store = _r()._store()
    if store is None:
        return None, None
    return getattr(store, "mutation_seq", None), chip_copy.store_id(store)


@nodesim_bp.route("/api/nodesim/status")
def nodesim_status():
    seq, sid = _seq()
    ans = manager().status(_window(), request.args.get("id"), seq=seq, store_id=sid)
    return jsonify(ok=True, chip_seq=seq, **ans)


@nodesim_bp.route("/api/nodesim/stop", methods=["POST"])
def nodesim_stop():
    try:
        data = request_body.json_object({})
    except request_body.BadBody as exc:
        return jsonify(ok=False, error=str(exc)), 400
    rid = data.get("id") if isinstance(data.get("id"), str) else None
    return jsonify(ok=True, **manager().stop(_window(data), rid))


@nodesim_bp.route("/api/nodesim/script")
def nodesim_script():
    got = manager().script(request.args.get("run"))
    if got is None:
        return jsonify(ok=False, error="No such simulation run."), 404
    return jsonify(ok=True, **got)


@nodesim_bp.route("/api/nodesim/save-script", methods=["POST"])
def nodesim_save_script():
    try:
        data = request_body.json_object({})
    except request_body.BadBody as exc:
        return jsonify(ok=False, error=str(exc)), 400
    r = _r()
    ctx = r._active_ctx() or {}
    folder = r._calibrations_folder(ctx)
    live = getattr(ctx.get("working_copy"), "live_folder", None) or ctx.get("path")
    forbidden = tuple(x for x in (click.library_root(folder), folder or None, live) if x)
    got = manager().save_script(data.get("run"), data.get("folder"), forbidden=forbidden)
    return jsonify(**got), 200
