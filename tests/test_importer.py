from dataclasses import replace
from pathlib import Path
from datetime import datetime, timezone
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.importer import listing_articles, relevance_reason, selection_reason, in_window
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


def test_real_listing_markup_pairs_dates_with_title_links():
    html=(Path(__file__).parent/'fixtures'/'anthropic_listing.html').read_text()
    articles={a.url.replace('https://www.anthropic.com',''):a for a in listing_articles(html,SOURCE,SOURCE.parent_url)}
    assert len(articles)==8
    assert not any(url in articles for url in ['/news/security','/news/announcements','/news/partnerships'])
    assert all(a.title and a.published_at for a in articles.values())
    assert '/threat-intelligence-report-september-2026' in articles
    assert '/news/ai-for-science-expands-foundations-research-institutions' in articles
    row=articles['/news/ai-for-science-expands-foundations-research-institutions']
    assert row.title=='AI for Science expands to foundations and research institutions'
    assert row.published_at==datetime(2026,10,28,4,tzinfo=timezone.utc)


def test_date_beside_a_list_of_titles_is_not_guessed():
    html='<section><time>Oct 1, 2026</time><div><a href="/news/one"><h3>One</h3></a></div><div><a href="/news/two"><h3>Two</h3></a></div></section>'
    assert listing_articles(html,SOURCE,SOURCE.parent_url)==[]


def test_repeated_article_keeps_the_copy_with_a_summary():
    html='<div><time>Oct 1, 2026</time><a href="/news/one"><h3>One</h3></a></div><div><time>Oct 1, 2026</time><a href="/news/one"><h3>One</h3></a><p>Summary</p></div>'
    articles=listing_articles(html,SOURCE,SOURCE.parent_url)
    assert len(articles)==1 and 'Summary' in articles[0].summary_html


def test_frontier_red_team_skips_keyword_filter():
    team=PublisherSource('anthropic-frontier-red-team','Anthropic Frontier Red Team','https://www.anthropic.com/research/team/frontier-red-team','ai_lab')
    a=Article('Anthropic','Claude reveals ECDSA curve origins','https://example.com/a','a',None,None,None,'')
    assert selection_reason(a,team)
    assert selection_reason(a,SOURCE) is None
    assert selection_reason(replace(a,title='Cyber report'),SOURCE)
