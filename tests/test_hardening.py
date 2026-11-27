"""Regression tests for security hardening, SSRF/port/size bounds, auth, and cleanup fixes."""

import asyncio
from datetime import datetime, timezone
from io import BytesIO
import re
import socket
import threading
from contextlib import closing, contextmanager

import httpx
import pytest
from PIL import Image
from pptx import Presentation

from intel_sourcerer import feeds, importer, temporary
from intel_sourcerer.auth import Accounts
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.discovery import import_pdf_upload
from intel_sourcerer.net_safety import public_only
from intel_sourcerer.sessions import delete_article
from intel_sourcerer.storage import connect_database
from intel_sourcerer.viewer import create_viewer
from intel_sourcerer.web import create_server

KEY = "hardening-service-key-" + "x" * 32


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


def test_public_only_blocks_non_http_ports(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))],
    )
    with public_only(httpx.Client(trust_env=False)) as client:
        with pytest.raises(httpx.ConnectError, match="non-HTTP port"):
            client.get("http://example.com:6379/")

    async def run_async():
        async with public_only(httpx.AsyncClient(trust_env=False)) as client:
            await client.get("http://example.com:25/")

    with pytest.raises(httpx.ConnectError, match="non-HTTP port"):
        asyncio.run(run_async())


def test_polite_get_enforces_streaming_size_limit():
    import gzip

    async def run():
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 2048))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="exceeds"):
                await feeds.polite_get(client, "https://example.com/huge", max_bytes=1024)

        compressed = gzip.compress(b"<html><body><h1>Anthropic</h1></body></html>")
        gz_transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=compressed,
                headers={"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip"},
            )
        )
        async with httpx.AsyncClient(transport=gz_transport) as client:
            response = await feeds.polite_get(client, "https://example.com/gzip")
            assert "Anthropic" in response.text
            assert "content-encoding" not in response.headers

    asyncio.run(run())


def test_importer_refusal_does_not_overwrite_query_until(monkeypatch, tmp_path):
    first = PublisherSource("s1", "Source One", "https://one.example", "security_vendor", "https://one.example/rss")
    second = PublisherSource("s2", "Source Two", "https://two.example", "security_vendor", "https://two.example/rss")
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    until = datetime(2026, 9, 10, tzinfo=timezone.utc)
    rss_future = (
        b"<rss version='2.0'><channel><item>"
        b"<title>Ransomware future report</title>"
        b"<link>https://two.example/future</link>"
        b"<pubDate>Fri, 18 Sep 2026 10:00:00 GMT</pubDate>"
        b"</item></channel></rss>"
    )

    def handler(request):
        if "one.example" in str(request.url):
            return httpx.Response(429, headers={"x-vercel-mitigated": "challenge"})
        return httpx.Response(200, content=rss_future)

    monkeypatch.setattr(importer, "load_sources", lambda _: [first, second])
    monkeypatch.setattr(
        importer,
        "public_only",
        lambda _: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True),
    )
    db_path = tmp_path / "articles.sqlite3"
    report = asyncio.run(importer.import_articles("unused", db_path, since, until))
    assert report["sources"][1]["in_window"] == 0
    with closing(connect_database(db_path)) as db:
        recorded_until = db.execute("SELECT until FROM import_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert recorded_until == until.isoformat()


def test_stateless_anonymous_sessions_do_not_write_to_sqlite(tmp_path):
    db_path = tmp_path / "articles.sqlite3"
    config = tmp_path / "sources.toml"
    config.write_text("sources = []\n")
    connect_database(db_path).close()
    accounts = Accounts(db_path, config)
    token, csrf = accounts.new_session()
    assert token.startswith("anon.")
    with accounts.transaction() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    session = accounts.session("intel_session=" + token)
    assert session is not None and session["user_id"] is None and session["csrf"] == csrf
    assert accounts.session("intel_session=" + token + "tampered") is None


def test_setup_code_attempts_are_throttled(tmp_path):
    db_path = tmp_path / "articles.sqlite3"
    config = tmp_path / "sources.toml"
    config.write_text("sources = []\n")
    connect_database(db_path).close()
    accounts = Accounts(db_path, config)
    for _ in range(10):
        accounts.record_setup_attempt("198.51.100.1")
    with pytest.raises(ValueError, match="Too many setup attempts"):
        accounts.record_setup_attempt("198.51.100.1")
    accounts.clear_setup_attempts("198.51.100.1")
    accounts.record_setup_attempt("198.51.100.1")


def test_last_admin_cannot_delete_account_while_other_users_exist(tmp_path):
    db_path = tmp_path / "articles.sqlite3"
    config = tmp_path / "sources.toml"
    config.write_text("sources = []\n")
    connect_database(db_path).close()
    accounts = Accounts(db_path, config)
    owner = accounts.create_user("owner", "very-long-password", first=True)
    reader = accounts.create_user("reader", "another-long-password")
    with pytest.raises(ValueError, match="only administrator"):
        accounts.delete_account(owner, "owner", "very-long-password")
    accounts.delete_account(reader, "reader", "another-long-password")
    accounts.delete_account(owner, "owner", "very-long-password")
    assert accounts.empty()


def _sample_pdf(text):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 100), text)
    data = doc.tobytes()
    doc.close()
    return data


