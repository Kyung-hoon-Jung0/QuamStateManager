"""docs/301 (F39): a run's header names its instant like every other time on the page.

With no project time zone, the header read the acquisition PC's clock with no
offset ("2026-04-03 01:03:26"), while Chip Status Trends placed the same run at
14:03 in the browser's zone -- a run recorded at 01:03-04:00. The header is now
the instant, localized by the page script (``ts-local`` with ``data-utc``); a
chosen zone keeps the server-rendered line."""
from __future__ import annotations

import json
import re
from pathlib import Path

from quam_state_manager.core import project_time
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def _seed(root: Path) -> None:
    d = root / "2026-04-03" / "#7_25_T1_010326"
    d.mkdir(parents=True)
    (d / "node.json").write_text(json.dumps({
        "metadata": {"name": "25_T1", "status": "successful",
                     "run_start": "2026-04-03T01:03:12.424-04:00", "run_end": "2026-04-03T01:03:26.757-04:00"},
        "data": {"parameters": {"model": {"qubits": ["q0"]}}, "outcomes": {}},
        "id": 7, "parents": [], "created_at": "2026-04-03T01:03:26-04:00",
    }), encoding="utf-8")
    (d / "data.json").write_text(json.dumps({"fit_results": {"q0": {"T1": 6.7e-5}}}), encoding="utf-8")


def _header(html: str) -> str:
    m = re.search(r'<div class="inspector-title">(.*?)</div>', html, re.S)
    assert m, html[:500]
    return m.group(1)


def _open(tmp_path):
    f = tmp_path / "data"
    _seed(f)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/workspace/add", data={"folder": str(f)})
    with app.app_context():
        key = routes._folder_key(f)
    return app, c, key


def test_no_zone_the_header_is_the_instant_localized_in_the_browser(tmp_path):
    _app, c, key = _open(tmp_path)
    head = _header(c.get(f"/dataset/{key}:7", headers={"HX-Request": "true"}).get_data(as_text=True))
    assert 'class="ts-local" data-utc="2026-04-03T05:03:26Z"' in head, head
    assert "01:03:26" not in re.sub(r"<[^>]+>", "", head), "not the acquisition PC's clock with no offset"


def test_a_chosen_zone_keeps_the_server_line(tmp_path):
    app, c, key = _open(tmp_path)
    project_time.set_zone(app.instance_path, "proj", "Asia/Seoul")
    head = _header(c.get(f"/dataset/{key}:7", headers={"HX-Request": "true"}).get_data(as_text=True))
    assert "2026-04-03 14:03:26 (UTC+9)" in head and "ts-local" not in head, head
