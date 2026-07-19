"""Working-copy lifecycle for conflict-safe live-file access.

The State Manager operates on a private *working copy* of a chip's
``state.json`` + ``wiring.json``, kept under ``instance/working_state/<key>/``.
The live files are touched only:

* once, to seed the working copy when a chip is loaded (:func:`create`);
* on an explicit user "sync" (:func:`sync_from_live`);
* on an explicit user "apply to live" (:func:`apply_to_live`).

Background change-detection compares ``os.stat`` mtimes only (:func:`live_changed`)
-- it never opens live content -- so during normal monitoring there is zero
contention with experiment programs.  All live I/O goes through
:mod:`quam_state_manager.core.safe_io`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from quam_state_manager.core import path_match, safe_io
from quam_state_manager.core.history import chip_name_for

logger = logging.getLogger(__name__)

_WORKING_DIRNAME = "working_state"


class StaleLiveError(Exception):
    """Applying to live would clobber an out-of-band change to the live files."""


# ----------------------------------------------------------------------
# Content hashing — the durable "did the live files really change / does
# the working copy really hold edits" signal. Mtimes alone cannot tell a
# value-edit from a whole-chip swap (or survive a backup restore with an
# older mtime); in-memory dirty flags don't survive an app restart or an
# LRU eviction. A content hash recorded at every sync point does both.
# ----------------------------------------------------------------------

def content_hash(state: dict, wiring: dict) -> str:
    """Stable content hash of a parsed ``(state, wiring)`` pair.

    Hashes canonical JSON (sorted keys, tight separators) of the *parsed*
    dicts, so byte-level differences that don't change content (key order,
    whitespace, trailing newline) hash the same — a freshly-read live pair
    can be compared against the working copy regardless of which writer
    serialized each.
    """
    payload = json.dumps([state, wiring], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sanitize(name: str) -> str:
    """Turn a chip name into a safe directory-name fragment."""
    return re.sub(r"[^\w\-.]", "_", name)


def working_state_root(instance_path: str | Path) -> Path:
    """Return ``<instance>/working_state/`` -- the parent of all working copies."""
    return Path(instance_path) / _WORKING_DIRNAME


def key_for(live_folder: str | Path) -> str:
    """Stable key for a live ``quam_state`` folder: ``<chip>-<path-hash>``.

    The chip-name prefix keeps the folder readable; the path hash makes it
    unique per live folder, so two folders of the same chip (the live state
    and a per-experiment copy) never share -- and overwrite -- one working
    copy.  The hash input is :func:`path_match.fs_key` -- the per-OS canonical
    identity (case-folded ONLY on case-insensitive-default hosts).  The old
    unconditional ``.lower()`` aliased *distinct* case-variant dirs on Linux
    onto ONE working copy, and ``apply_to_live`` then wrote the WRONG live
    folder.  The chip name is NFC-folded too (macOS NFD spellings).
    """
    digest = hashlib.sha1(
        path_match.fs_key(live_folder).encode("utf-8")).hexdigest()[:8]
    chip = _sanitize(
        unicodedata.normalize("NFC", chip_name_for(Path(live_folder)))) or "chip"
    return f"{chip}-{digest}"


def _legacy_key_for(live_folder: str | Path) -> str:
    """The pre-``fs_key`` key scheme (sha1 of ``resolve().lower()``).

    Kept ONLY so :func:`load` can migrate an existing Linux/macOS working dir
    -- possibly holding unapplied edits -- onto the new key instead of
    orphaning it (the fold-always hash differs from :func:`key_for` on any
    path with an upper-case character on a POSIX host).
    """
    resolved = str(Path(live_folder).resolve())
    digest = hashlib.sha1(resolved.lower().encode("utf-8")).hexdigest()[:8]
    chip = _sanitize(chip_name_for(Path(live_folder))) or "chip"
    return f"{chip}-{digest}"


@dataclass
class WorkingCopy:
    """A live folder and its private working copy, plus the last-synced mtimes.

    ``synced_live_hash`` is the :func:`content_hash` of the live files at the
    last sync point (create / sync-from-live / apply-to-live). ``None`` for
    working copies persisted before the hash existed (legacy meta) — those
    can't prove they are edit-free, so :func:`reconcile_with_live` treats a
    diverged legacy copy conservatively (kept + prompt, never auto-replaced).
    """

    key: str
    live_folder: Path
    working_folder: Path
    synced_state_mtime: float
    synced_wiring_mtime: float
    synced_live_hash: str | None = None

    def meta_path(self) -> Path:
        """Sidecar meta file -- kept *outside* the working folder so the folder
        stays a pristine state.json + wiring.json mirror."""
        return self.working_folder.parent / f"{self.key}.meta.json"

    def _write_meta(self) -> None:
        """Persist this working copy's tracking state. Raises :class:`OSError`
        on failure -- callers must surface the error rather than silently
        succeeding, because stale meta means future ``live_changed`` checks
        will lie about the live folder.

        Writes atomically via :func:`safe_io.atomic_write_json` so a crash
        mid-write can never leave the meta file half-written.
        """
        meta = {
            "key": self.key,
            "live_folder": str(self.live_folder),
            "synced_state_mtime": self.synced_state_mtime,
            "synced_wiring_mtime": self.synced_wiring_mtime,
            "synced_live_hash": self.synced_live_hash,
            "updated_utc": datetime.now(timezone.utc).isoformat(),
        }
        safe_io.atomic_write_json(self.meta_path(), meta)

    def _write_meta_pair(
        self, state_mtime: float, wiring_mtime: float,
        live_hash: str | None = None,
    ) -> None:
        """Atomically persist a new (state, wiring) mtime pair WITHOUT mutating
        ``self`` until the write succeeds.

        Returns normally on success; raises :class:`OSError` on write failure
        with ``self.synced_*`` left untouched, so a crashed meta write doesn't
        leave the in-memory copy ahead of disk (red-team Phase 1 follow-up,
        finding §4.4). Callers that want both the persistence and the
        in-memory update should call this first and only then assign to
        ``synced_state_mtime`` / ``synced_wiring_mtime``.
        """
        meta = {
            "key": self.key,
            "live_folder": str(self.live_folder),
            "synced_state_mtime": state_mtime,
            "synced_wiring_mtime": wiring_mtime,
            "synced_live_hash": (live_hash if live_hash is not None
                                 else self.synced_live_hash),
            "updated_utc": datetime.now(timezone.utc).isoformat(),
        }
        safe_io.atomic_write_json(self.meta_path(), meta)


# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------

def create(instance_path: str | Path, live_folder: str | Path) -> WorkingCopy:
    """Seed a fresh working copy from the live folder.

    Reads the live ``state.json`` + ``wiring.json`` once (armored) and writes
    them into ``instance/working_state/<key>/``.  Any previous working copy
    for the same live folder is overwritten.
    """
    # RESOLVED so the meta sidecar persists a canonical absolute path: the
    # caller's spelling (relative, symlinked) would otherwise round-trip into
    # the meta and defeat load()'s identity guard in a later session.
    try:
        live = Path(live_folder).resolve()
    except OSError:
        live = Path(live_folder)
    key = key_for(live)
    working = working_state_root(instance_path) / key
    working.mkdir(parents=True, exist_ok=True)

    # Stat BEFORE reading content, so a recorded mtime is never *newer* than
    # the content captured -- the safe direction (worst case is a spurious
    # re-sync prompt, never a missed change).
    state_mt, wiring_mt = safe_io.state_wiring_mtimes(live)
    state, wiring = safe_io.read_state_wiring(live)
    safe_io.write_state_wiring(working, state, wiring)

    wc = WorkingCopy(key, live, working, state_mt, wiring_mt,
                     synced_live_hash=content_hash(state, wiring))
    try:
        wc._write_meta()
    except OSError:
        # Meta write failure here is non-fatal: the working folder + files
        # are valid, only persisted tracking is missing. Log and continue
        # so the user can still use this session; the next load() will
        # return None and re-seed.
        logger.exception("Working copy created but meta write failed for %s", key)
    logger.info("Working copy created: %s -> %s", live, working)
    return wc


def _same_live(recorded: str | Path, requested: str | Path) -> bool:
    """Is the meta's recorded live folder the one being loaded?

    :func:`path_match.same_folder` (``samefile`` ground truth) decides when
    both sides exist; when either is missing, fall back to ``fs_key`` string
    equality -- the same per-OS canonical form the key hash uses, so this
    verdict can never disagree with the key scheme.
    """
    if path_match.same_folder(recorded, requested):
        return True
    return path_match.fs_key(recorded) == path_match.fs_key(requested)


def _migrate_legacy_dir(instance_path: str | Path, live_folder: str | Path,
                        key: str) -> None:
    """Best-effort rename of a pre-``fs_key`` working dir onto the new *key*.

    The legacy scheme hashed ``resolve().lower()``, so on POSIX hosts every
    path with an upper-case character keys differently now.  Those dirs may
    hold unapplied edits -- adopt them, but ONLY when their meta proves they
    belong to *live_folder* (on Linux the legacy fold ALIASED case-variant
    distinct dirs, so the key alone proves nothing).  Never clobbers an
    existing new-scheme copy; failures just log (caller re-seeds from live).
    """
    legacy = _legacy_key_for(live_folder)
    if legacy == key:
        return
    root = working_state_root(instance_path)
    old_dir = root / legacy
    old_meta_path = root / f"{legacy}.meta.json"
    if not old_meta_path.exists() or not (old_dir / "state.json").exists():
        return
    try:
        meta = json.loads(old_meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    recorded = meta.get("live_folder")
    if not recorded or not _same_live(recorded, live_folder):
        return
    new_dir = root / key
    new_meta_path = root / f"{key}.meta.json"
    if new_dir.exists() or new_meta_path.exists():
        return      # a new-scheme copy already exists -- never overwrite it
    try:
        old_dir.rename(new_dir)
        meta["key"] = key
        safe_io.atomic_write_json(new_meta_path, meta)
        old_meta_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Legacy working-copy migration %s -> %s failed",
                       legacy, key, exc_info=True)
        return
    logger.info("Migrated legacy working copy %s -> %s", legacy, key)


def load(instance_path: str | Path, live_folder: str | Path) -> WorkingCopy | None:
    """Reconstruct a :class:`WorkingCopy` from a persisted meta sidecar.

    Returns ``None`` if no usable working copy exists on disk for *live_folder*.
    """
    key = key_for(live_folder)
    working = working_state_root(instance_path) / key
    meta_path = working.parent / f"{key}.meta.json"
    if not meta_path.exists() or not (working / "state.json").exists():
        # Key miss: a dir made under the legacy key scheme may still hold this
        # folder's unapplied edits -- migrate it onto the new key and retry.
        _migrate_legacy_dir(instance_path, live_folder, key)
        if not meta_path.exists() or not (working / "state.json").exists():
            return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # Belt-and-braces identity guard: whatever the key scheme hashed, NEVER
    # hand back a copy whose meta records a DIFFERENT live folder -- serving
    # it cross-wires two chips and apply_to_live writes the wrong live files.
    recorded = meta.get("live_folder")
    if recorded and not _same_live(recorded, live_folder):
        logger.warning(
            "Working copy %s records live folder %r but %r was requested -- "
            "refusing the cross-wired copy (a fresh one will be seeded)",
            key, recorded, str(live_folder))
        return None
    raw_hash = meta.get("synced_live_hash")
    return WorkingCopy(
        key=key,
        live_folder=Path(meta.get("live_folder", live_folder)),
        working_folder=working,
        synced_state_mtime=float(meta.get("synced_state_mtime", 0.0)),
        synced_wiring_mtime=float(meta.get("synced_wiring_mtime", 0.0)),
        synced_live_hash=raw_hash if isinstance(raw_hash, str) else None,
    )


def discard(wc: WorkingCopy) -> None:
    """Remove a working copy's folder and meta sidecar."""
    shutil.rmtree(wc.working_folder, ignore_errors=True)
    try:
        wc.meta_path().unlink(missing_ok=True)
    except OSError:
        pass


