"""Data aggregation for the 'Learning Gym' Home dashboard view."""
from collections import Counter
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from .discovery import article_types
from .http_common import display_timezone
from .iocs import _attack_names, kev_info
from .topics import open_questions
from .youtube import video_duration

THEME_ORDER = ("Vulnerabilities", "AI misuse", "Incident analysis", "Threat research")
THEME_DESCRIPTIONS = {
    "Vulnerabilities": "CVEs, exploits, zero-days & prompt injection",
    "AI misuse": "Frontier model abuse, distillation & agent threats",
    "Incident analysis": "Campaigns, intrusions, espionage & ransomware",
    "Threat research": "Malware analysis, tradecraft & deep technical writeups",
}


def _extract_questions(html, limit=2):
    """Pull up to `limit` short question lines from topic notes."""
    if not html:
        return []
    text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    found = []
    for line in text.splitlines():
        cleaned = " ".join(line.split()).strip("•-*[] ")
        if "?" in cleaned and len(cleaned) >= 8:
            found.append(cleaned[:140] + ("…" if len(cleaned) > 140 else ""))
            if len(found) >= limit:
                break
    return found


def _extract_takeaway(notes, notes_format, highlight_quote=""):
    """Pick the most meaningful 1-2 line takeaway from an article's notes or highlights."""
    if notes:
        if notes_format == "html":
            soup = BeautifulSoup(notes, "html.parser")
            quote = soup.find("blockquote")
            if quote and quote.get_text(" ", strip=True):
                text = quote.get_text(" ", strip=True)
            else:
                text = soup.get_text(" ", strip=True)
        else:
            text = " ".join(notes.split())
        text = text.replace("\u200b", "").strip()
        if text:
            return text[:180] + ("…" if len(text) > 180 else "")
    if highlight_quote:
        cleaned = " ".join(highlight_quote.split())
        return cleaned[:180] + ("…" if len(cleaned) > 180 else "")
    return ""


def _estimate_minutes(content_format, url, word_count, media_json, fallback_text=""):
    from .content import estimate_minutes

    return estimate_minutes(content_format, url, word_count, media_json, fallback_text)


