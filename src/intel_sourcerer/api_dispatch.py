"""JSON API read and transactional mutation dispatch extracted from web.py."""

from dataclasses import asdict
from html import escape, unescape
import re
from urllib.parse import parse_qs

from bs4 import BeautifulSoup

TAG_STRIP = re.compile(r"<[^>]+>")


def _fast_note_preview(notes, notes_format):
    if not notes:
        return ""
    if notes_format != "html":
        return notes
    return " ".join(unescape(TAG_STRIP.sub(" ", notes)).split())

from .config import load_sources
from .content import arrange_media, format_label, render_article_soup, render_markdown_soup
from .discovery import article_types
from .iocs import extract_iocs
from .jobs import get_job
from .reading_state import (
    delete_highlight,
    get_position,
    in_progress_count,
    list_highlights,
    save_highlight,
    save_position,
    set_triage,
    week_counts,
)
from .sessions import (
    create_queue,
    delete_article,
    has_notes,
    navigate,
    queue_state,
    rename_article,
    save_notes,
    update_queue,
)
from .timed_reading import session_state, timer_action
from .youtube import video_duration


def handle_api_get(db, database, config, path, query, *, is_admin, read_state_articles, transcript_info):
    """Dispatch read-only /api/* GET endpoints. Returns (payload, status_code)."""
    params = parse_qs(query)
    if path == "/api/library":
        from .library import fts_match_urls, library_rows, storage_usage

        selected = params.get("selected", [""])[0]
        q = params.get("q", [""])[0]
        payload = {"articles": library_rows(db, selected=selected, include_body_text=bool(q)), "storage": storage_usage(database)}
        if q:
            matched = fts_match_urls(db, q)
            if matched is not None:
                payload["fts_urls"] = sorted(matched)
        return payload, 200
    if path == "/api/sources":
        from .source_view import recent_excluded, source_health

        return {
            "sources": [asdict(source) for source in load_sources(config)],
            "health": source_health(db),
            "excluded": recent_excluded(db),
        }, 200
    if path == "/api/watchlists":
        from .watchlists import list_watchlists

        return {"watchlists": list_watchlists(db)}, 200
    if path == "/api/ai-status":
        from .ai_assist import ai_status

        return ai_status(), 200
    if path == "/api/ai-results":
        from .ai_assist import saved_results

        return {"results": saved_results(db, params.get("url", [""])[0])}, 200
    if path == "/api/home-gym":
        from .home_gym import home_gym_data

        return home_gym_data(db), 200
    if path == "/api/stats":
        from .stats import stats_data

        return stats_data(db), 200
    if path == "/api/timer":
        return {"session": session_state(db)}, 200
    if path == "/api/jobs":
        job = get_job(database, params.get("id", [""])[0])
        if job is None:
            return {"error": "Job not found."}, 404
        return job, 200
    if path == "/api/state":
        from .watchlists import article_cve_summary, list_watchlists, match_watchlists

        watched = list_watchlists(db)
        cves_by_url, kev_by_url = article_cve_summary(db)
        raw_rows = read_state_articles(database)
        try:
            limit = int(params.get("limit", ["0"])[0])
        except ValueError:
            limit = 0
        if limit > 0:
            raw_rows = raw_rows[:limit]
        articles = [
            dict(
                {
                    k: r[k]
                    for k in (
                        "url",
                        "title",
                        "published_at",
                        "is_read",
                        "publisher",
                        "notes_updated_at",
                        "content_format",
                        "triage",
                    )
                },
                format_label=format_label(r["content_format"], r["publisher"]),
                note_preview=_fast_note_preview(r["notes"], r["notes_format"]),
                has_notes=has_notes(r["notes"], r["notes_format"]),
                article_types=article_types(r["title"], r["content_text"]),
                cves=cves_by_url.get(r["url"], []),
                kev_cves=kev_by_url.get(r["url"], []),
                watchlist_hits=match_watchlists(
                    watched,
                    r["title"],
                    r["publisher"],
                    r["content_text"] or "",
                    cves_by_url.get(r["url"], ()),
                ),
            )
            for r in raw_rows
        ]
        return {
            "articles": articles,
            "queue": queue_state(db),
            "week": week_counts(db),
            "in_progress": in_progress_count(db),
            "watchlists": watched,
        }, 200
    if path == "/api/queue":
        return {"queue": queue_state(db)}, 200
    if path == "/api/indicators":
        from .indicators import indicator_options, indicators_data

        return indicators_data(db, **indicator_options(params)), 200
    if path == "/api/tags":
        from .tags import tag_state

        return tag_state(db, params.get("url", [""])[0]), 200
    if path == "/api/topics":
        from .topics import list_topics

        return list_topics(db), 200
    if path == "/api/topics-overview":
        from .topics import topics_overview

        return topics_overview(db), 200
    if path == "/api/topic":
        from .topics import topic_data

        return topic_data(db, params.get("id", [""])[0]), 200
    if path == "/api/topic-insights":
        from .topics import topic_insights

        return topic_insights(db, params.get("id", [""])[0]), 200
    if path == "/api/article-topics":
        from .correlation import refresh_index
        from .topics import article_topics

        url = params.get("url", [""])[0]
        refresh_index(db, url, budget=1)
        return article_topics(db, url), 200
    if path == "/api/related":
        from .correlation import related_reports

        url = params.get("url", [""])[0]
        if not db.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone():
            return {"error": "Article not found"}, 404
        return related_reports(db, url), 200
    if path == "/api/article":
        url = params.get("url", [""])[0]
        row = db.execute(
            "SELECT a.*,s.name AS publisher FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.url=?",
            (url,),
        ).fetchone()
        if not row:
            return {"error": "Article not found"}, 404
        soup = (
            render_article_soup(row["content_html"])
            if row["content_html"]
            else render_markdown_soup(row["content_markdown"])
            if row["content_markdown"]
            else BeautifulSoup(
                "<p>" + escape(row["content_text"] or "No saved article text.") + "</p>",
                "html.parser",
            )
        )
        title = soup.find("h1")
        if title:
            for node in list(title.previous_siblings):
                node.extract()
            title.decompose()
        arrange_media(soup, row["title"], row["url"])
        body_html = str(soup)
        from urllib.parse import urlencode

        cite_href = "/?" + urlencode({"open": row["url"]})
        backlinks = [
            {"kind": "article", "title": b["title"], "href": "/?" + urlencode({"open": b["url"]})}
            for b in db.execute(
                "SELECT url, title FROM articles WHERE url<>? AND instr(notes, ?)>0 ORDER BY julianday(published_at) DESC LIMIT 20",
                (row["url"], cite_href),
            )
        ] + [
            {"kind": "topic", "title": t["name"], "href": "/topic?" + urlencode({"id": t["id"]})}
            for t in db.execute(
                "SELECT id, name FROM topics WHERE instr(notes, ?)>0 ORDER BY name COLLATE NOCASE LIMIT 20",
                (cite_href,),
            )
        ]
        from .indicators import apply_indicator_curation

        curated_iocs = apply_indicator_curation(db, row["url"], extract_iocs(body_html))
        return dict(
            content_format=row["content_format"],
            format_label=format_label(row["content_format"], row["publisher"]),
            url=row["url"],
            title=row["title"],
            published_at=row["published_at"],
            publisher=row["publisher"],
            is_read=row["is_read"],
            has_notes=has_notes(row["notes"], row["notes_format"]),
            notes=row["notes"],
            notes_format=row["notes_format"],
            notes_revision=row["notes_revision"],
            body_html=body_html,
            body_format="html" if row["content_html"] else "markdown",
            highlights=list_highlights(db, row["url"]),
            iocs=curated_iocs,
            backlinks=backlinks,
            position=get_position(db, row["url"]),
            transcript=transcript_info(database, row, is_admin),
            duration=video_duration(row["media_json"]),
        ), 200
    return {"error": "Not found"}, 404


