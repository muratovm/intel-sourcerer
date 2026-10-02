"""Persistent reading queues and article notes."""
import sqlite3
import bleach
from bs4 import BeautifulSoup
import uuid
from datetime import datetime, timezone
from pathlib import Path


class SessionError(ValueError):
    pass


def session_db(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def has_notes(text, format='plain'):
    if format=='html':text=BeautifulSoup(text or '', 'html.parser').get_text()
    return bool((text or '').replace('\u200b','').strip())


def queue_state(db):
    queue = db.execute('SELECT * FROM reading_queues ORDER BY rowid DESC LIMIT 1').fetchone()
    if not queue:
        return None
    items = [dict(r) for r in db.execute('''SELECT a.url,a.title,a.published_at,a.is_read,a.notes,a.notes_format,s.name AS publisher
      FROM queue_items q JOIN articles a ON a.url=q.article_url JOIN sources s ON s.id=a.source_id
      WHERE q.queue_id=? ORDER BY q.position''', (queue['id'],))]
    for item in items:item['has_notes']=has_notes(item.pop('notes'),item.pop('notes_format'))
    return dict(id=queue['id'],cursor=queue['cursor'],finished=bool(queue['finished']),items=items,
      read_count=sum(bool(i['is_read']) for i in items))


def create_queue(db, urls):
    if not isinstance(urls, list) or not urls or len(urls)>100 or any(not isinstance(u,str) for u in urls):
        raise SessionError('Choose between 1 and 100 articles.')
    if len(set(urls)) != len(urls):
        raise SessionError('Articles cannot appear twice in a queue.')
    for url in urls:
        if not db.execute('SELECT 1 FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.url=? AND s.enabled=1',(url,)).fetchone():
            raise SessionError('One of the selected articles is unavailable. Refresh and try again.')
    id=uuid.uuid4().hex
    db.execute('INSERT INTO reading_queues(id,created_at,cursor,finished) VALUES (?,?,0,0)',(id,datetime.now(timezone.utc).isoformat()))
    db.executemany('INSERT INTO queue_items VALUES (?,?,?)',[(id,i,url) for i,url in enumerate(urls)])
    return queue_state(db)


def clean_notes(text):
    soup=BeautifulSoup(text,'html.parser')
    for tag in list(soup.find_all(True)):
        if 'background-color' in tag.get('style','') and tag.name!='mark':
            if tag.name=='span':tag.name='mark'
            else:tag.wrap(soup.new_tag('mark'))
    return bleach.clean(str(soup),tags=['p','div','br','strong','b','em','i','mark','blockquote','ul','ol','li'],attributes={},strip=True)


def save_notes(db,url,text,revision,format='plain'):
    if not isinstance(text,str) or len(text)>100_000 or type(revision) is not int:
        raise SessionError('Invalid note content or revision.')
    if format not in {'plain','html'}:raise SessionError('Invalid note format.')
    if format=='html':text=clean_notes(text)
    result=db.execute('UPDATE articles SET notes=?,notes_format=?,notes_revision=notes_revision+1,notes_updated_at=? WHERE url=? AND notes_revision=?',(text,format,datetime.now(timezone.utc).isoformat(),url,revision))
    if result.rowcount != 1:
        raise SessionError('Notes changed in another window. Your draft is kept here; reload before continuing.')
    return revision+1


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


def delete_article(db,url):
    if not isinstance(url,str) or not db.execute('SELECT 1 FROM articles WHERE url=?',(url,)).fetchone():
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
    db.execute('DELETE FROM article_reads WHERE article_url=?',(url,))
    db.execute('DELETE FROM articles WHERE url=?',(url,))
    return {'deleted':url}
