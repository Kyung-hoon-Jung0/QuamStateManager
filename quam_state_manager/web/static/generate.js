/* Generate Config wizard — client-side controller.
 *
 * The wizard is mostly client-side: it assembles a "spec" object across 8
 * steps (environment, network, chassis, qubits, wiring, populate, output,
 * review) then POSTs it to /generate/allocate and /generate/build.
 *
 * Exposed as window.QuamGen.
 */
(function () {
  "use strict";

  var STEP_COUNT = 8;

  // The spec assembled by the wizard — the contract in
  // docs/27_config_generator.md. Steps mutate this; step 8 submits it.
  function freshSpec() {
    return {
      network: { host: "", cluster_name: "", port: null },
      instruments: { controllers: [], opx_plus: [], octaves: [] },
      qubits: [],
      qubit_pairs: [],
      twpas: [],
      lines: [],
      populate: {},
      pair_gate: "cz_tunable"
    };
  }

  var state = {
    step: 1,
    spec: freshSpec(),
    env: null,            // selected interpreter path
    allocation: null,     // last /generate/allocate result, keyed by element
    pairsTouched: false,  // user hand-edited pairs -> stop auto-filling them
    wiringTouched: false, // user drag-edited the wiring -> keep their groups
    // Qubit naming scheme (step 4): "one_based" (q1, q2, … — the historical
    // default), "zero_based" (q0, q1, …), "grid" (board-derived letters —
    // qA1, qB2; one-shot Apply), or "custom" (prefix + start index).
    naming: { preset: "one_based", prefix: "q", start: 1 },
    // A per-qubit hand rename detaches the set from the scheme: count changes
    // stop regenerating names and the scheme-conformance gate turns off.
    namesTouched: false,
    muxSize: 6,           // step-4 "Qubits per readout feedline" (DOM-mirrored)
    outputPath: "",       // step-7 destination folder (DOM-mirrored)
    scriptsEnabled: false, // step-7: also export the editable Python bundle
    scriptsPath: "",       // …into this folder (DOM-mirrored)
    // step-6 populate display units; overlaid from localStorage on entry
    populateUnits: { freq: "GHz", time: "ns", volt: "V", amp: "0-1" },
    // step-6 power input mode — "manual" (FSP + amplitude, today's flow) or
    // "absolute" (pulse powers typed in dBm; the port FSP is auto-allocated
    // and amplitudes derived). Overlaid from localStorage on entry.
    powerMode: "manual",
    // Line-type toggles (step 4); derived from hardware + user choice.
    qubitFlux: true,      // qubit z flux lines (requires LF-FEM)
    couplerFlux: true,    // DERIVED mirror: true when pairGate==="cz_tunable" and
                          // an LF-FEM + pairs exist. Kept so the populate/review
                          // code that reads state.couplerFlux stays unchanged.
    // 2-qubit gate emitted per pair:
    //   "cr"          Cross-resonance — MW drive on the control qubit (fixed-freq or any).
    //   "cz_fixed"    CZ, fixed coupler — needs qubit flux (z) only; run_build
    //                 creates the pair (no coupler wiring line).
    //   "cz_tunable"  CZ, tunable coupler — needs qubit flux (z) + a coupler flux line.
    pairGate: "cz_tunable",
    // CR drive port mode (docs/54): "dedicated" = one MW port per CR line;
    // "shared_xy" = the customer's dual-upconverter layout (CR/ZZ ride the
    // control qubit's own xy port on upconverter 2). Only used when
    // pairGate === "cr".
    crPortMode: "dedicated",
    // ZZ (Stark-CZ) drive lines per pair — CR chips only. Adds a zz_drive
    // wiring line + the StarkInducedCZGate seed alongside every CR line.
    zzEnabled: false,
    // Explicit chip architecture (step 4) — the single source of truth that drives
    // qubitFlux + pairGate. The qubit-flux/gate controls below are derived from it.
    // The physically-unrepresentable "fixed-frequency qubits + tunable coupler" is
    // deliberately NOT an option (quam_builder's CZGate hardcodes a qubit-z pulse —
    // a coupler-only CZ can't be expressed; fixed-freq chips use cross-resonance).
    //   "flux_tunable_coupler"        flux-tunable qubits + tunable coupler → cz_tunable
    //   "flux_tunable_fixed_coupler"  flux-tunable qubits + fixed coupler   → cz_fixed
    //   "fixed_frequency"             fixed-frequency qubits                → cr
    chipArch: "flux_tunable_coupler",

    // Re-generate mode — set by QuamGen.hydrateFromSpec() when the wizard is
    // re-opened pre-filled from an existing chip. "generate" builds fresh;
    // "regenerate" posts to buildEndpoint and merges the source chip's values.
    mode: "generate",
    buildEndpoint: "/generate/build",
    sourcePath: null      // source chip folder (regenerate: merge values from here)
  };

  // A readout feedline multiplexes at most 8 resonators on one MW-FEM
  // in/out pair (hardware bound — was wrongly capped at 16). Every read of
  // the mux size funnels through this clamp so a stale draft / hand-typed
  // value can never produce an unbuildable 9+-tone feedline.
  var MUX_MAX = 8;
  function clampMux(m) {
    m = parseInt(m, 10);
    if (isNaN(m) || m < 1) return 6;
    return Math.min(m, MUX_MAX);
  }

  // chip architecture → (qubitFlux, pairGate). Single source of truth.
  var CHIP_ARCH = {
    flux_tunable_coupler:       { qubitFlux: true,  pairGate: "cz_tunable" },
    flux_tunable_fixed_coupler: { qubitFlux: true,  pairGate: "cz_fixed" },
    fixed_frequency:            { qubitFlux: false, pairGate: "cr" }
  };

  // -- hardware introspection helpers -----------------------------------

  function hasMwFem() {
    return state.spec.instruments.controllers.some(function (c) {
      return (c.fems || []).some(function (f) { return f.fem === "mw"; });
    });
  }

  function hasLfFem() {
    return state.spec.instruments.controllers.some(function (c) {
      return (c.fems || []).some(function (f) { return f.fem === "lf"; });
    });
  }

  function hasOpxPlus() {
    return (state.spec.instruments.opx_plus || []).length > 0;
  }

  // step number -> function() returning an error string (block) or null (ok)
  var stepGuards = {
    1: function () {
      return state.env ? null : "Select an environment before continuing.";
    },
    2: function () {
      var net = state.spec.network;
      if (!net.host) return "Enter the QOP host IP.";
      if (!net.cluster_name) return "Enter the cluster name.";
      return null;
    },
    3: function () {
      var inst = state.spec.instruments;
      // OPX+ / Octave are not yet buildable by the wizard, so the only valid
      // hardware is OPX1000 chassis with FEM modules. (The +OPX+ / +Octave
      // buttons are hidden; a stale draft could still carry them — ignore.)
      if (!inst.controllers.length) {
        return "Add at least one OPX1000 chassis with a FEM module.";
      }
      if (inst.controllers.some(function (c) { return !c.fems.length; })) {
        return "Every OPX1000 needs at least one FEM module.";
      }
      return null;
    },
    4: function () {
      if (!state.spec.qubits.length) return "Set the number of qubits.";
      var badPair = state.spec.qubit_pairs.some(function (p) {
        return !p[0] || !p[1] || p[0] === p[1];
      });
      if (badPair) return "Every qubit pair needs two different qubits.";
      if (state.spec.twpas.some(function (t) { return !t.id; })) {
        return "Every TWPA needs an id.";
      }
      // Ensure at least one line type is active — a config with qubits but
      // zero control lines would produce a chip with no channels.
      var mw = hasMwFem() || hasOpxPlus();
      var lf = hasLfFem() || hasOpxPlus();
      if (!mw && !(lf && state.qubitFlux) && !(lf && state.couplerFlux)) {
        return "No control lines enabled — enable at least one line type, " +
               "or add a MW-FEM / LF-FEM in step 3.";
      }
      // Hard block the physically-unrepresentable combo (defense in depth — the
      // Chip-architecture selector doesn't offer it, but a stale draft might).
      // Fixed-frequency qubits (no z) can't drive a tunable-coupler CZ: quam_builder's
      // CZGate plays on the qubit z line, so a coupler-only CZ can't be expressed.
      if (!state.qubitFlux && state.pairGate === "cz_tunable") {
        return "Fixed-frequency qubits can't use a tunable-coupler CZ (the CZ gate " +
               "needs a qubit flux line). Pick a flux-tunable architecture, or use " +
               "the fixed-frequency (cross-resonance) chip type.";
      }
      // Pairs declared but the selected 2-qubit gate can't be built on this
      // hardware → the build would silently drop them (no error, no pairs in
      // the generated config). Block with an actionable message instead.
      if (state.spec.qubit_pairs.length) {
        var gate = state.pairGate || "cz_tunable";
        var canEmit = (gate === "cr" && mw) ||
                      (gate === "cz_fixed" && lf) ||
                      (gate === "cz_tunable" && lf);
        if (!canEmit) {
          var n = state.spec.qubit_pairs.length;
          var plural = n === 1 ? "" : "s";
          if (gate === "cr") {
            return "Cross-resonance needs an MW-FEM (add one in step 3) — " +
                   "otherwise your " + n + " qubit pair" + plural + " won't be built.";
          }
          return "CZ gates need an LF-FEM for qubit flux (add one in step 3), or " +
                 "pick Cross-resonance for fixed-frequency qubits — otherwise your " +
                 n + " qubit pair" + plural + " won't be built into the config.";
        }
      }
      // A pair that references a qubit which no longer exists (deleted on the
      // board) would silently vanish downstream — block it.
      var known = {};
      state.spec.qubits.forEach(function (q) { known[q] = true; });
      var dangling = state.spec.qubit_pairs.filter(function (p) {
        return p[0] && p[1] && (!known[p[0]] || !known[p[1]]);
      })[0];
      if (dangling) {
        return "A pair references a qubit that no longer exists (" +
               dangling.join("–") + ") — remove it before continuing.";
      }
      // ID-gate: the topology board lets you leave gaps WHILE editing (place a
      // partial / mid-thought layout), but ids must match the active naming
      // scheme before leaving the step. Offer a one-click renumber (spelling
      // out the remap so the user knows q5→q4 etc.); block if declined.
      // expectedNamesOrNull() is null for regenerate chips (their named ids
      // are kept verbatim — the value-merge matches on them), hand-renamed
      // sets, and the one-shot grid preset — no gate in those cases.
      var qs = state.spec.qubits;
      var expNames = expectedNamesOrNull();
      var hasHoles = !!expNames &&
        qs.some(function (q, i) { return q !== expNames[i]; });
      if (hasHoles) {
        if (window.confirm(renumberPrompt(qs))) {
          renumberContiguous();
        } else {
          return "Qubit ids don't match the naming scheme — use the " +
                 "Renumber button (or Apply names) to fix them.";
        }
      }
      // Partial-placement gate: if the board is in use (SOME qubits placed) then
      // ALL must be placed — otherwise the build hands the unplaced ones quam_builder's
      // default snake-fill grid_location, which can COLLIDE with a placed qubit's
      // cell and draws a topology on Chip Status the user never laid out. Zero placed
      // = pure form flow (uniform defaults, no collision) → allowed.
      var unplaced = unplacedQubits();
      if (unplaced.length && unplaced.length < qs.length) {
        var shown = unplaced.slice(0, 6).join(", ") + (unplaced.length > 6 ? ", …" : "");
        return "Some qubits aren't placed on the board (" + shown + "). Place them all " +
               "on the Chip board, or clear the board to use the default layout.";
      }
      return null;
    },
    7: function () {
      if (!getOutputPath()) return "Choose an output folder.";
      if (state.scriptsEnabled && !getScriptsPath()) {
        return "Choose a folder for the editable Python scripts, or untick " +
               "the export.";
      }
      return null;
    }
  };

  function root() {
    return document.getElementById("generate-root");
  }

  // -- shell -----------------------------------------------------------

  function showMessage(msg, kind) {
    var el = document.getElementById("gen-message");
    if (!el) return;
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.textContent = msg;
    el.className = "gen-message gen-message-" + (kind || "warn");
    el.hidden = false;
  }

  function render() {
    var r = root();
    if (!r) return;

    r.querySelectorAll(".gen-panel").forEach(function (panel) {
      panel.classList.toggle("active", Number(panel.dataset.step) === state.step);
    });

    r.querySelectorAll("#gen-steps li").forEach(function (li) {
      var n = Number(li.dataset.step);
      li.classList.toggle("active", n === state.step);
      li.classList.toggle("done", n < state.step);
    });

    // Keep BOTH nav clusters in lock-step — the bottom .gen-nav and the top
    // header mirror (so the user never has to scroll to Back/Next).
    var nextLabel = state.step === STEP_COUNT ? "Generate" : "Next →";
    ["gen-back", "gen-back-top"].forEach(function (id) {
      var b = document.getElementById(id);
      if (b) b.disabled = state.step === 1;
    });
    ["gen-next", "gen-next-top"].forEach(function (id) {
      var n = document.getElementById(id);
      if (n) n.textContent = nextLabel;
    });
    ["gen-progress", "gen-progress-top"].forEach(function (id) {
      var p = document.getElementById(id);
      if (p) p.textContent = "Step " + state.step + " of " + STEP_COUNT;
    });
    // Step 5 carries its own inline Back/Next on the Auto-allocate row, so the
    // bottom nav hides there; the TOP mirror stays visible on every step.
    var nav = document.querySelector(".gen-nav");
    if (nav) nav.hidden = (state.step === 5);

    // Downstream steps consume the derived spec.lines — rebuild it on entry so
    // edits made in step 4 (qubit count, pairs, 2Q-gate, flux) always propagate
    // forward, even via change paths that didn't re-derive. Respects wiringTouched.
    if (state.step >= 5) deriveLines();
    if (state.step === 4) {
      syncLineTypeToggles();
      syncTopoControls();   // show the Renumber button if we arrived with id holes
      // Re-render the pair list from state: a CZ auto-orientation flip on
      // step 6/8 (czAutoOrient) reorders spec.qubit_pairs while this list
      // isn't visible — without this, revisiting step 4 shows the stale
      // pre-flip Control/Target dropdowns.
      renderPairs();
      // Re-render the always-visible topology board from state (count change,
      // draft restore, CZ auto-flip while away). The WiringGrid guard stays —
      // several selfchecks eval generate.js without wiring-grid.js.
      if (window.WiringGrid) window.WiringGrid.refresh();
    }
    if (state.step === 5) enterWiringStep();
    if (state.step === 6) enterPopulateStep();
    if (state.step === 8) enterReviewStep();

    focusStep(state.step);
  }

  function goToStep(n) {
    // Persist the current step's edits BEFORE leaving it, so navigating away and
    // back — or an unexpected pane swap — never loses work. captureDomFields()
    // first flushes any value typed but not yet committed (webview blur race).
    captureDomFields();
    saveDraft();
    state.step = Math.max(1, Math.min(STEP_COUNT, n));
    showMessage(null);
    render();
  }

  function tryNext() {
    var guard = stepGuards[state.step];
    var err = guard ? guard() : null;
    if (err) {
      showMessage(err, "warn");
      return;
    }
    if (state.step < STEP_COUNT) {
      goToStep(state.step + 1);
    } else {
      runBuild();
    }
  }

  // Step-rail click target. Backward jumps stay free (feature-D: review/fix any
  // earlier step without losing work). FORWARD jumps are free too — EXCEPT they may
  // not carry a topology that would silently corrupt the generated chip (id holes,
  // dangling pairs, partial placement). Those route the user to step 4 with the
  // reason; the visible Renumber button / the Next gate fix it. Non-corrupting
  // requirements (output folder, etc.) are still enforced at Generate, not here —
  // so forward exploration of later steps stays unblocked.
  function jumpToStep(target) {
    if (target > state.step) {
      var topoErr = topologyBlocker();
      if (topoErr) { goToStep(4); showMessage(topoErr, "warn"); return; }
    }
    goToStep(target);
  }

  // -- step 1: environment picker --------------------------------------

  function loadEnvs() {
    var list = document.getElementById("gen-env-list");
    if (!list) return;
    fetch("/generate/envs")
      .then(function (r) { return r.json(); })
      .then(renderEnvList)
      .catch(function () {
        list.innerHTML =
          '<p class="muted">Could not scan conda environments.</p>';
      });
  }

  function renderEnvList(data) {
    var list = document.getElementById("gen-env-list");
    var empty = document.getElementById("gen-env-empty");
    if (!list) return;

    var envs = (data && data.envs) || [];
    list.innerHTML = "";
    if (!envs.length) {
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    var pending = envs.length;
    envs.forEach(function (env) {
      var row = document.createElement("div");
      row.className = "gen-env-row";
      row.dataset.python = env.python;
      row.innerHTML =
        '<span class="gen-env-radio" aria-hidden="true"></span>' +
        '<span class="gen-env-name"></span>' +
        '<span class="gen-env-path"></span>' +
        '<span class="gen-env-status" data-state="checking">checking…</span>';
      row.querySelector(".gen-env-name").textContent = env.name;
      row.querySelector(".gen-env-path").textContent = env.python;
      row.addEventListener("click", function () { selectEnv(env.python); });
      list.appendChild(row);
      probeEnv(env.python, row.querySelector(".gen-env-status"), function () {
        pending -= 1;
        if (pending === 0) checkAnyUsable();
      });
    });

    if (data.selected) {
      applySelection(data.selected);
    } else if (state.env) {
      // Restored draft — re-highlight the env the server didn't report.
      applySelection(state.env);
    }
  }

  function checkAnyUsable() {
    var list = document.getElementById("gen-env-list");
    if (!list || !list.querySelector(".gen-env-row")) return;
    if (!list.querySelector('.gen-env-status[data-state="ok"]')) {
      showMessage(
        "None of these environments has the QM stack (qualang_tools, " +
        "quam_builder, quam). Install it in one, then reload this page.",
        "warn"
      );
    }
  }

  function probeEnv(python, statusEl, done) {
    fetch("/generate/probe?python=" + encodeURIComponent(python))
      .then(function (r) { return r.json(); })
      .then(function (info) {
        if (info && info.usable) {
          var v = info.versions || {};
          statusEl.dataset.state = "ok";
          statusEl.textContent =
            "✓ qualang_tools " + (v.qualang_tools || "?") +
            " · quam_builder " + (v.quam_builder || "?") +
            " · quam " + (v.quam || "?");
        } else if (info && info.missing && info.missing.length) {
          statusEl.dataset.state = "bad";
          statusEl.textContent = "✗ missing: " + info.missing.join(", ");
        } else {
          statusEl.dataset.state = "bad";
          statusEl.textContent = "✗ probe failed";
        }
        if (done) done();
      })
      .catch(function () {
        statusEl.dataset.state = "bad";
        statusEl.textContent = "✗ probe failed";
        if (done) done();
      });
  }

  function applySelection(python) {
    state.env = python;
    var list = document.getElementById("gen-env-list");
    if (!list) return;
    list.querySelectorAll(".gen-env-row").forEach(function (row) {
      row.classList.toggle("selected", row.dataset.python === python);
    });
  }

  function selectEnv(python) {
    fetch("/generate/select-env", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ python: python })
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res && res.ok) {
          applySelection(python);
          showMessage(null);
        } else {
          showMessage((res && res.error) || "Could not select environment.", "error");
        }
      })
      .catch(function () {
        showMessage("Could not select environment.", "error");
      });
  }

  // Custom interpreter (a plain venv / any python.exe, not just conda envs):
  // probe it for the QM stack, then persist via the same select-env path
  // (which now validates the path is a real file).
  function useCustomEnv() {
    var input = document.getElementById("gen-env-custom-path");
    var status = document.getElementById("gen-env-custom-status");
    var python = input && input.value.trim();
    if (!python) { if (status) status.textContent = "Enter an interpreter path."; return; }
    if (status) status.textContent = "checking…";
    fetch("/generate/probe?python=" + encodeURIComponent(python))
      .then(function (r) { return r.json(); })
      .then(function (info) {
        if (info && info.usable) {
          selectEnv(python);
          if (status) status.textContent = "✓ QM stack found — selected.";
        } else {
          var miss = (info && info.missing || []).join(", ");
          if (status) status.textContent = info && info.error
            ? ("probe failed: " + info.error)
            : ("missing: " + (miss || "qualang_tools / quam_builder / quam"));
        }
      })
      .catch(function () { if (status) status.textContent = "probe failed."; });
  }

  // -- step 2: network -------------------------------------------------

  function bindNetworkStep() {
    var host = document.getElementById("gen-net-host");
    var cluster = document.getElementById("gen-net-cluster");
    var port = document.getElementById("gen-net-port");
    if (host) {
      host.addEventListener("input", function () {
        state.spec.network.host = host.value.trim();
      });
    }
    if (cluster) {
      cluster.addEventListener("input", function () {
        state.spec.network.cluster_name = cluster.value.trim();
      });
    }
    if (port) {
      port.addEventListener("input", function () {
        var v = port.value.trim();
        var num = parseInt(v, 10);
        state.spec.network.port = (v === "" || isNaN(num)) ? null : num;
      });
    }
  }

  // -- step 3: chassis & modules ---------------------------------------

  var OPX1000_SLOTS = 8;
  // Roving-tabindex highlight for the chassis slot grid — {con, slot}, or
  // null when there are no OPX1000 chassis. Value identity (not a DOM node)
  // so it survives renderChassis() rebuilding every tile.
  var activeSlot = null;

  function instruments() {
    return state.spec.instruments;
  }

  // Controller indices are positional: OPX1000 chassis are con 1..N, OPX+
  // controllers follow as con N+1.., octaves are index 1..K. syncCons()
  // re-derives them after any add / remove / count change.
  function syncCons() {
    var inst = instruments();
    var con = 1;
    inst.controllers.forEach(function (c) { c.con = con++; });
    inst.opx_plus.forEach(function (o) { o.con = con++; });
    inst.octaves.forEach(function (o, i) { o.index = i + 1; });
  }

  function setChassisCount(n) {
    n = Math.max(0, Math.min(20, isNaN(n) ? 0 : n));
    var ctrls = instruments().controllers;
    if (n < ctrls.length) {
      var losingFems = ctrls.slice(n).some(function (c) { return c.fems.length; });
      if (losingFems && !window.confirm(
          "Reducing the chassis count discards FEM modules on the removed " +
          "chassis. Continue?")) {
        return false;
      }
    }
    while (ctrls.length < n) ctrls.push({ con: 0, fems: [] });
    while (ctrls.length > n) ctrls.pop();
    renderChassis();
    return true;
  }

  function femAt(ctrl, slot) {
    var match = ctrl.fems.filter(function (f) { return f.slot === slot; })[0];
    return match ? match.fem : null;
  }

  function setFem(ctrl, slot, fem) {
    ctrl.fems = ctrl.fems.filter(function (f) { return f.slot !== slot; });
    if (fem) ctrl.fems.push({ slot: slot, fem: fem });
    ctrl.fems.sort(function (a, b) { return a.slot - b.slot; });
  }

  // -- step 3: keyboard navigation helpers -----------------------------
  // The OPX1000 slot tiles are a roving-tabindex grid: exactly one tile is
  // Tab-reachable, arrow keys move focus, `activeSlot` tracks the highlight.

  function allSlotEls() {
    return Array.prototype.slice.call(
      document.querySelectorAll("#gen-chassis-list .gen-slot"));
  }

  function ctrlByCon(con) {
    return instruments().controllers.filter(function (c) {
      return c.con === con;
    })[0] || null;
  }

  function slotElByActive() {
    if (!activeSlot) return null;
    return document.querySelector('#gen-chassis-list .gen-slot[data-con="' +
      activeSlot.con + '"][data-slot="' + activeSlot.slot + '"]');
  }

  // Re-focus the active tile after renderChassis() discards the old DOM.
  function restoreSlotFocus() {
    var el = slotElByActive();
    if (el) el.focus();
  }

  function hideSlotMenu() {
    var menu = document.getElementById("gen-slot-menu");
    if (menu) menu.hidden = true;
  }

  // FEM picker menu for one slot. Opened by click or by Enter on a focused
  // tile; keyboard-operable (Up/Down move, Enter picks, Esc/Tab cancel).
  function openSlotMenu(targetEl, ctrl, slot, fem) {
    var menu = document.getElementById("gen-slot-menu");
    if (!menu) return;
    menu.innerHTML = "";
    menu.setAttribute("role", "menu");

    var options = [
      { label: "MW-FEM", fem: "mw" },
      { label: "LF-FEM", fem: "lf" }
    ];
    if (fem) options.push({ label: "Empty slot", fem: null });

    options.forEach(function (opt) {
      var b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.textContent = opt.label;
      if (opt.fem === fem) b.className = "current";
      b.addEventListener("click", function () {
        setFem(ctrl, slot, opt.fem);
        activeSlot = { con: ctrl.con, slot: slot };
        hideSlotMenu();
        renderChassis({ restoreFocus: true });
      });
      menu.appendChild(b);
    });

    menu.onkeydown = function (ev) {
      var btns = Array.prototype.slice.call(menu.querySelectorAll("button"));
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        var i = btns.indexOf(document.activeElement);
        var d = ev.key === "ArrowDown" ? 1 : -1;
        var next = btns[(i + d + btns.length) % btns.length];
        if (next) next.focus();
      } else if (ev.key === "Escape" || ev.key === "Tab") {
        ev.preventDefault();
        hideSlotMenu();
        restoreSlotFocus();
      }
    };

    var rect = targetEl.getBoundingClientRect();
    menu.style.left = (window.scrollX + rect.left) + "px";
    menu.style.top = (window.scrollY + rect.bottom + 4) + "px";
    menu.hidden = false;
    var first = menu.querySelector("button");
    if (first) first.focus();
  }

  function renderSlot(ctrl, slot) {
    var fem = femAt(ctrl, slot);
    var el = document.createElement("div");
    el.className = "gen-slot" + (fem ? " filled fem-" + fem : "");
    el.dataset.con = ctrl.con;
    el.dataset.slot = slot;
    el.setAttribute("role", "button");
    el.setAttribute("aria-haspopup", "menu");
    el.setAttribute("aria-label", "con" + ctrl.con + " slot " + slot + ", " +
      (fem ? fem.toUpperCase() + "-FEM" : "empty"));
    // Roving tabindex: only the active tile is Tab-reachable.
    el.tabIndex = (activeSlot && activeSlot.con === ctrl.con &&
                   activeSlot.slot === slot) ? 0 : -1;
    el.innerHTML =
      '<span class="gen-slot-num">' + slot + '</span>' +
      '<span class="gen-slot-fem"></span>';
    el.querySelector(".gen-slot-fem").textContent =
      fem ? fem.toUpperCase() + "-FEM" : "+";
    el.addEventListener("click", function (ev) {
      ev.stopPropagation();
      openSlotMenu(el, ctrl, slot, fem);
    });
    // Any focus (mouse, Tab, arrow nav) syncs activeSlot and rolls the
    // roving tabindex so Tab always returns to the last-focused tile.
    el.addEventListener("focus", function () {
      activeSlot = { con: ctrl.con, slot: slot };
      allSlotEls().forEach(function (s) {
        s.tabIndex = (s === el) ? 0 : -1;
      });
    });
    return el;
  }

  function renderChassisHead(label, onRemove) {
    var head = document.createElement("div");
    head.className = "gen-chassis-head";
    var title = document.createElement("strong");
    title.textContent = label;
    head.appendChild(title);
    if (onRemove) {
      var del = document.createElement("button");
      del.type = "button";
      del.className = "gen-chassis-del";
      del.textContent = "×";
      del.title = "Remove";
      del.addEventListener("click", onRemove);
      head.appendChild(del);
    }
    return head;
  }

  function renderOpx1000(ctrl) {
    var box = document.createElement("div");
    box.className = "gen-chassis";
    // OPX1000 chassis count is managed by the count field, not per-box.
    box.appendChild(renderChassisHead("con" + ctrl.con + " · OPX1000", null));
    var slots = document.createElement("div");
    slots.className = "gen-slots";
    for (var s = 1; s <= OPX1000_SLOTS; s++) {
      slots.appendChild(renderSlot(ctrl, s));
    }
    box.appendChild(slots);
    return box;
  }

  function renderSimpleBox(label, onRemove) {
    var box = document.createElement("div");
    box.className = "gen-chassis gen-chassis-simple";
    box.appendChild(renderChassisHead(label, onRemove));
    return box;
  }

  function renderChassis(opts) {
    var list = document.getElementById("gen-chassis-list");
    var empty = document.getElementById("gen-chassis-empty");
    if (!list) return;
    syncCons();
    var inst = instruments();
    if (empty) {
      empty.hidden = !!(inst.controllers.length || inst.opx_plus.length || inst.octaves.length);
    }
    // Keep activeSlot pointing at a real OPX1000 slot before (re)rendering,
    // so renderSlot() can hand the roving tabindex 0 to the right tile.
    if (!inst.controllers.length) {
      activeSlot = null;
    } else if (!activeSlot || !ctrlByCon(activeSlot.con)) {
      activeSlot = { con: inst.controllers[0].con, slot: 1 };
    }
    list.innerHTML = "";
    inst.controllers.forEach(function (ctrl) {
      list.appendChild(renderOpx1000(ctrl));
    });
    inst.opx_plus.forEach(function (opx) {
      list.appendChild(renderSimpleBox("con" + opx.con + " · OPX+", function () {
        inst.opx_plus = inst.opx_plus.filter(function (o) { return o !== opx; });
        renderChassis();
      }));
    });
    inst.octaves.forEach(function (oct) {
      list.appendChild(renderSimpleBox("Octave " + oct.index, function () {
        inst.octaves = inst.octaves.filter(function (o) { return o !== oct; });
        renderChassis();
      }));
    });
    if (opts && opts.restoreFocus) restoreSlotFocus();
  }

  // Left/Right move the highlight across all OPX1000 slots; Up/Down cycle the
  // focused slot's FEM (empty → MW → LF), as do the M/L/Del keys; Enter opens
  // the picker menu. One delegated listener on #gen-chassis-list.
  var FEM_CYCLE = [null, "mw", "lf"];

  function onChassisKeydown(ev) {
    var tile = ev.target;
    if (!tile || !tile.classList || !tile.classList.contains("gen-slot")) {
      return;
    }
    var key = ev.key;
    var ctrl = ctrlByCon(parseInt(tile.dataset.con, 10));
    var slot = parseInt(tile.dataset.slot, 10);
    if (key === "ArrowRight" || key === "ArrowLeft") {
      ev.preventDefault();
      var els = allSlotEls();
      var idx = els.indexOf(tile);
      if (idx < 0) return;
      var next = els[Math.max(0, Math.min(els.length - 1,
                              idx + (key === "ArrowRight" ? 1 : -1)))];
      if (next) next.focus();   // focus handler syncs activeSlot + tabindex
    } else if (key === "ArrowDown" || key === "ArrowUp") {
      // Cycle the FEM type on the focused slot — Down advances, Up reverses.
      ev.preventDefault();
      if (ctrl) {
        var cur = FEM_CYCLE.indexOf(femAt(ctrl, slot));
        var step = key === "ArrowDown" ? 1 : -1;
        setFem(ctrl, slot,
               FEM_CYCLE[(cur + step + FEM_CYCLE.length) % FEM_CYCLE.length]);
        renderChassis({ restoreFocus: true });
      }
    } else if (key === "Enter" || key === " " || key === "Spacebar") {
      ev.preventDefault();
      if (ctrl) openSlotMenu(tile, ctrl, slot, femAt(ctrl, slot));
    } else if (key === "m" || key === "M" || key === "l" || key === "L" ||
               key === "Delete" || key === "Backspace") {
      ev.preventDefault();
      if (ctrl) {
        var f = (key === "m" || key === "M") ? "mw"
              : (key === "l" || key === "L") ? "lf" : null;
        setFem(ctrl, slot, f);
        renderChassis({ restoreFocus: true });
      }
    }
  }

  function bindChassisStep() {
    var countInput = document.getElementById("gen-chassis-count");
    var addOpxPlus = document.getElementById("gen-add-opxplus");
    var addOctave = document.getElementById("gen-add-octave");
    var list = document.getElementById("gen-chassis-list");
    if (list) list.addEventListener("keydown", onChassisKeydown);

    if (countInput) {
      countInput.addEventListener("change", function () {
        setChassisCount(parseInt(countInput.value, 10));
        countInput.value = instruments().controllers.length;
      });
    }
    if (addOpxPlus) {
      addOpxPlus.addEventListener("click", function () {
        instruments().opx_plus.push({ con: 0 });
        renderChassis();
      });
    }
    if (addOctave) {
      addOctave.addEventListener("click", function () {
        instruments().octaves.push({ index: 0 });
        renderChassis();
      });
    }

    // Start with 5 OPX1000 chassis the first time the wizard opens.
    if (!instruments().controllers.length) {
      setChassisCount(5);
    }
    if (countInput) countInput.value = instruments().controllers.length;

    document.addEventListener("click", hideSlotMenu);
    renderChassis();
  }

  // -- step 4: qubits, pairs & TWPAs -----------------------------------

  function defaultChainPairs(qubits) {
    // Nearest-neighbour chain: q1-q2, q2-q3, ... q(n-1)-qn.
    var pairs = [];
    for (var i = 0; i < qubits.length - 1; i++) {
      pairs.push([qubits[i], qubits[i + 1]]);
    }
    return pairs;
  }

  // Atomic lock-step rename of the qubit set against a single old→new map
  // (extracted from the old renumberContiguous — the highest-blast-radius
  // mutator; a partial rewrite would orphan per-qubit/per-pair data, so it is
  // ONE pass). Rewrites qubits + qubit_pairs + every populate bucket +
  // populate.pairs keys + TWPA qubit lists, preserving order; lines re-derive
  // and the (old-id-keyed) allocation is dropped so the user re-allocates in
  // step 5.
  function applyQubitIdMap(map) {
    var sp = state.spec;
    sp.qubits = (sp.qubits || []).map(function (q) { return map[q] || q; });
    sp.qubit_pairs = (sp.qubit_pairs || []).map(function (p) {
      return [map[p[0]] || p[0], map[p[1]] || p[1]];
    });
    var pop = sp.populate || {};
    ["qubit", "resonator", "flux", "pulses"].forEach(function (grp) {
      if (!pop[grp]) return;
      var nb = {};
      Object.keys(pop[grp]).forEach(function (qid) { nb[map[qid] || qid] = pop[grp][qid]; });
      pop[grp] = nb;
    });
    if (pop.pairs) {
      var npairs = {};
      Object.keys(pop.pairs).forEach(function (key) {
        var i = key.indexOf("-");
        if (i < 0) { npairs[key] = pop.pairs[key]; return; }
        var c = key.slice(0, i), t = key.slice(i + 1);
        npairs[(map[c] || c) + "-" + (map[t] || t)] = pop.pairs[key];
      });
      pop.pairs = npairs;
    }
    // TWPA qubit lists carry qubit ids too (the old renumber missed these).
    (sp.twpas || []).forEach(function (tw) {
      tw.qubits = (tw.qubits || []).map(function (q) { return map[q] || q; });
    });
    state.allocation = null;        // old element-id keys are stale → re-allocate
    state.pairsTouched = true;
    deriveLines();
    renderQubitsStep();             // includes the (unconditional) board repaint
    showMessage(null);              // clear any stale gap warning
  }

  // Renumber a hole-y qubit set back onto the active naming scheme (the
  // ID-gate fix). Falls back to contiguous q1…qN when no scheme expectation
  // is active (defensive — the gate only fires while one is).
  function renumberContiguous() {
    var old = (state.spec.qubits || []).slice();
    var target = expectedNamesOrNull() ||
      old.map(function (_q, i) { return "q" + (i + 1); });
    var map = {};
    old.forEach(function (q, i) { map[q] = target[i]; });
    applyQubitIdMap(map);
  }

  // Spell out the renumber so the user sees the identity remap (q5→q4 etc.) and
  // knows their typed values move WITH each qubit — not that they keep the old id.
  function renumberPrompt(qs) {
    var exp = expectedNamesOrNull() ||
      qs.map(function (_q, i) { return "q" + (i + 1); });
    var moves = [];
    for (var i = 0; i < qs.length; i++) {
      if (qs[i] !== exp[i]) moves.push(qs[i] + "→" + exp[i]);
    }
    return "Qubit ids have gaps (" + qs.join(", ") + "). The naming scheme " +
           "expects " + exp[0] + "…" + exp[exp.length - 1] + ". Renumbering " +
           "will remap: " + moves.join(", ") + " — your typed values move " +
           "with each qubit. Renumber now?";
  }

  // -- step 4: qubit naming scheme --------------------------------------
  // Name rule (backed by the pipeline's real constraints): a leading
  // lowercase "q" (quam_builder derives machine.qubits keys as
  // "q" + stripped index — other prefixes silently orphan populate values),
  // then letters/digits/underscore only — a "-" would corrupt pair-id
  // parsing (_parse_pair splits on the first "-") and whitespace breaks DOM
  // ids. Mirrored server-side in config_generator.validate_spec.
  var QUBIT_NAME_RE = /^q[A-Za-z0-9_]+$/;

  function validateQubitName(name, taken) {
    if (!QUBIT_NAME_RE.test(name)) {
      return 'Qubit name "' + name + '" is invalid — names must start with a ' +
        "lowercase 'q' followed by letters/digits/underscore (no '-' or " +
        "spaces; the builder derives element and pair ids from them).";
    }
    if (taken && taken[name]) return 'Duplicate qubit name "' + name + '".';
    return null;
  }

  // 0 → "A", 25 → "Z", 26 → "AA", … (board rows for the grid preset).
  function rowLetter(r) {
    var s = "";
    r = Math.floor(r);
    do {
      s = String.fromCharCode(65 + (r % 26)) + s;
      r = Math.floor(r / 26) - 1;
    } while (r >= 0);
    return s;
  }

  // The names the active scheme produces for n qubits: {names} or {error}.
  // grid: letter = board row (A = row 0 = chip bottom, the QUAM convention),
  // number = column + 1 — deterministic and collision-free (cells are unique).
  function schemeNames(n) {
    var nm = state.naming || {};
    var preset = nm.preset || "one_based";
    var names = [], i;
    if (preset === "zero_based") {
      for (i = 0; i < n; i++) names.push("q" + i);
    } else if (preset === "custom") {
      var prefix = (nm.prefix || "q").trim() || "q";
      var start = parseInt(nm.start, 10);
      if (isNaN(start)) start = 1;
      for (i = 0; i < n; i++) names.push(prefix + (start + i));
    } else if (preset === "grid") {
      var popq = (state.spec.populate && state.spec.populate.qubit) || {};
      var unplaced = state.spec.qubits.filter(function (q) {
        return (popq[q] || {}).grid_location == null;
      });
      if (unplaced.length) {
        return { error: "Grid naming needs every qubit placed on the board (" +
          unplaced.slice(0, 6).join(", ") +
          (unplaced.length > 6 ? ", …" : "") + " unplaced)." };
      }
      names = state.spec.qubits.map(function (q) {
        var gl = String(popq[q].grid_location).split(",");
        return "q" + rowLetter(parseInt(gl[1], 10)) + (parseInt(gl[0], 10) + 1);
      });
    } else {
      for (i = 0; i < n; i++) names.push("q" + (i + 1));   // one_based default
    }
    return { names: names };
  }

  // The standing name expectation, or null when there is none: hand-renamed
  // sets and regenerate chips keep their names; the grid preset is one-shot
  // (board moves must not silently re-rename).
  function expectedNamesOrNull() {
    if (state.namesTouched || state.mode === "regenerate") return null;
    if ((state.naming || {}).preset === "grid") return null;
    var r = schemeNames(state.spec.qubits.length);
    return r.error ? null : r.names;
  }

  function namesConform() {
    var exp = expectedNamesOrNull();
    if (!exp) return true;
    return state.spec.qubits.every(function (q, i) { return q === exp[i]; });
  }

  function hasPopulateValues() {
    var pop = state.spec.populate || {};
    return ["qubit", "resonator", "flux", "pulses", "pairs"].some(function (g) {
      return pop[g] && Object.keys(pop[g]).some(function (k) {
        return Object.keys(pop[g][k] || {}).length > 0;
      });
    });
  }

  // Apply the active scheme to the whole set (the "Apply names" button and
  // the one-shot grid path). Validates the generated names (a custom prefix
  // can produce illegal ones), confirms when typed values exist, then remaps
  // in one pass and re-arms the scheme expectation.
  function applyNamingScheme() {
    var old = state.spec.qubits.slice();
    var r = schemeNames(old.length);
    if (r.error) { showMessage(r.error, "warn"); return; }
    var names = r.names, seen = {};
    for (var i = 0; i < names.length; i++) {
      var err = validateQubitName(names[i], seen);
      if (err) { showMessage("Naming scheme: " + err, "warn"); return; }
      seen[names[i]] = true;
    }
    var map = {}, changed = false;
    old.forEach(function (q, i2) {
      map[q] = names[i2];
      if (q !== names[i2]) changed = true;
    });
    if (changed) {
      if (hasPopulateValues() && !window.confirm(renamePrompt(old, names))) return;
      applyQubitIdMap(map);
    }
    state.namesTouched = false;   // the set now IS the scheme — re-arm it
    syncTopoControls();
    renderNamingUi();
  }

  function renamePrompt(oldNames, newNames) {
    var moves = [];
    for (var i = 0; i < oldNames.length; i++) {
      if (oldNames[i] !== newNames[i]) moves.push(oldNames[i] + "→" + newNames[i]);
    }
    return "Apply the naming scheme? This remaps: " + moves.slice(0, 12).join(", ") +
      (moves.length > 12 ? ", … (" + moves.length + " total)" : "") +
      " — your typed values move with each qubit.";
  }

  // Rename ONE qubit (inline edit). Returns an error string, or null on
  // success. Success detaches the set from the scheme (namesTouched).
  function renameQubit(oldId, newId) {
    var taken = {};
    state.spec.qubits.forEach(function (q) { if (q !== oldId) taken[q] = true; });
    var err = validateQubitName(newId, taken);
    if (err) return err;
    if (oldId !== newId) {
      var map = {};
      map[oldId] = newId;
      applyQubitIdMap(map);
      state.namesTouched = true;
      syncTopoControls();
      renderNamingUi();
    }
    return null;
  }

  // Per-qubit rename inputs (inside the step-4 naming block).
  function renderQubitNameList() {
    var host = document.getElementById("gen-qubit-name-list");
    if (!host) return;
    host.innerHTML = "";
    if (state.mode === "regenerate" || !state.spec.qubits.length) return;
    state.spec.qubits.forEach(function (q) {
      var input = document.createElement("input");
      input.type = "text";
      input.value = q;
      input.className = "gen-qubit-name-in";
      input.title = "Rename " + q + " — typed values and pairs follow the rename";
      input.addEventListener("change", function () {
        var v = input.value.trim();
        if (v === q) return;
        var err = renameQubit(q, v);
        if (err) {
          showMessage(err, "warn");
          input.value = q;               // restore the valid name
        } else {
          showMessage(null);
        }
      });
      host.appendChild(input);
    });
  }

  // Keep the naming block's controls + note in step with state. Module-level
  // (not a bindQubitsStep closure) so renderQubitsStep / hydrateFromSpec can
  // call it.
  function renderNamingUi() {
    var block = document.getElementById("gen-naming");
    if (!block) return;
    block.hidden = state.mode === "regenerate";
    var nm = state.naming || {};
    var sel = document.getElementById("gen-naming-preset");
    if (sel) sel.value = nm.preset || "one_based";
    var custom = (nm.preset || "one_based") === "custom";
    var pf = document.getElementById("gen-naming-prefix-field");
    var sf = document.getElementById("gen-naming-start-field");
    if (pf) pf.hidden = !custom;
    if (sf) sf.hidden = !custom;
    var pin = document.getElementById("gen-naming-prefix");
    if (pin) pin.value = nm.prefix || "q";
    var sin = document.getElementById("gen-naming-start");
    if (sin) sin.value = (nm.start == null ? 1 : nm.start);
    var note = document.getElementById("gen-naming-note");
    if (note) {
      if (nm.preset === "grid") {
        var r = schemeNames(state.spec.qubits.length);
        note.textContent = r.error ? r.error
          : "Letters follow board rows (A = bottom row), numbers the column " +
            "(qA1, qA2, qB1, …). Applied once — re-Apply after board moves.";
      } else if (state.namesTouched) {
        note.textContent = "Names were hand-edited — count changes keep them; " +
          "Apply names re-imposes the scheme.";
      } else {
        note.textContent = "New qubits follow the scheme. Rename any qubit " +
          "below — names must start with 'q' (letters/digits/_ only).";
      }
    }
    var cap = document.getElementById("gen-naming-caption");
    if (cap) {
      var qs = state.spec.qubits;
      cap.textContent = qs.length
        ? "— " + qs.slice(0, 5).join(", ") + (qs.length > 5 ? ", …" : "") : "";
    }
    renderQubitNameList();
  }

  // Qubits the user started placing on the board but left unplaced (no grid_location).
  function unplacedQubits() {
    var popq = (state.spec.populate && state.spec.populate.qubit) || {};
    return state.spec.qubits.filter(function (q) {
      return (popq[q] || {}).grid_location == null;
    });
  }

  // Non-interactive final topology check for runBuild() — defense-in-depth so a
  // hole-y / dangling / partially-placed spec can never reach allocate+build even
  // via a path that skipped the step-4 guard. Returns a message or null.
  function topologyBlocker() {
    var qs = state.spec.qubits;
    // Scheme-conformance: null expectation (regenerate chips — their named
    // ids are kept verbatim; hand-renamed sets; the one-shot grid preset)
    // means any valid names pass. The dangling-pair + unplaced checks below
    // always apply.
    var expNames = expectedNamesOrNull();
    if (expNames && qs.some(function (q, i) { return q !== expNames[i]; })) {
      return "Qubit ids don't match the active naming scheme. Go to the " +
             "Qubits step and Renumber before generating.";
    }
    var known = {};
    qs.forEach(function (q) { known[q] = true; });
    var dangling = (state.spec.qubit_pairs || []).filter(function (p) {
      return p[0] && p[1] && (!known[p[0]] || !known[p[1]]);
    })[0];
    if (dangling) {
      return "A qubit pair references a qubit that no longer exists (" +
             dangling.join("–") + "). Fix it on the Qubits step before generating.";
    }
    var unplaced = unplacedQubits();
    if (unplaced.length && unplaced.length < qs.length) {
      return "Some qubits aren't placed on the board (" + unplaced.slice(0, 6).join(", ") +
             (unplaced.length > 6 ? ", …" : "") + "). Place them all, or clear the board.";
    }
    return null;
  }

  function setQubitCount(n) {
    n = Math.max(0, Math.min(200, isNaN(n) ? 0 : n));
    var qubits;
    var scheme = state.namesTouched || state.mode === "regenerate" ||
      (state.naming || {}).preset === "grid"
      ? null : schemeNames(n);
    if (scheme && !scheme.error) {
      // A scheme expectation is active — regenerate the whole set from it
      // (bit-identical to the historical q1…qN behavior for the default,
      // including hole-closing on count changes).
      qubits = scheme.names;
    } else {
      // Hand-renamed / grid / regenerate sets: keep existing names — append
      // collision-free q<k> fallbacks on grow, truncate from the end on shrink.
      qubits = state.spec.qubits.slice(0, n);
      var used = {};
      qubits.forEach(function (q) { used[q] = true; });
      var k = 1;
      while (qubits.length < n) {
        while (used["q" + k]) k++;
        qubits.push("q" + k);
        used["q" + k] = true;
      }
    }
    state.spec.qubits = qubits;

    var valid = {};
    qubits.forEach(function (q) { valid[q] = true; });
    if (state.pairsTouched) {
      // Hand-edited pairs are kept; only references to removed qubits drop.
      state.spec.qubit_pairs = state.spec.qubit_pairs.filter(function (p) {
        return valid[p[0]] && valid[p[1]];
      });
    } else {
      // Untouched: auto-fill a nearest-neighbour chain tracking the count.
      state.spec.qubit_pairs = defaultChainPairs(qubits);
    }
    state.spec.twpas.forEach(function (tw) {
      tw.qubits = (tw.qubits || []).filter(function (q) { return valid[q]; });
    });
    // Prune populate for removed qubits — otherwise lowering the count orphans a
    // qubit's grid_location / physics in populate, and bumping the count back up
    // silently RESURRECTS a previous design's board placement + values onto the
    // new same-id qubit. (removeQubit already does this for board deletes; the
    // count field is the other shrink path.) Mirror its cleanup here.
    prunePopulate(valid);
    renderQubitsStep();
  }

  // Drop every populate entry (per-qubit buckets + per-pair keys) whose id/endpoint
  // is no longer a live qubit. `valid` = { qid: true } for the surviving qubits.
  function prunePopulate(valid) {
    var pop = state.spec.populate; if (!pop) return;
    ["qubit", "resonator", "flux", "pulses"].forEach(function (grp) {
      if (!pop[grp]) return;
      Object.keys(pop[grp]).forEach(function (qid) { if (!valid[qid]) delete pop[grp][qid]; });
    });
    if (pop.pairs) {
      Object.keys(pop.pairs).forEach(function (key) {
        var seg = key.split("-");
        if (seg.some(function (q) { return !valid[q]; })) delete pop.pairs[key];
      });
    }
  }

  function qubitOptions(selected) {
    return state.spec.qubits.map(function (q) {
      return '<option value="' + q + '"' +
        (q === selected ? " selected" : "") + ">" + q + "</option>";
    }).join("");
  }

  // Live confirmation caption: count + the feedline grouping the mux size
  // implies (default consecutive chunks — the same default deriveLines uses).
  // Replaces the old "N qubits: q1, q2, …" sentence, which triple-duplicated
  // the name chips and the board stones.
  function renderQubitSummary() {
    var el = document.getElementById("gen-qubit-summary");
    if (!el) return;
    var qs = state.spec.qubits;
    if (!qs.length) {
      el.textContent = "No qubits — set the count.";
      return;
    }
    var mux = state.muxSize > 0 ? state.muxSize : 6;
    var groups = [];
    for (var i = 0; i < qs.length; i += mux) {
      var chunk = qs.slice(i, i + mux);
      groups.push(chunk.length === 1 ? chunk[0]
        : chunk[0] + "–" + chunk[chunk.length - 1]);
    }
    var shown = groups.slice(0, 4).join(" · ") + (groups.length > 4 ? " · …" : "");
    el.textContent = qs.length + " qubit" + (qs.length === 1 ? "" : "s") +
      " · " + groups.length + " feedline" + (groups.length === 1 ? "" : "s") +
      ": " + shown;
  }

  function renderPairs() {
    var list = document.getElementById("gen-pair-list");
    if (!list) return;
    list.innerHTML = "";
    if (!state.spec.qubit_pairs.length) {
      list.innerHTML = '<p class="muted">No pairs.</p>';
      return;
    }
    // Gate-aware header. CR: the drive direction is a physical choice made
    // HERE — label the roles and use the directed glyph (the board draws the
    // matching arrowhead). CZ: roles don't exist yet — they're assigned from
    // the RF frequencies typed at Populate — so the columns stay neutral and
    // a one-line caption says when the roles appear.
    var cz = czOrderActive();
    var head = document.createElement("div");
    head.className = "gen-pair-row gen-pair-head";
    head.innerHTML = cz
      ? '<span class="gen-pair-col">Qubit</span>' +
        '<span class="gen-pair-link">↔</span>' +
        '<span class="gen-pair-col">Qubit</span>'
      : '<span class="gen-pair-col">Control</span>' +
        '<span class="gen-pair-link">→</span>' +
        '<span class="gen-pair-col">Target</span>';
    list.appendChild(head);
    if (cz) {
      var hint = document.createElement("p");
      hint.className = "muted gen-pair-cz-hint";
      hint.textContent = "Control/target assigned automatically from qubit " +
        "frequencies at Populate (higher = control).";
      hint.title = "The higher-RF_freq qubit becomes the control, the lower " +
        "the target, re-checked as frequencies are typed in step 6. A " +
        "hand-picked order here is kept (marked manual).";
      list.appendChild(hint);
    }
    var popPairs = (state.spec.populate || {}).pairs || {};
    state.spec.qubit_pairs.forEach(function (pair, idx) {
      var row = document.createElement("div");
      row.className = "gen-pair-row";
      // Surface the (previously invisible) manual orientation pin.
      var isManual = cz && pair[0] && pair[1] &&
        (popPairs[pair[0] + "-" + pair[1]] || {}).cz_order === "manual";
      row.innerHTML =
        '<select class="gen-pair-c"><option value="">—</option>' +
        qubitOptions(pair[0]) + "</select>" +
        '<span class="gen-pair-link">' + (cz ? "↔" : "→") + "</span>" +
        '<select class="gen-pair-t"><option value="">—</option>' +
        qubitOptions(pair[1]) + "</select>" +
        (isManual
          ? '<span class="gen-pair-manual-chip" title="Orientation pinned by ' +
            'hand — frequency auto-assignment skips this pair">manual</span>'
          : "") +
        '<button type="button" class="gen-row-del">×</button>';
      row.querySelector(".gen-pair-c").addEventListener("change", function (e) {
        pair[0] = e.target.value;
        state.pairsTouched = true;
        markPairManual(pair);
        renderPairs();   // repaint (the manual chip may have just appeared)
      });
      row.querySelector(".gen-pair-t").addEventListener("change", function (e) {
        pair[1] = e.target.value;
        state.pairsTouched = true;
        markPairManual(pair);
        renderPairs();
      });
      row.querySelector(".gen-row-del").addEventListener("click", function () {
        state.pairsTouched = true;
        state.spec.qubit_pairs.splice(idx, 1);
        renderPairs();
        syncLineTypeToggles();   // gate selector depends on pair count
        deriveLines();           // keep spec.lines in sync after removal
      });
      list.appendChild(row);
    });
  }

  function nextTwpaId() {
    return "twpa" + String.fromCharCode(65 + state.spec.twpas.length);
  }

  function renderTwpas() {
    var list = document.getElementById("gen-twpa-list");
    if (!list) return;
    list.innerHTML = "";
    if (!state.spec.twpas.length) {
      list.innerHTML = '<p class="muted">No TWPAs.</p>';
      return;
    }
    state.spec.twpas.forEach(function (twpa, idx) {
      var row = document.createElement("div");
      row.className = "gen-twpa-row";
      var input = document.createElement("input");
      input.type = "text";
      input.value = twpa.id || "";
      input.placeholder = "twpaA";
      input.addEventListener("input", function () {
        twpa.id = input.value.trim();
      });
      var del = document.createElement("button");
      del.type = "button";
      del.className = "gen-row-del";
      del.textContent = "×";
      del.addEventListener("click", function () {
        state.spec.twpas.splice(idx, 1);
        renderTwpas();
      });
      row.appendChild(input);
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  // Refresh the line-type checkboxes to match hardware and state.
  // The effective (hardware-constrained) qubitFlux+pairGate map back to a chip
  // architecture so the explicit selector always reflects what will be built.
  function deriveChipArch() {
    if (!state.qubitFlux) return "fixed_frequency";
    return state.pairGate === "cz_fixed" ? "flux_tunable_fixed_coupler"
                                         : "flux_tunable_coupler";
  }

  // Apply an explicit chip-architecture choice: it is the single source of truth
  // for qubitFlux + pairGate. syncLineTypeToggles then enforces hardware limits
  // and reflects the effective result back into the selector.
  function applyChipArch(arch) {
    var map = CHIP_ARCH[arch] || CHIP_ARCH.flux_tunable_coupler;
    state.chipArch = (arch in CHIP_ARCH) ? arch : "flux_tunable_coupler";
    state.qubitFlux = map.qubitFlux;
    state.pairGate = map.pairGate;
    reconcilePopulatePairs();   // drop now-irrelevant CR/CZ pair populate
    syncLineTypeToggles();
    deriveLines();
    // Re-style the board edges to the new gate's line bundle (CR arrow / CZ dashed
    // / coupler dot) — edgeStyle() reads state.pairGate at render.
    if (window.WiringGrid) window.WiringGrid.refresh();
  }

  function syncLineTypeToggles() {
    var mw = hasMwFem() || hasOpxPlus();
    var lf = hasLfFem() || hasOpxPlus();
    var hasPairs = state.spec.qubit_pairs.length > 0;

    var rdIndicator = document.getElementById("gen-rd-indicator");
    var rdRow = document.getElementById("gen-line-resonator-drive");
    var chkQF = document.getElementById("gen-chk-qubit-flux");

    // Resonator + Drive: auto from hardware, shown as a text indicator.
    if (rdIndicator) rdIndicator.textContent = mw ? "\u2713" : "\u2717";
    if (rdRow) rdRow.classList.toggle("gen-line-off", !mw);

    // Qubit flux: only possible with LF-FEM.
    if (chkQF) {
      chkQF.disabled = !lf;
      if (!lf) { chkQF.checked = false; state.qubitFlux = false; }
      else chkQF.checked = state.qubitFlux;
    }

    // 2-qubit gate selector: coupler needs LF-FEM; CR/ZZ need MW-FEM; all need
    // at least one pair. Disable options the hardware can't build and fall back
    // to a valid gate so the wizard never emits a pair line it can't allocate.
    var pairSel = document.getElementById("gen-pair-gate");
    var pairNote = document.getElementById("gen-pair-gate-note");
    if (pairSel) {
      var gate = state.pairGate || "cz_tunable";
      Array.prototype.forEach.call(pairSel.options, function (opt) {
        // CR needs an MW-FEM (drive); both CZ flavours need an LF-FEM (qubit flux).
        opt.disabled = (opt.value === "cr") ? !mw : !lf;
      });
      // Fall back to a gate the current hardware can actually build.
      if ((gate === "cz_fixed" || gate === "cz_tunable") && !lf && mw) gate = "cr";
      if (gate === "cr" && !mw && lf) gate = "cz_tunable";
      state.pairGate = gate;
      pairSel.value = gate;
      pairSel.disabled = !hasPairs;
      // CZ requires qubit flux (z) lines — enable them along with the gate.
      if ((gate === "cz_fixed" || gate === "cz_tunable") && lf) {
        state.qubitFlux = true;
        if (chkQF) chkQF.checked = true;
      }
      state.couplerFlux = (gate === "cz_tunable") && lf && hasPairs;
      if (pairNote) {
        // Confirmation echo: how many pairs the gate applies to (live count).
        var n = state.spec.qubit_pairs.length;
        var echo = " · applies to " + n + " pair" + (n === 1 ? "" : "s");
        if (!hasPairs) pairNote.textContent = "(add qubit pairs below)";
        else if (gate === "cr") pairNote.textContent = mw ? "(CR drive on the control qubit" + echo + ")" : "(needs an MW-FEM)";
        else if (gate === "cz_fixed") pairNote.textContent = lf ? "(CZ flux on the qubit z line" + echo + ")" : "(needs an LF-FEM)";
        else pairNote.textContent = lf ? "(CZ flux on qubit z + coupler" + echo + ")" : "(needs an LF-FEM)";
      }
      // The qubit-flux checkbox + gate select are now DERIVED from the explicit
      // Chip-architecture selector — show them read-only so there's one source of
      // truth and the unrepresentable fixed-Q+tunable-coupler combo is unreachable.
      pairSel.disabled = true;
    }
    if (chkQF) chkQF.disabled = true;

    // Reflect the effective architecture back into the explicit selector, and
    // disable arch options the current hardware can't build.
    state.chipArch = deriveChipArch();
    var archSel = document.getElementById("gen-chip-arch");
    var archNote = document.getElementById("gen-chip-arch-note");
    if (archSel) {
      Array.prototype.forEach.call(archSel.options, function (opt) {
        // flux-tunable architectures need an LF-FEM (qubit flux);
        // fixed-frequency needs an MW-FEM (drive/readout + CR).
        opt.disabled = (opt.value === "fixed_frequency") ? !mw : !lf;
      });
      archSel.value = state.chipArch;
    }
    if (archNote) {
      if (state.chipArch === "fixed_frequency") {
        archNote.textContent = !mw
          ? "Fixed-frequency needs an MW-FEM — add one in step 3."
          : "Fixed-frequency qubits (no z line); 2-qubit gate is cross-resonance.";
      } else if (!lf) {
        archNote.textContent = "No LF-FEM — flux-tunable / coupler not available; "
          + "add an LF-FEM in step 3, or use a fixed-frequency chip.";
      } else if (state.chipArch === "flux_tunable_coupler") {
        archNote.textContent = "Flux-tunable qubits + a tunable coupler; CZ flux on "
          + "qubit z mirrored onto the coupler line.";
      } else {
        archNote.textContent = "Flux-tunable qubits + a fixed coupler; CZ flux on "
          + "the qubit z line only.";
      }
    }
    renderArchRadios();
  }

  // The visible architecture radios — a mirror of the hidden #gen-chip-arch
  // select (the single state anchor every code path + selfcheck drives).
  // Radios show all three options at once (customer feedback: the select
  // cost two clicks and hid the alternatives); a disabled option (hardware
  // can't build it) renders disabled with the reason in its tooltip.
  function renderArchRadios() {
    var host = document.getElementById("gen-arch-radios");
    var sel = document.getElementById("gen-chip-arch");
    if (!host || !sel) return;
    host.innerHTML = "";
    Array.prototype.forEach.call(sel.options, function (opt) {
      var lab = document.createElement("label");
      lab.className = "gen-arch-radio" + (opt.disabled ? " gen-arch-radio-off" : "");
      var r = document.createElement("input");
      r.type = "radio";
      r.name = "gen-chip-arch-radio";
      r.value = opt.value;
      r.checked = opt.value === sel.value;
      r.disabled = opt.disabled;
      if (opt.disabled) {
        lab.title = opt.value === "fixed_frequency"
          ? "Needs an MW-FEM — add one in step 3."
          : "Needs an LF-FEM (qubit flux) — add one in step 3.";
      }
      r.addEventListener("change", function () {
        if (!r.checked) return;
        sel.value = r.value;   // proxy through the anchor → applyChipArch runs
        sel.dispatchEvent(new Event("change", { bubbles: true }));
      });
      lab.appendChild(r);
      lab.appendChild(document.createTextNode(" " + opt.textContent));
      host.appendChild(lab);
    });
    renderCrOptions();
  }

  // Shared mode defaults every pair to the customer's full 4-shape CR set —
  // called by BOTH shared_xy entry points (the port-mode radio and the CSV
  // import) so the flagship customer-CSV flow builds the customer library.
  function seedSharedCrShapes() {
    var pop = state.spec.populate.pairs = state.spec.populate.pairs || {};
    state.spec.qubit_pairs.forEach(function (p) {
      if (!p[0] || !p[1]) return;
      var pid = p[0] + "-" + p[1];
      pop[pid] = pop[pid] || {};
      if (!pop[pid].cr_shapes) pop[pid].cr_shapes = "full";
    });
  }

  // CR sub-options (docs/54): drive-port layout + the Stark-CZ (ZZ) toggle.
  // Rendered under the architecture radios, only for fixed_frequency chips.
  function renderCrOptions() {
    var host = document.getElementById("gen-cr-options");
    if (!host) return;
    var isCr = state.pairGate === "cr";
    host.hidden = !isCr;
    if (!isCr) { host.innerHTML = ""; return; }
    host.innerHTML = "";

    var modeWrap = document.createElement("span");
    modeWrap.className = "gen-cr-portmode";
    modeWrap.appendChild(document.createTextNode("CR drive port: "));
    [["dedicated", "Dedicated MW port per pair",
      "Each CR line gets its own MW-FEM output (the wizard's classic layout)."],
     ["shared_xy", "Shared with control's xy (dual upconverter)",
      "CR/ZZ tones ride the CONTROL qubit's own xy port on upconverter 2 — " +
      "the QM fixed-transmon reference layout. Uses half the MW ports."]]
      .forEach(function (opt) {
        var lab = document.createElement("label");
        lab.className = "gen-arch-radio";
        lab.title = opt[2];
        var r = document.createElement("input");
        r.type = "radio";
        r.name = "gen-cr-portmode-radio";
        r.value = opt[0];
        r.checked = (state.crPortMode || "dedicated") === opt[0];
        r.addEventListener("change", function () {
          if (!r.checked) return;
          state.crPortMode = r.value;
          if (r.value === "shared_xy") seedSharedCrShapes();
          deriveLines();
          saveDraft();
        });
        lab.appendChild(r);
        lab.appendChild(document.createTextNode(" " + opt[1]));
        modeWrap.appendChild(lab);
      });
    host.appendChild(modeWrap);

    var zzLab = document.createElement("label");
    zzLab.className = "gen-arch-radio";
    zzLab.title = "Adds a zz_drive wiring line per pair + the StarkInducedCZGate " +
      "seed (the target's detuned-xy tones need a quam-builder with " +
      "FixedFrequencyZZDriveQuam — the env report card will say).";
    var zz = document.createElement("input");
    zz.type = "checkbox";
    zz.checked = !!state.zzEnabled;
    zz.addEventListener("change", function () {
      state.zzEnabled = zz.checked;
      deriveLines();
      saveDraft();
    });
    zzLab.appendChild(zz);
    zzLab.appendChild(document.createTextNode(" Also wire ZZ (Stark-CZ) drive lines"));
    host.appendChild(zzLab);
  }

  // Apply a parsed port-label CSV payload (/generate/import-port-csv) to the
  // wizard: instruments, qubits (+naming), grid, DIRECTED pairs, feedline
  // groups and port pins — the QM fixed-transmon reference flow's whole
  // chip definition in one file (docs/54). The CSV encodes the shared-port
  // layout, so the architecture flips to fixed_frequency + shared_xy.
  function applyPortCsv(payload) {
    if (!payload || !payload.ok) return false;
    state.spec.instruments = payload.instruments ||
      { controllers: [], opx_plus: [], octaves: [] };
    state.spec.qubits = (payload.qubits || []).slice();
    state.namesTouched = true;      // q0-based ids must survive the scheme gate
    state.spec.qubit_pairs = (payload.qubit_pairs || [])
      .map(function (p) { return p.slice(); });
    state.pairsTouched = true;
    var popq = state.spec.populate.qubit = state.spec.populate.qubit || {};
    Object.keys(payload.grid || {}).forEach(function (q) {
      popq[q] = popq[q] || {};
      popq[q].grid_location = payload.grid[q];
    });
    var countInput = document.getElementById("gen-qubit-count");
    if (countInput) countInput.value = state.spec.qubits.length;

    // architecture: the CSV layout IS the shared-port CR chip. State first
    // (deriveLines below must see it even before any UI listener runs), then
    // the select dispatch so the bound applyChipArch refreshes the step-4 UI.
    state.crPortMode = "shared_xy";
    state.chipArch = "fixed_frequency";
    state.pairGate = "cr";
    state.qubitFlux = false;
    seedSharedCrShapes();      // the customer flow gets the customer library
    var archSel = document.getElementById("gen-chip-arch");
    if (archSel) {
      archSel.value = "fixed_frequency";
      archSel.dispatchEvent(new Event("change", { bubbles: true }));
    }

    // pins + feedline groups onto the (re)derived lines; wiringTouched keeps
    // them across later deriveLines passes (the pinned map).
    state.wiringTouched = true;
    deriveLines();
    state.spec.lines.forEach(function (ln) {
      var pin = (payload.pins || {})[ln.element];
      if (!pin) return;
      if (ln.line === "drive" && pin.drive) ln.channel = pin.drive;
      if (ln.line === "resonator" && pin.resonator) {
        ln.channel = pin.resonator;
        var fl = (payload.feedlines || {})[ln.element];
        if (fl) ln.group = fl;
      }
    });
    saveDraft();
    if (typeof renderQubitsStep === "function") renderQubitsStep();
    return true;
  }

  function renderQubitsStep() {
    renderQubitSummary();
    renderPairs();
    renderTwpas();
    syncLineTypeToggles();
    renderNamingUi();
    // The board is always visible now — repaint it on every qubits-step
    // render (count changes, renames, draft restore, regenerate hydration).
    // Rendering into a display:none panel is safe: WiringGrid.render() is a
    // pure innerHTML build with no layout reads. Keep the guard — selfchecks
    // eval generate.js without wiring-grid.js.
    if (window.WiringGrid) {
      window.WiringGrid.refresh();
      // Mirror the (count-derived) zone into the Grid cols×rows inputs — the
      // old sync only ran via the board's own onChange.
      var z = window.WiringGrid.zone();
      var zc = document.getElementById("gen-topo-cols");
      var zr = document.getElementById("gen-topo-rows");
      if (zc) zc.value = z.cols;
      if (zr) zr.value = z.rows;
    }
  }

  function bindQubitsStep() {
    var countInput = document.getElementById("gen-qubit-count");
    var muxInput = document.getElementById("gen-mux-size");
    var addPair = document.getElementById("gen-add-pair");
    var addTwpa = document.getElementById("gen-add-twpa");

    // Port-label CSV import (docs/54): file picker → text → server parse →
    // applyPortCsv. Confirm before clobbering a non-empty chip definition.
    var csvBtn = document.getElementById("gen-csv-import-btn");
    var csvFile = document.getElementById("gen-csv-file");
    if (csvBtn && csvFile) {
      csvBtn.addEventListener("click", function () { csvFile.click(); });
      csvFile.addEventListener("change", function () {
        var f = csvFile.files && csvFile.files[0];
        csvFile.value = "";
        if (!f) return;
        if (state.spec.qubits.length &&
            !window.confirm("Importing the CSV replaces the current qubits, "
                            + "pairs, instruments and port pins. Continue?")) {
          return;
        }
        var reader = new FileReader();
        reader.onload = function () {
          fetch("/generate/import-port-csv", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: String(reader.result || "") })
          }).then(function (r) { return r.json(); }).then(function (payload) {
            if (!payload.ok) {
              window.alert("CSV import failed:\n"
                           + (payload.errors || ["unknown error"]).join("\n"));
              return;
            }
            applyPortCsv(payload);
            (payload.warnings || []).forEach(function (m) {
              console.warn("port CSV:", m);
            });
            if (payload.warnings && payload.warnings.length) {
              window.alert("Imported with warnings:\n" + payload.warnings.join("\n"));
            }
          }).catch(function (e) {
            window.alert("CSV import failed: " + e);
          });
        };
        reader.readAsText(f);
      });
    }

    if (countInput) {
      countInput.addEventListener("change", function () {
        setQubitCount(parseInt(countInput.value, 10));
        countInput.value = state.spec.qubits.length;
      });
    }
    if (muxInput) {
      // Mirror the DOM-only mux field into state so it persists in a draft.
      muxInput.addEventListener("input", function () {
        var m = parseInt(muxInput.value, 10);
        if (!isNaN(m)) {
          state.muxSize = clampMux(m);
          if (state.muxSize !== m) muxInput.value = state.muxSize;
          renderQubitSummary();   // live feedline-grouping confirmation
        }
      });
    }
    if (addPair) {
      addPair.addEventListener("click", function () {
        state.pairsTouched = true;
        var qs = state.spec.qubits;
        state.spec.qubit_pairs.push(
          qs.length >= 2 ? [qs[0], qs[1]] : ["", ""]
        );
        renderPairs();
        syncLineTypeToggles();   // gate selector depends on pair count
        deriveLines();           // keep spec.lines in sync with the new pair
      });
    }
    if (addTwpa) {
      addTwpa.addEventListener("click", function () {
        state.spec.twpas.push({
          id: nextTwpaId(),
          qubits: state.spec.qubits.slice()
        });
        renderTwpas();
      });
    }
    // (The old gen-chk-qubit-flux / gen-pair-gate change listeners were dead
    // code — syncLineTypeToggles force-disables both controls; the line
    // summary is a read-only confirmation derived from the architecture.)
    // Explicit chip-architecture selector — the primary control (drives qubitFlux
    // + pairGate). The line summary below it is a read-only reflection.
    var archSel = document.getElementById("gen-chip-arch");
    if (archSel) {
      archSel.value = state.chipArch;
      archSel.addEventListener("change", function () { applyChipArch(archSel.value); });
    }

    // Qubit naming scheme controls (renderNamingUi paints them from state).
    var nmPreset = document.getElementById("gen-naming-preset");
    var nmPrefix = document.getElementById("gen-naming-prefix");
    var nmStart = document.getElementById("gen-naming-start");
    var nmApply = document.getElementById("gen-naming-apply");
    if (nmPreset) {
      nmPreset.addEventListener("change", function () {
        state.naming.preset = nmPreset.value;
        renderNamingUi();
      });
    }
    if (nmPrefix) {
      nmPrefix.addEventListener("input", function () {
        state.naming.prefix = nmPrefix.value.trim() || "q";
      });
    }
    if (nmStart) {
      nmStart.addEventListener("input", function () {
        var v = parseInt(nmStart.value, 10);
        if (!isNaN(v)) state.naming.start = v;
      });
    }
    if (nmApply) nmApply.addEventListener("click", applyNamingScheme);

    bindTopoBoard();
    renderQubitsStep();
  }

  // -- step 4: chip-topology board (wiring-grid.js) --------------------------
  // The board mutates state.spec.{qubits, qubit_pairs, populate.qubit[*].
  // grid_location} only; on every board change we re-sync the dropdown pair list,
  // the gate selector, the derived lines, and the zone inputs (the board NEVER
  // touches allocation). Idempotent — safe to call on every step-4 (re)bind.
  // Show the one-click Renumber button only while qubit ids have gaps, so the user
  // can close holes without having to bump into the on-Next gate.
  function syncTopoControls() {
    var btn = document.getElementById("gen-topo-renumber");
    if (!btn) return;
    btn.hidden = namesConform();
  }

  function bindTopoBoard() {
    if (!window.WiringGrid) return;
    var cols = document.getElementById("gen-topo-cols");
    var rows = document.getElementById("gen-topo-rows");
    function syncZoneInputs() {
      var z = window.WiringGrid.zone();
      if (cols) cols.value = z.cols;
      if (rows) rows.value = z.rows;
    }
    window.WiringGrid.setOnChange(function (kind) {
      // Placement / drag change only grid_location — NOT the qubit set or pairs —
      // so they must NOT trigger the expensive pair-dropdown rebuild + line
      // re-derivation. Skipping them is what keeps a 50-qubit hand-placement fast
      // (renderPairs is O(pairs × qubits); firing it per placement was O(N²)).
      if (kind === "place" || kind === "move") return;

      // edge / delete / preset / full — the pair list and/or qubit set changed.
      // A board delete shrinks spec.qubits; keep the count field in lock-step so
      // captureDomFields() (which re-fires `change` when the input disagrees with
      // spec.qubits.length) doesn't "repair" the count and silently re-create the
      // deleted qubit. We set the value only — firing change here would regenerate
      // a contiguous q1…qN and erase the hole the ID-gate is meant to catch.
      var qc = document.getElementById("gen-qubit-count");
      if (qc) qc.value = String(state.spec.qubits.length);
      renderQubitSummary();    // count chip below the board reflects the delete
      renderPairs();           // keep the dropdown pair list in sync
      syncLineTypeToggles();   // gate selector depends on pair count
      deriveLines();           // keep spec.lines in sync (respects wiringTouched)
      syncZoneInputs();
      syncTopoControls();      // show/hide the Renumber button as holes appear/clear
    });
    // The board section is always visible (no <details> gate any more) —
    // rendering happens on step entry / renderQubitsStep; only the button
    // bindings live here (once-guarded on the board wrapper).
    var topo = document.getElementById("gen-topo");
    if (topo && !topo.dataset.bound) {
      topo.dataset.bound = "1";
      // Architecture presets (Chain / Ring / Star / Grid-NN) — auto-place +
      // connect. They live on the LAYOUT band's control row (outside
      // #gen-topo), so query the document; the ids/attributes are unique.
      document.querySelectorAll("[data-topo-preset]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          window.WiringGrid.preset(btn.getAttribute("data-topo-preset"));
        });
      });
      // One-click Renumber (visible only while ids have gaps).
      var renumberBtn = document.getElementById("gen-topo-renumber");
      if (renumberBtn) {
        renumberBtn.addEventListener("click", function () {
          renumberContiguous();
          syncTopoControls();
        });
      }
    }
    function applyZone() {
      var c = parseInt(cols && cols.value, 10), r = parseInt(rows && rows.value, 10);
      if (c > 0 && r > 0) { window.WiringGrid.setZone(c, r); window.WiringGrid.refresh(); }
    }
    if (cols && !cols.dataset.bound) { cols.dataset.bound = "1"; cols.addEventListener("change", applyZone); }
    if (rows && !rows.dataset.bound) { rows.dataset.bound = "1"; rows.addEventListener("change", applyZone); }
    syncZoneInputs();
    syncTopoControls();
  }

  // -- step 5: wiring --------------------------------------------------

  // spec line type -> short code used in the allocation result
  var ALLOC_KEY = { resonator: "rr", drive: "xy", flux: "z", coupler: "c",
                    cross_resonance: "cr", zz_drive: "zz" };

  function deriveLines() {
    // Rebuild lines from the current qubits/pairs, keeping any existing
    // channel pins keyed by (element, line type).
    var pinned = {};
    var groupOf = {};
    state.spec.lines.forEach(function (ln) {
      if (ln.channel) pinned[ln.element + "|" + ln.line] = ln.channel;
      if (ln.line === "resonator" && ln.group) groupOf[ln.element] = ln.group;
    });

    // Which line types are enabled — derived from hardware (Step 3) + user
    // toggles (Step 4). MW-FEM (or OPX+) → resonator + drive;
    // LF-FEM → flux/coupler are *possible*, but each needs its toggle on.
    var mw = hasMwFem() || hasOpxPlus();
    var lf = hasLfFem() || hasOpxPlus();
    var wantResonator = mw;
    var wantDrive     = mw;
    // 2-qubit gate per pair, from the step-4 selector:
    //   cr          -> a cross_resonance line on the control qubit's MW drive.
    //   cz_tunable  -> a coupler flux line (+ qubit flux); build_quam makes the pair.
    //   cz_fixed    -> NO pair line (+ qubit flux); run_build creates the pair.
    // Both CZ flavours REQUIRE qubit flux (z) lines, so force them on.
    var pairGate      = state.pairGate || "cz_tunable";
    var cz            = (pairGate === "cz_fixed" || pairGate === "cz_tunable");
    var wantFlux      = lf && (state.qubitFlux || cz);
    var wantCR        = mw && pairGate === "cr";
    var wantCoupler   = lf && pairGate === "cz_tunable";
    // Keep the derived mirror in sync for the populate/review code that reads it,
    // and stamp the gate onto the spec so run_build knows how to finalize pairs.
    state.couplerFlux = wantCoupler;
    state.spec.pair_gate = pairGate;

    // Readout is multiplexed: qubits in a feedline share one RF in/out pair.
    // `group` collapses them into a shared resonator line (see run_build.py).
    var muxEl = document.getElementById("gen-mux-size");
    var muxSize = clampMux(muxEl ? muxEl.value : 6);
    var lines = [];
    state.spec.qubits.forEach(function (q, idx) {
      if (wantResonator) {
        // Once the wiring is drag-edited, keep the user's feedline grouping.
        var fnum = Math.floor(idx / muxSize) + 1;
        var group = (state.wiringTouched && groupOf[q]) ? groupOf[q] : "feedline" + fnum;
        // LO-safe auto-pairing: a MW-FEM has 5 LOs, each shared by a port pair
        // (Out1+In1, Out2+Out3, Out4+Out5, Out6+Out7, Out8+In2). Alternating
        // feedlines Out8+In1 / Out1+In2 confines readout to LO1+LO5, leaving
        // Out2-7 (LO2/3/4) free for drives. con/slot left to the allocator.
        var loPair = (fnum % 2 === 1) ? { out_port: 8, in_port: 1 }
                                      : { out_port: 1, in_port: 2 };
        var rch = (state.wiringTouched && pinned[q + "|resonator"])
          ? pinned[q + "|resonator"]
          : { kind: "mw_fem", out_port: loPair.out_port, in_port: loPair.in_port };
        lines.push({ element: q, line: "resonator", group: group, channel: rch });
      }
      if (wantDrive) {
        lines.push({ element: q, line: "drive", channel: pinned[q + "|drive"] || null });
      }
      if (wantFlux) {
        lines.push({ element: q, line: "flux", channel: pinned[q + "|flux"] || null });
      }
    });
    // cz_fixed emits no pair wiring line (run_build creates the pair); cr and
    // cz_tunable emit their line so build_quam wires the pair. A CR chip with
    // the ZZ toggle on additionally gets a zz_drive line per pair (Stark-CZ).
    var pairLine = wantCR ? "cross_resonance"
                 : wantCoupler ? "coupler" : null;
    if (pairLine) {
      state.spec.qubit_pairs.forEach(function (p) {
        if (p[0] && p[1]) {
          var el = p[0] + "-" + p[1];
          lines.push({ element: el, line: pairLine, channel: pinned[el + "|" + pairLine] || null });
          if (wantCR && state.zzEnabled) {
            lines.push({ element: el, line: "zz_drive",
                         channel: pinned[el + "|zz_drive"] || null });
          }
        }
      });
    }
    // TWPA lines must survive every re-derive: this rebuild used to emit only
    // qubit/pair lines, silently WIPING the reconstructed twpa_pump pins on the
    // first count/rename/gate edit — a re-generated chip then built without its
    // TWPAs (review-r6 report). One pump line per TWPA (MW hardware only);
    // isolation only where a pin exists (reconstruct emits it when the source
    // wiring had an isolation port).
    if (mw) {
      (state.spec.twpas || []).forEach(function (tw) {
        var tid = (tw && typeof tw === "object") ? tw.id : tw;
        if (!tid) return;
        lines.push({ element: tid, line: "twpa_pump",
                     channel: pinned[tid + "|twpa_pump"] || null });
        if (pinned[tid + "|twpa_isolation"]) {
          lines.push({ element: tid, line: "twpa_isolation",
                       channel: pinned[tid + "|twpa_isolation"] });
        }
      });
    }
    state.spec.lines = lines;
    // CR drive port mode (docs/54): 'shared_xy' = the customer's dual-
    // upconverter layout (CR/ZZ ride the control's xy port, LO 2).
    if (wantCR) {
      state.spec.cr_port_mode = state.crPortMode || "dedicated";
    } else {
      delete state.spec.cr_port_mode;
    }
  }

  function channelToPin(ch) {
    if (!ch) return "";
    var slot = ch.slot != null ? ch.slot : ch.out_slot;
    var port = ch.out_port != null ? ch.out_port : ch.port;
    return [ch.con, slot, port].join("/");
  }

  function pinToChannel(str, lineType) {
    var parts = str.split("/").map(function (s) { return parseInt(s.trim(), 10); });
    if (parts.length !== 3 || parts.some(isNaN)) return null;
    var con = parts[0], slot = parts[1], port = parts[2];
    if (lineType === "resonator") {
      return { kind: "mw_fem", con: con, slot: slot, in_port: port, out_port: port };
    }
    if (lineType === "drive" || lineType === "cross_resonance" || lineType === "zz_drive") {
      // CR/ZZ are MW drive tones — the old lf_fem fallthrough mis-pinned a
      // hand-entered CR port onto LF hardware (allocation failure).
      return { kind: "mw_fem", con: con, slot: slot, out_port: port };
    }
    return { kind: "lf_fem", con: con, out_slot: slot, out_port: port };
  }

  function allocText(element, lineType) {
    var alloc = state.allocation;
    if (!alloc || !alloc[element]) return "—";
    var chans = alloc[element][ALLOC_KEY[lineType]];
    if (!chans || !chans.length) return "—";
    return chans.map(function (c) {
      return c.instrument_id + " con" + c.con +
        (c.slot != null ? " s" + c.slot : "") + " p" + c.port +
        (c.io_type === "input" ? " (in)" : "");
    }).join(", ");
  }

  function renderWiringTable() {
    var host = document.getElementById("gen-wiring-table");
    if (!host) return;
    if (!state.spec.lines.length) {
      host.innerHTML = '<p class="muted">Add qubits in step 4 first.</p>';
      return;
    }
    var body = state.spec.lines.map(function (ln, idx) {
      return '<tr data-idx="' + idx + '"' + (ln.channel ? ' class="pinned"' : "") + ">" +
        "<td>" + ln.element + "</td>" +
        "<td>" + ln.line +
        (ln.group ? ' <span class="muted">· ' + ln.group + "</span>" : "") + "</td>" +
        '<td class="gen-wiring-alloc">' + allocText(ln.element, ln.line) + "</td>" +
        '<td><input type="text" class="gen-wiring-pin" placeholder="auto" value="' +
        channelToPin(ln.channel) + '"></td></tr>';
    }).join("");
    host.innerHTML =
      '<table class="gen-wiring" id="gen-wiring-tbl"><thead><tr>' +
      "<th>Element</th><th>Line</th><th>Auto-allocated</th>" +
      '<th>Pin <span class="muted">con/slot/port</span></th>' +
      "</tr></thead><tbody>" + body + "</tbody></table>";

    host.querySelectorAll(".gen-wiring-pin").forEach(function (input) {
      // pins are "con/slot/port" strings, not numbers — auto-grow only (no comma).
      window.NumberInput.fit(input);
      input.addEventListener("input", function () { window.NumberInput.fit(input); });
      input.addEventListener("change", function () {
        var tr = input.closest("tr");
        var ln = state.spec.lines[Number(tr.dataset.idx)];
        var v = input.value.trim();
        ln.channel = v ? pinToChannel(v, ln.line) : null;
        tr.classList.toggle("pinned", !!ln.channel);
      });
    });
    if (window.armPlainResize) window.armPlainResize("gen-wiring-tbl", "quam_gen_wiring_cols");
  }

  // -- step 5: wiring diagram — reuses the Instrument Wiring renderer ----

  // Regroup the element-centric allocation into the controller-centric shape
  // window.renderInstrumentWiring (app.js) expects — the same data shape
  // query.py:get_instrument_wiring() produces for the /instrument page.
  function buildInstrumentData(allocation) {
    var ROLE = { rr: "rr", xy: "xy", z: "z", c: "coupler", cr: "cr", zz: "zz" };
    var controllers = {};
    Object.keys(allocation || {}).forEach(function (element) {
      Object.keys(allocation[element] || {}).forEach(function (lineType) {
        (allocation[element][lineType] || []).forEach(function (ch) {
          var isInput = ch.io_type === "input";
          var role = ROLE[lineType] || lineType;
          if (role === "rr" && isInput) role = "rr_in";
          var ctrl = controllers[ch.con] || (controllers[ch.con] = {});
          var fem = ctrl[ch.slot] || (ctrl[ch.slot] = {
            type: ch.instrument_id || "mw-fem",
            output_ports: {}, input_ports: {}
          });
          var bucket = isInput ? fem.input_ports : fem.output_ports;
          (bucket[ch.port] || (bucket[ch.port] = [])).push({
            role: role, element: element, label: element + "." + role,
            port_type: (ch.instrument_id || "") + (isInput ? "-input" : "-output")
          });
        });
      });
    });
    // Show every FEM configured in step 3, even with nothing allocated to it,
    // so its empty ports stay visible as drop targets.
    ((state.spec.instruments || {}).controllers || []).forEach(function (ctrl) {
      (ctrl.fems || []).forEach(function (fem) {
        var c = controllers[ctrl.con] || (controllers[ctrl.con] = {});
        if (!c[fem.slot]) {
          c[fem.slot] = { type: fem.fem === "mw" ? "mw-fem" : "lf-fem",
                          output_ports: {}, input_ports: {} };
        }
      });
    });
    var result = {};
    Object.keys(controllers).forEach(function (con) {
      var maxOut = 0;
      Object.keys(controllers[con]).forEach(function (fem) {
        Object.keys(controllers[con][fem].output_ports).forEach(function (p) {
          maxOut = Math.max(maxOut, parseInt(p, 10));
        });
      });
      // 8 rows always — every FEM's full port range stays droppable.
      result[con] = { fems: controllers[con], max_output_port: Math.max(8, maxOut) };
    });
    return { controllers: result };
  }

  // Docked monitor panel (step 5) — shows hovered-port info and live drag
  // state above the diagram, replacing the cursor-following port popup.
  var _monitorState = { hover: null, drag: null };

  function renderMonitor() {
    var host = document.getElementById("gen-wiring-monitor");
    if (!host) return;
    host.innerHTML = "";
    host.className = "gen-wiring-monitor";
    var d = _monitorState.drag, h = _monitorState.hover;
    function span(cls, text) {
      var s = document.createElement("span");
      s.className = cls;
      s.textContent = text;
      return s;
    }
    if (d) {
      if (d.hoverPort) {
        host.classList.add(d.valid ? "gen-monitor-ok" : "gen-monitor-bad");
      }
      host.appendChild(span("gen-monitor-tag", "Dragging"));
      host.appendChild(span("gen-monitor-src",
        (d.whole ? "feedline" : (d.element || "?")) + " · " + d.role));
      host.appendChild(span("gen-monitor-arrow", "→"));
      if (d.hoverPort) {
        var p = d.hoverPort;
        host.appendChild(span("gen-monitor-tgt", "con" + p.con + " slot" +
          p.slot + " port" + p.port + " " + p.io));
        host.appendChild(span("gen-monitor-badge",
          d.valid ? "✓ valid drop" : "✗ invalid drop"));
      } else {
        host.appendChild(span("gen-monitor-tgt", "drag onto a port…"));
      }
      return;
    }
    if (h) {
      host.appendChild(span("gen-monitor-label", h.label || h.element || ""));
      var role = h.role || "";
      host.appendChild(span("role-badge " + role, role.toUpperCase()));
      var bits = [];
      if (h.element != null) bits.push("element " + h.element);
      if (h.port_type) bits.push(h.port_type);
      if (bits.length) {
        host.appendChild(span("gen-monitor-fields", bits.join("  ·  ")));
      }
      return;
    }
    host.appendChild(span("gen-monitor-idle",
      "Hover a port to inspect it · drag a port circle or grip to rewire"));
  }

  // onPortHover callback handed to renderInstrumentWiring (editable mode).
  function setMonitorHover(assignment) {
    _monitorState.hover = assignment;
    renderMonitor();
  }

  function renderWiringDiagram() {
    var host = document.getElementById("gen-wiring-diagram");
    if (!host) return;
    _monitorState.hover = null;   // a re-render invalidates any hovered port
    renderMonitor();
    if (!state.allocation || !window.renderInstrumentWiring) {
      host.innerHTML =
        '<p class="muted">Run Auto-allocate to see the wiring diagram.</p>';
      return;
    }
    renderInstrumentWiring("gen-wiring-diagram",
                           buildInstrumentData(state.allocation), {},
                           { editable: true, onPortHover: setMonitorHover });
    attachWiringDrag();
    // Ring the ports involved in any validation error, then list the issues.
    var issues = validateWiring();
    issues.forEach(function (it) {
      if (it.level !== "error") return;
      (it.ports || []).forEach(function (p) {
        host.querySelectorAll('.iw-port[data-con="' + p.con + '"][data-slot="' +
          p.slot + '"][data-port="' + p.port + '"][data-io="' +
          (p.io_type || "output") + '"]').forEach(function (cell) {
          cell.classList.add("iw-port-invalid");
        });
      });
    });
    renderWiringIssues(issues);
  }

  // Close button of the shared instrument-wiring JSON drill-down panel.
  window.closeJsonPanel = function () {
    var p = document.getElementById("json-panel");
    if (p) p.classList.add("hidden");
  };

  // -- step 5: drag-and-drop port editing -------------------------------
  // Drag a port circle (a line) onto another port. xy/z/coupler move or
  // swap; a qubit's readout joins another feedline (or starts a new one).
  // Pure client-side: mutate state.allocation (redraw) + state.spec.lines.

  var DIAG_TO_ALLOC = { rr: "rr", rr_in: "rr", xy: "xy", z: "z", coupler: "c" };
  var DIAG_TO_LINE = { rr: "resonator", rr_in: "resonator", xy: "drive",
                       z: "flux", coupler: "coupler" };
  var ALLOC_TO_LINE = { rr: "resonator", xy: "drive", z: "flux", c: "coupler" };

  var _wireDrag = null;

  function femTypeAt(con, slot) {
    var ctrls = ((state.spec.instruments || {}).controllers) || [];
    for (var i = 0; i < ctrls.length; i++) {
      if (String(ctrls[i].con) === String(con)) {
        var fems = ctrls[i].fems || [];
        for (var j = 0; j < fems.length; j++) {
          if (String(fems[j].slot) === String(slot)) {
            return fems[j].fem === "mw" ? "mw-fem" : "lf-fem";
          }
        }
      }
    }
    return null;
  }

  // Lines currently allocated at a physical port (read from state.allocation).
  function linesAtPort(con, slot, port, io) {
    var found = [];
    Object.keys(state.allocation || {}).forEach(function (element) {
      Object.keys(state.allocation[element] || {}).forEach(function (key) {
        (state.allocation[element][key] || []).forEach(function (ch) {
          if (String(ch.con) === String(con) && String(ch.slot) === String(slot) &&
              String(ch.port) === String(port) && (ch.io_type || "output") === io) {
            found.push({ element: element, allocKey: key });
          }
        });
      });
    });
    return found;
  }

  function specLine(element, lineType) {
    return state.spec.lines.filter(function (ln) {
      return ln.element === element && ln.line === lineType;
    })[0] || null;
  }

  function freshFeedlineGroup() {
    var used = {};
    state.spec.lines.forEach(function (ln) {
      if (ln.line === "resonator" && ln.group) used[ln.group] = true;
    });
    for (var k = 1; k < 9999; k++) {
      if (!used["feedline" + k]) return "feedline" + k;
    }
    return "feedline_" + Date.now();
  }

  function readCell(cellEl) {
    if (!cellEl) return null;
    return {
      con: parseInt(cellEl.getAttribute("data-con"), 10),
      slot: parseInt(cellEl.getAttribute("data-slot"), 10),
      port: parseInt(cellEl.getAttribute("data-port"), 10),
      io: cellEl.getAttribute("data-io")
    };
  }

  // May the dragged item (src) be dropped on target cell t?
  // MW output ports are uniform: drives and readout-outputs swap freely.
  function isValidDrop(src, t) {
    if (!t) return false;
    if (src.con === t.con && src.slot === t.slot &&
        src.port === t.port && src.io === t.io) return false;     // same port
    var targetFem = femTypeAt(t.con, t.slot);
    if (src.role === "rr" || src.role === "rr_in") {
      // Output and input are independent — no FEM coupling between them.
      var wantIo = (src.role === "rr_in") ? "input" : "output";
      if (targetFem !== "mw-fem" || t.io !== wantIo) return false;
      if (src.whole) return true;                       // grip → any MW port
      // a single readout endpoint: an empty port, or one already holding readout
      var here = linesAtPort(t.con, t.slot, t.port, wantIo);
      return here.length === 0 ||
             here.some(function (x) { return x.allocKey === "rr"; });
    }
    if (src.role === "xy") return targetFem === "mw-fem" && t.io === "output";
    return targetFem === "lf-fem" && t.io === "output";           // z / coupler
  }

  // Move every channel of one allocation entry matching `io` to a new port.
  function moveChannel(x, con, slot, port, io) {
    (state.allocation[x.element][x.allocKey] || []).forEach(function (ch) {
      if ((ch.io_type || "output") === io) {
        ch.con = con; ch.slot = slot; ch.port = port;
      }
    });
  }

  // A feedline's readout output + input must share one MW-FEM. After a port
  // swap, pull any stray readout-input back onto its output's FEM.
  // Rewrite every spec line's channel pin from the drag-mutated allocation.
  function syncSpecChannels() {
    state.spec.lines.forEach(function (ln) {
      var a = state.allocation[ln.element] || {};
      if (ln.line === "drive" && (a.xy || [])[0]) {
        var d = a.xy[0];
        ln.channel = { kind: "mw_fem", con: d.con, slot: d.slot, out_port: d.port };
      } else if (ln.line === "flux" && (a.z || [])[0]) {
        var z = a.z[0];
        ln.channel = { kind: "lf_fem", con: z.con, out_slot: z.slot, out_port: z.port };
      } else if (ln.line === "coupler" && (a.c || [])[0]) {
        var c = a.c[0];
        ln.channel = { kind: "lf_fem", con: c.con, out_slot: c.slot, out_port: c.port };
      } else if (ln.line === "resonator" && a.rr) {
        var ro = a.rr.filter(function (x) { return (x.io_type || "output") === "output"; })[0];
        var ri = a.rr.filter(function (x) { return x.io_type === "input"; })[0];
        if (ro && ri) {
          ln.channel = { kind: "mw_fem", con: ro.con, slot: ro.slot,
                         out_port: ro.port, in_port: ri.port };
          // Feedline = qubits sharing one rr output port — re-derive `group`
          // from the dragged layout so build_connectivity multiplexes them.
          ln.group = "fl_" + ro.con + "_" + ro.slot + "_" + ro.port;
        }
      }
    });
  }

  // -- step 5: wiring validation ---------------------------------------
  // Rule engine over state.allocation. R1 (error): qubits sharing one rr
  // output port must share one rr input port, and vice versa — one output
  // port physically feeds one input port. R2 (warn): a feedline whose output
  // and input land on different MW-FEMs.
  function validateWiring() {
    var issues = [];
    if (!state.allocation) return issues;
    var outc = {}, inc = {};   // qubit -> rr output / input channel
    Object.keys(state.allocation).forEach(function (q) {
      var rr = state.allocation[q].rr;
      if (!rr) return;
      var o = rr.filter(function (c) { return (c.io_type || "output") === "output"; })[0];
      var i = rr.filter(function (c) { return c.io_type === "input"; })[0];
      if (o) outc[q] = o;
      if (i) inc[q] = i;
    });
    function key(c) { return c.con + "/" + c.slot + "/" + c.port; }
    function label(c) { return "con" + c.con + " slot" + c.slot + " p" + c.port; }
    // R1 — qubits sharing a `primary` port must all share one `secondary` port.
    function checkBundle(primary, secondary, primaryName, secondaryName) {
      var groups = {};
      Object.keys(primary).forEach(function (q) {
        (groups[key(primary[q])] || (groups[key(primary[q])] = [])).push(q);
      });
      Object.keys(groups).forEach(function (gk) {
        var qs = groups[gk].sort();
        var sec = {};
        qs.forEach(function (q) { if (secondary[q]) sec[key(secondary[q])] = 1; });
        if (Object.keys(sec).length > 1) {
          issues.push({
            level: "error",
            message: primaryName + " " + label(primary[qs[0]]) + " carries " +
              qs.join(", ") + " but their readout " + secondaryName +
              "s differ — one " + primaryName + " port must feed one " +
              secondaryName + " port.",
            ports: [primary[qs[0]]]
          });
        }
      });
    }
    checkBundle(outc, inc, "output", "input");
    checkBundle(inc, outc, "input", "output");
    // R2 — a feedline's output + input belong on the same MW-FEM.
    var seen = {};
    Object.keys(outc).forEach(function (q) {
      var o = outc[q], i = inc[q];
      if (!i) return;
      if (String(o.con) !== String(i.con) || String(o.slot) !== String(i.slot)) {
        var k = key(o) + "|" + key(i);
        if (seen[k]) return;
        seen[k] = 1;
        issues.push({
          level: "warn",
          message: "Feedline output " + label(o) + " and input " + label(i) +
            " are on different FEMs — recommended to keep a feedline on one FEM.",
          ports: [o, i]
        });
      }
    });
    return issues;
  }

  // Render the validation issues panel for step 5.
  function renderWiringIssues(issues) {
    var host = document.getElementById("gen-wiring-issues");
    if (!host) return;
    host.innerHTML = "";
    if (!issues || !state.allocation) return;
    if (!issues.length) {
      var ok = document.createElement("div");
      ok.className = "gen-wiring-ok";
      ok.textContent = "✓ Wiring valid";
      host.appendChild(ok);
      return;
    }
    issues.forEach(function (it) {
      var row = document.createElement("div");
      row.className = "gen-wiring-issue gen-wiring-" + it.level;
      row.textContent = (it.level === "error" ? "✗ " : "⚠ ") + it.message;
      host.appendChild(row);
    });
  }

  // Swap the contents of two ports (move if the target is empty). Drives and
  // readout-outputs are interchangeable on MW output ports; a feedline (all
  // its qubits) moves as a unit.
  function applyPortEdit(src, t) {
    var io = src.io;
    var srcLines = linesAtPort(src.con, src.slot, src.port, io);
    var tgtLines = linesAtPort(t.con, t.slot, t.port, io);
    srcLines.forEach(function (x) { moveChannel(x, t.con, t.slot, t.port, io); });
    tgtLines.forEach(function (x) { moveChannel(x, src.con, src.slot, src.port, io); });
    state.wiringTouched = true;
    syncSpecChannels();
    renderWiringDiagram();
    renderWiringTable();
  }

  // Move ONE qubit's readout endpoint. Output and input are independent — an
  // output-circle drag moves only the output channel, an input-circle drag
  // only the input. Consistency is checked later (the Populate band check),
  // never enforced by snapping the two ports together.
  function applyQubitReadoutEdit(src, t) {
    (state.allocation[src.element].rr || []).forEach(function (ch) {
      if ((ch.io_type || "output") === src.io) {
        ch.con = t.con; ch.slot = t.slot; ch.port = t.port;
      }
    });
    state.wiringTouched = true;
    syncSpecChannels();
    renderWiringDiagram();
    renderWiringTable();
  }

  function attachWiringDrag() {
    var host = document.getElementById("gen-wiring-diagram");
    if (!host) return;
    host.querySelectorAll(".iw-port-circle, .iw-port-grip").forEach(function (el) {
      el.addEventListener("mousedown", onWireDragStart);
    });
  }

  function onWireDragStart(ev) {
    ev.preventDefault();
    var el = ev.currentTarget;
    var cell = el.closest && el.closest(".iw-port");
    if (!cell) return;
    var c = readCell(cell);
    // The grip drags the whole feedline; a circle drags just that one qubit.
    var isGrip = el.classList && el.classList.contains("iw-port-grip");
    _wireDrag = {
      whole: isGrip,
      element: isGrip ? null : el.getAttribute("data-element"),
      role: isGrip ? (c.io === "input" ? "rr_in" : "rr") : el.getAttribute("data-role"),
      con: c.con, slot: c.slot, port: c.port, io: c.io,
      hover: null
    };
    _monitorState.drag = {
      whole: _wireDrag.whole, element: _wireDrag.element, role: _wireDrag.role,
      hoverPort: null, valid: false
    };
    renderMonitor();
    document.addEventListener("mousemove", onWireDragMove);
    document.addEventListener("mouseup", onWireDragEnd);
    document.addEventListener("keydown", onWireDragKey);
  }

  function onWireDragMove(ev) {
    if (!_wireDrag) return;
    var el = document.elementFromPoint(ev.clientX, ev.clientY);
    var cell = el && el.closest ? el.closest(".iw-port") : null;
    if (cell !== _wireDrag.hover) {
      if (_wireDrag.hover) {
        _wireDrag.hover.classList.remove("iw-port-ok", "iw-port-bad");
      }
      _wireDrag.hover = cell;
      var tgt = cell ? readCell(cell) : null;
      var valid = tgt ? isValidDrop(_wireDrag, tgt) : false;
      if (cell) cell.classList.add(valid ? "iw-port-ok" : "iw-port-bad");
      if (_monitorState.drag) {
        _monitorState.drag.hoverPort = tgt;
        _monitorState.drag.valid = valid;
        renderMonitor();
      }
    }
  }

  function onWireDragEnd(ev) {
    if (!_wireDrag) return;
    var drag = _wireDrag;
    var el = document.elementFromPoint(ev.clientX, ev.clientY);
    var cell = el && el.closest ? el.closest(".iw-port") : null;
    cleanupWireDrag();
    if (cell) {
      var target = readCell(cell);
      if (isValidDrop(drag, target)) {
        if (!drag.whole && (drag.role === "rr" || drag.role === "rr_in")) {
          applyQubitReadoutEdit(drag, target);   // a circle → move one qubit
        } else {
          applyPortEdit(drag, target);           // grip / drive → whole port
        }
      }
    }
  }

  function onWireDragKey(ev) {
    if (ev.key === "Escape") cleanupWireDrag();
  }

  function cleanupWireDrag() {
    if (!_wireDrag) return;
    if (_wireDrag.hover) _wireDrag.hover.classList.remove("iw-port-ok", "iw-port-bad");
    _wireDrag = null;
    _monitorState.drag = null;
    _monitorState.hover = null;
    renderMonitor();
    document.removeEventListener("mousemove", onWireDragMove);
    document.removeEventListener("mouseup", onWireDragEnd);
    document.removeEventListener("keydown", onWireDragKey);
  }

  function enterWiringStep() {
    deriveLines();
    renderWiringTable();
    renderWiringDiagram();
  }

  function runAutoAllocate() {
    if (!state.env) {
      showMessage("Select an environment in step 1 first.", "warn");
      return;
    }
    var btn = document.getElementById("gen-allocate-btn");
    var status = document.getElementById("gen-allocate-status");
    if (btn) btn.disabled = true;
    if (status) status.textContent = "Allocating…";

    fetch("/generate/allocate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec: state.spec })
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (btn) btn.disabled = false;
        if (res.ok && res.result) {
          state.allocation = res.result.allocation || {};
          if (status) status.textContent = "Allocated.";
          renderWiringTable();
          renderWiringDiagram();
          var warns = res.result.warnings || [];
          if (warns.length) showMessage(warns.join(" "), "warn");
        } else {
          if (status) status.textContent = "";
          showMessage(
            res.error || (res.errors || []).join("; ") || "Allocation failed.",
            "error"
          );
        }
      })
      .catch(function () {
        if (btn) btn.disabled = false;
        if (status) status.textContent = "";
        showMessage("Allocation request failed.", "error");
      });
  }

  function bindWiringStep() {
    var btn = document.getElementById("gen-allocate-btn");
    if (btn) btn.addEventListener("click", runAutoAllocate);
  }

  // -- step 6: populate ------------------------------------------------

  // Unit toggle. state.spec.populate always stores SI base units (Hz, ns, V)
  // — the generator's contract. Only the value shown in / typed into a cell
  // is scaled by the per-dimension unit the user picks.
  //
  // The `amp` dim covers MW-FEM pulse amplitudes (dimensionless [-1, 1] in
  // the spec). Display modes:
  //   "0-1"  raw dimensionless value
  //   "dBm"  output power = FSP + 20·log10(|amp|)
  //   "V_pk" peak voltage at 50 Ω = amp · sqrt(2·50·10^(FSP/10)/1000)
  // Conversion depends on the port's full_scale_power_dbm (per qubit), so
  // amp is special-cased in setPopValue / buildPopCell rather than going
  // through the scalar unitFactor() path. Formulas verified against
  // quam_builder/tools/power_tools.py (set_output_power_mw_channel,
  // get_output_power_mw_channel, calculate_voltage_scaling_factor).
  var POP_UNITS = {
    freq: { Hz: 1, MHz: 1e6, GHz: 1e9 },
    time: { ns: 1, "µs": 1e3 },
    volt: { V: 1, mV: 1e-3 },
    amp:  { "0-1": "linear", "dBm": "dbm", "V_pk": "vpk" }
  };
  var POP_UNIT_LABEL = {
    freq: "Frequency", time: "Time", volt: "Voltage", amp: "Amplitude"
  };
  // Fields stored as dimensionless MW-FEM amplitudes in the spec.
  var POP_AMP_FIELDS = {
    readout_amplitude: "resonator",
    x180_amplitude: "qubit",
    saturation_amplitude: "qubit"
  };
  // MWFEMAnalogOutputPort default — quam/components/ports/analog_outputs.py:80.
  var POP_FSP_DEFAULT = -11;

  // Overlay persisted unit choices onto state.populateUnits (runs once the
  // POP_UNITS / state definitions exist, i.e. on entering step 6).
  function loadPopulateUnits() {
    var saved;
    try {
      saved = JSON.parse(localStorage.getItem("quam_populate_units") || "{}");
    } catch (e) { return; }
    Object.keys(POP_UNITS).forEach(function (dim) {
      if (saved[dim] && POP_UNITS[dim][saved[dim]] != null) {
        state.populateUnits[dim] = saved[dim];
      }
    });
  }

  function savePopulateUnits() {
    try {
      localStorage.setItem("quam_populate_units",
                           JSON.stringify(state.populateUnits));
    } catch (e) { /* localStorage unavailable — units just won't persist */ }
  }

  function loadPowerMode() {
    try {
      var v = localStorage.getItem("quam_gen_power_mode");
      if (v === "absolute" || v === "manual") state.powerMode = v;
    } catch (e) { /* localStorage unavailable */ }
  }
  function savePowerMode() {
    try { localStorage.setItem("quam_gen_power_mode", state.powerMode); }
    catch (e) { /* localStorage unavailable */ }
  }

  // The effective amp display/entry mode. Absolute power mode forces dBm —
  // an amp cell there IS the pulse's absolute output power, and committing
  // it re-allocates the port FSP (see recomputeXyPower / recomputeReadoutPower).
  function ampMode() {
    return state.powerMode === "absolute" ? "dBm" : state.populateUnits.amp;
  }

  // Scale factor from the active unit of `dim` to SI base (1 if no dimension
  // or if the dim isn't a simple multiplicative unit — e.g. amp).
  function unitFactor(dim) {
    if (!dim) return 1;
    var u = POP_UNITS[dim];
    if (!u) return 1;
    var v = u[state.populateUnits[dim]];
    return typeof v === "number" ? v : 1;
  }

  // base SI value -> number shown in a cell (trims float noise from / 1e9).
  function toDisplayValue(baseVal, dim) {
    if (baseVal == null || baseVal === "") return "";
    var n = parseFloat(baseVal);
    if (isNaN(n)) return "";
    if (!dim) return n;
    return parseFloat((n / unitFactor(dim)).toPrecision(12));
  }

  // number typed in a cell -> SI base value stored in the spec.
  function toBaseValue(displayVal, dim) {
    var n = parseFloat(window.NumberInput.strip(displayVal));   // accept "100,000,000"
    if (isNaN(n)) return NaN;
    return dim ? n * unitFactor(dim) : n;
  }

  // -- step 6: MW amplitude conversions --------------------------------
  // FSP for the row that holds an amp value. XY amps (x180/saturation)
  // borrow the qubit's xy.opx_output FSP; readout uses the resonator's.
  // Fallback POP_FSP_DEFAULT matches the QUAM port default.
  function fspForAmp(group, rid) {
    var pop = state.spec.populate || {};
    var src = POP_AMP_FIELDS_SOURCE[group] || "qubit";
    var fsp = ((pop[src] || {})[rid] || {}).full_scale_power_dbm;
    // Coerce to a finite number: a string/NaN FSP (from a corrupt or
    // hand-edited sessionStorage draft) would otherwise string-concatenate in
    // the absolute-mode target recovery and write NaN across a whole bank.
    var n = Number(fsp);
    return (fsp == null || !isFinite(n)) ? POP_FSP_DEFAULT : n;
  }
  // Per-group FSP source: pulses rows are per-qubit, FSP comes from the
  // qubit table. Resonator rows have their own FSP column.
  var POP_AMP_FIELDS_SOURCE = {
    qubit: "qubit", pulses: "qubit", resonator: "resonator"
  };

  // baseVal is dimensionless [-1, 1]. Returns the value to display in the
  // active amp mode, or "" for an empty/invalid input.
  function ampToDisplay(baseVal, mode, fsp) {
    if (baseVal == null || baseVal === "") return "";
    var v = parseFloat(baseVal);
    if (isNaN(v)) return "";
    if (mode === "dBm") {
      if (v === 0) return "";   // log(0) is -∞ — no useful dBm value
      return parseFloat((fsp + 20 * Math.log10(Math.abs(v))).toPrecision(8));
    }
    if (mode === "V_pk") {
      var vMax = Math.sqrt(2 * 50 * Math.pow(10, fsp / 10) / 1000);
      return parseFloat((v * vMax).toPrecision(8));
    }
    return parseFloat(v.toPrecision(12));
  }

  // displayVal is in the active amp mode. Returns the dimensionless [-1, 1]
  // value to store in the spec, or NaN for unparseable input. dBm and V_pk
  // round-trips lose sign (amp is treated as |amp|, the QM convention for
  // single-tone pulses).
  function ampToBase(displayVal, mode, fsp) {
    var v = parseFloat(window.NumberInput.strip(displayVal));   // accept "100,000,000"
    if (isNaN(v)) return NaN;
    if (mode === "dBm") {
      return Math.pow(10, (v - fsp) / 20);
    }
    if (mode === "V_pk") {
      var vMax = Math.sqrt(2 * 50 * Math.pow(10, fsp / 10) / 1000);
      return v / vMax;
    }
    return v;
  }

  // Column header — append the active unit for a dimensioned column, or a
  // static display-only unit string (e.g. "0-1" for MW amplitudes) when the
  // column carries no `dim` but a fixed `unit` label.
  function colHeader(col) {
    if (col.dim === "amp") {
      // Absolute mode: the cell IS the pulse's output power and committing it
      // re-allocates the port FSP — distinguish it from the manual+dBm display
      // unit, which converts against a FIXED hand-set FSP.
      if (state.powerMode === "absolute") return col.label + " (dBm · sets FSP)";
      return col.label + " (" + ampMode() + ")";
    }
    if (col.dim) return col.label + " (" + state.populateUnits[col.dim] + ")";
    if (col.unit) return col.label + " (" + col.unit + ")";
    if (col.field === "full_scale_power_dbm" && state.powerMode === "absolute") {
      return col.label.replace("(dBm)", "(dBm · auto)");
    }
    return col.label;
  }

  // noBulk: a reason string excludes the column from the "Set all" row — LO is
  // auto-derived from RF (one shared value would break the IF window); grid
  // positions are unique per qubit.
  var POP_QUBIT_COLS = [
    { field: "RF_freq", label: "RF freq", dim: "freq" },
    { field: "anharmonicity", label: "anharm.", dim: "freq" },
    { field: "LO_frequency", label: "LO", dim: "freq",
      noBulk: "Auto-derived from RF freq to stay within the LO's IF window" },
    { field: "full_scale_power_dbm", label: "FSP (dBm)" },
    { field: "grid_location", label: "grid", kind: "text",
      noBulk: "Grid position is unique per qubit" }
  ];
  var POP_RESONATOR_COLS = [
    { field: "RF_freq", label: "RF freq", dim: "freq" },
    { field: "LO_frequency", label: "LO", dim: "freq",
      noBulk: "Auto-derived from RF freq to stay within the LO's IF window" },
    { field: "depletion_time", label: "depletion", dim: "time" },
    { field: "time_of_flight", label: "ToF", dim: "time" },
    { field: "readout_length", label: "readout len", dim: "time" },
    // MW-FEM amplitude stored as dimensionless [-1, 1]. The Amplitude unit
    // toggle (renderUnitToggles) lets the user enter it as dBm or peak V at
    // 50 Ω, derived from the qubit's resonator FSP.
    { field: "readout_amplitude", label: "readout amp", dim: "amp" },
    // Multiplexed qubits share one readout MW-FEM port, so they must share
    // one FSP. The cell value auto-syncs across the feedline group on edit
    // (see recomputeReadoutFSP). Allowed range [-11, +16] dBm in 3 dB steps
    // per qualang_tools/config/instrument_limits.py:OPX1000_MW_POWER_*.
    { field: "full_scale_power_dbm", label: "FSP (dBm)" }
  ];
  var POP_FLUX_COLS = [
    { field: "joint_offset", label: "joint", dim: "volt" },
    { field: "independent_offset", label: "indep", dim: "volt" },
    { field: "min_offset", label: "min", dim: "volt" },
    { field: "flux_point", label: "flux point", kind: "select",
      options: ["", "joint", "independent", "min", "arbitrary", "zero"] },
    { field: "output_mode", label: "output", kind: "select",
      options: ["", "direct", "amplified"] },
    { field: "upsampling_mode", label: "upsampling", kind: "select",
      options: ["", "mw", "pulse"] }
  ];
  // Per-pair 2Q-gate seed values. The columns shown depend on the chip's gate
  // (step 4): a CZ chip tunes the flux pulse + its variant; a CR chip tunes the
  // cross-resonance drive/cancel scaling + the ZI/IZ correction phases. The
  // values flow to spec.populate.pairs[<control-target>] and run_build seeds
  // them onto the gate (see _seed_cz_variant / _seed_cr_gate).
  var POP_CZ_PAIR_COLS = [
    // cz_variant picks which flux-pulse shape run_build seeds (unipolar default;
    // the rest are opt-in). Mirrors the customer's pair_gates.add_cz variants.
    { field: "cz_variant", label: "CZ variant", kind: "select",
      options: ["", "unipolar", "flattop", "bipolar", "SNZ", "flattop_erf"] },
    { field: "cz_interaction_duration", label: "CZ dur", dim: "time" },
    { field: "cz_amplitude", label: "CZ amp", dim: "volt" },
    { field: "moving_qubit", label: "moving qubit", kind: "select",
      options: ["", "control", "target"] },
    // Orientation escape hatch: "manual" pins the pair's control/target as
    // typed; blank/"auto" lets czAutoOrient() order by RF_freq (higher =
    // control). Kept in populate.pairs so reconcilePopulatePairs preserves it.
    { field: "cz_order", label: "order", kind: "select",
      options: ["", "auto", "manual"] },
    { field: "coupler_interaction_offset", label: "coupler off", dim: "volt" }
  ];
  var POP_CR_PAIR_COLS = [
    { field: "cr_drive_amplitude", label: "CR drive amp" },
    { field: "cr_cancel_amplitude", label: "CR cancel amp" },
    { field: "cr_drive_phase", label: "CR drive phase" },
    { field: "cr_cancel_phase", label: "CR cancel phase" },
    { field: "qc_correction_phase", label: "ZI corr (qc)" },
    { field: "qt_correction_phase", label: "IZ corr (qt)" },
    // Drive pulse library: "" (default) = basic square+flattop; "full" = the
    // customer's 4-shape set (+ cosine/gauss with DRAG params slaved to the
    // target's x180, and their cancel twins).
    { field: "cr_shapes", label: "CR shapes", kind: "select",
      options: ["", "basic", "full"] },
    // Escape hatch: run_build derives these from the target qubit's frequencies
    // when populated; set them explicitly to override (the CR drive plays at
    // target_LO + target_IF).
    { field: "target_qubit_LO_frequency", label: "target LO", dim: "freq" },
    { field: "target_qubit_IF_frequency", label: "target IF", dim: "freq" }
  ];
  // ZZ (Stark-CZ) populate columns — appended when the ZZ toggle is on.
  var POP_ZZ_PAIR_COLS = [
    { field: "zz_detuning", label: "ZZ detuning", dim: "freq" },
    { field: "zz_drive_amplitude", label: "ZZ drive amp" },
    { field: "zz_flattop_length", label: "ZZ flattop len", dim: "time" },
    { field: "zz_flattop_flat_length", label: "ZZ flat len", dim: "time" }
  ];
  // Alias for the LO-map cross-link (the coupler diagram role maps to the pairs
  // group; only present on a tunable-coupler chip, which is always CZ).
  var POP_PAIR_COLS = POP_CZ_PAIR_COLS;

  // The pair populate columns for the chip's current 2Q gate. CR uses its own
  // set; CZ drops the coupler-only column unless there's a tunable coupler.
  function pairPopCols() {
    if (state.pairGate === "cr") {
      return state.zzEnabled
        ? POP_CR_PAIR_COLS.concat(POP_ZZ_PAIR_COLS)
        : POP_CR_PAIR_COLS;
    }
    if (state.pairGate === "cz_tunable") return POP_CZ_PAIR_COLS;
    return POP_CZ_PAIR_COLS.filter(function (c) {
      return c.field !== "coupler_interaction_offset";
    });
  }

  // Drop stale per-pair populate: entries for pairs that no longer exist, and
  // fields that don't belong to the current gate (e.g. cr_* left over after a
  // CR->CZ switch). Keeps the spec + sessionStorage draft clean and stops a
  // hidden override from silently re-attaching when a pair / gate is restored.
  function reconcilePopulatePairs() {
    var pop = state.spec.populate;
    if (!pop || !pop.pairs) return;
    var liveIds = {};
    state.spec.qubit_pairs.forEach(function (p) {
      if (p[0] && p[1]) liveIds[p[0] + "-" + p[1]] = true;
    });
    var keep = {};
    pairPopCols().forEach(function (c) { keep[c.field] = true; });
    Object.keys(pop.pairs).forEach(function (id) {
      if (!liveIds[id]) { delete pop.pairs[id]; return; }
      var bucket = pop.pairs[id];
      Object.keys(bucket).forEach(function (f) {
        if (!keep[f]) delete bucket[f];
      });
      if (Object.keys(bucket).length === 0) delete pop.pairs[id];
    });
  }

  // -- step 6: CZ automatic control/target orientation -----------------
  // For a CZ chip the pair ROLES follow the physics: the higher-RF_freq
  // qubit is the control, the lower one the target (customer requirement —
  // and consistent with _seed_cz_variant's default: the higher-f qubit is
  // the flux-moving one). Pairs are drawn in step 4 before frequencies
  // exist, so orientation re-runs on every qubit RF_freq commit and flips
  // the STORED spec pair (run_build's pair-id contract stays untouched;
  // run_build only adds a warning safety net). CR pairs NEVER flip — the
  // CR drive direction is a physical user choice. A per-pair "order"
  // column (auto/manual) is the escape hatch; hand-editing the step-4
  // Control/Target dropdowns marks that pair manual.
  function czOrderActive() {
    return (state.pairGate === "cz_fixed" || state.pairGate === "cz_tunable") &&
           state.mode !== "regenerate";
  }

  function rfFreqOf(qid) {
    var v = parseFloat((((state.spec.populate || {}).qubit || {})[qid] || {}).RF_freq);
    return isFinite(v) ? v : null;
  }

  // Flip one pair in place, dragging along everything keyed by its id:
  // the populate.pairs bucket, the bucket's moving_qubit ROLE (swapped so
  // the same PHYSICAL qubit keeps the flux pulse), pinned wiring lines
  // (deriveLines keys pins on element|line), and allocation entries in
  // both the spec ("q1-q2") and QUAM ("q1-2") key forms.
  function flipPairOrder(pair) {
    var oldId = pair[0] + "-" + pair[1];
    var t = pair[0]; pair[0] = pair[1]; pair[1] = t;
    var newId = pair[0] + "-" + pair[1];
    var pop = state.spec.populate || {};
    if (pop.pairs && pop.pairs[oldId]) {
      pop.pairs[newId] = pop.pairs[oldId];
      delete pop.pairs[oldId];
      var mv = pop.pairs[newId].moving_qubit;
      if (mv === "control") pop.pairs[newId].moving_qubit = "target";
      else if (mv === "target") pop.pairs[newId].moving_qubit = "control";
    }
    (state.spec.lines || []).forEach(function (ln) {
      if (ln.element === oldId) ln.element = newId;
    });
    if (state.allocation) {
      var keyPairs = [[oldId, newId]];
      if (window.TopoGraph) {
        keyPairs.push([window.TopoGraph.quamPairId(oldId),
                       window.TopoGraph.quamPairId(newId)]);
      }
      keyPairs.forEach(function (kp) {
        if (kp[0] !== kp[1] && state.allocation[kp[0]] != null) {
          state.allocation[kp[1]] = state.allocation[kp[0]];
          delete state.allocation[kp[0]];
        }
      });
    }
    state.pairsTouched = true;
  }

  // Re-orient every CZ pair whose frequencies say the order is wrong.
  // Returns the flips performed (empty when nothing changed). Strict
  // ft > fc: equal or missing frequencies never flip.
  function czAutoOrient() {
    if (!czOrderActive()) return [];
    var flips = [];
    state.spec.qubit_pairs.forEach(function (pair) {
      if (!pair[0] || !pair[1]) return;
      var bucket = ((state.spec.populate || {}).pairs || {})[pair[0] + "-" + pair[1]] || {};
      if (bucket.cz_order === "manual") return;
      var fc = rfFreqOf(pair[0]), ft = rfFreqOf(pair[1]);
      if (fc == null || ft == null || !(ft > fc)) return;
      var oldId = pair[0] + "-" + pair[1];
      flipPairOrder(pair);
      flips.push({ from: oldId, to: pair[0] + "-" + pair[1] });
    });
    if (flips.length) deriveLines();
    return flips;
  }

  // Per-pair orientation status for the note + review summary.
  function czOrderStatus() {
    var out = [];
    state.spec.qubit_pairs.forEach(function (pair) {
      if (!pair[0] || !pair[1]) return;
      var id = pair[0] + "-" + pair[1];
      var bucket = ((state.spec.populate || {}).pairs || {})[id] || {};
      var fc = rfFreqOf(pair[0]), ft = rfFreqOf(pair[1]);
      out.push({
        id: id,
        status: bucket.cz_order === "manual" ? "manual"
          : (fc == null || ft == null) ? "pending"
          : fc === ft ? "equal" : "ok"
      });
    });
    return out;
  }

  function renderPairOrderNote() {
    var note = document.getElementById("gen-pop-pair-order-note");
    if (!note) return;
    if (!czOrderActive() || !state.spec.qubit_pairs.length) {
      note.hidden = true;
      return;
    }
    var buckets = { ok: [], manual: [], pending: [], equal: [] };
    czOrderStatus().forEach(function (s) { buckets[s.status].push(s.id); });
    var parts = [];
    if (buckets.ok.length) parts.push(buckets.ok.join(", ") + " set from frequencies");
    if (buckets.pending.length) parts.push(buckets.pending.join(", ") + " pending (missing RF freq)");
    if (buckets.equal.length) parts.push(buckets.equal.join(", ") + " equal frequencies (order kept)");
    if (buckets.manual.length) parts.push(buckets.manual.join(", ") + " manual");
    note.textContent = "CZ orientation is automatic — higher RF-freq qubit = " +
      "control, lower = target. " + parts.join(" · ") + ".";
    note.hidden = false;
  }

  // A qubit RF_freq commit may re-orient CZ pairs: rebuild the pairs table
  // (its row keys changed) + surface what moved.
  function czOrientAfterFreqEdit() {
    if (!czOrderActive()) return;
    var flips = czAutoOrient();
    if (flips.length) {
      var pairs = state.spec.qubit_pairs
        .filter(function (p) { return p[0] && p[1]; })
        .map(function (p) { return p[0] + "-" + p[1]; });
      setPopHost("gen-pop-pairs",
        pairs.length ? buildPopTable("pairs", pairs, pairPopCols(), "Pair") : null,
        "No qubit pairs defined.");
      showMessage(flips.map(function (f) {
        return "Pair " + f.from + " reordered to " + f.to;
      }).join("; ") + " — control is the higher-frequency qubit.", "info");
    }
    renderPairOrderNote();
  }

  // Mark a pair's orientation as hand-picked (step-4 dropdown edit).
  function markPairManual(pair) {
    if (!czOrderActive() || !pair[0] || !pair[1]) return;
    var pop = state.spec.populate;
    pop.pairs = pop.pairs || {};
    var id = pair[0] + "-" + pair[1];
    pop.pairs[id] = pop.pairs[id] || {};
    pop.pairs[id].cz_order = "manual";
  }
  // MW-FEM XY amplitudes are stored dimensionless ([-1, 1]); the real power
  // depends on each qubit's xy.opx_output.full_scale_power_dbm. The Amplitude
  // unit toggle covers entry in dBm / peak V — see POP_UNITS.amp.
  var POP_PULSE_FIELDS = [
    { field: "x180_length", label: "x180 length", dim: "time" },
    { field: "x180_amplitude", label: "x180 amplitude", dim: "amp" },
    { field: "drag_alpha", label: "DRAG alpha" },
    { field: "drag_detuning", label: "DRAG detuning", dim: "freq" },
    { field: "saturation_length", label: "saturation length", dim: "time" },
    { field: "saturation_amplitude", label: "saturation amplitude", dim: "amp" }
  ];

  // Port role -> the populate group + columns that hold its values, and the
  // reverse (populate group -> the diagram roles it owns). Used to cross-link
  // the read-only step-6 wiring diagram with the populate tables.
  var POP_ROLE_GROUP = {
    xy:      { group: "qubit",     cols: POP_QUBIT_COLS },
    rr:      { group: "resonator", cols: POP_RESONATOR_COLS },
    rr_in:   { group: "resonator", cols: POP_RESONATOR_COLS },
    z:       { group: "flux",      cols: POP_FLUX_COLS },
    coupler: { group: "pairs",     cols: POP_PAIR_COLS }
  };
  var POP_GROUP_ROLES = {
    qubit: ["xy"], resonator: ["rr", "rr_in"], flux: ["z"], pairs: ["coupler"]
  };

  // Write one populate cell's value into `bucket`, converting display units
  // to SI base for dimensioned numeric fields. For amp, `group`/`rid` are
  // needed to look up the row's FSP (a regular numeric column ignores them).
  // A blank value clears the key.
  function setPopValue(bucket, col, raw, group, rid) {
    raw = (raw == null ? "" : String(raw)).trim();
    if (raw === "") {
      delete bucket[col.field];
    } else if (col.kind === "text" || col.kind === "select") {
      bucket[col.field] = raw;
    } else if (col.dim === "amp") {
      var n = ampToBase(raw, ampMode(), fspForAmp(group, rid));
      if (!isNaN(n)) bucket[col.field] = n;
    } else {
      var n = col.dim ? toBaseValue(raw, col.dim) : parseFloat(window.NumberInput.strip(raw));
      if (!isNaN(n)) bucket[col.field] = n;
    }
  }

  function buildPopCell(group, rid, col) {
    var current = (((state.spec.populate[group] || {})[rid]) || {})[col.field];
    var input;
    if (col.kind === "select") {
      input = document.createElement("select");
      col.options.forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt;
        o.textContent = opt === "" ? "—" : opt;
        input.appendChild(o);
      });
      input.value = current == null ? "" : String(current);
    } else {
      input = document.createElement("input");
      input.type = col.kind === "text" ? "text" : "number";
      if (current != null) {
        if (col.dim === "amp") {
          input.value = ampToDisplay(current, ampMode(),
                                     fspForAmp(group, rid));
        } else {
          input.value = col.dim ? toDisplayValue(current, col.dim) : current;
        }
      }
    }
    // Absolute power mode: FSP is allocated from the pulse powers, never
    // hand-typed — the cell shows the derived value read-only.
    if (col.field === "full_scale_power_dbm" &&
        state.powerMode === "absolute") {
      input.disabled = true;
      input.title = "Auto-allocated from the pulse powers " +
        "(absolute power mode — switch Power input back to FSP + amplitude " +
        "to edit directly)";
    }
    // Absolute power mode: an amp cell holds the pulse's ABSOLUTE output power
    // in dBm; committing it re-allocates the port FSP (readout: the whole
    // multiplexed bank shares one FSP + equal per-tone amplitudes).
    if (col.dim === "amp" && state.powerMode === "absolute") {
      input.title = "Absolute output power in dBm — committing re-allocates " +
        "this port's full-scale power" + (group === "resonator"
          ? " and rewrites the whole readout bank (shared FSP, equal per-tone "
            + "amplitudes)" : "");
    }
    input.className = "gen-pop-in";
    // data-* identity so applyLoAssignments() can find the LO cells.
    input.dataset.group = group;
    input.dataset.rid = rid;
    input.dataset.field = col.field;
    if (col.dim) input.dataset.dim = col.dim;
    // On each keystroke: mark dirty (for the step-nav flush) AND live-write the
    // value into state.spec.populate so the live waveform preview reflects what's
    // being typed — but keep the SIDE EFFECTS (recomputeLOs / refreshAmpCells /
    // the dBm sign + LO re-derive) in the `change` handler only, so an in-progress
    // edit never clobbers a hand-typed LO, flips a dBm negative amp, or storms on
    // a big chip. The change handler re-commits + clears dirty on blur.
    input.addEventListener("input", function () {
      input.dataset.dirty = "1";
      var pop = state.spec.populate;
      pop[group] = pop[group] || {};
      pop[group][rid] = pop[group][rid] || {};
      setPopValue(pop[group][rid], col, input.value, group, rid);
      if (Object.keys(pop[group][rid]).length === 0) delete pop[group][rid];
      // As-you-type inline validation (debounced per cell; keystroke storms
      // collapse to one run). Read-only decoration — the heavier LO / power
      // recomputes stay on the change handler.
      clearTimeout(input._valTimer);
      input._valTimer = setTimeout(function () {
        validateCellInline(input, group, rid, col);
      }, VALIDATE_DEBOUNCE_MS);
    });
    // Compact value-fit + comma input. Numeric cells become comma-aware + auto-grow
    // (NumberInput flips number→text so "100,000,000" survives); text cells just
    // auto-grow; selects are left alone.
    if (col.kind === "text") {
      window.NumberInput.fit(input);
      input.addEventListener("input", function () { window.NumberInput.fit(input); });
    } else if (col.kind !== "select") {
      window.NumberInput.attach(input);
    }
    input.addEventListener("change", function () {
      // Clear the dirty flag FIRST: this cell is now committed, so any refresh
      // triggered below (refreshAmpCells etc.) may repaint its own display to
      // the achieved value — while still SKIPPING sibling cells that are also
      // dirty (a multi-cell blur-race flush), preserving their typed input.
      input.dataset.dirty = "";
      var pop = state.spec.populate;
      pop[group] = pop[group] || {};
      pop[group][rid] = pop[group][rid] || {};
      setPopValue(pop[group][rid], col, input.value, group, rid);
      if (Object.keys(pop[group][rid]).length === 0) {
        delete pop[group][rid];
      }
      // Only an RF_freq edit re-derives the LOs, so a hand-typed LO sticks.
      if (col.field === "RF_freq") recomputeLOs();
      // A qubit frequency edit may re-orient CZ pairs (higher f = control).
      if (col.field === "RF_freq" && group === "qubit") czOrientAfterFreqEdit();
      // Multiplexed readout shares one MW-FEM port — sync FSP across the group.
      if (col.field === "full_scale_power_dbm" && group === "resonator") {
        recomputeReadoutFSP(rid);
      }
      // FSP edits change every amp cell's dBm/V_pk display for the affected
      // qubit, since amp conversion uses FSP. Cheap to refresh them all.
      if (col.field === "full_scale_power_dbm") refreshAmpCells();
      // Absolute power mode: committing a pulse power (dBm) re-allocates the
      // port FSP and rewrites every amplitude on that port so each pulse's
      // absolute power is preserved (readout: the whole multiplexed bank).
      if (state.powerMode === "absolute" && col.dim === "amp") {
        if (group === "resonator" && col.field === "readout_amplitude") {
          recomputeReadoutPower(rid);
        } else if (group === "pulses") {
          recomputeXyPower(rid);
        }
      } else if (col.field === "readout_amplitude" && group === "resonator") {
        // Manual mode: a readout-amp edit can push the feedline's coherent
        // sum past full scale — re-derive the (sum-only) power findings.
        recomputeAllPowerFindings();
        renderAllConflicts();
      }
      // Commit-time inline validation, immediately (blur is authoritative —
      // don't leave a pending debounce timer racing the refreshed display).
      clearTimeout(input._valTimer);
      validateCellInline(input, group, rid, col);
    });
    return input;
  }

  // The "Set all" cell for one column — on commit, writes its value to every
  // row of the table for that column. Carries no data-* attributes, so it
  // never collides with the LO-cell selectors in applyLoAssignments() /
  // decorateLoCells(). `noBulk` columns get a muted, inert "—" instead.
  function buildBulkCell(group, rowIds, col) {
    var noBulk = col.noBulk;
    // Absolute power mode: FSP is derived, so bulk-setting it is meaningless.
    if (col.field === "full_scale_power_dbm" &&
        state.powerMode === "absolute") {
      noBulk = "Auto-allocated from the pulse powers (absolute power mode)";
    }
    if (noBulk) {
      var na = document.createElement("span");
      na.className = "gen-pop-setall-na";
      na.textContent = "—";
      if (typeof noBulk === "string") na.title = noBulk;
      return na;
    }
    var input;
    if (col.kind === "select") {
      input = document.createElement("select");
      col.options.forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt;
        o.textContent = opt === "" ? "—" : opt;
        input.appendChild(o);
      });
    } else {
      input = document.createElement("input");
      input.type = col.kind === "text" ? "text" : "number";
      input.placeholder = "set all…";
    }
    input.className = "gen-pop-in";
    if (col.kind === "text") {
      window.NumberInput.fit(input);
      input.addEventListener("input", function () { window.NumberInput.fit(input); });
    } else if (col.kind !== "select") {
      window.NumberInput.attach(input);
    }
    input.addEventListener("change", function () {
      var pop = state.spec.populate;
      pop[group] = pop[group] || {};
      rowIds.forEach(function (rid) {
        pop[group][rid] = pop[group][rid] || {};
        setPopValue(pop[group][rid], col, input.value, group, rid);
        if (Object.keys(pop[group][rid]).length === 0) {
          delete pop[group][rid];
        }
      });
      refreshColumnCells(group, col);
      if (col.field === "RF_freq") recomputeLOs();
      if (col.field === "RF_freq" && group === "qubit") czOrientAfterFreqEdit();
      if (col.field === "full_scale_power_dbm") {
        if (group === "resonator") {
          // Set-all already fills every row, so the group is already in sync —
          // just refresh the LO/group decoration for the FSP cells.
          var calc = computeLoAssignments();
          decorateReadoutFSPCells(calc);
        }
        refreshAmpCells();
      }
      // Absolute power mode: a bulk pulse-power fill re-allocates every
      // affected port's FSP. Idempotent per port, so the per-row loop is safe.
      if (state.powerMode === "absolute" && col.dim === "amp") {
        if (group === "resonator" && col.field === "readout_amplitude") {
          rowIds.forEach(function (rid) { recomputeReadoutPower(rid); });
        } else if (group === "pulses") {
          rowIds.forEach(function (rid) { recomputeXyPower(rid); });
        }
      } else if (col.field === "readout_amplitude" && group === "resonator") {
        // Manual mode: bulk-filled readout amps can clip the feedline sum.
        recomputeAllPowerFindings();
        renderAllConflicts();
      }
      // A bulk fill rewrote a whole column — re-validate every cell.
      validateAllPopCells();
    });
    return input;
  }

  // After a bulk fill, refresh one column's body cells in place — no table
  // rebuild, so the "Set all" input keeps focus and its typed value.
  function refreshColumnCells(group, col) {
    document.querySelectorAll('.gen-pop-in[data-group="' + group +
      '"][data-rid][data-field="' + col.field + '"]').forEach(function (input) {
      if (input.dataset.dirty === "1") return;   // don't clobber an uncommitted edit
      var rid = input.dataset.rid;
      var v = (((state.spec.populate[group] || {})[rid]) || {})[col.field];
      if (v == null) input.value = "";
      else if (col.kind === "select") input.value = String(v);
      else if (col.dim === "amp") {
        input.value = ampToDisplay(v, ampMode(),
                                   fspForAmp(group, rid));
      }
      else input.value = col.dim ? toDisplayValue(v, col.dim) : v;
      window.NumberInput.format(input);   // regroup commas + re-fit width (no-op on selects)
    });
  }

  // Refresh every amp cell — for use after the Amplitude unit toggle changes
  // or a qubit's FSP changes (both affect the displayed value but not the
  // stored dimensionless base value).
  function refreshAmpCells() {
    var pop = state.spec.populate || {};
    document.querySelectorAll('.gen-pop-in[data-dim="amp"]').forEach(
      function (input) {
        if (input.dataset.dirty === "1") return;   // preserve an uncommitted typed value
        var group = input.dataset.group;
        var rid = input.dataset.rid;
        var field = input.dataset.field;
        var v = ((pop[group] || {})[rid] || {})[field];
        input.value = ampToDisplay(v, ampMode(),
                                   fspForAmp(group, rid));
        window.NumberInput.format(input);   // regroup commas + re-fit width
      });
  }

  // -- step 6: spreadsheet-style arrow navigation -----------------------
  // Customer feedback: value boxes should be walkable with the arrow keys.
  // → moves to the next cell only when the caret sits at the END of the
  // text; ← moves back only from the START (mid-text arrows keep their
  // native caret behavior); ↑/↓ ALWAYS move to the cell above/below in the
  // same column (including the "Set all" row). SELECTs are never hijacked
  // (arrows change their value natively) and disabled cells (e.g. FSP in
  // absolute power mode) are skipped. One delegated listener per table.
  function popGridKeydown(ev) {
    var el = ev.target;
    if (!el || el.tagName !== "INPUT" || el.disabled ||
        !el.classList || !el.classList.contains("gen-pop-in")) return;
    var key = ev.key;
    if (key !== "ArrowLeft" && key !== "ArrowRight" &&
        key !== "ArrowUp" && key !== "ArrowDown") return;

    var td = el.closest("td");
    var tr = td && td.parentNode;
    if (!td || !tr) return;

    function focusable(cell) {
      return cell && cell.querySelector(
        "input.gen-pop-in:not(:disabled), select.gen-pop-in:not(:disabled)");
    }
    function move(target) {
      ev.preventDefault();
      target.focus();
      if (target.select) target.select();   // retype-ready, like a spreadsheet
    }

    if (key === "ArrowLeft" || key === "ArrowRight") {
      // Only leave the box when the caret is at the matching text edge.
      var atStart = el.selectionStart === 0 && el.selectionEnd === 0;
      var atEnd = el.selectionStart === el.value.length &&
                  el.selectionEnd === el.value.length;
      if (key === "ArrowLeft" && !atStart) return;
      if (key === "ArrowRight" && !atEnd) return;
      var step = key === "ArrowLeft" ? -1 : 1;
      for (var i = td.cellIndex + step; i >= 0 && i < tr.cells.length; i += step) {
        var t = focusable(tr.cells[i]);
        if (t) { move(t); return; }
      }
      return;   // row edge — stay put
    }

    // ↑ / ↓ — same column in the adjacent row (Set-all row included).
    var row = key === "ArrowUp" ? tr.previousElementSibling : tr.nextElementSibling;
    while (row) {
      var vt = focusable(row.cells[td.cellIndex]);
      if (vt) { move(vt); return; }
      row = key === "ArrowUp" ? row.previousElementSibling : row.nextElementSibling;
    }
    ev.preventDefault();   // table edge — swallow so the page doesn't scroll
  }

  function buildPopTable(group, rowIds, columns, rowLabel) {
    var table = document.createElement("table");
    table.className = "gen-pop-table";
    table.id = "gen-pop-tbl-" + group;       // stable id for armPlainResize
    table.addEventListener("keydown", popGridKeydown);
    var thead = document.createElement("thead");
    var hr = document.createElement("tr");
    var th0 = document.createElement("th");
    th0.textContent = rowLabel;
    hr.appendChild(th0);
    columns.forEach(function (col) {
      var th = document.createElement("th");
      th.textContent = colHeader(col);
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    var tbody = document.createElement("tbody");
    // "Set all →" row — bulk-fills a column for every row in this table.
    var setAllTr = document.createElement("tr");
    setAllTr.className = "gen-pop-setall";
    var setAllLabel = document.createElement("td");
    setAllLabel.className = "gen-pop-setall-label";
    setAllLabel.textContent = "Set all →";
    setAllTr.appendChild(setAllLabel);
    columns.forEach(function (col) {
      var bt = document.createElement("td");
      bt.appendChild(buildBulkCell(group, rowIds, col));
      setAllTr.appendChild(bt);
    });
    tbody.appendChild(setAllTr);
    rowIds.forEach(function (rid) {
      var tr = document.createElement("tr");
      tr.dataset.group = group;
      tr.dataset.rid = rid;
      // Hovering a row rings that qubit's port(s) in the read-only diagram.
      tr.addEventListener("mouseenter", function () {
        highlightPorts(group, rid, true);
      });
      tr.addEventListener("mouseleave", function () {
        highlightPorts(group, rid, false);
      });
      var td0 = document.createElement("td");
      td0.textContent = rid;
      tr.appendChild(td0);
      columns.forEach(function (col) {
        var td = document.createElement("td");
        td.appendChild(buildPopCell(group, rid, col));
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    return table;
  }

  function setPopHost(hostId, node, emptyMsg) {
    var host = document.getElementById(hostId);
    if (!host) return;
    host.innerHTML = "";
    if (node) {
      host.appendChild(node);
      var tbl = host.querySelector("table.gen-pop-table[id]");
      if (tbl && window.armPlainResize) window.armPlainResize(tbl.id, "quam_" + tbl.id + "_cols");
    } else {
      var p = document.createElement("p");
      p.className = "muted";
      p.textContent = emptyMsg;
      host.appendChild(p);
    }
  }

  // Real device states show per-qubit x180 length / amplitude / DRAG α /
  // detuning (LabA: 8 distinct α across 9 qubits; variantb: 48 vs 80 ns x180
  // length). The pulses table mirrors the per-qubit shape of the other
  // populate tables — "Set all →" provides the global-default behaviour.
  // spec.populate.pulses is therefore a per-qubit map: {qN: {x180_length…}}.
  function renderPulsesTable() {
    var qubits = state.spec.qubits;
    setPopHost("gen-pop-pulses",
      qubits.length
        ? buildPopTable("pulses", qubits, POP_PULSE_FIELDS, "Qubit")
        : null,
      "Define qubits in step 4 first.");
  }

  // -- step 6: unit toggle ---------------------------------------------

  // Three stage-wide unit selectors (Frequency / Time / Voltage). Changing
  // one re-renders every populate table so cells re-scale to the new unit.
  function renderUnitToggles() {
    var host = document.getElementById("gen-pop-units");
    if (!host) return;
    host.innerHTML = "";
    var lead = document.createElement("span");
    lead.className = "gen-pop-units-lead";
    lead.textContent = "Units:";
    host.appendChild(lead);
    Object.keys(POP_UNITS).forEach(function (dim) {
      var wrap = document.createElement("label");
      wrap.className = "gen-pop-unit";
      var span = document.createElement("span");
      span.textContent = POP_UNIT_LABEL[dim];
      var sel = document.createElement("select");
      Object.keys(POP_UNITS[dim]).forEach(function (u) {
        var o = document.createElement("option");
        o.value = u;
        o.textContent = u;
        sel.appendChild(o);
      });
      // Absolute power mode pins the amp unit to dBm — the amp cells ARE
      // absolute powers there, so the selector is shown locked.
      if (dim === "amp" && state.powerMode === "absolute") {
        sel.value = "dBm";
        sel.disabled = true;
        sel.title = "Absolute power mode: pulse powers are entered in dBm";
      } else {
        sel.value = state.populateUnits[dim];
      }
      sel.addEventListener("change", function () {
        // Commit any in-progress cell BEFORE renderPopulateTables() rebuilds every
        // cell from state — else a value typed but not yet blurred is lost on the
        // unit flip (same blur-race the step-nav flush guards).
        captureDomFields();
        state.populateUnits[dim] = sel.value;
        savePopulateUnits();
        renderPopulateTables();
        recomputeLOs();   // re-scale LO values + refresh cell colours / map
      });
      wrap.appendChild(span);
      wrap.appendChild(sel);
      host.appendChild(wrap);
    });

    // Power-input mode: "manual" = today's FSP + amplitude cells; "absolute" =
    // pulse powers typed in dBm, port FSP auto-allocated (lab policy: FSP
    // int [0..10] dBm preferred, amp 0.01–0.5, strongest pulse picks FSP).
    // Stored representation is IDENTICAL in both modes (fsp + amplitudes) —
    // the toggle only changes entry semantics, so specs/drafts stay portable.
    var pWrap = document.createElement("label");
    pWrap.className = "gen-pop-unit gen-pop-powermode";
    var pSpan = document.createElement("span");
    pSpan.textContent = "Power input";
    var pSel = document.createElement("select");
    [["manual", "FSP + amplitude"], ["absolute", "absolute dBm (auto FSP)"]]
      .forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt[0];
        o.textContent = opt[1];
        pSel.appendChild(o);
      });
    pSel.value = state.powerMode;
    pSel.title = "How pulse power is entered. Absolute: type each pulse's " +
      "output power in dBm; the port full-scale power is allocated " +
      "automatically (readout banks share one FSP with a multiplexing " +
      "amplitude budget).";
    pSel.addEventListener("change", function () {
      captureDomFields();   // same blur-race guard as the unit selectors
      state.powerMode = pSel.value === "absolute" ? "absolute" : "manual";
      savePowerMode();
      renderUnitToggles();      // re-render to lock/unlock the amp selector
      renderPopulateTables();
      recomputeLOs();
    });
    pWrap.appendChild(pSpan);
    pWrap.appendChild(pSel);
    host.appendChild(pWrap);
  }

  // -- step 6: MW-FEM LO auto-assignment -------------------------------
  // An MW-FEM has 5 LOs, each shared by a port pair (MW_LO_PAIRS). An LO can
  // up/down-convert RF only within ±0.4 GHz of itself — a 0.8 GHz IF window.
  // recomputeLOs() derives each LO from the RF_freq values the user enters,
  // writes it into every element on that LO's ports, and warns when one LO
  // cannot cover its port pair. bandOf() mirrors run_build.py's _band_for.
  function bandOf(freq) {
    freq = parseFloat(freq);
    if (isNaN(freq)) return null;
    if (freq >= 50e6 && freq < 5.5e9) return 1;
    if (freq >= 4.5e9 && freq < 7.5e9) return 2;
    if (freq >= 6.5e9 && freq <= 10.5e9) return 3;
    return null;
  }

  var MW_LO_PAIRS = [
    [[1, "output"], [1, "input"]],
    [[2, "output"], [3, "output"]],
    [[4, "output"], [5, "output"]],
    [[6, "output"], [7, "output"]],
    [[8, "output"], [2, "input"]]
  ];

  var LO_IF_HALF_WINDOW = 0.4e9;   // an LO covers RF within ±0.4 GHz

  // Readout demod floor: the MW-FEM cannot demodulate |IF| ≤ 5 MHz, and a
  // resonator that close to the LO responds to the LO-leakage dip itself.
  // The LO solver keeps every RESONATOR's |RF − LO| above the floor plus a
  // 1 MHz safety margin. xy drives are never demodulated, so they may sit at
  // IF = 0 (no hole). Mirrors spec_constraints.IF_FLOOR_MW_HZ.
  var LO_IF_HOLE_HZ = 5e6;
  var LO_IF_HOLE_MARGIN_HZ = 1e6;

  // RF coverage of each MW-FEM Nyquist band (mirrors bandOf's boundaries) and
  // the LO range that makes bandOf(lo) — and run_build's _band_for — actually
  // pick that band (bandOf checks band 1 → 2 → 3, so e.g. an LO below 5.5 GHz
  // always lands in band 1 even where band 2 overlaps).
  var BAND_RF_RANGES = { 1: [50e6, 5.5e9], 2: [4.5e9, 7.5e9], 3: [6.5e9, 10.5e9] };
  var BAND_LO_EFFECTIVE = { 1: [50e6, 5.5e9 - 1], 2: [5.5e9, 7.5e9 - 1], 3: [7.5e9, 10.5e9] };

  function rfInBand(rf, band) {
    if (band === 1) return rf >= 50e6 && rf < 5.5e9;
    if (band === 2) return rf >= 4.5e9 && rf < 7.5e9;
    if (band === 3) return rf >= 6.5e9 && rf <= 10.5e9;
    return false;
  }

  // -- step 6: inline as-you-type cell validation ----------------------
  // Layering rule: the INLINE layer flags per-cell, single-cell-derivable
  // facts IMMEDIATELY (debounced 'input' events); the conflict PANEL keeps
  // the cross-cell/port-level findings (LO solver + recomputeAllPowerFindings)
  // at blur/commit time. The inline validator is read-only — it never writes
  // _powerWarnings, panel entries, or the spec. The one deliberate overlap is
  // the feedline Σ|amp| > 1 clip (the customer wants it the moment it's
  // typed): short-form on the typed cell here, port-keyed panel entry on
  // commit.
  //
  // VALIDATE_RANGES mirrors core/diagnostics.py MW_OUTPUT_FREQ_RANGE_HZ /
  // MW_INPUT_FREQ_RANGE_HZ and core/spec_constraints.py
  // FULL_SCALE_POWER_DBM_RANGE — keep in sync (pinned by the JS↔Py parity
  // test in tests/test_generate_validation.py).
  var VALIDATE_DEBOUNCE_MS = 250;
  var VALIDATE_RANGES = { drive: [50e6, 10.5e9], readout: [2e9, 10.5e9], fsp: [-11, 18] };
  var _lastLoCalc = null;   // cached computeLoAssignments() result (recomputeLOs)

  // The LO group (from the last LO calc) that `group/rid` belongs to, or null.
  function loGroupOf(group, rid) {
    if (!_lastLoCalc) return null;
    var gid = _lastLoCalc.elementGroup[group + "/" + rid];
    if (gid == null) return null;
    for (var i = 0; i < _lastLoCalc.groups.length; i++) {
      if (_lastLoCalc.groups[i].id === gid) return _lastLoCalc.groups[i];
    }
    return null;
  }

  // Σ|amp| over `rid`'s readout feedline with `rid`'s own contribution
  // replaced by `ownAbs` (the value being typed). Null when unallocated.
  function feedlineAmpSum(rid, ownAbs) {
    var members = readoutBankMembers(rid);
    if (!members) return null;
    var res = (state.spec.populate || {}).resonator || {};
    var sum = ownAbs;
    members.forEach(function (m) {
      if (m.rid === rid) return;
      var a = Math.abs(parseFloat((res[m.rid] || {}).readout_amplitude));
      if (isFinite(a)) sum += a;
    });
    return sum;
  }

  // Validate ONE cell's BASE value (SI units / dimensionless amp — unit
  // conversion happens in the caller). Returns null when fine, else
  // { severity: "err" | "warn", message }. Pure derivation, no side effects.
  function validateCellValue(group, rid, col, base, raw) {
    function err(m) { return { severity: "err", message: m }; }
    function warn(m) { return { severity: "warn", message: m }; }
    if (isNaN(base)) return err('"' + raw + '" is not a number.');

    if (col.dim === "freq" && col.field === "RF_freq") {
      if (base <= 0) return err("Frequency must be positive.");
      if (group === "resonator") {
        if (base < VALIDATE_RANGES.readout[0] || base > VALIDATE_RANGES.readout[1]) {
          return err("RF " + fmtFreq(base) + " is outside the MW-FEM input range (2–10.5 GHz).");
        }
      } else if (base < VALIDATE_RANGES.drive[0] || base > VALIDATE_RANGES.drive[1]) {
        return err("RF " + fmtFreq(base) + " is outside the MW-FEM hardware reach (0.05–10.5 GHz).");
      }
      return null;
    }

    if (col.field === "LO_frequency") {
      if (base <= 0) return err("Frequency must be positive.");
      var b = bandOf(base);
      if (b == null) {
        return err("LO " + fmtFreq(base) + " is outside every MW-FEM Nyquist band (0.05–10.5 GHz).");
      }
      var g = loGroupOf(group, rid);
      if (g) {
        var pop = state.spec.populate || {};
        for (var i = 0; i < g.members.length; i++) {
          var m = g.members[i];
          var rf = parseFloat(((pop[m.group] || {})[m.rid] || {}).RF_freq);
          if (!isFinite(rf)) continue;
          if (Math.abs(rf - base) > LO_IF_HALF_WINDOW) {
            return err(m.rid + "'s RF " + fmtFreq(rf) +
              " is outside this LO's ±0.4 GHz IF window.");
          }
          if (!rfInBand(rf, b)) {
            return warn("LO band " + b + " does not cover " + m.rid +
              "'s RF " + fmtFreq(rf) + ".");
          }
          if (m.group === "resonator" && Math.abs(rf - base) <= LO_IF_HOLE_HZ) {
            return warn(m.rid + " sits within the 5 MHz demod hole of this LO" +
              " (|IF| ≤ 5 MHz is unreadable).");
          }
        }
      }
      return null;
    }

    if (col.dim === "amp") {
      if (state.powerMode === "absolute") {
        // The cell is an absolute dBm target; committing re-solves the FSP,
        // so the only single-cell fact is the hardware ceiling: amp 1 at
        // FSP 18 = +18 dBm is the port's maximum output.
        var dbm = parseFloat(window.NumberInput.strip(raw));
        if (isFinite(dbm) && dbm > PWR.FSP_MAX) {
          return err("+" + dbm + " dBm exceeds the port's maximum output (+" +
            PWR.FSP_MAX + " dBm at FSP " + PWR.FSP_MAX + ", amp 1).");
        }
        return null;   // bank re-solve is commit-time; the panel covers Σ
      }
      if (Math.abs(base) > 1 + 1e-9) {
        var mode = ampMode();
        if (mode === "dBm" || mode === "V_pk") {
          return err("Unreachable: needs |amp| " + Math.abs(base).toPrecision(3) +
            " > 1 at FSP " + fspForAmp(group, rid) + " dBm.");
        }
        return err("|amp| " + Math.abs(base).toPrecision(3) +
          " exceeds DAC full scale (amp is dimensionless in [-1, 1]).");
      }
      if (col.field === "readout_amplitude" && group === "resonator") {
        var sum = feedlineAmpSum(rid, Math.abs(base));
        if (sum != null && sum > PWR.SUM_MAX + 1e-9) {
          return err("Feedline Σ|amp| = " + sum.toFixed(2) +
            " > 1 — simultaneous readout tones will CLIP at the DAC.");
        }
      }
      return null;
    }

    if (col.field === "full_scale_power_dbm") {
      // Editable only in manual mode (absolute mode disables the cell).
      if (base < VALIDATE_RANGES.fsp[0] || base > VALIDATE_RANGES.fsp[1]) {
        return err("FSP " + base + " dBm is outside the MW-FEM range [" +
          VALIDATE_RANGES.fsp[0] + ", " + VALIDATE_RANGES.fsp[1] + "] dBm.");
      }
      if (base % 1 !== 0) {
        return warn("FSP uses an integer dB grid — " + base + " will not " +
          "round-trip exactly.");
      }
      return null;
    }

    if (col.dim === "freq" && base !== 0 && Math.abs(base) > 10.5e9) {
      // Generic frequency-dimension sanity (anharmonicity, detuning, IF):
      // nothing on an MW-FEM chip is legitimately beyond the hardware reach.
      return warn(fmtFreq(Math.abs(base)) + " is beyond the MW-FEM range — " +
        "check the unit (" + state.populateUnits.freq + ").");
    }
    return null;
  }

  // Decorate one cell from a finding (or clear it when null). Idempotent —
  // reuses the td's existing flag span, preserves the cell's original title.
  function setCellFlag(input, finding) {
    if (input.dataset.baseTitle == null) input.dataset.baseTitle = input.title || "";
    input.classList.toggle("gen-cell-err", !!finding && finding.severity === "err");
    input.classList.toggle("gen-cell-warn", !!finding && finding.severity === "warn");
    input.title = finding ? finding.message : input.dataset.baseTitle;
    var td = input.parentNode;
    if (!td) return;
    var flag = td.querySelector(".gen-cell-flag");
    if (!finding) {
      if (flag) flag.remove();
      return;
    }
    if (!flag) {
      flag = document.createElement("span");
      flag.textContent = "⚠";
      td.appendChild(flag);
    }
    flag.className = "gen-cell-flag " + finding.severity;
    flag.title = finding.message;
  }

  // Parse + validate one populate cell against its column, unit-aware: the
  // typed display value is converted to BASE first (15.3 typed in GHz mode
  // validates as 15.3e9). Selects and text cells are never flagged.
  function validateCellInline(input, group, rid, col) {
    if (!input.isConnected) return;   // table re-rendered before the timer fired
    if (!col || col.kind === "select" || col.kind === "text") return;
    var raw = input.value;
    if (String(raw == null ? "" : raw).trim() === "") {
      setCellFlag(input, null);
      return;
    }
    var base;
    if (col.dim === "amp") base = ampToBase(raw, ampMode(), fspForAmp(group, rid));
    else if (col.dim) base = toBaseValue(raw, col.dim);
    else base = parseFloat(window.NumberInput.strip(raw));
    setCellFlag(input, validateCellValue(group, rid, col, base, raw));
  }

  // Group -> its populate columns (pairs resolve per the active gate).
  function popColsOf(group) {
    if (group === "qubit") return POP_QUBIT_COLS;
    if (group === "resonator") return POP_RESONATOR_COLS;
    if (group === "flux") return POP_FLUX_COLS;
    if (group === "pulses") return POP_PULSE_FIELDS;
    if (group === "pairs") return pairPopCols();
    return [];
  }

  // Re-validate every populate cell (step entry, draft restore, unit toggle,
  // power-mode flip, LO rewrite, bulk fill). O(cells), pure reads — cheap.
  function validateAllPopCells() {
    document.querySelectorAll(".gen-pop-in[data-rid][data-field]")
      .forEach(function (input) {
        var group = input.dataset.group, field = input.dataset.field;
        var col = null;
        popColsOf(group).forEach(function (c) {
          if (c.field === field) col = c;
        });
        if (col) validateCellInline(input, group, input.dataset.rid, col);
      });
  }

  // Choose one LO for a port pair's elements. entries = [{rf, needHole}] —
  // needHole marks readout resonators (their |RF − LO| must clear the demod
  // floor). Feasibility, in constraint order:
  //   1. every |RF_i − LO| ≤ LO_IF_HALF_WINDOW      (±0.4 GHz IF window)
  //   2. LO sits where bandOf(LO) is a band that covers EVERY RF
  //   3. every resonator |RF_i − LO| > 5 MHz         (demod-floor hole)
  // Among feasible LOs, picks the one minimizing max|IF| (closest to the RF
  // midpoint; ties resolve to the HIGHER LO — negative IFs, the convention
  // the docs recommend and real CR chips use). Rounds to 1 MHz when the
  // rounded value stays feasible. On infeasibility returns the legacy
  // midpoint plus a `code` naming the first constraint that failed:
  //   "span"     RF spread exceeds the 0.8 GHz window
  //   "no_band"  no single Nyquist band covers every RF
  //   "band_window"  a band covers every RF but its LO range misses the window
  //   "hole"     every window position collides with a resonator's demod floor
  function solveLoWindow(entries) {
    var rfs = entries.map(function (e) { return e.rf; });
    var hi = Math.max.apply(null, rfs);
    var lo = Math.min.apply(null, rfs);
    var mid = (hi + lo) / 2;
    var out = { lo: mid, ok: false, code: null, span: hi - lo };
    if (hi - lo > 2 * LO_IF_HALF_WINDOW) { out.code = "span"; return out; }

    // Window interval intersected with each all-covering band's LO range.
    // Track the two failure causes separately: no band covers every RF
    // ("no_band"), vs. a covering band exists but its effective LO range
    // (bandOf's band-1-first precedence) misses the ±window ("band_window",
    // fixable by shifting the RFs, not by rewiring).
    var wLo = hi - LO_IF_HALF_WINDOW, wHi = lo + LO_IF_HALF_WINDOW;
    var intervals = [];
    var coveringBand = null;
    [1, 2, 3].forEach(function (b) {
      var covers = rfs.every(function (rf) { return rfInBand(rf, b); });
      if (!covers) return;
      if (coveringBand == null) coveringBand = b;
      var a = Math.max(wLo, BAND_LO_EFFECTIVE[b][0]);
      var z = Math.min(wHi, BAND_LO_EFFECTIVE[b][1]);
      if (a <= z) intervals.push([a, z]);
    });
    if (!intervals.length) {
      out.code = coveringBand == null ? "no_band" : "band_window";
      out.band = coveringBand;
      out.window = [wLo, wHi];
      return out;
    }

    // Subtract each resonator's demod-floor hole (radius 6 MHz = 5 MHz floor
    // + 1 MHz margin) from the allowed intervals. Standard interval minus
    // open hole: keep the piece left of the hole and the piece right of it
    // (a missed hole reconstructs the interval via whichever piece applies).
    var r = LO_IF_HOLE_HZ + LO_IF_HOLE_MARGIN_HZ;
    entries.forEach(function (e) {
      if (!e.needHole) return;
      var next = [];
      intervals.forEach(function (iv) {
        var a = iv[0], z = iv[1];
        if (e.rf - r >= a) next.push([a, Math.min(z, e.rf - r)]);
        if (e.rf + r <= z) next.push([Math.max(a, e.rf + r), z]);
      });
      intervals = next;
    });
    if (!intervals.length) { out.code = "hole"; return out; }

    // Feasible point closest to the midpoint = minimal max|IF|.
    var best = null;
    intervals.forEach(function (iv) {
      var c = Math.min(Math.max(mid, iv[0]), iv[1]);
      var d = Math.abs(c - mid);
      if (!best || d < best.d - 0.5 ||
          (Math.abs(d - best.d) <= 0.5 && c > best.c)) {
        best = { c: c, d: d };
      }
    });
    // Round to 1 MHz for readability when the rounded LO stays feasible.
    var snapped = Math.round(best.c / 1e6) * 1e6;
    var snapOk = intervals.some(function (iv) {
      return snapped >= iv[0] && snapped <= iv[1];
    });
    out.lo = snapOk ? snapped : best.c;
    out.ok = true;
    return out;
  }

  // Up to 12 distinct hues for LO-group colour-coding (see --lo-c* in CSS).
  // All are dark enough that white pill text reads on either theme.
  var LO_GROUP_PALETTE = [
    "--lo-c0", "--lo-c1", "--lo-c2", "--lo-c3", "--lo-c4", "--lo-c5",
    "--lo-c6", "--lo-c7", "--lo-c8", "--lo-c9", "--lo-c10", "--lo-c11"
  ];
  var _loColorIdx = {};   // groupId -> palette index, by first-appearance order

  // Claim a palette colour for each LO group, in first-appearance order.
  function assignGroupColors(groups) {
    _loColorIdx = {};
    groups.forEach(function (g, i) { _loColorIdx[g.id] = i; });
  }
  function groupColor(groupId) {
    var i = _loColorIdx[groupId];
    if (i == null) return null;
    return "var(" + LO_GROUP_PALETTE[i % LO_GROUP_PALETTE.length] + ")";
  }

  // Map each physical port to the elements allocated on it. Returns
  // { "con/slot/port/io": [ {group, rid, ch}, ... ] }, where group is the
  // populate group — "qubit" for xy drives, "resonator" for readout.
  function collectPortElements() {
    var portMap = {};
    function add(group, rid, ch) {
      if (!ch || ch.con == null || ch.slot == null || ch.port == null) return;
      var key = ch.con + "/" + ch.slot + "/" + ch.port + "/" +
                (ch.io_type || "output");
      (portMap[key] || (portMap[key] = [])).push(
        { group: group, rid: rid, ch: ch });
    }
    state.spec.qubits.forEach(function (q) {
      var a = (state.allocation || {})[q] || {};
      (a.xy || []).forEach(function (ch) { add("qubit", q, ch); });
      (a.rr || []).forEach(function (ch) { add("resonator", q, ch); });
    });
    return portMap;
  }

  // A frequency formatted for a warning line, in the active frequency unit.
  function fmtFreq(hz) {
    var u = state.populateUnits.freq;
    return parseFloat((hz / POP_UNITS.freq[u]).toPrecision(6)) + " " + u;
  }

  // The "Out2+Out3" / "Out8+In2" fragment naming an LO pair's two ports.
  function portPairDesc(pair) {
    return "Out" + pair[0][0] + "+" +
           (pair[1][1] === "input" ? "In" : "Out") + pair[1][0];
  }

  // Derive each MW-FEM LO from the RF_freq values on its port pair. Returns:
  //   assignments   {"group/rid": loHz}   — output-side LO frequency
  //   warnings      [{message, members:[{group,rid}]}]  — LO/band conflicts
  //   groups        [{id, con, slot, pairIdx, loLabel, portPairDesc,
  //                   members:[{group,rid}], loFreq, band}]  — occupied LOs
  //   elementGroup  {"group/rid": groupId} — output-side, for cell colouring
  function computeLoAssignments() {
    var result = { assignments: {}, warnings: [], groups: [], elementGroup: {} };
    if (!state.allocation) return result;
    var portMap = collectPortElements();
    var pop = state.spec.populate || {};
    var inputLo = {};   // "group/rid" -> LO derived from its input-side pair

    function rfOf(m) {
      var n = parseFloat(((pop[m.group] || {})[m.rid] || {}).RF_freq);
      return isNaN(n) ? null : n;
    }

    ((state.spec.instruments || {}).controllers || []).forEach(function (ctrl) {
      (ctrl.fems || []).forEach(function (fem) {
        if (fem.fem !== "mw") return;
        var pre = ctrl.con + "/" + fem.slot + "/";
        var femName = "con" + ctrl.con + " slot" + fem.slot;
        MW_LO_PAIRS.forEach(function (pair, idx) {
          var members = (portMap[pre + pair[0][0] + "/" + pair[0][1]] || [])
            .concat(portMap[pre + pair[1][0] + "/" + pair[1][1]] || []);
          // Keep each member paired with its RF; drop members with no RF.
          var withRf = [];
          members.forEach(function (m) {
            var rf = rfOf(m);
            if (rf != null) withRf.push({ m: m, rf: rf });
          });
          if (!withRf.length) return;
          // Hole-aware LO solve: resonators must clear the 5 MHz demod floor
          // (an IF that close to the LO is unreadable — the legacy midpoint
          // put a lone resonator's LO exactly ON its RF), xy drives may sit
          // anywhere in the window.
          var solved = solveLoWindow(withRf.map(function (x) {
            return { rf: x.rf, needHole: x.m.group === "resonator" };
          }));
          var rfs = withRf.map(function (x) { return x.rf; });
          var hi = Math.max.apply(null, rfs);
          var lo = Math.min.apply(null, rfs);
          var loFreq = solved.lo;
          var groupId = ctrl.con + "/" + fem.slot + "/" + idx;
          var loName = femName + " LO" + (idx + 1) +
            " (" + portPairDesc(pair) + ")";
          // Deduped {group,rid} of every member on this LO that has an RF.
          var seen = {}, groupMembers = [];
          withRf.forEach(function (x) {
            var key = x.m.group + "/" + x.m.rid;
            if (!seen[key]) {
              seen[key] = 1;
              groupMembers.push({ group: x.m.group, rid: x.m.rid });
            }
            if ((x.m.ch.io_type || "output") === "output") {
              result.assignments[key] = loFreq;
              result.elementGroup[key] = groupId;
            } else {
              inputLo[key] = loFreq;
            }
          });
          // Each conflict carries the members involved, so recomputeLOs() can
          // ring the offending ports in the wiring diagram.
          if (solved.code === "span") {
            result.warnings.push({
              message: loName + ": RF values span " + fmtFreq(hi - lo) +
                " — wider than the 0.8 GHz IF window, so one LO cannot cover " +
                "them. Move an element to another port pair.",
              members: groupMembers
            });
          } else if (solved.code === "no_band") {
            result.warnings.push({
              message: loName + ": no single MW-FEM band covers RF " +
                fmtFreq(lo) + "–" + fmtFreq(hi) +
                " (band 1: 0.05–5.5, band 2: 4.5–7.5, band 3: 6.5–10.5 GHz). " +
                "Move an element to another port pair.",
              members: groupMembers
            });
          } else if (solved.code === "band_window") {
            result.warnings.push({
              message: loName + ": band " + solved.band + " covers RF " +
                fmtFreq(lo) + "–" + fmtFreq(hi) + " but its LO range misses " +
                "the ±0.4 GHz IF window (" + fmtFreq(solved.window[0]) + "–" +
                fmtFreq(solved.window[1]) + ") — shift the RF values so the " +
                "window reaches band " + solved.band + "'s LO range, or move " +
                "an element to another port pair.",
              members: groupMembers
            });
          } else if (solved.code === "hole") {
            result.warnings.push({
              message: loName + ": every feasible LO lands within 5 MHz of " +
                "a resonator (unreadable — the MW-FEM cannot demodulate " +
                "|IF| ≤ 5 MHz). Adjust the resonator RF values.",
              members: groupMembers
            });
          }
          withRf.forEach(function (x) {
            if (bandOf(x.rf) == null) {
              result.warnings.push({
                message: loName + ": RF " + fmtFreq(x.rf) +
                  " is outside every MW-FEM Nyquist band (0.05–10.5 GHz).",
                members: [{ group: x.m.group, rid: x.m.rid }]
              });
            }
          });
          result.groups.push({
            id: groupId, con: ctrl.con, slot: fem.slot, pairIdx: idx,
            loLabel: "LO" + (idx + 1), portPairDesc: portPairDesc(pair),
            members: groupMembers, loFreq: loFreq, band: bandOf(loFreq)
          });
        });
      });
    });
    // Under the LO-safe layout a readout's output and input land on different
    // LO pairs; they should converge. Flag any hand-wired case where they don't.
    Object.keys(inputLo).forEach(function (key) {
      var out = result.assignments[key];
      if (out != null && Math.abs(out - inputLo[key]) > 1) {
        var cut = key.indexOf("/");
        result.warnings.push({
          message: key.replace("/", " ") + ": readout output LO " +
            fmtFreq(out) + " and input LO " + fmtFreq(inputLo[key]) +
            " differ — check the feedline wiring.",
          members: [{ group: key.slice(0, cut), rid: key.slice(cut + 1) }]
        });
      }
    });
    return result;
  }

  // Write computed LOs into the spec and refresh the live LO cells.
  function applyLoAssignments(assignments) {
    var pop = state.spec.populate;
    Object.keys(assignments).forEach(function (key) {
      var cut = key.indexOf("/");
      var group = key.slice(0, cut), rid = key.slice(cut + 1);
      pop[group] = pop[group] || {};
      pop[group][rid] = pop[group][rid] || {};
      pop[group][rid].LO_frequency = assignments[key];
    });
    document.querySelectorAll(
      '.gen-pop-in[data-field="LO_frequency"]').forEach(function (input) {
      var bucket = (pop[input.dataset.group] || {})[input.dataset.rid] || {};
      input.value = (bucket.LO_frequency == null)
        ? "" : toDisplayValue(bucket.LO_frequency, "freq");
    });
  }

  // -- step 6: LO-group visualisation ----------------------------------

  // Tint each LO_frequency cell by the physical LO its element belongs to,
  // with a small LO tag. Idempotent — reuses the cell's existing tag span,
  // so re-running on every RF edit never duplicates or leaks nodes.
  function decorateLoCells(calc) {
    var femSet = {};
    calc.groups.forEach(function (g) { femSet[g.con + "/" + g.slot] = 1; });
    var qualify = Object.keys(femSet).length > 1;   // cXsY·LON when 2+ FEMs
    var byId = {};
    calc.groups.forEach(function (g) { byId[g.id] = g; });

    document.querySelectorAll(
      '.gen-pop-in[data-field="LO_frequency"]').forEach(function (input) {
      var td = input.parentNode;
      if (!td) return;
      var tag = td.querySelector(".gen-lo-tag");
      var gid = calc.elementGroup[input.dataset.group + "/" + input.dataset.rid];
      var g = gid != null ? byId[gid] : null;
      if (g) {
        var color = groupColor(g.id);
        td.classList.add("gen-lo-cell");
        td.style.boxShadow = "inset 3px 0 0 " + color;
        if (!tag) {
          tag = document.createElement("span");
          tag.className = "gen-lo-tag";
          td.appendChild(tag);
        }
        tag.textContent = qualify
          ? ("c" + g.con + "s" + g.slot + "·" + g.loLabel) : g.loLabel;
        tag.style.background = color;
      } else {
        td.classList.remove("gen-lo-cell");
        td.style.boxShadow = "";
        if (tag) tag.remove();
      }
    });
  }

  // Persist the LO-map panel's open state (mirrors the populate-units pattern).
  function loadLoMapOpen() {
    // Open by default (customer feedback: SM shows its reference panels; a
    // user's explicit collapse — stored "0" — is remembered).
    try { return localStorage.getItem("quam_lo_map_open") !== "0"; }
    catch (e) { return true; }
  }
  function saveLoMapOpen(open) {
    try { localStorage.setItem("quam_lo_map_open", open ? "1" : "0"); }
    catch (e) { /* localStorage unavailable */ }
  }

  // Render the collapsible LO-map panel — grouped per MW-FEM, occupied LOs
  // only, so it stays compact even with many FEMs.
  function renderLoMap(calc) {
    var details = document.getElementById("gen-lo-map");
    if (!details) return;
    var summary = details.querySelector(".gen-lo-map-summary");
    var body = details.querySelector(".gen-lo-map-body");
    if (!summary || !body) return;
    body.innerHTML = "";
    if (!calc.groups.length) {
      details.hidden = true;
      return;
    }
    details.hidden = false;
    var femSet = {};
    calc.groups.forEach(function (g) { femSet[g.con + "/" + g.slot] = 1; });
    var n = calc.groups.length, m = Object.keys(femSet).length;
    summary.textContent = "LO map — " + n + " LO group" + (n === 1 ? "" : "s") +
      ", " + m + " MW-FEM" + (m === 1 ? "" : "s");

    var lastFem = null;
    calc.groups.forEach(function (g) {
      var femKey = "con" + g.con + " · slot" + g.slot;
      if (femKey !== lastFem) {
        lastFem = femKey;
        var h = document.createElement("div");
        h.className = "gen-lo-map-fem";
        h.textContent = femKey + " (MW-FEM)";
        body.appendChild(h);
      }
      var row = document.createElement("div");
      row.className = "gen-lo-row";
      var sw = document.createElement("span");
      sw.className = "gen-lo-swatch";
      sw.style.background = groupColor(g.id);
      row.appendChild(sw);
      var tag = document.createElement("span");
      tag.className = "gen-lo-row-tag";
      tag.textContent = g.loLabel + " " + g.portPairDesc;
      row.appendChild(tag);
      var who = document.createElement("span");
      who.className = "gen-lo-row-who";
      who.textContent = g.members.map(function (mem) {
        return mem.rid + (mem.group === "resonator" ? ".rr" : "");
      }).join(", ") || "—";
      row.appendChild(who);
      var freq = document.createElement("span");
      freq.className = "gen-lo-row-freq";
      freq.textContent = fmtFreq(g.loFreq) +
        (g.band ? " · band " + g.band : "");
      row.appendChild(freq);
      body.appendChild(row);
    });
  }

  // Render the LO/band-conflict panel and ring the offending ports amber in
  // the step-6 wiring diagram. Hovering a warning line emphasises just that
  // warning's ports; an empty list clears every ring (idempotent).
  function renderConflicts(warnings, hasPower) {
    var host = document.getElementById("gen-band-warnings");
    if (!host) return;
    host.innerHTML = "";
    var all = [];
    if (warnings && warnings.length) {
      var panel = document.createElement("div");
      panel.className = "gen-band-warn";
      var head = document.createElement("strong");
      head.textContent = hasPower
        ? "⚠ LO / band / power conflicts" : "⚠ LO / band conflicts";
      panel.appendChild(head);
      warnings.forEach(function (w) {
        var members = w.members || [];
        members.forEach(function (m) { all.push(m); });
        var line = document.createElement("div");
        line.className = "gen-band-warn-line";
        line.textContent = "• " + w.message;
        line.addEventListener("mouseenter", function () {
          markConflictPorts(members, "iw-port-conflict-focus");
        });
        line.addEventListener("mouseleave", function () {
          markConflictPorts([], "iw-port-conflict-focus");
        });
        panel.appendChild(line);
      });
      host.appendChild(panel);
    }
    markConflictPorts(all, "iw-port-conflict");
  }

  // Recompute + apply the MW-FEM LOs, colour the LO cells and the LO-map
  // panel, then render the conflict panel + diagram rings.
  // computeLoAssignments() returns an empty result with no allocation yet.
  function recomputeLOs() {
    var calc = computeLoAssignments();
    applyLoAssignments(calc.assignments);
    assignGroupColors(calc.groups);
    decorateLoCells(calc);
    decorateReadoutFSPCells(calc);
    renderLoMap(calc);
    _lastLoWarnings = calc.warnings;
    _lastLoCalc = calc;            // inline LO-cell validation reads the groups
    recomputeAllPowerFindings();   // derive power warnings fresh from spec
    renderAllConflicts();
    // LO rewrites repaint LO cells — keep the inline flags in step.
    validateAllPopCells();
    // Bands changed → refresh the per-qubit LF-FEM delay summary.
    renderFluxDelaySummary((state.spec && state.spec.qubits) || []);
  }

  // -- step 6: shared-feedline readout FSP sync ------------------------
  //
  // Multiplexed readout puts every qubit on one MW-FEM port, so they must
  // share one `full_scale_power_dbm` (last write wins on the physical port).
  // When the user edits one qubit's FSP cell, propagate the value to every
  // other resonator on the same port and refresh those cells.
  // The physical readout OUTPUT port key ("con/slot/port/output") a resonator
  // is allocated on, or null when the allocation doesn't cover it. This key
  // IS the multiplexed-bank identity — every resonator sharing it shares one
  // FSP, one summed-amplitude budget, one LO.
  function readoutPortKey(rid) {
    var alloc = (state.allocation || {})[rid];
    if (!alloc || !alloc.rr) return null;
    var outPort = alloc.rr.find(function (p) {
      return (p.io_type || "output") === "output";
    });
    if (!outPort || outPort.con == null) return null;
    return outPort.con + "/" + outPort.slot + "/" + outPort.port + "/output";
  }

  // The resonators multiplexed on `rid`'s readout output port (including
  // `rid` itself), or null when the allocation doesn't cover it.
  function readoutBankMembers(rid) {
    var portKey = readoutPortKey(rid);
    if (portKey == null) return null;
    var members = (collectPortElements()[portKey] || []).filter(function (m) {
      return m.group === "resonator";
    });
    return members.length ? members : null;
  }

  function recomputeReadoutFSP(editedRid) {
    var members = readoutBankMembers(editedRid);
    if (!members || members.length <= 1) return;

    var pop = state.spec.populate;
    var src = ((pop.resonator || {})[editedRid] || {}).full_scale_power_dbm;
    pop.resonator = pop.resonator || {};
    members.forEach(function (m) {
      if (m.rid === editedRid) return;
      pop.resonator[m.rid] = pop.resonator[m.rid] || {};
      if (src == null) {
        delete pop.resonator[m.rid].full_scale_power_dbm;
        if (Object.keys(pop.resonator[m.rid]).length === 0) {
          delete pop.resonator[m.rid];
        }
      } else {
        pop.resonator[m.rid].full_scale_power_dbm = src;
      }
    });
    refreshFSPCells();
  }

  // Refresh all FSP <input>s of one populate group ("resonator" default) in
  // place from the current spec.
  function refreshFSPCells(group) {
    group = group || "resonator";
    var pop = state.spec.populate || {};
    document.querySelectorAll(
      '.gen-pop-in[data-field="full_scale_power_dbm"]' +
      '[data-group="' + group + '"]').forEach(function (input) {
      if (input.dataset.dirty === "1") return;   // preserve an uncommitted typed value
      var bucket = (pop[group] || {})[input.dataset.rid] || {};
      input.value = (bucket.full_scale_power_dbm == null)
        ? "" : bucket.full_scale_power_dbm;
    });
  }

  // -- step 6: absolute power (dBm) allocation --------------------------
  //
  // Splits an absolute output power P (dBm at the MW-FEM port into 50 Ω)
  // into the port's full_scale_power_dbm (FSP) + a pulse amplitude via
  //   P = FSP + 20·log10(amp).
  // Lab policy (the port's strongest pulse picks the FSP; the amplitude is
  // the fine knob):
  //   • amp is happiest in [0.01, 0.5] — large enough to use the DAC range,
  //     small enough to leave headroom;
  //   • FSP is happiest as an integer in [0, 10] dBm — pick the LOWEST that
  //     keeps the strongest amp ≤ 0.5 (i.e. ceil(P + 6.02), floored at 0);
  //   • a very quiet pulse never drags the FSP below 0 — accept amp < 0.01
  //     and warn (DAC resolution suffers);
  //   • a very loud pulse pushes the FSP past 10 up to the hardware max 18;
  //     beyond that amp rises toward 1 (amp > 1 is unreachable → warn).
  // A multiplexed readout bank of n tones uses the same rule with the
  // worst-case coherent sum in place of the single amp: n·amp ≤ 0.5
  // preferred (the tones sum at the DAC in real time; past full scale the
  // output clips). Stored representation is unchanged — fsp + amplitudes —
  // so the dBm target is losslessly recoverable as fsp + 20·log10(amp).
  var PWR = {
    FSP_MIN: -11, FSP_MAX: 18, FSP_PREF_MIN: 0, FSP_PREF_MAX: 10,
    AMP_MIN: 0.01, AMP_PREF_MAX: 0.5, SUM_PREF: 0.5, SUM_MAX: 1.0
  };

  // The FSP (integer dBm) for a port whose strongest pulse targets
  // `strongestDbm`, with `nTones` simultaneous tones (1 for an xy drive).
  function solvePortFsp(strongestDbm, nTones) {
    if (!isFinite(strongestDbm)) return PWR.FSP_PREF_MIN;   // no NaN through the clamps
    var n = Math.max(1, nTones || 1);
    // amp (or n·amp) ≤ 0.5  ⇔  fsp ≥ P + 20·log10(2n). The 1e-9 keeps an
    // exactly-integer boundary from ceiling one dB too high.
    var fsp = Math.ceil(strongestDbm + 20 * Math.log10(2 * n) - 1e-9);
    if (fsp < PWR.FSP_PREF_MIN) fsp = PWR.FSP_PREF_MIN;
    if (fsp > PWR.FSP_MAX) fsp = PWR.FSP_MAX;
    return fsp;
  }

  // The amplitude that realizes `dbm` under a chosen FSP.
  function ampForTarget(dbm, fsp) {
    return Math.pow(10, (dbm - fsp) / 20);
  }

  // Absolute-mode power warnings, DERIVED FRESH from the spec on every render
  // (recomputeAllPowerFindings), exactly like the LO warnings — so they are
  // never stale: they self-clear on a mode flip to manual, prune with a
  // deleted/renumbered qubit, key by physical port (not the edited row), and
  // reappear after a draft restore. `_lastLoWarnings` is the sibling LO cache.
  var _powerWarnings = {};        // portKey -> [warning]
  var _lastLoWarnings = [];

  function powerWarningList() {
    var all = [];
    Object.keys(_powerWarnings).forEach(function (k) {
      _powerWarnings[k].forEach(function (w) { all.push(w); });
    });
    return all;
  }

  function renderAllConflicts() {
    var power = powerWarningList();
    renderConflicts(_lastLoWarnings.concat(power), power.length > 0);
  }

  // Amp-side findings for one port, derived from the STORED (fsp, amp) values.
  // `tones` = [{rid, group, field, amp}] — amp is the stored, DAC-clamped
  // (≤1) value; the dBm shown is fsp + 20·log10(amp). `sumBudget` is true for
  // a multiplexed readout bank (its tones sum coherently at the DAC).
  // `sumOnly` (manual power mode) keeps just the physical Σ>1 CLIP check —
  // the per-tone and headroom findings are absolute-mode allocation policy,
  // but a feedline whose amplitudes sum past full scale clips REGARDLESS of
  // how the user typed them (customer requirement: warn in both modes).
  function powerFindings(portDesc, fsp, tones, sumBudget, sumOnly) {
    var warns = [];
    var members = [];
    var seen = {};
    tones.forEach(function (t) {
      if (!seen[t.rid + "/" + t.group]) {
        seen[t.rid + "/" + t.group] = 1;
        members.push({ group: t.group, rid: t.rid });
      }
    });
    function pulseName(t) { return t.rid + " " + t.field.replace("_amplitude", ""); }
    if (sumOnly) {
      if (sumBudget) {
        var so = tones.reduce(function (s, t) { return s + Math.abs(t.amp); }, 0);
        if (so > PWR.SUM_MAX) {
          warns.push({
            message: portDesc + ": multiplexed amplitudes sum to " +
              so.toFixed(2) + " > 1 — simultaneous tones will CLIP at the " +
              "DAC even at FSP " + fsp + " dBm. Lower the per-tone power.",
            members: members
          });
        }
      }
      return warns;
    }
    tones.forEach(function (t) {
      if (t.amp >= 1 - 1e-6 && fsp >= PWR.FSP_MAX) {
        // At the hardware ceiling: amp is pinned at full scale AND the FSP is
        // already at its maximum, so any higher target was unreachable and
        // got clamped here.
        warns.push({
          message: portDesc + ": " + pulseName(t) +
            " is at the port's maximum output (FSP " + PWR.FSP_MAX +
            " dBm, amp 1 = +" + PWR.FSP_MAX + " dBm) — a higher target is " +
            "unreachable on this port.",
          members: members
        });
      } else if (t.amp < PWR.AMP_MIN) {
        var why = fsp > PWR.FSP_PREF_MIN
          ? "(the port's strongest pulse pins FSP at " + fsp + " dBm — this " +
            "pulse sits " + Math.round(fsp - (fsp + 20 * Math.log10(t.amp))) +
            " dB below it)"
          : "(FSP already floored at " + PWR.FSP_PREF_MIN +
            " dBm by the port's strongest pulse)";
        warns.push({
          message: portDesc + ": " + pulseName(t) + " amplitude " +
            t.amp.toPrecision(3) + " < 0.01 — the DAC uses a sliver of its " +
            "range " + why + ".",
          members: members
        });
      }
    });
    if (sumBudget) {
      var sum = tones.reduce(function (s, t) { return s + Math.abs(t.amp); }, 0);
      if (sum > PWR.SUM_MAX) {
        warns.push({
          message: portDesc + ": multiplexed amplitudes sum to " +
            sum.toFixed(2) + " > 1 — simultaneous tones will CLIP at the " +
            "DAC even at FSP " + fsp + " dBm. Lower the per-tone power.",
          members: members
        });
      } else if (sum > PWR.SUM_PREF) {
        warns.push({
          message: portDesc + ": multiplexed amplitudes sum to " +
            sum.toFixed(2) + " (> 0.5 headroom budget) — worst-case " +
            "coherent sum leaves little margin.",
          members: members
        });
      }
    }
    return warns;
  }

  // Rebuild _powerWarnings from scratch off the current spec + allocation.
  // Called from recomputeLOs (every step-6 entry / RF edit / mode flip), so
  // the panel always reflects live state. No spec mutation — pure derivation.
  function recomputeAllPowerFindings() {
    _powerWarnings = {};
    // Manual mode still runs the readout-bank sweep with sumOnly=true: the
    // Σ|amp| > 1 feedline clip is a physical DAC fact, not an allocation
    // choice, so it must warn no matter how the amplitudes were typed.
    var absolute = state.powerMode === "absolute";
    var pop = state.spec.populate || {};
    var qubits = state.spec.qubits || [];

    // xy drive ports — one per qubit; both pulses share the qubit FSP.
    // Absolute-mode only: single-tone ports can't clip by summation, and the
    // sliver/at-max findings describe the auto-allocation.
    if (absolute) qubits.forEach(function (rid) {
      var pl = (pop.pulses || {})[rid] || {};
      var fsp = fspForAmp("pulses", rid);
      var tones = [];
      ["x180_amplitude", "saturation_amplitude"].forEach(function (f) {
        var a = Math.abs(parseFloat(pl[f]));
        if (isFinite(a) && a > 0) {
          tones.push({ rid: rid, group: "qubit", field: f, amp: a });
        }
      });
      if (!tones.length) return;
      var w = powerFindings("Drive port (" + rid + ")", fsp, tones, false);
      if (w.length) _powerWarnings["xy/" + rid] = w;
    });

    // Readout banks — group resonators by their PHYSICAL output port so a
    // bank's findings live under one key (fixing the edited-row keying).
    var banks = {};   // portKey -> [rid]
    qubits.forEach(function (rid) {
      var r = (pop.resonator || {})[rid] || {};
      if (!(isFinite(parseFloat(r.readout_amplitude)) &&
            parseFloat(r.readout_amplitude) > 0)) return;
      var key = readoutPortKey(rid);
      if (key == null) {
        // Not allocated yet — solved single-tone, so the multiplex budget is
        // unchecked. Surface it rather than silently under-warning.
        // (Absolute-mode note: in manual mode nothing was "solved", and with
        // no port grouping there is no sum to check either.)
        if (absolute) _powerWarnings["rr/unalloc/" + rid] = [{
          message: "Readout port for " + rid + " not allocated yet — power " +
            "solved single-tone; run Auto-allocate (step 5) to solve the " +
            "multiplexed bank.",
          members: [{ group: "resonator", rid: rid }]
        }];
        return;
      }
      (banks[key] || (banks[key] = [])).push(rid);
    });
    Object.keys(banks).forEach(function (key) {
      var rids = banks[key];
      var fsp = fspForAmp("resonator", rids[0]);   // synced across the bank
      var tones = rids.map(function (rid) {
        return { rid: rid, group: "resonator", field: "readout_amplitude",
                 amp: Math.abs(parseFloat(pop.resonator[rid].readout_amplitude)) };
      });
      var w = powerFindings("Readout bank (" + rids.join(", ") + ")",
                            fsp, tones, true, !absolute);
      if (w.length) _powerWarnings["rr/" + key] = w;
    });
  }

  // Absolute power mode: a readout power edit is a BANK edit — every resonator
  // on the feedline gets the edited per-tone dBm, one FSP is chosen under the
  // multiplexed sum budget, and every member's amplitude follows. The target
  // is read back from the edited row's stored (fsp, amp) pair (written by the
  // change handler). Pure spec mutation; warnings render via recomputeLOs.
  function recomputeReadoutPower(editedRid) {
    var pop = state.spec.populate;
    var bucket = ((pop.resonator || {})[editedRid]) || {};
    var amp = Math.abs(parseFloat(bucket.readout_amplitude));
    if (!isFinite(amp) || amp <= 0) { recomputeLOs(); return; }
    var target = fspForAmp("resonator", editedRid) + 20 * Math.log10(amp);
    var members = readoutBankMembers(editedRid) ||
      [{ group: "resonator", rid: editedRid }];
    var fsp = solvePortFsp(target, members.length);
    var clamped = Math.min(ampForTarget(target, fsp), 1);
    pop.resonator = pop.resonator || {};
    members.forEach(function (m) {
      pop.resonator[m.rid] = pop.resonator[m.rid] || {};
      pop.resonator[m.rid].full_scale_power_dbm = fsp;
      pop.resonator[m.rid].readout_amplitude = clamped;
    });
    refreshFSPCells("resonator");
    refreshAmpCells();
    recomputeLOs();   // re-decorate FSP cells + derive+render power findings
  }

  // Absolute power mode: an xy pulse-power edit re-solves the qubit's drive
  // port FSP from BOTH pulse targets (x180 + saturation; x90 derives from
  // x180 downstream), then rewrites both amplitudes so each pulse's absolute
  // dBm is preserved under the new FSP. Targets recovered from the stored
  // (oldFsp, |amp|) pairs — sign is preserved on write-back. Pure mutation.
  function recomputeXyPower(rid) {
    var pop = state.spec.populate;
    var pl = ((pop.pulses || {})[rid]) || {};
    var oldFsp = fspForAmp("pulses", rid);
    var targets = [];
    ["x180_amplitude", "saturation_amplitude"].forEach(function (f) {
      var raw = parseFloat(pl[f]);
      var a = Math.abs(raw);
      if (isFinite(a) && a > 0) {
        targets.push({ field: f, sign: raw < 0 ? -1 : 1,
                       dbm: oldFsp + 20 * Math.log10(a) });
      }
    });
    if (!targets.length) { recomputeLOs(); return; }
    var strongest = Math.max.apply(null, targets.map(function (t) {
      return t.dbm;
    }));
    var fsp = solvePortFsp(strongest, 1);
    pop.qubit = pop.qubit || {};
    pop.qubit[rid] = pop.qubit[rid] || {};
    pop.qubit[rid].full_scale_power_dbm = fsp;
    targets.forEach(function (t) {
      pl[t.field] = t.sign * Math.min(ampForTarget(t.dbm, fsp), 1);
    });
    refreshFSPCells("qubit");
    refreshAmpCells();
    recomputeLOs();
  }

  // On step-6 entry, converge any readout bank whose members carry DIVERGENT
  // FSPs — the tell-tale of pre-allocation single-tone solves (edits made
  // while state.allocation was null). Collapse each to its strongest member's
  // target, exactly as an edit of that row would (bank shares one dBm). Runs
  // only in absolute mode with an allocation present; bounded to one recompute
  // per divergent bank.
  function reconcileReadoutBanks() {
    if (state.powerMode !== "absolute" || !state.allocation) return;
    var pop = state.spec.populate || {};
    var banks = {};
    (state.spec.qubits || []).forEach(function (rid) {
      var r = (pop.resonator || {})[rid] || {};
      if (!isFinite(parseFloat(r.readout_amplitude))) return;
      var key = readoutPortKey(rid);
      if (key != null) (banks[key] || (banks[key] = [])).push(rid);
    });
    Object.keys(banks).forEach(function (key) {
      var rids = banks[key];
      if (rids.length <= 1) return;
      var fsps = {};
      rids.forEach(function (rid) { fsps[fspForAmp("resonator", rid)] = 1; });
      if (Object.keys(fsps).length <= 1) return;   // already coherent
      var strongest = rids[0], best = -Infinity;
      rids.forEach(function (rid) {
        var t = fspForAmp("resonator", rid) +
          20 * Math.log10(Math.abs(parseFloat(pop.resonator[rid].readout_amplitude)));
        if (t > best) { best = t; strongest = rid; }
      });
      recomputeReadoutPower(strongest);
    });
  }

  // Colour each resonator FSP cell by its feedline (= LO) group, mirroring
  // decorateLoCells — same palette, same group, same tag style — so users
  // can see at a glance which qubits' FSPs auto-sync together.
  function decorateReadoutFSPCells(calc) {
    var femSet = {};
    calc.groups.forEach(function (g) { femSet[g.con + "/" + g.slot] = 1; });
    var qualify = Object.keys(femSet).length > 1;
    var byId = {};
    calc.groups.forEach(function (g) { byId[g.id] = g; });
    document.querySelectorAll(
      '.gen-pop-in[data-field="full_scale_power_dbm"]' +
      '[data-group="resonator"]').forEach(function (input) {
      var td = input.parentNode;
      if (!td) return;
      var tag = td.querySelector(".gen-lo-tag");
      var gid = calc.elementGroup["resonator/" + input.dataset.rid];
      var g = gid != null ? byId[gid] : null;
      if (g) {
        var color = groupColor(g.id);
        td.classList.add("gen-lo-cell");
        td.style.boxShadow = "inset 3px 0 0 " + color;
        if (!tag) {
          tag = document.createElement("span");
          tag.className = "gen-lo-tag";
          td.appendChild(tag);
        }
        tag.textContent = qualify
          ? ("c" + g.con + "s" + g.slot + "·" + g.loLabel) : g.loLabel;
        tag.style.background = color;
      } else {
        td.classList.remove("gen-lo-cell");
        td.style.boxShadow = "";
        if (tag) tag.remove();
      }
    });
  }

  // -- step 6: populate tables -----------------------------------------

  // Build the four populate tables + the pulses form from state.spec.populate.
  // Called on entering step 6 and after any unit-toggle change.
  // -- step 6: read-only wiring diagram --------------------------------
  // A reference copy of the step-5 diagram. Hovering a port shows that
  // qubit's typed Populate values; rows and ports cross-highlight.

  var _popHoverState = null;   // last hovered assignment, or null

  // The step-6 diagram port-circle <g>s for one populate element, filtered to
  // the roles that element owns (xy for a qubit row, rr/rr_in for resonator…).
  function portCirclesFor(group, rid) {
    var roles = POP_GROUP_ROLES[group];
    var host = document.getElementById("gen-pop-wiring-diagram");
    if (!roles || !host) return [];
    return Array.prototype.slice.call(
      host.querySelectorAll('.iw-port-circle[data-element="' + rid + '"]'))
      .filter(function (g) {
        return roles.indexOf(g.getAttribute("data-role")) >= 0;
      });
  }

  // Ring the diagram port circle(s) that carry one populate-table row.
  function highlightPorts(group, rid, on) {
    portCirclesFor(group, rid).forEach(function (g) {
      g.classList.toggle("iw-port-linked", on);
    });
  }

  // Apply class `cls` to the diagram port circles of a set of conflict
  // members, clearing it from every other circle first (idempotent).
  function markConflictPorts(members, cls) {
    var host = document.getElementById("gen-pop-wiring-diagram");
    if (!host) return;
    host.querySelectorAll(".iw-port-circle." + cls).forEach(function (g) {
      g.classList.remove(cls);
    });
    (members || []).forEach(function (m) {
      portCirclesFor(m.group, m.rid).forEach(function (g) {
        g.classList.add(cls);
      });
    });
  }

  // Highlight the populate-table row for a hovered diagram port.
  function highlightPopRow(assignment) {
    document.querySelectorAll("tr.gen-pop-row-linked").forEach(function (tr) {
      tr.classList.remove("gen-pop-row-linked");
    });
    if (!assignment) return;
    var rg = POP_ROLE_GROUP[assignment.role];
    if (!rg) return;
    var tr = document.querySelector('tr[data-group="' + rg.group +
      '"][data-rid="' + assignment.element + '"]');
    if (tr) tr.classList.add("gen-pop-row-linked");
  }

  // Fill the Populate wiring monitor with a hovered port's live values.
  function renderPopMonitor(assignment) {
    var host = document.getElementById("gen-pop-wiring-monitor");
    if (!host) return;
    host.innerHTML = "";
    function span(cls, text) {
      var s = document.createElement("span");
      s.className = cls;
      s.textContent = text;
      return s;
    }
    if (!assignment) {
      host.appendChild(span("gen-monitor-idle",
        "Hover a port or table row to see its values"));
      return;
    }
    host.appendChild(span("gen-monitor-label",
      assignment.label || assignment.element || ""));
    var role = assignment.role || "";
    host.appendChild(span("role-badge " + role, role.toUpperCase()));
    var rg = POP_ROLE_GROUP[role];
    var bucket = rg
      ? ((state.spec.populate[rg.group] || {})[assignment.element] || {})
      : {};
    var shown = 0;
    if (rg) {
      rg.cols.forEach(function (col) {
        var v = bucket[col.field];
        if (v == null || v === "") return;
        shown++;
        host.appendChild(span("gen-monitor-fields", colHeader(col) + ": " +
          (col.dim ? toDisplayValue(v, col.dim) : v)));
      });
    }
    if (!shown) {
      host.appendChild(span("gen-monitor-idle", "no values entered yet"));
    }
  }

  // onPortHover callback for the read-only Populate diagram.
  function setPopHover(assignment) {
    _popHoverState = assignment;
    renderPopMonitor(assignment);
    highlightPopRow(assignment);
  }

  function loadPopWiringOpen() {
    // Open by default (customer feedback) — explicit collapse remembered.
    try { return localStorage.getItem("quam_pop_wiring_open") !== "0"; }
    catch (e) { return true; }
  }
  function savePopWiringOpen(open) {
    try { localStorage.setItem("quam_pop_wiring_open", open ? "1" : "0"); }
    catch (e) { /* localStorage unavailable */ }
  }

  function loadPopTopoOpen() {
    // Open by default (both Generate + Re-generate); only a user's explicit
    // collapse ("0") keeps it shut.
    try { return localStorage.getItem("quam_pop_topo_open") !== "0"; }
    catch (e) { return true; }
  }
  function savePopTopoOpen(open) {
    try { localStorage.setItem("quam_pop_topo_open", open ? "1" : "0"); }
    catch (e) { /* localStorage unavailable */ }
  }

  // Read-only mirror of the step-4 chip board for the Populate step (toggleable).
  // Reuses the SHARED TopoGraph convention so board ↔ populate ↔ Chip Status agree
  // on placement + gate edge-styling. Purely a reference view — placement/pairs
  // stay editable only on the step-4 board.
  // Build the [{id, grid_location}] node list fresh from the CURRENT spec — never
  // close over a stale snapshot (a delete on step 4 then a collapse/reopen on step 6
  // would otherwise redraw the old topology).
  function popTopoNodes() {
    var popq = (state.spec.populate && state.spec.populate.qubit) || {};
    return state.spec.qubits.map(function (q) {
      return { id: q, grid_location: (popq[q] || {}).grid_location };
    });
  }

  function renderPopTopo() {
    var details = document.getElementById("gen-pop-topo");
    if (!details) return;
    var sp = state.spec;
    var nodes = popTopoNodes();
    var placed = nodes.filter(function (n) { return n.grid_location != null; }).length;
    // No topology to mirror (nothing placed on the board) → hide the panel entirely.
    if (!placed) { details.hidden = true; return; }
    details.hidden = false;
    details.open = loadPopTopoOpen();
    if (!details.dataset.bound) {
      details.dataset.bound = "1";
      details.addEventListener("toggle", function () {
        savePopTopoOpen(details.open);
        if (details.open) renderPopTopoBoard();   // re-derive nodes on open (no stale closure)
      });
    }
    var cap = document.getElementById("gen-pop-topo-caption");
    if (cap) cap.textContent = placed + "/" + sp.qubits.length + " placed · " + sp.qubit_pairs.length + " pairs";
    var leg = document.getElementById("gen-pop-topo-legend");
    if (leg && window.TopoGraph) leg.textContent = window.TopoGraph.legendForGate(state.pairGate);
    if (details.open) renderPopTopoBoard();
  }

  function renderPopTopoBoard() {
    var mount = document.getElementById("gen-pop-topo-board");
    if (!mount || !window.TopoGraph) return;
    window.TopoGraph.renderStatic(mount, {
      qubits: popTopoNodes(),
      pairs: state.spec.qubit_pairs,
      gate: state.pairGate,
    });
  }

  // Render the read-only wiring diagram for the Populate step. No drag —
  // re-wiring stays in step 5; this is purely a reference view.
  function renderPopWiring() {
    var details = document.getElementById("gen-pop-wiring");
    var host = document.getElementById("gen-pop-wiring-diagram");
    if (!details || !host) return;
    _popHoverState = null;   // a re-render invalidates any hovered port
    renderPopMonitor(null);
    if (!state.allocation || !window.renderInstrumentWiring) {
      details.hidden = true;
      return;
    }
    details.hidden = false;
    renderInstrumentWiring("gen-pop-wiring-diagram",
                           buildInstrumentData(state.allocation), {},
                           { onPortHover: setPopHover });
    // Ring the ports involved in any wiring error — same as step 5.
    validateWiring().forEach(function (it) {
      if (it.level !== "error") return;
      (it.ports || []).forEach(function (p) {
        host.querySelectorAll('.iw-port[data-con="' + p.con + '"][data-slot="' +
          p.slot + '"][data-port="' + p.port + '"][data-io="' +
          (p.io_type || "output") + '"]').forEach(function (cell) {
          cell.classList.add("iw-port-invalid");
        });
      });
    });
  }

  function renderPopulateTables() {
    reconcilePopulatePairs();   // drop populate for removed pairs / wrong-gate fields
    czAutoOrient();             // draft restore / step re-entry: order may be stale
    var qubits = state.spec.qubits;
    var pairs = state.spec.qubit_pairs
      .filter(function (p) { return p[0] && p[1]; })
      .map(function (p) { return p[0] + "-" + p[1]; });
    var noQubits = "Define qubits in step 4 first.";

    // Derive which line types are active (same logic as deriveLines).
    var mw = hasMwFem() || hasOpxPlus();
    var lf = hasLfFem() || hasOpxPlus();
    var wantFlux    = lf && state.qubitFlux;
    var wantCoupler = lf && state.couplerFlux;

    // Show/hide populate sections based on active line types.
    var secQubit     = document.getElementById("gen-pop-sec-qubit");
    var secResonator = document.getElementById("gen-pop-sec-resonator");
    var secFlux      = document.getElementById("gen-pop-sec-flux");
    var secPulses    = document.getElementById("gen-pop-sec-pulses");
    var secPairs     = document.getElementById("gen-pop-sec-pairs");
    if (secQubit)     secQubit.hidden     = !mw;
    if (secResonator) secResonator.hidden = !mw;
    if (secFlux)      secFlux.hidden      = !wantFlux;
    if (secPulses)    secPulses.hidden    = !mw;
    if (secPairs)     secPairs.hidden     = !pairs.length;

    // Show a note listing which sections are hidden and why.
    var hiddenNote = document.getElementById("gen-pop-hidden-note");
    if (hiddenNote) {
      var hidden = [];
      if (!mw) hidden.push("Qubit / Resonator / Pulses (no MW-FEM)");
      if (!wantFlux) hidden.push("Flux (disabled or no LF-FEM)");
      if (!pairs.length) hidden.push("Pairs (no pairs defined)");
      if (hidden.length && hidden.length < 5) {
        hiddenNote.textContent = "Sections not shown: " + hidden.join(" \u00b7 ") +
          ". Change line types in step 4 to enable them.";
        hiddenNote.hidden = false;
      } else {
        hiddenNote.hidden = true;
      }
    }

    if (mw) {
      setPopHost("gen-pop-qubit",
        qubits.length ? buildPopTable("qubit", qubits, POP_QUBIT_COLS, "Qubit") : null,
        noQubits);
      setPopHost("gen-pop-resonator",
        qubits.length ? buildPopTable("resonator", qubits, POP_RESONATOR_COLS, "Qubit") : null,
        noQubits);
      renderPulsesTable();
    }
    if (wantFlux) {
      setPopHost("gen-pop-flux",
        qubits.length ? buildPopTable("flux", qubits, POP_FLUX_COLS, "Qubit") : null,
        noQubits);
      renderFluxDelaySummary(qubits);
    }
    if (pairs.length) {
      setPopHost("gen-pop-pairs",
        buildPopTable("pairs", pairs, pairPopCols(), "Pair"),
        "No qubit pairs defined.");
    }
    renderPairOrderNote();
    // First-paint validation: covers step entry, draft restore, unit toggle
    // and power-mode flip (all of which land here) — a restored 15.3 GHz
    // shows its flag with zero keystrokes.
    validateAllPopCells();
  }

  // LF-FEM analog outputs need a fixed delay to align with MW-FEM outputs.
  // Mirror of generator/run_build.py:_BAND_TO_DELAY_NS — keep in sync.
  var BAND_TO_DELAY_NS = { 1: 141, 2: 161, 3: 141 };

  // Render a small read-only summary below the flux Populate table showing
  // the LF-FEM delay each qubit's z line will receive at generation time.
  // The actual value lives at `qubits.<id>.z.opx_output.delay` and is
  // editable from the qubit detail page after the chip is generated.
  function renderFluxDelaySummary(qubits) {
    var host = document.getElementById("gen-pop-flux-delay-summary");
    if (!host) {
      var fluxHost = document.getElementById("gen-pop-flux");
      if (!fluxHost || !fluxHost.parentNode) return;
      host = document.createElement("div");
      host.id = "gen-pop-flux-delay-summary";
      host.className = "gen-pop-flux-delay-summary";
      fluxHost.parentNode.insertBefore(host, fluxHost.nextSibling);
    }
    if (!qubits || qubits.length === 0) { host.innerHTML = ""; return; }
    var pop = state.spec.populate || {};
    var qubitPop = pop.qubit || {};
    var rows = qubits.map(function (qid) {
      var lo = qubitPop[qid] && qubitPop[qid].LO_frequency;
      var band = bandOf(lo);
      var ns = band ? BAND_TO_DELAY_NS[band] : null;
      var bandStr = band ? ("band " + band) : "no LO yet";
      var nsStr = ns != null ? (ns + " ns") : "—";
      return '<span class="gen-pop-flux-delay-chip">'
        + '<code>' + qid + '</code> · '
        + '<strong>' + nsStr + '</strong>'
        + ' <small class="muted">(' + bandStr + ')</small>'
        + '</span>';
    });
    host.innerHTML =
      '<div class="gen-pop-flux-delay-title">'
      + 'LF-FEM <code>delay</code> (auto-set at generation, editable per-qubit afterward):'
      + '</div>'
      + '<div class="gen-pop-flux-delay-chips">' + rows.join("") + '</div>';
  }

  // -- step 6: default-value presets ------------------------------------
  // Named server-side sets of populate defaults (instance/gen_presets/ via
  // /generate/presets) — save recurring x180/readout/flux/pair seeds once,
  // re-apply them to any new chip. Values are BASE units straight from
  // spec.populate, so unit toggles never corrupt a preset. Capture rule:
  // a column uniform across every valued row → sections[sec].defaults[field]
  // (what a "Set all" fill produces); differing rows → .overrides[rid].
  // Never part of a preset: LO_frequency (re-derived from RF on apply),
  // grid_location (chip topology), the CR target LO/IF escape hatches.
  var PRESET_SKIP_FIELDS = {
    qubit: { LO_frequency: 1, grid_location: 1 },
    resonator: { LO_frequency: 1 },
    pairs: { target_qubit_LO_frequency: 1, target_qubit_IF_frequency: 1 }
  };
  var PRESET_SECTIONS = ["pulses", "qubit", "resonator", "flux", "pairs"];

  function presetSectionCols(sec) {
    if (sec === "qubit") return POP_QUBIT_COLS;
    if (sec === "resonator") return POP_RESONATOR_COLS;
    if (sec === "flux") return POP_FLUX_COLS;
    if (sec === "pulses") return POP_PULSE_FIELDS;
    if (sec === "pairs") return pairPopCols();
    return [];
  }

  function presetRowIds(sec) {
    if (sec === "pairs") {
      return state.spec.qubit_pairs
        .filter(function (p) { return p[0] && p[1]; })
        .map(function (p) { return p[0] + "-" + p[1]; });
    }
    return state.spec.qubits;
  }

  // Which sections are live on the current chip (mirror of the
  // renderPopulateTables show/hide gating) — applying flux values to a chip
  // with no LF-FEM would write dead spec keys.
  function presetActiveSections() {
    var mw = hasMwFem() || hasOpxPlus();
    var lf = hasLfFem() || hasOpxPlus();
    return {
      qubit: mw, resonator: mw, pulses: mw,
      flux: lf && state.qubitFlux,
      pairs: presetRowIds("pairs").length > 0
    };
  }

  // Capture the named sections of the current populate spec → the storage
  // shape ({defaults, overrides} per section). Pure read.
  function capturePresetSections(sectionNames) {
    var out = {};
    var pop = state.spec.populate || {};
    sectionNames.forEach(function (sec) {
      var bucket = pop[sec] || {};
      var skip = PRESET_SKIP_FIELDS[sec] || {};
      var defaults = {}, overrides = {};
      presetSectionCols(sec).forEach(function (col) {
        var f = col.field;
        if (skip[f]) return;
        var valued = [];
        Object.keys(bucket).forEach(function (rid) {
          var v = (bucket[rid] || {})[f];
          if (v != null && v !== "") valued.push([rid, v]);
        });
        if (!valued.length) return;
        var uniform = valued.every(function (rv) { return rv[1] === valued[0][1]; });
        if (uniform) {
          defaults[f] = valued[0][1];
        } else {
          valued.forEach(function (rv) {
            (overrides[rv[0]] = overrides[rv[0]] || {})[f] = rv[1];
          });
        }
      });
      out[sec] = { defaults: defaults, overrides: overrides };
    });
    return out;
  }

  // Apply a stored preset to the current chip. defaults fill every current
  // row; overrides only where the id matches (skips reported, never errors);
  // fields not in the chip's current column set (e.g. cr_* on a CZ chip)
  // drop with a note. Returns the report; the caller re-renders + recomputes.
  function applyPreset(preset, overwrite) {
    var pop = state.spec.populate;
    var report = { applied: 0, skippedRows: [], hiddenSections: [], droppedFields: [] };
    var sections = (preset && preset.sections) || {};
    var active = presetActiveSections();
    PRESET_SECTIONS.forEach(function (sec) {
      var body = sections[sec];
      if (!body) return;
      if (!active[sec]) { report.hiddenSections.push(sec); return; }
      var keep = {};
      presetSectionCols(sec).forEach(function (c) { keep[c.field] = true; });
      var skip = PRESET_SKIP_FIELDS[sec] || {};
      var rowIds = presetRowIds(sec);
      var rowSet = {};
      rowIds.forEach(function (r) { rowSet[r] = true; });
      function put(rid, f, v) {
        if (!keep[f] || skip[f]) {
          if (report.droppedFields.indexOf(f) < 0) report.droppedFields.push(f);
          return;
        }
        pop[sec] = pop[sec] || {};
        var b = pop[sec][rid] = pop[sec][rid] || {};
        if (!overwrite && b[f] != null && b[f] !== "") return;
        b[f] = v;
        report.applied++;
      }
      var defaults = body.defaults || {};
      rowIds.forEach(function (rid) {
        Object.keys(defaults).forEach(function (f) { put(rid, f, defaults[f]); });
      });
      var overrides = body.overrides || {};
      Object.keys(overrides).forEach(function (rid) {
        if (!rowSet[rid]) {
          if (report.skippedRows.indexOf(rid) < 0) report.skippedRows.push(rid);
          return;
        }
        Object.keys(overrides[rid] || {}).forEach(function (f) {
          put(rid, f, overrides[rid][f]);
        });
      });
    });
    return report;
  }

  function presetNote(text) {
    var el = document.getElementById("gen-preset-note");
    if (!el) return;
    el.textContent = text || "";
    el.hidden = !text;
  }

  // Fill the preset dropdown from the server. A fetch failure degrades to a
  // disabled "(presets unavailable)" option — never blocks the step.
  function loadPresetList(selectSlug) {
    var sel = document.getElementById("gen-preset-select");
    if (!sel) return;
    fetch("/generate/presets")
      .then(function (r) { return r.json(); })
      .then(function (res) {
        sel.innerHTML = '<option value="">(none)</option>';
        (res.presets || []).forEach(function (p) {
          var o = document.createElement("option");
          o.value = p.slug;
          var count = Object.keys(p.sections || {}).length;
          o.textContent = p.corrupt
            ? p.name + " (unreadable)"
            : p.name + " (" + count + " section" + (count === 1 ? "" : "s") + ")";
          if (p.corrupt) o.disabled = true;
          sel.appendChild(o);
        });
        if (selectSlug) sel.value = selectSlug;
      })
      .catch(function () {
        sel.innerHTML = '<option value="">(presets unavailable)</option>';
      });
  }

  function bindPresetBar() {
    var bar = document.getElementById("gen-preset-bar");
    if (!bar || bar.dataset.bound) return;
    bar.dataset.bound = "1";
    var sel = document.getElementById("gen-preset-select");
    var savebox = document.getElementById("gen-preset-savebox");
    var errEl = document.getElementById("gen-preset-err");

    function saveErr(msg) { if (errEl) errEl.textContent = msg || ""; }

    document.getElementById("gen-preset-apply").addEventListener("click", function () {
      if (!sel || !sel.value) { presetNote("Pick a preset to apply."); return; }
      var overwrite = !!document.getElementById("gen-preset-overwrite").checked;
      fetch("/generate/presets/" + encodeURIComponent(sel.value))
        .then(function (r) { return r.json(); })
        .then(function (preset) {
          if (!preset.ok) { presetNote(preset.error || "Preset not found."); return; }
          var rep = applyPreset(preset, overwrite);
          // Same refresh sequence as step entry: rebuild tables, re-derive
          // LOs (also re-runs power findings, CZ orientation + inline
          // validation via their hooks), refresh amp displays.
          renderPopulateTables();
          recomputeLOs();
          refreshAmpCells();
          var parts = [rep.applied + " value" + (rep.applied === 1 ? "" : "s") +
            " applied" + (overwrite ? "" : " (empty cells only)")];
          if (rep.skippedRows.length) {
            parts.push(rep.skippedRows.length + " per-row override" +
              (rep.skippedRows.length === 1 ? "" : "s") + " skipped (" +
              rep.skippedRows.slice(0, 4).join(", ") +
              (rep.skippedRows.length > 4 ? ", …" : "") + " not on this chip)");
          }
          if (rep.hiddenSections.length) {
            parts.push("sections not on this chip: " + rep.hiddenSections.join(", "));
          }
          if (rep.droppedFields.length) {
            parts.push("fields not applicable: " + rep.droppedFields.join(", "));
          }
          presetNote('Preset "' + (preset.name || sel.value) + '": ' + parts.join(" · "));
        })
        .catch(function () { presetNote("Could not load the preset."); });
    });

    document.getElementById("gen-preset-save").addEventListener("click", function () {
      if (savebox) savebox.hidden = !savebox.hidden;
      saveErr("");
      // Disable checkboxes for sections hidden on this chip.
      var active = presetActiveSections();
      PRESET_SECTIONS.forEach(function (sec) {
        var box = document.getElementById("gen-preset-sec-" + sec);
        if (!box) return;
        box.disabled = !active[sec];
        box.parentNode.title = active[sec] ? ""
          : "Section not active on this chip (no matching hardware/pairs)";
        if (!active[sec]) box.checked = false;
      });
    });
    document.getElementById("gen-preset-save-cancel").addEventListener("click", function () {
      if (savebox) savebox.hidden = true;
      saveErr("");
    });

    document.getElementById("gen-preset-save-confirm").addEventListener("click", function doSave(ev, overwrite) {
      var name = (document.getElementById("gen-preset-name").value || "").trim();
      if (!name) { saveErr("Enter a preset name."); return; }
      var secs = PRESET_SECTIONS.filter(function (sec) {
        var box = document.getElementById("gen-preset-sec-" + sec);
        return box && box.checked && !box.disabled;
      });
      if (!secs.length) { saveErr("Tick at least one section."); return; }
      var sections = capturePresetSections(secs);
      var any = secs.some(function (sec) {
        var b = sections[sec];
        return Object.keys(b.defaults).length || Object.keys(b.overrides).length;
      });
      if (!any) { saveErr("Nothing to save — the ticked sections have no values."); return; }
      fetch("/generate/presets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, sections: sections, overwrite: !!overwrite })
      })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          if (res.needs_confirm) {
            if (window.confirm(res.error + " Overwrite it?")) doSave(null, true);
            return;
          }
          if (!res.ok) { saveErr(res.error || "Save failed."); return; }
          if (savebox) savebox.hidden = true;
          saveErr("");
          presetNote('Preset "' + name + '" saved.');
          loadPresetList(res.slug);
        })
        .catch(function () { saveErr("Save failed (network)."); });
    });

    document.getElementById("gen-preset-delete").addEventListener("click", function () {
      if (!sel || !sel.value) { presetNote("Pick a preset to delete."); return; }
      var label = sel.options[sel.selectedIndex].textContent;
      if (!window.confirm('Delete preset "' + label + '"?')) return;
      fetch("/generate/presets/" + encodeURIComponent(sel.value), { method: "DELETE" })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          if (res && res.ok === false) {   // e.g. the undeletable built-in
            presetNote(res.error || "Delete refused.");
            return;
          }
          presetNote("Preset deleted.");
          loadPresetList();
        })
        .catch(function () { presetNote("Delete failed (network)."); });
    });
  }

  function enterPopulateStep() {
    // Clear any stale live-preview panel from a previous visit to this step.
    if (window.GenPreview && window.GenPreview.reset) window.GenPreview.reset();
    bindPresetBar();
    loadPresetList();
    loadPopulateUnits();
    loadPowerMode();
    renderUnitToggles();
    renderPopulateTables();
    var loMap = document.getElementById("gen-lo-map");
    if (loMap) {
      loMap.open = loadLoMapOpen();
      if (!loMap.dataset.bound) {
        loMap.dataset.bound = "1";
        loMap.addEventListener("toggle", function () {
          saveLoMapOpen(loMap.open);
        });
      }
    }
    var popWiring = document.getElementById("gen-pop-wiring");
    if (popWiring) {
      popWiring.open = loadPopWiringOpen();
      if (!popWiring.dataset.bound) {
        popWiring.dataset.bound = "1";
        popWiring.addEventListener("toggle", function () {
          savePopWiringOpen(popWiring.open);
        });
      }
    }
    renderPopTopo();     // read-only chip-board mirror (toggleable)
    renderPopWiring();   // build the diagram first…
    reconcileReadoutBanks();   // converge any pre-allocation divergent-FSP banks
    recomputeLOs();      // …so renderConflicts() can ring its ports
  }

  // -- step 7: output folder -------------------------------------------

  // Read live from the input — the folder browser fills .value directly.
  function getOutputPath() {
    var input = document.getElementById("gen-output-path");
    return input ? input.value.trim() : "";
  }

  function getScriptsPath() {
    var input = document.getElementById("gen-scripts-path");
    return input ? input.value.trim() : (state.scriptsPath || "");
  }

  // Mirror the DOM-only output-path field into state so it persists in a draft.
  function bindOutputStep() {
    var out = document.getElementById("gen-output-path");
    if (out) {
      // "change" too: the folder browser fills .value programmatically and
      // dispatches change (never input) — without it the mirror ran only on
      // hand-typed paths.
      ["input", "change"].forEach(function (ev) {
        out.addEventListener(ev, function () {
          state.outputPath = out.value.trim();
          // Durable mirror — a cleared sessionStorage draft (crash, quota,
          // tab close) used to silently lose the output folder.
          try { localStorage.setItem("quam_gen_output_path", state.outputPath); }
          catch (e) { /* private mode */ }
        });
      });
    }
    // Editable-scripts export controls (same durability treatment).
    var chk = document.getElementById("gen-scripts-enable");
    var sp = document.getElementById("gen-scripts-path");
    var field = document.getElementById("gen-scripts-field");
    if (chk) {
      chk.addEventListener("change", function () {
        state.scriptsEnabled = chk.checked;
        if (field) field.hidden = !chk.checked;
      });
    }
    if (sp) {
      ["input", "change"].forEach(function (ev) {
        sp.addEventListener(ev, function () {
          state.scriptsPath = sp.value.trim();
          try { localStorage.setItem("quam_gen_scripts_path", state.scriptsPath); }
          catch (e) { /* private mode */ }
        });
      });
    }
  }

  // Paint the step-7 scripts controls from state (draft restore / repaint).
  function syncScriptsControls() {
    var chk = document.getElementById("gen-scripts-enable");
    var sp = document.getElementById("gen-scripts-path");
    var field = document.getElementById("gen-scripts-field");
    if (chk) chk.checked = !!state.scriptsEnabled;
    if (field) field.hidden = !state.scriptsEnabled;
    if (sp) sp.value = state.scriptsPath || "";
  }

  // -- step 8: review & generate ---------------------------------------

  function enterReviewStep() {
    var el = document.getElementById("gen-review");
    if (!el) return;
    czAutoOrient();   // frequencies may have changed since the pairs table
    var sp = state.spec;
    var inst = sp.instruments;
    var femCount = inst.controllers.reduce(function (n, c) {
      return n + c.fems.length;
    }, 0);
    // Build the "Lines" summary from active line types.
    var lineTypes = [];
    var mw = hasMwFem() || hasOpxPlus();
    var lf = hasLfFem() || hasOpxPlus();
    if (mw) lineTypes.push("Resonator + XY drive");
    if (lf && state.qubitFlux) lineTypes.push("Qubit flux (z)");
    if (sp.qubit_pairs.length) {
      var pairGate = state.pairGate || "cz_tunable";
      if (pairGate === "cr" && mw) {
        lineTypes.push("Cross-resonance (CR)"
          + (state.crPortMode === "shared_xy"
             ? " — shared xy port (dual upconverter)" : " — dedicated ports"));
        if (state.zzEnabled) lineTypes.push("ZZ (Stark-CZ) drive");
      }
      else if (pairGate === "cz_fixed" && lf) lineTypes.push("CZ — fixed coupler");
      else if (pairGate === "cz_tunable" && lf) lineTypes.push("CZ — tunable coupler (coupler flux)");
    }
    var linesSummary = lineTypes.length ? lineTypes.join(", ") : "(none)";

    var rows = [
      ["Environment", state.env || "(none selected — step 1)"],
      ["Network", (sp.network.host || "?") +
        " · cluster " + (sp.network.cluster_name || "?")],
      ["Instruments", inst.controllers.length + " OPX1000 (" + femCount +
        " FEMs), " + inst.opx_plus.length + " OPX+, " +
        inst.octaves.length + " Octave"],
      ["Qubits / Pairs / TWPAs", sp.qubits.length + " / " +
        sp.qubit_pairs.length + " / " + sp.twpas.length],
      ["Control lines", linesSummary],
      ["Output folder", getOutputPath() || "(not set — step 7)"],
      ["Python scripts", state.scriptsEnabled
        ? (getScriptsPath() || "(folder not set — step 7)") : "(off)"]
    ];
    // CZ chips: surface how the pair roles were assigned before generating.
    if (czOrderActive() && sp.qubit_pairs.length) {
      var czCounts = { ok: 0, manual: 0, pending: 0, equal: 0 };
      czOrderStatus().forEach(function (s) { czCounts[s.status]++; });
      var czParts = [];
      if (czCounts.ok) czParts.push(czCounts.ok + " auto (higher-f = control)");
      if (czCounts.manual) czParts.push(czCounts.manual + " manual");
      if (czCounts.pending) czParts.push(czCounts.pending + " pending frequencies");
      if (czCounts.equal) czParts.push(czCounts.equal + " equal frequencies");
      rows.splice(5, 0, ["CZ pair orientation", czParts.join(", ")]);
    }
    el.innerHTML = '<table class="gen-review-table"><tbody>' +
      rows.map(function (r) {
        return "<tr><th>" + r[0] + "</th><td></td></tr>";
      }).join("") + "</tbody></table>" +
      '<div id="gen-capability-report" class="gen-cap-report"></div>';
    // Fill values via textContent so user-entered strings can't inject HTML.
    var cells = el.querySelectorAll("td");
    rows.forEach(function (r, i) { cells[i].textContent = r[1]; });

    state._buildForce = false; state._buildAck = false;   // fresh review → fresh gates
    renderCapabilityReport(document.getElementById("gen-capability-report"));
  }

  // Ask the selected env what it can actually build for THIS spec, and render
  // the three-bucket verdict (available / blockers / degrades) + a full inventory.
  function renderCapabilityReport(box) {
    if (!box) return;
    if (!state.env) { box.innerHTML = ""; return; }
    deriveLines();
    box.innerHTML = '<p class="muted">Checking what this environment can build…</p>';
    fetch("/generate/capabilities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // regenerate mode: the source chip's folder rides along so the server
      // can compare its CR schema flavor against the env (null in plain
      // Generate mode — no chip to compare).
      body: JSON.stringify({ spec: state.spec,
                             source_folder: state.sourcePath || null })
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (!res.ok) { box.innerHTML = ""; return; }
        var rep = res.report || {};
        box.innerHTML = "";
        if (!rep.manifest_ok) {
          var u = document.createElement("p");
          u.className = "gen-build-warn-line";
          u.textContent = "⚠ Could not inspect this environment" +
            (res.probe_error ? " (" + res.probe_error + ")" : "") +
            " — capabilities unknown.";
          box.appendChild(u);
          _capRecheckButton(box);
          return;
        }
        var h = document.createElement("p");
        h.className = "gen-cap-head";
        h.textContent = rep.buildable
          ? "✓ This environment can build everything this chip needs."
          : "✗ This environment is missing something this chip needs.";
        box.appendChild(h);
        // Chip↔env schema-flavor mismatches (regenerate: warn BEFORE any
        // Quam.load fails in a subprocess). Shape: {level, message}.
        (res.flavor || []).forEach(function (f) {
          var line = document.createElement("p");
          line.className = (f.level === "blocker")
            ? "gen-cap-blocker" : "gen-cap-degrade";
          line.textContent = (f.level === "blocker" ? "✗ " : "⚠ ") + f.message;
          box.appendChild(line);
        });
        _capRows(box, rep.blockers, "gen-cap-blocker", "Cannot build");
        _capRows(box, rep.warnings, "gen-cap-degrade", "Will be skipped / downgraded");
        _capInventory(box, rep.inventory);
        _capRecheckButton(box);
      })
      .catch(function () { box.innerHTML = ""; });
  }

  function _capRows(box, rows, cls, heading) {
    if (!rows || !rows.length) return;
    var hd = document.createElement("p");
    hd.className = "gen-cap-subhead";
    hd.textContent = heading + ":";
    box.appendChild(hd);
    rows.forEach(function (r) {
      var line = document.createElement("p");
      line.className = cls;
      line.textContent = "• " + r.label + " — " + (r.produces || "") +
        ". Needs " + (r.package || "?") + " · " + (r.symbol || "") +
        ". Fix: " + (r.fix || "");
      box.appendChild(line);
    });
  }

  function _capInventory(box, inventory) {
    if (!inventory || !inventory.length) return;
    var det = document.createElement("details");
    det.className = "gen-cap-inventory";
    var sm = document.createElement("summary");
    var nAvail = inventory.filter(function (r) { return r.available; }).length;
    sm.textContent = "Full environment inventory (" + nAvail + "/" +
      inventory.length + " capabilities available)";
    det.appendChild(sm);
    inventory.forEach(function (r) {
      var line = document.createElement("div");
      line.className = "gen-cap-inv-line" + (r.available ? "" : " gen-cap-inv-miss");
      line.textContent = (r.available ? "✓ " : "✗ ") + r.label +
        (r.requested ? "  (used by this chip)" : "") + " — " + (r.symbol || "");
      det.appendChild(line);
    });
    box.appendChild(det);
  }

  function _capRecheckButton(box) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "outline gen-cap-recheck";
    btn.textContent = "Re-check environment";
    btn.title = "Re-probe the env (use after pip-installing into it)";
    btn.addEventListener("click", function () {
      box.innerHTML = '<p class="muted">Re-checking…</p>';
      fetch("/generate/capabilities", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ spec: state.spec, force: true,
                               source_folder: state.sourcePath || null })
      }).then(function () { renderCapabilityReport(box); });
    });
    box.appendChild(btn);
  }

  function runLoad(outPath) {
    fetch("/generate/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: outPath })
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.ok && res.redirect) {
          window.location.href = res.redirect;
        } else {
          showMessage(res.error || "Could not load the generated config.", "error");
        }
      })
      .catch(function () {
        showMessage("Load request failed.", "error");
      });
  }

  // Post-build preview: run machine.generate_config() on the just-built
  // folder and show the result right here — no Load-into-app round trip.
  // The backend stashes the result so a later Load seeds the store's cache.
  function renderPreviewError(out, res) {
    var head = document.createElement("p");
    head.className = "gen-build-err-line";
    head.textContent = "✗ Preview failed: " + (res.error || "previewer reported an error");
    out.appendChild(head);
    if (res.traceback) {
      var det = document.createElement("details");
      var sum = document.createElement("summary");
      sum.textContent = "Traceback";
      det.appendChild(sum);
      var pre = document.createElement("pre");
      pre.className = "config-trace";
      pre.textContent = res.traceback;
      det.appendChild(pre);
      out.appendChild(det);
    }
  }

  function runPreviewConfig(outPath, btn, out) {
    btn.disabled = true;
    out.textContent =
      "Running machine.generate_config()… this can take up to two minutes " +
      "while the QM stack loads.";

    fetch("/generate/preview-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: outPath })
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        btn.disabled = false;
        out.textContent = "";
        if (!res.ok || !res.config) {
          renderPreviewError(out, res || {});
          return;
        }
        var meta = res.meta || {};
        var v = meta.versions || {};
        var line = document.createElement("p");
        line.className = "muted";
        line.textContent = "Config: " +
          ((meta.qubits || []).length) + " qubits · " +
          ((meta.qubit_pairs || []).length) + " pairs · quam " +
          (v.quam || "?") + " · quam_builder " + (v.quam_builder || "?") +
          ((meta.warnings || []).length
            ? " · " + meta.warnings.length + " warning(s)" : "");
        out.appendChild(line);
        (meta.warnings || []).forEach(function (w) {
          var wel = document.createElement("p");
          wel.className = "gen-build-warn-line";
          wel.textContent = "⚠ " + w;
          out.appendChild(wel);
        });

        // Bare-QUA export: the previewed config is stashed server-side (keyed by
        // this folder), so offer it as a drop-in config.json / config.py without
        // loading the chip into the app first. Plain <a download> — the server
        // sets Content-Disposition, so the browser saves without navigating.
        var exp = document.createElement("div");
        exp.className = "gen-config-export";
        var lead = document.createElement("span");
        lead.className = "muted gen-config-export-lead";
        lead.textContent = "For bare QUA:";
        exp.appendChild(lead);
        var qp = "path=" + encodeURIComponent(outPath);
        [["config.json", "json"], ["config.py", "py"]].forEach(function (pair) {
          var a = document.createElement("a");
          a.className = "outline gen-config-export-btn";
          a.href = "/generate/export-config?" + qp + "&format=" + pair[1];
          a.setAttribute("download", "");
          a.textContent = "↓ " + pair[0];
          exp.appendChild(a);
        });
        out.appendChild(exp);

        // Reuse the shared slide-up JSON panel (read-only copy mode — the
        // default "edit" mode is for live-state trees only).
        var panel = document.getElementById("json-panel");
        var treeEl = document.getElementById("json-panel-tree");
        var title = document.getElementById("json-panel-title");
        if (panel && treeEl && title && typeof window.renderJsonTree === "function") {
          title.textContent = "Generated config — " +
            (outPath.split(/[\\/]/).pop() || outPath);
          treeEl.innerHTML = "";
          window.renderJsonTree("json-panel-tree", res.config,
                                { defaultDepth: 1, valueClick: "copy" });
          panel.classList.remove("hidden");
        }
      })
      .catch(function () {
        btn.disabled = false;
        out.textContent = "";
        renderPreviewError(out, { error: "Preview request failed." });
      });
  }

  function showBuildResult(res, outPath) {
    var el = document.getElementById("gen-build-result");
    if (!el) return;
    el.hidden = false;
    el.innerHTML = "";

    if (res.ok && res.result) {
      state._buildForce = false; state._buildAck = false;   // consumed — reset gates
      el.className = "gen-build-result gen-build-ok";
      var r = res.result;
      var msg = document.createElement("p");
      msg.textContent = "✓ Generated " +
        ((r.qubits || []).length) + " qubits and " +
        ((r.qubit_pairs || []).length) + " pairs into " + outPath;
      el.appendChild(msg);
      (r.warnings || []).forEach(function (w) {
        var wel = document.createElement("p");
        wel.className = "gen-build-warn-line";
        wel.textContent = "⚠ " + w;
        el.appendChild(wel);
      });
      // Editable-scripts export outcome (best-effort side artefact).
      if (res.scripts) {
        var sc = document.createElement("p");
        sc.textContent = "✓ Python scripts written to " + res.scripts.dir +
          " (" + (res.scripts.files || []).join(", ") + ")";
        el.appendChild(sc);
      } else if (res.scripts_error) {
        var se = document.createElement("p");
        se.className = "gen-build-warn-line";
        se.textContent = "⚠ Python scripts export failed: " + res.scripts_error +
          " (the chip itself built fine)";
        el.appendChild(se);
      }

      // Re-generate value-merge transparency — nothing is hidden: how many
      // calibrated values carried, user-added ops/macros grafted, and anything
      // that couldn't carry (structure the user removed / broken pointers).
      if (res.merge) {
        var m = res.merge;
        var supN = m.superseded || 0;               // value moved to a reference (preserved)
        var lostN = (m.residual_lost || []).length; // TRULY not carried
        var dangN = (m.dangling_grafts || []).length;
        var twpaN = m.twpa_wiring_carried || 0;     // TWPAs carried (wiring + ports)
        var prunedN = m.pruned_ops || 0;            // redundant old ops cleaned
        var mp = document.createElement("div");
        mp.className = "gen-merge-report";
        mp.innerHTML =
          '<span class="gen-merge-h">Values preserved</span>' +
          '<span class="gen-merge-stat gen-merge-ok">' + m.carried + ' carried</span>' +
          '<span class="gen-merge-stat gen-merge-graft">' + m.grafted + ' grafted</span>' +
          (supN ? '<span class="gen-merge-stat gen-merge-ok" title="Value preserved — the ' +
            'rebuild references it (e.g. a CZ pulse the old builder stored inline, now on the ' +
            'qubit z line)">' + supN + ' via reference</span>' : '') +
          (twpaN ? '<span class="gen-merge-stat gen-merge-ok" title="TWPAs the builder ' +
            "can't rebuild, carried whole (state + wiring + ports) so the config still " +
            'compiles">' + twpaN + ' TWPA carried</span>' : '') +
          '<span class="gen-merge-stat ' + (lostN ? 'gen-merge-warn' : 'gen-merge-muted') +
            '" title="OLD values with no home in the rebuild">' +
            lostN + ' not carried</span>' +
          (dangN ? '<span class="gen-merge-stat gen-merge-warn" title="Grafted legacy content ' +
            'whose reference no longer resolves">' + dangN + ' broken ref</span>' : '');
        el.appendChild(mp);
        if (prunedN) {
          var pn = document.createElement("div");
          pn.className = "gen-merge-muted gen-merge-detail";
          pn.textContent = "cleaned " + prunedN +
            " redundant legacy op" + (prunedN === 1 ? "" : "s") +
            " the rebuild re-expressed (unreferenced, broken pointers)";
          el.appendChild(pn);
        }
        if (lostN || dangN) {
          var det = document.createElement("details");
          det.className = "gen-merge-detail";
          var sm = document.createElement("summary");
          sm.textContent = "Not carried / broken (" + (lostN + dangN) + ") — expand";
          det.appendChild(sm);
          (m.residual_lost || []).concat(m.dangling_grafts || [])
            .slice(0, 80).forEach(function (p) {
              var line = document.createElement("div");
              line.className = "gen-merge-lost-line";
              line.textContent = p;
              det.appendChild(line);
            });
          el.appendChild(det);
        }
      }

      // Build recipe: a single editable Python file written next to state.json,
      // so the config is owned as code (QM generate + populate combined).
      if (res.script) {
        var sc = document.createElement("div");
        sc.className = "gen-merge-report";
        sc.innerHTML =
          '<span class="gen-merge-h">Build recipe</span>' +
          '<span class="gen-merge-stat gen-merge-ok" title="Editable build scripts ' +
          '(01 wiring / 02 populate+gates / 03 config check) reproducing this chip ' +
          '— edit the data blocks and re-run to rebuild">&#128196; ' +
          res.script + '</span>' +
          '<span class="gen-merge-muted gen-merge-detail" style="margin-left:.4rem">' +
          'written to the output folder</span>';
        el.appendChild(sc);
      } else if (res.script_error) {
        var se = document.createElement("div");
        se.className = "gen-merge-report";
        se.innerHTML = '<span class="gen-merge-stat gen-merge-warn">build recipe not ' +
          'written: ' + res.script_error + '</span>';
        el.appendChild(se);
      }

      var loadBtn = document.createElement("button");
      loadBtn.type = "button";
      loadBtn.textContent = "Load into app";
      loadBtn.addEventListener("click", function () { runLoad(outPath); });
      el.appendChild(loadBtn);

      var previewBtn = document.createElement("button");
      previewBtn.type = "button";
      previewBtn.className = "outline";
      previewBtn.textContent = "Preview config";
      el.appendChild(previewBtn);

      var previewOut = document.createElement("div");
      previewOut.className = "gen-preview-result";
      el.appendChild(previewOut);

      previewBtn.addEventListener("click", function () {
        runPreviewConfig(outPath, previewBtn, previewOut);
      });
    } else if (res.capability_blockers && res.capability_blockers.length) {
      // The env is missing something the build needs — cannot proceed. Show the
      // exact package/function gap + fix for each; no override (build would crash).
      el.className = "gen-build-result gen-build-error";
      var bhead = document.createElement("p");
      bhead.textContent = "✗ This environment can't build this chip:";
      el.appendChild(bhead);
      res.capability_blockers.forEach(function (b) {
        var p = document.createElement("p");
        p.className = "gen-build-err-line";
        p.textContent = "• " + b.label + " — needs " + (b.package || "?") +
          " · " + (b.symbol || "") + " (missing). Fix: " + (b.fix || "");
        el.appendChild(p);
      });
    } else {
      el.className = "gen-build-result gen-build-error";
      var errs;
      if (res.result && res.result.error) {
        errs = [res.result.error];
      } else {
        errs = res.errors || (res.error ? [res.error] : ["Generation failed."]);
      }
      var head = document.createElement("p");
      head.textContent = "✗ Generation failed:";
      el.appendChild(head);
      errs.forEach(function (e) {
        var p = document.createElement("p");
        p.className = "gen-build-err-line";
        p.textContent = e;
        el.appendChild(p);
      });
    }
  }

  // Output folder already holds other .json — the server asks before a build
  // that QUAM's whole-folder loader could not safely round-trip.
  function showBuildConfirm(res, outPath) {
    var el = document.getElementById("gen-build-result");
    if (!el) return;
    el.hidden = false;
    el.className = "gen-build-result gen-build-confirm";
    el.innerHTML = "";

    var head = document.createElement("p");
    head.textContent = "⚠ " + (res.error || "The output folder is not empty.");
    el.appendChild(head);

    if (res.confirm_kind === "capability") {
      // Requested features this env can't build — they'll be skipped/downgraded.
      (res.capability_warnings || []).forEach(function (w) {
        var line = document.createElement("p");
        line.className = "gen-build-warn-line";
        line.textContent = "• " + w.label + " — won't be built (" +
          (w.produces || "") + "). Fix: " + (w.fix || "");
        el.appendChild(line);
      });
      var chint = document.createElement("p");
      chint.className = "muted";
      chint.textContent = "Fix the environment for these, or build without them.";
      el.appendChild(chint);
      var cgo = document.createElement("button");
      cgo.type = "button";
      cgo.textContent = "Build without these features";
      cgo.addEventListener("click", function () { runBuild(false, true); });
      el.appendChild(cgo);
      return;
    }

    (res.conflict_files || []).forEach(function (name) {
      var line = document.createElement("p");
      line.className = "gen-build-warn-line";
      line.textContent = "• " + name;
      el.appendChild(line);
    });

    var hint = document.createElement("p");
    hint.className = "muted";
    hint.textContent =
      "Go back to step 7 to choose an empty folder, or generate anyway.";
    el.appendChild(hint);

    var go = document.createElement("button");
    go.type = "button";
    go.textContent = "Generate anyway";
    go.addEventListener("click", function () { runBuild(true); });
    el.appendChild(go);
  }

  function runBuild(force, ackDegrades) {
    if (!state.env) {
      showMessage("Select an environment in step 1.", "warn");
      return;
    }
    // Accumulate acknowledgements across confirm round-trips — the build has two
    // independent gates (capability degrades, stray-JSON) and acking one must not
    // drop the other. enterReviewStep()/a successful build reset these.
    if (force) state._buildForce = true;
    if (ackDegrades) state._buildAck = true;
    // Re-derive lines from the current qubits/pairs in case the wiring step
    // was skipped via the step chips — otherwise the build gets no lines.
    czAutoOrient();   // defense-in-depth: never build a CZ pair backwards
    deriveLines();
    var outPath = getOutputPath();
    if (!outPath) {
      showMessage("Choose an output folder in step 7.", "warn");
      return;
    }

    // Final topology gate (defense-in-depth) — a hole-y / dangling / partially-placed
    // spec must never reach the build, even if a step chip jumped past the step-4
    // guard. Send the user back to the Qubits step with the reason.
    var topoErr = topologyBlocker();
    if (topoErr) {
      goToStep(4);
      showMessage(topoErr, "warn");
      return;
    }

    // Wiring validation gate — R1-class errors are physically impossible.
    var wErrors = validateWiring().filter(function (it) { return it.level === "error"; });
    if (wErrors.length) {
      var gEl = document.getElementById("gen-build-result");
      if (gEl) {
        gEl.hidden = false;
        gEl.className = "gen-build-result gen-build-error";
        gEl.innerHTML = "";
        var gh = document.createElement("p");
        gh.textContent = "✗ Wiring has " + wErrors.length +
          " error(s) — fix them in step 5 (Wiring) before generating:";
        gEl.appendChild(gh);
        wErrors.forEach(function (it) {
          var gp = document.createElement("p");
          gp.className = "gen-build-err-line";
          gp.textContent = "• " + it.message;
          gEl.appendChild(gp);
        });
      }
      return;
    }

    var resultEl = document.getElementById("gen-build-result");
    var nextBtn = document.getElementById("gen-next");
    if (nextBtn) nextBtn.disabled = true;
    if (resultEl) {
      resultEl.hidden = false;
      resultEl.className = "gen-build-result";
      resultEl.textContent =
        "Generating… this can take up to a minute while the QM stack loads.";
    }

    fetch(state.buildEndpoint || "/generate/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        spec: state.spec, output_path: outPath,
        force: !!state._buildForce, ack_degrades: !!state._buildAck,
        source_folder: state.sourcePath || null,  // regenerate: merge from here
        // optional editable-scripts export (step 7 checkbox)
        scripts_dir: (state.scriptsEnabled && state.scriptsPath) || null
      })
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (nextBtn) nextBtn.disabled = false;
        if (res.needs_confirm) {
          showBuildConfirm(res, outPath);
          return;
        }
        showBuildResult(res, outPath);
      })
      .catch(function () {
        if (nextBtn) nextBtn.disabled = false;
        if (resultEl) {
          resultEl.className = "gen-build-result gen-build-error";
          resultEl.textContent = "Generate request failed.";
        }
      });
  }

  // -- init ------------------------------------------------------------

  // -- draft persistence -----------------------------------------------
  // The wizard's state is in-memory only; opening another sidebar page swaps
  // #table-pane away and would drop it. Save to sessionStorage on the way
  // out, restore on the way back. Cleared by Reset or by closing the app.
  var DRAFT_KEY = "quam_generate_draft";
  var DRAFT_VERSION = 2;

  // Mirror live DOM input values into state before serialising / navigating.
  // Flushes any value typed but not yet committed: the pywebview webview can
  // swallow the blur->change (or input) event on a fast stepper / Back / Next
  // click, which would otherwise lose the edit on the next save. Re-dispatching
  // an input's own commit event reuses its handler, so the value lands in
  // state.spec exactly as a normal edit would (idempotent for committed inputs).
  // Scoped to the current step's inputs so leaving an unrelated step never
  // re-commits a 200-qubit populate table.
  function captureDomFields() {
    if (root()) {
      // Network (step 2) — three cheap inputs whose handlers run on `input`.
      ["gen-net-host", "gen-net-cluster", "gen-net-port"].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.dispatchEvent(new Event("input", { bubbles: true }));
      });
      // Qubit count (step 4) — only re-fire when it actually changed, so a no-op
      // nav doesn't needlessly rebuild the qubit/pair table.
      var qc = document.getElementById("gen-qubit-count");
      if (qc && qc.value !== "" &&
          parseInt(qc.value, 10) !== state.spec.qubits.length) {
        qc.dispatchEvent(new Event("change", { bubbles: true }));
      }
      // Populate (step 6) — flush ONLY the cells the user actually edited (marked
      // data-dirty on input). Firing change on EVERY cell would clobber a
      // hand-typed LO_frequency (an untouched RF_freq fires recomputeLOs which
      // rewrites every LO), flip a dBm-mode negative amplitude to positive, delete
      // a dBm-mode 0, and O(N^2)-storm recomputeLOs on a big chip. Dirty-only
      // commits the blur-race edit no matter where focus moved (a real webview
      // moves focus to the clicked Back/Next button, so the focused-element alone
      // is not enough). `[data-field]` excludes the bulk "set all" cells.
      if (state.step === 6) {
        document.querySelectorAll('.gen-pop-in[data-field][data-dirty="1"]').forEach(
          function (el) { el.dispatchEvent(new Event("change", { bubbles: true })); });
      }
    }
    // The two fields with no commit listener of their own are read directly.
    var mux = document.getElementById("gen-mux-size");
    if (mux && mux.value !== "") {
      var m = parseInt(mux.value, 10);
      if (!isNaN(m)) state.muxSize = clampMux(m);
    }
    var out = document.getElementById("gen-output-path");
    if (out) state.outputPath = out.value.trim();
    var sp = document.getElementById("gen-scripts-path");
    if (sp) state.scriptsPath = sp.value.trim();
  }

  function saveDraft() {
    // Re-generate re-hydrates from the source chip on every visit, so it must
    // NOT write the shared Generate draft — otherwise its (named, non-contiguous)
    // spec leaks into a later plain Generate session and triggers the renumber gate.
    if (state.mode === "regenerate") return;
    try {
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify({
        v: DRAFT_VERSION, step: state.step, env: state.env,
        spec: state.spec, allocation: state.allocation,
        pairsTouched: state.pairsTouched, wiringTouched: state.wiringTouched,
        naming: state.naming, namesTouched: state.namesTouched,
        populateUnits: state.populateUnits,
        muxSize: state.muxSize, outputPath: state.outputPath,
        scriptsEnabled: state.scriptsEnabled, scriptsPath: state.scriptsPath,
        qubitFlux: state.qubitFlux, couplerFlux: state.couplerFlux,
        pairGate: state.pairGate, chipArch: state.chipArch,
        crPortMode: state.crPortMode, zzEnabled: state.zzEnabled,
        topoZone: state.topoZone
      }));
    } catch (e) { /* quota / serialisation — non-fatal */ }
  }

  // A saved draft, or null (missing / corrupt / wrong version — then dropped).
  function loadDraft() {
    var raw;
    try { raw = sessionStorage.getItem(DRAFT_KEY); } catch (e) { return null; }
    if (!raw) return null;
    var d = null;
    try { d = JSON.parse(raw); } catch (e) { d = null; }
    if (!d || d.v !== DRAFT_VERSION) {
      try { sessionStorage.removeItem(DRAFT_KEY); } catch (e2) {}
      return null;
    }
    return d;
  }

  // Copy a loaded draft into `state`, filling any spec keys it lacks against
  // freshSpec() (defensive across minor shape changes within a version).
  function applyDraft(d) {
    var spec = d.spec || {}, fresh = freshSpec();
    Object.keys(fresh).forEach(function (k) {
      if (spec[k] == null) spec[k] = fresh[k];
    });
    state.spec = spec;
    state.step = Math.max(1, Math.min(STEP_COUNT, d.step || 1));
    state.env = d.env || null;
    state.allocation = d.allocation || null;
    state.pairsTouched = !!d.pairsTouched;
    state.wiringTouched = !!d.wiringTouched;
    // Naming scheme — old drafts lack it: default = the historical q1…qN.
    var nm = (d.naming && typeof d.naming === "object") ? d.naming : {};
    state.naming = {
      preset: nm.preset || "one_based",
      prefix: nm.prefix || "q",
      start: isNaN(parseInt(nm.start, 10)) ? 1 : parseInt(nm.start, 10)
    };
    state.namesTouched = !!d.namesTouched;
    // Topology-board extent (build-INERT; the placements themselves live in
    // grid_location). A legacy draft lacks it -> WiringGrid.zone() re-derives a
    // default from the qubit count, so opening the board never throws/blanks.
    state.topoZone = (d.topoZone && d.topoZone.cols && d.topoZone.rows) ? d.topoZone : null;
    if (d.populateUnits) {
      // Merge, not replace: an older draft might lack `amp`, in which case
      // the toggle would default to undefined and show empty.
      Object.keys(d.populateUnits).forEach(function (dim) {
        state.populateUnits[dim] = d.populateUnits[dim];
      });
    }
    state.muxSize = clampMux(d.muxSize || 6);   // old drafts may carry 9–16
    // Restore precedence: draft > durable localStorage mirror. (The DOM input
    // wins over both — repaintFromState only paints state.outputPath, and
    // captureDomFields reads the input back on every nav.)
    state.outputPath = d.outputPath || "";
    if (!state.outputPath) {
      try { state.outputPath = localStorage.getItem("quam_gen_output_path") || ""; }
      catch (e) { /* private mode */ }
    }
    state.scriptsEnabled = !!d.scriptsEnabled;
    // CR options — old drafts lack them: dedicated ports, no ZZ (the
    // historical behavior).
    state.crPortMode = (d.crPortMode === "shared_xy") ? "shared_xy" : "dedicated";
    state.zzEnabled = !!d.zzEnabled;
    state.scriptsPath = d.scriptsPath || "";
    if (!state.scriptsPath) {
      try { state.scriptsPath = localStorage.getItem("quam_gen_scripts_path") || ""; }
      catch (e) { /* private mode */ }
    }
    // Line-type toggles — default true for backward compat with old drafts.
    state.qubitFlux = d.qubitFlux !== false;
    state.couplerFlux = d.couplerFlux !== false;
    // 2-qubit gate — default to the tunable-coupler CZ, and migrate the
    // pre-redesign vocabulary (coupler / cross_resonance / zz_drive).
    state.pairGate = d.pairGate || "cz_tunable";
    if (state.pairGate === "coupler") state.pairGate = "cz_tunable";
    else if (state.pairGate === "cross_resonance" || state.pairGate === "zz_drive") state.pairGate = "cr";
    // Chip architecture: trust a saved value, else derive from the (qubitFlux,
    // pairGate) pair so pre-redesign drafts get a sensible explicit architecture.
    state.chipArch = (d.chipArch && CHIP_ARCH[d.chipArch])
      ? d.chipArch
      : (!state.qubitFlux ? "fixed_frequency"
         : (state.pairGate === "cz_fixed" ? "flux_tunable_fixed_coupler" : "flux_tunable_coupler"));
  }

  // Paint the steps that render() / the bind functions do not repaint from
  // state (the plain text inputs). Called once after restoring a draft.
  function repaintFromState() {
    var net = state.spec.network || {};
    var host = document.getElementById("gen-net-host");
    var cluster = document.getElementById("gen-net-cluster");
    var port = document.getElementById("gen-net-port");
    if (host) host.value = net.host || "";
    if (cluster) cluster.value = net.cluster_name || "";
    if (port) port.value = (net.port == null ? "" : net.port);
    var qc = document.getElementById("gen-qubit-count");
    if (qc) qc.value = state.spec.qubits.length;
    var mux = document.getElementById("gen-mux-size");
    if (mux) mux.value = state.muxSize;
    var out = document.getElementById("gen-output-path");
    if (out) out.value = state.outputPath;
    syncScriptsControls();
    // Restore line-type checkboxes from draft.
    syncLineTypeToggles();
  }

  // Auto-focus a step's primary control on entry, for keyboard-only flow.
  function focusStep(step) {
    var id = { 3: "gen-chassis-count", 4: "gen-qubit-count",
               5: "gen-allocate-btn" }[step];
    if (!id) return;
    var el = document.getElementById(id);
    if (!el) return;
    el.focus();
    if (el.select) el.select();   // number inputs: select so typing replaces
  }

  // Discard the draft and start the wizard over.
  function resetWizard() {
    if (!window.confirm(
        "Discard everything entered in this wizard and start over?")) {
      return;
    }
    try { sessionStorage.removeItem(DRAFT_KEY); } catch (e) {}
    state.step = 1;
    state.spec = freshSpec();
    state.env = null;
    state.allocation = null;
    state.pairsTouched = false;
    state.wiringTouched = false;
    state.naming = { preset: "one_based", prefix: "q", start: 1 };
    state.namesTouched = false;
    state.qubitFlux = true;
    state.couplerFlux = true;
    // Reset the chip architecture + gate too — otherwise a previously-chosen
    // fixed-frequency (CR) chip silently survives Reset and the user rebuilds a
    // CR chip by accident.
    state.chipArch = "flux_tunable_coupler";
    state.pairGate = "cz_tunable";
    state.muxSize = 6;
    state.outputPath = "";
    state.scriptsEnabled = false;
    state.scriptsPath = "";
    try {
      localStorage.removeItem("quam_gen_output_path");
      localStorage.removeItem("quam_gen_scripts_path");
    } catch (e) {}
    ["gen-net-host", "gen-net-cluster", "gen-net-port",
     "gen-output-path", "gen-scripts-path"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.value = "";
    });
    syncScriptsControls();
    var archSel = document.getElementById("gen-chip-arch");
    if (archSel) archSel.value = state.chipArch;
    var cc = document.getElementById("gen-chassis-count");
    if (cc) cc.value = 5;
    var qc = document.getElementById("gen-qubit-count");
    if (qc) qc.value = 0;
    var mux = document.getElementById("gen-mux-size");
    if (mux) mux.value = 6;
    setChassisCount(5);   // re-seed 5 OPX1000 chassis (also renders the grid)
    setQubitCount(0);     // clears qubits / pairs / TWPAs and re-renders
    goToStep(1);
  }

  function init() {
    var r = root();
    // Idempotent: skip if absent or already wired (HTMX re-swaps a fresh node).
    if (!r || r._quamGenInit) return;
    r._quamGenInit = true;

    // Restore an in-progress draft if one exists, else start fresh.
    var draft = loadDraft();
    if (draft) {
      applyDraft(draft);
    } else {
      state.step = 1;
      state.spec = freshSpec();
      state.env = null;
      state.allocation = null;
      state.pairsTouched = false;
      state.wiringTouched = false;
      state.muxSize = 6;
      // Fresh session: recall the last output folder (durable mirror) — a
      // new browser session used to start with the path blank every time.
      state.outputPath = "";
      try { state.outputPath = localStorage.getItem("quam_gen_output_path") || ""; }
      catch (e) { /* private mode */ }
      var outEl = document.getElementById("gen-output-path");
      if (outEl && state.outputPath) outEl.value = state.outputPath;
    }

    // Bottom nav + the top header mirror share the same handlers.
    ["gen-back", "gen-back-top"].forEach(function (id) {
      var b = document.getElementById(id);
      if (b) b.addEventListener("click", function () { goToStep(state.step - 1); });
    });
    ["gen-next", "gen-next-top"].forEach(function (id) {
      var n = document.getElementById(id);
      if (n) n.addEventListener("click", tryNext);
    });

    // Step 5's inline Back/Next live on the Auto-allocate row.
    var wBack = document.getElementById("gen-wiring-back");
    var wNext = document.getElementById("gen-wiring-next");
    if (wBack) wBack.addEventListener("click", function () { goToStep(state.step - 1); });
    if (wNext) wNext.addEventListener("click", tryNext);

    var reset = document.getElementById("gen-reset");
    if (reset) reset.addEventListener("click", resetWizard);

    r.querySelectorAll("#gen-steps li").forEach(function (li) {
      li.addEventListener("click", function () {
        jumpToStep(Number(li.dataset.step));
      });
    });

    bindNetworkStep();
    bindChassisStep();
    bindQubitsStep();
    bindWiringStep();
    bindOutputStep();
    if (draft) repaintFromState();
    render();
    loadEnvs();
  }

  document.addEventListener("DOMContentLoaded", init);
  // The wizard arrives via an HTMX swap into #table-pane; init() is guarded
  // so calling it on every swap is safe. Listen on `document` (not
  // document.body): this script loads in <head>, before <body> exists, and
  // `document.body` would be null here. HTMX events bubble to document.
  document.addEventListener("htmx:afterSwap", init);

  // Save the in-progress wizard right before HTMX swaps #table-pane away
  // (e.g. the user opens another sidebar page) so init() can restore it.
  document.addEventListener("htmx:beforeSwap", function (evt) {
    if (!evt.detail || !evt.detail.target) return;
    if (evt.detail.target.id !== "table-pane") return;
    if (!root()) return;   // the wizard isn't currently mounted
    captureDomFields();
    saveDraft();
  });

  // ── Wizard field undo (Ctrl+Z) ────────────────────────────────────────────
  // A client-side undo stack for COMMITTED wizard field edits (value typed →
  // change fired on blur/Enter). Ctrl+Z restores the previous value and
  // re-dispatches input+change so every existing per-field listener re-syncs
  // `state` (and the live preview) exactly as if the user had retyped it.
  // While the wizard is mounted, Ctrl+Z is wizard-scoped — it must never fall
  // through to the app-wide POST /undo (which would silently revert an
  // unrelated CHIP edit while the user is looking at the wizard).
  var _wizStack = [];          // [{el, id, old}]
  var _WIZ_STACK_CAP = 100;
  var _wizApplying = false;

  function _wizField(t) {
    if (!t || !t.matches) return null;
    if (!t.matches('input, select, textarea')) return null;
    if (t.type === 'radio' || t.type === 'file' || t.type === 'button') return null;
    var r = root();
    return (r && r.contains(t)) ? t : null;
  }
  function _wizVal(el) {
    return el.type === 'checkbox' ? el.checked : el.value;
  }
  // Snapshot the committed value on focus so `change` knows what it replaced.
  document.addEventListener('focusin', function (evt) {
    var el = _wizField(evt.target);
    if (el) el.__wizPrev = _wizVal(el);
  });
  document.addEventListener('change', function (evt) {
    if (_wizApplying) return;
    var el = _wizField(evt.target);
    if (!el) return;
    var old = el.__wizPrev;
    if (old === undefined) {
      // No focusin snapshot (e.g. programmatic path): a checkbox toggle is
      // still recoverable (old = the inverse); anything else can't be.
      if (el.type === 'checkbox') old = !el.checked;
      else return;
    }
    var now = _wizVal(el);
    if (old === now) { el.__wizPrev = now; return; }
    _wizStack.push({ el: el, id: el.id || null, old: old });
    if (_wizStack.length > _WIZ_STACK_CAP) _wizStack.shift();
    el.__wizPrev = now;
  });

  window._wizUndo = {
    // Returns true when the event was CONSUMED (wizard mounted). Called by the
    // app-wide Ctrl+Z handler before it posts the server /undo.
    tryUndo: function () {
      if (!root()) return false;   // wizard not on screen → not ours
      while (_wizStack.length) {
        var entry = _wizStack.pop();
        var el = (entry.el && entry.el.isConnected) ? entry.el
               : (entry.id ? document.getElementById(entry.id) : null);
        // A step re-render replaced the element and it carries no id —
        // skip to the next undoable entry (documented limitation).
        if (!el || !el.isConnected) continue;
        _wizApplying = true;
        try {
          if (el.type === 'checkbox') el.checked = !!entry.old;
          else el.value = entry.old;
          el.__wizPrev = _wizVal(el);
          // Re-run every existing sync listener (state capture, live preview).
          el.dispatchEvent(new Event('input',  { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        } finally { _wizApplying = false; }
        try { captureDomFields(); saveDraft(); } catch (e) { /* draft best-effort */ }
        try { el.scrollIntoView({ block: 'nearest' }); } catch (e) { /* jsdom/old engines */ }
        el.classList.add('wiz-undo-flash');
        setTimeout(function () { el.classList.remove('wiz-undo-flash'); }, 900);
        if (window.showToast) {
          window.showToast('Undid: restored ' +
              (el.getAttribute('aria-label') || el.name || el.id || 'field') +
              ' to "' + entry.old + '"', 'success');
        }
        return true;
      }
      // Wizard mounted but nothing to undo — still consume (never fall through
      // to the server /undo while the user is inside the wizard).
      if (window.showToast) window.showToast('Nothing to undo in the wizard.', 'info');
      return true;
    },
  };

  // Pre-fill the wizard from an existing chip's reconstructed spec (Re-generate).
  // opts: {mode, buildEndpoint, sourcePath, step, env, outputPath}. Reuses the
  // draft-restore path so every step's UI + derived flags repaint from the spec;
  // pairsTouched/wiringTouched are set so the reconstructed pairs/lines aren't
  // auto-refilled. Idempotent — call after QuamGen.init() has mounted the wizard.
  function hydrateFromSpec(spec, opts) {
    var o = opts || {};
    // Re-generate owns the wizard state fresh from the source chip: drop any
    // stale/Generate draft so (a) it can't interfere here and (b) this named,
    // non-contiguous spec never lingers as a draft for a later plain Generate.
    try { sessionStorage.removeItem(DRAFT_KEY); } catch (e) {}
    // Normalize TWPAs to the wizard's object shape. Old exact-spec sidecars
    // (and the pre-fix reconstructor) carry bare id strings; the step-4 rows
    // bind twpa.id, so strings rendered as broken empty rows and edits
    // silently no-op'd on the primitives (review-r6 TWPA-loss report).
    if (spec && spec.twpas) {
      spec.twpas = spec.twpas.map(function (t) {
        return (t && typeof t === "object") ? t
             : { id: String(t), qubits: [] };
      });
    }
    var pg = (spec && spec.pair_gate) || "cz_tunable";
    var arch = pg === "cr" ? "fixed_frequency"
             : pg === "cz_fixed" ? "flux_tunable_fixed_coupler"
             : "flux_tunable_coupler";
    applyDraft({
      v: DRAFT_VERSION, step: o.step || 1, env: o.env || null,
      spec: spec, allocation: o.allocation || null,
      outputPath: o.outputPath || "",
      pairGate: pg, chipArch: arch,
      pairsTouched: true, wiringTouched: true,
      // Source-chip names are authoritative — detach every scheme expectation
      // so no gate/count-change can rename them out from under the value-merge.
      namesTouched: true
    });
    applyChipArch(state.chipArch);            // sync qubitFlux / couplerFlux / pairGate
    state.mode = o.mode || "regenerate";
    state.buildEndpoint = o.buildEndpoint || "/regenerate/build";
    state.sourcePath = o.sourcePath || null;
    repaintFromState();
    // repaintFromState() only syncs the scalar inputs — the Chassis grid, the
    // Qubits pair list, and the chip board render separately. Force them from the
    // spec now, else the pre-filled instruments/pairs stay invisible (or a stale
    // generate-draft's chassis shows) until the user re-enters each step.
    var cc = document.getElementById("gen-chassis-count");
    if (cc) cc.value = ((state.spec.instruments || {}).controllers || []).length;
    if (typeof renderChassis === "function") renderChassis();
    if (typeof renderQubitsStep === "function") renderQubitsStep();
    render();
  }

  window.QuamGen = {
    state: state,
    goToStep: goToStep,
    tryNext: tryNext,
    renumberContiguous: renumberContiguous,
    init: init,
    reloadEnvs: loadEnvs,
    useCustomEnv: useCustomEnv,
    hydrateFromSpec: hydrateFromSpec,
    // Pure allocation internals, exposed for the node selfcheck harness only
    // (tests/generate_power_selfcheck.cjs) — not a public API.
    _test: {
      solveLoWindow: solveLoWindow,
      solvePortFsp: solvePortFsp,
      ampForTarget: ampForTarget,
      computeLoAssignments: computeLoAssignments,
      recomputeReadoutPower: recomputeReadoutPower,
      recomputeXyPower: recomputeXyPower,
      PWR: PWR,
      validateCellValue: validateCellValue,
      validateAllPopCells: validateAllPopCells,
      VALIDATE_RANGES: VALIDATE_RANGES,
      setValidateDebounce: function (ms) { VALIDATE_DEBOUNCE_MS = ms; },
      czAutoOrient: czAutoOrient,
      czOrderStatus: czOrderStatus,
      flipPairOrder: flipPairOrder,
      schemeNames: schemeNames,
      validateQubitName: validateQubitName,
      applyNamingScheme: applyNamingScheme,
      renameQubit: renameQubit,
      expectedNamesOrNull: expectedNamesOrNull,
      applyQubitIdMap: applyQubitIdMap,
      capturePresetSections: capturePresetSections,
      applyPreset: applyPreset,
      applyPortCsv: applyPortCsv,
      pinToChannel: pinToChannel,
      deriveLines: deriveLines,
      pairPopCols: pairPopCols,
      ALLOC_KEY: ALLOC_KEY,
      state: state
    }
  };
})();
