"""Orchestrate the Re-generate Config pipeline: reconstruct -> build -> merge.

Ties :mod:`core.regen_spec` (structure from an existing chip) +
:mod:`core.config_generator` (subprocess rebuild) + :mod:`core.regen_merge`
(value-preserving merge) into one flow::

    old state+wiring  --reconstruct-->  spec  --(user edits in wizard)-->
    edited spec  --build (subprocess)-->  fresh structure  --merge old values-->
    final config in a NEW output folder (never overwriting the source).

The State Manager process never imports quam/quam_builder; the build step shells
out to the user-selected env via ``config_generator``. Everything here is pure
orchestration + JSON I/O through ``safe_io``. Verified end-to-end (P2): residual
loss 0, merged state compiles to a valid QUA config that supersets the original.
See ``docs/51_regenerate_config.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import config_generator, regen_merge, regen_script, regen_spec, safe_io


def reconstruct_from_folder(folder: Path | str) -> regen_spec.ReconstructedSpec:
    """Read a chip folder's state+wiring and reconstruct its build spec.

    Used to pre-fill the wizard when the user re-generates a chip (the currently
    loaded live chip by default, or any folder via the Load button). Prefers an
    EXACT spec sidecar (written by a prior rebuild) over the best-effort
    reconstruction when it exists and its hash still matches the chip.
    """
    folder = Path(folder)
    state, wiring = safe_io.read_state_wiring(folder)
    sidecar = regen_spec.load_spec_sidecar(folder, state, wiring)
    if sidecar is not None:
        # Exact structure from the sidecar; refresh populate from the CURRENT
        # state so displayed seeds reflect any in-app value edits since the build.
        merged = dict(state)
        merged["wiring"] = wiring.get("wiring", {})
        sidecar["populate"] = regen_spec._extract_populate(state, merged)
        return regen_spec.ReconstructedSpec(spec=sidecar, exact=True)
    return regen_spec.reconstruct_spec(state, wiring)


def run_regenerate(
    python_path: str,
    old_folder: Path | str,
    spec: dict,
    out_dir: Path | str,
    timeout: int = 300,
) -> dict:
    """Build ``spec`` fresh into ``out_dir`` then merge the OLD chip's values on.

    ``out_dir`` MUST differ from ``old_folder`` (the caller enforces the new-path
    rule; this never writes into the source). Returns the ``run_generator``
    outcome dict with an added ``"merge"`` block carrying transparency counts::

        {..., "merge": {"carried", "grafted", "kept_new_pointer",
                        "kept_new_only", "graft_subtrees", "residual_lost",
                        "dangling_grafts"}}

    On a build failure ``"merge"`` is ``None`` and the outcome carries the error.
    Never raises.
    """
    old_folder = Path(old_folder)
    out_dir = Path(out_dir)
    if old_folder.resolve() == out_dir.resolve():
        return {**config_generator._blank_outcome(),
                "error": "output folder must differ from the source chip folder",
                "merge": None}

    outcome = config_generator.run_generator(
        python_path, "build", spec, out_dir, timeout=timeout)
    if not outcome.get("ok"):
        outcome["merge"] = None
        return outcome

    try:
        old_state, old_wiring = safe_io.read_state_wiring(old_folder)
        new_state, new_wiring = safe_io.read_state_wiring(out_dir)
    except (OSError, ValueError) as exc:
        outcome["merge"] = None
        outcome["error"] = f"could not read state for merge: {exc}"
        return outcome

    result = regen_merge.merge_states(old_state, new_state)

    # TWPAs are grafted back at the state level but the builder made no TWPA
    # wiring/ports — carry those from OLD so the channel resolves and
    # generate_config() doesn't crash. This also un-dangles the TWPA pointers.
    twpa_carried = regen_merge.graft_twpa_wiring(
        result.merged, old_state, old_wiring, new_wiring)
    if twpa_carried:
        safe_io.atomic_write_json(out_dir / "wiring.json", new_wiring)
        # the TWPA channel pointers now resolve against the carried wiring, so
        # drop them from the state-only merge's "dangling" report (it can't see
        # wiring). What remains dangling, if anything, is a genuine broken ref.
        result.stats.dangling_grafts = [
            p for p in result.stats.dangling_grafts if not p.startswith("twpas.")]

    safe_io.atomic_write_json(out_dir / "state.json", result.merged)

    # Emit the editable build-script bundle alongside the rebuilt state, so the
    # user OWNS the config as Python. script_emitter is the SINGLE maintained
    # emitter (docs/54): it embeds run_build's own machinery verbatim —
    # including the CR/ZZ seeders and the shared-port two-phase allocation —
    # so the bundle reproduces the wizard build exactly, which regen_script's
    # pair_gates-repo idiom structurally cannot for CR chips. Written into a
    # subfolder so the chip dir stays clean (Quam.load ignores non-.json
    # either way). Best-effort: a script-emit hiccup never fails the merge.
    script_name = None
    try:
        from . import script_emitter
        chip = out_dir.name or "chip"
        res = outcome.get("result") or {}
        bundle = script_emitter.emit_bundle(
            spec, res.get("allocation"), res.get("versions"), chip)
        bundle_dir = out_dir / "build_scripts"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        for name, src in bundle.items():
            (bundle_dir / name).write_text(src, encoding="utf-8")
        script_name = "build_scripts/"
    except Exception as exc:  # noqa: BLE001 — transparency, not a hard failure
        outcome["script_error"] = str(exc)
        script_name = None

    # Exact-spec sidecar keyed by the OUTPUT chip's hash, so a later re-generate
    # FROM this folder uses the exact spec instead of re-inferring. Best-effort.
    regen_spec.write_spec_sidecar(out_dir, spec, result.merged, new_wiring)

    s = result.stats
    outcome["merge"] = {
        "carried": s.carried,
        "grafted": s.grafted,
        "kept_new_pointer": s.kept_new_pointer,
        "kept_new_only": s.kept_new_only,
        "graft_subtrees": s.graft_subtrees[:50],
        "superseded": len(s.superseded),
        "superseded_paths": s.superseded[:80],
        "residual_lost": s.residual_lost[:200],
        "dangling_grafts": s.dangling_grafts[:200],
        "pruned_ops": len(s.pruned_ops),
        "twpa_wiring_carried": twpa_carried,
    }
    outcome["script"] = script_name   # emitted build recipe filename, or None
    return outcome
