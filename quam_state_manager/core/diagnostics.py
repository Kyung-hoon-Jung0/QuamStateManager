"""Structural diagnostics linter for QUAM state + wiring + QM config.

This is the "what is cracked / misaligned?" engine. Researchers open these
files mostly to debug an error — a dangling JSON pointer, a port assignment
that points at a port that doesn't exist, two qubits colliding on one
physical port, or a generated config whose elements reference pulses/
waveforms that were never defined. None of that was surfaced before
(``QuamStore.pointer_warnings`` was collected but never shown).

Everything here is **pure**: no QM-stack imports, no subprocess, no disk —
so it runs inside the Flask process and against an in-memory
:meth:`QuamStore.from_dicts` preview store just as well as a live chip.

The checks are deliberately conservative. The project's philosophy is to
*trust researcher input* and never range-check values (coupler amplitudes
> 1, negative T2, etc. are all legal), so the linter only flags things that
are *structurally* broken — missing references, nonexistent/colliding
ports, non-finite numbers — plus a couple of low-severity consistency
hints. ``null`` is never flagged (it legitimately means "not yet measured").
"""

from __future__ import annotations

import math
import weakref
from dataclasses import dataclass
from typing import Any

from quam_state_manager.core import (
    config_view,
    pulse_index,
    spec_constraints,
    waveform_synth,
)
from quam_state_manager.core.loader import _walk
from quam_state_manager.core.mw_fem import MW_MAX_ABS_IF_HZ
from quam_state_manager.core.query import _parse_port_ref, _resolve

# Roles that may legitimately share one physical port (readout multiplexing:
# many resonators on a single feedline output, and their shared input).
_MULTIPLEX_ROLES = {"rr"}

# A single element may legitimately drive one physical port through a base role
# and its "_"-suffixed twin — notably a TWPA's ``pump`` + ``pump_`` (two pump-tone
# definitions on the device's single pump output). That is a self-share, not a
# cross-element collision, so it is allowed when ALL colliding assignments come
# from the same element and stay within this set.
_SELF_SHARE_ROLES = {"pump", "pump_"}


@dataclass
class Finding:
    """One diagnostics result.

    ``severity`` is ``"error" | "warning" | "info"``. ``location`` is a
    human-readable address; ``jump_path`` is a dot-path the Explorer can
    navigate to (empty when there's no single source). ``port_key`` is set
    for wiring findings so the diagram can ring the offending port.
    """

    severity: str
    category: str
    location: str
    message: str
    detail: str = ""
    jump_path: str = ""
    port_key: dict | None = None
    fix: dict | None = None  # an offered one-click fix, e.g. {"action","dot_path","pointer",...}
    advisory: bool = False  # an OPTIONAL recommendation (e.g. band-edge headroom),
    #                         not a defect — surfaced with a distinct "Recommendation"
    #                         tier in the UI without lowering its warning severity.

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "category": self.category,
            "location": self.location,
            "message": self.message,
            "detail": self.detail,
            "jump_path": self.jump_path,
            "port_key": self.port_key,
            "fix": self.fix,
            "advisory": self.advisory,
        }


def summarize(findings: list[Finding]) -> dict:
    """Count findings → ``{"error","warning","info","advisory","total"}``.

    ``advisory`` findings (optional recommendations such as band-edge headroom)
    carry a real ``severity`` — usually ``"warning"`` — but are bucketed
    SEPARATELY so the header/tray badge doesn't surface an optional
    recommendation as a warning-tier defect. This matches how
    ``_diagnostics_list.html`` already splits them out on the Diagnostics page;
    before this, the same chip showed "2 warnings" on the badge and
    "0 warnings / 2 recommendations" on the page."""
    out = {"error": 0, "warning": 0, "info": 0, "advisory": 0}
    for f in findings:
        if getattr(f, "advisory", False):
            out["advisory"] += 1
        elif f.severity in out:
            out[f.severity] += 1
    out["total"] = len(findings)
    return out


# Display domains for the diagnostics-list UI (collapsible sections + per-domain
# counts). Ordered; this is the single source of truth shared by the template's
# grouping. (Distinct from /diagnostics/findings.json's binary value_spec-vs-
# connectivity split, which routes Explorer marks vs wiring-port rings — a
# connectivity finding both rings its port AND lives in the Connectivity domain.)
DIAG_DOMAINS = [
    ("env", "Environment match"),
    ("connectivity", "Connectivity & wiring"),
    ("values", "Values"),
    ("waveforms", "Waveforms"),
    ("references", "References"),
    ("config", "Config"),
    ("other", "Other"),
]


def domain_of(category: str) -> str:
    """Map a Finding ``category`` to one of :data:`DIAG_DOMAINS` (first match)."""
    c = category or ""
    if c.startswith("env_"):
        return "env"
    if c.startswith(("connectivity", "port_", "downconverter")):
        return "connectivity"
    if c.startswith("waveform"):
        return "waveforms"
    if c.startswith("value_spec") or c in ("value_nan", "value_type", "value_freq_consistency"):
        return "values"
    if c == "dangling_pointer":
        return "references"
    if c.startswith("config"):
        return "config"
    return "other"


# ---------------------------------------------------------------------------
# "What does this page check?" catalogue
# ---------------------------------------------------------------------------
# A human-readable inventory of every check the linter runs, grouped by the same
# domains as DIAG_DOMAINS. The /diagnostics "What is checked?" popup renders this
# verbatim, so it is the single source of truth for the linter's documented
# coverage — KEEP IT IN SYNC when adding or removing a check. ``severity`` is the
# typical tier the check emits (a couple emit a softer tier in benign cases); it
# only drives the colour of the little badge in the popup.
_CHECK_CATALOG: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("env", [
        ("error", "Classes importable in the selected env", "Every __class__ the state references imports in the selected python environment (third-party packages included) — an unimportable class makes Quam.load() fail."),
        ("error", "Fields exist on the env's classes", "Every key under a __class__-bearing node is a real field of that class in the selected env — an unknown field raises AttributeError('Unexpected attribute') at Quam.load(). Free-form dicts (extras, operations) are never flagged."),
        ("error", "Required fields present", "Fields the env's class requires (no default) exist in the state."),
        ("warning", "Value types match annotations", "Scalar values match the env class's type annotations (int widening and pointer values always pass; enum membership is advisory)."),
        ("warning", "Package versions match", "The state's __package_versions__ stamp (written by quam ≥0.6) matches the selected env's installed versions."),
    ]),
    ("connectivity", [
        ("error", "Port exists", "Every channel's assigned opx_output / opx_input resolves to a real port declared in state.json 'ports'."),
        ("error", "No port collisions", "Two elements never share one physical port + upconverter (legal readout- and cross-resonance multiplexing and a TWPA's own pump/pump_ are allowed)."),
        ("error", "MW-FEM IF within ±500 MHz", "The element intermediate frequency (RF_frequency − port LO) stays inside the 1 GSa/s Nyquist limit the QUA compiler enforces."),
        ("warning", "MW-FEM carrier reach", "RF_frequency stays within the FEM's physical range — drive output 50 MHz–10.5 GHz, readout input 2–10.5 GHz."),
        ("error", "CR/ZZ effective IF within ±500 MHz", "A pair drive's emulated intermediate frequency (effective target RF − LO, the runtime-only #./inferred_* arithmetic) stays inside the Nyquist limit."),
        ("warning", "CR/ZZ effective IF within ±400 MHz", "The same emulated pair-drive IF stays inside the 400 MHz bound the QM populate scripts assert for cross-resonance / ZZ tones."),
        ("warning", "LO within its band", "Each MW port's upconverter / downconverter frequency lies in its configured band (1: 50 MHz–5.5 GHz, 2: 4.5–7.5, 3: 6.5–10.5 GHz)."),
        ("warning", "Coupled ports share a band", "MW port pairs that are hardware-coupled use the same band (or the allowed 1 & 3 combination)."),
        ("warning", "LF-FEM output bandwidth", "A flux/coupler intermediate frequency stays inside the LF-FEM passband (direct 750 MHz, amplified 330 MHz, 500 MHz on a 1 GSa/s port)."),
        ("warning", "Readout LO spacing", "Two MW-FEM input downconverter LOs on one FEM differ by at least 10 MHz when both read simultaneously."),
        ("recommendation", "Band-edge headroom", "Flags an LO sitting near a band edge when an overlapping band would seat it more centrally (optional, the LO still works)."),
        ("info", "Downconverter link", "A readout input's downconverter_frequency is a pointer to its paired output's upconverter (they share one LO) — offers a one-click link."),
    ]),
    ("values", [
        ("error", "Finite numbers", "No leaf holds NaN or Infinity."),
        ("warning", "f_01 ↔ RF_frequency in sync", "A qubit's bookkeeping f_01 matches the carrier RF_frequency the hardware actually plays (a deliberate readout detuning is allowed)."),
        ("warning", "f_01 drive reach", "An MW-driven qubit's f_01 falls within the FEM's 50 MHz–10.5 GHz drive range."),
        ("warning", "Readout IF demod floor", "A readout intermediate frequency is above the MW-FEM 5 MHz demodulation floor (|IF| ≤ 5 MHz can't be measured)."),
        ("warning", "Hardware value specs", "Catalogued hardware fields are in range/step: time_of_flight (mult-of-4), pulse length, full_scale_power_dbm (−11..18 dBm), band ∈ {1,2,3}, gain_db, sampling_rate, output/upsampling/lo_mode, Octave LO/gain/enums."),
        ("warning", "Field type consistency", "A field that is numeric on most siblings isn't a stray text value on one."),
    ]),
    ("waveforms", [
        ("error", "Sample within DAC range", "Every synthesized pulse sample stays inside its output's range (MW ±1, LF direct ±0.5 V / amplified ±2.5 V) — a sample outside it makes generate_config() reject the whole config."),
        ("error", "Valid pulse parameters", "Pulse parameters produce a real waveform (e.g. not sigma = 0 or a DRAG anharmonicity of 0)."),
    ]),
    ("references", [
        ("error", "Pointers resolve", "Every QUAM JSON pointer (#/, #../, #./) resolves to a real target."),
        ("recommendation", "Optional-default pointers", "A pointer at an omitted optional field is noted (it resolves to the quam default at runtime — not a breakage)."),
    ]),
    ("config", [
        ("error", "References defined", "Generated-config elements/pulses reference pulses, waveforms, integration weights, mixers and controllers that are actually defined."),
        ("warning", "No orphans", "Defined pulses / waveforms / weights / mixers are referenced by something."),
        ("warning", "Config value bounds", "Mixer correction ∈ [−2, 2−2⁻¹⁶], integration-weight magnitude ≤ 2048 with a mult-of-4 duration, time-tagging thresholds in signed-12-bit range."),
    ]),
]


def check_catalog() -> list[dict]:
    """The :data:`_CHECK_CATALOG` resolved against :data:`DIAG_DOMAINS` for display:
    a list of ``{key, label, checks:[{severity,title,desc}]}`` in domain order.
    Pure + static (no store) so the "What is checked?" popup can render it without
    a chip loaded."""
    labels = dict(DIAG_DOMAINS)
    return [
        {"key": key, "label": labels.get(key, key.title()),
         "checks": [{"severity": s, "title": t, "desc": d} for s, t, d in checks]}
        for key, checks in _CHECK_CATALOG
    ]


# ---------------------------------------------------------------------------
# State + wiring linter
# ---------------------------------------------------------------------------

