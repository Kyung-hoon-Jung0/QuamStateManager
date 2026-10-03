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

import hashlib
import html
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

_LOCK = threading.Lock()
# docs/173 S8: a journal line names its AUTHOR. by_claude/by_codex = the agent
# that ran it (from the backend); hook = a terminal agent SM could not name;
# unknown = a run nobody claimed; human = a person; sm = SM's own bookkeeping;
# unverified = a caller SM cannot vouch for -- no agent header and no person's
# window behind the request (docs/252, A-09).
KINDS = ("agent", "hook", "human", "sm", "by_claude", "by_codex", "unknown", "unverified")
_AUTHOR_KINDS = ("by_claude", "by_codex", "human", "unknown")


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
    # docs/173 S8: agent_says defaults ON now (the user's decision), because a
    # LABELLED line -- `by_claude` in front of the model's own words -- reads as
    # the model, not as the person. The old key `claude_says` still overrides.
    says = cfg.get("agent_says", cfg.get("claude_says", True))
    return {"root": str(cfg.get("root") or ""), "agent_says": bool(says), "claude_says": bool(says)}


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


def _same_folder(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


def _blocks(text: str) -> list[str]:
    """A day file as its entries: a top-level line starts a block, indented and
    blank lines continue it; the ``# chip -- day`` title is not an entry."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("# ") and not out:
            continue
        if out and (not line.strip() or line[:1] in (" ", "\t")):
            out[-1] += line
        elif line.strip():
            out.append(line)
    return out


def carry_over(src: Path, dst: Path) -> dict[str, list[str]]:
    """Bring every chip's day files from journal folder ``src`` into ``dst``
    (D-08 / C-15: a moved journal used to leave them behind, and the
    Calibration log read as an empty history). Copy, never move: ``src`` is a
    person's folder too. A day ``dst`` already has gets only the entries it is
    missing, appended -- never rewritten, because a vault file may hold a
    person's own words -- so moving back and forth duplicates nothing.
    Returns ``{chip: [days carried]}``."""
    import shutil
    carried: dict[str, list[str]] = {}
    try:
        chip_dirs = sorted(d for d in Path(src).iterdir() if d.is_dir())
    except OSError:
        return carried
    for d in chip_dirs:
        if _same_folder(d, dst):
            continue                                  # the new folder sits inside the old one
        days = sorted(f for f in d.glob("*.md") if _DAY_RE.fullmatch(f.stem))
        for f in days:
            target = Path(dst) / d.name / f.name
            try:
                with _LOCK:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copy2(f, target)
                    else:
                        have = target.read_text(encoding="utf-8")
                        missing = [b for b in _blocks(f.read_text(encoding="utf-8"))
                                   if b.strip() and b.strip() not in have]
                        if not missing:
                            continue
                        with open(target, "a", encoding="utf-8", newline="\n") as fh:
                            if have and not have.endswith("\n"):
                                fh.write("\n")
                            fh.write("".join(b if b.endswith("\n") else b + "\n" for b in missing))
            except OSError:
                continue
            carried.setdefault(d.name, []).append(f.stem)
    return carried


def move_root(instance_path, folder: str | None, *, who: str, chip: str | None = None) -> dict:
    """Point the journal at ``folder`` (blank = the default) AND keep its
    history: the day files come along (:func:`carry_over`), and the move is
    journaled in BOTH folders -- the old one says where the log went, the new
    one where it came from -- for every chip carried and the open one. The
    one function both doors call (``/api/agent/journal/root`` and Setup)."""
    old = root(instance_path)
    new = set_root(instance_path, folder)            # raises before anything moves
    if _same_folder(old, new):
        return {"root": new, "moved": False, "carried": {}}
    carried = carry_over(old, new)
    chips = {c: c for c in carried}                  # folder name -> the name a line is filed under
    if chip:
        chips[_safe_key(chip)] = chip
    for key, c in sorted(chips.items()):
        n = len(carried.get(key, []))
        try:
            append(instance_path, c, f"journal folder moved to {new} by {who} -- "
                   f"{n} day file(s) of this chip carried over", kind="sm", root_dir=old)
        except (OSError, ValueError):
            pass                                      # the old folder may be gone; the new one still says
        append(instance_path, c, f"journal folder moved here from {old} by {who} -- "
               f"{n} day file(s) of this chip carried over", kind="sm")
    return {"root": new, "moved": True, "from": old, "carried": carried,
            "days": sum(len(v) for v in carried.values())}


def set_claude_says(instance_path, on: bool) -> bool:
    return set_agent_says(instance_path, on)


def set_agent_says(instance_path, on: bool) -> bool:
    """docs/173 S8: whether the agent's own final message becomes a labelled
    journal line. Writes both keys so an older reader still sees it."""
    try:
        cfg = json.loads(config_path(instance_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cfg["agent_says"] = cfg["claude_says"] = bool(on)
    _write_settings(instance_path, cfg)
    return bool(on)


#: A key becomes a directory NAME, so it has a length as well as a character
#: set. docs/191 H06: a 5,000-character chip name sanitised to a 5,000-character
#: folder and `POST /api/agent/journal` answered 500 (the OS refused the path).
#: Truncation alone would fold two long names onto one folder, so a truncated
#: key carries a short digest of the whole name and stays distinct.
_KEY_MAX = 80


def _safe_key(name: str) -> str:
    raw = (name or "").strip()
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    if len(key) > _KEY_MAX:
        tag = hashlib.sha1(raw.encode("utf-8", "surrogatepass")).hexdigest()[:10]
        key = key[:_KEY_MAX - 11].rstrip("._") + "-" + tag
    return key or "chip"


#: What a journal day is -- the same shape `list_days` uses to decide which
#: files ARE days. docs/191 H05: the chip half of the path went through
#: `_safe_key` and the day half went through nothing, so `?date=../../secret`
#: read any .md file on the machine and `POST /journal/adopt {"day":
#: "../../victim"}` read it, appended it to itself and then UNLINKED it --
#: measured: `{"moved": 1, "ok": true}` and the file gone.
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def day_file(instance_path, chip: str, day: str | None = None) -> Path:
    day = day or datetime.now().strftime("%Y-%m-%d")
    if not _DAY_RE.fullmatch(str(day)):
        raise ValueError(f"not a journal day: {day!r} (a day is YYYY-MM-DD)")
    return root(instance_path) / _safe_key(chip) / f"{day}.md"


def is_day(day) -> bool:
    """Whether a caller's day is one -- for a door that wants to answer 400
    rather than raise."""
    return bool(_DAY_RE.fullmatch(str(day or "")))


def list_days(instance_path, chip: str) -> list[str]:
    d = root(instance_path) / _safe_key(chip)
    try:
        return sorted((f.stem for f in d.glob("*.md") if _DAY_RE.fullmatch(f.stem)),
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
    """The day's text, or "" -- a READ of a day that is not one has always built
    an empty page rather than raising (`story.build_day` relies on it), and the
    H05 gate must not change that. The honest refusal belongs at the DOOR, which
    answers 400; here a nonsense day is simply a day with nothing in it."""
    try:
        return day_file(instance_path, chip, day).read_text(encoding="utf-8")
    except (OSError, ValueError):
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
           paths: list[str] | None = None, when: datetime | None = None,
           root_dir: Path | None = None) -> dict:
    """Append one entry as a markdown bullet. Returns what was written.
    ``root_dir`` writes into a journal folder other than the configured one
    (the old folder of a move, :func:`move_root`)."""
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
    if root_dir is not None:
        f = Path(root_dir) / f.parent.name / f.name
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
_ENTRY_KIND = re.compile(r"^\*\*\d{2}:\d{2}:\d{2}\*\*\s+`([A-Za-z_][\w:.-]*)`")   # docs/173 S8: the author token


def _inline(s: str) -> str:
    """Escape, then re-introduce the few inline forms we allow.

    Code spans are set aside as placeholders and the text forms run over the WHOLE line, so bold
    or a link may wrap inline code. Splitting the line at each code span first left `**` showing
    around it: an agent's "**`sm`**" rendered as "** sm **" (integration finding I-02)."""
    codes: list[str] = []

    def _stash(m: "re.Match") -> str:
        code = m.group(1)
        if _PATH.match(code.strip()):
            p = html.escape(code.strip())
            codes.append(f'<code class="jr-path" data-path="{p}" title="open this value\'s history">{p}</code>')
        else:
            codes.append(f"<code>{html.escape(code)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = _INLINE_CODE.sub(_stash, s.replace("\x00", ""))
    return _CODE_SLOT.sub(lambda m: codes[int(m.group(1))], _inline_text(text))


_CODE_SLOT = re.compile("\x00(\\d+)\x00")


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
                km = _ENTRY_KIND.match(body)
                author = html.escape(km.group(1)) if km else ""
                cls = f' class="jr-entry" data-time="{tm.group(1)}"'
                if author:
                    cls += f' data-author="{author}"'      # docs/173 S8: by_claude / by_codex / human / unknown / sm
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
