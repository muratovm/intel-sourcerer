"""Local SQLite storage for imported source articles."""
import sqlite3
from pathlib import Path


# How long a write waits for another writer before giving up, in seconds.
BUSY_TIMEOUT = 15


def use_write_ahead_log(db):
    """Let readers keep working during a write. The mode is stored in the file, so this sticks."""
    try:
        db.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # Another connection is mid-write; the next open tries again, and the old mode still works.
        pass


def connect_database(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=BUSY_TIMEOUT)
    db.row_factory = sqlite3.Row
    use_write_ahead_log(db)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript('''
    CREATE TABLE IF NOT EXISTS sources (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, source_type TEXT NOT NULL,
      parent_url TEXT NOT NULL, enabled INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS articles (
      url TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
      title TEXT NOT NULL, published_at TEXT NOT NULL, author TEXT,
      summary_html TEXT NOT NULL, content_text TEXT, content_status TEXT NOT NULL,
      fetch_error TEXT, relevance_reason TEXT NOT NULL, discovered_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS articles_publication ON articles(published_at);
    CREATE TABLE IF NOT EXISTS import_runs (
      id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, since TEXT NOT NULL,
      until TEXT NOT NULL, report_json TEXT NOT NULL
    );
    ''')
    columns = {row[1] for row in db.execute('PRAGMA table_info(articles)')}
    if 'content_format' not in columns:
        db.execute("ALTER TABLE articles ADD COLUMN content_format TEXT NOT NULL DEFAULT 'article'")
    if 'content_markdown' not in columns:
        db.execute('ALTER TABLE articles ADD COLUMN content_markdown TEXT')
    if 'content_html' not in columns:
        db.execute('ALTER TABLE articles ADD COLUMN content_html TEXT')
    if 'media_json' not in columns:
        db.execute("ALTER TABLE articles ADD COLUMN media_json TEXT NOT NULL DEFAULT '[]'")
    if 'is_read' not in columns:
        db.execute('ALTER TABLE articles ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1))')
    if 'notes' not in columns:
        db.execute("ALTER TABLE articles ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
    if 'notes_format' not in columns:
        db.execute("ALTER TABLE articles ADD COLUMN notes_format TEXT NOT NULL DEFAULT 'plain'")
    if 'notes_updated_at' not in columns:
        db.execute('ALTER TABLE articles ADD COLUMN notes_updated_at TEXT')
    if 'notes_revision' not in columns:
        db.execute('ALTER TABLE articles ADD COLUMN notes_revision INTEGER NOT NULL DEFAULT 0')
    db.executescript('''CREATE TABLE IF NOT EXISTS reading_queues (
      id TEXT PRIMARY KEY,created_at TEXT NOT NULL,cursor INTEGER NOT NULL DEFAULT 0,
      finished INTEGER NOT NULL DEFAULT 0 CHECK(finished IN (0,1)));
    CREATE TABLE IF NOT EXISTS queue_items (
      queue_id TEXT NOT NULL REFERENCES reading_queues(id),position INTEGER NOT NULL,
      article_url TEXT NOT NULL REFERENCES articles(url),PRIMARY KEY(queue_id,position),UNIQUE(queue_id,article_url));''')
    db.executescript("""CREATE TABLE IF NOT EXISTS article_reads (
      article_url TEXT PRIMARY KEY REFERENCES articles(url),read_at TEXT);
    INSERT OR IGNORE INTO article_reads(article_url,read_at) SELECT url,NULL FROM articles WHERE is_read=1;
    CREATE TRIGGER IF NOT EXISTS record_first_article_read AFTER UPDATE OF is_read ON articles
      WHEN NEW.is_read=1 AND OLD.is_read=0
      BEGIN INSERT OR IGNORE INTO article_reads(article_url,read_at) VALUES(NEW.url,strftime('%Y-%m-%dT%H:%M:%SZ','now')); END;
    """)
    db.executescript("""CREATE TABLE IF NOT EXISTS timed_reading_sessions (
      id TEXT PRIMARY KEY, article_url TEXT NOT NULL REFERENCES articles(url),
      title TEXT NOT NULL, word_count INTEGER NOT NULL, target_seconds INTEGER NOT NULL,
      estimated_seconds INTEGER NOT NULL, elapsed_seconds REAL NOT NULL DEFAULT 0,
      started_at TEXT NOT NULL, resumed_at TEXT, finished_at TEXT,
      status TEXT NOT NULL CHECK(status IN ('running','paused','completed','abandoned'))
    );""")
    if 'triage' not in columns:
        db.execute("ALTER TABLE articles ADD COLUMN triage TEXT CHECK (triage IN ('keep','skip'))")
    db.executescript("""CREATE TABLE IF NOT EXISTS article_highlights (
      id TEXT PRIMARY KEY, article_url TEXT NOT NULL REFERENCES articles(url),
      quote TEXT NOT NULL, prefix TEXT NOT NULL DEFAULT '', suffix TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS article_highlights_article ON article_highlights(article_url);
    CREATE TABLE IF NOT EXISTS reading_positions (
      article_url TEXT PRIMARY KEY REFERENCES articles(url),
      fraction REAL NOT NULL, updated_at TEXT NOT NULL
    );""")
    db.executescript("""CREATE TABLE IF NOT EXISTS article_tags (
      article_url TEXT NOT NULL REFERENCES articles(url) DEFERRABLE INITIALLY DEFERRED,
      tag TEXT NOT NULL COLLATE NOCASE, created_at TEXT NOT NULL,
      PRIMARY KEY(article_url,tag)
    ) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS article_tags_tag ON article_tags(tag);
    CREATE TRIGGER IF NOT EXISTS article_tags_removed AFTER DELETE ON articles
      BEGIN DELETE FROM article_tags WHERE article_url=OLD.url; END;
    """)
    db.executescript("""CREATE TABLE IF NOT EXISTS topics (
      id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
      status TEXT NOT NULL DEFAULT 'tracking' CHECK (status IN ('tracking','closed')),
      notes TEXT NOT NULL DEFAULT '', notes_revision INTEGER NOT NULL DEFAULT 0, notes_updated_at TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS topic_articles (
      topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
      article_url TEXT NOT NULL REFERENCES articles(url) DEFERRABLE INITIALLY DEFERRED,
      added_at TEXT NOT NULL,
      PRIMARY KEY(topic_id,article_url)
    ) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS topic_articles_article ON topic_articles(article_url);
    CREATE TABLE IF NOT EXISTS topic_dismissed (
      topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
      article_url TEXT NOT NULL,
      PRIMARY KEY(topic_id,article_url)
    ) WITHOUT ROWID;
    CREATE TRIGGER IF NOT EXISTS topic_articles_removed AFTER DELETE ON articles
      BEGIN
        DELETE FROM topic_articles WHERE article_url=OLD.url;
        DELETE FROM topic_dismissed WHERE article_url=OLD.url;
      END;
    """)
    db.executescript("""CREATE TABLE IF NOT EXISTS source_fetch_state (
      source_id TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, covered_since TEXT,
      paused_until TEXT, pause_reason TEXT
    );""")
    # Rebuilt from article text on demand. Triggers keep it honest for every path that writes articles.
    db.executescript("""CREATE TABLE IF NOT EXISTS article_indicators (
      article_url TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
      PRIMARY KEY(article_url,kind,value)
    ) WITHOUT ROWID;
    CREATE INDEX IF NOT EXISTS article_indicators_value ON article_indicators(kind,value);
    CREATE TABLE IF NOT EXISTS indicator_index_state (
      article_url TEXT PRIMARY KEY, version INTEGER NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS article_body_changed AFTER UPDATE OF content_html,content_markdown,content_text ON articles
      WHEN NEW.content_html IS NOT OLD.content_html OR NEW.content_markdown IS NOT OLD.content_markdown OR NEW.content_text IS NOT OLD.content_text
      BEGIN
        DELETE FROM article_indicators WHERE article_url=NEW.url;
        DELETE FROM indicator_index_state WHERE article_url=NEW.url;
      END;
    CREATE TRIGGER IF NOT EXISTS article_removed AFTER DELETE ON articles
      BEGIN
        DELETE FROM article_indicators WHERE article_url=OLD.url;
        DELETE FROM indicator_index_state WHERE article_url=OLD.url;
      END;
    """)
    db.commit()
    return db


def save_source(db, source):
    db.execute('''INSERT INTO sources VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
      name=excluded.name,source_type=excluded.source_type,parent_url=excluded.parent_url,enabled=excluded.enabled''',
      (source.id, source.name, source.source_type, source.parent_url, source.enabled))


def save_article(db, source_id, article, *, content, error, reason, discovered_at, markdown=None, media=None, html=None):
    from .boilerplate import title_without_category

    existing = db.execute("SELECT content_html FROM articles WHERE url=?", (article.url,)).fetchone()
    title = title_without_category(article.title, html or (existing[0] if existing else None))
    db.execute('''INSERT INTO articles (url,source_id,title,published_at,author,summary_html,content_text,content_status,fetch_error,relevance_reason,discovered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(url) DO UPDATE SET title=excluded.title,published_at=excluded.published_at,
      author=excluded.author,summary_html=excluded.summary_html,
      content_text=COALESCE(excluded.content_text, articles.content_text),
      content_status=CASE WHEN excluded.content_text IS NOT NULL THEN 'full_text' ELSE articles.content_status END,
      fetch_error=excluded.fetch_error,relevance_reason=excluded.relevance_reason''',
      (article.url, source_id, title, article.published_at.isoformat(), article.author,
       article.summary_html, content, 'full_text' if content else 'metadata_only', error, reason, discovered_at))
    if markdown:
        import json
        db.execute('UPDATE articles SET content_markdown=?,content_html=?,media_json=? WHERE url=?', (markdown,html,json.dumps(media or []),article.url))
    return not bool(existing)
