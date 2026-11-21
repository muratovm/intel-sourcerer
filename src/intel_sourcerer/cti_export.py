"""Actionable CTI exports: Topic Markdown dossiers, MITRE ATT&CK Navigator layers, IOC CSV/JSON, and STIX 2.1 bundles."""
import csv
from datetime import datetime, timezone
from io import StringIO
import json
from urllib.parse import parse_qs, urlsplit
import uuid

from bs4 import BeautifulSoup
from markdownify import markdownify

from .content import render_markdown
from .correlation import refresh_index
from .iocs import _attack_names, extract_iocs, iocs_markdown, kev_info

STIX_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _stix_id(stix_type, seed):
    return f"{stix_type}--{uuid.uuid5(STIX_NAMESPACE, f'{stix_type}:{seed}')}"


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _stix_pattern(kind, value):
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    if kind == "hashes":
        algo = {32: "MD5", 40: "'SHA-1'", 64: "'SHA-256'", 128: "'SHA-512'"}.get(len(value), "'SHA-256'")
        return f"[file:hashes.{algo} = '{escaped}']"
    if kind == "ips":
        return f"[ipv4-addr:value = '{escaped}']"
    if kind == "domains":
        return f"[domain-name:value = '{escaped}']"
    if kind == "urls":
        return f"[url:value = '{escaped}']"
    if kind == "files":
        return f"[file:name = '{escaped}']"
    if kind == "registry":
        return f"[windows-registry-key:key = '{escaped}']"
    return None


def export_iocs_csv(iocs):
    """Export non-common IOCs as UTF-8 CSV ready for SIEM/EDR import."""
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["kind", "subtype", "value", "name", "kev"])
    for item in iocs.get("cves", []):
        writer.writerow(["cve", "", item["value"], "", "yes" if item.get("kev") else ""])
    for item in iocs.get("techniques", []):
        writer.writerow(["attack", "technique", item["value"], item.get("name", ""), ""])
    for item in iocs.get("entities", []):
        kind = "group" if item["value"].startswith("G") else "software"
        writer.writerow(["attack", kind, item["value"], item.get("name", ""), ""])
    for item in iocs.get("hashes", []):
        writer.writerow(["hash", item.get("type", ""), item["value"], "", ""])
    for item in iocs.get("domains", []):
        writer.writerow(["domain", "", item["value"], "", ""])
    for item in iocs.get("ips", []):
        writer.writerow(["ip", "ipv4", item["value"], "", ""])
    for item in iocs.get("urls", []):
        writer.writerow(["url", "", item["value"], "", ""])
    for item in iocs.get("files", []):
        if not item.get("common"):
            writer.writerow(["file", "", item["value"], "", ""])
    for item in iocs.get("registry", []):
        writer.writerow(["registry", "", item["value"], "", ""])
    return buf.getvalue()


def export_iocs_stix(title, iocs, source_url=None):
    """Build a valid STIX 2.1 JSON bundle from extracted IOCs."""
    stamp = _now_iso()
    objects = []
    report_refs = []

    for item in iocs.get("cves", []):
        val = item["value"]
        obj_id = _stix_id("vulnerability", val)
        report_refs.append(obj_id)
        obj = {
            "type": "vulnerability",
            "spec_version": "2.1",
            "id": obj_id,
            "created": stamp,
            "modified": stamp,
            "name": val,
            "external_references": [{"source_name": "cve", "external_id": val}],
        }
        if item.get("kev"):
            obj["labels"] = ["cisa-kev"]
        objects.append(obj)

    for item in iocs.get("techniques", []):
        val = item["value"]
        obj_id = _stix_id("attack-pattern", val)
        report_refs.append(obj_id)
        objects.append(
            {
                "type": "attack-pattern",
                "spec_version": "2.1",
                "id": obj_id,
                "created": stamp,
                "modified": stamp,
                "name": item.get("name") or val,
                "external_references": [{"source_name": "mitre-attack", "external_id": val}],
            }
        )

    for kind in ("hashes", "domains", "ips", "urls", "files", "registry"):
        for item in iocs.get(kind, []):
            if kind == "files" and item.get("common"):
                continue
            val = item["value"]
            pattern = _stix_pattern(kind, val)
            if not pattern:
                continue
            obj_id = _stix_id("indicator", f"{kind}:{val}")
            report_refs.append(obj_id)
            objects.append(
                {
                    "type": "indicator",
                    "spec_version": "2.1",
                    "id": obj_id,
                    "created": stamp,
                    "modified": stamp,
                    "name": f"{kind[:-1] if kind.endswith('s') else kind}: {val}",
                    "pattern": pattern,
                    "pattern_type": "stix",
                    "valid_from": stamp,
                }
            )

    if report_refs:
        report = {
            "type": "report",
            "spec_version": "2.1",
            "id": _stix_id("report", source_url or title),
            "created": stamp,
            "modified": stamp,
            "name": title,
            "published": stamp,
            "report_types": ["threat-report"],
            "object_refs": report_refs,
        }
        if source_url:
            report["external_references"] = [{"source_name": "source", "url": source_url}]
        objects.insert(0, report)

    return json.dumps(
        {
            "type": "bundle",
            "id": f"bundle--{uuid.uuid4()}",
            "objects": objects,
        },
        indent=2,
    ) + "\n"


