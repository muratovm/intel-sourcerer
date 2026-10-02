# Intel Sourcerer

A local cybersecurity reading workspace for collecting research articles, building focused reading queues, and keeping notes beside the full article.

## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run intel-sourcerer-web
```

Open [Intel Sourcerer](http://127.0.0.1:8765/). Run commands from the project root. The server binds to localhost; press Ctrl+C to stop it.

```sh
# Optional custom database and port
uv run intel-sourcerer-web --database data/intel_sourcerer.sqlite3 --port 8765

# Run tests
uv run pytest -q
```

## Reading workflow

- **Home:** resume the current session, build a new queue, and revisit saved note previews.
- **Queue builder:** filter articles by searchable source, article type, and timeframe. Hide read articles or show noted articles using independent checkboxes. Find latest articles checks all enabled configured sources.
- **Your queue:** add/remove articles with the Add/Added button, drag to reorder, or use the ordering buttons. Drop article URLs or paste a link to import a public HTML page manually.
- **Reading view:** read the saved article alongside autosaving notes. Resize the divider, quote selected passages into callouts, and apply bold or highlights. Next advances without changing read status; Mark read & next records the read and advances. Return to the builder with the current selection intact, or go Home.
- **Completion:** finishing the queue opens a centered popup. Starting another queue does not require finishing the previous one.
- **Sources:** view configured publishers and toggle them on/off. Changes persist to the TOML configuration and database. Disabled sources stop appearing in discovery results; stored articles and notes are retained.
- **Stats:** this year's daily reading heatmap in America/Toronto time. Each article counts once on its first recorded read. Previously read articles without timestamps are excluded. Changing read status later does not erase activity.

Queues and reading position persist in SQLite. Browser storage retains queue drafts, note drafts, article scroll positions, divider width, and media preferences. Notes are saved before leaving the reader; conflicting note edits are rejected to avoid overwriting newer changes.

## Sources and collection

Publisher configuration lives in [`config/sources.toml`](config/sources.toml). Each source has an ID, name, `source_type`, `enabled`, and `parent_url`; RSS URLs and notes are optional. Source types group publishers and do not restrict collection.

Collection supports RSS/Atom feeds and an Anthropic listing adapter. Enabled sources without a supported discovery method report an issue. Listing pagination is not implemented, so longer windows and All time do not guarantee a complete historical archive.

```sh
# Preview enabled RSS feeds as JSON, without saving articles
uv run intel-sourcerer

# Import relevant articles from enabled sources
uv run intel-sourcerer-import --since 2026-09-01
```

Cybersecurity relevance and article-type labels use keyword rules, not AI classification. They can miss articles or assign broad labels. Manual links bypass relevance filtering. If a manual page has no publication date, its import date is used. Adding an already saved manual URL preserves its existing copy.

## Saved articles and media

Articles are stored as text and Markdown with headings, lists, tables, links, and media placeholders. Blog header images can be captured from Open Graph/Twitter metadata. Multi-section reports use the enclosing main content when it contains several separate article sections; unusual layouts can still include unrelated material.

**Load article media** optionally loads linked images, supported direct videos, and YouTube/Vimeo embeds from external hosts. Media is not downloaded into the database. JavaScript-rendered charts, dynamic content, and unsupported video players may not be captured. Original article and media links remain available.

The `/classic` route retains the earlier expandable article list.

## Storage and implementation

The default database is `data/intel_sourcerer.sqlite3`. Database files, SQLite sidecars, virtual environments, caches, and `.env` are ignored by Git. Back up the database separately; it contains your articles, notes, queues, and read history. Existing databases migrate automatically when opened.

The current app uses Python's threaded HTTP server, SQLite, and plain HTML/CSS/JavaScript—no frontend build step. Dependencies include httpx, feedparser, Beautiful Soup, Trafilatura, markdownify, markdown-it-py, and bleach. Reader and note HTML are sanitized; write endpoints require a CSRF token.

Key modules in `src/intel_sourcerer/`:

| Module | Purpose |
| --- | --- |
| `config.py`, `feeds.py` | Source configuration and RSS/Atom fetching |
| `importer.py`, `discovery.py` | Configured-source collection and manual links |
| `content.py` | Markdown extraction, media placeholders, rendering |
| `storage.py`, `sessions.py` | Articles, queues, notes, and read history |
| `web.py`, `reader.*`, `sources.js`, `stats.py` | Local interface and endpoints |

## Planned work

AI summaries, article chat, scheduled collection, notifications, and richer extraction are future work. There is no automatic background polling or AI provider integration yet.

See [`docs/`](docs/) for source notes, design screenshots, and earlier architecture proposals. Those proposals describe possible future infrastructure rather than the current runtime.
