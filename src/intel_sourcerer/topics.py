"""Named collections of articles on one subject, with notes about the collection as a whole."""
from datetime import date, datetime, timedelta
from html import escape
import re
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

from bs4 import BeautifulSoup

from .correlation import KINDS, MIN_SHARED_TECHNIQUES, refresh_index
from .library import short_date
from .sessions import SessionError, clean_notes, has_notes
from .templates import render_template

MAX_NAME = 80
MAX_NOTES = 100_000
MAX_ARTICLES_PER_TOPIC = 500
MAX_INDICATORS = 40
MAX_SUGGESTED = 8
MAX_LISTED = 5
WEEKS = 12
BUSY_DAYS = 30
STATUSES = ("tracking", "closed")
KIND_LABELS = {"hashes": "Hash", "cves": "CVE", "files": "File", "techniques": "Technique"}
# Everything a topic counts, leaving out articles from sources the reader has switched off.
MEMBERS = """topic_articles m JOIN articles a ON a.url=m.article_url
    JOIN sources s ON s.id=a.source_id AND s.enabled=1"""


def _now():
    # Local time, because the list shows this date: an evening edit shouldn't read as tomorrow.
    return datetime.now().astimezone().isoformat()


def _day(value):
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def _note_text(html):
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True).replace("​", "")


def clean_name(name):
    if not isinstance(name, str):
        raise SessionError("Enter a topic name.")
    name = re.sub(r"\s+", " ", name).strip()
    if not name or len(name) > MAX_NAME or not name.isprintable():
        raise SessionError(f"Topic names need between 1 and {MAX_NAME} characters.")
    return name


def _require_topic(db, topic_id):
    row = db.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone() if isinstance(topic_id, str) else None
    if not row:
        raise SessionError("Topic not found. Refresh and try again.")
    return row


def _require_article(db, url):
    if not isinstance(url, str) or not db.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone():
        raise SessionError("Article not found. Refresh and try again.")


def create_topic(db, name):
    """Asking for a name already in use returns that topic, so "PHP2Shell" and "php2shell" stay one."""
    name = clean_name(name)
    existing = db.execute("SELECT id FROM topics WHERE name=?", (name,)).fetchone()
    if existing:
        return {"id": existing[0], "created": False}
    topic_id = uuid.uuid4().hex
    now = _now()
    db.execute(
        "INSERT INTO topics(id,name,created_at,updated_at) VALUES (?,?,?,?)",
        (topic_id, name, now, now),
    )
    return {"id": topic_id, "created": True}


def rename_topic(db, topic_id, name):
    _require_topic(db, topic_id)
    name = clean_name(name)
    other = db.execute("SELECT 1 FROM topics WHERE name=? AND id<>?", (name, topic_id)).fetchone()
    if other:
        raise SessionError("Another topic already has that name.")
    db.execute("UPDATE topics SET name=?,updated_at=? WHERE id=?", (name, _now(), topic_id))
    return {"name": name}


def set_status(db, topic_id, status):
    _require_topic(db, topic_id)
    if status not in STATUSES:
        raise SessionError("Choose Tracking or Closed.")
    db.execute("UPDATE topics SET status=?,updated_at=? WHERE id=?", (status, _now(), topic_id))
    return {"status": status}


def delete_topic(db, topic_id):
    """The articles stay in the Library. Only the grouping and its notes go."""
    _require_topic(db, topic_id)
    db.execute("DELETE FROM topics WHERE id=?", (topic_id,))
    return {"deleted": topic_id}


def save_topic_notes(db, topic_id, text, revision):
    _require_topic(db, topic_id)
    if not isinstance(text, str) or len(text) > MAX_NOTES or type(revision) is not int:
        raise SessionError("Invalid note content or revision.")
    now = _now()
    result = db.execute(
        "UPDATE topics SET notes=?,notes_revision=notes_revision+1,notes_updated_at=?,updated_at=? WHERE id=? AND notes_revision=?",
        (clean_notes(text), now, now, topic_id, revision),
    )
    if result.rowcount != 1:
        raise SessionError("Notes changed in another window. Your draft is kept here; reload before continuing.")
    return {"revision": revision + 1}