# The WHOLE lint result is memoized per store at one ``mutation_seq`` (same
# scheme as ``_wf_findings_cache`` below). ``lint_state`` is the hottest
# diagnostics entry point: a single page render fires it 2-3x (the header badge
# via /diagnostics/summary, the banner via /diagnostics/banner, plus /topology
# inline), each doing a full pointer-resolve tree-walk + value scans under the
# store lock — so without this the same chip is re-linted several times per
# navigation, contending with edits. Keyed weakly so stores can be GC'd.
_lint_state_cache: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def lint_state(store) -> list[Finding]:
    """Lint a :class:`QuamStore` (live or ``from_dicts``) for structural breakage.

    Memoized per store at its ``mutation_seq`` (every edit increments it, so the
    cache self-invalidates on mutation). Always returns a FRESH list — some
    callers sort the result in place (``routes._active_chip_findings`` does
    ``findings.sort(...)`` when there's no generated config), so handing back the
    cached object would corrupt it for the next caller."""
    seq = getattr(store, "mutation_seq", None)
    hit = _lint_state_cache.get(store)
    if hit is not None and hit[0] == seq:
        return list(hit[1])
    # Hold the store lock across the walk: _lint_state_uncached iterates
    # store.merged, and a concurrent /field/edit inserting/deleting a key would
    # raise 'dict changed size during iteration'. Read-only + ms-fast; the store's
    # RLock is reentrant so nested resolver calls that re-take it are fine.
    lock = getattr(store, "_lock", None)
    if lock is not None:
        with lock:
            out = _lint_state_uncached(store)
    else:
        out = _lint_state_uncached(store)
    try:
        _lint_state_cache[store] = (seq, out)
    except TypeError:  # pragma: no cover - store not weak-referenceable
        pass
    return list(out)


def _lint_state_uncached(store) -> list[Finding]:
    """The actual lint pass — run once per store mutation by :func:`lint_state`."""
    findings: list[Finding] = []
    root = store.merged if isinstance(store.merged, dict) else {}

    findings.extend(_port_findings(root))
    findings.extend(_dangling_pointer_findings(store))
    findings.extend(_value_findings(root, "qubits"))
    findings.extend(_value_findings(root, "qubit_pairs"))
    findings.extend(_frequency_consistency_findings(store))
    findings.extend(_downconverter_findings(root))
    findings.extend(_spec_findings(root))
    findings.extend(_coupling_findings(root))
    findings.extend(_band_edge_findings(root))
    findings.extend(_mw_carrier_findings(root))
    findings.extend(_pair_drive_carrier_findings(store))
    findings.extend(_f01_range_findings(root))
    findings.extend(_lffem_output_bw_findings(root))
    findings.extend(_resonator_if_floor_findings(root))
    findings.extend(_downconverter_spacing_findings(root))
    findings.extend(_waveform_findings_cached(store))

    return _ordered(findings)


def _spec_findings(root: dict) -> list[Finding]:
    """Hardware value-spec violations (type/range/step) → Explorer-jumpable warnings.

    Delegates the actual catalogue + numeric checks to :mod:`core.spec_constraints`
    (a pure data module) and wraps each plain dict into a :class:`Finding`. These
    carry ``jump_path`` (so the Explorer marks the offending row) and no
    ``port_key``. ``category`` is prefixed ``value_spec_*``.
    """
    findings: list[Finding] = []
    for d in spec_constraints.spec_findings(root):
        findings.append(Finding(
            d["severity"], d["category"], d["location"], d["message"],
            detail=d.get("detail", ""), jump_path=d.get("jump_path", ""),
        ))
    return findings


def _coupling_findings(root: dict) -> list[Finding]:
    """OPX1000 MW-FEM connectivity rules: band↔frequency range + coupled-pair band share.

    Emits findings with ``port_key`` (so the wiring diagram rings the port) AND
    ``jump_path`` (so it also surfaces in the Explorer). Only literal values are
    checked; pointer/None bands or frequencies are skipped. Partial setups (only
    one side of a coupled pair present) emit nothing.
    """
    findings: list[Finding] = []
    ports = root.get("ports")
    if not isinstance(ports, dict):
        return findings

    # Index MW ports per FEM: (ctrl, fem) -> {(side, port): {band, freq, section, freq_field}}
    index: dict[tuple, dict[tuple, dict]] = {}

    def _collect(section: str, side: str, freq_field: str) -> None:
        node = ports.get(section)
        if not isinstance(node, dict):
            return
        for ctrl, fems in node.items():
            if not isinstance(fems, dict):
                continue
            for fem, portmap in fems.items():
                if not isinstance(portmap, dict):
                    continue
                for port, cfg in portmap.items():
                    if not isinstance(cfg, dict):
                        continue
                    index.setdefault((ctrl, fem), {})[(side, str(port))] = {
                        "band": cfg.get("band"),
                        "freq": cfg.get(freq_field),
                        "section": section,
                        "freq_field": freq_field,
                    }

    _collect("mw_outputs", "out", "upconverter_frequency")
    _collect("mw_inputs", "in", "downconverter_frequency")

    # 1) Band ↔ LO-frequency range (per port).
    for (ctrl, fem), portmap in index.items():
        for (side, port), info in portmap.items():
            band = spec_constraints.as_int(info["band"])
            freq = spec_constraints._num(info["freq"])
            if band in spec_constraints.BAND_FREQ_RANGES and freq is not None:
                lo, hi = spec_constraints.BAND_FREQ_RANGES[band]
                if not (lo <= freq <= hi):
                    parsed = (info["section"], ctrl, fem, port)
                    findings.append(Finding(
                        "warning", "connectivity_freq", _port_label(parsed),
                        f"{info['freq_field']} {freq:.0f} Hz is outside band {band} "
                        f"range [{lo:.0f}, {hi:.0f}] Hz",
                        detail=repr(info["freq"]),
                        jump_path=f"ports.{info['section']}.{ctrl}.{fem}.{port}.{info['freq_field']}",
                        port_key=_port_key(parsed),
                    ))

    # 1b) Band ↔ LO for MULTI-upconverter ports. A shared xy+CR port stores its
    # LOs under upconverters.<n>.frequency (a dict), NOT the scalar
    # upconverter_frequency the loop above reads — so without this every such
    # port's LOs go unchecked. Band-check each upconverter frequency.
    mw_out = ports.get("mw_outputs")
    if isinstance(mw_out, dict):
        for ctrl, fems in mw_out.items():
            if not isinstance(fems, dict):
                continue
            for fem, portmap in fems.items():
                if not isinstance(portmap, dict):
                    continue
                for port, cfg in portmap.items():
                    ucs = cfg.get("upconverters") if isinstance(cfg, dict) else None
                    if not isinstance(ucs, dict):
                        continue
                    band = spec_constraints.as_int(cfg.get("band"))
                    if band not in spec_constraints.BAND_FREQ_RANGES:
                        continue
                    lo, hi = spec_constraints.BAND_FREQ_RANGES[band]
                    for uc_idx, uc in ucs.items():
                        freq = spec_constraints._num(uc.get("frequency")) if isinstance(uc, dict) else None
                        if freq is None or lo <= freq <= hi:
                            continue
                        parsed = ("mw_outputs", ctrl, fem, port)
                        findings.append(Finding(
                            "warning", "connectivity_freq", _port_label(parsed),
                            f"upconverter {uc_idx} frequency {freq:.0f} Hz is outside "
                            f"band {band} range [{lo:.0f}, {hi:.0f}] Hz",
                            detail=repr(uc.get("frequency")),
                            jump_path=f"ports.mw_outputs.{ctrl}.{fem}.{port}.upconverters.{uc_idx}.frequency",
                            port_key=_port_key(parsed),
                        ))

    # 2) Coupled-pair band share.
    for (ctrl, fem), portmap in index.items():
        for a_port, a_side, b_port, b_side in spec_constraints.COUPLED_PORT_PAIRS:
            a = portmap.get((a_side, a_port))
            b = portmap.get((b_side, b_port))
            if not a or not b:
                continue
            ba = spec_constraints.as_int(a["band"])
            bb = spec_constraints.as_int(b["band"])
            if ba is None or bb is None:
                continue
            if (ba, bb) not in spec_constraints.BAND_COMPAT:
                msg = (f"coupled MW ports {a_side}{a_port} (band {ba}) and "
                       f"{b_side}{b_port} (band {bb}) on {ctrl}/FEM {fem} are on "
                       f"incompatible bands (coupled ports must share a band)")
                for info, port in ((a, a_port), (b, b_port)):
                    parsed = (info["section"], ctrl, fem, port)
                    findings.append(Finding(
                        "warning", "connectivity_band", _port_label(parsed), msg,
                        detail=f"bands {ba},{bb}",
                        jump_path=f"ports.{info['section']}.{ctrl}.{fem}.{port}.band",
                        port_key=_port_key(parsed),
                    ))

    return findings


# How close (Hz) an MW-FEM LO may sit to its band edge before we suggest a more
# central overlapping band. Advisory only — bands genuinely overlap, so an edge
# LO still works; this is a signal-quality nudge, not a compile constraint.
BAND_EDGE_MARGIN_HZ = 50e6


def _coupled_mate_label(side: str, port: str) -> str | None:
    """If ``(side, port)`` participates in a :data:`spec_constraints.COUPLED_PORT_PAIRS`
    entry, return the mate's label (``"out2"`` / ``"in1"``); else None. Used to
    note that a coupled port's band can't move independently of its mate."""
    p = str(port)
    for a_port, a_side, b_port, b_side in spec_constraints.COUPLED_PORT_PAIRS:
        if a_side == side and a_port == p:
            return f"{b_side}{b_port}"
        if b_side == side and b_port == p:
            return f"{a_side}{a_port}"
    return None


def _mate_lo_band(root: dict, ctrl, fem, mate_label: str) -> tuple[float | None, int | None]:
    """Resolve a coupled-mate's ``(LO_frequency, band)`` on the same FEM, given a
    label like ``"out2"`` / ``"in1"`` from :func:`_coupled_mate_label`. The LO is
    the mate's ``upconverter_frequency`` (output) or ``downconverter_frequency``
    (input), resolved through a pointer if needed. ``(None, None)`` when the mate
    port or its LO can't be read — the caller then conservatively declines to
    recommend a band move (a recommendation the mate can't follow would be a
    self-contradiction)."""
    side = "out" if mate_label.startswith("out") else "in"
    mport = mate_label[len(side):]
    section = "mw_outputs" if side == "out" else "mw_inputs"
    field = "upconverter_frequency" if side == "out" else "downconverter_frequency"
    node: Any = root.get("ports")
    for key in (section, ctrl, fem, mport):
        node = node.get(key) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        return None, None
    return _abs_field_value(root, node.get(field)), spec_constraints.as_int(node.get("band"))


