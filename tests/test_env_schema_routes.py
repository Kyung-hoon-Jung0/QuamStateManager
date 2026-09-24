"""The /env-schema/* surface: what the library changed, and teaching SM about
it (docs/79).

The user-facing promise these pin: SM never rewrites a value because a schema
moved, it says what moved and who disagrees, and every correction the user
makes is explicit, scoped to the environment, visible afterwards and
revocable.
"""
import json
from pathlib import Path

import pytest

from quam_state_manager.core import state_env_baseline as seb
from quam_state_manager.core import state_env_schema as ses
from quam_state_manager.core import type_policy as tp
from quam_state_manager.core import type_verdicts as tv
from quam_state_manager.web.app import create_app

_GOLDEN = Path(__file__).resolve().parent / "golden"


def _manifest(name):
    path = _GOLDEN / f"state_schema_{name}.json"
    if not path.exists():
        pytest.skip(f"golden manifest {name} missing")
    return ses._decorate(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture
def modern():
    return _manifest("modern")


@pytest.fixture
def fork():
    return _manifest("fork")


@pytest.fixture
def cls_field(modern):
    for cp, entry in modern["classes"].items():
        for name, f in (entry.get("fields") or {}).items():
            if (f.get("type") or {}).get("base") in ("float", "int"):
                return cp, name
    pytest.skip("no numeric field in the golden manifest")


@pytest.fixture
def env(tmp_path, modern, fork, cls_field):
    """A loaded chip whose store carries the modern manifest, with the fork
    recorded as the PREVIOUS baseline (i.e. the user just upgraded)."""
    cp, field = cls_field
    inst = tmp_path / "_inst"
    inst.mkdir()
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"__class__": cp, field: 5}}}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(
        {"wiring": {}, "network": {"host": "127.0.0.1"}}), encoding="utf-8")
    seb.record_baseline(inst, fork)
    seb.record_baseline(inst, modern)
    app = create_app(testing=True, instance_path=str(inst))
    client = app.test_client()
    client.post("/load", data={"folder": str(chip)})
    ctx = app.config["contexts"][app.config["active_context"]]
    ctx["store"].type_policy = tp.load_policy(inst, chip, modern)
    return {"app": app, "client": client, "ctx": ctx, "inst": inst,
            "cp": cp, "field": field, "path": f"qubits.q1.{field}",
            "versions": modern["versions"], "manifest": modern}


class TestTheChangesSurface:
    def test_it_names_both_environments_and_lists_what_moved(self, env):
        html = env["client"].get("/env-schema/changes").get_data(as_text=True)
        assert "quam 0.5.0a3" in html and "quam 0.6.0" in html
        assert "tfx-row" in html
        assert "SM changes" in html, "the never-automatic promise must be stated"

    def test_a_cold_env_says_so_instead_of_failing(self, tmp_path):
        inst = tmp_path / "_i"
        inst.mkdir()
        chip = tmp_path / "chip"
        chip.mkdir()
        (chip / "state.json").write_text(json.dumps({"qubits": {}}), encoding="utf-8")
        (chip / "wiring.json").write_text(json.dumps(
            {"wiring": {}, "network": {}}), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(inst))
        c = app.test_client()
        c.post("/load", data={"folder": str(chip)})
        r = c.get("/env-schema/changes")
        assert r.status_code == 200
        assert "probed" in r.get_data(as_text=True)


class TestTeachingSM:
    def test_an_override_takes_effect_immediately(self, env):
        r = env["client"].post("/env-schema/verdict", data={
            "class_path": env["cp"], "field": env["field"],
            "decision": "override", "use": "grammar", "type": "str"})
        assert r.status_code == 200 and r.get_json()["ok"]
        store = env["ctx"]["store"]
        exp = store.type_policy.expected_for(store.merged, env["path"])
        assert exp.source == "verdict" and exp.spec["base"] == "str"

    def test_the_blast_radius_is_disclosed_but_never_blocks(self, env):
        """A verdict may itself be the repair path, so a conflicting current
        value is a warning — the same rule as a per-key type assignment."""
        r = env["client"].post("/env-schema/verdict", data={
            "class_path": env["cp"], "field": env["field"],
            "decision": "override", "use": "grammar", "type": "str"})
        body = r.get_json()
        assert body["ok"] is True
        assert body["affected"] == 1 and body["warning"]

    def test_keeping_the_previous_environments_type(self, env, fork):
        """"No — keep treating it as before" needs no type grammar: the old
        baseline's spec is copied verbatim, which is also how union/enum types
        (which the grammar cannot express) stay expressible."""
        key = seb.env_key(fork["versions"])
        cp, field = env["cp"], env["field"]
        body = seb.load_baseline(env["inst"], key)
        if not ((body.get("classes") or {}).get(cp) or {}).get("fields", {}).get(field):
            pytest.skip("that field does not exist in the fork baseline")
        r = env["client"].post("/env-schema/verdict", data={
            "class_path": cp, "field": field, "decision": "override",
            "use": "baseline", "baseline_key": key})
        assert r.status_code == 200 and r.get_json()["ok"]

    def test_a_field_the_environment_does_not_declare_is_refused(self, env):
        r = env["client"].post("/env-schema/verdict", data={
            "class_path": env["cp"], "field": "not_a_real_field",
            "decision": "accept"})
        assert r.status_code == 409
        body = r.get_json()
        assert body["error_kind"] == "unknown_field"
        assert "Quam.load()" in body["error"], "say WHY it cannot be taught away"

    def test_a_bad_type_expression_is_rejected(self, env):
        r = env["client"].post("/env-schema/verdict", data={
            "class_path": env["cp"], "field": env["field"],
            "decision": "override", "use": "grammar", "type": "not-a-type"})
        assert r.status_code == 400


