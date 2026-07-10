/* Live waveform preview for the Generate-Config wizard's Populate step (6).
 *
 * Reuses the Pulses page's in-process synth: a debounced, stateless POST
 * /api/pulse/synth with {qclass, params} (the wizard has no loaded chip, so it
 * uses the store-independent qclass branch) renders the pulse the user is
 * editing into a single shared panel via window.PulsesPage.renderPulsePlot.
 *
 * Fully decoupled from generate.js: a delegated focusin/input listener on
 * document watches the populate cells (.gen-pop-in[data-field]), reads the
 * row's COMMITTED base-unit values from window.QuamGen.state.spec.populate
 * (so no unit conversion is needed here) + the chip's 2Q gate, maps them to a
 * (qclass, params) pair, and previews it. House convention: IIFE exposing
 * window.GenPreview (like generate.js / pulses.js).
 */
window.GenPreview = (function () {
  "use strict";

  var DEBOUNCE_MS = 150;
  var _timer = null;
  var _gen = 0;          // drops stale synth responses
  var _last = null;      // last previewed {group, rid}

  // User on/off preference (persisted): some users find the auto-popping preview
  // distracting and want it gone. Default ON; the × on the panel or the toggle
  // checkbox sets it OFF, and it stays off across steps/sessions.
  var PREF_KEY = "quam_gen_preview_off";
  function isDisabled() {
    try { return localStorage.getItem(PREF_KEY) === "1"; } catch (e) { return false; }
  }
  function syncToggle() {
    var cb = document.getElementById("gen-preview-toggle");
    if (cb) cb.checked = !isDisabled();
  }
  function setDisabled(off) {
    try { localStorage.setItem(PREF_KEY, off ? "1" : "0"); } catch (e) { /* ignore */ }
    syncToggle();
    if (off) {
      if (_timer) { clearTimeout(_timer); _timer = null; }
      _gen++; _last = null;
      var p = panel(); if (p) p.hidden = true;
    }
  }

  function num(v, dflt) {
    if (v === null || v === undefined || v === "") return dflt;
    var n = typeof v === "number" ? v : parseFloat(String(v).replace(/,/g, ""));
    return isFinite(n) ? n : dflt;
  }

  function quamState() {
    return (window.QuamGen && window.QuamGen.state) || null;
  }

  // Committed (base-unit) values for one populate row, e.g. populate.qubit.q1.
  function rowValues(group, rid) {
    var st = quamState();
    var pop = (st && st.spec && st.spec.populate) || {};
    return ((pop[group] || {})[rid]) || {};
  }

  // (group, rid, field) -> { qclass, params, title } or null when the row is not
  // a previewable pulse (qubit-frequency / flux rows have no waveform). `field`
  // lets a row preview the SPECIFIC pulse being edited (e.g. a CR cancel tone).
  function describe(group, rid, field) {
    var st = quamState();
    if (!st) return null;

    if (group === "pulses") {
      var anh = num((rowValues("qubit", rid) || {}).anharmonicity, -200e6) || -200e6;
      var v = rowValues("pulses", rid);
      return {
        qclass: "DragCosinePulse",
        title: "x180 (DRAG) · " + rid,
        params: {
          length: num(v.x180_length, 40),
          amplitude: num(v.x180_amplitude, 0.1),
          axis_angle: 0,
          alpha: num(v.drag_alpha, 0),
          anharmonicity: anh,
          detuning: num(v.drag_detuning, 0),
        },
      };
    }

    if (group === "resonator") {
      var r = rowValues("resonator", rid);
      return {
        qclass: "SquareReadoutPulse",
        title: "readout · " + rid,
        params: {
          length: num(r.readout_length, 1000),
          amplitude: num(r.readout_amplitude, 0.1),
        },
      };
    }

    if (group === "pairs") {
      var p = rowValues("pairs", rid);
      if ((st.pairGate || "") === "cr") {
        // Editing a cancel-tone field previews the target-side cancel pulse
        // (run_build seeds it at cr_cancel_amplitude); otherwise the drive square.
        if (field === "cr_cancel_amplitude" || field === "cr_cancel_phase") {
          return {
            qclass: "SquarePulse", title: "CR cancel · " + rid,
            params: { length: 100, amplitude: num(p.cr_cancel_amplitude, 0.1), axis_angle: 0 },
          };
        }
        return {
          qclass: "SquarePulse",
          title: "CR square drive · " + rid,
          params: { length: num(p.cr_square_length, 100),
                    amplitude: num(p.cr_drive_amplitude, 1.0), axis_angle: 0 },
        };
      }
      return czDescribe(rid, p.cz_variant || "unipolar",
                        num(p.cz_interaction_duration, 100),
                        num(p.cz_amplitude, 0.1));
    }

    return null;   // qubit (frequencies), flux — not a pulse
  }

  function czDescribe(rid, variant, dur, amp) {
    var t = "CZ " + variant + " · " + rid;
    if (variant === "flattop") {
      return { qclass: "_FlatTopGaussianPulse", title: t,
        params: { amplitude: amp, flat_length: dur, smoothing_length: 20, post_zero_padding_length: 20 } };
    }
    if (variant === "bipolar") {
      return { qclass: "_CosineBipolarPulse", title: t,
        params: { amplitude: amp, flat_length: dur, smoothing_length: 20, post_zero_padding_length: 20 } };
    }
    if (variant === "SNZ") {
      return { qclass: "SNZPulse", title: t,
        params: { amplitude: amp, flat_length: dur, t_phi_eff: 0, padding: 20 } };
    }
    if (variant === "flattop_erf") {
      return { qclass: "ErfSquarePulse", title: t,
        params: { amplitude: amp, flat_length: dur, risetime_samples: 16, post_zero_padding_length: 20 } };
    }
    return { qclass: "SquarePulse", title: t, params: { length: dur, amplitude: amp } };
  }

  function panel() { return document.getElementById("gen-pop-preview"); }

  function showErr(p, msg) {
    var el = p && p.querySelector(".gen-pop-preview-err");
    if (el) { el.textContent = msg || ""; el.hidden = !msg; }
  }

  function firstParamError(pe) {
    if (!pe) return "preview unavailable";
    var k = Object.keys(pe);
    return k.length ? k[0] + ": " + pe[k[0]] : "preview unavailable";
  }

  function fetchSynth(body, cb) {
    var gen = ++_gen;
    fetch("/api/pulse/synth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (gen !== _gen) return;   // superseded
      cb(data);
    }).catch(function () { /* keep the last plot on a network error */ });
  }

  // True only while the populate step (the panel's own .gen-panel) is the active
  // step — so a late synth response / debounced timer never renders off-step.
  function stepActive(p) {
    var sec = p && p.closest && p.closest(".gen-panel");
    return !sec || sec.classList.contains("active");
  }

  // Reset the panel (called when the populate step is (re-)entered, so a stale
  // plot/title from a previous visit never lingers).
  function reset() {
    if (_timer) { clearTimeout(_timer); _timer = null; }
    _gen++;            // invalidate any in-flight synth response
    _last = null;
    syncToggle();      // reflect the persisted on/off pref on (re-)entry
    var p = panel();
    if (!p) return;
    p.hidden = true;
    var title = p.querySelector(".gen-pop-preview-title");
    if (title) title.textContent = "";
    var pd = document.getElementById("gen-pop-preview-plot");
    if (pd) pd.innerHTML = "";
    showErr(p, "");
  }

  function render(group, rid, field) {
    if (isDisabled()) { var pp = panel(); if (pp) pp.hidden = true; return; }
    var d = describe(group, rid, field);
    var p = panel();
    if (!p || !stepActive(p)) return;
    if (!d) { p.hidden = true; return; }
    _last = { group: group, rid: rid, field: field };
    p.hidden = false;
    var title = p.querySelector(".gen-pop-preview-title");
    if (title) title.textContent = d.title;
    fetchSynth({ qclass: d.qclass, params: d.params }, function (data) {
      if (!document.body.contains(p) || !stepActive(p)) return;
      if (data.ok && data.plot && data.plot.ok &&
          window.PulsesPage && window.PulsesPage.renderPulsePlot) {
        window.PulsesPage.renderPulsePlot("gen-pop-preview-plot", data.plot);
        showErr(p, "");
      } else {
        // Clear the old trace so a stale waveform isn't left drawn under the error.
        var pd = document.getElementById("gen-pop-preview-plot");
        if (pd) pd.innerHTML = "";
        showErr(p, data.error || firstParamError(data.param_errors));
      }
    });
  }

  function schedule(group, rid, field) {
    if (_timer) clearTimeout(_timer);
    _timer = setTimeout(function () { render(group, rid, field); }, DEBOUNCE_MS);
  }

  function onEvt(e) {
    if (isDisabled()) return;          // user turned the preview off
    var el = e.target;
    if (!el || !el.classList || !el.classList.contains("gen-pop-in") || !el.dataset.field) return;
    var g = el.dataset.group, rid = el.dataset.rid, field = el.dataset.field;
    if (!describe(g, rid, field)) {
      // A real populate cell with no waveform (qubit frequency / flux) — hide the
      // sticky panel + cancel any pending synth so it stops asserting the previous
      // pulse over an unrelated edit.
      if (_timer) { clearTimeout(_timer); _timer = null; }
      var p = panel(); if (p) p.hidden = true; _last = null;
      return;
    }
    schedule(g, rid, field);
  }

  // Delegated: focusing or typing in a previewable populate cell drives the panel.
  document.addEventListener("focusin", onEvt);
  document.addEventListener("input", onEvt);

  // Delegated controls (the panel + toggle are swapped-in HTMX content): the panel
  // × turns the preview OFF; the toggle checkbox flips it either way.
  document.addEventListener("click", function (e) {
    var t = e.target;
    if (t && t.id === "gen-pop-preview-close") { e.preventDefault(); setDisabled(true); }
  });
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t && t.id === "gen-preview-toggle") { setDisabled(!t.checked); }
  });

  return { render: render, describe: describe, reset: reset, syncToggle: syncToggle };
})();
