"""Exercise production Compose, HTTPS, persistence and Linux deploy/rollback locally.

Usage: python3 scripts/test_deployment.py intel-sourcerer:ci
Requires Docker and a prebuilt app image. Uses only disposable projects and volumes.
On Linux, also exercises deploy.sh against a disposable localhost registry.
"""

import http.cookiejar
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

REPO = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    result = subprocess.run(args, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def published_digest(tag):
    # The same local image may have digests from older disposable registries.
    prefix = tag.rsplit(":", 1)[0] + "@"
    records = json.loads(run("docker", "image", "inspect", tag))[0]["RepoDigests"]
    return next(value for value in records if value.startswith(prefix))


def main(image):
    project = "intel-ci-" + uuid.uuid4().hex[:10]
    registry = project + "-registry"
    root = Path(tempfile.mkdtemp(prefix="intel-deployment-"))
    env = dict(os.environ, INTEL_DEPLOY_ROOT=str(root), INTEL_COMPOSE_PROJECT=project)
    http_port, https_port, registry_port = free_port(), free_port(), free_port()
    (root / ".env").write_text(
        f"DOMAIN=localhost\nACME_EMAIL=test@example.com\nINTEL_GUEST=off\n"
        "INTEL_BIND_ADDRESS=127.0.0.1\n"
        f"INTEL_HTTP_PORT={http_port}\nINTEL_HTTPS_PORT={https_port}\n"
        "INTEL_SETUP_TOKEN=integration-test-setup-code\n"
    )
    release = root / "releases" / "first"
    release.mkdir(parents=True)
    for name in ("compose.yaml", "Caddyfile", "deploy.sh"):
        shutil.copyfile(REPO / "deploy" / name, release / name)
    (release / "image.env").write_text(f"INTEL_IMAGE={image}\n")

    def compose(directory, *args):
        return run("docker", "compose", "-p", project, "--env-file", str(root / ".env"),
                   "--env-file", str(directory / "image.env"), "-f", str(directory / "compose.yaml"), *args)

    def deploy(directory, reference, *, succeeds=True):
        result = subprocess.run(["bash", str(directory / "deploy.sh"), reference], env=env,
                                text=True, capture_output=True)
        if (result.returncode == 0) != succeeds:
            raise RuntimeError(f"Unexpected deployment result:\n{result.stdout}\n{result.stderr}")
        return result

    try:
        compose(release, "config", "--quiet")
        compose(release, "up", "-d", "--wait", "--wait-timeout", "180")
        # localhost uses Caddy's local CA, never an ACME staging or public certificate.
        ca = root / "root.crt"
        for _ in range(30):
            try:
                compose(release, "cp", "caddy:/data/caddy/pki/authorities/local/root.crt", str(ca))
                break
            except RuntimeError:
                time.sleep(1)
        assert ca.exists(), "Caddy did not provision its local CA"
        env["CURL_CA_BUNDLE"] = str(ca)
        context = ssl.create_default_context(cafile=str(ca))
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context), urllib.request.HTTPCookieProcessor(jar))
        origin = f"https://localhost:{https_port}"

        def request(path, data=None, headers=None):
            req = urllib.request.Request(origin + path, data=data, headers=headers or {})
            return opener.open(req, timeout=20)

        if platform.system() == "Linux":
            # Publish only to a disposable local registry; no GHCR credentials are needed.
            run("docker", "run", "-d", "--name", registry, "-p",
                f"127.0.0.1:{registry_port}:5000", "registry:3.0.0")
            tag = f"localhost:{registry_port}/intel-sourcerer:good"
            run("docker", "tag", image, tag)
            run("docker", "push", tag)
            digest = published_digest(tag)
            compose(release, "stop")
            deploy(release, digest)
        else:
            print("Linux deploy/rollback checks skipped on this OS; HTTPS/volume checks still run.", flush=True)

        health = request("/health")
        assert json.load(health)["status"] == "ok"
        assert "max-age" in health.headers["Strict-Transport-Security"]
        request("/setup")
        assert all(cookie.secure for cookie in jar), "Expected Secure session cookies"
        session = json.load(request("/api/viewer-session"))
        assert session["setup_code_required"]
        from urllib.parse import urlencode
        request("/auth/setup", urlencode({
            "username": "docker-owner", "password": "integration-test-password",
            "csrf_token": session["csrf_token"], "setup_code": "integration-test-setup-code",
        }).encode())
        csrf = json.load(request("/api/viewer-session"))["csrf_token"]
        email = b"From: test@example.com\nSubject: Deployment test\nContent-Type: text/plain\n\nSaved article.\n"
        response = request("/api/file-upload", email, {"X-CSRF-Token": csrf,
            "Content-Type": "application/octet-stream", "X-Filename": "test.eml"})
        url = json.load(response)["url"]
        json.load(request("/api/notes", json.dumps({"url": url, "text": "Survives deployment", "revision": 0}).encode(),
            {"X-CSRF-Token": csrf, "Content-Type": "application/json"}))
        try:
            request("/guest/prepare", b"{}", {"X-Guest-Request": "1", "Content-Type": "application/json"})
            raise AssertionError("Guest endpoint should be disabled")
        except urllib.error.HTTPError as error:
            assert error.code == 404
        config = json.loads(compose(release, "config", "--format", "json"))
        assert not config["services"]["backend"].get("ports"), "Backend must stay private"
        assert all(p["host_ip"] == "127.0.0.1" for p in config["services"]["caddy"]["ports"])
        assert config["services"]["backend"]["read_only"]
        assert config["services"]["backend"]["environment"]["INTEL_TRUSTED_PROXIES"] == "172.30.87.2/32"

        if platform.system() == "Linux":
            second = root / "releases" / "second"
            shutil.copytree(release, second)
            deploy(second, digest)
            deploy(second, "--backup")
            assert (root / "current").resolve() == second
            assert list((root / "backups").glob("*/data/accounts.sqlite3"))
            release = second
        else:
            compose(release, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180")

        articles = json.load(request("/api/library"))["articles"]
        assert any(a["notes"] == "Survives deployment" for a in articles)
        print("Production HTTPS, secure cookies, setup, uploads, notes, private backend and persistent volume: PASS", flush=True)

        if platform.system() == "Linux":
            invalid = root / "releases" / "invalid-config"
            shutil.copytree(release, invalid)
            (invalid / "Caddyfile").write_text("{\n definitely_invalid_option true\n}\n")
            deploy(invalid, digest, succeeds=False)
            assert (root / "current").resolve() == release
            assert json.load(request("/api/library"))["articles"][0]["notes"] == "Survives deployment"
            print("Invalid-config preflight leaves the running release available: PASS", flush=True)
            # A failed release simulates a schema change and writes an extra file.
            # Recovery must restore data as well as the image, and preserve failed data.
            bad_build = root / "bad-build"
            bad_build.mkdir()
            (bad_build / "Dockerfile").write_text(
                f"FROM {image}\n"
                'HEALTHCHECK --interval=1s --timeout=1s --start-period=0s --retries=1 CMD ["python", "-c", "raise SystemExit(1)"]\n'
                'CMD ["python", "-c", "import sqlite3,time; from pathlib import Path; '
                "db=sqlite3.connect('/app/data/accounts.sqlite3'); db.execute('PRAGMA user_version=999'); db.close(); "
                "Path('/app/data/failed-marker').write_text('failed release'); time.sleep(300)"+'"]\n'
            )
            bad_tag = f"localhost:{registry_port}/intel-sourcerer:bad"
            run("docker", "build", "-t", bad_tag, str(bad_build))
            run("docker", "push", bad_tag)
            bad_digest = published_digest(bad_tag)
            bad_release = root / "releases" / "bad"
            shutil.copytree(release, bad_release)
            result = deploy(bad_release, bad_digest, succeeds=False)
            assert "Previous release recovered" in result.stderr, result.stderr
            assert (root / "current").resolve() == release
            assert json.load(request("/api/library"))["articles"][0]["notes"] == "Survives deployment"
            compose(release, "exec", "-T", "backend", "python", "-c",
                "import sqlite3; from pathlib import Path; assert not Path('/app/data/failed-marker').exists(); "
                "assert sqlite3.connect('/app/data/accounts.sqlite3').execute('PRAGMA user_version').fetchone()[0] != 999")
            assert list((root / "backups").glob("*/failed-data/failed-marker"))
            print("Digest deployment, stopped-data backups, failed-release image/data rollback: PASS", flush=True)
    finally:
        try:
            compose(release, "down", "--volumes", "--remove-orphans")
        finally:
            subprocess.run(["docker", "rm", "-f", "-v", registry], capture_output=True)
            shutil.rmtree(root)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "intel-sourcerer:ci")
