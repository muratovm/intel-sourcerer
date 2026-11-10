"""Searchable local article and notes library."""

import json
from pathlib import Path
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


def format_length(seconds):
    minutes = max(1, round(seconds / 60))
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _host(url):
    from urllib.parse import urlsplit
    parts = urlsplit(url or "")
    return (parts.hostname or "").removeprefix("www.") if parts.scheme in {"http", "https"} else ""


def resource_details(row):
    """Facts about the selected item for the library's side panel: length, maker, transcript, topics, source."""
    from .content import reading_word_count
    from .transcripts import has_transcript
    from .youtube import video_duration

    media = row["content_format"] in {"audio", "video"} or row["url"].startswith("https://www.youtube.com/watch?v=")
    transcribed = has_transcript(row.get("content_markdown"))
    words = reading_word_count(row.get("content_markdown"), row.get("content_text") or "", row.get("content_html")) if not media or transcribed else 0
    read = f"~{max(1, -(-words // 200))} min read · {words:,} words" if words else ""
    facts = []
    if media:
        duration = video_duration(row.get("media_json"))
        facts.append(("Length", format_length(duration) if duration else "Unknown"))
        facts.append(("Transcript", read if transcribed else "Not transcribed yet"))
    elif read:
        facts.append(("Length", read))
    author = (row.get("author") or "").strip()
    if not author and media:
        # Videos saved before the channel reached the author column keep it on a "Channel:" line.
        import re
        found = re.search(r"^Channel: (.+)$", row.get("content_markdown") or "", re.M)
        author = found[1].strip() if found else ""
    if author and author.casefold() != (row.get("publisher") or "").casefold():
        facts.append(("Channel" if media else "Author", escape(author)))
    topics = [t for t in json.loads(row.get("topics_json") or "[]") if isinstance(t, dict) and t.get("id")]
    if topics:
        facts.append(("Topics", ", ".join(
            f'<a href="{escape("/topic?" + urlencode({"id": t["id"]}), quote=True)}">{escape(t["name"])}</a>' for t in topics)))
    facts.append(("Status", "Read" if row.get("is_read") else "Unread"))
    if row.get("discovered_at"):
        facts.append(("Added", escape(short_date(row["discovered_at"]))))
    host = _host(row["url"])
    if host:
        facts.append(("Source", f'<a href="{escape(row["url"], quote=True)}" target="_blank" rel="noopener noreferrer">{escape(host)} ↗</a>'))
    rows = "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in facts)
    return f'<dl class="lib-facts">{rows}</dl>'


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


def _row_tags(names):
    """Enough to recognize a row by, without every tag competing with the title."""
    shown = "".join(f'<span class="lib-row-tag">{escape(name)}</span>' for name in names[:2])
    more = f"<span>+{len(names) - 2}</span>" if len(names) > 2 else ""
    return f'<span class="lib-row-tags">{shown}{more}</span>' if names else '<span class="lib-row-tags" hidden></span>'


def render_library(db, query="", notes_only=False, selected="", csrf_token="", tag="", storage=None, kind=""):
    rows = library_rows(db)
    return render_library_rows(rows, query, notes_only, selected, csrf_token, tag, storage, kind)


def _folder_bytes(folder):
    return sum(f.stat().st_size for f in Path(folder).rglob("*") if f.is_file()) if Path(folder).is_dir() else 0