def home_gym_data(db, now=None):
    """Build real-data training pulse, research focus themes, recurring IOCs, open questions, and takeaways."""
    zone = display_timezone()
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = now_utc.astimezone(zone).date()

    # 1. Training rhythm: 8-week (56-day) mini-heatmap + 7-day pulse.
    counts = Counter()
    week_cutoff = today - timedelta(days=6)
    month_cutoff = today - timedelta(days=29)
    for (read_at,) in db.execute("SELECT read_at FROM article_reads WHERE read_at IS NOT NULL"):
        try:
            day = datetime.fromisoformat(read_at.replace("Z", "+00:00")).astimezone(zone).date()
        except ValueError:
            continue
        if day <= today:
            counts[day] += 1

    # Align to Monday 7 weeks back so the grid is always 8 clean Monday-Sunday columns.
    start_day = today - timedelta(days=today.weekday() + 7 * 7)
    heatmap = []
    cur = start_day
    while cur <= today + timedelta(days=(6 - today.weekday())):
        future = cur > today
        value = 0 if future else counts[cur]
        level = 0 if not value else 1 if value == 1 else 2 if value <= 3 else 3 if value <= 6 else 4
        heatmap.append({"date": cur.isoformat(), "count": value, "level": level, "future": future})
        cur += timedelta(days=1)

    week_read = sum(v for d, v in counts.items() if week_cutoff <= d <= today)
    active_days_30d = sum(1 for d, v in counts.items() if month_cutoff <= d <= today and v > 0)
    week_since_iso = (now_utc - timedelta(days=7)).isoformat()
    elapsed_row = db.execute(
        "SELECT COALESCE(SUM(elapsed_seconds), 0) FROM timed_reading_sessions WHERE started_at >= ?",
        (week_since_iso,),
    ).fetchone()
    week_minutes = round((elapsed_row[0] or 0) / 60)
    week_notes = db.execute(
        "SELECT COUNT(*) FROM articles WHERE notes_updated_at IS NOT NULL AND notes_updated_at >= ?",
        (week_since_iso,),
    ).fetchone()[0]

    # 2. Recent research landscape (30 days, falling back to 90 days if quiet).
    rows = db.execute(
        """SELECT a.url, a.title, a.published_at, a.is_read, a.triage, a.content_format, a.media_json, a.word_count,
                  substr(a.content_text, 1, 1500) AS snippet_text,
                  CASE WHEN a.word_count IS NULL THEN coalesce(a.content_text, '') ELSE '' END AS fallback_text,
                  s.name AS publisher
           FROM articles a JOIN sources s ON s.id=a.source_id
           WHERE s.enabled=1
           ORDER BY julianday(a.published_at) DESC"""
    ).fetchall()

    article_minutes = {
        r["url"]: _estimate_minutes(r["content_format"], r["url"], r["word_count"], r["media_json"], r["fallback_text"])
        for r in rows
    }

    cutoff_30 = (now_utc - timedelta(days=30)).isoformat()
    cutoff_90 = (now_utc - timedelta(days=90)).isoformat()
    recent = [r for r in rows if (r["published_at"] or "") >= cutoff_30]
    window_label = "Last 30 days"
    if len(recent) < 3:
        recent = [r for r in rows if (r["published_at"] or "") >= cutoff_90]
        window_label = "Last 90 days"
    if len(recent) < 3:
        recent = list(rows[:40])
        window_label = "Saved library"

    recent_urls = {r["url"] for r in recent}
    names_lookup, _ = _attack_names()
    ind_by_url = {}
    if recent_urls:
        for ind in db.execute(
            """SELECT i.article_url, i.kind, i.value
               FROM article_indicators i
               WHERE i.kind IN ('cves', 'techniques', 'entities')
                 AND NOT EXISTS (SELECT 1 FROM ignored_indicators ig WHERE ig.kind=i.kind AND ig.value=i.value)"""
        ):
            if ind["article_url"] in recent_urls:
                ind_by_url.setdefault(ind["article_url"], []).append((ind["kind"], ind["value"]))

    theme_buckets = {
        name: {"urls": [], "unread_urls": [], "publishers": set(), "indicators": Counter()}
        for name in THEME_ORDER
    }
    recurring_map = {}

    for r in recent:
        url = r["url"]
        types = article_types(r["title"], r["snippet_text"] or "")
        # Prefer specific theme over generic "Threat research" if the article has a specific category.
        primary_themes = [t for t in types if t != "Threat research"] or ["Threat research"]
        for theme in primary_themes:
            if theme not in theme_buckets:
                continue
            bucket = theme_buckets[theme]
            bucket["urls"].append(url)
            if not r["is_read"] and r["triage"] != "skip":
                bucket["unread_urls"].append(url)
            bucket["publishers"].add(r["publisher"])
            for kind, val in ind_by_url.get(url, ()):
                bucket["indicators"][(kind, val)] += 1
        for kind, val in ind_by_url.get(url, ()):
            rec = recurring_map.setdefault((kind, val), {"urls": [], "publishers": set()})
            rec["urls"].append(url)
            rec["publishers"].add(r["publisher"])

    themes = []
    for name in THEME_ORDER:
        bucket = theme_buckets[name]
        if not bucket["urls"]:
            continue
        top_ind = None
        if bucket["indicators"]:
            (ikind, ival), icount = bucket["indicators"].most_common(1)[0]
            label = f"{ival} {names_lookup[ival]}" if ival in names_lookup else ival
            top_ind = {"kind": ikind, "value": ival, "label": label, "count": icount}
        themes.append(
            {
                "name": name,
                "description": THEME_DESCRIPTIONS.get(name, ""),
                "count": len(bucket["urls"]),
                "unread": len(bucket["unread_urls"]),
                "publishers": len(bucket["publishers"]),
                "top_indicator": top_ind,
                "urls": bucket["urls"],
                "unread_urls": bucket["unread_urls"],
            }
        )

    recurring = []
    for (kind, val), info in recurring_map.items():
        if len(info["urls"]) < 2 and kev_info(val) is None:
            continue
        recurring.append(
            {
                "kind": kind,
                "value": val,
                "name": names_lookup.get(val, ""),
                "kev": kind == "cves" and kev_info(val) is not None,
                "reports": len(info["urls"]),
                "publishers": len(info["publishers"]),
                "urls": info["urls"],
            }
        )
    recurring.sort(
        key=lambda item: (item["kev"], item["publishers"], item["reports"], item["value"]),
        reverse=True,
    )

    # 3. Tracked topics & open questions.
    topics = []
    for trow in db.execute(
        """SELECT t.id, t.name, t.notes,
                  COUNT(a.url) AS articles,
                  SUM(CASE WHEN a.is_read=0 THEN 1 ELSE 0 END) AS unread
           FROM topics t
           LEFT JOIN topic_articles ta ON ta.topic_id=t.id
           LEFT JOIN articles a ON a.url=ta.article_url
           WHERE t.status='tracking'
           GROUP BY t.id
           ORDER BY t.updated_at DESC
           LIMIT 5"""
    ):
        qs = _extract_questions(trow["notes"])
        topics.append(
            {
                "id": trow["id"],
                "name": trow["name"],
                "articles": trow["articles"] or 0,
                "unread": trow["unread"] or 0,
                "open_questions": open_questions(trow["notes"]),
                "questions": qs,
            }
        )

    # 4. Recent takeaways (notes or highlights).
    takeaways = []
    for nrow in db.execute(
        """SELECT a.url, a.title, a.notes, a.notes_format, a.notes_updated_at, a.published_at, s.name AS publisher,
                  (SELECT h.quote FROM article_highlights h WHERE h.article_url=a.url ORDER BY h.created_at DESC LIMIT 1) AS hl_quote
           FROM articles a JOIN sources s ON s.id=a.source_id
           WHERE s.enabled=1 AND (trim(a.notes)<>'' OR EXISTS (SELECT 1 FROM article_highlights h WHERE h.article_url=a.url))
           ORDER BY coalesce(a.notes_updated_at, a.published_at) DESC
           LIMIT 4"""
    ):
        snippet = _extract_takeaway(nrow["notes"], nrow["notes_format"], nrow["hl_quote"] or "")
        if snippet:
            takeaways.append(
                {
                    "url": nrow["url"],
                    "title": nrow["title"],
                    "publisher": nrow["publisher"],
                    "date": nrow["notes_updated_at"] or nrow["published_at"],
                    "snippet": snippet,
                }
            )

    return {
        "window_label": window_label,
        "pulse": {
            "heatmap": heatmap,
            "week_read": week_read,
            "week_minutes": week_minutes,
            "week_notes": week_notes,
            "active_days_30d": active_days_30d,
        },
        "themes": themes,
        "recurring": recurring[:8],
        "topics": topics,
        "takeaways": takeaways,
        "article_minutes": article_minutes,
    }
