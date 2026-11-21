"""Shared HTTP security headers, upload limits, and path validation for web, viewer, and temporary servers."""

import os
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/Toronto"

PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"
REFERRER_POLICY = "strict-origin-when-cross-origin"

SHARED_STATIC_FILES = {
    "/reader.css": ("shared/styles.css", "text/css; charset=utf-8"),
    "/reader.js": ("workspace/workspace.js", "text/javascript; charset=utf-8"),
    "/workspace/storage.js": ("workspace/storage.js", "text/javascript; charset=utf-8"),
    "/workspace/media.js": ("workspace/media.js", "text/javascript; charset=utf-8"),
    "/workspace/highlights.js": ("workspace/highlights.js", "text/javascript; charset=utf-8"),
    "/workspace/iocs.js": ("workspace/iocs.js", "text/javascript; charset=utf-8"),
    "/workspace/panes.js": ("workspace/panes.js", "text/javascript; charset=utf-8"),
    "/workspace/shortcuts.js": ("workspace/shortcuts.js", "text/javascript; charset=utf-8"),
    "/focus.js": ("workspace/focus.js", "text/javascript; charset=utf-8"),
    "/math.js": ("workspace/math.js", "text/javascript; charset=utf-8"),
    "/library.js": ("library/library.js", "text/javascript; charset=utf-8"),
    "/indicators.js": ("indicators/indicators.js", "text/javascript; charset=utf-8"),
    "/notes-editor.js": ("shared/notes-editor.js", "text/javascript; charset=utf-8"),
    "/tag-editor.js": ("shared/tag-editor.js", "text/javascript; charset=utf-8"),
    "/topics.js": ("topics/topics.js", "text/javascript; charset=utf-8"),
    "/account.js": ("auth/account.js", "text/javascript; charset=utf-8"),
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


def display_timezone():
    """Configured display timezone (INTEL_TIMEZONE), defaulting to America/Toronto."""
    name = os.environ.get("INTEL_TIMEZONE", "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def frame_ancestors():
    """Pages that may embed this app. Nothing by default, to protect the login from clickjacking."""
    value = os.environ.get("INTEL_SOURCERER_FRAME_ANCESTORS", "'none'")
    return value.replace("\r", "").replace("\n", "").strip() or "'none'"


def build_csp(*, include_pwa=True, ancestors=None):
    """Consistent Content-Security-Policy shared across combined, viewer, and temporary servers."""
    ancestors = frame_ancestors() if ancestors is None else ancestors
    pwa = "worker-src 'self'; manifest-src 'self'; " if include_pwa else ""
    return (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        f"{pwa}img-src 'self' https: http:; media-src 'self' https: http:; "
        "frame-src https://www.youtube-nocookie.com https://player.vimeo.com; "
        f"base-uri 'none'; form-action 'self'; frame-ancestors {ancestors}"
    )


def send_security_headers(handler, *, csp, over_https=False, cache_control="no-store"):
    """Apply standard security headers to a BaseHTTPRequestHandler response."""
    handler.send_header("Cache-Control", cache_control)
    handler.send_header("Content-Security-Policy", csp)
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", REFERRER_POLICY)
    handler.send_header("Permissions-Policy", PERMISSIONS_POLICY)
    if over_https:
        handler.send_header("Strict-Transport-Security", "max-age=31536000")


def post_size_limit(path):
    """Maximum POST body size in bytes for a given route path."""
    if path in {"/api/file-upload", "/api/transcript-audio"}:
        return 100_000_000
    if path == "/api/pdf-upload":
        return 20_000_000
    if path.startswith("/auth/"):
        return 8192
    return 1_000_000


def is_safe_proxy_path(raw_target):
    """Reject protocol-relative URLs, backslashes, and dot-segments before proxying."""
    if not raw_target.startswith("/") or raw_target.startswith("//"):
        return False
    raw_path = urlsplit(raw_target).path
    if "\\" in raw_path:
        return False
    segments = unquote(raw_path).replace("\\", "/").split("/")
    return ".." not in segments and "." not in segments
