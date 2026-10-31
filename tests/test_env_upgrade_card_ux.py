"""The env-upgrade card as a person sees it: plain states, real names, tables."""
import json
import re
import sys

import pytest

from quam_state_manager.core import env_upgrade
from quam_state_manager.web.app import create_app
from quam_state_manager.web import env_upgrade_view as view, routes


@pytest.fixture
def loaded(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps({"qubits": {}}), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(folder)})
    ctx = app.config["contexts"][app.config["active_context"]]
    return app, client, ctx


def _button(html, label):
    return re.search(r"<button[^>]*>" + re.escape(label), html).group(0)


def test_no_env_says_what_to_do_and_disables_env_buttons(loaded, monkeypatch):
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda inst: None)
    _, client, _ = loaded
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert "No Python environment selected" in html and "Generate Config" in html
    assert "could not check" not in html and "report not recorded" not in html
    assert "Checked in:" not in html
    for label in ("Check this env", "Record reference"):
        tag = _button(html, label)
        assert "disabled" in tag and "title=" in tag
    answer = client.post("/env-upgrade/check", json={})
    assert answer.status_code == 400 and "No Python environment" in answer.get_json()["error"]


def test_selected_but_unchecked_reads_pending_not_failure(loaded, monkeypatch):
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda inst: sys.executable)
    _, client, ctx = loaded
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert "Not checked yet" in html and "report not recorded" not in html
    assert "()" not in html.split("Checked in:")[1].split("</p>")[0]
    key = view.env_seen.chip_key(ctx["path"])
    routes._env_upgrade_inflight.add(key + "|selected")
    try:
        html = client.get("/env-upgrade/card").get_data(as_text=True)
        assert "Checking" in html and "Not checked yet" not in html
        assert "disabled" in _button(html, "Check this env")
        assert client.get("/env-upgrade/status").get_json()["checking"] is True
        card = client.get("/diagnostics/env-card?poll=1")
        assert 'hx-trigger="every 2s"' in card.get_data(as_text=True)
        assert "HX-Trigger" not in card.headers
    finally:
        routes._env_upgrade_inflight.discard(key + "|selected")
    assert client.get("/env-upgrade/status").get_json()["checking"] is False


def _report_ctx(ctx, *, changed, case6=None, signature_rows=()):
    report = env_upgrade.report({"qubits": {}}, {"schema_format": 4, "classes": {}}, None,
                                event={"changed": changed, "first": False, "previous": {}})
    for n in range(1, 6):
        report["cases"][str(n)] = {"case": n, "rows": [], "status": "none_found", "message": "none found"}
    report["cases"]["5"]["rows"] = list(signature_rows)
    if case6:
        report["cases"]["6"] = case6
    ctx["env_upgrade_report"] = report
    ctx["env_upgrade_report_seq"] = ctx["store"].mutation_seq
    ctx["env_upgrade_env"] = sys.executable
    ctx["env_upgrade_body"] = {"versions": {"quam": "0.6.0"}}


def test_no_transition_is_one_compact_line_with_user_names(loaded):
    _, client, ctx = loaded
    _report_ctx(ctx, changed=False)
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert "No environment change since" in html
    assert '<details class="env-upgrade-details ui-disclosure">' in html
    assert "Case 2" not in html and "Case 6" not in html
    assert "New required fields: none found" in html
    assert "No reference from before this change" not in html
    assert '<code class="ui-chip">quam 0.6.0</code>' in html   # versions are chips in the card head (E1)


def test_transition_shows_groups_signature_table_and_no_reference_sentence(loaded):
    _, client, ctx = loaded
    row = {"class": "lab.Gate", "method": "apply", "label": "parent signature changed since the reference",
           "own": {"text": "(self, a)"}, "parent": {"text": "(self, a, *, b)"},
           "reference_parent": {"text": "(self, a)"}}
    _report_ctx(ctx, changed=True, signature_rows=[row])
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert "env-upgrade-details" not in html and "No reference from before this change" in html
    table = html.split('class="env-sig-table ui-table ui-table--kv"')[1].split("</table>")[0]
    assert table.count("<tr>") == 3 and "reference parent" in table and ";" not in table