# ----------------------------------------------------------------------
# Change detection / sync / apply
# ----------------------------------------------------------------------

def live_changed(wc: WorkingCopy) -> bool:
    """True if the live state/wiring mtimes differ from the last sync.

    Pure ``os.stat`` -- never opens live content.  Raises ``OSError`` if the
    live folder's files are missing.
    """
    state_mt, wiring_mt = safe_io.state_wiring_mtimes(wc.live_folder)
    return (state_mt != wc.synced_state_mtime
            or wiring_mt != wc.synced_wiring_mtime)


def read_live(wc: WorkingCopy, *, attempts: int | None = None) -> tuple[dict, dict]:
    """Armored read of the *live* state + wiring (e.g. for a diff preview).

    ``attempts`` raises the pair-settle retry budget for an explicit user-click read
    (e.g. /state/live-diff) so a QUAlibrate save-burst is far less likely to surface
    as a transient; the background drift poll keeps the cheap default."""
    return safe_io.read_state_wiring(wc.live_folder, attempts=attempts)


def live_diverged_now(wc: WorkingCopy) -> bool | None:
    """Ground-truth divergence probe: does the live content differ from the sync point?

    READ-ONLY — never mutates ``wc`` or its meta. This is the authoritative
    backstop for :func:`live_changed`, whose pure-mtime check false-negatives
    when an external writer (editor save, atomic re-save, coarse/same-second or
    9p/Windows mtime) rewrites content without advancing the float mtime. Reads
    the live files (armored, mtime-bracketed) and hashes them vs the sync-point
    baseline. Returns:

      * ``True``/``False`` vs ``synced_live_hash`` when that baseline exists;
      * ``None`` when the baseline is missing (legacy meta / never persisted) —
        without it we cannot tell "live changed" from "the working copy holds the
        user's saved edits", so we DEFER rather than risk a spurious "live changed"
        prompt (which the user could act on destructively). Legacy metas get a hash
        retrofitted by :func:`_upgrade_legacy_meta`, after which this works normally;
      * ``None`` if the live files can't be read (caller keeps its prior verdict).
    """
    if wc.synced_live_hash is None:
        return None
    try:
        live_hash = content_hash(*safe_io.read_state_wiring(wc.live_folder))
    except (OSError, ValueError):
        return None
    return live_hash != wc.synced_live_hash