def test_delete_article_removes_unreferenced_assets(tmp_path):
    db_path = tmp_path / "articles.sqlite3"
    payload = _sample_pdf("Threat report for cleanup verification.")
    url = asyncio.run(import_pdf_upload(db_path, payload))
    uploads = list((tmp_path / "uploads").glob("*.pdf"))
    assert len(uploads) == 1
    with closing(connect_database(db_path)) as db, db:
        delete_article(db, url)
    assert not uploads[0].exists()


def test_temporary_redirect_without_location_raises_value_error(monkeypatch):
    monkeypatch.setattr(temporary, "public_link", lambda value: "93.184.216.34")
    original = httpx.Client

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda _: httpx.Response(302))
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    with pytest.raises(ValueError, match="missing a destination"):
        temporary.load_public_article("https://example.com/broken-redirect", prefer_arxiv=False)


def test_pptx_rejects_oversized_pixel_image(tmp_path):
    from intel_sourcerer.media_import import import_file

    # Create a tiny-on-disk solid PNG that exceeds 16M pixels (4500 x 4500 = 20.25M pixels).
    img_buf = BytesIO()
    Image.new("RGB", (4500, 4500), color=(0, 0, 0)).save(img_buf, format="PNG")
    img_buf.seek(0)
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_picture(img_buf, 0, 0)
    deck = BytesIO()
    prs.save(deck)
    with pytest.raises(ValueError, match="maximum pixel limit"):
        asyncio.run(import_file(tmp_path / "articles.db", deck.getvalue(), "bomb.pptx"))


def test_security_headers_transfer_encoding_and_viewer_dot_segments(tmp_path, monkeypatch):
    for name in ("INTEL_SETUP_TOKEN", "INTEL_TRUSTED_PROXIES", "INTEL_SECURE_COOKIES"):
        monkeypatch.delenv(name, raising=False)
    config = tmp_path / "sources.toml"
    config.write_text("sources = []\n")
    backend = create_server(tmp_path / "articles.sqlite3", 0, config, api_only=True, service_key=KEY)
    with running(backend) as backend_url:
        with running(create_viewer(backend_url, KEY, 0)) as viewer_url:
            with httpx.Client(base_url=viewer_url, trust_env=False) as client:
                page = client.get("/setup")
                assert page.headers["referrer-policy"] == "strict-origin-when-cross-origin"
                assert "camera=()" in page.headers["permissions-policy"]
                assert client.get("/api/%2e%2e/health").status_code == 400
    combined = create_server(tmp_path / "combined.sqlite3", 0, config, auth_enabled=False)
    with running(combined) as url:
        with httpx.Client(base_url=url, trust_env=False) as client:
            home = client.get("/")
            assert "form-action 'self'" in home.headers["content-security-policy"]
            assert home.headers["referrer-policy"] == "strict-origin-when-cross-origin"
            csrf = re.search(r'name="csrf-token" content="([^"]+)"', home.text)[1]
            # Transfer-Encoding must be rejected on POST endpoints.
            bad = client.post(
                "/api/queue",
                content=b'{"urls":[]}',
                headers={"X-CSRF-Token": csrf, "Transfer-Encoding": "chunked"},
            )
            assert bad.status_code == 400
            # Local media responses must carry no-store and CSP headers.
            media_dir = tmp_path / "media"
            media_dir.mkdir(exist_ok=True)
            filename = "b" * 64 + ".mp3"
            (media_dir / filename).write_bytes(b"audio")
            media_resp = client.get("/local-media/" + filename)
            assert media_resp.status_code == 200
            assert media_resp.headers["cache-control"] == "no-store"
            assert "content-security-policy" in media_resp.headers


