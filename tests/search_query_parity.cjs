/* JS side of the SearchQuery ↔ core/search_query.py parity pin.
 *
 * Reads a JSON array of query strings (argv[2]... actually argv[1] after
 * node), loads the REAL web/static/search-query.js, and prints
 * JSON.stringify of each query's groups. tests/test_search_query.py runs the
 * same queries through the Python twin and compares structure-for-structure —
 * the value_delta precedent: two languages, one behaviour, a test that fails
 * when either side moves alone.
 */
const fs = require('fs');
const path = require('path');

const casesPath = process.argv[2];
if (!casesPath) { console.error('usage: node search_query_parity.cjs <cases.json>'); process.exit(2); }
const queries = JSON.parse(fs.readFileSync(casesPath, 'utf8'));

global.window = {};
const src = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static', 'search-query.js');
eval(fs.readFileSync(src, 'utf8'));
const SQ = global.window.SearchQuery;
if (!SQ) { console.error('SearchQuery did not load'); process.exit(2); }

const out = queries.map(q => SQ.groups(q));
process.stdout.write(JSON.stringify(out));
