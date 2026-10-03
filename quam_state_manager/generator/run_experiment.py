"""Standalone Scheduler runner — reports the qualibrate config of its env.

Like ``run_build.py`` and ``run_generate_config.py``, this script runs inside a
*user-selected* conda/venv interpreter that has the QM + qualibrate stack
installed. It is NEVER imported by ``quam_state_manager`` — it may import only
the qualibrate/QM libraries and the Python standard library.

Driven by ``quam_state_manager.core.scheduler``.

Phase 0 implements ``--mode report-config`` only: it reads the *effective*
(project-merged) qualibrate configuration the way a node run would resolve it,
plus the env's editable-install location, and writes the result to
``_result.json``. Later phases add ``scan`` (list nodes/graphs + parameter
schemas, hardware-safe via inspection mode) and ``run`` (execute one prepared
node/graph copy).

Usage::

    python run_experiment.py --mode report-config --out work_dir
"""

import argparse
import json
import sys
import traceback
from pathlib import Path

# Same defensive sys.path insert as the sibling scripts — the script's directory
# is normally sys.path[0], but PYTHONSAFEPATH / -P (3.11+) suppress that.
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from _script_common import library_versions as _library_versions  # noqa: E402

RESULT_FILENAME = "_result.json"


# ---------------------------------------------------------------------------
# report-config
# ---------------------------------------------------------------------------

def _qualibrate_version() -> str:
    from importlib.metadata import version, PackageNotFoundError
    try:
        return version("qualibrate")
    except PackageNotFoundError:
        return "<not installed>"
    except Exception as exc:  # pragma: no cover - defensive
        return f"<error: {exc}>"


def _file_url_to_path(url: str) -> str:
    """Convert a ``file:///...`` direct_url into a native filesystem path."""
    from urllib.parse import urlparse
    from urllib.request import url2pathname
    return url2pathname(urlparse(url).path)


def _editable_install() -> dict | None:
    """Locate the editable install root of the calibrations package, if any.

    The ExampleVendor project installs ``superconducting_calibrations`` as an
    editable package; its ``direct_url.json`` records the source tree the
    ``.pth`` points at. That tree (not the file being run) is what actually
    resolves ``quam_config`` / ``calibration_utils`` imports — so the Scheduler
    checks it against the chosen calibrations folder to catch a stale-install
    mismatch (the wsl_kri trap).
    """
    from importlib.metadata import distribution
    for name in ("superconducting_calibrations", "superconducting-calibrations"):
        try:
            dist = distribution(name)
        except Exception:
            continue
        try:
            txt = dist.read_text("direct_url.json")
        except Exception:
            txt = None
        if not txt:
            return {"dist": name, "path": None, "editable": None}
        try:
            info = json.loads(txt) or {}
        except (ValueError, TypeError):
            return {"dist": name, "path": None, "editable": None}
        url = info.get("url", "")
        editable = bool((info.get("dir_info") or {}).get("editable"))
        path = None
        if url.startswith("file://"):
            try:
                path = _file_url_to_path(url)
            except Exception:
                path = None
        return {"dist": name, "path": path, "url": url or None, "editable": editable}
    return None


def _effective_config() -> dict:
    """Resolve the *effective* (project-merged) qualibrate config of this env.

    qualibrate deep-merges ``~/.qualibrate/config.toml`` with
    ``projects/<project>/config.toml`` (project wins), so the raw top-level
    file is NOT authoritative. We read it via qualibrate's own resolvers (which
    do the merge + migrations), with a raw-merged-dict fallback so a model/API
    change in a future qualibrate version can't blank the whole report.
    """
    out = {
        "config_file": None,
        "project": None,
        "state_path": None,
        "storage_location": None,
        "calibration_library_folder": None,
        "source": None,
    }

    from qualibrate_config.resolvers import get_qualibrate_config_path
    cfg_path = get_qualibrate_config_path()
    out["config_file"] = str(cfg_path)

    # Primary: the typed model (applies the project merge + migrations).
    try:
        from qualibrate_config.resolvers import get_qualibrate_config
        qs = get_qualibrate_config(cfg_path)
        out["project"] = getattr(qs, "project", None)
        storage = getattr(qs, "storage", None)
        loc = getattr(storage, "location", None) if storage is not None else None
        if loc is not None:
            out["storage_location"] = str(loc)
        callib = getattr(qs, "calibration_library", None)
        folder = getattr(callib, "folder", None) if callib is not None else None
        if folder is not None:
            out["calibration_library_folder"] = str(folder)
        try:
            from qualibrate.core.config.resolvers import get_quam_state_path
            sp = get_quam_state_path(qs)
            if sp is not None:
                out["state_path"] = str(sp)
        except Exception:
            pass
        out["source"] = "model"
    except Exception:
        pass

    # Fallback / cross-check: the raw merged dict (project override applied).
    try:
        from qualibrate_config.file import read_config_file
        raw = read_config_file(cfg_path, solve_references=False) or {}
        q = raw.get("qualibrate", {}) or {}
        if out["project"] is None:
            out["project"] = q.get("project")
        if out["storage_location"] is None:
            loc = (q.get("storage", {}) or {}).get("location")
            out["storage_location"] = str(loc) if loc is not None else None
        if out["calibration_library_folder"] is None:
            folder = (q.get("calibration_library", {}) or {}).get("folder")
            out["calibration_library_folder"] = str(folder) if folder is not None else None
        if out["state_path"] is None:
            sp = (raw.get("quam", {}) or {}).get("state_path")
            out["state_path"] = str(sp) if sp is not None else None
        if out["source"] is None:
            out["source"] = "raw"
    except Exception:
        pass

    return out