def test_waveform_compare_is_a_table_with_identical_rows_folded(loaded):
    _, client, ctx = loaded
    rows = [{"element": f"q{i}", "operation": "x", "channel": "I", "two_qubit": False,
             "label": "bit-identical", "onset_lag": 0, "length_difference": 0, "relative_deviation": 0.0}
            for i in range(47)]
    rows.append({"element": "q1.z", "operation": "cz", "channel": "single", "two_qubit": True,
                 "label": "differs", "onset_lag": None, "onset_reason": "all-zero waveform",
                 "length_difference": 0, "relative_deviation": 0.5})
    _report_ctx(ctx, changed=True, case6={"case": 6, "rows": rows, "status": "found", "message": "x"})
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    # R2 change 8: three counts, never "N of M differ"
    assert "1 differ \u00b7 47 identical \u00b7 0 not comparable" in html
    assert not re.search(r"\d+ of \d+ differ", html)
    assert "47 of 48 bit-identical" in html
    table = html.split('class="env-wave-table ui-table"')[1].split("</table>")[0]
    assert "q1.z / cz" in table and "q3 / x" not in table
    assert "None" not in table and "all-zero waveform" in table
    assert "not measured hardware timing" in html
    rows[-1]["label"] = "bit-identical"
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert "All 48 waveforms are bit-identical to the reference" in html


def test_references_show_env_name_local_minute_and_size(loaded, monkeypatch):
    _, client, _ = loaded
    monkeypatch.setattr(view.waveform_refs, "list_references", lambda inst, key: [{
        "key": "a" * 64, "python_path": r"c:\envs\labenv\python.exe",
        "recorded_at": "2026-10-30T01:02:03.456789+00:00",
        "size": 924191, "traces": {"x": {}, "y": {}}, "files": {"state.json": "d"}, "other_json": []}])
    monkeypatch.setattr(view.config_generator, "discover_envs",
                        lambda: [{"name": "labenv", "python": r"c:\envs\labenv\python.exe"}])
    html = client.get("/env-upgrade/references").get_data(as_text=True)
    assert re.search(r"<code[^>]*>labenv</code>", html) and "903 KB" in html and "924191" not in html
    assert re.search(r"2026-10-30 \d\d:\d\d\b", html) and ".456789" not in html
    assert 'title="' + "a" * 64 in html
    assert '<select class="env-reference-python">' in html and 'type="text" value=' not in html


def test_compare_refuses_an_interpreter_sm_did_not_discover(loaded, monkeypatch):
    _, client, ctx = loaded
    monkeypatch.setattr(view.waveform_refs, "load", lambda inst, key: {
        "chip_key": view.env_seen.chip_key(ctx["path"]), "python_path": "x"})
    monkeypatch.setattr(view.config_generator, "known_interpreter", lambda *a, **k: False)
    spawned = []
    monkeypatch.setattr(view.waveform_refs, "compare", lambda *a, **k: spawned.append(a))
    answer = client.post("/env-upgrade/compare", json={"reference": "a" * 64, "python": r"c:\other\python.exe"})
    assert answer.status_code == 400 and not spawned


def test_record_reference_status_names_the_partial_config(loaded):
    app, _, ctx = loaded
    ctx["env_upgrade_body"] = {"versions": {}}
    ctx["env_upgrade_env"] = sys.executable
    outcome = {"ok": True, "result": {"warnings": ["w1", "w2"], "config": {}},
               "reference_inputs": {"state.json": "e30="}}
    with app.app_context():
        routes._record_env_waveform(ctx, app.instance_path, sys.executable, outcome)
    status = ctx["env_reference_status"]
    assert status.startswith("Reference not recorded") and "2 warning" in status


