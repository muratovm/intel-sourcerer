"""Persistent reading queues and article notes."""
import re
import sqlite3
import bleach
from bs4 import BeautifulSoup
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .storage import BUSY_TIMEOUT


class SessionError(ValueError):
    pass


def session_db(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=BUSY_TIMEOUT)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def has_notes(text, format='plain'):
    if format=='html':text=BeautifulSoup(text or '', 'html.parser').get_text()
    return bool((text or '').replace('\u200b','').strip())


def _item_minutes(content_format, url, word_count, media_json, fallback_text=""):
    from .content import estimate_minutes

    return estimate_minutes(content_format, url, word_count, media_json, fallback_text)


def queue_state(db):
    queue = db.execute('SELECT * FROM reading_queues ORDER BY rowid DESC LIMIT 1').fetchone()
    if not queue:
        return None
    items = [dict(r) for r in db.execute('''SELECT a.url,a.title,a.published_at,a.is_read,a.notes,a.notes_format,
      a.content_format,a.media_json,a.word_count,
      CASE WHEN a.word_count IS NULL THEN coalesce(a.content_text,'') ELSE '' END AS fallback_text,
      s.name AS publisher
      FROM queue_items q JOIN articles a ON a.url=q.article_url JOIN sources s ON s.id=a.source_id
      WHERE q.queue_id=? ORDER BY q.position''', (queue['id'],))]
    for item in items:
        item['has_notes'] = has_notes(item.pop('notes'), item.pop('notes_format'))
        item['minutes'] = _item_minutes(
            item.pop('content_format'),
            item['url'],
            item.pop('word_count'),
            item.pop('media_json'),
            item.pop('fallback_text'),
        )
    return dict(id=queue['id'],cursor=queue['cursor'],finished=bool(queue['finished']),items=items,
      read_count=sum(bool(i['is_read']) for i in items),created_at=queue['created_at'])


MAX_QUEUE_ITEMS = 10


def create_queue(db, urls):
    if not isinstance(urls, list) or not urls or len(urls) > MAX_QUEUE_ITEMS or any(not isinstance(u, str) for u in urls):
        raise SessionError(f'Choose between 1 and {MAX_QUEUE_ITEMS} articles per reading session.')
    if len(set(urls)) != len(urls):
        raise SessionError('Articles cannot appear twice in a queue.')
    for url in urls:
        if not db.execute('SELECT 1 FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.url=? AND s.enabled=1',(url,)).fetchone():
            raise SessionError('One of the selected articles is unavailable. Refresh and try again.')
    id=uuid.uuid4().hex
    db.execute('INSERT INTO reading_queues(id,created_at,cursor,finished) VALUES (?,?,0,0)',(id,datetime.now(timezone.utc).isoformat()))
    db.executemany('INSERT INTO queue_items VALUES (?,?,?)',[(id,i,url) for i,url in enumerate(urls)])
    return queue_state(db)


