/* QUAlibrate's UI through SM: an "Open in QSM" link on each run card.
 *
 * Loaded by the one tag SM inserts into QUAlibrate's root document
 * (web/qualibrate_proxy.py). Rules: never throw into QUAlibrate, add nothing
 * when the expected markup is not there, touch nothing of QUAlibrate's own.
 */
(function () {
  'use strict';
  var me = document.currentScript;
  var WS_TARGET = me && me.getAttribute('data-ws');      // ws://host:port/
  var PREFIX = (me && me.getAttribute('data-prefix')) || '/qualibrate-ui/';

  // 0. The page runs sandboxed (opaque origin), where reading localStorage
  //    throws; QUAlibrate reads it unguarded, so it gets an in-memory one.
  ['localStorage', 'sessionStorage'].forEach(function (name) {
    try { window[name].length; return; } catch (e) { /* blocked: shim it */ }
    try {
      var data = Object.create(null);
      var store = {
        getItem: function (k) { k = String(k); return k in data ? data[k] : null; },
        setItem: function (k, v) { data[String(k)] = String(v); },
        removeItem: function (k) { delete data[String(k)]; },
        clear: function () { data = Object.create(null); },
        key: function (i) { return Object.keys(data)[i] || null; }
      };
      Object.defineProperty(store, 'length', { get: function () { return Object.keys(data).length; } });
      Object.defineProperty(window, name, { value: store, configurable: true });
    } catch (e) { /* nothing more to do */ }
  });

  try { void document.cookie; } catch (e) {
    try {
      var jar = '';
      Object.defineProperty(document, 'cookie', {
        configurable: true,
        get: function () { return jar; },
        set: function () { /* the sandboxed page keeps no cookies */ }
      });
    } catch (e2) { /* nothing more to do */ }
  }

  // 0b. QUAlibrate's own calls carry the page's proxy token (D-19); SM refuses
  //     an opaque-origin request without it, so no other page can drive the
  //     proxy. Only urls under this page's own prefix on this host get it.
  var TOKEN = me && me.getAttribute('data-token');
  var TOKEN_HEADER = 'X-QSM-Proxy-Token';
  function ours(url) {
    try {
      var u = new URL(String(url), document.baseURI);
      return u.host === location.host && u.pathname.indexOf(PREFIX) === 0;
    } catch (e) { return false; }
  }
  if (TOKEN) {
    try {
      var nativeFetch = window.fetch;
      if (nativeFetch) {
        window.fetch = function (input, init) {
          try {
            var isReq = input && typeof input === 'object' && 'url' in input;
            if (ours(isReq ? input.url : input)) {
              init = init ? Object.assign({}, init) : {};
              var h = new Headers(init.headers || (isReq ? input.headers : undefined));
              h.set(TOKEN_HEADER, TOKEN);
              init.headers = h;
            }
          } catch (e) { /* send it as it was */ }
          return nativeFetch.call(this, input, init);
        };
      }
      var XO = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
      if (XO) {
        var nOpen = XO.open, nSend = XO.send;
        XO.open = function (m, url) { this.__qsmOurs = ours(url); return nOpen.apply(this, arguments); };
        XO.send = function () {
          try { if (this.__qsmOurs) this.setRequestHeader(TOKEN_HEADER, TOKEN); } catch (e) { /* as it was */ }
          return nSend.apply(this, arguments);
        };
      }
    } catch (e) { /* leave fetch/XHR alone */ }
  }

  // 1. WebSockets go straight to QUAlibrate (SM's server cannot upgrade one).
  //    Only urls under this page's own prefix are rewritten; on any doubt the
  //    original url is used unchanged.
  try {
    var Native = window.WebSocket;
    if (Native && WS_TARGET && /^wss?:\/\/[^/]+\/$/.test(WS_TARGET)) {
      var rewrite = function (url) {
        try {
          var u = String(url);
          var m = /^wss?:\/\/([^/]+)(\/.*)$/.exec(u);
          if (!m || m[1] !== location.host || m[2].indexOf(PREFIX) !== 0) return url;
          return WS_TARGET + m[2].slice(PREFIX.length);
        } catch (e) { return url; }
      };
      var Wrapped = function (url, protocols) {
        var to = rewrite(url);
        return protocols === undefined ? new Native(to) : new Native(to, protocols);
      };
      Wrapped.prototype = Native.prototype;
      ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'].forEach(function (k) { Wrapped[k] = Native[k]; });
      window.WebSocket = Wrapped;
    }
  } catch (e) { /* leave the native WebSocket alone */ }

  // 2. The links. Two QUAlibrate generations, two places each (D-9, D-22):
  //    1.5.x  readable CSS-module classes ("ListCard-module__listCard"):
  //           a History card (its "#25" span) and the title bar of the open
  //           run ("25 name"), the latter only when it names the SELECTED card.
  //    2.x    hashed CSS-module classes ("_idCell_1qftv_52"): a row of the
  //           runs table (its id cell reads "25") and the header of the open
  //           run (one "#25" id span).
  //    Anything else adds nothing.
  var CARD = '[class*="SnapshotsTimeline-module__wrapper"] [class*="ListCard-module__listCard"]';
  var META = '[class*="ExecutionCard-module__executionMetaItem"] span';
  var SELECTED = '[class*="ListCard-module__selected"]';
  var TITLE_H2 = '[class*="TitleBarMenu-module__wrapper"] > h2';

  // a 2.x CSS-module class: "_<local>_<hash>_<line>"
  function mod(el, local) {
    var c = el && el.getAttribute && el.getAttribute('class');
    if (!c) return false;
    var re = new RegExp('^_' + local + '_[A-Za-z0-9-]{3,12}_[0-9]+$');
    var parts = c.split(/\s+/);
    for (var i = 0; i < parts.length; i++) if (re.test(parts[i])) return true;
    return false;
  }
  function byMod(root, tag, local) {
    var out = [], els = root.getElementsByTagName(tag);
    for (var i = 0; i < els.length; i++) if (mod(els[i], local)) out.push(els[i]);
    return out;
  }
  function ownText(el) { return (el.textContent || '').trim(); }

  function runIdOf(card) {
    var ids = [];
    var spans = card.querySelectorAll(META);
    for (var i = 0; i < spans.length; i++) {
      if (spans[i].closest(CARD) !== card) continue;          // a nested card's id is not ours
      var m = /^#(\d{1,9})$/.exec(ownText(spans[i]));
      if (m) ids.push(m[1]);
    }
    return ids.length === 1 ? ids[0] : null;
  }

  // 2.x: a table row's run id = its one id cell, digits only
  function rowIdOf(tr) {
    var cells = [];
    for (var c = tr.firstElementChild; c; c = c.nextElementSibling) if (mod(c, 'idCell')) cells.push(c);
    if (cells.length !== 1) return null;
    var td = cells[0], t = '';
    for (var k = td.firstChild; k; k = k.nextSibling) {        // our own link is not part of the id
      if (k.nodeType === 1 && k.hasAttribute('data-qsm-open')) continue;
      t += k.textContent || '';
    }
    var m = /^(\d{1,9})$/.exec(t.trim());
    return m ? { id: m[1], host: td } : null;
  }

  // 1.5.x: the title bar names the open run; trust it only when it agrees
  // with the selected History card (exact match or nothing)
  function titleTarget() {
    var h2s = document.querySelectorAll(TITLE_H2);
    if (h2s.length !== 1) return null;
    var sel = document.querySelectorAll(CARD + SELECTED);
    if (sel.length !== 1) return null;
    var id = runIdOf(sel[0]);
    if (!id) return null;
    var m = /^(\d{1,9})\s/.exec(ownText(h2s[0]) + ' ');
    // beside the title, not inside it: the title keeps its own layout
    return (m && m[1] === id) ? { id: id, host: h2s[0].parentElement, after: h2s[0] } : null;
  }

  // 2.x: the open run's header has exactly one "#<n>" id span
  function headerTargets() {
    var out = [];
    var spans = byMod(document, 'span', 'id');
    for (var i = 0; i < spans.length; i++) {
      var sp = spans[i];
      var m = /^#(\d{1,9})$/.exec(ownText(sp));
      var head = sp.parentElement && sp.parentElement.closest('div');
      var h = head;
      while (h && !mod(h, 'header')) h = h.parentElement;
      if (!m || !h) continue;
      if (byMod(h, 'span', 'id').length !== 1) continue;
      out.push({ id: m[1], host: sp.parentElement });
    }
    return out;
  }

  function openRun(href) {
    try {
      if (window.parent !== window) {                         // the workbench pane
        window.parent.postMessage({ type: 'qsm-open-run', href: href }, '*');
        return;
      }
    } catch (e) { /* fall through to a tab */ }
    window.open(href, 'qsm-run');
  }

  function linkFor(id, where) {
    var a = document.createElement('a');
    a.setAttribute('data-qsm-open', id);
    if (where) a.setAttribute('data-qsm-where', where);
    a.setAttribute('href', '/qualibrate-run/' + id);
    a.setAttribute('title', 'Open run #' + id + ' in QUAM State Manager');
    a.textContent = 'Open in QSM';
    var stop = function (ev) { ev.stopPropagation(); };
    // a 2.x row navigates on press; the link must not
    a.addEventListener('mousedown', stop);
    a.addEventListener('pointerdown', stop);
    a.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      openRun(a.getAttribute('href'));
    });
    return a;
  }

  // keep exactly one link of ours as a direct child of `host`, for `id`
  function place(host, id, where, wanted, after) {
    var mine = null;
    for (var c = host.firstElementChild; c; c = c.nextElementSibling) {
      if (c.hasAttribute('data-qsm-open')) { mine = c; break; }
    }
    if (!id) { if (mine) mine.remove(); return; }
    if (mine && mine.getAttribute('data-qsm-open') === id
        && (!after || mine.previousElementSibling === after)) { wanted.push(mine); return; }
    if (mine) mine.remove();
    var a = linkFor(id, where);
    if (after && after.parentElement === host) host.insertBefore(a, after.nextSibling);
    else host.appendChild(a);
    wanted.push(a);
  }

  function pass() {
    var wanted = [];
    try {
      var cards = document.querySelectorAll(CARD);
      for (var i = 0; i < cards.length; i++) place(cards[i], runIdOf(cards[i]), 'card', wanted);
    } catch (e) { /* add nothing */ }
    try {
      var rows = document.getElementsByTagName('tr');
      for (var r = 0; r < rows.length; r++) {
        var hit = rowIdOf(rows[r]);
        if (hit) place(hit.host, hit.id, 'row', wanted);
      }
    } catch (e) { /* add nothing */ }
    try {
      var t = titleTarget();
      if (t) place(t.host, t.id, 'header', wanted, t.after);
    } catch (e) { /* add nothing */ }
    try {
      var hs = headerTargets();
      for (var j = 0; j < hs.length; j++) place(hs[j].host, hs[j].id, 'header', wanted);
    } catch (e) { /* add nothing */ }
    try {                                                    // a link whose place is gone goes too
      var all = document.querySelectorAll('[data-qsm-open]');
      for (var q = 0; q < all.length; q++) if (wanted.indexOf(all[q]) < 0) all[q].remove();
    } catch (e) { /* leave it */ }
  }

  var queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () { queued = false; pass(); });
  }

  function start() {
    try {
      new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true, characterData: true });
      schedule();
    } catch (e) { /* add nothing */ }
  }
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);

  // test hook (jsdom selfcheck)
  window.__qsmProxy = { pass: pass, runIdOf: runIdOf, rowIdOf: rowIdOf };
})();
