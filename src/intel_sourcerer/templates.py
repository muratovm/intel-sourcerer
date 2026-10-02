"""Render local HTML templates with explicitly escaped values.

Callers escape text and attributes before passing them here. HTML fragments
must come from our own templates or the notes sanitizer.
"""

import re
from pathlib import Path
from string import Template

UI_ROOT = Path(__file__).parent / "ui"


def load_template(name):
    """Expand trusted page fragments without evaluating browser content."""
    source = (UI_ROOT / name).read_text(encoding="utf-8")
    return re.sub(
        r"<!-- include: ([a-zA-Z0-9_./-]+) -->",
        lambda match: load_template(match.group(1)),
        source,
    )


def render_template(name, **context):
    source = load_template(name)
    return Template(source).substitute(context)
