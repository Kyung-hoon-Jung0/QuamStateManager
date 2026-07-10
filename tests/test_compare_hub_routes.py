"""Compare hub routes (/compare-hub*) — P1b (docs/49).

The hub is stateless + URL-canonical: the basket IS the query string
(``src=`` ref tokens + ``bucket`` + ``preset`` + ``ref`` + ``map``). These
tests cover the route layer only — alignment/tolerance/mapping semantics
are pinned by test_compare_engine.py, source resolution + pool isolation
by test_compare_sources.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


# ---------------------------------------------------------------------------
# Synth fixtures (same shape as test_compare_hub_p0.py).
# ---------------------------------------------------------------------------


def _make_state(f_01: float = 6.25e9, t1: float = 8834,
                n_qubits: int = 1) -> dict:
    qubits = {}
    for i in range(n_qubits):
        name = f"qA{i + 1}"
        qubits[name] = {
            "id": name,
            "f_01": f_01 + i * 1e8,
            "T1": t1,
            "T2ramsey": 1.5e-6,
            "anharmonicity": -220e6,
            "grid_location": f"{i},2",
            "xy": {
                "RF_frequency": f_01 + i * 1e8,
                "operations": {
                    "x180_DragCosine": {"amplitude": 0.115, "length": 40},
                },
            },
            "resonator": {
                "f_01": 7.64e9,
                "RF_frequency": 7.64e9,
                "operations": {
                    "readout": {"amplitude": 0.042, "length": 1000},
                },
            },
            "z": {"joint_offset": 0.081},
        }
    return {
        "qubits": qubits,
        "qubit_pairs": {},
        "active_qubit_names": list(qubits),
    }


def _make_wiring() -> dict:
    return {
        "wiring": {"qubits": {"qA1": {"xy": {"opx_output": "MW-FEM/1/2"}}}},
        "network": {"host": "10.1.1.18"},
    }


def _write_quam(folder: Path, *, f_01: float = 6.25e9, t1: float = 8834,
                n_qubits: int = 1, state: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(
        json.dumps(state or _make_state(f_01, t1, n_qubits), indent=2),
        encoding="utf-8")
    (folder / "wiring.json").write_text(
        json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return folder


@pytest.fixture
def env(tmp_path):
    """(client, chip_a_path, chip_b_path) — two 1-qubit chips, f_01 differs."""
    a = _write_quam(tmp_path / "ws" / "chipA" / "quam_state", f_01=6.25e9)
    b = _write_quam(tmp_path / "ws" / "chipB" / "quam_state", f_01=6.35e9)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client(), a, b


def _hub(c, *srcs, **params):
    qs = "&".join(f"src=ws:{s}" for s in srcs)
    for k, v in params.items():
        qs += f"&{k}={v}"
    return c.get(f"/compare-hub?{qs}")


# ===========================================================================
# Shell + basket
# ===========================================================================


class TestHubShell:
    def test_empty_hub_renders_pickers(self, env):
        c, _a, _b = env
        r = c.get("/compare-hub")
        assert r.status_code == 200
        assert b"cmp-pickers" in r.data
        assert b"cmp-headline" not in r.data

    def test_sidebar_has_compare_entry(self, env):
        c, _a, _b = env
        r = c.get("/compare-hub")   # full page (no HX-Request header)
        assert b'href="/compare-hub"' in r.data
        # P4: sidebar went 3→1 — the legacy entries are gone
        assert b'href="/chip-compare"' not in r.data
        assert b'href="/diff"' not in r.data

    def test_two_sources_without_bucket_prompts_for_context(self, env):
        """Axiom 2 — the context is user-declared, never auto-run."""
        c, a, b = env
        r = _hub(c, a, b)
        assert r.status_code == 200
        assert b"Pick the comparison context" in r.data
        assert b"cmp-headline" not in r.data

    def test_bad_token_is_honest_error_row_not_500(self, env):
        c, a, b = env
        r = c.get(f"/compare-hub?src=garbage&src=ws:{a}&src=ws:{b}&bucket=1")
        assert r.status_code == 200
        assert b"cmp-src-error" in r.data
        # the two valid sources still compare
        assert b"cmp-headline" in r.data

    def test_unreadable_folder_is_error_row(self, env, tmp_path):
        c, a, _b = env
        r = c.get(f"/compare-hub?src=ws:{tmp_path / 'nope'}&src=ws:{a}")
        assert r.status_code == 200
        assert b"cmp-src-error" in r.data

    def test_source_cap_is_eight(self, env):
        c, a, b = env
        srcs = "&".join(f"src=ws:{a if i % 2 else b}" for i in range(12))
        r = c.get(f"/compare-hub?{srcs}")
        assert r.status_code == 200
        assert b'data-sources="8"' in r.data

    def test_malformed_numeric_args_never_500(self, env):
        c, a, b = env
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=abc&ref=zz&preset=bogus")
        assert r.status_code == 200

    def test_label_html_is_escaped(self, env, tmp_path):
        evil = _write_quam(
            tmp_path / "ws2" / '<script>alert(1)</script>' / "quam_state")
        c, a, _b = env
        r = c.get(f"/compare-hub?src=ws:{evil}&src=ws:{a}&bucket=1")
        assert r.status_code == 200
        assert b"<script>alert(1)</script>" not in r.data


# ===========================================================================
# Bucket ① end-to-end
# ===========================================================================


class TestBucketOne:
    def test_headline_and_modified_row(self, env):
        c, a, b = env
        r = _hub(c, a, b, bucket=1)
        assert r.status_code == 200
        assert b"cmp-headline" in r.data
        assert b"changed" in r.data
        assert b"within tolerance" in r.data
        # f_01 differs by 100 MHz — a modified hairline row, inline
        assert b'cmp-cls-modified' in r.data
        assert b"f_01" in r.data
        assert b"cmp-delta" in r.data   # per-cell Δ vs ★ref (display pin)

    def test_identical_hero(self, env):
        """U6 — backup verification is binary; identical is a hero line."""
        c, a, _b = env
        r = _hub(c, a, a, bucket=1)
        assert r.status_code == 200
        assert b"Identical" in r.data
        assert b"leaves equal" in r.data

    def test_strictness_changes_the_verdict(self, env, tmp_path):
        """50 Hz apart: lab (±100 Hz) → within tolerance; exact → changed."""
        a = _write_quam(tmp_path / "t" / "x" / "quam_state", f_01=6.25e9)
        state = _make_state(6.25e9)
        state["qubits"]["qA1"]["f_01"] = 6.25e9 + 50.0
        b = _write_quam(tmp_path / "t" / "y" / "quam_state", state=state)
        app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        c = app.test_client()

        r_lab = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=1&preset=lab")
        r_exact = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=1&preset=exact")
        assert b"cmp-cls-within_tolerance" in r_lab.data
        assert b"cmp-cls-modified" in r_exact.data

    def test_ref_star_moves(self, env):
        c, a, b = env
        r = _hub(c, a, b, bucket=1, ref=1)
        assert r.status_code == 200
        html = r.data.decode()
        star_active = html.index("cmp-src-star active")
        second_row = html.index('data-valid-idx="1"')
        assert abs(star_active - second_row) < 400   # the ★ sits on row 1

    def test_meta_toggle_present_when_meta_rows_exist(self, env, tmp_path):
        """A literal→pointer flip is link-changed — meta affix, off by default."""
        a = _write_quam(tmp_path / "m" / "x" / "quam_state")
        state = _make_state()
        state["qubits"]["qA1"]["xy"]["RF_frequency"] = "#/qubits/qA1/f_01"
        b = _write_quam(tmp_path / "m" / "y" / "quam_state", state=state)
        app = create_app(testing=True, instance_path=str(tmp_path / "_i3"))
        c = app.test_client()
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=1")
        assert r.status_code == 200
        assert b"link/schema" in r.data
        assert b'data-meta="1"' in r.data

    def test_summary_tab_rendered(self, env):
        c, a, b = env
        r = _hub(c, a, b, bucket=1)
        assert b"cmp-summary" in r.data
        assert b"qA1" in r.data


# ===========================================================================
# Buckets ② / ③
# ===========================================================================


class TestBucketTwoThree:
    def test_bucket2_needs_confirm_then_map_param_compares(self, env):
        c, a, b = env
        r = _hub(c, a, b, bucket=2)
        assert r.status_code == 200
        # single-point grid is degenerate → suggestion, never auto (A2)
        assert b"cmp-confirm-panel" in r.data or b"cmp-mapping-bar" in r.data
        if b"cmp-confirm-panel" in r.data:
            assert b'data-map="qA1:qA1"' in r.data
            r2 = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=2&map=qA1:qA1")
            assert r2.status_code == 200
            assert b"cmp-mapping-bar" in r2.data
            assert b"cmp-headline" in r2.data

    def test_bucket2_with_three_sources_is_friendly_error(self, env):
        c, a, b = env
        r = _hub(c, a, b, a, bucket=2)
        assert r.status_code == 200
        assert b"exactly two" in r.data

    def test_bucket3_chip_cards(self, env):
        c, a, b = env
        r = _hub(c, a, b, bucket=3)
        assert r.status_code == 200
        assert b"cmp-card" in r.data
        assert b"qubits" in r.data
        # no leaf rows in bucket 3
        assert b"cmp-cls-modified" not in r.data


# ===========================================================================
# Lazy group fragment
# ===========================================================================


class TestGroupFragment:
    def test_group_rows(self, env):
        c, a, b = env
        r = c.get(f"/compare-hub/group?src=ws:{a}&src=ws:{b}"
                  f"&bucket=1&preset=lab&ref=0&section=Qubits&entity=qA1")
        assert r.status_code == 200
        assert b"cmp-row" in r.data

    def test_group_eq_toggle_includes_equal_rows(self, env):
        c, a, b = env
        base = (f"/compare-hub/group?src=ws:{a}&src=ws:{b}"
                f"&bucket=1&preset=lab&ref=0&section=Qubits&entity=qA1")
        r0 = c.get(base)
        r1 = c.get(base + "&eq=1")
        assert r1.data.count(b"cmp-row ") > r0.data.count(b"cmp-row ")
        assert b"cmp-cls-equal" in r1.data

    def test_unknown_group_is_warning_not_500(self, env):
        c, a, b = env
        r = c.get(f"/compare-hub/group?src=ws:{a}&src=ws:{b}"
                  f"&bucket=1&preset=lab&ref=0&section=Qubits&entity=nope")
        assert r.status_code == 200
        assert b"not found" in r.data.lower()

    def test_big_group_renders_lazily(self, tmp_path):
        """A5 — groups past the inline budget carry a lazy data-url."""
        state_a = _make_state()
        state_b = _make_state()
        ops_a = state_a["qubits"]["qA1"]["xy"]["operations"]
        ops_b = state_b["qubits"]["qA1"]["xy"]["operations"]
        for i in range(120):   # > _HUB_INLINE_GROUP_ROWS
            ops_a[f"op{i}"] = {"amplitude": 0.1}
            ops_b[f"op{i}"] = {"amplitude": 0.2}
        a = _write_quam(tmp_path / "big" / "x" / "quam_state", state=state_a)
        b = _write_quam(tmp_path / "big" / "y" / "quam_state", state=state_b)
        app = create_app(testing=True, instance_path=str(tmp_path / "_i4"))
        c = app.test_client()
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=1")
        assert r.status_code == 200
        assert b'data-lazy="1"' in r.data
        html = r.data.decode()
        import re as _re
        m = _re.search(r'data-url="([^"]+)"', html)
        assert m
        url = m.group(1).replace("&amp;", "&")
        r2 = c.get(url)
        assert r2.status_code == 200
        assert r2.data.count(b"cmp-row ") >= 120


# ===========================================================================
# Sources beyond ws: — working / hist / options popover
# ===========================================================================


class TestOtherOrigins:
    def test_working_source_after_load(self, env):
        c, a, b = env
        resp = c.post("/load", data={"folder": str(a)})
        assert resp.status_code in (200, 302)
        r = c.get(f"/compare-hub?src=working:{a}&src=ws:{b}&bucket=1")
        assert r.status_code == 200
        assert b"WORKING" in r.data
        assert b"cmp-headline" in r.data

    def test_history_snapshot_source(self, env):
        c, a, b = env
        # take a snapshot through the manager the app actually uses
        with c.application.app_context():
            hm = c.application.config["history_manager"]
            hm.check_and_snapshot(a, trigger="manual")
            snaps = hm.list_snapshots(a)
            assert snaps
            key = hm._key_for(a)
            ts = snaps[0].timestamp
        r = c.get(f"/compare-hub?src=hist:{key}/{ts}&src=ws:{b}&bucket=1")
        assert r.status_code == 200
        assert b"HISTORY" in r.data
        assert b"cmp-headline" in r.data

    def test_options_popover_lists_states(self, env):
        c, a, _b = env
        with c.application.app_context():
            hm = c.application.config["history_manager"]
            hm.check_and_snapshot(a, trigger="manual")
        c.post("/load", data={"folder": str(a)})
        from urllib.parse import quote
        r = c.get(f"/compare-hub/options?path={quote(str(a))}&name=chipA")
        assert r.status_code == 200
        assert b"Live files" in r.data
        assert b"Working state" in r.data
        assert b"HISTORY" in r.data

    def test_options_empty_for_unknown(self, env):
        c, _a, _b = env
        r = c.get("/compare-hub/options?path=/nowhere&name=x")
        assert r.status_code == 200


# ===========================================================================
# Isolation — the hub must not touch the app's own store caches
# ===========================================================================


class TestIsolation:
    def test_hub_render_leaves_app_caches_alone(self, env):
        c, a, b = env
        from quam_state_manager.web import routes as R
        with c.application.app_context():
            pass
        before_quam = dict(R._quam_cache)
        r = _hub(c, a, b, bucket=1)
        assert r.status_code == 200
        assert dict(R._quam_cache) == before_quam
        ws = c.application.config["workspace"]
        assert len(getattr(ws, "_loaded_stores", {})) == 0


# ===========================================================================
# Post-review hardening regressions (adversarial review of c17168b)
# ===========================================================================


class TestReviewHardening:
    def test_bulk_dangling_attention_panel_renders(self, tmp_path):
        """P0 regression: >=3 identical-leaf dangling pointers (the variantb
        family's x90 detunings) must render the attention panel, not 500 —
        Jinja `g.keys` used to resolve to dict.keys (the method)."""
        # the danglings must exist on BOTH sides — one-sided keys classify
        # added/removed, not unresolved, and never reach the coalescer
        state = _make_state(n_qubits=3)
        state_b = _make_state(n_qubits=3, f_01=6.26e9)
        for s in (state, state_b):
            for q in s["qubits"].values():
                q["xy"]["operations"]["x90_DragCosine"] = {
                    "detuning": "#../x90_DragCosine_does_not_exist/detuning"}
        a = _write_quam(tmp_path / "dang" / "x" / "quam_state", state=state)
        b = _write_quam(tmp_path / "dang" / "y" / "quam_state", state=state_b)
        app = create_app(testing=True, instance_path=str(tmp_path / "_ir"))
        c = app.test_client()
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=1")
        assert r.status_code == 200
        assert b"dangling" in r.data
        assert b"cmp-attention" in r.data

    def test_garbage_map_falls_back_to_suggestion_with_warning(self, env):
        c, a, b = env
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=2&map=zz:yy")
        assert r.status_code == 200
        assert b"matches neither device" in r.data
        # fell back to the needs_confirm suggestion, not an empty "result"
        assert b"cmp-confirm-panel" in r.data or b"cmp-mapping-bar" in r.data
        assert "0 changed".encode() not in r.data

    def test_inverted_map_is_flipped_not_emptied(self, tmp_path):
        """setRef used to keep a ref-oriented map — the server now flips a
        fully inverted map instead of rendering 0/0/0 as a result."""
        state_a = _make_state(n_qubits=2)
        state_b = json.loads(json.dumps(_make_state(n_qubits=2)))
        state_b["qubits"] = {f"qB{i+1}": v for i, (k, v) in
                             enumerate(state_b["qubits"].items())}
        for name, q in state_b["qubits"].items():
            q["id"] = name
        state_b["active_qubit_names"] = list(state_b["qubits"])
        a = _write_quam(tmp_path / "inv" / "x" / "quam_state", state=state_a)
        b = _write_quam(tmp_path / "inv" / "y" / "quam_state", state=state_b)
        app = create_app(testing=True, instance_path=str(tmp_path / "_iv"))
        c = app.test_client()
        # map oriented for ref=0 (qA*->qB*), requested with ref=1
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=2"
                  f"&ref=1&map=qA1:qB1,qA2:qB2")
        assert r.status_code == 200
        assert b"flipped to match" in r.data
        assert b"cmp-mapping-bar" in r.data
        assert b"cmp-headline" in r.data

    def test_duplicate_map_targets_dropped_with_note(self, tmp_path):
        state_a = _make_state(n_qubits=2)
        a = _write_quam(tmp_path / "dup" / "x" / "quam_state", state=state_a)
        b = _write_quam(tmp_path / "dup" / "y" / "quam_state",
                        state=_make_state(n_qubits=2))
        app = create_app(testing=True, instance_path=str(tmp_path / "_du"))
        c = app.test_client()
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=2"
                  f"&map=qA1:qA1,qA2:qA1")
        assert r.status_code == 200
        assert b"Duplicate mapping targets" in r.data

    def test_history_restore_request_gets_full_page(self, env):
        """A7: htmx history restores carry HX-Request AND
        HX-History-Restore-Request — they need the FULL page (htmx swaps
        into <body>; the bare partial would destroy the app chrome)."""
        c, a, b = env
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=1", headers={
            "HX-Request": "true",
            "HX-History-Restore-Request": "true",
        })
        assert r.status_code == 200
        assert b"<html" in r.data          # full page, chrome included
        r2 = c.get("/compare-hub", headers={"HX-Request": "true"})
        assert b"<html" not in r2.data     # normal swaps still get the partial

    def test_options_never_offers_dead_working_ref(self, env, tmp_path):
        """A persisted-but-not-loaded working copy must NOT be offered as a
        working: ref (it can only resolve in-memory) — it is offered as a
        folder ref onto the on-disk working files instead."""
        c, a, _b = env
        c.post("/load", data={"folder": str(a)})     # creates the working copy
        inst = c.application.instance_path
        # fresh app on the SAME instance = app restart (nothing in memory).
        # _quam_cache is a module global shared by both apps in-process —
        # clear it to actually simulate the restart.
        from quam_state_manager.web import routes as R
        saved_cache = dict(R._quam_cache)
        R._quam_cache.clear()
        app2 = create_app(testing=True, instance_path=inst)
        c2 = app2.test_client()
        from urllib.parse import quote
        r = c2.get(f"/compare-hub/options?path={quote(str(a))}&name=chipA")
        assert r.status_code == 200
        assert f"working:{a}".encode() not in r.data
        if b"Working state" in r.data:      # offered — must be the ws: form
            assert b"saved on disk" in r.data
            import re as _re
            m = _re.search(rb'data-ref="(ws:[^"]+working_state[^"]+)"', r.data)
            assert m, "saved working copy must be offered as a ws: folder ref"
            ref = m.group(1).decode().replace("&amp;", "&")
            r2 = c2.get(f"/compare-hub?src={ref}&src=ws:{a}&bucket=1")
            assert r2.status_code == 200
            assert b"cmp-src-error" not in r2.data   # and it must resolve
        R._quam_cache.update(saved_cache)

    def test_inline_groups_offer_equal_rows_toggle(self, env):
        """'Never truncated' = everything reachable — inline groups too."""
        c, a, b = env
        r = _hub(c, a, b, bucket=1)
        assert r.status_code == 200
        assert b"cmp-show-eq" in r.data

    def test_summary_capped_with_load_all_fragment(self, tmp_path):
        state_a = _make_state(n_qubits=30)
        state_b = _make_state(n_qubits=30, f_01=6.26e9)
        a = _write_quam(tmp_path / "sum" / "x" / "quam_state", state=state_a)
        b = _write_quam(tmp_path / "sum" / "y" / "quam_state", state=state_b)
        app = create_app(testing=True, instance_path=str(tmp_path / "_sm"))
        c = app.test_client()
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=1")
        assert r.status_code == 200
        assert b"cmp-summary-more" in r.data
        assert r.data.count(b"cmp-sum-entity") <= 25
        r2 = c.get(f"/compare-hub/summary?src=ws:{a}&src=ws:{b}"
                   f"&bucket=1&preset=lab&ref=0")
        assert r2.status_code == 200
        assert r2.data.count(b"cmp-sum-entity") == 30
        assert b"cmp-summary-more" not in r2.data

    def test_source_memo_invalidates_on_file_change(self, env):
        """The mtime memo must never serve a stale chip after the files
        change on disk."""
        import os, time as _time
        c, a, b = env
        r1 = _hub(c, a, b, bucket=1)
        assert b"6,250,000,000" in r1.data or b"6250000000" in r1.data
        new_state = _make_state(f_01=9.99e9)
        (a / "state.json").write_text(json.dumps(new_state), encoding="utf-8")
        st = (a / "state.json").stat()
        os.utime(a / "state.json", (st.st_atime + 5, st.st_mtime + 5))
        r2 = _hub(c, a, b, bucket=1)
        assert r2.status_code == 200
        assert b"9,990,000,000" in r2.data or b"9990000000" in r2.data


# ===========================================================================
# P2 — structure strip, fingerprint suggestion, deep links, drop-stash
# ===========================================================================


class TestP2Strips:
    def test_bucket3_ships_strip_cards(self, env):
        c, a, b = env
        r = _hub(c, a, b, bucket=3)
        assert r.status_code == 200
        assert b"cmp-strips-json" in r.data
        assert b"cmp-strip-mount" in r.data
        assert b"cmp-stone-diff" not in r.data   # ③ never tints

    def test_bucket1_strip_hidden_without_wiring_change(self, env):
        c, a, b = env    # same wiring content on both fixtures
        r = _hub(c, a, b, bucket=1)
        assert r.status_code == 200
        assert b"cmp-strips-json" not in r.data
        assert b"cmp-strip-banner" not in r.data

    def test_bucket1_strip_banner_when_wiring_changed(self, env, tmp_path):
        c, a, _b = env
        b2 = _write_quam(tmp_path / "w2" / "chipW" / "quam_state", f_01=6.25e9)
        wiring = _make_wiring()
        wiring["wiring"]["qubits"]["qA1"]["xy"]["opx_output"] = "MW-FEM/2/7"
        (b2 / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        r = _hub(c, a, b2, bucket=1)
        assert r.status_code == 200
        assert b"cmp-strip-banner" in r.data
        assert b"cmp-strips-json" in r.data

    def test_bucket2_tints_changed_stones(self, env):
        c, a, b = env    # f_01 differs → qA1 changed under the map
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=2&map=qA1:qA1")
        assert r.status_code == 200
        assert b"cmp-strips-json" in r.data
        assert b"cmp-stone-diff" in r.data

    def test_identical_result_ships_no_strip(self, env):
        c, a, _b = env
        r = _hub(c, a, a, bucket=1)
        assert b"cmp-strips-json" not in r.data


class TestP2Suggestion:
    def test_ghost_suggestion_for_matching_fingerprints(self, env):
        """chipA/chipB share network + qubit names → same fingerprint token."""
        c, a, b = env
        r = _hub(c, a, b)          # no bucket chosen
        assert r.status_code == 200
        assert b"cmp-suggest" in r.data
        assert b"cmp-suggest-primary" not in r.data   # ghost, never primary

    def test_hinted_deep_link_gets_primary_cta(self, env):
        c, a, b = env
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&hint=1")
        assert b"cmp-suggest-primary" in r.data
        assert b"autofocus" in r.data

    def test_no_suggestion_for_different_fingerprints(self, env, tmp_path):
        c, a, _b = env
        other = _write_quam(tmp_path / "o" / "otherchip" / "quam_state",
                            n_qubits=2)
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{other}&hint=1")
        assert r.status_code == 200
        assert b"cmp-suggest" not in r.data   # hint alone is never trusted

    def test_no_suggestion_once_bucket_chosen(self, env):
        c, a, b = env
        r = _hub(c, a, b, bucket=1)
        assert b"cmp-suggest" not in r.data


class TestP2Stash:
    def _payload(self, label="Dropped chip"):
        return {"state": _make_state(), "wiring": _make_wiring(),
                "label": label}

    def test_stash_roundtrip_into_basket(self, env):
        c, a, _b = env
        payload = self._payload("My Drop")
        # differ from chipA — identical content would (correctly) render the
        # identical hero instead of a headline
        payload["state"]["qubits"]["qA1"]["f_01"] = 5.55e9
        r = c.post("/compare-hub/stash", json=payload)
        assert r.status_code == 200
        ref = r.get_json()["ref"]
        assert ref.startswith("drop:")
        r2 = c.get(f"/compare-hub?src={ref}&src=ws:{a}&bucket=1")
        assert r2.status_code == 200
        assert b"cmp-src-error" not in r2.data
        assert b"My Drop" in r2.data          # meta.json label surfaced
        assert b"FILE" in r2.data             # U8 badge (never DROPPED)
        assert b"cmp-headline" in r2.data

    def test_stash_rejects_malformed(self, env):
        c, _a, _b = env
        assert c.post("/compare-hub/stash", json=[1, 2]).status_code == 400
        assert c.post("/compare-hub/stash",
                      json={"state": "nope", "wiring": {}}).status_code == 400

    def test_stash_dedups_by_content(self, env):
        c, _a, _b = env
        r1 = c.post("/compare-hub/stash", json=self._payload())
        r2 = c.post("/compare-hub/stash", json=self._payload())
        assert r1.get_json()["ref"] == r2.get_json()["ref"]

    def test_stash_gc_caps_at_twenty(self, env):
        c, _a, _b = env
        for i in range(23):
            payload = self._payload(f"chip{i}")
            payload["state"]["qubits"]["qA1"]["f_01"] = 6.0e9 + i * 1e6
            assert c.post("/compare-hub/stash",
                          json=payload).status_code == 200
        drops = Path(c.application.instance_path) / "compare_drops"
        n = len([d for d in drops.iterdir() if d.is_dir()])
        assert n <= 20, n


class TestP2DeepLinks:
    def _snapshot(self, c, path):
        with c.application.app_context():
            hm = c.application.config["history_manager"]
            hm.check_and_snapshot(path, trigger="manual")
            return hm._key_for(path), hm.list_snapshots(path)[0].timestamp

    def test_state_history_page_has_compare_link(self, env):
        c, a, _b = env
        c.post("/load", data={"folder": str(a)})
        self._snapshot(c, a)
        r = c.get("/state-history")
        assert r.status_code == 200
        assert b"/compare-hub?src=hist:" in r.data
        # the in-row diff STAYS verbatim (U1a)
        assert b"View changes vs current" in r.data

    def test_history_drawer_has_compare_link(self, env):
        c, a, _b = env
        c.post("/load", data={"folder": str(a)})
        self._snapshot(c, a)
        r = c.get("/api/history")
        assert r.status_code == 200
        assert b"/compare-hub?src=hist:" in r.data
        assert b"View Changes" in r.data          # drawer diff stays (U1a)

    def test_deep_link_resolves_with_primary_suggestion(self, env):
        c, a, _b = env
        c.post("/load", data={"folder": str(a)})
        key, ts = self._snapshot(c, a)
        r = c.get(f"/compare-hub?src=hist:{key}/{ts}"
                  f"&src=working:{a}&hint=1")
        assert r.status_code == 200
        assert b"cmp-src-error" not in r.data
        assert b"cmp-suggest-primary" in r.data   # fingerprint-proven + hinted
        assert b"cmp-headline" not in r.data      # still user-declared (axiom 2)


# ===========================================================================
# P3 — mapping editor + A1 persistence + U7 guard
# ===========================================================================


class TestP3Mapping:
    def test_editor_rendered_on_needs_confirm(self, env):
        c, a, b = env
        r = _hub(c, a, b, bucket=2)
        assert r.status_code == 200
        if b"cmp-confirm-panel" in r.data:
            assert b"data-cmp-map-editor" in r.data
            assert b'data-map-a="qA1"' in r.data

    def test_editor_rendered_on_confirmed_bar(self, env):
        c, a, b = env
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=2&map=qA1:qA1")
        assert b"cmp-mapping-bar" in r.data
        assert b"data-cmp-map-editor" in r.data   # [Edit mapping] toggle

    def test_map_save_then_autoload(self, env):
        """A1 — a confirmed mapping reloads without the URL param."""
        c, a, b = env
        r = c.post("/compare-hub/map/save", json={
            "srcs": [f"ws:{a}", f"ws:{b}"], "ref": 0, "map": "qA1:qA1"})
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True and d["persisted"] is True
        # NO map param — the saved mapping must apply on its own
        r2 = _hub(c, a, b, bucket=2)
        assert r2.status_code == 200
        assert b"cmp-mapping-bar" in r2.data
        assert b"saved mapping" in r2.data
        assert b"cmp-headline" in r2.data
        assert b"cmp-confirm-panel" not in r2.data

    def test_saved_map_survives_ref_flip(self, env):
        """The store re-orients by sorted anchors — moving the ★ must not
        orphan the saved mapping (A1 N-way rule)."""
        c, a, b = env
        c.post("/compare-hub/map/save", json={
            "srcs": [f"ws:{a}", f"ws:{b}"], "ref": 0, "map": "qA1:qA1"})
        r = _hub(c, a, b, bucket=2, ref=1)
        assert r.status_code == 200
        assert b"cmp-mapping-bar" in r.data
        assert b"saved mapping" in r.data

    def test_map_save_validation(self, env):
        c, a, b = env
        assert c.post("/compare-hub/map/save", json=[1]).status_code == 400
        assert c.post("/compare-hub/map/save", json={
            "srcs": [f"ws:{a}"], "map": "x:y"}).status_code == 400
        r = c.post("/compare-hub/map/save", json={
            "srcs": [f"ws:{a}", f"ws:{b}"], "ref": 0, "map": "zz:yy"})
        assert r.status_code == 400

    def test_drop_origin_maps_are_session_only(self, env):
        c, a, _b = env
        payload = {"state": _make_state(), "wiring": _make_wiring(),
                   "label": "drop"}
        payload["state"]["qubits"]["qA1"]["f_01"] = 5.0e9
        ref = c.post("/compare-hub/stash", json=payload).get_json()["ref"]
        r = c.post("/compare-hub/map/save", json={
            "srcs": [f"ws:{a}", ref], "ref": 0, "map": "qA1:qA1"})
        assert r.status_code == 200
        d = r.get_json()
        assert d["ok"] is True and d["persisted"] is False

    def test_stale_saved_names_degrade_gracefully(self, env, tmp_path):
        """A chip renaming a mapped qubit must not 500 or apply a bogus map."""
        c, a, b = env
        c.post("/compare-hub/map/save", json={
            "srcs": [f"ws:{a}", f"ws:{b}"], "ref": 0, "map": "qA1:qA1"})
        state = _make_state()
        state["qubits"] = {"qZ9": state["qubits"]["qA1"]}
        state["qubits"]["qZ9"]["id"] = "qZ9"
        state["active_qubit_names"] = ["qZ9"]
        (b / "state.json").write_text(json.dumps(state), encoding="utf-8")
        import os
        st = (b / "state.json").stat()
        os.utime(b / "state.json", (st.st_atime + 5, st.st_mtime + 5))
        r = _hub(c, a, b, bucket=2)
        assert r.status_code == 200
        # every saved pair went stale → back to the suggestion flow,
        # WITH an honest note (a silent disappearance looked like data loss)
        assert b"cmp-confirm-panel" in r.data
        assert b"stale" in r.data

    def test_u7_guard_banner_on_poor_match(self, env, tmp_path):
        c, a, _b = env
        big = _write_quam(tmp_path / "u7" / "bigchip" / "quam_state",
                          n_qubits=3)
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{big}&bucket=2&map=qA1:qA1")
        assert r.status_code == 200
        assert b"same design" in r.data          # the U7 banner
        assert b"Compare as" in r.data

    def test_no_guard_on_full_match(self, env):
        c, a, b = env
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{b}&bucket=2&map=qA1:qA1")
        assert b"cmp-map-guard" not in r.data


# ===========================================================================
# P4 — legacy redirects (docs/49: /diff + /compare + /chip-compare → hub)
# ===========================================================================


class TestP4Redirects:
    def test_run_archive_paths_get_run_tokens(self, env, tmp_path):
        """Archive-run layouts translate to run: (honest RUN badge); plain
        folders to ws:."""
        c, a, _b = env
        run_qs = (tmp_path / "arch" / "chipR" / "2026-07-04"
                  / "#12_rabi_153000" / "quam_state")
        _write_quam(run_qs)
        resp = c.post("/compare", data={"paths": [str(a), str(run_qs)]})
        assert resp.status_code == 302
        loc = resp.headers["Location"]
        assert "src=ws%3A" in loc
        assert "src=run%3A" in loc

    def test_redirected_legacy_post_lands_on_working_hub(self, env):
        """Follow the translation end-to-end: the hub page renders the
        basket the legacy POST described."""
        c, a, b = env
        resp = c.post("/chip-compare", data={"paths": [str(a), str(b)]})
        loc = resp.headers["Location"]
        r = c.get(loc)
        assert r.status_code == 200
        assert b'data-sources="2"' in r.data
        assert b"Pick the comparison context" in r.data   # axiom 2 intact

    def test_source_cap_applies_to_legacy_translation(self, env):
        c, a, b = env
        paths = [str(a), str(b)] * 6      # 12 → capped at 8
        resp = c.post("/compare", data={"paths": paths})
        loc = resp.headers["Location"]
        assert loc.count("src=") == 8
        assert "trunc=12" in loc          # never truncate silently
        r = c.get(loc)
        assert b"first 8 of" in r.data and b"12" in r.data

    def test_command_palette_points_at_hub(self, env):
        c, _a, _b = env
        html = c.get("/compare-hub").data.decode()
        assert '"/compare-hub"' in html
        assert '"/diff"' not in html
        assert '"/chip-compare"' not in html   # the palette entry too


# ===========================================================================
# Final-audit hardening regressions (P2–P4 adversarial review)
# ===========================================================================


class TestFinalAuditHardening:
    def test_legacy_get_with_htmx_uses_hx_redirect(self, env):
        """The palette/recents path issues htmx GETs — A7 applies to GET too."""
        c, _a, _b = env
        for url in ("/diff", "/chip-compare"):
            r = c.get(url, headers={"HX-Request": "true"})
            assert r.status_code == 200
            assert r.headers["HX-Redirect"].startswith("/compare-hub")

    def test_legacy_bare_get_lands_with_moved_note(self, env):
        c, _a, _b = env
        r = c.get("/chip-compare")
        assert r.headers["Location"] == "/compare-hub?from=chip-compare"
        r2 = c.get(r.headers["Location"])
        assert b"moved here" in r2.data

    def test_legacy_translation_never_carries_hint(self, env):
        """U1b — manual baskets (legacy forms) never get the primary CTA."""
        c, a, b = env
        r = c.post("/compare", data={"paths": [str(a), str(b)]})
        assert "hint" not in r.headers["Location"]
        r2 = c.post("/chip-compare", data={"paths": [str(a), str(b)]})
        assert "hint" not in r2.headers["Location"]

    def test_saved_map_survives_new_snapshot(self, env):
        """A1 — hist: anchors key on the CHIP, not one frozen snapshot;
        a new snapshot must not orphan the confirmed mapping."""
        c, a, b = env
        with c.application.app_context():
            hm = c.application.config["history_manager"]
            hm.check_and_snapshot(a, trigger="manual")
            key = hm._key_for(a)
            ts0 = hm.list_snapshots(a)[0].timestamp
        r = c.post("/compare-hub/map/save", json={
            "srcs": [f"hist:{key}/{ts0}", f"ws:{b}"], "ref": 0,
            "map": "qA1:qA1"})
        assert r.get_json()["persisted"] is True
        # a NEW snapshot of the same chip (touch the file so content differs)
        state = _make_state(f_01=6.26e9)
        (a / "state.json").write_text(json.dumps(state), encoding="utf-8")
        import os
        st = (a / "state.json").stat()
        os.utime(a / "state.json", (st.st_atime + 5, st.st_mtime + 5))
        with c.application.app_context():
            hm = c.application.config["history_manager"]
            hm.check_and_snapshot(a, trigger="manual", force=True)
            ts1 = hm.list_snapshots(a)[0].timestamp
        assert ts1 != ts0
        r2 = c.get(f"/compare-hub?src=hist:{key}/{ts1}&src=ws:{b}&bucket=2")
        assert r2.status_code == 200
        assert b"saved mapping" in r2.data     # reloaded across snapshots

    def test_no_suggestion_for_wiring_missing_sources(self, env, tmp_path):
        c, _a, _b = env
        x = tmp_path / "nw" / "cx" / "quam_state"
        y = tmp_path / "nw" / "cy" / "quam_state"
        for f in (x, y):
            f.mkdir(parents=True)
            (f / "state.json").write_text(json.dumps(_make_state()),
                                          encoding="utf-8")
            # no wiring.json — name-set match is not device identity
        r = c.get(f"/compare-hub?src=ws:{x}&src=ws:{y}&hint=1")
        assert r.status_code == 200
        assert b"cmp-suggest" not in r.data

    def test_pruned_drop_ref_degrades_to_error_row(self, env):
        c, a, _b = env
        payload = {"state": _make_state(), "wiring": _make_wiring(),
                   "label": "gone"}
        payload["state"]["qubits"]["qA1"]["f_01"] = 4.44e9
        ref = c.post("/compare-hub/stash", json=payload).get_json()["ref"]
        import shutil as _sh
        _sh.rmtree(ref.split(":", 1)[1], ignore_errors=True)   # GC'd
        r = c.get(f"/compare-hub?src={ref}&src=ws:{a}&bucket=1")
        assert r.status_code == 200
        assert b"cmp-src-error" in r.data

    def test_restash_same_content_updates_label(self, env):
        """Memo semantics: a re-drop with a new label must surface the new
        label (the stash route pops the source memo)."""
        c, a, _b = env
        payload = {"state": _make_state(), "wiring": _make_wiring(),
                   "label": "first name"}
        payload["state"]["qubits"]["qA1"]["f_01"] = 3.33e9
        ref = c.post("/compare-hub/stash", json=payload).get_json()["ref"]
        r1 = c.get(f"/compare-hub?src={ref}&src=ws:{a}&bucket=1")
        assert b"first name" in r1.data
        payload["label"] = "second name"
        ref2 = c.post("/compare-hub/stash", json=payload).get_json()["ref"]
        assert ref2 == ref
        r2 = c.get(f"/compare-hub?src={ref}&src=ws:{a}&bucket=1")
        assert b"second name" in r2.data

    def test_u7_wide_trigger_fires_on_full_match_wild_values(self, env, tmp_path):
        """U7's second (binding) trigger: all qubits map 1:1 but >50% of the
        summary rows differ beyond even Wide → still guarded."""
        c, a, _b = env
        state = _make_state()
        q = state["qubits"]["qA1"]
        q["f_01"] = 1.0e9
        q["T1"] = 3.0
        q["T2ramsey"] = 9.0
        q["anharmonicity"] = -9.9e8
        q["xy"]["RF_frequency"] = 1.0e9
        q["resonator"]["f_01"] = 2.0e9
        q["resonator"]["RF_frequency"] = 2.0e9
        q["resonator"]["operations"]["readout"]["amplitude"] = 0.9
        q["z"]["joint_offset"] = 0.9
        wild = _write_quam(tmp_path / "wild" / "cw" / "quam_state", state=state)
        r = c.get(f"/compare-hub?src=ws:{a}&src=ws:{wild}&bucket=2&map=qA1:qA1")
        assert r.status_code == 200
        assert b"same design" in r.data
        assert b"beyond even the Wide" in r.data

    def test_result_columns_use_deduped_labels(self, env, tmp_path):
        """Two flat same-named chips must be distinguishable in the summary
        headers / cards too, not just the basket."""
        x = _write_quam(tmp_path / "dl" / "rootA" / "LabA", f_01=6.25e9)
        y = _write_quam(tmp_path / "dl" / "rootB" / "LabA", f_01=6.35e9)
        app = create_app(testing=True, instance_path=str(tmp_path / "_dl"))
        c = app.test_client()
        r = c.get(f"/compare-hub?src=ws:{x}&src=ws:{y}&bucket=3")
        html = r.data.decode()
        assert "LabA (rootA)" in html
        assert "LabA (rootB)" in html
