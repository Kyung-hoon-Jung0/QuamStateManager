"""The shared class publication door; lab code runs only in a subprocess."""
from __future__ import annotations

import base64
import copy
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from quam_state_manager.core import chip_copy, config_generator, lab_worker, safe_io
from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_template as T
from quam_state_manager.core import pulse_class_watch as W

HISTORY_FILE = "class_files.jsonl"
PROPORTIONAL_WARNING = ("The waveform is not proportional to amplitude; SM cannot scale "
                        "this pulse by its amplitude.")
EXECUTION_NOTICE = "This file is Python that runs without isolation in the lab environment."
RESTART_NEW = ("A running QUAlibrate imports this new file the first time a state needs it; "
               "no restart is needed.")
RESTART_EDIT = ("Restart QUAlibrate before running nodes with this class: a running QUAlibrate "
                "keeps the code it imported before this save.")


def _run(python, request, timeout=120, *, natural=False):
    with tempfile.TemporaryDirectory(prefix="sm_class_save_") as work:
        root = Path(work)
        inp, out = root / "in.json", root / "out.json"
        inp.write_text(json.dumps(request), encoding="utf-8")
        result = lab_worker.run(
            [python, "-B", str(config_generator._script_path("run_class_save.py")),
             "--in", str(inp), "--out", str(out)], timeout=timeout,
            prepend_lab=not natural, cwd=work, text=True, encoding="utf-8",
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        if result.returncode or not out.is_file():
            raise D.DraftError("The lab check could not run: " + result.stderr[-1200:])
        return json.loads(out.read_text(encoding="utf-8"))


def destination(python, *, folder=None, runner=None):
    """Resolve the natural import location before any draft bytes are read."""
    run = runner or _run
    found = run(python, {"action": "discover"}, natural=True)
    if not found.get("ok"):
        raise D.DraftError(found.get("error") or "The environment cannot import quam_config.")
    natural = Path(found["folder"]).resolve()
    configured = config_generator.get_lab_code_dir(python)
    lab = Path(configured).resolve() if configured else natural
    if lab.name != "quam_config":
        lab = lab / "quam_config"
    target = Path(folder) if folder else natural
    if not target.is_absolute():
        raise D.DraftError("Choose a full folder path.")
    target = target.resolve()
    result = {"folder": str(target), "env_folder": str(natural), "lab_folder": str(lab),
              "importable": target == natural, "notice": EXECUTION_NOTICE}
    if target == natural and lab != natural:
        raise D.DraftError(f"The environment imports {natural}, but SM checks {lab}. "
                           "Set SM's lab code folder to the environment's folder before saving.")
    if any(Path(p).resolve() == target or Path(p).resolve() in target.parents
           for p in found.get("site_packages", []) if p):
        raise D.DraftError(f"The folder {target} is in site-packages; choose a writable source folder.")
    if not target.is_dir() or not os.access(target, os.W_OK) or not target.stat().st_mode & 0o222:
        raise D.DraftError(f"The folder {target} is not writable.")
    if not result["importable"]:
        result["warning"] = "The selected environment does not import this file. Gates need a successful load check."
    return result


def _uses(node, qclass, root, seen=None):
    seen = set() if seen is None else seen
    if isinstance(node, str) and node.startswith("#/") and node not in seen:
        seen.add(node)
        try:
            value = root
            for key in node[2:].split("/"):
                value = value[key.replace("~1", "/").replace("~0", "~")]
            return _uses(value, qclass, root, seen)
        except (KeyError, TypeError):
            return False
    if isinstance(node, dict):
        return node.get("__class__") == qclass or any(_uses(v, qclass, root, seen) for v in node.values())
    if isinstance(node, list):
        return any(_uses(v, qclass, root, seen) for v in node)
    return False


def affected_gates(merged, qclass, live=None):
    """Current chip gates using the class, including referenced operations."""
    result = []
    for pair, obj in (merged.get("qubit_pairs") or {}).items():
        for name, macro in (obj.get("macros") or {}).items():
            if name == "cz" and isinstance(macro, str):
                continue
            if not _uses(macro, qclass, merged):
                continue
            live_pair = ((live or {}).get("qubit_pairs") or {}).get(pair) or {}
            live_macros = live_pair.get("macros") or {}
            active = live_macros.get("cz")
            result.append({"pair": pair, "gate": name,
                           "live_active": name in live_macros and _uses(live_macros[name], qclass, live or {})
                           and (active == f"#./{name}" or active == f"#/qubit_pairs/{pair}/macros/{name}"
                                or name == "cz")})
    return result


def record_write(history_folder, *, actor, path, sha256, gates, warning=None):
    """One JSON line per write in the chip's history folder."""
    folder = Path(history_folder)
    folder.mkdir(parents=True, exist_ok=True)
    path_log = folder / HISTORY_FILE
    entry = {"time": datetime.now(timezone.utc).isoformat(), "actor": actor,
             "path": str(path), "sha256": sha256, "gates": gates}
    if warning:
        entry["warning"] = warning
    with safe_io.path_lock(path_log):
        old = path_log.read_bytes() if path_log.exists() else b""
        safe_io.atomic_write_bytes(path_log, old + json.dumps(entry).encode("utf-8") + b"\n")
    return entry


def save_class(python, instance, scope, class_name, *, confirmed_folder=None, folder=None,
               store=None, live=None, history_folder=None, actor="person", runner=None):
    """Discover, confirm, freeze, check, publish and record one class file.

    A caller first omits confirmed_folder to obtain the reviewable destination
    and gate list. It then sends that exact absolute folder back. Source bytes
    and current chip copies are never supplied by the caller.
    """
    if actor not in ("person", "agent"):
        raise D.DraftError("The author must be a person or agent.")
    if not config_generator.known_interpreter(python, instance):
        raise D.DraftError("Choose a Python environment SM found on this computer.")
    dest = destination(python, folder=folder, runner=runner)
    draft = D.draft_path(instance, scope, class_name)
    target = Path(dest["folder"]) / f"{class_name}.py"
    qclass = f"quam_config.{class_name}.{class_name}"
    with store._lock if store else safe_io.path_lock(draft):
        gates = affected_gates(copy.deepcopy(store.merged), qclass, live) if store else []
    if confirmed_folder != dest["folder"]:
        return {"ok": False, "confirm": dest | {"gates": gates, "path": str(target)}}
    if history_folder is None:
        raise D.DraftError("Open a chip so the class write can be recorded in its history.")
    data = D.load_draft(instance, scope, class_name)
    if data is None:
        raise D.DraftError("There is no draft for this class.")
    sha = D.sha256_bytes(data)
    text = data.decode("utf-8")
    if T._header_version(text) is None:
        raise D.DraftError("The draft needs its SM header and template version before saving.")
    with safe_io.path_lock(target):
        old = target.read_bytes() if target.exists() else None
        receipt = W.published_record(instance, scope, class_name)
        if old is not None and not old.startswith(T.HEADER_MARK.encode()):
            raise D.DraftError(f"{target} is a lab class file the environment already imports. "
                               "Pick another class name for this draft.")
        if old is not None and receipt.get("path") != str(target):
            raise D.DraftError(f"A file already exists at {target}. Choose a new class name.")
        run = runner or _run
        backup = None
        for attempt in range(3):
            copies = chip_copy.make_chip_copies(store) if store else None
            try:
                merged = (json.loads((copies.before / "state.json").read_text(encoding="utf-8"))
                          if copies else {})
                if copies:
                    from quam_state_manager.core.loader import merge_state_wiring
                    merged = merge_state_wiring(merged, json.loads(
                        (copies.before / "wiring.json").read_text(encoding="utf-8")))
                    gates = affected_gates(merged, qclass, live)
                used = bool(copies) and _uses(merged, qclass, merged)
                chip = {"chip": str(copies.before) if used else None,
                        "root_class": copies.root_class if copies else None}
                old_error = None
                with tempfile.TemporaryDirectory(prefix="sm_class_package_") as work:
                    package = Path(work) / "quam_config"
                    shutil.copytree(target.parent, package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                    if not (package / "__init__.py").exists():
                        (package / "__init__.py").write_text("", encoding="utf-8")
                    if used and old is not None:
                        # The chip with the file as it is now: a chip that does not
                        # load even before the change cannot judge the draft.
                        before = run(python, {"action": "load", "copy_root": work, **chip})
                        if not before.get("chip_loaded"):
                            old_error = before.get("error") or "the chip copy did not load"
                    check = run(python, {"action": "check", "source": base64.b64encode(data).decode("ascii"),
                                        "class_name": class_name, "filename": target.name,
                                        "copy_root": work, **chip})
                if not check.get("ok") and not (check.get("chip_error") and old_error):
                    raise D.DraftError(check.get("error") or "The lab check failed.")
                if check.get("sha256") != sha:
                    raise D.DraftError("The lab checked different source bytes.")
                if used and not check.get("chip_loaded") and not old_error:
                    raise D.DraftError("The chip copy was not loaded with the draft class.")
                chip_warning = None
                if old_error:
                    chip_warning = (("The chip copy does not load with the old class file either, so "
                                     "this check could not judge the draft against this chip. "
                                     if not check.get("chip_loaded") else
                                     "The chip copy did not load with the old class file; it loads "
                                     "with this draft. ") + f"Old file load error: {old_error}")
                proportional = (check.get("proportional") or {}).get("proportional")
                if proportional is False and T.parse(text).editable:
                    raise D.DraftError("The template waveform must be proportional to amplitude.")
                with store._lock if store else safe_io.path_lock(draft):
                    if copies and not chip_copy.store_at_seq(store, copies):
                        if attempt < 2:
                            continue
                        raise D.DraftError("The chip keeps changing. Try saving again when editing stops.")
                    current = target.read_bytes() if target.exists() else None
                    if current != old:
                        raise D.DraftError("The class file changed during the check. Review it and try again.")
                    if old is not None:
                        backup = Path(instance) / "class_file_backups" / (sha + "_" + D.sha256_bytes(old) + ".py")
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        safe_io.atomic_write_bytes(backup, old)
                    safe_io.atomic_write_bytes(target, data, create_only=old is None)
                break
            finally:
                chip_copy.remove_chip_copies(copies)
        record_error = None
        try:
            record_write(history_folder, actor=actor, path=target, sha256=sha, gates=gates,
                         warning=chip_warning)
        except Exception as exc:
            record_error = f"The class file was saved, but its history line failed: {exc}"
        receipt = {"path": str(target), "sha": sha, "importable": dest["importable"],
                   "qclass": qclass, "proportional": check.get("proportional")}
        try:
            safe_io.atomic_write_json(draft.parent / W.PUBLISHED_FILE, receipt)
            safe_io.atomic_write_json(draft.parent / W.TARGET_FILE, receipt)
        except OSError as exc:
            record_error = (record_error or "") + f" The saved file receipt failed: {exc}"
        registered = None
        if dest["importable"]:
            try:
                registered = _register_module(instance, class_name)
            except OSError as exc:
                record_error = (record_error or "") + f" The class list for the probe failed: {exc}"
        return {"ok": True, "target": str(target), "sha256": sha, "gates": gates,
                "backup": str(backup) if backup else None, "record_error": record_error,
                "status": D.STATE_LINES[D.STATE_SAME].format(path=target),
                "restart": RESTART_EDIT if old is not None else RESTART_NEW,
                "notice": EXECUTION_NOTICE, "warning": dest.get("warning"),
                "dependency": f"This chip now needs quam_config/{target.name}; every computer that opens this state needs the same file.",
                "proportional": check.get("proportional"), "chip_warning": chip_warning,
                "registered": registered,
                "proportional_warning": PROPORTIONAL_WARNING if proportional is False else None}


def _register_module(instance, class_name):
    """Add the saved module to the modules the environment probe imports."""
    from quam_state_manager.core import state_env_schema
    module = "quam_config." + class_name
    modules = state_env_schema.load_pulse_modules(instance)
    state_env_schema.save_pulse_modules(instance, list(dict.fromkeys([*modules, module])))
    return module


def lab_package(python):
    """SM's quam_config folder for *python*, or None when none is set."""
    configured = config_generator.get_lab_code_dir(python)
    if not configured:
        return None
    lab = Path(configured)
    return lab if lab.name == "quam_config" else lab / "quam_config"


def _sm_classes(instance, scope, creates=(), classes=()):
    """``(name, qclass)`` of every SM class draft of *scope* that the gate
    uses: named in *classes*, or used by a value in *creates*. Sorted by
    name, so every caller sees the same order."""
    classes = {c for c in classes if isinstance(c, str)}
    root = Path(instance) / D.DRAFTS_DIR / D._scope_key(scope)
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(root.iterdir(), key=lambda f: f.name):
        if not folder.is_dir():
            continue
        qclass = f"quam_config.{folder.name}.{folder.name}"
        if qclass in classes or any(_uses(value, qclass, {}) for _, value in creates):
            out.append((folder.name, qclass))
    return out


def class_refusal(instance, scope, creates=(), classes=(), lab_folder=None):
    """Why a gate cannot be created yet: it uses (in *creates*, or by name in
    *classes*) an SM class whose file is not saved to quam_config. None when
    nothing stands in the way. Independent of the lab check's fallback.

    A draft that was never published and shares its name with a lab class
    file in *lab_folder* (no SM header) does not stand for that class."""
    for name, _qclass in _sm_classes(instance, scope, creates, classes):
        receipt = W.published_record(instance, scope, name)
        target = receipt.get("path")
        if not target and lab_folder and _lab_made(Path(lab_folder) / f"{name}.py"):
            continue
        if not target or not Path(target).is_file():
            return f"Save {name} to quam_config before creating a gate that uses it."
        if receipt.get("importable") is not True:
            return "The selected environment does not import this class file. Save it to its quam_config folder first."
    return None


def proportional_flag(value):
    """The receipt's proportional result as True, False or None: the lab
    check gives a record ``{"proportional": ...}``, an older receipt a bool."""
    if isinstance(value, dict):
        value = value.get("proportional")
    return value if isinstance(value, bool) else None


def class_files(instance, scope, creates=(), classes=()):
    """The ``extras.sm_class_files`` list of a gate that uses SM classes: one
    entry per published SM class, copied from its publish receipt
    (``qclass``, ``file``, ``sha256``, ``proportional``). Empty when the gate
    uses none. No source text."""
    out = []
    for name, qclass in _sm_classes(instance, scope, creates, classes):
        receipt = W.published_record(instance, scope, name)
        if not receipt.get("path") or not receipt.get("sha"):
            continue
        out.append({"qclass": receipt.get("qclass") or qclass,
                    "file": Path(receipt["path"]).name, "sha256": receipt["sha"],
                    "proportional": proportional_flag(receipt.get("proportional"))})
    return out


def published_path(instance, scope, qclass):
    """The file Save to quam_config published for *qclass*, or None."""
    name = str(qclass).rsplit(".", 1)[-1]
    if not name or instance is None:
        return None
    return W.published_record(instance, scope, name).get("path") or None


def _lab_made(path):
    """True for an existing class file without the SM header."""
    try:
        with open(path, "rb") as fh:
            return not fh.read(len(T.HEADER_MARK)).startswith(T.HEADER_MARK.encode())
    except OSError:
        return False
