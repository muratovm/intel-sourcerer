"""Import YouTube metadata without downloading video content."""
import re
from urllib.parse import urlsplit, parse_qs
from html import escape
from datetime import datetime, timezone
import httpx
from .config import PublisherSource
from .feeds import Article
from .storage import connect_database, save_source, save_article


def video_id(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    if host not in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'www.youtube-nocookie.com'}:
        return None
    if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
        raise ValueError('Use a valid YouTube video URL.')
    parts = parsed.path.strip('/').split('/')
    value = parts[0] if host == 'youtu.be' else parts[1] if len(parts) > 1 and parts[0] in {'shorts', 'embed', 'live'} else parse_qs(parsed.query).get('v', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', value):
        raise ValueError('Use a link to a specific YouTube video.')
    return value


async def import_video(database, identifier):
    url = 'https://www.youtube.com/watch?v=' + identifier
    db = connect_database(database)
    try:
        if db.execute('SELECT 1 FROM articles WHERE url=?', (url,)).fetchone():
            return url
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get('https://www.youtube.com/oembed', params={'url': url, 'format': 'json'})
            if response.status_code != 200:
                raise ValueError('YouTube could not provide this video. Check that it is public and available.')
            metadata = response.json()
        title = metadata.get('title') or 'YouTube video'
        author = metadata.get('author_name') or 'YouTube'
        source = PublisherSource('youtube', 'YouTube', 'https://www.youtube.com', 'manual')
        now = datetime.now(timezone.utc)
        markdown = f'> **Video placeholder:** {title}\n>\n> [Watch on YouTube]({url})\n\nChannel: {author}'
        save_source(db, source)
        save_article(db, source.id, Article('YouTube', title, url, url, now, author, None, ''), content=f'YouTube video by {author}.', error=None, reason='Manually added YouTube video; date is import time', discovered_at=now.isoformat(), markdown=markdown, media=[{'position':1,'kind':'video','description':title,'url':url}])
        db.execute("UPDATE articles SET content_format='video' WHERE url=?", (url,))
        db.commit()
        return url
    finally:
        db.close()