def test_env_upgrade_compare_selfcheck():
    """F14: Compare swaps without a targetless htmx event (tests/env_upgrade_compare_selfcheck.cjs)."""
    import shutil
    import subprocess
    from pathlib import Path
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(["node", str(root / "tests" / "env_upgrade_compare_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(root), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr


def test_shared_after_swap_listener_tolerates_a_targetless_swap():
    from pathlib import Path
    base = (Path(__file__).resolve().parent.parent / "quam_state_manager/web/templates/base.html").read_text(encoding="utf-8")
    head = base.split('scope.listen(document.body, "htmx:afterSwap", function(evt) {', 1)[1][:200]
    assert "if (!evt.detail || !evt.detail.target) return;" in head


# ---- second GUI round ------------------------------------------------------

def test_g1_card_keeps_polling_while_a_reference_records(loaded, monkeypatch):
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda inst: sys.executable)
    _, client, ctx = loaded
    folder_key = str(ctx["path"]).lower()
    ctx["env_reference_status"] = "recording reference..."
    routes._cfg_warm_inflight.add(folder_key)
    try:
        card = client.get("/diagnostics/env-card?poll=1")
        assert 'hx-trigger="every 2s"' in card.get_data(as_text=True)
        assert "HX-Trigger" not in card.headers
    finally:
        routes._cfg_warm_inflight.discard(folder_key)
    ctx["env_reference_status"] = "Reference recorded: 3 waveforms, 1 KB"
    card = client.get("/diagnostics/env-card?poll=1")
    html = card.get_data(as_text=True)
    assert 'hx-trigger="every 2s"' not in html and "Reference recorded: 3 waveforms" in html
    assert card.headers.get("HX-Trigger") == "diagnostics-changed"


def test_g2_header_names_the_same_versions_as_the_card(loaded, monkeypatch):
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda inst: sys.executable)
    monkeypatch.setattr(routes, "_live_env_manifest",
                        lambda store: {"versions": {"quam_builder": "0.4.0"}, "missing_classes": []})
    _, client, ctx = loaded
    _report_ctx(ctx, changed=True)
    ctx["env_upgrade_body"] = {"versions": {"quam_builder": "0.6.0"}}
    html = client.get("/diagnostics/env-card").get_data(as_text=True)
    header = html.split("checked against", 1)[1].split("</span>", 1)[0]
    assert "quam_builder 0.6.0" in header and "0.4.0" not in header


def _transition_ctx(ctx):
    _report_ctx(ctx, changed=True, signature_rows=[{
        "class": "lab.Gate", "method": "apply", "label": "x",
        "own": {"text": "(self)"}, "parent": {"text": "(self)"}, "reference_parent": {"text": "(self)"}}])
    ctx["env_upgrade_report"]["event"]["previous"] = {"versions": {"quam": "0.5.0"}}


def test_g3_existing_pre_upgrade_reference_is_named_not_denied(loaded, monkeypatch):
    _, client, ctx = loaded
    _transition_ctx(ctx)
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert "install it in a separate env" in html
    monkeypatch.setattr(view.waveform_refs, "list_references", lambda inst, key: [{
        "key": "k", "recorded_at": "2026-10-01T00:00:00+00:00", "versions": {"quam": "0.5.0"},
        "python_path": view.baseline.normalize_env_path(sys.executable), "size": 2048, "traces": {}}])
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert "install it in a separate env" not in html
    assert "reference from before the change is recorded" in html
    assert "compare has not run yet" in html and "No reference from before" not in html


def test_g4_g5_no_env_says_it_once_and_hides_the_probe_footer(loaded, monkeypatch):
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda inst: None)
    _, client, _ = loaded
    html = client.get("/diagnostics/env-card").get_data(as_text=True)
    visible = re.sub(r"<[^>]+>", " ", html)   # tooltips are attributes, not sentences
    assert visible.lower().count("no python environment selected") == 1
    assert "classes not probed" not in html
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda inst: sys.executable)
    assert "classes not probed" in client.get("/diagnostics/env-card").get_data(as_text=True)