def _band_edge_findings(root: dict) -> list[Finding]:
    """MW-FEM output LO (``upconverter_frequency``) sitting within 50 MHz of its
    band's edge when an *overlapping* band would place it more centrally.

    The OPX1000 MW-FEM bands partially overlap (band 2 = [4.5, 7.5] GHz, band 3 =
    [6.5, 10.5] GHz), and ``band`` constrains the **LO** range (opx1000_fems.md
    L113: ``upconverter_frequency`` must lie in the port's band). A LO near a band
    edge has little headroom; if it also falls inside an overlapping band where it
    would be more central, that band leaves a more comfortable LO range margin.
    This is framed as **more headroom, NOT a guarantee of better signal quality**
    (which is setup-dependent), and as an **optional suggestion, not a
    requirement** — the LO is fully functional at the edge — so it's a ``warning``
    worded as advisory and never errors or banners. Literal LO only (pointer/None
    skip); only LOs genuinely inside their declared band (an out-of-band LO is
    reported separately as :func:`_coupling_findings`' ``connectivity_freq``
    warning, not double-reported here).

    For a **coupled** port the recommendation is only emitted when the move is
    feasible for the whole pair: coupled ports share one band, and the always-legal
    move is both ports → the alternative band (same-band), which requires the mate's
    own LO to also fit that band. If it can't, the recommendation is dropped (a
    coupled suggestion the mate can't follow would contradict itself)."""
    findings: list[Finding] = []
    ports = root.get("ports")
    mw_outputs = ports.get("mw_outputs") if isinstance(ports, dict) else None
    if not isinstance(mw_outputs, dict):
        return findings

    ranges = spec_constraints.BAND_FREQ_RANGES
    for ctrl, fems in mw_outputs.items():
        if not isinstance(fems, dict):
            continue
        for fem, portmap in fems.items():
            if not isinstance(portmap, dict):
                continue
            for port, cfg in portmap.items():
                if not isinstance(cfg, dict):
                    continue
                band = spec_constraints.as_int(cfg.get("band"))
                f = cfg.get("upconverter_frequency")
                if band not in ranges or not (
                        isinstance(f, (int, float)) and not isinstance(f, bool)):
                    continue
                f = float(f)
                lo, hi = ranges[band]
                if not (lo <= f <= hi):
                    continue  # out-of-band LO → _coupling_findings' job, not this
                margin = min(f - lo, hi - f)
                if margin >= BAND_EDGE_MARGIN_HZ:
                    continue

                # Is there an overlapping band that would seat this LO more centrally?
                best_alt, best_margin = None, margin
                for b2, (lo2, hi2) in ranges.items():
                    if b2 == band or not (lo2 <= f <= hi2):
                        continue
                    m2 = min(f - lo2, hi2 - f)
                    if m2 > best_margin:
                        best_alt, best_margin = b2, m2
                if best_alt is None:
                    continue

                parsed = ("mw_outputs", ctrl, fem, port)
                dp = f"ports.mw_outputs.{ctrl}.{fem}.{port}.upconverter_frequency"
                a_lo, a_hi = ranges[best_alt]

                # Coupled ports must share a band, so a band move means retuning
                # the mate too. Only recommend it when the mate's own LO also fits
                # the alternative band (the same-band move is always BAND_COMPAT-
                # legal); otherwise drop the recommendation entirely — never suggest
                # a move the coupled mate can't follow.
                mate = _coupled_mate_label("out", port)
                coupling_note = ""
                subject = "this LO"
                if mate is not None:
                    mate_freq, mate_band = _mate_lo_band(root, ctrl, fem, mate)
                    if mate_freq is None or not (a_lo <= mate_freq <= a_hi):
                        continue
                    subject = f"the coupled pair (this port + {mate})"
                    coupling_note = (
                        f" Note: {mate} is coupled to this port"
                        + (f" (currently band {mate_band})" if mate_band else "")
                        + f"; the OPX1000 requires coupled ports to share a band, so "
                        f"both retune to band {best_alt} together.")

                msg = (
                    f"LO (upconverter_frequency) {f / 1e9:.6g} GHz sits only "
                    f"{margin / 1e6:.3g} MHz from the band {band} edge "
                    f"[{lo / 1e9:.4g}, {hi / 1e9:.4g}] GHz; band {best_alt} "
                    f"([{a_lo / 1e9:.4g}, {a_hi / 1e9:.4g}] GHz) would place it "
                    f"{best_margin / 1e6:.3g} MHz from its nearest edge. The bands "
                    f"partially overlap, so this LO works in band {band}; placing "
                    f"{subject} in band {best_alt} would leave more headroom from the "
                    f"band edge (a more comfortable LO range margin — this does not "
                    f"guarantee better signal quality). Optional, not required."
                    + coupling_note)
                findings.append(Finding(
                    "warning", "connectivity_band_edge", _port_label(parsed), msg,
                    detail=f"margin {margin / 1e6:.3g} MHz (band {band}) vs "
                           f"{best_margin / 1e6:.3g} MHz (band {best_alt})",
                    jump_path=dp, port_key=_port_key(parsed), advisory=True))
    return findings


def _port_findings(root: dict) -> list[Finding]:
    """Wiring port reference integrity + illegal collisions."""
    findings: list[Finding] = []
    ports_root = root.get("ports") if isinstance(root.get("ports"), dict) else {}
    wiring = root.get("wiring") if isinstance(root.get("wiring"), dict) else {}

    # Collect every opx_output / opx_input reference under wiring.
    entries: list[dict] = []  # {dot_path, ref, parsed, role, element, leaf}
    for section in ("qubits", "qubit_pairs", "twpas"):
        sec = wiring.get(section)
        if not isinstance(sec, dict):
            continue
        for elem_name, elem_w in sec.items():
            if not isinstance(elem_w, dict):
                continue
            for rel, value, _ in _walk(elem_w):
                leaf = rel.split(".")[-1]
                if leaf not in ("opx_output", "opx_input") or not isinstance(value, str):
                    continue
                entries.append({
                    "dot_path": f"wiring.{section}.{elem_name}.{rel}",
                    "ref": value,
                    "parsed": _parse_port_ref(value),
                    "role": rel.split(".")[0],
                    "element": elem_name,
                    "section": section,
                })

    have_ports = bool(ports_root)
    by_port: dict[tuple, list[dict]] = {}
    for e in entries:
        parsed = e["parsed"]
        if parsed is None:
            findings.append(Finding(
                "warning", "port_unrecognized", e["dot_path"],
                "port reference is not in a recognized format",
                detail=e["ref"], jump_path=e["dot_path"],
            ))
            continue
        port_type, ctrl, fem, port = parsed
        by_port.setdefault(parsed, []).append(e)
        # Existence check (only if the chip declares a ports section).
        if have_ports and not _port_exists(ports_root, port_type, ctrl, fem, port):
            findings.append(Finding(
                "error", "port_missing", _port_label(parsed),
                f"{e['element']}.{e['role']} is assigned to a port that does not "
                f"exist in state.json 'ports'",
                detail=e["ref"], jump_path=e["dot_path"],
                port_key=_port_key(parsed),
            ))

    # Collisions: >1 assignment on a port, unless it's legal multiplexing.
    for parsed, group in by_port.items():
        if len(group) < 2:
            continue
        roles = {e["role"] for e in group}
        if roles <= _MULTIPLEX_ROLES:
            continue  # readout multiplexing is fine
        elements = {e["element"] for e in group}
        if len(elements) == 1 and roles <= _SELF_SHARE_ROLES:
            continue  # one element's pump/pump_ sharing its own port — intentional

        port_type = parsed[0]
        if "output" in port_type:
            # MW-FEM output ports legitimately host MULTIPLE elements via
            # frequency multiplexing: a qubit xy on upconverter 1 PLUS one-or-more
            # cross-resonance drives on upconverter 2 (each CR pair using its own
            # intermediate_frequency). So a shared output port is only a real
            # collision when two assignments clash on the SAME upconverter AND
            # aren't a legal multiplex set (all-readout or all-CR share one
            # upconverter/LO across distinct IFs, like readout multiplexing).
            by_uc: dict[Any, list[dict]] = {}
            for e in group:
                by_uc.setdefault(
                    _upconverter_index(root, e["section"], e["element"], e["role"]),
                    [],
                ).append(e)
            clashes = []
            for sub in by_uc.values():
                if len(sub) < 2:
                    continue
                sub_roles = {e["role"] for e in sub}
                if sub_roles <= _MULTIPLEX_ROLES or sub_roles <= _CR_ROLES:
                    continue  # rr-mux or CR-mux on one upconverter — legal
                clashes.append(sub)
            for sub in clashes:
                who = ", ".join(f"{e['element']}.{e['role']}" for e in sub)
                findings.append(Finding(
                    "error", "port_collision", _port_label(parsed),
                    f"{len(sub)} assignments collide on one physical port + "
                    f"upconverter: {who}",
                    detail=sub[0]["ref"], jump_path=sub[0]["dot_path"],
                    port_key=_port_key(parsed),
                ))
            continue

        who = ", ".join(f"{e['element']}.{e['role']}" for e in group)
        findings.append(Finding(
            "error", "port_collision", _port_label(parsed),
            f"{len(group)} assignments collide on one physical port: {who}",
            detail=parsed[0] and group[0]["ref"] or "",
            jump_path=group[0]["dot_path"],
            port_key=_port_key(parsed),
        ))

    return findings


# Wiring roles that may legally share one MW-FEM output port + upconverter via
# frequency (IF) multiplexing — readout drives, and cross-resonance drives.
_CR_ROLES = {"cr", "cross_resonance"}


def _upconverter_index(root: dict, section: str, elem: str, role: str) -> int:
    """The upconverter index an element's channel drives on a shared MW-FEM
    output port. A qubit xy has no explicit key → upconverter 1; a pair's
    cross-resonance drive declares ``upconverter`` (2 in the reference). The
    wiring role is ``cr`` but the state channel key is ``cross_resonance`` (key
    divergence), so map it; fall back to any sub-channel that declares one."""
    el = root.get(section, {})
    el = el.get(elem) if isinstance(el, dict) else None
    if not isinstance(el, dict):
        return 1
    ch = el.get(role)
    if not isinstance(ch, dict) and role in _CR_ROLES:
        ch = el.get("cross_resonance")
    if not isinstance(ch, dict):
        for v in el.values():  # last-resort: a channel carrying an explicit index
            if isinstance(v, dict) and isinstance(v.get("upconverter"), int):
                ch = v
                break
    uc = ch.get("upconverter") if isinstance(ch, dict) else None
    return uc if isinstance(uc, int) else 1


def _port_exists(ports_root: dict, port_type: str, ctrl: str, fem: str, port: str) -> bool:
    node: Any = ports_root.get(port_type)
    for key in (ctrl, fem, port):
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True


def _port_key(parsed: tuple) -> dict:
    port_type, ctrl, fem, port = parsed
    io = "in" if "input" in port_type else "out"
    return {"ctrl": ctrl, "fem": fem, "port": port, "port_type": port_type, "io": io}


def _port_label(parsed: tuple) -> str:
    port_type, ctrl, fem, port = parsed
    return f"{ctrl} / FEM {fem} / port {port} ({port_type})"


# The dangling-pointer walk (``store.validate_pointers()``) is itself a full
# pointer-resolve tree-walk under ``store._lock``. It's already paid once per
# whole-lint cache miss, but memoize it per ``mutation_seq`` too so a surface
# that asks for pointer warnings separately (or a forced lint recompute) reuses
# the same result rather than re-walking + re-contending on the lock.
_pointer_warnings_cache: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _validate_pointers_cached(store) -> list:
    """``store.validate_pointers()`` memoized per store at its ``mutation_seq``.

    Returns the SAME cached list (callers here only iterate/filter it, never
    mutate it). ``validate_pointers`` itself already hands back a fresh
    ``list(self.pointer_warnings)``."""
    seq = getattr(store, "mutation_seq", None)
    hit = _pointer_warnings_cache.get(store)
    if hit is not None and hit[0] == seq:
        return hit[1]
    out = store.validate_pointers()
    try:
        _pointer_warnings_cache[store] = (seq, out)
    except TypeError:  # pragma: no cover - store not weak-referenceable
        pass
    return out


def _dangling_pointer_findings(store) -> list[Finding]:
    """Unresolvable JSON pointers, reusing the store's pointer validator.

    ``#/ports/...`` pointers are skipped here — a dangling one is the same
    problem as a missing port, and :func:`_port_findings` reports it with a
    clearer message.
    """
    findings: list[Finding] = []
    try:
        warnings = _validate_pointers_cached(store)
    except Exception:  # pragma: no cover - defensive; never let a lint crash a view
        return findings
    for w in warnings:
        if isinstance(w.pointer, str) and w.pointer.startswith("#/ports/"):
            # Only skip the pointers _port_findings ACTUALLY re-reports: the wiring
            # opx_output/opx_input leaves. A dangling #/ports/ pointer living
            # elsewhere (e.g. ports.mw_inputs.*.downconverter_frequency, which the
            # generator + apply-fix create) is NOT covered there, so hiding it here
            # made a real dangling pointer invisible to the page's own catalog.
            _dp = w.dot_path if isinstance(w.dot_path, str) else ""
            _leaf = _dp.rsplit(".", 1)[-1]
            if _dp.startswith("wiring.") and _leaf in ("opx_output", "opx_input"):
                continue
        if getattr(w, "soft", False):
            # The target object exists; only an optional field is omitted (it's at
            # its quam default). quam resolves it to the default at runtime — no
            # crash — so this is a collapsed advisory, NOT a "would crash" error.
            # (Without this, DragCosine `detuning`/`digital_marker` sibling
            # pointers fire dozens of phantom errors on real fixed-freq/CR chips.)
            findings.append(Finding(
                "info", "pointer_optional_default", w.dot_path,
                "JSON pointer targets an optional field omitted at its default "
                "(resolves to the default at runtime — not a breakage)",
                detail=w.pointer, jump_path=w.dot_path, advisory=True,
            ))
            continue
        findings.append(Finding(
            "error", "dangling_pointer", w.dot_path,
            "JSON pointer does not resolve to anything",
            detail=w.pointer, jump_path=w.dot_path,
        ))
    return findings


