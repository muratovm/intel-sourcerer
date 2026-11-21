"""Sources page rendering, per-source health diagnostics, and filtered-out audit drawer."""

from datetime import datetime, timezone
from html import escape
import json

from .library import short_date
from .templates import render_template


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


def recent_excluded(db, limit=60):
    """Return articles filtered out by keyword rules in the most recent import run (excluding any since imported)."""
    row = db.execute("SELECT report_json FROM import_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return []
    try:
        report = json.loads(row["report_json"])
    except (TypeError, ValueError):
        return []
    saved = {r[0] for r in db.execute("SELECT url FROM articles")}
    items = []
    for src in report.get("sources", []):
        sid = src.get("id", "")
        for entry in src.get("excluded", []):
            if isinstance(entry, dict):
                title, url = entry.get("title", ""), entry.get("url", "")
            else:
                title, url = str(entry), ""
            if url and url in saved:
                continue
            if title:
                items.append({"source": sid, "title": title, "url": url})
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


def render_sources_list(sources, csrf_token="", config_label="Your source settings", health=None, excluded=None):
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
    excluded_html = ""
    if excluded:
        ex_rows = []
        for item in excluded:
            action = (
                f'<button type="button" class="source-check" data-import-excluded="{escape(item["url"], quote=True)}">Import anyway</button>'
                if item.get("url")
                else ""
            )
            ex_rows.append(
                f'<li><span><strong>{escape(item["title"])}</strong> <span class="muted">· {escape(item["source"])}</span></span>{action}</li>'
            )
        excluded_html = (
            f'<details class="excluded-drawer"><summary>Filtered out in last refresh ({len(excluded)})</summary>'
            f'<p class="muted">Posts skipped by keyword rules. Import any you want, or switch a source to "Keep all posts" above.</p>'
            f'<ul class="excluded-list">{"".join(ex_rows)}</ul></details>'
        )
    return render_template(
        "sources/index.html",
        csrf_token=escape(csrf_token, quote=True),
        source_count=len(sources),
        enabled_count=sum(source.enabled for source in sources),
        config_path=escape(str(config_label)),
        source_groups="".join(sections),
        excluded_section=excluded_html,
    )
