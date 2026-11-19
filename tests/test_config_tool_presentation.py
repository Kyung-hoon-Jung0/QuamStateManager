"""Configuration pages retain compact kit parts and responsive layout contracts."""
from pathlib import Path
import re

import pytest
from jinja2 import Environment, FileSystemLoader

WEB = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"


def read(name):
    return (WEB / name).read_text(encoding="utf-8")


# Each case guards the exact affected element, including fixes inherited from the kit.
CASES = [
    ("autofit_disclosure_heads", "templates/_autofit.html",
     '<summary class="ui-disclosure-summary">AI judge'),
    ("profile_answer_count_is_a_note", "templates/_chip_profile.html",
     '<p class="ui-note">{{ answers|length }} of {{ fields|length }} answered.'),
    ("profile_choices_fit_their_table", "templates/_chip_profile.html",
     'class="chip-profile-panel ui-config-profile"'),
    ("config_location_is_a_disclosure", "templates/_qualibrate.html",
     '<summary class="ui-disclosure-summary">Config location'),
    ("qubit_counts_use_numeric_controls", "templates/_generate.html",
     'id="gen-qubit-count" min="0" max="200" value="0" class="ui-input ui-input--num"'),
    ("architecture_probe_has_a_short_hint", "static/generate.js",
     'hint.textContent = _couplerCzCheck === "checking" ? "Checking environment..." : opt.title;'),
    ("gate_and_scheme_share_control_height", "static/ui-kit.css",
     '.ui-generate #gen-pair-gate.ui-select, .ui-generate #gen-naming-preset.ui-select {'),
    ("grid_dimensions_use_numeric_controls", "templates/_generate.html",
     'id="gen-topo-cols" min="1" max="20" class="gen-topo-dim ui-input ui-input--num"'),
    ("advanced_fields_align_with_connection", "templates/_opx_monitor_body.html",
     '<details class="ui-disclosure ui-config-advanced"'),
    ("monitor_port_explains_sdk_default", "templates/_opx_monitor_body.html", "else 'auto: QM SDK default'"),
    ("environment_rows_use_compact_kit_layout", "templates/_scheduler.html",
     'id="sched-env-list" class="sched-env-list ui-config-environments"'),
    ("gate_form_children_do_not_clip", "static/ui-kit.css",
     '.czg-form > *, .czg-evidence > * { flex-shrink: 0; }'),
    ("diagnostic_dialog_uses_a_shared_surface", "templates/_diagnostics.html",
     '<div class="ui-config-diagnostics">'),
    ("qdac_empty_state_uses_kit", "templates/_qdac.html",
     '<p class="ui-state ui-state--empty" role="status">No QDAC-biased qubits'),
    ("compare_heading_uses_kit_scale", "templates/_compare_hub.html", '<h2 class="ui-head-title">Compare</h2>'),
    ("source_icons_use_kit_hit_targets", "templates/_compare_hub.html", 'class="ui-btn-icon cmp-src-x"'),
    ("fit_replay_subtitle_is_body_copy", "templates/_fit_audit.html",
     '<p class="ui-note">Read-only. Replays saved fits, never changes chip values.</p>'),
    ("analysis_root_uses_compact_field_label", "templates/_fit_audit.html",
     '<label class="ui-field-label" for="fa-source-root">'),
    ("storage_path_is_separate_from_heading", "templates/_disk_page.html", '{{ ui_path(disk.root) }}'),
    ("diff_source_picker_uses_kit_select", "templates/_diff_workbench.html", 'class="ui-select"'),
]


@pytest.mark.parametrize("name,source,expected", CASES, ids=[c[0] for c in CASES])
def test_configuration_presentation(name, source, expected):
    assert expected in read(source)


def test_profile_omits_duplicate_heading_and_inline_table_sizes():
    source = read("templates/_chip_profile.html")
    assert "<h3" not in source and "style=" not in source


