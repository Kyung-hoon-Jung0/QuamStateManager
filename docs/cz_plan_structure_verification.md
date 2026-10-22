# CZ plan structure verification

Scope: items 1, 2, 3, 5, 11, 12 and review change 21. Base: ec551fb8.
Worktree: sm-v152e, branch fix/czplan-ux. No server was started and no hardware or live state was used.

## Decisions

- Validity is decided once in the route table builder. The three existing point
  sentences remain unchanged. Generic quadratic limits print as
  `in range (quad, generic limit)`. All mover sentences, with names, are in the
  title. Unknown values use warning colour; idle validity stays a dash.
- Amplitude presentation is computed once with the operation lines, before the
  route cache, so the table and JSON card use identical strings and titles.
  The registered pulse lookup supplies the played amplitude and pulse path.
  `pulse_lobes` returns amplitudes in the same units as that stored amplitude;
  its offset and zero-area tail therefore share the annotation's unit peak.
  The existing three-significant-figure engineering formatter is wrapped with
  an explicit positive sign. Existing callers of that formatter are unchanged.
- Unrecognized pulse classes or unresolved ports retain the stored amplitude,
  explicitly labelled `stored`. A frequency refusal alone does not erase a
  known played amplitude; an unplayable pulse or unreadable amplitude does.
- The caveat contains no leading name. The existing client prefix identifies
  the qubit in figure caveats and notes. The source explanation is appended
  only without a model rejection; a rejected model never gains that clause.
- Sort instructions are on the header, retaining the physics tooltip. Buttons
  have no title. The inactive glyph reserves 1.2em and appears on hover/focus.
- The map select remains authoritative. The table copies options on map events,
  including the event after asynchronous option loading; its own change sends
  exactly one map change. Neither the event listener nor option copying sends
  a second change.
- The notes moved above the table without changing their content. The history
  container is a div. The lede keeps the exact `muted czp-lede` class string.
- The one-line-header threshold changed from 62em to 75em to accommodate the
  eleventh column. The validity and last-set minimum widths are unchanged.
  The fits toggle and scroll redesign belong to the later batch.

## RED evidence

The final new test file and its companion selfcheck were copied into sm-v152d
under temporary names and run there with the required environment and pytest
options. All 23 cases failed on ec551fb8 (16.60 seconds):

| Pin | Base failure |
| --- | --- |
| Validity, seven cases | Missing `validity_class` for model, measured quad, generic quad, source union, mixed warning, unknown, missing validity |
| Validity HTML and idle | Compact muted generic text absent |
| Sort titles | Six sortable headers instead of seven |
| Sort CSS | No reserved inactive glyph rule |
| Controls placement | No table controls row |
| Lede | Purpose followed the lobe caveat (offset 146 versus 9) |
| Caveat, accepted/rejected | Old `fitted arc` wording |
| Amplitudes, six cases | No amplitude table cell for flattop, SNZ mirror, SNZ tail, macro-copy difference, unresolved port, refused pulse |
| Picker selfcheck | Local operation picker absent |
| Amplitude sort selfcheck | Sortable amplitude header absent |
| Card selfcheck | Server amplitude display absent |

The temporary files were deleted. `git -C D:/work/sm-v152d status --short`
printed nothing after cleanup. No stash or commit was used there.

## Regression results

Each file ran separately with `PYTHONUTF8=1`,
`NODE_PATH=D:/work/statemanager/node_modules`, and:
`conda run -n cqt python -m pytest tests/<file> -q --timeout=900 --timeout-method=thread`.

