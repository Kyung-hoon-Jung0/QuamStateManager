"""The calibration journal (docs/172): plain markdown files, SM as the reader.

The customer's lab keeps its notes in Obsidian/Notion and runs Claude Code
overnight. What was missing was a log that (a) the agent can write into as
it works, (b) records WHAT ran with WHICH parameters even when the agent
forgot to say why, and (c) links into the evidence -- a run number opens the
figure, a state path opens that value's history. So:

  * storage is one ``.md`` per chip per day under a folder the user picks
    (their vault is fine) -- Obsidian keeps editing it, SM only appends and
    renders. Nothing here is a database.
  * the renderer is a WHITELIST over a markdown subset. Everything else is
    escaped. The agent writes this text; it is never trusted as HTML.
  * a ``#1234`` token becomes a link to that run; a backticked dot path
    (``qubits.q1.xy.operations.x180.amplitude``) becomes a clickable path.
"""

from __future__ import annotations

import html
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

_LOCK = threading.Lock()
KINDS = ("agent", "hook", "human", "sm")


# ----------------------------------------------------------------- storage

def config_path(instance_path) -> Path:
    return Path(instance_path) / "journal.json"


def settings(instance_path) -> dict:
    """``{"root": str|"", "claude_says": bool}`` -- the folder, and whether the
    turn's final message goes into the vault (off by default: model prose in
    a person's notes is the thing the terminal user said would make them
    disable the hook)."""
    try:
        cfg = json.loads(config_path(instance_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    return {"root": str(cfg.get("root") or ""), "claude_says": bool(cfg.get("claude_says", False))}


def root(instance_path) -> Path:
    """The journal folder: the configured one, else ``<instance>/journal``."""
    r = settings(instance_path)["root"].strip()
    return Path(r) if r else Path(instance_path) / "journal"


def _write_settings(instance_path, cfg: dict) -> None:
    p = config_path(instance_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg), encoding="utf-8")


def set_root(instance_path, folder: str | None) -> Path:
    """Point the journal at ``folder`` (blank = back to the default). The
    folder must exist or be creatable; nothing is moved."""
    cfg = settings(instance_path)
    if folder and folder.strip():
        target = Path(folder.strip()).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        cfg["root"] = str(target)
    else:
        cfg["root"] = ""
    _write_settings(instance_path, cfg)
    return root(instance_path)


def set_claude_says(instance_path, on: bool) -> bool:
    cfg = settings(instance_path)
    cfg["claude_says"] = bool(on)
    _write_settings(instance_path, cfg)
    return bool(on)


def _safe_key(name: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip()).strip("._")
    return key or "chip"


def day_file(instance_path, chip: str, day: str | None = None) -> Path:
    day = day or datetime.now().strftime("%Y-%m-%d")
    return root(instance_path) / _safe_key(chip) / f"{day}.md"


def list_days(instance_path, chip: str) -> list[str]:
    d = root(instance_path) / _safe_key(chip)
    try:
        return sorted((f.stem for f in d.glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem)),
                      reverse=True)
    except OSError:
        return []


def list_chips(instance_path) -> list[str]:
    r = root(instance_path)
    try:
        return sorted(p.name for p in r.iterdir() if p.is_dir() and any(p.glob("*.md")))
    except OSError:
        return []


def read(instance_path, chip: str, day: str | None = None) -> str:
    try:
        return day_file(instance_path, chip, day).read_text(encoding="utf-8")
    except OSError:
        return ""


_RUN_TOKEN_IN_TEXT = re.compile(r"\s*·\s*run #(\d+)")
_FORGE = re.compile(r"^\s*(?:[-*+>#|]|```|~~~|\d+[.)])")


def _defuse(line: str) -> str:
    """A continuation line that would start a bullet / heading / fence / table
    row (review R3-10: an agent's text forged a `human` entry, an <h1>, and a
    fence that swallowed every later entry) is marked as prose."""
    return ("· " + line.lstrip()) if line and _FORGE.match(line) else line


