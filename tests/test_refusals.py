import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import intel_sourcerer.feeds as feeds
import intel_sourcerer.importer as importer
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import FeedSource, fetch_feed, refusal_from, retry_after_seconds
from intel_sourcerer.storage import connect_database

SOURCE = PublisherSource('vendor', 'Vendor', 'https://example.com', 'security_vendor', 'https://example.com/rss')
SINCE = datetime(2026, 9, 1, tzinfo=timezone.utc)
UNTIL = datetime(2026, 10, 1, tzinfo=timezone.utc)
ARTICLE = 'https://example.com/report'
RSS = b'''<rss version="2.0"><channel><item><title>Ransomware report</title><link>https://example.com/report</link>
<pubDate>Tue, 15 Sep 2026 10:00:00 GMT</pubDate></item></channel></rss>'''
PAGE = '<html><head><title>Ransomware report</title></head><body><article><h1>Ransomware report</h1>' + '<p>Long enough body text for extraction to keep this paragraph of the report.</p>' * 12 + '</article></body></html>'
CHALLENGE = {'x-vercel-mitigated': 'challenge'}


@pytest.fixture(autouse=True)
def no_real_waits(monkeypatch):
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(feeds.asyncio, 'sleep', sleep)
    return waits


def run_import(monkeypatch, tmp_path, handler, since=SINCE):
    monkeypatch.setattr(importer, 'load_sources', lambda _config: [SOURCE])
    monkeypatch.setattr(importer, 'public_only', lambda _client: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True))
    return asyncio.run(importer.import_articles('unused', tmp_path / 'test.db', since, UNTIL))


def state(tmp_path):
    db = connect_database(tmp_path / 'test.db')
    try:
        return importer.fetch_state(db, SOURCE.id)
    finally:
        db.close()


@pytest.mark.parametrize('status,headers,message,pause,wait', [
    (429, CHALLENGE, 'bot check', feeds.BOT_CHECK_PAUSE, None),
    (403, {'cf-mitigated': 'challenge'}, 'bot check', feeds.BOT_CHECK_PAUSE, None),
    (429, {}, 'rate limited', feeds.RATE_LIMIT_PAUSE, None),
    (429, {'retry-after': '5'}, 'rate limited', 60, 5),
    (429, {'retry-after': '7200'}, 'rate limited', 7200, None),
    (429, {'retry-after': '9999999'}, 'rate limited', feeds.MAX_PAUSE, None),
    (503, {'retry-after': '5'}, 'rate limited', 60, 5),
])
def test_refusals_are_classified(status, headers, message, pause, wait):
    refused = refusal_from(httpx.Response(status, headers=headers))
    assert message in refused.message and refused.pause_seconds == pause and refused.retry_wait == wait


@pytest.mark.parametrize('status', [200, 403, 404, 500, 503])
def test_ordinary_responses_are_not_refusals(status):
    assert refusal_from(httpx.Response(status)) is None


def test_retry_after_reads_seconds_dates_and_nonsense():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert retry_after_seconds('120') == 120
    assert retry_after_seconds('Thu, 01 Jan 2026 00:01:00 GMT', now) == 60
    assert retry_after_seconds('Wed, 31 Dec 2025 00:00:00 GMT', now) == 0
    assert retry_after_seconds('soon') is None and retry_after_seconds(None) is None


def fetch_with(handler):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_feed(client, FeedSource('Vendor', 'https://example.com/rss'))
    return asyncio.run(run())


def test_a_short_wait_is_honoured_and_retried_once(no_real_waits):
    replies = iter([httpx.Response(429, headers={'retry-after': '3'}), httpx.Response(200, content=RSS)])
    result = fetch_with(lambda request: next(replies))
    assert no_real_waits == [3] and result.error is None and len(result.articles) == 1


def test_a_second_refusal_stops(no_real_waits):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={'retry-after': '3'})

    result = fetch_with(handler)
    assert len(calls) == 2 and no_real_waits == [3] and result.refusal


