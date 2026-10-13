# Generic Interactive figures and map tools (v1.4.0)

With a selected environment and a saved state, Interactive now starts with the
experiment's own plotting code. Smooth, Contour and Fit work on eligible 2-D
maps. The comparison shows the saved node value, SM's estimate, delta, tolerance,
verdict and raw-to-smoothed change. Apply opens the existing editable popup;
staging and writing to live remain separate actions.

This document records the shipped contracts and the release campaign's measured
results. Family numbers below identify node families, not versions. Measurements
are from the package reports; the final review adds synthetic process and latency
checks, without repeating the real-run campaign.

## Reproduction and cache

The runner reads the matching node's imports and saved plotting-mode conditions
without running acquisition. It binds saved parameters, results, coordinates and
snapshot objects by name, including keyword-only arguments and explicit aliases.
`bind_report` records supplied, defaulted and unbound arguments, restored records
and dropped fields. Dictionary returns produce separate figures; `None` means
no figure for this run. Saved figure keys and standalone PNGs are matched without
calling both DSP analysis modes.

The revision ladder is current code, then the git revision before the run's start
time, then saved PNGs and raw data. The second rung is explicitly a heuristic:
the archive does not record the exact analysis revision. Import/plot failures,
missing saved keys and marker-feeding defaults enter the ladder. Environment,
spawn and timeout failures skip historical execution. Each result carries the
chosen rung, fingerprint, attempts and a visible status label.

`iplot/v2` retains lines, markers, meshes and images, contour polylines, fills,
texts, arrows, spans, secondary/twin axes, color norms, cell edges and complete
scalar arrays. Unsupported artists or coordinates are reported on the tile.
The converter also accepts `iplot/v1`. Numeric coordinate matches can establish
axis bindings; text-only matches remain unverified. Entity-owned markers and
Plotly axis references keep a click within its panel.

The cache includes run input stats, selected environment/signature, Python path,
source root, extractor schema, code fingerprint and revision rung. Watched code
includes imported analysis packages, package membership and the runner/extractor
helpers. Historical and saved-rung entries also watch current code, so fixing
current analysis retries the ladder. Unchanged nanosecond mtime/size snapshots
skip hashing; changed stats trigger a content hash, and a touch without a content
edit preserves a hit. A same-size edit that deliberately preserves mtime is
outside this stat-fast contract.

RAM holds at most 12 results and 64 MiB; the compressed instance cache is bounded
to 256 MiB. The RAM results retain full arrays within that byte limit. Analysis
replay holds at most 64 responses and 64 MiB. Browser maps are reduced to at most
400 samples per axis after server analysis. Concurrent identical replay requests
coalesce. Opening Interactive may write this instance cache or materialize a
historical source copy; it does not stage a state edit.

## One lab worker slot

Reproduction and waveform drawing use separate line protocols and interpreters
in one shared slot. A machine lock covers active requests; switching kind or
environment retires the previous idle process, including across SM processes
using the same coordination directory. PID creation time protects the ownership
record against PID reuse. Retirement targets the lab child, not an SM server.
QA uses an isolated coordination directory and never retires the user's worker.

Imported source changes retire a warm worker. A renewed idle deadline prevents
an old timer callback from stopping a recently used worker. Idle expiry is one
hour; requests are bounded to 180 seconds and 2 GiB worker RSS. Both warm requests
and cold plot/waveform fallback have the memory check. Retirement stops attached
descendants, and failed ownership registration cleans up the new process.
This is an RSS guard on the worker, not a bound on total machine commit or an
operating-system sandbox for arbitrary analysis code.

Runs whose two saved HDF files total more than 300 MiB skip reproduction and
offer saved figures/raw data. This conservative final-review limit replaces the
unimplemented large-run strided replay option. Server analysis refuses a panel
over four million cells; contour drawing uses a disclosed 2x stride above one
million cells.

## Verified clicks and proposals

