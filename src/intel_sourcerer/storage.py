"""Local SQLite storage for imported source articles."""
import sqlite3
from pathlib import Path


def connect_database(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
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
    if 'content_markdown' not in columns:
        db.execute('ALTER TABLE articles ADD COLUMN content_markdown TEXT')
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
    db.commit()
    return db


def save_source(db, source):
    db.execute('''INSERT INTO sources VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
      name=excluded.name,source_type=excluded.source_type,parent_url=excluded.parent_url,enabled=excluded.enabled''',
      (source.id, source.name, source.source_type, source.parent_url, source.enabled))


def save_article(db, source_id, article, *, content, error, reason, discovered_at, markdown=None, media=None):
    existing = db.execute("SELECT 1 FROM articles WHERE url=?", (article.url,)).fetchone()
    db.execute('''INSERT INTO articles (url,source_id,title,published_at,author,summary_html,content_text,content_status,fetch_error,relevance_reason,discovered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(url) DO UPDATE SET title=excluded.title,published_at=excluded.published_at,
      author=excluded.author,summary_html=excluded.summary_html,
      content_text=COALESCE(excluded.content_text, articles.content_text),
      content_status=CASE WHEN excluded.content_text IS NOT NULL THEN 'full_text' ELSE articles.content_status END,
      fetch_error=excluded.fetch_error,relevance_reason=excluded.relevance_reason''',
      (article.url, source_id, article.title, article.published_at.isoformat(), article.author,
       article.summary_html, content, 'full_text' if content else 'metadata_only', error, reason, discovered_at))
    if markdown:
        import json
        db.execute('UPDATE articles SET content_markdown=?,media_json=? WHERE url=?', (markdown,json.dumps(media or []),article.url))
    return not bool(existing)
