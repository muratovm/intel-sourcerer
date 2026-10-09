"""Stateless frontend server: templates locally, private data over HTTP.

No database, account store, source config, or uploaded files are opened here.
The browser talks to this origin; only this server knows the backend address
and service key. Session cookies are forwarded, never interpreted or stored.
"""

import argparse
import json
import os
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx

from .templates import UI_ROOT, load_template, render_template

STATIC_FILES = {
    "/reader.css": ("shared/styles.css", "text/css; charset=utf-8"),
    "/reader.js": ("workspace/workspace.js", "text/javascript; charset=utf-8"),
    "/focus.js": ("workspace/focus.js", "text/javascript; charset=utf-8"),
    "/math.js": ("workspace/math.js", "text/javascript; charset=utf-8"),
    "/library.js": ("library/library.js", "text/javascript; charset=utf-8"),
    "/sources.js": ("sources/sources.js", "text/javascript; charset=utf-8"),
    "/vendor/katex/katex.min.js": (
        "vendor/katex/katex.min.js",
        "text/javascript; charset=utf-8",
    ),
    "/vendor/katex/auto-render.min.js": (
        "vendor/katex/auto-render.min.js",
        "text/javascript; charset=utf-8",
    ),
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' https: http:; media-src 'self' https: http:; "
    "frame-src https://www.youtube-nocookie.com https://player.vimeo.com; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
FORWARDED_HEADERS = (
    "Cookie",
    "Content-Type",
    "X-CSRF-Token",
    "X-Filename",
    "Range",
    "If-Range",
)
RESPONSE_HEADERS = (
    "Content-Type",
    "Location",
    "Set-Cookie",
    "Accept-Ranges",
    "Content-Range",
    "Content-Disposition",
)


def create_viewer(backend_url, service_key, port=8765, host="127.0.0.1"):
    """Serve the frontend against one explicitly configured private backend."""
    parsed = urlsplit(backend_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            "Backend URL must be an HTTP(S) origin, without a path or credentials."
        )
    if not service_key or len(service_key) < 32:
        raise ValueError(
            "Set INTEL_BACKEND_KEY to a secret of at least 32 characters for both services."
        )
    backend_url = backend_url.rstrip("/")

    class ViewerHandler(BaseHTTPRequestHandler):
        def upstream_headers(self):
            headers = {
                name: self.headers[name]
                for name in FORWARDED_HEADERS
                if name in self.headers
            }
            # Client-supplied service keys and forwarding headers are never trusted.
            headers.update(
                {
                    "X-Intel-Service-Key": service_key,
                    "X-Intel-Client-IP": self.client_address[0],
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                }
            )
            return headers

        def reply(
            self,
            body="",
            status=200,
            content_type="text/html; charset=utf-8",
            headers=None,
        ):
            body = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.security_headers()
            for name, value in getattr(self, "session_cookies", []):
                self.send_header(name, value)
            for name, value in headers or []:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def security_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")

        def redirect(self, location):
            self.reply(status=303, headers=[("Location", location)])

        def model(self, path):
            # A fresh client prevents a pooled cookie jar from mixing users.
            with httpx.Client(
                timeout=30, trust_env=False, follow_redirects=False
            ) as client:
                response = client.get(
                    backend_url + path, headers=self.upstream_headers()
                )
            self.session_cookies = [
                ("Set-Cookie", value)
                for value in response.headers.get_list("set-cookie")
            ]
            if response.status_code == 401:
                self.redirect("/login")
                return None
            response.raise_for_status()
            return response.json()

        def render_login(self, session, setup=False, error="", status=200):
            content = render_template(
                "auth/form.html",
                action="/auth/setup" if setup else "/auth/login",
                csrf=escape(session["csrf_token"], quote=True),
                autocomplete="new-password" if setup else "current-password",
                button="Create administrator" if setup else "Sign in",
            )
            content += load_template("auth/guest-option.html")
            self.reply(
                render_template(
                    "auth/page.html",
                    title="Create your account" if setup else "Sign in",
                    description="The first account owns your existing library and can add other users."
                    if setup
                    else "Your library, notes, and reading history are private to your account.",
                    error=escape(error),
                    content=content,
                ),
                status,
            )

        def render_account(self, session, message="", status=200):
            user = session["user"]
            csrf = escape(session["csrf_token"], quote=True)
            create_user = ""
            if user["admin"]:
                create_user = "<h2>Add an account</h2>" + render_template(
                    "auth/form.html",
                    action="/auth/create-user",
                    csrf=csrf,
                    autocomplete="new-password",
                    button="Create account",
                )
            content = render_template(
                "auth/account.html",
                username=escape(user["username"]),
                csrf=csrf,
                password_form=render_template("auth/password.html", csrf=csrf),
                create_user_form=create_user,
            )
            self.reply(
                render_template(
                    "auth/page.html",
                    title="Account",
                    description="Each user has a separate library and source settings.",
                    error=escape(message),
                    content=content,
                ),
                status,
            )

        def page(self, html, session):
            user = session["user"]
            html = html.replace("__TOKEN__", escape(session["csrf_token"], quote=True))
            html = html.replace("__USER_ID__", escape(user["id"], quote=True))
            html = html.replace("</nav>", '<a href="/account">Account</a></nav>')
            self.reply(html)

        def unavailable(self):
            message = "The account and article service is unavailable. Please try again shortly."
            if urlsplit(self.path).path.startswith("/api/"):
                self.reply(json.dumps({"error": message}), 502, "application/json")
            else:
                self.reply(
                    render_template(
                        "auth/page.html",
                        title="Sign-in service unavailable",
                        description="You can still read in a temporary guest session.",
                        error=message,
                        content=load_template("auth/guest-option.html"),
                    ),
                    502,
                )

        def do_GET(self):
            try:
                self.get_page()
            except (httpx.HTTPError, ValueError, KeyError):
                self.unavailable()

        def get_page(self):
            path = urlsplit(self.path).path
            from .guest_routes import guest_page

            page = guest_page(path)
            if page is not None:
                body, content_type = page
                self.reply(body, content_type=content_type)
                return
            if path == "/health":
                self.reply(
                    '{"status":"ok","service":"viewer"}',
                    content_type="application/json",
                )
                return
            if path in STATIC_FILES:
                name, content_type = STATIC_FILES[path]
                self.reply((UI_ROOT / name).read_bytes(), content_type=content_type)
                return
            if path == "/brand-icon.png":
                self.reply(
                    (UI_ROOT.parent / "brand-icon.png").read_bytes(),
                    content_type="image/png",
                )
                return
            if (
                path.startswith(("/api/", "/pdf-assets/", "/local-media/"))
                or path == "/export"
            ):
                self.proxy("GET")
                return
            if path not in {
                "/",
                "/login",
                "/setup",
                "/account",
                "/library",
                "/sources",
                "/stats",
            }:
                self.reply("Not found", 404)
                return
            session = self.model("/api/viewer-session")
            if session is None:
                return
            user = session["user"]
            if path in {"/login", "/setup"}:
                if user:
                    self.redirect("/")
                elif path == "/setup" and not session["setup_required"]:
                    self.redirect("/login")
                else:
                    self.render_login(session, path == "/setup")
                return
            if not user:
                self.redirect("/setup" if session["setup_required"] else "/login")
                return
            if path == "/account":
                self.render_account(session)
                return
            if path == "/":
                self.page(load_template("workspace/index.html"), session)
                return
            params = parse_qs(urlsplit(self.path).query)
            if path == "/library":
                from .library import render_library_rows

                data = self.model("/api/library")
                if data is None:
                    return
                html = render_library_rows(
                    data["articles"],
                    params.get("q", [""])[0],
                    params.get("notes", [""])[0] == "1",
                    params.get("selected", [""])[0],
                    session["csrf_token"],
                )
            elif path == "/sources":
                from .config import PublisherSource
                from .source_view import render_sources_list

                data = self.model("/api/sources")
                if data is None:
                    return
                html = render_sources_list(
                    [PublisherSource(**source) for source in data["sources"]],
                    session["csrf_token"],
                )
            else:
                from .stats import render_stats_data

                data = self.model("/api/stats")
                if data is None:
                    return
                html = render_stats_data(data)
            self.page(html, session)

        def prepare_guest(self):
            from .guest_routes import prepare_guest

            result, status = prepare_guest(self.headers, self.rfile)
            self.reply(json.dumps(result), status, "application/json")

        def do_POST(self):
            path = urlsplit(self.path).path
            if path == "/guest/prepare":
                self.prepare_guest()
                return
            if not path.startswith(("/api/", "/auth/")):
                self.reply("Not found", 404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                limit = (
                    100_000_000
                    if path == "/api/file-upload"
                    else 20_000_000
                    if path == "/api/pdf-upload"
                    else 8192
                    if path.startswith("/auth/")
                    else 1_000_000
                )
                if not 0 < length <= limit or "Transfer-Encoding" in self.headers:
                    self.close_connection = True
                    self.reply("Invalid request size", 413)
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("Incomplete request")
                self.proxy("POST", body)
            except (httpx.HTTPError, ValueError):
                self.unavailable()

        def proxy(self, method, body=None):
            if not self.path.startswith("/") or self.path.startswith("//"):
                self.reply("Invalid path", 400)
                return
            with (
                httpx.Client(
                    timeout=300, trust_env=False, follow_redirects=False
                ) as client,
                client.stream(
                    method,
                    backend_url + self.path,
                    headers=self.upstream_headers(),
                    content=body,
                ) as response,
            ):
                if (
                    self.path.startswith("/auth/")
                    and response.headers.get("content-type", "").startswith(
                        "application/json"
                    )
                    and response.status_code in {200, 400}
                ):
                    result = response.read()
                    result = json.loads(result)
                    session = self.model("/api/viewer-session")
                    if session is None:
                        return
                    if result.get("page") == "account" and session["user"]:
                        self.render_account(
                            session,
                            result.get("message", result.get("error", "")),
                            response.status_code,
                        )
                    else:
                        self.render_login(
                            session,
                            result.get("page") == "setup",
                            result.get("error", ""),
                            response.status_code,
                        )
                    return
                self.send_response(response.status_code)
                self.security_headers()
                for name in RESPONSE_HEADERS:
                    for value in response.headers.get_list(name):
                        self.send_header(name, value)
                if "content-length" in response.headers:
                    self.send_header(
                        "Content-Length", response.headers["content-length"]
                    )
                else:
                    self.send_header("Connection", "close")
                    self.close_connection = True
                self.end_headers()
                for chunk in response.iter_raw():
                    self.wfile.write(chunk)

    return ThreadingHTTPServer((host, port), ViewerHandler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("INTEL_BACKEND_URL", "http://127.0.0.1:8766"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--article",
        action="append",
        default=[],
        help="Start a frontend-only temporary queue; repeat for multiple URLs or files",
    )
    parser.add_argument(
        "--articles", help="JSON list for a frontend-only temporary session"
    )
    args = parser.parse_args()
    key = os.environ.get("INTEL_BACKEND_KEY", "")
    try:
        if args.article or args.articles:
            from .temporary import create_temporary_viewer, load_inputs

            server = create_temporary_viewer(
                load_inputs(args.article, args.articles), args.port, args.host
            )
        else:
            server = create_viewer(args.backend_url, key, args.port, args.host)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        parser.error(str(exc))
    print(
        f"Intel Sourcerer viewer: http://{args.host}:{server.server_port}", flush=True
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