def test_a_bot_check_is_not_retried(no_real_waits):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers=CHALLENGE)

    result = fetch_with(handler)
    assert len(calls) == 1 and no_real_waits == [] and 'bot check' in result.error


def test_a_bot_check_pauses_the_source_until_the_pause_ends(monkeypatch, tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(429, headers=CHALLENGE)

    report = run_import(monkeypatch, tmp_path, handler)
    assert "Vendor: blocked by the site's bot check. Trying again in about 6 hours." == report['errors'][0]
    report = run_import(monkeypatch, tmp_path, handler)
    assert len(calls) == 1 and not report['errors']
    assert 'Vendor: blocked' in report['paused'][0]
    db = connect_database(tmp_path / 'test.db')
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    importer.save_fetch_state(db, SOURCE.id, paused_until=past.isoformat())
    db.commit()
    db.close()
    run_import(monkeypatch, tmp_path, handler)
    assert len(calls) == 2


def feed_site(calls, *, article=None):
    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == ARTICLE:
            return article or httpx.Response(200, text=PAGE, headers={'content-type': 'text/html'})
        if request.headers.get('if-none-match') == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=RSS, headers={'etag': '"v1"'})
    return handler


def test_an_unchanged_feed_and_saved_text_are_not_fetched_again(monkeypatch, tmp_path):
    calls = []
    first = run_import(monkeypatch, tmp_path, feed_site(calls))
    assert first['sources'][0]['inserted'] == 1 and calls == [SOURCE.rss_url, ARTICLE]
    assert state(tmp_path)['etag'] == '"v1"'
    calls.clear()
    second = run_import(monkeypatch, tmp_path, feed_site(calls))
    assert calls == [SOURCE.rss_url] and not second['errors']


def test_a_wider_window_reads_the_feed_again_without_refetching_saved_text(monkeypatch, tmp_path):
    calls = []
    run_import(monkeypatch, tmp_path, feed_site(calls))
    seen = []

    def handler(request):
        seen.append(request.headers.get('if-none-match'))
        return feed_site(calls)(request)

    report = run_import(monkeypatch, tmp_path, handler, since=SINCE - timedelta(days=30))
    assert seen == [None] and report['sources'][0]['already_saved'] == 1


def test_a_failed_article_is_tried_again_rather_than_hidden_by_a_304(monkeypatch, tmp_path):
    calls = []
    report = run_import(monkeypatch, tmp_path, feed_site(calls, article=httpx.Response(500)))
    assert report['sources'][0]['metadata_only'] == 1 and state(tmp_path)['etag'] is None
    calls.clear()
    report = run_import(monkeypatch, tmp_path, feed_site(calls))
    assert calls == [SOURCE.rss_url, ARTICLE] and report['sources'][0]['full_text'] == 1


def test_a_refused_article_pauses_its_source(monkeypatch, tmp_path):
    calls = []
    report = run_import(monkeypatch, tmp_path, feed_site(calls, article=httpx.Response(429, headers=CHALLENGE)))
    saved = state(tmp_path)
    assert saved['paused_until'] and saved['etag'] is None
    assert 'bot check' in report['errors'][0]


def test_success_clears_a_finished_pause(monkeypatch, tmp_path):
    calls = []
    db = connect_database(tmp_path / 'test.db')
    importer.save_fetch_state(db, SOURCE.id, paused_until=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), pause_reason='old')
    db.commit()
    db.close()
    run_import(monkeypatch, tmp_path, feed_site(calls))
    assert state(tmp_path)['paused_until'] is None


def test_adding_a_blocked_link_explains_what_to_do(monkeypatch, tmp_path):
    from intel_sourcerer import discovery

    monkeypatch.setattr(discovery, 'public_url', lambda url: url)
    monkeypatch.setattr(discovery, 'public_only', lambda _client: httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(429, headers=CHALLENGE))))
    with pytest.raises(ValueError, match='bot check.*PDF'):
        asyncio.run(discovery.import_link(tmp_path / 'test.db', 'https://example.com/blocked'))