# Reconcile outcomes -- see :func:`reconcile_with_live`.
RECONCILE_IN_SYNC = "in_sync"             # live unchanged (or touch-only) -> keep working copy
RECONCILE_SYNCED = "synced"               # live replaced + working clean -> auto-pulled
RECONCILE_STALE = "stale"                 # live replaced + working (possibly) edited -> kept, prompt
RECONCILE_LIVE_UNREADABLE = "live_unreadable"  # live missing/locked -> keep working copy


def _upgrade_legacy_meta(wc: WorkingCopy) -> None:
    """Best-effort: stamp a legacy (pre-hash) meta with ``synced_live_hash``.

    Callable only when the live mtimes still equal the synced mtimes -- then
    the live content *is* the content of the last sync, so hashing it now
    retroactively records the sync-point hash. Re-stats after the read and
    bails if the live moved mid-read (TOCTOU), so a racing experiment write
    can never be mis-recorded as the synced content.
    """
    try:
        state, wiring = safe_io.read_state_wiring(wc.live_folder)
        if live_changed(wc):    # live moved during/after the read -- abort
            return
        cur = content_hash(state, wiring)
        # Set the in-memory baseline FIRST, regardless of whether the disk persist
        # below succeeds: it enables live_diverged_now this session (and a failed
        # write only means the next session re-upgrades — never a stale baseline).
        wc.synced_live_hash = cur
        try:
            wc._write_meta_pair(wc.synced_state_mtime, wc.synced_wiring_mtime, cur)
        except OSError:
            logger.warning("Legacy meta hash retrofit: in-memory set, disk persist failed for %s", wc.key)
        else:
            logger.info("Legacy working-copy meta upgraded with content hash: %s", wc.key)
    except (OSError, ValueError):
        logger.debug("Legacy meta upgrade skipped for %s", wc.key, exc_info=True)


