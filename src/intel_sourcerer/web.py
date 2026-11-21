"""A small, browser interface for saved articles."""

import argparse
import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
from html import escape
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .api_dispatch import handle_api_get, handle_api_mutation
from .classic import read_articles, render_page
from .config import load_sources, set_source_enabled
from .discovery import import_link
from .guest_routes import guest_option
from .http_common import (
    SHARED_STATIC_FILES,
    build_csp,
    frame_ancestors,
    post_size_limit,
    send_security_headers,
)
from .importer import import_articles
from .jobs import JobConflictError, start_job
from .net_safety import (
    LOOPBACK_HOSTS,
    BoundedHTTPServer,
    client_address,
    env_flag,
    forwarded_https,
    trusted_proxies,
)
from .sessions import SessionError, session_db
from .storage import BUSY_TIMEOUT, connect_database
from .templates import SETUP_FIELD, UI_ROOT, load_template, render_template

VALID_DISCOVERY_DAYS = {0, 1, 3, 7, 14, 30, 60, 90, 180, 365}



APP_HEAD = (
    '<link rel="manifest" href="/manifest.webmanifest" />'
    '<meta name="theme-color" content="#071f4a" />'
    '<link rel="apple-touch-icon" href="/icon-192.png" />'
    '<script src="/app.js"></script>'
)
ACCOUNT_LINK = '<a href="/account">Account</a>'
STATIC_FILES = {
    "/app.js": ("shared/app.js", "text/javascript; charset=utf-8"),
    "/sw.js": ("shared/sw.js", "text/javascript; charset=utf-8"),
    "/save.js": ("shared/save.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("shared/manifest.webmanifest", "application/manifest+json"),
    "/icon-192.png": ("shared/icon-192.png", "image/png"),
    "/icon-512.png": ("shared/icon-512.png", "image/png"),
}


def youtube_download_id(url, is_admin):
    """The video to download for transcription. Downloads use the owner's Chrome cookies, so only administrators get them."""
    from . import transcripts
    from .youtube import video_id

    if not is_admin or not transcripts.downloader_command() or not isinstance(url, str):
        return None
    if not url.startswith("https://www.youtube.com/watch?v="):
        return None
    try:
        return video_id(url)
    except ValueError:
        return None


def transcript_info(database, row, is_admin=False):
    """What the reader needs to offer, run or show a transcript for audio and video."""
    if row["content_format"] not in {"audio", "video"}:
        return None
    from . import transcripts

    job = transcripts.status(database, row["url"])
    return dict(
        available=transcripts.available(),
        local=transcripts.local_media_path(database, row["url"]) is not None,
        youtube=youtube_download_id(row["url"], is_admin) is not None,
        has_transcript=transcripts.has_transcript(row["content_markdown"]),
        state=job.get("state", ""),
        error=job.get("error", ""),
    )


def read_state_articles(database):
    """Slim projection for /api/state: omits full HTML/Markdown bodies and truncates text to what article_types needs."""
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT)) as db, db:
        db.row_factory = sqlite3.Row
        return db.execute("""SELECT a.title,a.url,a.published_at,substr(a.content_text,1,1500) AS content_text,a.is_read,a.notes,a.notes_format,a.notes_updated_at,a.content_format,a.triage,s.name AS publisher
            FROM articles a JOIN sources s ON s.id=a.source_id
            WHERE s.enabled=1 ORDER BY a.published_at DESC,a.title""").fetchall()


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
    secure_cookies = secure_cookies or env_flag("INTEL_SECURE_COOKIES")
    proxies = trusted_proxies()
    from .backups import backup_database

    backup_database(database)
    csrf_token = secrets.token_urlsafe(32)
    locks_guard = threading.Lock()
    discovery_locks = {}

    def discovery_lock_for(path):
        key = str(Path(path).resolve())
        with locks_guard:
            lock = discovery_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                discovery_locks[key] = lock
            return lock

    from .auth import Accounts

    accounts = Accounts(database, config) if auth_enabled else None
    # A localhost server can still be reached through a public viewer or reverse proxy.
    setup_code = os.environ.get("INTEL_SETUP_TOKEN", "").strip()
    env_setup_code = bool(setup_code)

    def guard_first_account(rotate=False):
        """Whoever creates the first account becomes administrator, so a reachable server asks for a code."""
        nonlocal setup_code
        if rotate and not env_setup_code:
            setup_code = ""
        if accounts and accounts.empty() and not setup_code and (api_only or proxies or host not in LOOPBACK_HOSTS):
            setup_code = secrets.token_urlsafe(18)
            print(f"First-run setup code, needed once to create the administrator: {setup_code}", flush=True)

    guard_first_account()
    upload_slots = threading.BoundedSemaphore(2)
    base_database, base_config, default_csrf = database, config, csrf_token

    migrated = {str(database)}
    migrated_lock = threading.Lock()

    def ensure_schema(path):
        """Upgrade each account's database once, the first time this server sees it."""
        key = str(path)
        with migrated_lock:
            if key in migrated:
                return
        connect_database(path).close()
        with migrated_lock:
            migrated.add(key)

    class Handler(BaseHTTPRequestHandler):
        # A client that goes quiet gives its thread back rather than holding it forever.
        timeout = 30

        def real_client(self):
            return client_address(self.client_address[0], self.headers, proxies)

        def over_https(self):
            # Only an authenticated viewer may describe the browser-facing scheme.
            viewer_https = (
                api_only
                and secrets.compare_digest(
                    self.headers.get("X-Intel-Service-Key", "").encode(), service_key.encode()
                )
                and self.headers.get("X-Intel-Client-Proto") == "https"
            )
            return secure_cookies or viewer_https or forwarded_https(self.client_address[0], self.headers, proxies)

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
                token, csrf = accounts.new_session()
                self.cookie(token, max_age=1800)
                user = None
            else:
                csrf = session["csrf"]
                user = (
                    {
                        "id": session["user_id"],
                        "username": session["username"],
                        "admin": bool(session["admin"]),
                    }
                    if session["user_id"]
                    else None
                )
            empty = accounts.empty()
            self.respond(
                {
                    "user": user,
                    "csrf_token": csrf,
                    "setup_required": empty,
                    "setup_code_required": bool(setup_code) and empty,
                }
            )

        def redirect(self, location):
            self.respond("", "text/html; charset=utf-8", 303, {"Location": location})

        def cookie(self, token, max_age=28800):
            self.pending_cookie = (
                f"intel_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
                + ("; Secure" if self.over_https() else "")
            )

        def clear_cookie(self):
            self.pending_cookie = (
                "intel_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
                + ("; Secure" if self.over_https() else "")
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
                self.cookie(token, max_age=1800)
            else:
                csrf = session["csrf"]
            content = render_template(
                "auth/form.html",
                action="/auth/setup" if setup else "/auth/login",
                csrf=escape(csrf),
                autocomplete="new-password" if setup else "current-password",
                button="Create administrator" if setup else "Sign in",
                setup_field=SETUP_FIELD if setup and setup_code else "",
            )
            self.respond(
                render_template(
                    "auth/page.html",
                    title="Create your account" if setup else "Sign in",
                    description="The first account owns your existing library and can add other users."
                    if setup
                    else "Your library, notes, and reading history are private to your account.",
                    error=escape(error),
                    content=content + guest_option(),
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
            ensure_schema(session["database_path"])
            return session["database_path"], session["config_path"], session["csrf"]

        def account_page(self, error="", status=200):
            if api_only and self.headers.get("Accept") == "application/json":
                self.respond({"message": error, "page": "account"}, status=status)
                return
            csrf = escape(self.session["csrf"])
            content = render_template("auth/password.html", csrf=csrf)
            signout = render_template("auth/signout.html", username=escape(self.session["username"]), csrf=csrf)
            from .stats import render_stats_section, stats_data

            with closing(session_db(self.session["database_path"])) as db:
                stats = render_stats_section(stats_data(db))
            self.respond(
                render_template(
                    "auth/account-page.html",
                    error=escape(error),
                    content=content,
                    stats=stats,
                    signout=signout,
                ),
                "text/html; charset=utf-8",
                status,
            )

        def auth_post(self):
            path = urlsplit(self.path).path
            session = accounts.session(self.headers.get("Cookie"))
            try:
                if "Transfer-Encoding" in self.headers:
                    raise ValueError("Invalid request.")
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
                client_ip = (
                    self.headers.get("X-Intel-Client-IP", self.client_address[0])
                    if api_only
                    else self.real_client()
                )
                if path == "/auth/setup":
                    if setup_code:
                        accounts.record_setup_attempt(client_ip)
                        if not secrets.compare_digest(
                            data.get("setup_code", [""])[0].strip().encode(),
                            setup_code.encode(),
                        ):
                            raise ValueError("That setup code isn't right. Check the server log.")
                    user_id = accounts.create_user(username, password, first=True)
                    if setup_code:
                        accounts.clear_setup_attempts(client_ip)
                elif path == "/auth/login":
                    user_id = accounts.login(username, password, client_ip)
                elif path in {"/auth/logout", "/auth/create-user", "/auth/password", "/auth/delete-account"}:
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
                    if path == "/auth/delete-account":
                        accounts.delete_account(session["user_id"], username, password)
                        guard_first_account(rotate=True)
                        self.clear_cookie()
                        self.redirect("/setup" if accounts.empty() else "/login")
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
                    self.clear_cookie()
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
                    path in {"/auth/create-user", "/auth/password", "/auth/delete-account"}
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
                        body = body.replace("<!-- account-link -->", ACCOUNT_LINK)
                    elif not accounts:
                        # With no accounts there is no Account page to hold the stats.
                        body = body.replace("<!-- account-link -->", '<a href="/stats">Stats</a>')
                    body = body.replace("</title>", "</title>" + APP_HEAD, 1)
                body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            send_security_headers(
                self,
                csp=build_csp(include_pwa=True),
                over_https=self.over_https(),
            )
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
            if not api_only and path in STATIC_FILES:
                name, content_type = STATIC_FILES[path]
                self.respond((UI_ROOT / name).read_bytes(), content_type)
                return
            if not api_only and path == "/save":
                # Public: a link from another site arrives without the Strict session cookie.
                self.respond(
                    load_template("shared/save.html").replace(
                        "__TOKEN__", "" if accounts else default_csrf
                    ),
                    "text/html; charset=utf-8",
                )
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
            public = (
                path in SHARED_STATIC_FILES
                or path == "/brand-icon.png"
                or path.startswith("/vendor/katex/")
            )
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
                from .library import render_library, export_article, storage_usage

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
                                params.get("tag", [""])[0],
                                storage_usage(database),
                                params.get("type", [""])[0],
                                params.get("progress", [""])[0] == "1",
                                page=params.get("page", ["1"])[0],
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
            if path in SHARED_STATIC_FILES:
                name, content_type = SHARED_STATIC_FILES[path]
                self.respond((UI_ROOT / name).read_bytes(), content_type)
                return
            if path.startswith("/vendor/katex/"):
                self.respond({"error": "Not found"}, status=404)
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
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors " + frame_ancestors())
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
                if self.over_https():
                    self.send_header("Strict-Transport-Security", "max-age=31536000")
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
            if path in {"/topics", "/topic"}:
                from .topics import list_topics, render_topic, render_topics, topic_data

                params = parse_qs(urlsplit(self.path).query)
                with closing(session_db(database)) as db:
                    html = (
                        render_topics(
                            list_topics(db)["topics"],
                            params.get("q", [""])[0],
                            params.get("status", [""])[0],
                            csrf_token,
                        )
                        if path == "/topics"
                        else render_topic(topic_data(db, params.get("id", [""])[0]), csrf_token)
                    )
                self.respond(html, "text/html; charset=utf-8")
                return
            if path == "/stats":
                if accounts:
                    self.redirect("/account")
                    return
                from .stats import render_stats

                with closing(session_db(database)) as db:
                    self.respond(render_stats(db), "text/html; charset=utf-8")
                return
            if path == "/sources":
                try:
                    self.respond(
                        render_sources(config, csrf_token), "text/html; charset=utf-8"
                    )
                except ValueError as exc:
                    self.respond({"error": str(exc)}, status=500)
                except OSError:
                    self.respond({"error": "Source configuration unavailable."}, status=500)
                return
            if path == "/":
                content = load_template("workspace/index.html").replace("__TOKEN__", csrf_token)
                self.respond(content, "text/html; charset=utf-8")
                return
            if path.startswith("/api/"):
                try:
                    with closing(session_db(database)) as db, db:
                        payload, status = handle_api_get(
                            db,
                            database,
                            config,
                            path,
                            urlsplit(self.path).query,
                            is_admin=self.is_admin,
                            read_state_articles=read_state_articles,
                            transcript_info=transcript_info,
                        )
                    self.respond(payload, status=status)
                except SessionError as exc:
                    self.respond({"error": str(exc)}, status=404)
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
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'self'; frame-ancestors " + frame_ancestors(),
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            if self.over_https():
                self.send_header("Strict-Transport-Security", "max-age=31536000")
            self.end_headers()
            self.wfile.write(content)

        def read_upload(self, length):
            """Acquire a slot before buffering; do_POST releases it after all handling."""
            if not upload_slots.acquire(blocking=False):
                self.close_connection = True
                self.respond({"error": "Another upload is in progress. Try again shortly."}, status=429)
                return None
            self.upload_acquired = True
            return self.rfile.read(length)

        def do_POST(self):
            self.upload_acquired = False
            try:
                self.handle_post()
            finally:
                # handle_post's frame (including any payload) is gone before admission resumes.
                if self.upload_acquired:
                    upload_slots.release()

        def handle_post(self):
            path = urlsplit(self.path).path
            if not api_only and path == "/guest/prepare":
                from .guest_routes import prepare_guest

                result, status = prepare_guest(self.headers, self.rfile, self.real_client())
                self.respond(result, status=status)
                return
            if not self.service_authorized():
                return
            if api_only and not path.startswith(
                ("/api/", "/auth/")
            ):
                self.respond({"error": "Not found"}, status=404)
                return
            if accounts and path.startswith("/auth/"):
                self.auth_post()
                return
            context = self.context()
            if not context:
                return
            database, config, csrf_token = context
            user_lock = discovery_lock_for(database)
            if path.startswith("/api/"):
                try:
                    if "Transfer-Encoding" in self.headers:
                        raise ValueError("Invalid request.")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= post_size_limit(path):
                        raise ValueError("Invalid request size")
                    if not secrets.compare_digest(
                        self.headers.get("X-CSRF-Token", ""), csrf_token
                    ):
                        self.respond(
                            {"error": "Refresh the page and try again."}, status=403
                        )
                        return
                    if path == "/api/file-upload":
                        from .media_import import import_file
                        from urllib.parse import unquote

                        payload = self.read_upload(length)
                        if payload is None:
                            return
                        if len(payload) != length:
                            raise ValueError("Incomplete upload")
                        filename = unquote(self.headers.get("X-Filename", ""))
                        url = asyncio.run(import_file(database, payload, filename))
                        payload = None
                        from . import transcripts

                        media = transcripts.local_media_path(database, url)
                        if media and media.suffix.lower() in transcripts.AUDIO_UPLOADS and transcripts.available():
                            transcripts.start(database, url, media)
                        self.respond({"url": url})
                        return
                    if path == "/api/transcript-audio":
                        from . import transcripts
                        from urllib.parse import unquote

                        url = unquote(self.headers.get("X-Article-URL", ""))
                        with closing(connect_database(database)) as db:
                            row = db.execute("SELECT content_format FROM articles WHERE url=?", (url,)).fetchone()
                        if not row or row[0] not in {"audio", "video"}:
                            raise ValueError("Attach audio to a saved video or audio item.")
                        if not transcripts.available():
                            raise ValueError("Transcription needs mlx_whisper on this machine.")
                        payload = self.read_upload(length)
                        if payload is None:
                            return
                        if len(payload) != length:
                            raise ValueError("Incomplete upload")
                        path_saved = transcripts.save_upload(
                            database, payload, unquote(self.headers.get("X-Filename", ""))
                        )
                        payload = None
                        self.respond(transcripts.start(database, url, path_saved, remove_after=True))
                        return
                    if path == "/api/pdf-upload":
                        from .discovery import import_pdf_upload

                        payload = self.read_upload(length)
                        if payload is None:
                            return
                        if len(payload) != length:
                            raise ValueError("Incomplete PDF upload")
                        url = asyncio.run(import_pdf_upload(database, payload))
                        self.respond({"url": url})
                        return
                    data = json.loads(self.rfile.read(length))
                    if path in {"/api/notes", "/api/delete-article", "/api/topic-notes", "/api/topic-delete"}:
                        backup_database(database)
                    if path == "/api/source-status":
                        with user_lock:
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
                    if path == "/api/transcribe":
                        from . import transcripts

                        url = data.get("url")
                        media = transcripts.local_media_path(database, url) if isinstance(url, str) else None
                        video = None if media else youtube_download_id(url, self.is_admin)
                        if video:
                            with closing(connect_database(database)) as video_db:
                                if not video_db.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone():
                                    raise ValueError("Article not found.")
                        elif not media:
                            raise ValueError("Only local audio and video files, or YouTube videos for the administrator, can be transcribed directly.")
                        if not transcripts.available():
                            raise ValueError("Transcription needs mlx_whisper on this machine.")
                        self.respond(transcripts.start(database, url, media, youtube=video))
                        return
                    if path == "/api/jobs/discover":
                        days = data.get("days", 30)
                        if type(days) is not int or days not in VALID_DISCOVERY_DAYS:
                            raise ValueError("Choose a valid time window.")

                        def run_discover():
                            now = datetime.now(timezone.utc)
                            since = (
                                datetime(1970, 1, 1, tzinfo=timezone.utc)
                                if days == 0
                                else now - timedelta(days=days)
                            )
                            return asyncio.run(import_articles(config, database, since, now))

                        job = start_job(database, "discover", user_lock, run_discover)
                        self.respond(job, status=202)
                        return
                    if path == "/api/jobs/playlist":
                        if not self.is_admin:
                            raise ValueError(
                                "Playlist import reads YouTube with the owner's browser cookies, so only an administrator can use it."
                            )
                        from .youtube import import_playlist, playlist_id

                        submitted = data.get("url", "")
                        playlist = playlist_id(submitted)
                        if not playlist:
                            raise ValueError("Use a link to a specific YouTube playlist.")
                        topic_id = data.get("topic")
                        job = start_job(
                            database,
                            "playlist",
                            user_lock,
                            lambda: import_playlist(database, playlist, topic_id=topic_id),
                        )
                        self.respond(job, status=202)
                        return
                    if path in {"/api/discover", "/api/link"}:
                        if not user_lock.acquire(blocking=False):
                            self.respond(
                                {
                                    "error": "An article fetch is already running. Try again shortly."
                                },
                                status=409,
                            )
                            return
                        try:
                            if path == "/api/link":
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
                                from .youtube import fetch_durations, import_playlist, playlist_id

                                playlist = playlist_id(submitted)
                                if playlist and not self.is_admin:
                                    raise ValueError(
                                        "Playlist import reads YouTube with the owner's browser cookies, so only an administrator can use it."
                                    )
                                result = (
                                    import_playlist(database, playlist, topic_id=data.get("topic"))
                                    if playlist
                                    else {"url": asyncio.run(import_link(database, data.get("url"), fetch_durations if self.is_admin else None))}
                                )
                            else:
                                days = data.get("days", 30)
                                if type(days) is not int or days not in VALID_DISCOVERY_DAYS:
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
                            user_lock.release()
                        return
                    with closing(session_db(database)) as db, db:
                        db.execute("BEGIN IMMEDIATE")
                        result, status = handle_api_mutation(db, path, data)
                    self.respond(result, status=status)
                except (SessionError, JobConflictError) as exc:
                    self.respond({"error": str(exc)}, status=409)
                except (ValueError, httpx.HTTPError) as exc:
                    self.respond({"error": str(exc)}, status=400)
                except (TypeError, OSError):
                    self.respond({"error": "Unable to process request."}, status=400)
                except sqlite3.Error:
                    self.respond(
                        {"error": "Unable to save. Your draft is kept; try again."},
                        status=503,
                    )
                return
            if path != "/read-status":
                self.send_error(404)
                return
            if "Transfer-Encoding" in self.headers:
                self.send_error(400)
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
                with (
                    closing(
                        sqlite3.connect(
                            Path(database).resolve().as_uri() + "?mode=rw",
                            uri=True,
                            timeout=BUSY_TIMEOUT,
                        )
                    ) as db,
                    db,
                ):
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

    return BoundedHTTPServer((host, port), Handler)


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
