"""Structured queries over QUAM state data.

QueryEngine wraps a QuamStore and provides high-level accessors that resolve
pointers, flatten nested structures into researcher-friendly flat dicts, and
support filtering and comparison tables.
"""

from __future__ import annotations

import ast
import logging
import operator
import math

from typing import Any

from quam_state_manager.core import chip_health, cr_semantics, qdac
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.pointer_resolver import is_pointer, is_self_ref

logger = logging.getLogger(__name__)


# Chip Status MetricRecord map (the universal per-metric carrier — see the
# redesign plan). The scalars stay on the node/edge for back-compat; `metrics`
# is a sibling map of records so every client surface reads the same enriched
# record (physicality / verdict / recency / provenance) without re-deriving it.
_NODE_METRIC_KEYS = (
    "f_01", "f_12", "anharmonicity", "chi", "T1", "T2ramsey", "T2echo",
    "readout_frequency", "x180_amplitude", "x180_length", "x180_alpha",
    "x90_amplitude", "saturation_amplitude", "readout_amplitude",
    "readout_length", "readout_threshold", "assignment_fidelity",
    "assignment_fidelity_gef",
    "ro_fidelity_g", "ro_fidelity_e",
    "ro_fidelity_gef_g", "ro_fidelity_gef_e", "ro_fidelity_gef_f",
    "gate_fidelity_avg",
    "gate_fidelity_x180", "gate_fidelity_x90",
)
_EDGE_METRIC_KEYS = ("cz_fidelity", "detuning", "coupler_decouple_offset", "mutual_flux_bias")


def _make_metric_record(key, resolved_value, *, updated_at=None, provenance=None):
    """Build a MetricRecord from an already-resolved value.

    A value that is *still a pointer* didn't resolve (dangling ref) → record it
    as ``unresolved`` (value None, counted missing) rather than leaking the raw
    ``#/...`` string into a numeric tile (which became ``NaN GHz`` and silently
    vanished from aggregates).
    """
    unresolved = is_pointer(resolved_value)
    val = None if unresolved else resolved_value
    return chip_health.make_record(key, val, updated_at=updated_at,
                                   provenance=provenance, unresolved=unresolved)


