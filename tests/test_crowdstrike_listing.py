"""CrowdStrike's feed holds only the latest ten posts, so its category pages fill in the rest."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

import intel_sourcerer.importer as importer
from intel_sourcerer.config import PublisherSource, load_sources
from intel_sourcerer.feeds import Article, FeedResult
from intel_sourcerer.importer import crowdstrike_listing

CATEGORY = 'https://www.crowdstrike.com/en-us/blog/category.securing-ai/'
SOURCE = PublisherSource(
    'crowdstrike', 'CrowdStrike', 'https://www.crowdstrike.com/en-us/blog/', 'security_vendor',
    'https://www.crowdstrike.com/en-us/blog/feed', listing_urls=(CATEGORY,),
)
FIXTURE = (Path(__file__).parent / 'fixtures' / 'crowdstrike_category.html').read_text()


def test_category_cards_keep_dated_crowdstrike_posts_only():
    articles = crowdstrike_listing(FIXTURE, SOURCE, CATEGORY)
    assert [a.url for a in articles] == [
        'https://www.crowdstrike.com/en-us/blog/supply-chain-risk-from-backdoored-lora-models/',
        'https://www.crowdstrike.com/en-us/blog/crowdstrike-to-acquire-fiddler/',
    ]
    lora = articles[0]
    assert lora.title.startswith('Hidden in the Weights')
    assert lora.author == 'Donato Onofri'
    assert 'Backdoored adapters' in lora.summary_html
    assert lora.published_at == datetime(2026, 11, 16, 5, tzinfo=timezone.utc)


def test_listing_urls_load_from_config(tmp_path):
    path = tmp_path / 'sources.toml'
    path.write_text(
        'schema_version = 1\n[[sources]]\nid = "cs"\nname = "CS"\nsource_type = "security_vendor"\n'
        f'parent_url = "https://www.crowdstrike.com/en-us/blog/"\nlisting_urls = ["{CATEGORY}"]\n'
    )
    assert load_sources(path)[0].listing_urls == (CATEGORY,)
    path.write_text(path.read_text().replace(CATEGORY, 'file:///etc/passwd'))
    with pytest.raises(ValueError, match='listing_urls'):
        load_sources(path)


def test_import_merges_category_pages_with_the_feed(tmp_path, monkeypatch):
    lora_url = 'https://www.crowdstrike.com/en-us/blog/supply-chain-risk-from-backdoored-lora-models/'
    feed_copy = Article('CrowdStrike', 'LoRA backdoors', lora_url, lora_url, datetime(2026, 11, 16, tzinfo=timezone.utc), None, None, 'malware', '<p>full post</p>')
    monkeypatch.setattr(importer, 'load_sources', lambda _: [SOURCE])

    async def fetch(client, feed, cache=None):
        return FeedResult(feed, [feed_copy])

    class Page:
        text = FIXTURE
        url = CATEGORY

    fetched = []

    async def get(client, url):
        fetched.append(url)
        if url == CATEGORY:
            return Page()
        raise importer.httpx.ConnectError('offline')

    monkeypatch.setattr(importer, 'fetch_feed', fetch)
    monkeypatch.setattr(importer, 'polite_get', get)
    report = asyncio.run(importer.import_articles(
        'unused', tmp_path / 'cs.db', datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 20, tzinfo=timezone.utc),
    ))
    stats = report['sources'][0]
    assert fetched[0] == CATEGORY
    # The LoRA post is in both; the feed copy wins, so it counts once and keeps its keyword match.
    assert stats['discovered'] == 2 and stats['selected'] == 1
    assert [e['title'] for e in stats['excluded']] == ['CrowdStrike to Acquire Fiddler']
    assert not any('no listing adapter' in error for error in report['errors'])


def test_listing_summary_fills_a_feed_item_without_one(tmp_path, monkeypatch):
    url = 'https://www.crowdstrike.com/en-us/blog/supply-chain-risk-from-backdoored-lora-models/'
    bare = Article('CrowdStrike', 'Hidden in the Weights', url, url, datetime(2026, 11, 16, tzinfo=timezone.utc), None, None, '', '<p>full post</p>')
    monkeypatch.setattr(importer, 'load_sources', lambda _: [SOURCE])

    async def fetch(client, feed, cache=None):
        return FeedResult(feed, [bare])

    class Page:
        text = FIXTURE.replace('Backdoored adapters can ride along', 'A backdoor can ride along')
        url = CATEGORY

    async def get(client, page):
        if page == CATEGORY:
            return Page()
        raise importer.httpx.ConnectError('offline')

    monkeypatch.setattr(importer, 'fetch_feed', fetch)
    monkeypatch.setattr(importer, 'polite_get', get)
    report = asyncio.run(importer.import_articles(
        'unused', tmp_path / 'cs.db', datetime(2026, 11, 1, tzinfo=timezone.utc), datetime(2026, 11, 20, tzinfo=timezone.utc),
    ))
    assert report['sources'][0]['selected'] == 1
