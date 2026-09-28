"""A stdlib reverse proxy that mounts SM under a path prefix (docs/226, spec 5.5).

The FALLBACK of the proxy QA rig, not a substitute for it: nginx and Caddy
(sm_qa_rigs/_tools/proxy) are the proxies a lab actually runs; this stub exists
so the rig still works where their binaries cannot be downloaded, and so every
cell of the matrix can be emulated with one small, readable program.

    python proxy_stub.py --listen 5341 --upstream 127.0.0.1:5339 --prefix /sm
                         [--strip | --no-strip]
                         [--send-prefix-header | --no-send-prefix-header]
                         [--host preserve | rewrite] [--read-timeout 3600]

What it does, per request:
  * a path outside the prefix -> 404 "platform fallback" (a URL that leaked
    out of the mount becomes a loud 404, exactly like the real platform's);
  * exactly the prefix (/sm)   -> 301 to /sm/;
  * --strip removes the prefix before forwarding (nginx `proxy_pass .../`,
    Caddy `handle_path`); --no-strip forwards the path unchanged (Caddy
    `handle`, k8s ingress default);
  * X-Forwarded-For/Proto/Host are SET (never appended to what the client
    sent: every client-supplied X-Forwarded-* header is dropped first, so a
    browser cannot spoof one through this proxy); X-Forwarded-Host keeps the
    port, as nginx's $http_host and Caddy's default do;
  * --send-prefix-header adds X-Forwarded-Prefix: <prefix>;
  * --host preserve forwards the browser's Host; --host rewrite sends the
    upstream's host:port instead (so SM's CSRF check can only pass through
    X-Forwarded-Host + --behind-proxy);
  * bodies stream both ways (Content-Length, chunked or read-to-close), so a
    long poll or a big upload is never buffered whole; the upstream read
    timeout is --read-timeout seconds;
  * Location headers are passed through UNCHANGED (like `proxy_redirect off`
    and Caddy's default): a root-absolute redirect from SM stays visible.
"""
from __future__ import annotations

import argparse
import http.client
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOP = {"connection", "keep-alive", "proxy-connection", "proxy-authenticate",
       "proxy-authorization", "te", "trailer", "trailers", "transfer-encoding",
       "upgrade"}
CHUNK = 64 * 1024


def _norm_prefix(p: str) -> str:
    p = "/" + (p or "").strip("/")
    return "" if p == "/" else p