`learn_click` uses numeric `/quam/` replace writes from `node.json` patches
(applied), falling back to `state_updates.json` operations (proposed, not applied).
An unusable nonempty patch tier does not fall through to proposals. Fit-result
field names only annotate proof; they never invent a write target.

The closed mapping set is identity, powers-of-ten scale, frozen-old prefactor,
anchored increment and independently verified half/double chains. Every evidence
entity sharing a target template must verify a common axis/marker/transform tuple.
The displayed marker must reproduce the recorded value within relative 1e-6.
Missing numeric old values, marker-feeding defaults, ambiguous transforms or
unowned markers disable that target. Partial contracts retain refusal reasons
for the other paths. Proof is rebuilt on both RAM and disk cache hits.

Proposal contracts are labelled throughout the figure and popup. They prove a
saved proposal, not an accepted live write. Frozen prefactors come from evidence,
never the post-update snapshot. Rename translation and the existing chip token
guard still apply.

Recipes with evidence must first prove their own formula against a marker or
explicit verification point. A changed previously verified recipe fingerprint
disables its click contract. Proof history is bounded and in-process; restart
or eviction requires fresh marker verification. Recipes without write evidence
retain a visible unverified formula note. Raw-data coordinate candidates retain
their existing name-bound semantics; they are not learned write evidence.

## Models, caps and authoritative Apply

Models are error-amplitude/shared-pi, frequency ridge/asymptote, chevron hyperbola,
phase-constrained landscape minimum and generic extremum. Classification uses
coordinates, labels and signal units; transposed/descending axes are normalized
with an explicit model-to-figure axis mapping. Unknown maps offer a generic
extremum. A fit can refuse with flat, no feature, no extremum, outside the sweep
or ambiguous. A refusal produces no model coordinate to apply.

Smoothing is NaN-aware Gaussian, box, Savitzky-Golay or median; phase smoothing
uses circular arithmetic. Strengths are in samples, with a slider limit of eight.
Explicit radian units retain a 2*pi period even for small phase excursions;
the range heuristic for turns under a degree label does not override radians.
Count rows never mix in error-amplitude maps. The web analysis uses the tightest
row cap for one slider. Override is explicit and recorded.

| Family | Default before cap | Cap rule | Provenance |
| --- | --- | --- | --- |
| Error amplitude | 2 pt amplitude, 0 count | Per-row sigma <= 0.0731 fringe period | Derived attenuation; 10% loss is a reference-code guideline |
| Ridge | 0.5 pt control, 0.5 HWHM frequency | HWHM/step; control displacement bounded by HWHM, floor 0.5 pt; unknown track uses conservative 0.5 pt | Derived width rule; default/floor measured |
| Chevron | Half the tightest time cap, 1 pt control | Time <= 0.0731/(f*dt); control <= 0.15*f0/(slope*dc) | Derived time rule; control fraction measured |
| Landscape/extremum | 1 pt each, clamped | Quarter of edge/contour distance, tightened by local asymmetry | Derived geometry and cubic shift |

Tags distinguish derived formulas, measured settings, reference implementation
and design policy; none is presented as a published-paper guarantee. Comparison
tolerances are 0.003 for shared-pi, one step for landscape/voltage, a step or
0.1 HWHM for ridge frequency, and three recorded errors or 5% where applicable.
Units and saved physical quantities are matched before comparison. Nominal
policy placeholders are not treated as measured unrestricted minima.

Local Gaussian/box previews only draw a reduced array. Server analysis uses full
arrays, fixed numerical calls and canonical versioned hashes. The hash includes
run, source, figure, panel, settings and revision. Apply fetches the run-scoped
cached response and sends its server-computed updates to the existing popup.
Changing settings invalidates pending Apply; stale responses are ignored. An
expired response requires another analysis. The popup remains explicitly editable.
Neither the browser preview nor a client-supplied array supplies a fitted update.

## Provenance through the write door

