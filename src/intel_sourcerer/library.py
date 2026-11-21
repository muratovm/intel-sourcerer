"""Searchable local article and notes library."""

import json
from pathlib import Path
from datetime import datetime

from .content import format_label, render_markdown
from .templates import load_template, render_template
from html import escape, unescape
import re
from string import Template
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from markdownify import markdownify
from .sessions import has_notes

_TAG_RE = re.compile(r"<[^>]+>")


def _fast_plain(text, fmt):
    if not text:
        return ""
    if fmt == "html":
        return " ".join(unescape(_TAG_RE.sub(" ", text)).split())
    return text


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
    transcribed = bool(row["has_transcript"]) if "has_transcript" in row and row.get("content_markdown") is None else has_transcript(row.get("content_markdown"))
    words = reading_word_count(row.get("content_markdown"), row.get("content_text") or "", row.get("content_html")) if not media or transcribed else 0
    mins = max(1, -(-words // 200)) if words else 0
    read_dur = f"{mins // 60}h {mins % 60}m" if mins >= 60 and mins % 60 else f"{mins // 60}h" if mins >= 60 else f"{mins} min"
    read = f"~{read_dur} read · {words:,} words" if words else ""
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
    facts.append(("Status", "Read" if row.get("is_read") else _progress_label(row) or "Unread"))
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
    topics = [
        item["name"]
        for item in db.execute(
            """SELECT tp.name FROM topic_articles ta JOIN topics tp ON tp.id=ta.topic_id
            WHERE ta.article_url=? ORDER BY tp.name COLLATE NOCASE""",
            (url,),
        )
    ]
    topic_line = f"Topics: {', '.join(topics)}\n\n" if topics else ""
    return f"# {row['title']}\n\nSource: {url}\n\nPublished: {row['published_at']}\n\n{topic_line}## Notes\n\n{notes}\n\n{highlights}{indicators}## Article\n\n{row['content_markdown'] or row['content_text'] or ''}\n"


def _in_progress(row):
    from .reading_state import is_in_progress

    return is_in_progress(
        row.get("is_read"),
        row.get("position_fraction"),
        has_notes(row["notes"], row["notes_format"]),
        json.loads(row.get("highlights_json") or "[]"),
    )


def _progress_label(row):
    """"In progress · 35%", or None when the article hasn't been started or is already read."""
    if not _in_progress(row):
        return None
    fraction = row.get("position_fraction") or 0
    return f"In progress · {round(fraction * 100)}%" if fraction else "In progress"


def _last_touched(row):
    return max(row.get("position_updated_at") or "", row.get("notes_updated_at") or "")


def _row_tags(names):
    """Enough to recognize a row by, without every tag competing with the title."""
    shown = "".join(f'<span class="lib-row-tag">{escape(name)}</span>' for name in names[:2])
    more = f"<span>+{len(names) - 2}</span>" if len(names) > 2 else ""
    return f'<span class="lib-row-tags">{shown}{more}</span>' if names else '<span class="lib-row-tags" hidden></span>'


DEFAULT_PAGE_SIZE = 100


def fts_match_urls(db, query):
    """Use the SQLite FTS5 trigram index to narrow candidate URLs in O(log N) for terms of 3+ chars."""
    import sqlite3

    terms = [term for term in (query or "").split() if len(term) >= 3]
    if not terms:
        return None
    match_expr = " ".join('"' + term.replace('"', '""') + '"' for term in terms)
    try:
        return {
            row[0]
            for row in db.execute(
                "SELECT url FROM articles_fts WHERE articles_fts MATCH ?",
                (match_expr,),
            )
        }
    except sqlite3.OperationalError:
        return None


def render_library(db, query="", notes_only=False, selected="", csrf_token="", tag="", storage=None, kind="", progress=False, page=1, page_size=DEFAULT_PAGE_SIZE, unread_only=False, source="", topic="", days=""):
    rows = library_rows(db, selected=selected, include_body_text=bool(query))
    fts_urls = fts_match_urls(db, query) if query else None
    return render_library_rows(rows, query, notes_only, selected, csrf_token, tag, storage, kind, progress, page=page, page_size=page_size, fts_urls=fts_urls, unread_only=unread_only, source=source, topic=topic, days=days)


def _folder_bytes(folder):
    return sum(f.stat().st_size for f in Path(folder).rglob("*") if f.is_file()) if Path(folder).is_dir() else 0


def storage_breakdown(database):
    """Detailed byte breakdown for the Account page: DB + WAL/SHM, media/PDFs, and daily backups."""
    path = Path(database).resolve()
    db_bytes = sum(p.stat().st_size for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")) if p.is_file())
    media_bytes = sum(_folder_bytes(path.parent / name) for name in ("media", "pdf-assets", "uploads"))
    backups = sum(f.stat().st_size for f in (path.parent / "backups").glob(f"{path.stem}-*.sqlite3") if f.is_file())
    return {"stored": db_bytes + media_bytes, "db_bytes": db_bytes, "media_bytes": media_bytes, "backups": backups}


def storage_usage(database):
    """Bytes this library keeps on disk: the database with its media and assets, and its daily backups apart."""
    detail = storage_breakdown(database)
    return {"stored": detail["stored"], "backups": detail["backups"]}


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


def library_rows(db, selected="", include_body_text=False):
    """Read private library records at the backend boundary without serializing full HTML/Markdown for every row."""
    target = selected
    if not target:
        first = db.execute(
            "SELECT a.url FROM articles a JOIN sources s ON s.id=a.source_id ORDER BY julianday(a.published_at) DESC,a.title COLLATE NOCASE,a.url LIMIT 1"
        ).fetchone()
        target = first[0] if first else ""
    body_col = "a.content_text" if include_body_text else "CASE WHEN a.url=? THEN a.content_text ELSE '' END AS content_text"
    params = (target, target, target) if include_body_text else (target, target, target, target)
    return [
        dict(row)
        for row in db.execute(
            f"""SELECT a.url,a.source_id,a.title,a.published_at,a.author,{body_col},
            a.content_status,
            a.discovered_at,a.content_format,a.media_json,a.is_read,a.notes,a.notes_format,a.notes_updated_at,
            a.notes_revision,a.triage,
            CASE WHEN a.url=? THEN a.content_html END AS content_html,
            CASE WHEN a.url=? THEN a.content_markdown END AS content_markdown,
            CASE WHEN a.url=? THEN instr(coalesce(a.content_markdown,''), char(10)||char(10)||'## Transcript'||char(10))>0 ELSE 0 END AS has_transcript,
            s.name AS publisher,
            (SELECT json_group_array(h.quote) FROM (SELECT quote FROM article_highlights WHERE article_url=a.url ORDER BY created_at,id) h) AS highlights_json,
            (SELECT json_group_array(t.name) FROM (SELECT tp.name FROM topic_articles ta JOIN topics tp ON tp.id=ta.topic_id WHERE ta.article_url=a.url ORDER BY tp.name COLLATE NOCASE) t) AS tags_json,
            (SELECT json_group_array(json_object('id',p.id,'name',p.name)) FROM (SELECT tp.id,tp.name FROM topic_articles ta JOIN topics tp ON tp.id=ta.topic_id WHERE ta.article_url=a.url ORDER BY tp.name COLLATE NOCASE) p) AS topics_json,
            rp.fraction AS position_fraction,rp.updated_at AS position_updated_at
            FROM articles a JOIN sources s ON s.id=a.source_id LEFT JOIN reading_positions rp ON rp.article_url=a.url
            ORDER BY julianday(a.published_at) DESC,a.title COLLATE NOCASE,a.url""",
            params,
        )
    ]


def _kind(row):
    return format_label(row["content_format"], row["publisher"])


def _estimate_minutes(row):
    from .youtube import video_duration

    media = row.get("content_format") in {"audio", "video"} or (row.get("url") or "").startswith("https://www.youtube.com/watch?v=")
    if media:
        dur = video_duration(row.get("media_json"))
        if dur:
            return max(1, round(dur / 60))
    text_len = row.get("text_len")
    if text_len is None:
        text_len = len(row.get("content_text") or "")
    if text_len:
        return max(1, round(text_len / 1100))
    return 5


TIMEFRAME_OPTIONS = (
    ("7", "Last 7 days"),
    ("30", "Last 30 days"),
    ("90", "Last 90 days"),
    ("365", "Last year"),
)


def render_library_rows(rows, query="", notes_only=False, selected="", csrf_token="", tag="", storage=None, kind="", progress=False, page=1, page_size=DEFAULT_PAGE_SIZE, fts_urls=None, unread_only=False, source="", topic="", days=""):
    """Render a library from backend JSON without opening a database."""
    from datetime import timedelta, timezone
    from .sessions import clean_notes

    progress = progress and not notes_only
    unread_only = unread_only and not notes_only and not progress
    days_int = int(days) if str(days or "").isdigit() and int(days) > 0 else 0
    days_cutoff = (datetime.now(timezone.utc) - timedelta(days=days_int)).isoformat() if days_int else ""
    terms = query.casefold().split()
    matches = []
    counts = {}
    kinds = {}
    sources_counts = {}
    topics_counts = {}
    topics_names = {}
    parsed_tags = {}
    parsed_topics = {}
    parsed_quotes = {}
    parsed_notes = {}
    noted_urls = set()
    started = set()
    unread_total = 0
    from .reading_state import is_in_progress

    for row in rows:
        url = row["url"]
        kinds[_kind(row)] = kinds.get(_kind(row), 0) + 1
        pub = row["publisher"]
        sources_counts[pub] = sources_counts.get(pub, 0) + 1
        if not row.get("is_read"):
            unread_total += 1
        row_topics = [t for t in json.loads(row.get("topics_json") or "[]") if isinstance(t, dict) and t.get("id")]
        tags = [t["name"] for t in row_topics] if row_topics else json.loads(row.get("tags_json") or "[]")
        quotes = json.loads(row.get("highlights_json") or "[]")
        notes_plain = _fast_plain(row["notes"], row["notes_format"])
        noted = bool(notes_plain.replace("\u200b", "").strip())
        parsed_tags[url] = tags
        parsed_topics[url] = row_topics
        parsed_quotes[url] = quotes
        parsed_notes[url] = notes_plain
        for tp in row_topics:
            tid = tp["id"]
            topics_names[tid] = tp["name"]
            topics_counts[tid] = topics_counts.get(tid, 0) + 1
        if noted:
            noted_urls.add(url)
        if is_in_progress(row.get("is_read"), row.get("position_fraction"), noted, quotes):
            started.add(url)
        for name in tags:
            counts[name] = counts.get(name, 0) + 1
    for row in rows:
        url = row["url"]
        if fts_urls is not None and url not in fts_urls:
            continue
        tags = parsed_tags[url]
        if tag and tag.casefold() not in {name.casefold() for name in tags}:
            continue
        if kind and _kind(row).casefold() != kind.casefold():
            continue
        if source and row["publisher"].casefold() != source.casefold():
            continue
        if topic and topic not in {tp["id"] for tp in parsed_topics[url]} and topic.casefold() not in {name.casefold() for name in tags}:
            continue
        if days_cutoff and (row.get("published_at") or "") < days_cutoff:
            continue
        if unread_only and row.get("is_read"):
            continue
        notes = parsed_notes[url]
        quotes = parsed_quotes[url]
        if notes_only and url not in noted_urls and not quotes:
            continue
        if progress and url not in started:
            continue
        if all(
            term
            in " ".join(
                [row["title"], row["publisher"], row["content_text"] or "", notes, *quotes, *tags]
            ).casefold()
            for term in terms
        ):
            matches.append(row)
    if progress:
        # Most recently read or noted first, so the article you just left is on top.
        matches.sort(key=_last_touched, reverse=True)
    total_matches = len(matches)
    per_page = max(1, int(page_size)) if page_size else total_matches or 1
    total_pages = max(1, (total_matches + per_page - 1) // per_page)
    try:
        current_page = max(1, min(int(page), total_pages))
    except (TypeError, ValueError):
        current_page = 1
    visible = matches[(current_page - 1) * per_page : current_page * per_page]
    active = next(
        (r for r in matches if r["url"] == selected), visible[0] if visible else None
    )

    def link(**kwargs):
        base = dict(q=query, notes="1" if notes_only else "0", progress="1" if progress else "0", tag=tag, type=kind)
        if unread_only:
            base["unread"] = "1"
        if source:
            base["source"] = source
        if topic:
            base["topic"] = topic
        if days_int:
            base["days"] = str(days_int)
        if current_page > 1 and "page" not in kwargs:
            base["page"] = str(current_page)
        params = dict(base, **kwargs)
        for opt_key in ("unread", "source", "topic", "days"):
            if params.get(opt_key) in {"", "0", 0, None}:
                params.pop(opt_key, None)
        if params.get("page") in {"1", 1, "", None}:
            params.pop("page", None)
        return escape("/library?" + urlencode(params), quote=True)

    row_template = Template(load_template("library/article-row.html"))
    read_badge_html = load_template("library/read-badge.html")
    noted_badge_html = load_template("library/noted-badge.html")
    items = []
    for row in visible:
        is_active = active["url"] == row["url"]
        fraction = row.get("position_fraction") or 0
        badges = (
            read_badge_html
            if row["is_read"]
            else f'<span class="lib-progress">{round(fraction * 100)}% read</span>'
            if row["url"] in started and fraction >= 0.02
            else '<span class="lib-progress">Started</span>'
            if row["url"] in started
            else ""
        ) + (
            noted_badge_html
            if row["url"] in noted_urls
            else ""
        )
        items.append(
            row_template.substitute(
                selected_class="selected" if is_active else "",
                url=escape(row["url"], quote=True),
                selection_url=link(selected=row["url"]),
                current_attribute="aria-current=true" if is_active else "",
                publisher=escape(row["publisher"]),
                title=escape(row["title"]),
                minutes=str(_estimate_minutes(row)),
                format_label=(label := format_label(row["content_format"], row["publisher"])),
                format_class=label.lower(),
                published_date=escape(short_date(row["published_at"])),
                badges=badges,
                tags=_row_tags(parsed_tags[row["url"]]),
            )
        )
    detail = render_template("library/empty.html")
    if active:
        notes = (
            clean_notes(active["notes"])
            if active["notes_format"] == "html"
            else "<p>" + escape(active["notes"]).replace("\n", "<br>") + "</p>"
        )
        quotes = parsed_quotes[active["url"]]
        if active["url"] not in noted_urls:
            notes = "" if quotes else render_template("library/no-notes.html")
        if quotes:
            notes += '<h3 class="lib-highlights-heading">Highlights</h3>' + "".join(
                f'<blockquote class="lib-highlight">{escape(quote)}</blockquote>' for quote in quotes
            )
        opened = escape("/?" + urlencode({"open": active["url"]}), quote=True)
        exported = escape("/export?" + urlencode({"url": active["url"]}), quote=True)
        exported_csv = escape("/export?" + urlencode({"url": active["url"], "format": "csv"}), quote=True)
        exported_stix = escape("/export?" + urlencode({"url": active["url"], "format": "stix"}), quote=True)
        exported_nav = escape("/export?" + urlencode({"url": active["url"], "format": "navigator"}), quote=True)
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
            minutes=str(_estimate_minutes(active)),
            format_label=(active_label := format_label(active["content_format"], active["publisher"])),
            format_class=active_label.lower(),
            published_date=escape(short_date(active["published_at"])),
            opened=opened,
            exported=exported,
            exported_csv=exported_csv,
            exported_stix=exported_stix,
            exported_nav=exported_nav,
            url=escape(active["url"], quote=True),
            notes=notes,
            details=resource_details(active),
        )
    all_link = link(notes="0", progress="0", unread="0", page="1")
    unread_link = link(notes="0", progress="0", unread="1", page="1")
    notes_link = link(notes="1", progress="0", unread="0", page="1")
    progress_link = link(notes="0", progress="1", unread="0", page="1")
    pagination = ""
    if total_pages > 1:
        prev_link = f'<a href="{link(page=str(current_page - 1))}">← Previous</a>' if current_page > 1 else '<span class="muted">← Previous</span>'
        next_link = f'<a href="{link(page=str(current_page + 1))}">Next →</a>' if current_page < total_pages else '<span class="muted">Next →</span>'
        pagination = f'<nav class="lib-pagination" aria-label="Library pages">{prev_link}<span class="muted">Page {current_page} of {total_pages}</span>{next_link}</nav>'
    # Unified Topics filter (replaces the separate Tags dropdown). Legacy ?tag= links stay supported.
    tag_filter = ""
    # The same menu shape for Type; picking the active type again shows every type.
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
            f'<details class="lib-filter-menu lib-type-menu"><summary>{summary}</summary>'
            f'<div class="lib-tag-list">{kind_links}</div></details>{clear}'
        )
    source_links = "".join(
        f'<a class="lib-tag-option{" active" if name.casefold() == source.casefold() else ""}" '
        f'{"aria-current=true " if name.casefold() == source.casefold() else ""}'
        f'href="{link(source="" if name.casefold() == source.casefold() else name, page="1")}">{escape(name)} <span>{count}</span></a>'
        for name, count in sorted(sources_counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    )
    source_filter = ""
    if len(sources_counts) > 1 or source:
        summary = f"Source: {escape(source)}" if source else "Source"
        clear = f'<a class="lib-tag-clear" aria-label="Clear source filter" href="{link(source="", page="1")}">×</a>' if source else ""
        source_filter = (
            f'<details class="lib-filter-menu"><summary>{summary}</summary>'
            f'<div class="lib-tag-list">{source_links}</div></details>{clear}'
        )
    topic_links = "".join(
        f'<a class="lib-tag-option{" active" if tid == topic or (tag and topics_names[tid].casefold() == tag.casefold()) else ""}" '
        f'{"aria-current=true " if tid == topic or (tag and topics_names[tid].casefold() == tag.casefold()) else ""}'
        f'href="{link(topic="" if tid == topic else tid, tag="", page="1")}">{escape(topics_names[tid])} <span>{count}</span></a>'
        for tid, count in sorted(topics_counts.items(), key=lambda item: (-item[1], topics_names[item[0]].casefold()))
    )
    topic_filter = ""
    if topic_links or topic or tag:
        topic_label = topics_names.get(topic, topic) if topic else tag
        summary = f"Topic: {escape(topic_label)}" if topic_label else "Topic"
        clear = f'<a class="lib-tag-clear" aria-label="Clear topic filter" href="{link(topic="", tag="", page="1")}">×</a>' if topic_label else ""
        topic_filter = (
            f'<details class="lib-filter-menu"><summary>{summary}</summary>'
            f'<div class="lib-tag-list">{topic_links}</div></details>{clear}'
        )
    days_labels = dict(TIMEFRAME_OPTIONS)
    days_links = "".join(
        f'<a class="lib-tag-option{" active" if str(days_int) == value else ""}" '
        f'{"aria-current=true " if str(days_int) == value else ""}'
        f'href="{link(days="" if str(days_int) == value else value, page="1")}">{label}</a>'
        for value, label in TIMEFRAME_OPTIONS
    )
    days_summary = days_labels.get(str(days_int), f"Last {days_int} days") if days_int else "Timeframe"
    days_clear = f'<a class="lib-tag-clear" aria-label="Clear timeframe filter" href="{link(days="", page="1")}">×</a>' if days_int else ""
    days_filter = (
        f'<details class="lib-filter-menu"><summary>{escape(days_summary)}</summary>'
        f'<div class="lib-tag-list">{days_links}</div></details>{days_clear}'
    )
    active_filter_chips = []
    if source:
        active_filter_chips.append(
            f'<span class="lib-active-chip">Source: {escape(source)} <a aria-label="Clear source filter" href="{link(source="", page="1")}">×</a></span>'
        )
    if topic:
        topic_label = topics_names.get(topic, topic)
        active_filter_chips.append(
            f'<span class="lib-active-chip">Topic: {escape(topic_label)} <a aria-label="Clear topic filter" href="{link(topic="", page="1")}">×</a></span>'
        )
    elif tag:
        active_filter_chips.append(
            f'<span class="lib-active-chip">Topic: {escape(tag)} <a aria-label="Clear topic filter" href="{link(tag="", page="1")}">×</a></span>'
        )
    if days_int:
        active_filter_chips.append(
            f'<span class="lib-active-chip">{escape(days_summary)} <a aria-label="Clear timeframe filter" href="{link(days="", page="1")}">×</a></span>'
        )
    if kind:
        active_filter_chips.append(
            f'<span class="lib-active-chip">Type: {escape(kind)} <a aria-label="Clear type filter" href="{link(type="")}">×</a></span>'
        )
    active_filter_count = len(active_filter_chips)
    filters_label = f"Filters · {active_filter_count}" if active_filter_count else "Filters"
    filters_active_class = " has-active" if active_filter_count else ""
    active_chips_html = (
        '<div class="lib-active-chips">' + "".join(active_filter_chips) + "</div>"
        if active_filter_chips
        else ""
    )
    match_count_label = f"Showing {total_matches}" if total_matches == len(rows) else f"Showing {total_matches} of {len(rows)}"
    return render_template(
        "library/index.html",
        csrf_token=escape(csrf_token, quote=True),
        query=escape(query, quote=True),
        notes_filter="1" if notes_only else "0",
        unread_filter="1" if unread_only else "0",
        tag=escape(tag, quote=True),
        tag_filter=tag_filter,
        kind=escape(kind, quote=True),
        kind_filter=kind_filter,
        source=escape(source, quote=True),
        source_filter=source_filter,
        topic=escape(topic, quote=True),
        topic_filter=topic_filter,
        days=str(days_int) if days_int else "",
        days_filter=days_filter,
        filters_label=escape(filters_label),
        filters_active_class=filters_active_class,
        active_chips=active_chips_html,
        progress_filter="1" if progress else "0",
        all_tab_class="active" if not notes_only and not progress and not unread_only else "",
        all_link=all_link,
        unread_tab_class="active" if unread_only else "",
        unread_link=unread_link,
        unread_count=unread_total,
        notes_tab_class="active" if notes_only else "",
        notes_link=notes_link,
        progress_tab_class="active" if progress else "",
        progress_link=progress_link,
        progress_count=len(started),
        match_count_label=escape(match_count_label),
        library_summary=escape(library_summary(len(rows), storage)),
        article_rows="".join(items)
        or (
            '<p class="muted">Nothing in progress. Articles you start reading, note or highlight show up here until you mark them read.</p>'
            if progress and not terms and not tag and not kind and not source and not topic and not days_int
            else '<p class="muted">No results</p>'
        ),
        pagination=pagination,
        detail=detail,
    )
