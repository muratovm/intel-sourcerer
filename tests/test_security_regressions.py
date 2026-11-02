"""Regression checks for setup, upload memory budgets, and viewer HTTPS trust."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import re
import threading

import httpx
import pytest

from intel_sourcerer.viewer import create_viewer
from intel_sourcerer.web import create_server

KEY = "regression-service-key-" + "x" * 32


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


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("INTEL_SETUP_TOKEN", "INTEL_TRUSTED_PROXIES", "INTEL_SECURE_COOKIES"):
        monkeypatch.delenv(name, raising=False)


def test_public_viewer_requires_backend_setup_code(tmp_path, capsys):
    backend = create_server(tmp_path / "library.sqlite3", 0, api_only=True, service_key=KEY)
    code = capsys.readouterr().out.split("administrator: ")[1].split()[0]
    with running(backend) as backend_url:
        with running(create_viewer(backend_url, KEY, 0, host="0.0.0.0")) as viewer_url:
            with httpx.Client(base_url=viewer_url, trust_env=False) as client:
                page = client.get("/setup")
                assert 'name="setup_code"' in page.text
                csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
                data = {"username": "owner", "password": "long-test-password", "csrf_token": csrf}
                for wrong in ({}, {"setup_code": "wrong"}):
                    assert client.post("/auth/setup", data={**data, **wrong}).status_code == 400
                    assert client.get("/api/viewer-session").json()["setup_required"]
                assert client.post("/auth/setup", data={**data, "setup_code": code}).status_code == 303
                assert client.get("/api/viewer-session").json()["user"]["admin"]


@pytest.mark.parametrize("via_viewer", [False, True])
@pytest.mark.parametrize("endpoint", ["/api/file-upload", "/api/pdf-upload"])
@pytest.mark.parametrize("import_fails", [False, True])
def test_upload_budget_covers_processing_and_recovers(tmp_path, monkeypatch, via_viewer, endpoint, import_fails):
    entered, release, second_read = threading.Event(), threading.Event(), threading.Event()
    failures = [import_fails]

    async def slow_import(*_):
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Test did not release the parser")
        if failures[0]:
            raise ValueError("Test parse failure")
        return "file:///test-upload"

    target = "intel_sourcerer.media_import.import_file" if endpoint == "/api/file-upload" else "intel_sourcerer.discovery.import_pdf_upload"
    monkeypatch.setattr(target, slow_import)
    backend = create_server(tmp_path / "library.sqlite3", 0, auth_enabled=False)
    original = backend.RequestHandlerClass.read_upload
    reads = []

    def counted_upload(self, length):
        payload = original(self, length)
        if payload is not None:
            reads.append(len(payload))
            if len(reads) == 2:
                second_read.set()
        return payload

    monkeypatch.setattr(backend.RequestHandlerClass, "read_upload", counted_upload)
    with running(backend) as backend_url:
        token = re.search(r'name="csrf-token" content="([^"]+)"', httpx.get(backend_url).text)[1]
        # Only the upload gateway is used here; account setup is covered separately.
        gateway = create_viewer(backend_url, KEY, 0) if via_viewer else None
        with (running(gateway) if gateway else nullcontext(backend_url)) as url:
            with httpx.Client(base_url=url, trust_env=False, timeout=15) as client:
                def upload():
                    return client.post(endpoint, content=b"test payload", headers={
                        "X-CSRF-Token": token, "X-Filename": "test.pdf",
                        "Content-Type": "application/octet-stream",
                    })

                with ThreadPoolExecutor(2) as pool:
                    first = pool.submit(upload)
                    try:
                        assert entered.wait(5)
                        second = pool.submit(upload)
                        assert second_read.wait(5)
                        assert not first.done()
                        assert upload().status_code == 429
                        assert len(reads) == 2
                    finally:
                        release.set()
                    expected = 400 if import_fails else 200
                    assert first.result().status_code == expected
                    assert second.result().status_code == expected
                failures[0] = False
                assert upload().status_code == 200


@pytest.mark.parametrize("trusted,forced", [(False, False), (True, False), (False, True)])
def test_viewer_https_sets_secure_cookies_and_hsts(tmp_path, monkeypatch, trusted, forced):
    # The backend doesn't trust proxy headers; it must authenticate viewer metadata.
    backend = create_server(tmp_path / "library.sqlite3", 0, api_only=True, service_key=KEY)
    if trusted:
        monkeypatch.setenv("INTEL_TRUSTED_PROXIES", "127.0.0.1/32")
    if forced:
        monkeypatch.setenv("INTEL_SECURE_COOKIES", "1")
    with running(backend) as backend_url:
        with running(create_viewer(backend_url, KEY, 0)) as viewer_url:
            with httpx.Client(base_url=viewer_url, trust_env=False) as client:
                # A forged service metadata header is always overwritten by the viewer.
                headers = {"X-Forwarded-Proto": "https", "X-Intel-Client-Proto": "https"}
                response = client.get("/login", headers=headers)
                expected = trusted or forced
                assert ("; Secure" in response.headers["set-cookie"]) == expected
                assert ("strict-transport-security" in response.headers) == expected
                assert ("strict-transport-security" in client.get("/reader.css", headers=headers).headers) == expected
                # Also cover responses streamed through the gateway rather than rendered locally.
                client.cookies.clear()
                proxied = client.get("/api/viewer-session", headers=headers)
                assert ("; Secure" in proxied.headers["set-cookie"]) == expected
                assert ("strict-transport-security" in proxied.headers) == expected
        with httpx.Client(base_url=backend_url, trust_env=False) as client:
            forged = client.get("/api/viewer-session", headers={"X-Intel-Client-Proto": "https"})
            assert forged.status_code == 403
            assert "strict-transport-security" not in forged.headers


def test_combined_server_ignores_viewer_scheme_header(tmp_path):
    backend = create_server(tmp_path / "library.sqlite3", 0)
    with running(backend) as url:
        response = httpx.get(url + "/login", headers={"X-Intel-Client-Proto": "https"})
        assert "; Secure" not in response.headers["set-cookie"]
        assert "strict-transport-security" not in response.headers
