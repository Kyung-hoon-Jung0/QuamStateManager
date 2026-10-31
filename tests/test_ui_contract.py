"""The UI kit contract (docs/308).

New or changed UI uses only the UI kit. A class with no CSS rule, a new
visual rule outside ``ui-kit.css``, or a raw value in place of a ``--ui-*``
token fails this file.

Existing debt is frozen as ``{file: max count}`` ratchets (and, for the
dead classes, as an explicit class-name list) in
``tests/ui_contract_baseline.json``. A count may only go down; when one does,
the test warns with the new maximum to write into the baseline. Re-measure
with ``python tests/test_ui_contract.py --write-baseline`` ONLY when lowering.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_WEB = _ROOT / "quam_state_manager" / "web"
_STATIC = _WEB / "static"
_TPL = _WEB / "templates"
_BASELINE = Path(__file__).with_name("ui_contract_baseline.json")
_UI_KIT = _STATIC / "ui-kit.css"


# ---------------------------------------------------------------- sources
def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _templates():
    return sorted(_TPL.glob("*.html"))


def _scripts():
    return sorted(p for p in _STATIC.glob("*.js") if not p.name.endswith(".min.js") and not p.name.startswith("plotly"))


def _rel(p: Path) -> str:
    return p.relative_to(_WEB).as_posix()


def _blocks(css: str):
    """Yield (selector, body, at_rule_stack) for every leaf rule of comment-stripped CSS."""
    def walk(text, stack):
        i, n = 0, len(text)
        while i < n:
            j = text.find("{", i)
            if j < 0:
                return
            sel = text[i:j].strip()
            # the selector is what follows the last ';' or '}' (an @import / stray text)
            sel = re.split(r"[;}]", sel)[-1].strip()
            depth, k = 1, j + 1
            while k < n and depth:
                if text[k] == "{":
                    depth += 1
                elif text[k] == "}":
                    depth -= 1
                k += 1
            body = text[j + 1:k - 1]
            if sel.startswith("@") and "{" in body:
                yield from walk(body, stack + [sel])
            else:
                yield sel, body, stack
            i = k
    yield from walk(css, [])


def _decls(body: str):
    for d in body.split(";"):
        if ":" in d:
            k, v = d.split(":", 1)
            yield k.strip(), v.strip()


def _css_sources():
    out = {"static/style.css": _read(_STATIC / "style.css"), "static/ui-kit.css": _read(_UI_KIT),
           "static/pico.min.css": _read(_STATIC / "pico.min.css")}
    for t in _templates():
        for m in re.finditer(r"<style[^>]*>(.*?)</style>", _read(t), flags=re.DOTALL):
            out.setdefault(_rel(t), "")
            out[_rel(t)] += m.group(1)
    return out


def _known_classes() -> set:
    known = set()
    for css in _css_sources().values():
        for sel, _b, _s in _blocks(_strip_comments(css)):
            known.update(re.findall(r"\.(-?[_a-zA-Z][\w-]*)", sel))
    return known


_JINJA = re.compile(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", flags=re.DOTALL)


def _literal_tokens(attr: str):
    attr = _JINJA.sub(" ", attr)
    attr = re.sub(r"\$\{[^}]*\}", " ", attr)          # JS template literal holes
    return [t for t in attr.split() if re.fullmatch(r"-?[_a-zA-Z][\w-]*", t)]


def _class_attrs(path: Path):
    """Literal class strings on elements: template class="..." / JS markup and className strings."""
    text = _read(path)
    if path.suffix == ".html":
        text = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)
        for m in re.finditer(r"<([a-zA-Z][\w-]*)\b(?:[^>{]|\{\{.*?\}\}|\{%.*?%\})*?\bclass=\"([^\"]*)\"", text, flags=re.DOTALL):
            yield m.group(1).lower(), m.group(2)
    else:
        for m in re.finditer(r"<([a-zA-Z][\w-]*)\b[^<>]*?\bclass=\\?[\"']([^\"'\\]*)", text):
            yield m.group(1).lower(), m.group(2)
        for m in re.finditer(r"(?<![\w-])className\s*[:=]\s*([\"'])([^\"'\n]*)\1", text):
            yield "?", m.group(2)


# ---------------------------------------------------------------- measures
def measure_dead_classes(known=None):
    known = known if known is not None else _known_classes()
    per_file, names = {}, set()
    for p in _templates() + _scripts():
        n = 0
        for _tag, attr in _class_attrs(p):
            toks = _literal_tokens(attr)
            if toks and not any(t in known for t in toks):
                n += 1
                names.update(toks)
        if n:
            per_file[_rel(p)] = n
    return per_file, sorted(names)


def measure_style_css():
    raw = _read(_STATIC / "style.css")
    return {"rules": _strip_comments(raw).count("{"), "lines": raw.count("\n") + 1}


_COLOR = re.compile(r"(?<![&\w-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|\brgba?\(|\bhsla?\(")
_THEME_PART = re.compile(r"^(?::root|\[data-theme=\"?\w+\"?\]|:root\[data-theme=\"?\w+\"?\]|:root:not\(\[data-theme=\"?\w+\"?\]\))$")


def _is_var_block(sel: str) -> bool:
    return all(_THEME_PART.match(s.strip()) for s in sel.split(","))


def measure_colors():
    out = {}
    css = _strip_comments(_read(_STATIC / "style.css"))
    out["static/style.css"] = sum(len(_COLOR.findall(body)) for sel, body, _s in _blocks(css) if not _is_var_block(sel))
    for p in _templates() + _scripts():
        n = len(_COLOR.findall(_read(p)))
        if n:
            out[_rel(p)] = n
    return out


def measure_bare_buttons(known=None):
    known = known if known is not None else _known_classes()
    out = {}
    for p in _templates():
        text = re.sub(r"\{#.*?#\}", "", _read(p), flags=re.DOTALL)
        n = 0
        for m in re.finditer(r"<button\b((?:[^>{]|\{\{.*?\}\}|\{%.*?%\})*)>", text, flags=re.DOTALL):
            cm = re.search(r"\bclass=\"([^\"]*)\"", m.group(1))
            if not cm:
                n += 1
                continue
            toks = _literal_tokens(cm.group(1))
            if toks and not any(t in known for t in toks):
                n += 1
        if n:
            out[_rel(p)] = n
    for p in _scripts():
        n = len(re.findall(r"<button\b(?![^<>]*\bclass=)[^<>]*>", _read(p)))
        if n:
            out[_rel(p)] = n
    return out


def measure_inline_styles():
    out = {}
    for p in _templates():
        n = len(re.findall(r"\bstyle=\"[^\"]*\b(?:color|background|font-size)\b", _read(p)))
        if n:
            out[_rel(p)] = n
    for p in _scripts():
        n = len(re.findall(r"\.style\.(?:color|background\w*|fontSize)\s*=(?!=)", _read(p)))
        if n:
            out[_rel(p)] = n
    return out


def measure_overlay_shells():
    out = {}
    for p in _templates() + _scripts():
        t = _read(p)
        # a shell that wears the kit's ui-dialog is not a new shell
        tags = re.findall(r"<dialog\b[^>]*>|<[a-zA-Z][^<>]*\brole=\\?[\"']dialog[^<>]*>", t)
        n = sum(1 for tag in tags if "ui-dialog" not in tag)
        if n:
            out[_rel(p)] = n
    return out


def measure_confirm_alert():
    out = {}
    for p in _templates() + _scripts():
        n = len(re.findall(r"(?<![\w.$-])(?:window\.)?(?:confirm|alert)\s*\(", _read(p)))
        if n:
            out[_rel(p)] = n
    return out


def measure_csp():
    hx_on, filters = {}, {}
    for p in _templates() + _scripts():
        t = _read(p)
        a = len(re.findall(r"hx-on", t))
        b = len(re.findall(r"hx-trigger=\\?[\"'][^\"']*\[", t))
        if a:
            hx_on[_rel(p)] = a
        if b:
            filters[_rel(p)] = b
    return {"hx_on": hx_on, "trigger_filter": filters}


def measure_all():
    known = _known_classes()
    dead, names = measure_dead_classes(known)
    return {
        "dead_class_elements": dead, "dead_class_names": names,
        "style_css": measure_style_css(),
        "color_literals": measure_colors(),
        "bare_buttons": measure_bare_buttons(known),
        "inline_styles": measure_inline_styles(),
        "overlay_shells": measure_overlay_shells(),
        "confirm_alert": measure_confirm_alert(),
        "csp": measure_csp(),
        "contrast_failures": sorted(f"{t} {a}/{b}" for t, a, b, _r, _m in contrast_results() if _r < _m),
    }


# ---------------------------------------------------------------- contrast
def _parse_color(v: str):
    v = v.strip().lower()
    named = {"black": (0, 0, 0, 1), "white": (255, 255, 255, 1), "transparent": (0, 0, 0, 0)}
    if v in named:
        return named[v]
    m = re.fullmatch(r"#([0-9a-f]+)", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1
        return r, g, b, a
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if m:
        parts = [x for x in re.split(r"[\s,/]+", m.group(1)) if x]
        r, g, b = (float(x) for x in parts[:3])
        a = float(parts[3].rstrip("%")) / (100 if parts[3].endswith("%") else 1) if len(parts) > 3 else 1
        return r, g, b, a
    raise ValueError(v)


def _split_top(s: str):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [x.strip() for x in out]


def _resolve(v: str, env: dict, depth=0):
    if depth > 30:
        raise ValueError("var loop")
    v = v.strip()
    m = re.fullmatch(r"var\((.*)\)", v)
    if m:
        args = _split_top(m.group(1))
        if args[0] in env:
            return _resolve(env[args[0]], env, depth + 1)
        if len(args) > 1:
            return _resolve(args[1], env, depth + 1)
        raise KeyError(args[0])
    m = re.fullmatch(r"color-mix\(in srgb,(.*)\)", v)
    if m:
        a, b = _split_top(m.group(1))
        def part(x):
            pm = re.fullmatch(r"(.*?)\s+([\d.]+)%", x)
            return (pm.group(1), float(pm.group(2)) / 100) if pm else (x, None)
        ca, pa = part(a)
        cb, pb = part(b)
        pa = pa if pa is not None else (1 - pb if pb is not None else .5)
        A, B = _resolve(ca, env, depth + 1), _resolve(cb, env, depth + 1)
        alpha = A[3] * pa + B[3] * (1 - pa)
        if alpha == 0:
            return (0, 0, 0, 0)
        rgb = tuple((A[i] * A[3] * pa + B[i] * B[3] * (1 - pa)) / alpha for i in range(3))
        return (*rgb, alpha)
    return _parse_color(v)


def _theme_matches(sel_part: str, theme: str):
    """Specificity (count) if this selector part matches <html data-theme=theme>, else None."""
    s = sel_part.strip()
    if not _THEME_PART.match(s):
        return None
    spec = 0
    for tok in re.findall(r":root|:not\(\[data-theme=\"?\w+\"?\]\)|\[data-theme=\"?\w+\"?\]", s):
        if tok == ":root":
            spec += 1
        elif tok.startswith(":not"):
            if re.search(r"=\"?(\w+)", tok).group(1) == theme:
                return None
            spec += 1
        else:
            if re.search(r"=\"?(\w+)", tok).group(1) != theme:
                return None
            spec += 1
    return spec


def theme_env(theme: str) -> dict:
    decls, order = [], 0
    for name in ("pico.min.css", "style.css", "ui-kit.css"):
        css = _strip_comments(_read(_STATIC / name))
        for sel, body, stack in _blocks(css):
            if stack:
                continue
            best = max((x for x in (_theme_matches(p, theme) for p in sel.split(",")) if x is not None), default=None)
            if best is None:
                continue
            for k, v in _decls(body):
                if k.startswith("--"):
                    order += 1
                    decls.append((best, order, k, v))
    env = {}
    for _spec, _o, k, v in sorted(decls):
        env[k] = v
    return env


def _lum(c):
    def ch(x):
        x = x / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def _over(fg, bg):
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1,)


# (foreground token, background token, minimum): text 4.5:1, state dots 3:1 (WCAG non-text).
CONTRAST_PAIRS = [
    ("--ui-text", "--ui-surface-0", 4.5), ("--ui-text", "--ui-surface-1", 4.5), ("--ui-text", "--ui-surface-2", 4.5),
    ("--ui-text-2", "--ui-surface-0", 4.5), ("--ui-text-2", "--ui-surface-1", 4.5), ("--ui-text-2", "--ui-surface-2", 4.5),
    ("--ui-accent", "--ui-surface-0", 4.5), ("--ui-accent", "--ui-surface-1", 4.5), ("--ui-accent", "--ui-surface-2", 4.5),
    ("--ui-on-accent", "--ui-accent-fill", 4.5),
    ("--ui-warn-text", "--ui-warn-bg", 4.5), ("--ui-danger-text", "--ui-danger-bg", 4.5), ("--ui-ok-text", "--ui-ok-bg", 4.5),
    ("--ui-ok-text", "--ui-surface-1", 4.5),
    ("--ui-ok-border", "--ui-surface-1", 3.0), ("--ui-warn-border", "--ui-surface-1", 3.0),
    ("--ui-danger-border", "--ui-surface-1", 3.0),
]
THEMES = ("light", "dark", "qm")


def contrast_results():
    out = []
    for t in THEMES:
        env = theme_env(t)
        page = _resolve("var(--ui-surface-0)", env)
        for a, b, minimum in CONTRAST_PAIRS:
            bg = _over(_resolve(f"var({b})", env), page)
            fg = _over(_resolve(f"var({a})", env), bg)
            la, lb = sorted((_lum(fg), _lum(bg)), reverse=True)
            out.append((t, a, b, round((la + 0.05) / (lb + 0.05), 2), minimum))
    return out


# ---------------------------------------------------------------- tests
def _baseline():
    return json.loads(_BASELINE.read_text(encoding="utf-8"))


def _ratchet(name, now: dict, base: dict):
    grew = {f: (n, base.get(f, 0)) for f, n in now.items() if n > base.get(f, 0)}
    assert not grew, f"{name}: count grew (file: now, max) {grew}"
    lower = {f: n for f, n in now.items() if n < base.get(f, 0)}
    lower.update({f: 0 for f in base if f not in now and base[f]})
    if lower:
        warnings.warn(f"{name}: counts went down -- lower the baseline to {lower}")


def test_1_color_literals_do_not_grow():
    _ratchet("color literals", measure_colors(), _baseline()["color_literals"])


def test_2_no_new_bare_or_unstyled_button():
    _ratchet("bare buttons", measure_bare_buttons(), _baseline()["bare_buttons"])


def test_3_no_new_inline_visual_style():
    _ratchet("inline styles", measure_inline_styles(), _baseline()["inline_styles"])


def test_4_no_new_overlay_shell():
    _ratchet("overlay shells", measure_overlay_shells(), _baseline()["overlay_shells"])


def test_5_no_new_window_confirm_or_alert():
    _ratchet("confirm/alert", measure_confirm_alert(), _baseline()["confirm_alert"])


def test_7_token_contrast_meets_aa_in_every_theme():
    known = set(_baseline()["contrast_failures"])
    bad = [f"{t} {a}/{b} = {r} < {m}" for t, a, b, r, m in contrast_results() if r < m and f"{t} {a}/{b}" not in known]
    assert not bad, bad


_STAGE1_MARK = "/* ===== stage 1:"


def _ui_kit_parts():
    raw = _read(_UI_KIT)
    assert raw.count(_STAGE1_MARK) == 1, "ui-kit.css has exactly one marked stage-1 section"
    head, tail = raw.split(_STAGE1_MARK, 1)
    return list(_blocks(_strip_comments(head))), list(_blocks(_strip_comments(_STAGE1_MARK + tail)))


def _ui_kit_rules():
    stage0, stage1 = _ui_kit_parts()
    return stage0 + stage1


def test_7b_stage0_boundary_only_ui_selectors_and_one_token_block():
    blocks = _ui_kit_parts()[0]
    roots = [b for b in blocks if b[0] == ":root"]
    assert len(roots) == 1, "exactly one :root token block"
    style_vars = set(re.findall(r"(--[\w-]+)\s*:", _strip_comments(_read(_STATIC / "style.css"))))
    for k, _v in _decls(roots[0][1]):
        assert k.startswith("--ui-"), f"token block declares a non-kit variable {k}"
        assert k not in style_vars, f"{k} redeclares a style.css variable"
    for sel, body, stack in blocks:
        assert not stack, f"no at-rule (theme or media) blocks in ui-kit.css: {stack}"
        if sel == ":root":
            continue
        for part in sel.split(","):
            part = part.strip()
            assert part.startswith(".ui-"), f"selector does not start with .ui-: {part!r}"
            assert ":has(" not in part and "[" not in part and ":where(" not in part, f"forbidden selector: {part!r}"
        assert not any(k.startswith("--") for k, _v in _decls(body)), f"{sel}: variables live in the token block only"


# Stage 1 (D-29, D-52): the one section allowed to target existing classes --
# the listed button selectors and the focus ring, nothing else.
_STAGE1_SELECTOR = re.compile(
    r"^(?:button\.btn-sm|\[role=button\]\.btn-sm|summary\.btn-sm|\.btn-sync)"
    r"(?:\.(?:outline|secondary|danger|primary))*(?::(?:hover|disabled))*$"
    r"|^(?:input|select|textarea)?:focus-visible$")
# Full-width rows that keep their pre-1a width (the sidebar State Load and
# Dataset Load submits, the landing "open current project" button): width only.
_STAGE1_FULL_WIDTH = {"#load-form > button.btn-sm", ".sidebar-add-form > button.btn-sm",
                      ".landing-current-project > button.btn-sm"}
# A button.btn-sm takes its colour through the variables Pico's button rule reads.
_STAGE1_VARS = {"--pico-border-radius", "--pico-background-color", "--pico-border-color", "--pico-color"}


def test_7d_stage1_section_targets_only_the_listed_button_selectors_and_focus():
    stage1 = _ui_kit_parts()[1]
    assert stage1, "the stage-1 section has rules"
    for sel, body, stack in stage1:
        assert not stack, f"no at-rule blocks in ui-kit.css: {stack}"
        parts = [x.strip() for x in sel.split(",")]
        if all(x in _STAGE1_FULL_WIDTH for x in parts):
            assert [k for k, _v in _decls(body)] == ["width"], f"{sel}: a full-width exception sets width only"
            continue
        for part in parts:
            assert _STAGE1_SELECTOR.match(part), f"stage-1 selector outside the allowed list: {part!r}"
        for k, _v in _decls(body):
            if k.startswith("--"):
                assert k in _STAGE1_VARS, f"{sel}: only {sorted(_STAGE1_VARS)} may be set here, not {k}"
                assert "btn-sm" in sel, f"{sel}: Pico variables are set on button.btn-sm only"


_VALUE_PROPS = re.compile(r"^(color|background(-color)?|border(-(top|right|bottom|left))?(-(color|width|style))?|border-radius|"
                          r"outline(-color)?|box-shadow|font-size|font-weight|font-family|line-height|gap|row-gap|column-gap)$")
_KEYWORDS = {"none", "transparent", "0", "solid", "inherit", "currentcolor", "normal", "initial"}


def test_11_ui_kit_values_are_tokens():
    for sel, body, _s in _ui_kit_rules():
        if sel == ":root":
            continue
        for k, v in _decls(body):
            assert "--pico-" not in v, f"{sel} {{{k}}}: references a Pico variable directly"
            assert not _COLOR.search(v) and "color-mix" not in v, f"{sel} {{{k}: {v}}}: a raw colour outside the token block"
            if _VALUE_PROPS.match(k):
                rest = re.sub(r"var\(--ui-[\w-]+\)", " ", v)
                bad = [t for t in rest.split() if t.lower() not in _KEYWORDS]
                assert not bad, f"{sel} {{{k}: {v}}}: raw value {bad}; use a --ui-* token"


def test_10_style_css_does_not_grow():
    now, base = measure_style_css(), _baseline()["style_css"]
    assert now["rules"] <= base["rules"] and now["lines"] <= base["lines"], f"style.css grew: {now} > {base}"
    if now["rules"] < base["rules"] or now["lines"] < base["lines"]:
        warnings.warn(f"style.css shrank -- lower the baseline to {now}")


def test_9_no_new_class_without_a_css_rule():
    per_file, names = measure_dead_classes()
    base = _baseline()
    new = sorted(set(names) - set(base["dead_class_names"]))
    assert not new, f"classes with no CSS rule (add a ui-* class or a rule in ui-kit.css): {new}"
    _ratchet("dead-class elements", per_file, base["dead_class_elements"])
    gone = sorted(set(base["dead_class_names"]) - set(names))
    if gone:
        warnings.warn(f"dead classes fixed -- remove from the baseline: {gone}")


def test_7c_csp_no_new_hx_on_or_trigger_filter():
    now, base = measure_csp(), _baseline()["csp"]
    _ratchet("hx-on", now["hx_on"], base["hx_on"])
    _ratchet("trigger filters", now["trigger_filter"], base["trigger_filter"])
    base_html = _read(_TPL / "base.html")
    assert base_html.count("attributesToSettle") == 1
    assert '<meta name="htmx-config" content=\'{"attributesToSettle":["width","height"],"historyCacheSize":0}\'>' in base_html
    for p in (_STATIC / "ui.js", _TPL / "_ui.html", _TPL / "ui_kit.html"):
        t = _read(p)
        assert "hx-on" not in t and "eval(" not in t and "new Function" not in t and "innerHTML" not in t, p.name


@pytest.fixture(scope="module")
def ui_kit_html():
    from quam_state_manager.web.app import create_app
    with tempfile.TemporaryDirectory() as tmp:
        c = create_app(testing=True, instance_path=str(Path(tmp) / "_inst")).test_client()
        r = c.get("/ui-kit")
        assert r.status_code == 200
        help_html = c.get("/help").get_data(as_text=True)
        yield r.get_data(as_text=True), help_html


def test_12_every_ui_class_appears_on_the_sample_page(ui_kit_html):
    html, _h = ui_kit_html
    on_page = set()
    for m in re.finditer(r'class="([^"]*)"', html):
        on_page.update(m.group(1).split())
    kit = set()
    for sel, _b, _s in _ui_kit_rules():
        kit.update(re.findall(r"\.(ui-[\w-]+)", sel))
    missing = sorted(kit - on_page)
    assert not missing, f"ui-kit classes not shown on /ui-kit: {missing}"


def test_the_sample_page_is_linked_from_help_and_loads_the_kit(ui_kit_html):
    html, help_html = ui_kit_html
    assert help_html.count('href="/ui-kit"') == 1
    assert 'data-bundle-file="ui.js"' in html
    assert "ui.js" not in help_html.split('id="bundle-manifest"')[0], "ui.js is a page bundle, never core"
    style_at = html.index("style.css")
    assert html.index("ui-kit.css") > style_at and html.index("ui-kit.css") - style_at < 200, "ui-kit.css right after style.css"
    for name in ("calc_window.html", "chip_report.html"):
        assert "asset_url('ui-kit.css')" in _read(_TPL / name), name
    assert _read(_TPL / "chip_report.html").count("asset_url('ui-kit.css')") == 2, "the link and the download CSS list"


def test_macros_escape_text(ui_kit_html):
    from quam_state_manager.web.app import create_app
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(testing=True, instance_path=str(Path(tmp) / "_inst"))
        with app.test_request_context():
            from flask import render_template_string
            evil = '<img src=x onerror="x">'
            out = render_template_string(
                '{% import "_ui.html" as ui %}{{ ui.ui_render(s) }}',
                s={"part": "item", "chip": evil, "title": evil, "body": [evil], "note": evil, "badge": {"text": evil},
                   "actions": [{"label": evil, "title": evil, "cls": "btn-sync"}]})
    assert "<img" not in out and "&lt;img" in out


def test_6_macro_and_ui_js_build_the_same_tree(ui_kit_html, tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    page = tmp_path / "ui_kit.html"
    page.write_text(ui_kit_html[0], encoding="utf-8")
    res = subprocess.run([node, str(_ROOT / "tests" / "ui_kit_selfcheck.cjs"), str(page)], capture_output=True,
                         text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ok ui_kit_selfcheck" in res.stdout and " 0 failed" in res.stdout


if __name__ == "__main__":
    if "--write-baseline" in sys.argv:
        _BASELINE.write_text(json.dumps(measure_all(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote", _BASELINE)
    else:
        print(json.dumps(measure_all(), indent=1, sort_keys=True))
        for row in contrast_results():
            print(*row)
