# Intel Sourcerer

A local cybersecurity reading workspace for collecting research articles, building focused reading queues, and keeping notes beside the full article.

<img width="1261" height="1176" alt="image" src="https://github.com/user-attachments/assets/0df31668-9b5e-4684-b4e5-580e9c071525" />


## Run locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run intel-sourcerer-web
```

Open [Intel Sourcerer](http://127.0.0.1:8765/). Run commands from the project root. One server on port 8765 serves both authenticated accounts and temporary guest reading; press Ctrl+C to stop it.

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
- **Library:** search titles, publishers, full saved article text, and notes, including disabled sources. Select a result to preview its formatted notes beside the article list; use With notes to narrow the list. Open an article without replacing your reading queue, or export its article and notes as Markdown. Articles sort by publication date, newest first.
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

The default database is `data/intel_sourcerer.sqlite3`. Database files, SQLite sidecars, virtual environments, caches, and `.env` are ignored by Git. It contains your articles, notes, queues, and read history. Existing databases migrate automatically when opened.

The current app uses Python's threaded HTTP server, SQLite, and plain HTML/CSS/JavaScript—no frontend build step. Dependencies include httpx, feedparser, Beautiful Soup, Trafilatura, markdownify, markdown-it-py, and bleach. Reader and note HTML are sanitized; write endpoints require a CSRF token.

Key modules in `src/intel_sourcerer/`:

| Module | Purpose |
| --- | --- |
| `config.py`, `feeds.py` | Source configuration and RSS/Atom fetching |
| `importer.py`, `discovery.py` | Configured-source collection and manual links |
| `content.py` | Markdown extraction, media placeholders, rendering |
| `storage.py`, `sessions.py` | Articles, queues, notes, and read history |
| `viewer.py`, `ui/`, page renderers | Stateless interface and backend gateway |
| `backend.py`, `web.py`, `auth.py`, storage modules | Private authentication and article API |

## Planned work

AI summaries, article chat, scheduled collection, notifications, and richer extraction are future work. There is no automatic background polling or AI provider integration yet.

See [`docs/`](docs/) for source notes, design screenshots, and earlier architecture proposals. Those proposals describe possible future infrastructure rather than the current runtime.

## Automatic backups and recovery

The web server creates one SQLite snapshot per UTC day in `data/backups/`, on startup or before the first note save that day. Existing daily snapshots are kept unchanged. These are daily recovery points, not continuous backups; later edits may not be in that day's snapshot. Snapshots stay local, are ignored by Git, and are not automatically pruned. Copy important snapshots to separate storage for protection against disk loss.

To restore a snapshot:

1. Stop the web server and any import processes.
2. Move the current database and any matching `-wal` and `-shm` sidecars to a separate recovery folder. Keep them until you have verified the restore.
3. Copy the desired file from `data/backups/` to `data/intel_sourcerer.sqlite3` (or your configured database path).
4. Restart the server and check your articles and notes. Restoring also restores queues and read history to the snapshot date.

Browser-local unsaved note drafts may conflict with restored notes. Preserve any draft you want to keep before dismissing a conflict.

## Editing the UI

Page layouts and their JavaScript live together under `src/intel_sourcerer/ui/`. See [the UI guide](src/intel_sourcerer/ui/README.md) for the file map and rendering conventions. The shared stylesheet is `ui/shared/styles.css`.

## Timed reading and notes

In the reader, choose a target (defaults to the article reading estimate) and start the timer. Pause/resume excludes paused time; switching tabs or leaving through reader navigation pauses automatically. Reloads retain the session. Overtime keeps counting. Finish reading & notes saves notes first, records the attempt, and marks the article read without advancing the queue. End attempt records an abandoned attempt separately.

Stats shows completion rates against your target and the default 200-word/minute estimate, weighted reading-and-annotation pace, weekly summaries, and recent sessions. Pace measures article words per active minute, including time spent taking notes; it does not measure comprehension. Abandoned attempts are excluded. Only one timer can be active at a time. Deleting an article deletes its timed sessions too.

### More media in your queue

The Library and Queue Builder accept links, local paths, and file drops:

- Audio: MP3, M4A, OGG, WAV URLs or local files. Podcast RSS links import the first available audio episode. The reader offers playback speed and **Add timestamp to notes**.
- Newsletters: public newsletter pages use the article importer; exported `.eml` messages preserve their subject, sender, and readable body. Remote email images still use the media toggle. Inbox connection and paywall access are not included.
- Slides: `.pptx` files or direct links import slide text, figures, tables, and speaker notes in slide order. PDF slides use the PDF parser. Full slide layout/animations are not reproduced.
- GitHub: public repository links import the README; release links import release notes. Release imports follow GitHub's public API rate limits. Repositories are not executed or cloned.
- Local video: MP4 and WebM with playback speed and timestamp notes. Audio and video uploads are capped at 100 MB; PDFs, newsletters, and slides at 20 MB.
- Social posts: public readable posts use article extraction; access-restricted supported platforms are saved as clearly labelled bookmarks. Logged-in content and complete thread capture are not included.

Uploads stay under `data/media/` and are ignored by Git. Content format is stored separately from topic/source type and shown in the Library and Queue Builder. Audio/video do not contribute to word-based reading-speed sessions. YouTube retains its embedded player; timestamp notes currently use native audio/local-video players.

## Accounts and private libraries

Open the site and create the first account. It becomes the administrator and retains
all existing articles, notes, queues, and reading history. Use **Account** in the
navigation to create additional users, change your password, or sign out. Each
additional user starts with an empty library and a personal copy of the configured
sources. Passwords require at least 12 characters; there are no default credentials.

Accounts are stored in `data/accounts.sqlite3`; additional libraries and private
source settings live under `data/users/<id>/`. These files are ignored by Git.
Back up the entire data directory to preserve accounts and personal content.
Sessions expire after eight hours; changing a password revokes existing sessions.
Regular users upload local files; importing arbitrary server filesystem paths is
restricted to the administrator. Password recovery by email is not implemented.

The server still binds to localhost. For access from other computers, deploy behind
an HTTPS reverse proxy and start with `--secure-cookies` so session cookies are sent
only over HTTPS. Do not enable this flag for the ordinary local HTTP address.

## Separate viewer and backend hosting

The viewer can run on a different machine from authentication and saved data.
Use `intel-sourcerer-viewer --backend-url ...` and `intel-sourcerer-backend` for
independent deployments. Configure the same secret `INTEL_BACKEND_KEY` on both.
The viewer renders the interface and forwards requests; the backend retains all
accounts, sessions, private libraries, configuration, and uploaded files.

See [hosting instructions and architecture](docs/hosting.md) for commands,
environment variables, HTTPS setup, and the API boundary. `intel-sourcerer-web`
runs both modes on a single server for local development.

## Temporary reader without accounts or a backend

Run `uv run intel-sourcerer-viewer --article ./report.md --port 8767` to open a frontend-only reading session. Repeat `--article` for a queue, or use `--articles articles.json` for prepared content. Notes and progress stay in the browser tab; use **Export articles & notes** to keep them. See [temporary-session setup](docs/hosting.md#frontend-only-temporary-sessions).
