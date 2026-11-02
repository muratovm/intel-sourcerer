"""Per-article reading state: highlights, reading position and triage decisions."""
from datetime import datetime, timedelta, timezone
import uuid

from .sessions import SessionError

MAX_QUOTE = 20_000
MAX_CONTEXT = 200


def _article_exists(db, url):
    if not isinstance(url, str) or not db.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone():
        raise SessionError("Article not found. Refresh and try again.")


def list_highlights(db, url):
    return [
        dict(row)
        for row in db.execute(
            "SELECT id,quote,prefix,suffix,created_at FROM article_highlights WHERE article_url=? ORDER BY created_at,id",
            (url,),
        )
    ]


def save_highlight(db, url, quote, prefix="", suffix=""):
    _article_exists(db, url)
    if not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_QUOTE:
        raise SessionError("Select a shorter passage to highlight.")
    if not all(isinstance(part, str) and len(part) <= MAX_CONTEXT for part in (prefix, suffix)):
        raise SessionError("Invalid highlight.")
    highlight = {
        "id": uuid.uuid4().hex,
        "quote": quote,
        "prefix": prefix,
        "suffix": suffix,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.execute(
        "INSERT INTO article_highlights(id,article_url,quote,prefix,suffix,created_at) VALUES (?,?,?,?,?,?)",
        (highlight["id"], url, quote, prefix, suffix, highlight["created_at"]),
    )
    return highlight


def delete_highlight(db, identifier):
    if not isinstance(identifier, str) or db.execute("DELETE FROM article_highlights WHERE id=?", (identifier,)).rowcount != 1:
        raise SessionError("Highlight not found. Refresh and try again.")
    return {"deleted": identifier}


def get_position(db, url):
    row = db.execute("SELECT fraction,updated_at FROM reading_positions WHERE article_url=?", (url,)).fetchone()
    return dict(row) if row else None


def save_position(db, url, fraction):
    _article_exists(db, url)
    if type(fraction) not in {int, float} or not 0 <= fraction <= 1:
        raise SessionError("Invalid reading position.")
    updated_at = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO reading_positions(article_url,fraction,updated_at) VALUES (?,?,?) "
        "ON CONFLICT(article_url) DO UPDATE SET fraction=excluded.fraction,updated_at=excluded.updated_at",
        (url, fraction, updated_at),
    )
    return {"updated_at": updated_at}


def set_triage(db, urls, decision):
    """Record keep or skip for articles; None returns them to the triage list."""
    if decision not in {"keep", "skip", None}:
        raise SessionError("Unknown triage decision.")
    if not isinstance(urls, list) or not urls or not all(isinstance(url, str) for url in urls):
        raise SessionError("Choose at least one article.")
    db.executemany("UPDATE articles SET triage=? WHERE url=?", [(decision, url) for url in urls])
    return {"decision": decision, "urls": urls}


def week_counts(db, now=None):
    """Articles saved and first read in the last seven days."""
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=7)).isoformat()
    saved = db.execute("SELECT COUNT(*) FROM articles WHERE julianday(discovered_at)>=julianday(?)", (since,)).fetchone()[0]
    read = db.execute("SELECT COUNT(*) FROM article_reads WHERE julianday(read_at)>=julianday(?)", (since,)).fetchone()[0]
    return {"saved": saved, "read": read}
