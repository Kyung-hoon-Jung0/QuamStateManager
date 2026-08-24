"""State-manager-side engine for the Generate Config feature.

This module is the bridge between the web wizard and the standalone
``generator/run_build.py`` script. It runs inside the Flask process, so it
depends only on the standard library — never on the Quantum Machines stack.

Responsibilities (built across phases B1-B3):

- **B1** ``validate_spec`` — a fast, friendly sanity check on the spec the UI
  assembled, run before a subprocess is ever spawned.
- **B2** conda-env discovery — find environments that can run the generator.
- **B3** subprocess runner — invoke a chosen env's Python with
  ``run_build.py`` and parse its ``_result.json``.

The *spec* is a plain JSON-able dict (``network`` / ``instruments`` /
``qubits`` / ``qubit_pairs`` / ``twpas`` / ``lines`` / ``populate``); see
``docs/27_config_generator.md`` for the contract. It stays a dict end to end
— assembled by the browser, validated here, forwarded verbatim to the
generator subprocess.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from quam_state_manager.core import safe_io

logger = logging.getLogger(__name__)

# --- spec vocabulary -------------------------------------------------------

KNOWN_FEM_TYPES = {"mw", "lf"}
KNOWN_CHANNEL_KINDS = {"mw_fem", "lf_fem", "opx", "octave"}

QUBIT_LINE_TYPES = {"resonator", "drive", "flux"}
PAIR_LINE_TYPES = {"coupler", "cross_resonance", "zz_drive"}
# CZ flux-pulse variants run_build can seed (populate['pairs'][<id>].cz_variant).
# KEEP IN SYNC with ``run_build._CZ_VARIANTS`` (the seeder, which runs stdlib-only
# in a foreign QM env so it can't import this symbol). If they drift, the validator
# could pass a variant the seeder silently coerces to 'unipolar'.
# ``TestCzVariantAllowlistInSync`` (tests/test_pair_gates_seed.py) pins them equal.
CZ_VARIANTS = {"unipolar", "flattop", "bipolar", "SNZ", "flattop_erf"}
TWPA_LINE_TYPES = {"twpa_pump", "twpa_isolation"}
KNOWN_LINE_TYPES = QUBIT_LINE_TYPES | PAIR_LINE_TYPES | TWPA_LINE_TYPES

# OPX1000 chassis slot numbers.
OPX1000_SLOTS = frozenset(range(1, 9))

# Integer-valued channel fields, validated only for type when present.
_CHANNEL_INT_FIELDS = (
    "con", "slot", "in_slot", "out_slot",
    "in_port", "out_port", "port", "index", "rf_in", "rf_out",
)


# --- validation ------------------------------------------------------------

def _is_int(v) -> bool:
    """A real int, excluding bool (``isinstance(True, int)`` is True in Python)."""
    return isinstance(v, int) and not isinstance(v, bool)


def _validate_channel(channel, ctx: str) -> list[str]:
    """Validate a single ``channel`` (port-pin) object. Returns error strings."""
    if not isinstance(channel, dict):
        return [f"{ctx}: must be an object"]

    errors: list[str] = []
    kind = channel.get("kind")
    if kind not in KNOWN_CHANNEL_KINDS:
        errors.append(
            f"{ctx}.kind: must be one of {sorted(KNOWN_CHANNEL_KINDS)}, got {kind!r}"
        )
    for field in _CHANNEL_INT_FIELDS:
        value = channel.get(field)
        if value is not None and not _is_int(value):
            errors.append(f"{ctx}.{field}: must be an integer")
    return errors


def validate_spec(spec) -> list[str]:
    """Validate a Generate-Config spec. Returns a list of human-readable errors.

    An empty list means the spec is structurally sound enough to hand to the
    generator subprocess. This is a *friendly* early check — it does not
    attempt the full channel-allocation feasibility analysis that
    ``allocate_wiring`` performs in the subprocess.
    """
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]

    errors: list[str] = []

    # -- network -----------------------------------------------------------
    network = spec.get("network")
    if not isinstance(network, dict):
        errors.append("network: missing or not an object")
    else:
        if not network.get("host"):
            errors.append("network.host: required")
        if not network.get("cluster_name"):
            errors.append("network.cluster_name: required")
        port = network.get("port")
        if port is not None and not _is_int(port):
            errors.append("network.port: must be an integer or null")

    # -- instruments -------------------------------------------------------
    instruments = spec.get("instruments")
    if not isinstance(instruments, dict):
        errors.append("instruments: missing or not an object")
        instruments = {}

    controllers = instruments.get("controllers", []) or []
    opx_plus = instruments.get("opx_plus", []) or []
    octaves = instruments.get("octaves", []) or []
    if not isinstance(controllers, list):
        errors.append("instruments.controllers: must be a list")
        controllers = []

    occupied_slots: set = set()
    for i, ctrl in enumerate(controllers):
        if not isinstance(ctrl, dict):
            errors.append(f"instruments.controllers[{i}]: must be an object")
            continue
        con = ctrl.get("con")
        if not _is_int(con):
            errors.append(f"instruments.controllers[{i}].con: must be an integer")
        for j, fem in enumerate(ctrl.get("fems", []) or []):
            if not isinstance(fem, dict):
                errors.append(f"instruments.controllers[{i}].fems[{j}]: must be an object")
                continue
            slot = fem.get("slot")
            if slot not in OPX1000_SLOTS:
                errors.append(
                    f"controller {con} fem slot {slot!r}: slot must be an integer 1-8"
                )
            if fem.get("fem") not in KNOWN_FEM_TYPES:
                errors.append(
                    f"controller {con} slot {slot!r}: fem must be 'mw' or 'lf'"
                )
            key = (con, slot)
            if key in occupied_slots:
                errors.append(f"controller {con} slot {slot}: two FEMs in the same slot")
            occupied_slots.add(key)

    for i, opx in enumerate(opx_plus):
        con = opx.get("con") if isinstance(opx, dict) else opx
        if not _is_int(con):
            errors.append(f"instruments.opx_plus[{i}]: needs an integer 'con'")

    for i, octave in enumerate(octaves):
        index = octave.get("index") if isinstance(octave, dict) else octave
        if not _is_int(index):
            errors.append(f"instruments.octaves[{i}]: needs an integer 'index'")

    if not (controllers or opx_plus or octaves):
        errors.append("instruments: define at least one controller, OPX+, or Octave")

    # -- qubits ------------------------------------------------------------
    qubits = spec.get("qubits", [])
    if not isinstance(qubits, list) or not qubits:
        errors.append("qubits: define at least one qubit")
        qubits = qubits if isinstance(qubits, list) else []
    qubit_ids = [str(q) for q in qubits]
    if len(set(qubit_ids)) != len(qubit_ids):
        errors.append("qubits: ids must be unique")
    # Name shape (mirrors the wizard's validateQubitName): a leading lowercase
    # "q" (quam_builder derives machine.qubits keys as "q" + stripped index —
    # other prefixes silently orphan populate values), then letters/digits/
    # underscore only. A "-" would corrupt pair-id parsing (run_build's
    # _parse_pair splits on the first "-"); whitespace breaks element naming.
    for q in qubit_ids:
        if not re.match(r"^q[A-Za-z0-9_]+$", q):
            errors.append(
                f"qubits: id {q!r} must start with 'q' followed by "
                "letters/digits/underscore only (no '-' or whitespace)")
    qubit_set = set(qubit_ids)

    # -- qubit_pairs -------------------------------------------------------
    pairs = spec.get("qubit_pairs", []) or []
    for i, pair in enumerate(pairs):
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            errors.append(f"qubit_pairs[{i}]: must be a [control, target] pair")
            continue
        control, target = str(pair[0]), str(pair[1])
        if control not in qubit_set:
            errors.append(f"qubit_pairs[{i}]: control '{control}' is not a declared qubit")
        if target not in qubit_set:
            errors.append(f"qubit_pairs[{i}]: target '{target}' is not a declared qubit")
        if control == target:
            errors.append(f"qubit_pairs[{i}]: control and target must differ "
                          f"(a qubit can't be paired with itself)")

    # -- twpas -------------------------------------------------------------
    twpa_set: set = set()
    for i, twpa in enumerate(spec.get("twpas", []) or []):
        # a TWPA is either a bare id string or a dict carrying {'id': ...}
        if isinstance(twpa, str) and twpa:
            twpa_set.add(twpa)
        elif isinstance(twpa, dict) and twpa.get("id"):
            twpa_set.add(str(twpa["id"]))
        else:
            errors.append(f"twpas[{i}]: needs an 'id'")

    # -- qdac ----------------------------------------------------------------
    # QDAC-II is an external DC voltage source used to flux-bias specific
    # qubits instead of an OPX LF-FEM port. There is no native qualang_tools/
    # quam_builder support for it (see docs/... — capability ids instr.qdac /
    # wire.qdac_trigger_line degrade gracefully when the env lacks it); this
    # is purely a structural/type check, same spirit as the twpas block above.
    qdac_qubit_ids: set = set()
    qdac_tee_ids: set = set()
    qdac = spec.get("qdac")
    if not isinstance(qdac, dict) and qdac:
        errors.append("qdac: must be an object")
        qdac = {}
    # NOTHING about the instrument is required until a qubit actually USES it.
    #
    # docs/119 states the rule for the capability gate — "requested only when
    # `spec.qdac.qubits` is non-empty" — but this validator never got it, and
    # the wizard always emits a `qdac` key because the section exists in the UI.
    # So an untouched QDAC section failed EVERY build with "qdac.ip_address:
    # required when communication_type is 'Ethernet'" (the default comm type),
    # blocking Auto-allocate, Generate AND Re-generate on a chip with no QDAC
    # at all. Confirmed in a real browser: the wizard could not build anything.
    #
    # The address checks below stay exactly as they were for a spec that DOES
    # bias a qubit from the QDAC — that is when an unreachable instrument is a
    # real error rather than an unused form field.
    qdac_qubits_declared = (qdac or {}).get("qubits") or {}
    if qdac and qdac_qubits_declared:
        comm = qdac.get("communication_type", "Ethernet")
        if comm not in ("Ethernet", "USB"):
            errors.append("qdac.communication_type: must be 'Ethernet' or 'USB'")
        if comm == "Ethernet" and not qdac.get("ip_address"):
            errors.append("qdac.ip_address: required when communication_type is 'Ethernet'")
        if comm == "USB" and not _is_int(qdac.get("usb_device")):
            errors.append("qdac.usb_device: required integer when communication_type is 'USB'")
        port = qdac.get("port", 5025)
        if port is not None and not _is_int(port):
            errors.append("qdac.port: must be an integer")

        qdac_qubits = qdac.get("qubits") or {}
        if not isinstance(qdac_qubits, dict):
            errors.append("qdac.qubits: must be an object")
            qdac_qubits = {}
        seen_channels: dict = {}
        for qid, fields in qdac_qubits.items():
            if qid not in qubit_set:
                errors.append(f"qdac.qubits[{qid!r}]: is not a declared qubit")
                continue
            qdac_qubit_ids.add(qid)
            if not isinstance(fields, dict):
                errors.append(f"qdac.qubits[{qid!r}]: must be an object")
                continue
            if fields.get("bias_tee"):
                qdac_tee_ids.add(qid)
            ch = fields.get("channel")
            if not _is_int(ch) or ch <= 0:
                errors.append(f"qdac.qubits[{qid!r}].channel: required positive integer")
            elif ch in seen_channels:
                errors.append(
                    f"qdac.qubits[{qid!r}].channel: {ch} is already used by "
                    f"qubit {seen_channels[ch]!r} — each qubit needs its own QDAC channel"
                )
            else:
                seen_channels[ch] = qid
            trigger_port = fields.get("trigger_port")
            if trigger_port is not None and trigger_port not in ("ext1", "ext2", "ext3", "ext4"):
                errors.append(
                    f"qdac.qubits[{qid!r}].trigger_port: must be one of "
                    "'ext1'..'ext4' or null"
                )
            output_range = fields.get("output_range")
            if output_range is not None and output_range not in ("low", "high"):
                errors.append(f"qdac.qubits[{qid!r}].output_range: must be 'low' or 'high'")
            output_filter = fields.get("output_filter")
            if output_filter is not None and output_filter not in ("dc", "med", "high"):
                errors.append(
                    f"qdac.qubits[{qid!r}].output_filter: must be 'dc', 'med', or 'high'"
                )
            for numfield in ("dwell", "slew_rate", "settle_time", "dc_offset"):
                v = fields.get(numfield)
                if v is not None and not isinstance(v, (int, float)):
                    errors.append(f"qdac.qubits[{qid!r}].{numfield}: must be a number")

    # -- lines -------------------------------------------------------------
    lines = spec.get("lines", []) or []
    if not isinstance(lines, list):
        errors.append("lines: must be a list")
        lines = []
    for i, line in enumerate(lines):
        if not isinstance(line, dict):
            errors.append(f"lines[{i}]: must be an object")
            continue
        line_type = line.get("line")
        element = line.get("element")

        if line_type not in KNOWN_LINE_TYPES:
            errors.append(f"lines[{i}]: unknown line type {line_type!r}")
        if not element:
            errors.append(f"lines[{i}]: missing 'element'")
        elif line_type in QUBIT_LINE_TYPES and str(element) not in qubit_set:
            errors.append(
                f"lines[{i}]: {line_type} element '{element}' is not a declared qubit"
            )
        elif (line_type == "flux" and str(element) in qdac_qubit_ids
                and str(element) not in qdac_tee_ids):
            # docs/136: a QDAC qubit CAN also carry an OPX flux line — that is
            # the bias-tee shape, where the QDAC holds the DC operating point
            # and the LF-FEM plays pulses on top of it. But it has to SAY so.
            # Co-presence alone is equally the signature of the mistake this
            # check was written for (a qubit switched to QDAC while its flux
            # line lingered), and the two cases build differently, so the flag
            # is what separates them rather than the reader's guess.
            errors.append(
                f"lines[{i}]: qubit '{element}' is QDAC-biased (declared in "
                "spec.qdac.qubits) and must not also have an OPX flux line — "
                "its z bias comes from the QDAC, not an LF-FEM port. If this "
                "qubit really is wired through a bias tee (QDAC DC + LF-FEM "
                "pulses), set qdac.qubits['" + str(element) + "'].bias_tee = true."
            )
        elif line_type in PAIR_LINE_TYPES:
            parts = str(element).split("-", 1)
            # r16 0-1: pair-id notation is user-free — real chips carry the
            # SHORT second-member form ("q1-2", "qA1-A2": the target drops
            # the leading "q"; run_build._parse_pair and the populate seed
            # lookup both accept it). Re-prefix a member that only resolves
            # with "q" prepended instead of rejecting the whole spec.
            members = [m if m in qubit_set or not m
                       else (f"q{m}" if f"q{m}" in qubit_set else m)
                       for m in parts]
            if (len(parts) != 2 or members[0] not in qubit_set
                    or members[1] not in qubit_set):
                errors.append(
                    f"lines[{i}]: {line_type} element '{element}' must be "
                    "'<control>-<target>' of two declared qubits"
                )
        elif line_type in TWPA_LINE_TYPES and str(element) not in twpa_set:
            errors.append(
                f"lines[{i}]: {line_type} element '{element}' is not a declared TWPA"
            )

        channel = line.get("channel")
        if channel is not None:
            errors.extend(_validate_channel(channel, f"lines[{i}].channel"))

    # -- bias tee, the other way round --------------------------------------
    # `bias_tee` is a claim about TWO components, so it is checked from both
    # ends. Without this, declaring the flag and then removing the flux line
    # (or arriving from a hand-edited spec that never had one) builds a plain
    # QDAC qubit while the wizard, the review step and the emitted recipe all
    # say bias tee — the disagreement would surface only as a missing pulse
    # line on the finished chip.
    flux_elements = {
        str(ln.get("element")) for ln in lines
        if isinstance(ln, dict) and ln.get("line") == "flux"
    }
    for qid in sorted(qdac_tee_ids - flux_elements, key=lambda q: (len(q), q)):
        errors.append(
            f"qdac.qubits[{qid!r}].bias_tee: declared, but this qubit has no "
            "OPX flux line — a bias tee is a QDAC DC bias AND an LF-FEM pulse "
            "line on the same qubit. Add a 'flux' line for it, or clear the flag."
        )

    # -- feedline multiplex bound -------------------------------------------
    # One MW-FEM readout in/out pair multiplexes at most 8 resonators. The
    # wizard clamps its mux input, but a stale draft / hand-crafted spec can
    # still carry an over-full group — block it before the build.
    feedline_counts: dict = {}
    for ln in lines:
        if isinstance(ln, dict) and ln.get("line") == "resonator" and ln.get("group"):
            g = str(ln["group"])
            feedline_counts[g] = feedline_counts.get(g, 0) + 1
    for g, n in sorted(feedline_counts.items()):
        if n > 8:
            errors.append(
                f"lines: readout feedline '{g}' multiplexes {n} qubits — the "
                "MW-FEM bound is 8 per feedline; lower 'qubits per readout "
                "feedline' or split the group")

    # -- unrepresentable architecture --------------------------------------
    # A tunable coupler (coupler lines) with fixed-frequency qubits (no qubit
    # flux lines) cannot be built: quam_builder's CZGate plays on the qubit z
    # line, so a coupler-only CZ has no representation. Fixed-frequency chips use
    # cross-resonance. The Generate wizard's chip-architecture selector doesn't
    # offer this combo; this guards hand-crafted specs / the API.
    line_types_present = {
        ln.get("line") for ln in lines if isinstance(ln, dict)
    }
    if "coupler" in line_types_present and "flux" not in line_types_present:
        errors.append(
            "coupler lines need qubit flux lines (flux-tunable qubits): a tunable-"
            "coupler CZ plays on the qubit z line. For fixed-frequency qubits use "
            "cross-resonance (cross_resonance lines) instead of a coupler."
        )

    # -- pair_gate ---------------------------------------------------------
    # run_build dispatches the 2Q-gate family on this; an unknown value silently
    # builds a chip with no 2Q macros, so validate it here.
    pair_gate = spec.get("pair_gate")
    if pair_gate not in (None, "", "cr", "cz_fixed", "cz_tunable"):
        errors.append(
            f"pair_gate: unknown value {pair_gate!r} "
            f"(expected 'cr', 'cz_fixed', or 'cz_tunable')"
        )

    # -- cr_port_mode --------------------------------------------------------
    # "shared_xy" = the customer's dual-upconverter layout: CR/ZZ drives ride
    # the CONTROL qubit's own xy MW port on upconverter 2 (two-phase
    # allocation in run_build.allocate_full). Only meaningful for CR chips.
    # Documented pass-through populate keys (unvalidated, like the other CR
    # fields): populate.pairs[*].cr_shapes (""|"basic"|"full"),
    # zz_detuning / zz_drive_amplitude / zz_flattop_length /
    # zz_flattop_flat_length; populate.qubit[*].cr_lo_frequency;
    # populate.options.pin_cores (bool). See docs/54.
    cr_port_mode = spec.get("cr_port_mode")
    if cr_port_mode not in (None, "", "dedicated", "shared_xy"):
        errors.append(
            f"cr_port_mode: unknown value {cr_port_mode!r} "
            f"(expected 'dedicated' or 'shared_xy')"
        )
    elif cr_port_mode == "shared_xy" and pair_gate != "cr":
        errors.append(
            "cr_port_mode: 'shared_xy' requires pair_gate 'cr' — the shared "
            "dual-upconverter layout is a cross-resonance architecture"
        )

    # -- populate ----------------------------------------------------------
    populate = spec.get("populate")
    if "populate" in spec and not isinstance(populate, dict):
        errors.append("populate: must be an object")
    elif isinstance(populate, dict):
        pop_options = populate.get("options")
        if pop_options is not None and not isinstance(pop_options, dict):
            errors.append("populate.options: must be an object")
        pop_pairs = populate.get("pairs")
        if pop_pairs is not None and not isinstance(pop_pairs, dict):
            errors.append("populate.pairs: must be an object")
        elif isinstance(pop_pairs, dict):
            for pid, pvals in pop_pairs.items():
                if not isinstance(pvals, dict):
                    continue
                variant = pvals.get("cz_variant")
                # "" is the wizard's "use default" sentinel — which now means
                # "all" (every variant seeded, missing shapes skipped); "all"
                # is also accepted explicitly. Only an unknown value errors.
                if variant not in (None, "", "all") and variant not in CZ_VARIANTS:
                    errors.append(
                        f"populate.pairs['{pid}'].cz_variant: unknown value "
                        f"{variant!r} (expected one of {sorted(CZ_VARIANTS)})"
                    )
        # r16 ⓪-3: user-settable MW-FEM band (empty = auto from the LO).
        for grp in ("qubit", "resonator"):
            rows = populate.get(grp)
            if not isinstance(rows, dict):
                continue
            for rid, rvals in rows.items():
                if not isinstance(rvals, dict) or "band" not in rvals:
                    continue
                if rvals["band"] not in (1, 2, 3):
                    errors.append(
                        f"populate.{grp}['{rid}'].band: must be 1, 2 or 3 "
                        f"(got {rvals['band']!r})")

    return errors


# --- conda environment discovery (B2) -------------------------------------

# Probe source run by a candidate interpreter. Uses importlib.metadata only —
# it reads package *metadata* and never imports the heavy QM packages (an
# `import quam` triggers a slow `qm` session init), so probing stays fast.
_PROBE_SRC = """
import json, sys
try:
    from importlib.metadata import version, distribution
