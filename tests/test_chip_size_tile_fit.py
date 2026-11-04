"""F26 (final walk): the Overview "Chip Size" value ("21 qubits, 32 pairs",
19 characters) must fit its tile at narrow widths. Its font size scales with
the card (cqi); 19 bold characters at ~0.6em each must stay under 100% of
the card width, with an ellipsis as the safety net."""
import os
import re

CSS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "quam_state_manager", "web", "static", "style.css")
CHARS = 19
EM_PER_CHAR = 0.6


def _rule():
    text = open(CSS, encoding="utf-8").read()
    m = re.search(r'\.topo-card\[data-tile-id="chip_size"\]\s*\.topo-card-value\s*\{([^}]*)\}', text)
    assert m, "chip_size value rule missing"
    return m.group(1)


def test_the_chip_size_value_fits_its_card():
    body = _rule()
    m = re.search(r"([\d.]+)cqi", body)
    assert m, "chip_size font size no longer scales with the card"
    assert float(m.group(1)) * CHARS * EM_PER_CHAR <= 100.0


def test_the_chip_size_value_clips_with_an_ellipsis():
    body = _rule()
    assert re.search(r"overflow\s*:\s*hidden", body)
    assert re.search(r"text-overflow\s*:\s*ellipsis", body)
