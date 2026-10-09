"""Sources page rendering, independent of configuration storage."""

from html import escape

from .templates import render_template


def render_sources_list(sources, csrf_token="", config_label="Your source settings"):
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
                ("Legacy index", source.legacy_parent_url),
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
            cards.append(
                render_template(
                    "sources/source-row.html",
                    source_name=escape(source.name),
                    source_id=escape(source.id, quote=True),
                    checked_attribute="checked" if source.enabled else "",
                    status_class=status.lower(),
                    status=status,
                    source_links=" · ".join(links),
                    source_parent_url=escape(source.parent_url),
                    source_notes=escape(source.notes),
                )
            )
        sections.append(
            render_template(
                "sources/group.html",
                kind=escape(kind),
                source_rows="".join(cards),
            )
        )
    return render_template(
        "sources/index.html",
        csrf_token=escape(csrf_token, quote=True),
        source_count=len(sources),
        enabled_count=sum(source.enabled for source in sources),
        config_path=escape(str(config_label)),
        source_groups="".join(sections),
    )
