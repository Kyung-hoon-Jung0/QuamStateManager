"""Environment capability model: does the selected env's stack build this spec?

The *detection* of what an env can do lives in ``generator/probe_capabilities.py``
(it runs inside the user's env and introspects the installed packages). This
module is the State-Manager-side counterpart that owns the *meaning* of each
capability and answers the real question: **given the chip the user is about to
build, what will succeed, what will silently degrade, and what will fail — and
why + how to fix it.**

Three pieces:

- :data:`REGISTRY` — per capability id: human label, category, the package +
  symbol it needs, **what QUAM value it produces**, how to **fix** a miss, and its
  ``severity`` *when required* (``blocker`` = build fails; ``degrade`` = build
  succeeds but the feature is dropped / falls back; ``info`` = not needed to
  build). The id set is pinned equal to ``probe_capabilities.CATALOG_IDS`` by a
  test, so detection and meaning can never drift.
- :func:`required_capabilities` — pure map from a build *spec* to the set of ids
  that spec actually needs (so we never warn about features the user didn't ask
  for). Severity is handled by *inclusion*: an id is only required in the context
  where its ``severity`` applies.
- :func:`assess` — intersect required vs the env's manifest → a report with three
  buckets (ok / blockers / warnings) plus a full inventory, each row carrying the
  produces/fix chain.

Stdlib-only; no ``quam`` imports. Reuses the spec vocabulary from
``config_generator`` (line types, CZ variants) so requirement mapping stays in
sync with validation. See ``docs/52_env_capabilities.md``.
"""

from __future__ import annotations

from typing import Any

BLOCKER = "blocker"
DEGRADE = "degrade"
INFO = "info"


