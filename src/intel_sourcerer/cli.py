"""Inspect configured RSS feeds without writing to a database."""
import argparse
import asyncio
from dataclasses import asdict
import json
import sys

from .config import load_sources
from .feeds import FeedSource, fetch_feeds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="*", help="Optional explicit RSS/Atom URLs overriding config")
    parser.add_argument("--config", default="config/sources.toml", help="Source TOML file (default: config/sources.toml relative to working directory)")
    args = parser.parse_args()
    if args.urls:
        sources = [FeedSource(url, url) for url in args.urls]
    else:
        try:
            publishers = load_sources(args.config)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        sources = []
        for publisher in publishers:
            if not publisher.enabled:
                continue
            feed = publisher.as_feed()
            if feed:
                sources.append(feed)
            else:
                print(f"Skipping {publisher.name}: no RSS URL; listing adapter required ({publisher.parent_url})", file=sys.stderr)
        if not sources:
            parser.error("Config has no enabled RSS sources")
    results = asyncio.run(fetch_feeds(sources))
    print(json.dumps([asdict(result) for result in results], default=str, indent=2))
    if any(result.error for result in results):
        raise SystemExit(1)