def append(instance_path, chip: str, text: str, *, kind: str = "agent",
           reason: str | None = None, run_id: int | None = None,
           paths: list[str] | None = None, when: datetime | None = None) -> dict:
    """Append one entry as a markdown bullet. Returns what was written."""
    if kind not in KINDS:
        kind = "agent"
    when = when or datetime.now()
    text = _RUN_TOKEN_IN_TEXT.sub(r" run #\1", (text or "").strip())      # review R3-10: prose never carries the token
    lines = [l.rstrip() for l in text.splitlines()] or [""]
    lines = [lines[0]] + [_defuse(l) for l in lines[1:]]
    head = f"- **{when.strftime('%H:%M:%S')}** `{kind}` {lines[0]}"
    if run_id is not None:
        head += f" · run #{int(run_id)}"
    if paths:
        head += " · " + ", ".join(f"`{p}`" for p in paths if p)
    body = [head]
    body += [("  " + l) if l else "" for l in lines[1:]]
    if reason and reason.strip():
        body.append("  - because: " + reason.strip().replace("\n", " "))
    entry = "\n".join(body) + "\n"
    f = day_file(instance_path, chip, when.strftime("%Y-%m-%d"))
    with _LOCK:
        f.parent.mkdir(parents=True, exist_ok=True)
        fresh = not f.exists() or f.stat().st_size == 0
        with open(f, "a", encoding="utf-8", newline="\n") as fh:
            if fresh:
                fh.write(f"# {chip} — {when.strftime('%Y-%m-%d')}\n\n")
            fh.write(entry)
    return {"file": str(f), "kind": kind, "ts": when.isoformat(timespec="seconds"),
            "text": text, "reason": reason, "run_id": run_id, "paths": paths or []}


def adopt_unassigned(instance_path, chip: str, day: str | None = None) -> int:
    """Move the day's 'unassigned' lines under ``chip`` (a person decided).
    Returns how many entry bullets moved. The unassigned file is removed."""
    day = day or datetime.now().strftime("%Y-%m-%d")
    src = day_file(instance_path, "unassigned", day)
    try:
        text = src.read_text(encoding="utf-8")
    except OSError:
        return 0
    bullets = [l for l in text.splitlines() if l.startswith("- **")]
    if not bullets:
        return 0
    body = "\n".join(l for l in text.splitlines() if not l.startswith("# ")).strip("\n") + "\n"
    dst = day_file(instance_path, chip, day)
    with _LOCK:
        dst.parent.mkdir(parents=True, exist_ok=True)
        fresh = not dst.exists() or dst.stat().st_size == 0
        with open(dst, "a", encoding="utf-8", newline="\n") as fh:
            if fresh:
                fh.write(f"# {chip} — {day}\n\n")
            fh.write(f"\n<!-- adopted from unassigned, {datetime.now().isoformat(timespec='seconds')} -->\n{body}")
        try:
            src.unlink()
        except OSError:
            pass
    return len(bullets)


# ---------------------------------------------------------------- renderer

_RUN = re.compile(r"(?<![\w/])#(\d{1,7})\b")
_PATH = re.compile(r"^[A-Za-z_][\w-]*(?:\.[\w-]+)+$")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?!\w)")
_LINK = re.compile(r"\[([^\]\n]+)\]\(((?:https?://|/(?!/))[^)\s]+)\)")
_WIKI = re.compile(r"\[\[([^\]\n|]+)(?:\|([^\]\n]+))?\]\]")
_TIME = re.compile(r"^\*\*(\d{2}:\d{2}:\d{2})\*\*")


def _inline(s: str) -> str:
    """Escape, then re-introduce the few inline forms we allow."""
    out = []
    pos = 0
    for m in _INLINE_CODE.finditer(s):
        out.append(_inline_text(s[pos:m.start()]))
        code = m.group(1)
        if _PATH.match(code.strip()):
            p = html.escape(code.strip())
            out.append(f'<code class="jr-path" data-path="{p}" title="open this value\'s history">{p}</code>')
        else:
            out.append(f"<code>{html.escape(code)}</code>")
        pos = m.end()
    out.append(_inline_text(s[pos:]))
    return "".join(out)


