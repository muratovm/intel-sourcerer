"""Global indicator pivot directory, lifecycle aging, false-positive ignorelist, and manual IOCs."""
from .article_links import article_href
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import quote, urlencode

from .correlation import refresh_index
from .iocs import _attack_names, kev_info
from .library import short_date
from .sessions import SessionError
from .templates import render_template

KIND_TITLES = {
    "cves": "CVEs",
    "techniques": "MITRE ATT&CK",
    "entities": "Groups & Software",
    "hashes": "Hashes",
    "domains": "Domains",
    "ips": "IPs",
    "files": "Files",
    "registry": "Registry",
}
VALID_KINDS = frozenset(KIND_TITLES)
STALE_NETWORK_DAYS = 180


def _now():
    return datetime.now(timezone.utc).isoformat()


def normalize_indicator_value(kind, value):
    if kind not in VALID_KINDS:
        raise SessionError("Unsupported indicator kind.")
    if not isinstance(value, str):
        raise SessionError("Enter an indicator value.")
    cleaned = " ".join(value.split()).strip()
    if not cleaned or len(cleaned) > 250 or not cleaned.isprintable():
        raise SessionError("Indicator value must be 1-250 printable characters.")
    if kind in {"cves", "techniques", "entities"}:
        return cleaned.upper().replace("/", ".")
    if kind in {"hashes", "domains", "files", "registry"}:
        return cleaned.lower()
    return cleaned


def ignore_indicator(db, kind, value):
    norm = normalize_indicator_value(kind, value)
    db.execute(
        "INSERT OR IGNORE INTO ignored_indicators(kind, value, created_at) VALUES (?,?,?)",
        (kind, norm, _now()),
    )
    return {"kind": kind, "value": norm, "ignored": True}


def unignore_indicator(db, kind, value):
    norm = normalize_indicator_value(kind, value)
    db.execute("DELETE FROM ignored_indicators WHERE kind=? AND value=?", (kind, norm))
    return {"kind": kind, "value": norm, "ignored": False}


def list_ignored_indicators(db):
    return [
        {"kind": row["kind"], "value": row["value"], "created_at": row["created_at"]}
        for row in db.execute("SELECT kind, value, created_at FROM ignored_indicators ORDER BY created_at DESC, value")
    ]


def add_manual_indicator(db, url, kind, value):
    if not isinstance(url, str) or not db.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone():
        raise SessionError("Article not found.")
    norm = normalize_indicator_value(kind, value)
    now = _now()
    db.execute(
        "INSERT OR IGNORE INTO manual_indicators(article_url, kind, value, created_at) VALUES (?,?,?,?)",
        (url, kind, norm, now),
    )
    db.execute(
        "INSERT OR IGNORE INTO article_indicators(article_url, kind, value) VALUES (?,?,?)",
        (url, kind, norm),
    )
    # If the analyst manually adds a previously ignored indicator, un-ignore it so it shows up.
    db.execute("DELETE FROM ignored_indicators WHERE kind=? AND value=?", (kind, norm))
    return {"url": url, "kind": kind, "value": norm}


def delete_manual_indicator(db, url, kind, value):
    norm = normalize_indicator_value(kind, value)
    db.execute(
        "DELETE FROM manual_indicators WHERE article_url=? AND kind=? AND value=?",
        (url, kind, norm),
    )
    # Invalidate indicator index state for this article so re-extraction drops non-present values.
    db.execute("DELETE FROM indicator_index_state WHERE article_url=?", (url,))
    return {"url": url, "kind": kind, "value": norm, "deleted": True}


