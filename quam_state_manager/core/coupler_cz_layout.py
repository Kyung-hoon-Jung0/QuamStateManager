"""The coupler-only CZ layouts: one home for their names (decisions D-1, D-15).

Two `pair_gate` modes build a fixed-frequency-qubit chip whose CZ is played on
a tunable coupler:

* ``cz_coupler`` -- the QSM extension layout: FixedFrequencyQuam root, the
  ``qsm_quam_ext`` pair and gate classes, macro ``cz_coupler``. Unchanged.
* ``cz_coupler_only`` -- the lab layout: a flux-tunable root whose qubits carry
  no flux line (``z`` null), the stock FluxTunableTransmonPair with a
  TunableCoupler, and the lab's own ``quam_config.coupler_cz_gate.CouplerCZGate``
  under macro ``cz_coupler_only``.

Pure (stdlib only). The build subprocess keeps a copy of these values
(``run_build._COUPLER_ONLY``), pinned equal by a test.
"""
from __future__ import annotations

import math

EXT_MODE = "cz_coupler"
MODE = "cz_coupler_only"
MODES = (EXT_MODE, MODE)

MACRO_KEY = "cz_coupler_only"
ALIAS_KEY = "cz"

# The lab classes this layout writes (probed in the selected env, never imported here).
LAB_GATE_MODULE = "quam_config.coupler_cz_gate"
LAB_GATE_CLASS = "CouplerCZGate"
LAB_SNZ_MODULE = "quam_config.two_flux_gate"
LAB_SNZ_CLASS = "SNZTwoFluxPulse"
FLATTOP_MODULE = "quam_builder.common.pulses"   # the flat-top class the lab writes

# The pulse shapes a pair may ask for (populate.pairs[<pair>].coupler_pulse_shape).
SHAPE_FLATTOP = "flattop"
SHAPE_SNZ = "snz"
SHAPES = (SHAPE_FLATTOP, SHAPE_SNZ)

# Placeholder values the coupler CZ nodes calibrate (V, ns), as the lab's own helper writes them.
DEFAULT_AMPLITUDE_V = 0.1
DEFAULT_FLAT_NS = 60
DEFAULT_RISE_NS = 8
DEFAULT_SNZ_PADDING_NS = 2

# Coupler LF-FEM port values for this layout.
COUPLER_SAMPLING_RATE = 1e9
COUPLER_UPSAMPLING_MODE = "pulse"
COUPLER_OUTPUT_MODE = "amplified"
COUPLER_FLUX_POINT = "off"


def pulse_key(control: str, target: str) -> str:
    """The coupler operation key (and pulse id) of a pair's coupler-only CZ."""
    return f"cz_coupler_only_pulse_{control}_{target}"


def flattop_length(flat_length: int) -> int:
    """Total flat-top length: flat + 2 rises, rounded up to whole 4 ns."""
    return int(math.ceil((int(flat_length) + 2 * DEFAULT_RISE_NS) / 4) * 4)


def is_coupler_only(spec: dict) -> bool:
    return str((spec or {}).get("pair_gate") or "").lower() == MODE


def pair_shapes(spec: dict) -> set[str]:
    """The pulse shapes this spec's pairs ask for (flat-top when unset)."""
    pops = ((spec or {}).get("populate") or {}).get("pairs") or {}
    shapes = {str((v or {}).get("coupler_pulse_shape") or SHAPE_FLATTOP).lower()
              for v in pops.values() if isinstance(v, dict)}
    if not shapes or len(pops) < len((spec or {}).get("qubit_pairs") or []):
        shapes.add(SHAPE_FLATTOP)
    return shapes