def test_g6_static_scripts_carry_a_content_based_version(loaded):
    from pathlib import Path
    templates = Path(__file__).resolve().parent.parent / "quam_state_manager/web/templates"
    offenders = [t.name for t in templates.glob("*.html")
                 if re.search(r"url_for\(\s*['\"]static['\"]\s*,\s*filename\s*=\s*['\"][^'\"]+\.js", t.read_text(encoding="utf-8"))]
    assert offenders == []
    _, client, _ = loaded
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    assert re.search(r"env-upgrade\.js\?v=\d+", html)


def test_w2_card_polls_and_reads_comparing_while_the_compare_runs(loaded, monkeypatch):
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda inst: sys.executable)
    _, client, ctx = loaded
    _transition_ctx(ctx)
    monkeypatch.setattr(view.waveform_refs, "list_references", lambda inst, key: [{
        "key": "k", "recorded_at": "2026-10-01T00:00:00+00:00", "versions": {"quam": "0.5.0"},
        "python_path": view.baseline.normalize_env_path(sys.executable), "size": 2048, "traces": {}}])
    ctx["env_compare_running"] = True
    card = client.get("/diagnostics/env-card?poll=1")
    html = card.get_data(as_text=True)
    assert 'hx-trigger="every 2s"' in html and "Comparing waveforms" in html
    assert "compare has not run yet" not in html and "HX-Trigger" not in card.headers
    ctx["env_compare_running"] = False
    card = client.get("/diagnostics/env-card?poll=1")
    assert 'hx-trigger="every 2s"' not in card.get_data(as_text=True)
    assert card.headers.get("HX-Trigger") == "diagnostics-changed"


def test_w2_compare_runs_off_the_request_and_clears_its_flag(loaded, monkeypatch):
    import threading
    _, _, ctx = loaded
    _report_ctx(ctx, changed=True)
    done = threading.Event()
    monkeypatch.setattr(view.env_upgrade_service, "compare_matched",
                        lambda *a: (done.wait(5), {"status": "none_found", "attempted": True, "rows": [],
                                                   "message": "ok"})[1])
    ctx["env_compare_running"] = True
    view._start_compare(ctx, "inst", "key", sys.executable, {}, {}, None, "ref")
    assert ctx["env_compare_running"] is True
    done.set()
    import time
    for _ in range(100):
        if not ctx["env_compare_running"]:
            break
        time.sleep(0.05)
    assert ctx["env_compare_running"] is False
    assert ctx["env_upgrade_report"]["cases"]["6"]["attempted"] is True


def test_w3_state_hashes_become_plain_words_with_a_tooltip(loaded, monkeypatch):
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda inst: sys.executable)
    _, client, ctx = loaded
    _report_ctx(ctx, changed=True, case6={
        "case": 6, "rows": [], "status": "none_found", "message": "x", "attempted": True,
        "reference_state_hash": "411b3f55c8", "current_state_hash": "2ede0db29b", "current_env": "venv_up"})
    ctx["env_upgrade_body"] = {"versions": {"quam_builder": "0.6.0"}}
    html = client.get("/env-upgrade/card").get_data(as_text=True)
    visible = re.sub(r"<[^>]+>", " ", html)
    assert "411b3f55c8" not in visible and "2ede0db29b" not in visible
    assert "Reference inputs regenerated in" in visible and "venv_up" in visible
    assert "quam_builder 0.6.0" in visible
    assert 'title="State 411b3f55c8' in html and "2ede0db29b" in html


def _ref(key, recorded, versions, python=r"c:\envs\venv_up\python.exe"):
    return {"key": key, "python_path": python, "recorded_at": recorded, "versions": versions,
            "official_digest": "d", "source_fingerprint": "s", "size": 1024, "traces": {}, "files": {}}


