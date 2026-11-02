import asyncio
from datetime import datetime, timezone
import sqlite3
import threading
import time

import pytest

from intel_sourcerer import importer
from intel_sourcerer.auth import Accounts
from intel_sourcerer.backups import backup_database
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import FeedResult
from intel_sourcerer.sessions import session_db
from intel_sourcerer.storage import BUSY_TIMEOUT, connect_database
from intel_sourcerer.web import read_articles


def journal_mode(path):
    with sqlite3.connect(path) as db:
        return db.execute('PRAGMA journal_mode').fetchone()[0]


@pytest.fixture
def path(tmp_path):
    database = tmp_path / 'lib.sqlite3'
    db = connect_database(database)
    db.execute("INSERT INTO sources VALUES ('s','S','manual','http://x',1)")
    db.execute("INSERT INTO articles(url,source_id,title,published_at,summary_html,content_status,relevance_reason,discovered_at) VALUES ('u','s','T','2026-01-01','','full_text','r','t')")
    db.commit()
    db.close()
    return database


def test_databases_use_write_ahead_log(path, tmp_path):
    assert journal_mode(path) == 'wal'
    Accounts(path, tmp_path / 'none.toml')
    assert journal_mode(tmp_path / 'accounts.sqlite3') == 'wal'


def test_existing_database_is_upgraded_and_keeps_its_data(tmp_path):
    path = tmp_path / 'old.sqlite3'
    connect_database(path).close()
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute("INSERT INTO sources VALUES ('s','S','manual','http://x',1)")
    assert journal_mode(path) == 'delete'
    db = connect_database(path)
    assert db.execute('SELECT id FROM sources').fetchone()[0] == 's'
    db.close()
    assert journal_mode(path) == 'wal'


def test_all_connections_wait_for_the_same_time(path):
    db = session_db(path)
    assert db.execute('PRAGMA busy_timeout').fetchone()[0] == BUSY_TIMEOUT * 1000
    db.close()
    db = connect_database(path)
    assert db.execute('PRAGMA busy_timeout').fetchone()[0] == BUSY_TIMEOUT * 1000
    db.close()


def test_reads_continue_during_an_open_write(path):
    writer = session_db(path)
    writer.execute('BEGIN IMMEDIATE')
    writer.execute("UPDATE articles SET notes='unsaved'")
    start = time.monotonic()
    reader = session_db(path)
    reader.execute('PRAGMA busy_timeout=0')
    assert reader.execute('SELECT notes FROM articles').fetchone()[0] == ''
    assert read_articles(path)[0]['notes'] == ''
    assert time.monotonic() - start < 1
    writer.commit()
    assert reader.execute('SELECT notes FROM articles').fetchone()[0] == 'unsaved'
    writer.close()
    reader.close()


def test_a_second_write_waits_instead_of_failing(path):
    first = session_db(path)
    first.execute('BEGIN IMMEDIATE')
    first.execute("UPDATE articles SET notes='first'")
    errors = []

    def second_write():
        db = session_db(path)
        try:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE articles SET notes=notes||' second'")
            db.commit()
        except sqlite3.Error as error:
            errors.append(error)
        finally:
            db.close()

    thread = threading.Thread(target=second_write)
    thread.start()
    time.sleep(0.5)
    first.commit()
    first.close()
    thread.join(5)
    assert not errors
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT notes FROM articles').fetchone()[0] == 'first second'


def test_backup_includes_writes_not_yet_moved_out_of_the_log(path):
    holder = sqlite3.connect(path)
    holder.execute('SELECT * FROM articles').fetchall()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE articles SET notes='only in the log'")
    snapshot = backup_database(path)
    holder.close()
    with sqlite3.connect(snapshot) as db:
        assert db.execute('SELECT notes FROM articles').fetchone()[0] == 'only in the log'


def test_fetching_during_import_leaves_notes_free_to_save(path, monkeypatch):
    source = PublisherSource('s', 'S', 'http://x', 'ai_lab', 'http://x/feed')
    seen = []

    async def fetch(_client, feed):
        # A note save during a network call must not have to wait for the import.
        db = session_db(path)
        db.execute('PRAGMA busy_timeout=0')
        try:
            db.execute("UPDATE articles SET notes='saved during fetch'")
            db.commit()
            seen.append('saved')
        except sqlite3.OperationalError as error:
            seen.append(str(error))
        finally:
            db.close()
        return FeedResult(feed, [])

    monkeypatch.setattr(importer, 'load_sources', lambda _config: [source])
    monkeypatch.setattr(importer, 'fetch_feed', fetch)
    asyncio.run(importer.import_articles('unused', path, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc)))
    assert seen == ['saved']
