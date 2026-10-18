"""v1.5.1 T1-F10: the State History drift table shows the whole path.

Its PATH column was cut to about ten characters ("ports.anal",
"qubit_pairs.q1-"), so nobody could tell which port or pair changed. The cell
now wraps the full path at its dots (``<wbr>``, never mid-name), the full text
still on hover and in a copy. Pinned on the REAL /state/drift/view markup
under the shipped stylesheets (tests/v151_css_selfcheck.cjs).
"""

from __future__ import annotations

import re

from tests.css_pin import run_css_cases
from tests.test_live_drift import _app_client, _chip_state, _drift_count, _write_live, live_folder  # noqa: F401


def test_the_drift_path_is_whole_and_wraps_at_its_dots(tmp_path, live_folder):  # noqa: F811
    client = _app_client(tmp_path)
    client.post("/load", data={"folder": str(live_folder)})
    _drift_count(client)
    _write_live(live_folder, _chip_state(f_01=6.30e9, t1=8500))
    html = client.get("/state/drift/view").get_data(as_text=True)
    cells = re.findall(r'<td class="diff-path-cell"><code class="dot-path" title="([^"]+)">(.*?)</code></td>', html)
    assert cells, html[:2000]
    for title, body in cells:
        assert body.replace("<wbr>", "") == title, (title, body)
        assert body.count("<wbr>") == title.count("."), body
    run_css_cases(tmp_path, [{
        "name": "drift path", "html": html,
        "checks": [
            {"sel": ".live-drift td.diff-path-cell", "prop": "whiteSpace", "eq": "normal", "all": True,
             "why": "the path wraps instead of being cut"},
            {"sel": ".live-drift td.diff-path-cell", "prop": "textOverflow", "neq": "ellipsis",
             "why": "no ellipsis hides the port or pair"},
            {"sel": ".live-drift td.diff-path-cell", "prop": "maxWidth", "eq": "none",
             "why": "the column is not squeezed to its minimum"},
        ]}])