`/field/edit` accepts optional form JSON provenance; `/field/edit-batch` accepts
batch-wide provenance and row overrides. The only fields are `src`, `run_uid`,
`request_hash`, `text`, each valid Unicode text of at most 300 characters.
Unknown keys, nonstrings, lone surrogates and malformed JSON return 400 before
mutation, including a malformed late batch row. Absent provenance retains the
ordinary edit shape.

Origin follows `ChangeEntry` into the Review tray, recovery sidecar, pull/replay,
undo/redo unit and ledger. Mixed origins stay attached to individual paths,
including pointer holders and renamed effective-history segments. UI text is
escaped. Apply and Apply All preserve origins across compensation and type-fix
retries. Provenance describes an edit; it does not authenticate a client or
replace the chip/type/structure guards. `/state/apply-to-live` remains the live
writer. Opening a map strip analyzes only; staging requires a popup action.

## Running QUAlibrate config banner

Discovery recognizes explicit `qualibrate start --config-path` arguments,
including module launch, quoted paths and both flag forms. Relative paths need
the server's working directory. Multiple servers are deduplicated by config
path. Root and project-overlay TOML reads are read-only; inaccessible listings
or invalid files produce an unknown result rather than a guessed location.

The banner compares explicit config paths for the open live chip. Its Use action
uses SM's existing config-location adoption flow; Dismiss persists in SM's
instance. Datasets can offer a different running data folder for that same chip.
Both process discovery and SM config/storage resolution run in background threads
with a three-second cache. Request handlers consume snapshots; missing/expired
snapshots hide hints until ready. Location changes invalidate snapshots. The
final review pins both hint endpoints below 200 ms while config I/O is blocked.
No discovery, banner or adoption action edits QUAlibrate config files.

## Measured coverage

P0's normal driver reproduced 15 figures in 10 of 31 sampled runs. Diagnostic
manual module/binder overrides were excluded. It lost secondary axes, contours,
fills, figure texts and fitted lines, and failed imports/binding/dict returns.
P1 restored every sampled family; zero skips applies to these measured artists,
not every possible matplotlib artist.

| Family | Runs producing figures P0 -> P1 | Figures P0 -> P1 | Saved keys matched | P1 artists | P1 skips |
| --- | --- | --- | --- | --- | --- |
| 11 power Rabi | 5/5 -> 5/5 | 10 -> 10 | 5 | 75 | 0 |
| 31d chevron | 0/5 -> 5/5 | 0 -> 10 | 10 | 83 | 0 |
| 31d DSP | 0/5 -> 5/5 | 0 -> 14 | 14 | 319 | 0 |
| 38b landscape | 0/5 -> 5/5 | 0 -> 15 | 15 | 145 | 0 |
| 38c amplified landscape | 0/5 -> 5/5 | 0 -> 15 | 15 | 186 | 0 |
| 09 flux ridge | 5/5 -> 5/5 | 5 -> 5 | 5 | 26 | 0 |
| 08b power ridge | 0/1 -> 1/1 | 0 -> 4 | 4 | 25 | 0 |
| Total | 10/31 -> 31/31 | 15 -> 73 | 68 | 859 | 0 |

The five extra figures are raw power-Rabi output. Saved-panel count mismatches
were zero; structural parity does not establish pixel or semantic parity.
Five sampled smoothed-map calls correctly returned absence.

P1c measured two runs per family through the actual driver. Cold medians were
9.69-10.92 s, warm plotting 2.82-3.18 s, RAM hits 0.039-0.071 s and disk hits
0.051-0.084 s. Peak sampled worker RSS was 314.7-343.5 MiB in those timing
samples; the wider census peaked at 389.1 MiB. All eight RAM,
eight disk and eight post-edit-hit probes made zero plotting invocations.
The QA slot was isolated, so these measurements do not establish exclusive
machine-wide occupancy alongside an already running installed worker.

### P2 synthetic cap bias and fault cases

Bias/RMS are in pixels or steps against the noiseless raw estimator. Ridge rows
use the P6 remeasurement after the control-default change; the other P2 rows
are unchanged. Trials used only two or three seeds per row.