def test_monitor_port_hint_matches_worker_omitting_unset_port():
    worker = (WEB.parent / "generator" / "opx_monitor_worker.py").read_text(encoding="utf-8")
    assert 'if t.get("port"):' in worker
    assert 'kw["port"] = int(t["port"])' in worker
    source = read("templates/_opx_monitor_body.html")
    assert 'dflt_target.port if dflt_target and dflt_target.port and not st_saved' in source
    assert 'class="ui-input ui-input--num" id="ui-opxmon-f-port" name="port" value="' in source


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_profile_kit_render_in_both_themes(theme):
    env = Environment(loader=FileSystemLoader(WEB / "templates"), autoescape=True)
    html = env.get_template("_chip_profile.html").render(
        fields=[dict(key="design", question="Design fact", help="Choose a case", cases=["known"])],
        answers={}, invalid={}, readonly=False,
    )
    assert 'class="ui-select"' in html and 'class="ui-note"' in html
    assert 'class="chip-profile-panel ui-config-profile"' in html
    css = read("static/ui-kit.css").split("/* Kit sweep wave 2: config */")[1]
    assert "data-theme" not in css  # the same token-based rules apply to both themes
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|color-mix", css)
    from tests.test_ui_contract import _resolve, theme_env
    tokens = theme_env(theme)
    for token in re.findall(r"var\((--ui-[\w-]+)\)", css):
        assert token in tokens, token
    for token in ("--ui-text", "--ui-text-2", "--ui-surface-1", "--ui-accent", "--ui-ctl-bg"):
        assert len(_resolve(tokens[token], tokens)) == 4, token


def test_configuration_layout_has_shrink_rules_for_phone_width():
    css = read("static/ui-kit.css")
    block = css.split("/* Kit sweep wave 2: config */")[1]
    assert 'grid-template-columns: auto minmax(0, 1fr) auto; min-width: 0; max-width: 100%;' in block
    assert '.ui-config-profile .ui-select { min-width: 0; max-width: 100%; }' in block
    assert 'width: var(--ui-input-w); min-width: 0; max-width: 100%;' in block
    assert 'grid-column: 2 / -1; font-size: var(--ui-fs-sm); white-space: normal;' in block
    assert '.ui-path-head { flex: 0 1 auto; min-width: 0; overflow: hidden;' in css
    assert 'max-width: 100%; min-width: 0;' in block


def test_location_controls_and_diagnostic_copy_use_kit_tokens():
    css = read("static/ui-kit.css").split("/* Kit sweep wave 2: config */")[1]
    assert '.ui-config-location .locate-input {' in css
    assert '.ui-config-advanced > .ui-disclosure-body { padding-inline: 0; }' in css
    assert 'height: var(--ui-ctl-h); min-height: var(--ui-ctl-h);' in css
    assert '.ui-config-diagnostics .diag-checks-head, .ui-config-diagnostics .diag-checks-body {' in css
    assert 'padding: var(--ui-gap-3) var(--ui-gap-4); background: var(--ui-surface-1);' in css


def test_architecture_radios_use_round_kit_marks_and_quiet_labels():
    assert 'ui-check ui-check--radio gen-arch-radio' in read("static/generate.js")
    css = read("static/ui-kit.css")
    assert '.ui-check--radio > input { border-radius: var(--ui-r-round); }' in css
    assert 'border: 0; border-radius: var(--ui-r-round); background: var(--ui-on-accent);' in css
    assert '.ui-generate .gen-chassis-count-field > .ui-field-label { font-weight: var(--ui-fw-normal); }' in css


def test_gate_check_hint_tracks_disabled_state_and_dialog_reopening():
    import os
    import subprocess
    result = subprocess.run(["node", str(Path(__file__).with_name("config_gate_hint_selfcheck.cjs"))],
                            capture_output=True, text=True, encoding="utf-8", timeout=30, env=os.environ.copy())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all checks passed" in result.stdout
    source = read("static/cz-gate.js")
    assert 'b.disabled = S.busy || !fp || !fp.ok || hasInvalid();' in source
    css = read("static/ui-kit.css")
    assert '.ui-dialog-foot > #czp-check-hint { flex: 1 1 100%; min-width: 0; overflow-wrap: anywhere; }' in css
