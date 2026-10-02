# Intel Sourcerer

*Cybersecurity intelligence, from source to insight.*

A personal cybersecurity reading workbench that monitors company blogs, identifies relevant new articles, and lets you read, question, annotate, and extract evidence in one interface.

## Status

Early implementation. RSS/Atom fetching and normalization are implemented with a command-line preview and tests. A local SQLite database, article extraction, and AI-lab cybersecurity importer are now available. The interface, scheduler, PostgreSQL deployment, and AI features remain planned.

## Product experience

The default view answers: **What has appeared since I last checked?**

A three-pane desktop interface combines:

- **Article inbox:** unread articles, publisher and topic filters, publication dates, reading time, and a short relevance explanation.
- **Article reader:** clean text, original-source link, highlights, notes, and explicit read/save controls.
- **AI workspace:** article actions, streamed conversation, evidence citations, and saved results.

On mobile, the panes become separate views. Navigation includes Catch up, Inbox, Saved, and Sources. Sources shows collection failures and the last successful check.

## Proposed stack

| Layer | Technology | Responsibility |
| --- | --- | --- |
| Backend | Python + FastAPI | Article API, AI actions, search, reading state, export |
| Frontend | React + TypeScript + Vite + Tailwind | Inbox, reader, highlights, notes, AI workspace |
| Storage | PostgreSQL | Articles, content versions, full-text search, notes, conversations, cached results, durable jobs |
| Collection | feedparser + Trafilatura | Feed parsing and article text/metadata extraction |
| Background work | Separate Python worker | Scheduled checks, enrichment, retries, long-running AI actions |
| Development | uv + Bun | Python and frontend dependencies/tooling |
| Local infrastructure | Docker Compose for PostgreSQL | Local database lifecycle |

Vite is the initial frontend recommendation. Next.js remains an alternative if a public website or existing framework conventions make it useful. Embeddings can be added later if full-text search proves insufficient.

## Initial publishers

- **Google Threat Intelligence:** use the official RSS feed linked from https://cloud.google.com/blog/topics/threat-intelligence.
- **OpenAI:** test and validate https://openai.com/news/rss.xml during implementation.
- **Anthropic:** monitor https://www.anthropic.com/news and relevant research listings; an official RSS feed has not been verified.

Use a small adapter per publisher, preferring official feeds. Listing adapters discover links when feeds are unavailable. Adding other company blogs should require source configuration and an adapter only when needed.

## Collection and relevance

1. Check sources every 30 minutes while the host and worker are running.
2. Discover URLs, normalize canonical links, and remove duplicates.
3. Extract article text, author, publisher, and publication date.
4. Classify cybersecurity relevance using the article body and assign topic tags.
5. Generate a short summary and relevance explanation, then place matches in the inbox.

Initial topics: threat intelligence, vulnerabilities, adversarial AI, prompt injection, agent security, identity, and supply-chain attacks. Keep uncertain matches accessible. Track discovery time separately from publication time; article updates should be distinct from new releases.

Persist job status and retry information. Record source health, extraction failures, content hashes, and content versions. Prevent overlapping collection runs.

## AI actions

The first release includes **Summarize** and **Ask this article**. Follow-up actions:

- Explain a highlighted passage.
- Extract TTPs, indicators, vulnerabilities, and supporting passages.
- Create an editable Markdown research note with source links.
- Compare selected articles.
- Suggest detection ideas, clearly labeled as suggestions.
- Export notes to Obsidian or feed evidence into a TTP Evidence Workbench.

Answers should cite stored source passages and distinguish reported facts from model interpretations. Say when the article does not support an answer. Treat downloaded content as untrusted input, sanitize reader content, and keep provider credentials on the backend.

Automatically run only relevance classification and short summaries. Deeper analysis runs on demand. Cache outputs by article version, action, prompt version, and model.

## First-release scope

- [ ] Three publisher adapters with validated collection.
- [ ] Scheduled checks, deduplication, source-health visibility, and retries.
- [ ] Cybersecurity inbox with filters and explicit read/save controls.
- [ ] Clean article reader.
- [ ] Summaries and article chat with source citations.
- [ ] Saved notes and Markdown export.
- [ ] In-app indicator for new relevant articles.

The first milestone: open the app, see what changed, read an article, ask a question, and save an evidence-backed note without switching tools.

Later: daily digests, opt-in immediate topic alerts, cross-article comparisons, and richer evidence extraction.

## Deployment direction

Start as a browser application running on the Mac, with a Python API, separate worker, and PostgreSQL. Bind local services to loopback. Local collection requires the Mac and worker to be awake. Move the same components to an always-on server when reliable monitoring is needed.

