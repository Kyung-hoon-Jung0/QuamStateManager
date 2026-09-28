/* sm-root.js -- where the URL prefix is known on the client (docs/226).

   SM can be mounted under a path prefix by a reverse proxy (`--url-prefix
   /sm`). The server tells the page its mount point ONCE, in
   <html data-root="/sm">, and this file is the only client code that reads
   it. It MUST be the first <script> of every full document: everything after
   it may call window.SM.url / window.SM.path.

     window.SM.root      the prefix, '' at root
     window.SM.url(p)    add the prefix to an app-root-absolute path,
                         idempotently ('/sm/x' stays '/sm/x'); anything that
                         is not a string starting with exactly one '/' --
                         '//cdn/x', 'http://h/x', 'x/y', '?q', '' -- is
                         returned untouched
     window.SM.path(p)   strip the prefix, for comparing a location/request
                         path against an app route ('/sm/diff' -> '/diff')

   Identity at root: when data-root is empty (no prefix configured) NOTHING
   below `window.SM = ...` runs -- no fetch/history wrapper, no htmx
   extension; url() and path() return their argument.

   Under a prefix three request sinks are made prefix-correct here, so the
   ~140 literal fetch('/...') / htmx.ajax(m, '/...') / pushState(.., '/...')
   call sites need no edit:
     1. window.fetch: a root-absolute string gets the prefix (url()); a URL
        or Request object whose target is SAME-ORIGIN and not already under
        the prefix is re-targeted (a Request keeps its method, headers, body
        and options). Cross-origin, protocol-relative, relative and
        already-prefixed inputs pass through as the very same value.
     2. history.pushState / replaceState: the URL argument, same rules.
     3. htmx: an EXTENSION named `sm-root` (enabled by base.html's
        <body hx-ext="sm-root">, rendered only under a prefix) prefixes
        htmx:configRequest's detail.path. An extension, not a listener,
        because htmx 2.0.4's triggerEvent (`he` in htmx.min.js) runs
        dispatchEvent, then the kebab-case dispatch, THEN every extension's
        onEvent -- so this runs after every DOM listener, whenever and on
        whichever element it was registered, and a listener that rewrites or
        replaces the path (diff-panes.js, bulk-edit.js, app.js) is prefixed
        exactly once afterwards. It covers hx-get/hx-post attributes,
        htmx.ajax() and HX-Location follow-ups alike.

   Only `window.*` is referenced, never a bare `document`/`history`/`fetch`:
   the jsdom selfchecks evaluate this file in the Node realm, where window
   properties are not globals. */