def storage_usage(database):
    """Bytes this library keeps on disk: the database with its media and assets, and its daily backups apart."""
    path = Path(database).resolve()
    stored = sum(p.stat().st_size for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")) if p.is_file())
    stored += sum(_folder_bytes(path.parent / name) for name in ("media", "pdf-assets", "uploads"))
    backups = sum(f.stat().st_size for f in (path.parent / "backups").glob(f"{path.stem}-*.sqlite3") if f.is_file())
    return {"stored": stored, "backups": backups}


def format_size(size):
    for unit, scale in (("GB", 1e9), ("MB", 1e6)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{max(size, 0) / 1e6:.2f} MB" if size else "0 MB"


def library_summary(total, storage):
    text = f"{total} {'article' if total == 1 else 'articles'}"
    if storage:
        text += f" · {format_size(storage['stored'])} stored"
        if storage.get("backups"):
            text += f" · {format_size(storage['backups'])} in backups"
    return text


def library_rows(db):
    """Read private library records at the backend boundary."""
    return [
        dict(row)
        for row in db.execute(
            "SELECT a.*,s.name AS publisher,(SELECT json_group_array(h.quote) FROM (SELECT quote FROM article_highlights WHERE article_url=a.url ORDER BY created_at,id) h) AS highlights_json,(SELECT json_group_array(t.tag) FROM (SELECT tag FROM article_tags WHERE article_url=a.url ORDER BY tag COLLATE NOCASE) t) AS tags_json,(SELECT json_group_array(json_object('id',p.id,'name',p.name)) FROM (SELECT tp.id,tp.name FROM topic_articles ta JOIN topics tp ON tp.id=ta.topic_id WHERE ta.article_url=a.url ORDER BY tp.name COLLATE NOCASE) p) AS topics_json FROM articles a JOIN sources s ON s.id=a.source_id ORDER BY julianday(a.published_at) DESC,a.title COLLATE NOCASE,a.url"
        )
    ]


def _kind(row):
    return format_label(row["content_format"], row["publisher"])


def render_library_rows(rows, query="", notes_only=False, selected="", csrf_token="", tag="", storage=None, kind=""):
    """Render a library from backend JSON without opening a database."""
    from .sessions import clean_notes

    terms = query.casefold().split()
    matches = []
    counts = {}
    kinds = {}
    for row in rows:
        kinds[_kind(row)] = kinds.get(_kind(row), 0) + 1
        for name in json.loads(row.get("tags_json") or "[]"):
            counts[name] = counts.get(name, 0) + 1
    for row in rows:
        tags = json.loads(row.get("tags_json") or "[]")
        if tag and tag.casefold() not in {name.casefold() for name in tags}:
            continue
        if kind and _kind(row).casefold() != kind.casefold():
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
            + urlencode(dict(dict(q=query, notes="1" if notes_only else "0", tag=tag, type=kind), **kwargs)),
            quote=True,
        )

    items = []
    for row in matches:
        is_active = active["url"] == row["url"]
        badges = (
            render_template("library/read-badge.html")
            if row["is_read"]
            else ""
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
                tags=_row_tags(json.loads(row.get("tags_json") or "[]")),
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
            details=resource_details(active),
        )
    all_link = link(notes="0")
    notes_link = link(notes="1")
    # Selecting the active tag again clears the filter.
    tag_links = "".join(
        f'<a class="lib-tag-option{" active" if name.casefold() == tag.casefold() else ""}" '
        f'{"aria-current=true " if name.casefold() == tag.casefold() else ""}'
        f'href="{link(tag="" if name.casefold() == tag.casefold() else name)}">{escape(name)} <span>{count}</span></a>'
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    )
    tag_filter = ""
    if tag_links or tag:
        summary = f"Tag: {escape(tag)}" if tag else "Tags"
        clear = f'<a class="lib-tag-clear" aria-label="Clear tag filter" href="{link(tag="")}">×</a>' if tag else ""
        tag_filter = (
            f'<details class="lib-tag-menu"><summary>{summary}</summary>'
            f'<div class="lib-tag-list">{tag_links}</div></details>{clear}'
        )
    # The same menu shape as tags; picking the active type again shows every type.
    kind_links = "".join(
        f'<a class="lib-tag-option{" active" if name.casefold() == kind.casefold() else ""}" '
        f'{"aria-current=true " if name.casefold() == kind.casefold() else ""}'
        f'href="{link(type="" if name.casefold() == kind.casefold() else name)}">{escape(name)} <span>{count}</span></a>'
        for name, count in sorted(kinds.items(), key=lambda item: (-item[1], item[0].casefold()))
    )
    kind_filter = ""
    if len(kinds) > 1 or kind:
        summary = f"Type: {escape(kind)}" if kind else "Type"
        clear = f'<a class="lib-tag-clear" aria-label="Clear type filter" href="{link(type="")}">×</a>' if kind else ""
        kind_filter = (
            f'<details class="lib-tag-menu lib-type-menu"><summary>{summary}</summary>'
            f'<div class="lib-tag-list">{kind_links}</div></details>{clear}'
        )
    return render_template(
        "library/index.html",
        csrf_token=escape(csrf_token, quote=True),
        query=escape(query, quote=True),
        notes_filter="1" if notes_only else "0",
        tag=escape(tag, quote=True),
        tag_filter=tag_filter,
        kind=escape(kind, quote=True),
        kind_filter=kind_filter,
        all_tab_class="active" if not notes_only else "",
        all_link=all_link,
        notes_tab_class="active" if notes_only else "",
        notes_link=notes_link,
        library_summary=escape(library_summary(len(rows), storage)),
        article_rows="".join(items) or '<p class="muted">No results</p>',
        detail=detail,
    )
