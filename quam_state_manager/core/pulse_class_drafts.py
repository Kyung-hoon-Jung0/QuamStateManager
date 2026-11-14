"""Draft store for pulse classes made or edited in SM.

A draft is the class source file while it is being edited. It lives under
SM's instance folder, never in the lab's ``quam_config``: a broken file in
``quam_config`` would stop the whole chip from loading, a broken draft only
stops its own preview.

Layout (one folder per scope, a chip or a project, and per class)::

    <instance>/pulse_class_drafts/<scope>/<ClassName>/draft.py
    <instance>/pulse_class_drafts/<scope>/<ClassName>/last_preview.json

This module treats a draft as BYTES. It never imports, compiles or runs one:
the preview runs only in the lab env subprocess
(``generator/run_pulse_draft.py``), started by :func:`preview`. The one look
inside is :func:`import_check`, which parses the text to a syntax tree (no
code object, nothing runs) and reads the last good preview's hash.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tempfile
from pathlib import Path

from quam_state_manager.core import safe_io

DRAFTS_DIR = "pulse_class_drafts"
DRAFT_FILE = "draft.py"
PREVIEW_FILE = "last_preview.json"

#: the three save states and their exact user-facing lines
STATE_DRAFT = "draft"
STATE_SAME = "same"
STATE_DIFFERS = "differs"
STATE_LINES = {
    STATE_DRAFT: "Draft, not saved",
    STATE_SAME: "Saved to {path}, same as draft",
    STATE_DIFFERS: "Saved to {path}, differs from draft",
}
#: not one of the three save states: the draft file itself is gone
STATE_MISSING = "missing"
MISSING_LINE = "The draft file is gone: {path}"
PREVIOUS_LINE = "Previous version: the last preview that ran"

_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TIMEOUT_S = 120


def _scope_key(scope: str) -> str:
    """A folder name for *scope* (a chip or project key). A key that is not
    already a safe folder name is reduced to a short hash of itself, so
    distinct keys keep distinct folders. That covers a Windows device name
    (any case, any extension), which Windows cannot make, and a name ending
    in "." (also "." and ".."), whose dot Windows drops: "chip." would
    otherwise share the folder of "chip". Mixed-case keys are hashed too,
    so case variants stay distinct on Windows."""
    from quam_state_manager.core.pulse_class_template import WINDOWS_DEVICE_NAMES
    if (isinstance(scope, str) and _SAFE.fullmatch(scope) and not scope.endswith(".")
            and scope == scope.casefold()
            and scope.split(".", 1)[0].casefold() not in WINDOWS_DEVICE_NAMES):
        return scope
    return "s_" + hashlib.sha256(str(scope).encode("utf-8")).hexdigest()[:16]


class DraftError(ValueError):
    """A draft cannot be stored (the message is shown to the user as is)."""


def _folder(instance: Path | str, scope: str, class_name: str) -> Path:
    from quam_state_manager.core.pulse_class_template import WINDOWS_DEVICE_NAMES
    if not isinstance(class_name, str) or not _CLASS.fullmatch(class_name):
        raise DraftError(f"Class name {class_name!r} is not a valid Python name")
    if class_name.casefold() in WINDOWS_DEVICE_NAMES:
        raise DraftError(f"Class name {class_name!r} is a reserved Windows device name; "
                         "choose another name")
    return Path(instance) / DRAFTS_DIR / _scope_key(scope) / class_name


def draft_path(instance, scope, class_name) -> Path:
    return _folder(instance, scope, class_name) / DRAFT_FILE


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = safe_io._write_tmp_bytes(path, data)
    safe_io._replace_into_place(tmp, path)


def save_draft(instance, scope, class_name, source: str | bytes) -> str:
    """Write the draft atomically; returns its sha256."""
    data = source.encode("utf-8") if isinstance(source, str) else bytes(source)
    root = _folder(instance, scope, class_name).parent
    if root.is_dir():
        for d in root.iterdir():
            # folder names are case-blind on Windows: two classes that differ
            # only by case would share one draft
            if d.name != class_name and d.name.casefold() == class_name.casefold():
                raise DraftError(f"A draft named {d.name!r} already exists; class names "
                                 "must differ by more than letter case")
    _atomic_bytes(draft_path(instance, scope, class_name), data)
    return sha256_bytes(data)


def load_draft(instance, scope, class_name) -> bytes | None:
    p = draft_path(instance, scope, class_name)
    try:
        return p.read_bytes()
    except FileNotFoundError:
        return None


def list_drafts(instance, scope) -> list[dict]:
    """``[{"class_name", "path", "sha256"}]`` for every draft in *scope*."""
    root = Path(instance) / DRAFTS_DIR / _scope_key(scope)
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        f = d / DRAFT_FILE
        if d.is_dir() and _CLASS.fullmatch(d.name) and f.is_file():
            out.append({"class_name": d.name, "path": str(f),
                        "sha256": sha256_bytes(f.read_bytes())})
    return out


def save_state(draft: bytes, target: Path | str | None) -> dict:
    """Compare the draft bytes with the saved class file at *target*.

    ``{"state", "line", "draft_sha256", "file_sha256"}``; no file (or no
    target) is "draft, not saved"."""
    dh = sha256_bytes(draft)
    fh = None
    if target is not None:
        try:
            fh = sha256_bytes(Path(target).read_bytes())
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            fh = None
    state = STATE_DRAFT if fh is None else STATE_SAME if fh == dh else STATE_DIFFERS
    line = STATE_LINES[state].format(path=str(target) if target is not None else "")
    return {"state": state, "line": line,
            "draft_sha256": dh, "file_sha256": fh}


def missing_state(path: Path | str) -> dict:
    """The status line when the draft file was deleted: it names the missing
    draft instead of comparing a class file against nothing."""
    return {"state": STATE_MISSING, "line": MISSING_LINE.format(path=str(path)),
            "draft_sha256": None, "file_sha256": None}


def import_check(instance, scope, class_name, data: bytes) -> str | None:
    """None when *data* may become a class file; otherwise the reason it may
    not. A class file that does not import stops the whole chip from loading,
    so the draft must parse and its exact bytes must have imported in the lab
    env (the last good preview carries their hash)."""
    try:
        ast.parse(data.decode("utf-8"), filename=DRAFT_FILE)
    except UnicodeDecodeError:
        return "The draft is not UTF-8 text, so it cannot be a Python class file."
    except SyntaxError as exc:
        where = f" on line {exc.lineno}" if exc.lineno else ""
        return f"The draft does not parse as Python{where}: {exc.msg}. Fix it before saving it as a class file."
    prev = last_preview(instance, scope, class_name)
    if not prev or prev.get("draft_sha256") != sha256_bytes(data):
        return ("This version of the draft has not imported in a lab env yet. Run the preview; "
                "the class file is written once the preview of this exact draft succeeded.")
    return None


def last_preview(instance, scope, class_name) -> dict | None:
    p = _folder(instance, scope, class_name) / PREVIEW_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def _store_preview(instance, scope, class_name, rec: dict) -> None:
    p = _folder(instance, scope, class_name) / PREVIEW_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    safe_io.atomic_write_json(p, rec)


def _run_runner(python_path: str, request: dict, timeout: int) -> dict:
    """One cold subprocess of the lab-side runner in the user's chosen env."""
    from quam_state_manager.core import lab_worker
    from quam_state_manager.core.config_generator import (
        _blank_outcome, _cleanup_work_dir, _run_script_outcome, _script_path)
    if not python_path:
        return {"ok": False, "error": "No Python environment is chosen for the preview."}
    if not Path(python_path).is_file():
        return {"ok": False, "error": "The chosen Python environment no longer exists."}
    outcome = _blank_outcome()
    work_dir = Path(tempfile.mkdtemp(prefix="quampcdraft_"))
    try:
        (work_dir / "_in.json").write_text(json.dumps(request), encoding="utf-8")
        _run_script_outcome(
            [python_path, "-B", str(_script_path("run_pulse_draft.py")),
             "--in", str(work_dir / "_in.json"), "--out", str(work_dir / "_result.json")],
            work_dir, timeout, outcome, runner=lab_worker.command,
            no_result_label="pulse draft preview",
            error_fallback="the draft preview failed")
    finally:
        _cleanup_work_dir(work_dir)
    res = outcome.get("result")
    if isinstance(res, dict) and "ok" in res:
        return res
    return {"ok": False, "error": outcome.get("error") or "the draft preview failed"}


