"""Public temporary-session routes shared by combined and standalone viewers."""

import threading

import httpx

from .net_safety import RateLimiter, guest_enabled
from .templates import UI_ROOT, load_template

# Guests need no account, so these keep them from turning the server into a free fetcher.
# The budget counts URLs fetched, because refreshing a guest page re-fetches its queue.
GUEST_BUDGET = RateLimiter(120, 600)
GUEST_SLOTS = threading.BoundedSemaphore(3)


def guest_page(path):
    """Return a guest page or asset without accessing account storage."""
    if not guest_enabled():
        return None
    if path in {"/guest", "/guest/read"}:
        from .temporary import temporary_page

        return temporary_page("guest", guest=True), "text/html; charset=utf-8"
    if path == "/guest-reader.js":
        from .temporary import temporary_reader_script

        return temporary_reader_script(), "text/javascript; charset=utf-8"
    if path in {"/guest.js", "/temporary.js"}:
        name = "auth/guest.js" if path == "/guest.js" else "workspace/temporary.js"
        return (UI_ROOT / name).read_bytes(), "text/javascript; charset=utf-8"
    return None


def guest_option():
    return load_template("auth/guest-option.html") if guest_enabled() else ""


def prepare_guest(headers, stream, client="unknown"):
    """Parse bounded guest input without persisting articles or notes."""
    from .temporary import parse_session_content

    if not guest_enabled():
        return {"error": "Guest reading is turned off."}, 404
    if not GUEST_BUDGET.allow(client):
        return {"error": "Too many requests. Try again in a few minutes."}, 429
    if not GUEST_SLOTS.acquire(blocking=False):
        return {"error": "The reader is busy. Try again shortly."}, 503

    def charge():
        if not GUEST_BUDGET.allow(client):
            raise ValueError("Too many articles fetched. Try again in a few minutes.")

    try:
        length = int(headers.get("Content-Length", "0"))
        if (headers.get("X-Guest-Request") != "1"
                or "Transfer-Encoding" in headers
                or not 0 < length <= 20_000_000):
            raise ValueError("Invalid guest request.")
        body = stream.read(length)
        if len(body) != length:
            raise ValueError("Incomplete file.")
        return parse_session_content(body, headers.get("Content-Type", ""), charge), 200
    except (ValueError, AttributeError, TypeError, OSError, httpx.HTTPError) as exc:
        return {"error": str(exc)}, 400
    finally:
        GUEST_SLOTS.release()