def apply_indicator_curation(db, url, iocs):
    """Filter out analyst-ignored false positives and merge manual IOCs for an article."""
    ignored = {(row["kind"], row["value"]) for row in db.execute("SELECT kind, value FROM ignored_indicators")}
    manual = [
        (row["kind"], row["value"])
        for row in db.execute("SELECT kind, value FROM manual_indicators WHERE article_url=? ORDER BY created_at", (url,))
    ]
    names, retired = _attack_names()
    curated = {}
    for kind, items in iocs.items():
        kept = []
        for item in items:
            key = item["value"].lower() if kind in {"files", "registry", "domains", "hashes"} else item["value"]
            if (kind, key) in ignored:
                continue
            kept.append(item)
        curated[kind] = kept

    for kind, val in manual:
        if (kind, val) in ignored or kind not in curated:
            continue
        existing = {
            (item["value"].lower() if kind in {"files", "registry", "domains", "hashes"} else item["value"])
            for item in curated[kind]
        }
        if val in existing:
            for item in curated[kind]:
                cmp_val = item["value"].lower() if kind in {"files", "registry", "domains", "hashes"} else item["value"]
                if cmp_val == val:
                    item["manual"] = True
            continue
        entry = {"value": val, "manual": True}
        if kind == "cves":
            kinfo = kev_info(val)
            if kinfo is not None:
                entry["kev"] = True
                if kinfo.get("ransomware"):
                    entry["ransomware"] = True
        elif kind in {"techniques", "entities"} and val in names:
            entry["name"] = names[val]
            if val in retired:
                entry["retired"] = True
        elif kind == "hashes":
            entry["type"] = {32: "MD5", 40: "SHA-1", 64: "SHA-256", 128: "SHA-512"}.get(len(val), "HASH")
        elif kind == "files":
            entry["common"] = False
        curated[kind].append(entry)
    return curated


def external_pivots(kind, value):
    """User-initiated external lookup URLs (zero automatic network requests)."""
    encoded = quote(value, safe="")
    if kind == "cves":
        return [
            ("NVD", f"https://nvd.nist.gov/vuln/detail/{encoded}"),
            ("CISA KEV", f"https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search_api_fulltext={encoded}"),
        ]
    if kind == "techniques":
        if value.startswith("AML."):
            return [("ATLAS", f"https://atlas.mitre.org/techniques/{encoded}")]
        section = "tactics" if value.startswith("TA") else "techniques"
        return [("ATT&CK", f"https://attack.mitre.org/{section}/{value.replace('.', '/')}/")]
    if kind == "entities":
        section = "groups" if value.startswith("G") else "software"
        return [("ATT&CK", f"https://attack.mitre.org/{section}/{encoded}/")]
    if kind == "hashes":
        return [
            ("VirusTotal", f"https://www.virustotal.com/gui/file/{encoded}"),
            ("MalwareBazaar", f"https://bazaar.abuse.ch/browse.php?search={encoded}"),
        ]
    if kind == "domains":
        return [
            ("VirusTotal", f"https://www.virustotal.com/gui/domain/{encoded}"),
            ("Shodan", f"https://www.shodan.io/search?query=hostname%3A{encoded}"),
        ]
    if kind == "ips":
        return [
            ("VirusTotal", f"https://www.virustotal.com/gui/ip-address/{encoded}"),
            ("Shodan", f"https://www.shodan.io/host/{encoded}"),
            ("GreyNoise", f"https://viz.greynoise.io/ip/{encoded}"),
        ]
    return []


def _parse_dt(value):
    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


SORTS = {"reports": "Most reports", "recent": "Recently seen", "first": "First seen", "value": "A to Z"}
WINDOWS = {"": "Any time", "7": "Last 7 days", "30": "Last 30 days", "90": "Last 90 days", "365": "Last year"}
PAGE = 50
MAX_LIMIT = 5000


def indicator_options(params):
    """Read the page's filters from query parameters, keeping only known values."""
    def first(name):
        return params.get(name, [""])[0]

    try:
        limit = min(MAX_LIMIT, max(PAGE, int(first("limit") or PAGE)))
    except ValueError:
        limit = PAGE
    return {
        "query": first("q"),
        "kind": first("kind") if first("kind") in VALID_KINDS else "",
        "kev_only": first("kev") == "1",
        "days": first("days") if first("days") in WINDOWS else "",
        "sort": first("sort") if first("sort") in SORTS else "reports",
        "limit": limit,
    }


