"""The run diff stacks its cells on phones and prefers dot and slash breaks.

Numbers stay whole, unstructured tokens retain an emergency wrap, and the
class row says it has no numeric delta. CSS pins complement the jsdom resize
selfcheck; actual rendering is left to the next Chrome round.
"""
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


def test_the_existing_scroll_shell_contains_the_responsive_table(body):
    assert re.search(
        r'<div class="ui-xscroll">\s*<table class="ui-table diff-wb-list ui-run-diff" data-run-diff>', body)


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
    assert '<td class="diff-list-a"><span class="muted">(absent)</span>' in _row(body, "only_b")
    assert '<td class="diff-list-b"><span class="muted">(absent)</span>' in _row(body, "only_a")
    assert "x" * 60 in _row(body, ">label<")


def test_path_breaks_filter_keeps_numbers_whole():
    app = create_app(testing=True)
    f = app.jinja_env.filters["path_breaks"]
    assert str(f("1.5e-3")) == '<span class="ui-cell-nowrap">1.5e-3</span>'
    assert str(f("-0.25")) == '<span class="ui-cell-nowrap">-0.25</span>'
    assert "<wbr>" not in str(f("123,456,789,012"))
    assert str(f("a/b.c")) == '<span class="ui-dot-name">a/<wbr>b.<wbr>c</span>'
    assert str(f("<x>.y")) == '<span class="ui-dot-name">&lt;x&gt;.<wbr>y</span>'


def _rule(css, sel):
    # The selector as written, never the tail of a longer one.
    m = re.search(r"(?<![\w.#-])" + re.escape(sel) + r"\s*\{([^}]*)\}", css)
    assert m, sel
    return m.group(1)


def test_css_contract():
    style = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    kit = (ROOT / "static" / "ui-kit.css").read_text(encoding="utf-8")
    for sel in (".diff-list-path", ".diff-list-a, .diff-list-b"):
        body = _rule(style, sel)
        # break-all would split every word; "anywhere" is the long-token
        # fallback that keeps a 200 character hash from widening the table.
        assert "break-all" not in body and "overflow-wrap: anywhere" in body, sel
        assert re.search(r"min-width:\s*\d+(ch|rem)", body), sel
    assert "overflow-x: auto" in _rule(kit, ".ui-xscroll")
    assert "nowrap" in _rule(kit, ".ui-cell-nowrap")
    assert "normal" in _rule(kit, ".ui-cell-nowrap > .ui-cell-note")


def test_a_long_token_and_numbers_in_the_value_cells(tmp_path):
    """A 200 character token with no joint relies on the CSS fallback (no
    <wbr> is invented); a number cell is marked nowrap so it never splits."""
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    tok = "Z" * 200
    a = _state_dir(tmp_path, "a", "c.A", 123456789012, tok)
    b = _state_dir(tmp_path, "b", "c.A", 1, "ab")
    r = app.test_client().get(f"/diff?a=run:{a}&b=run:{b}&tab=state&view=list",
                              headers={"HX-Request": "true"})
    body = r.get_data(as_text=True)
    assert tok in _row(body, ">label<")
    row = _row(body, ">length<")
    assert '<code class="ui-cell-nowrap">123,456,789,012</code>' in row
    assert '<code class="ui-cell-nowrap">1</code>' in row
    assert "<code>ab</code>" in _row(body, ">label<")


def test_joint_wrapper_and_phone_stack(body):
    app = create_app(testing=True)
    f = app.jinja_env.filters["path_breaks"]
    assert str(f("UndottedClass")) == "UndottedClass"
    assert str(f("quam.components.SquarePulse")) == '<span class="ui-dot-name">quam.<wbr>components.<wbr>SquarePulse</span>'
    kit = (ROOT / "static/ui-kit.css").read_text(encoding="utf-8")
    joint = _rule(kit, ".ui-dot-name")
    assert "word-break: normal" in joint and "overflow-wrap: normal" in joint
    phone = kit
    assert "display: block" in _rule(phone, ".ui-run-diff-phone, .ui-run-diff-phone tbody, .ui-run-diff-phone tr, .ui-run-diff-phone td")
    assert "min-width: 0" in _rule(kit, ".ui-run-diff-compact .diff-list-path, .ui-run-diff-compact .diff-list-a, .ui-run-diff-compact .diff-list-b")
    for name in ("a", "b", "delta"):
        assert "content:" in _rule(phone, f".ui-run-diff-phone .diff-list-{name}::before")
    assert 'diff-wb-list ui-run-diff' in body


