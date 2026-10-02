from datetime import datetime, timezone
import sqlite3
from intel_sourcerer.storage import connect_database,save_source,save_article
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.sessions import save_notes,create_queue,queue_state
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
    db.close()
