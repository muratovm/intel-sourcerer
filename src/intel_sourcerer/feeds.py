"""Fetch and normalize RSS/Atom feeds; fetching never persists reading state."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from calendar import timegm
from urllib.parse import urljoin, urlsplit, urlunsplit

import feedparser
import httpx


@dataclass(frozen=True)
class FeedSource:
    name: str
    url: str


@dataclass(frozen=True)
class FeedCache:
    """Persist these validators only after successfully storing returned articles."""
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class Article:
    source: str
    title: str
    url: str
    external_id: str
    published_at: datetime | None
    updated_at: datetime | None
    author: str | None
    summary_html: str


@dataclass
class FeedResult:
    source: FeedSource
    articles: list[Article] = field(default_factory=list)
    cache: FeedCache = field(default_factory=FeedCache)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    not_modified: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def _web_url(value: str, base: str) -> str | None:
    url = urlsplit(urljoin(base, value))
    if url.scheme not in {"http", "https"} or not url.hostname:
        return None
    return urlunsplit((url.scheme, url.netloc, url.path, url.query, ""))


def _date(value) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(timegm(value), timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return None


def parse_feed(data: bytes, source: FeedSource, *, base_url: str | None = None) -> tuple[list[Article], list[str]]:
    """Parse feed bytes without network access. HTML remains untrusted source data."""
    parsed = feedparser.parse(data)
    if not parsed.version:
        raise ValueError("Response is not a recognized RSS or Atom feed")
    warnings = []
    if parsed.get("bozo"):
        warnings.append(f"Feed parsing warning: {parsed.get('bozo_exception')}")
    articles, seen = [], set()
    for entry in parsed.entries:
        link = entry.get("link", "")
        url = _web_url(link, base_url or source.url) if link else None
        if not url:
            warnings.append("Skipped entry without a valid HTTP article link")
            continue
        if url in seen:
            continue
        seen.add(url)
        articles.append(Article(
            source=source.name,
            title=entry.get("title", "").strip() or "Untitled",
            url=url,
            external_id=entry.get("id") or url,
            published_at=_date(entry.get("published_parsed")),
            updated_at=_date(entry["updated_parsed"] if "updated_parsed" in entry else None),
            author=entry.get("author"),
            summary_html=entry.get("summary", ""),
        ))
    return articles, warnings


async def fetch_feed(
    client: httpx.AsyncClient,
    source: FeedSource,
    cache: FeedCache | None = None,
    *,
    max_bytes: int = 5_000_000,
) -> FeedResult:
    """Return a per-source result, including recoverable HTTP/parse failures.

    The caller owns the shared client and retry scheduling. Failed checks retain
    previous validators. A 304 means unchanged, not an empty replacement feed.
    """
    previous = cache or FeedCache()
    result = FeedResult(source=source, cache=previous)
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    headers = {"User-Agent": "IntelSourcerer/0.1", "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml"}
    if previous.etag:
        headers["If-None-Match"] = previous.etag
    if previous.last_modified:
        headers["If-Modified-Since"] = previous.last_modified
    try:
        async with client.stream("GET", source.url, headers=headers, follow_redirects=True, timeout=20) as response:
            if response.status_code == 304:
                result.not_modified = True
                result.cache = FeedCache(response.headers.get("etag", previous.etag), response.headers.get("last-modified", previous.last_modified))
                return result
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > max_bytes:
                    raise ValueError(f"Feed exceeds {max_bytes} bytes")
                body.extend(chunk)
            result.articles, result.warnings = await asyncio.to_thread(parse_feed, bytes(body), source, base_url=str(response.url))
            result.cache = FeedCache(response.headers.get("etag"), response.headers.get("last-modified"))
    except (httpx.HTTPError, ValueError) as exc:
        result.error = str(exc)
    return result


async def fetch_feeds(
    sources: list[FeedSource] | tuple[FeedSource, ...],
    caches: dict[str, FeedCache] | None = None,
    *,
    concurrency: int = 4,
    client: httpx.AsyncClient | None = None,
) -> list[FeedResult]:
    """Check feeds concurrently with a shared connection pool and bounded load."""
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    gate = asyncio.Semaphore(concurrency)

    async def collect(shared):
        async def one(source):
            async with gate:
                return await fetch_feed(shared, source, (caches or {}).get(source.url))
        return await asyncio.gather(*(one(source) for source in sources))

    if client is not None:
        return await collect(client)
    async with httpx.AsyncClient() as shared:
        return await collect(shared)
