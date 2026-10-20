"""Explicit unresolved pairs cannot select a different plan."""
import pytest
from tests.test_cz_plan_page import _client


@pytest.mark.parametrize("pair", ["no-such-pair", '<img src=x onerror="alert(1)">', ""])
def test_unknown_pair_selects_no_operation(tmp_path, pair):
    c = _client(tmp_path)
    html = c.get("/cz-plan", query_string={"pair": pair}, headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'data-selected=""' in html
    assert "Unknown pair:" in html and "No operation selected." in html
    assert 'aria-selected="true"' not in html
    assert '<img src=x' not in html
    if pair.startswith("<"):
        assert "&lt;img" in html
