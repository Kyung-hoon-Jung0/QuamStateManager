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

    return { tokens: tokens, groupBy: groupBy, groups: groups,
             matchesHay: matchesHay };
})();
