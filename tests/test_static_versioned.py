"""Static assets are served with a one-year cache, so every URL a page emits for one must
carry a version token (asset_url's ?v=): an unversioned URL keeps the previous release's
file in the browser after an upgrade.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tests.test_bundles import _manifest, client  # noqa: F401  (fixture)

_TEMPLATES = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "templates"
_STATIC_REF = re.compile(r'(?:src|href|data-plotly-src)="(/static/[^"]+)"')


def test_no_template_builds_an_unversioned_static_url():
    bad = [f"{p.name}:{i}" for p in _TEMPLATES.rglob("*.html")
           for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
           if re.search(r"url_for\(\s*['\"]static['\"]", line) or re.search(r'(?:src|href)="/static/', line)]
    assert not bad, "use asset_url(...) for static files: " + ", ".join(bad)


def test_every_static_url_on_a_page_carries_a_version(client):  # noqa: F811
    seen = 0
    for url in ("/", "/bulk", "/pulses", "/topology", "/cz-plan", "/generate", "/datasets", "/calc-window"):
        r = client.get(url)
        if r.status_code != 200:
            continue
        html = r.get_data(as_text=True)
        refs = _STATIC_REF.findall(html)
        unversioned = [u for u in refs if "?v=" not in u]
        assert not unversioned, f"{url}: unversioned static URLs {unversioned}"
        seen += len(refs)
        if 'id="bundle-manifest"' in html:
            files = _manifest(html)["files"]
            assert all("?v=" in u for u in files.values()), f"{url}: an unversioned bundle URL"
    assert seen > 20, "the pages emitted their static references"