def indicators_data(db, *, query="", kind="", kev_only=False, days="", sort="reports", limit=PAGE, now=None):
    """Return indexed indicators across the user's library with lifecycle dates, excluding ignored FPs."""
    remaining = refresh_index(db)
    names, retired = _attack_names()
    now = now or datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=STALE_NETWORK_DAYS)
    rows = db.execute(
        """SELECT i.kind, i.value, a.url, a.title, a.published_at, s.name AS publisher
        FROM article_indicators i
        JOIN articles a ON a.url=i.article_url
        JOIN sources s ON s.id=a.source_id
        WHERE NOT EXISTS (SELECT 1 FROM ignored_indicators ig WHERE ig.kind=i.kind AND ig.value=i.value)
        ORDER BY julianday(a.published_at) DESC, a.title COLLATE NOCASE"""
    ).fetchall()
    grouped = {}
    counts_by_kind = {}
    kev_total = 0
    for row in rows:
        key = (row["kind"], row["value"])
        entry = grouped.get(key)
        if entry is None:
            meta = {
                "kind": row["kind"],
                "value": row["value"],
                "articles": [],
                "first_seen": row["published_at"],
                "last_seen": row["published_at"],
            }
            if row["kind"] in {"techniques", "entities"} and row["value"] in names:
                meta["name"] = names[row["value"]]
                if row["value"] in retired:
                    meta["retired"] = True
            if row["kind"] == "cves":
                kinfo = kev_info(row["value"])
                if kinfo is not None:
                    meta["kev"] = True
                    if kinfo.get("ransomware"):
                        meta["ransomware"] = True
                    kev_total += 1
            grouped[key] = entry = meta
            counts_by_kind[row["kind"]] = counts_by_kind.get(row["kind"], 0) + 1
        if row["published_at"] < entry["first_seen"]:
            entry["first_seen"] = row["published_at"]
        if row["published_at"] > entry["last_seen"]:
            entry["last_seen"] = row["published_at"]
        entry["articles"].append(
            {
                "url": row["url"],
                "title": row["title"],
                "published_at": row["published_at"],
                "publisher": row["publisher"],
            }
        )
    for entry in grouped.values():
        if entry["kind"] in {"ips", "domains"}:
            last_dt = _parse_dt(entry["last_seen"])
            if last_dt and last_dt < stale_cutoff:
                entry["stale"] = True

    terms = query.casefold().split()
    items = []
    for entry in grouped.values():
        if kind and entry["kind"] != kind:
            continue
        if kev_only and not entry.get("kev"):
            continue
        if days:
            seen = _parse_dt(entry["last_seen"])
            if not seen or seen < now - timedelta(days=int(days)):
                continue
        haystack = " ".join(
            [
                entry["value"],
                entry.get("name", ""),
                entry["kind"],
                *(a["title"] + " " + a["publisher"] for a in entry["articles"]),
            ]
        ).casefold()
        if terms and not all(term in haystack for term in terms):
            continue
        items.append(entry)
    if sort == "recent":
        items.sort(key=lambda item: (item.get("last_seen", ""), len(item["articles"])), reverse=True)
    elif sort == "first":
        items.sort(key=lambda item: (item.get("first_seen", ""), item["value"]))
    elif sort == "value":
        items.sort(key=lambda item: item["value"].casefold())
    else:
        items.sort(
            key=lambda item: (
                len(item["articles"]),
                bool(item.get("kev")),
                not bool(item.get("stale")),
                item.get("last_seen", ""),
                item["value"],
            ),
            reverse=True,
        )
    return {
        "complete": remaining == 0,
        "total": len(items),
        "kev_total": kev_total,
        "counts": counts_by_kind,
        "indicators": items[:limit],
        "ignored": list_ignored_indicators(db),
    }


def _short_value(kind, value):
    """Long hashes keep both ends, which is what people compare."""
    return value[:10] + "…" + value[-10:] if kind == "hashes" and len(value) > 24 else value


