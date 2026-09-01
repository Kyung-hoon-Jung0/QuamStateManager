"""One listing of a directory's child directories, shared by the two
staleness walkers (docs/155 F3').

Two functions in this app ask the filesystem the same question with different
words. ``HistoryManager._workspace_token`` stats every date directory under
every workspace root to answer *"did the workspace change?"*, and
``DatasetStore._current_mtime`` stats every date directory under a data folder
to answer *"did this store's root change?"*. On the customer's archive those
are the SAME directories — a data folder is a workspace root in the shallow
layout, and the chip directory the token descends into in the deep one — so a
`/datasets` render walked the whole archive twice, in two functions, for two
answers.

This module is the one walk. It does not merge the two questions or their
contracts; it merges the I/O underneath them, which is the only part that was
ever duplicated.

**Two costs, kept separate on purpose.** The LISTING (`os.scandir`) tells you
the names and which are directories — the type bits ride the listing, so that
half is free. An mtime is not free and needs `os.stat` per entry, and the two
callers want mtimes for different subsets: the store wants date directories
only, the token wants every child directory. So `mtime()` is per-entry and
lazily memoized: each caller pays for exactly the entries it asks about, and
the second caller in a request pays for none of them again.

`os.stat`, never `DirEntry.stat()`: on Windows the DirEntry's stat is served
from the parent's cached listing and does not see a write INSIDE the child
directory, which is the only change these walkers exist to notice (docs/141
§4ac, docs/155 §5b).

**Scope of the cache is one request, and that is load-bearing.** docs/105 #8
rejected a *TTL* memo because a run written milliseconds before a poll must be
seen by THAT poll. A request-scoped cache keeps that exactly: every poll is its
own request and takes its own sample. Outside a request — the scheduler worker,
autofit, a CLI call — `begin()` was never called, nothing is cached, and both
walkers behave precisely as they did before this module existed.
"""
from __future__ import annotations

import os
import threading

_local = threading.local()


def begin() -> None:
    """Open a sampling scope on THIS thread (one per request).

    Overwrites any scope left open by a request whose teardown did not run, so
    a leaked scope self-heals on this thread's next request rather than
    serving one stale listing forever.
    """
    _local.cache = {}


def end() -> None:
    """Close the sampling scope on this thread."""
    _local.cache = None


class DirSample:
    """The child directories of one directory, with mtimes on demand.

    ``own_mtime`` is None when the directory itself could not be stat'ed (the
    callers treat that as "no reading", each in its own established way).
    ``error`` carries the OSError from a failed LISTING so a caller that
    propagates one can propagate the real exception rather than a stand-in.
    """

    __slots__ = ("path", "own_mtime", "children", "error", "_mtimes")

    def __init__(self, path: str, own_mtime: float | None,
                 children: tuple[tuple[str, str, bool], ...],
                 error: OSError | None) -> None:
        self.path = path
        self.own_mtime = own_mtime
        # ((name, full path, is_symlink), ...) — directories only. The
        # symlink bit rides the listing like the type bits do; the own-root
        # exclusion in `_workspace_token` needs it per entry and must not pay
        # a syscall for it (docs/155 F2').
        self.children = children
        self.error = error
        self._mtimes: dict[str, float | None] = {}

    def mtime(self, name: str, path: str) -> float | None:
        """This child's mtime, stat'ed at most once per sampling scope."""
        if name in self._mtimes:
            return self._mtimes[name]
        try:
            value: float | None = os.stat(path).st_mtime
        except OSError:
            value = None
        self._mtimes[name] = value
        return value


def _read(path: str, own_mtime: float | None = None) -> DirSample:
    own = own_mtime
    if own is None:
        try:
            own = os.stat(path).st_mtime
        except OSError:
            own = None
    # The listing happens even when that stat failed, because
    # ``_workspace_token`` descended into such a directory and folding its
    # children in is the behaviour being preserved. ``_current_mtime`` checks
    # ``own_mtime`` first and returns its sentinel without looking at the
    # children, so the only cost is one scandir on a directory that could not
    # be stat'ed — which almost always fails immediately anyway.
    return _list(path, own)


def _list(path: str, own: float | None) -> DirSample:
    try:
        with os.scandir(path) as it:
            kids = []
            for de in it:
                try:
                    if de.is_dir():
                        kids.append((de.name, de.path, de.is_symlink()))
                except OSError:
                    # Path.is_dir() answers False here rather than raising.
                    continue
        return DirSample(path, own, tuple(kids), None)
    except OSError as exc:
        return DirSample(path, own, (), exc)


def sample(path, own_mtime: float | None = None) -> DirSample:
    """The child directories of *path*, once per sampling scope.

    *own_mtime* is an already-known mtime for *path* itself. A caller that
    just read it while listing the PARENT passes it rather than paying a
    second stat for the same directory — which is the whole deep-layout case
    (`<root>/<chip>/<date>`: the chip dir's mtime comes from the root's
    listing, then we descend into it).
    """
    key = os.fspath(path)
    cache = getattr(_local, "cache", None)
    if cache is None:
        return _read(key, own_mtime)
    hit = cache.get(key)
    if hit is None:
        hit = _read(key, own_mtime)
        cache[key] = hit
    return hit