# id -> metadata. ``severity`` is what a MISS means *when the capability is
# required* (required_capabilities only pulls it in where that severity holds).
REGISTRY: dict[str, dict] = {
    # -- wiring lines ------------------------------------------------------
    "wire.resonator_line": {
        "label": "Readout resonator lines", "category": "wiring",
        "package": "qualang-tools", "symbol": "Connectivity.add_resonator_line",
        "produces": "the multiplexed readout feed-line per resonator",
        "fix": "install/upgrade qualang-tools", "severity": BLOCKER},
    "wire.qubit_drive_line": {
        "label": "Qubit xy drive lines", "category": "wiring",
        "package": "qualang-tools", "symbol": "Connectivity.add_qubit_drive_lines",
        "produces": "the MW xy drive channel per qubit",
        "fix": "install/upgrade qualang-tools", "severity": BLOCKER},
    "wire.qubit_flux_line": {
        "label": "Qubit flux (z) lines", "category": "wiring",
        "package": "qualang-tools", "symbol": "Connectivity.add_qubit_flux_lines",
        "produces": "the LF flux (z) channel per flux-tunable qubit",
        "fix": "install/upgrade qualang-tools", "severity": BLOCKER},
    "wire.pair_flux_line": {
        "label": "Tunable-coupler flux lines", "category": "wiring",
        "package": "qualang-tools", "symbol": "Connectivity.add_qubit_pair_flux_lines",
        "produces": "the LF coupler flux channel per tunable-coupler pair",
        "fix": "install/upgrade qualang-tools", "severity": BLOCKER},
    "wire.pair_cross_resonance_line": {
        "label": "Cross-resonance lines", "category": "wiring",
        "package": "qualang-tools", "symbol": "Connectivity.add_qubit_pair_cross_resonance_lines",
        "produces": "the CR drive channel per CR pair",
        "fix": "install/upgrade qualang-tools", "severity": BLOCKER},
    "wire.pair_zz_drive_line": {
        "label": "ZZ-drive lines", "category": "wiring",
        "package": "qualang-tools", "symbol": "Connectivity.add_qubit_pair_zz_drive_lines",
        "produces": "the ZZ-drive channel per pair",
        # BLOCKER (was degrade before any producer of zz lines existed):
        # build_connectivity calls add_qubit_pair_zz_drive_lines unconditionally
        # when a zz_drive line is in the spec — a miss is an AttributeError
        # crash inside the build, not a graceful feature drop.
        "fix": "install/upgrade qualang-tools", "severity": BLOCKER},
    "wire.alloc_block_reuse": {
        "label": "Shared-port wiring allocation", "category": "wiring",
        "package": "qualang-tools", "symbol": "allocate_wiring(block_used_channels=...)",
        "produces": "CR/ZZ drives sharing the control qubit's xy port "
                    "(the dual-upconverter customer layout)",
        "fix": "upgrade qualang-tools to a build whose allocate_wiring accepts "
               "block_used_channels", "severity": BLOCKER},
    "wire.twpa_lines": {
        "label": "TWPA pump lines", "category": "wiring",
        "package": "qualang-tools", "symbol": "Connectivity.add_twpa_lines",
        "produces": "pump + pump_ channels on one MW-FEM port per TWPA",
        "fix": "upgrade qualang-tools to a build that exposes add_twpa_lines",
        "severity": DEGRADE},
    # -- instruments -------------------------------------------------------
    "instr.mw_fem": {
        "label": "MW-FEM", "category": "instruments",
        "package": "qualang-tools", "symbol": "Instruments.add_mw_fem",
        "produces": "MW-FEM output/input ports (readout, xy, coupler, TWPA)",
        "fix": "install/upgrade qualang-tools", "severity": BLOCKER},
    "instr.lf_fem": {
        "label": "LF-FEM", "category": "instruments",
        "package": "qualang-tools", "symbol": "Instruments.add_lf_fem",
        "produces": "LF-FEM output ports (qubit / coupler flux)",
        "fix": "install/upgrade qualang-tools", "severity": BLOCKER},
    "instr.opx_plus": {
        "label": "OPX+", "category": "instruments",
        "package": "qualang-tools", "symbol": "Instruments.add_opx_plus",
        "produces": "OPX+ analog ports",
        "fix": "install/upgrade qualang-tools", "severity": BLOCKER},
    "instr.octave": {
        "label": "Octave", "category": "instruments",
        "package": "qualang-tools", "symbol": "Instruments.add_octave",
        "produces": "Octave up/down-conversion channels",
        "fix": "install/upgrade qualang-tools", "severity": BLOCKER},
    # -- build core --------------------------------------------------------
    "build.quam_wiring": {
        "label": "Wiring builder", "category": "build",
        "package": "quam-builder", "symbol": "build_quam_wiring",
        "produces": "wiring.json from the allocated connectivity",
        "fix": "install/upgrade quam-builder", "severity": BLOCKER},
    "build.quam": {
        "label": "State builder", "category": "build",
        "package": "quam-builder", "symbol": "build_quam",
        "produces": "state.json (qubits / pairs / resonators / twpas stubs)",
        "fix": "install/upgrade quam-builder", "severity": BLOCKER},
    "qpu.flux_tunable": {
        "label": "Flux-tunable QPU class", "category": "build",
        "package": "quam-builder", "symbol": "FluxTunableQuam",
        "produces": "the flux-tunable-transmon machine root",
        "fix": "install/upgrade quam-builder", "severity": BLOCKER},
    "qpu.fixed_frequency": {
        "label": "Fixed-frequency QPU class", "category": "build",
        "package": "quam-builder", "symbol": "FixedFrequencyQuam",
        "produces": "the fixed-frequency-transmon machine root",
        "fix": "install/upgrade quam-builder", "severity": BLOCKER},
    # -- single-qubit gates (unconditional) --------------------------------
    "pulses.drag_cosine": {
        "label": "DRAG single-qubit gates", "category": "1Q gates",
        "package": "quam-builder", "symbol": "add_DragCosine_pulses",
        "produces": "x180 / x90 / etc. DRAG-cosine pulse set on every qubit",
        "fix": "install/upgrade quam-builder", "severity": BLOCKER},
    "pulses.square": {
        "label": "Square pulse", "category": "1Q gates",
        "package": "quam", "symbol": "pulses.SquarePulse",
        "produces": "the base square pulse used by readout / CZ / CR",
        "fix": "install/upgrade quam", "severity": BLOCKER},
    # -- two-qubit gates ---------------------------------------------------
    "pair.cz_gate": {
        "label": "CZ gate macro", "category": "2Q gates",
        "package": "quam-builder", "symbol": "CZGate",
        "produces": "the cz_* gate macro on each pair",
        "fix": "install/upgrade quam-builder", "severity": BLOCKER},
    "pair.cz_parametric": {
        "label": "Parametric CZ gate", "category": "2Q gates",
        "package": "quam-builder", "symbol": "ParametricCZGate",
        "produces": "a parametric-drive CZ macro (else falls back to unipolar CZ)",
        "fix": "upgrade quam-builder to a build with ParametricCZGate",
        "severity": DEGRADE},
    "pair.fixed_pair": {
        "label": "Flux-tunable transmon pair", "category": "2Q gates",
        "package": "quam-builder", "symbol": "FluxTunableTransmonPair",
        "produces": "the fixed-coupler pair object a CZ needs (no coupler wiring)",
        "fix": "install/upgrade quam-builder", "severity": BLOCKER},
    "pair.cr_gate": {
        "label": "CR gate macro", "category": "2Q gates",
        "package": "quam-builder", "symbol": "CRGate",
        "produces": "the cr gate macro (else the CR channel exists but pair.apply('cr') is missing)",
        "fix": "upgrade quam-builder to a build with CRGate",
        "severity": DEGRADE},
    "pair.stark_cz_gate": {
        "label": "Stark-induced CZ macro", "category": "2Q gates",
        "package": "quam-builder", "symbol": "StarkInducedCZGate",
        "produces": "the stark_cz gate macro on ZZ-driven pairs "
                    "(else the ZZ channel exists but pair.apply('stark_cz') is missing)",
        "fix": "upgrade quam-builder to a build with StarkInducedCZGate",
        "severity": DEGRADE},
    "pair.cr_channel": {
        "label": "CR channel component", "category": "2Q gates",
        "package": "quam-builder", "symbol": "CrossResonanceMW / CrossResonanceDriveMW",
        "produces": "the cross_resonance channel object build_quam creates for "
                    "each cr wiring line (a modern wirer + old builder passes "
                    "the wiring blocker yet dies inside build_quam without it)",
        "fix": "install quam-builder from the CR/CZ-macros branch: pip install "
               "'quam-builder @ git+https://github.com/qua-platform/"
               "quam-builder.git@fa540b6'", "severity": BLOCKER},
    "pair.zz_channel": {
        "label": "ZZ-drive channel component", "category": "2Q gates",
        "package": "quam-builder", "symbol": "ZZDriveMW",
        "produces": "the zz_drive channel object build_quam creates for each "
                    "zz wiring line",
        "fix": "install quam-builder from the CR/CZ-macros branch: pip install "
               "'quam-builder @ git+https://github.com/qua-platform/"
               "quam-builder.git@fa540b6'", "severity": BLOCKER},
    "chan.xy_detuned": {
        "label": "Detuned-xy target channel", "category": "2Q gates",
        "package": "quam-builder", "symbol": "XYDetunedDriveMW",
        "produces": "the target qubit's xy_detuned channel that Stark-CZ drives "
                    "(else ZZ seeds without the target-lobe twins)",
        "fix": "install quam-builder from the CR/CZ-macros branch (fa540b6)",
        "severity": DEGRADE},
    "qpu.fixed_frequency_zz": {
        "label": "ZZ-drive QPU class", "category": "build",
        "package": "quam-builder", "symbol": "FixedFrequencyZZDriveQuam",
        "produces": "the machine root whose qubits carry xy_detuned "
                    "(else falls back to FixedFrequencyQuam; ZZ twins skipped)",
        "fix": "install quam-builder from the CR/CZ-macros branch (fa540b6)",
        "severity": DEGRADE},
    # -- schema-flavor markers (INFO — inventory only, never *required*; they
    #    name which CR generation this env writes/loads so flavor_findings can
    #    warn about chip↔env mismatches BEFORE a Quam.load fails) ------------
    "cr.flavor_rf_pointer": {
        "label": "CR schema: RF-pointer flavor", "category": "flavor",
        "package": "quam-builder", "symbol": "CrossResonance*.target_qubit_RF_frequency",
        "produces": "reads/writes target_qubit_RF_frequency CR states (the "
                    "customer flavor); absent = the LO/IF-literal flavor",
        "fix": "", "severity": INFO},
    "pair.zz_field_zz_drive": {
        "label": "ZZ pair field spelled zz_drive", "category": "flavor",
        "package": "quam-builder", "symbol": "FixedFrequencyTransmonPair.zz_drive",
        "produces": "serializes the pair's ZZ channel under 'zz_drive' "
                    "(the branch tip renames it to 'zz')",
        "fix": "", "severity": INFO},
    # -- CZ-variant pulse shapes ------------------------------------------
    "pulse.cz_flattop": {
        "label": "Flat-top CZ shape", "category": "2Q pulse shapes",
        "package": "quam", "symbol": "pulses._FlatTopGaussianPulse",
        "produces": "the flat-top CZ flux pulse (else falls back to unipolar)",
        "fix": "upgrade quam to a build with _FlatTopGaussianPulse", "severity": DEGRADE},
    "pulse.cz_bipolar": {
        "label": "Bipolar CZ shape", "category": "2Q pulse shapes",
        "package": "quam", "symbol": "pulses._CosineBipolarPulse",
        "produces": "the cosine-bipolar CZ flux pulse (else falls back to unipolar)",
        "fix": "upgrade quam to a build with _CosineBipolarPulse", "severity": DEGRADE},
    "pulse.cz_snz": {
        "label": "SNZ CZ shape", "category": "2Q pulse shapes",
        "package": "quam / quam-builder", "symbol": "pulses.SNZPulse",
        "produces": "the SNZ CZ flux pulse (else falls back to unipolar)",
        "fix": "upgrade quam or quam-builder to a build with SNZPulse "
               "(quam <=0.5 ships it in quam.components.pulses; "
               "quam-builder >=0.4 in its architecture package)",
        "severity": DEGRADE},
    "pulse.cz_erf": {
        "label": "Erf-square CZ shape", "category": "2Q pulse shapes",
        "package": "quam / quam-builder", "symbol": "pulses.ErfSquarePulse",
        "produces": "the erf-square CZ flux pulse (else falls back to unipolar)",
        "fix": "upgrade quam or quam-builder to a build with ErfSquarePulse "
               "(quam <=0.5 ships it in quam.components.pulses; "
               "quam-builder >=0.4 in its architecture package)",
        "severity": DEGRADE},
    "pulse.cr_flattop": {
        "label": "Flat-top CR shape", "category": "2Q pulse shapes",
        "package": "quam / quam-builder", "symbol": "pulses.FlatTopGaussianPulse",
        "produces": "the flat-top CR drive op (else CR keeps only its square op)",
        "fix": "upgrade quam or quam-builder to a build with FlatTopGaussianPulse",
        "severity": DEGRADE},
    # -- runtime (preview / QUA only) -------------------------------------
    "runtime.qm_qua": {
        "label": "qm-qua runtime", "category": "runtime",
        "package": "qm-qua", "symbol": "QuantumMachinesManager",
        "produces": "the QUA config preview (generate_config) after a build",
        "fix": "install qm-qua", "severity": INFO},
}

