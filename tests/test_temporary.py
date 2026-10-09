"""Frontend-only reading needs neither auth nor persistent storage."""

import sqlite3
from threading import Thread
import httpx
import pytest
from intel_sourcerer.temporary import (
    create_temporary_viewer,
    load_article,
    prepare_article,
)


def test_temporary_reader_without_database(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Temporary viewer accessed a database")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    first = tmp_path / "first.md"
    first.write_text("# First\n\nSome **research**.\n\n- One\n- Two")
    second = tmp_path / "second.html"
    second.write_text(
        "<article><h1>Second</h1><p>More research.</p><script>alert(1)</script></article>"
    )
    articles = [load_article(str(first)), load_article(str(second))]
    server = create_temporary_viewer(articles, 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}") as client:
            page = client.get("/")
            assert page.status_code == 200 and "temporary.js" in page.text
            assert "Export articles" in page.text and "/focus.js" in page.text
            payload = client.get("/temporary-session.json").json()
            assert len(payload["articles"]) == 2
            assert payload["articles"][0]["title"] == "First"
            assert "<ul>" in payload["articles"][0]["body_html"]
            assert "<script" not in payload["articles"][1]["body_html"]
            script = client.get("/reader.js").text
            assert "localStorage" not in script and "sessionStorage" in script
            assert "renderSetup();" in script
            assert client.get("/temporary.js").status_code == 200
            assert client.get("/api/state").status_code == 404
            assert client.get("/setup").status_code == 404
            assert client.post("/api/notes", json={}).status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["first.md", "second.html"]


def test_temporary_content_validation_and_notes():
    article = prepare_article(
        {
            "title": "Title",
            "markdown": "# Title\n\nBody",
            "notes_format": "html",
            "notes": "<p>note<script>alert(1)</script></p>",
        },
        0,
    )
    assert "<h1" not in article["body_html"] and "<script" not in article["notes"]
    with pytest.raises(ValueError):
        create_temporary_viewer([], 0)
    with pytest.raises(ValueError):
        prepare_article({"url": "javascript:alert(1)", "text": "Body"}, 0)
    with pytest.raises(ValueError):
        prepare_article({"text": "Body", "published_at": "nonsense"}, 0)
    with pytest.raises(ValueError):
        prepare_article({"title": "Missing content"}, 0)
    with pytest.raises(ValueError):
        create_temporary_viewer(
            [
                {"url": "https://example.com", "text": "One"},
                {"url": "https://example.com", "text": "Two"},
            ],
            0,
        )


def test_session_parser_retains_sources_and_blocks_private_urls():
    import json
    from intel_sourcerer.temporary import parse_session_content, public_link

    source = {
        "url": "https://example.com/report",
        "markdown": "# Research\n\nSource contents",
    }
    result = parse_session_content(
        json.dumps({"articles": [source]}).encode(), "application/json"
    )
    article = result["articles"][0]
    assert article["source"] == source
    reparsed = parse_session_content(
        json.dumps({"articles": [article["source"]]}).encode(), "application/json"
    )
    assert reparsed["articles"][0]["body_html"] == article["body_html"]
    for url in [
        "http://127.0.0.1/report",
        "http://[::1]/report",
        "file:///private/file",
        "http://localhost/report",
    ]:
        with pytest.raises(ValueError):
            public_link(url)


def test_link_parser_pins_public_address_and_preserves_original_host(monkeypatch):
    from intel_sourcerer import temporary

    monkeypatch.setattr(temporary, "public_link", lambda value: "93.184.216.34")
    original = httpx.Client

    def upstream(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["Host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        return httpx.Response(
            200, text="<article><h1>Research</h1><p>Public contents</p></article>"
        )

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(upstream)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    article = temporary.load_public_article("https://example.com/report")
    assert article["title"] == "Research"
    assert article["url"] == "https://example.com/report"
