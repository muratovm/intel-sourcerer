"""Rebuild stored article bodies from their sources, leaving notes and read state alone."""
import argparse
import json
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from .net_safety import public_only
from bs4 import BeautifulSoup

from .content import extract_article, render_article_html, render_markdown
from .pdf_layout import extract_layout_article
from .storage import connect_database

REPARSED_FORMATS = ('article', 'newsletter', 'paper')
MAX_BYTES = 20_000_000
# A much shorter result usually means a bot check or a changed page, not a better parse.
MIN_KEPT_TEXT = 0.5


def fetch_public(url):
    from .discovery import public_url

    with public_only(httpx.Client(timeout=25, trust_env=False, headers={'User-Agent': 'IntelSourcerer/0.1'})) as client:
        for _ in range(6):
            url = public_url(url)
            response = client.get(url)
            if response.is_redirect:
                url = urljoin(url, response.headers.get('location', ''))
                continue
            response.raise_for_status()
            if len(response.content) > MAX_BYTES:
                raise ValueError('larger than 20 MB')
            return response.content, response.headers.get('content-type', '').lower(), url
    raise ValueError('too many redirects')


def rebuild(url, assets):
    """Return (clean HTML, Markdown, media) for one stored article URL."""
    parsed = urlsplit(url)
    if parsed.scheme == 'file':
        path = Path(unquote(parsed.path))
        if path.suffix.lower() != '.pdf' or not path.is_file():
            raise ValueError('local PDF is no longer available')
        return extract_layout_article(path.read_bytes(), url, assets)
    from .arxiv import preferred_article

    preferred = preferred_article(url)
    if preferred:
        return extract_article(preferred['html'], preferred['content_url'])
    payload, content_type, final_url = fetch_public(url)
    if 'application/pdf' in content_type or payload[:1024].lstrip().startswith(b'%PDF-'):
        return extract_layout_article(payload, url, assets)
    if 'html' not in content_type:
        raise ValueError(f'unexpected content type {content_type}')
    return extract_article(payload.decode('utf-8', errors='replace'), final_url)


def visible_length(html):
    return len(''.join(BeautifulSoup(html, 'html.parser').get_text().split()))


def reparse(database, *, include_done=False, limit=None, url_contains=None, rebuilder=rebuild):
    assets = Path(database).resolve().parent / 'pdf-assets'
    db = connect_database(database)
    report = {'updated': [], 'shrunk': [], 'failed': []}
    try:
        marks = ','.join('?' * len(REPARSED_FORMATS))
        query = f'SELECT url,content_markdown,content_html FROM articles WHERE content_format IN ({marks})'
        if not include_done:
            query += ' AND content_html IS NULL'
        rows = db.execute(query, REPARSED_FORMATS).fetchall()
        if url_contains:
            rows = [row for row in rows if url_contains in row['url']]
        for row in rows[:limit]:
            url = row['url']
            try:
                clean_html, markdown, media = rebuilder(url, assets)
            except (httpx.HTTPError, OSError, ValueError) as error:
                report['failed'].append({'url': url, 'error': str(error)[:200]})
                continue
            previous = render_article_html(row['content_html']) if row['content_html'] else render_markdown(row['content_markdown'] or '')
            if visible_length(clean_html) < visible_length(previous) * MIN_KEPT_TEXT:
                report['shrunk'].append({'url': url, 'before': visible_length(previous), 'after': visible_length(clean_html)})
                continue
            db.execute('UPDATE articles SET content_html=?,content_markdown=?,media_json=? WHERE url=?', (clean_html, markdown, json.dumps(media), url))
            db.commit()
            report['updated'].append(url)
    finally:
        db.close()
    return report


def reclean(database, *, dry_run=False):
    """Apply the current furniture rules to stored articles without fetching anything."""
    from .boilerplate import strip_boilerplate, title_without_category

    db = connect_database(database)
    report = {'changed': 0, 'unchanged': 0, 'characters_removed': 0, 'titles': []}
    try:
        for row in db.execute('SELECT url,title,content_html FROM articles WHERE content_html IS NOT NULL').fetchall():
            title = title_without_category(row['title'], row['content_html'])
            if title == row['title']:
                continue
            report['titles'].append({'from': row['title'], 'to': title})
            if not dry_run:
                db.execute('UPDATE articles SET title=? WHERE url=?', (title, row['url']))
                db.execute('UPDATE timed_reading_sessions SET title=? WHERE article_url=? AND title=?', (title, row['url'], row['title']))
        for row in db.execute('SELECT url,content_html FROM articles WHERE content_html IS NOT NULL').fetchall():
            soup = BeautifulSoup(row['content_html'], 'html.parser')
            before = visible_length(row['content_html'])
            if not strip_boilerplate(soup):
                report['unchanged'] += 1
                continue
            cleaned = str(soup)
            report['changed'] += 1
            report['characters_removed'] += before - visible_length(cleaned)
            if not dry_run:
                db.execute('UPDATE articles SET content_html=? WHERE url=?', (cleaned, row['url']))
        db.commit()
    finally:
        db.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reclean', action='store_true', help='strip site furniture from stored HTML, without fetching')
    parser.add_argument('--dry-run', action='store_true', help='with --reclean, report without saving')
    parser.add_argument('--database', default='data/intel_sourcerer.sqlite3')
    parser.add_argument('--all', action='store_true', help='also rebuild articles that already have clean HTML')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--url-contains', help='only rebuild articles whose URL contains this text')
    args = parser.parse_args()
    if args.reclean:
        print(json.dumps(reclean(args.database, dry_run=args.dry_run), indent=2))
        return
    print(json.dumps(reparse(args.database, include_done=args.all, limit=args.limit, url_contains=args.url_contains), indent=2))


if __name__ == "__main__":
    main()
