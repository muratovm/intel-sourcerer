import pytest
from datetime import datetime, timezone
from intel_sourcerer.storage import connect_database, save_source, save_article
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.sessions import create_queue, queue_state, save_notes, navigate, SessionError


def test_queue_notes_and_resume(tmp_path):
    path=tmp_path/'reader.sqlite3'
    db=connect_database(path)
    source=PublisherSource('test','Test','https://example.com','ai_lab')
    save_source(db,source)
    for i in range(2):
        article=Article('Test',f'Report {i}',f'https://example.com/{i}',str(i),datetime.now(timezone.utc),None,None,'')
        save_article(db,source.id,article,content='Body',error=None,reason='test',discovered_at='today')
    urls=['https://example.com/1','https://example.com/0']
    with pytest.raises(SessionError):create_queue(db,[urls[0],urls[0]])
    q=create_queue(db,urls)
    assert [i['url'] for i in q['items']]==urls
    assert save_notes(db,urls[0],'My note',0)==1
    with pytest.raises(SessionError):save_notes(db,urls[0],'Old draft',0)
    q=navigate(db,q['id'],0,'next')
    assert q['cursor']==1 and q['read_count']==0
    with pytest.raises(SessionError):navigate(db,q['id'],0,'mark_next')
    q=navigate(db,q['id'],1,'previous')
    q=navigate(db,q['id'],0,'mark_next')
    assert q['read_count']==1
    old_id=q['id']
    replacement=create_queue(db,list(reversed(urls)))
    assert replacement['id']!=old_id and replacement['cursor']==0
    assert [item['url'] for item in replacement['items']]==list(reversed(urls))
    assert replacement['read_count']==1 and replacement['items'][1]['has_notes']
    q=create_queue(db,urls)
    q=navigate(db,q['id'],0,'next')
    db.commit();db.close()
    db=connect_database(path)
    assert queue_state(db)['cursor']==1
    assert db.execute('SELECT notes FROM articles WHERE url=?',(urls[0],)).fetchone()[0]=='My note'
    q=navigate(db,q['id'],1,'mark_next')
    assert q['finished'] and q['read_count']==2
    assert create_queue(db,[urls[0]])['cursor']==0
    db.close()


def test_rich_notes_sanitize_and_preserve_formatting():
    from intel_sourcerer.sessions import clean_notes
    result=clean_notes('<blockquote onclick="bad()"><b style="background-color: yellow">Finding</b><img src=x onerror="bad()"></blockquote>')
    assert '<blockquote>' in result and '<b>Finding</b>' in result and '<mark>' in result
    assert 'onclick' not in result and 'onerror' not in result and '<img' not in result


def test_notes_keep_code_and_safe_links():
    from intel_sourcerer.sessions import clean_notes
    code='<blockquote><pre><code>let a\n  | where b\n</code></pre></blockquote>'
    assert clean_notes(code)==code
    kept=clean_notes('<a href="https://example.com/a?b=1&amp;c=2" onclick="bad()" style="x" class="y">one</a><a href="mailto:a@example.com">two</a>')
    assert 'href="https://example.com/a?b=1&amp;c=2"' in kept and 'href="mailto:a@example.com"' in kept
    assert kept.count('target="_blank"')==2 and kept.count('rel="noopener noreferrer"')==2
    assert 'onclick' not in kept and 'style' not in kept and 'class' not in kept


@pytest.mark.parametrize('href',['javascript:alert(1)',' JaVaScRiPt:alert(1)','java\nscript:alert(1)','data:text/html,x','vbscript:x','#top','/local','//example.com/x',''])
def test_notes_drop_unsafe_links_but_keep_their_text(href):
    from intel_sourcerer.sessions import clean_notes
    assert clean_notes(f'<a href="{href}">label</a>')=='label'


def test_notes_keep_checklists_and_nothing_else():
    from intel_sourcerer.sessions import clean_notes
    kept='<ul class="checklist"><li data-checked="true">Done</li><li>Open</li></ul>'
    assert clean_notes(kept)==kept
    assert clean_notes('<ul class="evil" onclick="bad()"><li data-checked="false" data-x="1" class="y">Item</li></ul>')=='<ul><li>Item</li></ul>'
    assert clean_notes('<p class="checklist" data-checked="true">Text</p>')=='<p>Text</p>'
    assert clean_notes('<li class="checklist">x</li><ul data-checked="true"></ul>')=='<li>x</li><ul></ul>'


def test_export_writes_checklist_boxes(tmp_path):
    from intel_sourcerer.library import export_article
    db=_reading_db(tmp_path)
    save_notes(db,'https://example.com/a','<ul class="checklist"><li data-checked="true">Done task</li><li>Open task</li></ul><ul><li>Plain item</li></ul>',0,'html')
    text=export_article(db,'https://example.com/a')
    assert '[x] Done task' in text and '[ ] Open task' in text
    assert '[ ] Plain' not in text and 'Plain item' in text


