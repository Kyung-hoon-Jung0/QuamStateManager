"""w9 uxpolish: a MOUSE press leaves no ring around a whole button group.

Pico rings a whole ``[role=group]`` while any button in it has ``:focus``:

    [role=group]:has(button:focus,[type=submit]:focus,[type=button]:focus,
                     [role=button]:focus) { --pico-group-box-shadow: <ring> }

and Chrome gives a mouse-clicked button ``:focus``. So pressing Table/Flat
View, a Figures-per-row count, Changes/Summary, a Compare context or
strictness, a quick-filter chip, a sidebar tool or "Full names" left a ring
around the entire group until the next click elsewhere (w8/chipplace fixed
the rows toolbar alone). ONE rule in style.css now resets the group's shadow
when the focused member was focused WITHOUT ``:focus-visible``; keyboard focus
never matches it, so Pico's ring stays for the keyboard. The real-Chrome proof
(every group: mouse -> no ring, Tab -> ring) is the w9 uxpolish report; the
rows toolbar also keeps tests/browser/journeys/chip_place.cjs ``ring``.
"""
from __future__ import annotations

import re
from pathlib import Path

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"

_PICO_RULE = "[role=group]:has(button:focus,[type=submit]:focus,[type=button]:focus,[role=button]:focus)"
_OURS = ("[role=group]:has(:is(button, [type=submit], [type=button], [role=button])"
         ":focus:not(:focus-visible))")


def _split_args(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _spec(sel: str) -> tuple[int, int, int]:
    """Specificity of a compound selector in the grammar these rules use
    (tags, [attr], .class, #id, :pseudo, :is/:has/:not(list) = its most
    specific argument, :where(...) = 0)."""
    a = b = c = 0
    i = 0
    while i < len(sel):
        ch = sel[i]
        if ch == "[":
            j = sel.index("]", i)
            b += 1
            i = j + 1
        elif ch == ".":
            m = re.match(r"\.[\w-]+", sel[i:])
            b += 1
            i += m.end()
        elif ch == "#":
            m = re.match(r"#[\w-]+", sel[i:])
            a += 1
            i += m.end()
        elif ch == ":":
            m = re.match(r":([\w-]+)", sel[i:])
            name = m.group(1)
            i += m.end()
            if i < len(sel) and sel[i] == "(":
                depth, j = 0, i
                while True:
                    if sel[j] == "(":
                        depth += 1
                    elif sel[j] == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                inner = sel[i + 1:j]
                i = j + 1
                if name == "where":
                    continue
                if name in ("is", "has", "not"):
                    best = max((_spec(x) for x in _split_args(inner)), default=(0, 0, 0))
                    a, b, c = a + best[0], b + best[1], c + best[2]
                    continue
            b += 1
        elif re.match(r"[A-Za-z]", ch):
            m = re.match(r"[\w-]+", sel[i:])
            c += 1
            i += m.end()
        else:
            i += 1
    return (a, b, c)


def _css() -> str:
    return (_STATIC / "style.css").read_text(encoding="utf-8")


def test_the_pico_rule_is_still_the_one_this_overrides():
    pico = (_STATIC / "pico.min.css").read_text(encoding="utf-8")
    assert _PICO_RULE in pico
    # the ring it sets is the group's shadow variable, nothing else
    assert _PICO_RULE + ",[role=search]:has(button:focus,[type=submit]:focus,[type=button]:focus,[role=button]:focus)" \
        "{--pico-group-box-shadow:var(--pico-group-box-shadow-focus-with-button)}" in pico
    assert _spec(_PICO_RULE) == (0, 3, 0)


def test_a_mouse_press_leaves_no_ring_around_any_button_group():
    css = _css()
    at = css.find(_OURS)
    assert at > 0, "the one [role=group] mouse-focus reset is missing"
    body = css[css.index("{", at) + 1:css.index("}", at)]
    assert "--pico-group-box-shadow: 0 0 0 transparent" in body, body
    # the same members as Pico's own rule -- a member missing here rings again
    pico_members = {m.replace(":focus", "") for m in _split_args(
        _PICO_RULE[len("[role=group]:has("):-1])}
    ours_members = set(_split_args(re.search(r":is\((.*?)\):focus", _OURS).group(1)))
    assert ours_members == pico_members, (ours_members, pico_members)
    # it outranks Pico wherever both match (a mouse-focused member)
    assert _spec(_OURS) > _spec(_PICO_RULE), (_spec(_OURS), _spec(_PICO_RULE))
    # GENERAL: every group, not one class (the w8 fix covered the rows toolbar alone)
    assert _OURS.startswith("[role=group]:has(")
    # keyboard focus keeps Pico's ring: the reset never matches :focus-visible,
    # and nothing in style.css sets the group shadow for a plain :focus
    for m in re.finditer(r"([^{}]*)\{([^{}]*--pico-group-box-shadow\s*:[^{}]*)\}", css):
        sel = m.group(1).strip()
        sel = re.sub(r"/\*.*?\*/", "", sel, flags=re.S).strip()
        assert ":not(:focus-visible)" in sel or ":focus-visible" in sel, \
            "a rule that rings (or unrings) a group on plain :focus: " + sel


def test_every_group_on_the_pages_is_a_role_group_the_rule_selects(tmp_path):
    # the groups the user named (and every other) are [role=group] containers
    found = {}
    for f in sorted(_TPL.glob("*.html")):
        for m in re.finditer(r'<(\w+)\b[^>]*\brole="group"[^>]*>', f.read_text(encoding="utf-8")):
            cls = re.search(r'(?:class|id)="([^"]+)"', m.group(0))
            found[cls.group(1) if cls else m.group(0)] = f.name
    for want in ("sidebar-tools", "sidebar-tree-toolbar", "bulk-segmented", "ds-interactive-colbtns",
                 "cmp-tabs", "cmp-context", "cmp-strictness", "bulk-chipbar", "bulk-docbadges", "wb-seg"):
        assert any(k.split()[0] == want for k in found), (want, found)
    # a rendered page carries them as [role=group] (the sidebar tools + rows toolbar)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    html = app.test_client().get("/").get_data(as_text=True)
    for cls in ("sidebar-tools", "sidebar-tree-toolbar"):
        tag = re.search(r'<div class="' + cls + r'"[^>]*>', html)
        assert tag and 'role="group"' in tag.group(0), (cls, tag)
    # the Sync-Qualibrate workbench loads no Pico (its #wb-seg has no group
    # ring to reset); if it ever does, it must load the reset with it
    wb = (_TPL / "workbench.html").read_text(encoding="utf-8")
    assert "pico" not in wb.lower() or "style.css" in wb


def test_the_specificity_helper_itself():
    # a vacuous helper would let the ranking assertion pass on anything
    assert _spec("[role=group]") == (0, 1, 0)
    assert _spec("button") == (0, 0, 1)
    assert _spec(".a[role=group]:has(button:focus)") == (0, 3, 1)
    assert _spec(":where(.a) button") == (0, 0, 1)
    assert _spec(":not(#x)") == (1, 0, 0)
