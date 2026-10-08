"""docs/301 F42 -- a component page's script runs on every visit; its Escape
handler for the JSON panel must be bound once, not once per visit.

The Qubits / Pairs / Flux / Resonators / Couplers pages each ran
``document.addEventListener('keydown', ...)`` at every render, so every visit
left one more document listener behind (measured in real Chrome: +1 per
visit). The rendered page's own script is executed three times under jsdom
and the document keydown listeners are counted.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_hub_drawer import _inline, sm  # noqa: F401 -- fixtures

NODE = shutil.which("node")
HARNESS = r"""
const { JSDOM } = require('jsdom');
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const dom = new JSDOM('<!DOCTYPE html><body><div id="json-panel" class="hidden"></div></body>',
                      { runScripts: 'outside-only' });
const w = dom.window;
let n = 0;
const add = w.document.addEventListener.bind(w.document);
w.document.addEventListener = function (type, fn, opts) { if (type === 'keydown') n++; return add(type, fn, opts); };
const counts = [];
for (let i = 0; i < 3; i++) {
  try { w.eval(src); } catch (e) { /* other parts of the page script may need more DOM */ }
  counts.push(n);
}
let closed = 0;
w.closeJsonPanel = function () { closed++; };
w.document.getElementById('json-panel').classList.remove('hidden');
w.document.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Escape' }));
console.log(JSON.stringify({ counts, closed }));
"""


def _page_script(html: str) -> str:
    """The page script's JSON-panel part, as rendered: from the panel's close
    function to the end of the script's closure (the rest of the script needs
    the page's tables, which the harness does not build)."""
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.S):
        body = m.group(1)
        i = body.find("window.closeJsonPanel = function")
        if i >= 0:
            j = body.rfind("})();")
            assert j > i, "the JSON-panel part is not inside the page's closure"
            return body[i:j]
    raise AssertionError("no JSON-panel script on the page")


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("page", ["/qubits", "/pairs"])
def test_the_escape_handler_is_bound_once(sm, tmp_path, page):
    html = sm["client"].get(page, headers={"HX-Request": "true"}).data.decode()
    script = tmp_path / "page.js"
    script.write_text(_page_script(html), encoding="utf-8")
    harness = tmp_path / "h.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    env = dict(os.environ, NODE_PATH=os.environ.get("NODE_PATH")
               or str(Path(__file__).resolve().parents[1] / "node_modules"))
    out = subprocess.run([NODE, str(harness), str(script)], capture_output=True, text=True,
                         env=env, timeout=120)
    if "Cannot find module 'jsdom'" in out.stderr:
        pytest.skip("jsdom not installed")
    assert out.returncode == 0, out.stderr[-800:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["counts"][0] >= 1, got
    assert got["counts"] == [got["counts"][0]] * 3, f"one more keydown listener per visit: {got}"
    assert got["closed"] == 1, f"Escape closes the panel exactly once: {got}"
