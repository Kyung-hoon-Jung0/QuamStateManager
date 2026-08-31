"""The server-side twin of ``web/static/search-query.js`` — ONE query grammar.

The sidebar workspace filter runs on the server (``routes._parse_tree_query``)
while every other search box runs in the browser, and the two scoped grammars
that claimed to mirror each other had already drifted (``-x=y`` negates on one
and is literal on the other; commas split on one and not the other). This
module and its JS twin carry the SHARED part — plain tokenization and the
AND-of-OR group structure — and are pinned to each other case-for-case by
``tests/test_search_query.py`` (the ``value_delta`` precedent: two languages,
one behaviour, a test that fails when either side moves alone).

Grammar (see the JS twin's header for the measurements behind it):

* whitespace = AND — a plain-word query becomes singleton groups, i.e. the
  historic behaviour character for character;
* a STANDALONE ``|`` token with a joinable term on both sides merges its
  neighbours into one OR group, binding tighter than AND
  (``x180 amplitude | length`` = x180 AND (amplitude OR length));
* every other pipe — embedded (``|e>`` ket notation), leading, trailing,
  doubled, or beside a negated term — stays a literal term.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence


def tokens(q: str | None) -> list[str]:
    """Lowercased whitespace tokens; ``[]`` for an empty/blank query."""
    q = ("" if q is None else str(q)).lower().strip()
    return q.split() if q else []


def group_by(
    items: Sequence[Any],
    get_raw: Callable[[Any], str] | None = None,
    joinable: Callable[[Any], bool] | None = None,
) -> list[list[Any]]:
    """Group ANY ordered token list into AND-of-OR groups (tight-binding ``|``).

    ``get_raw(item)`` must return the raw token string (identity by default) —
    only an item whose raw is exactly ``"|"`` can be an operator. ``joinable``
    says whether an item may be an OR operand (default yes; scoped surfaces
    pass ``not negated``). Returns groups of items: AND over groups, OR within
    a group. Every non-operator pipe is its own literal group.
    """
    if get_raw is None:
        get_raw = lambda x: x  # noqa: E731 — mirrors the JS default exactly
    if joinable is None:
        joinable = lambda x: True  # noqa: E731

    groups: list[list[Any]] = []
    can_join: list[bool] = []
    i = 0
    n = len(items)
    while i < n:
        item = items[i]
        if get_raw(item) == "|":
            nxt = items[i + 1] if i + 1 < n else None
            if (groups and can_join[-1] and nxt is not None
                    and get_raw(nxt) != "|" and joinable(nxt)):
                groups[-1].append(nxt)          # a | b  ->  {a, b}
                can_join[-1] = True
                i += 2                          # consumed the operand
                continue
            groups.append([item])               # literal pipe
            can_join.append(False)
            i += 1
            continue
        groups.append([item])
        can_join.append(bool(joinable(item)))
        i += 1
    return groups


def groups(q: str | None) -> list[list[str]]:
    """Convenience for plain-string surfaces: query -> groups of strings."""
    return group_by(tokens(q))


def matches_hay(hay_lower: str, grps: Sequence[Sequence[str]]) -> bool:
    """(AND over groups) of (OR within a group) of substring tests."""
    for grp in grps:
        if not any(tok in hay_lower for tok in grp):
            return False
    return True


# ── The one search hint, docs/141 4aj (user-directed) ────────────────────────
#
# Thirteen search boxes carried thirteen hand-written placeholders ("Search
# keys or values...", "Search all pulses…", "Search: q2, q5, time · rabi ·
# tag:flagged · is:bookmarked"), and none of them said what the grammar
# actually is — so the AND/OR the whole app shares was invisible everywhere
# except the two boxes that happened to spell it out. The grammar IS the
# thing worth saying in the little space a placeholder has: examples are
# guessable, operators are not.
#
# Shape: "Search: space = AND, | = OR" plus, only where the surface really
# has them, its own scope tokens. The full sentence lives in the `title`
# (SEARCH_TITLE) so nothing is lost to the compaction.
HINT = "space = AND, | = OR"
SEARCH_TITLE = (
    "Space between words = AND (every word must match). "
    "A standalone | between two words = OR. Any other pipe is a literal."
)


def search_hint(*extras: str) -> str:
    """The placeholder every SM search box uses.

    ``extras`` are the surface's OWN scope tokens (``"tag:"``, ``"is:"``) —
    kept to what that box can really do, appended after the grammar and
    never instead of it.
    """
    parts = [HINT] + [e for e in extras if e]
    return "Search: " + ", ".join(parts)


def search_title(*extras: str) -> str:
    """The full grammar sentence for the box's tooltip."""
    tail = (" Scopes here: " + ", ".join(e for e in extras if e) + ".") if any(extras) else ""
    return SEARCH_TITLE + tail
