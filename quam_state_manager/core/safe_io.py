"""Conflict-safe I/O for the live QUAM state files.

This module is the single chokepoint for reading and writing the *live*
``state.json`` / ``wiring.json`` that QM experiment programs also access.

The problem: while one process holds ``state.json`` open, an experiment
program's atomic save can fail.  Empirically, on Windows:

* ``os.replace`` (``MoveFileExW``) fails with ``ACCESS_DENIED`` if the target
  has **any** open handle -- the reader's share mode does not matter.
* ``ReplaceFileW`` *succeeds* even while another process holds the target
  open for reading.

So this module:

* **Reads** with ``FILE_SHARE_DELETE`` (via ``CreateFileW``) and slurps the
  bytes in one shot before closing -- the handle is held for microseconds,
  and the share flags let writers using ``ReplaceFileW``/``DeleteFile``
  proceed.  Transient errors (a read landing mid-write against a non-atomic
  writer) are retried.
* **Writes** with ``ReplaceFileW`` on Windows, so the State Manager's own
  "apply to live" never fails just because a reader is attached.

It cannot make an experiment's ``os.replace`` immune to *our* open handle --
no share mode can.  The defence against that is the working-copy design
(:mod:`quam_state_manager.core.working_copy`): live content is read only on
load and on an explicit user sync, never in the background.  This module
keeps each of those reads as short as possible.

On POSIX, renaming over an open file is harmless, so plain ``open`` /
``os.replace`` are used.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# Read retry: a non-atomic external writer can briefly expose a truncated or
# locked file; back off and retry a few times before giving up.
_READ_ATTEMPTS = 4
_READ_BACKOFF_S = 0.15

# Write retry: a replace can hit a transient lock (AV, indexer).
_WRITE_ATTEMPTS = 3
_WRITE_BACKOFF_S = 0.5

# State + wiring are a logical pair. When stat'd before/after a read, the
# mtimes must match -- otherwise an experiment may have atomically updated
# one between our two reads and we'd return a torn snapshot. 4 attempts is
# enough for typical writers; we surface a warning if we still can't settle.
_PAIR_READ_ATTEMPTS = 4


class LiveFileError(OSError):
    """A live state/wiring file could not be read or written after retries."""


# ----------------------------------------------------------------------
# Share-delete open
# ----------------------------------------------------------------------

def _create_file_shared_windows(path: Path) -> int:
    """``CreateFileW`` with all three share flags; return an OS handle (int).

    ``FILE_SHARE_DELETE`` lets writers that delete/rename (``ReplaceFileW``,
    ``DeleteFile``, POSIX-semantics rename) proceed while we read.
    """
    import ctypes
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.restype = wintypes.HANDLE
    create_file.argtypes = [
        wintypes.LPCWSTR,  # lpFileName
        wintypes.DWORD,    # dwDesiredAccess
        wintypes.DWORD,    # dwShareMode
        wintypes.LPVOID,   # lpSecurityAttributes
        wintypes.DWORD,    # dwCreationDisposition
        wintypes.DWORD,    # dwFlagsAndAttributes
        wintypes.HANDLE,   # hTemplateFile
    ]

    handle = create_file(
        str(path),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if not handle or handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


@contextmanager
def open_shared(path: Path | str) -> Iterator:
    """Open *path* for binary reading without blocking a delete/rename writer.

    On Windows the handle carries ``FILE_SHARE_DELETE``.  On POSIX a plain
    ``open`` is used (rename-over-open is harmless there).  Yields a binary
    file object; the handle is always closed on exit.
    """
    path = Path(path)
    if not _IS_WINDOWS:
        f = open(path, "rb")
        try:
            yield f
        finally:
            f.close()
        return

    import ctypes
    import msvcrt

    handle = _create_file_shared_windows(path)
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError:
        ctypes.windll.kernel32.CloseHandle(handle)
        raise
    # fd now owns the handle: closing the file object closes the OS handle.
    f = os.fdopen(fd, "rb")
    try:
        yield f
    finally:
        f.close()


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------

def read_json(path: Path | str) -> dict:
    """Read a single JSON file conflict-safely, with transient retry.

    The handle is held only long enough to slurp the bytes; parsing happens
    after it is closed.  A transient ``FileNotFoundError`` -- e.g. a read that
    lands in the brief window of an external atomic replace -- is retried.
    Raises :class:`FileNotFoundError` if the file stays absent across every
    attempt, or :class:`LiveFileError` if it cannot be read as a valid JSON
    object.
    """
    path = Path(path)
    last_exc: Exception | None = None
    for attempt in range(_READ_ATTEMPTS):
        try:
            with open_shared(path) as f:
                raw = f.read()
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data
        except (OSError, ValueError) as exc:
            # FileNotFoundError can be a transient atomic-replace window;
            # JSONDecodeError a mid-write read; locks are OSError.  Retry all.
            last_exc = exc
            logger.debug("read %s attempt %d failed: %s", path.name, attempt + 1, exc)
            if attempt + 1 < _READ_ATTEMPTS:
                time.sleep(_READ_BACKOFF_S * (attempt + 1))
    if isinstance(last_exc, FileNotFoundError):
        raise last_exc
    if isinstance(last_exc, ValueError):
        # A content problem (bad JSON / not an object) -- retries won't help.
        raise LiveFileError(f"{path.name} is not valid JSON: {last_exc}") from last_exc
    raise LiveFileError(
        f"Could not read {path} after {_READ_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc


def read_state_wiring(folder: Path | str, *, attempts: int | None = None) -> tuple[dict, dict]:
    """Read ``state.json`` + ``wiring.json`` from a live folder, conflict-safe.

    Reads both files inside a *double-checked-mtime* loop: stat both before
    and after the reads, and accept the pair only if neither file's mtime
    changed in between. An experiment that atomically updates both files
    can otherwise let a caller capture state.json from the new version
    plus wiring.json from the old (or vice versa). When the mtimes don't
    settle within ``_PAIR_READ_ATTEMPTS``, a :class:`LiveFileError` is
    raised -- a possibly-torn pair is never returned, because callers may
    adopt it as a sync-point baseline / drift baseline (audit C28, critic
    #6). A torn pair is a transient condition: every caller already treats
    an unreadable live as "not replaced / stale" and retries, which is the
    correct outcome here too.

    ``attempts`` overrides the pair-settle retry budget (default
    ``_PAIR_READ_ATTEMPTS``). An explicit user-click read (e.g. /state/live-diff)
    passes a larger budget so a QUAlibrate save-burst is far less likely to surface
    as a transient before its ``os.replace`` settles; the cheap background poll keeps
    the default. The torn-pair refusal itself is unchanged — only how many times we
    patiently retry before raising.

    Returns ``(state, wiring)`` dicts.  Raises :class:`FileNotFoundError`
    if a file is genuinely absent, or :class:`LiveFileError` on unreadable
    JSON or when the pair never settled (external writer mid-save).
    """
    folder = Path(folder)
    n = attempts if attempts is not None else _PAIR_READ_ATTEMPTS
    state_path = folder / "state.json"
    wiring_path = folder / "wiring.json"
    for attempt in range(n):
        before = _pair_fingerprint(folder)   # (mtime_ns, size) per file — catches
        last_state = read_json(state_path)    # coarse/non-advancing-mtime rewrites a
        last_wiring = read_json(wiring_path)  # float-mtime bracket would miss
        after = _pair_fingerprint(folder)
        if before == after:
            return last_state, last_wiring
        logger.debug(
            "state+wiring read attempt %d saw mtime drift (before=%s after=%s); retrying",
            attempt + 1, before, after,
        )
        time.sleep(_READ_BACKOFF_S * (attempt + 1))
    logger.warning(
        "state+wiring mtimes never settled after %d attempts in %s; refusing to "
        "return a possibly-torn snapshot of an ongoing external write",
        n, folder,
    )
    raise LiveFileError(
        f"state.json + wiring.json in {folder} kept changing across "
        f"{n} read attempts (an external writer is actively "
        "saving) — not returning a possibly-torn pair; try again"
    )


# ----------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------

def _replace_file_windows(replacement: Path, replaced: Path) -> None:
    """``ReplaceFileW(replaced, replacement)`` -- atomically swap content.

    Unlike ``MoveFileExW`` / ``os.replace``, ``ReplaceFileW`` succeeds even
    while another process holds *replaced* open for reading.  Raises
    ``OSError`` on failure.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    replace_file = kernel32.ReplaceFileW
    replace_file.restype = wintypes.BOOL
    replace_file.argtypes = [
        wintypes.LPCWSTR,  # lpReplacedFileName
        wintypes.LPCWSTR,  # lpReplacementFileName
        wintypes.LPCWSTR,  # lpBackupFileName
        wintypes.DWORD,    # dwReplaceFlags
        wintypes.LPVOID,   # lpExclude
        wintypes.LPVOID,   # lpReserved
    ]
    ok = replace_file(str(replaced), str(replacement), None, 0, None, None)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def _fsync_dir(path: Path) -> None:
    """Best-effort fsync of a directory (POSIX only).

    ``os.replace`` is atomic but NOT durable until the parent directory's
    metadata hits the platter — a power cut right after apply-to-live could
    silently lose the rename (the old content, or nothing, reappears on
    reboot). Windows' ``ReplaceFileW`` is already durable, so this is a no-op
    there. Failures are swallowed: durability is best-effort, never worth
    failing an otherwise-complete write over.
    """
    if _IS_WINDOWS:
        return
    try:
        fd = os.open(str(path), os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _replace_into_place(tmp: Path, dst: Path) -> None:
    """Atomically move *tmp* onto *dst*, retrying transient failures.

    On Windows, when *dst* already exists, ``ReplaceFileW`` is used so the
    write succeeds even while a reader holds *dst* open.  Otherwise (POSIX,
    or first-time creation) ``os.replace`` is used, followed by a
    best-effort parent-dir fsync so the rename survives power loss.
    """
    last_exc: Exception | None = None
    for attempt in range(_WRITE_ATTEMPTS):
        try:
            if _IS_WINDOWS and dst.exists():
                _replace_file_windows(tmp, dst)
            else:
                os.replace(str(tmp), str(dst))
                _fsync_dir(dst.parent)
            return
        except OSError as exc:
            last_exc = exc
            logger.debug("replace %s attempt %d failed: %s", dst.name, attempt + 1, exc)
            if attempt + 1 < _WRITE_ATTEMPTS:
                time.sleep(_WRITE_BACKOFF_S * (attempt + 1))

    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    raise LiveFileError(
        f"Could not write {dst} after {_WRITE_ATTEMPTS} attempts: {last_exc}"
    )


def _write_tmp_json(path: Path, data) -> Path:
    """Write *data* as pretty JSON to a ``.tmp`` sibling of *path* (flushed +
    fsync'd) and return the tmp path. The caller swaps it into place."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    return tmp


def _write_tmp_bytes(path: Path, data: bytes) -> Path:
    """Write raw *data* bytes to a ``.tmp`` sibling of *path* (flushed + fsync'd).

    Used to restore a file to EXACT prior bytes (a rollback), where re-serialising
    a parsed dict could reorder/reformat it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return tmp


def atomic_write_json(path: Path | str, data) -> None:
    """Write *data* as pretty JSON to *path* atomically.

    Writes a ``.tmp`` sibling (flushed + fsync'd), then atomically replaces
    *path* with it (see :func:`_replace_into_place`).  Raises
    :class:`LiveFileError` if the write cannot complete.

    *data* may be any JSON-serialisable value (dict, list, primitive); we
    don't enforce a dict here because callers persist mixed shapes (e.g.
    ``workspace_roots.json`` is a list, ``last_session.json`` is a dict).
    """
    path = Path(path)
    _replace_into_place(_write_tmp_json(path, data), path)


def write_state_wiring(folder: Path | str, state: dict, wiring: dict) -> None:
    """Write ``state.json`` + ``wiring.json`` into *folder* as a near-atomic pair.

    True 2-file atomicity isn't achievable without a transaction, but we write
    BOTH ``.tmp`` files (fully flushed + fsync'd) FIRST and only then swap them
    in back-to-back. So the window in which a reader — or a crash — could observe
    new-state + old-wiring shrinks to the two fast ``replace`` calls, instead of
    spanning the second file's write + fsync (critic #2). Combined with
    :func:`read_state_wiring`'s mtime-bracketing (which makes a reader retry
    across exactly this window), a torn pair is effectively unobservable.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "state.json"
    wiring_path = folder / "wiring.json"
    # Snapshot the OLD state bytes so we can roll back if the wiring replace fails
    # AFTER the state replace already landed — otherwise the folder is left holding
    # NEW state + OLD wiring, a torn pair ("a chip that never existed") an
    # experiment could then load. Best-effort: no snapshot ⇒ no rollback (no worse
    # than before). open_shared never blocks a concurrent writer.
    old_state_bytes: bytes | None = None
    if state_path.exists():
        try:
            with open_shared(state_path) as f:
                old_state_bytes = f.read()
        except OSError:
            old_state_bytes = None
    state_tmp = _write_tmp_json(state_path, state)
    try:
        wiring_tmp = _write_tmp_json(wiring_path, wiring)
    except OSError:
        # Couldn't even stage wiring — drop the orphan state tmp, write nothing.
        try:
            state_tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    _replace_into_place(state_tmp, state_path)
    try:
        _replace_into_place(wiring_tmp, wiring_path)
    except OSError:   # LiveFileError is an OSError — covers exhausted retries too
        # State replaced but wiring failed → roll state back to the old bytes so the
        # live pair stays CONSISTENT (old+old) rather than torn (new+old). If the
        # rollback ALSO fails, log loudly and re-raise the original wiring error.
        if old_state_bytes is not None:
            try:
                _replace_into_place(_write_tmp_bytes(state_path, old_state_bytes),
                                    state_path)
            except OSError:
                logger.error(
                    "write_state_wiring: wiring replace failed AND state rollback "
                    "failed for %s — live may hold NEW state + OLD wiring", folder)
        raise


# ----------------------------------------------------------------------
# Metadata -- never opens file content, so never conflicts with a writer
# ----------------------------------------------------------------------

def state_wiring_mtimes(folder: Path | str) -> tuple[float, float]:
    """Return ``(state.json mtime, wiring.json mtime)`` for *folder*.

    Pure ``os.stat`` -- never opens file content, so it never conflicts with
    a concurrent writer.  Raises ``OSError`` if a file is missing.
    """
    folder = Path(folder)
    return (
        (folder / "state.json").stat().st_mtime,
        (folder / "wiring.json").stat().st_mtime,
    )


def _pair_fingerprint(folder: Path) -> tuple:
    """``((state mtime_ns, size), (wiring mtime_ns, size))`` — the torn-pair bracket.

    Stronger than float ``st_mtime``: that's a float (~0.24µs at this epoch,
    discarding NTFS 100ns precision) and can FAIL TO ADVANCE on a same-second /
    coarse / 9p / FAT rewrite (labs write experiment data to SMB shares; the dev
    env is a 9p/WSL mount), so a writer replacing wiring.json between the two reads
    could slip a MIXED pair past a float-mtime bracket. ``st_mtime_ns`` is lossless
    and ``st_size`` changes on virtually every real state save, so this catches it.
    Raises ``OSError`` if a file is missing (same as the caller's read)."""
    st = (folder / "state.json").stat()
    wi = (folder / "wiring.json").stat()
    return ((st.st_mtime_ns, st.st_size), (wi.st_mtime_ns, wi.st_size))
