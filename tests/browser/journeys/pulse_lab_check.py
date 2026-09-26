"""Does a pulse SM wrote behave like one the lab's own code would write?

Run in the LAB's env (it imports the chip's root class) against a COPY of a
chip folder:

    <lab python> pulse_lab_check.py <chip folder copy> <expect.json> [out.json]

For every pulse the create journey recorded (expect.json from
pulses_create_qa.cjs) it answers, executably:

  load     -- Quam.load() of the whole chip succeeds.
  typed    -- the loaded object carries exactly the values the user typed.
  native   -- quam's own serialisation of ``cls(**typed)`` (what a lab script
              doing ``ops[name] = Cls(...)`` + ``machine.save()`` writes) equals
              what SM wrote, key for key.
  resave   -- a Quam.load -> machine.save round trip (every lab script and node
              that saves does this) keeps the pulse byte-equal.
  config   -- machine.generate_config() compiles and the pulse is in it (the
              thing a QUA program actually plays).
  macro    -- for a gate macro: the pulse label its apply() plays exists on
              the moving qubit's z channel (quam_builder CZGate.apply plays
              ``moving_qubit.z.play(self.flux_pulse_qubit_label)``).

Nothing here writes the folder it is given; the round trip saves to a temp dir.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path


def _num_eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=1e-12, abs_tol=0.0)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_num_eq(x, y) for x, y in zip(a, b))
    return a == b


def _parse_typed(v: str):
    s = str(v).strip()
    if "," in s:
        return [float(x) for x in s.split(",") if x.strip()]
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def _walk(obj, dot):
    for seg in dot.split("."):
        if isinstance(obj, dict):
            obj = obj[seg]
        else:
            try:
                obj = obj[seg]
            except Exception:  # noqa: BLE001
                obj = getattr(obj, seg)
    return obj


def main(folder: str, expect_path: str, out_path: str | None):
    from quam.core import QuamRoot  # noqa: F401  (import check)
    state = json.loads(Path(folder, "state.json").read_text(encoding="utf-8"))
    root_cls = state.get("__class__", "quam.core.QuamRoot")
    mod, _, name = root_cls.rpartition(".")
    Root = getattr(__import__(mod, fromlist=[name]), name)
    exp = json.loads(Path(expect_path).read_text(encoding="utf-8"))["expect"]
    res = {"root": root_cls, "items": []}
    try:
        machine = Root.load(folder)
        res["load"] = "ok"
    except Exception as e:  # noqa: BLE001
        res["load"] = f"FAIL {type(e).__name__}: {e}"[:400]
        print(json.dumps(res, indent=1))
        return 1
    try:
        cfg = machine.generate_config()
        res["config"] = "ok"
    except Exception as e:  # noqa: BLE001
        cfg = None
        res["config"] = f"FAIL {type(e).__name__}: {e}"[:400]
    tmp = Path(tempfile.mkdtemp(prefix="pulse_lab_check_"))
    machine.save(str(tmp))
    saved = json.loads((tmp / "state.json").read_text(encoding="utf-8")) \
        if (tmp / "state.json").exists() else {}
    for e in exp:
        if "path" not in e:
            continue
        path = e["path"]
        it = {"path": path, "cls": e["cls"]}
        sm_json = _walk(state, path)
        try:
            obj = _walk(machine, path)
        except Exception as ex:  # noqa: BLE001
            it["typed"] = f"FAIL not reachable: {ex}"[:200]
            res["items"].append(it)
            continue
        it["loaded_class"] = type(obj).__name__
        bad = []
        for k, v in (e.get("fields") or {}).items():
            want = _parse_typed(v)
            got = getattr(obj, k, "<missing>")
            if not _num_eq(got, want):
                bad.append(f"{k}: typed {want!r} loaded {got!r}")
        it["typed"] = "ok" if not bad else "FAIL " + "; ".join(bad)
        # native: what a lab script instantiating the class would serialise
        try:
            typed = {k: _parse_typed(v) for k, v in (e.get("fields") or {}).items()}
            fresh = type(obj)(**typed)
            native = fresh.to_dict()
            sm_cmp = {k: v for k, v in sm_json.items()}
            diff = []
            for k in sorted(set(native) | set(sm_cmp)):
                a, b = native.get(k, "<absent>"), sm_cmp.get(k, "<absent>")
                if k == "__class__":
                    if str(a).rsplit(".", 1)[-1] != str(b).rsplit(".", 1)[-1]:
                        diff.append(f"__class__ {a} vs {b}")
                    elif a != b:
                        diff.append(f"__class__ path {a} (native) vs {b} (SM)")
                    continue
                if not _num_eq(a, b):
                    diff.append(f"{k}: native {a!r} SM {b!r}")
            it["native"] = "same" if not diff else "DIFF " + "; ".join(diff)
        except Exception as ex:  # noqa: BLE001
            it["native"] = f"n/a ({type(ex).__name__}: {ex})"[:240]
        try:
            it["resave"] = "same" if _walk(saved, path) == sm_json else \
                "DIFF " + json.dumps(_walk(saved, path))[:240]
        except Exception as ex:  # noqa: BLE001
            it["resave"] = f"FAIL {ex}"[:200]
        # config: channel operations become config pulses "<element>.<op>.pulse"
        if cfg is not None:
            try:
                ch = obj.channel
                pname = obj.pulse_name if hasattr(obj, "pulse_name") else None
                in_cfg = bool(pname and pname in cfg.get("pulses", {}))
                it["config"] = f"ok ({pname})" if in_cfg else \
                    f"MISSING (channel={getattr(ch, 'name', None)}, pulse_name={pname})"
            except Exception as ex:  # noqa: BLE001
                it["config"] = f"no channel: {type(ex).__name__}: {ex}"[:240]
        if e.get("gate") or ".macros." in path:
            gate_path = e.get("gate") or path.rsplit(".", 1)[0]
            try:
                gate = _walk(machine, gate_path)
                slot = path.rsplit(".", 1)[-1]
                pair = gate.qubit_pair
                if slot == "coupler_flux_pulse":
                    cp = getattr(pair, "coupler", None)
                    if cp is None:
                        it["macro"] = "FAIL the pair has no coupler: apply() calls qubit_pair.coupler.play on None"
                    else:
                        lab = gate.coupler_flux_pulse_label
                        it["macro"] = ("ok" if lab in cp.operations else
                                       f"FAIL coupler plays {lab!r}, not in coupler.operations")
                else:
                    mq = pair.qubit_control if pair.moving_qubit == "control" else pair.qubit_target
                    lab = gate.flux_pulse_qubit_label
                    it["macro"] = ("ok" if lab in mq.z.operations else
                                   f"FAIL apply() plays {mq.name}.z {lab!r}, which is not in its operations")
            except Exception as ex:  # noqa: BLE001
                it["macro"] = f"FAIL {type(ex).__name__}: {ex}"[:240]
        res["items"].append(it)
    for e in exp:
        if "gcz" in e:
            pid = e["gcz"]
            it = {"gcz": pid}
            try:
                pair = machine.qubit_pairs[pid]
                for g in ("cz_gaussian_unipolar", "cz_gaussian_bipolar"):
                    gate = pair.macros[g]
                    mq = pair.qubit_control if pair.moving_qubit == "control" else pair.qubit_target
                    lab = gate.flux_pulse_qubit_label
                    it[g] = ("ok" if lab in mq.z.operations else f"FAIL {lab!r} not on {mq.name}.z")
                    p = gate.flux_pulse_qubit
                    it[g + ".padding_length"] = getattr(p, "padding_length", None)
                    it[g + ".gaussian_filter_frequency_mhz"] = getattr(p, "gaussian_filter_frequency_mhz", None)
            except Exception as ex:  # noqa: BLE001
                it["err"] = f"{type(ex).__name__}: {ex}"[:240]
            res["items"].append(it)
    txt = json.dumps(res, indent=1, default=str)
    print(txt)
    if out_path:
        Path(out_path).write_text(txt, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
