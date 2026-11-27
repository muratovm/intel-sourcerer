"""Render local HTML templates with explicitly escaped values.

Callers escape text and attributes before passing them here. HTML fragments
must come from our own templates or the notes sanitizer.
"""

import re
from pathlib import Path
from string import Template

UI_ROOT = Path(__file__).parent / "ui"
SETUP_FIELD = (
    '<label for="auth-setup-code">Setup code</label>'
    '<input id="auth-setup-code" name="setup_code" autocomplete="off" spellcheck="false" '
    'placeholder="Printed in the server log" required />'
)


def _header(active):
    """Every main page draws the same header, differing only in which link is marked."""
    link = f'<a href="{active}">'
    return (UI_ROOT / "shared/header.html").read_text(encoding="utf-8").replace(
        link, f'<a class="active" aria-current="page" href="{active}">'
    )


# Served by the combined server's own table rather than the shared one.
OTHER_ASSETS = {"/app.js": ("shared/app.js",), "/temporary.js": ("workspace/temporary.js",)}
ASSET_LINK = re.compile(r'(\b(?:src|href)=")(/[A-Za-z0-9_./-]+\.(?:js|css))(")')


def asset_version(path):
    """A short tag that changes whenever the served file changes, or None for unknown paths."""
    from .http_common import SHARED_STATIC_FILES

    entry = SHARED_STATIC_FILES.get(path) or OTHER_ASSETS.get(path)
    if not entry:
        return None
    try:
        stat = (UI_ROOT / entry[0]).stat()
    except OSError:
        return None
    return format(stat.st_mtime_ns ^ stat.st_size, "x")[-10:]


def _versioned(source):
    """Point script and style links at ?v=<version>, so no browser or proxy cache can serve an old copy."""
    def link(match):
        version = asset_version(match.group(2))
        return match.group(0) if version is None else f"{match.group(1)}{match.group(2)}?v={version}{match.group(3)}"

    return ASSET_LINK.sub(link, source)


def _expand(name):
    source = (UI_ROOT / name).read_text(encoding="utf-8")
    source = re.sub(r"<!-- header: (\S*) -->", lambda match: _header(match.group(1)), source)
    return re.sub(
        r"<!-- include: ([a-zA-Z0-9_./-]+) -->",
        lambda match: _expand(match.group(1)),
        source,
    )


def versioned(html):
    return _versioned(html)


def load_template(name):
    """Expand trusted page fragments without evaluating browser content."""
    return _versioned(_expand(name))


def render_template(name, **context):
    source = load_template(name)
    return Template(source).substitute(context)
