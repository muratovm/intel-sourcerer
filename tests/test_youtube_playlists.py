import pytest

from intel_sourcerer import youtube
from intel_sourcerer.storage import connect_database


PLAYLIST = {'title': 'Threat  Hunting\nTalks', 'videos': [
    {'id': 'aaaaaaaaaaa', 'title': 'First talk', 'author': 'SANS'},
    {'id': 'bbbbbbbbbbb', 'title': 'Second talk', 'author': 'SANS'},
]}


def test_playlist_links_are_recognised():
    assert youtube.playlist_id('https://www.youtube.com/playlist?list=PLfouvuAjspTrreLxdVrUYE_GFSbxSyZMg') == 'PLfouvuAjspTrreLxdVrUYE_GFSbxSyZMg'
    # A video inside a playlist is still one video.
    assert youtube.playlist_id('https://www.youtube.com/watch?v=aaaaaaaaaaa&list=PLfouvuAjspTrreLxdVrUYE') is None
    assert youtube.playlist_id('https://example.com/playlist?list=PLfouvuAjspTrreLxdVrUYE') is None
    with pytest.raises(ValueError):
        youtube.playlist_id('https://www.youtube.com/playlist?list=--exec')


def test_listing_rejects_anything_but_a_playlist_id():
    with pytest.raises(ValueError, match='Not a YouTube playlist ID'):
        youtube.list_playlist('x; rm -rf /')


def test_importing_a_playlist_makes_a_topic_and_reimport_adds_only_new_videos(tmp_path):
    database = tmp_path / 'articles.db'
    connect_database(database).close()
    first = youtube.import_playlist(database, 'PLfouvuAjspTrreLxdVrUYE', lister=lambda _: PLAYLIST)
    assert first['name'] == 'Threat Hunting Talks' and first['videos'] == 2 and first['added'] == 2
    longer = {**PLAYLIST, 'videos': PLAYLIST['videos'] + [{'id': 'ccccccccccc', 'title': 'Third', 'author': 'SANS'}]}
    again = youtube.import_playlist(database, 'PLfouvuAjspTrreLxdVrUYE', lister=lambda _: longer)
    assert again['topic'] == first['topic'] and again['added'] == 1
    with connect_database(database) as db:
        rows = db.execute("SELECT title, content_format FROM articles ORDER BY title").fetchall()
        assert [tuple(r) for r in rows] == [('First talk', 'video'), ('Second talk', 'video'), ('Third', 'video')]
        assert db.execute('SELECT count(*) FROM topic_articles WHERE topic_id=?', (first['topic'],)).fetchone()[0] == 3


def test_empty_playlists_are_refused(tmp_path):
    database = tmp_path / 'articles.db'
    connect_database(database).close()
    with pytest.raises(ValueError, match='no videos'):
        youtube.import_playlist(database, 'PLfouvuAjspTrreLxdVrUYE', lister=lambda _: {'title': 'x', 'videos': []})


def test_a_playlist_can_go_into_an_existing_topic(tmp_path):
    from intel_sourcerer.topics import create_topic
    database = tmp_path / 'articles.db'
    with connect_database(database) as db:
        topic = create_topic(db, 'Detection Engineering')['id']
    result = youtube.import_playlist(database, 'PLfouvuAjspTrreLxdVrUYE', lister=lambda _: PLAYLIST, topic_id=topic)
    assert result['topic'] == topic and result['name'] == 'Detection Engineering' and result['added'] == 2
    with pytest.raises(ValueError, match='Topic not found'):
        youtube.import_playlist(database, 'PLfouvuAjspTrreLxdVrUYE', lister=lambda _: PLAYLIST, topic_id='missing')