# Which cz_variant needs which pulse-shape capability (bipolar needs both).
_CZ_VARIANT_CAPS: dict[str, tuple[str, ...]] = {
    "unipolar": (),                       # SquarePulse — already core
    "flattop": ("pulse.cz_flattop",),
    "bipolar": ("pulse.cz_bipolar", "pulse.cz_flattop"),
    "SNZ": ("pulse.cz_snz",),
    "flattop_erf": ("pulse.cz_erf",),
}

_LINE_CAP = {
    "flux": "wire.qubit_flux_line",
    "coupler": "wire.pair_flux_line",
    "cross_resonance": "wire.pair_cross_resonance_line",
    "zz_drive": "wire.pair_zz_drive_line",
    "twpa_pump": "wire.twpa_lines",
    "twpa_isolation": "wire.twpa_lines",
}


def _line_types(spec: dict) -> set[str]:
    return {ln.get("line") for ln in (spec.get("lines") or []) if isinstance(ln, dict)}


def _pair_populate(spec: dict) -> list[dict]:
    pop = (spec.get("populate") or {}).get("pairs") or {}
    return [v for v in pop.values() if isinstance(v, dict)]


def required_capabilities(spec: dict) -> set[str]:
    """The capability ids this build *spec* needs (pure). Context is expressed by
    inclusion: an id appears only where its :data:`REGISTRY` severity applies."""
    req: set[str] = {
        "build.quam_wiring", "build.quam",
        "pulses.drag_cosine", "pulses.square",
        "wire.resonator_line", "wire.qubit_drive_line",
    }
    lines = _line_types(spec)
    for lt in lines:
        cap = _LINE_CAP.get(lt)
        if cap:
            req.add(cap)

    # QPU root class: flux-tunable if the chip has qubit/coupler flux, else fixed.
    req.add("qpu.flux_tunable" if ("flux" in lines or "coupler" in lines)
            else "qpu.fixed_frequency")

    # instruments
    instr = spec.get("instruments") or {}
    for ctrl in instr.get("controllers") or []:
        for fem in (ctrl.get("fems") or []):
            if fem.get("fem") == "mw":
                req.add("instr.mw_fem")
            elif fem.get("fem") == "lf":
                req.add("instr.lf_fem")
    if instr.get("opx_plus"):
        req.add("instr.opx_plus")
    if instr.get("octaves"):
        req.add("instr.octave")

    # two-qubit gate family
    pair_gate = spec.get("pair_gate") or ""
    has_pairs = bool(spec.get("qubit_pairs"))
    if has_pairs:
        if pair_gate.startswith("cz"):
            req.add("pair.cz_gate")
            if pair_gate == "cz_fixed":
                req.add("pair.fixed_pair")   # pair never created without it → blocker
        elif pair_gate == "cr":
            req.add("pair.cr_gate")
            req.add("pulse.cr_flattop")

    # CR/ZZ pair-channel components: build_quam realizes each cr/zz wiring line
    # through these classes — required whenever the corresponding line exists.
    if "cross_resonance" in lines:
        req.add("pair.cr_channel")
    if "zz_drive" in lines:
        req.add("pair.zz_channel")
        req.add("pair.stark_cz_gate")
        req.add("qpu.fixed_frequency_zz")
        req.add("chan.xy_detuned")

    # Shared-port CR layout (dual upconverter on the control's xy port) needs
    # allocate_wiring(block_used_channels=...) for the two-phase allocation.
    if spec.get("cr_port_mode") == "shared_xy":
        req.add("wire.alloc_block_reuse")

    # per-pair CZ variants + parametric gate type (from the populate step)
    for pv in _pair_populate(spec):
        variant = pv.get("cz_variant")
        for cap in _CZ_VARIANT_CAPS.get(variant, ()):
            req.add(cap)
        if pv.get("gate_type") == "cz_parametric":
            req.add("pair.cz_parametric")

    return req


