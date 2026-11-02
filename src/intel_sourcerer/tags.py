"""Tags the reader chooses, and the other saved articles that share them."""
from datetime import datetime, timezone
import re

from .sessions import SessionError

MAX_TAG = 40
MAX_TAGS_PER_ARTICLE = 30


def clean_tag(tag):
    """Tags compare without regard to case, and a leading # is habit rather than part of the name."""
    if not isinstance(tag, str):
        raise SessionError("Enter a tag.")
    tag = re.sub(r"\s+", " ", tag).strip().lstrip("#").strip()
    if not tag or len(tag) > MAX_TAG or not tag.isprintable():
        raise SessionError(f"Tags need between 1 and {MAX_TAG} characters.")
    return tag


def _require_article(db, url):
    if not isinstance(url, str) or not db.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone():
        raise SessionError("Article not found. Refresh and try again.")


def add_tag(db, url, tag):
    _require_article(db, url)
    tag = clean_tag(tag)
    # Reuse the spelling already in the library, so "APT29" and "apt29" stay one tag.
    existing = db.execute("SELECT tag FROM article_tags WHERE tag=? ORDER BY created_at LIMIT 1", (tag,)).fetchone()
    count = db.execute("SELECT count(*) FROM article_tags WHERE article_url=?", (url,)).fetchone()[0]
    if count >= MAX_TAGS_PER_ARTICLE:
        raise SessionError(f"An article can have up to {MAX_TAGS_PER_ARTICLE} tags.")
    db.execute(
        "INSERT OR IGNORE INTO article_tags(article_url,tag,created_at) VALUES (?,?,?)",
        (url, existing[0] if existing else tag, datetime.now(timezone.utc).isoformat()),
    )
    return tag_state(db, url)


def remove_tag(db, url, tag):
    _require_article(db, url)
    if not isinstance(tag, str):
        raise SessionError("Enter a tag.")
    db.execute("DELETE FROM article_tags WHERE article_url=? AND tag=?", (url, tag))
    return tag_state(db, url)


def tag_state(db, url):
    """This article's tags with the other articles under each, plus every tag for suggestions."""
    _require_article(db, url)
    mine = [
        row[0]
        for row in db.execute("SELECT tag FROM article_tags WHERE article_url=? ORDER BY tag COLLATE NOCASE", (url,))
    ]
    tags = []
    for tag in mine:
        others = db.execute(
            """SELECT a.url,a.title,a.published_at,s.name AS publisher FROM article_tags t
            JOIN articles a ON a.url=t.article_url JOIN sources s ON s.id=a.source_id AND s.enabled=1
            WHERE t.tag=? AND t.article_url<>? ORDER BY julianday(a.published_at) DESC,a.url""",
            (tag, url),
        ).fetchall()
        tags.append({"tag": tag, "others": [dict(row) for row in others]})
    suggestions = [
        row[0]
        for row in db.execute(
            """SELECT t.tag FROM article_tags t JOIN articles a ON a.url=t.article_url JOIN sources s ON s.id=a.source_id AND s.enabled=1
            GROUP BY t.tag COLLATE NOCASE ORDER BY count(*) DESC,t.tag COLLATE NOCASE"""
        )
    ]
    return {"tags": tags, "suggestions": [tag for tag in suggestions if tag.casefold() not in {item.casefold() for item in mine}]}
