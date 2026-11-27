"""Short, readable reader links: /read/<id>/<title-slug>.

The library keys articles by their source URL, so the id is derived from it and nothing is stored.
The slug is only for people reading the link; lookups ignore it, so a renamed article keeps its links.
"""
import hashlib
import re
import unicodedata

ID_LENGTH = 8
READ_PATH = re.compile(r"^/read/([0-9a-f]{%d})(?:/[^/]*)?/?$" % ID_LENGTH)
SLUG_WORDS = 8


def article_id(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:ID_LENGTH]


def slugify(title):
    text = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words[:SLUG_WORDS])


def article_href(url, title=None):
    slug = slugify(title)
    return f"/read/{article_id(url)}" + (f"/{slug}" if slug else "")


def read_path_id(path):
    """The id in a /read/ path, or None for any other path."""
    match = READ_PATH.match(path or "")
    return match.group(1) if match else None


def resolve_article(db, identifier):
    """(url, title) of the saved article with this id, or None. A full scan is fine at library sizes."""
    for row in db.execute("SELECT url, title FROM articles"):
        if article_id(row["url"]) == identifier:
            return row["url"], row["title"]
    return None
