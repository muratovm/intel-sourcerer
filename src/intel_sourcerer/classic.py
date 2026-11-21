"""Classic single-page expandable article view (/classic)."""

from contextlib import closing
from datetime import datetime
import hashlib
from html import escape
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from .content import render_article_html, render_markdown
from .http_common import display_timezone
from .storage import BUSY_TIMEOUT

STYLE = """
:root { color-scheme: light; font-family: system-ui, -apple-system, sans-serif; color: #1e293b; background: #f6f7f9; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 860px; margin: 56px auto; padding: 0 24px; }
h1 { font-size: 30px; letter-spacing: -.8px; margin: 0 0 8px; }
.intro { color: #64748b; margin: 0 0 28px; line-height: 1.6; }
.list { border: 1px solid #dde2e8; border-radius: 12px; background: white; overflow: hidden; }
details + details { border-top: 1px solid #e5e9ef; }
summary { cursor: pointer; padding: 22px 24px; list-style: none; display: flex; gap: 14px; align-items: flex-start; }
summary::-webkit-details-marker { display: none; }
summary::before { content: '+'; color: #64748b; font-size: 22px; width: 18px; flex-shrink: 0; line-height: 1.25; }
details[open] > summary::before { content: '−'; }
summary:hover { background: #f8fafc; }
summary:focus-visible { outline: 3px solid #2563eb; outline-offset: -3px; }
.headline { display: block; font-size: 17px; font-weight: 600; line-height: 1.5; }
.meta { display: block; color: #64748b; font-size: 13px; margin-top: 6px; }
.read-control { margin: 0 0 20px; }
.read-control button { cursor: pointer; background: #f8fafc; border: 1px solid #cbd5e1; padding: 8px 14px; border-radius: 6px; font: inherit; font-size: 14px; }
.read-control button:hover { background: #e2e8f0; }
.read-control button:focus-visible { outline: 3px solid #2563eb; }
.article { padding: 0 24px 28px 56px; }
a { color: #1d4ed8; text-underline-offset: 3px; }
.original { display: inline-block; margin: 0 0 20px; font-size: 14px; }
.content { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 16px; line-height: 1.8; }
.markdown { overflow-wrap: anywhere; font-size: 16px; line-height: 1.8; }
.markdown h1, .markdown h2, .markdown h3 { line-height: 1.35; margin: 1.6em 0 .7em; }
.markdown li { margin: .35em 0; }
.markdown blockquote { border-left: 3px solid #94a3b8; background: #f8fafc; margin: 20px 0; padding: 8px 18px; color: #475569; }
.markdown table { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; font-size: 14px; margin: 20px 0; }
.markdown th, .markdown td { border: 1px solid #dbe2ea; padding: 10px 14px; text-align: left; }
.markdown th { background: #f1f5f9; }
.markdown pre { overflow-x: auto; padding: 16px; background: #f1f5f9; border-radius: 6px; }
.empty { padding: 24px; color: #64748b; }
@media (max-width: 600px) { main { margin: 28px auto; padding: 0 14px; } summary { padding: 18px 16px; } .article { padding: 0 18px 24px; } h1 { font-size: 26px; } }
"""


def read_articles(database):
    """Open the existing database read-only; never create an empty replacement."""
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT)) as db, db:
        db.row_factory = sqlite3.Row
        return db.execute("""SELECT a.title,a.url,a.published_at,a.content_text,a.content_markdown,a.content_html,a.is_read,a.notes,a.notes_format,a.notes_updated_at,a.content_format,a.triage,s.name AS publisher
            FROM articles a JOIN sources s ON s.id=a.source_id
            WHERE s.enabled=1 ORDER BY a.published_at DESC,a.title""").fetchall()


def render_page(articles, csrf_token="", open_url=None):
    rows = []
    zone = display_timezone()
    for article in articles:
        try:
            date = (
                datetime.fromisoformat(article["published_at"])
                .astimezone(zone)
                .strftime("%b %d, %Y")
            )
        except (TypeError, ValueError):
            date = "Date unavailable"
        url = article["url"]
        parsed = urlsplit(url)
        link = (
            f'<a class="original" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Read original article ↗</a>'
            if parsed.scheme in {"http", "https"} and parsed.hostname
            else ""
        )
        text = (
            article["content_text"]
            or "The full article text has not been saved yet. Use the original article link to read it."
        )
        keys = article.keys()
        markdown = article["content_markdown"] if "content_markdown" in keys else None
        clean_html = article["content_html"] if "content_html" in keys else None
        body = (
            render_article_html(clean_html)
            if clean_html
            else render_markdown(markdown)
            if markdown
            else escape(text)
        )
        content_class = "markdown" if clean_html or markdown else "content"
        is_read = bool(article["is_read"]) if "is_read" in keys else False
        status = "Read" if is_read else "Unread"
        action = "Mark unread" if is_read else "Mark read"
        article_id = "article-" + hashlib.sha256(url.encode()).hexdigest()[:16]
        expanded = " open" if url == open_url else ""
        form = f'''<form class="read-control" method="post" action="/read-status">
        <input type="hidden" name="url" value="{escape(url, quote=True)}">
        <input type="hidden" name="is_read" value="{0 if is_read else 1}">
        <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
        <button type="submit">{action}</button></form>'''
        rows.append(f'''<details id="{article_id}"{expanded}><summary><span><span class="headline">{escape(article["title"])}</span>
        <span class="meta">{escape(article["publisher"])} · {date} · {status}</span></span></summary>
        <div class="article">{link}{form}<div class="{content_class}">{body}</div></div></details>''')
    listing = (
        "".join(rows)
        or '<p class="empty">No saved articles yet. Import articles to see them here.</p>'
    )
    count = len(articles)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1"><title>Intel Sourcerer</title><style>{STYLE}</style></head>
    <body><main><h1>Intel Sourcerer</h1><p class="intro">{count} saved article{"s" if count != 1 else ""} · Click a headline to read.</p>
    <div class="list">{listing}</div></main></body></html>"""
