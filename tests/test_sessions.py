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


def test_note_flag_ignores_empty_editor_markup():
    from intel_sourcerer.sessions import has_notes
    assert not has_notes('  ')
    assert not has_notes('<div><br></div><blockquote>&nbsp;</blockquote>', 'html')
    assert has_notes('<blockquote>Saved quote</blockquote>', 'html')
    assert has_notes('My takeaway')
