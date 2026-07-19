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
        expandedSizes:  [35, 65],  /* DEFAULT "expanded" target — inspector gets 65% (spacious) while the upper table stays visible (35%), so clicking a detail no longer makes the table vanish. Users who want a full cover can still set it via the gutter ⤒ set-icon → localStorage "quam_split_expanded" (incl. [0,100]). */
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
            edgeFidelityGood: '#2ca02c',  /* edge color when CZ fidelity >= 95% — green */
            edgeFidelityWarn: '#ff7f0e',  /* edge color when CZ fidelity >= 85% — orange */
            edgeFidelityBad:  '#d62728',  /* edge color when CZ fidelity < 85% — red */
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
                   GnBu 5-stop: light mint → teal → dark blue.
                   Change this one array to re-skin every heatmap at once.       */
                colorScale: ['#e0f3db', '#a8ddb5', '#7bccc4', '#43a2ca', '#0868ac'],
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

    /* ── TOPOLOGY LIVE UPDATE ─────────────────────────────────────────
     * How often (in seconds) the topology page polls the server to
     * check if state.json / wiring.json have been modified on disk.
     * The check is a pair of stat() calls (~microseconds), so short
     * intervals are fine.  Set to 0 to disable polling entirely.     */
    topoLivePollInterval: 3,

    /* ── LIVE-DRIFT TRACKING ──────────────────────────────────────────
     * How often (in seconds) every page polls /state/drift to refresh the
     * accumulating "Live changes since baseline" banner. The server gates
     * the work on a pair of stat() calls (no content read unless the live
     * files actually moved), so short intervals are cheap. 0 disables it. */
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
document.addEventListener('htmx:beforeSwap', function(evt) {
    if (!evt.detail) return;
    // No swap will happen (htmx drops 4xx/5xx bodies → shouldSwap=false), so the
    // container keeps its current content — purging its live plots here would
    // blank them with nothing to replace them (a failed inspector-pane load used
    // to wipe the visible figures). Only tear down when the swap is real.
    if (evt.detail.shouldSwap === false) return;
    if (typeof Plotly === 'undefined') return;
    var el = evt.detail.target || evt.detail.elt;   // the container being replaced
    if (!el || !el.querySelectorAll) return;
    var plots = el.querySelectorAll('.js-plotly-plot');
    for (var i = 0; i < plots.length; i++) {
        try { Plotly.purge(plots[i]); } catch(e) {}
    }
});

/* Tear down any still-zoomed figure inside a swapped-out container. toggleFigureZoom
   attaches two capture-phase document listeners (Esc + outside-pointerdown) cleaned up
   by imgEl._zoomCleanup; if an htmx swap detaches the zoomed <img> while it is still
   zoomed, that cleanup would otherwise never run and the listeners would dangle on a
   detached node until the next pointer/key event. Run it deterministically here. */
document.addEventListener('htmx:beforeSwap', function(evt) {
    if (!evt.detail) return;
    if (evt.detail.shouldSwap === false) return;   // no swap → don't tear down (see above)
    var el = evt.detail.target || evt.detail.elt;   // the container being replaced
    if (!el || !el.querySelectorAll) return;
    var zoomed = el.querySelectorAll('img.figure-zoomed');
    for (var i = 0; i < zoomed.length; i++) {
        if (typeof zoomed[i]._zoomCleanup === 'function') zoomed[i]._zoomCleanup();
    }
});

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
        var inner = div.querySelector('.js-plotly-plot');
        if (inner && typeof Plotly !== 'undefined') Plotly.purge(inner);
    } catch (e) {}
    div.innerHTML = '';
    div.setAttribute('data-rendered', '0');
}

// Purge the least-recently-rendered OFFSCREEN tiles until within budget.
// A hard ceiling (2× the soft budget) so a tall / 1-column layout that keeps many
// tiles on-screen at once can't blow past the heap budget — above it we purge the
// oldest tile even if visible (it re-renders on the next observer tick).
var INTERACTIVE_RENDER_HARD_CAP = INTERACTIVE_RENDER_BUDGET * 2;

function _pruneInteractiveTiles(container) {
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
    // Hard cap: too many visible-and-rendered tiles → drop the oldest regardless.
    while (rendered.length > INTERACTIVE_RENDER_HARD_CAP) {
        var old = rendered.shift();
        if (old && old.getAttribute('data-rendered') === '1') _purgeInteractiveTile(old);
    }
}

