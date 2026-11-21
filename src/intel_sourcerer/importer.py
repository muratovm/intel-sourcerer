"""Import dated cybersecurity articles from enabled AI-lab publishers."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from math import ceil
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import httpx

from .net_safety import public_only
import trafilatura

from .content import extract_article
from .config import load_sources
from .feeds import BOT_CHECK_PAUSE, Article, FeedCache, Refused, fetch_feed, polite_get
from .storage import connect_database, save_article, save_source

TERMS = re.compile(r"cyber|distillation|malicious|misuse|espionage|phishing|ransomware|vulnerabilit|prompt injection|hacking|frontier safeguards", re.I)
# Everything these teams publish is in scope, so no keyword has to match.
ALWAYS_RELEVANT_SOURCES = {'anthropic-frontier-red-team'}


def try_again_label(seconds):
    if seconds < 3600:
        return 'Trying again within the hour.'
    hours = ceil(seconds / 3600)
    return f"Trying again in about {hours} hour{'s' if hours != 1 else ''}."


def fetch_state(db, source_id):
    row = db.execute('SELECT * FROM source_fetch_state WHERE source_id=?', (source_id,)).fetchone()
    return dict(row) if row else {'etag': None, 'last_modified': None, 'covered_since': None, 'paused_until': None, 'pause_reason': None}


def save_fetch_state(db, source_id, **changes):
    columns = ','.join(changes)
    db.execute(
        f"INSERT INTO source_fetch_state (source_id,{columns}) VALUES (?,{','.join('?' * len(changes))}) "
        f"ON CONFLICT(source_id) DO UPDATE SET {','.join(f'{column}=excluded.{column}' for column in changes)}",
        (source_id, *changes.values()),
    )


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


# Enough words that the feed holds the post itself, not a teaser.
FEED_TEXT_MIN_WORDS = 150
# Sources whose article pages answered with a bot check, until when. The feed's own copy is used meanwhile.
_pages_blocked_until = {}


def feed_text(article):
    """(text, Markdown, media, clean HTML) from the feed's full post, or None when the feed only has a teaser."""
    if not article.content_html:
        return None
    clean_html, markdown, media = extract_article(article.content_html, article.url)
    text = BeautifulSoup(clean_html, 'html.parser').get_text(' ', strip=True)
    if len(text.split()) < FEED_TEXT_MIN_WORDS:
        return None
    return text, markdown, media, clean_html


