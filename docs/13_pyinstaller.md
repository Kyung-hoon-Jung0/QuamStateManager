# 13 -- PyInstaller Packaging (`build/quam-manager.spec`)

## What was done

Created the full PyInstaller packaging pipeline to produce a distributable Windows desktop application from the QUAM State Manager source code. The output is a folder (`dist/quam-manager/`) containing `quam-manager.exe` and all dependencies -- users double-click the exe and the app launches instantly.

### Files created / modified

| File | Purpose |
|------|---------|
| `build/quam-manager.spec` | PyInstaller spec file -- defines entry point, bundled data, hidden imports, excludes |
| `quam_state_manager/web/static/htmx.min.js` | Bundled HTMX 2.0.4 (previously loaded from CDN) |
| `quam_state_manager/web/static/pico.min.css` | Bundled Pico CSS v2 (previously loaded from CDN) |
| `quam_state_manager/web/static/plotly.min.js` | Bundled Plotly 2.35.2 (previously loaded from CDN) |
| `quam_state_manager/web/templates/base.html` | Updated to load CSS/JS from local static files |
| `quam_state_manager/web/templates/_wiring.html` | Updated Plotly script to local static |
| `quam_state_manager/web/templates/_compare.html` | Updated Plotly script to local static |
| `quam_state_manager/web/app.py` | Fixed `_resource_path()` for correct `sys._MEIPASS` resolution |
| `.gitignore` | Created -- excludes `dist/`, `build/work/`, `__pycache__/`, etc. |

## Key design decisions

### `onedir` mode (NOT `onefile`)

The spec uses `onedir` mode which produces a folder structure:

```
dist/quam-manager/
├── quam-manager.exe          ← Double-click to launch
└── _internal/
    ├── python313.dll
    ├── quam_state_manager/web/templates/   ← All HTML templates
    ├── quam_state_manager/web/static/      ← CSS + JS assets
    ├── webview/                       ← pywebview + WebView2
    └── ...                            ← Python stdlib, Flask, etc.
```

**Why not `onefile`?** `onefile` mode extracts the entire bundle to a temp folder on every launch, adding 3-10 seconds of startup delay. `onedir` starts instantly because files are already on disk. Distribution is as a zip of the folder.

### Bundled static assets (offline-capable)

All three CDN dependencies were downloaded and bundled locally:

| Asset | Size | Previously |
|-------|------|-----------|
| `pico.min.css` | 83 KB | `cdn.jsdelivr.net` |
| `htmx.min.js` | 51 KB | `unpkg.com` |
| `plotly.min.js` | 4.5 MB | `cdn.plot.ly` |

Templates now reference `{{ url_for('static', filename='...') }}` instead of CDN URLs. This means the bundled `.exe` works completely offline -- no internet required.

### `_resource_path()` fix

The `_resource_path` function in `app.py` was updated to construct the correct path under `sys._MEIPASS` for PyInstaller bundles. In dev mode, paths are relative to `quam_state_manager/web/`. In a bundle, data is stored under `sys._MEIPASS/quam_state_manager/web/<relative>`.

### Hidden imports

The spec explicitly lists all `quam_state_manager.*` submodules because PyInstaller's static analysis may miss dynamically imported modules (especially Flask route imports done inside `create_app()`). Unused heavy packages (`tkinter`, `matplotlib`, `numpy.testing`, `pytest`, `scipy`) are excluded to reduce bundle size.

### `console=False`

The exe is built as a Windows GUI application (no console window). All logging goes to the pywebview window's developer tools console.

## Build instructions

```bash
# From the project root
pyinstaller build/quam-manager.spec

# Output: dist/quam-manager/
# Executable: dist/quam-manager/quam-manager.exe
```

### Distributing

```bash
# Zip the output folder
# Windows PowerShell:
Compress-Archive -Path dist/quam-manager -DestinationPath dist/quam-manager-v1.0.zip
```

Send the zip file to users. They extract and double-click `quam-manager.exe`.

## Build output

| Metric | Value |
|--------|-------|
| Total folder size | ~37 MB |
| Expected zip size | ~20 MB |
| Build time | ~24 seconds |
| Cold start time | Instant (onedir) |

## What changed in templates

All three templates that referenced external CDN URLs were updated to use Flask's `url_for('static', ...)`:

- `base.html`: Pico CSS + HTMX
- `_wiring.html`: Plotly
- `_compare.html`: Plotly

This change works seamlessly in both dev mode (Flask serves from `quam_state_manager/web/static/`) and bundled mode (Flask serves from `sys._MEIPASS/quam_state_manager/web/static/`).

## Project status

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

**All 13 TODO items are now complete.** The project has:
- 13 Python modules (core, CLI, web, desktop launcher)
- 469 passing tests (1 skipped)
- Full documentation (14 doc files: `00_overview.md` through `13_pyinstaller.md`)
- A working PyInstaller build producing a distributable `.exe`
