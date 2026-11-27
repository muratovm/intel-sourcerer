"""Tests for CTI power features: network IOCs, CISA KEV, /indicators, exports, custom feeds, watchlists, backlinks, and AI assist."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import re
import threading

import httpx
import pytest

from intel_sourcerer import ai_assist
from intel_sourcerer.config import PublisherSource, add_custom_source, load_sources, remove_custom_source
from intel_sourcerer.cti_export import export_article_format, export_topic_format
from intel_sourcerer.feeds import Article
from intel_sourcerer.indicators import indicators_data, render_indicators_page
from intel_sourcerer.iocs import extract_iocs, iocs_markdown, kev_info
from intel_sourcerer.source_view import render_sources_list, source_health
from intel_sourcerer.storage import connect_database, save_article, save_source
from intel_sourcerer.topics import add_article, create_topic, save_topic_notes
from intel_sourcerer.update_kev import parse_kev
from intel_sourcerer.watchlists import add_watchlist, delete_watchlist, list_watchlists, match_watchlists
from intel_sourcerer.web import create_server


@contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _seed_article(db, url, title, html, *, source_id="s1", day=10):
    save_source(db, PublisherSource(source_id, "Vendor One", "https://vendor.example", "security_vendor"))
    art = Article(
        "Vendor One",
        title,
        url,
        url,
        datetime(2026, 10, day, tzinfo=timezone.utc),
        None,
        None,
        "<p>summary</p>",
    )
    save_article(
        db,
        source_id,
        art,
        content=title + " " + html,
        error=None,
        reason="r",
        discovered_at="2026-10-10",
        markdown=html,
        html=html,
    )


def test_network_iocs_registry_mitre_entities_and_kev():
    html = (
        "<p>APT29 (G0016) deployed Cobalt Strike (S0154) using T1059.001 to exploit CVE-2021-44228.</p>"
        "<p>Beacons reached 45[.]77[.]12[.]34, c2[.]evil-actor[.]xyz, and hxxps://drop[.]evil-actor[.]xyz/stage.bin.</p>"
        "<p>Private 10.0.0.1 and doc 198.51.100.5 and v1.2.3.4 are ignored, along with github.com.</p>"
        "<p>Persistence set at HKLM\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\\Updater.</p>"
    )
    iocs = extract_iocs(html)
    assert [x["value"] for x in iocs["ips"]] == ["45.77.12.34"]
    assert "c2.evil-actor.xyz" in [x["value"] for x in iocs["domains"]]
    assert "github.com" not in [x["value"] for x in iocs["domains"]]
    assert [x["value"] for x in iocs["urls"]] == ["https://drop.evil-actor.xyz/stage.bin"]
    assert any("CurrentVersion\\Run\\Updater" in x["value"] for x in iocs["registry"])
    assert {x["value"] for x in iocs["entities"]} == {"G0016", "S0154"}
    cve = iocs["cves"][0]
    assert cve["value"] == "CVE-2021-44228" and cve.get("kev") is True
    assert kev_info("CVE-2021-44228") is not None
    md = iocs_markdown(iocs)
    assert "(CISA KEV)" in md and "### Domains" in md and "### IP addresses" in md and "### Registry keys" in md


def test_kev_parser_filters_invalid_records():
    parsed = parse_kev(
        {
            "catalogVersion": "2026.11.20",
            "vulnerabilities": [
                {"cveID": "CVE-2024-12345", "vulnerabilityName": "Test <Flaw>", "knownRansomwareCampaignUse": "Known"},
                {"cveID": "NOT-A-CVE", "vulnerabilityName": "Ignored"},
            ],
        }
    )
    assert parsed["version"] == "2026.11.20"
    assert list(parsed["entries"]) == ["CVE-2024-12345"]
    assert parsed["entries"]["CVE-2024-12345"]["ransomware"] is True
    assert "<" not in parsed["entries"]["CVE-2024-12345"]["name"]


def test_indicators_directory_and_cti_exports(tmp_path):
    db_path = tmp_path / "cti.sqlite3"
    with closing(connect_database(db_path)) as db, db:
        u1 = "https://vendor.example/report-1"
        u2 = "https://vendor.example/report-2"
        _seed_article(
            db,
            u1,
            "Campaign Alpha",
            "<p>Exploits CVE-2021-44228 with T1059.001 and beacon 45[.]77[.]12[.]34 at evil-c2[.]xyz</p>",
            day=1,
        )
        _seed_article(
            db,
            u2,
            "Campaign Alpha Follow-up",
            "<p>Also uses T1059.001 and T1190 against CVE-2021-44228</p>",
            day=5,
        )
        data = indicators_data(db, kev_only=True)
        assert data["total"] == 1 and data["indicators"][0]["value"] == "CVE-2021-44228"
        page = render_indicators_page(data, kev_only=True)
        assert "CVE-2021-44228" in page and "KEV" in page and "Campaign Alpha" in page

        # Article exports: CSV, STIX 2.1, Navigator
        csv_body, ctype, ext = export_article_format(db, u1, "csv")
        assert ctype.startswith("text/csv") and ext == "csv" and "CVE-2021-44228" in csv_body and "45.77.12.34" in csv_body
        stix_body, _, _ = export_article_format(db, u1, "stix")
        bundle = json.loads(stix_body)
        assert bundle["type"] == "bundle"
        assert any(o["type"] == "vulnerability" and o["name"] == "CVE-2021-44228" for o in bundle["objects"])
        assert any(o["type"] == "indicator" and "45.77.12.34" in o["pattern"] for o in bundle["objects"])

        # Topic dossier & Navigator layer exports
        topic_id = create_topic(db, "Alpha Campaign")["id"]
        add_article(db, u1, topic_id)
        add_article(db, u2, topic_id)
        save_topic_notes(db, topic_id, '<p>Confirmed via <a href="/?open=https%3A%2F%2Fvendor.example%2Freport-1">Vendor One</a>.</p>', 0)
        dossier, _, _, _ = export_topic_format(db, topic_id, "markdown")
        assert "# Alpha Campaign" in dossier and "## Synthesis notes" in dossier and "CVE-2021-44228 (CISA KEV)" in dossier
        nav_body, _, _, _ = export_topic_format(db, topic_id, "navigator")
        layer = json.loads(nav_body)
        scores = {t["techniqueID"]: t["score"] for t in layer["techniques"]}
        assert scores["T1059.001"] == 2 and scores["T1190"] == 1


def test_custom_sources_health_and_watchlists(tmp_path):
    cfg = tmp_path / "sources.toml"
    cfg.write_text(
        'schema_version = 1\n\n[[sources]]\nid = "builtin"\nname = "Built In"\n'
        'source_type = "security_vendor"\nenabled = true\nparent_url = "https://example.com/"\n'
        'rss_url = "https://example.com/rss.xml"\nnotes = "Built-in"\n'
    )
    added = add_custom_source(cfg, "The DFIR Report", "https://thedfirreport.com/feed/")
    assert added.source_type == "custom" and added.id == "custom-the-dfir-report"
    assert len(load_sources(cfg)) == 2
    with pytest.raises(ValueError, match="already configured"):
        add_custom_source(cfg, "Dup", "https://thedfirreport.com/feed/")
    with pytest.raises(ValueError, match="Only custom"):
        remove_custom_source(cfg, "builtin")

    db_path = tmp_path / "w.sqlite3"
    with closing(connect_database(db_path)) as db, db:
        _seed_article(db, "https://vendor.example/ivanti", "Ivanti VPN zero-day", "<p>Affects Ivanti Connect Secure</p>", source_id="builtin")
        health = source_health(db)
        assert health["builtin"]["articles"] == 1
        html = render_sources_list(load_sources(cfg), health=health)
        assert "1 article" in html and "Remove" in html and "Add custom RSS/Atom feed" in html

        item = add_watchlist(db, "Ivanti")
        assert item["created"] is True
        terms = list_watchlists(db)
        assert match_watchlists(terms, "Ivanti VPN zero-day", "Vendor", "", []) == ["Ivanti"]
        delete_watchlist(db, item["id"])
        assert list_watchlists(db) == []

    remove_custom_source(cfg, added.id)
    assert len(load_sources(cfg)) == 1


def test_backlinks_indicators_route_and_ai_assist(tmp_path, monkeypatch):
    cfg = tmp_path / "sources.toml"
    cfg.write_text(
        'schema_version = 1\n\n[[sources]]\nid = "s1"\nname = "Vendor One"\n'
        'source_type = "security_vendor"\nenabled = true\nparent_url = "https://vendor.example/"\nnotes = ""\n'
    )
    db_path = tmp_path / "srv.sqlite3"
    u1 = "https://vendor.example/first"
    u2 = "https://vendor.example/second"
    with closing(connect_database(db_path)) as db, db:
        _seed_article(db, u1, "First Report", "<p>CVE-2021-44228 exploit analysis</p>")
        _seed_article(db, u2, "Second Report", "<p>Follow up</p>")
        db.execute(
            "UPDATE articles SET notes=?, notes_format='html' WHERE url=?",
            ('<p>See <a href="/?open=https%3A%2F%2Fvendor.example%2Ffirst">First</a></p>', u2),
        )

    # Mock both OpenAI-compatible and Anthropic providers
    monkeypatch.setenv("INTEL_AI_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("INTEL_AI_MODEL", "llama3.1")
    monkeypatch.delenv("INTEL_AI_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ai_assist.ai_status() == {"enabled": True, "provider": "openai", "model": "llama3.1", "local": True}

    original_client = httpx.Client

    def mock_openai(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "**BLUF:** Critical flaw."}}]})
        )
        return original_client(*args, **kwargs)

    monkeypatch.setattr(ai_assist.httpx, "Client", mock_openai)

    server = create_server(db_path, 0, cfg, auth_enabled=False)
    with running(server) as base_url:
        with original_client(base_url=base_url, trust_env=False) as client:
            home = client.get("/")
            csrf = re.search(r'name="csrf-token" content="([^"]+)"', home.text)[1]

            art = client.get("/api/article", params={"url": u1}).json()
            assert len(art["backlinks"]) == 1 and art["backlinks"][0]["title"] == "Second Report"

            ind = client.get("/indicators")
            assert ind.status_code == 200 and "CVE-2021-44228" in ind.text

            ai_resp = client.post("/api/ai-assist", json={"url": u1, "mode": "bluf"}, headers={"X-CSRF-Token": csrf})
            assert ai_resp.status_code == 200 and "BLUF" in ai_resp.json()["markdown"]

            # Test Anthropic provider branch too
            monkeypatch.setenv("INTEL_AI_PROVIDER", "anthropic")
            monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
            monkeypatch.delenv("INTEL_AI_URL", raising=False)

            def mock_anthropic(*args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(
                    lambda req: httpx.Response(200, json={"content": [{"type": "text", "text": "- `T1059.001` PowerShell"}]})
                )
                return original_client(*args, **kwargs)

            monkeypatch.setattr(ai_assist.httpx, "Client", mock_anthropic)
            ai_anth = client.post("/api/ai-assist", json={"url": u1, "mode": "ttps"}, headers={"X-CSRF-Token": csrf})
            assert ai_anth.status_code == 200 and "T1059.001" in ai_anth.json()["markdown"]


def test_ioc_curation_lifecycle_tracking_params_and_source_relevance(tmp_path):
    from intel_sourcerer.config import set_source_relevance
    from intel_sourcerer.correlation import related_reports
    from intel_sourcerer.feeds import _web_url
    from intel_sourcerer.importer import selection_reason
    from intel_sourcerer.indicators import (
        add_manual_indicator,
        apply_indicator_curation,
        delete_manual_indicator,
        ignore_indicator,
        unignore_indicator,
    )
    from intel_sourcerer.source_view import recent_excluded

    # Tracking parameters stripped during feed URL normalization
    assert _web_url("https://vendor.example/post?utm_source=rss&id=42&fbclid=xyz", "https://vendor.example/") == "https://vendor.example/post?id=42"

    # Per-source relevance override
    cfg = tmp_path / "sources.toml"
    cfg.write_text(
        'schema_version = 1\n\n[[sources]]\nid = "s1"\nname = "Vendor"\n'
        'source_type = "security_vendor"\nenabled = true\nparent_url = "https://vendor.example/"\nnotes = ""\n'
    )
    set_source_relevance(cfg, "s1", "all")
    src = load_sources(cfg)[0]
    assert src.relevance == "all"
    non_keyword = Article("Vendor", "Print Spooler RPC internals", "https://vendor.example/rpc", "rpc", datetime(2026, 10, 1, tzinfo=timezone.utc), None, None, "<p>details</p>")
    assert selection_reason(non_keyword, src) is not None

    # IOC ignorelist, manual IOCs, lifecycle aging, and excluded audit drawer
    db_path = tmp_path / "curation.sqlite3"
    with closing(connect_database(db_path)) as db, db:
        u1 = "https://vendor.example/old-ip"
        u2 = "https://vendor.example/new-report"
        _seed_article(db, u1, "Old Report", "<p>Beacon 45[.]77[.]12[.]34 and noisy-tool.exe</p>", day=1)
        db.execute("UPDATE articles SET published_at='2024-01-15T00:00:00+00:00' WHERE url=?", (u1,))
        _seed_article(db, u2, "New Report", "<p>Also mentions noisy-tool.exe</p>", day=20)
        db.commit()

        # Initially linked by noisy-tool.exe
        assert related_reports(db, u1)["related"] == [u2]

        # Ignore noisy-tool.exe as false positive -> disappears from related, indicators, and curated article IOCs
        ignore_indicator(db, "files", "noisy-tool.exe")
        assert related_reports(db, u1)["related"] == []
        curated = apply_indicator_curation(db, u1, extract_iocs("<p>Beacon 45[.]77[.]12[.]34 and noisy-tool.exe</p>"))
        assert curated["files"] == []

        # Lifecycle: old 2024 IP is marked stale (>180 days)
        ind = indicators_data(db, now=datetime(2026, 10, 25, tzinfo=timezone.utc))
        ip_entry = next(x for x in ind["indicators"] if x["value"] == "45.77.12.34")
        assert ip_entry.get("stale") is True and len(ind["ignored"]) == 1

        # Restore ignored indicator -> link returns
        unignore_indicator(db, "files", "noisy-tool.exe")
        assert related_reports(db, u1)["related"] == [u2]

        # Manual IOC added to u2 correlates with u1
        add_manual_indicator(db, u2, "ips", "45.77.12.34")
        curated_u2 = apply_indicator_curation(db, u2, extract_iocs("<p>Screenshot only</p>"))
        assert curated_u2["ips"] == [{"value": "45.77.12.34", "manual": True}]
        delete_manual_indicator(db, u2, "ips", "45.77.12.34")

        # Excluded audit drawer reads across recent import_runs and respects dismissals
        db.execute(
            "INSERT INTO import_runs(started_at,since,until,report_json) VALUES ('2026-10-20','2026-10-01','2026-10-20',?)",
            (json.dumps({"sources": [{"id": "s1", "excluded": [{"title": "Dropped Deep Dive", "url": "https://vendor.example/dropped"}]}]}),),
        )
        # Even after a subsequent single-source run with no excluded items, earlier excluded items stay reviewable.
        db.execute(
            "INSERT INTO import_runs(started_at,since,until,report_json) VALUES ('2026-10-21','2026-10-01','2026-10-21',?)",
            (json.dumps({"sources": [{"id": "s2", "excluded": []}]}),),
        )
        ex = recent_excluded(db)
        assert len(ex) == 1 and ex[0]["title"] == "Dropped Deep Dive"

        from intel_sourcerer.source_view import dismiss_excluded
        assert dismiss_excluded(db, ["https://vendor.example/dropped"]) == 1
        assert recent_excluded(db) == []

    # Custom keyword filter persistence and live matching
    from intel_sourcerer.config import load_filters, reset_filters, save_filters
    from intel_sourcerer.importer import exclusion_reason, relevance_reason
    initial = load_filters(cfg)
    assert initial["is_custom"] is False and "ransomware" in initial["include_keywords"]
    updated = save_filters(cfg, ["agentic soc", "siem"], ["gartner"])
    assert updated["is_custom"] is True and updated["include_keywords"] == ["agentic soc", "siem"]
    assert load_sources(cfg)[0].id == "s1"  # sources.toml remains valid
    soc_post = Article("Vendor", "Building the Agentic SOC with Next-Gen SIEM", "https://vendor.example/soc", "soc", datetime(2026, 10, 1, tzinfo=timezone.utc), None, None, "")
    assert relevance_reason(soc_post, filters=updated) is not None
    gartner_post = Article("Vendor", "Named a Leader in Gartner SIEM", "https://vendor.example/g", "g", datetime(2026, 10, 1, tzinfo=timezone.utc), None, None, "")
    assert relevance_reason(gartner_post, filters=updated) is None
    assert "gartner" in exclusion_reason(gartner_post, filters=updated).lower()
    restored = reset_filters(cfg)
    assert restored["is_custom"] is False


def test_home_gym_endpoint_returns_themes_pulse_topics_and_takeaways(tmp_path):
    from intel_sourcerer.home_gym import home_gym_data

    db_path = tmp_path / "gym.sqlite3"
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    with closing(connect_database(db_path)) as db, db:
        u1 = "https://vendor.example/vuln-1"
        u2 = "https://vendor.example/ai-1"
        _seed_article(db, u1, "Zero-day exploit in CVE-2021-44228", "<p>Exploits CVE-2021-44228 via T1059.001.</p>", day=18)
        _seed_article(db, u2, "Model distillation and prompt injection in LLM agents", "<p>Prompt injection against LLM.</p>", day=19)
        db.execute(
            "UPDATE articles SET is_read=1, notes=?, notes_format='html', notes_updated_at=? WHERE url=?",
            ("<blockquote>Key takeaway: patch immediately.</blockquote>", "2026-10-19T10:00:00+00:00", u1),
        )
        db.execute("INSERT OR REPLACE INTO article_reads(article_url, read_at) VALUES (?,?)", (u1, "2026-10-19T10:00:00+00:00"))
        tid = create_topic(db, "Edge Vulns")["id"]
        add_article(db, u1, tid)
        save_topic_notes(db, tid, "<p>How do attackers bypass WAF rules?</p>", 0)
        db.commit()

        from intel_sourcerer.correlation import refresh_index

        refresh_index(db)
        data = home_gym_data(db, now=now)
        assert data["pulse"]["week_read"] == 1
        assert len(data["pulse"]["heatmap"]) >= 56
        theme_names = {t["name"] for t in data["themes"]}
        assert "Vulnerabilities" in theme_names and "AI misuse" in theme_names
        assert any(r["value"] == "CVE-2021-44228" and r["kev"] for r in data["recurring"])
        assert data["topics"][0]["questions"] == ["How do attackers bypass WAF rules?"]
        assert "patch immediately" in data["takeaways"][0]["snippet"]


def test_consolidated_library_filters_and_queue_panel(tmp_path):
    from intel_sourcerer.library import render_library

    db_path = tmp_path / "lib.sqlite3"
    with closing(connect_database(db_path)) as db, db:
        u1 = "https://vendor.example/unread-one"
        u2 = "https://vendor.example/read-two"
        _seed_article(db, u1, "Unread Report Alpha", "<p>Body text</p>", day=18)
        _seed_article(db, u2, "Read Report Beta", "<p>Body text</p>", day=19)
        db.execute("UPDATE articles SET is_read=1 WHERE url=?", (u2,))
        tid = create_topic(db, "Campaign X")["id"]
        add_article(db, u1, tid)
        db.commit()

        page_all = render_library(db, csrf_token="tok")
        assert "lib-panel-queue" in page_all
        assert "lib-queue-visible" in page_all
        assert 'data-queue-url="https://vendor.example/unread-one"' in page_all
        assert "Unread Report Alpha" in page_all and "Read Report Beta" in page_all

        page_unread = render_library(db, unread_only=True, csrf_token="tok")
        assert "Unread Report Alpha" in page_unread
        assert "Read Report Beta" not in page_unread

        page_topic = render_library(db, topic=tid, csrf_token="tok")
        assert "Unread Report Alpha" in page_topic
        assert "Read Report Beta" not in page_topic





def test_indicators_page_filters_sorts_and_pages(tmp_path):
    from intel_sourcerer.indicators import indicator_options

    db_path = tmp_path / "ind.sqlite3"
    with closing(connect_database(db_path)) as db, db:
        _seed_article(db, "https://vendor.example/a", "Old <script>alert(1)</script> report",
                      "<p>CVE-2021-44228 and T1059.001 and T1190</p>", day=1)
        _seed_article(db, "https://vendor.example/b", "New report", "<p>CVE-2021-44228 and T1190</p>", day=20)
        assert indicator_options({"kind": ["bogus"], "sort": ["nope"], "limit": ["9"], "days": ["13"]}) == {
            "query": "", "kind": "", "kev_only": False, "days": "", "sort": "reports", "limit": 50}
        by_reports = indicators_data(db)
        assert by_reports["indicators"][0]["value"] in {"CVE-2021-44228", "T1190"}
        assert [i["value"] for i in indicators_data(db, sort="value")["indicators"]] == sorted(
            (i["value"] for i in by_reports["indicators"]), key=str.casefold)
        options = {"query": "", "kind": "", "kev_only": False, "days": "", "sort": "reports", "limit": 50}
        data = dict(by_reports, total=120)
        page = render_indicators_page(data, options=options)
    # Grouped by type under "All", an honest count, and a link for more.
    assert 'class="ind-group"' in page and "Showing 3 of 120" in page and "limit=150" in page
    # Report titles reach the panel only through the JSON island, with "<" escaped.
    island = page.split('id="ind-data">', 1)[1].split("</script>", 1)[0]
    assert "<script>" not in island and "\\u003cscript>" in island
    assert json.loads(island)[0]["articles"]