def update_queue(db, urls):
    """Edit the single active reading queue in place (add, remove, reorder, or clear)."""
    if not isinstance(urls, list) or any(not isinstance(u, str) for u in urls):
        raise SessionError(f'A reading queue can hold up to {MAX_QUEUE_ITEMS} articles at a time.')
    if len(set(urls)) != len(urls):
        raise SessionError('Articles cannot appear twice in a queue.')
    current = db.execute('SELECT * FROM reading_queues ORDER BY rowid DESC LIMIT 1').fetchone()
    if not urls:
        if current:
            db.execute('DELETE FROM queue_items WHERE queue_id=?', (current['id'],))
            db.execute('UPDATE reading_queues SET cursor=0, finished=1 WHERE id=?', (current['id'],))
        return queue_state(db)
    existing = (
        [
            r['article_url']
            for r in db.execute(
                'SELECT article_url FROM queue_items WHERE queue_id=? ORDER BY position',
                (current['id'],),
            )
        ]
        if current and not current['finished']
        else []
    )
    # Allow shrinking a legacy oversized queue, but never exceed MAX_QUEUE_ITEMS when adding.
    if len(urls) > MAX_QUEUE_ITEMS and len(urls) >= len(existing):
        raise SessionError(f'A reading queue can hold up to {MAX_QUEUE_ITEMS} articles at a time.')
    for url in urls:
        if not db.execute('SELECT 1 FROM articles WHERE url=?', (url,)).fetchone():
            raise SessionError('One of the selected articles is unavailable. Refresh and try again.')
    if current and not current['finished']:
        # Reading follows the list as shown: the next article is the first unread one from the top,
        # so dragging an article to the top makes it the one you start with.
        read = {r[0] for r in db.execute(
            f"SELECT url FROM articles WHERE is_read=1 AND url IN ({','.join('?' * len(urls))})", urls)}
        unread = [i for i, url in enumerate(urls) if url not in read]
        active_url = existing[current['cursor']] if 0 <= current['cursor'] < len(existing) else None
        if unread:
            new_cursor = unread[0]
        elif active_url and active_url in urls:
            new_cursor = urls.index(active_url)
        else:
            new_cursor = min(max(0, current['cursor']), len(urls) - 1)
        db.execute('DELETE FROM queue_items WHERE queue_id=?', (current['id'],))
        db.executemany('INSERT INTO queue_items VALUES (?,?,?)', [(current['id'], i, url) for i, url in enumerate(urls)])
        db.execute('UPDATE reading_queues SET cursor=?, finished=0 WHERE id=?', (new_cursor, current['id']))
        return queue_state(db)
    qid = uuid.uuid4().hex
    db.execute('INSERT INTO reading_queues(id,created_at,cursor,finished) VALUES (?,?,0,0)', (qid, datetime.now(timezone.utc).isoformat()))
    db.executemany('INSERT INTO queue_items VALUES (?,?,?)', [(qid, i, url) for i, url in enumerate(urls)])
    return queue_state(db)


def _note_attribute(tag, name, value):
    if tag == 'a':
        return name in {'href', 'target', 'rel'}
    return (tag, name, value) in {('ul', 'class', 'checklist'), ('li', 'data-checked', 'true')}


def clean_notes(text):
    soup=BeautifulSoup(text,'html.parser')
    for tag in list(soup.find_all(True)):
        if 'background-color' in tag.get('style','') and tag.name!='mark':
            if tag.name=='span':tag.name='mark'
            else:tag.wrap(soup.new_tag('mark'))
    for link in soup.find_all('a'):
        href=str(link.get('href','')).strip()
        if not re.match(r'(https?:|mailto:|/\?open=)',href,re.I):
            link.unwrap()
            continue
        link.attrs={'href':href,'target':'_blank','rel':'noopener noreferrer'}
    return bleach.clean(str(soup),tags=['p','div','br','strong','b','em','i','mark','blockquote','ul','ol','li','pre','code','a','table','thead','tbody','tr','th','td'],attributes=_note_attribute,protocols=['http','https','mailto'],strip=True)


def save_notes(db,url,text,revision,format='plain'):
    if not isinstance(text,str) or len(text)>100_000 or type(revision) is not int:
        raise SessionError('Invalid note content or revision.')
    if format not in {'plain','html'}:raise SessionError('Invalid note format.')
    if format=='html':text=clean_notes(text)
    result=db.execute('UPDATE articles SET notes=?,notes_format=?,notes_revision=notes_revision+1,notes_updated_at=? WHERE url=? AND notes_revision=?',(text,format,datetime.now(timezone.utc).isoformat(),url,revision))
    if result.rowcount != 1:
        raise SessionError('Notes changed in another window. Your draft is kept here; reload before continuing.')
    return revision+1


def set_article_read(db, url, is_read):
    """Mark a single article read or unread directly (e.g. when opened standalone in the reader)."""
    if not isinstance(url, str) or type(is_read) is not bool:
        raise SessionError('Invalid read status.')
    result = db.execute('UPDATE articles SET is_read=? WHERE url=?', (int(is_read), url))
    if result.rowcount != 1:
        raise SessionError('Article not found. Refresh and try again.')
    return {'url': url, 'is_read': is_read, 'queue': queue_state(db)}