def _manifest_available(manifest: Any, cid: str) -> tuple[bool, str]:
    caps = (manifest or {}).get("capabilities") or {}
    entry = caps.get(cid) or {}
    return bool(entry.get("available")), str(entry.get("detail") or "")


def _row(cid: str, detail: str, requested: bool, available: bool) -> dict:
    meta = REGISTRY.get(cid, {})
    return {
        "id": cid,
        "label": meta.get("label", cid),
        "category": meta.get("category", ""),
        "package": meta.get("package", ""),
        "symbol": meta.get("symbol", ""),
        "produces": meta.get("produces", ""),
        "fix": meta.get("fix", ""),
        "severity": meta.get("severity", DEGRADE),
        "detail": detail,
        "requested": requested,
        "available": available,
    }


def assess(spec: dict, manifest: Any) -> dict:
    """Compare what ``spec`` needs against the env's capability ``manifest``.

    Returns::

        {"buildable": bool, "manifest_ok": bool, "versions": {...},
         "ok": [...], "blockers": [...], "warnings": [...], "inventory": [...]}

    ``blockers`` = requested + missing + severity blocker (build WILL fail).
    ``warnings`` = requested + missing + degrade (build succeeds, feature dropped/
    falls back). ``inventory`` = every catalog capability with available/requested
    flags (the full "this env can / can't build …" map). ``manifest_ok`` is False
    when no capability manifest was available (probe failed / not yet run) — the
    caller should treat that as "unknown, re-probe" rather than "all missing".
    """
    manifest_ok = bool((manifest or {}).get("capabilities"))
    required = required_capabilities(spec)

    ok: list[dict] = []
    blockers: list[dict] = []
    warnings: list[dict] = []
    inventory: list[dict] = []

    # No manifest (probe failed / not yet run) → UNKNOWN, not "all missing".
    # Don't fabricate blockers from an env we couldn't inspect; the caller
    # re-probes. buildable is False only because we can't confirm it.
    if not manifest_ok:
        return {"buildable": False, "manifest_ok": False,
                "versions": (manifest or {}).get("versions") or {},
                "ok": [], "blockers": [], "warnings": [], "inventory": []}

    for cid in REGISTRY:
        available, detail = _manifest_available(manifest, cid)
        requested = cid in required
        inventory.append(_row(cid, detail, requested, available))
        if not requested:
            continue
        row = _row(cid, detail, True, available)
        if available:
            ok.append(row)
        elif row["severity"] == BLOCKER:
            blockers.append(row)
        else:                       # degrade (info-severity caps are never required)
            warnings.append(row)

    inventory.sort(key=lambda r: (r["category"], r["label"]))
    return {
        "buildable": manifest_ok and not blockers,
        "manifest_ok": manifest_ok,
        "versions": (manifest or {}).get("versions") or {},
        "ok": ok,
        "blockers": blockers,
        "warnings": warnings,
        "inventory": inventory,
    }


