import {
  draftKey,
  isTemporarySession,
  positionKey,
  readItem,
  readJSON,
  removeItem,
  storagePrefix,
  writeItem,
  writeJSON,
} from "/workspace/storage.js";
import { renderMedia as renderArticleMedia, seekTo, transcriptSeconds } from "/workspace/media.js";
import {
  clearHighlightMarks,
  locateHighlight,
  markHighlight,
  passageAnchor,
} from "/workspace/highlights.js";
import { renderIOCs as renderArticleIOCs, renderRelated as renderArticleRelated } from "/workspace/iocs.js";
import { initPaneDivider, initTextScale } from "/workspace/panes.js";
import { initShortcuts } from "/workspace/shortcuts.js";

const $ = (id) => document.getElementById(id);
let standaloneURL = null;
let state,
  currentArticle,
  noteModel,
  timer,
  saving = Promise.resolve(),
  busy = false,
  selectedPassage = "",
  selectedPassageHTML = "";
const token = document.querySelector('meta[name="csrf-token"]').content;

function notice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}
async function api(path, data) {
  const response = await fetch(
    path,
    data === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": token,
          },
          body: JSON.stringify(data),
        },
  );
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || "Unable to save. Please try again.");
  return result;
}
async function runBackgroundJob(path, data) {
  const started = await api(path, data);
  if (!started?.id) return started;
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const job = await api("/api/jobs?" + new URLSearchParams({ id: started.id }));
    if (job.state === "done") return job.result;
    if (job.state === "failed") throw new Error(job.error || "Background request failed.");
  }
}
function dateLabel(date) {
  return new Intl.DateTimeFormat("en-CA", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "America/Toronto",
  }).format(new Date(date));
}
let replayingHistory = false;
function syncHistory(search = "") {
  if (replayingHistory || isTemporarySession() || !window.history?.pushState) return;
  const target = "/" + (search ? "?" + search : "");
  const current = location.pathname + location.search;
  if (current !== target) history.pushState({ search }, "", target);
}
function show(view) {
  $("setup").inert = false;
  for (const id of ["home", "setup", "reader", "complete"])
    $(id).hidden = id !== view;
}
function articleFraction() {
  const pane = $("article-pane"),
    room = pane.scrollHeight - pane.clientHeight;
  return room > 0 ? Math.min(1, Math.max(0, pane.scrollTop / room)) : 0;
}
function restoreFraction(fraction) {
  const pane = $("article-pane");
  pane.scrollTop = fraction * (pane.scrollHeight - pane.clientHeight);
}
let positionTimer,
  sentFraction = null;
function sendPosition(keepalive = false) {
  clearTimeout(positionTimer);
  if (!currentArticle || $("article-pane").offsetParent === null) return;
  const fraction = articleFraction();
  if (sentFraction !== null && Math.abs(fraction - sentFraction) < 0.005) return;
  sentFraction = fraction;
  // Losing one position update is harmless, and the local copy still has it.
  fetch("/api/position", {
    method: "POST",
    keepalive,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: JSON.stringify({ url: currentArticle.url, fraction }),
  }).catch(() => {});
}
function stashScroll() {
  if (!currentArticle || $("article-pane").offsetParent === null) return;
  writeJSON(positionKey(currentArticle.url), {
    fraction: articleFraction(),
    updated_at: new Date().toISOString(),
  });
  clearTimeout(positionTimer);
  positionTimer = setTimeout(sendPosition, 1500);
}
// The newest position wins, whether it came from this device or another one.
function savedFraction(article) {
  const local = readJSON(positionKey(article.url));
  const server = article.position;
  if (local && (!server || local.updated_at > server.updated_at)) return local.fraction;
  return server?.fraction || 0;
}
function stashDraft() {
  if (noteModel) {
    writeJSON(draftKey(noteModel.url), {
      text: notesText(),
      format: "html",
      revision: noteModel.revision,
    });
  }
}
function setBusy(value) {
  busy = value;
  $("notes").contentEditable = String(!value);
  document
    .querySelectorAll("[data-format]")
    .forEach((b) => (b.disabled = value));
  $("quote-selection").disabled = value || !selectedPassage;
  document
    .querySelectorAll("[data-action]")
    .forEach(
      (b) =>
        (b.disabled =
          value ||
          (b.dataset.action === "previous" && state?.queue?.cursor === 0)),
    );
}
const MAX_QUEUE_ITEMS = 10;
let draftQueue = [],
  draggingURL = null;
function loadQueueDraft() {
  const draft = readJSON(storagePrefix() + "intel-queue-draft");
  if (Array.isArray(draft)) {
    draftQueue = [
      ...new Set(
        draft.filter((url) => state?.articles?.some((a) => a.url === url)),
      ),
    ].slice(0, MAX_QUEUE_ITEMS);
  } else {
    draftQueue = [];
  }
}
function saveQueueDraft() {
  writeJSON(storagePrefix() + "intel-queue-draft", draftQueue);
  if (state?.articles) {
    const existing = readJSON(storagePrefix() + "intel-queue-meta");
    const meta = existing && typeof existing === "object" ? existing : {};
    const byURL = new Map(state.articles.map((a) => [a.url, a]));
    for (const url of draftQueue) {
      const art = byURL.get(url);
      if (art) {
        meta[url] = {
          title: art.title,
          publisher: art.publisher,
          minutes: gymData?.article_minutes?.[url] || meta[url]?.minutes || 5,
        };
      }
    }
    writeJSON(storagePrefix() + "intel-queue-meta", meta);
  }
}
function openQueueBuilder() {
  if (isTemporarySession()) {
    renderSetup();
  } else {
    location.assign("/library?panel=queue");
  }
}
function renderSetup() {
  show("setup");
  syncHistory("view=builder");
  loadQueueDraft();
  renderBuilder();
  loadBuilderTopics();
}
// Temporary sessions have no topics, and the builder works the same without them.
let builderTopics = null;
async function loadBuilderTopics() {
  try {
    builderTopics = (await api("/api/topics")).topics;
  } catch {
    builderTopics = null;
  }
  renderBuilderTopics();
  renderBuilder();
}
function renderBuilderTopics() {
  const select = $("article-topic"),
    chosen = select.value,
    topics = builderTopics || [];
  select.replaceChildren(
    new Option("All articles", ""),
    ...topics.map((topic) =>
      new Option(`${topic.name} · ${topic.articles}${topic.status === "closed" ? " · closed" : ""}`, topic.id),
    ),
  );
  select.value = topics.some((topic) => topic.id === chosen) ? chosen : "";
  $("article-topic-field").hidden = !topics.length;
  $("save-topic-names").replaceChildren(...topics.map((topic) => new Option(topic.name, topic.name)));
}
function renderHome(skipFetch = false) {
  currentArticle = null;
  noteModel = null;
  show("home");
  syncHistory("");
  renderWatchlist();
  if (gymData) renderGym();
  if (!skipFetch) loadAndRenderGym();
}
let gymData = null,
  gymEffort = "all",
  gymFilter = null,
  homeQueueExpanded = false;

async function loadAndRenderGym() {
  if (isTemporarySession()) return;
  try {
    gymData = await api("/api/home-gym");
  } catch {
    gymData = null;
  }
  renderGym();
}

