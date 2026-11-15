"""Check a slot gate in the user's QM interpreter, never imported by SM.

Standard library only at import time. --in and --out name JSON files.
Input: file (absolute path), class_name, spectators (name: [V, ns, unit V]).
Optional probes: inline_slots, probe_slot_values, spectator_keys (dict name:
key list), spectator_own (spectator name: control or target), apply_kwargs.
Output: status describes the script; ok describes the class. imports holds
ok/error, slots_declared and slots_played are lists, spectator_plays holds
name/scale/duration records. Errors include the line in the user's file.
No chip file is loaded. Real QUAM channels and a property-override subclass
exercise apply inside a real QUA program; QUA spectator calls are delegated.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace


def _draft_line(exc, path):
    if isinstance(exc, SyntaxError) and exc.filename == path:
        return exc.lineno
    line = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if Path(frame.filename).resolve() == Path(path).resolve():
            line = frame.lineno
    return line


def _load(path, class_name):
    data = Path(path).read_bytes()
    name = "sm_slot_gate_" + hashlib.sha256(data).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(data, path, "exec"), module.__dict__)
    cls = getattr(module, class_name)
    if not isinstance(cls, type):
        raise TypeError("Requested name is not a class.")
    return module, cls


def check(request):
    result = {"status": "ok", "ok": False, "imports": {"ok": False, "error": None},
              "slots_declared": [], "slots_played": [], "spectator_plays": [],
              "error": None, "line": None, "slot_plays": [], "rotations": [], "alignments": []}
    path = request["file"]
    try:
        module, cls = _load(path, request["class_name"])
        result["imports"]["ok"] = True
        from qm.qua import program

        from quam.components.channels import Channel
        from quam.components.pulses import SquarePulse

        class RecordingChannel(Channel):
            def play(self, label, **kwargs):
                slot = self.name.removesuffix("_z")
                result["slots_played"].append(slot)
                result["slot_plays"].append(dict(slot=slot, label=label, **kwargs))
                return super().play(label, **kwargs)

            def frame_rotation_2pi(self, phase):
                result["rotations"].append([self.name, phase])
                return super().frame_rotation_2pi(phase)

        def qubit(name, unit=0.1):
            operations = {name + "_op": SquarePulse(length=16, amplitude=0.1)}
            unit_op = module.__dict__.get("SPECTATOR_UNIT_OP")
            if unit_op:
                operations[unit_op] = SquarePulse(length=16, amplitude=unit)
            z = RecordingChannel(id=name + "_z", operations=operations)
            xy = RecordingChannel(id=name + "_xy")
            return SimpleNamespace(z=z, xy=xy, channels={"z": z, "xy": xy})

        pair = SimpleNamespace(qubit_control=qubit("control"),
                               qubit_target=qubit("target"),
                               coupler=RecordingChannel(id="coupler", operations={
                                   "coupler_op": SquarePulse(length=16, amplitude=0.1)}))

        class Recorder(cls):
            @property
            def qubit_pair(self):
                return pair

        declared = [s for s in ("control", "target", "coupler")
                    if s + "_flux_pulse" in {f.name for f in dataclasses.fields(cls)}]
        result["slots_declared"] = declared
        params = {s + "_flux_pulse": s + "_op" for s in declared}
        if request.get("inline_slots"):
            params.update({s + "_flux_pulse": SquarePulse(length=16, amplitude=0.1)
                           for s in request["inline_slots"]})
        rows = request.get("spectators", {})
        if rows:
            params.update(spectator_qubits={n: qubit(n, unit=row[2]) for n, row in rows.items()},
                          spectator_levels={n: row[0] for n, row in rows.items()},
                          spectator_lengths={n: row[1] for n, row in rows.items()})
        if "spectator_keys" in request:
            for key, names in request["spectator_keys"].items():
                params["spectator_" + key] = {n: params["spectator_" + key].get(n, 20) for n in names}
        for name, role in request.get("spectator_own", {}).items():
            params["spectator_qubits"][name] = getattr(pair, "qubit_" + role)
        gate = Recorder(**params)
        if request.get("probe_slot_values"):
            result["slot_value_probes"] = []
            for slot in declared:
                channel = pair.coupler if slot == "coupler" else getattr(pair, "qubit_" + slot).z
                for kind in ("string", "inline"):
                    probe_params = {s + "_flux_pulse": s + "_op" for s in declared}
                    if kind == "inline":
                        probe_params[slot + "_flux_pulse"] = SquarePulse(length=16, amplitude=0.1)
                    probe = Recorder(**probe_params)
                    record = dict(slot=slot, kind=kind, ok=False, channel_play_called=False)
                    try:
                        with program():
                            label = getattr(probe, slot + "_flux_pulse_label")
                            record["channel_play_called"] = True
                            channel.play(label, validate=slot != "coupler")
                        record["ok"] = True
                    except Exception as exc:
                        record["error"] = f"{type(exc).__name__}: {exc}"
                    result["slot_value_probes"].append(record)
            result["slots_played"].clear()
            result["slot_plays"].clear()
        # Keep real QUA emission, recording the evaluated numeric amp argument.
        real_amp, real_play, real_align = module.amp, module.play, module.align
        scales = []

        def record_amp(scale):
            scales.append(scale)
            return real_amp(scale)

        def record_play(pulse, element, **kwargs):
            result["spectator_plays"].append({"name": element.removesuffix("_z"),
                                               "scale": scales.pop(0),
                                               "duration": kwargs.get("duration")})
            return real_play(pulse, element, **kwargs)

        def record_align(*channels):
            result["alignments"].append(list(channels))
            return real_align(*channels)

        module.amp, module.play, module.align = record_amp, record_play, record_align
        with program():
            result["program_build_started"] = True
            gate.apply(**request.get("apply_kwargs", {}))
        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["line"] = _draft_line(exc, path)
        if not result["imports"]["ok"]:
            result["imports"]["error"] = result["error"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="input", required=True)
    parser.add_argument("--out", dest="output", required=True)
    args = parser.parse_args()
    try:
        result = check(json.loads(Path(args.input).read_text(encoding="utf-8")))
    except Exception as exc:
        result = {"status": "error", "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(args.output).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
