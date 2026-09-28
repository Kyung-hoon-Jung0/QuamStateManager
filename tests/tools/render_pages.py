"""Render every SM page through the Flask test client, normalised, into a directory.

One renderer, two consumers (docs/226, spec §5.3 / §5.4):

* the ROOT byte-identity golden -- ``tests/test_root_golden.py`` renders the
  base commit and HEAD with this same script (each in its own subprocess, so
  each imports ITS OWN ``quam_state_manager``) and requires the two output
  trees to be identical once the two DECLARED deltas are removed;
* the rendered-output LEAK lint -- ``tests/test_prefix_render_lint.py`` renders
  under ``/sm`` and scans what actually left the server, whatever the reason a
  root-absolute URL got there.

Usage::

    python tests/tools/render_pages.py --code D:\\work\\sm-base --work W --out O
    python tests/tools/render_pages.py --prefix /sm --work W --out O [--legacy-mount]

``--code``  the checkout whose ``quam_state_manager`` is rendered (default: the
            repo this file lives in). The FIXTURE always comes from this file's
            own repo (``tests/test_web.py``'s synthetic chip), so base and HEAD
            render the same chip.
``--work``  scratch dir (wiped). Use the SAME path for two renders you intend to
            compare: a few ids (the dataset folder key) are hashes of the path.
``--prefix`` mount the app under this prefix (``create_app(url_prefix=...)``).
``--legacy-mount`` only for code WITHOUT the ``url_prefix`` kwarg (91c8aae and
            older): render what such an SM emits behind a stripping proxy --
            requests arrive un-prefixed and the app has no idea it is mounted.
            That is the colleague's reported failure, reproduced on purpose.

Isolation (mirrors tests/conftest.py): no ``~/.qualibrate``, no env warm-up, no
history verify sweep, every ``subprocess.Popen`` refused (conda probes, CLIs --
deterministic error branch, identical for base and HEAD), every non-loopback
socket refused, background threads settled before each request.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parent.parent.parent            # tests/tools/ -> repo root
TESTS = REPO / "tests"

# GETs that must not run in a render sweep, with the reason. Everything else
# without a URL converter is rendered.
EXCLUDE = {
    "/open-folder": "side effect: opens the OS file manager",
    "/generate/probe": "spawns the conda env probe",
    "/scheduler/effective-config": "spawns the selected env",
    "/browse": "reads the real filesystem of this machine",
    "/datasets/wait": "25 s long poll",
    "/workspace/tree/poll": "long poll",
    "/api/agent/events": "event stream",
    "/api/agent/chat/events": "event stream",
    "/workbench/watch": "long poll",
    "/field/lab-watch": "long poll",
    "/disk": "measures the real disk of this machine",
    "/disk/status": "measures the real disk of this machine",
    "/disk/banner": "measures the real disk of this machine",
    "/debug/ram": "process RSS, changes every call",
    "/instances/banner": "lists live SM processes on this machine",
    "/api/working-copies/scan": "scans this machine's instance dirs",
}

# Declared root deltas of the prefix branch (spec §5.3): each full document
# gains exactly these two things at root. The renderer counts and removes them.
_DATA_ROOT_EMPTY = re.compile(r' data-root=""')
_SM_ROOT_LINE = re.compile(
    r'^[ \t]*<script src="[^"]*/sm-root\.js(?:\?v=X)?"></script>[ \t]*\r?\n', re.M)
_SM_ROOT_TAG = re.compile(r'<script src="[^"]*/sm-root\.js(?:\?v=X)?"></script>')


def _load_module(name: str, path: Path, functions: tuple[str, ...]):
    """The named top-level functions of a test module, WITHOUT importing it.

    Importing tests/test_web.py would run its imports (``tests._prefix``,
    ``create_app``) -- and with a base checkout first on sys.path, ``tests``
    would even resolve to THAT checkout's package. The fixture builders are
    pure (json + pathlib), so only their source is executed."""
    import ast
    import types
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keep = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in functions]
    missing = set(functions) - {n.name for n in keep}
    if missing:
        raise SystemExit(f"{path.name}: fixture functions not found: {sorted(missing)}")
    mod = types.ModuleType(name)
    mod.__dict__.update({"json": json, "Path": Path})
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), mod.__dict__)
    return mod


# --------------------------------------------------------------------------
# isolation
# --------------------------------------------------------------------------
def _isolate(work: Path) -> None:
    # the user's home: /api/agent/setup reads ~/.claude.json, which Claude Code
    # rewrites all the time -- a golden must not depend on it
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["USERPROFILE"] = str(home)
    os.environ["HOME"] = str(home)
    os.environ["SM_DISABLE_ENV_WARMUP"] = "1"
    os.environ["SM_DISABLE_HISTORY_VERIFY"] = "1"
    os.environ["QUALIBRATE_CONFIG_FILE"] = str(work / "_no_qualibrate_config")
    for k in ("QUALIBRATE_CONFIG_DIR", "QUALIBRATE_STATE_PATH", "QUAM_STATE_PATH",
              "SM_URL_PREFIX", "SM_BEHIND_PROXY", "SM_FRAME_ANCESTORS",
              "SM_TEST_URL_PREFIX"):
        os.environ.pop(k, None)

    class _NoSubprocess(subprocess.Popen):
        def __init__(self, *a, **k):          # noqa: D401 - refuse, deterministically
            raise OSError("render_pages: subprocess refused")
    subprocess.Popen = _NoSubprocess          # type: ignore[misc]

    real_cc = socket.create_connection

    def _cc(address, *a, **k):
        host = str(address[0])
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise OSError("render_pages: network refused")
        return real_cc(address, *a, **k)
    socket.create_connection = _cc            # type: ignore[assignment]


_PERSISTENT: dict[int, str] = {}


def _settle(baseline: set, timeout: float = 8.0) -> list[str]:
    """Wait for threads started since `baseline` to finish; return stragglers.

    A thread that outlives one full wait is a long-lived worker (a poller, a
    watcher), not a job a page is about to depend on: it is recorded in
    ``_PERSISTENT`` and never waited on again, so one resident thread cannot
    turn a 200-page sweep into 200 timeouts."""
    end = time.monotonic() + timeout
    while True:
        extra = [t for t in threading.enumerate()
                 if t.ident not in baseline and t.ident not in _PERSISTENT
                 and t.is_alive() and t is not threading.current_thread()]
        if not extra:
            return []
        if time.monotonic() > end:
            for t in extra:
                _PERSISTENT[t.ident] = t.name
            return [t.name for t in extra]
        time.sleep(0.05)


# --------------------------------------------------------------------------
# app + fixture
# --------------------------------------------------------------------------
def build_app(instance: Path, prefix: str, legacy_mount: bool):
    """Return (app, mode). mode: 'root' | 'native' | 'legacy'."""
    from quam_state_manager.web.app import create_app
    has_kw = "url_prefix" in inspect.signature(create_app).parameters
    if not prefix:
        # NEVER pass url_prefix at root: the base commit has no such kwarg, and
        # the branch must be byte-identical when it is not configured at all.
        return create_app(testing=True, instance_path=str(instance)), "root"
    if has_kw:
        return create_app(testing=True, instance_path=str(instance), url_prefix=prefix), "native"
    if not legacy_mount:
        raise SystemExit(
            "create_app() has no url_prefix kwarg at this commit; pass --legacy-mount "
            "to render what it emits behind a stripping proxy")
    return create_app(testing=True, instance_path=str(instance)), "legacy"


def make_fixture(work: Path) -> dict:
    tw = _load_module("_rp_test_web", TESTS / "test_web.py", ("_make_state", "_make_wiring"))
    dl = _load_module("_rp_test_dataset_load_state", TESTS / "test_dataset_load_state.py",
                      ("_chip", "_seed_run"))
    chip = work / "chip"
    chip.mkdir(parents=True)
    (chip / "state.json").write_text(json.dumps(tw._make_state(), indent=2), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(tw._make_wiring(), indent=2), encoding="utf-8")
    data = work / "data"
    dl._seed_run(data, 1, state=tw._make_state(), wiring=tw._make_wiring())
    dl._seed_run(data, 2, state=tw._make_state(), wiring=tw._make_wiring(),
                 hhmmss="020000", name="32_ramsey")
    return {"chip": chip, "data": data}


def url_list(app, uid: str) -> list[str]:
    rules = sorted({r.rule for r in app.url_map.iter_rules()
                    if "GET" in r.methods and not r.arguments})
    urls = [u for u in rules if u not in EXCLUDE]
    urls += [
        "/qubit/qA1", "/qubit/qA1/config", "/api/qubit/qA1",
        "/pair/qA1-A2", "/pair/qA1-A2/config", "/api/pair/qA1-A2",
        f"/dataset/{uid}", f"/dataset/{uid}/json", f"/dataset/{uid}/interactive",
        f"/dataset/{uid}/ndview", f"/dataset/{uid}/compare-prev",
        f"/dataset/{uid}/prev-state-diff", f"/dataset/{uid}/neighbor",
        "/dataset/by-run/1",
        "/diff?a=nonsense:x&b=nonsense:y",
        "/topology?view=overview", "/topology?view=trends", "/topology?view=coherence",
        "/datasets?q=qA1", "/pulses?per_page=200", "/zline?line=qubits.qA1.z",
        "/journal/day?date=2026-07-29",
    ]
    return urls


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------
def _path_spellings(p: Path) -> list[str]:
    s = str(p)
    out = {s, s.replace("\\", "/"), s.replace("\\", "\\\\"), s.replace("\\", "\\\\\\\\"),
           s.lower(), s.replace("\\", "/").lower()}
    try:
        from urllib.parse import quote, quote_plus
        out |= {quote(s), quote_plus(s), quote(s.replace("\\", "/")),
                quote_plus(s.replace("\\", "/"))}
    except Exception:
        pass
    return sorted(out, key=len, reverse=True)


_ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
# Each entry exists because two renders of the SAME commit differed there
# (measured on 91c8aae: 55 of 398 files before these, 0 after). Keep it that
# way -- a normaliser nobody needed only blinds the golden.
_NORMALISERS = [
    (re.compile(r"\?v=\d+"), "?v=X"),                        # asset mtime (spec §5.3)
    (re.compile(r"([?&]v=)\d+"), r"\1X"),
    (re.compile(r'(__chipToken\s*=\s*)"[^"]*"'), r'\1"X"'),  # spec §5.3
    (re.compile(r'(data-utc=")[^"]*"'), r'\1X"'),            # spec §5.3
    (_ISO, "ISO-TS"),                                        # spec §5.3
    (re.compile(r"\b1[6-9]\d{17}\b"), "EPOCHNS"),           # edit-seq mtime_ns
    (re.compile(r"\b1[6-9]\d{8}(?:\.\d+)?\b"), "EPOCH"),     # "now": unix seconds
    (re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}_\d{6}(?!\d)"), "FILESTAMP"),  # report download name
    (re.compile(r"\b\d{2}:\d{2}:\d{2}\b"), "HH:MM:SS"),       # "checked 19:46:37"
    (re.compile(r'("\w*_ms"\s*:\s*)[\d.]+'), r"\1X"),        # "scan_ms": 0.3
]


def normalise(text: str, work: Path, code: Path | None = None) -> str:
    for sp in _path_spellings(work):
        text = text.replace(sp, "<WORK>")
    if code is not None:                  # /api/agent/setup reports the checkout ("repo")
        for sp in _path_spellings(code):
            text = text.replace(sp, "<CODE>")
    for rx, rep in _NORMALISERS:
        text = rx.sub(rep, text)
    return text


# Spec §5.3 declares two deltas, but the spec itself forces a third CLASS:
# inline template JS that must compare app routes wraps the value in
# `window.SM.path(...)` (§3.2 rule 3: the base.html menu hook; the same shape
# fixes onChipPage) or builds a URL with `window.SM.url(...)`. Both functions
# return their input unchanged when data-root is empty (sm-root.js, §4.2), so
# at root the wrapper is behaviour-neutral -- but it is a byte change. Declared
# here as exactly that: `window.SM.path(E)` / `window.SM.url(E)` with E a plain
# identifier/member chain or one string literal maps back to `E`. Nothing
# else: an edit next to the wrapper, a different call, a wrapped expression
# with an operator in it -- all stay real differences.
_SM_IDENTITY_CALL = re.compile(
    r"""window\.SM\.(?:path|url)\(((?:[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)|'[^'\\\n]*'|"[^"\\\n]*")\)""")


def strip_declared(text: str) -> tuple[str, dict]:
    """Remove the declared root deltas; count them per document."""
    counts = {
        "html_docs": len(re.findall(r"<html[\s>]", text)),
        "data_root_empty": len(_DATA_ROOT_EMPTY.findall(text)),
        "sm_root_script": len(_SM_ROOT_TAG.findall(text)),
        "hx_ext_sm_root": text.count('hx-ext="sm-root"'),
        "data_root_any": len(re.findall(r"\bdata-root=", text)),
        "text_deltas": len(_SM_IDENTITY_CALL.findall(text)),
    }
    text = _DATA_ROOT_EMPTY.sub("", text)
    text = _SM_ROOT_LINE.sub("", text)
    text = _SM_ROOT_TAG.sub("", text)
    text = _SM_IDENTITY_CALL.sub(lambda m: m.group(1), text)
    return text, counts


def _slug(url: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", url.strip("/")) or "ROOT"
    return s[:90] + "_" + hashlib.sha1(url.encode()).hexdigest()[:6]


# --------------------------------------------------------------------------
# the leak scanner (spec §5.4) -- it does not care WHY a URL leaked
# --------------------------------------------------------------------------
_URL_ATTRS = ("href|src|action|formaction|poster|hx-get|hx-post|hx-put|hx-patch|"
              "hx-delete|hx-push-url|hx-replace-url")
_ATTR = re.compile(r'\s(' + _URL_ATTRS + r'|data-[a-z0-9-]+)\s*=\s*(["\'])(/[^"\']*)\2')
_CSS_URL = re.compile(r'url\(\s*["\']?(/[^)"\']*)')
_TOP_HTML = re.compile(r"\A\s*(?:<!doctype[^>]*>\s*)?(?:<!--.*?-->\s*)*(<html\b[^>]*>)",
                       re.I | re.S)
_REDIRECT_HEADERS = ("Location", "HX-Redirect", "HX-Push-Url", "HX-Replace-Url")

ALLOW_FILE = TESTS / "golden" / "prefix_render_allow.txt"


def load_allow(path: Path = ALLOW_FILE) -> list:
    """Allow-list for route KEYS in inline JS (never for URLs that are requested).

    One entry per line: ``region-regex <TAB> requires-regex <TAB> why``.
    A quoted '/route' literal is allowed only if it lies inside a match of
    ``region`` AND ``requires`` matches somewhere in the same page ('-' = no
    requirement). ``requires`` is what makes an entry self-checking: a key
    list is only harmless while the code compares it against a STRIPPED path,
    so the entry names that stripping call -- revert it and the leak is back.
    Blank lines and '#' comments are ignored."""
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError(f"{path.name}: need 3 TAB-separated fields: {line!r}")
        region, requires, why = parts
        out.append((re.compile(region), None if requires == "-" else re.compile(requires), why))
    return out


def _allowed_spans(text: str, allow: list | None) -> list[tuple[int, int]]:
    spans = []
    for region, requires, _why in allow or ():
        if requires is not None and not requires.search(text):
            continue
        spans += [(m.start(), m.end()) for m in region.finditer(text)]
    return spans


def _under(v: str, prefix: str) -> bool:
    return v == prefix or v.startswith((prefix + "/", prefix + "?", prefix + "#"))


def scan_page(text: str, headers: dict, prefix: str, segs: set,
              status: int = 200, allow: list | None = None) -> list[tuple[str, str]]:
    """Return [(kind, snippet)] of every root-absolute app URL not under `prefix`.

    kinds: attr (a URL attribute), data-attr (a data-* attribute naming an app
    route), literal (a quoted '/route...' in inline JS/JSON), css (url(/...)),
    header (Location / HX-*), double (prefix twice), doc (a full document
    without data-root == prefix, or an htmx document without hx-ext="sm-root").
    """
    out: list[tuple[str, str]] = []
    seg_alt = "|".join(re.escape(s) for s in sorted(segs, key=len, reverse=True))
    literal = re.compile(r'(["\'`])/(' + seg_alt + r')(?=[/?#&"\'`])') if segs else None
    covered: set[int] = set()
    for m in _ATTR.finditer(text):
        attr, v = m.group(1), m.group(3)
        covered.add(m.start(3) - 1)
        if v.startswith("//") or _under(v, prefix):
            continue
        first = v.split("/")[1].split("?")[0].split("#")[0]
        if attr.startswith("data-") and not (first in segs or v == "/"):
            continue                              # a data-* path that is not an app URL
        out.append(("data-attr" if attr.startswith("data-") else "attr",
                    text[m.start(1):m.end()][:160]))
    spans = _allowed_spans(text, allow) if prefix else []
    if literal is not None:
        for m in literal.finditer(text):
            if m.start() in covered or any(a <= m.start() < b for a, b in spans):
                continue
            out.append(("literal", text[max(0, m.start() - 40):m.end() + 40]
                        .replace("\n", " ")[:160]))
    for m in _CSS_URL.finditer(text):
        v = m.group(1)
        if not v.startswith("//") and not _under(v, prefix):
            out.append(("css", m.group(0)[:160]))
    for h in _REDIRECT_HEADERS:
        v = headers.get(h)
        if v and v.startswith("/") and not v.startswith("//") and not _under(v, prefix):
            out.append(("header", f"{h}: {v}"[:160]))
    hl = headers.get("HX-Location")
    if hl:
        try:
            p = json.loads(hl).get("path", "") if hl.lstrip().startswith("{") else hl
        except ValueError:
            p = hl
        if p.startswith("/") and not _under(p, prefix):
            out.append(("header", f"HX-Location: {hl}"[:160]))
    if prefix and (prefix + prefix + "/") in text:
        out.append(("double", prefix + prefix + "/"))
    top = _TOP_HTML.match(text) if status == 200 else None
    if top is not None:                    # the DOCUMENT element, not a "<html>" in JS prose
        dr = re.search(r'\sdata-root="([^"]*)"', top.group(1))
        if dr is None or dr.group(1) != prefix:
            out.append(("doc", f"data-root != {prefix!r}: {top.group(1)[:120]}"))
        if "htmx.min.js" in text:
            body = re.search(r"<body\b[^>]*>", text)
            has_ext = bool(body and 'hx-ext="sm-root"' in body.group(0))
            if bool(prefix) != has_ext:
                out.append(("doc", ("no" if prefix else "unexpected")
                            + ' hx-ext="sm-root" on <body>'))
    return out


def scan_dir(out: Path) -> dict:
    """Scan a render_pages output dir -> {page_key: [(kind, snippet)]} (non-empty only)."""
    idx = json.loads((out / "_index.json").read_text(encoding="utf-8"))
    segs = set(idx.get("route_segments") or [])
    prefix = idx["prefix"]
    allow = load_allow()
    found = {}
    for key, meta in idx["pages"].items():
        text = (out / "pages" / meta["file"]).read_text(encoding="utf-8")
        hits = scan_page(text, meta.get("headers") or {}, prefix, segs,
                         meta.get("status", 200), allow=allow)
        if hits:
            found[key] = hits
    return found


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def render_subprocess(out: Path, *, work: Path, code: Path = REPO, prefix: str = "",
                      legacy_mount: bool = False, strip: bool = True,
                      timeout: float = 1800) -> dict:
    """Run this script in a FRESH interpreter (render() patches process globals
    and must import one checkout's quam_state_manager) and return
    ``{"rc", "stdout", "stderr", "out", "index"}`` (index None on failure)."""
    cmd = [sys.executable, str(HERE), "--code", str(code), "--work", str(work),
           "--out", str(out)]
    if prefix:
        cmd += ["--prefix", prefix]
    if legacy_mount:
        cmd.append("--legacy-mount")
    if not strip:
        cmd.append("--no-strip")
    env = dict(os.environ, PYTHONUTF8="1")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=env)
    idx = None
    if r.returncode == 0 and (out / "_index.json").exists():
        idx = json.loads((out / "_index.json").read_text(encoding="utf-8"))
    return {"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr,
            "out": out, "index": idx}


def render(code: Path, work: Path, out: Path, prefix: str, legacy_mount: bool,
           hx: bool = True, strip: bool = True, only: list[str] | None = None) -> dict:
    sys.path.insert(0, str(code))
    import quam_state_manager
    mod = os.path.normcase(os.path.realpath(quam_state_manager.__file__))
    if not mod.startswith(os.path.normcase(os.path.realpath(code)) + os.sep):
        raise SystemExit(f"imported {mod}, not from {code}")

    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    if out.exists():
        shutil.rmtree(out)
    (out / "pages").mkdir(parents=True)
    _isolate(work)

    fx = make_fixture(work)
    app, mode = build_app(work / "instance", prefix, legacy_mount)
    client = app.test_client()
    # what the BROWSER uses: prefixed in native mode; a stripping proxy removes
    # it before SM sees the request in legacy mode, so SM gets the bare path.
    req_prefix = prefix if mode == "native" else ""
    baseline = {t.ident for t in threading.enumerate()}

    r = client.post(req_prefix + "/load", data={"folder": str(fx["chip"])})
    load_status = r.status_code
    r = client.post(req_prefix + "/workspace/add", data={"folder": str(fx["data"])})
    ws_status = r.status_code
    from quam_state_manager.web import routes as routes_mod
    uid = f"{routes_mod._folder_key(fx['data'])}:1"
    _settle(baseline)

    urls = only or url_list(app, uid)
    index: dict = {"code": str(code), "mode": mode, "prefix": prefix,
                   "load_status": load_status, "workspace_add_status": ws_status,
                   "uid": "<UID>", "pages": {},
                   "route_segments": sorted({r.rule.split("/")[1]
                                             for r in app.url_map.iter_rules()
                                             if r.rule.count("/") and r.rule != "/"})}
    variants = [("", {})] + ([(".hx", {"HX-Request": "true"})] if hx else [])
    for url in urls:
        for suffix, headers in variants:
            key = url.replace(uid, "<UID>") + suffix
            stragglers = _settle(baseline)
            t0 = time.perf_counter()
            try:
                resp = client.get(req_prefix + url, headers=headers)
                body = resp.get_data()
                status = resp.status_code
                hdrs = {k: resp.headers.get(k) for k in
                        ("Location", "HX-Redirect", "HX-Location", "HX-Push-Url",
                         "HX-Replace-Url", "HX-Trigger", "Content-Type")
                        if resp.headers.get(k) is not None}
                err = None
            except Exception as exc:                   # a crash is a result too
                body, status, hdrs, err = b"", -1, {}, f"{type(exc).__name__}: {exc}"
            dt = time.perf_counter() - t0
            text = body.decode("utf-8", errors="replace")
            text = text.replace(uid, "<UID>")
            hdrs = {k: normalise(v.replace(uid, "<UID>"), work, code) for k, v in hdrs.items()}
            text = normalise(text, work, code)
            counts = None
            if strip:
                text, counts = strip_declared(text)
            fname = _slug(url.replace(uid, "<UID>")) + suffix + ".txt"
            (out / "pages" / fname).write_text(text, encoding="utf-8", newline="")
            index["pages"][key] = {"status": status, "headers": hdrs, "file": fname,
                                   "error": err, "counts": counts,
                                   "secs": round(dt, 3), "stragglers": stragglers}
    _settle(baseline)
    index["persistent_threads"] = sorted(set(_PERSISTENT.values()))
    (out / "_index.json").write_text(json.dumps(index, indent=1, sort_keys=True),
                                     encoding="utf-8")
    return index


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--code", default=str(REPO))
    ap.add_argument("--work", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefix", default="")
    ap.add_argument("--legacy-mount", action="store_true")
    ap.add_argument("--no-hx", action="store_true")
    ap.add_argument("--no-strip", action="store_true",
                    help="keep the declared deltas (for the leak lint / debugging)")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--scan", action="store_true",
                    help="after rendering, run the leak scanner and print a summary")
    a = ap.parse_args(argv)
    idx = render(Path(a.code), Path(a.work), Path(a.out), a.prefix, a.legacy_mount,
                 hx=not a.no_hx, strip=not a.no_strip, only=a.only)
    slow = sorted(((v["secs"], k) for k, v in idx["pages"].items()), reverse=True)[:5]
    print(json.dumps({"mode": idx["mode"], "pages": len(idx["pages"]),
                      "load": idx["load_status"], "ws": idx["workspace_add_status"],
                      "errors": sum(1 for v in idx["pages"].values() if v["error"]),
                      "slowest": slow}))
    if a.scan:
        found = scan_dir(Path(a.out))
        kinds: dict = {}
        for hits in found.values():
            for k, _ in hits:
                kinds[k] = kinds.get(k, 0) + 1
        print(json.dumps({"leaky_pages": len(found), "leaks": sum(kinds.values()),
                          "by_kind": kinds}))
        (Path(a.out) / "_leaks.json").write_text(json.dumps(found, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
