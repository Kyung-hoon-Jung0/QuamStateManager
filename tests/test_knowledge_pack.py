"""The domain-knowledge pack (docs/129): the shipped res-vs-power manual
loads clean, the Clause-B lint refuses absolute-scale rules, overlays are
additive-only, and the md/json pair cannot drift on case ids.

The pack encodes the expert review of 2026-08-21: F2 fallback writes are
auto-reverted, F3 geometry overrides fit labels, N3 adopts the dressed
frequency only, and P1-P3 live in a provisional registry expected to change.
"""
from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.core.autofit import knowledge

_ROOT = Path(__file__).resolve().parent.parent
_FAM = "resonator_spectroscopy_vs_power"


class TestShippedPack:
    def test_loads_with_zero_lint_drops(self):
        pack = knowledge.load_family(_FAM)
        assert pack is not None
        assert pack["lint_dropped"] == [], \
            "the shipped manual must itself satisfy Clause-B"
        assert len(pack["cases"]) >= 21

    def test_the_expert_decisions_are_encoded(self):
        pack = knowledge.load_family(_FAM)
        f2 = knowledge.case_by_id(pack, "F2")
        assert "AUTO-REVERT" in f2["prescription"]
        f3 = knowledge.case_by_id(pack, "F3")
        assert "geometry" in f3["prescription"] and "overrides" in f3["prescription"]
        n3 = knowledge.case_by_id(pack, "N3")
        assert "DRESSED frequency only" in n3["prescription"]

    def test_provisional_registry_p1_p3(self):
        pack = knowledge.load_family(_FAM)
        reg = {p["id"] for p in pack["provisional_registry"]}
        assert reg == {"P1", "P2", "P3"}
        for cid in ("F6", "N7", "N2"):
            assert knowledge.case_by_id(pack, cid)["status"] == "provisional"

    def test_spur_lock_exemplar_is_excluded_from_verification(self):
        pack = knowledge.load_family(_FAM)
        er0 = [e for e in pack["edge_case_references"] if e["id"] == "ER-0"]
        assert er0 and er0[0]["excluded_from_verification"] is True
        others = [e for e in pack["edge_case_references"] if e["id"] != "ER-0"]
        assert others and all(not e["excluded_from_verification"] for e in others)

    def test_manual_hash_is_stable_and_content_bound(self):
        a = knowledge.load_family(_FAM)["manual_hash"]
        b = knowledge.load_family(_FAM)["manual_hash"]
        assert a == b and len(a) == 16

    def test_md_and_json_carry_the_same_case_ids(self):
        pack = json.loads(knowledge.pack_path(_FAM).read_text(encoding="utf-8"))
        md = (knowledge.pack_path(_FAM).parent / "cases.md").read_text(encoding="utf-8")
        for c in pack["cases"]:
            assert f"### {c['id']} -- " in md, f"{c['id']} missing from cases.md"

    def test_unknown_family_returns_none(self):
        assert knowledge.load_family("no_such_family") is None


class TestClauseBLint:
    def _pack_dir(self, tmp_path, cases):
        d = tmp_path / "v1" / "famX"
        d.mkdir(parents=True)
        (d / "cases.json").write_text(json.dumps(
            {"schema": "smknow/v1", "family": "famX", "cases": cases}),
            encoding="utf-8")
        return tmp_path

    def test_absolute_frequency_rule_is_dropped(self, tmp_path, monkeypatch):
        root = self._pack_dir(tmp_path, [
            {"id": "BAD", "geometry": "the dip sits at 5.9 GHz",
             "prescription": "accept"},
            {"id": "OK", "geometry": "the dip shifts with power",
             "prescription": "accept"},
        ])
        monkeypatch.setattr(knowledge, "_ROOT", root)
        pack = knowledge.load_family("famX")
        assert pack["lint_dropped"] == ["BAD"]
        assert [c["id"] for c in pack["cases"]] == ["OK"]

    def test_window_relative_size_is_dropped(self, tmp_path, monkeypatch):
        root = self._pack_dir(tmp_path, [
            {"id": "BAD2", "geometry": "the feature spans 30% of the window",
             "prescription": "accept"},
        ])
        monkeypatch.setattr(knowledge, "_ROOT", root)
        assert knowledge.load_family("famX")["cases"] == []

    def test_relative_db_language_survives(self, tmp_path, monkeypatch):
        root = self._pack_dir(tmp_path, [
            {"id": "OK2", "geometry": "the optimum sits a few dB below the knee",
             "prescription": "raise the floor by a bounded step"},
        ])
        monkeypatch.setattr(knowledge, "_ROOT", root)
        assert knowledge.load_family("famX")["lint_dropped"] == []


