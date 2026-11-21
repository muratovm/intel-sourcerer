"""Priority watchlists (vendors, products, actors, CVEs, TTPs) for highlighting incoming threat intel."""
from datetime import datetime, timezone
import re
import uuid

from .iocs import kev_info
from .sessions import SessionError

MAX_TERM = 80
MAX_WATCHLISTS = 100


def _now():
    return datetime.now(timezone.utc).isoformat()


def clean_term(term):
    if not isinstance(term, str):
        raise SessionError("Enter a watchlist term.")
    value = re.sub(r"\s+", " ", term).strip()
    if not value or len(value) > MAX_TERM or not value.isprintable():
        raise SessionError(f"Watchlist terms must be between 1 and {MAX_TERM} characters.")
    return value


def list_watchlists(db):
    return [
        {"id": row["id"], "term": row["term"], "created_at": row["created_at"]}
        for row in db.execute("SELECT id, term, created_at FROM watchlists ORDER BY term COLLATE NOCASE")
    ]


def add_watchlist(db, term):
    value = clean_term(term)
    existing = db.execute("SELECT id, term, created_at FROM watchlists WHERE term=?", (value,)).fetchone()
    if existing:
        return {"id": existing["id"], "term": existing["term"], "created": False}
    count = db.execute("SELECT COUNT(*) FROM watchlists").fetchone()[0]
    if count >= MAX_WATCHLISTS:
        raise SessionError(f"Watchlists support up to {MAX_WATCHLISTS} terms.")
    item_id = uuid.uuid4().hex
    db.execute("INSERT INTO watchlists(id, term, created_at) VALUES (?,?,?)", (item_id, value, _now()))
    return {"id": item_id, "term": value, "created": True}


def delete_watchlist(db, item_id):
    if not isinstance(item_id, str) or not item_id:
        raise SessionError("Watchlist term not found.")
    db.execute("DELETE FROM watchlists WHERE id=?", (item_id,))
    return {"deleted": item_id}


def match_watchlists(terms, title, publisher, content_text="", cves=()):
    """Return watched terms appearing in an article's title, publisher, text, or CVEs."""
    if not terms:
        return []
    haystack = " ".join([title or "", publisher or "", (content_text or "")[:8000], *cves]).casefold()
    return [item["term"] for item in terms if item["term"].casefold() in haystack]


def article_cve_summary(db):
    """Return (cves_by_url, kev_by_url) from already-indexed article indicators in one query."""
    cves_by_url = {}
    kev_by_url = {}
    for row in db.execute("SELECT article_url, value FROM article_indicators WHERE kind='cves' ORDER BY value"):
        url, cve = row["article_url"], row["value"]
        cves_by_url.setdefault(url, []).append(cve)
        if kev_info(cve) is not None:
            kev_by_url.setdefault(url, []).append(cve)
    return cves_by_url, kev_by_url