| Test file | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| test_cz_plan_structure.py | 23 | 0 | 0 |
| test_cz_gate_plan.py | 74 | 0 | 0 |
| test_cz_plan_page.py | 41 | 0 | 2 |
| test_cz_plan_client.py | 6 | 0 | 0 |
| test_cz_plan_filter_sort.py | 1 | 0 | 0 |
| test_v151_cz_table_fit.py | 4 | 0 | 0 |
| test_cz_chip_map.py | 19 | 0 | 0 |
| test_v152_cz_operating.py | 17 | 0 | 0 |
| test_no_names_in_code.py | 7 | 0 | 0 |
| test_cust_0930_ui.py | 14 | 0 | 0 |
| test_clock_health.py | 173 | 0 | 0 |
| test_project_time.py | 55 | 0 | 0 |
| test_tag_vocab.py | 28 | 0 | 0 |
| Total | 462 | 0 | 2 |

The two skips require optional real-state folders. Every jsdom selfcheck ran.
The existing 71, 123 and 14 selfcheck counts are unchanged.

Existing pins updated:

- test_cz_plan_client.py: ordering and caveat-class assertions beside the literal
  lede-class pin; its prior assertions remain.
- test_v151_cz_table_fit.py: validity, last-set and nearest-resonance cell indices
  move right by one for the amplitude column. Cell classes and minimum-width
  assertions remain unchanged.
- No existing point-validity sentence pins or caveat-string assertions needed
  changing. Frozen route responses remain historical fixtures.

## Browser evidence

Offline Chrome used a synthetic fixture with 64 rows. All external requests
were intercepted; assets came from local files. The full page was exported
through Flask's test client. The table pane was scrolled to its controls and
captures were made with the table at rest, halfway down, and scrolled right.
The map plotting and application navigation scripts were stubbed for this
isolated table capture. Behaviour is separately covered by the jsdom pins.

Artifacts are in `.pytest_cache/cz-plan-structure/`:

- `table-1600-rest.png`, `table-1600-middle.png`, `table-1600-right.png`
- `table-1280-rest.png`, `table-1280-middle.png`, `table-1280-right.png`
- `table-950-rest.png`, `table-950-middle.png`, `table-950-right.png`
- `table-1600-focus.png`, `table-1600-controls-filtered.png`
- `lede-1600.png`, `metrics.json`

The lede and body both measure 17 px. Caveat and lede muted colour both measure
rgb(152, 161, 179) in the exported theme. Sort cue opacity is 0 at rest,
0.45 on keyboard focus and 1 when active. The sort button width is unchanged
at 29.421875 px. The glyph reserves 1.2em. The first row is approximately
1.88rem tall with the measured 21 px root at 1600. No fixed pixel-size targets
were added. The table fits its 1252 px pane at the 1600 viewport; 1280 and 950
retain local horizontal scrolling, and the held identity columns remain.

## Mutation checks

All 14 mutations were killed by the new pins. Each was applied alone with a
byte-for-byte restore in a finally block. The raw outputs are in
`.pytest_cache/cz-plan-structure/mutations.json`.

| Mutation | Result |
| --- | --- |
| validity compact | RED, restored |
| sort title | RED, restored |
| sort cue | RED, restored |
| local picker dispatch | RED, restored |
| options loaded event | RED, restored |
| notes placement | RED, restored |
| caveat acceptance | RED, restored |
| lede order | RED, restored |
| lede font | RED, restored |
| played versus copy | RED, restored |
| voltage sign | RED, restored |
| physical versus stored | RED, restored |
| card server text | RED, restored |
| amplitude sort key | RED, restored |

## Limits and deviations

- Screenshot data is synthetic; no live or real state was opened. The later
  batch's fits toggle is absent on this base, so its acceptance cannot yet be
  measured. This batch verifies the default 1600 table fit and narrower views.
- The scope adds web/routes.py for server decisions and cz-map.js for the
  required post-options event, as explicitly allowed by the code anchors.
- Dedicated new tests keep all new RED evidence separate from existing tests.
- No excluded map-label, history-content, nested-scroll, or global changes.

## Text invariants

All 11 changed text files report zero LF-only newlines. Git reports an LF
index and CRLF working files for each. The staged added-line scan reports zero
non-ASCII lines, and `git diff --cached --check` passes. The names guard includes
the newly staged test files. The base worktree remains clean.