def add_article(db, url, topic_id=None, name=None):
    """Add to a topic by id, or by name from the reader, which starts the topic if it's new."""
    _require_article(db, url)
    if topic_id is None:
        topic_id = create_topic(db, name)["id"]
    _require_topic(db, topic_id)
    count = db.execute("SELECT count(*) FROM topic_articles WHERE topic_id=?", (topic_id,)).fetchone()[0]
    if count >= MAX_ARTICLES_PER_TOPIC:
        raise SessionError(f"A topic can hold up to {MAX_ARTICLES_PER_TOPIC} articles.")
    now = _now()
    db.execute("INSERT OR IGNORE INTO topic_articles(topic_id,article_url,added_at) VALUES (?,?,?)", (topic_id, url, now))
    db.execute("UPDATE topics SET updated_at=? WHERE id=?", (now, topic_id))
    return article_topics(db, url)


def save_articles_as_topic(db, name, urls):
    """Keep a list of articles under a name. A name already in use adds to that topic."""
    if not isinstance(urls, list) or not urls or len(urls) > MAX_ARTICLES_PER_TOPIC:
        raise SessionError(f"Choose between 1 and {MAX_ARTICLES_PER_TOPIC} articles.")
    for url in urls:
        _require_article(db, url)
    topic = create_topic(db, name)
    now = _now()
    added = sum(
        db.execute(
            "INSERT OR IGNORE INTO topic_articles(topic_id,article_url,added_at) VALUES (?,?,?)",
            (topic["id"], url, now),
        ).rowcount
        for url in urls
    )
    total = db.execute("SELECT count(*) FROM topic_articles WHERE topic_id=?", (topic["id"],)).fetchone()[0]
    if total > MAX_ARTICLES_PER_TOPIC:
        raise SessionError(f"A topic can hold up to {MAX_ARTICLES_PER_TOPIC} articles.")
    db.execute("UPDATE topics SET updated_at=? WHERE id=?", (now, topic["id"]))
    name = db.execute("SELECT name FROM topics WHERE id=?", (topic["id"],)).fetchone()[0]
    return {"id": topic["id"], "name": name, "created": topic["created"], "added": added}


def remove_article(db, url, topic_id):
    _require_article(db, url)
    _require_topic(db, topic_id)
    db.execute("DELETE FROM topic_articles WHERE topic_id=? AND article_url=?", (topic_id, url))
    db.execute("UPDATE topics SET updated_at=? WHERE id=?", (_now(), topic_id))
    return article_topics(db, url)


def dismiss_suggestion(db, topic_id, url):
    """Stop suggesting this article for this topic. Adding it by hand still works."""
    _require_article(db, url)
    _require_topic(db, topic_id)
    db.execute("INSERT OR IGNORE INTO topic_dismissed(topic_id,article_url) VALUES (?,?)", (topic_id, url))
    return {"dismissed": url}


def citation_href(url):
    return "/?" + urlencode({"open": url})


def _cites(href, url):
    parts = urlsplit(href or "")
    return parts.path == "/" and parse_qs(parts.query).get("open") == [url]


def remove_citations(db, url):
    """A citation to a deleted article would lead nowhere, so it keeps its words and loses the link."""
    for row in db.execute("SELECT id,notes FROM topics WHERE notes LIKE '%/?open=%'").fetchall():
        soup = BeautifulSoup(row["notes"], "html.parser")
        links = [link for link in soup.find_all("a") if _cites(link.get("href"), url)]
        if not links:
            continue
        for link in links:
            link.unwrap()
        # A window still open on the old text gets the usual conflict message rather than restoring the link.
        db.execute("UPDATE topics SET notes=?,notes_revision=notes_revision+1 WHERE id=?", (str(soup), row["id"]))


def open_questions(notes):
    """Unticked checklist items in a topic's notes."""
    items = BeautifulSoup(notes or "", "html.parser").select("ul.checklist > li")
    return sum(item.get("data-checked") != "true" and bool(item.get_text(strip=True)) for item in items)


