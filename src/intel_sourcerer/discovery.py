"""Discover and save articles for a reading queue."""
import asyncio
from datetime import datetime, timezone
import hashlib
import ipaddress
import socket
from urllib.parse import urlsplit, urljoin, urldefrag
import httpx
from bs4 import BeautifulSoup
import trafilatura
from .content import extract_markdown
from .config import PublisherSource
from .feeds import Article
from .storage import connect_database, save_source, save_article


def article_types(title, content=''):
    text=(title+' '+(content or '')[:1500]).lower()
    types=['Threat research']
    if any(t in text for t in ('vulnerab','exploit','cve-','zero-day','prompt injection')):types.append('Vulnerabilities')
    if any(t in text for t in ('misuse','distillation',' ai ','model','claude','llm')):types.append('AI misuse')
    if any(t in text for t in ('incident','campaign','disrupt','espionage','ransomware','malware')):types.append('Incident analysis')
    return types


def public_url(url):
    parsed=urlsplit(url)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None,80,443}:
        raise ValueError('Use a public HTTP or HTTPS article URL.')
    addresses=socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('Use a public article URL, rather than a local network address.')
    return urldefrag(url)[0]


async def import_link(database, url):
    if not isinstance(url,str) or len(url)>4096:raise ValueError('Invalid article URL.')
    url=urldefrag(url.strip())[0]
    db=connect_database(database)
    try:
        if db.execute('SELECT 1 FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.url=? AND s.enabled=1',(url,)).fetchone():return url
        async with httpx.AsyncClient(timeout=20,headers={'User-Agent':'IntelSourcerer/0.1'}) as client:
            for _ in range(6):
                url=await asyncio.to_thread(public_url,url)
                async with client.stream('GET',url) as response:
                    if response.is_redirect:
                        url=urljoin(url,response.headers.get('location',''));continue
                    response.raise_for_status()
                    if 'html' not in response.headers.get('content-type',''):raise ValueError('This link must point to an HTML article.')
                    chunks=[];size=0
                    async for chunk in response.aiter_bytes():
                        size+=len(chunk)
                        if size>5_000_000:raise ValueError('This article page is too large.')
                        chunks.append(chunk)
                    html=b''.join(chunks).decode('utf-8',errors='replace');break
            else:raise ValueError('Too many redirects for this article.')
        soup=BeautifulSoup(html,'html.parser');title=soup.find('meta',property='og:title')
        title=title.get('content') if title else soup.title.get_text(strip=True) if soup.title else urlsplit(url).hostname
        content=trafilatura.extract(html,include_comments=False,include_tables=True)
        if not content:raise ValueError('No readable article text found. Try the publisher’s direct article link.')
        markdown,media=extract_markdown(html,url)
        now=datetime.now(timezone.utc);published=now
        stamp=soup.find('meta',property='article:published_time') or soup.find('time',datetime=True)
        if stamp:
            try:published=datetime.fromisoformat((stamp.get('content') or stamp.get('datetime')).replace('Z','+00:00'));published=published if published.tzinfo else published.replace(tzinfo=timezone.utc)
            except (ValueError,TypeError):pass
        host=urlsplit(url).hostname;source=PublisherSource('link-'+hashlib.sha256(host.encode()).hexdigest()[:12],host,'https://'+host,'manual')
        save_source(db,source)
        article=Article(host,title,url,url,published,None,None,'')
        save_article(db,source.id,article,content=content,error=None,reason='Manually added link; date defaults to added time if publisher date is unavailable',discovered_at=now.isoformat(),markdown=markdown,media=media)
        db.commit();return url
    finally:db.close()
