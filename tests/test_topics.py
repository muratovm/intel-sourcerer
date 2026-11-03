from datetime import date

import pytest

from intel_sourcerer.sessions import SessionError, clean_notes, delete_article
from intel_sourcerer.storage import connect_database
from intel_sourcerer.topics import (
    add_article,
    article_topics,
    citation_href,
    clean_name,
    create_topic,
    delete_topic,
    dismiss_suggestion,
    open_questions,
    topics_overview,
    list_topics,
    remove_article,
    rename_topic,
    render_topic,
    render_topics,
    save_articles_as_topic,
    save_topic_notes,
    set_status,
    topic_data,
    topic_insights,
)

CVE = '<p>CVE-2026-1234 is the flaw here.</p>'


def save(db, slug, *, source='src', day=1, body=''):
    url = f'https://example.com/{slug}'
    db.execute("INSERT OR IGNORE INTO sources VALUES (?,?,'rss','https://example.com',1)", (source, source.title()))
    db.execute(
        """INSERT INTO articles (url,source_id,title,published_at,summary_html,content_status,relevance_reason,discovered_at,content_text,content_html)
        VALUES (?,?,?,?,'','full_text','','2026-01-01','Body text',?)""",
        (url, source, slug.title(), f'2026-01-{day:02d}', body or None),
    )
    return url


@pytest.fixture
def db(tmp_path):
    connection = connect_database(tmp_path / 'test.sqlite3')
    yield connection
    connection.close()


@pytest.mark.parametrize('raw,expected', [('  PHP2Shell ', 'PHP2Shell'), ('a   b', 'a b')])
def test_names_are_tidied(raw, expected):
    assert clean_name(raw) == expected


@pytest.mark.parametrize('raw', ['', '   ', 'x' * 81, 'a\x00b', None, 5])
def test_bad_names_are_refused(raw):
    with pytest.raises(SessionError):
        clean_name(raw)


def test_a_name_in_any_case_is_one_topic(db):
    first = create_topic(db, 'PHP2Shell')
    second = create_topic(db, 'php2shell')
    assert first['created'] and not second['created'] and first['id'] == second['id']


def test_rename_refuses_a_name_in_use(db):
    first, second = create_topic(db, 'One')['id'], create_topic(db, 'Two')['id']
    with pytest.raises(SessionError):
        rename_topic(db, second, 'ONE')
    assert rename_topic(db, first, 'one')['name'] == 'one'


def test_articles_list_oldest_first_with_counts(db):
    newer, older = save(db, 'newer', day=9), save(db, 'older', source='other', day=2)
    topic = create_topic(db, 'T')['id']
    add_article(db, newer, topic)
    add_article(db, older, topic)
    add_article(db, older, topic)
    assert [item['url'] for item in topic_data(db, topic)['articles']] == [older, newer]
    row = list_topics(db)['topics'][0]
    assert (row['articles'], row['publishers'], row['unread']) == (2, 2, 2)
    remove_article(db, older, topic)
    assert [item['url'] for item in topic_data(db, topic)['articles']] == [newer]


def test_adding_by_name_starts_or_reuses_a_topic(db):
    first, second = save(db, 'first'), save(db, 'second')
    state = add_article(db, first, name='New one')
    assert [item['name'] for item in state['topics']] == ['New one']
    add_article(db, second, name='new ONE')
    assert len(list_topics(db)['topics']) == 1
    assert list_topics(db)['topics'][0]['articles'] == 2


def test_unknown_article_or_topic_is_refused(db):
    url = save(db, 'first')
    with pytest.raises(SessionError):
        add_article(db, 'https://example.com/missing', name='T')
    with pytest.raises(SessionError):
        add_article(db, url, 'missing')
    with pytest.raises(SessionError):
        set_status(db, 'missing', 'closed')
    assert topic_data(db, 'missing') == {'topic': None}


def test_status_must_be_known(db):
    topic = create_topic(db, 'T')['id']
    with pytest.raises(SessionError):
        set_status(db, topic, 'archived')
    assert set_status(db, topic, 'closed') == {'status': 'closed'}


def test_notes_are_cleaned_and_a_stale_revision_is_refused(db):
    topic = create_topic(db, 'T')['id']
    assert save_topic_notes(db, topic, '<p onclick="x()">Hi</p><script>bad()</script>', 0) == {'revision': 1}
    notes = topic_data(db, topic)['topic']['notes']
    assert 'onclick' not in notes and '<script' not in notes and 'Hi' in notes
    with pytest.raises(SessionError):
        save_topic_notes(db, topic, 'other', 0)


@pytest.mark.parametrize('href', ['javascript:alert(1)', '//evil.example/', '/settings', '/?other=1'])
def test_only_article_citations_pass_as_local_links(href):
    assert 'href' not in clean_notes(f'<a href="{href}">x</a>')


