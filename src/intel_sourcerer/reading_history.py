"""Count and wipe reading history (reads, Read marks, scroll positions, timer sessions, queues), keeping the library."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from .storage import connect_database

# What a wipe removes, in the order the account page lists it.
PARTS = (
    ("reads", "SELECT COUNT(*) FROM article_reads"),
    ("read_marks", "SELECT COUNT(*) FROM articles WHERE is_read=1"),
    ("positions", "SELECT COUNT(*) FROM reading_positions"),
    ("timer_sessions", "SELECT COUNT(*) FROM timed_reading_sessions"),
    ("queues", "SELECT COUNT(*) FROM reading_queues"),
    ("queue_items", "SELECT COUNT(*) FROM queue_items"),
)


def history_counts(db):
    return {name: db.execute(sql).fetchone()[0] for name, sql in PARTS}


def _plural(n, one, many=None):
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def history_lines(counts):
    """The account page's list of what a wipe deletes, in plain words."""
    return [
        f"{_plural(counts['reads'], 'entry', 'entries')} in your read history",
        f"the Read mark on {_plural(counts['read_marks'], 'article')}",
        f"{_plural(counts['positions'], 'saved scroll position')}, so articles reopen at the top",
        f"{_plural(counts['timer_sessions'], 'timer session')}, which also resets your reading stats and pace",
        f"{_plural(counts['queues'], 'reading queue')} with their {_plural(counts['queue_items'], 'entry', 'entries')}, including your current queue",
    ]


def backup_before_wipe(database):
    """A named copy beside the daily snapshots; daily pruning leaves named copies alone."""
    path = Path(database).resolve()
    folder = path.parent / "backups"
    folder.mkdir(exist_ok=True)
    target = folder / f"{path.stem}-before-history-wipe-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.sqlite3"
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
    return target


def wipe_reading_history(database):
    """Back up the library, then clear reading history in one transaction. Articles, notes, topics, tags stay."""
    backup = backup_before_wipe(database)
    db = connect_database(database)
    try:
        before = history_counts(db)
        with db:
            db.execute("DELETE FROM article_reads")
            db.execute("UPDATE articles SET is_read=0 WHERE is_read=1")
            db.execute("DELETE FROM reading_positions")
            db.execute("DELETE FROM timed_reading_sessions")
            db.execute("DELETE FROM queue_items")
            db.execute("DELETE FROM reading_queues")
        return {"removed": before, "backup": backup.name}
    finally:
        db.close()


def render_history_panel(counts, csrf):
    """Account page section: what a wipe removes, what stays, and a confirmed button."""
    if not any(counts.values()):
        body = '<p class="muted">Nothing to wipe. Your reading history is already empty.</p>'
    else:
        items = "".join(f"<li>{escape(line)}</li>" for line in history_lines(counts))
        body = (
            "<p>Wiping deletes:</p>"
            f'<ul class="history-wipe-list">{items}</ul>'
            '<p class="muted">Your articles, notes, topics, tags, indicators and sources stay. '
            "A backup copy of the library is saved first.</p>"
            '<form method="post" action="/auth/wipe-history" class="history-wipe-form">'
            f'<input type="hidden" name="csrf_token" value="{csrf}" />'
            '<label><input type="checkbox" name="confirm" value="wipe" required /> '
            "I understand this can only be undone from the backup</label>"
            '<button type="submit" class="danger">Wipe reading history</button>'
            "</form>"
        )
    return (
        '<section class="account-panel history-wipe" aria-label="Reading history">'
        f"<h2>Reading history</h2>{body}</section>"
    )
