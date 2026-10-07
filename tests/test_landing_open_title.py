"""docs/301 (F23): the landing's open button says where it goes.

Its title promised "then the Agent home opens"; /qualibrate/open lands on the
Qubits page (docs/63)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_landing_open_button_says_where_it_goes():
    src = (ROOT / "quam_state_manager" / "web" / "templates" / "_landing_shell.html").read_text(encoding="utf-8")
    m = re.search(r'class="btn-sm landing-current-btn"\s+title="([^"]*)"', src)
    assert m and "Qubits page" in m.group(1) and "Agent home" not in m.group(1), m and m.group(1)
    routes_src = (ROOT / "quam_state_manager" / "web" / "routes.py").read_text(encoding="utf-8")
    i = routes_src.index("def qualibrate_open_project(")
    body = routes_src[i:routes_src.index("\n@bp.route", i)]
    assert 'url_for("main.qubits")' in body, "the title follows where the open lands"
