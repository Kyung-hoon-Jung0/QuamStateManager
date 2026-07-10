# 12 -- Desktop Entry Point (`quam_state_manager/main.py`)

## What was done

Created the **pywebview desktop launcher** that wraps the Flask web dashboard in a native OS window. This eliminates the need for a browser -- users double-click an executable and get a native desktop application experience.

### Files created

| File | Purpose |
|------|---------|
| `quam_state_manager/main.py` | Desktop entry point: starts Flask, opens pywebview window |
| `quam_state_manager/__main__.py` | Enables `python -m quam_state_manager` invocation |
| `tests/test_main.py` | 14 tests covering all launcher components |

### How it works

```
main()
  │
  ├── create_app()            ← Flask factory (from web/app.py)
  ├── find_free_port()        ← OS assigns a random free port
  ├── _start_server(app, port)← Flask runs in a daemon thread
  ├── _wait_for_server(port)  ← Polls until Flask responds (up to 10s)
  │
  └── webview.create_window() ← Native OS window pointing at localhost
      webview.start()         ← Blocks until window is closed
```

### Key design decisions

1. **Random port**: `find_free_port()` binds to port 0, letting the OS pick an available port. This avoids conflicts if the user has other services running.

2. **Server health check**: `_wait_for_server()` polls the Flask server with a 100ms interval before opening the window. If the server doesn't respond within 10 seconds, the process exits with code 1.

3. **Daemon thread**: Flask runs in a daemon thread so it automatically terminates when the main thread (pywebview event loop) exits. No cleanup or signal handling needed.

4. **`use_reloader=False`**: Werkzeug's reloader spawns a child process which breaks both pywebview (loses the window) and PyInstaller (can't find the entry point). This flag is critical.

5. **Module-level `import webview`**: pywebview is imported at module level (not inside `main()`). This is intentional -- `main.py` is the desktop launcher and should fail fast with a clear `ImportError` if pywebview isn't installed.

6. **`__main__.py`**: Allows `python -m quam_state_manager` to launch the desktop app, consistent with Python packaging conventions.

### Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `WINDOW_TITLE` | `"QUAM State Manager"` | Title bar text |
| `DEFAULT_WIDTH` | 1400 | Initial window width (px) |
| `DEFAULT_HEIGHT` | 900 | Initial window height (px) |
| `MIN_WIDTH` | 1000 | Minimum resizable width |
| `MIN_HEIGHT` | 600 | Minimum resizable height |
| `SERVER_STARTUP_TIMEOUT` | 10.0 | Max seconds to wait for Flask |

### Running the desktop app

```bash
# From development environment (requires pywebview installed)
python -m quam_state_manager

# Or directly
python quam_state_manager/main.py
```

The app opens a native window. Close the window to exit -- the Flask server shuts down automatically (daemon thread).

## Test coverage

14 tests in `tests/test_main.py`:

| Test class | Count | What it covers |
|-----------|-------|----------------|
| `TestFindFreePort` | 4 | Returns int, valid range, port actually free, consecutive calls differ |
| `TestWaitForServer` | 2 | Returns True when server responds, returns False on timeout |
| `TestStartServer` | 1 | Creates daemon thread, passes correct kwargs to `app.run()` |
| `TestMainFunction` | 2 | Happy path (all mocked), exits on server timeout |
| `TestConstants` | 3 | Window title, dimensions, timeout value |
| `TestModuleImport` | 2 | `quam_state_manager.main` importable, `__main__` spec exists |

The `TestMainFunction` tests mock `webview`, `create_app`, `find_free_port`, `_start_server`, and `_wait_for_server` to avoid opening real windows during CI.

## Dependencies

- **pywebview** (6.1): Native OS webview wrapper. On Windows uses Edge WebView2 (Chromium). On macOS uses WebKit. On Linux uses GTK WebKit.
- **Flask** (already in project): HTTP backend.

## What's next

See [`00_overview.md`](00_overview.md) for the full module inventory, architecture, and remaining work.

The next step is **TODO #13: PyInstaller packaging** -- creating a `build/quam-manager.spec` file for `onedir` mode distribution as a single folder (zipped) containing the `.exe` and all dependencies.