class TestOverlayAdditiveOnly:
    def _base(self, tmp_path, monkeypatch):
        d = tmp_path / "v1" / "famY"
        d.mkdir(parents=True)
        (d / "cases.json").write_text(json.dumps(
            {"schema": "smknow/v1", "family": "famY", "cases": [
                {"id": "C1", "geometry": "two branches", "prescription": "accept"},
            ]}), encoding="utf-8")
        monkeypatch.setattr(knowledge, "_ROOT", tmp_path)

    def test_new_id_is_added_and_marked(self, tmp_path, monkeypatch):
        self._base(tmp_path, monkeypatch)
        ov = tmp_path / "overlay" / "famY"
        ov.mkdir(parents=True)
        (ov / "cases.json").write_text(json.dumps({"cases": [
            {"id": "L1", "geometry": "a lab-specific braid pattern",
             "prescription": "repeat once"}]}), encoding="utf-8")
        pack = knowledge.load_family("famY", overlay_dir=tmp_path / "overlay")
        ids = [c["id"] for c in pack["cases"]]
        assert ids == ["C1", "L1"]
        assert knowledge.case_by_id(pack, "L1").get("overlay") is True

    def test_duplicate_id_is_refused_never_replaces(self, tmp_path, monkeypatch):
        self._base(tmp_path, monkeypatch)
        ov = tmp_path / "overlay" / "famY"
        ov.mkdir(parents=True)
        (ov / "cases.json").write_text(json.dumps({"cases": [
            {"id": "C1", "geometry": "REPLACED", "prescription": "REPLACED"}]}),
            encoding="utf-8")
        pack = knowledge.load_family("famY", overlay_dir=tmp_path / "overlay")
        assert pack["overlay_refused"] == ["C1"]
        assert knowledge.case_by_id(pack, "C1")["geometry"] == "two branches"

    def test_overlay_is_linted_too(self, tmp_path, monkeypatch):
        self._base(tmp_path, monkeypatch)
        ov = tmp_path / "overlay" / "famY"
        ov.mkdir(parents=True)
        (ov / "cases.json").write_text(json.dumps({"cases": [
            {"id": "L9", "geometry": "a dip at 4.5 GHz",
             "prescription": "accept"}]}), encoding="utf-8")
        pack = knowledge.load_family("famY", overlay_dir=tmp_path / "overlay")
        assert pack["overlay_refused"] == ["L9"]
        assert [c["id"] for c in pack["cases"]] == ["C1"]


class TestGoldenPathsClassifiedStore:
    def test_paths_exist_per_chip_and_the_exclusion_is_recorded(self):
        g = _ROOT / "tests" / "golden" / "calib_paths" / _FAM
        chips = sorted(p.name for p in g.iterdir() if p.is_dir())
        assert chips == ["AS_10TQ9TC", "CQT"]
        as9 = json.loads((g / "AS_10TQ9TC" / "2026-08-09.json").read_text(encoding="utf-8"))
        assert as9["schema"] == "smgolden/v1"
        assert any("#8" == e["run"] for e in as9.get("exclusions", [])), \
            "the spur-lock false accept must be excluded from the answer key"
        total = len(list(g.rglob("*.json")))
        assert total == 10