def preview(python_path: str, instance, scope, class_name, *, params: dict | None = None,
            sampling_rate: float = 1e9, amplitudes: tuple = (0.1, 0.2),
            timeout: int = TIMEOUT_S, runner=None) -> dict:
    """Ask the lab env for the draft's fields, samples and proportional check.

    Success: the result is stored as the last good preview and returned with
    ``"previous": None``. Failure: the result carries the Python error (and
    ``line`` when it points into the draft) plus ``"previous"``: the last good
    preview, untouched, with :data:`PREVIOUS_LINE`."""
    path = draft_path(instance, scope, class_name)
    data = load_draft(instance, scope, class_name)
    if data is None:
        return {"ok": False, "error": "There is no draft for this class.", "previous": None}
    request = {"draft": str(path), "class_name": class_name, "params": params or {},
               "sampling_rate": sampling_rate, "amplitudes": list(amplitudes)}
    res = (runner or _run_runner)(python_path, request, timeout)
    res = dict(res)
    mine = sha256_bytes(data)
    res["draft_sha256"] = mine
    if res.get("ok") and res.get("sha256") != mine:
        # the runner read other bytes than SM hashed (the draft changed in
        # between): this result is not a preview of this draft
        res["ok"] = False
        res["error"] = "The draft changed while the preview ran; run the preview again."
    if res.get("ok"):
        res["previous"] = None
        _store_preview(instance, scope, class_name, res)
        return res
    prev = last_preview(instance, scope, class_name)
    res["previous"] = ({"line": PREVIOUS_LINE, "preview": prev} if prev else None)
    return res
