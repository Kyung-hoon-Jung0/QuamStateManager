"""D-103 / M-1: a head-fold button only renders on a head that has something to fold."""
import pathlib, re

TPL = pathlib.Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "templates"
DATA_HEADS = ["_wiring", "_qubits", "_pairs", "_resonators", "_flux", "_couplers",
              "_qdac", "_instrument_wiring", "_cz_plan"]


def test_data_heads_carry_no_fold():
    for name in DATA_HEADS:
        assert "ui_head_fold" not in (TPL / f"{name}.html").read_text(encoding="utf-8"), name


def test_macro_folds_only_with_a_gist():
    src = (TPL / "_ui.html").read_text(encoding="utf-8")
    m = re.search(r"\{%- if ([^%]*)%\}\{\{ ui_head_fold\(\) \}\}", src)
    assert m and "gist" in m.group(1) and "not data" in m.group(1)


def test_map_fit_shrinks_to_the_floor():
    js = (TPL.parent / "static" / "component-map.js").read_text(encoding="utf-8")
    assert "if (s < floor) s = Math.min(1, floor);" in js
