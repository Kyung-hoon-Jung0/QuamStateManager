"""Lint pin: quam_state_manager/web/templates/** never emits a root-absolute
app URL as a literal string.

prefix_spec.md §3.2 mechanically rewrote every ``="/x"`` route attribute,
every inline-script route literal (fetch/htmx.ajax/JSON url field/{% set %})
and the two hand-edited sites (workbench.html's iframe, base.html's
``href.indexOf``) to go through the frozen Jinja global ``root`` (or, for the
JS-side comparison, ``window.SM.path``). This test is the regression guard:
a future template edit that reintroduces a bare ``/...`` literal, or that
double-prefixes ``{{ root }}{{ ... }}``, or that imports a root-using macro
file without ``with context``, fails here instead of silently breaking every
mount that isn't root.

Owner: implementer B (prefix_spec.md §6.1). Runs in both pytest modes
(root and SM_TEST_URL_PREFIX=/sm) -- the templates are static text, so the
lint result does not depend on which mode pytest is running in.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _ROOT / "quam_state_manager" / "web" / "templates"
_ALLOW_FILE = _ROOT / "tests" / "golden" / "template_root_allow.txt"

_COMMENT_RE = re.compile(r"\{#.*?#\}", re.S)
_JINJA_SPAN_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)

_ATTR_NAMES = (
    "href|src|action|formaction|poster|hx-get|hx-post|hx-put|hx-patch|"
    "hx-delete|hx-push-url|hx-replace-url|data-[a-z0-9-]+"
)

# (check name, compiled regex) -- each fires on a REMAINING violation, i.e.
# the mechanical rewrite (or a later edit) failed to route it through `root`.
_CHECKS: list[tuple[str, re.Pattern]] = [
    ("a_bare_attr", re.compile(r'\s(?:%s)=(["\'])(/(?!/))' % _ATTR_NAMES)),
    ("b_fetch", re.compile(r"fetch\(\s*['\"](/(?!/))")),
    ("b_htmx_ajax", re.compile(r"htmx\.ajax\([^,]+,\s*['\"](/(?!/))")),
    ("b_push_state", re.compile(r"pushState\([^,]+,[^,]+,\s*['\"](/(?!/))")),
    ("b_replace_state", re.compile(r"replaceState\([^,]+,[^,]+,\s*['\"](/(?!/))")),
    ("b_location", re.compile(r"location\.(?:href\s*=|assign\(|replace\()\s*['\"](/(?!/))")),
    ("b_window_open", re.compile(r"window\.open\(\s*['\"](/(?!/))")),
    ("c_json_field", re.compile(r'"(?:url|href|path|endpoint|buildEndpoint|action|redirect)"\s*:\s*"(/(?!/))')),
    ("d_set_literal", re.compile(r'\{%-?\s*set\s+\w+\s*=\s*"(/(?!/))')),
    ("d_iframe_src", re.compile(r'<iframe[^>]*\ssrc="(/(?!/))')),
    ("f_double_root", re.compile(r"\{\{\s*root\s*\}\}\{\{")),
]

_ROOT_TOKEN_RE = re.compile(r"\broot\b")


def _strip_comments(text: str) -> str:
    return _COMMENT_RE.sub("", text)


def _iter_template_files():
    for path in sorted(_TEMPLATES_DIR.rglob("*.html")):
        yield path


def _load_allowlist():
    entries = []  # (path_re, line_re)
    if _ALLOW_FILE.exists():
        for raw in _ALLOW_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            file_part, _, regex_part = line.partition(":")
            entries.append((re.compile(re.escape(file_part) + "$"), re.compile(regex_part)))
    return entries


def _is_allowed(rel_path: str, line_text: str, allowlist) -> bool:
    for path_re, line_re in allowlist:
        if path_re.search(rel_path) and line_re.search(line_text):
            return True
    return False


def _find_import_sites():
    """Return {macro_file: [(importer_rel_path, has_context: bool), ...]}."""
    import_re = re.compile(
        r"\{%-?\s*(?:from|import)\s+['\"]([^'\"]+)['\"][^%}]*?(with\s+context)?\s*-?%\}"
    )
    sites: dict[str, list[tuple[str, bool]]] = {}
    for path in _iter_template_files():
        text = _strip_comments(path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(_ROOT)).replace("\\", "/")
        for m in import_re.finditer(text):
            macro_file = m.group(1)
            has_context = m.group(2) is not None
            sites.setdefault(macro_file, []).append((rel, has_context))
    return sites


def test_no_bare_root_absolute_literals():
    allowlist = _load_allowlist()
    violations = []
    for path in _iter_template_files():
        rel = str(path.relative_to(_ROOT)).replace("\\", "/")
        text = _strip_comments(path.read_text(encoding="utf-8"))
        lines = text.splitlines()
        for name, pat in _CHECKS:
            for m in pat.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                line_text = lines[line_no - 1] if line_no - 1 < len(lines) else ""
                if _is_allowed(rel, line_text, allowlist):
                    continue
                violations.append(f"[{name}] {rel}:{line_no}: {line_text.strip()}")
    assert not violations, "root-absolute literal(s) found (rewrite or allow-list them):\n" + "\n".join(violations)


def test_root_using_macros_are_imported_with_context():
    """(e) `root` referenced inside a macro file imported WITHOUT `with context`
    renders as Undefined -> '' under Jinja -- silently wrong under a prefix,
    byte-identical at root, so nothing catches it at root-mode test time."""
    sites = _find_import_sites()
    violations = []
    for macro_rel_name, importers in sites.items():
        # macro_rel_name is the string literal used in {% from '...' %}, which
        # is always a bare filename relative to the templates dir today.
        macro_path = _TEMPLATES_DIR / macro_rel_name
        if not macro_path.exists():
            continue
        macro_text = _strip_comments(macro_path.read_text(encoding="utf-8"))
        uses_root = any(_ROOT_TOKEN_RE.search(span) for span in _JINJA_SPAN_RE.findall(macro_text))
        if not uses_root:
            continue
        for importer_rel, has_context in importers:
            if not has_context:
                violations.append(f"{importer_rel} imports {macro_rel_name} (uses `root`) without 'with context'")
    assert not violations, "macro imports missing 'with context' while the macro file uses `root`:\n" + "\n".join(violations)


def test_full_documents_carry_data_root_and_sm_root_script():
    """The 4 full documents (base.html, calc_window.html, chip_report.html,
    workbench.html) each declare data-root and load sm-root.js FIRST in
    <head>, before any other <script> tag."""
    full_docs = ["base.html", "calc_window.html", "chip_report.html", "workbench.html"]
    for name in full_docs:
        path = _TEMPLATES_DIR / name
        text = path.read_text(encoding="utf-8")
        assert 'data-root="{{ root }}"' in text, f"{name}: missing data-root=\"{{{{ root }}}}\" on <html>"
        head_match = re.search(r"<head>(.*?)</head>", text, re.S)
        assert head_match, f"{name}: no <head>...</head> found"
        head = head_match.group(1)
        scripts = re.findall(r"<script\b[^>]*>", head)
        assert scripts, f"{name}: no <script> tag in <head>"
        assert "sm-root.js" in scripts[0], (
            f"{name}: first <script> in <head> must load sm-root.js, got: {scripts[0]!r}"
        )


def test_base_body_carries_conditional_hx_ext():
    text = (_TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    m = re.search(r"<body\b[^>]*>", text)
    assert m, "base.html: no <body> tag found"
    body_tag = m.group(0)
    assert '{% if root %} hx-ext="sm-root"{% endif %}' in body_tag, (
        f"base.html <body> must carry the conditional sm-root extension, got: {body_tag!r}"
    )


def test_report_root_occurrence_count(capsys):
    """Not a pin -- prints the `{{ root }}/` occurrence count for the review
    to compare against the pre-rewrite inventory (prefix_spec.md §3.4:
    expect approximately 411 attrs + 29 JSON url/buildEndpoint fields +
    13 fetch + 4 htmx.ajax; the 8 `{% set %}` literals and the 1 hand-edited
    href.indexOf don't literally contain the substring `{{ root }}/`)."""
    total = 0
    for path in _iter_template_files():
        text = _strip_comments(path.read_text(encoding="utf-8"))
        total += text.count("{{ root }}/")
    print(f"\n[test_template_root_lint] `{{{{ root }}}}/` occurrences across templates: {total}")
    assert total > 0


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
