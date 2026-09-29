"""docs/226 -- first-party JS never builds or compares an app URL that misses the mount prefix.

Under ``--url-prefix /sm`` every app URL the browser sees starts with ``/sm``.
``sm-root.js`` makes the three request SINKS prefix-correct on their own
(``fetch``, ``history.pushState/replaceState``, every htmx request), so a
literal ``fetch('/x')``, ``htmx.ajax('GET', '/x')`` or ``pushState(s, '', '/x')``
is fine and is NOT flagged here. What no wrapper can reach is flagged:

* a URL handed to something that is not a wrapped sink -- ``location.href =
  '/x'``, ``a.href = '/x'``, ``window.open('/x')``, ``setAttribute('src', '/x')``,
  markup built in JS (``'<a href="/x" hx-get="/x">'``), an attribute selector
  that expects an un-prefixed value (``[href="/x"]``), a ``'/static/..'``
  literal. The fix is ``window.SM.url(..)``.
* a location/request path compared with an app route -- ``location.pathname
  === '/x'``, ``u.pathname !== '/x'``, ``path.indexOf('/x') === 0``,
  ``/^\\/x/.test(path)``. Under a prefix the left side is ``/sm/x``. The fix is
  ``window.SM.path(..)`` on the compared value; a value normalised on an
  earlier line (``p = window.SM.path(p)``) or by a helper whose body calls
  ``SM.path`` is recognised.

A reviewed exception goes in ``tests/golden/js_root_allow.txt`` as
``file:regex`` (the regex is matched against the line's text, so it survives
line shifts), with its reason on the line above. An entry that no longer
matches anything fails ``test_allow_list_has_no_stale_entries``.

This file also drives the three node selfchecks that pin sm-root.js and the
hand-edited sites: sm_root_selfcheck.cjs, prefix_hooks_selfcheck.cjs (the REAL
htmx 2.0.4), prefix_sites_selfcheck.cjs.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
_ROOT = _TESTS.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_ALLOW = _TESTS / "golden" / "js_root_allow.txt"
VENDORED = frozenset({"htmx.min.js", "plotly.min.js", "split.min.js"})

_ATTRS = (r"(?:href|src|action|formaction|hx-get|hx-post|hx-put|hx-patch|hx-delete|"
          r"hx-push-url|hx-replace-url|data-url)")

# Sinks no wrapper reaches (the spec's list, widened to every URL attribute).
SINK_RULES: dict[str, str] = {
    "location-nav": r"location\.(?:href\s*=|assign\(|replace\()\s*['\"`]/(?!/)",
    "prop-set": r"\.(?:href|src|action)\s*=\s*['\"`]/(?!/)",
    "window-open": r"window\.open\(\s*['\"`]/",
    "set-attribute": r"setAttribute\(\s*['\"]" + _ATTRS + r"['\"]\s*,\s*['\"`]/(?!/)",
    "attr-selector": r"\[" + _ATTRS + r"[\^$*]?=\\?[\"']/",
    "markup-attr": _ATTRS + r"=\\?[\"']/(?!/)",
    "fetch-object": r"fetch\(\s*new (?:Request|URL)\(",
    "static-literal": r"['\"`]/static/",
}
# Comparisons of a path with an app route. The receiver is checked for
# SM.path normalisation before a hit is reported.
COMPARE_RULES: dict[str, str] = {
    "pathname-compare": r"\.pathname\s*(?:===|!==|==|!=)\s*['\"`]/",
    "pathname-method": r"\.pathname\.(?:indexOf|startsWith|lastIndexOf|match|slice|substr|substring|split)\(",
    "route-compare": r"(?:===|!==|==|!=)\s*['\"]/[A-Za-z_]",
    "route-prefix": r"\.(?:indexOf|startsWith|lastIndexOf)\(\s*['\"]/[A-Za-z_]",
    "route-regex": r"/\^\\/[A-Za-z_]",
}
_RECEIVER = {
    "route-compare": re.compile(r"([A-Za-z_$][\w$]*(?:\(\))?)\s*(?:===|!==|==|!=)\s*['\"]/[A-Za-z_]"),
    "route-prefix": re.compile(r"([A-Za-z_$][\w$]*)(?:\([^()]*\))?\.(?:indexOf|startsWith|lastIndexOf)\(\s*['\"]/"),
    "route-regex": re.compile(r"\.test\(\s*(?:String\(\s*)?([A-Za-z_$][\w$]*)"),
}
_LOOKBACK = 30


@dataclass(frozen=True)
class Hit:
    file: str
    line: int
    rule: str
    text: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line} [{self.rule}] {self.text[:160]}"


def first_party() -> list[Path]:
    return sorted(p for p in _STATIC.glob("*.js") if p.name not in VENDORED)


def code_lines(text: str):
    """(line number, text) of every line that is not a comment line.

    Line-level on purpose: a block comment opened at the start of a line is
    skipped to its end, as are ``//`` and ``*`` lines. Code with a trailing
    comment is scanned whole (an allow-list entry covers a false hit)."""
    in_block = False
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if in_block:
            if "*/" not in s:
                continue
            in_block = False
            s = s.split("*/", 1)[1].strip()
        elif s.startswith("/*"):
            if "*/" not in s[2:]:
                in_block = True
                continue
            s = s[2:].split("*/", 1)[1].strip()
        if not s or s.startswith("//") or s.startswith("*"):
            continue
        yield i, s


def _normalised(lines: list[str], idx: int, receiver: str, text: str) -> bool:
    """Is `receiver` (on 0-based line idx) an SM.path-normalised value?"""
    if "SM.path(" in lines[idx]:
        return True
    if receiver.endswith("()"):                   # a helper: its body strips the prefix
        name = receiver[:-2]
        for j, ln in enumerate(lines):
            if re.search(r"function\s+" + re.escape(name) + r"\s*\(", ln):
                return any("SM.path(" in b for b in lines[j:j + 6])
        return False
    assign = re.compile(r"(?<![\w$.])" + re.escape(receiver) + r"\s*=(?!=)")
    call = None
    for ln in reversed(lines[max(0, idx - _LOOKBACK):idx]):
        if assign.search(ln):
            if "SM.path(" in ln:
                return True
            m = re.search(re.escape(receiver) + r"\s*=\s*([A-Za-z_$][\w$]*)\(", ln)
            call = m.group(1) if m else None
            break
    if call:                                       # value = helper(..) whose body strips the prefix
        for j, ln in enumerate(lines):
            if re.search(r"function\s+" + re.escape(call) + r"\s*\(", ln):
                return any("SM.path(" in b for b in lines[j:j + 15])
    return False


def scan(name: str, text: str) -> list[Hit]:
    raw = text.splitlines()
    hits: list[Hit] = []
    for i, s in code_lines(text):
        for rule, rx in SINK_RULES.items():
            if re.search(rx, s):
                hits.append(Hit(name, i, rule, s))
        for rule, rx in COMPARE_RULES.items():
            if not re.search(rx, s):
                continue
            if "SM.path(" in s:
                continue
            m = _RECEIVER.get(rule)
            rec = m.search(s) if m else None
            if rec and _normalised(raw, i - 1, rec.group(1), s):
                continue
            hits.append(Hit(name, i, rule, s))
    return hits


def load_allow() -> list[tuple[str, re.Pattern[str]]]:
    out = []
    for ln in _ALLOW.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        f, _, rx = ln.partition(":")
        out.append((f.strip(), re.compile(rx)))
    return out


def _allowed(hit: Hit, allow) -> bool:
    return any(f == hit.file and rx.search(hit.text) for f, rx in allow)


# ── the lint itself ─────────────────────────────────────────────────────────

def test_every_first_party_file_is_scanned():
    names = {p.name for p in first_party()}
    assert {"sm-root.js", "app.js", "agent.js", "chip-status.js", "lab-check.js"} <= names
    assert not (names & VENDORED)
    assert len(names) >= 38, sorted(names)


@pytest.mark.parametrize("name", [p.name for p in first_party()])
def test_no_unprefixed_app_url(name):
    allow = load_allow()
    text = (_STATIC / name).read_text(encoding="utf-8")
    bad = [h for h in scan(name, text) if not _allowed(h, allow)]
    assert not bad, (
        f"{len(bad)} app URL(s) in {name} would miss the mount prefix (docs/226) -- "
        "use window.SM.url(..) for a URL, window.SM.path(..) for a compared path, "
        "or add a reviewed file:regex line to tests/golden/js_root_allow.txt:\n"
        + "\n".join(str(h) for h in bad))


def test_allow_list_has_no_stale_entries():
    stale = []
    for f, rx in load_allow():
        p = _STATIC / f
        if not p.is_file():
            stale.append(f"{f}: file is gone")
            continue
        text = p.read_text(encoding="utf-8")
        if not any(rx.search(h.text) for h in scan(f, text)):
            stale.append(f"{f}:{rx.pattern} matches no lint hit any more")
    assert not stale, "\n".join(stale)


# Each rule fires on the shape it names and is quiet on the fixed shape; the
# wrapped sinks are never flagged. (Not vacuous: a rule that matched nothing
# would pass the per-file test for every file.)
_BAD = [
    ("location-nav", "window.location.href = '/dataset/' + uid;"),
    ("location-nav", "location.assign(\"/datasets\");"),
    ("prop-set", "a.href = \"/generate/export-config?\" + qp;"),
    ("prop-set", "img.src = '/dataset/x/fig/y';"),
    ("window-open", "window.open('/calc-window', 'n');"),
    ("set-attribute", "img.setAttribute('src', '/dataset/' + r);"),
    ("set-attribute", "el.setAttribute(\"hx-get\", \"/qubits\");"),
    ("attr-selector", "document.querySelector('.nav a[href=\"/datasets\"]');"),
    ("attr-selector", "closest('button[hx-post=\"/datasets/rescan\"]')"),
    ("markup-attr", "html += '<a href=\"/journal\" hx-get=\"/journal\">log</a>';"),
    ("markup-attr", "'<a class=\"x\" href=\"/\">home</a>'"),
    ("fetch-object", "fetch(new Request('/x'))"),
    ("static-literal", "s.src = '/static/plotly.min.js';"),
    ("pathname-compare", "if (location.pathname !== '/topology') return;"),
    ("pathname-compare", "if (u.pathname === '/zline') {"),
    ("pathname-method", "if (location.pathname.indexOf('/pulses') !== 0) return;"),
    ("route-compare", "if (p.split('?')[0] !== '/bulk') return;"),
    ("route-prefix", "if (d.path.indexOf('/diff') !== 0) return;"),
    ("route-regex", "return /^\\/topology(\\?|$)/.test(String(rc.path || ''));"),
]
_GOOD = [
    "fetch('/field/peek', { method: 'POST' });",
    "htmx.ajax('GET', '/qubits', { target: '#t' });",
    "history.pushState({ htmx: true }, '', '/pairs');",
    "history.replaceState(history.state, '', '/topology?view=' + v);",
    "var u = window.SM.url('/dataset/' + uid); window.location.href = u;",
    "linkEl.href = window.SM ? window.SM.url(href) : href;",
    "if ((window.SM ? window.SM.path(u.pathname) : u.pathname) === '/zline') {",
    "if (!d.path || window.SM.path(d.path).indexOf('/diff') !== 0) return;",
    "var cdn = '//cdn.example/x.js'; a.href = 'https://example.org/';",
    "// location.href = '/in-a-comment';",
    "'<a href=\"' + u + '\" hx-get=\"' + u + '\">'",
    "a.href = '#';",
]


@pytest.mark.parametrize("rule,src", _BAD, ids=[f"{r}:{i}" for i, (r, _) in enumerate(_BAD)])
def test_rule_fires_on_its_shape(rule, src):
    assert rule in {h.rule for h in scan("x.js", src)}, (rule, src)


@pytest.mark.parametrize("src", _GOOD)
def test_fixed_shapes_and_wrapped_sinks_are_quiet(src):
    assert scan("x.js", src) == [], src


def test_a_value_normalised_above_is_recognised():
    src = "\n".join([
        "var rp = String(rc.path || '');",
        "if (window.SM) rp = window.SM.path(rp);",
        "return /^\\/topology(\\?|$)/.test(rp);",
        "function herePath() {",
        "    var p = window.location.pathname;",
        "    return window.SM ? window.SM.path(p) : p;",
        "}",
        "if (herePath() === '/compare-hub') {",
        "var path = rawPath;",
        "if (path.indexOf('/diff/') === 0) path = '/diff';",
    ])
    hits = scan("x.js", src)
    assert [h.line for h in hits] == [10], [str(h) for h in hits]


# ── the node selfchecks this lint's fixes are pinned by ─────────────────────

@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize("name", ["sm_root_selfcheck.cjs", "prefix_hooks_selfcheck.cjs",
                                  "prefix_sites_selfcheck.cjs"])
def test_prefix_selfcheck(name):
    proc = subprocess.run(["node", str(_TESTS / name)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(_ROOT), timeout=300)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, (
        f"{name} failed\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}")
    assert "ok - " in proc.stdout and " 0 failed" in proc.stdout, proc.stdout[-2000:]
