/* The pulse class edit screen: form + code pane + waveform preview.
 *
 * The draft file is the one source. The pane saves it explicitly (Save draft,
 * Ctrl+S); the form rewrites only the template lines; an outside editor writes
 * the file directly and the poll (server side: the settle rule) brings it in.
 * When the pane has changes that are not saved and the file moved too, a kit
 * dialog asks which side to keep and names the side that is lost.
 *
 * Every text SM adopts (load, save, form, outside edit) redraws the form from
 * that text at once, unless the user is editing the form: then the form stays,
 * says it is older than the file, and Apply the form asks which one to keep.
 * The waveform redraws after a successful preview; any drawn preview that did
 * not run on the current draft carries the previous-version line.
 */
(function () {
    "use strict";
    var POLL_MS = 1000;
    var S = null;
    var DIRTY_LINE = "Changes in this editor are not saved to the draft yet";
    var FORM_OLD_LINE = "The draft file changed after you started editing the form. Apply the form asks which one to keep.";

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
    function showLines(node, lines) {
        if (!node) return;
        node.textContent = "";
        lines.filter(Boolean).forEach(function (line) { node.appendChild(el("li", null, line)); });
        node.hidden = !node.firstChild;
    }
    function isDark() { return (document.documentElement.getAttribute("data-theme") || "qm") !== "light"; }

    // The status line carries only the server's words (where the draft is
    // saved); the separate dirty note says the pane has unsaved changes.
    function setStatus(line) { if (S) q(S.root, "[data-pce-status]").textContent = line; }
    function markDirty(d) {
        S.dirty = d;
        show(q(S.root, "[data-pce-dirty]"), d ? DIRTY_LINE : "");
    }

    // ------------------------------------------------------------ form
    var COLS = ["Name", "Type", "Default", "Unit"];
    function headRow() {
        var row = el("div", "pce-field-row");
        row.setAttribute("data-pce-field-head", "");
        row.setAttribute("aria-hidden", "true");
        COLS.forEach(function (c) { row.appendChild(el("span", "ui-field-label", c)); });
        row.appendChild(el("span"));
        return row;
    }
    function fieldRow(f) {
        var row = el("div", "pce-field-row");
        row.setAttribute("data-pce-field", "");
        function input(k, label, value) {
            var x = el("input", "ui-input ui-input--block"); x.type = "text"; x.value = value;
            x.setAttribute("aria-label", label); x.setAttribute("data-k", k); return x;
        }
        var name = input("name", "Field name", f.name || "");
        var type = el("select", "ui-select ui-select--block"); type.setAttribute("aria-label", "Field type"); type.setAttribute("data-k", "type");
        ["float", "int", "bool", "str"].forEach(function (t) { var o = el("option", null, t); o.value = t; if (t === (f.type || "float")) o.selected = true; type.appendChild(o); });
        var def = input("default", "Default", f.default == null ? "" : String(f.default));
        var unit = input("unit", "Unit", f.unit || "");
        var rm = el("button", "btn-sm outline", "Remove"); rm.type = "button";
        rm.addEventListener("click", function () { row.remove(); formEdited(); });
        [name, type, def, unit, rm].forEach(function (x) { row.appendChild(x); });
        return row;
    }
    function regionText() {
        var r = S.cm.getProtected();
        if (!r) return null;
        return S.cm.getDoc().slice(r.from, r.to);
    }
    function formEdited() { if (S) S.formDirty = true; }
    // Draw the form from one adopted text: info is its parse, sha its hash.
    function renderForm(info, sha) {
        var host = q(S.root, "[data-pce-fields]");
        var codeOnly = q(S.root, "[data-pce-code-only]");
        host.textContent = "";
        S.formInfo = info;
        S.formSha = sha;
        S.formDirty = false;
        show(q(S.root, "[data-pce-form-note]"), "");
        var editable = !!(info && info.editable && info.spec);
        show(codeOnly, editable ? "" : (info && info.reason) || "The form cannot read this file; edit it in the code view.");
        q(S.root, "[data-pce-apply-form]").disabled = !editable;
        q(S.root, "[data-pce-add-field]").disabled = !editable;
        q(S.root, "[data-pce-formula]").disabled = !editable;
        S.formRegion = editable ? regionText() : null;
        if (!editable) { q(S.root, "[data-pce-formula]").value = ""; return; }
        host.appendChild(headRow());
        info.spec.fields.forEach(function (f) { host.appendChild(fieldRow(f)); });
        q(S.root, "[data-pce-formula]").value = info.spec.formula || "";
    }
    var NUM = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;
    var INT = /^[+-]?\d+$/;
    var NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;
    // One field's default, typed; never invented: an empty or unreadable
    // value is an error naming the field, not a 0, a NaN or a truncation.
    function typed(type, raw) {
        if (type === "str") return { value: raw };
        if (raw === "") return { error: "the default is empty" };
        if (type === "float") return NUM.test(raw) && Number.isFinite(Number(raw)) ? { value: Number(raw) } : { error: raw + " is not a number" };
        if (type === "int") return INT.test(raw) && Number.isSafeInteger(Number(raw)) ? { value: parseInt(raw, 10) } : { error: raw + " is not a whole number" };
        if (type === "bool") {
            if (raw === "True" || raw === "true") return { value: true };
            if (raw === "False" || raw === "false") return { value: false };
            return { error: raw + " is not True or False" };
        }
        return { error: "the type " + type + " is unknown" };
    }
    function readForm() {
        var fields = [], errors = [];
        S.root.querySelectorAll("[data-pce-field]").forEach(function (row, i) {
            var box = function (k) { return row.querySelector('[data-k="' + k + '"]'); };
            var name = box("name").value.trim(), type = box("type").value, raw = box("default").value;
            if (type !== "str") raw = raw.trim();
            var label = name || ("row " + (i + 1));
            var bad = { name: false, default: false };
            if (!name) { errors.push("Field " + label + ": the name is empty"); bad.name = true; }
            else if (!NAME.test(name)) { errors.push("Field " + label + ": the name is not a Python name"); bad.name = true; }
            var t = typed(type, raw);
            if (t.error) { errors.push("Field " + label + ": " + t.error); bad.default = true; }
            ["name", "default"].forEach(function (k) {
                if (bad[k]) box(k).setAttribute("aria-invalid", "true"); else box(k).removeAttribute("aria-invalid");
            });
            fields.push({ name: name, type: type, default: t.value, unit: box("unit").value.trim() });
        });
        return { fields: fields, errors: errors };
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
        S.drawnSha = p.draft_sha256 || null;
        labelPreview();
    }
    // A drawn preview that did not run on the current draft is the previous
    // version, and says so.
    function labelPreview() {
        var prev = q(S.root, "[data-pce-prev-label]");
        var old = S.drawnSha !== undefined && S.drawnSha !== S.baseSha;
        show(prev, old ? S.root.getAttribute("data-pce-previous-line") : "");
    }
    function preview() {
        var python = q(S.root, "[data-pce-env]").value;
        var err = q(S.root, "[data-pce-error]");
        labelPreview();
        if (!python) { show(err, "Choose a Python environment to run the preview."); return Promise.resolve(); }
        S.busy = true;
        return api("POST", "/pulse-class/preview", { cls: S.cls, python: python }).then(function (r) {
            if (!S) return;
            S.busy = false;
            if (r.ok) {
                S.cm.setErrors([]);
                show(err, "");
                draw(r);
                return;
            }
            show(err, (r.line ? "Line " + r.line + ": " : "") + (r.error || "The preview failed."));
            S.cm.setErrors(r.line ? [{ line: r.line, message: r.error || "" }] : []);
            if (r.previous && r.previous.preview) draw(r.previous.preview);
            else labelPreview();
        });
    }

    // ------------------------------------------------------------ file <-> pane
    // Adopt a text the server holds. sent: the pane text the request carried
    // (null for a load or an outside edit). A keystroke made while the request
    // was out is never overwritten: the pane stays, and the base moves only
    // when the file holds exactly what was sent, so the next save asks.
    function apply(p, sent) {
        S.statusLine = p.status || S.statusLine;
        if (S.statusLine) setStatus(S.statusLine);
        if (p.path) q(S.root, "[data-pce-path]").textContent = "Draft file: " + p.path;
        if (p.target != null && !q(S.root, "[data-pce-target]").value) q(S.root, "[data-pce-target]").value = p.target;
        if (sent != null && S.cm.getDoc() !== sent) {
            if (p.text === sent) S.baseSha = p.sha;
            labelPreview();
            return false;
        }
        S.baseSha = p.sha;
        if (S.cm.getDoc() !== p.text) S.cm.setDoc(p.text);
        protect(p.info);
        S.fileInfo = p.info;
        if (!S.formDirty || !S.formInfo) renderForm(p.info, p.sha);
        else show(q(S.root, "[data-pce-form-note]"), formIsOld() ? FORM_OLD_LINE : "");
        markDirty(false);
        labelPreview();
        return true;
    }
    function protect(info) {
        S.cm.setProtected(info && info.protect ? { from: info.protect[0], to: info.protect[1] } : null);
        S.cm.setGuard(!S.unlocked);
    }
    function save(keep, text, fileSha, conflict) {
        var body = { cls: S.cls, text: text != null ? text : S.cm.getDoc(), base_sha: S.baseSha };
        if (keep) body.keep = keep;
        if (fileSha) body.file_sha = fileSha;
        if (S.unlocked) body.hand_edit = true;
        return api("POST", "/pulse-class/save", body).then(function (r) {
            if (!S) return;
            if (r.conflict) { askConflict(r.conflict, body.text); return; }
            if (!r.ok) {
                setStatus(r.error);
                show(q(S.root, "[data-pce-error]"), r.error);
                if (conflict && !r.base_unknown) askConflict(conflict, body.text);
                return;
            }
            apply(r, keep === "file" ? null : body.text);
            return preview();
        });
    }
    function formIsOld() {
        return regionText() !== S.formRegion;
    }
    function applyForm(force) {
        var err = q(S.root, "[data-pce-error]");
        var f = readForm();
        if (f.errors.length) { show(err, "The form was not applied. " + f.errors.join(". ") + "."); return Promise.resolve(); }
        if (force !== true && formIsOld()) {
            askChoice({
                title: "The form is older than the file",
                question: "The template lines changed after you started editing the form. Which one do you keep?",
                choices: [
                    { label: "Apply my form", loses: "The template lines the file has now are replaced by the form.", primary: true,
                      act: function () { applyForm(true); } },
                    { label: "Reload the form from the file", loses: "Your edits in the form are dropped.",
                      act: function () { renderForm(S.fileInfo, S.baseSha); } },
                ],
            });
            return Promise.resolve();
        }
        show(err, "");
        var body = { cls: S.cls, fields: f.fields, formula: q(S.root, "[data-pce-formula]").value,
                     text: S.cm.getDoc(), base_sha: S.baseSha };
        return submitForm(body);
    }
    function submitForm(body, conflict) {
        var err = q(S.root, "[data-pce-error]");
        function retry(keep, _mine, fileSha, c) {
            return submitForm(Object.assign({}, body, { keep: keep, file_sha: fileSha }), c);
        }
        return api("POST", "/pulse-class/form", body).then(function (r) {
            if (!S) return;
            if (r.conflict) { askConflict(r.conflict, body.text, retry); return; }
            if (!r.ok) {
                show(err, r.error);
                if (conflict && !r.base_unknown) askConflict(conflict, body.text, retry);
                return;
            }
            S.formDirty = false;     // the form wrote these values: show them now
            apply(r, body.keep === "file" ? null : body.text);
            return preview();
        });
    }
    // One kit dialog for every question: each choice names what it loses.
    function askChoice(spec) {
        var d = q(S.root, "[data-pce-conflict]");
        if (!d || d.open) return;
        q(d, "[data-pce-conflict-title]").textContent = spec.title;
        var question = q(d, "[data-pce-conflict-question]");
        question.textContent = "";
        spec.question.split("\n").forEach(function (line, i) {
            if (i) question.appendChild(el("br"));
            question.appendChild(document.createTextNode(line));
        });
        var losses = q(d, "[data-pce-conflict-losses]");
        var acts = q(d, "[data-pce-conflict-actions]");
        losses.textContent = ""; acts.textContent = "";
        spec.choices.forEach(function (ch) {
            losses.appendChild(el("li", null, ch.label + ": " + ch.loses));
            var b = el("button", ch.primary ? "btn-sync primary" : "btn-sync outline", ch.label);
            b.type = "button";
            if (ch.keep) b.setAttribute("data-pce-keep", ch.keep);
            b.addEventListener("click", function () {
                if (d.close) d.close();
                d.removeAttribute("open");
                if (S) S.conflictOpen = false;
                ch.act();
            });
            acts.appendChild(b);
        });
        S.conflictOpen = true;
        d.addEventListener("close", function () { if (S) S.conflictOpen = false; }, { once: true });
        if (d.showModal) d.showModal(); else d.setAttribute("open", "");
    }
    function askConflict(c, mine, action) {
        askChoice({
            title: "Two editors changed the draft",
            question: c.question,
            choices: c.choices.map(function (ch) {
                return { label: ch.label, loses: ch.loses, keep: ch.keep, primary: ch.keep === "sm",
                         act: function () { (action || save)(ch.keep, mine, c.file_sha, c); } };
            }),
        });
    }
    function saveFile(target, replaceSha) {
        var dirty = S.dirty;
        show(q(S.root, "[data-pce-file-note]"), dirty ?
            "Save to file writes the saved draft, not the unsaved pane. Save draft first to include your typing." : "");
        var body = { cls: S.cls, target: target };
        if (replaceSha) body.replace_sha = replaceSha;
        return api("POST", "/pulse-class/save-file", body).then(function (r) {
            if (!S) return;
            if (r.confirm) {
                var c = r.confirm;
                askChoice({
                    title: "Replace the class file?",
                    question: c.question + "\n" + c.path + (dirty ? "\nThis writes the saved draft, not the unsaved pane." : ""),
                    choices: [
                        { label: "Yes", loses: c.loses, primary: true,
                          act: function () { saveFile(c.path, c.file_sha); } },
                        { label: "No", loses: "The class file stays as it is.", act: function () {} },
                    ],
                });
                return;
            }
            if (!r.ok) { show(q(S.root, "[data-pce-error]"), r.error); return; }
            show(q(S.root, "[data-pce-error]"), "");
            S.statusLine = r.status;
            show(q(S.root, "[data-pce-file-note]"),
                (r.backup === null ? "Saved; no earlier file to back up." : "Backup: " + r.backup) +
                (dirty || S.dirty ? " The saved draft was written, not the unsaved pane." : "") +
                (r.record_error ? " " + r.record_error : ""));
            setStatus(S.statusLine);
        });
    }
    function saveConfig(confirmedFolder) {
        if (S.dirty) {
            show(q(S.root, "[data-pce-error]"), "Save draft before saving to quam_config.");
            return Promise.resolve();
        }
        var mine = S;
        var body = { cls: S.cls, python: q(S.root, "[data-pce-env]").value,
                     folder: q(S.root, "[data-pce-config-folder]").value.trim() };
        if (confirmedFolder) body.confirmed_folder = confirmedFolder;
        S.busy = true;
        return api("POST", "/pulse-class/save-config", body).then(function (r) {
            mine.busy = false;
            if (S !== mine) return;
            if (r.confirm) {
                var c = r.confirm;
                var gates = c.gates.map(function (g) {
                    return g.pair + ": " + g.gate + (g.live_active ? " (live and active)" : "");
                }).join("\n");
                askChoice({ title: "Save to quam_config", question: "File: " + c.path +
                    "\nThe environment imports quam_config from: " + c.env_folder + "\n" + c.notice +
                    (c.warning ? "\n" + c.warning : "") + (gates ? "\nGates on this chip using this class:\n" + gates : ""),
                    choices: [
                        { label: "Confirm folder and save", primary: true,
                          loses: "SM checks the draft in the lab environment, then writes " + c.path,
                          act: function () { saveConfig(c.folder); } },
                        { label: "Cancel", loses: "nothing is written", act: function () {} },
                    ] });
                return;
            }
            if (!r.ok) { show(q(S.root, "[data-pce-error]"), r.error); return; }
            show(q(S.root, "[data-pce-error]"), "");
            S.statusLine = r.status;
            setStatus(r.status);
            showLines(q(S.root, "[data-pce-config-result]"), ["Saved to " + r.target, r.dependency,
                r.restart, r.warning, r.chip_warning, r.proportional_warning, r.record_error, r.backup ? "Backup of the replaced file: " + r.backup : ""]);
        }).catch(function (err) {
            mine.busy = false;
            if (S === mine) show(q(S.root, "[data-pce-error]"), String(err));
        });
    }
    function poll() {
        if (!S || !document.body.contains(S.root)) { stop(); return; }
        if (S.busy || S.conflictOpen || S.polling) return;
        S.polling = true;
        var mine = S;
        // dirty=1 always: the answer must carry the question for the case
        // where the user types while the request is out.
        return api("GET", "/pulse-class/poll?cls=" + encodeURIComponent(S.cls) + "&dirty=1").then(function (r) {
            mine.polling = false;
            if (S !== mine || !r) return;
            if (r.status_line) { S.statusLine = r.status_line; setStatus(r.status_line); }
            if (r.status !== "changed" || r.sha === S.baseSha) return;
            if (S.dirty) {
                // not adopted: the base stays, so a save without this answer asks too
                if (r.conflict) askConflict(r.conflict, S.cm.getDoc());
                return;
            }
            apply({ sha: r.sha, text: r.text, info: r.info, status: r.status_line }, null);
            return preview();
        });
    }
    function stop() {
        if (S && S.timer) clearInterval(S.timer);
        if (S && S.themeWatch) S.themeWatch.disconnect();
        S = null;
    }

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
        S = { root: root, cls: cls, dirty: false, baseSha: null, formInfo: null, formSha: null, formRegion: null,
              formDirty: false, fileInfo: null, drawnSha: undefined, unlocked: false };
        var fileNote = el("p", "ui-field-hint");
        fileNote.setAttribute("data-pce-file-note", "");
        fileNote.setAttribute("role", "status");
        fileNote.hidden = true;
        q(root, "[data-pce-status]").after(fileNote);
        S.cm = window.SmCodePython.create(q(root, "[data-pce-code]"), {
            doc: "", dark: isDark(),
            onChange: function () { if (S) markDirty(true); },
        });
        if (window.MutationObserver) {
            var cm = S.cm;
            S.themeWatch = new MutationObserver(function () { cm.setDark(isDark()); });
            S.themeWatch.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
        }
        var formBox = q(root, "[data-pce-fields]");
        formBox.addEventListener("input", formEdited);
        formBox.addEventListener("change", formEdited);
        q(root, "[data-pce-formula]").addEventListener("input", formEdited);
        q(root, "[data-pce-save]").addEventListener("click", function () { save(); });
        q(root, "[data-pce-apply-form]").addEventListener("click", function () { applyForm(); });
        q(root, "[data-pce-add-field]").addEventListener("click", function () {
            formBox.appendChild(fieldRow({ name: "", type: "float", default: "", unit: "" }));
            formEdited();
        });
        var unlock = q(root, "[data-pce-unlock]");
        unlock.addEventListener("click", function () {
            S.unlocked = !S.unlocked;
            unlock.setAttribute("aria-pressed", S.unlocked ? "true" : "false");
            unlock.textContent = S.unlocked ? "Let the form own the template lines" : "Edit the template lines by hand";
            S.cm.setGuard(!S.unlocked);
        });
        q(root, "[data-pce-code]").addEventListener("keydown", function (ev) {
            if ((ev.ctrlKey || ev.metaKey) && (ev.key === "s" || ev.key === "S")) { ev.preventDefault(); save(); }
        });
        q(root, "[data-pce-save-file]").addEventListener("click", function () {
            saveFile(q(root, "[data-pce-target]").value);
        });
        q(root, "[data-pce-save-config]").addEventListener("click", function () { saveConfig(); });
        return api("GET", "/pulse-class/load?cls=" + encodeURIComponent(cls)).then(function (r) {
            if (!S) return;
            if (!r.ok) { setStatus(r.error); return; }
            apply(r, null);
            if (r.last_preview) draw(r.last_preview);
            S.timer = setInterval(poll, POLL_MS);
        });
    }
    document.addEventListener("htmx:afterSettle", boot);
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
    window.PulseClassEdit = { state: function () { return S; }, _poll: poll, _save: save, _applyForm: applyForm, _boot: boot };
})();
