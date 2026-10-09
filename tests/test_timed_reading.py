from datetime import datetime, timedelta, timezone
import pytest
from intel_sourcerer.storage import connect_database, save_source, save_article
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.sessions import SessionError, delete_article
from intel_sourcerer.timed_reading import (
    timer_action,
    session_state,
    render_session_stats,
)


def test_active_time_completion_retry_and_cleanup(tmp_path):
    db = connect_database(tmp_path / "test.sqlite3")
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    save_source(db, PublisherSource("test", "Test", "https://example.com", "ai_lab"))
    article = Article(
        "Test", "Example", "https://example.com/article", "id", now, None, None, ""
    )
    save_article(
        db,
        "test",
        article,
        content="word " * 200,
        error=None,
        reason="test",
        discovered_at=now.isoformat(),
    )
    started = timer_action(
        db, dict(action="start", url=article.url, target_seconds=60), now
    )
    with pytest.raises(SessionError):
        timer_action(db, dict(action="start", url=article.url, target_seconds=60), now)
    paused = timer_action(
        db, dict(action="pause", id=started["id"]), now + timedelta(seconds=20)
    )
    assert paused["elapsed_seconds"] == 20
    assert session_state(db, now + timedelta(seconds=90))["elapsed_seconds"] == 20
    timer_action(
        db, dict(action="resume", id=started["id"]), now + timedelta(seconds=90)
    )
    finished = timer_action(
        db, dict(action="finish", id=started["id"]), now + timedelta(seconds=120)
    )
    assert finished["elapsed_seconds"] == 50
    assert finished["estimated_seconds"] == 60
    assert db.execute("SELECT is_read FROM articles").fetchone()[0] == 1
    assert (
        timer_action(db, dict(action="finish", id=started["id"]), now)["id"]
        == started["id"]
    )
    assert db.execute("SELECT count(*) FROM timed_reading_sessions").fetchone()[0] == 1
    assert "240" in render_session_stats(db)
    overtime = timer_action(
        db, dict(action="start", url=article.url, target_seconds=60), now
    )
    result = timer_action(
        db, dict(action="finish", id=overtime["id"]), now + timedelta(seconds=90)
    )
    assert result["elapsed_seconds"] > result["target_seconds"]
    abandoned = timer_action(
        db, dict(action="start", url=article.url, target_seconds=60), now
    )
    timer_action(
        db, dict(action="abandon", id=abandoned["id"]), now + timedelta(seconds=10)
    )
    assert "1 abandoned attempts" in render_session_stats(db)
    delete_article(db, article.url)
    assert db.execute("SELECT count(*) FROM timed_reading_sessions").fetchone()[0] == 0
    db.close()
