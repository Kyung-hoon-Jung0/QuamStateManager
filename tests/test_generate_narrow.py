"""Narrow Generate keeps actions and interpreter paths readable."""
from pathlib import Path
from tests.test_run_diff_narrow import _rule

WEB = Path(__file__).resolve().parents[1] / "quam_state_manager/web"


def test_generate_shrinks_and_wraps():
    css = (WEB / "static/ui-kit.css").read_text(encoding="utf-8")
    assert 'generate-root ui-generate' in (WEB / "templates/_generate.html").read_text(encoding="utf-8")
    assert "flex-wrap: wrap" in _rule(css, ".ui-generate .gen-header-actions")
    path = _rule(css, ".ui-generate #gen-env-list .gen-env-path")
    for prop in ("flex: 1 1 20ch", "overflow: visible", "white-space: normal", "overflow-wrap: anywhere"):
        assert prop in path
    assert "flex-wrap: wrap" in _rule(css, ".ui-generate #gen-env-list .gen-env-row")
    assert "min-width: 0" in _rule(css, ".ui-root-select.ui-select")
    assert "overflow-wrap: anywhere" in _rule(css, ".ui-root-value")
