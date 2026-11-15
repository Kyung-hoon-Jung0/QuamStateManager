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
  The choice that answers the question names the file content it was asked
  about (``file_sha``); when the file moved again, the question is asked again.
* Region rule: a pane save may not change the template region (the markers
  and the lines between them) unless the user unlocked those lines; the form
  is their owner.
* Save target: the class file the draft is saved to ("Save to file") is an
  absolute path, kept next to the draft so the status line can name it. A
  file that is already there is replaced only after the user confirmed that
  exact content, and a copy of it is kept next to it.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_template as T

TARGET_FILE = "target.json"
# What Save to quam_config published; Save to file never writes it.
PUBLISHED_FILE = "published.json"

CONFLICT_QUESTION = ("The draft file changed outside SM while this editor has changes "
                     "that are not saved. Which one do you keep?")
REPLACE_QUESTION = "A file is already there. Replace it with this draft?"
SETTLE_PAUSE_S = 0.25
SETTLE_TRIES = 8
# Save to file refuses a target that has any ancestor folder with this name
# (letter case ignored), on the path as typed and on the resolved path.
PACKAGE_FOLDER_NAME = "quam_config"
PACKAGE_SENTENCE = ("{parent} is a quam_config folder; use Save to quam_config, "
                    "which checks the class before it writes the file.")
PUBLISHED_SENTENCE = ("{target} was saved with Save to quam_config; use Save to quam_config "
                      "again, which checks the chip with the new code first.")
NO_DRAFT_SENTENCE = "There is no draft for this class."
INVALID_TARGET_SENTENCE = "Choose a valid full path for the class file."


_KNOWN_MAX = 8


def file_stat(path: Path | str):
    """``(size, mtime_ns)`` or None when there is no file."""
    try:
        st = Path(path).stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return (st.st_size, st.st_mtime_ns)


