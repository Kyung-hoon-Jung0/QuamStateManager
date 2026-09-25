"""QA F9 -- Rescan cleared the user's search.

The Rescan button posted only the date tab, and the swapped-in partial
rendered the box from ``q`` -- a GET-only preset a POST never carries -- so
init() read an empty box and every search filter and the "Showing N of M"
strip were gone. The box now rides along as ``keep_q`` and refills the new
box's VALUE only: ``data-preset`` stays empty, because a preset arrival
clears every picker/facet/experiment tick (docs/167) and a Rescan must not.
"""
from __future__ import annotations

from tests.test_datasets_refresh import HX, _DS_HTML, _app, _seed_run
from tests.test_qubit_runs_link import _search_input


class TestRescanKeepsTheSearch:
    def test_the_rescan_swap_refills_the_box_and_is_not_a_preset(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        app, c = _app(tmp_path, root)
        r = c.post("/datasets/rescan", data={"keep_q": "power_rabi"}, headers=HX)
        assert r.status_code == 200
        tag = _search_input(r.get_data(as_text=True))
        assert 'value="power_rabi"' in tag
        assert 'data-preset=""' in tag          # never an arrival: ticks stay

    def test_a_get_never_reads_keep_q(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        app, c = _app(tmp_path, root)
        tag = _search_input(c.get("/datasets?keep_q=x").get_data(as_text=True))
        assert 'value=""' in tag and 'data-preset=""' in tag

    def test_a_q_preset_still_wins_and_still_presets(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        app, c = _app(tmp_path, root)
        tag = _search_input(c.get("/datasets?q=q7").get_data(as_text=True))
        assert 'value="q7"' in tag and 'data-preset="q7"' in tag

    def test_the_value_is_escaped(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        app, c = _app(tmp_path, root)
        r = c.post("/datasets/rescan", data={"keep_q": 'name:"a b"'}, headers=HX)
        tag = _search_input(r.get_data(as_text=True))
        assert 'value="name:&#34;a b&#34;"' in tag

    def test_the_box_is_what_the_button_sends(self):
        i = _DS_HTML.index('hx-post="/datasets/rescan"')
        btn = _DS_HTML[i - 200:i + 400]
        assert "#dataset-search" in btn.split("hx-include=", 1)[1].split('"')[1]
        assert '<input type="search" id="dataset-search" name="keep_q"' in _DS_HTML
