"""Confirm / Investigate by agent (agents ver180, decisions D-11..D-23).

The agent REPORTS values and cites evidence; SM checks every citation
itself and SM decides every row. Nothing here writes anything.

- ``parse_answer``  -- the LAST fenced ```json block of the final answer
  text (D-15: Codex has no schema flag, so both backends answer in prose
  with one fenced block).
- ``check_evidence`` -- a file citation must sit under an allowed root, the
  file must exist and the whitespace-normalised quote must be a substring of
  it (R-2); a state citation must resolve to the quoted value. A path outside
  the roots fails and its quote is never echoed (D-17).
- ``compare_confirm`` -- SM's value vs the agent's: agrees / disagrees /
  not reported / unverified (D-13). A ``verdict`` key is ignored.
- ``vet_investigate`` -- answers outside the question schema are dropped and
  counted; ``asks`` may name only ids SM sent (D-16); an answer whose
  evidence fails is unsure; every answer starts "proposed" (D-21).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

MAX_EVIDENCE_PER_VALUE = 3
MAX_EVIDENCE = 20
MAX_QUOTE = 400
MIN_QUOTE_CHARS = 8          # review r1 P1-1: "=" proves nothing
LINE_WINDOW = 1              # the cited line, +-1
_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S | re.I)

CONFIRM_FIELDS = ("chip_name", "n_qubits", "cwd", "config_file", "state_path_in_config",
                  "calibrations_folder_in_config", "storage_in_config", "node_files", "run_folders")
QUOTE_REQUIRED = ("state_path_in_config", "calibrations_folder_in_config", "storage_in_config")
PATH_FIELDS = ("cwd", "config_file", "state_path_in_config", "calibrations_folder_in_config", "storage_in_config")


# ------------------------------------------------------------------ prompts

def confirm_prompt(config_dir: str | None, project: str | None) -> str:
    return (
        "You are checking, READ-ONLY, what you can see from where you run. Do not change anything.\n"
        "1. Call the SM tool sm_status and note the chip name and the number of qubits.\n"
        "2. Note your current working directory.\n"
        f"3. Read the QUAlibrate config file(s) under {config_dir or 'the QUAlibrate config folder'}"
        f"{' (project ' + project + ')' if project else ''}: the root config.toml and, if present, "
        "projects/<project>/config.toml. Report the values of quam.state_path, "
        "qualibrate.calibration_library.folder and qualibrate.storage.location that apply to this project.\n"
        "4. List up to 20 node file names (.py) in your working directory, and up to 5 run folder names "
        "under the storage location if you can see them.\n"
        "Report VALUES only, never a verdict. For each *_in_config value cite the file, the line number and "
        "the exact line text. Answer with exactly ONE fenced ```json block, nothing else after it, shaped:\n"
        '{"seen": {"chip_name": "", "n_qubits": 0, "cwd": "", "config_file": "", "state_path_in_config": "", '
        '"calibrations_folder_in_config": "", "storage_in_config": "", "node_files": [], "run_folders": []}, '
        '"evidence": [{"for": "<a seen key>", "kind": "file", "ref": "<absolute path>", "line": 1, '
        '"quote": "<exact text>"}], "notes": ""}\n'
        "Use null for anything you could not see.")


def investigate_prompt(questions: list[dict], roots: dict) -> str:
    qs = []
    for q in questions:
        opts = q.get("options")
        qs.append({"id": q["id"], "question": q.get("question"),
                   **({"options": opts} if opts else {"kind": "text"})})
    allowed = [r for r in (roots.get("calibrations"), roots.get("config"), roots.get("state")) if r]
    return (
        "You are helping a person answer questions about their quantum device, READ-ONLY. Change nothing.\n"
        "Use the SM tools (state_get, state_search, sm_status) and read files only under: "
        + "; ".join(allowed) + ".\n"
        "For each question below, answer ONLY if you can cite evidence: a file path + line + the exact line "
        "text, or a state key + its value. If you are not sure, set value to null and confidence to unsure -- "
        "never guess. A choice answer must be one of its options. List under \"asks\" the ids of questions the "
        "person should answer themselves.\n"
        "Questions:\n" + json.dumps(qs, ensure_ascii=True) + "\n"
        "Answer with exactly ONE fenced ```json block, shaped:\n"
        '{"answers": [{"id": "", "value": null, "confidence": "sure|unsure", "evidence": '
        '[{"kind": "file", "ref": "<absolute path>", "line": 1, "quote": "<exact text>"} | '
        '{"kind": "state_key", "ref": "<dot.path>", "quote": "<value>"}]}], "asks": [{"id": ""}]}')


# ------------------------------------------------------------------ parsing

def parse_answer(text: str | None) -> tuple[dict | None, str | None]:
    """The LAST fenced json block as a dict, or (None, reason)."""
    if not text or not str(text).strip():
        return None, "no answer"
    blocks = _FENCE.findall(str(text))
    candidates = [blocks[-1]] if blocks else [str(text).strip()]
    for c in candidates:
        try:
            obj = json.loads(c)
        except ValueError:
            return None, "the answer was not in the expected form"
        if isinstance(obj, dict):
            return obj, None
    return None, "the answer was not in the expected form"


# ------------------------------------------------------------------ evidence

def _norm(p) -> str:
    try:
        return os.path.normcase(os.path.realpath(str(p)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.abspath(str(p)))


def native(p: Any) -> str | None:
    if not isinstance(p, str) or not p.strip():
        return None
    from quam_state_manager.core import qualibrate_config as qc
    n = qc.native_path(p.strip())
    return str(n) if n else None


def same_path(a, b) -> bool:
    na, nb = native(a) if isinstance(a, str) else None, native(b) if isinstance(b, str) else None
    return bool(na and nb) and _norm(na) == _norm(nb)


def _under(path: str, roots: list[str]) -> bool:
    p = _norm(path)
    for r in roots:
        if not r:
            continue
        rr = _norm(r)
        if p == rr or p.startswith(rr.rstrip("\\/") + os.sep):
            return True
    return False


def _ws(s: str) -> str:
    return " ".join(str(s).split())


def check_evidence(item: Any, *, roots: list[str], exist_roots: list[str] | None = None,
                   state_lookup: Callable[[str], Any] | None = None) -> dict:
    """One evidence item, checked by SM. Returns ``{ok, kind, ref, line,
    quote|None, why}``; a ref outside every root never echoes its quote."""
    if not isinstance(item, dict):
        return {"ok": False, "why": "not an evidence item", "kind": None, "ref": None, "quote": None}
    kind = str(item.get("kind") or "file")
    ref = item.get("ref")
    quote = item.get("quote")
    line = item.get("line") if isinstance(item.get("line"), int) else None
    out = {"ok": False, "kind": kind, "ref": str(ref) if ref is not None else None, "line": line,
           "quote": None, "for": item.get("for")}
    if kind == "state_key":
        if state_lookup is None or not isinstance(ref, str) or not ref:
            return {**out, "why": "no state to check against"}
        from quam_state_manager.core.differ import compare_equal
        try:
            val = state_lookup(ref)
        except Exception:  # noqa: BLE001
            return {**out, "why": "the state key does not exist"}
        q = quote
        if isinstance(q, str):
            try:
                q = json.loads(q)
            except ValueError:
                pass
        ok = compare_equal(val, q) or (isinstance(quote, str) and _ws(str(val)) == _ws(quote))
        return {**out, "ok": bool(ok), "quote": _clip(quote),
                "why": None if ok else "the state holds a different value"}
    if kind == "run":
        n = native(ref) if isinstance(ref, str) else None
        if not n or not _under(n, exist_roots or []):
            return {**out, "ref": None, "why": "outside the folders SM lets an agent cite"}
        ok = Path(n).is_dir()
        return {**out, "ok": ok, "why": None if ok else "no such run folder"}
    # a file citation
    n = native(ref) if isinstance(ref, str) else None
    if not n:
        return {**out, "why": "no file named"}
    if not _under(n, roots):
        return {**out, "ref": None, "why": "outside the folders SM lets an agent cite"}
    p = Path(n)
    if not p.is_file():
        return {**out, "why": "the file does not exist"}
    if not isinstance(quote, str) or not quote.strip():
        return {**out, "why": "no quote"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {**out, "why": "the file cannot be read"}
    # review r1 P1-1: the quote must be meaningful AND sit at the cited line (+-LINE_WINDOW)
    q = _ws(quote)
    lines = text.splitlines()
    if len(q.replace(" ", "")) < MIN_QUOTE_CHARS and not any(_ws(ln) == q for ln in lines):
        return {**out, "quote": _clip(quote), "why": "the quote is too short to prove anything"}
    if line is None or line < 1 or line > len(lines):
        return {**out, "quote": _clip(quote), "why": "no valid line number"}
    lo, hi = max(0, line - 1 - LINE_WINDOW), min(len(lines), line + LINE_WINDOW)
    ok = q in _ws("\n".join(lines[lo:hi]))
    return {**out, "ok": ok, "quote": _clip(quote),
            "why": None if ok else "the quote is not at the cited line"}


def _clip(q: Any) -> str | None:
    if q is None:
        return None
    s = str(q)
    return s if len(s) <= MAX_QUOTE else s[:MAX_QUOTE] + "…"


def cap_evidence(items: Any, per_key: str = "for") -> tuple[list, int]:
    """At most MAX_EVIDENCE_PER_VALUE per value and MAX_EVIDENCE in all;
    returns (kept, dropped)."""
    if not isinstance(items, list):
        return [], 0
    kept, per, dropped = [], {}, 0
    for it in items:
        k = it.get(per_key) if isinstance(it, dict) else None
        if len(kept) >= MAX_EVIDENCE or per.get(k, 0) >= MAX_EVIDENCE_PER_VALUE:
            dropped += 1
            continue
        per[k] = per.get(k, 0) + 1
        kept.append(it)
    return kept, dropped


# ------------------------------------------------------------------ Confirm

def compare_confirm(answer: dict, sm: dict, *, roots: list[str], exist_roots: list[str] | None = None,
                    state_lookup=None) -> dict:
    """SM's own rows with an agent column (D-13). ``sm`` holds SM's values
    under the same keys as ``seen``; a key SM does not know is shown as the
    agent's report only (``sm`` None)."""
    seen = answer.get("seen") if isinstance(answer.get("seen"), dict) else {}
    ev_raw, dropped = cap_evidence(answer.get("evidence"))
    checked = [check_evidence(e, roots=roots, exist_roots=exist_roots, state_lookup=state_lookup) for e in ev_raw]
    rows = []
    for key in CONFIRM_FIELDS:
        mine = sm.get(key)
        theirs = seen.get(key)
        ev = [c for c in checked if c.get("for") == key]
        if key in QUOTE_REQUIRED:
            # review r1 P1-1: the quote must carry the value the agent reports
            ev = [c if not c["ok"] or _quote_has(c.get("quote"), theirs)
                  else {**c, "ok": False, "why": "the quote does not contain the reported value"} for c in ev]
        if theirs in (None, "", []):
            agent = "not reported"
        elif key in QUOTE_REQUIRED and not any(c["ok"] for c in ev):
            agent = "unverified"
        elif mine in (None, "", []):
            agent = "reported"
        elif _agrees(key, mine, theirs):
            agent = "agrees"
        else:
            agent = "disagrees"
        rows.append({"key": key, "sm": mine, "agent_value": theirs if agent != "not reported" else None,
                     "agent": agent, "evidence": ev})
    return {"rows": rows, "evidence_dropped": dropped,
            "notes": str(answer.get("notes"))[:1000] if answer.get("notes") else ""}