def test_note_flag_ignores_empty_editor_markup():
    from intel_sourcerer.sessions import has_notes
    assert not has_notes('  ')
    assert not has_notes('<div><br></div><blockquote>&nbsp;</blockquote>', 'html')
    assert has_notes('<blockquote>Saved quote</blockquote>', 'html')
    assert has_notes('My takeaway')


def _reading_db(tmp_path):
    db=connect_database(tmp_path/'reading.sqlite3')
    save_source(db,PublisherSource('test','Test','https://example.com','ai_lab'))
    for name in ('a','b'):
        save_article(db,'test',Article('Test',name,f'https://example.com/{name}',name,datetime(2026,1,1,tzinfo=timezone.utc),None,None,''),content='words here',error=None,reason='match',discovered_at='2026-11-01T00:00:00+00:00')
    db.commit()
    return db


def test_highlights_round_trip_and_reject_bad_input(tmp_path):
    from intel_sourcerer.reading_state import delete_highlight, list_highlights, save_highlight
    db=_reading_db(tmp_path)
    saved=save_highlight(db,'https://example.com/a','quoted words','before ','  after')
    assert [h['id'] for h in list_highlights(db,'https://example.com/a')]==[saved['id']]
    assert list_highlights(db,'https://example.com/a')[0]['prefix']=='before '
    for bad in [('https://example.com/missing','x'),('https://example.com/a','   '),('https://example.com/a','x'*20_001),('https://example.com/a',7)]:
        with pytest.raises(SessionError):save_highlight(db,*bad)
    delete_highlight(db,saved['id'])
    assert list_highlights(db,'https://example.com/a')==[]
    with pytest.raises(SessionError):delete_highlight(db,saved['id'])


def test_position_keeps_latest_and_rejects_out_of_range(tmp_path):
    from intel_sourcerer.reading_state import get_position, save_position
    db=_reading_db(tmp_path)
    assert get_position(db,'https://example.com/a') is None
    save_position(db,'https://example.com/a',0.25)
    save_position(db,'https://example.com/a',0.5)
    assert get_position(db,'https://example.com/a')['fraction']==0.5
    for bad in (-0.1,1.1,'0.5',True,None):
        with pytest.raises(SessionError):save_position(db,'https://example.com/a',bad)


def test_triage_sets_and_clears_decisions(tmp_path):
    from intel_sourcerer.reading_state import set_triage
    db=_reading_db(tmp_path)
    set_triage(db,['https://example.com/a'],'keep')
    set_triage(db,['https://example.com/b'],'skip')
    rows=dict(db.execute('SELECT url,triage FROM articles').fetchall())
    assert rows=={'https://example.com/a':'keep','https://example.com/b':'skip'}
    set_triage(db,['https://example.com/a'],None)
    assert db.execute("SELECT triage FROM articles WHERE url='https://example.com/a'").fetchone()[0] is None
    for bad in (([], 'keep'),(['x'],'maybe'),('https://example.com/a','keep')):
        with pytest.raises(SessionError):set_triage(db,*bad)


def test_week_counts_only_recent_activity(tmp_path):
    from intel_sourcerer.reading_state import week_counts
    db=_reading_db(tmp_path)
    db.execute("UPDATE articles SET discovered_at='2026-01-01T00:00:00+00:00' WHERE url='https://example.com/b'")
    db.execute("UPDATE articles SET is_read=1 WHERE url='https://example.com/a'")
    db.execute("UPDATE article_reads SET read_at='2026-11-02T00:00:00Z'")
    assert week_counts(db,datetime(2026,11,3,tzinfo=timezone.utc))=={'saved':1,'read':1}
    assert week_counts(db,datetime(2027,1,1,tzinfo=timezone.utc))=={'saved':0,'read':0}


def test_delete_article_removes_highlights_and_position(tmp_path):
    from intel_sourcerer.reading_state import get_position, list_highlights, save_highlight, save_position
    from intel_sourcerer.sessions import delete_article
    db=_reading_db(tmp_path)
    save_highlight(db,'https://example.com/a','words')
    save_position(db,'https://example.com/a',0.5)
    delete_article(db,'https://example.com/a')
    assert list_highlights(db,'https://example.com/a')==[] and get_position(db,'https://example.com/a') is None


def test_new_columns_upgrade_an_existing_database(tmp_path):
    path=tmp_path/'old.sqlite3'
    connect_database(path).close()
    old=__import__('sqlite3').connect(path)
    old.execute('ALTER TABLE articles DROP COLUMN triage');old.execute('DROP TABLE article_highlights');old.commit();old.close()
    db=connect_database(path)
    assert 'triage' in {row[1] for row in db.execute('PRAGMA table_info(articles)')}
    assert db.execute('SELECT COUNT(*) FROM article_highlights').fetchone()[0]==0
