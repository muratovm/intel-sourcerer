"""Public temporary-session routes shared by combined and standalone viewers."""

import httpx

from .templates import UI_ROOT


def guest_page(path):
    """Return a guest page or asset without accessing account storage."""
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


def prepare_guest(headers, stream):
    """Parse bounded guest input without persisting articles or notes."""
    from .temporary import parse_session_content

    try:
        length = int(headers.get("Content-Length", "0"))
        if (headers.get("X-Guest-Request") != "1"
                or "Transfer-Encoding" in headers
                or not 0 < length <= 20_000_000):
            raise ValueError("Invalid guest request.")
        body = stream.read(length)
        if len(body) != length:
            raise ValueError("Incomplete file.")
        return parse_session_content(body, headers.get("Content-Type", "")), 200
    except (ValueError, AttributeError, TypeError, OSError, httpx.HTTPError) as exc:
        return {"error": str(exc)}, 400