class QueryEngine:
    """High-level query interface over a loaded QuamStore."""

    def __init__(self, store: QuamStore) -> None:
        self.store = store
        self._qubit_cache: dict[str, dict[str, Any]] = {}
        self._pair_cache: dict[str, dict[str, Any]] = {}
        self._topology_cache: dict[str, Any] | None = None

    def invalidate_cache(self) -> None:
        """Clear cached qubit/pair/topology resolutions (call after store mutation)."""
        self._qubit_cache.clear()
        self._pair_cache.clear()
        self._topology_cache = None

    # ------------------------------------------------------------------
    # Single qubit
    # ------------------------------------------------------------------

    def get_qubit(self, name: str) -> dict[str, Any]:
        """Return a flat dict of all resolved properties for one qubit.

        Keys are human-readable short names (e.g. ``"f_01"``, ``"readout_amplitude"``).
        Values are resolved (pointers followed to concrete numbers).

        Raises ``KeyError`` if the qubit doesn't exist.
        """
        cached = self._qubit_cache.get(name)
        if cached is not None:
            return cached

        # Snapshot the mutation counter BEFORE this lock-free compute; store the
        # result only if no edit landed meanwhile (see the fill below). Otherwise a
        # pre-edit result computed here can land AFTER a concurrent /field/edit
        # cleared the cache, poisoning it until the next mutation.
        seq = self.store.mutation_seq
        qubits = self.store.merged.get("qubits", {})
        if name not in qubits:
            raise KeyError(f"Qubit {name!r} not found (available: {sorted(qubits.keys())})")

        q = qubits[name]
        root = self.store.merged
        base = ("qubits", name)
        result: dict[str, Any] = {}

        result["id"] = name
        result["grid_location"] = q.get("grid_location")
        # r15 (docs/69): membership in state.json's active_qubit_names — QUAM
        # semantics: an absent/empty list means "everything active". List pages
        # always show ALL qubits and only MARK the active ones (never filter).
        active_names = root.get("active_qubit_names")
        result["is_active"] = (not isinstance(active_names, list)
                               or not active_names or name in active_names)
        result["f_01"] = _resolve(self.store, q.get("f_01"), base + ("f_01",))
        result["f_12"] = q.get("f_12")
        result["anharmonicity"] = _resolve(self.store, q.get("anharmonicity"), base + ("anharmonicity",))
        result["chi"] = q.get("chi")
        result["T1"] = q.get("T1")
        result["T2ramsey"] = q.get("T2ramsey")
        result["T2echo"] = q.get("T2echo")

        result["freq_vs_flux_01_quad_term"] = q.get("freq_vs_flux_01_quad_term")
        result["phi0_current"] = q.get("phi0_current")
        result["phi0_voltage"] = q.get("phi0_voltage")

        # ``or {}`` instead of ``.get(k, {})`` so that an explicit null in
        # state.json (e.g. ``"xy": null``) doesn't crash downstream lookups.
        xy = q.get("xy") or {}
        result["xy_RF_frequency"] = _resolve(self.store, xy.get("RF_frequency"), base + ("xy", "RF_frequency"))
        result["xy_intermediate_frequency"] = _resolve(self.store, xy.get("intermediate_frequency"), base + ("xy", "intermediate_frequency"))

        x180 = _get_nested(xy, "operations", "x180_DragCosine") or {}
        result["x180_amplitude"] = _resolve(self.store, x180.get("amplitude"), base + ("xy", "operations", "x180_DragCosine", "amplitude"))
        result["x180_length"] = _resolve(self.store, x180.get("length"), base + ("xy", "operations", "x180_DragCosine", "length"))
        result["x180_alpha"] = _resolve(self.store, x180.get("alpha"), base + ("xy", "operations", "x180_DragCosine", "alpha"))

        x90 = _get_nested(xy, "operations", "x90_DragCosine") or {}
        result["x90_amplitude"] = _resolve(self.store, x90.get("amplitude"), base + ("xy", "operations", "x90_DragCosine", "amplitude"))

        sat = _get_nested(xy, "operations", "saturation") or {}
        result["saturation_amplitude"] = sat.get("amplitude")

        rr = q.get("resonator") or {}
        result["readout_frequency"] = _resolve(self.store, rr.get("f_01"), base + ("resonator", "f_01"))
        result["readout_RF_frequency"] = _resolve(self.store, rr.get("RF_frequency"), base + ("resonator", "RF_frequency"))

        ro = _get_nested(rr, "operations", "readout") or {}
        result["readout_amplitude"] = ro.get("amplitude")
        result["readout_length"] = ro.get("length")
        result["readout_threshold"] = ro.get("threshold")
        result["readout_iw_angle"] = ro.get("integration_weights_angle")

        result["confusion_matrix"] = rr.get("confusion_matrix")
        result["time_of_flight"] = rr.get("time_of_flight")
        # Structural presence (not "does it have a resolved value") — the
        # Resonators/Flux nav pages hide themselves chip-wide when nothing has
        # one, and filter their own rows to only qubits that structurally do.
        result["has_resonator"] = isinstance(q.get("resonator"), dict)
        result["has_z"] = isinstance(q.get("z"), dict)

        z = q.get("z") or {}
        result["z_joint_offset"] = z.get("joint_offset")
        result["z_independent_offset"] = z.get("independent_offset")
        result["z_flux_point"] = z.get("flux_point")
        # LF-FEM output delay (ns) — auto-set by the generator to align with
        # the qubit's MW-FEM band; editable from the detail page.
        #
        # docs/136: `z.opx_output` is a POINTER on every real chip
        # ("#/wiring/qubits/q2/z/opx_output" -> "#/ports/analog_outputs/..."),
        # so the old `isinstance(z_port, dict)` guard was false always and the
        # branch behind it never ran. /flux, /couplers and the qubit inspector
        # all printed "not set" while the Live-Edit grid — which resolves the
        # same dot-path through `resolve_field_target` — printed 78. One app,
        # two answers, for a value that was there the whole time. Resolve the
        # pointer chain first (`_resolve` follows it to the port dict), then
        # read the leaf; an inline dict still passes through untouched.
        z_port = _resolve(self.store, z.get("opx_output"), base + ("z", "opx_output"))
        result["z_delay_ns"] = (
            _resolve(self.store, z_port.get("delay"), base + ("z", "opx_output", "delay"))
            if isinstance(z_port, dict) else None
        )

        # docs/136: how this qubit's flux bias is actually sourced —
        # "opx" (LF-FEM port) / "qdac" (QDAC-II channel) / "bias_tee" (both:
        # QDAC holds the DC point, the LF-FEM plays pulses on top) / None.
        # `has_z` above is structural and says only "there is a z dict", which
        # is true of a QDAC-biased qubit too — that is why /flux listed eleven
        # rows it could not fill.
        bias_mode = qdac.bias_mode(q)
        result["bias_mode"] = bias_mode
        bias_found = qdac.bias_line_of(q)
        result["has_qdac"] = bias_found is not None
        if bias_found is not None:
            bias_field, bias = bias_found
            bias_base = base + (bias_field,)
            for field in qdac.QDAC_FIELDS:
                result["qdac_" + field] = _resolve(
                    self.store, bias.get(field), bias_base + (field,))
            trig = qdac.trigger_ref(q, self.store.merged, name)
            result["qdac_trigger_port_label"] = (
                f"con{trig['con'][3:] if trig['con'].startswith('con') else trig['con']}"
                f"/fem{trig['slot']}/p{trig['port']}" if trig else None
            )
        else:
            for field in qdac.QDAC_FIELDS:
                result["qdac_" + field] = None
            result["qdac_trigger_port_label"] = None

        gf = q.get("gate_fidelity") or {}
        result["gate_fidelity_avg"] = gf.get("averaged") if isinstance(gf, dict) else None
        result["gate_fidelity_x180"] = gf.get("x180") if isinstance(gf, dict) else None
        result["gate_fidelity_x90"] = gf.get("x90") if isinstance(gf, dict) else None

        if seq == self.store.mutation_seq:   # skip a stale fill that races invalidation
            self._qubit_cache[name] = result
        return result

    # ------------------------------------------------------------------
    # Single qubit pair
    # ------------------------------------------------------------------

    def get_pair(self, name: str) -> dict[str, Any]:
        """Return a flat dict of all resolved properties for one qubit pair.

        Raises ``KeyError`` if the pair doesn't exist.
        """
        cached = self._pair_cache.get(name)
        if cached is not None:
            return cached

        seq = self.store.mutation_seq   # see get_qubit: guard the fill against races
        pairs = self.store.merged.get("qubit_pairs", {})
        if name not in pairs:
            raise KeyError(f"Qubit pair {name!r} not found (available: {sorted(pairs.keys())})")

        p = pairs[name]
        if not isinstance(p, dict):
            # A null / string pair value is a real serialization (hand edits,
            # partial regenerations). Raise a TYPED error — the /pairs route
            # degrades it to a visible error row (r16 0-1); raising KeyError
            # here would make the row silently vanish instead.
            raise TypeError(
                f"Qubit pair {name!r} is not an object "
                f"(found {type(p).__name__})")
        root = self.store.merged
        base = ("qubit_pairs", name)
        result: dict[str, Any] = {}

        result["id"] = name

        qc = p.get("qubit_control", "")
        qt = p.get("qubit_target", "")
        result["qubit_control"] = _pair_qubit_ref(self.store, qc, base + ("qubit_control",))
        result["qubit_target"] = _pair_qubit_ref(self.store, qt, base + ("qubit_target",))
        result["moving_qubit"] = p.get("moving_qubit")

        macros = p.get("macros") or {}
        if not isinstance(macros, dict):
            macros = {}
        for gate_name, gate in macros.items():
            if not isinstance(gate, dict):
                continue
            # Only emit CZ/coupler-shaped fields for gates that ACTUALLY have
            # that shape. A CR gate (drive lives in the pair's
            # ``cross_resonance`` channel, handled below) would otherwise
            # produce a whole section of all-None CZ fields — the misleading
            # blank "Cr" section. Detection is by PRESENCE of flux-shaped keys
            # (not their value): an uncalibrated CZ gate has the keys set to
            # null and must still render. ``fidelity`` is deliberately NOT a
            # CZ signal — modern CRGate macros carry a (null) ``fidelity``
            # field, which used to trip this check and resurrect the phantom
            # section with editable rows whose Apply 400'd.
            if not cr_semantics.is_cz_shaped_macro(gate):
                continue
            prefix = gate_name

            # Use ``or {}`` (not ``.get(k, {})``) because the value may be
            # explicitly null in state.json — ``dict.get`` only falls back
            # to the default when the key is MISSING, not when it's None.
            # Wizard-built chips (run_build._seed_cz_variant — and customer
            # chips built with the same pair_gates recipe) store the macro's
            # flux_pulse_qubit / coupler_flux_pulse as JSON REFERENCES to the
            # op on the moving qubit's z (or the coupler) line — the op is the
            # single calibration home. Deref the reference to its target dict
            # (re-anchoring the path so inner relative pointers resolve in the
            # TARGET's frame); calling .get() on the raw pointer string used
            # to crash the whole Pairs page on such chips.
            fpq = gate.get("flux_pulse_qubit") or {}
            fpq_base = base + ("macros", gate_name, "flux_pulse_qubit")
            fpq, fpq_base = _deref_pulse_ref(self.store, fpq, fpq_base)
            result[f"{prefix}_amplitude"] = fpq.get("amplitude")
            result[f"{prefix}_length"] = _resolve(self.store, fpq.get("length"), fpq_base + ("length",))

            if "flat_length" in fpq:
                result[f"{prefix}_flat_length"] = fpq.get("flat_length")
                result[f"{prefix}_smoothing_length"] = fpq.get("smoothing_length")

            cfp = gate.get("coupler_flux_pulse") or {}
            cfp_base = base + ("macros", gate_name, "coupler_flux_pulse")
            cfp, _cfp_base = _deref_pulse_ref(self.store, cfp, cfp_base)
            result[f"{prefix}_coupler_amplitude"] = cfp.get("amplitude")

            result[f"{prefix}_phase_shift_control"] = gate.get("phase_shift_control")
            result[f"{prefix}_phase_shift_target"] = gate.get("phase_shift_target")

            fidelity = gate.get("fidelity") or {}
            if isinstance(fidelity, dict):
                bell = fidelity.get("Bell_State") or {}
                if isinstance(bell, dict):
                    result[f"{prefix}_bell_fidelity"] = bell.get("Fidelity")
                result[f"{prefix}_standard_rb"] = fidelity.get("StandardRB")
                result[f"{prefix}_interleaved_rb"] = fidelity.get("InterleavedRB")

        # Cross-resonance drive channel (the CR 2Q gate). Surface the channel
        # parameters; the CR pulse shapes (operations) live on the Pulses page.
        # Flavor-tolerant via cr_semantics (docs/54): target frequency may be a
        # single RF pointer (customer flavor) or LO+IF literals (a08bf66), the
        # calibration levers may live on the channel or the macro, and the
        # runtime-only #./inferred_* IF is emulated numerically.
        cr = cr_semantics.cr_channel(p)
        if cr is not None:
            crbase = base + ("cross_resonance",)
            result["cr_lo_frequency"] = _resolve(
                self.store, cr.get("LO_frequency"), crbase + ("LO_frequency",))
            result["cr_intermediate_frequency"] = cr.get("intermediate_frequency")
            result["cr_upconverter"] = cr.get("upconverter")
            ops = cr.get("operations")
            if isinstance(ops, dict) and ops:
                result["cr_operations"] = ", ".join(ops.keys())
            eff = cr_semantics.effective_frequencies(self.store, name)
            if eff is not None:
                # flavor-aware target RF (the old key read only the RF-pointer
                # flavor and showed nothing on LO/IF-literal chips)
                result["cr_target_qubit_rf"] = eff.target_rf_hz
                result["cr_effective_lo"] = eff.lo_hz
                result["cr_effective_if"] = eff.if_hz
                result["cr_if_problems"] = list(eff.problems)
            for lever, suffix in cr_semantics.lever_map(p).items():
                if lever.startswith("zz_") or lever == "upconverter":
                    continue     # zz_* surfaced below; upconverter set above
                try:
                    raw = self.store.get_value(f"qubit_pairs.{name}.{suffix}")
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
                result[f"cr_{lever}"] = _resolve(
                    self.store, raw, base + tuple(suffix.split(".")))

        zz = cr_semantics.zz_channel(p)
        if zz is not None:
            zz_key, zz_chan = zz
            result["zz_channel_key"] = zz_key
            result["zz_upconverter"] = zz_chan.get("upconverter")
            zops = zz_chan.get("operations")
            if isinstance(zops, dict) and zops:
                result["zz_operations"] = ", ".join(zops.keys())
            zeff = cr_semantics.effective_frequencies(self.store, name, channel="zz")
            if zeff is not None:
                result["zz_effective_if"] = zeff.if_hz
                result["zz_if_problems"] = list(zeff.problems)

        fid = cr_semantics.fidelity(p)
        if fid is not None:
            result["pair_fidelity"] = fid["value"]
            result["pair_fidelity_source"] = fid["source"]
            # Quarantine philosophy (chip_health): a value outside (0,1] is a
            # failed fit — shown raw + flagged, never painted green. Without
            # this the CR pairs table contradicted Chip Status's gated edge.
            result["pair_fidelity_physical"] = chip_health.physicality(
                "cz_fidelity", fid["value"])

        result["active"] = cr_semantics.is_active(root, name) if (
            cr is not None or zz is not None) else None
        # r15 (docs/69): universal active flag for EVERY pair (the CR-vocab
        # "active" above stays channel-gated for back-compat) — same QUAM
        # semantics: absent/empty active_qubit_pair_names = all active.
        result["is_active"] = cr_semantics.is_active(root, name)

        # Structural presence — the Couplers nav page hides itself chip-wide
        # when no pair has one, and filters its own rows to pairs that do.
        result["has_coupler"] = isinstance(p.get("coupler"), dict)

        coupler = p.get("coupler") or {}
        result["coupler_decouple_offset"] = coupler.get("decouple_offset")
        result["coupler_interaction_offset"] = coupler.get("interaction_offset")
        # LF-FEM coupler delay (ns) — auto-set from the moving qubit's xy band.
        # Same dead guard as `z_delay_ns` above, same fix (docs/136): the value
        # is a pointer chain, so resolve it before reading `delay`. On the real
        # 20Q chip every coupler port carries 141 and /couplers printed "-".
        coupler_port = _resolve(
            self.store, coupler.get("opx_output"), base + ("coupler", "opx_output"))
        result["coupler_delay_ns"] = (
            _resolve(
                self.store, coupler_port.get("delay"),
                base + ("coupler", "opx_output", "delay"),
            )
            if isinstance(coupler_port, dict) else None
        )

        result["detuning"] = p.get("detuning")
        result["confusion"] = p.get("confusion")
        result["mutual_flux_bias"] = p.get("mutual_flux_bias")

        if seq == self.store.mutation_seq:   # skip a stale fill that races invalidation
            self._pair_cache[name] = result
        return result

    # ------------------------------------------------------------------
    # Port info
    # ------------------------------------------------------------------

    def get_port_for(self, qubit: str, channel: str) -> dict[str, Any] | None:
        """Resolve the full port info for a qubit's channel (``"xy"``, ``"rr"``, ``"z"``).

        Returns a dict with port details, or ``None`` if unavailable.
        """
        q = self.store.merged.get("qubits", {}).get(qubit)
        if q is None:
            return None

        if channel == "rr":
            ch = q.get("resonator")
        else:
            ch = q.get(channel)
        # A channel key can be present but explicitly ``null`` (real data) — the
        # dict default only applies to a *missing* key, so guard the type.
        if not isinstance(ch, dict):
            return None

        raw_output = ch.get("opx_output")
        if raw_output is None:
            return None

        path_tuple = ("qubits", qubit, channel if channel != "rr" else "resonator", "opx_output")
        resolved = _resolve(self.store, raw_output, path_tuple)

        if isinstance(resolved, dict):
            return resolved
        if isinstance(resolved, str):
            return {"port_ref": resolved}
        return None

    def get_port_for_pair(self, pair: str) -> dict[str, Any] | None:
        """Resolve coupler port info for a qubit pair.

        Looks in ``wiring.qubit_pairs.<pair>.c.opx_output`` (or ``.coupler.opx_output``).
        Returns a dict with port details, or ``None`` if unavailable.
        """
        root = self.store.merged
        p = root.get("qubit_pairs", {}).get(pair)
        if p is None:
            return None

        # ``coupler`` is often present-but-``null`` (LabA pairs have no coupler
        # wiring at all) — the dict default only applies to a missing key, so the
        # type guard is what prevents a 500 on every pair click. CR pairs have no
        # coupler; fall back to the cross-resonance drive port so the shared
        # MW-FEM port is still shown.
        raw_output = None
        path_tuple: tuple[str, ...] | None = None
        coupler = p.get("coupler")
        if isinstance(coupler, dict) and coupler.get("opx_output") is not None:
            raw_output = coupler.get("opx_output")
            path_tuple = ("qubit_pairs", pair, "coupler", "opx_output")
        else:
            cr = p.get("cross_resonance")
            if isinstance(cr, dict) and cr.get("opx_output") is not None:
                raw_output = cr.get("opx_output")
                path_tuple = ("qubit_pairs", pair, "cross_resonance", "opx_output")
        if raw_output is None:
            return None

        resolved = _resolve(self.store, raw_output, path_tuple)

        if isinstance(resolved, dict):
            return resolved
        if isinstance(resolved, str):
            return {"port_ref": resolved}
        return None

    def get_pair_port_roles(self, pair: str) -> dict[str, dict]:
        """Every drive-port role on a pair → resolved port info.

        ``{"coupler"|"cr"|"zz": port_dict}`` — honestly labeled, unlike
        :meth:`get_port_for_pair` whose single return forced the pair detail
        page to caption a CR drive port "coupler". Explicit-null channels are
        skipped (the type guard, not ``dict.get`` defaults — real data
        serializes both missing-key and null).
        """
        p = self.store.merged.get("qubit_pairs", {}).get(pair)
        if not isinstance(p, dict):
            return {}
        out: dict[str, dict] = {}
        candidates = [("coupler", "coupler")]
        candidates += [("cr", k) for k in cr_semantics.CR_CHANNEL_KEYS]
        candidates += [("zz", k) for k in cr_semantics.ZZ_CHANNEL_KEYS]
        for role, key in candidates:
            chan = p.get(key)
            if not isinstance(chan, dict) or chan.get("opx_output") is None:
                continue
            resolved = _resolve(self.store, chan["opx_output"],
                                ("qubit_pairs", pair, key, "opx_output"))
            if isinstance(resolved, dict):
                out.setdefault(role, resolved)
            elif isinstance(resolved, str):
                out.setdefault(role, {"port_ref": resolved})
        return out

    # ------------------------------------------------------------------
    # List / filter qubits
    # ------------------------------------------------------------------

    def list_qubits(self, filter_expr: str | None = None) -> list[dict[str, Any]]:
        """Return summary dicts for all qubits, optionally filtered.

        ``filter_expr`` supports safe comparisons like ``"T2ramsey > 1e-6"``
        or ``"f_01 > 6e9"``, parsed with ``ast.parse()`` and evaluated via
        a whitelist -- never ``eval()``.
        """
        results = []
        for name in self.store.qubit_names:
            try:
                q = self.get_qubit(name)
            except Exception:
                continue
            if filter_expr and not _eval_filter(q, filter_expr):
                continue
            results.append(q)
        return results

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------

    def summary_table(self, properties: list[str]) -> list[dict[str, Any]]:
        """Return a list of dicts (one per qubit) with only the requested properties.

        Always includes ``"id"`` as the first column.
        """
        rows = []
        for name in self.store.qubit_names:
            try:
                q = self.get_qubit(name)
            except Exception:
                continue
            row: dict[str, Any] = {"id": name}
            for prop in properties:
                row[prop] = q.get(prop)
            rows.append(row)
        return rows

    # ------------------------------------------------------------------
    # Wiring map
    # ------------------------------------------------------------------

    def get_wiring_map(self) -> list[dict[str, Any]]:
        """Return a list of dicts mapping each qubit to its port assignments."""
        rows = []
        wiring_qubits = _get_nested(self.store.merged, "wiring", "qubits") or {}
        for name in self.store.qubit_names:
            qw = wiring_qubits.get(name, {})
            row: dict[str, Any] = {"qubit": name}
            for channel, key in [("xy", "opx_output"), ("rr", "opx_output"), ("rr", "opx_input"), ("z", "opx_output")]:
                ch_data = qw.get(channel, {})
                raw = ch_data.get(key)
                col_name = f"{channel}_{key}"
                if is_pointer(raw) and not is_self_ref(raw):
                    resolved = self.store.resolve_pointer(raw, ("wiring", "qubits", name, channel, key))
                    row[col_name] = resolved if not isinstance(resolved, str) or not resolved.startswith("#") else raw
                else:
                    row[col_name] = raw
            # docs/136 — a QDAC-biased qubit has NO z line in wiring.json at
            # all, so every column above is blank for it and the table said
            # nothing about where its bias comes from. The columns are added
            # only when at least one qubit has a QDAC (below), so a chip
            # without one renders exactly as before.
            bias = qdac.bias_line_of(self.store.merged.get("qubits", {}).get(name))
            if bias:
                _field, node = bias
                row["qdac_channel"] = node.get("channel")
                ref = qdac.trigger_ref(
                    self.store.merged.get("qubits", {}).get(name),
                    self.store.merged, name)
                # `con` already carries its own prefix — it is the raw path
                # segment ("con1"), not an index.
                row["qdac_trigger"] = (
                    f"{ref['con']}/{ref['slot']}/{ref['port']}"
                    + (f" ({ref['ext']})" if ref.get("ext") else "")) if ref else None
            rows.append(row)
        if any("qdac_channel" in r for r in rows):
            for r in rows:
                r.setdefault("qdac_channel", None)
                r.setdefault("qdac_trigger", None)
        return rows

    # ------------------------------------------------------------------
    # Topology graph
    # ------------------------------------------------------------------

    def get_topology(self) -> dict[str, Any]:
        """Return nodes and edges for rendering a connectivity graph.

        Returns ``{"nodes": [...], "edges": [...]}``. Cached: the result is a
        pure derivation of ``store.merged``, and the cache is cleared by
        ``invalidate_cache`` after any mutation. /topology is hit on every Chip
        Status navigation (incl. sub-view links), so this avoids recomputing
        ~50 qubit nodes + pair edges on each visit.
        """
        if self._topology_cache is not None:
            return self._topology_cache
        seq = self.store.mutation_seq   # see get_qubit: guard the fill against races
        root = self.store.merged
        nodes = []
        for name in self.store.qubit_names:
            q = root.get("qubits", {}).get(name, {})
            loc = q.get("grid_location", "")
            chain = ""
            if name and len(name) >= 2:
                chain = name[1] if name[0] == "q" and name[1].isalpha() else ""

            # ``or {}`` (not ``.get(k, {})``): a trimmed real chip carries
            # explicitly-NULL channels ("z": null — docs/72), and dict.get
            # falls back only when the key is missing, not when it's None; a
            # null z used to AttributeError → 500 the whole topology.
            xy = q.get("xy") or {}
            x180 = _get_nested(xy, "operations", "x180_DragCosine") or {}
            x90 = _get_nested(xy, "operations", "x90_DragCosine") or {}
            sat = _get_nested(xy, "operations", "saturation") or {}
            rr = q.get("resonator") or {}
            ro = _get_nested(rr, "operations", "readout") or {}

            z = q.get("z") or {}
            gf = q.get("gate_fidelity") or {}

            node = {
                "id": name,
                "chain": chain,
                "grid_location": loc,
                "f_01": _resolve(self.store, q.get("f_01"), ("qubits", name, "f_01")),
                "f_12": q.get("f_12"),
                "anharmonicity": _resolve(self.store, q.get("anharmonicity"), ("qubits", name, "anharmonicity")),
                "chi": q.get("chi"),
                "T1": q.get("T1"),
                "T2ramsey": q.get("T2ramsey"),
                "T2echo": q.get("T2echo"),
                "readout_frequency": _resolve(self.store, rr.get("f_01"), ("qubits", name, "resonator", "f_01")),
                "x180_amplitude": _resolve(self.store, x180.get("amplitude"), ("qubits", name, "xy", "operations", "x180_DragCosine", "amplitude")),
                "x180_length": _resolve(self.store, x180.get("length"), ("qubits", name, "xy", "operations", "x180_DragCosine", "length")),
                "x180_alpha": _resolve(self.store, x180.get("alpha"), ("qubits", name, "xy", "operations", "x180_DragCosine", "alpha")),
                "x90_amplitude": _resolve(self.store, x90.get("amplitude"), ("qubits", name, "xy", "operations", "x90_DragCosine", "amplitude")),
                "saturation_amplitude": sat.get("amplitude"),
                "readout_amplitude": ro.get("amplitude"),
                "readout_length": ro.get("length"),
                "readout_threshold": ro.get("threshold"),
                "assignment_fidelity": _assignment_fidelity(rr.get("confusion_matrix")),
                # docs/141 4o: three-state (g/e/f) readout fidelity, from the
                # 3x3 matrix the GEF discrimination node stores beside the 2x2
                "assignment_fidelity_gef": _assignment_fidelity_n(rr.get("gef_confusion_matrix")),
                "ro_fidelity_g": _cm_diag(rr.get("confusion_matrix"), 0),
                "ro_fidelity_e": _cm_diag(rr.get("confusion_matrix"), 1),
                # docs/148b (customer): the readout section reads GE (g, e)
                # then GEF (g, e, f) -- these are the per-state diagonals of
                # the same 3x3 matrix the GEF assignment fidelity averages
                "ro_fidelity_gef_g": _cm_diag(rr.get("gef_confusion_matrix"), 0),
                "ro_fidelity_gef_e": _cm_diag(rr.get("gef_confusion_matrix"), 1),
                "ro_fidelity_gef_f": _cm_diag(rr.get("gef_confusion_matrix"), 2),
                "gate_fidelity_avg": gf.get("averaged") if isinstance(gf, dict) else None,
                "gate_fidelity_x180": gf.get("x180") if isinstance(gf, dict) else None,
                "gate_fidelity_x90": gf.get("x90") if isinstance(gf, dict) else None,
                "z_flux_point": z.get("flux_point"),
                "xy_port": _extract_port_label(self.store, q, name, "xy"),
                "rr_port": _extract_port_label(self.store, q, name, "resonator"),
                "z_port": _extract_port_label(self.store, q, name, "z"),
                # docs/136 — how this qubit is flux-biased. `z_port` is None on
                # a QDAC-only qubit (it has no OPX analog port), so the map's
                # flux stub, which keys on z_port, drew nothing for over half
                # this chip and the /flux highlight lit almost none of it.
                "bias_mode": qdac.bias_mode(q),
                "qdac_channel": (
                    (qdac.bias_line_of(q) or (None, {}))[1].get("channel")),
                # Calibration recency (epoch ms; client renders "N days ago").
                # gate_fidelity_updated_at = the 1Q-fidelity measurement time;
                # last_calibrated = the freshest *updated_at anywhere in the qubit.
                "gate_fidelity_updated_at": (
                    chip_health.epoch_ms(gf.get("averaged_updated_at"))
                    if isinstance(gf, dict) else None),
                "last_calibrated": chip_health.newest_epoch_ms(q),
            }
            # MetricRecord map (Foundation): per-metric records read off the
            # already-resolved scalars. Per-metric recency wired where known
            # (Phase 5 expands the rest); the 1Q-fidelity timestamp is real now.
            _node_ts = {"gate_fidelity_avg": node.get("gate_fidelity_updated_at")}
            node["metrics"] = {
                k: _make_metric_record(k, node.get(k), updated_at=_node_ts.get(k))
                for k in _NODE_METRIC_KEYS
            }
            nodes.append(node)

        edges = []
        for pair_name in self.store.qubit_pair_names:
            p = self.store.merged.get("qubit_pairs", {}).get(pair_name, {})

            qc_raw = p.get("qubit_control", "")
            qt_raw = p.get("qubit_target", "")
            pair_base = ("qubit_pairs", pair_name)
            source = _pair_qubit_ref(self.store, qc_raw, pair_base + ("qubit_control",))
            target = _pair_qubit_ref(self.store, qt_raw, pair_base + ("qubit_target",))

            # ``or {}`` instead of ``.get(k, {})`` because the value may be
            # explicitly null in state.json (dict.get falls back only when
            # the key is missing, not when its value is None).
            macros = p.get("macros") or {}
            has_cz = bool(macros)

            best_fidelity = None
            best_gate = None
            best_fid_obj = None
            for gate_name, gate in macros.items():
                if not isinstance(gate, dict):
                    continue
                fid = gate.get("fidelity") or {}
                if isinstance(fid, dict):
                    bell = fid.get("Bell_State") or {}
                    if isinstance(bell, dict) and bell.get("Fidelity") is not None:
                        val = bell["Fidelity"]
                        # Numeric guard: a dangling-pointer string / non-number must
                        # NOT reach the `>` comparison — a pair mixing a string and a
                        # float 500'd the WHOLE topology (Chip Status, /api/topology,
                        # compare cards). Physicality guard: a broken fit (Fidelity > 1)
                        # must not WIN best-gate or paint the edge green over a real
                        # gate — skip it so the edge/★/tile agree with the gated health
                        # records (which already quarantine it), instead of the page
                        # contradicting itself (green 2270% edge vs 'missing' tile).
                        if (not isinstance(val, (int, float)) or isinstance(val, bool)
                                or not chip_health.physicality("cz_fidelity", val)):
                            continue
                        if best_fidelity is None or val > best_fidelity:
                            best_fidelity = val
                            best_gate = gate_name
                            best_fid_obj = fid

            # Provenance: the RB run id that produced the best gate's numbers, so
            # the UI can deep-link to the source dataset (no timestamp lives here).
            cz_load_id = None
            cz_updated_at = None
            if isinstance(best_fid_obj, dict):
                cz_load_id = (best_fid_obj.get("StandardRB_load_id")
                              or best_fid_obj.get("InterleavedRB_load_id"))
                # Per-metric recency: the CZ fidelity's OWN measurement time (the
                # best gate's Bell_State / RB timestamp), so the UI can say "this
                # number, measured N days ago" — not the freshest unrelated
                # calibration anywhere in the pair (which over-states freshness).
                for _src in ("Bell_State", "StandardRB", "InterleavedRB"):
                    _o = best_fid_obj.get(_src)
                    if isinstance(_o, dict) and _o.get("updated_at"):
                        cz_updated_at = _o.get("updated_at")
                        break

            # Channel-fidelity fallback (docs/54): the lo_if CR flavor stores
            # the 2Q Bell fidelity ON the cross_resonance channel — macro-only
            # reading left those edges gray "no fidelity". Channel source ONLY
            # (macro ladders beyond Bell_State would alter CZ chips' edges,
            # which are pinned byte-equal).
            fidelity_source = "macro" if best_fidelity is not None else None

            # docs/138 — a chip that measures its CZ by INTERLEAVED RB has a
            # real, per-gate fidelity and was still drawn grey, because only
            # Bell_State was consulted. InterleavedRB is 1 - EPG: the gate's own
            # error, from the interleaved/reference alpha ratio. It is the same
            # quantity Bell_State approximates, measured better.
            #
            # StandardRB is deliberately NOT a fallback here. It is 1 - EPC, a
            # CLIFFORD fidelity, and a Clifford is ~1.5 gates — colouring the
            # edge with it would understate every gate on the chip. There is no
            # conversion available: `average_gates_per_clifford` is a node
            # namespace value and is in neither state.json nor the saved run.
            if best_fidelity is None:
                for _g, _gate in macros.items():
                    if not isinstance(_gate, dict):
                        continue
                    _f = _gate.get("fidelity") or {}
                    if not isinstance(_f, dict):
                        continue
                    for _k in ("InterleavedRB", "IRB"):
                        _v = _f.get(_k)
                        if (isinstance(_v, (int, float)) and not isinstance(_v, bool)
                                and chip_health.physicality("cz_fidelity", _v)
                                and (best_fidelity is None or _v > best_fidelity)):
                            best_fidelity = _v
                            best_gate = _g
                            best_fid_obj = _f
                            fidelity_source = "interleaved_rb"

            if best_fidelity is None:
                _fb = cr_semantics.fidelity(p)
                if (_fb is not None and _fb["source"] == "channel"
                        and chip_health.physicality("cz_fidelity", _fb["value"])):
                    best_fidelity = _fb["value"]
                    fidelity_source = "channel"

            gate_fidelities = _extract_pair_gate_fidelities(macros)
            gate_details = _extract_gate_details(macros)
            gate_details.extend(_extract_cr_details(self.store, pair_name, p))
            confusion_raw = p.get("confusion")
            confusion_offdiag = _extract_pair_confusion_offdiag(confusion_raw)
            confusion_size = len(confusion_raw) if isinstance(confusion_raw, list) else 0
            confusion_diag = None
            if isinstance(confusion_raw, list) and confusion_size >= 2:
                try:
                    confusion_diag = [confusion_raw[i][i] for i in range(confusion_size)]
                except (IndexError, TypeError):
                    pass

            coupler = p.get("coupler", {}) if isinstance(p.get("coupler"), dict) else {}

            _is_cr_pair = (cr_semantics.cr_channel(p) is not None
                           or cr_semantics.cr_gate_macro(p) is not None)
            edge = {
                "pair_id": pair_name,
                "source": source,
                "target": target,
                "has_cz": has_cz,
                # same convention as get_pair's has_coupler: a dict-valued
                # coupler counts, an explicit null does not (docs/92 — the
                # component-map coupler symbols key on this)
                "has_coupler": isinstance(p.get("coupler"), dict),
                # docs/120 item 11 — which END of this pair actually moves.
                # A ROLE ("control"/"target"), never a qubit id: quam_builder
                # writes it as a role and defaults it to the higher-f_01 qubit
                # (run_build._seed_cz_variant), so M always coincides with C or
                # T and is drawn as a combined marker, never a third position.
                # None on a chip that does not record it — the map then simply
                # shows no M rather than inferring one from the frequencies,
                # because an override is exactly the case worth seeing.
                "moving_qubit": (p.get("moving_qubit")
                                 if p.get("moving_qubit") in ("control", "target")
                                 else None),
                "cz_fidelity": best_fidelity,
                "fidelity_source": fidelity_source,
                "gate_kind": "cr" if _is_cr_pair else ("cz" if macros else "none"),
                "directed": _is_cr_pair,
                "active": (cr_semantics.is_active(self.store.merged, pair_name)
                           if _is_cr_pair else None),
                "edge_key": (list(cr_semantics.physical_edge_key(p) or ())
                             or None),
                "best_gate": best_gate,
                "gate_fidelities": gate_fidelities,
                "gate_details": gate_details,
                "confusion_size": confusion_size,
                "confusion_diag": confusion_diag,
                "confusion_offdiag": confusion_offdiag,
                "detuning": p.get("detuning"),
                "coupler_decouple_offset": coupler.get("decouple_offset"),
                "mutual_flux_bias": p.get("mutual_flux_bias"),
                "cz_load_id": cz_load_id,
                "cz_fidelity_updated_at": chip_health.epoch_ms(cz_updated_at),
                "last_calibrated": chip_health.newest_epoch_ms(p),
            }
            _cz_prov = {"load_id": cz_load_id, "run_id": None} if cz_load_id else None
            _edge_ts = {"cz_fidelity": edge.get("cz_fidelity_updated_at")}
            edge["metrics"] = {
                k: _make_metric_record(
                    k, edge.get(k), updated_at=_edge_ts.get(k),
                    provenance=_cz_prov if k == "cz_fidelity" else None)
                for k in _EDGE_METRIC_KEYS
            }
            edges.append(edge)

        # Chip-wide aggregates, PHYSICAL-GATED over the MetricRecords (Phase 0):
        # unphysical/unresolved values never feed min/max/avg/median (so a -473µs
        # T2 can't stretch the relative-colour domain) and yield honest
        # measured/missing/bad/total counts. The min/max double as the relative
        # colour domain. (Scalars stay on each node/edge for histograms +
        # /chip-compare; only the summary moved to the record-gated path.)
        cal_epochs = [n["last_calibrated"] for n in nodes if n.get("last_calibrated")]
        cal_epochs += [e["last_calibrated"] for e in edges if e.get("last_calibrated")]
        _kinds = {e["gate_kind"] for e in edges if e.get("gate_kind") != "none"}
        summary = {
            "nodes": chip_health.aggregate_records(nodes, list(_NODE_METRIC_KEYS)),
            "edges": chip_health.aggregate_records(edges, list(_EDGE_METRIC_KEYS)),
            "oldest_calibration": min(cal_epochs) if cal_epochs else None,
            "newest_calibration": max(cal_epochs) if cal_epochs else None,
            "qubit_count": len(nodes),
            "pair_count": len(edges),
            # Gate-neutral labeling: "CZ Coverage"/"lowest CZ Bell" become
            # CR-branded on CR chips, "2Q" on mixed (the cz_fidelity metric
            # KEY is stable — it threads thresholds/heatmaps/compare).
            "gate_vocab": ("CR" if _kinds == {"cr"}
                           else "CZ" if _kinds <= {"cz"} else "2Q"),
        }

        topo = {"nodes": nodes, "edges": edges, "summary": summary}
        if seq == self.store.mutation_seq:   # skip a stale fill that races invalidation
            self._topology_cache = topo
        return topo

    # ------------------------------------------------------------------
    # Instrument wiring diagram
    # ------------------------------------------------------------------

    def get_instrument_wiring(self) -> dict[str, Any]:
        """Return structured port assignments grouped by controller → FEM → port.

        Returns ``{"controllers": {ctrl: {"fems": {fem_id: {"type": ..., "ports": {...}}}, "max_port": N}}}``.
        """
        root = self.store.merged
        wiring_qubits = _get_nested(root, "wiring", "qubits") or {}
        wiring_pairs = _get_nested(root, "wiring", "qubit_pairs") or {}
        logger.debug("Instrument wiring: %d qubits, %d pairs in wiring", len(wiring_qubits), len(wiring_pairs))

        # (ctrl, fem, port, port_type) → list of assignment dicts
        port_assignments: dict[tuple, list] = {}
        # Count refs we saw vs actually placed, so the UI can tell a genuinely
        # unwired chip apart from one whose ports we couldn't parse (OPX+ 5-part
        # refs / Octave) — otherwise both render as an empty rack with a
        # misleading "no wiring" message.
        ref_stats = {"seen": 0, "placed": 0}

        def add_assignment(ref_str: str, role: str, element: str, extra: dict) -> None:
            """Parse a port reference and record the assignment with role-specific metadata."""
            ref_stats["seen"] += 1
            parsed = _parse_port_ref(ref_str)
            if not parsed:
                logger.warning("Unrecognized port ref: %r (element=%s, role=%s)", ref_str, element, role)
                return
            ref_stats["placed"] += 1
            port_type, ctrl, fem, port = parsed
            key = (ctrl, fem, port, port_type)
            if key not in port_assignments:
                port_assignments[key] = []
            port_dict = _resolve_port_dict(root, ref_str)

            def _pv(*names: str) -> Any:
                # First present value across *names*, pointer-resolved. The project's
                # post-build fix-up rewrites an MW input port's downconverter_frequency
                # as a JSON pointer to the paired output's upconverter_frequency, so the
                # raw value is a "#/..." string on every built chip — resolve it to the
                # number instead of showing the literal pointer in the port popup.
                for nm in names:
                    v = port_dict.get(nm)
                    if v is not None:
                        return _resolve(self.store, v, ("ports", ref_str, nm))
                return None

            port_assignments[key].append({
                "role": role,
                "element": element,
                "label": f"{element}.{role}",
                "port_type": port_type,
                "band": _pv("band"),
                "lo_frequency": _pv("upconverter_frequency", "downconverter_frequency"),
                "full_scale_power_dbm": _pv("full_scale_power_dbm"),
                "sampling_rate": _pv("sampling_rate"),
                "output_mode": _pv("output_mode"),
                "upsampling_mode": _pv("upsampling_mode"),
                **extra,
            })

        for qname, qw in wiring_qubits.items():
            # `or {}` (not `.get(k, {})`): a channel key present with a JSON null
            # value returns None, and real data has it (LabA_CR pairs carry
            # "coupler": null; nulling a channel in Explorer produces it too). The
            # subsequent .get() on None crashed the whole diagram → blank rack.
            q = (root.get("qubits") or {}).get(qname) or {}
            xy = q.get("xy") or {}
            x180 = _get_nested(xy, "operations", "x180_DragCosine") or {}
            sat = _get_nested(xy, "operations", "saturation") or {}
            rr = q.get("resonator") or {}
            ro = _get_nested(rr, "operations", "readout") or {}
            z = q.get("z") or {}

            xy_ref = _get_nested(qw, "xy", "opx_output")
            if xy_ref:
                add_assignment(xy_ref, "xy", qname, {
                    "f_01": _resolve(self.store, q.get("f_01"), ("qubits", qname, "f_01")),
                    "rf_frequency": _resolve(self.store, xy.get("RF_frequency"), ("qubits", qname, "xy", "RF_frequency")),
                    "anharmonicity": q.get("anharmonicity"),
                    "x180_amplitude": _resolve(self.store, x180.get("amplitude"), ("qubits", qname, "xy", "operations", "x180_DragCosine", "amplitude")),
                    "x180_length": _resolve(self.store, x180.get("length"), ("qubits", qname, "xy", "operations", "x180_DragCosine", "length")),
                    "drag_alpha": x180.get("alpha"),
                    "saturation_amplitude": sat.get("amplitude"),
                    "saturation_length": sat.get("length"),
                })

            rr_out_ref = _get_nested(qw, "rr", "opx_output")
            if rr_out_ref:
                add_assignment(rr_out_ref, "rr", qname, {
                    "rf_frequency": _resolve(self.store, rr.get("f_01"), ("qubits", qname, "resonator", "f_01")),
                    "readout_amplitude": ro.get("amplitude"),
                    "readout_length": ro.get("length"),
                    "readout_threshold": ro.get("threshold"),
                    "time_of_flight": rr.get("time_of_flight"),
                    "depletion_time": rr.get("depletion_time"),
                })

            rr_in_ref = _get_nested(qw, "rr", "opx_input")
            if rr_in_ref:
                add_assignment(rr_in_ref, "rr_in", qname, {
                    "rf_frequency": _resolve(self.store, rr.get("f_01"), ("qubits", qname, "resonator", "f_01")),
                })

            z_ref = _get_nested(qw, "z", "opx_output")
            if z_ref:
                z_details = {
                    "flux_point": z.get("flux_point"),
                    "joint_offset": z.get("joint_offset"),
                    "independent_offset": z.get("independent_offset"),
                }
                # docs/136 — the bias tee. This qubit's DC operating point comes
                # from a QDAC-II channel while THIS OPX port plays pulses on top
                # of it, so the port is shared between two instruments and the
                # diagram has to say so: reading `joint_offset` alone (or worse,
                # its absence) would describe half the hardware. The QDAC facts
                # ride along so one hover answers "what drives this line?"
                # completely, rather than sending the reader to another page.
                bias_found = qdac.bias_line_of(q)
                if bias_found is not None:
                    _, bias_node = bias_found
                    z_details.update({
                        "qdac_shared": True,
                        "qdac_channel": bias_node.get("channel"),
                        "qdac_dc_offset": bias_node.get("dc_offset"),
                        "qdac_trigger_port": bias_node.get("trigger_port"),
                    })
                add_assignment(z_ref, "z", qname, z_details)

        for pair_name, pw in wiring_pairs.items():
            p = (root.get("qubit_pairs") or {}).get(pair_name) or {}
            coupler = p.get("coupler") or {}
            c_ref = _get_nested(pw, "c", "opx_output") or _get_nested(pw, "coupler", "opx_output")
            if c_ref:
                add_assignment(c_ref, "coupler", pair_name, {
                    "flux_point": coupler.get("flux_point"),
                    "decouple_offset": coupler.get("decouple_offset"),
                    "interaction_offset": coupler.get("interaction_offset"),
                })
            # Cross-resonance drive line: CR-gate chips (which the Generate wizard
            # produces) wire each pair as wiring.qubit_pairs.<pair>.cr.opx_output,
            # usually on a dedicated MW-FEM. Without collecting it, that whole FEM is
            # simply absent from the rack (FEMs only appear when they have >=1 port).
            cr_ref = _get_nested(pw, "cr", "opx_output") or _get_nested(pw, "cross_resonance", "opx_output")
            if cr_ref:
                add_assignment(cr_ref, "cr", pair_name, {
                    "qubit_control": p.get("qubit_control"),
                    "qubit_target": p.get("qubit_target"),
                })

        wiring_twpas = _get_nested(root, "wiring", "twpas") or {}
        for twpa_name, tw in wiring_twpas.items():
            t = (root.get("twpas") or {}).get(twpa_name) or {}
            spec_state = t.get("spectroscopy") or {}
            pump_ref = _get_nested(tw, "pump", "opx_output")
            if pump_ref:
                add_assignment(pump_ref, "twpa_pump", twpa_name, {
                    "pump_frequency": t.get("pump_frequency"),
                    "pump_amplitude": t.get("pump_amplitude"),
                    "max_avg_gain": t.get("max_avg_gain"),
                })
            spec_out_ref = _get_nested(tw, "spectroscopy", "opx_output")
            if spec_out_ref:
                add_assignment(spec_out_ref, "twpa_ro", twpa_name, {
                    "rf_frequency": _resolve(self.store, spec_state.get("f_01"), ("twpas", twpa_name, "spectroscopy", "f_01")),
                    "depletion_time": spec_state.get("depletion_time"),
                    "time_of_flight": spec_state.get("time_of_flight"),
                })
            spec_in_ref = _get_nested(tw, "spectroscopy", "opx_input")
            if spec_in_ref:
                add_assignment(spec_in_ref, "twpa_in", twpa_name, {
                    "rf_frequency": _resolve(self.store, spec_state.get("f_01"), ("twpas", twpa_name, "spectroscopy", "f_01")),
                })

        # ── Digital outputs (docs/126 follow-up: "does the diagram show the
        # trigger lines?") ─ two shapes carry them, describing the SAME
        # physical connection when both are present:
        #   ① any channel dict in state with a non-empty ``digital_outputs``
        #     map (quam's Channel base field — the QDAC trigger lives at
        #     z.opx_trigger_out.digital_outputs.trigger; a readout marker
        #     would live at resonator.digital_outputs.<name>). Its opx_output
        #     is usually a TWO-hop pointer:
        #     #/wiring/qubits/q1/qt/digital_output → #/ports/digital_outputs/…
        #   ② the wiring-level ``qt.digital_output`` line docs/119 writes —
        #     kept as a fallback so a chip whose state channel degraded away
        #     still shows the trigger the wiring claims.
        # ② is skipped when ① already placed the same (element, port).
        placed_digital: set[tuple[str, tuple]] = set()

        # docs/136 — which qubits' digital markers are QDAC-II triggers, and
        # which ext input each is cabled to. A generic "digital" label cannot
        # explain why three qubits legitimately share one port; the ext is the
        # reason, so it travels with the assignment.
        qdac_ext: dict[str, Any] = {}
        for _qid, _q in (root.get("qubits") or {}).items():
            _found = qdac.bias_line_of(_q)
            if _found is not None:
                qdac_ext[_qid] = _found[1].get("trigger_port")

        def add_digital(element: str, marker: str, source: str, ref: str,
                        ch_entry: dict) -> None:
            final = _follow_port_ref(root, ref) or ref
            parsed = _parse_port_ref(final)
            if parsed is not None and (element, parsed) in placed_digital:
                return
            details = {
                "label": f"{element}.{marker}",
                "marker": marker,
                "source": source,
                "delay": ch_entry.get("delay"),
                "buffer": ch_entry.get("buffer"),
                "shareable": ch_entry.get("shareable"),
                "inverted": ch_entry.get("inverted"),
            }
            if element in qdac_ext:
                details["qdac_trigger"] = True
                details["qdac_ext"] = qdac_ext[element]
            add_assignment(final, "digital", element, details)
            if parsed is not None:
                placed_digital.add((element, parsed))

        def scan_digital(element: str, node: Any, path: str, depth: int = 0) -> None:
            # Depth-limited walk of an entity's state subtree for channels
            # carrying digital_outputs. ``operations`` subtrees are skipped —
            # pulses carry ``digital_marker`` (a pulse property), never a port.
            if depth > 6 or not isinstance(node, dict):
                return
            douts = node.get("digital_outputs")
            if isinstance(douts, dict):
                for marker, entry in douts.items():
                    if not isinstance(entry, dict):
                        continue
                    ref = entry.get("opx_output")
                    if isinstance(ref, str) and ref:
                        add_digital(element, str(marker), path, ref, entry)
            for k, v in node.items():
                if k in ("operations", "digital_outputs") or not isinstance(v, dict):
                    continue
                scan_digital(element, v, f"{path}.{k}" if path else str(k), depth + 1)

        for coll in ("qubits", "qubit_pairs", "twpas"):
            for ename, edict in (root.get(coll) or {}).items():
                if isinstance(edict, dict):
                    scan_digital(ename, edict, "")

        for qname, qw in wiring_qubits.items():
            qt_ref = _get_nested(qw, "qt", "digital_output")
            if isinstance(qt_ref, str) and qt_ref:
                parsed = _parse_port_ref(qt_ref)
                if parsed is not None and (qname, parsed) in placed_digital:
                    continue
                add_assignment(qt_ref, "digital", qname, {
                    "label": f"{qname}.trigger",
                    "marker": "trigger",
                    "source": "qt",
                    "delay": None,
                    "buffer": None,
                    "shareable": None,
                    "inverted": None,
                    # The wiring-only fallback: the state channel degraded away
                    # (or was never built), but a `qt` line is a QDAC trigger by
                    # definition — so it says so even here, where the ext is
                    # only known if the bias line survived.
                    "qdac_trigger": True,
                    "qdac_ext": qdac_ext.get(qname),
                })
                if parsed is not None:
                    placed_digital.add((qname, parsed))

        # Group into controller → FEM structure, separating output and input ports
        controllers: dict[str, Any] = {}
        for (ctrl, fem, port, port_type), assignments in port_assignments.items():
            if ctrl not in controllers:
                controllers[ctrl] = {}
            if fem not in controllers[ctrl]:
                controllers[ctrl][fem] = {"type": None, "output_ports": {},
                                          "input_ports": {}, "digital_ports": {}}
            fem_entry = controllers[ctrl][fem]
            if "digital" in port_type:
                # Digital port numbers overlap the analog ones (both count
                # 1..8 on a FEM) — a shared bucket would merge digital port 1
                # into analog output 1, so they get their own.
                fem_entry["digital_ports"][port] = assignments
                continue
            # Only an analog port can name the FEM flavor; a digital-only FEM
            # stays the honest "fem" (resolved at assembly below).
            fem_entry["type"] = "mw-fem" if "mw" in port_type else "lf-fem"
            is_input = "input" in port_type
            if is_input:
                fem_entry["input_ports"][port] = assignments
            else:
                fem_entry["output_ports"][port] = assignments

        # Numeric-first, then lexical: a single odd/legacy FEM or port id (e.g. a
        # non-digit string from a hand-edited wiring) must not raise int() and blank
        # the WHOLE diagram — it just sorts after the numeric ones.
        def _id_key(x):
            s = str(x)
            return (0, int(s)) if s.isdigit() else (1, s)

        result = {}
        for ctrl, fems in controllers.items():
            max_output_port = 0
            max_digital_port = 0
            sorted_fems = {}
            for fem_id in sorted(fems.keys(), key=_id_key):
                fem_data = fems[fem_id]
                out_ports = fem_data["output_ports"]
                in_ports = fem_data["input_ports"]
                dig_ports = fem_data["digital_ports"]
                for p in out_ports:
                    if str(p).isdigit():
                        max_output_port = max(max_output_port, int(p))
                for p in dig_ports:
                    if str(p).isdigit():
                        max_digital_port = max(max_digital_port, int(p))
                sorted_fems[fem_id] = {
                    "type": fem_data["type"] or "fem",
                    "output_ports": {p: out_ports[p] for p in sorted(out_ports.keys(), key=_id_key)},
                    "input_ports": {p: in_ports[p] for p in sorted(in_ports.keys(), key=_id_key)},
                    "digital_ports": {p: dig_ports[p] for p in sorted(dig_ports.keys(), key=_id_key)},
                }
            result[ctrl] = {"fems": sorted_fems, "max_output_port": max_output_port,
                            "max_digital_port": max_digital_port}

        # Octave RF path uses opx_output_I/Q + frequency_converter_up rather than a
        # single opx_output, which we don't collect — detect it so the UI can say the
        # layout isn't rendered yet instead of showing a misleading empty rack.
        octave_detected = any(
            isinstance(ch, dict) and (
                "opx_output_I" in ch or "opx_input_I" in ch or "frequency_converter_up" in ch)
            for qw in wiring_qubits.values() if isinstance(qw, dict)
            for ch in qw.values()
        )
        logger.debug("Instrument wiring: %d port assignments, %d controllers", len(port_assignments), len(result))
        return {
            "controllers": result,
            "stats": {
                "refs_seen": ref_stats["seen"],
                "refs_placed": ref_stats["placed"],
                "octave_detected": octave_detected,
            },
        }


