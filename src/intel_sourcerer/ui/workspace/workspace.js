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
let draftQueue = [],
  draggingURL = null;
function saveQueueDraft() {
  writeJSON(storagePrefix() + "intel-queue-draft", draftQueue);
}
function renderSetup() {
  show("setup");
  const draft = readJSON(storagePrefix() + "intel-queue-draft");
  if (Array.isArray(draft))
    draftQueue = [
      ...new Set(
        draft.filter((url) => state.articles.some((a) => a.url === url)),
      ),
    ].slice(0, 100);
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
function renderHome() {
  currentArticle = null;
  noteModel = null;
  show("home");
  const q = state.queue,
    active = q && !q.finished && q.items.length;
  $("home-current").replaceChildren();
  if (active) {
    const item = q.items[q.cursor],
      heading = document.createElement("h2"),
      meta = document.createElement("p");
    heading.textContent = item.title;
    meta.className = "muted";
    meta.textContent = `${item.publisher} · ${dateLabel(item.published_at)}`;
    const chip = formatChip(state.articles.find((article) => article.url === item.url));
    if (chip) meta.prepend(chip);
    $("home-current").append(heading, meta);
  } else {
    const heading = document.createElement("h2");
    heading.textContent = q
      ? "Your last queue is complete."
      : "Your first reading session starts here.";
    $("home-current").append(heading);
  }
  $("home-progress-text").textContent = q
    ? `${q.read_count} of ${q.items.length} articles read · ${Math.round((q.read_count / q.items.length) * 100)}%`
    : "Choose a few articles to start your queue.";
  $("home-progress").hidden = !q;
  $("home-progress").max = q?.items.length || 1;
  $("home-progress").value = q?.read_count || 0;
  $("resume-reading").hidden = !active;
  renderTriage();
  $("recent-notes").replaceChildren();
  $("home-library-count").textContent = `${state.articles.length} saved articles`;
  $("home-in-progress").hidden = !state.in_progress;
  $("home-in-progress").firstElementChild.textContent = `${state.in_progress} ${state.in_progress === 1 ? "article" : "articles"} in progress`;
  const noted = state.articles
    .filter((a) => a.has_notes)
    .sort((a, b) =>
      (b.notes_updated_at || "").localeCompare(a.notes_updated_at || ""),
    )
    .slice(0, 5);
  if (!noted.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent =
      "Your saved takeaways will appear here as you read and take notes.";
    $("recent-notes").append(empty);
  }
  for (const article of noted) {
    const row = document.createElement("article");
    row.className = "home-note";
    const source = document.createElement("div"),
      publisher = document.createElement("strong"),
      date = document.createElement("p"),
      copy = document.createElement("div"),
      link = document.createElement("button"),
      preview = document.createElement("p");
    publisher.textContent = article.publisher;
    date.textContent = dateLabel(article.published_at);
    date.className = "muted";
    const chip = formatChip(article);
    if (chip) date.prepend(chip);
    source.append(publisher, date);
    link.className = "note-article-link";
    link.textContent = article.title;
    link.onclick = async () => {
      link.disabled = true;
      try {
        standaloneURL = article.url;
        await renderQueue();
      } catch (error) {
        notice(error.message);
        link.disabled = false;
      }
    };
    preview.textContent = article.note_preview;
    preview.className = "muted";
    copy.append(link, preview);
    row.append(source, copy);
    $("recent-notes").append(row);
  }
}
let triageUndo = null;
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
  renderTriage();
}
function renderTriage() {
  // Temporary sessions only hold what was just supplied, so there's nothing to sort.
  const temporary = isTemporarySession(),
    queued = new Set(
      state.queue && !state.queue.finished ? state.queue.items.map((item) => item.url) : [],
    ),
    pending = temporary
      ? []
      : state.articles.filter(
          (a) => !a.is_read && !a.triage && !a.has_notes && !queued.has(a.url),
        ),
    kept = temporary
      ? []
      : state.articles.filter((a) => a.triage === "keep" && !a.is_read && !queued.has(a.url));
  if (state.week) {
    $("home-week").hidden = false;
    $("home-week").textContent = `Last 7 days: ${state.week.saved} saved · ${state.week.read} read`;
  }
  $("home-kept").hidden = !kept.length;
  $("kept-count").textContent = `· ${kept.length}`;
  $("kept-titles").textContent =
    kept.slice(0, 3).map((a) => a.title).join(" · ") +
    (kept.length > 3 ? ` · and ${kept.length - 3} more` : "");
  $("kept-warning").hidden = !queued.size;
  $("kept-start").onclick = async () => {
    $("kept-start").disabled = true;
    try {
      standaloneURL = null;
      state.queue = await api("/api/queue", { urls: kept.slice(0, 20).map((a) => a.url) });
      await renderQueue();
    } catch (error) {
      notice(error.message);
    } finally {
      $("kept-start").disabled = false;
    }
  };
  $("home-triage").hidden = !pending.length && !triageUndo;
  $("triage-count").textContent = `· ${pending.length}`;
  $("triage-undo").hidden = !triageUndo;
  $("triage-undo").onclick = () => decideTriage(triageUndo, null, false);
  const cutoff = Date.now() - 30 * 86400000,
    old = pending.filter((a) => new Date(a.published_at).getTime() < cutoff);
  $("triage-old").hidden = !old.length;
  $("triage-old").textContent = `Skip ${old.length} older than 30 days`;
  $("triage-old").onclick = () => decideTriage(old.map((a) => a.url), "skip");
  $("triage-list").replaceChildren(
    ...pending.slice(0, 5).map((article) => {
      const row = document.createElement("article"),
        copy = document.createElement("div"),
        title = document.createElement("button"),
        meta = document.createElement("p"),
        actions = document.createElement("div"),
        keep = document.createElement("button"),
        skip = document.createElement("button");
      row.className = "triage-row";
      title.className = "note-article-link";
      title.textContent = article.title;
      title.onclick = () => openStandalone(article.url);
      meta.className = "muted";
      meta.textContent = `${article.publisher} · ${dateLabel(article.published_at)}`;
      const chip = formatChip(article);
      if (chip) meta.prepend(chip);
      copy.append(title, meta);
      keep.textContent = "Keep";
      keep.setAttribute("aria-label", "Keep: " + article.title);
      keep.onclick = () => decideTriage([article.url], "keep");
      skip.textContent = "Skip";
      skip.setAttribute("aria-label", "Skip: " + article.title);
      skip.onclick = () => decideTriage([article.url], "skip");
      actions.append(keep, skip);
      row.append(copy, actions);
      return row;
    }),
  );
}
$("home-build").onclick = () => renderSetup();
// Temporary sessions only hold what was supplied, so there are no sources to check.
$("home-find").hidden = isTemporarySession();
// A new tab, so leaving to look at sources doesn't lose the result of the check.
function sourcesLink(text) {
  const link = document.createElement("a");
  link.href = "/sources";
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = text;
  return link;
}
$("home-find").onclick = async () => {
  const button = $("home-find"),
    status = $("home-find-status");
  button.disabled = true;
  status.hidden = false;
  status.replaceChildren("Checking your ", sourcesLink("sources"), " for the last 30 days. This can take a minute.");
  notice("");
  try {
    const report = await runBackgroundJob("/api/jobs/discover", { days: 30 }),
      added = report.sources.reduce((total, source) => total + source.inserted, 0);
    state = await api("/api/state");
    if (!$("home").hidden) renderHome();
    status.replaceChildren(
      added ? `${added} new ${added === 1 ? "article" : "articles"} added to To triage.` : "No new articles.",
    );
    if (report.errors.length) status.append(" Some ", sourcesLink("sources"), " had problems.");
    if (report.errors.length || report.paused.length) notice([...report.errors, ...report.paused].join(" · "));
  } catch (error) {
    status.textContent = "Couldn't check for new articles. Your saved articles are still here.";
    notice(error.message);
  } finally {
    button.disabled = false;
  }
};
$("resume-reading").onclick = () => {
  standaloneURL = null;
  renderQueue().catch((error) => notice(error.message));
};
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
  if (draftQueue.length >= 100) {
    notice("Choose up to 100 articles per queue.");
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
  if (!queued && draftQueue.length >= 100) {
    notice("Choose up to 100 articles per queue.");
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
    const room = 100 - draftQueue.length;
    if (addable.length > room) notice("A queue can hold up to 100 articles.");
    draftQueue.push(...addable.slice(0, room).map((a) => a.url));
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

function renderIOCs() {
  renderArticleIOCs(currentArticle, {
    bodyEl: $("article-body"),
    setPhoneView,
    notice,
  });
}

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
    renderSetup();
    return;
  }
  if (q.finished) {
    currentArticle = null;
    noteModel = null;
    renderSetup();
    $("complete").hidden = false;
    $("setup").inert = true;
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
  loadRelated(article);
  loadTags(article);
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
          renderSetup();
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
const tagEditor = mountTagEditor($("tags-editor"), { post: api, onError: notice });
async function loadTags(article) {
  $("tags-panel").hidden = true;
  try {
    const data = await api("/api/tags?" + new URLSearchParams({ url: article.url }));
    if (currentArticle !== article) return;
    tagEditor.show(article.url, data);
    $("tags-panel").hidden = false;
  } catch {
    // Offline and temporary sessions have no tag store, so the panel stays hidden.
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
    $("article-type").focus();
  } else if (event.key === "Tab") {
    event.preventDefault();
    $("new-queue").focus();
  }
});
$("new-queue").onclick = async () => {
  try {
    state = await api("/api/state");
    renderSetup();
  } catch (error) {
    notice(error.message);
  }
};
// Keeps what you're likely to read next on this device, in case the connection drops.
function saveForOffline() {
  if (!navigator.serviceWorker?.controller || !navigator.onLine || !state) return;
  const upcoming = [
    ...(state.queue && !state.queue.finished ? state.queue.items.map((item) => item.url) : []),
    ...state.articles.filter((a) => a.triage === "keep" && !a.is_read).map((a) => a.url),
    ...state.articles.filter((a) => !a.is_read && !a.triage).map((a) => a.url),
  ];
  (async () => {
    for (const url of [...new Set(upcoming)].slice(0, 30)) {
      if (!navigator.onLine) return;
      await fetch("/api/article?" + new URLSearchParams({ url })).catch(() => {});
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

// Expose workspace hooks needed by companion classic scripts (notes-editor.js, focus.js, temporary.js).
Object.defineProperty(window, "busy", { get: () => busy, configurable: true });
Object.defineProperty(window, "currentArticle", { get: () => currentArticle, configurable: true });
window.notice = notice;
window.saveNow = saveNow;
window.focusMinutes = focusMinutes;
window.readerMetadata = readerMetadata;

(async () => {
  try {
    const pending = readItem("intel-pending-save");
    if (pending && pending.startsWith("?")) {
      removeItem("intel-pending-save");
      location.replace("/save" + pending);
      return;
    }
    state = await api("/api/state");
    setTimeout(saveForOffline, 2000);
    const view = new URLSearchParams(location.search).get("view");
    const open = new URLSearchParams(location.search).get("open");
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
    } else renderHome();
  } catch (error) {
    notice(error.message);
  }
})();