def _is_different_chip(old_state: dict, old_wiring: dict,
                       new_state: dict, new_wiring: dict) -> bool:
    """True iff *new* is a hardware-DIFFERENT chip than *old* (C30).

    Gated on a STRONG signal only: a real, differing network (host/cluster)
    fingerprint. A same-chip value update (qualibrate fit) is ``aligned`` →
    False. When NEITHER side carries network info we can't be sure (could be
    renamed qubits, or network-less fixtures), so we don't block the
    stale-chip-bug auto-pull — only a genuine physical-chip swap, which changes
    the network identity, prompts. Imported lazily to avoid an import cycle
    (history imports loader, which imports this module)."""
    try:
        from quam_state_manager.core import history
        old_fp = history.fingerprint_from_dicts(old_state, old_wiring)
        new_fp = history.fingerprint_from_dicts(new_state, new_wiring)
        if not old_fp.network and not new_fp.network:
            return False  # no hardware signal on either side — don't gate
        return history.align(old_fp, new_fp) == history.ALIGN_DIFFERENT_CHIP
    except Exception:   # noqa: BLE001 — identity is best-effort; never block a sync on it
        logger.debug("chip-identity gate errored; treating as same chip", exc_info=True)
        return False


def _try_sync(wc: WorkingCopy,
              restore: tuple[dict, dict] | None = None) -> str:
    """Auto-pull for a clean working copy; degrade gracefully on failure.

    A transient live-read failure mid-pull (the experiment program's atomic
    save landing between our hash read and the sync's re-read) must not
    break the load -- the working copy is still usable, so report
    ``RECONCILE_STALE``: the caller serves it and shows the "live changed"
    prompt, which is true.

    *restore* is the pre-sync working pair, if the caller already read it.
    ``sync_from_live`` writes state.json then wiring.json as two separate
    atomic replaces; a failure between the two leaves a torn pair (new
    state + old wiring -- a chip that never existed). Putting the old pair
    back keeps the served working copy self-consistent.
    """
    try:
        sync_from_live(wc)
    except (OSError, ValueError):
        logger.warning("Auto-sync after live replacement failed for %s -- "
                       "serving the working copy with a sync prompt", wc.key,
                       exc_info=True)
        if restore is not None:
            try:
                safe_io.write_state_wiring(wc.working_folder, *restore)
            except OSError:
                logger.exception("Pre-sync pair restore failed for %s", wc.key)
        return RECONCILE_STALE
    logger.info("Live files replaced under clean working copy %s -- auto-synced",
                wc.key)
    return RECONCILE_SYNCED