def _menu(label, current_label, options, href, current):
    """A dropdown in the same shape as the Library's Tags and Type menus."""
    links = "".join(
        f'<a class="lib-tag-option{" active" if key == current else ""}"'
        f'{" aria-current=true" if key == current else ""} href="{href(key)}">{escape(text)}'
        f'{f" <span>{count}</span>" if count is not None else ""}</a>'
        for key, text, count in options
    )
    summary = f"{label}: {escape(current_label)}"
    return f'<details class="lib-tag-menu ind-menu"><summary>{summary}</summary><div class="lib-tag-list">{links}</div></details>'


def render_indicators_page(data, *, options=None, query="", kind="", kev_only=False, csrf_token=""):
    """Render the /indicators page: one filter row, a table grouped by type, and a detail panel."""
    import json

    options = dict(options or {"query": query, "kind": kind, "kev_only": kev_only})
    options.setdefault("days", "")
    options.setdefault("sort", "reports")
    options.setdefault("limit", PAGE)
    query, kind, kev_only = options.get("query", ""), options.get("kind", ""), options.get("kev_only", False)

    def href(**changes):
        state = {"q": query, "kind": kind, "kev": "1" if kev_only else "", "days": options["days"],
                 "sort": "" if options["sort"] == "reports" else options["sort"]}
        state.update(changes)
        state = {k: v for k, v in state.items() if v}
        return escape("/indicators" + ("?" + urlencode(state) if state else ""), quote=True)

    counts = data.get("counts", {})
    type_options = [("", "All", sum(counts.values()))] + [
        (code, label, counts[code]) for code, label in KIND_TITLES.items() if counts.get(code)
    ]
    type_menu = _menu("Type", KIND_TITLES.get(kind, "All"), type_options, lambda key: href(kind=key), kind)
    kev_toggle = (
        f'<a class="ind-toggle{" active" if kev_only else ""}" href="{href(kev="" if kev_only else "1")}"'
        f'{" aria-pressed=true" if kev_only else " aria-pressed=false"}>CISA KEV'
        f' <span>{data.get("kev_total", 0)}</span></a>'
        if data.get("kev_total") or kev_only else ""
    )
    seen_menu = _menu("Seen", WINDOWS[options["days"]], [(k, v, None) for k, v in WINDOWS.items()],
                      lambda key: href(days=key), options["days"])
    sort_menu = _menu("Sort", SORTS[options["sort"]], [(k, v, None) for k, v in SORTS.items()],
                      lambda key: href(sort="" if key == "reports" else key), options["sort"])

    shown = data.get("indicators", [])
    details = []
    groups = {}
    for item in shown:
        groups.setdefault(item["kind"], []).append(item)
    rows_html = []
    # Under one type the header would only repeat the filter, so groups show only for "All".
    order = [code for code in KIND_TITLES if code in groups] + [code for code in groups if code not in KIND_TITLES]
    for code in order:
        if not kind:
            rows_html.append(f'<tr class="ind-group"><th colspan="5" scope="rowgroup">{escape(KIND_TITLES.get(code, code))}'
                             f' <span>{counts.get(code, len(groups[code]))}</span></th></tr>')
        for item in groups[code]:
            index = len(details)
            flags = []
            if item.get("kev"):
                flags.append('<span class="ind-flag ind-kev" title="Listed in CISA Known Exploited Vulnerabilities">KEV</span>')
            if item.get("ransomware"):
                flags.append('<span class="ind-flag ind-ransom" title="Known ransomware campaign use">Ransomware</span>')
            if item.get("stale"):
                flags.append('<span class="ind-flag" title="Last seen more than 180 days ago">Stale</span>')
            if item.get("retired"):
                flags.append('<span class="ind-flag" title="Retired in ATT&amp;CK">Retired</span>')
            name = f'<span class="ind-name">{escape(item["name"])}</span>' if item.get("name") else ""
            count = len(item["articles"])
            rows_html.append(
                f'<tr class="ind-row" data-index="{index}" tabindex="0" aria-selected="false">'
                f'<td class="ind-check"><input type="checkbox" class="ind-pick" aria-label="Select {escape(item["value"], quote=True)}"></td>'
                f'<td class="ind-value" title="{escape(item["value"], quote=True)}"><code>{escape(_short_value(item["kind"], item["value"]))}</code>{name}</td>'
                f'<td class="ind-flags">{"".join(flags)}</td>'
                f'<td class="ind-num">{count}</td>'
                f'<td class="ind-num ind-date">{escape(short_date(item.get("last_seen", "")))}</td></tr>'
            )
            details.append({
                "kind": item["kind"],
                "kindLabel": KIND_TITLES.get(item["kind"], item["kind"]),
                "value": item["value"],
                "name": item.get("name", ""),
                "kev": bool(item.get("kev")),
                "ransomware": bool(item.get("ransomware")),
                "stale": bool(item.get("stale")),
                "first": item.get("first_seen", ""),
                "last": item.get("last_seen", ""),
                "pivots": [{"label": label, "url": url} for label, url in external_pivots(item["kind"], item["value"])],
                "articles": [
                    {"href": article_href(art["url"], art["title"]), "title": art["title"],
                     "publisher": art["publisher"], "date": art["published_at"]}
                    for art in item["articles"]
                ],
            })
    total = data.get("total", 0)
    if rows_html:
        table = (
            '<div class="ind-selbar" hidden><span class="ind-selcount" role="status"></span>'
            '<button type="button" class="ind-copy-selected">Copy to clipboard</button>'
            '<button type="button" class="ind-clear-selected">Clear</button></div>'
            '<table class="ind-table"><colgroup><col class="ind-c-check"><col class="ind-c-value"><col class="ind-c-flags"><col class="ind-c-num"><col class="ind-c-date"></colgroup>'
            '<thead><tr><th scope="col" class="ind-check"><input type="checkbox" class="ind-pick-all" aria-label="Select all shown"></th><th scope="col">Indicator</th><th scope="col">Flags</th><th scope="col" class="ind-num">Reports</th>'
            '<th scope="col" class="ind-num">Last seen</th></tr></thead><tbody>' + "".join(rows_html) + "</tbody></table>"
        )
        more = ""
        if total > len(shown):
            more = f' · <a href="{href(limit=str(options["limit"] + 100))}">Show 100 more</a>'
        footer = f'<p class="ind-count">Showing {len(shown):,} of {total:,}{more}</p>'
    else:
        table = '<p class="muted">No indicators match these filters.</p>'
        footer = ""

    ignored = data.get("ignored", [])
    ignored_section = ""
    if ignored:
        ig_rows = "".join(
            f'<li><code>{escape(item["value"])}</code> <span class="muted">{escape(KIND_TITLES.get(item["kind"], item["kind"]))}</span> '
            f'<button type="button" class="ioc-ignore-btn" data-unignore-kind="{escape(item["kind"], quote=True)}" '
            f'data-unignore-value="{escape(item["value"], quote=True)}">Restore</button></li>'
            for item in ignored
        )
        ignored_section = (
            f'<details class="ignored-drawer"><summary>Ignored false positives ({len(ignored)})</summary>'
            f'<ul class="ignored-list">{ig_rows}</ul></details>'
        )

    status = "" if data.get("complete", True) else "Still indexing older articles. Reload shortly for complete results."
    # A JSON island the panel script reads; "<" is escaped so report titles can't close the script tag.
    payload = json.dumps(details, ensure_ascii=False).replace("<", "\\u003c")
    return render_template(
        "indicators/index.html",
        csrf_token=escape(csrf_token, quote=True),
        query=escape(query, quote=True),
        kind=escape(kind, quote=True),
        kev="1" if kev_only else "",
        days=escape(options["days"], quote=True),
        sort=escape("" if options["sort"] == "reports" else options["sort"], quote=True),
        summary=f"{total:,} matching indicators",
        filters=type_menu + kev_toggle + seen_menu + sort_menu,
        table=table,
        footer=footer,
        details_json=payload,
        ignored_section=ignored_section,
        status=escape(status),
    )