def test_citations_survive_saving(db):
    url = save(db, 'first')
    href = citation_href(url)
    assert f'href="{href}"' in clean_notes(f'<a href="{href}">x</a>')


def test_deleting_an_article_leaves_the_notes_and_unlinks_its_citations(db):
    gone, kept = save(db, 'gone'), save(db, 'kept')
    topic = create_topic(db, 'T')['id']
    add_article(db, gone, topic)
    add_article(db, kept, topic)
    save_topic_notes(db, topic, f'<p>See <a href="{citation_href(gone)}">Gone, Jan 1</a> and <a href="{citation_href(kept)}">Kept, Jan 1</a></p>', 0)
    delete_article(db, gone)
    db.commit()
    data = topic_data(db, topic)
    assert [item['url'] for item in data['articles']] == [kept]
    notes = data['topic']['notes']
    assert 'Gone, Jan 1' in notes and citation_href(gone) not in notes and citation_href(kept) in notes
    assert data['topic']['notes_revision'] == 2


def test_deleting_a_topic_keeps_its_articles(db):
    url = save(db, 'first')
    topic = create_topic(db, 'T')['id']
    add_article(db, url, topic)
    db.commit()
    delete_topic(db, topic)
    db.commit()
    assert db.execute('SELECT count(*) FROM topic_articles').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM articles').fetchone()[0] == 1


def test_disabled_sources_are_left_out(db):
    shown, hidden = save(db, 'shown'), save(db, 'hidden', source='off')
    topic = create_topic(db, 'T')['id']
    add_article(db, shown, topic)
    add_article(db, hidden, topic)
    db.execute("UPDATE sources SET enabled=0 WHERE id='off'")
    assert [item['url'] for item in topic_data(db, topic)['articles']] == [shown]
    assert list_topics(db)['topics'][0]['articles'] == 1


def test_insights_count_indicators_and_suggest_outside_articles(db):
    one, two = save(db, 'one', body=CVE), save(db, 'two', source='other', body=CVE)
    outside, unrelated = save(db, 'outside', source='third', body=CVE), save(db, 'unrelated', body='<p>Nothing.</p>')
    topic = create_topic(db, 'T')['id']
    add_article(db, one, topic)
    add_article(db, two, topic)
    data = topic_insights(db, topic)
    assert data['complete']
    assert data['indicators'] == [{'kind': 'CVE', 'value': 'CVE-2026-1234', 'articles': 2}]
    assert [item['url'] for item in data['suggested']] == [outside]
    assert data['suggested'][0]['why'] == 'Shares CVE-2026-1234 with 2 articles'
    assert unrelated not in [item['url'] for item in data['suggested']]


def test_reader_suggests_topics_that_share_an_indicator(db):
    member, reading = save(db, 'member', body=CVE), save(db, 'reading', body=CVE)
    tracked = create_topic(db, 'Tracked')['id']
    closed = create_topic(db, 'Closed one')['id']
    add_article(db, member, tracked)
    add_article(db, member, closed)
    set_status(db, closed, 'closed')
    topic_insights(db, tracked)
    state = article_topics(db, reading)
    assert [item['name'] for item in state['suggestions']] == ['Tracked']
    assert state['suggestions'][0]['value'] == 'CVE-2026-1234'
    add_article(db, reading, tracked)
    state = article_topics(db, reading)
    assert state['suggestions'] == [] and [item['name'] for item in state['topics']] == ['Tracked']
    assert state['names'] == ['Closed one']


def test_list_page_filters_searches_and_escapes(db):
    open_topic = create_topic(db, '<b>Open</b>')['id']
    save_topic_notes(db, open_topic, '<p>needle in the notes</p>', 0)
    set_status(db, create_topic(db, 'Done')['id'], 'closed')
    rows = list_topics(db)['topics']
    page = render_topics(rows)
    assert '&lt;b&gt;Open&lt;/b&gt;' in page and '<b>Open' not in page and 'Done' not in page
    assert 'Done' in render_topics(rows, status='closed')
    assert 'Open' in render_topics(rows, 'NEEDLE') and 'No topics match' in render_topics(rows, 'absent')


def test_topic_page_renders_and_escapes(db):
    db.execute("INSERT INTO sources VALUES ('src','Src','rss','https://example.com',1)")
    url = save(db, 'first')
    db.execute("UPDATE articles SET title='<i>Title</i>' WHERE url=?", (url,))
    topic = create_topic(db, 'T "quoted"')['id']
    add_article(db, url, topic)
    page = render_topic(topic_data(db, topic), 'csrf')
    assert '<i>Title' not in page and '&lt;i&gt;Title' in page
    assert 'value="T &quot;quoted&quot;"' in page and 'content="csrf"' in page
    assert 'Topic not found' in render_topic({'topic': None})