def _value_findings(root: dict, section: str) -> list[Finding]:
    """Non-finite numbers + conservative cross-sibling type-mismatch hints."""
    findings: list[Finding] = []
    sec = root.get(section)
    if not isinstance(sec, dict):
        return findings

    # Group leaf values by their path *within* an entry, across all entries.
    by_sub: dict[str, list[tuple[str, Any]]] = {}
    for name, sub in sec.items():
        if not isinstance(sub, dict):
            continue
        for rel, value, _ in _walk(sub):
            by_sub.setdefault(rel, []).append((name, value))

    singular = section[:-1] if section.endswith("s") else section
    for rel, items in by_sub.items():
        # Non-finite numbers (NaN / Inf) are always broken.
        for name, value in items:
            if isinstance(value, float) and not math.isfinite(value):
                dp = f"{section}.{name}.{rel}"
                findings.append(Finding(
                    "error", "value_nan", dp,
                    "value is a non-finite number (NaN or Infinity)",
                    detail=repr(value), jump_path=dp,
                ))

        # Type mismatch: a non-pointer text value where the same field is
        # numeric on a clear majority of siblings.
        considered = [
            (n, v) for n, v in items
            if v is not None and not (isinstance(v, str) and v.startswith("#"))
        ]
        numeric = [(n, v) for n, v in considered
                   if isinstance(v, (int, float)) and not isinstance(v, bool)]
        strings = [(n, v) for n, v in considered if isinstance(v, str)]
        if len(numeric) >= 2 and len(numeric) > len(strings) and strings:
            for n, v in strings:
                dp = f"{section}.{n}.{rel}"
                findings.append(Finding(
                    "warning", "value_type", dp,
                    f"value is text but the same field is numeric on "
                    f"{len(numeric)} other {singular}(s)",
                    detail=repr(v), jump_path=dp,
                ))

    return findings


# ---------------------------------------------------------------------------
# f_01 ↔ RF_frequency consistency
# ---------------------------------------------------------------------------
# Each qubit carries the transition frequency twice: ``f_01`` (the physics value)
# and the matching channel ``RF_frequency`` (the carrier we drive/read at). The
# calibration nodes write BOTH to the same fit result in lock-step (e.g.
# 1Q_08_qubit_spectroscopy sets ``q.f_01`` and ``q.xy.RF_frequency`` to the
# identical value; 1Q_12_ramsey applies the same delta to both; 1Q_02 does it for
# the resonator). Critically, ONLY ``RF_frequency`` reaches the hardware — quam's
# config uses the channel ``intermediate_frequency``, which the LabA states infer
# as ``RF_frequency - LO`` (quam ``inferred_intermediate_frequency``); ``f_01`` is
# never read by ``generate_config``. So when the two drift, the box keeps driving
# at ``RF_frequency`` while ``f_01`` silently lies — usually because one was edited
# without the other. A deliberate readout detuning (the readout-optimization node
# moves ``resonator.f_01`` to the optimal point) is the legitimate exception, so
# this is advisory (info / warning), NEVER an error and NEVER auto-corrected.
FREQ_CONSISTENCY_WARN_HZ = 1_000.0   # |Δ| ≥ 1 kHz → warning (actionable: likely a stale edit)
FREQ_CONSISTENCY_INFO_HZ = 1.0       # 1 Hz ≤ |Δ| < 1 kHz → info (FYI: could be a tiny detune)

# (f_01 path relative to the qubit, RF_frequency path, human label)
_FREQ_PAIRS = [
    (("f_01",), ("xy", "RF_frequency"), "qubit drive"),
    (("resonator", "f_01"), ("resonator", "RF_frequency"), "readout"),
]


