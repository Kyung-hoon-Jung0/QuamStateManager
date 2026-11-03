/* SearchQuery — THE one boolean structure for SM's search boxes.
 *
 * The app grew five mutually incompatible search semantics (whole-substring
 * trees, three different AND tokenizers, two scoped grammars), and users kept
 * asking why two words work in Live State Edit but not in the Json Tree View.
 * This module owns the part every surface shares — tokenization for the plain
 * surfaces, and the AND/OR group structure for all of them — so they cannot
 * drift apart again. (The failure shape is proven, twice over: the two
 * row-hide classes of which only one was filtered, and the two grid
 * addressing models — docs/62. Even the two scoped grammars that CLAIM to
 * mirror each other have drifted on `-x=y` and on comma splitting.)
 *
 * Grammar:
 *
 *   space          AND — every group must match. For a query of plain words
 *                  this module produces exactly `split(/\s+/)` as singleton
 *                  groups, so every surface's historic behaviour is the
 *                  no-pipe special case, character for character.
 *
 *   " | "          OR — a STANDALONE pipe token with a joinable term on BOTH
 *                  sides merges its neighbours into one alternation group.
 *                  Alternation binds TIGHTER than conjunction (as in Google):
 *                  `x180 amplitude | length` = x180 AND (amplitude|length).
 *                  Any pipe that is not exactly that — embedded (`|e>`),
 *                  leading, trailing, doubled, or beside a negated term —
 *                  stays a LITERAL term, which is what it was yesterday.
 *
 * The standalone-token guard is measured, not stylistic: `|` occurs in 0 of
 * 43,115 distinct terms across 41 real chip states, 0 of the whole Datasets
 * haystack and 0 of 102 grid column labels — but node.json description
 * strings carry ket notation (`|e>`, `|00>`, `|baseline|`) in 25.7% of 2,829
 * real run files, and the dataset detail tab renders those through the same
 * tree search. An `OR` keyword was rejected on the same evidence: 21.9% of
 * tree nodes contain "or" (resonator, readout, DragCosine...).
 *
 * Surfaces with richer grammars (the dataset table's scopes and negation, the
 * sidebar tree filter's server-side twin in core/search_query.py) keep their
 * own token CLASSIFIERS and compose here at the group level via groupBy():
 * split into OR groups, run the existing per-token semantics inside each.
 * core/search_query.py mirrors this file and is pinned to it by
 * tests/test_search_query.py — change BOTH or the parity test fails.
 */