def list_topics(db):
    topics = []
    for row in db.execute("SELECT * FROM topics ORDER BY updated_at DESC,name").fetchall():
        counts = db.execute(
            f"""SELECT count(*) AS articles,count(DISTINCT a.source_id) AS publishers,
            coalesce(sum(a.is_read=0),0) AS unread FROM {MEMBERS} WHERE m.topic_id=?""",
            (row["id"],),
        ).fetchone()
        latest = db.execute(
            f"SELECT a.published_at FROM {MEMBERS} WHERE m.topic_id=? ORDER BY julianday(a.published_at) DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        topics.append(
            {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "updated_at": row["updated_at"],
                "notes_text": _note_text(row["notes"]),
                "articles": counts["articles"],
                "publishers": counts["publishers"],
                "unread": counts["unread"],
                "open_questions": open_questions(row["notes"]),
                "latest_report": latest[0] if latest else "",
                "indicators": [
                    item[0]
                    for item in db.execute(
                        f"""SELECT DISTINCT i.value FROM {MEMBERS} JOIN article_indicators i ON i.article_url=a.url
                        WHERE m.topic_id=?""",
                        (row["id"],),
                    )
                ],
                "urls": [
                    item[0]
                    for item in db.execute(
                        f"SELECT a.url FROM {MEMBERS} WHERE m.topic_id=? ORDER BY julianday(a.published_at),a.url",
                        (row["id"],),
                    )
                ],
            }
        )
    return {"topics": topics}


def topic_data(db, topic_id):
    """A missing topic is a normal answer here, since old links outlive deleted topics."""
    row = db.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
    if not row:
        return {"topic": None}
    articles = []
    for item in db.execute(
        f"""SELECT a.url,a.title,a.published_at,a.is_read,a.notes,a.notes_format,s.name AS publisher
        FROM {MEMBERS} WHERE m.topic_id=? ORDER BY julianday(a.published_at),a.url""",
        (topic_id,),
    ).fetchall():
        noted = has_notes(item["notes"], item["notes_format"])
        text = _note_text(item["notes"]) if item["notes_format"] == "html" else (item["notes"] or "")
        articles.append(
            {
                "url": item["url"],
                "title": item["title"],
                "publisher": item["publisher"],
                "published_at": item["published_at"],
                "is_read": bool(item["is_read"]),
                "note_excerpt": text[:160] if noted else "",
            }
        )
    highlights = [
        dict(item)
        for item in db.execute(
            f"""SELECT h.quote,a.url,a.title FROM {MEMBERS} JOIN article_highlights h ON h.article_url=a.url
            WHERE m.topic_id=? ORDER BY julianday(a.published_at),h.created_at,h.id""",
            (topic_id,),
        )
    ]
    candidates = [
        dict(item)
        for item in db.execute(
            """SELECT a.url,a.title,s.name AS publisher FROM articles a JOIN sources s ON s.id=a.source_id AND s.enabled=1
            WHERE a.url NOT IN (SELECT article_url FROM topic_articles WHERE topic_id=?)
            ORDER BY julianday(a.published_at) DESC,a.url""",
            (topic_id,),
        )
    ]
    return {
        "topic": {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "notes": row["notes"],
            "notes_revision": row["notes_revision"],
        },
        "articles": articles,
        "highlights": highlights,
        "candidates": candidates,
    }


def _suggestions(db, topic_id):
    """Other saved articles that seem to belong, strongest first, leaving out ones the reader turned down."""
    found = {}
    for row in db.execute(
        f"""SELECT o.article_url AS url,c.title,p.name AS publisher,i.kind,i.value,count(DISTINCT a.url) AS articles
        FROM {MEMBERS} JOIN article_indicators i ON i.article_url=a.url
        JOIN article_indicators o ON o.kind=i.kind AND o.value=i.value
        JOIN articles c ON c.url=o.article_url JOIN sources p ON p.id=c.source_id AND p.enabled=1
        WHERE m.topic_id=? AND o.article_url NOT IN (SELECT article_url FROM topic_articles WHERE topic_id=?)
          AND o.article_url NOT IN (SELECT article_url FROM topic_dismissed WHERE topic_id=?)
        GROUP BY o.article_url,i.kind,i.value""",
        (topic_id, topic_id, topic_id),
    ):
        item = found.setdefault(row["url"], {"url": row["url"], "title": row["title"], "publisher": row["publisher"], "shared": []})
        item["shared"].append((row["kind"], row["value"], row["articles"]))
    suggested = []
    for item in found.values():
        # The same bar as Related reports: techniques alone need several, since common ones are everywhere.
        strong = sorted((s for s in item["shared"] if s[0] != "techniques"), key=lambda s: (-s[2], KINDS.index(s[0]), s[1]))
        techniques = sorted((s for s in item["shared"] if s[0] == "techniques"), key=lambda s: (-s[2], s[1]))
        if not strong and len(techniques) < MIN_SHARED_TECHNIQUES:
            continue
        lead = (strong or techniques)[0]
        others = len(item["shared"]) - 1
        suggested.append(
            {
                "url": item["url"],
                "title": item["title"],
                "publisher": item["publisher"],
                "why": f"Shares {lead[1]} with {lead[2]} {'article' if lead[2] == 1 else 'articles'}"
                + (f", and {others} more" if others else ""),
                "_rank": (len(strong), len(techniques), lead[2]),
            }
        )
    suggested.sort(key=lambda item: (item["_rank"], item["url"]), reverse=True)
    for item in suggested:
        del item["_rank"]
    return suggested


