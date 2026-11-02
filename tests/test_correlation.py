import pytest

from intel_sourcerer.correlation import INDEX_VERSION, indicators_for_index, refresh_index, related_reports
from intel_sourcerer.sessions import delete_article
from intel_sourcerer.storage import connect_database

HASH = '9f1f11a708d393e0a4109ae189bc64f1f3e312653dcf317a2bd406f18ffcc507'


def save(db, slug, html, *, day=1, source='src'):
    url = f'https://example.com/{slug}'
    db.execute("INSERT OR IGNORE INTO sources VALUES (?,?,'rss','https://example.com',1)", (source, source.title()))
    db.execute(
        """INSERT INTO articles (url,source_id,title,published_at,summary_html,content_status,relevance_reason,discovered_at,content_html,content_markdown)
        VALUES (?,?,?,?,'','full_text','','2026-01-01',?,'x') ON CONFLICT(url) DO UPDATE SET content_html=excluded.content_html""",
        (url, source, slug.title(), f'2026-01-{day:02d}', html),
    )
    db.commit()
    return url


@pytest.fixture
def db(tmp_path):
    connection = connect_database(tmp_path / 'test.sqlite3')
    yield connection
    connection.close()


def test_common_tools_stay_out_and_file_case_is_ignored():
    pairs = indicators_for_index('<p>Loader.EXE ran cmd.exe</p>')
    assert pairs == {('files', 'loader.exe')}


def test_shared_values_link_reports_in_both_directions(db):
    first = save(db, 'first', f'<p>{HASH} and CVE-2026-1234</p>')
    second = save(db, 'second', f'<p>{HASH.upper()}</p>')
    save(db, 'unrelated', '<p>CVE-2020-9999</p>')
    forward, backward = related_reports(db, first), related_reports(db, second)
    assert forward['complete'] and forward['related'] == [second]
    assert forward['values'] == {'hashes': {HASH: [second]}}
    assert backward['related'] == [first]
    assert forward['reports'][0]['shared']['hashes'] == [HASH]


def test_one_shared_technique_is_shown_on_the_value_but_is_not_a_related_report(db):
    first = save(db, 'first', '<p>T1059 T1105 T1082</p>')
    weak = save(db, 'weak', '<p>T1059</p>')
    strong = save(db, 'strong', '<p>T1059 T1105 T1082</p>')
    data = related_reports(db, first)
    assert data['related'] == [strong]
    assert set(data['values']['techniques']['T1059']) == {weak, strong}


def test_strongest_evidence_comes_first(db):
    current = save(db, 'current', f'<p>{HASH} CVE-2026-1234 CVE-2026-5678 loader.exe</p>')
    newer_cves = save(db, 'cves', '<p>CVE-2026-1234 CVE-2026-5678</p>', day=20)
    older_hash = save(db, 'hash', f'<p>{HASH}</p>', day=2)
    file_only = save(db, 'file', '<p>loader.exe</p>', day=25)
    assert related_reports(db, current)['related'] == [older_hash, newer_cves, file_only]


def test_changing_an_article_drops_its_old_links(db):
    first = save(db, 'first', '<p>CVE-2026-1234</p>')
    second = save(db, 'second', '<p>CVE-2026-1234</p>')
    assert related_reports(db, first)['related'] == [second]
    save(db, 'second', '<p>nothing here now</p>')
    assert related_reports(db, first)['related'] == []


def test_saving_identical_text_keeps_the_index(db):
    url = save(db, 'first', '<p>CVE-2026-1234</p>')
    refresh_index(db)
    save(db, 'first', '<p>CVE-2026-1234</p>')
    assert db.execute('SELECT version FROM indicator_index_state WHERE article_url=?', (url,)).fetchone()[0] == INDEX_VERSION


def test_deleting_an_article_removes_it_from_the_index(db):
    first = save(db, 'first', '<p>CVE-2026-1234</p>')
    second = save(db, 'second', '<p>CVE-2026-1234</p>')
    related_reports(db, first)
    delete_article(db, second)
    db.commit()
    assert related_reports(db, first)['related'] == []
    assert db.execute('SELECT count(*) FROM article_indicators WHERE article_url=?', (second,)).fetchone()[0] == 0


def test_an_older_index_version_is_read_again(db):
    url = save(db, 'first', '<p>CVE-2026-1234</p>')
    refresh_index(db)
    db.execute('UPDATE indicator_index_state SET version=?', (INDEX_VERSION - 1,))
    db.execute("DELETE FROM article_indicators")
    db.commit()
    assert refresh_index(db) == 0
    assert db.execute('SELECT value FROM article_indicators WHERE article_url=?', (url,)).fetchone()[0] == 'CVE-2026-1234'


def test_running_out_of_time_reports_the_rest_and_indexes_the_open_article_first(db):
    save(db, 'newest', '<p>CVE-2026-1234</p>', day=28)
    wanted = save(db, 'wanted', '<p>CVE-2026-1234</p>', day=1)
    ticks = iter([0, 0, 99, 99])
    assert refresh_index(db, wanted, budget=1, clock=lambda: next(ticks)) == 1
    assert [row[0] for row in db.execute('SELECT article_url FROM indicator_index_state')] == [wanted]


def test_disabled_sources_are_not_linked(db):
    first = save(db, 'first', '<p>CVE-2026-1234</p>')
    save(db, 'hidden', '<p>CVE-2026-1234</p>', source='off')
    db.execute("UPDATE sources SET enabled=0 WHERE id='off'")
    db.commit()
    assert related_reports(db, first)['related'] == []


def test_the_related_route_answers_and_rejects_unknown_articles(tmp_path):
    import threading

    import httpx

    from intel_sourcerer.web import create_server

    database = tmp_path / 'test.sqlite3'
    db = connect_database(database)
    first = save(db, 'first', '<p>CVE-2026-1234</p>')
    second = save(db, 'second', '<p>CVE-2026-1234</p>')
    db.close()
    config = tmp_path / 'sources.toml'
    config.write_text('')
    server = create_server(database, 0, config, auth_enabled=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as client:
            found = client.get('/api/related', params={'url': first})
            assert found.status_code == 200 and found.json()['related'] == [second]
            assert client.get('/api/related', params={'url': 'https://example.com/missing'}).status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_other_publishers_come_before_stronger_matches_from_the_same_one(db):
    current = save(db, 'current', f'<p>{HASH} CVE-2026-1234</p>')
    same = save(db, 'same', f'<p>{HASH} CVE-2026-1234</p>', day=20)
    other = save(db, 'other', '<p>CVE-2026-1234</p>', day=2, source='elsewhere')
    data = related_reports(db, current)
    assert data['related'] == [other, same]
    assert data['values']['cves']['CVE-2026-1234'] == [other, same]
    assert [report['other_publisher'] for report in data['reports']] == [True, False]
