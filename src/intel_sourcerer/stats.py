"""Yearly first-read activity, displayed in Toronto local time."""
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def render_stats(db, now=None):
    zone=ZoneInfo('America/Toronto')
    today=(now or datetime.now(zone)).astimezone(zone).date()
    counts=Counter()
    undated=0
    for row in db.execute('SELECT read_at FROM article_reads'):
        if not row[0]:
            undated+=1
            continue
        day=datetime.fromisoformat(row[0].replace('Z','+00:00')).astimezone(zone).date()
        if day.year==today.year and day<=today:
            counts[day]+=1
    start=today.replace(month=1,day=1)
    end=start.replace(year=start.year+1)
    first=start-timedelta(days=start.weekday())
    cells=[]
    months=[]
    weeks=((end-first).days+6)//7
    day=first
    while day<end or day.weekday()!=0:
        valid=start<=day<end
        future=day>today
        value=counts[day] if valid else 0
        level=0 if not value else 1 if value==1 else 2 if value<=3 else 3 if value<=6 else 4
        label=f'{day.isoformat()}: {value} article'+('' if value==1 else 's')+' read'
        if future:label=f'{day.isoformat()}: upcoming day'
        cells.append(f'<span class="heat-day level-{level}{" outside" if not valid else " future" if future else ""}" title="{label}" aria-label="{label}" tabindex="{0 if valid and not future else -1}"></span>')
        if valid and day.day==1:
            months.append(((day-first).days//7,day.strftime("%b")))
        day+=timedelta(days=1)
    month_cells=[]
    for index,(column,label) in enumerate(months):
        next_column=months[index+1][0] if index+1<len(months) else weeks
        month_cells.append(f'<span class="month-span-{next_column-column}">{label}</span>')
    total=sum(counts.values())
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Reading stats · Intel Sourcerer</title><link rel="stylesheet" href="/reader.css"></head><body><main class="stats-page"><header class="builder-header"><div class="brand"><img class="brand-icon" src="/brand-icon.png" alt="">Intel Sourcerer</div><nav class="builder-links" aria-label="Main navigation"><a href="/">Home</a><a href="/?view=builder">Queue builder</a><a href="/library">Library</a><a href="/sources">Sources</a><a href="/stats" class="active" aria-current="page">Stats</a></nav></header><div class="stats-content"><h1>Your reading year · {today.year}</h1><p class="muted">{total} articles read · {len(counts)} active days · America/Toronto</p><div class="heat-scroll"><div class="heat-months">{"".join(month_cells)}</div><div class="heat-layout"><div class="heat-weekdays"><span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span><span>Sun</span></div><div class="heat-grid" role="group" aria-label="Daily articles read in {today.year}">{"".join(cells)}</div></div></div><div class="heat-legend">Less <span class="heat-day level-0"></span><span class="heat-day level-1"></span><span class="heat-day level-2"></span><span class="heat-day level-3"></span><span class="heat-day level-4"></span> More</div><p class="muted">Each article counts once, on the day you first mark it read. Changing its read status later does not erase that reading activity. Hover or focus a day to see its count.</p><p class="muted">{undated} previously read articles have no recorded read date and are excluded from the heatmap.</p></div></main></body></html>'''
