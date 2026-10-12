"""Serve a frontend-only reading session from supplied articles."""

import argparse
import json
import uuid
from datetime import UTC, datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from .content import extract_markdown, render_markdown
from .templates import UI_ROOT, load_template
from .viewer import CSP, STATIC_FILES


def prepare_article(record, index):
    """Normalize supplied content without importing it into storage."""
    if not isinstance(record, dict):
        raise ValueError("Each article must be an object.")  # noqa: TRY004
    url = record.get("url") or f"https://temporary.invalid/article/{index}"
    if not isinstance(url, str) or urlsplit(url).scheme not in {
        "http",
        "https",
        "file",
    }:
        raise ValueError("Article URLs must use http, https, or file.")
    markdown = record.get("content_markdown", record.get("markdown"))
    text = record.get("content_text", record.get("text"))
    html = record.get("body_html", record.get("html"))
    if html and not markdown:
        markdown, _ = extract_markdown(str(html), url)
    if not isinstance(markdown or text, str) or not (markdown or text).strip():
        raise ValueError(
            "Supply markdown, content_markdown, text, or html for each article."
        )
    from .sessions import clean_notes, has_notes

    notes_format = record.get("notes_format", "plain")
    if notes_format not in {"plain", "html"}:
        raise ValueError("Invalid notes format.")
    notes = str(record.get("notes", ""))
    if notes_format == "html":
        notes = clean_notes(notes)
    published = record.get("published_at") or datetime.now(UTC).isoformat()
    try:
        datetime.fromisoformat(published)
    except (ValueError, TypeError):
        raise ValueError("published_at must be an ISO date or timestamp.") from None
    body = BeautifulSoup(
        render_markdown(markdown)
        if markdown
        else "<p>" + escape(text).replace("\n", "<br>") + "</p>",
        "html.parser",
    )
    heading = body.find("h1")
    title = str(
        record.get("title")
        or (heading.get_text(" ", strip=True) if heading else f"Article {index + 1}")
    )
    if heading:
        heading.decompose()
    for block in body.find_all("blockquote"):
        label, link = block.find("strong"), block.find("a", href=True)
        if (
            label
            and link
            and label.get_text().strip()
            in {
                "Image placeholder:",
                "Video placeholder:",
                "Audio placeholder:",
                "Embedded media placeholder:",
            }
        ):
            from .content import safe_url

            media_url = safe_url(link["href"], url)
            if media_url:
                block["data-media-url"] = media_url
                block["data-media-kind"] = label.get_text().split()[0].lower()
                block["data-media-description"] = block.get_text(" ", strip=True)
    return {
        "source": record.get("source")
        or {
            key: value
            for key, value in record.items()
            if key
            not in {
                "notes",
                "notes_format",
                "notes_revision",
                "is_read",
                "has_notes",
                "notes_updated_at",
                "source",
            }
        },
        "url": url,
        "title": title,
        "publisher": str(record.get("publisher", "Temporary session")),
        "published_at": published,
        "content_format": "article",
        "content_markdown": markdown or "",
        "body_html": str(body),
        "notes": notes,
        "notes_format": notes_format,
        "notes_revision": 0,
        "is_read": bool(record.get("is_read", False)),
        "has_notes": has_notes(notes, notes_format),
        "notes_updated_at": None,
        "article_types": ["Article"],
        "note_preview": "",
    }