def _quote_has(quote, value) -> bool:
    if not isinstance(quote, str) or not isinstance(value, str) or not value.strip():
        return False
    v = value.strip()
    forms = {v, v.replace("\\", "\\\\"), v.replace("\\", "/"), v.replace("/", "\\")}
    q = quote.replace("\\\\", "\\")
    return any(f in quote or f in q for f in forms)


def _agrees(key: str, mine, theirs) -> bool:
    if key in PATH_FIELDS:
        return isinstance(theirs, str) and same_path(mine, theirs)
    if key in ("node_files", "run_folders"):
        if not isinstance(theirs, list) or not isinstance(mine, (list, tuple, set)):
            return False
        # a name, a relative "date/run" path or an absolute one: compare the last component
        def _name(x):
            return os.path.splitext(str(x).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1])[0]
        m = {_name(x) for x in mine}
        t = {_name(x) for x in theirs}
        return bool(m & t)
    if key == "n_qubits":
        try:
            return int(mine) == int(theirs)
        except (TypeError, ValueError):
            return False
    return str(mine) == str(theirs)


# ------------------------------------------------------------------ Investigate

def vet_investigate(answer: dict, questions: list[dict], *, roots: list[str], exist_roots=None,
                    state_lookup=None) -> dict:
    by_id = {q["id"]: q for q in questions}
    raw = answer.get("answers") if isinstance(answer.get("answers"), list) else []
    out, dropped_ids, dropped_values, ev_dropped = [], 0, 0, 0
    seen_ids = set()
    for a in raw:
        if not isinstance(a, dict) or a.get("id") not in by_id or a.get("id") in seen_ids:
            dropped_ids += 1
            continue
        q = by_id[a["id"]]
        seen_ids.add(a["id"])
        value = a.get("value")
        if value is not None and q.get("kind") == "choice" and value not in (q.get("options") or []):
            dropped_values += 1
            value = None
        if value is not None and not isinstance(value, (str, int, float, bool)):
            dropped_values += 1
            value = None
        ev_items, d = cap_evidence([{**e, "for": a["id"]} if isinstance(e, dict) else e
                                    for e in (a.get("evidence") or [])][:MAX_EVIDENCE_PER_VALUE + 50])
        ev_dropped += d
        checked = [check_evidence(e, roots=roots, exist_roots=exist_roots, state_lookup=state_lookup) for e in ev_items]
        verified = any(c["ok"] for c in checked)
        sure = str(a.get("confidence") or "").lower() == "sure"
        if value is not None and not verified:
            sure = False                                   # D-17: uncited / failed -> unsure
        if not sure:
            value = None                                   # D-18: unsure means no guess
        out.append({"id": a["id"], "value": value, "confidence": "sure" if sure else "unsure",
                    "evidence": checked, "status": "proposed"})
    asks, invented = [], []
    for x in (answer.get("asks") if isinstance(answer.get("asks"), list) else []):
        xid = x.get("id") if isinstance(x, dict) else x
        if isinstance(xid, str) and xid in by_id:
            if xid not in asks:
                asks.append(xid)
        elif xid is not None:
            invented.append(str(x.get("question") or xid if isinstance(x, dict) else xid)[:300])
    # every unsure answer becomes a popup question too (D-18)
    for a in out:
        if a["confidence"] == "unsure" and a["id"] not in asks:
            asks.append(a["id"])
    return {"answers": out, "asks": asks, "also_asked": invented, "dropped_ids": dropped_ids,
            "dropped_values": dropped_values, "evidence_dropped": ev_dropped}
