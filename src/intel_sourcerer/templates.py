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


def load_template(name):
    """Expand trusted page fragments without evaluating browser content."""
    source = (UI_ROOT / name).read_text(encoding="utf-8")
    source = re.sub(r"<!-- header: (\S*) -->", lambda match: _header(match.group(1)), source)
    return re.sub(
        r"<!-- include: ([a-zA-Z0-9_./-]+) -->",
        lambda match: load_template(match.group(1)),
        source,
    )


def render_template(name, **context):
    source = load_template(name)
    return Template(source).substitute(context)
