"""The Pairs table never prints a non-volt detuning under a volt header;
the Bell mark thresholds are stated on the page; the empty Couplers page
says what defines a coupler; component-map ids are never dimmed by mode."""
import re
from pathlib import Path

from quam_state_manager.core import units

ROOT = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"


def test_a_volt_sized_detuning_renders_in_volts():
    text, doubt = units.pair_detuning_cell(0.0123)
    assert not doubt
    assert text == units.qty_filter(0.0123, "pair_detuning")


def test_a_frequency_sized_detuning_is_raw_and_flagged():
    text, doubt = units.pair_detuning_cell(540739600.0281938)
    assert doubt
    assert "540739600" in text


def test_none_and_bool_are_not_flagged():
    assert units.pair_detuning_cell(None) == ("-", False)
    assert units.pair_detuning_cell(True)[1] is False


def test_pairs_template_uses_the_doubt_aware_cell_and_states_bell_marks():
    s = (ROOT / "templates" / "_pairs.html").read_text(encoding="utf-8")
    assert "pair_detuning_cell" in s
    assert "qty('pair_detuning')" not in s
    assert "data-bell-legend" in s and "0.95" in s and "0.85" in s


def test_empty_couplers_names_the_defining_leaf():
    s = (ROOT / "templates" / "_couplers.html").read_text(encoding="utf-8")
    assert "qubit_pairs.&lt;pair&gt;.coupler" in s


def test_mode_highlights_never_dim_the_qubit_id():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    bad = re.findall(r"\.cm-hl-[a-z]+ \.cm-id[^{]*\{[^}]*opacity", css)
    assert not bad, bad