def reconcile_with_live(wc: WorkingCopy, *, sync_if_clean: bool = True) -> str:
    """Content-aware staleness decision for an existing working copy.

    Called on the *load/select* path (never from background polls -- those
    stay ``os.stat``-only). Decides what an out-of-band live-file change
    means for this working copy:

    - ``RECONCILE_IN_SYNC`` -- live unchanged since the last sync (mtime
      short-circuit), or touched without a content change (mtimes refreshed
      so the cheap check stops firing). Working copy served as-is.
    - ``RECONCILE_SYNCED`` -- live content replaced AND the working copy
      held no edits (working hash == synced hash): auto-pulled via
      :func:`sync_from_live`, so the caller serves the *new* live content.
      Only when *sync_if_clean* (callers with unsaved in-memory edits pass
      ``False`` -- their edits make the copy effectively dirty even if the
      on-disk working files are clean).
    - ``RECONCILE_STALE`` -- live content replaced but the working copy has
      (or, for legacy metas, *might* have) unapplied edits. Never clobbered;
      the caller should surface a "live changed -- sync?" prompt.
    - ``RECONCILE_LIVE_UNREADABLE`` -- the live files cannot even be
      stat'ed (missing/locked at step 1). With no evidence of a change,
      the working copy is served unchanged and no prompt is raised. (If
      the stat PROVES the mtimes moved but the subsequent content read
      fails, the verdict is ``RECONCILE_STALE`` instead -- we know
      something changed, so the prompt is honest.)
    """
    # 1. Cheap short-circuit: stat only. The overwhelmingly common case.
    try:
        if not live_changed(wc):
            if wc.synced_live_hash is None:
                # Live still at the sync point -> safe moment to retrofit the
                # hash onto a pre-hash meta (makes FUTURE swaps auto-detectable).
                _upgrade_legacy_meta(wc)
            return RECONCILE_IN_SYNC
    except OSError:
        return RECONCILE_LIVE_UNREADABLE

    # 2. Live mtimes moved -> read both contents and decide by hash.
    try:
        state_mt, wiring_mt = safe_io.state_wiring_mtimes(wc.live_folder)
        live_state, live_wiring = safe_io.read_state_wiring(wc.live_folder)
    except (OSError, ValueError):
        # Step 1 PROVED the live mtimes moved; only the content read failed
        # (mid-replace, transient lock). Unlike a failed stat, we positively
        # know something changed on disk -- keep the working copy but let
        # the caller surface the "live changed" prompt rather than serving
        # the old chip with no hint at all.
        return RECONCILE_STALE
    cur_live_hash = content_hash(live_state, live_wiring)

    try:
        w_state, w_wiring = safe_io.read_state_wiring(wc.working_folder)
        working_hash = content_hash(w_state, w_wiring)
    except ValueError:
        # Genuinely corrupt working files (safe_io's atomic writes make torn
        # JSON impossible, so a parse failure means real corruption) --
        # nothing to preserve. Re-seed from live if allowed.
        if sync_if_clean:
            return _try_sync(wc)
        return RECONCILE_STALE
    except OSError:
        # TRANSIENT read failure (AV/backup/indexer lock) is not proof the
        # copy is worthless -- it may hold saved unapplied edits. Keep it
        # and prompt instead of re-seeding over it.
        return RECONCILE_STALE

    def _adopt(new_hash: str, why: str) -> str:
        """Record (mtimes, hash) as the new sync point; working copy kept."""
        try:
            wc._write_meta_pair(state_mt, wiring_mt, new_hash)
            wc.synced_state_mtime = state_mt
            wc.synced_wiring_mtime = wiring_mt
            wc.synced_live_hash = new_hash
        except OSError:
            logger.exception("Sync-point %s failed for %s", why, wc.key)
        return RECONCILE_IN_SYNC

    if wc.synced_live_hash is not None:
        if cur_live_hash == wc.synced_live_hash:
            # Touch without content change (atomic re-save of identical
            # data, backup restore of the same content): refresh the
            # recorded mtimes so the stat-level check goes quiet again.
            return _adopt(wc.synced_live_hash, "mtime refresh")
        if working_hash == cur_live_hash:
            # Live was replaced with content identical to the working copy
            # (an external writer applied the same edits): nothing to pull
            # or preserve -- adopt the new sync point, no false alarm.
            return _adopt(cur_live_hash, "identical-content adopt")
        dirty = working_hash != wc.synced_live_hash
        if not dirty and sync_if_clean:
            # Identity gate (C30): auto-pull silently only when the live folder
            # still holds the SAME chip. If its files were replaced out-of-band
            # with a DIFFERENT chip (git checkout, backup restore, a retargeted
            # `latest`-style symlink), don't silently swap the displayed chip —
            # surface the "live changed — sync?" prompt so the user confirms the
            # switch. A same-chip qualibrate fit-update (same network + qubits,
            # different values) still auto-pulls as before.
            if _is_different_chip(w_state, w_wiring, live_state, live_wiring):
                logger.info("Live folder %s now holds a DIFFERENT chip than the "
                            "working copy — prompting instead of auto-pulling", wc.key)
                return RECONCILE_STALE
            return _try_sync(wc, restore=(w_state, w_wiring))
        return RECONCILE_STALE

    # 3. Legacy meta (no recorded hash) + live mtimes moved.
    if working_hash == cur_live_hash:
        # Working copy already equals the new live content -- adopt it.
        return _adopt(cur_live_hash, "legacy adopt")
    # Contents differ and a legacy meta can't distinguish "user edits in the
    # working copy" from "live replaced out-of-band". Never clobber: keep the
    # working copy and let the caller prompt for an explicit sync.
    return RECONCILE_STALE