function formatDuration(mins) {
  if (!mins || mins < 60) return `${mins || 0} min`;
  const h = Math.floor(mins / 60),
    m = mins % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

function activeQueueItems() {
  return state?.queue && !state.queue.finished && Array.isArray(state.queue.items) ? state.queue.items : [];
}

let draggingHomeQueueURL = null;
async function updateServerQueue(urls) {
  try {
    state.queue = await api("/api/queue-update", { urls });
    renderGym();
  } catch (error) {
    notice(error.message);
  }
}

async function toggleQueueArticle(article) {
  if (!article?.url) return;
  const current = activeQueueItems().map((item) => item.url);
  if (current.includes(article.url)) {
    await updateServerQueue(current.filter((u) => u !== article.url));
  } else {
    if (current.length >= MAX_QUEUE_ITEMS) {
      notice(`Queue is full (${MAX_QUEUE_ITEMS} articles max). Finish or remove one to add more.`);
      return;
    }
    await updateServerQueue([...current, article.url]);
  }
}

function renderGym() {
  if (!gymData || !state || $("home").hidden) return;
  const pulse = gymData.pulse || {};
  // 1. Mini 8-week heatmap
  const mapEl = $("gym-heatmap");
  mapEl.replaceChildren(
    ...(pulse.heatmap || []).map((cell) => {
      const sq = document.createElement("span");
      sq.className = `heat-day level-${cell.level}${cell.future ? " future" : ""}`;
      sq.title = cell.future
        ? `${cell.date}: upcoming`
        : `${cell.date}: ${cell.count} ${cell.count === 1 ? "report" : "reports"} read`;
      return sq;
    }),
  );
  const weekParts = [`${pulse.week_read || 0} read`];
  if (pulse.week_minutes) weekParts.push(`${formatDuration(pulse.week_minutes)} focused`);
  weekParts.push(`${pulse.week_notes || 0} ${pulse.week_notes === 1 ? "note" : "notes"}`);
  $("gym-week-summary").textContent = `This week: ${weekParts.join(" · ")}`;
  $("gym-month-summary").textContent =
    `${pulse.active_days_30d || 0} active ${pulse.active_days_30d === 1 ? "day" : "days"} in the last 30 days · ${state.articles.length} saved reports`;

  // 2. Research focus themes
  $("gym-window-label").textContent = gymData.window_label || "";
  const themes = gymData.themes || [];
  $("gym-themes").replaceChildren(
    ...themes.map((theme) => {
      const card = document.createElement("article");
      card.className = "gym-theme-card" + (gymFilter?.kind === "theme" && gymFilter.label === theme.name ? " is-active" : "");
      const title = document.createElement("h3");
      title.textContent = theme.name;
      const stats = document.createElement("p");
      stats.className = "gym-theme-stats";
      stats.textContent = `${theme.count} ${theme.count === 1 ? "report" : "reports"} · ${theme.publishers} ${theme.publishers === 1 ? "publisher" : "publishers"}`;
      const desc = document.createElement("p");
      desc.className = "muted gym-theme-desc";
      desc.textContent = theme.top_indicator
        ? `Top signal: ${theme.top_indicator.label}`
        : theme.description;
      const foot = document.createElement("div");
      foot.className = "gym-theme-foot";
      const focusBtn = document.createElement("button");
      focusBtn.type = "button";
      focusBtn.className = "gym-theme-action";
      focusBtn.textContent =
        gymFilter?.kind === "theme" && gymFilter.label === theme.name
          ? "Showing these ✓"
          : `Focus (${theme.unread} unread) →`;
      focusBtn.onclick = () => {
        if (gymFilter?.kind === "theme" && gymFilter.label === theme.name) gymFilter = null;
        else gymFilter = { kind: "theme", label: theme.name, urls: new Set(theme.urls) };
        renderGym();
      };
      foot.append(focusBtn);
      card.append(title, stats, desc, foot);
      return card;
    }),
  );

  // Recurring indicators across labs
  const recurring = gymData.recurring || [];
  $("gym-recurring").hidden = !recurring.length;
  $("gym-recurring-list").replaceChildren(
    ...recurring.map((item) => {
      const pill = document.createElement("button");
      pill.type = "button";
      pill.className =
        "gym-signal-pill" +
        (item.kev ? " is-kev" : "") +
        (gymFilter?.kind === "ioc" && gymFilter.label === item.value ? " is-active" : "");
      pill.textContent = item.name ? `${item.value} · ${item.name}` : item.value;
      pill.title = `${item.reports} reports across ${item.publishers} publishers`;
      pill.onclick = () => {
        if (gymFilter?.kind === "ioc" && gymFilter.label === item.value) gymFilter = null;
        else gymFilter = { kind: "ioc", label: item.value, urls: new Set(item.urls) };
        renderGym();
      };
      return pill;
    }),
  );

  // 3. Single active reading queue card
  const q = state.queue,
    qItems = activeQueueItems(),
    active = qItems.length > 0;
  const minutesMap = gymData.article_minutes || {};
  $("gym-continue").hidden = !active;
  if (!active) homeQueueExpanded = false;
  if (active) {
    const item = qItems[Math.min(q.cursor, qItems.length - 1)];
    const totalMin = qItems.reduce((sum, entry) => sum + (entry.minutes || minutesMap[entry.url] || 5), 0);
    const resuming = q.cursor > 0 || q.read_count > 0;
    // Pull toward finishing: what's done, what's left, how long it has waited, and a next step sized to one article.
    const minutesOf = (entry) => entry.minutes || minutesMap[entry.url] || 5;
    const readCount = qItems.filter((entry) => entry.is_read).length;
    const leftCount = qItems.length - readCount;
    const leftMin = qItems.reduce((sum, entry) => sum + (entry.is_read ? 0 : minutesOf(entry)), 0);
    const nextMin = minutesOf(item);
    const started = q.created_at ? new Date(q.created_at) : null;
    const ageDays = started && !Number.isNaN(started.getTime()) ? Math.floor((Date.now() - started.getTime()) / 86400000) : 0;
    const waiting = ageDays >= 3;
    $("gym-continue").classList.toggle("is-waiting", waiting);
    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
    $("gym-continue-eyebrow").textContent = [
      readCount ? `${readCount} OF ${qItems.length} READ` : `${plural(qItems.length, "ARTICLE").toUpperCase()} WAITING`,
      waiting ? `QUEUED ${ageDays} DAYS AGO` : resuming ? "PICK UP WHERE YOU LEFT OFF" : "",
    ].filter(Boolean).join(" · ");
    $("gym-continue-title").textContent = item.title;
    $("gym-continue-meta").textContent = [
      item.publisher,
      `this one ~${formatDuration(nextMin)}`,
      leftCount > 1
        ? `~${formatDuration(leftMin)} to finish ${readCount ? "the remaining" : "all"} ${leftCount}`
        : "last one in the queue",
    ].join(" · ");
    const progress = $("gym-queue-progress");
    progress.setAttribute("aria-label", `${readCount} of ${qItems.length} read`);
    progress.replaceChildren(
      ...qItems.map((entry, idx) => {
        const seg = document.createElement("span");
        seg.className = entry.is_read ? "done" : idx === q.cursor ? "current" : "";
        seg.style.flexGrow = String(minutesOf(entry));
        seg.title = `${entry.title} · ~${formatDuration(minutesOf(entry))}${entry.is_read ? " · read" : ""}`;
        return seg;
      }),
    );
    $("gym-continue-btn").textContent = `${resuming ? "Read next" : "Start"} · ~${formatDuration(nextMin)} →`;
    $("gym-continue-btn").onclick = () => {
      standaloneURL = null;
      renderQueue().catch((error) => notice(error.message));
    };
    const toggleBtn = $("gym-queue-toggle");
    const queueList = $("gym-queue-list");
    if (toggleBtn && queueList) {
      // A full-width row under the card reads as "open this list", where a small dropdown button did not.
      const count = `${qItems.length} ${qItems.length === 1 ? "article" : "articles"}`;
      const next = qItems[Math.min(q.cursor + 1, qItems.length - 1)];
      toggleBtn.querySelector(".gym-queue-toggle-label").textContent = homeQueueExpanded
        ? `Hide the ${count} in this queue`
        : `Show all ${count} in this queue`;
      toggleBtn.querySelector(".gym-queue-toggle-hint").textContent =
        !homeQueueExpanded && qItems.length > 1 && next !== item ? `Then: ${next.title}` : "";
      toggleBtn.setAttribute("aria-expanded", String(homeQueueExpanded));
      queueList.hidden = !homeQueueExpanded;
      if (homeQueueExpanded) {
        const urls = qItems.map((entry) => entry.url);
        queueList.replaceChildren(
          ...qItems.map((entry, idx) => {
            const li = document.createElement("li");
            const isCurrent = idx === q.cursor;
            li.className = "gym-queue-item" + (isCurrent ? " is-current" : "");
            // Same whole-row drag as the Library's queue panel: drop a row onto another to place it there.
            li.draggable = true;
            li.dataset.url = entry.url;
            li.addEventListener("dragstart", (event) => {
              draggingHomeQueueURL = entry.url;
              li.classList.add("dragging");
              event.dataTransfer.effectAllowed = "move";
              event.dataTransfer.setData("text/plain", entry.url);
            });
            li.addEventListener("dragend", () => {
              draggingHomeQueueURL = null;
              li.classList.remove("dragging");
            });
            li.addEventListener("dragover", (event) => {
              if (!draggingHomeQueueURL) return;
              event.preventDefault();
              li.classList.add("drag-target");
            });
            li.addEventListener("dragleave", () => li.classList.remove("drag-target"));
            li.addEventListener("drop", (event) => {
              if (!draggingHomeQueueURL || draggingHomeQueueURL === entry.url) return;
              event.preventDefault();
              li.classList.remove("drag-target");
              const next = urls.filter((u) => u !== draggingHomeQueueURL);
              next.splice(next.indexOf(entry.url), 0, draggingHomeQueueURL);
              updateServerQueue(next);
            });
            const num = document.createElement("span");
            num.className = "gym-queue-num muted";
            num.textContent = "≡";
            num.setAttribute("aria-hidden", "true");
            const copy = document.createElement("div");
            copy.className = "gym-queue-copy";
            const titleBtn = document.createElement("button");
            titleBtn.type = "button";
            titleBtn.className = "note-article-link";
            titleBtn.textContent = entry.title;
            titleBtn.onclick = () => openStandalone(entry.url);
            const mins = entry.minutes || minutesMap[entry.url];
            const sub = document.createElement("p");
            sub.className = "muted";
            sub.textContent = [
              isCurrent && resuming ? "Up next" : entry.is_read ? "Read" : "",
              entry.publisher,
              mins ? `~${formatDuration(mins)}` : "",
            ].filter(Boolean).join(" · ");
            copy.append(titleBtn, sub);

            const controls = document.createElement("div");
            controls.className = "gym-queue-controls";
            const remove = document.createElement("button");
            remove.type = "button";
            remove.textContent = "×";
            remove.setAttribute("aria-label", `Remove ${entry.title} from queue`);
            remove.onclick = () => updateServerQueue(urls.filter((u) => u !== entry.url));
            controls.append(remove);

            li.append(num, copy, controls);
            return li;
          }),
        );
      }
    }
  }

  // 4. Filtered reading suggestions (keeping queued items visible and clearly badged)
  const queuedSet = new Set(qItems.map((i) => i.url));

  $("gym-active-filter").hidden = !gymFilter;
  if (gymFilter) {
    $("gym-active-filter-text").textContent = `Focused on: ${gymFilter.label}`;
  }

  let pool = state.articles.filter((a) => !a.is_read && a.triage !== "skip");
  if (gymFilter) pool = pool.filter((a) => gymFilter.urls.has(a.url));
  pool = pool.filter((a) => {
    const mins = minutesMap[a.url] || 5;
    const isMedia = ["audio", "video"].includes(a.content_format) || a.url.startsWith("https://www.youtube.com/watch?v=");
    if (gymEffort === "quick") return !isMedia && mins <= 10;
    if (gymEffort === "deep") return !isMedia && mins >= 15;
    if (gymEffort === "media") return isMedia;
    return true;
  });
  pool.sort((a, b) => {
    const score = (art) => (art.kev_cves?.length ? 2 : 0) + (art.watchlist_hits?.length ? 1 : 0);
    return score(b) - score(a);
  });
  currentPendingTriage = pool;

  if (!pool.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No unread reports match this filter right now. Try All or refresh your sources.";
    $("gym-exercise-list").replaceChildren(empty);
  } else {
    $("gym-exercise-list").replaceChildren(
      ...pool.slice(0, 8).map((article) => {
        const row = document.createElement("article");
        row.className = "gym-exercise-row";
        const copy = document.createElement("div");
        const title = document.createElement("button");
        title.type = "button";
        title.className = "note-article-link";
        title.textContent = article.title;
        title.onclick = () => openStandalone(article.url);
        const meta = document.createElement("p");
        meta.className = "muted";
        const mins = minutesMap[article.url];
        meta.textContent = `${article.publisher} · ${dateLabel(article.published_at)}` + (mins ? ` · ~${formatDuration(mins)}` : "");
        const chip = formatChip(article);
        if (chip) meta.prepend(chip);
        copy.append(title, meta);
        if (article.kev_cves?.length || article.watchlist_hits?.length) {
          const wrap = document.createElement("div");
          wrap.className = "triage-badges";
          for (const cve of (article.kev_cves || []).slice(0, 1)) {
            const b = document.createElement("span");
            b.className = "kev-badge";
            b.textContent = `KEV · ${cve}`;
            wrap.append(b);
          }
          for (const hit of (article.watchlist_hits || []).slice(0, 2)) {
            const b = document.createElement("span");
            b.className = "watchlist-hit-badge";
            b.textContent = hit;
            wrap.append(b);
          }
          copy.append(wrap);
        }
        const actions = document.createElement("div");
        actions.className = "gym-exercise-actions";
        const readBtn = document.createElement("button");
        readBtn.type = "button";
        readBtn.textContent = "Read";
        readBtn.onclick = () => openStandalone(article.url);
        const inQueue = queuedSet.has(article.url);
        const queueFull = queuedSet.size >= MAX_QUEUE_ITEMS;
        const setBtn = document.createElement("button");
        setBtn.type = "button";
        setBtn.textContent = inQueue ? "Queued ✓" : "+ Queue";
        setBtn.classList.toggle("is-added", inQueue);
        setBtn.disabled = !inQueue && queueFull;
        setBtn.title = !inQueue && queueFull ? `Queue is full (${MAX_QUEUE_ITEMS} articles max)` : "";
        setBtn.onclick = () => toggleQueueArticle(article);
        const dismissBtn = document.createElement("button");
        dismissBtn.type = "button";
        dismissBtn.className = "gym-dismiss-btn";
        dismissBtn.textContent = "×";
        dismissBtn.title = "Dismiss from suggestions";
        dismissBtn.setAttribute("aria-label", `Dismiss ${article.title}`);
        dismissBtn.onclick = async () => {
          await decideTriage([article.url], "skip");
          renderGym();
        };
        actions.append(readBtn, setBtn, dismissBtn);
        row.append(copy, actions);
        return row;
      }),
    );
  }

  // 5. Right column: Open questions & topics + Recent takeaways
  const topics = gymData.topics || [];
  $("gym-topics-section").hidden = !topics.length;
  $("gym-topics-list").replaceChildren(
    ...topics.map((topic) => {
      const card = document.createElement("a");
      card.className = "gym-topic-item";
      card.href = "/topic?" + new URLSearchParams({ id: topic.id });
      const head = document.createElement("strong");
      head.textContent = topic.name;
      const meta = document.createElement("span");
      meta.className = "muted";
      const bits = [`${topic.articles} ${topic.articles === 1 ? "article" : "articles"}`];
      if (topic.open_questions) bits.push(`${topic.open_questions} open ${topic.open_questions === 1 ? "question" : "questions"}`);
      meta.textContent = bits.join(" · ");
      card.append(head, meta);
      for (const qText of topic.questions || []) {
        const qLine = document.createElement("p");
        qLine.className = "gym-question-quote";
        qLine.textContent = qText;
        card.append(qLine);
      }
      return card;
    }),
  );

  const takeaways = gymData.takeaways || [];
  if (!takeaways.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Jot down a takeaway or highlight while reading — your insights will appear here for recall.";
    $("gym-takeaways-list").replaceChildren(empty);
  } else {
    $("gym-takeaways-list").replaceChildren(
      ...takeaways.map((item) => {
        const card = document.createElement("article");
        card.className = "gym-takeaway-card";
        const quote = document.createElement("p");
        quote.className = "gym-takeaway-text";
        quote.textContent = item.snippet;
        const source = document.createElement("button");
        source.type = "button";
        source.className = "gym-takeaway-source";
        source.textContent = `${item.title} · ${item.publisher}`;
        source.onclick = () => openStandalone(item.url);
        card.append(quote, source);
        return card;
      }),
    );
  }
}

document.querySelectorAll("[data-gym-effort]").forEach((button) => {
  button.addEventListener("click", () => {
    gymEffort = button.dataset.gymEffort;
    document.querySelectorAll("[data-gym-effort]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.gymEffort === gymEffort)),
    );
    renderGym();
  });
});
$("gym-clear-filter")?.addEventListener("click", () => {
  gymFilter = null;
  renderGym();
});
$("gym-clear-queue")?.addEventListener("click", () => updateServerQueue([]));
$("gym-queue-toggle")?.addEventListener("click", () => {
  homeQueueExpanded = !homeQueueExpanded;
  renderGym();
});
$("gym-refresh")?.addEventListener("click", async () => {
  const button = $("gym-refresh"),
    status = $("gym-refresh-status");
  button.disabled = true;
  status.hidden = false;
  status.textContent = "Checking your sources for new research…";
  notice("");
  try {
    const report = await runBackgroundJob("/api/jobs/discover", { days: 30 }),
      added = report.sources.reduce((total, source) => total + source.inserted, 0);
    state = await api("/api/state");
    await loadAndRenderGym();
    status.textContent = added
      ? `${added} new ${added === 1 ? "report" : "reports"} added.`
      : "Up to date — no new reports right now.";
    if (report.errors.length || report.paused.length) notice([...report.errors, ...report.paused].join(" · "));
  } catch (error) {
    status.textContent = "Couldn't refresh sources right now.";
    notice(error.message);
  } finally {
    button.disabled = false;
  }
});

let triageUndo = null,
  currentPendingTriage = [];
function openStandalone(url) {
  standaloneURL = url;
  return renderQueue().catch((error) => notice(error.message));
}
async function decideTriage(urls, decision, undoable = true) {
  try {
    await api("/api/triage", { urls, decision });
  } catch (error) {
    notice(error.message);
    return;
  }
  for (const article of state.articles)
    if (urls.includes(article.url)) article.triage = decision;
  triageUndo = undoable && decision ? urls : null;
  renderGym();
}
function renderWatchlist() {
  const bar = $("watchlist-bar");
  if (!bar) return;
  const temporary = isTemporarySession();
  bar.hidden = temporary;
  if (temporary) return;
  const items = state.watchlists || [];
  $("watchlist-tags").replaceChildren(
    ...items.map((item) => {
      const chip = document.createElement("span");
      chip.className = "watchlist-chip";
      chip.textContent = item.term;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Stop watching ${item.term}`);
      remove.onclick = async () => {
        try {
          await api("/api/watchlist-delete", { id: item.id });
          state = await api("/api/state");
          renderWatchlist();
          renderGym();
        } catch (error) {
          notice(error.message);
        }
      };
      chip.append(remove);
      return chip;
    }),
  );
}
$("watchlist-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("watchlist-input");
  const term = input.value.trim();
  if (!term) return;
  input.disabled = true;
  try {
    await api("/api/watchlists", { term });
    input.value = "";
    state = await api("/api/state");
    renderWatchlist();
    renderGym();
  } catch (error) {
    notice(error.message);
  } finally {
    input.disabled = false;
    input.focus();
  }
});
function formatChip(article) {
  if (!article?.format_label) return null;
  const chip = document.createElement("span");
  chip.className = `format-chip format-${article.format_label.toLowerCase()}`;
  chip.textContent = article.format_label;
  return chip;
}
function styleArticleStatus(meta, article) {
  const publisher = document.createElement("span");
  publisher.className = "article-source";
  publisher.textContent = article.publisher;
  meta.replaceChildren(
    ...[formatChip(article)].filter(Boolean),
    publisher,
    document.createTextNode(` · ${dateLabel(article.published_at)} · `),
  );
  const status = document.createElement("span");
  status.textContent = article.is_read ? "Read" : "Unread";
  if (article.is_read) status.className = "status-read";
  meta.append(status);
  if (article.has_notes) {
    meta.append(document.createTextNode(" · "));
    const noted = document.createElement("span");
    noted.className = "status-noted";
    noted.textContent = "Noted";
    meta.append(noted);
  }
}
function addToDraft(url) {
  if (draftQueue.includes(url)) return;
  if (draftQueue.length >= MAX_QUEUE_ITEMS) {
    notice(`Choose up to ${MAX_QUEUE_ITEMS} articles per queue.`);
    return;
  }
  draftQueue.push(url);
  saveQueueDraft();
  renderBuilder();
}
// Puts an article before another one, or at the end, whether or not it's queued yet.
function placeDraft(url, beforeURL = null) {
  if (url === beforeURL) return;
  const queued = draftQueue.includes(url);
  if (!queued && draftQueue.length >= MAX_QUEUE_ITEMS) {
    notice(`Choose up to ${MAX_QUEUE_ITEMS} articles per queue.`);
    return;
  }
  if (queued) draftQueue.splice(draftQueue.indexOf(url), 1);
  const index = beforeURL ? draftQueue.indexOf(beforeURL) : -1;
  draftQueue.splice(index < 0 ? draftQueue.length : index, 0, url);
  saveQueueDraft();
  renderBuilder();
}
function moveDraft(url, index) {
  const from = draftQueue.indexOf(url);
  if (from < 0) return;
  draftQueue.splice(from, 1);
  draftQueue.splice(Math.max(0, Math.min(index, draftQueue.length)), 0, url);
  saveQueueDraft();
  renderBuilder();
}
function renderBuilder() {
  const sourceInput = $("article-source");
  $("source-options").replaceChildren(
    ...[...new Set(state.articles.map((article) => article.publisher))]
      .sort((a, b) => a.localeCompare(b))
      .map((publisher) => new Option(publisher, publisher)),
  );
  const source = sourceInput.value.trim().toLowerCase();
  const type = $("article-type").value,
    days = Number($("timeframe").value),
    since = days === 0 ? -Infinity : Date.now() - days * 86400000;
  const hideRead = document.querySelector('input[value="hide-read"]').checked,
    onlyNoted = document.querySelector('input[value="only-noted"]').checked,
    topic = (builderTopics || []).find((item) => item.id === $("article-topic").value),
    topicOrder = topic && new Map(topic.urls.map((url, index) => [url, index]));
  // A topic is a deliberate choice, so its older and skipped articles still show.
  $("timeframe").disabled = !!topic;
  const articles = state.articles.filter(
    (a) =>
      (!source || a.publisher.toLowerCase().includes(source)) &&
      (type === "All" ||
        (a.article_types || ["Threat research"]).includes(type)) &&
      (topic
        ? topicOrder.has(a.url)
        : new Date(a.published_at).getTime() >= since && a.triage !== "skip") &&
      (!hideRead || !a.is_read) &&
      (!onlyNoted || a.has_notes),
  );
  if (topic) articles.sort((a, b) => topicOrder.get(a.url) - topicOrder.get(b.url));
  $("result-count").textContent = `${articles.length} results · ${topic ? "oldest" : "newest"} first`;
  const addable = topic ? articles.filter((a) => !draftQueue.includes(a.url)) : [];
  $("add-all").hidden = !addable.length;
  $("add-all").textContent = `Add ${addable.length === articles.length ? "all" : addable.length + " more"} to queue`;
  $("add-all").onclick = () => {
    const room = MAX_QUEUE_ITEMS - draftQueue.length;
    if (addable.length > room) notice(`A queue can hold up to ${MAX_QUEUE_ITEMS} articles.`);
    draftQueue.push(...addable.slice(0, Math.max(0, room)).map((a) => a.url));
    saveQueueDraft();
    renderBuilder();
  };
  $("discovery-results").replaceChildren();
  for (const article of articles) {
    const row = document.createElement("div");
    row.className = "discovery-row";
    const copy = document.createElement("div"),
      title = document.createElement("strong"),
      meta = document.createElement("span"),
      tag = document.createElement("span"),
      button = document.createElement("button");
    title.textContent = article.title;
    if (article.is_read) title.classList.add("read-title");
    meta.className = "choice-meta";
    styleArticleStatus(meta, article);
    copy.append(title, meta);
    tag.className = "type-tag";
    tag.textContent =
      type === "All" ? (article.article_types || ["Threat research"])[0] : type;
    button.textContent = draftQueue.includes(article.url) ? "Added" : "Add";
    button.classList.toggle("is-added", draftQueue.includes(article.url));
    button.setAttribute(
      "aria-pressed",
      String(draftQueue.includes(article.url)),
    );
    button.setAttribute(
      "aria-label",
      `${draftQueue.includes(article.url) ? "Remove from queue" : "Add"}: ${article.title}`,
    );
    button.onclick = () => {
      if (draftQueue.includes(article.url)) {
        draftQueue = draftQueue.filter((url) => url !== article.url);
        saveQueueDraft();
        renderBuilder();
      } else addToDraft(article.url);
    };
    const actions = document.createElement("div");
    actions.className = "builder-article-actions";
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "builder-delete";
    deleteButton.textContent = "Delete";
    deleteButton.setAttribute("aria-label", `Delete article: ${article.title}`);
    deleteButton.onclick = () => openArticleDelete(article);
    actions.append(button, deleteButton);
    row.append(copy, tag, actions);
    row.draggable = true;
    row.addEventListener("dragstart", (e) => {
      draggingURL = article.url;
      e.dataTransfer.setData("text/uri-list", article.url);
    });
    row.addEventListener("dragend", () => {
      draggingURL = null;
      clearQueueDrop();
    });
    $("discovery-results").append(row);
  }
  if (!articles.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No saved matches. Find latest articles or add a link.";
    $("discovery-results").append(empty);
  }
  $("queue-count").textContent = `· ${draftQueue.length} articles`;
  $("draft-queue").replaceChildren();
  draftQueue.forEach((url, index) => {
    const article = state.articles.find((a) => a.url === url);
    if (!article) return;
    const row = document.createElement("li");
    row.draggable = true;
    row.dataset.url = url;
    const number = document.createElement("span");
    number.className = "queue-number";
    number.textContent = String(index + 1);
    const copy = document.createElement("div"),
      title = document.createElement("strong"),
      meta = document.createElement("span");
    title.textContent = article.title;
    if (article.is_read) title.classList.add("read-title");
    meta.className = "choice-meta";
    styleArticleStatus(meta, article);
    copy.append(title, meta);
    const tools = document.createElement("div");
    tools.className = "queue-row-tools";
    for (const [label, shift] of [
      ["Move up", -1],
      ["Move down", 1],
      ["Remove", null],
    ]) {
      const button = document.createElement("button");
      button.textContent = label;
      button.setAttribute("aria-label", `${label}: ${article.title}`);
      button.disabled =
        (shift === -1 && index === 0) ||
        (shift === 1 && index === draftQueue.length - 1);
      button.onclick = () => {
        if (shift === null) {
          draftQueue = draftQueue.filter((u) => u !== url);
          saveQueueDraft();
          renderBuilder();
        } else moveDraft(url, index + shift);
      };
      tools.append(button);
    }
    row.append(number, copy, tools);
    row.addEventListener("dragstart", (e) => {
      draggingURL = url;
      e.dataTransfer.setData("text/uri-list", url);
    });
    row.addEventListener("dragend", () => {
      draggingURL = null;
      clearQueueDrop();
    });
    $("draft-queue").append(row);
  });
  if (!draftQueue.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Drag saved articles here to start your next session.";
    $("draft-queue").append(empty);
  }
  $("save-topic-open").hidden = !draftQueue.length || !builderTopics || !$("save-topic-form").hidden;
  if (!draftQueue.length) $("save-topic-form").hidden = $("save-topic-status").hidden = true;
  $("start").disabled = !draftQueue.length;
  $("start").textContent =
    `Start reading${draftQueue.length ? " · " + draftQueue.length + " articles" : ""}`;
}
document
  .querySelectorAll('input[name="article-status"]')
  .forEach((input) => (input.onchange = renderBuilder));
$("article-source").oninput = renderBuilder;
$("article-topic").onchange = renderBuilder;
$("save-topic-open").onclick = () => {
  $("save-topic-open").hidden = true;
  $("save-topic-form").hidden = false;
  $("save-topic-status").hidden = true;
  $("save-topic-name").focus();
};
$("save-topic-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    const saved = await api("/api/topic-from-articles", {
      name: $("save-topic-name").value,
      urls: draftQueue,
    });
    $("save-topic-name").value = "";
    $("save-topic-form").hidden = true;
    $("save-topic-status").firstElementChild.textContent = saved.created
      ? "Saved as a new topic."
      : saved.added
        ? `Added ${saved.added} ${saved.added === 1 ? "article" : "articles"} to this topic.`
        : "All of these are already in this topic.";
    $("save-topic-link").textContent = saved.name;
    $("save-topic-link").href = "/topic?" + new URLSearchParams({ id: saved.id });
    $("save-topic-status").hidden = false;
    await loadBuilderTopics();
  } catch (error) {
    notice(error.message);
  }
};
$("article-type").onchange = renderBuilder;
$("timeframe").onchange = renderBuilder;
$("discovery-form").onsubmit = async (event) => {
  event.preventDefault();
  $("find-latest").disabled = true;
  $("discovery-status").textContent =
    "Checking enabled publishers for fresh articles…";
  notice("");
  try {
    const report = await runBackgroundJob("/api/jobs/discover", {
      days: Number($("timeframe").value),
    });
    state = await api("/api/state");
    renderBuilder();
    $("discovery-status").textContent = report.errors.length
      ? "Updated with some source issues. Saved results are available."
      : "Updated just now · newest first";
    if (report.errors.length || report.paused.length) notice([...report.errors, ...report.paused].join(" · "));
  } catch (error) {
    $("discovery-status").textContent =
      "Could not refresh. Saved articles remain available.";
    notice(error.message);
  } finally {
    $("find-latest").disabled = false;
  }
};
async function importLinks(text) {
  const urls = [
    ...new Set(( /^(?:file:\/\/|\/|~\/)/i.test(text.trim()) ? text.trim().split(/\r?\n/).filter(Boolean) : text.split(/\s+/).filter((u) => /^https?:\/\//i.test(u)) )),
  ];
  if (!urls.length) {
    notice("Drop or paste an HTTP or HTTPS article link.");
    return;
  }
  $("add-link").disabled = true;
  try {
    for (const url of urls.slice(0, 20)) {
      $("link-status").textContent = "Fetching article details…";
      try {
        const result = await api("/api/link", { url });
        state = await api("/api/state");
        addToDraft(result.url);
        $("article-url").value = "";
      } catch (error) {
        notice(error.message);
      }
    }
    $("link-status").textContent =
      "Links processed. Your queue is ready to edit.";
  } finally {
    $("add-link").disabled = false;
  }
}
$("link-form").onsubmit = (event) => {
  event.preventDefault();
  importLinks($("article-url").value);
};
// The whole pane takes drops. Over a row, the pointer picks which side of it; elsewhere means the end.
const queuePane = document.querySelector(".queue-pane");
function clearQueueDrop() {
  queuePane.classList.remove("drag-over");
  for (const row of $("draft-queue").querySelectorAll(".drop-before, .drop-after"))
    row.classList.remove("drop-before", "drop-after");
}
function queueDropPoint(event) {
  const row = event.target.closest?.("#draft-queue li"),
    rows = $("draft-queue").querySelectorAll("li");
  if (!row) return { row: rows[rows.length - 1] || null, after: true, before: null };
  const box = row.getBoundingClientRect(),
    after = event.clientY > box.top + box.height / 2;
  return { row, after, before: after ? row.nextElementSibling?.dataset.url || null : row.dataset.url };
}
queuePane.addEventListener("dragover", (event) => {
  event.preventDefault();
  clearQueueDrop();
  queuePane.classList.add("drag-over");
  const point = queueDropPoint(event);
  point.row?.classList.add(point.after ? "drop-after" : "drop-before");
});
queuePane.addEventListener("dragleave", (event) => {
  if (!queuePane.contains(event.relatedTarget)) clearQueueDrop();
});
queuePane.addEventListener("drop", (event) => {
  event.preventDefault();
  const { before } = queueDropPoint(event);
  clearQueueDrop();
  if (draggingURL) {
    placeDraft(draggingURL, before);
    return;
  }
  if (event.dataTransfer.files.length) {
    importFiles(Array.from(event.dataTransfer.files));
    return;
  }
  importLinks(
    event.dataTransfer.getData("text/uri-list") ||
      event.dataTransfer.getData("text/plain"),
  );
});
$("queue-form").onsubmit = async (event) => {
  event.preventDefault();
  $("start").disabled = true;
  notice("");
  try {
    standaloneURL = null;
    state.queue = await api("/api/queue", { urls: draftQueue });
    draftQueue = [];
    saveQueueDraft();
    await renderQueue();
  } catch (error) {
    notice(error.message);
    $("start").disabled = false;
  }
};
// Estimate prose reading time at 200 words per minute; exclude media placeholders.
function proseMinutes(article) {
  const template = document.createElement("template");
  template.innerHTML = article.body_html || "";
  const body = template.content || template;
  body
    .querySelectorAll("[data-reading-exclude], figure, video, iframe")
    .forEach((element) => element.remove());
  const words = (body.textContent || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean).length;
  return Math.max(1, Math.ceil(words / 200));
}

// Minutes the focus timer starts at: the text to read, or the length of untranscribed media.
function focusMinutes(article) {
  const media = ["audio", "video"].includes(article.content_format) || article.url.startsWith("https://www.youtube.com/watch?v=");
  if (media && !article.transcript?.has_transcript)
    return article.duration ? Math.max(1, Math.round(article.duration / 60)) : 1;
  return proseMinutes(article);
}

// Audio and video show their saved length, plus the transcript's reading time once there is one.
function readingTimeLabel(article) {
  const media = ["audio", "video"].includes(article.content_format)
    ? article.content_format
    : article.url.startsWith("https://www.youtube.com/watch?v=") ? "video" : "";
  if (!media) return `~${proseMinutes(article)} min read`;
  const length = article.duration
    ? `${Math.max(1, Math.round(article.duration / 60))} min ${media}`
    : media === "audio" ? "Audio" : "Video";
  return article.transcript?.has_transcript
    ? `${length} · ~${proseMinutes(article)} min read`
    : length;
}

function readerMetadata(article) {
  return `${article.publisher} · ${dateLabel(article.published_at)} · ${readingTimeLabel(article)} · ${article.is_read ? "Read" : "Unread"}${article.has_notes ? " · Noted" : ""}`;
}

function renderMedia() {
  renderArticleMedia({
    article: currentArticle,
    mediaEnabled,
    bodyEl: $("article-body"),
    insertCallout,
  });
}

async function refreshArticleIOCs() {
  if (!currentArticle) return;
  const url = currentArticle.url;
  const refreshed = await api("/api/article?" + new URLSearchParams({ url }));
  if (currentArticle?.url !== url) return;
  currentArticle.iocs = refreshed.iocs;
  const wasOpen = $("iocs-panel").open;
  renderIOCs();
  $("iocs-panel").open = wasOpen;
  loadRelated(currentArticle);
}

function renderIOCs() {
  renderArticleIOCs(currentArticle, {
    bodyEl: $("article-body"),
    setPhoneView,
    notice,
    async onIgnore(kind, value, manual) {
      if (!currentArticle) return;
      try {
        await api(
          manual ? "/api/indicator-remove" : "/api/indicator-ignore",
          manual ? { url: currentArticle.url, kind, value } : { kind, value },
        );
        await refreshArticleIOCs();
      } catch (error) {
        notice(error.message);
      }
    },
  });
}

$("ioc-add-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentArticle) return;
  const kind = $("ioc-add-kind").value;
  const input = $("ioc-add-value");
  const value = input.value.trim();
  if (!value) return;
  input.disabled = true;
  try {
    await api("/api/indicator-add", { url: currentArticle.url, kind, value });
    input.value = "";
    await refreshArticleIOCs();
  } catch (error) {
    notice(error.message);
  } finally {
    input.disabled = false;
    input.focus();
  }
});

async function renderQueue() {
  const q = standaloneURL
    ? {
        id: "standalone",
        items: [{ url: standaloneURL }],
        cursor: 0,
        read_count: 0,
      }
    : state.queue;
  if (!q) {
    if (isTemporarySession()) renderSetup();
    else renderHome();
    return;
  }
  if (q.finished) {
    currentArticle = null;
    noteModel = null;
    if (isTemporarySession()) {
      renderSetup();
      $("setup").inert = true;
    } else {
      renderHome();
      $("home").inert = true;
    }
    $("complete").hidden = false;
    $("new-queue").focus();
    $("completion-count").textContent =
      `${q.read_count} of ${q.items.length} articles marked read. ${q.items.length - q.read_count} remain unread.`;
    return;
  }
  const item = q.items[q.cursor];
  const article = await api(
    "/api/article?" + new URLSearchParams({ url: item.url }),
  );
  currentArticle = article;
  sentFraction = null;
  armPassage();
  setPhoneView("read");
  noteModel = {
    url: article.url,
    revision: article.notes_revision,
    savedText: article.notes,
  };
  let text = article.notes,
    draftFormat = null;
  const draft = readJSON(draftKey(article.url));
  if (draft && draft.text !== article.notes) {
    text = draft.text;
    draftFormat = draft.format || "plain";
    noteModel.revision = draft.revision;
    notice(
      draft.revision === article.notes_revision
        ? "Recovered your unsaved note draft."
        : "A local draft conflicts with newer saved notes. Copy your draft before reloading.",
    );
  }
  loadNotes(text, draftFormat || article.notes_format);
  noteModel.savedText =
    article.notes_format === "html"
      ? sanitizeNotes(article.notes)
      : plainHTML(article.notes);
  $("save-status").textContent =
    text === article.notes ? "Saved" : "Unsaved draft";
  $("article-title").textContent = article.title;
  $("article-meta").textContent = readerMetadata(article);
  $("article-format").replaceChildren(...[formatChip(article)].filter(Boolean));
  let source = null;
  try {
    source = new URL(article.url);
  } catch {}
  $("original").hidden =
    !source ||
    !["https:", "http:"].includes(source.protocol) ||
    (isTemporarySession() && source.hostname === "temporary.invalid");
  $("original").href = source ? article.url : "";
  $("article-body").dataset.bodyFormat = article.body_format || "markdown";
  $("article-body").innerHTML = article.body_html;
  applyTextScale();
  renderMedia();
  renderTranscript();
  window.renderArticleMath?.();
  renderHighlights();
  renderIOCs();
  renderBacklinks(article);
  populateArticleCitations(article);
  resetAIPanel();
  loadRelated(article);
  loadTopics(article);
  const next = q.items[q.cursor + 1];
  document
    .querySelectorAll(".position")
    .forEach(
      (el) =>
        (el.textContent = `Article ${q.cursor + 1} of ${q.items.length} · ${q.read_count} read`),
    );
  document.querySelectorAll(".queue-progress").forEach((el) => {
    el.max = q.items.length;
    el.value = q.read_count;
  });
  document
    .querySelectorAll(".next-title")
    .forEach(
      (el) => (el.textContent = next ? next.title : "Finish this queue"),
    );
  $("read-next").textContent =
    q.cursor === q.items.length - 1 ? "Mark read & finish" : "Mark read & next";
  document.querySelector('[data-action="next"]').textContent =
    q.cursor === q.items.length - 1 ? "Finish" : "Next";
  $("mark-unread").hidden = !article.is_read;
  show("reader");
  syncHistory(
    standaloneURL
      ? new URLSearchParams({ open: standaloneURL }).toString()
      : "view=reader",
  );
  document.dispatchEvent(
    new CustomEvent("reader-article", { detail: article }),
  );
  setBusy(false);
  document
    .querySelectorAll(
      "[data-action=previous],[data-action=next],[data-action=mark_next],[data-action=unread],.nav-bar .up-next,.nav-bar .progress-area",
    )
    .forEach((el) => (el.hidden = !!standaloneURL));
  $("mark-unread").hidden = !!standaloneURL || !article.is_read;
  restoreFraction(savedFraction(article));
  sentFraction = articleFraction();
  if (text !== article.notes && noteModel.revision === article.notes_revision)
    timer = setTimeout(() => saveNow().catch((e) => notice(e.message)), 600);
}
function saveNow() {
  clearTimeout(timer);
  if (!noteModel) return saving;
  const model = noteModel,
    text = notesText();
  saving = saving
    .catch(() => {})
    .then(async () => {
      if (text === model.savedText) return;
      if (noteModel === model) $("save-status").textContent = "Saving…";
      try {
        const result = await api("/api/notes", {
          url: model.url,
          text,
          format: "html",
          revision: model.revision,
        });
        model.revision = result.revision;
        model.savedText = text;
        if (noteModel === model) {
          const container = document.createElement("template");
          container.innerHTML = text;
          currentArticle.has_notes = !!(container.content.textContent || "")
            .replace(/\u200b/g, "")
            .trim();
          $("article-meta").textContent = readerMetadata(currentArticle);
        }
        if (noteModel === model) {
          $("save-status").textContent =
            notesText() === text ? "Saved" : "Unsaved changes";
          if (notesText() === text) removeItem(draftKey(model.url));
          else stashDraft();
        }
      } catch (error) {
        if (noteModel === model) {
          $("save-status").textContent = "Not saved — retry by editing";
          stashDraft();
        }
        throw error;
      }
    });
  return saving;
}
$("notes").addEventListener("input", () => {
  stashDraft();
  $("save-status").textContent = "Unsaved changes";
  clearTimeout(timer);
  timer = setTimeout(
    () => saveNow().catch((error) => notice(error.message)),
    600,
  );
});
$("article-pane").addEventListener("scroll", stashScroll, { passive: true });
document.addEventListener("visibilitychange", () => {
  if (document.hidden) sendPosition(true);
});
// Notes written offline are already stashed; send them when the connection returns.
window.addEventListener("online", () => saveNow().catch(() => {}));
window.addEventListener("beforeunload", (event) => {
  stashDraft();
  if (noteModel && notesText() !== noteModel.savedText) {
    event.preventDefault();
    event.returnValue = "";
  }
});
document.querySelectorAll("[data-action]").forEach(
  (button) =>
    (button.onclick = async () => {
      if (busy) return;
      setBusy(true);
      notice("");
      stashScroll();
      try {
        await saveNow();
        await window.pauseFocusTimer?.();
        if (button.dataset.action === "home") {
          standaloneURL = null;
          state = await api("/api/state");
          renderHome();
          setBusy(false);
          return;
        }
        if (button.dataset.action === "exit") {
          const wasStandalone = !!standaloneURL;
          standaloneURL = null;
          state = await api("/api/state");
          if (!wasStandalone && state.queue) {
            draftQueue = state.queue.items.map((item) => item.url);
            saveQueueDraft();
          }
          currentArticle = null;
          noteModel = null;
          armPassage();
          openQueueBuilder();
          setBusy(false);
          return;
        }
        state.queue = await api("/api/navigate", {
          queue_id: state.queue.id,
          cursor: state.queue.cursor,
          action: button.dataset.action,
        });
        await renderQueue();
      } catch (error) {
        notice(error.message);
        setBusy(false);
      }
    }),
);
let selectedAnchor = null,
  activeHighlight = null,
  holdPassage = false;
function updateSelectionBar() {
  $("selection-actions").hidden = !selectedPassage && !activeHighlight;
  $("quote-selection").hidden = !selectedPassage;
  $("highlight-selection").hidden = !selectedAnchor;
  $("remove-highlight").hidden = !activeHighlight;
}
function armPassage(text = "", html = "", words = 0, anchor = null) {
  selectedPassage = text;
  selectedPassageHTML = html;
  selectedAnchor = anchor;
  if (text) activeHighlight = null;
  updateSelectionBar();
  const button = $("quote-selection");
  button.disabled = busy || !text;
  button.textContent = text
    ? `Add ${words} selected ${words === 1 ? "word" : "words"} to notes`
    : "Add selected passage to notes";
}
function noteCode(tex) {
  const code = document.createElement("code");
  code.textContent = tex;
  return code;
}
document.addEventListener("selectionchange", () => {
  const selection = window.getSelection();
  const isArticleText = (node) =>
    $("article-body").contains(node) || $("article-title").contains(node);
  if (
    !selection ||
    !selection.rangeCount ||
    !isArticleText(selection.anchorNode) ||
    !isArticleText(selection.focusNode)
  )
    return;
  const text = selection.toString().trim();
  if (holdPassage) return;
  // Clicking into the notes keeps the passage, so it can go where the caret is.
  if (!text) {
    armPassage();
    return;
  }
  const range = selection.getRangeAt(0),
    holder = document.createElement("div");
  holder.append(range.cloneContents());
  const common =
    range.commonAncestorContainer.nodeType === 1
      ? range.commonAncestorContainer
      : range.commonAncestorContainer.parentElement;
  // A selection inside one element clones without that element around it.
  const math = common.closest(".article-math"),
    code = common.closest("pre, code");
  if (math) holder.replaceChildren(noteCode(math.dataset.tex));
  else if (code?.closest("pre")) {
    const pre = document.createElement("pre");
    pre.append(noteCode(holder.textContent));
    holder.replaceChildren(pre);
  } else if (code) holder.replaceChildren(noteCode(holder.textContent));
  for (const element of holder.querySelectorAll(".article-math"))
    element.replaceWith(noteCode(element.dataset.tex));
  for (const heading of holder.querySelectorAll("h1,h2,h3,h4,h5,h6")) {
    const bold = document.createElement("strong");
    bold.append(...heading.childNodes);
    const paragraph = document.createElement("p");
    paragraph.append(bold);
    heading.replaceWith(paragraph);
  }
  const start =
    range.startContainer.nodeType === 1
      ? range.startContainer
      : range.startContainer.parentElement;
  const end =
    range.endContainer.nodeType === 1
      ? range.endContainer
      : range.endContainer.parentElement;
  const heading = start.closest("h1,h2,h3,h4,h5,h6");
  armPassage(
    text,
    heading && heading === end.closest("h1,h2,h3,h4,h5,h6")
      ? "<strong>" + plainHTML(text) + "</strong>"
      : sanitizeNotes(holder.innerHTML),
    holder.textContent.trim().split(/\s+/).length,
    passageAnchor(range, $("article-body")),
  );
});
// Pressing a button must not clear the selection and hide it mid-click.
$("selection-actions").addEventListener("mousedown", (e) => e.preventDefault());
$("selection-actions").addEventListener("pointerdown", () => (holdPassage = true));
document.addEventListener("pointerup", () => setTimeout(() => (holdPassage = false), 400));
$("quote-selection").onclick = () => {
  holdPassage = false;
  if (!selectedPassage) return;
  notice("");
  insertCallout(selectedPassage, selectedPassageHTML);
  armPassage();
  window.getSelection().removeAllRanges();
};
$("highlight-selection").onclick = async () => {
  holdPassage = false;
  if (!selectedAnchor || !currentArticle) return;
  const article = currentArticle,
    anchor = selectedAnchor;
  armPassage();
  window.getSelection().removeAllRanges();
  try {
    const highlight = await api("/api/highlights", { url: article.url, ...anchor });
    (article.highlights ||= []).push(highlight);
    if (currentArticle === article) renderHighlights();
  } catch (error) {
    notice(error.message);
  }
};
function renderHighlights() {
  const bodyEl = $("article-body");
  clearHighlightMarks(bodyEl);
  const highlights = currentArticle?.highlights || [],
    all = bodyEl.textContent;
  for (const highlight of highlights) {
    const start = locateHighlight(all, highlight);
    highlight.found = start >= 0;
    if (start >= 0) markHighlight(bodyEl, start, start + highlight.quote.length, highlight.id);
  }
  $("highlights-panel").hidden = !highlights.length;
  $("highlights-count").textContent = highlights.length ? `· ${highlights.length}` : "";
  $("highlights-list").replaceChildren(
    ...highlights.map((highlight) => {
      const item = document.createElement("li"),
        jump = document.createElement("button");
      jump.type = "button";
      jump.textContent = highlight.quote;
      if (highlight.found) jump.onclick = () => jumpToHighlight(highlight.id);
      else {
        jump.disabled = true;
        jump.title = "This passage is no longer in the saved text.";
      }
      item.append(jump);
      return item;
    }),
  );
}
function jumpToHighlight(id) {
  setPhoneView("read");
  const mark = $("article-body").querySelector(`[data-highlight-id="${CSS.escape(id)}"]`);
  if (!mark) return;
  mark.scrollIntoView({ block: "center" });
  mark.classList.add("highlight-flash");
  setTimeout(() => mark.classList.remove("highlight-flash"), 1600);
  activeHighlight = id;
  updateSelectionBar();
}
$("article-body").addEventListener("click", (event) => {
  if (!window.getSelection().isCollapsed) return;
  const mark = event.target.closest?.("[data-highlight-id]");
  activeHighlight = mark ? mark.dataset.highlightId : null;
  updateSelectionBar();
});
$("remove-highlight").onclick = async () => {
  const article = currentArticle,
    id = activeHighlight;
  if (!article || !id) return;
  try {
    await api("/api/highlight-delete", { id });
    article.highlights = article.highlights.filter((item) => item.id !== id);
    activeHighlight = null;
    updateSelectionBar();
    if (currentArticle === article) renderHighlights();
  } catch (error) {
    notice(error.message);
  }
};
// Separate from the article request, because a first visit may have a whole library to read.
async function loadRelated(article) {
  $("related-panel").hidden = true;
  $("related-panel").open = false;
  try {
    while (currentArticle === article) {
      const data = await api("/api/related?" + new URLSearchParams({ url: article.url }));
      if (currentArticle !== article) return;
      renderArticleRelated(data, { dateLabel });
      if (data.complete) return;
    }
  } catch {
    // The article is still readable without this, including offline and in temporary sessions.
  }
}
const topicEditor = mountTopicEditor($("topics-editor"), { post: api, onError: notice });
async function loadTopics(article) {
  $("topics-panel").hidden = true;
  try {
    const data = await api("/api/article-topics?" + new URLSearchParams({ url: article.url }));
    if (currentArticle !== article) return;
    topicEditor.show(article.url, data);
    $("topics-panel").hidden = false;
  } catch {
    // Offline and temporary sessions have no topics, so the panel stays hidden.
  }
}
function setPhoneView(view) {
  const workspace = $("workspace");
  if (workspace.dataset.phoneView === view) return;
  // Hiding the pane resets its scroll, so the place is carried across the switch.
  const fraction = $("article-pane").offsetParent ? articleFraction() : null;
  if (fraction !== null) stashScroll();
  workspace.dataset.phoneView = view;
  for (const tab of document.querySelectorAll(".reader-tabs [role=tab]")) {
    tab.setAttribute("aria-selected", String(tab.dataset.view === view));
    if (tab.id === "tab-notes" && view === "notes") tab.textContent = "Notes";
  }
  if (view === "read" && currentArticle) restoreFraction(savedFraction(currentArticle));
}
for (const tab of document.querySelectorAll(".reader-tabs [role=tab]")) {
  tab.addEventListener("mousedown", (event) => event.preventDefault());
  tab.onclick = () => setPhoneView(tab.dataset.view);
}

const { applyTextScale } = initTextScale({
  readItem,
  writeItem,
  articleFraction,
  restoreFraction,
  onScaleChange() {
    window.renderArticleMath?.();
    renderHighlights();
  },
});
$("theme-select").value = window.intelTheme?.get() || "auto";
$("theme-select").onchange = () => window.intelTheme?.set($("theme-select").value);

document.addEventListener("keydown", (event) => {
  if ($("complete").hidden) return;
  if (event.key === "Escape") {
    event.preventDefault();
    $("complete").hidden = true;
    $("setup").inert = false;
    $("home").inert = false;
    if (isTemporarySession()) $("article-type")?.focus();
  } else if (event.key === "Tab") {
    event.preventDefault();
    $("new-queue").focus();
  }
});
$("new-queue").onclick = async () => {
  try {
    $("complete").hidden = true;
    $("home").inert = false;
    $("setup").inert = false;
    if (isTemporarySession()) {
      state = await api("/api/state");
      renderSetup();
    } else {
      location.assign("/library?panel=queue");
    }
  } catch (error) {
    notice(error.message);
  }
};
// Keeps only active queued articles cached offline without flooding the server on every page load.
function saveForOffline() {
  if (!navigator.serviceWorker?.controller || !navigator.onLine || !state || !window.caches) return;
  const upcoming = state.queue && !state.queue.finished ? state.queue.items.map((item) => item.url) : [];
  if (!upcoming.length) return;
  (async () => {
    for (const url of [...new Set(upcoming)].slice(0, 10)) {
      if (!navigator.onLine) return;
      const apiPath = "/api/article?" + new URLSearchParams({ url });
      try {
        if (await caches.match(apiPath)) continue;
      } catch {}
      await fetch(apiPath).catch(() => {});
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
  })();
}

let mediaEnabled = readItem(storagePrefix() + "intel-load-media") === "true";
$("load-media").checked = mediaEnabled;
$("load-media").addEventListener("change", () => {
  mediaEnabled = $("load-media").checked;
  writeItem(storagePrefix() + "intel-load-media", String(mediaEnabled));
  const scroll = $("article-pane").scrollTop;
  if (currentArticle) {
    $("article-body").innerHTML = currentArticle.body_html;
    renderMedia();
    renderTranscript();
    window.renderArticleMath?.();
    renderHighlights();
    $("article-pane").scrollTop = scroll;
  }
});

// Transcripts: timestamps that seek the player, or a panel to make one with local Whisper.
let transcriptPoll = null;
function renderTranscript() {
  clearTimeout(transcriptPoll);
  const info = currentArticle?.transcript;
  if (!info) return;
  const body = $("article-body");
  if (info.has_transcript) {
    const heading = [...body.querySelectorAll("h2")].find((h) => h.textContent.trim() === "Transcript");
    let node = heading?.nextElementSibling;
    for (; node; node = node.nextElementSibling) {
      const time = node.firstElementChild;
      if (node.tagName !== "P" || time?.tagName !== "STRONG" || !/^\d+:\d{2}(:\d{2})?$/.test(time.textContent)) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "transcript-time";
      button.textContent = time.textContent;
      button.setAttribute("aria-label", `Play from ${time.textContent}`);
      button.onclick = () => seekTo(transcriptSeconds(button.textContent), body);
      time.replaceWith(button);
    }
    return;
  }
  if (!info.available) return;
  const panel = document.createElement("section");
  panel.className = "transcript-panel";
  const heading = document.createElement("h2");
  heading.textContent = "Transcript";
  const message = document.createElement("p");
  message.className = "transcript-status";
  panel.append(heading, message);
  const running = ["queued", "downloading", "running"].includes(info.state);
  if (running) {
    message.textContent = {
      queued: "Waiting for another transcription to finish.",
      downloading: "Downloading the audio from YouTube. The first time, macOS may ask on the Mac to allow access to Chrome Safe Storage.",
      running: "Transcribing with Whisper, about a minute for every 10 minutes. You can keep reading.",
    }[info.state];
    transcriptPoll = setTimeout(checkTranscript, 5000);
  } else if (info.local || info.youtube) {
    message.textContent =
      info.state === "failed"
        ? info.error
        : info.local
          ? "No transcript yet. Whisper runs on this machine."
          : "No transcript yet. This downloads the audio with your Chrome cookies and transcribes it on this machine.";
    const start = document.createElement("button");
    start.type = "button";
    start.textContent = info.state === "failed" ? "Try again" : "Transcribe";
    start.onclick = async () => {
      start.disabled = true;
      try {
        currentArticle.transcript = { ...info, ...(await api("/api/transcribe", { url: currentArticle.url })) };
        renderTranscript();
      } catch (error) {
        message.textContent = error.message;
        start.disabled = false;
      }
    };
    panel.append(start);
  } else {
    const id = new URL(currentArticle.url).searchParams.get("v");
    message.textContent =
      info.state === "failed"
        ? info.error
        : "Download the audio with your own browser cookies, then attach the file from your Downloads folder. Whisper transcribes it on this machine.";
    if (id) {
      const command = document.createElement("code");
      command.className = "transcript-command";
      command.textContent = `uvx --from "yt-dlp[default]" yt-dlp -x --audio-format m4a --cookies-from-browser chrome -P ~/Downloads -o "%(id)s.%(ext)s" ${id}`;
      const copy = document.createElement("button");
      copy.type = "button";
      copy.textContent = "Copy command";
      copy.onclick = async () => {
        try {
          await navigator.clipboard.writeText(command.textContent);
          copy.textContent = "Copied";
        } catch {
          copy.textContent = "Select and copy the command";
        }
      };
      panel.append(command, copy);
    }
    const pick = document.createElement("label");
    pick.className = "transcript-attach";
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "audio/*,.m4a,.opus,.webm,.mp4";
    input.className = "sr-only";
    pick.append("Attach audio file", input);
    const upload = async (file) => {
      if (!file) return;
      message.textContent = `Uploading ${file.name}…`;
      try {
        const response = await fetch("/api/transcript-audio", {
          method: "POST",
          headers: {
            "Content-Type": "application/octet-stream",
            "X-CSRF-Token": token,
            "X-Filename": encodeURIComponent(file.name),
            "X-Article-URL": encodeURIComponent(currentArticle.url),
          },
          body: file,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Upload failed.");
        currentArticle.transcript = { ...info, ...result };
        renderTranscript();
      } catch (error) {
        message.textContent = error.message;
      }
    };
    input.onchange = () => upload(input.files[0]);
    panel.addEventListener("dragover", (event) => {
      event.preventDefault();
      panel.classList.add("is-dragging");
    });
    panel.addEventListener("dragleave", () => panel.classList.remove("is-dragging"));
    panel.addEventListener("drop", (event) => {
      event.preventDefault();
      panel.classList.remove("is-dragging");
      upload(event.dataTransfer.files[0]);
    });
    panel.append(pick);
  }
  body.querySelector(".transcript-panel")?.remove();
  body.append(panel);
}
async function checkTranscript() {
  const url = currentArticle?.url;
  if (!url) return;
  try {
    const article = await api("/api/article?" + new URLSearchParams({ url }));
    if (currentArticle?.url !== url) return;
    const done = article.transcript?.has_transcript;
    currentArticle = { ...currentArticle, transcript: article.transcript, body_html: article.body_html, iocs: article.iocs };
    if (done) {
      const scroll = $("article-pane").scrollTop;
      $("article-body").innerHTML = currentArticle.body_html;
      renderMedia();
      renderTranscript();
      renderHighlights();
      renderIOCs();
      $("article-pane").scrollTop = scroll;
    } else renderTranscript();
  } catch {
    transcriptPoll = setTimeout(checkTranscript, 15000);
  }
}

initPaneDivider({ storagePrefix, readItem, writeItem });

// Finder drops provide file bytes, rather than a usable local filesystem path.
async function importFiles(files) {
  $("add-link").disabled = true;
  const feedback = document.getElementById("link-status");
  feedback.hidden = false;
  try {
    for (const file of files.slice(0, 20)) {
      if (!(isTemporarySession() ? /\.(pdf|md|markdown|html|htm|txt|json)$/i : /\.(pdf|mp3|m4a|ogg|wav|mp4|webm|eml|pptx)$/i).test(file.name)) throw new Error("Unsupported file type.");
      if (file.size > (/\.(pdf|eml|pptx)$/i.test(file.name) ? 20000000 : 100000000)) throw new Error("Documents are limited to 20 MB; audio/video to 100 MB.");
      feedback.textContent = `Importing ${file.name}…`;
      const response = await fetch("/api/file-upload", {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-CSRF-Token": token, "X-Filename": encodeURIComponent(file.name) },
        body: file,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "File import failed.");
      state = await api("/api/state");
      addToDraft(result.url);
    }
    feedback.textContent = "Files added.";
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    $("add-link").disabled = false;
  }
}

let articleToDelete = null;
function openArticleDelete(article) {
  articleToDelete = article;
  $("delete-title").textContent = "Delete this article?";
  $("delete-error").hidden = true;
  $("delete-dialog").showModal();
}
$("cancel-delete").onclick = () => $("delete-dialog").close();
$("delete-dialog").addEventListener("cancel", (event) => {
  if ($("confirm-delete").disabled) event.preventDefault();
});
$("confirm-delete").onclick = async () => {
  const article = articleToDelete;
  if (!article) return;
  $("confirm-delete").disabled = true;
  $("cancel-delete").disabled = true;
  $("delete-error").hidden = true;
  try {
    await api("/api/delete-article", { url: article.url });
    draftQueue = draftQueue.filter((url) => url !== article.url);
    saveQueueDraft();
    removeItem(draftKey(article.url));
    state = await api("/api/state");
    renderBuilder();
    $("delete-dialog").close();
    articleToDelete = null;
  } catch (error) {
    $("delete-error").textContent = error.message;
    $("delete-error").hidden = false;
  } finally {
    $("confirm-delete").disabled = false;
    $("cancel-delete").disabled = false;
  }
};

function renderBacklinks(article) {
  const panel = $("backlinks-panel");
  if (!panel) return;
  const links = article?.backlinks || [];
  panel.hidden = !links.length;
  $("backlinks-count").textContent = links.length ? `· ${links.length}` : "";
  $("backlinks-list").replaceChildren(
    ...links.map((item) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.className = "related-report";
      a.href = item.href;
      const title = document.createElement("span");
      title.className = "related-title";
      title.textContent = item.title;
      const meta = document.createElement("span");
      meta.className = "muted";
      meta.textContent = item.kind === "topic" ? "Topic notes" : "Article notes";
      a.append(title, meta);
      li.append(a);
      return li;
    }),
  );
}

function populateArticleCitations(article) {
  const menu = $("article-cite-menu");
  const list = $("article-cite-list");
  if (!menu || !list) return;
  const others = (state?.articles || []).filter((a) => a.url !== article?.url).slice(0, 60);
  menu.hidden = isTemporarySession() || !others.length;
  if (menu.hidden) return;
  list.replaceChildren(
    ...others.map((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      const title = document.createElement("span");
      title.textContent = item.title;
      const meta = document.createElement("span");
      meta.className = "muted";
      meta.textContent = `${item.publisher} · ${dateLabel(item.published_at)}`;
      btn.append(title, meta);
      btn.addEventListener("mousedown", (event) => event.preventDefault());
      btn.onclick = () => {
        if (busy) return;
        const link = document.createElement("a");
        link.setAttribute("href", "/?" + new URLSearchParams({ open: item.url }));
        link.textContent = `${item.publisher}, ${dateLabel(item.published_at)}`;
        focusNotes();
        document.execCommand("insertHTML", false, link.outerHTML + "&nbsp;");
        menu.open = false;
        noteChanged();
      };
      return btn;
    }),
  );
}

let aiLastOutput = "";
let aiResults = {};
let aiShown = null;
let aiArticleURL = "";
const AI_ID_NOTES = { retired: "retired in ATT&CK", unknown: "not an ATT&CK ID" };

function aiWhen(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : dateLabel(iso);
}

// Shows one saved answer: rendered Markdown, who answered and when, and a check of every ATT&CK ID in it.
function showAIResult(result) {
  aiShown = result;
  aiLastOutput = result?.markdown || "";
  $("ai-result").hidden = !result;
  $("ai-status").hidden = true;
  if (!result) return;
  $("ai-result-label").textContent = result.label;
  $("ai-result-info").textContent = `· ${result.model} · ${aiWhen(result.created_at)}`;
  $("ai-result-question").hidden = result.mode !== "ask";
  $("ai-result-question").textContent = result.mode === "ask" ? `Q: ${result.question}` : "";
  // The server renders Markdown with raw HTML turned off, so model output can't inject markup.
  $("ai-output").innerHTML = result.html;
  const checks = result.checks || [];
  const flagged = checks.filter((c) => c.status !== "ok");
  $("ai-checks").hidden = !checks.length;
  $("ai-checks").replaceChildren(
    ...checks.map((check) => {
      const li = document.createElement("li");
      li.className = `ai-check ai-check-${check.status}`;
      const code = document.createElement("code");
      code.textContent = check.id;
      li.append(code, ` ${check.status === "ok" ? check.name : AI_ID_NOTES[check.status]}`);
      return li;
    }),
  );
  if (checks.length) {
    const head = document.createElement("li");
    head.className = "ai-checks-head";
    head.textContent = flagged.length
      ? `ATT&CK check: ${flagged.length} of ${checks.length} IDs need a look`
      : `ATT&CK check: all ${checks.length} IDs are real techniques`;
    $("ai-checks").prepend(head);
  }
  document.querySelectorAll("[data-ai-mode]").forEach((b) => {
    b.classList.toggle("has-result", Boolean(aiResults[b.dataset.aiMode]));
    b.setAttribute("aria-pressed", String(b.dataset.aiMode === result.mode));
  });
}

function resetAIPanel() {
  aiLastOutput = "";
  aiResults = {};
  aiShown = null;
  if (!$("ai-result")) return;
  $("ai-status").textContent = "";
  $("ai-status").hidden = true;
  $("ai-result").hidden = true;
  if ($("ai-question")) $("ai-question").value = "";
  document.querySelectorAll("[data-ai-mode]").forEach((b) => {
    b.classList.remove("has-result");
    b.setAttribute("aria-pressed", "false");
  });
  loadAIResults();
}

// Earlier answers for this article come back without another model call.
async function loadAIResults() {
  if ($("ai-panel")?.hidden || !currentArticle) return;
  const url = (aiArticleURL = currentArticle.url);
  try {
    const data = await api("/api/ai-results?" + new URLSearchParams({ url }));
    if (url !== aiArticleURL) return;
    aiResults = Object.fromEntries((data.results || []).map((r) => [r.mode, r]));
    if (data.results?.length) showAIResult(data.results[0]);
  } catch {
    /* Saved answers are a convenience; the buttons still work without them. */
  }
}

async function runAI(mode, question = "") {
  if (!currentArticle) return;
  const panel = $("ai-panel");
  const url = currentArticle.url;
  panel.querySelectorAll("button, input").forEach((el) => (el.disabled = true));
  $("ai-status").textContent = aiStatusLocal
    ? "Asking the local model… a long report can take a minute or two."
    : "Asking the model…";
  $("ai-status").hidden = false;
  try {
    const result = await api("/api/ai-assist", { url, mode, question });
    if (url !== currentArticle?.url) return;
    aiResults[mode] = result;
    showAIResult(result);
  } catch (error) {
    $("ai-status").textContent = error.message;
  } finally {
    panel.querySelectorAll("button, input").forEach((el) => (el.disabled = false));
  }
}

let aiStatusLocal = false;
async function initAIAssist() {
  const panel = $("ai-panel");
  if (!panel || isTemporarySession()) return;
  try {
    const status = await api("/api/ai-status");
    if (!status?.enabled) return;
    panel.hidden = false;
    aiStatusLocal = Boolean(status.local);
    $("ai-model-label").textContent = `· ${status.model || status.provider}${status.local ? " · local" : " · cloud"}`;
  } catch {
    return;
  }
  // A saved answer shows instantly; Regenerate asks the model again.
  panel.querySelectorAll("[data-ai-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      const saved = aiResults[button.dataset.aiMode];
      if (saved) showAIResult(saved);
      else runAI(button.dataset.aiMode);
    });
  });
  $("ai-ask")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = $("ai-question").value.trim();
    if (!question) {
      $("ai-status").textContent = "Type a question about this article first.";
      $("ai-status").hidden = false;
      return;
    }
    runAI("ask", question);
  });
  $("ai-regenerate")?.addEventListener("click", () => {
    if (aiShown) runAI(aiShown.mode, aiShown.question || "");
  });
  $("ai-insert")?.addEventListener("click", () => {
    if (!aiLastOutput || busy) return;
    insertCallout(aiLastOutput);
  });
  loadAIResults();
}

initShortcuts({
  onTriageKeep() {
    if (currentPendingTriage.length) toggleQueueArticle(currentPendingTriage[0]);
  },
  onTriageSkip() {
    if (currentPendingTriage.length) decideTriage([currentPendingTriage[0].url], "skip");
  },
  onTriageUndo() {
    if (triageUndo) decideTriage(triageUndo, null, false);
  },
});

// Expose workspace hooks needed by companion classic scripts (notes-editor.js, focus.js, temporary.js).
Object.defineProperty(window, "busy", { get: () => busy, configurable: true });
Object.defineProperty(window, "currentArticle", { get: () => currentArticle, configurable: true });
window.notice = notice;
window.saveNow = saveNow;
window.focusMinutes = focusMinutes;
window.readerMetadata = readerMetadata;

window.addEventListener("popstate", async () => {
  if (isTemporarySession() || !state) return;
  replayingHistory = true;
  try {
    await saveNow().catch(() => {});
    const params = new URLSearchParams(location.search);
    const open = params.get("open");
    const view = params.get("view");
    if (open) {
      standaloneURL = open;
      await renderQueue();
    } else if (view === "builder") {
      location.replace("/library?panel=queue");
      return;
    } else if (view === "reader") {
      standaloneURL = null;
      await renderQueue();
    } else {
      standaloneURL = null;
      renderHome();
    }
  } catch (error) {
    notice(error.message);
  } finally {
    replayingHistory = false;
  }
});

(async () => {
  try {
    const pending = readItem("intel-pending-save");
    if (pending && pending.startsWith("?")) {
      removeItem("intel-pending-save");
      location.replace("/save" + pending);
      return;
    }
    const initialParams = new URLSearchParams(location.search);
    const view = initialParams.get("view");
    const open = initialParams.get("open");
    if (view === "builder" && !isTemporarySession()) {
      location.replace("/library?panel=queue");
      return;
    }
    const landingHome = !open && !view && !isTemporarySession();
    if (landingHome) {
      const [st, gym] = await Promise.all([
        api("/api/state"),
        api("/api/home-gym").catch(() => null),
      ]);
      state = st;
      gymData = gym;
    } else {
      state = await api("/api/state");
    }
    initAIAssist();
    setTimeout(saveForOffline, 4000);
    if (open) {
      standaloneURL = open;
      await renderQueue();
    } else if (view === "builder") renderSetup();
    else if (view === "reader") await renderQueue();
    else if (isTemporarySession()) {
      try {
        if (!sessionStorage.getItem(storagePrefix() + "intel-queue-draft") && state.queue) {
          draftQueue = state.queue.items.map((item) => item.url);
          saveQueueDraft();
        }
      } catch {}
      renderSetup();
    } else renderHome(true);
  } catch (error) {
    notice(error.message);
  }
})();