# ======================================================================
# Internal helpers
# ======================================================================


def _valid_confusion_matrix(cm: Any) -> bool:
    """A readout confusion matrix must be **row-stochastic** (each row a
    probability vector: square, non-negative, sums ≈ 1). An unnormalized /
    counts / transposed matrix yields a confident-but-wrong "readout fidelity",
    so Phase 0 refuses to derive one from it (the metric reads missing instead).
    """
    if not isinstance(cm, list) or len(cm) < 2:
        return False
    n = len(cm)
    for row in cm:
        if not isinstance(row, list) or len(row) != n:
            return False
        s = 0.0
        for x in row:
            if isinstance(x, bool) or not isinstance(x, (int, float)) or x < -1e-9:
                return False
            # docs/141 4ac: NaN and inf pass every comparison above -- `nan <
            # -1e-9` is False and `abs(nan - 1.0) > 0.02` is False -- so a
            # matrix carrying one was accepted as row-stochastic and produced
            # a confident, green fidelity from a distribution that is not one.
            if not math.isfinite(x):
                return False
            s += x
        if abs(s - 1.0) > 0.02:
            return False
    return True


def _assignment_fidelity(confusion_matrix: Any) -> float | None:
    """Assignment fidelity (avg of the diagonal) of a validated confusion matrix."""
    if not _valid_confusion_matrix(confusion_matrix):
        return None
    try:
        return (confusion_matrix[0][0] + confusion_matrix[1][1]) / 2
    except (IndexError, TypeError):
        return None