| Model / variant | Effective test cap x / y | Bias x or primary / y | RMS x or primary / y |
| --- | --- | --- | --- |
| Error amplitude, slope 1 | Per row, maximum 28.7 / 0 | 0.038 | 0.069 |
| Error amplitude, slope 1.5 | Per row, maximum 19.2 / 0 | 0.025 | 0.027 |
| Error amplitude, 5% NaN | Per row, maximum 28.7 / 0 | 0.019 | 0.058 |
| Ridge, symmetric | 1.33 / 5.5 | 0.056 / -0.101 | 0.091 / 0.123 |
| Ridge, asymmetric | 1.21 / 5.5 | 0.045 / 0.605 | 0.103 / 0.627 |
| Chevron | 2.15 / 1.96 | -0.028 control | 0.029 |
| Landscape, quadratic or cubic | 0.417 / 0.417 | -0.012 / 0 | 0.572 / 0 |
| Extremum, quadratic | 8.5 / 8.5 before slider limit | 0.003 / -0.003 | 0.005 / 0.008 |
| Extremum, cubic | 5.75 / 8.5 before slider limit | -0.440 / -0.003 | 0.443 / 0.010 |

Mean cap bias was below one pixel; chevron below 0.1 step. Twice-cap overrides
in the original sweep moved asymmetric ridge frequency 2.06 pixels and cubic
extremum -2.04 pixels; error-amplitude/chevron fits refused. These are small
synthetic experiments, not universal error bounds.

| Fault | Pinned result |
| --- | --- |
| Pure noise, three seeds | No feature |
| Feature beyond sweep | Outside the sweep |
| Two features | Ambiguous, both listed |
| Planted +3-pixel shift | Follows within one pixel |
| NaN row and column | Valid estimate; missing-cell count retained |
| Transposed axes | Same type/values; axis mapping swapped |
| Turns under a degree label | Period one detected |
| Small excursions labelled radians | Period 2*pi retained; independent circular-mean check |
| Ramp with/without noise | No extremum |
| Constant map | Flat |
| Above-cap override | Warning in verdict and provenance |
| Wrong old value in evidence | Display-only |

Five synthetic shared-pi reference checks agreed to 1e-10/1e-12. SM's NaN
masking deliberately differs from a reference implementation whose raw sums
propagate NaN.

### P3 contract counts

Forty-one runs included all 31 P0 samples and all 15 target-family evidence
runs, with overlaps deduplicated. Counts below are figures, except Runs.

| Family | Runs | Figures | Applied proof | Proposed proof | Display-only |
| --- | --- | --- | --- | --- | --- |
| 11 | 10 | 20 | 5 | 1 | 14 |
| 31d | 5 | 10 | 0 | 0 | 10 |
| 31d DSP | 6 | 17 | 0 | 0 | 17 |
| 38b | 8 | 25 | 3 | 7 | 15 |
| 38c | 6 | 18 | 0 | 2 | 16 |
| 09 | 5 | 5 | 1 | 0 | 4 |
| 08b | 1 | 4 | 0 | 0 | 4 |
| Total | 41 | 99 | 9 | 10 | 80 |

Nine figures had partial-contract refusal reasons. Separate legacy-recipe
measurement: 31 figures, 18 labelled unverified, 13 proof refusals, zero verified
recipe contracts in this sample. A display-only count is never an agreement.

### P6 real-run validation and adjudication

All 244 requested runs reproduced at the current rung: 571 figures, zero plot
errors. Seven runs had no figures. Compared means at least one finite node/SM
pair; any failed tolerance makes that run disagree. Display-only, ambiguous and
no-feature counts can overlap across panels. Ratio columns are per-run maximum
absolute delta/tolerance, allowing comparisons across physical quantities.
These campaign measurements precede the final-review small-radian correction;
that correction has synthetic unit and independent smoothing pins.

