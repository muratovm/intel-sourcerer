"""Discover and save articles for a reading queue."""
import asyncio
from datetime import datetime, timezone
import hashlib
import ipaddress
import socket
from urllib.parse import urlsplit, urljoin, urldefrag, unquote
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
import trafilatura
from .content import extract_markdown
from .pdf_content import extract_pdf
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
    url=url.strip()
    from .youtube import video_id, import_video
    identifier = video_id(url)
    if identifier:
        return await import_video(database, identifier)
    from .media_import import AUDIO, VIDEO, SUPPORTED, import_file, save_item, media_markdown
    if urlsplit(url).hostname in {'github.com','www.github.com'}:
        await asyncio.to_thread(public_url,url)
        from .github_import import import_github
        return await import_github(database,url)
    suffix=Path(urlsplit(url).path).suffix.lower()
    if urlsplit(url).scheme in {'http','https'} and suffix in AUDIO | VIDEO:
        url=await asyncio.to_thread(public_url,url)
        kind='audio' if suffix in AUDIO else 'video'
        title=unquote(Path(urlsplit(url).path).stem)
        return save_item(database,url,title,media_markdown(title,url,kind),kind,urlsplit(url).hostname)
    local_path=None
    if url.startswith('/') or url.startswith('~/'):
        local_path=Path(url).expanduser()
    elif urlsplit(url).scheme == 'file':
        parsed=urlsplit(url)
        if parsed.netloc not in {'', 'localhost'} or parsed.query:
            raise ValueError('Use a local PDF path, without a remote host or query.')
        local_path=Path(unquote(parsed.path))
    if local_path is not None:
        local_path=local_path.resolve()
        if not local_path.is_file() or local_path.suffix.lower() not in SUPPORTED:
            raise ValueError('Use an existing local PDF file or supported media document.')
        if local_path.suffix.lower() != '.pdf':
            with local_path.open('rb') as local_file:
                return await import_file(database,local_file.read(100_000_001),local_path.name)
        url=local_path.as_uri()
    else:
        url=urldefrag(url)[0]
    db=connect_database(database)
    try:
        if db.execute('SELECT 1 FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.url=? AND s.enabled=1',(url,)).fetchone():return url
        if local_path is not None:
            with local_path.open('rb') as file:
                payload=file.read(20_000_001)
            if len(payload)>20_000_000:
                raise ValueError('This PDF exceeds the 20 MB limit.')
            is_pdf=True
        else:
            async with httpx.AsyncClient(timeout=20,headers={'User-Agent':'IntelSourcerer/0.1'}) as client:
                for _ in range(6):
                    url=await asyncio.to_thread(public_url,url)
                    async with client.stream('GET',url) as response:
                        if response.is_redirect:
                            url=urljoin(url,response.headers.get('location',''));continue
                        if response.status_code in {401,403,429} and (urlsplit(url).hostname or '').removeprefix('www.') in {'x.com','twitter.com','linkedin.com','bsky.app','mastodon.social','reddit.com'}:
                            return save_item(database,url,'Social post',f'This platform restricts automated access. Saved as a bookmark.\n\n[Open original post]({url})','social',urlsplit(url).hostname)
                        response.raise_for_status()
                        content_type=response.headers.get('content-type','').lower()
                        chunks=[];size=0
                        async for chunk in response.aiter_bytes():
                            size+=len(chunk)
                            if size>20_000_000:raise ValueError('This article or PDF exceeds the 20 MB limit.')
                            chunks.append(chunk)
                        payload=b''.join(chunks)
                        if Path(urlsplit(url).path).suffix.lower() in {'.eml','.pptx'}:
                            return await import_file(database,payload,unquote(Path(urlsplit(url).path).name))
                        is_pdf='application/pdf' in content_type or payload[:1024].lstrip().startswith(b'%PDF-')
                        if not is_pdf and ('xml' in content_type or payload.lstrip().startswith((b'<?xml', b'<rss', b'<feed'))):
                            from .media_import import import_podcast_feed
                            return await import_podcast_feed(database,payload,url)
                        if not is_pdf and 'html' not in content_type:raise ValueError('This link must point to an HTML article or PDF.')
                        html=payload.decode('utf-8',errors='replace') if not is_pdf else ''
                        break
                else:raise ValueError('Too many redirects for this article.')
        now=datetime.now(timezone.utc);published=now
        if is_pdf:
            title,content,markdown=await asyncio.to_thread(extract_pdf,payload,url)
            from .pdf_layout import extract_layout
            markdown,media=await asyncio.to_thread(extract_layout,payload,url,Path(database).resolve().parent / 'pdf-assets')
        else:
            soup=BeautifulSoup(html,'html.parser');title=soup.find('meta',property='og:title')
            title=title.get('content') if title else soup.title.get_text(strip=True) if soup.title else urlsplit(url).hostname
            content=trafilatura.extract(html,include_comments=False,include_tables=True)
            social_host=(urlsplit(url).hostname or '').removeprefix('www.') in {'x.com','twitter.com','linkedin.com','bsky.app','mastodon.social','reddit.com'}
            if not content and social_host:
                markdown=f'Saved social link. This platform did not expose readable post text. Open the original to view it.\n\n[Open post]({url})'
                return save_item(database,url,title or 'Social post',markdown,'social',urlsplit(url).hostname)
            if not content:raise ValueError('No readable article text found. Try the publisher’s direct article link.')
            markdown,media=extract_markdown(html,url)
            stamp=soup.find('meta',property='article:published_time') or soup.find('time',datetime=True)
            if stamp:
                try:published=datetime.fromisoformat((stamp.get('content') or stamp.get('datetime')).replace('Z','+00:00'));published=published if published.tzinfo else published.replace(tzinfo=timezone.utc)
                except (ValueError,TypeError):pass
        host='Local PDFs' if local_path else urlsplit(url).hostname
        source=PublisherSource('link-'+hashlib.sha256(host.encode()).hexdigest()[:12],host,'http://localhost' if local_path else 'https://'+host,'manual')
        save_source(db,source)
        article=Article(host,title,url,url,published,None,None,'')
        save_article(db,source.id,article,content=content,error=None,reason='Manually added link; date defaults to added time if publisher date is unavailable',discovered_at=now.isoformat(),markdown=markdown,media=media)
        content_format='paper' if is_pdf else 'social' if social_host else 'newsletter' if 'substack.com' in host else 'article'
        db.execute('UPDATE articles SET content_format=? WHERE url=?',(content_format,url))
        db.commit();return url
    finally:db.close()


async def import_pdf_upload(database, payload):
    """Keep an uploaded PDF under application data, then use the regular importer."""
    if not payload or len(payload) > 20_000_000:
        raise ValueError("PDF files must be no larger than 20 MB.")
    # Validate before writing; use a content hash rather than an untrusted filename.
    await asyncio.to_thread(extract_pdf, payload, "https://upload.invalid/document.pdf")
    directory = Path(database).resolve().parent / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (hashlib.sha256(payload).hexdigest() + ".pdf")
    if not path.exists():
        path.write_bytes(payload)
    return await import_link(database, str(path))