(function () {
  var doc = window.document;
  var root = '';
  try { root = doc.documentElement.getAttribute('data-root') || ''; } catch (e) { root = ''; }
  if (root === '/') root = '';
  while (root.length && root.charAt(root.length - 1) === '/') root = root.slice(0, -1);

  // Segment boundary: '/sm' owns '/sm', '/sm/..', '/sm?..', '/sm#..' --
  // never '/smx' or '/sm-x/y', which are app paths that get the prefix.
  function rooted(p) {
    return p === root || p.indexOf(root + '/') === 0
      || p.indexOf(root + '?') === 0 || p.indexOf(root + '#') === 0;
  }
  function url(p) {                                   // add the prefix, idempotently; strings only
    if (!root || typeof p !== 'string' || p.charAt(0) !== '/') return p;
    var c = p.charAt(1);
    if (c === '/' || c === '\\') return p;            // protocol-relative (a URL parser reads '/\' as '//')
    return rooted(p) ? p : root + p;
  }
  function path(p) {                                  // strip the prefix for app-relative comparisons
    if (!root || typeof p !== 'string') return p;
    if (p === root) return '/';
    if (p.indexOf(root + '/') === 0) return p.slice(root.length);
    if (p.indexOf(root + '?') === 0 || p.indexOf(root + '#') === 0) return '/' + p.slice(root.length);
    return p;
  }
  window.SM = { root: root, url: url, path: path };
  if (!root) return;                                  // ---- root: no wrapper, no hook, no extension ----

  var slice = Array.prototype.slice;

  // An absolute URL (a URL object, a Request's url) -> the prefixed href, or
  // null when it must pass untouched: another origin, or already under root.
  function sameOriginTarget(href) {
    var U = window.URL, loc = window.location;
    if (typeof U !== 'function' || !loc) return null;
    var u = new U(href);
    if (u.origin !== loc.origin || rooted(u.pathname)) return null;
    u.pathname = root + u.pathname;
    return u.href;
  }
  function isURL(x) { return typeof window.URL === 'function' && x instanceof window.URL; }
  function isRequest(x) { return typeof window.Request === 'function' && x instanceof window.Request; }

  // A string or URL object -> itself or its prefixed form (same type).
  function target(x) {
    if (typeof x === 'string') return url(x);
    if (isURL(x)) {
      try { var h = sameOriginTarget(x.href); return h ? new window.URL(h) : x; } catch (e) { return x; }
    }
    return x;
  }

  // A Request re-targeted at `href`, everything else kept. The options are
  // copied explicitly rather than `new Request(href, req)`: that form reads
  // req.body as a streaming body, which a browser refuses without `duplex`
  // (and Chrome only streams uploads over HTTP/2). A body is buffered once
  // instead -- so the answer is a Request, or a Promise of one.
  function retarget(req, href) {
    var R = window.Request;
    var init = {
      method: req.method, headers: req.headers, credentials: req.credentials,
      cache: req.cache, redirect: req.redirect, referrer: req.referrer,
      referrerPolicy: req.referrerPolicy, integrity: req.integrity,
      keepalive: req.keepalive, signal: req.signal
    };
    if (req.mode && req.mode !== 'navigate') init.mode = req.mode;
    var m = String(req.method || 'GET').toUpperCase();
    if (m === 'GET' || m === 'HEAD' || req.body === null) return new R(href, init);
    return req.arrayBuffer().then(function (buf) { init.body = buf; return new R(href, init); });
  }

  // 1. fetch
  var _fetch = window.fetch;
  if (typeof _fetch === 'function' && !_fetch.__smRoot) {
    var wrappedFetch = function (input) {
      var a = slice.call(arguments), self = this;
      if (!a.length) return _fetch.apply(self, a);
      if (isRequest(input)) {
        var built;
        try {
          var h = sameOriginTarget(input.url);
          if (!h) return _fetch.apply(self, a);
          built = retarget(input, h);
        } catch (e) { return _fetch.apply(self, a); }
        if (isRequest(built)) { a[0] = built; return _fetch.apply(self, a); }
        return built.then(function (r) { a[0] = r; return _fetch.apply(self, a); });
      }
      a[0] = target(input);
      return _fetch.apply(self, a);
    };
    wrappedFetch.__smRoot = root;
    window.fetch = wrappedFetch;
  }

  // 2. history -- the URL argument only; state and title untouched, arity kept
  var H = window.history;
  if (H) {
    ['pushState', 'replaceState'].forEach(function (k) {
      var o = H[k];
      if (typeof o !== 'function' || o.__smRoot) return;
      var w = function () {
        var a = slice.call(arguments);
        if (a.length > 2) a[2] = target(a[2]);
        return o.apply(this, a);
      };
      w.__smRoot = root;
      H[k] = w;
    });
  }

  // 3. htmx -- an extension, so it runs after every DOM listener (see top)
  function defineExt() {
    var hx = window.htmx;
    if (!hx || typeof hx.defineExtension !== 'function') return false;
    hx.defineExtension('sm-root', {
      onEvent: function (name, evt) {
        if (name === 'htmx:configRequest' && evt && evt.detail && typeof evt.detail.path === 'string') {
          evt.detail.path = url(evt.detail.path);
        }
        return true;
      }
    });
    return true;
  }
  // htmx.min.js loads AFTER this file (base.html). Its own ready() handler is
  // registered after ours, so on DOMContentLoaded the extension exists before
  // htmx processes the body. The capture-phase htmx:configRequest listener is
  // the second chance for an htmx that arrives any later: htmx looks its
  // extensions up AFTER dispatchEvent returns, so defining it during the very
  // first configRequest dispatch still covers that first request.
  if (!defineExt()) {
    var later = function () {
      if (!defineExt()) return;
      doc.removeEventListener('DOMContentLoaded', later);
      doc.removeEventListener('htmx:configRequest', later, true);
    };
    doc.addEventListener('DOMContentLoaded', later);
    doc.addEventListener('htmx:configRequest', later, true);
  }
})();
