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
