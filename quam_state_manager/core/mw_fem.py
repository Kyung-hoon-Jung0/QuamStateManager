"""OPX1000 MW-FEM band + LO-sharing model.

Per the QM docs (Guides/opx1000_fems): the MW-FEM up/downconverter LOs are shared
across fixed port pairs **per controller+FEM** — Out1↔In1, Out2↔Out3, Out4↔Out5,
Out6↔Out7, Out8↔In2. Coupled ports must use the **same band** (NOT the same
frequency); bands 1 and 3 are mutually compatible, band 2 is compatible only with
band 2. A port's up/downconverter frequency must lie within its band's Hz range.

This is the single source of truth for those constraints — both the server (to
attach per-cell LO metadata) and the client (live band-range warnings) read it.
Validation is **advisory** (warn, never hard-block) per the project's
trust-researcher-input philosophy.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# Inclusive Hz range per band (QM docs, user-confirmed).
BANDS: dict[int, tuple[float, float]] = {
    1: (50e6, 5.5e9),
    2: (4.5e9, 7.5e9),
    3: (6.5e9, 10.5e9),
}

# Max |intermediate frequency| an MW-FEM output can synthesize around its
# upconverter LO. The QM stack asserts |IF| < 400 MHz for pair-drive (CR/ZZ)
# channels (the customer's populate scripts enforce exactly this bound);
# advisory here per the module's warn-never-block philosophy.
MW_MAX_ABS_IF_HZ: float = 400e6

# LO-coupled OUTPUT port pairs within one (controller, FEM).
_OUT_PAIRS = {2: 3, 3: 2, 4: 5, 5: 4, 6: 7, 7: 6}


def in_band(freq: Any, band: Any) -> bool:
    """True if *freq* is within *band*'s range (or band/freq unknown — never a
    false alarm)."""
    rng = BANDS.get(band)
    if rng is None or not isinstance(freq, (int, float)) or isinstance(freq, bool):
        return True
    return rng[0] <= freq <= rng[1]


def bands_of(freq: Any) -> list[int]:
    """The band(s) whose range contains *freq* (bands overlap, so >1 possible)."""
    if not isinstance(freq, (int, float)) or isinstance(freq, bool):
        return []
    return [b for b, (lo, hi) in BANDS.items() if lo <= freq <= hi]


def bands_compatible(b1: Any, b2: Any) -> bool:
    """Two LO-coupled ports' bands are compatible iff equal, or {1, 3}."""
    if b1 == b2:
        return True
    return {b1, b2} == {1, 3}


def lo_peer(kind: str, port_id: int) -> Optional[tuple[str, int]]:
    """``(peer_kind, peer_port_id)`` of the LO-coupled port within the same FEM,
    or ``None`` (e.g. an LF-FEM port or an unpaired id)."""
    if kind == "mw_outputs":
        if port_id in _OUT_PAIRS:
            return ("mw_outputs", _OUT_PAIRS[port_id])
        if port_id == 1:
            return ("mw_inputs", 1)
        if port_id == 8:
            return ("mw_inputs", 2)
    elif kind == "mw_inputs":
        if port_id == 1:
            return ("mw_outputs", 1)
        if port_id == 2:
            return ("mw_outputs", 8)
    return None


def freq_field(kind: str) -> str:
    """The frequency leaf field for a port *kind*."""
    return "downconverter_frequency" if kind == "mw_inputs" else "upconverter_frequency"


_PORT_RE = re.compile(r"^ports\.(mw_outputs|mw_inputs)\.([^.]+)\.([^.]+)\.([^.]+)(?:\.(.+))?$")


def port_of_resolved(resolved_path: Any) -> Optional[tuple[str, str, int, int, str]]:
    """Parse ``ports.mw_outputs.con1.1.2.band`` → ``(kind, controller, fem, port, field)``.

    Returns ``None`` for a non-MW-port path. fem/port are ints when numeric.
    """
    if not isinstance(resolved_path, str):
        return None
    m = _PORT_RE.match(resolved_path)
    if not m:
        return None
    kind, con, fem, port, field = m.group(1), m.group(2), m.group(3), m.group(4), (m.group(5) or "")

    def _int(x: str):
        try:
            return int(x)
        except (TypeError, ValueError):
            return x

    return (kind, con, _int(fem), _int(port), field)
