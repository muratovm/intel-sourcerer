import asyncio

from intel_sourcerer import youtube
from intel_sourcerer.storage import connect_database


def duration_of(database, url):
    with connect_database(database) as db:
        return youtube.video_duration(db.execute('SELECT media_json FROM articles WHERE url=?', (url,)).fetchone()[0])


def test_seconds_accepts_only_sane_lengths():
    assert youtube.seconds(842.4) == 842 and youtube.seconds('60') == 60
    assert youtube.seconds(None) is None and youtube.seconds('NA') is None
    assert youtube.seconds(0) is None and youtube.seconds(10 ** 9) is None


def test_playlist_lengths_are_saved_and_fill_in_on_reimport(tmp_path):
    database = tmp_path / 'articles.db'
    connect_database(database).close()
    videos = [{'id': 'aaaaaaaaaaa', 'title': 'Talk', 'author': 'SANS', 'duration': None}]
    youtube.import_playlist(database, 'PLfouvuAjspTrreLxdVrUYE', lister=lambda _: {'title': 'T', 'videos': videos})
    url = 'https://www.youtube.com/watch?v=aaaaaaaaaaa'
    assert duration_of(database, url) is None
    videos[0]['duration'] = 845.0
    youtube.import_playlist(database, 'PLfouvuAjspTrreLxdVrUYE', lister=lambda _: {'title': 'T', 'videos': videos})
    assert duration_of(database, url) == 845


def test_backfill_fills_only_missing_lengths(tmp_path):
    database = tmp_path / 'articles.db'
    with connect_database(database) as db:
        youtube.save_video(db, 'aaaaaaaaaaa', 'Known', 'SANS', 300)
        youtube.save_video(db, 'bbbbbbbbbbb', 'Unknown', 'SANS')
    asked = []
    filled = youtube.backfill_durations(database, fetcher=lambda ids: asked.extend(ids) or {'bbbbbbbbbbb': 61})
    assert asked == ['bbbbbbbbbbb'] and filled == 1
    assert duration_of(database, 'https://www.youtube.com/watch?v=bbbbbbbbbbb') == 61


def test_single_import_survives_a_failed_length_lookup(tmp_path, monkeypatch):
    database = tmp_path / 'articles.db'
    connect_database(database).close()

    class Response:
        status_code = 200
        def json(self):
            return {'title': 'Talk', 'author_name': 'SANS'}

    class Client:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
        async def get(self, *_, **__): return Response()

    monkeypatch.setattr(youtube.httpx, 'AsyncClient', Client)
    def broken(_):
        raise RuntimeError('no cookies')
    url = asyncio.run(youtube.import_video(database, 'aaaaaaaaaaa', broken))
    assert duration_of(database, url) is None
    url = asyncio.run(youtube.import_video(database, 'ccccccccccc', lambda ids: {ids[0]: 125}))
    assert duration_of(database, url) == 125


def test_library_panel_lists_video_details(tmp_path):
    from intel_sourcerer.library import library_rows, resource_details
    database = tmp_path / 'articles.db'
    with connect_database(database) as db:
        youtube.save_video(db, 'aaaaaaaaaaa', 'Talk', 'SANS Institute', 3725)
        db.execute("UPDATE articles SET author=NULL")  # as videos saved before the fix are stored
        row = next(r for r in library_rows(db) if r['url'].endswith('aaaaaaaaaaa'))
    details = resource_details(row)
    assert '1 h 02 min' in details and 'SANS Institute' in details
    assert 'Not transcribed yet' in details and 'youtube.com ↗' in details


def test_library_filters_by_content_type(tmp_path):
    from intel_sourcerer.library import render_library
    from intel_sourcerer.config import PublisherSource
    from intel_sourcerer.feeds import Article
    from intel_sourcerer.storage import save_article, save_source
    from datetime import datetime, timezone
    database = tmp_path / 'articles.db'
    with connect_database(database) as db:
        youtube.save_video(db, 'aaaaaaaaaaa', 'A talk', 'SANS', 300)
        source = PublisherSource('blog', 'Blog', 'https://blog.example', 'manual')
        save_source(db, source)
        now = datetime.now(timezone.utc)
        save_article(db, source.id, Article('Blog', 'A post', 'https://blog.example/p', 'p', now, None, None, ''),
                     content='Words.', error=None, reason='', discovered_at=now.isoformat())
        page = render_library(db, kind='Video')
        everything = render_library(db)
    assert 'A talk' in page and 'A post' not in page
    assert 'Type: Video' in page and 'aria-label="Clear type filter"' in page
    assert 'name="type" value="Video"' in page
    assert '>Type</summary>' in everything and 'A post' in everything and ' articles</span>' not in everything