def make_handler(cfg):
    prefix = cfg.prefix
    up_host, up_port = cfg.upstream.rsplit(":", 1)
    up_port = int(up_port)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "sm-proxy-stub"

        def log_message(self, fmt, *args):  # one line per request on stderr
            if not cfg.quiet:
                sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

        def _plain(self, code, text, extra=None):
            body = (text + "\n").encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _read_body(self):
            """Yield the request body in chunks (Content-Length or chunked)."""
            te = (self.headers.get("Transfer-Encoding") or "").lower()
            if "chunked" in te:
                while True:
                    line = self.rfile.readline()
                    size = int(line.split(b";")[0].strip() or b"0", 16)
                    if size == 0:
                        while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                            pass
                        return
                    yield self.rfile.read(size)
                    self.rfile.readline()
            n = int(self.headers.get("Content-Length") or 0)
            while n > 0:
                b = self.rfile.read(min(CHUNK, n))
                if not b:
                    return
                n -= len(b)
                yield b

        def _proxy(self):
            path = self.path
            if not path.startswith("/"):  # absolute-form request line
                path = "/" + path.split("/", 3)[-1] if "://" in path else "/" + path
            bare, _, query = path.partition("?")
            if prefix:
                if bare == prefix:
                    loc = prefix + "/" + (("?" + query) if query else "")
                    # drain any body first so the connection stays usable
                    for _ in self._read_body():
                        pass
                    return self._plain(301, "moved", {"Location": loc})
                if not bare.startswith(prefix + "/"):
                    for _ in self._read_body():
                        pass
                    return self._plain(404, "platform fallback")
                if cfg.strip:
                    path = path[len(prefix):] or "/"
            client_host = self.headers.get("Host") or ("%s:%d" % self.server.server_address[:2])
            headers = []
            for k, v in self.headers.items():
                lk = k.lower()
                if lk in HOP or lk.startswith("x-forwarded-") or lk in ("host", "content-length", "forwarded"):
                    continue
                headers.append((k, v))
            headers.append(("Host", client_host if cfg.host == "preserve" else "%s:%d" % (up_host, up_port)))
            headers.append(("X-Forwarded-For", self.client_address[0]))
            headers.append(("X-Forwarded-Proto", "http"))
            headers.append(("X-Forwarded-Host", client_host))
            if cfg.send_prefix_header and prefix:
                headers.append(("X-Forwarded-Prefix", prefix))
            has_body = ("Content-Length" in self.headers) or ("chunked" in (self.headers.get("Transfer-Encoding") or "").lower())
            conn = http.client.HTTPConnection(up_host, up_port, timeout=cfg.read_timeout)
            try:
                conn.putrequest(self.command, path, skip_host=True, skip_accept_encoding=True)
                for k, v in headers:
                    conn.putheader(k, v)
                if has_body:
                    if "Content-Length" in self.headers:
                        conn.putheader("Content-Length", self.headers["Content-Length"])
                        conn.endheaders()
                        for b in self._read_body():
                            conn.send(b)
                    else:
                        conn.putheader("Transfer-Encoding", "chunked")
                        conn.endheaders()
                        for b in self._read_body():
                            conn.send(b"%x\r\n%s\r\n" % (len(b), b))
                        conn.send(b"0\r\n\r\n")
                else:
                    conn.endheaders()
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                conn.close()
                return self._plain(502, "proxy stub: upstream error: %s" % exc)
            try:
                self.send_response_only(resp.status, resp.reason)
                length = resp.getheader("Content-Length")
                for k, v in resp.getheaders():
                    if k.lower() in HOP or k.lower() == "content-length":
                        continue
                    self.send_header(k, v)
                no_body = self.command == "HEAD" or resp.status in (204, 304) or 100 <= resp.status < 200
                chunked = False
                if no_body:
                    if length is not None:
                        self.send_header("Content-Length", length)
                elif length is not None:
                    self.send_header("Content-Length", length)
                else:
                    self.send_header("Transfer-Encoding", "chunked")
                    chunked = True
                self.end_headers()
                if not no_body:
                    while True:
                        b = resp.read1(CHUNK) if hasattr(resp, "read1") else resp.read(CHUNK)
                        if not b:
                            break
                        if chunked:
                            self.wfile.write(b"%x\r\n%s\r\n" % (len(b), b))
                        else:
                            self.wfile.write(b)
                        self.wfile.flush()
                    if chunked:
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
            except (OSError, socket.timeout):
                self.close_connection = True
            finally:
                conn.close()

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _proxy

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="stdlib prefix-mounting reverse proxy for SM's QA rig")
    ap.add_argument("--listen", type=int, required=True)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--upstream", required=True, help="host:port of SM")
    ap.add_argument("--prefix", default="/sm")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--strip", dest="strip", action="store_true", default=True)
    g.add_argument("--no-strip", dest="strip", action="store_false")
    h = ap.add_mutually_exclusive_group()
    h.add_argument("--send-prefix-header", dest="send_prefix_header", action="store_true", default=False)
    h.add_argument("--no-send-prefix-header", dest="send_prefix_header", action="store_false")
    ap.add_argument("--host", choices=("preserve", "rewrite"), default="preserve")
    ap.add_argument("--read-timeout", type=float, default=3600.0)
    ap.add_argument("--quiet", action="store_true")
    cfg = ap.parse_args(argv)
    cfg.prefix = _norm_prefix(cfg.prefix)
    srv = ThreadingHTTPServer((cfg.bind, cfg.listen), make_handler(cfg))
    srv.daemon_threads = True
    print("proxy_stub listening on http://%s:%d%s/ -> %s (strip=%s prefix-header=%s host=%s read-timeout=%ss)"
          % (cfg.bind, cfg.listen, cfg.prefix, cfg.upstream, cfg.strip, cfg.send_prefix_header, cfg.host,
             cfg.read_timeout), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
