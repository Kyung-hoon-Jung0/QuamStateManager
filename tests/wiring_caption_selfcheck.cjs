// Port captions under each FEM of the Instrument Wiring rack: the name
// compaction (_iwCompactNames) and the line wrap (_iwWrapCaption) run from the
// shipped app.js. Exit code 0 = every assertion held.
const fs = require('fs'), path = require('path'), vm = require('vm');
const src = fs.readFileSync(path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'app.js'), 'utf8');
function grab(name) {
    const i = src.indexOf('function ' + name + '(');
    if (i < 0) throw new Error('missing ' + name);
    let d = 0, j = src.indexOf('{', i);
    for (let k = j; k < src.length; k++) {
        if (src[k] === '{') d++;
        else if (src[k] === '}' && --d === 0) return src.slice(i, k + 1);
    }
}
const ctx = {};
vm.createContext(ctx);
vm.runInContext(grab('_iwCompactNames') + grab('_iwWrapCaption') +
    'var _IW_CAPTION_IO = {output: "out", input: "in", digital: "dig"};' + grab('_iwCaptionLines'), ctx);
let n = 0, bad = 0;
function eq(got, want, what) {
    n++;
    if (JSON.stringify(got) !== JSON.stringify(want)) { bad++; console.log('FAIL', what, JSON.stringify(got), '!=', JSON.stringify(want)); }
}
const C = ctx._iwCompactNames, W = ctx._iwWrapCaption;
eq(C(['qA1', 'qA2', 'qA3', 'qA4', 'qA5', 'qA6']), 'qA1-6', 'a full run');
eq(C(['qA1', 'qA4', 'qB2', 'qC7', 'q12']), 'qA1,4 qB2 qC7 q12', 'gaps and mixed prefixes');
eq(C(['qA6', 'qA1', 'qA3', 'qA2']), 'qA1-3,6', 'unsorted input');
eq(C(['qA1', 'qA1', 'qA2', 'Q_left_3', 'Q_left_3']), 'qA1,2 Q_left_3', 'duplicates');
eq(C(['q1', 'q2', 'q3', 'q5', 'q7', 'q8', 'q9', 'q11']), 'q1-3,5,7-9,11', 'eight members');
eq(C(['Q_left_3', 'Q_left_4', 'Q_right_1', 'resonator']), 'Q_left_3,4 Q_right_1 resonator', 'long names');
eq(/01-03/.test(C(['q01', 'q02', 'q03'])), true, 'zero padding is kept in a range');
eq(C(['qA1', 'qB1', 'qA2', 'qB2', 'qA3', 'qB3']), 'qA1-3 qB1-3', 'interleaved prefixes');
// wrap: never longer than the column, nothing lost
const long = 'out 1 ' + C(['QubitLeft_1', 'QubitLeft_4', 'QubitRight_2', 'qA1', 'qA4', 'qB2', 'qC7', 'q12']);
[8, 12, 17, 30].forEach(function (mx) {
    const lines = W(long, mx);
    eq(lines.every(l => l.length <= mx), true, 'wrap fits ' + mx);
    if (mx >= 17) eq(lines.join('').replace(/\s+/g, ''), long.replace(/\s+/g, ''), 'wrap keeps every name at ' + mx);
});
eq(W('out 1 qA1,4 qB2,5 qC2,5 qD1,3', 16), ['out 1 qA1,4', '  qB2,5 qC2,5', '  qD1,3'], 'a prefix group is never split');
eq(W('out 1 qA1-6', 17), ['out 1 qA1-6'], 'a short caption is one line');
eq(W('out 1 ' + 'X'.repeat(40), 12)[1], '  XXXXXXXXX\u2026', 'an over-long name is cut with an ellipsis');
// caption lines: a gappy feedline names every member, a TWPA reads pump
const fem = {output_ports: {'1': [{element: 'qA4', role: 'rr'}, {element: 'qC7', role: 'rr'}, {element: 'qA1', role: 'rr'}],
                            '8': [{element: 'twpaA', role: 'twpa_pump'}], '2': [{element: 'qA1', role: 'xy'}]},
             input_ports: {'1': [{element: 'qA4', role: 'rr_in'}, {element: 'qC7', role: 'rr_in'}]}};
eq(ctx._iwCaptionLines(fem), ['out 1 qA1,4 qC7', 'in 1 qA4 qC7', 'pump 8 twpaA'], 'caption lines');
console.log(bad ? bad + ' of ' + n + ' failed' : n + ' assertions held');
process.exit(bad ? 1 : 0);