def _inline_text(s: str) -> str:
    s = html.escape(s, quote=True)
    s = _LINK.sub(lambda m: f'<a href="{m.group(2)}" rel="noopener">{m.group(1)}</a>', s)
    s = _WIKI.sub(lambda m: f'<span class="jr-wiki">{m.group(2) or m.group(1)}</span>', s)
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    s = _ITALIC.sub(r"<em>\1</em>", s)
    # review R9: a #N inside a link's href (e.g. [z](/a?x=#123)) was turned into a
    # NESTED <a>. Run the #run linker only on the segments OUTSIDE the anchors _LINK built.
    return "".join(part if k % 2 else _RUN.sub(_run_link, part)
                   for k, part in enumerate(_ANCHOR.split(s)))


_ANCHOR = re.compile(r"(<a\b[^>]*?>.*?</a>)", re.DOTALL)


def _run_link(m: "re.Match") -> str:
    return (f'<a class="jr-run" href="/dataset/by-run/{m.group(1)}" '
            f'hx-get="/dataset/by-run/{m.group(1)}" hx-target="#table-pane" '
            f'hx-push-url="true">#{m.group(1)}</a>')


def render(md: str) -> str:
    """Markdown subset -> HTML. Headings, bullets (nested by indent), ordered
    lists, fenced code, blockquotes, pipe tables, rules, paragraphs; inline
    code / bold / italic / links / wikilinks; ``#run`` and path tokens."""
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    list_stack: list[tuple[int, str]] = []        # (indent, 'ul'|'ol')
    para: list[str] = []

    def close_lists(to_indent: int = -1):
        while list_stack and list_stack[-1][0] > to_indent:
            out.append(f"</{list_stack.pop()[1]}>")

    def flush_para():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_para(); close_lists()
            lang = html.escape(stripped[3:].strip())
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            out.append(f'<pre><code class="lang-{lang}">' + html.escape("\n".join(buf)) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            flush_para(); close_lists()
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
            i += 1; continue
        if re.match(r"^(-{3,}|\*{3,})$", stripped):
            flush_para(); close_lists(); out.append("<hr>"); i += 1; continue
        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush_para(); close_lists()
            head = [c.strip() for c in stripped.strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")]); i += 1
            out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>")
            for r in rows:
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
            out.append("</tbody></table>")
            continue
        m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            flush_para()
            indent = len(m.group(1).expandtabs(4))
            kind = "ol" if m.group(2)[0].isdigit() else "ul"
            if not list_stack or indent > list_stack[-1][0]:
                out.append(f"<{kind}>"); list_stack.append((indent, kind))
            else:
                close_lists(indent)
                if not list_stack or list_stack[-1][0] != indent:
                    out.append(f"<{kind}>"); list_stack.append((indent, kind))
            body = m.group(3)
            tm = _TIME.match(body)
            cls = ""
            if tm:
                cls = f' class="jr-entry" data-time="{tm.group(1)}"'
            out.append(f"<li{cls}>{_inline(body)}</li>")
            i += 1; continue
        if stripped.startswith(">"):
            flush_para(); close_lists()
            out.append("<blockquote>" + _inline(stripped.lstrip("> ")) + "</blockquote>")
            i += 1; continue
        if not stripped:
            flush_para(); close_lists(); i += 1; continue
        if list_stack and line.startswith(" "):
            # continuation of the previous bullet
            if out and out[-1].endswith("</li>"):
                out[-1] = out[-1][:-5] + "<br>" + _inline(stripped) + "</li>"
            i += 1; continue
        close_lists()
        para.append(stripped)
        i += 1
    flush_para(); close_lists()
    return "\n".join(out)
