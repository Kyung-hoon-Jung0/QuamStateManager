"""Page-specific script bundles load only for the page that uses them
(docs/141 4l). base.html emits the core scripts on every page, the current
page's bundles as tags, and ONE manifest of every lazy file; app.js's
Bundles loads a page's bundles before an htmx navigation issues its request.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"

CORE = {"sidebar-order.js", "htmx.min.js", "split.min.js", "search-query.js", "app.js",
        "sidebar-typeahead.js", "auto-apply.js",
        "plot-theme.js", "calc.js", "manual.js", "undo-trail.js", "opx-health.js", "ui-menu.js"}
LAZY = {"grid-virt.js", "bulk-edit.js", "pair-edit.js", "all-values.js", "pulses.js", "topo-graph.js", "wiring-grid.js",
        "component-map.js", "diagram-add.js", "chip-status.js", "gen-additions.js", "gen-rules.js", "generate.js", "generate_preview.js", "dataset-virtual.js",
        "ndview.js", "scheduler.js", "autofit.js", "compare-hub.js", "diff-panes.js", "journal.js", "opx-monitor.js",
        "notes.js", "agent-setup.js", "zline.js", "pulses-vt.js", "gate-status.js",
        "coupler-units.js", "cz-plan.js", "cz-map.js", "ui.js", "cz-gate.js", "gate-timeline.js", "vendor/cm6/cm6.min.js", "tree-json-edit.js", "port-play.js",
        "vendor/cm6/cm6-python.min.js", "pulse-class-edit.js"}   # cz-plan.js: Chip Status > CZ gate plan (2026-10-15); cz-map.js: its chip map (A14)   # notes.js: docs/167's bundle, never added here (red on main since); journal.js / agent-setup.js: docs/173


def _scripts(html: str):
    tags = re.findall(r'<script src="/static/([A-Za-z0-9_\-\./]+\.js)[^"]*"( data-bundle-file="[^"]*")?', html)
    return [t[0] for t in tags], [t[0] for t in tags if t[1]]


def _manifest(html: str) -> dict:
    m = re.search(r'<script id="bundle-manifest" type="application/json">(.*?)</script>', html, re.S)
    assert m, "the manifest is on every page"
    return json.loads(m.group(1))


def _write_chip(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9, "z": {"joint_offset": 0.08},
                           "xy": {"operations": {"x180": {"length": 40, "amplitude": 0.2,
                                                          "__class__": "quam.components.pulses.SquarePulse"}}}}},
        "qubit_pairs": {}, "active_qubit_names": ["qA1"],
    }), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}), encoding="utf-8")


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmp:
        live = Path(tmp) / "chips" / "live"
        _write_chip(live)
        c = create_app(testing=True, instance_path=str(Path(tmp) / "_inst")).test_client()
        r = c.post("/load", data={"folder": str(live)})
        assert r.status_code in (200, 302)
        yield c
        # S10 C3: snapshot readers -> ledger readers, release the pool before deleting the fixture.
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def test_every_page_ships_the_core_and_only_its_own_bundles(client):
    seen = {}
    for url, want, unwanted in (
        ("/bulk", {"grid-virt.js", "bulk-edit.js", "pair-edit.js", "all-values.js"}, {"chip-status.js", "generate.js", "pulses.js"}),
        ("/pulses", {"pulses.js"}, {"bulk-edit.js", "chip-status.js", "generate.js", "zline.js"}),
        ("/zline", {"zline.js"}, {"bulk-edit.js", "chip-status.js", "generate.js", "pulses.js"}),
        ("/explorer", {"vendor/cm6/cm6.min.js", "tree-json-edit.js"}, LAZY - {"vendor/cm6/cm6.min.js", "tree-json-edit.js"}),
        ("/topology", {"chip-status.js", "topo-graph.js", "component-map.js"}, {"bulk-edit.js", "generate.js"}),
        ("/generate", {"coupler-units.js", "gen-additions.js", "gen-rules.js", "generate.js", "generate_preview.js", "wiring-grid.js", "pulses.js", "topo-graph.js"}, {"bulk-edit.js", "chip-status.js"}),
        ("/datasets", {"dataset-virtual.js", "ndview.js"}, {"bulk-edit.js", "generate.js"}),
        # A14: the CZ map draws the chip with TopoGraph.layoutFor -- the page carries it
        ("/cz-plan", {"coupler-units.js", "topo-graph.js", "cz-map.js", "cz-plan.js"}, {"bulk-edit.js", "chip-status.js", "generate.js"}),
    ):
        html = client.get(url).get_data(as_text=True)
        names, lazy = _scripts(html)
        assert CORE <= set(names), (url, names)
        assert want <= set(lazy), (url, lazy)
        assert not (unwanted & set(names)), (url, unwanted & set(names))
        assert len(names) == len(set(names)), (url, "a file is emitted twice")
        seen[url] = names
    assert len(seen["/explorer"]) < len(seen["/bulk"])


def test_the_manifest_names_every_lazy_file_once_and_the_page_map_covers_the_routes(client):
    man = _manifest(client.get("/explorer").get_data(as_text=True))
    assert set(man["files"]) == LAZY
    for f, url in man["files"].items():
        assert url.startswith("/static/" + f), (f, url)
        assert (_STATIC / f).exists(), f
    for name, files in man["bundles"].items():
        assert files and all(f in man["files"] for f in files), name
    for page, bundles in man["pages"].items():
        assert all(b in man["bundles"] for b in bundles), page
    # every page token base.html maps (a dropped token used to pass every pin -- 4l-review)
    assert set(man["pages"]) == {
        "bulk", "table", "pulses", "generate", "regenerate", "instrument", "topology", "trends", "trend",
        "qubits", "pairs", "resonators", "flux", "couplers", "qdac",
        "datasets", "dataset_detail", "dataset_compare", "collections", "fit-audit",
        "scheduler", "autofit", "compare_hub", "diff", "journal", "opx_monitor", "agent_setup", "zline", "czplan",
        "ui_kit", "explorer", "port_play", "pulse_class",  # port_play: the standalone Port play page; explorer: the CodeMirror bundle (D-21); docs/308: the UI kit sample page (a full page load from /help)
    }
    assert man["pages"]["topology"] == ["chipstatus", "components"] and man["pages"]["trends"] == ["chipstatus", "datasets"]
    # A14: an htmx navigation to /cz-plan loads the topology helper before the map, the map before the detail
    assert man["bundles"]["czplan"] == ["coupler-units.js", "topo-graph.js", "cz-map.js", "cz-plan.js"]
    # the JS path map agrees with the page map on the pages that matter
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "window.Bundles = (function () {" in app_js
    for token in ('["grid"]', '["pulses"]', '["generate"]', '["wiring"]', '["chipstatus", "components"]',
                  '["components"]', '["datasets"]', '["scheduler"]', '["autofit"]', '["compare"]',
                  '["journal"]', '["opxmon"]', '["agent_setup"]', '["zline"]', '["czplan", "components"]', '["cm6"]', '["portplay"]'):
        assert token in app_js, token
    assert 'document.addEventListener("htmx:confirm", function (evt) {' in app_js
    assert "d.issueRequest();" in app_js and "d.issueRequest(true)" not in app_js, "the skip flag would skip hx-confirm"
    # every PATHS regex the loader carries (a removed line used to pass every pin -- 4l-review)
    for rx in ('/^\\/bulk(', '/^\\/table(', '/^\\/pulses?(', '/^\\/(generate|regenerate)(', '/^\\/instrument(',
               '/^\\/topology(', '/^\\/chip-status(', '/^\\/wiring(', '/^\\/trends?(',
               '/^\\/(qubits|pairs|resonators|flux|couplers|qdac)(', '/^\\/(datasets?|collections|fit-audit)(',
               '/^\\/scheduler(', '/^\\/autofit(', '/^\\/(compare-hub|compare|diff)(',
               # docs/173 partial-reached bundle pages (dry-run review round 1: the Agent home's
               # "Setup" link swapped a shell that stayed on "Loading..." -- no entry, no script)
               '/^\\/journal(', '/^\\/agent\\/setup(', '/^\\/zline(', '/^\\/cz-plan(', '/^\\/port-play('):
        assert rx in app_js, rx


def test_global_controls_reach_into_bundles_through_the_loader():
    base = (_ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    # no inline handler calls a grid global directly (the grid bundle is not on every page)
    assert re.search(r'on\w+="(BulkEdit|AllValues|PairEdit)\.', base) is None
    assert "Bundles.call('grid', 'BulkEdit.setFont', 0.85)" in base


def test_the_loader_executes_under_jsdom():
    """The loader block runs for real (tests/bundles_selfcheck.cjs): manifest,
    present-tag detection, URL map, ordered loading, the htmx:confirm hold,
    a lost script never stranding a navigation, Bundles.call."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT),
                      timeout=300).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "bundles_selfcheck.cjs")], capture_output=True, text=True,
                         encoding="utf-8", cwd=str(_ROOT), timeout=300)
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok bundles_selfcheck" in res.stdout and " 0 failed" in res.stdout


