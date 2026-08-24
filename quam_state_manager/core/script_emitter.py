"""Emit an editable Python bundle that reproduces a wizard-generated chip.

Customer requirement: the Generate wizard must write "not only state/wiring
files but also generate/populate python scripts in a user-defined folder, so
the user can modify along with a code IDE later". This module renders that
bundle — readable, tutorial-style Python with the actual values INLINED:

    01_make_wiring.py     network / instruments / wiring data blocks →
                          allocate_wiring → build_quam_wiring → build_quam
    02_build_machine.py   POPULATE / QUBIT_PAIRS / PAIR_GATE data blocks +
                          the populate & 2Q-gate machinery → machine.save()
    03_generate_config.py load the machine → generate_config() sanity run
    README.md             run order, env pins, edit-and-rerun contract

Fidelity strategy (two pillars):

1. **Insertion-order mirroring** — 01 adds connectivity lines in EXACTLY the
   order ``run_build.build_connectivity`` does (resonator groups → TWPAs →
   drive/flux/coupler/CR in spec-lines order) and calls ``allocate_wiring``
   once, so the allocator lands on the same ports as the wizard build did —
   no fragile allocation-key plumbing. The wizard's actual allocated ports
   are still inlined as comments for reference/manual pinning.

2. **Verbatim machinery** — 02's populate + gate-seeding functions are
   extracted from ``generator/run_build.py`` at emit time via
   ``inspect.getsource`` (the module plain-loads; its QM imports are
   function-local). The emitted code IS the code the wizard ran — in-sync by
   construction, no hand-transcribed mirror to drift.

Pure string generation — the State Manager process never imports the QM
stack here. Sibling module: :mod:`core.regen_script` (the Re-generate flow's
one-file calibration-repo recipe; this bundle targets the wizard's own
quam_builder idiom instead so it runs with just the QM stack, no
``quam_config`` template repo needed).
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
from datetime import datetime, timezone
from pathlib import Path
from pprint import pformat

from quam_state_manager.core import qdac_lf_recipe
from quam_state_manager.core.regen_script import _fmt

logger = logging.getLogger(__name__)


def _norm_index(qubit_id):
    """run_build._norm_index EXACTLY (int when all-digits, else string) — the
    emitted wirer calls must hand the allocator the same index types the
    wizard build did. (regen_script's variant is string-only; not used here.)
    """
    s = str(qubit_id)
    if s[:1] in ("q", "Q"):
        s = s[1:]
    return int(s) if s.isdigit() else s

_RUN_BUILD_PATH = Path(__file__).resolve().parent.parent / "generator" / "run_build.py"

# The run_build machinery 02 embeds, in dependency order. Constants are
# rendered by repr; functions by inspect.getsource. A missing name raises at
# emit time (and fails the golden test) — the moment run_build refactors,
# this list is the single thing to update.
_RUNTIME_CONSTS = ("_BAND_TO_DELAY_NS", "_CZ_VARIANTS", "_PULSE_HOMES")
# docs/136 — emitted ONLY for a chip that declares a QDAC. On every other chip
# this is ~200 lines of dead weight, and a recipe nobody wants to read is not
# an editable recipe.
_QDAC_CONSTS = ("QDAC_COMPONENTS_MODULE",)
_QDAC_FUNCS = ("_import_qdac_components", "_find_bias_tee_class",
               "_attach_qdac_bias", "_inject_qdac_state",
               "_inject_qdac_trigger_wiring")
_RUNTIME_FUNCS = (
    "_norm_index", "_parse_pair", "_quam_pair_id", "_norm_pair_qubits",
    "_match_populate_pairs",
    "_num", "_target_lo",
    "_band_for", "_delay_for_band", "_apply_lf_delay",
    "_set_port_lo", "_set_channel_lo", "_operation",
    "_apply_resonator", "_apply_qubit", "_apply_flux", "_apply_pulses",
    "_make_cz_gate", "_apply_pairs", "apply_populate",
    "_apply_dual_upconverters", "_pin_cores",
    "_pulse_class", "_cz_variant_pulses", "_seed_cz_variant",
    "_cr_flavor", "_import_cr_gate", "_make_cr_gate", "_seed_cr_gate",
    "_import_stark_cz_gate", "_make_stark_cz_gate", "_make_xy_detuned",
    "_seed_zz_gate",
    "_cz_order_warning", "_finalize_pair_gates",
    "_split_port_pointer", "_walk_state", "_link_input_downconverters_to_outputs",
)

_rb_module = None


def _run_build():
    """Plain-load generator/run_build.py (QM imports are function-local —
    the same loader pattern tests/test_run_build_delay.py uses)."""
    global _rb_module
    if _rb_module is None:
        spec = importlib.util.spec_from_file_location(
            "run_build_for_emitter", _RUN_BUILD_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _rb_module = mod
    return _rb_module


def _runtime_block(extra_consts: tuple = (), extra_funcs: tuple = ()) -> str:
    """The verbatim run_build machinery 02 embeds (cached per process)."""
    mod = _run_build()
    parts = [
        "# ======================================================================",
        "# Machinery — VERBATIM from the State Manager's generator/run_build.py",
        "# (the exact code the wizard ran to build this chip). Edit the DATA",
        "# blocks above instead; change these only if you know the QUAM stack.",
        "# ======================================================================",
        "",
    ]
    for name in tuple(_RUNTIME_CONSTS) + tuple(extra_consts):
        parts.append(f"{name} = {getattr(mod, name)!r}")
    parts.append("")
    for name in tuple(_RUNTIME_FUNCS) + tuple(extra_funcs):
        parts.append(inspect.getsource(getattr(mod, name)).rstrip("\n"))
        parts.append("")
    return "\n".join(parts)


# --- channel constraint rendering -------------------------------------------

def _constraint(ch) -> str:
    """The qualang_tools spec call for a wizard channel dict, or ``None``.

    Mirrors run_build._make_constraint; only the fields the wizard actually
    pinned are written (unspecified kwargs default to None = free)."""
    if not ch:
        return "None"
    kind = ch.get("kind")
    if kind == "mw_fem":
        fields = [("con", ch.get("con")), ("slot", ch.get("slot")),
                  ("in_port", ch.get("in_port")), ("out_port", ch.get("out_port"))]
        fn = "mw_fem_spec"
    elif kind == "lf_fem":
        fields = [("con", ch.get("con")),
                  ("in_slot", ch.get("in_slot", ch.get("slot"))),
                  ("in_port", ch.get("in_port")),
                  ("out_slot", ch.get("out_slot", ch.get("slot"))),
                  ("out_port", ch.get("out_port"))]
        fn = "lf_fem_spec"
    elif kind == "opx":
        fields = [("con", ch.get("con")), ("in_port", ch.get("in_port")),
                  ("out_port", ch.get("out_port"))]
        fn = "opx_spec"
    elif kind == "octave":
        fields = [("index", ch.get("index")), ("rf_in", ch.get("rf_in")),
                  ("rf_out", ch.get("rf_out"))]
        fn = "octave_spec"
    else:
        return "None"
    args = ", ".join(f"{k}={v!r}" for k, v in fields if v is not None)
    return f"{fn}({args})" if args else "None"


# Allocation line-type keys (WiringLineType.value) for the reference comments.
_ALLOC_KEY = {"resonator": "rr", "drive": "xy", "flux": "z",
              "coupler": "c", "cross_resonance": "cr", "zz_drive": "zz"}


def _pair_pin(allocation, element, line_type) -> str | None:
    """An inlined ``mw_fem_spec(...)`` pin from the wizard build's allocation
    for a CR/ZZ pair line — the shared-port bundle must be DETERMINISTIC even
    if the user reorders lines, so the pins are frozen at emit time."""
    if not allocation:
        return None
    try:
        key = _run_build()._quam_pair_id(element)
    except Exception:  # noqa: BLE001
        return None
    chans = (allocation.get(key) or {}).get(_ALLOC_KEY.get(line_type, ""), [])
    for c in chans:
        con, slot, port = c.get("con"), c.get("slot"), c.get("port")
        if None not in (con, slot, port):
            return f"mw_fem_spec(con={con!r}, slot={slot!r}, out_port={port!r})"
    return None


def _quam_cls_import(flux_tunable: bool, zz_lines: bool,
                     quam_class: str | None = None) -> list[str]:
    """The QuamCls import block: flux root, ZZ-drive root (guarded — older
    builders predate FixedFrequencyZZDriveQuam), or plain fixed-frequency."""
    # docs/136 — an explicit root wins over the derived one. A QDAC chip is
    # rooted at a customer subclass (its `qubits` union accepts the QDAC-biased
    # transmon and it declares the `qdac` field); loading it as stock
    # FluxTunableQuam fails on the first QDAC qubit, so a recipe that derived
    # the root from line types would not open the chip it just wrote.
    if quam_class and "." in quam_class:
        mod, _, cls = quam_class.rpartition(".")
        return [f"from {mod} import {cls} as QuamCls"]
    if flux_tunable:
        return ["from quam_builder.architecture.superconducting.qpu import ("
                "FluxTunableQuam as QuamCls)"]
    if zz_lines:
        return [
            "try:",
            "    from quam_builder.architecture.superconducting.qpu import (",
            "        FixedFrequencyZZDriveQuam as QuamCls)",
            "except ImportError:  # older quam_builder: qubits lack xy_detuned",
            "    from quam_builder.architecture.superconducting.qpu import (",
            "        FixedFrequencyQuam as QuamCls)",
        ]
    return ["from quam_builder.architecture.superconducting.qpu import ("
            "FixedFrequencyQuam as QuamCls)"]


def _alloc_comment(allocation, element, line_type) -> str:
    """'  # allocated: con1 s2 p3 (out), con1 s2 p1 (in)' — best-effort."""
    if not allocation:
        return ""
    key = element
    if line_type in ("coupler", "cross_resonance", "zz_drive"):
        try:
            key = _run_build()._quam_pair_id(element)
        except Exception:  # noqa: BLE001 — comment only, never fatal
            return ""
    else:
        key = "q" + str(_norm_index(element))
    chans = (allocation.get(key) or {}).get(_ALLOC_KEY.get(line_type, ""), [])
    if not chans:
        return ""
    bits = []
    for c in chans:
        io = c.get("io_type")
        bits.append("con%s s%s p%s%s" % (
            c.get("con"), c.get("slot"), c.get("port"),
            f" ({io})" if io else ""))
    return "  # allocated: " + ", ".join(bits)


# --- 01_make_wiring.py -------------------------------------------------------

def _emit_wiring(spec: dict, allocation: dict, chip: str, stamp: str) -> str:
    net = spec.get("network", {}) or {}
    instruments = spec.get("instruments", {}) or {}
    lines = spec.get("lines", []) or []
    flux_tunable = any(ln.get("line") in ("flux", "coupler") for ln in lines)
    zz_lines = any(ln.get("line") == "zz_drive" for ln in lines)
    shared = spec.get("cr_port_mode") == "shared_xy"

    out: list[str] = []
    w = out.append
    w("#!/usr/bin/env python")
    w('"""%s — step 1/3: instruments, wiring + base machine.' % chip)
    w("")
    w("Generated by QUAM State Manager (%s). Edit the data blocks and re-run:" % stamp)
    w("")
    w("    python 01_make_wiring.py [STATE_DIR]      # default ./quam_state")
    w("")
    w("Lines are added in the SAME order the wizard used and allocated in one")
    w("pass, so the allocator lands on the wizard's exact ports (each line's")
    w("allocated port is noted in a comment — pin it via `constraints=` to")
    w("survive re-ordering). Point STATE_DIR at an EMPTY folder: quam loads")
    w("every .json under it, so stray files corrupt the build.")
    w('"""')
    w("import os")
    w("import sys")
    w("import inspect")
    w("")
    w('STATE_DIR = sys.argv[1] if len(sys.argv) > 1 else "./quam_state"')
    w("os.makedirs(STATE_DIR, exist_ok=True)")
    w("_stray = [f for f in os.listdir(STATE_DIR) if f.endswith('.json')")
    w("          and f not in ('state.json', 'wiring.json')]")
    w("if _stray:")
    w("    sys.exit(f'STATE_DIR contains stray JSON files {_stray} — quam would '")
    w("             'recursively load them. Use an empty folder.')")
    w('os.environ["QUAM_STATE_PATH"] = os.path.abspath(STATE_DIR)')
    w("")
    w("from qualang_tools.wirer import Connectivity, Instruments, allocate_wiring")
    w("from qualang_tools.wirer.wirer.channel_specs import (  # noqa: F401")
    w("    mw_fem_spec, lf_fem_spec, opx_spec, octave_spec,")
    w(")")
    w("from quam_builder.builder.qop_connectivity import build_quam_wiring")
    w("from quam_builder.builder.superconducting import build_quam")
    for ln_ in _quam_cls_import(flux_tunable, zz_lines, spec.get("quam_class")):
        w(ln_)
    w("")
    w("# ============================ EDIT: network ============================")
    w("HOST = %s" % _fmt(net.get("host")))
    w("CLUSTER = %s" % _fmt(net.get("cluster_name")))
    w("PORT = %s" % _fmt(net.get("port")))
    w("")
    w("# ========================== EDIT: instruments =========================")
    w("instruments = Instruments()")
    for ctrl in instruments.get("controllers", []):
        con = ctrl.get("con")
        mw_slots = sorted(f["slot"] for f in ctrl.get("fems", []) if f.get("fem") == "mw")
        lf_slots = sorted(f["slot"] for f in ctrl.get("fems", []) if f.get("fem") == "lf")
        if mw_slots:
            w("instruments.add_mw_fem(controller=%r, slots=%r)" % (con, mw_slots))
        if lf_slots:
            w("instruments.add_lf_fem(controller=%r, slots=%r)" % (con, lf_slots))
    for opx in instruments.get("opx_plus", []) or []:
        w("instruments.add_opx_plus(controllers=%r)" % (opx.get("con"),))
    for octv in instruments.get("octaves", []) or []:
        w("instruments.add_octave(indices=%r)" % (octv.get("index"),))
    w("")
    w("# ============================= EDIT: wiring ===========================")
    w("# SAME insertion order as the wizard (this is what reproduces its ports).")
    w("connectivity = Connectivity()")
    w("")

    # 1) resonator groups — first-seen order, exactly like build_connectivity.
    res_groups: dict = {}
    for ln in lines:
        if ln.get("line") != "resonator":
            continue
        g = ln.get("group", "__solo__%s" % ln.get("element"))
        res_groups.setdefault(g, []).append(ln)
    if res_groups:
        w("# readout feed-lines (multiplexed: qubits sharing one MW in/out port)")
        for items in res_groups.values():
            qs = [_norm_index(it["element"]) for it in items]
            first = items[0]
            w("connectivity.add_resonator_line(qubits=%r, constraints=%s)%s"
              % (qs, _constraint(first.get("channel")),
                 _alloc_comment(allocation, first["element"], "resonator")))
        w("")

    # 2) TWPAs — sorted element order, exactly like build_connectivity.
    twpa_pumps = {ln["element"]: ln.get("channel")
                  for ln in lines if ln.get("line") == "twpa_pump"}
    twpa_iso = {ln["element"]: ln.get("channel")
                for ln in lines if ln.get("line") == "twpa_isolation"}
    twpa_elems = sorted(set(twpa_pumps) | set(twpa_iso))
    if twpa_elems:
        w("# readout TWPA pumps (add_twpa_lines seeds pump + pump_ on the port)")
        for tid in twpa_elems:
            args = "twpas=[%r]" % tid
            if twpa_pumps.get(tid) is not None:
                args += ", pump_constraints=%s" % _constraint(twpa_pumps[tid])
            if twpa_iso.get(tid) is not None:
                args += ", isolation_constraints=%s" % _constraint(twpa_iso[tid])
            w("connectivity.add_twpa_lines(%s)" % args)
        w("")

    # 3) drive / flux (+ pair lines when NOT shared) — spec-lines order.
    _PAIR_FN = {"coupler": "add_qubit_pair_flux_lines",
                "cross_resonance": "add_qubit_pair_cross_resonance_lines",
                "zz_drive": "add_qubit_pair_zz_drive_lines"}

    def _emit_pair_line(ln, *, pin_from_allocation: bool) -> None:
        lt = ln.get("line")
        el = ln.get("element")
        c = _constraint(ln.get("channel"))
        # Allocation-frozen pins are MW-only (CR/ZZ) — a coupler is a DC line
        # on an LF-FEM; rendering its LF allocation as mw_fem_spec would make
        # add_qubit_pair_flux_lines unsatisfiable (mirror run_build's
        # _add_one_pair_line, which pins only cross_resonance/zz_drive).
        if (c == "None" and pin_from_allocation
                and lt in ("cross_resonance", "zz_drive")):
            c = _pair_pin(allocation, el, lt) or "None"
        note = _alloc_comment(allocation, el, lt)
        ctl, tgt = str(el).split("-", 1)
        w("connectivity.%s(qubit_pairs=[(%r, %r)], constraints=%s)%s"
          % (_PAIR_FN[lt], _norm_index(ctl), _norm_index(tgt), c, note))

    # Partition mirror of run_build.allocate_full (audit round 2): in shared
    # mode only CR/ZZ lines with a frozen pin may allocate per-line from the
    # freed pool; couplers and unpinnable CR/ZZ join the FIRST call, where
    # in-call blocking guarantees collision-free allocation.
    def _pinnable(ln) -> bool:
        if ln.get("line") not in ("cross_resonance", "zz_drive"):
            return False
        if ln.get("channel"):
            return True
        return _pair_pin(allocation, ln.get("element"), ln.get("line")) is not None

    emitted_any = False
    for ln in lines:
        lt = ln.get("line")
        el = ln.get("element")
        c = _constraint(ln.get("channel"))
        note = _alloc_comment(allocation, el, lt)
        if lt == "drive":
            w("connectivity.add_qubit_drive_lines(qubits=%r, constraints=%s)%s"
              % (_norm_index(el), c, note))
            emitted_any = True
        elif lt == "flux":
            w("connectivity.add_qubit_flux_lines(qubits=%r, constraints=%s)%s"
              % (_norm_index(el), c, note))
            emitted_any = True
        elif lt in _PAIR_FN and (not shared or not _pinnable(ln)):
            _emit_pair_line(ln, pin_from_allocation=False)
            emitted_any = True
    if emitted_any:
        w("")

    if shared:
        # Shared-port CR layout (dual upconverter, docs/54): allocate the
        # qubit lines (+ any unpinnable pair lines) first with
        # block_used_channels=False (the used xy ports return to the pool at
        # call end), then each PINNED CR/ZZ line onto its control's xy port
        # with its OWN allocate call — within one call, channels used by
        # earlier specs stay blocked, so two CR lines pinned to the same
        # control port would collide (the customer script's
        # allocate-after-every-add idiom). Pins are INLINED from the wizard's
        # allocation so the script stays deterministic.
        w("# shared-port CR: per-line allocation (CR/ZZ ride the control's")
        w("# xy port, upconverter 2 — pins frozen from the wizard's build)")
        w("allocate_wiring(connectivity, instruments, block_used_channels=False)")
        w("")
        for ln in lines:
            if ln.get("line") in _PAIR_FN and _pinnable(ln):
                _emit_pair_line(ln, pin_from_allocation=True)
                w("allocate_wiring(connectivity, instruments, block_used_channels=False)")
    else:
        w("allocate_wiring(connectivity, instruments)")
    w("")
    w("# =============================== build ================================")
    w("machine = QuamCls()")
    w("# Older quam_builder takes an explicit path kwarg; newer reads")
    w("# QUAM_STATE_PATH — the same shim the wizard build uses.")
    w('_kwargs = {"port": PORT}')
    w('if "path" in inspect.signature(build_quam_wiring).parameters:')
    w('    _kwargs["path"] = os.environ["QUAM_STATE_PATH"]')
    w("build_quam_wiring(connectivity, HOST, CLUSTER, machine, **_kwargs)")
    w("machine = QuamCls.load()")
    w("build_quam(machine)")
    w('print(f"wiring built: {len(machine.qubits)} qubits, "')
    w('      f"{len(machine.qubit_pairs)} pairs -> {os.environ[\'QUAM_STATE_PATH\']}")')
    w("")
    return "\n".join(out)


# --- 02_build_machine.py -----------------------------------------------------

def _qdac_pins(spec: dict, allocation: dict) -> dict:
    """``{qid: [con, slot, port]}`` — each QDAC qubit's trigger output.

    A pin in the spec wins (it records how the bench is actually cabled); the
    build's own allocation fills in the rest. Resolved HERE, at emit time,
    because by the time someone runs the recipe the ports are a fact about a
    bench rather than a choice — re-allocating them from an emitted script
    could hand the same chip a different cable map on every run.
    """
    out: dict = {}
    for qid, fields in ((spec.get("qdac") or {}).get("qubits") or {}).items():
        pin = (fields or {}).get("trigger_pin")
        if isinstance(pin, dict) and all(
                isinstance(pin.get(k), int) for k in ("con", "slot", "port")):
            out[qid] = [pin["con"], pin["slot"], pin["port"]]
            continue
        for ch in ((allocation.get(qid) or {}).get("qt") or []):
            if all(isinstance(ch.get(k), int) for k in ("con", "slot", "port")):
                out[qid] = [ch["con"], ch["slot"], ch["port"]]
                break
    return out


def _emit_build(spec: dict, chip: str, stamp: str, allocation: dict | None = None) -> str:
    lines = spec.get("lines", []) or []
    flux_tunable = any(ln.get("line") in ("flux", "coupler") for ln in lines)
    zz_lines = any(ln.get("line") == "zz_drive" for ln in lines)
    pair_gate = (spec.get("pair_gate") or "").lower()
    cr_port_mode = spec.get("cr_port_mode") or ""
    # The slim line list _apply_dual_upconverters walks (control→target pairs).
    pair_line_data = [{"element": ln.get("element"), "line": ln.get("line")}
                      for ln in lines
                      if ln.get("line") in ("cross_resonance", "zz_drive")]
    populate = spec.get("populate", {}) or {}
    qubit_pairs = [list(p) for p in (spec.get("qubit_pairs") or [])]

    out: list[str] = []
    w = out.append
    w("#!/usr/bin/env python")
    w('"""%s — step 2/3: populate physics values + 2Q gates, then save.' % chip)
    w("")
    w("Generated by QUAM State Manager (%s). Run AFTER 01_make_wiring.py," % stamp)
    w("same STATE_DIR:")
    w("")
    w("    python 02_build_machine.py [STATE_DIR]   # default ./quam_state")
    w("")
    w("Edit the DATA blocks (POPULATE / QUBIT_PAIRS / PAIR_GATE) and re-run 01")
    w("then 02 to rebuild. The machinery below the data is copied verbatim from")
    w("the State Manager's own build subprocess — including the readout-LO")
    w("pointer fix-up — so this bundle reproduces state.json byte-for-byte.")
    w('"""')
    w("import json")
    w("import os")
    w("import sys")
    w("from pathlib import Path")
    w("")
    w('STATE_DIR = sys.argv[1] if len(sys.argv) > 1 else "./quam_state"')
    w('os.environ["QUAM_STATE_PATH"] = os.path.abspath(STATE_DIR)')
    w("")
    for ln_ in _quam_cls_import(flux_tunable, zz_lines, spec.get("quam_class")):
        w(ln_)
    w("")
    w("# ============================ EDIT: populate ==========================")
    w("# Base SI units (Hz, ns, V, dimensionless amp). Blank/missing keys keep")
    w("# quam_builder defaults (x180: amp 0.1 / len 40 ns; anharmonicity 200e6).")
    w("POPULATE = %s" % pformat(populate, indent=4, width=88, sort_dicts=True))
    w("")
    w("# [control, target] per pair — for CZ chips the wizard ordered these")
    w("# control = higher-f qubit; the flux pulse plays on the moving qubit.")
    w("QUBIT_PAIRS = %s" % pformat(qubit_pairs, indent=4, width=88))
    w("")
    w("# 2Q-gate family: 'cz_tunable' | 'cz_fixed' | 'cr' | '' (no wizard gate).")
    w("PAIR_GATE = %r" % pair_gate)
    w("")
    w("# CR drive port mode: '' | 'dedicated' | 'shared_xy' (the customer's")
    w("# dual-upconverter layout — CR/ZZ ride the control's xy port, LO 2).")
    w("CR_PORT_MODE = %r" % cr_port_mode)
    w("PAIR_LINES = %s" % pformat(pair_line_data, indent=4, width=88))
    w("")

    # -- QDAC-II (docs/136) — emitted only when the chip declares one --------
    qdac_spec = spec.get("qdac") or {}
    qdac_qubits = qdac_spec.get("qubits") or {}
    if qdac_qubits:
        pins = _qdac_pins(spec, allocation or {})
        w("# ============================ EDIT: QDAC-II ==========================")
        w("# An external DC source biasing %d of this chip's qubits. Needs the"
          % len(qdac_qubits))
        w("# customer-local quam_config.qdac_components; without it the qubits")
        w("# below build with NO bias component and the script says so.")
        w("#")
        w("# QDAC_PINS is the OPX digital output whose marker arms each qubit's")
        w("# channel. One output drives one QDAC ext trigger input and arms every")
        w("# channel on it, so several qubits sharing a pin is normal cabling,")
        w("# not a mistake. These are the ports the wizard allocated/you pinned —")
        w("# edit them to match your bench.")
        w("QDAC = %s" % pformat(
            {k: v for k, v in qdac_spec.items() if k != "qubits"},
            indent=4, width=88, sort_dicts=True))
        w("QDAC_QUBITS = %s" % pformat(qdac_qubits, indent=4, width=88, sort_dicts=True))
        w("QDAC_PINS = %s" % pformat(pins, indent=4, width=88, sort_dicts=True))
        w("")

    if qdac_qubits:
        w(_runtime_block(_QDAC_CONSTS, _QDAC_FUNCS))
    else:
        w(_runtime_block())
    w("# =============================== run ==================================")
    w("machine = QuamCls.load()")
    w('_spec = {"populate": POPULATE, "qubit_pairs": QUBIT_PAIRS,')
    w('         "cr_port_mode": CR_PORT_MODE, "lines": PAIR_LINES}')
    if qdac_qubits:
        # BEFORE apply_populate: its z.operations guards, and _finalize_pair_
        # gates', both branch on the qubit's final class.
        w("_qdac_warnings = []")
        w("_qdac_wired = _attach_qdac_bias(machine, QDAC_QUBITS,")
        w("                               {q: tuple(p) for q, p in QDAC_PINS.items()},")
        w("                               _qdac_warnings)")
        w("for _w in _qdac_warnings:")
        w('    print(f"WARNING: {_w}", file=sys.stderr)')
        w("")
    w('apply_populate(machine, POPULATE, handle_pairs=(PAIR_GATE == ""))')
    w("for _w in _apply_dual_upconverters(machine, _spec):")
    w('    print(f"WARNING: {_w}", file=sys.stderr)')
    w('if PAIR_GATE in ("cz_fixed", "cz_tunable", "cr"):')
    w("    for _w in _finalize_pair_gates(machine, _spec, PAIR_GATE):")
    w('        print(f"WARNING: {_w}", file=sys.stderr)')
    w('if (POPULATE.get("options") or {}).get("pin_cores"):')
    w("    _pin_cores(machine)")
    w("machine.save()")
    w("")
    if qdac_qubits:
        # Both of these are FILE patches, and both must be post-save: the QPU
        # root class declares no `qdac` field (so `machine.qdac = ...` would be
        # rejected), and quam_builder's wiring vocabulary has no "qt" line type
        # (so the trigger's wiring entry cannot go through machine.wiring).
        # Same reasoning, and the same code, as the wizard's own build.
        w("_qdac_inst = dict(QDAC)")
        w('_qdac_inst.pop("qubits", None)')
        w('_qdac_inst.pop("share_cables", None)')
        w("try:")
        w("    from quam_config.qdac_components import QdacInstrument as _QI")
        w('    _qdac_inst = {k: _qdac_inst.get(k) for k in')
        w('                  ("id", "communication_type", "ip_address", "port",')
        w('                   "usb_device", "lib")}')
        w('    _qdac_inst.setdefault("id", "qdac")')
        w('    _qdac_inst["__class__"] = f"{_QI.__module__}.{_QI.__name__}"')
        w("except Exception as _exc:")
        w('    print(f"WARNING: no QdacInstrument class ({_exc}) — top-level '
          '\'qdac\' entry not written.", file=sys.stderr)')
        w("    _qdac_inst = None")
        w("_inject_qdac_state(Path(STATE_DIR) / \"state.json\", _qdac_inst)")
        w("_inject_qdac_trigger_wiring(Path(STATE_DIR) / \"wiring.json\", _qdac_wired)")
        w("")
    w("# Readout-LO constraint lock (the wizard's post-save fix-up): each MW")
    w("# input port's downconverter_frequency becomes a JSON pointer to its")
    w("# paired output port's upconverter_frequency (one physical LO).")
    w("_link_input_downconverters_to_outputs(")
    w('    Path(STATE_DIR) / "state.json", Path(STATE_DIR) / "wiring.json")')
    w("")
    w('print(f"populated + saved: {len(machine.qubits)} qubits, "')
    w('      f"{len(machine.qubit_pairs)} pairs, "')
    w('      f"macros: { {p: sorted(m.macros) for p, m in machine.qubit_pairs.items()} }")')
    w("")
    return "\n".join(out)


# --- 03_generate_config.py ---------------------------------------------------

def _emit_config_check(chip: str, stamp: str, quam_class: str | None = None) -> str:
    out: list[str] = []
    w = out.append
    w("#!/usr/bin/env python")
    w('"""%s — step 3/3: sanity-run machine.generate_config().' % chip)
    w("")
    w("Generated by QUAM State Manager (%s)." % stamp)
    w("")
    w("    python 03_generate_config.py [STATE_DIR] [--dump config.json]")
    w('"""')
    w("import json")
    w("import os")
    w("import sys")
    w("")
    w("args = [a for a in sys.argv[1:] if not a.startswith('--')]")
    w('STATE_DIR = args[0] if args else "./quam_state"')
    w('os.environ["QUAM_STATE_PATH"] = os.path.abspath(STATE_DIR)')
    w("")
    w("from quam import QuamRoot  # noqa: F401  (ensures quam is importable)")
    w("from quam_builder.architecture.superconducting.qpu import (")
    w("    FluxTunableQuam, FixedFrequencyQuam,")
    w(")")
    w("")
    if quam_class and "." in quam_class:
        # docs/136 — this chip is rooted at a class the stock list does not
        # contain (a QDAC chip's root widens `qubits` and declares `qdac`).
        # Trying the stock roots FIRST would not merely be slower: each failed
        # load raises deep inside quam's type validation, and the loop's
        # `except Exception: continue` would swallow the real reason this
        # chip's own root failed, if it did.
        _mod, _, _cls = quam_class.rpartition(".")
        w("# This chip's own root class (it holds components the stock roots")
        w("# cannot type). Tried first; the stock roots remain as a fallback.")
        w("try:")
        w("    from %s import %s as _ChipRoot" % (_mod, _cls))
        w("except Exception as _exc:")
        w("    print(f'NOTE: %s is not importable here ({_exc}) — "
          "falling back to the stock roots.', file=sys.stderr)" % quam_class)
        w("    _ChipRoot = None")
    w("# Load with whichever architecture the state carries (the ZZ-drive root")
    w("# is guarded — older quam_builder predates it).")
    w("try:")
    w("    from quam_builder.architecture.superconducting.qpu import (")
    w("        FixedFrequencyZZDriveQuam,)")
    w("except ImportError:")
    w("    FixedFrequencyZZDriveQuam = None")
    w("machine = None")
    w("for _cls in (%sFluxTunableQuam, FixedFrequencyZZDriveQuam, FixedFrequencyQuam):"
      % ("_ChipRoot, " if (quam_class and "." in quam_class) else ""))
    w("    if _cls is None:")
    w("        continue")
    w("    try:")
    w("        machine = _cls.load()")
    w("        break")
    w("    except Exception:  # noqa: BLE001 — try the next architecture")
    w("        continue")
    w("if machine is None:")
    w("    sys.exit('could not load the state with any known QUAM root class')")
    w("")
    w("cfg = machine.generate_config()")
    w("print(f\"generate_config() OK: {len(cfg['elements'])} elements, \"")
    w("      f\"{len(cfg.get('controllers', {}))} controllers\")")
    w("")
    w("if '--dump' in sys.argv:")
    w("    i = sys.argv.index('--dump')")
    w("    dst = sys.argv[i + 1] if len(sys.argv) > i + 1 else 'config.json'")
    w("    with open(dst, 'w', encoding='utf-8') as fh:")
    w("        json.dump(cfg, fh, indent=2, default=str)")
    w("    print(f'config dumped to {dst}')")
    w("")
    return "\n".join(out)


# --- README ------------------------------------------------------------------

def _emit_readme(spec: dict, versions: dict, chip: str, stamp: str) -> str:
    qubits = spec.get("qubits") or []
    pairs = spec.get("qubit_pairs") or []
    gate = spec.get("pair_gate") or "(none)"
    ctrls = (spec.get("instruments") or {}).get("controllers") or []
    fems = sum(len(c.get("fems") or []) for c in ctrls)
    v = versions or {}
    out = [
        f"# {chip} — editable build scripts",
        "",
        f"Generated by QUAM State Manager on {stamp}, alongside the chip's",
        "`state.json` + `wiring.json`. These scripts REPRODUCE that chip from",
        "code — edit the data blocks in any IDE and re-run to rebuild.",
        "",
        "## Chip",
        "",
        f"- {len(qubits)} qubits: {', '.join(str(q) for q in qubits[:12])}"
        + (", …" if len(qubits) > 12 else ""),
        f"- {len(pairs)} qubit pairs · 2Q gate family: `{gate}`",
        f"- {len(ctrls)} OPX1000 chassis, {fems} FEMs",
    ]
    # docs/136 — the QDAC is a component, and the one part of this bundle that
    # needs a package outside the QM stack. Saying so in the README is the
    # difference between "it didn't work" and "install quam_config".
    qdac_qubits = (spec.get("qdac") or {}).get("qubits") or {}
    if qdac_qubits:
        tees = sorted((q for q, f in qdac_qubits.items() if (f or {}).get("bias_tee")),
                      key=lambda q: (len(str(q)), str(q)))
        out += [
            f"- QDAC-II DC bias on {len(qdac_qubits)} qubit(s)"
            + (f", {len(tees)} through a bias tee ({', '.join(tees)})" if tees else ""),
            "  - needs `quam_config.qdac_components` importable (customer-local,"
            " not part of the QM stack); without it those qubits build with **no"
            " bias component** and `02` says which",
            "  - `QDAC_PINS` in `02_build_machine.py` is the OPX digital output"
            " that arms each channel — one output drives one QDAC ext input and"
            " arms every channel on it, so a shared pin is normal cabling",
        ]
        if tees:
            out.append(
                "  - a bias-tee qubit needs a transmon class that keeps `z` a"
                " FluxLine and carries a `QdacBiasLine` beside it; `02` looks"
                " for one by shape and degrades with a named warning if this"
                " env has none")
    if spec.get("quam_class"):
        out.append(f"- root class: `{spec['quam_class']}`")

    # docs/137 — the bias-tee bundle carries two extra files in the LAB's own
    # idiom, and one change SM cannot make for them.
    if qdac_lf_recipe.wanted(spec):
        out += [
            "",
            "## Bias-tee qubits — two extra files, and one thing you must add",
            "",
            f"This chip has {len(qdac_lf_recipe.qdac.spec_bias_tee_qubits(spec))}"
            " qubit(s) carrying **both** a QDAC-II DC bias and an LF-FEM flux"
            " line. Your `build_quam_qdac.py` decides between those two per"
            " qubit id — one `if`, two branches — so it has nowhere to put a"
            " qubit that is both. Worse, its bias assignment is unconditional"
            " on `z`, so on a qubit that did get a flux line the `FluxLine` is"
            " overwritten by the `QdacBiasLine` **silently**: no exception, a"
            " chip that builds, and a flux line that stopped existing.",
            "",
            f"- `{qdac_lf_recipe.BUILDER_FILENAME}` — the combined builder."
            " Copy it into your `quam_config/` package beside"
            " `build_quam_qdac.py`. It is a transcription of your own"
            " `_add_transmons_with_qdac` with four named divergences; read them"
            " side by side.",
            f"- `{qdac_lf_recipe.GENERATOR_FILENAME}` — the top-level script."
            " Run it from `quam_config/`; it takes the output folder as its"
            " one argument.",
            "",
            "**What you must add yourself** (SM will not write into your tree):",
            "",
            "```python",
            qdac_lf_recipe.SNIPPET.rstrip(),
            "```",
            "",
            "Both emitted files refuse to run without it. That is deliberate:"
            " a chip whose DC bias silently did not attach looks exactly like a"
            " working one, and by the time `Quam.load()` fails in the next"
            " process the good `state.json` has already been overwritten.",
            "",
            "Every flux and coupler line in the generator is **explicitly"
            " pinned**. Do not replace them with unpinned `add_*_lines` calls:"
            " those cable by allocation order, so adding one line moves every"
            " later coupler to a different port — with no error, and nothing"
            " visibly wrong until a CZ misbehaves.",
        ]
    out += [
        "",
        "## Run order",
        "",
        "```bash",
        "python 01_make_wiring.py     ./quam_state   # instruments + wiring + base machine",
        "python 02_build_machine.py   ./quam_state   # populate values + 2Q gates + save",
        "python 03_generate_config.py ./quam_state   # sanity: machine.generate_config()",
        "```",
        "",
        "Use one (EMPTY) state folder for all three — quam recursively loads",
        "every `.json` under `QUAM_STATE_PATH`, so stray files corrupt a build.",
        "",
        "## Environment",
        "",
        "Needs the QM stack the wizard used (any env with these installed):",
        "",
    ]
    for k in ("python", "quam", "quam_builder", "qualang_tools", "qm"):
        if v.get(k):
            out.append(f"- `{k}` {v[k]}")
    out += [
        "",
        "## What is (and isn't) here",
        "",
        "- `01` pins the wizard's line order so `allocate_wiring` reproduces its",
        "  exact ports (each allocated port is noted in a comment — add it to",
        "  `constraints=` to survive re-ordering).",
        "- `02` carries the populate values and the 2Q-gate seeding copied",
        "  verbatim from the State Manager's build subprocess, including the",
        "  readout-LO pointer fix-up — the rebuilt `state.json` matches the",
        "  wizard's output.",
        "- Later calibration edits made in the State Manager (or by QUAlibrate)",
        "  live in the chip's `state.json`, NOT here — re-running these scripts",
        "  rebuilds the DESIGN, not the measured calibration.",
        "",
    ]
    return "\n".join(out)


# --- public API ---------------------------------------------------------------

def emit_bundle(spec: dict, allocation: dict | None, versions: dict | None,
                chip_name: str | None = None, stamp: str | None = None) -> dict:
    """Render the 4-file bundle → ``{filename: source}``.

    ``allocation`` is the build result's ``read_allocation`` dict (reference
    comments only — fidelity comes from insertion-order mirroring);
    ``versions`` feeds the README's env pins. Pure — writes nothing.
    """
    chip = chip_name or "chip"
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    bundle = {
        "01_make_wiring.py": _emit_wiring(spec, allocation or {}, chip, stamp),
        "02_build_machine.py": _emit_build(spec, chip, stamp, allocation or {}),
        "03_generate_config.py": _emit_config_check(chip, stamp,
                                                   spec.get("quam_class")),
        "README.md": _emit_readme(spec, versions or {}, chip, stamp),
    }
    # docs/137 — a bias-tee chip additionally gets the two files that build it
    # through the LAB'S OWN builder. They are a different idiom from 01-03 on
    # purpose: 01-03 reproduce the chip from the stock quam_builder stack,
    # while these go through quam_config, which is the path the lab's
    # calibration nodes actually load. A chip with no bias-tee qubit gets a
    # bundle byte-identical to before.
    bundle.update(qdac_lf_recipe.emit_files(spec, allocation or {}, chip, stamp))
    return bundle


def write_bundle(scripts_dir, bundle: dict) -> list:
    """Write the bundle files; returns the filenames written."""
    d = Path(scripts_dir)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for name, src in bundle.items():
        (d / name).write_text(src, encoding="utf-8", newline="\n")
        written.append(name)
    return written
