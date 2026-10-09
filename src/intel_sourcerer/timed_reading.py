"""Timed reading-and-annotation sessions; paused time is excluded."""

from datetime import datetime, timezone
import math
import uuid
from .sessions import SessionError
from .content import reading_word_count


def session_state(db, now=None):
    now = now or datetime.now(timezone.utc)
    row = db.execute(
        "SELECT * FROM timed_reading_sessions WHERE status IN ('running','paused') ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    elapsed = row["elapsed_seconds"]
    if row["status"] == "running":
        elapsed += max(
            0, (now - datetime.fromisoformat(row["resumed_at"])).total_seconds()
        )
    result["elapsed_seconds"] = elapsed
    return result


def timer_action(db, data, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    action = data.get("action")
    current = session_state(db, now)
    if action == "start":
        target = data.get("target_seconds")
        if type(target) is not int or not 30 <= target <= 14400:
            raise SessionError("Choose a target between 0.5 and 240 minutes.")
        article = db.execute(
            "SELECT title,content_text,content_markdown,content_format FROM articles WHERE url=?",
            (data.get("url"),),
        ).fetchone()
        if not article:
            raise SessionError("Article not found.")
        if article["content_format"] in {"audio", "video"}:
            raise SessionError(
                "Use playback controls for audio/video. Word-based reading pace applies to text content."
            )
        if current:
            raise SessionError(
                "Pause and end your current timer before starting another."
            )
        words = reading_word_count(
            article["content_markdown"], article["content_text"] or ""
        )
        if not words:
            raise SessionError("This article has no saved text to time.")
        db.execute(
            "INSERT INTO timed_reading_sessions(id,article_url,title,word_count,target_seconds,estimated_seconds,started_at,resumed_at,status) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                data["url"],
                article["title"],
                words,
                target,
                max(1, math.ceil(words / 200)) * 60,
                stamp,
                stamp,
                "running",
            ),
        )
        return session_state(db, now)
    if not current or data.get("id") != current["id"]:
        # Finish retries must not duplicate a completed session.
        row = db.execute(
            "SELECT * FROM timed_reading_sessions WHERE id=? AND status='completed'",
            (data.get("id"),),
        ).fetchone()
        if action == "finish" and row:
            return dict(row)
        raise SessionError("This timer has changed. Refresh and try again.")
    if action not in {"pause", "resume", "finish", "abandon"}:
        raise SessionError("Unknown timer action.")
    elapsed = current["elapsed_seconds"]
    if action == "resume":
        if current["status"] != "paused":
            return current
        db.execute(
            "UPDATE timed_reading_sessions SET status='running',resumed_at=? WHERE id=?",
            (stamp, current["id"]),
        )
    else:
        status = {"pause": "paused", "finish": "completed", "abandon": "abandoned"}[
            action
        ]
        db.execute(
            "UPDATE timed_reading_sessions SET status=?,elapsed_seconds=?,resumed_at=NULL,finished_at=? WHERE id=?",
            (status, elapsed, stamp if status != "paused" else None, current["id"]),
        )
        if action == "finish":
            db.execute(
                "UPDATE articles SET is_read=1 WHERE url=?", (current["article_url"],)
            )
    return dict(
        db.execute(
            "SELECT * FROM timed_reading_sessions WHERE id=?", (current["id"],)
        ).fetchone()
    )


def render_session_stats(db):
    """Aggregate completed sessions using weighted words / active minutes."""
    data = session_stats_data(db)
    return render_session_stats_data(data["sessions"], data["abandoned"])


def session_stats_data(db):
    return {
        "sessions": [
            dict(row)
            for row in db.execute(
                "SELECT * FROM timed_reading_sessions WHERE status='completed' AND elapsed_seconds>0 ORDER BY finished_at DESC"
            )
        ],
        "abandoned": db.execute(
            "SELECT count(*) FROM timed_reading_sessions WHERE status='abandoned'"
        ).fetchone()[0],
    }


def render_session_stats_data(rows, attempts):
    """Render timing statistics from a serialized backend result."""
    from collections import defaultdict
    from html import escape
    from zoneinfo import ZoneInfo
    from .templates import render_template

    if not rows:
        return render_template("stats/timer-empty.html", abandoned=attempts)
    within = sum(row["elapsed_seconds"] <= row["target_seconds"] for row in rows)
    estimate_within = sum(
        row["elapsed_seconds"] <= row["estimated_seconds"] for row in rows
    )
    pace = round(
        sum(row["word_count"] for row in rows)
        / (sum(row["elapsed_seconds"] for row in rows) / 60)
    )
    weeks = defaultdict(lambda: [0, 0, 0])
    for row in rows:
        day = (
            datetime.fromisoformat(row["finished_at"])
            .astimezone(ZoneInfo("America/Toronto"))
            .date()
        )
        week = day.fromordinal(day.toordinal() - day.weekday()).isoformat()
        weeks[week][0] += row["word_count"]
        weeks[week][1] += row["elapsed_seconds"]
        weeks[week][2] += 1
    weekly = []
    for week, (words, seconds, count) in sorted(weeks.items())[-12:]:
        weekly.append(
            render_template(
                "stats/timer-week.html",
                week=week,
                pace=round(words / (seconds / 60)),
                count=count,
            )
        )
    history = []
    for row in rows[:20]:
        history.append(
            render_template(
                "stats/timer-row.html",
                title=escape(row["title"]),
                day=escape(row["finished_at"][:10]),
                target=f"{row['target_seconds'] / 60:g}",
                actual=f"{row['elapsed_seconds'] / 60:.1f}",
                pace=round(row["word_count"] / (row["elapsed_seconds"] / 60)),
                result="Within target"
                if row["elapsed_seconds"] <= row["target_seconds"]
                else "Over target",
            )
        )
    return render_template(
        "stats/timer-summary.html",
        completed=len(rows),
        within=round(within / len(rows) * 100),
        estimate_within=round(estimate_within / len(rows) * 100),
        pace=pace,
        abandoned=attempts,
        weeks="".join(weekly),
        history="".join(history),
    )
