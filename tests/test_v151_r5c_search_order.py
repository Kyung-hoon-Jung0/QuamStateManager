"""Header Search order within a score tier (R3B M4, R4 b2 and b9).

R3B M4 made a pointer row sort below every real value of its tier (the
+1000 tier of entries whose own path holds every word, or the rest), so
pointer rows cannot crowd the real values out of the first page. The review
found the sort-key line itself unpinned: reverting it kept every pin green.

The chip is the real QUAM shape of a qubit's pulses: x90's amplitude is a
pointer to x180's (``#../x180/amplitude``), its length a real value.

R4 b9: the tier was ``score // 1000``. Each word adds up to 100, so ten
words add up to a tier of their own and a pointer that matched every word
well jumped above a real value of the same tier.
"""
from __future__ import annotations

from quam_state_manager.core.search_index import SearchIndex

_STATE = {"qubits": {"q1": {"id": "q1", "xy": {"operations": {
    "x180": {"amplitude": 0.3, "length": 40, "amplitude_scale": 1.0},
    "x90": {"amplitude": "#../x180/amplitude", "length": 40}}}}}}


def _rows(query, limit=10):
    hits = SearchIndex.build(_STATE).search(query, limit=limit)
    return [(r.dot_path.split("operations.")[1], isinstance(r.raw_value, str)
             and r.raw_value.startswith("#")) for r in hits]


def test_a_pointer_sorts_below_a_real_value_of_its_tier():
    # both rows hold "x90" in their own path and score the same; the path
    # order alone would list the pointer (x90.amplitude) first
    assert _rows("x90") == [("x90.length", False), ("x90.amplitude", True)]
    assert _rows("x90", limit=1) == [("x90.length", False)]


def test_the_tier_holds_at_ten_or_more_words():
    # all three hold "amplitude" in their own path (one tier); the pointer
    # x90.amplitude scores 2000 at ten words, the real amplitude_scale 1700
    rows = _rows(" ".join(["amplitude"] * 10))
    assert rows == [("x180.amplitude", False), ("x180.amplitude_scale", False),
                    ("x90.amplitude", True)], rows
    assert rows == _rows(" ".join(["amplitude"] * 9))