def test_reference_rows_show_versions_and_tag_the_before_reference(loaded, monkeypatch):
    _, client, _ = loaded
    old, new = {"quam_builder": "0.4.0"}, {"quam_builder": "0.6.0"}
    refs = [_ref("b" * 64, "2026-10-31T01:21:00+00:00", new), _ref("a" * 64, "2026-10-31T01:19:00+00:00", old)]
    monkeypatch.setattr(view.waveform_refs, "list_references", lambda inst, key: refs)
    rows = view.present_references("i", "k", r"c:\envs\venv_up\python.exe", {**_ref("x", "", old)})
    assert [r["is_before"] for r in rows] == [False, True]
    assert rows[0]["versions_text"] == "quam_builder 0.6.0" and rows[1]["versions_text"] == "quam_builder 0.4.0"
    assert not any(r["is_before"] for r in view.present_references("i", "k"))
    monkeypatch.setattr(view.config_generator, "discover_envs", lambda: [])
    html = client.get("/env-upgrade/references").get_data(as_text=True)
    assert "quam_builder 0.6.0" in html and "quam_builder 0.4.0" in html
    assert "before the upgrade" not in html  # no transition on this chip


def test_before_tag_renders_only_for_the_marked_row(loaded):
    html = loaded[0].jinja_env.get_template("_env_upgrade_references.html").render(
        references=[{"key": "a", "env_name": "e", "python_path": "p", "local_time": "t", "waveforms": 1,
                     "size_text": "1 KB", "files": [], "other_json": [], "versions_text": "quam 0.5.0",
                     "is_before": flag} for flag in (True, False)], envs=[], selected="", selected_name="")
    assert html.count("before the upgrade") == 1 and html.count("quam 0.5.0") == 2


def test_custom_interpreter_option_shows_folder_name_with_full_path_in_title(loaded, monkeypatch):
    _, client, _ = loaded
    custom = r"c:\somewhere\venv_up\Scripts\python.exe"
    monkeypatch.setattr(routes.config_generator, "get_selected_env", lambda inst: custom)
    monkeypatch.setattr(view.config_generator, "discover_envs", lambda: [])
    monkeypatch.setattr(view.waveform_refs, "list_references",
                        lambda inst, key: [_ref("a" * 64, "2026-10-31T01:19:00+00:00", {}, custom)])
    html = client.get("/env-upgrade/references").get_data(as_text=True)
    option = re.search(r"<option[^>]*selected[^>]*>[^<]*</option>", html).group(0)
    assert ">venv_up</option>" in option and "title=\"" + custom in option
    assert ">" + custom + "<" not in option


def test_before_tag_follows_the_from_versions_with_an_upgrade_in_one_env(loaded, monkeypatch):
    """Two references in one env, recorded before then after an upgrade; the
    tag goes on the one whose versions equal the transition's FROM side."""
    _, client, ctx = loaded
    env = r"c:\envs\venv_up\python.exe"
    base = {"quam": "0.6.0", "qualang_tools": "0.22.0", "qm": "1.4.1", "numpy": "2.5.3", "scipy": "1.18.1"}
    before = {**base, "quam_builder": "0.4.0"}
    after = {**base, "quam_builder": "0.6.0"}
    refs = [_ref("b" * 64, "2026-10-31T01:21:00+00:00", after, env),
            _ref("a" * 64, "2026-10-31T01:19:00+00:00", before, env)]
    monkeypatch.setattr(view.waveform_refs, "list_references", lambda inst, key: refs)
    for tagged_versions, expected in ((before, [False, True]), (after, [True, False])):
        rows = view.present_references("i", "k", env, {"versions": tagged_versions, "official_digest": "d",
                                                       "source_fingerprint": "s"})
        assert [r["is_before"] for r in rows] == expected
    none = view.present_references("i", "k", env, {"versions": {**base, "quam_builder": "0.5.0"},
                                                   "official_digest": "d", "source_fingerprint": "s"})
    assert not any(r["is_before"] for r in none)
    # only the differing package is shown, the full list sits in the title
    assert [r["versions_text"] for r in none] == ["quam_builder 0.6.0", "quam_builder 0.4.0"]
    assert "numpy 2.5.3" in none[0]["versions_full"]
