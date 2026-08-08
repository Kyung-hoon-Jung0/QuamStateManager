"""Env × archive-generation compatibility (docs/78 D-13, P0a).

Archives span quam generations: a quam-0.6 env refuses a 0.5-era ``quam_state``
(``duration_control``→``duration_qubit`` rename, unknown optional attributes),
while an older env loads it bit-faithfully. The customer's env is the ground
truth for verification, so SM never guesses — it PROBES: spawn the candidate
interpreter, try the real ``Quam.load``, and classify the outcome honestly
("this run's state generation is incompatible with this env's quam") instead of
surfacing a raw traceback.

Caching discipline (the docs/52 lesson — never key a probe on interpreter
mtime): the cache key is the env's *package-version signature* (obtained through
``config_generator.probe_envs``' own freshness machinery, which re-probes after
a ``pip install``) × the run's *state-generation fingerprint* (a content hash of
the ``__class__`` inventory — loadability is a property of the class schema, not
of the individual run) × a stat-signature of the ``quam_config`` tree the Quam
class itself is imported from. Only deterministic outcomes are cached; transient
failures (spawn errors, timeouts, unreadable state) stay retryable.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "autofit_envmatrix.json"
_CACHE_MAX = 512

# Deterministic classifications — same env versions + same generation will fail
# the same way forever, so these are safe to cache. Everything else is treated
# as transient and re-probed next time.
_CACHEABLE = ("ok", "generation_mismatch", "tree_incompatible", "no_quam",
              "no_quam_config")

# Packages whose absence means "this env cannot run the QM analysis at all";
# a DOTTED miss inside one of them means the analysis tree is newer than the env.
_QM_PACKAGES = ("quam", "qm", "qualang_tools", "quam_builder", "qualibrate",
                "qualibration_libs")

_MEM_CACHE: dict[str, dict] = {}
_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# fingerprints
# ---------------------------------------------------------------------------

def generation_fingerprint(run_folder) -> str | None:
    """Content hash of a run's state *generation*: the sorted ``__class__``
    inventory + top-level keys of ``quam_state/{state,wiring}.json``.

    Loadability under a given quam is a property of the class schema the state
    was serialized with — two runs with the same inventory load (or refuse)
    identically — so this is the honest per-run cache key. ``None`` when the
    snapshot is unreadable (the caller must classify, never cache).
    """
    from quam_state_manager.core import safe_io

    state_dir = Path(run_folder) / "quam_state"
    classes: set[str] = set()
    tops: list[str] = []
    for name in ("state.json", "wiring.json"):
        p = state_dir / name
        try:
            data = safe_io.read_json(p)
        except (OSError, ValueError):
            if name == "state.json":
                return None          # no readable state ⇒ no generation
            data = {}
        if isinstance(data, dict):
            tops.extend(f"{name}:{k}" for k in sorted(data.keys()))
            _collect_classes(data, classes)
    h = hashlib.sha256()
    for item in sorted(classes) + sorted(tops):
        h.update(item.encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest()


def _collect_classes(obj, out: set[str], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(obj, dict):
        c = obj.get("__class__")
        if isinstance(c, str):
            out.add(c)
        for v in obj.values():
            _collect_classes(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _collect_classes(v, out, depth + 1)


def _env_version_sig(env: str, instance_path) -> str | None:
    """Package-version signature for the interpreter, via ``probe_envs``' own
    persisted, install-aware cache (site-packages mtime → re-probe after pip)."""
    from quam_state_manager.core import config_generator

    try:
        info = config_generator.probe_envs([env], instance_path=instance_path).get(env) or {}
    except Exception:  # noqa: BLE001 — a broken probe must degrade, not raise
        logger.exception("env probe failed for %s", env)
        return None
    if info.get("error") and not info.get("versions"):
        return None
    v = info.get("versions") or {}
    return "|".join(f"{k}={v.get(k)}" for k in sorted(v)) + f"|py={info.get('python')}"


def _source_sig(source_root: str | None) -> str:
    """Stat-level signature of the ``quam_config`` tree the Quam class comes
    from — a different (or edited) tree defines a different Quam.

    Empty string when no source root is set; the env's own installed
    ``quam_config`` is then pinned by the interpreter path in the cache key
    (NOT by the version signature — ``quam_config`` is not a probed package)."""
    if not source_root:
        return ""
    root = Path(source_root) / "quam_config"
    parts: list[str] = []
    try:
        for p in sorted(root.rglob("*.py")):
            try:
                st = p.stat()
                parts.append(f"{p.relative_to(root)}:{st.st_size}:{st.st_mtime_ns}")
            except OSError:
                continue
    except OSError:
        return f"unreadable:{source_root}"
    h = hashlib.sha256("\0".join(parts).encode("utf-8", "replace"))
    return h.hexdigest()[:16]


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------

# Message fragments that mark a *generation* refusal (schema drift between the
# state snapshot and the env's quam/quam_config classes) rather than a broken
# file or environment. Matched case-insensitively against exc_type + detail.
_GENERATION_MARKS = (
    "did you mean",            # attribute rename suggestions (0.5→0.6 renames)
    "unexpected keyword",
    "got an unexpected",
    "optional attributes",     # quam 0.6 unknown-attribute listing
    "required attributes",
    "no attribute",
    "not a valid attr",        # quam_instantiation: a NEWER state on an OLDER
                               # quam (measured: `Attribute isolation is not a
                               # valid attr of Quam.twpas[...]` — 2026-07 runs
                               # under quam 0.5). Without this the refusal fell
                               # to the uncached `error` bucket and re-spawned.
    "cannot import name",      # a __class__ that moved homes between stacks
    "validationerror",
)


def classify(stage: str | None, exc_type: str | None, detail: str | None,
             missing_module: str | None = None) -> str:
    """Map a probe outcome to one honest bucket.

    ``ok`` · ``generation_mismatch`` · ``no_quam`` · ``no_quam_config`` ·
    ``state_unreadable`` · ``error`` (unclassified — retryable, never cached).

    The import branch classifies on ``missing_module`` (the probe's
    ``ModuleNotFoundError.name``), NOT on the traceback text: the traceback
    always renders the source line ``from quam_config import Quam``, so
    substring-sniffing it labels a missing ``quam`` as "no quam_config" and the
    ``no_quam`` bucket becomes unreachable. Text is the fallback only when the
    probe reported no module name (an older probe, or a non-import error).
    """
    text = f"{exc_type or ''} {detail or ''}".lower()
    if stage == "import":
        mod = missing_module or ""
        root = mod.split(".")[0]
        if root:
            # A DOTTED miss means the package is there but the submodule is not:
            # the analysis tree wants a newer library than this env ships
            # (measured: the tree's quam_config imports
            # `quam.components._waveform_tools`, quam 0.6-only, so a quam-0.5
            # env cannot serve THIS tree at all). That is a tree/env mismatch,
            # not a missing install — labelling it "no quam" would send the user
            # to reinstall a package they already have.
            if "." in mod and root in _QM_PACKAGES:
                return "tree_incompatible"
            if root == "quam_config":
                return "no_quam_config"
            if root in _QM_PACKAGES:
                return "no_quam"
            return "error"
        # no module name (non-ModuleNotFoundError import failure, e.g. a syntax
        # error or a transitive ImportError inside quam_config itself)
        if "no module named 'quam_config" in text:
            return "no_quam_config"
        if "no module named 'quam" in text:
            return "no_quam"
        if "quam_config" in text:
            return "no_quam_config"
        return "error"
    if stage == "load":
        if exc_type in ("FileNotFoundError", "NotADirectoryError", "JSONDecodeError"):
            return "state_unreadable"
        if any(m in text for m in _GENERATION_MARKS):
            return "generation_mismatch"
        return "error"
    return "error"


def explain(entry: dict) -> str:
    """One honest human sentence for a probe entry (never a traceback)."""
    c = entry.get("classification")
    quam = (entry.get("lib_versions") or {}).get("quam")
    if c == "ok":
        return f"compatible (quam {quam})"
    if c == "generation_mismatch":
        return (f"this run's state generation is incompatible with this env's "
                f"quam ({quam}) — pick an env whose quam matches the run's era")
    if c == "tree_incompatible":
        return (f"the analysis tree needs a newer library than this env ships "
                f"(quam {quam}) — use a pinned revision of the tree, or an env "
                f"that matches it")
    if c == "no_quam":
        return "this env has no quam installed"
    if c == "no_quam_config":
        return ("quam_config is not importable here — check the source root "
                "(the …/superconducting folder) or the env install")
    if c == "state_unreadable":
        return "the run's quam_state snapshot is missing or unreadable"
    return "probe failed for an unclassified reason (will retry)"


# ---------------------------------------------------------------------------
# the probe
# ---------------------------------------------------------------------------

def probe_load(env: str, run_folder, *, source_root: str | None = None,
               instance_path=None, timeout: int = 180,
               force: bool = False) -> dict:
    """Can ``env`` load ``run_folder``'s quam_state? Classified + cached.

    Returns ``{ok, classification, message, exc_type, lib_versions, cached,
    fingerprint, env_sig}``. Every verdict downstream must carry this entry's
    ``lib_versions`` (docs/78 D-13.3: a verdict without its env is not
    reproducible).
    """
    fp = generation_fingerprint(run_folder)
    if fp is None:
        entry = {"ok": False, "classification": "state_unreadable",
                 "exc_type": None, "lib_versions": {}, "cached": False,
                 "fingerprint": None, "env_sig": None}
        entry["message"] = explain(entry)
        return entry

    env_sig = _env_version_sig(env, instance_path)
    # `env` is IN the key, not just its package versions: two interpreters can
    # report identical versions of the probed distributions and still differ on
    # what the probe actually imports (quam_config is not a probed package), so
    # a version-only key serves one env's verdict for another. Same doctrine as
    # fit_audit.audit_run_cached.
    key = f"{env}|{env_sig}|{fp}|{_source_sig(source_root)}" if env_sig else None

    if key and not force:
        with _CACHE_LOCK:
            hit = _MEM_CACHE.get(key)
        if hit is None and instance_path is not None:
            hit = _load_disk_cache(instance_path).get(key)
            if hit is not None:
                with _CACHE_LOCK:
                    _MEM_CACHE[key] = hit
        if hit is not None:
            out = dict(hit)
            out.update(cached=True, fingerprint=fp, env_sig=env_sig)
            out["message"] = explain(out)
            return out

    raw = _spawn_probe(env, run_folder, source_root, timeout)
    cls = "ok" if raw.get("ok") else classify(raw.get("stage"),
                                              raw.get("exc_type"),
                                              raw.get("detail"),
                                              raw.get("missing_module"))
    entry = {"ok": bool(raw.get("ok")), "classification": cls,
             "exc_type": raw.get("exc_type"),
             "lib_versions": raw.get("lib_versions") or {},
             "cached": False, "fingerprint": fp, "env_sig": env_sig}
    entry["message"] = explain(entry)

    if key and cls in _CACHEABLE:
        stored = {k: entry[k] for k in
                  ("ok", "classification", "exc_type", "lib_versions")}
        with _CACHE_LOCK:
            _MEM_CACHE[key] = stored
            while len(_MEM_CACHE) > _CACHE_MAX:
                _MEM_CACHE.pop(next(iter(_MEM_CACHE)), None)
        if instance_path is not None:
            _store_disk_cache(instance_path, key, stored)
    return entry


def choose_env(run_folder, envs: list[str], *, source_root: str | None = None,
               instance_path=None, timeout: int = 180) -> dict:
    """First env (in the caller's order — the user's preference order) that can
    load the run against ONE source root. ``{"env": str|None, "probes": [...]}``
    — the probes list is the honest story for the UI when none fit."""
    ctx = choose_context(run_folder, envs, [source_root],
                         instance_path=instance_path, timeout=timeout)
    return {"env": ctx["env"], "probes": ctx["probes"]}


def choose_context(run_folder, envs: list[str], roots: list, *,
                   instance_path=None, timeout: int = 180) -> dict:
    """First (env, source root) pair that can load the run.

    Verification compatibility is a TRIPLE — env × analysis-tree revision ×
    run generation (docs/78 D-13 amendment, measured 2026-08-06: a tree whose
    ``quam_config`` moved to quam 0.6 makes every older archive unreplayable
    against the LIVE root, while a pinned revision of the same tree replays
    them bit-identically). Roots are tried in the caller's preference order
    (live first, pinned fallbacks after); ``roots`` entries may be plain paths
    or ``sourceroot.candidates`` dicts.

    Returns ``{env, source_root, root_kind, root_rev, probes}``; ``probes``
    lists every (env, root) attempt with its honest classification.
    """
    probes = []
    for root in roots or [None]:
        meta = root if isinstance(root, dict) else {"path": root}
        path = meta.get("path")
        for env in envs:
            entry = probe_load(env, run_folder, source_root=path,
                               instance_path=instance_path, timeout=timeout)
            probes.append({"env": env, "source_root": path,
                           "root_kind": meta.get("kind"),
                           "root_rev": meta.get("rev"),
                           "ok": entry["ok"],
                           "classification": entry["classification"],
                           "message": entry["message"],
                           "lib_versions": entry.get("lib_versions") or {}})
            if entry["ok"]:
                return {"env": env, "source_root": path,
                        "root_kind": meta.get("kind"),
                        "root_rev": meta.get("rev"), "probes": probes}
    return {"env": None, "source_root": None, "root_kind": None,
            "root_rev": None, "probes": probes}


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------

def _spawn_probe(env: str, run_folder, source_root: str | None,
                 timeout: int) -> dict:
    from quam_state_manager.core.config_generator import _script_path
    from quam_state_manager.core.fit_audit import _pth

    state_dir = os.path.join(str(run_folder), "quam_state")
    args = [env, _pth(env, str(_script_path("run_quam_load_probe.py"))),
            "--state", _pth(env, state_dir)]
    if source_root:
        args += ["--source-root", _pth(env, str(source_root))]
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "spawn", "exc_type": "TimeoutExpired",
                "detail": f"probe exceeded {timeout}s"}
    except OSError as e:
        return {"ok": False, "stage": "spawn", "exc_type": type(e).__name__,
                "detail": str(e)}
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                break
    return {"ok": False, "stage": "spawn", "exc_type": "NoOutput",
            "detail": (proc.stderr or proc.stdout or "no output")[-800:]}


def _cache_path(instance_path) -> Path:
    return Path(instance_path) / _CACHE_FILENAME


def _load_disk_cache(instance_path) -> dict:
    try:
        data = json.loads(_cache_path(instance_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _store_disk_cache(instance_path, key: str, stored: dict) -> None:
    from quam_state_manager.core import safe_io

    try:
        cache = _load_disk_cache(instance_path)
        cache[key] = stored
        while len(cache) > _CACHE_MAX:
            cache.pop(next(iter(cache)), None)
        safe_io.atomic_write_json(_cache_path(instance_path), cache)
    except OSError:
        logger.warning("could not persist envmatrix cache", exc_info=True)