def load_article(value):
    """Load a URL or a local Markdown/HTML/text/PDF supplied at startup."""
    parsed = urlsplit(value)
    remote = parsed.scheme in {"http", "https"}
    if remote:
        from .arxiv import preferred_article

        preferred = preferred_article(value)
        if preferred:
            return preferred
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.get(value)
            response.raise_for_status()
            if len(response.content) > 20_000_000:
                raise ValueError("Article exceeds 20 MB.")
            data = response.content
            url = str(response.url)
            suffix = Path(urlsplit(url).path).suffix.lower()
            pdf = "application/pdf" in response.headers.get(
                "Content-Type", ""
            ) or data.startswith(b"%PDF-")
    else:
        path = Path(value).expanduser().resolve()
        if path.stat().st_size > 20_000_000:
            raise ValueError("Article exceeds 20 MB.")
        data, url, suffix = path.read_bytes(), path.as_uri(), path.suffix.lower()
        pdf = suffix == ".pdf"
    if pdf:
        from .pdf_content import extract_pdf

        title, _, markdown = extract_pdf(data, url)
        return {"url": url, "title": title, "markdown": markdown}
    text = data.decode("utf-8")
    if suffix in {".md", ".markdown", ".txt"}:
        heading = next(
            (
                line.removeprefix("# ")
                for line in text.splitlines()
                if line.startswith("# ")
            ),
            None,
        )
        return (
            {
                "url": url,
                "title": heading or Path(urlsplit(url).path).name,
                "markdown": text,
            }
            if suffix != ".txt"
            else {"url": url, "title": Path(urlsplit(url).path).name, "text": text}
        )
    soup = BeautifulSoup(text, "html.parser")
    title = soup.find("h1") or soup.find("title")
    return {
        "url": url,
        "title": title.get_text(" ", strip=True) if title else url,
        "html": text,
    }


def temporary_page(session_id, guest=False):
    page = (
        load_template("workspace/index.html")
        .replace("__USER_ID__", "temporary-" + session_id)
        .replace("__TOKEN__", "temporary")
    )
    page = page.replace(
        '<script src="/reader.js" defer>',
        '<script src="/temporary.js" defer></script>\n    <script src="/reader.js" defer>',
    )
    page = page.replace(
        "<body>",
        '<body class="temporary-session"><div class="temporary-banner">Temporary reading session · no account or saved library <button id="export-temporary">Export articles &amp; notes</button><span>Notes stay in this tab. Export before closing it.</span></div>',
    )

    if guest:
        page = page.replace("/reader.js", "/guest-reader.js")
        page = page.replace(
            "<span>Notes stay",
            '<a href="/login">Sign in</a><a href="/guest">New guest queue</a><span>Notes stay',
        )
    return page


def temporary_reader_script():
    return (
        (UI_ROOT / "workspace/workspace.js")
        .read_text()
        .replace("localStorage", "sessionStorage")
        .replace(
            '!["https:", "http:"].includes(source.protocol)',
            '!["https:", "http:"].includes(source.protocol) || source.hostname === "temporary.invalid"',
        )
        .replace(
            "else renderHome();",
            'else { if (!sessionStorage.getItem(userStoragePrefix + "intel-queue-draft")) { draftQueue = state.queue.items.map(item => item.url); saveQueueDraft(); } renderSetup(); }',
        )
    )


def create_temporary_viewer(articles, port=8767, host="127.0.0.1"):
    """Serve only immutable inputs/assets; all editing state lives in the tab."""
    if not isinstance(articles, list) or not 1 <= len(articles) <= 100:
        raise ValueError("Supply between 1 and 100 articles.")
    prepared = [prepare_article(record, index) for index, record in enumerate(articles)]
    if len({record["url"] for record in prepared}) != len(prepared):
        raise ValueError("Articles must have distinct URLs.")
    session_id = uuid.uuid4().hex
    payload = json.dumps({"id": session_id, "articles": prepared}).encode()
    page = temporary_page(session_id)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            content_type = "text/html; charset=utf-8"
            if path == "/":
                body = page.encode()
            elif path == "/temporary-session.json":
                body, content_type = payload, "application/json"
            elif path == "/temporary.js":
                body, content_type = (
                    (UI_ROOT / "workspace/temporary.js").read_bytes(),
                    "text/javascript; charset=utf-8",
                )
            elif path == "/brand-icon.png":
                body, content_type = (
                    (UI_ROOT.parent / "brand-icon.png").read_bytes(),
                    "image/png",
                )
            elif path in STATIC_FILES:
                name, content_type = STATIC_FILES[path]
                body = (UI_ROOT / name).read_bytes()
                if path == "/reader.js":
                    body = temporary_reader_script().encode()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if urlsplit(self.path).path != "/temporary/parse":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if (
                    self.headers.get("X-Guest-Request") != "1"
                    or "Transfer-Encoding" in self.headers
                    or not 0 < length <= 20_000_000
                ):
                    raise ValueError("Invalid temporary-session request.")
                data = parse_session_content(
                    self.rfile.read(length), self.headers.get("Content-Type", "")
                )
                body, status = json.dumps(data).encode(), 200
            except (ValueError, TypeError, OSError, httpx.HTTPError) as exc:
                body, status = json.dumps({"error": str(exc)}).encode(), 400
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