## Architecture

See [architecture notes and Mermaid source](docs/architecture.md).

![Proposed local architecture](docs/architecture.svg)

## Decisions still open

- AI provider and model selection, based on quality, latency, and cost.
- Additional publishers and personal relevance rules.
- Notification channel and digest schedule.
- Whether Obsidian integration stays a manual export or becomes a direct integration.

## Reference documentation

- [FastAPI](https://fastapi.tiangolo.com/)
- [Vite](https://vite.dev/guide/)
- [feedparser](https://feedparser.readthedocs.io/en/latest/)
- [Trafilatura](https://trafilatura.readthedocs.io/en/latest/)
- [PostgreSQL full-text search](https://www.postgresql.org/docs/current/textsearch.html)

## Run the RSS collector

```sh
uv sync
uv run intel-sourcerer
# Or supply RSS/Atom URLs:
uv run intel-sourcerer https://example.com/feed.xml
uv run pytest
```

The command prints JSON and exits with status 1 if any source fails. The default config contains 20 curated research sources: 17 have verified RSS feeds; Anthropic, Elastic Security Labs, and Trend Micro require listing adapters and are explicitly skipped by the RSS CLI. This command does not store articles or schedule future checks.

### Feed functions

`src/intel_sourcerer/feeds.py` provides:

- `parse_feed(data, source)`: normalize RSS/Atom bytes without network access.
- `fetch_feed(client, source, cache)`: fetch one feed with a 20-second request timeout, redirects, a 5 MB decoded-body limit, conditional HTTP validators, and per-source errors.
- `fetch_feeds(sources, caches, concurrency=4)`: fetch multiple sources with bounded concurrency and a shared connection pool.

`FeedResult` contains articles, cache validators, check time, warnings, unchanged status, and an optional error. Persist new validators only after successfully storing articles. A 304 response preserves existing articles; errors preserve prior validators. Retry scheduling and cross-poll deduplication belong to the future persistence/worker layer. Feed summaries are untrusted HTML and must be sanitized before display.

Python is appropriate for this network-bound workload. Requests run concurrently; feed parsing is moved off the event loop. Profile real collection runs before considering a language change.

## Publisher configuration

Edit [`config/sources.toml`](config/sources.toml) to change the publishers and parent article index pages. `parent_url` identifies the discovery page, not a common URL prefix: linked reports can live elsewhere, including PDF hosts. `rss_url` is optional, and may cover a broader subject than the parent page. Current RSS collection does not yet filter feeds to parent-page membership or cybersecurity relevance.

Each source has a stable `id`, display `name`, required `source_type`, `parent_url`, optional `rss_url`, and explanatory `notes`. Set `enabled = false` to disable a source. The old TAG index redirects to the broader Google Security page; both URLs are recorded. Dedicated OpenAI and Anthropic threat-intelligence hubs are configured. Google Threat Intelligence is retained as a separate publication.

Run from the project root, or provide an explicit configuration path:

```sh
uv run intel-sourcerer --config config/sources.toml
```

Explicit positional feed URLs override the config. Invalid configs fail before network requests. Enabled sources without RSS URLs are reported on stderr and skipped; listing collection remains to be implemented.

## Curated research coverage

The config now includes 20 first-party research publications spanning AI misuse, nation-state campaigns, malware, active exploitation, cloud attacks, and incident response. See [the source catalogue](docs/sources.md) for publisher links, selection rationale, verification notes, and RSS limitations.

## Source grouping

`source_type` is the sole grouping label. The config keeps entries together by type:

- `ai_lab`: OpenAI and Anthropic (2 sources).
- `security_vendor`: security research teams at Google, Microsoft, and the other security vendors (18 sources).

This groups publishers rather than article topics or collection methods. Source types must be nonempty strings; additional groups can be added through configuration later. RSS availability remains represented by `rss_url`.

## Import AI-lab cybersecurity articles

Only `ai_lab` sources are currently enabled. Other publisher entries remain in the config with `enabled = false`.

```sh
uv run intel-sourcerer-import --since 2026-09-01
```

The importer stores articles at `data/intel_sourcerer.sqlite3`, using SQLite for this local iteration because no PostgreSQL server/container runtime is running. PostgreSQL remains the planned deployment database; the current implementation uses SQLite directly and will need a storage migration.

The date window starts at midnight America/Toronto on the supplied date and ends at import time. It uses publisher dates, not discovery dates. OpenAI discovery uses its full RSS feed; Anthropic discovery checks its threat-intelligence page and visible dated newsroom cards. Listing pagination is not implemented, so this is not a guarantee of exhaustive publisher history.

Selection uses explicit cybersecurity rules on titles and summaries, plus exclusions for general launch/event posts. This is initial rule-based relevance filtering, not an AI classifier. Each article stores its inclusion reason. Review rules over time to address false positives and missed relevant posts.

The database stores source identity, title, URL, publication date, summary, extracted text, fetch status/error, relevance reason, and discovery time. Imports update existing URLs without creating duplicate articles. Failed re-fetches preserve previously extracted text. Import runs record discovery and selection counts and source failures. Nothing runs on a schedule yet.

See [the initial import report](docs/initial-import.md) for the saved articles.

## Local article reader

From the project root:

```sh
uv run intel-sourcerer-web
```

Open http://127.0.0.1:8765. Select articles (unread articles are preselected), then start reading. The desktop reader divides the screen equally between the saved article and your notes, with queue progress and navigation at the top and bottom. On narrow screens, the panes stack.

Notes autosave to SQLite. Select article text and use **Add selected passage to notes** to append a quotation. **Next** moves on without marking read; **Mark read & next** saves the read status and advances. **Mark unread** reverses that status. Finishing shows your queue totals and lets you create another queue.

The active queue and cursor persist in SQLite across refreshes and server restarts; article scroll positions and unsaved note drafts are kept locally in the browser. Notes are flushed before navigation. Conflicting edits from another window are rejected rather than overwriting newer notes. Each article stores `notes` and `notes_revision`; queue order and position live in `reading_queues` and `queue_items`. Imports preserve notes and read state.

This interface uses plain HTML, CSS, and JavaScript served by the existing Python server, with no frontend build step. The earlier expandable list remains at `/classic`.

## Markdown and media placeholders

Articles now store `content_markdown` alongside the existing plain text and structured media metadata in `media_json`. Imports preserve headings, lists, tables, and links, and replace article images, videos, and embedded media with inline placeholders containing descriptions and original media links where available. Recommendation sections and navigation are excluded. Media is not downloaded or embedded. Extraction reflects server-delivered HTML; dynamically inserted media and complex layouts may not be captured.

The reader renders Markdown as sanitized HTML, with scrollable tables. Existing content survives the database migration and failed re-fetches. Refresh existing articles by rerunning the importer.

## Article read status

`articles.is_read` stores read state: `0` for unread (the default), `1` for read. Existing articles start unread. Repeat imports preserve this field. Opening an article does not automatically mark it read. Use Mark read & next or Mark unread in the queue reader to save its status.

## Optional inline media

The queue reader includes a **Load article media** checkbox, off by default. Your choice is remembered in this browser. When enabled, saved media placeholders load publisher-hosted images, direct MP4/WebM/Ogg videos, and supported YouTube/Vimeo embeds. Images and embedded players load lazily; direct videos do not preload or autoplay. Switching off removes players/images and restores placeholders. Original media links remain available. Loading media contacts external hosts; some hosts may block embedding. Unsupported video players remain links.

The desktop article/notes divider is draggable, with article width limited to 30–70% so both panes remain usable. Its position is remembered in the browser. Focus the divider and use Left/Right to adjust, Home/End for limits, or Enter (or double-click) to reset to equal widths. Narrow screens retain the stacked layout.

## Rich-text notes

The compact note toolbar supports Bold, Highlight, and Callout. Select note text to format it; Bold also supports the browser's Cmd/Ctrl+B shortcut. Callouts use a tinted block and accent border. Add selected passage to notes inserts an editable quote callout at the last note cursor, followed by space for commentary. Notes autosave as sanitized HTML, identified by `notes_format`; existing plain-text notes remain readable and convert on editing. Pasted content is inserted as plain text. Imports preserve note formatting and revisions.

## Building a queue

The queue builder shows article discovery on the left and your ordered queue on the right. Choose a topic and a 7/30/90-day window, then Find latest articles to refresh enabled AI-lab publishers. Topic selection filters saved results using keywords, with all selected cybersecurity posts available under Threat research. Only OpenAI and Anthropic are currently fetched; other configured sources remain disabled. Source failures are surfaced while existing results remain available.

Add individual results, drag rows to reorder, use Move up/Move down for keyboard-friendly ordering, or remove items. Drop one or more HTTP(S) URLs into the drop area, or paste a URL and choose Add link. New public HTML links are fetched, extracted, and saved under a manual source; existing links preserve notes/read state. Pages with blocked access or no extractable content report an error. If a publisher date is absent, the added time is used. Up to 20 dropped links are processed per batch and up to 100 articles fit a queue. Draft selections and order persist in the browser. Start reading follows your chosen order.
