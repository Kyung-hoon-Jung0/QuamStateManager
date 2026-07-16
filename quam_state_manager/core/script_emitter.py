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
_RUNTIME_CONSTS = ("_BAND_TO_DELAY_NS", "_CZ_VARIANTS")
_RUNTIME_FUNCS = (
    "_norm_index", "_parse_pair", "_quam_pair_id", "_norm_pair_qubits",
    "_num", "_target_lo",
    "_band_for", "_delay_for_band", "_apply_lf_delay",
    "_set_port_lo", "_set_channel_lo", "_operation",
    "_apply_resonator", "_apply_qubit", "_apply_flux", "_apply_pulses",
    "_make_cz_gate", "_apply_pairs", "apply_populate",
    "_cz_variant_pulses", "_seed_cz_variant",
    "_import_cr_gate", "_seed_cr_gate",
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


def _runtime_block() -> str:
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
    for name in _RUNTIME_CONSTS:
        parts.append(f"{name} = {getattr(mod, name)!r}")
    parts.append("")
    for name in _RUNTIME_FUNCS:
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
              "coupler": "c", "cross_resonance": "cr"}


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
    quam_cls = "FluxTunableQuam" if flux_tunable else "FixedFrequencyQuam"

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
    w("from quam_builder.architecture.superconducting.qpu import %s" % quam_cls)
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

    # 3) drive / flux / coupler / CR / ZZ — spec-lines order.
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
        elif lt in ("coupler", "cross_resonance", "zz_drive"):
            ctl, tgt = str(el).split("-", 1)
            fn = {"coupler": "add_qubit_pair_flux_lines",
                  "cross_resonance": "add_qubit_pair_cross_resonance_lines",
                  "zz_drive": "add_qubit_pair_zz_drive_lines"}[lt]
            w("connectivity.%s(qubit_pairs=[(%r, %r)], constraints=%s)%s"
              % (fn, _norm_index(ctl), _norm_index(tgt), c, note))
            emitted_any = True
    if emitted_any:
        w("")
    w("allocate_wiring(connectivity, instruments)")
    w("")
    w("# =============================== build ================================")
    w("machine = %s()" % quam_cls)
    w("# Older quam_builder takes an explicit path kwarg; newer reads")
    w("# QUAM_STATE_PATH — the same shim the wizard build uses.")
    w('_kwargs = {"port": PORT}')
    w('if "path" in inspect.signature(build_quam_wiring).parameters:')
    w('    _kwargs["path"] = os.environ["QUAM_STATE_PATH"]')
    w("build_quam_wiring(connectivity, HOST, CLUSTER, machine, **_kwargs)")
    w("machine = %s.load()" % quam_cls)
    w("build_quam(machine)")
    w('print(f"wiring built: {len(machine.qubits)} qubits, "')
    w('      f"{len(machine.qubit_pairs)} pairs -> {os.environ[\'QUAM_STATE_PATH\']}")')
    w("")
    return "\n".join(out)


# --- 02_build_machine.py -----------------------------------------------------

def _emit_build(spec: dict, chip: str, stamp: str) -> str:
    lines = spec.get("lines", []) or []
    flux_tunable = any(ln.get("line") in ("flux", "coupler") for ln in lines)
    quam_cls = "FluxTunableQuam" if flux_tunable else "FixedFrequencyQuam"
    pair_gate = (spec.get("pair_gate") or "").lower()
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
    w("from quam_builder.architecture.superconducting.qpu import %s" % quam_cls)
    w("")
    w("# ============================ EDIT: populate ==========================")
    w("# Base SI units (Hz, ns, V, dimensionless amp). Blank/missing keys keep")
    w("# quam_builder defaults (x180: amp 0.1 / len 40 ns; anharmonicity -200e6).")
    w("POPULATE = %s" % pformat(populate, indent=4, width=88, sort_dicts=True))
    w("")
    w("# [control, target] per pair — for CZ chips the wizard ordered these")
    w("# control = higher-f qubit; the flux pulse plays on the moving qubit.")
    w("QUBIT_PAIRS = %s" % pformat(qubit_pairs, indent=4, width=88))
    w("")
    w("# 2Q-gate family: 'cz_tunable' | 'cz_fixed' | 'cr' | '' (no wizard gate).")
    w("PAIR_GATE = %r" % pair_gate)
    w("")
    w(_runtime_block())
    w("# =============================== run ==================================")
    w("machine = %s.load()" % quam_cls)
    w('_spec = {"populate": POPULATE, "qubit_pairs": QUBIT_PAIRS}')
    w('apply_populate(machine, POPULATE, handle_pairs=(PAIR_GATE == ""))')
    w('if PAIR_GATE in ("cz_fixed", "cz_tunable", "cr"):')
    w("    for _w in _finalize_pair_gates(machine, _spec, PAIR_GATE):")
    w('        print(f"WARNING: {_w}", file=sys.stderr)')
    w("machine.save()")
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

def _emit_config_check(chip: str, stamp: str) -> str:
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
    w("# Load with whichever architecture the state carries.")
    w("try:")
    w("    machine = FluxTunableQuam.load()")
    w("except Exception:  # noqa: BLE001 — fixed-frequency chip")
    w("    machine = FixedFrequencyQuam.load()")
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
    return {
        "01_make_wiring.py": _emit_wiring(spec, allocation or {}, chip, stamp),
        "02_build_machine.py": _emit_build(spec, chip, stamp),
        "03_generate_config.py": _emit_config_check(chip, stamp),
        "README.md": _emit_readme(spec, versions or {}, chip, stamp),
    }


def write_bundle(scripts_dir, bundle: dict) -> list:
    """Write the bundle files; returns the filenames written."""
    d = Path(scripts_dir)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for name, src in bundle.items():
        (d / name).write_text(src, encoding="utf-8", newline="\n")
        written.append(name)
    return written
