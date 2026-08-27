/* ================================================================
 * UI_CONFIG — Design tokens for JavaScript-only consumers
 * ----------------------------------------------------------------
 * Plotly charts and Split.js require plain numbers — they cannot
 * read CSS custom properties. This object is the single source of
 * truth for all chart dimensions, colors, and layout settings that
 * live in JavaScript.
 *
 * ⚠  Three values must stay in sync with style.css :root tokens:
 *      split.gutterSize         ↔  --split-gutter-size
 *      plotly.trendChart.height ↔  --trend-chart-height
 *      plotly.trendsMini.height ↔  --trends-mini-height
 * ================================================================ */
var UI_CONFIG = {

    /* ── SPLIT PANES ──────────────────────────────────────────────── */
    /* Controls the resizable vertical split between the upper table
       pane and the lower inspector/detail pane.                       */
    split: {
        defaultSizes:   [55, 45],  /* initial split as percentages [upper pane %, lower pane %] — must add up to 100 */
        expandedSizes:  [15, 85],  /* DEFAULT "expanded" target — customer ask (2026-08-27): clicking a run should open the detail panel nearly full-screen, not half. The inspector gets 85%; the upper table keeps a 15% sliver for context + grabbing. A user's own ⤒ set-icon preset (localStorage "quam_split_expanded", incl. [0,100]) still wins over this default. */
        collapsedSizes: [85, 15],  /* DEFAULT "collapsed" target — inspector gets 15%. Overridable via the gutter ⤓ set-icon → localStorage "quam_split_collapsed". */
        minSizes:       [0, 60],   /* min height (px) for [upper, lower] pane. Upper=0 lets the inspector be dragged to FULLY cover the page (Qubits / Chip Status / …); lower=60 keeps the inspector grabbable (use its × button to fully reveal the page instead). */
        gutterSize:     6          /* ⚠ height of the drag handle bar in pixels — keep in sync with --split-gutter-size in style.css */
    },

    plotly: {

        /* ── WIRING PAGE — Chip Topology Diagram ──────────────────── */
        topology: {
            markerSize:      38,                          /* diameter of each qubit dot on the chip diagram */
            textFont:        { size: 13, color: '#fff' }, /* font for qubit name INSIDE the colored dot */
            subLabelFont:    { size: 10, color: '#555' }, /* font for always-visible metrics below each dot */
            edgeLabelFont:   { size: 10, color: '#444' }, /* font for pair name + fidelity on edge midpoints */
            hoverFont:       { family: 'monospace', size: 12 }, /* font used inside the tooltip popup when hovering over a qubit */
            margin:          { l: 30, r: 30, t: 30, b: 40 },   /* blank space (pixels) around the diagram */
            chainColors: {
                A: '#4e79a7',   /* dot color for chain A — blue   */
                B: '#f28e2b',   /* dot color for chain B — orange */
                C: '#e15759',   /* dot color for chain C — red    */
                D: '#76b7b2',   /* dot color for chain D — teal   */
                E: '#59a14f',   /* dot color for chain E — green  */
            },
            chainFallback:   '#777',      /* dot color for any chain letter not listed above (darkened for white text) */
            nodeBorderColor: '#ffffff',   /* thin ring drawn around each qubit dot */
            /* Edge fidelity tiers share the app-blue single-hue ramp (same
               unification as the diagram cells: colour = magnitude, deep =
               good, washed-out toward the page = bad). A single-hue
               luminance ramp is also inherently colorblind-safe. Light-theme
               values; _applyThemeToPlotly swaps in the dark-theme ramp. */
            edgeFidelityGood: '#08519c',  /* edge color when CZ fidelity >= 95% — deep app blue */
            edgeFidelityWarn: '#4292c6',  /* edge color when CZ fidelity >= 85% — mid blue */
            edgeFidelityBad:  '#9ecae1',  /* edge color when CZ fidelity < 85% — washed-out pale blue */
            edgeFidelityNone: '#bbbbbb',  /* edge color when no fidelity data — gray */
            hoverBg:         '#ffffff',   /* background color of the hover tooltip box */
            hoverBorder:     '#cccccc',   /* border color of the hover tooltip box */

            /* ── Card layout (HTML topology section) ──────────────── */
            /* ⚠ These dimensions drive the absolute-positioned qubit
               cards. Row heights must stay in sync with the CSS
               --topo-prop-row-height and --topo-node-header-size tokens
               so the computed card height matches the actual render.     */
            layout: {
                cardWidth:    260,  /* width (px) of each qubit property card */
                rowHeight:    32,   /* height (px) of one property row — match --topo-prop-row-height */
                headerHeight: 44,   /* height (px) of the colored header bar (qubit name) */
                bodyPadding:  8,    /* extra padding (px) below the last row inside the card */
                moreRowHeight:32,   /* height (px) of the "... more ›" toggle row */
                gapX:        180,   /* horizontal gap (px) between cards — room for edge labels */
                gapY:        140,   /* vertical gap (px) between card rows — room for coupler edge labels */
                padding:      32,   /* outer padding (px) around the entire topology container */
                autoFit:      true, /* auto-scale topology to fit container width (no horizontal scrollbar) */
            },

            /* ── Dashboard panels (chip overview) ─────────────────── */
            dashboard: {
                /* Single sequential color scale for all heatmaps (low → high).
                   Superseded immediately by chip-status.js's PALETTES['Blues']
                   default (or the user's saved quam_heatmap_palette choice) —
                   this literal only matters if something reads UI_CONFIG
                   directly before that override runs. Kept in sync so it's
                   never a misleading fallback. */
                colorScale: ['#eff3ff', '#bdd7e7', '#6baed6', '#3182bd', '#08519c'],
                histColors: {
                    gateFidelity: '#43a2ca',
                    t1:           '#7bccc4',
                    czFidelity:   '#0868ac',
                    f01:          '#a8ddb5',
                },
                nullCellColor: '#f0f0f0',
                pairBarColor:  '#43a2ca',
            },
        },

        /* ── TRENDS PAGE — Full Comparison Chart ──────────────────── */
        /* The large chart shown when comparing trends across experiments */
        trendChart: {
            height:     250,                  /* ⚠ chart height in pixels — keep in sync with --trend-chart-height in style.css */
            titleFont:  { size: 14 },         /* font size of the metric name shown as the chart title (e.g. "T1") */
            xTickFont:  { size: 12 },         /* font size of the labels along the x-axis (experiment run names) */
            yTickFont:  { size: 11 },         /* font size of the labels along the y-axis (metric values) */
            margin:     { l: 80, r: 20, t: 40, b: 40 },  /* blank space around the chart in pixels: left/right/top/bottom */
            legendFont: { size: 11 },         /* font size of the qubit name legend below the chart */
            legendY:    -0.25                 /* vertical position of the legend: 0 = chart bottom edge, negative = below the chart */
        },

        /* ── TRENDS DASHBOARD — Mini Metric Charts ─────────────────── */
        /* The small individual charts shown on the Trends dashboard page */
        trendsMini: {
            height:     220,   /* ⚠ chart height in pixels — keep in sync with --trends-mini-height in style.css */
            xTickAngle: -45,   /* rotation of x-axis labels in degrees (negative = tilt clockwise to avoid overlap) */
            xTickFont:  { size: 10 },  /* font size of x-axis tick labels */
            yTickFont:  { size: 10 },  /* font size of y-axis tick labels */
            margin:     { t: 10, r: 20, b: 40, l: 70 }  /* blank space around the mini chart in pixels: top/right/bottom/left */
        },

        /* ── DATASETS PAGE — HDF5 Data Plot ───────────────────────── */
        /* The plot rendered when you click "Plot" on an HDF5 variable row */
        h5Plot: {
            height: 400,                          /* height of the HDF5 data plot in pixels */
            margin: { t: 40, r: 20, b: 50, l: 60 } /* blank space around the plot in pixels: top/right/bottom/left */
        },

        /* ── ALL CHARTS — Qubit Line Color Sequence ────────────────── */
        /* Plotly cycles through these colors to draw each qubit's line.
           Add more hex colors at the end if you have more than 8 qubits. */
        colorway: [
            '#4e79a7',  /* qubit 1 — blue   */
            '#f28e2b',  /* qubit 2 — orange */
            '#59a14f',  /* qubit 3 — green  */
            '#e15759',  /* qubit 4 — red    */
            '#76b7b2',  /* qubit 5 — teal   */
            '#edc948',  /* qubit 6 — yellow */
            '#b07aa1',  /* qubit 7 — purple */
            '#ff9da7'   /* qubit 8 — pink   */
        ]
    },

    /* ── PORT ROLE COLORS ─────────────────────────────────────────── */
    /* Colors for port-role badges in the Wiring table AND the colored
       circles in the SVG instrument wiring diagram.
       ⚠ After changing a color here, also update the matching
         --role-<name>-color in style.css :root so the table badge
         pills stay in sync with the SVG diagram circles.              */
    roleColors: {
        xy:        '#9b59b6',   /* XY drive port       — purple   */
        rr:        '#e67e22',   /* Readout resonator   — orange   */
        rr_in:     '#f0a030',   /* Readout input       — gold     */
        z:         '#3498db',   /* Flux / Z line       — blue     */
        coupler:   '#1abc9c',   /* Coupler             — teal     */
        cr:        '#27ae60',   /* Cross-resonance drive — green  */
        twpa_pump: '#e74c3c',   /* TWPA pump           — red      */
        twpa_ro:   '#a93226',   /* TWPA readout        — dark red */
        twpa_in:   '#d63384',   /* TWPA input          — magenta  */
        digital:   '#54617a',   /* Digital output / trigger — slate */
        z_qdac:    '#f1c40f',   /* Bias-tee flux port (z + QDAC-II) — amber.
                                   Its own colour on purpose (docs/136 r2):
                                   the dashed-ring mark was invisible at port
                                   size. Amber is the widest free hue gap in
                                   this palette — gold rr_in is orange-family
                                   and lives on MW-FEM input columns, never
                                   beside an LF-FEM z output. */
        fallback:  '#999999',   /* any unrecognised role — gray   */
    },

    /* ── INSTRUMENT WIRING DIAGRAM — Structural Colors ────────────── */
    /* Background and grid colors for the SVG wiring diagram on the
       Wiring page. These are layout/structure colors, not port role colors. */
    instrumentWiring: {
        gridBg:            '#f8f8f8',   /* background fill of the port grid area */
        gridBorder:        '#dddddd',   /* border drawn around the entire grid */
        rowLabelColor:     '#aaaaaa',   /* color of the row number labels on the left */
        separatorColor:    '#bbbbbb',   /* color of the vertical lines dividing FEM columns */
        subSeparatorColor: '#cccccc',   /* color of the dashed lines separating OUT and IN sub-columns */
        subLabelColor:     '#999999',   /* color of the OUT / IN column header labels */
        femLabelColor:     '#888888',   /* color of the FEM ID label at the bottom of each column */
        portLabelColor:    '#ffffff',   /* color of the text printed on colored port circles */
        emptyPortFill:     '#e8e8e8',   /* fill color of ports that are not physically present */
        emptyPortStroke:   '#cccccc',   /* border color of ports that are not physically present */
        unassignedFill:    '#ffffff',   /* fill color of ports that exist but have no signal assigned */
        unassignedStroke:  '#cccccc',   /* border color of ports that exist but have no signal assigned */
    },

    /* ── AUTO-REFRESH ──────────────────────────────────────────────────
     * How often (in seconds) the sidebar workspace tree polls the server
     * for new experiment folders. The server uses a cheap filesystem
     * mtime check and only does a full disk re-scan when something has
     * actually changed on disk, so short intervals are fine.
     * Recommended range: 10 – 300 seconds.                            */
    autoRefreshInterval: 60,   /* seconds between automatic workspace tree polls */

    /* The open Datasets table's delta poll (/datasets/changes-since) defaults
     * to 5 s instead, so a run that just finished appears promptly (docs/132;
     * #3; a tick is ~3.5 ms server-side per docs/103). Add a
     * `datasetPollInterval` key here (seconds) to pin that poll explicitly;
     * without one, an `autoRefreshInterval` tuned away from its shipped 60
     * is honored for the dataset poll too (dataset-virtual.js).           */

    /* ── TOPOLOGY LIVE UPDATE ─────────────────────────────────────────
     * How often (in seconds) the topology page polls the server to
     * check if state.json / wiring.json have been modified on disk.
     * The check is a pair of stat() calls (~microseconds), so short
     * intervals are fine.  Set to 0 to disable polling entirely.     */
    topoLivePollInterval: 3,

    /* ── LIVE-DRIFT TRACKING ──────────────────────────────────────────
     * How often (in seconds) every page polls /state/drift so the "Live
     * changes since baseline" panels (Param History / State History) and an
     * open drift overlay stay fresh — there is no main-screen banner any
     * more (docs/58). The server gates the work on a pair of stat() calls
     * (no content read unless the live files actually moved), so short
     * intervals are cheap. 0 disables it. */
    driftPollInterval: 5,
};

/**
 * QUAM State Manager -- client-side application logic.
 *
 * Functions are added incrementally as the UI redesign progresses.
 * All functions are attached to the global `window` so templates can
 * reference them via inline event handlers (oninput, onclick, etc.).
 */

/* ------------------------------------------------------------------
 * localStorage helpers (Phase 5 §4.2)
 * ------------------------------------------------------------------
 * Firefox / Safari Private Browsing disables localStorage by throwing
 * on every setItem / getItem call; same for users who disable storage
 * in browser settings. Unwrapped calls then abort their containing
 * handler and downstream UX silently breaks. These helpers fail
 * silently — every persisted value in this app is UX state (theme,
 * sidebar collapsed, font size, recent paths) where "no persistence"
 * is the correct fallback.
 */
function safeLSSet(key, value) {
    try { localStorage.setItem(key, value); } catch (_e) {}
}
function safeLSGet(key) {
    try { return localStorage.getItem(key); } catch (_e) { return null; }
}
function safeLSRemove(key) {
    try { localStorage.removeItem(key); } catch (_e) {}
}

/* ------------------------------------------------------------------ */
/* Utility: debounce                                                    */
/* ------------------------------------------------------------------ */

var _debounceTimers = {};
function _debounce(key, fn, delay) {
    if (_debounceTimers[key]) clearTimeout(_debounceTimers[key]);
    _debounceTimers[key] = setTimeout(fn, delay);
}

/* ------------------------------------------------------------------ */
/* Pagination: page-size selector                                       */
/* ------------------------------------------------------------------ */

/**
 * Called by the page-size <select> in _pagination.html.
 * Persists the user's choice in localStorage, then navigates to page 1
 * with the new per_page value via HTMX.
 */
window.setPageSize = function(selectEl, baseUrl, extraQs, storageKey) {
    var val = selectEl.value;
    try { localStorage.setItem(storageKey, val); } catch(e) {}
    if (window.htmx) {
        htmx.ajax('GET', baseUrl + '?page=1&per_page=' + val + extraQs, {target: '#table-pane', swap: 'innerHTML'});
    }
};

/**
 * Read the stored page-size preference from localStorage.
 * Returns the numeric value or the given default.
 */
window.getPageSize = function(storageKey, defaultVal) {
    try {
        var v = localStorage.getItem(storageKey);
        if (v !== null) return parseInt(v, 10);
    } catch(e) {}
    return defaultVal;
};

/* ------------------------------------------------------------------ */
/* Plotly cleanup on HTMX swaps (prevents memory leaks)                */
/* ------------------------------------------------------------------ */

/**
 * Before HTMX replaces any container, purge all Plotly charts inside the
 * OUTGOING DOM — the swap *target* (the container being replaced), NOT the
 * trigger element. The previous version scanned evt.detail.elt (the element
 * that fired the request, e.g. a clicked pulse/qubit row), which does not
 * contain the plot living in #inspector-pane — so the outgoing plot was never
 * purged. Destroying a Plotly node via innerHTML without purge leaves dangling
 * <defs>/clip-paths in the document, and the NEXT plot rendered into the same
 * container comes up with clipped (invisible) axes and dead interactivity
 * (the "2nd pulse click → broken plot, but fine after close+reopen" bug).
 * Without it Plotly also leaks WebGL contexts + DOM refs (~2-5MB per swap).
 */
/* htmx history restore replaces the BODY outside every swap hook — the one
   destructive path the beforeSwap choke point cannot see (docs/124 minor).
   By the time htmx:historyRestore fires the outgoing nodes are gone, so the
   reachable cleanup is the observer registry sweep (unobserveWithin's
   gone-sweep semantics); Plotly's own responsive handlers on the dropped
   graph divs are NOT reachable post-restore — recorded, not hidden. All
   charts in this app are SVG (docs/122 measured), so the residue is
   plain-memory until GC pressure, not WebGL contexts. */
document.addEventListener('htmx:historyRestore', function () {
    if (window.PlotHost) { try { window.PlotHost.unobserveWithin(document); } catch (e) {} }
});

/* The pinned-run interceptor must speak FIRST (docs/124 M-2): it may cancel
   the swap to keep the current layout, and every teardown listener below
   gates on shouldSwap — a later-registered interceptor meant the purge had
   already blanked the KEPT layout's figures (executed: click the pinned run
   again -> svg 3->0, unrecoverable, since the same pre-suppression pass also
   disconnected the lazy-render observer). The function is declared next to
   the rest of the pin machinery (hoisted — app.js is one top-level script);
   only the registration lives up here, ahead of the destroyers. */
document.addEventListener('htmx:beforeSwap', _pinnedRunSwapInterceptor);

/* (docs/125 round 3) The docs/110-era bare-class purge listener that lived
   here was REMOVED: the PlotHost choke point (registered further down) purges
   strictly more (structural graphDivs ⊇ the class), releases observers at the
   same moment, and honors the PaneState keep-route carve-out this one never
   did — two doors with different rules was itself a docs/124 finding, and the
   pinned-run blanking was purged TWICE through them. One door now. */

/* /config/regenerate returns its error banner with a 4xx/5xx status; htmx 2.x
   drops error-response bodies by default (responseHandling), so the banner
   silently never rendered. Allow the swap for config-status hosts only — no
   other 4xx/5xx behaviour changes. */
document.addEventListener('htmx:beforeSwap', function(evt) {
    if (!evt.detail || !evt.detail.target) return;
    var t = evt.detail.target;
    var status = evt.detail.xhr ? evt.detail.xhr.status : 0;
    // Config status host swaps its own error bodies (generate failures).
    if (t.classList && t.classList.contains('config-status-host') && status >= 400) {
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
    }
    // State History stage/restore gates answer 409 with a warning + force
    // button. Without this the warning would be dropped (htmx ignores error
    // responses) and the gate would look like a silent no-op.
    if (t.id === 'state-history-detail' && status === 409) {
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
    }
    // Landing project cards (docs/63): an Open-in-SM 4xx (dangling
    // state_path race, vanished project) carries a doctor-quality message —
    // render it inline in the landing's shared error slot instead of
    // dropping the body.
    if (t.id === 'landing-open-err' && status >= 400) {
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
    }
    // docs/114 (#15): a failed /load answers 400 with the persistent
    // explanation panel — htmx drops 4xx bodies by default, so without this
    // allowance the whole feature was invisible and the user fell back to a
    // vanishing toast (the integration audit caught it). Narrow: only a
    // /load response carrying that panel may swap into the main pane.
    if ((t.id === 'load-failed-slot' || t.id === 'table-pane')
        && status === 400 && evt.detail.xhr
        && /load-failed-panel/.test(evt.detail.xhr.responseText || '')) {
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
    }
    // Dataset "Load State" gates (r11): chip-mismatch / pending-edits answer
    // 409 with a confirm fragment — same pattern as state-history-detail.
    if (t.id === 'ds-load-state-result' && status === 409) {
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
    }
    // docs/120 item 10: the top-bar version panel's "Go back" posts the SAME
    // gated restore-live route, into #table-pane. Its two independent gates
    // (unsaved edits, wiring-topology mismatch) answer 409 with the force
    // panel, and having unsaved edits is the ORDINARY state — so without this
    // the button was a dead click exactly when "revert back and forth has to
    // be really free" mattered most. Narrow, like its siblings: only a body
    // that actually carries the confirm fragment may swap.
    if (t.id === 'table-pane' && status === 409 && evt.detail.xhr
        && /sh-confirm/.test(evt.detail.xhr.responseText || '')) {
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
    }
    // The tray's "Revert last apply" targets #status-bar; a stale tray can post
    // while edits exist and the stage gate 409s with a confirm fragment —
    // render it there instead of a dead click (docs/65). Narrowed to the
    // state-history stage/restore endpoints.
    if (t.id === 'status-bar' && status === 409) {
        var _p409 = (evt.detail.requestConfig && evt.detail.requestConfig.path) || '';
        if (_p409.indexOf('/state-history/') === 0) {
            evt.detail.shouldSwap = true;
            evt.detail.isError = false;
        }
    }
    // Data-folder cross-machine confirm (docs/20 r10): /chip-data-folder/set
    // answers 409 with _data_folder_confirm.html into the banner strip.
    if (t.id === 'chip-name-banner' && status === 409) {
        var _pdf = (evt.detail.requestConfig && evt.detail.requestConfig.path) || '';
        if (_pdf.indexOf('/chip-data-folder/') === 0) {
            evt.detail.shouldSwap = true;
            evt.detail.isError = false;
        }
    }
});

/* Surface a toast on ANY htmx error response. htmx 2.x drops error-response
   bodies, so a POST that 500s (e.g. "Apply to live chip" when the live file is
   locked by a running experiment) used to swap nothing → the click looked dead
   ("sometimes doesn't work"). Skip the targets that render their own error body
   (handled in beforeSwap above) to avoid a double-report. */
document.addEventListener('htmx:responseError', function(evt) {
    var t = evt.detail && evt.detail.target;
    if (t && t.classList && t.classList.contains('config-status-host')) return;
    if (t && t.id === 'state-history-detail') return;
    var xhr = evt.detail && evt.detail.xhr;
    var msg = "That action didn't go through — please try again.";
    if (xhr && xhr.responseText) {
        var m = xhr.responseText.match(/<p[^>]*>([\s\S]*?)<\/p>/);
        if (m) { var clean = m[1].replace(/<[^>]+>/g, '').trim(); if (clean) msg = clean; }
    }
    if (window.showToast) window.showToast(msg, "error");
});

// Network-level failure (server unreachable / connection dropped / request
// aborted) fires htmx:sendError, NOT htmx:responseError — so without this the
// loading indicator just vanishes and the click silently no-ops. For the
// desktop build (pywebview → local Flask) this is exactly what a backend crash
// or hang looks like, so the user must be told the app is unreachable rather
// than left thinking nothing happened. Same target exclusions as responseError
// (those hosts render their own inline failure state).
document.addEventListener('htmx:sendError', function(evt) {
    var t = evt.detail && evt.detail.target;
    if (t && t.classList && t.classList.contains('config-status-host')) return;
    if (t && t.id === 'state-history-detail') return;
    if (window.showToast) window.showToast("Couldn't reach the app — is it still running? Please retry.", "error");
});

// Global unsaved-edits guard: the pending tray's change_log ("N unsaved") lives
// only in server memory — nothing writes it to disk until Save — so closing the
// tab/window discards those edits with no warning. Warn whenever the tray shows
// pending changes. (The per-grid beforeunload guards cover cells the user typed
// but hasn't POSTed yet; this covers the committed-but-unsaved change_log.)
window.addEventListener('beforeunload', function (ev) {
    var tray = document.getElementById('pending-tray');
    var n = tray ? parseInt(tray.getAttribute('data-change-count') || '0', 10) : 0;
    if (n > 0) { ev.preventDefault(); ev.returnValue = ''; return ''; }
});

/* ------------------------------------------------------------------ */
/* Interactive-figure lifecycle: observer cleanup + offscreen purging   */
/* ------------------------------------------------------------------ */

// Keep at most this many interactive figures rendered at once. At 50 qubits a
// run emits ~50 figures (~1-2 MB Plotly heap each); without a cap the tab
// allocates ~75 MB and freezes for seconds. Offscreen tiles beyond the budget
// are purged and re-rendered on re-entry (the observer keeps watching them).
var INTERACTIVE_RENDER_BUDGET = 6;

function _purgeInteractiveTile(div) {
    if (!div) return;
    try {
        // docs/118: purge the tile's OWN plot node; falling back to the tile
        // itself covers the narrow race where a prune lands mid-draw, before
        // Plotly has added .js-plotly-plot — detaching that un-purged is what
        // leaves dangling <defs>/clip-paths behind.
        var inner = div.querySelector('.js-plotly-plot') || div.firstElementChild;
        if (inner && typeof Plotly !== 'undefined') Plotly.purge(inner);
    } catch (e) {}
    div.innerHTML = '';
    div.setAttribute('data-rendered', '0');
    // docs/118: a purged tile that stayed in container._rendered inflated the
    // budget, so the soft prune under-freed and the hard cap engaged early.
    var host = div.closest && div.closest('[id$="interactive-container"]');
    if (host && host._rendered) {
        var at = host._rendered.indexOf(div);
        if (at >= 0) host._rendered.splice(at, 1);
    }
}

// Purge the least-recently-rendered OFFSCREEN tiles until within budget.
// A hard ceiling (2× the soft budget) so a tall / 1-column layout that keeps many
// tiles on-screen at once can't blow past the heap budget — above it we purge the
// oldest tile even if visible (it re-renders on the next observer tick).
var INTERACTIVE_RENDER_HARD_CAP = INTERACTIVE_RENDER_BUDGET * 2;

function _pruneInteractiveTiles(container) {
    // r16 ⑤: a plot-popup apply swaps the tray + diagnostics banner, whose
    // height change re-lays-out the page → the observer marks every tile as
    // moved → this prune purged past the budget and each re-entry re-fetched
    // + re-rendered ALL figures (the "every update re-renders everything"
    // slowdown). Tiles depend only on the run's FROZEN artifacts, so the
    // correct post-apply behavior is NO tile work: skip pruning in the
    // layout-settle window after a tray swap.
    if (window._interactiveFreezeUntil
        && Date.now() < window._interactiveFreezeUntil) return;
    var rendered = container._rendered || [];
    if (rendered.length <= INTERACTIVE_RENDER_BUDGET) return;
    var excess = rendered.length - INTERACTIVE_RENDER_BUDGET;
    for (var i = 0; i < rendered.length && excess > 0; i++) {
        var div = rendered[i];
        if (div && !div._isVisible && div.getAttribute('data-rendered') === '1') {
            _purgeInteractiveTile(div);
            rendered.splice(i, 1);
            i--;
            excess--;
        }
    }
    // Hard cap: too many rendered tiles at once → drop the oldest.
    //
    // docs/118: this used to purge the oldest tile "even if visible (it
    // re-renders on the next observer tick)" — which it does NOT. An
    // IntersectionObserver only fires on a threshold CROSSING, and an emptied
    // tile keeps its min-height, so its intersection state never changes: the
    // tile stays blank until the user scrolls it fully out and back in. Purging
    // a visible tile is therefore purging it forever. Prefer offscreen tiles;
    // if the cap is still exceeded by visible ones, re-arm the observer for the
    // one we drop so it comes back on the next tick for real.
    var guard = 0;
    while (rendered.length > INTERACTIVE_RENDER_HARD_CAP && guard++ < 500) {
        var idx = -1;
        for (var k = 0; k < rendered.length; k++) {
            if (rendered[k] && !rendered[k]._isVisible) { idx = k; break; }
        }
        if (idx < 0) idx = 0;                       // all visible — oldest goes
        var old = rendered.splice(idx, 1)[0];
        if (old && old.getAttribute('data-rendered') === '1') {
            _purgeInteractiveTile(old);
            if (old._isVisible && container._io) {
                try {
                    container._io.unobserve(old);
                    container._io.observe(old);     // forces a fresh callback
                } catch (e) {}
            }
        }
    }
}
// Explicit binding for the jsdom selfcheck (eval'd contexts don't hoist
// top-level declarations onto window — see _navigateToExplorerPath).
window._pruneInteractiveTiles = _pruneInteractiveTiles;

// When a dataset pane is swapped out, disconnect its interactive observer and
// bump its generation so any in-flight tile fetch drops instead of painting
// into a detached/reused container. Scoped to the swap target (not evt.elt),
// so it catches the inspector pane on a dataset switch.
document.addEventListener('htmx:beforeSwap', function(evt) {
    if (!evt.detail) return;
    // A cancelled swap keeps its content (the pinned-run same-run click, a
    // dropped 4xx body) — disconnecting its lazy-render observer would leave
    // tiles that can never rebuild (docs/124 M-2's second half: data-rendered
    // still "1", _io dead, tab re-click builds nothing).
    if (evt.detail.shouldSwap === false) return;
    var scope = evt.detail.target || evt.detail.elt;
    if (!scope || !scope.querySelectorAll) return;
    scope.querySelectorAll('[id$="interactive-container"]').forEach(function(c) {
        if (c._io) { try { c._io.disconnect(); } catch (e) {} c._io = null; }
        c._gen = (c._gen || 0) + 1;
    });
});

/* ------------------------------------------------------------------ */
/* Sidebar tree: preserve open/closed state across auto-refresh         */
/* ------------------------------------------------------------------ */

/**
 * The sidebar workspace tree polls `/workspace/tree` every N seconds.
 * Each poll replaces #sidebar-tree innerHTML, destroying which <details>
 * (date groups, root folders) are open/closed and the scroll position.
 * We capture before swap and restore after.
 */
var _sidebarSticky = { roots: {}, dates: {}, scrollTop: 0 };

document.addEventListener('htmx:beforeSwap', function(evt) {
    if (!evt.detail || !evt.detail.target) return;
    if (evt.detail.target.id !== 'sidebar-tree') return;

    var tree = document.getElementById('sidebar-tree');
    if (!tree) return;

    // Capture root <details> open state keyed by root path
    _sidebarSticky.roots = {};
    tree.querySelectorAll('details.tree-root').forEach(function(d) {
        var label = d.querySelector('.tree-root-label span');
        if (label) _sidebarSticky.roots[label.getAttribute('title') || label.textContent.trim()] = d.open;
    });

    // Capture container <details> open state keyed by "rootPath::tpath"
    // (r13: containers nest arbitrarily — chip dirs above date dirs — and
    // data-tpath is the stable per-root path key; label text collides when
    // two chips carry the same date).
    _sidebarSticky.dates = {};
    tree.querySelectorAll('details.tree-root').forEach(function(root) {
        var rootLabel = root.querySelector('.tree-root-label span');
        var rootKey = rootLabel ? (rootLabel.getAttribute('title') || rootLabel.textContent.trim()) : '';
        root.querySelectorAll('details.tree-dir').forEach(function(d) {
            var tp = d.getAttribute('data-tpath');
            if (tp) _sidebarSticky.dates[rootKey + '::' + tp] = d.open;
        });
    });

    // Capture sidebar scroll position
    var sidebar = document.getElementById('sidebar');
    if (sidebar) _sidebarSticky.scrollTop = sidebar.scrollTop;
});

document.addEventListener('htmx:afterSwap', function(evt) {
    if (!evt.detail || !evt.detail.target) return;
    if (evt.detail.target.id !== 'sidebar-tree') return;

    var tree = document.getElementById('sidebar-tree');
    if (!tree) return;

    // Only restore if we have captured state (skip on initial load)
    var hasState = Object.keys(_sidebarSticky.roots).length > 0;
    if (!hasState) return;

    // Restore root <details> open state
    tree.querySelectorAll('details.tree-root').forEach(function(d) {
        var label = d.querySelector('.tree-root-label span');
        if (label) {
            var key = label.getAttribute('title') || label.textContent.trim();
            if (key in _sidebarSticky.roots) d.open = _sidebarSticky.roots[key];
        }
    });

    // Restore container <details> open state (tpath-keyed — see capture)
    tree.querySelectorAll('details.tree-root').forEach(function(root) {
        var rootLabel = root.querySelector('.tree-root-label span');
        var rootKey = rootLabel ? (rootLabel.getAttribute('title') || rootLabel.textContent.trim()) : '';
        root.querySelectorAll('details.tree-dir').forEach(function(d) {
            var tp = d.getAttribute('data-tpath');
            if (tp && (rootKey + '::' + tp) in _sidebarSticky.dates) {
                d.open = _sidebarSticky.dates[rootKey + '::' + tp];
            }
        });
    });
    // Re-mark the active branch (the swap rebuilt the DOM).
    if (window._markActiveTreeBranch) window._markActiveTreeBranch(null);

    // Restore sidebar scroll position
    var sidebar = document.getElementById('sidebar');
    if (sidebar) {
        requestAnimationFrame(function() { sidebar.scrollTop = _sidebarSticky.scrollTop; });
    }
});

/* Below-the-fold result reveal (audit P0-2/P0-3). The State-History "Compare 2
 * selected" / "View changes" / stage / restore / 409-gate all swap their result
 * into #state-history-detail — the LAST element on the page, below an up-to-40-entry
 * timeline — and htmx does NOT auto-scroll a swap without a show:/scroll: modifier,
 * so the user clicks and "nothing happens" (the canary). The Wiring-page history
 * drawer (#history-detail-area, a 42vh scroll box) has the same break. One delegated
 * handler scrolls a freshly-swapped, NON-EMPTY detail target into view. */
document.addEventListener('htmx:afterSwap', function (evt) {
    var t = evt.detail && evt.detail.target;
    if (!t || !t.id || !t.innerHTML || t.innerHTML.trim() === '') return;
    if (t.id === 'state-history-detail') t.scrollIntoView({ behavior: 'smooth', block: 'start' });
    else if (t.id === 'history-detail-area') t.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
});

/* Tray reflection (audit P1): after ANY swap that replaces #pending-tray — including
 * the DECLARATIVE Save / discard / apply-to-live swaps that no JS callback follows —
 * restore the drawer open-state and clear stale sidebar .tree-row-pending markers
 * (keyed on the tray's data-change-count). The JS edit callers route through
 * _swapPendingTray which now also calls this, so all paths converge. */
document.addEventListener('htmx:afterSwap', function (e) {
    var t = e.detail && e.detail.target;
    if (t && t.id === 'pending-tray' && window._restoreTrayState) window._restoreTrayState();
});

/* C2: render all state-change timestamps in the user's LOCAL time (users are
 * worldwide). The server emits <span class="ts-local" data-utc="…Z">…UTC fallback…</span>
 * at display sites; convert data-utc → toLocaleString() once (idempotent via
 * data-localized), on first paint AND on every HTMX swap so swapped-in compare/detail/
 * timeline headers localize too. Attribute sites keep plain UTC text (format_ts). */
function applyLocalTimes(root) {
    var nodes = (root || document).querySelectorAll('.ts-local[data-utc]:not([data-localized])');
    for (var i = 0; i < nodes.length; i++) {
        var el = nodes[i], iso = el.getAttribute('data-utc'), d = new Date(iso);
        if (!isNaN(d.getTime())) { el.textContent = d.toLocaleString(); el.title = iso + ' (UTC)'; }
        el.setAttribute('data-localized', '1');   // never re-convert (safe across nested swaps)
    }
}
window.applyLocalTimes = applyLocalTimes;
document.addEventListener('htmx:afterSwap', function (e) {
    if (e.detail && e.detail.target) applyLocalTimes(e.detail.target);
});
if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', function () { applyLocalTimes(document); });
else applyLocalTimes(document);

/**
 * Lazily load Plotly (~4.5 MB) on first use instead of eagerly on every page.
 * Returns a Promise that resolves once window.Plotly is available. Idempotent:
 * resolves immediately if already loaded, and coalesces concurrent callers onto
 * a single in-flight <script> injection. The src comes from <body data-plotly-src>
 * (rendered by url_for) so it stays PyInstaller-safe and has no hard-coded path.
 */
window._plotlyPromise = null;
window.requirePlotly = function() {
    if (window.Plotly) return Promise.resolve(window.Plotly);
    if (window._plotlyPromise) return window._plotlyPromise;
    window._plotlyPromise = new Promise(function(resolve, reject) {
        var src = (document.body && document.body.getAttribute('data-plotly-src'))
                  || '/static/plotly.min.js';
        var s = document.createElement('script');
        s.src = src;
        s.async = true;
        s.onload = function() { resolve(window.Plotly); };
        s.onerror = function() {
            window._plotlyPromise = null;  // allow a later retry
            reject(new Error('Failed to load Plotly'));
        };
        document.head.appendChild(s);
    });
    return window._plotlyPromise;
};

/**
 * Safe Plotly render: lazy-loads Plotly, then purges/reacts to prevent WebGL
 * context leaks (newPlot on first render, react() for updates). Returns a
 * Promise that resolves after the figure is drawn, so callers can attach click
 * handlers in .then(). Resolves to null if the target element is missing or
 * Plotly fails to load (the caller's .then() must tolerate that).
 */
window._plotlyRender = function(divId, data, layout, config) {
    var el = typeof divId === 'string' ? document.getElementById(divId) : divId;
    if (!el) return Promise.resolve(null);
    /* Make charts transparent so they inherit the page background color
       from the current theme (light or dark). Individual callers can
       still override by setting these explicitly in their layout.       */
    if (!layout.paper_bgcolor) layout.paper_bgcolor = 'transparent';
    if (!layout.plot_bgcolor)  layout.plot_bgcolor  = 'transparent';
    // Serialize renders PER ELEMENT. Plotly.newPlot is async; two renders of the
    // same div in quick succession (e.g. the inspector swap renders once, then a
    // scrollbar/Split layout shift renders again a frame later) would each see an
    // empty el.data — because the first newPlot hasn't populated it yet — and run
    // two CONCURRENT Plotly.newPlot on the same node. That race draws the plot but
    // leaves NO hover/zoom event handlers bound (the "2nd pulse click → static,
    // frozen plot, no interactivity" bug; the DOM looks identical to a healthy
    // plot, which is why it was so hard to spot). Chaining makes the 2nd call
    // await the 1st, so it sees el.data and does a clean Plotly.react (which
    // rebinds handlers) instead of a colliding newPlot.
    var prev = el.__plotlyRenderChain || Promise.resolve();
    var run = prev.catch(function() {}).then(function() {
        return window.requirePlotly();
    }).then(function() {
        if (!document.body.contains(el)) return null;  // detached between chained renders
        if (el.data && el.data.length > 0) {
            return Plotly.react(el, data, layout, config);
        }
        return Plotly.newPlot(el, data, layout, config);
    }).catch(function(e) {
        try { el.innerHTML = '<p class="muted" style="padding:.5rem">Plot library failed to load.</p>'; } catch (_) {}
        return null;
    });
    el.__plotlyRenderChain = run;
    return run;
};

/* ------------------------------------------------------------------ */
/* Reusable column resizing for any server-rendered <table>            */
/* ------------------------------------------------------------------ */

/**
 * Make a plain table's columns drag-resizable, persisting per-column widths
 * in localStorage. Adds a thin handle to each <th>, switches the table to
 * table-layout:fixed so the widths stick, and restores saved widths on each
 * (re)render. Idempotent — safe to call after every HTMX swap of the table.
 *   enhanceColumnResize('pulses-table', 'quam_pulses_col_widths')
 */
window.enhanceColumnResize = function(tableId, storageKey) {
    var table = document.getElementById(tableId);
    if (!table) return;
    var ths = table.querySelectorAll('thead th');
    if (!ths.length) return;

    var saved = {};
    try { saved = JSON.parse(localStorage.getItem(storageKey) || '{}') || {}; } catch (e) {}

    // Freeze the browser's current auto-sized widths as explicit pixel widths
    // BEFORE switching to table-layout:fixed — otherwise fixed layout would
    // redistribute every column to equal width and wreck the baseline (the
    // sparkline column especially). Saved overrides win over the snapshot.
    if (table.style.tableLayout !== 'fixed') {
        ths.forEach(function(th, i) {
            if (!saved[i]) th.style.width = th.offsetWidth + 'px';
        });
        table.style.tableLayout = 'fixed';
    }

    function persist() {
        try { localStorage.setItem(storageKey, JSON.stringify(saved)); } catch (e) {}
    }

    ths.forEach(function(th, i) {
        if (saved[i]) th.style.width = saved[i] + 'px';
        if (th.querySelector('.col-resize-handle')) return;   // already enhanced
        th.style.position = th.style.position || 'relative';
        var h = document.createElement('span');
        h.className = 'col-resize-handle';
        h.title = 'Drag to resize';
        th.appendChild(h);
        var startX = 0, startW = 0, dragging = false;
        h.addEventListener('mousedown', function(e) {
            e.preventDefault(); e.stopPropagation();
            dragging = true; startX = e.clientX; startW = th.offsetWidth;
            document.body.style.cursor = 'col-resize';
            function move(ev) {
                if (!dragging) return;
                var w = Math.max(36, startW + (ev.clientX - startX));
                th.style.width = w + 'px';
                saved[i] = w;
            }
            function up() {
                dragging = false;
                document.body.style.cursor = '';
                document.removeEventListener('mousemove', move);
                document.removeEventListener('mouseup', up);
                persist();
            }
            document.addEventListener('mousemove', move);
            document.addEventListener('mouseup', up);
        });
        // double-click a handle clears that column's manual width
        h.addEventListener('dblclick', function(e) {
            e.preventDefault(); e.stopPropagation();
            th.style.width = ''; delete saved[i]; persist();
        });
    });
};

/* ------------------------------------------------------------------ */
/* Config Viewer — waveform plot (Surface A)                           */
/* ------------------------------------------------------------------ */

/**
 * Caption for a waveform payload, built entirely with DOM/textContent so
 * server JSON can never inject HTML. One line per trace (I/Q/single) plus
 * honesty chips: "length unknown" when the backend guessed the window,
 * "config may be stale" when the cached config predates the current state.
 */
function _waveformCaption(data) {
    var cap = document.createElement('div');
    cap.className = 'waveform-plot-caption';
    function code(t) { var c = document.createElement('code'); c.textContent = t; return c; }
    function chip(t, title) {
        var s = document.createElement('span');
        s.className = 'waveform-warn-chip';
        s.textContent = t;
        if (title) s.title = title;
        return s;
    }
    cap.appendChild(code(data.operation));
    cap.appendChild(document.createTextNode(' on '));
    cap.appendChild(code(data.element));
    cap.appendChild(document.createTextNode(' → '));
    cap.appendChild(code(data.pulse));
    (data.traces || []).forEach(function(t) {
        cap.appendChild(document.createTextNode(' · ' + t.label + ': '));
        cap.appendChild(code(t.name));
        var info = (t.kind === 'constant')
            ? 'constant ' + t.constant_value + ' V × ' + t.length_ns + ' ns'
            : t.length_ns + ' samples';
        cap.appendChild(document.createTextNode(' (' + info + ')'));
        if (t.length_inferred) {
            cap.appendChild(document.createTextNode(' '));
            cap.appendChild(chip('length unknown',
                'No pulse length found in the config — showing a 16-sample placeholder window.'));
        }
    });
    if (data.stale) {
        cap.appendChild(document.createTextNode(' '));
        cap.appendChild(chip('config may be stale',
            'The state changed after the last Regenerate — regenerate the config to refresh.'));
    }
    return cap;
}

/**
 * "view waveform" button handler on the per-qubit/pair Generated Config
 * sections. Lives here (eagerly-loaded app.js) instead of inline in the
 * partial so pair pages opened first still find it, and so the wizard can
 * reuse it. Renders ALL traces of the operation's pulse (I+Q for IQ pulses).
 */
window.showWaveformPlot = function(btn) {
    var prefix = btn.dataset.targetPrefix;
    var op = btn.dataset.opName;
    // Pair ops can share a name across the pair's two elements (e.g. "square" on
    // cr_<c>_<t> and cr_<t>_<c>); the element disambiguates the lookup.
    var element = btn.dataset.element;
    // Scope to the surrounding pane so a qubit pane in #inspector-pane and a
    // pair pane in #table-pane can't fight over a duplicated id.
    var scope = btn.closest('.qubit-config-pane');
    var area = (scope && scope.querySelector('.waveform-plot-area'))
        || document.getElementById('waveform-plot-area');
    if (!area) return;
    var kind = prefix.indexOf('-') !== -1 ? 'pair' : 'qubit';

    function fail(msg) {
        area.textContent = '';
        var d = document.createElement('div');
        d.className = 'waveform-plot-err';
        d.textContent = msg || 'failed';
        area.appendChild(d);
    }

    area.textContent = '';
    var loading = document.createElement('div');
    loading.className = 'waveform-plot-loading';
    loading.textContent = 'loading…';
    area.appendChild(loading);

    var url = '/' + kind + '/' + encodeURIComponent(prefix) + '/waveform/' + encodeURIComponent(op);
    if (element) url += '?element=' + encodeURIComponent(element);
    fetch(url)
        .then(function(r) { return r.json().then(function(d) { return { ok: r.ok, data: d }; }); })
        .then(function(res) {
            if (!res.ok) { fail(res.data && res.data.error); return; }
            var data = res.data;
            area.textContent = '';
            area.appendChild(_waveformCaption(data));
            var canvas = document.createElement('div');
            canvas.style.height = '280px';
            area.appendChild(canvas);
            var traces = (data.traces || []).map(function(t) {
                return { x: t.x, y: t.y, mode: 'lines', type: 'scatter',
                         name: t.label, line: { width: 2 } };
            });
            window._plotlyRender(canvas, traces, {
                margin: { l: 50, r: 10, t: 10, b: 40 },
                xaxis: { title: 'time (ns)' },
                yaxis: { title: 'voltage (V at 50 Ω)' },
                showlegend: traces.length > 1,
                legend: { orientation: 'h', y: -0.25 },
            }, { responsive: true, displayModeBar: false });
        })
        .catch(function(err) { fail(String(err)); });
};

/* ------------------------------------------------------------------ */
/* Table filter                                                        */
/* ------------------------------------------------------------------ */

/**
 * Instant client-side filter for data tables.
 *
 * Usage in a template:
 *   <input type="search" oninput="filterTable(this, 'my-table-id')"
 *          placeholder="Filter rows...">
 *   <table id="my-table-id"> ... </table>
 *
 * Matches against the concatenated visible text of every <td> in each
 * <tbody> row.  Matching is case-insensitive.  Multiple space-separated
 * terms are AND-matched (all must appear somewhere in the row).
 */
/* ------------------------------------------------------------------ */
/* Sortable table columns                                              */
/* ------------------------------------------------------------------ */

/**
 * Client-side column sorting for data tables.  Clicking a <th class="sortable">
 * header toggles ascending/descending sort on that column.
 *
 * The <th> needs data-col="N" (0-based column index) and data-type="num"|"str".
 */
(function() {
    document.addEventListener('click', function(evt) {
        var th = evt.target.closest('th.sortable');
        if (!th) return;
        var table = th.closest('table');
        if (!table) return;
        var tbody = table.querySelector('tbody');
        if (!tbody) return;

        var col = parseInt(th.dataset.col, 10);
        var isNum = th.dataset.type === 'num';
        var asc = th.classList.contains('sort-asc');

        // Clear sort state from all headers in this table
        table.querySelectorAll('th.sortable').forEach(function(h) {
            h.classList.remove('sort-asc', 'sort-desc');
        });

        // Toggle direction
        var dir = asc ? 'desc' : 'asc';
        th.classList.add('sort-' + dir);

        var rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort(function(a, b) {
            // docs/109: a cell may carry data-sort — a display-independent sort
            // key (the phys columns sort by dBm no matter which unit is shown;
            // "12 mV" vs "1.2 V" would otherwise compare 12 > 1.2).
            var aText = (a.cells[col] ? (a.cells[col].getAttribute('data-sort')
                         || a.cells[col].textContent.trim()) : '');
            var bText = (b.cells[col] ? (b.cells[col].getAttribute('data-sort')
                         || b.cells[col].textContent.trim()) : '');
            if (isNum) {
                var aVal = parseFloat(aText.replace(/[^0-9eE.\-+]/g, '')) || 0;
                var bVal = parseFloat(bText.replace(/[^0-9eE.\-+]/g, '')) || 0;
                if (aText === '-' || aText === '') aVal = -Infinity;
                if (bText === '-' || bText === '') bVal = -Infinity;
                return dir === 'asc' ? aVal - bVal : bVal - aVal;
            }
            // numeric:true = NATURAL sort — q2 before q10 (labs with double-digit
            // qubit numbering hit q1, q10, q11, q2 with the plain comparator).
            var nat = { numeric: true, sensitivity: 'base' };
            return dir === 'asc' ? aText.localeCompare(bText, undefined, nat)
                                 : bText.localeCompare(aText, undefined, nat);
        });
        rows.forEach(function(row) { tbody.appendChild(row); });
    });
})();

/* ------------------------------------------------------------------ */
/* Keyboard navigation for data tables                                 */
/* ------------------------------------------------------------------ */

/**
 * Arrow-key navigation for clickable table rows.  Up/Down moves selection,
 * Enter triggers the HTMX load for the selected row's inspector.
 */
(function() {
    document.addEventListener('keydown', function(evt) {
        var tablePane = document.getElementById('table-pane');
        if (!tablePane) return;
        // Only handle when no input/textarea is focused
        var active = document.activeElement;
        if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.tagName === 'SELECT')) return;

        if (evt.key !== 'ArrowUp' && evt.key !== 'ArrowDown' && evt.key !== 'Enter') return;

        var rows = Array.from(tablePane.querySelectorAll('tr.clickable-row'));
        if (!rows.length) return;
        // Filter to visible rows only
        rows = rows.filter(function(r) { return r.style.display !== 'none'; });
        if (!rows.length) return;

        var current = tablePane.querySelector('tr.clickable-row.row-selected');
        var idx = current ? rows.indexOf(current) : -1;

        if (evt.key === 'Enter' && current) {
            current.click();
            evt.preventDefault();
            return;
        }
        if (evt.key === 'ArrowDown') {
            idx = Math.min(idx + 1, rows.length - 1);
        } else if (evt.key === 'ArrowUp') {
            idx = Math.max(idx - 1, 0);
        } else {
            return;
        }

        evt.preventDefault();
        if (current) current.classList.remove('row-selected');
        rows[idx].classList.add('row-selected');
        rows[idx].scrollIntoView({ block: 'nearest' });
    });
})();

/* ------------------------------------------------------------------ */
/* Inline edit: Escape cancels and restores original value             */
/* ------------------------------------------------------------------ */

/**
 * Delegated keydown handler for the inspector's inline edit inputs.
 * Escape blurs the input and restores the value it had when the form
 * was rendered, so a typo can be discarded without firing an edit.
 *
 * The input's defaultValue (HTML attribute) is the originally rendered
 * value; we use that rather than tracking state separately.
 */
document.addEventListener('keydown', function(evt) {
    if (evt.key !== 'Escape') return;
    var t = evt.target;
    if (!t || !t.classList || !t.classList.contains('edit-input')) return;
    if (t.value !== t.defaultValue) t.value = t.defaultValue;
    t.blur();
    evt.preventDefault();
});

/* The f_01↔RF_frequency 🔗 sync preference, shared with the bulk table's toggle
 * (localStorage 'quam_bulk_freqsync'): "1" unless explicitly turned off. The
 * inspector inline-edit forms send this via hx-vals so editing f_01/RF mirrors its
 * twin server-side — matching the bulk table's client-side mirror. */
window.freqSyncFlag = function () {
    try { return localStorage.getItem('quam_bulk_freqsync') === '0' ? '0' : '1'; }
    catch (e) { return '1'; }
};

/* ------------------------------------------------------------------ */
/* Settings dropdown                                                    */
/* ------------------------------------------------------------------ */

/**
 * Toggle the settings dropdown in the topbar.  Clicking outside the
 * dropdown will close it (handled by a one-time document click listener).
 */
/* Anchor a body-level popover under the trigger that opened it (docs/89).
 *
 * Both tool popovers used `position:absolute; right:0; top:100%` inside their
 * topbar <li>. From the sidebar that cannot work twice over: the sidebar is
 * `overflow-y:auto`, which CLIPS an absolutely-positioned child, and it
 * collapses to width 0. So they live at body level and get placed here, in
 * viewport coordinates, from the trigger's own rect.
 *
 * Placement is below-and-left-aligned, flipping to right-aligned or above when
 * that would leave the viewport — the calculator is ~348×560, which does not
 * fit under a trigger near the bottom of a short window. */
window._anchorPopover = function (pop, btn) {
    if (!pop || !btn || !btn.getBoundingClientRect) return;
    pop.classList.add("pop-anchored");
    // measure with the popover laid out but before we commit a position
    pop.style.left = "0px";
    pop.style.top = "0px";
    var r = btn.getBoundingClientRect();
    var w = pop.offsetWidth || 280;
    var h = pop.offsetHeight || 200;
    var pad = 6;
    var left = r.left;
    if (left + w > window.innerWidth - pad) left = window.innerWidth - w - pad;
    if (left < pad) left = pad;
    var top = r.bottom + 4;
    if (top + h > window.innerHeight - pad) {
        var above = r.top - h - 4;
        top = above >= pad ? above : Math.max(pad, window.innerHeight - h - pad);
    }
    pop.style.left = Math.round(left) + "px";
    pop.style.top = Math.round(top) + "px";
};

/* The VISIBLE trigger for a tool: the sidebar row normally, the topbar
 * fallback while the sidebar is collapsed. Decided from the collapsed class
 * rather than from layout (`offsetParent`/rects) — that is the actual
 * condition, it needs no layout pass, and it stays checkable. A trigger the
 * user really clicked always wins. */
window._toolTrigger = function (selector, preferred) {
    if (preferred && preferred.isConnected) return preferred;
    var all = Array.prototype.slice.call(document.querySelectorAll(selector));
    if (!all.length) return null;
    var collapsed = !!document.querySelector(".app-layout.sidebar-collapsed");
    var wanted = all.filter(function (b) {
        return b.classList.contains("topbar-tool") === collapsed;
    });
    return wanted[0] || all[0];
};

window.toggleSettings = function(trigger) {
    var dd = document.getElementById("settings-dropdown");
    if (!dd) return;
    var opening = dd.classList.toggle("settings-hidden");
    if (!opening) {
        // singleton: never overlap the calculator (mirrors toggleCalc)
        var cp = document.getElementById("calc-popover");
        if (cp) cp.classList.add("calc-hidden");
        window._anchorPopover(dd, window._toolTrigger(".settings-btn", trigger));
        setTimeout(function() {
            document.addEventListener("click", function closer(e) {
                if (!dd.contains(e.target) && !e.target.closest(".settings-btn")) {
                    dd.classList.add("settings-hidden");
                }
                document.removeEventListener("click", closer);
            });
        }, 0);
    }
};

/**
 * Set the UI font size by applying a data-font-size attribute on <html>.
 * Task 9 defined CSS rules that map this attribute to --font-size-base:
 *   "" (empty/absent) → 14px (default)
 *   "small"           → 13px
 *   "large"           → 16px
 * Persists the choice in localStorage.
 */
window.setFontSize = function(size) {
    if (size) {
        document.documentElement.setAttribute("data-font-size", size);
    } else {
        document.documentElement.removeAttribute("data-font-size");
    }
    try {
        localStorage.setItem("quam_font_size", size || "");
    } catch(e) {}

    var opts = document.querySelectorAll(".settings-opt[data-size]");
    for (var i = 0; i < opts.length; i++) {
        opts[i].classList.toggle(
            "settings-opt-active",
            (opts[i].getAttribute("data-size") || "") === (size || "")
        );
    }
};

/* ------------------------------------------------------------------ */
/* Sidebar tree multi-select (compare checkboxes)                      */
/* ------------------------------------------------------------------ */
// Customer ask: select MANY runs at once to compare. File-manager
// convention beats drag-rubber-band in a scrolling tree: SHIFT-click a
// checkbox to select the whole range since the last click; the Compare
// button echoes the live count and a Clear chip appears. Delegated on
// document so htmx tree re-renders never lose the behavior.
(function() {
    var lastIdx = -1;

    function boxes() {
        return Array.prototype.slice.call(
            document.querySelectorAll('#sidebar-tree input[name="paths"]'));
    }

    function syncCompareCount() {
        var n = document.querySelectorAll(
            '#sidebar-tree input[name="paths"]:checked').length;
        var cmp = document.querySelector('#compare-form .btn-compare');
        if (cmp) cmp.textContent = n > 1
            ? 'Compare Selected (' + n + ')' : 'Compare Selected';
        var trend = document.querySelector('#compare-form .btn-trend');
        if (trend) trend.textContent = n > 1
            ? 'Trend Tracker (' + n + ')' : 'Trend Tracker';
        var clr = document.getElementById('compare-clear');
        if (clr) clr.hidden = n === 0;
    }
    window.syncCompareCount = syncCompareCount;

    window.compareClearSelection = function() {
        boxes().forEach(function(b) { b.checked = false; });
        lastIdx = -1;
        syncCompareCount();
    };

    document.addEventListener('click', function(ev) {
        var t = ev.target;
        if (!t || t.name !== 'paths' || !t.closest || !t.closest('#sidebar-tree')) return;
        var all = boxes();
        var idx = all.indexOf(t);
        if (ev.shiftKey && lastIdx >= 0 && idx >= 0 && lastIdx !== idx) {
            var lo = Math.min(lastIdx, idx), hi = Math.max(lastIdx, idx);
            for (var i = lo; i <= hi; i++) all[i].checked = t.checked;
        }
        lastIdx = idx;
        syncCompareCount();
    });

    // Tree re-renders (workspace add/remove/filter) rebuild the checkboxes —
    // re-sync the count (selections inside the swapped region are gone).
    // Listener sits on document (always exists at eval time; the app-wide
    // rule forbids top-level document.body listeners).
    document.addEventListener('htmx:afterSwap', function(ev) {
        var el = ev.target;
        if (el && el.id === 'sidebar-tree') { lastIdx = -1; syncCompareCount(); }
    });
})();

// Global UI scale (Settings → "UI scale"): CSS zoom on <html>, 80%–150% in
// 10% steps — the pragmatic global-readability control given the app's many
// hardcoded px font sizes (rem-only scaling misses them; zoom scales
// everything). Applied live AND pre-paint on load (head inline script);
// persisted in quam_ui_scale. step: -1 smaller, +1 larger, 0 reset.
window.setUiScale = function(step) {
    var cur = 1;
    try { cur = parseFloat(localStorage.getItem("quam_ui_scale")) || 1; } catch(e) {}
    var next = step === 0 ? 1 : Math.round((cur + step * 0.1) * 10) / 10;
    next = Math.min(1.5, Math.max(0.8, next));
    document.documentElement.style.zoom = (Math.abs(next - 1) > 0.001) ? next : "";
    try { localStorage.setItem("quam_ui_scale", String(next)); } catch(e) {}
    window.syncUiScaleLabel();
    return next;
};

window.syncUiScaleLabel = function() {
    var el = document.getElementById("ui-scale-value");
    if (!el) return;
    var cur = 1;
    try { cur = parseFloat(localStorage.getItem("quam_ui_scale")) || 1; } catch(e) {}
    el.textContent = Math.round(cur * 100) + "%";
    el.classList.toggle("settings-opt-active", Math.abs(cur - 1) > 0.001);
};

// Explorer-only scale (r6 item 3): zooms JUST the two tree containers —
// fonts + toggles + icons + spacing coherently — and MULTIPLIES with the
// global quam_ui_scale zoom on <html>. Persisted; no restart.
window.explorerSetScale = function(scale) {
    var s = Math.min(1.7, Math.max(0.75, parseFloat(scale) || 1));
    try { localStorage.setItem("quam_explorer_scale", String(s)); } catch(e) {}
    window.explorerApplyScale();
    return s;
};

window.explorerApplyScale = function() {
    var s = 1;
    try { s = parseFloat(localStorage.getItem("quam_explorer_scale")) || 1; } catch(e) {}
    ["explorer-tree-state", "explorer-tree-wiring"].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.style.zoom = (Math.abs(s - 1) > 0.001) ? s : "";
    });
    var slider = document.getElementById("explorer-scale-slider");
    if (slider && Math.abs(parseFloat(slider.value) - s) > 0.001) slider.value = s;
    var presets = document.querySelectorAll(".tree-scale-preset");
    for (var i = 0; i < presets.length; i++) {
        presets[i].classList.toggle("active",
            Math.abs(parseFloat(presets[i].getAttribute("data-sc")) - s) < 0.001);
    }
};

window.toggleColorblindMode = function() {
    var active = document.body.classList.toggle('colorblind-mode');
    try { localStorage.setItem('quam_colorblind', active ? '1' : '0'); } catch(e) {}
    var btn = document.getElementById('colorblind-toggle');
    if (btn) btn.classList.toggle('settings-opt-active', active);
    // The topology edge-fidelity tiers no longer need a colorblind override:
    // they moved to a single-hue blue luminance ramp (see UI_CONFIG /
    // _applyThemeToPlotly), which is distinguishable under every CVD type.
};

// Restore colorblind mode on page load
(function() {
    try {
        if (localStorage.getItem('quam_colorblind') === '1') {
            document.body.classList.add('colorblind-mode');
            var btn = document.getElementById('colorblind-toggle');
            if (btn) btn.classList.add('settings-opt-active');
        }
    } catch(e) {}
})();

/* ------------------------------------------------------------------ */
/* Experiment-list density: full multi-line names <-> compact one-row  */
/* ------------------------------------------------------------------ */

/**
 * Switch the Workspace experiment list between full multi-line names
 * (default) and compact single-row truncation. The class lives on
 * <body> so it survives HTMX re-swaps of #sidebar-tree with no per-row
 * JS re-application.
 */
window.setExpListCompact = function(compact) {
    document.body.classList.toggle('exp-list-compact', compact);
    try { localStorage.setItem('quam_exp_list_compact', compact ? '1' : '0'); } catch(e) {}
    var full = document.getElementById('exp-density-full');
    var comp = document.getElementById('exp-density-compact');
    if (full) full.setAttribute('aria-pressed', compact ? 'false' : 'true');
    if (comp) comp.setAttribute('aria-pressed', compact ? 'true' : 'false');
};

// Restore density on load. Default is multi-line, so the class is added
// only when the user previously opted into compact.
(function() {
    function apply() {
        try {
            if (localStorage.getItem('quam_exp_list_compact') === '1') {
                setExpListCompact(true);
            }
        } catch(e) {}
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', apply);
    } else { apply(); }
})();

/* ------------------------------------------------------------------ */
/* Datasets experiment-filter: collapse the always-on badge grid        */
/* ------------------------------------------------------------------ */

/**
 * Collapse / expand the Datasets experiment-badge filter. The class lives on
 * <body> so the collapsed state survives HTMX swaps of the datasets page with
 * no per-render JS — the CSS hides .exp-filter-section while body has the class.
 */
window.toggleExpFilterCollapsed = function() {
    var collapsed = document.body.classList.toggle('exp-filter-collapsed');
    try { localStorage.setItem('quam_exp_filter_collapsed', collapsed ? '1' : '0'); } catch (e) {}
    var btn = document.getElementById('exp-filter-toggle');
    if (btn) btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
};

(function() {
    function apply() {
        try {
            var collapsed = localStorage.getItem('quam_exp_filter_collapsed') === '1';
            document.body.classList.toggle('exp-filter-collapsed', collapsed);
            var btn = document.getElementById('exp-filter-toggle');
            if (btn) btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        } catch (e) {}
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', apply);
    } else { apply(); }
    // Re-sync the toggle button's aria-expanded after the datasets page swaps
    // in. Delegate on `document` (not document.body) — app.js may parse before
    // <body> exists, and htmx:afterSwap bubbles to document. See the
    // no-top-level-document.body regression test.
    document.addEventListener('htmx:afterSwap', function(evt) {
        var t = evt.detail && evt.detail.target;
        if (t && t.querySelector && t.querySelector('#exp-filter-toggle')) apply();
    });
})();

/* ------------------------------------------------------------------ */
/* Keyboard activation for onclick-only tab controls (role="tab")       */
/* ------------------------------------------------------------------ */
// The dataset-detail tabs are onclick-only <a> with no href, so they aren't
// keyboard-operable on their own. They carry role="tab" tabindex="0"; this
// delegated handler activates the focused one on Enter/Space.
document.addEventListener('keydown', function(e) {
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    var el = document.activeElement;
    if (!el || !el.matches) return;
    if (el.matches('.dataset-tabs a[role="tab"]:not(.disabled)')) {
        e.preventDefault();
        el.click();
    }
});

/* ------------------------------------------------------------------ */
/* Chip Status left-nav sub-views (mirror of the in-page tab row)       */
/* ------------------------------------------------------------------ */

/**
 * Navigate to a Chip Status sub-view from the left sidebar. If we're already
 * on the Chip Status page (the in-page sub-nav exists), switch instantly via
 * setChipStatusView — no reload — and update the URL. Otherwise HTMX-navigate
 * to /topology?view=<view>. Returns false to suppress the <a> default.
 */
window.chipNavView = function(view, ev) {
    if (ev && ev.preventDefault) ev.preventDefault();
    if (typeof window.setChipStatusView === 'function' && document.querySelector('.topo-subnav')) {
        window.setChipStatusView(view, null, true);   // scroll to the chosen section
        try { history.replaceState(null, '', '/topology?view=' + view); } catch (e) {}
        if (window.syncSidebarNavActive) window.syncSidebarNavActive();
    } else if (window.htmx) {
        window.htmx.ajax('GET', '/topology?view=' + view,
                         { target: '#table-pane', swap: 'innerHTML' }).then(function() {
            try { history.pushState(null, '', '/topology?view=' + view); } catch (e) {}
            if (window.syncSidebarNavActive) window.syncSidebarNavActive();
        });
    } else {
        window.location.href = '/topology?view=' + view;
    }
    return false;
};

/** Generic collapse / expand for a sidebar sub-item list (Chip Status, Config). */
window.toggleNavSub = function(btn, ulId, storageKey) {
    var ul = document.getElementById(ulId);
    if (!ul) return;
    var collapsed = ul.classList.toggle('nav-subitems-collapsed');
    try { localStorage.setItem(storageKey, collapsed ? '1' : '0'); } catch (e) {}
    if (btn) btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
};

/** Kept as a wrapper: existing onclick handlers + tests reference it. */
window.toggleChipStatusSub = function(btn) {
    window.toggleNavSub(btn, 'chip-status-subnav', 'quam_chipstatus_nav_collapsed');
};

/** Projects subnav cap (r8 feedback): only the first few projects render
 *  visible; this expands/collapses the rest. Preference persists in
 *  localStorage and is re-applied by the fragment's inline restore script
 *  on every lazy re-load. */
window.qualibrateSubnavToggleAll = function(btn) {
    var ul = btn.closest('ul');
    if (!ul) return;
    var expand = btn.getAttribute('data-expanded') !== '1';
    ul.querySelectorAll('[data-subnav-extra]').forEach(function(li) {
        li.hidden = !expand;
    });
    btn.setAttribute('data-expanded', expand ? '1' : '0');
    btn.textContent = expand ? '… show fewer'
                             : (btn.getAttribute('data-label-all') || '… show all');
    try {
        localStorage.setItem('quam_projects_subnav_all', expand ? '1' : '0');
    } catch (e) { /* private mode */ }
};

// Restore each sub-list's collapsed state on load. Chip Status defaults
// expanded, the Config group defaults collapsed (the server also renders it
// collapsed, so JS only ever *removes* the class — no flash). A sub-list
// holding the active page is force-expanded regardless of the stored state.
(function() {
    var SUBNAVS = [
        // Projects first (docs/63): the primary entry point defaults OPEN and,
        // unlike before, its collapsed choice now round-trips (the key was
        // written by the toggle but never read back — it re-collapsed on
        // every navigation).
        { id: 'qualibrate-subnav',      key: 'quam_qualibrate_nav_collapsed',   def: '0' },
        { id: 'chip-status-subnav',     key: 'quam_chipstatus_nav_collapsed',   def: '0' },
        { id: 'config-subnav',          key: 'quam_config_nav_collapsed',       def: '1' },
        // r15 IA groups (docs/69). Chip Components is primary navigation and
        // defaults OPEN; the tool groups default collapsed (force-expand on an
        // active child keeps context visible either way).
        { id: 'chip-components-subnav', key: 'quam_components_nav_collapsed',   def: '0' },
        { id: 'live-edit-subnav',       key: 'quam_liveedit_nav_collapsed',     def: '1' },
        { id: 'state-history-subnav',   key: 'quam_statehistory_nav_collapsed', def: '1' },
        { id: 'datasets-subnav',        key: 'quam_datasets_nav_collapsed',     def: '1' },
    ];
    function apply() {
        SUBNAVS.forEach(function(s) {
            var ul = document.getElementById(s.id);
            if (!ul) return;
            var btn = document.querySelector('.nav-sub-toggle[aria-controls="' + s.id + '"]');
            var collapsed;
            if (ul.querySelector('a.active')) {
                collapsed = false;          // never hide the active page
            } else {
                var stored = null;
                try { stored = localStorage.getItem(s.key); } catch (e) {}
                collapsed = (stored === null ? s.def : stored) === '1';
            }
            ul.classList.toggle('nav-subitems-collapsed', collapsed);
            if (btn) btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', apply);
    } else { apply(); }
})();

/* ------------------------------------------------------------------ */
/* Dark / Light theme toggle                                            */
/* ------------------------------------------------------------------ */

/* Store the light-mode defaults for UI_CONFIG structural colors so we
   can restore them when switching back from dark mode.                 */
var _lightDefaults = {
    topoHoverBg:     '#ffffff',
    topoHoverBorder: '#cccccc',
    subLabelColor:   '#555',
    edgeLabelColor:  '#444',
    edgeFidelityGood:'#08519c',
    edgeFidelityWarn:'#4292c6',
    edgeFidelityBad: '#9ecae1',
    iwGridBg:        '#f8f8f8',
    iwGridBorder:    '#dddddd',
    iwRowLabel:      '#aaaaaa',
    iwSeparator:     '#bbbbbb',
    iwSubSeparator:  '#cccccc',
    iwSubLabel:      '#999999',
    iwFemLabel:      '#888888',
    iwEmptyFill:     '#e8e8e8',
    iwEmptyStroke:   '#cccccc',
    iwUnassignedFill:'#ffffff',
    iwUnassignedStroke:'#cccccc',
    nullCellColor:   '#f0f0f0',
};

/**
 * Apply theme-appropriate colors to UI_CONFIG for Plotly charts and SVG.
 * Called when the user toggles dark/light mode.
 */
function _applyThemeToPlotly(theme) {
    if (!window.UI_CONFIG) return;
    var t = UI_CONFIG.plotly.topology;
    var iw = UI_CONFIG.instrumentWiring;
    if (theme === 'dark') {
        t.hoverBg      = '#2a2a3a';
        t.hoverBorder  = '#555';
        t.subLabelFont.color  = '#aaa';
        t.edgeLabelFont.color = '#bbb';
        /* Same single-hue blue ramp, luminance-flipped for the dark bg:
           brightest = best (pops against the dark), dimmest = washed toward
           the background — mirrors "pale = bad" on the light theme. */
        t.edgeFidelityGood = '#85bbe8';
        t.edgeFidelityWarn = '#4a86c2';
        t.edgeFidelityBad  = '#2e5578';
        iw.gridBg            = '#1e1e2e';
        iw.gridBorder        = '#444';
        iw.rowLabelColor     = '#666';
        iw.separatorColor    = '#555';
        iw.subSeparatorColor = '#555';
        iw.subLabelColor     = '#777';
        iw.femLabelColor     = '#888';
        iw.emptyPortFill     = '#2a2a3a';
        iw.emptyPortStroke   = '#444';
        iw.unassignedFill    = '#1e1e2e';
        iw.unassignedStroke  = '#555';
        t.dashboard.nullCellColor = '#2a2a3a';
    } else {
        t.hoverBg      = _lightDefaults.topoHoverBg;
        t.hoverBorder  = _lightDefaults.topoHoverBorder;
        t.subLabelFont.color  = _lightDefaults.subLabelColor;
        t.edgeLabelFont.color = _lightDefaults.edgeLabelColor;
        t.edgeFidelityGood = _lightDefaults.edgeFidelityGood;
        t.edgeFidelityWarn = _lightDefaults.edgeFidelityWarn;
        t.edgeFidelityBad  = _lightDefaults.edgeFidelityBad;
        iw.gridBg            = _lightDefaults.iwGridBg;
        iw.gridBorder        = _lightDefaults.iwGridBorder;
        iw.rowLabelColor     = _lightDefaults.iwRowLabel;
        iw.separatorColor    = _lightDefaults.iwSeparator;
        iw.subSeparatorColor = _lightDefaults.iwSubSeparator;
        iw.subLabelColor     = _lightDefaults.iwSubLabel;
        iw.femLabelColor     = _lightDefaults.iwFemLabel;
        iw.emptyPortFill     = _lightDefaults.iwEmptyFill;
        iw.emptyPortStroke   = _lightDefaults.iwEmptyStroke;
        iw.unassignedFill    = _lightDefaults.iwUnassignedFill;
        iw.unassignedStroke  = _lightDefaults.iwUnassignedStroke;
        t.dashboard.nullCellColor = _lightDefaults.nullCellColor;
    }
}

/**
 * Toggle between light and dark themes.
 * Persists the choice in localStorage and updates Plotly chart colors.
 */
window.toggleTheme = function() {
    var html = document.documentElement;
    var isDark = html.getAttribute('data-theme') === 'dark';
    var newTheme = isDark ? 'light' : 'dark';
    html.setAttribute('data-theme', newTheme);
    try { localStorage.setItem('quam_theme', newTheme); } catch(e) {}
    var btn = document.getElementById('theme-toggle');
    if (btn) {
        btn.classList.toggle('settings-opt-active', newTheme === 'dark');
        btn.textContent = newTheme === 'dark' ? 'Dark mode' : 'Light mode';   // reflect current state
    }
    _applyThemeToPlotly(newTheme);
    // House-themed plots (ndview + any surface using PlotTheme) follow instantly.
    if (window.PlotTheme) window.PlotTheme.retheme();
};

// Restore theme on page load. Dark is the default; the inline script
// in base.html already applied data-theme on <html> to prevent FOUC.
// This block keeps Plotly and the toggle-button state in sync.
(function() {
    var theme = 'dark';
    try {
        var saved = localStorage.getItem('quam_theme');
        if (saved === 'light' || saved === 'dark') theme = saved;
        // honor a ?theme= override (mirrors the FOUC init in base.html) so a
        // forced theme stays applied to the toggle/Plotly state too.
        var qp = new URLSearchParams(location.search).get('theme');
        if (qp === 'light' || qp === 'dark') theme = qp;
    } catch(e) {}
    if (theme === 'dark') _applyThemeToPlotly('dark');
    // r16 ⑤-1: retheme() used to run only on the theme TOGGLE — plots
    // rendered before Plotly/theme settled on a dark-mode LOAD kept the
    // light template's near-black axis text. One pass after load fixes any
    // early renders; per-render theming is houseLayout's job.
    document.addEventListener('DOMContentLoaded', function() {
        setTimeout(function() {
            if (window.PlotTheme && window.PlotTheme.retheme) window.PlotTheme.retheme();
        }, 800);
    });
    document.addEventListener('DOMContentLoaded', function() {
        var btn = document.getElementById('theme-toggle');
        if (!btn) return;
        btn.classList.toggle('settings-opt-active', theme === 'dark');
        btn.textContent = theme === 'dark' ? 'Dark mode' : 'Light mode';   // correct label on load
    });
})();

/* ------------------------------------------------------------------ */
/* Sidebar toggle                                                       */
/* ------------------------------------------------------------------ */

/**
 * Toggle the sidebar between expanded and collapsed states.
 * Persists the choice in localStorage so it survives page reloads.
 * The IIFE in base.html reads this key on load to restore the state.
 */
window.toggleSidebar = function() {
    var layout = document.querySelector(".app-layout");
    if (!layout) return;
    var collapsed = layout.classList.toggle("sidebar-collapsed");
    try {
        localStorage.setItem("quam_sidebar_collapsed", collapsed ? "1" : "0");
    } catch(e) {}
};

/* docs/126 cycled THREE states (sidebar → +top bar → restore). Customer
   feedback (2026-08-27): "don't make us press it twice" — ONE press now
   collapses the sidebar AND the top bar together (the floating ☰ + the sync
   badge/Auto-Sync pill remain), and one press on the floating ☰ restores
   both. Any mixed state the user reached through the individual toggles
   resolves the same way: anything still visible ⇒ collapse everything;
   nothing visible ⇒ restore everything. Both legs persist through the
   toggles' own localStorage keys. */
window.cycleChrome = function() {
    var layout = document.querySelector(".app-layout");
    var sbCollapsed = !!(layout && layout.classList.contains("sidebar-collapsed"));
    var tbHidden = document.documentElement.classList.contains("topbar-hidden");
    var collapseAll = !(sbCollapsed && tbHidden);
    if (sbCollapsed !== collapseAll) window.toggleSidebar();
    if (tbHidden !== collapseAll) window.toggleTopbar();
};

/**
 * Global toggle that hides the top title bar to reclaim vertical space. The class
 * lives on <html> (NOT .app-layout) because the .topbar sits OUTSIDE .app-layout;
 * the matching CSS also zeroes --topbar-height so the calc(100vh - topbar) panels
 * don't leave a dead strip. Restored on load by restorePrefs() in base.html. When
 * hidden, the .topbar-reveal handle (always reachable) brings it back.
 */
window.toggleTopbar = function() {
    var hidden = document.documentElement.classList.toggle("topbar-hidden");
    try {
        localStorage.setItem("quam_topbar_hidden", hidden ? "1" : "0");
    } catch(e) {}
};

/**
 * Per-page header collapse (Item 4): hide the current page's .table-header-row
 * heading to give the list/content more vertical room. The class lives on <body>
 * (survives HTMX #table-pane swaps) and the toggle button lives in #content-area
 * (outside the swapped pane), so it stays reachable when collapsed. Restored on
 * load by restorePrefs() in base.html.
 */
window.togglePageHeader = function() {
    var collapsed = document.body.classList.toggle("pageheader-collapsed");
    try {
        localStorage.setItem("quam_pageheader_collapsed", collapsed ? "1" : "0");
    } catch(e) {}
    var b = document.getElementById("pageheader-toggle");
    if (b) b.setAttribute("aria-expanded", collapsed ? "false" : "true");
};

/* ------------------------------------------------------------------ */
/* Sidebar: load dataset detail when clicking a run entry               */
/* ------------------------------------------------------------------ */

/**
 * Delegated click handler: clicking a sidebar RUN entry VIEWS its dataset detail
 * — WITHOUT activating the run's frozen quam_state. The live chip the user
 * loaded stays the active, editable context (they want to stick to their state
 * folder; a dataset click must not flip the whole app into read-only archive
 * mode). The run's frozen state is opt-in via the detail's "Load State" button.
 * Loads into the inspector pane when present (Datasets page / Explorer split),
 * else the main #table-pane so it still works from any page.
 */
/* r13: tint the active run's ANCESTOR folders too — every containing
 * <details> summary gets .tree-branch-active, so even a fully collapsed tree
 * still shows which folder holds the open experiment. Pass the entry element
 * to mark its chain; pass null to re-derive from the current
 * .tree-entry-active (used after tree swaps rebuild the DOM). */
window._markActiveTreeBranch = function(el) {
    document.querySelectorAll('.tree-branch-active').forEach(function(d) {
        d.classList.remove('tree-branch-active');
    });
    if (!el) el = document.querySelector('.tree-entry-click.tree-entry-active');
    if (!el) return;
    var d = el.closest('details');
    while (d) {
        d.classList.add('tree-branch-active');
        d = d.parentElement ? d.parentElement.closest('details') : null;
    }
};

document.addEventListener('click', function(evt) {
    var el = evt.target.closest('.tree-entry-click[data-uid]');
    if (!el) return;
    var uid = el.getAttribute('data-uid');
    if (uid && window.htmx) {
        var hasInspector = !!document.getElementById('inspector-pane');
        var target = hasInspector ? '#inspector-pane' : '#table-pane';
        // Mark the active run in the tree (the flip-compare gesture needs to
        // see WHICH run is open at a glance).
        document.querySelectorAll('.tree-entry-active').forEach(function(a) {
            a.classList.remove('tree-entry-active');
        });
        el.classList.add('tree-entry-active');
        window._markActiveTreeBranch(el);
        _dsMarkSlowLoad(target, el.getAttribute('data-run-id'));
        // CRITICAL: pass `source` so htmx reads the target's hx-sync and queues
        // the request on the TARGET, not document.body. Without it every dataset
        // load shares body's single (timeout-0) queue, so one slow/stalled load
        // wedges every later click → the intermittent "Datasets frozen" dead-clicks.
        htmx.ajax('GET', '/dataset/' + uid,
                  {source: target, target: target, swap: 'innerHTML'});
    }
});

/* Honest loading feedback for run loads: the pane used to just dim the PREVIOUS
 * run's content for the whole request — during rapid comparison the user reads a
 * stale panel that merely looks gray. After the server-side fix loads are ~ms,
 * so only flag the genuinely slow ones: after 200ms in flight, overlay a clear
 * "Loading #id…" chip (CSS .ds-slow-loading::after reads data-loading-run). */
var _dsSlowTimer = null;
function _dsMarkSlowLoad(targetSel, runId) {
    var pane = document.querySelector(targetSel);
    if (!pane) return;
    if (_dsSlowTimer) clearTimeout(_dsSlowTimer);
    _dsSlowTimer = setTimeout(function() {
        pane.setAttribute('data-loading-run', runId ? ('#' + runId) : '…');
        pane.classList.add('ds-slow-loading');
    }, 200);
    var clear = function(e) {
        if (e.detail && e.detail.target && e.detail.target !== pane) return;
        if (_dsSlowTimer) { clearTimeout(_dsSlowTimer); _dsSlowTimer = null; }
        pane.classList.remove('ds-slow-loading');
        pane.removeAttribute('data-loading-run');
        document.removeEventListener('htmx:afterSwap', clear);
        document.removeEventListener('htmx:responseError', clear);
    };
    document.addEventListener('htmx:afterSwap', clear);
    document.addEventListener('htmx:responseError', clear);
}

/* Prev/next run navigation — walks the sidebar tree's VISIBLE run entries in
 * display order (the same list the user scans), relative to the currently-open
 * run. Buttons in the dataset inspector header + the [ and ] keys. Clicking via
 * el.click() reuses the exact delegated handler above (source/hx-sync, active
 * marker, slow-load chip — one path, no drift). */
window.dsNavRun = function(dir) {
    // The CURRENT (unprefixed) detail only — in pinned compare the left column's
    // ids are "pinned-"-prefixed and must not anchor the navigation.
    var root = document.getElementById('ds-detail-root');
    var curUid = root ? root.getAttribute('data-uid') : null;
    // r16 ④: server neighbor fallback — a run opened from the Datasets TABLE
    // (or with the tree collapsed / date group closed / filter hiding it) has
    // no visible tree entries, so both nav buttons were silently dead.
    function serverNeighbor() {
        if (!curUid || !window.htmx) return;
        fetch('/dataset/' + curUid + '/neighbor?dir=' + dir)
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (!d || !d.uid) return;   // genuinely at the end
                var hasInspector = !!document.getElementById('inspector-pane');
                var target = hasInspector ? '#inspector-pane' : '#table-pane';
                _dsMarkSlowLoad(target, d.run_id);
                htmx.ajax('GET', '/dataset/' + d.uid,
                          {source: target, target: target, swap: 'innerHTML'});
            }).catch(function() {});
    }
    var entries = Array.prototype.filter.call(
        document.querySelectorAll('.tree-entry-click[data-uid]'),
        function(e) { return e.offsetParent !== null; });   // visible only
    if (!entries.length) { serverNeighbor(); return; }
    var idx = -1;
    for (var i = 0; i < entries.length; i++) {
        if (entries[i].getAttribute('data-uid') === curUid) { idx = i; break; }
    }
    if (idx === -1) { serverNeighbor(); return; }   // open run not in the tree
    // docs/126 ⑥: dir may be ±10 (the fast buttons). A big step past the end
    // CLAMPS to the end entry (the server neighbor walk is single-step only);
    // a single step past the end keeps the server fallback.
    var tgt = idx + dir;
    if (Math.abs(dir) > 1) tgt = Math.max(0, Math.min(entries.length - 1, tgt));
    if (tgt === idx) return;
    var next = entries[tgt];
    if (!next) { serverNeighbor(); return; }        // tree end — folder may have more
    next.scrollIntoView({block: 'nearest'});
    next.click();
};

/* docs/126 r3: the run-number jump lives on the Prev State comparison bar
 * (its original home per the request) — typing a number compares the open
 * run against exactly that run. A number with no saved state renders the
 * route's honest fallback note in the same pane. */
window.prevDiffJump = function(inp, uid, compact) {
    var n = parseInt((inp && inp.value || '').replace(/[^0-9]/g, ''), 10);
    if (!isFinite(n)) return;
    window.loadPrevDiff(inp, uid, n, compact);
};

// Enter/Space open a keyboard-focused tree run entry (they're tabindex=0 now).
document.addEventListener('keydown', function(evt) {
    if (evt.key !== 'Enter' && evt.key !== ' ') return;
    var el = document.activeElement;
    if (!el || !el.classList || !el.classList.contains('tree-entry-click')) return;
    if (!el.hasAttribute('data-uid')) return;   // chip-folder entries keep hx-post
    evt.preventDefault();
    el.click();
});

// [ = previous run, ] = next run (outside text fields). The bracket keys avoid
// hijacking arrow-key page scrolling / table navigation.
document.addEventListener('keydown', function(evt) {
    if (evt.key !== '[' && evt.key !== ']') return;
    if (evt.ctrlKey || evt.metaKey || evt.altKey) return;
    var a = document.activeElement;
    if (a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable)) return;
    // Only when a dataset detail is open (so [ ] stays free elsewhere).
    if (!document.getElementById('ds-detail-root')) return;
    evt.preventDefault();
    window.dsNavRun(evt.key === '[' ? -1 : 1);
});

/* "⤢ Open as a full page": render this run's detail into the main #table-pane
 * (full width — figures at real size), keeping the sidebar tree for navigation.
 * Closes the inspector copy first so the two panes never hold duplicate ids. */
window.dsOpenFullPage = function(btn) {
    var root = btn.closest('[data-uid]');
    var uid = root ? root.getAttribute('data-uid') : null;
    if (!uid || !window.htmx) return;
    if (window.closeInspector) window.closeInspector();
    htmx.ajax('GET', '/dataset/' + uid,
              {source: '#table-pane', target: '#table-pane', swap: 'innerHTML'});
};

/* "vs prev": one-click compare against the previous run of the SAME experiment
 * (the calibration engineer's core question). The server resolves the prior
 * same-node run and 302s to /datasets/compare — the XHR follows transparently. */
window.dsComparePrev = function(btn) {
    var root = btn.closest('[data-uid]');
    var uid = root ? root.getAttribute('data-uid') : null;
    if (!uid || !window.htmx) return;
    htmx.ajax('GET', '/dataset/' + uid + '/compare-prev',
              {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
};

/* ── Compare basket: Alt+click tree runs to collect 2-8, then compare ────────
 * The sidebar tree's checkboxes feed the quam-STATE compare (different thing);
 * this basket feeds the figures+fits compare (/datasets/compare) without a trip
 * to the Datasets table. A floating chip bar shows the collection. */
window._dsBasket = [];
function _dsBasketRender() {
    var bar = document.getElementById('ds-basket-bar');
    if (!window._dsBasket.length) { if (bar) bar.remove(); return; }
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'ds-basket-bar';
        document.body.appendChild(bar);
    }
    var chips = window._dsBasket.map(function(u) {
        var rid = u.split(':')[1] || u;
        return '<span class="ds-basket-chip">#' + rid +
               '<button type="button" data-drop="' + u + '" title="Remove">&times;</button></span>';
    }).join('');
    bar.innerHTML = '<span class="ds-basket-label">Compare:</span>' + chips +
        (window._dsBasket.length >= 2
            ? '<button type="button" class="ds-basket-go">Compare ' + window._dsBasket.length + '</button>'
            : '<span class="muted" style="font-size:0.75rem">Alt+click more runs…</span>') +
        '<button type="button" class="ds-basket-clear" title="Clear">Clear</button>';
}
document.addEventListener('click', function(evt) {
    var t = evt.target;
    if (t.closest && t.closest('#ds-basket-bar')) {
        if (t.classList.contains('ds-basket-go') && window.htmx) {
            htmx.ajax('GET', '/datasets/compare?ids=' + window._dsBasket.join(','),
                      {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
        } else if (t.classList.contains('ds-basket-clear')) {
            window._dsBasket = []; _dsBasketRender();
        } else if (t.hasAttribute('data-drop')) {
            var u = t.getAttribute('data-drop');
            window._dsBasket = window._dsBasket.filter(function(x) { return x !== u; });
            _dsBasketRender();
        }
        return;
    }
    // Alt+click a tree run → toggle it in the basket (and DON'T open its detail).
    if (!evt.altKey) return;
    var el = t.closest && t.closest('.tree-entry-click[data-uid]');
    if (!el) return;
    evt.preventDefault(); evt.stopImmediatePropagation();
    var uid = el.getAttribute('data-uid');
    var i = window._dsBasket.indexOf(uid);
    if (i !== -1) window._dsBasket.splice(i, 1);
    else if (window._dsBasket.length < 8) window._dsBasket.push(uid);
    _dsBasketRender();
}, true);   // capture: pre-empt the plain-click open handler on Alt+click

/* ------------------------------------------------------------------ */
/* Sidebar entry right-click context menu                              */
/* ------------------------------------------------------------------ */

/**
 * Right-clicking a sidebar experiment entry opens a small menu offering
 * "Copy folder path" (reuses copyWithFeedback) and "Open in Explorer"
 * (POSTs to /open-folder, which validates the path is inside a workspace
 * root before launching the OS file manager). Delegated on document so it
 * survives the sidebar's HTMX re-renders.
 */
window._closeSidebarContextMenu = function() {
    var m = document.getElementById('sidebar-context-menu');
    if (m) m.remove();
};

function _showEntryContextMenu(evt, el) {
    window._closeSidebarContextMenu();
    var folder = el.getAttribute('data-folder-path') || '';

    var menu = document.createElement('div');
    menu.id = 'sidebar-context-menu';
    menu.className = 'sidebar-context-menu';
    menu.setAttribute('role', 'menu');

    var copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'sidebar-context-item';
    copyBtn.textContent = 'Copy folder path';
    copyBtn.onclick = function() {
        window.copyWithFeedback(folder, el, 'Copied folder path to clipboard.');
        window._closeSidebarContextMenu();
    };

    var openBtn = document.createElement('button');
    openBtn.type = 'button';
    openBtn.className = 'sidebar-context-item';
    openBtn.textContent = 'Open in Explorer';
    openBtn.onclick = function() {
        window.openFolderInExplorer(folder);
        window._closeSidebarContextMenu();
    };

    menu.appendChild(copyBtn);
    menu.appendChild(openBtn);
    document.body.appendChild(menu);

    // Position at the cursor, clamped to the viewport (6px padding).
    var rect = menu.getBoundingClientRect();
    var x = evt.clientX, y = evt.clientY;
    if (x + rect.width + 6 > window.innerWidth) x = window.innerWidth - rect.width - 6;
    if (y + rect.height + 6 > window.innerHeight) y = window.innerHeight - rect.height - 6;
    menu.style.left = Math.max(6, x) + 'px';
    menu.style.top = Math.max(6, y) + 'px';
}

document.addEventListener('contextmenu', function(evt) {
    var el = evt.target.closest('.tree-entry-click[data-folder-path]');
    if (!el) return;
    evt.preventDefault();
    _showEntryContextMenu(evt, el);
});

// Dismiss on click-away, Escape, scroll, or any HTMX swap.
document.addEventListener('click', function(evt) {
    if (!evt.target.closest('#sidebar-context-menu')) window._closeSidebarContextMenu();
});
document.addEventListener('keydown', function(evt) {
    if (evt.key === 'Escape') window._closeSidebarContextMenu();
});
document.addEventListener('scroll', function() { window._closeSidebarContextMenu(); }, true);
// htmx events bubble to document; bind there (document.body is null at head parse-time).
document.addEventListener('htmx:beforeSwap', function() { window._closeSidebarContextMenu(); });

/**
 * Ask the backend to open `folderPath` in the OS file explorer. The route
 * validates the path is inside a workspace root and translates WSL→Windows
 * paths server-side; we only surface the {ok,error} result as a toast.
 */
window.openFolderInExplorer = function(folderPath) {
    if (!folderPath) { window.showToast('No folder path available', 'warning'); return; }
    fetch('/open-folder', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: 'folder=' + encodeURIComponent(folderPath)
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.ok) window.showToast('Opening folder…', 'success');
        else window.showToast('Could not open: ' + (data.error || 'unknown error'), 'error');
    })
    .catch(function(e) { window.showToast('Request failed: ' + e, 'error'); });
};

/* ------------------------------------------------------------------ */
/* Inspector close                                                      */
/* ------------------------------------------------------------------ */

/**
 * Close the inspector panel by clearing its content and dispatching
 * a custom "inspector-closed" event.  The Split.js lifecycle manager
 * in base.html listens for this event and recalculates the layout
 * (hiding the inspector, returning table-pane to full height).
 */
window.closeInspector = function() {
    var pane = document.getElementById("inspector-pane");
    if (!pane) return;
    // Purge any Plotly charts before blanking — innerHTML="" alone leaves
    // dangling Plotly <defs>/clip-paths and leaks WebGL contexts (same reason
    // the htmx:beforeSwap purge exists; this path bypasses htmx).
    if (typeof Plotly !== "undefined") {
        var plots = pane.querySelectorAll(".js-plotly-plot");
        for (var i = 0; i < plots.length; i++) {
            try { Plotly.purge(plots[i]); } catch (e) {}
        }
    }
    pane.innerHTML = "";
    document.body.dispatchEvent(new Event("inspector-closed"));
};

/* A State History stage/restore replaces the working copy (and live, in Mode 2)
   wholesale, so any qubit/pair/pulse inspector pane left open from another menu
   now shows pre-restore values — and the entity it described (e.g. a pulse that
   the snapshot doesn't have) may no longer exist, which would 404 the next edit.
   Clear the pane so the user can never act on stale content. The server has
   already rebuilt every derived cache; this is purely the client catch-up.

   docs/65 additions:
   - ALSO soft-refresh the main state surface: the stage routes (State History
     stage, dataset "Load State", the tray's "Revert last apply") emit only
     HX-Trigger events, and the Live-Edit grid re-pulls only on the DOM event
     quam:state-changed — nothing bridged them, so /bulk kept showing
     pre-stage values indefinitely ("Load State does nothing" report). The
     same _softRefreshLiveSurface a sync pull uses is the bridge.
   - Do NOT close the inspector when it hosts a DATASET DETAIL: that's an
     immutable run archive (never stale), and it's exactly where the user just
     pressed "Load State" — closing it would erase the confirmation they're
     reading. */
document.addEventListener("stateRestored", function() {
    var dsDetail = document.querySelector("#inspector-pane #ds-detail-root");
    if (!dsDetail && window.closeInspector) window.closeInspector();
    // audit-r10: the stage already force-gated any pending edits; typed-but-
    // uncommitted grid text belongs to the REPLACED state, so the grids'
    // dirty-cell confirm must not veto (or double-prompt) this refresh —
    // time-boxed flag consumed by the beforeSwap guards.
    window._stateRestoredRefresh = Date.now();
    _keepPaneScroll();
    _softRefreshLiveSurface();
});

/* ------------------------------------------------------------------ */
/* Focus retention after inline edit                                    */
/* ------------------------------------------------------------------ */

/**
 * Re-focus the edit input whose hidden dot_path field matches the
 * given path.  Called from a <script> tag injected at the bottom of
 * _qubit_detail.html when the detail is rendered after an edit.
 *
 * Uses requestAnimationFrame so the DOM swap is fully settled before
 * we attempt to focus.  Positions the cursor at the end of the value.
 */
window.focusEditInput = function(dotPath) {
    requestAnimationFrame(function() {
        var hidden = document.querySelector(
            'input[type="hidden"][name="dot_path"][value="' + dotPath + '"]'
        );
        if (!hidden) return;
        var input = hidden.parentElement.querySelector('input[name="value"]');
        if (!input) return;
        input.focus();
        var len = input.value.length;
        input.setSelectionRange(len, len);
    });
};

/* ------------------------------------------------------------------ */
/* Pending tray toggle                                                 */
/* ------------------------------------------------------------------ */

window.togglePendingTray = function() {
    var drawer = document.getElementById("tray-drawer");
    var label  = document.getElementById("tray-toggle-label");
    if (!drawer) return;
    var open = drawer.classList.toggle("tray-expanded");
    if (label) label.textContent = open ? "\u25B2 Close" : "\u25BC Review";
    try { sessionStorage.setItem("quam_tray_open", open ? "1" : "0"); } catch(e) {}
};

window._restoreTrayState = function() {
    var drawer = document.getElementById("tray-drawer");
    var label  = document.getElementById("tray-toggle-label");
    if (drawer) {
        var open = false;
        try { open = sessionStorage.getItem("quam_tray_open") === "1"; } catch(e) {}
        drawer.classList.toggle("tray-expanded", open);
        if (label) label.textContent = open ? "\u25B2 Close" : "\u25BC Review";
    }
    // Clear stale sidebar pending markers whenever the tray reports ZERO pending
    // changes \u2014 keyed on the tray's data-change-count, NOT the old
    // "#pending-tray.tray-empty" (which is only set when there's NO active chip, so
    // after a save/apply on a loaded chip the markers used to persist forever \u2014 audit
    // P1 tray staleness).
    var tray = document.getElementById("pending-tray");
    var cc = tray ? parseInt(tray.getAttribute("data-change-count") || "0", 10) : 0;
    if (!cc) {
        var pending = document.querySelectorAll(".tree-row-pending");
        for (var i = 0; i < pending.length; i++) pending[i].classList.remove("tree-row-pending");
    }
};

/* ------------------------------------------------------------------ */
/* Live-state review / sync (working copy vs. live chip)                */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* Modal accessibility: focus trap + focus restore, and a toast helper  */
/* ------------------------------------------------------------------ */

function _focusableIn(container) {
    var sel = 'a[href], button:not([disabled]), input:not([disabled]), ' +
              'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    return Array.prototype.slice.call(container.querySelectorAll(sel))
        .filter(function(el) { return el.offsetWidth > 0 || el.offsetHeight > 0; });
}

/* A trap whose container is hidden or gone must never keep eating keys.
   checkVisibility (Chromium 105+/WebView2) also catches ancestor-hidden
   containers (e.g. a card inside a display:none overlay); the fallback chain
   covers engines without it (incl. jsdom, which has no layout, hence the
   computed-display ancestor walk instead of offsetWidth alone). */
function _trapContainerGone(c) {
    if (!c.isConnected || c.hidden) return true;
    if (typeof c.checkVisibility === "function") return !c.checkVisibility();
    if (c.offsetWidth || c.offsetHeight) return false;
    for (var el = c; el && el.nodeType === 1; el = el.parentElement) {
        if (window.getComputedStyle(el).display === "none") return true;
    }
    return false;
}

/**
 * Trap keyboard focus inside `container` until released. Tab/Shift+Tab cycle
 * within the modal; Escape calls `onEscape` (if given). Returns a release()
 * that detaches the handler and restores focus to whatever was focused when
 * the trap was set (the opener). Stored on `container._releaseTrap` by callers.
 *
 * Leak-proof by construction (the "global Tab is dead" bug): a leaked CAPTURE
 * handler whose container had been hidden used to swallow every Tab in the app
 * (nothing focusable in a hidden container → unconditional preventDefault).
 * Two defenses, so no caller-discipline mistake can ever kill Tab again:
 *   1. Re-trapping an already-trapped container releases the previous trap
 *      first (an unguarded double-open — e.g. Ctrl+K while the palette was
 *      already up — used to overwrite the stored release and orphan the old
 *      handler forever).
 *   2. Self-heal: on any keydown, a trap whose container is hidden/detached
 *      detaches itself and swallows nothing (no focus restore — the opener
 *      context is long stale by then).
 */
window.trapFocus = function(container, onEscape) {
    if (!container) return function() {};
    if (container.__trapRelease) { try { container.__trapRelease(); } catch (e) {} }
    var opener = document.activeElement;
    var released = false;
    function detach() {
        if (released) return;
        released = true;
        document.removeEventListener("keydown", onKey, true);
        if (container.__trapRelease === release) container.__trapRelease = null;
    }
    function onKey(e) {
        if (_trapContainerGone(container)) { detach(); return; }
        if (e.key === "Escape" && onEscape) { e.preventDefault(); onEscape(); return; }
        if (e.key !== "Tab") return;
        var f = _focusableIn(container);
        if (!f.length) { e.preventDefault(); return; }
        var first = f[0], last = f[f.length - 1];
        if (!container.contains(document.activeElement)) {
            e.preventDefault(); first.focus();
        } else if (e.shiftKey && document.activeElement === first) {
            e.preventDefault(); last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault(); first.focus();
        }
    }
    function release() {
        if (released) return;
        detach();
        if (opener && typeof opener.focus === "function" && document.body.contains(opener)) {
            try { opener.focus(); } catch (e) {}
        }
    }
    document.addEventListener("keydown", onKey, true);
    container.__trapRelease = release;
    requestAnimationFrame(function() {
        if (released) return;
        // Don't override focus the caller already placed inside the modal.
        if (container.contains(document.activeElement)) return;
        var f = _focusableIn(container);
        try { (f[0] || container).focus(); } catch (e) {}
    });
    return release;
};

/**
 * Show a transient toast in #status-bar. level: 'info' | 'success' | 'warning'
 * | 'error'. Used to surface async failures that would otherwise be silent.
 */
window.showToast = function(message, level) {
    var bar = document.getElementById("status-bar");
    if (!bar) return;
    var div = document.createElement("div");
    div.className = "toast toast-" + (level || "info");
    if (level === "error" || level === "warning") div.setAttribute("role", "alert");
    var p = document.createElement("p");
    p.textContent = message;
    div.appendChild(p);
    bar.appendChild(div);
    var duration = (level === "error" || level === "warning") ? 6000 : 3500;
    setTimeout(function() { div.style.opacity = "0"; }, duration);
    setTimeout(function() { div.remove(); }, duration + 500);
};

/**
 * Copy text to the clipboard with a uniform highlight + toast. `el` (optional)
 * gets the same transient `.tree-copied` highlight the JSON-tree key-copy uses;
 * `message` (optional) overrides the default "Copied: …" toast. Shared by the
 * dataset cell-copy, table-copy, and tree-value-copy affordances.
 */
window.copyWithFeedback = function(text, el, message) {
    if (text == null) return Promise.resolve(false);
    text = String(text);
    if (!navigator.clipboard) {
        window.showToast("Clipboard unavailable in this browser.", "warning");
        return Promise.resolve(false);
    }
    return navigator.clipboard.writeText(text).then(function() {
        if (el) {
            el.classList.add("tree-copied");
            setTimeout(function() { el.classList.remove("tree-copied"); }, 800);
        }
        var shown = message || ("Copied: " + (text.length > 80 ? text.slice(0, 80) + "…" : text));
        window.showToast(shown, "success");
        return true;
    }).catch(function() {
        window.showToast("Copy failed.", "error");
        return false;
    });
};

/**
 * Delegated click-to-copy for dataset Property/Parameter tables. One listener
 * for the whole page — the prop-tables are injected via HTMX swaps, so a
 * delegated handler survives re-renders without re-binding. Copies the cell's
 * `data-copy` (full-precision raw value, set on rounded float cells) when
 * present, else its visible text. Bails on real interactive children so links,
 * edit inputs, and the inline JSON sub-trees keep their own behavior.
 */
document.addEventListener("click", function(e) {
    var cell = e.target.closest(".prop-table td.col-val, .prop-table td.col-prop code");
    if (!cell) return;
    if (e.target.closest("a, button, input, textarea, select, .ds-inline-tree, .json-tree")) return;
    var text = cell.getAttribute("data-copy");
    if (text == null) text = (cell.textContent || "").trim();
    if (!text) return;
    window.copyWithFeedback(text, cell);
});

/**
 * Copy an entire Property/Parameters table as tab-separated `key\tvalue` rows
 * (one per line) — pastes into Excel / Google Sheets as two clean columns.
 * Called from a small "Copy" button in each section header.
 */
window.copyPropTable = function(btn, fmt) {
    var details = btn.closest("details");
    var table = details ? details.querySelector(".prop-table") : null;
    if (!table) { window.showToast("Nothing to copy here.", "warning"); return; }
    var rows = table.querySelectorAll("tbody > tr");
    var lines = [];
    for (var i = 0; i < rows.length; i++) {
        var keyEl = rows[i].querySelector(".col-prop");
        var valEl = rows[i].querySelector(".col-val");
        if (!keyEl || !valEl) continue;
        var key = (keyEl.textContent || "").trim();
        var val = valEl.getAttribute("data-copy");
        if (val == null) val = (valEl.textContent || "").trim().replace(/\s+/g, " ");
        if (fmt === "md") {
            lines.push("| " + key + " | " + val + " |");
        } else {
            lines.push(key + "\t" + val);
        }
    }
    if (!lines.length) { window.showToast("Nothing to copy here.", "warning"); return; }
    window.copyWithFeedback(lines.join("\n"), btn,
        "Copied " + lines.length + " row" + (lines.length === 1 ? "" : "s") + " to the clipboard.");
};

var _reviewDismissTimer = null;
function _clearReviewDismiss() {
    if (_reviewDismissTimer) { clearTimeout(_reviewDismissTimer); _reviewDismissTimer = null; }
}

/* Open the live-chip-vs-working-copy review overlay.
 * opts.autoDismiss (ms) — auto-close after N ms; cancelled by user interaction
 * (hover, pointer-down, focus-within). Used by the workbench auto-open path so
 * the overlay doesn't block the screen when Qualibrate fires a burst of writes;
 * manual opens (pending-tray click, Review & sync button) pass no opts. */
window.openReview = function(opts) {
    var overlay = document.getElementById("state-review-overlay");
    var host = document.getElementById("state-review-host");
    if (!overlay || !host) return;
    _clearReviewDismiss();
    host.innerHTML = '<p class="muted" style="padding:1.5rem">Reading the live state…</p>';
    overlay.style.display = "flex";
    overlay._releaseTrap = window.trapFocus(overlay, window.closeReview);
    fetch("/state/review")
        .then(function(r) { return r.text(); })
        .then(function(html) {
            host.innerHTML = html;
            if (window.htmx) htmx.process(host);
        })
        .catch(function() {
            host.innerHTML = '<p class="muted" style="padding:1.5rem">Could not read the live state.</p>';
            window.showToast("Could not read the live chip state (network error).", "error");
        });
    // Auto-dismiss: start a timer that closes the overlay unless the user
    // interacts (hover / pointer / focus cancels it permanently).
    var ms = opts && opts.autoDismiss;
    if (ms && ms > 0) {
        _reviewDismissTimer = setTimeout(function () { window.closeReview(); }, ms);
        var cancel = function () {
            _clearReviewDismiss();
            overlay.removeEventListener("pointerdown", cancel);
            overlay.removeEventListener("pointerenter", cancel);
            overlay.removeEventListener("focusin", cancel);
        };
        overlay.addEventListener("pointerdown", cancel);
        overlay.addEventListener("pointerenter", cancel);
        overlay.addEventListener("focusin", cancel);
    }
};

window.closeReview = function() {
    _clearReviewDismiss();
    var overlay = document.getElementById("state-review-overlay");
    if (overlay) {
        overlay.style.display = "none";
        if (overlay._releaseTrap) { overlay._releaseTrap(); overlay._releaseTrap = null; }
    }
};

/* Pull the live state into the working copy. `mode` decides what happens to the
 * user's pending edits: 'apply' (replay them on top, then push the merged result
 * straight to the live chip), 'reapply' (replay them on top, best-effort, left
 * pending for review), or 'discard' (drop them). Soft-refreshes the tray + any
 * visible live-state surface — never reloads the page. */
/* Compact "Affected: a, b, +N more" suffix for a replay's failed-edit list, so
   the user sees WHICH edits couldn't be re-applied (not just a count) — audit D6.
   The dropped values are recoverable from Param History if needed. */
function _failedPathsSummary(failed) {
    var paths = (failed || []).map(function(f){ return f && f.dot_path; })
                              .filter(Boolean);
    if (!paths.length) return "";
    var shown = paths.slice(0, 5).join(", ");
    if (paths.length > 5) shown += ", +" + (paths.length - 5) + " more";
    return " Affected: " + shown + ".";
}

window.doStateSync = function(mode, forced, ackUnseen) {
    mode = mode || "discard";
    // Double-submit guard: a second click (or a grid ⚡ + tray button double-fire)
    // while one apply/sync is in flight used to queue a second /state/sync that
    // races the first's store.reload() — the "clicked twice, stuttered" report.
    if (window._applyInFlight) return;
    window._applyInFlight = true;
    // Close the review overlay NOW (not after the response): its 45%-black
    // backdrop otherwise dims the page for the whole server round-trip and then
    // vanishes — the reported "screen suddenly BRIGHTENS" flash. A conflict
    // response is handled by the conflict tray + toast, which never needed the
    // modal open.
    window.closeReview();
    // docs/120 item 22: declare what THIS screen is showing. Two SM windows
    // share one server-side change log, and a tray only refreshes on its own
    // actions — so an Apply pressed here can carry edits made in the other
    // window that were never on this one. Sending the count the user actually
    // saw is what lets the server tell the difference between "apply my three
    // edits" and "apply three edits plus one you have never seen".
    var _seen = (function () {
        var t = document.getElementById("pending-tray");
        var v = t && t.getAttribute("data-change-count");
        return (v === null || v === undefined || v === "") ? null : v;
    })();
    fetch("/state/sync", {
        method: "POST",
        headers: {"Content-Type": "application/x-www-form-urlencoded", "HX-Request": "true"},
        body: "mode=" + encodeURIComponent(mode) + (forced ? "&force=1" : "")
              + (ackUnseen ? "&ack_unseen=1" : "")
              + (_seen !== null ? "&seen_changes=" + encodeURIComponent(_seen) : "")
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.status === "unseen_changes") {
                // Never a dead end — name what would go, and let one click
                // accept it or send the user to review it first.
                var lines = (data.paths || []).slice(0, 6).join("\n  ");
                if (window.confirm((data.message || "") + "\n\n  " + lines
                        + "\n\nApply everything, including those?")) {
                    setTimeout(function () {
                        window._applyInFlight = false;
                        // `ackUnseen`, never `forced`: force=1 answers the
                        // STALENESS question and must not double as consent to
                        // another window's edits.
                        window.doStateSync(mode, false, true);
                    }, 0);
                } else {
                    // Refresh the tray so this screen stops lying, then show it.
                    if (window.htmx) {
                        window.htmx.ajax("GET", "/state/tray",
                                         {target: "#pending-tray", swap: "outerHTML"});
                    }
                    if (window.showToast) {
                        window.showToast("Nothing was applied — the tray now shows "
                                         + "every pending edit.", "info");
                    }
                }
                return;
            }
            if (data.status === "needs_confirm") {
                // docs/65: the working state holds staged/saved content that a
                // pull would destroy — the server refuses until confirmed. The
                // forced re-post runs on a MACROTASK so the finally below has
                // already cleared the in-flight guard.
                if (window.confirm((data.message || "Overwrite the working state?")
                                   + "\n\nContinue and discard it?")) {
                    setTimeout(function() { window.doStateSync(mode, true); }, 0);
                } else if (window.showToast) {
                    // r16 ⑥: a declined confirm used to end SILENTLY — the
                    // click looked accepted while nothing happened.
                    window.showToast("Cancelled — nothing was changed.", "info");
                }
                return;
            }
            if (data.status === "error") {
                window.showToast("Sync failed: " + (data.message || "unknown error"), "error");
                return;
            }
            // Swap whatever tray the server returned — the synced tray, or (on a
            // fresh conflict during a one-click apply) the conflict tray.
            // _bulkSelfEdit suppresses the /bulk quam:state-changed full re-GET:
            // after a CLEAN apply the grid already shows the correct values (this
            // swap used to trigger a full 21Q grid re-render + remount = the
            // reported blink/freeze), and for PULL modes _softRefreshLiveSurface
            // below does the one legitimate refresh (the re-GET here would race
            // it — two concurrent full swaps into #table-pane).
            if (data.tray_html) {
                window._bulkSelfEdit = true;
                try { _swapPendingTray(data.tray_html); }
                finally { window._bulkSelfEdit = false; }
            }
            // audit-r10 boundary: a completed sync (pull OR apply) resolves
            // every un-staged in-memory edit — reverting them afterwards
            // would cross the apply/pull boundary.
            if (window.LiveEditUndo) LiveEditUndo.clear();
            // A sync pull/apply replaces the working copy wholesale (store.reload()
            // bumps mutation_seq), so the linter must re-run — fire unconditionally,
            // never relying on a tray_html being present in the response.
            window._diagChanged();
            // A clean one-click apply (mode=apply, ok) pushed the user's OWN edits to the
            // live chip — the working copy now equals (live + those edits), so the surface
            // and any open inspector already show the correct, applied values. Re-fetching
            // the surface or blanking the inspector here would needlessly RESET the page and
            // throw away the user's open edit screen (the pulse-edit → "Apply to live"
            // regression). Only the PULL modes (discard / reapply) — and a fresh apply
            // conflict — change the working copy to DIFFERENT live values, where the
            // stale-screen refresh + inspector-close actually matter.
            var cleanApply = (data.status === "ok" && data.mode === "apply");
            // A "clean" apply still needs the ONE surface refresh when the screen
            // provably no longer matches the working copy: (a) the pull-replay
            // DROPPED some of the user's edits (the grid would keep showing the
            // dropped value as applied), or (b) the pull absorbed OTHER live
            // changes (an experiment wrote between edits — third-party values on
            // screen are stale). Otherwise skip it — the values shown are exactly
            // what was applied, and the blanket refresh was the blink/freeze.
            var replayFailed = !!(data.replay && data.replay.failed && data.replay.failed.length);
            if (!cleanApply || replayFailed || data.pulled_other_changes) {
                // Customer (2026-08-27): patch the changed leaves in place —
                // the page stays where it is; only a shape change still
                // refreshes wholesale (scroll kept).
                _patchOrRefreshLiveSurface(data);
            }
            // The user's own pull/apply just moved the baseline — re-poll drift NOW so
            // the "N parameters changed on the live chip" banner reconciles immediately
            // instead of showing the pre-apply count until the next 5s poll (feedback #5,
            // audit P0-5). Mirrors resetBaseline's immediate re-poll.
            if (window._pollDrift) window._pollDrift();
            // The pull consumed any out-of-band live change — drop the
            // "live files changed on disk" banner(s) wherever they render.
            document.querySelectorAll(".live-diverged-banner").forEach(function(b) {
                b.hidden = true;
            });
            // Refresh State History surfaces that listen for these triggers.
            // Plain sync (pull/reapply) used to NOT emit them — only apply-to-live
            // did — so the timeline and drift panel stayed stale (sync red-team audit).
            (document.body || document).dispatchEvent(
                new CustomEvent("liveDriftChanged", { bubbles: true }));
            (document.body || document).dispatchEvent(
                new CustomEvent("stateHistoryChanged", { bubbles: true }));
            // PULL modes changed the working copy — any open qubit/pair/pulse inspector
            // now shows pre-sync values, so close it. A clean apply did NOT (it pushed the
            // user's own edits), so KEEP the page + inspector open and instead let any
            // page-local gentle refresher (e.g. the Pulses rows, which listen for
            // pulses-changed and re-render in place without touching #inspector-pane)
            // clear its pending markers. The trigger is a no-op off those pages.
            if (!cleanApply) {
                if (window.closeInspector) window.closeInspector();
            } else if (window.htmx) {
                try { window.htmx.trigger(document.body, "pulses-changed"); } catch (e) {}
            }

            if (data.status === "conflict") {
                window.showToast(
                    "The live chip changed again while applying — choose how to resolve it.",
                    "warning");
                return;
            }

            var applied = (data.replay && data.replay.applied) || 0;
            var failed = (data.replay && data.replay.failed) || [];
            if (data.mode === "apply") {
                if (failed.length) {
                    window.showToast(
                        "Pulled the live state, applied " + applied + " edit" +
                        (applied === 1 ? "" : "s") + " to the live chip; " + failed.length +
                        " could not be re-applied and were dropped — the field changed or no " +
                        "longer exists on the new live chip." + _failedPathsSummary(failed) +
                        " Re-enter them if still needed.",
                        "warning");
                } else {
                    window.showToast(
                        "Pulled the live state, re-applied " + applied + " edit" +
                        (applied === 1 ? "" : "s") + ", and applied them to the live chip.",
                        "success");
                }
            } else if (data.mode === "reapply") {
                if (failed.length) {
                    window.showToast(
                        "Pulled the live state and re-applied " + applied + " edit" +
                        (applied === 1 ? "" : "s") + "; " + failed.length +
                        " could not be re-applied (the field changed or no longer exists)." +
                        _failedPathsSummary(failed),
                        "warning");
                } else {
                    window.showToast(
                        "Pulled the live state and re-applied " + applied + " edit" +
                        (applied === 1 ? "" : "s") + " — review them in the tray, then apply to the live chip.",
                        "success");
                }
            } else {
                window.showToast("Pulled the live state into the working state.", "success");
            }
        })
        .catch(function() { window.showToast("Sync failed (network error).", "error"); })
        .finally(function() { window._applyInFlight = false; });
};

/* The THIRD choice when the live chip drifted (docs/86): keep the working state
 * and overwrite the live chip with it.
 *
 * Until now the drift banner and the review modal offered only pull-or-close,
 * which is one direction, not a choice — and the case that needs the other
 * direction is common: a test run wrote parameters by mistake and the state SM
 * is holding is the good one. The capability already existed (State History →
 * restore-live; the conflict tray's force-overwrite); what was missing was
 * reaching it at the moment the user learns about the drift.
 *
 * Pull and push are NOT symmetric, so this is never the primary action and
 * never silent: the preflight names how many live values disappear, whether a
 * run is writing this chip right now, and that the push snapshots the live
 * state first so the tray's "Revert last apply" undoes it. ONE confirm — the
 * push is forced, because the user has just been told exactly what it forces
 * past (an unforced push would land on the staleness conflict screen and ask a
 * second time). */
window.overwriteLiveWithWorking = function () {
    if (window._applyInFlight) return;   // shared guard with doStateSync
    fetch("/state/overwrite-live/preflight", { headers: { "HX-Request": "true" } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (!d || !d.ok) {
                window.showToast((d && d.message) || "Cannot overwrite the live chip.", "error");
                return;
            }
            var n = d.live_changes;
            var lines = ["Overwrite the live chip with the working state?", ""];
            if (n === null || n === undefined) {
                lines.push("The live files could not be read, so what they hold right now is unknown.");
            } else if (n === 0) {
                lines.push("The live chip already matches the working state — nothing would change.");
            } else {
                lines.push(n + " value" + (n === 1 ? "" : "s") + " that differ on the live chip "
                    + "will be REPLACED by the working state's. Whatever wrote them "
                    + "(an experiment program, another window) loses those changes.");
            }
            if (d.unsaved) {
                lines.push("Your " + d.unsaved + " unsaved edit" + (d.unsaved === 1 ? "" : "s")
                    + " are saved and pushed along with it.");
            }
            if (d.run_active) {
                lines.push("", "⚠ A run is in progress"
                    + (d.run_label ? " (" + d.run_label + ")" : "")
                    + " — it may write these values again when its next node finishes.");
            }
            if (d.reversible) {
                lines.push("", "The current live state is snapshotted first, so the tray's "
                    + "“Revert last apply” undoes this.");
            }
            if (!window.confirm(lines.join("\n"))) {
                if (window.showToast) window.showToast("Cancelled — nothing was changed.", "info");
                return;
            }
            window.closeReview();
            if (!window.htmx) {
                window.showToast("Open the top-bar tray and use “Apply to live chip”.", "info");
                return;
            }
            window._applyInFlight = true;
            htmx.ajax("POST", "/state/apply-to-live?force=1",
                      { target: "#pending-tray", swap: "outerHTML" })
                .finally(function () { window._applyInFlight = false; });
        })
        .catch(function () {
            window.showToast("Could not check the live chip (network error).", "error");
        });
};

/* The grid ⚡ "Apply to live now" buttons push the user's edits all the way to the
 * live chip in ONE click (the grids call this after applyAll commits the edits).
 * Routing is working-copy-state aware so it can never silently drop a saved edit AND
 * the button always actually pushes (it never dead-ends), reading the tray's data-*
 * which reflect the just-committed state:
 *   - saved-but-unapplied edits exist (working_dirty) → a pull-merge would overwrite
 *     them, so push the FULL working state to live directly via /state/apply-to-live
 *     (which saves the pending edits first, then pushes working→live with NO pull —
 *     preserving the saved edits). The grid already confirmed the push in applyAll.
 *   - pending edits only (nothing saved yet) → doStateSync('apply') pull+re-apply+push
 *     merge in one shot (qualibrate's other live changes survive).
 *   - nothing pending (all edits already matched live) → a small "nothing to apply" toast
 *     so the click still gives feedback. */
window.applyEditsToLive = function () {
    if (window._applyInFlight) return;   // double-submit guard (shared with doStateSync)
    var tray = document.getElementById("pending-tray");
    var cc = tray ? parseInt(tray.getAttribute("data-change-count") || "0", 10) : 0;
    var dirty = !!(tray && tray.getAttribute("data-working-dirty") === "1");
    if (dirty) {
        if (window.htmx) {
            // Same direct push the tray's "Apply to live chip" button uses, but without
            // a second confirm (applyAll already confirmed). htmx.ajax handles the tray
            // swap + OOB status toast + HX-Trigger natively.
            window._applyInFlight = true;
            htmx.ajax("POST", "/state/apply-to-live", { source: "#pending-tray", target: "#pending-tray", swap: "outerHTML" })
                .finally(function () { window._applyInFlight = false; });
        } else if (window.showToast) {
            window.showToast("Open the top-bar tray and click “Apply to live chip” to push your saved edits to the live chip.", "info");
        }
        return;
    }
    if (cc > 0) {
        if (window.doStateSync) window.doStateSync("apply");
        return;
    }
    // r16 ⑥: the tray's data-* can be STALE (a missed OOB swap) — "nothing
    // to apply" was declared from them without ever asking the server, so a
    // real pending edit silently never reached the live chip. Re-fetch the
    // tray (server truth), re-route ONCE on fresh attributes, and only then
    // report the honest no-op.
    if (window.htmx && !window._applyRecheck) {
        window._applyRecheck = true;
        htmx.ajax("GET", "/state/tray", {
            target: "#pending-tray", swap: "outerHTML"
        }).then(function () {
            try {
                var t2 = document.getElementById("pending-tray");
                var cc2 = t2 ? parseInt(t2.getAttribute("data-change-count") || "0", 10) : 0;
                var dirty2 = !!(t2 && t2.getAttribute("data-working-dirty") === "1");
                if (dirty2 || cc2 > 0) { window.applyEditsToLive(); return; }
                if (window.showToast) {
                    window.showToast("Nothing to apply — your edits already match the live chip.", "info");
                }
            } finally { window._applyRecheck = false; }
        }, function () { window._applyRecheck = false; });
    } else if (window.showToast) {
        window.showToast("Nothing to apply — your edits already match the live chip.", "info");
    }
};

/* ------------------------------------------------------------------ */
/* Live-drift tracking — accumulating "Live changes since baseline"    */
/* ------------------------------------------------------------------ */

/* Accumulating comparison of the live chip against a baseline that survives
 * the working-copy auto-sync. A watch-only user (most users) runs qualibrate
 * fit after fit without touching SM; the working copy keeps auto-adopting the
 * new live, which used to silently absorb the diff. This polls /state/drift
 * (mtime-gated server-side, stat()-cheap) and dispatches liveDriftChanged
 * whenever the count moves, so the embedded panels stay live.
 *
 * There is deliberately NO main-screen banner any more (docs/58): the popping
 * "N parameters changed — Reset baseline" alarm interrupted everyone and was
 * almost never acted on. The same data now waits where a user comes looking —
 * the "Live changes since baseline" panels on Param History and State History
 * (count + baseline time + per-param table + Reset baseline) — plus the
 * openDrift() overlay for surfaces that link to it. */
(function () {
    var POLL_MS = ((window.UI_CONFIG && UI_CONFIG.driftPollInterval) || 0) * 1000;
    var _lastCount = null;        // last polled count (change detection → event dispatch)

    var _driftPolling = false;
    function poll() {
        // In-flight guard + visibility gating (audit B24): never overlap a slow
        // request, and don't poll while the window is hidden/backgrounded.
        if (_driftPolling || document.hidden) return;
        _driftPolling = true;
        fetch("/state/drift", { cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                // docs/132 — hist_seq rides this every-page poll: when the
                // chip's snapshot store moved (a capture in another window,
                // the background EXP ingest, a prune), announce it once. The
                // topbar Versions chip and the open panel both listen for
                // stateHistoryChanged; 0 means "unknown" and is never a
                // signal. Dispatched on document.body because the chip's
                // hx-trigger is `stateHistoryChanged from:body`.
                if (d && d.hist_seq && d.hist_seq !== window._histSeqSeen) {
                    var first = window._histSeqSeen === undefined;
                    window._histSeqSeen = d.hist_seq;
                    if (!first && document.body) {
                        try {
                            document.body.dispatchEvent(new CustomEvent(
                                'stateHistoryChanged', { bubbles: true }));
                        } catch (e) {}
                    }
                }
                // (docs/87) The "✓ Live chip updated — N params pulled" toast lived
                // here. It announced a silent auto-pull after the fact; the user-facing
                // path now ASKS first through the live-diverged banner, which carries
                // the same count and both directions, so there is nothing to report.
                // docs/120 item 8 — Auto-Sync's pull rides THIS poll rather
                // than adding one of its own. The server decides everything:
                // whether pull is armed, whether live actually diverged, and
                // whether unapplied edits block it. All the client does is
                // press the button when told, and share the in-flight latch
                // with the manual Apply + the auto-apply flusher so a pull can
                // never interleave with a push.
                if (d && d.auto_pull && !window._applyInFlight && window.htmx) {
                    window._applyInFlight = true;
                    var _relTimer = null;
                    var _rel = function () {
                        if (_relTimer) { clearTimeout(_relTimer); _relTimer = null; }
                        window._applyInFlight = false;
                        // This latch also gates the MANUAL Apply buttons and the
                        // auto-apply flusher, and the flusher parks work in its
                        // own `_queued` flag while it is held. Releasing without
                        // poking it left an edit unapplied until the next tray
                        // mutation, while the pill still claimed auto-push was on.
                        if (window.AutoApply && window.AutoApply.drain) {
                            try { window.AutoApply.drain(); } catch (e2) {}
                        }
                    };
                    // A never-settling request must not wedge Apply forever —
                    // docs/80 fixed exactly this class for the dataset poll and
                    // the pattern belongs here too. The server side is idempotent,
                    // so releasing early can at worst allow one redundant pull.
                    _relTimer = setTimeout(_rel, 20000);
                    try {
                        // The server cannot see typed-but-uncommitted grid cells
                        // (a fill-down or pasted column lives only in the DOM
                        // until Apply), so it would read the working copy as
                        // clean and pull straight over them. Report them.
                        //
                        // The class is `dirty`, set by `_markCellDirty` in BOTH
                        // bulk-edit.js and pair-edit.js. The first cut looked
                        // for `.bulk-dirty` — which exists only as the id of the
                        // COUNTER span (`#bulk-dirty-count`) — and fell back to
                        // `BulkEdit.hasUnsaved`, which does not exist. So this
                        // never once reported dirt, and a filled-down column was
                        // destroyed by a pull with "replace" UNCHECKED: exactly
                        // the row of the policy table that is supposed to ask.
                        // Nothing failed and no test saw it, because the pin
                        // posted `dom_dirty=1` by hand.
                        var _domDirty = !!document.querySelector('.bulk-cell.dirty');
                        var pp = window.htmx.ajax('POST',
                            '/auto-sync/pull' + (_domDirty ? '?dom_dirty=1' : ''), {
                            target: '#pending-tray', swap: 'outerHTML',
                        });
                        if (pp && typeof pp.then === 'function') pp.then(_rel, _rel);
                        else _rel();
                    } catch (e) { _rel(); }
                }
                var count = (d && d.ok && d.tracked) ? (d.count || 0) : 0;
                // Count changed → refresh any embedded panel / open overlay so
                // the State History page + a viewing user see it accumulate.
                if (_lastCount !== null && count !== _lastCount) {
                    (document.body || document).dispatchEvent(new CustomEvent("liveDriftChanged", { bubbles: true }));
                    if (window._driftOverlayOpen) _loadDriftView();
                }
                _lastCount = count;
                // docs/113 (#13): staleness indicators get a "when" — the
                // status badge's tooltip names the last successful check, so
                // "Synced" is a claim with a timestamp, not a vibe.
                try {
                    // audit: only a SUCCESSFUL poll may claim a check time —
                    // the failure path reaches here with d === null, and a
                    // staleness indicator that lies is worse than none.
                    if (!d) throw 0;
                    var badge = document.querySelector('.tray-indicator');
                    if (badge) {
                        var base = badge.getAttribute('data-tip-base');
                        if (base === null) {
                            base = badge.getAttribute('title') || '';
                            badge.setAttribute('data-tip-base', base);
                        }
                        var t = new Date();
                        var hh = ('0' + t.getHours()).slice(-2)
                            + ':' + ('0' + t.getMinutes()).slice(-2)
                            + ':' + ('0' + t.getSeconds()).slice(-2);
                        badge.setAttribute('title',
                            (base ? base + ' — ' : '') + 'last checked ' + hh);
                    }
                } catch (e) {}
            })
            .catch(function () {})
            .then(function () { _driftPolling = false; });
    }
    // Expose so an explicit action (reset) can force an immediate refresh.
    window._pollDrift = poll;
    // Re-poll the global drift banner whenever something fires liveDriftChanged on the
    // body — a server HX-Trigger from an apply/sync (the tray "apply to live" button only
    // swaps #pending-tray and emits this trigger), or the drift IIFE itself. poll() is
    // in-flight-guarded (_driftPolling), so its OWN dispatch is a no-op here (no loop):
    // the dispatch happens before _driftPolling resets, so re-entry returns early. A
    // server trigger (no poll in flight) re-polls immediately (audit P0-5/6).
    document.addEventListener("liveDriftChanged", function () { poll(); });
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden && POLL_MS > 0) poll();  // catch up on re-focus
    });

    if (POLL_MS > 0) {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", function () {
                poll(); setInterval(poll, POLL_MS);
            });
        } else {
            poll(); setInterval(poll, POLL_MS);
        }
    }

    function _loadDriftView() {
        var host = document.getElementById("live-drift-host");
        if (!host) return;
        fetch("/state/drift/view", { cache: "no-store" })
            .then(function (r) { return r.text(); })
            .then(function (html) {
                host.innerHTML = html;
                // Same one-liner the version-diff overlay needs (docs/128
                // review found this sibling carrying the identical defect):
                // no htmx swap event fires here, so _live_drift.html's
                // "baseline: <ts>" would render as an invisible blank.
                if (window.applyLocalTimes) window.applyLocalTimes(host);
                if (window.htmx) htmx.process(host);
            })
            .catch(function () {
                host.innerHTML = '<p class="muted" style="padding:1.5rem">Could not read the live chip.</p>';
            });
    }
    window._loadDriftView = _loadDriftView;
})();

window.openDrift = function () {
    var overlay = document.getElementById("live-drift-overlay");
    var host = document.getElementById("live-drift-host");
    if (!overlay || !host) return;
    host.innerHTML = '<p class="muted" style="padding:1.5rem">Reading the live chip…</p>';
    overlay.style.display = "flex";
    window._driftOverlayOpen = true;
    overlay._releaseTrap = window.trapFocus(overlay, window.closeDrift);
    window._loadDriftView();
};

window.closeDrift = function () {
    var overlay = document.getElementById("live-drift-overlay");
    if (overlay) {
        overlay.style.display = "none";
        if (overlay._releaseTrap) { overlay._releaseTrap(); overlay._releaseTrap = null; }
    }
    window._driftOverlayOpen = false;
};

/* Acknowledge all accumulated changes: set the baseline to the current live
 * chip and start counting fresh. */
window.resetBaseline = function () {
    if (!confirm("Reset the comparison baseline to the current live chip?\n\n" +
                 "The accumulated list of changes will be cleared and start fresh from now.")) {
        return;
    }
    fetch("/state/baseline/reset", { method: "POST", headers: { "HX-Request": "true" } })
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (!d || !d.ok) {
                window.showToast("Could not reset baseline: " + ((d && d.error) || "unknown error"), "error");
                return;
            }
            window.showToast("Baseline reset to the current live chip.", "success");
            // Refresh the State History embedded panel + any open overlay. Bubble from
            // body so the from:body listener on _state_history.html actually receives it
            // (a non-bubbling event on document never reaches a body listener — audit P0-4).
            (document.body || document).dispatchEvent(new CustomEvent("liveDriftChanged", { bubbles: true }));
            if (window._driftOverlayOpen) window._loadDriftView();
            if (window._pollDrift) window._pollDrift();
        })
        .catch(function () { window.showToast("Reset baseline failed (network error).", "error"); });
};

/* ------------------------------------------------------------------ */
/* Working-copy GC banner                                              */
/* ------------------------------------------------------------------ */

/* One-click cleanup of accumulated working copies. Server-side, only
 * provably-clean copies (working content == recorded sync point) and broken
 * leftovers are deleted; anything holding unapplied edits is kept, as are
 * the copies of currently-loaded chips. */
window.wcGcCleanup = function(btn) {
    if (btn) { btn.disabled = true; btn.textContent = "Cleaning…"; }
    fetch("/api/working-copies/gc", {method: "POST"})
        .then(function(r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
        })
        .then(function(data) {
            var banner = document.getElementById("wc-gc-banner");
            if (banner) banner.hidden = true;
            // Mark dismissed for this tab-session either way — when nothing
            // could be cleaned (all remaining copies hold possible edits),
            // re-showing the banner on the next page would just loop the
            // user through a no-op "Clean up" forever.
            try { sessionStorage.setItem("quam_wc_gc_dismissed", "1"); } catch (e) {}
            var deleted = data.deleted || 0;
            var kept = data.by_status || {};
            var keptDirty = (kept.dirty || 0) + (kept.unverifiable || 0);
            if (deleted === 0) {
                window.showToast(
                    "Nothing to clean: the remaining working states hold (possible) " +
                    "unapplied edits" + (keptDirty ? " (" + keptDirty + ")" : "") +
                    " or belong to loaded chips.",
                    "warning");
            } else {
                window.showToast(
                    "Removed " + deleted + " clean working " +
                    (deleted === 1 ? "state" : "states") +
                    (keptDirty ? " — kept " + keptDirty + " with (possible) unapplied edits." : "."),
                    "success");
            }
        })
        .catch(function() {
            if (btn) { btn.disabled = false; btn.textContent = "Clean up"; }
            window.showToast("Working-state cleanup failed.", "error");
        });
};

window.wcGcDismiss = function() {
    try { sessionStorage.setItem("quam_wc_gc_dismissed", "1"); } catch (e) {}
    var banner = document.getElementById("wc-gc-banner");
    if (banner) banner.hidden = true;
};

/* Re-render only a visible live-state surface after a sync, so we never reload
 * the whole page. The explorer tree (#table-pane) is the one always-safe,
 * self-contained surface; on pages that show no live state (e.g. a dataset
 * detail view) this is a no-op and the tray swap + toast is enough. */
/* Customer (2026-08-27, critical): a sync pull used to re-fetch the WHOLE
   page into #table-pane — on the 20Q chip that is an 8.8 MB /bulk render, a
   multi-second freeze, and the grid coming back at its first-click view
   (scroll, focus, column state gone). The user wants the opposite: the
   screen stays exactly where it is and only the VALUES change. So the sync
   response now names every leaf the pull changed (and whether the SHAPE
   changed), and this patches those leaves in place on whatever state
   surface is open; only a structural change (keys added/removed, or an
   uncapped diff) still falls back to the wholesale refresh — with the
   scroll position carried across it. */
window.LiveSurfacePatch = (function () {
    function _esc(s) { return (window.CSS && CSS.escape) ? CSS.escape(s) : s; }
    function _kindClass(v) {
        if (v === null || v === undefined) return "null";
        if (typeof v === "string") return v.charAt(0) === "#" ? "pointer" : "string";
        if (typeof v === "boolean") return "boolean";
        if (typeof v === "number") return "number";
        return "object";
    }
    // A collapsed tree subtree renders LATER from node._lazyData.value — the
    // snapshot taken at render time. Patch that snapshot too, or expanding
    // it after a pull would show the pre-pull value.
    function _patchLazy(dp, value) {
        var segs = dp.split(".");
        for (var i = segs.length - 1; i >= 1; i--) {
            var anc = document.querySelector('.tree-node[data-path="' + _esc(segs.slice(0, i).join(".")) + '"]');
            if (!anc) continue;
            if (!anc._lazyData || anc._lazyData.value == null || typeof anc._lazyData.value !== "object") return false;
            var cur = anc._lazyData.value;
            for (var j = i; j < segs.length - 1; j++) {
                var k = Array.isArray(cur) ? parseInt(segs[j], 10) : segs[j];
                if (cur == null || typeof cur !== "object" || !(k in cur)) return false;
                cur = cur[k];
            }
            if (cur == null || typeof cur !== "object") return false;
            var last = Array.isArray(cur) ? parseInt(segs[segs.length - 1], 10) : segs[segs.length - 1];
            if (!(last in cur)) return false;
            cur[last] = value;
            return true;
        }
        return false;
    }
    function _patchTree(e) {
        var node = document.querySelector('.tree-node[data-path="' + _esc(e.dot_path) + '"]');
        var n = 0;
        if (node) {
            var valEl = node.querySelector(".tree-val");
            if (valEl) {
                var fmt = window._treeFormatValue;
                var shown = fmt ? fmt(e.value) : String(e.value);
                valEl.textContent = shown;
                valEl.dataset.editVal = (typeof e.value === "string") ? e.value : shown;
                valEl.className = valEl.className.replace(/tree-val-(string|number|boolean|null|pointer|object)/g, "").trim();
                valEl.classList.add("tree-val-" + _kindClass(e.value));
                n++;
            }
        } else if (_patchLazy(e.dot_path, e.value)) n++;
        return n;
    }
    function _patchInputs(e) {
        var n = 0;
        document.querySelectorAll('.av-input[data-dot-path="' + _esc(e.dot_path) + '"]').forEach(function (inp) {
            inp.value = e.old_value_disp != null ? String(e.old_value_disp) : String(e.old_value_str || "");
            if (inp.hasAttribute("data-orig")) inp.setAttribute("data-orig", inp.value);
            inp.classList.remove("dirty");
            n++;
        });
        document.querySelectorAll('input[type="hidden"][name="dot_path"][value="' + _esc(e.dot_path) + '"]').forEach(function (h) {
            var form = h.closest("form"), input = form && form.querySelector('input[name="value"]');
            if (input) { input.value = e.old_value_str != null ? String(e.old_value_str) : ""; n++; }
        });
        return n;
    }
    function apply(changes) {
        var res = { patched: 0, tree: 0, inputs: 0 };
        if (!changes || !changes.length) return res;
        var grids = [window.BulkEdit, window.BulkPairEdit];
        grids.forEach(function (g) {
            if (g && typeof g.revertPaths === "function") {
                try { var r = g.revertPaths(changes); res.patched += (r && r.patched) || 0; } catch (err) { console.error("grid patch failed", err); }
            }
        });
        var hasTree = !!document.getElementById("explorer-tree-state") || !!document.querySelector(".tree-node[data-path]");
        changes.forEach(function (e) {
            if (!e || !e.dot_path) return;
            if (hasTree) res.tree += _patchTree(e);
            res.inputs += _patchInputs(e);
        });
        return res;
    }
    return { apply: apply, _patchLazy: _patchLazy };
})();

/* Carry #table-pane's scroll across a wholesale refresh (the structural
   fallback and stage/restore) so even that does not throw the user back to
   the top. One-shot: the first #table-pane swap after the call. */
function _keepPaneScroll() {
    var pane = document.getElementById("table-pane");
    if (!pane) return;
    var top = pane.scrollTop, left = pane.scrollLeft;
    if (!top && !left) return;
    var once = function (evt) {
        if (!evt.detail || !evt.detail.target || evt.detail.target.id !== "table-pane") return;
        document.removeEventListener("htmx:afterSwap", once);
        requestAnimationFrame(function () { pane.scrollTop = top; pane.scrollLeft = left; });
    };
    document.addEventListener("htmx:afterSwap", once);
    setTimeout(function () { document.removeEventListener("htmx:afterSwap", once); }, 15000);
}

/* Sync response → in-place patch when the shape is unchanged, wholesale
   refresh (scroll kept) when it is not. */
function _patchOrRefreshLiveSurface(data) {
    if (data && data.changes && !data.structural) {
        window.LiveSurfacePatch.apply(data.changes);
        return "patched";
    }
    _keepPaneScroll();
    _softRefreshLiveSurface();
    return "refreshed";
}
window._patchOrRefreshLiveSurface = _patchOrRefreshLiveSurface;

function _softRefreshLiveSurface() {
    if (!window.htmx) return;
    if (document.getElementById("explorer-tree-state")) {
        window.htmx.ajax("GET", "/explorer", {target: "#table-pane", swap: "innerHTML"});
        return;
    }
    // Any other state-rendering page: re-fetch the CURRENT page into its
    // pane — after a pull swapped in a different chip, leaving the old
    // chip's table on screen under a success toast would be a silent lie.
    // Limited to pages that actually render chip state (all are HTMX-
    // partial-capable); dataset-style pages keep their scroll/virtual-list
    // state instead of being needlessly re-fetched.
    var STATE_PAGES = ["/qubits", "/pairs", "/resonators", "/flux", "/couplers",
                       "/table", "/bulk", "/wiring",
                       "/instrument", "/topology", "/workbench",
                       "/pulses", "/config", "/scheduler"];
    var path = location.pathname;
    var isStatePage = STATE_PAGES.some(function(p) {
        return path === p || path.indexOf(p + "/") === 0;
    });
    var pane = document.getElementById("table-pane");
    if (pane && isStatePage) {
        window.htmx.ajax("GET", location.pathname + location.search,
                         {target: "#table-pane", swap: "innerHTML"});
    }
}
window._softRefreshLiveSurface = _softRefreshLiveSurface;

/* ------------------------------------------------------------------ */
/* History panel (Chip Status page)                                    */
/* ------------------------------------------------------------------ */

window.toggleHistoryPanel = function() {
    var panel = document.getElementById("history-panel");
    if (!panel) return;
    var open = panel.classList.toggle("history-panel-open");
    try { localStorage.setItem("quam_history_panel_open", open ? "1" : "0"); } catch(e) {}
    // Trigger HTMX to load history content on first open
    if (open) {
        var content = document.getElementById("history-content");
        if (content && !content.dataset.loaded) {
            content.dataset.loaded = "1";
            if (window.htmx) htmx.ajax("GET", "/api/history", {target: "#history-content", swap: "innerHTML"});
        }
    }
};

window._restoreHistoryPanelState = function() {
    var panel = document.getElementById("history-panel");
    if (!panel) return;
    var open = false;
    try { open = localStorage.getItem("quam_history_panel_open") === "1"; } catch(e) {}
    panel.classList.toggle("history-panel-open", open);
    if (open) {
        var content = document.getElementById("history-content");
        if (content && !content.dataset.loaded) {
            content.dataset.loaded = "1";
            if (window.htmx) htmx.ajax("GET", "/api/history", {target: "#history-content", swap: "innerHTML"});
        }
    }
};

window.selectHistoryEntry = function(checkbox) {
    var checked = document.querySelectorAll(".history-compare-cb:checked");
    if (checked.length > 2) {
        checkbox.checked = false;
        if (window.showToast) window.showToast("Pick exactly two snapshots to compare.", "info");
        return;
    }
    var btn = document.getElementById("history-compare-btn");
    if (btn) btn.disabled = (checked.length !== 2);
};

window.compareSelectedSnapshots = function() {
    var checked = document.querySelectorAll(".history-compare-cb:checked");
    if (checked.length !== 2) return;
    // docs/84: "Compare selected" used to land on three different surfaces
    // depending on which page you started from. It now always opens the diff
    // workbench, which resolves the chip dir server-side.
    _openDiffForSnapshots(checked[0].value, checked[1].value);
};

/* Two snapshot timestamps -> the diff workbench. HX-Redirect when htmx is
   present (the response navigates), a plain location change otherwise. */
function _openDiffForSnapshots(tsA, tsB) {
    var url = "/diff/snapshots?ts_a=" + encodeURIComponent(tsA)
            + "&ts_b=" + encodeURIComponent(tsB);
    if (window.htmx) { htmx.ajax("GET", url, {target: "body", swap: "none"}); }
    else { window.location.href = url; }
}

/* State History page: pick exactly two snapshots and diff them. Reuses the
   existing /api/history/compare endpoint; renders into the State History
   detail area. Idempotent init (the partial calls it on swap). */
window.StateHistory = (function () {
    'use strict';
    function selected() {
        return Array.prototype.slice.call(
            document.querySelectorAll('.sh-cb:checked'));
    }
    function toggleSelect(cb) {
        var sel = selected();
        if (sel.length > 2) {
            cb.checked = false; sel = selected();
            if (window.showToast) window.showToast("Pick exactly two snapshots to compare.", "info");
        }
        var btn = document.getElementById('sh-compare-btn');
        if (btn) btn.disabled = sel.length !== 2;
    }
    function compareSelected() {
        var sel = selected();
        if (sel.length !== 2) return;
        // docs/84: same destination as every other "Compare selected".
        _openDiffForSnapshots(sel[0].value, sel[1].value);
    }
    function init() {
        var btn = document.getElementById('sh-compare-btn');
        if (btn) btn.disabled = selected().length !== 2;
    }
    return { toggleSelect: toggleSelect, compareSelected: compareSelected, init: init };
})();

document.addEventListener("cellDiscarded", function(evt) {
    var d = evt.detail || {};
    _revertCell(d.dot_path, d.old_value_str != null ? d.old_value_str : "");
});

// Ctrl+Z undo: the server reverts one user action (a batch/rename undoes as a
// unit) and fires cellsReverted with every affected path so the visible cells +
// Explorer nodes roll back in place. Reuses the same _revertCell path as discard.
/* docs/122 item 3: the grids used to answer EVERY undo with a full /bulk
   re-GET — 2,418 ms on the real 20-qubit chip against 55 ms for the undo
   itself, and the same press on a page with no grid settles in 56 ms. But the
   response already names each reverted path and the value it reverted to, so
   the grids can repaint exactly those cells.

   The refetch is not simply deleted; it is kept for the two cases the patch
   provably cannot express:
     - a CREATE or DELETE was undone. A restored subtree can add columns and an
       undone creation turns a cell back into "not set" (data-missing) — neither
       is a value edit, and the response's own created/deleted flags say so.
     - some named path had no cell to land in even after cold-column hydration.
       Then we cannot claim the grid is up to date for it, so we don't.
   Debounced, because a burst of presses should cost ONE rebuild, not N. */
var _gridResyncTimer = null;
function _scheduleGridResync(ms) {
    if (_gridResyncTimer) clearTimeout(_gridResyncTimer);
    _gridResyncTimer = setTimeout(function () {
        _gridResyncTimer = null;
        document.dispatchEvent(new CustomEvent("quam:state-changed"));
    }, ms == null ? 900 : ms);
}
window._scheduleGridResync = _scheduleGridResync;

document.addEventListener("cellsReverted", function(evt) {
    var d = evt.detail || {};
    var entries = d.entries || [];
    entries.forEach(function(e) {
        _revertCell(e.dot_path, e.old_value_str != null ? e.old_value_str : "");
    });
    // The Live-State-Edit grids render their own cells (not inspector inputs),
    // so _revertCell can't reach them — repaint by path, then decide.
    var structural = entries.some(function (e) { return e && (e.created || e.deleted); });
    var gridOnScreen = !!(document.getElementById('bulk-table')
                          || document.getElementById('bulk-pair-table'));
    var uncovered = 0;
    try {
        // Night session 2026-08-28: only a cell a grid FOUND but could not
        // repaint honestly (kind/decoration mismatch) needs the whole-grid
        // re-GET. A path with no cell on either grid is not stale here -- it is
        // simply not a column (a pulse leaf undone from the inspector, a tree
        // edit) -- and it used to count as uncovered, i.e. the 2.4 s rebuild on
        // exactly the Ctrl+Z presses that touched nothing on the grid.
        [window.BulkEdit, window.BulkPairEdit].forEach(function (api) {
            if (!api || !api.revertPaths) return;
            var res = api.revertPaths(entries) || {};
            uncovered += (res.uncovered || []).length;
        });
    } catch (err) { uncovered = entries.length; }   // never trust a half repaint
    if (gridOnScreen && (structural || uncovered > 0)) _scheduleGridResync();
    if (d.message && window.showToast) window.showToast(d.message, "success");
    // r16 ⓪-2 (docs/73): flash the reverted item in place, or navigate to
    // its owning surface with the current page's typing stashed + restored.
    if (window.UndoNav) window.UndoNav.handle(d.entries || []);
});

/* ------------------------------------------------------------------ */
/* Stored-as-text auto-correction (docs/77)                             */
/* ------------------------------------------------------------------ */
/* SM detects numbers written as text ("0.13") and used to leave the repair
   to the user: visit the field, retype the value, confirm the type offer.
   This is the one-click path — SM shows exactly what it proposes (each
   field, what it holds now, what it would hold, which type), the user
   confirms once, and the whole repair lands as ONE change group (one
   Ctrl+Z) in the working copy. Nothing reaches the live chip until the
   usual Save / Apply. */
(function () {
    var overlay = null;

    function ensure() {
        if (overlay) return overlay;
        overlay = document.createElement("div");
        overlay.className = "ch-overlay tfx-overlay";
        overlay.style.display = "none";
        var backdrop = document.createElement("div");
        backdrop.className = "ch-backdrop";
        backdrop.addEventListener("click", window.closeTypeFixPlan);
        var card = document.createElement("div");
        card.className = "ch-card tfx-host";
        overlay.appendChild(backdrop);
        overlay.appendChild(card);
        document.body.appendChild(overlay);
        return overlay;
    }

    window.closeTypeFixPlan = function () {
        if (!overlay) return;
        if (overlay._releaseTrap) {
            try { overlay._releaseTrap(); } catch (e) {}
            overlay._releaseTrap = null;
        }
        overlay.style.display = "none";
        overlay.querySelector(".tfx-host").innerHTML = "";
    };

    /* Fetch the plan and show it. The plan is built server-side and carries
       its own signature, so what the user confirms is what the server
       re-validates before writing.

       The optional url lets the self-raising alert (docs/78) render the SAME
       dialog from /type-alert — one dialog, one apply path, so "auto-correct"
       stays one click without ever writing something the user did not see. */
    window.openTypeFixPlan = function (url) {
        var o = ensure();
        var host = o.querySelector(".tfx-host");
        host.innerHTML = '<div class="tfx-card"><p class="tfx-lead">Checking the chip…</p></div>';
        o.style.display = "flex";
        fetch(url || "/type-fix/plan", { headers: { "HX-Request": "true" } })
            .then(function (r) { return r.text(); })
            .then(function (html) {
                host.innerHTML = html;
                window.typeFixCount();
                o._releaseTrap = window.trapFocus
                    ? window.trapFocus(o, window.closeTypeFixPlan) : null;
                var btn = host.querySelector("#tfx-apply");
                if (btn) { try { btn.focus(); } catch (e) {} }
            })
            .catch(function (e) {
                host.innerHTML = '<div class="tfx-card"><p class="tfx-lead">'
                    + "Could not build the fix list: " + String(e) + "</p></div>";
            });
    };

    /* Scope every selection query to the ONE open dialog — the alert and the
       manual entry point render the same card, and a document-wide query
       would miscount the moment anything else on the page grew a .tfx-pick. */
    function _picks(node) {
        var card = (node && node.closest && node.closest(".tfx-card"))
            || (overlay && overlay.querySelector(".tfx-card"));
        return (card || document).querySelectorAll(".tfx-pick");
    }

    /* Mount an ALREADY-FETCHED plan card (the self-raising alert has the HTML
       in hand — re-fetching would race the one-shot server flag). */
    window.openTypeFixPlanHtml = function (html) {
        var o = ensure();
        var host = o.querySelector(".tfx-host");
        host.innerHTML = html;
        o.style.display = "flex";
        window.typeFixCount();
        o._releaseTrap = window.trapFocus
            ? window.trapFocus(o, window.closeTypeFixPlan) : null;
        var btn = host.querySelector("#tfx-apply");
        if (btn) { try { btn.focus(); } catch (e) {} }
        return o;
    };

    window.typeFixToggleAll = function (box) {
        var picks = _picks(box);
        Array.prototype.forEach.call(picks, function (p) { p.checked = box.checked; });
        window.typeFixCount();
    };

    window.typeFixCount = function () {
        var picks = _picks(null);
        var n = 0;
        Array.prototype.forEach.call(picks, function (p) { if (p.checked) n++; });
        var out = document.getElementById("tfx-count");
        if (out) out.textContent = n;
        var btn = document.getElementById("tfx-apply");
        if (btn) btn.disabled = (n === 0);
        var all = document.getElementById("tfx-all");
        if (all) all.checked = (n === picks.length && n > 0);
        return n;
    };

    window.typeFixApply = function (btn) {
        var card = btn.closest(".tfx-card");
        var errBox = card.querySelector(".tfx-error");
        var paths = [];
        Array.prototype.forEach.call(card.querySelectorAll(".tfx-pick"), function (p) {
            if (p.checked) paths.push(p.getAttribute("data-path"));
        });
        if (!paths.length) return;
        btn.disabled = true;
        var label = btn.textContent;
        btn.textContent = "Converting…";
        fetch("/type-fix/apply", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ paths: paths, sig: card.getAttribute("data-sig") })
        }).then(function (r) {
            return r.json().then(function (d) { return { status: r.status, body: d }; });
        }).then(function (res) {
            var d = res.body || {};
            if (!d.ok) {
                btn.disabled = false; btn.textContent = label;
                if (errBox) {
                    errBox.hidden = false;
                    errBox.textContent = (d.error || "The repair did not run.")
                        + (d.note ? "  " + d.note : "");
                }
                return;
            }
            if (d.tray_html && window._swapPendingTray) window._swapPendingTray(d.tray_html);
            window.closeTypeFixPlan();
            if (window.showToast) {
                window.showToast(d.count + " value" + (d.count === 1 ? "" : "s")
                    + " now stored as numbers — review in the tray, then Save / Apply.",
                    "success");
            }
            // the anomaly set shrank: refresh the badge, banner and tree marks
            try { window.htmx && window.htmx.trigger(document.body, "diagnostics-changed"); } catch (e) {}
            document.dispatchEvent(new CustomEvent("quam:state-changed"));
        }).catch(function (e) {
            btn.disabled = false; btn.textContent = label;
            if (errBox) { errBox.hidden = false; errBox.textContent = String(e); }
        });
    };
})();

/* The type-anomaly alert that raises ITSELF (docs/78).

   Detection was already automatic, but the repair sat behind a button the
   user had to go and find. Now, whenever NEW CONTENT enters the working copy
   — a chip is opened, the live state is pulled, a snapshot is staged, a run's
   state is loaded, or SM adopts an experiment's write — the server arms a
   one-shot flag and this asks for it. /type-alert answers 204 unless the
   anomaly set is genuinely new, so ordinary editing never prompts.

   The dialog it opens is the docs/77 repair dialog: the proposal is on screen,
   so auto-correct is one click AND nothing is written unseen. */
window.TypeAlert = (function () {
    var inflight = false, retryTimer = null;

    /* Never interrupt: not while typing, not mid-drag, not over another
       modal, not in a background tab. The server flag is only consumed by a
       200, so deferring costs nothing — the alert waits for a calm moment. */
    function _busy() {
        if (document.hidden) return true;
        var a = document.activeElement;
        if (a && a.matches && a.matches('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) {
            return true;
        }
        if (document.body && document.body.classList.contains("dragging")) return true;
        if (document.querySelector(".drag-ghost")) return true;
        var modals = document.querySelectorAll(".ch-overlay, #plot-apply-popup, #new-run-popup");
        for (var i = 0; i < modals.length; i++) {
            if (modals[i].style.display && modals[i].style.display !== "none") return true;
        }
        return false;
    }

    function check() {
        if (inflight) return;
        if (_busy()) {
            if (retryTimer) return;
            retryTimer = setTimeout(function () { retryTimer = null; check(); }, 3000);
            return;
        }
        inflight = true;
        fetch("/type-alert", { headers: { "HX-Request": "true" } })
            // docs/120 item 21: the 204 branch returned without reading the
            // body, so Chrome cancelled the response stream and logged
            // `net::ERR_ABORTED` — on EVERY page, since 204 is the normal
            // answer. Console noise is not cosmetic here: it is the channel a
            // real uncaught error has to be noticed in, and this drowned it.
            // Draining the (empty) body costs nothing and keeps it quiet.
            .then(function (r) {
                var st = r.status;
                return r.text().then(function (t) { return st === 200 ? t : null; });
            })
            .then(function (html) {
                inflight = false;
                if (!html) return;                 // 204: nothing new to say
                window.openTypeFixPlanHtml(html);
            })
            .catch(function () { inflight = false; });
    }

    function _card() { return document.querySelector(".tfx-overlay .tfx-card"); }

    return {
        check: check,
        /* "I'll fix them myself" — close and land on the first offending field. */
        manual: function (path) {
            window.closeTypeFixPlan();
            if (path && window._navigateToExplorerPath) window._navigateToExplorerPath(path);
        },
        diagnostics: function () {
            window.closeTypeFixPlan();
            try {
                window.htmx.ajax("GET", "/diagnostics",
                                 { target: "#table-pane", swap: "innerHTML" });
                history.pushState({}, "", "/diagnostics");
            } catch (e) {}
        },
        /* Explicit "don't show this again" — Esc / backdrop / Cancel do NOT
           memo, so closing the dialog never silently loses the finding. */
        dismiss: function (btn) {
            var card = (btn && btn.closest && btn.closest(".tfx-card")) || _card();
            if (!card) { window.closeTypeFixPlan(); return; }
            var body = new URLSearchParams({
                sig: card.getAttribute("data-alert-sig") || "",
                env_sig: card.getAttribute("data-alert-env-sig") || "",
                token: card.getAttribute("data-alert-token") || ""
            });
            fetch("/type-alarm/dismiss", {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: body.toString()
            }).catch(function () {});
            window.closeTypeFixPlan();
            try { window.htmx && window.htmx.trigger(document.body, "diagnostics-changed"); } catch (e) {}
        }
    };
})();

/* Environment schema changes + the verdicts the user records about them
   (docs/79). Reuses the same ch-overlay shell as the repair dialog: this is
   the other half of the same question ("is this value wrong, or did the
   library move?"), so it should not look like a different app. */
(function () {
    var overlay = null;

    function ensure() {
        if (overlay) return overlay;
        overlay = document.createElement("div");
        overlay.className = "ch-overlay tfx-overlay envchg-overlay";
        overlay.style.display = "none";
        var backdrop = document.createElement("div");
        backdrop.className = "ch-backdrop";
        backdrop.addEventListener("click", window.closeEnvSchemaChanges);
        var card = document.createElement("div");
        card.className = "ch-card tfx-host";
        overlay.appendChild(backdrop);
        overlay.appendChild(card);
        document.body.appendChild(overlay);
        return overlay;
    }

    window.closeEnvSchemaChanges = function () {
        if (!overlay) return;
        if (overlay._releaseTrap) {
            try { overlay._releaseTrap(); } catch (e) {}
            overlay._releaseTrap = null;
        }
        overlay.style.display = "none";
        overlay.querySelector(".tfx-host").innerHTML = "";
    };

    function open(url) {
        var o = ensure();
        var host = o.querySelector(".tfx-host");
        host.innerHTML = '<div class="tfx-card"><p class="tfx-lead">Comparing environments…</p></div>';
        o.style.display = "flex";
        fetch(url, { headers: { "HX-Request": "true" } })
            .then(function (r) { return r.text(); })
            .then(function (html) {
                host.innerHTML = html;
                o._releaseTrap = window.trapFocus
                    ? window.trapFocus(o, window.closeEnvSchemaChanges) : null;
            })
            .catch(function (e) {
                host.innerHTML = '<div class="tfx-card"><p class="tfx-lead">'
                    + "Could not compare the environments: " + String(e) + "</p></div>";
            });
    }

    window.openEnvSchemaChanges = function () { open("/env-schema/changes"); };
    window.openEnvSchemaVerdicts = function () { open("/env-schema/verdicts"); };

    function _err(btn, msg) {
        var card = btn.closest(".tfx-card");
        var box = card && card.querySelector(".tfx-error");
        if (box) { box.hidden = false; box.textContent = msg; }
    }

    /* Record what the user knows: either "the environment is right" or "keep
       treating this as the previous environment did". Both are explicit
       clicks — SM never decides this on its own. */
    window.envVerdict = function (btn, decision, use) {
        var body = new URLSearchParams({
            class_path: btn.getAttribute("data-class") || "",
            field: btn.getAttribute("data-field") || "",
            decision: decision, use: use
        });
        if (btn.getAttribute("data-baseline")) {
            body.set("baseline_key", btn.getAttribute("data-baseline"));
        }
        btn.disabled = true;
        fetch("/env-schema/verdict", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: body.toString()
        }).then(function (r) {
            return r.json().then(function (d) { return { status: r.status, body: d }; });
        }).then(function (res) {
            if (!res.body || !res.body.ok) {
                btn.disabled = false;
                _err(btn, (res.body && res.body.error) || "Could not record that.");
                return;
            }
            var row = btn.closest("tr");
            if (row) {
                row.classList.add("envchg-answered");
                var cell = btn.closest("td");
                if (cell) cell.textContent = "recorded";
            }
            if (window.showToast) {
                window.showToast(res.body.warning
                    || "Recorded — SM will use that in this environment.",
                    res.body.warning ? "warning" : "success");
            }
            try { window.htmx && window.htmx.trigger(document.body, "diagnostics-changed"); } catch (e) {}
        }).catch(function (e) { btn.disabled = false; _err(btn, String(e)); });
    };

    window.envVerdictRevoke = function (btn) {
        var body = new URLSearchParams({
            env_key: btn.getAttribute("data-env-key") || "",
            class_path: btn.getAttribute("data-class") || "",
            field: btn.getAttribute("data-field") || ""
        });
        btn.disabled = true;
        fetch("/env-schema/verdict/revoke", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: body.toString()
        }).then(function () {
            var row = btn.closest("tr");
            if (row && row.parentNode) row.parentNode.removeChild(row);
            try { window.htmx && window.htmx.trigger(document.body, "diagnostics-changed"); } catch (e) {}
        }).catch(function () { btn.disabled = false; });
    };

    window.envSchemaDismiss = function (btn) {
        var card = btn.closest(".tfx-card");
        if (card) {
            fetch("/env-schema/dismiss", {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: new URLSearchParams({
                    from_key: card.getAttribute("data-from") || "",
                    to_key: card.getAttribute("data-to") || "",
                    sig: card.getAttribute("data-sig") || ""
                }).toString()
            }).catch(function () {});
        }
        window.closeEnvSchemaChanges();
    };
})();

/* Content-entry events the app already fires (they bubble to document) — no
   new poller, no route changes, no live-file reads. */
document.addEventListener("DOMContentLoaded", function () {
    window.TypeAlert.check();
    ["diagnostics-changed", "stateRestored", "liveDriftChanged"].forEach(function (ev) {
        document.addEventListener(ev, function () { window.TypeAlert.check(); });
    });
});

/* ------------------------------------------------------------------ */
/* PhysAmp (docs/109): physical output under amp cells + unit setting */
/* ------------------------------------------------------------------ */
/* The server renders MW annotations in canonical dBm and stamps data-dbm on
   the span + data-phys-kind/-fsp on amplitude inputs (core/physical_units.py).
   This module owns the VIEWER's unit preference (localStorage quam_phys_unit:
   'dbm' | 'v' | 'both' — some labs think in dBm, others in volts): one global
   setting reformats EVERY [data-dbm] surface (Live-Edit sub-lines, Components
   tables, inspector notes). V is always V_rms @ 50 Ω and labeled so — the
   same identity as the calculator's dBm↔V section. LF/flux annotations are
   already volts and never converted. Live typing recomputes via the
   FSP-compensation identity (fsp + 20·log10|amp|); invalid or mw-zero →
   blank (never an invented -∞). */
window.PhysAmp = (function () {
    var KEY = 'quam_phys_unit';
    function unit() {
        var u = null;
        try { u = localStorage.getItem(KEY); } catch (e) {}
        return (u === 'v' || u === 'both') ? u : 'dbm';
    }
    function vrms(dbm) { return Math.sqrt(50 * Math.pow(10, (dbm - 30) / 10)); }
    function fmtV(v) {
        return (Math.abs(v) >= 1 ? Number(v.toPrecision(3)) + ' V'
                                 : Number((v * 1e3).toPrecision(3)) + ' mV') + ' rms';
    }
    function fmt(dbm) {
        var d = dbm.toFixed(1) + ' dBm';
        var u = unit();
        if (u === 'dbm') return d;
        if (u === 'v') return fmtV(vrms(dbm));
        return d + ' · ' + fmtV(vrms(dbm));
    }
    function paint(el, dbm) {
        var prefix = el.classList.contains('phys-note') ? '≈ ' : '';
        if (dbm === null || !isFinite(dbm)) {
            el.textContent = '';
            el.removeAttribute('data-dbm');
            return;
        }
        el.setAttribute('data-dbm', String(dbm));
        el.textContent = prefix + fmt(dbm);
    }
    function applyAll(root) {
        (root || document).querySelectorAll('[data-dbm]').forEach(function (el) {
            var d = parseFloat(el.getAttribute('data-dbm'));
            if (isFinite(d)) paint(el, d);
        });
        // Column headers name the unit too (the Components P(·) columns);
        // the 50 Ω assumption is stated where V is shown (audit: it lived
        // only in code comments before).
        var u = unit();
        var lbl = u === 'dbm' ? '(dBm)'
                : u === 'v' ? '(V rms)' : '(dBm · V rms)';
        document.querySelectorAll('.phys-unit-label').forEach(function (el) {
            el.textContent = lbl;
            if (u !== 'dbm') el.title = 'V_rms into 50 Ω';
            else el.removeAttribute('title');
        });
        _markButtons();
    }
    function setUnit(u) {
        try { localStorage.setItem(KEY, u); } catch (e) {}
        applyAll(document);
    }
    function _markButtons() {
        document.querySelectorAll('[data-phys-unit]').forEach(function (b) {
            b.classList.toggle('settings-opt-active',
                               b.getAttribute('data-phys-unit') === unit());
        });
    }
    document.addEventListener('input', function (e) {
        var t = e.target;
        if (!t || !t.classList || !t.classList.contains('bulk-cell')) return;
        var kind = t.getAttribute('data-phys-kind');
        if (!kind) return;
        var td = t.closest('td'); if (!td) return;
        var el = td.querySelector('.bulk-phys'); if (!el) return;
        var v = parseFloat(String(t.value).replace(/,/g, ''));
        if (kind === 'mw') {
            var fsp = parseFloat(t.getAttribute('data-phys-fsp'));
            if (isFinite(v) && isFinite(fsp) && v !== 0) {
                paint(el, fsp + 20 * Math.log10(Math.abs(v)));
            } else {
                paint(el, null);
            }
        } else {
            el.textContent = isFinite(v)
                ? (Math.abs(v) >= 1 || v === 0
                   ? Number(v.toPrecision(3)) + ' V'
                   : Number((v * 1e3).toPrecision(3)) + ' mV')
                : '';
        }
    }, true);
    // Fresh server fragments arrive in canonical dBm — reformat to the
    // viewer's unit on every swap (and once at load). The gate also matches
    // .phys-unit-label (audit): a P(·) column whose every row failed to
    // annotate ships a header but zero [data-dbm] cells, and its "(dBm)"
    // must still be relabeled to the viewer's unit.
    function _onSwap(e) {
        if (e.target && e.target.querySelector
            && e.target.querySelector('[data-dbm], .phys-unit-label')) {
            applyAll(e.target);
        }
    }
    document.addEventListener('htmx:afterSwap', _onSwap);
    document.addEventListener('htmx:oobAfterSwap', _onSwap);
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { applyAll(document); });
    } else {
        applyAll(document);
    }
    return { unit: unit, setUnit: setUnit, applyAll: applyAll,
             fmt: fmt, vrms: vrms };
})();


/* ------------------------------------------------------------------ */
/* Keyboard polish (docs/113 #13): '/' search, Ctrl+Enter, '?' sheet  */
/* ------------------------------------------------------------------ */
/* Three low-overhead shortcuts (each gated on not-typing-in-a-field):
   - '/' focuses the page's PRIMARY search: the first visible search box in
     the main pane, else the topbar global search — the muscle memory every
     list UI ships.
   - Ctrl+Enter presses "Apply all" when the Live-Edit grid is mounted and
     the button is armed (disabled = silently nothing; the button's own
     confirm/warning path runs unchanged — this is a CLICK, not a bypass).
   - '?' (Shift+/) toggles a static shortcut cheat sheet (Esc closes; the
     shared trapFocus keeps Tab inside). */
(function () {
    function _typing() {
        var a = document.activeElement;
        return a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA'
                     || a.isContentEditable);
    }
    function _primarySearch() {
        var pane = document.getElementById('table-pane');
        if (pane) {
            var els = pane.querySelectorAll('input[type="search"], .tree-search');
            // offsetParent is null for EVERYTHING when no layout engine runs
            // (jsdom) — only trust it when at least one element has layout.
            var layout = false;
            for (var i = 0; i < els.length; i++) {
                if (els[i].offsetParent !== null) { layout = true; break; }
            }
            for (var j = 0; j < els.length; j++) {
                var el = els[j];
                if (el.hidden || (el.closest && el.closest('[hidden]'))) continue;
                if (layout && el.offsetParent === null) continue;
                return el;
            }
        }
        return document.getElementById('global-search');
    }
    var SHEET_ID = 'kb-cheatsheet';
    function _sheet() { return document.getElementById(SHEET_ID); }
    function _closeSheet() {
        var el = _sheet();
        if (!el) return;
        if (el._releaseTrap) { try { el._releaseTrap(); } catch (e) {} }
        el.remove();
    }
    window._kbToggleCheatsheet = function () {
        if (_sheet()) { _closeSheet(); return; }
        var wrap = document.createElement('div');
        wrap.id = SHEET_ID;
        wrap.className = 'kb-sheet-backdrop';
        wrap.setAttribute('role', 'dialog');
        wrap.setAttribute('aria-label', 'Keyboard shortcuts');
        var card = document.createElement('div');
        card.className = 'kb-sheet';
        var rows = [
            ['Global', ''],
            ['Ctrl+K', 'search palette (toggle)'],
            ['/', 'focus the page search'],
            ['?', 'this cheat sheet'],
            ['Alt+C', 'calculator'],
            ['Ctrl+Z / Ctrl+Shift+Z', 'undo / redo (crosses saves — staged into Review)'],
            ['Live State Edit — the Qubits grid', ''],
            ['Tab / Shift+Tab', 'hop between edit cells'],
            ['Shift+click / Ctrl+click', 'select a range / toggle cells in ONE column'],
            ['Ctrl+D', 'fill selection from the anchor cell'],
            ['Ctrl+V (multi-line)', 'paste a column downward'],
            ['Ctrl+Enter', 'Apply all (when armed)'],
            ['Datasets', ''],
            ['j / k', 'move through runs'],
            ['Enter / Space', 'open / select the active run'],
            ['[ / ]', 'previous / next run in the open detail'],
        ];
        var h = document.createElement('h3');
        h.textContent = 'Keyboard shortcuts';
        card.appendChild(h);
        var dl = document.createElement('div');
        dl.className = 'kb-sheet-rows';
        rows.forEach(function (r) {
            if (!r[1]) {
                var g = document.createElement('div');
                g.className = 'kb-sheet-group';
                g.textContent = r[0];
                dl.appendChild(g);
                return;
            }
            var row = document.createElement('div');
            row.className = 'kb-sheet-row';
            var k = document.createElement('kbd');
            k.textContent = r[0];
            var v = document.createElement('span');
            v.textContent = r[1];
            row.appendChild(k); row.appendChild(v);
            dl.appendChild(row);
        });
        card.appendChild(dl);
        var hint = document.createElement('p');
        hint.className = 'muted kb-sheet-hint';
        hint.textContent = 'Esc closes';
        card.appendChild(hint);
        var close = document.createElement('button');
        close.className = 'btn-sm outline kb-sheet-close';
        close.textContent = 'Close';
        close.addEventListener('click', _closeSheet);
        card.appendChild(close);
        wrap.appendChild(card);
        wrap.addEventListener('click', function (ev) {
            if (ev.target === wrap) _closeSheet();
        });
        document.body.appendChild(wrap);
        if (window.trapFocus) wrap._releaseTrap = window.trapFocus(wrap, _closeSheet);
        close.focus();
    };
    function _modalOpen() {
        // a second focus trap over an open modal breaks BOTH (the older
        // capture-phase trap runs first, so Escape closed the wrong thing).
        // Visibility, not attributes — see window.smModalOpen.
        return window.smModalOpen ? window.smModalOpen() : false;
    }
    document.addEventListener('keydown', function (ev) {
        // KEY FIRST: this runs on every keystroke in the app, so no DOM query
        // may happen before we know the key is one of ours (measured: the
        // modal-guard querySelector alone cost ~2 ms per keystroke on a
        // 4,851-cell grid page — on the typing path).
        var mine = (ev.key === '/' || ev.key === '?' || ev.key === 'Escape'
                    || (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)));
        if (!mine) return;
        if (ev.key === 'Escape' && _sheet()) { _closeSheet(); return; }
        if (ev.key === 'Escape') return;      // Escape is otherwise not ours
        if (!_sheet() && _modalOpen()) return;
        if (_typing()) {
            // Ctrl+Enter works FROM a grid cell too — that is where the user is
            if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
                var btn0 = document.getElementById('bulk-apply-all');
                if (btn0 && !btn0.disabled) { ev.preventDefault(); btn0.click(); }
            }
            return;
        }
        if (ev.ctrlKey || ev.metaKey || ev.altKey) {
            if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
                var btn = document.getElementById('bulk-apply-all');
                if (btn && !btn.disabled) { ev.preventDefault(); btn.click(); }
            }
            return;
        }
        if (ev.key === '/') {
            var el = _primarySearch();
            if (el) { ev.preventDefault(); el.focus(); el.select && el.select(); }
        } else if (ev.key === '?') {
            ev.preventDefault();
            window._kbToggleCheatsheet();
        }
    });
})();


/* Is a real modal on screen? The app closes its overlays with
   display:none (not [hidden]) and base.html always renders several
   role="dialog" nodes, so an attribute test is either always-true or
   always-false — both were shipped and both were wrong. Test what the
   user can actually see. */
window.smModalOpen = function () {
    if (document.querySelector('dialog[open]')) return true;
    var sel = '.ch-overlay, #state-review-overlay, #live-drift-overlay,'
            + ' #version-diff-overlay,'
            + ' #plot-apply-popup, #new-run-popup, #cmd-palette, #kb-cheatsheet,'
            + ' .modal';
    var els = document.querySelectorAll(sel);
    for (var i = 0; i < els.length; i++) {
        var el = els[i];
        if (el.hidden) continue;
        var st = window.getComputedStyle(el);
        if (st.display !== 'none' && st.visibility !== 'hidden') return true;
    }
    return false;
};

/* ------------------------------------------------------------------ */
/* PaneState (docs/110 #10-A): a tab keeps its state when you return   */
/* ------------------------------------------------------------------ */
/* THE most-asked-for UX fix: navigating the main pane used to destroy the
   previous surface wholesale (search text, expanded tree nodes, scroll).

   v2 architecture (the stream-1 audit killed v1's beforeRequest
   interception: it poisoned htmx's history snapshot, bypassed htmx's
   pushState, and a failed nav left a blank pane):
   - Every navigation runs COMPLETELY NORMALLY through htmx (request,
     history snapshot, URL push, swap) -- PaneState never cancels anything.
   - PARK happens at htmx:beforeSwap on #table-pane, i.e. AFTER htmx took
     its history snapshot of the outgoing page and ONLY when a real swap is
     about to replace the DOM (a failed request never parks -- the pane
     stays intact). KEEP routes detach their children into the stash;
     every SOFT route refreshes its search-input capture here too (so a
     deliberately cleared box is captured as cleared -- never resurrected).
   - RESTORE happens at htmx:afterSwap: if the arriving route has a FRESH
     parked copy (tray data-seq + chip token unmoved), the just-swapped
     server render is discarded (Plotly-purged first) and the parked DOM
     re-attached -- search text, expanded nodes, scroll, everything. The
     redundant fetch is the price of letting htmx own history; what
     keep-alive preserves is CLIENT state, not server cost. A background
     /state/tray fetch then re-verifies the seq against server truth (the
     on-screen tray can lag a scheduler adopt) -- a mismatch refetches the
     pane fresh. Stale/chip-moved copies are dropped and the SOFT tier
     re-applies the query over the fresh DOM instead.
   - Back/forward belongs to htmx's own history machinery: popstate and
     htmx:historyRestore clear the stash AND re-sync the current route.
   Parked DOM is detached -- getElementById can't see it, pollers no-op
   until restore. Stash: LRU 4 (evicted holders are Plotly-purged). */
window.PaneState = (function () {
    var KEEP = ['/explorer', '/bulk'];
    var SOFT = ['/explorer', '/bulk', '/datasets', '/param-history',
                '/pulses', '/state-history', '/qubits', '/pairs',
                '/resonators', '/flux', '/couplers'];
    var MAX = 4;
    var stash = {};   // route -> {holder, seq, chip, scroll, order}
    var soft = {};    // route -> {inputs: [{key, value}]}
    var _order = 0;
    var _cur = location.pathname;

    function pane() { return document.getElementById('table-pane'); }
    function seqNow() {
        var t = document.getElementById('pending-tray');
        return t ? (t.getAttribute('data-seq') || '') : '';
    }
    function chipNow() { return String(window.__chipToken || ''); }

    function _purge(root) {
        // The app-wide rule: a Plotly node must never die via innerHTML
        // without purge (WebGL contexts + DOM refs leak). Structural lookup
        // (docs/124 §4.3 — the class does not always survive); bare class
        // only if PlotHost is somehow absent.
        if (window.PlotHost) {
            try { window.PlotHost.purgeWithin(root); } catch (e) {}
            return;
        }
        if (window.Plotly && root.querySelectorAll) {
            root.querySelectorAll('.js-plotly-plot').forEach(function (n) {
                try { window.Plotly.purge(n); } catch (e) {}
            });
        }
    }
    /* docs/122 item 2: the SOFT tier used to carry the search TEXT and nothing
       else, so an /explorer rebuild -- which an armed Auto-Sync pull triggers
       unattended, measured at ~25 s after a qualibrate write -- still lost the
       expanded nodes, the state/wiring tab and the scroll position. Those are
       state the user built by hand; a rebuild that keeps only the text still
       reads as "it reset itself". */
    function _captureExplorer() {
        var st = document.getElementById('explorer-tree-state');
        var wi = document.getElementById('explorer-tree-wiring');
        if (!st || !wi) return null;
        var onState = st.style.display !== 'none';
        var active = onState ? st : wi;
        return {
            tab: onState ? 'state' : 'wiring',
            expanded: window.jsonTreeExpandedPaths
                ? window.jsonTreeExpandedPaths(active.id) : [],
            scroll: active.scrollTop || 0,
            pane: (pane() || {}).scrollTop || 0,
        };
    }
    /* A retried scroll restore must never fight the user. Same rule docs/75's
       InlineCommit already applies to its own restore: a wheel or a PageUp
       means they are reading somewhere else now, and the pending attempts are
       abandoned.
       Two hard-won rules (docs/124 M-16/M-17):
       - the abort state is PER RESTORE, and a new restore SUPERSEDES every
         older one's timers via a generation counter. One shared boolean let
         arming restore B reset the flag and resurrect restore A's
         already-aborted retries, which yanked the user to A's stale target
         (executed: four ping-pong yanks over 2 s).
       - Chrome gives scrollbar interaction no wheel/keydown at all — a track
         click or thumb drag targets the SCROLLER ELEMENT itself, never a row
         — and ArrowUp/ArrowDown/Space scroll too; all were invisible to the
         old listener set and yanked back by the next retry. A raw 'scroll'
         listener is deliberately NOT the fix: while the filter settles the
         browser CLAMPS scrollTop and fires the same event, which would read
         as a user scroll and abort the very restore the retries exist for. */
    var _restoreGen = 0;
    function _armScrollAbort() {
        var state = { aborted: false };
        function typing(e) {
            var t = e.target;
            return !!(t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA'
                            || t.isContentEditable));
        }
        function off() { state.aborted = true; cleanup(); }
        function onKey(e) {
            if (typing(e)) return;   // arrows inside the search box are typing
            if (e.key === 'PageUp' || e.key === 'PageDown' || e.key === 'Home'
                || e.key === 'End' || e.key === 'ArrowUp' || e.key === 'ArrowDown'
                || e.key === ' ') off();
        }
        function onDown(e) {
            if (e.button === 1) { off(); return; }   // middle-click autoscroll
            var t = e.target;
            if (t && ((t.classList && t.classList.contains('json-tree'))
                      || t.id === 'table-pane')) off();
        }
        function cleanup() {
            window.removeEventListener('wheel', off, true);
            window.removeEventListener('keydown', onKey, true);
            window.removeEventListener('touchmove', off, true);
            window.removeEventListener('mousedown', onDown, true);
        }
        window.addEventListener('wheel', off, true);
        window.addEventListener('touchmove', off, true);
        window.addEventListener('keydown', onKey, true);
        window.addEventListener('mousedown', onDown, true);
        setTimeout(cleanup, 2600);
        return state;
    }

    function _restoreExplorer(d) {
        if (!d || !document.getElementById('explorer-tree-state')) return;
        var gen = ++_restoreGen;
        var abort = _armScrollAbort();
        if (d.tab === 'wiring' && window.switchExplorerTab) {
            window.switchExplorerTab('wiring');
        }
        var id = d.tab === 'wiring' ? 'explorer-tree-wiring' : 'explorer-tree-state';
        // Expansion FIRST: the search that follows hides rows, it does not
        // collapse them, so restoring in this order gives the filter the same
        // tree the user was looking at.
        if (window.jsonTreeSetExpanded) window.jsonTreeSetExpanded(id, d.expanded || []);
        /* Scroll is RETRIED, not scheduled once.
           A single delayed write silently CLAMPS: the search that follows is
           debounced 200 ms and then hides rows across a 7,800-row tree, so for
           a while the document is shorter than the offset we are restoring and
           the browser quietly truncates it. Measured on the real chip: a
           restore of 420 landed on 119 and looked preserved only because the
           earlier probe had itself been clamped to the same number at BOTH
           ends. So: try, check whether it stuck, and try again while the layout
           is still settling. Stops as soon as it takes (or the attempts run
           out) — never loops, never fights a user who scrolled meanwhile. */
        var tries = [260, 700, 1400, 2400];
        tries.forEach(function (ms, i) {
            setTimeout(function () {
                var el = document.getElementById(id);
                var p = pane();
                // A scroll the user has since moved themselves is theirs —
                // and a NEWER restore owns the pane now (generation check:
                // a superseded restore's timers must never write its stale
                // target, aborted or not).
                if (abort.aborted || gen !== _restoreGen) return;
                if (el && d.scroll && Math.abs(el.scrollTop - d.scroll) > 4) {
                    el.scrollTop = d.scroll;
                }
                if (p && d.pane && Math.abs(p.scrollTop - d.pane) > 4) {
                    p.scrollTop = d.pane;
                }
            }, ms);
        });
    }
    function _captureSoft(root, route) {
        var inputs = [];
        var els = root.querySelectorAll('input[type="search"], .tree-search');
        Array.prototype.forEach.call(els, function (el, i) {
            // keyed by id when present, else by position -- 8 of the 11 SOFT
            // routes have id-less filter boxes (audit)
            inputs.push({ key: el.id ? ('#' + el.id) : ('@' + i),
                          value: el.value });
        });
        var d = { inputs: inputs };
        if (route === '/explorer') d.explorer = _captureExplorer();
        return d;
    }
    function _reapplySoft(route) {
        var d = soft[route];
        if (!d) return;
        var p = pane();
        if (!p) return;
        if (d.explorer) _restoreExplorer(d.explorer);
        if (!d.inputs.length) return;
        var els = p.querySelectorAll('input[type="search"], .tree-search');
        d.inputs.forEach(function (it) {
            var el = null;
            if (it.key.charAt(0) === '#') {
                el = p.querySelector('input[id="' + it.key.slice(1) + '"]');
            } else {
                el = els[parseInt(it.key.slice(1), 10)] || null;
            }
            if (el && it.value && el.value !== it.value) {
                el.value = it.value;
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
    }
    function _park(route) {
        var p = pane();
        if (!p || !p.firstChild) return;
        if (SOFT.indexOf(route) >= 0) soft[route] = _captureSoft(p, route);
        if (KEEP.indexOf(route) < 0) return;
        var holder = document.createElement('div');
        while (p.firstChild) holder.appendChild(p.firstChild);
        stash[route] = { holder: holder, seq: seqNow(), chip: chipNow(),
                         scroll: p.scrollTop, order: ++_order };
        var keys = Object.keys(stash);
        if (keys.length > MAX) {
            keys.sort(function (a, b) { return stash[a].order - stash[b].order; });
            _purge(stash[keys[0]].holder);
            delete stash[keys[0]];
        }
    }
    function _verifyRestore(route, seqAtRestore) {
        // Server-truth re-verify (audit M5): the on-screen tray can lag a
        // mutation that never answered THIS tab (scheduler post-node adopt,
        // a second window's edit). Mismatch => the restored pane lied --
        // refetch it fresh. Best-effort: a failed probe changes nothing.
        try {
            fetch('/state/tray', { cache: 'no-store' })
                .then(function (r) { return r.ok ? r.text() : null; })
                .then(function (html) {
                    if (!html) return;
                    var m = html.match(/data-seq="([^"]*)"/);
                    if (!m || m[1] === seqAtRestore) return;
                    if (_cur !== route || !window.htmx) return;
                    delete stash[route];
                    window.htmx.ajax('GET', route, {
                        source: '#table-pane', target: '#table-pane',
                        swap: 'innerHTML' });
                })
                .catch(function () {});
        } catch (e) {}
    }
    function _tryRestore(route) {
        var e = stash[route];
        if (!e) return false;
        delete stash[route];
        // stale -- keep the fresh swap; the SOFT tier re-applies the query
        if (e.seq !== seqNow() || e.chip !== chipNow()) return false;
        var p = pane();
        if (!p) return false;
        _purge(p);                 // the redundant fresh render dies cleanly
        p.innerHTML = '';
        while (e.holder.firstChild) p.appendChild(e.holder.firstChild);
        p.scrollTop = e.scroll || 0;
        if (window.PhysAmp) window.PhysAmp.applyAll(p);
        document.dispatchEvent(new CustomEvent('paneRestored',
                                               { detail: { route: route } }));
        _verifyRestore(route, e.seq);
        return true;
    }
    function _routeOf(detail) {
        var pi = detail && detail.pathInfo;
        var path = pi && (pi.finalRequestPath || pi.requestPath);
        if (!path && detail && detail.requestConfig) path = detail.requestConfig.path;
        return path ? String(path).split('?')[0] : null;
    }

    /* docs/139 fix 1 - skip the fetch when the parked copy will win anyway.
       docs/110's v2 doctrine ("never cancels anything") let htmx own history
       by paying a redundant fetch + a fresh ~MB render that _tryRestore then
       threw away - measured at 2.4 s of htmx SWAP alone on the 452-column
       bulk grid. This amends that doctrine ONE step: when the arriving KEEP
       route has a FRESH parked copy (tray seq + chip token unmoved - the
       exact _tryRestore gate), the request is cancelled BEFORE it is sent
       and the parked DOM restored synchronously. Everything else about v2
       stands: a stale/absent copy fetches normally, a same-route click is a
       deliberate refresh and always fetches, and _verifyRestore still
       re-checks the seq against server truth in the background (mismatch =>
       fresh refetch), so the safety net is unchanged. htmx takes no history
       snapshot for a cancelled nav - Back is covered by _historyReset's
       existing clear-and-refetch fallback, the same path an htmx
       parked-empty snapshot already rides. */
    document.addEventListener('htmx:beforeRequest', function (evt) {
        var tgt = evt.detail && evt.detail.target;
        if (!tgt || tgt.id !== 'table-pane') return;
        var cfg = evt.detail.requestConfig || {};
        if (String(cfg.verb || 'get').toLowerCase() !== 'get') return;
        var route = _routeOf(evt.detail);
        if (!route || KEEP.indexOf(route) < 0 || route === _cur) return;
        var e = stash[route];
        if (!e || e.seq !== seqNow() || e.chip !== chipNow()) return;
        evt.preventDefault();
        // A cancelled request is not an in-flight request. htmx itself is
        // clean (it adds its indicator classes AFTER the beforeRequest
        // check), but THREE app listeners downstream of this one count the
        // event as a live xhr and only ever clear on a terminal xhr event
        // that now never comes: NavProgress (count++ -> the brand progress
        // bar ticked forever AND polled /api/progress every 350 ms, which is
        // its own slowdown - customer-reported at 154 s), the slow-request
        // overlay, and the inline-edit commit marker. This handler is
        // registered first, so stopping propagation is what makes the cancel
        // truthful to the rest of the app.
        evt.stopImmediatePropagation();
        _park(_cur);
        // {htmx:true} - the exact state htmx stamps on ITS entries, so Back
        // into/out of a skip entry runs htmx's own popstate restore (cache
        // miss => server history-restore of the full page). A plain entry
        // left htmx blind: measured live, Back restored NOTHING and later
        // POISONED htmx's cache (bulk content saved under /explorer).
        try { history.pushState({ htmx: true }, '', route); } catch (err) {}
        _cur = route;
        var sp = pane();
        if (sp) sp.setAttribute('data-pane-route', route);
        if (!_tryRestore(route) && window.htmx) {
            // belt-and-braces: the copy refused at the last instant (pane
            // gone mid-flight) - never leave a blank pane, fetch fresh.
            window.htmx.ajax('GET', route, { source: '#table-pane',
                target: '#table-pane', swap: 'innerHTML' });
        }
        if (window.syncSidebarNavActive) window.syncSidebarNavActive();
    });
    document.addEventListener('htmx:beforeSwap', function (evt) {
        if (!evt.target || evt.target.id !== 'table-pane') return;
        if (evt.detail && evt.detail.shouldSwap === false) return;
        var inRoute = _routeOf(evt.detail);
        // park the OUTGOING route (htmx's history snapshot is already taken);
        // a same-route refresh only refreshes the SOFT capture, never parks
        if (inRoute && inRoute !== _cur) _park(_cur);
        else if (SOFT.indexOf(_cur) >= 0 && pane()) soft[_cur] = _captureSoft(pane(), _cur);
    });
    document.addEventListener('htmx:afterSwap', function (evt) {
        if (!evt.target || evt.target.id !== 'table-pane') return;
        var route = _routeOf(evt.detail);
        if (route) _cur = route;
        // The content's own route, stamped ON the pane (docs/139 fix 1): a
        // skip-nav pushes URLs htmx has no snapshot for, so after Back the
        // pane can hold route A under URL B with nothing blank to notice.
        // htmx's history snapshot preserves the attribute, so it stays
        // truthful through both machineries.
        evt.target.setAttribute('data-pane-route', _cur);
        if (!_tryRestore(_cur)) _reapplySoft(_cur);
    });
    // A wholesale working-copy replacement invalidates every parked pane;
    // back/forward belongs to htmx's own history machinery -- clear AND
    // re-sync the route (audit M3: a desynced _cur parked the WRONG DOM).
    function _historyReset() {
        for (var k in stash) _purge(stash[k].holder);
        stash = {};
        _cur = location.pathname;
        // htmx's history cache may hold a snapshot taken while this pane was
        // PARKED (i.e. empty). Never leave the user on a blank pane: refetch.
        setTimeout(function () {
            var p = pane();
            if (!p || !window.htmx) return;
            // Blank pane (parked-empty snapshot) OR a pane whose stamped
            // route disagrees with the URL (a skip-nav pushState entry htmx
            // could not restore -- measured live: /bulk's grid standing
            // under /explorer after Back). An unstamped pane is a full page
            // load: the server rendered it for THIS url, leave it alone.
            var stamped = p.getAttribute('data-pane-route');
            var mismatch = stamped && stamped !== location.pathname;
            if (!p.firstElementChild || mismatch) {
                // A mismatch also means htmx's history cache is POISONED:
                // its private currentPathForHistory does not move on a skip
                // pushState, so its next popstate save files the on-screen
                // content under the WRONG url (measured live: bulk grid
                // cached under /explorer, then served back from that cache).
                // Drop the cache - the next Back server-restores instead.
                if (mismatch) {
                    try { localStorage.removeItem('htmx-history-cache'); } catch (e2) {}
                }
                // popstate AND htmx:historyRestore both funnel here - one
                // refetch is enough.
                if (window.PaneState.__refetchFor === location.pathname) return;
                window.PaneState.__refetchFor = location.pathname;
                setTimeout(function () { window.PaneState.__refetchFor = null; }, 1000);
                window.htmx.ajax('GET', location.pathname + location.search,
                                 { source: '#table-pane', target: '#table-pane',
                                   swap: 'innerHTML' });
            }
        }, 60);
    }
    document.addEventListener('stateRestored', function () {
        for (var k in stash) _purge(stash[k].holder);
        stash = {};
    });
    window.addEventListener('popstate', _historyReset);
    document.addEventListener('htmx:historyRestore', _historyReset);

    return {
        // docs/122 item 4: the global purge-on-swap must not kill plots that are
        // about to be PARKED alive. Answers for the CURRENT route by default —
        // that is the one whose DOM is leaving.
        isKeepRoute: function (route) { return KEEP.indexOf(route || _cur) >= 0; },
        // docs/124 (the parked-observer minor): PlotHost's registry sweep must
        // not treat a parked-but-returning subtree as dead DOM — its observers
        // survive detach/re-attach (a ResizeObserver keeps its observation and
        // re-fires with the new size, verified in real Chrome), so sweeping
        // them handed the restored pane back with zero live observers and
        // nothing re-observing. Latent while KEEP=['/explorer'] holds no
        // plots; armed the moment KEEP grows.
        holdsDetached: function (el) {
            for (var k in stash) {
                var h = stash[k] && stash[k].holder;
                if (h && (h === el || (h.contains && h.contains(el)))) return true;
            }
            return false;
        },
        _stash: function () { return stash; },
        _cur: function () { return _cur; },
        _soft: function () { return soft; },
        clear: function () { stash = {}; soft = {}; },
    };
})();

/* docs/115 (#14): the tray's teaching line — shown until dismissed ONCE.
   A new user forms the working-copy model in their first minutes; hiding
   the explanation until they already understand it is backwards. */
/* The docs/115 tray-teach banner is gone (docs/132 follow-up, customer:
   nobody read the always-on explainer) — the teaching lives in the sync
   badge's hover title now, rendered server-side in _pending_tray.html. */

/* docs/115 (#14) — the landing CTA opens the SAME folder browser the
   sidebar's State Load uses, bound to that form's input (the audit caught
   the first version calling openFolderBrowser() with no target, so picking
   a folder filled nothing and submitted nothing). If the browser is
   unavailable, focus the input so the CTA is never a dead click. */
window.smOpenStateFolder = function () {
    var input = document.getElementById('load-path-input')
             || document.querySelector('#load-form input[name="folder"]');
    if (input && window.openFolderBrowser) {
        window.openFolderBrowser(input.id || 'load-path-input');
        return;
    }
    if (input) { input.focus(); input.scrollIntoView({block: 'center'}); }
};

/* ------------------------------------------------------------------ */
/* Value delta (Δ) — the JS mirror of core/value_delta.py               */
/* ------------------------------------------------------------------ */
/* Every before→after surface shows old, new AND the difference (docs/76).
   This is the client half; it must agree with the Python half character for
   character — tests/test_value_delta.py feeds the same case table through
   both and diffs the output.

   The subtraction is EXACT DECIMAL arithmetic over BigInt, not float: in
   binary floating point 5.2 - 5.1 is 0.10000000000000053, and showing that
   as a researcher's "difference" is worse than showing nothing. Both sides
   are parsed from their shortest round-tripping decimal spelling, so the
   answer reads 0.1 — the number a physicist would have written down. */
window.ValueDelta = (function () {
    var GROUPED = /^[+-]?\d[\d,]*(\.\d+)?$/;
    var DECIMAL = /^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;
    var SCI_HIGH_EXP = 16;    // |v| >= 1e15  (mirrors _SCI_HIGH)
    var SCI_LOW_EXP = -6;     // |v| <  1e-6  (mirrors _SCI_LOW)

    /* -> {mant: BigInt, scale: int} with value = mant * 10^-scale, or null */
    function parse(value) {
        if (value === null || value === undefined || typeof value === "boolean") return null;
        var s;
        if (typeof value === "number") {
            if (!isFinite(value)) return null;
            s = String(value);                  // shortest round-tripping form
        } else if (typeof value === "string") {
            s = value.trim();
            if (s.indexOf(",") >= 0 && GROUPED.test(s)) s = s.replace(/,/g, "");
            if (!s) return null;
        } else {
            return null;                        // list / dict / anything else
        }
        var m = DECIMAL.exec(s);
        if (!m) return null;
        var frac = m[3] || "";
        var exp = m[4] ? parseInt(m[4], 10) : 0;
        var mant = BigInt(m[2] + frac);
        if (m[1] === "-") mant = -mant;
        return { mant: mant, scale: frac.length - exp };
    }

    function align(a, b) {
        var s = Math.max(a.scale, b.scale);
        var am = a.mant * pow10(s - a.scale);
        var bm = b.mant * pow10(s - b.scale);
        return { am: am, bm: bm, scale: s };
    }
    function pow10(n) {
        var r = 1n;
        for (var i = 0; i < n; i++) r *= 10n;
        return r;
    }
    function toNumber(mant, scale) { return Number(mant.toString() + "e" + (-scale)); }

    function padExp(s) { return s.replace(/[eE]([+-])(\d)$/, "e$10$2"); }

    function groupInt(digits) {
        return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    }

    /* Format |mant * 10^-scale| exactly as core.value_delta._format_magnitude. */
    function formatMagnitude(mant, scale) {
        if (mant === 0n) return "0";
        var digits = (mant < 0n ? -mant : mant).toString();
        var expo = digits.length - scale;        // value in [10^(expo-1), 10^expo)
        if (expo >= SCI_HIGH_EXP || expo <= SCI_LOW_EXP) {
            return padExp(Math.abs(toNumber(mant, scale)).toExponential(3));
        }
        var intPart, frac;
        if (scale <= 0) {
            intPart = digits + new Array(1 - scale).join("0");
            frac = "";
        } else {
            while (digits.length <= scale) digits = "0" + digits;
            intPart = digits.slice(0, digits.length - scale);
            frac = digits.slice(digits.length - scale).replace(/0+$/, "");
        }
        return groupInt(intPart) + (frac ? "." + frac : "");
    }

    function formatDelta(mant, scale) {
        if (mant === 0n) return "0";
        return (mant < 0n ? "-" : "+") + formatMagnitude(mant, scale);
    }

    function formatPercent(pct) {
        var a = Math.abs(pct);
        if (a && a < 0.001) return padExp(sign(pct) + Math.abs(pct).toExponential(2));
        var digits = a >= 100 ? 0 : (a >= 10 ? 1 : (a >= 1 ? 2 : 3));
        var s = sign(pct) + Math.abs(pct).toFixed(digits);
        if (digits) s = s.replace(/0+$/, "").replace(/\.$/, "");
        return s;
    }
    function sign(v) { return v < 0 ? "-" : "+"; }

    /* {delta, text, pct, pct_text, dir, coerced, title} | null */
    function compute(oldValue, newValue) {
        var a = parse(oldValue), b = parse(newValue);
        if (!a || !b) return null;
        var al = align(a, b);
        var dm = al.bm - al.am;
        var aNum = toNumber(al.am, al.scale);
        var text = formatDelta(dm, al.scale);
        var pct = null, pctText = null;
        if (aNum !== 0 && dm !== 0n) {
            pct = toNumber(dm, al.scale) / Math.abs(aNum) * 100;
            if (isFinite(pct)) pctText = formatPercent(pct) + "%"; else pct = null;
        }
        var dir = dm > 0n ? "up" : (dm < 0n ? "down" : "same");
        var coerced = (typeof oldValue === "string") || (typeof newValue === "string");
        var title = "difference: " + text + (pctText ? " (" + pctText + ")" : "");
        if (coerced) title += " — one side is stored as text";
        if (dm === 0n) title = "same numeric value" + (coerced ? " (stored type differs)" : "");
        return { delta: toNumber(dm, al.scale), text: text, pct: pct,
                 pct_text: pctText, dir: dir, coerced: coerced, title: title };
    }

    /* Standard chip markup, shared by every JS-rendered surface. Returns ""
       when a delta is meaningless, so callers can append unconditionally. */
    function chipHtml(oldValue, newValue, extraClass) {
        var d = compute(oldValue, newValue);
        if (!d) return "";
        var cls = "val-delta delta-" + d.dir + (extraClass ? " " + extraClass : "");
        var esc = function (s) {
            return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
                .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
        };
        return '<span class="' + cls + '" title="' + esc(d.title) + '">' + esc(d.text)
             + (d.pct_text ? ' <span class="val-delta-pct">(' + esc(d.pct_text) + ')</span>' : '')
             + '</span>';
    }

    /* Fill an existing element with the chip (or blank it). Only the
       val-delta / delta-<dir> classes are managed — the caller's own classes
       on that element are left alone. */
    function paint(el, oldValue, newValue) {
        if (!el) return null;
        var d = compute(oldValue, newValue);
        el.classList.remove("delta-up", "delta-down", "delta-same");
        if (!d) {
            el.textContent = ""; el.hidden = true; el.removeAttribute("title");
            return null;
        }
        el.classList.add("val-delta", "delta-" + d.dir);
        el.hidden = false;
        el.title = d.title;
        el.textContent = d.text + (d.pct_text ? " (" + d.pct_text + ")" : "");
        return d;
    }

    return { compute: compute, chipHtml: chipHtml, paint: paint,
             formatDelta: formatDelta, formatPercent: formatPercent, parse: parse };
})();

/* ------------------------------------------------------------------ */
/* Inline-edit commit plumbing (Pulses detail, Qubit/Pair inspector)    */
/* ------------------------------------------------------------------ */
/* One commit = POST → the whole #inspector-pane re-renders. Three things
   must hold across that swap or the surface feels broken (docs/75):
     1. the swap REMOVES the focused input, which fires focusout on it —
        that must not re-submit the form that is still committing,
     2. an htmx-owned form must never fall back to a NATIVE submission,
     3. focus, caret and panel scroll must survive, so the next keystroke
        lands where the user is looking.
   Pinned by tests/pulses_commit_selfcheck.cjs. */
window.InlineCommit = (function () {
    var INLINE_SEL = 'form.inline-edit input[name="value"]';
    var RESTORE_TTL_MS = 5000;
    // The pane keeps growing after the swap (Plotly re-renders the waveform at
    // +250ms and its newPlot resolves later still), so a single scrollTop write
    // at settle time gets CLAMPED to the not-yet-final scrollHeight. Re-apply
    // over the whole settling window instead of guessing one delay.
    var RESTORE_PASSES_MS = [0, 120, 300, 600, 1000];
    var FOCUSABLE = ['input:not([type="hidden"]):not([disabled])',
                     'button:not([disabled])', 'select:not([disabled])',
                     'textarea:not([disabled])', 'a[href]',
                     '[tabindex]:not([tabindex="-1"])'].join(",");
    var pending = null;
    var userMoved = false;      // the user scrolled/clicked since the commit

    /* htmx marks the requesting element with .htmx-request; we ALSO carry our
       own flag so the in-flight window is detected even if htmx's bookkeeping
       moves (the flag rides the element, which the swap discards anyway). */
    function inFlight(form) {
        return !!(form && ((form.classList && form.classList.contains("htmx-request"))
                           || (form.dataset && form.dataset.committing === "1")));
    }

    function fieldKey(input) {
        var form = input.closest && input.closest("form");
        var dp = form && form.querySelector('input[name="dot_path"]');
        if (dp && dp.value) return "dot:" + dp.value;
        var p = input.getAttribute("data-param");
        return p ? "param:" + p : null;
    }

    function findByKey(key) {
        if (!key) return null;
        var cut = key.indexOf(":");
        var kind = key.slice(0, cut), val = key.slice(cut + 1);
        var all = document.querySelectorAll(INLINE_SEL);
        for (var i = 0; i < all.length; i++) {
            var el = all[i];
            if (kind === "param") {
                if (el.getAttribute("data-param") === val) return el;
            } else {
                var f = el.closest("form");
                var dp = f && f.querySelector('input[name="dot_path"]');
                if (dp && dp.value === val) return el;
            }
        }
        return null;
    }

    function scrollerOf(el) {
        var n = el;
        while (n && n !== document.body) {
            if (n.scrollHeight > n.clientHeight + 4) return n;
            n = n.parentElement;
        }
        return null;
    }

    function paneFocusables() {
        var pane = document.getElementById("inspector-pane");
        if (!pane) return [];
        return Array.prototype.slice.call(pane.querySelectorAll(FOCUSABLE));
    }

    /* Called just before a commit. `nextEl === input` means the commit came
       from Enter (focus stays put); anything else is where focus was heading
       when the commit fired (Tab / click-away). Three restore modes:
         key   — put focus back on the SAME field (Enter),
         index — Tab moved on inside the pane: the re-render rebuilds the same
                 structure, so the n-th focusable is the same control,
         none  — focus left the pane entirely: restore the scroll, never yank
                 focus back out of wherever the user went. */
    function remember(input, nextEl) {
        if (!input || !input.closest) return;
        var mode = "none", key = null, idx = -1, caret = null;
        if (nextEl === input) {
            mode = "key";
            key = fieldKey(input);
            try { caret = input.selectionStart; } catch (e) { caret = null; }
        } else if (nextEl && nextEl.nodeType === 1) {
            var pane = document.getElementById("inspector-pane");
            if (pane && pane.contains(nextEl)) {
                idx = paneFocusables().indexOf(nextEl);
                if (idx >= 0) mode = "index";
            }
        }
        var sc = scrollerOf(input);
        userMoved = false;
        pending = { mode: mode, key: key, idx: idx, caret: caret,
                    scroller: sc, scrollTop: sc ? sc.scrollTop : null,
                    ts: Date.now() };
    }

    function restore(finalPass) {
        if (!pending) return;
        if (Date.now() - pending.ts > RESTORE_TTL_MS) { pending = null; return; }
        var p = pending;
        if (!userMoved && p.scroller && p.scroller.isConnected && p.scrollTop != null
            && Math.abs(p.scroller.scrollTop - p.scrollTop) > 2) {
            p.scroller.scrollTop = p.scrollTop;
        }
        // Only ever restore focus the SWAP took away (it lands on <body>);
        // never steal focus the user has since moved somewhere real.
        var a = document.activeElement;
        if (p.mode !== "none" && (!a || a === document.body || a.tagName === "HTML")) {
            var el = p.mode === "key" ? findByKey(p.key) : (paneFocusables()[p.idx] || null);
            if (el) {
                try { el.focus({ preventScroll: true }); } catch (e) { }
                if (p.mode === "key" && el.setSelectionRange) {
                    try {
                        var pos = p.caret == null ? el.value.length
                                                  : Math.min(p.caret, el.value.length);
                        el.setSelectionRange(pos, pos);
                    } catch (e) { /* non-text inputs have no selection range */ }
                }
            }
        }
        if (finalPass) pending = null;
    }

    function afterSwap() {
        RESTORE_PASSES_MS.forEach(function (ms, i) {
            var last = i === RESTORE_PASSES_MS.length - 1;
            if (ms === 0) { restore(last); return; }
            setTimeout(function () { restore(last); }, ms);
        });
    }

    function noteUserScroll() { userMoved = true; }
    /* A click/tap means the user has taken over — drop the pending restore
       entirely, so a commit followed by opening ANOTHER pulse can never land
       focus in the new pulse's same-named field (they would then be typing
       into a parameter they never chose to edit). Ordering is safe: mousedown
       fires BEFORE the focusout that records a click-away commit, so the
       commit's own remember() still wins. */
    function noteUserTakeover() { pending = null; userMoved = true; }
    document.addEventListener("wheel", noteUserScroll, { passive: true });
    document.addEventListener("touchstart", noteUserTakeover, { passive: true });
    document.addEventListener("mousedown", noteUserTakeover, true);
    document.addEventListener("keydown", function (e) {
        if (e.key === "PageUp" || e.key === "PageDown"
            || e.key === "Home" || e.key === "End") userMoved = true;
    }, true);

    return { inFlight: inFlight, remember: remember, restore: restore,
             afterSwap: afterSwap, _key: fieldKey, _find: findByKey,
             _focusables: paneFocusables,
             _pending: function () { return pending; } };
})();

document.addEventListener("htmx:beforeRequest", function (evt) {
    var elt = evt.detail && evt.detail.elt;
    if (elt && elt.matches && elt.matches("form.inline-edit") && elt.dataset) {
        elt.dataset.committing = "1";
    }
});
document.addEventListener("htmx:afterRequest", function (evt) {
    var elt = evt.detail && evt.detail.elt;
    if (elt && elt.dataset && elt.dataset.committing) delete elt.dataset.committing;
});
document.addEventListener("htmx:afterSettle", function (evt) {
    if (evt.target && evt.target.id === "inspector-pane") window.InlineCommit.afterSwap();
});

/* An htmx-owned form must NEVER perform the browser's native submission.
   htmx prevents the default action whenever it issues the request — but when
   it DECLINES one (a duplicate while the same form is in flight is dropped by
   hx-sync's default) the event's default action survives, and the browser then
   navigates to the current URL with the form fields as a query string. On
   Pulses that showed up as the "Leave site?" prompt (or, with nothing unsaved,
   a silent full-page reload that closed the inspector). Bubble phase: htmx's
   own listener has already run, so this only ever covers the declined case. */
document.addEventListener("submit", function (evt) {
    if (!window.htmx) return;          // no htmx ⇒ native submit is the fallback
    var f = evt.target;
    if (!f || !f.getAttribute) return;
    if (f.getAttribute("hx-post") || f.getAttribute("hx-get")
        || f.getAttribute("data-hx-post") || f.getAttribute("data-hx-get")) {
        evt.preventDefault();
    }
});

/* Enter commits the field it is typed in — remember where focus (and the
   panel's scroll) must come back to once the re-render lands. Capture phase:
   this must run before htmx turns the implicit submission into a request. */
document.addEventListener("keydown", function (evt) {
    if (evt.key !== "Enter") return;
    var input = evt.target;
    if (!input || !input.matches || !input.matches('form.inline-edit input[name="value"]')) return;
    var form = input.closest("form");
    if (form && window.InlineCommit.inFlight(form)) return;
    window.InlineCommit.remember(input, input);
}, true);

// Tab / click-away COMMITS the inline-edit forms (Pulses detail, Qubit/Pair
// inspector) like Enter. These forms re-render #inspector-pane on commit — same
// as Enter — so tabbing to the next field re-renders the pane; the value is still
// committed (the reported pain: "clicked away, my edit was lost").
// The baseline guard skips unchanged values (and Escape-restores) so a bare
// click-away with no edit is a no-op — the server never no-ops set_value.
//
// The in-flight guard is load-bearing, not defensive: Enter's OWN response
// swaps #inspector-pane, and removing the focused input fires focusout on it
// with the typed (≠ data-committed) value still in place. Re-submitting from
// there double-commits AND — because htmx drops the duplicate without
// preventing the default action — hands the browser a native form submission.
document.addEventListener("focusout", function(evt) {
    var input = evt.target;
    if (!input || !input.matches
        || !input.matches('form.inline-edit input[name="value"]')) return;
    var form = input.closest("form");
    if (!form || !form.isConnected || !form.requestSubmit) return;
    if (window.InlineCommit.inFlight(form)) return;   // the swap's own focusout
    var baseline = input.hasAttribute("data-committed")
        ? input.getAttribute("data-committed") : input.defaultValue;
    if (input.value === baseline) return;   // unchanged → don't commit/reswap
    window.InlineCommit.remember(input, evt.relatedTarget);
    form.requestSubmit();
});

// Global Ctrl/⌘+Z → undo the last in-SM modification. Tiered (docs/20 v2):
//   1. Generate-Config wizard (when mounted — wizard-scoped, never chip edits)
//   2. LiveEditUndo — un-staged grid fills/typing (in-memory, value-level)
//   3. server /undo — staged edits, one change_log GROUP per press; the
//      response swaps the whole Review tray, so one press = exactly one
//      event's parameters disappearing from Review (the sync contract).
// docs/107: the save/apply boundary is no longer the end of the chain — with
// the log empty the server walks the cross-save journal, STAGING each older
// unit's inverse back into the tray (live untouched; Apply stays the gate).
// Ctrl+Shift+Z mirrors the chain as redo (wizard swallow → LiveEditUndo
// tryRedo → server /redo). Ctrl+Y is deliberately unbound (native in-field
// redo keeps working).
// Input focus: we do NOT hijack Ctrl(+Shift)+Z inside ordinary fields (native
// text-undo keeps working) — EXCEPT bulk-grid cells and the Column History
// panel, where LiveEditUndo owns the history (Escape still restores a
// cell's original value).
//
// The carve-out keys on the Column History panel's OWN class, not on the
// shared `.ch-overlay` shell. Four dialogs reuse that shell (Column History,
// the type-fix repair dialog, the env-schema dialog, and the FSP compensation
// popup) and only Column History wants LiveEditUndo to own the keystroke. When
// docs/120 item 7 made the FSP amplitudes editable, a `.ch-overlay` test meant
// Ctrl+Z on a typo in an amplitude field skipped the native-undo bail-out and
// fell through to the app-wide chain — silently restoring a grid cell hidden
// behind the modal, or POSTing /undo to discard a staged group.
/* docs/122 item 3 — the server tier is a QUEUE, because presses were being
   dropped, not raced.
   Measured on the real 20-qubit chip: ten Ctrl+Z presses all reached the server
   tier (window._lastUndoTier read 'server' ten times) and produced only FOUR
   htmx:beforeRequest events, three of which completed. htmx keeps per-source
   request bookkeeping and discards a second request from #pending-tray while
   one is in flight, so six presses did nothing and said nothing — which is the
   "unstable / it goes back and forth" half of the report. Peak concurrency was
   1 and the tray count never went backwards, so serialisation was never the
   missing piece; not throwing the presses away is.

   Undo and redo share one queue so an interleaved burst is applied in the order
   it was typed. The bound is a held key, not a rate limit: past it we stop
   accepting rather than silently discarding somewhere in the middle. */
window.UndoQueue = (function () {
    var MAX = 20;
    var q = [], busy = false, waiting = false, _fullToastAt = 0;
    /* The queue's OWN htmx sync source (docs/124 M-11). It must NOT be the
       tray: htmx 2.0.4's per-element sync (default strategy "last") lives on
       the SOURCE element, and both the grid ⚡ apply (applyEditsToLive) and
       the armed auto-apply flush issue from "#pending-tray" — a /undo queued
       behind their in-flight request was REPLACED in htmx's queuedRequests,
       the pump's promise resolved instantly, and the lone survivor re-issued
       against the tray element the apply's own swap had detached, dying on
       htmx's isConnected guard. Executed on the real chip: 3 presses in an
       apply window → 0 POST /undo, no toast — the original customer symptom
       reintroduced through a side door, chronic under an armed auto-apply
       session (every commit triggers a flush). A body-level element that no
       response ever swaps has its own sync lane; the events htmx raises from
       the HX-Trigger header bubble from it to document, where the
       cellsReverted listener lives. */
    function src() {
        var s = document.getElementById("undo-sync-src");
        if (!s) {
            s = document.createElement("div");
            s.id = "undo-sync-src";
            s.style.display = "none";
            document.body.appendChild(s);
        }
        return s;
    }
    function pump() {
        if (busy || !q.length || !window.htmx) return;
        /* An apply (manual ⚡ or an auto-apply flush) is mid-write: HOLD the
           press, never race it and never drop it. Ordered execution is also
           the docs/107 model — an undo pressed during an apply lands after
           it, walking the journal the apply just wrote. */
        if (window._applyInFlight) {
            if (!waiting) {
                waiting = true;
                setTimeout(function () { waiting = false; pump(); }, 120);
            }
            return;
        }
        var path = q.shift();
        if (!document.getElementById("pending-tray")) { q.length = 0; return; }
        busy = true;
        var done = function () { busy = false; pump(); };
        var r;
        // A throw here (htmx torn down mid-navigation) must not wedge the queue
        // for the rest of the session — releasing on the spot is the only
        // behaviour that cannot strand the presses still waiting behind it.
        try {
            r = htmx.ajax("POST", path, {
                source: src(), target: "#pending-tray", swap: "outerHTML",
            });
        } catch (e) { done(); return; }
        /* A /undo that never settles used to hold `busy` FOREVER — every
           later press queued silently behind it for the rest of the session
           (docs/124 minor: no timeout at any layer). Releasing busy alone
           would just queue the next request behind the wedged one inside
           htmx's own sync lane, so a timeout gives up HONESTLY: drop the
           queue, say so. A response that still lands later swaps the tray
           normally — nothing is corrupted, the user just pressed again. */
        var guard = setTimeout(function () {
            if (!busy) return;
            busy = false;
            q.length = 0;
            if (window.showToast) window.showToast(
                "Undo is not responding — the press was abandoned. "
                + "Check the server and try again.", "error");
        }, 20000);
        var settle = function () { clearTimeout(guard); done(); };
        if (r && typeof r.then === "function") r.then(settle, settle);
        else settle();   // no completion signal ⇒ never hold the lock on a guess
    }
    return {
        push: function (path) {
            if (!window.htmx || !document.getElementById("pending-tray")) return false;
            if (q.length >= MAX) {
                // The refusal used to be COMPLETELY invisible — preventDefault
                // had already fired and the return value was discarded, the
                // same silence the queue was built to end (docs/124 minor).
                // Throttled: a held key reaches this ~30x/s.
                var now = Date.now();
                if (now - _fullToastAt > 1500 && window.showToast) {
                    _fullToastAt = now;
                    window.showToast("Undo queue is full (" + MAX
                        + " pending) — release the key.", "warning");
                }
                return false;
            }
            q.push(path);
            pump();
            return true;
        },
        depth: function () { return q.length; },
        busy: function () { return busy; },
    };
})();

document.addEventListener("keydown", function(evt) {
    if (!((evt.ctrlKey || evt.metaKey) && (evt.key === "z" || evt.key === "Z")
          && !evt.altKey)) return;
    var a = document.activeElement;
    var inGridCell = !!(a && a.classList && a.classList.contains("bulk-cell"));
    var inChPanel = !!(a && a.closest && a.closest(".colhist-overlay"));
    if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable)
        && !inGridCell && !inChPanel) return;
    if (evt.shiftKey) {
        // ---- redo chain (docs/107) ----
        // Wizard MOUNTED → swallow (the wizard has no redo; letting the press
        // fall through to chip-level redo would act behind the user's back).
        // NB: window._wizUndo exists on EVERY page — only its mounted() probe
        // says whether the wizard is actually on screen (real-browser catch).
        if (window._wizUndo && window._wizUndo.mounted
            && window._wizUndo.mounted()) { evt.preventDefault(); return; }
        // Server ops in flight or queued: redo must join THAT order, not
        // answer from the client stack first — Ctrl+Shift+Z during an
        // in-flight server undo used to re-apply an older client action
        // instead of redoing the press it chased (docs/124 minor).
        if (window.UndoQueue && (window.UndoQueue.busy() || window.UndoQueue.depth() > 0)
            && window.htmx && document.getElementById("pending-tray")) {
            evt.preventDefault();
            window.UndoQueue.push("/redo");
            return;
        }
        if (window.LiveEditUndo && window.LiveEditUndo.tryRedo()) { evt.preventDefault(); return; }
        if (!window.htmx || !document.getElementById("pending-tray")) return;
        evt.preventDefault();
        window.UndoQueue.push("/redo");
        return;
    }
    // ---- undo chain ----
    // Generate-Config wizard mounted → Ctrl+Z is WIZARD-scoped (undoes the last
    // committed wizard field, never a chip edit behind the user's back).
    if (window._wizUndo && window._wizUndo.tryUndo()) { evt.preventDefault(); window._lastUndoTier = "wizard"; return; }
    if (window.LiveEditUndo && window.LiveEditUndo.tryUndo()) { evt.preventDefault(); window._lastUndoTier = "liveedit"; return; }
    // audit-r10: mid-typing in a DIRTY cell with an empty in-memory stack —
    // the user means "undo my keystrokes", not "delete a staged group".
    // Restore the cell to its committed value and stop; a CLEAN focused
    // cell falls through to the server tier as designed.
    if (inGridCell && a.value !== a.getAttribute("data-orig")
        && a.getAttribute("data-orig") !== null) {
        evt.preventDefault();
        a.value = a.getAttribute("data-orig");
        a.dispatchEvent(new Event("input", { bubbles: true }));
        return;
    }
    if (!window.htmx || !document.getElementById("pending-tray")) return;
    evt.preventDefault();
    window._lastUndoTier = "server";
    window.UndoQueue.push("/undo");
}, true);

function _revertCell(dotPath, oldValueStr) {
    // Revert inspector cell
    var hidden = document.querySelector(
        'input[type="hidden"][name="dot_path"][value="' + dotPath + '"]'
    );
    if (hidden) {
        var form = hidden.parentElement;
        var input = form.querySelector('input[name="value"]');
        if (input) {
            input.value = oldValueStr;
            input.classList.remove("edit-input-modified");
            input.removeAttribute("title");
        }
        var td = form.closest("td");
        if (td) {
            td.classList.remove("cell-modified");
            td.removeAttribute("title");
        }
    }
    // Revert Explorer tree node
    window._revertTreeNode && window._revertTreeNode(dotPath, oldValueStr);
}

/* ------------------------------------------------------------------ */
/* Compare tab switcher                                                */
/* ------------------------------------------------------------------ */

/**
 * Toggle the active class on compare tabs.  For the "Differences" tab
 * (which is inlined on first load), we also swap #compare-content back
 * to its original diff HTML.  State tabs use hx-get for lazy loading,
 * so HTMX handles the content swap automatically.
 */
window.switchCompareTab = function(el) {
    var bar = el.closest(".compare-tab-bar");
    if (!bar) return;
    var tabs = bar.querySelectorAll(".compare-tab");
    for (var i = 0; i < tabs.length; i++) {
        tabs[i].classList.remove("active");
    }
    el.classList.add("active");
};

/* ------------------------------------------------------------------ */
/* Table filter                                                        */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* Path autocomplete                                                    */
/* ------------------------------------------------------------------ */

window.initPathAutocomplete = function(inputEl) {
    var timer = null;
    var box = document.createElement("div");
    box.className = "path-suggestions";
    inputEl.parentNode.style.position = "relative";
    inputEl.parentNode.appendChild(box);
    var activeIdx = -1;

    function hide() { box.innerHTML = ""; box.style.display = "none"; activeIdx = -1; }

    function show(items) {
        if (!items || items.length === 0) { hide(); return; }
        box.innerHTML = "";
        activeIdx = -1;
        for (var i = 0; i < items.length; i++) {
            var div = document.createElement("div");
            div.className = "path-suggestion";
            div.textContent = items[i];
            div.setAttribute("data-path", items[i]);
            div.addEventListener("mousedown", function(e) {
                e.preventDefault();
                inputEl.value = this.getAttribute("data-path");
                hide();
                inputEl.dispatchEvent(new Event("input"));
            });
            box.appendChild(div);
        }
        box.style.display = "block";
    }

    function highlight(idx) {
        var items = box.querySelectorAll(".path-suggestion");
        for (var i = 0; i < items.length; i++) {
            items[i].classList.toggle("active", i === idx);
        }
        activeIdx = idx;
    }

    inputEl.addEventListener("input", function() {
        clearTimeout(timer);
        var val = inputEl.value.trim();
        if (!val) { hide(); return; }
        timer = setTimeout(function() {
            // complete=1: the autocomplete wants prefix-completions of the
            // half-typed last segment; the folder-browser DIALOG never sends
            // it (it gets ancestor-walk semantics instead — see /browse).
            fetch("/browse?complete=1&path=" + encodeURIComponent(val))
                .then(function(r) { return r.json(); })
                .then(function(data) { show(data.dirs || []); })
                .catch(function() { hide(); });
        }, 250);
    });

    inputEl.addEventListener("keydown", function(e) {
        var items = box.querySelectorAll(".path-suggestion");
        if (!items.length) return;
        if (e.key === "ArrowDown") {
            e.preventDefault();
            highlight(Math.min(activeIdx + 1, items.length - 1));
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            highlight(Math.max(activeIdx - 1, 0));
        } else if (e.key === "Enter" && activeIdx >= 0) {
            e.preventDefault();
            inputEl.value = items[activeIdx].getAttribute("data-path");
            hide();
        } else if (e.key === "Escape") {
            hide();
        }
    });

    inputEl.addEventListener("blur", function() {
        setTimeout(hide, 200);
    });
};

/* ------------------------------------------------------------------ */
/* Folder browser modal                                                */
/* ------------------------------------------------------------------ */

(function() {
    var _targetInputId = null;
    var _currentPath = "";      // ONLY ever a successfully-listed folder
    var _lastGoodPath = "";     // "Go back" target after a failed navigation
    var _navSeq = 0;            // monotonic token — stale responses drop
    var _RECENT_KEY = "recentFolders";
    var _RECENT_MAX = 10;
    var _LAST_PATH_PREFIX = "quam_folder_last:";   // per-target-input memory
    var _FETCH_TIMEOUT_MS = 8000;

    // fetch with an abort timeout; resolves {ok, data} or rejects with a
    // typed reason ("timeout" / "network" / "http <code>") so the dialog can
    // say WHY it failed instead of hanging silently.
    function _browserFetch(url, opts) {
        var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
        var timer = ctrl && setTimeout(function() { ctrl.abort(); }, _FETCH_TIMEOUT_MS);
        var o = opts || {};
        if (ctrl) o.signal = ctrl.signal;
        return fetch(url, o)
            .then(function(r) {
                if (!r.ok) throw new Error("http " + r.status);
                return r.json();
            })
            .catch(function(e) {
                throw new Error(
                    e && e.name === "AbortError" ? "timeout"
                        : (e && e.message && e.message.indexOf("http ") === 0)
                            ? e.message : "network");
            })
            .finally(function() { if (timer) clearTimeout(timer); });
    }

    function _rememberLastPath(path) {
        if (!_targetInputId || !path) return;
        try { localStorage.setItem(_LAST_PATH_PREFIX + _targetInputId, path); }
        catch(e) { /* private mode — memory just won't persist */ }
    }
    function _recallLastPath(targetInputId) {
        try { return localStorage.getItem(_LAST_PATH_PREFIX + targetInputId) || ""; }
        catch(e) { return ""; }
    }

    function _getRecentFolders() {
        try { return JSON.parse(localStorage.getItem(_RECENT_KEY) || "[]"); }
        catch(e) { return []; }
    }

    function _addRecentFolder(path) {
        if (!path) return;
        var list = _getRecentFolders().filter(function(p) { return p !== path; });
        list.unshift(path);
        if (list.length > _RECENT_MAX) list = list.slice(0, _RECENT_MAX);
        try { localStorage.setItem(_RECENT_KEY, JSON.stringify(list)); } catch(e) {}
    }

    function _renderRecentFolders() {
        var container = document.getElementById("browser-recent-list");
        if (!container) return;
        var list = _getRecentFolders();
        container.innerHTML = "";
        if (list.length === 0) {
            container.innerHTML = '<div class="browser-empty">No recent folders</div>';
            return;
        }
        for (var i = 0; i < list.length; i++) {
            (function(path) {
                var row = document.createElement("div");
                row.className = "browser-recent-item";
                row.textContent = path;
                row.title = path;
                row.onclick = function() { navigateBrowser(path); };
                container.appendChild(row);
            })(list[i]);
        }
    }

    var _browseKind = "";   // "" = quam-state highlighting; "dataset" = run folders
    var _browseNoSubmit = false;   // r12: Select fills without submitting the form

    window.openFolderBrowser = function(targetInputId, kind, opts) {
        _targetInputId = targetInputId;
        // r12: opts.autoSubmit === false keeps Select as a pure fill — the
        // name-prompt's Browse must not submit the whole identity form on
        // folder pick (the dangling-fix form WANTS the auto-submit).
        _browseNoSubmit = !!(opts && opts.autoSubmit === false);
        // What the caller is hunting decides what the dialog highlights:
        // dataset pickers mark run folders (node.json/data.json), everything
        // else keeps the quam_state highlighting.
        _browseKind = kind === "dataset" ? "dataset" : "";
        var dialog = document.getElementById("folder-browser");
        if (!dialog) return;
        var input = document.getElementById(targetInputId);
        // Start-path precedence: the input's current value → the last folder
        // successfully browsed FOR THIS INPUT (localStorage) → server default
        // (drive list on Windows, $HOME on POSIX). Per-input keying keeps the
        // state-folder picker and the generate-output picker independent.
        var startPath = (input && input.value.trim())
            ? input.value.trim()
            : _recallLastPath(targetInputId);
        dialog.showModal();
        _renderRecentFolders();
        navigateBrowser(startPath);
    };

    window.navigateBrowser = function(path) {
        var seq = ++_navSeq;    // newer navigations obsolete this one
        var list = document.getElementById("browser-list");
        var pathInput = document.getElementById("browser-selected-path");
        if (pathInput) pathInput.value = path;   // optimistic — reverted on failure
        if (list) list.innerHTML = '<div class="browser-empty browser-loading">Loading…</div>';

        function renderFailure(reason) {
            if (seq !== _navSeq || !list) return;
            var msg = reason === "timeout" ? "Timed out reading the folder"
                : reason === "network" ? "Could not reach the app"
                : "Unable to read the folder (" + reason + ")";
            list.innerHTML = "";
            var row = document.createElement("div");
            row.className = "browser-empty browser-error";
            row.textContent = msg + ".";
            list.appendChild(row);
            var retry = document.createElement("button");
            retry.type = "button";
            retry.className = "outline btn-sm";
            retry.textContent = "Retry";
            retry.onclick = function() { navigateBrowser(path); };
            list.appendChild(retry);
            if (_lastGoodPath && _lastGoodPath !== path) {
                var back = document.createElement("button");
                back.type = "button";
                back.className = "outline btn-sm";
                back.textContent = "Go back";
                back.onclick = function() { navigateBrowser(_lastGoodPath); };
                list.appendChild(back);
            }
            // _currentPath was NOT updated — the selected path reverts to the
            // last folder that actually listed, so Select/mkdir can't act on
            // a folder we never reached.
            if (pathInput) pathInput.value = _currentPath;
            var failSelBtn = document.getElementById("browser-select-btn");
            if (failSelBtn) failSelBtn.disabled = false;   // value is a good path again
        }

        // Defense-in-depth: a bare drive token ("D:") is CWD-relative on
        // Windows — normalize to the drive ROOT before it reaches the server.
        if (/^[A-Za-z]:$/.test(path)) path = path + "\\";

        _browserFetch("/browse?path=" + encodeURIComponent(path) +
                      (_browseKind ? "&kind=" + _browseKind : ""))
            .then(function(data) {
                if (seq !== _navSeq) return;     // a newer navigation won
                if (data.error) {
                    // Server saw the folder but couldn't read it
                    // (permission / IO) — same failure surface.
                    renderFailure(data.error);
                    return;
                }
                // data.path is ALWAYS the folder the server actually listed
                // (ancestor-walk semantics for dead paths) — breadcrumbs and
                // the selected path must mirror it, never the request.
                // EXCEPT a dead-end response (relative junk / no surviving
                // ancestor): the server echoes the request back (path ===
                // missing, nothing listed). That is NOT a browsable folder —
                // never remember it as last-good, and Select must not offer
                // a folder that does not exist.
                var deadEnd = !!data.missing && data.missing === data.path;
                if (!deadEnd) {
                    _currentPath = data.path || path;
                    _lastGoodPath = _currentPath;
                    _rememberLastPath(_currentPath);
                }
                var selBtn = document.getElementById("browser-select-btn");
                if (selBtn) selBtn.disabled = deadEnd;
                if (pathInput) {
                    pathInput.value = deadEnd ? (data.path || path)
                                              : (_currentPath || path);
                }
                renderBreadcrumbs(data.path || path);
                renderFolderList(data);
                if (data.missing) {
                    // A stale Recent entry / deleted folder: we landed at the
                    // nearest existing ancestor — say so instead of silently
                    // showing a different folder.
                    var note = document.createElement("div");
                    note.className = "browser-empty browser-missing-note";
                    note.textContent = "“" + data.missing + "” was not " +
                        "found — showing the nearest existing folder.";
                    list.prepend(note);
                }
            })
            .catch(function(e) { renderFailure(e && e.message || "network"); });
    };

    function renderBreadcrumbs(pathStr) {
        var container = document.getElementById("browser-breadcrumbs");
        if (!container) return;
        container.innerHTML = "";

        if (!pathStr) {
            var root = document.createElement("span");
            root.className = "breadcrumb-item";
            root.textContent = "Computer";
            container.appendChild(root);
            return;
        }

        var rootBtn = document.createElement("span");
        rootBtn.className = "breadcrumb-item breadcrumb-link";
        rootBtn.textContent = "Computer";
        rootBtn.onclick = function() { navigateBrowser(""); };
        container.appendChild(rootBtn);

        // Portable crumb paths. The old builder joined every part with "\\"
        // and dropped the leading "/", so POSIX crumbs navigated to garbage
        // ("home\\user"). Detect the path style and rebuild each prefix in it:
        //   POSIX     /home/user/x   → /home, /home/user, …
        //   Drive     C:\Users\x     → C:\, C:\Users, …
        //   UNC       \\srv\share\x  → \\srv\share, \\srv\share\x (server+share
        //                              are one navigable unit)
        var isUNC = /^\\\\/.test(pathStr);
        // Style from the LEADING pattern ONLY ("C:…" / "\\\\server") — a POSIX
        // path containing a backslash inside a FILENAME used to flip the whole
        // path to Windows splitting, corrupting every crumb (each click
        // navigated to garbage).
        var isWin = isUNC || /^[A-Za-z]:/.test(pathStr);
        // POSIX absolute paths get an explicit "/" crumb right after Computer:
        // "Computer" is the server's start listing ($HOME on POSIX), so the
        // filesystem root needs its own truthful, clickable crumb.
        if (!isWin && pathStr.charAt(0) === "/") {
            var sep0 = document.createElement("span");
            sep0.className = "breadcrumb-sep";
            sep0.textContent = " > ";
            container.appendChild(sep0);
            var rootSlash = document.createElement("span");
            rootSlash.className = "breadcrumb-item breadcrumb-link";
            rootSlash.textContent = "/";
            rootSlash.setAttribute("data-path", "/");
            rootSlash.onclick = function() { navigateBrowser("/"); };
            container.appendChild(rootSlash);
        }
        // POSIX-classified paths split on "/" ALONE — "\" is a legal filename
        // character there, never a separator.
        var parts = (isWin ? pathStr.split(/[\\/]/) : pathStr.split("/"))
            .filter(function(p) { return p; });
        if (isUNC && parts.length >= 2) {
            // \\server\share is the smallest navigable UNC unit — one crumb.
            parts = ["\\\\" + parts[0] + "\\" + parts[1]].concat(parts.slice(2));
        }
        function crumbPath(i) {
            if (!isWin) return "/" + parts.slice(0, i + 1).join("/");
            if (!isUNC && i === 0 && parts[0].indexOf(":") >= 0) return parts[0] + "\\";
            return parts.slice(0, i + 1).join("\\");
        }
        for (var i = 0; i < parts.length; i++) {
            var built = crumbPath(i);
            var arrow = document.createElement("span");
            arrow.className = "breadcrumb-sep";
            arrow.textContent = " > ";
            container.appendChild(arrow);

            var crumb = document.createElement("span");
            crumb.setAttribute("data-path", built);
            if (i < parts.length - 1) {
                crumb.className = "breadcrumb-item breadcrumb-link";
                crumb.onclick = function() { navigateBrowser(this.getAttribute("data-path")); };
            } else {
                crumb.className = "breadcrumb-item breadcrumb-current";
            }
            crumb.textContent = parts[i].replace(/^\\\\/, "");
            container.appendChild(crumb);
        }
    }

    function renderFolderList(data) {
        var container = document.getElementById("browser-list");
        if (!container) return;
        container.innerHTML = "";

        if (data.path) {
            var up = document.createElement("div");
            up.className = "browser-folder browser-up";
            up.textContent = ".. (up)";
            up.onclick = function() { navigateBrowser(data.parent || ""); };
            container.appendChild(up);
        }

        var dirs = data.dirs || [];
        if (dirs.length === 0 && !data.parent) {
            container.innerHTML = '<div class="browser-empty">No subdirectories</div>';
            return;
        }

        // In dataset mode the server marks which children ARE dataset runs
        // (node.json / data.json) — highlight those; in state mode keep the
        // classic quam_state highlighting. Set-lookup for O(1) per row.
        var dsMarks = {};
        (data.dataset_dirs || []).forEach(function(d) { dsMarks[d] = true; });

        for (var i = 0; i < dirs.length; i++) {
            var row = document.createElement("div");
            row.className = "browser-folder";
            row.setAttribute("data-path", dirs[i]);

            var dirPath = dirs[i];
            // Same LEADING-pattern style classification as the breadcrumbs —
            // a POSIX folder name containing "\" must not be chopped at it.
            var isWinChild = /^[A-Za-z]:/.test(dirPath) || /^\\\\/.test(dirPath);
            var name = (isWinChild ? dirPath.split(/[\\/]/) : dirPath.split("/"))
                .filter(function(s) { return s; }).pop() || dirPath;
            row.textContent = name;

            if (_browseKind === "dataset") {
                if (dsMarks[dirPath]) {
                    row.classList.add("is-dataset");
                    row.title = "Contains dataset files (node.json / data.json)";
                }
            } else if (name === "quam_state") {
                // Highlight a CHILD only when it is itself a quam_state folder.
                // data.has_quam_state describes the CURRENT (parent) folder, so
                // OR-ing it here painted every child as a quam folder whenever
                // the parent held state.json.
                row.classList.add("is-quam");
            }

            row.onclick = function() {
                navigateBrowser(this.getAttribute("data-path"));
            };
            container.appendChild(row);
        }

        if (data.truncated) {
            // The server capped the listing — say so instead of silently
            // hiding the rest of a big archive.
            var trunc = document.createElement("div");
            trunc.className = "browser-empty browser-truncated-note";
            trunc.textContent = "Showing first " + dirs.length + " of " +
                (data.total || dirs.length) + " folders — type a path to narrow.";
            container.appendChild(trunc);
        }

        if (_browseKind === "dataset") {
            if (data.has_dataset) {
                var dsBadge = document.createElement("div");
                dsBadge.className = "browser-quam-badge";
                dsBadge.textContent = "This folder contains dataset files (node.json / data.json)";
                container.prepend(dsBadge);
            }
        } else {
            if (data.has_quam_state) {
                var badge = document.createElement("div");
                badge.className = "browser-quam-badge";
                badge.textContent = "This folder contains state.json + wiring.json";
                container.prepend(badge);
            }
            if (data.has_experiment_children) {
                var badge2 = document.createElement("div");
                badge2.className = "browser-quam-badge";
                badge2.textContent = "Contains experiment subfolders";
                container.prepend(badge2);
            }
        }
    }

    window.selectBrowserFolder = function() {
        var pathInput = document.getElementById("browser-selected-path");
        var target = _targetInputId ? document.getElementById(_targetInputId) : null;
        if (target && pathInput && pathInput.value) {
            target.value = pathInput.value;
            _addRecentFolder(pathInput.value);
            // Programmatic .value assignment never fires `change` — targets
            // that live OUTSIDE a form (chip-compare, compare-hub) listen on
            // onchange and were silently dead without this dispatch.
            target.dispatchEvent(new Event("change", { bubbles: true }));
        }
        var dialog = document.getElementById("folder-browser");
        if (dialog) dialog.close();
        if (target && !_browseNoSubmit) {
            var form = target.closest("form");
            if (form) form.requestSubmit();
        }
    };

    // --- create a new folder inside the current one -----------------------------
    window.toggleNewFolder = function() {
        var row = document.getElementById("browser-newfolder-row");
        if (!row) return;
        row.hidden = !row.hidden;
        var err = document.getElementById("browser-newfolder-err");
        if (err) err.textContent = "";
        if (!row.hidden) {
            var inp = document.getElementById("browser-newfolder-name");
            if (inp) { inp.value = ""; inp.focus(); }
        }
    };

    var _mkdirInFlight = false;   // double-submit guard (Enter + click race)

    window.createBrowserFolder = function() {
        var nameInp = document.getElementById("browser-newfolder-name");
        var err = document.getElementById("browser-newfolder-err");
        var name = nameInp ? nameInp.value.trim() : "";
        if (err) err.textContent = "";
        if (!name) { if (err) err.textContent = "Enter a name."; return; }
        // _currentPath is "" at the Computer/drive-list root — can't mkdir there.
        if (!_currentPath) { if (err) err.textContent = "Open a folder first."; return; }
        if (_mkdirInFlight) return;
        _mkdirInFlight = true;
        var body = "path=" + encodeURIComponent(_currentPath) + "&name=" + encodeURIComponent(name);
        _browserFetch("/mkdir", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: body,
        })
            .then(function(d) {
                if (d && d.ok) {
                    var row = document.getElementById("browser-newfolder-row");
                    if (row) row.hidden = true;
                    // Enter the new folder — it becomes the selected path, ready to Select.
                    navigateBrowser(d.path);
                } else {
                    if (err) err.textContent = (d && d.error) || "Could not create folder.";
                    // Re-list the current folder — the failure may mean our
                    // view of it is stale (deleted/unmounted underneath us).
                    navigateBrowser(_currentPath);
                }
            })
            .catch(function(e) {
                if (err) {
                    err.textContent = (e && e.message === "timeout")
                        ? "Timed out creating the folder." : "Could not create folder.";
                }
            })
            .finally(function() { _mkdirInFlight = false; });
    };
})();

/* ------------------------------------------------------------------ */
/* Table filter                                                        */
/* ------------------------------------------------------------------ */

function _splitQueryTokens(raw) {
    // Whitespace split that keeps "double-quoted" runs (quotes included) as one
    // token, so e.g. name:"power rabi" is a single removable pill that
    // round-trips back into the query unchanged.
    var out = [], cur = "", inQ = false;
    raw = raw || "";
    for (var i = 0; i < raw.length; i++) {
        var ch = raw[i];
        if (ch === '"') { inQ = !inQ; cur += ch; continue; }
        if (!inQ && /\s/.test(ch)) { if (cur) { out.push(cur); cur = ""; } continue; }
        cur += ch;
    }
    if (cur) out.push(cur);
    return out;
}

window.renderFilterTags = function(inputEl, containerEl) {
    if (!containerEl) return;
    var tokens = _splitQueryTokens(inputEl.value || "");
    containerEl.innerHTML = "";
    for (var i = 0; i < tokens.length; i++) {
        (function(idx) {
            var pill = document.createElement("span");
            pill.className = "filter-tag";
            pill.textContent = tokens[idx] + " ";
            var btn = document.createElement("button");
            btn.type = "button";
            btn.innerHTML = "&times;";
            btn.onclick = function() {
                var parts = _splitQueryTokens(inputEl.value || "");
                parts.splice(idx, 1);
                inputEl.value = parts.join(" ");
                if (window.autoGrowNote) autoGrowNote(inputEl);  // shrink back as pills go
                renderFilterTags(inputEl, containerEl);
                // 'input', matching the box's hx-trigger (docs/126 #20).
                htmx.trigger(inputEl, "input");
            };
            pill.appendChild(btn);
            containerEl.appendChild(pill);
        })(i);
    }
};

// The sidebar filter is an auto-grow <textarea>, so it grows by width-wrapping,
// NOT by Enter. Swallow Enter so it never injects a blank line (which would also
// flow into the hx-get `name` param). HTMX still filters on keyup. Delegated on
// document so it survives sidebar re-renders.
document.addEventListener('keydown', function(e) {
    if (e.target && e.target.id === 'sidebar-filter-input' && e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
    }
});

window.filterTable = function(inputEl, tableId) {
    var raw = (inputEl.value || "").toLowerCase().trim();
    _debounce('filter-' + tableId, function() {
        var terms = raw ? raw.split(/\s+/) : [];
        var table = document.getElementById(tableId);
        if (!table) return;

        var tbody = table.querySelector("tbody");
        if (!tbody) return;

        var rows = tbody.querySelectorAll("tr");
        var visible = 0;

        // Batch: read all text content first
        var texts = new Array(rows.length);
        for (var i = 0; i < rows.length; i++) {
            texts[i] = rows[i].textContent.toLowerCase();
        }

        // Batch: compute matches, then write display in one pass
        for (var i = 0; i < rows.length; i++) {
            var match = true;
            for (var j = 0; j < terms.length; j++) {
                if (texts[i].indexOf(terms[j]) === -1) { match = false; break; }
            }
            rows[i].style.display = match ? "" : "none";
            if (match) visible++;
        }

        var counter = document.getElementById(tableId + "-filter-count");
        if (counter) {
            counter.textContent = terms.length > 0
                ? visible + " of " + rows.length + " shown"
                : "";
        }
    }, 150);
};

/* ------------------------------------------------------------------ */
/* Inspector-panel search (qubit / pair detail)                        */
/* ------------------------------------------------------------------ */
/* Filters rows inside the inspector pane's `.qubit-detail` /          */
/* `.pair-detail` <article>. Space-separated tokens, AND semantics —   */
/* matches what filterTable does above, but spans multiple <details>   */
/* sections and includes section names + editable <input> values in    */
/* the haystack. The search bar lives in _inspector_header.html        */
/* (sibling of <article>); we locate the article via the stable        */
/* #inspector-pane container.                                           */

function _detailPanelArticle() {
    return document.querySelector(
        "#inspector-pane .qubit-detail, #inspector-pane .pair-detail"
    );
}

window.filterDetailPanel = function(inputEl) {
    var raw = (inputEl.value || "").toLowerCase().trim();
    _debounce('filter-detail-panel', function() {
        var article = _detailPanelArticle();
        if (!article) return;
        var terms = raw ? raw.split(/\s+/) : [];

        var sections = article.querySelectorAll("details.detail-section");
        var totalRows = 0;
        var visibleRows = 0;

        for (var s = 0; s < sections.length; s++) {
            var section = sections[s];
            var sumEl = section.querySelector("summary");
            var sectionName = (sumEl ? sumEl.textContent : "").toLowerCase();
            var rows = section.querySelectorAll(".prop-table tbody > tr");
            var sectionVisible = 0;

            for (var i = 0; i < rows.length; i++) {
                totalRows++;
                if (terms.length === 0) {
                    rows[i].style.display = "";
                    visibleRows++;
                    sectionVisible++;
                    continue;
                }
                // Haystack = row textContent + parent section name + every
                // input's typed value (editable cells render as <input>,
                // their .value isn't in textContent).
                var hay = rows[i].textContent.toLowerCase() + " " + sectionName;
                var inputs = rows[i].querySelectorAll("input");
                for (var k = 0; k < inputs.length; k++) {
                    hay += " " + (inputs[k].value || "").toLowerCase();
                }
                var matched = true;
                for (var j = 0; j < terms.length; j++) {
                    if (hay.indexOf(terms[j]) === -1) { matched = false; break; }
                }
                rows[i].style.display = matched ? "" : "none";
                if (matched) { visibleRows++; sectionVisible++; }
            }

            if (terms.length === 0) {
                // Empty query — show all sections; leave the user's collapse
                // state alone (don't force open) so manual collapses survive
                // a clear.
                section.style.display = "";
            } else if (sectionVisible === 0) {
                section.style.display = "none";
            } else {
                section.style.display = "";
                section.open = true;  // auto-open so matches aren't hidden
            }
        }

        // The "Generated Config" + "Wiring Ports" sections at the bottom of
        // _qubit_detail.html aren't part of .prop-table; hide them when
        // filtering so the search result feels coherent.
        var aux = article.querySelectorAll(":scope > details.detail-section");
        for (var a = 0; a < aux.length; a++) {
            // Skip ones we already touched (they have a .prop-table inside).
            if (aux[a].querySelector(".prop-table")) continue;
            aux[a].style.display = terms.length > 0 ? "none" : "";
        }

        // Header pieces: counter + clear button live in _inspector_header.html.
        var header = document.querySelector("#inspector-pane .detail-search");
        if (header) {
            var counter = header.querySelector(".detail-search-count");
            if (counter) {
                counter.textContent = terms.length > 0
                    ? visibleRows + " of " + totalRows + " shown"
                    : "";
            }
            var clearBtn = header.querySelector(".detail-search-clear");
            if (clearBtn) clearBtn.hidden = !inputEl.value;
        }
    }, 120);
};

window.clearDetailPanelSearch = function(btnEl) {
    var header = btnEl.closest(".detail-search");
    var input = header && header.querySelector(".detail-search-input");
    if (!input) return;
    input.value = "";
    window.filterDetailPanel(input);
    input.focus();
};

/* ------------------------------------------------------------------ */
/* JSON Tree Viewer                                                     */
/* ------------------------------------------------------------------ */

(function() {
    var _POINTER_RE = /^#(\/|\.\/|\.\.\/)/;

    function _isPointer(v) {
        return typeof v === "string" && _POINTER_RE.test(v);
    }

    function _typeOf(v) {
        if (v === null) return "null";
        if (Array.isArray(v)) return "array";
        return typeof v;
    }

    // LOSSLESS full-digit + thousands-comma — the JS mirror of units.group_digits.
    // Shows every stored digit (no e-notation precision loss) so a frequency reads
    // "5,075,187,484.52453". window-exposed so bulk-edit.js shares one formatter.
    function _groupDigits(v) {
        if (typeof v !== "number" || !isFinite(v)) return String(v);
        var s = String(v);                      // shortest round-tripping form
        if (s.indexOf("e") >= 0 || s.indexOf("E") >= 0) return s;  // exponential — leave
        var neg = s.charAt(0) === "-";
        if (neg) s = s.slice(1);
        var dot = s.indexOf(".");
        var intPart = dot >= 0 ? s.slice(0, dot) : s;
        var frac = dot >= 0 ? s.slice(dot) : "";
        intPart = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
        return (neg ? "-" : "") + intPart + frac;
    }
    window._groupDigits = _groupDigits;

    // ── Shared numeric-input enhancer ─────────────────────────────────────────
    // Lets users type thousands-comma numbers ("100,000,000" == "100000000"),
    // groups them on BLUR (never per-keystroke — zero caret risk, matches the
    // app's commit-time grouping), and auto-grows the box to fit its content via
    // the HTML `size` attr (mono+tabular ⇒ 1 glyph = 1ch). Reuses the SAME comma
    // rule the server uses (type_policy.py `parse_value` (grouped-number gate)), so a genuine
    // string ("MW,FEM", a pointer, "con/slot/port") is left untouched. Scientific
    // notation (1.2e9) is left verbatim — value identical, notation respected.
    window.NumberInput = (function () {
        var GROUPED = /^[+-]?\d[\d,]*(\.\d+)?$/;   // mirror type_policy._PLAIN_GROUPED_NUMBER (no exponent)
        function strip(s) {
            s = String(s == null ? "" : s).trim();
            return (s.indexOf(",") >= 0 && GROUPED.test(s)) ? s.replace(/,/g, "") : s;
        }
        function sizeFor(s) { return Math.max(4, (String(s == null ? "" : s).length) + 1); }
        function fit(el) { if (el && el.tagName === "INPUT") el.size = sizeFor(el.value || el.placeholder || ""); }
        // On blur: regroup a plain/grouped number to thousands-comma. Leaves
        // exponent strings (e/E) and non-numbers exactly as typed.
        function format(el) {
            if (!el || el.tagName !== "INPUT") return;
            var raw = String(el.value).trim();
            if (raw !== "" && GROUPED.test(raw) && !/[eE]/.test(raw)) {
                var n = +strip(raw);
                if (isFinite(n)) el.value = _groupDigits(n);
            }
            fit(el);
        }
        // attach() turns a numeric input into a comma-aware, auto-growing field.
        // Idempotent (guarded). Selects/text inputs should NOT be attached.
        function attach(el) {
            if (!el || el.tagName !== "INPUT" || el._numInput) return;
            el._numInput = true;
            if (el.type === "number") el.type = "text";   // a number input drops commas
            el.inputMode = "decimal"; el.autocomplete = "off";
            el.addEventListener("input", function () { fit(el); });
            el.addEventListener("blur", function () { format(el); });
            format(el);   // group any seeded value + fit on first render
        }
        return { strip: strip, sizeFor: sizeFor, fit: fit, format: format, attach: attach };
    })();

    // ── Purpose-built per-column drag-resize for plain JS-rendered tables ─────
    // The shared enhanceColumnResize flips table-layout:fixed on init, which would
    // kill size-attr auto-grow table-wide. So this stays table-layout:auto (auto-
    // grow live) until the FIRST drag, then freezes current widths to px + fixed —
    // manual control wins table-wide thereafter; double-click a handle reverts that
    // column to auto-fit, and once nothing is pinned the table returns to auto-grow.
    window.armPlainResize = function (tableId, storageKey) {
        var table = document.getElementById(tableId);
        if (!table || table._plainResizeArmed) return;
        table._plainResizeArmed = true;
        var saved = {};
        try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}") || {}; } catch (e) { saved = {}; }
        function persist() { try { localStorage.setItem(storageKey, JSON.stringify(saved)); } catch (e) {} }
        function freeze() {
            if (table.style.tableLayout === "fixed") return;
            table.querySelectorAll("thead th").forEach(function (th) {
                th.style.width = th.getBoundingClientRect().width + "px";
            });
            table.style.tableLayout = "fixed";
        }
        var ths = table.querySelectorAll("thead th");
        ths.forEach(function (th, i) {
            if (th.querySelector(".col-resize-handle")) return;
            th.style.position = th.style.position || "relative";
            var h = document.createElement("span");
            h.className = "col-resize-handle";
            h.title = "Drag to resize · double-click to auto-fit";
            h.addEventListener("mousedown", function (e) {
                e.preventDefault(); e.stopPropagation();
                freeze();
                var startX = e.clientX, startW = th.getBoundingClientRect().width;
                document.body.style.cursor = "col-resize";
                function mv(ev) { th.style.width = Math.max(30, startW + (ev.clientX - startX)) + "px"; }
                function up() {
                    saved[i] = Math.round(th.getBoundingClientRect().width); persist();
                    document.body.style.cursor = "";
                    document.removeEventListener("mousemove", mv);
                    document.removeEventListener("mouseup", up);
                }
                document.addEventListener("mousemove", mv);
                document.addEventListener("mouseup", up);
            });
            h.addEventListener("dblclick", function (e) {
                e.preventDefault(); e.stopPropagation();
                th.style.width = ""; delete saved[i]; persist();
                if (!Object.keys(saved).length) table.style.tableLayout = "";   // back to auto-grow
            });
            th.appendChild(h);
        });
        // Re-apply any saved widths from a previous session (implies manual control).
        var any = false;
        ths.forEach(function (th, i) { if (saved[i] != null) { th.style.width = saved[i] + "px"; any = true; } });
        if (any) table.style.tableLayout = "fixed";
    };

    function _formatValue(v) {
        if (v === null) return "null";
        if (typeof v === "boolean") return v ? "true" : "false";
        if (typeof v === "string") return '"' + v + '"';
        if (typeof v === "number") return _groupDigits(v);
        return String(v);
    }

    function _deepEqual(a, b) {
        if (a === b) return true;
        if (a === null || b === null) return false;
        if (typeof a !== typeof b) return false;
        if (typeof a !== "object") return false;
        var isArrA = Array.isArray(a), isArrB = Array.isArray(b);
        if (isArrA !== isArrB) return false;
        if (isArrA) {
            if (a.length !== b.length) return false;
            for (var i = 0; i < a.length; i++) {
                if (!_deepEqual(a[i], b[i])) return false;
            }
            return true;
        }
        var keysA = Object.keys(a), keysB = Object.keys(b);
        if (keysA.length !== keysB.length) return false;
        for (var k = 0; k < keysA.length; k++) {
            if (!(keysA[k] in b)) return false;
            if (!_deepEqual(a[keysA[k]], b[keysA[k]])) return false;
        }
        return true;
    }

    /**
     * Build a single tree node.  Container nodes (objects/arrays) are LAZY:
     * children are only materialised on first expand, keeping the initial
     * render O(visible-nodes) instead of O(total-keys).
     */
    /* A key the primary side does not have. Distinct from `undefined`, which a
       document can legitimately contain as a missing optional. */
    var _ABSENT = {absent: true};

    /* Union of both sides' keys, primary order first (docs/84). Used only in
       union mode so the live-diff overlay keeps iterating one document. */
    function _unionKeys(value, refValue, union, hasDiff) {
        var keys = (value && typeof value === "object" && !Array.isArray(value))
            ? Object.keys(value) : [];
        if (!union || !hasDiff || !refValue || typeof refValue !== "object"
            || Array.isArray(refValue)) return keys;
        var seen = {};
        for (var i = 0; i < keys.length; i++) seen[keys[i]] = 1;
        var extra = Object.keys(refValue);
        for (var j = 0; j < extra.length; j++) {
            if (!seen[extra[j]]) keys.push(extra[j]);
        }
        return keys;
    }

    function _buildNode(key, value, path, depth, refValue, hasDiff, valueClick, union) {
        valueClick = valueClick || "edit";
        // UNION mode (docs/84): the two sides are compared as a SET of keys, so
        // a key only one side has still gets a row — "added" / "removed" are
        // differences an IDE shows and this tree used to render as nothing at
        // all (it iterated the primary document's keys only). Off by default:
        // the live-diff overlay is a before→after of one document and must
        // keep its exact behaviour.
        // The primary document is the BEFORE side and refData is the AFTER
        // side, so every row reads "A value → B value" like every other
        // before→after surface in the app.
        var onlyRef = (value === _ABSENT);          // appeared on the after side
        var isAbsent = onlyRef;                     // (kept: read as "not here")
        var shown = onlyRef ? refValue : value;
        var onlyPrimary = !!(union && hasDiff && !onlyRef && refValue === undefined);
        var type = _typeOf(shown);
        var isContainer = (type === "object" || type === "array");
        var node = document.createElement("div");
        node.className = "tree-node";
        node.setAttribute("data-depth", depth);
        node.setAttribute("data-path", path);
        // Stashed so a whole-container JSON edit can rebuild this node in place
        // (see _makeContainerEditable / _rebuildNode) without re-fetching the tree.
        node._meta = {key: key, path: path, depth: depth, refValue: refValue,
                      hasDiff: hasDiff, valueClick: valueClick, union: union};
        node._value = value;   // current value (kept fresh by _rebuildNode) — for key-copy

        if (hasDiff && refValue !== undefined && !isAbsent && !_deepEqual(value, refValue)) {
            node.classList.add("tree-diff");
        }
        if (onlyRef) node.classList.add("tree-diff", "tree-added");
        if (onlyPrimary) node.classList.add("tree-diff", "tree-removed");

        var row = document.createElement("div");
        row.className = "tree-row";

        if (isContainer) {
            var toggle = document.createElement("span");
            toggle.className = "tree-toggle collapsed";
            toggle.textContent = "\u25B6";
            toggle.onclick = function() { _toggleNode(node); };
            row.appendChild(toggle);
        } else {
            var spacer = document.createElement("span");
            spacer.className = "tree-toggle-spacer";
            row.appendChild(spacer);
        }

        if (key !== null) {
            var keyEl = document.createElement("span");
            keyEl.className = "tree-key";
            keyEl.textContent = key;
            keyEl.title = "Click to copy path \u00b7 double-click to copy this value (paste into an empty '" + key + "')";
            // Single click copies the PATH (debounced so a double-click doesn't also
            // fire it); double-click copies this node's VALUE into the paste buffer so
            // it can be dropped into an empty same-key field elsewhere \u2014 the easiest
            // way to fill a list / matrix / multi-value field (see _treeCopyKey).
            (function(el, p, nd) {
                var t = null;
                el.onclick = function() {
                    if (t) return;
                    t = setTimeout(function() {
                        t = null;
                        navigator.clipboard.writeText(p);
                        el.classList.add("tree-copied");
                        setTimeout(function() { el.classList.remove("tree-copied"); }, 800);
                    }, 230);
                };
                el.ondblclick = function(e) {
                    e.preventDefault(); if (t) { clearTimeout(t); t = null; }
                    _treeCopyKey(nd);
                };
            })(keyEl, path, node);
            row.appendChild(keyEl);
            // Config Manual (2026-08-27): a ? on every editable state row —
            // "what is this key, what can it be, what else can this node carry"
            var helpEl = null;
            if (_keyHelpOn && valueClick === "edit" && window.openConfigManual) {
                helpEl = document.createElement("button");
                helpEl.type = "button"; helpEl.className = "key-help-btn tree-help"; helpEl.tabIndex = -1;
                helpEl.textContent = "?"; helpEl.title = "Config Manual — this key (F1)";
                (function (p2) {
                    helpEl.onclick = function (ev) { ev.stopPropagation(); window.openConfigManual({ path: p2 }); };
                })(path);
            }

            var colon = document.createElement("span");
            colon.className = "tree-colon";
            colon.textContent = ": ";
            row.appendChild(colon);
        }

        if (isContainer) {
            var summary = document.createElement("span");
            summary.className = "tree-summary";
            if (type === "object") {
                var n = Object.keys(shown).length;
                summary.textContent = "{" + n + " key" + (n !== 1 ? "s" : "") + "}";
            } else {
                summary.textContent = "[" + shown.length + " item" + (shown.length !== 1 ? "s" : "") + "]";
            }
            row.appendChild(summary);

            // Edit the WHOLE list/dict as JSON — the only way to enter a list value
            // (the scalar leaf editor can't). Read-only trees (copy / livediff) get
            // no edit affordance. Click is stopped so it never toggles expand.
            if (valueClick === "edit" && !isAbsent) {
                var jsonBtn = document.createElement("button");
                jsonBtn.type = "button";
                jsonBtn.className = "tree-json-edit-btn";
                jsonBtn.textContent = "✎";   // ✎
                jsonBtn.title = "Edit this " + (type === "array" ? "list" : "object") + " as JSON";
                (function(nd, p, v) {
                    jsonBtn.onclick = function(e) { e.stopPropagation(); _makeContainerEditable(nd, p, v); };
                })(node, path, value);
                row.appendChild(jsonBtn);
            }

            // docs/126 ④: name the port's owner right on the node ("q2 · z",
            // "q1-2 · coupler") — the map is server-derived from the wiring
            // pointers and injected by the explorer page (absent elsewhere).
            if (window._treePortOwners && window._treePortOwners[path]) {
                var ownEl = document.createElement("span");
                ownEl.className = "tree-owner-chip";
                ownEl.textContent = "⌁ " + window._treePortOwners[path];
                ownEl.title = "This port is wired to " + window._treePortOwners[path];
                row.appendChild(ownEl);
            }

            // Lazy children container — populated on first expand
            var children = document.createElement("div");
            children.className = "tree-children";
            children.style.display = "none";

            // Store data for deferred rendering (closure captures value/refValue)
            node._lazyData = { value: isAbsent ? _ABSENT : value, type: type, path: path,
                               depth: depth, refValue: refValue, hasDiff: hasDiff,
                               valueClick: valueClick, union: union };

            if (helpEl) row.appendChild(helpEl);   // last, after the value
            node.appendChild(row);
            node.appendChild(children);
        } else {
            var valEl = document.createElement("span");
            var valClass = "tree-val tree-val-" + type;
            if (_isPointer(shown)) valClass = "tree-val tree-val-pointer";
            valEl.className = valClass;
            valEl.textContent = _formatValue(shown);
            // raw value for the edit input (strings without display-quotes) / copy
            valEl.dataset.editVal = (typeof shown === "string") ? shown : _formatValue(shown);
            if (valueClick === "copy") {
                // Read-only tree (e.g. a dataset's frozen parameters): click copies
                // the value. Editing here would wrongly POST against the live store.
                valEl.title = "Click to copy value";
                valEl.style.cursor = "copy";
                (function(el) {
                    el.onclick = function(e) {
                        e.stopPropagation();
                        var raw = el.dataset.editVal != null ? el.dataset.editVal : el.textContent;
                        window.copyWithFeedback(raw, el);
                    };
                })(valEl);
            } else if (valueClick === "diff" || isAbsent) {
                // Read-only comparison view (docs/84): the two sides come from
                // arbitrary sources \u2014 one of them is usually not even editable \u2014
                // so a click must never open an editor against the LOADED chip.
                // Copying the value is the useful action here.
                valEl.title = "Click to copy value";
                valEl.style.cursor = "copy";
                (function(el) {
                    el.onclick = function(e) {
                        e.stopPropagation();
                        var raw = el.dataset.editVal != null ? el.dataset.editVal : el.textContent;
                        window.copyWithFeedback(raw, el);
                    };
                })(valEl);
            } else {
                valEl.title = _isPointer(shown) ? "Pointer \u2014 click to edit" : "Click to edit";
                valEl.style.cursor = "pointer";
                (function(el, p) {
                    el.onclick = function(e) { e.stopPropagation(); _makeValueEditable(el, p); };
                })(valEl, path);
            }
            row.appendChild(valEl);

            // A null leaf is the common "not yet set" field (e.g. exponential_filter):
            // offer the SAME multi-line JSON editor as containers so a list / matrix /
            // object can be entered comfortably, not just squeezed into the one-line
            // box. (The one-line editor still works for a scalar.)
            if (shown === null && valueClick === "edit" && !isAbsent) {
                var nullJsonBtn = document.createElement("button");
                nullJsonBtn.type = "button";
                nullJsonBtn.className = "tree-json-edit-btn";
                nullJsonBtn.textContent = "✎";   // ✎
                nullJsonBtn.title = "Enter a value as JSON (list / object / any type)";
                (function(nd, p) {
                    nullJsonBtn.onclick = function(e) { e.stopPropagation(); _makeContainerEditable(nd, p, null); };
                })(node, path);
                row.appendChild(nullJsonBtn);
            }

            if (onlyRef || onlyPrimary) {
                // A key only ONE side has. The tree used to render nothing for
                // these — it iterated the primary document's keys only — which
                // is precisely what an IDE diff must show.
                var tag = document.createElement("span");
                tag.className = "tree-sidetag " +
                    (onlyRef ? "tree-tag-added" : "tree-tag-removed");
                tag.textContent = onlyRef ? "added" : "removed";
                row.appendChild(tag);
            } else if (hasDiff && refValue !== undefined && !_deepEqual(value, refValue)) {
                // "livediff" is the workbench's before→after mode: value = the SM
                // working copy (before), refValue = Qualibrate's live value (after).
                var liveDiff = (valueClick === "livediff");
                var cmpDiff = (valueClick === "diff");
                if (liveDiff || cmpDiff) {
                    row.classList.add("tree-row-incoming");
                    var arrow = document.createElement("span");
                    arrow.className = "tree-incoming-arrow";
                    arrow.textContent = " → ";
                    row.appendChild(arrow);
                    var inEl = document.createElement("span");
                    inEl.className = "tree-incoming-val tree-val-" + _typeOf(refValue) +
                        (_isPointer(refValue) ? " tree-val-pointer" : "");
                    inEl.textContent = _formatValue(refValue);
                    inEl.title = cmpDiff ? "the other side's value"
                                         : "Qualibrate's live value";
                    row.appendChild(inEl);
                }
                // ONE delta implementation (docs/76). This tree printed its own
                // toFixed(6)/toExponential(3), so the same change read
                // "(+0.000123)" here and "+100,000,000 (+1.96%)" in the Review
                // tray. ValueDelta is the JS mirror of the server filter, so
                // every surface now agrees character for character.
                // Both modes read the same way now: the primary document is
                // the BEFORE side, the ref document the AFTER side.
                var oldV = value, newV = refValue;
                var chipHtml = window.ValueDelta
                    ? window.ValueDelta.chipHtml(oldV, newV, "tree-delta") : "";
                if (chipHtml) {
                    var holder = document.createElement("span");
                    holder.innerHTML = chipHtml;
                    while (holder.firstChild) row.appendChild(holder.firstChild);
                }
                if (liveDiff) {
                    var acc = document.createElement("button");
                    acc.type = "button";
                    acc.className = "tree-accept-btn";
                    acc.textContent = "✓";
                    acc.title = "Accept Qualibrate's value into the working state";
                    (function(p, rv, el, rw) {
                        // window.-qualified: the handlers live in the live-diff
                        // IIFE, not this one — a bare call is a ReferenceError
                        // at click time and the accept is silently lost
                        // (docs/124 C-1; same cross-IIFE class as _deepEqual).
                        acc.onclick = function(e) { e.stopPropagation(); window._acceptLiveValue(p, rv, el, rw); };
                    })(path, refValue, valEl, row);
                    row.appendChild(acc);
                    var rej = document.createElement("button");
                    rej.type = "button";
                    rej.className = "tree-reject-btn";
                    rej.textContent = "✗";
                    rej.title = "Keep your value (dismiss this incoming change)";
                    (function(rw, p) {
                        rej.onclick = function(e) { e.stopPropagation(); window._rejectLiveValue(rw, p); };
                    })(row, path);
                    row.appendChild(rej);
                }
            }

            if (helpEl) row.appendChild(helpEl);   // last, after the value
            node.appendChild(row);
        }

        // If a key-copy is active, a freshly-built empty same-key node (e.g. one
        // lazily materialised on expand) should immediately offer its paste button.
        if (_treeCopyBuffer) _applyPasteTargetTo(node);

        return node;
    }

    /** Materialise lazy children for a container node (called once on first expand). */
    function _materializeChildren(nodeEl) {
        var d = nodeEl._lazyData;
        if (!d) return; // already materialised
        var children = nodeEl.querySelector(":scope > .tree-children");
        if (!children) return;
        var _kc = nodeEl.closest ? nodeEl.closest(".json-tree") : null;
        _keyHelpOn = !!(_kc && _kc._keyHelp);

        // An absent container renders from the OTHER side, so a whole removed
        // subtree can still be expanded and read.
        var src = (d.value === _ABSENT) ? d.refValue : d.value;
        if (d.type === "object") {
            var keys = _unionKeys(src, (d.value === _ABSENT) ? undefined : d.refValue,
                                  d.union, d.hasDiff);
            for (var i = 0; i < keys.length; i++) {
                var childPath = d.path ? d.path + "." + keys[i] : keys[i];
                var childRef = (d.hasDiff && d.refValue && typeof d.refValue === "object" && !Array.isArray(d.refValue))
                    ? d.refValue[keys[i]] : undefined;
                var childVal = (d.value === _ABSENT) ? _ABSENT
                    : (Object.prototype.hasOwnProperty.call(src, keys[i]) ? src[keys[i]] : _ABSENT);
                children.appendChild(_buildNode(keys[i], childVal, childPath, d.depth + 1,
                                                childRef, d.hasDiff, d.valueClick, d.union));
            }
        } else {
            for (var j = 0; j < src.length; j++) {
                // Canonical dot-form numeric segment (a.b.3) — matches the server
                // path grammar so element edits POST directly to /field/edit.
                var itemPath = d.path + "." + j;
                var itemRef = (d.hasDiff && Array.isArray(d.refValue)) ? d.refValue[j] : undefined;
                var itemVal = (d.value === _ABSENT) ? _ABSENT : src[j];
                children.appendChild(_buildNode(String(j), itemVal, itemPath, d.depth + 1,
                                                itemRef, d.hasDiff, d.valueClick, d.union));
            }
        }

        delete nodeEl._lazyData; // free memory, prevent double-build
    }

    function _toggleNode(nodeEl) {
        var children = nodeEl.querySelector(":scope > .tree-children");
        var toggle = nodeEl.querySelector(":scope > .tree-row > .tree-toggle");
        if (!children || !toggle) return;
        var collapsed = children.style.display === "none";

        // Lazy: build children on first expand
        if (collapsed && nodeEl._lazyData) {
            _materializeChildren(nodeEl);
        }

        children.style.display = collapsed ? "" : "none";
        toggle.textContent = collapsed ? "\u25BC" : "\u25B6";
        toggle.classList.toggle("collapsed", !collapsed);
        toggle.classList.toggle("expanded", collapsed);
    }

    function _expandToDepth(container, maxDepth) {
        var nodes = container.querySelectorAll(".tree-node");
        for (var i = 0; i < nodes.length; i++) {
            var d = parseInt(nodes[i].getAttribute("data-depth"), 10);
            var children = nodes[i].querySelector(":scope > .tree-children");
            var toggle = nodes[i].querySelector(":scope > .tree-row > .tree-toggle");
            if (!children || !toggle) continue;
            if (d < maxDepth) {
                if (nodes[i]._lazyData) _materializeChildren(nodes[i]);
                children.style.display = "";
                toggle.textContent = "\u25BC";
                toggle.classList.remove("collapsed");
                toggle.classList.add("expanded");
            } else {
                children.style.display = "none";
                toggle.textContent = "\u25B6";
                toggle.classList.add("collapsed");
                toggle.classList.remove("expanded");
            }
        }
    }

    function _collapseAll(container) {
        _expandToDepth(container, 0);
    }

    function _expandAll(container) {
        // Materialise all lazy nodes — loop until none remain
        for (var pass = 0; pass < 20; pass++) {
            var lazy = container.querySelectorAll(".tree-node");
            var found = false;
            for (var i = 0; i < lazy.length; i++) {
                if (lazy[i]._lazyData) { _materializeChildren(lazy[i]); found = true; }
            }
            if (!found) break;
        }
        // Expand every node
        var nodes = container.querySelectorAll(".tree-node");
        for (var i = 0; i < nodes.length; i++) {
            var children = nodes[i].querySelector(":scope > .tree-children");
            var toggle = nodes[i].querySelector(":scope > .tree-row > .tree-toggle");
            if (!children || !toggle) continue;
            children.style.display = "";
            toggle.textContent = "\u25BC";
            toggle.classList.remove("collapsed");
            toggle.classList.add("expanded");
        }
    }

    /**
     * Strip the trailing segment of a dot/bracket path, returning the parent
     * path ("" at the root). Handles both encodings produced by _buildNode /
     * _materializeChildren: object child "parent.key" and array child "parent[i]".
     */
    function _parentPath(path) {
        // Pure dot-form paths (list elements use numeric segments now).
        if (!path) return "";
        var dot = path.lastIndexOf(".");
        return dot <= 0 ? "" : path.slice(0, dot);
    }

    /**
     * Walk the source JS object ONCE into a flat, pre-lowercased search index.
     * This replaces the old approach of materialising the entire DOM and reading
     * textContent on every keystroke. Path encoding is byte-identical to
     * _buildNode / _materializeChildren so a matched path maps straight onto a
     * node's data-path attribute.
     * Returns { flat: [{path, pathLower, hayLower}] }.
     */
    function _buildFlatIndex(data) {
        var flat = [];

        function add(path, keyStr, valStr) {
            var hay = ((keyStr == null ? "" : String(keyStr)) + " " + (valStr || "")).toLowerCase();
            flat.push({ path: path, pathLower: path.toLowerCase(), hayLower: hay, val: valStr || "" });
        }

        function walk(key, value, path) {
            var type = _typeOf(value);
            if (type === "object") {
                var keys = Object.keys(value);
                add(path, key, "{" + keys.length + " key" + (keys.length !== 1 ? "s" : "") + "}");
                for (var i = 0; i < keys.length; i++) {
                    var childPath = path ? path + "." + keys[i] : keys[i];
                    walk(keys[i], value[keys[i]], childPath);
                }
            } else if (type === "array") {
                add(path, key, "[" + value.length + " item" + (value.length !== 1 ? "s" : "") + "]");
                for (var j = 0; j < value.length; j++) {
                    // dot-form numeric segments — must mirror _materializeChildren
                    // or search keepPaths never match materialised element rows
                    walk(String(j), value[j], path + "." + j);
                }
            } else {
                add(path, key, _formatValue(value));
            }
        }

        // Mirror renderJsonTree's top-level handling exactly.
        if (typeof data === "object" && data !== null && !Array.isArray(data)) {
            var topKeys = Object.keys(data);
            for (var k = 0; k < topKeys.length; k++) {
                walk(topKeys[k], data[topKeys[k]], topKeys[k]);
            }
        } else {
            walk(null, data, "");
        }
        return { flat: flat };
    }

    /**
     * Search dispatcher. renderJsonTree trees carry their source object on
     * container._treeData and use the fast data-driven path; the eagerly-built
     * unified comparison tree has no _treeData and uses the DOM fallback.
     * A repeat-query guard skips redundant re-runs (e.g. tab-switch re-fires).
     */
    function _searchTree(container, query) {
        var q = (query || "").toLowerCase().trim();
        if (container._lastSearchQuery === q) return;
        container._lastSearchQuery = q;

        if (container._treeData !== undefined && container._treeData !== null) {
            _searchTreeData(container, q);
        } else {
            _searchTreeDom(container, q);
        }
    }

    /**
     * Data-driven search for renderJsonTree trees. Matches against the cached
     * flat index (no DOM walk), then materialises + expands ONLY the branches
     * that contain matches \u2014 never the whole tree, and with zero contains() calls.
     */
    var _TREE_SEARCH_MATERIALIZE_MAX = (window.__treeSearchMaterializeMax || 150);   // matches: above this, list instead of expand
    // Key-help (?) rows are for the LIVE state trees only (crud renders) --
    // never a compare view's other chip or an inspector's relative subtree.
    var _keyHelpOn = false;
    var _TREE_SEARCH_LIST_MAX = 400;          // rows in that list
    // The flat result list for a broad tree search (see _searchTreeData).
    // `res` null removes it. Rows carry the dot path; clicking one expands the
    // tree to that single row and highlights it (the classic per-row cost, once).
    function _treeSearchResults(container, res) {
        var el = container.parentNode ? container.parentNode.querySelector(":scope > .tree-search-results") : null;
        if (!res) { if (el) el.parentNode.removeChild(el); return; }
        if (!el) {
            el = document.createElement("div");
            el.className = "tree-search-results";
            container.parentNode.insertBefore(el, container);
            el.addEventListener("click", function (ev) {
                var row = ev.target.closest && ev.target.closest(".tsr-row");
                if (!row) return;
                ev.preventDefault();
                var p = row.getAttribute("data-path");
                _treeSearchResults(container, null);
                var nodesAll = container.querySelectorAll(".tree-node");
                for (var i = 0; i < nodesAll.length; i++) nodesAll[i].classList.remove("tree-search-hidden");
                if (window._navigateToExplorerPath) window._navigateToExplorerPath(p);
            });
        }
        var h = '<div class="tsr-head muted">' + res.total + ' matches for <code>' + _escapeHtml(res.q) + '</code>'
              + ' — listed, not expanded (more than ' + _TREE_SEARCH_MATERIALIZE_MAX + '); click a row to open it'
              + (res.hits.length < res.total ? ' · first ' + res.hits.length + ' shown' : '') + '</div>';
        for (var r = 0; r < res.hits.length; r++) {
            var e = res.hits[r];
            h += '<a href="#" class="tsr-row" data-path="' + _escapeHtml(e.path) + '"><code class="tsr-path">'
               + _escapeHtml(e.path) + '</code><span class="tsr-val">' + _escapeHtml(e.val || '') + '</span></a>';
        }
        el.innerHTML = h;
    }
    function _escapeHtml(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function _searchTreeData(container, q) {
        // Clear stale search classes on whatever is currently materialised.
        var rendered = container.querySelectorAll(".tree-node");
        for (var i = 0; i < rendered.length; i++) {
            rendered[i].classList.remove("tree-highlight", "tree-search-hidden");
        }
        if (!q) {
            _treeSearchResults(container, null);
            _expandToDepth(container, 1);
            return;
        }

        if (!container._flatIndex) {
            container._flatIndex = _buildFlatIndex(container._treeData);
        }
        var flat = container._flatIndex.flat;

        // Space = AND, standalone | = OR (SearchQuery — the shared grammar).
        // The tree used to test the WHOLE trimmed query as one substring, so
        // two words found nothing while the same two words worked in Live
        // State Edit — the single most-asked search question. A term matches
        // in the node's key+value haystack OR its dot-path; joining the two
        // with a space is exact because tokens cannot contain one. Strictly
        // additive: a phrase match implies every token matches.
        var grps = window.SearchQuery ? SearchQuery.groups(q) : [[q]];

        // O(N) scan over pre-lowercased fields. keepPaths = matches + ancestors.
        var matchPaths = new Set();
        var keepPaths = new Set();
        for (var j = 0; j < flat.length; j++) {
            var e = flat[j];
            if (window.SearchQuery
                    ? SearchQuery.matchesHay(e.hayLower + ' ' + e.pathLower, grps)
                    : (e.hayLower.indexOf(q) >= 0 || e.pathLower.indexOf(q) >= 0)) {
                matchPaths.add(e.path);
                var p = e.path;
                while (!keepPaths.has(p)) {       // stop once an ancestor chain is known
                    keepPaths.add(p);
                    if (p === "") break;
                    p = _parentPath(p);
                }
            }
        }

        if (matchPaths.size === 0) {
            for (var h = 0; h < rendered.length; h++) {
                rendered[h].classList.add("tree-search-hidden");
            }
            _treeSearchResults(container, null);
            return;
        }
        // Night session 2026-08-28: a broad query ("amplitude" on a 20Q chip =
        // 1,384 matches) used to MATERIALISE every matching subtree -- 430 ms of
        // DOM creation + 330 ms of style/layout on a real chip. Past the cap,
        // the matches are listed as a flat result list instead (path + value,
        // click = expand + jump to that one row); the tree itself is left as
        // it was. Below the cap the classic in-tree highlight is unchanged.
        if (matchPaths.size > _TREE_SEARCH_MATERIALIZE_MAX) {
            var hits = [];
            for (var jj = 0; jj < flat.length && hits.length < _TREE_SEARCH_LIST_MAX; jj++) {
                if (matchPaths.has(flat[jj].path)) hits.push(flat[jj]);
            }
            _treeSearchResults(container, { total: matchPaths.size, hits: hits, q: q });
            for (var hh = 0; hh < rendered.length; hh++) rendered[hh].classList.add("tree-search-hidden");
            return;
        }
        _treeSearchResults(container, null);

        // Materialise only the kept branches by descending top-down from the
        // container and pruning any subtree not in keepPaths. Compares data-path
        // via string equality (robust to any key content; no per-path
        // querySelector, which is unindexed and would break on keys with quotes).
        var stack = [];
        var top = container.children;
        for (var s = 0; s < top.length; s++) {
            if (top[s].classList && top[s].classList.contains("tree-node")) stack.push(top[s]);
        }
        while (stack.length) {
            var kn = stack.pop();
            if (!keepPaths.has(kn.getAttribute("data-path") || "")) continue;  // prune
            if (kn._lazyData) _materializeChildren(kn);                        // build this level
            var kids = kn.querySelector(":scope > .tree-children");
            if (kids) {
                var kc = kids.children;
                for (var c = 0; c < kc.length; c++) {
                    if (kc[c].classList && kc[c].classList.contains("tree-node")) stack.push(kc[c]);
                }
            }
        }

        // Single pass over now-materialised nodes: highlight matches, expand
        // kept branches, hide the rest.
        var nodes = container.querySelectorAll(".tree-node");
        for (var n = 0; n < nodes.length; n++) {
            var nd = nodes[n];
            var path = nd.getAttribute("data-path") || "";
            if (matchPaths.has(path)) nd.classList.add("tree-highlight");
            if (keepPaths.has(path)) {
                var ch = nd.querySelector(":scope > .tree-children");
                var tg = nd.querySelector(":scope > .tree-row > .tree-toggle");
                if (ch && tg) {
                    ch.style.display = "";
                    tg.textContent = "\u25BC";
                    tg.classList.remove("collapsed");
                    tg.classList.add("expanded");
                }
            } else {
                nd.classList.add("tree-search-hidden");
            }
        }
    }

    /**
     * DOM fallback search for fully-materialised trees with no source object
     * (the unified comparison tree). Caches per-node search text on first use
     * and finds ancestors with an upward parentElement walk (O(M*depth)) instead
     * of the old O(N*M) contains() scan.
     */
    function _searchTreeDom(container, q) {
        var nodes = container.querySelectorAll(".tree-node");
        for (var i = 0; i < nodes.length; i++) {
            nodes[i].classList.remove("tree-highlight", "tree-search-hidden");
        }
        if (!q) {
            _expandToDepth(container, 1);
            return;
        }

        // Safety net for any lazy DOM-only tree (no-op for the eager unified tree).
        var changed = true;
        while (changed) {
            changed = false;
            for (var m = 0; m < nodes.length; m++) {
                if (nodes[m]._lazyData) { _materializeChildren(nodes[m]); changed = true; }
            }
            if (changed) nodes = container.querySelectorAll(".tree-node");
        }

        // Same grammar as _searchTreeData — this DOM path serves the unified
        // compare tree, which must not answer differently from the data path.
        var grpsD = window.SearchQuery ? SearchQuery.groups(q) : [[q]];

        var matches = [];
        for (var j = 0; j < nodes.length; j++) {
            var nd = nodes[j];
            var hay = nd._searchText;
            if (hay === undefined) {
                var row = nd.querySelector(":scope > .tree-row");
                hay = row ? row.textContent.toLowerCase() : "";
                nd._searchText = hay;
            }
            var pathAttr = (nd.getAttribute("data-path") || "").toLowerCase();
            if (window.SearchQuery
                    ? SearchQuery.matchesHay(hay + ' ' + pathAttr, grpsD)
                    : (hay.indexOf(q) >= 0 || pathAttr.indexOf(q) >= 0)) {
                nd.classList.add("tree-highlight");
                matches.push(nd);
            }
        }

        if (matches.length === 0) {
            for (var h = 0; h < nodes.length; h++) nodes[h].classList.add("tree-search-hidden");
            return;
        }

        // Keep set = matches + their ancestors (upward walk, early-terminated).
        var keep = new Set();
        for (var k = 0; k < matches.length; k++) {
            var cur = matches[k];
            while (cur && cur !== container) {
                if (cur.classList && cur.classList.contains("tree-node")) {
                    if (keep.has(cur)) break;
                    keep.add(cur);
                }
                cur = cur.parentElement;
            }
        }

        for (var n = 0; n < nodes.length; n++) {
            var node2 = nodes[n];
            if (keep.has(node2)) {
                var ch2 = node2.querySelector(":scope > .tree-children");
                var tg2 = node2.querySelector(":scope > .tree-row > .tree-toggle");
                if (ch2 && tg2) {
                    ch2.style.display = "";
                    tg2.textContent = "\u25BC";
                    tg2.classList.remove("collapsed");
                    tg2.classList.add("expanded");
                }
            } else {
                node2.classList.add("tree-search-hidden");
            }
        }
    }

    // Inline, dismissible error chip after a rejected edit — the red flash alone
    // told the user NOTHING about why the server bounced the write (type errors,
    // policy blocks, bad list index all looked identical). Auto-clears in 8s.
    function _showEditError(anchorEl, msg) {
        var row = anchorEl.closest ? (anchorEl.closest(".tree-row") || anchorEl) : anchorEl;
        var old = row.querySelector(".tree-edit-err");
        if (old) old.remove();
        var chip = document.createElement("span");
        chip.className = "tree-edit-err";
        chip.textContent = "✗ " + (msg || "edit rejected");
        chip.title = "click to dismiss";
        chip.onclick = function() { chip.remove(); };
        row.appendChild(chip);
        setTimeout(function() { chip.remove(); }, 8000);
    }
    window._showEditError = _showEditError;

    function _makeValueEditable(valEl, dotPath) {
        if (valEl.querySelector("input")) return; // already editing
        var currentDisplay = valEl.textContent;
        var editVal = valEl.dataset.editVal !== undefined ? valEl.dataset.editVal : currentDisplay;

        var input = document.createElement("input");
        input.type = "text";
        input.className = "tree-edit-input";
        input.value = editVal;
        input.size = Math.max(10, editVal.length + 2);

        valEl.textContent = "";
        valEl.appendChild(input);
        valEl.classList.add("tree-val-editing");
        input.focus();
        input.select();

        // expected-type chip (env schema / user assignment / inference) —
        // fetched on editor open only, never per keystroke
        fetch("/field/peek?dot_path=" + encodeURIComponent(dotPath))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var e = d.expected && d.expected[dotPath];
                if (!e || !valEl.contains(input)) return;
                var chip = document.createElement("span");
                chip.className = "tree-type-chip";
                chip.textContent = e.type + " · " + e.source;
                chip.title = (e.class_path ? e.class_path + "." + e.field + " — " : "") +
                             (e.detail || "");
                valEl.appendChild(chip);
            }).catch(function () {});

        var committed = false;

        function commit() {
            if (committed) return;
            var newVal = input.value;
            // No-op guard: an unchanged value must NOT POST (the server never
            // no-ops set_value → it would spam the change log / pending tray).
            // This makes commit-on-blur/Tab safe to fire unconditionally.
            if (newVal === editVal) { cancel(); return; }
            committed = true;
            valEl.textContent = currentDisplay;
            valEl.classList.remove("tree-val-editing");

            var body = new URLSearchParams();
            body.append("dot_path", dotPath);
            body.append("value", newVal);
            body.append("expect_chip", window.__chipToken || "");   // wrong-chip 409 gate

            var _post = function(extraBody) {
                var b2 = new URLSearchParams(body);
                if (extraBody) Object.keys(extraBody).forEach(function(k) {
                    b2.append(k, extraBody[k]);
                });
                return fetch("/field/edit", {
                    method: "POST",
                    headers: {"Content-Type": "application/x-www-form-urlencoded"},
                    body: b2.toString()
                }).then(function(resp) { return resp.json(); });
            };
            _post(null)
            .then(function handleData(data) {
                if (!data.ok && data.fsp_compensation) {
                    // r12-B: never silent — the compensation offer first.
                    window._openFspPopup(data.fsp_compensation, function(mode, plan) {
                        if (mode === "cancel") return;   // nothing committed
                        if (mode === "solo") {
                            _post({fsp_ack: "solo"}).then(handleData);
                            return;
                        }
                        fetch("/field/edit-batch", {
                            method: "POST",
                            headers: {"Content-Type": "application/json"},
                            body: JSON.stringify({
                                updates: [{dot_path: dotPath, value: newVal}]
                                    .concat(window._fspCompUpdates(plan)),
                                fsp_ack: "comp",
                                expect_chip: window.__chipToken || "",
                            })
                        }).then(function(r) { return r.json(); })
                          .then(handleData);
                    });
                    return;
                }
                if (!data.ok && data.type_fix) {
                    // r14 ⑩: the field is stored as TEXT ("0.13") — the legacy
                    // coercer would keep it text forever. Never silent: ask.
                    var conv = window._confirmTypeFix(data.type_fix);
                    _post({type_fix: conv ? "convert" : "keep"}).then(handleData);
                    return;
                }
                if (!data.ok) {
                    valEl.classList.add("tree-val-error");
                    setTimeout(function() { valEl.classList.remove("tree-val-error"); }, 2000);
                    _showEditError(valEl, data.error);
                    return;
                }
                // docs/120 item 25: the rejection chip has an 8-second life and
                // was only ever cleared by a NEWER rejection, so after a fix it
                // sat beside the accepted value still reading "✗ …" — the screen
                // contradicting itself about the edit the user just made. A
                // success is the most definitive reason to retire it.
                (function () {
                    var _r = valEl.closest ? valEl.closest(".tree-row") : null;
                    var _e = _r && _r.querySelector(".tree-edit-err");
                    if (_e) _e.remove();
                    valEl.classList.remove("tree-val-error");
                })();
                // r14 honesty: re-render from the COMMITTED value the server
                // echoes (the coercer may have kept the old type) — the old
                // raw-text write-back showed "0.13"-the-string as bare 0.13
                // and mis-kept the number/string colour class.
                if (data.stored_kind !== undefined) {
                    valEl.textContent = _formatValue(data.stored);
                    valEl.dataset.editVal = (typeof data.stored === "string")
                        ? data.stored : _formatValue(data.stored);
                    valEl.className = valEl.className
                        .replace(/tree-val-(string|number|boolean|null|pointer)/g, "")
                        .trim();
                    valEl.classList.add("tree-val-" + _typeOf(data.stored));
                    if (_isPointer(data.stored)) valEl.classList.add("tree-val-pointer");
                } else {
                    valEl.textContent = newVal;
                    valEl.dataset.editVal = newVal;
                }
                var row = valEl.closest(".tree-row");
                if (row) row.classList.add("tree-row-pending");
                // If this field was part of an incoming live diff, inline-editing it
                // IS the user's choice for that row — invalidate its incoming entry so
                // a later "Accept all" can't overwrite the typed value with the stale
                // live one. (The ✓/✗ per-row buttons already do this; the inline editor
                // used to skip it, silently re-clobbering on Accept all.)
                if (row && row.classList.contains("tree-row-incoming") &&
                        window._explorerNoteInlineEdit) {
                    window._explorerNoteInlineEdit(dotPath, row);
                }
                // Update the pending tray. Route through _swapPendingTray so HTMX
                // is re-activated (htmx.process) on the injected subtree — without
                // it the tray's pure hx-post buttons (Save to working state / Apply
                // to live chip / per-value discard-X) stay inert. This was the lone
                // tray-swap site that hand-rolled replaceChild and skipped
                // htmx.process. _swapPendingTray does NOT restore the drawer
                // open-state / clear tree-pending markers, so keep _restoreTrayState.
                if (data.tray_html) {
                    _swapPendingTray(data.tray_html);
                    window._restoreTrayState && window._restoreTrayState();
                }
            })
            .catch(function() {
                valEl.textContent = currentDisplay;
                valEl.classList.remove("tree-val-editing");
            });
        }

        function cancel() {
            if (committed) return;
            committed = true;
            valEl.textContent = currentDisplay;
            valEl.classList.remove("tree-val-editing");
        }

        input.addEventListener("keydown", function(e) {
            if (e.key === "Enter")  { e.preventDefault(); commit(); }
            if (e.key === "Escape") { cancel(); }
        });
        // Tab / click-away / focus-loss COMMITS (like Enter), instead of the old
        // discard-on-blur. Escape still cancels (it sets `committed` first, so this
        // blur→commit is a no-op after it), and an unchanged value is a no-op via
        // the guard in commit(). The 100ms defer lets an Escape keydown win the race.
        input.addEventListener("blur", function() { setTimeout(commit, 100); });
    }

    // ── Copy a key's value → paste into an EMPTY same-key field elsewhere ──────
    // Double-clicking a key copies its value here; every empty field with the same
    // key name then offers a "paste" button. Built for list / matrix / multi-value
    // fields that are painful to retype (e.g. copy one qubit's confusion_matrix to
    // all the others). The buffer survives pastes (paste into many) until the user
    // clears it (Esc / ✕) or the tree is fully re-rendered (chip switch).
    var _treeCopyBuffer = null;   // {key, value, srcPath}

    function _isEmptyVal(v) {
        if (v === null || v === undefined) return true;
        if (Array.isArray(v)) return v.length === 0;
        if (typeof v === "object") return Object.keys(v).length === 0;
        return false;   // 0 / "" / false are real values, not "empty to fill"
    }

    function _clearPasteButtons() {
        document.querySelectorAll(".tree-paste-btn").forEach(function(b) { b.remove(); });
        document.querySelectorAll(".tree-paste-target").forEach(function(r) { r.classList.remove("tree-paste-target"); });
    }

    function _clearTreeCopy() {
        _treeCopyBuffer = null;
        _clearPasteButtons();
        var pill = document.getElementById("tree-copy-pill");
        if (pill) pill.hidden = true;
    }

    /** Add a "paste" button to one node iff a copy is active, the key matches, it
     *  isn't the source, and the node is empty (null / [] / {}). Editable trees only. */
    function _applyPasteTargetTo(node) {
        if (!_treeCopyBuffer) return;
        var m = node._meta;
        if (!m || m.valueClick !== "edit" || m.key !== _treeCopyBuffer.key) return;
        if (m.path === _treeCopyBuffer.srcPath || !_isEmptyVal(node._value)) return;
        var row = node.querySelector(":scope > .tree-row");
        if (!row || row.querySelector(".tree-paste-btn")) return;
        row.classList.add("tree-paste-target");
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn-sm tree-paste-btn";
        btn.textContent = "⎘ paste";   // ⎘
        btn.title = "Paste the copied '" + m.key + "' value into this empty field";
        (function(nd) { btn.onclick = function(e) { e.stopPropagation(); _pasteIntoNode(nd); }; })(node);
        row.appendChild(btn);
    }

    function _refreshPasteTargets() {
        _clearPasteButtons();
        if (!_treeCopyBuffer) return;
        document.querySelectorAll(".tree-node").forEach(_applyPasteTargetTo);
    }

    function _treeCopyKey(node) {
        var m = node._meta;
        if (!m || m.key == null) return;
        if (_isEmptyVal(node._value)) {
            if (window.showToast) window.showToast("'" + m.key + "' is empty — nothing to copy", "warning");
            return;
        }
        _treeCopyBuffer = {key: m.key, value: node._value, srcPath: m.path};
        _refreshPasteTargets();
        var n = document.querySelectorAll(".tree-paste-btn").length;
        var pill = document.getElementById("tree-copy-pill");
        if (!pill) {
            pill = document.createElement("div");
            pill.id = "tree-copy-pill"; pill.className = "tree-copy-pill";
            document.body.appendChild(pill);
        }
        pill.innerHTML = "";
        var label = document.createElement("span");
        label.textContent = "Copied '" + m.key + "' — " +
            (n ? ("click “paste” on " + n + " empty field" + (n === 1 ? "" : "s")) :
                 "open an empty '" + m.key + "' to paste");
        var x = document.createElement("button");
        x.type = "button"; x.className = "tree-copy-pill-x"; x.textContent = "✕";
        x.title = "Clear copy (Esc)"; x.onclick = _clearTreeCopy;
        pill.appendChild(label); pill.appendChild(x);
        pill.hidden = false;
    }

    function _pasteIntoNode(node) {
        if (!_treeCopyBuffer) return;
        var m = node._meta;
        var val = _treeCopyBuffer.value;
        var body = new URLSearchParams();
        body.append("dot_path", m.path);
        body.append("value", JSON.stringify(val));
        body.append("expect_chip", window.__chipToken || "");   // wrong-chip 409 gate
        fetch("/field/edit", {
            method: "POST",
            headers: {"Content-Type": "application/x-www-form-urlencoded"},
            body: body.toString()
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (!data.ok) { if (window.showToast) window.showToast("Paste failed: " + (data.error || ""), "error"); return; }
            var fresh = _rebuildNode(node, val);
            if (fresh) { var fr = fresh.querySelector(":scope > .tree-row"); if (fr) fr.classList.add("tree-row-pending"); }
            if (data.tray_html) { _swapPendingTray(data.tray_html); window._restoreTrayState && window._restoreTrayState(); }
            if (window._diagChanged) window._diagChanged();
            _refreshPasteTargets();   // the just-filled field drops out; others stay
        })
        .catch(function() { if (window.showToast) window.showToast("Paste request failed", "error"); });
    }

    document.addEventListener("keydown", function(e) {
        if (e.key === "Escape" && _treeCopyBuffer) _clearTreeCopy();
    });

    /** Rebuild a tree node in place from a new value, reusing the metadata stashed
     *  on the node by _buildNode. Returns the fresh node (or null if un-rebuildable). */
    function _rebuildNode(oldNode, newValue) {
        var m = oldNode._meta;
        if (!m || !oldNode.parentNode) return null;
        var fresh = _buildNode(m.key, newValue, m.path, m.depth, m.refValue, m.hasDiff, m.valueClick);
        oldNode.parentNode.replaceChild(fresh, oldNode);
        return fresh;
    }

    /** Edit a whole list/dict container as raw JSON. The server re-parses the text
     *  through _parse_value (JSON-aware), so a `[[..],[..]]` matrix / an
     *  `exponential_filter` list / a `{...}` object can be entered at once —
     *  something the per-leaf scalar editor cannot express. */
    function _makeContainerEditable(node, dotPath, value) {
        var row = node.querySelector(":scope > .tree-row");
        if (!row || node.querySelector(":scope > .tree-json-editor")) return;  // already editing

        var children = node.querySelector(":scope > .tree-children");
        var childDisplay = children ? children.style.display : null;
        if (children) children.style.display = "none";

        var editor = document.createElement("div");
        editor.className = "tree-json-editor";
        var ta = document.createElement("textarea");
        ta.className = "tree-json-textarea";
        ta.spellcheck = false;
        try { ta.value = JSON.stringify(value, null, 2); } catch (e) { ta.value = String(value); }
        ta.rows = Math.min(18, Math.max(3, ta.value.split("\n").length + 1));

        var bar = document.createElement("div");
        bar.className = "tree-json-editor-bar";
        var save = document.createElement("button");
        save.type = "button"; save.className = "btn-sm"; save.textContent = "Save";
        var cancel = document.createElement("button");
        cancel.type = "button"; cancel.className = "btn-sm outline"; cancel.textContent = "Cancel";
        var hint = document.createElement("span");
        hint.className = "tree-json-hint";
        hint.textContent = "JSON — Ctrl/⌘+Enter saves, Esc cancels";
        var err = document.createElement("span");
        err.className = "tree-json-err"; err.hidden = true;
        bar.appendChild(save); bar.appendChild(cancel); bar.appendChild(hint); bar.appendChild(err);

        editor.appendChild(ta); editor.appendChild(bar);
        node.insertBefore(editor, row.nextSibling);
        ta.focus(); ta.select();

        function close() {
            editor.remove();
            if (children && childDisplay !== null) children.style.display = childDisplay;
        }

        function doSave() {
            var txt = ta.value.trim();
            var parsed;
            try { parsed = JSON.parse(txt); }
            catch (ex) { err.hidden = false; err.textContent = "Invalid JSON: " + ex.message; return; }
            err.hidden = true; save.disabled = true;

            var body = new URLSearchParams();
            body.append("dot_path", dotPath);
            body.append("value", txt);   // server re-parses (authoritative coercion)
            body.append("expect_chip", window.__chipToken || "");   // wrong-chip 409 gate
            fetch("/field/edit", {
                method: "POST",
                headers: {"Content-Type": "application/x-www-form-urlencoded"},
                body: body.toString()
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.ok) { err.hidden = false; err.textContent = data.error || "Edit rejected"; save.disabled = false; return; }
                close();
                var fresh = _rebuildNode(node, parsed);
                if (fresh) {
                    var fr = fresh.querySelector(":scope > .tree-row");
                    if (fr) fr.classList.add("tree-row-pending");
                }
                if (data.tray_html) { _swapPendingTray(data.tray_html); window._restoreTrayState && window._restoreTrayState(); }
                if (window._diagChanged) window._diagChanged();
            })
            .catch(function() { err.hidden = false; err.textContent = "Request failed"; save.disabled = false; });
        }

        save.onclick = doSave;
        cancel.onclick = close;
        ta.addEventListener("keydown", function(e) {
            if (e.key === "Escape") { e.preventDefault(); close(); }
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); doSave(); }
        });
    }

    window._revertTreeNode = function(dotPath, oldValueStr) {
        var treeNode = document.querySelector('.tree-node[data-path="' + dotPath + '"]');
        if (!treeNode) return;
        var row = treeNode.querySelector(":scope > .tree-row");
        if (!row) return;
        row.classList.remove("tree-row-pending");
        var valEl = row.querySelector(".tree-val");
        if (!valEl) return;

        function paint(v) {
            valEl.textContent = _formatValue(v);
            valEl.dataset.editVal = (typeof v === "string") ? v : _formatValue(v);
            valEl.className = valEl.className
                .replace(/tree-val-(string|number|boolean|null|pointer)/g, "")
                .trim();
            valEl.classList.add("tree-val-" + _typeOf(v));
            if (_isPointer(v)) valEl.classList.add("tree-val-pointer");
        }

        // r14 honesty: the old numeric-first guess repainted a reverted STRING
        // "0.13" as bare 0.13 (wrong text AND wrong colour). Ask the server for
        // the actual typed value; the guess only remains as a fetch-failure
        // fallback.
        fetch("/field/peek?dot_path=" + encodeURIComponent(dotPath))
            .then(function(r) { return r.json(); })
            .then(function(d) {
                if (d && d.ok && d.values && dotPath in d.values) {
                    paint(d.values[dotPath]);
                    return;
                }
                throw new Error("peek miss");
            })
            .catch(function() {
                var num = Number(oldValueStr);
                if (oldValueStr !== "" && oldValueStr !== "null" && !isNaN(num)) {
                    paint(num);
                } else if (oldValueStr === "") {
                    valEl.textContent = "null";
                    valEl.dataset.editVal = "";
                } else {
                    paint(oldValueStr);
                }
            });
    };

    /* ── Explorer structural CRUD + type picker ─────────────────────────
       Hover-built row actions (＋ add child key on dicts, ✕ delete, ⚙ type
       picker on leaves), lazily attached via ONE delegated mouseover per
       crud-enabled container — no build cost across 10k idle rows. */

    // "real" (not "number") for floats — r14; the server accepts both tokens.
    var _TYPE_CHOICES = ["infer", "int", "real", "str", "bool", "list",
                         "matrix", "dict"];

    function _attachCrudHover(container) {
        container._crudEnabled = true;      // re-checked per hover: a re-render
        if (container._crudHover) return;   // without crud must disable actions
        container._crudHover = true;
        container.addEventListener("mouseover", function (e) {
            if (!container._crudEnabled) return;
            var row = e.target.closest ? e.target.closest(".tree-row") : null;
            if (!row || row.querySelector(":scope > .tree-row-actions")) return;
            var node = row.closest(".tree-node");
            if (!node || !node._meta || !node._meta.path) return;
            _buildRowActions(container, node, row);
        });
    }

    function _parentInfo(node) {
        var pn = node.parentElement ? node.parentElement.closest(".tree-node") : null;
        if (pn && pn._meta) return { node: pn, value: pn._value };
        var c = node.closest(".json-tree");
        return { node: null, value: c ? c._treeData : null };
    }

    function _mkBtn(txt, title, cls, onclick) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "tree-act-btn " + (cls || "");
        b.textContent = txt;
        b.title = title;
        b.onclick = function (e) { e.stopPropagation(); onclick(b); };
        return b;
    }

    function _copyClipboardFallback(txt) {
        try {
            var ta = document.createElement("textarea");
            ta.value = txt; ta.style.position = "fixed"; ta.style.opacity = "0";
            document.body.appendChild(ta); ta.select();
            var ok = document.execCommand("copy");
            ta.remove(); return ok;
        } catch (e) { return false; }
    }

    /* docs/126 ④ — copy this row (key + value, as a JSON snippet) to the
       system clipboard. Distinct from the in-app paste buffer (_treeCopyKey):
       this hands the text to the OS for pasting anywhere. */
    function _copyKeyValue(node, btn) {
        var m = node._meta, v = node._value, txt;
        try {
            txt = (m && m.key != null && m.key !== "")
                ? JSON.stringify(String(m.key)) + ": " + JSON.stringify(v, null, 2)
                : JSON.stringify(v, null, 2);
        } catch (e) { txt = String(v); }
        function done(okFlag) {
            if (!btn) return;
            var old = btn.textContent;
            btn.textContent = okFlag ? "✓" : "✗";
            setTimeout(function () { btn.textContent = old; }, 800);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(txt).then(
                function () { done(true); },
                function () { done(_copyClipboardFallback(txt)); });
        } else { done(_copyClipboardFallback(txt)); }
    }

    function _buildRowActions(container, node, row) {
        var m = node._meta, v = node._value;
        var parent = _parentInfo(node);
        var span = document.createElement("span");
        span.className = "tree-row-actions";
        var isDict = v !== null && typeof v === "object" && !Array.isArray(v);
        var isArr = Array.isArray(v);
        var inList = Array.isArray(parent.value);
        var topLevel = m.depth === 0;
        var identity = m.key === "__class__" || m.key === "id";

        // docs/126 ④: EVERY row copies (key + value as JSON) — the customer
        // pointed at the empty gap between the hover actions and asked for it.
        span.appendChild(_mkBtn("⧉",
            "Copy " + (m.key != null && m.key !== "" ? '"' + m.key + '" and its value' : "this value")
            + " as JSON",
            "tree-act-copy", function (b) { _copyKeyValue(node, b); }));

        if (!inList && !identity) {          // elements/identity: value-edit + copy only
            if (isDict) {
                span.appendChild(_mkBtn("＋", "Add a key under " + (m.key || "root"),
                    "tree-act-add", function () { _openAddKey(container, node); }));
            }
            if (!isDict && !isArr) {
                span.appendChild(_mkBtn("⚙", "Expected type of " + m.key,
                    "tree-act-type", function (b) { _openTypePicker(node, row, b); }));
            }
            if (!topLevel) {
                span.appendChild(_mkBtn("✕", "Delete " + m.key,
                    "tree-act-del", function () { _confirmDelete(container, node, row, span); }));
            }
        }
        if (span.children.length) row.appendChild(span);
    }

    function _closeCrudPanels(node) {
        node.querySelectorAll(":scope > .tree-crud-panel").forEach(function (p) { p.remove(); });
    }

    /* -- add key ------------------------------------------------------- */

    function _openAddKey(container, node) {
        _closeCrudPanels(node);
        var m = node._meta;
        var panel = document.createElement("div");
        panel.className = "tree-crud-panel";
        var listId = "crud-keys-" + Math.abs((m.path || "").length) + "-" + Date.now();
        panel.innerHTML =
            '<input class="tree-crud-key" placeholder="new key" list="' + listId + '">' +
            '<datalist id="' + listId + '"></datalist>' +
            '<select class="tree-crud-type">' + _TYPE_CHOICES.map(function (t) {
                return '<option value="' + t + '">' + (t === "infer" ? "type: infer" : t) + "</option>";
            }).join("") + "</select>" +
            '<input class="tree-crud-val" placeholder="value (JSON for lists/dicts)">' +
            '<button type="button" class="btn-sm tree-crud-ok">Add</button>' +
            '<button type="button" class="btn-sm outline tree-crud-cancel">Cancel</button>' +
            '<span class="tree-crud-err"></span>';
        var row = node.querySelector(":scope > .tree-row");
        row.after(panel);
        var keyIn = panel.querySelector(".tree-crud-key");
        var typeSel = panel.querySelector(".tree-crud-type");
        var valIn = panel.querySelector(".tree-crud-val");
        var err = panel.querySelector(".tree-crud-err");
        keyIn.focus();

        // schema-suggested missing keys (warm manifest only) — auto-fills type
        var suggestions = {};
        fetch("/schema/missing-keys?scope=" + encodeURIComponent(m.path))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d.ok || !d.warm) return;
                var dl = panel.querySelector("datalist");
                (d.missing || []).forEach(function (s) {
                    suggestions[s.key] = s;
                    var o = document.createElement("option");
                    o.value = s.key;
                    o.label = s.expected_type + (s.source_class ? " · " + s.source_class : "");
                    dl.appendChild(o);
                });
            }).catch(function () {});
        keyIn.addEventListener("change", function () {
            var s = suggestions[keyIn.value];
            if (!s) return;
            // legacy manifests may still say "number" — map onto the "real" choice
            var t = s.expected_type === "number" ? "real" : s.expected_type;
            if (_TYPE_CHOICES.indexOf(t) >= 0) typeSel.value = t;
            if (s.default !== null && s.default !== undefined && valIn.value === "") {
                valIn.value = typeof s.default === "string" ? s.default : JSON.stringify(s.default);
            }
        });

        function submit() {
            var key = keyIn.value.trim();
            if (!key) { err.textContent = "key required"; return; }
            var body = new URLSearchParams();
            var dotPath = (m.path ? m.path + "." : "") + key;
            body.append("dot_path", dotPath);
            body.append("value", valIn.value);
            body.append("expect_type", typeSel.value);
            body.append("expect_chip", window.__chipToken || "");
            fetch("/field/create", { method: "POST",
                headers: {"Content-Type": "application/x-www-form-urlencoded"},
                body: body.toString() })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d.ok) { err.textContent = d.error || "create failed"; return; }
                // pull the committed value (server truth) and rebuild this node
                fetch("/field/peek?dot_path=" + encodeURIComponent(dotPath))
                    .then(function (r) { return r.json(); })
                    .then(function (p) {
                        node._value[key] = p.values ? p.values[dotPath] : null;
                        var fresh = _rebuildNode(node, node._value);
                        if (fresh) {
                            var fr = fresh.querySelector(":scope > .tree-row");
                            if (fr) fr.classList.add("tree-row-pending");
                            // materialise + open so the just-added key is visible
                            var tg = fresh.querySelector(":scope > .tree-row > .tree-toggle.collapsed");
                            if (tg) tg.click();
                        }
                    }).catch(function () {});
                if (d.tray_html) { _swapPendingTray(d.tray_html); window._restoreTrayState && window._restoreTrayState(); }
                if (window._diagChanged) window._diagChanged();
            })
            .catch(function () { err.textContent = "request failed"; });
        }
        panel.querySelector(".tree-crud-ok").onclick = submit;
        panel.querySelector(".tree-crud-cancel").onclick = function () { panel.remove(); };
        panel.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && e.target !== valIn) { e.preventDefault(); submit(); }
            if (e.key === "Enter" && e.target === valIn) { e.preventDefault(); submit(); }
            if (e.key === "Escape") panel.remove();
        });
    }

    /* -- delete -------------------------------------------------------- */

    function _countLeaves(v) {
        if (v === null || typeof v !== "object") return 1;
        var n = 0;
        if (Array.isArray(v)) { return 1; }
        Object.keys(v).forEach(function (k) { n += _countLeaves(v[k]); });
        return n || 1;
    }

    function _confirmDelete(container, node, row, actionsSpan) {
        var m = node._meta;
        actionsSpan.innerHTML = "";
        var label = document.createElement("span");
        label.className = "tree-del-confirm";
        label.textContent = "delete " + m.key + " (" + _countLeaves(node._value) +
            " leaves, refs: …)? ";
        actionsSpan.appendChild(label);
        fetch("/field/refs?dot_path=" + encodeURIComponent(m.path))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.ok) label.textContent = label.textContent.replace("refs: …",
                    d.total + " pointer ref" + (d.total === 1 ? "" : "s"));
            }).catch(function () {});
        actionsSpan.appendChild(_mkBtn("Delete", "confirm", "tree-act-del", function () {
            var body = new URLSearchParams();
            body.append("dot_path", m.path);
            body.append("expect_chip", window.__chipToken || "");
            fetch("/field/delete", { method: "POST",
                headers: {"Content-Type": "application/x-www-form-urlencoded"},
                body: body.toString() })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d.ok) { _showEditError(row, d.error); actionsSpan.remove(); return; }
                var parent = _parentInfo(node);
                if (parent.value && typeof parent.value === "object") {
                    delete parent.value[m.key];
                }
                if (parent.node) {
                    _rebuildNode(parent.node, parent.node._value);
                } else {
                    node.remove();
                }
                if (d.dangling_refs > 0 && window.showToast) {
                    window.showToast("Deleted — " + d.dangling_refs +
                        " pointer(s) now dangle (see Diagnostics).", "warning");
                }
                if (d.tray_html) { _swapPendingTray(d.tray_html); window._restoreTrayState && window._restoreTrayState(); }
                if (window._diagChanged) window._diagChanged();
            })
            .catch(function () { actionsSpan.remove(); });
        }));
        actionsSpan.appendChild(_mkBtn("Cancel", "keep", "", function () {
            actionsSpan.remove();
        }));
    }

    /* -- type picker ---------------------------------------------------- */

    function _openTypePicker(node, row, anchorBtn) {
        _closeCrudPanels(node);
        var m = node._meta;
        var panel = document.createElement("div");
        panel.className = "tree-crud-panel tree-type-panel";
        panel.innerHTML =
            '<div class="tree-type-head muted">loading expected type…</div>' +
            '<div class="tree-type-opts">' +
            ["int", "real", "str", "bool", "list", "matrix", "dict"].map(function (t) {
                return '<label><input type="radio" name="tp" value="' + t + '"> ' + t + "</label>";
            }).join("") + "</div>" +
            '<button type="button" class="btn-sm tree-type-assign">Assign</button>' +
            '<button type="button" class="btn-sm outline tree-type-clear">Clear override</button>' +
            '<button type="button" class="btn-sm outline tree-type-close">Close</button>' +
            '<span class="tree-crud-err"></span>';
        row.after(panel);
        var head = panel.querySelector(".tree-type-head");
        var err = panel.querySelector(".tree-crud-err");
        fetch("/field/peek?dot_path=" + encodeURIComponent(m.path))
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var e = d.expected && d.expected[m.path];
                if (!e) { head.textContent = "no expected type — assign one to make this key type-safe"; return; }
                head.textContent = "expected: " + e.type + " · " + e.source +
                    (e.class_path ? " (" + e.class_path.split(".").pop() + "." + e.field + ")" : "") +
                    (e.detail ? " — " + e.detail : "");
            }).catch(function () { head.textContent = "expected type unavailable"; });

        function post(override) {
            var sel = panel.querySelector('input[name="tp"]:checked');
            if (!sel) { err.textContent = "pick a type"; return; }
            var body = new URLSearchParams();
            body.append("dot_path", m.path);
            body.append("type", sel.value);
            if (override) body.append("override_env", "1");
            body.append("expect_chip", window.__chipToken || "");
            fetch("/field/type-assign", { method: "POST",
                headers: {"Content-Type": "application/x-www-form-urlencoded"},
                body: body.toString() })
            .then(function (r) { return r.json().then(function (d) { return {s: r.status, d: d}; }); })
            .then(function (res) {
                if (res.s === 409 && res.d.error_kind === "env_conflict") {
                    if (window.confirm("The env schema types this key as " +
                            (res.d.env_type && res.d.env_type.type) +
                            ". Override it with " + sel.value + "?")) post(true);
                    return;
                }
                if (!res.d.ok) { err.textContent = res.d.error || "assign failed"; return; }
                if (res.d.warning && window.showToast) window.showToast(res.d.warning, "warning");
                panel.remove();
                if (window.showToast) window.showToast("Type assigned: " + sel.value, "success");
            })
            .catch(function () { err.textContent = "request failed"; });
        }
        panel.querySelector(".tree-type-assign").onclick = function () { post(false); };
        panel.querySelector(".tree-type-clear").onclick = function () {
            var body = new URLSearchParams();
            body.append("dot_path", m.path);
            fetch("/field/type-unassign", { method: "POST",
                headers: {"Content-Type": "application/x-www-form-urlencoded"},
                body: body.toString() })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                panel.remove();
                if (window.showToast) window.showToast(
                    d.removed ? "Override cleared" : "No override was set", "info");
            }).catch(function () {});
        };
        panel.querySelector(".tree-type-close").onclick = function () { panel.remove(); };
        panel.addEventListener("keydown", function (e) {
            if (e.key === "Escape") panel.remove();
        });
    }

    // The live-diff IIFE's ✓-accept handler repaints a value element this
    // renderer built, and must format it the way this renderer would — its
    // bare `_formatValue` call was a ReferenceError that fired AFTER the edit
    // landed, killing the pending mark / count / tray swap while the value was
    // already staged (docs/124 C-1, second layer — found by the pin the first
    // layer's fix added).
    window._formatValue = _formatValue;

    // The tree's own value formatter, for in-place leaf patches after a sync
    // pull (LiveSurfacePatch) — one formatting rule, not a second copy.
    window._treeFormatValue = _formatValue;
    window.renderJsonTree = function(containerId, data, options) {
        var container = document.getElementById(containerId);
        if (!container) return;
        // A full (re)render is a new context (chip load / switch) — drop any stale
        // key-copy so its paste buttons don't linger against a different chip.
        _clearTreeCopy();
        container.innerHTML = "";
        container.className = "json-tree";

        options = options || {};
        container._keyHelp = !!options.crud;   // the ? rows follow the live-state (crud) trees
        _keyHelpOn = container._keyHelp;
        var refData = options.refData || null;
        var defaultDepth = options.defaultDepth !== undefined ? options.defaultDepth : 1;
        var hasDiff = !!refData;
        // Compare BOTH sides' key sets, so keys only one side has still render
        // (docs/84). Opt-in: the live-diff overlay is a before→after of one
        // document and keeps its exact behaviour.
        var union = !!options.union;
        // "edit" (default) keeps the existing live-state behavior; "copy" makes
        // scalar values click-to-copy for read-only trees (dataset params/results).
        var valueClick = options.valueClick || "edit";

        if (typeof data === "object" && data !== null && !Array.isArray(data)) {
            var keys = _unionKeys(data, refData, union, hasDiff);
            for (var i = 0; i < keys.length; i++) {
                var refVal = (hasDiff && refData && typeof refData === "object") ? refData[keys[i]] : undefined;
                var own = Object.prototype.hasOwnProperty.call(data, keys[i]);
                container.appendChild(_buildNode(
                    keys[i], own ? data[keys[i]] : _ABSENT, keys[i], 0, refVal,
                    hasDiff, valueClick, union));
            }
        } else {
            container.appendChild(_buildNode(null, data, "", 0, refData, hasDiff, valueClick, union));
        }

        // Stash the source object so search runs against data (not the DOM).
        // innerHTML was wiped above, so any prior index/state is now invalid.
        container._treeData = data;
        container._flatIndex = null;
        container._lastSearchQuery = undefined;

        // Explorer trees opt into structural CRUD (add/delete key, type
        // picker) — never the read-only copy/diff trees. The flag is
        // re-stamped on EVERY render so a non-crud re-render disables it.
        container._crudEnabled = false;
        if (options.crud) _attachCrudHover(container);

        if (defaultDepth >= 99) {
            _expandAll(container);
        } else {
            _expandToDepth(container, defaultDepth);
        }
    };

    window.jsonTreeExpandToDepth = function(containerId, depth) {
        var c = document.getElementById(containerId);
        if (c) _expandToDepth(c, depth);
    };

    window.jsonTreeCollapseAll = function(containerId) {
        var c = document.getElementById(containerId);
        if (c) _collapseAll(c);
    };

    window.jsonTreeExpandAll = function(containerId) {
        var c = document.getElementById(containerId);
        if (c) _expandAll(c);
    };

    /* docs/122 item 2 — expansion is state the user built by hand, and every
       /explorer rebuild threw it away because nothing anywhere recorded it.
       Addressed by dot-path, never by DOM index: the rebuilt tree is a
       different document, and an index would restore the wrong nodes rather
       than none. Bounded, because a fully expanded 20-qubit chip is ~7,800 rows
       and a restore that walked all of them would cost more than the rebuild it
       is repairing. */
    var _EXPAND_CAP = 1200;
    window.jsonTreeExpandedPaths = function(containerId) {
        var c = document.getElementById(containerId);
        if (!c) return [];
        var out = [], nodes = c.querySelectorAll('.tree-node[data-path]');
        for (var i = 0; i < nodes.length && out.length < _EXPAND_CAP; i++) {
            var t = nodes[i].querySelector(':scope > .tree-row > .tree-toggle');
            if (t && !t.classList.contains('collapsed')) {
                out.push(nodes[i].getAttribute('data-path'));
            }
        }
        return out;
    };
    window.jsonTreeSetExpanded = function(containerId, paths) {
        var c = document.getElementById(containerId);
        if (!c || !paths || !paths.length) return 0;
        // Shallowest first: a child node does not exist in the DOM until its
        // parent has been expanded, so depth order is what makes one pass enough.
        var sorted = paths.slice().sort(function (a, b) {
            return a.split('.').length - b.split('.').length || (a < b ? -1 : 1);
        });
        var n = 0;
        for (var i = 0; i < sorted.length; i++) {
            var node = c.querySelector('.tree-node[data-path="' + sorted[i] + '"]');
            if (!node) continue;
            var t = node.querySelector(':scope > .tree-row > .tree-toggle');
            if (t && t.classList.contains('collapsed')) { t.click(); n++; }
        }
        return n;
    };

    window.jsonTreeSearch = function(containerId, query) {
        _debounce('tree-search-' + containerId, function() {
            var c = document.getElementById(containerId);
            if (c) _searchTree(c, query);
        }, 200);
    };
})();

/* ------------------------------------------------------------------ */
/* Unified Tree Viewer (multi-state comparison)                        */
/* ------------------------------------------------------------------ */

(function() {
    var _POINTER_RE = /^#(\/|\.\/|\.\.\/)/;
    var _LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";

    function _isPointer(v) {
        return typeof v === "string" && _POINTER_RE.test(v);
    }

    function _typeOf(v) {
        if (v === null) return "null";
        if (Array.isArray(v)) return "array";
        return typeof v;
    }

    function _fmtVal(v) {
        if (v === null) return "null";
        if (typeof v === "boolean") return v ? "true" : "false";
        if (typeof v === "string") return '"' + v + '"';
        if (typeof v === "number") return _groupDigits(v);
        return String(v);
    }

    function _deepEqual(a, b) {
        if (a === b) return true;
        if (a === null || b === null) return false;
        if (typeof a !== typeof b) return false;
        if (typeof a !== "object") return false;
        var isArrA = Array.isArray(a), isArrB = Array.isArray(b);
        if (isArrA !== isArrB) return false;
        if (isArrA) {
            if (a.length !== b.length) return false;
            for (var i = 0; i < a.length; i++) {
                if (!_deepEqual(a[i], b[i])) return false;
            }
            return true;
        }
        var keysA = Object.keys(a), keysB = Object.keys(b);
        if (keysA.length !== keysB.length) return false;
        for (var k = 0; k < keysA.length; k++) {
            if (!(keysA[k] in b)) return false;
            if (!_deepEqual(a[keysA[k]], b[keysA[k]])) return false;
        }
        return true;
    }

    function _allEqual(values) {
        for (var i = 1; i < values.length; i++) {
            if (!_deepEqual(values[0], values[i])) return false;
        }
        return true;
    }

    function _mergeKeys(objects) {
        var seen = {};
        var result = [];
        for (var i = 0; i < objects.length; i++) {
            if (objects[i] && typeof objects[i] === "object" && !Array.isArray(objects[i])) {
                var keys = Object.keys(objects[i]);
                for (var j = 0; j < keys.length; j++) {
                    if (!seen[keys[j]]) {
                        seen[keys[j]] = true;
                        result.push(keys[j]);
                    }
                }
            }
        }
        return result;
    }

    function _maxArrayLen(values) {
        var max = 0;
        for (var i = 0; i < values.length; i++) {
            if (Array.isArray(values[i]) && values[i].length > max) {
                max = values[i].length;
            }
        }
        return max;
    }

    function _fmtDelta(delta) {
        var a = Math.abs(delta);
        if (a >= 1e6 || (a > 0 && a < 1e-3)) return delta.toExponential(3);
        return delta.toFixed(6);
    }

    function _buildUnifiedNode(key, values, path, depth, labels, refIndex) {
        var defined = values.filter(function(v) { return v !== undefined; });
        if (defined.length === 0) return null;

        var representative = defined[0];
        var type = _typeOf(representative);
        var isContainer = (type === "object" || type === "array");
        var allSame = _allEqual(values);

        var node = document.createElement("div");
        node.className = "tree-node";
        node.setAttribute("data-depth", depth);
        node.setAttribute("data-path", path);

        if (!allSame) {
            node.classList.add("tree-diff");
        }

        var row = document.createElement("div");
        row.className = "tree-row";

        if (isContainer) {
            var toggle = document.createElement("span");
            toggle.className = "tree-toggle collapsed";
            toggle.textContent = "\u25B6";
            toggle.onclick = function() { _uToggleNode(node); };
            row.appendChild(toggle);
        } else {
            var spacer = document.createElement("span");
            spacer.className = "tree-toggle-spacer";
            row.appendChild(spacer);
        }

        if (key !== null) {
            var keyEl = document.createElement("span");
            keyEl.className = "tree-key";
            keyEl.textContent = key;
            keyEl.title = "Click to copy path: " + path;
            keyEl.onclick = function() {
                navigator.clipboard.writeText(path);
                keyEl.classList.add("tree-copied");
                setTimeout(function() { keyEl.classList.remove("tree-copied"); }, 800);
            };
            row.appendChild(keyEl);

            var colon = document.createElement("span");
            colon.className = "tree-colon";
            colon.textContent = ": ";
            row.appendChild(colon);
        }

        if (isContainer) {
            var allObjects = defined.every(function(v) { return _typeOf(v) === "object"; });
            var allArrays = defined.every(function(v) { return _typeOf(v) === "array"; });

            if (allSame) {
                var summary = document.createElement("span");
                summary.className = "tree-summary";
                if (type === "object") {
                    var n = Object.keys(representative).length;
                    summary.textContent = "{" + n + " key" + (n !== 1 ? "s" : "") + "}";
                } else {
                    summary.textContent = "[" + representative.length + " item" + (representative.length !== 1 ? "s" : "") + "]";
                }
                row.appendChild(summary);
            } else {
                var summary2 = document.createElement("span");
                summary2.className = "tree-summary tree-summary-diff";
                if (allObjects) {
                    var counts = defined.map(function(v) { return Object.keys(v).length; });
                    summary2.textContent = "{" + counts.join("/") + " keys}";
                } else if (allArrays) {
                    var lens = defined.map(function(v) { return v.length; });
                    summary2.textContent = "[" + lens.join("/") + " items]";
                } else {
                    summary2.textContent = "(mixed types)";
                }
                row.appendChild(summary2);
            }

            var children = document.createElement("div");
            children.className = "tree-children";
            children.style.display = "none";
            var childHasDiff = false;

            if (allObjects) {
                var mergedKeys = _mergeKeys(defined);
                for (var i = 0; i < mergedKeys.length; i++) {
                    var childKey = mergedKeys[i];
                    var childPath = path ? path + "." + childKey : childKey;
                    var childValues = values.map(function(v) {
                        return (v && typeof v === "object" && !Array.isArray(v) && childKey in v)
                            ? v[childKey] : undefined;
                    });
                    var childNode = _buildUnifiedNode(childKey, childValues, childPath, depth + 1, labels, refIndex);
                    if (childNode) {
                        children.appendChild(childNode);
                        if (childNode.classList.contains("tree-diff") || childNode.classList.contains("tree-has-diff")) {
                            childHasDiff = true;
                        }
                    }
                }
            } else if (allArrays) {
                var maxLen = _maxArrayLen(defined);
                for (var j = 0; j < maxLen; j++) {
                    var itemPath = path + "." + j;   // dot-form numeric segments everywhere
                    var itemValues = values.map(function(v) {
                        return Array.isArray(v) && j < v.length ? v[j] : undefined;
                    });
                    var itemNode = _buildUnifiedNode(String(j), itemValues, itemPath, depth + 1, labels, refIndex);
                    if (itemNode) {
                        children.appendChild(itemNode);
                        if (itemNode.classList.contains("tree-diff") || itemNode.classList.contains("tree-has-diff")) {
                            childHasDiff = true;
                        }
                    }
                }
            }

            if (childHasDiff) {
                node.classList.add("tree-has-diff");
            }

            node.appendChild(row);
            node.appendChild(children);
        } else {
            if (allSame) {
                var valEl = document.createElement("span");
                var valClass = "tree-val tree-val-" + _typeOf(representative);
                if (_isPointer(representative)) valClass = "tree-val tree-val-pointer";
                valEl.className = valClass;
                valEl.textContent = _fmtVal(representative);
                if (_isPointer(representative)) valEl.title = "Pointer: " + representative;
                row.appendChild(valEl);
            } else {
                var multiVal = document.createElement("span");
                multiVal.className = "tree-multi-val";
                var refVal = (refIndex >= 0 && refIndex < values.length) ? values[refIndex] : undefined;

                for (var m = 0; m < values.length; m++) {
                    var badge = document.createElement("span");
                    badge.className = "tree-state-badge";
                    badge.setAttribute("data-idx", m);
                    badge.textContent = _LETTERS[m] || String(m);
                    multiVal.appendChild(badge);

                    if (values[m] === undefined) {
                        var missing = document.createElement("span");
                        missing.className = "tree-val-missing";
                        missing.textContent = "--";
                        multiVal.appendChild(missing);
                    } else {
                        var vSpan = document.createElement("span");
                        var vt = _typeOf(values[m]);
                        var vc = "tree-val tree-val-" + vt;
                        if (_isPointer(values[m])) vc = "tree-val tree-val-pointer";
                        vSpan.className = vc;
                        vSpan.textContent = _fmtVal(values[m]);
                        multiVal.appendChild(vSpan);

                        if (refIndex >= 0 && refVal !== undefined) {
                            if (m === refIndex) {
                                var refTag = document.createElement("span");
                                refTag.className = "tree-ref-tag";
                                refTag.textContent = "(REF)";
                                multiVal.appendChild(refTag);
                            } else if (typeof values[m] === "number" && typeof refVal === "number") {
                                var delta = values[m] - refVal;
                                var dEl = document.createElement("span");
                                if (delta > 0) {
                                    dEl.className = "tree-delta-up";
                                    dEl.textContent = "(+" + _fmtDelta(delta) + " \u2191)";
                                } else if (delta < 0) {
                                    dEl.className = "tree-delta-down";
                                    dEl.textContent = "(" + _fmtDelta(delta) + " \u2193)";
                                } else {
                                    dEl.className = "tree-delta-same";
                                    dEl.textContent = "(= \u2194)";
                                }
                                multiVal.appendChild(dEl);
                            } else if (_deepEqual(values[m], refVal)) {
                                var sameTag = document.createElement("span");
                                sameTag.className = "tree-delta-same";
                                sameTag.textContent = "(= \u2194)";
                                multiVal.appendChild(sameTag);
                            }
                        }
                    }

                    if (m < values.length - 1) {
                        var sep = document.createElement("span");
                        sep.className = "tree-multi-sep";
                        sep.textContent = " ";
                        multiVal.appendChild(sep);
                    }
                }
                row.appendChild(multiVal);
            }

            node.appendChild(row);
        }

        // Cache the node's own row text once for fast, repeat-free search.
        // (row holds key + value/summary; children live in a separate element.)
        node._searchText = row.textContent.toLowerCase();

        return node;
    }

    function _uToggleNode(nodeEl) {
        var children = nodeEl.querySelector(":scope > .tree-children");
        var toggle = nodeEl.querySelector(":scope > .tree-row > .tree-toggle");
        if (!children || !toggle) return;
        var collapsed = children.style.display === "none";
        children.style.display = collapsed ? "" : "none";
        toggle.textContent = collapsed ? "\u25BC" : "\u25B6";
        toggle.classList.toggle("collapsed", !collapsed);
        toggle.classList.toggle("expanded", collapsed);
    }

    window.renderUnifiedTree = function(containerId, datasets, options) {
        var container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = "";
        container.className = "json-tree";

        options = options || {};
        var defaultDepth = options.defaultDepth !== undefined ? options.defaultDepth : 1;
        var refIndex = options.refIndex !== undefined ? options.refIndex : -1;

        container._uDatasets = datasets;
        container._uOptions = options;

        // Unified tree is eagerly built and has no single source object, so it
        // uses the DOM-fallback search path (see _searchTree).
        container._treeData = null;
        container._flatIndex = null;
        container._lastSearchQuery = undefined;

        var labels = datasets.map(function(d) { return d.label; });
        var allData = datasets.map(function(d) { return d.data; });

        var allObjects = allData.every(function(d) { return d && typeof d === "object" && !Array.isArray(d); });
        if (allObjects) {
            var mergedKeys = _mergeKeys(allData);
            for (var i = 0; i < mergedKeys.length; i++) {
                var key = mergedKeys[i];
                var values = allData.map(function(d) {
                    return (d && key in d) ? d[key] : undefined;
                });
                var node = _buildUnifiedNode(key, values, key, 0, labels, refIndex);
                if (node) container.appendChild(node);
            }
        }

        var c = container;
        var nodes = c.querySelectorAll(".tree-node");
        for (var j = 0; j < nodes.length; j++) {
            var d = parseInt(nodes[j].getAttribute("data-depth"), 10);
            var ch = nodes[j].querySelector(":scope > .tree-children");
            var tg = nodes[j].querySelector(":scope > .tree-row > .tree-toggle");
            if (!ch || !tg) continue;
            if (d < defaultDepth) {
                ch.style.display = "";
                tg.textContent = "\u25BC";
                tg.classList.remove("collapsed");
                tg.classList.add("expanded");
            }
        }
    };

    window.toggleUnifiedDiffOnly = function(containerId) {
        var container = document.getElementById(containerId);
        if (!container) return;
        var active = container.classList.toggle("diff-only-active");

        var btn = document.getElementById("full-cmp-diff-only");
        if (btn) btn.classList.toggle("active", active);

        if (active) {
            var nodes = container.querySelectorAll(".tree-node");
            for (var i = 0; i < nodes.length; i++) {
                var hasDiff = nodes[i].classList.contains("tree-diff") || nodes[i].classList.contains("tree-has-diff");
                if (!hasDiff) {
                    nodes[i].classList.add("diff-only-hidden");
                } else {
                    nodes[i].classList.remove("diff-only-hidden");
                    var ch = nodes[i].querySelector(":scope > .tree-children");
                    var tg = nodes[i].querySelector(":scope > .tree-row > .tree-toggle");
                    if (ch && tg && ch.style.display === "none") {
                        ch.style.display = "";
                        tg.textContent = "\u25BC";
                        tg.classList.remove("collapsed");
                        tg.classList.add("expanded");
                    }
                }
            }
        } else {
            var all = container.querySelectorAll(".diff-only-hidden");
            for (var k = 0; k < all.length; k++) {
                all[k].classList.remove("diff-only-hidden");
            }
        }
    };
})();

/* ONE canonical sidebar-active sync (docs/126 r3). Three independent setters
 * used to fight: this handler (which compared hrefs WITH their query string
 * against the bare path, so subnav links never toggled and same-href
 * parent+child both lit), chipNavView's manual push/replaceState (which fires
 * no htmx history event, so nothing ever CLEARED the previous menu), and
 * chip-status's _setActiveTab (its own group only). Every navigation now
 * clears everything and re-derives the active set from the URL. */
window.syncSidebarNavActive = function() {
    var path = window.location.pathname;
    // The diff family is ONE destination in the sidebar: /diff/versions and
    // /diff/snapshots are entry routes into the same Compare surface, and the
    // server's own full render marks Compare for page="diff" — the client
    // matcher must agree or a Versions-panel Compare press leaves the
    // PREVIOUS page's item lit (customer, 2026-08-22).
    if (path.indexOf('/diff/') === 0) path = '/diff';
    var view = null;
    try { view = new URLSearchParams(window.location.search).get("view"); } catch (e) {}
    var matches = [];
    document.querySelectorAll(".sidebar-nav a[href]").forEach(function(a) {
        a.classList.remove("active");
        var href = a.getAttribute("href") || "";
        var q = href.indexOf("?");
        var hPath = q < 0 ? href : href.slice(0, q);
        if (hPath !== path) return;
        var hView = null;
        if (q >= 0) { try { hView = new URLSearchParams(href.slice(q)).get("view"); } catch (e) {} }
        // a view-scoped link matches its own view; bare /topology means the
        // page's first section (the spy moves the subnav highlight later)
        if (hView && view && hView !== view) return;
        if (hView && !view && hView !== "topology") return;
        matches.push({ a: a, href: href, sub: !!a.closest(".nav-subitems") });
    });
    // same-href parent+child (Chip Components + Qubits are both /qubits):
    // the child owns the highlight — base.html: "Parent deliberately carries
    // no active class". Distinct hrefs (Chip Status + its ?view= child) keep
    // both, matching the server's own full-load render.
    matches.forEach(function(m) {
        var twin = matches.some(function(o) { return o !== m && o.href === m.href && o.sub; });
        if (!(m.sub === false && twin)) m.a.classList.add("active");
    });
    // Bring the active item into the sidebar's own scrollport (the sidebar
    // is overflow-y:auto; a Compare press from the topbar can land on an item
    // scrolled out of sight). block:'nearest' keeps this a sidebar-only
    // scroll — it never moves the page.
    var act = document.querySelector('.sidebar-nav a.active');
    if (act && act.scrollIntoView) {
        try { act.scrollIntoView({ block: 'nearest' }); } catch (e) {}
    }
};
/* Workspace Refresh feedback (docs/126 r3, second round): the CSS-only
 * .htmx-request spin is invisible on a SMALL workspace — the rescan settles
 * in milliseconds, one or two frames of animation. So the press itself arms
 * a spin that lasts at least 700 ms (or the real request, whichever is
 * longer), then flashes a ✓ for a second. A press is now always seen. */
(function () {
    /* Round 4 (user: the ring still does not READ as moving): rotation is now
       driven by rAF in JS — immune to any environment that freezes CSS
       animations — and the rotating shape is a HALF-FILLED disc, whose sweep
       is unmistakable at any size (a quadrant ring at 13px was not). */
    function _wsSpin(b, on) {
        var ico = b.querySelector('.ws-refresh-ico');
        if (!ico) return;
        if (on) {
            if (b._wsRaf) return;
            ico.textContent = '◐';                 // the half-filled disc
            var step = function (ts) {
                ico.style.transform = 'rotate(' + ((ts / 2) % 360) + 'deg)';
                b._wsRaf = requestAnimationFrame(step);
            };
            b._wsRaf = requestAnimationFrame(step);
        } else {
            if (b._wsRaf) cancelAnimationFrame(b._wsRaf);
            b._wsRaf = null;
            ico.style.transform = '';
            ico.textContent = '↻';                 // the resting glyph
        }
    }
    document.addEventListener('click', function (e) {
        var b = e.target && e.target.closest && e.target.closest('.btn-workspace-refresh');
        if (!b) return;
        b.classList.remove('ws-done');
        b.classList.add('ws-kick');
        b._wsT0 = Date.now();
        _wsSpin(b, true);
    });
    document.addEventListener('htmx:afterRequest', function (evt) {
        var b = evt.detail && evt.detail.elt;
        if (!b || !b.classList || !b.classList.contains('btn-workspace-refresh')) return;
        var wait = Math.max(0, 700 - (Date.now() - (b._wsT0 || 0)));
        setTimeout(function () {
            _wsSpin(b, false);
            b.classList.remove('ws-kick');
            b.classList.add('ws-done');
            setTimeout(function () { b.classList.remove('ws-done'); }, 1100);
        }, wait);
    });
})();

document.addEventListener("htmx:pushedIntoHistory", window.syncSidebarNavActive);
document.addEventListener("htmx:replacedInHistory", window.syncSidebarNavActive);
window.addEventListener("popstate", function() { setTimeout(window.syncSidebarNavActive, 0); });

/* NavProgress (docs/126 r3) — the brand-area loading indicator. Counts
 * in-flight #table-pane requests via htmx's own events; shows after 400 ms
 * (fast navigations never flash) with an elapsed-seconds counter, and hides
 * when the LAST one settles. A WeakSet dedups the settle events — htmx can
 * fire more than one terminal event for the same xhr (afterRequest +
 * responseError; sendAbort under hx-sync replace). */
window.NavProgress = (function () {
    var count = 0, t0 = 0, showTimer = null, tick = null, poll = null;
    var seen = (typeof WeakSet !== 'undefined') ? new WeakSet() : null;
    var ext = null;        // {label, done, total} pushed by a background job
    var srv = null;        // newest /api/progress answer while visible
    function el() { return document.getElementById('nav-progress'); }
    function isPane(evt) {
        var t = evt.detail && evt.detail.target;
        return !!(t && t.id === 'table-pane');
    }
    function _render() {
        var p = el(); if (!p) return;
        var timeEl = p.querySelector('.nav-progress-time');
        if (!timeEl) return;
        // real counts beat elapsed time (docs/126 r3 follow-up: "12/1000 →
        // 24/1000…") — shown ONLY when a loop actually reports them
        var op = ext || srv;
        if (op && op.total) {
            timeEl.textContent = op.done + '/' + op.total;
            p.title = op.label || '';
        } else {
            timeEl.textContent = ((Date.now() - t0) / 1000).toFixed(1) + ' s';
            p.title = '';
        }
    }
    function show() {
        var p = el(); if (!p) return;
        p.hidden = false;
        clearInterval(tick);
        tick = setInterval(_render, 100);
        // while visible, ask the server whether a loop is reporting real
        // counts — hidden means no polling, so an idle app pays nothing
        clearInterval(poll);
        poll = setInterval(function () {
            fetch('/api/progress').then(function (r) { return r.json(); })
                .then(function (j) { srv = (j && j.total) ? j : null; })
                .catch(function () { srv = null; });
        }, 350);
        _render();
    }
    function hide() {
        clearTimeout(showTimer); showTimer = null;
        clearInterval(tick); tick = null;
        clearInterval(poll); poll = null;
        srv = null;
        var p = el(); if (p) { p.hidden = true; p.title = ''; }
    }
    function maybeHide() {
        if (count === 0 && !ext) hide();
    }
    document.addEventListener('htmx:beforeRequest', function (evt) {
        if (!isPane(evt)) return;
        count++;
        if (count === 1 && !ext) {
            t0 = Date.now();
            clearTimeout(showTimer);
            showTimer = setTimeout(show, 400);
        }
    });
    function settle(evt) {
        if (!isPane(evt)) return;
        var x = evt.detail && evt.detail.xhr;
        if (seen && x) {
            if (seen.has(x)) return;
            seen.add(x);
        }
        count = Math.max(0, count - 1);
        maybeHide();
    }
    ['htmx:afterRequest', 'htmx:sendAbort', 'htmx:sendError',
     'htmx:responseError', 'htmx:timeout'].forEach(function (n) {
        document.addEventListener(n, settle);
    });
    return {
        /* A background job (the param-history backfill poller) pushes its
           own real counts here — the indicator shows without any pane
           request in flight, which is exactly the phase the user watches. */
        external: function (label, done, total) {
            ext = { label: label, done: done || 0, total: total || 0 };
            if (!t0) t0 = Date.now();
            var p = el();
            if (p && p.hidden) show(); else _render();
        },
        externalDone: function () {
            ext = null;
            maybeHide();
        }
    };
})();

/* ------------------------------------------------------------------ */
/* Instrument Wiring Diagram                                           */
/* ------------------------------------------------------------------ */

var _popupElement = null;
var _popupHideTimer = null;

/**
 * Build an SVG chassis diagram with dual sub-columns per FEM (outputs left, inputs right).
 * Colored port circles encode role; hover shows details, dblclick shows raw wiring JSON.
 */
/* ---- instrument rack sizing (docs/135) -------------------------------
 * Two honest presentations of one drawing, never a third silent one:
 *   Fit  — the viewBox scales the WHOLE rack into the host width
 *   1:1  — intrinsic size; the host's `overflow-x:auto` scrolls (full-size
 *          port labels and drag targets)
 * With no recorded preference the default is Fit while it stays legible
 * (see _INSTR_FIT_FLOOR), 1:1 below that. The viewer's own choice always
 * wins and rides localStorage, so it survives the re-renders every wizard
 * edit triggers. This lives inside the renderer, so it applies to EVERY
 * mount (page, wizard, compare, floating panel).
 */
var _INSTR_FIT_KEY = 'quam_instrument_fit';
// Below this the labels inside the port circles stop being readable, so
// scaling the rack down would trade one unusable view for another —
// scrolling a full-size rack is the honest fallback. Measured on the real
// 20Q chip: an 8-FEM rack in a 1194px pane lands at 0.63.
var _INSTR_FIT_FLOOR = 0.55;
// The viewer's choice for THIS page. localStorage carries it to the next one
// but is never the only copy — see _applyInstrumentFit.
var _instrFitChoice = null;

function _applyInstrumentFit(container) {
    _watchInstrumentResize(container);   // armed even while it currently fits
    var svgs = container.querySelectorAll('svg.instrument-svg');
    var widest = 0;
    Array.prototype.forEach.call(svgs, function(svg) {
        widest = Math.max(widest, parseFloat(svg.dataset.natW) || 0);
    });
    var avail = container.clientWidth || widest;
    var scale = widest ? Math.min(1, avail / widest) : 1;
    // The viewer's explicit choice always wins; with none recorded, show the
    // WHOLE rack whenever it stays legible — the complaint this fixes was
    // "half my FEMs aren't there", and a rack the viewer must discover by
    // scrolling answers that only halfway.
    // localStorage is best-effort, never the only copy: a browser with site
    // data blocked (Safari private, Chrome "block all cookies", a webview
    // with no user-data dir) throws on setItem, and a mode kept ONLY in
    // storage makes the toggle silently inert. _instrFitChoice is the truth
    // for this page; storage just carries it to the next one.
    var stored = _instrFitChoice;
    if (stored === null) {
        try { stored = localStorage.getItem(_INSTR_FIT_KEY); } catch (e) { /* blocked */ }
    }
    var fit = stored === '1' ? true
            : stored === '0' ? false
            : scale >= _INSTR_FIT_FLOOR;

    Array.prototype.forEach.call(svgs, function(svg) {
        var w = parseFloat(svg.dataset.natW) || 0;
        var h = parseFloat(svg.dataset.natH) || 0;
        if (!w || !h) return;
        if (fit) {
            // NEVER magnify. `width:100%` on a viewBox'd svg scales UP as
            // happily as down, so a rack narrower than its pane — every chip
            // smaller than the 8-FEM one this was measured on — would be
            // blown up past natural size, in a picture whose whole job is to
            // be a faithful rack. Cap at the drawing's own width.
            svg.style.width = 'min(100%, ' + w + 'px)';
            if (!svg.style.width) svg.style.width = Math.min(avail, w) + 'px';  // no min() support
            svg.style.height = 'auto';
            svg.style.maxWidth = w + 'px';
        } else {
            svg.style.width = w + 'px';
            svg.style.height = h + 'px';
            svg.style.maxWidth = 'none';   // the crop this replaced
        }
    });
    var old = container.querySelector('.iw-fitbar');
    if (old) old.remove();
    // Only speak when the drawing does not fit on its own — an 8-FEM rack in
    // a wide pane needs no chrome.
    if (!svgs.length || widest <= container.clientWidth + 1) return;
    var bar = document.createElement('div');
    bar.className = 'iw-fitbar';
    var note = document.createElement('span');
    note.className = 'iw-fitbar-note';
    note.textContent = fit
        ? 'Whole rack scaled to fit — switch to 1:1 for full-size ports.'
        : '↔ Wider than this pane — scroll sideways, or fit it all in view.';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-xs outline iw-fitbar-btn';
    btn.textContent = fit ? '1:1' : 'Fit width';
    btn.title = fit ? 'Show the rack at full size (scrolls sideways)'
                    : 'Scale the whole rack into this pane';
    btn.addEventListener('click', function() {
        var next = fit ? '0' : '1';
        _instrFitChoice = next;                  // in-memory first: always takes effect
        try {
            localStorage.setItem(_INSTR_FIT_KEY, next);
            // The write landed, so storage is the record again and the
            // in-memory override stops shadowing it — otherwise this page
            // would ignore a change made in another tab for the rest of the
            // session. The override exists exactly while storage cannot hold
            // the value.
            _instrFitChoice = null;
        } catch (e) { /* blocked — the override stays, and the button works */ }
        // Every rack on the page follows one choice, not just this container.
        var hosts = document.querySelectorAll('#instrument-diagram, .gen-wiring-diagram, [id$="-wiring-diagram"]');
        Array.prototype.forEach.call(hosts, function(h) {
            if (h.querySelector('svg.instrument-svg')) _applyInstrumentFit(h);
        });
        if (!container.querySelector('.iw-fitbar')) _applyInstrumentFit(container);
    });
    bar.appendChild(note);
    bar.appendChild(btn);
    container.insertBefore(bar, container.firstChild);
}

/* The rack's host changes width without the window doing so — the sidebar
 * collapses, the split pane is dragged, a wizard panel swaps. Re-decide on
 * the CONTAINER's own resize (the docs/122 lesson: a window-resize listener
 * never sees any of those). One observer per container, and it never
 * re-enters while it is itself resizing. */
function _watchInstrumentResize(container) {
    if (container._iwFitObs || typeof ResizeObserver !== 'function') return;
    var busy = false;
    container._iwFitObs = new ResizeObserver(function() {
        if (busy) return;
        busy = true;
        requestAnimationFrame(function() {
            busy = false;
            if (container.isConnected && container.querySelector('svg.instrument-svg')) {
                _applyInstrumentFit(container);
            }
        });
    });
    container._iwFitObs.observe(container);
}

window.renderInstrumentWiring = function(containerId, data, rawWiring, options) {
    var container = document.getElementById(containerId);
    if (!container) return;
    // editable: tag cells with data-* and skip the inspector click handlers
    // so a caller (the Generate Config wizard) can layer drag-drop on top.
    var editable = !!(options && options.editable);
    // onPortHover(assignment|null): in editable mode, route port hover to a
    // caller-supplied panel instead of the cursor-following popup.
    var onPortHover = options && options.onPortHover;

    // Clear fallback/previous content before rendering
    container.innerHTML = '';

    var roleColors = UI_CONFIG.roleColors;

    // Each FEM: output sub-column (left) + input sub-column (right), plus a
    // DIG sub-column when the chip wires any digital output (QDAC triggers,
    // readout markers) — femW is per-controller, computed below.
    var outSubW = 82, inSubW = 66, digSubW = 66, femGap = 16;
    var rowH = 56, circleR = 21;
    var marginLeft = 40, marginTop = 58, marginBottom = 40;

    var controllers = (data && data.controllers) || {};
    if (Object.keys(controllers).length === 0) {
        // Distinguish a genuinely-unwired chip from one whose ports we couldn't
        // place (OPX+ 5-part refs / Octave opx_output_I/Q) — otherwise both looked
        // like "no wiring", wrongly telling the user their chip is unwired.
        var st = (data && data.stats) || {};
        var unplaceable = st.octave_detected || (st.refs_seen > 0 && !st.refs_placed);
        var msg = unplaceable
            ? ('This chip’s wiring uses a layout this diagram doesn’t render yet '
               + '(e.g. OPX+ or an Octave RF setup)'
               + (st.refs_seen ? ' — ' + st.refs_seen + ' port connection(s) were found but couldn’t be placed on the rack' : '')
               + '.')
            : 'No instrument wiring data found. Load a quam_state with wiring information.';
        var pEl = document.createElement('p');
        pEl.style.cssText = 'padding:1rem;color:var(--pico-muted-color)';
        pEl.textContent = msg;
        container.innerHTML = '';
        container.appendChild(pEl);
        return;
    }

    Object.keys(controllers).forEach(function(ctrlName) {
        var ctrlData = controllers[ctrlName];
        var fems = ctrlData.fems || {};
        // Support both new max_output_port and old max_port key
        var maxOutPort = ctrlData.max_output_port || ctrlData.max_port || 8;
        var femIds = Object.keys(fems).sort(function(a, b) { return parseInt(a) - parseInt(b); });
        if (!femIds.length) return;

        // DIG sub-column: only when this controller wires >=1 digital output.
        // A chip with none renders byte-identically to the pre-digital layout.
        var hasDigital = femIds.some(function(fid) {
            return Object.keys(fems[fid].digital_ports || {}).length > 0;
        });
        var maxDig = 0;
        if (hasDigital) {
            femIds.forEach(function(fid) {
                Object.keys(fems[fid].digital_ports || {}).forEach(function(p) {
                    var n = parseInt(p);
                    if (isFinite(n)) maxDig = Math.max(maxDig, n);
                });
            });
        }
        var femW = outSubW + inSubW + (hasDigital ? digSubW : 0);
        // Both FEM flavors physically carry 8 digital outputs (QM FEM guide),
        // so a visible DIG column shows all 8 slots — grid rows grow to fit.
        var nDig = hasDigital ? Math.max(8, maxDig) : 0;
        var rows = Math.max(maxOutPort, nDig);

        var totalFemW = femIds.length * femW + Math.max(0, femIds.length - 1) * femGap;
        var svgW = marginLeft + totalFemW + 20;
        var svgH = marginTop + rows * rowH + marginBottom;

        var svg = _svgEl('svg');
        svg.setAttribute('width', svgW);
        svg.setAttribute('height', svgH);
        svg.setAttribute('class', 'instrument-svg');
        // docs/135 — NEVER crop the rack. The old `max-width:100%` with NO
        // viewBox shrank the ELEMENT's box while the drawing kept its own
        // coordinates, so every FEM past the container width was painted
        // outside the visible box — and because the element itself fitted,
        // the host's `overflow-x:auto` never produced a scrollbar either.
        // A real 20Q chip's rack is 1884px with the DIG column, 1356 without;
        // the user reported "3 MW + 4 LF" on /instrument and "3 MW + 2 LF,
        // cut off" in the wizard, at their own two window widths — one chip,
        // two different answers, neither of them the truth (3 MW + 5 LF).
        // The viewBox makes the drawing scalable; `_applyInstrumentFit`
        // (defined above) picks scale-to-fit vs intrinsic-size-and-scroll,
        // and neither one ever clips.
        svg.setAttribute('viewBox', '0 0 ' + svgW + ' ' + svgH);
        svg.setAttribute('preserveAspectRatio', 'xMinYMin meet');
        svg.dataset.natW = svgW;
        svg.dataset.natH = svgH;

        // Controller title
        svg.appendChild(_svgText(svgW / 2, 22, ctrlName + ' \u2014 OPX1000 Wiring', 14, '600', '#333', 'middle'));

        // Background rect for the grid area
        var iw = UI_CONFIG.instrumentWiring;
        var bg = _svgEl('rect');
        _svgAttrs(bg, {
            x: marginLeft, y: marginTop - 16,
            width: totalFemW, height: rows * rowH + 16,
            fill: iw.gridBg, stroke: iw.gridBorder, rx: 4
        });
        svg.appendChild(bg);

        // Row number labels on the left margin
        for (var rn = 1; rn <= rows; rn++) {
            svg.appendChild(_svgText(
                marginLeft - 6, marginTop + (rn - 1) * rowH + rowH / 2 + 4,
                rn, 10, '400', iw.rowLabelColor, 'end'
            ));
        }

        femIds.forEach(function(femId, colIdx) {
            var femData = fems[femId];
            // Support both new (output_ports/input_ports) and old (ports) data shape
            var outPorts = femData.output_ports || femData.ports || {};
            var inPorts  = femData.input_ports  || {};
            var femX  = marginLeft + colIdx * (femW + femGap);
            var outCx = femX + outSubW / 2;
            var inCx  = femX + outSubW + inSubW / 2;
            var digCx = femX + outSubW + inSubW + digSubW / 2;

            // Solid separator between FEMs
            if (colIdx > 0) {
                var sep = _svgEl('line');
                _svgAttrs(sep, {
                    x1: femX - femGap / 2, y1: marginTop - 16,
                    x2: femX - femGap / 2, y2: marginTop - 16 + rows * rowH + 16,
                    stroke: iw.separatorColor, 'stroke-width': 2
                });
                svg.appendChild(sep);
            }

            // Dashed separator between OUT and IN sub-columns within a FEM
            var subSep = _svgEl('line');
            _svgAttrs(subSep, {
                x1: femX + outSubW, y1: marginTop - 16,
                x2: femX + outSubW, y2: marginTop - 16 + rows * rowH + 16,
                stroke: iw.subSeparatorColor, 'stroke-width': 1, 'stroke-dasharray': '4,4'
            });
            svg.appendChild(subSep);

            // Sub-column header labels
            svg.appendChild(_svgText(outCx, marginTop - 4, 'OUT', 8, '700', iw.subLabelColor, 'middle'));
            svg.appendChild(_svgText(inCx,  marginTop - 4, 'IN',  8, '700', iw.subLabelColor, 'middle'));

            if (hasDigital) {
                // Dashed separator between IN and DIG + the DIG header
                var digSep = _svgEl('line');
                _svgAttrs(digSep, {
                    x1: femX + outSubW + inSubW, y1: marginTop - 16,
                    x2: femX + outSubW + inSubW, y2: marginTop - 16 + rows * rowH + 16,
                    stroke: iw.subSeparatorColor, 'stroke-width': 1, 'stroke-dasharray': '4,4'
                });
                svg.appendChild(digSep);
                svg.appendChild(_svgText(digCx, marginTop - 4, 'DIG', 8, '700', iw.subLabelColor, 'middle'));
            }

            // FEM label at bottom
            svg.appendChild(_svgText(
                femX + femW / 2, svgH - 8,
                'FEM\u00a0' + femId + '  (' + femData.type + ')', 10, '400', iw.femLabelColor, 'middle'
            ));

            // Output port rows
            for (var portNum = 1; portNum <= rows; portNum++) {
                var py = marginTop + (portNum - 1) * rowH + rowH / 2;
                _renderPortCell(svg, outCx, py, circleR, roleColors, outPorts[String(portNum)] || [], rawWiring,
                                {con: ctrlName, slot: femId, port: portNum, io: 'output'}, editable, onPortHover);
            }

            // Digital output rows (all 8 physical slots; smaller circles)
            if (hasDigital) {
                var digPorts = femData.digital_ports || {};
                for (var dn = 1; dn <= rows; dn++) {
                    var dpy = marginTop + (dn - 1) * rowH + rowH / 2;
                    if (dn <= nDig) {
                        _renderPortCell(svg, digCx, dpy, 13, roleColors, digPorts[String(dn)] || [], rawWiring,
                                        {con: ctrlName, slot: femId, port: dn, io: 'digital'}, editable, onPortHover);
                    } else {
                        var ddot = _svgEl('circle');
                        _svgAttrs(ddot, {cx: digCx, cy: dpy, r: 5, fill: iw.emptyPortFill, stroke: iw.emptyPortStroke, 'stroke-width': 1});
                        svg.appendChild(ddot);
                    }
                }
            }

            // Physical input port positions depend on FEM type:
            //   MW-FEM: port 1 at row 1 (top), port 2 at last row (bottom)
            //   LF-FEM: port 1 at second-to-last row, port 2 at last row (both at bottom)
            var inputRowMap = {};  // display_row → input_port_number
            if (femData.type === 'mw-fem') {
                inputRowMap[1] = 1;
                inputRowMap[rows] = 2;
            } else {
                inputRowMap[rows - 1] = 1;
                inputRowMap[rows] = 2;
            }
            var inR = Math.round(circleR * 0.82);
            for (var pn = 1; pn <= rows; pn++) {
                var ipy = marginTop + (pn - 1) * rowH + rowH / 2;
                var portNumAtRow = inputRowMap[pn];
                if (portNumAtRow !== undefined) {
                    // Physical input port: show assignment or placeholder circle
                    _renderPortCell(svg, inCx, ipy, inR, roleColors, inPorts[String(portNumAtRow)] || [], rawWiring,
                                    {con: ctrlName, slot: femId, port: portNumAtRow, io: 'input'}, editable, onPortHover);
                } else {
                    // Non-physical row: tiny dot
                    var dot = _svgEl('circle');
                    _svgAttrs(dot, {cx: inCx, cy: ipy, r: 5, fill: iw.emptyPortFill, stroke: iw.emptyPortStroke, 'stroke-width': 1});
                    svg.appendChild(dot);
                }
            }
        });

        container.appendChild(svg);
    });

    // docs/135: size the rack(s) now that they are in the document, and offer
    // the overview when the rack is wider than its host.
    _applyInstrumentFit(container);

    // Keep popup alive when mouse enters it (cursor-popup mode only)
    var popup = onPortHover ? null : document.getElementById('port-popup');
    if (popup) {
        popup.addEventListener('mouseenter', function() { clearTimeout(_popupHideTimer); });
        popup.addEventListener('mouseleave', function() { _scheduleHidePopup(); });
    }
};

/**
 * Render one port cell: empty placeholder if no assignments; single row for ≤3 assignments;
 * two-row layout (max 3 per row) for 4+ assignments to avoid horizontal overflow.
 */
function _renderPortCell(svg, cx, cy, r, roleColors, assignments, rawWiring, portInfo, editable, onPortHover) {
    // Wrap the cell in a <g class="iw-port"> tagged with its con/slot/port/io
    // so drag-drop callers can identify and hit-test ports. Empty cells get a
    // group too, so they are valid drop targets.
    var cell = _svgEl('g');
    cell.setAttribute('class', 'iw-port');
    if (portInfo) {
        cell.setAttribute('data-con', portInfo.con);
        cell.setAttribute('data-slot', portInfo.slot);
        cell.setAttribute('data-port', portInfo.port);
        cell.setAttribute('data-io', portInfo.io);
    }
    if (assignments.length === 0) {
        var emptyC = _svgEl('circle');
        var iw2 = UI_CONFIG.instrumentWiring;
        _svgAttrs(emptyC, {cx: cx, cy: cy, r: r, fill: iw2.unassignedFill, stroke: iw2.unassignedStroke, 'stroke-width': 1.5});
        cell.appendChild(emptyC);
    } else if (assignments.length === 1) {
        _appendPortCircle(cell, cx, cy, r, roleColors, assignments[0], rawWiring, editable, onPortHover);
    } else if (assignments.length <= 3) {
        // Single row: spread smaller circles horizontally
        var sr = Math.max(10, Math.floor(r * 0.62));
        var spread = sr * 2 + 2;
        var startX = cx - (assignments.length - 1) * spread / 2;
        assignments.forEach(function(a, ai) {
            _appendPortCircle(cell, startX + ai * spread, cy, sr, roleColors, a, rawWiring, editable, onPortHover);
        });
    } else {
        // Two-row layout: first 3 on top row, remainder on bottom row
        var sr2 = Math.max(9, Math.floor(r * 0.55));
        var spread2 = sr2 * 2 + 2;
        var rowOff = sr2 + 3;
        var row1 = assignments.slice(0, 3);
        var row2 = assignments.slice(3);
        row1.forEach(function(a, ai) {
            var rx = cx - (row1.length - 1) * spread2 / 2 + ai * spread2;
            _appendPortCircle(cell, rx, cy - rowOff, sr2, roleColors, a, rawWiring, editable, onPortHover);
        });
        row2.forEach(function(a, ai) {
            var rx = cx - (row2.length - 1) * spread2 / 2 + ai * spread2;
            _appendPortCircle(cell, rx, cy + rowOff, sr2, roleColors, a, rawWiring, editable, onPortHover);
        });
    }
    if (editable && assignments.length >= 2) {
        // Feedline grip — drag to move the whole multiplexed feedline;
        // dragging a single circle moves just that one qubit.
        var grip = _svgEl('rect');
        var gh = Math.min(2 * r, 30);
        _svgAttrs(grip, {x: cx - r - 12, y: cy - gh / 2, width: 7, height: gh,
                         rx: 2, fill: '#8a8f98', stroke: 'rgba(0,0,0,0.3)', 'stroke-width': 1});
        grip.setAttribute('class', 'iw-port-grip');
        grip.style.cursor = 'grab';
        cell.appendChild(grip);
    }
    svg.appendChild(cell);
}

/** Create a colored SVG circle for a port assignment with hover and double-click handlers. */
function _appendPortCircle(svg, cx, cy, r, roleColors, assignment, rawWiring, editable, onPortHover) {
    var color = roleColors[assignment.role] || '#999';
    // docs/136 r2 — a bias-tee flux port is z + QDAC on one physical line, and
    // it gets its OWN colour. The first pass marked it with a dashed slate
    // ring over the z blue ("still a z port"), and the customer's verdict was
    // that it is invisible at port size. A distinct fill is the honest signal:
    // this port is not like the others, and the hover names both instruments.
    var qdacShared = !!assignment.qdac_shared;
    if (qdacShared) color = roleColors.z_qdac || '#f1c40f';
    var g = _svgEl('g');
    g.style.cursor = editable ? 'grab' : 'pointer';
    g.setAttribute('class', 'iw-port-circle');
    if (assignment.element != null) g.setAttribute('data-element', assignment.element);
    if (assignment.role != null) g.setAttribute('data-role', assignment.role);

    var circle = _svgEl('circle');
    // The bias-tee port keeps a solid slate outline — the digital hue, tying
    // it to the trigger cable that arms its QDAC channel.
    _svgAttrs(circle, {cx: cx, cy: cy, r: r, fill: color,
                       stroke: qdacShared ? (roleColors.digital || '#54617a') : 'rgba(0,0,0,0.15)',
                       'stroke-width': qdacShared ? 2 : 1.5});
    g.appendChild(circle);
    if (qdacShared) g.setAttribute('data-qdac-shared', '1');

    // Strip .role suffix for display: "qA1.xy" → "qA1" (role is encoded by color)
    var label = assignment.label || '';
    var displayLabel = label.replace(/\.[^.]+$/, '') || label;
    var maxChars = r < 14 ? 4 : (r < 18 ? 6 : 7);
    var display = displayLabel.length > maxChars ? displayLabel.substring(0, maxChars - 1) + '\u2026' : displayLabel;
    var fontSize;
    if (r >= 16) {
        // Single-member circle (control/z/coupler, single readout, input
        // single): size the label to the chord at the text band, so short
        // names get big type and longer ones shrink instead of only truncating.
        var cap = r >= 18 ? 14 : 11;
        var chord = 2 * Math.sqrt(r * r - 36);
        fontSize = Math.max(9, Math.min(cap,
            Math.floor(chord / (0.62 * Math.max(1, display.length)))));
    } else {
        fontSize = 7;  // multi-member feedline sub-circles
    }
    // White text fails on the amber bias-tee fill — dark label there instead.
    var txt = _svgText(cx, cy + Math.round(fontSize * 0.36), display, fontSize, '700',
        qdacShared ? '#1f2430' : UI_CONFIG.instrumentWiring.portLabelColor, 'middle');
    txt.setAttribute('font-family', 'monospace');
    g.appendChild(txt);

    // Single click → open inspector; double-click → JSON panel (timer
    // distinguishes them). Skipped in any wizard context (editable, or a
    // read-only diagram with onPortHover) — the wizard has no loaded chip.
    if (!editable && !onPortHover) {
        var _clickDelay = null;
        g.addEventListener('click', function() {
            clearTimeout(_clickDelay);
            _clickDelay = setTimeout(function() { _openInspectorForElement(assignment.element); }, 220);
        });
        g.addEventListener('dblclick', function() {
            clearTimeout(_clickDelay);
            _showInstrumentJsonPanel(assignment, rawWiring);
        });
    }
    // With an onPortHover callback (the wizard's diagrams), route hover to
    // the caller's docked monitor panel; otherwise use the cursor popup.
    if (onPortHover) {
        g.addEventListener('mouseenter', function() { onPortHover(assignment); });
        g.addEventListener('mouseleave', function() { onPortHover(null); });
    } else {
        g.addEventListener('mouseenter', function(e) { _showPortPopup(e, assignment); });
        g.addEventListener('mouseleave', function() { _scheduleHidePopup(); });
    }

    svg.appendChild(g);
}

/** Open the qubit or pair inspector for the given element name. */
function _openInspectorForElement(element) {
    if (!element) return;
    var url = element.indexOf('-') !== -1 ? '/pair/' + element : '/qubit/' + element;
    htmx.ajax('GET', url, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
}

/** Position and populate the floating popup with role-specific field data near the hovered port. */
function _showPortPopup(event, assignment) {
    clearTimeout(_popupHideTimer);
    var popup = document.getElementById('port-popup');
    if (!popup) return;

    _popupElement = assignment.element;

    document.getElementById('popup-label').textContent = assignment.label;
    var badge = document.getElementById('popup-role-badge');
    badge.textContent = (assignment.role || '').toUpperCase();
    badge.className = 'role-badge ' + (assignment.role || '');

    var body = document.getElementById('popup-body');
    body.innerHTML = '';
    _getPopupFields(assignment).forEach(function(f) {
        var k = document.createElement('span');
        k.className = 'popup-key';
        k.textContent = f.key;
        var v = document.createElement('span');
        v.className = 'popup-val';
        v.textContent = (f.value !== null && f.value !== undefined) ? f.value : '—';
        body.appendChild(k);
        body.appendChild(v);
    });

    popup.classList.remove('hidden');
    // quam_ui_scale sets a CSS zoom on <html>; fixed-element px get
    // re-multiplied by it, so divide the viewport coords back out (same fix
    // as the wizard's slot menu / drag ghost — r15, docs/70).
    var z = parseFloat(document.documentElement.style.zoom);
    if (!isFinite(z) || z <= 0) z = 1;
    var px = event.clientX + 14;
    var py = event.clientY - 10;
    if (px + 340 > window.innerWidth) px = event.clientX - 340;
    popup.style.left = (px / z) + 'px';
    popup.style.top = (py / z) + 'px';
}

function _scheduleHidePopup() {
    _popupHideTimer = setTimeout(function() {
        var popup = document.getElementById('port-popup');
        if (popup) popup.classList.add('hidden');
    }, 280);
}

/** Format a numeric value with unit suffix (GHz, MHz, ns, dBm, GSps). Returns null for null/undefined. */
function _fmtVal(v, type) {
    if (v === null || v === undefined) return null;
    if (type === 'GHz' && typeof v === 'number') return (v / 1e9).toFixed(4) + ' GHz';
    if (type === 'MHz' && typeof v === 'number') return (v / 1e6).toFixed(1) + ' MHz';
    if (type === 'ns'  && typeof v === 'number') return v + ' ns';
    if (type === 'dBm' && typeof v === 'number') return v + ' dBm';
    if (type === 'GSps'&& typeof v === 'number') return (v / 1e9).toFixed(1) + ' GSps';
    return String(v);
}

/** Format a number to fixed decimals, or return null if value is null/undefined. */
function _fmtNum(v, d) {
    return (v != null && typeof v === 'number') ? v.toFixed(d) : null;
}

/** Return role-specific key/value pairs for the port hover popup. */
function _getPopupFields(a) {
    var r = a.role;
    if (r === 'xy') return [
        {key: 'f\u2080\u2081',  value: _fmtVal(a.f_01, 'GHz')},
        {key: 'RF freq',    value: _fmtVal(a.rf_frequency, 'GHz')},
        {key: 'LO (upconv)',value: _fmtVal(a.lo_frequency, 'GHz')},
        {key: 'band',       value: a.band},
        {key: 'x180 amp',   value: _fmtNum(a.x180_amplitude, 4)},
        {key: 'x180 len',   value: _fmtVal(a.x180_length, 'ns')},
        {key: 'DRAG \u03b1', value: _fmtNum(a.drag_alpha, 4)},
        {key: 'anharm.',    value: _fmtVal(a.anharmonicity, 'MHz')},
        {key: 'sat amp',    value: _fmtNum(a.saturation_amplitude, 4)},
        {key: 'sat len',    value: _fmtVal(a.saturation_length, 'ns')},
        {key: 'power',      value: _fmtVal(a.full_scale_power_dbm, 'dBm')},
    ];
    if (r === 'rr') return [
        {key: 'RO freq',    value: _fmtVal(a.rf_frequency, 'GHz')},
        {key: 'LO (upconv)',value: _fmtVal(a.lo_frequency, 'GHz')},
        {key: 'band',       value: a.band},
        {key: 'RO amp',     value: _fmtNum(a.readout_amplitude, 4)},
        {key: 'RO len',     value: _fmtVal(a.readout_length, 'ns')},
        {key: 'TOF',        value: _fmtVal(a.time_of_flight, 'ns')},
        {key: 'depletion',  value: _fmtVal(a.depletion_time, 'ns')},
        {key: 'threshold',  value: _fmtNum(a.readout_threshold, 4)},
        {key: 'power',      value: _fmtVal(a.full_scale_power_dbm, 'dBm')},
    ];
    if (r === 'rr_in') return [
        {key: 'RO freq',      value: _fmtVal(a.rf_frequency, 'GHz')},
        {key: 'LO (downconv)',value: _fmtVal(a.lo_frequency, 'GHz')},
        {key: 'band',         value: a.band},
    ];
    if (r === 'z') {
        var zf = [
            {key: 'flux point',   value: a.flux_point},
            {key: 'joint offset', value: _fmtNum(a.joint_offset, 4)},
            {key: 'indep offset', value: _fmtNum(a.independent_offset, 4)},
            {key: 'output mode',  value: a.output_mode},
            {key: 'upsampling',   value: a.upsampling_mode},
        ];
        // docs/136 — a bias-tee line is driven by TWO instruments and one
        // hover has to answer for both. The QDAC holds the DC operating point
        // (which is why `joint offset` above may be 0 and mean nothing) while
        // this OPX port plays the pulses; showing only one half would read as
        // a complete description of the line and be wrong.
        if (a.qdac_shared) {
            zf.push({key: '— bias tee —', value: 'QDAC-II + LF-FEM'});
            zf.push({key: 'QDAC channel', value: a.qdac_channel});
            zf.push({key: 'QDAC DC offset', value: _fmtNum(a.qdac_dc_offset, 4)});
            zf.push({key: 'QDAC trigger', value: a.qdac_trigger_port});
        }
        return zf;
    }
    if (r === 'coupler') return [
        {key: 'flux point',     value: a.flux_point},
        {key: 'decouple ofs',   value: _fmtNum(a.decouple_offset, 4)},
        {key: 'interact ofs',   value: _fmtNum(a.interaction_offset, 4)},
        {key: 'output mode',    value: a.output_mode},
        {key: 'upsampling',     value: a.upsampling_mode},
    ];
    if (r === 'cr') return [
        {key: 'control',    value: a.qubit_control},
        {key: 'target',     value: a.qubit_target},
        {key: 'LO',         value: _fmtVal(a.lo_frequency, 'GHz')},
        {key: 'band',       value: a.band},
        {key: 'power',      value: _fmtVal(a.full_scale_power_dbm, 'dBm')},
    ];
    if (r === 'twpa_pump') return [
        {key: 'pump freq',  value: _fmtVal(a.pump_frequency, 'GHz')},
        {key: 'pump amp',   value: _fmtNum(a.pump_amplitude, 4)},
        {key: 'max gain',   value: a.max_avg_gain != null ? a.max_avg_gain + ' dB' : null},
    ];
    if (r === 'twpa_ro') return [
        {key: 'RO freq',    value: _fmtVal(a.rf_frequency, 'GHz')},
        {key: 'depletion',  value: _fmtVal(a.depletion_time, 'ns')},
        {key: 'TOF',        value: _fmtVal(a.time_of_flight, 'ns')},
    ];
    if (r === 'twpa_in') return [
        {key: 'RO freq',    value: _fmtVal(a.rf_frequency, 'GHz')},
    ];
    if (r === 'digital') {
        var df = [{key: 'marker', value: a.marker}];
        // docs/136 — on a QDAC trigger the ext input is the ONLY thing that
        // explains why several qubits legitimately land on one port: the OPX
        // output feeds one ext BNC and that arms every channel armed on it.
        // Without it a shared port reads as a wiring collision.
        if (a.qdac_trigger) df.push({key: 'QDAC input', value: a.qdac_ext});
        df.push({key: 'line',      value: a.source || null});
        df.push({key: 'delay',     value: _fmtVal(a.delay, 'ns')});
        df.push({key: 'buffer',    value: _fmtVal(a.buffer, 'ns')});
        df.push({key: 'shareable', value: a.shareable == null ? null : String(a.shareable)});
        df.push({key: 'inverted',  value: a.inverted == null ? null : String(a.inverted)});
        return df;
    }
    return [];
}

/** Load the qubit or pair detail view in the inspector pane when the popup button is clicked. */
window.openInspectorFromPopup = function() {
    if (!_popupElement) return;
    document.getElementById('port-popup').classList.add('hidden');
    // Pair element names contain a dash (e.g. "q4-5") — route to pair detail
    if (_popupElement.indexOf('-') !== -1) {
        htmx.ajax('GET', '/pair/' + _popupElement, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
    } else {
        htmx.ajax('GET', '/qubit/' + _popupElement, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
    }
};

/** Open the slide-up JSON panel showing the raw wiring subtree for the clicked element. */
function _showInstrumentJsonPanel(assignment, rawWiring) {
    var panel = document.getElementById('json-panel');
    var treeEl = document.getElementById('json-panel-tree');
    if (!panel || !treeEl) return;

    var elem = assignment.element;
    var subtree = null;
    var wiring = (rawWiring || {}).wiring || {};
    var qubits = wiring.qubits || {};
    var pairs  = wiring.qubit_pairs || {};
    var twpas  = wiring.twpas || {};
    if (qubits[elem]) subtree = qubits[elem];
    else if (pairs[elem]) subtree = pairs[elem];
    else if (twpas[elem]) subtree = twpas[elem];

    document.getElementById('json-panel-title').textContent = 'Wiring JSON — ' + elem;
    treeEl.innerHTML = '';
    if (subtree) renderJsonTree('json-panel-tree', subtree, {defaultDepth: 2});
    panel.classList.remove('hidden');
}

// SVG helpers
function _svgEl(tag) {
    return document.createElementNS('http://www.w3.org/2000/svg', tag);
}
function _svgAttrs(el, attrs) {
    Object.keys(attrs).forEach(function(k) { el.setAttribute(k, attrs[k]); });
}
function _svgText(x, y, text, size, weight, fill, anchor) {
    var t = _svgEl('text');
    _svgAttrs(t, {x: x, y: y, 'font-size': size, 'font-weight': weight, fill: fill, 'text-anchor': anchor});
    t.textContent = text;
    return t;
}

// ======================================================================
// Dataset browser functions
// ======================================================================

/**
 * Real-time multi-token search filter for the datasets table.
 * Splits query by spaces, hides rows where ALL tokens don't match (AND logic).
 */
// ── Experiment multi-select filter ──────────────────────────────────────────
var _selectedExps = new Set();

window._selectedExps = _selectedExps;  // Exposed so dataset-virtual.js can read live state.

window.toggleExpFilter = function(exp, chipEl) {
    if (exp === '') {
        _selectedExps.clear();
    } else {
        if (_selectedExps.has(exp)) {
            _selectedExps.delete(exp);
        } else {
            _selectedExps.add(exp);
        }
    }
    _syncExpFilterUI();
    _applyDatasetFilters();
};

window.toggleExpCategory = function(catLabel, labelEl) {
    var section = labelEl.closest('.exp-filter-section');
    if (!section) return;
    var chips = section.querySelectorAll('.exp-chip');
    var catExps = [];
    chips.forEach(function(c) { catExps.push(c.getAttribute('data-exp')); });
    // If all in this category are already selected, deselect them; otherwise select all
    var allSelected = catExps.every(function(e) { return _selectedExps.has(e); });
    catExps.forEach(function(e) {
        if (allSelected) _selectedExps.delete(e); else _selectedExps.add(e);
    });
    _syncExpFilterUI();
    _applyDatasetFilters();
};

function _syncExpFilterUI() {
    var grid = document.getElementById('exp-filter-grid');
    if (!grid) return;
    grid.querySelectorAll('.exp-chip').forEach(function(c) {
        var v = c.getAttribute('data-exp');
        if (v === '') {
            c.classList.toggle('active', _selectedExps.size === 0);
        } else {
            c.classList.toggle('active', _selectedExps.has(v));
        }
    });
    // Highlight sections where all chips are active
    grid.querySelectorAll('.exp-filter-section').forEach(function(sec) {
        var chips = sec.querySelectorAll('.exp-chip');
        var allActive = chips.length > 0;
        chips.forEach(function(c) { if (!_selectedExps.has(c.getAttribute('data-exp'))) allActive = false; });
        sec.classList.toggle('section-active', allActive);
    });
}

/* ------------------------------------------------------------------ */
/* Collections page: tag-filter chips (mirror of the exp-filter chips)  */
/* ------------------------------------------------------------------ */
// Multi-select like the exp chips (clicking a 2nd tag keeps the 1st). The
// filter is OR (a row passes with ANY selected tag) and dataset-virtual.js
// ranks rows matching the MOST selected tags to the top. '' = the "All" chip.
var _selectedTags = new Set();
window._selectedTags = _selectedTags;  // read live by dataset-virtual.js

window.toggleTagFilter = function(tag, chipEl) {
    if (tag === '') {
        _selectedTags.clear();
    } else if (_selectedTags.has(tag)) {
        _selectedTags.delete(tag);
    } else {
        _selectedTags.add(tag);
    }
    _syncTagFilterUI();
    _applyDatasetFilters();
};

function _syncTagFilterUI() {
    var grid = document.getElementById('tag-filter-grid');
    if (!grid) return;
    grid.querySelectorAll('.tag-chip').forEach(function(c) {
        var v = c.getAttribute('data-tag');
        if (v === '') {
            c.classList.toggle('active', _selectedTags.size === 0);
        } else {
            c.classList.toggle('active', _selectedTags.has(v));
        }
    });
}

function _applyDatasetFilters() {
    // Delegated to dataset-virtual.js, which filters the in-memory row array.
    if (window.DatasetVirtual && typeof window.DatasetVirtual.applyFilters === 'function') {
        window.DatasetVirtual.applyFilters();
    }
}

/* ------------------------------------------------------------------ */
/* Multi-folder: folder-filter chips (mirror of the exp-filter chips)   */
/* ------------------------------------------------------------------ */
// The selected-folder set lives in dataset-virtual.js (state.folderFilter) so it
// resets when the active-folder SET changes; app.js owns only the chip UI.
// '' = the "All" chip (clears the filter → show every folder).
window.toggleFolderFilter = function(key, chipEl) {
    if (window.DatasetVirtual && typeof window.DatasetVirtual.toggleFolder === 'function') {
        window.DatasetVirtual.toggleFolder(key);
    }
    _syncFolderFilterUI();
};

function _syncFolderFilterUI() {
    var grid = document.getElementById('folder-filter-grid');
    if (!grid) return;
    var keys = (window.DatasetVirtual && typeof window.DatasetVirtual.folderFilterKeys === 'function')
               ? window.DatasetVirtual.folderFilterKeys() : [];
    var sel = new Set(keys);
    grid.querySelectorAll('.folder-chip').forEach(function(c) {
        var v = c.getAttribute('data-folder-key') || '';
        c.classList.toggle('active', v === '' ? sel.size === 0 : sel.has(v));
    });
}

window.filterDatasetTable = function(input) {
    _applyDatasetFilters();
};

/* Scoped-search help panel triggers.
 *
 * The search box, help icon, and panel all live inside #table-pane, which
 * HTMX innerHTML-swaps on every date-tab / rescan / nav-back. Delegated
 * listeners on document.body avoid re-binding after every swap.
 *
 * Open/close trigger (docs/120 item 3): the ? icon TOGGLES. Nothing opens
 * this panel on its own any more — it used to open on the first focus of
 * #dataset-search per browser session, which meant the panel appeared the
 * moment you started typing and then would not close from the same button
 * you opened it with (the ? was open-only). The ? is the whole affordance.
 * Also closable via #ds-search-help-close (the × button).
 *
 * Per user spec: NO auto-dismiss on blur/outside-click. The panel persists
 * through typing, sorting, chip clicks, and HTMX swaps until X is clicked.
 */
(function() {
    function closeHelp() {
        var panel = document.getElementById('ds-search-help');
        if (panel) panel.hidden = true;
    }
    function toggleHelp() {
        var panel = document.getElementById('ds-search-help');
        if (panel) panel.hidden = !panel.hidden;
    }

    // Attach to document (not document.body) — app.js loads in <head> with no
    // defer, so document.body is null at script-parse time. Click bubbles up
    // to document, so delegation works identically.
    document.addEventListener('click', function(e) {
        var t = e.target;
        if (!t) return;
        // Dead-click guard: the help panel floats over the run list (z-index:30), so
        // while it's open the rows it covers are unclickable. Dismiss it the moment the
        // user engages the TABLE (clicks a row in #datasets-scroll) — the click still
        // proceeds to the row. It still persists through typing + example clicks per the
        // original spec; only table interaction closes it.
        if (t.closest && t.closest('#datasets-scroll')) closeHelp();
        if (t.id === 'ds-search-help-toggle') {
            e.preventDefault();
            toggleHelp();
            return;
        }
        if (t.id === 'ds-search-help-close') {
            e.preventDefault();
            closeHelp();
            return;
        }
        if (t.classList && t.classList.contains('ds-help-example')) {
            e.preventDefault();
            var example = t.getAttribute('data-example') || '';
            var input = document.getElementById('dataset-search');
            if (!input) return;
            input.value = example;
            input.focus();
            input.dispatchEvent(new Event('input', {bubbles: true}));
        }
    });
})();

/* Generic scoped-search help panel — reused by any search box that opts in via
 * classes + data-attributes (currently the sidebar workspace filter):
 *   ? icon:  class="search-help-toggle"  data-search-help="<panel-id>"
 *   × close: class="search-help-close"   data-search-help="<panel-id>"
 *   example: class="search-help-example" data-search-help-input="<input-id>" data-example="…"
 * The ? TOGGLES; × closes. Delegated on document (app.js loads in <head>).
 * The Datasets page keeps its own id-based handler above.
 *
 * The INPUT needs no markup contract any more. It used to carry
 * class="search-help-input" + data-search-help so the focus handler could find
 * its panel; with that handler gone nothing reads either, and base.html's
 * leftovers are vestigial — a new search box only needs the three rows above.
 *
 * docs/120 item 3 — nothing auto-opens this any more. It used to open on the
 * first focus of the input per browser session, and the sidebar's copy of the
 * panel is deliberately `position: static` (style.css, so a narrow scrolling
 * sidebar can't clip an absolute popover) — meaning it renders INLINE and
 * pushes the experiment tree down. Typing one character therefore buried the
 * folder list, and the ? could not put it back because it was open-only. */
(function() {
    document.addEventListener('click', function(e) {
        var t = e.target;
        if (!t || !t.classList) return;
        if (t.classList.contains('search-help-toggle')) {
            e.preventDefault();
            var p = document.getElementById(t.getAttribute('data-search-help'));
            if (p) p.hidden = !p.hidden;
            return;
        }
        if (t.classList.contains('search-help-close')) {
            e.preventDefault();
            var p2 = document.getElementById(t.getAttribute('data-search-help'));
            if (p2) p2.hidden = true;
            return;
        }
        if (t.classList.contains('search-help-example')) {
            e.preventDefault();
            var input = document.getElementById(t.getAttribute('data-search-help-input'));
            if (!input) return;
            input.value = t.getAttribute('data-example') || '';
            input.focus();
            input.dispatchEvent(new Event('input', {bubbles: true}));  // oninput → filter pills
            if (window.htmx) window.htmx.trigger(input, 'keyup');        // hx-trigger → server filter
        }
    });
})();

/**
 * Switch dataset detail tabs (Overview, Results, Figures, Data, State).
 */
window._dsActiveTab = 'full';

// Tabs whose content lives inside the single #ds-tab-combined container:
// 'full' shows every [data-fvsec] section, the others show just their own.
var _DS_COMBINED_TABS = ['full', 'overview', 'results', 'figures'];

window.switchDatasetTab = function(tabName, linkEl) {
    window._dsActiveTab = tabName;
    _dsSticky.tab = tabName;
    // Scope EVERY query to the panel containing the clicked tab. In the pinned
    // compare view there are two detail panels (the left one's ids are `pinned-`
    // prefixed), so global getElementById/querySelectorAll would clobber both and
    // leave the clicked panel blank. _h5Panel falls back to #inspector-pane in the
    // normal single view. `[id$=…]` matches both prefixed + unprefixed ids.
    var panel = _h5Panel(linkEl);
    if (!panel) return;
    panel.querySelectorAll('.dataset-tab-content').forEach(function(el) {
        el.classList.add('hidden');
    });
    if (_DS_COMBINED_TABS.indexOf(tabName) !== -1) {
        var combined = panel.querySelector('[id$="ds-tab-combined"]');
        if (combined) {
            combined.classList.remove('hidden');
            combined.setAttribute('data-view', tabName);
            combined.querySelectorAll('[data-fvsec]').forEach(function(sec) {
                var key = sec.getAttribute('data-fvsec');
                sec.classList.toggle('hidden', !(tabName === 'full' || key === tabName));
            });
        }
    } else {
        var target = panel.querySelector('[id$="ds-tab-' + tabName + '"]');
        if (target) target.classList.remove('hidden');
    }

    // Update active tab link IN THIS PANEL.
    panel.querySelectorAll('.dataset-tabs a').forEach(function(a) {
        a.classList.remove('active');
    });
    if (linkEl) linkEl.classList.add('active');

    // Lazy-load the Interactive figures the first time the tab is shown.
    if (tabName === 'interactive') {
        var c = panel.querySelector('[id$="interactive-container"]');
        var rid = c ? c.getAttribute('data-run-id') : null;   // uid string
        if (rid) loadDatasetInteractive(rid, panel);
        // docs/118: tiles already rendered were drawn for the geometry they
        // were last shown in. Re-showing the tab is exactly when that is wrong.
        if (c) {
            _observeInteractiveResize(c);
            setTimeout(function() { window.resizeInteractiveTiles(c); }, 0);
        }
    }
    // Lazy-load the Prev State diff the first time the tab is shown.
    if (tabName === 'prev') {
        var pc = panel.querySelector('[id$="ds-prevdiff-container"]');
        if (pc && pc.getAttribute('data-loaded') !== '1') {
            loadPrevDiffInto(pc.id, pc.getAttribute('data-run-id'), 0);   // uid string
        }
    }
    // Lazy-load the HDF5 summary the first time Raw Data is shown (the old
    // hx-trigger="load" opened the run's HDF5 on EVERY click, in a hidden tab).
    if (tabName === 'data') {
        var hc = panel.querySelector('[id$="h5-summary-container"]');
        if (hc && hc.getAttribute('data-loaded') !== '1' && window.htmx) {
            hc.setAttribute('data-loaded', '1');
            htmx.trigger(hc, 'ds-h5-open');
        }
    }
};

// ── Prev-state diff (item 5) ──────────────────────────────────────────
// Fetch the diff partial into a container. An AbortController per container
// cancels a still-in-flight load if the user steps again or switches runs,
// so a stale response can't overwrite the current one (red-team race guard).
window._prevDiffAbort = {};
window.loadPrevDiffInto = function(containerId, runId, compact, vs) {
    var el = document.getElementById(containerId);
    if (!el) return;
    if (window._prevDiffAbort[containerId]) window._prevDiffAbort[containerId].abort();
    var ctrl = new AbortController();
    window._prevDiffAbort[containerId] = ctrl;
    el.innerHTML = '<p class="muted" style="padding:.6rem">Loading diff…</p>';
    var url = '/dataset/' + runId + '/prev-state-diff?compact=' + (compact ? 1 : 0);
    if (vs !== undefined && vs !== null) url += '&vs=' + vs;
    fetch(url, { signal: ctrl.signal })
        .then(function(r) { return r.text(); })
        .then(function(html) { el.innerHTML = html; el.setAttribute('data-loaded', '1'); })
        .catch(function(e) {
            if (e.name === 'AbortError') return;
            el.innerHTML = '<p style="color:var(--pico-del-color);padding:.6rem">Failed to load diff.</p>';
        });
};
// Stepper button → reload the diff (against run `vs`) in whichever container holds it.
window.loadPrevDiff = function(btn, runId, vs, compact) {
    // r16 ④: SUFFIX match (the file's established idiom) — pinned-compare
    // prefixes every cloned id with "pinned-", so the exact-id closest()
    // missed and BOTH stepper buttons were silently dead there.
    var container = btn.closest('[id$="ds-prevdiff-container"], [id$="ds-prevdiff-fv-body"]');
    if (container) loadPrevDiffInto(container.id, runId, compact, vs);
};

/**
 * Figure lightbox. Entry point for every figure <img onclick="toggleFigureZoom(this)">
 * (dataset detail / compare / interactive / trends).
 */
window.toggleFigureZoom = function(imgEl) {
    // r13 feedback: the old class-toggle only pinned the <img> over the page —
    // "the popup opens but how do I zoom in/out?". Real viewer now:
    // wheel = cursor-anchored zoom (fit ×1 … ×12), drag = pan,
    // double-click = fit↔250%, +/−/⟲/× buttons with a live % readout,
    // Esc (trapFocus) / backdrop click / × close. The overlay holds a CLONE on
    // <body>, so the figure grid never reflows and htmx swaps underneath are
    // harmless (the old in-place approach needed a beforeSwap teardown sweep).
    var existing = document.getElementById('figure-lightbox');
    if (existing) { if (existing._close) existing._close(); return; }
    if (!imgEl) return;

    var box = document.createElement('div');
    box.id = 'figure-lightbox';
    box.className = 'fig-lightbox';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-label', 'Figure viewer');

    var img = document.createElement('img');
    img.className = 'fig-lightbox-img';
    img.src = imgEl.getAttribute('src') || imgEl.src;
    img.alt = imgEl.alt || '';
    img.draggable = false;
    box.appendChild(img);

    // Customer ask (2026-08-27): "to see the next figure I had to close and
    // click the next one". The viewer now walks the figures of the SAME grid
    // the clicked one sits in (a run's figure grid, a compare grid, a trend
    // strip, the Interactive tab) — ← / → keys and on-screen arrows. The
    // group is read at open time from the live DOM, so it is exactly what
    // the user sees; a lone figure gets no arrows at all.
    var groupHost = imgEl.closest
        ? imgEl.closest('.figure-grid, .compare-figure-grid, .figure-strip, .dataset-tab-content')
        : null;
    var group = Array.prototype.slice.call(
        (groupHost || document).querySelectorAll('img[onclick*="toggleFigureZoom"]'));
    var idx = group.indexOf(imgEl);
    if (idx < 0) { group = [imgEl]; idx = 0; }

    var bar = document.createElement('div');
    bar.className = 'fig-lightbox-bar';
    var countLabel = document.createElement('span');
    countLabel.className = 'fig-lightbox-count';
    if (group.length > 1) bar.appendChild(countLabel);
    var zoomLabel = document.createElement('span');
    zoomLabel.className = 'fig-lightbox-zoom';
    bar.appendChild(zoomLabel);
    [['out', '−', 'Zoom out'], ['in', '+', 'Zoom in'],
     ['reset', '⟲', 'Reset zoom'], ['close', '×', 'Close  (Esc)']]
        .forEach(function(b) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'fig-lightbox-btn';
            btn.setAttribute('data-act', b[0]);
            btn.textContent = b[1];
            btn.title = b[2];
            bar.appendChild(btn);
        });
    box.appendChild(bar);

    var caption = document.createElement('div');
    caption.className = 'fig-lightbox-caption';
    box.appendChild(caption);

    var navPrev = null, navNext = null;
    if (group.length > 1) {
        [['-1', '‹', 'Previous figure  (←)', 'fig-lightbox-nav-prev'],
         ['1', '›', 'Next figure  (→)', 'fig-lightbox-nav-next']].forEach(function(n) {
            var nb = document.createElement('button');
            nb.type = 'button';
            nb.className = 'fig-lightbox-nav ' + n[3];
            nb.setAttribute('data-nav', n[0]);
            nb.textContent = n[1];
            nb.title = n[2];
            nb.setAttribute('aria-label', n[2]);
            box.appendChild(nb);
            if (n[0] === '-1') navPrev = nb; else navNext = nb;
        });
    }

    var hint = document.createElement('div');
    hint.className = 'fig-lightbox-hint';
    hint.textContent = 'scroll to zoom · drag to pan · double-click to zoom'
        + (group.length > 1 ? ' · ← → other figures' : '') + ' · Esc to close';
    box.appendChild(hint);

    // transform = translate(tx,ty) scale(s), origin center. zoomAt keeps the
    // content point under the cursor fixed: with the img's visual center at
    // rect-center, cursor→content is (cursor − center)/s, so the translate
    // shifts by p·(s − s') when s changes.
    var s = 1, tx = 0, ty = 0;
    function apply() {
        img.style.transform = 'translate(' + tx + 'px, ' + ty + 'px) scale(' + s + ')';
        zoomLabel.textContent = Math.round(s * 100) + '%';
    }
    function zoomAt(cx, cy, factor) {
        var ns = Math.min(12, Math.max(1, s * factor));
        if (ns === s) return;
        var r = img.getBoundingClientRect();
        var px = (cx - (r.left + r.width / 2)) / s;
        var py = (cy - (r.top + r.height / 2)) / s;
        tx += px * (s - ns); ty += py * (s - ns);
        s = ns;
        if (s === 1) { tx = 0; ty = 0; }   // back at fit → recenter
        apply();
    }
    apply();

    // Show group member i: swap the clone's source, reset the zoom (a pan
    // into one figure means nothing on the next), and keep the count + the
    // end-of-strip arrow states honest. No wrap-around — the ends are ends.
    function show(i) {
        if (i < 0 || i >= group.length) return;
        idx = i;
        var src = group[idx];
        img.src = src.getAttribute('src') || src.src;
        img.alt = src.alt || '';
        s = 1; tx = 0; ty = 0; apply();
        caption.textContent = img.alt;
        countLabel.textContent = (idx + 1) + ' / ' + group.length;
        if (navPrev) { navPrev.disabled = idx === 0; }
        if (navNext) { navNext.disabled = idx === group.length - 1; }
    }
    show(idx);

    function onNavKey(e) {
        if (e.key === 'ArrowLeft') { e.preventDefault(); show(idx - 1); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); show(idx + 1); }
    }
    if (group.length > 1) document.addEventListener('keydown', onNavKey, true);

    var release = null;
    function close() {
        document.removeEventListener('keydown', onNavKey, true);
        if (release) { try { release(); } catch (e) {} release = null; }
        if (box.parentNode) box.parentNode.removeChild(box);
    }
    box._close = close;

    box.addEventListener('wheel', function(e) {
        e.preventDefault();
        zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.0015));
    }, { passive: false });

    var drag = null;
    img.addEventListener('pointerdown', function(e) {
        if (e.button !== undefined && e.button !== 0) return;
        drag = { x: e.clientX, y: e.clientY };
        img.classList.add('dragging');
        if (img.setPointerCapture && e.pointerId !== undefined) {
            try { img.setPointerCapture(e.pointerId); } catch (err) {}
        }
        e.preventDefault();
    });
    img.addEventListener('pointermove', function(e) {
        if (!drag) return;
        tx += e.clientX - drag.x;
        ty += e.clientY - drag.y;
        drag = { x: e.clientX, y: e.clientY };
        apply();
    });
    function endDrag() { drag = null; img.classList.remove('dragging'); }
    img.addEventListener('pointerup', endDrag);
    img.addEventListener('pointercancel', endDrag);

    img.addEventListener('dblclick', function(e) {
        if (s > 1.01) { s = 1; tx = 0; ty = 0; apply(); }
        else zoomAt(e.clientX, e.clientY, 2.5);
    });

    box.addEventListener('click', function(e) {
        var nav = e.target.closest ? e.target.closest('.fig-lightbox-nav') : null;
        if (nav) { show(idx + parseInt(nav.getAttribute('data-nav'), 10)); return; }
        var b = e.target.closest ? e.target.closest('.fig-lightbox-btn') : null;
        if (b) {
            var act = b.getAttribute('data-act');
            var cx = window.innerWidth / 2, cy = window.innerHeight / 2;
            if (act === 'in') zoomAt(cx, cy, 1.4);
            else if (act === 'out') zoomAt(cx, cy, 1 / 1.4);
            else if (act === 'reset') { s = 1; tx = 0; ty = 0; apply(); }
            else if (act === 'close') close();
            return;
        }
        if (e.target === box) close();   // backdrop (drags start on the img)
    });

    document.body.appendChild(box);
    release = window.trapFocus ? window.trapFocus(box, close) : null;
    var closeBtn = bar.querySelector('[data-act="close"]');
    if (closeBtn) closeBtn.focus();
};

/**
 * Return the enclosing dataset column (pinned or current) for an element,
 * falling back to #inspector-pane when not in split-view.
 */
function _h5Panel(el) {
    return (el && el.closest('.inspector-pinned-col, .inspector-current-col'))
           // docs/118: a run opened as a FULL PAGE ("Open as a full page", or a
           // /dataset/<uid> URL) renders into #table-pane, not the inspector.
           // The old fallback then scoped every query below to a pane that does
           // not contain the tabs, so switchDatasetTab set _dsActiveTab and
           // changed NOTHING on screen: no tab ever switched and Interactive
           // never loaded. The detail root that actually CONTAINS the link is
           // the honest answer; the global fallback stays for callers that pass
           // no element.
           || (el && el.closest('#ds-detail-root, .dataset-detail'))
           || document.getElementById('inspector-pane');
}

/**
 * Load HDF5 summary for a dataset tab switch (ds_raw / ds_fit).
 * triggerEl: the button that was clicked (used to scope DOM queries to the right panel)
 */
window.loadDatasetH5 = function(triggerEl, runId, which) {
    window._dsLastH5Which = which;
    var panel = _h5Panel(triggerEl);
    // Update active button within this panel only
    panel.querySelectorAll('.h5-tab').forEach(function(b) { b.classList.remove('active'); });
    if (triggerEl) triggerEl.classList.add('active');

    // Load via HTMX-style fetch
    var container = panel.querySelector('[id$="h5-summary-container"]');
    if (!container) return;
    container.innerHTML = '<p class="muted" style="padding:1rem">Loading...</p>';

    fetch('/dataset/' + runId + '/h5?which=' + which)
        .then(function(r) { return r.text(); })
        .then(function(html) {
            container.innerHTML = html;
            // innerHTML does not execute <script> tags — run them manually so that
            // _h5CoordsById[runId] is populated before the MutationObserver fires.
            container.querySelectorAll('script').forEach(function(s) {
                var ns = document.createElement('script');
                ns.textContent = s.textContent;
                document.head.appendChild(ns);
                document.head.removeChild(ns);
            });
        })
        .catch(function(e) { container.innerHTML = '<p style="color:var(--pico-del-color)">Error: ' + e.message + '</p>'; });
};

/**
 * Fetch plot data for a HDF5 variable and render with Plotly.
 */
/* ------------------------------------------------------------------ */
/* HDF5 Multi-Plot: selection state + rendering                        */
/* ------------------------------------------------------------------ */

/**
 * _dsLastPlot schema:
 *   { which: 'ds_raw'|'ds_fit', experimentType: string,
 *     selections: [{varName, dims, qubitIdx}] }
 */
window._dsLastPlot = null;

/** Read the experiment type from the currently-shown dataset detail. */
function _currentExperimentType() {
    var root = document.getElementById('ds-detail-root');
    return root ? root.getAttribute('data-experiment') : null;
}

/** Detect whether dims contains a qubit-like coordinate; return {idx, labels} or null. */
function _findQubitDim(dims, coords) {
    for (var i = 0; i < dims.length; i++) {
        var cv = coords[dims[i]];
        var isQ = dims[i] === 'qubit' ||
            (cv && cv.length <= 10 && cv.every(function(v) { return typeof v === 'string'; }));
        if (isQ) return { idx: i, labels: cv || null };
    }
    return null;
}

/** Count qubit labels in dims (used for range-checking sticky state). */
function _getQubitCount(dims, coords) {
    var q = _findQubitDim(dims, coords);
    return (q && q.labels) ? q.labels.length : 0;
}

/** Toggle a (varName, qubitIdx) combo in _dsLastPlot.selections. */
function _toggleSelection(which, varName, dims, qubitIdx) {
    if (!window._dsLastPlot) {
        window._dsLastPlot = { which: which, experimentType: _currentExperimentType(), selections: [] };
    }
    var sels = window._dsLastPlot.selections;
    var pos = -1;
    for (var i = 0; i < sels.length; i++) {
        if (sels[i].varName === varName && sels[i].qubitIdx === qubitIdx) { pos = i; break; }
    }
    if (pos >= 0) {
        sels.splice(pos, 1);
    } else {
        sels.push({ varName: varName, dims: dims, qubitIdx: qubitIdx });
    }
    _updateVarRowStates();
}

/** Return true if the given (varName, qubitIdx) is currently selected. */
function _hasSelection(varName, qubitIdx) {
    if (!window._dsLastPlot || !window._dsLastPlot.selections) return false;
    for (var i = 0; i < window._dsLastPlot.selections.length; i++) {
        var s = window._dsLastPlot.selections[i];
        if (s.varName === varName && s.qubitIdx === qubitIdx) return true;
    }
    return false;
}

/** Reflect selection state onto h5-vars-table rows (highlight + button label). */
function _updateVarRowStates() {
    // Build Set for O(1) lookups instead of O(n) .some() per row
    var selectedVars = new Set();
    if (window._dsLastPlot && window._dsLastPlot.selections) {
        window._dsLastPlot.selections.forEach(function(s) { selectedVars.add(s.varName); });
    }
    document.querySelectorAll('.h5-vars-table tbody tr').forEach(function(row) {
        var code = row.querySelector('td:first-child code');
        if (!code) return;
        var hasAny = selectedVars.has(code.textContent.trim());
        row.classList.toggle('h5-var-selected', hasAny);
        var btn = row.querySelector('button');
        if (btn) btn.textContent = hasAny ? 'Remove' : 'Plot';
    });
}

// ── Interactive tab: faithful experiment-figure reproductions ──────
// Mirrors loadDatasetH5 / _fetchAndRenderPlot, but the figures come from
// the recipe layer (/dataset/<id>/interactive[/plot]) and may be clickable.

/**
 * Load the Interactive figure menu for a run, then lazy-render each figure
 * (via IntersectionObserver) as its tile scrolls into view.
 * @param {number} runId
 * @param {Element} [panel]  enclosing dataset column (pinned/split scoping)
 */
window.loadDatasetInteractive = function(runId, panel) {
    panel = panel || document.getElementById('inspector-pane') || document;
    var container = panel.querySelector ? panel.querySelector('[id$="interactive-container"]')
                                        : document.getElementById('ds-interactive-container');
    if (!container || container.getAttribute('data-loaded') === '1') return;
    container.setAttribute('data-loaded', '1');
    // Per-container generation: bumping invalidates in-flight tile fetches so a
    // stale dataset's response can't paint into a reused container.
    container._gen = (container._gen || 0) + 1;
    var gen = container._gen;
    // Drop any observer left from a prior load of this container.
    if (container._io) { try { container._io.disconnect(); } catch (e) {} container._io = null; }
    container._rendered = [];

    fetch('/dataset/' + runId + '/interactive')
        .then(function(r) { return r.text(); })
        .then(function(html) {
            if (container._gen !== gen) return;  // superseded while fetching
            container.innerHTML = html;
            // Apply the persisted column count (default 2) + reflect it in the toolbar.
            var savedCols = parseInt(localStorage.getItem('quam_interactive_cols'), 10);
            if (!(savedCols >= 1 && savedCols <= 3)) savedCols = 2;
            var listEl = container.querySelector('.ds-interactive-list');
            if (listEl) listEl.style.setProperty('--ds-cols', savedCols);
            container.querySelectorAll('.ds-col-btn').forEach(function(b) {
                b.classList.toggle('active', parseInt(b.getAttribute('data-cols'), 10) === savedCols);
            });
            var plots = container.querySelectorAll('.ds-interactive-plot');
            if (!plots.length) return;
            if (typeof IntersectionObserver === 'undefined') {
                plots.forEach(function(p) { _fetchInteractiveFig(p, runId, p.getAttribute('data-fig'), gen, container); });
                return;
            }
            // Two-way observer (never unobserve): render tiles entering view,
            // purge offscreen tiles beyond the keep-alive budget. Re-entry
            // re-renders because the tile stays observed.
            var io = new IntersectionObserver(function(entries) {
                entries.forEach(function(e) {
                    e.target._isVisible = e.isIntersecting;
                    if (e.isIntersecting) {
                        _fetchInteractiveFig(e.target, runId, e.target.getAttribute('data-fig'), gen, container);
                    }
                });
                _pruneInteractiveTiles(container);
            }, { rootMargin: '200px' });
            container._io = io;
            plots.forEach(function(p) { io.observe(p); });
            // docs/118: attach the size watcher at BOTH mount points, so a
            // container reached by any path (not just a tab click) tracks its
            // own geometry.
            _observeInteractiveResize(container);
        })
        .catch(function(e) {
            if (container._gen !== gen) return;
            container.innerHTML = '<p style="color:var(--pico-del-color)">Error: ' + e.message + '</p>';
            container.setAttribute('data-loaded', '0');
        });
};

/**
 * Strategy-B: reproduce a run's figures by re-running its own plotting.py in the
 * selected QM env. Mirrors loadDatasetInteractive but targets #ds-replot-container
 * and points its tiles at the replot endpoint (via data-endpoint in the partial).
 * @param {string|number} runId  dataset uid
 * @param {Element} [panel]       enclosing tab/column (scoping)
 * @param {boolean} [force]       re-run even if cached (Regenerate / analysis edit)
 */
window.loadDatasetReplot = function(runId, panel, force) {
    panel = panel || document.getElementById('inspector-pane') || document;
    // Suffix/contains match (not prefix) so the selectors survive the "pinned-"
    // id prefix added when a dataset column is pinned — mirrors loadDatasetInteractive.
    var container = panel.querySelector ? panel.querySelector('[id$="ds-replot-container"]')
                                        : document.getElementById('ds-replot-container');
    if (!container) return;
    var btn = panel.querySelector ? panel.querySelector('[id*="ds-replot-btn-"]') : null;
    if (!force && container.getAttribute('data-loaded') === '1') return;
    container.setAttribute('data-loaded', '1');
    container._gen = (container._gen || 0) + 1;
    var gen = container._gen;
    if (container._io) { try { container._io.disconnect(); } catch (e) {} container._io = null; }
    container._rendered = [];
    if (btn) { btn.setAttribute('aria-busy', 'true'); btn.disabled = true; }
    container.innerHTML = '<p class="muted" style="padding:1rem">Re-running the experiment plotting… (first run can take a few seconds)</p>';

    fetch('/dataset/' + runId + '/replot' + (force ? '?force=1' : ''))
        .then(function(r) { return r.text(); })
        .then(function(html) {
            if (container._gen !== gen) return;
            container.innerHTML = html;
            if (btn) { btn.removeAttribute('aria-busy'); btn.disabled = false; }
            var savedCols = parseInt(localStorage.getItem('quam_interactive_cols'), 10);
            if (!(savedCols >= 1 && savedCols <= 3)) savedCols = 2;
            var listEl = container.querySelector('.ds-interactive-list');
            if (listEl) listEl.style.setProperty('--ds-cols', savedCols);
            container.querySelectorAll('.ds-col-btn').forEach(function(b) {
                b.classList.toggle('active', parseInt(b.getAttribute('data-cols'), 10) === savedCols);
            });
            var plots = container.querySelectorAll('.ds-interactive-plot');
            if (!plots.length) return;
            if (typeof IntersectionObserver === 'undefined') {
                plots.forEach(function(p) { _fetchInteractiveFig(p, runId, p.getAttribute('data-fig'), gen, container); });
                return;
            }
            var io = new IntersectionObserver(function(entries) {
                entries.forEach(function(e) {
                    e.target._isVisible = e.isIntersecting;
                    if (e.isIntersecting) {
                        _fetchInteractiveFig(e.target, runId, e.target.getAttribute('data-fig'), gen, container);
                    }
                });
                _pruneInteractiveTiles(container);
            }, { rootMargin: '200px' });
            container._io = io;
            plots.forEach(function(p) { io.observe(p); });
            // docs/118: attach the size watcher at BOTH mount points, so a
            // container reached by any path (not just a tab click) tracks its
            // own geometry.
            _observeInteractiveResize(container);
        })
        .catch(function(e) {
            if (container._gen !== gen) return;
            if (btn) { btn.removeAttribute('aria-busy'); btn.disabled = false; }
            container.innerHTML = '<p style="color:var(--pico-del-color)">Error: ' + e.message + '</p>';
            container.setAttribute('data-loaded', '0');
        });
};

/**
 * Set the Interactive grid's column count (1–3), persist it, mark the active
 * toolbar button, and resize already-rendered Plotly plots to the new cell
 * width. Scoped to the interactive container holding the clicked button so
 * pinned/split dataset columns stay independent.
 * @param {number} n    columns (clamped 1–3)
 * @param {Element} [btn] the clicked toolbar button (for scoping)
 */
window.setInteractiveCols = function(n, btn) {
    n = Math.max(1, Math.min(3, parseInt(n, 10) || 2));
    var scope = (btn && btn.closest && btn.closest('[id$="interactive-container"]'))
             || document.getElementById('ds-interactive-container')
             || document;
    scope.querySelectorAll('.ds-interactive-list').forEach(function(list) {
        list.style.setProperty('--ds-cols', n);
    });
    scope.querySelectorAll('.ds-col-btn').forEach(function(b) {
        b.classList.toggle('active', parseInt(b.getAttribute('data-cols'), 10) === n);
    });
    try { localStorage.setItem('quam_interactive_cols', String(n)); } catch (e) {}
    // docs/122: the last raw Plots.resize on this path. A 1<->3 column change is
    // the biggest width step any tile ever takes, and it went through the call
    // measured as a no-op, over the class that does not always survive.
    // requestAnimationFrame because --ds-cols has only just been written: the
    // grid tracks have not been laid out yet, so reading clientWidth in this
    // turn would resize every tile to its OLD width.
    var _rz = function () { window.resizeInteractiveTiles(scope); };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(_rz);
    else _rz();
};

/**
 * docs/118: re-size every rendered interactive tile inside `scope`.
 *
 * Plotly fixes a plot's pixel size at draw time. These tiles are drawn once and
 * then survive tab switches (CSS `hidden`), split-preset changes (clicking a
 * sidebar menu collapses the split, base.html) and window resizes — none of
 * which redrew them. Coming back therefore showed figures still sized for the
 * geometry they were drawn in: squashed, clipped, or with a stranded modebar.
 * `setInteractiveCols` was the ONLY caller of Plots.resize before this.
 */
/* docs/122 item 4 — ONE place for the three things every Plotly mount needs.
 *
 * This bug class keeps being re-fixed per surface: docs/118 fixed resize for the
 * Interactive tiles, docs/120 fixed a WebGL blank on Chip Status Trends, and
 * docs/122 arrived at Trends again for the resize half. An inventory of the app
 * found 15 Plotly mounts, of which 13 handle no CONTAINER resize (Plotly's
 * `responsive:true` listens to WINDOW resize only — measured on Plotly 2.35.2:
 * collapsing the sidebar left a 609 px SVG in a 742 px holder and it never
 * healed in 6 s, while a window resize healed it instantly) and 7 destroy live
 * plots with innerHTML/outerHTML without purging first, which the app's own rule
 * (PaneState._purge) forbids.
 *
 * So: a helper, plus one global purge-on-swap so the rule is enforced by the
 * framework rather than remembered at 15 call sites. */
window.PlotHost = (function () {
    /* Finding the graph divs is NOT `.js-plotly-plot`.
       That class is Plotly's own marker and the whole app selects on it — but it
       does not always survive here. Measured on the real chip: a Chip Status
       Trends holder carried `_fullLayout`, a populated `.data`, three
       `svg.main-svg` children and Plotly's own `<div class="plot-container
       plotly">` — and its class attribute was exactly "topo-trend-chart".
       `querySelectorAll('.js-plotly-plot')` inside that grid returned ZERO, so
       the ResizeObserver fired (verified: 2 hits) against nothing at all and the
       first version of this fix silently did not work.
       `.plot-container.plotly` is structure Plotly always builds, so its parent
       is the graph div whether or not the class stuck. Union with the class so
       nothing that DOES carry it is missed, and de-duplicate. */
    function _graphDivs(root) {
        var r = root || document;
        if (!r.querySelectorAll) return [];
        var out = [], seen = [];
        function add(el) {
            if (!el || seen.indexOf(el) >= 0) return;
            seen.push(el); out.push(el);
        }
        // The ROOT ITSELF counts. querySelectorAll never returns its own
        // context node, and an `outerHTML` swap replaces exactly that node — so
        // a purge driven off the swap target used to skip the one graph div
        // most certain to be destroyed.
        if (r.nodeType === 1 && (r.classList && r.classList.contains('js-plotly-plot')
                                 || (r.querySelector && r.querySelector(':scope > .plot-container.plotly')))) {
            add(r);
        }
        Array.prototype.forEach.call(r.querySelectorAll('.js-plotly-plot'), add);
        Array.prototype.forEach.call(r.querySelectorAll('.plot-container.plotly'),
            function (n) { add(n.parentElement); });
        return out;
    }
    /* Plotly.Plots.resize is the documented call for this and it NO-OPS here.
       Measured on the real chip after collapsing the sidebar: the holder was
       1531 px wide, `_fullLayout.width` was 1265 with `autosize: true`, the
       element was displayed — and it was still 1265 after Plots.resize AND
       after relayout({autosize:true}). An explicit width moves it immediately.

       WIDTH ONLY, and no release back to autosize. The first version followed
       the width with `relayout({width: null, autosize: true})` to keep Plotly's
       own responsiveness — but Plotly's implied-edit table makes `autosize`
       imply `height: null`, so that second call DELETES the caller's explicit
       layout height. On Chip Status Trends that is invisible, and only because
       `.topo-trend-chart { min-height: 300px }` (style.css) happens to equal the
       300 the caller asked for — verified in a browser: the rendered height held
       at 300 while `layout.height` was gone. It would NOT be invisible on
       ndview (`height: 420`, CSS min-height 200) or on the Chip Status bar
       charts (height computed 160–640, no CSS height at all), which is exactly
       where this was about to be extended. Releasing autosize is also
       unnecessary: an observer watching the CONTAINER already fires on a window
       resize, because that is what changes the container.

       Chained through the element's own render chain, not fired beside it: a
       relayout racing an in-flight newPlot is the collision _plotlyRender was
       written to serialise. */
    function resizeWithin(root) {
        if (typeof Plotly === 'undefined') return 0;
        var n = 0;
        _graphDivs(root).forEach(function (el) {
            if (!el.offsetParent) return;   // hidden — resizing is a no-op
            var w = el.clientWidth;
            if (!w) return;
            var cur = el._fullLayout && el._fullLayout.width;
            if (cur && Math.abs(cur - w) < 2) return;   // already matched
            try {
                var prev = el.__plotlyRenderChain || Promise.resolve();
                el.__plotlyRenderChain = prev.catch(function () {}).then(function () {
                    if (!document.body.contains(el) || !el.offsetParent) return null;
                    var w2 = el.clientWidth;      // re-read: the layout may have moved again
                    if (!w2) return null;
                    /* Snapshot-restore (docs/124 M-1, payload chosen by an
                       executed 6-candidate × 3-shape probe — docs/125 fix 5).
                       Plotly 2.35.2's width relayout implies autosize=null AND
                       pins the OTHER dimension, and Plots.resize — the
                       responsive:true window handler — permanently rejects
                       once layout.width && layout.height are both set. So one
                       bare width touch froze every chart against window
                       resizes forever (168px-clipped bar charts, executed).
                       The cure: apply the width, then hand gd.layout back
                       exactly as the caller wrote it — fullLayout keeps the
                       correction, layout.width is absent again, the window
                       path stays alive, and a DECLARED layout.height survives
                       (which stock Plotly itself loses on window resizes —
                       the restored state is byte-identical to an untouched
                       chart's). relayout({autosize:true}) is NOT an
                       alternative: its implied width:null never deletes an
                       existing layout.width key in this Plotly. The snapshot
                       is taken INSIDE the chain so back-to-back touches each
                       see the restored layout. */
                    var lay = el.layout || {};
                    var has = Object.prototype.hasOwnProperty;
                    var snap = {
                        width:    has.call(lay, 'width')    ? lay.width    : undefined,
                        height:   has.call(lay, 'height')   ? lay.height   : undefined,
                        autosize: has.call(lay, 'autosize') ? lay.autosize : undefined
                    };
                    return Plotly.relayout(el, { width: w2 }).then(function () {
                        var l2 = el.layout || {};
                        ['width', 'height', 'autosize'].forEach(function (k) {
                            if (snap[k] === undefined) delete l2[k]; else l2[k] = snap[k];
                        });
                    });
                }).catch(function () {});
                n++;
            } catch (e) {}
        });
        return n;
    }
    /* Watch a CONTAINER, not the window: the geometry changes that break these
       figures are a sidebar collapse and a split-gutter drag, neither of which
       is a window resize. Debounced, and idempotent per container. */
    var _observed = [];   // every container we hold an observer on
    function observe(container) {
        if (!container || container._phRo || typeof ResizeObserver === 'undefined') return;
        // SINGLE OWNER per subtree. An ancestor already watching this region
        // would resize the same divs on the same event, and docs/118's own
        // interactive container (`_ro`) is exactly such an ancestor. Two
        // observers driving two strategies at one node is how the Pulses plot
        // was measurably broken once already.
        for (var a = container.parentElement; a; a = a.parentElement) {
            if (a._phRo || a._ro) return;
        }
        var t = null, lastW = 0;
        container._phRo = new ResizeObserver(function (entries) {
            // WIDTH-only. RO fires on both axes and #table-pane's content height
            // moves on every banner, tray or toast — a height-only change must
            // not cost a relayout per chart.
            var w = 0;
            try { w = Math.round((entries[0].contentRect || {}).width || 0); } catch (e) {}
            if (w && Math.abs(w - lastW) < 2) return;
            lastW = w;
            clearTimeout(t);
            t = setTimeout(function () { resizeWithin(container); }, 120);
        });
        try {
            container._phRo.observe(container);
            _observed.push(container);
        } catch (e) { container._phRo = null; }
    }
    function unobserve(container) {
        if (container && container._phRo) {
            try { container._phRo.disconnect(); } catch (e) {}
            container._phRo = null;
        }
        var i = _observed.indexOf(container);
        if (i >= 0) _observed.splice(i, 1);
    }
    /* Teardown belongs at the same choke point as the purge, or it never
       happens: `unobserve` shipped with ZERO callers while ChipTrends._reload
       swaps its observed grid with outerHTML on EVERY metric toggle, so the app
       leaked one ResizeObserver — and the detached subtree it strongly
       references — per toggle. */
    function unobserveWithin(root) {
        var r = root || document;
        var n = 0;
        // Walk the REGISTRY, not the DOM: a swap target can be #table-pane,
        // whose subtree on the real chip is tens of thousands of nodes, and
        // paying a full-tree query per swap to find at most a handful of
        // observers is the wrong trade. Also sweeps entries whose element has
        // already been detached by some other path.
        _observed = _observed.filter(function (el) {
            var gone = !document.body || !document.body.contains(el);
            // Detached-but-PARKED is not dead: PaneState will re-attach that
            // subtree and its observations resume on their own (docs/124, the
            // parked-observer minor).
            if (gone && window.PaneState && window.PaneState.holdsDetached
                && window.PaneState.holdsDetached(el)) gone = false;
            if (gone || r === el || (r.contains && r.contains(el))) {
                if (el._phRo) { try { el._phRo.disconnect(); } catch (e) {} el._phRo = null; }
                n++;
                return false;
            }
            return true;
        });
        return n;
    }
    /* A Plotly node must never die via innerHTML without purge — WebGL contexts
       and DOM references leak. Safe to call on anything: purging a node that is
       about to be discarded cannot break it. */
    function purgeWithin(root) {
        if (typeof Plotly === 'undefined') return 0;
        var n = 0;
        _graphDivs(root).forEach(function (el) {
            try { Plotly.purge(el); n++; } catch (e) {}
        });
        return n;
    }
    return { resizeWithin: resizeWithin, observe: observe,
             unobserve: unobserve, unobserveWithin: unobserveWithin,
             purgeWithin: purgeWithin, graphDivs: _graphDivs,
             _observed: function () { return _observed.slice(); } };
})();

/* Enforce the purge rule at the ONE place every destructive swap goes through.
 * Additive by construction: the nodes are about to be replaced anyway.
 * The exception is a pane PaneState is about to PARK — those plots are meant to
 * come back alive, and purging them would hand the user a corpse on return. */
function _plotSwapTeardown(evt) {
    var t = evt && evt.target;
    if (!t || !t.querySelectorAll) return;
    if (evt.detail && evt.detail.shouldSwap === false) return;
    if (t.id === 'table-pane' && window.PaneState && window.PaneState.isKeepRoute
        && window.PaneState.isKeepRoute()) return;
    window.PlotHost.purgeWithin(t);
    // ...and release the observers on what is being destroyed, at the same
    // choke point, or they strand on a detached subtree they keep alive.
    window.PlotHost.unobserveWithin(t);
}
document.addEventListener('htmx:beforeSwap', _plotSwapTeardown);
// OOB swaps fire their OWN event and used to bypass the door entirely
// (docs/124 note) — same rules, same handler.
document.addEventListener('htmx:oobBeforeSwap', _plotSwapTeardown);

window.resizeInteractiveTiles = function(scope) {
    // Scope preserved exactly (docs/118 pins assert .ds-interactive-list only);
    // the implementation is now shared.
    var root = scope || document;
    if (root.classList && root.classList.contains('ds-interactive-list')) {
        return void window.PlotHost.resizeWithin(root);
    }
    if (!root.querySelectorAll) return;
    Array.prototype.forEach.call(root.querySelectorAll('.ds-interactive-list'),
        function (l) { window.PlotHost.resizeWithin(l); });
};

/**
 * docs/118: revive interactive markup that was round-tripped through a STRING.
 *
 * Pin & Browse serializes the pane (`clone.innerHTML`), and unpin / close-keep
 * do `pane.innerHTML = otherCol.innerHTML` — after purging the live plots. What
 * lands back in the DOM is therefore SVG with no Plotly instance behind it,
 * still carrying `data-loaded="1"` on the container and `data-rendered="1"` on
 * every tile. Those two flags are exactly what the loader and the tile fetcher
 * check, so they refuse to rebuild: the figures look drawn, respond to nothing,
 * and can never come back. Resetting the flags (and dropping the corpse markup)
 * is what lets the ordinary lazy path draw them again.
 */
function _reviveInteractiveMarkup(root) {
    if (!root || !root.querySelectorAll) return;
    root.querySelectorAll('[id$="interactive-container"]').forEach(function(c) {
        c.setAttribute('data-loaded', '0');
        c._rendered = [];
        if (c._io) { try { c._io.disconnect(); } catch (e) {} c._io = null; }
        c._gen = (c._gen || 0) + 1;
        if (c._ro) { try { c._ro.disconnect(); } catch (e) {} c._ro = null; }
    });
    root.querySelectorAll('.ds-interactive-plot').forEach(function(t) {
        t.setAttribute('data-rendered', '0');
        t.innerHTML = '';
    });
}
window._reviveInteractiveMarkup = _reviveInteractiveMarkup;

/** docs/118: keep tiles matched to their container without polling. */
function _observeInteractiveResize(container) {
    if (!container || container._ro || typeof ResizeObserver === 'undefined') return;
    var t = null;
    container._ro = new ResizeObserver(function() {
        clearTimeout(t);
        t = setTimeout(function() { window.resizeInteractiveTiles(container); }, 120);
    });
    try { container._ro.observe(container); } catch (e) { container._ro = null; }
}

/**
 * Fetch one interactive figure's Plotly JSON and render it. Attaches the
 * click-to-edit handler when the recipe marked the figure clickable.
 */
function _fetchInteractiveFig(div, runId, figKey, gen, container) {
    if (div.getAttribute('data-rendered') === '1') return;
    div.setAttribute('data-rendered', '1');
    div.innerHTML = '<div class="plot-skeleton" aria-label="Loading figure"></div>';
    // Stale-response guard: if the container was reused for another dataset
    // (gen bumped) or the tile was detached, drop the response.
    function stale() {
        return (container && container._gen !== gen) || !document.body.contains(div);
    }
    // Tiles default to the recipe layer; replot tiles set data-endpoint="replot/plot"
    // so the same render/observer/clickable machinery serves both sources.
    var endpoint = div.getAttribute('data-endpoint') || 'interactive/plot';
    fetch('/dataset/' + runId + '/' + endpoint + '?fig=' + encodeURIComponent(figKey))
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (stale()) return;
            if (d.error) {
                div.innerHTML = '<p style="color:var(--pico-del-color)">' + d.error + '</p>';
                div.setAttribute('data-rendered', '0');
                return;
            }
            div.innerHTML = '';
            var inner = document.createElement('div');
            inner.style.width = '100%';
            inner.style.height = '360px';
            div.appendChild(inner);
            // r16 ⑤-1: HOUSE THEME — the raw server layout carries no colors,
            // so Plotly's light template painted near-black axis text on the
            // dark page (ndview was the only themed consumer).
            var layout = (window.PlotTheme && PlotTheme.houseLayout)
                ? window.PlotTheme.houseLayout(d.layout || {}) : (d.layout || {});
            if (!layout.height) layout.height = 360;
            // _plotlyRender lazy-loads Plotly and returns a promise; attach the
            // click handler only once the figure exists.
            Promise.resolve(_plotlyRender(inner, d.data, layout, {responsive: true})).then(function() {
                if (stale()) { _purgeInteractiveTile(div); return; }
                if (d.clickable && typeof _attachInteractivePlotClickHandler === 'function') {
                    _attachInteractivePlotClickHandler(inner, d.clickable, runId);
                    inner.style.cursor = 'pointer';
                    div.setAttribute('title', 'Click a point to edit the corresponding parameter');
                }
            });
            // Track in the keep-alive pool (most-recent last) + prune offscreen.
            if (container) {
                container._rendered = container._rendered || [];
                var ri = container._rendered.indexOf(div);
                if (ri !== -1) container._rendered.splice(ri, 1);
                container._rendered.push(div);
                _pruneInteractiveTiles(container);
            }
        })
        .catch(function(e) {
            if (stale()) return;
            div.innerHTML = '<p style="color:var(--pico-del-color)">' + e.message + '</p>';
            div.setAttribute('data-rendered', '0');
        });
}

/**
 * Fetch a single plot from the backend and render it into container.
 */
function _fetchAndRenderPlot(container, runId, which, varName, qubitIdx) {
    container.innerHTML = '<div class="plot-skeleton" aria-label="Loading plot"></div>';
    var url = '/dataset/' + runId + '/h5/plot?which=' + encodeURIComponent(which) +
              '&var=' + encodeURIComponent(varName);
    if (qubitIdx !== null && qubitIdx !== undefined) url += '&qubit=' + qubitIdx;
    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            // Drop the response if the pane was swapped out while fetching.
            if (!document.body.contains(container)) return;
            if (data.error) {
                container.innerHTML = '<p style="color:var(--pico-del-color)">Error: ' + data.error + '</p>';
                return;
            }
            container.innerHTML = '';
            var plotDiv = document.createElement('div');
            plotDiv.style.width = '100%';
            plotDiv.style.height = UI_CONFIG.plotly.h5Plot.height + 'px';
            container.appendChild(plotDiv);
            var layout = (window.PlotTheme && PlotTheme.houseLayout)
                ? window.PlotTheme.houseLayout(data.layout || {}) : (data.layout || {});   // r16 ⑤-1
            layout.margin = UI_CONFIG.plotly.h5Plot.margin;
            layout.height = UI_CONFIG.plotly.h5Plot.height;
            Promise.resolve(_plotlyRender(plotDiv, data.traces, layout, {responsive: true})).then(function() {
                if (!document.body.contains(plotDiv)) return;
                if (data.qubit_names) {
                    plotDiv.setAttribute('data-qubit-names', data.qubit_names.join(','));
                }
                _attachPlotClickHandler(plotDiv);
            });
        })
        .catch(function(e) {
            if (!document.body.contains(container)) return;
            container.innerHTML = '<p style="color:var(--pico-del-color)">Error: ' + e.message + '</p>';
        });
}

// ── Plot click → copy x,y → navigate to Explorer ───────────────────

function _getRunQubits() {
    var root = document.getElementById('ds-detail-root');
    return root ? (root.getAttribute('data-qubits') || '').split(',').filter(Boolean) : [];
}

// ── Experiment name → state.json dot-path mapping ──────────────────
// Maps experiment names to arrays of {axis, path} objects.
// axis: which plot coordinate ('x' or 'y') provides the value.
// path: state.json dot-path template.  {name} = qubit, {pair} = qubit pair.
var EXPERIMENT_PATH_MAP = {
    'time_of_flight':           [{axis: 'x', path: 'qubits.{name}.resonator.time_of_flight'}],
    'resonator_spectroscopy':   [{axis: 'x', path: 'qubits.{name}.resonator.f_01'}],
    'qubit_spectroscopy':       [
        {axis: 'x', path: 'qubits.{name}.f_01'},
        {axis: 'x', path: 'qubits.{name}.xy.RF_frequency'},
    ],
    'qubit_spectroscopy_vs_flux': [
        {axis: 'x', path: 'qubits.{name}.z.joint_offset'},
        {axis: 'y', path: 'qubits.{name}.xy.RF_frequency'},
        {axis: 'y', path: 'qubits.{name}.f_01'},
    ],
};

/**
 * Resolve experiment name → array of {axis, path} with qubit/pair substituted.
 * Returns null if no mapping found.
 */
function _resolveExperimentPath(experimentName, qubitName) {
    var key = (experimentName || '').toLowerCase().replace(/[_\s]+/g, '_').replace(/_+$/, '');
    var mappings = EXPERIMENT_PATH_MAP[key];
    if (!mappings) {
        for (var k in EXPERIMENT_PATH_MAP) {
            if (key.indexOf(k) >= 0 || k.indexOf(key) >= 0) {
                mappings = EXPERIMENT_PATH_MAP[k];
                break;
            }
        }
    }
    if (!mappings) return null;
    return mappings.map(function(m) {
        var p = m.path;
        if (qubitName) p = p.replace('{name}', qubitName).replace('{pair}', qubitName);
        return {axis: m.axis, path: p};
    });
}

function _attachPlotClickHandler(plotDiv) {
    // docs/118: clearing first is what makes a re-render idempotent. Without
    // it, any path that draws into the SAME node twice (Plotly.react) leaves two
    // handlers, and one click stages the edit twice. ndview.js has done this
    // since docs/67; the interactive tiles never did.
    try { if (plotDiv.removeAllListeners) plotDiv.removeAllListeners('plotly_click'); } catch (e) {}
    plotDiv.on('plotly_click', function(eventData) {
        if (!eventData || !eventData.points || !eventData.points.length) return;
        var pt = eventData.points[0];
        var x = pt.x, y = pt.y;

        // Build coordinate text + copy to clipboard
        var text = 'x=' + x + ', y=' + y;
        if (pt.z !== undefined) text += ', z=' + pt.z;
        if (navigator.clipboard) {
            navigator.clipboard.writeText(text).catch(function() {});
        }

        // Resolve qubit name from customdata → trace name → fallback
        var qubitName = null;
        if (pt.customdata) qubitName = String(pt.customdata).trim();
        if (!qubitName && pt.data && pt.data.name) qubitName = pt.data.name.trim();
        if (!qubitName) {
            var qubits = _getRunQubits();
            if (qubits.length === 1) qubitName = qubits[0];
        }

        // Resolve experiment name → field mappings
        var root = document.getElementById('ds-detail-root');
        var expName = root ? root.getAttribute('data-experiment') : '';
        var mappings = _resolveExperimentPath(expName, qubitName);
        var dotPath = mappings ? mappings[0].path : null;

        // Show clipboard toast
        _showPlotClickToast(text, qubitName, dotPath);

        // Open the confirmation popup (replaces the old auto-apply flow);
        // Explorer-tree navigation still happens in the background as a
        // contextual hint, regardless of whether the popup actually opens.
        if (mappings && mappings.length) {
            _showPlotApplyPopup(mappings, pt, expName, qubitName);
            _navigateToExplorerPath(dotPath);
        } else if (dotPath) {
            _navigateToExplorerPath(dotPath);
        }
    });
}

/* ------------------------------------------------------------------ */
/* Plot click → confirmation popup                                     */
/* ------------------------------------------------------------------ */
/* Replaces the old auto-apply behavior: clicking a Plotly point now
   opens a popup with one row per affected dot-path (path · old value ·
   editable new value · Apply button), plus an Apply-All button when
   multiple rows exist. Per-row Apply keeps the popup open and marks
   that row as applied; Cancel / × dismisses without writing.  Atomic
   batch apply uses the /field/edit-batch endpoint backed by
   modifier.batch_set so partial failures roll back cleanly. */

function _ppEscape(s) {
    return String(s).replace(/[&<>"']/g, function(c) {
        return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
}

/* Async, non-blocking fit-audit verdict badge on the apply popup: asks whether the
   CURRENT hardened gate would still accept this run's fit for this qubit. Advisory
   ONLY — it never gates Apply, and silently shows nothing when the run isn't an
   auditable family / no env / the check fails (mirrors the domain-warning contract).
   A generation token discards a stale in-flight result if the popup is reopened. */
function _fetchApplyVerdict(qubitName, compute) {
    var slot = document.getElementById('plot-apply-verdict');
    if (!slot) return;
    slot.hidden = true;
    slot.innerHTML = '';
    var root = document.getElementById('ds-detail-root');
    var uid = root ? root.getAttribute('data-uid') : '';
    if (!uid || !qubitName) return;   // no run context / no qubit → no badge
    var gen = (window.__pavGen = (window.__pavGen || 0) + 1);
    slot.hidden = false;
    slot.innerHTML = '<div class="pp-verdict-loading">' +
        (compute ? 'checking this fit against the current gate… (up to a minute)' : 'checking…') + '</div>';
    // On an explicit "Check" (keyboard/AT), the button just self-destructed — hand
    // focus to this live region so containment holds and the result is announced.
    if (compute) { try { slot.focus(); } catch (e) {} }
    // Warm-only by default (instant if a sweep/prior check cached it); the returned
    // "check" affordance's button opts into the slow-cold replay on demand.
    var url = '/fit-audit/verdict?uid=' + encodeURIComponent(uid)
            + '&qubit=' + encodeURIComponent(qubitName) + (compute ? '&compute=1' : '');
    fetch(url, {headers: {'HX-Request': 'true'}})
        .then(function(r) { return r.status === 200 ? r.text() : ''; })
        .then(function(html) {
            if (window.__pavGen !== gen) return;   // superseded by a newer popup
            if (html && html.trim()) {
                slot.innerHTML = html;
                slot.hidden = false;
                var btn = document.getElementById('pp-verdict-check-btn');
                if (btn) btn.addEventListener('click', function() { _fetchApplyVerdict(qubitName, true); });
            } else {
                slot.innerHTML = '';
                slot.hidden = true;
            }
        })
        .catch(function() {
            if (window.__pavGen !== gen) return;
            slot.innerHTML = '';                    // fail silent — advisory only
            slot.hidden = true;
        });
}

function _showPlotApplyPopup(mappings, pt, expName, qubitName) {
    // Data tab: build {dot_path, value} from axis→path mappings + clicked point.
    var updates = [];
    mappings.forEach(function(m) {
        var val = m.axis === 'x' ? pt.x : pt.y;
        if (val === undefined || val === null) return;
        updates.push({dot_path: m.path, value: val});
    });
    _openPlotApplyPopup(updates, expName, qubitName);
}

/* Open the editable parameter-apply popup for pre-computed {dot_path, value}
   updates. Shared by the Data tab (axis→path mappings) and the Interactive tab
   (recipe `clickable` spec). Activates the loaded state first so edits target it. */
function _openPlotApplyPopup(updates, expName, qubitName, contextRows, chipExpect) {
    if (!updates || !updates.length) return;
    // chipExpect = {token, name} for a dataset fit-apply: the run's OWN chip
    // identity. We carry it into every Apply so the server refuses (409) to
    // write a run's fit onto a different loaded chip that reuses qubit names.
    var expect = (chipExpect && chipExpect.token) ? chipExpect : null;
    function render() {
        // Even if activation failed, still render — the popup shows real
        // per-row errors when Apply is clicked.
        _renderPlotApplyPopup(updates, expName, qubitName, contextRows, expect);
        _fetchPlotApplyOldValues(updates);
    }
    // Cross-chip pre-check: warn BEFORE the popup if the loaded chip isn't
    // the chip this fit came from. Cancel aborts; OK marks it force-applied.
    function preCheckAndRender(act) {
        // Freshen the render-time active path (the popup's "Target chip:" line
        // reads it) — an in-page context switch may have outdated the baked one.
        if (act && act.path) window.__activePath = act.path;
        if (expect && act && act.token && expect.token && act.token !== expect.token) {
            var ok = window.confirm(
                'This fitted value is from chip "' + (expect.name || '?') +
                '", but the loaded chip is "' + (act.name || '?') + '".\n\n' +
                'Applying will write ' + (expect.name || 'that chip') +
                '’s value onto the loaded chip. Continue anyway?');
            if (!ok) return;               // abort — nothing rendered
            expect.forced = true;          // user accepted → send force_chip
        }
        render();
    }
    // The ACTIVE chip is authoritative. The old flow read the load-path text
    // box and silently POSTed /load on it — so a researcher who had switched
    // chips via the sidebar got their context flipped BACK to the stale box
    // path on a plot click (and, for tokenless runs, the value written to that
    // stale chip). Only when NOTHING is loaded fall back to activating the box
    // path (first-use convenience).
    fetch('/chip/active-token').then(function (r) { return r.json(); })
        .then(function (act) {
            if (act && act.loaded) { preCheckAndRender(act); return; }
            var loadInput = document.getElementById('load-path-input');
            var statePath = loadInput ? loadInput.value.trim() : '';
            if (!statePath) {
                _showPlotClickToast('No state loaded — cannot apply', null, null);
                return;
            }
            return fetch('/load', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded', 'HX-Request': 'true'},
                body: 'folder=' + encodeURIComponent(statePath),
                redirect: 'manual'
            }).then(function () {
                if (!expect) { render(); return; }
                return fetch('/chip/active-token')
                    .then(function (r2) { return r2.json(); })
                    .then(preCheckAndRender);
            });
        })
        .catch(function () {
            render();   // any failure: fail open, per-row errors still show
        });
}

/* Dataset Results tab → "Apply" a single mapped fitted value to the loaded state,
   reusing the exact same popup as an Interactive-tab plot click. The button
   carries the resolved state dot-path + (scaled) value as data-attributes
   (set server-side by core.fit_targets.resolve_fit_targets). */
function applyFitValue(btn) {
    if (!btn) return;
    var path = btn.getAttribute('data-fit-path');
    var value = btn.getAttribute('data-fit-value');  // keep as string → full precision
    if (!path || value == null) return;
    var qubit = btn.getAttribute('data-fit-qubit') || null;
    var root = document.getElementById('ds-detail-root');
    var expName = root ? root.getAttribute('data-experiment') : '';
    window._openPlotApplyPopup([{dot_path: path, value: value}], expName, qubit, [],
                               _runChipExpect(root));
}
window.applyFitValue = applyFitValue;

/* The dataset run's OWN chip identity (token + name), stamped on #ds-detail-root,
   so an apply-fit can be checked against the loaded chip (audit #1). */
function _runChipExpect(root) {
    if (!root) return null;
    var token = root.getAttribute('data-chip-token');
    if (!token) return null;  // run has no bundled quam_state → can't gate
    return {token: token, name: root.getAttribute('data-chip-name') || ''};
}

/* Dataset Results tab → "Go to state": jump to the exact state field the fitted value
   would update, shown in the Explorer (raw JSON tree) in the TOP pane while the dataset
   stays visible below. Reuses the same dot-path the Apply button carries. Collapses the
   bottom (dataset) pane to the user's configured preset so the Explorer is prominent. */
function goToFitState(btn) {
    if (!btn) return;
    var path = btn.getAttribute('data-fit-path');
    if (!path) return;
    var expect = _runChipExpect(document.getElementById('ds-detail-root'));
    function navigate() {
        if (window._applySplitPreset) window._applySplitPreset('collapsed');
        window._navigateToExplorerPath(path);
    }
    // A18: if this run is from a DIFFERENT chip than the loaded one, the field
    // may not exist there — warn instead of silently scrolling to nothing.
    if (!expect) { navigate(); return; }
    fetch('/chip/active-token').then(function (r) { return r.json(); }).then(function (act) {
        if (act && act.token && expect.token && act.token !== expect.token && window.showToast) {
            window.showToast('This field is from chip "' + (expect.name || '?') +
                '", not the loaded chip "' + (act.name || '?') + '" — it may not exist here.',
                'warning');
        }
        navigate();
    }).catch(navigate);
}
window.goToFitState = goToFitState;

/* "Apply all mapped" for one fit-results section: collect every per-row Apply
   button in the section into one multi-row popup (the popup's Apply-All handles
   the atomic batch). */
function applyAllFitValues(sectionBtn) {
    var sec = sectionBtn ? sectionBtn.closest('.detail-section') : null;
    if (!sec) return;
    var updates = [], qubit = null, expName = '';
    Array.prototype.forEach.call(sec.querySelectorAll('.fit-apply-btn'), function(b) {
        var path = b.getAttribute('data-fit-path');
        var value = b.getAttribute('data-fit-value');
        if (path && value != null) updates.push({dot_path: path, value: value});
        qubit = qubit || b.getAttribute('data-fit-qubit');
    });
    if (!updates.length) return;
    var root = document.getElementById('ds-detail-root');
    expName = root ? root.getAttribute('data-experiment') : '';
    window._openPlotApplyPopup(updates, expName, qubit, [], _runChipExpect(root));
}
window.applyAllFitValues = applyAllFitValues;

/* Interactive-tab click-to-edit: a clicked point + the recipe's `clickable`
   spec → the same editable parameter-update popup. The spec carries the target
   dot-path(s), the per-target unit transform (value = axisVal*scale + offset),
   and the figure's qubit. */
function _attachInteractivePlotClickHandler(plotDiv, clickable, runId) {
    if (!clickable || !clickable.targets || !clickable.targets.length) return;
    plotDiv.on('plotly_click', function(ev) {
        if (!ev || !ev.points || !ev.points.length) return;
        var pt = ev.points[0];

        var q = clickable.qubit || (pt.customdata != null ? String(pt.customdata).trim() : null);
        if (!q) { var qs = _getRunQubits(); if (qs.length === 1) q = qs[0]; }

        var updates = [];
        clickable.targets.forEach(function(t) {
            // Per-target axis overrides the clickable-level axis (so one click can
            // set values from both x and y — e.g. flux on y, frequency on x).
            var av = ((t.axis || clickable.axis) === 'y') ? pt.y : pt.x;
            if (av === undefined || av === null) return;
            var path = String(t.path).replace('{q}', q || '').replace('{name}', q || '');
            if (path.indexOf('{') >= 0) return;  // unresolved qubit → skip
            var value;
            if (t.transform && t.transform.type === 'dbm_to_amp') {
                var amp0 = (t.transform.scale === undefined || t.transform.scale === null)
                           ? 1 : t.transform.scale;
                value = amp0 * Math.pow(10, (av - t.transform.ref_dbm) / 20);
            } else if (t.transform && t.transform.type === 'ceil4') {
                // CZ length contract: ceil(clicked_ns / 4) * 4 (+ add).
                value = Math.ceil(av / 4) * 4 + (t.transform.add || 0);
            } else if (t.transform && t.transform.type === 'dbm_gridfs') {
                // 08b drive-power contract: the node realises a dBm as a PAIR —
                // full-scale on the 1 dB grid + waveform amplitude. Mirror of
                // recipes/qubit_spec_vs_power.fs_and_amp (parity pinned by
                // TestDbmGridFs against real patch values):
                //   fs  = clamp(ceil(P − 20·log10(max_amp)), fs_min, fs_max)
                //   amp = 10^((P − fs)/20)
                var gfs = Math.ceil(av - 20 * Math.log10(t.transform.max_amp));
                gfs = Math.min(Math.max(gfs, t.transform.fs_min), t.transform.fs_max);
                value = (t.transform.part === 'fs') ? gfs : Math.pow(10, (av - gfs) / 20);
            } else if (t.transform && t.transform.type === 'wrap01') {
                // MOD-WRAP phase contract (CZ phase compensation): the node
                // writes (pre ± clicked frame) mod 1 — 2π units. a=±1, b=pre.
                var w = t.transform.a * av + t.transform.b;
                value = ((w % 1) + 1) % 1;
            } else if (t.transform) {
                // Unknown transform type: the contract expected a COMPUTED
                // value — silently staging the raw clicked coordinate would be
                // wrong (identity ≠ the node's formula). Skip this target.
                console.warn('plot click: unknown transform type "' +
                             t.transform.type + '" for ' + path + ' — target skipped');
                return;
            } else {
                var scale = (t.scale === undefined || t.scale === null) ? 1 : t.scale;
                var offset = (t.offset === undefined || t.offset === null) ? 0 : t.offset;
                value = av * scale + offset;
            }
            // Carry the server-baked provenance through so the popup can show
            // HOW this value was computed (formula + the frozen inputs it was
            // baked against) — the trust line for contract-faithful clicks.
            updates.push({dot_path: path, value: value,
                          provenance: t.provenance || null});
        });
        if (!updates.length) return;

        // Read-only context rows: shown in the popup but never written (e.g. the
        // clicked readout power in dBm alongside the editable amplitude).
        var contextRows = [];
        (clickable.context || []).forEach(function(c) {
            var av = ((c.axis || clickable.axis) === 'y') ? pt.y : pt.x;
            if (av === undefined || av === null) return;
            var scale = (c.scale === undefined || c.scale === null) ? 1 : c.scale;
            var offset = (c.offset === undefined || c.offset === null) ? 0 : c.offset;
            var val = av * scale + offset;
            var disp = (c.decimals === undefined || c.decimals === null)
                       ? String(val) : val.toFixed(c.decimals);
            contextRows.push({label: c.label || '', value: disp, unit: c.unit || ''});
        });

        var root = document.getElementById('ds-detail-root');
        var expName = root ? root.getAttribute('data-experiment') : '';
        var toastVal = (clickable.axis === 'y') ? pt.y : pt.x;
        _showPlotClickToast((clickable.axis === 'y' ? 'y=' : 'x=') + toastVal, q, updates[0].dot_path);
        // Carry the run's own chip identity so the server 409s a cross-chip
        // write (same gate as the Results-tab apply path) — without it a run's
        // CZ amp could silently land on a different chip reusing pair names.
        _openPlotApplyPopup(updates, expName, q, contextRows, _runChipExpect(root));
    });
}
window._attachInteractivePlotClickHandler = _attachInteractivePlotClickHandler;

/* Non-blocking value-domain heads-up: amplitudes beyond OPX full scale (±1)
   or flux/DC offsets beyond ±0.5 V get an amber inline note on the row. This
   NEVER blocks Apply — the app's philosophy is trust-researcher-input (real
   chips legitimately exceed textbook ranges); it's a typo net, not a gate. */
function _plotApplyDomainWarning(path, valStr) {
    var v = parseFloat(valStr);
    if (!isFinite(v)) return '';
    var p = String(path || '').toLowerCase();
    if (p.indexOf('amplitude') !== -1 && Math.abs(v) > 1) {
        return 'exceeds OPX full scale ±1';
    }
    var isOffset = /\.offset$/.test(p) || p.indexOf('decouple_offset') !== -1 ||
                   p.indexOf('independent_offset') !== -1 ||
                   p.indexOf('joint_offset') !== -1;
    if (isOffset && Math.abs(v) > 0.5) {
        return 'exceeds flux DC range ±0.5 V';
    }
    return '';
}

function _updatePlotRowDomainWarning(row) {
    var box = row.querySelector('.plot-apply-row-domainwarn');
    var input = row.querySelector('.plot-apply-new-input');
    if (!box || !input) return;
    var msg = _plotApplyDomainWarning(row.getAttribute('data-dot-path'), input.value);
    if (msg) { box.textContent = '⚠ ' + msg; box.hidden = false; }
    else { box.textContent = ''; box.hidden = true; }
}

function _renderPlotApplyPopup(updates, expName, qubitName, contextRows, chipExpect) {
    var rowsBox = document.getElementById('plot-apply-rows');
    var ctxBox = document.getElementById('plot-apply-context');
    var popup = document.getElementById('plot-apply-popup');
    if (!rowsBox || !popup) return;
    // Stash the run's chip token so the Apply / Apply-All requests carry it
    // (server 409s a cross-chip write unless force-overridden).
    if (chipExpect && chipExpect.token) {
        popup.dataset.expectChip = chipExpect.token;
        if (chipExpect.forced) popup.dataset.forceChip = '1';
        else delete popup.dataset.forceChip;
    } else {
        delete popup.dataset.expectChip;
        delete popup.dataset.forceChip;
    }

    if (ctxBox) {
        var bits = [];
        if (expName)   bits.push('<small>Experiment: <code>' + _ppEscape(expName) + '</code></small>');
        if (qubitName) bits.push('<small>Qubit: <code>'      + _ppEscape(qubitName) + '</code></small>');
        // The edit targets the LOADED chip (not the dataset's own snapshot) —
        // show the active context's path, refreshed by the popup's pre-check;
        // the load-path box is only a fallback when nothing is loaded.
        var _loadInput = document.getElementById('load-path-input');
        var _chip = window.__activePath
            || (_loadInput ? _loadInput.value.trim() : '');
        if (_chip) bits.push('<small>Target chip: <code>' + _ppEscape(_chip) + '</code></small>');
        ctxBox.innerHTML = bits.join(' &middot; ');
    }

    // Async, non-blocking fit-audit verdict for this run + qubit (advisory badge).
    _fetchApplyVerdict(qubitName);

    // Read-only context rows (e.g. the clicked readout power in dBm). Shown
    // above the editable rows; never part of the Apply / Apply-All payload.
    var extraBox = document.getElementById('plot-apply-extra');
    if (extraBox) {
        if (contextRows && contextRows.length) {
            extraBox.innerHTML = contextRows.map(function(c) {
                return '<div class="plot-apply-ctx-row">'
                     + '<span class="plot-apply-ctx-label">' + _ppEscape(c.label) + '</span>'
                     + '<span class="plot-apply-ctx-val">' + _ppEscape(c.value)
                     + (c.unit ? ' ' + _ppEscape(c.unit) : '') + '</span></div>';
            }).join('');
            extraBox.hidden = false;
        } else {
            extraBox.innerHTML = '';
            extraBox.hidden = true;
        }
    }

    rowsBox.innerHTML = '';
    updates.forEach(function(u) {
        var row = document.createElement('div');
        row.className = 'plot-apply-row';
        row.setAttribute('data-input-path', u.dot_path);
        row.setAttribute('data-dot-path', u.dot_path);
        // Provenance line: HOW the staged value was computed (the node's own
        // formula) + the frozen inputs it was baked against — the trust line
        // for contract-faithful clicks (e.g. "clicked − RF_at_run + frozen
        // f_01" for 05b's += semantics).
        var provHtml = '';
        if (u.provenance && u.provenance.formula) {
            var inputs = (u.provenance.inputs || []).map(function(inp) {
                return _ppEscape(inp.label) + ' = ' + _ppEscape(String(inp.frozen_value));
            }).join(' · ');
            provHtml = '<div class="plot-apply-row-prov muted">'
                     + _ppEscape(u.provenance.formula)
                     + (inputs ? '<br>' + inputs : '') + '</div>';
        }
        row.innerHTML =
            '<div class="plot-apply-row-path"><code>' + _ppEscape(u.dot_path) + '</code>'
          + '<div class="plot-apply-row-ptr" hidden></div></div>'
          + '<div class="plot-apply-row-old"><span class="muted">previous</span> '
          + '<span class="plot-apply-old-val muted">…</span></div>'
          + '<div class="plot-apply-row-new"><span class="muted">new</span> '
          + '<input type="text" class="plot-apply-new-input" value="' + _ppEscape(String(u.value)) + '">'
          + '<span class="plot-apply-delta val-delta" hidden></span></div>'
          + '<div class="plot-apply-row-action">'
          + '<button type="button" class="primary btn-sm plot-apply-row-btn">Apply</button></div>'
          + provHtml
          + '<div class="plot-apply-row-domainwarn" hidden'
          + ' style="color:var(--color-warning-text);font-size:0.74rem"></div>'
          + '<div class="plot-apply-row-error" hidden></div>';

        var btn = row.querySelector('.plot-apply-row-btn');
        var input = row.querySelector('.plot-apply-new-input');
        btn.addEventListener('click', function() { applyPlotRow(row); });
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                applyPlotRow(row);
            }
        });
        // Value-domain heads-up (non-blocking) — re-evaluated on every edit.
        input.addEventListener('input', function() {
            _updatePlotRowDomainWarning(row);
            _updatePlotRowDelta(row);       // docs/76: how far this click moves the value
        });
        _updatePlotRowDomainWarning(row);
        rowsBox.appendChild(row);
    });

    // Apply All only makes sense for 2+ rows.
    var applyAllBtn = document.getElementById('plot-apply-all');
    if (applyAllBtn) applyAllBtn.style.display = (updates.length > 1) ? '' : 'none';

    popup.style.display = 'flex';
    popup._releaseTrap = window.trapFocus(popup, window.closePlotApplyPopup);
    var first = rowsBox.querySelector('.plot-apply-new-input');
    if (first) { try { first.focus(); first.select(); } catch (e) {} }
}

function _setOldVal(slot, v, err) {
    if (!slot) return;
    if (v === null || v === undefined) {
        slot.textContent = err ? '(not set)' : '(null)';
        slot.classList.add('muted');
        slot._rawValue = null;
    } else {
        slot.textContent = String(v);
        slot.classList.remove('muted');
        slot._rawValue = v;         // keep the RAW value for the Δ (docs/76)
    }
    var row = slot.closest ? slot.closest('.plot-apply-row') : null;
    if (row) _updatePlotRowDelta(row);
}

/* Δ between the field's current value (fetched via /field/peek, or the
   selected pointer write-target) and the value this click would stage.
   Blank whenever a difference is meaningless — a not-yet-loaded or
   non-numeric previous value never fabricates one. */
function _updatePlotRowDelta(row) {
    if (!row || !window.ValueDelta) return;
    var chip = row.querySelector('.plot-apply-delta');
    var slot = row.querySelector('.plot-apply-old-val');
    var input = row.querySelector('.plot-apply-new-input');
    if (!chip || !slot || !input) return;
    var oldRaw = (slot._rawValue === undefined) ? null : slot._rawValue;
    window.ValueDelta.paint(chip, oldRaw, input.value);
}

// Render the pointer chain + write-target selector + shared warning for a
// pointer-backed row, and set the row's effective data-dot-path (the write
// target) to the selected candidate (default = final pointed-to literal).
function _renderPointerRow(row, info, rawValue) {
    var ptrBox = row.querySelector('.plot-apply-row-ptr');
    var oldSlot = row.querySelector('.plot-apply-old-val');
    var cands = info.candidates || [];
    var rawPtr = (info.chain && info.chain.length) ? info.chain[0].pointer : rawValue;

    var html = '';
    if (rawPtr) {
        html += '<span class="ptr-inline pointer-badge" title="Resolves to: '
              + _ppEscape(info.resolved_path) + '">' + _ppEscape(String(rawPtr)) + '</span>';
    }
    if (cands.length > 1) {
        html += ' <label class="plot-apply-chain">write target: <select class="plot-apply-target">';
        cands.forEach(function(c, idx) {
            html += '<option value="' + _ppEscape(c.path) + '"'
                  + (idx === cands.length - 1 ? ' selected' : '') + '>'
                  + _ppEscape(c.label) + '</option>';
        });
        html += '</select></label>';
    } else if (cands.length === 1) {
        html += ' <span class="plot-apply-chain">&rarr; <code>' + _ppEscape(cands[0].label) + '</code></span>';
    }
    if (!info.resolvable) {
        html += ' <span class="muted">(runtime alias — not separately stored)</span>';
    }
    if (info.shared_by && info.shared_by.length) {
        html += '<span class="plot-apply-shared">⚠ also used by: '
              + info.shared_by.map(_ppEscape).join(', ') + '</span>';
    }
    if (ptrBox) { ptrBox.innerHTML = html; ptrBox.hidden = false; }

    function selectCandidate(c) {
        row.setAttribute('data-dot-path', c.path);
        _setOldVal(oldSlot, c.value, null);
    }
    var def = cands.length ? cands[cands.length - 1]
                           : {path: info.resolved_path, value: info.resolved_value};
    selectCandidate(def);

    var sel = ptrBox ? ptrBox.querySelector('.plot-apply-target') : null;
    if (sel) {
        sel.addEventListener('change', function() {
            var c = cands.filter(function(x) { return x.path === sel.value; })[0];
            if (c) selectCandidate(c);
        });
    }
}

function _fetchPlotApplyOldValues(updates) {
    var rowsBox = document.getElementById('plot-apply-rows');
    if (!rowsBox) return;
    var rows = Array.prototype.slice.call(rowsBox.querySelectorAll('.plot-apply-row'));
    var inputs = rows.map(function(r) { return r.getAttribute('data-input-path'); });
    var qs = inputs.map(function(p) { return 'dot_path=' + encodeURIComponent(p); }).join('&');
    fetch('/field/peek?' + qs).then(function(resp) {
        return resp.json();
    }).then(function(payload) {
        if (!payload) return;
        var resolved = payload.resolved || {};
        var values = payload.values || {};
        var errors = payload.errors || {};
        rows.forEach(function(row) {
            var input = row.getAttribute('data-input-path');
            var info = resolved[input];
            var oldSlot = row.querySelector('.plot-apply-old-val');
            if (info && info.is_pointer) {
                _renderPointerRow(row, info, values[input]);
            } else {
                var v = (info && info.resolved_value !== undefined && info.resolved_value !== null)
                        ? info.resolved_value : values[input];
                _setOldVal(oldSlot, v, errors[input]);
            }
        });
    }).catch(function() { /* leave placeholders */ });
}

function applyPlotRow(row) {
    if (!row || row.classList.contains('plot-apply-applied')) return;
    var input = row.querySelector('.plot-apply-new-input');
    var btn = row.querySelector('.plot-apply-row-btn');
    var errEl = row.querySelector('.plot-apply-row-error');
    var dotPath = row.getAttribute('data-dot-path');
    if (!input || !btn || !dotPath) return;

    if (errEl) { errEl.hidden = true; errEl.textContent = ''; }
    btn.disabled = true;
    var prevLabel = btn.textContent;
    btn.textContent = '…';

    var _pp = document.getElementById('plot-apply-popup');
    // Acks ACCUMULATE across the 409 chain (fsp then type_fix): a resend that
    // dropped an earlier ack would re-trigger that gate — popup ping-pong.
    var _fspAck = null, _plan = null, _typeFix = null;

    function _restore() { btn.disabled = false; btn.textContent = prevLabel; }

    function _send() {
        if (_fspAck === 'comp') {
            // comp = this row + the compensated amps in ONE /field/edit-batch
            // (one gid = one Review bundle = one Ctrl+Z).
            var b = {
                updates: [{dot_path: dotPath, value: input.value}]
                    .concat(window._fspCompUpdates(_plan)),
                fsp_ack: 'comp'
            };
            if (_typeFix) b.type_fix = _typeFix;
            if (_pp && _pp.dataset.expectChip) {
                b.expect_chip = _pp.dataset.expectChip;
                if (_pp.dataset.forceChip) b.force_chip = true;
            }
            return fetch('/field/edit-batch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(b)
            }).then(function(resp) {
                return resp.json().then(function(j) { return {status: resp.status, body: j}; });
            });
        }
        var body = 'dot_path=' + encodeURIComponent(dotPath)
                 + '&value=' + encodeURIComponent(input.value);
        if (_fspAck) body += '&fsp_ack=' + _fspAck;
        if (_typeFix) body += '&type_fix=' + _typeFix;
        if (_pp && _pp.dataset.expectChip) {
            body += '&expect_chip=' + encodeURIComponent(_pp.dataset.expectChip)
                  + (_pp.dataset.forceChip ? '&force_chip=1' : '');
        }
        return fetch('/field/edit', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded', 'HX-Request': 'true'},
            body: body
        }).then(function(resp) {
            return resp.json().then(function(j) { return {status: resp.status, body: j}; });
        });
    }

    function _fail(e) {
        _restore();
        if (errEl) { errEl.hidden = false; errEl.textContent = String(e); }
    }

    function handleR(r) {
        if (r.body && r.body.ok) {
            // Both response shapes land here — single {stored, stored_kind}
            // and the comp batch {results}: only ok/tray_html are consumed.
            _markPlotRowApplied(row);
            if (r.body.tray_html) _swapPendingTray(r.body.tray_html);
            if (_fspAck === 'comp' && window.showToast) {
                var nAmp = ((_plan && _plan.amps) || []).length;
                window.showToast('Also updated ' + nAmp + ' compensated amplitude'
                    + (nAmp === 1 ? '' : 's') + ' — one undo reverts both.', 'success');
            }
            _closePlotPopupIfDone();
        } else if (r.status === 409 && r.body && r.body.chip_mismatch) {
            _restore();
            if (window.confirm((r.body.error || 'Different chip.') + '\n\nApply anyway?')
                && _pp) { _pp.dataset.forceChip = '1'; applyPlotRow(row); }
        } else if (r.status === 409 && r.body && r.body.type_fix && window._confirmTypeFix) {
            _typeFix = window._confirmTypeFix(r.body.type_fix) ? 'convert' : 'keep';
            _send().then(handleR).catch(_fail);
        } else if (r.status === 409 && r.body && r.body.fsp_compensation && window._openFspPopup) {
            // r12-B never silent: nothing committed yet — the offer first.
            window._openFspPopup(r.body.fsp_compensation, function(mode, plan) {
                if (mode === 'cancel') { _restore(); return; }  // user choice, not a failure
                _fspAck = mode; _plan = plan;
                _send().then(handleR).catch(_fail);
            });
        } else {
            _restore();
            if (errEl) {
                errEl.hidden = false;
                errEl.textContent = (r.body && r.body.error) || 'Apply failed';
            }
        }
    }

    _send().then(handleR).catch(_fail);
}

function applyAllPlotRows() {
    var rowsBox = document.getElementById('plot-apply-rows');
    if (!rowsBox) return;
    var rows = Array.prototype.slice.call(rowsBox.querySelectorAll('.plot-apply-row'));
    var pending = rows.filter(function(r) { return !r.classList.contains('plot-apply-applied'); });
    if (!pending.length) { closePlotApplyPopup(); return; }

    var updates = pending.map(function(r) {
        var input = r.querySelector('.plot-apply-new-input');
        return {dot_path: r.getAttribute('data-dot-path'), value: input ? input.value : ''};
    });

    var applyAllBtn = document.getElementById('plot-apply-all');
    var prevLabel = applyAllBtn ? applyAllBtn.textContent : 'Apply All';
    if (applyAllBtn) { applyAllBtn.disabled = true; applyAllBtn.textContent = '…'; }

    pending.forEach(function(r) {
        var e = r.querySelector('.plot-apply-row-error');
        if (e) { e.hidden = true; e.textContent = ''; }
    });

    var _pp = document.getElementById('plot-apply-popup');
    // Acks ACCUMULATE across the 409 chain (fsp then type_fix) — see applyPlotRow.
    var _ups = updates, _fspAck = null, _plan = null, _typeFix = null;

    // Re-enable only at terminal exits (ok / chip-confirm / cancel / error /
    // catch), so Apply All can't be re-clicked while the FSP popup is open
    // or a resend is in flight.
    function _restore() {
        if (applyAllBtn) { applyAllBtn.disabled = false; applyAllBtn.textContent = prevLabel; }
    }

    function _post() {
        var b = {updates: _ups};
        if (_fspAck) b.fsp_ack = _fspAck;
        if (_typeFix) b.type_fix = _typeFix;
        if (_pp && _pp.dataset.expectChip) {
            b.expect_chip = _pp.dataset.expectChip;
            if (_pp.dataset.forceChip) b.force_chip = true;
        }
        return fetch('/field/edit-batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(b)
        }).then(function(resp) {
            return resp.json().then(function(j) { return {status: resp.status, body: j}; });
        });
    }

    function _fail(e) {
        _restore();
        var first = pending[0];
        if (first) {
            var er = first.querySelector('.plot-apply-row-error');
            if (er) { er.hidden = false; er.textContent = String(e); }
        }
    }

    function handleR(r) {
        if (r.body && r.body.ok) {
            _restore();
            pending.forEach(function(row) { _markPlotRowApplied(row); });
            if (r.body.tray_html) _swapPendingTray(r.body.tray_html);
            if (_fspAck === 'comp' && window.showToast) {
                var nAmp = ((_plan && _plan.amps) || []).length;
                window.showToast('Also updated ' + nAmp + ' compensated amplitude'
                    + (nAmp === 1 ? '' : 's') + ' — one undo reverts both.', 'success');
            }
            _closePlotPopupIfDone();
        } else if (r.status === 409 && r.body && r.body.chip_mismatch) {
            _restore();
            if (window.confirm((r.body.error || 'Different chip.') + '\n\nApply anyway?')
                && _pp) { _pp.dataset.forceChip = '1'; applyAllPlotRows(); }
        } else if (r.status === 409 && r.body && r.body.type_fix && window._confirmTypeFix) {
            _typeFix = window._confirmTypeFix(r.body.type_fix) ? 'convert' : 'keep';
            _post().then(handleR).catch(_fail);
        } else if (r.status === 409 && r.body && r.body.fsp_compensation && window._openFspPopup) {
            // r12-B never silent: the batch is untouched — the offer first.
            window._openFspPopup(r.body.fsp_compensation, function(mode, plan) {
                if (mode === 'cancel') { _restore(); return; }  // rows stay pending, no error
                _fspAck = mode; _plan = plan;
                if (mode === 'comp') _ups = updates.concat(window._fspCompUpdates(plan));
                _post().then(handleR).catch(_fail);
            });
        } else {
            _restore();
            var byPath = {};
            (r.body.results || []).forEach(function(res) { byPath[res.dot_path] = res; });
            var shown = false;
            pending.forEach(function(row) {
                var info = byPath[row.getAttribute('data-dot-path')];
                if (info && !info.applied && info.error) {
                    var e = row.querySelector('.plot-apply-row-error');
                    if (e) { e.hidden = false; e.textContent = info.error; shown = true; }
                }
            });
            // Batch-level failure with no per-row results (e.g. /field/edit-batch
            // 400 "No active context"/"No updates supplied", or a 500) was
            // silently swallowed — the button just re-enabled. Surface it.
            if (!shown) {
                var msg = (r.body && r.body.error) || ('Apply failed (' + r.status + ')');
                var first0 = pending[0];
                var fe = first0 && first0.querySelector('.plot-apply-row-error');
                if (fe) { fe.hidden = false; fe.textContent = msg; }
                else if (window.showToast) window.showToast(msg, 'error');
            }
        }
    }

    _post().then(handleR).catch(_fail);
}

function _markPlotRowApplied(row) {
    if (!row) return;
    row.classList.add('plot-apply-applied');
    var btnSlot = row.querySelector('.plot-apply-row-action');
    if (btnSlot) btnSlot.innerHTML = '<span class="plot-apply-row-check">✓ applied</span>';
    var input = row.querySelector('.plot-apply-new-input');
    if (input) input.readOnly = true;
}

/* ── FSP → amplitude compensation popup (docs/20 r12-B) ──
   NEVER silent: any /field/edit(-batch) that would change a port's
   full_scale_power_dbm without an ack gets a 409 carrying the compensation
   plan; this popup lists EVERY amplitude old→new ("SM will update these
   amplitudes WITH the port change") plus DAC-clip warnings, and only an
   explicit choice commits:
     [Apply FSP + compensate N] → resend('comp')  (one batch: FSP + amps
                                  = one Review bundle = one Ctrl+Z)
     [Apply FSP only]           → resend('solo')
     [Cancel]                   → nothing written. */
window._openFspPopup = (function () {
    var overlay = null;
    function _el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }
    function _fmt(v) {
        if (typeof v === "number" && window._groupDigits) return window._groupDigits(v);
        return String(v);
    }
    /* An amplitude for an INPUT: never grouped (thousands separators would not
       parse back) and never the raw product of a float multiply. */
    function _ampStr(v) {
        if (typeof v !== "number" || !isFinite(v)) return String(v);
        var s = v.toPrecision(6);
        if (s.indexOf("e") < 0 && s.indexOf(".") >= 0) {
            s = s.replace(/0+$/, "").replace(/\.$/, "");
        }
        return s;
    }
    function ensure() {
        if (overlay) return overlay;
        overlay = document.createElement("div");
        overlay.className = "ch-overlay";
        overlay.style.display = "none";
        var backdrop = _el("div", "ch-backdrop");
        backdrop.addEventListener("click", close);
        var card = _el("div", "ch-card fsp-card");
        card.setAttribute("role", "dialog");
        card.setAttribute("aria-modal", "true");
        overlay.appendChild(backdrop);
        overlay.appendChild(card);
        document.body.appendChild(overlay);
        return overlay;
    }
    function close() {
        if (!overlay) return;
        if (overlay._releaseTrap) {
            try { overlay._releaseTrap(); } catch (e) {}
            overlay._releaseTrap = null;
        }
        overlay.style.display = "none";
    }
    function open(plan, resend) {
        var o = ensure();
        var card = o.querySelector(".fsp-card");
        card.textContent = "";
        // Every exit notifies the caller exactly once — a dangling promise in
        // a row-apply chain would freeze the grid's Apply-all otherwise.
        var done = false;
        function finish(mode) {
            if (done) return;
            done = true;
            close();
            resend(mode, plan);
        }
        o.querySelector(".ch-backdrop").onclick = function () { finish("cancel"); };
        var head = _el("div", "ch-head");
        head.appendChild(_el("span", "ch-title",
            "⚡ Full-scale power change — port " + (plan.port || "?")));
        var x = _el("button", "ch-close", "×");
        x.type = "button"; x.title = "Close (Esc)";
        x.addEventListener("click", function () { finish("cancel"); });
        head.appendChild(x);
        card.appendChild(head);
        card.appendChild(_el("p", "fsp-line",
            "full_scale_power_dbm: " + _fmt(plan.fsp_old) + " → "
            + _fmt(plan.fsp_new) + " dBm  (amplitude factor ×"
            + Number(plan.factor).toPrecision(6) + ")"));
        card.appendChild(_el("p", "fsp-note",
            "To keep every pulse's real output power constant, SM will update "
            + "these amplitudes WITH the port change. Nothing is written until "
            + "you choose below."));
        // docs/120 item 7 — the compensated amplitudes are EDITABLE, so this
        // warning can no longer be a fact baked in at 409 time: it has to track
        // what will ACTUALLY be written. Always build it, hide it at zero, and
        // recompute on every keystroke (_recount below).
        var lowering = Number(plan.fsp_new) < Number(plan.fsp_old);
        var clipWarn = _el("p", "fsp-warn");
        clipWarn.style.display = "none";
        card.appendChild(clipWarn);
        if (plan.range_warn) card.appendChild(_el("p", "fsp-warn", "⚠ " + plan.range_warn));
        if (plan.more_fsp_in_batch) {
            card.appendChild(_el("p", "fsp-note",
                "This batch edits more than one FSP — they are confirmed one at a time."));
        }
        var wrap = _el("div", "ch-scroll");
        var table = _el("table", "ch-table");
        var thead = document.createElement("thead");
        var hr = document.createElement("tr");
        ["pulse", "amplitude now", "", "compensated", "Δ", "", ""].forEach(function (t) {
            hr.appendChild(_el("th", null, t));
        });
        thead.appendChild(hr);
        table.appendChild(thead);
        var tbody = document.createElement("tbody");
        var rows = [];   // {a, input, dTd, mark, reset}

        /* The value this row will actually write. `a.new` stays the COMPUTED
           value forever so "reset" has something to return to; the user's
           override rides alongside as `a.userNew` (read back by
           _fspCompUpdates). Blank means "use the computed one" — deleting the
           contents is not a request to write nothing. */
        function _rowValue(r) {
            var raw = String(r.input.value).trim();
            if (raw === "") return Number(r.a.new);
            var v = Number(raw);
            return isFinite(v) ? v : NaN;
        }
        function _recount() {
            var clips = 0, bad = 0, edited = 0;
            rows.forEach(function (r) {
                var v = _rowValue(r);
                var raw = String(r.input.value).trim();
                if (!isFinite(v)) {
                    bad++;
                    r.input.classList.add("fsp-amp-bad");
                    r.mark.textContent = "not a number";
                    r.dTd.textContent = ""; r.dTd.hidden = true;
                } else {
                    r.input.classList.remove("fsp-amp-bad");
                    if (Math.abs(v) > 1.0) { clips++; r.mark.textContent = "⚠ >1.0"; }
                    else { r.mark.textContent = ""; }
                    if (window.ValueDelta) window.ValueDelta.paint(r.dTd, r.a.old, v);
                }
                r.input.classList.toggle("fsp-amp-clip", isFinite(v) && Math.abs(v) > 1.0);
                // "edited" means differs from the computed value, not merely
                // non-empty — retyping the same number is not an override.
                var isEdit = raw !== "" && Number(raw) !== Number(r.a.new);
                if (isEdit) edited++;
                r.reset.style.visibility = isEdit ? "visible" : "hidden";
                r.a.userNew = isEdit && isFinite(v) ? v : undefined;
            });
            if (clips) {
                clipWarn.textContent = "⚠ " + clips + " amplitude"
                    + (clips === 1 ? "" : "s") + " above 1.0 — the DAC clips. "
                    + (lowering
                       ? "Do not lower FSP this far (or reduce those pulses' powers first)."
                       : "Those pulses already sit past DAC full scale — fix them first.");
                clipWarn.style.display = "";
            } else {
                clipWarn.style.display = "none";
            }
            // Never let a typo be written: an unparseable cell blocks the apply
            // rather than silently falling back to the computed value.
            bComp.disabled = !rows.length || bad > 0;
            bComp.title = bad
                ? bad + " amplitude" + (bad === 1 ? " is" : "s are") + " not a number"
                : (edited ? edited + " amplitude" + (edited === 1 ? "" : "s")
                            + " edited from the computed value" : "");
            editNote.style.display = edited ? "" : "none";
        }

        (plan.amps || []).forEach(function (a) {
            var tr = document.createElement("tr");
            tr.appendChild(_el("td", "fsp-pulse", (a.channel || "") + " · " + (a.op || "")));
            tr.appendChild(_el("td", null, _fmt(a.old)));
            tr.appendChild(_el("td", "fsp-arrow", "→"));
            // docs/120 item 7: an input, not text. The customer had only
            // accept-all or discard-all; they want to nudge an amplitude and
            // then commit. RAW value (never _fmt) — thousands separators would
            // not parse back.
            var inTd = _el("td");
            var inp = document.createElement("input");
            inp.type = "text";
            inp.className = "fsp-amp-input" + (a.clips ? " fsp-amp-clip" : "");
            // Readable, not raw. `a.new` is amp*factor, so it arrives as
            // 0.15848931924611134 — which overflowed the field and read as
            // noise beside a nicely formatted "amplitude now". Amplitudes are
            // O(0.001..1), so 6 significant figures is far finer than anything
            // a DAC resolves while still fitting. `a.new` keeps the exact value
            // for the reset and for the un-edited resend.
            inp.value = _ampStr(a.new);
            inp.setAttribute("inputmode", "decimal");
            inp.setAttribute("aria-label", "compensated amplitude for "
                + (a.channel || "") + " " + (a.op || ""));
            inp.addEventListener("input", _recount);
            inTd.appendChild(inp);
            tr.appendChild(inTd);
            // docs/76: the compensation factor is uniform, but the amplitude
            // MOVE per pulse is not — show it per row.
            var dTd = _el("td", "fsp-delta");
            tr.appendChild(dTd);
            var mark = _el("td", "fsp-clipmark");
            tr.appendChild(mark);
            var reset = _el("button", "fsp-amp-reset", "↺");
            reset.type = "button";
            reset.title = "Back to the computed value (" + _ampStr(a.new) + ")";
            reset.style.visibility = "hidden";
            reset.addEventListener("click", function () {
                inp.value = _ampStr(a.new);
                _recount();
                inp.focus();
            });
            var rTd = _el("td");
            rTd.appendChild(reset);
            tr.appendChild(rTd);
            rows.push({ a: a, input: inp, dTd: dTd, mark: mark, reset: reset });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        wrap.appendChild(table);
        card.appendChild(wrap);
        if ((plan.skipped || []).length) {
            var sk = _el("p", "fsp-skipped",
                "Not compensated: " + plan.skipped.map(function (s) {
                    return s.path + " (" + s.reason + ")";
                }).join("; "));
            card.appendChild(sk);
        }
        // Shown only once something is actually overridden — the identity is
        // what the compensation is FOR, so departing from it should be said out
        // loud rather than left for the user to notice later in the tray.
        var editNote = _el("p", "fsp-note fsp-edited-note",
            "Edited amplitudes no longer satisfy P = FSP + 20·log10|amp| — those "
            + "pulses' output power will move. ↺ restores the computed value.");
        editNote.style.display = "none";
        card.appendChild(editNote);
        var foot = _el("div", "fsp-actions");
        var n = (plan.amps || []).length;
        var bComp = _el("button", "btn-sync primary", "Apply FSP + compensate "
            + n + " amplitude" + (n === 1 ? "" : "s"));
        bComp.type = "button";
        bComp.addEventListener("click", function () { finish("comp"); });
        var bSolo = _el("button", "btn-sync", "Apply FSP only");
        bSolo.type = "button";
        bSolo.title = "Change the port power WITHOUT touching amplitudes — every pulse's real output power shifts by the FSP delta";
        bSolo.addEventListener("click", function () { finish("solo"); });
        var bCancel = _el("button", "btn-sync outline", "Cancel");
        bCancel.type = "button";
        bCancel.addEventListener("click", function () { finish("cancel"); });
        if (n === 0) bComp.disabled = true;
        foot.appendChild(bComp);
        foot.appendChild(bSolo);
        foot.appendChild(bCancel);
        card.appendChild(foot);
        // First pass paints every Δ / clip mark from the seeded inputs, so the
        // clip warning above is derived by the SAME code that will keep it in
        // sync as the user types — never a baked count that drifts.
        _recount();
        o.style.display = "flex";
        if (window.trapFocus) {
            o._releaseTrap = window.trapFocus(card, function () { finish("cancel"); });
        }
    }
    return open;
})();

/* Build the compensated-amp updates a 'comp' resend appends to the batch.
 *
 * THE one place every caller funnels through (Explorer, both grids, All-values,
 * and the plot-apply popup's per-row + Apply-All paths), which is why docs/120
 * item 7 -- "users want to adjust the amps a little and then update" -- lands
 * here rather than in five resend sites.
 *
 * `a.new` is always the value SM computed from P = FSP + 20*log10|amp|;
 * `a.userNew` is set by the popup only when the user typed something different,
 * so a plan that was never edited (or came from an un-wired caller that never
 * opened the popup) serialises byte-identically to before. */
window._fspCompUpdates = function (plan) {
    return (plan && plan.amps ? plan.amps : []).map(function (a) {
        var v = (a.userNew === undefined || a.userNew === null
                 || !isFinite(a.userNew)) ? a.new : a.userNew;
        return { dot_path: a.path, value: String(v) };
    });
};

/* r14 ⑩: shared stored-as-TEXT conversion confirm (the /field/edit[-batch]
 * type_fix 409). true = convert the field type and store the number;
 * false = keep text. Every edit surface funnels through this so the wording
 * can't drift. */
window._confirmTypeFix = function (tf) {
    var extra = tf && tf.more_in_batch
        ? "\n(+" + tf.more_in_batch + " more field(s) in this batch)" : "";
    return window.confirm(
        "Stored as TEXT: " + (tf.path || "") + " = " + (tf.current_display || "") + extra +
        "\n\nOK — convert the field type to " + tf.proposed +
        " and store the number (persisted: future edits stay " + tf.proposed + ")" +
        "\nCancel — keep it text");
};

/* docs/65: once every row is applied, the popup's job is done — close it and
   say so. It used to stay open showing "✓ applied", so "Apply All" appeared to
   need a SECOND press (the second click hit the nothing-left-to-apply early
   return, which was the only success path that closed). */
function _closePlotPopupIfDone() {
    var rowsBox = document.getElementById('plot-apply-rows');
    if (!rowsBox) return;
    var total = rowsBox.querySelectorAll('.plot-apply-row').length;
    var left = rowsBox.querySelectorAll('.plot-apply-row:not(.plot-apply-applied)').length;
    if (total && !left) {
        window.closePlotApplyPopup();
        if (window.showToast) {
            window.showToast(
                'Applied ' + total + ' value' + (total === 1 ? '' : 's') +
                ' to the working state — Review (top bar) to push to the live chip.',
                'success');
        }
    }
}

// Single, debounced announcer for "the active chip's state changed → the
// diagnostics linter must re-run". The badge (#diag-tray-slot) and the auto
// error-banner (#diagnostics-banner-slot) both listen for `diagnostics-changed
// from:body` and re-fetch /diagnostics/summary + /diagnostics/banner, which
// re-lint the current store (cache keyed on store.mutation_seq, so a fresh
// result every mutation). Diagnostics is a SAFETY net — an edit can push a
// waveform sample out of the DAC range, move a carrier out of band, etc. — so
// this must fire after EVERY state change, NOT only when a pending tray happens
// to be swapped. Every mutation path (grid Apply, All-values, pair grid,
// plot-click, sync pull/apply, the one-click diagnostics fix, the review-overlay
// accept) calls this directly; the 350 ms trailing debounce coalesces a burst (and the
// belt-and-suspenders double call from _swapPendingTray) into one re-lint.
// TRAILING debounce: re-lint once ~350 ms after the user STOPS editing, not on
// every keystroke/apply. The full lint (waveform DAC synthesis) is ~130 ms on a
// 21-qubit chip, so firing it per edit made rapid editing crawl; the badge/banner
// don't need to be instant (they reflect the latest state when they do run).
var _diagChangedTimer = null;
window._diagChanged = function () {
    if (!window.htmx) return;
    if (_diagChangedTimer) clearTimeout(_diagChangedTimer);   // reset → fire after the LAST call
    _diagChangedTimer = setTimeout(function () {
        _diagChangedTimer = null;
        try { htmx.trigger(document.body, 'diagnostics-changed'); } catch (e) {}
    }, 350);
};

function _swapPendingTray(html) {
    // r16 ⑤: the tray (and the diagnostics-banner refetch that follows) can
    // change page height — freeze the interactive-tile prune through the
    // layout settle so an apply never re-renders every figure.
    window._interactiveFreezeUntil = Date.now() + 1500;
    var slot = document.getElementById('pending-tray');
    if (slot) {
        slot.outerHTML = html;
        var newTray = document.getElementById('pending-tray');
        if (newTray && window.htmx) htmx.process(newTray);
    }
    // This hand-rolled outerHTML replace doesn't fire htmx:afterSwap, so restore the
    // drawer state + clear stale sidebar pending markers here too (audit P1) — all 7
    // JS edit callers funnel through this one place.
    if (window._restoreTrayState) window._restoreTrayState();
    // audit-r10: same reason — re-evaluate the LiveEditUndo ↶ visibility
    // (the fresh tray arrives with the button display:none).
    if (window.LiveEditUndo) window.LiveEditUndo._updateTrayBtn();
    var saveBtn = document.querySelector('.btn-save');
    if (saveBtn) saveBtn.disabled = false;
    // Cross-surface consistency: every edit path funnels through here, so this is
    // the one place to announce "the working copy changed" — open surfaces (Bulk
    // Edit behind a modal, the Explorer tree) listen and soft-refresh.
    document.dispatchEvent(new CustomEvent('quam:state-changed'));
    // Refresh the diagnostics tray badge + auto error-banner. Routed through the
    // debounced announcer so it coalesces with the explicit per-path calls (a
    // value edit can add or clear a crash-class finding — e.g. a readout amplitude
    // pushed past the DAC range).
    window._diagChanged();
}

// After an on-the-fly accept the review overlay's change log is no longer empty,
// so swap its action bar from the lone "Pull & discard" button (which a later
// Sync would use to throw the just-accepted edit away) to the edit-preserving
// apply/reapply trio, and surface the keep-your-edits note. The server renders
// the trio hidden when the working copy opens clean; this is what reveals it.
// Idempotent — safe to call on every accept.
function _reviewRevealEditSync() {
    var actions = document.getElementById('state-review-actions');
    if (actions) {
        var clean = actions.querySelector('.review-sync-clean');
        var saved = actions.querySelector('.review-sync-saved');
        var edits = actions.querySelector('.review-sync-edits');
        if (clean) clean.hidden = true;
        if (saved) saved.hidden = true;   // accepting a live value adds a change-log edit → the trio takes over
        if (edits) edits.hidden = false;
    }
    var note = document.querySelector('.state-review .review-accept-note');
    if (note) note.hidden = false;
}

/* The review screen's "new" side is an EDITABLE live value, so its Δ has to
   follow what the user typed — a server-rendered delta would keep describing
   the value they just replaced (docs/76). Delegated so it survives every
   re-render of the overlay; the old value rides the input as data-old-raw
   (JSON, so a stored-as-text number stays text and the Δ still says so). */
document.addEventListener("input", function (evt) {
    var input = evt.target;
    if (!input || !input.classList || !input.classList.contains("review-live-input")) return;
    var row = input.closest(".review-row, tr");
    var chip = row && row.querySelector(".review-delta");
    if (!chip || !window.ValueDelta) return;
    var oldRaw;
    try { oldRaw = JSON.parse(input.getAttribute("data-old-raw")); }
    catch (e) { return; }
    window.ValueDelta.paint(chip, oldRaw, input.value);
});

// Review modal: write a (possibly edited) live value into the working copy on the
// fly, without a full pull. Goes through the same /field/edit-batch path; the tray
// swap then fires quam:state-changed so the surface behind reflects it.
window.reviewAccept = function (btn) {
    var wrap = btn.closest('.review-live-edit');
    var input = wrap && wrap.querySelector('.review-live-input');
    if (!input) return;
    var dotPath = input.getAttribute('data-dot-path');
    // A12: this row's working-copy value is the user's OWN edit (e.g. accepted
    // earlier this session) — accepting the live value would REVERT it. Confirm
    // before overwriting, so a reflexive second ✓ on a re-opened review can't
    // silently discard the edit.
    if (input.getAttribute('data-yours') === '1') {
        if (!window.confirm('The working state already holds your own edited value for ' +
                dotPath + '.\n\nAccepting replaces it with the live value. Continue?')) {
            return;
        }
    }
    // "Added" rows: the key doesn't exist in the working copy yet, so accepting
    // must CREATE it (server only honours create when this flag is set). The
    // review list is now stacked .review-row cards (no <tr>), so match either.
    var acceptRow = btn.closest('.review-row, tr');
    var isAdded = !!(acceptRow && acceptRow.classList.contains('diff-row-added'));
    btn.disabled = true; btn.textContent = '…';
    fetch('/field/edit-batch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: [{ dot_path: dotPath, value: input.value, create: isAdded }] })
    }).then(function (r) { return r.json(); }).then(function (d) {
        var row = btn.closest('.review-row, tr');
        if (d && d.ok) {
            btn.textContent = '✓ accepted';
            input.readOnly = true;
            if (row) row.classList.add('review-accepted');
            _reviewRevealEditSync();
            if (d.tray_html) _swapPendingTray(d.tray_html);
        } else {
            btn.disabled = false; btn.textContent = '✓';
            var err = (d && d.results && d.results[0] && d.results[0].error) || (d && d.error) || 'edit failed';
            if (window.showToast) window.showToast('Could not accept ' + dotPath + ': ' + err, 'error');
        }
    }).catch(function () { btn.disabled = false; btn.textContent = '✓'; });
};

window.applyPlotRow = applyPlotRow;
window.applyAllPlotRows = applyAllPlotRows;
window.closePlotApplyPopup = function() {
    var popup = document.getElementById('plot-apply-popup');
    if (popup) {
        popup.style.display = 'none';
        if (popup._releaseTrap) { popup._releaseTrap(); popup._releaseTrap = null; }
    }
    var rowsBox = document.getElementById('plot-apply-rows');
    if (rowsBox) rowsBox.innerHTML = '';
    var ctxBox = document.getElementById('plot-apply-context');
    if (ctxBox) ctxBox.innerHTML = '';
    var extraBox = document.getElementById('plot-apply-extra');
    if (extraBox) { extraBox.innerHTML = ''; extraBox.hidden = true; }
};

function _showPlotClickToast(coordText, qubitName, dotPath) {
    var bar = document.getElementById('status-bar');
    if (!bar) return;
    var isUpdate = coordText.indexOf('Updated:') === 0;
    var isWarning = !isUpdate && !qubitName && !dotPath;
    var msg = (isWarning || isUpdate) ? coordText : 'Copied: ' + coordText;
    if (qubitName) msg += '  \u2502  ' + qubitName;
    if (dotPath) msg += '  \u2192  ' + dotPath;
    var div = document.createElement('div');
    var cls = isUpdate ? 'toast-success' : (isWarning ? 'toast-warning' : 'toast-info');
    div.className = 'toast ' + cls;
    var p = document.createElement('p');
    p.textContent = msg;
    div.appendChild(p);
    bar.appendChild(div);
    var duration = isWarning ? 5000 : 3500;
    setTimeout(function() { div.style.opacity = '0'; }, duration);
    setTimeout(function() { div.remove(); }, duration + 500);
}

/**
 * Navigate to the Explorer tab and expand the JSON tree to a dot-path.
 * e.g. "qubits.q4.resonator.time_of_flight"
 */
function _navigateToExplorerPath(dotPath) {
    function openExplorer() {
        htmx.ajax('GET', '/explorer', {target: '#table-pane', swap: 'innerHTML'}).then(function() {
            var attempts = 0;
            var maxAttempts = 15;
            function tryExpand() {
                attempts++;
                var container = document.getElementById('explorer-tree-state');
                if (!container) {
                    _showPlotClickToast('Failed to load state \u2014 check the folder path', null, null);
                    return;
                }
                if (container.children.length === 0 && attempts < maxAttempts) {
                    setTimeout(tryExpand, 100);
                    return;
                }
                _expandTreeToPath('explorer-tree-state', dotPath);
            }
            tryExpand();
        });
    }
    // The ACTIVE chip is authoritative \u2014 the old flow re-POSTed /load on the
    // load-path text box, silently flipping a sidebar-switched context back to
    // the stale box path. Only when NOTHING is loaded fall back to activating
    // the box path.
    fetch('/chip/active-token').then(function (r) { return r.json(); })
        .then(function (act) {
            if (act && act.loaded) { openExplorer(); return; }
            var loadInput = document.getElementById('load-path-input');
            var statePath = loadInput ? loadInput.value.trim() : '';
            if (!statePath) {
                _showPlotClickToast('Enter a quam_state folder path to edit fields', null, null);
                return;
            }
            return fetch('/load', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded',
                           'HX-Request': 'true'},
                body: 'folder=' + encodeURIComponent(statePath),
                redirect: 'manual'
            }).then(openExplorer);
        })
        .catch(openExplorer);   // probe failure \u2192 fail open (Explorer shows its own state)
}
// Explicit window binding: the guarded callers (value-history Data links,
// UndoNav) reference window._navigateToExplorerPath \u2014 a classic <script>
// hoists top-level declarations onto window, but eval'd/bundled contexts
// (the jsdom selfchecks) don't. Pin it so the guard never silently no-ops.
window._navigateToExplorerPath = _navigateToExplorerPath;

/**
 * Expand a JSON tree to reveal a specific dot-path (e.g. "qubits.q4.resonator.time_of_flight").
 * Walks the path segments, materializing lazy nodes and expanding parents along the way.
 */
function _expandTreeToPath(containerId, dotPath) {
    var container = document.getElementById(containerId);
    if (!container) return;

    var segments = dotPath.split('.');
    var currentPath = '';

    for (var i = 0; i < segments.length; i++) {
        currentPath = i === 0 ? segments[i] : currentPath + '.' + segments[i];
        var node = container.querySelector('.tree-node[data-path="' + currentPath + '"]');
        if (!node) break;

        if (node._lazyData) {
            // Lazy node: click toggle to materialize + expand in one step
            var toggle = node.querySelector(':scope > .tree-row > .tree-toggle');
            if (toggle && toggle.classList.contains('collapsed')) {
                toggle.click();
            }
        } else {
            // Already materialized — just ensure expanded
            var children = node.querySelector(':scope > .tree-children');
            var toggle2 = node.querySelector(':scope > .tree-row > .tree-toggle');
            if (children && toggle2 && toggle2.classList.contains('collapsed')) {
                toggle2.click();
            }
        }
    }

    // Highlight the target node
    var target = container.querySelector('.tree-node[data-path="' + dotPath + '"]');
    if (!target) {
        // Try parent path (the field might be inside a deeper structure)
        var parentPath = segments.slice(0, -1).join('.');
        target = container.querySelector('.tree-node[data-path="' + parentPath + '"]');
    }
    if (target) {
        // Remove any stale highlight/popup
        var oldHighlight = container.querySelector('.tree-highlight');
        if (oldHighlight) oldHighlight.classList.remove('tree-highlight');
        var oldPopup = document.querySelector('.tree-edit-popup');
        if (oldPopup) oldPopup.remove();

        target.classList.add('tree-highlight');

        // Add a popup badge next to the target node
        var row = target.querySelector(':scope > .tree-row');
        var popup = null;
        if (row) {
            var fieldName = segments[segments.length - 1];
            popup = document.createElement('span');
            popup.className = 'tree-edit-popup';
            var arrow = document.createTextNode('\u2190 Update ');
            var bold = document.createElement('b');
            bold.textContent = fieldName;
            popup.appendChild(arrow);
            popup.appendChild(bold);
            row.style.position = 'relative';
            row.appendChild(popup);
        }

        // Delay scroll to let DOM settle after expanding nodes, then start dismiss timers after scroll
        setTimeout(function() {
            target.scrollIntoView({behavior: 'smooth', block: 'center'});
            // Start dismiss timers after scroll finishes (~600ms for smooth scroll)
            setTimeout(function() {
                if (popup && popup.parentNode) {
                    setTimeout(function() { popup.remove(); }, 8000);
                }
                setTimeout(function() { target.classList.remove('tree-highlight'); }, 8000);
            }, 700);
        }, 150);
    }
}

/**
 * Re-render the full stack of selected plots into the plot container.
 * Preserves any qubit-selector rows already present (they live above the entries).
 */
function _renderAllSelections(panel, runId) {
    var container = panel.querySelector('[id$="h5-plot-container"]');
    if (!container) return;

    // Remove only .h5-plot-entry children (leave qubit-selectors in place)
    container.querySelectorAll('.h5-plot-entry').forEach(function(e) { e.remove(); });

    var plot = window._dsLastPlot;
    if (!plot || !plot.selections || !plot.selections.length) return;

    var coords = (window._h5CoordsById && window._h5CoordsById[runId]) || {};

    plot.selections.forEach(function(sel, idx) {
        var qubitLabel = '';
        if (sel.qubitIdx !== null && sel.qubitIdx !== undefined) {
            var q = _findQubitDim(sel.dims, coords);
            if (q && q.labels) qubitLabel = q.labels[sel.qubitIdx] || ('q' + sel.qubitIdx);
        }
        var title = sel.varName + (qubitLabel ? ' \u2014 ' + qubitLabel : '');
        var entry = document.createElement('div');
        entry.className = 'h5-plot-entry';
        var inner = document.createElement('div');
        inner.className = 'h5-plot-inner';
        // Header with title + × remove button
        var header = document.createElement('div');
        header.className = 'h5-plot-entry-header';
        var titleSpan = document.createElement('span');
        titleSpan.className = 'h5-plot-entry-title';
        titleSpan.textContent = title;
        var removeBtn = document.createElement('button');
        removeBtn.className = 'h5-plot-entry-remove';
        removeBtn.textContent = '\xd7';
        removeBtn.setAttribute('data-idx', idx);
        removeBtn.setAttribute('data-runid', runId);
        removeBtn.onclick = function() {
            var i = parseInt(this.getAttribute('data-idx'));
            var rid = parseInt(this.getAttribute('data-runid'));
            window._removeSelection(i, rid);
        };
        header.appendChild(titleSpan);
        header.appendChild(removeBtn);
        entry.appendChild(header);
        entry.appendChild(inner);
        container.appendChild(entry);
        _fetchAndRenderPlot(inner, runId, plot.which, sel.varName, sel.qubitIdx);
    });
}

/** Remove one selection by index and re-render. */
window._removeSelection = function(idx, runId) {
    if (!window._dsLastPlot || !window._dsLastPlot.selections) return;
    window._dsLastPlot.selections.splice(idx, 1);
    _updateVarRowStates();
    // Rebuild qubit-selector active states
    var panel = document.getElementById('inspector-pane');
    _refreshQubitSelectorStates(panel);
    _renderAllSelections(panel, runId);
};

/** Update active-button state on all qubit selectors after a selection change. */
function _refreshQubitSelectorStates(panel) {
    if (!panel) return;
    panel.querySelectorAll('.h5-qubit-selector').forEach(function(sel) {
        var varName = sel.getAttribute('data-var');
        sel.querySelectorAll('.h5-qubit-btn').forEach(function(btn, idx) {
            btn.classList.toggle('active', _hasSelection(varName, idx));
        });
    });
}

/**
 * Show (or refresh) a multi-select qubit row inside the plot container.
 * Clicking a qubit toggles that (varName, qubitIdx) combo.
 */
function _showQubitMultiSelector(panel, runId, which, varName, dims, qubitLabels) {
    var container = panel.querySelector('[id$="h5-plot-container"]');
    if (!container) return;

    // Remove any existing selector for a *different* variable so it doesn't stack up
    container.querySelectorAll('.h5-qubit-selector').forEach(function(s) {
        if (s.getAttribute('data-var') !== varName) s.remove();
    });

    var existing = container.querySelector('.h5-qubit-selector[data-var="' + varName + '"]');
    if (existing) {
        // Just refresh active states — user clicked same-var Plot again
        existing.querySelectorAll('.h5-qubit-btn').forEach(function(btn, idx) {
            btn.classList.toggle('active', _hasSelection(varName, idx));
        });
        return;
    }

    var selectorDiv = document.createElement('div');
    selectorDiv.className = 'h5-qubit-selector';
    selectorDiv.setAttribute('data-var', varName);
    var label = document.createElement('span');
    label.className = 'h5-qubit-label';
    label.innerHTML = 'Qubits for <code>' + varName + '</code>:';
    selectorDiv.appendChild(label);

    qubitLabels.forEach(function(lbl, idx) {
        var btn = document.createElement('button');
        btn.className = 'btn-sm outline h5-qubit-btn';
        btn.textContent = lbl;
        if (_hasSelection(varName, idx)) btn.classList.add('active');
        btn.onclick = function() {
            _toggleSelection(which, varName, dims, idx);
            btn.classList.toggle('active', _hasSelection(varName, idx));
            _renderAllSelections(panel, runId);
        };
        selectorDiv.appendChild(btn);
    });

    container.insertBefore(selectorDiv, container.firstChild);
}

/**
 * Called when the user clicks "Plot" on a variable row.
 * Toggles the variable into the multi-selection and shows/hides qubit selector.
 */
window.plotOrSelectQubit = function(triggerEl, runId, which, varName, dims) {
    dims = dims || [];
    if (!window._dsLastPlot) {
        window._dsLastPlot = { which: which, experimentType: _currentExperimentType(), selections: [] };
    }
    // Switching ds_raw ↔ ds_fit clears all selections and plot UI
    if (window._dsLastPlot.which !== which) {
        window._dsLastPlot.which = which;
        window._dsLastPlot.selections = [];
        var panel0 = _h5Panel(triggerEl);
        panel0.querySelectorAll('.h5-qubit-selector, .h5-plot-entry, .h5-caution-banner').forEach(function(s) { s.remove(); });
        _updateVarRowStates();
    }
    window._dsLastPlot.experimentType = _currentExperimentType();

    var panel = _h5Panel(triggerEl);
    var coords = (window._h5CoordsById && window._h5CoordsById[runId]) || {};
    var qubitInfo = _findQubitDim(dims, coords);

    if (dims.length >= 3 && qubitInfo && qubitInfo.labels && qubitInfo.labels.length > 0) {
        _showQubitMultiSelector(panel, runId, which, varName, dims, qubitInfo.labels);
    } else {
        // 1D/2D: toggle directly (no qubit dim)
        _toggleSelection(which, varName, dims, null);
        _renderAllSelections(panel, runId);
    }
};

/** Shim kept for any stale onclick attributes in the DOM. */
window.plotDatasetVar = function() {};

/* ------------------------------------------------------------------ */
/* Notion-style Tag Picker                                              */
/* ------------------------------------------------------------------ */

/**
 * Rebuild tag badges in a table cell from a tag list.
 */
// Reserved tag backing the ⭐ favorite (toggled by the row star, not shown as a
// badge). Keep in sync with FAVORITE_TAG in core/dataset.py + dataset-virtual.js.
var FAVORITE_TAG = 'favorite';

function _rebuildTagCell(td, runId, tags) {
    var html = '';
    (tags || []).forEach(function(t) {
        if (t === FAVORITE_TAG) return;  // represented by the ⭐ star, not a badge
        // data-tag (not an inline onclick with the tag inlined) — removal is
        // handled by delegation (onTbodyClick for the table; the detail-tags
        // delegate for the panel), which avoids nested-quoting bugs.
        html += '<span class="tag-badge" data-tag="' + _ppEscape(t) + '" title="Click to remove">' + _ppEscape(t) + '</span>';
    });
    // runId is the composite uid string ("<hex>:<int>") — it MUST be quoted in the
    // inline handler, else `openTagPicker(a1b2c3d4:250, this)` is a JS SyntaxError
    // (the colon) and the rebuilt + button dead-clicks after the first tag edit.
    html += '<button class="tag-add-btn" onclick="openTagPicker(\'' + runId + '\', this)" title="Add tag">+</button>';
    td.innerHTML = html;
}

// Remove a tag from the dataset detail panel. Those badges live outside the
// virtual table's tbody (which has its own delegate), so they get this one.
document.addEventListener('click', function(e) {
    if (!e.target || !e.target.closest) return;
    var badge = e.target.closest('.ds-detail-tags .tag-badge');
    if (!badge) return;
    var box = badge.closest('.ds-detail-tags');
    var rid = box ? box.getAttribute('data-run-id') : null;   // uid string
    var tag = badge.getAttribute('data-tag');
    if (rid && tag && typeof window.removeDatasetTag === 'function') {
        window.removeDatasetTag(rid, tag, badge);
    }
});

/**
 * Open a Notion-style inline tag picker dropdown.
 * Fetches all existing tags, shows checkmarks for tags on this run,
 * allows instant toggle and new tag creation.
 */
window.openTagPicker = function(runId, btnEl) {
    // Close any existing picker
    closeTagPicker();

    var td = btnEl.closest('.col-tags');
    if (!td) return;

    // Get current tags from badge elements
    var currentTags = [];
    td.querySelectorAll('.tag-badge').forEach(function(el) {
        currentTags.push(el.textContent.trim());
    });

    // Fetch all tags then build picker
    fetch('/datasets/tags')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var allTags = data.tags || [];
            _showTagPicker(runId, btnEl, td, allTags, currentTags);
        });
};

function _showTagPicker(runId, btnEl, td, allTags, currentTags) {
    // The reserved favorite tag is toggled by the ⭐ star, not the picker.
    allTags = (allTags || []).filter(function(t) { return t !== FAVORITE_TAG; });

    var picker = document.createElement('div');
    picker.className = 'tag-picker';
    picker.id = 'active-tag-picker';

    // Tag list
    var listHtml = '<div class="tag-picker-list">';
    if (allTags.length === 0) {
        listHtml += '<div class="tag-picker-empty">No tags yet</div>';
    }
    allTags.forEach(function(tag) {
        var isOn = currentTags.indexOf(tag) !== -1;
        // Tags are arbitrary user strings (e.g. "T1<10us") stored verbatim server-side
        // and shared across a LAN-served instance, so escape both the attribute and the
        // visible label (matching _rebuildTagCell); getAttribute('data-tag') decodes the
        // entities back on toggle, so escaped values round-trip.
        var esc = _ppEscape(tag);
        listHtml += '<div class="tag-picker-item" data-tag="' + esc + '">' +
            '<span class="tag-picker-check">' + (isOn ? '&#10003;' : '') + '</span>' +
            '<span>' + esc + '</span>' +
        '</div>';
    });
    listHtml += '</div>';

    // New tag input
    listHtml += '<div class="tag-picker-new">' +
        '<input type="text" placeholder="New tag..." id="tag-picker-new-input">' +
    '</div>';

    picker.innerHTML = listHtml;

    // Position relative to button
    td.style.position = 'relative';
    td.appendChild(picker);

    // Focus the new tag input
    var newInput = picker.querySelector('#tag-picker-new-input');
    if (newInput) {
        setTimeout(function() { newInput.focus(); }, 50);
    }

    // Handle tag item clicks (toggle)
    picker.querySelectorAll('.tag-picker-item').forEach(function(item) {
        item.addEventListener('click', function() {
            var tag = item.getAttribute('data-tag');
            var isOn = item.querySelector('.tag-picker-check').textContent.trim() !== '';
            var method = isOn ? 'DELETE' : 'POST';

            fetch('/dataset/' + runId + '/tag', {
                method: method,
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({tag: tag})
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (window.DatasetVirtual && typeof window.DatasetVirtual.patchTags === 'function') {
                    window.DatasetVirtual.patchTags(runId, data.tags);
                }
                // Update check mark
                var check = item.querySelector('.tag-picker-check');
                if (isOn) {
                    check.innerHTML = '';
                } else {
                    check.innerHTML = '&#10003;';
                }
                // Rebuild badges (but keep picker open)
                _rebuildTagCell(td, runId, data.tags);
                // Re-append picker since innerHTML was replaced
                td.appendChild(picker);
            });
        });
    });

    // Handle new tag creation
    if (newInput) {
        newInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                var tag = newInput.value.trim();
                if (!tag) return;

                fetch('/dataset/' + runId + '/tag', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({tag: tag})
                })
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (window.DatasetVirtual && typeof window.DatasetVirtual.patchTags === 'function') {
                        window.DatasetVirtual.patchTags(runId, data.tags);
                    }
                    // Close and reopen to refresh tag list
                    closeTagPicker();
                    _rebuildTagCell(td, runId, data.tags);
                    // Reopen picker with updated tags
                    var newBtn = td.querySelector('.tag-add-btn');
                    if (newBtn) openTagPicker(runId, newBtn);
                });
            }
            if (e.key === 'Escape') {
                closeTagPicker();
            }
        });
    }

    // Close on click outside
    setTimeout(function() {
        document.addEventListener('click', _tagPickerOutsideClick);
    }, 0);
    // Close on Escape
    document.addEventListener('keydown', _tagPickerEscapeHandler);
}

function _tagPickerOutsideClick(e) {
    var picker = document.getElementById('active-tag-picker');
    if (picker && !picker.contains(e.target) && !e.target.classList.contains('tag-add-btn')) {
        closeTagPicker();
    }
}

function _tagPickerEscapeHandler(e) {
    if (e.key === 'Escape') closeTagPicker();
}

window.closeTagPicker = function() {
    var picker = document.getElementById('active-tag-picker');
    if (picker) picker.remove();
    document.removeEventListener('click', _tagPickerOutsideClick);
    document.removeEventListener('keydown', _tagPickerEscapeHandler);
};

/**
 * Remove a tag from a dataset run (called from tag badge click).
 * Instant toggle — no confirmation dialog.
 */
window.removeDatasetTag = function(runId, tag, spanEl) {
    fetch('/dataset/' + runId + '/tag', {
        method: 'DELETE',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({tag: tag})
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (window.DatasetVirtual && typeof window.DatasetVirtual.patchTags === 'function') {
            window.DatasetVirtual.patchTags(runId, data.tags);
        }
        var td = spanEl.closest('.col-tags');
        if (td) _rebuildTagCell(td, runId, data.tags);
    });
};

/**
 * Legacy alias — old templates may still call promptAddTag.
 */
window.promptAddTag = function(runId, btnEl) {
    openTagPicker(runId, btnEl);
};

/**
 * Save a note on a dataset run. Shows brief ✓ confirmation.
 */
window.saveDatasetNote = function(runId, note, el) {
    fetch('/dataset/' + runId + '/note', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({note: note})
    })
    .then(function() {
        // Brief ✓ feedback on the edited textarea (el is passed from onblur so it
        // targets the right one in split/pinned view; falls back to the first).
        var ta = el || document.querySelector('.ds-note-textarea');
        if (ta) {
            ta.style.borderColor = '#27ae60';
            setTimeout(function() { ta.style.borderColor = ''; }, 1200);
            // Keep the badge's filled-state + hover title in sync with the value.
            var block = ta.closest('.ds-note-block');
            var btn = block && block.querySelector('.ds-note-toggle');
            if (btn) {
                var filled = (note || '').trim().length > 0;
                btn.classList.toggle('has-note', filled);
                if (filled) btn.setAttribute('title', note);
                else btn.removeAttribute('title');
            }
        }
    });
};

/**
 * Resize the note <textarea> to fit its content: one line by default, taller
 * only when the user wraps a long line or presses Enter. height='auto' first so
 * a shrinking note collapses back down. Bails if hidden (scrollHeight would be 0).
 */
window.autoGrowNote = function(ta) {
    if (!ta || ta.hidden) return;
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
};

/**
 * Toggle the collapsible note editor from the "Note" badge. Opening un-hides the
 * textarea, sizes it (now that it has layout), focuses, and drops the caret at
 * the end. Always-collapsed-by-default means sizing only ever happens here, so
 * there's no hidden->scrollHeight=0 or pinned-mode load-sizing problem.
 */
window.toggleNoteEditor = function(btn) {
    var block = btn.closest('.ds-note-block');
    if (!block) return;
    var ta = block.querySelector('.ds-note-textarea');
    if (!ta) return;
    var willOpen = ta.hidden;
    ta.hidden = !willOpen;
    btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    if (willOpen) {
        autoGrowNote(ta);
        ta.focus();
        var len = ta.value.length;
        try { ta.setSelectionRange(len, len); } catch (e) {}
    }
};

/* ------------------------------------------------------------------ */
/* Multi-Select Compare: checkbox management + compare trigger          */
/* ------------------------------------------------------------------ */

/**
 * Update the compare bar when checkboxes change.
 * Shows/hides the bar and updates the count label.
 */
function _selectedRunIds() {
    if (window.DatasetVirtual && typeof window.DatasetVirtual.getSelectedIds === 'function') {
        return window.DatasetVirtual.getSelectedIds();
    }
    var ids = [];
    document.querySelectorAll('.ds-check:checked').forEach(function(cb) {
        ids.push(parseInt(cb.value, 10));
    });
    return ids;
}

window.updateCompareButton = function() {
    var bar = document.getElementById('ds-compare-bar');
    if (!bar) return;
    var count = _selectedRunIds().length;
    // Drive the floating compare panel via a single data-state attribute (the
    // CSS hides the wrong message variants, and hides the whole panel at
    // "empty"). So the panel stays out of the way until the first checkbox tick
    // moves the state off "empty" (item 3).
    var newState;
    if (count === 0) newState = 'empty';
    else if (count === 1) newState = 'one';
    else if (count <= 5) newState = 'ready';
    else newState = 'over';
    bar.setAttribute('data-state', newState);
    var btn = document.getElementById('ds-compare-btn');
    if (btn) btn.disabled = (newState !== 'ready');
    // Diff is a TWO-run question (docs/84); the N-run compare covers 3+.
    var dbtn = document.getElementById('ds-diff-btn');
    if (dbtn) dbtn.disabled = (count !== 2);
    var counter = document.getElementById('ds-compare-count');
    if (counter) counter.textContent = String(count);
};

/**
 * Clear all dataset checkboxes and hide the compare bar.
 */
window.clearDatasetCheckboxes = function() {
    if (window.DatasetVirtual && typeof window.DatasetVirtual.clearSelection === 'function') {
        window.DatasetVirtual.clearSelection();
    }
    var master = document.getElementById('ds-select-all');
    if (master) master.checked = false;
    updateCompareButton();
};

/**
 * Collect selected run IDs and load the comparison view into the inspector.
 */
window.compareSelectedDatasets = function() {
    var ids = _selectedRunIds();
    // docs/104 #13: the toolbar disables Compare above 5, so this alert
    // promised a limit (8) no click could reach — one number everywhere.
    if (ids.length < 2 || ids.length > 5) {
        alert('Select 2-5 runs to compare.');
        return;
    }
    // docs/84: the diff workbench is the front door for exactly two sources —
    // the "Compare" and "Diff" buttons sat side by side at count===2, and a
    // user reaching for "Compare" (the more prominent/first button) landed on
    // the old Figures/Fit-Results/Parameters run comparison instead of the
    // diff view they actually wanted. Two selected now always means diff,
    // whichever button is pressed; the N-run comparison is for 3+ only.
    if (ids.length === 2) { window.diffSelectedDatasets(); return; }
    htmx.ajax('GET', '/datasets/compare?ids=' + ids.join(','),
              {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
};

/* docs/84: exactly two runs is the case an IDE-style diff answers best —
   what was ASKED differently (node.json), what the chip looked like, what
   data came out. The N-run comparison above stays for 3+. */
window.diffSelectedDatasets = function() {
    var uids = _selectedRunIds();   // the virtual table's selection IS uids
    if (uids.length !== 2) {
        if (window.showToast) window.showToast('Pick exactly two runs to diff.', 'info');
        return;
    }
    var url = '/diff/runs?uids=' + uids.map(encodeURIComponent).join(',');
    if (window.htmx) { htmx.ajax('GET', url, {target: 'body', swap: 'none'}); }
    else { window.location.href = url; }
};

/**
 * Tab switching for the dataset compare view.
 * Scoped to #ds-compare-root so it doesn't conflict with normal dataset tabs.
 */
window.switchCompareDatasetTab = function(tabName, linkEl) {
    var root = document.getElementById('ds-compare-root');
    if (!root) return;

    // Update tab links
    root.closest('div').querySelectorAll('.dataset-tabs a').forEach(function(a) {
        a.classList.remove('active');
    });
    if (linkEl) linkEl.classList.add('active');

    // Show/hide tab content
    root.querySelectorAll('.dataset-tab-content').forEach(function(div) {
        div.classList.add('hidden');
    });
    var target = document.getElementById('ds-cmp-tab-' + tabName);
    if (target) target.classList.remove('hidden');
};

/* ------------------------------------------------------------------ */
/* Trend Dashboard: load trend data via HTMX                           */
/* ------------------------------------------------------------------ */

/**
 * Load trend data based on selected experiment and qubit filters.
 * Called by onchange handlers on the trend dropdowns.
 */
window.loadTrendData = function() {
    var exp = document.getElementById('trend-exp-select');
    var qubit = document.getElementById('trend-qubit-select');
    if (!exp || !exp.value) return;
    var url = '/trends/data?experiment=' + encodeURIComponent(exp.value);
    if (qubit && qubit.value) url += '&qubit=' + encodeURIComponent(qubit.value);
    // Multi-folder: pass the selected folder_keys. The server merges only when
    // they're the same chip; otherwise it returns a warning fragment.
    var grid = document.getElementById('trend-folder-grid');
    if (grid) {
        var keys = [];
        grid.querySelectorAll('.folder-chip.active').forEach(function(c) {
            keys.push(c.getAttribute('data-folder-key'));
        });
        if (keys.length) url += '&folders=' + encodeURIComponent(keys.join(','));
    }
    htmx.ajax('GET', url, {target: '#trends-content', swap: 'innerHTML'});
};

// Trends folder chips: multi-select among same-chip folders (default single).
// At least one folder stays selected; the most-recently-activated key is
// remembered so the cross-chip warning can fall back to "just that one".
window._lastTrendFolder = null;
window.toggleTrendFolder = function(key, el) {
    var grid = document.getElementById('trend-folder-grid');
    if (!grid || !el) return;
    el.classList.toggle('active');
    if (!grid.querySelector('.folder-chip.active')) el.classList.add('active');  // keep >= 1
    if (el.classList.contains('active')) window._lastTrendFolder = key;
    loadTrendData();
};
window.trendUseSingleFolder = function() {
    var grid = document.getElementById('trend-folder-grid');
    if (!grid) return;
    var keep = window._lastTrendFolder;
    var first = null;
    grid.querySelectorAll('.folder-chip').forEach(function(c) {
        if (!first) first = c;
        c.classList.toggle('active', c.getAttribute('data-folder-key') === keep);
    });
    if (!grid.querySelector('.folder-chip.active') && first) first.classList.add('active');
    loadTrendData();
};

/* ------------------------------------------------------------------ */
/* Pin & Browse: pin one run, browse others side-by-side               */
/* ------------------------------------------------------------------ */

window._pinnedRunId = null;
window._pinnedHtml = null;

/**
 * innerHTML never runs <script> tags nor wires htmx attributes. The Pin & Browse
 * flows build their panes via innerHTML, so without this the browsed (right)
 * column's htmx-driven bits (the Raw Data tab's hx-trigger container, lazy
 * loaders) never activate — the tab spins "Loading data files…" forever — and
 * inline init scripts never run. Mirror swapPane(): re-create each <script> so
 * it executes, then htmx.process the subtree.
 */
function _activatePinnedPane(pane) {
    if (!pane) return;
    var scripts = pane.querySelectorAll('script');
    for (var i = 0; i < scripts.length; i++) {
        var old = scripts[i], s = document.createElement('script');
        if (old.src) s.src = old.src; else s.textContent = old.textContent;
        if (old.parentNode) old.parentNode.replaceChild(s, old);
    }
    if (window.htmx && htmx.process) htmx.process(pane);
}

/**
 * Unpin: clear pin state and collapse the split to just the current (right) run.
 * Shared by the pin button's second press AND the pinned (left) column's X close.
 */
window.unpinDataset = function() {
    window._pinnedRunId = null;
    window._pinnedHtml = null;
    var btn = document.getElementById('inspector-pin-btn');
    if (btn) btn.classList.remove('pinned');
    var pane = document.getElementById('inspector-pane');
    var currentCol = pane ? pane.querySelector('.inspector-current-col') : null;
    if (currentCol) {
        // Purge live Plotly plots BEFORE innerHTML destroys them — otherwise
        // dangling <defs>/clip-paths corrupt the next plot (clipped/invisible axes)
        // and ~2-5MB of WebGL/DOM leaks per unpin. These are plain calls, so the
        // htmx:beforeSwap purge handler never runs.
        if (window.PlotHost) {
            try { window.PlotHost.purgeWithin(pane); } catch (e) {}
            try { window.PlotHost.unobserveWithin(pane); } catch (e) {}
        }
        pane.innerHTML = currentCol.innerHTML;
        _reviveInteractiveMarkup(pane);     // docs/118: the plots above are gone
        _activatePinnedPane(pane);
    }
};

/**
 * Close the current (right) comparison but KEEP the pinned run, shown alone. Wired
 * onto the current column's X so that close never nukes the whole split. Un-prefixes
 * the cloned 'pinned-' ids so the surviving detail behaves like a normal single one.
 */
function _closeCurrentKeepPinned() {
    var pane = document.getElementById('inspector-pane');
    if (!pane) return;
    var pinnedCol = pane.querySelector('.inspector-pinned-col');
    if (!pinnedCol) { window.closeInspector(); return; }
    var tmp = document.createElement('div');
    tmp.innerHTML = pinnedCol.innerHTML;
    var label = tmp.querySelector('.pinned-label');
    if (label) label.remove();
    tmp.querySelectorAll('[id^="pinned-"]').forEach(function(el) { el.id = el.id.slice(7); });
    window._pinnedRunId = null;
    window._pinnedHtml = null;
    // Purge live plots before innerHTML nukes them (see unpinDataset).
    if (window.PlotHost) {
        try { window.PlotHost.purgeWithin(pane); } catch (e) {}
        try { window.PlotHost.unobserveWithin(pane); } catch (e) {}
    }
    pane.innerHTML = tmp.innerHTML;
    _reviveInteractiveMarkup(pane);         // docs/118
    _activatePinnedPane(pane);
}

/**
 * Toggle pin/unpin of the current dataset run in the inspector.
 * When pinned, subsequent dataset loads will show a two-column layout.
 */
window.togglePinDataset = function() {
    if (window._pinnedRunId) { window.unpinDataset(); return; }

    // Pin: capture the CURRENTLY-shown detail. In split mode read the current (right)
    // column; otherwise the whole pane — never the global #ds-detail-root, which in
    // split mode resolves to the wrong column.
    var pane = document.getElementById('inspector-pane');
    if (!pane) return;
    var source = pane.querySelector('.inspector-current-col') || pane;
    var root = source.querySelector('#ds-detail-root');
    if (!root) return;
    window._pinnedRunId = root.dataset.runId;

    // Clone HTML and prefix IDs to avoid duplicates with the live (right) column.
    var clone = source.cloneNode(true);
    clone.querySelectorAll('[id]').forEach(function(el) {
        if (el.id.indexOf('pinned-') !== 0) el.id = 'pinned-' + el.id;
    });
    window._pinnedHtml = clone.innerHTML;

    var btn = document.getElementById('inspector-pin-btn');
    if (btn) btn.classList.add('pinned');
};

/**
 * Build the two-column split layout with pinned (left) and current (right).
 */
function _wrapPinnedLayout(pinnedHtml, currentHtml) {
    // Persisted split (% width of the pinned/left column), clamped [20,80].
    var pct = 50;
    try { var v = parseFloat(localStorage.getItem('quam_cmp_split')); if (v >= 20 && v <= 80) pct = v; } catch (e) {}
    return '<div class="inspector-split">' +
        '<div class="inspector-pinned-col" style="flex:0 0 ' + pct + '%">' +
            '<div class="pinned-label">&#128204; Pinned: #' + window._pinnedRunId + '</div>' +
            pinnedHtml +
        '</div>' +
        '<div class="inspector-split-gutter" title="Drag to resize"></div>' +
        '<div class="inspector-current-col">' +
            currentHtml +
        '</div>' +
    '</div>';
}

/**
 * Compare view: drag the gutter to resize the two columns (Part D). Mirrors the
 * sidebar #sidebar-resizer — sets the pinned (left) column's flex-basis %, persisted
 * to quam_cmp_split. Not a Split.js instance (independent of the table/inspector gutter).
 */
function _initCompareSplitResizer(pane) {
    var split = pane.querySelector('.inspector-split');
    var gutter = split && split.querySelector('.inspector-split-gutter');
    var left = split && split.querySelector('.inspector-pinned-col');
    if (!gutter || !left) return;
    var dragging = false;
    function onMove(e) {
        if (!dragging) return;
        var rect = split.getBoundingClientRect();
        if (rect.width <= 0) return;
        var pct = ((e.clientX - rect.left) / rect.width) * 100;
        pct = Math.max(20, Math.min(80, pct));
        left.style.flex = '0 0 ' + pct + '%';
    }
    function onUp() {
        if (!dragging) return;
        dragging = false;
        document.body.classList.remove('cmp-resizing');
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        try {
            var rect = split.getBoundingClientRect();
            var pct = (left.getBoundingClientRect().width / rect.width) * 100;
            if (pct >= 20 && pct <= 80) localStorage.setItem('quam_cmp_split', String(Math.round(pct)));
        } catch (e) {}
    }
    gutter.addEventListener('pointerdown', function(e) {
        dragging = true;
        document.body.classList.add('cmp-resizing');
        document.addEventListener('pointermove', onMove);
        document.addEventListener('pointerup', onUp);
        e.preventDefault();
    });
}

/**
 * HTMX beforeSwap interceptor: when a run is pinned, intercept the new
 * dataset detail swap and render two-column layout instead.
 */
// Registered EARLY (see the top-of-file registration, docs/124 M-2): this
// must set shouldSwap before the purge/unobserve/_io-teardown listeners look.
function _pinnedRunSwapInterceptor(evt) {
    if (!window._pinnedRunId) return;
    if (!evt.detail || !evt.detail.target) return;
    if (evt.detail.target.id !== 'inspector-pane') return;

    // Check if the new content is a dataset detail (contains ds-detail-root)
    var tmp = document.createElement('div');
    tmp.innerHTML = evt.detail.serverResponse;
    var newRoot = tmp.querySelector('#ds-detail-root');
    if (!newRoot) return; // Not a dataset detail — let it swap normally

    // Same run clicked again: do NOT fall through to the default swap (which would
    // replace the whole pane with a single-column response and silently drop the
    // pinned column — the "sometimes the pinned vanishes" bug). Suppress the swap and
    // leave the current layout untouched.
    if (newRoot.dataset.runId === window._pinnedRunId) {
        evt.detail.shouldSwap = false;
        return;
    }

    // Prevent default HTMX swap
    evt.detail.shouldSwap = false;

    // Build two-column layout
    var pane = document.getElementById('inspector-pane');
    if (pane) {
        // Running FIRST means the choke-point purge listeners will see our
        // shouldSwap=false and skip — so this branch, which replaces the pane
        // itself, must do its own teardown (same rule as unpinDataset).
        if (window.PlotHost) {
            try { window.PlotHost.purgeWithin(pane); } catch (e) {}
            try { window.PlotHost.unobserveWithin(pane); } catch (e) {}
        }
        pane.innerHTML = _wrapPinnedLayout(window._pinnedHtml, evt.detail.serverResponse);
        _reviveInteractiveMarkup(pane);     // docs/118: the pinned half is a string
        // innerHTML skips <script> execution + htmx wiring, so without this the
        // browsed (right) column's Raw Data tab (hx-trigger container) never
        // activates and spins "Loading data files…" forever — the inert-column bug.
        _activatePinnedPane(pane);
        // No tab rewiring needed: each column's native onclick="switchDatasetTab('…', this)"
        // now scopes to its own panel via _h5Panel(this), so the two columns' tabs work
        // independently (the old _syncPinnedTabs/_switchBothColumns looked for the removed
        // ds-tab-overview/results/figures ids and rendered nothing — round-4 regression).
        _initCompareSplitResizer(pane);

        // Update pin button in current column
        var pinBtn = pane.querySelector('.inspector-current-col #inspector-pin-btn');
        if (pinBtn) pinBtn.remove(); // Remove pin button from right column to avoid confusion

        // Per-column close (Item 2): each column's X must remove only ITS comparison,
        // not the global closeInspector() that blanks the whole #inspector-pane (= the
        // entire split). Left X unpins (keep current alone); right X closes current
        // (keep pinned alone). Scope by column so we rewrite the right button.
        var leftClose = pane.querySelector('.inspector-pinned-col .inspector-close');
        if (leftClose) leftClose.onclick = window.unpinDataset;
        var rightClose = pane.querySelector('.inspector-current-col .inspector-close');
        if (rightClose) rightClose.onclick = _closeCurrentKeepPinned;
    }
}

// ── Sticky view state: preserves inspector state across ALL navigation ──
// Works for table rows, bookmark panel, parent/child links — no click capture needed.

// Qubit/Pair inspector sticky state
var _inspectorSticky = {
    type: null,            // 'qubit' or 'pair' — only restore when same type
    sections: {},          // { sectionName: true/false (open/closed) }
    scrollTop: 0,          // Inspector pane scroll position
};

// Dataset inspector sticky state
var _dsSticky = {
    tab: 'full',           // Last tab the user was on (default Full View)
    sectionAnchor: null,   // { key, within } — section + in-section offset for combined tabs
    scrollTop: 0,          // Inspector pane scroll position before last swap
    plot: null,            // HDF5 plot state before last swap
    currentRunId: null,    // Run ID currently shown in inspector
    expandedPaths: [],     // JSON tree paths expanded in Parameters (Overview tab)
    stateTab: null,        // Active State sub-tab: 'state'|'wiring'|'node'|'data'
    stateTreePaths: {},    // { 'node': [...paths], 'data': [...paths], ... }
};

// After a #table-pane swap, restore the experiment chip selection state.
// The server always renders the chip grid with "All" active; if the user had
// selected specific experiments before clicking a date tab, re-apply them.
document.addEventListener('htmx:afterSwap', function(evt) {
    if (!evt.detail || !evt.detail.target) return;
    if (evt.detail.target.id !== 'table-pane') return;
    if (!_selectedExps || _selectedExps.size === 0) return;
    var grid = document.getElementById('exp-filter-grid');
    if (!grid) return;
    // Update chip active states to match _selectedExps
    grid.querySelectorAll('.exp-chip').forEach(function(c) {
        var v = c.getAttribute('data-exp');
        if (v === '') {
            c.classList.toggle('active', false);
        } else {
            c.classList.toggle('active', _selectedExps.has(v));
        }
    });
    _applyDatasetFilters();
});

// Sync the Collections tag-filter chips after a #table-pane swap. Clear the tag
// selection when leaving Collections (no tag grid present) so it can't bleed
// onto the plain Datasets page; re-apply it when the chips are present.
document.addEventListener('htmx:afterSwap', function(evt) {
    if (!evt.detail || !evt.detail.target) return;
    if (evt.detail.target.id !== 'table-pane') return;
    var tagGrid = document.getElementById('tag-filter-grid');
    if (!tagGrid) {
        if (_selectedTags.size > 0) _selectedTags.clear();
        return;
    }
    _syncTagFilterUI();
    if (_selectedTags.size > 0) _applyDatasetFilters();
});

// Capture inspector state just before swap (inspector DOM still has old content)
document.addEventListener('htmx:beforeSwap', function(evt) {
    if (!evt.detail || !evt.detail.target) return;
    if (evt.detail.target.id !== 'inspector-pane') return;
    if (window._pinnedRunId) return; // Pin mode handles its own layout

    var pane = document.getElementById('inspector-pane');
    if (!pane) return;

    // ── Capture section collapse state for all inspector types ──
    // Read the type off the header class (datasets no longer carry a badge —
    // their run-id replaced it, so badge-class sniffing would miss them).
    var header = pane.querySelector('.inspector-header');
    if (header) {
        var type = null;
        if (header.classList.contains('inspector-header-qubit')) type = 'qubit';
        else if (header.classList.contains('inspector-header-pair')) type = 'pair';
        else if (header.classList.contains('inspector-header-dataset')) type = 'dataset';
        if (type) {
            _inspectorSticky.type = type;
            _inspectorSticky.scrollTop = pane.scrollTop;
            _inspectorSticky.sections = {};
            pane.querySelectorAll('details.detail-section').forEach(function(d) {
                var summary = d.querySelector('summary');
                if (summary) _inspectorSticky.sections[summary.textContent.trim()] = d.open;
            });
        }
    }

    // ── Capture dataset JSON tree expanded paths ──
    // Helper to collect expanded paths from a tree container
    function _collectExpanded(container) {
        var expanded = [];
        if (!container) return expanded;
        container.querySelectorAll('.tree-node').forEach(function(node) {
            var toggle = node.querySelector(':scope > .tree-row > .tree-toggle');
            if (toggle && toggle.classList.contains('expanded')) {
                var path = node.getAttribute('data-path');
                if (path) expanded.push(path);
            }
        });
        return expanded;
    }

    // Parameters tree (Overview tab)
    _dsSticky.expandedPaths = _collectExpanded(document.getElementById('ds-params-tree'));

    // State tab sub-tabs (node.json, data.json, state.json, wiring.json)
    _dsSticky.stateTab = null;
    _dsSticky.stateTreePaths = {};
    var stateTabActive = pane.querySelector('#ds-state-file-tabs .tree-file-tab.active');
    if (stateTabActive) {
        _dsSticky.stateTab = stateTabActive.textContent.trim().replace('.json', '');
    }
    ['state', 'wiring', 'node', 'data'].forEach(function(name) {
        var tree = document.getElementById('ds-state-tree-' + name);
        if (tree && tree.querySelector('.tree-node')) {
            _dsSticky.stateTreePaths[name] = _collectExpanded(tree);
        }
    });

    // ── Capture the section anchor for the combined (Full/Overview/Results/
    //    Figures) view: which section sits at the top of the viewport + the
    //    offset within it. On the next run we scroll to the SAME section, so the
    //    user keeps their place even though runs differ in content height. ──
    _dsSticky.sectionAnchor = null;
    var combined = document.getElementById('ds-tab-combined');
    if (combined && !combined.classList.contains('hidden')) {
        var top = pane.scrollTop;
        var anchorSec = null;
        combined.querySelectorAll('[data-fvsec]').forEach(function(sec) {
            if (sec.classList.contains('hidden')) return;
            if (sec.offsetTop <= top + 4) anchorSec = sec; // topmost section at/above the fold
        });
        if (!anchorSec) {
            anchorSec = Array.prototype.filter.call(
                combined.querySelectorAll('[data-fvsec]'),
                function(s) { return !s.classList.contains('hidden'); })[0] || null;
        }
        if (anchorSec) {
            _dsSticky.sectionAnchor = {
                key: anchorSec.getAttribute('data-fvsec'),
                within: Math.max(0, top - anchorSec.offsetTop),
            };
        }
    }

    // ── Capture scroll position and plot state ──
    _dsSticky.scrollTop = pane.scrollTop;
    try {
        _dsSticky.plot = window._dsLastPlot ? JSON.parse(JSON.stringify(window._dsLastPlot)) : null;
    } catch(e) {
        _dsSticky.plot = null;
    }
});

// Restore qubit/pair inspector state after HTMX swap
document.addEventListener('htmx:afterSwap', function(evt) {
    if (!evt.detail || !evt.detail.target) return;
    if (evt.detail.target.id !== 'inspector-pane') return;
    var pane = document.getElementById('inspector-pane');
    if (!pane) return;

    // Detect inspector type from badge
    var badge = pane.querySelector('.inspector-badge');
    if (!badge) return;
    var type = null;
    if (badge.classList.contains('inspector-badge-qubit')) type = 'qubit';
    else if (badge.classList.contains('inspector-badge-pair')) type = 'pair';
    if (!type || type !== _inspectorSticky.type) return;

    // Restore <details> open/closed state by matching summary text
    var sections = _inspectorSticky.sections;
    if (Object.keys(sections).length > 0) {
        pane.querySelectorAll('details.detail-section').forEach(function(d) {
            var summary = d.querySelector('summary');
            if (!summary) return;
            var name = summary.textContent.trim();
            if (name in sections) d.open = sections[name];
        });
    }

    // Restore scroll position
    if (_inspectorSticky.scrollTop) {
        requestAnimationFrame(function() {
            pane.scrollTop = _inspectorSticky.scrollTop;
        });
    }
});

// --- Sidebar tree highlight: mirror the opened dataset run into the left
// workspace tree (highlight + REVEAL). Two reasons the highlight used to be
// invisible even though the class was applied:
//   1. Date groups render as COLLAPSED <details> — the matched entry was hidden
//      inside a closed group, so the user saw nothing. We now open every
//      ancestor <details> to reveal it before scrolling.
//   2. Each date group caps at 50 rendered entries ("Show all N" loads the
//      rest on demand) — a run past the cap isn't in the DOM at all. We expand
//      that date group's "Show all" once and retry the highlight on the swap.
var _pendingTreeHighlight = null;   // {uid, date} awaiting a "Show all" expansion

function _openTreeAncestors(el) {
    // Open the date-group + root <details> so the entry is actually visible.
    var d = el.closest('details');
    while (d) {
        d.open = true;
        d = d.parentElement ? d.parentElement.closest('details') : null;
    }
}

function syncSidebarTreeHighlight(uid, date) {
    var tree = document.getElementById('sidebar-tree');
    if (!tree || !uid) return;
    tree.querySelectorAll('.tree-entry-click.tree-entry-active').forEach(function(e) {
        e.classList.remove('tree-entry-active');
    });
    var match = tree.querySelector(
        '.tree-entry-click[data-uid="' + CSS.escape(String(uid)) + '"]');
    if (match) {
        _openTreeAncestors(match);   // reveal it inside collapsed date groups
        match.classList.add('tree-entry-active');
        window._markActiveTreeBranch(match);
        // Scroll after the layout settles from opening the <details>. 'center'
        // (not 'nearest') so the revealed entry lands mid-viewport instead of
        // clinging to the bottom edge.
        requestAnimationFrame(function() {
            match.scrollIntoView({ block: 'center' });
        });
        _pendingTreeHighlight = null;
        return;
    }
    // Not rendered → likely past its date group's 50-entry cap. Find that group
    // by date, expand "Show all" once, and retry after the entries swap in.
    // (Guard against re-triggering for the same uid so a non-existent run can't
    // loop.) If two folders share the date this picks the first capped group;
    // worst case it reveals an unrelated group and no highlight lands — never a
    // wrong highlight.
    if (!date || (_pendingTreeHighlight && _pendingTreeHighlight.uid === uid)) return;
    var groups = tree.querySelectorAll('details.tree-date');
    for (var i = 0; i < groups.length; i++) {
        var label = groups[i].querySelector('summary.tree-date-label');
        var btn = groups[i].querySelector('.tree-show-more-btn');
        if (!label || !btn) continue;   // no "Show all" → group isn't capped
        if (label.textContent.trim().indexOf(date) === 0) {
            groups[i].open = true;
            _pendingTreeHighlight = { uid: uid, date: date };
            btn.click();   // HTMX GET → swaps the full date group into the <ul>
            return;
        }
    }
}

// Restore state after HTMX loads new dataset detail
document.addEventListener('htmx:afterSwap', function(evt) {
    // A pending "Show all" expansion (cap overflow) just swapped its entries in
    // — retry the highlight now that the run's entry exists. One-shot: clear
    // before retrying so a still-missing run can't loop. (Runs on ANY swap: the
    // "Show all" button targets the date group's <ul>, not the inspector pane.)
    if (_pendingTreeHighlight) {
        var ph = _pendingTreeHighlight;
        _pendingTreeHighlight = null;
        syncSidebarTreeHighlight(ph.uid, ph.date);
    }

    // Everything below mirrors/restores the dataset detail — only when THIS swap
    // actually (re)rendered the inspector pane. Without this guard, navigating to
    // Explorer/Chip Compare (a #table-pane swap) re-fires syncSidebarTreeHighlight
    // against the stale #ds-detail-root still sitting in the inspector, yanking the
    // sidebar back down to the previously-opened run. Matches siblings at 5796/5892.
    if (!evt.detail || !evt.detail.target) return;
    if (evt.detail.target.id !== 'inspector-pane') return;

    var pane = document.getElementById('inspector-pane');
    if (!pane) return;
    // r13: one-shot fresh-open flag (new-run popup). Consumed on ANY inspector
    // swap so a failed detail render can never leak it onto a later navigation.
    var freshOpen = window._dsOpenAtTop === true;
    window._dsOpenAtTop = false;
    var root = pane.querySelector('#ds-detail-root');
    if (!root) return;

    var newRunId = root.dataset.uid;   // folder-aware uid (matches tree entries' data-uid)

    // Mirror the opened run into the left sidebar tree (highlight + reveal).
    // Highlight only — never the /workspace/select round-trip (a different
    // action that would reload the chip).
    syncSidebarTreeHighlight(newRunId, root.dataset.date);

    if (newRunId === _dsSticky.currentRunId) {
        if (freshOpen) pane.scrollTop = 0;   // re-opened the same run from the popup
        return;
    }

    var hadPrevious = !!_dsSticky.currentRunId;
    _dsSticky.currentRunId = newRunId;
    window._dsLastPlot = null;

    if (freshOpen) {
        // New-run popup open: Full View is the template default; land at the
        // TOP and restore nothing (params trees / section anchors / State
        // sub-tabs belong to the previous run's viewing session).
        pane.scrollTop = 0;
        return;
    }

    if (!hadPrevious) return; // First dataset ever opened — Full View is the default

    setTimeout(function() {
        // 1. Per user request: every dataset opens in FULL VIEW by default (the
        // template default). We intentionally NO LONGER restore the last manually-
        // chosen tab across runs — Full View is always the landing tab.

        // 1b. Restore JSON tree expanded paths in Parameters section
        if (_dsSticky.expandedPaths.length > 0) {
            var paramsTree = document.getElementById('ds-params-tree');
            if (paramsTree) {
                _dsSticky.expandedPaths.forEach(function(path) {
                    var node = paramsTree.querySelector('.tree-node[data-path="' + CSS.escape(path) + '"]');
                    if (node) {
                        var toggle = node.querySelector(':scope > .tree-row > .tree-toggle');
                        if (toggle && toggle.classList.contains('collapsed')) {
                            toggle.click();
                        }
                    }
                });
            }
        }

        // 1c. Restore <details> open/closed state in dataset detail
        var dsSections = _inspectorSticky.sections;
        if (_inspectorSticky.type === 'dataset' && Object.keys(dsSections).length > 0) {
            pane.querySelectorAll('details.detail-section').forEach(function(d) {
                var summary = d.querySelector('summary');
                if (!summary) return;
                var name = summary.textContent.trim();
                if (name in dsSections) d.open = dsSections[name];
            });
        }

        // 1d. Restore scroll for the combined tabs (Full / Overview / Results /
        //     Figures) to the SAME section the user was viewing — runs differ in
        //     height, so anchor on the section, not a raw pixel offset. Figures
        //     reflow as their lazy <img>s load, so re-apply after a short delay.
        if (_DS_COMBINED_TABS.indexOf(_dsSticky.tab) !== -1) {
            var _restoreSectionScroll = function() {
                var p = document.getElementById('inspector-pane');
                if (!p) return;
                var anchor = _dsSticky.sectionAnchor;
                var combined = document.getElementById('ds-tab-combined');
                var sec = (anchor && combined)
                    ? combined.querySelector('[data-fvsec="' + anchor.key + '"]') : null;
                if (sec && !sec.classList.contains('hidden')) {
                    var targetTop = sec.offsetTop + (anchor.within || 0);
                    p.scrollTop = Math.min(targetTop, p.scrollHeight - p.clientHeight);
                } else if (_dsSticky.scrollTop) {
                    p.scrollTop = _dsSticky.scrollTop; // fallback
                }
            };
            requestAnimationFrame(_restoreSectionScroll);
            setTimeout(_restoreSectionScroll, 250);
        }

        // 1e. Restore State tab sub-tab and tree paths
        if (_dsSticky.tab === 'state' && _dsSticky.stateTab) {
            // Switch to the active sub-tab (node, data, state, wiring)
            if (typeof switchDatasetStateTab === 'function') {
                switchDatasetStateTab(_dsSticky.stateTab);
            }
            // Wait for lazy-loaded trees to render, then restore paths
            setTimeout(function() {
                Object.keys(_dsSticky.stateTreePaths).forEach(function(name) {
                    var paths = _dsSticky.stateTreePaths[name];
                    if (!paths || !paths.length) return;
                    var tree = document.getElementById('ds-state-tree-' + name);
                    if (!tree) return;
                    paths.forEach(function(path) {
                        var node = tree.querySelector('.tree-node[data-path="' + CSS.escape(path) + '"]');
                        if (node) {
                            var toggle = node.querySelector(':scope > .tree-row > .tree-toggle');
                            if (toggle && toggle.classList.contains('collapsed')) {
                                toggle.click();
                            }
                        }
                    });
                });
                // Restore scroll
                var p = document.getElementById('inspector-pane');
                if (p && _dsSticky.scrollTop) p.scrollTop = _dsSticky.scrollTop;
            }, 500);
        }

        // 2. Replay HDF5 multi-plot selections (figure scroll is now handled by
        //    the section-anchor restore above).
        if (_dsSticky.tab === 'data' && _dsSticky.plot &&
                 _dsSticky.plot.selections && _dsSticky.plot.selections.length) {
            // newRunId is the composite uid string ("<hex>:<int>"); parseInt() of it
            // is NaN, which made loadDatasetH5 fetch /dataset/NaN/h5 and the coords
            // lookup (keyed by the uid string) miss → HDF5 plot-selection replay was
            // dead across run navigation. Use the uid string directly.
            var runId = newRunId;
            var plot = _dsSticky.plot;

            // Legacy-only replay: ndview (no .h5-tab buttons) manages its own
            // state; replaying legacy selections onto it would mis-fire and the
            // MutationObserver below would watch forever (leak). Skip cleanly.
            // TODO(remove-legacy-h5): nothing renders .h5-tab since ndview
            // replaced the summary pipeline — drop this replay + loadDatasetH5
            // + the dataset_h5* routes after one transition release.
            if (!document.querySelector('.h5-tab')) return;
            // Switch to the correct h5 tab (ds_raw / ds_fit)
            if (plot.which) {
                document.querySelectorAll('.h5-tab').forEach(function(b) {
                    if (b.textContent.trim() === plot.which) loadDatasetH5(b, runId, plot.which);
                });
            }

            var summaryEl = document.getElementById('h5-summary-container');
            if (summaryEl) {
                var obs = new MutationObserver(function(_, observer) {
                    if (!summaryEl.querySelector('.h5-vars-table')) return;
                    observer.disconnect();
                    setTimeout(function() {
                        // Collect available vars + dims from the rendered table
                        var availableVars = [];
                        summaryEl.querySelectorAll('.h5-vars-table tbody tr').forEach(function(row) {
                            var code = row.querySelector('td:first-child code');
                            var btn  = row.querySelector('button');
                            if (!code || !btn) return;
                            var onclick = btn.getAttribute('onclick') || '';
                            var m = onclick.match(/plotOrSelectQubit\([^,]+,[^,]+,[^,]+,[^,]+,(\[[^\]]*\])\)/);
                            var dims = [];
                            try { if (m) dims = JSON.parse(m[1]); } catch(e) {}
                            availableVars.push({ varName: code.textContent.trim(), dims: dims });
                        });

                        var newExpType = _currentExperimentType();
                        var isSameExp = plot.experimentType && plot.experimentType === newExpType;
                        var validSelections = [];
                        var usedFallback = false;
                        var coords = (window._h5CoordsById && window._h5CoordsById[runId]) || {};

                        if (isSameExp) {
                            // Keep selections whose varName still exists in this run
                            plot.selections.forEach(function(sel) {
                                var found = null;
                                for (var i = 0; i < availableVars.length; i++) {
                                    if (availableVars[i].varName === sel.varName) { found = availableVars[i]; break; }
                                }
                                if (!found) return; // var no longer exists
                                var qCount = _getQubitCount(found.dims, coords);
                                var qIdx = sel.qubitIdx;
                                // 3D+ var with no stored qubit → default to the first
                                // qubit (so the replay highlights it instead of sending
                                // a null qubit_idx that the backend would reject).
                                if ((qIdx === null || qIdx === undefined) && qCount > 0) qIdx = 0;
                                if (qIdx === null || qIdx === undefined || qIdx < qCount) {
                                    validSelections.push({ varName: sel.varName, dims: found.dims, qubitIdx: qIdx });
                                }
                            });
                            if (!validSelections.length) usedFallback = true;
                        } else {
                            usedFallback = true;
                        }

                        if (usedFallback) {
                            // Fall back to first available variable
                            if (availableVars.length) {
                                var first = availableVars[0];
                                var qCount = _getQubitCount(first.dims, coords);
                                validSelections = [{
                                    varName: first.varName,
                                    dims: first.dims,
                                    qubitIdx: qCount > 0 ? 0 : null
                                }];
                            }
                        }

                        window._dsLastPlot = {
                            which: plot.which || 'ds_raw',
                            experimentType: newExpType,
                            selections: validSelections
                        };
                        _updateVarRowStates();

                        // Show caution banner when we fell back to defaults
                        if (usedFallback) {
                            var banner = document.createElement('div');
                            banner.className = 'h5-caution-banner';
                            banner.textContent = '\u26a0 Different experiment type \u2014 showing default variable';
                            var pc = document.querySelector('[id$="h5-plot-container"]');
                            if (pc) pc.parentNode.insertBefore(banner, pc);
                        }

                        var panel = document.getElementById('inspector-pane');
                        // Restore qubit selectors for 3D+ vars
                        validSelections.forEach(function(sel) {
                            var qInfo = _findQubitDim(sel.dims, coords);
                            if (qInfo && qInfo.labels && qInfo.labels.length > 0) {
                                _showQubitMultiSelector(panel, runId, window._dsLastPlot.which, sel.varName, sel.dims, qInfo.labels);
                            }
                        });
                        _renderAllSelections(panel, runId);

                        // Restore scroll after Plotly renders
                        requestAnimationFrame(function() {
                            setTimeout(function() {
                                var p = document.getElementById('inspector-pane');
                                if (p && _dsSticky.scrollTop) p.scrollTop = _dsSticky.scrollTop;
                            }, 600);
                        });
                    }, 50);
                });
                obs.observe(summaryEl, { childList: true, subtree: true });
            }
        }
    }, 150);
});


// ══════════════════════════════════════════════════════════════════════
// Global new-run detection poller
// ══════════════════════════════════════════════════════════════════════

(function() {
    // Multi-folder new-run detection. The poll returns the globally-latest run
    // across ALL active data folders as a folder-aware uid ("<folder_key>:<run_id>")
    // plus its (date,time). We fire the popup only when a run with a STRICTLY
    // newer timestamp than the baseline appears — keyed by uid, never by the bare
    // run_id. That kills the old false positive where the active folder silently
    // flipped (a different folder's higher run_id read as a "new experiment").
    var _lastSeenUid = null;
    var _lastSeenStamp = null;   // "<date> <time>" of the latest run we've acknowledged
    var _newRunHideTimer = null; // auto-dismiss timer for the new-run popup
    var _pendingRun = null;
    var POLL_SECS = (window.UI_CONFIG && UI_CONFIG.autoRefreshInterval) || 60;
    // Phase 5 §1.1 + §1.2 + §4.1 — chained-setTimeout loop with
    // visibility gating + exponential backoff. Replaces the previous
    // setInterval that ran forever, ignored document.visibilityState,
    // swallowed errors silently, and had no client-side timeout. A
    // backgrounded pywebview window or browser tab now stops issuing
    // requests until it becomes visible again; consecutive failures
    // back off up to 5 minutes and surface a "connection lost" toast.
    var _pollTimer = null;
    var _failures = 0;
    var POLL_MAX_BACKOFF_MS = 5 * 60 * 1000;
    var POLL_FETCH_TIMEOUT_MS = 10 * 1000;

    function _fetchWithTimeout(url, ms) {
        var ctrl = (typeof AbortController !== "undefined") ? new AbortController() : null;
        var opts = ctrl ? { signal: ctrl.signal } : {};
        var timer = setTimeout(function() { if (ctrl) ctrl.abort(); }, ms);
        return fetch(url, opts).finally(function() { clearTimeout(timer); });
    }

    function _schedule(delayMs) {
        if (_pollTimer) clearTimeout(_pollTimer);
        _pollTimer = setTimeout(function() {
            if (document.visibilityState === "hidden") {
                // Tab is hidden: postpone but keep the chain alive so
                // a future visibilitychange wakes us up cheaply.
                _schedule(delayMs);
                return;
            }
            pollForNewRuns();
        }, delayMs);
    }

    function pollForNewRuns() {
        _fetchWithTimeout('/datasets/poll', POLL_FETCH_TIMEOUT_MS)
            .then(function(r) {
                if (!r.ok) throw new Error("HTTP " + r.status);
                return r.json();
            })
            .then(function(data) {
                if (_failures > 0) {
                    _failures = 0;
                    _clearPollFailureBanner();
                }
                if (!data.uid) {
                    _schedule(POLL_SECS * 1000);
                    return;
                }
                var stamp = ((data.date || '') + ' ' + (data.time || '')).trim();
                // First poll: record baseline, don't popup.
                if (_lastSeenUid === null) {
                    _lastSeenUid = data.uid;
                    _lastSeenStamp = stamp;
                } else if (data.uid !== _lastSeenUid && stamp > _lastSeenStamp) {
                    // A genuinely newer run (later timestamp) became the latest —
                    // not merely a folder-set change pointing at a pre-existing run.
                    _lastSeenUid = data.uid;
                    _lastSeenStamp = stamp;
                    _pendingRun = data;
                    _showNewRunPopup(data);
                }
                _schedule(POLL_SECS * 1000);
            })
            .catch(function() {
                _failures++;
                if (_failures >= 3) _showPollFailureBanner();
                var backoff = Math.min(POLL_SECS * 1000 * Math.pow(2, _failures - 1),
                                       POLL_MAX_BACKOFF_MS);
                _schedule(backoff);
            });
    }

    function _showPollFailureBanner() {
        var el = document.getElementById("dataset-poll-status");
        if (!el) {
            el = document.createElement("div");
            el.id = "dataset-poll-status";
            el.className = "poll-status-toast";
            el.setAttribute("role", "status");
            el.textContent = "Lost connection to the server. Retrying…";
            document.body.appendChild(el);
        }
        el.hidden = false;
    }
    function _clearPollFailureBanner() {
        var el = document.getElementById("dataset-poll-status");
        if (el) el.hidden = true;
    }

    document.addEventListener("visibilitychange", function() {
        if (document.visibilityState === "visible") {
            // Fire immediately on tab-return; the next scheduled tick
            // will then chain off this one's outcome.
            if (_pollTimer) clearTimeout(_pollTimer);
            pollForNewRuns();
        }
    });

    function _showNewRunPopup(data) {
        var popup = document.getElementById('new-run-popup');
        if (!popup) return;
        document.getElementById('new-run-popup-id').textContent = '#' + data.run_id;
        document.getElementById('new-run-popup-exp').textContent = data.experiment_name;
        var qEl = document.getElementById('new-run-popup-qubits');
        qEl.textContent = data.qubits && data.qubits.length ? data.qubits.join(', ') : '';
        qEl.style.display = qEl.textContent ? '' : 'none';
        document.getElementById('new-run-popup-time').textContent =
            ((data.date || '') + ' ' + (data.time || '')).trim();
        popup.style.display = '';
        // Auto-dismiss after a few seconds so the popup doesn't linger until the
        // user manually closes it (frequent request). Hovering the card pauses
        // the timer so the user can read it / click "Show Now".
        if (_newRunHideTimer) clearTimeout(_newRunHideTimer);
        _newRunHideTimer = setTimeout(window.dismissNewRunPopup, 7000);
        var card = popup.querySelector('.new-run-popup-card');
        if (card) card.onmouseenter = function() {
            if (_newRunHideTimer) { clearTimeout(_newRunHideTimer); _newRunHideTimer = null; }
        };
    }

    window.dismissNewRunPopup = function() {
        if (_newRunHideTimer) { clearTimeout(_newRunHideTimer); _newRunHideTimer = null; }
        var popup = document.getElementById('new-run-popup');
        if (popup) popup.style.display = 'none';
    };

    // docs/132 (heavy UX feedback): Esc dismisses too — the backdrop click
    // and the new ✕ already do. Bound on document (app.js runs from <head>);
    // stopPropagation so the modal beneath (if any) doesn't also close on
    // the same press.
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape') return;
        var popup = document.getElementById('new-run-popup');
        if (!popup || popup.style.display === 'none') return;
        // stopImmediatePropagation, not stopPropagation: trapFocus modals
        // register their own CAPTURE listener on this same node, and
        // stopPropagation does not suppress same-node listeners — one Esc
        // was closing both the popup AND the modal beneath it (docs/132
        // review). This handler registers at load, before any trap, so it
        // runs first.
        e.stopImmediatePropagation();
        e.preventDefault();
        window.dismissNewRunPopup();
    }, true);

    window.showNewRun = function() {
        window.dismissNewRunPopup();
        if (!_pendingRun) return;
        var runId = _pendingRun.uid;   // folder-aware uid for /dataset/<uid>
        _pendingRun = null;

        // Load dataset detail into #inspector-pane WITHOUT navigating away
        // from the current page. The inspector pane is present on every page
        // (Pulses, Explorer, Live Edit, etc.), so the user stays where they
        // are and sees the new run in the side panel. This mirrors the
        // sidebar-tree-entry click behavior.
        var inspectorPane = document.getElementById("inspector-pane");
        if (inspectorPane) {
            // r13 feedback: a NEW run opens FRESH — Full View, scrolled to the
            // top (the template default). This used to force-switch to the Raw
            // Data tab (a relic of the legacy h5 pipeline) and the sticky
            // restore then re-applied the previous run's scroll anchor on top —
            // users landed mid-page in an expanded Raw Data view. The one-shot
            // flag is consumed by the afterSwap restore handler, which skips
            // every sticky restore for this open only.
            window._dsOpenAtTop = true;
            htmx.ajax('GET', '/dataset/' + runId, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
        } else {
            // Fallback: no inspector pane → navigate to Datasets
            htmx.ajax('GET', '/datasets', {target: '#table-pane', swap: 'innerHTML'}).then(function() {
                history.pushState({}, '', '/datasets');
                if (window.syncSidebarNavActive) window.syncSidebarNavActive();
                window._dsOpenAtTop = true;
                htmx.ajax('GET', '/dataset/' + runId, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
            });
        }
    };

    // Start polling after page settles. The chain re-schedules itself
    // from inside pollForNewRuns (see _schedule), so no setInterval —
    // a slow response can't pile up overlapping requests.
    setTimeout(pollForNewRuns, 3000);
})();


/* ──────────────────────────────────────────────────────────────────
 * Param History — sparkline rendering + drawer
 * ────────────────────────────────────────────────────────────────── */
function renderParamHistorySparklines() {
    // Server-side pre-render (Family D1+D2 in
    // docs/23_param_history_performance.md): the SVG is now generated
    // by HistoryManager.render_sparkline_svg_inner() and injected
    // directly by the Jinja template. This function stays as a safety
    // net for any legacy ``data-points`` cells that might still arrive
    // (e.g. from a custom client). Cells whose SVG already has content
    // are skipped.
    var cells = document.querySelectorAll('#param-history-root .history-cell');
    cells.forEach(function(td) {
        var svg = td.querySelector('.history-cell-spark');
        if (!svg) return;
        // Skip if already server-rendered.
        if (svg.children && svg.children.length > 0) return;
        var pointsAttr = td.getAttribute('data-points');
        if (!pointsAttr) return;
        var points;
        try { points = JSON.parse(pointsAttr); }
        catch(e) { return; }
        if (!points || !points.length) return;

        var nums = points.map(function(p) { return p.value; })
                         .filter(function(v) { return typeof v === 'number' && isFinite(v); });
        if (nums.length < 2) return;

        var min = Math.min.apply(null, nums);
        var max = Math.max.apply(null, nums);
        var range = max - min || 1;
        var W = 100, H = 30;

        var coords = [];
        var pts = [];
        for (var i = 0; i < points.length; i++) {
            var v = points[i].value;
            if (typeof v !== 'number' || !isFinite(v)) continue;
            var x = (i / (points.length - 1)) * W;
            var y = H - ((v - min) / range) * (H - 4) - 2;
            coords.push(x.toFixed(2) + ',' + y.toFixed(2));
            pts.push({x: x, y: y, trigger: points[i].trigger});
        }
        if (coords.length < 2) return;

        var fillD = 'M0,' + H + ' L' + coords.join(' L') + ' L' + W + ',' + H + ' Z';
        var line  = '<path class="hs-fill" d="' + fillD + '"/>'
                  + '<polyline class="hs-line" points="' + coords.join(' ') + '"/>';

        var curRaw = td.getAttribute('data-current');
        if (curRaw !== null && curRaw !== '') {
            var cur = parseFloat(curRaw);
            if (!isNaN(cur) && cur >= min && cur <= max) {
                var cy = H - ((cur - min) / range) * (H - 4) - 2;
                line += '<line class="hs-current" x1="0" y1="' + cy.toFixed(2)
                      + '" x2="' + W + '" y2="' + cy.toFixed(2) + '"/>';
            }
        }

        var dotEvery = Math.max(1, Math.floor(pts.length / 30));
        var dots = '';
        for (var j = 0; j < pts.length; j += dotEvery) {
            var p = pts[j];
            dots += '<circle class="hs-pt hs-pt-' + (p.trigger || 'auto') + '" cx="' + p.x.toFixed(2)
                  + '" cy="' + p.y.toFixed(2) + '" r="1.4"/>';
        }
        var last = pts[pts.length - 1];
        dots += '<circle class="hs-pt hs-pt-' + (last.trigger || 'auto') + '" cx="' + last.x.toFixed(2)
              + '" cy="' + last.y.toFixed(2) + '" r="2"/>';

        svg.innerHTML = line + dots;
    });
}

function paramHistoryOpenDrawer(qubit, prop) {
    var drawer = document.getElementById('param-history-drawer');
    if (!drawer) return;
    // A live #phd-chart rendered with responsive:true holds a window-resize
    // handler that references the graph div — innerHTML without purge leaks
    // the whole detached subtree per open (docs/124, the popover/drawer
    // minor). Purge through the choke point this drawer used to bypass.
    if (window.PlotHost) { try { window.PlotHost.purgeWithin(drawer); } catch (e) {} }
    drawer.style.display = 'block';
    drawer.innerHTML = '<p class="muted" style="padding:1rem">Loading…</p>';
    var url = '/param-history/expand?qubit=' + encodeURIComponent(qubit)
            + '&prop=' + encodeURIComponent(prop);
    // Chart the chip the GRID is showing (archived chips included) — the
    // drawer used to silently chart the loaded chip regardless.
    var root = document.getElementById('param-history-root');
    var activeKey = root && (root.getAttribute('data-active-chip-key') || '');
    if (activeKey) url += '&chip_key=' + encodeURIComponent(activeKey);
    fetch(url).then(function(r) { return r.text(); }).then(function(html) {
        drawer.innerHTML = html;
        // Manually evaluate inline scripts (fetch doesn't run them)
        drawer.querySelectorAll('script').forEach(function(s) {
            var n = document.createElement('script');
            if (s.type) n.type = s.type;
            if (s.id)   n.id = s.id;
            n.textContent = s.textContent;
            s.parentNode.replaceChild(n, s);
        });
    });
    drawer.scrollIntoView({behavior: 'smooth', block: 'nearest'});
}

function paramHistoryCloseDrawer() {
    var drawer = document.getElementById('param-history-drawer');
    if (!drawer) return;
    if (window.PlotHost) {
        try { window.PlotHost.purgeWithin(drawer); } catch (e) {}
        try { window.PlotHost.unobserveWithin(drawer); } catch (e) {}
    }
    drawer.style.display = 'none';
    drawer.innerHTML = '';
}

// ---- filter form UX (debounced submit) --------------------------------
// The filter chips hide their <input> (CSS display:none) — the lit state is
// the label's .active class, which the SERVER renders. The form's submit is
// debounced (hx-trigger="change delay:500ms" in _param_history.html), so
// without an immediate echo a chip click would show nothing until the swap
// lands. This listener mirrors checked → .active instantly; the server
// render then confirms it. Delegated on document so it survives the
// outerHTML swaps of #param-history-root.
document.addEventListener('change', function(e) {
    var input = e.target;
    if (!input || !input.closest || !input.closest('#param-history-filters')) return;
    var chip = input.closest('.phf-chip');
    if (!chip) return;
    if (input.type === 'radio') {
        // Radio groups (the Date row): the browser unchecks the sibling
        // silently — resync the whole group, not just the clicked chip.
        var form = input.closest('form');
        form.querySelectorAll('input[type="radio"][name="' + input.name + '"]')
            .forEach(function(r) {
                var c = r.closest('.phf-chip');
                if (c) c.classList.toggle('active', r.checked);
            });
    } else if (input.type === 'checkbox') {
        chip.classList.toggle('active', input.checked);
    }
});

// All/None row togglers (_param_history.html). Programmatic .checked writes
// emit no events, so flip every box in the row first, then dispatch exactly
// ONE bubbling change event — the form's debounced hx-trigger fires once for
// the whole flip instead of once per checkbox.
function paramHistoryFilterSetRow(btn, on) {
    var row = btn.closest('.phf-row');
    if (!row) return;
    var boxes = row.querySelectorAll('.phf-chips input[type="checkbox"]');
    if (!boxes.length) return;
    boxes.forEach(function(box) {
        box.checked = on;
        var c = box.closest('.phf-chip');
        if (c) c.classList.toggle('active', on);
    });
    boxes[boxes.length - 1].dispatchEvent(new Event('change', { bubbles: true }));
}

// ---- trend statistics (moving average + ±σ band) ---------------------
// Customer request: history/trend charts should read like a statistics
// figure — a rolling-mean line with a shaded standard-deviation band.
// Centered window, edge-clamped; window auto-scales with series length.
window.rollingStats = function(values, win) {
    var n = values.length;
    if (!win) win = Math.min(15, Math.max(3, Math.round(n / 6)));
    var mean = new Array(n), std = new Array(n);
    for (var i = 0; i < n; i++) {
        var half = Math.floor(win / 2);
        var lo = Math.max(0, i - half);
        var hi = Math.min(n - 1, i + half);
        var s = 0, c = 0;
        for (var j = lo; j <= hi; j++) { s += values[j]; c++; }
        var m = s / c;
        var v = 0;
        for (var k = lo; k <= hi; k++) { v += (values[k] - m) * (values[k] - m); }
        mean[i] = m;
        std[i] = c > 1 ? Math.sqrt(v / (c - 1)) : 0;
    }
    return { mean: mean, std: std, win: win };
};

function _hexToRgba(hex, alpha) {
    var m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec((hex || '').trim());
    if (!m) return 'rgba(128,128,128,' + alpha + ')';
    var h = m[1];
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    return 'rgba(' + parseInt(h.slice(0, 2), 16) + ',' +
        parseInt(h.slice(2, 4), 16) + ',' + parseInt(h.slice(4, 6), 16) + ',' +
        alpha + ')';
}

// The three Plotly traces (upper band edge, lower band edge w/ fill, MA
// line) for a finite series. Returns [] when the series is too short to
// say anything statistical (n < 5). One legend entry toggles all three.
window.trendStatTraces = function(x, values, opts) {
    opts = opts || {};
    var xs = [], ys = [];
    for (var i = 0; i < values.length; i++) {
        if (typeof values[i] === 'number' && isFinite(values[i])) {
            xs.push(x[i]); ys.push(values[i]);
        }
    }
    if (ys.length < 5) return [];
    var st = window.rollingStats(ys, opts.win);
    var color = opts.color || '#4f9cf9';
    var upper = st.mean.map(function(m, i) { return m + st.std[i]; });
    var lower = st.mean.map(function(m, i) { return m - st.std[i]; });
    var group = opts.legendgroup || 'trendstats';
    return [
        { x: xs, y: upper, type: 'scatter', mode: 'lines',
          line: { width: 0 }, hoverinfo: 'skip', showlegend: false,
          legendgroup: group },
        { x: xs, y: lower, type: 'scatter', mode: 'lines',
          line: { width: 0 }, fill: 'tonexty',
          fillcolor: _hexToRgba(color, 0.14),
          hoverinfo: 'skip', showlegend: false, legendgroup: group },
        { x: xs, y: st.mean, type: 'scatter', mode: 'lines',
          line: { color: color, width: 2 },
          name: 'moving avg ±σ (w=' + st.win + ')',
          legendgroup: group,
          hovertemplate: 'avg %{y:.6g}<extra></extra>' },
    ];
};

function paramHistoryRenderDrawerChart(data, currentValue) {
    // Plotly is lazy-loaded; the actual newPlot below gates on requirePlotly().
    var pts = (data.values || []).filter(function(p) {
        return typeof p.value === 'number' && isFinite(p.value);
    });
    if (!pts.length) {
        if (window.PlotHost) {
            try { window.PlotHost.purgeWithin(document.getElementById('phd-chart')); } catch (e) {}
        }
        document.getElementById('phd-chart').innerHTML =
            '<p class="muted" style="text-align:center;padding:2rem">No numeric values.</p>';
        return;
    }
    var triggers = ['save', 'manual', 'auto', 'experiment'];
    var TRIGGER_LABELS = {
        save:       'Saved through app',
        manual:     'Manual snapshot',
        auto:       'External edit (mtime change)',
        experiment: 'Experiment run',
    };
    var TRIGGER_PRETTY = {
        save: 'Save', manual: 'Manual', auto: 'Auto', experiment: 'Experiment',
    };
    var cssVar = function(t) {
        var s = getComputedStyle(document.documentElement)
            .getPropertyValue('--trigger-' + (t || 'auto'));
        return (s || '#888').trim();
    };
    var fmtTs = function(ts) {
        return ts.slice(0,4) + '-' + ts.slice(4,6) + '-' + ts.slice(6,8)
             + ' ' + ts.slice(9,11) + ':' + ts.slice(11,13) + ':' + ts.slice(13,15);
    };
    // Build a context line per point — used in hovertemplate.
    var contextLine = function(p) {
        var t = p.trigger || 'auto';
        if (t === 'experiment') {
            // Prefer "#<run_id> <experiment_name>" for experiment-driven snapshots
            var bits = [];
            if (p.run_id) bits.push('#' + p.run_id);
            if (p.experiment) bits.push(p.experiment);
            if (bits.length) return 'Experiment: ' + bits.join(' ');
            return TRIGGER_LABELS.experiment;
        }
        return TRIGGER_LABELS[t] || t;
    };
    var clickHintLine = function(p) {
        return p.run_id
            ? '<i style="opacity:0.7">click → open dataset #' + p.run_id + '</i>'
            : '';
    };

    // Statistics layer first (band renders BENEATH the trigger markers, and
    // being the first trace pins the category-axis order to the full
    // time-sorted series). Sorted copy — never mutate the fetched data.
    var sorted = pts.slice().sort(function(a, b) {
        return a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0;
    });
    var statTraces = window.trendStatTraces(
        sorted.map(function(p) { return fmtTs(p.timestamp); }),
        sorted.map(function(p) { return p.value; }),
        { color: (getComputedStyle(document.documentElement)
                    .getPropertyValue('--plot-colorway-1') || '#4f9cf9').trim() });

    var traces = triggers.map(function(t) {
        var subset = pts.filter(function(p) { return p.trigger === t; });
        // customdata = [run_id, experiment, contextLine, clickHint] for hovertemplate
        var customdata = subset.map(function(p) {
            return [
                p.run_id || 0,
                p.experiment || '',
                contextLine(p),
                clickHintLine(p),
            ];
        });
        return {
            x: subset.map(function(p) { return fmtTs(p.timestamp); }),
            y: subset.map(function(p) { return p.value; }),
            customdata: customdata,
            type: 'scatter', mode: 'markers',
            name: TRIGGER_PRETTY[t] || t,
            marker: {color: cssVar(t), size: 7,
                     line: {color: 'rgba(255,255,255,0.4)', width: 0.5}},
            hovertemplate:
                '<b>%{x}</b>'
                + '<br><span style="font-size:0.95em">%{y:.6g}</span>'
                + '<br>%{customdata[2]}'
                + '<br>%{customdata[3]}'
                + '<extra></extra>',
            // Stash the raw points so the click handler can read run_id directly
            _phRawPoints: subset,
        };
    }).filter(function(tr) { return tr.x.length > 0; });
    traces = statTraces.concat(traces);

    var layout = {
        margin: {l: 50, r: 15, t: 10, b: 50},
        xaxis: {title: '', tickfont: {size: 10}},
        yaxis: {title: data.property, tickfont: {size: 10}},
        legend: {orientation: 'h', y: -0.25},
        plot_bgcolor: 'transparent', paper_bgcolor: 'transparent',
        font: {color: getComputedStyle(document.documentElement).getPropertyValue('--pico-color').trim() || '#222'},
        shapes: (typeof currentValue === 'number') ? [{
            type: 'line', xref: 'paper', x0: 0, x1: 1, y0: currentValue, y1: currentValue,
            line: {color: cssVar('experiment'), dash: 'dot', width: 1},
        }] : [],
        hoverlabel: {bgcolor: 'rgba(40,40,40,0.92)', font: {color: '#eee', size: 12}},
    };
    window.requirePlotly().then(function() {
        return Plotly.newPlot('phd-chart', traces, layout, {responsive: true, displayModeBar: false});
    })
        .then(function() {
            var plotDiv = document.getElementById('phd-chart');
            /* docs/122 — one of the two surfaces that draws itself instead of
               going through _plotlyRender, so no central hook could ever reach
               it. Its host is `width:100%` inside the main pane: exactly the box
               a sidebar collapse or a gutter drag changes. Observe the DRAWER,
               not the chart div. */
            if (window.PlotHost) {
                window.PlotHost.observe(document.getElementById('param-history-drawer'));
            }
            // Click → open the experiment's dataset detail in the same window
            plotDiv.on('plotly_click', function(evt) {
                if (!evt.points || !evt.points.length) return;
                var pt = evt.points[0];
                var cd = pt.customdata;
                if (!cd) return;
                var runId = cd[0];
                if (!runId) return;
                // Use HTMX so the dataset detail loads inside the main pane
                var url = '/dataset/' + runId;
                if (window.htmx) {
                    window.htmx.ajax('GET', url, {
                        target: '#table-pane', swap: 'innerHTML', pushUrl: 'true',
                    });
                } else {
                    window.location.href = url;
                }
            });
            // Cursor: pointer for clickable points (run_id present)
            plotDiv.on('plotly_hover', function(evt) {
                if (!evt.points || !evt.points.length) return;
                var cd = evt.points[0].customdata;
                if (cd && cd[0]) {
                    plotDiv.style.cursor = 'pointer';
                }
            });
            plotDiv.on('plotly_unhover', function() {
                plotDiv.style.cursor = '';
            });
        });
}

function dismissChipSwap(btn) {
    // Hide the banner immediately for responsiveness, then tell the server.
    var banner = btn.closest('.chip-swap-banner');
    if (banner) banner.style.display = 'none';
    fetch('/param-history/dismiss-chip-swap', {method: 'POST'}).catch(function() {});
}

function paramHistoryDecide(btn, decision) {
    var banner = btn.closest('.chip-decision-banner');
    if (!banner) return;
    var chipKey = banner.getAttribute('data-chip-key');
    var dataFolder = banner.getAttribute('data-data-folder');
    var fd = new FormData();
    fd.append('chip_key', chipKey);
    fd.append('data_folder', dataFolder);
    fd.append('decision', decision);
    btn.disabled = true;
    fetch('/param-history/decide', {method: 'POST', body: fd})
        .then(function(r) { return r.json(); })
        .then(function() {
            // Hide the banner and re-run backfill so the new decision takes effect
            banner.style.display = 'none';
            paramHistoryBackfill();
        })
        .catch(function() {
            btn.disabled = false;
        });
}

function paramHistoryBackfill(forceRenamed) {
    var status = document.getElementById('ph-backfill-status');
    if (status) status.textContent = 'Starting…';
    // Mark the session attempt at FIRE time, not only on completion: otherwise a
    // second htmx:afterSwap during the in-flight window (or a rejected fetch)
    // sees no marker and kicks off a duplicate / looping backfill. The done/error
    // branches re-mark (harmless) for the chipKey that may load slightly later.
    _paramHistoryMarkSessionAttempt();
    var url = '/param-history/backfill' + (forceRenamed ? '?force_renamed=1' : '');
    fetch(url, {method: 'POST'})
        .then(function(r) { return r.json(); })
        .then(function() {
            // Wake the topbar pill so it tracks progress even if the user
            // navigates away from /param-history mid-import.
            document.dispatchEvent(new CustomEvent('param-history:backfill-started'));
            _paramHistoryPollBackfill();
        })
        .catch(function(err) {
            // Network/parse failure: marker is already set (above) so we won't
            // auto-loop; just surface it.
            if (status) status.textContent = 'Import request failed.';
            console.warn('param-history backfill failed:', err);
        });
}

function _paramHistoryPollBackfill() {
    var status = document.getElementById('ph-backfill-status');
    var loader = document.getElementById('quam-loader');
    var progressLine = document.getElementById('quam-loader-progress');
    fetch('/param-history/backfill/status')
        .then(function(r) { return r.json(); })
        .then(function(s) {
            if (s.status === 'running') {
                var msg = 'Importing… ' + (s.done || 0) + ' / ' + (s.total || '?');
                if (status) status.textContent = msg;
                // Mirror the count under the QUAM STATE MANAGER animation
                // so the user can watch progress without hunting for the
                // tiny status text in the filter row.
                if (loader && progressLine) {
                    loader.classList.add('visible');
                    progressLine.textContent = msg;
                }
                // the brand indicator counts the same import (docs/126 r3)
                if (window.NavProgress) NavProgress.external('Importing snapshots', s.done || 0, s.total || 0);
                setTimeout(_paramHistoryPollBackfill, 800);
            } else if (s.status === 'done') {
                if (status) status.textContent = 'Imported ' + (s.ingested || 0) + ' snapshots. Reloading…';
                if (progressLine) progressLine.textContent = '';
                if (loader) loader.classList.remove('visible');
                if (window.NavProgress) NavProgress.externalDone();
                // Mark this chip as "user has imported at least once" so
                // the auto-incremental backfill on next visit can fire
                // without surprising a first-time user.
                _paramHistoryMarkImported();
                // ALSO mark "we already auto-fired this session" so the
                // post-reload htmx:afterSwap → paramHistoryMaybeAutoBackfill
                // doesn't kick off another backfill if the workspace-vs-
                // index gap didn't close (e.g. every entry skipped as a
                // failure). Without this guard the user sees an infinite
                // "Importing…" loop — the bug fix-of-record.
                _paramHistoryMarkSessionAttempt();
                setTimeout(function() {
                    if (window.htmx) {
                        window.htmx.ajax('GET', '/param-history',
                            {target: '#param-history-root', swap: 'outerHTML', pushUrl: 'true'});
                    } else {
                        location.reload();
                    }
                }, 600);
            } else if (s.status === 'error') {
                if (status) status.textContent = 'Error: ' + (s.error || 'unknown');
                if (progressLine) progressLine.textContent = '';
                if (loader) loader.classList.remove('visible');
                if (window.NavProgress) NavProgress.externalDone();
                // Errors also count as an attempt — don't keep auto-firing.
                _paramHistoryMarkSessionAttempt();
            } else {
                // Unknown/unexpected status — treat as terminal so the poll chain
                // doesn't die silently and let htmx:afterSwap re-fire forever.
                if (loader) loader.classList.remove('visible');
                if (window.NavProgress) NavProgress.externalDone();
                _paramHistoryMarkSessionAttempt();
            }
        })
        .catch(function(err) {
            // Status fetch failed — stop the chain; the attempt is already marked.
            if (loader) loader.classList.remove('visible');
            if (window.NavProgress) NavProgress.externalDone();
            _paramHistoryMarkSessionAttempt();
            console.warn('param-history backfill status poll failed:', err);
        });
}

/* Empty-state CTA card (template: param-history-cta) calls this. Disables
 * the button to prevent double-click, then routes through the normal
 * backfill flow which surfaces progress via the QUAM STATE MANAGER loader. */
function paramHistoryImportFromCta(btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Importing…'; }
    var loader = document.getElementById('quam-loader');
    var progressLine = document.getElementById('quam-loader-progress');
    if (loader) loader.classList.add('visible');
    if (progressLine) progressLine.textContent = 'Starting import…';
    paramHistoryBackfill(false);
}

/* Persist "this chip has been imported at least once" so auto-incremental
 * can run on the next visit. Keyed by chip key from the page's data-attr,
 * scoped to the localStorage of this browser profile (intentional — we
 * don't want a second machine to silently re-import a chip the user
 * hasn't seen here yet). */
function _paramHistoryImportedKey(chipKey) {
    return 'quam_imported_' + (chipKey || 'unknown');
}
function _paramHistoryMarkImported() {
    var root = document.getElementById('param-history-root');
    if (!root) return;
    var chipKey = root.getAttribute('data-loaded-chip-key') || '';
    if (!chipKey) return;
    try { localStorage.setItem(_paramHistoryImportedKey(chipKey), '1'); } catch(e) {}
}
function _paramHistoryLegacyChipKey() {
    // Pre-identity-ladder path-derived key: existing localStorage /
    // sessionStorage guards were written under it. Accept either key so an
    // adopted/named chip doesn't re-fire a redundant auto-backfill.
    var root = document.getElementById('param-history-root');
    return (root && root.getAttribute('data-legacy-chip-key')) || '';
}
function _paramHistoryHasImportedBefore(chipKey) {
    try {
        if (localStorage.getItem(_paramHistoryImportedKey(chipKey)) === '1') return true;
        var legacy = _paramHistoryLegacyChipKey();
        return !!legacy && legacy !== chipKey &&
            localStorage.getItem(_paramHistoryImportedKey(legacy)) === '1';
    }
    catch(e) { return false; }
}

/* Per-session "already auto-fired" marker. Scoped to sessionStorage so
 * it persists across HTMX page reloads (which is exactly when the bug
 * loop would re-fire) but resets when the browser tab closes. Keyed by
 * chip so switching chips lets the new chip auto-fire independently.
 * Cleared by the banner's "Retry import" button so the user can opt in
 * to another auto-attempt after fixing the underlying problem. */
function _paramHistorySessionAttemptKey(chipKey) {
    return 'paramHistoryBackfillAttempt:' + (chipKey || 'unknown');
}
function _paramHistoryMarkSessionAttempt() {
    var root = document.getElementById('param-history-root');
    if (!root) return;
    var chipKey = root.getAttribute('data-loaded-chip-key') || '';
    if (!chipKey) return;
    try { sessionStorage.setItem(_paramHistorySessionAttemptKey(chipKey), String(Date.now())); } catch(e) {}
}
function _paramHistorySessionAttemptedAlready(chipKey) {
    try {
        if (sessionStorage.getItem(_paramHistorySessionAttemptKey(chipKey))) return true;
        var legacy = _paramHistoryLegacyChipKey();
        return !!legacy && legacy !== chipKey &&
            !!sessionStorage.getItem(_paramHistorySessionAttemptKey(legacy));
    }
    catch(e) { return false; }
}
function _paramHistoryClearSessionAttempt(chipKey) {
    try { sessionStorage.removeItem(_paramHistorySessionAttemptKey(chipKey)); } catch(e) {}
}

/* Banner button: explicit user retry. Clears the per-session guard so
 * the auto-trigger could fire again, but we also call the manual
 * backfill straight away — no need to wait for an htmx:afterSwap. */
window.paramHistoryRetryBackfill = function() {
    var root = document.getElementById('param-history-root');
    var chipKey = root && (root.getAttribute('data-loaded-chip-key') || '');
    if (!chipKey) return;
    _paramHistoryClearSessionAttempt(chipKey);
    paramHistoryBackfill(false);
};

/* Auto-incremental backfill: on Param History load, check if this chip
 * has been imported before AND the workspace alignment scan now reports
 * more importable experiments than the index has. If so, kick off a
 * silent backfill. The QUAM STATE MANAGER loader animation makes the
 * wait feel intentional rather than broken. */
function paramHistoryMaybeAutoBackfill() {
    var root = document.getElementById('param-history-root');
    if (!root) return;
    if (root.getAttribute('data-is-loaded-chip') !== '1') return;
    var chipKey = root.getAttribute('data-loaded-chip-key') || '';
    if (!chipKey) return;
    if (!_paramHistoryHasImportedBefore(chipKey)) return;  // first visit handled by CTA card
    // Loop guard: once this session has attempted (and completed, error or
    // success) an auto-backfill for this chip, don't fire again. The
    // banner's "Retry import" button is the only path back in. Without
    // this guard a chip whose workspace experiments fail to copy/parse
    // would loop forever — the heuristic below stays satisfied because
    // failed entries never write SQLite rows.
    if (_paramHistorySessionAttemptedAlready(chipKey)) return;
    // RESIDUAL gate (feedback P1): fire when the SERVER reports aligned workspace
    // experiments whose run_id isn't in this chip's index yet — even 1-4 of them. The
    // old "aligned-count − experiment-snapshot-count ≥ 5" heuristic silently skipped a
    // small batch (the user's complaint). The per-tab session guard above prevents a
    // re-loop; the backfill content-hash-dedups, so a stale residual is a harmless no-op.
    var pending = parseInt(root.getAttribute('data-pending-import-count') || '0', 10);
    if (pending <= 0) return;
    paramHistoryBackfill(false);
}

// Listen on `document` (not document.body): this script loads in <head>,
// before <body> exists. HTMX events bubble to document either way.
document.addEventListener('htmx:afterSwap', function(evt) {
    if (evt.target && (evt.target.id === 'param-history-root'
                       || (evt.target.querySelector && evt.target.querySelector('#param-history-root')))) {
        renderParamHistorySparklines();
        // Subsequent visits to a chip the user has already imported once:
        // silently catch up on any new workspace experiments. CTA card
        // handles the very first visit.
        paramHistoryMaybeAutoBackfill();
    }
});
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
        renderParamHistorySparklines();
        paramHistoryMaybeAutoBackfill();
    });
} else {
    renderParamHistorySparklines();
    paramHistoryMaybeAutoBackfill();
}

/* ──────────────────────────────────────────────────────────────────
 * Slow-route loader (the "QUAM STATE MANAGER" letter-fill animation
 * defined in style.css and rendered by base.html). Shown after a
 * 200 ms grace period so fast requests stay invisible; hidden the
 * moment the request finishes (success or error). Currently scoped
 * to /param-history* — the only route slow enough on cold cache to
 * justify a loading indicator. Add prefixes to SLOW_PREFIXES if
 * other routes warrant the same treatment.
 * ────────────────────────────────────────────────────────────────── */
(function setupSlowRouteLoader() {
    var SLOW_PREFIXES = ['/param-history', '/datasets',
        // docs/103: measured slow-on-big-chips surfaces — /bulk is the
        // app's largest render (10 MB HTML on 21Q) and had NO indicator.
        '/bulk', '/diff', '/topology', '/autofit', '/compare-hub'];
    var SHOW_AFTER_MS = 200;

    function getLoader() { return document.getElementById('quam-loader'); }

    function isSlow(detail) {
        var path = (detail && detail.requestConfig && detail.requestConfig.path) || '';
        for (var i = 0; i < SLOW_PREFIXES.length; i++) {
            if (path.indexOf(SLOW_PREFIXES[i]) === 0) return true;
        }
        return false;
    }

    var timer = null;
    function show() {
        var el = getLoader();
        if (el) el.classList.add('visible');
    }
    function hide() {
        if (timer) { clearTimeout(timer); timer = null; }
        var el = getLoader();
        if (el) el.classList.remove('visible');
    }

    document.addEventListener('htmx:beforeRequest', function(evt) {
        if (!isSlow(evt.detail)) return;
        if (timer) clearTimeout(timer);
        timer = setTimeout(show, SHOW_AFTER_MS);
    });
    // afterRequest fires on success AND error, so it's the only listener
    // we strictly need. The error-specific events are belt-and-suspenders
    // in case a future HTMX version changes the contract.
    document.addEventListener('htmx:afterRequest', hide);
    document.addEventListener('htmx:responseError', hide);
    document.addEventListener('htmx:sendError', hide);
})();


/* ──────────────────────────────────────────────────────────────────
 * Topbar import status pill (doc 24 future-work item).
 *
 * The Param-History backfill runs on a background thread, so it survives
 * page navigation. Without this pill, a user who clicks Import then
 * navigates away loses all visibility of the job. The pill bridges that
 * gap: it polls /param-history/backfill/status on a slow interval, shows
 * up in the topbar whenever the server reports ``running``, and links
 * back to Param History. On done/error it flashes a brief terminal state
 * then auto-hides.
 *
 * Polling cadence is asymmetric on purpose: 30 s when the pill is hidden
 * (cheap idle check, recovers state after page reload), 1 s when running
 * (we want the counter to update in real time), and stop entirely once
 * we've shown the terminal state.
 * ────────────────────────────────────────────────────────────────── */
(function setupImportStatusPill() {
    var POLL_RUNNING_MS = 1000;
    var POLL_IDLE_MS = 30000;
    var TERMINAL_LINGER_MS = 4000;

    var pill = null;
    var label = null;
    var count = null;
    var timer = null;
    var lingerTimer = null;

    function getEls() {
        if (pill) return true;
        pill = document.getElementById('topbar-import-pill');
        if (!pill) return false;
        label = pill.querySelector('.import-pill-label');
        count = pill.querySelector('.import-pill-count');
        return true;
    }

    function show(stateClass, labelText, countText) {
        if (!getEls()) return;
        pill.hidden = false;
        pill.className = 'topbar-import-pill ' + (stateClass || '');
        if (label) label.textContent = labelText || '';
        if (count) count.textContent = countText || '';
    }

    function hide() {
        if (!getEls()) return;
        pill.hidden = true;
        pill.className = 'topbar-import-pill';
        if (count) count.textContent = '';
    }

    function schedule(delay) {
        if (timer) clearTimeout(timer);
        timer = setTimeout(poll, delay);
    }

    function poll() {
        fetch('/param-history/backfill/status')
            .then(function(r) { return r.json(); })
            .then(function(s) {
                var status = s && s.status;
                if (status === 'running') {
                    var total = s.total || 0;
                    var done = s.done || 0;
                    var pct = total > 0 ? Math.min(100, Math.round(done * 100 / total)) : 0;
                    show('running', 'Importing…', done + ' / ' + (total || '?')
                        + (total > 0 ? ' (' + pct + '%)' : ''));
                    schedule(POLL_RUNNING_MS);
                } else if (status === 'done') {
                    // Only flash the success state if we were previously
                    // showing the pill — avoids a "phantom done" flash on
                    // initial load when an old run finished hours ago.
                    if (!pill || pill.hidden) {
                        schedule(POLL_IDLE_MS);
                        return;
                    }
                    show('done', 'Import done', '(' + (s.ingested || 0) + ')');
                    if (lingerTimer) clearTimeout(lingerTimer);
                    lingerTimer = setTimeout(function() {
                        hide();
                        schedule(POLL_IDLE_MS);
                    }, TERMINAL_LINGER_MS);
                } else if (status === 'error') {
                    if (!pill || pill.hidden) {
                        schedule(POLL_IDLE_MS);
                        return;
                    }
                    show('error', 'Import failed', '');
                    if (lingerTimer) clearTimeout(lingerTimer);
                    lingerTimer = setTimeout(function() {
                        hide();
                        schedule(POLL_IDLE_MS);
                    }, TERMINAL_LINGER_MS);
                } else {
                    // idle — make sure the pill is hidden, schedule a slow recheck.
                    if (pill && !pill.hidden) hide();
                    schedule(POLL_IDLE_MS);
                }
            })
            .catch(function() {
                // Network blip — back off and try again on the idle cadence.
                schedule(POLL_IDLE_MS);
            });
    }

    // Wake on demand from paramHistoryBackfill so the first counter update
    // shows within ~1 s of the user clicking Import even if the idle poll
    // last fired 29 s ago.
    document.addEventListener('param-history:backfill-started', function() {
        if (timer) clearTimeout(timer);
        schedule(200);
    });

    // Initial poll: discover any in-flight backfill that was kicked off
    // before this page loaded (cross-navigation case).
    function start() {
        if (!getEls()) return;
        schedule(500);
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();


/* ──────────────────────────────────────────────────────────────────
 * Recent quam_state paths — dropdown next to the Load button
 * ────────────────────────────────────────────────────────────────── */
function toggleRecentPaths(btn) {
    var panel = document.getElementById('recents-dropdown');
    if (!panel) return;
    if (!panel.hidden) {
        panel.hidden = true;
        btn.setAttribute('aria-expanded', 'false');
        return;
    }
    fetch('/api/recent-paths')
        .then(function(r) { return r.json(); })
        .then(function(data) {
            var recents = (data && data.recents) || [];
            if (!recents.length) {
                panel.innerHTML = '<div class="recents-empty">No recent paths yet — click <strong>Load</strong> to add one.</div>';
            } else {
                panel.innerHTML = recents.map(function(p, i) {
                    var safe = p.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
                    return '<button type="button" class="recents-item" data-path="' + safe + '" title="' + safe + '">'
                         + (i === 0 ? '<span class="recents-current">●</span>' : '<span class="recents-dot"></span>')
                         + '<span class="recents-path">' + safe + '</span></button>';
                }).join('');
                Array.prototype.forEach.call(panel.querySelectorAll('.recents-item'), function(el) {
                    el.addEventListener('click', function() {
                        var path = el.getAttribute('data-path');
                        var input = document.getElementById('load-path-input');
                        var form = document.getElementById('load-form');
                        if (input && form) {
                            input.value = path;
                            panel.hidden = true;
                            btn.setAttribute('aria-expanded', 'false');
                            if (window.htmx) {
                                window.htmx.trigger(form, 'submit');
                            } else {
                                form.requestSubmit();
                            }
                        }
                    });
                });
            }
            panel.hidden = false;
            btn.setAttribute('aria-expanded', 'true');
        })
        .catch(function() {
            panel.innerHTML = '<div class="recents-empty">Could not load recent paths.</div>';
            panel.hidden = false;
        });
}

// Click outside the dropdown closes it
document.addEventListener('click', function(evt) {
    var panel = document.getElementById('recents-dropdown');
    if (!panel || panel.hidden) return;
    if (panel.contains(evt.target)) return;
    var btn = document.querySelector('.btn-recents');
    if (btn && btn.contains(evt.target)) return;
    panel.hidden = true;
    if (btn) btn.setAttribute('aria-expanded', 'false');
});

/* ====================================================================== */
/* Command palette (Ctrl+K / Cmd+K)                                       */
/* ====================================================================== */
(function() {
    var _cpEntries = null;     // [{type, label, sub, url}]
    var _cpFiltered = [];      // currently visible
    var _cpActiveIdx = 0;
    var _RECENTS_KEY = 'cmd_palette_recents';

    function _loadData() {
        if (_cpEntries) return _cpEntries;
        var script = document.getElementById('cmd-palette-data');
        if (!script) return [];
        var data;
        try { data = JSON.parse(script.textContent); } catch (e) { return []; }
        var entries = [];
        (data.pages || []).forEach(function(p) {
            entries.push({type: 'page', label: p.label, sub: p.url, url: p.url});
        });
        (data.qubits || []).forEach(function(q) {
            entries.push({type: 'qubit', label: q, sub: 'Qubit', url: '/qubit/' + encodeURIComponent(q)});
        });
        (data.pairs || []).forEach(function(p) {
            entries.push({type: 'pair', label: p, sub: 'Pair', url: '/pair/' + encodeURIComponent(p)});
        });
        _cpEntries = entries;
        return entries;
    }

    function _recents() {
        try { return JSON.parse(localStorage.getItem(_RECENTS_KEY) || '[]'); }
        catch (e) { return []; }
    }

    function _pushRecent(entry) {
        var list = _recents().filter(function(e) { return e.url !== entry.url; });
        list.unshift({type: entry.type, label: entry.label, sub: entry.sub, url: entry.url});
        try { localStorage.setItem(_RECENTS_KEY, JSON.stringify(list.slice(0, 10))); }
        catch (e) { /* localStorage full or disabled — silent */ }
    }

    function _matches(entry, q) {
        var hay = (entry.label + ' ' + (entry.sub || '')).toLowerCase();
        var needles = q.toLowerCase().split(/\s+/).filter(Boolean);
        return needles.every(function(n) { return hay.indexOf(n) !== -1; });
    }

    function _render(query) {
        var list = document.getElementById('cmd-palette-results');
        if (!list) return;
        var entries = _loadData();
        if (!query) {
            // Default view: recents + pages, capped at 12
            var recent = _recents();
            var seen = {};
            recent.forEach(function(r) { seen[r.url] = true; });
            _cpFiltered = recent.concat(entries.filter(function(e) { return e.type === 'page' && !seen[e.url]; })).slice(0, 12);
        } else {
            _cpFiltered = entries.filter(function(e) { return _matches(e, query); }).slice(0, 30);
        }
        _cpActiveIdx = 0;
        list.innerHTML = '';
        if (!_cpFiltered.length) {
            list.innerHTML = '<li class="cmd-palette-empty">No matches</li>';
            return;
        }
        _cpFiltered.forEach(function(entry, idx) {
            var li = document.createElement('li');
            li.className = 'cmd-palette-item' + (idx === 0 ? ' active' : '');
            li.setAttribute('role', 'option');
            li.setAttribute('data-idx', String(idx));
            li.innerHTML = '<span class="cmd-palette-type cmd-palette-type-' + entry.type + '">' + entry.type + '</span>' +
                           '<span class="cmd-palette-label">' + _escape(entry.label) + '</span>' +
                           '<span class="cmd-palette-sub">' + _escape(entry.sub || '') + '</span>';
            li.addEventListener('mouseenter', function() { _setActive(idx); });
            li.addEventListener('click', function() { _activate(entry); });
            list.appendChild(li);
        });
    }

    function _escape(s) {
        return String(s).replace(/[&<>"']/g, function(c) {
            return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
        });
    }

    function _setActive(idx) {
        var items = document.querySelectorAll('#cmd-palette-results .cmd-palette-item');
        items.forEach(function(it) { it.classList.remove('active'); });
        if (idx < 0 || idx >= items.length) return;
        _cpActiveIdx = idx;
        items[idx].classList.add('active');
        items[idx].scrollIntoView({block: 'nearest'});
    }

    function _activate(entry) {
        _pushRecent(entry);
        window.closeCmdPalette();
        if (entry.type === 'qubit' || entry.type === 'pair') {
            // Load into inspector pane via HTMX
            if (window.htmx) {
                htmx.ajax('GET', entry.url, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
            } else {
                window.location.href = entry.url;
            }
        } else {
            // Page navigation via HTMX so push-url works
            if (window.htmx) {
                htmx.ajax('GET', entry.url, {target: '#table-pane', swap: 'innerHTML', pushUrl: true});
            } else {
                window.location.href = entry.url;
            }
        }
    }

    window.openCmdPalette = function() {
        var pal = document.getElementById('cmd-palette');
        var input = document.getElementById('cmd-palette-input');
        if (!pal || !input) return;
        pal.hidden = false;
        input.value = '';
        _render('');
        // Tab-cycle within the palette + restore focus to the opener on close.
        // (Escape/Arrows/Enter are handled by the dedicated keydown handler below.)
        pal._releaseTrap = window.trapFocus(pal);
        // Focus on the next frame so the dialog is visible.
        requestAnimationFrame(function() { input.focus(); });
    };

    window.closeCmdPalette = function() {
        var pal = document.getElementById('cmd-palette');
        if (pal) {
            pal.hidden = true;
            if (pal._releaseTrap) { pal._releaseTrap(); pal._releaseTrap = null; }
        }
    };

    document.addEventListener('keydown', function(evt) {
        // Toggle palette on Ctrl+K / Cmd+K from anywhere. A TOGGLE, not a bare
        // open: re-opening while already up used to stack a second focus trap
        // over the first (leaked on close → every Tab in the app swallowed).
        if ((evt.ctrlKey || evt.metaKey) && (evt.key === 'k' || evt.key === 'K')) {
            evt.preventDefault();
            var palK = document.getElementById('cmd-palette');
            if (palK && !palK.hidden) window.closeCmdPalette();
            else window.openCmdPalette();
            return;
        }
        var pal = document.getElementById('cmd-palette');
        if (!pal || pal.hidden) return;
        if (evt.key === 'Escape') {
            window.closeCmdPalette();
            evt.preventDefault();
        } else if (evt.key === 'ArrowDown') {
            _setActive(Math.min(_cpActiveIdx + 1, _cpFiltered.length - 1));
            evt.preventDefault();
        } else if (evt.key === 'ArrowUp') {
            _setActive(Math.max(_cpActiveIdx - 1, 0));
            evt.preventDefault();
        } else if (evt.key === 'Enter') {
            if (_cpFiltered[_cpActiveIdx]) _activate(_cpFiltered[_cpActiveIdx]);
            evt.preventDefault();
        }
    });

    document.addEventListener('input', function(evt) {
        if (evt.target && evt.target.id === 'cmd-palette-input') {
            _render(evt.target.value.trim());
        }
    });

    // Reset cache after HTMX swaps so newly-loaded qubits/pairs are searchable.
    // Attach to document (not document.body) — app.js runs in <head> before
    // body exists, so any top-level document.body access throws and halts
    // the rest of the script. htmx:afterSwap bubbles to document.
    document.addEventListener('htmx:afterSwap', function() {
        _cpEntries = null;
    });
})();


/* ------------------------------------------------------------------ */
/* Chip Compare picker                                                 */
/* ------------------------------------------------------------------ */
/* The picker (_chip_compare_picker.html) renders selected chips as
   tags inside #chip-compare-form. Each tag carries a hidden input
   name="paths" so the form POST submits the full list to /chip-compare.
   These helpers wire up the workspace/recent dropdowns and the browse
   button into the same tag list, plus the Compare-button label/disabled
   state. No sessionStorage — the source of truth is the live DOM. */
(function() {
    function _form() { return document.getElementById("chip-compare-form"); }
    function _tags() { return document.getElementById("chip-compare-tags"); }

    function _currentPaths() {
        var form = _form();
        if (!form) return [];
        var out = [];
        var inputs = form.querySelectorAll('input[name="paths"]');
        for (var i = 0; i < inputs.length; i++) {
            if (inputs[i].value) out.push(inputs[i].value);
        }
        return out;
    }

    function _updateGoButton() {
        var btn = document.getElementById("chip-compare-go-btn");
        if (!btn) return;
        var n = _currentPaths().length;
        btn.disabled = (n < 2);
        btn.textContent = "Compare " + n + " chip" + (n === 1 ? "" : "s");
    }

    function _shortLabel(path) {
        // Display-only: split by the path's LEADING style — a POSIX folder
        // name containing "\" must not be chopped at it.
        var isWin = /^[A-Za-z]:/.test(path) || /^\\\\/.test(path);
        var parts = (isWin ? path.split(/[\\/]/) : path.split("/")).filter(Boolean);
        if (parts.length === 0) return path;
        var last = parts[parts.length - 1];
        // For ".../foo/quam_state", show the parent "foo" rather than "quam_state".
        if (last === "quam_state" && parts.length >= 2) return parts[parts.length - 2];
        return last;
    }

    window.addChipFromSelect = function(selEl, sourceKind) {
        var p = selEl.value;
        if (!p) return;
        var opt = selEl.options[selEl.selectedIndex];
        var label = (opt && opt.dataset.label) || _shortLabel(p);
        _addChip(p, label);
        selEl.value = "";  // reset so re-picking the same chip after removal works
    };

    window.addChipFromInput = function(inputEl) {
        var p = inputEl.value && inputEl.value.trim();
        if (!p) return;
        _addChip(p, _shortLabel(p));
        inputEl.value = "";
    };

    window.removeChipFromCompare = function(btn) {
        var tag = btn.closest(".chip-compare-tag");
        if (tag) tag.remove();
        _showEmptyHintIfNeeded();
        _updateGoButton();
    };

    function _showEmptyHintIfNeeded() {
        var tagsBox = _tags();
        if (!tagsBox) return;
        var has = tagsBox.querySelector(".chip-compare-tag");
        var existing = tagsBox.querySelector(".chip-compare-tags-empty");
        if (!has && !existing) {
            var hint = document.createElement("span");
            hint.className = "muted chip-compare-tags-empty";
            hint.textContent = "No chips selected — add 2 or more below.";
            tagsBox.appendChild(hint);
        } else if (has && existing) {
            existing.remove();
        }
    }

    function _addChip(path, label) {
        var tagsBox = _tags();
        if (!tagsBox) return;
        // Dedup by path.
        var existing = tagsBox.querySelectorAll('.chip-compare-tag');
        for (var i = 0; i < existing.length; i++) {
            if (existing[i].dataset.path === path) return;
        }
        var hint = tagsBox.querySelector(".chip-compare-tags-empty");
        if (hint) hint.remove();

        var tag = document.createElement("span");
        tag.className = "chip-compare-tag";
        tag.setAttribute("role", "listitem");
        tag.dataset.path = path;

        var lbl = document.createElement("span");
        lbl.className = "chip-compare-tag-label";
        lbl.textContent = label.length > 28 ? label.slice(0, 27) + "…" : label;
        lbl.title = path;

        var x = document.createElement("button");
        x.type = "button";
        x.className = "chip-compare-tag-x";
        x.title = "Remove";
        x.innerHTML = "&times;";
        x.onclick = function() { window.removeChipFromCompare(x); };

        var hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = "paths";
        hidden.value = path;

        tag.appendChild(lbl);
        tag.appendChild(x);
        tag.appendChild(hidden);
        tagsBox.appendChild(tag);
        _updateGoButton();
    }

    // After every HTMX swap, re-sync the Compare button label (the picker
    // may have just been re-rendered server-side with new tags).
    // Attach to document (not document.body) — app.js loads in <head>
    // with no defer, so any top-level document.body access throws and
    // halts the rest of the script. htmx:afterSwap bubbles to document.
    document.addEventListener("htmx:afterSwap", function() {
        if (document.getElementById("chip-compare-form")) _updateGoButton();
    });
})();

/* ------------------------------------------------------------------ */
/* Drag-drop preview + wiring compare + diagnostics jump              */
/*                                                                    */
/* Drag a quam_state folder onto Instrument Wiring to preview its     */
/* wiring (read-only, in-memory); drag a config.json onto Config      */
/* Viewer to preview it. We read file *contents* in the browser       */
/* (webkitGetAsEntry / FileReader) — drag-drop never yields a real    */
/* path — and POST them to /instrument/preview or /config/preview.    */
/* The diagnostics linter runs server-side so a dropped (possibly     */
/* broken) chip shows what's cracked immediately.                     */
/* ------------------------------------------------------------------ */
(function() {
    "use strict";

    window._wiringCompare = window._wiringCompare || [];
    window._dropMode = window._dropMode || "preview";   // "preview" | "compare"

    /* ---- small UI helpers ---- */
    function dropToast(msg) {
        var t = document.getElementById("drop-toast");
        if (!t) {
            t = document.createElement("div");
            t.id = "drop-toast";
            t.className = "drop-toast";
            document.body.appendChild(t);
        }
        t.textContent = msg;
        t.classList.add("active");
        clearTimeout(t._timer);
        t._timer = setTimeout(function() { t.classList.remove("active"); }, 3500);
    }

    var _overlay = null;
    function ensureOverlay() {
        if (_overlay) return _overlay;
        _overlay = document.createElement("div");
        _overlay.id = "drop-overlay";
        _overlay.className = "drop-overlay";
        _overlay.innerHTML = '<div class="drop-overlay-msg"></div>';
        document.body.appendChild(_overlay);
        return _overlay;
    }
    function overlayMsg(zone) {
        if (zone === "cmphub") return "Drop quam_state folder(s) to add them to the comparison basket";
        if (window._dropMode === "compare" && zone === "instrument")
            return "Drop another quam_state folder to add it to the comparison";
        if (zone === "config") return "Drop a config.json to preview it";
        if (zone === "instrument") return "Drop a quam_state folder to preview its wiring";
        return "Drop a quam_state folder on Instrument Wiring, or a config.json on Config Viewer";
    }
    function showOverlay(zone) {
        var o = ensureOverlay();
        o.querySelector(".drop-overlay-msg").textContent = overlayMsg(zone);
        o.classList.add("active");
    }
    function hideOverlay() { if (_overlay) _overlay.classList.remove("active"); }

    function currentZone() {
        // hub first — its page contains none of the other zones' markers
        if (document.getElementById("cmp-hub-root")) return "cmphub";
        if (document.querySelector('#instrument-diagram, [id^="cmp-diagram-"]')) return "instrument";
        if (document.querySelector(".config-browser, #config-status")) return "config";
        return null;
    }
    function isFileDrag(e) {
        var dt = e.dataTransfer;
        if (!dt || !dt.types) return false;
        for (var i = 0; i < dt.types.length; i++) if (dt.types[i] === "Files") return true;
        return false;
    }

    /* ---- async file/folder reading (no real paths needed) ---- */
    function readDirEntries(dirEntry) {
        return new Promise(function(resolve, reject) {
            var reader = dirEntry.createReader(), all = [];
            (function batch() {
                reader.readEntries(function(items) {
                    if (!items.length) { resolve(all); return; }
                    all = all.concat(Array.prototype.slice.call(items));
                    batch();
                }, reject);
            })();
        });
    }
    function fileFromEntry(fileEntry) {
        return new Promise(function(resolve, reject) { fileEntry.file(resolve, reject); });
    }
    function readText(file) {
        return new Promise(function(resolve, reject) {
            var fr = new FileReader();
            fr.onload = function() { resolve(fr.result); };
            fr.onerror = function() { reject(new Error("Failed to read " + file.name)); };
            fr.readAsText(file);
        });
    }
    function parseJson(text, name) {
        try { return JSON.parse(text); }
        catch (e) { throw new Error(name + " is not valid JSON"); }
    }
    function readFolderEntry(dirEntry) {
        return readDirEntries(dirEntry).then(function(children) {
            var files = {}, dirs = {};
            children.forEach(function(c) {
                if (c.isFile) files[c.name] = c; else if (c.isDirectory) dirs[c.name] = c;
            });
            if (files["state.json"] && files["wiring.json"]) {
                return Promise.all([
                    fileFromEntry(files["state.json"]).then(readText),
                    fileFromEntry(files["wiring.json"]).then(readText)
                ]).then(function(txt) {
                    return {
                        state: parseJson(txt[0], "state.json"),
                        wiring: parseJson(txt[1], "wiring.json"),
                        label: dirEntry.name
                    };
                });
            }
            if (dirs["quam_state"]) {
                return readFolderEntry(dirs["quam_state"]).then(function(res) {
                    res.label = dirEntry.name; return res;
                });
            }
            throw new Error('No state.json + wiring.json in "' + dirEntry.name + '"');
        });
    }
    function readLooseQuam(fileEntries, looseFiles) {
        var byName = {};
        fileEntries.forEach(function(fe) {
            byName[fe.name] = function() { return fileFromEntry(fe).then(readText); };
        });
        looseFiles.forEach(function(f) {
            if (!byName[f.name]) byName[f.name] = function() { return readText(f); };
        });
        if (byName["state.json"] && byName["wiring.json"]) {
            return Promise.all([byName["state.json"](), byName["wiring.json"]()]).then(function(txt) {
                return {
                    state: parseJson(txt[0], "state.json"),
                    wiring: parseJson(txt[1], "wiring.json"),
                    label: "dropped files"
                };
            });
        }
        return Promise.reject(new Error("Drop a folder containing state.json and wiring.json"));
    }

    /* ---- POST + swap (re-executes the fragment's inline <script>) ---- */
    function swapPane(html) {
        var pane = document.getElementById("table-pane");
        if (!pane) return;
        pane.innerHTML = html;
        var scripts = pane.querySelectorAll("script");
        for (var i = 0; i < scripts.length; i++) {
            var old = scripts[i], s = document.createElement("script");
            if (old.src) s.src = old.src; else s.textContent = old.textContent;
            old.parentNode.replaceChild(s, old);
        }
        if (window.htmx && htmx.process) htmx.process(pane);
    }
    function postPreview(url, body) {
        fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json", "HX-Request": "true" },
            body: JSON.stringify(body)
        }).then(function(r) { return r.text(); })
          .then(function(html) { swapPane(html); })
          .catch(function() { dropToast("Preview request failed"); });
    }

    /* ---- preview / compare orchestration ---- */
    function renderPreview(chip) {
        window._lastDropped = chip;
        postPreview("/instrument/preview", { state: chip.state, wiring: chip.wiring, label: chip.label });
    }
    function addChipToCompare(chip) {
        if (window._wiringCompare.length >= 3) { dropToast("Maximum 3 chips in a comparison"); return; }
        window._wiringCompare.push(chip);
        if (window._wiringCompare.length < 2) { dropToast("Drop one more folder to compare"); return; }
        postPreview("/instrument/compare", { chips: window._wiringCompare });
    }
    window.addPreviewToCompare = function() {
        if (!window._lastDropped) { dropToast("Drop a chip first, then Compare"); return; }
        window._dropMode = "compare";
        window._wiringCompare = [window._lastDropped];
        dropToast("Comparing 1 chip — drop another quam_state folder to add it (max 3)");
    };
    window._clearWiringCompare = function() {
        window._wiringCompare = [];
        window._dropMode = "preview";
    };

    function handleFolderDrop(dirEntries, fileEntries, looseFiles) {
        var p = dirEntries.length ? readFolderEntry(dirEntries[0])
                                  : readLooseQuam(fileEntries, looseFiles);
        p.then(function(chip) {
            if (window._dropMode === "compare") addChipToCompare(chip);
            else renderPreview(chip);
        }).catch(function(err) { dropToast(err.message || "Could not read the dropped folder"); });
    }
    // ── Compare-hub drop zone: stash each dropped folder server-side, then
    // add its drop: token to the basket (docs/49 zone A — multi-folder drops
    // add each; per-folder failures toast and the rest continue). Sequential
    // on purpose: cmpHub.add() re-reads location.search, and reload() pushes
    // the canonical URL BEFORE its in-flight gate (pinned by
    // tests/compare_hub_selfcheck.cjs), so chained adds accumulate even
    // while the pane re-render is still in flight.
    function stashForHub(payload) {
        return fetch("/compare-hub/stash", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        }).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (d) {
                if (!resp.ok || !d || !d.ref) {
                    throw new Error((d && d.error) || ("stash failed (" + resp.status + ")"));
                }
                if (window.cmpHub) window.cmpHub.add(d.ref);
            });
        });
    }
    function handleHubDrop(dirEntries, fileEntries, looseFiles) {
        if (!dirEntries.length) {
            readLooseQuam(fileEntries, looseFiles)
                .then(stashForHub)
                .catch(function (err) { dropToast(err.message); });
            return;
        }
        var chain = Promise.resolve();
        dirEntries.forEach(function (entry) {
            chain = chain.then(function () {
                return readFolderEntry(entry).then(stashForHub);
            }).catch(function (err) {
                dropToast(entry.name + ": " + err.message);
            });
        });
    }

    function handleConfigDrop(fileEntries, looseFiles) {
        var getters = [];
        fileEntries.forEach(function(fe) {
            getters.push({ name: fe.name, get: function() { return fileFromEntry(fe).then(readText); } });
        });
        looseFiles.forEach(function(f) {
            getters.push({ name: f.name, get: function() { return readText(f); } });
        });
        if (!getters.length) { dropToast("Drop a config.json file here"); return; }
        getters.sort(function(a, b) {
            return (a.name.toLowerCase() === "config.json" ? 0 : 1)
                 - (b.name.toLowerCase() === "config.json" ? 0 : 1);
        });
        var pick = getters[0];
        if (!/\.json$/i.test(pick.name)) { dropToast("Drop a JSON config file"); return; }
        pick.get().then(function(text) {
            postPreview("/config/preview", { config: parseJson(text, pick.name), label: pick.name });
        }).catch(function(err) { dropToast(err.message || "Could not read the config"); });
    }

    function onDrop(e) {
        if (!isFileDrag(e)) return;
        e.preventDefault();              // stop WebView2/Chromium from navigating to the file
        _dragDepth = 0; hideOverlay();
        var zone = currentZone();
        var dt = e.dataTransfer, dirEntries = [], fileEntries = [], looseFiles = [];
        if (dt.items && dt.items.length) {
            for (var i = 0; i < dt.items.length; i++) {
                var it = dt.items[i];
                var en = it.webkitGetAsEntry ? it.webkitGetAsEntry() : null;
                if (en) { if (en.isDirectory) dirEntries.push(en); else fileEntries.push(en); }
                else if (it.kind === "file" && it.getAsFile) { var f = it.getAsFile(); if (f) looseFiles.push(f); }
            }
        } else if (dt.files) {
            for (var j = 0; j < dt.files.length; j++) looseFiles.push(dt.files[j]);
        }
        if (!zone) {
            dropToast("Open Compare, Instrument Wiring (for a folder) or Config Viewer (for config.json), then drop");
            return;
        }
        if (zone === "cmphub") handleHubDrop(dirEntries, fileEntries, looseFiles);
        else if (zone === "config") handleConfigDrop(fileEntries, looseFiles);
        else handleFolderDrop(dirEntries, fileEntries, looseFiles);
    }

    /* ---- document-level listeners (survive every HTMX swap) ---- */
    var _dragDepth = 0;
    document.addEventListener("dragenter", function(e) {
        if (!isFileDrag(e)) return;
        e.preventDefault();
        _dragDepth++;
        showOverlay(currentZone());
    });
    document.addEventListener("dragover", function(e) {
        if (!isFileDrag(e)) return;
        e.preventDefault();           // required for 'drop' to fire + stops navigation
        try { e.dataTransfer.dropEffect = "copy"; } catch (_) {}
    });
    document.addEventListener("dragleave", function(e) {
        if (!isFileDrag(e)) return;
        _dragDepth--;
        if (_dragDepth <= 0) { _dragDepth = 0; hideOverlay(); }
    });
    document.addEventListener("drop", onDrop);

    /* ---- diagnostics: jump to the offending field in the Explorer ---- */
    window.goToDiagField = function(btn) {
        var p = btn && btn.getAttribute("data-jump-path");
        if (p && window._navigateToExplorerPath) window._navigateToExplorerPath(p);
    };
    window.applyDiagFix = function(btn) {
        if (!btn) return;
        // For the value-DIFFERS (warning) case the convert relinks the input so its
        // value tracks the paired upconverter — i.e. it CHANGES the number. Confirm
        // first so the customer isn't surprised by a later config diff. (The equal
        // case is info-severity → no data-confirm → one-click, nothing changes.)
        if (btn.getAttribute("data-confirm") === "1") {
            // r9: server-rendered confirm text when present (set_value fixes
            // carry their own wording); the downconverter relink keeps its
            // historical message.
            var confirmText = btn.getAttribute("data-confirm-text");
            if (!confirmText) {
                var oldv = btn.getAttribute("data-old") || "the current literal";
                confirmText = "Relink downconverter_frequency to its paired upconverter?\n\n" +
                    "Its value (" + oldv + ") will change to track the shared LO. The change is added " +
                    "to your pending edits — review it in the tray before applying to the live chip.";
            }
            if (!window.confirm(confirmText)) {
                return;
            }
        }
        var body = new URLSearchParams();
        body.append("action", btn.getAttribute("data-action") || "");
        body.append("dot_path", btn.getAttribute("data-dot-path") || "");
        body.append("pointer", btn.getAttribute("data-pointer") || "");
        body.append("value", btn.getAttribute("data-value") || "");
        var orig = btn.textContent;
        btn.disabled = true; btn.textContent = "Applying…";
        fetch("/diagnostics/apply-fix", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: body.toString()
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d && d.ok) {
                // the edit is a pending working-copy change → refresh the tray,
                // then re-render diagnostics (the finding is now resolved).
                if (d.tray_html && window._swapPendingTray) {
                    window._swapPendingTray(d.tray_html);
                    if (window._restoreTrayState) window._restoreTrayState();
                }
                if (window.htmx) htmx.ajax("GET", "/diagnostics", { target: "#table-pane", swap: "innerHTML" });
                if (window._refreshSidebarDiagDots) window._refreshSidebarDiagDots();
            } else {
                btn.disabled = false; btn.textContent = orig;
                alert((d && d.error) || "Convert failed");
            }
        })
        .catch(function() { btn.disabled = false; btn.textContent = orig; alert("Convert request failed"); });
    };
    window.togglePreviewIssues = function() {
        var el = document.getElementById("preview-issues");
        if (el) el.classList.toggle("hidden");
    };

    /* ---- ring the broken (diagnostics) / differing (compare) ports ---- */
    function cssEsc(v) {
        v = String(v);
        if (window.CSS && CSS.escape) return CSS.escape(v);
        return v.replace(/["\\]/g, "\\$&");
    }
    window._highlightInstrumentPorts = function(containerId, entries, cls) {
        if (!entries || !entries.length) return;
        var container = document.getElementById(containerId);
        if (!container) return;
        entries.forEach(function(e) {
            var io = e.io || (e.port_type && e.port_type.indexOf("input") >= 0 ? "in" : "out");
            var sel = '.iw-port[data-con="' + cssEsc(e.ctrl) + '"]'
                    + '[data-slot="' + cssEsc(e.fem) + '"]'
                    + '[data-port="' + cssEsc(e.port) + '"]';
            var cells = container.querySelectorAll(sel);
            for (var i = 0; i < cells.length; i++) {
                var cio = cells[i].getAttribute("data-io") || "";
                if (cio.indexOf(io) === 0) cells[i].classList.add(cls);
            }
        });
    };
})();

/* ──────────────────────────────────────────────────────────────────────────
 * Diagnostics surfacing — hardware value-spec + connectivity warnings on the
 * Explorer tree rows and the sidebar tab dots, driven by one JSON feed
 * (GET /diagnostics/findings.json). See core/spec_constraints + diagnostics.
 * The Instrument-Wiring diagram highlight lives in _instrument_wiring.html
 * (reuses window._highlightInstrumentPorts above).
 * ────────────────────────────────────────────────────────────────────────── */
(function() {
    // Materialize lazy nodes along a dot-path (like _expandTreeToPath, but no
    // scroll/popup) then mark the leaf row with a ⚠ + tooltip.
    function markTreePath(containerId, dotPath, message) {
        var container = document.getElementById(containerId);
        if (!container) return;
        var segments = dotPath.split('.');
        var currentPath = '';
        for (var i = 0; i < segments.length; i++) {
            currentPath = i === 0 ? segments[i] : currentPath + '.' + segments[i];
            var node = container.querySelector('.tree-node[data-path="' + currentPath + '"]');
            if (!node) break;
            var toggle = node.querySelector(':scope > .tree-row > .tree-toggle');
            if (toggle && toggle.classList.contains('collapsed')) toggle.click();
        }
        var target = container.querySelector('.tree-node[data-path="' + dotPath + '"]')
            || container.querySelector('.tree-node[data-path="' + segments.slice(0, -1).join('.') + '"]');
        if (!target) return;
        var row = target.querySelector(':scope > .tree-row');
        if (!row) return;
        row.classList.add('tree-row-warn');
        var ic = row.querySelector('.tree-warn-icon');
        if (!ic) {
            ic = document.createElement('span');
            ic.className = 'tree-warn-icon';
            ic.textContent = '⚠';
            ic.title = message || 'Hardware spec warning';
            ic.setAttribute('role', 'button');
            ic.setAttribute('tabindex', '0');
            // r14 ⑨: the mark used to be a bare tooltip span — clicking it now
            // reveals + highlights the finding's row (same treatment the
            // Diagnostics "Go to field" navigation applies), so a spotted red
            // mark is one click from the exact field.
            ic.addEventListener('click', function (e) {
                e.stopPropagation();
                if (window._navigateToExplorerPath) {
                    window._navigateToExplorerPath(dotPath);
                }
            });
            row.appendChild(ic);
        } else if (message && ic.title.indexOf(message) === -1) {
            ic.title += '\n' + message;
        }
    }
    window._markTreePath = markTreePath;

    function clearExplorerMarks() {
        var ids = ['explorer-tree-state', 'explorer-tree-wiring'];
        for (var k = 0; k < ids.length; k++) {
            var c = document.getElementById(ids[k]);
            if (!c) continue;
            var rows = c.querySelectorAll('.tree-row-warn');
            for (var i = 0; i < rows.length; i++) rows[i].classList.remove('tree-row-warn');
            var ics = c.querySelectorAll('.tree-warn-icon');
            for (var j = 0; j < ics.length; j++) ics[j].remove();
        }
    }

    /* ================================================================== */
    /* Explorer "Live diff" — Qualibrate before → after, accept per field  */
    /* ================================================================== */
    /* Compares the SM working copy (the Explorer trees) against Qualibrate's
       live state, inline in the tree (VS Code "compare" style). Each changed
       leaf shows "working → live" with ✓ accept / ✗ keep. Accept routes through
       /field/edit-batch (raw JSON value — no string round-trip) so the value
       lands as a pending working-copy edit; the usual "Apply to live" then
       writes it. GATED by the workbench path-match: only meaningful when SM and
       Qualibrate share the chip (a mismatch shows zero changes). */
    /* Diff-mode truth is the DOM, DERIVED — never a shadow variable
       (docs/124 M-4/M-5). The old closure flag survived every pane swap while
       the toggle's class did not (_explorer.html always renders it inactive),
       so any fresh render of /explorer with diff mode on produced flag=true /
       DOM=inactive — the next click computed !true and ran the OFF branch: a
       silent dead first click, reachable by three ordinary daily sequences
       (grid edit → PaneState seq-mismatch → back; a held /state/live-diff
       response committing against a parked pane; stateRestored's soft
       refresh). And the zero-pairs no-op flipped only the flag, leaving a
       stuck-lit toggle whose own button could never turn it off while it
       toasted "No incoming changes" against a real server divergence. A pane
       replacement drops the overlay WITH the DOM, so deriving from the DOM is
       not merely consistent — it is the true state. */
    function _explorerLiveDiffOn() {
        var t = document.getElementById("explorer-livediff-toggle");
        return !!(t && t.classList.contains("active"));
    }
    function _setLiveDiffUi(on, remaining) {
        var t = document.getElementById("explorer-livediff-toggle");
        if (t) t.classList.toggle("active", !!on);
        var bar = document.getElementById("explorer-livediff-bar");
        if (bar) bar.hidden = !on;
        if (on) {
            var cnt = document.getElementById("livediff-bar-count");
            if (cnt) cnt.textContent = remaining;
        }
    }
    var _liveDiffState = [];   // [{dot_path, value(live)}] for state.json tree
    var _liveDiffWiring = [];  // ... for wiring.json tree
    var _liveDiffDone = {};    // dot_path -> 1 once accepted/rejected this session
    var _liveDiffRemaining = 0;

    // Scope-local deep equality. This IIFE had NO _deepEqual in scope — the two
    // definitions live inside the tree-renderer IIFEs — so _collectDiffPairs
    // threw ReferenceError on every live-diff toggle, caught by the recover
    // handler as a permanent "Could not render the live diff." (latent since
    // the first commit; exposed by explorer_paths_selfcheck.cjs).
    function _deepEqual(a, b) {
        if (a === b) return true;
        if (a === null || b === null) return false;
        if (typeof a !== typeof b) return false;
        if (typeof a !== "object") return false;
        var isArrA = Array.isArray(a), isArrB = Array.isArray(b);
        if (isArrA !== isArrB) return false;
        if (isArrA) {
            if (a.length !== b.length) return false;
            for (var i = 0; i < a.length; i++) {
                if (!_deepEqual(a[i], b[i])) return false;
            }
            return true;
        }
        var keysA = Object.keys(a), keysB = Object.keys(b);
        if (keysA.length !== keysB.length) return false;
        for (var k = 0; k < keysA.length; k++) {
            if (!(keysA[k] in b)) return false;
            if (!_deepEqual(a[keysA[k]], b[keysA[k]])) return false;
        }
        return true;
    }

    // Walk working `val` vs live `ref`, collecting the dot-path + live value of
    // every differing leaf (or a whole added/removed/type-changed node).
    function _collectDiffPairs(val, ref, base, out) {
        if (_deepEqual(val, ref)) return;
        var vObj = val && typeof val === "object";
        var rObj = ref && typeof ref === "object";
        if (vObj && rObj && Array.isArray(val) === Array.isArray(ref)) {
            if (Array.isArray(val)) {
                if (val.length !== ref.length) {
                    // Length change = structural: one whole-array entry (per-element
                    // accepts would need create/delete-on-list semantics).
                    out.push({ dot_path: base, value: ref });
                    return;
                }
                // Equal lengths: per-element dot-form entries (a.b.3) — directly
                // acceptable through /field/edit-batch's element grammar.
                for (var i = 0; i < val.length; i++) _collectDiffPairs(val[i], ref[i], base + "." + i, out);
            } else {
                var seen = {}, k;
                for (k in val) if (Object.prototype.hasOwnProperty.call(val, k)) seen[k] = 1;
                for (k in ref) if (Object.prototype.hasOwnProperty.call(ref, k)) seen[k] = 1;
                for (k in seen) {
                    _collectDiffPairs(val[k], ref[k], base ? base + "." + k : k, out);
                }
            }
        } else {
            out.push({ dot_path: base, value: ref });
        }
    }

    // "a.b.2.c" -> ["a","a.b","a.b.2","a.b.2.c"] (each ancestor's data-path).
    // Paths are pure dot-form now (list elements use numeric segments), so a
    // plain split accumulation is exact.
    function _ancestorPaths(dotPath) {
        var parts = dotPath.split(".");
        var out = [], cur = "";
        for (var i = 0; i < parts.length; i++) {
            cur = cur ? cur + "." + parts[i] : parts[i];
            out.push(cur);
        }
        return out;
    }

    // Expand every collapsed ancestor along a path (materialises lazy children
    // so the changed leaf node — with its incoming markers — exists in the DOM).
    function _expandToPath(container, ancestors) {
        for (var i = 0; i < ancestors.length; i++) {
            var node = container.querySelector('.tree-node[data-path="' + ancestors[i] + '"]');
            if (!node) break;
            var toggle = node.querySelector(':scope > .tree-row > .tree-toggle');
            if (toggle && toggle.classList.contains('collapsed')) toggle.click();
        }
    }

    function _autoExpandAndTag(containerId, pairs) {
        var container = document.getElementById(containerId);
        if (!container) return;
        var counts = {};
        for (var i = 0; i < pairs.length; i++) {
            var ancestors = _ancestorPaths(pairs[i].dot_path);
            _expandToPath(container, ancestors);
            for (var j = 0; j < ancestors.length - 1; j++) {
                counts[ancestors[j]] = (counts[ancestors[j]] || 0) + 1;
            }
        }
        Object.keys(counts).forEach(function(p) {
            var node = container.querySelector('.tree-node[data-path="' + p + '"]');
            if (!node) return;
            node.classList.add("tree-has-diff");
            var row = node.querySelector(":scope > .tree-row");
            if (!row) return;
            var pill = row.querySelector(":scope > .tree-rollup-pill");
            if (!pill) {
                pill = document.createElement("span");
                pill.className = "tree-rollup-pill";
                row.appendChild(pill);
            }
            pill.textContent = counts[p] + " changed";
        });
    }

    // Strip the incoming markers from a row (after accept or reject).
    function _clearIncoming(row) {
        if (!row) return;
        row.classList.remove("tree-row-incoming");
        var bits = row.querySelectorAll(
            ".tree-incoming-arrow, .tree-incoming-val, .tree-accept-btn, .tree-reject-btn, .tree-delta");
        for (var i = 0; i < bits.length; i++) {
            if (bits[i].parentNode) bits[i].parentNode.removeChild(bits[i]);
        }
        var node = row.parentNode;
        if (node && node.classList) node.classList.remove("tree-diff");
    }

    function _bumpLiveDiffCount(delta) {
        _liveDiffRemaining = Math.max(0, _liveDiffRemaining + delta);
        var cnt = document.getElementById("livediff-bar-count");
        if (cnt) cnt.textContent = _liveDiffRemaining;
    }

    // ✓ — accept Qualibrate's live value into the working copy as a pending edit.
    function _acceptLiveValue(dotPath, liveValue, valEl, row) {
        // Defensive-parse + bounded retry (feedback #5): a burst at click time no
        // longer dead-ends in "Accept failed (network error)"; a transient retries.
        _liveFetchJson("/field/edit-batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ updates: [{ dot_path: dotPath, value: liveValue }] })
        }).then(function (res) {
            var d = res.data;
            if (!res.ok || !d) {
                var msg = (d && d.results && d.results[0] && d.results[0].error) ||
                          (d && d.error) || (res.transient ? "live chip busy" : "edit rejected");
                window.showToast("Could not accept " + dotPath + ": " + msg
                    + (res.transient ? " — try again" : ""), "warning");
                return;
            }
            valEl.textContent = window._formatValue(liveValue);
            valEl.dataset.editVal = (typeof liveValue === "string") ? liveValue : window._formatValue(liveValue);
            _clearIncoming(row);
            row.classList.add("tree-row-pending");
            _liveDiffDone[dotPath] = 1;
            if (d.tray_html) {
                _swapPendingTray(d.tray_html);
                window._restoreTrayState && window._restoreTrayState();
            }
            _bumpLiveDiffCount(-1);
        });
    }

    // ✗ — keep the working-copy value; just drop the incoming markers.
    function _rejectLiveValue(row, dotPath) {
        _clearIncoming(row);
        if (dotPath) _liveDiffDone[dotPath] = 1;
        _bumpLiveDiffCount(-1);
    }

    // Test hooks (jsdom selfchecks pin the dot-form path grammar through these).
    window._collectDiffPairs = _collectDiffPairs;
    window._ancestorPaths = _ancestorPaths;
    // NOT test hooks: the tree renderer (a different IIFE) wires the per-row
    // ✓/✗ buttons to these. They close over this IIFE's state (_liveDiffDone,
    // the remaining-count, _liveFetchJson), so unlike _deepEqual above they
    // cannot be copied into the caller's scope — they must be exported. Bare
    // cross-IIFE calls threw ReferenceError on every click and the accept was
    // silently LOST while the user believed it staged (docs/124 C-1).
    window._acceptLiveValue = _acceptLiveValue;
    window._rejectLiveValue = _rejectLiveValue;

    // The tree's own inline value-editor (_makeValueEditable, a different scope)
    // calls this after the user types a new value into a field that is part of
    // the incoming live diff. Treat it like a per-row accept of the user's value:
    // drop the incoming markers and remove the path from the Accept-All set, so a
    // later "Accept all" can't replay the stale LIVE value over the value the user
    // just typed (field/edit-batch is last-write-wins per path). Idempotent.
    window._explorerNoteInlineEdit = function (dotPath, row) {
        if (!_explorerLiveDiffOn() || !dotPath || _liveDiffDone[dotPath]) return;
        if (row) _clearIncoming(row);
        _liveDiffDone[dotPath] = 1;
        _bumpLiveDiffCount(-1);
    };

    // Toggle the Explorer's live-diff overlay. on=undefined flips current state.
    // Robust JSON fetch for the live-sync surfaces (feedback #5): defensively PARSE
    // (r.text → try JSON.parse, so a non-JSON Werkzeug HTML 500 can NEVER make
    // r.json() throw the dreaded "network error") and RETRY a transient failure
    // (503 / {transient:true} / a real network drop) with bounded backoff before
    // surfacing anything — self-healing like the drift poll. A QUAlibrate write-burst
    // at click time is momentary, so the next try almost always wins. Resolves to
    // {ok, status, data, transient}; never rejects.
    function _ldDelay(ms) { return new Promise(function (res) { setTimeout(res, ms); }); }
    function _liveFetchJson(url, opts, tries) {
        opts = opts || {};
        tries = tries || 3;
        var delays = [500, 1000, 2000];
        var headers = Object.assign({ "X-Requested-With": "XMLHttpRequest" }, opts.headers || {});
        var fetchOpts = Object.assign({ cache: "no-store" }, opts, { headers: headers });
        function attempt(i) {
            return fetch(url, fetchOpts).then(function (r) {
                return r.text().then(function (text) {
                    var data = null;
                    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
                    var failed = !r.ok || (data && data.ok === false);
                    var transient = r.status === 503 || !!(data && data.transient);
                    if (failed && transient && i + 1 < tries)
                        return _ldDelay(delays[i] || 2000).then(function () { return attempt(i + 1); });
                    return { ok: !failed, status: r.status, data: data, transient: transient };
                });
            }).catch(function () {
                if (i + 1 < tries) return _ldDelay(delays[i] || 2000).then(function () { return attempt(i + 1); });
                return { ok: false, status: 0, data: null, transient: true };
            });
        }
        return attempt(0);
    }
    window._liveFetchJson = _liveFetchJson;

    // Non-fatal recovery for a persistently-failing live read: NEVER a dead red toast.
    // The auto-retries already failed (live genuinely busy / error), so leave the
    // toggle OFF (known state, not stuck half-on) and tell the user the Live-diff
    // button itself retries (re-invokes this on click) — the discoverable recourse.
    function _liveDiffRecover(msg) {
        _explorerLiveDiffOn = false;
        var t = document.getElementById("explorer-livediff-toggle");
        if (t) t.classList.remove("active");
        var bar = document.getElementById("explorer-livediff-bar");
        if (bar) bar.hidden = true;
        window.showToast(msg + " Click ⇄ Live diff again to retry.", "warning");
    }

    /* docs/122 item 2 — a re-rendered tree must never leave the search box
       describing rows that are no longer filtered.

       renderJsonTree deliberately clears `_lastSearchQuery` (it wiped
       innerHTML), so every caller owns the re-apply — and explorerLiveDiff was
       the one that did not. Measured on the real 20-qubit chip: with
       `amplitude` in the box, turning live diff ON took the tree to 189 visible
       rows of which 189 did NOT match the query, the box still reading
       `amplitude`; re-typing the same value restored the filter WITHOUT leaving
       diff mode, which is what proved the search was fine and simply never
       called. On /workbench this is not even a click: a 3 s poll turns diff on
       by itself on every qualibrate write (workbench.html:512 ->
       showLiveDiffInline), so the search died unattended. */
    function _explorerReapplySearch() {
        var box = document.getElementById("explorer-search");
        var q = box && box.value ? box.value : "";
        if (!q || !window.jsonTreeSearch || !window._activeTreeId) return false;
        window.jsonTreeSearch(window._activeTreeId(), q);
        return true;
    }

    /* Report, never silently hide. A filter applied over a diff can exclude the
       very rows the diff is announcing, and a bar that says "changed 3 fields"
       above an empty tree reads as "qualibrate changed nothing here". Runs
       after jsonTreeSearch's own 200 ms debounce has settled. */
    function _explorerDiffFilterNote() {
        setTimeout(function () {
            var note = document.getElementById("livediff-bar-filtered");
            if (!note) return;
            var bar = document.getElementById("explorer-livediff-bar");
            var box = document.getElementById("explorer-search");
            if (!bar || bar.hidden || !box || !box.value) { note.hidden = true; return; }
            var el = document.getElementById(window._activeTreeId());
            if (!el) { note.hidden = true; return; }
            var rows = el.querySelectorAll(".tree-row-incoming");
            var hidden = 0;
            Array.prototype.forEach.call(rows, function (r) {
                if (r.offsetParent === null) hidden++;
            });
            if (!hidden) { note.hidden = true; return; }
            note.textContent = " — " + hidden + " of them " +
                (hidden === 1 ? "is" : "are") + " hidden by your search";
            note.hidden = false;
        }, 350);
    }
    /* The search box's single entry point: filter, then keep the diff bar's
       claim honest about what the filter left on screen. */
    window.explorerSearch = function (value) {
        if (window.jsonTreeSearch && window._activeTreeId) {
            window.jsonTreeSearch(window._activeTreeId(), value);
        }
        _explorerDiffFilterNote();
    };

    window.explorerLiveDiff = function(on) {
        var stateEl = document.getElementById("explorer-tree-state");
        var wiringEl = document.getElementById("explorer-tree-wiring");
        if (!stateEl || !wiringEl) return;
        if (on === undefined) on = !_explorerLiveDiffOn();

        if (!on) {
            _setLiveDiffUi(false);
            _liveDiffState = []; _liveDiffWiring = []; _liveDiffDone = {}; _liveDiffRemaining = 0;
            // Reload the explorer fresh: drops refData AND reflects any accepted
            // edits (the client tree data went stale as we accepted them).
            if (window._softRefreshLiveSurface) window._softRefreshLiveSurface();
            return;
        }

        _liveFetchJson("/state/live-diff?with_live=1").then(function (res) {
            if (!res.ok) {
                _liveDiffRecover(res.transient
                    ? "Live chip is being written — couldn't read it just now."
                    : ((res.data && res.data.error) || "Could not read the live state."));
                return;
            }
            var d = res.data || {};
            try {
                var sData = stateEl._treeData, wData = wiringEl._treeData;
                var liveState = d.live_state || {}, liveWiring = d.live_wiring || {};
                _liveDiffState = []; _collectDiffPairs(sData, liveState, "", _liveDiffState);
                _liveDiffWiring = []; _collectDiffPairs(wData, liveWiring, "", _liveDiffWiring);
                _liveDiffDone = {};
                _liveDiffRemaining = _liveDiffState.length + _liveDiffWiring.length;

                if (_liveDiffRemaining === 0) {
                    // BOTH halves off (docs/124 M-5): clearing only the flag
                    // left a lit toggle that lied and could not be turned off.
                    _setLiveDiffUi(false);
                    window.showToast(
                        "No incoming changes — the working state matches the live chip.", "info");
                    return;
                }

                renderJsonTree("explorer-tree-state", sData,
                    { defaultDepth: 1, refData: liveState, valueClick: "livediff" });
                renderJsonTree("explorer-tree-wiring", wData,
                    { defaultDepth: 1, refData: liveWiring, valueClick: "livediff" });
                _autoExpandAndTag("explorer-tree-state", _liveDiffState);
                _autoExpandAndTag("explorer-tree-wiring", _liveDiffWiring);
                // renderJsonTree wiped innerHTML — re-apply hardware-spec marks.
                if (window._applyExplorerSpecMarks) window._applyExplorerSpecMarks();
                // ...and the search, for the same reason (docs/122 item 2).
                // AFTER the tagging, so the incoming marks exist on the rows the
                // filter then judges — and so the count below describes what the
                // user can actually see.
                _explorerReapplySearch();
                _explorerDiffFilterNote();

                // Commit the ON state ATOMICALLY — only after the render fully
                // succeeded, so a render error never leaves a half-applied overlay
                // with a stuck-on toggle (the old code set on=true BEFORE rendering).
                _setLiveDiffUi(true, _liveDiffRemaining);
            } catch (err) {
                window.explorerLiveDiff(false);   // full clean reset — never a half overlay
                _liveDiffRecover("Could not render the live diff.");
            }
        });
    };

    // Accept every remaining incoming change in ONE request, applied per-row
    // (independent mode): one drifted/rejected value must not roll back the
    // hundreds of accepted ones.
    window.explorerAcceptAll = function() {
        var pairs = _liveDiffState.concat(_liveDiffWiring).filter(function(p) {
            return !_liveDiffDone[p.dot_path];
        });
        if (!pairs.length) { window.showToast("Nothing left to accept.", "info"); return; }
        var updates = pairs.map(function(p) { return { dot_path: p.dot_path, value: p.value }; });
        // Defensive-parse + bounded retry: a burst no longer dead-ends in an
        // ambiguous "network error".
        _liveFetchJson("/field/edit-batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ updates: updates, independent: true })
        }).then(function (res) {
            var d = res.data;
            if (!res.ok && !(d && d.results)) {
                window.showToast(res.transient
                    ? "Live chip is busy — nothing applied. Try Accept all again."
                    : "Accept all failed — nothing applied.", "warning");
                if (d && d.tray_html) {
                    _swapPendingTray(d.tray_html);
                    window._restoreTrayState && window._restoreTrayState();
                }
                return;
            }
            if (d.tray_html) {
                _swapPendingTray(d.tray_html);
                window._restoreTrayState && window._restoreTrayState();
            }
            var failed = (d.results || []).filter(function(r) { return !r.applied; });
            if (!failed.length) {
                window.showToast(
                    "Accepted " + updates.length + " value" + (updates.length === 1 ? "" : "s") +
                    " into the working state — review the tray, then Apply to live.", "success");
                window.explorerLiveDiff(false);  // exit diff (soft-refresh shows pending values)
                return;
            }
            var okCount = updates.length - failed.length;
            window.showToast(
                "Accepted " + okCount + " of " + updates.length + " — " + failed.length +
                " rejected (first: " + (failed[0].error || "edit rejected") +
                "). The remaining rows stay marked below.", "warning");
            // Re-render the overlay: applied rows vanish (working copy now matches
            // live there); rejected rows keep their incoming markers for per-row
            // handling.
            window.explorerLiveDiff(false);
            window.explorerLiveDiff(true);
        });
    };

    // Entry point the workbench nudge calls in the SM iframe: prefer the inline
    // Explorer diff; fall back to the flat review overlay on any other page.
    window.showLiveDiffInline = function() {
        if (document.getElementById("explorer-tree-state")) {
            window.explorerLiveDiff(true);
        } else if (window.openReview) {
            // Auto-opened by the workbench when Qualibrate touches the live
            // chip — dismiss after 8s unless the user hovers/clicks/focuses.
            window.openReview({ autoDismiss: 8000 });
        }
    };

    window._applyExplorerSpecMarks = function() {
        if (!document.getElementById('explorer-tree-state')) return;
        fetch('/diagnostics/findings.json', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(function(d) {
                clearExplorerMarks();
                var marks = (d.value_spec || []).concat(d.connectivity || []);
                for (var i = 0; i < marks.length; i++) {
                    var f = marks[i];
                    if (!f.jump_path) continue;
                    var cid = f.jump_path.indexOf('wiring.') === 0
                        ? 'explorer-tree-wiring' : 'explorer-tree-state';
                    markTreePath(cid, f.jump_path, f.message);
                }
            })
            .catch(function() {});
    };

    // Severity-aware sidebar dot: red iff a crash-class ERROR exists on that tab,
    // amber when only warnings/recommendations, none when clean — so a by-design
    // advisory (e.g. the band-edge nudge) doesn't light the sidebar red.
    function setNavDot(href, level) {  // level: 'error' | 'warn' | null
        var els = document.querySelectorAll('#sidebar a[href="' + href + '"]');
        for (var i = 0; i < els.length; i++) {
            els[i].classList.toggle('nav-diag-dot', level === 'error');
            els[i].classList.toggle('nav-diag-dot-warn', level === 'warn');
        }
    }
    function _maxLevel(arr) {
        var hasErr = false, has = false;
        (arr || []).forEach(function(f) { has = true; if (f.severity === 'error') hasErr = true; });
        return hasErr ? 'error' : (has ? 'warn' : null);
    }

    window._refreshSidebarDiagDots = function() {
        fetch('/diagnostics/findings.json', { cache: 'no-store' })
            .then(function(r) { return r.json(); })
            .then(function(d) {
                setNavDot('/explorer', _maxLevel(d.value_spec));
                setNavDot('/instrument', _maxLevel(d.connectivity));
            })
            .catch(function() {});
    };

    /* ---- Diagnostics filter pills (severity + advisory), persisted ---------- */
    /* Toggles row visibility on #diag-filter-bar pills, hides emptied domain
       sections, writes "X of Y shown", and persists to localStorage so the
       choice survives reloads + table-pane swaps (mirrors the inspector's
       filterDetailPanel muscle memory). Buckets: error/warning/advisory/info. */
    var _DIAG_BUCKETS = ['error', 'warning', 'advisory', 'info'];
    function _diagFilterState() {
        var s = {};
        try { s = JSON.parse(localStorage.getItem('quam_diag_filter') || '{}') || {}; } catch (e) { s = {}; }
        _DIAG_BUCKETS.forEach(function(b) { if (s[b] === undefined) s[b] = true; });
        return s;
    }
    function _applyDiagFilter() {
        var bar = document.getElementById('diag-filter-bar');
        if (!bar) return;
        var st = _diagFilterState();
        bar.querySelectorAll('.diag-pill').forEach(function(p) {
            var on = st[p.getAttribute('data-bucket')] !== false;
            p.classList.toggle('diag-pill-off', !on);
            p.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        var results = document.querySelector('.diag-results');
        var shown = 0, total = 0;
        if (results) {
            results.querySelectorAll('tr.diag-row').forEach(function(tr) {
                total++;
                var on = st[tr.getAttribute('data-bucket')] !== false;
                tr.style.display = on ? '' : 'none';
                if (on) shown++;
            });
            results.querySelectorAll('details.diag-domain').forEach(function(sec) {
                var any = false;
                sec.querySelectorAll('tr.diag-row').forEach(function(tr) {
                    if (tr.style.display !== 'none') any = true;
                });
                sec.style.display = any ? '' : 'none';
            });
        }
        var cnt = bar.querySelector('.diag-shown-count');
        if (cnt) cnt.textContent = (shown === total || total === 0) ? '' : (shown + ' of ' + total + ' shown');
    }
    window._applyDiagFilter = _applyDiagFilter;
    document.addEventListener('click', function(e) {
        var pill = e.target.closest ? e.target.closest('.diag-pill') : null;
        if (!pill || !pill.getAttribute('data-bucket')) return;
        var b = pill.getAttribute('data-bucket');
        var st = _diagFilterState();
        st[b] = !(st[b] !== false);            // flip current on/off
        try { localStorage.setItem('quam_diag_filter', JSON.stringify(st)); } catch (err) {}
        _applyDiagFilter();
    });

    // After any table-pane swap (load→/explorer, or sidebar nav): always refresh
    // the sidebar dots; mark Explorer rows when the Explorer is the swapped view;
    // re-apply the persisted diagnostics filter when the list is on screen.
    document.addEventListener('htmx:afterSwap', function(evt) {
        if (!evt.detail || !evt.detail.target || evt.detail.target.id !== 'table-pane') return;
        if (window._refreshSidebarDiagDots) window._refreshSidebarDiagDots();
        if (document.getElementById('explorer-tree-state') && window._applyExplorerSpecMarks) {
            window._applyExplorerSpecMarks();
        }
        _applyDiagFilter();
    });
    // Once on first full-page load so the dots + filter show immediately.
    function _diagInitOnLoad() {
        if (window._refreshSidebarDiagDots) window._refreshSidebarDiagDots();
        _applyDiagFilter();
        // r14 ⑨: a FULL-page load of /explorer never applied the row marks
        // (only the #table-pane afterSwap path did) — apply them on load too.
        if (window._applyExplorerSpecMarks &&
                document.getElementById('explorer-tree-state')) {
            window._applyExplorerSpecMarks();
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _diagInitOnLoad);
    } else {
        _diagInitOnLoad();
    }
})();

/* ------------------------------------------------------------------ */
/* Pulses page — channel-tab active state + live search preservation   */
/* ------------------------------------------------------------------ */

/* Switch the active class on pulse channel badges (client-side, instant). */
window.pulseTabActive = function (a) {
    var nav = document.getElementById("pulse-channel-tabs");
    if (!nav) return;
    nav.querySelectorAll("a").forEach(function (el) { el.classList.remove("active"); });
    a.classList.add("active");
    _pulsesSyncUrl();
};

/* Mirror the Pulses search + active channel into the browser URL (replaceState) so
 * the filter state lives in location.search, NOT only in the DOM. Without this, ANY
 * full re-fetch of /pulses (an apply that pulls, a conflict/discard/reapply, a page
 * reload, browser back/forward) re-renders the server's DEFAULT page and resets the
 * searched keyword + the pressed All/XY/Z/Resonator/Pair-flux badge. With the state
 * in the URL, the server re-renders the input value={{q}} + the active badge, so it
 * survives every path (the route already reads ?channel= / ?q=). */
function _pulsesSyncUrl() {
    if (location.pathname.indexOf("/pulses") !== 0) return;
    var inp = document.querySelector('.table-filter input[name="q"]');
    var q = inp ? inp.value.trim() : "";
    var tab = document.querySelector("#pulse-channel-tabs a.active");
    var ch = "";
    if (tab) {
        var m = (tab.getAttribute("hx-get") || "").match(/channel=([^&]+)/);
        if (m) ch = m[1];
    }
    var parts = [];
    if (ch) parts.push("channel=" + ch);
    if (q) parts.push("q=" + encodeURIComponent(q));
    try {
        history.replaceState(history.state, "", "/pulses" + (parts.length ? "?" + parts.join("&") : ""));
    } catch (e) {}
}
window._pulsesSyncUrl = _pulsesSyncUrl;

// Persist the search keyword to the URL as the user types (cheap, no network).
document.addEventListener("input", function (e) {
    if (e.target && e.target.matches &&
        e.target.matches('.table-filter input[name="q"]') &&
        location.pathname.indexOf("/pulses") === 0) {
        _pulsesSyncUrl();
    }
});

/* Before the pulses-changed HTMX refresh fires, patch the hx-get URL on
 * #pulses-rows-wrap to reflect the CURRENT search input + channel badge so
 * the server returns correctly filtered rows. Without this the URL is baked
 * at template render time and goes stale after edits/sync. */
/* ------------------------------------------------------------------ */
/* Pulses page — multi-select + waveform comparison overlay            */
/* ------------------------------------------------------------------ */

var _pulseSelection = [];   // paths of selected pulses (max 5)
var _PULSE_MAX_COMPARE = 5;
var _PULSE_COMPARE_COLORS = [
    "var(--pico-primary)", "#e67e22", "#2ecc71", "#e74c3c", "#9b59b6"
];

window.pulseSelChanged = function () {
    _pulseSelection = [];
    document.querySelectorAll(".pulse-sel-chk:checked").forEach(function (cb) {
        _pulseSelection.push(cb.getAttribute("data-path"));
    });
    // Enforce max by unchecking excess
    if (_pulseSelection.length > _PULSE_MAX_COMPARE) {
        _pulseSelection = _pulseSelection.slice(0, _PULSE_MAX_COMPARE);
        document.querySelectorAll(".pulse-sel-chk:checked").forEach(function (cb, i) {
            if (i >= _PULSE_MAX_COMPARE) cb.checked = false;
        });
    }
    var bar = document.getElementById("pulse-compare-bar");
    var countEl = document.getElementById("pulse-compare-count");
    if (bar) bar.hidden = _pulseSelection.length < 2;
    if (countEl) countEl.textContent = _pulseSelection.length;
};

window.clearPulseSelection = function () {
    document.querySelectorAll(".pulse-sel-chk:checked").forEach(function (cb) {
        cb.checked = false;
    });
    _pulseSelection = [];
    var bar = document.getElementById("pulse-compare-bar");
    if (bar) bar.hidden = true;
};

window.openPulseCompare = function () {
    if (_pulseSelection.length < 2) return;
    // Create or reuse modal
    var overlay = document.getElementById("pulse-compare-overlay");
    if (!overlay) {
        overlay = document.createElement("div");
        overlay.id = "pulse-compare-overlay";
        overlay.className = "state-review-overlay";
        overlay.style.display = "none";
        overlay.innerHTML =
            '<div class="state-review-backdrop" onclick="closePulseCompare()"></div>' +
            '<div class="state-review-card pulse-compare-card">' +
              '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem">' +
                '<h3 style="margin:0">Pulse Waveform Comparison</h3>' +
                '<button type="button" onclick="closePulseCompare()" style="background:none;border:none;font-size:1.2rem;cursor:pointer;color:var(--pico-muted-color)">&times;</button>' +
              '</div>' +
              '<div id="pulse-compare-plot" style="width:100%;height:400px"></div>' +
              '<div id="pulse-compare-legend" style="margin-top:0.5rem"></div>' +
            '</div>';
        document.body.appendChild(overlay);
    }
    overlay.style.display = "flex";

    // Purge any previous Plotly chart so re-renders work reliably.
    var plotDiv = document.getElementById("pulse-compare-plot");
    if (window.Plotly && plotDiv && plotDiv.data) {
        try { Plotly.purge(plotDiv); } catch (e) {}
    }
    plotDiv.innerHTML = '<p class="muted" style="padding:2rem;text-align:center">Synthesizing waveforms…</p>';

    fetch("/api/pulse/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json", "HX-Request": "true" },
        body: JSON.stringify({ paths: _pulseSelection })
    })
    .then(function (r) { return r.json(); })
    .then(function (d) {
        if (!d.ok) {
            plotDiv.innerHTML = '<p class="muted" style="padding:2rem">' + (d.error || "Comparison failed") + '</p>';
            return;
        }
        var traces = [];
        var legendHtml = [];
        d.pulses.forEach(function (p, idx) {
            if (!p.ok || !p.plot || !p.plot.traces) return;
            var color = _PULSE_COMPARE_COLORS[idx % _PULSE_COMPARE_COLORS.length];
            p.plot.traces.forEach(function (t) {
                traces.push({
                    x: t.x, y: t.y,
                    name: p.label + " " + t.name,
                    mode: "lines",
                    line: { color: color, width: t.name === "Q" ? 1.5 : 2,
                            dash: t.name === "Q" ? "dot" : "solid" },
                    hovertemplate: p.label + " " + t.name + ": %{y:.4g}<extra></extra>"
                });
            });
            legendHtml.push(
                '<span style="display:inline-flex;align-items:center;gap:0.3rem;margin-right:1rem">' +
                '<span style="width:12px;height:3px;background:' + color + ';display:inline-block"></span>' +
                '<span style="font-size:0.82rem">' + (p.label || p.path) + '</span></span>'
            );
        });

        var cs = getComputedStyle(document.documentElement);
        var cardBg = cs.getPropertyValue("--pico-card-background-color").trim() || "#1e2029";
        var plotBg = cs.getPropertyValue("--pico-background-color").trim() || "#13141a";
        var layout = {
            margin: { t: 20, r: 20, b: 40, l: 50 },
            xaxis: { title: "Time (ns)", gridcolor: "rgba(128,128,128,0.15)" },
            yaxis: { title: "Amplitude", gridcolor: "rgba(128,128,128,0.15)" },
            showlegend: false,
            paper_bgcolor: cardBg,
            plot_bgcolor: plotBg,
            font: { color: cs.getPropertyValue("--pico-color").trim() }
        };
        if (window._plotlyRender) {
            window._plotlyRender("pulse-compare-plot", traces, layout, { responsive: true });
        } else if (window.Plotly) {
            Plotly.newPlot("pulse-compare-plot", traces, layout, { responsive: true });
        }
        var legendEl = document.getElementById("pulse-compare-legend");
        if (legendEl) legendEl.innerHTML = legendHtml.join("");
    })
    .catch(function () {
        plotDiv.innerHTML = '<p class="muted" style="padding:2rem">Comparison request failed.</p>';
    });
};

window.closePulseCompare = function () {
    var overlay = document.getElementById("pulse-compare-overlay");
    if (overlay) overlay.style.display = "none";
};

/* Strip any existing `key=` from a URL's query string and, when value is
   non-empty, append the fresh one — so overriding a baked query param can't
   create a `key=stale&key=fresh` duplicate (Flask reads the first). Preserves
   the path, other params, and any #hash. */
function _setQueryParam(path, key, value) {
    if (typeof path !== "string") return path;
    var hashIdx = path.indexOf("#");
    var hash = hashIdx >= 0 ? path.slice(hashIdx) : "";
    if (hashIdx >= 0) path = path.slice(0, hashIdx);
    var qIdx = path.indexOf("?");
    var base = qIdx >= 0 ? path.slice(0, qIdx) : path;
    var qs = qIdx >= 0 ? path.slice(qIdx + 1) : "";
    var parts = qs ? qs.split("&").filter(function (p) {
        return p && decodeURIComponent(p.split("=")[0]) !== key;
    }) : [];
    if (value !== null && value !== undefined && value !== "") {
        parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(value));
    }
    return base + (parts.length ? "?" + parts.join("&") : "") + hash;
}

document.addEventListener("htmx:configRequest", function (evt) {
    var el = evt.detail.elt;
    if (!el) return;
    // Applies to EVERY pulses-table request — the search input, a channel badge, AND
    // the pulses-changed mutation refresh (all target #pulses-rows-wrap). Each must
    // carry BOTH the live search keyword AND the live channel: the input's baked
    // hx-get channel and a badge's hx-include can otherwise go stale after a
    // client-side switch (e.g. click XY, then type → the search would drop back to
    // the render-time channel and mix in other channels).
    var isPulsesReq = el.id === "pulses-rows-wrap" ||
        (el.matches && el.matches('.table-filter input[name="q"], #pulse-channel-tabs a'));
    if (!isPulsesReq) return;
    // Rewrite evt.detail.path itself rather than setting evt.detail.parameters:
    // htmx 2.x SERIALIZES parameters and APPENDS them to the baked query string,
    // so a stale baked `channel=xy` plus a parameter `channel=all` produced
    // `?channel=xy&channel=all` and Flask took the FIRST duplicate — leaving the
    // filter stuck on the render-time channel. Strip+set the params in the path
    // and drop them from parameters so no duplicate is appended.
    var searchInput = document.querySelector('.table-filter input[name="q"]');
    var q = searchInput ? searchInput.value.trim() : "";
    var channel = "";
    var activeTab = document.querySelector("#pulse-channel-tabs a.active");
    if (activeTab) {
        var m = (activeTab.getAttribute("hx-get") || "").match(/channel=([^&]+)/);
        if (m) channel = decodeURIComponent(m[1]);   // "" ⇒ all channels
    }
    var path = _setQueryParam(evt.detail.path, "q", q);
    path = _setQueryParam(path, "channel", channel);
    evt.detail.path = path;
    delete evt.detail.parameters["q"];
    delete evt.detail.parameters["channel"];
    // Keep the browser URL in sync so a later full re-fetch / reload preserves both.
    if (window._pulsesSyncUrl) window._pulsesSyncUrl();
});

// ---------------------------------------------------------------------------
// JSON drill-down panel (#json-panel) — drag-to-resize (review-r7: the
// Generate Config preview needed more room than a quick wiring-pointer
// lookup, and the panel had no resize at all). One delegated listener covers
// every call site (Wiring/Pairs/Qubits/Instrument pages + the wizard) since
// each renders its own #json-panel copy and none share a mount hook; height
// (not the CSS max-height) is the resizable knob, persisted globally so a
// user's preferred size follows them from page to page.
(function () {
    var H_KEY = "quam_json_panel_h";
    function _applyPersistedHeight(root) {
        var h = parseInt(localStorage.getItem(H_KEY), 10);
        if (!h) return;
        (root || document).querySelectorAll(".json-panel").forEach(function (p) {
            p.style.height = h + "px";
        });
    }
    document.addEventListener("DOMContentLoaded", function () { _applyPersistedHeight(); });
    document.addEventListener("htmx:afterSwap", function (evt) {
        _applyPersistedHeight(evt.detail && evt.detail.target);
    });
    var _drag = null;
    document.addEventListener("mousedown", function (e) {
        var handle = e.target.closest && e.target.closest(".json-panel-resizer");
        if (!handle) return;
        var panel = handle.closest(".json-panel");
        if (!panel) return;
        e.preventDefault();
        handle.classList.add("json-panel-resizing");
        _drag = { panel: panel, handle: handle, startY: e.clientY,
                  startH: panel.getBoundingClientRect().height };
    });
    document.addEventListener("mousemove", function (e) {
        if (!_drag) return;
        // Panel is bottom-docked — dragging the top edge UP grows it.
        var dh = _drag.startY - e.clientY;
        var h = Math.max(160, Math.min(window.innerHeight * 0.92, _drag.startH + dh));
        _drag.panel.style.height = h + "px";
    });
    document.addEventListener("mouseup", function () {
        if (!_drag) return;
        _drag.handle.classList.remove("json-panel-resizing");
        try {
            localStorage.setItem(H_KEY, String(Math.round(_drag.panel.getBoundingClientRect().height)));
        } catch (e) {}
        _drag = null;
    });
})();

/* ── Per-field value history (docs/20): 🕘 on Live-Edit cells + inspector rows ──
   Opens a floating panel of the field's past values (Param History change
   points). "Use" fills the originating edit input — the commit stays
   user-explicit through the normal staging flow (Enter). "Data" hx-gets the
   producing run's detail into #inspector-pane so value + data sit together. */
window.FieldHistory = (function () {
    var panel = null;
    var applyInput = null;   // the edit input "Use" fills

    function ensurePanel() {
        if (panel) return panel;
        panel = document.createElement("div");
        panel.id = "field-history-panel";
        panel.className = "field-history-panel";
        panel.setAttribute("role", "dialog");
        panel.style.display = "none";
        document.body.appendChild(panel);
        document.addEventListener("mousedown", function (e) {
            if (panel.style.display === "none") return;
            if (panel.contains(e.target)) return;
            if (e.target.closest && e.target.closest(".field-hist-btn, #fh-cellbtn")) return;
            close();
        });
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape" && panel.style.display !== "none") close();
        });
        // Singleton (ensurePanel runs once): a window shrink used to strand
        // the position:fixed panel fully off-screen — config.responsive
        // covers only the PLOT (docs/124, the popover minor). Re-clamp into
        // the viewport while visible.
        window.addEventListener("resize", function () {
            if (!panel || panel.style.display === "none") return;
            var w = Math.min(500, window.innerWidth - 16);
            panel.style.width = w + "px";
            var r = panel.getBoundingClientRect();
            var left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
            // Clamp by the panel's own HEIGHT, not a fixed top margin — the
            // first version guaranteed only the top edge and left the bottom
            // overhanging by up to the panel height (measured 270px worst
            // case, 10.6px in the realistic one). Floored at 8: a panel
            // taller than the viewport top-aligns, which is the best honest
            // outcome.
            var top = Math.max(8, Math.min(r.top, window.innerHeight - r.height - 8));
            panel.style.left = left + "px";
            panel.style.top = top + "px";
        });
        return panel;
    }

    function position(anchor) {
        var p = ensurePanel();
        var r = anchor.getBoundingClientRect();
        var w = Math.min(500, window.innerWidth - 16);
        p.style.width = w + "px";
        p.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px";
        p.style.top = (r.bottom + 6) + "px";
        p.style.display = "block";
        // flip above the anchor if the panel overflows the viewport bottom
        requestAnimationFrame(function () {
            var h = p.offsetHeight;
            if (r.bottom + 6 + h > window.innerHeight - 8) {
                p.style.top = Math.max(8, r.top - h - 6) + "px";
            }
        });
    }

    function open(anchor, path, input) {
        if (!path) return;
        applyInput = input || null;
        var p = ensurePanel();
        // The previous open's #fh-chart (responsive:true) holds a window
        // resize handler referencing the graph div — innerHTML without purge
        // leaked one handler + one detached Plotly subtree PER OPEN
        // (docs/124, the popover minor).
        if (window.PlotHost) { try { window.PlotHost.purgeWithin(p); } catch (e) {} }
        p.innerHTML = '<p class="fh-empty">Loading history…</p>';
        position(anchor);
        fetch("/field/history?path=" + encodeURIComponent(path))
            .then(function (r) { return r.text(); })
            .then(function (html) {
                p.innerHTML = html;
                if (window.htmx) window.htmx.process(p);
                renderChart(p);
                position(anchor);
            })
            .catch(function () {
                p.innerHTML = '<p class="fh-empty">Could not load history.</p>';
            });
    }

    function renderChart(p) {
        // The parameter's own mini trend (the Param History drawer chart's
        // small twin): change points as a step line, trigger-colored markers.
        var mount = p.querySelector("#fh-chart");
        var dataEl = p.querySelector("#fh-chart-data");
        if (!mount || !dataEl) return;
        if (!window.Plotly) {
            // Plotly is lazy-loaded, and this popover's home surfaces (the
            // qubit/pair inspectors, the bulk grids) mount no other chart — so
            // on a fresh page load the library is simply not there yet, and
            // bailing made the docs/20 mini-trend dead on arrival exactly
            // where it lives (docs/124 M-18). Load it, then render whatever
            // the panel holds by then; renderChart re-queries its own mounts,
            // so a panel that moved on to another path renders that one, and
            // a closed panel (display:none, never detached) renders hidden —
            // harmless, correct on reopen.
            if (window.requirePlotly) {
                window.requirePlotly().then(function () {
                    if (p.isConnected) renderChart(p);
                }).catch(function () {});
            }
            return;
        }
        var pts;
        try { pts = JSON.parse(dataEl.textContent || "[]"); }
        catch (e) { return; }
        if (!pts || pts.length < 2) return;
        var cssVar = function (t) {
            var s = getComputedStyle(document.documentElement)
                .getPropertyValue("--trigger-" + (t || "auto"));
            return (s || "#888").trim() || "#888";
        };
        var muted = (getComputedStyle(document.documentElement)
            .getPropertyValue("--color-text-muted") || "#888").trim();
        var trace = {
            x: pts.map(function (d) { return d.t; }),
            y: pts.map(function (d) { return d.v; }),
            type: "scatter", mode: "lines+markers",
            line: { shape: "hv", color: muted, width: 1.2 },
            marker: {
                size: 7,
                color: pts.map(function (d) { return cssVar(d.trigger); }),
            },
            hovertemplate: "%{x}<br>%{y}<extra></extra>",
        };
        var layout = {
            height: 128,
            margin: { l: 46, r: 8, t: 6, b: 30 },
            xaxis: { type: "date", tickfont: { size: 9 } },
            yaxis: { tickfont: { size: 9 }, exponentformat: "SI" },
            showlegend: false,
            paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
            font: { color: muted },
        };
        try {
            Plotly.newPlot(mount, [trace], layout,
                           { displayModeBar: false, responsive: true });
        } catch (e) { /* chart is a bonus — never break the panel */ }
    }

    function close() {
        if (panel) panel.style.display = "none";
    }

    function useValue(btn) {
        var v = btn.getAttribute("data-value") || "";
        if (applyInput && document.body.contains(applyInput)) {
            // Grid cells join the LiveEditUndo stack (docs/20 v2) so one
            // Ctrl+Z reverts this fill before it is ever staged.
            if (window.LiveEditUndo && applyInput.classList
                    && applyInput.classList.contains("bulk-cell")
                    && applyInput.dataset.dotPath) {
                window.LiveEditUndo.record("history fill (1 cell)", [{
                    dp: applyInput.dataset.dotPath,
                    prev: applyInput.value, next: v,
                }]);
            }
            applyInput.value = v;
            applyInput.dispatchEvent(new Event("input", { bubbles: true }));
            applyInput.focus();
            if (applyInput.select) applyInput.select();
        }
        close();
    }

    function openInspector(btn) {
        var form = btn.closest("form.inline-edit");
        if (!form) return;
        var hidden = form.querySelector("input[name=dot_path]");
        var input = form.querySelector("input[name=value]");
        open(btn, hidden ? hidden.value : "", input);
    }

    /* Bulk-grid affordance: one shared 🕘 button that docks INSIDE the focused
       cell's <td> (r10): the td is the containing block, so the button moves
       with the cell through column resize/autofit, font scaling, scrolling and
       reflow. The old approach — a body-mounted position:fixed float pinned to
       the focusin-time getBoundingClientRect() — kept STALE viewport coords
       whenever layout shifted afterwards and visibly drifted outside the box
       (the "clock escaped the cell again" report). A per-cell button would
       widen every column of a dense grid, so the single shared button stays.
       mousedown + preventDefault keeps the cell focused. */
    var CELLBTN_LABEL = "Value history — past values of this field from Param History snapshots";
    // A plain `title` attribute waits on the BROWSER's own hover delay
    // (500ms+, not something CSS/JS can shorten) — user feedback: hovering
    // the clock should show its label right away. A tiny custom tooltip,
    // shown on mouseenter with no artificial delay, replaces the native one;
    // aria-label keeps the same text available to assistive tech.
    var cellBtnTip = null;
    function _showCellBtnTip() {
        if (!cellBtn) return;
        if (!cellBtnTip) {
            cellBtnTip = document.createElement("div");
            cellBtnTip.className = "fh-cellbtn-tip";
            cellBtnTip.textContent = CELLBTN_LABEL;
            document.body.appendChild(cellBtnTip);
        }
        var r = cellBtn.getBoundingClientRect();
        cellBtnTip.style.display = "block";
        var w = cellBtnTip.offsetWidth;
        var left = Math.max(4, Math.min(r.left + r.width / 2 - w / 2,
                                          window.innerWidth - w - 4));
        cellBtnTip.style.left = left + "px";
        cellBtnTip.style.top = (r.bottom + 5) + "px";
    }
    function _hideCellBtnTip() {
        if (cellBtnTip) cellBtnTip.style.display = "none";
    }
    var cellBtn = null;
    function ensureCellBtn() {
        if (cellBtn) return cellBtn;
        cellBtn = document.createElement("button");
        cellBtn.id = "fh-cellbtn";
        cellBtn.type = "button";
        cellBtn.setAttribute("aria-label", CELLBTN_LABEL);
        cellBtn.textContent = "🕘";
        cellBtn.style.display = "none";
        cellBtn.addEventListener("mouseenter", _showCellBtnTip);
        cellBtn.addEventListener("mouseleave", _hideCellBtnTip);
        cellBtn.addEventListener("mousedown", function (e) {
            e.preventDefault();
            _hideCellBtnTip();
            var input = cellBtn._input;
            if (input) {
                open(cellBtn, input.dataset.resolved || input.dataset.dotPath || "", input);
            }
        });
        document.body.appendChild(cellBtn);
        return cellBtn;
    }
    /* r11: the icon sits right AFTER the value text, not at the td's right
       edge — a wide column put it far from the value, and the focused
       input's opaque background (z-index 4) painted OVER a td-edge icon
       whenever the input box reached that far ("clock invisible"). Width is
       measured off-DOM (canvas measureText + letter-spacing correction; a
       length×char-width monospace fallback where canvas is unavailable). */
    var _measureCanvas = null;
    function _cellTextWidth(input, geom) {
        var value = input.value || "";
        // `geom` carries the font already measured for this cell (docs/120 item
        // 24) — asking the engine again per keystroke is a forced style recalc
        // for values that cannot have changed since the cell took focus.
        var cs = geom || null;
        if (!cs) {
            try { cs = window.getComputedStyle(input); } catch (e) {}
        }
        var fontPx = 14;
        if (cs) {
            var fp = parseFloat(cs.fontSize !== undefined ? cs.fontSize : cs.fontPx);
            if (fp > 0) fontPx = fp;
        }
        if (_measureCanvas === null) {
            // Cache the 2d context (or false) — engines without canvas
            // (jsdom) must not throw per keystroke.
            try {
                var cnv = document.createElement("canvas");
                _measureCanvas = (cnv.getContext && cnv.getContext("2d")) || false;
            } catch (e) { _measureCanvas = false; }
        }
        if (_measureCanvas && cs) {
            try {
                _measureCanvas.font = cs.font || ((cs.fontWeight || "500") + " "
                    + fontPx + "px " + (cs.fontFamily || "monospace"));
                var w = _measureCanvas.measureText(value).width;
                var ls = parseFloat(cs.letterSpacing);
                if (ls > 0 && value.length > 1) w += ls * (value.length - 1);
                return w;
            } catch (e) { /* fall through to the approximation */ }
        }
        return value.length * fontPx * 0.62;   // monospace approximation
    }
    /* docs/120 item 24 — this runs on EVERY keystroke in a grid cell, and it
       was reading `getComputedStyle` twice (once here, once inside
       _cellTextWidth) and then `offsetLeft`/`offsetWidth`, which forces a full
       layout of a 158-column sticky table, before writing `style.left` and
       toggling a class — i.e. read/write/read alternation, per key.

       Measured with the CPU profiler on the customer's 20-qubit chip: 70.4 ms
       of self time across ten keystrokes, the dominant app cost of typing by an
       order of magnitude. (The audit agent had blamed `_refreshGlobal` scanning
       3,160 nodes twice; that measures 0.9 ms for the same ten keystrokes.)

       None of what it reads CHANGES while a key is pressed — padding, font and
       the cell's own geometry are fixed for as long as the cell holds focus,
       which the neighbouring comment already relies on ("the cell never resizes
       on plain focus"). So measure once when the button arrives at a cell and
       reuse it; only the text width, measured off-DOM on a canvas, is per-key. */
    function _cellBtnMeasure() {
        var b = cellBtn, input = b && b._input;
        if (!b || !input || !input.isConnected) { if (b) b._geom = null; return; }
        var padL = 4, cs = null;
        try { cs = window.getComputedStyle(input); } catch (e) {}
        if (cs) {
            var pl = parseFloat(cs.paddingLeft);
            if (pl >= 0) padL = pl;
        }
        b._geom = {
            padL: padL,
            left: input.offsetLeft,
            width: input.offsetWidth,
            font: cs ? ((cs.fontWeight || "500") + " "
                        + (parseFloat(cs.fontSize) > 0 ? parseFloat(cs.fontSize) : 14)
                        + "px " + (cs.fontFamily || "monospace")) : null,
            fontPx: (cs && parseFloat(cs.fontSize) > 0) ? parseFloat(cs.fontSize) : 14,
            letterSpacing: cs ? parseFloat(cs.letterSpacing) : NaN,
        };
    }
    function _positionCellBtn() {
        var b = cellBtn, input = b && b._input;
        if (!b || !input || !input.isConnected) return;
        if (!b._geom) _cellBtnMeasure();
        var g = b._geom;
        if (!g) return;
        var padL = g.padL;
        var want = g.left + padL + _cellTextWidth(input, g) + 4;
        var max = g.left + g.width - 20;
        var clamped = want > max;
        b.style.left = Math.max(g.left + 2, Math.min(want, max)) + "px";
        // Only a FULL cell needs the text padded away from the icon — the
        // unclamped icon sits in the input's empty tail (and the cell never
        // resizes on plain focus, restoring the style.css invariant).
        input.classList.toggle("fh-docked", clamped);
    }
    function showCellBtn(input) {
        var b = ensureCellBtn();
        var td = input.closest("td");
        if (!td) return;
        if (b._input && b._input !== input) {
            b._input.classList.remove("fh-docked");   // cell-to-cell move
            _hideCellBtnTip();
        }
        b._input = input;
        td.appendChild(b);               // appendChild MOVES the shared button
        b.style.display = "block";
        // The one place the cell's geometry really can differ: re-measure HERE,
        // then every keystroke reuses it (docs/120 item 24).
        _cellBtnMeasure();
        _positionCellBtn();
    }
    function hideCellBtn() {
        if (!cellBtn) return;
        _hideCellBtnTip();
        cellBtn.style.display = "none";
        cellBtn.style.left = "";
        if (cellBtn._input) {
            cellBtn._input.classList.remove("fh-docked");
            cellBtn._input = null;
        }
        cellBtn._geom = null;            // stale geometry must never be reused
        // Park on <body> so a grid re-render can't destroy the shared button
        // (and the td's search/sort surface stays byte-clean while unfocused).
        if (cellBtn.parentElement && cellBtn.parentElement !== document.body) {
            document.body.appendChild(cellBtn);
        }
    }
    /* Drop the focused cell's cached metrics WITHOUT hiding the button — the
       one thing that can change them mid-focus is a UI-scale change, which
       rescales every cell while the user is still typing in one. */
    window.__cellBtnInvalidate = function () {
        if (!cellBtn) return;
        cellBtn._geom = null;
        if (cellBtn._input && cellBtn.style.display !== "none") {
            _cellBtnMeasure();
            _positionCellBtn();
        }
    };
    document.addEventListener("focusin", function (e) {
        var t = e.target;
        if (t && t.classList && t.classList.contains("bulk-cell") &&
            !t.classList.contains("bulk-cell-ro")) {
            showCellBtn(t);
        } else if (!t || t.id !== "fh-cellbtn") {
            hideCellBtn();
        }
    });
    // Typing changes the text length — the icon follows the value's tail.
    document.addEventListener("input", function (e) {
        if (cellBtn && e.target === cellBtn._input) _positionCellBtn();
    });

    return { open: open, close: close, useValue: useValue,
             openInspector: openInspector,
             _cellTextWidth: _cellTextWidth };   // r11 test seam
})();

/* ── LiveEditUndo (docs/20 v2): in-memory undo for UN-STAGED grid edits ──
   The clipboard-fast tier of the unified Ctrl+Z: programmatic fills
   (Column History Use / Use all, 🕘 history fills) and manual typing in
   bulk-grid cells, value-level, selector-addressed (survives grid swaps).
   Staged edits are the SERVER's undo (POST /undo, one change_log group per
   press — the Review tray swaps atomically with it); save/apply stay hard
   boundaries. */
window.LiveEditUndo = (function () {
    var stack = [];        // {label, cells: [{dp, prev, next}]}
    var redo = [];         // docs/107: what tryUndo popped — Ctrl+Shift+Z target
    var CAP = 100;

    function _esc(s) {
        return (window.CSS && CSS.escape) ? CSS.escape(s) : s;
    }
    function _input(dp) {
        try {
            return document.querySelector(
                '.bulk-cell[data-dot-path="' + _esc(dp) + '"]');
        } catch (e) { return null; }
    }

    function record(label, cells) {
        cells = (cells || []).filter(function (c) {
            return c && c.dp && c.prev !== c.next;
        });
        if (!cells.length) return;
        stack.push({ label: label, cells: cells });
        if (stack.length > CAP) stack.shift();
        redo = [];   // docs/107: a NEW action forks history — redo dies
        _updateTrayBtn();
    }

    function tryUndo() {
        // audit-r10 discipline: an entry only "succeeds" when it actually
        // RESTORED at least one on-screen, un-staged cell. Cells that are
        // gone (grid navigated away) or whose value was COMMITTED since
        // (data-orig == the recorded next → the server tier owns that undo
        // now) are skipped; a fully-stale entry is dropped silently and the
        // loop continues — so stale entries can never eat Ctrl+Z presses or
        // block the server tier, and a fill that got staged can't be
        // half-undone into a phantom dirty edit.
        while (stack.length) {
            var a = stack.pop();
            var restored = 0, gone = 0, staged = 0;
            a.cells.forEach(function (c) {
                var input = _input(c.dp);
                if (!input || input.readOnly) { gone++; return; }
                // "Committed since" has TWO spellings, and only one was
                // recognised. `c.next` is the RAW TYPED TEXT, captured by the
                // change listener; a commit rewrites data-orig with the
                // STORED value, which is formatted (4.41e9 -> 4,410,000,000).
                // On the ENTER path the two happen to match, so Ctrl+Z
                // correctly fell through to the server. On the CLICK-AWAY
                // path `change` fires before `focusout`, the stored text
                // differs from what was typed, the guard missed — and Ctrl+Z
                // then "undid" locally: no request, the cell rewound and was
                // re-marked dirty, while the working state still held the new
                // value. On a field that was "not set" it staged an empty
                // string that Apply-all could never coerce, wedging the grid
                // until a Reset threw away every pending edit.
                //
                // A committed cell is a CLEAN cell, whatever the formatting
                // did to the text, so ask that instead.
                var _committed = input.getAttribute("data-orig") === String(c.next)
                    || input.value === input.getAttribute("data-orig");
                if (_committed) { staged++; return; }
                input.value = c.prev;
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.classList.add("leu-flash");
                (function (el) {
                    setTimeout(function () { el.classList.remove("leu-flash"); }, 650);
                })(input);
                restored++;
            });
            _updateTrayBtn();
            if (restored) {
                redo.push(a);   // docs/107: Ctrl+Shift+Z re-applies this action
                try {
                    document.dispatchEvent(new CustomEvent("quam:undo-step", { detail: {
                        kind: "undo", tier: "memory", label: a.label,
                        entries: a.cells.map(function (c) { return { dot_path: c.dp, value: c.prev, from: c.next }; }) } }));
                } catch (e) {}
                if (redo.length > CAP) redo.shift();
                if (window.showToast) {
                    var extra = [];
                    if (gone) extra.push(gone + " no longer on screen");
                    if (staged) extra.push(staged + " already staged");
                    window.showToast("Undid " + a.label
                        + (extra.length ? " (" + extra.join(", ") + ")" : ""));
                }
                return true;
            }
            // fully stale (gone/staged) — drop and keep looking
        }
        _updateTrayBtn();
        return false;
    }

    /* docs/107 Ctrl+Shift+Z tier: re-apply the last tryUndo'd action. Same
       skip discipline, mirrored: a cell is only re-filled when it still shows
       the value the undo restored (c.prev) — a cell that moved since (typed,
       staged, re-rendered to a different value) is never clobbered. A fully
       stale entry drops silently and the loop continues, so stale entries
       can't eat the press or block the server /redo tier below. */
    function tryRedo() {
        while (redo.length) {
            var a = redo.pop();
            var applied = 0;
            a.cells.forEach(function (c) {
                var input = _input(c.dp);
                if (!input || input.readOnly) return;
                if (input.value !== String(c.prev)) return;   // moved since
                input.value = c.next;
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.classList.add("leu-flash");
                (function (el) {
                    setTimeout(function () { el.classList.remove("leu-flash"); }, 650);
                })(input);
                applied++;
            });
            if (applied) {
                stack.push(a);   // the redone action is undoable again
                try {
                    document.dispatchEvent(new CustomEvent("quam:undo-step", { detail: {
                        kind: "redo", tier: "memory", label: a.label,
                        entries: a.cells.map(function (c) { return { dot_path: c.dp, value: c.next, from: c.prev }; }) } }));
                } catch (e) {}
                if (stack.length > CAP) stack.shift();
                _updateTrayBtn();
                if (window.showToast) window.showToast("Redid " + a.label);
                return true;
            }
        }
        return false;
    }

    // Hard boundaries (audit-r10): once content reached the live chip (or
    // was replaced wholesale by a stage/pull), reverting cells from memory
    // would cross the apply boundary — the module's own contract forbids it.
    function clear() {
        stack = [];
        redo = [];
        _updateTrayBtn();
    }
    document.addEventListener("stateRestored", function () { clear(); });

    /* Manual typing joins the stack (the _wizUndo idiom): snapshot the
       cell's value on focusin, push one action per committed change. A
       programmatic fill dispatches only 'input' (never 'change'), so fills
       are never double-recorded. */
    var _snap = null;
    document.addEventListener("focusin", function (e) {
        var t = e.target;
        if (t && t.classList && t.classList.contains("bulk-cell") && !t.readOnly) {
            _snap = { dp: t.dataset.dotPath || "", value: t.value };
        }
    });
    document.addEventListener("change", function (e) {
        var t = e.target;
        if (!t || !t.classList || !t.classList.contains("bulk-cell")) return;
        var dp = t.dataset.dotPath || "";
        if (!_snap || _snap.dp !== dp || _snap.value === t.value) return;
        var leaf = dp.split(".").slice(-1)[0] || "cell";
        record("typed edit (" + leaf + ")",
               [{ dp: dp, prev: _snap.value, next: t.value }]);
        _snap = { dp: dp, value: t.value };
    });

    /* docs/111 audit F14: a programmatic write into a cell the user had ALREADY
       typed in would be recorded twice — once explicitly by the writer (paste
       /fill) and once by the change-listener below on the eventual blur, whose
       snapshot still holds the pre-typing value. The writer calls resync() to
       move the snapshot forward, so only its own single action is recorded. */
    function resync(input) {
        if (!input) return;
        var dp = (input.dataset && input.dataset.dotPath) || '';
        if (_snap && _snap.dp === dp) _snap = { dp: dp, value: input.value };
    }

    /* The tray ↶ runs the SAME tier chain as Ctrl+Z. */
    function trigger() {
        if (window._wizUndo && window._wizUndo.tryUndo()) return;
        if (tryUndo()) return;
        // Same queue as Ctrl+Z (docs/122 item 3): a fast double-click on the
        // tray ↶ used to lose its second press exactly like a fast keypress.
        if (window.UndoQueue) window.UndoQueue.push("/undo");
    }

    function _changeCount() {
        var tray = document.getElementById("pending-tray");
        return tray ? parseInt(tray.getAttribute("data-change-count") || "0", 10) : 0;
    }
    function _updateTrayBtn() {
        var btn = document.getElementById("tray-undo-btn");
        if (!btn) return;
        btn.style.display = (stack.length || _changeCount() > 0) ? "" : "none";
    }
    function refreshTip(btn) {
        // Transparency contract: the tooltip names what the NEXT press does.
        var tip;
        if (stack.length) {
            tip = "Undo " + stack[stack.length - 1].label + " (Ctrl+Z)";
        } else if (_changeCount() > 0) {
            // r16 ⓪-2 (docs/73): NAME the target — /undo pops the newest
            // change-log group, i.e. the LAST tray item (+ its group mates).
            var items = document.querySelectorAll(
                "#pending-tray .tray-change-item");
            var last = items.length ? items[items.length - 1] : null;
            var pathEl = last && last.querySelector(".tray-change-path");
            var path = pathEl ? pathEl.textContent : "";
            var n = 1;
            var gid = last ? last.getAttribute("data-group-id") : "";
            if (gid) {
                n = 0;
                for (var i = items.length - 1; i >= 0
                     && items[i].getAttribute("data-group-id") === gid; i--) n++;
            }
            if (gid && gid.indexOf("jrn:") === 0) {
                // docs/107: with a staged journal step on top, the next press
                // walks DEEPER into history (stages the previous save's
                // inverse) — it does not remove the shown entry. The tooltip
                // must name what the press actually does.
                tip = "Undo more from history — stages the previous save's "
                    + "inverse into Review (Ctrl+Z; Ctrl+Shift+Z un-stages)";
            } else {
            tip = path
                ? ("Undo staged change to " + path
                   + (n > 1 ? " (+" + (n - 1) + " more in this action)" : "")
                   + " (Ctrl+Z)")
                : ("Undo last staged change — removes exactly that entry "
                   + "group from Review (Ctrl+Z)");
            }
        } else {
            tip = "Nothing to undo";
        }
        btn.title = tip;
    }

    // The tray re-renders on every staged mutation — re-evaluate the button.
    // audit-r10: ALSO on oobAfterSwap (every server _tray_oob() rides OOB,
    // which never fires afterSwap) — without it the ↶ stayed display:none
    // after nearly every staging path.
    function _onTraySwap(e) {
        var t = e.target;
        if (t && (t.id === "pending-tray"
                  || (t.querySelector && t.querySelector("#pending-tray")))) {
            _updateTrayBtn();
        }
    }
    document.addEventListener("htmx:afterSwap", _onTraySwap);
    document.addEventListener("htmx:oobAfterSwap", _onTraySwap);
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", _updateTrayBtn);
    } else {
        _updateTrayBtn();
    }

    return { record: record, tryUndo: tryUndo, tryRedo: tryRedo, resync: resync,
             trigger: trigger, clear: clear,
             refreshTip: refreshTip, _updateTrayBtn: _updateTrayBtn };
})();

/* ── UndoNav (r16 ⓪-2, docs/73): make the SERVER undo tier visible ──
   Before this, a Ctrl+Z that fell through to POST /undo reverted the most
   recent change-log group — which may belong to ANOTHER page/tab — with no
   visible effect on the current page ("Ctrl+Z did nothing / changed a value
   I typed elsewhere"). Driven by the /undo RESPONSE (cellsReverted entries —
   authoritative; a peek-then-undo design would race a concurrent commit):
   - target visible on this page → flash it in place;
   - target elsewhere → STASH this page's in-progress typing, navigate to the
     owning surface (qubit/pair inspector deep link, /bulk, Explorer) and
     highlight the reverted item; the stash refills on return so nothing the
     user typed is lost (the r16 hard requirement). */
window.UndoNav = (function () {
    var STASH_KEY = "quam_undo_stash";
    var STASH_TTL_MS = 30 * 60 * 1000;
    var _pendingHighlight = [];        // dot paths still awaiting a visible el

    function _esc(s) {
        return (window.CSS && CSS.escape) ? CSS.escape(String(s)) : String(s);
    }

    function _visible(el) {
        return !!(el && el.getClientRects && el.getClientRects().length);
    }

    // First VISIBLE element owning a dot-path on the current page. A hidden
    // bulk column deliberately counts as NOT covered (escape hatch: navigate
    // to the inspector instead of flashing something the user can't see).
    function visibleEl(dp) {
        var el = null;
        try {
            el = document.querySelector('.bulk-cell[data-dot-path="' + _esc(dp) + '"]');
            if (_visible(el)) return el;
            el = document.querySelector('.av-input[data-dot-path="' + _esc(dp) + '"]');
            if (_visible(el)) return el;
            var hidden = document.querySelector(
                'input[type="hidden"][name="dot_path"][value="' + _esc(dp) + '"]');
            if (hidden) {
                var form = hidden.closest("form");
                var input = form && form.querySelector('input[name="value"]');
                if (_visible(input)) return input;
            }
            el = document.querySelector('.tree-node[data-path="' + _esc(dp) + '"]');
            if (_visible(el)) return el;
        } catch (e) { /* selector quirks — treat as not covered */ }
        return null;
    }

    function flash(el) {
        if (!el || !el.classList) return;
        el.classList.add("leu-flash");
        setTimeout(function () { el.classList.remove("leu-flash"); }, 900);
        if (el.scrollIntoView) {
            try { el.scrollIntoView({ block: "center", behavior: "smooth" }); }
            catch (e) { el.scrollIntoView(); }
        }
    }

    // Owning surface for a reverted group. Anchor = the OLDEST entry (the
    // action's anchor — same one the server toast names).
    function ownerSurface(entries) {
        var anchor = entries[entries.length - 1] || entries[0];
        var dp = (anchor && anchor.dot_path) || "";
        var seg = dp.split(".");
        var owners = {};
        entries.forEach(function (e) {
            var s = ((e && e.dot_path) || "").split(".");
            owners[s[0] + (s[1] ? "." + s[1] : "")] = 1;
        });
        var multi = Object.keys(owners).length > 1;
        if (seg[0] === "qubits" && seg[1]) {
            return multi
                ? { kind: "pane", url: "/bulk" }
                : { kind: "inspector",
                    url: "/qubit/" + encodeURIComponent(seg[1])
                         + "?focus=" + encodeURIComponent(dp) };
        }
        if (seg[0] === "qubit_pairs" && seg[1]) {
            return multi
                ? { kind: "pane", url: "/bulk" }
                : { kind: "inspector",
                    url: "/pair/" + encodeURIComponent(seg[1])
                         + "?focus=" + encodeURIComponent(dp) };
        }
        // ports / octaves / mixers / twpas / wiring / top-level → Explorer
        return { kind: "explorer", path: dp };
    }

    // In-progress typing on the CURRENT page, keyed by dot-path. All-Values
    // keeps its own dirty Map across rebuilds — skipped here.
    function stashDirtyInputs() {
        var cells = {};
        document.querySelectorAll(".bulk-cell").forEach(function (c) {
            var dp = c.dataset && c.dataset.dotPath;
            var orig = c.getAttribute("data-orig");
            if (dp && orig !== null && c.value !== orig) cells[dp] = c.value;
        });
        document.querySelectorAll("form.inline-edit").forEach(function (f) {
            var hidden = f.querySelector('input[name="dot_path"]');
            var input = f.querySelector('input[name="value"]');
            if (!hidden || !input) return;
            var baseline = input.hasAttribute("data-committed")
                ? input.getAttribute("data-committed") : input.defaultValue;
            if (input.value !== baseline) cells[hidden.value] = input.value;
        });
        if (!Object.keys(cells).length) return;
        try {
            sessionStorage.setItem(STASH_KEY, JSON.stringify(
                { ts: Date.now(), cells: cells }));
        } catch (e) { /* private mode — navigation still proceeds */ }
    }

    function _readStash() {
        try {
            var raw = sessionStorage.getItem(STASH_KEY);
            if (!raw) return null;
            var st = JSON.parse(raw);
            if (!st || !st.cells || Date.now() - (st.ts || 0) > STASH_TTL_MS) {
                sessionStorage.removeItem(STASH_KEY);
                return null;
            }
            return st;
        } catch (e) { return null; }
    }

    function clearStash() {
        try { sessionStorage.removeItem(STASH_KEY); } catch (e) {}
    }

    // After swaps: refill stashed typing into any now-present inputs (marks
    // them dirty via the existing input handlers) and apply pending
    // highlights. Runs cheaply — both lists are usually empty.
    function restorePass() {
        var st = _readStash();
        if (st) {
            var left = false;
            Object.keys(st.cells).forEach(function (dp) {
                var el = visibleEl(dp);
                if (el && el.tagName === "INPUT" && !el.readOnly) {
                    el.value = st.cells[dp];
                    el.dispatchEvent(new Event("input", { bubbles: true }));
                    delete st.cells[dp];
                } else if (!el) {
                    left = true;      // page without this input — keep for later
                }
            });
            if (!left || !Object.keys(st.cells).length) clearStash();
            else {
                try {
                    sessionStorage.setItem(STASH_KEY, JSON.stringify(st));
                } catch (e) {}
            }
        }
        if (_pendingHighlight.length) {
            var now = Date.now();
            _pendingHighlight = _pendingHighlight.filter(function (h) {
                if (now - h.ts > 8000) return false;     // stale — stop trying
                var el = visibleEl(h.dp);
                if (el) { flash(el); return false; }
                return true;
            });
        }
    }

    function _pend(paths) {
        var ts = Date.now();
        _pendingHighlight = paths.map(function (dp) { return { dp: dp, ts: ts }; });
    }

    function handle(entries) {
        entries = entries || [];
        if (!entries.length) return;
        var covered = entries.filter(function (e) {
            return e && visibleEl(e.dot_path);
        });
        if (covered.length) {
            // The undone value is right here — flash it NOW, and keep it
            // pending so the async grid re-GET (quam:state-changed) can't
            // swallow the highlight mid-swap.
            covered.forEach(function (e) { flash(visibleEl(e.dot_path)); });
            _pend(covered.map(function (e) { return e.dot_path; }));
            return;
        }
        var os = ownerSurface(entries);
        _pend(entries.map(function (e) { return e.dot_path; }));
        if (os.kind === "inspector") {
            // Opens in #inspector-pane — #table-pane (and the user's typing
            // in it) is untouched; ?focus= scrolls + focuses the field.
            if (window.htmx && document.getElementById("inspector-pane")) {
                htmx.ajax("GET", os.url, {
                    target: "#inspector-pane", swap: "innerHTML" });
            }
            return;
        }
        stashDirtyInputs();
        window._undoNavAt = Date.now();     // one-shot beforeSwap-confirm bypass
        if (os.kind === "explorer") {
            if (window._navigateToExplorerPath) {
                _navigateToExplorerPath(os.path);   // nav + expand + highlight
            }
            return;
        }
        if (window.htmx && document.getElementById("table-pane")) {
            htmx.ajax("GET", os.url, { target: "#table-pane", swap: "innerHTML" });
            try {
                if (window.history && history.pushState) {
                    history.pushState({}, "", os.url.split("?")[0]);
                }
            } catch (e) {}
        } else {
            window.location.assign(os.url);
        }
    }

    document.addEventListener("htmx:afterSwap", function () { restorePass(); });
    document.addEventListener("stateRestored", function () {
        clearStash();
        _pendingHighlight = [];
    });

    return { handle: handle, visibleEl: visibleEl, ownerSurface: ownerSurface,
             stashDirtyInputs: stashDirtyInputs, restorePass: restorePass,
             clearStash: clearStash };
})();

/* ── Column History (docs/20 v2): 🕘 on a bulk-grid COLUMN HEADER ──
   Opens a centered comparison panel: rows = entities, first column = the
   Param-History-style trend sparkline, then the current value and the last
   N matching runs. Value click / per-run "Use all" fill the grid cells
   (LiveEditUndo-recorded; staging stays user-explicit via the normal
   Enter / Apply All flow). Body-mounted (swap-proof), plot-apply-popup
   structure + trapFocus. */
window.ColumnHistory = (function () {
    var overlay = null;
    var _paths = {};       // row_id → dot_path (as POSTed)
    var _label = "";

    function _esc(s) {
        return (window.CSS && CSS.escape) ? CSS.escape(s) : s;
    }

    function ensureOverlay() {
        if (overlay) return overlay;
        overlay = document.createElement("div");
        // `colhist-overlay` is what the Ctrl+Z carve-out keys on: inside THIS
        // panel LiveEditUndo owns the keystroke. The bare `.ch-overlay` shell
        // is shared with three other dialogs that must keep native text undo.
        overlay.className = "ch-overlay colhist-overlay";
        overlay.style.display = "none";
        var backdrop = document.createElement("div");
        backdrop.className = "ch-backdrop";
        backdrop.addEventListener("click", close);
        var card = document.createElement("div");
        card.className = "ch-card";
        card.setAttribute("role", "dialog");
        card.setAttribute("aria-modal", "true");
        overlay.appendChild(backdrop);
        overlay.appendChild(card);
        document.body.appendChild(overlay);
        return overlay;
    }

    function open(btn) {
        var th = btn.closest("th");
        var table = btn.closest("table");
        if (!th || !table) return;
        var colKey = th.getAttribute("data-col-key") || "";
        var grid = btn.getAttribute("data-grid") || "qubit";
        _label = btn.getAttribute("data-label") || colKey;
        _paths = {};
        table.querySelectorAll("tbody tr[data-qubit]").forEach(function (tr) {
            var row = tr.getAttribute("data-qubit");
            var input = tr.querySelector(
                'td[data-col-key="' + _esc(colKey) + '"] .bulk-cell');
            if (row && input && input.dataset.dotPath) {
                _paths[row] = input.dataset.dotPath;
            }
        });
        if (!Object.keys(_paths).length) {
            if (window.showToast) showToast("No history-capable cells in this column");
            return;
        }
        var o = ensureOverlay();
        var card = o.querySelector(".ch-card");
        card.innerHTML = '<p class="ch-empty">Loading column history…</p>';
        o.style.display = "flex";
        if (window.trapFocus) o._releaseTrap = window.trapFocus(card, close);
        var body = new URLSearchParams();
        body.set("grid", grid);
        body.set("col_key", colKey);
        body.set("label", _label);
        body.set("unit", btn.getAttribute("data-unit") || "");
        body.set("paths", JSON.stringify(_paths));
        fetch("/bulk/column-history", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: body.toString(),
        })
            .then(function (r) { return r.text(); })
            .then(function (html) {
                card.innerHTML = html;
                if (window.htmx) window.htmx.process(card);
                _applyView(card);
            })
            .catch(function () {
                card.innerHTML =
                    '<p class="ch-empty">Could not load column history.</p>';
            });
    }

    function close() {
        if (!overlay) return;
        if (overlay._releaseTrap) {
            try { overlay._releaseTrap(); } catch (e) {}
            overlay._releaseTrap = null;
        }
        overlay.style.display = "none";
    }

    // r9: Changes (default) ⇄ By run tab, remembered across opens. Applied
    // synchronously right after inject — no flicker; no-ops on the empty /
    // error branches (no .ch-view nodes there).
    var VIEW_KEY = "quam_colhist_view";
    function _applyView(card) {
        var v = safeLSGet(VIEW_KEY) === "byrun" ? "byrun" : "changes";
        card.querySelectorAll(".ch-view").forEach(function (sec) {
            sec.hidden = !sec.classList.contains("ch-view-" + v);
        });
        card.querySelectorAll(".ch-tab").forEach(function (b) {
            b.setAttribute("aria-pressed",
                b.getAttribute("data-view") === v ? "true" : "false");
        });
    }
    function switchView(btn) {
        safeLSSet(VIEW_KEY, btn.getAttribute("data-view") || "changes");
        var card = btn.closest(".ch-card");
        if (card) _applyView(card);
    }

    function _gridInput(row) {
        var dp = _paths[row];
        if (!dp) return null;
        try {
            return document.querySelector(
                '.bulk-cell[data-dot-path="' + _esc(dp) + '"]');
        } catch (e) { return null; }
    }

    function _fill(input, value) {
        input.value = value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.classList.add("leu-flash");
        setTimeout(function () { input.classList.remove("leu-flash"); }, 650);
    }

    function useValue(td) {
        var row = td.getAttribute("data-row");
        var v = td.getAttribute("data-fill");
        if (!row || v === null) return;
        var input = _gridInput(row);
        if (!input || input.readOnly) {
            if (window.showToast) showToast(row + ": cell is not editable here");
            return;
        }
        if (input.value === v) {
            if (window.showToast) showToast(row + ": already this value");
            return;
        }
        if (window.LiveEditUndo) {
            LiveEditUndo.record("column fill (" + row + " " + _label + ")",
                [{ dp: input.dataset.dotPath, prev: input.value, next: v }]);
        }
        _fill(input, v);
        // Panel stays open (fill several rows in a row) — Esc/backdrop/× close.
    }

    function useAll(btn) {
        var idx = btn.getAttribute("data-run-index");
        var tableEl = btn.closest("table");
        if (idx === null || !tableEl) return;
        var cells = [];
        var filled = 0, skipped = 0;
        tableEl.querySelectorAll(
            'td.ch-val[data-run-index="' + _esc(idx) + '"][data-fill]'
        ).forEach(function (td) {
            var row = td.getAttribute("data-row");
            var v = td.getAttribute("data-fill");
            var input = row ? _gridInput(row) : null;
            if (!input || input.readOnly) { skipped++; return; }
            if (input.value === v) return;          // already there — no-op
            cells.push({ dp: input.dataset.dotPath, prev: input.value, next: v });
            _fill(input, v);
            filled++;
        });
        if (window.LiveEditUndo && cells.length) {
            LiveEditUndo.record(
                "column fill (" + filled + " cells, " + _label + ")", cells);
        }
        if (window.showToast) {
            showToast(filled
                ? "Filled " + filled + " cell(s) from this run — one Ctrl+Z "
                  + "undoes the whole fill; Enter / Apply All stages them"
                : "Nothing to fill" + (skipped ? " (" + skipped + " read-only)" : ""));
        }
    }

    return { open: open, close: close, useValue: useValue, useAll: useAll,
             switchView: switchView };
})();


/* ── docs/120 item 10: the working-state version panel ───────────────────
 *
 * Customer: "move the bookmark below Calculator and put the current state
 * working version id in its place ... clicking it lists the versions with when
 * each was updated, checkboxes to pick several -> show just the combined diff
 * -> and let a chosen state be applied to the live chip."
 *
 * This module is only the popover mechanics and the selection maths. Every
 * ACTION delegates to a surface that already exists and is already gated:
 * two ticks open the docs/84 diff workbench, three or more open the
 * differences-only column table (/diff/versions, docs/128 — the Compare hub
 * stays a link there for the hard cases), and "Go back" posts the same
 * restore-live route State History uses, with both of its independent force
 * gates intact.
 */
window.StateVersions = (function () {
    function panel() { return document.getElementById('state-version-panel'); }
    function chip() { return document.querySelector('.state-version-chip'); }

    function close() {
        var p = panel(); if (!p) return;
        p.hidden = true;
        var c = chip(); if (c) c.setAttribute('aria-expanded', 'false');
    }
    function _clampToViewport(p) {
        // The panel is CSS-anchored left:0 under its topbar chip; a chip far
        // enough right pushes the 36rem panel past the viewport edge (bug
        // report: "the panel is cut off on the right"). Nudge it back in.
        p.style.left = '';
        var r = p.getBoundingClientRect();
        var over = r.right - (window.innerWidth - 8);
        if (over > 0) p.style.left = (p.offsetLeft - over) + 'px';
        r = p.getBoundingClientRect();
        if (r.left < 8) p.style.left = (p.offsetLeft + (8 - r.left)) + 'px';
    }
    function toggle() {
        var p = panel(); if (!p) return;
        // htmx fills the panel from the same click; only visibility is ours.
        var opening = p.hidden;
        p.hidden = !opening;
        var c = chip(); if (c) c.setAttribute('aria-expanded', opening ? 'true' : 'false');
        if (opening) {
            requestAnimationFrame(function () { _clampToViewport(p); });
            setTimeout(function () {
                document.addEventListener('click', function away(e) {
                    if (p.hidden) { document.removeEventListener('click', away); return; }
                    if (p.contains(e.target) || (chip() && chip().contains(e.target))) return;
                    // The per-row Diff overlay stacks ABOVE the list;
                    // interacting with it must not dismiss the panel
                    // underneath — closing the diff should land the user
                    // back on the row they were judging. Deliberately OUR
                    // overlay only: the sync-review / live-drift overlays
                    // keep their pre-docs/128 behavior (a click there still
                    // dismisses the panel, whose content their actions can
                    // stale).
                    if (e.target.closest && e.target.closest('#version-diff-overlay')) return;
                    close();
                    document.removeEventListener('click', away);
                });
            }, 0);
        }
    }
    /* docs/126: while the panel is OPEN and an Auto-Sync session is armed,
       applies keep landing — the quick "since the previous version" table
       must follow them. The tray swaps on every flush, so ride that (the
       docs/117 observation: every commit path ends in a tray swap), debounced;
       ticked rows survive the refresh exactly like more() preserves them. */
    /* docs/132 — the changes-only filter mode. Server default is 'only'
       (users don't care about unchanged copies); the choice persists per
       browser and rides every refetch this module makes. */
    function _changesMode() {
        try { return localStorage.getItem('quam_versions_changes') || 'only'; }
        catch (e) { return 'only'; }
    }
    function _versionsUrl(limit) {
        var u = '/state/versions?changes=' + encodeURIComponent(_changesMode());
        if (limit) u += '&limit=' + encodeURIComponent(limit);
        return u;
    }
    /* One refetch used by paging, the filter toggle and both live-refresh
       listeners — ticked rows always survive by value. */
    function _refetch(limit) {
        if (!window.htmx) return;
        var keep = _checked();
        htmx.ajax('GET', _versionsUrl(limit),
                  { target: '#state-version-panel', swap: 'innerHTML' })
            .then(function () {
                keep.forEach(function (ts) {
                    var el = document.querySelector(
                        '#state-version-panel .sv-check[value="' + ts + '"]');
                    if (el) el.checked = true;
                });
                pick();
            });
    }
    function setChanges(mode) {
        try { localStorage.setItem('quam_versions_changes', mode); } catch (e) {}
        _refetch();
    }
    var _svLiveTimer = null;
    function _debouncedRefetch() {
        clearTimeout(_svLiveTimer);
        _svLiveTimer = setTimeout(function () {
            var pp = panel();
            if (!pp || pp.hidden) return;
            // Preserve the expanded page: a bare refetch rendered the
            // 40-row default, collapsing "Show more" and silently dropping
            // Compare ticks beyond the first page the moment ANY other
            // window captured a snapshot (docs/132 review).
            var shown = pp.querySelectorAll('.sv-check').length;
            _refetch(shown > 40 ? shown : undefined);
        }, 900);
    }
    // on document, not document.body: app.js runs from <head>, where body is
    // still null — and a throw here would take the whole IIFE (toggle incl.)
    // down with it. htmx events bubble to document anyway.
    document.addEventListener('htmx:afterSwap', function (evt) {
        var p = panel();
        if (!p || p.hidden) return;
        var t = evt.detail && evt.detail.target;
        // The chip's own initial fill renders the server-default filter mode;
        // if this browser chose 'all', reconcile once (data-changes carries
        // the rendered mode, so this can never loop).
        if (t && t.id === 'state-version-panel') {
            var root = p.querySelector('.state-versions');
            if (root && root.getAttribute('data-changes')
                    && root.getAttribute('data-changes') !== _changesMode()) {
                _refetch();
            }
            return;
        }
        if (!document.querySelector('.auto-apply-pill.auto-apply-on')) return;
        var isTray = t && (t.id === 'pending-tray'
            || (t.querySelector && t.querySelector('#pending-tray')));
        if (!isTray) return;
        _debouncedRefetch();
    });
    /* docs/132 — the every-page /state/drift poll dispatches
       stateHistoryChanged when the chip's history dir moved (a capture in
       ANOTHER window, the background EXP ingest, a prune). The topbar chip
       already refetches on this event; the open panel follows too. */
    document.addEventListener('stateHistoryChanged', function () {
        var p = panel();
        if (!p || p.hidden) return;
        _debouncedRefetch();
    });
    function _checked() {
        return Array.prototype.slice
            .call(document.querySelectorAll('#state-version-panel .sv-check:checked'))
            .map(function (c) { return c.value; });
    }
    /* Paging. A real chip has hundreds of versions and the first page shows 40,
       so the rest has to be reachable — but re-fetching replaces the list, and
       a user who has already ticked the version they want to compare against
       must not lose it just for scrolling further back. So the selection is
       carried across the swap and re-applied by value. */
    function more(limit) {
        _refetch(limit);
    }
    /* The button says what the current selection will actually do, rather than
       being enabled-and-then-explaining afterwards. */
    function pick() {
        var n = _checked().length;
        var btn = document.getElementById('sv-compare');
        var hint = document.getElementById('sv-hint');
        if (!btn) return;
        btn.disabled = n < 2;
        if (hint) {
            hint.textContent = n === 0 ? 'Tick two versions to see what changed.'
                : n === 1 ? 'Tick one more.'
                : n === 2 ? 'Opens the diff of these two.'
                : 'Lists what differs across all ' + n + '.';
        }
    }
    function compare(chipKey) {
        var sel = _checked();
        if (sel.length < 2) return;
        // Oldest first, so the diff reads forward in time like every other
        // before -> after surface in SM (docs/76).
        sel.sort();
        var url;
        if (sel.length === 2) {
            url = '/diff/snapshots?ts_a=' + encodeURIComponent(sel[0])
                + '&ts_b=' + encodeURIComponent(sel[1])
                + (chipKey ? '&chip_key=' + encodeURIComponent(chipKey) : '');
        } else {
            // docs/128 (customer): 3+ used to open the Compare hub — a
            // configuration surface. What the user wants at this button is
            // the ANSWER: only the differing keys, one column per version,
            // immediately. The hub stays a link on that page for the hard
            // cases (entity mapping across devices).
            url = '/diff/versions?' + sel.map(function (ts) {
                return 'ts=' + encodeURIComponent(ts);
            }).join('&')
                + (chipKey ? '&chip_key=' + encodeURIComponent(chipKey) : '');
        }
        close();
        if (window.htmx) {
            htmx.ajax('GET', url, { target: '#table-pane', swap: 'innerHTML' });
            try { history.pushState({}, '', url); } catch (e) {}
            // Manual pushState fires no htmx history event, so nothing else
            // re-derives the sidebar's active item — call the canonical
            // setter ourselves (customer, 2026-08-22: Compare didn't light).
            if (window.syncSidebarNavActive) window.syncSidebarNavActive();
        } else {
            window.location.href = url;
        }
    }
    /* ── the per-row Diff (docs/128) ─────────────────────────────────────
       "What does this version hold against now?" used to cost tick-two +
       Compare — a navigation. Each row's Diff opens THIS version vs the
       current working state in the same overlay shell + Δ language the sync
       review modal uses (docs/76/86), read-only, with none of its write
       actions. The versions panel stays open underneath (the click-away
       guard in toggle()), so closing the diff lands back on the row —
       where the ↑ Pull to Live decision is now an informed one. */
    function _diffOverlay() { return document.getElementById('version-diff-overlay'); }
    // Monotonic request token — the docs/122 stale-response class: a slow
    // cold-snapshot diff must never repaint over a newer row's (or a closed
    // overlay's) content. Same pattern as _navSeq / the plothost gens.
    var _diffGen = 0;
    function closeDiff() {
        _diffGen++;
        var o = _diffOverlay(); if (!o) return;
        o.style.display = 'none';
        if (o._releaseTrap) { o._releaseTrap(); o._releaseTrap = null; }
    }
    function diff(ts, chipKey) {
        var o = _diffOverlay();
        var host = document.getElementById('version-diff-host');
        if (!o || !host) return;
        var gen = ++_diffGen;
        host.innerHTML = '<p class="muted" style="padding:1.5rem">Comparing…</p>';
        o.style.display = 'flex';
        o._releaseTrap = window.trapFocus(o, closeDiff);
        // chip_key rides along so a press racing a chip switch in another
        // window gets the honest "open that chip first" refusal instead of
        // an answer from the wrong chip's history (docs/120: two windows
        // share one server context).
        fetch('/state/versions/' + encodeURIComponent(ts) + '/diff'
              + (chipKey ? '?chip_key=' + encodeURIComponent(chipKey) : ''))
            .then(function (r) { return r.text(); })
            .then(function (html) {
                if (gen !== _diffGen) return;
                host.innerHTML = html;
                // `.ts-local` ships visibility:hidden and is revealed only by
                // applyLocalTimes stamping data-localized. A raw fetch+
                // innerHTML fires no htmx swap event, so without this call the
                // ONE line naming which version you are looking at renders as
                // a blank gap (docs/128 review, measured in real Chrome).
                if (window.applyLocalTimes) window.applyLocalTimes(host);
                if (window.htmx) htmx.process(host);
            })
            .catch(function () {
                if (gen !== _diffGen) return;
                host.innerHTML = '<p class="muted" style="padding:1.5rem">'
                    + 'Could not compute the diff.</p>';
            });
    }
    /* ── per-value take (docs/132, feedback #7) ─────────────────────────
       The ✓ beside a value on ANY of the three compare surfaces (the
       version-diff overlay, the N-way table, the workbench's gated rows)
       stages that value into the WORKING copy through /field/edit-batch —
       the same door the sync review's ✓ uses. Nothing here touches the
       live chip: the edit lands in the Review tray, where Apply-to-live
       stays the one gated write. Unlike reviewAccept, expect_chip rides
       along (docs/120: two windows share one server context — a press must
       name the chip it believes it is editing). */
    /* Edit-before-accept (docs/132 r5): swaps the taken-side value display
       (.sv-take-src) for an inline input pre-filled with the display text —
       groupdigits is round-trip exact by contract, and /field/edit-batch
       parses strings for the target's type, so posting the typed text is
       the same door every grid cell already uses. */
    function editTake(btn) {
        var holder = btn.closest('[data-dot-path]');
        if (!holder) return;
        var existing = holder.querySelector('.sv-take-input');
        if (existing) { existing.focus(); existing.select(); return; }
        var src = holder.querySelector('.sv-take-src');
        if (!src) return;
        var input = document.createElement('input');
        input.type = 'text';
        input.className = 'sv-take-input';
        input.value = src.textContent.trim();
        input.setAttribute('aria-label', 'Edited value to accept');
        input.size = Math.max(input.value.length + 1, 8);
        src.replaceChildren(input);
        input.focus();
        input.select();
        // Enter = accept with the edited value; Escape = back to the plain
        // display (the version's own value).
        input.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') {
                ev.preventDefault();
                var t = holder.querySelector('.sv-take');
                if (t) take(t);
            } else if (ev.key === 'Escape') {
                ev.preventDefault();
                ev.stopPropagation();
                src.textContent = input.defaultValue;
            }
        });
    }
    /* ── the RAM undo stack (docs/132 r5) ────────────────────────────────
       Manual accepts are rare and few (the user's own read), so each one
       records {dot_path, prev, taken} in memory — prev being the working
       value the row itself displayed. Ctrl+Z / Ctrl+Shift+Z then step
       accepts back and forth with ONE POST each, no server group machinery.
       Scope: only while the version-diff overlay is open or a workbench
       with take rows is on screen; an empty stack falls through to the
       docs/107 global tiers. Takes onto CREATED leaves (no prev) are not
       RAM-recorded — un-creating is the server tier's job. */
    var _tkUndo = [], _tkRedo = [], _tkBusy = false;
    function _tkPost(dot, value, create, done) {
        fetch('/field/edit-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                updates: [{ dot_path: dot, value: value, create: !!create }],
                expect_chip: window.__chipToken || '' })
        })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var res = d && d.results && d.results[0];
                var ok = !!(d && d.ok && res && !res.error);
                if (ok && d.tray_html && window._swapPendingTray) {
                    window._swapPendingTray(d.tray_html);
                }
                done(ok, (res && res.error) || (d && d.error));
            })
            .catch(function () { done(false, 'network error'); });
    }
    function _tkMark(rec, accepted) {
        var h = rec.holder;
        if (!h || !h.isConnected) return;
        h.classList.toggle('review-accepted', accepted);
        var b = h.querySelector('.sv-take');
        if (b) {
            b.disabled = accepted;
            b.classList.toggle('sv-taken', accepted);
            b.textContent = accepted ? '✓ staged' : rec.origLabel;
        }
    }
    function takeUndo(redo) {
        if (_tkBusy) return true;
        var rec = (redo ? _tkRedo : _tkUndo).pop();
        if (!rec) return false;             // empty → fall through to global
        _tkBusy = true;
        _tkPost(rec.dot, redo ? rec.taken : rec.prev, redo && rec.create,
            function (ok, err) {
                _tkBusy = false;
                if (ok) {
                    (redo ? _tkUndo : _tkRedo).push(rec);
                    _tkMark(rec, !!redo);
                } else {
                    (redo ? _tkRedo : _tkUndo).push(rec);   // keep the record
                    if (window.showToast) {
                        window.showToast((redo ? 'Redo' : 'Undo')
                            + ' failed: ' + (err || 'unknown'), 'error');
                    }
                }
            });
        return true;
    }
    document.addEventListener('keydown', function (e) {
        if (!((e.ctrlKey || e.metaKey) && !e.altKey
              && (e.key === 'z' || e.key === 'Z'))) return;
        // typing INSIDE the edit input keeps the browser's own text undo
        if (e.target && e.target.classList
                && e.target.classList.contains('sv-take-input')) return;
        var o = _diffOverlay();
        var scoped = (o && o.style.display !== 'none')
            || !!document.querySelector('#diff-root [data-dot-path] .sv-take');
        if (!scoped) return;
        if (!takeUndo(e.shiftKey)) return;   // empty stack → global tiers
        // capture phase, so this preempts the bubble-phase docs/107 chain
        e.preventDefault();
        e.stopImmediatePropagation();
    }, true);
    function take(btn) {
        var holder = btn.closest('[data-dot-path]');
        if (!holder || btn.disabled) return;
        var dot = holder.getAttribute('data-dot-path');
        var input = holder.querySelector('.sv-take-input');
        var val;
        if (input) {
            // the edited text — the server parses strings for the target type
            val = input.value;
        } else {
            var raw = holder.getAttribute('data-value');
            try { val = JSON.parse(raw); } catch (e) { val = raw; }
        }
        var create = holder.getAttribute('data-create') === '1';
        var origLabel = btn.textContent;
        btn.disabled = true;
        _tkPost(dot, val, create, function (ok, err) {
            if (ok) {
                var row = btn.closest('.review-row, tr');
                if (row) row.classList.add('review-accepted');
                btn.textContent = '✓ staged';
                btn.classList.add('sv-taken');
                var prevRaw = holder.getAttribute('data-prev');
                if (prevRaw !== null) {
                    var prev;
                    try { prev = JSON.parse(prevRaw); } catch (e2) { prev = prevRaw; }
                    _tkUndo.push({ dot: dot, prev: prev, taken: val,
                                   create: create, holder: row || holder,
                                   origLabel: origLabel });
                    _tkRedo.length = 0;
                }
            } else {
                btn.disabled = false;
                // window.showToast, called THROUGH window: the bare-call
                // guard trap (CLAUDE.md standing harness rule) — caught
                // by version_diff_selfcheck the day this was written.
                if (window.showToast) {
                    window.showToast(err || 'Could not stage the value.', 'error');
                }
            }
        });
    }
    return { toggle: toggle, close: close, pick: pick, compare: compare,
             more: more, diff: diff, closeDiff: closeDiff,
             setChanges: setChanges, take: take, editTake: editTake };
})();

/* ── the top bar's REAL height (docs/120 item 23) ─────────────────────────
 *
 * `--topbar-height` is used by every `calc(100vh - var(--topbar-height))`
 * panel, and it declared 48px while the rendered bar — a wrapping <nav> —
 * measured 201px at 1600 wide, 229 at 1280 and 254 at 1024. So every main
 * panel was sized 150-200px taller than the space it had, on every page and
 * at every width, and worse on the narrow windows a laptop actually uses.
 * That is what pushed the Generate wizard's failure message below the fold
 * and let content scroll under a sticky bar the layout thought was 48px.
 *
 * A stylesheet cannot express "however tall that element turns out to be",
 * so measure it and publish it. No rule changes; they all just start being
 * given the truth.
 *
 * Two details that matter:
 *  - `html.topbar-hidden` zeroes the variable in CSS, and an inline style on
 *    <html> would BEAT that rule (inline wins over a stylesheet). So a hidden
 *    or absent bar must publish 0 here rather than leave a stale number.
 *  - writes are gated on an actual change, because this runs from a
 *    ResizeObserver and re-publishing the same value would loop.
 */
window.TopbarHeight = (function () {
    'use strict';
    var _last = null;
    function measure() {
        var tb = document.querySelector('.topbar');
        if (!tb || document.documentElement.classList.contains('topbar-hidden')) return 0;
        var r = tb.getBoundingClientRect();
        return (r.height > 0 && r.width > 0) ? Math.round(r.height) : 0;
    }
    function publish() {
        var h = measure();
        if (h === _last) return h;
        _last = h;
        document.documentElement.style.setProperty('--topbar-height', h + 'px');
        return h;
    }
    function start() {
        publish();
        var tb = document.querySelector('.topbar');
        if (tb && window.ResizeObserver) {
            try { new ResizeObserver(function () { publish(); }).observe(tb); }
            catch (e) { /* older engine — the resize listener below still runs */ }
        }
        window.addEventListener('resize', publish);
        // The bar's contents change with the chip (badges, project chip, the
        // Auto-Sync pill), and htmx swaps them in without a resize event.
        // Bound to `document`, not `document.body`: app.js is loaded in <head>
        // and `document.body` is null there — the existing
        // `test_app_js_no_top_level_document_body` pin caught exactly that.
        // htmx events bubble to document, so nothing is lost.
        document.addEventListener('htmx:afterSwap', function () { publish(); });
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
    return { publish: publish, measure: measure };
})();

/* ── hx-on without eval (docs/120 item 27) ────────────────────────────────
 *
 * The app sets its own Content-Security-Policy, and it deliberately does NOT
 * include 'unsafe-eval'. htmx compiles every `hx-on::…` attribute with
 * `new Function("event", body)`, so under our own policy that compile throws:
 * EVERY `hx-on::after-request` in this codebase silently never ran, and the
 * console carried one CSP violation per page as the only sign.
 *
 * Seven handlers were affected, and one of them had already been reported as a
 * bug by hand: the Auto-Sync popup did not close after Save. That was patched
 * defensively before the cause was known — this is the cause.
 *
 * Verifying it needs care, and two of my probes lied before one held up. A
 * `new Function` evaluated from a debugger-injected script returns a value
 * quite happily, so "eval works here" proves nothing about what the page's own
 * htmx can do. What settled it, and what a future check should repeat: attach
 * an `hx-on::after-request` that writes into the DOM, fire the request, and
 * read the DOM back — it stayed unwritten while a CSP `new Function` violation
 * appeared. After this change the same probe on a `data-after-request` handler
 * writes, and the violation count on the page goes 1 -> 0.
 *
 * The fix keeps every behaviour and removes the eval: a data attribute names
 * an action, one delegated listener runs it. Greppable, no mini-language, and
 * a typo'd name fails loudly here rather than silently in a compile nobody
 * sees.
 */
(function () {
    'use strict';
    var ACTIONS = {
        /* the archive form: reset + flash the status line on success */
        archiveDone: function (el, ev) {
            var xhr = ev && ev.detail && ev.detail.xhr;
            if (!xhr || String(xhr.responseText || '').indexOf('archive-ok') < 0) return;
            if (el.reset) el.reset();
            var s = document.getElementById('archive-status');
            if (!s) return;
            s.classList.remove('archive-flash');
            void s.offsetWidth;                       // restart the animation
            s.classList.add('archive-flash');
            if (window.applyLocalTimes) window.applyLocalTimes(s);
        },
        autoSyncClose: function () {
            if (window.AutoSync && AutoSync.close) AutoSync.close();
        },
        dropApplyConflict: function (el, ev) {
            if (!(ev && ev.detail && ev.detail.successful)) return;
            var box = el.closest('.ds-apply-conflict');
            if (box) box.remove();
        },
        clearUndo: function () {
            if (window.LiveEditUndo) LiveEditUndo.clear();
        },
        closeReviewAndClearUndo: function () {
            if (window.closeReview) window.closeReview();
            if (window.LiveEditUndo) LiveEditUndo.clear();
        }
    };
    document.addEventListener('htmx:afterRequest', function (ev) {
        var el = ev.target;
        if (!el || !el.getAttribute) return;
        var name = el.getAttribute('data-after-request');
        if (!name) return;
        var fn = ACTIONS[name];
        if (!fn) {
            if (window.console) console.warn('unknown data-after-request:', name);
            return;
        }
        try { fn(el, ev); }
        catch (e) { if (window.console) console.warn('after-request ' + name, e); }
    });
    window.__afterRequestActions = ACTIONS;   // named so a pin can enumerate them
})();

/* ── docs/126 ④: Json Tree quick patches ──────────────────────────────────
   The Live-Edit patch idea, on the tree: curated terms that actually OCCUR in
   this chip's documents (honesty — never a chip that matches nothing) + the
   user's own saved patches, from the SAME store Live Edit writes
   (quam_bulk_custom_chips — "decouple" registered once serves both surfaces).
   Click → the term joins/leaves #explorer-search (space = AND, the tree
   grammar) and the tree filters through window.explorerSearch. */
window.ExplorerChips = (function () {
    var CUSTOM_KEY = 'quam_bulk_custom_chips';
    var CURATED = [
        ['freq', 'Freq'], ['readout', 'Readout'], ['resonator', 'Resonator'],
        ['flux', 'Flux'],
        // docs/136 — QDAC-II bias is a component of its own. Here the honesty
        // gate is the raw document haystack, so this chip renders on a chip
        // whose state carries the top-level `qdac` instrument or a
        // QdacBiasLine `__class__`; Live Edit reaches the same word through
        // its columns' section + search text. Both surfaces or neither.
        ['qdac', 'QDAC'],
        ['coupler', 'Coupler'], ['amp', 'Amp'],
        ['power', 'Power'], ['length', 'Length'], ['delay', 'Delay'],
        ['offset', 'Offset'], ['filter', 'Filter'], ['phase', 'Phase'],
        ['port', 'Port'],
    ];
    function _custom() {
        try {
            var a = JSON.parse(localStorage.getItem(CUSTOM_KEY) || '[]');
            return Array.isArray(a) ? a.filter(function (t) {
                return typeof t === 'string' && /^[^\s|]{1,40}$/.test(t);
            }) : [];
        } catch (e) { return []; }
    }
    function _saveCustom(list) {
        try { localStorage.setItem(CUSTOM_KEY, JSON.stringify(list)); } catch (e) {}
    }
    function _input() { return document.getElementById('explorer-search'); }
    function _tokens() {
        var el = _input();
        return el ? el.value.trim().split(/\s+/).filter(Boolean) : [];
    }
    function _apply(value) {
        var el = _input(); if (!el) return;
        el.value = value;
        if (window.explorerSearch) window.explorerSearch(value);
    }
    function _toggle(bar, term) {
        var toks = _tokens();
        var i = toks.map(function (t) { return t.toLowerCase(); }).indexOf(term);
        if (i >= 0) toks.splice(i, 1); else toks.push(term);
        _apply(toks.join(' '));
        _paint(bar);
    }
    function _paint(bar) {
        var lit = {};
        _tokens().forEach(function (t) { lit[t.toLowerCase()] = 1; });
        bar.querySelectorAll('.bulk-chip[data-chip-term]').forEach(function (b) {
            var on = !!lit[b.getAttribute('data-chip-term')];
            b.classList.toggle('active', on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }
    function _render(bar, hay) {
        bar.innerHTML = '';
        var seen = {};
        CURATED.forEach(function (c) {
            if (hay.indexOf(c[0]) < 0) return;   // term occurs nowhere on this chip
            seen[c[0]] = 1;
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'bulk-chip';
            b.setAttribute('data-chip-term', c[0]);
            b.setAttribute('aria-pressed', 'false');
            b.textContent = c[1];
            bar.appendChild(b);
        });
        _custom().forEach(function (t) {
            if (seen[t]) return;
            var b = document.createElement('button');
            b.type = 'button';
            b.className = 'bulk-chip bulk-chip-custom';
            b.setAttribute('data-chip-term', t);
            b.setAttribute('aria-pressed', 'false');
            b.title = 'your saved filter patch — × removes it';
            b.textContent = t;
            var x = document.createElement('span');
            x.className = 'bulk-chip-x';
            x.textContent = '×';
            b.appendChild(x);
            bar.appendChild(b);
        });
        var add = document.createElement('button');
        add.type = 'button';
        add.className = 'bulk-chip bulk-chip-add';
        add.title = 'Save your own filter word as a patch (shared with Live Edit)';
        add.textContent = '+';
        bar.appendChild(add);
    }
    function mount(barId, docsArr) {
        var bar = document.getElementById(barId);
        if (!bar) return;
        var hay = '';
        try { hay = JSON.stringify(docsArr).toLowerCase(); } catch (e) {}
        bar._hay = hay;
        _render(bar, hay);
        _paint(bar);
        if (!bar._chipWired) {
            bar._chipWired = true;
            bar.addEventListener('click', function (e) {
                var t = e.target;
                if (!t || !t.classList) return;
                if (t.classList.contains('bulk-chip-x')) {
                    var term = t.parentNode.getAttribute('data-chip-term');
                    _saveCustom(_custom().filter(function (x) { return x !== term; }));
                    var toks = _tokens().filter(function (tk) { return tk.toLowerCase() !== term; });
                    _apply(toks.join(' '));
                    _render(bar, bar._hay || '');
                    _paint(bar);
                } else if (t.classList.contains('bulk-chip-add')) {
                    if (bar.querySelector('.bulk-chip-add-input')) return;
                    var inp = document.createElement('input');
                    inp.className = 'bulk-chip-add-input';
                    inp.placeholder = 'new patch…';
                    inp.setAttribute('aria-label', 'New filter patch');
                    bar.insertBefore(inp, t);
                    inp.focus();
                    var commit = function () {
                        var v = inp.value.trim().toLowerCase();
                        if (inp.parentNode) inp.parentNode.removeChild(inp);
                        if (!/^[^\s|]{1,40}$/.test(v)) return;
                        var cur = _custom();
                        if (cur.indexOf(v) < 0) { cur.push(v); _saveCustom(cur); }
                        _render(bar, bar._hay || '');
                        var toks = _tokens();
                        if (toks.map(function (x) { return x.toLowerCase(); }).indexOf(v) < 0) toks.push(v);
                        _apply(toks.join(' '));
                        _paint(bar);
                    };
                    inp.addEventListener('keydown', function (ke) {
                        if (ke.key === 'Enter') { ke.preventDefault(); commit(); }
                        else if (ke.key === 'Escape') {
                            if (inp.parentNode) inp.parentNode.removeChild(inp);
                        }
                    });
                    inp.addEventListener('blur', function () {
                        setTimeout(function () { if (inp.parentNode) commit(); }, 120);
                    });
                } else if (t.classList.contains('bulk-chip')) {
                    _toggle(bar, t.getAttribute('data-chip-term'));
                }
            });
            // hand-typing a patch's word lights its chip
            var el = _input();
            if (el && !el._chipPaint) {
                el._chipPaint = true;
                el.addEventListener('input', function () { _paint(bar); });
            }
        }
    }
    return { mount: mount };
})();

/* ── docs/126 ⑥: the floating Instrument Wiring panel ─────────────────────
   The customer wants a port picture ALWAYS in view while working elsewhere —
   today the diagram only exists as the main pane. The ⧉ beside the sidebar
   item opens this body-level panel: the SAME renderInstrumentWiring drawing
   fed by /api/instrument/data, draggable by its header, collapsible to the
   header bar, resizable (CSS resize), position/size/collapse persisted.
   Read-only by design — hover details land in the panel's own footer strip
   (the cursor popup and the JSON drill-down belong to the main page). */
window.FloatWiring = (function () {
    var KEY = 'quam_float_wiring';
    function _geo() {
        try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; }
        catch (e) { return {}; }
    }
    function _save(patch) {
        var g = _geo();
        Object.keys(patch).forEach(function (k) { g[k] = patch[k]; });
        try { localStorage.setItem(KEY, JSON.stringify(g)); } catch (e) {}
    }
    function panel() { return document.getElementById('float-wiring'); }

    function refresh() {
        var host = document.getElementById('float-wiring-diagram');
        if (!host) return;
        host.innerHTML = '<p class="muted" style="padding:.6rem">loading…</p>';
        fetch('/api/instrument/data', { cache: 'no-store' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var h = document.getElementById('float-wiring-diagram');
                if (!h) return;
                if (!d || d.error) {
                    h.innerHTML = '';
                    var pe = document.createElement('p');
                    pe.className = 'muted'; pe.style.padding = '.6rem';
                    pe.textContent = 'Wiring unavailable: ' + ((d && d.error) || 'no data');
                    h.appendChild(pe);
                    return;
                }
                window.renderInstrumentWiring('float-wiring-diagram',
                    d.instrument, d.wiring, {
                        onPortHover: function (a) {
                            var f = document.getElementById('float-wiring-status');
                            if (!f) return;
                            f.textContent = a
                                ? (a.label || '') + (a.role ? ' · ' + a.role : '')
                                : '';
                        },
                    });
            })
            .catch(function () {
                var h = document.getElementById('float-wiring-diagram');
                if (h) h.innerHTML = '<p class="muted" style="padding:.6rem">Could not load the wiring data.</p>';
            });
    }

    function _applyCollapsed(p, on) {
        p.classList.toggle('fw-collapsed', !!on);
        var b = p.querySelector('.fw-collapse');
        if (b) { b.textContent = on ? '▸' : '▾'; b.title = on ? 'Expand' : 'Collapse to the title bar'; }
    }

    function open() {
        if (panel()) return;
        var p = document.createElement('div');
        p.id = 'float-wiring';
        p.className = 'float-wiring';
        p.innerHTML =
            '<div class="fw-head">'
            + '<span class="fw-title">Instrument Wiring</span>'
            + '<span id="float-wiring-status" class="fw-status muted"></span>'
            + '<button type="button" class="fw-btn fw-refresh" title="Reload from the open chip">↻</button>'
            + '<button type="button" class="fw-btn fw-collapse" title="Collapse to the title bar">▾</button>'
            + '<button type="button" class="fw-btn fw-close" title="Close">✕</button>'
            + '</div>'
            + '<div class="fw-body"><div id="float-wiring-diagram"></div></div>';
        document.body.appendChild(p);
        var g = _geo();
        if (typeof g.x === 'number' && typeof g.y === 'number') {
            p.style.left = Math.max(0, Math.min(g.x, window.innerWidth - 120)) + 'px';
            p.style.top = Math.max(0, Math.min(g.y, window.innerHeight - 60)) + 'px';
            p.style.right = 'auto'; p.style.bottom = 'auto';
        }
        if (typeof g.w === 'number') p.style.width = g.w + 'px';
        if (typeof g.h === 'number') p.style.height = g.h + 'px';
        _applyCollapsed(p, !!g.collapsed);

        p.querySelector('.fw-close').onclick = function () { p.remove(); };
        p.querySelector('.fw-refresh').onclick = refresh;
        p.querySelector('.fw-collapse').onclick = function () {
            var on = !p.classList.contains('fw-collapsed');
            _applyCollapsed(p, on);
            _save({ collapsed: on });
        };
        // drag by the header (buttons excluded)
        var head = p.querySelector('.fw-head');
        head.addEventListener('pointerdown', function (e) {
            if (e.target.closest && e.target.closest('.fw-btn')) return;
            var r = p.getBoundingClientRect();
            var dx = e.clientX - r.left, dy = e.clientY - r.top;
            function mv(ev) {
                p.style.left = Math.max(0, ev.clientX - dx) + 'px';
                p.style.top = Math.max(0, ev.clientY - dy) + 'px';
                p.style.right = 'auto'; p.style.bottom = 'auto';
            }
            function up(ev) {
                document.removeEventListener('pointermove', mv);
                document.removeEventListener('pointerup', up);
                var r2 = p.getBoundingClientRect();
                _save({ x: r2.left, y: r2.top });
            }
            document.addEventListener('pointermove', mv);
            document.addEventListener('pointerup', up);
            e.preventDefault();
        });
        // persist a CSS resize (the handle fires no event — sample on pointerup)
        p.addEventListener('pointerup', function () {
            var r = p.getBoundingClientRect();
            if (r.width > 80 && r.height > 40) _save({ w: r.width, h: r.height });
        });
        refresh();
    }

    function toggle() {
        var p = panel();
        if (p) p.remove(); else open();
    }

    // a wholesale working-copy replacement (chip switch, stage, pull) can
    // change the wiring the panel shows — refresh it in place
    document.addEventListener('stateRestored', function () { if (panel()) refresh(); });
    return { toggle: toggle, open: open, refresh: refresh };
})();
