"""Route-level tests for the stale-working-copy fix.

THE bug: load a quam_state folder, replace its live ``state.json`` /
``wiring.json`` out-of-band with a *different chip* (qubits qA1,qA2 → q0,q1),
re-load — the app kept showing the OLD chip, surviving even an app restart,
because both the in-memory ``_quam_cache`` (door A) and the on-disk
working-copy rehydrate (door B) were content-blind.

These tests drive the real routes: door A (same-process re-select), door B
(simulated restart via a fresh app + cleared module cache), the
edits-preserved variants (banner instead of clobber), and the working-copy
GC endpoints.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from quam_state_manager.core import working_copy
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app


def _chip_state(names: tuple[str, ...], f_01: float = 6.25e9) -> dict:
    return {
        "qubits": {
            n: {"id": n, "f_01": f_01, "T1": 8000 + i}
            for i, n in enumerate(names)
        },
        "qubit_pairs": {},
        "active_qubit_names": list(names),
    }


def _wiring() -> dict:
    return {"wiring": {"qubits": {}}, "network": {"host": "10.1.1.18"}}


def _write_live(folder: Path, state: dict) -> None:
    """Out-of-band replacement of the live state.json (future mtime so the
    change is unambiguously detectable on coarse-mtime filesystems)."""
    p = folder / "state.json"
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    future = time.time() + 100
    os.utime(p, (future, future))


@pytest.fixture
def live_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "chipA" / "quam_state"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(
        json.dumps(_chip_state(("qA1", "qA2")), indent=2), encoding="utf-8")
    (folder / "wiring.json").write_text(
        json.dumps(_wiring(), indent=2), encoding="utf-8")
    return folder


def _app_client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    return app.test_client()


def _shown_qubits(client) -> str:
    """The state JSON the explorer page embeds — what the user actually sees."""
    return client.get("/explorer").data.decode()


def _has_qubit(html: str, name: str) -> bool:
    """True if *name* appears as a JSON key in the embedded state — the
    quoted form avoids false hits on UI chrome (the search placeholder
    literally contains "qA1")."""
    return f'"{name}"' in html


def _simulate_restart(folder: Path) -> None:
    """Drop the module-level in-memory context cache (what a process restart
    drops), leaving the on-disk working copy — door B."""
    with routes_mod._quam_cache_lock:
        routes_mod._quam_cache.pop(str(folder), None)


# ---------------------------------------------------------------------------
# Door B — restart / evicted in-memory context, clean working copy
# ---------------------------------------------------------------------------

class TestRestartCleanCopy:
    def test_live_replaced_clean_copy_shows_new_chip(self, tmp_path, live_folder):
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        assert "qA1" in _shown_qubits(client)

        _write_live(live_folder, _chip_state(("q0", "q1", "q2")))
        _simulate_restart(live_folder)

        client2 = _app_client(tmp_path)
        client2.post("/load", data={"folder": str(live_folder)})
        html = _shown_qubits(client2)
        assert _has_qubit(html, "q0")                  # the NEW chip
        assert not _has_qubit(html, "qA1")
        assert "live-diverged-banner" not in html      # clean → silent refresh

    def test_legacy_meta_replaced_shows_banner_not_clobber(self, tmp_path, live_folder):
        # Pre-fix working copies have no recorded hash: a replaced live can't
        # be told apart from user edits, so the old chip is kept BUT the
        # banner makes the divergence visible (one click to pull).
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})

        wc = working_copy.load(str(tmp_path / "_app_instance"), live_folder)
        meta = json.loads(wc.meta_path().read_text(encoding="utf-8"))
        meta.pop("synced_live_hash", None)
        wc.meta_path().write_text(json.dumps(meta), encoding="utf-8")

        _write_live(live_folder, _chip_state(("q0", "q1")))
        _simulate_restart(live_folder)

        client2 = _app_client(tmp_path)
        client2.post("/load", data={"folder": str(live_folder)})
        html = _shown_qubits(client2)
        assert _has_qubit(html, "qA1")                 # never clobbered
        assert "live-diverged-banner" in html          # ...but loudly flagged

        # One click: pull the live state -> new chip, banner gone.
        data = client2.post("/state/sync", data={"mode": "discard"}).get_json()
        assert data["status"] == "ok"
        html = _shown_qubits(client2)
        assert _has_qubit(html, "q0")
        assert not _has_qubit(html, "qA1")
        assert "live-diverged-banner" not in html


# ---------------------------------------------------------------------------
# Door B — restart with SAVED edits in the working copy
# ---------------------------------------------------------------------------

class TestRestartDirtyCopy:
    def test_saved_edits_preserved_plus_banner(self, tmp_path, live_folder):
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        client.post("/field/edit", data={"dot_path": "qubits.qA1.f_01",
                                         "value": "5.0e9"})
        client.post("/save")                           # persist to working copy

        _write_live(live_folder, _chip_state(("q0", "q1")))
        _simulate_restart(live_folder)

        client2 = _app_client(tmp_path)
        client2.post("/load", data={"folder": str(live_folder)})
        html = _shown_qubits(client2)
        assert _has_qubit(html, "qA1")
        assert "5000000000" in html                    # edit survived
        assert "live-diverged-banner" in html


# ---------------------------------------------------------------------------
# Door A — same-process re-select through the in-memory cache
# ---------------------------------------------------------------------------

class TestInMemoryCache:
    def test_reselect_after_replace_shows_new_chip(self, tmp_path, live_folder):
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        assert "qA1" in _shown_qubits(client)

        _write_live(live_folder, _chip_state(("q0", "q1")))
        client.post("/load", data={"folder": str(live_folder)})   # cache hit
        html = _shown_qubits(client)
        assert _has_qubit(html, "q0")
        assert not _has_qubit(html, "qA1")
        assert "live-diverged-banner" not in html

    def test_unsaved_edit_preserved_on_reselect(self, tmp_path, live_folder):
        # An unsaved change-log edit exists nowhere on disk — the cached
        # context must NOT be rebuilt/auto-synced over it.
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        client.post("/field/edit", data={"dot_path": "qubits.qA1.f_01",
                                         "value": "5.0e9"})

        _write_live(live_folder, _chip_state(("q0", "q1")))
        client.post("/load", data={"folder": str(live_folder)})
        html = _shown_qubits(client)
        assert _has_qubit(html, "qA1")                 # old chip kept
        assert not _has_qubit(html, "q0")
        assert "live-diverged-banner" in html          # divergence flagged
        # The pending edit is still in the change log.
        assert "qubits.qA1.f_01" in client.get("/changes").data.decode()

    def test_touch_only_change_stays_quiet(self, tmp_path, live_folder):
        # Same content re-saved with a new mtime (atomic re-write): no banner,
        # no resync churn.
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        _write_live(live_folder, _chip_state(("qA1", "qA2")))     # identical
        client.post("/load", data={"folder": str(live_folder)})
        html = _shown_qubits(client)
        assert _has_qubit(html, "qA1")
        assert "live-diverged-banner" not in html


# ---------------------------------------------------------------------------
# Working-copy GC endpoints
# ---------------------------------------------------------------------------

class TestWorkingCopyGC:
    def test_scan_and_gc(self, tmp_path, live_folder):
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        inst = str(tmp_path / "_app_instance")

        # A clean stray copy (deletable) + a dirty one (kept).
        clean_live = tmp_path / "other1" / "quam_state"
        clean_live.mkdir(parents=True)
        (clean_live / "state.json").write_text(json.dumps(_chip_state(("x0",))))
        (clean_live / "wiring.json").write_text(json.dumps(_wiring()))
        clean_wc = working_copy.create(inst, clean_live)

        dirty_live = tmp_path / "other2" / "quam_state"
        dirty_live.mkdir(parents=True)
        (dirty_live / "state.json").write_text(json.dumps(_chip_state(("y0",))))
        (dirty_live / "wiring.json").write_text(json.dumps(_wiring()))
        dirty_wc = working_copy.create(inst, dirty_live)
        (dirty_wc.working_folder / "state.json").write_text(
            json.dumps({"qubits": {"edited": {}}}), encoding="utf-8")

        scan = client.get("/api/working-copies/scan").get_json()
        assert scan["total"] == 3
        assert scan["by_status"]["clean"] == 2          # active copy + stray
        assert scan["by_status"]["dirty"] == 1

        result = client.post("/api/working-copies/gc").get_json()
        assert result["deleted"] == 1                   # only the stray clean
        assert not clean_wc.working_folder.exists()
        assert dirty_wc.working_folder.exists()
        # The ACTIVE chip's copy is protected even though it scans clean.
        active_wc = working_copy.load(inst, live_folder)
        assert active_wc is not None
        assert (active_wc.working_folder / "state.json").exists()


# ---------------------------------------------------------------------------
# Banner plumbing — single base-level slot + HTMX out-of-band update
# ---------------------------------------------------------------------------

class TestBannerSlot:
    def _make_diverged(self, tmp_path, live_folder):
        client = _app_client(tmp_path)
        client.post("/load", data={"folder": str(live_folder)})
        wc = working_copy.load(str(tmp_path / "_app_instance"), live_folder)
        meta = json.loads(wc.meta_path().read_text(encoding="utf-8"))
        meta.pop("synced_live_hash", None)            # legacy meta
        wc.meta_path().write_text(json.dumps(meta), encoding="utf-8")
        _write_live(live_folder, _chip_state(("q0", "q1")))
        _simulate_restart(live_folder)
        client2 = _app_client(tmp_path)
        client2.post("/load", data={"folder": str(live_folder)})
        return client2

    def test_full_page_has_exactly_one_slot(self, tmp_path, live_folder):
        client = self._make_diverged(tmp_path, live_folder)
        html = client.get("/explorer").data.decode()
        assert html.count('id="live-diverged-slot"') == 1
        assert "live-diverged-banner" in html

    def test_htmx_partial_carries_oob_slot(self, tmp_path, live_folder):
        client = self._make_diverged(tmp_path, live_folder)
        html = client.get("/explorer", headers={"HX-Request": "true"}).data.decode()
        assert 'hx-swap-oob' in html
        assert html.count('id="live-diverged-slot"') == 1
        assert "live-diverged-banner" in html

    def test_banner_visible_on_other_pages(self, tmp_path, live_folder):
        # The slot is base-level on EVERY full page now — a diverged chip
        # warns on /qubits too, not just the explorer.
        client = self._make_diverged(tmp_path, live_folder)
        html = client.get("/qubits").data.decode()
        assert "live-diverged-banner" in html
