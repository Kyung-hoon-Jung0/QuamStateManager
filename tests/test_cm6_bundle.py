"""The vendored CodeMirror 6 bundle (D-21): present, licensed, a page bundle of
the Json Tree page only, and loadable under jsdom."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_bundles import client, _manifest, _scripts  # noqa: F401  (fixture)

_ROOT = Path(__file__).resolve().parent.parent
_DIR = _ROOT / "quam_state_manager" / "web" / "static" / "vendor" / "cm6"
_F = "vendor/cm6/cm6.min.js"


def test_bundle_and_license_exist():
    assert (_DIR / "cm6.min.js").stat().st_size > 100_000
    lic = (_DIR / "LICENSE").read_text(encoding="utf-8")
    assert "Permission is hereby granted" in lic and "@codemirror/view" in lic
    assert (_ROOT / "tools" / "cm6" / "package-lock.json").exists()


def test_not_a_core_script():
    base = (_ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert not re.search(r"<script src=\"\{\{ asset_url\('vendor/cm6", base)


def test_only_the_json_tree_page_lists_it(client):  # noqa: F811
    man = _manifest(client.get("/explorer").get_data(as_text=True))
    assert man["bundles"]["cm6"] == [_F]
    assert "cm6" in man["pages"]["explorer"]
    assert [p for p, b in man["pages"].items() if "cm6" in b] == ["explorer"]
    _, lazy = _scripts(client.get("/explorer").get_data(as_text=True))
    assert _F in lazy
    names, _ = _scripts(client.get("/bulk").get_data(as_text=True))
    assert _F not in names


def test_bundle_creates_an_editor_under_jsdom():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(_ROOT / "tests" / "cm6_bundle_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if "Cannot find module 'jsdom'" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok (" in r.stdout
