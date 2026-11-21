from contextlib import closing
from datetime import datetime, timezone

from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.reading_history import history_counts, history_lines, render_history_panel, wipe_reading_history
from intel_sourcerer.sessions import create_queue
from intel_sourcerer.storage import connect_database, save_article, save_source


def _library(path):
    with closing(connect_database(path)) as db, db:
        save_source(db, PublisherSource("s", "Source", "https://s.example", "security_vendor"))
        for name in ("a", "b"):
            url = f"https://s.example/{name}"
            save_article(db, "s", Article("Source", name, url, url, datetime(2026, 11, 1, tzinfo=timezone.utc), None, None, ""),
                         content="Body", error=None, reason="", discovered_at="2026-11-01")
        db.execute("UPDATE articles SET is_read=1, notes='keep me' WHERE url='https://s.example/a'")
        db.execute("INSERT INTO reading_positions VALUES ('https://s.example/a', 0.5, '2026-11-02')")
        create_queue(db, ["https://s.example/a", "https://s.example/b"])


def test_wipe_clears_history_keeps_library_and_backs_up_first(tmp_path):
    path = tmp_path / "lib.sqlite3"
    _library(path)
    with closing(connect_database(path)) as db:
        counts = history_counts(db)
    assert counts == {"reads": 1, "read_marks": 1, "positions": 1, "timer_sessions": 0, "queues": 1, "queue_items": 2}
    assert history_lines(counts)[0] == "1 entry in your read history"
    result = wipe_reading_history(path)
    assert result["removed"] == counts and (tmp_path / "backups" / result["backup"]).is_file()
    with closing(connect_database(path)) as db:
        assert not any(history_counts(db).values())
        assert tuple(db.execute("SELECT count(*), max(notes) FROM articles").fetchone()) == (2, "keep me")
    # The backup still holds the history, so the wipe can be undone from it.
    with closing(connect_database(tmp_path / "backups" / result["backup"])) as copy:
        assert history_counts(copy) == counts


def test_panel_lists_counts_and_needs_confirmation():
    panel = render_history_panel({"reads": 3, "read_marks": 2, "positions": 48, "timer_sessions": 19,
                                  "queues": 151, "queue_items": 231}, "tok")
    assert "48 saved scroll positions" in panel and "151 reading queues with their 231 entries" in panel
    assert 'name="confirm" value="wipe" required' in panel and 'action="/auth/wipe-history"' in panel
    assert "Nothing to wipe" in render_history_panel(dict.fromkeys(("reads", "read_marks", "positions",
                                                                    "timer_sessions", "queues", "queue_items"), 0), "tok")