# ---------------------------------------------------------------------------
# Chip ↔ env schema-flavor findings (the pre-Quam.load mismatch warning)
# ---------------------------------------------------------------------------

def flavor_findings(state: dict, manifest: Any) -> list[dict]:
    """Compare a CHIP's CR schema flavor against an ENV's flavor markers.

    ``Quam.load`` fails on an unknown dataclass field or a missing class — so a
    chip written by one quam-builder generation cannot even be *loaded* by an
    env from another (the CR branch renamed fields, classes, and modules while
    the version string stood still). This surfaces that BEFORE any subprocess
    load: Config Viewer, generate preview, Verify-vs-config, and regenerate all
    gate on it. Returns ``[{"level": "blocker"|"warning", "message": str}, ...]``
    — empty for non-CR chips or when no manifest is available (unknown ≠ bad).
    """
    from quam_state_manager.core import cr_semantics

    findings: list[dict] = []
    caps = (manifest or {}).get("capabilities") or {}
    if not isinstance(state, dict) or not caps:
        return findings

    report = cr_semantics.detect_flavor(state)
    if report.flavor in (cr_semantics.FLAVOR_NONE,):
        return findings

    def _has(cid: str) -> bool:
        entry = caps.get(cid) or {}
        return bool(entry.get("available"))

    env_rf = _has("cr.flavor_rf_pointer")
    fix = ("pick/build an env with quam-builder from the CR/CZ-macros branch "
           "(pip install 'quam-builder @ git+https://github.com/qua-platform/"
           "quam-builder.git@fa540b6')")

    if report.flavor in (cr_semantics.FLAVOR_RF, cr_semantics.FLAVOR_RF_DRIVE):
        if not _has("pair.cr_channel"):
            findings.append({"level": "blocker", "message":
                "this chip has cross-resonance channels but the selected env's "
                "quam-builder has no CR channel class at all — " + fix})
        elif not env_rf:
            findings.append({"level": "blocker", "message":
                "this chip stores CR as target_qubit_RF_frequency (the "
                "RF-pointer flavor) — the selected env's CrossResonance class "
                "has no such field, so Quam.load() will fail; " + fix})
    elif report.flavor == cr_semantics.FLAVOR_LO_IF and env_rf:
        findings.append({"level": "warning", "message":
            "this chip stores CR as target_qubit_LO/IF_frequency literals (the "
            "older flavor) but the selected env writes the RF-pointer flavor — "
            "loading may fail on the unknown LO/IF fields; prefer an env "
            "matching the chip's quam-builder generation"})

    # Qubit class: FixedFrequencyZZDriveTransmon exists only on the CR branch.
    qubits = state.get("qubits")
    if isinstance(qubits, dict):
        uses_zz_transmon = any(
            isinstance(q, dict)
            and isinstance(q.get("__class__"), str)
            and q["__class__"].rsplit(".", 1)[-1] == "FixedFrequencyZZDriveTransmon"
            for q in qubits.values())
        if uses_zz_transmon and not _has("qpu.fixed_frequency_zz"):
            findings.append({"level": "blocker", "message":
                "this chip's qubits are FixedFrequencyZZDriveTransmon — the "
                "selected env's quam-builder has no such class, so Quam.load() "
                "will fail; " + fix})

    # Pair ZZ field spelling: the branch tip renamed zz_drive → zz.
    pairs = state.get("qubit_pairs")
    if isinstance(pairs, dict):
        uses_tip_zz = any(isinstance(p, dict) and "zz" in p and "zz_drive" not in p
                          for p in pairs.values())
        if uses_tip_zz and _has("pair.zz_field_zz_drive"):
            findings.append({"level": "warning", "message":
                "this chip spells the pair ZZ channel 'zz' (branch-tip flavor) "
                "but the selected env's pair class still uses 'zz_drive' — "
                "loading may drop or reject the ZZ channel"})

    return findings