#: A three-state readout metric needs three states. docs/141 4ac: a 2x2 stored
#: under ``gef_confusion_matrix`` averaged to a correct GE number wearing the
#: GEF label and scored against the GEF thresholds, which are deliberately
#: lower (warn 0.90 / fail 0.80) because three-state discrimination runs worse.
_GEF_MIN_STATES = 3


def _assignment_fidelity_n(confusion_matrix: Any, *,
                           n_min: int = _GEF_MIN_STATES) -> float | None:
    """Assignment fidelity of an n-state confusion matrix: the mean of the
    diagonal over ALL n states (a 3x3 g/e/f matrix reads all three — the 2x2
    formula above would silently ignore the f row). Same validator, plus a
    floor on n: a matrix with fewer than ``n_min`` states is not the thing this
    metric names, and a blank is what SM already shows for a chip that has no
    matrix at all."""
    if not _valid_confusion_matrix(confusion_matrix):
        return None
    if len(confusion_matrix) < n_min:
        return None
    try:
        n = len(confusion_matrix)
        return sum(confusion_matrix[i][i] for i in range(n)) / n
    except (IndexError, TypeError, ZeroDivisionError):
        return None


def _cm_diag(confusion_matrix: Any, idx: int) -> float | None:
    """A single diagonal element of a validated confusion matrix."""
    if not _valid_confusion_matrix(confusion_matrix) or len(confusion_matrix) <= idx:
        return None
    try:
        return confusion_matrix[idx][idx]
    except (IndexError, TypeError):
        return None


