from datetime import datetime, timezone
from intel_sourcerer.storage import connect_database, save_source, save_article
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.stats import render_stats


def test_first_read_history_and_toronto_date(tmp_path):
    db=connect_database(tmp_path/'stats.db')
    source=PublisherSource('test','Test','https://example.com','ai_lab')
    save_source(db,source)
    article=Article('Test','Report','https://example.com/report','1',datetime.now(timezone.utc),None,None,'')
    save_article(db,source.id,article,content='Body',error=None,reason='test',discovered_at='today')
    db.execute('UPDATE articles SET is_read=1')
    assert db.execute('SELECT count(*) FROM article_reads').fetchone()[0]==1
    db.execute("UPDATE article_reads SET read_at='2026-10-02T02:00:00Z'")
    db.execute('UPDATE articles SET is_read=0')
    db.execute('UPDATE articles SET is_read=1')
    page=render_stats(db,datetime(2026,10,2,12,tzinfo=timezone.utc))
    assert '2026-10-01: 1 article read' in page
    assert '1 articles read · 1 active days' in page
    assert db.execute('SELECT count(*) FROM article_reads').fetchone()[0]==1
