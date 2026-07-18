# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for QUAM State Manager
#
# Build:   pyinstaller build/quam-manager.spec
# Output:  dist/quam-manager/  (onedir bundle)
# Run:     dist/quam-manager/quam-manager.exe
#
# Uses onedir mode (NOT onefile) for instant cold start.
# onefile extracts to a temp folder on every launch, adding 3-10s of startup
# delay.  onedir starts instantly and distributes as a zip of the folder.

import os
import sys

block_cipher = None

# Paths relative to this spec file
_spec_dir = os.path.dirname(os.path.abspath(SPEC))
_project_root = os.path.dirname(_spec_dir)
_pkg = os.path.join(_project_root, "quam_state_manager")

a = Analysis(
    [os.path.join(_pkg, "main.py")],
    pathex=[_project_root],
    binaries=[],
    datas=[
        (os.path.join(_pkg, "web", "templates"), os.path.join("quam_state_manager", "web", "templates")),
        (os.path.join(_pkg, "web", "static"), os.path.join("quam_state_manager", "web", "static")),
        # The config generator runs under an external QM-capable interpreter,
        # so it must ship as a plain .py file, not be frozen into the exe.
        (os.path.join(_pkg, "generator"), os.path.join("quam_state_manager", "generator")),
    ],
    hiddenimports=[
        "quam_state_manager",
        "quam_state_manager.core",
        "quam_state_manager.core.pointer_resolver",
        "quam_state_manager.core.loader",
        "quam_state_manager.core.scanner",
        "quam_state_manager.core.search_index",
        "quam_state_manager.core.query",
        "quam_state_manager.core.modifier",
        "quam_state_manager.core.saver",
        "quam_state_manager.core.differ",
        "quam_state_manager.core.config_generator",
        "quam_state_manager.core.state_env_schema",
        # Pulses-page waveform synthesis (lazy-imported in routes.py) + its
        # scipy dependency — pin so the frozen bundle keeps them.
        "quam_state_manager.core.waveform_synth",
        "scipy",
        "scipy.ndimage",
        "scipy.signal.windows",
        "quam_state_manager.web",
        "quam_state_manager.web.app",
        "quam_state_manager.web.routes",
        # routes.py imports _parse_value from cli.py, which imports typer/rich/click
        # at module top — pin them so the frozen web app doesn't miss typer/rich's
        # dynamic bits (their absence 500s /field/edit on first use).
        "quam_state_manager.cli",
        "typer",
        "rich",
        "click",
        "flask",
        "jinja2",
        "webview",
        "h5py",
        "h5py.h5ac",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy.testing",
        "pytest",
        "IPython",
        "notebook",
        # NOTE: scipy must NOT be excluded — core/waveform_synth.py imports
        # scipy.ndimage + scipy.signal.windows at module top for the Pulses-page
        # waveform preview/sparklines/synth. Excluding it makes those routes 500
        # in the frozen exe (invisible from a source run). See the pre-delivery
        # audit's packaging finding.
    ],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="quam-manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX OFF: it is a known cause of frozen-exe crashes when it compresses native
    # DLLs (scipy/h5py/numpy MKL, pywebview's WebView2 loader) — corrupted or
    # AV-quarantined on Windows. The onedir startup win doesn't need it.
    upx=False,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="quam-manager",
)
