"""Searchable local article and notes library."""

import json
from datetime import datetime

from .content import format_label, render_markdown
from .templates import render_template
from html import escape
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from markdownify import markdownify
from .sessions import has_notes


def short_date(value):
    try:
        date = datetime.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return value or ""
    return f"{date:%b} {date.day}, {date.year}"


def _checklist_text(html):
    """Markdown has no tick boxes of its own, so items export in the usual [ ] and [x] form."""
    soup = BeautifulSoup(html, "html.parser")
    for item in soup.select("ul.checklist > li"):
        item.insert(0, "[x] " if item.get("data-checked") == "true" else "[ ] ")
    return str(soup)


def export_article(db, url):
    row = db.execute("SELECT * FROM articles WHERE url=?", (url,)).fetchone()
    if row is None:
        return None
    notes = markdownify(_checklist_text(row["notes"])) if row["notes_format"] == "html" else row["notes"]
    quotes = [item["quote"] for item in db.execute("SELECT quote FROM article_highlights WHERE article_url=? ORDER BY created_at,id", (url,))]
    highlights = "## Highlights\n\n" + "\n\n".join("> " + quote.replace("\n", "\n> ") for quote in quotes) + "\n\n" if quotes else ""
    from .iocs import extract_iocs, iocs_markdown

    indicators = iocs_markdown(extract_iocs(row["content_html"] or render_markdown(row["content_markdown"] or "")))
    tags = [item["tag"] for item in db.execute("SELECT tag FROM article_tags WHERE article_url=? ORDER BY tag COLLATE NOCASE", (url,))]
    tag_line = f"Tags: {', '.join(tags)}\n\n" if tags else ""
    return f"# {row['title']}\n\nSource: {url}\n\nPublished: {row['published_at']}\n\n{tag_line}## Notes\n\n{notes}\n\n{highlights}{indicators}## Article\n\n{row['content_markdown'] or row['content_text'] or ''}\n"


def render_library(db, query="", notes_only=False, selected="", csrf_token="", tag=""):
    rows = library_rows(db)
    return render_library_rows(rows, query, notes_only, selected, csrf_token, tag)


def library_rows(db):
    """Read private library records at the backend boundary."""
    return [
        dict(row)
        for row in db.execute(
            "SELECT a.*,s.name AS publisher,(SELECT json_group_array(h.quote) FROM (SELECT quote FROM article_highlights WHERE article_url=a.url ORDER BY created_at,id) h) AS highlights_json,(SELECT json_group_array(t.tag) FROM (SELECT tag FROM article_tags WHERE article_url=a.url ORDER BY tag COLLATE NOCASE) t) AS tags_json FROM articles a JOIN sources s ON s.id=a.source_id ORDER BY julianday(a.published_at) DESC,a.title COLLATE NOCASE,a.url"
        )
    ]


def render_library_rows(rows, query="", notes_only=False, selected="", csrf_token="", tag=""):
    """Render a library from backend JSON without opening a database."""
    from .sessions import clean_notes

    terms = query.casefold().split()
    matches = []
    counts = {}
    for row in rows:
        for name in json.loads(row.get("tags_json") or "[]"):
            counts[name] = counts.get(name, 0) + 1
    for row in rows:
        tags = json.loads(row.get("tags_json") or "[]")
        if tag and tag.casefold() not in {name.casefold() for name in tags}:
            continue
        notes = (
            BeautifulSoup(row["notes"], "html.parser").get_text(" ", strip=True)
            if row["notes_format"] == "html"
            else row["notes"]
        )
        quotes = json.loads(row.get("highlights_json") or "[]")
        if notes_only and not has_notes(row["notes"], row["notes_format"]) and not quotes:
            continue
        if all(
            term
            in " ".join(
                [row["title"], row["publisher"], row["content_text"] or "", notes, *quotes, *tags]
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
            + urlencode(dict(dict(q=query, notes="1" if notes_only else "0", tag=tag), **kwargs)),
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
                format_label=(label := format_label(row["content_format"], row["publisher"])),
                format_class=label.lower(),
                published_date=escape(short_date(row["published_at"])),
                badges=badges,
                tags="".join(f'<span class="lib-tag">{escape(name)}</span>' for name in json.loads(row.get("tags_json") or "[]")),
            )
        )
    detail = render_template("library/empty.html")
    if active:
        notes = (
            clean_notes(active["notes"])
            if active["notes_format"] == "html"
            else "<p>" + escape(active["notes"]).replace("\n", "<br>") + "</p>"
        )
        quotes = json.loads(active.get("highlights_json") or "[]")
        if not has_notes(active["notes"], active["notes_format"]):
            notes = "" if quotes else render_template("library/no-notes.html")
        if quotes:
            notes += '<h3 class="lib-highlights-heading">Highlights</h3>' + "".join(
                f'<blockquote class="lib-highlight">{escape(quote)}</blockquote>' for quote in quotes
            )
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
            format_label=(active_label := format_label(active["content_format"], active["publisher"])),
            format_class=active_label.lower(),
            published_date=escape(short_date(active["published_at"])),
            opened=opened,
            exported=exported,
            url=escape(active["url"], quote=True),
            notes=notes,
            tags="".join(f'<a class="lib-tag" href="{link(tag=name, selected="")}">{escape(name)}</a>' for name in json.loads(active.get("tags_json") or "[]")),
        )
    all_link = link(notes="0")
    notes_link = link(notes="1")
    # Selecting the active tag again clears the filter.
    tag_links = "".join(
        f'<a class="lib-tag{" active" if name.casefold() == tag.casefold() else ""}" '
        f'{"aria-current=true " if name.casefold() == tag.casefold() else ""}'
        f'href="{link(tag="" if name.casefold() == tag.casefold() else name)}">{escape(name)} <span>{count}</span></a>'
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    )
    return render_template(
        "library/index.html",
        csrf_token=escape(csrf_token, quote=True),
        query=escape(query, quote=True),
        notes_filter="1" if notes_only else "0",
        tag=escape(tag, quote=True),
        tag_filter=f'<nav class="lib-tag-filter" aria-label="Filter by tag">{tag_links}</nav>' if tag_links else "",
        all_tab_class="active" if not notes_only else "",
        all_link=all_link,
        notes_tab_class="active" if notes_only else "",
        notes_link=notes_link,
        article_count=len(matches),
        article_rows="".join(items) or '<p class="muted">No results</p>',
        detail=detail,
    )
