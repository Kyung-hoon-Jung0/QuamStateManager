"""The parameter vocabulary the sidebar typeahead completes from.

Customer, 2026-09-10: "사람은 m, mu, mul, mult, multi... 이렇게 순차적으로
타이핑하잖아? youtube나 vscode에서 자동완성 후보군 보여주는 것처럼 즉각적으로
m을 치면 m으로 시작하는 parameter들이 쭉 아래로 팝업되게 할수있어?"

MEASURED, on the customer's own archive (1,755 runs, 210 distinct keys): the
whole vocabulary -- every key, every distinct value, every count -- is 12 KB of
compact JSON and takes 9.5 ms to build. At 5,000 runs it is 12.3 KB / 27.7 ms;
at 20,000 runs, 12.9 KB / 139 ms. The PAYLOAD is flat because this is a
vocabulary, not an index: ten times the runs is not ten times the words. So it
is built once per workspace version and filtered in the browser, and a
keystroke costs no request at all.

Why the values are spelled HERE and not in JS: ``_param_hit`` compares
``param_norm(v) == want`` exactly, and the two languages do not agree on how a
number looks. ``str(1e-05)`` is ``'1e-05'`` in Python and ``'0.00001'`` in
JavaScript (measured, both). A vocabulary built client-side would therefore
offer values the server can never match. One spelling, server-side, and
``routes._param_norm`` is this function.

``insert_token`` is the other half of that honesty: it returns the text to put
in the search box, or ``None`` when the grammar cannot express the pair. A
value containing ``"`` is the real case -- the tokenizer strips quotes with no
escape, so ``a"b`` would be searched as ``ab`` and match nothing. Such pairs
are counted into ``omitted`` and never offered, because a suggestion that finds
nothing is worse than no suggestion.
"""

from __future__ import annotations

import re
from typing import Any

# Mirrors ``routes._SIDEBAR_PARAM_OP`` -- the shape a BARE ``key<op>value``
# token must have to be routed to the param facet rather than to free text.
_BARE_KEY = re.compile(r"^[A-Za-z][\w.\-]*$")

# What counts as a number for the panel's own purposes. Deliberately stricter
# than ``float()``: this rejects ``inf``, ``nan`` and hex by construction, and
# an extent printed from one of those would be nonsense on screen.
_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")

# A single run that recorded nothing must not cost a key its numeric nature.
# Measured on the customer archive: ``readout_amplitude_in_dBm`` (12 values) and
# ``load_data_id`` (20) are each demoted by exactly ONE ``None`` -- and the
# first of those is precisely the "amp" key the report named.
_NULLISH = {"none", "null", ""}

# The operators a token body may carry. ``>=`` before ``>`` matters in the
# regex; here the set is only used to validate what a caller asks for.
_OPS = (">=", "<=", ">", "<", "=")

# Per-key value cap. 30 is the widest real key measured (num_shots); 200 leaves
# an order of magnitude of headroom while bounding a pathological chip.
MAX_VALUES_PER_KEY = 200


def param_norm(v: Any) -> str:
    """A param value as the search sees it.

    ``True`` is the string ``true`` -- the user types what the Parameters facet
    shows them, not Python syntax.
    """
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v).lower()


def insert_token(key: str, value: str, force_scope: str = "",
                 negate: str = "", op: str = "=") -> str | None:
    """The search-box text that filters to ``key <op> value``, or ``None``.

    ``None`` means the grammar cannot express this pair, and the caller must
    not offer it:

    * either side contains ``"`` -- the tokenizer consumes quotes with no
      escape, so the token would search for something else entirely;
    * the key contains ``=`` or ``:`` -- it would re-partition;
    * the value has leading or trailing whitespace and needs the scope form,
      which ``_parse_tree_query`` strips.

    ``force_scope`` is the scope the user already typed (``"p:"``/``"param:"``)
    -- their spelling is kept. A key the bare form cannot carry (leading
    underscore, leading digit) gets ``p:`` added, because the scope path applies
    no key regex.
    """
    key = "" if key is None else str(key)
    value = "" if value is None else str(value)
    if not key or '"' in key or '"' in value:
        return None
    if "=" in key or ":" in key:
        return None

    scope = force_scope or ("" if _BARE_KEY.match(key) else "p:")
    if scope and value != value.strip():
        return None                      # the scope path strips the value
    if not value:
        return None

    if op not in _OPS:
        return None
    token = "%s%s%s%s%s" % (negate, scope, key, op, value)
    # A whitespace or comma anywhere in the token would split it in two, so the
    # WHOLE token is quoted -- quotes are consumed wherever they appear, so
    # `"k=a b"` is one param condition while `k=a b` is measurably two tokens.
    if any(ch.isspace() or ch == "," for ch in token):
        token = '"%s"' % token
    return token