// When a dataset pane is swapped out, disconnect its interactive observer and
// bump its generation so any in-flight tile fetch drops instead of painting
// into a detached/reused container. Scoped to the swap target (not evt.elt),
// so it catches the inspector pane on a dataset switch.
document.addEventListener('htmx:beforeSwap', function(evt) {
    if (!evt.detail) return;
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

    // Capture date <details> open state keyed by "rootPath::dateLabel"
    _sidebarSticky.dates = {};
    tree.querySelectorAll('details.tree-root').forEach(function(root) {
        var rootLabel = root.querySelector('.tree-root-label span');
        var rootKey = rootLabel ? (rootLabel.getAttribute('title') || rootLabel.textContent.trim()) : '';
        root.querySelectorAll('details.tree-date').forEach(function(d) {
            var dateLabel = d.querySelector('.tree-date-label');
            if (dateLabel) {
                var dateKey = rootKey + '::' + dateLabel.textContent.trim().split('(')[0].trim();
                _sidebarSticky.dates[dateKey] = d.open;
            }
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

    // Restore date <details> open state
    tree.querySelectorAll('details.tree-root').forEach(function(root) {
        var rootLabel = root.querySelector('.tree-root-label span');
        var rootKey = rootLabel ? (rootLabel.getAttribute('title') || rootLabel.textContent.trim()) : '';
        root.querySelectorAll('details.tree-date').forEach(function(d) {
            var dateLabel = d.querySelector('.tree-date-label');
            if (dateLabel) {
                var dateKey = rootKey + '::' + dateLabel.textContent.trim().split('(')[0].trim();
                if (dateKey in _sidebarSticky.dates) d.open = _sidebarSticky.dates[dateKey];
            }
        });
    });

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
            var aText = (a.cells[col] ? a.cells[col].textContent.trim() : '');
            var bText = (b.cells[col] ? b.cells[col].textContent.trim() : '');
            if (isNum) {
                var aVal = parseFloat(aText.replace(/[^0-9eE.\-+]/g, '')) || 0;
                var bVal = parseFloat(bText.replace(/[^0-9eE.\-+]/g, '')) || 0;
                if (aText === '-' || aText === '') aVal = -Infinity;
                if (bText === '-' || bText === '') bVal = -Infinity;
                return dir === 'asc' ? aVal - bVal : bVal - aVal;
            }
            return dir === 'asc' ? aText.localeCompare(bText) : bText.localeCompare(aText);
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
window.toggleSettings = function() {
    var dd = document.getElementById("settings-dropdown");
    if (!dd) return;
    var opening = dd.classList.toggle("settings-hidden");
    if (!opening) {
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
    // Update Plotly topology colors if applicable
    if (window.UI_CONFIG && UI_CONFIG.plotly && UI_CONFIG.plotly.topology) {
        var t = UI_CONFIG.plotly.topology;
        if (active) {
            t.edgeFidelityGood = '#0571b0';
            t.edgeFidelityWarn = '#ca6500';
            t.edgeFidelityBad = '#92440a';
        } else {
            t.edgeFidelityGood = '#2ca02c';
            t.edgeFidelityWarn = '#ff7f0e';
            t.edgeFidelityBad = '#d62728';
        }
    }
};

// Restore colorblind mode on page load
(function() {
    try {
        if (localStorage.getItem('quam_colorblind') === '1') {
            document.body.classList.add('colorblind-mode');
            var btn = document.getElementById('colorblind-toggle');
            if (btn) btn.classList.add('settings-opt-active');
            if (window.UI_CONFIG && UI_CONFIG.plotly && UI_CONFIG.plotly.topology) {
                var t = UI_CONFIG.plotly.topology;
                t.edgeFidelityGood = '#0571b0';
                t.edgeFidelityWarn = '#ca6500';
                t.edgeFidelityBad = '#92440a';
            }
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
    } else if (window.htmx) {
        window.htmx.ajax('GET', '/topology?view=' + view,
                         { target: '#table-pane', swap: 'innerHTML' }).then(function() {
            try { history.pushState(null, '', '/topology?view=' + view); } catch (e) {}
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

// Restore each sub-list's collapsed state on load. Chip Status defaults
// expanded, the Config group defaults collapsed (the server also renders it
// collapsed, so JS only ever *removes* the class — no flash). A sub-list
// holding the active page is force-expanded regardless of the stored state.
(function() {
    var SUBNAVS = [
        { id: 'chip-status-subnav', key: 'quam_chipstatus_nav_collapsed', def: '0' },
        { id: 'config-subnav',      key: 'quam_config_nav_collapsed',     def: '1' },
        { id: 'pulses-subnav',      key: 'quam_pulses_nav_collapsed',     def: '1' },
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
    var entries = Array.prototype.filter.call(
        document.querySelectorAll('.tree-entry-click[data-uid]'),
        function(e) { return e.offsetParent !== null; });   // visible only
    if (!entries.length) return;
    var idx = -1;
    for (var i = 0; i < entries.length; i++) {
        if (entries[i].getAttribute('data-uid') === curUid) { idx = i; break; }
    }
    var next = entries[idx === -1 ? 0 : idx + dir];
    if (!next) return;   // at either end
    next.scrollIntoView({block: 'nearest'});
    next.click();
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
   already rebuilt every derived cache; this is purely the client catch-up. */
document.addEventListener("stateRestored", function() {
    if (window.closeInspector) window.closeInspector();
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

/**
 * Trap keyboard focus inside `container` until released. Tab/Shift+Tab cycle
 * within the modal; Escape calls `onEscape` (if given). Returns a release()
 * that detaches the handler and restores focus to whatever was focused when
 * the trap was set (the opener). Stored on `container._releaseTrap` by callers.
 */
window.trapFocus = function(container, onEscape) {
    if (!container) return function() {};
    var opener = document.activeElement;
    function onKey(e) {
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
    document.addEventListener("keydown", onKey, true);
    requestAnimationFrame(function() {
        // Don't override focus the caller already placed inside the modal.
        if (container.contains(document.activeElement)) return;
        var f = _focusableIn(container);
        try { (f[0] || container).focus(); } catch (e) {}
    });
    return function release() {
        document.removeEventListener("keydown", onKey, true);
        if (opener && typeof opener.focus === "function" && document.body.contains(opener)) {
            try { opener.focus(); } catch (e) {}
        }
    };
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

window.doStateSync = function(mode) {
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
    fetch("/state/sync", {
        method: "POST",
        headers: {"Content-Type": "application/x-www-form-urlencoded", "HX-Request": "true"},
        body: "mode=" + encodeURIComponent(mode)
    })
        .then(function(r) { return r.json(); })
        .then(function(data) {
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
                _softRefreshLiveSurface();
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
    } else if (window.showToast) {
        window.showToast("Nothing to apply — your edits already match the live chip.", "info");
    }
};

/* ------------------------------------------------------------------ */
/* Live-drift tracking — accumulating "Live changes since baseline"    */
/* ------------------------------------------------------------------ */

/* A persistent, global comparison of the live chip against a baseline that
 * survives the working-copy auto-sync. A watch-only user (most users) runs
 * qualibrate fit after fit without touching SM; the working copy keeps
 * auto-adopting the new live, which used to silently absorb the diff. This
 * polls /state/drift (mtime-gated server-side) on every page and keeps a
 * running banner of how many params the live chip changed since the baseline.
 * "View changes" opens the full before/after overlay; "Reset baseline"
 * acknowledges them all and starts fresh. The same data is embedded at the
 * top of the State History page. */
(function () {
    var POLL_MS = ((window.UI_CONFIG && UI_CONFIG.driftPollInterval) || 0) * 1000;
    var _lastCount = null;        // last count we rendered (for change detection)
    var _dismissedAt = 0;         // count value at which the user dismissed the banner
    var _shownCount = 0;          // count currently rendered in the banner (skip re-render churn)
    var _driftHideTimer = null;   // auto-dismiss timer for the live-drift banner
    function _clearDriftHideTimer() {
        if (_driftHideTimer) { clearTimeout(_driftHideTimer); _driftHideTimer = null; }
    }

    function _fmtBaseline(iso) {
        if (!iso) return "";
        // Emit a ts-local span (matches the C2 filter): the JS-off fallback is the UTC
        // text, and applyLocalTimes() converts data-utc → the user's local time, so the
        // persistent banner reads in local time like the _live_drift.html panel — not
        // "since … UTC" next to a panel showing the same instant localized (audit P1).
        var norm = String(iso).slice(0, 19);   // YYYY-MM-DDTHH:MM:SS
        return '<span class="ts-local" data-utc="' + norm + 'Z">'
            + norm.replace("T", " ") + ' UTC</span>';
    }

    function _esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function _renderBanner(d) {
        var slot = document.getElementById("live-drift-slot");
        if (!slot) return;
        var count = (d && d.ok && d.tracked) ? (d.count || 0) : 0;
        if (!count) { slot.innerHTML = ""; _dismissedAt = 0; _shownCount = 0; _clearDriftHideTimer(); return; }
        // Re-show on any NEW change beyond what was dismissed.
        if (count > _dismissedAt) _dismissedAt = 0;
        if (_dismissedAt && count <= _dismissedAt) { slot.innerHTML = ""; _shownCount = 0; _clearDriftHideTimer(); return; }
        // Already showing this exact count → leave the banner (and its running
        // auto-dismiss timer) alone; the 5s poll must not reset either.
        if (count === _shownCount) return;
        _shownCount = count;
        var when = _fmtBaseline(d.baseline_utc);
        slot.innerHTML =
            '<div class="topo-change-banner live-drift-banner" role="status">' +
              '<span class="topo-change-banner-text">' +
                '&#128202; <b>' + count + '</b> parameter' + (count === 1 ? '' : 's') +
                ' changed on the live chip' +
                (when ? ' since <span class="muted">' + when + '</span>' : '') +
              '</span>' +
              '<button type="button" class="topo-change-banner-btn" onclick="openDrift()">View changes</button>' +
              '<button type="button" class="btn-sm outline" onclick="resetBaseline()" ' +
                'title="Acknowledge all current changes and start accumulating fresh">Reset baseline</button>' +
              '<button type="button" class="topo-change-banner-dismiss" aria-label="Dismiss" ' +
                'onclick="window._dismissDrift(' + count + ')">&#10005;</button>' +
            '</div>';
        if (window.applyLocalTimes) window.applyLocalTimes(slot);   // localize the baseline time (audit P1)
        // PERSISTENT until acknowledged (feedback #5): NO auto-dismiss timer — a
        // notice about un-synced live changes must survive the user being away in the
        // IDE (the old 9s timer hid it before they ever looked, the root of "SM was
        // supposed to pop up but I never saw it"). It clears only when the count
        // returns to 0, on explicit dismiss (×), or on Reset baseline; the poll's
        // visibilitychange catch-up keeps it fresh when the tab regains focus.
        _clearDriftHideTimer();
    }

    window._dismissDrift = function (count) {
        _clearDriftHideTimer();
        _shownCount = 0;
        _dismissedAt = count || 0;
        var slot = document.getElementById("live-drift-slot");
        if (slot) slot.innerHTML = "";
    };

    var _driftPolling = false;
    function poll() {
        // In-flight guard + visibility gating (audit B24): never overlap a slow
        // request, and don't poll while the window is hidden/backgrounded.
        if (_driftPolling || document.hidden) return;
        _driftPolling = true;
        fetch("/state/drift", { cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                _renderBanner(d);
                // Surface the clean auto-pull that just happened on load/select — the
                // pull used to be SILENT, so a user's IDE pulse edit was adopted with
                // no signal and read as "not synced" (feedback #5). One-shot from the server.
                if (d && d.auto_pulled && window.showToast) {
                    var n = d.auto_pulled.count || 0;
                    window.showToast(
                        n > 0 ? ("✓ Live chip updated — " + n + " param" + (n === 1 ? "" : "s")
                                 + " pulled into the working state")
                              : "✓ Live chip state pulled into the working state",
                        "success");
                }
                var count = (d && d.ok && d.tracked) ? (d.count || 0) : 0;
                // Count changed → refresh any embedded panel / open overlay so
                // the State History page + a viewing user see it accumulate.
                if (_lastCount !== null && count !== _lastCount) {
                    (document.body || document).dispatchEvent(new CustomEvent("liveDriftChanged", { bubbles: true }));
                    if (window._driftOverlayOpen) _loadDriftView();
                }
                _lastCount = count;
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
            .then(function (html) { host.innerHTML = html; if (window.htmx) htmx.process(host); })
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
            var slot = document.getElementById("live-drift-slot");
            if (slot) slot.innerHTML = "";
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
    var STATE_PAGES = ["/qubits", "/pairs", "/table", "/bulk", "/wiring",
                       "/instrument-wiring", "/topology", "/workbench",
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
    var ts_a = checked[0].value, ts_b = checked[1].value;
    if (window.htmx) {
        htmx.ajax("GET", "/api/history/compare?ts_a=" + ts_a + "&ts_b=" + ts_b,
                   {target: "#history-detail-area", swap: "innerHTML"});
    }
};

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
        if (sel.length !== 2 || !window.htmx) return;
        var p = htmx.ajax('GET', '/api/history/compare?ts_a=' + encodeURIComponent(sel[0].value)
                  + '&ts_b=' + encodeURIComponent(sel[1].value),
                  { target: '#state-history-detail', swap: 'innerHTML' });
        // Belt-and-suspenders to the delegated afterSwap reveal: the result lands below
        // the timeline (off-screen), so scroll it into view (audit P0-2, the canary).
        if (p && p.then) p.then(function () {
            var d = document.getElementById('state-history-detail');
            if (d && d.innerHTML.trim() !== '') d.scrollIntoView({ behavior: 'smooth', block: 'start' });
        });
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
document.addEventListener("cellsReverted", function(evt) {
    var d = evt.detail || {};
    (d.entries || []).forEach(function(e) {
        _revertCell(e.dot_path, e.old_value_str != null ? e.old_value_str : "");
    });
    // The Live-State-Edit grids render their own cells (not inspector inputs), so
    // _revertCell can't roll them back — tell the grids to re-pull from the (now
    // reverted) working copy so they don't keep showing the undone value. The
    // listeners no-op off their page and skip when a cell is mid-edit/dirty.
    document.dispatchEvent(new CustomEvent("quam:state-changed"));
    if (d.message && window.showToast) window.showToast(d.message, "success");
});

// Tab / click-away COMMITS the inline-edit forms (Pulses detail, Qubit/Pair
// inspector) like Enter. These forms re-render #inspector-pane on commit — same
// as Enter — so tabbing to the next field re-renders the pane; the value is still
// committed (the reported pain: "clicked away, my edit was lost"). focusout does
// NOT fire on Enter (Enter never blurs the input), so there's no double-submit.
// The baseline guard skips unchanged values (and Escape-restores) so a bare
// click-away with no edit is a no-op — the server never no-ops set_value.
document.addEventListener("focusout", function(evt) {
    var input = evt.target;
    if (!input || !input.matches
        || !input.matches('form.inline-edit input[name="value"]')) return;
    var form = input.closest("form");
    if (!form || !form.isConnected || !form.requestSubmit) return;
    var baseline = input.hasAttribute("data-committed")
        ? input.getAttribute("data-committed") : input.defaultValue;
    if (input.value === baseline) return;   // unchanged → don't commit/reswap
    form.requestSubmit();
});

// Global Ctrl/⌘+Z → undo the last in-SM modification (LIFO, server-side, race-safe).
// Scope: works on the pending edits made in Explorer / Live-State-Edit / Pulses
// BEFORE they're saved or applied to live (Save/apply clear the change log — the
// intended undo boundary). We do NOT hijack Ctrl+Z while the user is typing INSIDE
// a field — native text-undo must keep working there; document-level undo only
// kicks in once focus is outside an editable field (e.g. after a Tab/blur commit).
document.addEventListener("keydown", function(evt) {
    if (!((evt.ctrlKey || evt.metaKey) && (evt.key === "z" || evt.key === "Z")
          && !evt.shiftKey && !evt.altKey)) return;
    var a = document.activeElement;
    if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable)) return;
    // Generate-Config wizard mounted → Ctrl+Z is WIZARD-scoped (undoes the last
    // committed wizard field, never a chip edit behind the user's back).
    if (window._wizUndo && window._wizUndo.tryUndo()) { evt.preventDefault(); return; }
    if (!window.htmx || !document.getElementById("pending-tray")) return;
    evt.preventDefault();
    htmx.ajax("POST", "/undo", {source: "#pending-tray", target: "#pending-tray", swap: "outerHTML"});
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

    window.openFolderBrowser = function(targetInputId, kind) {
        _targetInputId = targetInputId;
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
        if (target) {
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
                htmx.trigger(inputEl, "keyup");
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
    // rule the server uses (cli.py `_parse_value` / `_GROUPED_NUMBER`), so a genuine
    // string ("MW,FEM", a pointer, "con/slot/port") is left untouched. Scientific
    // notation (1.2e9) is left verbatim — value identical, notation respected.
    window.NumberInput = (function () {
        var GROUPED = /^[+-]?\d[\d,]*(\.\d+)?$/;   // mirror cli.py:546 _GROUPED_NUMBER (no exponent)
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
    function _buildNode(key, value, path, depth, refValue, hasDiff, valueClick) {
        valueClick = valueClick || "edit";
        var type = _typeOf(value);
        var isContainer = (type === "object" || type === "array");
        var node = document.createElement("div");
        node.className = "tree-node";
        node.setAttribute("data-depth", depth);
        node.setAttribute("data-path", path);
        // Stashed so a whole-container JSON edit can rebuild this node in place
        // (see _makeContainerEditable / _rebuildNode) without re-fetching the tree.
        node._meta = {key: key, path: path, depth: depth, refValue: refValue,
                      hasDiff: hasDiff, valueClick: valueClick};
        node._value = value;   // current value (kept fresh by _rebuildNode) — for key-copy

        if (hasDiff && refValue !== undefined && !_deepEqual(value, refValue)) {
            node.classList.add("tree-diff");
        }

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

            var colon = document.createElement("span");
            colon.className = "tree-colon";
            colon.textContent = ": ";
            row.appendChild(colon);
        }

        if (isContainer) {
            var summary = document.createElement("span");
            summary.className = "tree-summary";
            if (type === "object") {
                var n = Object.keys(value).length;
                summary.textContent = "{" + n + " key" + (n !== 1 ? "s" : "") + "}";
            } else {
                summary.textContent = "[" + value.length + " item" + (value.length !== 1 ? "s" : "") + "]";
            }
            row.appendChild(summary);

            // Edit the WHOLE list/dict as JSON — the only way to enter a list value
            // (the scalar leaf editor can't). Read-only trees (copy / livediff) get
            // no edit affordance. Click is stopped so it never toggles expand.
            if (valueClick === "edit") {
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

            // Lazy children container — populated on first expand
            var children = document.createElement("div");
            children.className = "tree-children";
            children.style.display = "none";

            // Store data for deferred rendering (closure captures value/refValue)
            node._lazyData = { value: value, type: type, path: path, depth: depth, refValue: refValue, hasDiff: hasDiff, valueClick: valueClick };

            node.appendChild(row);
            node.appendChild(children);
        } else {
            var valEl = document.createElement("span");
            var valClass = "tree-val tree-val-" + type;
            if (_isPointer(value)) valClass = "tree-val tree-val-pointer";
            valEl.className = valClass;
            valEl.textContent = _formatValue(value);
            // raw value for the edit input (strings without display-quotes) / copy
            valEl.dataset.editVal = (typeof value === "string") ? value : _formatValue(value);
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
            } else {
                valEl.title = _isPointer(value) ? "Pointer \u2014 click to edit" : "Click to edit";
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
            if (value === null && valueClick === "edit") {
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

            if (hasDiff && refValue !== undefined && !_deepEqual(value, refValue)) {
                // "livediff" is the workbench's before→after mode: value = the SM
                // working copy (before), refValue = Qualibrate's live value (after).
                var liveDiff = (valueClick === "livediff");
                if (liveDiff) {
                    row.classList.add("tree-row-incoming");
                    var arrow = document.createElement("span");
                    arrow.className = "tree-incoming-arrow";
                    arrow.textContent = " → ";
                    row.appendChild(arrow);
                    var inEl = document.createElement("span");
                    inEl.className = "tree-incoming-val tree-val-" + _typeOf(refValue) +
                        (_isPointer(refValue) ? " tree-val-pointer" : "");
                    inEl.textContent = _formatValue(refValue);
                    inEl.title = "Qualibrate's live value";
                    row.appendChild(inEl);
                }
                if (typeof value === "number" && typeof refValue === "number") {
                    // livediff reads "after - before" (Qualibrate's change); the
                    // N-way compare keeps its original "primary - ref" orientation.
                    var delta = liveDiff ? (refValue - value) : (value - refValue);
                    var deltaEl = document.createElement("span");
                    var cls = delta > 0 ? "delta-pos" : (delta < 0 ? "delta-neg" : "delta-zero");
                    deltaEl.className = "tree-delta " + cls;
                    var a2 = Math.abs(delta);
                    var fmt = (a2 >= 1e6 || (a2 > 0 && a2 < 1e-3)) ? delta.toExponential(3) : delta.toFixed(6);
                    deltaEl.textContent = " (" + (delta > 0 ? "+" : "") + fmt + ")";
                    row.appendChild(deltaEl);
                }
                if (liveDiff) {
                    var acc = document.createElement("button");
                    acc.type = "button";
                    acc.className = "tree-accept-btn";
                    acc.textContent = "✓";
                    acc.title = "Accept Qualibrate's value into the working state";
                    (function(p, rv, el, rw) {
                        acc.onclick = function(e) { e.stopPropagation(); _acceptLiveValue(p, rv, el, rw); };
                    })(path, refValue, valEl, row);
                    row.appendChild(acc);
                    var rej = document.createElement("button");
                    rej.type = "button";
                    rej.className = "tree-reject-btn";
                    rej.textContent = "✗";
                    rej.title = "Keep your value (dismiss this incoming change)";
                    (function(rw, p) {
                        rej.onclick = function(e) { e.stopPropagation(); _rejectLiveValue(rw, p); };
                    })(row, path);
                    row.appendChild(rej);
                }
            }

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

        if (d.type === "object") {
            var keys = Object.keys(d.value);
            for (var i = 0; i < keys.length; i++) {
                var childPath = d.path ? d.path + "." + keys[i] : keys[i];
                var childRef = (d.hasDiff && d.refValue && typeof d.refValue === "object" && !Array.isArray(d.refValue))
                    ? d.refValue[keys[i]] : undefined;
                children.appendChild(_buildNode(keys[i], d.value[keys[i]], childPath, d.depth + 1, childRef, d.hasDiff, d.valueClick));
            }
        } else {
            for (var j = 0; j < d.value.length; j++) {
                // Canonical dot-form numeric segment (a.b.3) — matches the server
                // path grammar so element edits POST directly to /field/edit.
                var itemPath = d.path + "." + j;
                var itemRef = (d.hasDiff && Array.isArray(d.refValue)) ? d.refValue[j] : undefined;
                children.appendChild(_buildNode(String(j), d.value[j], itemPath, d.depth + 1, itemRef, d.hasDiff, d.valueClick));
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
            flat.push({ path: path, pathLower: path.toLowerCase(), hayLower: hay });
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
    function _searchTreeData(container, q) {
        // Clear stale search classes on whatever is currently materialised.
        var rendered = container.querySelectorAll(".tree-node");
        for (var i = 0; i < rendered.length; i++) {
            rendered[i].classList.remove("tree-highlight", "tree-search-hidden");
        }
        if (!q) {
            _expandToDepth(container, 1);
            return;
        }

        if (!container._flatIndex) {
            container._flatIndex = _buildFlatIndex(container._treeData);
        }
        var flat = container._flatIndex.flat;

        // O(N) scan over pre-lowercased fields. keepPaths = matches + ancestors.
        var matchPaths = new Set();
        var keepPaths = new Set();
        for (var j = 0; j < flat.length; j++) {
            var e = flat[j];
            if (e.hayLower.indexOf(q) >= 0 || e.pathLower.indexOf(q) >= 0) {
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
            return;
        }

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
            if (hay.indexOf(q) >= 0 || pathAttr.indexOf(q) >= 0) {
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

            fetch("/field/edit", {
                method: "POST",
                headers: {"Content-Type": "application/x-www-form-urlencoded"},
                body: body.toString()
            })
            .then(function(resp) { return resp.json(); })
            .then(function(data) {
                if (!data.ok) {
                    valEl.classList.add("tree-val-error");
                    setTimeout(function() { valEl.classList.remove("tree-val-error"); }, 2000);
                    _showEditError(valEl, data.error);
                    return;
                }
                valEl.textContent = newVal;
                valEl.dataset.editVal = newVal;
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
        // Re-format using tree conventions: try numeric first, then fall back
        var num = Number(oldValueStr);
        if (oldValueStr !== "" && oldValueStr !== "null" && !isNaN(num)) {
            valEl.textContent = _formatValue(num);
            valEl.dataset.editVal = _formatValue(num);
        } else {
            valEl.textContent = oldValueStr === "" ? "null" : _formatValue(oldValueStr);
            valEl.dataset.editVal = oldValueStr;
        }
    };

    /* ── Explorer structural CRUD + type picker ─────────────────────────
       Hover-built row actions (＋ add child key on dicts, ✕ delete, ⚙ type
       picker on leaves), lazily attached via ONE delegated mouseover per
       crud-enabled container — no build cost across 10k idle rows. */

    var _TYPE_CHOICES = ["infer", "int", "number", "str", "bool", "list",
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
        if (inList || identity) return;      // elements/identity: value-edit only

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
            var t = s.expected_type === "number" ? "number" : s.expected_type;
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
            ["int", "number", "str", "bool", "list", "matrix", "dict"].map(function (t) {
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

    window.renderJsonTree = function(containerId, data, options) {
        var container = document.getElementById(containerId);
        if (!container) return;
        // A full (re)render is a new context (chip load / switch) — drop any stale
        // key-copy so its paste buttons don't linger against a different chip.
        _clearTreeCopy();
        container.innerHTML = "";
        container.className = "json-tree";

        options = options || {};
        var refData = options.refData || null;
        var defaultDepth = options.defaultDepth !== undefined ? options.defaultDepth : 1;
        var hasDiff = !!refData;
        // "edit" (default) keeps the existing live-state behavior; "copy" makes
        // scalar values click-to-copy for read-only trees (dataset params/results).
        var valueClick = options.valueClick || "edit";

        if (typeof data === "object" && data !== null && !Array.isArray(data)) {
            var keys = Object.keys(data);
            for (var i = 0; i < keys.length; i++) {
                var refVal = (hasDiff && refData && typeof refData === "object") ? refData[keys[i]] : undefined;
                container.appendChild(_buildNode(keys[i], data[keys[i]], keys[i], 0, refVal, hasDiff, valueClick));
            }
        } else {
            container.appendChild(_buildNode(null, data, "", 0, refData, hasDiff, valueClick));
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

document.addEventListener("htmx:pushedIntoHistory", function() {
    var path = window.location.pathname.replace(/^\//, "").split("/")[0] || "home";
    document.querySelectorAll(".sidebar-nav a").forEach(function(a) {
        var href = a.getAttribute("href").replace(/^\//, "");
        a.classList.toggle("active", href === path);
    });
});

/* ------------------------------------------------------------------ */
/* Instrument Wiring Diagram                                           */
/* ------------------------------------------------------------------ */

var _popupElement = null;
var _popupHideTimer = null;

/**
 * Build an SVG chassis diagram with dual sub-columns per FEM (outputs left, inputs right).
 * Colored port circles encode role; hover shows details, dblclick shows raw wiring JSON.
 */
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

    // Each FEM: output sub-column (left) + input sub-column (right)
    var outSubW = 82, inSubW = 66, femGap = 16;
    var femW = outSubW + inSubW;
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

        var totalFemW = femIds.length * femW + Math.max(0, femIds.length - 1) * femGap;
        var svgW = marginLeft + totalFemW + 20;
        var svgH = marginTop + maxOutPort * rowH + marginBottom;

        var svg = _svgEl('svg');
        svg.setAttribute('width', svgW);
        svg.setAttribute('height', svgH);
        svg.setAttribute('class', 'instrument-svg');
        svg.setAttribute('style', 'max-width:100%;');

        // Controller title
        svg.appendChild(_svgText(svgW / 2, 22, ctrlName + ' \u2014 OPX1000 Wiring', 14, '600', '#333', 'middle'));

        // Background rect for the grid area
        var iw = UI_CONFIG.instrumentWiring;
        var bg = _svgEl('rect');
        _svgAttrs(bg, {
            x: marginLeft, y: marginTop - 16,
            width: totalFemW, height: maxOutPort * rowH + 16,
            fill: iw.gridBg, stroke: iw.gridBorder, rx: 4
        });
        svg.appendChild(bg);

        // Row number labels on the left margin
        for (var rn = 1; rn <= maxOutPort; rn++) {
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

            // Solid separator between FEMs
            if (colIdx > 0) {
                var sep = _svgEl('line');
                _svgAttrs(sep, {
                    x1: femX - femGap / 2, y1: marginTop - 16,
                    x2: femX - femGap / 2, y2: marginTop - 16 + maxOutPort * rowH + 16,
                    stroke: iw.separatorColor, 'stroke-width': 2
                });
                svg.appendChild(sep);
            }

            // Dashed separator between OUT and IN sub-columns within a FEM
            var subSep = _svgEl('line');
            _svgAttrs(subSep, {
                x1: femX + outSubW, y1: marginTop - 16,
                x2: femX + outSubW, y2: marginTop - 16 + maxOutPort * rowH + 16,
                stroke: iw.subSeparatorColor, 'stroke-width': 1, 'stroke-dasharray': '4,4'
            });
            svg.appendChild(subSep);

            // Sub-column header labels
            svg.appendChild(_svgText(outCx, marginTop - 4, 'OUT', 8, '700', iw.subLabelColor, 'middle'));
            svg.appendChild(_svgText(inCx,  marginTop - 4, 'IN',  8, '700', iw.subLabelColor, 'middle'));

            // FEM label at bottom
            svg.appendChild(_svgText(
                femX + femW / 2, svgH - 8,
                'FEM\u00a0' + femId + '  (' + femData.type + ')', 10, '400', iw.femLabelColor, 'middle'
            ));

            // Output port rows
            for (var portNum = 1; portNum <= maxOutPort; portNum++) {
                var py = marginTop + (portNum - 1) * rowH + rowH / 2;
                _renderPortCell(svg, outCx, py, circleR, roleColors, outPorts[String(portNum)] || [], rawWiring,
                                {con: ctrlName, slot: femId, port: portNum, io: 'output'}, editable, onPortHover);
            }

            // Physical input port positions depend on FEM type:
            //   MW-FEM: port 1 at row 1 (top), port 2 at last row (bottom)
            //   LF-FEM: port 1 at second-to-last row, port 2 at last row (both at bottom)
            var inputRowMap = {};  // display_row → input_port_number
            if (femData.type === 'mw-fem') {
                inputRowMap[1] = 1;
                inputRowMap[maxOutPort] = 2;
            } else {
                inputRowMap[maxOutPort - 1] = 1;
                inputRowMap[maxOutPort] = 2;
            }
            var inR = Math.round(circleR * 0.82);
            for (var pn = 1; pn <= maxOutPort; pn++) {
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
    var g = _svgEl('g');
    g.style.cursor = editable ? 'grab' : 'pointer';
    g.setAttribute('class', 'iw-port-circle');
    if (assignment.element != null) g.setAttribute('data-element', assignment.element);
    if (assignment.role != null) g.setAttribute('data-role', assignment.role);

    var circle = _svgEl('circle');
    _svgAttrs(circle, {cx: cx, cy: cy, r: r, fill: color, stroke: 'rgba(0,0,0,0.15)', 'stroke-width': 1.5});
    g.appendChild(circle);

    // Strip .role suffix for display: "qA1.xy" → "qA1" (role is encoded by color)
    var label = assignment.label || '';
    var displayLabel = label.replace(/\.[^.]+$/, '') || label;
    var maxChars = r < 14 ? 4 : (r < 18 ? 6 : 8);
    var display = displayLabel.length > maxChars ? displayLabel.substring(0, maxChars - 1) + '\u2026' : displayLabel;
    var fontSize = r < 14 ? 7 : (r < 18 ? 8 : 10);
    var txt = _svgText(cx, cy + 4, display, fontSize, '700', UI_CONFIG.instrumentWiring.portLabelColor, 'middle');
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
    var px = event.clientX + 14;
    var py = event.clientY - 10;
    if (px + 340 > window.innerWidth) px = event.clientX - 340;
    popup.style.left = px + 'px';
    popup.style.top = py + 'px';
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
    if (r === 'z') return [
        {key: 'flux point',   value: a.flux_point},
        {key: 'joint offset', value: _fmtNum(a.joint_offset, 4)},
        {key: 'indep offset', value: _fmtNum(a.independent_offset, 4)},
        {key: 'output mode',  value: a.output_mode},
        {key: 'upsampling',   value: a.upsampling_mode},
    ];
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
 * Open triggers:
 *   - first focus on #dataset-search per browser session (sessionStorage flag)
 *   - any click on #ds-search-help-toggle (the ? icon)
 * Close trigger:
 *   - click on #ds-search-help-close (the × button)
 *
 * Per user spec: NO auto-dismiss on blur/outside-click. The panel persists
 * through typing, sorting, chip clicks, and HTMX swaps until X is clicked.
 */
(function() {
    var FOCUS_FLAG = 'quam_dataset_search_help_shown';

    function openHelp() {
        var panel = document.getElementById('ds-search-help');
        if (panel) panel.hidden = false;
    }
    function closeHelp() {
        var panel = document.getElementById('ds-search-help');
        if (panel) panel.hidden = true;
    }

    // Attach to document (not document.body) — app.js loads in <head> with no
    // defer, so document.body is null at script-parse time. Both focusin and
    // click bubble up to document, so delegation works identically.
    document.addEventListener('focusin', function(e) {
        var t = e.target;
        if (!t || t.id !== 'dataset-search') return;
        try {
            if (sessionStorage.getItem(FOCUS_FLAG) === '1') return;
            sessionStorage.setItem(FOCUS_FLAG, '1');
        } catch (_err) { /* sessionStorage may be disabled — open anyway */ }
        openHelp();
    });

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
            openHelp();
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
 *   input:   class="search-help-input"   data-search-help="<panel-id>"
 *   ? icon:  class="search-help-toggle"  data-search-help="<panel-id>"
 *   × close: class="search-help-close"   data-search-help="<panel-id>"
 *   example: class="search-help-example" data-search-help-input="<input-id>" data-example="…"
 * Opens on first focus per session (flag keyed by panel id) + on ? click;
 * closes only via ×. Delegated on document (app.js loads in <head>). The
 * Datasets page keeps its own id-based handler above, unchanged. */
(function() {
    function flagKey(panelId) { return 'quam_search_help_shown:' + panelId; }

    document.addEventListener('focusin', function(e) {
        var t = e.target;
        if (!t || !t.classList || !t.classList.contains('search-help-input')) return;
        var panelId = t.getAttribute('data-search-help');
        if (!panelId) return;
        try {
            if (sessionStorage.getItem(flagKey(panelId)) === '1') return;
            sessionStorage.setItem(flagKey(panelId), '1');
        } catch (_e) { /* sessionStorage disabled — open anyway */ }
        var panel = document.getElementById(panelId);
        if (panel) panel.hidden = false;
    });

    document.addEventListener('click', function(e) {
        var t = e.target;
        if (!t || !t.classList) return;
        if (t.classList.contains('search-help-toggle')) {
            e.preventDefault();
            var p = document.getElementById(t.getAttribute('data-search-help'));
            if (p) p.hidden = false;
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
    var container = btn.closest('#ds-prevdiff-container, #ds-prevdiff-fv-body');
    if (container) loadPrevDiffInto(container.id, runId, compact, vs);
};

/**
 * Toggle figure zoom on click. When zoomed the <img> becomes a near-full-viewport
 * position:fixed; z-index:9999 layer over #table-pane, so it MUST be dismissable by
 * more than re-clicking the image — otherwise the run table is left under an
 * undismissable layer (clicks hit the image, not the rows). Add Escape + outside-click
 * dismissal, cleaned up when un-zoomed.
 */
window.toggleFigureZoom = function(imgEl) {
    var zoomed = imgEl.classList.toggle('figure-zoomed');
    if (!zoomed) {
        if (imgEl._zoomCleanup) imgEl._zoomCleanup();
        return;
    }
    var off = function() {
        imgEl.classList.remove('figure-zoomed');
        if (imgEl._zoomCleanup) imgEl._zoomCleanup();
    };
    var onKey = function(e) { if (e.key === 'Escape') off(); };
    // A pointerdown anywhere but the image dismisses (the triggering click already
    // happened, so this only fires on the NEXT interaction). Capture phase so it wins
    // before the underlying row handler — the user then clicks again to act on the row.
    var onDown = function(e) { if (e.target !== imgEl) off(); };
    imgEl._zoomCleanup = function() {
        document.removeEventListener('keydown', onKey, true);
        document.removeEventListener('pointerdown', onDown, true);
        imgEl._zoomCleanup = null;
    };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onDown, true);
};

/**
 * Return the enclosing dataset column (pinned or current) for an element,
 * falling back to #inspector-pane when not in split-view.
 */
function _h5Panel(el) {
    return (el && el.closest('.inspector-pinned-col, .inspector-current-col'))
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
    if (typeof Plotly !== 'undefined') {
        scope.querySelectorAll('.ds-interactive-list .js-plotly-plot').forEach(function(el) {
            try { Plotly.Plots.resize(el); } catch (e) {}
        });
    }
};

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
            var layout = d.layout || {};
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
            var layout = data.layout || {};
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
          + '<input type="text" class="plot-apply-new-input" value="' + _ppEscape(String(u.value)) + '"></div>'
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
        input.addEventListener('input', function() { _updatePlotRowDomainWarning(row); });
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
    } else {
        slot.textContent = String(v);
        slot.classList.remove('muted');
    }
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
    var _chipBody = (_pp && _pp.dataset.expectChip)
        ? '&expect_chip=' + encodeURIComponent(_pp.dataset.expectChip)
          + (_pp.dataset.forceChip ? '&force_chip=1' : '')
        : '';
    fetch('/field/edit', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded', 'HX-Request': 'true'},
        body: 'dot_path=' + encodeURIComponent(dotPath) + '&value=' + encodeURIComponent(input.value) + _chipBody
    }).then(function(resp) {
        return resp.json().then(function(j) { return {status: resp.status, body: j}; });
    }).then(function(r) {
        if (r.body && r.body.ok) {
            _markPlotRowApplied(row);
            if (r.body.tray_html) _swapPendingTray(r.body.tray_html);
        } else if (r.status === 409 && r.body && r.body.chip_mismatch) {
            btn.disabled = false; btn.textContent = prevLabel;
            if (window.confirm((r.body.error || 'Different chip.') + '\n\nApply anyway?')
                && _pp) { _pp.dataset.forceChip = '1'; applyPlotRow(row); }
        } else {
            btn.disabled = false;
            btn.textContent = prevLabel;
            if (errEl) {
                errEl.hidden = false;
                errEl.textContent = (r.body && r.body.error) || 'Apply failed';
            }
        }
    }).catch(function(e) {
        btn.disabled = false;
        btn.textContent = prevLabel;
        if (errEl) { errEl.hidden = false; errEl.textContent = String(e); }
    });
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
    var _body = {updates: updates};
    if (_pp && _pp.dataset.expectChip) {
        _body.expect_chip = _pp.dataset.expectChip;
        if (_pp.dataset.forceChip) _body.force_chip = true;
    }
    fetch('/field/edit-batch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(_body)
    }).then(function(resp) {
        return resp.json().then(function(j) { return {status: resp.status, body: j}; });
    }).then(function(r) {
        if (applyAllBtn) { applyAllBtn.disabled = false; applyAllBtn.textContent = prevLabel; }
        if (r.body && r.body.ok) {
            pending.forEach(function(row) { _markPlotRowApplied(row); });
            if (r.body.tray_html) _swapPendingTray(r.body.tray_html);
        } else if (r.status === 409 && r.body && r.body.chip_mismatch) {
            if (window.confirm((r.body.error || 'Different chip.') + '\n\nApply anyway?')
                && _pp) { _pp.dataset.forceChip = '1'; applyAllPlotRows(); }
        } else {
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
    }).catch(function(e) {
        if (applyAllBtn) { applyAllBtn.disabled = false; applyAllBtn.textContent = prevLabel; }
        var first = pending[0];
        if (first) {
            var er = first.querySelector('.plot-apply-row-error');
            if (er) { er.hidden = false; er.textContent = String(e); }
        }
    });
}

function _markPlotRowApplied(row) {
    if (!row) return;
    row.classList.add('plot-apply-applied');
    var btnSlot = row.querySelector('.plot-apply-row-action');
    if (btnSlot) btnSlot.innerHTML = '<span class="plot-apply-row-check">✓ applied</span>';
    var input = row.querySelector('.plot-apply-new-input');
    if (input) input.readOnly = true;
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
    if (ids.length < 2 || ids.length > 8) {
        alert('Select 2-8 runs to compare.');
        return;
    }
    htmx.ajax('GET', '/datasets/compare?ids=' + ids.join(','),
              {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'});
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
        if (window.Plotly) pane.querySelectorAll('.js-plotly-plot')
            .forEach(function (p) { try { Plotly.purge(p); } catch (e) {} });
        pane.innerHTML = currentCol.innerHTML;
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
    if (window.Plotly) pane.querySelectorAll('.js-plotly-plot')
        .forEach(function (p) { try { Plotly.purge(p); } catch (e) {} });
    pane.innerHTML = tmp.innerHTML;
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
document.addEventListener('htmx:beforeSwap', function(evt) {
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
        pane.innerHTML = _wrapPinnedLayout(window._pinnedHtml, evt.detail.serverResponse);
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
});

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
    var root = pane.querySelector('#ds-detail-root');
    if (!root) return;

    var newRunId = root.dataset.uid;   // folder-aware uid (matches tree entries' data-uid)

    // Mirror the opened run into the left sidebar tree (highlight + reveal).
    // Highlight only — never the /workspace/select round-trip (a different
    // action that would reload the chip).
    syncSidebarTreeHighlight(newRunId, root.dataset.date);

    if (newRunId === _dsSticky.currentRunId) return; // Same run — skip

    var hadPrevious = !!_dsSticky.currentRunId;
    _dsSticky.currentRunId = newRunId;
    window._dsLastPlot = null;

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
            htmx.ajax('GET', '/dataset/' + runId, {source: '#inspector-pane', target: '#inspector-pane', swap: 'innerHTML'}).then(function() {
                setTimeout(function() {
                    var dataLink = document.querySelector('.dataset-tabs a[onclick*="\'data\'"]');
                    if (dataLink) window.switchDatasetTab('data', dataLink);

                    // ndview auto-opens the first variable on mount — nothing
                    // more to do here (the legacy h5-vars-table observer is gone;
                    // it would watch forever against the new DOM and leak).
                }, 150);
            });
        } else {
            // Fallback: no inspector pane → navigate to Datasets
            htmx.ajax('GET', '/datasets', {target: '#table-pane', swap: 'innerHTML'}).then(function() {
                document.querySelectorAll('.sidebar-nav a').forEach(function(a) {
                    a.classList.toggle('active', a.getAttribute('href') === '/datasets');
                });
                history.pushState({}, '', '/datasets');
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
    drawer.style.display = 'block';
    drawer.innerHTML = '<p class="muted" style="padding:1rem">Loading…</p>';
    var url = '/param-history/expand?qubit=' + encodeURIComponent(qubit)
            + '&prop=' + encodeURIComponent(prop);
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
    drawer.style.display = 'none';
    drawer.innerHTML = '';
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
                setTimeout(_paramHistoryPollBackfill, 800);
            } else if (s.status === 'done') {
                if (status) status.textContent = 'Imported ' + (s.ingested || 0) + ' snapshots. Reloading…';
                if (progressLine) progressLine.textContent = '';
                if (loader) loader.classList.remove('visible');
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
                // Errors also count as an attempt — don't keep auto-firing.
                _paramHistoryMarkSessionAttempt();
            } else {
                // Unknown/unexpected status — treat as terminal so the poll chain
                // doesn't die silently and let htmx:afterSwap re-fire forever.
                if (loader) loader.classList.remove('visible');
                _paramHistoryMarkSessionAttempt();
            }
        })
        .catch(function(err) {
            // Status fetch failed — stop the chain; the attempt is already marked.
            if (loader) loader.classList.remove('visible');
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
function _paramHistoryHasImportedBefore(chipKey) {
    try { return localStorage.getItem(_paramHistoryImportedKey(chipKey)) === '1'; }
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
    try { return !!sessionStorage.getItem(_paramHistorySessionAttemptKey(chipKey)); }
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
    var SLOW_PREFIXES = ['/param-history', '/datasets'];
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
        // Open palette on Ctrl+K / Cmd+K from anywhere
        if ((evt.ctrlKey || evt.metaKey) && (evt.key === 'k' || evt.key === 'K')) {
            evt.preventDefault();
            window.openCmdPalette();
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
            var oldv = btn.getAttribute("data-old") || "the current literal";
            if (!window.confirm("Relink downconverter_frequency to its paired upconverter?\n\n" +
                    "Its value (" + oldv + ") will change to track the shared LO. The change is added " +
                    "to your pending edits — review it in the tray before applying to the live chip.")) {
                return;
            }
        }
        var body = new URLSearchParams();
        body.append("action", btn.getAttribute("data-action") || "");
        body.append("dot_path", btn.getAttribute("data-dot-path") || "");
        body.append("pointer", btn.getAttribute("data-pointer") || "");
        var orig = btn.textContent;
        btn.disabled = true; btn.textContent = "Converting…";
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
    var _explorerLiveDiffOn = false;
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
            valEl.textContent = _formatValue(liveValue);
            valEl.dataset.editVal = (typeof liveValue === "string") ? liveValue : _formatValue(liveValue);
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

    // The tree's own inline value-editor (_makeValueEditable, a different scope)
    // calls this after the user types a new value into a field that is part of
    // the incoming live diff. Treat it like a per-row accept of the user's value:
    // drop the incoming markers and remove the path from the Accept-All set, so a
    // later "Accept all" can't replay the stale LIVE value over the value the user
    // just typed (field/edit-batch is last-write-wins per path). Idempotent.
    window._explorerNoteInlineEdit = function (dotPath, row) {
        if (!_explorerLiveDiffOn || !dotPath || _liveDiffDone[dotPath]) return;
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

    window.explorerLiveDiff = function(on) {
        var stateEl = document.getElementById("explorer-tree-state");
        var wiringEl = document.getElementById("explorer-tree-wiring");
        if (!stateEl || !wiringEl) return;
        if (on === undefined) on = !_explorerLiveDiffOn;

        if (!on) {
            _explorerLiveDiffOn = false;
            _liveDiffState = []; _liveDiffWiring = []; _liveDiffDone = {}; _liveDiffRemaining = 0;
            var t0 = document.getElementById("explorer-livediff-toggle");
            if (t0) t0.classList.remove("active");
            var b0 = document.getElementById("explorer-livediff-bar");
            if (b0) b0.hidden = true;
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
                    _explorerLiveDiffOn = false;
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

                // Commit the ON state ATOMICALLY — only after the render fully
                // succeeded, so a render error never leaves a half-applied overlay
                // with a stuck-on toggle (the old code set on=true BEFORE rendering).
                _explorerLiveDiffOn = true;
                var t = document.getElementById("explorer-livediff-toggle");
                if (t) t.classList.add("active");
                var cnt = document.getElementById("livediff-bar-count");
                if (cnt) cnt.textContent = _liveDiffRemaining;
                var bar = document.getElementById("explorer-livediff-bar");
                if (bar) bar.hidden = false;
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

