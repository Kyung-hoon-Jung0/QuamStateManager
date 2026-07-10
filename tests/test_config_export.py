"""Unit tests for core.config_export — the bare-QUA config.json / config.py emitter."""

import json

import pytest

from quam_state_manager.core import config_export


def _exec_module(src: str) -> dict:
    ns: dict = {}
    exec(compile(src, "config.py", "exec"), ns)
    return ns["config"]


SMALL_CONFIG = {
    "version": 1,
    "controllers": {"con1": {"analog_outputs": {"1": {"offset": 0.0}}}},
    "elements": {"q1.xy": {"intermediate_frequency": 100e6, "operations": {"x180": "p"}}},
    "pulses": {"p": {"operation": "control", "length": 40, "waveforms": {"I": "w"}}},
    "waveforms": {"w": {"type": "arbitrary", "samples": [0.0, -0.05, 0.1]}},
    "mixers": {"m": [{"intermediate_frequency": 100e6, "lo_frequency": 5e9,
                      "correction": [1.0, 0.0, 0.0, 1.0]}]},
}


class TestJsonBytes:
    def test_roundtrips_exactly(self):
        assert json.loads(config_export.json_bytes(SMALL_CONFIG).decode()) == SMALL_CONFIG

    def test_is_indented(self):
        assert b"\n" in config_export.json_bytes(SMALL_CONFIG)


class TestPythonModule:
    def test_small_config_is_readable_literal_and_roundtrips(self):
        src = config_export.python_module_source(SMALL_CONFIG, chip="qA")
        assert "config = (" in src          # literal branch, not json.loads embed
        assert "import json" not in src
        assert _exec_module(src) == SMALL_CONFIG

    def test_preserves_key_order(self):
        src = config_export.python_module_source(SMALL_CONFIG, chip="qA")
        # sort_dicts=False → config's meaningful order is kept (controllers first).
        assert src.index("'controllers'") < src.index("'elements'") < src.index("'pulses'")

    def test_header_has_open_qm_usage(self):
        src = config_export.python_module_source(SMALL_CONFIG, chip="myChip",
                                                 meta={"versions": {"quam": "0.4.0"}})
        assert "open_qm(config)" in src
        assert "myChip" in src
        assert "quam 0.4.0" in src

    def test_stale_flag_emits_warning(self):
        src = config_export.python_module_source(SMALL_CONFIG, stale=True)
        assert "WARNING" in src and "Regenerate" in src
        assert _exec_module(src) == SMALL_CONFIG   # still valid Python

    def test_not_stale_has_no_warning(self):
        assert "WARNING" not in config_export.python_module_source(SMALL_CONFIG)

    def test_large_config_uses_embedded_json_and_still_roundtrips(self, monkeypatch):
        # Force the large branch with a tiny threshold so the test is fast.
        monkeypatch.setattr(config_export, "_PY_LITERAL_MAX_BYTES", 10)
        src = config_export.python_module_source(SMALL_CONFIG, chip="qA")
        assert "json.loads(" in src
        assert _exec_module(src) == SMALL_CONFIG

    def test_embedded_branch_survives_adversarial_strings(self, monkeypatch):
        # Triple quotes, backslashes, newlines, a trailing backslash, unicode —
        # the repr()-embedding must reproduce them byte-for-byte.
        nasty = {
            "a": 'has """ triple and \\ backslash',
            "b": "line1\nline2\ttab",
            "c": "ends with backslash\\",
            "d": "µs · Ω · 中文",
            "e": {"nested": [1, 2, {"deep": 'q"u"o"t"e'}]},
        }
        monkeypatch.setattr(config_export, "_PY_LITERAL_MAX_BYTES", 1)
        src = config_export.python_module_source(nasty)
        assert _exec_module(src) == nasty

    def test_literal_branch_survives_adversarial_strings(self):
        # The small/literal (pprint) branch must be just as faithful.
        nasty = {"a": 'q"u"o"tes', "b": "back\\slash", "c": "µΩ中", "d": True, "e": None,
                 "f": [1.0, -2.5e-9, 3]}
        src = config_export.python_module_source(nasty)
        assert "config = (" in src
        assert _exec_module(src) == nasty

    def test_inf_nan_forces_embedded_branch_and_roundtrips(self):
        # pprint would render inf/nan as bare identifiers (exec → NameError), so a
        # non-finite config must fall to the json.loads embed even when small.
        import math
        cfg = {"controllers": {"con1": {"hi": float("inf"), "lo": float("-inf")}},
               "z": float("nan")}
        src = config_export.python_module_source(cfg)
        assert "json.loads(" in src and "config = (" not in src
        out = _exec_module(src)
        assert out["controllers"]["con1"]["hi"] == float("inf")
        assert out["controllers"]["con1"]["lo"] == float("-inf")
        assert math.isnan(out["z"])

    def test_size_boundary_uses_bytes_not_chars(self, monkeypatch):
        # char-count under the cap but byte-count over → must take the embed.
        monkeypatch.setattr(config_export, "_PY_LITERAL_MAX_BYTES", 40)
        cfg = {"k": "µ" * 30}   # ~39 JSON chars but ~69 UTF-8 bytes
        src = config_export.python_module_source(cfg)
        assert "json.loads(" in src
        assert _exec_module(src) == cfg


class TestSafeStem:
    @pytest.mark.parametrize("chip,expected", [
        ("qA1_chip", "qA1_chip"),
        ("chip with spaces", "chip_with_spaces"),
        ("bad/../slash", "bad_.._slash"),
        ("", "chip"),
        (None, "chip"),
        ("___", "chip"),
        ("...", "chip"),
    ])
    def test_stem(self, chip, expected):
        assert config_export.safe_stem(chip) == expected