def test_an_htmx_navigation_loads_every_bundle_a_full_load_of_that_page_ships(client):
    """The Pulses page and the CZ gate plan draw the shared chip map: base.html
    ships them the components bundle, so the loader's URL map must ask for it
    too, or a sidebar navigation leaves the map on "Loading chip layout..."."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    man = _manifest(client.get("/explorer").get_data(as_text=True))
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    i = app_js.index("var PATHS = [")
    j = app_js.index("];", i)
    script = (app_js[i:j + 2] + "\nconst out = {};\n"
              + "for (const p of ['/pulses', '/cz-plan']) { out[p] = [];"
              + " PATHS.forEach(function (pr) { if (pr[0].test(p)) pr[1].forEach(function (n) {"
              + " if (out[p].indexOf(n) < 0) out[p].push(n); }); }); }\n"
              + "console.log(JSON.stringify(out));")
    got = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True,
                                     check=True, timeout=300).stdout)
    assert set(man["pages"]["pulses"]) <= set(got["/pulses"]), got
    assert set(man["pages"]["czplan"]) <= set(got["/cz-plan"]), got


# docs/308 4b: the kit menu's behaviour is CORE (ui-menu.js in base.html), so a
# .ui-menu works on every page that extends base.html, in every swapped
# partial and dialog. A standalone page (its own <html>, not base.html) that
# renders a menu would get a dead menu: such a page must load ui-menu.js.
_STANDALONE_MENU_PAGES_THAT_LOAD_IT = set()


def test_every_page_that_can_render_a_kit_menu_has_its_behaviour():
    import re
    tpl = _ROOT / "quam_state_manager" / "web" / "templates"
    base = (tpl / "base.html").read_text(encoding="utf-8")
    assert "asset_url('ui-menu.js')" in base and "data-bundle-file=\"{{ _f }}\"" in base
    core_part = base.split('id="bundle-manifest"')[0]
    assert "ui-menu.js" in core_part, "ui-menu.js is a core script, before the page bundles"
    for p in sorted(tpl.glob("*.html")):
        t = p.read_text(encoding="utf-8")
        if p.name == "_ui.html" or not re.search(r"ui_menu\(|\bui-menu\b|part.{0,4}menu", t):
            continue
        standalone = "<html" in t and "extends" not in t
        if standalone:
            assert "ui-menu.js" in t, f"{p.name} renders a kit menu but is a standalone page without ui-menu.js"