def topic_insights(db, topic_id):
    """What these articles have in common, and other saved articles that seem to belong with them."""
    _require_topic(db, topic_id)
    first = db.execute("SELECT article_url FROM topic_articles WHERE topic_id=? LIMIT 1", (topic_id,)).fetchone()
    remaining = refresh_index(db, first[0] if first else None)
    total = db.execute(f"SELECT count(*) FROM {MEMBERS} WHERE m.topic_id=?", (topic_id,)).fetchone()[0]
    rows = db.execute(
        f"""SELECT i.kind,i.value,count(*) AS articles FROM {MEMBERS}
        JOIN article_indicators i ON i.article_url=a.url WHERE m.topic_id=?
        GROUP BY i.kind,i.value ORDER BY articles DESC,i.kind,i.value""",
        (topic_id,),
    ).fetchall()
    suggested = _suggestions(db, topic_id)
    return {
        "complete": remaining == 0,
        "total_articles": total,
        "total_indicators": len(rows),
        "indicators": [
            {"kind": KIND_LABELS[row["kind"]], "value": row["value"], "articles": row["articles"]}
            for row in rows[:MAX_INDICATORS]
        ],
        "suggested": suggested[:MAX_SUGGESTED],
    }


def topics_overview(db, today=None):
    """Statistics across tracked topics. This reads every topic's matches, so pages ask for it after drawing."""
    today = today or date.today()
    remaining = refresh_index(db)
    tracked = db.execute("SELECT id,name,notes FROM topics WHERE status='tracking' ORDER BY name").fetchall()
    belong = {row["id"]: len(_suggestions(db, row["id"])) for row in tracked}
    rows = db.execute(
        f"""SELECT m.topic_id,a.url,a.published_at,a.is_read,a.source_id FROM {MEMBERS}
        JOIN topics t ON t.id=m.topic_id AND t.status='tracking'"""
    ).fetchall()
    published = {row["url"]: _day(row["published_at"]) for row in rows}
    monday = today - timedelta(days=today.weekday())
    weeks = []
    for back in range(WEEKS - 1, -1, -1):
        start = monday - timedelta(weeks=back)
        count = sum(day is not None and start <= day < start + timedelta(days=7) for day in published.values())
        weeks.append({"start": short_date(start.isoformat()), "reports": count})
    names = {row["id"]: row["name"] for row in tracked}
    recent, publishers, sizes = {}, {}, {}
    for row in rows:
        day = published[row["url"]]
        sizes[row["topic_id"]] = sizes.get(row["topic_id"], 0) + 1
        publishers.setdefault(row["topic_id"], set()).add(row["source_id"])
        if day is not None and timedelta(0) <= today - day <= timedelta(days=BUSY_DAYS):
            recent[row["topic_id"]] = recent.get(row["topic_id"], 0) + 1
    shared = {}
    for row in db.execute(
        f"""SELECT DISTINCT i.kind,i.value,m.topic_id FROM {MEMBERS}
        JOIN topics t ON t.id=m.topic_id AND t.status='tracking'
        JOIN article_indicators i ON i.article_url=a.url WHERE i.kind<>'techniques'"""
    ):
        shared.setdefault((row["kind"], row["value"]), []).append(row["topic_id"])
    linked = sorted(
        ((kind, value, ids) for (kind, value), ids in shared.items() if len(ids) > 1),
        key=lambda item: (-len(item[2]), item[1]),
    )
    return {
        "complete": remaining == 0,
        "topics": len(tracked),
        "articles": len(published),
        "unread": len({row["url"] for row in rows if not row["is_read"]}),
        "open_questions": sum(open_questions(row["notes"]) for row in tracked),
        "might_belong": sum(belong.values()),
        "belong": belong,
        "weeks": weeks,
        "weeks_total": sum(week["reports"] for week in weeks),
        "busiest": [
            {"id": topic_id, "name": names[topic_id], "reports": count}
            for topic_id, count in sorted(recent.items(), key=lambda item: (-item[1], names[item[0]]))[:MAX_LISTED]
        ],
        "shared": [
            {
                "kind": KIND_LABELS[kind],
                "value": value,
                "topics": [{"id": topic_id, "name": names[topic_id]} for topic_id in sorted(ids, key=names.get)],
            }
            for kind, value, ids in linked[:MAX_LISTED]
        ],
        "single_publisher": [
            {"id": topic_id, "name": names[topic_id], "articles": sizes[topic_id]}
            for topic_id in sorted(sizes, key=names.get)
            if len(publishers[topic_id]) == 1
        ][:MAX_LISTED],
    }