def handle_api_mutation(db, path, data):
    """Dispatch transactional /api/* POST mutations. Returns (payload, status_code)."""
    if path == "/api/timer":
        return {"session": timer_action(db, data)}, 200
    if path == "/api/queue":
        return create_queue(db, data.get("urls")), 200
    if path == "/api/queue-update":
        return update_queue(db, data.get("urls")), 200
    if path == "/api/notes":
        return {
            "revision": save_notes(
                db,
                data.get("url"),
                data.get("text"),
                data.get("revision"),
                data.get("format", "plain"),
            )
        }, 200
    if path == "/api/highlights":
        return save_highlight(
            db,
            data.get("url"),
            data.get("quote"),
            data.get("prefix", ""),
            data.get("suffix", ""),
        ), 200
    if path == "/api/highlight-delete":
        return delete_highlight(db, data.get("id")), 200
    if path == "/api/position":
        return save_position(db, data.get("url"), data.get("fraction")), 200
    if path == "/api/triage":
        return set_triage(db, data.get("urls"), data.get("decision")), 200
    if path == "/api/watchlists":
        from .watchlists import add_watchlist

        return add_watchlist(db, data.get("term")), 200
    if path == "/api/watchlist-delete":
        from .watchlists import delete_watchlist

        return delete_watchlist(db, data.get("id")), 200
    if path == "/api/indicator-ignore":
        from .indicators import ignore_indicator

        return ignore_indicator(db, data.get("kind"), data.get("value")), 200
    if path == "/api/indicator-unignore":
        from .indicators import unignore_indicator

        return unignore_indicator(db, data.get("kind"), data.get("value")), 200
    if path == "/api/indicator-add":
        from .indicators import add_manual_indicator

        return add_manual_indicator(db, data.get("url"), data.get("kind"), data.get("value")), 200
    if path == "/api/indicator-remove":
        from .indicators import delete_manual_indicator

        return delete_manual_indicator(db, data.get("url"), data.get("kind"), data.get("value")), 200
    if path == "/api/tags":
        from .tags import add_tag

        return add_tag(db, data.get("url"), data.get("tag")), 200
    if path == "/api/tag-delete":
        from .tags import remove_tag

        return remove_tag(db, data.get("url"), data.get("tag")), 200
    if path == "/api/topics":
        from .topics import create_topic

        return create_topic(db, data.get("name")), 200
    if path == "/api/topic-from-articles":
        from .topics import save_articles_as_topic

        return save_articles_as_topic(db, data.get("name"), data.get("urls")), 200
    if path == "/api/topic-rename":
        from .topics import rename_topic

        return rename_topic(db, data.get("id"), data.get("name")), 200
    if path == "/api/topic-status":
        from .topics import set_status

        return set_status(db, data.get("id"), data.get("status")), 200
    if path == "/api/topic-notes":
        from .topics import save_topic_notes

        return save_topic_notes(db, data.get("id"), data.get("text"), data.get("revision")), 200
    if path == "/api/topic-delete":
        from .topics import delete_topic

        return delete_topic(db, data.get("id")), 200
    if path == "/api/topic-dismiss":
        from .topics import dismiss_suggestion

        return dismiss_suggestion(db, data.get("id"), data.get("url")), 200
    if path == "/api/topic-article":
        from .topics import add_article

        return add_article(db, data.get("url"), data.get("id"), data.get("name")), 200
    if path == "/api/topic-article-delete":
        from .topics import remove_article

        return remove_article(db, data.get("url"), data.get("id")), 200
    if path == "/api/rename-article":
        return rename_article(db, data.get("url"), data.get("title")), 200
    if path == "/api/delete-article":
        return delete_article(db, data.get("url")), 200
    if path == "/api/navigate":
        return navigate(
            db,
            data.get("queue_id"),
            data.get("cursor"),
            data.get("action"),
        ), 200
    return {"error": "Not found"}, 404
