"""The run diff list stays readable at narrow widths: the table scrolls inside
its own box, dotted names and paths break only at "." and "/", a number never
splits, and the class row says it has no numeric delta.

jsdom has no layout, so this pins the CSS contract and the markup that give
the behaviour; the real-width check is a headless Chrome screenshot round."""
import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"


def _state_dir(root: Path, name: str, cls: str, length, label: str) -> str:
    d = root / name / "quam_state"
    d.mkdir(parents=True)
    d.joinpath("state.json").write_text(json.dumps({"qubits": {"q1": {
        "xy": {"operations": {"sat": {"__class__": cls, "length": length}}},
        "label": label, "only_a" if name == "a" else "only_b": -0.25}}}), encoding="utf-8")
    d.joinpath("wiring.json").write_text("{}", encoding="utf-8")
    return str(d)


@pytest.fixture
def body(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    a = _state_dir(tmp_path, "a", "quam.components.pulses.SquarePulse", 20000, "x" * 60)
    b = _state_dir(tmp_path, "b", "quam.components.pulses.GaussianPulse", 2000, "y" * 60)
    r = app.test_client().get(f"/diff?a=run:{a}&b=run:{b}&tab=state&view=list",
                              headers={"HX-Request": "true"})
    assert r.status_code == 200
    return r.get_data(as_text=True)


def _row(body, key):
    rows = body.split("<tbody>", 1)[1].split("</tbody>", 1)[0].split("</tr>")
    hit = [r for r in rows if key in r]
    assert hit, key
    return hit[0]


def test_the_table_scrolls_inside_its_own_box(body):
    assert re.search(r'<div class="ui-xscroll">\s*<table class="diff-wb-list">', body)


def test_dotted_names_break_only_at_their_dots(body):
    row = _row(body, "__class__")
    assert "qubits.<wbr>q1.<wbr>xy.<wbr>operations.<wbr>sat.<wbr>__class__" in row
    assert "quam.<wbr>components.<wbr>pulses.<wbr>SquarePulse" in row
    assert "__class_<" not in row


def test_the_class_row_says_it_has_no_numeric_delta(body):
    row = _row(body, "__class__")
    assert '<td class="diff-list-delta ui-cell-nowrap"><span class="ui-cell-note">class changed, no numeric delta' in row
    assert "val-delta" not in row


def test_numbers_are_never_broken_and_keep_their_delta(body):
    row = _row(body, ">length<")
    assert "20,000" in row and "2,000" in row and "<wbr>2" not in row.split("diff-list-a", 1)[1]
    assert "val-delta" in row and "class changed" not in row


def test_one_sided_and_long_rows_render(body):
    assert "(absent)" in _row(body, "only_a") and "-0.25" in _row(body, "only_a")
    assert "x" * 60 in _row(body, ">label<")


def test_path_breaks_filter_keeps_numbers_whole():
    app = create_app(testing=True)
    f = app.jinja_env.filters["path_breaks"]
    assert str(f("1.5e-3")) == "1.5e-3"
    assert str(f("-0.25")) == "-0.25"
    assert str(f("a/b.c")) == "a/<wbr>b.<wbr>c"
    assert str(f("<x>.y")) == "&lt;x&gt;.<wbr>y"


def _rule(css, sel):
    m = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", css)
    assert m, sel
    return m.group(1)


def test_css_contract():
    style = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    kit = (ROOT / "static" / "ui-kit.css").read_text(encoding="utf-8")
    for sel in (".diff-list-path", ".diff-list-a, .diff-list-b"):
        body = _rule(style, sel)
        assert "break-all" not in body and "anywhere" not in body, sel
    assert "overflow-x: auto" in _rule(kit, ".ui-xscroll")
    assert "nowrap" in _rule(kit, ".ui-cell-nowrap")
    assert "normal" in _rule(kit, ".ui-cell-nowrap > .ui-cell-note")
