"""One chip object, several names — the Live-Edit search follows the context.

Customer, on-site (2026-09-11):

    "제발 동일한 컨택스트의 키워드는 live edit에서 같이 검색되게 해주세요!!
     예를 들어, ro, readout 만 입력해도 resonator 도 당연히 함께 나와야하는데,
     지금은 resonator를 입력하면 readout이 검색 안되고 그 vice versa 임."

They are right, and the chip itself is the evidence. On the reporting lab's
5Q chip a qubit's readout object is literally named ``resonator``, its pulses
are named
``readout`` and ``readout_GEF``, and SM's own metric labels read
``"Readout frequency"`` with the blurb ``"Resonator frequency used to read the
qubit out."`` (``chip_health.py``). One object, three spellings, and a substring
search can reach only the spelling you happened to type — ``resonator`` and
``readout`` share no substring at all.

WHOLE WORDS, never substrings
-----------------------------
Membership is decided on TOKENS. ``ro`` occurs inside ``control``,
``crosstalk``, ``cross_resonance`` and ``phase_shift_control`` on a real chip,
so a substring test would quietly pull every control column into the readout
group — the search would get vaguer, which is the opposite of the request.

WHAT IS NOT HERE, AND WHY
-------------------------
``amp``/``amplitude`` and ``freq``/``frequency`` are NOT groups: one is a prefix
of the other, so ordinary substring matching already finds them, and adding them
would be pure noise in the haystack. The groups below are exactly the ones whose
members share no substring — which is what makes them unreachable today.

The words are curated, not derived: this is a small, explainable dictionary of
how this domain names the same thing twice, and every entry is checkable against
a real state.json. It is deliberately NOT a fuzzy matcher — docs/175 drew that
line for the typeahead and it holds here too. A suggestion may be approximate;
a query must not be.
"""
from __future__ import annotations

import re

# Each group is a set of names for ONE thing on the chip. Every member's
# haystack gains every other member, so any spelling finds all of them.
GROUPS: tuple[tuple[str, ...], ...] = (
    # The reported one. `resonator` is the object, `readout` is what it does
    # (and what its pulses are called), `ro`/`rr` are how the labels and node
    # names abbreviate it (`RO amp`, `RO len`, `f_ro` in chip_health.py).
    ("readout", "resonator", "ro", "rr"),
    # `qubit.xy` IS the drive line — quam names the channel, people say drive.
    ("xy", "drive"),
    # `qubit.z` IS the flux line. On a QDAC chip the DC bias sits elsewhere
    # (docs/136), which is why `bias` rides along: a user looking for "flux"
    # on such a chip is looking for the bias line.
    ("z", "flux", "bias"),
    # A tunable coupler is what a CZ is played on, and the wizard, the pair
    # grid and the node names use both words for the same pair surface.
    ("coupler", "cz"),
)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")

# name -> the other names in its group (the name itself is already in the text)
_INDEX: dict[str, tuple[str, ...]] = {}
for _g in GROUPS:
    for _w in _g:
        _INDEX[_w] = tuple(x for x in _g if x != _w)


def extra_terms(*texts: str) -> list[str]:
    """The words to ADD to a search haystack made of ``texts``.

    Returns the other names of every group this text already belongs to, in
    group order and without repeats. Empty when the text names nothing known —
    which is the common case, so this costs one tokenisation and no allocation
    on most columns.

    Tokenised, so ``phase_shift_control`` does not join the readout group on
    account of the ``ro`` inside ``control``.
    """
    seen: set[str] = set()
    out: list[str] = []
    for text in texts:
        if not text:
            continue
        for tok in _WORD.findall(str(text).lower()):
            for other in _INDEX.get(tok, ()):
                if other not in seen:
                    seen.add(other)
                    out.append(other)
    return out


def augment(*texts: str) -> str:
    """``extra_terms`` as one space-joined string, ready to append to a
    column's existing ``search`` text. ``""`` when nothing applies."""
    return " ".join(extra_terms(*texts))