def sync_from_live(wc: WorkingCopy) -> tuple[dict, dict]:
    """Pull the live files into the working copy and update the synced mtimes.

    Returns the freshly-read ``(state, wiring)`` dicts. If the meta write
    fails the synced mtimes are still updated in-memory so the current
    session is correct, but the failure is logged loudly because the next
    session's staleness tracking will be wrong.
    """
    state_mt, wiring_mt = safe_io.state_wiring_mtimes(wc.live_folder)
    state, wiring = safe_io.read_state_wiring(wc.live_folder)
    safe_io.write_state_wiring(wc.working_folder, state, wiring)
    wc.synced_state_mtime = state_mt
    wc.synced_wiring_mtime = wiring_mt
    wc.synced_live_hash = content_hash(state, wiring)
    try:
        wc._write_meta()
    except OSError:
        logger.exception(
            "Sync succeeded but meta write failed for %s -- next session may misreport "
            "staleness", wc.key,
        )
    logger.info("Working copy synced from live: %s", wc.live_folder)
    return state, wiring


def apply_to_live(wc: WorkingCopy, *, force: bool = False) -> None:
    """Push the working copy's state + wiring to the live folder.

    Unless *force*, raises :class:`StaleLiveError` if the live files changed
    since the last sync -- applying would otherwise silently overwrite an
    experiment program's write.

    The staleness check happens *twice*: once at the top of the function
    (preserves the historical contract), then again immediately before the
    write to narrow the TOCTOU window between the two safe-io calls below.

    If the post-write mtime read or meta write fails, the function raises
    -- ``synced_*`` is intentionally NOT updated, so the next staleness
    check will flag the divergence rather than silently treat the live as
    in-sync.
    """
    if not force:
        stale = live_changed(wc)
        if not stale and wc.synced_live_hash is not None:
            # mtime says unchanged — but coarse filesystem mtime granularity (or
            # an experiment write landing in the same tick as the last sync) can
            # collide, making a real content change invisible to the mtime gate.
            # We are about to OVERWRITE live, so confirm by content hash here. The
            # extra read is justified on this destructive, user-initiated path; the
            # background poll / cache-hit pre-check stays mtime-only (cheap, and
            # must never block an experiment's atomic save).
            try:
                l_state, l_wiring = safe_io.read_state_wiring(wc.live_folder)
                stale = content_hash(l_state, l_wiring) != wc.synced_live_hash
            except OSError:
                stale = False   # unreadable live → let the write path surface it
        if stale:
            raise StaleLiveError(
                "The live state files changed since they were loaded or synced."
            )
    state, wiring = safe_io.read_state_wiring(wc.working_folder)

    # Tightest possible TOCTOU recheck: re-stat *right* before the write so
    # the window between "is the live still in-sync" and "we are about to
    # replace it" is just the function-call overhead. Cannot be zero (the
    # OS does not expose a check-and-replace atomic), but this catches the
    # common case of an experiment write that lands during the working
    # read above.
    if not force:
        try:
            pre_mt = safe_io.state_wiring_mtimes(wc.live_folder)
        except OSError:
            pre_mt = None
        if pre_mt is not None and pre_mt != (wc.synced_state_mtime, wc.synced_wiring_mtime):
            raise StaleLiveError(
                "The live state files changed while preparing to apply -- refusing "
                "to overwrite an out-of-band write."
            )

    safe_io.write_state_wiring(wc.live_folder, state, wiring)

    # Post-write: read back the mtimes our write produced. If this fails
    # (live folder vanished, permission flipped under us), do NOT update the
    # synced mtimes and do NOT write meta -- raise instead so the user sees
    # the failure and can investigate, rather than future syncs silently
    # treating a partial-write state as authoritative.
    try:
        state_mt, wiring_mt = safe_io.state_wiring_mtimes(wc.live_folder)
    except OSError as exc:
        raise safe_io.LiveFileError(
            f"Wrote to {wc.live_folder} but could not read back its mtimes "
            f"({exc}); working copy's synced state was not advanced."
        ) from exc

    # Persist meta FIRST -- only advance in-memory synced_* after the meta
    # write succeeds. Otherwise an OSError on the meta write leaves the
    # in-memory copy ahead of disk: the current session would treat the
    # live as in-sync, but the next session would re-read the stale meta
    # and prompt a needless re-sync (red-team Phase 1 follow-up §4.4).
    applied_hash = content_hash(state, wiring)  # live now holds the working content
    try:
        wc._write_meta_pair(state_mt, wiring_mt, applied_hash)
    except OSError as exc:
        raise safe_io.LiveFileError(
            f"Applied to live but could not persist working-copy meta "
            f"({exc}); working copy's synced state was not advanced."
        ) from exc
    wc.synced_state_mtime = state_mt
    wc.synced_wiring_mtime = wiring_mt
    wc.synced_live_hash = applied_hash
    logger.info("Working copy applied to live: %s", wc.live_folder)