def article_topics(db, url):
    """The topics holding this article, ones that might suit it, and every other name for the picker."""
    _require_article(db, url)
    mine = [
        dict(row)
        for row in db.execute(
            """SELECT t.id,t.name FROM topic_articles m JOIN topics t ON t.id=m.topic_id
            WHERE m.article_url=? ORDER BY t.name""",
            (url,),
        )
    ]
    suggestions = [
        dict(row)
        for row in db.execute(
            """SELECT t.id,t.name,min(i.value) AS value,count(DISTINCT m.article_url) AS articles
            FROM article_indicators i
            JOIN article_indicators o ON o.kind=i.kind AND o.value=i.value AND o.article_url<>i.article_url
            JOIN topic_articles m ON m.article_url=o.article_url
            JOIN topics t ON t.id=m.topic_id AND t.status='tracking'
            WHERE i.article_url=? AND i.kind<>'techniques'
              AND t.id NOT IN (SELECT topic_id FROM topic_articles WHERE article_url=?)
              AND t.id NOT IN (SELECT topic_id FROM topic_dismissed WHERE article_url=?)
            GROUP BY t.id ORDER BY articles DESC,t.name LIMIT 3""",
            (url, url, url),
        )
    ]
    names = [
        row[0]
        for row in db.execute(
            "SELECT name FROM topics WHERE id NOT IN (SELECT topic_id FROM topic_articles WHERE article_url=?) ORDER BY status='closed',updated_at DESC",
            (url,),
        )
    ]
    return {"topics": mine, "suggestions": suggestions, "names": names}


def _count(number, word):
    return f"{number} {word}" + ("" if number == 1 else "s")


def _status_of(value):
    return value if value in STATUSES else STATUSES[0]


def render_topics(topics, query="", status="tracking", csrf_token=""):
    """Render from backend JSON without opening a database, like the Library."""
    status = _status_of(status)
    terms = query.casefold().split()
    rows = []
    shown = [
        topic
        for topic in topics
        if topic["status"] == status
        and all(
            term in " ".join([topic["name"], topic["notes_text"], *topic["indicators"]]).casefold()
            for term in terms
        )
    ]
    shown.sort(key=lambda topic: _day(topic["latest_report"]) or date.min, reverse=True)
    shown.sort(key=lambda topic: not (topic["unread"] or topic["open_questions"]))
    for topic in shown:
        waiting = []
        if topic["open_questions"]:
            waiting.append(f'<span class="topic-open">{_count(topic["open_questions"], "open question")}</span>')
        if topic["unread"]:
            waiting.append(f'<span>{topic["unread"]} unread</span>')
        rows.append(
            render_template(
                "topics/topic-row.html",
                link=escape("/topic?" + urlencode({"id": topic["id"]}), quote=True),
                id=escape(topic["id"], quote=True),
                latest_value=escape(topic["latest_report"], quote=True),
                waiting_count=topic["open_questions"] + topic["unread"],
                topic_name=escape(topic["name"]),
                latest=escape(short_date(topic["latest_report"])) if topic["latest_report"] else "No reports yet",
                size=_count(topic["articles"], "article"),
                waiting="".join(waiting),
            )
        )

    def link(value):
        return escape("/topics?" + urlencode({"q": query, "status": value}), quote=True)

    empty = (
        "No topics match that search."
        if terms
        else "No topics yet. Create one for a campaign, actor or CVE."
        if not topics
        else f"No {status} topics."
    )
    return render_template(
        "topics/index.html",
        csrf_token=escape(csrf_token, quote=True),
        query=escape(query, quote=True),
        status=status,
        tracking_link=link("tracking"),
        tracking_class="active" if status == "tracking" else "",
        closed_link=link("closed"),
        closed_class="active" if status == "closed" else "",
        topic_count=_count(len(shown), "topic"),
        topic_rows="".join(rows) or f'<p class="muted topic-empty">{empty}</p>',
    )