except Exception:
    version = None
    distribution = None


def _v(dist):
    if version is None:
        return None
    try:
        return version(dist)
    except Exception:
        return None


def _commit(dist):
    # Install provenance for git/URL installs (PEP 610 direct_url.json).
    # quam-builder is pinned by git SHA in the field while its version string
    # stays frozen (0.2.0 metadata on wildly different commits — versions lie),
    # so the SHA must join the capability-cache key or a reinstall of a
    # different commit silently serves the stale manifest.
    if distribution is None:
        return None
    try:
        raw = distribution(dist).read_text("direct_url.json")
        if not raw:
            return None
        info = json.loads(raw)
        vcs = info.get("vcs_info") or {}
        return vcs.get("commit_id") or info.get("url")
    except Exception:
        return None


print(json.dumps({
    "python": sys.version.split()[0],
    "qualang_tools": _v("qualang-tools"),
    "quam_builder": _v("quam-builder"),
    "quam": _v("quam"),
    "qm": _v("qm-qua") or _v("qm"),
    "quam_builder_commit": _commit("quam-builder"),
}))
"""

# QM packages a usable generator env must have.
_REQUIRED_LIBS = ("qualang_tools", "quam_builder", "quam")

_SETTINGS_FILENAME = "config_generator.json"
_PROBE_CACHE_FILENAME = "config_generator_probe_cache.json"
_CAPABILITY_CACHE_FILENAME = "config_generator_capability_cache.json"
_PROBE_WORKERS = 4


def _run_command(args, timeout: int = 60):
    """Run a subprocess; return ``(returncode, stdout, stderr)``.

    Isolated in one function so tests can monkeypatch it without spawning
    real processes.

    On Windows the child is created with CREATE_NO_WINDOW: the packaged
    desktop app is a ``console=False`` build, so without it every probe
    flashes a console window in the user's face (docs/135 — the review that
    found it was looking at the new startup warm-up, which spawns one with no
    user action at all, but the flash was reachable from every generator
    subprocess).
    """
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, **kwargs
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return -1, "", f"executable not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", "command timed out"
    except Exception as exc:  # noqa: BLE001 - defensive: never raise to caller
        return -1, "", f"{type(exc).__name__}: {exc}"


def find_conda_executable() -> str | None:
    """Locate a ``conda`` executable, or ``None`` if conda is not installed."""
    exe = os.environ.get("CONDA_EXE")
    if exe and Path(exe).exists():
        return exe

    found = shutil.which("conda")
    if found:
        return found

    home = Path.home()
    bases = [
        home / "miniconda3", home / "anaconda3", home / "miniforge3",
        home / "opt" / "miniconda3",   # macOS graphical-installer default
    ]
    if os.name == "nt":
        bases += [
            Path("C:/ProgramData/miniconda3"), Path("C:/ProgramData/anaconda3"),
        ]
    else:
        # macOS system/homebrew locations. A Finder-launched .app inherits
        # launchd's minimal PATH — no CONDA_EXE, no conda on PATH — so this
        # hardcoded list is the only discovery channel there.
        bases += [
            Path("/opt/miniconda3"), Path("/opt/anaconda3"),
            Path("/opt/homebrew/Caskroom/miniconda/base"),
            Path("/opt/homebrew/Caskroom/miniforge/base"),
            Path("/usr/local/Caskroom/miniconda/base"),
        ]
    for base in bases:
        for rel in ("Scripts/conda.exe", "condabin/conda.bat", "bin/conda"):
            candidate = base / rel
            if candidate.exists():
                return str(candidate)
    return None


def _env_python(env_path) -> str:
    """Path to the Python interpreter inside a conda env directory."""
    env_path = Path(env_path)
    windows = env_path / "python.exe"
    posix = env_path / "bin" / "python"
    if windows.exists():
        return str(windows)
    if posix.exists():
        return str(posix)
    return str(windows if os.name == "nt" else posix)


def resolve_python_interpreter(path_str: str) -> str | None:
    """Resolve a user-given path to a Python interpreter FILE (r15, docs/71).

    Accepts: the interpreter file itself, a venv/conda FOLDER (both the
    Windows ``Scripts/python.exe`` and POSIX ``bin/python`` layouts, plus a
    conda root ``python.exe``; OS-native layout tried first), or a project
    folder holding a ``.venv`` (the uv convention). Pure stat — never spawns.
    ``None`` when nothing resolves.
    """
    try:
        p = Path(path_str)
        if p.is_file():
            return str(p)
        if not p.is_dir():
            return None
        layouts = [("Scripts", "python.exe"), ("python.exe",), ("bin", "python")]
        if os.name != "nt":
            layouts = [("bin", "python"), ("Scripts", "python.exe"), ("python.exe",)]
        for root in (p, p / ".venv"):
            for parts in layouts:
                cand = root.joinpath(*parts)
                if cand.is_file():
                    return str(cand)
    except OSError:
        return None
    return None


def discover_uv_venvs() -> list[dict]:
    """Discover uv/venv interpreters via the qualibrate projects (r15, docs/71).

    No interpreter key exists anywhere in qualibrate configs, but every
    project's ``[qualibrate.calibration_library] folder`` points into its
    calibration repo — and uv puts the venv at ``<repo>/.venv``. So: walk UP
    from each project's calibration folder (the folder itself + ≤4 ancestors)
    looking for ``.venv/pyvenv.cfg`` with a resolvable interpreter.
    Active-project-first, per-interpreter deduped, dangling folders skipped,
    never raises. READ-ONLY on the qualibrate tree (docs/55 doctrine).
    """
    out: list[dict] = []
    seen: set[str] = set()
    try:
        from quam_state_manager.core import qualibrate_config
        listing = qualibrate_config.list_projects()
    except Exception:  # noqa: BLE001 - discovery must never break the wizard
        return out
    projects = list(listing.get("projects") or [])
    projects.sort(key=lambda p: not p.get("active"))
    for proj in projects:
        calib = (proj.get("calibration_library") or {}).get("native")
        if not calib:
            continue
        try:
            node = Path(calib)
            for _ in range(5):                    # itself + ≤4 ancestors
                venv = node / ".venv"
                if (venv / "pyvenv.cfg").is_file():
                    python = resolve_python_interpreter(str(venv))
                    if python and python not in seen:
                        seen.add(python)
                        out.append({
                            "name": ".venv · " + str(proj.get("name") or node.name),
                            "path": str(venv),
                            "python": python,
                            "kind": "uv-venv",
                            "project": proj.get("name"),
                        })
                    break
                if node.parent == node:
                    break
                node = node.parent
        except OSError:
            continue
    return out


def _envs_from_environments_txt() -> list[Path]:
    """Env paths from ``~/.conda/environments.txt`` (one absolute path per line).

    conda maintains this registry on every OS and every install flavor, and it
    is dialect-free (no JSON, no subprocess) — so it still finds envs when the
    ``conda`` executable itself is unreachable (e.g. a Finder-launched .app
    running with launchd's minimal PATH). Missing/unreadable file → ``[]``.
    """
    txt = Path.home() / ".conda" / "environments.txt"
    try:
        lines = txt.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [Path(s) for s in (ln.strip() for ln in lines) if s]


_CONDA_ENVS_CACHE: dict = {"key": None, "paths": None}
_CONDA_ENVS_LOCK = threading.Lock()


def reset_env_discovery_cache() -> None:
    """Forget the memoized conda inventory (test isolation; a process-level
    memo must never leak one test's fake env list into the next)."""
    with _CONDA_ENVS_LOCK:
        _CONDA_ENVS_CACHE["key"] = None
        _CONDA_ENVS_CACHE["paths"] = None


def _conda_inventory_key(env_paths) -> tuple:
    """Cheap mtime fingerprint of the conda env inventory.

    Creating or removing an env writes ``~/.conda/environments.txt`` AND
    changes the mtime of the envs directory holding it, so stat'ing the
    registry plus every known env's PARENT directory catches both. An
    unreadable path contributes ``None`` (unknown, not "unchanged") —
    two different unreadable states still compare equal, which is the
    conservative direction: the cache only survives while the inventory
    provably looks the same.
    """
    paths = {Path.home() / ".conda" / "environments.txt"}
    for p in env_paths or ():
        parent = Path(p).parent
        if str(parent):
            paths.add(parent)
    stamps = []
    for p in sorted(paths, key=str):
        try:
            stamps.append((str(p), os.stat(p).st_mtime_ns))
        except OSError:
            stamps.append((str(p), None))
    return tuple(stamps)


def _conda_env_paths(refresh: bool = False) -> list[Path]:
    """``conda env list --json``, memoized on the inventory's mtimes (docs/135).

    The subprocess costs ~4 s on a real machine (measured, 8 envs) and it
    dominated the Generate/Re-generate wizard's step-5 wait — the whole
    auto-allocate chain sat behind it while it re-answered a question whose
    answer had not changed. Re-running it is only ever needed when an env is
    created or removed, which :func:`_conda_inventory_key` sees.

    A miss (or ``refresh``) runs conda exactly as before. A conda that is
    unreachable or errors is NOT cached — ``~/.conda/environments.txt``
    still carries the envs, and a transient failure must not stick.
    """
    with _CONDA_ENVS_LOCK:
        cached = _CONDA_ENVS_CACHE.get("paths")
        if cached is not None and not refresh:
            if _CONDA_ENVS_CACHE.get("key") == _conda_inventory_key(cached):
                return list(cached)

    conda = find_conda_executable()
    if not conda:
        return []
    returncode, stdout, _ = _run_command([conda, "env", "list", "--json"], timeout=30)
    if returncode != 0 or not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return []
    paths = [Path(raw) for raw in (data.get("envs") or [])]
    if not paths:
        # An empty answer is not evidence of an empty machine, and caching it
        # would be self-confirming: the key is derived FROM the result, so
        # "no envs" would key on nothing but the registry file and could never
        # invalidate itself. Re-ask next time instead.
        return []
    with _CONDA_ENVS_LOCK:
        _CONDA_ENVS_CACHE["paths"] = list(paths)
        _CONDA_ENVS_CACHE["key"] = _conda_inventory_key(paths)
    return paths


def discover_envs(refresh: bool = False) -> list[dict]:
    """List the Python environments on this machine.

    Merges ``conda env list --json`` (when conda is findable) with
    ``~/.conda/environments.txt`` (which needs no conda executable at all),
    plus uv/.venv interpreters discovered through the qualibrate projects
    (:func:`discover_uv_venvs`, r15) — deduped by path. Rows carry a
    ``kind`` tag (``"conda"`` / ``"uv-venv"``). Probing each env for the QM
    stack is done separately by :func:`probe_env`.
    """
    env_paths: list[Path] = list(_conda_env_paths(refresh=refresh))
    env_paths.extend(_envs_from_environments_txt())

    envs: list[dict] = []
    seen: set[str] = set()
    for path in env_paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        envs.append({
            "name": path.name,
            "path": str(path),
            "python": _env_python(path),
            "kind": "conda",
        })
    # uv/.venv interpreters from the qualibrate projects (r15, docs/71) —
    # appended after conda's so the familiar list stays on top.
    for uv in discover_uv_venvs():
        if uv["path"] not in seen:
            seen.add(uv["path"])
            envs.append(uv)
    return envs


def probe_env(python_path: str) -> dict:
    """Check whether an interpreter has the QM stack needed by the generator.

    Returns ``{"python", "versions", "usable", "missing", "error"}``.
    ``usable`` is True only when ``qualang_tools`` + ``quam_builder`` + ``quam``
    are all importable.
    """
    info = {"python": None, "versions": {}, "usable": False, "missing": [], "error": None}

    returncode, stdout, stderr = _run_command([python_path, "-c", _PROBE_SRC], timeout=60)
    if returncode != 0:
        info["error"] = (stderr or "probe failed").strip()[:300]
        return info

    lines = [ln for ln in stdout.strip().splitlines() if ln.strip()]
    if not lines:
        info["error"] = "probe produced no output"
        return info
    try:
        data = json.loads(lines[-1])
    except (ValueError, TypeError) as exc:
        info["error"] = f"could not parse probe output: {exc}"
        return info

    info["python"] = data.get("python")
    # quam_builder_commit rides along in versions so the version-keyed
    # capability cache (see _env_versions) is git-SHA-aware: same version
    # string + different pinned commit → cache miss, fresh deep probe.
    info["versions"] = {k: data.get(k) for k in (
        "qualang_tools", "quam_builder", "quam", "qm", "quam_builder_commit")}
    info["missing"] = [lib for lib in _REQUIRED_LIBS if not info["versions"].get(lib)]
    info["usable"] = not info["missing"]
    return info


def _probe_cache_path(instance_path) -> Path:
    return Path(instance_path) / _PROBE_CACHE_FILENAME


def _load_probe_cache(instance_path) -> dict[str, dict]:
    """Read the persisted probe-result cache; tolerate a missing/corrupt file."""
    p = _probe_cache_path(instance_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_probe_cache(instance_path, cache: dict[str, dict]) -> None:
    """Persist the probe-result cache atomically; failures are non-fatal."""
    try:
        safe_io.atomic_write_json(_probe_cache_path(instance_path), cache)
    except OSError:
        logger.warning("Could not persist probe cache", exc_info=True)


def _site_packages_for(python_path: str) -> Path | None:
    """Best-effort resolve an interpreter's site-packages dir WITHOUT running it.

    Covers the conda and venv layouts on both Linux and Windows:
      * ``<env>/bin/python``          → ``<env>/lib/pythonX.Y/site-packages`` (posix)
      * ``<env>/python.exe``          → ``<env>/Lib/site-packages``          (win conda)
      * ``<env>/Scripts/python.exe``  → ``<env>/Lib/site-packages``          (win venv)
    Returns ``None`` if none resolves (the caller then falls back to a coarser
    signature that just re-probes — never to serving a stale entry).
    """
    p = Path(python_path)
    env = p.parent
    if env.name.lower() in ("bin", "scripts"):
        env = env.parent
    candidates = [env / "Lib" / "site-packages"]          # windows conda + venv
    libdir = env / "lib"
    try:
        if libdir.is_dir():
            for child in sorted(libdir.glob("python*")):    # posix: pythonX.Y
                candidates.append(child / "site-packages")
    except OSError:
        pass
    for c in candidates:
        try:
            if c.is_dir():
                return c
        except OSError:
            continue
    return None


def _env_signature(python_path: str) -> str | None:
    """A cheap change-signature for an interpreter's *installed packages*.

    Composites the interpreter binary's mtime with the site-packages directory's
    mtime. The latter is the load-bearing part: ``pip install``/``uninstall``
    writes (or removes) the ``<dist>-<ver>.dist-info`` dir straight under
    site-packages, bumping that directory's mtime — while the interpreter binary
    is left untouched. Keying on the binary alone (the old behaviour) therefore
    served a STALE version/usable verdict after a pip install into an existing
    env. Returns ``None`` only when the interpreter itself is gone (→ re-probe).
    """
    try:
        pstat = Path(python_path).stat()
    except OSError:
        return None
    parts = [str(pstat.st_mtime_ns)]
    sp = _site_packages_for(python_path)
    if sp is not None:
        try:
            parts.append(str(sp.stat().st_mtime_ns))
        except OSError:
            pass
    return "|".join(parts)


def probe_envs(
    python_paths: list[str],
    *,
    instance_path=None,
    max_workers: int = _PROBE_WORKERS,
) -> dict[str, dict]:
    """Probe every interpreter in *python_paths* in parallel, with disk cache.

    Returns ``{python_path: probe_result_dict}`` — the same dict shape
    :func:`probe_env` returns, plus a ``"cached": True`` marker on entries
    served from the on-disk cache.

    Cache key: ``(python_path, _env_signature)`` where the signature folds in
    the site-packages mtime — so an env that gained or lost the QM stack via a
    ``pip install`` (which leaves the interpreter binary's mtime untouched) is
    re-probed automatically, not served a stale verdict.
    The cache is written under ``<instance>/config_generator_probe_cache.json``
    via :func:`safe_io.atomic_write_json`; if *instance_path* is None the
    cache is in-memory only (used by tests and by call sites without a
    Flask instance dir).

    Probing is fanned out across a small ``ThreadPoolExecutor`` so a user
    with 10+ conda envs doesn't pay 10× the per-probe cost serially
    (red-team Phase 2 finding §2.1).
    """
    if not python_paths:
        return {}

    cache = _load_probe_cache(instance_path) if instance_path is not None else {}
    results: dict[str, dict] = {}
    needs_probe: list[str] = []

    for path in python_paths:
        sig = _env_signature(path)
        entry = cache.get(path)
        if entry is not None and sig is not None and entry.get("sig") == sig:
            result = dict(entry.get("result", {}))
            result["cached"] = True
            results[path] = result
        else:
            needs_probe.append(path)

    if needs_probe:
        workers = min(max_workers, len(needs_probe)) or 1
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for path, result in zip(needs_probe, ex.map(probe_env, needs_probe)):
                results[path] = result
                sig = _env_signature(path)
                if sig is not None and result.get("error") is None:
                    cache[path] = {"sig": sig, "result": result}
        if instance_path is not None:
            _save_probe_cache(instance_path, cache)

    return results


def probe_selected_env(
    python_path: str,
    *,
    instance_path=None,
) -> dict:
    """Fast-path probe of the previously-selected env: cache lookup, then probe.

    Wraps :func:`probe_envs` for the single-env case so a route that just
    needs to confirm the user's already-chosen env is QM-capable doesn't
    pay the cost of probing every other env on the machine.
    """
    return probe_envs([python_path], instance_path=instance_path).get(python_path, probe_env(python_path))


# --- selected-env persistence ---------------------------------------------

def _settings_path(instance_path) -> Path:
    return Path(instance_path) / _SETTINGS_FILENAME


def get_selected_env(instance_path) -> str | None:
    """Return the Python path the user previously selected, or ``None``."""
    path = _settings_path(instance_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    selected = data.get("selected_env_python")
    return selected or None


def set_selected_env(instance_path, python_path: str) -> None:
    """Persist the user's chosen generator interpreter under ``instance/``.

    Read-modify-write: the settings file is shared with other keys (e.g. the
    Fit-Auditor's ``fit_audit_source_root``), so a whole-file overwrite here would
    silently drop them when the user re-picks an env.
    """
    path = _settings_path(instance_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except (ValueError, OSError):
        data = {}
    data["selected_env_python"] = python_path
    # Atomic (tmp + fsync + replace), never a plain write_text: this file is a
    # SHARED read-modify-write target (fit_audit_source_root lives here too) —
    # a torn/partial write would silently drop the other keys.
    safe_io.atomic_write_json(path, data)


def running_under_wsl() -> bool:
    """True when this process runs inside WSL — the one documented, supported
    bridge for driving a *Windows* interpreter (``….exe``) from a POSIX app:
    both WSL1 and WSL2 kernels report 'microsoft' in ``/proc/version``."""
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def _cleanup_work_dir(work_dir: Path) -> None:
    """Best-effort cleanup of a generator/preview tmp dir.

    Tries once, sleeps briefly on failure (AV / indexer holding a handle is
    transient on Windows), tries again, and on a second failure logs the
    leak instead of silently swallowing it. ``shutil.rmtree(ignore_errors=True)``
    would otherwise let orphan ``quamgen_work_*`` / ``quamcfg_work_*`` dirs
    accumulate under ``/tmp`` over months of wizard use (red-team Phase 2
    finding §2.2).
    """
    try:
        shutil.rmtree(work_dir)
        return
    except OSError:
        time.sleep(0.5)
    try:
        shutil.rmtree(work_dir)
    except OSError as exc:
        logger.warning("Could not remove generator work dir %s: %s", work_dir, exc)


# --- generator subprocess runner (B3) -------------------------------------

def _script_path(filename: str) -> Path:
    """Locate a ``generator/`` script in a dev checkout or a frozen bundle.

    PyInstaller ships ``quam_state_manager/generator/`` as data relative to
    ``sys._MEIPASS``; in a dev checkout it sits next to this package. The
    scripts must stay plain ``.py`` files on disk either way — they are run
    by an *external* interpreter, not imported.
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", "") or Path(sys.executable).parent)
        return base / "quam_state_manager" / "generator" / filename
    return Path(__file__).resolve().parent.parent / "generator" / filename


# The standalone generator scripts, run by the user-selected interpreter.
GENERATOR_SCRIPT = _script_path("run_build.py")
CONFIG_PREVIEW_SCRIPT = _script_path("run_generate_config.py")
CAPABILITY_SCRIPT = _script_path("probe_capabilities.py")


def _blank_outcome() -> dict:
    """The shared outcome-dict shape both subprocess runners fill in."""
    return {
        "ok": False, "status": "error", "result": None,
        "returncode": None, "stdout": "", "stderr": "", "error": None,
    }


def _run_script_outcome(
    argv: list,
    work_dir: Path,
    timeout: int,
    outcome: dict,
    *,
    no_result_label: str,
    error_fallback: str,
) -> dict:
    """Run a ``generator/`` script and fill *outcome* from its ``_result.json``.

    Shared tail of :func:`run_generator` and :func:`run_config_preview` —
    spawn via :func:`_run_command` (looked up as a module global so tests can
    monkeypatch it), then parse ``work_dir/_result.json`` into the outcome.
    """
    returncode, stdout, stderr = _run_command(argv, timeout=timeout)
    outcome["returncode"] = returncode
    outcome["stdout"] = stdout
    outcome["stderr"] = stderr

    result_file = work_dir / "_result.json"
    if not result_file.exists():
        outcome["error"] = (
            f"{no_result_label} produced no _result.json — the interpreter "
            f"may have failed to start. stderr: {(stderr or '').strip()[:300]}"
        )
        return outcome

    try:
        parsed = json.loads(result_file.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        outcome["error"] = f"could not read _result.json: {exc}"
        return outcome

    outcome["result"] = parsed
    outcome["status"] = parsed.get("status", "error")
    outcome["ok"] = outcome["status"] == "ok"
    if not outcome["ok"]:
        outcome["error"] = explain_build_error(
            parsed.get("error") or error_fallback)
    return outcome


#: Build failures a user can actually act on, and what to say instead of the
#: library's own exception text. docs/120 item 26: the wizard printed
#: ``NotEnoughChannelsException: <internal message>`` straight through, which
#: names the allocator's class rather than the user's problem — and the problem
#: (too few ports for the chip as configured) is one the wizard can be told how
#: to fix. Keyed on the exception CLASS NAME, which is stable across the message
#: wording; the original text is always appended so nothing is hidden.
_BUILD_ERROR_HELP: dict[str, str] = {
    "NotEnoughChannelsException": (
        "This environment's instrument list does not have enough channels for "
        "the chip as configured. Add or enlarge a controller/FEM in step 2, or "
        "reduce the number of qubits, pairs or lines in step 3–4, then "
        "re-allocate."
    ),
    "ConstraintsTooStrictException": (
        "The port constraints you pinned cannot all be satisfied at once. "
        "Relax or clear a pinned port in step 3 and re-allocate."
    ),
    "ModuleNotFoundError": (
        "The selected environment is missing a package this build needs. "
        "Pick a different environment in step 1, or install the package there "
        "and press Re-check environment."
    ),
    "ImportError": (
        "The selected environment could not import something this build needs "
        "— usually a version mismatch in the QM stack. Re-check the "
        "environment in step 1."
    ),
}


def explain_build_error(raw: str | None) -> str:
    """Prefix a known build failure with what the user can do about it.

    The raw text is always kept: a message that hides the original makes the
    failure unreportable, and these mappings are a convenience, not a claim to
    have understood every case.
    """
    if not raw:
        return raw or ""
    cls = str(raw).split(":", 1)[0].strip()
    help_text = _BUILD_ERROR_HELP.get(cls)
    if not help_text:
        return raw
    return f"{help_text}\n\n(reported as: {raw})"


def run_generator(
    python_path: str,
    mode: str,
    spec: dict,
    out_dir,
    timeout: int = 300,
) -> dict:
    """Run ``run_build.py`` in a subprocess and return a parsed result.

    The spec and ``_result.json`` live in a private temp work dir — never in
    ``out_dir``. QUAM's loader reads *every* ``.json`` in a folder, so a stray
    file in the output directory would corrupt the ``state.json`` the build
    just wrote. ``out_dir`` therefore receives only ``state.json`` +
    ``wiring.json`` (``build`` mode) and nothing in ``allocate`` mode.

    Returns::

        {
          "ok": bool,             # subprocess ran AND _result.json status==ok
          "status": "ok"|"error",
          "result": dict | None,  # parsed _result.json
          "returncode": int|None,
          "stdout": str, "stderr": str,
          "error": str | None,    # high-level failure message
        }

    Never raises — every failure mode is reported in the returned dict.
    """
    out_dir = Path(out_dir)
    outcome = _blank_outcome()

    if mode not in ("allocate", "build"):
        outcome["error"] = f"invalid mode: {mode!r} (expected 'allocate' or 'build')"
        return outcome
    if not GENERATOR_SCRIPT.exists():
        outcome["error"] = f"generator script not found: {GENERATOR_SCRIPT}"
        return outcome

    work_dir = Path(tempfile.mkdtemp(prefix="quamgen_work_"))
    try:
        try:
            # run_build.py writes _result.json next to the spec (the work dir).
            spec_path = work_dir / "_spec.json"
            spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            outcome["error"] = f"could not prepare directories: {exc}"
            return outcome

        return _run_script_outcome(
            [
                python_path, str(GENERATOR_SCRIPT),
                "--mode", mode,
                "--spec", str(spec_path),
                "--out", str(out_dir),
            ],
            work_dir, timeout, outcome,
            no_result_label="generator",
            error_fallback="generator reported an error",
        )
    finally:
        _cleanup_work_dir(work_dir)


def run_config_preview(
    python_path: str,
    state_folder,
    timeout: int = 120,
) -> dict:
    """Run ``run_generate_config.py`` in a subprocess and return the parsed result.

    Loads the existing ``state.json`` + ``wiring.json`` at *state_folder*
    into a QUAM machine via the chosen Python env and returns the dict that
    ``machine.generate_config()`` produces (the same config a calibration
    script would receive after ``node.machine.connect()``).

    Same return contract as :func:`run_generator`::

        {
          "ok": bool,
          "status": "ok"|"error",
          "result": dict | None,   # the _result.json envelope (config inside)
          "returncode": int|None,
          "stdout": str, "stderr": str,
          "error": str | None,
        }
    """
    outcome = _blank_outcome()

    if not CONFIG_PREVIEW_SCRIPT.exists():
        outcome["error"] = f"config previewer script not found: {CONFIG_PREVIEW_SCRIPT}"
        return outcome

    state_folder = Path(state_folder)
    if not (state_folder / "state.json").exists():
        outcome["error"] = f"state.json not found in {state_folder}"
        return outcome

    work_dir = Path(tempfile.mkdtemp(prefix="quamcfg_work_"))
    try:
        return _run_script_outcome(
            [
                python_path, str(CONFIG_PREVIEW_SCRIPT),
                "--state-folder", str(state_folder),
                "--out", str(work_dir),
            ],
            work_dir, timeout, outcome,
            no_result_label="config previewer",
            error_fallback="previewer reported an error",
        )
    finally:
        _cleanup_work_dir(work_dir)


# ---------------------------------------------------------------------------
# Capability probe — what can THIS env actually build? (deep introspection)
# ---------------------------------------------------------------------------

def _env_versions(python_path: str) -> dict:
    """The QM-package version tuple for an env (fast metadata probe, no QM import).

    This is the capability cache KEY: capability presence is a pure function of
    the installed package versions, so re-probe iff a version changed. (The
    interpreter mtime — the older probe cache's key — does NOT change on a
    ``pip install`` into the same env, so it would go stale silently.)
    """
    return probe_env(python_path).get("versions") or {}


def _capability_cache_path(instance_path) -> Path:
    return Path(instance_path) / _CAPABILITY_CACHE_FILENAME


def probe_capabilities(python_path: str, instance_path=None, *,
                       force: bool = False, timeout: int = 240) -> dict:
    """Deep-introspect an env for the capabilities the generator relies on.

    Runs ``generator/probe_capabilities.py`` in the selected interpreter (it
    imports the stack — slower than the metadata probe, so this is for the ONE
    selected env, not the fan-out list) and returns::

        {"ok": bool, "capabilities": {id: {available, detail}},
         "versions": {...}, "error": str|None, "cached": bool}

    Version-keyed disk cache under ``<instance>/config_generator_capability_cache
    .json``: a hit requires the env's current versions to match the cached ones.
    ``force=True`` bypasses the cache (for editable installs whose version string
    didn't change). Only successful probes are cached. Never raises.
    """
    versions = _env_versions(python_path)

    if instance_path is not None and not force:
        cache = _load_capability_cache(instance_path)
        entry = cache.get(python_path)
        man = entry.get("manifest") or {} if isinstance(entry, dict) else {}
        # docs/136: an entry cached before the QPU-root probe existed carries
        # no `qpu_roots`. Treating that as a hit would silently disable the
        # root-class gate for everyone with a warm cache — exactly the users
        # least likely to notice — so a missing key is a MISS and the cache
        # self-heals on the next probe.
        if (isinstance(entry, dict) and entry.get("versions") == versions
                and "qpu_roots" in man):
            return {"ok": True, "cached": True, "error": None,
                    "capabilities": man.get("capabilities") or {},
                    "versions": man.get("versions") or versions,
                    "qpu_roots": man.get("qpu_roots") or []}

    result = {"ok": False, "cached": False, "error": None,
              "capabilities": {}, "versions": versions, "qpu_roots": []}

    if not CAPABILITY_SCRIPT.exists():
        result["error"] = f"capability probe script not found: {CAPABILITY_SCRIPT}"
        return result

    outcome = _blank_outcome()
    work_dir = Path(tempfile.mkdtemp(prefix="quamcap_work_"))
    try:
        _run_script_outcome(
            [python_path, str(CAPABILITY_SCRIPT), "--out", str(work_dir / "_result.json")],
            work_dir, timeout, outcome,
            no_result_label="capability probe",
            error_fallback="capability probe reported an error",
        )
    finally:
        _cleanup_work_dir(work_dir)

    parsed = outcome.get("result") or {}
    if not outcome.get("ok"):
        result["error"] = outcome.get("error") or "capability probe failed"
        return result

    manifest = {"capabilities": parsed.get("capabilities") or {},
                "versions": parsed.get("versions") or versions,
                "qpu_roots": parsed.get("qpu_roots") or []}
    result.update(ok=True, capabilities=manifest["capabilities"],
                  versions=manifest["versions"],
                  qpu_roots=manifest["qpu_roots"])

    if instance_path is not None:                       # cache only successes
        cache = _load_capability_cache(instance_path)
        cache[python_path] = {"versions": versions, "manifest": manifest}
        _save_capability_cache(instance_path, cache)
    return result


def _load_capability_cache(instance_path) -> dict[str, dict]:
    p = _capability_cache_path(instance_path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_capability_cache(instance_path, cache: dict[str, dict]) -> None:
    try:
        safe_io.atomic_write_json(_capability_cache_path(instance_path), cache)
    except OSError:
        logger.warning("Could not persist capability cache", exc_info=True)
