"""Sources page rendering, per-source health diagnostics, and filtered-out audit drawer."""

from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
from urllib.parse import urlsplit

from .config import DEFAULT_EXCLUDE_KEYWORDS, DEFAULT_INCLUDE_KEYWORDS
from .library import short_date
from .templates import render_template


def _ensure_dismissals_table(db):
    db.execute(
        """CREATE TABLE IF NOT EXISTS excluded_dismissals (
            url TEXT PRIMARY KEY,
            dismissed_at TEXT NOT NULL
        )"""
    )


def dismiss_excluded(db, urls):
    """Persist dismissed filtered-out article URLs so they stay out of the review queue."""
    _ensure_dismissals_table(db)
    now = datetime.now(timezone.utc).isoformat()
    cleaned = [u.strip() for u in (urls or []) if isinstance(u, str) and u.strip()]
    if not cleaned:
        return 0
    db.executemany(
        "INSERT OR REPLACE INTO excluded_dismissals(url, dismissed_at) VALUES (?, ?)",
        [(url, now) for url in cleaned],
    )
    return len(cleaned)


def source_health(db):
    """Summarize saved article counts, latest publication date, and pause/block status per source."""
    counts = {
        row["source_id"]: {
            "articles": row["count"],
            "latest": row["latest"] or "",
        }
        for row in db.execute(
            "SELECT source_id, COUNT(*) AS count, MAX(published_at) AS latest FROM articles GROUP BY source_id"
        )
    }
    now = datetime.now(timezone.utc)
    health = {}
    for row in db.execute(
        "SELECT source_id, covered_since, paused_until, pause_reason, pages_blocked_until FROM source_fetch_state"
    ):
        sid = row["source_id"]
        info = dict(counts.get(sid, {"articles": 0, "latest": ""}))
        info["covered_since"] = row["covered_since"] or ""
        if row["paused_until"]:
            try:
                if datetime.fromisoformat(row["paused_until"]) > now:
                    info["paused"] = row["pause_reason"] or "paused"
            except ValueError:
                pass
        if row["pages_blocked_until"]:
            try:
                if datetime.fromisoformat(row["pages_blocked_until"]) > now:
                    info["pages_blocked"] = True
            except ValueError:
                pass
        health[sid] = info
    for sid, info in counts.items():
        health.setdefault(sid, info)
    return health


def recent_excluded(db, limit=120):
    """Return recently filtered-out articles across recent import runs (excluding saved or dismissed URLs)."""
    _ensure_dismissals_table(db)
    rows = db.execute("SELECT report_json FROM import_runs ORDER BY id DESC LIMIT 20").fetchall()
    if not rows:
        return []
    saved = {r[0] for r in db.execute("SELECT url FROM articles")}
    dismissed = {r[0] for r in db.execute("SELECT url FROM excluded_dismissals")}
    source_names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM sources")}
    seen_keys = set()
    items = []
    for row in rows:
        try:
            report = json.loads(row["report_json"])
        except (TypeError, ValueError):
            continue
        for src in report.get("sources", []):
            sid = src.get("id", "")
            sname = source_names.get(sid, sid)
            for entry in src.get("excluded", []):
                if isinstance(entry, dict):
                    title = (entry.get("title") or "").strip()
                    url = (entry.get("url") or "").strip()
                    reason = (entry.get("reason") or "").strip() or "No keyword match"
                    summary = (entry.get("summary") or "").strip()
                    published_at = (entry.get("published_at") or "").strip()
                else:
                    title, url, reason, summary, published_at = str(entry).strip(), "", "No keyword match", "", ""
                if not title:
                    continue
                if url and (url in saved or url in dismissed):
                    continue
                dedupe_key = url or f"{sid}:{title.casefold()}"
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                items.append({
                    "source": sid,
                    "source_name": sname,
                    "title": title,
                    "url": url,
                    "reason": reason,
                    "summary": summary,
                    "published_at": published_at,
                })
                if len(items) >= limit:
                    return items
    return items


