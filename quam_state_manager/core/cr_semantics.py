"""Flavor-tolerant semantics for cross-resonance (CR) and ZZ-drive pair gates.

The quam-builder CR/ZZ schema exists in (at least) three generations, and real
chips from all of them must render and edit correctly:

- ``lo_if``    — quam-builder @ a08bf66 (the envs' installed commit): the CR
  channel stores ``target_qubit_LO_frequency`` + ``target_qubit_IF_frequency``
  literals plus ``bell_state_fidelity``; calibration levers
  (``drive/cancel_amplitude_scaling``, ``drive/cancel_phase``,
  ``qc/qt_correction_phase``) live ON the channel; the pair's ZZ field is
  ``zz_drive``.
- ``rf``       — quam-builder ``feat/add-cr-cz-macros`` @ fa540b6 (the flavor of
  every customer artifact in hand): the channel stores a single
  ``target_qubit_RF_frequency`` (usually an absolute pointer to the target's
  ``xy.RF_frequency``); levers still on the channel; ZZ field ``zz_drive``;
  qubits may be ``FixedFrequencyZZDriveTransmon``.
- ``rf_drive`` — the branch tip (c119d62): channel classes renamed to
  ``CrossResonanceDriveMW/IQ`` (module ``components/cross_resonance_drive``),
  the channel keeps ONLY ``target_qubit_RF_frequency`` (levers move off-channel,
  onto the CRGate macro), and the pair's ZZ field renames ``zz_drive`` → ``zz``.
  Provisional: no state artifact from the tip exists yet — detection claims this
  flavor ONLY on class-module evidence, never on key absence (a sparsely
  serialized ``rf`` chip omits lever keys too).

Everything here is **key-driven, never version-driven**: accessors read what is
actually in the JSON. Pure functions over plain dicts (state may come from a
live :class:`QuamStore`, a Param-History snapshot, or a compare source) — no
Flask, no store import; only :func:`effective_frequencies` takes a store, for
its per-instance pointer cache. Non-CR chips pay one cheap ``is_cr_chip`` scan
and nothing else.

Design record: ``docs/54_cr_integration.md``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from quam_state_manager.core.mw_fem import MW_MAX_ABS_IF_HZ, bands_of, in_band
from quam_state_manager.core.pointer_resolver import is_pointer, is_self_ref

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flavor identifiers
# ---------------------------------------------------------------------------

FLAVOR_NONE = "none"          # not a CR pair / chip
FLAVOR_LO_IF = "lo_if"        # (A) a08bf66: target LO+IF literals, bell_state_fidelity
FLAVOR_RF = "rf"              # (B) fa540b6: target_qubit_RF_frequency, levers on channel
FLAVOR_RF_DRIVE = "rf_drive"  # (C) tip: CrossResonanceDrive* classes, levers off-channel
FLAVOR_UNKNOWN = "unknown"    # CR content present but signals contradict / none match

# Pair-channel key vocabulary (stable `cross_resonance`; `zz_drive` → `zz` at tip).
CR_CHANNEL_KEYS: tuple[str, ...] = ("cross_resonance",)
ZZ_CHANNEL_KEYS: tuple[str, ...] = ("zz_drive", "zz")
XY_DETUNED_KEY = "xy_detuned"

# ---------------------------------------------------------------------------
# Class-name tolerance registry (mirrors pulse_catalog._EXTRA_HOMES/_QCLASS_ALIASES:
# exact known paths first, leaf-name fallback second — "leaf-match renders,
# exact-match trusts"). Per-path provenance lives in docs/54_cr_integration.md.
# ---------------------------------------------------------------------------

_COMP = "quam_builder.architecture.superconducting.components."
_GATES_FIX = "quam_builder.architecture.superconducting.custom_gates.fixed_transmon_pair.two_qubit_gates."
_GATES_FTC = "quam_builder.architecture.superconducting.custom_gates.flux_tunable_transmon_pair.two_qubit_gates."
_GATES_TOP = "quam_builder.architecture.superconducting.custom_gates."
_QUBIT_FF = "quam_builder.architecture.superconducting.qubit.fixed_frequency_transmon."

CR_CHANNEL_CLASSES: dict[str, str] = {
    _COMP + "cross_resonance.CrossResonanceMW": "mw",
    _COMP + "cross_resonance.CrossResonanceIQ": "iq",
    _COMP + "cross_resonance_drive.CrossResonanceDriveMW": "mw",   # tip c119d62
    _COMP + "cross_resonance_drive.CrossResonanceDriveIQ": "iq",
}
ZZ_CHANNEL_CLASSES: dict[str, str] = {
    _COMP + "zz_drive.ZZDriveMW": "mw",
    _COMP + "zz_drive.ZZDriveIQ": "iq",
}
CR_GATE_CLASSES: tuple[str, ...] = (
    _GATES_FIX + "CRGate", _GATES_FTC + "CRGate", _GATES_TOP + "CRGate",
)
STARK_CZ_CLASSES: tuple[str, ...] = (
    _GATES_FIX + "StarkInducedCZGate", _GATES_TOP + "StarkInducedCZGate",
)
CR_QUBIT_CLASSES: tuple[str, ...] = (
    _QUBIT_FF + "FixedFrequencyTransmon",
    _QUBIT_FF + "FixedFrequencyZZDriveTransmon",
)
CR_PAIR_CLASSES: tuple[str, ...] = (
    "quam_builder.architecture.superconducting.qubit_pair."
    "fixed_frequency_transmon_pair.FixedFrequencyTransmonPair",
)

# leaf-name -> kind (fallback when the module prefix is unknown/churned)
_LEAF_KINDS: tuple[tuple[str, str], ...] = (
    ("CrossResonanceDriveMW", "cr_channel_mw"),
    ("CrossResonanceDriveIQ", "cr_channel_iq"),
    ("CrossResonanceMW", "cr_channel_mw"),
    ("CrossResonanceIQ", "cr_channel_iq"),
    ("ZZDriveMW", "zz_channel_mw"),
    ("ZZDriveIQ", "zz_channel_iq"),
    ("StarkInducedCZGate", "stark_cz_gate"),   # before CZGate (substring overlap)
    ("CRGate", "cr_gate"),
    ("CZGate", "cz_gate"),
)


def classify_class(qclass: Any) -> tuple[str | None, str | None]:
    """Classify a ``__class__`` string → ``(kind, how)``.

    ``kind`` ∈ ``{"cr_channel_mw", "cr_channel_iq", "zz_channel_mw",
    "zz_channel_iq", "cr_gate", "stark_cz_gate", "cz_gate", None}``;
    ``how`` ∈ ``{"exact", "leaf", None}``. Exact paths are the registry above;
    leaf matching tolerates module churn (the branch renames modules faster
    than we can pin them).
    """
    if not isinstance(qclass, str) or not qclass:
        return (None, None)
    if qclass in CR_CHANNEL_CLASSES:
        return ("cr_channel_" + CR_CHANNEL_CLASSES[qclass], "exact")
    if qclass in ZZ_CHANNEL_CLASSES:
        return ("zz_channel_" + ZZ_CHANNEL_CLASSES[qclass], "exact")
    if qclass in CR_GATE_CLASSES:
        return ("cr_gate", "exact")
    if qclass in STARK_CZ_CLASSES:
        return ("stark_cz_gate", "exact")
    leaf = qclass.rsplit(".", 1)[-1]
    for leaf_name, kind in _LEAF_KINDS:
        if leaf == leaf_name:
            return (kind, "leaf")
    return (None, None)


# ---------------------------------------------------------------------------
# Channel / macro accessors
# ---------------------------------------------------------------------------

def cr_channel(pair_obj: Any) -> dict | None:
    """The pair's CR drive channel dict, or ``None``.

    Explicit-null and missing-key both → ``None`` (real chips serialize both
    ways; ``dict.get`` default only covers the missing-key case).
    """
    if not isinstance(pair_obj, dict):
        return None
    for key in CR_CHANNEL_KEYS:
        ch = pair_obj.get(key)
        if isinstance(ch, dict):
            return ch
    return None


def zz_channel(pair_obj: Any) -> tuple[str, dict] | None:
    """``(key_used, channel_dict)`` for the pair's ZZ drive, or ``None``.

    ``key_used`` matters: lever paths and pulse enumeration must emit the REAL
    path segment (``zz_drive`` on a08bf66/fa540b6 chips, ``zz`` at the tip).
    """
    if not isinstance(pair_obj, dict):
        return None
    for key in ZZ_CHANNEL_KEYS:
        ch = pair_obj.get(key)
        if isinstance(ch, dict):
            return (key, ch)
    return None


def xy_detuned_channel(pair_obj: Any) -> dict | None:
    """The pair-level ``xy_detuned`` channel dict, or ``None``.

    (The target qubit may also carry one — that lives under ``qubits``, not
    here; this accessor is for the pair-level slot some builder generations
    serialize.)
    """
    if not isinstance(pair_obj, dict):
        return None
    ch = pair_obj.get(XY_DETUNED_KEY)
    return ch if isinstance(ch, dict) else None


def cr_gate_macro(pair_obj: Any) -> tuple[str, dict] | None:
    """``(macro_name, macro_dict)`` of the pair's CR gate macro, or ``None``.

    Prefers ``macros["cr"]``; otherwise the first macro whose ``__class__``
    classifies as a CR gate. Follows a ``"#./<name>"`` alias one hop (the same
    idiom the CZ family uses for ``macros.cz → cz_unipolar``).
    """
    if not isinstance(pair_obj, dict):
        return None
    macros = pair_obj.get("macros")
    if not isinstance(macros, dict):
        return None

    def _deref(name: str, value: Any, hops: int = 0) -> tuple[str, dict] | None:
        if isinstance(value, str) and value.startswith("#./") and hops == 0:
            target = value[3:].split("/")[0]
            return _deref(target, macros.get(target), hops=1)
        if isinstance(value, dict):
            return (name, value)
        return None

    hit = _deref("cr", macros.get("cr"))
    if hit is not None:
        # A macro merely NAMED "cr" but positively CZ-classified/CZ-shaped is
        # not a CR gate (guards the false-positive pin in the test corpus).
        kind, _how = classify_class(hit[1].get("__class__"))
        if kind not in ("cz_gate",) and not is_cz_shaped_macro(hit[1]):
            return hit
    for name, value in macros.items():
        got = _deref(name, value)
        if got is None:
            continue
        kind, _how = classify_class(got[1].get("__class__"))
        if kind in ("cr_gate", "stark_cz_gate"):
            return got
    return None


_FLUX_SHAPED_KEYS = (
    "flux_pulse_qubit", "coupler_flux_pulse",
    "phase_shift_control", "phase_shift_target",
)


def is_cz_shaped_macro(gate: Any) -> bool:
    """Does this macro have the flux-CZ *shape* (its calibration fields)?

    Detection is by key PRESENCE, not value: an uncalibrated CZ gate carries
    the keys set to null and must still render. ``fidelity`` is deliberately
    NOT in the presence set — modern CRGate macros carry a (null) ``fidelity``
    field, which used to trip this check and render a phantom all-None
    CZ-shaped "Cr" section whose Apply always 400'd. A class that positively
    classifies as CR/Stark-CZ is never CZ-shaped, whatever keys it grows.
    """
    if not isinstance(gate, dict):
        return False
    kind, _how = classify_class(gate.get("__class__"))
    if kind in ("cr_gate", "stark_cz_gate"):
        return False
    return any(k in gate for k in _FLUX_SHAPED_KEYS)


# ---------------------------------------------------------------------------
# Flavor detection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CrFlavorReport:
    flavor: str                                        # chip-level majority
    mixed: bool                                        # pairs disagree
    per_pair: dict[str, str] = field(default_factory=dict)
    signals: tuple[tuple[str, str, str], ...] = ()     # (signal, dot_path, value)


_LEVER_KEYS = (
    "drive_amplitude_scaling", "drive_phase",
    "cancel_amplitude_scaling", "cancel_phase",
    "qc_correction_phase", "qt_correction_phase",
)


def detect_pair_flavor(pair_obj: Any) -> tuple[str, list[tuple[str, str, str]]]:
    """One pair's CR flavor + the evidence signals that decided it.

    Precedence: Drive* class module → LO/IF keys → RF(+levers) → RF-sparse.
    Sparse serialization (CR_state omits null keys entirely) must read as
    ``rf``, never ``rf_drive`` — only class-module evidence claims the tip.
    """
    chan = cr_channel(pair_obj)
    if chan is None:
        # A CR gate macro without a channel is still CR content, flavor unknown.
        if isinstance(pair_obj, dict) and cr_gate_macro(pair_obj) is not None:
            return (FLAVOR_UNKNOWN, [("cr_macro_only", "macros", "")])
        return (FLAVOR_NONE, [])

    signals: list[tuple[str, str, str]] = []
    qclass = chan.get("__class__")
    kind, how = classify_class(qclass)
    is_drive_cls = (
        isinstance(qclass, str)
        and (".cross_resonance_drive." in qclass
             or qclass.rsplit(".", 1)[-1].startswith("CrossResonanceDrive"))
    )
    if is_drive_cls:
        signals.append(("drive_class", "cross_resonance.__class__", str(qclass)))
        return (FLAVOR_RF_DRIVE, signals)

    if any(k in chan for k in
           ("target_qubit_LO_frequency", "target_qubit_IF_frequency",
            "bell_state_fidelity")):
        present = [k for k in ("target_qubit_LO_frequency",
                               "target_qubit_IF_frequency", "bell_state_fidelity")
                   if k in chan]
        signals.append(("lo_if_keys", "cross_resonance", ",".join(present)))
        return (FLAVOR_LO_IF, signals)

    if "target_qubit_RF_frequency" in chan:
        levers = [k for k in _LEVER_KEYS if k in chan]
        signals.append(("rf_key", "cross_resonance.target_qubit_RF_frequency",
                        str(chan.get("target_qubit_RF_frequency"))))
        if levers:
            signals.append(("channel_levers", "cross_resonance", ",".join(levers)))
        return (FLAVOR_RF, signals)

    if kind is not None:
        signals.append(("cr_class_only", "cross_resonance.__class__", str(qclass)))
    return (FLAVOR_UNKNOWN, signals)


def detect_flavor(merged: Any) -> CrFlavorReport:
    """Chip-level flavor report: per-pair verdicts, majority, mixed flag."""
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    if not isinstance(pairs, dict) or not pairs:
        return CrFlavorReport(FLAVOR_NONE, False)

    per_pair: dict[str, str] = {}
    all_signals: list[tuple[str, str, str]] = []
    for pid, pobj in pairs.items():
        flavor, signals = detect_pair_flavor(pobj)
        if flavor == FLAVOR_NONE:
            continue
        per_pair[pid] = flavor
        for sig, path, val in signals[:2]:   # cap evidence per pair
            all_signals.append((sig, f"qubit_pairs.{pid}.{path}", val))

    if not per_pair:
        return CrFlavorReport(FLAVOR_NONE, False)

    counts: dict[str, int] = {}
    for fl in per_pair.values():
        counts[fl] = counts.get(fl, 0) + 1
    # Majority; deterministic tie-break by (count, flavor-name) like chip_qclass.
    majority = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    distinct = {fl for fl in per_pair.values() if fl != FLAVOR_UNKNOWN}
    mixed = len(distinct) > 1
    return CrFlavorReport(majority, mixed, per_pair, tuple(all_signals))


def is_cr_chip(merged: Any) -> bool:
    """Cheap gate: does any pair carry CR content (channel or CR-class macro)?

    This is the early-exit every consumer calls first — CZ chips pay one
    O(pairs) scan of dict lookups and nothing else.
    """
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    if not isinstance(pairs, dict):
        return False
    for pobj in pairs.values():
        if cr_channel(pobj) is not None:
            return True
        if isinstance(pobj, dict) and cr_gate_macro(pobj) is not None:
            return True
    return False


# ---------------------------------------------------------------------------
# Fidelity
# ---------------------------------------------------------------------------

def _num(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def fidelity(pair_obj: Any) -> dict | None:
    """Canonical 2Q fidelity: macro ladder first, channel fallback.

    Returns ``{"value", "source": "macro"|"channel", "gate", "clifford",
    "path_suffix"}`` (``path_suffix`` relative to ``qubit_pairs.<pid>.``) or
    ``None``. Macro-first is the binding decision (docs/54): the CRGate macro's
    ``fidelity`` field exists in every builder generation, while
    ``bell_state_fidelity`` on the channel exists only in the ``lo_if`` flavor.
    Ladder shapes mirror ``compare.canonical_pair_fidelity`` +
    ``query.get_topology``: ``StandardRB.average_gate_fidelity`` → bare
    ``StandardRB`` float (Clifford) → ``Bell_State.Fidelity`` → bare float.
    """
    hit = cr_gate_macro(pair_obj)
    if hit is not None:
        gate_name, gate = hit
        fid = gate.get("fidelity")
        base = f"macros.{gate_name}.fidelity"
        if isinstance(fid, dict):
            srb = fid.get("StandardRB")
            if isinstance(srb, dict) and _num(srb.get("average_gate_fidelity")) is not None:
                return {"value": srb["average_gate_fidelity"], "source": "macro",
                        "gate": gate_name, "clifford": False,
                        "path_suffix": base + ".StandardRB.average_gate_fidelity"}
            if _num(srb) is not None:
                return {"value": srb, "source": "macro", "gate": gate_name,
                        "clifford": True,          # bare float = Clifford fidelity
                        "path_suffix": base + ".StandardRB"}
            bell = fid.get("Bell_State")
            if isinstance(bell, dict) and _num(bell.get("Fidelity")) is not None:
                return {"value": bell["Fidelity"], "source": "macro",
                        "gate": gate_name, "clifford": False,
                        "path_suffix": base + ".Bell_State.Fidelity"}
        elif _num(fid) is not None:
            return {"value": fid, "source": "macro", "gate": gate_name,
                    "clifford": False, "path_suffix": base}

    chan = cr_channel(pair_obj)
    if chan is not None and _num(chan.get("bell_state_fidelity")) is not None:
        return {"value": chan["bell_state_fidelity"], "source": "channel",
                "gate": None, "clifford": False,
                "path_suffix": "cross_resonance.bell_state_fidelity"}
    return None


# ---------------------------------------------------------------------------
# Lever map
# ---------------------------------------------------------------------------

def lever_map(pair_obj: Any) -> dict[str, str]:
    """Normalized lever name → dot-path suffix (relative to ``qubit_pairs.<pid>.``).

    Only levers whose keys EXIST are returned — sparse chips (CR_state omits
    null keys) must never grow phantom editable rows whose Apply 400s. When a
    lever exists on both the channel and the CR macro (flavor B carries
    ``qc/qt_correction_phase`` on both), the channel wins the bare name and the
    macro copy is exposed as ``macro_<lever>``. ZZ-channel levers come back
    prefixed ``zz_`` with the pair's REAL key (``zz_drive`` vs ``zz``) in the
    path.
    """
    out: dict[str, str] = {}
    if not isinstance(pair_obj, dict):
        return out

    chan = cr_channel(pair_obj)
    if chan is not None:
        for key in CR_CHANNEL_KEYS:
            if pair_obj.get(key) is chan:
                chan_key = key
                break
        else:                                    # pragma: no cover - defensive
            chan_key = CR_CHANNEL_KEYS[0]
        for lever in _LEVER_KEYS + ("bell_state_fidelity", "upconverter"):
            if lever in chan:
                out[lever] = f"{chan_key}.{lever}"

    hit = cr_gate_macro(pair_obj)
    if hit is not None:
        gate_name, gate = hit
        for lever in _LEVER_KEYS:
            if lever in gate:
                name = lever if lever not in out else f"macro_{lever}"
                out[name] = f"macros.{gate_name}.{lever}"

    zz = zz_channel(pair_obj)
    if zz is not None:
        zz_key, zz_chan = zz
        for lever in ("detuning",) + _LEVER_KEYS + ("upconverter",):
            if lever in zz_chan:
                out[f"zz_{lever}"] = f"{zz_key}.{lever}"

    return out


# ---------------------------------------------------------------------------
# Effective frequency chain (numeric emulation of the runtime-only #./inferred_*)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CrFrequencies:
    lo_hz: float | None
    target_rf_hz: float | None
    if_hz: float | None                  # target_rf − lo (+ detuning for zz)
    rf_hz: float | None                  # emulated inferred_RF_frequency
    upconverter: int | None
    formula: str                         # "rf-lo" | "lo+if-lo" | "rf-lo+det"
    sources: dict = field(default_factory=dict)   # input -> resolved-from note
    valid: bool = False
    problems: tuple[str, ...] = ()


def _resolve_leaf(store: Any, value: Any, path: tuple[str, ...]) -> Any:
    """Resolve an absolute/relative pointer via the store's cache; literals and
    self-refs come back as-is (matching ``query._resolve`` semantics)."""
    if is_pointer(value) and not is_self_ref(value):
        return store.resolve_pointer(value, path)
    return value


def _port_dict(store: Any, chan: dict, base: tuple[str, ...]) -> dict | None:
    """Resolve the channel's ``opx_output`` (usually a ``#/wiring/...`` double
    hop) to the concrete port dict."""
    raw = chan.get("opx_output")
    if raw is None:
        return None
    resolved = _resolve_leaf(store, raw, base + ("opx_output",))
    return resolved if isinstance(resolved, dict) else None


def _upconverter_freq(port: dict, upconverter: Any) -> float | None:
    """Emulate ``MWChannel.upconverter_frequency``: prefer the per-port
    ``upconverters`` dict (dual-LO layout), fall back to the scalar."""
    ups = port.get("upconverters")
    if isinstance(ups, dict) and upconverter is not None:
        entry = ups.get(str(upconverter), ups.get(upconverter))
        if isinstance(entry, dict):
            return _num(entry.get("frequency"))
        return _num(entry)
    return _num(port.get("upconverter_frequency"))


def effective_frequencies(store: Any, pair_id: str,
                          channel: str = "cr") -> CrFrequencies | None:
    """Numeric emulation of the CR/ZZ channel's ``#./inferred_*`` properties.

    ``pointer_resolver`` deliberately returns ``#./`` self-refs raw — the
    inferred IF/RF are Python ``@property``s with no JSON value. This
    re-implements their arithmetic (like ``pulse_catalog.inferred_length``
    does for runtime lengths) so surfaces can show the effective CR drive
    frequency and run the |IF| ≤ 400 MHz MW-FEM check the customer's own
    populate scripts assert. Unresolvable inputs become ``problems`` entries —
    never exceptions, never guesses. Returns ``None`` when the pair has no
    such channel (the cheap no-op path for non-CR chips).
    """
    pair_obj = store.merged.get("qubit_pairs", {}).get(pair_id)
    if not isinstance(pair_obj, dict):
        return None

    if channel == "cr":
        chan = cr_channel(pair_obj)
        chan_key = next((k for k in CR_CHANNEL_KEYS
                         if pair_obj.get(k) is chan), CR_CHANNEL_KEYS[0])
    else:
        zz = zz_channel(pair_obj)
        chan, chan_key = (zz[1], zz[0]) if zz is not None else (None, "zz_drive")
    if chan is None:
        return None

    base = ("qubit_pairs", pair_id, chan_key)
    problems: list[str] = []
    sources: dict[str, str] = {}
    flavor, _sig = detect_pair_flavor(pair_obj)
    upconverter = chan.get("upconverter")
    port = _port_dict(store, chan, base)

    # --- effective LO -----------------------------------------------------
    lo_raw = chan.get("LO_frequency")
    lo_hz: float | None = None
    if is_self_ref(lo_raw) or lo_raw is None:
        # "#./upconverter_frequency" (or unset → same default): read the port.
        if port is not None:
            lo_hz = _upconverter_freq(port, upconverter)
            sources["lo"] = "port upconverter"
        if lo_hz is None:
            problems.append("could not resolve LO (port upconverter unavailable)")
    else:
        resolved = _resolve_leaf(store, lo_raw, base + ("LO_frequency",))
        lo_hz = _num(resolved)
        if lo_hz is not None:
            sources["lo"] = lo_raw if isinstance(lo_raw, str) else "literal"
        else:
            problems.append(f"could not resolve LO_frequency ({lo_raw!r})")

    # --- effective target RF ----------------------------------------------
    target_rf: float | None = None
    if "target_qubit_RF_frequency" in chan:
        raw = chan.get("target_qubit_RF_frequency")
        resolved = _resolve_leaf(store, raw, base + ("target_qubit_RF_frequency",))
        target_rf = _num(resolved)
        formula = "rf-lo"
        if target_rf is not None:
            sources["target_rf"] = raw if isinstance(raw, str) else "literal"
        else:
            problems.append(
                f"could not resolve target_qubit_RF_frequency ({raw!r})")
    else:
        t_lo = _resolve_leaf(store, chan.get("target_qubit_LO_frequency"),
                             base + ("target_qubit_LO_frequency",))
        t_if = _resolve_leaf(store, chan.get("target_qubit_IF_frequency"),
                             base + ("target_qubit_IF_frequency",))
        formula = "lo+if-lo"
        if _num(t_lo) is not None and _num(t_if) is not None:
            target_rf = float(t_lo) + float(t_if)
            sources["target_rf"] = "target LO + IF literals"
        else:
            problems.append("could not resolve target LO+IF frequencies")

    # --- detuning (zz only) -------------------------------------------------
    detuning = 0.0
    if channel != "cr":
        det = _resolve_leaf(store, chan.get("detuning"), base + ("detuning",))
        if _num(det) is not None:
            detuning = float(det)
            formula = "rf-lo+det"
        elif chan.get("detuning") is not None:
            problems.append("could not resolve zz detuning")

    # --- combine ------------------------------------------------------------
    if_hz: float | None = None
    if lo_hz is not None and target_rf is not None:
        if_hz = target_rf - lo_hz + detuning
        if abs(if_hz) > MW_MAX_ABS_IF_HZ:
            problems.append(
                f"|IF| {abs(if_hz) / 1e6:.1f} MHz exceeds the "
                f"{MW_MAX_ABS_IF_HZ / 1e6:.0f} MHz MW-FEM limit")

    band = port.get("band") if isinstance(port, dict) else None
    if lo_hz is not None and band is not None and not in_band(lo_hz, band):
        problems.append(f"LO {lo_hz / 1e9:.3f} GHz outside port band {band}")
    if (target_rf is not None and band is not None
            and bands_of(target_rf) and band not in bands_of(target_rf)):
        problems.append(
            f"target RF {target_rf / 1e9:.3f} GHz outside port band {band}")

    return CrFrequencies(
        lo_hz=lo_hz,
        target_rf_hz=target_rf,
        if_hz=if_hz,
        rf_hz=target_rf if channel == "cr" else (
            None if if_hz is None or lo_hz is None else lo_hz + if_hz),
        upconverter=upconverter if isinstance(upconverter, int) else None,
        formula=formula,
        sources=sources,
        valid=(if_hz is not None and not problems),
        problems=tuple(problems),
    )


# ---------------------------------------------------------------------------
# Directed-pair helpers
# ---------------------------------------------------------------------------

def pair_endpoints(pair_obj: Any) -> tuple[str | None, str | None]:
    """``(control_name, target_name)`` from the pair's qubit POINTERS.

    Never derived by splitting the pair id: the ``"q0-1"`` naming convention
    (target index without the ``q`` prefix) makes id-splitting yield ``"1"``,
    not ``"q1"``. The ``qubit_control``/``qubit_target`` pointers are the only
    authoritative source.
    """
    if not isinstance(pair_obj, dict):
        return (None, None)

    def _tail(v: Any) -> str | None:
        if isinstance(v, str) and v:
            return v.split("/")[-1] if "/" in v else v
        return None

    return (_tail(pair_obj.get("qubit_control")), _tail(pair_obj.get("qubit_target")))


def physical_edge_key(pair_obj: Any) -> tuple[str, str] | None:
    """Undirected edge identity — ``tuple(sorted((qc, qt)))`` — so the two
    directions of one physical coupling share a key."""
    qc, qt = pair_endpoints(pair_obj)
    if qc is None or qt is None:
        return None
    return tuple(sorted((qc, qt)))  # type: ignore[return-value]


def directed_partner(merged: Any, pair_id: str) -> str | None:
    """The pair id driving the SAME physical edge in the opposite direction."""
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    if not isinstance(pairs, dict):
        return None
    me = pairs.get(pair_id)
    qc, qt = pair_endpoints(me)
    if qc is None or qt is None:
        return None
    for pid, pobj in pairs.items():
        if pid == pair_id:
            continue
        oc, ot = pair_endpoints(pobj)
        if oc == qt and ot == qc:
            return pid
    return None


def is_active(merged: Any, pair_id: str) -> bool:
    """Membership in ``active_qubit_pair_names`` — when that list exists and is
    non-empty. An absent/empty list means "everything active" (CZ chips and
    older states must not suddenly render as inactive)."""
    if not isinstance(merged, dict):
        return True
    names = merged.get("active_qubit_pair_names")
    if not isinstance(names, list) or not names:
        return True
    return pair_id in names


def gate_class_evidence(merged: Any, leaf_name: str) -> str | None:
    """Majority-vote verbatim ``__class__`` among this chip's macros whose leaf
    equals *leaf_name* (e.g. ``"CRGate"``). The add-gate form writes what the
    chip already uses — never an invented module path (the
    ``routes._parametric_cz_evidence`` pattern, lifted to core so routes and
    tests share it). Deterministic tie-break: lexicographically smallest.
    """
    pairs = merged.get("qubit_pairs") if isinstance(merged, dict) else None
    if not isinstance(pairs, dict):
        return None
    counts: dict[str, int] = {}
    for pobj in pairs.values():
        macros = pobj.get("macros") if isinstance(pobj, dict) else None
        if not isinstance(macros, dict):
            continue
        for gate in macros.values():
            if not isinstance(gate, dict):
                continue
            cls = gate.get("__class__")
            if isinstance(cls, str) and cls.rsplit(".", 1)[-1] == leaf_name:
                counts[cls] = counts.get(cls, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
