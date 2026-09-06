"""How an OUTSIDE process finds the running State Manager (docs/172).

Two out-of-process helpers ride this: the MCP server a terminal agent talks
to (``quam_state_manager.mcp``) and the Claude Code hook script
(``quam_state_manager.hook``). Both are stdlib-only on purpose -- they run
under whatever Python the customer's ``claude`` invokes, which may not have
SM's web dependencies installed.

Discovery is the instance registry SM already keeps for its multi-window
bookkeeping (docs/80): ``<instance>/instances/<pid>.json`` carries the port
the window is served on. A dead PID or a null port is skipped, the newest
live entry wins, and a GET confirms before anyone trusts it -- a crashed SM
leaves its file behind (measured: one stale entry on this machine).

The CSRF guard (app.py) wants ``Origin`` equal to ``http://<host:port>`` --
exactly the host string we connect to, so ``127.0.0.1`` and ``localhost``
are two different origins.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def instance_dir() -> Path:
    """Where SM keeps its instance data, resolved the way SM resolves it.

    ``SM_INSTANCE`` wins (set it when SM was started with a custom
    ``instance_path``); otherwise the same rule as ``web.app
    .default_instance_path``: repo checkout -> ``<repo>/instance``, installed
    package / frozen -> the per-user data dir.
    """
    env = os.environ.get("SM_INSTANCE")
    if env:
        return Path(env)
    pkg_root = Path(__file__).resolve().parents[1]          # quam_state_manager/
    if (pkg_root.parent / "pyproject.toml").exists() and not getattr(sys, "frozen", False):
        return pkg_root.parent / "instance"
    try:
        from quam_state_manager.web.app import default_instance_path  # noqa: WPS433
        p = default_instance_path()
        if p:
            return Path(p)
    except Exception:  # noqa: BLE001 -- the web stack may be absent here
        pass
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "quam_state_manager" / "instance"
    return Path.home() / ".local" / "share" / "quam_state_manager" / "instance"


def _pid_alive(pid: int) -> bool:
    try:
        from quam_state_manager.core.instances import pid_alive
        return pid_alive(pid)
    except Exception:  # noqa: BLE001
        pass
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def candidate_urls(instance_path: Path | None = None) -> list[str]:
    """Every live SM window, newest first, as ``http://127.0.0.1:<port>``."""
    env = os.environ.get("SM_URL")
    if env:
        return [env.rstrip("/")]
    d = (instance_path or instance_dir()) / "instances"
    rows = []
    try:
        for f in d.glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            port = rec.get("port")
            pid = rec.get("pid")
            if not port or not pid or not _pid_alive(int(pid)):
                continue
            rows.append((str(rec.get("updated_utc") or ""), int(port)))
    except OSError:
        return []
    rows.sort(reverse=True)
    return [f"http://127.0.0.1:{port}" for _, port in rows]


class SMLink:
    """A tiny HTTP client that speaks to one SM window."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"Origin": self.base, "Accept": "application/json", "User-Agent": "sm-agent-link"}
        if extra:
            h.update(extra)
        return h

    def get(self, path: str, params: dict | None = None) -> tuple[int, object]:
        url = self.base + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers=self._headers())
        return self._send(req)

    def post_form(self, path: str, data: dict) -> tuple[int, object]:
        body = urllib.parse.urlencode({k: v for k, v in data.items() if v is not None}).encode()
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers=self._headers({"Content-Type": "application/x-www-form-urlencoded"}))
        return self._send(req)

    def post_json(self, path: str, data: dict) -> tuple[int, object]:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=body, method="POST",
                                     headers=self._headers({"Content-Type": "application/json"}))
        return self._send(req)

    def _send(self, req) -> tuple[int, object]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, _decode(r.read(), r.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as e:
            return e.code, _decode(e.read(), e.headers.get("Content-Type", ""))

    def alive(self) -> bool:
        try:
            code, body = self.get("/api/agent/chip")
            return code == 200 and isinstance(body, dict)
        except (urllib.error.URLError, OSError, ValueError):
            return False


def _decode(raw: bytes, ctype: str):
    text = raw.decode("utf-8", errors="replace")
    if "json" in ctype:
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def connect(instance_path: Path | None = None, timeout: float = 30.0) -> SMLink | None:
    """The first SM window that answers, or None."""
    for url in candidate_urls(instance_path):
        link = SMLink(url, timeout=timeout)
        if link.alive():
            return link
    return None
