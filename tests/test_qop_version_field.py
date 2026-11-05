"""QOP choices survive rebuilds; caveats reach wizard and script users."""
from pathlib import Path

from quam_state_manager.core.regen_spec import reconstruct_spec
from tests.test_port_identity import state, wiring

_ROOT = Path(__file__).resolve().parent.parent


def test_a_chip_that_records_its_qop_version_carries_it_into_the_spec():
    s = state()
    s.setdefault("extras", {})["qop_version"] = "3.4.2"
    assert reconstruct_spec(s, wiring()).spec["qop_version"] == "3.4.2"


def test_an_unversioned_chip_reconstructs_with_no_qop_key():
    s = state()
    s.pop("extras", None)
    assert "qop_version" not in reconstruct_spec(s, wiring()).spec


def test_the_build_result_carries_the_port_sharing_caveat():
    src = (_ROOT / "quam_state_manager" / "generator" / "run_build.py").read_text(encoding="utf-8")
    body = src[src.index("        machine.save()\n"):]
    body = body[:body.index("# 4. Copy ONLY the two artefacts")]
    assert '_record_qop' in body


def test_rebuilt_chip_records_qop_and_nonwizard_build_gets_caveat(tmp_path):
    import json
    from quam_state_manager.generator.run_build import _record_qop
    s = state()
    s['ports'] = {'mw_outputs': {'con1': {'1': {'2': {'band': 2}, '3': {'band': 2}}}}}
    dest = tmp_path / 'state.json'
    dest.write_text(json.dumps(s), encoding='utf-8')
    warnings = []
    _record_qop(dest, s, {'qop_version': '3.4.2'}, warnings)
    saved = json.loads(dest.read_text(encoding='utf-8'))
    assert saved['extras']['qop_version'] == '3.4.2'
    assert reconstruct_spec(saved, wiring()).spec['qop_version'] == '3.4.2'
    assert len(warnings) == 1 and 'stop playing' in warnings[0]
    warnings.clear()
    _record_qop(dest, s, {'qop_version': '3.7'}, warnings)
    assert not warnings
    # Same-port sharing also needs the caveat; an unset version cannot dismiss it.
    s['ports']['mw_outputs']['con1']['1'].pop('3')
    _record_qop(dest, s, {}, warnings)
    assert len(warnings) == 1 and 'version not set' in warnings[0]


def test_rebuild_merge_keeps_the_new_qop_choice(tmp_path, monkeypatch):
    import json
    from quam_state_manager.core import regenerate
    old = tmp_path / 'old'
    old.mkdir()
    s = {'qubits': {}, 'extras': {'qop_version': '3.4', 'custom': 7}}
    (old / 'state.json').write_text(json.dumps(s), encoding='utf-8')
    (old / 'wiring.json').write_text('{}', encoding='utf-8')
    def build(py, mode, spec, dest, **kwargs):
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / 'state.json').write_text(json.dumps(
            {'qubits': {}, 'extras': {'qop_version': spec['qop_version']}}), encoding='utf-8')
        (dest / 'wiring.json').write_text('{}', encoding='utf-8')
        return {'ok': True, 'result': {}}
    monkeypatch.setattr(regenerate.config_generator, 'run_generator', build)
    dest = tmp_path / 'new'
    result = regenerate.run_regenerate('py', old, {'qop_version': '3.7'}, dest,
                                       scripts_enabled=False)
    assert result['ok'], result
    saved = json.loads((dest / 'state.json').read_text(encoding='utf-8'))
    assert saved['extras'] == {'qop_version': '3.7', 'custom': 7}


def test_the_wizard_offers_only_versions_with_a_qm_release_page():
    # hidden_pages/QOP33x.md .. QOP37x.md are the release pages the rules cite
    js = (_ROOT / "quam_state_manager" / "web" / "static" / "generate.js").read_text(encoding="utf-8")
    line = next(l for l in js.splitlines() if l.strip().startswith("var QOP_VERSIONS"))
    offered = set(line.split("[", 1)[1].split("]", 1)[0].replace('"', "").replace(" ", "").split(","))
    assert offered == {"3.3", "3.4", "3.5", "3.6", "3.7"}
