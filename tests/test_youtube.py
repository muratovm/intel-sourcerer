import asyncio
import httpx
from intel_sourcerer import youtube
from intel_sourcerer.storage import connect_database


def test_video_links():
    for url in ['https://youtu.be/dQw4w9WgXcQ','https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30','https://youtube.com/shorts/dQw4w9WgXcQ']:
        assert youtube.video_id(url) == 'dQw4w9WgXcQ'
    assert youtube.video_id('https://example.com/watch?v=dQw4w9WgXcQ') is None


def test_video_import_preserves_notes(tmp_path, monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200,json={'title':'Security talk','author_name':'Research channel'}))
    monkeypatch.setattr(youtube.httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    dbpath=tmp_path/'articles.db'
    url=asyncio.run(youtube.import_video(dbpath,'dQw4w9WgXcQ'))
    db=connect_database(dbpath)
    row=db.execute('SELECT title,content_markdown FROM articles').fetchone()
    assert row['title']=='Security talk' and 'Video placeholder' in row['content_markdown']
    db.execute("UPDATE articles SET notes='Keep this'");db.commit();db.close()
    asyncio.run(youtube.import_video(dbpath,'dQw4w9WgXcQ'))
    db=connect_database(dbpath);assert db.execute('SELECT notes FROM articles').fetchone()[0]=='Keep this';db.close()
