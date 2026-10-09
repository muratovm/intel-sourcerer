"""Real HTTP checks across the independently hosted viewer/backend boundary."""

import re
from contextlib import closing, contextmanager
from threading import Thread

import httpx
import pytest

from intel_sourcerer.storage import connect_database
from intel_sourcerer.viewer import create_viewer
from intel_sourcerer.web import create_server

KEY = "service-test-key-" + "x" * 32
CONFIG = """schema_version = 1
[[sources]]
id = "test"
name = "Test source"
source_type = "security_vendor"
parent_url = "https://example.com/reports"
"""


@contextmanager
def running(server):
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def token(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]


def test_separate_viewer_backend_workflow(tmp_path):
    database = tmp_path / "private/articles.sqlite3"
    config = tmp_path / "sources.toml"
    config.write_text(CONFIG)
    backend = create_server(database, 0, config, api_only=True, service_key=KEY)
    with running(backend) as backend_url:
        viewer = create_viewer(backend_url, KEY, 0)
        with (
            running(viewer) as viewer_url,
            httpx.Client(base_url=viewer_url) as client,
            httpx.Client(base_url=viewer_url) as stranger,
        ):
            assert httpx.get(backend_url + "/health").json()["service"] == "backend"
            assert httpx.get(backend_url + "/api/viewer-session").status_code == 403
            assert (
                httpx.get(
                    backend_url + "/", headers={"X-Intel-Service-Key": KEY}
                ).status_code
                == 404
            )
            assert client.get("/health").json()["service"] == "viewer"
            assert client.get("/reader.css").status_code == 200
            assert client.get("/api/state").status_code == 401
            first = client.get("/")
            assert first.headers["location"] == "/setup"
            setup = client.get("/setup")
            assert "Continue as guest" in setup.text
            csrf = token(setup)
            response = client.post(
                "/auth/setup",
                data={
                    "username": "owner",
                    "password": "a-long-test-password",
                    "csrf_token": csrf,
                },
            )
            assert response.status_code == 303
            assert "HttpOnly" in response.headers["set-cookie"]
            assert response.headers["location"] == "/"
            assert client.get("/setup").headers["location"] == "/"
            workspace = client.get("/")
            assert (
                "__USER_ID__" not in workspace.text
                and "__TOKEN__" not in workspace.text
            )
            assert "/account" in workspace.text
            session = client.get("/api/viewer-session").json()
            csrf = session["csrf_token"]
            assert session["user"]["admin"]
            assert "database_path" not in str(session)
            # Every request derives ownership on the backend, not from browser headers.
            with closing(connect_database(database)) as db, db:
                db.execute(
                    "INSERT INTO sources VALUES('test','Test source','security_vendor','https://example.com',1)"
                )
                db.execute(
                    "INSERT INTO articles(url,source_id,title,published_at,content_text,summary_html,content_status,relevance_reason,discovered_at) VALUES('https://example.com/private','test','Private report','2026-10-08','Report body','','full_text','','2026-10-08')"
                )
            url = "https://example.com/private"
            assert client.get("/library").status_code == 200
            assert "Private report" in client.get("/library").text
            assert client.get("/sources").status_code == 200
            assert "Test source" in client.get("/sources").text
            assert client.get("/stats").status_code == 200
            assert (
                client.get("/api/library").json()["articles"][0]["title"]
                == "Private report"
            )
            assert (
                client.get("/api/article", params={"url": url}).json()["title"]
                == "Private report"
            )
            assert (
                client.post(
                    "/api/notes",
                    json={"url": url, "text": "personal note", "revision": 0},
                    headers={"X-CSRF-Token": csrf},
                ).status_code
                == 200
            )
            assert "personal note" in client.get("/library").text
            assert "personal note" in client.get("/export", params={"url": url}).text
            assert (
                client.post(
                    "/api/rename-article",
                    json={"url": url, "title": "Renamed report"},
                    headers={"X-CSRF-Token": "wrong"},
                ).status_code
                == 403
            )
            account = client.get("/account")
            assert (
                client.post(
                    "/auth/create-user",
                    data={
                        "username": "reader",
                        "password": "another-long-password",
                        "csrf_token": token(account),
                    },
                ).status_code
                == 200
            )
            login = stranger.get("/login")
            assert "Continue as guest" in login.text
            failed = stranger.post(
                "/auth/login",
                data={
                    "username": "reader",
                    "password": "bad-password",
                    "csrf_token": token(login),
                },
            )
            assert failed.status_code == 400
            assert "Invalid username or password" in failed.text
            response = stranger.post(
                "/auth/login",
                data={
                    "username": "reader",
                    "password": "another-long-password",
                    "csrf_token": token(failed),
                },
            )
            assert response.status_code == 303
            assert stranger.get("/api/state").json()["articles"] == []
            assert (
                stranger.get(
                    "/api/article",
                    params={"url": url},
                    headers={
                        "X-Intel-Client-IP": "forged",
                        "X-Intel-Service-Key": "forged",
                    },
                ).status_code
                == 404
            )
            assert stranger.get("/sources").status_code == 200
            upload = client.post(
                "/api/file-upload",
                content=b"fake audio bytes",
                headers={"X-Filename": "sample.mp3", "X-CSRF-Token": csrf},
            )
            assert upload.status_code == 200
            media = next((database.parent / "media").glob("*.mp3"))
            response = client.get(
                "/local-media/" + media.name, headers={"Range": "bytes=0-3"}
            )
            assert response.status_code == 206 and response.content == b"fake"
            assert response.headers["content-range"] == "bytes 0-3/16"
            assert stranger.get("/local-media/" + media.name).status_code == 404
            # A second viewer process can resume the same account and stored notes.
            with (
                running(create_viewer(backend_url, KEY, 0)) as other_viewer,
                httpx.Client(base_url=other_viewer, cookies=client.cookies) as resumed,
            ):
                assert (
                    resumed.get("/api/viewer-session").json()["user"]["id"]
                    == session["user"]["id"]
                )
                assert (
                    "personal note"
                    in resumed.get("/library", params={"selected": url}).text
                )
            stranger_token = token(stranger.get("/account"))
            assert (
                stranger.post(
                    "/auth/password",
                    data={
                        "csrf_token": stranger_token,
                        "current_password": "another-long-password",
                        "password": "changed-long-password",
                    },
                ).status_code
                == 303
            )
            assert (
                stranger.post(
                    "/auth/logout", data={"csrf_token": token(stranger.get("/account"))}
                ).status_code
                == 303
            )
            assert stranger.get("/api/state").status_code == 401
            assert stranger.get("/library").headers["location"] == "/login"


