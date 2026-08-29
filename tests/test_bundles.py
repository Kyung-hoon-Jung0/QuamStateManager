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

CORE = {"htmx.min.js", "split.min.js", "search-query.js", "app.js", "auto-apply.js",
        "plot-theme.js", "calc.js", "manual.js", "undo-trail.js"}
LAZY = {"bulk-edit.js", "pair-edit.js", "all-values.js", "pulses.js", "topo-graph.js", "wiring-grid.js",
        "component-map.js", "chip-status.js", "generate.js", "generate_preview.js", "dataset-virtual.js",
        "ndview.js", "scheduler.js", "autofit.js", "compare-hub.js", "diff-panes.js"}


def _scripts(html: str):
    tags = re.findall(r'<script src="/static/([A-Za-z_\-\.]+\.js)[^"]*"( data-bundle-file="[^"]*")?', html)
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


def test_every_page_ships_the_core_and_only_its_own_bundles(client):
    seen = {}
    for url, want, unwanted in (
        ("/bulk", {"bulk-edit.js", "pair-edit.js", "all-values.js"}, {"chip-status.js", "generate.js", "pulses.js"}),
        ("/pulses", {"pulses.js"}, {"bulk-edit.js", "chip-status.js", "generate.js"}),
        ("/explorer", set(), LAZY),
        ("/topology", {"chip-status.js", "topo-graph.js", "component-map.js"}, {"bulk-edit.js", "generate.js"}),
        ("/generate", {"generate.js", "generate_preview.js", "wiring-grid.js", "pulses.js", "topo-graph.js"}, {"bulk-edit.js", "chip-status.js"}),
        ("/datasets", {"dataset-virtual.js", "ndview.js"}, {"bulk-edit.js", "generate.js"}),
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
        "scheduler", "autofit", "compare_hub", "diff",
    }
    assert man["pages"]["topology"] == ["chipstatus", "components"] and man["pages"]["trends"] == ["chipstatus", "datasets"]
    # the JS path map agrees with the page map on the pages that matter
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "window.Bundles = (function () {" in app_js
    for token in ('["grid"]', '["pulses"]', '["generate"]', '["wiring"]', '["chipstatus", "components"]',
                  '["components"]', '["datasets"]', '["scheduler"]', '["autofit"]', '["compare"]'):
        assert token in app_js, token
    assert 'document.addEventListener("htmx:confirm", function (evt) {' in app_js
    assert "d.issueRequest();" in app_js and "d.issueRequest(true)" not in app_js, "the skip flag would skip hx-confirm"
    # every PATHS regex the loader carries (a removed line used to pass every pin -- 4l-review)
    for rx in ('/^\\/bulk(', '/^\\/table(', '/^\\/pulses?(', '/^\\/(generate|regenerate)(', '/^\\/instrument(',
               '/^\\/topology(', '/^\\/chip-status(', '/^\\/wiring(', '/^\\/trends?(',
               '/^\\/(qubits|pairs|resonators|flux|couplers|qdac)(', '/^\\/(datasets?|collections|fit-audit)(',
               '/^\\/scheduler(', '/^\\/autofit(', '/^\\/(compare-hub|compare|diff)('):
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
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "bundles_selfcheck.cjs")], capture_output=True, text=True,
                         encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok bundles_selfcheck" in res.stdout and " 0 failed" in res.stdout
