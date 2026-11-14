"""Pulse class template, draft store and the lab-side draft runner.

Lab-env tests run the real runner in the lab interpreter and skip when it is
absent on this machine."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_template as T

LAB_PY = Path("C:/ProgramData/miniconda3/envs/gx_qdac_ff/python.exe")
lab = pytest.mark.skipif(not LAB_PY.is_file(), reason="lab env not on this machine")
F = T.FieldSpec
FORMULA_A = ("amplitude * np.where(t < edge_length, 0.5*(1-np.cos(np.pi*t/edge_length)),\n"
             "    np.where(t > length - edge_length, "
             "0.5*(1-np.cos(np.pi*(length-t)/edge_length)), 1.0))")


def spec_a():
    return T.ClassSpec("FlatCosineEdges", [F("amplitude", "float", 0.1, "V"),
                                           F("length", "int", 100, "ns"),
                                           F("edge_length", "int", 20, "ns")], FORMULA_A)


def spec_b():
    return T.ClassSpec("SquaredAmp", [F("amplitude", "float", 0.1, "V"),
                                      F("length", "int", 40, "ns")], "amplitude**2 * np.ones(n)")


# ------------------------------------------------------------- template text

def test_header_marks_tool_and_version():
    src = T.render(spec_a())
    assert src.startswith(f"{T.HEADER_MARK}, version {T.TEMPLATE_VERSION}.\n")
    p = T.parse(src)
    assert p.editable and p.made_by_template and p.version == T.TEMPLATE_VERSION


def test_parse_round_trips():
    s = spec_a()
    p = T.parse(T.render(s))
    assert p.spec.class_name == s.class_name
    assert [(f.name, f.type, f.default, f.unit) for f in p.spec.fields] == \
        [(f.name, f.type, f.default, f.unit) for f in s.fields]
    assert p.spec.formula == s.formula
    assert p.spec.parent == s.parent
    assert T.render(p.spec) == T.render(s)


def test_amplitude_field_required():
    s = spec_a()
    s.fields = [f for f in s.fields if f.name != "amplitude"]
    with pytest.raises(T.TemplateError, match="amplitude"):
        T.render(s)


@pytest.mark.parametrize("bad", [
    F("id", "str", "x"), F("amp lit", "float", 1.0), F("w", "float", "1"),
    F("k", "int", 1.5), F("b", "complex", 1.0)])
def test_invalid_fields_refused(bad):
    s = spec_a()
    s.fields.append(bad)
    with pytest.raises(T.TemplateError):
        T.render(s)


def test_bad_formula_refused():
    s = spec_a()
    s.formula = "amplitude *"
    with pytest.raises(T.TemplateError, match="formula"):
        T.render(s)


def _with_hand_edits(src: str) -> str:
    """Hand edits outside the region: a comment above, a method below."""
    src = src.replace("import numpy as np\n", "import numpy as np  # my note\nimport math\n")
    return src + "\n    def my_helper(self):\n        return math.pi\n"


def test_rewrite_changes_only_the_region():
    src = _with_hand_edits(T.render(spec_a()))
    s = T.parse(src).spec
    s.fields.append(F("rise_sigma", "float", 2.5, "ns"))
    new = T.rewrite(src, s)
    b, e = T.parse(src).region
    nb, ne = T.parse(new).region
    assert new[:b].encode() == src[:b].encode()
    assert new[ne:].encode() == src[e:].encode()
    assert new[nb:ne] != src[b:e]
    assert "rise_sigma: float = 2.5  # unit: ns" in new
    assert T.parse(new).editable


@pytest.mark.parametrize("breaker", [
    lambda s: s.replace(T.REGION_END + "\n", ""),
    lambda s: s.replace(T.REGION_BEGIN + "\n", ""),
    lambda s: s.replace(T.REGION_BEGIN + "\n", T.REGION_BEGIN + "\n" + T.REGION_BEGIN + "\n"),
    lambda s: s.replace("        return (\n", "        x = 1\n        return (\n"),
    lambda s: s.replace("    amplitude: float = 0.1", "    amplitude: float = 0.1 + 0"),
    lambda s: s + "\n" + T.REGION_END + "\n",
    lambda s: s.replace("    amplitude: float = 0.1  #", "    amplitude: float = 0.10  #"),
])
def test_broken_markers_are_code_view_only(breaker):
    src = breaker(T.render(spec_a()))
    p = T.parse(src)
    assert not p.editable
    assert p.reason == T.CODE_VIEW_ONLY_LINE
    with pytest.raises(T.TemplateError, match="code view"):
        T.rewrite(src, spec_a())


def test_proportional_verdict_template_rejects_hand_edit_warns():
    chk = {"proportional": False, "ratio_min": 4.0, "ratio_max": 4.0, "ratio_expected": 2.0}
    src = T.render(spec_b())
    v = T.proportional_verdict(src, chk)
    assert v["level"] == "reject" and "ratios seen 4 to 4, expected 2" in v["line"]
    hand = src.replace(T.REGION_END + "\n", "")
    assert T.proportional_verdict(hand, chk)["level"] == "warn"
    assert T.proportional_verdict(src, dict(chk, proportional=True))["level"] == "ok"
    assert T.proportional_verdict(src, None)["level"] == "unknown"


# ---------------------------------------------------------------- draft store

def test_draft_store_roundtrip_and_states(tmp_path):
    inst = tmp_path / "instance"
    src = T.render(spec_a())
    h = D.save_draft(inst, "chip-1", "FlatCosineEdges", src)
    assert h == hashlib.sha256(src.encode()).hexdigest()
    assert D.load_draft(inst, "chip-1", "FlatCosineEdges") == src.encode()
    assert [d["class_name"] for d in D.list_drafts(inst, "chip-1")] == ["FlatCosineEdges"]
    assert D.list_drafts(inst, "other") == []
    data = src.encode()
    target = tmp_path / "quam_config" / "flat_cosine_edges.py"
    st = D.save_state(data, target)
    assert (st["state"], st["line"]) == ("draft", "Draft, not saved")
    target.parent.mkdir()
    target.write_bytes(data)
    assert D.save_state(data, target)["line"] == f"Saved to {target}, same as draft"
    target.write_bytes(data + b"# x\n")
    assert D.save_state(data, target)["line"] == f"Saved to {target}, differs from draft"
    assert D.draft_path(inst, "chip-1", "FlatCosineEdges").is_relative_to(inst)


def test_scope_and_class_names_cannot_escape(tmp_path):
    p = D.draft_path(tmp_path, "../../evil", "X")
    assert p.is_relative_to(tmp_path / D.DRAFTS_DIR)
    with pytest.raises(ValueError):
        D.draft_path(tmp_path, "s", "../X")


def _fail_runner(err="SyntaxError: invalid syntax", line=7):
    return lambda py, req, timeout: {"ok": False, "error": err, "line": line}


def _good_runner(good, mutate=None):
    """A runner that reads the draft the way the lab runner does and returns
    the sha256 of the bytes it read; *mutate* changes the draft first."""
    def run(py, req, timeout):
        if mutate:
            mutate(Path(req["draft"]))
        data = Path(req["draft"]).read_bytes()
        return dict(good, sha256=hashlib.sha256(data).hexdigest())
    return run


def test_failed_preview_keeps_last_good(tmp_path):
    D.save_draft(tmp_path, "s", "FlatCosineEdges", T.render(spec_a()))
    good = {"ok": True, "samples": [0.0, 1.0], "n": 2, "fields": []}
    r = D.preview("py", tmp_path, "s", "FlatCosineEdges", runner=_good_runner(good))
    assert r["ok"] and r["previous"] is None
    before = (tmp_path / D.DRAFTS_DIR / "s" / "FlatCosineEdges" / D.PREVIEW_FILE).read_bytes()
    r = D.preview("py", tmp_path, "s", "FlatCosineEdges", runner=_fail_runner())
    assert not r["ok"] and r["line"] == 7 and "SyntaxError" in r["error"]
    assert r["previous"]["line"] == D.PREVIOUS_LINE
    assert r["previous"]["preview"]["samples"] == [0.0, 1.0]
    after = (tmp_path / D.DRAFTS_DIR / "s" / "FlatCosineEdges" / D.PREVIEW_FILE).read_bytes()
    assert after == before


def _tree_digest(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_broken_draft_never_touches_quam_config(tmp_path):
    qc = tmp_path / "quam_config"
    qc.mkdir()
    (qc / "__init__.py").write_text("")
    (qc / "existing_pulse.py").write_text("X = 1\n")
    before = _tree_digest(qc)
    inst = tmp_path / "instance"
    D.save_draft(inst, "s", "FlatCosineEdges", "def broken(:\n")
    D.preview("py", inst, "s", "FlatCosineEdges", runner=_fail_runner())
    D.list_drafts(inst, "s")
    D.save_state(D.load_draft(inst, "s", "FlatCosineEdges"), qc / "flat_cosine_edges.py")
    assert _tree_digest(qc) == before


# --------------------------------------------------------------------- safety

CORE = [Path(T.__file__), Path(D.__file__)]


@pytest.mark.parametrize("path", CORE, ids=lambda p: p.name)
def test_core_modules_never_run_a_draft(path):
    code = re.sub(r'"""[\s\S]*?"""', "", path.read_text(encoding="utf-8"))
    code = "\n".join(l.split("#", 1)[0] for l in code.splitlines())
    calls = re.findall(r"(?<![.\w])(exec|eval|compile|__import__)\s*\(", code)
    assert not calls, f"{path.name} calls {calls}"
    for word in ("importlib", "runpy", "spec_from_file_location", "import_module",
                 "exec_module", "subprocess.", "Popen"):
        assert word not in code, f"{path.name} uses {word}"


def test_side_effect_draft_does_not_run_in_sm(tmp_path):
    marker = tmp_path / "ran.txt"
    src = T.render(spec_a()).replace(
        "import numpy as np\n",
        f"import numpy as np\nopen({str(marker)!r}, 'w').write('ran')\n")
    inst = tmp_path / "instance"
    D.save_draft(inst, "s", "FlatCosineEdges", src)
    D.list_drafts(inst, "s")
    D.load_draft(inst, "s", "FlatCosineEdges")
    D.save_state(src.encode(), tmp_path / "x.py")
    T.parse(src)
    T.proportional_verdict(src, {"proportional": False})
    assert not marker.exists()


def test_preview_spawns_only_the_chosen_env(tmp_path, monkeypatch):
    from quam_state_manager.core import lab_worker
    seen = []

    def fake(argv, *, timeout, env_extra=None):
        seen.append(argv)
        return 1, "", "no"
    monkeypatch.setattr(lab_worker, "command", fake)
    py = tmp_path / "python.exe"
    py.write_text("")
    D.save_draft(tmp_path, "s", "FlatCosineEdges", T.render(spec_a()))
    D.preview(str(py), tmp_path, "s", "FlatCosineEdges")
    assert len(seen) == 1 and seen[0][0] == str(py)
    assert seen[0][2].endswith("run_pulse_draft.py")


# ------------------------------------------------------------------- lab env

def _lab_preview(inst, cls, **kw):
    return D.preview(str(LAB_PY), inst, "s", cls, **kw)


@lab
def test_lab_samples_proportional_and_sample_count(tmp_path):
    import numpy as np
    D.save_draft(tmp_path, "s", "FlatCosineEdges", T.render(spec_a()))
    for rate, n in ((1e9, 100), (2e9, 200)):
        r = _lab_preview(tmp_path, "FlatCosineEdges", params={"amplitude": 0.3}, sampling_rate=rate)
        assert r["ok"], r
        assert r["n"] == n
        dt = 1e9 / rate
        t = np.arange(n) * dt
        ref = 0.3 * np.where(t < 20, 0.5 * (1 - np.cos(np.pi * t / 20)),
                             np.where(t > 80, 0.5 * (1 - np.cos(np.pi * (100 - t) / 20)), 1.0))
        assert np.allclose(r["samples"], ref, rtol=0, atol=1e-15)
        assert r["proportional"]["proportional"] is True
    D.save_draft(tmp_path, "s", "SquaredAmp", T.render(spec_b()))
    r = _lab_preview(tmp_path, "SquaredAmp")
    assert r["ok"] and r["proportional"]["proportional"] is False
    assert r["proportional"]["ratio_min"] == pytest.approx(4.0)


@lab
def test_lab_hand_added_field_is_listed(tmp_path):
    src = T.render(spec_a()).replace(
        "    def waveform_function(self):",
        "    note_gain: float = 1.0\n\n    def waveform_function(self):")
    assert "note_gain" not in [f.name for f in T.parse(src).spec.fields]
    D.save_draft(tmp_path, "s", "FlatCosineEdges", src)
    r = _lab_preview(tmp_path, "FlatCosineEdges")
    assert r["ok"], r
    assert "note_gain" in [f["name"] for f in r["fields"]]


@lab
def test_lab_errors_report_line_and_keep_previous(tmp_path):
    D.save_draft(tmp_path, "s", "FlatCosineEdges", T.render(spec_a()))
    good = _lab_preview(tmp_path, "FlatCosineEdges")
    assert good["ok"]
    lines = T.render(spec_a()).split("\n")
    lines.insert(30, "    def broken(:")
    D.save_draft(tmp_path, "s", "FlatCosineEdges", "\n".join(lines))
    r = _lab_preview(tmp_path, "FlatCosineEdges")
    assert not r["ok"] and r["line"] == 31 and "SyntaxError" in r["error"]
    assert r["previous"]["preview"]["samples"] == good["samples"]
    src = T.render(spec_a()).replace("        return (\n",
                                     "        raise RuntimeError('boom')\n        return (\n")
    D.save_draft(tmp_path, "s", "FlatCosineEdges", src)
    r = _lab_preview(tmp_path, "FlatCosineEdges")
    want = [i + 1 for i, l in enumerate(src.split("\n")) if "boom" in l][0]
    assert not r["ok"] and r["line"] == want and "boom" in r["error"]
    assert r["previous"]["preview"]["samples"] == good["samples"]


@lab
def test_runner_cli_contract(tmp_path):
    from quam_state_manager.core.config_generator import _script_path
    D.save_draft(tmp_path, "s", "FlatCosineEdges", T.render(spec_a()))
    inp, out = tmp_path / "in.json", tmp_path / "out.json"
    inp.write_text(json.dumps({"draft": str(D.draft_path(tmp_path, "s", "FlatCosineEdges")),
                               "class_name": "FlatCosineEdges"}))
    subprocess.run([str(LAB_PY), "-B", str(_script_path("run_pulse_draft.py")),
                    "--in", str(inp), "--out", str(out)], timeout=300, capture_output=True)
    res = json.loads(out.read_text())
    assert res["status"] == "ok" and res["ok"] and res["n"] == 100


# ------------------------------------------------------------- review r1 pins

def test_marker_text_in_formula_refused_and_markers_are_whole_lines():
    s = spec_b()
    s.formula = "amplitude*np.ones(n)    # <<< SM template region end <<<"
    with pytest.raises(T.TemplateError, match="marker"):
        T.render(s)
    s.formula = "amplitude*np.ones(n)  " + T.REGION_BEGIN.strip()
    with pytest.raises(T.TemplateError, match="marker"):
        T.render(s)
    # a marker text inside another line is not a marker: the file stays editable
    src = T.render(spec_b()).replace(
        "import numpy as np\n", "import numpy as np\nX = 1  " + T.REGION_END + "\n")
    assert T.parse(src).editable
    src = src.replace("import numpy as np\n", "import numpy as np\nY = 2  " + T.REGION_BEGIN + "\n")
    assert T.parse(src).editable


@pytest.mark.parametrize("bad", [100.5, -3, 10, 18, 0])
def test_length_rule_refuses(bad):
    s = spec_b()
    s.fields[1] = F("length", "float" if isinstance(bad, float) else "int", bad, "ns")
    with pytest.raises(T.TemplateError) as ei:
        T.render(s)
    assert str(ei.value) == T.LENGTH_RULE_LINE


def test_length_rule_float_type_refused_even_when_whole():
    s = spec_b()
    s.fields[1] = F("length", "float", 40.0, "ns")
    with pytest.raises(T.TemplateError, match="whole number"):
        T.render(s)


@pytest.mark.parametrize("ok", [16, 40])
def test_length_rule_accepts(ok):
    s = spec_b()
    s.fields[1] = F("length", "int", ok, "ns")
    assert T.parse(T.render(s)).editable


def test_case_only_class_names_share_no_draft(tmp_path):
    D.save_draft(tmp_path, "s", "FooPulse", "a")
    with pytest.raises(D.DraftError, match="letter case"):
        D.save_draft(tmp_path, "s", "FOOPULSE", "b")
    assert D.load_draft(tmp_path, "s", "FooPulse") == b"a"
    assert [d["class_name"] for d in D.list_drafts(tmp_path, "s")] == ["FooPulse"]
    D.save_draft(tmp_path, "s", "FooPulse", "c")     # same name: an update
    assert D.load_draft(tmp_path, "s", "FooPulse") == b"c"


@pytest.mark.parametrize("name,match", [
    ("Nul", "device name"), ("con", "capital|device"), ("Com3", "device name"),
    ("Lpt9", "device name"), ("Pulse", "parent class"), ("SquarePulse", "known pulse class"),
    ("DRAGGAUSSIANPULSE", "known pulse class")])
def test_reserved_class_names_refused(name, match):
    s = spec_b()
    s.class_name = name
    with pytest.raises(T.TemplateError, match=match):
        T.render(s)


@pytest.mark.parametrize("name", ["Nul", "Con", "Aux", "Prn", "Com1", "Lpt1"])
def test_device_name_drafts_refused(tmp_path, name):
    with pytest.raises(D.DraftError, match="device name"):
        D.save_draft(tmp_path, "s", name, "x")
    assert not (tmp_path / D.DRAFTS_DIR / "s" / name).exists()


@pytest.mark.parametrize("kw", ["class", "None", "lambda", "for"])
def test_keyword_names_refused(kw):
    s = spec_b()
    s.fields.append(F(kw, "float", 1.0))
    with pytest.raises(T.TemplateError, match="not a valid Python name"):
        T.render(s)
    with pytest.raises(T.TemplateError, match="not a valid Python name"):
        T._check_ident(kw, "Class name")


def test_rewrite_refuses_a_rename():
    src = T.render(spec_b())
    s = T.parse(src).spec
    s.class_name = "OtherName"
    with pytest.raises(T.TemplateError, match="Renaming"):
        T.rewrite(src, s)


def test_preview_hash_mismatch_not_stored(tmp_path):
    D.save_draft(tmp_path, "s", "SquaredAmp", T.render(spec_b()))
    good = {"ok": True, "samples": [1.0], "n": 1, "fields": []}
    r = D.preview("py", tmp_path, "s", "SquaredAmp",
                  runner=_good_runner(good, mutate=lambda p: p.write_bytes(p.read_bytes() + b"#\n")))
    assert not r["ok"] and "changed" in r["error"]
    assert D.last_preview(tmp_path, "s", "SquaredAmp") is None
    r = D.preview("py", tmp_path, "s", "SquaredAmp", runner=lambda *a: dict(good))
    assert not r["ok"]       # a runner that names no hash is not trusted either
    assert D.last_preview(tmp_path, "s", "SquaredAmp") is None
    r = D.preview("py", tmp_path, "s", "SquaredAmp", runner=_good_runner(good))
    assert r["ok"] and D.last_preview(tmp_path, "s", "SquaredAmp")["samples"] == [1.0]


def test_zero_waveform_cannot_tell():
    src = T.render(spec_b())
    v = T.proportional_verdict(src, {"proportional": None, "zero_waveform": True})
    assert v["level"] == "unknown" and v["line"] == T.CANNOT_TELL_LINE
    assert "scales with amplitude." not in v["line"].split(":")[0]


@lab
def test_lab_zero_waveform_is_unknown(tmp_path):
    s = T.ClassSpec("ZeroWave", [F("amplitude", "float", 0.1, "V"),
                                 F("length", "int", 40, "ns")], "0 * amplitude * np.ones(n)")
    D.save_draft(tmp_path, "s", "ZeroWave", T.render(s))
    r = _lab_preview(tmp_path, "ZeroWave")
    assert r["ok"], r
    assert r["proportional"]["proportional"] is None and r["proportional"]["zero_waveform"]
    assert T.proportional_verdict(T.render(s), r["proportional"])["line"] == T.CANNOT_TELL_LINE


@pytest.mark.parametrize("py,want", [
    ("", "No Python environment is chosen for the preview."),
    ("Z:/nowhere/python.exe", "The chosen Python environment no longer exists.")])
def test_env_message_names_the_case(py, want):
    r = D._run_runner(py, {}, 5)
    assert not r["ok"] and r["error"] == want


# ------------------------------------------------- review round 2 pins

@pytest.mark.parametrize("where", ["class", "field"])
def test_trailing_newline_name_refused(where):
    s = spec_b()
    if where == "class":
        s.class_name = "Foo\n"
    else:
        s.fields.append(F("x\n", "float", 1.0))
    with pytest.raises(T.TemplateError, match="not a valid Python name"):
        T.render(s)


def test_trailing_newline_draft_name_refused_readably(tmp_path):
    with pytest.raises(D.DraftError, match="not a valid Python name"):
        D.save_draft(tmp_path, "s", "Foo\n", "x")
    assert not (tmp_path / D.DRAFTS_DIR).exists()


@pytest.mark.parametrize("unit", ["V\rimport os; os.system('echo hi')", "V\u2028x", "V\x0bx",
                                  "V\x85x"])
def test_unit_with_any_line_break_refused(unit):
    s = spec_b()
    s.fields[0] = F("amplitude", "float", 0.1, unit)
    with pytest.raises(T.TemplateError, match="unit must be one line"):
        T.render(s)


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\x85", "\x0c", "\r"])
def test_formula_with_other_line_breaks_refused(sep):
    s = spec_b()
    s.formula = f"amplitude * np.ones(n) if '{sep}' else 0"
    with pytest.raises(T.TemplateError, match="plain line breaks"):
        T.render(s)


def test_multi_line_formula_still_round_trips():
    p = T.parse(T.render(spec_a()))
    assert p.editable and p.spec.formula == FORMULA_A


@pytest.mark.parametrize("bad,want", [
    (F("k", "int", True), "is not an int"), (F("k", "int", 1.5), "is not an int"),
    (F("w", "float", "1"), "is not a float"), (F("b", "bool", 1), "is not a bool")])
def test_type_message_grammar(bad, want):
    s = spec_b()
    s.fields.append(bad)
    with pytest.raises(T.TemplateError, match=want):
        T.render(s)


def test_crlf_file_names_the_line_endings():
    src = T.render(spec_a()).replace("\n", "\r\n")
    p = T.parse(src)
    assert not p.editable
    assert p.reason == T.CRLF_LINE and "line endings" in p.reason
    with pytest.raises(T.TemplateError, match="code view"):
        T.rewrite(src, spec_a())


def test_length_rule_line_names_the_floor_honestly():
    line = T.LENGTH_RULE_LINE
    assert "QM config rule)" not in line
    assert "conservative" in line and "divisible by 4" in line
    assert " -- " not in line
