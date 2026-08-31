/* jsdom selfcheck for QDAC-II on the Instrument Wiring diagram (docs/136),
 * driving the REAL app.js renderInstrumentWiring + _showPortPopup.
 *
 * Two things the diagram could not say before:
 *
 *  1. A **bias-tee** flux port is driven by TWO instruments — a QDAC-II holds
 *     the DC operating point while this OPX analog output plays the pulses on
 *     top of it. Drawn as any other z port, it claimed to be the whole story.
 *  2. A **QDAC trigger** marker is shared on purpose: one OPX digital output
 *     feeds one QDAC ext input, and that arms every channel armed on it. On
 *     the real 20Q chip that is 11 qubits on 4 ports. Labelled only "digital",
 *     a shared port reads as a wiring collision.
 *
 * Pins:
 *   Q1. a bias-tee z port is marked, a plain z port is NOT (no false positive);
 *   Q2. the mark IS a recolour (r2, customer-directed): its own amber fill
 *       + solid slate outline + dark label — the dashed ring was invisible;
 *   Q3. hovering it answers for BOTH instruments in one popup;
 *   Q4. hovering a plain z port gains nothing (the fields stay as they were);
 *   Q5. a QDAC trigger's popup names the ext input it is cabled to;
 *   Q6. a non-QDAC digital marker does not claim one.
 *
 * Run: node tests/instrument_qdac_selfcheck.cjs (driven by
 * tests/test_qdac_component.py).
 */
'use strict';

const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom not installed');
  process.exit(2);
}

const STATIC = path.join(__dirname, '..', 'quam_state_manager', 'web', 'static');

let fails = 0;
let checks = 0;
function ok(c, m) {
  checks++;
  if (!c) { console.error('FAIL: ' + m); fails++; } else { console.log('ok - ' + m); }
}

const dom = new JSDOM(
  `<!doctype html><html><body>
     <div id="instrument-diagram"></div>
     <div id="port-popup" class="hidden">
       <span id="popup-label"></span>
       <span id="popup-role-badge"></span>
       <div id="popup-body"></div>
     </div>
   </body></html>`,
  { url: 'http://localhost/instrument', pretendToBeVisual: true });
const { window } = dom;

global.window = window;
global.document = window.document;
global.CSS = window.CSS;
global.CustomEvent = window.CustomEvent;
global.Event = window.Event;
global.KeyboardEvent = window.KeyboardEvent;
try { global.navigator = window.navigator; } catch (e) { /* node's own */ }
global.location = window.location;
global.MutationObserver = window.MutationObserver;
global.IntersectionObserver = class { observe() {} disconnect() {} unobserve() {} };
window.IntersectionObserver = global.IntersectionObserver;
global.ResizeObserver = class { observe() {} disconnect() {} unobserve() {} };
window.ResizeObserver = global.ResizeObserver;
global.requestAnimationFrame = (f) => setTimeout(f, 0);
window.requestAnimationFrame = global.requestAnimationFrame;

const store = {};
const ls = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; },
};
global.localStorage = ls;
Object.defineProperty(window, 'localStorage', { value: ls, configurable: true });
global.sessionStorage = ls;

window.htmx = { ajax() {}, trigger() {}, process() {}, on() {} };
global.htmx = window.htmx;
window.fetch = global.fetch = () => Promise.resolve(
  { status: 200, json: () => Promise.resolve({}), text: () => Promise.resolve('') });

