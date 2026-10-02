# Reading queue design QA

final result: passed

Reference: /Users/michael/.codex/generated_images/01a0f9a0-dd0e-7071-8e6f-9d016fd69e1e/exec-84d44cc1-7de2-4a42-a66f-52ad5c8e1e78.png
Implementation screenshot: docs/ui/reader.png
Full-view comparison: docs/ui/comparison.png (reference left, implementation right).
Viewport: 1487 × 1058 CSS pixels. Both source and implementation are 1487 × 1058 pixels; 1× density, no normalization. Comparison displayed smaller by the review tool; individual images also inspected at original size.
State: same saved article open at the beginning, with saved notes. Reference uses illustrative queue counts, excerpt copy, and passage cards; implementation uses the actual stored Markdown, real queue state, and an editable notes field with appended Markdown quotations. The source's fabricated article bullets are not substituted for publisher content.

## Findings

No unresolved P0/P1/P2 findings in the approved flow. Equal-width article/notes panes, white/navy palette, subtle dividers, title hierarchy, editable notes, and persistent top/bottom navigation follow the selected direction. System sans-serif fallback is used; display weight and title wrapping differ slightly from the generated reference. Body text is 17px with 1.65 line height and notes 17px with 1.75 line height. Real article paragraph lengths change above-fold content. Controls have visible focus outlines and readable contrast. No decorative raster assets are present in this design. Saved article media remains represented by existing placeholders.

## Comparison history

- Initial desktop capture: /private/tmp/intel-focus/reader-desktop.png, 1280 × 720; recaptured at source dimensions for comparison.
- P2: up-next cues were hidden below 1100px. Fixed responsive rules to retain a compact cue at both ends. Verified at 390 × 844 CSS pixels; docs/ui/mobile.png shows notes, read toggle, and bottom navigation without horizontal overflow.
- Final desktop capture and combined comparison inspected. Focused crops were unnecessary because full-size individual screenshots clearly showed typography, pane boundaries, note field, and both navigation bars.

## Interaction verification

Browser-tested against an isolated copy of the database: select two articles, start queue, write notes, Next without marking read, Previous with notes restored, refresh with queue/notes restored, Mark read & next, Mark read & finish, completion totals, create a new queue, Mark unread, select article passage and append quotation, save quotation by navigation, mobile bottom navigation. Browser warning/error log was empty.

Automated validation: 37 tests pass, including persistent queue order/cursor, optimistic note conflicts, stale navigation rejection, explicit read status, queue completion/recreation, and HTTP API authentication/save/resume behavior.

## Follow-up polish

Optional P3: render quoted passages as separate editable cards and use an exact licensed font if desired. Notes currently remain a simple text editor with Markdown-style quotes. This initial implementation has no AI actions yet.

## Media toggle update

Added a compact checkbox beneath the original article link. Evidence: docs/ui/media-toggle.png at 1280 × 720. Browser checks confirmed off state contains no media elements, enabling creates inline image/video elements from saved placeholders, direct videos have autoplay disabled, preference survives refresh, and disabling removes media. Original links stay present. External image pixels were not observed during this check (lazy images were outside the viewed portion); publisher-hosted playback and YouTube/Vimeo availability depend on the host. HTTP tests verify placeholder metadata and media policy; all 37 tests pass. No layout overflow observed in the toggle row.

## Resizable panes update

Evidence: docs/ui/resizable-panes.png, 1280 × 720. Browser verification: Right arrow adjusts 50 to 52%; dragging adjusts to 63%; refresh restores 63%; Enter resets to 50%. Divider has an accessible name, vertical separator role, value, and keyboard controls. Both panes maintain readable layout at the verified split; 30–70% bounds protect minimum widths. Existing 37 tests pass. Narrow stacked layout hides the horizontal resizing control.

## Rich notes update

Evidence: docs/ui/rich-notes.png at 1280 × 720. The compact Bold/Highlight/Callout toolbar fits above the editor with the requested tight padding. Selected article passages create editable blue callouts. Browser checks covered applying bold, creating a callout, highlighting selected notes, saving on navigation, and reopening the article with highlight/callout retained. Fixed a browser-generated inline-style highlight persistence issue by inserting explicit mark elements. Quote insertion collapses the note selection before insertion to preserve existing text. Existing plain notes are escaped on load, HTML is sanitized in the browser and server, and revision conflict handling remains. 38 tests pass, including rich-format sanitization. Final result remains passed.

## Formatting toggle correction

Browser-tested partial selection: only “Al” in “Alpha beta gamma” became bold; active Bold button has aria-pressed=true and blue background. Highlight applied and then removed on the same selection without removing bold. Typing “x” after that selection produced plain text outside the bold span. Navigation flushed saved notes. Evidence: docs/ui/notes-active-bold.png. All 38 existing tests pass.

## Queue builder implementation

final result: passed

Source: /Users/michael/.codex/generated_images/01a0f9a0-dd0e-7071-8e6f-9d016fd69e1e/exec-41b6830d-f33d-49be-a6c3-5c4136da250a.png
Implementation: docs/ui/queue-builder.png
Combined evidence: docs/ui/queue-comparison.png
Source and implementation: 1487 × 1058 pixels, viewport 1487 × 1058 CSS pixels, 1× density with no normalization. State: queue builder with three articles selected. Real saved headlines replace illustrative mock headlines; only currently enabled AI-lab publishers are discovered.

Comparison: inspected source, rendered screen, and combined full view. Both retain white/navy palette, blue discovery action, two-column 61/39 layout, source rows, ordered queue, URL drop/paste area, and prominent start action. Initial heading size and Added state drift corrected to 50px desktop heading and green selected state. Current type size uses system sans; some wrapping differs with actual headlines. Move up/down and Remove text controls provide accessible alternatives to drag; ornamental icons omitted. Focused crops unnecessary because full-size individual images clearly expose typography and spacing.

Verification: live publisher refresh completed successfully in isolated database. Added three results; reordered with native drag and Move up; pasted an existing URL without duplication; refreshed with draft order retained; topic filters work; Start reading opened the first article in the chosen order. Narrow-screen document width equals viewport width (375px); no horizontal overflow. New-link fetching/extraction, duplicate note preservation, private URL rejection and topic rules tested with mocked network. All 41 tests pass.

P3 follow-up: ornamental drag/link icons and exact typeface matching. No unresolved P0/P1/P2 findings. Publisher extraction remains subject to existing importer limitations; topic classification is rule-based.

## Selected backdrop, option 3
Compared the selected Queue Glow mock with the rendered queue builder. Scope: backdrop only. Article list and header are white; blue gradient is confined to the right queue pane with strongest color at the lower outer edge. Existing layout and controls retained. Screenshot: /private/tmp/intel-selected-backdrop.png.
final result: passed

## Home page implementation
Selected revised mock: current session at left, new queue at right, saved notes below; white and pale blue backdrop. Browser checked home and builder navigation. Empty notes and finished queue render without sample data. All 43 tests pass. Existing typography retained; publisher logos omitted in favor of configured publisher names.
final result: passed

## Queue filter arrangement, selected option 2
Browser capture confirms Source and Article type share first row; Timeframe, Status and Find latest articles align on second row. Existing article list and queue unchanged. Mobile CSS keeps paired fields and full-width action. Screenshot: /private/tmp/intel-filters-two-rows.png.
final result: passed