# ----------------------------------------------------------------------
# Garbage collection — working copies accumulate one per loaded folder
# (chips, run snapshots, history loads) and are never dropped on eviction
# by design (an evicted copy may hold unapplied edits). Hundreds pile up.
# ----------------------------------------------------------------------

def scan_working_copies(instance_path: str | Path) -> list[dict]:
    """Classify every persisted working copy under ``instance/working_state``.

    Returns one record per meta sidecar (plus orphan dirs without meta):
    ``{"key", "live_folder", "status", "updated_utc"}`` where status is

    - ``"clean"``    -- provably edit-free: working content hash equals the
      recorded ``synced_live_hash`` (or, for legacy metas whose live folder
      is provably untouched since the sync, working content equals live
      content). Safe to delete.
    - ``"dirty"``    -- working content differs from the recorded sync point:
      holds unapplied edits. Never auto-deleted.
    - ``"unverifiable"`` -- cleanliness can't be PROVEN: legacy meta whose
      live moved or is unreadable, or a TRANSIENT read failure on the meta /
      working files (AV, backup tool, concurrent sync). Kept -- a transient
      lock must never become a deletion.
    - ``"broken"``   -- deterministic evidence of uselessness: meta or
      working state.json missing, or unparsable JSON (safe_io's atomic
      writes make torn files impossible, so a parse failure is real
      corruption). Safe to delete.

    Legacy (pre-hash) copies are compared against their live folder ONLY
    when the live mtimes still equal the recorded sync point -- a folder
    nobody has written since the copy was made. Live folders with moved
    mtimes (possibly an experiment actively writing) are never content-read
    here; they classify as unverifiable and become provable over time as
    loads upgrade their metas.

    Content-reads every copy — O(total size); call on demand (an explicit
    user "clean up" flow), never per-request.
    """
    root = working_state_root(instance_path)
    if not root.exists():
        return []
    records: list[dict] = []
    seen_keys: set[str] = set()
    for meta_path in sorted(root.glob("*.meta.json")):
        key = meta_path.name[: -len(".meta.json")]
        seen_keys.add(key)
        rec = {"key": key, "live_folder": None, "status": "unverifiable",
               "updated_utc": None}
        records.append(rec)
        working = root / key
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            rec["status"] = "broken"        # vanished since the glob
            continue
        except OSError:
            continue                        # transient -> unverifiable (kept)
        except ValueError:
            rec["status"] = "broken"        # corrupt sidecar
            continue
        rec["live_folder"] = meta.get("live_folder")
        rec["updated_utc"] = meta.get("updated_utc")
        if not (working / "state.json").exists():
            rec["status"] = "broken"        # load() refuses these anyway
            continue
        try:
            w_state, w_wiring = safe_io.read_state_wiring(working)
            working_hash = content_hash(w_state, w_wiring)
        except ValueError:
            rec["status"] = "broken"        # real corruption
            continue
        except OSError:
            continue                        # transient -> unverifiable (kept)
        synced = meta.get("synced_live_hash")
        if isinstance(synced, str):
            rec["status"] = "clean" if working_hash == synced else "dirty"
            continue
        # Legacy meta: provable only against live, and only worth (and safe)
        # reading when the live folder is static since the sync point.
        live = meta.get("live_folder")
        if not live:
            continue
        try:
            cur_mt = safe_io.state_wiring_mtimes(Path(live))
        except OSError:
            continue                        # live missing/locked -> unverifiable
        if cur_mt != (meta.get("synced_state_mtime"),
                      meta.get("synced_wiring_mtime")):
            continue                        # live moved since sync -> unverifiable
        try:
            l_state, l_wiring = safe_io.read_state_wiring(Path(live))
        except (OSError, ValueError):
            continue
        if content_hash(l_state, l_wiring) == working_hash:
            rec["status"] = "clean"
    # Orphan working dirs with no meta sidecar: load() already refuses to
    # use them (it requires the meta), so they are dead weight.
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name not in seen_keys:
            records.append({"key": child.name, "live_folder": None,
                            "status": "broken", "updated_utc": None})
    return records