window.eval(fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8'));

/* An LF-FEM carrying all four shapes at once: a plain flux port, a bias-tee
 * flux port, a QDAC trigger shared by three qubits, and an ordinary readout
 * marker that has nothing to do with the QDAC. */
function rack() {
  return {
    controllers: {
      '1': {
        max_output_port: 8,
        fems: {
          '4': {
            type: 'lf-fem',
            output_ports: {
              '1': [{ role: 'z', element: 'q2', label: 'q2.z', port_type: 'out',
                      flux_point: 'joint', joint_offset: 0.0627 }],
              '2': [{ role: 'z', element: 'q5', label: 'q5.z', port_type: 'out',
                      flux_point: 'joint', joint_offset: 0.0,
                      qdac_shared: true, qdac_channel: 7,
                      qdac_dc_offset: -0.0907, qdac_trigger_port: 'ext3' }],
            },
            input_ports: {},
            digital_ports: {
              '1': [
                { role: 'digital', element: 'q1', label: 'q1.trigger',
                  marker: 'trigger', source: 'z.opx_trigger_out',
                  shareable: true, qdac_trigger: true, qdac_ext: 'ext1' },
                { role: 'digital', element: 'q9', label: 'q9.trigger',
                  marker: 'trigger', source: 'z.opx_trigger_out',
                  shareable: true, qdac_trigger: true, qdac_ext: 'ext1' },
              ],
              '5': [
                { role: 'digital', element: 'q2', label: 'q2.ro_marker',
                  marker: 'ro_marker', source: 'resonator', shareable: false },
              ],
            },
          },
        },
      },
    },
  };
}

const host = window.document.getElementById('instrument-diagram');
Object.defineProperty(host, 'clientWidth', { value: 1400, configurable: true });
window.renderInstrumentWiring('instrument-diagram', rack(), {});

function groupFor(element, role) {
  const all = host.querySelectorAll('g.iw-port-circle[data-element="' + element + '"]');
  for (let i = 0; i < all.length; i++) {
    if (!role || all[i].getAttribute('data-role') === role) return all[i];
  }
  return null;
}

/* -- Q1 / Q2: the mark, and only where it belongs --------------------- */
const tee = groupFor('q5', 'z');
const plain = groupFor('q2', 'z');
ok(!!tee && !!plain, 'Q1 both flux ports rendered');
ok(tee && tee.getAttribute('data-qdac-shared') === '1',
   'Q1 the bias-tee flux port is marked');
ok(plain && plain.getAttribute('data-qdac-shared') === null,
   'Q1 a plain flux port is NOT marked (no false positive)');

// docs/136 r2 — a RECOLOUR now, by customer direction: the first pass marked
// the port with a dashed slate ring over the z blue, and the verdict from the
// real screen was that it is invisible at port size. The port gets its own
// amber fill (roleColors.z_qdac) and a solid slate outline tying it to the
// trigger. docs/141 4ah (user): the fill is DARK amber now and the label is
// white like every other port's -- a port lettered differently from its
// neighbours reads as a rendering fault, not as a role.
const teeCircles = tee ? tee.querySelectorAll('circle') : [];
const plainCircles = plain ? plain.querySelectorAll('circle') : [];
ok(teeCircles.length === plainCircles.length,
   'Q2 no extra ring any more — the mark is the fill itself');
ok(teeCircles[0].getAttribute('fill') !== plainCircles[0].getAttribute('fill'),
   'Q2 the bias-tee fill DIFFERS from a plain z port');
ok(/a9791c/i.test(teeCircles[0].getAttribute('fill') || ''),
   'Q2 ...and is the DARK amber the palette reserves for it ('
   + teeCircles[0].getAttribute('fill') + ')');
ok(teeCircles[0].getAttribute('stroke') === '#54617a'
   && teeCircles[0].getAttribute('stroke-width') === '2',
   'Q2 solid slate outline ties it to the digital trigger');
ok(!teeCircles[0].getAttribute('stroke-dasharray'),
   'Q2 no dash — that was the invisible idiom being replaced');
const teeLabel = tee.querySelector('text');
const plainLabel = plain.querySelector('text');
ok(teeLabel && plainLabel
   && teeLabel.getAttribute('fill') === plainLabel.getAttribute('fill'),
   'Q2 ONE label colour app-wide: the bias-tee label matches every other port ('
   + (teeLabel && teeLabel.getAttribute('fill')) + ')');
ok(plainLabel && plainLabel.getAttribute('fill') === '#ffffff',
   'Q2 ...and that colour is white');

/* -- Q3 / Q4: one hover answers for both instruments ------------------ */
function popupText(g) {
  const ev = new window.MouseEvent('mouseenter', { bubbles: false });
  Object.defineProperty(ev, 'clientX', { value: 100 });
  Object.defineProperty(ev, 'clientY', { value: 100 });
  g.dispatchEvent(ev);
  return window.document.getElementById('popup-body').textContent;
}
const teeText = popupText(tee);
ok(/flux point/.test(teeText) && /joint offset/.test(teeText),
   'Q3 the flux half is still there');
ok(/QDAC channel/.test(teeText) && /7/.test(teeText),
   'Q3 ...and the QDAC channel is named');
ok(/QDAC DC offset/.test(teeText) && /-0\.09/.test(teeText),
   'Q3 ...and the DC offset the QDAC actually holds');
ok(/QDAC trigger/.test(teeText) && /ext3/.test(teeText),
   'Q3 ...and which ext input steps it');
ok(/bias tee/i.test(teeText),
   'Q3 ...and the popup says what this arrangement IS');

const plainText = popupText(plain);
ok(/flux point/.test(plainText), 'Q4 a plain z port still describes itself');
ok(!/QDAC/.test(plainText),
   'Q4 ...and never claims a QDAC it does not have');

/* -- Q5 / Q6: the shared trigger explains itself ---------------------- */
const trig = groupFor('q1', 'digital');
ok(!!trig, 'Q5 the QDAC trigger marker rendered');
const trigText = popupText(trig);
ok(/QDAC input/.test(trigText) && /ext1/.test(trigText),
   'Q5 the popup names the ext input — the reason a port can be shared');
ok(/marker/.test(trigText), 'Q5 ...without losing the generic digital facts');

const roMarker = groupFor('q2', 'digital');
ok(!!roMarker, 'Q6 an ordinary digital marker rendered');
const roText = popupText(roMarker);
ok(!/QDAC/.test(roText), 'Q6 ...and does not claim to be a QDAC trigger');

// Both qubits sharing port 1 are drawn — the sharing is visible, not collapsed.
const sharedGroups = host.querySelectorAll(
  'g.iw-port-circle[data-role="digital"]');
ok(sharedGroups.length >= 3,
   'Q6 every element on a shared port is drawn (got ' + sharedGroups.length + ')');

window.close();
const summary = fails
  ? fails + ' check(s) failed\n'
  : 'instrument_qdac_selfcheck: all checks passed (' + checks + ' assertions)\n';
process.stdout.write(summary, function () { process.exit(fails ? 1 : 0); });
