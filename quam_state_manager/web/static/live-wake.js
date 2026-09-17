/* LiveWake (docs/141 §4p) — the server says "a run folder changed", the page
   polls NOW. One long-poll at a time to GET /datasets/wait?since=<tick>: the
   server answers as soon as its watcher's tick moves (a new run directory, a
   file landing in the newest one) or after ~25 s. On a change this dispatches
   `sm:runs-changed` on document (the new-run popup poll listens) and calls
   DatasetVirtual.pollNow() (the Datasets table's delta poll). The existing
   polls keep their own cadence as the safety net; this only makes them fire
   at once. Paused while the tab is hidden, exponential backoff on failure,
   never two requests in flight. */
(function () {
    'use strict';
    if (window.LiveWake) return;
    var WAIT_TIMEOUT_S = 25, ABORT_MS = 35000, BACKOFF_MIN_MS = 1000, BACKOFF_MAX_MS = 30000;
    // docs/141 4ac: the server bounds how many waits may block at once and
    // answers `saturated` AT ONCE when the bound is reached. Going straight
    // back to waiting on that answer is a busy loop (measured ~73 req/s from
    // one tab), so a saturated answer is the one success that waits.
    var SATURATED_MS = 5000;
    // tick -1 = "not yet in contact": the first request is a handshake the
    // server answers at once with its current tick (no wake — the page has
    // just loaded and polled). Every later answer with a moved tick is a real
    // change, including the very first change on a freshly started server.
    var tick = -1, failures = 0, ctl = null, abortTimer = null, retryTimer = null;
    var inFlight = false, stopped = false, wakes = 0, requests = 0;
    // docs/191 P01: a second cursor, on the AGENT clock. The server bumps
    // `agent_seq` on every agent event, but that never moved the run watcher's
    // tick -- so a page that was not the Agent page learnt an approval was
    // waiting only when the pill's own 60 s safety timer came round. -1 until
    // the handshake answers, so we never wake on the first reading.
    var aseq = -1, agentWakes = 0;

    function wake(detail, runsChanged) {
        wakes++;
        var d = detail || {};
        // Two signals, two events: `sm:runs-changed` still means a run folder
        // changed (the new-run popup and the Datasets table read it that way),
        // and an agent-only wake must not send them looking for a run.
        if (runsChanged) {
            try { document.dispatchEvent(new CustomEvent('sm:runs-changed', { detail: d })); } catch (e) {}
            try { if (window.DatasetVirtual && typeof DatasetVirtual.pollNow === 'function') DatasetVirtual.pollNow(); } catch (e) {}
        }
        if (d.agent_changed) {
            agentWakes++;
            try { document.dispatchEvent(new CustomEvent('sm:agent-changed', { detail: d })); } catch (e) {}
        }
    }
    function schedule(ms) {
        if (retryTimer) clearTimeout(retryTimer);
        retryTimer = setTimeout(function () { retryTimer = null; loop(); }, ms);
    }
    function loop() {
        if (stopped || inFlight) return;
        if (document.hidden) return;                    // resumed by visibilitychange
        inFlight = true;
        requests++;
        ctl = (typeof AbortController === 'function') ? new AbortController() : null;
        var url = '/datasets/wait?since=' + tick + '&aseq=' + aseq + '&timeout=' + WAIT_TIMEOUT_S;
        abortTimer = setTimeout(function () { try { if (ctl) ctl.abort(); } catch (e) {} }, ABORT_MS);
        var opts = { credentials: 'same-origin', cache: 'no-store' };
        if (ctl) opts.signal = ctl.signal;
        fetch(url, opts)
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (d) {
                clearTimeout(abortTimer); inFlight = false; failures = 0;
                if (!d || typeof d.tick !== 'number') throw new Error('bad payload');
                var handshake = (tick < 0);
                var changed = !!d.changed && !handshake;
                var agentChanged = !!d.agent_changed && !handshake;
                tick = d.tick;
                if (typeof d.agent_seq === 'number') aseq = d.agent_seq;
                if (changed || agentChanged) {
                    wake({ tick: tick, agent_seq: d.agent_seq, agent_changed: agentChanged }, changed);
                }
                schedule(d.saturated ? SATURATED_MS : 0);   // straight back to waiting
            })
            .catch(function () {
                clearTimeout(abortTimer); inFlight = false;
                if (stopped) return;
                if (document.hidden) return;                // an abort on hide is not a failure
                failures++;
                schedule(Math.min(BACKOFF_MIN_MS * Math.pow(2, failures - 1), BACKOFF_MAX_MS));
            });
    }
    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
            try { if (ctl && inFlight) ctl.abort(); } catch (e) {}
        } else {
            failures = 0;
            loop();
        }
    });
    window.LiveWake = {
        start: loop,
        stop: function () { stopped = true; if (retryTimer) clearTimeout(retryTimer); try { if (ctl && inFlight) ctl.abort(); } catch (e) {} },
        state: function () { return { tick: tick, aseq: aseq, failures: failures, inFlight: inFlight, wakes: wakes, agentWakes: agentWakes, requests: requests, stopped: stopped }; },
        _wake: wake,
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', loop);
    else loop();
})();
