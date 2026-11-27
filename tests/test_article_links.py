"""Reader links are /read/<id>/<slug>: short, stable across renames, and resolved per library."""
import threading
from datetime import datetime, timezone

import httpx

from intel_sourcerer.article_links import article_href, article_id, read_path_id, slugify
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.storage import connect_database, save_article, save_source
from intel_sourcerer.web import create_server

URL = 'https://www.crowdstrike.com/en-us/blog/crowdstrike-helps-protect-against-nginx2root-cve-2026-123007/'


def test_href_is_a_short_id_and_a_readable_slug():
    assert article_id(URL) == '81ea5fff'
    assert article_href(URL, 'NGINX2Root: Identify and Protect with the CrowdStrike Falcon Platform') == \
        '/read/81ea5fff/nginx2root-identify-and-protect-with-the-crowdstrike-falcon'
    assert slugify('Café — Ünïcode & <script>') == 'cafe-unicode-script'
    assert article_href(URL, '') == '/read/81ea5fff'


def test_only_well_formed_read_paths_match():
    assert read_path_id('/read/81ea5fff/any-slug') == '81ea5fff'
    assert read_path_id('/read/81ea5fff') == '81ea5fff'
    for path in ['/read/81EA5FFF', '/read/81ea5ff', '/read/81ea5fff/a/b', '/reader/81ea5fff', '/']:
        assert read_path_id(path) is None


def test_read_paths_serve_the_reader_and_resolve_in_this_library(tmp_path):
    path = tmp_path / 'articles.sqlite3'
    db = connect_database(path)
    source = PublisherSource('test', 'Test', 'https://example.com', 'security_vendor')
    save_source(db, source)
    save_article(db, source.id, Article('Test', 'NGINX2Root', URL, URL, datetime.now(timezone.utc), None, None, ''),
                 content='Article text', error=None, reason='test', discovered_at='today')
    db.commit()
    server = create_server(path, 0, auth_enabled=False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as client:
            page = client.get('/read/81ea5fff/renamed-later')
            assert page.status_code == 200 and 'id="reader"' in page.text
            assert client.get('/read/zzzz').status_code == 404
            assert client.get('/api/resolve-article', params={'id': '81ea5fff'}).json() == {'url': URL, 'href': '/read/81ea5fff/nginx2root'}
            assert client.get('/api/resolve-article', params={'url': URL}).json()['href'] == '/read/81ea5fff/nginx2root'
            assert client.get('/api/resolve-article', params={'id': '00000000'}).status_code == 404
            assert client.get('/api/article', params={'url': URL}).json()['read_href'] == '/read/81ea5fff/nginx2root'
    finally:
        server.shutdown()
        db.close()


def test_pages_version_their_scripts_and_styles():
    import re

    from intel_sourcerer.templates import load_template, versioned

    page = load_template('workspace/index.html')
    assert re.search(r'src="/reader\.js\?v=[0-9a-f]+"', page)
    assert re.search(r'href="/reader\.css\?v=[0-9a-f]+"', page)
    assert re.search(r'src="/app\.js\?v=[0-9a-f]+"', versioned('<script src="/app.js"></script>'))
    # Unknown paths are left alone rather than given a made-up version.
    assert versioned('<script src="/not-served.js"></script>') == '<script src="/not-served.js"></script>'
