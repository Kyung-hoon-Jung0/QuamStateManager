/* NodeSim: the "Simulate a node" page (page bundle 'nodesim').
 *
 * Mounts on every [data-nodesim] element (optional attributes: data-node = the
 * node file to preselect, else the ?node= query; data-qubit = the qubit
 * selected in the sidebar). It builds the pickers from GET /api/nodesim/nodes,
 * starts a run with POST /api/nodesim/start, polls GET /api/nodesim/status
 * while it runs (the server stops a run nobody asks about), and draws the
 * cloud simulator's waveform report with GateTimeline.
 *
 * Honesty rules:
 * - every server answer is {state, line, ...}; `line` is shown verbatim (a
 *   newline splits it into a conclusion and its reason), never re-worded, and
 *   an unknown state still shows its line; the only client-worded lines are
 *   NO_ANSWER (SM itself did not answer) and NO_READOUT;
 * - the report has no samples (report only): a segment carries its time span
 *   and operation name, never an invented level;
 * - lanes are one per (element, output port); two lanes that end up with the
 *   same key, or a reserved key, are refused by GateTimeline (cannot draw),
 *   never merged.
 *
 * Exposed for the selfcheck: window.NodeSim = {mount, reportToTimeline,
 * renderAnswer, STATES_SHOWN}.
 */