def export_attack_navigator(name, technique_counts, description=""):
    """Build a MITRE ATT&CK Navigator v4.5 JSON layer scored by observation count."""
    max_score = max(technique_counts.values(), default=1)
    techniques = [
        {
            "techniqueID": tech_id,
            "score": count,
            "comment": f"Observed in {count} {'report' if count == 1 else 'reports'}",
            "enabled": True,
        }
        for tech_id, count in sorted(technique_counts.items())
        if tech_id.startswith("T") and not tech_id.startswith("TA")
    ]
    layer = {
        "name": name,
        "versions": {"attack": "16", "navigator": "5.1.0", "layer": "4.5"},
        "domain": "enterprise-attack",
        "description": description or f"ATT&CK techniques observed in {name} (exported from Intel Sourcerer)",
        "gradient": {
            "colors": ["#dbeafe", "#2563eb", "#1e3a8a"],
            "minValue": 1,
            "maxValue": max(2, max_score),
        },
        "techniques": techniques,
        "legendItems": [{"label": "Observed in saved reports", "color": "#2563eb"}],
    }
    return json.dumps(layer, indent=2) + "\n"


def _checklist_and_citations_markdown(html, article_titles):
    """Convert rich topic HTML notes into clean Markdown, resolving /?open= citations."""
    soup = BeautifulSoup(html or "", "html.parser")
    for item in soup.select("ul.checklist > li"):
        item.insert(0, "[x] " if item.get("data-checked") == "true" else "[ ] ")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if href.startswith("/?"):
            target = parse_qs(urlsplit(href).query).get("open", [""])[0]
            if target:
                anchor["href"] = target
                if not anchor.get_text(strip=True) and target in article_titles:
                    anchor.string = article_titles[target]
    return markdownify(str(soup)).strip()


def _topic_iocs(db, topic_id):
    """Aggregate deduplicated IOCs and technique counts across all articles in a topic."""
    refresh_index(db)
    names, retired = _attack_names()
    rows = db.execute(
        """SELECT i.kind, i.value, COUNT(DISTINCT i.article_url) AS count
        FROM topic_articles m
        JOIN article_indicators i ON i.article_url=m.article_url
        WHERE m.topic_id=?
          AND NOT EXISTS (SELECT 1 FROM ignored_indicators ig WHERE ig.kind=i.kind AND ig.value=i.value)
        GROUP BY i.kind, i.value
        ORDER BY count DESC, i.value""",
        (topic_id,),
    ).fetchall()
    iocs = {
        "cves": [],
        "techniques": [],
        "entities": [],
        "hashes": [],
        "domains": [],
        "ips": [],
        "urls": [],
        "files": [],
        "registry": [],
    }
    tech_counts = {}
    for row in rows:
        kind, value, count = row["kind"], row["value"], row["count"]
        if kind == "cves":
            entry = {"value": value, "count": count}
            if kev_info(value) is not None:
                entry["kev"] = True
            iocs["cves"].append(entry)
        elif kind == "techniques":
            entry = {"value": value, "count": count}
            if value in names:
                entry["name"] = names[value]
            if value in retired:
                entry["retired"] = True
            iocs["techniques"].append(entry)
            tech_counts[value] = count
        elif kind == "entities":
            entry = {"value": value, "count": count}
            if value in names:
                entry["name"] = names[value]
            iocs["entities"].append(entry)
        elif kind == "hashes":
            htype = {32: "MD5", 40: "SHA-1", 64: "SHA-256", 128: "SHA-512"}.get(len(value), "HASH")
            iocs["hashes"].append({"value": value, "type": htype, "count": count})
        elif kind == "files":
            iocs["files"].append({"value": value, "common": False, "count": count})
        elif kind in iocs:
            iocs[kind].append({"value": value, "count": count})
    return iocs, tech_counts


