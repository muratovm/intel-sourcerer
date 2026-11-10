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


def playlist_id(url):
    """The list ID of a youtube.com/playlist?list=... link, or None. A watch link inside a playlist stays one video."""
    parsed = urlsplit(url) if isinstance(url, str) else None
    if not parsed or (parsed.hostname or '').lower() not in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
        return None
    if parsed.scheme not in {'http', 'https'} or parsed.path.rstrip('/') != '/playlist':
        return None
    value = parse_qs(parsed.query).get('list', [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{10,64}', value):
        raise ValueError('Use a link to a specific YouTube playlist.')
    return value


UNAVAILABLE = {'[Private video]', '[Deleted video]', '[Unavailable video]'}


def list_playlist(identifier, limit=500):
    """Title and videos of a playlist, read by yt-dlp with the Chrome cookies on this Mac. Nothing is downloaded."""
    import json
    import subprocess
    from .transcripts import downloader_command, tool_env

    if not re.fullmatch(r'[A-Za-z0-9_-]{10,64}', identifier or ''):
        raise ValueError('Not a YouTube playlist ID.')
    command = downloader_command()
    if not command:
        raise ValueError('Playlist import needs uv (uvx) on this machine.')
    result = subprocess.run(
        [command, '--from', 'yt-dlp[default]', 'yt-dlp', '--flat-playlist', '--dump-single-json',
         '--cookies-from-browser', 'chrome', '--playlist-end', str(limit), '--quiet', '--no-warnings',
         '--', 'https://www.youtube.com/playlist?list=' + identifier],
        capture_output=True, text=True, env=tool_env(command), timeout=600, stdin=subprocess.DEVNULL,
    )
    try:
        data = json.loads(result.stdout) if result.returncode == 0 else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        errors = [line for line in result.stderr.splitlines() if 'ERROR' in line]
        detail = ' ' + errors[-1].split('ERROR:', 1)[-1].strip()[:200] if errors else ''
        raise ValueError('yt-dlp could not read this playlist.' + detail +
                         ' If macOS asked on the Mac for access to Chrome Safe Storage, allow it and try again.')
    videos = []
    for entry in data.get('entries') or []:
        if not isinstance(entry, dict):
            continue
        video, title = str(entry.get('id') or ''), str(entry.get('title') or '').strip()
        if re.fullmatch(r'[A-Za-z0-9_-]{11}', video) and title not in UNAVAILABLE:
            videos.append({'id': video, 'title': title or 'YouTube video',
                           'author': str(entry.get('channel') or entry.get('uploader') or 'YouTube')})
    return {'title': str(data.get('title') or 'YouTube playlist'), 'videos': videos[:limit]}


def save_video(db, identifier, title, author):
    url = 'https://www.youtube.com/watch?v=' + identifier
    if db.execute('SELECT 1 FROM articles WHERE url=?', (url,)).fetchone():
        return url
    source = PublisherSource('youtube', 'YouTube', 'https://www.youtube.com', 'manual')
    now = datetime.now(timezone.utc)
    markdown = f'> **Video placeholder:** {title}\n>\n> [Watch on YouTube]({url})\n\nChannel: {author}'
    save_source(db, source)
    save_article(db, source.id, Article('YouTube', title, url, url, now, author, None, ''), content=f'YouTube video by {author}.', error=None, reason='Manually added YouTube video; date is import time', discovered_at=now.isoformat(), markdown=markdown, media=[{'position':1,'kind':'video','description':title,'url':url}])
    db.execute("UPDATE articles SET content_format='video' WHERE url=?", (url,))
    return url


def import_playlist(database, identifier, lister=None, topic_id=None):
    """Save every available video into a topic: the one given, or one named after the playlist. Importing again adds new videos."""
    from .topics import MAX_NAME, _require_topic, save_articles_as_topic

    playlist = (lister or list_playlist)(identifier)
    if not playlist['videos']:
        raise ValueError('This playlist has no videos that can be imported.')
    name = ' '.join(playlist['title'].split())[:MAX_NAME].strip() or 'YouTube playlist'
    db = connect_database(database)
    try:
        if topic_id is not None:
            name = _require_topic(db, topic_id)['name']
        urls = [save_video(db, v['id'], v['title'], v['author']) for v in playlist['videos']]
        topic = save_articles_as_topic(db, name, urls)
        db.commit()
    finally:
        db.close()
    return {'url': urls[-1], 'topic': topic['id'], 'name': topic['name'], 'videos': len(urls), 'added': topic['added']}


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
        save_video(db, identifier, metadata.get('title') or 'YouTube video', metadata.get('author_name') or 'YouTube')
        db.commit()
        return url
    finally:
        db.close()
