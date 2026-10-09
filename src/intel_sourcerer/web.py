"""A small, browser interface for saved articles."""

import argparse
from datetime import datetime
from .templates import UI_ROOT, load_template, render_template
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
import secrets
import hashlib
import json
import asyncio
import threading
from datetime import timedelta, timezone
import httpx
from .discovery import article_types, import_link
from .config import load_sources, set_source_enabled
from .importer import import_articles
from contextlib import closing
from bs4 import BeautifulSoup

from .storage import connect_database
from .sessions import (
    session_db,
    queue_state,
    create_queue,
    save_notes,
    rename_article,
    navigate,
    delete_article,
    SessionError,
    has_notes,
)
from urllib.parse import urlsplit, parse_qs, urlencode
from zoneinfo import ZoneInfo

from .content import render_markdown
from .timed_reading import session_state, timer_action

STYLE = """
:root { color-scheme: light; font-family: system-ui, -apple-system, sans-serif; color: #1e293b; background: #f6f7f9; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 860px; margin: 56px auto; padding: 0 24px; }
h1 { font-size: 30px; letter-spacing: -.8px; margin: 0 0 8px; }
.intro { color: #64748b; margin: 0 0 28px; line-height: 1.6; }
.list { border: 1px solid #dde2e8; border-radius: 12px; background: white; overflow: hidden; }
details + details { border-top: 1px solid #e5e9ef; }
summary { cursor: pointer; padding: 22px 24px; list-style: none; display: flex; gap: 14px; align-items: flex-start; }
summary::-webkit-details-marker { display: none; }
summary::before { content: '+'; color: #64748b; font-size: 22px; width: 18px; flex-shrink: 0; line-height: 1.25; }
details[open] > summary::before { content: '−'; }
summary:hover { background: #f8fafc; }
summary:focus-visible { outline: 3px solid #2563eb; outline-offset: -3px; }
.headline { display: block; font-size: 17px; font-weight: 600; line-height: 1.5; }
.meta { display: block; color: #64748b; font-size: 13px; margin-top: 6px; }
.read-control { margin: 0 0 20px; }
.read-control button { cursor: pointer; background: #f8fafc; border: 1px solid #cbd5e1; padding: 8px 14px; border-radius: 6px; font: inherit; font-size: 14px; }
.read-control button:hover { background: #e2e8f0; }
.read-control button:focus-visible { outline: 3px solid #2563eb; }
.article { padding: 0 24px 28px 56px; }
a { color: #1d4ed8; text-underline-offset: 3px; }
.original { display: inline-block; margin: 0 0 20px; font-size: 14px; }
.content { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 16px; line-height: 1.8; }
.markdown { overflow-wrap: anywhere; font-size: 16px; line-height: 1.8; }
.markdown h1, .markdown h2, .markdown h3 { line-height: 1.35; margin: 1.6em 0 .7em; }
.markdown li { margin: .35em 0; }
.markdown blockquote { border-left: 3px solid #94a3b8; background: #f8fafc; margin: 20px 0; padding: 8px 18px; color: #475569; }
.markdown table { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; font-size: 14px; margin: 20px 0; }
.markdown th, .markdown td { border: 1px solid #dbe2ea; padding: 10px 14px; text-align: left; }
.markdown th { background: #f1f5f9; }
.markdown pre { overflow-x: auto; padding: 16px; background: #f1f5f9; border-radius: 6px; }
.empty { padding: 24px; color: #64748b; }
@media (max-width: 600px) { main { margin: 28px auto; padding: 0 14px; } summary { padding: 18px 16px; } .article { padding: 0 18px 24px; } h1 { font-size: 26px; } }
"""


def read_articles(database):
    """Open the existing database read-only; never create an empty replacement."""
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db, db:
        db.row_factory = sqlite3.Row
        return db.execute("""SELECT a.title,a.url,a.published_at,a.content_text,a.content_markdown,a.is_read,a.notes,a.notes_format,a.notes_updated_at,a.content_format,s.name AS publisher
            FROM articles a JOIN sources s ON s.id=a.source_id
            WHERE s.enabled=1 ORDER BY a.published_at DESC,a.title""").fetchall()


