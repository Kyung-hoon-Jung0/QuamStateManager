"""D-104: the env-upgrade banner names what its number counts (fields and
classes, separately) and lists them in a disclosure."""
from pathlib import Path

import jinja2

TPL = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "templates"


def _render(cases):
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TPL)))
    env.globals["asset_url"] = lambda n: n
    return env.get_template("_env_upgrade_banner.html").render(
        upgrade={"report": {"event": {"direction": "upgrade"}, "cases": cases}})


def test_two_fields_and_one_class():
    html = _render({"2": {"rows": [{"class": "a.B", "field": "x"}]},
                    "3": {"rows": [{"class": "a.B", "field": "y",
                                    "candidates": [{"field": "y2"}]}]},
                    "4": {"rows": [{"class": "a.C"}]}})
    assert "2 fields and 1 class your chip uses no longer load." in html
    assert "finding" not in html
    assert html.count("<li>") == 3
    assert "a.B.x — now required" in html
    assert "a.B.y — no longer declared (maybe renamed to y2)" in html
    assert "a.C — no longer imports" in html


def test_only_classes():
    html = _render({"4": {"rows": [{"class": "a.C"}, {"class": "a.D"}]}})
    assert "2 classes your chip uses no longer load." in html
    assert "field" not in html.split("no longer load")[0]