def test_library_rows_omits_unselected_article_bodies(tmp_path):
    from intel_sourcerer.feeds import Article
    from intel_sourcerer.library import library_rows
    from intel_sourcerer.storage import save_article, save_source

    db_path = tmp_path / "lib.sqlite3"
    with closing(connect_database(db_path)) as db, db:
        save_source(db, PublisherSource("s", "Source", "https://example.com", "rss"))
        for idx in (1, 2):
            art = Article("Source", f"Report {idx}", f"https://example.com/{idx}", str(idx), datetime(2026, 10, idx, tzinfo=timezone.utc), None, None, "<p>" + "summary " * 500 + "</p>")
            save_article(db, "s", art, content=f"Body {idx}", error=None, reason="r", discovered_at="2026-10-01", markdown=f"# Report {idx}", html=f"<p>HTML {idx}</p>")
        rows = {row["url"]: row for row in library_rows(db, selected="https://example.com/1")}
    assert rows["https://example.com/1"]["content_html"] == "<p>HTML 1</p>"
    assert rows["https://example.com/2"]["content_html"] is None
    assert rows["https://example.com/2"]["content_markdown"] is None
    # Every row carries a feed summary for its preview line, but only a capped slice of it.
    assert all(len(row["summary_html"]) <= 1200 for row in rows.values())


def test_display_timezone_and_ytdlp_override(monkeypatch, tmp_path):
    from intel_sourcerer import transcripts
    from intel_sourcerer.http_common import display_timezone

    monkeypatch.delenv("INTEL_TIMEZONE", raising=False)
    assert display_timezone().key == "America/Toronto"
    monkeypatch.setenv("INTEL_TIMEZONE", "UTC")
    assert display_timezone().key == "UTC"
    monkeypatch.setenv("INTEL_TIMEZONE", "Invalid/Zone")
    assert display_timezone().key == "America/Toronto"

    fake_bin = tmp_path / "yt-dlp"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    monkeypatch.setenv("INTEL_SOURCERER_YTDLP", str(fake_bin))
    prefix, executable = transcripts.ytdlp_base_command()
    assert prefix == [str(fake_bin)] and executable == str(fake_bin)


def test_transcript_jobs_evict_old_terminal_entries(monkeypatch):
    from intel_sourcerer import transcripts

    monkeypatch.setattr(transcripts, "_jobs", {
        ("db", "old"): {"state": "done", "_at": 0.0},
        ("db", "active"): {"state": "running", "_at": 0.0},
    })
    transcripts._prune_jobs(now=transcripts.JOB_RETENTION_SECONDS + 10)
    assert ("db", "old") not in transcripts._jobs
    assert ("db", "active") in transcripts._jobs


