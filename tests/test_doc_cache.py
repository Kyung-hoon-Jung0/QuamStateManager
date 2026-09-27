"""doc_cache: a cached answer never differs from a cold recompute.

w7/livewrite. The live-write doors and sync polls read the live pair through
``doc_cache.read_pair``, which serves parses and canonical hashes from RAM
keyed on the SHA-256 of the bytes actually read. Pinned here:

* a randomized sequence of writers (SM's own atomic writer, an outside
  ``json.dump`` like quam's ``machine.save()``, same-size rewrites, rewrites
  that put the old mtime back) interleaved with reads -- every read's
  documents and content hash equal a cold ``json.load`` + ``content_hash``;
* a caller that mutates a shared document never gets it served again;
* bytes never seen before are parsed inside the read, so an invalid file
  fails there (``LiveFileError``), never later at first use;
* bytes already known are not parsed until a document is actually used.
"""
import json
import os
import random

import pytest

from quam_state_manager.core import doc_cache, safe_io, working_copy


def _cold(folder):
    s = json.loads((folder / "state.json").read_bytes())
    w = json.loads((folder / "wiring.json").read_bytes())
    return s, w, working_copy.content_hash(s, w)


def _doc(r, n):
    return {"qubits": {f"q{i}": {"f": r.choice([4.8e9, 5.0e9, 5, 5.0]),
                                 "T1": r.random(), "ok": r.choice([True, False, 1]),
                                 "xy": {"operations": {"x180": {"amplitude": r.random(),
                                                               "length": 40}}}}
                       for i in range(n)},
            "active_qubit_names": [f"q{i}" for i in range(n)]}


@pytest.mark.parametrize("seed", range(25))
def test_random_writers_and_readers_agree_with_a_cold_read(tmp_path, seed):
    r = random.Random(seed)
    state, wiring = _doc(r, 3), {"wiring": {"q0": {"xy": "#/ports/1"}}}
    safe_io.write_state_wiring(tmp_path, state, wiring)
    for step in range(30):
        op = r.random()
        if op < 0.3:                                   # SM's own write
            q = f"q{r.randrange(3)}"
            state["qubits"][q]["T1"] = r.choice([r.random(), 1, 1.0, True])
            safe_io.write_state_wiring(tmp_path, state, wiring)
        elif op < 0.5:                                 # quam machine.save()
            st = json.loads((tmp_path / "state.json").read_bytes())
            st["qubits"]["q1"]["f"] = r.choice([4.9e9, 4.9e9 + 1, 5])
            with open(tmp_path / "state.json", "w", encoding="utf-8") as f:
                json.dump(st, f, indent=4)
        elif op < 0.65:                                # same size, old mtime back
            p = tmp_path / "state.json"
            raw = p.read_bytes()
            old = os.stat(p)
            i = raw.find(b'"length": 40')
            if i >= 0:
                raw = raw[:i] + b'"length": 41' + raw[i + 12:]
                p.write_bytes(raw)
                os.utime(p, ns=(old.st_atime_ns, old.st_mtime_ns))
        cs, cw, ch = _cold(tmp_path)
        mode = r.choice(["shared", "hash", "fresh"])
        pair = doc_cache.read_pair(tmp_path, mode=mode)
        assert pair.content_hash() == ch, (seed, step, mode)
        if r.random() < 0.6:
            assert pair.state == cs and pair.wiring == cw, (seed, step, mode)
        # the store keeps whatever it last wrote in sync with the disk
        state = json.loads(json.dumps(cs))


def test_a_mutated_shared_document_is_never_served_again(tmp_path):
    safe_io.write_state_wiring(tmp_path, {"a": {"b": 1}}, {"w": 1})
    p1 = doc_cache.read_pair(tmp_path, mode="shared")
    p1.state["a"]["b"] = 999                  # a caller breaks the read-only rule
    p2 = doc_cache.read_pair(tmp_path, mode="shared")
    assert p2.state == {"a": {"b": 1}}


def test_unseen_invalid_bytes_fail_inside_the_read(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_bytes(b'{"a": 1')           # torn
    (tmp_path / "wiring.json").write_bytes(b"{}")
    monkeypatch.setattr(safe_io, "_READ_BACKOFF_S", 0.0)
    for mode in ("shared", "hash"):
        with pytest.raises(safe_io.LiveFileError):
            doc_cache.read_pair(tmp_path, mode=mode, attempts=1)


def test_known_bytes_are_not_parsed_until_used(tmp_path, monkeypatch):
    safe_io.write_state_wiring(tmp_path, {"a": {"b": 1.5}}, {"w": [1, 2]})
    calls = {"n": 0}
    real = doc_cache.parse_bytes

    def counting(raw):
        calls["n"] += 1
        return real(raw)
    monkeypatch.setattr(doc_cache, "parse_bytes", counting)
    pair = doc_cache.read_pair(tmp_path, mode="shared")      # SM wrote these bytes
    h = pair.content_hash()
    assert calls["n"] == 0, "a hash-only use parsed the documents"
    assert pair.state == {"a": {"b": 1.5}} and calls["n"] == 1
    assert h == working_copy.content_hash({"a": {"b": 1.5}}, {"w": [1, 2]})


def test_a_fresh_document_is_the_callers_own(tmp_path):
    """mode="fresh" may be a marshal copy of the shared parse: mutating it
    must not reach the shared one, and it must equal a cold parse exactly."""
    safe_io.write_state_wiring(tmp_path, {"a": {"b": 1, "n": [1.0, 2]}}, {"w": 1})
    shared = doc_cache.read_pair(tmp_path, mode="shared")
    assert shared.state["a"]["b"] == 1                     # parsed + cached
    mine = doc_cache.read_pair(tmp_path, mode="fresh")
    assert mine.state == json.loads((tmp_path / "state.json").read_bytes())
    assert json.dumps(mine.state) == json.dumps(shared.state)
    mine.state["a"]["b"] = 2
    mine.state["a"]["n"].append(3)
    assert shared.state == {"a": {"b": 1, "n": [1.0, 2]}}, "the fresh doc aliases the shared one"
    again = doc_cache.read_pair(tmp_path, mode="shared")
    assert again.state == {"a": {"b": 1, "n": [1.0, 2]}}
    assert type(again.state["a"]["n"][0]) is float
