"""Searchable local article and notes library."""

from .templates import render_template
from html import escape
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from markdownify import markdownify
from .sessions import has_notes


def export_article(db, url):
    row = db.execute("SELECT * FROM articles WHERE url=?", (url,)).fetchone()
    if row is None:
        return None
    notes = markdownify(row["notes"]) if row["notes_format"] == "html" else row["notes"]
    return f"# {row['title']}\n\nSource: {url}\n\nPublished: {row['published_at']}\n\n## Notes\n\n{notes}\n\n## Article\n\n{row['content_markdown'] or row['content_text'] or ''}\n"


def render_library(db, query="", notes_only=False, selected="", csrf_token=""):
    rows = library_rows(db)
    return render_library_rows(rows, query, notes_only, selected, csrf_token)


def library_rows(db):
    """Read private library records at the backend boundary."""
    return [
        dict(row)
        for row in db.execute(
            "SELECT a.*,s.name AS publisher FROM articles a JOIN sources s ON s.id=a.source_id ORDER BY julianday(a.published_at) DESC,a.title COLLATE NOCASE,a.url"
        )
    ]


def render_library_rows(rows, query="", notes_only=False, selected="", csrf_token=""):
    """Render a library from backend JSON without opening a database."""
    from .sessions import clean_notes

    terms = query.casefold().split()
    matches = []
    for row in rows:
        notes = (
            BeautifulSoup(row["notes"], "html.parser").get_text(" ", strip=True)
            if row["notes_format"] == "html"
            else row["notes"]
        )
        if notes_only and not has_notes(row["notes"], row["notes_format"]):
            continue
        if all(
            term
            in " ".join(
                [row["title"], row["publisher"], row["content_text"] or "", notes]
            ).casefold()
            for term in terms
        ):
            matches.append(row)
    active = next(
        (r for r in matches if r["url"] == selected), matches[0] if matches else None
    )

    def link(**kwargs):
        return escape(
            "/library?"
            + urlencode(dict(q=query, notes="1" if notes_only else "0", **kwargs)),
            quote=True,
        )

    items = []
    for row in matches:
        is_active = active["url"] == row["url"]
        badges = (
            render_template("library/read-badge.html")
            if row["is_read"]
            else render_template("library/unread-badge.html")
        ) + (
            render_template("library/noted-badge.html")
            if has_notes(row["notes"], row["notes_format"])
            else ""
        )
        items.append(
            render_template(
                "library/article-row.html",
                selected_class="selected" if is_active else "",
                selection_url=link(selected=row["url"]),
                current_attribute="aria-current=true" if is_active else "",
                publisher=escape(row["publisher"]),
                title=escape(row["title"]),
                published_date=escape(
                    row["published_at"][:10]
                    + " · "
                    + row["content_format"].capitalize()
                ),
                badges=badges,
            )
        )
    detail = render_template("library/empty.html")
    if active:
        notes = (
            clean_notes(active["notes"])
            if active["notes_format"] == "html"
            else "<p>" + escape(active["notes"]).replace("\n", "<br>") + "</p>"
        )
        if not has_notes(active["notes"], active["notes_format"]):
            notes = render_template("library/no-notes.html")
        opened = escape("/?" + urlencode({"open": active["url"]}), quote=True)
        exported = escape("/export?" + urlencode({"url": active["url"]}), quote=True)
        edited = (
            "Edited " + active["notes_updated_at"][:10]
            if active["notes_updated_at"]
            else ""
        )
        detail = render_template(
            "library/notes-preview.html",
            edited=edited,
            title=escape(active["title"]),
            publisher=escape(active["publisher"]),
            published_date=escape(active["published_at"][:10]),
            opened=opened,
            exported=exported,
            url=escape(active["url"], quote=True),
            notes=notes,
        )
    all_link = escape("/library?" + urlencode({"q": query}), quote=True)
    notes_link = escape("/library?" + urlencode({"q": query, "notes": "1"}), quote=True)
    return render_template(
        "library/index.html",
        csrf_token=escape(csrf_token, quote=True),
        query=escape(query, quote=True),
        notes_filter="1" if notes_only else "0",
        all_tab_class="active" if not notes_only else "",
        all_link=all_link,
        notes_tab_class="active" if notes_only else "",
        notes_link=notes_link,
        article_count=len(matches),
        article_rows="".join(items) or '<p class="muted">No results</p>',
        detail=detail,
    )