def conflict_block(path: Path | str, file_sha: str | None = None) -> dict:
    """The one question and its two choices; each choice names what it loses.
    ``file_sha`` is the file content the question is about: the answer sends
    it back, and a file that moved since is asked about again."""
    return {
        "question": CONFLICT_QUESTION,
        "file_sha": file_sha,
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
        self.known: dict = {}    # sha -> text of the last few settled contents

    def _remember(self, sha: str, text: str) -> None:
        self.known.pop(sha, None)
        self.known[sha] = text
        while len(self.known) > _KNOWN_MAX:
            self.known.pop(next(iter(self.known)))

    def adopt(self, data: bytes) -> None:
        """SM wrote *data* itself: it is the settled content, no re-read."""
        with self._lock:
            st = file_stat(self.path)
            self._seen = self._settled = st
            self.sha = D.sha256_bytes(data)
            self.text = data.decode("utf-8", errors="replace")
            self.info = _describe(self.text)
            self._remember(self.sha, self.text)

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
            self._remember(sha, self.text)
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


def settled_read(path: Path | str, pause: float = SETTLE_PAUSE_S,
                 tries: int = SETTLE_TRIES) -> bytes | None:
    """The settle rule inside one request: the bytes of *path* once its size
    and mtime are equal across two looks *pause* apart and the read matched
    the stat. None when the file is missing or never settled within *tries*."""
    path = Path(path)
    prev = file_stat(path)
    for _ in range(tries):
        if prev is None:
            return None
        time.sleep(pause)
        st = file_stat(path)
        if st is None or st != prev:
            prev = st
            continue
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        after = file_stat(path)
        if after == st and len(data) == st[0]:
            return data
        prev = after
    return None


def _field_map(spec: dict) -> dict:
    return {f["name"]: (f["type"], f["default"], f["unit"]) for f in spec["fields"]}


def region_change(base_text: str, text: str) -> str | None:
    """None when *text* keeps the template region of *base_text* byte for byte
    (or *base_text* has no editable region); otherwise the user-facing reason,
    naming what changed."""
    old = _describe(base_text)
    if not old["protect"]:
        return None
    a, b = old["protect"]
    new = _describe(text)
    if new["protect"]:
        c, d = new["protect"]
        if text[c:d] == base_text[a:b]:
            return None
    tail = (" The form owns these lines: change them in the form, or press "
            "\"Edit the template lines by hand\" first.")
    if not new["protect"] or new["spec"] is None:
        return "This save breaks the template region markers or their lines." + tail
    old_spec, new_spec = old["spec"], new["spec"]
    of, nf = _field_map(old_spec), _field_map(new_spec)
    names = [n for n in of if n not in nf or of[n] != nf[n]] + [n for n in nf if n not in of]
    if names:
        what = ", ".join(f"'{n}'" for n in names)
        noun = "field" if len(names) == 1 else "fields"
        return f"This save changes the template line of {noun} {what}." + tail
    if old_spec["formula"] != new_spec["formula"]:
        return "This save changes the template formula." + tail
    return "This save changes the template region." + tail


def save_from_pane(instance, scope, class_name, text: str, base_sha: str | None,
                   keep: str | None = None, file_sha: str | None = None,
                   region_ok: bool = False) -> dict:
    """Write the pane's *text* to the draft unless the file moved under it.

    ``keep`` None: when the file's sha differs from *base_sha* nothing is
    written and the result is ``{"ok": False, "conflict": {...}}``.
    ``keep="sm"`` writes the pane's text; ``keep="file"`` writes nothing and
    returns the file's content for the pane to load, read by the settle rule.
    Either choice carries the ``file_sha`` of the question it answers; when
    the file is no longer that content, the question comes back.
    ``region_ok`` False (a pane save with the template lines locked) refuses
    a change to the template region; the form door passes True. With neither
    a known base nor a file, there is no earlier template line to compare,
    so only the marker/spec shape is checked."""
    path = D.draft_path(instance, scope, class_name)
    if keep not in (None, "sm", "file"):
        return {"ok": False, "error": "Choose which side to keep."}
    w = watch_for(instance, scope, class_name)
    if keep == "file":
        disk = settled_read(path)
        if disk is None:
            if D.load_draft(instance, scope, class_name) is None:
                return {"ok": False, "error": "There is no draft file to keep."}
            return {"ok": False, "error": "The draft file is still being written; "
                                          "choose again when it has stopped changing."}
        disk_sha = D.sha256_bytes(disk)
        if disk_sha != file_sha:
            return {"ok": False, "conflict": conflict_block(path, disk_sha), "moved": True}
        w.adopt(disk)
        txt = disk.decode("utf-8", errors="replace")
        return {"ok": True, "kept": "file", "sha": disk_sha, "text": txt, "info": _describe(txt)}
    disk = D.load_draft(instance, scope, class_name)
    disk_sha = D.sha256_bytes(disk) if disk is not None else None
    if keep is None and disk is not None and disk_sha != base_sha:
        return {"ok": False, "conflict": conflict_block(path, disk_sha)}
    if keep == "sm" and disk_sha != file_sha:
        return {"ok": False, "conflict": conflict_block(path, disk_sha), "moved": True}
    if not region_ok:
        base_text = w.known.get(base_sha) if base_sha else None
        if base_text is None and disk is not None and disk_sha == base_sha:
            base_text = disk.decode("utf-8", errors="replace")
        if base_text is None and disk is not None:
            return {"ok": False, "base_unknown": True,
                    "error": "The template base is no longer known. To save these lines, press "
                             "\"Edit the template lines by hand\" first, then Save draft."}
        why = region_change(base_text, text) if base_text is not None else None
        if base_text is None:
            info = _describe(text)
            if not info["protect"] or info["spec"] is None:
                why = ("There is no saved draft to compare with, and the form cannot read "
                       "a template region in this text. To save it as code only, press "
                       "\"Edit the template lines by hand\" first, then Save draft.")
        if why:
            return {"ok": False, "error": why, "region": True}
    data = text.encode("utf-8")
    sha = D.save_draft(instance, scope, class_name, data)
    w.adopt(data)
    return {"ok": True, "kept": "sm", "sha": sha, "text": text, "info": _describe(text)}


def _target_record(instance, scope, class_name) -> dict:
    p = D.draft_path(instance, scope, class_name).parent / TARGET_FILE
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def published_record(instance, scope, class_name) -> dict:
    """The file Save to quam_config published for this draft ({} when none).
    Kept apart from the Save to file record, so a copy saved elsewhere does
    not erase it."""
    p = D.draft_path(instance, scope, class_name).parent / PUBLISHED_FILE
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        old = _target_record(instance, scope, class_name)
        return old if old.get("qclass") else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def get_target(instance, scope, class_name) -> str | None:
    """The remembered class file; a stored path that is not absolute (written
    before paths had to be absolute) is not trusted."""
    v = _target_record(instance, scope, class_name).get("path")
    return v if isinstance(v, str) and v and Path(v).is_absolute() else None


def check_target(target: str) -> Path:
    """The class file path *target*, or DraftError naming what is wrong."""
    if not target:
        raise D.DraftError("Type the full path of the class file.")
    t = Path(target)
    if not t.is_absolute():
        raise D.DraftError("The class file must be a full path, starting with its drive "
                           f"or root folder: {target}")
    if ":" in target[len(t.drive):]:
        raise D.DraftError("The class file path cannot contain a colon after the drive.")
    if t.suffix != ".py":
        raise D.DraftError("The class file must end in .py")
    if t.stem.split(".", 1)[0].rstrip(" .").casefold() in T.WINDOWS_DEVICE_NAMES:
        raise D.DraftError(f"{t.name} is a reserved Windows device name; choose another file name.")
    if t.is_dir():
        raise D.DraftError(f"{t} is a folder, not a file; choose a file name inside it.")
    if not t.parent.is_dir():
        raise D.DraftError(f"The folder {t.parent} does not exist")
    return t


def backup_path(t: Path) -> Path:
    """A free name next to *t* for the copy of the file being replaced."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for i in range(1000):
        tail = f"-{i}" if i else ""
        b = t.with_name(f"{t.name}.sm-backup-{stamp}{tail}.bak")
        if not b.exists():
            return b
    raise D.DraftError(f"No free backup name next to {t}")


def package_ancestor(*paths: Path) -> Path | None:
    """The nearest ancestor folder named quam_config (letter case ignored) of
    the first of *paths* that has one, else None. Only the name decides: the
    package folder is always called quam_config, wherever it lives."""
    for path in paths:
        for parent in path.parents:
            if parent.name.casefold() == PACKAGE_FOLDER_NAME:
                return parent
    return None


def save_to_target(instance, scope, class_name, target: str, replace_sha: str | None = None) -> dict:
    """Copy the draft bytes to the class file *target* (an absolute ``.py``
    path in an existing folder) and remember it for the status line.

    The draft must parse and have imported in the lab env
    (:func:`pulse_class_drafts.import_check`). A file already at *target*
    with other bytes is replaced only when *replace_sha* is its sha (the user
    confirmed exactly that content) or it is SM's own last save there; the
    result is otherwise ``{"ok": False, "confirm": {...}}`` and nothing is
    written. A replaced file is copied next to itself first (``backup``)."""
    try:
        if "\x00" in target:
            raise ValueError("embedded null character")
        t = check_target(target)
        resolved = t.resolve()
    except D.DraftError:
        raise
    except (ValueError, OSError) as exc:
        raise D.DraftError(INVALID_TARGET_SENTENCE) from exc
    published = published_record(instance, scope, class_name)
    if published.get("path") == str(t):
        raise D.DraftError(PUBLISHED_SENTENCE.format(target=t))
    # The typed path and the resolved path are both tested, so a link named
    # otherwise that leads into a quam_config folder and a quam_config-named
    # link that leads elsewhere are both refused.
    package = package_ancestor(t, resolved)
    if package is not None:
        raise D.DraftError(PACKAGE_SENTENCE.format(parent=package))
    data = D.load_draft(instance, scope, class_name)
    if data is None:
        raise D.DraftError(NO_DRAFT_SENTENCE)
    why = D.import_check(instance, scope, class_name, data)
    if why:
        raise D.DraftError(why)
    backup = None
    if t.is_file():
        cur = t.read_bytes()
        cur_sha = D.sha256_bytes(cur)
        if cur != data:
            rec = _target_record(instance, scope, class_name)
            own = rec.get("path") == str(t) and rec.get("sha") == cur_sha
            if not own and replace_sha != cur_sha:
                return {"ok": False, "confirm": {
                    "question": REPLACE_QUESTION, "path": str(t), "file_sha": cur_sha,
                    "loses": f"{t} will be replaced; a copy of it is kept next to it."}}
            backup = backup_path(t)
            D._atomic_bytes(backup, cur)
    D._atomic_bytes(t, data)
    folder = D.draft_path(instance, scope, class_name).parent
    D._atomic_bytes(folder / TARGET_FILE,
                    json.dumps({"path": str(t), "sha": D.sha256_bytes(data)}).encode("utf-8"))
    return status(instance, scope, class_name) | {"ok": True, "backup": str(backup) if backup else None}


def status(instance, scope, class_name) -> dict:
    """The status line: one of the three save-state phrases, or the line
    naming a draft file that is gone."""
    data = D.load_draft(instance, scope, class_name)
    if data is None:
        return D.missing_state(D.draft_path(instance, scope, class_name))
    return D.save_state(data, get_target(instance, scope, class_name))