def _dig(node: Any, rel: tuple) -> Any:
    """Walk a relative key tuple through nested dicts; None if any hop is missing."""
    cur = node
    for key in rel:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _isnum(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _fmt_delta(delta: float) -> str:
    a = abs(delta)
    if a >= 1e6:
        return f"{delta / 1e6:+.6g} MHz"
    if a >= 1e3:
        return f"{delta / 1e3:+.6g} kHz"
    return f"{delta:+.6g} Hz"


def _frequency_consistency_findings(store) -> list[Finding]:
    """Flag qubits whose ``f_01`` disagrees with the matching ``RF_frequency``.

    ``RF_frequency`` is the carrier the QUA config actually plays (the element
    ``intermediate_frequency`` is inferred as ``RF_frequency - LO``); ``f_01`` is
    physics bookkeeping the calibration nodes keep equal to it. When they drift the
    hardware follows ``RF_frequency`` while ``f_01`` lies — surface it. Pointer-
    encoded values are resolved first (a chip that hard-links ``RF_frequency`` to
    ``f_01`` via a ``#/`` reference resolves equal → no finding). Advisory only:
    a deliberate readout detuning is legitimate, so never an error.
    """
    findings: list[Finding] = []
    root = store.merged if isinstance(getattr(store, "merged", None), dict) else {}
    qubits = root.get("qubits")
    if not isinstance(qubits, dict):
        return findings

    for qname, q in qubits.items():
        if not isinstance(q, dict):
            continue
        for f01_rel, rf_rel, label in _FREQ_PAIRS:
            f01_raw = _dig(q, f01_rel)
            rf_raw = _dig(q, rf_rel)
            if f01_raw is None or rf_raw is None:
                continue
            f01 = _resolve(store, f01_raw, ("qubits", qname) + f01_rel)
            rf = _resolve(store, rf_raw, ("qubits", qname) + rf_rel)
            if not _isnum(f01) or not _isnum(rf):
                continue
            delta = float(f01) - float(rf)
            if abs(delta) < FREQ_CONSISTENCY_INFO_HZ:
                continue
            severity = "warning" if abs(delta) >= FREQ_CONSISTENCY_WARN_HZ else "info"
            f01_dp = "qubits." + qname + "." + ".".join(f01_rel)
            rf_dp = "qubits." + qname + "." + ".".join(rf_rel)
            findings.append(Finding(
                severity, "value_freq_consistency", f01_dp,
                f"{label} f_01 ({_ghz(f01)}) and RF_frequency ({_ghz(rf)}) differ by "
                f"{_fmt_delta(delta)}. RF_frequency is the carrier the hardware plays "
                f"(f_01 is not read by generate_config), so f_01 is out of sync — one "
                f"was edited without the other (or it's a deliberate detuning).",
                detail=f"{f01_dp}={f01!r}  vs  {rf_dp}={rf!r}  (Δ={delta:+.3f} Hz)",
                jump_path=f01_dp,
            ))
    return findings


def _readout_io_pairs(root: dict) -> dict[tuple, tuple]:
    """Map each readout MW input port ``(ctrl,fem,port)`` -> its paired MW output
    port ``(ctrl,fem,port)``, derived from the wiring's per-qubit
    ``rr.opx_input`` / ``rr.opx_output`` (the readout input and output on one
    MW-FEM port share a single physical local oscillator)."""
    pairs: dict[tuple, tuple] = {}
    wiring = root.get("wiring") if isinstance(root.get("wiring"), dict) else {}
    qubits = wiring.get("qubits") if isinstance(wiring.get("qubits"), dict) else {}
    for qd in qubits.values():
        if not isinstance(qd, dict):
            continue
        rr = qd.get("rr")
        if not isinstance(rr, dict):
            continue
        pin = _parse_port_ref(rr.get("opx_input")) if isinstance(rr.get("opx_input"), str) else None
        pout = _parse_port_ref(rr.get("opx_output")) if isinstance(rr.get("opx_output"), str) else None
        if not pin or not pout:
            continue
        pairs[(pin[1], pin[2], pin[3])] = (pout[1], pout[2], pout[3])
    return pairs


def _ghz(v) -> str:
    try:
        return f"{float(v) / 1e9:.6g} GHz"
    except (TypeError, ValueError):
        return repr(v)


def _downconverter_findings(root: dict) -> list[Finding]:
    """Readout input ``downconverter_frequency`` that is a literal instead of a
    pointer to its paired output's ``upconverter_frequency``.

    Hardware-wise a readout input and its paired output share one physical LO;
    production encodes the input as a JSON pointer to the output's
    ``upconverter_frequency`` so the constraint can't drift. This check resolves
    the paired output (via the wiring's ``rr.opx_input``/``opx_output``),
    compares the literal to that output's upconverter, and offers a one-click
    "convert to pointer" fix (``Finding.fix``):
      - equal  -> ``info``    (link them so they can't drift)
      - differs -> ``warning`` (already drifted; relinking changes the input)
    """
    findings: list[Finding] = []
    ports = root.get("ports")
    if not isinstance(ports, dict):
        return findings
    mw_inputs = ports.get("mw_inputs")
    if not isinstance(mw_inputs, dict):
        return findings
    mw_outputs = ports.get("mw_outputs") if isinstance(ports.get("mw_outputs"), dict) else {}
    pairs = _readout_io_pairs(root)

    def _up_freq(oc, of, op):
        node = mw_outputs
        for key in (oc, of, op):
            node = node.get(key) if isinstance(node, dict) else None
        return node.get("upconverter_frequency") if isinstance(node, dict) else None

    for ctrl, fems in mw_inputs.items():
        if not isinstance(fems, dict):
            continue
        for fem, portmap in fems.items():
            if not isinstance(portmap, dict):
                continue
            for port, cfg in portmap.items():
                if not isinstance(cfg, dict):
                    continue
                dc = cfg.get("downconverter_frequency")
                if not (isinstance(dc, (int, float)) and not isinstance(dc, bool)):
                    continue  # already a pointer / unset
                dp = f"ports.mw_inputs.{ctrl}.{fem}.{port}.downconverter_frequency"

                out = pairs.get((ctrl, fem, port))
                if out is None:
                    findings.append(Finding(
                        "info", "downconverter_literal", dp,
                        "downconverter_frequency is a literal, not a pointer to the "
                        "paired output's upconverter_frequency (may drift under edits); "
                        "no paired readout output found in the wiring to link to",
                        detail=repr(dc), jump_path=dp,
                    ))
                    continue

                oc, of, op = out
                pointer = f"#/ports/mw_outputs/{oc}/{of}/{op}/upconverter_frequency"
                up = _up_freq(oc, of, op)
                fix = {"action": "set_pointer", "dot_path": dp, "pointer": pointer,
                       "label": "Convert to pointer"}

                if not (isinstance(up, (int, float)) and not isinstance(up, bool)):
                    findings.append(Finding(
                        "info", "downconverter_literal", dp,
                        f"downconverter_frequency is a literal ({_ghz(dc)}); paired output "
                        f"{oc}/FEM {of}/port {op} has no concrete upconverter_frequency to "
                        f"compare, but they share one LO — link to keep them in lockstep",
                        detail=repr(dc), jump_path=dp, fix=fix,
                    ))
                    continue

                if float(up) == float(dc):
                    findings.append(Finding(
                        "info", "downconverter_literal", dp,
                        f"downconverter_frequency is a literal ({_ghz(dc)}) equal to its paired "
                        f"output's upconverter_frequency ({oc}/FEM {of}/port {op}) — convert to "
                        f"a pointer so they stay linked and can't drift under edits",
                        detail=repr(dc), jump_path=dp,
                        fix={**fix, "matches": True},
                    ))
                else:
                    findings.append(Finding(
                        "warning", "downconverter_literal", dp,
                        f"downconverter_frequency literal ({_ghz(dc)}) DIFFERS from its paired "
                        f"output's upconverter_frequency ({_ghz(up)}, {oc}/FEM {of}/port {op}) — "
                        f"they share one physical LO; converting to a pointer relinks them "
                        f"(input becomes {_ghz(up)})",
                        detail=repr(dc), jump_path=dp,
                        fix={**fix, "matches": False},
                    ))
    return findings


# ---------------------------------------------------------------------------
# Waveform DAC-range linter (the "sample out of [-1, 1]" class of crash)
# ---------------------------------------------------------------------------
#
# A pulse whose synthesized waveform sample falls outside its output port's DAC
# range makes ``machine.generate_config()`` / the QUA compiler reject the WHOLE
# config (e.g. ``Constant waveform '…readout.wf.I' sample (1.2056) is outside of
# the valid range ([-1.0, 1.0])``) — so a single bad amplitude on one qubit
# breaks every node run on the chip. This is the one place the linter flags a
# value *range*: it is not a physics choice but a hard hardware limit the
# compiler enforces, so the project's "trust researcher input" rule yields to
# "don't let a known crash through silently". It is fully derivable in-process —
# the config sample IS the resolved amplitude for constant (Square) pulses, and
# :mod:`core.waveform_synth` reproduces every shaped class bit-for-bit — so NO
# ``generate_config()`` subprocess is needed.
#
# The bound is PER OUTPUT-PORT-TYPE (else every legitimate LF-FEM flux pulse on
# an amplified port would false-positive): an MW-FEM output is normalized to
# ±1.0; LF-FEM output samples are in VOLTS (direct ±0.5 V, amplified ±2.5 V).
# ``full_scale_power_dbm`` is a SEPARATE downstream analog stage (checked in
# :mod:`core.spec_constraints`), never a sample multiplier — the two are
# independent. When the port (or its output_mode) can't be resolved the range
# check is simply skipped, so the pass never invents a false positive.

_MW_SAMPLE_BOUND = 1.0
_LF_DIRECT_BOUND = 0.5
_LF_AMPLIFIED_BOUND = 2.5
_SAMPLE_EPS = 1e-9  # an exactly-at-limit sample (and float dust) never trips

_CHANNEL_WIRING_ROLE = {"xy": "xy", "resonator": "rr", "z": "z"}

# Waveform findings cached per store at one ``mutation_seq``: lint_state feeds
# several surfaces (badge poll, topology health, report card), so the shaped-
# pulse synthesis is paid once per chip mutation rather than per call.
_wf_findings_cache: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _get_by_dotpath(root: dict, dot_path: str) -> Any:
    node: Any = root
    for seg in dot_path.split("."):
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        else:
            return None
    return node


def _dget(d: Any, key: str) -> Any:
    """``d[key]`` only when *d* is a dict — a truthy non-dict (a malformed wiring
    entry that is a string/int/list) returns None instead of raising. The bare
    ``(x or {}).get(...)`` idiom does NOT cover a truthy non-dict."""
    return d.get(key) if isinstance(d, dict) else None


def _as_dict(v: Any) -> dict:
    """*v* if it's a dict, else ``{}`` — so a malformed (truthy non-dict) config
    section never raises on ``.items()`` (``x or {}`` keeps a truthy non-dict)."""
    return v if isinstance(v, dict) else {}


def _abs_field_value(root: dict, ref: Any) -> float | None:
    """Resolve an absolute ``#/a/b/c`` pointer (or a literal) to a numeric value,
    following one extra hop if it lands on another ``#/`` pointer. None unless it
    ends on a real (non-bool) number — so unresolvable / None / pointer-to-missing
    simply skip."""
    if isinstance(ref, (int, float)) and not isinstance(ref, bool):
        return float(ref)
    for _ in range(3):
        if not (isinstance(ref, str) and ref.startswith("#/")):
            break
        ref = _get_by_dotpath(root, ref[2:].replace("/", "."))
    return float(ref) if isinstance(ref, (int, float)) and not isinstance(ref, bool) else None


def _qubit_name_from_ref(ref: Any) -> str | None:
    if isinstance(ref, str) and ref.startswith("#/qubits/"):
        rest = ref[len("#/qubits/"):].split("/")
        return rest[0] or None
    return None


def _starting_output_ref(merged: dict, row: dict) -> str | None:
    """The output-port pointer a pulse row plays through (``#/wiring/…`` or a
    direct ``#/ports/…``), taken from the channel component's ``opx_output``
    (real chips) with a wiring-role fallback (the diagnostics-test shorthand and
    partial chips). Returns None when the channel/port can't be located."""
    kind, owner, channel = row.get("owner_kind"), row.get("owner"), row.get("channel")

    comp_path: str | None = None
    if kind == "qubit":
        comp_path = f"qubits.{owner}.{channel}"
    elif kind == "pair" and channel == "coupler_flux_pulse":
        comp_path = f"qubit_pairs.{owner}.coupler"
    elif kind == "pair" and channel == "flux_pulse_qubit":
        ctrl = _qubit_name_from_ref(
            _dget(_dget(merged.get("qubit_pairs"), owner), "qubit_control"))
        comp_path = f"qubits.{ctrl}.z" if ctrl else None

    if comp_path:
        comp = _get_by_dotpath(merged, comp_path)
        if isinstance(comp, dict) and isinstance(comp.get("opx_output"), str):
            return comp["opx_output"]

    # Wiring fallback — _dget-guarded so a truthy non-dict entry (a malformed
    # partial chip from the drag-drop preview) yields None, never an AttributeError.
    wiring = merged.get("wiring")
    if kind == "qubit":
        role = _CHANNEL_WIRING_ROLE.get(channel)
        if role:
            r = _dget(_dget(_dget(_dget(wiring, "qubits"), owner), role), "opx_output")
            if isinstance(r, str):
                return r
    elif kind == "pair" and channel == "coupler_flux_pulse":
        node = _dget(_dget(wiring, "qubit_pairs"), owner)
        for role in ("c", "coupler"):
            r = _dget(_dget(node, role), "opx_output")
            if isinstance(r, str):
                return r
    return None


def _follow_to_ports_ref(merged: dict, ref: str, max_hops: int = 4) -> str | None:
    """Follow an absolute ``#/`` pointer chain to the terminal ``#/ports/…``
    string. ``opx_output`` points at a wiring entry that itself points at a port,
    so a hop or two lands on the ports pointer; we return that STRING (not the
    resolved port dict) so :func:`_parse_port_ref` can classify the FEM type."""
    cur: Any = ref
    for _ in range(max_hops):
        if not isinstance(cur, str) or not cur.startswith("#/"):
            return None
        if cur.startswith("#/ports/"):
            return cur
        node: Any = merged
        for seg in cur[2:].split("/"):
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            elif isinstance(node, list) and seg.isdigit() and int(seg) < len(node):
                node = node[int(seg)]  # seg.isdigit() is False for "-5" → no negative index
            else:
                return None
        cur = node
    return None


def _sample_bound(merged: dict, ports_ref: str) -> tuple[float | None, str | None]:
    """``(bound, fem_label)`` for a ``#/ports/<type>/<ctrl>/<fem>/<port>`` ref.

    MW-FEM → ±1.0 (normalized). LF-FEM analog output → ±0.5 V (direct) / ±2.5 V
    (amplified), read from the port's ``output_mode``. Unknown port type or
    unknown output_mode → ``(None, None)`` so the range check is skipped (no
    false positive — e.g. a 2.0 V flux pulse on an amplified port is legal)."""
    segs = ports_ref[2:].split("/")  # ['ports', type, ctrl, fem, port]
    if len(segs) < 2:
        return None, None
    if segs[1] == "mw_outputs":
        return _MW_SAMPLE_BOUND, "MW-FEM"
    if segs[1] == "analog_outputs":
        node: Any = merged.get("ports", {})
        for seg in segs[1:]:
            node = node.get(seg) if isinstance(node, dict) else None
        mode = node.get("output_mode") if isinstance(node, dict) else None
        if mode == "amplified":
            return _LF_AMPLIFIED_BOUND, "LF-FEM amplified"
        if mode == "direct":
            return _LF_DIRECT_BOUND, "LF-FEM direct"
        return None, None
    return None, None


def _is_constant_pulse(row: dict) -> bool:
    qc = row.get("qclass")
    qc = qc if isinstance(qc, str) else ""   # a malformed non-string __class__ → not constant
    return qc.endswith("SquarePulse") or qc.endswith("SquareReadoutPulse")


def _pulse_peak(store, row: dict) -> tuple[float | None, str | None]:
    """``(peak_abs_sample, hard_error)``. ``peak`` None ⇒ not evaluable (skip).

    Constant (Square / SquareReadout) pulses: the config sample IS the resolved
    amplitude, so ``|amplitude|`` with no synthesis. Everything else is
    synthesized via :mod:`core.waveform_synth` and the peak is the largest
    ``|I|``/``|Q|`` sample. A synth failure caused purely by an unresolved
    pointer / missing param is benign here (covered by the dangling-pointer
    check) → ``(None, None)``; a genuine value error (sigma=0, odd flat parity,
    DRAG anharmonicity=0, …) returns ``(None, message)`` so it surfaces as a
    config-build crash."""
    if _is_constant_pulse(row):
        amp = row.get("amplitude")
        if isinstance(amp, (int, float)) and not isinstance(amp, bool):
            return abs(float(amp)), None
        return None, None

    try:
        payload = waveform_synth.synth_for_operation(store, row["path"])
    except Exception:  # pragma: no cover - synth must never crash a lint
        return None, None

    if payload.get("class_match") == "leaf":
        # The class matched the catalog by NAME only — a foreign module path
        # (churned QM stack, fork, or an unrelated same-named class). Our
        # transcription may not be that class's math: render it elsewhere,
        # but never fabricate a config-crash / DAC-range finding from it.
        # Verify-vs-config is the authoritative check for these.
        # NB: "env" (module home verified by the active env overlay —
        # pulse_catalog.apply_env_overlay) is fully-known like "exact"/"alias"
        # and deliberately passes this gate: env-verified pulses re-enter
        # DAC-range linting.
        return None, None

    if payload.get("ok"):
        peak = 0.0
        for arr in (payload.get("i"), payload.get("q")):
            if arr:
                m = max((abs(v) for v in arr), default=0.0)
                if m > peak:
                    peak = m
        return peak, None

    if not payload.get("spec_key"):
        # The pulse class isn't recognized by the synthesizer (a custom/builder
        # class). We can't evaluate it — but it is NOT an invalid waveform, so
        # never report it (only a recognized class that fails on its params, which
        # carries a spec_key, is a genuine generate_config crash).
        return None, None

    pe = payload.get("param_errors") or {}
    if pe and all(("pointer" in str(m).lower() or "missing" in str(m).lower()
                   or "preview" in str(m).lower())
                  for m in pe.values()):
        # unresolved pointer / missing param (covered elsewhere) or "too long for
        # preview" (a legal long waveform — NOT a generate_config crash) → skip
        return None, None
    return None, payload.get("error")


def _waveform_findings(store) -> list[Finding]:
    """Out-of-DAC-range (and invalid-parameter) waveform samples — see the
    section comment above. Pure + in-process (numpy/scipy synth, no QM stack)."""
    root = store.merged if isinstance(getattr(store, "merged", None), dict) else {}
    if not root:
        return []
    try:
        # used_by (the reverse-pointer index) isn't needed for the DAC-range check
        # — skip it; this lint runs on every edit, so the saving compounds.
        rows = pulse_index.list_pulses(root, with_used_by=False)
    except Exception:  # pragma: no cover - never let enumeration crash a lint
        return []

    findings: list[Finding] = []
    for row in rows:
        if row.get("is_alias"):
            continue  # the alias target row is checked on its own
        path = row.get("path") or ""

        peak, hard_error = _pulse_peak(store, row)
        if hard_error:
            findings.append(Finding(
                "error", "waveform_invalid", path,
                "pulse parameters produce an invalid waveform — "
                "machine.generate_config() would raise",
                detail=str(hard_error), jump_path=path,
            ))
            continue
        if peak is None:
            continue

        ref = _starting_output_ref(root, row)
        if not ref:
            continue
        ports_ref = _follow_to_ports_ref(root, ref)
        if not ports_ref:
            continue
        bound, fem_label = _sample_bound(root, ports_ref)
        if bound is None:
            continue

        if peak > bound + _SAMPLE_EPS:
            unit = " V" if fem_label and fem_label.startswith("LF") else ""
            parsed = _parse_port_ref(ports_ref)
            findings.append(Finding(
                "error", "waveform_range", path,
                f"waveform sample peak {peak:.4g}{unit} exceeds the {fem_label} "
                f"output range ±{bound:g}{unit} — machine.generate_config()/the "
                f"QUA compiler rejects samples outside it",
                detail=f"peak {peak!r} vs ±{bound} ({ports_ref})",
                jump_path=f"{path}.amplitude",
                port_key=_port_key(parsed) if parsed else None,
            ))
    return findings


def _waveform_findings_cached(store) -> list[Finding]:
    """:func:`_waveform_findings` memoized per store at its ``mutation_seq``."""
    seq = getattr(store, "mutation_seq", None)
    hit = _wf_findings_cache.get(store)
    if hit is not None and hit[0] == seq:
        return hit[1]
    out = _waveform_findings(store)
    try:
        _wf_findings_cache[store] = (seq, out)
    except TypeError:  # pragma: no cover - store not weak-referenceable
        pass
    return out


# ---------------------------------------------------------------------------
# MW-FEM carrier reachability — RF_frequency vs the FEM's physical frequency
# reach and its per-port LO (the "this carrier can't be produced" class).
# ---------------------------------------------------------------------------
#
# These complement the LO/band checks in :func:`_coupling_findings` (which check
# the port ``upconverter_frequency`` against its band) by checking the actual
# **carrier** the channel plays — ``RF_frequency`` — which on real chips is a
# literal (the LO and IF are pointers: ``#./upconverter_frequency`` and
# ``#./inferred_intermediate_frequency``), so the stored ``intermediate_frequency``
# value-spec check never fires on production data. Computing IF = RF − LO
# in-process is the only way to see it.
#
# Hardware reach (opx1000_fems.md L62-63, mwfem_port_spec.csv):
#   * MW-FEM digital upconverters drive 50 MHz – 10.5 GHz at the OUTPUT.
#   * MW-FEM analog inputs receive 2 – 10.5 GHz (readout).
# A carrier outside these ranges can't be produced / received by the FEM at all,
# regardless of band.
MW_OUTPUT_FREQ_RANGE_HZ = (50e6, 10.5e9)   # drive (xy / cross-resonance)
MW_INPUT_FREQ_RANGE_HZ = (2e9, 10.5e9)     # readout (resonator)

# The quadrature DAC/ADC run at 1 GSa/s, so the element intermediate_frequency
# (RF − port LO) has a hard ±500 MHz Nyquist ceiling — generate_config()/the QUA
# compiler reject an element IF beyond it (opx1000_fems.md L34/L35: an element on a
# 1 GSa/s port is "limited to a frequency of 500 MHz"). NOTE we deliberately do NOT
# warn on the ~800 MHz "sub-band" figure (±400 MHz around each DUC, L84): that is a
# soft architecture description, and real production chips routinely run |IF| up to
# ~485 MHz (the very reason IF_LIMIT_XY_HZ is 500, not 440 MHz) — an advisory there
# fires on essentially every healthy chip, which violates this module's zero-false-
# positive rule. Only the genuine Nyquist ceiling is checked.
MW_IF_NYQUIST_HZ = spec_constraints.IF_LIMIT_XY_HZ   # 500e6 (single source of truth)


def _mw_output_port_of(root: dict, comp: Any) -> tuple[dict | None, str | None]:
    """Resolve a channel component's ``opx_output`` to its MW-FEM **output** port
    dict + the terminal ``#/ports/…`` ref string. ``(None, None)`` unless it
    resolves to an ``MWFEMAnalogOutputPort`` (so LF-FEM / Octave / unresolved ports
    are silently skipped — never a false positive)."""
    ref = comp.get("opx_output") if isinstance(comp, dict) else None
    if not isinstance(ref, str):
        return None, None
    ports_ref = _follow_to_ports_ref(root, ref)
    if not ports_ref:
        return None, None
    port = _get_by_dotpath(root, ports_ref[2:].replace("/", "."))
    if not isinstance(port, dict):
        return None, None
    if not str(port.get("__class__") or "").endswith("MWFEMAnalogOutputPort"):
        return None, None
    return port, ports_ref


def _channel_lo_hz(port: dict, comp: Any) -> float | None:
    """The LO (Hz) a channel sees on its MW-FEM output port: the scalar
    ``upconverter_frequency``, or — for a multi-DUC port — the ``upconverters``
    entry the channel's ``upconverter`` index selects (default 1). None when no
    concrete LO can be read (so the IF check is skipped, not faked)."""
    lo = port.get("upconverter_frequency")
    if _isnum(lo):
        return float(lo)
    ucs = port.get("upconverters")
    if isinstance(ucs, dict) and ucs:
        idx = comp.get("upconverter") if isinstance(comp, dict) else None
        # JSON object keys are strings ("1"); the channel index is an int (1).
        cand = [str(idx), idx, "1", 1]
        key = next((k for k in cand if k in ucs), next(iter(ucs)))
        uc = ucs.get(key)
        if isinstance(uc, dict) and _isnum(uc.get("frequency")):
            return float(uc["frequency"])
    return None


# (channel key on the qubit, human label, kind, absolute RF range)
_MW_CARRIER_CHANNELS = [
    ("xy", "qubit drive", "drive", MW_OUTPUT_FREQ_RANGE_HZ),
    ("resonator", "readout", "readout", MW_INPUT_FREQ_RANGE_HZ),
]


def _mw_carrier_findings(root: dict) -> list[Finding]:
    """MW-FEM carrier (``RF_frequency``) reachability: absolute FEM frequency range
    + computed IF = RF − port-LO against the ±500 MHz Nyquist ceiling.

    Literal RF + concrete MW-FEM port LO only — any pointer / None / non-MW-FEM port
    skips (zero false positives; real chips keep RF literal). When the IF already
    blows past the Nyquist ceiling the redundant absolute-range warning for the same
    channel is suppressed (one root cause, one finding). The IF floor (≤ 5 MHz,
    readout demod) stays in :func:`_resonator_if_floor_findings` — these add the
    ceiling + absolute-range that were missing."""
    findings: list[Finding] = []
    qubits = root.get("qubits")
    if not isinstance(qubits, dict):
        return findings

    for qname, q in qubits.items():
        if not isinstance(q, dict):
            continue
        for chan, label, kind, (lo_b, hi_b) in _MW_CARRIER_CHANNELS:
            comp = q.get(chan)
            rf = comp.get("RF_frequency") if isinstance(comp, dict) else None
            if not _isnum(rf):
                continue                       # pointer / None / absent → skip
            port, ports_ref = _mw_output_port_of(root, comp)
            if port is None:
                continue                       # not an MW-FEM port → skip
            rf = float(rf)
            rf_dp = f"qubits.{qname}.{chan}.RF_frequency"
            parsed = _parse_port_ref(ports_ref)
            pk = _port_key(parsed) if parsed else None

            # IF = RF − port LO vs the hard ±500 MHz Nyquist ceiling.
            if_ceiling_hit = False
            lo = _channel_lo_hz(port, comp)
            if lo is not None:
                aif = abs(rf - lo)
                if aif > MW_IF_NYQUIST_HZ:
                    if_ceiling_hit = True
                    findings.append(Finding(
                        "error", "connectivity_if_ceiling", rf_dp,
                        f"{label} intermediate frequency |{aif / 1e6:.6g} MHz| "
                        f"(RF_frequency {rf / 1e9:.6g} GHz − port LO {lo / 1e9:.6g} GHz) "
                        f"exceeds the MW-FEM ±500 MHz Nyquist limit (1 GSa/s) — "
                        f"generate_config()/the QUA compiler reject an element "
                        f"intermediate_frequency beyond ±500 MHz",
                        detail=f"RF {rf!r} − LO {lo!r} Hz", jump_path=rf_dp,
                        port_key=pk))

            # Absolute carrier reach (independent of the LO). Suppressed when the IF
            # ceiling already fired — that is the same fault stated more precisely.
            if not if_ceiling_hit and not (lo_b <= rf <= hi_b):
                where = "produce" if kind == "drive" else "receive"
                io = "output" if kind == "drive" else "input"
                findings.append(Finding(
                    "warning", "connectivity_carrier_range", rf_dp,
                    f"{label} carrier RF_frequency {rf / 1e9:.6g} GHz is outside the "
                    f"MW-FEM {io} frequency range [{lo_b / 1e9:g}, {hi_b / 1e9:g}] GHz "
                    f"— the FEM cannot physically {where} this carrier",
                    detail=repr(comp.get("RF_frequency")), jump_path=rf_dp,
                    port_key=pk))
    return findings


def _pair_drive_carrier_findings(store) -> list[Finding]:
    """CR/ZZ pair-drive effective-IF reachability (docs/54).

    The pair drive's ``intermediate_frequency`` is a runtime-only ``#./``
    property no other check can see — ``cr_semantics.effective_frequencies``
    emulates it (target RF − LO, + detuning for ZZ), resolving the pointer
    chain through the port's per-upconverter LO. Two tiers:

    - error ``pair_drive_if_ceiling``   — |IF| > 500 MHz (the same Nyquist
      wall as ``connectivity_if_ceiling``; the QUA compiler rejects it);
    - warning ``pair_drive_if_soft``    — 400 MHz < |IF| ≤ 500 MHz, CR/ZZ
      channels only: the customer's own populate scripts assert |IF| < 400
      MHz for these tones (advisory — corpus-proven silent on healthy chips).

    Unresolvable inputs skip silently (dangling pointers have their own
    findings family; never guess). Non-CR chips exit on the is_cr_chip gate.
    """
    from quam_state_manager.core import cr_semantics

    findings: list[Finding] = []
    root = store.merged
    if not cr_semantics.is_cr_chip(root):
        return findings
    pairs = root.get("qubit_pairs")
    if not isinstance(pairs, dict):
        return findings
    for pid, pobj in pairs.items():
        if not isinstance(pobj, dict):
            continue
        for kind, label in (("cr", "cross-resonance"), ("zz", "ZZ")):
            eff = cr_semantics.effective_frequencies(store, pid, channel=kind)
            if eff is None or eff.if_hz is None:
                continue
            if kind == "cr":
                chan_key = next((k for k in cr_semantics.CR_CHANNEL_KEYS
                                 if isinstance(pobj.get(k), dict)), None)
            else:
                zz = cr_semantics.zz_channel(pobj)
                chan_key = zz[0] if zz else None
            if chan_key is None:                    # pragma: no cover - guarded above
                continue
            dp = f"qubit_pairs.{pid}.{chan_key}.intermediate_frequency"
            aif = abs(eff.if_hz)
            if aif > MW_IF_NYQUIST_HZ:
                findings.append(Finding(
                    "error", "pair_drive_if_ceiling", dp,
                    f"{pid} {label} drive effective IF |{aif / 1e6:.6g} MHz| "
                    f"(target RF {eff.target_rf_hz / 1e9:.6g} GHz − LO "
                    f"{eff.lo_hz / 1e9:.6g} GHz) exceeds the MW-FEM ±500 MHz "
                    f"Nyquist limit — the QUA compiler rejects it",
                    detail=f"formula {eff.formula}", jump_path=dp))
            elif aif > MW_MAX_ABS_IF_HZ:
                findings.append(Finding(
                    "warning", "pair_drive_if_soft", dp,
                    f"{pid} {label} drive effective IF |{aif / 1e6:.6g} MHz| "
                    f"exceeds the 400 MHz bound the QM populate scripts assert "
                    f"for pair drives — retune the port's upconverter-2 LO "
                    f"closer to the target frequency",
                    detail=f"formula {eff.formula}", jump_path=dp))
    return findings


def _f01_range_findings(root: dict) -> list[Finding]:
    """A qubit's ``f_01`` outside the MW-FEM drive frequency reach (50 MHz – 10.5
    GHz) when its ``xy`` channel is on an MW-FEM output port.

    ``f_01`` is physics bookkeeping (never read by generate_config — see
    :func:`_frequency_consistency_findings`), so a value the drive hardware can't
    reach doesn't crash a build; but it does mean the stored qubit frequency can't
    be driven by its current hardware, which is almost always a stale/fat-fingered
    edit (the user's "f_01 jumped past 10.5 GHz" case). Warning, never error;
    literal ``f_01`` + confirmed MW-FEM xy port only (zero false positives)."""
    findings: list[Finding] = []
    qubits = root.get("qubits")
    if not isinstance(qubits, dict):
        return findings
    lo_b, hi_b = MW_OUTPUT_FREQ_RANGE_HZ
    for qname, q in qubits.items():
        if not isinstance(q, dict):
            continue
        f01 = q.get("f_01")
        if not _isnum(f01):
            continue
        port, _ = _mw_output_port_of(root, q.get("xy"))
        if port is None:
            continue                            # drive not on an MW-FEM → skip
        f01 = float(f01)
        if lo_b <= f01 <= hi_b:
            continue
        dp = f"qubits.{qname}.f_01"
        findings.append(Finding(
            "warning", "value_spec_f01_range", dp,
            f"f_01 {f01 / 1e9:.6g} GHz is outside the MW-FEM drive frequency range "
            f"[{lo_b / 1e9:g}, {hi_b / 1e9:g}] GHz — this qubit frequency can't be "
            f"driven by its current hardware (f_01 is not read by generate_config, so "
            f"this is a stored-value sanity check, not a build error)",
            detail=repr(q.get("f_01")), jump_path=dp))
    return findings


# ---------------------------------------------------------------------------
# LF-FEM analog-output usable modulation bandwidth.
# ---------------------------------------------------------------------------
# opx1000_fems.md L54-59: the LF-FEM analog output's usable bandwidth depends on
# its output_mode — direct = DC–750 MHz, amplified = DC–330 MHz. AND L34: an
# element on a 1 GSa/s output port (``sampling_rate`` 1e9, the default) is further
# limited to 500 MHz. Flux / z / coupler lines are normally DC (``intermediate_
# frequency`` None or 0 on every real chip), so this only bites a hand-entered
# modulated IF on an LF-FEM output — zero false positives on production data.
LF_OUTPUT_BW_DIRECT_HZ = 750e6
LF_OUTPUT_BW_AMPLIFIED_HZ = 330e6
LF_OUTPUT_BW_1GSPS_HZ = 500e6


def _iter_channels(root: dict):
    """Yield ``(section, owner, channel, comp)`` for every direct-child channel
    dict (one carrying an ``opx_output``) under qubits / qubit_pairs — xy, z,
    resonator, coupler, cross_resonance, …. The caller gates on the resolved port
    type, so a channel on the 'wrong' FEM is simply skipped."""
    for section in ("qubits", "qubit_pairs"):
        node = root.get(section)
        if not isinstance(node, dict):
            continue
        for owner, od in node.items():
            if not isinstance(od, dict):
                continue
            for chan, comp in od.items():
                if isinstance(comp, dict) and "opx_output" in comp:
                    yield section, owner, chan, comp


def _lffem_output_port_of(root: dict, comp: Any) -> dict | None:
    """The channel's ``opx_output`` resolved to its LF-FEM **output** port dict, or
    None unless it is an ``LFFEMAnalogOutputPort`` (MW / Octave / unresolved skip)."""
    ref = comp.get("opx_output") if isinstance(comp, dict) else None
    if not isinstance(ref, str):
        return None
    ports_ref = _follow_to_ports_ref(root, ref)
    if not ports_ref:
        return None
    port = _get_by_dotpath(root, ports_ref[2:].replace("/", "."))
    if not isinstance(port, dict):
        return None
    if not str(port.get("__class__") or "").endswith("LFFEMAnalogOutputPort"):
        return None
    return port


def _lffem_output_bw_findings(root: dict) -> list[Finding]:
    """A channel ``intermediate_frequency`` exceeding its LF-FEM output's usable
    modulation bandwidth (direct 750 / amplified 330 MHz, and 500 MHz on a 1 GSa/s
    port). Literal IF + confirmed LF-FEM output port only (zero false positives —
    real flux lines store no IF). When the port omits ``output_mode``/``sampling_
    rate`` only the bound that can be determined is applied (the 1 GSa/s default
    gives 500 MHz); if none can be determined the channel is skipped, never faked."""
    findings: list[Finding] = []
    for section, owner, chan, comp in _iter_channels(root):
        ifq = comp.get("intermediate_frequency")
        if not _isnum(ifq):
            continue                            # None / 0-ish pointer / absent → skip
        port = _lffem_output_port_of(root, comp)
        if port is None:
            continue                            # not an LF-FEM output → skip
        mode = port.get("output_mode")
        rate = port.get("sampling_rate")
        limits: list[float] = []
        # sampling_rate defaults to 1 GSa/s when omitted → the 500 MHz PPU cap.
        if rate is None or (_isnum(rate) and float(rate) == 1e9):
            limits.append(LF_OUTPUT_BW_1GSPS_HZ)
        if mode == "direct":
            limits.append(LF_OUTPUT_BW_DIRECT_HZ)
        elif mode == "amplified":
            limits.append(LF_OUTPUT_BW_AMPLIFIED_HZ)
        if not limits:
            continue                            # 2 GSa/s + unknown mode → can't bound
        limit = min(limits)
        aif = abs(float(ifq))
        if aif <= limit:
            continue
        dp = f"{section}.{owner}.{chan}.intermediate_frequency"
        ports_ref = _follow_to_ports_ref(root, comp["opx_output"])
        parsed = _parse_port_ref(ports_ref) if ports_ref else None
        mode_lbl = f"{mode} mode" if mode in ("direct", "amplified") else "1 GSa/s"
        findings.append(Finding(
            "warning", "connectivity_lf_output_bw", dp,
            f"{chan} intermediate_frequency |{aif / 1e6:.6g} MHz| exceeds the LF-FEM "
            f"{mode_lbl} usable output bandwidth ({limit / 1e6:.0f} MHz) — the "
            f"modulated tone falls outside the analog passband",
            detail=repr(ifq), jump_path=dp,
            port_key=_port_key(parsed) if parsed else None))
    return findings


# Sub-kHz |IF| is representational float dust on an intended zero-IF readout
# (RF stored as LO + 1e-4 etc.), well below any deliberate readout IF — treat it
# as zero-IF (exempt), not a demod-floor case. Mirrors the ``_SAMPLE_EPS`` idea.
_IF_ZERO_EPS_HZ = 1e3


def _resonator_if_floor_findings(root: dict) -> list[Finding]:
    """Readout IF within the MW-FEM measurable floor (0 < |IF| ≤ 5 MHz, inclusive,
    can't be demodulated). IF is derived in-process as ``RF_frequency`` − the
    resolved ``upconverter_frequency`` of the resonator's MW-FEM output port — the
    stored ``intermediate_frequency`` is the ``#./inferred_intermediate_frequency``
    pointer (and is absent entirely on some chips, e.g. the variantb family), so it
    can't be range-checked or reliably jumped to directly; the finding navigates to
    ``RF_frequency`` (the literal the user actually edits to move the IF) instead.
    Resonator/readout only; literal RF + LO only (any pointer / None / non-MW-FEM
    port → skip), so it never false-positives (real chips sit ≥ 6 MHz)."""
    findings: list[Finding] = []
    qubits = root.get("qubits")
    if not isinstance(qubits, dict):
        return findings
    for qname, q in qubits.items():
        if not isinstance(q, dict):
            continue
        res = q.get("resonator")
        if not isinstance(res, dict):
            continue
        rf = res.get("RF_frequency")
        ref = res.get("opx_output")
        if not (isinstance(rf, (int, float)) and not isinstance(rf, bool)):
            continue
        if not isinstance(ref, str):
            continue
        ports_ref = _follow_to_ports_ref(root, ref)
        if not ports_ref:
            continue
        port = _get_by_dotpath(root, ports_ref[2:].replace("/", "."))
        if not isinstance(port, dict):
            continue
        if not str(port.get("__class__") or "").endswith("MWFEMAnalogOutputPort"):
            continue
        up = port.get("upconverter_frequency")
        if not (isinstance(up, (int, float)) and not isinstance(up, bool)):
            continue
        if_hz = abs(float(rf) - float(up))
        # |IF| at/under _IF_ZERO_EPS_HZ is a deliberate zero-IF / baseband readout
        # (any sub-kHz value is just float dust around 0), which generate_config()
        # fully supports — only the narrow non-zero sub-floor band is hard to
        # demodulate, and even then it is a measurement-quality note (warning,
        # badge-only), not a compile crash, so this never errors or banners. The
        # gate is ``<= 5 MHz`` (inclusive): opx1000_fems.md L151 states IFs that are
        # ``<= |5| MHz`` cannot be measured, so an exactly-5 MHz IF is NOT fine.
        if _IF_ZERO_EPS_HZ <= if_hz <= spec_constraints.IF_FLOOR_MW_HZ:
            # Navigate to RF_frequency (always present, the field the user edits);
            # intermediate_frequency is only the human-readable location label and
            # may be absent on some chips.
            loc = f"qubits.{qname}.resonator.intermediate_frequency"
            jp = f"qubits.{qname}.resonator.RF_frequency"
            # kHz below 1 MHz, else MHz at full precision — so the rendered value
            # can never collide with the "5 MHz floor" label (e.g. avoid "|5 MHz|
            # is below the 5 MHz floor").
            if_disp = (f"{if_hz / 1e3:.0f} kHz" if if_hz < 1e6
                       else f"{if_hz / 1e6:.6g} MHz")
            findings.append(Finding(
                "warning", "value_spec_if_floor", loc,
                f"readout IF |{if_disp}| is within the MW-FEM 5 MHz demodulation "
                f"floor (|IF| ≤ 5 MHz) and cannot be measured "
                f"(RF_frequency − port upconverter_frequency)",
                detail=f"RF {rf!r} − LO {up!r} Hz", jump_path=jp,
            ))
    return findings


_DOWNCONVERTER_MIN_SPACING_HZ = 10e6


def _downconverter_spacing_findings(root: dict) -> list[Finding]:
    """Two MW-FEM inputs on one FEM whose downconverter LOs are < 10 MHz apart.

    The MW-FEM has two analog input ports, each with its own downconverter LO. If
    both are used to read distinct readout lines simultaneously, their LOs must
    differ by ≥ 10 MHz (opx1000_fems.md L148, "Optimized Readout") — too-close LOs
    interfere in the shared input processing. ``downconverter_frequency`` is
    usually a JSON pointer to the paired output's ``upconverter_frequency``, so it
    is resolved first; only resolvable numeric pairs are compared (None / dangling
    skip). A 0 Hz gap (both inputs on one shared LO) is flagged too: reading two
    physically distinct readout lines on one LO is not the recommended layout."""
    findings: list[Finding] = []
    ports = root.get("ports")
    mw_inputs = ports.get("mw_inputs") if isinstance(ports, dict) else None
    if not isinstance(mw_inputs, dict):
        return findings

    groups: dict[tuple, list[tuple]] = {}   # (ctrl, fem) -> [(port, freq), ...]
    for ctrl, fems in mw_inputs.items():
        if not isinstance(fems, dict):
            continue
        for fem, portmap in fems.items():
            if not isinstance(portmap, dict):
                continue
            for port, cfg in portmap.items():
                if not isinstance(cfg, dict):
                    continue
                freq = _abs_field_value(root, cfg.get("downconverter_frequency"))
                if freq is not None:
                    groups.setdefault((ctrl, fem), []).append((port, freq))

    for (ctrl, fem), entries in groups.items():
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                (p1, f1), (p2, f2) = entries[i], entries[j]
                gap = abs(f1 - f2)
                if gap < _DOWNCONVERTER_MIN_SPACING_HZ:
                    msg = (f"two MW-FEM input downconverter LOs on {ctrl}/FEM {fem} "
                           f"(ports {p1} & {p2}) are {gap / 1e6:.3g} MHz apart — if both "
                           f"inputs read simultaneously they should differ by ≥ 10 MHz")
                    for p in (p1, p2):
                        parsed = ("mw_inputs", ctrl, fem, p)
                        dp = f"ports.mw_inputs.{ctrl}.{fem}.{p}.downconverter_frequency"
                        findings.append(Finding(
                            "warning", "connectivity_downconverter", _port_label(parsed),
                            msg, detail=f"{f1 / 1e9:.6g} GHz vs {f2 / 1e9:.6g} GHz",
                            jump_path=dp, port_key=_port_key(parsed)))
    return findings


# ---------------------------------------------------------------------------
# QM config linter
# ---------------------------------------------------------------------------

def lint_config(config: dict) -> list[Finding]:
    """Lint a QM config dict (from ``generate_config()`` or a dropped config.json).

    Reuses the pure reference-walkers in :mod:`core.config_view` to find both
    broken references (a referenced pulse/waveform/weight/mixer/controller
    that isn't defined) and orphans (defined but never referenced).
    """
    findings: list[Finding] = []
    if not isinstance(config, dict):
        return findings

    elements = _as_dict(config.get("elements"))
    pulses = _as_dict(config.get("pulses"))
    waveforms = _as_dict(config.get("waveforms"))
    iws = _as_dict(config.get("integration_weights"))
    mixers = _as_dict(config.get("mixers"))
    controllers = _as_dict(config.get("controllers"))

    # NOTE: a missing top-level 'version' key is intentionally NOT flagged.
    # The in-house generator output doesn't carry it and it isn't required;
    # the lint used to emit a noisy info finding for it (feedback Config #1).

    # --- Missing references (errors) ---------------------------------------
    for ek, elem in elements.items():
        if not isinstance(elem, dict):
            continue
        ops = elem.get("operations")
        if isinstance(ops, dict):
            for op, pname in ops.items():
                if isinstance(pname, str) and pname not in pulses:
                    findings.append(Finding(
                        "error", "config_missing_pulse",
                        f"elements.{ek}.operations.{op}",
                        f"operation '{op}' references undefined pulse '{pname}'",
                        detail=pname,
                    ))
        mix_obj = elem.get("mixInputs") or elem.get("MWInput") or {}
        if isinstance(mix_obj, dict):
            mn = mix_obj.get("mixer")
            if isinstance(mn, str) and mn not in mixers:
                findings.append(Finding(
                    "error", "config_missing_mixer", f"elements.{ek}",
                    f"element references undefined mixer '{mn}'", detail=mn,
                ))
        for con, hint in _element_controller_refs(elem):
            if controllers and con not in controllers:
                findings.append(Finding(
                    "error", "config_missing_controller", f"elements.{ek}.{hint}",
                    f"element wired to controller '{con}' which is not in 'controllers'",
                    detail=con,
                ))

    for pk, pulse in pulses.items():
        if not isinstance(pulse, dict):
            continue
        wfs = pulse.get("waveforms")
        if isinstance(wfs, dict):
            for ch, wname in wfs.items():
                if isinstance(wname, str) and wname not in waveforms:
                    findings.append(Finding(
                        "error", "config_missing_waveform",
                        f"pulses.{pk}.waveforms.{ch}",
                        f"pulse references undefined waveform '{wname}'", detail=wname,
                    ))
        elif isinstance(wfs, str) and wfs not in waveforms:
            findings.append(Finding(
                "error", "config_missing_waveform", f"pulses.{pk}.waveforms",
                f"pulse references undefined waveform '{wfs}'", detail=wfs,
            ))
        iwref = pulse.get("integration_weights")
        if isinstance(iwref, dict):
            for ch, iname in iwref.items():
                if isinstance(iname, str) and iname not in iws:
                    findings.append(Finding(
                        "error", "config_missing_iw",
                        f"pulses.{pk}.integration_weights.{ch}",
                        f"pulse references undefined integration_weights '{iname}'",
                        detail=iname,
                    ))

    # --- Orphans (warnings) ------------------------------------------------
    ref_pulses = set(config_view._pulse_names_referenced_by(elements, list(elements.keys())))
    for pk in pulses:
        if pk not in ref_pulses:
            findings.append(Finding(
                "warning", "config_orphan_pulse", f"pulses.{pk}",
                "pulse is defined but never referenced by any element"))

    ref_wfs = set(config_view._waveform_names_referenced_by(pulses, list(pulses.keys())))
    for wk in waveforms:
        if wk not in ref_wfs:
            findings.append(Finding(
                "warning", "config_orphan_waveform", f"waveforms.{wk}",
                "waveform is defined but never referenced by any pulse"))

    ref_iws: set[str] = set()
    for pulse in pulses.values():
        if isinstance(pulse, dict) and isinstance(pulse.get("integration_weights"), dict):
            ref_iws.update(v for v in pulse["integration_weights"].values() if isinstance(v, str))
    for ik in iws:
        if ik not in ref_iws:
            findings.append(Finding(
                "warning", "config_orphan_iw", f"integration_weights.{ik}",
                "integration weight is defined but never referenced"))

    ref_mixers: set[str] = set()
    for elem in elements.values():
        if isinstance(elem, dict):
            mo = elem.get("mixInputs") or elem.get("MWInput") or {}
            if isinstance(mo, dict) and isinstance(mo.get("mixer"), str):
                ref_mixers.add(mo["mixer"])
    for mk in mixers:
        if mk not in ref_mixers:
            findings.append(Finding(
                "warning", "config_orphan_mixer", f"mixers.{mk}",
                "mixer is defined but never referenced"))

    findings.extend(_config_value_findings(config))
    return _ordered(findings)


# Config-dict value bounds the QUA compiler enforces but that only EXIST after
# generate_config(): mixer correction matrix, integration-weight values/durations,
# time-tagging thresholds. Deliberately NOT checked: the integration-weight
# ADC×weight / running-sum overflow rules (input-data dependent → would false-
# positive from the config alone), and IF=RF−LO (done in Tier-1 from state, where
# RF_frequency is first-class; the config carries IF but not the RF target).
_MIXER_CORR_MIN = -2.0
_MIXER_CORR_MAX = 2.0 - 2 ** -16        # 1.999984741210938
_IW_VALUE_ABS_MAX = 2048                 # weight range ±2048 (step 2^-15)
_TIMETAG_MIN = -2048                     # signed 12-bit ADC
_TIMETAG_MAX = 2047                      # asymmetric — +2048 is out of range


def _is_real_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _config_value_findings(config: dict) -> list[Finding]:
    findings: list[Finding] = []

    # Mixer correction matrix [C00,C01,C10,C11] each in [-2, 2 - 2^-16].
    for mname, entries in _as_dict(config.get("mixers")).items():
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            corr = entry.get("correction") if isinstance(entry, dict) else None
            if not isinstance(corr, (list, tuple)):
                continue
            for j, v in enumerate(corr):
                if _is_real_num(v) and (v < _MIXER_CORR_MIN - 1e-9
                                        or v > _MIXER_CORR_MAX + 1e-9):
                    findings.append(Finding(
                        "error", "config_mixer_correction",
                        f"mixers.{mname}[{i}].correction[{j}]",
                        f"mixer correction element {v:g} is outside the valid "
                        f"range [-2, 2-2^-16]; the QUA compiler rejects it",
                        detail=repr(v)))

    # Integration weights: value magnitude ≤ 2048, per-tuple duration a positive
    # multiple of 4 ns. Tuple form [[value, duration], …]; a bare-scalar form
    # carries only values (no per-sample duration).
    for iname, iw in _as_dict(config.get("integration_weights")).items():
        if not isinstance(iw, dict):
            continue
        for comp in ("cosine", "sine"):
            seq = iw.get(comp)
            if not isinstance(seq, list):
                continue
            for k, item in enumerate(seq):
                loc = f"integration_weights.{iname}.{comp}[{k}]"
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    val, dur = item[0], item[1]
                    if _is_real_num(val) and abs(val) > _IW_VALUE_ABS_MAX:
                        findings.append(Finding(
                            "warning", "config_iw_value", loc,
                            f"integration weight value {val:g} exceeds |{_IW_VALUE_ABS_MAX}|",
                            detail=repr(val)))
                    if _is_real_num(dur) and float(dur).is_integer():
                        di = int(dur)
                        if di <= 0 or di % 4 != 0:
                            findings.append(Finding(
                                "warning", "config_iw_duration", loc,
                                f"integration weight duration {di} must be a positive "
                                f"multiple of 4 ns", detail=repr(dur)))
                elif _is_real_num(item) and abs(item) > _IW_VALUE_ABS_MAX:
                    findings.append(Finding(
                        "warning", "config_iw_value", loc,
                        f"integration weight value {item:g} exceeds |{_IW_VALUE_ABS_MAX}|",
                        detail=repr(item)))

    # Time-tagging signal/derivative thresholds in [-2048, 2047] (signed 12-bit).
    for ename, el in _as_dict(config.get("elements")).items():
        if not isinstance(el, dict):
            continue
        tt = el.get("timeTaggingParameters")
        if not isinstance(tt, dict):
            tt = el.get("outputPulseParameters")  # deprecated alias, same field
        if not isinstance(tt, dict):
            continue
        for field in ("signalThreshold", "derivativeThreshold"):
            v = tt.get(field)
            if _is_real_num(v) and float(v).is_integer():
                iv = int(v)
                if iv < _TIMETAG_MIN or iv > _TIMETAG_MAX:
                    findings.append(Finding(
                        "warning", "config_timetag_threshold",
                        f"elements.{ename}.timeTaggingParameters.{field}",
                        f"{field} {iv} is outside the signed-12-bit range "
                        f"[{_TIMETAG_MIN}, {_TIMETAG_MAX}]",
                        detail=repr(v)))

    return findings


def _element_controller_refs(elem: dict):
    """Yield ``(controller_name, location_hint)`` for an element's port wiring.

    Handles the common QM element input/output shapes: ``mixInputs.I/Q``,
    ``singleInput.port``, ``MWInput/MWOutput.port`` and ``outputs.<name>``.
    A port is a ``[con, port]`` or ``[con, fem, port]`` list/tuple.
    """
    out: list[tuple[str, str]] = []

    def add(portval, hint):
        if isinstance(portval, (list, tuple)) and portval and isinstance(portval[0], str):
            out.append((portval[0], hint))

    mix = elem.get("mixInputs")
    if isinstance(mix, dict):
        add(mix.get("I"), "mixInputs.I")
        add(mix.get("Q"), "mixInputs.Q")
    si = elem.get("singleInput")
    if isinstance(si, dict):
        add(si.get("port"), "singleInput.port")
    for k in ("MWInput", "MWOutput"):
        mw = elem.get(k)
        if isinstance(mw, dict):
            add(mw.get("port"), f"{k}.port")
    outs = elem.get("outputs")
    if isinstance(outs, dict):
        for n, pv in outs.items():
            add(pv, f"outputs.{n}")
    return out


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def _ordered(findings: list[Finding]) -> list[Finding]:
    """Errors first, then warnings, then info; stable within a severity."""
    return sorted(findings, key=lambda f: _SEVERITY_RANK.get(f.severity, 3))