def run_report_config() -> dict:
    """The parts of the result envelope the report-config step provides."""
    return {
        "config": _effective_config(),
        "editable_install": _editable_install(),
    }


# ---------------------------------------------------------------------------
# run — execute one prepared node/graph copy
# ---------------------------------------------------------------------------

def run_target(target: str, state_path: str | None, config_file: str | None,
               baseline_out: str | None = None, *, isolate: bool = False,
               replay: bool = False) -> None:
    """Execute a prepared node/graph ``.py`` (already overridden) via runpy.

    Pins the chip + config via the env so the experiment loads/saves the
    intended state regardless of the ambient config. The file runs its actions
    at import (run as ``__main__``); a node's spliced ``custom_param`` applies
    the chosen qubits/params/simulate before the experiment body executes.

    SAFETY: dict-style graph files call ``g.run()`` at module top level (no
    ``__main__`` guard), so a plain import/runpy of such a file fires the graph
    on hardware immediately. This MUST only be called by the dry-run-gated
    Scheduler run path on a prepared copy — never import/runpy a calibrations
    ``.py`` in the State Manager process without qualibrate inspection mode.
    """
    import os
    import runpy

    if not target or not Path(target).exists():
        raise FileNotFoundError(f"target not found: {target!r}")
    # docs/173 S9 (found on a real customer env): a node's plot action calls
    # plt.show(), and the customer env's default matplotlib backend is the
    # INTERACTIVE tkagg (tkinter present) -- so a headless Scheduler subprocess
    # blocks forever on a GUI window that never opens. Force a non-interactive
    # backend; an operator override wins. docs/245: this must come FIRST -- the
    # isolation pin below imports qualibrate, which imports matplotlib, and the
    # backend is chosen at that import (found on the rig: a replay hung in
    # tkinter's mainloop when this line still sat after the pin).
    os.environ.setdefault("MPLBACKEND", "Agg")
    if state_path:
        os.environ["QUAM_STATE_PATH"] = str(state_path)
    if isolate:
        # docs/245 (D-01): an isolated run (an agent's per-run scratch) is ALWAYS
        # pinned to its scratch -- never conditional on a cached Runner setting.
        # The pinned config is generated from the config this env would resolve
        # (the passed --config-file, else QUALIBRATE_CONFIG_FILE / ~/.qualibrate)
        # and re-read through qualibrate's own resolver; anything short of "the
        # framework's state path IS the scratch" refuses the run before the node
        # file is imported.
        _ISOLATION_REPORT.clear()
        if not state_path:
            raise RuntimeError("isolation refused: an isolated run needs --state-path (its scratch)")
        pinned, why = _pin_config_strict(config_file, str(state_path))
        if pinned is None:
            raise RuntimeError(f"isolation refused: {why}")
        os.environ["QUALIBRATE_CONFIG_FILE"] = str(pinned)
    elif config_file:
        # (a person's Runner run: it writes the chip, by contract -- docs/245 keeps
        # this branch unchanged)
        # docs/174 amended II (found on a real customer's cloud chain): the qualibrate
        # config's ``[quam] state_path`` wins over the QUAM_STATE_PATH env for the
        # framework's own machine save, so a node whose config points at the LIVE
        # chip writes LIVE directly mid-run -- bypassing the scratch entirely and
        # re-introducing the class-default root keys (and any writes) onto live,
        # which then diverges -> stale_live on the next node. Redirect that config's
        # state_path at the per-run scratch so EVERY qualibrate save lands there
        # (where _strip_phantom_roots cleans it) and a run never touches live -- the
        # docs/173 S9 invariant. Best-effort: on any failure the original config
        # stands.
        eff_config = _config_pinned_to_scratch(config_file, state_path) if state_path else config_file
        os.environ["QUALIBRATE_CONFIG_FILE"] = str(eff_config)
    # docs/174 (amended): capture the state's top-level keys BEFORE the node runs,
    # while the scratch is still the byte-for-byte make_scratch copy of the chip.
    # ``machine.save()`` later materializes EVERY field the quam class declares,
    # adding class-default ROOT keys the customer's state.json never had
    # (flux_crosstalk_max_v / require_flux_crosstalk_dc / twpa_ext on that
    # class). Knowing the original roots lets _persist strip exactly those, so the
    # scratch SM reads back is the chip + the node's real writes and nothing else.
    original_roots = _state_root_keys(state_path) if state_path else None
    if isolate:
        # docs/245 (D-02): keep the scratch's pre-run bytes and watch what the node
        # itself changes (its record_state_updates blocks, its machine since it was
        # loaded), so the proposal is the node's writes -- never a whole machine the
        # node swapped in (an offline replay's load_from_id) or reserialized.
        original_files = _snapshot_files(str(state_path))
        recorder = _UpdateRecorder()
        recorder.install()
        try:
            ns = runpy.run_path(str(target), run_name="__main__")
        except SystemExit as exc:
            recorder.uninstall()
            if exc.code in (0, None):
                # main() counts sys.exit(0) as a success: keep what the node did
                # (runpy's namespace is lost; the recorder saw the node)
                _ISOLATION_REPORT.update(_persist_isolated(
                    None, str(state_path), original_roots, original_files, recorder,
                    replay=replay))
            else:
                _restore_files(str(state_path), original_files)
            raise
        except BaseException:
            # a node that died after its own save must not leave a reserialized or
            # swapped machine in the scratch for SM to read as "its writes"
            _restore_files(str(state_path), original_files)
            raise
        finally:
            recorder.uninstall()
        _ISOLATION_REPORT.update(_persist_isolated(
            ns, str(state_path), original_roots, original_files, recorder, replay=replay))
        return
    ns = runpy.run_path(str(target), run_name="__main__")
    if state_path:
        _persist_node_state(ns, str(state_path), original_roots)


