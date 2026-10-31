"""D-101: a coupler is a component -- every map draws it clearly and no
highlight mode dims it as context; R4-WC-2: tiny rack labels are hidden."""
import pathlib
import re

_STATIC = pathlib.Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"


def test_no_highlight_mode_dims_the_coupler():
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    for m in re.finditer(r"([^{}]*)\{([^}]*)\}", css):
        sel, body = m.group(1), m.group(2)
        if "opacity" in body and ".cm-coupler" in sel:
            for part in sel.split(","):
                if ".cm-coupler" in part and ".cm-hl-" in part:
                    assert "cm-hl-couplers" in part, part.strip()


def test_one_coupler_radius_for_drawing_and_clearance():
    js = (_STATIC / "topo-graph.js").read_text(encoding="utf-8")
    assert "function couplerR(R)" in js
    assert "R * 0.38" not in js


def test_the_rack_hides_labels_below_nine_px():
    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "_INSTR_MIN_LABEL_PX = 9" in js
    assert "_instrHideTinyLabels(svg, w);" in js