window.SearchQuery = (function () {
    'use strict';

    /** Lowercased whitespace tokens; [] for an empty/blank query. */
    function tokens(q) {
        q = (q == null ? '' : String(q)).toLowerCase().trim();
        return q ? q.split(/\s+/) : [];
    }

    /**
     * Group ANY ordered token list into AND-of-OR groups (tight-binding `|`).
     *
     * `list` items can be plain strings or a surface's structured tokens;
     * `getRaw(item)` must return the raw token string (identity by default)
     * — only an item whose raw is exactly "|" can be an operator; and
     * `joinable(item)` says whether an item may be an OR operand (default
     * yes; scoped surfaces pass `not negated`, so `-tag:wip | x` keeps its
     * pipe literal instead of inventing OR-of-negation semantics).
     *
     * Returns an array of groups; each group is an array of items, meaning
     * (AND over groups) of (OR within a group). Every non-operator pipe is
     * its own literal group — i.e. exactly the term it always was.
     */
    function groupBy(list, getRaw, joinable) {
        getRaw = getRaw || function (x) { return x; };
        joinable = joinable || function () { return true; };
        var groups = [];        // parallel: canJoin[i] = last item of groups[i] joinable
        var canJoin = [];
        for (var i = 0; i < list.length; i++) {
            var item = list[i];
            if (getRaw(item) === '|') {
                var nxt = (i + 1 < list.length) ? list[i + 1] : null;
                if (groups.length && canJoin[groups.length - 1] &&
                        nxt !== null && getRaw(nxt) !== '|' && joinable(nxt)) {
                    groups[groups.length - 1].push(nxt);   // a | b  →  {a, b}
                    canJoin[groups.length - 1] = true;
                    i++;                                    // consumed the operand
                    continue;
                }
                groups.push([item]);                        // literal pipe
                canJoin.push(false);
                continue;
            }
            groups.push([item]);
            canJoin.push(!!joinable(item));
        }
        return groups;
    }

    /** Convenience for plain-string surfaces: query → groups of strings. */
    function groups(q) {
        return groupBy(tokens(q));
    }

    /** (AND over groups) of (OR within a group) of substring tests. */
    function matchesHay(hayLower, grps) {
        for (var g = 0; g < grps.length; g++) {
            var toks = grps[g], any = false;
            for (var t = 0; t < toks.length; t++) {
                if (hayLower.indexOf(toks[t]) >= 0) { any = true; break; }
            }
            if (!any) return false;
        }
        return true;
    }

    /* The same character class Python's str.isspace() uses for the characters
       a query can actually carry. NOT /\s/: Python calls U+0085 whitespace and
       JS does not, so a regex here would split a pasted query differently on
       the two sides of the wire. */
    function _isSep(ch) {
        return ch === ' ' || ch === ',' || ch === '\t' || ch === '\n'
            || ch === '\r' || ch === '\f' || ch === '\v'
            || ch === '\u001c' || ch === '\u001d' || ch === '\u001e'
            || ch === '\u001f' || ch === '\u0085' || ch === '\u00a0'
            || ch === '\u1680' || (ch >= '\u2000' && ch <= '\u200a')
            || ch === '\u2028' || ch === '\u2029' || ch === '\u202f'
            || ch === '\u205f' || ch === '\u3000';
    }

    /* Every token as [rawStart, rawEnd, text] — the twin of
       core.search_query.scoped_spans. The span is RAW (it covers the quote
       characters the text drops), because a caller splicing into an input's
       value needs offsets into what the user can see. */
    function scopedSpans(text) {
        var out = [], cur = '', start = -1, inQ = false;
        text = String(text == null ? '' : text);
        for (var i = 0; i < text.length; i++) {
            var ch = text.charAt(i);
            if (ch === '"') {
                if (start < 0) start = i;
                inQ = !inQ;
                continue;
            }
            if (!inQ && _isSep(ch)) {
                if (cur) out.push([start, i, cur]);
                cur = ''; start = -1;
                continue;
            }
            if (start < 0) start = i;
            cur += ch;
        }
        if (cur) out.push([start, text.length, cur]);
        return out;
    }

    function scopedTokens(text) {
        return scopedSpans(text).map(function (s) { return s[2]; });
    }

    /* The token the caret is FINISHING, or null. Refuses the two states where
       completing would move text the user did not mean: a token not begun (the
       caret at 0 or straight after a separator), and an unbalanced quote (past
       an open quote the scan finds no separator, so the span runs to the end of
       the box). */
    function caretSpan(text, caret) {
        text = String(text == null ? '' : text);
        if (caret == null || caret <= 0 || caret > text.length) return null;
        var q = 0;
        for (var i = 0; i < caret; i++) if (text.charAt(i) === '"') q++;
        if (q % 2) return null;
        // No separator test: `spans[j][0] < caret` below already excludes a
        // caret on one. Measured redundant over 219,344 (text, caret) pairs.
        var spans = scopedSpans(text);
        for (var j = 0; j < spans.length; j++) {
            if (spans[j][0] < caret && caret <= spans[j][1]) return spans[j];
        }
        return null;
    }

    /* Comparison and range operators -- the ONE JS parser (docs/175). The
       twin of routes._param_cond / _as_number / _param_hit; the Datasets box
       (dataset-virtual.js) and both Live-Edit grids call it.
       `>=` precedes `>` (and `<=` precedes `<`) or the value becomes "=1000".
       A bare token must start with a letter; `scoped` (inside `p:`) takes any
       key. A comparison is only a comparison when its right side is a finite
       number, so `q1>q2` stays free text. */
    var _OP_BARE = /^([A-Za-z][\w.\-]*)(>=|<=|>|<|=)(.+)$/;
    var _OP_SCOPED = /^([^=<>]+)(>=|<=|>|<|=)(.+)$/;

    /** A finite number, or null. A bool is never a magnitude. */
    function num(v) {
        if (typeof v === 'boolean' || v == null || v === '') return null;
        var f = typeof v === 'number' ? v : Number(String(v).trim());
        return (typeof f === 'number' && isFinite(f)) ? f : null;
    }

    function paramCond(body, scoped) {
        var m = String(body).match(scoped ? _OP_SCOPED : _OP_BARE);
        if (!m) return null;
        var key = m[1].toLowerCase(), op = m[2], want = m[3];
        if (op === '=') {
            var dd = want.indexOf('..');
            if (dd >= 0) {
                var a = num(want.slice(0, dd)), b = num(want.slice(dd + 2));
                if (a != null && b != null) {
                    return { key: key, op: '..', want: want, wnum: null,
                             lo: Math.min(a, b), hi: Math.max(a, b) };
                }
            }
            return { key: key, op: '=', want: want, wnum: num(want), lo: null, hi: null };
        }
        var w = num(want);
        if (w == null) return null;
        return { key: key, op: op, want: want, wnum: w, lo: null, hi: null };
    }

    /** Does the magnitude n satisfy a parsed condition? (numeric part only) */
    function cmpHit(cond, n) {
        if (n == null) return false;
        switch (cond.op) {
            case '..': return cond.lo <= n && n <= cond.hi;
            case '=': return cond.wnum != null && n === cond.wnum;
            case '>=': return n >= cond.wnum;
            case '>': return n > cond.wnum;
            case '<=': return n <= cond.wnum;
            case '<': return n < cond.wnum;
        }
        return false;
    }

    /* A comparison token for a Live-Edit grid: {cond, keys} when `tok` parses
       as a NUMERIC condition and its key occurs in some column's search text
       (`hayOf(col)`, lowercased); null otherwise, and the token keeps the
       grid's column/id/value reading. A plain `key=text` is not numeric and
       stays free text, as the grids always read it. */
    function gridCmp(tok, cols, hayOf) {
        var cond = paramCond(tok);
        if (!cond || (cond.op === '=' && cond.wnum == null)) return null;
        var keys = {}, n = 0;
        cols.forEach(function (c) {
            if (hayOf(c).indexOf(cond.key) >= 0) { keys[c.key] = 1; n++; }
        });
        return n ? { cond: cond, keys: keys } : null;
    }
    /** Does a row (its {colKey: displayed text}) satisfy a grid token? */
    function gridCmpHit(ti, byKey) {
        if (!byKey) return false;
        for (var k in ti.cmpKeys) {
            if (Object.prototype.hasOwnProperty.call(byKey, k)
                && cmpHit(ti.cond, num(String(byKey[k]).replace(/,/g, '')))) return true;
        }
        return false;
    }

    return { tokens: tokens, groupBy: groupBy, groups: groups,
             matchesHay: matchesHay,
             scopedSpans: scopedSpans, scopedTokens: scopedTokens,
             caretSpan: caretSpan,
             num: num, paramCond: paramCond, cmpHit: cmpHit,
             gridCmp: gridCmp, gridCmpHit: gridCmpHit };
})();
