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

    return { tokens: tokens, groupBy: groupBy, groups: groups,
             matchesHay: matchesHay,
             scopedSpans: scopedSpans, scopedTokens: scopedTokens,
             caretSpan: caretSpan };
})();