def navigate(db,queue_id,cursor,action):
    state=queue_state(db)
    if type(cursor) is not int or not state or state['id']!=queue_id or state['cursor']!=cursor or state['finished']:
        raise SessionError('The queue changed in another window. Refresh to continue.')
    if action not in {'previous','next','mark_next','unread'}:
        raise SessionError('Unknown queue action.')
    url=state['items'][cursor]['url']
    if action=='unread':
        db.execute('UPDATE articles SET is_read=0 WHERE url=?',(url,))
    elif action=='previous':
        db.execute('UPDATE reading_queues SET cursor=? WHERE id=?',(max(0,cursor-1),queue_id))
    else:
        if action=='mark_next':db.execute('UPDATE articles SET is_read=1 WHERE url=?',(url,))
        if cursor+1==len(state['items']):
            db.execute('UPDATE reading_queues SET finished=1 WHERE id=?',(queue_id,))
        else:
            db.execute('UPDATE reading_queues SET cursor=cursor+1 WHERE id=?',(queue_id,))
    return queue_state(db)


def _cleanup_article_assets(db, url, html, markdown):
    """Remove local upload/media/PDF-asset files owned by a deleted article if no other article uses them."""
    from urllib.parse import unquote, urlsplit

    row = db.execute("PRAGMA database_list").fetchone()
    db_file = row[2] if row and len(row) > 2 else ""
    if not db_file:
        return
    base = Path(db_file).resolve().parent
    candidates = set()
    body = (html or "") + "\n" + (markdown or "")
    for match in re.findall(r"/pdf-assets/[a-f0-9]{64}-\d+-\d+\.png", body):
        candidates.add((base / "pdf-assets" / match.rsplit("/", 1)[-1], match))
    for match in re.findall(r"/local-media/[a-f0-9]{64}\.(?:mp3|m4a|ogg|wav|mp4|webm|png)", body):
        candidates.add((base / "media" / match.rsplit("/", 1)[-1], match))
    parsed = urlsplit(url)
    if parsed.scheme == "file":
        target = Path(unquote(parsed.path)).resolve()
        for folder in ("uploads", "media"):
            root = (base / folder).resolve()
            if target.parent == root:
                candidates.add((target, url))
    for file_path, token in candidates:
        if not file_path.is_file():
            continue
        still_used = db.execute(
            "SELECT 1 FROM articles WHERE url=? OR instr(coalesce(content_html,''),?)>0 OR instr(coalesce(content_markdown,''),?)>0 LIMIT 1",
            (token, token, token),
        ).fetchone()
        if not still_used:
            file_path.unlink(missing_ok=True)


def delete_article(db,url):
    article = db.execute('SELECT content_html,content_markdown FROM articles WHERE url=?',(url,)).fetchone() if isinstance(url,str) else None
    if not article:
        raise SessionError('Article not found. Refresh the library.')
    queues=db.execute('SELECT q.* FROM reading_queues q JOIN queue_items i ON i.queue_id=q.id WHERE i.article_url=?',(url,)).fetchall()
    for queue in queues:
        items=db.execute('SELECT article_url,position FROM queue_items WHERE queue_id=? ORDER BY position',(queue['id'],)).fetchall()
        removed=next(i['position'] for i in items if i['article_url']==url)
        remaining=[i['article_url'] for i in items if i['article_url']!=url]
        db.execute('DELETE FROM queue_items WHERE queue_id=?',(queue['id'],))
        db.executemany('INSERT INTO queue_items VALUES (?,?,?)',[(queue['id'],i,u) for i,u in enumerate(remaining)])
        cursor=max(0,queue['cursor']-int(removed<queue['cursor']))
        db.execute('UPDATE reading_queues SET cursor=?,finished=? WHERE id=?',(min(cursor,max(0,len(remaining)-1)),int(bool(queue['finished']) or not remaining),queue['id']))
    db.execute('DELETE FROM article_highlights WHERE article_url=?',(url,))
    db.execute('DELETE FROM reading_positions WHERE article_url=?',(url,))
    db.execute('DELETE FROM timed_reading_sessions WHERE article_url=?',(url,))
    db.execute('DELETE FROM article_reads WHERE article_url=?',(url,))
    from .topics import remove_citations

    remove_citations(db,url)
    db.execute('DELETE FROM articles WHERE url=?',(url,))
    _cleanup_article_assets(db, url, article['content_html'], article['content_markdown'])
    return {'deleted':url}


def rename_article(db, url, title):
    """Change the saved display title without altering content or reading history."""
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 500:
        raise SessionError("Enter a title between 1 and 500 characters.")
    if not isinstance(url, str):
        raise SessionError("Article not found.")
    title = title.strip()
    if db.execute("UPDATE articles SET title=? WHERE url=?", (title, url)).rowcount != 1:
        raise SessionError("Article not found.")
    return {"title": title}
