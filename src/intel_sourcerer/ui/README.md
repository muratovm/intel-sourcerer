# Editing the interface

UI files are grouped by page. Browser URLs are unchanged; viewer.py serves these files and renders pages from backend JSON.

- workspace/home.html: Home layout.
- workspace/queue-builder.html: discovery filters and queue selection.
- workspace/reading.html: article and notes panes.
- workspace/index.html: document shell and completion popup. Includes the three layouts above.
- workspace/workspace.js: shared state and interactions for Home, Queue Builder, and Reading. Search for renderHome, renderSetup, renderQueue, or saveNow.
- library/index.html and library/library.js: Library layout, selection, imports, and deletion. Article rows and notes preview are separate HTML components beside them.
- sources/index.html and sources/sources.js: source configuration display and toggles.
- stats/index.html: reading heatmap, with day and month components.
- shared/styles.css: formatted stylesheet shared by all pages. Search by class name or section comment.

Python modules prepare data and pass escaped values into ${placeholder} slots. templates.py loads those files using Python's string.Template; it does not run JavaScript or evaluate template expressions. HTML fragments must be produced by our own components or the existing notes sanitizer. Escape any new user text before inserting it. Workspace files use trusted include comments assembled by the server.

Format HTML, JavaScript, and CSS with Prettier and Python with Ruff. Run uv run pytest after changing rendering or behavior. No build step is required.

- workspace/focus.js: timed reading controls and pause/resume synchronization. Timed session storage and statistics are handled by timed_reading.py.

The viewer opens no database or source configuration. Database wrappers in library.py/stats.py/timed_reading.py prepare JSON on the backend; their data-based renderers produce the same layouts on the viewer. source_view.py renders serialized sources. See docs/hosting.md for the service boundary.