async def import_articles(config, database, since, until):
    if since >= until:
        raise ValueError('since must precede until')
    sources = [s for s in load_sources(config) if s.enabled]
    report = {'since':since.isoformat(), 'until':until.isoformat(), 'sources':[], 'errors':[], 'paused':[]}
    db = connect_database(database)
    started = datetime.now(timezone.utc).isoformat()
    try:
        # Feeds choose these URLs, so a hostile feed could otherwise point the importer at internal services.
        async with public_only(httpx.AsyncClient(timeout=20,follow_redirects=True,headers={'User-Agent':'IntelSourcerer/0.1'},trust_env=False)) as client:
            for source in sources:
                save_source(db, source)
                # Fetching below takes a while, and an open write would block note saves throughout.
                db.commit()
                candidates = {}
                stats = {'id':source.id, 'discovered':0, 'in_window':0, 'selected':0, 'inserted':0, 'full_text':0, 'already_saved':0, 'metadata_only':0, 'excluded':[]}
                state = fetch_state(db, source.id)
                now = datetime.now(timezone.utc)
                if state['paused_until'] and datetime.fromisoformat(state['paused_until']) > now:
                    wait = (datetime.fromisoformat(state['paused_until']) - now).total_seconds()
                    report['paused'].append(f"{source.name}: {state['pause_reason']}. {try_again_label(wait)}")
                    report['sources'].append(stats)
                    continue
                refused = None
                feed = source.as_feed()
                result = None
                if feed:
                    # A 304 only covers what the last full read covered, so a wider window reads the feed again.
                    covered = state['covered_since'] and datetime.fromisoformat(state['covered_since']) <= since
                    result = await fetch_feed(client, feed, FeedCache(state['etag'], state['last_modified']) if covered else None)
                    refused = result.refusal
                    if result.error and not refused: report['errors'].append(f'{source.id}: {result.error}')
                    candidates.update({a.url:a for a in result.articles})
                elif source.id in {'anthropic', 'anthropic-frontier-red-team'}:
                    indexes = [source.parent_url]
                    if source.id == 'anthropic':
                        indexes.append('https://www.anthropic.com/news')
                    for index in indexes:
                        try:
                            response = await polite_get(client, index)
                            found = listing_articles(response.text,source,str(response.url))
                            if not found:report['errors'].append(f'{source.id}: no dated article cards parsed at {index}')
                            candidates.update({a.url:a for a in found})
                        except Refused as exc:
                            refused = exc.refusal
                            break
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
                # Text already saved isn't fetched again. Failed ones stay out of this set, so they get another try.
                saved={row[0] for row in db.execute("SELECT url FROM articles WHERE source_id=? AND content_status='full_text' AND content_html IS NOT NULL",(source.id,))}
                gate=asyncio.Semaphore(4)
                refusals=[]
                blocked=_pages_blocked_until.get(source.id)
                if blocked and blocked<=now:_pages_blocked_until.pop(source.id,None);blocked=None
                async def from_feed(article,reason,message):
                    copy=await asyncio.to_thread(feed_text,article)
                    if not copy:return article,reason,None,message,None,[],None
                    text,markdown,media,clean_html=copy
                    return article,reason,text,None,markdown,media,clean_html
                async def extract(item):
                    article,reason=item
                    if article.url in saved:return article,reason,None,None,None,[],None
                    async with gate:
                        # While the site's pages answer with a bot check, read the post from the feed instead.
                        if blocked:return await from_feed(article,reason,"blocked by the site's bot check")
                        # One refusal speaks for the site, so the rest of the batch leaves it alone.
                        if refusals:return await from_feed(article,reason,refusals[0].message)
                        try:
                            response=await polite_get(client,article.url)
                            content=await asyncio.to_thread(trafilatura.extract,response.text,include_comments=False,include_tables=True)
                            if not content:return article,reason,None,'No readable article text extracted',None,[],None
                            clean_html,markdown,media=await asyncio.to_thread(extract_article,response.text,str(response.url))
                            return article,reason,content,None,markdown,media,clean_html
                        except Refused as exc:
                            refusals.append(exc.refusal)
                            return await from_feed(article,reason,exc.refusal.message)
                        except httpx.HTTPError as exc:
                            return article,reason,None,str(exc),None,[],None
                extracted=await asyncio.gather(*(extract(i) for i in selected))
                if refusals:
                    _pages_blocked_until[source.id]=now+timedelta(seconds=refusals[0].pause_seconds)
                    # The feed itself still answers. When it supplied every blocked post, nothing is lost,
                    # so the source isn't paused and the next run still reads the feed.
                    if not any(error for _,_,_,error,*_ in extracted):refusals.clear()
                refused=refused or next(iter(refusals),None)
                for article,reason,content,error,markdown,media,clean_html in extracted:
                    stats['selected']+=1
                    stats['inserted']+=save_article(db,source.id,article,content=content,error=error,reason=reason,discovered_at=started,markdown=markdown,media=media,html=clean_html)
                    stats['already_saved' if article.url in saved else 'full_text' if content else 'metadata_only']+=1
                if refused:
                    until=datetime.now(timezone.utc)+timedelta(seconds=refused.pause_seconds)
                    save_fetch_state(db,source.id,paused_until=until.isoformat(),pause_reason=refused.message)
                    report['errors'].append(f'{source.name}: {refused.message}. {try_again_label(refused.pause_seconds)}')
                elif result and not result.error:
                    failed=any(error for _,_,_,error,*_ in extracted)
                    if result.not_modified:
                        save_fetch_state(db,source.id,paused_until=None,pause_reason=None)
                    else:
                        # Forgetting the validators after a failure means the next run sees those articles again.
                        save_fetch_state(db,source.id,etag=None if failed else result.cache.etag,last_modified=None if failed else result.cache.last_modified,covered_since=None if failed else since.isoformat(),paused_until=None,pause_reason=None)
                elif not result:
                    save_fetch_state(db,source.id,paused_until=None,pause_reason=None)
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
