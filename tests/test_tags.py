import json

import pytest

from intel_sourcerer.library import export_article, render_library
from intel_sourcerer.sessions import SessionError, delete_article
from intel_sourcerer.storage import connect_database
from intel_sourcerer.tags import add_tag, clean_tag, remove_tag, tag_state


def save(db, slug, *, source='src', day=1):
    url = f'https://example.com/{slug}'
    db.execute("INSERT OR IGNORE INTO sources VALUES (?,?,'rss','https://example.com',1)", (source, source.title()))
    db.execute(
        """INSERT INTO articles (url,source_id,title,published_at,summary_html,content_status,relevance_reason,discovered_at,content_text)
        VALUES (?,?,?,?,'','full_text','','2026-01-01','Body text')""",
        (url, source, slug.title(), f'2026-01-{day:02d}'),
    )
    return url


@pytest.fixture
def db(tmp_path):
    connection = connect_database(tmp_path / 'test.sqlite3')
    yield connection
    connection.close()


@pytest.mark.parametrize('raw,expected', [('  APT29 ', 'APT29'), ('#ransomware', 'ransomware'), ('  a   b ', 'a b'), ('# spaced', 'spaced')])
def test_tags_are_tidied(raw, expected):
    assert clean_tag(raw) == expected


@pytest.mark.parametrize('raw', ['', '   ', '#', 'x' * 41, 'a\x00b', None, 5])
def test_bad_tags_are_refused(raw):
    with pytest.raises(SessionError):
        clean_tag(raw)


def test_unknown_article_is_refused(db):
    with pytest.raises(SessionError):
        add_tag(db, 'https://example.com/missing', 'apt29')
    with pytest.raises(SessionError):
        tag_state(db, 'https://example.com/missing')


def test_case_variants_become_one_tag_and_list_the_other_articles(db):
    first, second, third = save(db, 'first', day=1), save(db, 'second', day=3), save(db, 'third', day=2)
    add_tag(db, first, 'APT29')
    add_tag(db, second, 'apt29')
    state = add_tag(db, third, 'Apt29')
    assert [item['tag'] for item in state['tags']] == ['APT29']
    assert [item['url'] for item in state['tags'][0]['others']] == [second, first]
    assert len(add_tag(db, third, 'apt29')['tags']) == 1


def test_suggestions_rank_by_use_and_leave_out_this_articles_tags(db):
    first, second, third = save(db, 'first'), save(db, 'second'), save(db, 'third')
    add_tag(db, first, 'rare')
    add_tag(db, first, 'common')
    add_tag(db, second, 'common')
    add_tag(db, third, 'mine')
    assert tag_state(db, third)['suggestions'] == ['common', 'rare']


def test_removing_and_deleting_clean_up(db):
    first, second = save(db, 'first'), save(db, 'second')
    add_tag(db, first, 'apt29')
    add_tag(db, second, 'apt29')
    assert remove_tag(db, first, 'APT29')['tags'] == []
    assert remove_tag(db, first, 'never there')['tags'] == []
    add_tag(db, first, 'apt29')
    delete_article(db, second)
    db.commit()
    assert tag_state(db, first)['tags'][0]['others'] == []
    assert db.execute('SELECT count(*) FROM article_tags WHERE article_url=?', (second,)).fetchone()[0] == 0


def test_articles_from_disabled_sources_are_left_out(db):
    first, hidden = save(db, 'first'), save(db, 'hidden', source='off')
    add_tag(db, first, 'apt29')
    add_tag(db, hidden, 'apt29')
    db.execute("UPDATE sources SET enabled=0 WHERE id='off'")
    assert tag_state(db, first)['tags'][0]['others'] == []


def test_library_filters_counts_searches_and_escapes(db):
    first, second = save(db, 'first'), save(db, 'second')
    add_tag(db, first, 'apt29')
    add_tag(db, second, 'APT29')
    add_tag(db, second, '<b>x</b>')
    page = render_library(db, tag='apt29')
    assert 'apt29 <span>2</span>' in page and 'aria-current=true' in page
    assert '<b>x</b>' not in page and '&lt;b&gt;x&lt;/b&gt;' in page
    only = render_library(db, tag='<b>x</b>')
    assert '>Second<' in only and '>First<' not in only
    assert '>First<' not in render_library(db, '<b>x')
    assert '>First<' in render_library(db, 'apt29')
    assert 'No results' in render_library(db, tag='nothing')


def test_library_links_keep_the_tag_and_active_tag_clears(db):
    first = save(db, 'first')
    add_tag(db, first, 'a b')
    page = render_library(db, tag='a b')
    assert 'tag=a+b' in page
    assert 'name="tag" value="a b"' in page
    assert 'class="lib-tag-option active" aria-current=true href="/library?q=&amp;notes=0&amp;tag=&amp;type="' in page
    assert 'Tag: a b' in page and 'aria-label="Clear tag filter"' in page


def test_library_rows_show_two_tags_and_a_count(db):
    first = save(db, 'first')
    for name in ['one', 'three', 'two', '<b>']:
        add_tag(db, first, name)
    page = render_library(db)
    assert '<span class="lib-row-tags"><span class="lib-row-tag">&lt;b&gt;</span><span class="lib-row-tag">one</span><span>+2</span></span>' in page
    assert 'id="lib-tag-editor"' in page


def test_library_with_no_tags_has_no_tag_menu(db):
    save(db, 'first')
    assert 'lib-tag-menu' not in render_library(db)


def test_export_lists_tags(db):
    first = save(db, 'first')
    assert 'Tags:' not in export_article(db, first)
    add_tag(db, first, 'apt29')
    assert 'Tags: apt29\n' in export_article(db, first)


def test_library_rows_carry_tags_as_json(db):
    from intel_sourcerer.library import library_rows

    add_tag(db, save(db, 'first'), 'apt29')
    assert json.loads(library_rows(db)[0]['tags_json']) == ['apt29']