def test_compact_table_bounds_and_long_segments():
    kit = (ROOT / "static/ui-kit.css").read_text(encoding="utf-8")
    compact = _rule(kit, ".ui-run-diff.ui-run-diff-compact")
    assert "table-layout: fixed" in compact and "width: 100%" in compact
    assert "overflow-wrap: anywhere" in _rule(kit, ".ui-run-diff-compact .ui-dot-name")
    delta = _rule(kit, ".ui-run-diff-compact .diff-list-delta, .ui-run-diff-compact .diff-list-delta .val-delta")
    assert "white-space: normal" in delta and "overflow-wrap: anywhere" in delta
    assert "width: 2ch" in _rule(kit, ".ui-run-diff-compact th:nth-child(3), .ui-run-diff-compact .diff-list-arrow")
    assert "white-space: normal" in _rule(kit, ".ui-run-diff-compact .diff-list-take")
    app = create_app(testing=True)
    value = "module." + "LongSegment" * 20
    assert str(app.jinja_env.filters["path_breaks"](value)) == '<span class="ui-dot-name">module.<wbr>' + "LongSegment" * 20 + '</span>'


_HIDING = re.compile(
    r"(display\s*:\s*none|visibility\s*:\s*(hidden|collapse)"
    r"|(^|;)\s*(max-)?(width|height)\s*:\s*0(px|ch|em|rem|%)?\s*(;|$|!)"
    r"|(^|;)\s*clip(-path)?\s*:\s*(?!auto|none)|(^|;)\s*opacity\s*:\s*0\s*(;|$|!)"
    r"|(^|;)\s*font-size\s*:\s*0(px)?\s*(;|$|!))", re.I)


def _hiding_selectors(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    found = []
    for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if _HIDING.search(declarations.strip()):
            found.extend(" ".join(s.split()) for s in selectors.split(",") if s.strip())
    return found


def _cells():
    tpl = (ROOT / "templates/_diff_workbench.html").read_text(encoding="utf-8")
    table = tpl.split("data-run-diff>", 1)[1].split("</table>", 1)[0]
    cells = set(re.findall(r'<td class="(diff-list-[a-z]+)', table))
    assert {"diff-list-a", "diff-list-b", "diff-list-delta"} <= cells
    return cells


def test_phone_only_hides_header_and_arrow():
    """No rule, in or out of a media query, under any selector prefix, hides
    the A, B or delta cell (or the whole table); only the header row and the
    arrow column are hidden, and only under the phone class."""
    kit = (ROOT / "static/ui-kit.css").read_text(encoding="utf-8")
    keep = _cells() - {"diff-list-arrow"}
    allowed = {".ui-run-diff-phone thead", ".ui-run-diff-phone .diff-list-arrow"}
    mine = [s for s in _hiding_selectors(kit)
            if re.search(r"ui-run-diff(?![-\w])", s) or any(c in s for c in keep)
            or re.search(r"ui-run-diff-(phone|compact)", s)]
    assert sorted(set(mine) - allowed) == [], mine
    assert allowed <= set(mine)
    assert {"diff-list-a", "diff-list-b", "diff-list-delta"} <= keep


def test_hiding_scan_catches_the_holes():
    bad = ["@media (max-width: 480px) { .diff-list-delta { display: none; } }",
           ".diff-list-b { visibility: hidden; }",
           ".ui-run-diff .diff-list-a { width: 0; }",
           ".ui-run-diff-compact .diff-list-delta { display:none }",
           ".diff-list-delta { DISPLAY: NONE !important; }"]
    for css in bad:
        assert _hiding_selectors(css), css
    good = ["/* .diff-list-delta { display: none; } */",
            ".diff-list-delta { display: block; }", ".diff-list-a { display: grid; }",
            ".diff-list-a { width: 20ch; }"]
    for css in good:
        assert _hiding_selectors(css) == [], css
