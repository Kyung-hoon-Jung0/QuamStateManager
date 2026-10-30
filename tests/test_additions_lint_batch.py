"""Batch lint preserves the old per-index result with one scan of additions."""
import json
from copy import deepcopy
from statistics import median
from time import perf_counter
from quam_state_manager.core import additions
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def large_spec():
    qs = [f"q{i}" for i in range(1, 61)]
    pairs = [[qs[i], qs[i+1]] for i in range(59)] + [[qs[i], qs[i+2]] for i in range(41)]
    spec = {"qubits": qs, "qubit_pairs": pairs, "pair_gate": "cz_tunable",
            "lines": [{"element": q, "line": "flux"} for q in qs], "additions": []}
    for i, pair in enumerate(pairs):
        for role,q in zip(("high", "low"), pair):
            spec["additions"].append({"kind": "pulse", "target": f"qubits.{q}.z.operations",
                                      "name": f"probe_{role}_{i}", "class": "pkg.Pulse",
                                      "fields": {"length": 40, "amplitude": 0.1}})
        spec["additions"].append({"kind": "macro", "target": f"qubit_pairs.{additions.quam_pair_id(pair)}.macros",
                                  "name": "cz_probe", "class": "pkg.Gate", "fields": {}})
    return spec


def old_results(spec, indices):
    adds = list(spec.get("additions") or [])
    return [additions.lint_proposal(dict(spec, additions=adds[:i]+adds[i+1:]), adds[i], None)
            if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(adds)
            else {"ok": False, "errors": ["no such addition"], "warnings": []} for i in indices]


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_proposal_manifest", lambda *args: None)
    app = create_app(testing=True, instance_path=str(tmp_path))
    return app.test_client()


def test_mixed_batch_is_byte_identical_to_old_results(tmp_path, monkeypatch):
    import random
    client = make_client(tmp_path, monkeypatch)
    base = large_spec()
    base["qubits"] = base["qubits"][:4]
    base["qubit_pairs"] = [["q1", "q2"], ["q2", "q3"]]
    pool = base["additions"][:6] + [None, {},
        {"kind": "field", "target": "extras.flag", "value": True},
        {"kind": "field", "target": "qubits.q1.f_01"},
        {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_unipolar", "class": "pkg.Gate"},
        {"kind": "default_gate", "target": "qubit_pairs.q1-2.macros", "name": "cz", "to": "cz_probe"},
        {"kind": "default_gate", "target": "qubit_pairs.q1-2.macros", "name": "cz", "to": "absent"},
        {"kind": "pulse", "target": "bad", "name": "?", "class": "bad", "fields": []}]
    rng = random.Random(47)
    cases = [pool, pool[:6], pool[10:13], pool[10:12], [pool[11], pool[11]]]
    cases += [rng.choices(pool, k=rng.randrange(1, 16)) for _ in range(100)]
    for adds in cases:
        spec = dict(base, additions=deepcopy(adds))
        indices = list(range(len(adds))) + [True, -1, 999, "0"]
        want = json.dumps(old_results(spec, indices), sort_keys=True)
        response = client.post("/generate/additions/lint", json={"spec": spec, "indices": indices})
        assert response.status_code == 200
        assert json.dumps(response.json["results"], sort_keys=True) == want, adds


def test_batch_builds_one_index_and_never_rescans_per_addition(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    counts = {"shape_rows": 0, "structural": 0, "index": 0}
    shape, structural, index = additions._shape_errors, additions.structural_errors, additions.SpecIndex
    def shapes(adds):
        counts["shape_rows"] += len(adds)
        return shape(adds)
    def structures(spec):
        counts["structural"] += 1
        return structural(spec)
    class Index(index):
        def __init__(self, spec):
            counts["index"] += 1
            super().__init__(spec)
    monkeypatch.setattr(additions, "_shape_errors", shapes)
    monkeypatch.setattr(additions, "structural_errors", structures)
    monkeypatch.setattr(additions, "SpecIndex", Index)
    spec = large_spec()
    result = client.post("/generate/additions/lint", json={"spec": spec, "indices": list(range(300))})
    assert len(result.json["results"]) == 300
    assert counts == {"shape_rows": 300, "structural": 0, "index": 1}


def test_batch_lint_budget_60_qubits_100_pairs_300_additions(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    spec = large_spec()
    body = {"spec": spec, "indices": list(range(300))}
    client.post("/generate/additions/lint", json=body)
    times = []
    for _ in range(5):
        start = perf_counter()
        response = client.post("/generate/additions/lint", json=body)
        times.append(perf_counter() - start)
        assert response.status_code == 200 and len(response.json["results"]) == 300
    elapsed = median(times)
    print(f"BATCH_LINT_MEDIAN_MS={elapsed * 1000:.3f}")
    assert elapsed < 0.25, times
