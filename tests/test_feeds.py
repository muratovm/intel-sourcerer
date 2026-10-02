import asyncio
from datetime import timezone

import httpx
import pytest

from intel_sourcerer.feeds import FeedCache, FeedSource, fetch_feed, fetch_feeds, parse_feed

SOURCE = FeedSource("Example", "https://example.com/feed")
RSS = b'''<rss version="2.0"><channel><title>Example</title><link>https://example.com</link><description>News</description>
<item><title>Security report</title><link>https://example.com/report#top</link><guid>report-1</guid><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate><description>Summary</description></item>
<item><title>Duplicate</title><link>https://example.com/report</link></item>
<item><title>Missing link</title></item>
</channel></rss>'''
ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>News</title><id>urn:news</id><updated>2026-10-01T12:00:00Z</updated><entry><title>Agent security</title><id>urn:article</id><link href="https://example.com/agent"/><updated>2026-10-01T12:00:00Z</updated></entry></feed>'''


def test_rss_normalizes_dates_deduplicates_and_warns():
    articles, warnings = parse_feed(RSS, SOURCE)
    assert len(articles) == 1
    assert articles[0].url == "https://example.com/report"
    assert articles[0].external_id == "report-1"
    assert articles[0].published_at.tzinfo == timezone.utc
    assert warnings


def test_atom_and_missing_publication_date():
    articles, _ = parse_feed(ATOM, SOURCE)
    assert articles[0].external_id == "urn:article"
    assert articles[0].published_at is None
    assert articles[0].updated_at is not None


def test_html_is_rejected():
    with pytest.raises(ValueError, match="not a recognized"):
        parse_feed(b"<html><body>Access denied</body></html>", SOURCE)


def run_fetch(handler, cache=None, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_feed(client, SOURCE, cache, **kwargs)
    return asyncio.run(run())


def test_fetch_stores_validators():
    result = run_fetch(lambda request: httpx.Response(200, content=RSS, headers={"etag": '"v1"'}))
    assert result.error is None
    assert len(result.articles) == 1
    assert result.cache.etag == '"v1"'


def test_conditional_request_and_304():
    cache = FeedCache('"v1"', "Thu, 01 Oct 2026 12:00:00 GMT")
    def handler(request):
        assert request.headers["if-none-match"] == cache.etag
        assert request.headers["if-modified-since"] == cache.last_modified
        return httpx.Response(304)
    result = run_fetch(handler, cache)
    assert result.not_modified and not result.articles
    assert result.cache == cache


@pytest.mark.parametrize("status", [403, 429, 500])
def test_http_failures_preserve_previous_cache(status):
    cache = FeedCache('"old"')
    result = run_fetch(lambda request: httpx.Response(status), cache)
    assert result.error
    assert result.cache == cache


def test_timeout_is_a_source_error():
    def handler(request):
        raise httpx.ReadTimeout("Timed out", request=request)
    assert run_fetch(handler).error == "Timed out"


def test_size_limit_and_invalid_response_preserve_cache():
    cache = FeedCache('"old"')
    result = run_fetch(lambda request: httpx.Response(200, content=RSS), cache, max_bytes=10)
    assert "exceeds" in result.error
    assert result.cache == cache
    result = run_fetch(lambda request: httpx.Response(200, content=b"not a feed", headers={"etag": '"new"'}), cache)
    assert result.error and result.cache == cache


def test_redirect_and_relative_article_link():
    def handler(request):
        if request.url.path == "/feed":
            return httpx.Response(302, headers={"location": "/news/feed"})
        return httpx.Response(200, content=RSS.replace(b"https://example.com/report#top", b"../report#top"))
    assert run_fetch(handler).articles[0].url == "https://example.com/report"


def test_concurrency_bound_and_failure_isolation():
    active = peak = 0
    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(500 if request.url.path == "/bad" else 200, content=RSS)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            sources = [FeedSource(str(i), f"https://example.com/{i}") for i in range(5)] + [FeedSource("Bad", "https://example.com/bad")]
            return await fetch_feeds(sources, concurrency=2, client=client)
    results = asyncio.run(run())
    assert peak == 2
    assert all(result.articles for result in results[:-1])
    assert results[-1].error
