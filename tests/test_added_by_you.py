from datetime import datetime, timezone

from intel_sourcerer.api_dispatch import handle_api_get
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.library import render_library
from intel_sourcerer.storage import connect_database, save_article, save_source

FEED = "https://example.com/feed-report"
SAVED = "https://blog.example.org/saved-report"


def library(tmp_path):
    db = connect_database(tmp_path / "articles.sqlite3")
    save_source(db, PublisherSource("feed", "Feed Publisher", "https://example.com", "security_vendor"))
    save_source(db, PublisherSource("link-abc", "blog.example.org", "https://blog.example.org", "manual"))
    now = datetime.now(timezone.utc)
    for source, url, title in (("feed", FEED, "Feed report"), ("link-abc", SAVED, "Saved report")):
        article = Article(source, title, url, url, now, None, None, "")
        save_article(db, source, article, content="Body", markdown="Body", error=None, reason="t", discovered_at="now")
    db.commit()
    return db


def article(db, url):
    payload, status = handle_api_get(
        db, None, None, "/api/article", "url=" + url,
        is_admin=False, read_state_articles=None, transcript_info=lambda *_: None,
    )
    assert status == 200
    return payload


def test_manual_articles_are_flagged(tmp_path):
    db = library(tmp_path)
    assert article(db, SAVED)["added_manually"] is True
    assert article(db, FEED)["added_manually"] is False
    page = render_library(db)
    assert page.count('class="lib-added"') == 1
    assert "· Added by you" in render_library(db, selected=SAVED)
    assert "Added by you" not in render_library(db, selected=FEED).split('class="lib-detail-meta"')[1][:300]


def test_library_filters_by_who_added_it(tmp_path):
    db = library(tmp_path)
    mine = render_library(db, origin="manual")
    assert "Saved report" in mine and "Feed report" not in mine
    assert "Added by you <a" in mine and 'name="origin" value="manual"' in mine
    found = render_library(db, origin="sources")
    assert "Feed report" in found and "Saved report" not in found
    everything = render_library(db, origin="bogus")
    assert "Feed report" in everything and "Saved report" in everything
    assert "By you <span>1</span>" in everything and "origin=manual" in everything
