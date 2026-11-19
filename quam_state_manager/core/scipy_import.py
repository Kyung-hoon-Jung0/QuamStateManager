"""One door for importing scipy inside the SM process.

The server imports scipy from several threads: the start-up preload
(``waveform_synth.preload_scipy_quietly``), request threads (``/zline`` via
``zline_filters``, the map tools via ``mapfit``, the NetCDF readers) and the
lint. Concurrent imports of scipy subpackages from different threads can make
CPython's import machinery raise ``_DeadlockError`` (measured with scipy 1.18
on Python 3.12: two threads importing ``scipy.stats`` and ``scipy.signal`` at
once). When such a failure hits a package import, CPython drops the package
from ``sys.modules`` but keeps the children that finished; the next import
re-creates the package object WITHOUT those children bound as attributes, and
from then on every module that reads ``scipy._lib.<x>`` fails with
``AttributeError: Module 'scipy' has no attribute '_lib'`` for the life of
the process.

:func:`import_scipy` prevents and repairs that:

* every scipy import made by SM code runs under one re-entrant lock, so SM's
  own threads never import scipy concurrently;
* before and after the import, a finished child module that is in
  ``sys.modules`` but not bound on its parent package is bound again, so an
  orphaned state left by an earlier failure heals instead of persisting.
"""
from __future__ import annotations

import importlib
import sys
import threading
from types import ModuleType

__all__ = ["SCIPY_IMPORT_LOCK", "import_scipy", "rebind_orphans"]

#: held around every scipy import SM makes (the preload and the foreground)
SCIPY_IMPORT_LOCK = threading.RLock()


def _complete(mod) -> bool:
    spec = getattr(mod, "__spec__", None)
    return mod is not None and not getattr(spec, "_initializing", False)


def rebind_orphans(root: str = "scipy") -> int:
    """Bind every fully imported ``root.*`` module onto its parent package
    when the parent lacks that attribute; returns how many were bound."""
    n = 0
    prefix = root + "."
    for name, mod in list(sys.modules.items()):
        if not name.startswith(prefix) or not _complete(mod):
            continue
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is None or child in vars(parent):
            continue
        setattr(parent, child, mod)
        n += 1
    return n


def _bound(name: str):
    """The module *name* when it and every package above it are complete and
    bound on their parents (the healthy state), else None."""
    mod = sys.modules.get(name)
    if not _complete(mod):
        return None
    parts = name.split(".")
    for i in range(1, len(parts)):
        parent = sys.modules.get(".".join(parts[:i]))
        child = sys.modules.get(".".join(parts[:i + 1]))
        if not _complete(parent) or vars(parent).get(parts[i]) is not child:
            return None
    return mod


def import_scipy(name: str) -> ModuleType:
    """``importlib.import_module(name)`` for a scipy module, under
    :data:`SCIPY_IMPORT_LOCK`, healing orphaned submodules around it.
    Raises what the import raises (``ImportError`` when scipy is missing).
    A module already loaded and bound is returned without the lock."""
    mod = _bound(name)
    if mod is not None:
        return mod
    root = name.split(".", 1)[0]
    with SCIPY_IMPORT_LOCK:
        if not _complete(sys.modules.get(root)):
            importlib.import_module(root)
        rebind_orphans(root)
        try:
            return importlib.import_module(name)
        finally:
            rebind_orphans(root)