def build_vocab(tree: Any) -> dict:
    """``{key -> {value -> count}}`` over every run in the workspace tree.

    Reads ``DateGroup.entries`` directly rather than ``Workspace.all_entries``:
    that property sorts every group on each access, and this walk wants the
    entries, not an order. The tree is only ever replaced by whole-dict atomic
    rebind and a published ``DateGroup.entries`` list is never mutated, so the
    walk needs no lock -- but the caller must read ``ws.version`` BEFORE it, or
    it can stamp a new version onto an older read.
    """
    counts: dict[str, dict[str, int]] = {}
    n_runs = 0
    if isinstance(tree, dict):
        for groups in tree.values():
            for g in (groups or ()):
                for e in (getattr(g, "entries", None) or ()):
                    n_runs += 1
                    params = getattr(e, "filter_params", None)
                    if not params:
                        continue
                    for k, v in params.items():
                        if not isinstance(k, str) or not k:
                            continue
                        by = counts.setdefault(k, {})
                        s = param_norm(v)
                        by[s] = by.get(s, 0) + 1
    return {"counts": counts, "n_runs": n_runs}


def to_payload(built: dict) -> dict:
    """The wire shape: a list of keys, each with its offerable values.

    Every value that survives is one ``insert_token`` can express, so the client
    never has to decide -- it renders what it is given. Values the grammar
    cannot carry are counted in ``omitted`` and said out loud rather than
    dropped in silence.
    """
    counts = built.get("counts") or {}
    keys = []
    omitted = 0
    for k in sorted(counts):
        by = counts[k]
        vals = sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))
        offerable = []
        for v, n in vals:
            if insert_token(k, v) is None:
                omitted += 1
                continue
            offerable.append([v, n])
        more = 0
        if len(offerable) > MAX_VALUES_PER_KEY:
            more = len(offerable) - MAX_VALUES_PER_KEY
            offerable = offerable[:MAX_VALUES_PER_KEY]
        if not offerable and not more:
            omitted += 0
        d = {"k": k, "n": sum(by.values()), "v": offerable, "more": more}
        # Three additive fields for the keys a LIST cannot serve. The panel
        # prints the extent from `min`/`max` VERBATIM as this function spells
        # them, so nothing it shows can disagree with what a token would match.
        nums = [float(v) for v, _n in offerable if _NUM_RE.match(v)]
        skipped = sum(1 for v, _n in offerable
                      if not _NUM_RE.match(v) and v not in _NULLISH)
        if len(nums) >= 2 and not skipped:
            d["num"] = 1
            lo = min(nums)
            hi = max(nums)
            # the value's own spelling, not a re-formatted float
            d["min"] = next(v for v, _n in offerable
                            if _NUM_RE.match(v) and float(v) == lo)
            d["max"] = next(v for v, _n in offerable
                            if _NUM_RE.match(v) and float(v) == hi)
        # The value ORDER is deliberately unchanged (count-descending).
        # Magnitude-ascending was considered and rejected by measurement:
        # `num_shots=8000` is 538 of 1,782 runs and would land at row 27 of 30,
        # outside an 8-row panel. "Recognise my own run" is the dominant intent;
        # the answer to breadth is the operator, not a re-sort.
        keys.append(d)
    # Coverage first: the key most runs carry is the one most likely wanted.
    keys.sort(key=lambda d: (-d["n"], d["k"]))
    return {"keys": keys, "n_runs": built.get("n_runs", 0), "omitted": omitted}
