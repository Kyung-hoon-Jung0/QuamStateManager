/* The pulse class edit screen: form + code pane + waveform preview.
 *
 * The draft file is the one source. The pane saves it explicitly (Save draft,
 * Ctrl+S); the form rewrites only the template lines; an outside editor writes
 * the file directly and the poll (server side: the settle rule) brings it in.
 * When the pane has changes that are not saved and the file moved too, a kit
 * dialog asks which side to keep and names the side that is lost.
 * The form, waveform and timeline redraw after a successful preview; a failed
 * preview keeps the last good one, labelled as the previous version.
 */
(function () {
    "use strict";
    var POLL_MS = 1000;
    var S = null;

    function q(root, sel) { return root.querySelector(sel); }
    function el(tag, cls, text) {
        var e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text != null) e.textContent = text;
        return e;
    }
    function api(method, url, body) {
        var o = { method: method, headers: { "Accept": "application/json" } };
        if (body) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(body); }
        return fetch(url, o).then(function (r) {
            return r.json().catch(function () { return { ok: false, error: "The server sent no answer." }; })
                .then(function (j) { j._http = r.status; return j; });
        }, function () { return { ok: false, error: "SM did not answer." }; });
    }
    function show(node, text) { if (!node) return; node.textContent = text || ""; node.hidden = !text; }

    function setStatus(line) { if (S) q(S.root, "[data-pce-status]").textContent = line; }
    function markDirty(d) {
        S.dirty = d;
        if (d) setStatus("Draft, not saved");
        else if (S.statusLine) setStatus(S.statusLine);
    }

    // ------------------------------------------------------------ form
    function fieldRow(f) {
        var row = el("div", "pce-field-row");
        row.setAttribute("data-pce-field", "");
        var name = el("input", "ui-input"); name.type = "text"; name.value = f.name || ""; name.setAttribute("aria-label", "Field name"); name.setAttribute("data-k", "name");
        var type = el("select", "ui-select"); type.setAttribute("aria-label", "Field type"); type.setAttribute("data-k", "type");
        ["float", "int", "bool", "str"].forEach(function (t) { var o = el("option", null, t); o.value = t; if (t === (f.type || "float")) o.selected = true; type.appendChild(o); });
        var def = el("input", "ui-input"); def.type = "text"; def.value = f.default == null ? "" : String(f.default); def.setAttribute("aria-label", "Default"); def.setAttribute("data-k", "default");
        var unit = el("input", "ui-input"); unit.type = "text"; unit.value = f.unit || ""; unit.setAttribute("aria-label", "Unit"); unit.setAttribute("data-k", "unit");
        var rm = el("button", "btn-sm outline", "Remove"); rm.type = "button";
        rm.addEventListener("click", function () { row.remove(); });
        [name, type, def, unit, rm].forEach(function (x) { row.appendChild(x); });
        return row;
    }
    function renderForm(info) {
        var host = q(S.root, "[data-pce-fields]");
        var codeOnly = q(S.root, "[data-pce-code-only]");
        host.textContent = "";
        S.formInfo = info;
        var editable = !!(info && info.editable && info.spec);
        show(codeOnly, editable ? "" : (info && info.reason) || "The form cannot read this file; edit it in the code view.");
        q(S.root, "[data-pce-apply-form]").disabled = !editable;
        q(S.root, "[data-pce-add-field]").disabled = !editable;
        q(S.root, "[data-pce-formula]").disabled = !editable;
        if (!editable) return;
        info.spec.fields.forEach(function (f) { host.appendChild(fieldRow(f)); });
        q(S.root, "[data-pce-formula]").value = info.spec.formula || "";
    }
    function typed(type, raw) {
        if (type === "float") return Number(raw);
        if (type === "int") return parseInt(raw, 10);
        if (type === "bool") return raw === "True" || raw === "true" || raw === "1";
        return raw;
    }
    function readForm() {
        var out = [];
        S.root.querySelectorAll("[data-pce-field]").forEach(function (row) {
            var g = function (k) { return row.querySelector('[data-k="' + k + '"]').value; };
            out.push({ name: g("name").trim(), type: g("type"), default: typed(g("type"), g("default").trim()), unit: g("unit").trim() });
        });
        return out;
    }

    // ------------------------------------------------------------ preview
    function draw(p) {
        var sr = p.sampling_rate || 1e9;
        var v = p.samples || [];
        var t = v.map(function (_x, i) { return i * 1e9 / sr; });
        var wave = q(S.root, "[data-pce-wave]");
        if (window._plotlyRender && wave) {
            window._plotlyRender(wave.id, [{ x: t, y: v, mode: "lines", name: S.cls }],
                { margin: { l: 48, r: 12, t: 12, b: 36 }, xaxis: { title: { text: "Time (ns)" } }, yaxis: { title: { text: "Amplitude (V)" } }, showlegend: false },
                { displaylogo: false, responsive: true });
        }
        var tl = q(S.root, "[data-pce-timeline]");
        if (window.GateTimeline && tl) {
            var end = v.length * 1e9 / sr;
            window.GateTimeline.render(tl, { lanes: [{ key: "pulse", label: S.cls, group: "", segments: [
                { operation: S.cls, start_ns: 0, end_ns: end, t_ns: t, v: v }] }] }, {});
        }
        var pr = p.proportional || {};
        var bits = [v.length + " samples at " + (sr / 1e9) + " GS/s"];
        if (pr.proportional === true) bits.push("the waveform scales with the amplitude");
        else if (pr.proportional === false) bits.push("the waveform does not scale with the amplitude");
        (p.warnings || []).forEach(function (w) { bits.push(String(w)); });
        q(S.root, "[data-pce-checks]").textContent = bits.join(". ") + ".";
    }
    function preview() {
        var python = q(S.root, "[data-pce-env]").value;
        var err = q(S.root, "[data-pce-error]");
        var prev = q(S.root, "[data-pce-prev-label]");
        if (!python) { show(err, "Choose a Python environment to run the preview."); return Promise.resolve(); }
        S.busy = true;
        return api("POST", "/pulse-class/preview", { cls: S.cls, python: python }).then(function (r) {
            S.busy = false;
            if (r.ok) {
                S.cm.setErrors([]);
                show(err, ""); show(prev, "");
                if (S.pendingInfo) renderForm(S.pendingInfo);
                S.pendingInfo = null;
                draw(r);
                return;
            }
            show(err, (r.line ? "Line " + r.line + ": " : "") + (r.error || "The preview failed."));
            S.cm.setErrors(r.line ? [{ line: r.line, message: r.error || "" }] : []);
            if (r.previous && r.previous.preview) { show(prev, r.previous.line); draw(r.previous.preview); }
        });
    }

    // ------------------------------------------------------------ file <-> pane
    function apply(p) {
        S.baseSha = p.sha;
        S.statusLine = p.status || S.statusLine;
        if (S.cm.getDoc() !== p.text) S.cm.setDoc(p.text);
        protect(p.info);
        if (!S.formInfo) renderForm(p.info);   // first load
        else S.pendingInfo = p.info;           // later: after a successful preview
        if (p.path) q(S.root, "[data-pce-path]").textContent = "Draft file: " + p.path;
        if (p.target != null && !q(S.root, "[data-pce-target]").value) q(S.root, "[data-pce-target]").value = p.target;
        markDirty(false);
    }
    function protect(info) {
        S.protectRange = info && info.protect ? { from: info.protect[0], to: info.protect[1] } : null;
        S.cm.setProtected(S.unlocked ? null : S.protectRange);
    }
    function save(keep, text) {
        var body = { cls: S.cls, text: text != null ? text : S.cm.getDoc(), base_sha: S.baseSha };
        if (keep) body.keep = keep;
        return api("POST", "/pulse-class/save", body).then(function (r) {
            if (r.conflict) { askConflict(r.conflict, body.text); return; }
            if (!r.ok) { show(q(S.root, "[data-pce-error]"), r.error); return; }
            apply(r);
            return preview();
        });
    }
    function applyForm() {
        var body = { cls: S.cls, fields: readForm(), formula: q(S.root, "[data-pce-formula]").value,
                     text: S.cm.getDoc(), base_sha: S.baseSha };
        return api("POST", "/pulse-class/form", body).then(function (r) {
            if (r.conflict) { askConflict(r.conflict, body.text); return; }
            if (!r.ok) { show(q(S.root, "[data-pce-error]"), r.error); return; }
            S.formInfo = null;      // the form wrote these values: show them now
            apply(r);
            return preview();
        });
    }
    function askConflict(c, mine) {
        var d = q(S.root, "[data-pce-conflict]");
        if (!d || d.open) return;
        q(d, "[data-pce-conflict-question]").textContent = c.question;
        var losses = q(d, "[data-pce-conflict-losses]");
        var acts = q(d, "[data-pce-conflict-actions]");
        losses.textContent = ""; acts.textContent = "";
        c.choices.forEach(function (ch) {
            losses.appendChild(el("li", null, ch.label + ": " + ch.loses));
            var b = el("button", ch.keep === "sm" ? "btn-sync primary" : "btn-sync outline", ch.label);
            b.type = "button";
            b.setAttribute("data-pce-keep", ch.keep);
            b.addEventListener("click", function () {
                d.close();
                save(ch.keep, ch.keep === "sm" ? mine : undefined);
            });
            acts.appendChild(b);
        });
        S.conflictOpen = true;
        d.addEventListener("close", function () { S && (S.conflictOpen = false); }, { once: true });
        if (d.showModal) d.showModal(); else d.setAttribute("open", "");
    }
    function poll() {
        if (!S || !document.body.contains(S.root)) { stop(); return; }
        if (S.busy || S.conflictOpen || S.polling) return;
        S.polling = true;
        api("GET", "/pulse-class/poll?cls=" + encodeURIComponent(S.cls) + "&dirty=" + (S.dirty ? "1" : "0")).then(function (r) {
            S && (S.polling = false);
            if (!S || !r) return;
            if (r.status_line) { S.statusLine = r.status_line; if (!S.dirty) setStatus(r.status_line); }
            if (r.status !== "changed") return;
            if (S.dirty && r.conflict) { askConflict(r.conflict, S.cm.getDoc()); return; }
            apply({ sha: r.sha, text: r.text, info: r.info, status: r.status_line });
            preview();
        });
    }
    function stop() { if (S && S.timer) clearInterval(S.timer); S = null; }

    // ------------------------------------------------------------ boot
    function bootNew(root) {
        var btn = q(root, "[data-pce-new]");
        if (!btn) return;
        btn.addEventListener("click", function () {
            var name = q(root, "[data-pce-new-name]").value.trim();
            api("POST", "/pulse-class/new", { cls: name }).then(function (r) {
                if (!r.ok) { show(q(root, "[data-pce-new-error]"), r.error); return; }
                var url = "/pulse-class?cls=" + encodeURIComponent(r.cls);
                if (window.htmx) window.htmx.ajax("GET", url, { target: "#table-pane", swap: "innerHTML" }).then(function () { history.pushState({}, "", url); });
                else location.href = url;
            });
        });
    }
    function boot() {
        var root = document.getElementById("pulse-class-page");
        if (!root || root.hasAttribute("data-pce-booted")) return;
        root.setAttribute("data-pce-booted", "");
        if (S) stop();
        var cls = root.getAttribute("data-pce-cls");
        if (!cls) { bootNew(root); return; }
        if (!window.SmCodePython) { show(q(root, "[data-pce-error]"), "The code editor did not load."); return; }
        S = { root: root, cls: cls, dirty: false, baseSha: null, formInfo: null, pendingInfo: null, unlocked: false };
        S.cm = window.SmCodePython.create(q(root, "[data-pce-code]"), {
            doc: "", dark: document.documentElement.getAttribute("data-theme") === "dark",
            onChange: function () { if (S) markDirty(true); },
        });
        q(root, "[data-pce-save]").addEventListener("click", function () { save(); });
        q(root, "[data-pce-apply-form]").addEventListener("click", applyForm);
        q(root, "[data-pce-add-field]").addEventListener("click", function () {
            q(root, "[data-pce-fields]").appendChild(fieldRow({ name: "", type: "float", default: 0, unit: "" }));
        });
        var unlock = q(root, "[data-pce-unlock]");
        unlock.addEventListener("click", function () {
            S.unlocked = !S.unlocked;
            unlock.setAttribute("aria-pressed", S.unlocked ? "true" : "false");
            unlock.textContent = S.unlocked ? "Let the form own the template lines" : "Edit the template lines by hand";
            S.cm.setProtected(S.unlocked ? null : S.protectRange);
        });
        q(root, "[data-pce-code]").addEventListener("keydown", function (ev) {
            if ((ev.ctrlKey || ev.metaKey) && (ev.key === "s" || ev.key === "S")) { ev.preventDefault(); save(); }
        });
        q(root, "[data-pce-save-file]").addEventListener("click", function () {
            api("POST", "/pulse-class/save-file", { cls: S.cls, target: q(root, "[data-pce-target]").value }).then(function (r) {
                if (!r.ok) { show(q(root, "[data-pce-error]"), r.error); return; }
                S.statusLine = r.status;
                if (!S.dirty) setStatus(r.status);
            });
        });
        api("GET", "/pulse-class/load?cls=" + encodeURIComponent(cls)).then(function (r) {
            if (!S) return;
            if (!r.ok) { setStatus(r.error); return; }
            apply(r);
            if (r.last_preview) draw(r.last_preview);
            S.timer = setInterval(poll, POLL_MS);
        });
    }
    document.addEventListener("htmx:afterSettle", boot);
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
    window.PulseClassEdit = { state: function () { return S; }, _poll: poll, _save: save };
})();
