"""Read-only discovery of local QUAlibrate servers, outside request threads."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import qualibrate_config as qc

_CACHE_SECONDS = 3.0
_SCAN_SECONDS = 2.0
_lock = threading.Lock()
_cache: dict[str, Any] = {"checked": float("-inf"), "busy": False,
                          "known": False, "servers": []}


def path_key(value: str | Path) -> str:
    """Path spelling comparison without filesystem I/O on a request thread."""
    return os.path.normcase(os.path.abspath(os.path.normpath(str(value))))


def config_argument(command: list[str] | str) -> str | None:
    """Extract a server's explicit config; never infer another shell's env."""
    if isinstance(command, str):
        # Windows command lines keep backslashes literal inside quoted paths.
        if os.name == "nt":
            command = re.findall(r'(?:[^\s"\']+|"[^"]*"|\'[^\']*\')+', command)
        else:
            try:
                command = shlex.split(command)
            except ValueError:
                return None
    args = [str(a).strip('"\'') for a in command]
    for i, arg in enumerate(args):
        base = arg.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base not in ("qualibrate", "qualibrate.exe"):
            continue
        # Accept the console launcher and `python -m qualibrate start`.
        if i and args[i - 1] != "-m":
            continue
        tail = args[i + 1:]
        if not tail or tail[0] != "start":
            continue
        for n, token in enumerate(tail[1:], 1):
            if token.startswith("--config-path="):
                return token.split("=", 1)[1].strip('"\'') or None
            if token == "--config-path" and n + 1 < len(tail):
                value = tail[n + 1].strip('"\'')
                return value if value and not value.startswith("--") else None
    return None


def _platform_processes() -> list[dict]:
    if os.name == "nt":
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
               "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=1.5, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise OSError("process listing unavailable")
        rows = json.loads(result.stdout or "[]")
        return [{"pid": r["ProcessId"], "cmdline": r.get("CommandLine") or []}
                for r in (rows if isinstance(rows, list) else [rows])]
    result = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True,
                            text=True, timeout=1.5)
    if result.returncode:
        raise OSError("process listing unavailable")
    rows = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            rows.append({"pid": int(parts[0]), "cmdline": parts[1]})
    return rows


def _processes():
    try:
        import psutil
    except ImportError:
        yield from _platform_processes()
        return
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            row = dict(proc.info)
            if config_argument(row.get("cmdline") or []):
                try:
                    row["cwd"] = proc.cwd()
                except psutil.Error:
                    pass
            yield row
        except psutil.Error:
            continue


def read_server_config(file: Path) -> dict | None:
    """Use the existing tolerant TOML reader and project merge, read-only."""
    try:
        root = qc._load_toml(file)
        section = root.get("qualibrate") or {}
        project = section.get("project")
        if not isinstance(project, str) or not project:
            return None
        # A project must remain within the config's projects tree.
        if project in (".", "..") or any(c in project for c in "/\\\0"):
            return None
        overlay = qc._load_toml(file.parent / "projects" / project / "config.toml")
        effective = qc._deep_merge(root, overlay)
        effective["qualibrate"]["project"] = project
        raw_state = (effective.get("quam") or {}).get("state_path")
        storage, _ = qc._storage_location(effective, project, file.parent)

        def native(raw):
            if not isinstance(raw, str) or not raw or "\0" in raw:
                return None
            return str(qc._to_native(raw, os.name, qc._wsl_root_of(file)))

        return {"config_path": str(file), "project": project,
                "storage": native(storage), "state_path": native(raw_state)}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def detect_servers(processes=None) -> dict:
    """A bounded worker scan; injection accepts a synthetic process listing."""
    servers = []
    seen = set()
    deadline = time.monotonic() + _SCAN_SECONDS
    try:
        for row in _processes() if processes is None else processes:
            if time.monotonic() >= deadline:
                return {"known": False, "servers": []}
            value = config_argument(row.get("cmdline") or [])
            if not value:
                continue
            file = Path(value)
            if not file.is_absolute():
                if not row.get("cwd"):
                    continue
                file = Path(row["cwd"]) / file
            key = path_key(file)
            if key in seen:
                continue
            seen.add(key)
            info = read_server_config(file)
            if info is not None:
                servers.append({**info, "pid": row.get("pid")})
        return {"known": True, "servers": servers}
    except Exception:
        # AccessDenied, platform listing failures and torn configs are unknown.
        return {"known": False, "servers": []}


def _refresh() -> None:
    result = detect_servers()
    with _lock:
        _cache.update(result, checked=time.monotonic(), busy=False)


def running_servers() -> dict:
    """Return immediately; a single daemon refreshes the short-lived cache."""
    now = time.monotonic()
    with _lock:
        expired = now - _cache["checked"] >= _CACHE_SECONDS
        if expired and not _cache["busy"]:
            _cache["busy"] = True
            threading.Thread(target=_refresh, name="sm-config-discovery", daemon=True).start()
        # Never advertise stale servers while a slow refresh is outstanding.
        if expired:
            return {"known": False, "servers": []}
        return {"known": _cache["known"], "servers": list(_cache["servers"])}
