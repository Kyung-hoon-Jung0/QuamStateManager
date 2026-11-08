"""FB-03 / FB-04 / FB-05: Diagnostics groups same-kind findings, lets the user
acknowledge any warning or suggestion (never an error) tied to its values,
offers two named-direction fixes with a one-undo "Set all N", and gives every
finding with a state/wiring path a Go to field button."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.core import diag_ack
from quam_state_manager.core import diagnostics as diag
from quam_state_manager.core.diagnostics import Finding, summarize
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def _chip(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    qubits = {}
    for i, delta in enumerate((150e3, 300e3, 550e3), start=1):
        qubits[f"q{i}"] = {"id": f"q{i}", "f_01": 5.0e9 + i * 1e8,
                           "xy": {"RF_frequency": 5.0e9 + i * 1e8},
                           "resonator": {"f_01": 7.0e9 + i * 1e7 + delta,
                                         "RF_frequency": 7.0e9 + i * 1e7}}
    state = {"qubits": qubits, "active_qubit_names": list(qubits),
             "qubit_pairs": {"q1-q2": {"id": "q1-q2", "macros": {"cz": {
                 "fidelity": {"IRB": 1.53447}}}}}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    return folder


@pytest.fixture
def loaded(tmp_path):
    chip = _chip(tmp_path / "chip")
    inst = tmp_path / "_inst"
    app = create_app(testing=True, instance_path=str(inst))
    client = app.test_client()
    assert client.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    name = app.config["active_context"]
    return {"app": app, "client": client, "chip": chip, "inst": inst,
            "store": app.config["contexts"][name]["store"]}


def _page(client) -> str:
    return client.get("/diagnostics", headers={"HX-Request": "true"}).get_data(as_text=True)


def _findings(client):
    return client.get("/diagnostics/findings.json").get_json()


def _freq_keys(loaded):
    from quam_state_manager.web import routes
    with loaded["app"].test_request_context():
        found = routes._active_chip_findings(loaded["store"])
    return [f for f in found if f.category == "value_freq_consistency" and f.severity == "warning"]


# ── FB-03 grouping ───────────────────────────────────────────────────────

def test_same_kind_findings_are_one_collapsible_row(loaded):
    html = _page(loaded["client"])
    heads = re.findall(r'data-group-head="([0-9a-f]+)"', html)
    assert heads, "no group row rendered"
    freq_head = [h for h in heads if f'data-group-head="{h}" data-category="value_freq_consistency"' in html]
    assert len(freq_head) == 1
    key = freq_head[0]
    members = re.findall(r'<tr class="diag-row[^"]*diag-row-in-group[^"]*"\s+data-bucket="warning" '
                         r'data-category="value_freq_consistency" data-group="' + key + '" hidden>', html)
    assert len(members) == 3, len(members)
    assert "3 places" in html
    # honest counts: instances AND kinds
    assert re.search(r"\d+ in \d+ kinds?", html)


def test_grouping_reaches_every_level():
    rows = [Finding("info", "pointer_optional_default", f"qubits.q{i}.x", f"q{i} x is a default")
            for i in range(3)]
    rows += [Finding("error", "waveform_range", f"qubits.q{i}.amp", f"sample {i}.5 > 1") for i in range(2)]
    groups = diag.finding_groups(rows)
    assert sorted(len(g["items"]) for g in groups) == [2, 3]


# ── FB-04 two directions + bulk ─────────────────────────────────────────

def test_each_item_names_both_directions_without_arrows_or_confirm(loaded):
    html = _page(loaded["client"])
    assert html.count("Set f_01 to RF_frequency (") >= 3
    assert html.count("Set RF_frequency to f_01 (") >= 3
    assert "Set all 3 f_01 to RF_frequency" in html
    assert "Set all 3 RF_frequency to f_01" in html
    assert "data-confirm" not in html
    assert "Update f_01" not in html
    for label in re.findall(r'onclick="applyDiagFix\(this\)">([^<]+)<', html):
        assert "→" not in label and "->" not in label, label
    js = (ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
    block = js[js.index("    /* FB-04: a fix press is the consent"):js.index("    /* QA diagnostics-r2-20")]
    assert "confirm(" not in block and "alert(" not in block


def test_set_all_is_one_undo_group_and_one_ctrl_z_restores_all(loaded):
    client, store = loaded["client"], loaded["store"]
    before = {q: store.merged["qubits"][q]["resonator"]["f_01"] for q in ("q1", "q2", "q3")}
    items = [f.fixes[0] for f in _freq_keys(loaded)]
    assert len(items) == 3 and all(i["kind"] == "f01_to_rf" for i in items)
    r = client.post("/diagnostics/apply-fix", json={"items": items})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert len(body["changed"]) == 3 and body["tray_html"]
    for q in before:
        res = store.merged["qubits"][q]["resonator"]
        assert res["f_01"] == res["RF_frequency"]
    assert len({e.group_id for e in store.change_log}) == 1
    client.post("/undo")
    assert {q: store.merged["qubits"][q]["resonator"]["f_01"] for q in before} == before


def test_the_other_direction_changes_what_the_hardware_plays(loaded):
    client, store = loaded["client"], loaded["store"]
    f = _freq_keys(loaded)[0]
    alt = [x for x in f.fixes if x["kind"] == "rf_to_f01"][0]
    assert client.post("/diagnostics/apply-fix", json={"items": [alt]}).status_code == 200
    q = alt["dot_path"].split(".")[1]
    res = store.merged["qubits"][q]["resonator"]
    assert res["RF_frequency"] == res["f_01"]


def test_a_tampered_item_refuses_the_whole_press(loaded):
    client, store = loaded["client"], loaded["store"]
    items = [f.fixes[0] for f in _freq_keys(loaded)]
    items[1] = {**items[1], "value": "1.0"}
    before = json.dumps(store.merged, sort_keys=True)
    assert client.post("/diagnostics/apply-fix", json={"items": items}).status_code == 409
    assert json.dumps(store.merged, sort_keys=True) == before and not store.change_log


# ── FB-03 acknowledge ───────────────────────────────────────────────────

def test_ack_group_stops_counting_and_a_value_change_brings_one_back(loaded):
    client, store, app = loaded["client"], loaded["store"], loaded["app"]
    keys = [f.vack_key for f in _freq_keys(loaded)]
    assert len(keys) == 3 and all(keys)
    r = client.post("/diagnostics/ack", json={"keys": keys})
    assert r.get_json() == {"ok": True, "acknowledged": 3}
    assert not [f for f in _freq_keys(loaded) if not f.acknowledged]
    html = _page(client)
    assert "Acknowledged (3)" in html
    # stored in SM's instance folder, never in the chip
    assert list((loaded["inst"] / "diag_acks").glob("*.json"))
    assert "diag_ack" not in (loaded["chip"] / "state.json").read_text(encoding="utf-8")
    # a later value change on ONE qubit brings that one back
    client.post("/field/edit", data={"dot_path": "qubits.q2.resonator.f_01", "value": "7020400000.0"})
    back = [f for f in _freq_keys(loaded) if not f.acknowledged]
    assert [f.location for f in back] == ["qubits.q2.resonator.f_01"]
    # un-acknowledge
    client.post("/diagnostics/ack", json={"keys": keys, "revoke": True})
    assert all(not f.acknowledged for f in _freq_keys(loaded))


def test_ack_survives_a_restart(loaded, tmp_path):
    client = loaded["client"]
    keys = [f.vack_key for f in _freq_keys(loaded)]
    client.post("/diagnostics/ack", json={"keys": keys})
    app2 = create_app(testing=True, instance_path=str(loaded["inst"]))
    c2 = app2.test_client()
    c2.post("/load", data={"folder": str(loaded["chip"])})
    html = c2.get("/diagnostics", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "Acknowledged (3)" in html


def test_errors_cannot_be_acknowledged(loaded):
    err = Finding("error", "waveform_range", "qubits.q1.amp", "sample 1.2 > 1", jump_path="qubits.q1.f_01")
    diag_ack.stamp([err], str(loaded["inst"]), "chip", loaded["store"])
    assert not err.vack_key
    n = diag_ack.acknowledge(str(loaded["inst"]), "chip", [err], [diag_ack.identity(err)], loaded["store"])
    assert n == 0


def test_acknowledged_warnings_drop_out_of_the_counts(loaded):
    client = loaded["client"]
    before = summarize(_findings_objs(loaded))
    keys = [f.vack_key for f in _freq_keys(loaded)]
    client.post("/diagnostics/ack", json={"keys": keys})
    after = summarize(_findings_objs(loaded))
    assert after["warning"] == before["warning"] - 3
    assert after["acknowledged"] == before["acknowledged"] + 3


def _findings_objs(loaded):
    from quam_state_manager.web import routes
    with loaded["app"].test_request_context():
        return routes._active_chip_findings(loaded["store"])


def _warning_categories() -> list[str]:
    """Every check id that can be raised at warning (or suggestion) level."""
    src = (ROOT / "quam_state_manager/core/diagnostics.py").read_text(encoding="utf-8")
    src += (ROOT / "quam_state_manager/web/routes.py").read_text(encoding="utf-8")
    cats = set(re.findall(r'Finding\(\s*\n?\s*(?:"warning"|"info"|severity|sev|level)\s*,\s*\n?\s*"([a-z_.0-9]+)"', src))
    cats |= set(re.findall(r'category="([a-z_.0-9]+)"', src))
    return sorted(c for c in cats if not c.startswith("env_"))


def test_every_warning_check_renders_the_ack_control(loaded):
    """The enumeration pin: every check id the linters can raise at warning
    level carries "I know - intended" when rendered."""
    cats = _warning_categories()
    assert len(cats) >= 20, cats
    from flask import render_template
    rows = [Finding("warning", c, f"qubits.q1.f_01", f"{c} says something {i}") for i, c in enumerate(cats)]
    diag_ack.stamp(rows, str(loaded["inst"]), "enum", loaded["store"])
    with loaded["app"].test_request_context():
        html = render_template("_diagnostics_list.html", findings=rows,
                               diag_summary=summarize(rows), allow_jump=True, allow_fix=True)
    missing = []
    for c in cats:
        m = re.search(r'data-category="' + re.escape(c) + r'".*?</tr>', html, re.S)
        if not m or "diag-vack" not in m.group(0):
            missing.append(c)
    assert not missing, missing


# ── FB-05 Go to field ───────────────────────────────────────────────────

def test_every_finding_with_a_path_has_go_to_field(loaded):
    found = _findings_objs(loaded)
    irb = [f for f in found if f.category == "value_unphysical"]
    assert irb and irb[0].jump_path == "qubit_pairs.q1-q2.macros.cz.fidelity.IRB"
    store = loaded["store"]
    for f in found:
        try:
            store.get_value(f.location)
        except Exception:  # noqa: BLE001
            continue
        assert f.jump_path, f.category
    html = _page(loaded["client"])
    assert 'data-jump-path="qubit_pairs.q1-q2.macros.cz.fidelity.IRB"' in html
    # never on a group row
    for row in re.findall(r'<tr class="diag-row diag-group-row.*?</tr>', html, re.S):
        assert "diag-goto" not in row


def test_a_finding_without_a_path_says_so():
    from flask import render_template
    app = create_app(testing=True)
    f = Finding("warning", "connectivity_x", "con1 / FEM 2", "instrument-level thing")
    with app.test_request_context():
        html = render_template("_diagnostics_list.html", findings=[f],
                               diag_summary=summarize([f]), allow_jump=True)
    assert "no single field" in html and "diag-goto" not in html