#: What a 2Q RB number actually MEASURES (docs/138). These are not
#: interchangeable and the difference is a factor of `average_gates_per_clifford`:
#:
#:   StandardRB     = 1 - EPC   the average CLIFFORD fidelity
#:   InterleavedRB  = 1 - EPG   the CZ GATE fidelity, from the alpha ratio
#:
#: Verified three ways against the customer's own chip and code: arithmetically
#: (StandardRB reproduces exactly from `(d-1)/d*(1-alpha)` with d=4;
#: InterleavedRB from `(d-1)/d*(1-a_int/a_ref)`), from the lab's node docstrings
#: ("fitted average Clifford fidelity" vs "fitted CZ-gate fidelity"), and from
#: their `compute_srb_fidelity`, which computes `average_gate_fidelity =
#: 1 - epc/average_gates_per_clifford` and then stores only the CLIFFORD one.
#:
#: SM cannot convert the Clifford number into a gate number: that divisor is a
#: node-namespace value and appears nowhere in state.json or in the run's saved
#: artefacts. So the honest move is to say which one each number is, never to
#: derive one from the other.
_RB_LEVEL = {
    "StandardRB": "clifford",
    "InterleavedRB": "gate",
    "IRB": "gate",                      # LabA's name for the same thing
    "Bell_State": "state",
}