| Family | Runs | Compared | Agree | Disagree | Median ratio | P90 | Max | Display-only | Ambiguous | No feature |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 11 power Rabi | 106 | 70 | 64 | 6 | 0.134 | 0.894 | 18.248 | 93 | 0 | 31 |
| 38b landscape | 56 | 1 | 0 | 1 | 1.076 | 1.076 | 1.076 | 54 | 50 | 3 |
| 38c amplified landscape | 37 | 2 | 2 | 0 | 0.673 | 0.712 | 0.722 | 35 | 15 | 15 |
| 31d chevron | 29 | 6 | 6 | 0 | 0.344 | 0.655 | 0.862 | 24 | 0 | 2 |
| 31d DSP | 9 | 4 | 2 | 2 | 0.874 | 1.017 | 1.017 | 6 | 0 | 2 |
| 09 flux ridge | 6 | 2 | 2 | 0 | 0.152 | 0.251 | 0.275 | 6 | 0 | 4 |
| 08b power ridge | 1 | 0 | 0 | 0 | n/a | n/a | n/a | 1 | 0 | 1 |

Twelve power-Rabi runs were not the target 2-D sweep; one eligible power-Rabi
panel and two landscape maps had fewer than three samples on one axis.
Raw shared-pi: 111 finite pairs in 63 runs, 110 agree and one disagrees at 0.003.
All 111 successful raw/default pairs had smoothing gap <=0.003 (maximum
0.001499); at the effective cap the maximum was 0.001606. Twenty-four successful
default pairs lacked a successful raw estimate and are excluded from this bias
statistic. Chevron cap-control mean bias was 0.1303 steps, maximum 1.4196;
the cap does not bound every noisy map to one step. Landscape real bias evidence
is sparse; power-ridge has no resolved real asymptote/paired bias.

Adjudication retained earlier confident disagreements after corrections; it did
not count newly refused estimates as agreement.

| Cause | Historical/default comparison pairs | Unique runs (can overlap) |
| --- | --- | --- |
| SM model failure | 30 | 21 |
| Different physical quantity | 9 | 5 |
| Node estimator failure | 10 | 9 |
| Total distinct comparison pairs | 49 | Not additive |

The ten final disagree pairs in nine runs remain explained node-estimator
failures, including failed multipulse fits, unbracketed DSP extrapolations and
a quadratic landscape point away from the measured contour minimum. No
unexplained default disagreement remains in the campaign. This is an adjudicated
archive result, not a claim that all node estimates or SM estimates are correct.

## Not in this release

- Exact historical-revision recovery when the run never recorded its revision.
- One combined plotting/waveform interpreter; they share a slot instead.
- Strided lab replay of runs above 300 MiB, or full-array analysis above four
  million cells. Saved figures/raw viewing remain available.
- Complete support for arbitrary matplotlib artists, nonlinear secondary-axis
  click transforms, or pixel-identical typography/layout.
- Learned Apply for a run without numeric write/proposal evidence, or a claim
  that proposals were accepted. Labelled recipe/raw candidates keep their older
  semantics.
- Guaranteed bias bounds for every noisy map; asymmetric cross derivatives and
  narrow ridges remain limitations. An override can produce a biased fit.
- Real resonator-versus-flux validation: no such runs existed in this archive.
  No comparison against future smoothed-result keys absent from saved runs.
- Universal landscape accuracy: very few real minima passed the uncertainty
  gates, and most such maps remain ambiguous or display-only.
- A full passing browser Apply journey for every sampled family. P5 completed
  12 of 20 journeys; eight could not Apply because proof was absent. Later
  conservative P6 refusals were validated numerically, without a new browser run.
- Detection of servers that supplied no explicit config flag, or guaranteed
  discovery when process access/config reads are unavailable.
- Confinement of arbitrary plotting-code side effects or detached helpers in
  an OS sandbox. SM's own replay does not save to the archived state.

Final-review evidence and unresolved suspicions are recorded outside the
repository in the release QA notes. See also [plot provenance](306_plot_provenance.md)
and [the original reproduction path](46_interactive_replot.md).
