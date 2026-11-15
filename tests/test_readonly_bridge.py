"""A read-only ask's bridge says so, and SM's write doors refuse it
(agents ver180, decision D-22).

Three layers: the bridge hides the write tools (unchanged), the bridge sends
``X-SM-Mode: readonly`` on every call, and every mutating request carrying it
is refused with 403 ``readonly_ask``. Without the header nothing changes."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parent.parent / "quam_state_manager"


@pytest.fixture()
def client(tmp_path):
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    return app.test_client()


def _bridge_post_paths() -> set[str]:
    src = (_PKG / "mcp.py").read_text(encoding="utf-8")
    # f-strings too: the path up to the first placeholder
    return {p.split("{")[0] for p in re.findall(r"""post_(?:json|form|form_h)\(\s*f?["'](/[^"']+)["']""", src)}


def test_inventory_covers_every_post_the_bridge_makes():
    from quam_state_manager.web.app import READONLY_REFUSED_DOORS
    missing = {p for p in _bridge_post_paths() if not any(p == d or p.startswith(d + "/") for d in READONLY_REFUSED_DOORS)}
    assert "/api/agent/plans/" in _bridge_post_paths(), "the f-string door must be seen"
    assert _bridge_post_paths(), "the scan found no bridge POSTs -- the pin would be vacuous"
    assert not missing, f"bridge POST paths not in the read-only inventory: {sorted(missing)}"


def test_inventory_names_the_decided_doors():
    from quam_state_manager.web.app import READONLY_REFUSED_DOORS
    for d in ("/field/edit", "/field/edit-batch", "/state/apply-to-live", "/undo", "/api/agent/run-node",
              "/api/agent/plans", "/api/agent/approvals", "/api/agent/journal", "/api/agent/note",
              "/api/agent/take-live"):
        assert d in READONLY_REFUSED_DOORS


@pytest.mark.parametrize("door", __import__("quam_state_manager.web.app", fromlist=["x"]).READONLY_REFUSED_DOORS)
def test_every_door_refuses_a_readonly_bridge(client, door):
    r = client.post(door, json={}, headers={"X-SM-Mode": "readonly", "X-SM-Agent": "mcp"})
    assert r.status_code == 403 and r.get_json()["refused"] == "readonly_ask"
    # the same request without the header is not refused for being read-only
    r2 = client.post(door, json={}, headers={"X-SM-Agent": "mcp"})
    assert not (r2.status_code == 403 and (r2.get_json(silent=True) or {}).get("refused") == "readonly_ask")


def test_reads_still_work(client):
    r = client.get("/api/agent/chip", headers={"X-SM-Mode": "readonly"})
    assert r.status_code != 403


def test_bridge_sends_the_mode_only_when_readonly(monkeypatch):
    from quam_state_manager.core import agent_link
    link = agent_link.SMLink("http://127.0.0.1:1")
    monkeypatch.delenv("SM_MCP_MODE", raising=False)
    assert "X-SM-Mode" not in link._headers()
    monkeypatch.setenv("SM_MCP_MODE", "readonly")
    assert link._headers()["X-SM-Mode"] == "readonly"


def test_the_readonly_backend_sets_the_bridge_mode():
    src = (_PKG / "core" / "agent_backend.py").read_text(encoding="utf-8")
    assert "SM_MCP_MODE" in src and "readonly" in src


def test_a_readonly_bridge_offers_no_tool_that_posts(monkeypatch):
    """Every POST of a read-only bridge is refused, so a tool whose function
    posts is not listed there, and the read-only allow-list names none."""
    import inspect
    from quam_state_manager import mcp
    from quam_state_manager.core import agent_backend
    posting = {n for n, (_spec, fn) in mcp.TOOLS.items() if re.search(r"\bpost_\w+\(", inspect.getsource(fn))}
    assert {"cz_gate_plan", "propose_additions", "state_edit"} <= posting, "the scan must see the posting tools"
    monkeypatch.setattr(mcp, "READ_ONLY", True)
    visible = set(mcp._visible_tools())
    assert not (posting & visible), sorted(posting & visible)
    assert visible, "a read-only bridge still reads"
    assert not (posting & set(agent_backend.READ_TOOLS)), sorted(posting & set(agent_backend.READ_TOOLS))
    assert set(agent_backend.READ_TOOLS) <= visible, sorted(set(agent_backend.READ_TOOLS) - visible)
    monkeypatch.setattr(mcp, "READ_ONLY", False)
    assert set(mcp._visible_tools()) == set(mcp.TOOLS)
