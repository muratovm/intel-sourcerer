"""Guest routes must work without account or database access."""

import sqlite3
from threading import Thread

import httpx
from intel_sourcerer.viewer import create_viewer


def test_guest_works_without_backend_or_database(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Guest mode contacted a backend or opened SQLite")

    real_client = httpx.Client
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    server = create_viewer("http://backend.invalid", "x" * 32, 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with real_client(base_url=f"http://127.0.0.1:{server.server_port}") as client:
            assert client.get("/guest").status_code == 200
            assert "/guest-reader.js" in client.get("/guest").text
            reader = client.get("/guest/read")
            assert "/guest-reader.js" in reader.text and "/focus.js" in reader.text
            assert "Sign in" in reader.text
            assert client.get("/guest.js").status_code == 200
            assert client.get("/temporary.js").status_code == 200
            assert "sessionStorage" in client.get("/guest-reader.js").text
            result = client.post(
                "/guest/prepare",
                json={
                    "articles": [
                        {
                            "title": "Guest article",
                            "markdown": "# Guest article\n\nSome **research**.",
                        }
                    ]
                },
                headers={"X-Guest-Request": "1"},
            )
            assert result.status_code == 200
            assert (
                "<strong>research</strong>" in result.json()["articles"][0]["body_html"]
            )
            assert "set-cookie" not in result.headers
            assert (
                client.post(
                    "/guest/prepare",
                    json={"articles": [{"url": "http://127.0.0.1/no-contents"}]},
                    headers={"X-Guest-Request": "1"},
                ).status_code
                == 400
            )
            assert (
                client.post(
                    "/guest/prepare", json={"articles": [{"text": "content"}]}
                ).status_code
                == 400
            )
            assert (
                client.post(
                    "/guest/prepare",
                    json={"articles": [{"url": "/private/file", "text": "content"}]},
                    headers={"X-Guest-Request": "1"},
                ).status_code
                == 400
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_combined_server_supports_both_modes(tmp_path):
    from intel_sourcerer.web import create_server

    server = create_server(str(tmp_path / "articles.sqlite3"), port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}") as client:
            assert client.get("/health").json()["service"] == "combined"
            assert "Continue as guest" in client.get("/setup").text
            assert client.get("/guest").status_code == 200
            assert client.get("/guest/read").status_code == 200
            for path in ("/guest.js", "/temporary.js", "/guest-reader.js"):
                assert client.get(path).status_code == 200
            result = client.post("/guest/prepare", json={"articles": [
                {"title": "Temporary", "markdown": "Some **research**."}
            ]}, headers={"X-Guest-Request": "1"})
            assert result.status_code == 200
            assert "<strong>research</strong>" in result.json()["articles"][0]["body_html"]
            assert "set-cookie" not in result.headers
            assert client.get("/api/library").status_code == 401
            assert client.post("/guest/prepare", json={}).status_code == 400
        with sqlite3.connect(tmp_path / "articles.sqlite3") as db:
            assert db.execute("SELECT count(*) FROM articles").fetchone()[0] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