def _rb_level(metric: Any) -> str | None:
    """``"gate"`` / ``"clifford"`` / ``"state"`` / ``"decay"`` / None.

    ``*_alpha`` is the RB exponential's decay base — a fit parameter, not a
    fidelity. It was being rendered as a percentage under a "Gate fidelity"
    heading, which reads as "this gate is 93.8% good" when it means nothing of
    the sort.
    """
    if not isinstance(metric, str):
        return None
    if metric.endswith("_alpha"):
        return "decay"
    return _RB_LEVEL.get(metric)


def _extract_pair_gate_fidelities(macros: dict) -> list[dict]:
    """Search all gate macros for fidelity data, return list of found results.

    Each row carries a ``level`` saying what it measures — see :data:`_RB_LEVEL`.
    """
    results = []
    if not isinstance(macros, dict):
        return results
    for gate_name, gate in macros.items():
        if not isinstance(gate, dict):
            continue
        fid = gate.get("fidelity", {})
        if not isinstance(fid, dict) or not fid:
            continue
        # Provenance: the RB run id for THIS gate's numbers, read once per gate and
        # attached to every fidelity row of the gate, so the popup can deep-link
        # each gate to its source dataset — even when this gate isn't the pair's
        # best (the old single edge-level cz_load_id only surfaced one pair).
        gate_load_id = fid.get("StandardRB_load_id") or fid.get("InterleavedRB_load_id")
        # Search for any key containing fidelity-like data
        for metric_name, metric_val in fid.items():
            # The *_load_id keys are provenance, not a measurement — don't emit
            # them as their own "fidelity" rows (they rendered as e.g. "529.0000").
            if isinstance(metric_name, str) and metric_name.endswith("_load_id"):
                continue
            if isinstance(metric_val, dict):
                entry = {"gate": gate_name, "metric": metric_name}
                for k, v in metric_val.items():
                    if isinstance(v, (int, float)):
                        entry[k] = v
                # Canonical scalar so the UI has one fidelity value regardless of
                # schema: older data stored a bare float (caught by the elif
                # below), newer LabA data nests e.g. StandardRB.average_gate_fidelity
                # or Bell_State.Fidelity. Without this, downstream readers of
                # ``gf.value`` (2Q RB panels, Overview tiles) skip every pair.
                if "value" not in entry:
                    for vk in ("average_gate_fidelity", "Fidelity", "fidelity"):
                        if isinstance(metric_val.get(vk), (int, float)):
                            entry["value"] = metric_val[vk]
                            break
                if len(entry) > 2:  # has at least one numeric value
                    entry["load_id"] = gate_load_id
                    entry["level"] = _rb_level(metric_name)
                    results.append(entry)
            elif isinstance(metric_val, (int, float)):
                results.append({"gate": gate_name, "metric": metric_name,
                                "value": metric_val, "load_id": gate_load_id,
                                "level": _rb_level(metric_name)})
    return results