def _health_html(source_id, health_map):
    info = (health_map or {}).get(source_id) or {}
    parts = []
    count = info.get("articles", 0)
    if count:
        latest = f" · latest {short_date(info['latest'])}" if info.get("latest") else ""
        parts.append(f"<span>{count} {'article' if count == 1 else 'articles'}{escape(latest)}</span>")
    else:
        parts.append("<span>0 saved</span>")
    if info.get("paused"):
        parts.append(f'<span class="source-badge source-paused">Paused: {escape(info["paused"])}</span>')
    elif info.get("pages_blocked"):
        parts.append('<span class="source-badge source-warn">Pages blocked (using feed)</span>')
    return " · ".join(parts)


def _safe_http_url(url):
    parsed = urlsplit(url or "")
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _render_review_panel(sources, excluded, filters):
    source_map = {s.id: s for s in sources}
    effective_filters = filters or {
        "include_keywords": list(DEFAULT_INCLUDE_KEYWORDS),
        "exclude_keywords": list(DEFAULT_EXCLUDE_KEYWORDS),
        "is_custom": False,
        "defaults": {
            "include_keywords": list(DEFAULT_INCLUDE_KEYWORDS),
            "exclude_keywords": list(DEFAULT_EXCLUDE_KEYWORDS),
        },
    }
    excluded = list(excluded or [])
    inc_count = len(effective_filters.get("include_keywords", []))
    exc_count = len(effective_filters.get("exclude_keywords", []))

    # Group counts by source for quick filter pills.
    by_source = {}
    for item in excluded:
        sid = item.get("source") or ""
        sname = item.get("source_name") or (source_map[sid].name if sid in source_map else sid or "Other")
        info = by_source.setdefault(sid, {"id": sid, "name": sname, "count": 0, "relevance": getattr(source_map.get(sid), "relevance", "auto")})
        info["count"] += 1

    pills = [
        f'<button type="button" class="excluded-source-pill is-active" data-excluded-source="" aria-pressed="true">'
        f'All <span class="excluded-pill-count">{len(excluded)}</span></button>'
    ]
    for info in sorted(by_source.values(), key=lambda x: (-x["count"], x["name"].casefold())):
        pills.append(
            f'<button type="button" class="excluded-source-pill" data-excluded-source="{escape(info["id"], quote=True)}" '
            f'data-source-name="{escape(info["name"], quote=True)}" data-source-relevance="{escape(info["relevance"], quote=True)}" aria-pressed="false">'
            f'{escape(info["name"])} <span class="excluded-pill-count">{info["count"]}</span></button>'
        )

    ex_rows = []
    for item in excluded:
        sid = item.get("source") or ""
        sname = item.get("source_name") or (source_map[sid].name if sid in source_map else sid or "Source")
        title = item.get("title") or ""
        url = _safe_http_url(item.get("url") or "")
        reason = item.get("reason") or "No keyword match"
        summary = item.get("summary") or ""
        title_html = (
            f'<a class="excluded-title-link" href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(title)} <span aria-hidden="true">↗</span></a>'
            if url
            else f'<span class="excluded-title-link">{escape(title)}</span>'
        )
        summary_html = f'<p class="excluded-snippet muted">{escape(summary)}</p>' if summary else ""
        import_btn = (
            f'<button type="button" class="source-check excluded-import-btn" data-import-excluded="{escape(url, quote=True)}">Import</button>'
            if url
            else ""
        )
        dismiss_btn = (
            f'<button type="button" class="excluded-dismiss-btn" data-dismiss-excluded="{escape(url, quote=True)}" '
            f'aria-label="Dismiss {escape(title, quote=True)}" title="Dismiss from review">×</button>'
            if url
            else ""
        )
        ex_rows.append(
            f'<li class="excluded-row" data-source="{escape(sid, quote=True)}" data-url="{escape(url, quote=True)}" '
            f'data-title="{escape(title, quote=True)}" data-summary="{escape(summary, quote=True)}" data-reason="{escape(reason, quote=True)}">'
            f'<div class="excluded-row-main">'
            f'<div class="excluded-row-top">{title_html}</div>'
            f'<div class="excluded-row-meta">'
            f'<span class="excluded-source-tag">{escape(sname)}</span>'
            f'<span class="excluded-reason-tag">{escape(reason)}</span>'
            f'<span class="excluded-match-tag" hidden></span>'
            f'</div>'
            f'{summary_html}'
            f'<div class="excluded-quick-terms" hidden></div>'
            f'</div>'
            f'<div class="excluded-row-actions">'
            f'<button type="button" class="excluded-tune-btn" data-suggest-keywords title="Add a keyword from this article">+ Keyword</button>'
            f'{import_btn}'
            f'{dismiss_btn}'
            f'</div>'
            f'</li>'
        )

    badge_cls = "sources-review-badge has-items" if excluded else "sources-review-badge"
    badge_text = f"{len(excluded)} filtered out" if excluded else "All caught up"
    filters_json = escape(json.dumps(effective_filters), quote=True)

    review_body = (
        f'<div class="excluded-toolbar">'
        f'<div class="excluded-source-pills" id="excluded-source-pills">{"".join(pills)}</div>'
        f'<div class="excluded-toolbar-actions">'
        f'<button type="button" id="excluded-keep-source-all" class="source-check" hidden></button>'
        f'<button type="button" id="excluded-dismiss-shown" class="excluded-secondary-btn">Dismiss shown</button>'
        f'</div>'
        f'</div>'
        f'<div id="excluded-matched-banner" class="excluded-matched-banner" hidden>'
        f'<span id="excluded-matched-text"></span>'
        f'<button type="button" id="excluded-import-matched" class="primary">Import matching</button>'
        f'</div>'
        f'<ul id="excluded-review-list" class="excluded-list">{"".join(ex_rows)}</ul>'
        f'<p id="excluded-empty-state" class="muted excluded-empty" hidden>No filtered articles left to review.</p>'
        if excluded
        else '<p id="excluded-empty-state" class="muted excluded-empty">No filtered articles waiting for review. Run <strong>Check all sources</strong> or tune your keywords below.</p>'
        '<ul id="excluded-review-list" class="excluded-list" hidden></ul>'
    )

    return (
        f'<section id="sources-review-panel" class="sources-review-panel" data-filters="{filters_json}" aria-label="Keyword filter and filtered articles review">'
        f'<div class="sources-review-bar">'
        f'<div class="sources-review-title">'
        f'<h2>Keyword filter &amp; review</h2>'
        f'<span id="excluded-count-badge" class="{badge_cls}">{escape(badge_text)}</span>'
        f'</div>'
        f'<div class="sources-review-toggles">'
        f'<button type="button" id="toggle-excluded-pane" class="sources-review-toggle" aria-expanded="false" aria-controls="excluded-review-pane">'
        f'Review filtered <span id="excluded-toggle-count" class="excluded-pill-count">{len(excluded)}</span>'
        f'</button>'
        f'<button type="button" id="toggle-keywords-pane" class="sources-review-toggle" aria-expanded="false" aria-controls="keyword-tuning-pane">'
        f'Tune keywords <span id="keywords-toggle-count" class="excluded-pill-count">{inc_count} · {exc_count}</span>'
        f'</button>'
        f'</div>'
        f'</div>'
        f'<div id="keyword-tuning-pane" class="keyword-tuning-pane" hidden>'
        f'<div class="keyword-columns">'
        f'<div class="keyword-group">'
        f'<div class="keyword-group-head"><h3>Include keywords</h3><span class="muted">Keep posts matching any term in title or summary</span></div>'
        f'<div id="include-keyword-chips" class="keyword-chips"></div>'
        f'<form id="include-keyword-form" class="keyword-add-form">'
        f'<label class="sr-only" for="include-keyword-input">Add include keyword</label>'
        f'<input id="include-keyword-input" type="text" maxlength="80" placeholder="Add keyword (e.g. siem, guardrails, agentic soc)" autocomplete="off" />'
        f'<button type="submit">Add</button>'
        f'</form>'
        f'</div>'
        f'<div class="keyword-group">'
        f'<div class="keyword-group-head"><h3>Exclude phrases</h3><span class="muted">Skip posts whose title matches any phrase</span></div>'
        f'<div id="exclude-keyword-chips" class="keyword-chips"></div>'
        f'<form id="exclude-keyword-form" class="keyword-add-form">'
        f'<label class="sr-only" for="exclude-keyword-input">Add exclude phrase</label>'
        f'<input id="exclude-keyword-input" type="text" maxlength="80" placeholder="Add exclude phrase (e.g. webinar, ipo, legal)" autocomplete="off" />'
        f'<button type="submit">Add</button>'
        f'</form>'
        f'</div>'
        f'</div>'
        f'<div class="keyword-tuning-footer">'
        f'<p id="keyword-tuning-status" class="muted" role="status" aria-live="polite"></p>'
        f'<button type="button" id="keyword-reset-defaults" class="excluded-secondary-btn"{" hidden" if not effective_filters.get("is_custom") else ""}>Reset to defaults</button>'
        f'</div>'
        f'</div>'
        f'<div id="excluded-review-pane" class="excluded-review-pane" hidden>'
        f'{review_body}'
        f'</div>'
        f'</section>'
    )