(function () {
    "use strict";

    var POLL_MS = 2000;            // while a run is live (the server heartbeat is 12 s)
    var VISIBLE_POLL_MS = 2000;    // after a result, page on screen: another window's job shows soon (P3-f)
    var IDLE_POLL_MS = 10000;      // after a result, page hidden: only to see the chip move on
    var MISS_LIMIT = 3;            // failed status fetches in a row before NO_ANSWER shows
    var WINDOWS_US = [10, 20, 50, 100];
    var DEFAULT_US = 20;
    var PHASES = ["copy", "build", "login", "spawn", "connect", "submit", "wait", "report", "close"];
    var WARM_PHASES = ["copy", "lock", "build", "lease", "connect", "submit", "wait", "report"];
    var FAILED_LINE = { failed: 1, node_error: 1, serializer: 1, spawn_failed: 1, broker_failed: 1,
        lease_failed: 1, timeout: 1, worker_crashed: 1, build_watchdog: 1 };
    var LIVE = { busy: 1, busy_chip: 1, starting: 1, running: 1, warming: 1, renewing: 1, expired_retry: 1 };
    var NOT_IN_LIBRARY = "The node asked for is not in the calibration library of this chip.";
    var STALE = "chip changed since - re-simulate";
    var NO_ANSWER = "No answer from SM; the run stops if this page stops asking.";
    var NO_READOUT = "No readout inside this window; try a longer window.";
    var NO_ANALOG = "cannot draw: the report has no analog waveform inside this window";
    var BAD_REPORT = "cannot draw: the report has a form SM does not read";

    /* How each state of the answer is shown: [part, tone, key word]. A state not
       listed here is shown as a neutral verdict holding its line. */
    var SHOW = {
        blocked: ["callout", "danger", "Simulator library"],
        no_env: ["notice", "warn", "Env"],
        no_library: ["notice", "warn", "Library"],
        not_configured: ["notice", "warn", "Login"],
        needs_targets: ["notice", "note", "Targets"],
        refused: ["notice", "warn", "Not simulated"],
        busy: ["notice", "note", "Busy"],
        starting: ["phases"],
        running: ["phases"],
        done: ["verdict", "ok"],
        node_error: ["verdict", "danger"],
        serializer: ["verdict", "danger"],
        guard: ["verdict", "danger"],
        chip_changed: ["verdict", "danger"],
        too_long: ["verdict", "danger"],
        timeout: ["verdict", "danger"],
        stopped: ["verdict", "neutral"],
        failed: ["verdict", "danger"]
    };
    var BANNER_TONE = { "script incomplete": "danger", "REDUCED SIMULATION": "neutral" };

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = String(text);
        return n;
    }
    function clear(n) { while (n && n.firstChild) n.removeChild(n.firstChild); return n; }
    function num(v) { return typeof v === "number" && isFinite(v); }
    function str(v) { return typeof v === "string" ? v : ""; }
    function secs(v) { return num(v) ? (v < 10 ? v.toFixed(2) : v.toFixed(1)) + " s" : ""; }
    function windowId() { return window.__smWindowId || ""; }

    function call(url, payload) {
        var init = payload === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" },
                                                  body: JSON.stringify(payload) };
        return fetch(url, init).then(function (r) {
            return r.json().then(function (j) { return (j && typeof j === "object") ? j : null; },
                                 function () { return null; });
        }, function () { return null; });
    }

    /* ---- report -> timeline data (gate-timeline.js data shape) ---------- */
    function portText(w, p) {
        return [w.controller, w.fem, p].filter(function (x) { return x !== undefined && x !== null && x !== ""; })
            .map(String).join(" / ");
    }
    function iqText(info) {
        if (!info || !info.isPartOfIq) return "";
        return info.isI && info.isQ ? "I+Q" : info.isI ? "I" : info.isQ ? "Q" : "";
    }
    function reportToTimeline(report) {
        var out = { lanes: [], marks: [], notes: [], cannot_draw: null };
        if (!report || typeof report !== "object" || !Array.isArray(report.analog_waveforms)) {
            out.cannot_draw = BAD_REPORT;
            return out;
        }
        var groups = [], byId = {}, perElement = {};
        var bad = false;
        function group(id, element, key, port, first) {
            if (!byId[id]) {
                byId[id] = { id: id, element: element, key: key, port: port, first: first, segs: {}, order: [] };
                groups.push(byId[id]);
                if (key === null) perElement[element] = (perElement[element] || 0) + 1;
            }
            var g = byId[id];
            if (first < g.first) g.first = first;
            return g;
        }
        function addSeg(g, start, end, op, iq) {
            var sk = start + "|" + end + "|" + op;
            var s = g.segs[sk];
            if (!s) {
                s = g.segs[sk] = { operation: op, start_ns: start, end_ns: end, t_ns: [], v: [],
                                   peak_v: null, min_v: null, length_ns: end - start, truncated: false,
                                   flags: [], iq: [] };
                g.order.push(sk);
            }
            // an I and a Q entry of one pulse are one segment (de-duplicated IQ)
            if (iq && s.iq.indexOf(iq) < 0) s.iq.push(iq);
        }
        report.analog_waveforms.forEach(function (w) {
            if (!w || typeof w !== "object") { bad = true; return; }
            var start = w.timestamp, len = w.length, element = str(w.element);
            if (!num(start) || !num(len) || !element) { bad = true; return; }
            var ports = Array.isArray(w.output_ports) && w.output_ports.length ? w.output_ports : [null];
            ports.forEach(function (p) {
                var pt = portText(w, p);
                var g = group("a\u0000" + element + "\u0000" + pt, element, null, pt || null, start);
                addSeg(g, start, start + len, str(w.pulse_name) || str(w.waveform_name) || "pulse", iqText(w.iq_info));
            });
        });
        (Array.isArray(report.adc_acquisitions) ? report.adc_acquisitions : []).forEach(function (a) {
            if (!a || typeof a !== "object") { bad = true; return; }
            var element = str(a.element) || str(a.quantum_element);
            if (!num(a.start_time) || !num(a.end_time) || !element) { bad = true; return; }
            var ins = Array.isArray(a.adc_ports) && a.adc_ports.length ? "in " + a.adc_ports.join(", ") : "";
            var where = [portText(a, null), ins].filter(Boolean).join(" / ");
            var g = group("r\u0000" + element, element, element + " (readout)", where || null, a.start_time);
            addSeg(g, a.start_time, a.end_time, "readout", "");
        });
        if (bad) { out.cannot_draw = BAD_REPORT; return out; }
        // the key: the element when it has one lane, else element + port; a key two
        // lanes share is NOT merged here: GateTimeline refuses to draw it
        groups.forEach(function (g) {
            if (g.key === null) g.key = perElement[g.element] > 1 && g.port ? g.element + " " + g.port : g.element;
        });
        groups.sort(function (a, b) { return a.first - b.first; });
        out.lanes = groups.map(function (g, i) {
            var segs = g.order.map(function (k) {
                var s = g.segs[k];
                var iq = s.iq.join("+");
                var hasI = iq.indexOf("I") >= 0, hasQ = iq.indexOf("Q") >= 0;
                if (hasI || hasQ) s.operation += " (" + (hasI && hasQ ? "I+Q" : hasI ? "I" : "Q") + ")";
                delete s.iq;
                return s;
            }).sort(function (a, b) { return a.start_ns - b.start_ns; });
            return { key: g.key, group: "gate", label: g.key, port: g.port, step: false, limit_v: null,
                     offset_v: 0, segments: segs, sim: null, color: i, color_index: i };
        });
        (Array.isArray(report.digital_waveforms) ? report.digital_waveforms : []).forEach(function (d) {
            if (!d || !num(d.timestamp) || !num(d.length)) return;
            out.marks.push({ kind: "wait", label: "digital " + (str(d.waveform_name) || "marker"),
                             statement: str(d.pulse_name), element: str(d.element) || undefined,
                             start_ns: d.timestamp, end_ns: d.timestamp + d.length });
        });
        if (!report.analog_waveforms.length) out.cannot_draw = NO_ANALOG;
        if (!(report.adc_acquisitions || []).length) out.notes.push(NO_READOUT);
        return out;
    }

    /* The chart data of a done answer: a report is turned into lanes here; a
       result that already carries lanes is drawn as given. */
    function chartData(result) {
        result = result || {};
        var src = result.report || (result.lanes && !Array.isArray(result.lanes) ? result.lanes : null);
        var data = src ? reportToTimeline(src)
                       : { lanes: Array.isArray(result.lanes) ? result.lanes : [], marks: result.marks || [],
                           notes: [], cannot_draw: null };
        if (result.cannot_draw) data.cannot_draw = String(result.cannot_draw);
        (Array.isArray(result.notes) ? result.notes : []).forEach(function (n) {
            if (typeof n === "string" && n && data.notes.indexOf(n) < 0) data.notes.push(n);
        });
        return data;
    }

    /* ---- one answer -> its DOM ------------------------------------------ */
    /* D-19, P2 spec 1: keep cold and warm phase orders distinct. */
    function phaseList(j) {
        var ol = el("ol", "ui-steps");
        ol.setAttribute("data-nodesim-phases", "");
        var done = {};
        (Array.isArray(j.phases) ? j.phases : []).forEach(function (p) { if (p && p.name) done[p.name] = p.s; });
        // a live run lists every phase still to come; a finished one only the phases that ran (a cache hit
        // ran copy and cache, nothing else)
        var names = LIVE[str(j.state)] ? (j.warm ? WARM_PHASES : PHASES).slice() : [];
        Object.keys(done).forEach(function (n) { if (names.indexOf(n) < 0) names.push(n); });
        names.forEach(function (n) {
            var cur = j.phase === n;
            var li = el("li", cur ? "is-current" : done.hasOwnProperty(n) ? "is-done" : null,
                        n + (done.hasOwnProperty(n) && num(done[n]) ? " " + secs(done[n]) : ""));
            li.setAttribute("data-phase", n);
            if (cur) li.setAttribute("aria-current", "step");
            ol.appendChild(li);
        });
        return ol;
    }
    function renderAnswer(j) {
        var box = el("div");
        box.setAttribute("data-nodesim-answer", "");
        var line = str(j && j.line);
        var state = str(j && j.state);
        box.setAttribute("data-state", state);
        var parts = line.split("\n");
        var head = parts[0];
        var how = SHOW[state] || ["verdict", "neutral"];
        var node;
        if (how[0] === "callout") node = window.UI.callout(how[1], how[2], line);
        else if (how[0] === "notice") node = window.UI.notice(how[1], how[2], line);
        else if (how[0] === "phases") {
            node = el("div");
            var t = el("p", "ui-note", line);
            t.setAttribute("data-nodesim-line", "");
            node.appendChild(t);
            if (num(j.elapsed_s)) {
                var e = el("p", "ui-text-quiet", secs(j.elapsed_s));
                e.setAttribute("data-nodesim-elapsed", "");
                node.appendChild(e);
            }
            node.appendChild(phaseList(j));
        } else {
            // P3-b: every line of a failure is its own text element, never one joined run.
            node = window.UI.verdict(how[1], head, parts[1] || "", true);
            parts.slice(2).forEach(function (part) { node.appendChild(el("span", "ui-verdict-why", part)); });
        }
        box.appendChild(node);
        if (!LIVE[state] && Array.isArray(j.phases) && j.phases.length) {
            box.appendChild(window.UI.disclosure("Phases", j.phases.length, [], false));
            box.lastChild.querySelector(".ui-disclosure-body").appendChild(phaseList(j));
        }
        return box;
    }

    /* ---- the page ------------------------------------------------------- */
    function nodeItems(n) {   // nodes only (a graph is not simulated here)
        return ((n && n.items) || []).filter(function (x) { return x && x.file && (!x.kind || x.kind === "node"); });
    }
    /* P2 spec 5: page controls use the UI kit and server verdict lines. */
    function mount(root) {
        if (!root || root.__nodesim) return root && root.__nodesim;
        var UI = window.UI;
        var S = { root: root, nodes: null, node: null, targets: [], windowUs: DEFAULT_US, run: null,
                  answer: null, misses: 0, timer: null, gen: 0, chart: null, shownResult: null, scriptRun: null };
        root.__nodesim = S;

        var ctxRow = el("div", "ui-actions ui-actions--start");
        ctxRow.setAttribute("data-nodesim-context", "");
        var pickNote = el("p", "ui-note");
        pickNote.setAttribute("data-nodesim-pick-note", "");
        pickNote.hidden = true;
        var nodeHost = el("div");
        var targetHost = el("div", "ui-actions ui-actions--start");
        targetHost.setAttribute("data-nodesim-targets", "");
        var winHost = el("div");
        var estimate = el("p", "ui-note");
        estimate.setAttribute("data-nodesim-estimate", "");
        var simBtn = UI.button({ label: "Simulate", cls: "btn-sync", disabled: true });
        simBtn.setAttribute("data-nodesim-start", "");
        var stopBtn = UI.button({ label: "Stop", cls: "btn-sm" });
        stopBtn.setAttribute("data-nodesim-stop", "");
        stopBtn.hidden = true;
        var acts = UI.actions([], "Simulation");
        acts.appendChild(stopBtn);
        acts.appendChild(simBtn);
        var instanceHost = el("div");
        instanceHost.setAttribute("data-nodesim-instances", "");
        var renewBtn = UI.button({ label: "Renew simulator (about 62 s)", cls: "btn-sm secondary" });
        renewBtn.setAttribute("data-nodesim-renew", "");
        var newBtn = UI.button({ label: "Start new instance", cls: "btn-sm secondary" });
        newBtn.setAttribute("data-nodesim-new-instance", "");
        renewBtn.hidden = true;
        newBtn.hidden = true;
        acts.appendChild(renewBtn);
        acts.appendChild(newBtn);
        var settingHost = el("div");
        settingHost.setAttribute("data-nodesim-setting", "");
        var lost = el("div");
        lost.setAttribute("data-nodesim-lost", "");
        var status = el("div");
        status.setAttribute("data-nodesim-status", "");
        status.setAttribute("aria-live", "polite");
        var strip = el("div", "ui-actions ui-actions--start");
        strip.setAttribute("data-nodesim-strip", "");
        var chartHost = el("div");
        chartHost.setAttribute("data-nodesim-chart", "");
        var scriptHost = el("div");
        scriptHost.setAttribute("data-nodesim-script", "");
        [ctxRow, nodeHost, pickNote, targetHost, winHost, estimate, settingHost, acts, instanceHost,
         lost, status, strip, chartHost, scriptHost]
            .forEach(function (n) { root.appendChild(n); });

        function item() {
            var items = (S.nodes && S.nodes.items) || [];
            return items.filter(function (x) { return x.file === S.node; })[0] || null;
        }
        function canStart() {
            var it = item();
            return !!(it && !it.refused && S.targets.length && !(S.answer && LIVE[S.answer.state]));
        }
        /* P2 spec 1: replacement actions appear only with their offer. */
        function buttons() {
            var live = !!(S.answer && LIVE[S.answer.state]);
            stopBtn.hidden = !(live && S.answer.id && S.answer.id === S.run);
            simBtn.disabled = !canStart();
            renewBtn.hidden = !(S.answer && S.answer.state === "renew_offer");
            newBtn.hidden = !(S.answer && S.answer.state === "needs_instance");
        }
        /* D-31, P2 spec 1: the server labels the warm estimate. */
        function showEstimate() {
            var it = item();
            var lines = (it && it.estimate_lines) || {};
            if (root.getAttribute("data-warm-pool") === "true" && it && it.warm_estimate_lines) {
                lines = it.warm_estimate_lines;
            }
            var t = lines[String(S.windowUs)];
            estimate.textContent = typeof t === "string" ? t : "";
            estimate.hidden = !estimate.textContent;
        }
        function drawContext(ctx) {
            clear(ctxRow);
            [["Chip", ctx.chip], ["Env", ctx.env], ["Library", ctx.library], ["QOP", ctx.qop]].forEach(function (kv) {
                if (typeof kv[1] === "string" && kv[1]) ctxRow.appendChild(UI.badge(kv[0] + " " + kv[1], "neutral"));
            });
            ctxRow.hidden = !ctxRow.firstChild;
        }
        function drawTargets() {
            clear(targetHost);
            var it = item();
            var pairs = it && it.targets_name === "qubit_pairs";
            var all = ((S.nodes && (pairs ? S.nodes.pairs : S.nodes.qubits)) || [])
                .filter(function (x) { return typeof x === "string"; });
            all.forEach(function (name) {
                var c = UI.check(name, S.targets.indexOf(name) >= 0, !it || !!it.refused);
                var box = c.querySelector("input");
                box.setAttribute("data-target", name);
                box.addEventListener("change", function () {
                    var i = S.targets.indexOf(name);
                    if (box.checked && i < 0) S.targets.push(name);
                    if (!box.checked && i >= 0) S.targets.splice(i, 1);
                    buttons();
                });
                targetHost.appendChild(c);
            });
        }
        function defaultTargets() {
            var it = item(), n = S.nodes || {};
            if (!it) return [];
            if (it.targets_name === "qubit_pairs") {
                var ap = Array.isArray(n.active_pairs) ? n.active_pairs : (n.pairs || []);
                return ap.length ? [ap[0]] : [];
            }
            var sel = root.getAttribute("data-qubit") || n.selected_qubit;
            if (sel && (n.qubits || []).indexOf(sel) >= 0) return [sel];
            var aq = Array.isArray(n.active_qubits) ? n.active_qubits : (n.qubits || []);
            return aq.length ? [aq[0]] : [];
        }
        function pickNode(file) {
            // P3-c: a failure line belongs to the node it ran; a newly picked node starts without it.
            if (S.answer && FAILED_LINE[S.answer.state]) {
                clear(status);
                S.answer = null;
            }
            S.node = file;
            var it = item();
            pickNote.textContent = it && it.refused ? (it.refused_line || "") : "";
            pickNote.hidden = !pickNote.textContent;
            S.targets = defaultTargets();
            drawTargets();
            showEstimate();
            buttons();
        }
        /* D-12, P2 spec 2.6: only a deliberate picker change expresses intent. */
        function drawPickers() {
            clear(nodeHost);
            var items = nodeItems(S.nodes);
            var sel = UI.select({ label: "Node", value: S.node,
                                  options: [{ value: "", label: "Pick a node" }].concat(items.map(function (x) {
                                      return { value: x.file, label: (x.name || x.file)
                                          + (x.refused_line ? " - " + x.refused_line : ""), disabled: !!x.refused };
                                  })) });
            sel.setAttribute("data-nodesim-node", "");
            sel.addEventListener("change", function () { pickNode(sel.value); intent(true); });
            nodeHost.appendChild(sel);
            clear(winHost);
            var seg = UI.segmented(WINDOWS_US.map(function (w) {
                return { label: w + " us", selected: w === S.windowUs, attrs: { "data-window-us": String(w) } };
            }), "Simulated window");
            seg.addEventListener("click", function (e) {
                var b = e.target && e.target.closest ? e.target.closest("[data-window-us]") : null;
                if (!b) return;
                S.windowUs = +b.getAttribute("data-window-us");
                Array.prototype.forEach.call(seg.querySelectorAll("[data-window-us]"), function (x) {
                    var on = x === b;
                    x.classList.toggle("ui-segmented-on", on);
                    x.setAttribute("aria-pressed", on ? "true" : "false");
                });
                showEstimate();
            });
            winHost.appendChild(seg);
        }

        function showLost(on) {
            clear(lost);
            if (on) lost.appendChild(UI.notice("warn", "No answer", NO_ANSWER));
        }
        function drawStrip(j) {
            clear(strip);
            var res = (j && j.result) || {};
            (Array.isArray(j.banners) ? j.banners : []).forEach(function (b) {
                if (typeof b !== "string" || !b || b === STALE) return;
                strip.appendChild(UI.badge(b, BANNER_TONE[b] || "warn"));
            });
            var prov = res.provenance;
            var provLine = typeof prov === "string" ? prov : (prov && typeof prov.line === "string" ? prov.line : "");
            var copySeq = prov && typeof prov === "object" && num(prov.seq) ? prov.seq : null;
            var stale = (Array.isArray(j.banners) && j.banners.indexOf(STALE) >= 0)
                || (num(j.chip_seq) && copySeq !== null && j.chip_seq > copySeq);
            if (provLine) {
                var p = el("p", "ui-note", provLine);
                p.setAttribute("data-nodesim-provenance", "");
                strip.appendChild(p);
            }
            if (stale) {
                var w = UI.badge(STALE, "warn");
                w.setAttribute("data-nodesim-stale", "");
                strip.appendChild(w);
            }
            strip.hidden = !strip.firstChild;
        }
        function drawChart(j) {
            var res = j.result || {};
            var sig = (j.id || "") + "|" + (j.cached ? 1 : 0);
            if (S.shownResult === sig && S.chart) return;
            S.shownResult = sig;
            if (S.chart) { try { S.chart.destroy(); } catch (e) { /* gone */ } S.chart = null; }
            clear(chartHost);
            S.chart = window.GateTimeline.render(chartHost, chartData(res), {});
            drawScript(j);
        }
        function clearResult() {
            if (S.chart) { try { S.chart.destroy(); } catch (e) { /* gone */ } S.chart = null; }
            S.shownResult = null;
            clear(chartHost);
            clear(strip);
            clear(scriptHost);
            S.scriptRun = null;
        }

        function drawScript(j) {
            clear(scriptHost);
            var run = j.id;
            S.scriptRun = run || null;
            if (!run) return;
            var d = UI.disclosure("Show script", null, [], false);
            d.setAttribute("data-nodesim-script-fold", "");
            var body = d.querySelector(".ui-disclosure-body");
            var pre = el("pre", "ui-wrap-value");
            pre.setAttribute("data-nodesim-script-text", "");
            var said = el("p", "ui-note");
            said.setAttribute("data-nodesim-save-line", "");
            var folder = UI.input({ label: "Folder to save the script in", placeholder: "Folder", block: true });
            folder.id = "nodesim-script-folder-" + String(run).replace(/[^\w-]/g, "_");
            folder.setAttribute("data-nodesim-folder", "");
            var browse = UI.button({ label: "Browse", cls: "btn-sm secondary" });
            var save = UI.button({ label: "Save script", cls: "btn-sm" });
            save.setAttribute("data-nodesim-save", "");
            var row = UI.actions([], "Save the script");
            row.appendChild(browse);
            row.appendChild(save);
            [pre, folder, row, said].forEach(function (n) { body.appendChild(n); });
            browse.hidden = typeof window.openFolderBrowser !== "function";
            browse.addEventListener("click", function () {
                if (typeof window.openFolderBrowser !== "function") return;
                window.openFolderBrowser(folder.id, "", { autoSubmit: false });
            });
            save.addEventListener("click", function () {
                var f = folder.value.trim();
                if (!f) { folder.setAttribute("aria-invalid", "true"); folder.focus(); return; }
                folder.removeAttribute("aria-invalid");
                save.disabled = true;
                call("/api/nodesim/save-script", { run: run, folder: f, window: windowId() }).then(function (a) {
                    save.disabled = false;
                    said.textContent = a && typeof a.line === "string" ? a.line : NO_ANSWER;
                });
            });
            var loaded = false;
            d.addEventListener("toggle", function () {
                if (!d.open || loaded) return;
                loaded = true;
                fetch("/api/nodesim/script?run=" + encodeURIComponent(run)).then(function (r) {
                    var ct = (r.headers && r.headers.get && r.headers.get("Content-Type")) || "";
                    return ct.indexOf("json") >= 0
                        ? r.json().then(function (a) {
                            return a && typeof a.script === "string" ? a.script : str(a && a.line);
                        })
                        : r.text();
                }).then(function (t) { pre.textContent = t; },
                        function () { loaded = false; pre.textContent = NO_ANSWER; });
            });
            scriptHost.appendChild(d);
        }

        function apply(j) {
            if (j === null) {
                S.misses += 1;
                if (S.misses >= MISS_LIMIT) showLost(true);
                return;
            }
            S.misses = 0;
            showLost(false);
            S.answer = j;
            clear(status).appendChild(renderAnswer(j));
            if (typeof j.id === "string" && j.id) S.run = j.id;
            if (j.state === "done") { drawChart(j); drawStrip(j); }
            else if (!LIVE[j.state] && j.state !== "busy") clearResult();
            buttons();
        }
        /* D-17, P2 spec 3.4: keep observing instance notices between clicks. */
        function schedule(gen) {
            if (S.timer) clearTimeout(S.timer);
            S.timer = null;
            if (gen !== S.gen || !root.isConnected) return;
            var live = (S.answer && LIVE[S.answer.state]) || (S.misses > 0 && S.run);
            var idle = S.answer && S.answer.state === "done" && S.run;
            idle = idle || (root.getAttribute("data-warm-pool") === "true" && S.answer
                && ["ready", "renew_offer", "needs_instance", "idle_closed"].indexOf(S.answer.state) >= 0);
            if (!live && !idle) return;
            // P3-f: another window's job ("busy") is live; an idle panel on screen sees it within 2 s.
            var visible = !document.hidden && !root.hidden;
            var delay = live ? POLL_MS : (visible ? VISIBLE_POLL_MS : IDLE_POLL_MS);
            S.timer = setTimeout(function () { poll(gen); }, delay);
        }
        /* D-19, P2 spec 1: display the server's progress and instance rows. */
        function poll(gen) {
            if (gen !== S.gen || !root.isConnected) return Promise.resolve();
            var q = "?window=" + encodeURIComponent(windowId()) + (S.run ? "&id=" + encodeURIComponent(S.run) : "");
            return call("/api/nodesim/status" + q).then(function (j) {
                if (gen !== S.gen) return;
                apply(j);
                if (root.getAttribute("data-warm-pool") === "true") instances();
                schedule(gen);
            });
        }
        function start() {
            if (!canStart()) return Promise.resolve();
            S.gen += 1;
            var gen = S.gen;
            S.run = null;
            S.misses = 0;
            clearResult();
            simBtn.disabled = true;
            return call("/api/nodesim/start", { node: S.node, targets: S.targets.slice(), window_us: S.windowUs,
                                                window: windowId() }).then(function (j) {
                if (gen !== S.gen) return;
                if (j === null) { S.misses = MISS_LIMIT; showLost(true); buttons(); return; }
                apply(j);
                schedule(gen);
            });
        }
        function stop() {
            stopBtn.disabled = true;
            return call("/api/nodesim/stop", { id: S.run, window: windowId() }).then(function (j) {
                stopBtn.disabled = false;
                if (j === null) { S.misses += 1; if (S.misses >= MISS_LIMIT) showLost(true); return; }
                apply(j);
                schedule(S.gen);
            });
        }
        // P3-f: a panel shown again resumes the faster poll at once instead of waiting for a click.
        document.addEventListener("visibilitychange", function onShown() {
            if (!root.isConnected) { document.removeEventListener("visibilitychange", onShown); return; }
            if (!document.hidden && !S.timer) schedule(S.gen);
        });
        simBtn.addEventListener("click", start);
        stopBtn.addEventListener("click", stop);

        /* D-12, P2 spec 2.6: panel opening and a node pick are separate inputs. */
        function intent(explicit) {
            if (root.getAttribute("data-warm-pool") !== "true") return Promise.resolve();
            return call("/api/nodesim/intent", { explicit: explicit, window: windowId() }).then(function (j) {
                if (j && !S.run) { apply(j); schedule(S.gen); }
            });
        }
        /* D-11, P2 spec 3.5: only token-free server lines reach the strip. */
        function instances() {
            return call("/api/nodesim/instances").then(function (j) {
                if (!j || !Array.isArray(j.rows)) return;
                clear(instanceHost);
                if (typeof j.status_line === "string") {
                    instanceHost.appendChild(UI.badge(j.status_line, "neutral"));
                }
                j.rows.forEach(function (r) {
                    if (typeof r.line === "string") instanceHost.appendChild(UI.badge(r.line, "neutral"));
                });
                (j.notices || []).forEach(function (n) {
                    if (typeof n.line === "string") instanceHost.appendChild(UI.badge(n.line, "neutral"));
                    if (n.state === "renew_offer") renewBtn.hidden = false;
                });
            });
        }
        /* D-13, D-17, P2 spec 3.4: replacement requires an explicit action. */
        function replaceInstance(route) {
            return call(route, { window: windowId() }).then(function (j) { apply(j); schedule(S.gen); });
        }
        renewBtn.addEventListener("click", function () { replaceInstance("/api/nodesim/renew"); });
        newBtn.addEventListener("click", function () { replaceInstance("/api/nodesim/new-instance"); });
        /* D-12, P2 spec 1: persist only the keep-warm choice. */
        function settings() {
            if (root.getAttribute("data-warm-pool") !== "true") return Promise.resolve();
            return call("/api/nodesim/settings").then(function (j) {
                if (!j || typeof j.label !== "string") return;
                clear(settingHost);
                var check = UI.check(j.label, !!j.keep_warm, false);
                var input = check.querySelector("input");
                input.setAttribute("data-nodesim-keep-warm", "");
                input.addEventListener("change", function () {
                    call("/api/nodesim/settings", { keep_warm: input.checked }).then(function () {
                        if (input.checked) intent(false);
                    });
                });
                settingHost.appendChild(check);
                settingHost.appendChild(el("p", "ui-note", j.note));
            });
        }

        /* D-12, P2 spec 2.1: prepare imports when this panel opens. */
        function load() {
            var want = root.getAttribute("data-node");
            if (!want) {
                try { want = new URL(window.location.href).searchParams.get("node"); } catch (e) { want = null; }
            }
            // the wanted node is named to the server: a node in a subfolder is not in the folder scan
            var url = "/api/nodesim/nodes" + (want ? "?node=" + encodeURIComponent(want) : "");
            return call(url).then(function (n) {
                if (n === null) { showLost(true); return; }
                S.nodes = n;
                if (n.context) drawContext(n.context); else ctxRow.hidden = true;
                if (typeof n.line === "string" && n.line && n.state) clear(status).appendChild(renderAnswer(n));
                var items = nodeItems(n);
                var found = items.some(function (x) { return x.file === want; });
                S.node = found ? want : "";
                drawPickers();
                pickNode(S.node);
                if (want && !found) { pickNote.textContent = NOT_IN_LIBRARY; pickNote.hidden = false; }
                // a run of this window that is still going (a reload, a page switch) is shown again
                return settings().then(function () { return intent(false); }).then(function () {
                    return poll(S.gen).then(function () { schedule(S.gen); });
                });
            });
        }
        S.ready = load();
        S.start = start;
        S.stop = stop;
        S.poll = function () { return poll(S.gen); };
        S.apply = apply;
        return S;
    }

    function boot() {
        Array.prototype.forEach.call(document.querySelectorAll("[data-nodesim]"), function (r) { mount(r); });
    }
    window.NodeSim = { mount: mount, reportToTimeline: reportToTimeline, chartData: chartData,
                       renderAnswer: renderAnswer, STATES_SHOWN: Object.keys(SHOW), NO_ANSWER: NO_ANSWER };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
    else boot();
    document.addEventListener("htmx:afterSettle", boot);
})();
