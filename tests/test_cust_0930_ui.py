"""Customer feedback 2026-09-30 (docs/231): four UI defects on one afternoon.

1. The sidebar's Dataset Load box showed the last path typed in this browser
   (another project's folder) instead of the open project's data folder.
2. The top bar's title painted over the ⌗ link and the sync pill whenever the
   CONTENT outgrew the window-width ladder (L text size, a long drift pill, the
   Sync-Qualibrate frame). -> TopbarFit (app.js), pinned in
   topbar_fit_selfcheck.cjs; items no longer shrink below their content.
3. With the bar collapsed (☰) only the Auto pill floated: the sync control --
   and its Apply / Take live / Pull & apply action -- was hidden by an older,
   MORE specific rule.
4. Korean text in the UI ("한글은 전부 영어로"): tooltips, the Auto-Sync panel,
   the Agent-lock message. The UI is English-only now, compact.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "quam_state_manager"
CSS = PKG / "web" / "static" / "style.css"
HANGUL = re.compile(r"[가-힣]")


def _css_rules(text: str):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        yield m.group(1).strip(), m.group(2)


# ------------------------------------------------------------- 2. the top bar
@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_topbar_fit_selfcheck():
    r = subprocess.run(["node", str(ROOT / "tests" / "topbar_fit_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr


def test_title_row_items_keep_their_content_width():
    rules = list(_css_rules(CSS.read_text(encoding="utf-8")))
    hit = [b for s, b in rules if s == ".topbar > nav > ul:first-child > li"
           and re.search(r"flex-shrink\s*:\s*0", b)]
    assert hit, "a top-bar item may shrink below its own text again -- it paints over its neighbour"


def test_every_fit_level_has_css():
    css = CSS.read_text(encoding="utf-8")
    js = (PKG / "web" / "static" / "app.js").read_text(encoding="utf-8")
    m = re.search(r"window\.TopbarFit = \(function \(\) \{.*?var MAX = (\d+);", js, re.S)
    assert m, "TopbarFit's MAX not found"
    for n in range(1, int(m.group(1)) + 1):
        assert f".topbar.tb-fit-{n}" in css, f"fit level {n} has no rule: a step that frees nothing"


# ----------------------------------------------------- 3. the collapsed bar
def test_the_collapsed_bar_keeps_the_sync_control():
    """Every rule that hides #pending-tray children while the bar is collapsed
    must exempt .sync-control -- the one that did not was MORE specific than
    the sync-ux rule meant to show it, so it won."""
    offenders = []
    for sel, body in _css_rules(CSS.read_text(encoding="utf-8")):
        if not re.search(r"display\s*:\s*none", body):
            continue
        for part in sel.split(","):
            part = part.strip()
            if part.startswith("html.topbar-hidden #pending-tray >") and ":not(" in part \
                    and ":not(.sync-control)" not in part:
                offenders.append(part)
    assert not offenders, offenders


def test_the_sync_control_is_shown_when_collapsed():
    rules = dict(_css_rules(CSS.read_text(encoding="utf-8")))
    body = rules.get("html.topbar-hidden #pending-tray > .sync-control", "")
    assert re.search(r"display\s*:\s*inline-flex", body), body


# ------------------------------------------------------- 4. English-only UI
def _strip_js_comments(t: str) -> str:
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return re.sub(r"(?m)(^|\s)//.*$", r"\1", t)


def _korean_ui_lines() -> list[str]:
    out = []
    for p in (PKG / "web" / "templates").rglob("*.html"):
        t = re.sub(r"\{#.*?#\}", "", p.read_text(encoding="utf-8"), flags=re.S)
        t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
        t = re.sub(r"<script\b[^>]*>(.*?)</script>",
                   lambda m: _strip_js_comments(m.group(0)), t, flags=re.S)
        out += [f"{p.name}: {l.strip()[:90]}" for l in t.splitlines() if HANGUL.search(l)]
    for p in (PKG / "web" / "static").glob("*.js"):
        if ".min." in p.name or "plotly" in p.name:
            continue
        t = _strip_js_comments(p.read_text(encoding="utf-8"))
        out += [f"{p.name}: {l.strip()[:90]}" for l in t.splitlines() if HANGUL.search(l)]
    for p in PKG.rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        if not HANGUL.search(src):
            continue
        tree = ast.parse(src)
        docs = {id(n.value) for n in ast.walk(tree)
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        for n in ast.walk(tree):
            if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in docs and HANGUL.search(n.value)):
                # the tag vocabulary drops common Korean words from a user's
                # OWN notes -- a filter over their text, never shown
                if p.name == "tag_vocab.py":
                    continue
                out.append(f"{p.name}:{n.lineno}: {n.value[:90]!r}")
    return out


def test_the_ui_is_english_only():
    found = _korean_ui_lines()
    assert not found, "Korean text reaches the UI:\n" + "\n".join(found[:20])


# ------------------------------------------- 1. the Dataset Load box (server)
def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True)
    _write(folder / "state.json", '{"qubits": {"qA1": {"id": "qA1"}}}')
    _write(folder / "wiring.json", '{"wiring": {}}')
    return folder


@pytest.fixture
def proj(tmp_path, monkeypatch, any_project_env_chosen):
    cfg = tmp_path / ".qualibrate"
    chip = _chip(tmp_path / "chips" / "arbel_chip")
    storage = tmp_path / "data" / "arbel_data"
    storage.mkdir(parents=True)
    _write(cfg / "config.toml", f'''
[qualibrate]
project = "arbel"
version = 5

[quam]
state_path = "{chip.as_posix()}"
version = 3
''')
    _write(cfg / "projects" / "arbel" / "config.toml",
           f'[qualibrate.storage]\nlocation = "{storage.as_posix()}"\n'
           f'[quam]\nstate_path = "{chip.as_posix()}"\n')
    monkeypatch.setenv("QUALIBRATE_CONFIG_FILE", str(cfg))
    monkeypatch.delenv("QUALIBRATE_CONFIG_DIR", raising=False)
    from quam_state_manager.core import qualibrate_config as qc
    qc._state_index_cache.clear()
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return {"c": app.test_client(), "storage": storage, "chip": chip, "tmp": tmp_path}


def _data_path_in(html: str) -> str:
    m = re.search(r"window\.__activeDataPath = (\"(?:[^\"\\]|\\.)*\");", html)
    assert m, "base.html no longer publishes __activeDataPath"
    import json
    return json.loads(m.group(1))


def test_an_open_project_publishes_its_data_folder(proj):
    r = proj["c"].post("/qualibrate/open", data={"project": "arbel"})
    assert r.status_code == 302
    html = proj["c"].get("/qubits").get_data(as_text=True)
    assert Path(_data_path_in(html)) == proj["storage"]


def test_no_project_publishes_nothing(proj):
    other = _chip(proj["tmp"] / "chips" / "standalone")
    proj["c"].post("/load", data={"folder": str(other)})
    assert _data_path_in(proj["c"].get("/qubits").get_data(as_text=True)) == ""


def test_the_page_script_fills_the_box_from_it():
    base = (PKG / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    i = base.index('var _wp = document.getElementById("workspace-path-input");')
    block = base[i:i + 400]
    assert "window.__activeDataPath" in block and "_wp.value = window.__activeDataPath" in block


# ----------------------------------------- the sidebar poll (docs/231 aside)
def test_the_sidebar_poll_pauses_only_while_typing():
    """Focus alone used to pause the tree refresh for as long as the caret sat
    in the filter box -- a click into it and no new run ever appeared."""
    base = (PKG / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "window.__sbFilterKeyAt = Date.now()" in base
    assert re.search(r"document\.activeElement === _fi\s*&& \(Date\.now\(\) - \(window\.__sbFilterKeyAt \|\| 0\)\) < 5000\) return;",
                     base), "the poll may pause on focus alone again"


# ------------------- docs/233: the before->after chip never outlives the mark
def test_the_chip_only_shows_on_a_modified_cell():
    rules = dict(_css_rules(CSS.read_text(encoding="utf-8")))
    assert re.search(r"display\s*:\s*block",
                     rules.get(".bulk-td.bulk-ba-show:has(.bulk-cell-modified) .bulk-ba", "")), \
        "the chip may show on a cell that is no longer modified (Auto-Sync applied it)"
    assert ".bulk-td.bulk-ba-show .bulk-ba" not in rules, "an unguarded show rule is back"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
@pytest.mark.parametrize("name", ["bulk_markup_selfcheck.cjs", "pending_markers_selfcheck.cjs"])
def test_chip_selfchecks(name):
    r = subprocess.run(["node", str(ROOT / "tests" / name)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(ROOT), timeout=180)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "FAIL" not in (r.stdout + r.stderr), r.stdout[-2000:] + r.stderr[-2000:]


def test_the_shipped_package_carries_no_hangul_at_all():
    """2026-10-03 (user): QSM is English all the way down -- comments,
    docstrings, templates and stylesheets included, not only on-screen text.
    The customer quotes that explained a change were translated in place.
    A non-English word list that is DATA (tag_vocab's Korean filler words)
    is written as unicode escapes, so it works the same and the source stays
    English."""
    vendor = ("plotly", "htmx", "split", "pico", ".min.")
    found = []
    for p in sorted(PKG.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (
                ".py", ".js", ".html", ".css", ".json", ".md", ".txt", ".toml"):
            continue
        if any(v in p.name.lower() for v in vendor):
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if HANGUL.search(line):
                found.append(f"{p.relative_to(PKG)}:{n}: {line.strip()[:80]}")
    assert not found, "Hangul in the shipped package:\n" + "\n".join(found[:20])
