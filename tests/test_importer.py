from dataclasses import replace
from datetime import datetime, timezone
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.importer import listing_articles, relevance_reason, in_window
from intel_sourcerer.storage import connect_database, save_article, save_source

SOURCE=PublisherSource('anthropic','Anthropic','https://www.anthropic.com/threat-intelligence','ai_lab')


def test_listing_dates_and_navigation():
    html='''<a href="/news/report"><h2>Cyber report</h2><time>Sep 10, 2026</time></a><a href="/news/unknown">No date</a><a href="https://elsewhere.com/report"><time>Sep 10, 2026</time></a>'''
    articles=listing_articles(html,SOURCE,SOURCE.parent_url)
    assert len(articles)==1
    assert articles[0].url=='https://www.anthropic.com/news/report'
    assert articles[0].published_at==datetime(2026,9,10,4,tzinfo=timezone.utc)


def test_relevance_excludes_incidental_security():
    a=Article('OpenAI','DevDay 2026 Recap','https://example.com/a','a',None,None,None,'Cybersecurity announcements')
    assert relevance_reason(a) is None
    assert relevance_reason(replace(a,title='Disrupting malicious activity'))
    assert relevance_reason(replace(a,title='Retail productivity',summary_html='')) is None


def test_idempotent_import_preserves_good_content_after_fetch_failure(tmp_path):
    db=connect_database(tmp_path/'articles.sqlite3')
    save_source(db,SOURCE)
    a=Article('Anthropic','Cyber report','https://example.com/a','a',datetime(2026,9,10,tzinfo=timezone.utc),None,None,'Summary')
    assert save_article(db,SOURCE.id,a,content='Full text',error=None,reason='cyber',discovered_at='first')
    assert not save_article(db,SOURCE.id,a,content=None,error='403',reason='cyber',discovered_at='second')
    row=db.execute('SELECT * FROM articles').fetchone()
    assert row['content_text']=='Full text'
    assert row['content_status']=='full_text'
    assert row['discovered_at']=='first'
    assert db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]==1
    db.close()


def test_date_window_excludes_old_future_and_undated_articles():
    since=datetime(2026,9,1,4,tzinfo=timezone.utc)
    until=datetime(2026,10,1,23,tzinfo=timezone.utc)
    a=Article('OpenAI','Cyber report','https://example.com/a','a',since,None,None,'')
    assert in_window(a,since,until)
    assert in_window(replace(a,published_at=until),since,until)
    assert not in_window(replace(a,published_at=None),since,until)
    assert not in_window(replace(a,published_at=datetime(2026,8,31,tzinfo=timezone.utc)),since,until)
    assert not in_window(replace(a,published_at=datetime(2026,10,2,tzinfo=timezone.utc)),since,until)


def test_read_status_defaults_unread_and_survives_reimport(tmp_path):
    db=connect_database(tmp_path/'articles.sqlite3')
    save_source(db,SOURCE)
    a=Article('Anthropic','Cyber report','https://example.com/read','read',datetime(2026,9,10,tzinfo=timezone.utc),None,None,'Summary')
    args=dict(content='Full text',error=None,reason='cyber',discovered_at='first')
    save_article(db,SOURCE.id,a,**args)
    assert db.execute('SELECT is_read FROM articles').fetchone()[0]==0
    db.execute('UPDATE articles SET is_read=1 WHERE url=?',(a.url,))
    save_article(db,SOURCE.id,a,**args)
    assert db.execute('SELECT is_read FROM articles').fetchone()[0]==1
    db.close()


def test_import_attempts_every_enabled_source_type(tmp_path,monkeypatch):
    import asyncio
    import intel_sourcerer.importer as importer
    from intel_sourcerer.feeds import FeedResult
    sources=[PublisherSource('vendor','Vendor','https://example.com','security_vendor','https://example.com/rss'),PublisherSource('unsupported','Other','https://example.com','government'),PublisherSource('disabled','Disabled','https://example.com','ai_lab',enabled=False)]
    monkeypatch.setattr(importer,'load_sources',lambda _:sources)
    calls=[]
    async def fetch(client,feed):
        calls.append(feed.name)
        return FeedResult(feed,[])
    monkeypatch.setattr(importer,'fetch_feed',fetch)
    report=asyncio.run(importer.import_articles('unused',tmp_path/'all.db',datetime(2026,9,1,tzinfo=timezone.utc),datetime(2026,10,1,tzinfo=timezone.utc)))
    assert calls==['Vendor']
    assert [s['id'] for s in report['sources']]==['vendor','unsupported']
    assert any('unsupported: no listing adapter' in error for error in report['errors'])
