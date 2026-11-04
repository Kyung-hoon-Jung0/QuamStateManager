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

  // 2. The links.
  var CARD = '[class*="SnapshotsTimeline-module__wrapper"] [class*="ListCard-module__listCard"]';
  var META = '[class*="ExecutionCard-module__executionMetaItem"] span';

  function runIdOf(card) {
    var ids = [];
    var spans = card.querySelectorAll(META);
    for (var i = 0; i < spans.length; i++) {
      if (spans[i].closest(CARD) !== card) continue;          // a nested card's id is not ours
      var t = (spans[i].textContent || '').trim();
      var m = /^#(\d{1,9})$/.exec(t);
      if (m) ids.push(m[1]);
    }
    return ids.length === 1 ? ids[0] : null;
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

  function linkFor(id) {
    var a = document.createElement('a');
    a.setAttribute('data-qsm-open', id);
    a.setAttribute('href', '/qualibrate-run/' + id);
    a.setAttribute('title', 'Open run #' + id + ' in QUAM State Manager');
    a.textContent = 'Open in QSM';
    a.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      openRun(a.getAttribute('href'));
    });
    return a;
  }

  function pass() {
    try {
      var cards = document.querySelectorAll(CARD);
      for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var id = runIdOf(card);
        var mine = null;
        for (var c = card.firstElementChild; c; c = c.nextElementSibling) {
          if (c.hasAttribute('data-qsm-open')) { mine = c; break; }
        }
        if (!id) { if (mine) mine.remove(); continue; }
        if (mine && mine.getAttribute('data-qsm-open') === id) continue;
        if (mine) mine.remove();
        card.appendChild(linkFor(id));
      }
    } catch (e) { /* add nothing */ }
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
  window.__qsmProxy = { pass: pass, runIdOf: runIdOf };
})();