def test_viewer_static_files_do_not_need_backend_or_database(tmp_path, monkeypatch):
    import sqlite3

    def no_database(*args, **kwargs):
        raise AssertionError("The viewer attempted to open a database")

    monkeypatch.setattr(sqlite3, "connect", no_database)
    # A closed local socket supplies a reliably unavailable backend.
    import socket

    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
    server = create_viewer(f"http://127.0.0.1:{port}", KEY, 0)
    with running(server) as address, httpx.Client(base_url=address) as client:
        assert client.get("/reader.css").status_code == 200
        assert client.get("/reader.js").status_code == 200
        assert client.get("/health").status_code == 200
        assert client.get("/brand-icon.png").status_code == 200
        assert client.get("/").status_code == 502
        assert client.get("/../../accounts.sqlite3").status_code == 404
        assert client.get("/vendor/katex/../../accounts.sqlite3").status_code == 404
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/private",
        "https://user:password@example.com",
        "https://example.com/prefix",
        "https://example.com?next=foo",
    ],
)
def test_viewer_requires_explicit_origin_and_secret(url):
    with pytest.raises(ValueError):
        create_viewer(url, KEY, 0)
    with pytest.raises(ValueError):
        create_viewer("http://127.0.0.1:8766", "", 0)


def test_all_viewer_pages_render_without_database_access(tmp_path, monkeypatch):
    import sqlite3

    def no_database(*args, **kwargs):
        raise AssertionError("Viewer rendering opened a database")

    monkeypatch.setattr(sqlite3, "connect", no_database)
    responses = {
        "/api/viewer-session": {
            "user": {"id": "remote-user", "username": "reader", "admin": False},
            "csrf_token": "remote-csrf",
            "setup_required": False,
        },
        "/api/library": {
            "articles": [
                {
                    "url": "https://example.com/report",
                    "title": "Remote report",
                    "publisher": "Example",
                    "published_at": "2026-10-08",
                    "notes": "Remote note",
                    "notes_format": "plain",
                    "notes_updated_at": None,
                    "content_text": "Remote text",
                    "content_format": "article",
                    "is_read": 0,
                }
            ]
        },
        "/api/sources": {
            "sources": [
                {
                    "id": "example",
                    "name": "Example",
                    "source_type": "ai_lab",
                    "parent_url": "https://example.com",
                    "enabled": True,
                }
            ]
        },
        "/api/stats": {"reads": [], "timing": {"sessions": [], "abandoned": 0}},
    }

    def upstream(request):
        assert request.headers["X-Intel-Service-Key"] == KEY
        return httpx.Response(200, json=responses[request.url.path])

    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        if kwargs.get("trust_env") is False:
            kwargs["transport"] = httpx.MockTransport(upstream)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    with (
        running(create_viewer("http://backend.example", KEY, 0)) as address,
        real_client(base_url=address) as client,
    ):
        for page in ["/", "/library", "/sources", "/stats", "/account"]:
            response = client.get(page)
            assert response.status_code == 200
        assert "Remote report" in client.get("/library").text
        assert "remote-user" in client.get("/").text
    assert list(tmp_path.iterdir()) == []