def render_page(articles, csrf_token="", open_url=None):
    rows = []
    for article in articles:
        try:
            date = (
                datetime.fromisoformat(article["published_at"])
                .astimezone(ZoneInfo("America/Toronto"))
                .strftime("%b %d, %Y")
            )
        except (TypeError, ValueError):
            date = "Date unavailable"
        url = article["url"]
        parsed = urlsplit(url)
        link = (
            f'<a class="original" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Read original article ↗</a>'
            if parsed.scheme in {"http", "https"} and parsed.hostname
            else ""
        )
        text = (
            article["content_text"]
            or "The full article text has not been saved yet. Use the original article link to read it."
        )
        keys = article.keys()
        markdown = article["content_markdown"] if "content_markdown" in keys else None
        body = render_markdown(markdown) if markdown else escape(text)
        content_class = "markdown" if markdown else "content"
        is_read = bool(article["is_read"]) if "is_read" in keys else False
        status = "Read" if is_read else "Unread"
        action = "Mark unread" if is_read else "Mark read"
        article_id = "article-" + hashlib.sha256(url.encode()).hexdigest()[:16]
        expanded = " open" if url == open_url else ""
        form = f'''<form class="read-control" method="post" action="/read-status">
        <input type="hidden" name="url" value="{escape(url, quote=True)}">
        <input type="hidden" name="is_read" value="{0 if is_read else 1}">
        <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
        <button type="submit">{action}</button></form>'''
        rows.append(f'''<details id="{article_id}"{expanded}><summary><span><span class="headline">{escape(article["title"])}</span>
        <span class="meta">{escape(article["publisher"])} · {date} · {status}</span></span></summary>
        <div class="article">{link}{form}<div class="{content_class}">{body}</div></div></details>''')
    listing = (
        "".join(rows)
        or '<p class="empty">No saved articles yet. Import articles to see them here.</p>'
    )
    count = len(articles)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1"><title>Intel Sourcerer</title><style>{STYLE}</style></head>
    <body><main><h1>Intel Sourcerer</h1><p class="intro">{count} saved article{"s" if count != 1 else ""} · Click a headline to read.</p>
    <div class="list">{listing}</div></main></body></html>"""


def render_sources(config, csrf_token=""):
    from .source_view import render_sources_list

    return render_sources_list(load_sources(config), csrf_token, str(config))


def create_server(
    database,
    port=8765,
    config="config/sources.toml",
    *,
    auth_enabled=True,
    secure_cookies=False,
    api_only=False,
    service_key=None,
    host="127.0.0.1",
):
    if api_only and (not service_key or len(service_key) < 32):
        raise ValueError(
            "The standalone backend requires a service key of at least 32 characters."
        )
    connect_database(database).close()
    from .backups import backup_database

    backup_database(database)
    csrf_token = secrets.token_urlsafe(32)
    discovery_lock = threading.Lock()

    from .auth import Accounts

    accounts = Accounts(database, config) if auth_enabled else None
    base_database, base_config, default_csrf = database, config, csrf_token

    class Handler(BaseHTTPRequestHandler):
        def service_authorized(self):
            if not api_only:
                return True
            supplied = self.headers.get("X-Intel-Service-Key", "").encode()
            if secrets.compare_digest(supplied, service_key.encode()):
                return True
            self.respond({"error": "Service access required."}, status=403)
            return False

        def viewer_session(self):
            session = accounts.session(self.headers.get("Cookie"))
            if not session:
                token, _ = accounts.new_session()
                self.cookie(token)
                session = accounts.session("intel_session=" + token)
            self.respond(
                {
                    "user": {
                        "id": session["user_id"],
                        "username": session["username"],
                        "admin": bool(session["admin"]),
                    }
                    if session["user_id"]
                    else None,
                    "csrf_token": session["csrf"],
                    "setup_required": accounts.empty(),
                }
            )

        def redirect(self, location):
            self.respond("", "text/html; charset=utf-8", 303, {"Location": location})

        def cookie(self, token):
            self.pending_cookie = (
                f"intel_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800"
                + ("; Secure" if secure_cookies else "")
            )

        def auth_page(self, setup=False, error="", status=200):
            if api_only and self.headers.get("Accept") == "application/json":
                self.respond(
                    {"error": error, "page": "setup" if setup else "login"},
                    status=status,
                )
                return
            session = accounts.session(self.headers.get("Cookie"))
            if not session:
                token, csrf = accounts.new_session()
                self.cookie(token)
            else:
                csrf = session["csrf"]
            content = render_template(
                "auth/form.html",
                action="/auth/setup" if setup else "/auth/login",
                csrf=escape(csrf),
                autocomplete="new-password" if setup else "current-password",
                button="Create administrator" if setup else "Sign in",
            )
            self.respond(
                render_template(
                    "auth/page.html",
                    title="Create your account" if setup else "Sign in",
                    description="The first account owns your existing library and can add other users."
                    if setup
                    else "Your library, notes, and reading history are private to your account.",
                    error=escape(error),
                    content=content + load_template("auth/guest-option.html"),
                ),
                "text/html; charset=utf-8",
                status,
            )

        def context(self):
            if not accounts:
                self.user_id = "local"
                self.is_admin = True
                return base_database, base_config, default_csrf
            session = accounts.session(self.headers.get("Cookie"))
            if not session or not session["user_id"]:
                if urlsplit(self.path).path.startswith("/api/"):
                    self.respond({"error": "Please sign in."}, status=401)
                else:
                    self.redirect("/setup" if accounts.empty() else "/login")
                return None
            self.user_id, self.is_admin = session["user_id"], bool(session["admin"])
            self.session = session
            return session["database_path"], session["config_path"], session["csrf"]

        def account_page(self, error="", status=200):
            if api_only and self.headers.get("Accept") == "application/json":
                self.respond({"message": error, "page": "account"}, status=status)
                return
            csrf = escape(self.session["csrf"])
            content = f'<p>Signed in as <strong>{escape(self.session["username"])}</strong></p><p><a href="/">Back to Intel Sourcerer</a></p><form method="post" action="/auth/logout"><input type="hidden" name="csrf_token" value="{csrf}"><button>Sign out</button></form>'
            content += render_template("auth/password.html", csrf=csrf)
            if self.is_admin:
                content += "<h2>Add an account</h2>" + render_template(
                    "auth/form.html",
                    action="/auth/create-user",
                    csrf=csrf,
                    autocomplete="new-password",
                    button="Create account",
                )
            self.respond(
                render_template(
                    "auth/page.html",
                    title="Account",
                    description="Each user has a separate library and source settings.",
                    error=escape(error),
                    content=content,
                ),
                "text/html; charset=utf-8",
                status,
            )

        def auth_post(self):
            path = urlsplit(self.path).path
            session = accounts.session(self.headers.get("Cookie"))
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError("Invalid request size.")
                data = parse_qs(self.rfile.read(length).decode())
                if not session or not secrets.compare_digest(
                    data.get("csrf_token", [""])[0], session["csrf"]
                ):
                    self.respond(
                        {"error": "Refresh the page and try again."}, status=403
                    )
                    return
                username, password = (
                    data.get("username", [""])[0],
                    data.get("password", [""])[0],
                )
                if path == "/auth/setup":
                    user_id = accounts.create_user(username, password, first=True)
                elif path == "/auth/login":
                    user_id = accounts.login(
                        username,
                        password,
                        self.headers.get("X-Intel-Client-IP", self.client_address[0])
                        if api_only
                        else self.client_address[0],
                    )
                elif path in {"/auth/logout", "/auth/create-user", "/auth/password"}:
                    context = self.context()
                    if not context:
                        return
                    if path == "/auth/password":
                        accounts.change_password(
                            session["user_id"],
                            data.get("current_password", [""])[0],
                            password,
                        )
                        token, _ = accounts.new_session(session["user_id"])
                        self.cookie(token)
                        self.redirect("/account")
                        return
                    if path == "/auth/create-user":
                        if not self.is_admin:
                            self.respond(
                                {"error": "Administrator access required."}, status=403
                            )
                            return
                        accounts.create_user(username, password)
                        self.account_page("Account created.")
                        return
                    accounts.revoke(session)
                    self.pending_cookie = (
                        "intel_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
                    )
                    self.redirect("/login")
                    return
                else:
                    self.respond({"error": "Not found"}, status=404)
                    return
                accounts.revoke(session)
                token, _ = accounts.new_session(user_id)
                self.cookie(token)
                self.redirect("/")
            except (ValueError, UnicodeError) as exc:
                if (
                    path in {"/auth/create-user", "/auth/password"}
                    and session
                    and session["user_id"]
                ):
                    if self.context():
                        self.account_page(str(exc), 400)
                else:
                    self.auth_page(path == "/auth/setup", str(exc), 400)

        def respond(
            self, body, content_type="application/json", status=200, headers=None
        ):
            if content_type == "application/json":
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                if content_type.startswith("text/html"):
                    body = body.replace(
                        "__USER_ID__", getattr(self, "user_id", "local")
                    )
                    if accounts and getattr(self, "user_id", None):
                        body = body.replace(
                            "</nav>", '<a href="/account">Account</a></nav>'
                        )
                body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' https: http:; media-src 'self' https: http:; frame-src https://www.youtube-nocookie.com https://player.vimeo.com; base-uri 'none'; frame-ancestors 'none'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            if getattr(self, "pending_cookie", None):
                self.send_header("Set-Cookie", self.pending_cookie)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if not api_only:
                from .guest_routes import guest_page

                page = guest_page(path)
                if page is not None:
                    body, content_type = page
                    self.respond(body, content_type)
                    return
            if path == "/health":
                self.respond(
                    {"status": "ok", "service": "backend" if api_only else "combined"}
                )
                return
            if not self.service_authorized():
                return
            if path == "/api/viewer-session" and accounts:
                self.viewer_session()
                return
            if api_only and not (
                path.startswith(("/api/", "/pdf-assets/", "/local-media/"))
                or path == "/export"
            ):
                self.respond({"error": "Not found"}, status=404)
                return
            if accounts and path in {"/login", "/setup"}:
                if path == "/setup" and not accounts.empty():
                    self.redirect("/login")
                else:
                    self.auth_page(path == "/setup")
                return
            public = path in {
                "/reader.css",
                "/reader.js",
                "/focus.js",
                "/math.js",
                "/library.js",
                "/sources.js",
                "/brand-icon.png",
            } or path.startswith("/vendor/katex/")
            context = (
                (base_database, base_config, default_csrf) if public else self.context()
            )
            if not context:
                return
            database, config, csrf_token = context
            if accounts and path == "/account":
                self.account_page()
                return
            if path in {"/library", "/export"}:
                from .library import render_library, export_article

                params = parse_qs(urlsplit(self.path).query)
                with closing(session_db(database)) as db:
                    if path == "/library":
                        self.respond(
                            render_library(
                                db,
                                params.get("q", [""])[0],
                                params.get("notes", [""])[0] == "1",
                                params.get("selected", [""])[0],
                                csrf_token,
                            ),
                            "text/html; charset=utf-8",
                        )
                    else:
                        result = export_article(db, params.get("url", [""])[0])
                        self.respond(
                            result or "Article not found",
                            "text/markdown; charset=utf-8",
                            200 if result else 404,
                        )
                return
            if path.startswith("/vendor/katex/"):
                root = UI_ROOT / "vendor/katex"
                name = path.removeprefix("/vendor/katex/")
                if name not in {"katex.min.js", "auto-render.min.js"}:
                    self.respond({"error": "Not found"}, status=404)
                else:
                    self.respond(
                        (root / name).read_bytes(), "text/javascript; charset=utf-8"
                    )
                return
            if path == "/math.js":
                self.respond(
                    (UI_ROOT / "workspace/math.js").read_text(),
                    "text/javascript; charset=utf-8",
                )
                return
            if path == "/library.js":
                self.respond(
                    (UI_ROOT / "library/library.js").read_text(),
                    "text/javascript; charset=utf-8",
                )
                return
            if path == "/sources.js":
                self.respond(
                    (UI_ROOT / "sources/sources.js").read_text(),
                    "text/javascript; charset=utf-8",
                )
                return
            if path.startswith("/local-media/"):
                from .content import re_local_media
                import mimetypes

                asset = (
                    Path(database).resolve().parent / "media" / path.rsplit("/", 1)[-1]
                )
                if not re_local_media(path) or not asset.is_file():
                    self.respond({"error": "Not found"}, status=404)
                    return
                size = asset.stat().st_size
                start, end = 0, size - 1
                requested = self.headers.get("Range")
                if requested:
                    import re

                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                    if not match or not any(match.groups()):
                        self.send_error(416)
                        return
                    if match[1]:
                        start = int(match[1])
                        end = min(size - 1, int(match[2]) if match[2] else size - 1)
                    else:
                        start = max(0, size - int(match[2]))
                    if start > end or start >= size:
                        self.send_error(416)
                        return
                self.send_response(206 if requested else 200)
                self.send_header(
                    "Content-Type",
                    mimetypes.guess_type(asset.name)[0] or "application/octet-stream",
                )
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("X-Content-Type-Options", "nosniff")
                if requested:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with asset.open("rb") as file:
                    file.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = file.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            if path.startswith("/pdf-assets/"):
                from .content import re_asset_url

                asset = (
                    Path(database).resolve().parent
                    / "pdf-assets"
                    / path.rsplit("/", 1)[-1]
                )
                if not re_asset_url(path) or not asset.is_file():
                    self.respond({"error": "Not found"}, status=404)
                else:
                    self.respond(asset.read_bytes(), "image/png")
                return
            if path == "/brand-icon.png":
                self.respond(
                    Path(__file__).with_name("brand-icon.png").read_bytes(), "image/png"
                )
                return
            if path == "/stats":
                from .stats import render_stats

                with closing(session_db(database)) as db:
                    self.respond(render_stats(db), "text/html; charset=utf-8")
                return
            if path == "/sources":
                try:
                    self.respond(
                        render_sources(config, csrf_token), "text/html; charset=utf-8"
                    )
                except (OSError, ValueError) as exc:
                    self.respond({"error": str(exc)}, status=500)
                return
            if path in {"/", "/reader.js", "/reader.css", "/focus.js"}:
                filename = {
                    "/": "workspace/index.html",
                    "/reader.js": "workspace/workspace.js",
                    "/focus.js": "workspace/focus.js",
                    "/reader.css": "shared/styles.css",
                }[path]
                content = load_template(filename).replace("__TOKEN__", csrf_token)
                self.respond(
                    content,
                    {
                        "/": "text/html; charset=utf-8",
                        "/reader.js": "text/javascript; charset=utf-8",
                        "/focus.js": "text/javascript; charset=utf-8",
                        "/reader.css": "text/css; charset=utf-8",
                    }[path],
                )
                return
            if path.startswith("/api/"):
                try:
                    with closing(session_db(database)) as db, db:
                        if path == "/api/library":
                            from .library import library_rows

                            self.respond({"articles": library_rows(db)})
                        elif path == "/api/sources":
                            from dataclasses import asdict

                            self.respond(
                                {
                                    "sources": [
                                        asdict(source)
                                        for source in load_sources(config)
                                    ]
                                }
                            )
                        elif path == "/api/stats":
                            from .stats import stats_data

                            self.respond(stats_data(db))
                        elif path == "/api/timer":
                            self.respond({"session": session_state(db)})
                        elif path == "/api/state":
                            articles = [
                                dict(
                                    {
                                        k: r[k]
                                        for k in (
                                            "url",
                                            "title",
                                            "published_at",
                                            "is_read",
                                            "publisher",
                                            "notes_updated_at",
                                            "content_format",
                                        )
                                    },
                                    note_preview=BeautifulSoup(
                                        r["notes"] or "", "html.parser"
                                    ).get_text(" ", strip=True)
                                    if r["notes_format"] == "html"
                                    else (r["notes"] or ""),
                                    has_notes=has_notes(r["notes"], r["notes_format"]),
                                    article_types=article_types(
                                        r["title"], r["content_text"]
                                    ),
                                )
                                for r in read_articles(database)
                            ]
                            self.respond(
                                {"articles": articles, "queue": queue_state(db)}
                            )
                        elif path == "/api/article":
                            url = parse_qs(urlsplit(self.path).query).get("url", [""])[
                                0
                            ]
                            row = db.execute(
                                "SELECT a.*,s.name AS publisher FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.url=?",
                                (url,),
                            ).fetchone()
                            if not row:
                                self.respond({"error": "Article not found"}, status=404)
                                return
                            html = (
                                render_markdown(row["content_markdown"])
                                if row["content_markdown"]
                                else "<p>"
                                + escape(
                                    row["content_text"] or "No saved article text."
                                )
                                + "</p>"
                            )
                            soup = BeautifulSoup(html, "html.parser")
                            title = soup.find("h1")
                            if title:
                                for node in list(title.previous_siblings):
                                    node.extract()
                                title.decompose()
                            for block in soup.find_all("blockquote"):
                                heading = block.find("strong")
                                if not heading:
                                    continue
                                kind = (
                                    heading.get_text()
                                    .strip()
                                    .removesuffix(" placeholder:")
                                    .lower()
                                )
                                if heading.get_text().strip() not in {
                                    "Image placeholder:",
                                    "Video placeholder:",
                                    "Audio placeholder:",
                                    "Embedded media placeholder:",
                                }:
                                    continue
                                link = block.find("a", href=True)
                                if link and (
                                    link["href"].startswith(
                                        ("/pdf-assets/", "/local-media/")
                                    )
                                    or urlsplit(link["href"]).scheme
                                    in {
                                        "http",
                                        "https",
                                    }
                                ):
                                    block["data-media-kind"] = kind
                                    block["data-media-url"] = link["href"]
                                    block["data-media-description"] = (
                                        heading.parent.get_text(" ", strip=True)
                                        .split("placeholder:", 1)[-1]
                                        .strip()
                                    )
                            self.respond(
                                dict(
                                    content_format=row["content_format"],
                                    url=row["url"],
                                    title=row["title"],
                                    published_at=row["published_at"],
                                    publisher=row["publisher"],
                                    is_read=row["is_read"],
                                    has_notes=has_notes(
                                        row["notes"], row["notes_format"]
                                    ),
                                    notes=row["notes"],
                                    notes_format=row["notes_format"],
                                    notes_revision=row["notes_revision"],
                                    body_html=str(soup),
                                )
                            )
                        else:
                            self.respond({"error": "Not found"}, status=404)
                except (sqlite3.Error, OSError, ValueError):
                    self.respond({"error": "Backend data unavailable"}, status=503)
                return
            if path != "/classic":
                self.send_error(404)
                return
            try:
                open_url = parse_qs(urlsplit(self.path).query).get("open", [None])[0]
                content = render_page(
                    read_articles(database), csrf_token, open_url
                ).encode("utf-8")
            except sqlite3.Error:
                self.send_error(
                    503, "Article database unavailable; run the importer first."
                )
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            if not api_only and urlsplit(self.path).path == "/guest/prepare":
                from .guest_routes import prepare_guest

                result, status = prepare_guest(self.headers, self.rfile)
                self.respond(result, status=status)
                return
            if not self.service_authorized():
                return
            if api_only and not urlsplit(self.path).path.startswith(
                ("/api/", "/auth/")
            ):
                self.respond({"error": "Not found"}, status=404)
                return
            if accounts and urlsplit(self.path).path.startswith("/auth/"):
                self.auth_post()
                return
            context = self.context()
            if not context:
                return
            database, config, csrf_token = context
            if self.path.startswith("/api/"):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if (
                        not 0
                        < length
                        <= (
                            100_000_000
                            if self.path == "/api/file-upload"
                            else 20_000_000
                            if self.path == "/api/pdf-upload"
                            else 1_000_000
                        )
                    ):
                        raise ValueError("Invalid request size")
                    if not secrets.compare_digest(
                        self.headers.get("X-CSRF-Token", ""), csrf_token
                    ):
                        self.respond(
                            {"error": "Refresh the page and try again."}, status=403
                        )
                        return
                    if self.path == "/api/file-upload":
                        from .media_import import import_file
                        from urllib.parse import unquote

                        payload = self.rfile.read(length)
                        if len(payload) != length:
                            raise ValueError("Incomplete upload")
                        filename = unquote(self.headers.get("X-Filename", ""))
                        with discovery_lock:
                            url = asyncio.run(import_file(database, payload, filename))
                        self.respond({"url": url})
                        return
                    if self.path == "/api/pdf-upload":
                        from .discovery import import_pdf_upload

                        payload = self.rfile.read(length)
                        if len(payload) != length:
                            raise ValueError("Incomplete PDF upload")
                        with discovery_lock:
                            url = asyncio.run(import_pdf_upload(database, payload))
                        self.respond({"url": url})
                        return
                    data = json.loads(self.rfile.read(length))
                    if self.path in {"/api/notes", "/api/delete-article"}:
                        backup_database(database)
                    if self.path == "/api/source-status":
                        with discovery_lock:
                            set_source_enabled(
                                config, data.get("id"), data.get("enabled")
                            )
                            with (
                                closing(connect_database(database)) as source_db,
                                source_db,
                            ):
                                source_db.execute(
                                    "UPDATE sources SET enabled=? WHERE id=?",
                                    (int(data["enabled"]), data["id"]),
                                )
                        self.respond({"enabled": data["enabled"]})
                        return
                    if not isinstance(data, dict):
                        raise ValueError("Invalid request")
                    if self.path in {"/api/discover", "/api/link"}:
                        if not discovery_lock.acquire(blocking=False):
                            self.respond(
                                {
                                    "error": "An article fetch is already running. Try again shortly."
                                },
                                status=409,
                            )
                            return
                        try:
                            if self.path == "/api/link":
                                submitted = data.get("url", "")
                                if (
                                    not self.is_admin
                                    and isinstance(submitted, str)
                                    and (
                                        submitted.startswith(("/", "~", "file:"))
                                        or urlsplit(submitted).scheme
                                        not in {"http", "https"}
                                    )
                                ):
                                    raise ValueError(
                                        "Upload local files instead of submitting a server filesystem path."
                                    )
                                result = {
                                    "url": asyncio.run(
                                        import_link(database, data.get("url"))
                                    )
                                }
                            else:
                                days = data.get("days", 30)
                                if type(days) is not int or days not in {
                                    0,
                                    1,
                                    3,
                                    7,
                                    14,
                                    30,
                                    60,
                                    90,
                                    180,
                                    365,
                                }:
                                    raise ValueError("Choose a valid time window.")
                                now = datetime.now(timezone.utc)
                                result = asyncio.run(
                                    import_articles(
                                        config,
                                        database,
                                        (
                                            datetime(1970, 1, 1, tzinfo=timezone.utc)
                                            if days == 0
                                            else now - timedelta(days=days)
                                        ),
                                        now,
                                    )
                                )
                            self.respond(result)
                        finally:
                            discovery_lock.release()
                        return
                    with closing(session_db(database)) as db, db:
                        db.execute("BEGIN IMMEDIATE")
                        if self.path == "/api/timer":
                            result = {"session": timer_action(db, data)}
                        elif self.path == "/api/queue":
                            result = create_queue(db, data.get("urls"))
                        elif self.path == "/api/notes":
                            result = {
                                "revision": save_notes(
                                    db,
                                    data.get("url"),
                                    data.get("text"),
                                    data.get("revision"),
                                    data.get("format", "plain"),
                                )
                            }
                        elif self.path == "/api/rename-article":
                            result = rename_article(
                                db, data.get("url"), data.get("title")
                            )
                        elif self.path == "/api/delete-article":
                            result = delete_article(db, data.get("url"))
                        elif self.path == "/api/navigate":
                            result = navigate(
                                db,
                                data.get("queue_id"),
                                data.get("cursor"),
                                data.get("action"),
                            )
                        else:
                            self.respond({"error": "Not found"}, status=404)
                            return
                    self.respond(result)
                except SessionError as exc:
                    self.respond({"error": str(exc)}, status=409)
                except (ValueError, TypeError, httpx.HTTPError, OSError) as exc:
                    self.respond({"error": str(exc)}, status=400)
                except sqlite3.Error:
                    self.respond(
                        {"error": "Unable to save. Your draft is kept; try again."},
                        status=503,
                    )
                return
            if self.path != "/read-status":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400)
                return
            if not 0 < length <= 16384:
                self.send_error(400)
                return
            fields = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
            token = fields.get("csrf_token", [""])[0]
            if not secrets.compare_digest(token, csrf_token):
                self.send_error(403, "Refresh the page and try again.")
                return
            state = fields.get("is_read", [""])[0]
            url = fields.get("url", [""])[0]
            if state not in {"0", "1"} or not url:
                self.send_error(400)
                return
            try:
                with sqlite3.connect(
                    Path(database).resolve().as_uri() + "?mode=rw", uri=True
                ) as db:
                    result = db.execute(
                        "UPDATE articles SET is_read=? WHERE url=? AND source_id IN (SELECT id FROM sources WHERE enabled=1)",
                        (int(state), url),
                    )
                    if result.rowcount != 1:
                        self.send_error(404, "Article not found.")
                        return
            except sqlite3.Error:
                self.send_error(503, "Unable to save read status.")
                return
            article_id = "article-" + hashlib.sha256(url.encode()).hexdigest()[:16]
            self.send_response(303)
            self.send_header(
                "Location", "/classic?" + urlencode({"open": url}) + "#" + article_id
            )
            self.send_header("Content-Length", "0")
            self.end_headers()

    return ThreadingHTTPServer((host, port), Handler)


def main():
    """Run authenticated accounts and temporary guest reading on one server."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/intel_sourcerer.sqlite3")
    parser.add_argument("--config", default="config/sources.toml")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--secure-cookies", action="store_true",
                        help="Set when the public server uses HTTPS")
    args = parser.parse_args()
    server = create_server(args.database, args.port, args.config,
                           secure_cookies=args.secure_cookies, host=args.host)
    print(f"Intel Sourcerer: http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