class TestListingAndRevoking:
    def test_taught_types_are_listed_with_their_standing(self, env):
        env["client"].post("/env-schema/verdict", data={
            "class_path": env["cp"], "field": env["field"],
            "decision": "override", "use": "grammar", "type": "str"})
        body = env["client"].get("/env-schema/verdicts?format=json").get_json()
        assert body["count"] == 1
        item = body["verdicts"][0]
        assert item["status"] == "exact" and item["conflicts"] == 1

    def test_revoking_restores_the_environment(self, env):
        env["client"].post("/env-schema/verdict", data={
            "class_path": env["cp"], "field": env["field"],
            "decision": "override", "use": "grammar", "type": "str"})
        item = env["client"].get(
            "/env-schema/verdicts?format=json").get_json()["verdicts"][0]
        r = env["client"].post("/env-schema/verdict/revoke", data={
            "env_key": item["from_env_key"], "class_path": env["cp"],
            "field": env["field"]})
        assert r.get_json()["removed"] is True
        store = env["ctx"]["store"]
        assert store.type_policy.expected_for(store.merged, env["path"]).source == "env"


class TestFindingsFollowTheOverlay:
    def test_saving_a_verdict_does_not_serve_stale_findings(self, env):
        """The memo key folds in the overlay signature — without that, the
        findings the user just answered would keep coming back."""
        from quam_state_manager.core import state_env_validate as sev
        store = env["ctx"]["store"]
        before = sev._manifest_key(store.type_policy.manifest)
        env["client"].post("/env-schema/verdict", data={
            "class_path": env["cp"], "field": env["field"],
            "decision": "override", "use": "grammar", "type": "str"})
        after = sev._manifest_key(store.type_policy.manifest)
        assert before != after


class TestDismissal:
    def test_it_is_env_scoped_and_delta_gated(self, env):
        transition = seb.env_transition(env["inst"], env["manifest"])
        r = env["client"].post("/env-schema/dismiss", data={
            "from_key": transition["from_key"], "to_key": transition["to_key"],
            "sig": transition["sig"]})
        assert r.status_code == 200
        assert seb.env_transition(env["inst"], env["manifest"])["dismissed"] is True

    # QA diagnostics-r2-17: the dismissal was stored and never read -- the
    # button had no visible effect. The count stays (docs/79: a dismissal
    # never hides the fact); the card says the set is hidden and stops
    # calling for a review, and the overlay stops offering the same action.
    def _dismiss(self, env, sig=None):
        t = seb.env_transition(env["inst"], env["manifest"])
        return t, env["client"].post("/env-schema/dismiss", data={
            "from_key": t["from_key"], "to_key": t["to_key"],
            "sig": t["sig"] if sig is None else sig})

    def test_the_card_says_the_set_is_hidden_and_keeps_the_count(self, env):
        t, r = self._dismiss(env)
        assert r.status_code == 200
        html = env["client"].get("/diagnostics/types-card").get_data(as_text=True)
        count = (t["diff"] or {}).get("total")
        assert f"<strong>{count}</strong> schema" in html     # the fact stays
        assert "you hid this set" in html
        assert "Review again" in html and "openEnvSchemaChanges" in html
        assert f"Review {count} schema change" not in html

    def test_the_overlay_stops_offering_the_same_action(self, env):
        before = env["client"].get("/env-schema/changes").get_data(as_text=True)
        assert "envSchemaDismiss" in before and "You hid this set" not in before
        self._dismiss(env)
        after = env["client"].get("/env-schema/changes").get_data(as_text=True)
        assert "envSchemaDismiss" not in after
        assert "You hid this set" in after

    def test_a_stale_signature_hides_nothing(self, env):
        t, r = self._dismiss(env, sig="not-the-current-set")
        assert r.status_code == 200
        html = env["client"].get("/diagnostics/types-card").get_data(as_text=True)
        assert "you hid this set" not in html
        assert f"Review {(t['diff'] or {}).get('total')} schema change" in html

    def test_a_memo_that_was_not_written_is_not_a_200(self, env):
        r = env["client"].post("/env-schema/dismiss", data={
            "from_key": "", "to_key": "", "sig": ""})
        assert r.status_code == 400


class TestTheDiagnosticsCardSurfacesIt:
    def test_the_card_offers_the_review_and_the_manage_entry_points(self, env):
        html = env["client"].get("/diagnostics/types-card").get_data(as_text=True)
        assert "openEnvSchemaChanges" in html
        env["client"].post("/env-schema/verdict", data={
            "class_path": env["cp"], "field": env["field"],
            "decision": "override", "use": "grammar", "type": "str"})
        html = env["client"].get("/diagnostics/types-card").get_data(as_text=True)
        assert "openEnvSchemaVerdicts" in html and "taught SM" in html


def test_env_dismiss_convert_selfcheck():
    """QA diagnostics-r2-17 + F-O client halves: a dismissal refreshes the
    types card (or says it failed), and a failed type-fix apply keeps the live
    count span (tests/env_dismiss_convert_selfcheck.cjs)."""
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(["node", str(root / "tests" / "env_dismiss_convert_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(root), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL OK" in r.stdout, r.stdout + r.stderr
