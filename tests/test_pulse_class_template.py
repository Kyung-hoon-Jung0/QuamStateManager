"""Pulse class template, draft store and the lab-side draft runner.

Lab-env tests run the real runner in the lab interpreter named by
``SM_LAB_ENV_PYTHON`` and skip when it is not set or absent."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_template as T

LAB_PY = Path(os.environ.get("SM_LAB_ENV_PYTHON") or "__no_lab_env__")
lab = pytest.mark.skipif(not LAB_PY.is_file(), reason="SM_LAB_ENV_PYTHON not set or absent")
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
    lambda s: s + "\n" + T.REGION_END + "\n",
])
def test_broken_markers_are_code_view_only(breaker):
    src = breaker(T.render(spec_a()))
    p = T.parse(src)
    assert not p.editable
    assert p.reason == T.CODE_VIEW_ONLY_LINE
    with pytest.raises(T.TemplateError) as ei:
        T.rewrite(src, spec_a())
    assert str(ei.value) == T.CODE_VIEW_ONLY_LINE


@pytest.mark.parametrize("breaker", [
    lambda s: s.replace("        return (\n", "        x = 1\n        return (\n"),
    lambda s: s.replace("    amplitude: float = 0.1", "    amplitude: float = 0.1 + 0"),
    lambda s: s.replace("    amplitude: float = 0.1  #", "    amplitude: float = 0.10  #"),
    lambda s: s.replace("    amplitude: float = 0.1  #", "    amplitude: float =  0.1  #"),
])
def test_changed_region_names_the_region_not_the_markers(breaker):
    src = breaker(T.render(spec_a()))
    p = T.parse(src)
    assert not p.editable
    assert p.reason == T.REGION_CHANGED_LINE and p.reason != T.CODE_VIEW_ONLY_LINE
    assert "lines between the template markers" in p.reason
    with pytest.raises(T.TemplateError) as ei:
        T.rewrite(src, spec_a())
    assert str(ei.value) == T.REGION_CHANGED_LINE


def test_region_that_fails_validation_names_the_rule():
    src = T.render(spec_a()).replace("    length: int = 100", "    length: int = 42")
    p = T.parse(src)
    assert not p.editable
    assert p.reason.startswith(T.REGION_CHANGED_LINE) and T.LENGTH_RULE_LINE in p.reason
    with pytest.raises(T.TemplateError, match="divisible by 4"):
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
    assert D.save_state(data, target)["line"] == "Saved, same as the file"
    target.write_bytes(data + b"# x\n")
    assert D.save_state(data, target)["line"] == "Saved, differs from the file"
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
    with pytest.raises(T.TemplateError) as ei:
        T.rewrite(src, spec_a())
    assert str(ei.value) == T.CRLF_LINE


def test_length_rule_line_names_the_floor_honestly():
    line = T.LENGTH_RULE_LINE
    assert "QM config rule)" not in line
    assert "conservative" in line and "divisible by 4" in line
    assert " -- " not in line


# ------------------------------------------------- review round 3 pins

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_float_default_refused(bad):
    s = spec_b()
    s.fields.append(F("limit", "float", bad))
    with pytest.raises(T.TemplateError, match="'limit': the default must be a finite number"):
        T.render(s)


@pytest.mark.parametrize("ok", [1e-9, -0.5, 3])
def test_finite_float_defaults_still_render(ok):
    s = spec_b()
    s.fields.append(F("w", "float", ok))
    p = T.parse(T.render(s))
    assert p.editable and p.spec.fields[-1].default == float(ok)


def test_int_and_negative_defaults_still_render():
    s = spec_b()
    s.fields += [F("k", "int", -3), F("m", "int", 7)]
    assert T.parse(T.render(s)).editable


@pytest.mark.parametrize("name", ["calculate_waveform", "waveform_function", "_sm_formula",
                                  "_get_sampling_rate", "__init__", "__post_init__", "_x",
                                  "apply_to_config", "get_reference", "float", "int", "bool",
                                  "str", "play", "to_dict"])
def test_reserved_field_names_refused(name):
    s = spec_b()
    s.fields.append(F(name, "float", 1.0))
    with pytest.raises(T.TemplateError, match="is reserved"):
        T.render(s)


@pytest.mark.parametrize("name", ["amplitude_scale", "duration_ns", "waveform", "play_rate",
                                  "lengthy"])
def test_names_that_only_look_reserved_are_fine(name):
    s = spec_b()
    s.fields.append(F(name, "float", 1.0))
    assert T.parse(T.render(s)).editable


def test_template_names_come_from_the_template_text():
    names = T.reserved_field_names()
    assert {"_sm_formula", "waveform_function", "_get_sampling_rate"} <= names
    assert "length" not in names and "amplitude" not in names


@pytest.mark.skipif(not (LAB_PY.is_file() or __import__("importlib").util.find_spec("quam")),
                    reason="no env with quam")
def test_reserved_names_cover_the_installed_pulse_base():
    py = str(LAB_PY) if LAB_PY.is_file() else __import__("sys").executable
    out = subprocess.run(
        [py, "-c", "import importlib.util, sys\n"
                   f"spec = importlib.util.spec_from_file_location('tmpl', {str(Path(T.__file__))!r})\n"
                   "m = importlib.util.module_from_spec(spec)\n"
                   "sys.modules['tmpl'] = m\n"
                   "spec.loader.exec_module(m)\n"
                   "from quam.components.pulses import Pulse\n"
                   "print('NAMES=' + ','.join(sorted(m.public_attribute_names(Pulse))))"],
        capture_output=True, text=True, timeout=300)
    line = [x for x in out.stdout.splitlines() if x.startswith("NAMES=")]
    assert line, out.stderr[-2000:]
    base = set(line[0][6:].split(","))
    assert "length" not in base
    assert base <= T.PULSE_BASE_NAMES, sorted(base - T.PULSE_BASE_NAMES)
    assert base <= T.reserved_field_names(), sorted(base - T.reserved_field_names())


@pytest.mark.parametrize("formula", ["amplitude * np.ones(n) if '''a\nb''' else 0",
                                     'amplitude * np.ones(n) if """a\n  b""" else 0',
                                     "amplitude * np.ones(n) if f'''{n}\nb''' else 0"])
def test_formula_with_a_multi_line_string_refused(formula):
    s = spec_b()
    s.formula = formula
    with pytest.raises(T.TemplateError, match="string that spans lines"):
        T.render(s)


@pytest.mark.parametrize("formula", ["amplitude * np.ones(n) if 'a' else 0",
                                     "(amplitude * np.ones(n)  # a comment\n    + 0 * n)",
                                     "amplitude * np.ones(n) if f'{n}' else 0",
                                     "(amplitude\n  # 'quoted'\n  * np.ones(n))"])
def test_formula_with_one_line_strings_and_comments_round_trips(formula):
    s = spec_b()
    s.formula = formula
    p = T.parse(T.render(s))
    assert p.editable and p.spec.formula == formula


def test_zero_waveform_is_never_ok_even_if_called_proportional():
    src = T.render(spec_b())
    v = T.proportional_verdict(src, {"proportional": True, "zero_waveform": True})
    assert v["level"] == "unknown" and v["line"] == T.CANNOT_TELL_LINE


def test_str_default_holding_the_unit_mark_refused():
    s = spec_b()
    s.fields.append(F("label", "str", "a  # unit: b"))
    with pytest.raises(T.TemplateError, match="may not contain"):
        T.render(s)


def test_unit_with_hash_refused():
    s = spec_b()
    s.fields[0] = F("amplitude", "float", 0.1, "V # x")
    with pytest.raises(T.TemplateError, match="without '#'"):
        T.render(s)


def test_lf_rewrite_keeps_bytes_outside_the_region():
    src = T.render(spec_a())
    src = src.replace("import numpy as np\n", "import numpy as np\nimport math  # mine\n")
    src += "\n    def extra(self):\n        return 1\n"
    s = spec_a()
    s.fields.append(F("w", "float", 2.0, "ns"))
    out = T.rewrite(src, s)
    b, e = T.parse(src).region
    assert out[:b] == src[:b] and out.endswith(src[e:])
    assert T.parse(out).spec.fields[-1].name == "w"


def test_scope_with_a_trailing_newline_is_hashed(tmp_path):
    assert D._scope_key("chip\n").startswith("s_")
    D.save_draft(tmp_path, "chip\n", "SquaredAmp", "x")
    assert D.load_draft(tmp_path, "chip", "SquaredAmp") is None


@pytest.mark.parametrize("scope", ["nul", "NUL", "con.txt", "com1", "lpt9.x", "chip.", "a.",
                                   "CON", "nul.txt", "COM1"])
def test_windows_hostile_scope_is_hashed(tmp_path, scope):
    key = D._scope_key(scope)
    assert key.startswith("s_") and key != scope
    D.save_draft(tmp_path, scope, "SquaredAmp", "x")
    assert D.load_draft(tmp_path, scope, "SquaredAmp") == b"x"
    assert [d["class_name"] for d in D.list_drafts(tmp_path, scope)] == ["SquaredAmp"]
    plain = scope.rstrip(".")
    if plain and plain != scope:
        # "chip." never shares the folder of "chip"
        assert D.load_draft(tmp_path, plain, "SquaredAmp") is None


def test_device_name_scopes_differing_by_case_keep_distinct_folders():
    assert D._scope_key("nul") != D._scope_key("NUL")
    assert D._scope_key(" a").startswith("s_")
    assert D._scope_key("chip_a") == "chip_a"


@pytest.mark.parametrize("scope", ["chip-1", "null", "console", "chip.v2", "com10"])
def test_ordinary_scopes_kept_as_is(scope):
    assert D._scope_key(scope) == scope


def test_windows_line_ending_inside_the_region_is_named():
    src = T.render(spec_a()).replace("    length: int = 100  # unit: ns\n",
                                     "    length: int = 100  # unit: ns\r\n")
    p = T.parse(src)
    assert not p.editable and p.reason == T.CRLF_LINE


def test_windows_line_ending_on_the_end_marker_is_named():
    src = T.render(spec_a()).replace(T.REGION_END + "\n", T.REGION_END + "\r\n")
    p = T.parse(src)
    assert not p.editable and p.reason == T.CRLF_LINE


# ------------------------------------------------- review round 4 pins

def test_int_default_too_large_for_a_float_gives_the_finite_line():
    s = spec_b()
    s.fields.append(F("limit", "float", 10 ** 400))
    with pytest.raises(T.TemplateError, match="'limit': the default must be a finite number"):
        T.render(s)


def test_pulse_parent_needs_no_note_and_another_parent_says_so():
    assert T.validate(spec_b()) == []
    s = spec_b()
    s.parent = "lab_pulses.base.FancyPulse"
    notes = T.validate(s)
    assert notes == [T.PARENT_NOT_CHECKED_LINE.format(parent=s.parent)]
    assert "only against the quam Pulse base" in notes[0]
    # given the parent's names, nothing is left unchecked
    assert T.validate(s, parent_names=["ramp"]) == []


def test_field_shadowing_a_parent_attribute_refused():
    s = spec_b()
    s.parent = "lab_pulses.base.FancyPulse"
    s.fields.append(F("ramp", "float", 1.0))
    parent = type("FancyPulse", (), {"ramp": lambda self: 0, "length": 4})
    with pytest.raises(T.TemplateError, match="'ramp' is reserved: the parent class "
                                              "FancyPulse already has it"):
        T.validate(s, parent_names=T.public_attribute_names(parent))
    # length may be re-declared even when the parent names it
    s.fields.pop()
    assert T.validate(s, parent_names={"length", "ramp"}) == []


def test_public_attribute_names_is_the_one_derivation():
    class Base:
        width = 2.0
        length = 16

        def shape(self):
            return 0

        @property
        def area(self):
            return 1

        def _hidden(self):
            return 2
    assert T.public_attribute_names(Base) == frozenset({"width", "shape", "area"})


def test_scope_messages_never_say_scope():
    text = Path(D.__file__).read_text(encoding="utf-8")
    assert "Scope" not in text and "drafts cannot be kept" not in text


def test_cr_only_file_names_the_line_endings():
    src = T.render(spec_a()).replace("\n", "\r")
    assert "\n" not in src
    p = T.parse(src)
    assert not p.editable and p.reason == T.CRLF_LINE
    with pytest.raises(T.TemplateError) as ei:
        T.rewrite(src, spec_a())
    assert str(ei.value) == T.CRLF_LINE


def test_tokenize_failure_refuses_the_formula(monkeypatch):
    import tokenize

    def boom(readline):
        raise tokenize.TokenError("planted", (1, 0))
    monkeypatch.setattr(T.tokenize, "generate_tokens", boom)
    with pytest.raises(T.TemplateError, match="could not be checked for strings"):
        T.render(spec_b())