def test_saving_a_queue_makes_a_topic_and_reuses_a_name(db):
    first, second = save(db, 'first'), save(db, 'second')
    made = save_articles_as_topic(db, 'New topic', [first])
    assert made['created'] and made['added'] == 1 and made['name'] == 'New topic'
    again = save_articles_as_topic(db, 'NEW TOPIC', [first, second])
    assert again['id'] == made['id'] and not again['created'] and again['added'] == 1 and again['name'] == 'New topic'
    assert list_topics(db)['topics'][0]['urls'] == [first, second]


@pytest.mark.parametrize('urls', [[], None, 'x', ['https://example.com/missing'], [5]])
def test_saving_bad_article_lists_is_refused_and_starts_no_topic(db, urls):
    with pytest.raises(SessionError):
        save_articles_as_topic(db, 'T', urls)
    assert list_topics(db)['topics'] == []


def test_dismissed_suggestions_stay_gone_for_that_topic_only(db):
    member, outside = save(db, 'member', body=CVE), save(db, 'outside', body=CVE)
    one, two = create_topic(db, 'One')['id'], create_topic(db, 'Two')['id']
    add_article(db, member, one)
    add_article(db, member, two)
    dismiss_suggestion(db, one, outside)
    assert topic_insights(db, one)['suggested'] == []
    assert [item['url'] for item in topic_insights(db, two)['suggested']] == [outside]
    assert [item['name'] for item in article_topics(db, outside)['suggestions']] == ['Two']
    delete_article(db, outside)
    db.commit()
    assert db.execute('SELECT count(*) FROM topic_dismissed').fetchone()[0] == 0


@pytest.mark.parametrize('notes,expected', [
    ('', 0),
    ('<p>None</p>', 0),
    ('<ul class="checklist"><li>Open</li><li data-checked="true">Done</li><li> </li></ul>', 1),
    ('<ul><li>Plain bullet</li></ul>', 0),
])
def test_open_questions_count_unticked_items(notes, expected):
    assert open_questions(notes) == expected


def test_overview_counts_tracked_topics_only(db):
    today = date(2026, 1, 28)
    one = save(db, 'one', day=26, body=CVE)
    two = save(db, 'two', source='other', day=20, body=CVE)
    outside = save(db, 'outside', source='third', day=5, body=CVE)
    closed_only = save(db, 'closed', day=6)
    first, second = create_topic(db, 'First')['id'], create_topic(db, 'Second')['id']
    closed = create_topic(db, 'Closed')['id']
    add_article(db, one, first)
    add_article(db, two, first)
    add_article(db, one, second)
    add_article(db, closed_only, closed)
    set_status(db, closed, 'closed')
    save_topic_notes(db, first, '<ul class="checklist"><li>Why?</li></ul>', 0)
    data = topics_overview(db, today)
    assert data['complete']
    assert (data['topics'], data['articles'], data['unread'], data['open_questions']) == (2, 2, 2, 1)
    assert data['belong'] == {first: 1, second: 2}
    assert data['might_belong'] == 3
    assert len(data['weeks']) == 12 and data['weeks_total'] == 2
    assert data['weeks'][-1] == {'start': 'Jan 26, 2026', 'reports': 1}
    assert data['weeks'][-2] == {'start': 'Jan 19, 2026', 'reports': 1}
    assert [(item['name'], item['reports']) for item in data['busiest']] == [('First', 2), ('Second', 1)]
    assert data['shared'][0]['value'] == 'CVE-2026-1234'
    assert [topic['name'] for topic in data['shared'][0]['topics']] == ['First', 'Second']
    assert [item['name'] for item in data['single_publisher']] == ['Second']
    assert outside not in str(data['shared'])


def test_a_shared_technique_alone_does_not_link_topics(db):
    body = '<p>Uses T1505.003 here.</p>'
    one, two = save(db, 'one', body=body), save(db, 'two', body=body)
    first, second = create_topic(db, 'First')['id'], create_topic(db, 'Second')['id']
    add_article(db, one, first)
    add_article(db, two, second)
    assert topics_overview(db)['shared'] == []


def test_overview_with_no_topics_is_empty(db):
    data = topics_overview(db)
    assert data['articles'] == 0 and data['shared'] == [] and data['weeks_total'] == 0


def test_list_sorts_waiting_first_and_searches_indicators(db):
    quiet = save(db, 'quiet', day=20, body=CVE)
    waiting = save(db, 'waiting', day=2)
    db.execute('UPDATE articles SET is_read=1 WHERE url=?', (quiet,))
    a, b = create_topic(db, 'Quiet newest')['id'], create_topic(db, 'Waiting older')['id']
    add_article(db, quiet, a)
    add_article(db, waiting, b)
    topic_insights(db, a)
    rows = list_topics(db)['topics']
    page = render_topics(rows)
    assert page.index('Waiting older') < page.index('Quiet newest')
    assert '1 unread' in page and 'Jan 20, 2026' in page
    found = render_topics(rows, 'cve-2026-1234')
    assert 'Quiet newest' in found and 'Waiting older' not in found
