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
_location_cache = {"checked": float("-inf"), "busy": False, "key": None, "value": None}


def _read_sm_location(project):
    """Resolve local config hints in a background thread, including share I/O."""
    file = qc.root_config_path()
    storage = qc.project_storage(project).get("native") if project else None
    return {"config_path": str(file), "storage": storage}


def sm_location(project=None):
    """Nonblocking config snapshot; hide stale hints after a location change."""
    key = (qc.env_raw(), str(qc._dir_override), str(qc._file_override),
           qc._override_beats_env, project)
    now = time.monotonic()
    with _lock:
        cache = _location_cache
        expired = key != cache["key"] or now - cache["checked"] >= _CACHE_SECONDS
        if expired and not cache["busy"]:
            cache["busy"] = True

            def refresh():
                value = None
                try:
                    value = _read_sm_location(project)
                except (OSError, ValueError, TypeError, AttributeError):
                    value = None
                finally:
                    with _lock:
                        cache.update(key=key, value=value,
                                     checked=time.monotonic(), busy=False)

            threading.Thread(target=refresh, name="sm-config-location", daemon=True).start()
        return None if expired else dict(cache["value"]) if cache["value"] else None


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
    def base(value):
        return value.replace("\\", "/").rsplit("/", 1)[-1].lower()

    module_index = None
    if args and re.fullmatch(r"(?:python(?:w|\d+(?:\.\d+)*)?|pypy\d*)(?:\.exe)?", base(args[0])):
        index = 1
        while index < len(args):
            token = args[index]
            if token == "-m":
                module_index = index + 1
                break
            if token in {"-B", "-u", "-E", "-I", "-s", "-S", "-O", "-OO", "-q"}:
                index += 1
            elif token in {"-X", "-W"} and index + 1 < len(args):
                index += 2
            elif token.startswith(("-X", "-W")):
                index += 1
            else:
                break
    for i, arg in enumerate(args):
        if base(arg) not in ("qualibrate", "qualibrate.exe"):
            continue
        # Accept the console launcher and `python -m qualibrate start`.
        if i and i != module_index:
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


def config_mismatches(discovery: dict, sm_config: str | Path,
                      open_state: str | None = None, dismissed=()) -> list[dict]:
    """Only compare explicit config files relevant to the open live chip."""
    if not discovery.get("known"):
        return []
    ignored = {path_key(p) for p in dismissed}
    current = path_key(sm_config)
    return [dict(server, sm_config=str(sm_config))
            for server in discovery.get("servers", [])
            if path_key(server["config_path"]) != current
            and path_key(server["config_path"]) not in ignored
            and (not open_state or (server.get("state_path")
                 and path_key(server["state_path"]) == path_key(open_state)))]


def dataset_hints(discovery: dict, open_state: str | None,
                  data_folders: list[str], added=()) -> list[dict]:
    """Offer a distinct running server's storage only for this live chip."""
    if not discovery.get("known") or not open_state or not data_folders:
        return []
    have = {path_key(p) for p in (*data_folders, *added)}
    hints = []
    for server in discovery.get("servers", []):
        storage, state = server.get("storage"), server.get("state_path")
        if (storage and state and path_key(state) == path_key(open_state)
                and path_key(storage) not in have):
            hints.append(server)
            have.add(path_key(storage))
    return hints
