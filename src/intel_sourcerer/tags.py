"""Legacy tag API compatibility layer backed directly by unified Topics."""
import re

from .sessions import SessionError
from .topics import MAX_NAME, add_article, remove_article

MAX_TAG = MAX_NAME
MAX_TAGS_PER_ARTICLE = 30


def clean_tag(tag):
    """Topics/tags compare without regard to case, and a leading # is habit rather than part of the name."""
    if not isinstance(tag, str):
        raise SessionError("Enter a topic.")
    tag = re.sub(r"\s+", " ", tag).strip().lstrip("#").strip()
    if not tag or len(tag) > MAX_TAG or not tag.isprintable():
        raise SessionError(f"Topic names need between 1 and {MAX_TAG} characters.")
    return tag


def _require_article(db, url):
    if not isinstance(url, str) or not db.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone():
        raise SessionError("Article not found. Refresh and try again.")


def add_tag(db, url, tag):
    _require_article(db, url)
    name = clean_tag(tag)
    count = db.execute("SELECT count(*) FROM topic_articles WHERE article_url=?", (url,)).fetchone()[0]
    if count >= MAX_TAGS_PER_ARTICLE:
        raise SessionError(f"An article can belong to up to {MAX_TAGS_PER_ARTICLE} topics.")
    add_article(db, url, name=name)
    return tag_state(db, url)


def remove_tag(db, url, tag):
    _require_article(db, url)
    if not isinstance(tag, str):
        raise SessionError("Enter a topic.")
    row = db.execute("SELECT id FROM topics WHERE name=?", (tag.strip().lstrip("#").strip(),)).fetchone()
    if row:
        remove_article(db, url, row[0])
    return tag_state(db, url)


def tag_state(db, url):
    """This article's topics with the other articles under each, plus every topic for suggestions."""
    _require_article(db, url)
    mine = db.execute(
        """SELECT t.id, t.name FROM topic_articles m
        JOIN topics t ON t.id=m.topic_id
        WHERE m.article_url=? ORDER BY t.name COLLATE NOCASE""",
        (url,),
    ).fetchall()
    tags = []
    mine_names = set()
    for row in mine:
        name = row["name"]
        mine_names.add(name.casefold())
        others = db.execute(
            """SELECT a.url,a.title,a.published_at,s.name AS publisher FROM topic_articles m
            JOIN articles a ON a.url=m.article_url JOIN sources s ON s.id=a.source_id AND s.enabled=1
            WHERE m.topic_id=? AND m.article_url<>? ORDER BY julianday(a.published_at) DESC,a.url""",
            (row["id"], url),
        ).fetchall()
        tags.append({"tag": name, "id": row["id"], "others": [dict(other) for other in others]})
    suggestions = [
        row[0]
        for row in db.execute(
            """SELECT t.name FROM topics t
            LEFT JOIN topic_articles m ON m.topic_id=t.id
            LEFT JOIN articles a ON a.url=m.article_url
            LEFT JOIN sources s ON s.id=a.source_id AND s.enabled=1
            GROUP BY t.id ORDER BY count(s.id) DESC, t.name COLLATE NOCASE"""
        )
    ]
    return {"tags": tags, "suggestions": [name for name in suggestions if name.casefold() not in mine_names]}
