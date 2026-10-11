"""The Pulses page against the lab's own code (v1.3.0 pre-deployment check).

Two parts.

1. CONTRACT on a real chip copy (skips without the lab-map chip or an env
   that imports its lab classes): for a sample of every pulse class on the
   chip, the class's own ``calculate_waveform()`` -- run through
   ``generator/run_pulse_waveform.py`` in the lab env, with the references
   resolved by an INDEPENDENT resolver written here from quam's reference
   rules -- is the truth. SM's in-process preview matches it within 1e-9 V,
   SM's lab drawing matches it exactly (same script, same env: any
   difference is a marshalling bug), the row length is the truth length, and
   every parameter the inspector shows is the value the chip copy resolves
   to (an int shows no ``.0``, a reference shows its target's value).

2. PINS of the defects the full-chip run found (one id each, PU-01..09;
   synthetic chip, no env needed): the values SM hands a pulse class, the
   config op SM calls a slot's ground truth, the length column of a class
   SM cannot synthesize. They were strict xfails until v1.3.0 fixed them
   (core/pulse_refs: references read the way quam reads them); now they
   pass. The rig harness that ran every pulse of the chip lives outside the
   repository.

Env override for part 1: ``SM_LAB_TRUTH_PYTHON`` (a python executable).
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.archive_roots import lab_path, lab_value

_ROOT = Path(__file__).resolve().parents[1]
_KEY = "lab-G"
_SCRIPT = _ROOT / "quam_state_manager" / "generator" / "run_pulse_waveform.py"
_CATALOG_HOMES = ("quam.", "quam_builder.")


# ---------------------------------------------------------------------------
# an independent reference resolver (quam.utils.string_reference semantics)
# ---------------------------------------------------------------------------

def _is_ref(v) -> bool:
    return isinstance(v, str) and v.startswith(("#/", "#./", "#../"))


_MISSING = object()


def _get(root, segs):
    node = root
    for s in segs:
        if isinstance(node, dict) and s in node:
            node = node[s]
        elif isinstance(node, list) and s.isdigit() and int(s) < len(node):
            node = node[int(s)]
        else:
            return _MISSING
    return node


def _target(holder, ref):
    segs = [] if ref.startswith("#/") else list(holder)
    s = ref.lstrip("#/")
    while s:
        if s.startswith("../"):
            if not segs:
                return None
            segs, s = segs[:-1], s[3:]
        elif s.startswith("./"):
            s = s[2:]
        else:
            head, _, s = s.partition("/")
            if head:
                segs.append(head)
    return segs


def _resolve(root, holder, value):
    """(ok, target_segs, value): follow a reference held by the object at
    *holder*; a target value that is itself a reference is followed from the
    object holding it."""
    segs = None
    for _ in range(20):
        if not _is_ref(value):
            return True, segs, value
        segs = _target(holder, value)
        nxt = _get(root, segs) if segs is not None else _MISSING
        if nxt is _MISSING:
            return False, segs, value
        holder, value = segs[:-1], nxt
    return False, segs, value


def _pulses(merged):
    """Channel operations and pair-macro slots (the paths SM lists by name)."""
    out = []
    for qn, q in (merged.get("qubits") or {}).items():
        for ch in ("xy", "z", "resonator", "xy_detuned"):
            ops = ((q or {}).get(ch) or {}).get("operations") if isinstance(q, dict) else None
            for op, body in (ops or {}).items() if isinstance(ops, dict) else ():
                out.append((f"qubits.{qn}.{ch}.operations.{op}", body))
    for pn, p in (merged.get("qubit_pairs") or {}).items():
        for g, m in ((p or {}).get("macros") or {}).items():
            if not isinstance(m, dict):
                continue
            for slot in ("flux_pulse_qubit", "coupler_flux_pulse", "flux_pulse_target"):
                if isinstance(m.get(slot), dict):
                    out.append((f"qubit_pairs.{pn}.macros.{g}.{slot}", m[slot]))
    return out


def _resolved_params(merged, path, body):
    """Every field, references resolved; ``None`` when a reference cannot be
    followed through the JSON (a key the target does not store, another
    object's runtime property) -- such pulses are pinned in part 2."""
    segs = path.split(".")
    out = {}
    for k, v in body.items():
        if k == "__class__":
            continue
        if _is_ref(v):
            if v.startswith(("#./inferred", "#./default_")):
                out[k] = v           # the class resolves its own property
                continue
            ok, _t, val = _resolve(merged, segs, v)
            if not ok:
                return None
            out[k] = val
        else:
            out[k] = v
    return out


def _max_delta(traces, ti, tq):
    """max|SM - truth| over SM's (possibly decimated) plot traces."""
    n = len(ti)
    tq = tq if tq is not None else [0.0] * n
    by = {t["name"]: t for t in traces}
    worst = 0.0
    for name, truth in (("I", ti), ("Q", tq)):
        t = by.get(name)
        if t is None:
            worst = max([worst] + [abs(v) for v in truth if v is not None])
            continue
        assert t["x"][-1] <= n - 1 and (t["x"] != list(range(len(t["x"]))) or len(t["x"]) == n), \
            f"{name}: {len(t['x'])} samples vs truth {n}"
        for x, y in zip(t["x"], t["y"]):
            if y is None or truth[x] is None:
                assert y is None and truth[x] is None
                continue
            worst = max(worst, abs(y - truth[x]))
    return worst


# ---------------------------------------------------------------------------
# part 1: the contract on a real chip copy
# ---------------------------------------------------------------------------

def _chip_dir():
    d = lab_path(_KEY)
    return d if (d / "state.json").is_file() and (d / "wiring.json").is_file() else None


def _env_python(lab_modules):
    py = os.environ.get("SM_LAB_TRUTH_PYTHON")
    if not py:
        from quam_state_manager.core import config_generator as cg
        name = lab_value(_KEY, "env")
        py = next((e.get("python") for e in cg.discover_envs() if e.get("name") == name), None)
    if not py or not Path(py).is_file():
        return None
    code = "import quam.components.pulses" + "".join(f"; import {m}" for m in sorted(lab_modules))
    try:
        r = subprocess.run([py, "-c", code], capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    return py if r.returncode == 0 else None


@pytest.fixture(scope="module")
def lab_chip(tmp_path_factory):
    src = _chip_dir()
    if src is None:
        pytest.skip("the lab-map chip is not on this machine")
    tmp = tmp_path_factory.mktemp("labtruth")
    chip = tmp / "chip"
    chip.mkdir()
    for name in ("state.json", "wiring.json"):
        shutil.copy2(src / name, chip / name)
    merged = json.loads((chip / "state.json").read_text(encoding="utf-8"))
    merged.update(json.loads((chip / "wiring.json").read_text(encoding="utf-8")))
    rows = []
    for path, body in _pulses(merged):
        if not isinstance(body, dict) or not isinstance(body.get("__class__"), str):
            continue
        params = _resolved_params(merged, path, body)
        if params is not None:
            rows.append((path, body["__class__"], params))
    lab_modules = {q.rsplit(".", 1)[0] for _p, q, _x in rows if not q.startswith(_CATALOG_HOMES)}
    py = _env_python(lab_modules)
    if py is None:
        pytest.skip("no env on this machine imports the chip's lab pulse classes")
    # a sample: the first two pulses of every class
    by_cls: dict = {}
    for row in rows:
        by_cls.setdefault(row[1], [])
        if len(by_cls[row[1]]) < 2:
            by_cls[row[1]].append(row)
    sample = [r for v in by_cls.values() for r in v]
    f_in, f_out = tmp / "in.json", tmp / "out.json"
    f_in.write_text(json.dumps({"items": [{"qclass": q, "params": p} for _x, q, p in sample]}),
                    encoding="utf-8")
    r = subprocess.run([py, str(_SCRIPT), "--in", str(f_in), "--out", str(f_out)],
                       capture_output=True, timeout=600, env=dict(os.environ, PYTHONUTF8="1"))
    assert r.returncode == 0, r.stderr[-2000:]
    truth = json.loads(f_out.read_text(encoding="utf-8"))["items"]
    from quam_state_manager.core import config_generator
    from quam_state_manager.web.app import create_app
    mp = pytest.MonkeyPatch()
    mp.setattr(config_generator, "get_selected_env", lambda inst: py)
    app = create_app(testing=True, instance_path=str(tmp / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    yield {"client": c, "merged": merged, "sample": sample,
           "truth": {s[0]: t for s, t in zip(sample, truth)}}
    mp.undo()
    from quam_state_manager.core import lab_waveform
    lab_waveform.shutdown_workers()      # the warm worker this module started


def test_every_sampled_pulse_is_drawn_like_the_class_draws_it(lab_chip):
    c, bad = lab_chip["client"], []
    seen = set()
    for path, qclass, params in lab_chip["sample"]:
        t = lab_chip["truth"][path]
        assert t["ok"], (path, t.get("error"))
        seen.add(qclass)
        if qclass.startswith(_CATALOG_HOMES):
            j = c.post("/api/pulse/synth", json={"path": path}).get_json()
            if not j["ok"]:
                bad.append((path, "synth", j.get("error")))
                continue
            d = _max_delta(j["plot"]["traces"], t["i"], t["q"])
            if d > 1e-9 or j["plot"]["length"] != t["length"]:
                bad.append((path, "synth", d, j["plot"]["length"], t["length"]))
        else:
            j = c.post("/api/pulse/synth", json={"path": path}).get_json()
            if j.get("ok") or j.get("reason") != "unknown_class":
                bad.append((path, "a lab class drawn by SM's catalog", j.get("error")))
        # v1.3.0: a catalog class keeps its list fields too (PU-06, fixed)
        lab = c.post("/api/pulse/lab-waveform", json={"paths": [path]}).get_json()
        rec = (lab.get("results") or [{}])[0]
        if not rec.get("ok"):
            bad.append((path, "lab", rec.get("error")))
            continue
        d = _max_delta(rec["plot"]["traces"], t["i"], t["q"])
        if d != 0.0 or rec["plot"]["length"] != t["length"] or rec["dropped"] != t["dropped"]:
            bad.append((path, "lab", d, rec["plot"]["length"], t["length"], rec["dropped"]))
    assert len(seen) >= 3
    assert not bad, bad[:10]


_INPUT = re.compile(r'data-copy="([^"]+)">[^<]*</code>.*?(?:<input type="text" name="value"[^>]*?'
                    r'(?:data-target-path="([^"]*)"[^>]*?)?value="([^"]*)"|</tr>)', re.S)


def test_the_inspector_shows_what_the_chip_resolves_to(lab_chip):
    import html
    c, merged, bad = lab_chip["client"], lab_chip["merged"], []
    n_int = 0
    for path, _q, _p in lab_chip["sample"]:
        txt = c.get("/pulse/detail", query_string={"path": path}).get_data(as_text=True)
        body = _get(merged, path.split("."))
        for dot, tgt, val in _INPUT.findall(txt):
            if not dot.startswith(path + ".") or val == "" and tgt == "":
                continue
            key = dot[len(path) + 1:]
            raw = body.get(key, _MISSING)
            if raw is _MISSING or isinstance(raw, (list, dict)):
                continue
            val = html.unescape(val)
            if _is_ref(raw):
                ok, segs, want = _resolve(merged, path.split("."), raw)
                if not ok or isinstance(want, (list, dict)):
                    continue
                if html.unescape(tgt) != ".".join(segs):
                    bad.append((dot, "target", tgt, ".".join(segs)))
            else:
                want = raw
            if isinstance(want, int) and not isinstance(want, bool):
                n_int += 1
            if val != ("" if want is None else str(want)):
                bad.append((dot, val, want))
    assert n_int > 0
    assert not bad, bad[:10]


def test_the_row_length_is_the_class_length(lab_chip):
    c, bad = lab_chip["client"], []
    for path, qclass, _p in lab_chip["sample"]:
        if not qclass.startswith(_CATALOG_HOMES):
            # v1.3.0 (PU-05, fixed): a lab class's row shows the length its
            # own code reported, once it has drawn the pulse
            c.post("/api/pulse/lab-waveform", json={"paths": [path]})
        txt = c.get("/pulse/row", query_string={"path": path}).get_data(as_text=True)
        cells = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", txt, re.S)]
        want = lab_chip["truth"][path]["length"]
        if cells[6].split()[0] != str(want):
            bad.append((path, cells[6], want))
    assert not bad, bad[:10]


# ---------------------------------------------------------------------------
# part 2: pins of the defects (synthetic chip, no env)
# ---------------------------------------------------------------------------

LABC = "labpkg.flux.NZPulse"
LABL = "labpkg.flux.ListPulse"
GATE = "labpkg.flux.CZTwo"
CB = "quam.components.pulses._CosineBipolarPulse"
SLOT = "#/qubit_pairs/q1-q2/macros/czl/flux_pulse_qubit"


def _syn_state() -> dict:
    nz = {"__class__": LABC, "length": "#./inferred_length", "amplitude": 0.2,
          "flat_length": 32}
    ctl = dict(nz, tail_length="#../flux_pulse_target/lobe3_length")
    return {
        "__class__": "labpkg.root.Quam",
        "qubits": {
            "q1": {"id": "q1", "xy": {"operations": {}}, "z": {"operations": {
                # every field follows the macro slot; the slot stores no
                # `unipolar` (quam reads the class default there)
                "czl_q1_q2": {"__class__": LABC, "length": "#./inferred_length",
                              "amplitude": SLOT + "/amplitude",
                              "flat_length": SLOT + "/flat_length",
                              "unipolar": SLOT + "/unipolar"},
                "listed": {"__class__": LABL, "length": 4, "samples": [0.1, 0.2, 0.3, 0.4]},
                "park": {"__class__": "quam.components.pulses.WaveformPulse",
                         "waveform_I": [0.1, 0.2, 0.3, 0.4]},
                # smoothing_length -> another pulse's length, which is that
                # pulse's runtime property
                "cb_chain": {"__class__": CB, "length": "#./inferred_total_length",
                             "amplitude": 0.25, "flat_length": 40,
                             "smoothing_length": "#/qubit_pairs/q1-q2/macros/czb/flux_pulse_qubit/length",
                             "post_zero_padding_length": 0},
            }}},
            "q2": {"id": "q2", "xy": {"operations": {}}, "z": {"operations": {}}},
            "q5": {"id": "q5", "xy": {"operations": {}}, "z": {"operations": {}}},
        },
        "qubit_pairs": {
            "q1-q2": {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                      "macros": {
                          "czl": {"__class__": GATE, "flux_pulse_qubit": ctl,
                                  "flux_pulse_target": copy.deepcopy(nz)},
                          "czb": {"__class__": GATE, "flux_pulse_qubit": {
                              "__class__": CB, "length": "#./inferred_total_length",
                              "amplitude": 0.2, "flat_length": 8, "smoothing_length": 4,
                              "post_zero_padding_length": 0}}}},
            "q5-q1": {"qubit_control": "#/qubits/q5", "qubit_target": "#/qubits/q1",
                      "macros": {}},
        },
    }


class _Recorder:
    """Stands in for the lab worker: records what SM sends the class and
    answers with a 60-sample drawing."""

    def __init__(self):
        self.items = []

    def draw(self, py, items, spawn=True, **_kw):
        self.items.extend(items)
        return [{"ok": True, "i": [0.1] * 60, "q": None, "iq": False, "kind": "arbitrary",
                 "length": 60, "dropped": [], "warnings": [], "canonical": q}
                for q, _p in items]


@pytest.fixture
def syn(tmp_path, monkeypatch):
    from quam_state_manager.core import config_generator, lab_waveform, pulse_catalog
    from quam_state_manager.web.app import create_app
    from tests.test_pulses_routes import _make_wiring
    pulse_catalog.apply_env_overlay(None)
    pulse_catalog.apply_chip_classes({
        LABC: {"importable": True, "canonical": LABC, "bases": ["quam.components.pulses.Pulse"],
               "fields": {"amplitude": {"type": {"base": "float"}, "has_default": False},
                          "flat_length": {"type": {"base": "int"}, "has_default": False},
                          "unipolar": {"type": {"base": "bool"}, "has_default": True},
                          "tail_length": {"type": {"base": "float"}, "has_default": True}}},
        LABL: {"importable": True, "canonical": LABL, "bases": ["quam.components.pulses.Pulse"],
               "fields": {"samples": {"type": {"base": "list"}, "has_default": False}}}})
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_syn_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    rec = _Recorder()
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: sys.executable)
    monkeypatch.setattr(lab_waveform, "draw", rec.draw)
    c._app = app
    yield c, rec
    pulse_catalog.apply_chip_classes(None)


def _sent(c, rec, path):
    rec.items.clear()
    j = c.post("/api/pulse/lab-waveform", json={"paths": [path]}).get_json()
    assert j["ok"], j
    assert rec.items, "the class was never asked"
    return rec.items[-1][1]


# PU-01 (fixed in v1.3.0): was a strict xfail
@pytest.mark.parametrize("path", ["qubits.q1.z.operations.czl_q1_q2",
                                  "qubit_pairs.q1-q2.macros.czl.flux_pulse_qubit"])
def test_pu01_the_class_never_receives_a_raw_reference(syn, path):
    c, rec = syn
    params = _sent(c, rec, path)
    raw = {k: v for k, v in params.items()
           if _is_ref(v) and not v.startswith(("#./inferred", "#./default_"))}
    assert not raw, raw


# PU-07 (fixed in v1.3.0): was a strict xfail
def test_pu07_the_cosine_bipolar_chain_reaches_the_class_as_a_number(syn):
    c, rec = syn
    params = _sent(c, rec, "qubits.q1.z.operations.cb_chain")
    assert isinstance(params.get("smoothing_length"), (int, float)), params


def test_a_lab_class_list_field_reaches_the_class(syn):
    # the full-chip run asked: does a LAB class lose a list field on its way
    # to its own code? It does not (the unknown-class payload keeps it)
    c, rec = syn
    params = _sent(c, rec, "qubits.q1.z.operations.listed")
    assert params.get("samples") == [0.1, 0.2, 0.3, 0.4], params


# PU-06 (fixed in v1.3.0): was a strict xfail
def test_pu06_a_catalog_class_list_field_reaches_the_class(syn):
    c, rec = syn
    params = _sent(c, rec, "qubits.q1.z.operations.park")
    assert params.get("waveform_I") == [0.1, 0.2, 0.3, 0.4], params


# PU-02 (fixed in v1.3.0): was a strict xfail
def test_pu02_a_class_default_reference_is_not_called_unresolvable(syn):
    c, _rec = syn
    path = "qubits.q1.z.operations.czl_q1_q2"
    txt = c.get("/pulse/detail", query_string={"path": path}).get_data(as_text=True)
    row = txt[txt.index(f'data-copy="{path}.unipolar"'):]
    row = row[:row.index("</tr>")]
    assert 'title="Unresolvable pointer"' not in row


# PU-03 (fixed in v1.3.0): was a strict xfail
def test_pu03_preview_follows_a_reference_to_a_runtime_length(syn):
    c, _rec = syn
    j = c.post("/api/pulse/synth", json={"path": "qubits.q1.z.operations.cb_chain"}).get_json()
    assert j["ok"], j.get("error")


# PU-05 (fixed in v1.3.0): was a strict xfail
def test_pu05_row_length_is_the_length_the_class_reports(syn):
    c, _rec = syn
    path = "qubits.q1.z.operations.czl_q1_q2"
    c.post("/api/pulse/lab-waveform", json={"paths": [path]})      # the class has drawn it
    txt = c.get("/pulse/row", query_string={"path": path}).get_data(as_text=True)
    cells = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", txt, re.S)]
    assert cells[6].split()[0] == "60", cells[6]


def _gt_config(*ops):
    """A generated-config shape: element -> operations -> pulse -> waveform."""
    cfg = {"elements": {}, "pulses": {}, "waveforms": {}}
    for elem, op, n, v in ops:
        pn, wn = f"{elem}.{op}.pulse", f"{elem}.{op}.wf"
        cfg["elements"].setdefault(elem, {"operations": {}})["operations"][op] = pn
        cfg["pulses"][pn] = {"operation": "control", "length": n, "waveforms": {"single": wn}}
        cfg["waveforms"][wn] = {"type": "constant", "sample": v}
    return cfg


# PU-04 (fixed in v1.3.0): was a strict xfail
def test_pu04_a_slot_ground_truth_is_never_another_pairs_op(syn):
    from quam_state_manager.web import routes
    c, _rec = syn
    store = next(iter(c._app.config["contexts"].values()))["store"]
    # the only config op whose name starts with the gate and names a pair
    # qubit is pair q5-q1's own pulse on q5's line
    cfg = _gt_config(("q5.z", "czb_pulse_q1", 16, 0.7))
    elem, _op = routes._config_op_for_pulse_path(
        cfg, "qubit_pairs.q1-q2.macros.czb.flux_pulse_qubit", store.merged)
    assert elem is None or elem.split(".")[0] in ("q1", "q2"), elem


# PU-09 (fixed in v1.3.0): was a strict xfail
def test_pu09_not_matched_does_not_promise_a_regenerate(syn):
    c, _rec = syn
    store = next(iter(c._app.config["contexts"].values()))["store"]
    # each line plays several pairs' mirrors, as on a real chip
    store.generated_config = _gt_config(("q1.z", "czl_q1_q2", 60, 0.2),
                                        ("q1.z", "czl_q1_q5", 60, 0.3),
                                        ("q2.z", "czl_q2_q1", 60, 0.2))
    store.generated_config_meta = {"at": "now"}
    j = c.get("/api/pulse/ground-truth",
              query_string={"path": "qubit_pairs.q1-q2.macros.czl.flux_pulse_qubit"}).get_json()
    # the config carries this slot's mirror op (every field points into the
    # slot): either it is matched, or the text does not send the user to a
    # regenerate that cannot change the answer
    assert j.get("ok") or "Regenerating the config settles" not in (j.get("error") or ""), j


# PU-08 (fixed in v1.3.0): was a strict xfail
def test_pu08_a_constant_whose_length_does_not_resolve_is_not_drawn_silently():
    pytest.importorskip("quam.components.pulses")
    sys.path.insert(0, str(_SCRIPT.parent))
    try:
        import run_pulse_waveform as rpw
    finally:
        sys.path.remove(str(_SCRIPT.parent))
    res = rpw._draw_one("quam.components.pulses.SquarePulse",
                        {"length": "#./inferred_total_length", "amplitude": 0.3}, 1000)
    assert not res["ok"] or res["warnings"], res


# ---------------------------------------------------------------------------
# part 3: the fixes, end to end (v1.3.0)
# ---------------------------------------------------------------------------

def test_pu02_a_class_default_reference_shows_the_default_and_an_edit_keeps_the_link(syn):
    """When the env's probe knows the default, the box shows it; a value-mode
    edit writes it AT the target (the slot), so the mirror stays a mirror."""
    from quam_state_manager.core import pulse_catalog
    c, _rec = syn
    pulse_catalog.apply_chip_classes({LABC: {
        "importable": True, "canonical": LABC, "bases": ["quam.components.pulses.Pulse"],
        "fields": {"amplitude": {"type": {"base": "float"}, "has_default": False},
                   "flat_length": {"type": {"base": "int"}, "has_default": False},
                   "unipolar": {"type": {"base": "bool"}, "has_default": True,
                                "default": False, "default_repr": None},
                   "tail_length": {"type": {"base": "float"}, "has_default": True}}}})
    # a background probe of the selected env (this interpreter, which cannot
    # import the synthetic lab package) must not swap the inventory mid-test
    hold = pytest.MonkeyPatch()
    hold.setattr(pulse_catalog, "apply_chip_classes", lambda classes: None)
    try:
        _pu02_default_through_mirror(c)
    finally:
        hold.undo()


def _pu02_default_through_mirror(c):
    path = "qubits.q1.z.operations.czl_q1_q2"
    txt = c.get("/pulse/detail", query_string={"path": path}).get_data(as_text=True)
    row = txt[txt.index(f'data-copy="{path}.unipolar"'):]
    row = row[:row.index("</tr>")]
    assert 'value="False"' in row and "class default" in row, row[:600]
    assert 'value="#/' not in row
    r = c.post("/pulse/edit", data={"path": path, "dot_path": f"{path}.unipolar",
                                    "mode": "value", "value": "true"})
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    store = next(iter(c._app.config["contexts"].values()))["store"]
    assert store.get_value("qubit_pairs.q1-q2.macros.czl.flux_pulse_qubit.unipolar") is True
    assert store.get_value(f"{path}.unipolar") == SLOT + "/unipolar"   # still linked


def test_pu03_a_computed_reference_is_shown_as_its_value_not_edited_through(syn):
    c, _rec = syn
    path = "qubits.q1.z.operations.cb_chain"
    txt = c.get("/pulse/detail", query_string={"path": path}).get_data(as_text=True)
    row = txt[txt.index(f'data-copy="{path}.smoothing_length"'):]
    row = row[:row.index("</tr>")]
    # czb's own length: ceil((8 + 4 + 0) / 4) * 4 = 12
    assert 'data-ref-how="computed">12' in row, row[:600]
    assert 'name="value"' not in row                  # no box writing through it
    assert "Unresolvable pointer" not in row
    # the row Length is the class's: ceil((40 + 12 + 0) / 4) * 4 = 52
    rtxt = c.get("/pulse/row", query_string={"path": path}).get_data(as_text=True)
    cells = [re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", rtxt, re.S)]
    assert cells[6] == "52", cells


def test_pu05_the_length_cell_says_why_before_the_class_has_drawn(syn):
    c, _rec = syn
    path = "qubits.q1.z.operations.czl_q1_q2"
    from quam_state_manager.core import lab_waveform

    def not_drawn(py, items, spawn=True, **_kw):
        return [{"ok": False, "reason": "not-drawn", "error": "not drawn yet"} for _ in items]
    mp = pytest.MonkeyPatch()
    mp.setattr(lab_waveform, "draw", not_drawn)
    try:
        txt = c.get("/pulse/row", query_string={"path": path}).get_data(as_text=True)
    finally:
        mp.undo()
    cell = re.findall(r"<td[^>]*>.*?</td>", txt, re.S)[6]
    assert re.sub(r"<[^>]+>", "", cell).strip() == "-"
    assert "computed by the class" in cell and "own code" in cell, cell


def test_pu09_a_slot_with_an_exact_copy_in_the_config_matches_that_copy(syn):
    from quam_state_manager.web import routes
    c, _rec = syn
    store = next(iter(c._app.config["contexts"].values()))["store"]
    slot = "qubit_pairs.q1-q2.macros.czl.flux_pulse_target"
    sp = "#/" + slot.replace(".", "/")
    store.merged["qubits"]["q2"]["z"]["operations"]["czl_q2_q1"] = {
        "__class__": LABC, "length": "#./inferred_length",
        "amplitude": sp + "/amplitude", "flat_length": sp + "/flat_length", "id": "x"}
    cfg = _gt_config(("q2.z", "czl_q2_q1", 60, 0.2), ("q5.z", "czl_q5_q2", 60, 0.9))
    assert routes._config_op_for_pulse_path(cfg, slot, store.merged) == ("q2.z", "czl_q2_q1")
    got = routes._slot_config_match(cfg, slot, store.merged)
    assert got["mirror"] == "qubits.q2.z.operations.czl_q2_q1"
    # an op of the SAME name on the slot's own line that is NOT a copy (its
    # own literal amplitude) is another pulse: never matched (PU-04)
    store.merged["qubits"]["q2"]["z"]["operations"]["czl_q2_q1"]["amplitude"] = 0.21
    assert routes._config_op_for_pulse_path(cfg, slot, store.merged) == (None, None)
    # and the not-matched sentence offers a regenerate only for a copy the
    # config predates
    store.merged["qubits"]["q2"]["z"]["operations"]["czl_q2_q1"]["amplitude"] = sp + "/amplitude"
    store.generated_config = _gt_config(("q5.z", "czl_q5_q2", 60, 0.9))
    store.generated_config_meta = {"at": "now"}
    j = c.get("/api/pulse/ground-truth", query_string={"path": slot}).get_json()
    assert j["status"] == "not-matched" and j["mirror"] == "qubits.q2.z.operations.czl_q2_q1"
    assert "Regenerate to include it" in j["error"]


LAB2_MOD = "smlab_twoline.flux"
LAB2 = LAB2_MOD + ".TwoLinePulse"
LAB2_SRC = '''"""A synthetic two-line CZ flux pulse: a lab class SM does not transcribe."""
import math
import numpy as np
from quam.components.pulses import Pulse
from quam.core import quam_dataclass


@quam_dataclass
class TwoLinePulse(Pulse):
    amplitude: float
    flat_length: int
    unipolar: bool = False
    lobe3_max_v: float = 0.05
    tail_length: int = 0
    length: int = "#./inferred_length"

    @property
    def lobe3_length(self) -> int:
        if self.unipolar is not True:
            return 0
        return 4 * math.ceil(abs(self.amplitude) * self.flat_length / self.lobe3_max_v / 4)

    @property
    def inferred_length(self) -> int:
        return self.flat_length + int(self.tail_length) + self.lobe3_length

    def waveform_function(self):
        core = [self.amplitude] * self.flat_length
        if self.unipolar is True:
            n = self.lobe3_length
            core += [-self.amplitude * self.flat_length / n] * n
        return np.array(core + [0.0] * int(self.tail_length))
'''


def _two_line_state():
    slot = "#/qubit_pairs/q1-q2/macros/cz2/flux_pulse_qubit"
    mirror = {"__class__": LAB2, "length": "#./inferred_length",
              "amplitude": slot + "/amplitude", "flat_length": slot + "/flat_length",
              "unipolar": slot + "/unipolar", "tail_length": slot + "/tail_length"}
    return {
        "__class__": "labpkg.root.Quam",
        "qubits": {
            "q1": {"id": "q1", "xy": {"operations": {}},
                   "z": {"operations": {"cz2_q1_q2": mirror}}},
            "q2": {"id": "q2", "xy": {"operations": {}}, "z": {"operations": {}}},
        },
        "qubit_pairs": {"q1-q2": {
            "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
            "macros": {"cz2": {
                "__class__": "quam_builder.architecture.superconducting.custom_gates.CZGate",
                "flux_pulse_qubit": {"__class__": LAB2, "length": "#./inferred_length",
                                     "amplitude": 0.2, "flat_length": 16,
                                     "tail_length": "#../flux_pulse_target/lobe3_length"},
                "flux_pulse_target": {"__class__": LAB2, "length": "#./inferred_length",
                                      "amplitude": -0.1, "flat_length": 16,
                                      "unipolar": True}}}}},
    }


@pytest.fixture
def two_line(tmp_path, monkeypatch):
    """A lab package on PYTHONPATH, its class drawn by the REAL worker script
    in this interpreter (no stand-in): the edit check runs the class's code."""
    pytest.importorskip("quam.components.pulses")
    from quam_state_manager.core import config_generator, lab_waveform, pulse_catalog
    from quam_state_manager.web.app import create_app
    from tests.test_pulses_routes import _make_wiring
    root = tmp_path / "labpkg"
    pkg = root / "smlab_twoline"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "flux.py").write_text(LAB2_SRC, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(root))
    lab_waveform.shutdown_workers()
    lab_waveform.MEMO.clear()
    with lab_waveform._SLOT_LOCK:
        lab_waveform._SLOT_FILES.clear()
    pulse_catalog.apply_env_overlay(None)
    pulse_catalog.apply_chip_classes(None)
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_two_line_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: sys.executable)
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    c._app = app
    yield c
    lab_waveform.shutdown_workers()
    lab_waveform.MEMO.clear()
    pulse_catalog.apply_chip_classes(None)


def test_pu01_a_two_line_gate_is_drawn_by_its_class_and_its_edits_commit(two_line):
    """The control line's tail is the target line's lobe-3 length -- a
    property only the lab's class computes. The class gets the number its
    own code computes (never the reference string, which made int() raise
    and every edit of the gate a refusal)."""
    c = two_line
    want_tail = 4 * -(-int(0.1 * 16 / 0.05) // 4)               # 32
    for path in ("qubits.q1.z.operations.cz2_q1_q2",
                 "qubit_pairs.q1-q2.macros.cz2.flux_pulse_qubit"):
        j = c.post("/api/pulse/lab-waveform", json={"paths": [path]}).get_json()
        rec = j["results"][0]
        assert rec["ok"], (path, rec.get("error"))
        assert rec["plot"]["length"] == 16 + want_tail
        ys = rec["plot"]["traces"][0]["y"]
        assert ys[:16] == [0.2] * 16 and ys[16:] == [0.0] * want_tail
    slot = "qubit_pairs.q1-q2.macros.cz2.flux_pulse_qubit"
    r = c.post("/pulse/edit", data={"path": slot, "dot_path": slot + ".amplitude",
                                    "mode": "value", "value": "0.25"})
    assert r.status_code == 200, r.get_data(as_text=True)[:600]
    store = next(iter(c._app.config["contexts"].values()))["store"]
    assert store.get_value(slot + ".amplitude") == 0.25
    tgt = "qubit_pairs.q1-q2.macros.cz2.flux_pulse_target"
    r = c.post("/pulse/edit", data={"path": tgt, "dot_path": tgt + ".amplitude",
                                    "mode": "value", "value": "-0.12"})
    assert r.status_code == 200, r.get_data(as_text=True)[:600]
    assert store.get_value(tgt + ".amplitude") == -0.12
