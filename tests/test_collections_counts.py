"""QA F10 -- the Collections header counted the whole workspace.

``_datasets_view`` built ``total``/``stats``/experiment chips/date tabs from
store-wide aggregates (``run_count``, ``experiment_types``, ``dates``,
``summary_stats``) and filtered only the ROWS to tagged runs, so one tagged
run read "Collections (4162 runs, 67 types, 7 qubits)" over a 1-row table.
The Collections aggregates now come from the tagged runs (every date, like
Datasets' are independent of the date tab); /datasets is unchanged.
"""
from __future__ import annotations

import re

from tests.test_datasets_refresh import HX, _app, _seed_run


def _setup(tmp_path, tag_ids):
    root = tmp_path / "data"
    _seed_run(root, 1, date="2026-05-01", name="power_rabi")
    _seed_run(root, 2, date="2026-05-02", name="ramsey")
    _seed_run(root, 3, date="2026-05-03", name="resonator_spectroscopy")
    app, c = _app(tmp_path, root)
    from quam_state_manager.web import routes as rm
    for rid in tag_ids:
        r = c.post(f"/dataset/{rm._dataset_uid(rm._folder_key(root), rid)}/tag",
                   json={"tag": "flagged"})
        assert r.status_code == 200, r.status_code
    return app, c


def _head(html):
    m = re.search(r'class="ds-head-title">.*?<small class="muted">\((.*?)\)</small>', html, re.S)
    assert m, "the header count"
    return m.group(1)


def _summary(html):
    m = re.search(r'class="exp-filter-summary">(.*?)</span>', html, re.S)
    assert m
    return m.group(1)


class TestCollectionsCountsTheCollection:
    def test_one_tagged_run_reads_one_run(self, tmp_path):
        app, c = _setup(tmp_path, [2])
        coll = c.get("/collections", headers=HX).get_data(as_text=True)
        assert _head(coll) == "1 runs, 1 types, 1 qubits"
        assert _summary(coll).startswith("1 experiment type —")
        # one tagged date -> no date-tab strip (the existing `> 1` guard)
        assert "ds-date-tabs" not in coll
        # the experiment chips are the collection's
        assert 'data-exp="ramsey"' in coll
        assert 'data-exp="power_rabi"' not in coll

    def test_datasets_still_counts_everything(self, tmp_path):
        app, c = _setup(tmp_path, [2])
        ds = c.get("/datasets", headers=HX).get_data(as_text=True)
        assert _head(ds) == "3 runs, 3 types, 3 qubits"
        assert "ds-date-tabs" in ds

    def test_two_tagged_dates_get_their_tabs_only(self, tmp_path):
        app, c = _setup(tmp_path, [1, 3])
        coll = c.get("/collections", headers=HX).get_data(as_text=True)
        assert _head(coll) == "2 runs, 2 types, 2 qubits"
        tabs = coll[coll.index("ds-date-tabs"):]
        tabs = tabs[:tabs.index("</div>")]
        assert "2026-05-01" in tabs and "2026-05-03" in tabs
        assert "2026-05-02" not in tabs

    def test_the_counts_ignore_the_active_date_tab(self, tmp_path):
        """As on Datasets: the tab narrows the table, not the header."""
        app, c = _setup(tmp_path, [1, 3])
        coll = c.get("/collections?date=2026-05-01", headers=HX).get_data(as_text=True)
        assert _head(coll) == "2 runs, 2 types, 2 qubits"

    def test_no_tags_reads_zero(self, tmp_path):
        app, c = _setup(tmp_path, [])
        coll = c.get("/collections", headers=HX).get_data(as_text=True)
        assert _head(coll) == "0 runs, 0 types, 0 qubits"
