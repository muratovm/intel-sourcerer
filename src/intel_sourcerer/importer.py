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

from .content import extract_markdown
from .config import load_sources
from .feeds import Article, fetch_feed
from .storage import connect_database, save_article, save_source

TERMS = re.compile(r"cyber|distillation|malicious|misuse|espionage|phishing|ransomware|vulnerabilit|prompt injection|hacking|frontier safeguards", re.I)


def in_window(article, since, until):
    return article.published_at is not None and since <= article.published_at <= until


def relevance_reason(article):
    # Exclude broad launch/event posts whose security mention is incidental.
    if re.search(r"^(Introducing |GPT-\d.*: A new generation)|DevDay.*Recap", article.title, re.I):
        return None
    text = article.title + ' ' + BeautifulSoup(article.summary_html, 'html.parser').get_text(' ', strip=True)
    match = TERMS.search(text)
    return f"Cybersecurity rule match: {match.group(0)}; publisher title/summary" if match else None


def listing_articles(html, source, page_url):
    """Only dated article cards; avoid navigation, product links, and undated links."""
    soup = BeautifulSoup(html, 'html.parser')
    articles = {}
    for anchor in soup.select('a[href]'):
        date = anchor.find('time')
        if date is None:
            continue
        raw_date = date.get('datetime') or date.get_text(' ', strip=True)
        published = None
        try:
            published = datetime.fromisoformat(raw_date.replace('Z', '+00:00'))
        except ValueError:
            for fmt in ('%b %d, %Y', '%B %d, %Y'):
                try:
                    published = datetime.strptime(raw_date, fmt)
                    break
                except ValueError:
                    pass
        if published is None:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=ZoneInfo('America/Toronto'))
        url = urljoin(page_url, anchor['href']).split('#')[0]
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or parsed.hostname != 'www.anthropic.com':
            continue
        heading = anchor.find(re.compile('^h[1-6]$'))
        title = heading.get_text(' ', strip=True) if heading else anchor.get_text(' ', strip=True).replace(date.get_text(' ',strip=True),'').strip()
        summary = anchor.find('p')
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
                candidates = {}
                stats = {'id':source.id, 'discovered':0, 'in_window':0, 'selected':0, 'inserted':0, 'full_text':0, 'metadata_only':0, 'excluded':[]}
                feed = source.as_feed()
                if feed:
                    result = await fetch_feed(client, feed)
                    if result.error: report['errors'].append(f'{source.id}: {result.error}')
                    candidates.update({a.url:a for a in result.articles})
                elif source.id == 'anthropic':
                    for index in [source.parent_url, 'https://www.anthropic.com/news']:
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
                    reason = relevance_reason(article)
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
                            if not content:return article,reason,None,'No readable article text extracted',None,[]
                            markdown,media=await asyncio.to_thread(extract_markdown,response.text,str(response.url))
                            return article,reason,content,None,markdown,media
                        except httpx.HTTPError as exc:
                            return article,reason,None,str(exc),None,[]
                for article,reason,content,error,markdown,media in await asyncio.gather(*(extract(i) for i in selected)):
                    stats['selected']+=1
                    stats['inserted']+=save_article(db,source.id,article,content=content,error=error,reason=reason,discovered_at=started,markdown=markdown,media=media)
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
