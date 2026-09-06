"""Agent setup (docs/173 S7): what this PC still needs, done from inside SM.

Everything here is a PREVIEW first and a WRITE only on a click, with a
backup beside every file it touches -- the user's own Claude Code and Codex
configuration is theirs. Reads are honest ("registered" means the file says
so, not that it works); the one live check is ``/api/agent/setup/test``.

Files this module may write (each idempotent, marked, backed up):
  ~/.claude.json                         mcpServers.quam-state-manager  (user scope)
  ~/.claude/settings.json                hooks.{PreToolUse,PostToolUse,PostToolUseFailure,Stop}
  <calibrations>/.claude/settings.local.json   permissions.allow
  ~/.codex/config.toml                   [mcp_servers.quam-state-manager] between markers
  <calibrations>/CLAUDE.local.md | AGENTS.local.md   the lab-context block between markers
  <instance>/agent_setup.json            what was done, when, and where the backups are
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SERVER_NAME = "quam-state-manager"
HOOK_MARK = "quam_state_manager.hook"
TOML_START = "# --- quam-state-manager: start (written by SM; edit between the markers at your own risk)"
TOML_END = "# --- quam-state-manager: end"
CTX_START = "<!-- sm:lab-context:start -->"
CTX_END = "<!-- sm:lab-context:end -->"
ALLOW_RULES = ("mcp__quam-state-manager__*", "Bash(python *)", "Bash(python3 *)")
HOOK_MATCHER = "Bash|Edit|Write|MultiEdit"


# ---------------------------------------------------------------- record

def record_path(instance_path) -> Path:
    return Path(instance_path) / "agent_setup.json"


def load_record(instance_path) -> dict:
    try:
        d = json.loads(record_path(instance_path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_record(instance_path, patch: dict) -> dict:
    cur = load_record(instance_path)
    cur.update(patch)
    p = record_path(instance_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, p)
    return cur


# ---------------------------------------------------------------- backup

def backup(path: Path) -> str | None:
    """A dated copy beside the file, before the first write of a click."""
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = path.with_name(path.name + f".sm-backup-{stamp}")
    shutil.copyfile(path, dst)
    return str(dst)


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ----------------------------------------------------------- the commands

def hook_command(python: str | None = None, instance: str | None = None, backend: str = "claude") -> str:
    """The hook line for the user's settings: the frozen exe's ``--hook`` or
    ``python -m quam_state_manager.hook``; ``--instance`` when SM runs on a
    custom instance dir (else the hook's own default resolution applies)."""
    if getattr(sys, "frozen", False):
        base = f'"{sys.executable}" --hook'
    else:
        base = f'"{python or sys.executable}" -m quam_state_manager.hook'
    cmd = f"{base} --backend {backend}"
    if instance:
        cmd += f' --instance "{instance}"'
    return cmd


def mcp_server_spec(python: str | None = None, repo: str | None = None, instance: str | None = None,
                    chip: str | None = None) -> dict:
    """The stdio server entry both CLIs get. No SM_URL: the bridge finds the
    running window through the instance registry (two windows, two chips --
    the SM_CHIP pin decides)."""
    env = {"PYTHONUTF8": "1"}
    if repo:
        env["PYTHONPATH"] = str(repo)
    if instance:
        env["SM_INSTANCE"] = str(instance)
    if chip:
        env["SM_CHIP"] = chip
    if getattr(sys, "frozen", False):
        return {"type": "stdio", "command": sys.executable, "args": ["--mcp"], "env": env}
    return {"type": "stdio", "command": python or sys.executable, "args": ["-m", "quam_state_manager.mcp"], "env": env}


# ------------------------------------------------------------ Claude Code

def claude_json_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".claude.json"


def claude_settings_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".claude" / "settings.json"


def claude_mcp_registered(home: Path | None = None) -> dict | None:
    return (_read_json(claude_json_path(home)).get("mcpServers") or {}).get(SERVER_NAME)


def preview_claude_mcp(spec: dict, home: Path | None = None) -> dict:
    p = claude_json_path(home)
    cur = _read_json(p)
    servers = dict(cur.get("mcpServers") or {})
    old = servers.get(SERVER_NAME)
    return {"file": str(p), "exists": p.exists(), "before": old, "after": spec, "changed": old != spec}


def write_claude_mcp(spec: dict, home: Path | None = None) -> dict:
    p = claude_json_path(home)
    bak = backup(p)
    cur = _read_json(p)
    servers = dict(cur.get("mcpServers") or {})
    servers[SERVER_NAME] = spec
    cur["mcpServers"] = servers
    _write_json(p, cur)
    return {"file": str(p), "backup": bak}


def remove_claude_mcp(home: Path | None = None) -> dict:
    p = claude_json_path(home)
    cur = _read_json(p)
    servers = dict(cur.get("mcpServers") or {})
    if SERVER_NAME not in servers:
        return {"file": str(p), "backup": None, "removed": False}
    bak = backup(p)
    servers.pop(SERVER_NAME, None)
    cur["mcpServers"] = servers
    _write_json(p, cur)
    return {"file": str(p), "backup": bak, "removed": True}


def hooks_block(cmd: str) -> dict:
    entry = {"type": "command", "command": cmd, "async": True}
    return {"PreToolUse": [{"matcher": HOOK_MATCHER, "hooks": [entry]}],
            "PostToolUse": [{"matcher": HOOK_MATCHER, "hooks": [entry]}],
            "PostToolUseFailure": [{"matcher": HOOK_MATCHER, "hooks": [entry]}],
            "Stop": [{"hooks": [entry]}]}


def _is_sm_hook(group: dict) -> bool:
    for h in group.get("hooks") or []:
        c = str(h.get("command") or "")
        if HOOK_MARK in c or "--hook" in c:
            return True
    return False


def claude_hooks_registered(home: Path | None = None) -> bool:
    hooks = _read_json(claude_settings_path(home)).get("hooks") or {}
    return any(_is_sm_hook(g) for groups in hooks.values() if isinstance(groups, list) for g in groups)


def _merge_hooks(cur_hooks: dict, block: dict) -> dict:
    out: dict = {}
    for ev, groups in (cur_hooks or {}).items():
        if isinstance(groups, list):
            out[ev] = [g for g in groups if not _is_sm_hook(g)]     # ours are replaced, never duplicated
        else:
            out[ev] = groups
    for ev, groups in block.items():
        out.setdefault(ev, [])
        if isinstance(out[ev], list):
            out[ev] = out[ev] + groups
    return out


def preview_claude_hooks(cmd: str, home: Path | None = None) -> dict:
    p = claude_settings_path(home)
    cur = _read_json(p)
    after = _merge_hooks(cur.get("hooks") or {}, hooks_block(cmd))
    return {"file": str(p), "exists": p.exists(), "before": cur.get("hooks") or {}, "after": after,
            "changed": (cur.get("hooks") or {}) != after}


def write_claude_hooks(cmd: str, home: Path | None = None) -> dict:
    p = claude_settings_path(home)
    bak = backup(p)
    cur = _read_json(p)
    cur["hooks"] = _merge_hooks(cur.get("hooks") or {}, hooks_block(cmd))
    _write_json(p, cur)
    return {"file": str(p), "backup": bak}


def remove_claude_hooks(home: Path | None = None) -> dict:
    p = claude_settings_path(home)
    cur = _read_json(p)
    hooks = cur.get("hooks") or {}
    if not any(_is_sm_hook(g) for groups in hooks.values() if isinstance(groups, list) for g in groups):
        return {"file": str(p), "backup": None, "removed": False}
    bak = backup(p)
    cur["hooks"] = {ev: ([g for g in groups if not _is_sm_hook(g)] if isinstance(groups, list) else groups)
                    for ev, groups in hooks.items()}
    cur["hooks"] = {ev: g for ev, g in cur["hooks"].items() if g}
    _write_json(p, cur)
    return {"file": str(p), "backup": bak, "removed": True}


def allow_path(cal_folder: str | Path) -> Path:
    return Path(cal_folder) / ".claude" / "settings.local.json"


def allow_registered(cal_folder: str | Path | None) -> bool:
    if not cal_folder:
        return False
    allow = (_read_json(allow_path(cal_folder)).get("permissions") or {}).get("allow") or []
    return all(r in allow for r in ALLOW_RULES[:2])


def preview_allow(cal_folder: str | Path) -> dict:
    p = allow_path(cal_folder)
    cur = _read_json(p)
    perms = dict(cur.get("permissions") or {})
    allow = list(perms.get("allow") or [])
    after = allow + [r for r in ALLOW_RULES if r not in allow]
    return {"file": str(p), "exists": p.exists(), "before": allow, "after": after, "changed": after != allow}


def write_allow(cal_folder: str | Path) -> dict:
    p = allow_path(cal_folder)
    bak = backup(p)
    cur = _read_json(p)
    perms = dict(cur.get("permissions") or {})
    allow = list(perms.get("allow") or [])
    allow += [r for r in ALLOW_RULES if r not in allow]
    perms["allow"] = allow
    cur["permissions"] = perms
    _write_json(p, cur)
    return {"file": str(p), "backup": bak}


# ------------------------------------------------------------------ Codex

def codex_config_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".codex" / "config.toml"


def _toml_str(v: str) -> str:
    s = str(v)
    if "'" not in s and "\n" not in s:
        return "'" + s + "'"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def codex_block(spec: dict) -> str:
    lines = [TOML_START, f"[mcp_servers.{SERVER_NAME}]", f"command = {_toml_str(spec['command'])}",
             "args = [" + ", ".join(_toml_str(a) for a in spec.get("args") or []) + "]",
             "tool_timeout_sec = 1800"]
    env = spec.get("env") or {}
    if env:
        lines.append(f"[mcp_servers.{SERVER_NAME}.env]")
        for k, v in env.items():
            lines.append(f"{k} = {_toml_str(v)}")
    lines.append(TOML_END)
    return "\n".join(lines) + "\n"


def _strip_block(text: str) -> str:
    pat = re.compile(re.escape(TOML_START) + r".*?" + re.escape(TOML_END) + r"\n?", re.S)
    return pat.sub("", text)


def codex_registered(home: Path | None = None) -> bool:
    try:
        t = codex_config_path(home).read_text(encoding="utf-8")
    except OSError:
        return False
    return f"[mcp_servers.{SERVER_NAME}]" in t


def preview_codex(spec: dict, home: Path | None = None) -> dict:
    p = codex_config_path(home)
    try:
        cur = p.read_text(encoding="utf-8")
    except OSError:
        cur = ""
    base = _strip_block(cur)
    after = base.rstrip("\n") + ("\n\n" if base.strip() else "") + codex_block(spec)
    return {"file": str(p), "exists": p.exists(), "before": cur, "after": after, "changed": after != cur}


def write_codex(spec: dict, home: Path | None = None) -> dict:
    p = codex_config_path(home)
    bak = backup(p)
    prev = preview_codex(spec, home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(prev["after"], encoding="utf-8")
    os.replace(tmp, p)
    return {"file": str(p), "backup": bak}


def remove_codex(home: Path | None = None) -> dict:
    p = codex_config_path(home)
    try:
        cur = p.read_text(encoding="utf-8")
    except OSError:
        return {"file": str(p), "backup": None, "removed": False}
    if TOML_START not in cur:
        return {"file": str(p), "backup": None, "removed": False}
    bak = backup(p)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(_strip_block(cur), encoding="utf-8")
    os.replace(tmp, p)
    return {"file": str(p), "backup": bak, "removed": True}


# ------------------------------------------------------------ lab context

def detect_facts(state: dict, wiring: dict | None = None, node_names: list[str] | None = None) -> dict:
    """What SM can SEE about the device -- facts only, each with its source;
    the questions ask what SM cannot see."""
    from quam_state_manager.core import qdac
    qubits = state.get("qubits") or {}
    pairs = state.get("qubit_pairs") or {}
    modes: dict[str, int] = {}
    for q in qubits.values():
        if not isinstance(q, dict):
            continue
        try:
            m = qdac.bias_mode(q) or "none"
        except Exception:  # noqa: BLE001
            m = "unknown"
        modes[m] = modes.get(m, 0) + 1
    text = json.dumps(state)[:2_000_000].lower()
    facts = {
        "n_qubits": len(qubits), "n_pairs": len(pairs), "qubit_ids": sorted(qubits)[:60],
        "bias_modes": modes,
        "flux_tunable_seen": bool(modes.get("opx") or modes.get("bias_tee")),
        "qdac_seen": bool(modes.get("qdac") or modes.get("bias_tee")),
        "couplers_seen": any(isinstance(p, dict) and "coupler" in p for p in pairs.values()),
        "purcell_mentioned": "purcell" in text,
        "twpa_seen": "twpa" in text,
        "chip_name": ((state.get("extras") or {}).get("chip_name") if isinstance(state.get("extras"), dict) else None),
        "nodes": sorted(node_names or [])[:80],
    }
    return facts


def questions(facts: dict) -> list[dict]:
    """The device questions SM cannot answer from the files (docs/173 §2.3-2),
    each with what SM detected so the person confirms rather than types."""
    q = []
    q.append({"id": "tunable", "kind": "choice", "options": ["flux-tunable", "fixed-frequency", "mixed"],
              "question": "Are the qubits flux-tunable?",
              "detected": ("flux-tunable" if facts.get("flux_tunable_seen") and not facts.get("bias_modes", {}).get("none")
                           else "fixed-frequency" if not facts.get("flux_tunable_seen") else "mixed"),
              "why": "a flux line in the state says a qubit CAN be tuned, not whether the lab parks it"})
    q.append({"id": "coupler", "kind": "choice", "options": ["tunable coupler", "fixed coupling", "none"],
              "question": "Two-qubit coupling?",
              "detected": "tunable coupler" if facts.get("couplers_seen") else ("fixed coupling" if facts.get("n_pairs") else "none")})
    q.append({"id": "purcell", "kind": "choice", "options": ["yes", "no", "unknown"],
              "question": "Is there a Purcell filter on the readout line?",
              "detected": "yes" if facts.get("purcell_mentioned") else "unknown",
              "why": "the state rarely says; the agent's readout advice depends on it"})
    q.append({"id": "squid", "kind": "choice", "options": ["symmetric", "asymmetric", "no SQUID", "unknown"],
              "question": "SQUID loop: symmetric or asymmetric?", "detected": "unknown",
              "why": "decides the flux-vs-frequency shape the agent should expect"})
    q.append({"id": "data_read", "kind": "choice", "options": ["yes", "no"],
              "question": "May the agent READ the data folder (run figures and node.json) directly?",
              "detected": "no", "why": "SM already hands it runs through its tools; direct reads are a lab choice"})
    q.append({"id": "notes", "kind": "text", "question": "Anything else the agent must know about this device or lab?",
              "detected": ""})
    return q


def context_block(facts: dict, answers: dict, *, chip: str | None = None, data_folder: str | None = None) -> str:
    lines = [CTX_START,
             f"# Lab context for the calibration agent (written by QUAM State Manager on {datetime.now().strftime('%Y-%m-%d')})",
             "",
             f"- Chip: {chip or facts.get('chip_name') or '?'} — {facts.get('n_qubits', 0)} qubits, {facts.get('n_pairs', 0)} pairs",
             f"- Qubits: {facts.get('tunable') or answers.get('tunable') or '?'}; coupling: {answers.get('coupler') or '?'}",
             f"- Purcell filter: {answers.get('purcell') or 'unknown'}; SQUID: {answers.get('squid') or 'unknown'}",
             f"- Bias sources seen in the state: {', '.join(f'{k}×{v}' for k, v in (facts.get('bias_modes') or {}).items()) or 'none'}",
             ]
    if facts.get("twpa_seen"):
        lines.append("- A TWPA is present")
    if data_folder:
        lines.append(f"- Data folder: {data_folder} — direct reads: {'allowed' if str(answers.get('data_read')) == 'yes' else 'NOT allowed (use the SM tools)'}")
    if answers.get("notes"):
        lines.append("")
        lines.append("Notes from the lab:")
        for ln in str(answers["notes"]).splitlines():
            lines.append("  " + ln.rstrip())
    lines += ["",
              "Rules: the State Manager (SM) is the only door to the chip's state and to hardware. Read with its tools, "
              "stage writes with state_edit, write with apply_to_live, run nodes only with run_node after a plan the human "
              "started. Never edit state.json/wiring.json directly, never run `python <node>.py` yourself.",
              CTX_END]
    return "\n".join(lines) + "\n"


def context_path(cal_folder: str | Path, target: str = "claude", local: bool = True) -> Path:
    name = ("CLAUDE" if target == "claude" else "AGENTS") + (".local.md" if local else ".md")
    return Path(cal_folder) / name


def preview_context(cal_folder: str | Path, block: str, *, target: str = "claude", local: bool = True) -> dict:
    p = context_path(cal_folder, target, local)
    try:
        cur = p.read_text(encoding="utf-8")
    except OSError:
        cur = ""
    pat = re.compile(re.escape(CTX_START) + r".*?" + re.escape(CTX_END) + r"\n?", re.S)
    if pat.search(cur):
        after = pat.sub(lambda _m: block, cur)
    else:
        after = cur.rstrip("\n") + ("\n\n" if cur.strip() else "") + block
    return {"file": str(p), "exists": p.exists(), "before": cur, "after": after, "changed": after != cur}


def write_context(cal_folder: str | Path, block: str, *, target: str = "claude", local: bool = True) -> dict:
    prev = preview_context(cal_folder, block, target=target, local=local)
    p = Path(prev["file"])
    bak = backup(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(prev["after"], encoding="utf-8")
    os.replace(tmp, p)
    return {"file": str(p), "backup": bak}


def context_written(cal_folder: str | Path | None) -> dict:
    out = {}
    if not cal_folder:
        return out
    for target in ("claude", "codex"):
        for local in (True, False):
            p = context_path(cal_folder, target, local)
            try:
                if CTX_START in p.read_text(encoding="utf-8"):
                    out[f"{target}:{'local' if local else 'shared'}"] = str(p)
            except OSError:
                continue
    return out


# ------------------------------------------------------------------ state

def status(instance_path, *, home: Path | None = None, cal_folder: str | None = None, python: str | None = None,
           repo: str | None = None, detect: dict | None = None) -> dict:
    """Everything the setup page shows: done / not done per item, from the
    files themselves. ``detect`` = the CLIs' presence (agent_backend.detect)."""
    rec = load_record(instance_path)
    return {
        "record": rec,
        "clis": detect or {},
        "python": python or sys.executable, "repo": repo,
        "claude": {"mcp": claude_mcp_registered(home) is not None, "hooks": claude_hooks_registered(home),
                   "allow": allow_registered(cal_folder), "json": str(claude_json_path(home)),
                   "settings": str(claude_settings_path(home))},
        "codex": {"mcp": codex_registered(home), "config": str(codex_config_path(home))},
        "context": context_written(cal_folder),
        "calibrations_folder": cal_folder,
        "frozen": bool(getattr(sys, "frozen", False)),
    }