def _extract_pair_confusion_offdiag(confusion: Any) -> list[float] | None:
    """Extract off-diagonal values from an NxN confusion matrix."""
    if not isinstance(confusion, list) or len(confusion) < 2:
        return None
    try:
        offdiag = []
        for i, row in enumerate(confusion):
            if not isinstance(row, list):
                return None
            for j, val in enumerate(row):
                if i != j and isinstance(val, (int, float)):
                    offdiag.append(val)
        return offdiag if offdiag else None
    except (IndexError, TypeError):
        return None


def _extract_gate_details(macros: dict) -> list[dict]:
    """Extract per-gate characterization: amplitude, coupler amp, length, phase shifts."""
    results = []
    if not isinstance(macros, dict):
        return results
    for gate_name, gate in macros.items():
        if not isinstance(gate, dict):
            continue
        flux = gate.get("flux_pulse_qubit", {})
        coupler_pulse = gate.get("coupler_flux_pulse", {})
        if not isinstance(flux, dict):
            flux = {}
        if not isinstance(coupler_pulse, dict):
            coupler_pulse = {}

        def _num(v: Any) -> float | None:
            return v if isinstance(v, (int, float)) else None

        entry: dict[str, Any] = {"name": gate_name}
        amp = _num(flux.get("amplitude"))
        if amp is not None:
            entry["amplitude"] = amp
        c_amp = _num(coupler_pulse.get("amplitude"))
        if c_amp is not None:
            entry["coupler_amp"] = c_amp
        length = _num(flux.get("length"))
        flat_length = _num(flux.get("flat_length"))
        if length is not None:
            entry["length"] = length
        if flat_length is not None:
            entry["flat_length"] = flat_length
        ps_ctrl = _num(gate.get("phase_shift_control"))
        ps_tgt = _num(gate.get("phase_shift_target"))
        if ps_ctrl is not None:
            entry["phase_ctrl"] = ps_ctrl
        if ps_tgt is not None:
            entry["phase_tgt"] = ps_tgt
        # Only include if we extracted at least one characterization value
        if len(entry) > 1:
            results.append(entry)
    return results


def _extract_cr_details(store: QuamStore, pair_name: str, pair_obj: dict) -> list[dict]:
    """CR/ZZ lever rows for the Chip Status edge popup (``gate_details`` shape).

    The flux extractor (:func:`_extract_gate_details`) reads only
    flux_pulse_qubit/coupler fields — empty on CR pairs, leaving the popup
    blank. This emits the drive levers + the emulated effective IF instead.
    Numeric-only (the popup renders numbers); missing keys are simply absent.
    """
    if not isinstance(pair_obj, dict):
        return []
    levers = cr_semantics.lever_map(pair_obj)
    if not levers and cr_semantics.cr_channel(pair_obj) is None:
        return []

    def _num(v: Any) -> float | None:
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    entry: dict[str, Any] = {"name": "cr"}
    for lever, suffix in levers.items():
        if lever.startswith(("macro_",)):
            continue
        try:
            val = _num(store.get_value(f"qubit_pairs.{pair_name}.{suffix}"))
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if val is not None:
            entry[lever] = val
    eff = cr_semantics.effective_frequencies(store, pair_name)
    if eff is not None and eff.if_hz is not None:
        entry["eff_if_mhz"] = round(eff.if_hz / 1e6, 3)
    return [entry] if len(entry) > 1 else []