def public_link(value):
    """Guest URL imports may only address public HTTP(S) sites."""
    import ipaddress
    import socket

    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 80, 443}
    ):
        raise ValueError("Use a public HTTP(S) article URL.")
    addresses = socket.getaddrinfo(
        parsed.hostname,
        parsed.port or (443 if parsed.scheme == "https" else 80),
        type=socket.SOCK_STREAM,
    )
    if not addresses or any(
        not ipaddress.ip_address(item[4][0]).is_global for item in addresses
    ):
        raise ValueError(
            "Temporary sessions cannot fetch private network or local URLs."
        )
    return addresses[0][4][0]


def load_public_article(value, *, prefer_arxiv=True):
    """Validate every redirect and bound downloaded article content."""
    public_link(value)
    if prefer_arxiv:
        from .arxiv import preferred_article

        preferred = preferred_article(value)
        if preferred:
            return preferred
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        for _ in range(6):
            address = public_link(value)
            origin = urlsplit(value)
            target = httpx.URL(value).copy_with(host=address)
            with client.stream(
                "GET",
                target,
                headers={"Host": origin.netloc},
                extensions={"sni_hostname": origin.hostname.encode("idna").decode()},
            ) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin

                    value = urljoin(value, response.headers["location"])
                    continue
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > 20_000_000:
                        raise ValueError("Article exceeds 20 MB.")
                if data.startswith(b"%PDF-"):
                    from .pdf_content import extract_pdf

                    title, _, markdown = extract_pdf(bytes(data), value)
                    return {"url": value, "title": title, "markdown": markdown}
                soup = BeautifulSoup(bytes(data), "html.parser")
                title = soup.find("h1") or soup.find("title")
                return {
                    "url": value,
                    "title": title.get_text(" ", strip=True) if title else value,
                    "html": str(soup),
                }
    raise ValueError("Too many article redirects.")


def parse_session_content(body, content_type):
    """Stateless parsing: sources arrive from the tab and are never persisted."""
    if content_type.startswith("application/pdf"):
        from .pdf_content import extract_pdf

        url = "https://temporary.invalid/" + uuid.uuid4().hex + ".pdf"
        title, _, markdown = extract_pdf(body, url)
        records = [{"title": title, "url": url, "markdown": markdown}]
    else:
        records = json.loads(body).get("articles")
    if not isinstance(records, list) or not 1 <= len(records) <= 100:
        raise ValueError("Choose between 1 and 100 articles.")
    articles = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError("Invalid article source.")  # noqa: TRY004
        source = record.get("source") or record
        if not any(
            source.get(key)
            for key in (
                "markdown",
                "content_markdown",
                "text",
                "content_text",
                "html",
                "body_html",
            )
        ):
            loaded = load_public_article(source.get("url", ""))
            loaded["url"] = source["url"]
            loaded["source"] = source
            article = prepare_article(loaded, index)
        else:
            article = prepare_article(source, index)
        articles.append(article)
    if len({article["url"] for article in articles}) != len(articles):
        raise ValueError("Articles must have different URLs.")
    return {"articles": articles}


def load_inputs(values, manifest=None):
    articles = []
    if manifest:
        supplied = json.loads(Path(manifest).expanduser().read_text())
        articles = supplied.get("articles") if isinstance(supplied, dict) else supplied
        if not isinstance(articles, list):
            raise ValueError("The JSON file must contain an articles list.")
    if not 1 <= len(articles) + len(values) <= 100:
        raise ValueError("Supply between 1 and 100 articles.")
    articles.extend(load_article(value) for value in values)
    return articles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--article",
        action="append",
        default=[],
        help="URL or local Markdown, HTML, text or PDF; repeat for multiple articles",
    )
    parser.add_argument(
        "--articles",
        help="JSON file containing an article list or an object with an articles list",
    )
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    try:
        articles = load_inputs(args.article, args.articles)
        server = create_temporary_viewer(articles, args.port, args.host)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        parser.error(str(exc))
    print(f"Temporary reader: http://{args.host}:{server.server_port}", flush=True)
    print(
        "No account, database or backend. Export notes before closing the browser tab.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
