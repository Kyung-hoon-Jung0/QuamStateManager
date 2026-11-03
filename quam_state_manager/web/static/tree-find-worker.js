/* Regex matching for the Json tree find bar (D-36). Runs off the page's main
 * thread so the page can terminate a pattern that backtracks without end.
 * Messages: {corpus, texts, kinds} once per model revision, then one
 * {serial, source, whole, cs, keys, rep} per pass; answers {serial, hits}. */
'use strict';
var texts = [], kinds = [];
self.onmessage = function (ev) {
    var m = ev.data;
    if (m.texts) { texts = m.texts; kinds = m.kinds; return; }
    try {
        var re = new RegExp(m.source, m.cs ? 'g' : 'gi');
        var whole = new RegExp(m.whole, m.cs ? '' : 'i');
        var hits = [];
        for (var i = 0; i < texts.length; i++) {
            var k = kinds[i];
            if (k === 'k' && !m.keys) continue;
            var t = texts[i], hit, next;
            if (k === 's' || k === 'k') {
                re.lastIndex = 0; hit = re.test(t);
                if (hit && m.rep !== null) { re.lastIndex = 0; next = t.replace(re, m.rep); }
            } else {
                hit = whole.test(t);
                if (hit && m.rep !== null) next = t.replace(whole, m.rep);
            }
            if (hit) hits.push({i: i, next: next});
        }
        self.postMessage({serial: m.serial, hits: hits});
    } catch (err) {
        self.postMessage({serial: m.serial, error: String(err && err.message || err)});
    }
};