def gc_working_copies(
    instance_path: str | Path,
    *,
    keep_keys: frozenset[str] | set[str] = frozenset(),
    keep_fn=None,
    statuses: tuple[str, ...] = ("clean", "broken"),
    orphan_grace_s: float = 600.0,
) -> dict:
    """Delete working copies whose scan status is in *statuses*.

    *keep_keys* protects copies that are live in the running app regardless
    of status. *keep_fn* (``() -> set[str]``), when given, is re-evaluated
    immediately before EACH deletion -- the scan above content-reads every
    copy and can take seconds, during which a copy may become active.

    Meta-less orphan dirs younger than *orphan_grace_s* are skipped: a
    concurrent ``create()`` writes the working files BEFORE the meta
    sidecar, so a fresh meta-less dir may be a load in flight, not junk.

    Returns ``{"deleted": int, "kept": int, "by_status": {...}}``.
    """
    root = working_state_root(instance_path)
    records = scan_working_copies(instance_path)
    now = time.time()
    deleted = 0
    by_status: dict[str, int] = {}
    for rec in records:
        by_status[rec["status"]] = by_status.get(rec["status"], 0) + 1
        if rec["status"] not in statuses:
            continue
        key = rec["key"]
        target = root / key
        if rec["live_folder"] is None and rec["status"] == "broken":
            try:
                if now - target.stat().st_mtime < orphan_grace_s:
                    continue                # possible create() in flight
            except OSError:
                pass
        protected = set(keep_keys)
        if keep_fn is not None:
            try:
                protected |= set(keep_fn())
            except Exception:
                logger.exception("GC keep_fn failed -- skipping %s to be safe", key)
                continue
        if key in protected:
            continue
        shutil.rmtree(target, ignore_errors=True)
        try:
            (root / f"{key}.meta.json").unlink(missing_ok=True)
        except OSError:
            pass
        deleted += 1
    logger.info("Working-copy GC: deleted %d of %d (%s)",
                deleted, len(records), by_status)
    return {"deleted": deleted, "kept": len(records) - deleted,
            "by_status": by_status}