# docs/245: what the isolated run proposed and why (copied into _result.json)
_ISOLATION_REPORT: dict = {}


def _config_pinned_to_scratch(config_file: str, state_path: str) -> str:
    """docs/174 amended II: return a copy of the qualibrate config whose
    ``[quam] state_path`` is repointed at *state_path* (the per-run scratch), so
    the node's framework-level ``machine.save()`` writes the scratch, never the
    live chip. ``state_path`` is the only ``state_path =`` key in a qualibrate
    config (storage uses ``location``), so a line-wise rewrite is safe. TOML basic
    strings take forward slashes; backslashes would be escapes. Best-effort: any
    failure returns the original config path unchanged."""
    import re
    try:
        text = Path(config_file).read_text(encoding="utf-8")
        scratch = str(state_path).replace("\\", "/")
        new_line = f'state_path = "{scratch}"'
        out, replaced = [], False
        for line in text.splitlines():
            if re.match(r"\s*state_path\s*=", line):
                out.append(new_line); replaced = True
            else:
                out.append(line)
        if not replaced:
            return config_file
        dst = Path(state_path).parent / "qualibrate_config.scratch.toml"
        dst.write_text("\n".join(out) + "\n", encoding="utf-8")
        return str(dst)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[run_experiment] config repoint skipped: {type(exc).__name__}: {exc}\n")
        return config_file


_SCRATCH_CONFIG_NAME = "qualibrate_config.scratch.toml"


def _resolve_env_config_path() -> Path:
    """The qualibrate config file this env resolves on its own
    (QUALIBRATE_CONFIG_FILE, else ~/.qualibrate/config.toml), via
    qualibrate_config's own resolver."""
    from qualibrate_config.resolvers import get_qualibrate_config_path
    return Path(get_qualibrate_config_path())


def _framework_state_path(config_path) -> str | None:
    """The state path the qualibrate FRAMEWORK saves the machine to under this
    config -- qualibrate's own resolver, where ``[quam] state_path`` wins over the
    QUAM_STATE_PATH env (measured on qualibrate 1.5.1). Falls back to the raw
    merged ``[quam] state_path`` when the typed resolver is not importable."""
    try:
        from qualibrate_config.resolvers import get_qualibrate_config
        from qualibrate.core.config.resolvers import get_quam_state_path
    except ImportError:
        from qualibrate_config.file import read_config_file
        raw = read_config_file(Path(config_path), solve_references=True) or {}
        sp = (raw.get("quam") or {}).get("state_path")
        return None if sp is None else str(sp)
    sp = get_quam_state_path(get_qualibrate_config(Path(config_path)))
    return None if sp is None else str(sp)


def _same_path(a, b) -> bool:
    import os
    norm = lambda p: os.path.normcase(os.path.normpath(os.path.abspath(str(p))))  # noqa: E731
    return norm(a) == norm(b)