def test_fts5_search_syncs_articles_tags_highlights_and_pagination(tmp_path):
    from intel_sourcerer.feeds import Article
    from intel_sourcerer.library import fts_match_urls, render_library
    from intel_sourcerer.reading_state import save_highlight
    from intel_sourcerer.storage import save_article, save_source
    from intel_sourcerer.tags import add_tag, remove_tag

    db_path = tmp_path / "fts.sqlite3"
    with closing(connect_database(db_path)) as db, db:
        save_source(db, PublisherSource("s", "Source", "https://example.com", "rss"))
        for idx, body in [
            (1, "Alpha beacon analysis with <b>x</b> marker"),
            (2, "Bravo kerberoasting guidance"),
            (3, "Charlie loader disassembly"),
        ]:
            art = Article(
                "Source",
                f"Report {idx}",
                f"https://example.com/{idx}",
                str(idx),
                datetime(2026, 10, idx, tzinfo=timezone.utc),
                None,
                None,
                "<p>sum</p>",
            )
            save_article(
                db,
                "s",
                art,
                content=body,
                error=None,
                reason="r",
                discovered_at="2026-10-01",
                markdown=body,
                html=f"<p>{body}</p>",
            )
        add_tag(db, "https://example.com/2", "apt29")
        save_highlight(db, "https://example.com/3", "disassembly", "loader ", "")

        assert fts_match_urls(db, "<b>x") == {"https://example.com/1"}
        assert fts_match_urls(db, "apt29") == {"https://example.com/2"}
        assert fts_match_urls(db, "disassembly") == {"https://example.com/3"}
        # Short (<3 char) query falls back to Python substring filtering.
        assert fts_match_urls(db, "ab") is None

        remove_tag(db, "https://example.com/2", "apt29")
        assert "https://example.com/2" not in fts_match_urls(db, "apt29")

        page1 = render_library(db, page=1, page_size=2)
        assert "Page 1 of 2" in page1
        assert '<span class="muted">← Previous</span>' in page1
        assert 'page=2">Next →</a>' in page1
        assert "Report 3" in page1 and "Report 2" in page1
        page2 = render_library(db, page=2, page_size=2)
        assert "Page 2 of 2" in page2
        assert ">← Previous</a>" in page2 and "page=1" not in page2
        assert '<span class="muted">Next →</span>' in page2
        assert "Report 1" in page2


def test_background_jobs_run_detect_conflict_and_isolate_users(tmp_path, monkeypatch):
    import time
    from intel_sourcerer import jobs, web

    gate = threading.Event()
    started = threading.Event()

    async def slow_import(config, database, since, until):
        started.set()
        gate.wait(timeout=5)
        return {"sources": [{"inserted": 2}], "errors": [], "paused": []}

    monkeypatch.setattr(web, "import_articles", slow_import)
    config = tmp_path / "sources.toml"
    config.write_text("sources = []\n")
    db_path = tmp_path / "jobs.sqlite3"
    server = create_server(db_path, 0, config, auth_enabled=False)
    with running(server) as url:
        with httpx.Client(base_url=url, trust_env=False) as client:
            home = client.get("/")
            csrf = re.search(r'name="csrf-token" content="([^"]+)"', home.text)[1]
            resp = client.post("/api/jobs/discover", json={"days": 7}, headers={"X-CSRF-Token": csrf})
            assert resp.status_code == 202
            job_id = resp.json()["id"]
            assert resp.json()["state"] == "running"
            assert started.wait(timeout=3)
            # Second concurrent job for the same database must return 409 Conflict.
            conflict = client.post("/api/jobs/discover", json={"days": 7}, headers={"X-CSRF-Token": csrf})
            assert conflict.status_code == 409
            # Different database owner cannot read this job ID.
            assert jobs.get_job(tmp_path / "other.sqlite3", job_id) is None
            gate.set()
            final = None
            for _ in range(50):
                poll = client.get("/api/jobs", params={"id": job_id})
                assert poll.status_code == 200
                final = poll.json()
                if final["state"] != "running":
                    break
                time.sleep(0.05)
            assert final["state"] == "done"
            assert final["result"]["sources"][0]["inserted"] == 2


def test_workspace_es_modules_served_and_temporary_script_unpatched(tmp_path):
    config = tmp_path / "sources.toml"
    config.write_text("sources = []\n")
    modules = (
        "/workspace/storage.js",
        "/workspace/media.js",
        "/workspace/highlights.js",
        "/workspace/iocs.js",
        "/workspace/panes.js",
    )
    server = create_server(tmp_path / "mods.sqlite3", 0, config, auth_enabled=False)
    with running(server) as url:
        with httpx.Client(base_url=url, trust_env=False) as client:
            # The tag carries a ?v= content version so browsers can't run a stale copy.
            assert re.search(r'<script type="module" src="/reader\.js\?v=[0-9a-f]+"></script>', client.get("/").text)
            for path in modules:
                resp = client.get(path)
                assert resp.status_code == 200
                assert "export " in resp.text
    # Temporary reader script should serve workspace.js verbatim without string replacement hacks.
    from intel_sourcerer.templates import UI_ROOT

    assert temporary.temporary_reader_script() == (UI_ROOT / "workspace/workspace.js").read_text()
    assert "localStorage" not in temporary.temporary_reader_script()