def _dates(articles):
    dates = [short_date(item["published_at"]) for item in articles]
    if not dates:
        return ""
    return dates[0] if dates[0] == dates[-1] else f"{dates[0]} to {dates[-1]}"


def _option(item, attribute):
    return f'<option {attribute}="{escape(item["url"], quote=True)}" value="{escape(item["title"], quote=True)}">{escape(item["publisher"])}</option>'


def render_topic(data, csrf_token=""):
    """Render one topic from backend JSON. Stored notes pass through the sanitizer on the way out too."""
    topic = data["topic"]
    if topic is None:
        return render_template("topics/missing.html")
    articles, highlights = data["articles"], data["highlights"]
    unread = [item for item in articles if not item["is_read"]]
    rows = []
    cite = []
    for item in articles:
        date = short_date(item["published_at"])
        state = "Read" if item["is_read"] else "Unread"
        rows.append(
            render_template(
                "topics/article-row.html",
                date=escape(date),
                link=escape(citation_href(item["url"]), quote=True),
                title=escape(item["title"]),
                meta=escape(" · ".join([item["publisher"], state] + (["Noted"] if item["note_excerpt"] else []))),
                note=f'<p class="topic-article-note">{escape(item["note_excerpt"])}</p>' if item["note_excerpt"] else "",
                url=escape(item["url"], quote=True),
                remove_label=escape(f"Remove {item['title']} from this topic", quote=True),
            )
        )
        cite.append(
            f'<button type="button" data-cite="{escape(citation_href(item["url"]), quote=True)}" '
            f'data-label="{escape(item["publisher"] + ", " + date, quote=True)}">'
            f'<span>{escape(item["title"])}</span><span class="muted">{escape(item["publisher"])} · {escape(date)}</span></button>'
        )
    summary = [_count(len(articles), "article"), _count(len({item["publisher"] for item in articles}), "publisher")]
    if unread:
        summary.append(f"{len(unread)} unread")
    if articles:
        summary.append(_dates(articles))
    quotes = "".join(
        f'<blockquote class="lib-highlight">{escape(item["quote"])}'
        f'<a class="topic-quote-source" href="{escape(citation_href(item["url"]), quote=True)}">{escape(item["title"])}</a></blockquote>'
        for item in highlights
    )
    return render_template(
        "topics/topic.html",
        csrf_token=escape(csrf_token, quote=True),
        id=escape(topic["id"], quote=True),
        topic_name=escape(topic["name"]),
        name_value=escape(topic["name"], quote=True),
        summary=escape(" · ".join(summary)),
        tracking_selected="selected" if topic["status"] == "tracking" else "",
        closed_selected="selected" if topic["status"] == "closed" else "",
        read_hidden="" if unread else "hidden",
        read_label=f"Read {len(unread)} unread",
        unread_urls=escape(" ".join(citation_href(item["url"]) for item in unread), quote=True),
        article_rows="".join(rows) or '<p class="muted topic-empty">No articles yet. Add one above, or from the reader while you read.</p>',
        candidates="".join(_option(item, "data-url") for item in data["candidates"]),
        revision=topic["notes_revision"],
        notes=clean_notes(topic["notes"]),
        cite_hidden="" if articles else "hidden",
        cite_buttons="".join(cite),
        highlight_hidden="" if highlights else "hidden",
        highlight_count=len(highlights),
        highlights=quotes,
    )
