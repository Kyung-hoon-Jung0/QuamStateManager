"""Shared helpers for the standalone ``generator/`` scripts.

Imported ONLY by the sibling scripts (``run_build.py``,
``run_generate_config.py``) that run under an *external* user-selected
interpreter — it is NEVER imported by ``quam_state_manager`` and may use
only the Python standard library. The scripts add this directory to
``sys.path`` defensively before importing (CPython normally prepends the
script's own directory, but ``PYTHONSAFEPATH`` / ``-P`` suppress that).

PyInstaller ships the whole ``generator/`` directory as data files
(build/quam-manager.spec), so this module travels with the scripts in the
frozen bundle automatically.
"""


# The customer-local package providing QDAC-II QUAM components (QdacInstrument,
# QdacBiasLine, QdacBiasedFixedFrequencyTransmon). There is no upstream
# qualang_tools/quam_builder support for QDAC-II — this module only exists in
# specific customer environments. Centralized here (not hardcoded separately
# in run_build.py and probe_capabilities.py) so both scripts agree on where to
# look and a future rename touches one line.
QDAC_COMPONENTS_MODULE = "quam_config.qdac_components"


def library_versions() -> dict:
    """Best-effort version string for each QM library the scripts rely on."""
    from importlib.metadata import version, PackageNotFoundError

    # module import name -> candidate distribution names
    candidates = {
        "qualang_tools": ("qualang-tools", "qualang_tools"),
        "quam_builder": ("quam-builder", "quam_builder"),
        "quam": ("quam",),
        "qm": ("qm-qua", "qm"),
    }
    out = {}
    for mod, dists in candidates.items():
        ver = None
        for dist in dists:
            try:
                ver = version(dist)
                break
            except PackageNotFoundError:
                continue
            except Exception as exc:  # pragma: no cover - defensive
                ver = f"<error: {exc}>"
                break
        out[mod] = ver or "<not installed>"
    return out


def out_of_env_sources() -> dict:
    """``{file: [mtime_ns, size]}`` for every loaded module that lives OUTSIDE
    the interpreter's own install (docs/2xx adaptive pulses).

    Those are the files a lab edits in place -- an editable ``quam_config``,
    a package on ``PYTHONPATH`` -- and editing one does NOT move the
    ``site-packages`` mtime the env signature is built from. A cache of a
    class schema or of a waveform computed by that code is only as fresh as
    these files, so the SM side re-stats them on every read and treats any
    difference as a miss (validate on read). The stat is taken HERE, in the
    process that imported the code, so an edit landing after the import
    already disagrees with the recorded value.
    """
    import os
    import sys

    roots = set()
    for p in {sys.prefix, sys.base_prefix, getattr(sys, "exec_prefix", "")}:
        if p:
            roots.add(os.path.normcase(os.path.abspath(p)))
    out = {}
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not isinstance(f, str) or not f.endswith(".py"):
            continue
        try:
            af = os.path.abspath(f)
        except (OSError, ValueError):
            continue
        nf = os.path.normcase(af)
        if any(nf == r or nf.startswith(r + os.sep) for r in roots):
            continue
        if os.sep + "site-packages" + os.sep in nf:
            continue
        # SM's own generator scripts are the launcher, not lab code
        if os.path.basename(os.path.dirname(nf)) == "generator" and \
                os.path.basename(nf).startswith(("probe_", "run_", "_script_common")):
            continue
        try:
            st = os.stat(af)
        except OSError:
            continue
        out[af] = [st.st_mtime_ns, st.st_size]
    return dict(sorted(out.items()))