def export_article_format(db, url, fmt="markdown"):
    """Export an article in markdown, csv, json, stix, or navigator format. Returns (body, content_type, ext) or None."""
    from .library import export_article

    row = db.execute("SELECT * FROM articles WHERE url=?", (url,)).fetchone()
    if row is None:
        return None
    if fmt == "markdown":
        return export_article(db, url), "text/markdown; charset=utf-8", "md"
    from .indicators import apply_indicator_curation

    iocs = apply_indicator_curation(
        db, url, extract_iocs(row["content_html"] or render_markdown(row["content_markdown"] or ""))
    )
    if fmt == "csv":
        return export_iocs_csv(iocs), "text/csv; charset=utf-8", "csv"
    if fmt == "json":
        return json.dumps({"title": row["title"], "url": url, "iocs": iocs}, indent=2) + "\n", "application/json", "json"
    if fmt == "stix":
        return export_iocs_stix(row["title"], iocs, source_url=url), "application/stix+json; charset=utf-8", "stix.json"
    if fmt == "navigator":
        counts = {item["value"]: 1 for item in iocs.get("techniques", [])}
        return export_attack_navigator(row["title"], counts), "application/json", "navigator.json"
    raise ValueError("Unsupported export format.")


def export_topic_format(db, topic_id, fmt="markdown"):
    """Export a topic dossier in markdown, csv, json, stix, or navigator format. Returns (body, content_type, ext, name) or None."""
    topic = db.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
    if topic is None:
        return None
    articles = db.execute(
        """SELECT a.url, a.title, a.published_at, s.name AS publisher
        FROM topic_articles m
        JOIN articles a ON a.url=m.article_url
        JOIN sources s ON s.id=a.source_id
        WHERE m.topic_id=?
        ORDER BY julianday(a.published_at) ASC, a.title COLLATE NOCASE""",
        (topic_id,),
    ).fetchall()
    iocs, tech_counts = _topic_iocs(db, topic_id)
    name = topic["name"]
    if fmt == "csv":
        return export_iocs_csv(iocs), "text/csv; charset=utf-8", "csv", name
    if fmt == "json":
        payload = {
            "topic": name,
            "status": topic["status"],
            "articles": [dict(a) for a in articles],
            "iocs": iocs,
        }
        return json.dumps(payload, indent=2) + "\n", "application/json", "json", name
    if fmt == "stix":
        return export_iocs_stix(name, iocs), "application/stix+json; charset=utf-8", "stix.json", name
    if fmt == "navigator":
        return export_attack_navigator(name, tech_counts), "application/json", "navigator.json", name
    if fmt == "markdown":
        titles = {a["url"]: a["title"] for a in articles}
        notes_md = _checklist_and_citations_markdown(topic["notes"], titles)
        highlights = db.execute(
            """SELECT h.quote, a.title
            FROM topic_articles m
            JOIN article_highlights h ON h.article_url=m.article_url
            JOIN articles a ON a.url=m.article_url
            WHERE m.topic_id=?
            ORDER BY julianday(a.published_at) ASC, h.created_at""",
            (topic_id,),
        ).fetchall()
        lines = [
            f"# {name}",
            "",
            f"Status: {topic['status']} · {len(articles)} {'article' if len(articles) == 1 else 'articles'}",
            "",
        ]
        if notes_md:
            lines += ["## Synthesis notes", "", notes_md, ""]
        if articles:
            lines += ["## Timeline of articles", ""]
            for art in articles:
                lines.append(f"- {art['published_at'][:10]} — [{art['title']}]({art['url']}) ({art['publisher']})")
            lines.append("")
        ind_md = iocs_markdown(iocs)
        if ind_md:
            lines.append(ind_md)
        if highlights:
            lines += ["## Highlights", ""]
            for h in highlights:
                lines.append(f"> {h['quote'].replace(chr(10), chr(10) + '> ')}")
                lines.append(f"> — *{h['title']}*")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n", "text/markdown; charset=utf-8", "md", name
    raise ValueError("Unsupported export format.")