def _pin_config_strict(config_file: str | None, state_path: str):
    """docs/245 (D-01): write a qualibrate config whose framework state path is
    *state_path* and PROVE it. Returns ``(path, None)`` or ``(None, reason)``.

    Source: *config_file* when the Runner verified one, else the config this env
    resolves itself. The copy is the project-MERGED config (qualibrate_config's own
    reader applies ``projects/<p>/config.toml``; at the new location that overlay
    is absent, so nothing is applied twice), so storage/library settings living in
    a project overlay survive the move next to the scratch -- a copy of the root
    file alone would drop them. ``[quam] state_path`` is set to the scratch, and
    the copy re-read through qualibrate's own resolver must name the scratch."""
    try:
        src = Path(config_file) if config_file else _resolve_env_config_path()
    except Exception as exc:  # noqa: BLE001
        return None, f"the env's qualibrate config could not be resolved ({type(exc).__name__}: {exc})"
    if not src.is_file():
        return None, f"no qualibrate config file at {src}, so the node's own save cannot be pinned to the scratch"
    dst = Path(state_path).parent / _SCRATCH_CONFIG_NAME
    try:
        from qualibrate_config.file import read_config_file
        import tomli_w
    except ImportError:
        # no qualibrate_config/tomli_w: the docs/174 line-wise rewrite of the root
        # file; the resolver check below still has the last word.
        out = _config_pinned_to_scratch(str(src), state_path)
        if out == str(src):
            return None, f"could not repoint [quam] state_path in {src}"
        dst = Path(out)
    else:
        try:
            merged = read_config_file(src, solve_references=False) or {}
            quam = merged.get("quam")
            if not isinstance(quam, dict):
                quam = merged["quam"] = {}
            quam["state_path"] = str(state_path).replace("\\", "/")
            dst.write_text(tomli_w.dumps(merged), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return None, f"could not write the pinned config ({type(exc).__name__}: {exc})"
    try:
        got = _framework_state_path(dst)
    except Exception as exc:  # noqa: BLE001
        return None, f"the pinned config does not resolve ({type(exc).__name__}: {exc})"
    if got is None or not _same_path(got, state_path):
        return None, (f"the pinned config resolves the framework state path to {got!r}, "
                      f"not the scratch {state_path!r}")
    try:
        from qualibrate.core.config.resolvers import invalidate_settings_cache
        invalidate_settings_cache()
    except Exception:  # noqa: BLE001
        pass
    return str(dst), None


# ---------------------------------------------------------------------------
# docs/245 (D-02): an isolated run's writes are what the node itself changed on
# its machine -- never the whole machine it happens to hold when it saves
# ---------------------------------------------------------------------------

class _Deleted:
    """A key the node removed from its machine (a write that deletes)."""

    def __repr__(self) -> str:
        return "<deleted>"


_DELETED = _Deleted()


def _snapshot_files(state_path: str) -> dict:
    """The scratch's pre-run bytes per file (state.json / wiring.json)."""
    out = {}
    for name in ("state.json", "wiring.json"):
        try:
            out[name] = (Path(state_path) / name).read_bytes()
        except OSError:
            out[name] = None
    return out


def _restore_files(state_path: str, original_files: dict) -> None:
    for name, data in (original_files or {}).items():
        if data is None:
            continue
        try:
            (Path(state_path) / name).write_bytes(data)
        except OSError as exc:
            sys.stderr.write(f"[run_experiment] could not restore {name}: {exc}\n")


def _merged_files(blobs: dict) -> dict:
    """state.json + wiring.json merged (SM's own layout), from bytes per file."""
    out: dict = {}
    for name in ("state.json", "wiring.json"):
        data = (blobs or {}).get(name)
        if data is None:
            continue
        try:
            d = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(d, dict):
            out.update(d)
    return out


def _machine_dict(machine):
    """A DEEP copy of the machine's serialized form (``to_dict`` may hand back
    the machine's own mutable leaves, which a later in-place change would then
    rewrite on both sides of the diff)."""
    import copy
    if machine is None or not hasattr(machine, "to_dict"):
        return None
    try:
        d = machine.to_dict(include_defaults=True)
    except TypeError:
        d = machine.to_dict()
    try:
        return copy.deepcopy(d)
    except Exception:  # noqa: BLE001
        return d


def _ptr_escape(seg) -> str:
    return str(seg).replace("~", "~0").replace("/", "~1")


def _differs(a, b) -> bool:
    if type(a) is not type(b):
        return True
    if isinstance(a, float) and a != a and b != b:   # NaN stays NaN: not a write
        return False
    try:
        return bool(a != b)
    except Exception:  # noqa: BLE001 -- e.g. numpy arrays: elementwise !=
        try:
            import numpy as np
            return not np.array_equal(a, b)
        except Exception:  # noqa: BLE001
            return True


def _leaf_delta(pre, post, path: str, out: dict) -> None:
    """``out[pointer] = new`` for every leaf that differs between two machine
    dicts; ``out[pointer] = _DELETED`` for a key *post* no longer has (a pulse
    replaced by another class drops the old class's fields -- leaving them would
    make the merged dict unloadable). A list that changed length is one write of
    the whole list (SM's route_writes names a resized list and holds it)."""
    if isinstance(pre, dict) and isinstance(post, dict):
        for k, v in post.items():
            sub = f"{path}/{_ptr_escape(k)}"
            if k not in pre:
                out[sub] = v
            else:
                _leaf_delta(pre[k], v, sub, out)
        for k in pre:
            if k not in post:
                out[f"{path}/{_ptr_escape(k)}"] = _DELETED
        return
    if isinstance(pre, list) and isinstance(post, list) and len(pre) == len(post):
        for i, (x, y) in enumerate(zip(pre, post)):
            _leaf_delta(x, y, f"{path}/{i}", out)
        return
    if _differs(pre, post):
        out[path] = post


class _UpdateRecorder:
    """Watches one isolated run's ``QualibrationNode`` for the two things that
    say what the node itself changed:

    * every ``record_state_updates`` block -- the machine dict before vs after is
      the node's DECLARED update. (With ``interactive_only=True``, the default, a
      non-interactive run applies the change and records nothing; with
      ``interactive_only=False`` qualibrate reverts the simple replaces and records
      them in ``node.state_updates`` instead, which ``_persist_isolated`` folds in.)
    * every assignment to ``node.machine`` (the constructor's ``machine=Quam.load()``
      and an offline replay's ``load_from_id`` both go through the property
      setter) -- the machine dict at its LAST assignment is the baseline the
      node's own changes are measured from, so a replay's swapped-in stored
      snapshot is never one of them.

    Best-effort: without qualibrate nothing is installed (``installed`` False)."""

    def __init__(self):
        self.blocks = 0
        self.writes: dict = {}
        self.node = None
        self.base = None          # machine dict at its last assignment
        self.base_id = None       # id() of that machine object
        self.assignments = 0
        self.installed = False
        self._patches: list = []  # (cls, name, had_own, original)

    def _patch(self, cls, name: str, new) -> None:
        had_own = name in cls.__dict__
        self._patches.append((cls, name, had_own, cls.__dict__.get(name)))
        setattr(cls, name, new)

    def install(self) -> None:
        import contextlib
        import inspect
        try:
            from qualibrate.core.qualibration_node import QualibrationNode
        except Exception:  # noqa: BLE001
            return
        rec = self
        orig_block = inspect.getattr_static(QualibrationNode, "record_state_updates", None)
        if callable(orig_block) and not isinstance(orig_block, (staticmethod, classmethod)):
            @contextlib.contextmanager
            def record_state_updates(node_self, *args, **kwargs):
                rec.node = node_self
                pre = _machine_dict(getattr(node_self, "machine", None))
                with orig_block(node_self, *args, **kwargs):
                    yield
                post = _machine_dict(getattr(node_self, "machine", None))
                rec.blocks += 1
                if pre is not None and post is not None:
                    _leaf_delta(pre, post, "", rec.writes)

            record_state_updates.__doc__ = getattr(orig_block, "__doc__", None)
            self._patch(QualibrationNode, "record_state_updates", record_state_updates)
        prop = inspect.getattr_static(QualibrationNode, "machine", None)
        if isinstance(prop, property) and prop.fset is not None:
            def _set_machine(node_self, value):
                prop.fset(node_self, value)
                try:
                    rec.node = node_self
                    rec.base = _machine_dict(value)
                    rec.base_id = id(value) if rec.base is not None else None
                    rec.assignments += 1
                except Exception:  # noqa: BLE001 -- never break the node's own assignment
                    rec.base = rec.base_id = None

            self._patch(QualibrationNode, "machine",
                        property(prop.fget, _set_machine, prop.fdel, prop.__doc__))
        self.installed = bool(self._patches)

    def uninstall(self) -> None:
        while self._patches:
            cls, name, had_own, original = self._patches.pop()
            if had_own:
                setattr(cls, name, original)
            else:
                try:
                    delattr(cls, name)
                except AttributeError:
                    pass

    def since_load(self, machine) -> dict | None:
        """The node's changes to *machine* since it was last assigned, or None
        when that machine's assignment was not observed."""
        if machine is None or self.base is None or self.base_id != id(machine):
            return None
        post = _machine_dict(machine)
        if post is None:
            return None
        out: dict = {}
        _leaf_delta(self.base, post, "", out)
        return out


def _json_safe(o):
    if hasattr(o, "item"):
        try:
            return o.item()
        except Exception:  # noqa: BLE001
            pass
    if hasattr(o, "tolist"):
        return o.tolist()
    return str(o)


def _apply_pointer_writes(state_path: str, writes: dict) -> tuple[int, list]:
    """Write ``{"/a/b/c": value}`` (``_DELETED`` removes the key) onto the
    scratch's JSON files (the merged state+wiring layout SM diffs). A top-level
    key goes to whichever file already holds it, else state.json. Returns
    ``(n_applied, [(pointer, why_skipped)])``."""
    files = {}
    for name in ("state.json", "wiring.json"):
        try:
            d = json.loads((Path(state_path) / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = None
        if isinstance(d, dict):
            files[name] = d
    if "state.json" not in files:
        return 0, [(k, "scratch state.json unreadable") for k in writes]
    dirty, applied, skipped = set(), 0, []
    for ptr, val in writes.items():
        parts = [s.replace("~1", "/").replace("~0", "~") for s in ptr.split("/")[1:]]
        if not parts:
            skipped.append((ptr, "the root itself"))
            continue
        delete = val is _DELETED
        name = next((n for n, d in files.items() if parts[0] in d), "state.json")
        obj = files[name]
        try:
            for seg in parts[:-1]:
                if isinstance(obj, list):
                    obj = obj[int(seg)]
                elif isinstance(obj, dict):
                    if delete and seg not in obj:
                        obj = None          # nothing there to delete
                        break
                    obj = obj.setdefault(seg, {})
                else:
                    raise TypeError(f"{type(obj).__name__} is not a container")
            leaf = parts[-1]
            if delete:
                if isinstance(obj, dict) and leaf in obj:
                    del obj[leaf]
                    dirty.add(name)
                applied += 1                # a key the file never had is already absent
                continue
            if isinstance(obj, list):
                obj[int(leaf)] = val
            elif isinstance(obj, dict):
                obj[leaf] = val
            else:
                raise TypeError(f"{type(obj).__name__} is not a container")
            dirty.add(name)
            applied += 1
        except Exception as exc:  # noqa: BLE001
            skipped.append((ptr, f"{type(exc).__name__}: {exc}"))
    for name in dirty:
        (Path(state_path) / name).write_text(
            json.dumps(files[name], indent=4, default=_json_safe), encoding="utf-8")
    return applied, skipped


def _recorded_state_updates(node) -> dict:
    """``node.state_updates`` (the interactive_only=False style) as ``{ptr: new}``."""
    out = {}
    try:
        updates = dict(getattr(node, "state_updates", {}) or {})
    except Exception:  # noqa: BLE001
        return out
    for key, rec in updates.items():
        if not isinstance(rec, dict) or "new" not in rec:
            continue
        ref = str(rec.get("key") or key)
        out["/" + ref.lstrip("#").lstrip("/")] = rec["new"]
    return out


def _is_replay(node, replay: bool) -> bool:
    if replay:
        return True
    params = getattr(node, "parameters", None)
    return getattr(params, "load_data_id", None) is not None


def _covered(ptr: str, writes: dict) -> bool:
    if ptr in writes:
        return True
    return any(ptr.startswith(w + "/") for w in writes)


def _log_isolation(msg: str) -> None:
    sys.stderr.write(f"[run_experiment] isolation: {msg}\n")


def _persist_isolated(ns, state_path: str, original_roots, original_files: dict,
                      recorder: "_UpdateRecorder", *, replay: bool = False) -> dict:
    """docs/245 (D-02): an isolated run's scratch becomes its pre-run bytes plus the
    node's OWN writes, and nothing else. Returns a report (also logged).

    The node's own save (pinned to the scratch since D-01) reserializes the machine
    it holds: after an offline replay's ``load_from_id`` that is the STORED run's
    whole snapshot (that day's network, data folder, every other qubit); after any
    run it also carries serializer defaults. So the pre-run bytes are restored and
    one source of writes is applied on top, in this order:

    1. ``record_state_updates`` blocks (+ ``node.state_updates``) -- the node's
       declared update. A change the node made to its machine OUTSIDE every block
       (measurement scaffolding) is not proposed; it is listed in the log.
    2. no block: the node's machine changes since that machine was last assigned
       (a node like 17d/15e writes ``node.machine.x = ...`` directly) -- every
       change it made, never the replay's swapped-in snapshot.
    3. neither observable (qualibrate not importable / the machine was not set
       through ``node.machine``): a normal run falls back to the docs/173 S9
       ``machine.save()`` + phantom-root strip (its machine came from this
       scratch); a replay proposes NOTHING, loudly -- its machine is another run's.

    Any change the node's own save left in the scratch that is not proposed is
    counted in the log, so a write that bypassed ``node.machine`` is never dropped
    silently."""
    report = {"source": None, "blocks": recorder.blocks, "proposed": 0,
              "replay": False, "not_proposed": [], "skipped": []}
    try:
        node = (ns.get("node") if isinstance(ns, dict) else None) or recorder.node
        machine = getattr(node, "machine", None)
        is_replay = _is_replay(node, replay)
        report["replay"] = is_replay
        since_load = recorder.since_load(machine)
        declared = dict(recorder.writes)
        declared.update(_recorded_state_updates(node))
        # what the node's own save(s) left in the scratch, for the disclosure below
        footprint: dict = {}
        _leaf_delta(_merged_files(original_files),
                    _merged_files(_snapshot_files(state_path)), "", footprint)
        if recorder.blocks or declared:
            writes, source = declared, "record_state_updates"
            outside = [p for p in (since_load or {}) if not _covered(p, writes)]
            if outside:
                _log_isolation(f"{len(outside)} change(s) the node made to its machine OUTSIDE its "
                               f"record_state_updates block(s) are NOT proposed: "
                               + ", ".join(outside[:8]) + (" ..." if len(outside) > 8 else ""))
            report["not_proposed"] = outside[:50]
        elif since_load is not None:
            writes, source = since_load, "machine_since_load"
            _log_isolation("the node opened no record_state_updates block: proposing every change it "
                           f"made to its machine since it was loaded ({len(writes)})")
        elif not is_replay:
            report["source"] = "machine_save"
            _log_isolation("the node's machine was not observed: falling back to saving the machine "
                           "it holds (docs/173 S9; loaded from this scratch, so its changes are its own)")
            _persist_node_state(ns if isinstance(ns, dict) else {"node": node},
                                state_path, original_roots)
            return report
        else:
            writes, source = {}, "none"
            _log_isolation("an offline replay whose machine changes could not be observed (no "
                           "record_state_updates block, no observed load): proposing NOTHING -- the "
                           "machine it holds is the stored run's, not this run's writes")
        report["source"] = source
        _restore_files(state_path, original_files)
        applied, skipped = _apply_pointer_writes(state_path, writes)
        report["proposed"], report["skipped"] = applied, [p for p, _ in skipped][:50]
        dropped = [p for p in footprint if not _covered(p, writes)]
        _log_isolation(f"proposing {applied} write(s) from {source} "
                       f"({recorder.blocks} block(s), replay={is_replay})")
        if dropped:
            _log_isolation(f"the node's own save rewrote {len(dropped)} other leaf(s) of the scratch "
                           "(serializer defaults, a replay's stored snapshot, or a write that bypassed "
                           "node.machine); discarded, NOT proposed: "
                           + ", ".join(dropped[:8]) + (" ..." if len(dropped) > 8 else ""))
        for ptr, why in skipped[:20]:
            _log_isolation(f"write skipped {ptr}: {why}")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[run_experiment] state capture skipped: {type(exc).__name__}: {exc}\n")
        report["source"] = "error"
        # never leave a reserialized / swapped machine behind as "the node's writes"
        _restore_files(state_path, original_files)
    return report


def _state_root_keys(state_path: str) -> set | None:
    """The top-level keys of ``state.json`` at *state_path* right now, or None if
    it can't be read. Captured BEFORE the node's ``machine.save()`` so
    _strip_phantom_roots can tell a class-default root the serializer added from a
    key the chip genuinely had (docs/174 amended)."""
    try:
        raw = json.loads((Path(state_path) / "state.json").read_text(encoding="utf-8"))
        return set(raw.keys()) if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _strip_phantom_roots(state_path: str, original_roots: set | None, updates: dict) -> None:
    """docs/174 (amended -- the real fix, found on a real customer's cloud chain):
    ``machine.save()`` writes back EVERY field the quam class declares, so it adds
    top-level ROOT keys the customer's state.json never had (that lab's class's
    ``flux_crosstalk_max_v`` / ``require_flux_crosstalk_dc`` / ``twpa_ext``). The
    first docs/174 fix only cancelled these in SM's DIFF; but SM's post-run adopt
    copies the scratch's FULL state into the working copy (byte-identical, then to
    live), so the phantom roots still reached live and diverged it -> ``stale_live``
    on the very next node.

    Fix at the source: after the node's save, delete any top-level key that (a) the
    chip did not originally have AND (b) the node's ``state_updates`` did not write.
    The scratch SM reads back is then the chip + the node's real writes and nothing
    else, so nothing downstream (diff, apply, adopt, auto-sync) ever sees a phantom.
    A node that genuinely writes a new root key keeps it (its ref is in
    ``state_updates``). Best-effort and never fatal."""
    if original_roots is None:
        return
    try:
        sp = Path(state_path) / "state.json"
        state = json.loads(sp.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return
        touched = set()
        for key, rec in (updates or {}).items():
            ref = (rec or {}).get("key") or key
            seg = str(ref).lstrip("#/").split("/")[0]
            if seg:
                touched.add(seg)
        removed = [k for k in list(state.keys())
                   if k not in original_roots and k not in touched]
        if not removed:
            return
        for k in removed:
            del state[k]
        sp.write_text(json.dumps(state, indent=4), encoding="utf-8")
        sys.stderr.write(f"[run_experiment] stripped {len(removed)} class-default root key(s): "
                         f"{', '.join(removed)}\n")
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[run_experiment] phantom-root strip skipped: {type(exc).__name__}: {exc}\n")


def _persist_node_state(ns: dict, state_path: str, original_roots: set | None = None) -> None:
    """docs/173 S9 (found on a real customer cloud backend): a qualibrate node NEVER
    rewrites the state.json at QUAM_STATE_PATH. In a non-interactive run its
    ``record_state_updates()`` either applies the calibration to the in-memory
    machine and records nothing (interactive_only=True, the customer default),
    or reverts the machine and records the diff in ``node.state_updates``
    (interactive_only=False). ``node.save()`` only writes the run's storage
    snapshot. So the Scheduler's scratch state.json stays byte-identical and
    SM's leaf-diff sees NO writes.

    Here, on SM's own subprocess boundary, we make the node's PROPOSED state the
    thing on disk at QUAM_STATE_PATH: apply any recorded ``state_updates`` back
    onto the machine (the reverted case), then ``machine.save()`` to the scratch.
    The live chip is never touched -- state_path is always the Scheduler's
    per-run scratch copy. Best-effort and never fatal: a run that produced no
    node object, or a machine without ``save``, just leaves the scratch as-is
    (the pre-S9 behaviour) rather than failing the whole run.
    """
    try:
        node = ns.get("node") if isinstance(ns, dict) else None
        machine = getattr(node, "machine", None)
        if node is None or machine is None or not hasattr(machine, "save"):
            return
        updates = {}
        try:
            updates = dict(getattr(node, "state_updates", {}) or {})
        except Exception:  # noqa: BLE001
            updates = {}
        for key, rec in updates.items():
            # rec = {"key": "#/qubits/qA1/resonator/time_of_flight", "attr": ..., "old": .., "new": ..}
            ref = (rec or {}).get("key") or key
            if "new" not in (rec or {}):
                continue
            try:
                from qualibrate.core.utils.node.record_state_update import update_machine_attribute
                update_machine_attribute(machine, ref, rec["new"])   # writes rec["new"] at ref
            except Exception:  # noqa: BLE001
                _set_by_ref(machine, ref, rec["new"])
        machine.save()
        # docs/174 amended: remove the class-default root keys machine.save() just
        # materialized, so the scratch (and everything SM adopts from it) matches
        # the chip's own schema and never diverges live on the next node.
        _strip_phantom_roots(state_path, original_roots, updates)
    except Exception as exc:  # noqa: BLE001
        # never let the capture step fail a run that already measured on hardware
        sys.stderr.write(f"[run_experiment] state capture skipped: {type(exc).__name__}: {exc}\n")


def _set_by_ref(machine, ref: str, value) -> None:
    """Fallback setter for a ``#/a/b/c`` quam reference when qualibrate's own
    helper is unavailable: walk attributes/items and set the leaf."""
    parts = [p for p in str(ref).lstrip("#/").split("/") if p]
    obj = machine
    for p in parts[:-1]:
        if isinstance(obj, dict):
            obj = obj[p]
        elif isinstance(obj, (list, tuple)):
            obj = obj[int(p)]
        else:
            obj = getattr(obj, p)
    leaf = parts[-1]
    if isinstance(obj, dict):
        obj[leaf] = value
    elif isinstance(obj, list):
        obj[int(leaf)] = value
    else:
        setattr(obj, leaf, value)


# ---------------------------------------------------------------------------
# scan — full parameter schemas via qualibrate inspection (hardware-safe)
# ---------------------------------------------------------------------------

def run_scan(folder: str) -> dict:
    """Discover nodes/graphs in *folder* + their full parameter JSON-schemas.

    Uses qualibrate's inspection-mode library scan: each file is imported, but
    the QualibrationNode/Graph constructor raises StopInspection BEFORE the
    experiment body / Quam.load() runs — so no hardware is touched even for
    dict-style graphs that call g.run() at module top level. Each runnable's
    ``.serialize()`` yields ``{description, parameters: <resolved json-schema>}``.
    """
    from pathlib import Path as _Path

    from qualibrate import QualibrationLibrary

    lib = QualibrationLibrary(library_folder=_Path(folder), set_active=False)
    items: list[dict] = []

    def _collect(collection, kind: str) -> None:
        try:
            names = list(collection.keys())
        except Exception:
            try:
                names = list(collection)
            except Exception:
                names = []
        for name in names:
            entry = {"name": name, "kind": kind, "description": "",
                     "parameters": {}, "targets_name": None, "error": None}
            try:
                runnable = (collection.get_nocopy(name)
                            if hasattr(collection, "get_nocopy") else collection[name])
                # The library captures runnables as PlaceholderNode/Graph whose own
                # serialize() has parameters=null; the full field schema (type,
                # default, description, enum, is_targets) lives on parameters_class.
                try:
                    entry["description"] = (runnable.serialize() or {}).get("description") or ""
                except Exception:
                    pass
                pc = getattr(runnable, "parameters_class", None)
                if pc is not None:
                    entry["parameters"] = dict(pc.serialize())
                    entry["targets_name"] = getattr(pc, "targets_name", None)
            except Exception as exc:  # noqa: BLE001 - one bad file shouldn't sink the scan
                entry["error"] = f"{type(exc).__name__}: {exc}"
            items.append(entry)

    _collect(lib.nodes, "node")
    _collect(lib.graphs, "graph")
    return {"items": items}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Scheduler experiment runner")
    parser.add_argument(
        "--mode", required=True,
        choices=("report-config", "scan", "run"),
        help="report-config: dump the env's effective qualibrate config; "
             "scan: list nodes/graphs + parameter schemas (inspection); "
             "run: execute a prepared node/graph copy",
    )
    parser.add_argument(
        "--out", required=True,
        help="work directory that _result.json is written to",
    )
    parser.add_argument("--folder", help="calibrations folder to scan (scan mode)")
    parser.add_argument("--target", help="prepared node/graph .py to run (run mode)")
    parser.add_argument("--state-path", help="QUAM_STATE_PATH for the run (run mode)")
    parser.add_argument("--baseline-out", help="dir to write the serializer-normalized "
                        "pre-node baseline into, for SM's leaf diff (run mode; docs/174)")
    parser.add_argument("--config-file", help="QUALIBRATE_CONFIG_FILE for the run (run mode)")
    parser.add_argument("--isolate", action="store_true",
                        help="--state-path is a per-run scratch: pin the framework's save there "
                             "or refuse, and leave only the node's recorded writes in it "
                             "(run mode; docs/245)")
    parser.add_argument("--replay", action="store_true",
                        help="the run is an offline replay (load_data_id) (run mode; docs/245)")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "status": "error",
        "mode": args.mode,
        "versions": {},
        "error": None,
        "traceback": None,
        "config": None,
        "editable_install": None,
    }

    if args.mode == "run":
        result["target"] = args.target

    try:
        versions = _library_versions()
        versions["qualibrate"] = _qualibrate_version()
        result["versions"] = versions
        if args.mode == "report-config":
            result.update(run_report_config())
        elif args.mode == "scan":
            if not args.folder:
                raise ValueError("--folder is required for scan mode")
            result.update(run_scan(args.folder))
        elif args.mode == "run":
            run_target(args.target, args.state_path, args.config_file, args.baseline_out,
                       isolate=args.isolate, replay=args.replay)
        result["status"] = "ok"
    except SystemExit as exc:
        # A node that calls sys.exit() is not a crash; exit code 0/None = success.
        if exc.code in (0, None):
            result["status"] = "ok"
        else:
            result["status"] = "error"
            result["error"] = f"SystemExit: {exc.code}"
    except Exception as exc:  # noqa: BLE001 - top-level guard
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    finally:
        if _ISOLATION_REPORT:
            result["isolation"] = dict(_ISOLATION_REPORT)
        # Always write _result.json (even on SystemExit / KeyboardInterrupt) so
        # the parent classifies the run instead of seeing 'no _result.json'.
        result_path = out_dir / RESULT_FILENAME
        try:
            with open(result_path, "w", encoding="utf-8") as fh:
                json.dump(result, fh, indent=2)
        except OSError:
            pass

    print(json.dumps({"status": result["status"],
                      "result_file": str(out_dir / RESULT_FILENAME)}))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