def _resolve(store: QuamStore, value: Any, path_tuple: tuple[str, ...]) -> Any:
    """Resolve a JSON pointer (e.g. '#/qubits/q1/f_01') to its target value, or return as-is.

    Routes through *store*'s per-instance pointer cache so two QuamStore
    instances with same-named qubits don't share resolutions. See
    ``docs/32_red_team_phase_2.md`` finding 0.1.
    """
    if is_pointer(value) and not is_self_ref(value):
        return store.resolve_pointer(value, path_tuple)
    return value


def _deref_pulse_ref(
    store: QuamStore, val: Any, path_tuple: tuple[str, ...],
) -> tuple[dict, tuple[str, ...]]:
    """Follow a pointer-valued pulse field to its target dict.

    ``(value, effective_path)``: an inline dict passes through unchanged (the
    pre-existing behavior, byte-identical); a pointer (``"#/qubits/q1/z/
    operations/<op>"`` — the form run_build's CZ seeds and the pair_gates
    recipe write) returns the resolved TARGET dict together with the target's
    path tuple, so inner relative pointers keep resolving in the right frame.
    Anything unresolvable (dangling pointer, ``#./`` self-ref, non-dict
    target) degrades to ``({}, path)`` — the pair renders with blank fields
    instead of crashing the Pairs page.
    """
    if isinstance(val, dict):
        return val, path_tuple
    if isinstance(val, str) and is_pointer(val):
        resolved = _resolve(store, val, path_tuple)
        if isinstance(resolved, dict):
            if val.startswith("#/"):
                return resolved, tuple(val[2:].split("/"))
            return resolved, path_tuple
    return {}, path_tuple


def _pair_qubit_ref(store: QuamStore, raw: Any, path_tuple: tuple[str, ...]) -> Any:
    """Resolve a pair's ``qubit_control``/``qubit_target`` to a bare qubit id.

    Most chips point straight at the qubit (``"#/qubits/qA2"``) — splitting
    off the tail segment resolves that with zero pointer traversal (the old
    behavior). Real tunable-coupler chip families instead route
    control/target through a WIRING-side indirection (``"#/wiring/
    qubit_pairs/<w-pair>/c/control_qubit"`` -> ``"#/qubits/qA2"``) —
    splitting THAT tail gives the wiring key (``"control_qubit"``), not a
    qubit id, which is exactly the garbage that showed up as the Chip
    Status arrow label and the Pairs/Couplers table's Control/Target
    columns. ``store.resolve_pointer`` already recurses through pointer
    chains of any depth, so fully resolving lands on the qubit's own dict
    either way — read its ``"id"`` from there. Falls back to the old tail-
    segment heuristic when resolution doesn't yield a qubit dict (dangling
    pointer / already a bare string / etc.), so chips that never hit this
    indirection are byte-for-byte unaffected.
    """
    resolved = _resolve(store, raw, path_tuple)
    if isinstance(resolved, dict) and isinstance(resolved.get("id"), str):
        return resolved["id"]
    if isinstance(raw, str) and "/" in raw:
        return raw.split("/")[-1]
    return raw


def _get_nested(obj: Any, *keys: str) -> Any:
    """Safely traverse nested dicts, returning None if any key is missing."""
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _extract_port_label(store: QuamStore, q: dict, qname: str, channel: str) -> str | None:
    """Return a compact port label like 'con1/fem1/p2' for a qubit channel.

    Follows the pointer in state (e.g. '#/qubits/qA1/xy/opx_output'). On real
    chips the chain resolves ALL the way to the port dict itself
    (state → wiring → ports), so the label is built from the port's identity
    fields; a string result means resolution stopped at a dangling/partial
    reference and is parsed as before. (The string-only version returned None
    for EVERY fully-resolving chip — the docs/92 feedline grouping was the
    first real consumer and exposed it.) Resolution goes through *store*'s
    per-instance pointer cache.
    """
    ch = q.get(channel)
    if not isinstance(ch, dict):        # absent or explicitly-null channel
        return None
    raw = ch.get("opx_output")
    if not raw:
        return None
    resolved = _resolve(store, raw, ("qubits", qname, channel, "opx_output"))
    if isinstance(resolved, dict):
        ctrl = resolved.get("controller_id")
        fem = resolved.get("fem_id")
        port = resolved.get("port_id")
        if ctrl is not None and port is not None:
            return (f"{ctrl}/fem{fem}/p{port}" if fem is not None
                    else f"{ctrl}/p{port}")
        return None
    ref_str = resolved if isinstance(resolved, str) else None
    return _port_ref_to_label(ref_str)


def _port_ref_to_label(ref_str: str | None) -> str | None:
    """Parse '#/ports/mw_outputs/con1/1/2' → 'con1/fem1/p2'."""
    if not ref_str or not isinstance(ref_str, str) or not ref_str.startswith("#/ports/"):
        return ref_str
    parts = ref_str.split("/")
    if len(parts) >= 6:
        ctrl, fem, port = parts[3], parts[4], parts[5]
        return f"{ctrl}/fem{fem}/p{port}"
    return ref_str


def _parse_port_ref(ref_str: str) -> tuple[str, str, str, str] | None:
    """Parse a port reference into (port_type, ctrl, fem, port) or None.

    Handles two formats:
    - Pointer: ``'#/ports/mw_outputs/con1/1/2'`` → ``('mw_outputs', 'con1', '1', '2')``
    - Label:   ``'MW-FEM/1/2'``  → ``('mw_outputs', 'con1', '1', '2')``
               ``'LF-FEM/5/1'``  → ``('analog_outputs', 'con1', '5', '1')``
    """
    if not ref_str or not isinstance(ref_str, str):
        return None
    if ref_str.startswith("#/ports/"):
        parts = ref_str.split("/")
        if len(parts) < 6:
            return None
        return parts[2], parts[3], parts[4], parts[5]
    # Label format: "MW-FEM/1/2" or "LF-FEM/5/1"
    upper = ref_str.upper()
    if upper.startswith("MW-FEM/"):
        parts = ref_str.split("/")
        if len(parts) >= 3:
            return "mw_outputs", "con1", parts[1], parts[2]
    if upper.startswith("LF-FEM/"):
        parts = ref_str.split("/")
        if len(parts) >= 3:
            return "analog_outputs", "con1", parts[1], parts[2]
    return None


def _follow_port_ref(root: dict, ref_str: str, max_hops: int = 4) -> str | None:
    """Follow ABSOLUTE pointer hops until the ref lands under ``#/ports/``.

    A channel-level digital ref is usually two-hop —
    ``#/wiring/qubits/q1/qt/digital_output`` → ``#/ports/digital_outputs/con1/4/1``
    — and ``_parse_port_ref`` only reads the final ``#/ports/...`` spelling.
    Returns that final string, or None when the chain dead-ends, is relative
    (``#../``/``#./`` — no anchor to walk from here), or loops past max_hops.
    """
    cur: Any = ref_str
    for _ in range(max_hops):
        if not isinstance(cur, str) or not cur.startswith("#/"):
            return None
        if cur.startswith("#/ports/"):
            return cur
        obj: Any = root
        try:
            for seg in cur[2:].split("/"):
                obj = obj[seg]
        except (KeyError, TypeError, IndexError):
            return None
        cur = obj
    return None


def _resolve_port_dict(root: dict, ref_str: str) -> dict:
    """Traverse the merged dict to find the port's config (band, LO, power, etc.).

    Only works for pointer-format refs (``#/ports/...``); returns ``{}`` for label-format refs.
    """
    if not ref_str or not isinstance(ref_str, str) or not ref_str.startswith("#/"):
        return {}
    parts = ref_str[2:].split("/")  # strip leading "#/"
    obj: Any = root
    try:
        for p in parts:
            obj = obj[p]
        return obj if isinstance(obj, dict) else {}
    except (KeyError, TypeError):
        return {}


# ======================================================================
# Safe filter expression evaluator
# ======================================================================

_ALLOWED_OPS = {
    ast.Gt: operator.gt,
    ast.Lt: operator.lt,
    ast.GtE: operator.ge,
    ast.LtE: operator.le,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}

_ALLOWED_BOOL_OPS = {
    ast.And: all,
    ast.Or: any,
}


def _eval_filter(qubit_dict: dict[str, Any], expr: str) -> bool:
    """Evaluate a filter expression against a qubit's flat property dict.

    Uses ``ast.parse()`` with a strict whitelist. Never calls ``eval()``.
    Supported: comparisons (>, <, >=, <=, ==, !=), logical (and, or, not),
    numeric/string literals, and property names from qubit_dict keys.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        logger.warning("Invalid filter expression: %r", expr)
        return True

    try:
        return bool(_eval_node(tree.body, qubit_dict))
    except Exception:
        logger.warning("Filter evaluation failed for %r", expr)
        return True


def _eval_node(node: ast.AST, ctx: dict[str, Any]) -> Any:
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, ctx)
            op_func = _ALLOWED_OPS.get(type(op))
            if op_func is None:
                raise ValueError(f"Unsupported operator: {type(op).__name__}")
            if left is None or right is None:
                return False
            if not op_func(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.BoolOp):
        func = _ALLOWED_BOOL_OPS.get(type(node.op))
        if func is None:
            raise ValueError(f"Unsupported bool op: {type(node.op).__name__}")
        return func(_eval_node(v, ctx) for v in node.values)

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval_node(node.operand, ctx)

    if isinstance(node, ast.Name):
        if node.id not in ctx:
            raise ValueError(f"Unknown property: {node.id}")
        return ctx[node.id]

    if isinstance(node, ast.Constant):
        return node.value

    raise ValueError(f"Unsupported AST node: {type(node).__name__}")
