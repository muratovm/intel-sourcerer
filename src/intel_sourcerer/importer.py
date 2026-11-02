"""Import dated cybersecurity articles from enabled AI-lab publishers."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import httpx
import trafilatura

from .content import extract_article
from .config import load_sources
from .feeds import Article, fetch_feed
from .storage import connect_database, save_article, save_source

TERMS = re.compile(r"cyber|distillation|malicious|misuse|espionage|phishing|ransomware|vulnerabilit|prompt injection|hacking|frontier safeguards", re.I)
# Everything these teams publish is in scope, so no keyword has to match.
ALWAYS_RELEVANT_SOURCES = {'anthropic-frontier-red-team'}


def in_window(article, since, until):
    return article.published_at is not None and since <= article.published_at <= until


def selection_reason(article, source):
    if source.id in ALWAYS_RELEVANT_SOURCES:
        return f'Published by {source.name}, a dedicated security research team'
    return relevance_reason(article)


def relevance_reason(article):
    # Exclude broad launch/event posts whose security mention is incidental.
    if re.search(r"^(Introducing |GPT-\d.*: A new generation)|DevDay.*Recap", article.title, re.I):
        return None
    text = article.title + ' ' + BeautifulSoup(article.summary_html, 'html.parser').get_text(' ', strip=True)
    match = TERMS.search(text)
    return f"Cybersecurity rule match: {match.group(0)}; publisher title/summary" if match else None


def listing_date(element):
    raw_date = element.get('datetime') or element.get_text(' ', strip=True)
    try:
        published = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
    except ValueError:
        for fmt in ('%b %d, %Y', '%B %d, %Y'):
            try:
                published = datetime.strptime(raw_date, fmt)
                break
            except ValueError:
                pass
        else:
            return None
    return published if published.tzinfo else published.replace(tzinfo=ZoneInfo('America/Toronto'))


def heading_links(container):
    """Links that carry a card's title; category and image links carry no heading."""
    found = {}
    for heading in container.find_all(re.compile('^h[1-6]$')):
        link = heading.find_parent('a', href=True) or heading.find('a', href=True)
        if link and link['href'] not in found:
            found[link['href']] = (link, heading)
    return found


def listing_card(date):
    """Find the title link for a date; the date may sit inside the link or beside it."""
    wrapping = date.find_parent('a', href=True)
    if wrapping:
        return wrapping, wrapping.find(re.compile('^h[1-6]$')), wrapping
    container = date
    for _ in range(6):
        container = container.parent
        if container is None:
            return None
        links = heading_links(container)
        if links:
            # Several titles means this is a list of cards, not this date's card.
            if len(links) > 1:
                return None
            link, heading = next(iter(links.values()))
            return link, heading, container
    return None


def listing_articles(html, source, page_url):
    """Only dated article cards; avoid navigation, product links, and undated links."""
    soup = BeautifulSoup(html, 'html.parser')
    articles = {}
    for date in soup.find_all('time'):
        published = listing_date(date)
        card = listing_card(date)
        if published is None or card is None:
            continue
        anchor, heading, container = card
        url = urljoin(page_url, anchor['href']).split('#')[0]
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or parsed.hostname != 'www.anthropic.com':
            continue
        title = heading.get_text(' ', strip=True) if heading else anchor.get_text(' ', strip=True).replace(date.get_text(' ', strip=True), '').strip()
        summary = container.find('p')
        # A page can show one article twice; keep the copy that has a summary.
        if url not in articles or (summary and not articles[url].summary_html):
            articles[url] = Article(source.name, title, url, url, published.astimezone(timezone.utc), None, None, str(summary) if summary else '')
    return list(articles.values())


async def import_articles(config, database, since, until):
    if since >= until:
        raise ValueError('since must precede until')
    sources = [s for s in load_sources(config) if s.enabled]
    report = {'since':since.isoformat(), 'until':until.isoformat(), 'sources':[], 'errors':[]}
    db = connect_database(database)
    started = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=True,headers={'User-Agent':'IntelSourcerer/0.1'}) as client:
            for source in sources:
                save_source(db, source)
                # Fetching below takes a while, and an open write would block note saves throughout.
                db.commit()
                candidates = {}
                stats = {'id':source.id, 'discovered':0, 'in_window':0, 'selected':0, 'inserted':0, 'full_text':0, 'metadata_only':0, 'excluded':[]}
                feed = source.as_feed()
                if feed:
                    result = await fetch_feed(client, feed)
                    if result.error: report['errors'].append(f'{source.id}: {result.error}')
                    candidates.update({a.url:a for a in result.articles})
                elif source.id in {'anthropic', 'anthropic-frontier-red-team'}:
                    indexes = [source.parent_url]
                    if source.id == 'anthropic':
                        indexes.append('https://www.anthropic.com/news')
                    for index in indexes:
                        try:
                            response = await client.get(index)
                            response.raise_for_status()
                            found = listing_articles(response.text,source,str(response.url))
                            if not found:report['errors'].append(f'{source.id}: no dated article cards parsed at {index}')
                            candidates.update({a.url:a for a in found})
                        except httpx.HTTPError as exc:
                            report['errors'].append(f'{source.id}: {index}: {exc}')
                else:
                    report['errors'].append(f'{source.id}: no listing adapter')
                stats['discovered'] = len(candidates)
                selected=[]
                for article in candidates.values():
                    if not in_window(article, since, until):
                        continue
                    stats['in_window']+=1
                    reason = selection_reason(article, source)
                    if reason: selected.append((article,reason))
                    else:stats['excluded'].append(article.title)
                gate=asyncio.Semaphore(4)
                async def extract(item):
                    article,reason=item
                    async with gate:
                        try:
                            response=await client.get(article.url)
                            response.raise_for_status()
                            content=await asyncio.to_thread(trafilatura.extract,response.text,include_comments=False,include_tables=True)
                            if not content:return article,reason,None,'No readable article text extracted',None,[],None
                            clean_html,markdown,media=await asyncio.to_thread(extract_article,response.text,str(response.url))
                            return article,reason,content,None,markdown,media,clean_html
                        except httpx.HTTPError as exc:
                            return article,reason,None,str(exc),None,[],None
                for article,reason,content,error,markdown,media,clean_html in await asyncio.gather(*(extract(i) for i in selected)):
                    stats['selected']+=1
                    stats['inserted']+=save_article(db,source.id,article,content=content,error=error,reason=reason,discovered_at=started,markdown=markdown,media=media,html=clean_html)
                    stats['full_text' if content else 'metadata_only']+=1
                report['sources'].append(stats)
                db.commit()
        db.execute('INSERT INTO import_runs(started_at,since,until,report_json) VALUES (?,?,?,?)', (started,since.isoformat(),until.isoformat(),json.dumps(report)))
        db.commit()
        return report
    finally:
        db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='config/sources.toml')
    parser.add_argument('--database',default='data/intel_sourcerer.sqlite3')
    parser.add_argument('--since',required=True,help='Inclusive YYYY-MM-DD in America/Toronto')
    args=parser.parse_args()
    since=datetime.fromisoformat(args.since).replace(tzinfo=ZoneInfo('America/Toronto')).astimezone(timezone.utc)
    report=asyncio.run(import_articles(args.config,args.database,since,datetime.now(timezone.utc)))
    print(json.dumps(report,indent=2))
    if report['errors']:raise SystemExit(1)
