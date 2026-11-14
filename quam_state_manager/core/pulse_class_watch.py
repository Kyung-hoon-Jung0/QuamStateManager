"""Watching a pulse-class draft file and saving it from the SM code pane.

Two editors can hold the same draft: the SM code pane and an outside editor
that writes ``draft.py`` directly. This module owns the three rules that keep
them honest.

* Settle rule: a changed file is read only once its size and mtime are equal
  across two polls, and the stat is checked again after the read. A file that
  is still being written (a half-written save) is never read, never parsed.
* Conflict rule: a save from the pane names the draft content it started
  from (``base_sha``). When the file on disk changed since then, nothing is
  written; the caller gets a question that names the side each choice loses.
* Save target: the class file the draft is saved to ("Save to file") is kept
  next to the draft, so the status line can name it.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_template as T

TARGET_FILE = "target.json"

CONFLICT_QUESTION = ("The draft file changed outside SM while this editor has changes "
                     "that are not saved. Which one do you keep?")


def file_stat(path: Path | str):
    """``(size, mtime_ns)`` or None when there is no file."""
    try:
        st = Path(path).stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return (st.st_size, st.st_mtime_ns)


def conflict_block(path: Path | str) -> dict:
    """The one question and its two choices; each choice names what it loses."""
    return {
        "question": CONFLICT_QUESTION,
        "choices": [
            {"keep": "sm", "label": "Keep the SM editor",
             "loses": f"The outside edit in {path} will be lost."},
            {"keep": "file", "label": "Keep the file on disk",
             "loses": "Your unsaved changes in this editor will be lost."},
        ],
    }


def _describe(text: str) -> dict:
    p = T.parse(text)
    spec = None
    if p.spec is not None:
        spec = {"class_name": p.spec.class_name, "formula": p.spec.formula,
                "fields": [{"name": f.name, "type": f.type, "default": f.default, "unit": f.unit}
                           for f in p.spec.fields]}
    protect = None
    if p.editable and p.region:
        b, e = p.region
        bm = text.rfind(T.REGION_BEGIN, 0, b)
        em = text.find(T.REGION_END, e)
        if bm >= 0 and em >= 0:
            # the markers and everything between them: the form owns these lines
            protect = [bm, em + len(T.REGION_END)]
    return {"editable": bool(p.editable), "reason": p.reason, "protect": protect, "spec": spec}


class DraftWatch:
    """Per-draft poll state. ``poll()`` follows the settle rule."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._seen = None        # stat seen on the last poll
        self._settled = None     # stat of the last settled read
        self.sha = None
        self.text = None
        self.info = None

    def adopt(self, data: bytes) -> None:
        """SM wrote *data* itself: it is the settled content, no re-read."""
        with self._lock:
            st = file_stat(self.path)
            self._seen = self._settled = st
            self.sha = D.sha256_bytes(data)
            self.text = data.decode("utf-8", errors="replace")
            self.info = _describe(self.text)

    def poll(self) -> dict:
        """``{"status": "unchanged"|"settling"|"changed"|"missing", "sha"}``;
        "changed" also carries ``text`` and ``info`` (the parsed template)."""
        with self._lock:
            st = file_stat(self.path)
            if st is None:
                self._seen = None
                return {"status": "missing", "sha": self.sha}
            if st == self._settled:
                self._seen = st
                return {"status": "unchanged", "sha": self.sha}
            if st != self._seen:
                self._seen = st
                return {"status": "settling", "sha": self.sha}
            data = self.path.read_bytes()
            after = file_stat(self.path)
            if after != st or len(data) != st[0]:
                # written while we read: wait for it to settle again
                self._seen = after
                return {"status": "settling", "sha": self.sha}
            self._settled = st
            sha = D.sha256_bytes(data)
            if sha == self.sha:
                return {"status": "unchanged", "sha": sha}
            self.sha = sha
            self.text = data.decode("utf-8", errors="replace")
            self.info = _describe(self.text)
            return {"status": "changed", "sha": sha, "text": self.text, "info": self.info}


_WATCHES: dict = {}
_WATCHES_LOCK = threading.Lock()


def watch_for(instance, scope, class_name) -> DraftWatch:
    path = D.draft_path(instance, scope, class_name)
    key = str(path)
    with _WATCHES_LOCK:
        w = _WATCHES.get(key)
        if w is None:
            w = _WATCHES[key] = DraftWatch(path)
            data = D.load_draft(instance, scope, class_name)
            if data is not None:
                w.adopt(data)
        return w


def save_from_pane(instance, scope, class_name, text: str, base_sha: str | None,
                   keep: str | None = None) -> dict:
    """Write the pane's *text* to the draft unless the file moved under it.

    ``keep`` None: when the file's sha differs from *base_sha* nothing is
    written and the result is ``{"ok": False, "conflict": {...}}``.
    ``keep="sm"`` writes the pane's text; ``keep="file"`` writes nothing and
    returns the file's content for the pane to load."""
    path = D.draft_path(instance, scope, class_name)
    disk = D.load_draft(instance, scope, class_name)
    disk_sha = D.sha256_bytes(disk) if disk is not None else None
    w = watch_for(instance, scope, class_name)
    if keep == "file":
        if disk is None:
            return {"ok": False, "error": "There is no draft file to keep."}
        w.adopt(disk)
        txt = disk.decode("utf-8", errors="replace")
        return {"ok": True, "kept": "file", "sha": disk_sha, "text": txt, "info": _describe(txt)}
    if keep is None and disk is not None and disk_sha != base_sha:
        blk = conflict_block(path)
        blk["file_sha"] = disk_sha
        return {"ok": False, "conflict": blk}
    if keep not in (None, "sm"):
        return {"ok": False, "error": "Choose which side to keep."}
    data = text.encode("utf-8")
    sha = D.save_draft(instance, scope, class_name, data)
    w.adopt(data)
    return {"ok": True, "kept": "sm", "sha": sha, "text": text, "info": _describe(text)}


def get_target(instance, scope, class_name) -> str | None:
    p = D.draft_path(instance, scope, class_name).parent / TARGET_FILE
    try:
        v = json.loads(p.read_text(encoding="utf-8")).get("path")
    except (FileNotFoundError, ValueError, AttributeError):
        return None
    return v if isinstance(v, str) and v else None


def save_to_target(instance, scope, class_name, target: str) -> dict:
    """Copy the draft bytes to the class file *target* (a ``.py`` file in an
    existing folder) and remember it for the status line."""
    t = Path(target)
    if t.suffix != ".py":
        raise D.DraftError("The class file must end in .py")
    if not t.parent.is_dir():
        raise D.DraftError(f"The folder {t.parent} does not exist")
    data = D.load_draft(instance, scope, class_name)
    if data is None:
        raise D.DraftError("There is no draft for this class.")
    D._atomic_bytes(t, data)
    folder = D.draft_path(instance, scope, class_name).parent
    D._atomic_bytes(folder / TARGET_FILE, json.dumps({"path": str(t)}).encode("utf-8"))
    return status(instance, scope, class_name)


def status(instance, scope, class_name) -> dict:
    """The status line: always one of the three save-state phrases."""
    data = D.load_draft(instance, scope, class_name) or b""
    return D.save_state(data, get_target(instance, scope, class_name))
