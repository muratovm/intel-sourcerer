"""Yearly first-read activity, displayed in Toronto local time."""

from .templates import render_template
from .timed_reading import session_stats_data, render_session_stats_data
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def stats_data(db):
    return {
        "reads": [row[0] for row in db.execute("SELECT read_at FROM article_reads")],
        "timing": session_stats_data(db),
    }


def render_stats(db, now=None):
    return render_stats_data(stats_data(db), now)


def render_stats_data(data, now=None):
    """Build the heatmap from data provided by the backend."""
    zone = ZoneInfo("America/Toronto")
    today = (now or datetime.now(zone)).astimezone(zone).date()
    counts = Counter()
    undated = 0
    for read_at in data["reads"]:
        if not read_at:
            undated += 1
            continue
        day = (
            datetime.fromisoformat(read_at.replace("Z", "+00:00"))
            .astimezone(zone)
            .date()
        )
        if day.year == today.year and day <= today:
            counts[day] += 1
    start = today.replace(month=1, day=1)
    end = start.replace(year=start.year + 1)
    first = start - timedelta(days=start.weekday())
    cells = []
    months = []
    weeks = ((end - first).days + 6) // 7
    day = first
    while day < end or day.weekday() != 0:
        valid = start <= day < end
        future = day > today
        value = counts[day] if valid else 0
        level = (
            0
            if not value
            else 1
            if value == 1
            else 2
            if value <= 3
            else 3
            if value <= 6
            else 4
        )
        label = (
            f"{day.isoformat()}: {value} article"
            + ("" if value == 1 else "s")
            + " read"
        )
        if future:
            label = f"{day.isoformat()}: upcoming day"
        cells.append(
            render_template(
                "stats/day.html",
                level=level,
                day_class=" outside" if not valid else " future" if future else "",
                label=label,
                label2=label,
                tab_index=0 if valid and (not future) else -1,
            )
        )
        if valid and day.day == 1:
            months.append(((day - first).days // 7, day.strftime("%b")))
        day += timedelta(days=1)
    month_cells = []
    for index, (column, label) in enumerate(months):
        next_column = months[index + 1][0] if index + 1 < len(months) else weeks
        month_cells.append(
            render_template(
                "stats/month.html",
                week_span=next_column - column,
                label=label,
            )
        )
    total = sum(counts.values())
    return render_template(
        "stats/index.html",
        today_year=today.year,
        total=total,
        active_days=len(counts),
        month_labels="".join(month_cells),
        today_year2=today.year,
        day_cells="".join(cells),
        undated=undated,
        timed_sessions=render_session_stats_data(
            data["timing"]["sessions"], data["timing"]["abandoned"]
        ),
    )