def render_sources_list(sources, csrf_token="", config_label="Your source settings", health=None, excluded=None, filters=None):
    """Render configured sources supplied by the backend."""
    groups = {}
    for source in sources:
        groups.setdefault(source.source_type, []).append(source)
    sections = []
    for kind, entries in groups.items():
        cards = []
        for source in entries:
            links = []
            for label, url in [
                ("Article index", source.parent_url),
                ("RSS feed", source.rss_url),
            ]:
                if url:
                    links.append(
                        render_template(
                            "sources/source-link.html",
                            url=escape(url, quote=True),
                            label=label,
                        )
                    )
            status = "Enabled" if source.enabled else "Disabled"
            remove_btn = (
                f'<button type="button" class="source-remove" data-remove-source="{escape(source.id, quote=True)}">Remove</button>'
                if source.source_type == "custom"
                else ""
            )
            rel_auto = "selected" if getattr(source, "relevance", "auto") != "all" else ""
            rel_all = "selected" if getattr(source, "relevance", "auto") == "all" else ""
            cards.append(
                render_template(
                    "sources/source-row.html",
                    source_name=escape(source.name),
                    source_id=escape(source.id, quote=True),
                    checked_attribute="checked" if source.enabled else "",
                    status_class=status.lower(),
                    status=status,
                    source_links=" · ".join(links),
                    source_notes=escape(source.notes, quote=True),
                    source_health=_health_html(source.id, health),
                    relevance_auto=rel_auto,
                    relevance_all=rel_all,
                    remove_button=remove_btn,
                )
            )
        sections.append(
            render_template(
                "sources/group.html",
                kind=escape(kind.replace("_", " ").capitalize().replace("Ai ", "AI ")),
                source_rows="".join(cards),
            )
        )
    review_section = _render_review_panel(sources, excluded, filters)
    raw_config = str(config_label or "").strip()
    if "/" in raw_config or "\\" in raw_config:
        short_name = Path(raw_config).name or raw_config
        config_badge = (
            '<span aria-hidden="true">·</span>'
            f'<code class="sources-config-chip" title="{escape(raw_config, quote=True)}">{escape(short_name)}</code>'
        )
    elif raw_config and raw_config != "Your source settings":
        config_badge = f'<span aria-hidden="true">·</span><span>{escape(raw_config)}</span>'
    else:
        config_badge = ""
    return render_template(
        "sources/index.html",
        csrf_token=escape(csrf_token, quote=True),
        source_count=len(sources),
        enabled_count=sum(source.enabled for source in sources),
        config_badge=config_badge,
        review_section=review_section,
        source_groups="".join(sections),
    )
