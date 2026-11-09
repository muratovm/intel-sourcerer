from datetime import datetime, timezone
import sqlite3
from intel_sourcerer.storage import connect_database,save_source,save_article
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.sessions import save_notes,create_queue,queue_state,delete_article
from intel_sourcerer.library import render_library,export_article
from intel_sourcerer.backups import backup_database


def test_library_export_notes_and_backup(tmp_path):
    path=tmp_path/'articles.sqlite3'
    db=connect_database(path)
    source=PublisherSource('test','Test','https://example.com','ai_lab')
    save_source(db,source)
    article=Article('Test','Example report','https://example.com/report','id',datetime.now(timezone.utc),None,None,'')
    save_article(db,'test',article,content='Unique body phrase',markdown='## Findings\nUnique body phrase',error=None,reason='test',discovered_at='now')
    queue=create_queue(db,[article.url])
    save_notes(db,article.url,'<p>Personal takeaway</p>',0,'html')
    assert db.execute('SELECT notes_updated_at FROM articles').fetchone()[0]
    db.execute('UPDATE sources SET enabled=0');db.commit()
    assert 'Example report' in render_library(db,'unique body')
    assert 'Example report' in render_library(db,'personal',True)
    assert 'No matching articles' in render_library(db,'unmatched')
    page=render_library(db,selected=article.url)
    assert 'aria-label="Notes preview"' in page
    assert '<p>Personal takeaway</p>' in page
    assert '>Notes</a>' not in page
    assert 'aria-current=true' in page
    assert 'Example report' in render_library(db,selected='missing')
    export=export_article(db,article.url)
    assert 'Personal takeaway' in export and '## Findings' in export
    assert queue_state(db)['id']==queue['id']
    snapshot=backup_database(path)
    with sqlite3.connect(snapshot) as restored:
        assert restored.execute('SELECT notes FROM articles').fetchone()[0]=='<p>Personal takeaway</p>'
    assert backup_database(path)==snapshot
    db.execute('PRAGMA foreign_keys=ON')
    delete_article(db,article.url)
    assert not db.execute('SELECT 1 FROM articles WHERE url=?',(article.url,)).fetchone()
    assert queue_state(db)['items']==[]
    assert queue_state(db)['finished']
    assert not db.execute('SELECT 1 FROM article_reads WHERE article_url=?',(article.url,)).fetchone()
    db.close()


def test_library_shows_article_count_and_storage(tmp_path):
    from intel_sourcerer.library import format_size, library_summary, storage_usage
    database = tmp_path / 'articles.db'
    database.write_bytes(b'x' * 2_000_000)
    (tmp_path / 'media').mkdir()
    (tmp_path / 'media' / 'talk.mp4').write_bytes(b'x' * 1_500_000)
    (tmp_path / 'backups').mkdir()
    (tmp_path / 'backups' / 'articles-2026-11-08.sqlite3').write_bytes(b'x' * 3_000_000)
    (tmp_path / 'backups' / 'other-2026-11-08.sqlite3').write_bytes(b'x' * 9_000_000)
    usage = storage_usage(database)
    assert usage == {'stored': 3_500_000, 'backups': 3_000_000}
    assert library_summary(12, usage) == '12 articles · 3.5 MB stored · 3.0 MB in backups'
    assert library_summary(1, None) == '1 article'
    assert format_size(2_400_000_000) == '2.4 GB' and format_size(0) == '0 MB'


def test_backups_keep_only_the_newest_daily_copies(tmp_path):
    from intel_sourcerer.backups import prune_backups
    database = tmp_path / 'articles.db'
    folder = tmp_path / 'backups'; folder.mkdir()
    for day in ['2026-11-01', '2026-11-02', '2026-11-03', '2026-11-08', '2026-11-09']:
        (folder / f'articles-{day}.sqlite3').write_bytes(b'x')
    (folder / 'articles-before-cleanup-2026-11-01.sqlite3').write_bytes(b'x')
    (folder / 'other-2026-10-01.sqlite3').write_bytes(b'x')
    prune_backups(database)
    assert sorted(f.name for f in folder.iterdir()) == [
        'articles-2026-11-03.sqlite3', 'articles-2026-11-08.sqlite3', 'articles-2026-11-09.sqlite3',
        'articles-before-cleanup-2026-11-01.sqlite3', 'other-2026-10-01.sqlite3']
