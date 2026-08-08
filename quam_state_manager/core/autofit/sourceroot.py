"""Pinned analysis source roots (docs/78 D-13 amendment, measured 2026-08-06).

The plan modelled replay compatibility as (env × state-generation). Reality has
a **third axis: the analysis tree's own revision**. Measured: the customer tree's
working copy gained a ``quam_config`` import that requires quam ≥ 0.6, so the
older env can no longer import ``quam_config`` from the LIVE tree at all — and
the newer env cannot load the older runs' ``quam_state``. With a single live
root, every pre-0.6-era archive (including the only coupler-flux data) becomes
unreplayable, while a **pinned revision** of the same tree replays them
bit-identically.

So a verification context is a triple: **(env, source root, run generation)**.
This module owns the root axis: it materializes a git revision of a tree into a
private cache with ``git archive`` — a READ-ONLY operation on the customer tree
(no checkout, no worktree metadata, no index touch). Nothing here ever writes
inside the source tree.
"""
from __future__ import annotations

import logging
import subprocess
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIRNAME = "autofit_pinned_roots"
_GIT_TIMEOUT = 180


def _git(root, *args, timeout: int = _GIT_TIMEOUT):
    """Run a read-only git command in ``root``; return stdout or None."""
    try:
        proc = subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def is_git_tree(root) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree") == "true"


def resolve_rev(root, rev: str = "HEAD") -> str | None:
    """Full SHA for ``rev``, or None when the tree isn't git / rev is unknown."""
    return _git(root, "rev-parse", "--verify", f"{rev}^{{commit}}")


def is_dirty(root, *, paths: list[str] | None = None) -> bool:
    """True when the working copy differs from HEAD (tracked modifications or
    untracked files) — i.e. the live root is NOT reproducible from a revision.

    Callers use this to decide whether a live-root verdict can be attributed to
    a commit at all; ``gate_hash`` still stamps the exact analysis bytes.
    """
    args = ["status", "--porcelain"]
    if paths:
        args += ["--", *paths]
    out = _git(root, *args)
    return bool(out)


def materialize(root, rev: str, cache_dir) -> str | None:
    """Extract ``rev`` of ``root`` into ``cache_dir/<sha>/`` and return the path.

    Idempotent: an already-materialized revision is reused (revisions are
    immutable, so the SHA is a complete cache key). Returns None when the tree
    isn't git, the revision is unknown, or the archive fails — never a partial
    tree (extraction lands in a sibling temp dir and is renamed into place).
    """
    sha = resolve_rev(root, rev)
    if not sha:
        logger.warning("cannot resolve %r in %s (not a git tree?)", rev, root)
        return None
    cache_dir = Path(cache_dir)
    dest = cache_dir / sha
    marker = dest / ".sm_pinned_ok"
    if marker.is_file():
        return str(dest)

    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / f".{sha}.zip"
    staging = cache_dir / f".{sha}.staging"
    # git archive READS the object store and writes only to -o (outside the
    # source tree). No checkout, no worktree registration, no index write.
    if _git(root, "archive", "--format=zip", "-o", str(zip_path), sha) is None:
        logger.warning("git archive failed for %s@%s", root, sha[:12])
        return None
    try:
        staging.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging)
        (staging / ".sm_pinned_ok").write_text(sha, encoding="utf-8")
        if dest.exists():           # a concurrent materialize won the race
            return str(dest)
        staging.rename(dest)
    except (OSError, zipfile.BadZipFile):
        logger.exception("could not materialize %s@%s", root, sha[:12])
        return None
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass
    return str(dest)


def cache_dir_for(instance_path) -> Path:
    return Path(instance_path) / _CACHE_DIRNAME


def history_revs(root, *, paths=("quam_config",), limit: int = 8) -> list[str]:
    """SHAs that touched ``paths``, newest first — the revisions at which the
    analysis contract could have changed.

    Pinning HEAD alone is not enough: the lab keeps committing, and the moment a
    commit moves ``quam_config`` onto a newer library, HEAD stops serving every
    older archive (measured — a single commit made the whole pre-0.6 corpus
    unreplayable again). The honest fallback is the newest revision that STILL
    serves the run, and these are the only revisions worth probing.
    """
    out = _git(root, "log", f"--max-count={int(limit)}", "--format=%H",
               "--", *paths)
    return [ln.strip() for ln in (out or "").splitlines() if ln.strip()]


def candidates(live_root, instance_path, *, revs=("HEAD",),
               include_live: bool = True) -> list[dict]:
    """Ordered candidate roots for verification: the live tree first (it is the
    lab's current truth), then pinned revisions as fallbacks for older runs.

    ``revs="auto"`` walks the history of the analysis-defining paths
    (:func:`history_revs`) so a run that HEAD can no longer serve falls back to
    the newest revision that still can. Materialization is lazy per revision and
    cached by SHA, so the walk costs one archive extraction per revision ever
    used — not per run.

    Each entry is ``{path, kind, rev, dirty}`` so a verdict can name exactly
    which analysis tree produced it (docs/78 D-13.3).
    """
    out: list[dict] = []
    if include_live and Path(live_root).is_dir():
        out.append({"path": str(live_root), "kind": "live",
                    "rev": resolve_rev(live_root), "dirty": is_dirty(live_root)})
    if not is_git_tree(live_root):
        return out
    if revs == "auto":
        revs = ["HEAD", *history_revs(live_root)]
    cache = cache_dir_for(instance_path)
    for rev in revs:
        sha = resolve_rev(live_root, rev)
        if not sha or any(c.get("rev") == sha and c["kind"] == "pinned"
                          for c in out):
            continue
        path = materialize(live_root, sha, cache)
        if path:
            out.append({"path": path, "kind": "pinned", "rev": sha,
                        "dirty": False})
    return out
