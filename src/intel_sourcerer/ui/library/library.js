"use strict";
const userStoragePrefix = "user:" + document.querySelector('meta[name="user-id"]').content + ":";
const token = document.querySelector('meta[name="csrf-token"]').content;
async function libraryAPI(path, data) {
  const response = await fetch(
    path,
    data === undefined
      ? {}
      : {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
        body: JSON.stringify(data),
      },
  );
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || "Unable to complete this action.");
  return result;
}
async function importPlaylistJob(url) {
  const started = await libraryAPI("/api/jobs/playlist", { url });
  if (!started?.id) return started;
  for (; ;) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const job = await libraryAPI("/api/jobs?" + new URLSearchParams({ id: started.id }));
    if (job.state === "done") return job.result;
    if (job.state === "failed") throw new Error(job.error || "Playlist import failed.");
  }
}
const currentDeleteURL = () => document.querySelector("#lib-panel-details .lib-delete")?.dataset.url || "";
const dialog = document.getElementById("delete-dialog"),
  confirmDelete = document.getElementById("confirm-delete"),
  cancelDelete = document.getElementById("cancel-delete"),
  deleteError = document.getElementById("delete-error");
document.addEventListener("click", (event) => {
  if (!event.target.closest("#lib-panel-details .lib-delete")) return;
  deleteError.hidden = true;
  dialog.showModal();
});
cancelDelete.addEventListener("click", () => dialog.close());
dialog.addEventListener("cancel", (event) => {
  if (confirmDelete.disabled) event.preventDefault();
});
confirmDelete.addEventListener("click", async () => {
  confirmDelete.disabled = true;
  cancelDelete.disabled = true;
  deleteError.hidden = true;
  try {
    const deletedURL = currentDeleteURL();
    await libraryAPI("/api/delete-article", { url: deletedURL });
    try {
      localStorage.removeItem(userStoragePrefix + "intel-draft:" + deletedURL);
      const raw = JSON.parse(localStorage.getItem(userStoragePrefix + "intel-queue-draft") || "[]");
      if (Array.isArray(raw)) {
        const next = raw.filter((u) => u !== deletedURL);
        localStorage.setItem(userStoragePrefix + "intel-queue-draft", JSON.stringify(next));
      }
    } catch { }
    const url = new URL(location.href);
    url.searchParams.delete("selected");
    location.assign(url);
  } catch (error) {
    deleteError.textContent = error.message;
    deleteError.hidden = false;
    confirmDelete.disabled = false;
    cancelDelete.disabled = false;
  }
});
const drop = document.getElementById("library-drop"),
  addForm = document.getElementById("library-add-form"),
  addButton = document.getElementById("library-add"),
  articleURL = document.getElementById("library-url"),
  feedback = document.getElementById("library-feedback");
let importing = false;
const isPlaylist = (url) => /^https?:\/\/(www\.|m\.)?youtube\.com\/playlist\?/i.test(url.trim());
async function addLinks(text) {
  if (importing) return;
  const urls = [
    ...new Set((/^(?:file:\/\/|\/|~\/)/i.test(text.trim()) ? text.trim().split(/\r?\n/).filter(Boolean) : text.split(/\s+/).filter((url) => /^https?:\/\//i.test(url)))),
  ].slice(0, 20);
  feedback.hidden = false;
  if (!urls.length) {
    feedback.textContent = "Drop or paste an HTTP or HTTPS article link.";
    return;
  }
  importing = true;
  addButton.disabled = true;
  const cast = window.intelMagic?.casting(addButton);
  let added = 0,
    lastURL = null,
    lastTopic = null,
    errors = [];
  for (const url of urls) {
    feedback.textContent = isPlaylist(url)
      ? "Importing the playlist into a topic. Reading a long playlist can take a minute…"
      : `Fetching article ${added + errors.length + 1} of ${urls.length}…`;
    try {
      const result = isPlaylist(url)
        ? await importPlaylistJob(url)
        : await libraryAPI("/api/link", { url });
      lastURL = result.url;
      lastTopic = result.topic || lastTopic;
      added++;
    } catch (error) {
      errors.push(error.message);
    }
  }
  importing = false;
  addButton.disabled = false;
  if (added > 0) cast?.finish(true);
  else cast?.cancel();
  if (errors.length) {
    feedback.textContent = `${added} articles added. ${errors.join(" ")}`;
    if (added) {
      const refresh = document.createElement("a");
      refresh.href = "/library";
      refresh.textContent = " View library";
      feedback.append(refresh);
    }
    return;
  }
  if (lastTopic && urls.length === 1) {
    location.assign("/topic?" + new URLSearchParams({ id: lastTopic }));
    return;
  }
  const destination = new URL("/library", location.origin);
  destination.searchParams.set("selected", lastURL);
  location.assign(destination);
}
addForm.addEventListener("submit", (event) => {
  event.preventDefault();
  addLinks(articleURL.value);
});
drop.addEventListener("dragover", (event) => {
  event.preventDefault();
  drop.classList.add("drag-over");
});
drop.addEventListener("dragleave", (event) => {
  if (!drop.contains(event.relatedTarget)) drop.classList.remove("drag-over");
});
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  drop.classList.remove("drag-over");
  if (event.dataTransfer.files.length) {
    addFiles(Array.from(event.dataTransfer.files));
    return;
  }
  addLinks(
    event.dataTransfer.getData("text/uri-list") ||
    event.dataTransfer.getData("text/plain"),
  );
});

// Finder drops provide file bytes, rather than a usable local filesystem path.
async function addFiles(files) {
  addButton.disabled = true;
  const cast = window.intelMagic?.casting(addButton);
  const feedback = document.getElementById("library-feedback");
  feedback.hidden = false;
  let lastURL = null;
  try {
    for (const file of files.slice(0, 20)) {
      if (!/\.(pdf|mp3|m4a|ogg|wav|mp4|webm|eml|pptx)$/i.test(file.name)) throw new Error("Unsupported file type.");
      if (file.size > (/\.(pdf|eml|pptx)$/i.test(file.name) ? 20000000 : 100000000)) throw new Error("Documents are limited to 20 MB; audio/video to 100 MB.");
      feedback.textContent = `Importing ${file.name}…`;
      const response = await fetch("/api/file-upload", {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-CSRF-Token": token, "X-Filename": encodeURIComponent(file.name) },
        body: file,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "File import failed.");
      lastURL = result.url;
    }
    cast?.finish(true);
    feedback.textContent = "Files added.";
    if (lastURL) location.assign("/library?selected=" + encodeURIComponent(lastURL));
  } catch (error) {
    cast?.cancel();
    feedback.textContent = error.message;
  } finally {
    addButton.disabled = false;
  }
}

const currentRenameURL = () => document.querySelector('#lib-panel-details .lib-rename')?.dataset.url || '';
const renameDialog = document.getElementById('rename-dialog');
const renameInput = document.getElementById('article-new-title');
const renameError = document.getElementById('rename-error');
const renameSave = document.getElementById('save-rename');
document.addEventListener('click', (event) => {
  if (!event.target.closest('#lib-panel-details .lib-rename')) return;
  renameInput.value = document.querySelector('#lib-panel-details > h2').textContent;
  renameError.hidden = true;
  renameDialog.showModal();
  renameInput.focus();
  renameInput.select();
});
document.getElementById('cancel-rename').addEventListener('click', () => renameDialog.close());
document.getElementById('rename-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  renameSave.disabled = true;
  try {
    await libraryAPI('/api/rename-article', { url: currentRenameURL(), title: renameInput.value });
    location.reload();
  } catch (error) {
    renameError.textContent = error.message;
    renameError.hidden = false;
  } finally {
    renameSave.disabled = false;
  }
});

function mountDetailTopics() {
  const topicBox = document.getElementById("lib-topic-editor");
  if (!topicBox) return;
  const editor = mountTopicEditor(topicBox, {
    post: libraryAPI,
    onError(message) {
      feedback.hidden = false;
      feedback.textContent = message;
    },
    // Keeps the list in step without a reload, in the same short form the server draws.
    onChange(names) {
      const row = document.querySelector(".lib-item.selected .lib-row-tags");
      if (!row) return;
      row.replaceChildren(
        ...names.slice(0, 2).map((name) =>
          Object.assign(document.createElement("span"), { className: "lib-row-tag", textContent: name }),
        ),
      );
      if (names.length > 2) row.append(Object.assign(document.createElement("span"), { textContent: `+${names.length - 2}` }));
      row.hidden = !names.length;
    },
  });
  fetch("/api/article-topics?" + new URLSearchParams({ url: topicBox.dataset.url }))
    .then((response) => (response.ok ? response.json() : Promise.reject()))
    .then((data) => editor.show(topicBox.dataset.url, data))
    .catch(() => {
      topicBox.hidden = true;
    });
}
mountDetailTopics();
// Filter menus close on an outside click, and opening one closes siblings.
const outerFilterMenu = document.querySelector(".lib-more-filters");
const filterMenus = document.querySelectorAll(".lib-tag-menu, .lib-filter-menu");
for (const menu of filterMenus) {
  menu.addEventListener("toggle", () => {
    if (!menu.open) return;
    for (const other of filterMenus) if (other !== menu) other.open = false;
  });
}
document.addEventListener("click", (event) => {
  if (outerFilterMenu?.open && !outerFilterMenu.contains(event.target)) outerFilterMenu.open = false;
  for (const menu of filterMenus)
    if (menu.open && !menu.contains(event.target)) menu.open = false;
});
const articleList = document.querySelector(".lib-list"),
  selectedArticle = articleList?.querySelector(".lib-item.selected");
// The page reloads on selection, so bring a selected row further down the list back into view below the sticky header.
if (selectedArticle && selectedArticle.getBoundingClientRect().bottom > articleList.getBoundingClientRect().bottom) {
  const stickyHead = articleList.querySelector(".lib-list-head")?.getBoundingClientRect().height || 0;
  articleList.scrollTop +=
    selectedArticle.getBoundingClientRect().top - articleList.getBoundingClientRect().top - stickyHead - 4;
}
// On narrow screens the details sit below the list, so tapping an article jumps down to them.
if (matchMedia("(max-width: 899px)").matches)
  document.querySelectorAll(".lib-item").forEach((item) => {
    item.hash = "lib-details";
  });

// --- Single Server-Backed Reading Queue (shared live with Home & Reader via /api/queue) ---
const MAX_QUEUE_ITEMS = 10;
let activeQueue = null;
let draggingQueueURL = null;

function formatDuration(mins) {
  if (!mins || mins < 60) return `${mins || 0} min`;
  const h = Math.floor(mins / 60),
    m = mins % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

function queueItems() {
  return activeQueue && !activeQueue.finished && Array.isArray(activeQueue.items) ? activeQueue.items : [];
}

function queueURLs() {
  return queueItems().map((item) => item.url);
}

async function saveServerQueue(urls) {
  try {
    activeQueue = await libraryAPI("/api/queue-update", { urls });
    renderLibraryQueue();
  } catch (error) {
    feedback.hidden = false;
    feedback.textContent = error.message;
  }
}

function syncQueueButtons() {
  const queued = new Set(queueURLs());
  const full = queued.size >= MAX_QUEUE_ITEMS;
  // Move queued items visually to the top Reading Queue section by hiding duplicates in the list below.
  document.querySelectorAll(".lib-item-row[data-url]").forEach((row) => {
    row.hidden = queued.has(row.dataset.url);
  });
  document.querySelectorAll("[data-queue-url]").forEach((btn) => {
    const inQueue = queued.has(btn.dataset.queueUrl);
    btn.textContent = inQueue ? "Queued ✓" : "+ Queue";
    btn.setAttribute("aria-pressed", String(inQueue));
    btn.classList.toggle("is-queued", inQueue);
    btn.disabled = !inQueue && full;
    btn.title = !inQueue && full ? `Queue is full (${MAX_QUEUE_ITEMS} articles max)` : "";
  });
  const visibleRows = [...document.querySelectorAll(".lib-item-row[data-url]")];
  const visibleBtn = document.getElementById("lib-queue-visible");
  if (visibleBtn) {
    const remaining = visibleRows.filter((r) => !queued.has(r.dataset.url)).length;
    const slots = Math.max(0, MAX_QUEUE_ITEMS - queued.size);
    const canAdd = Math.min(remaining, slots);
    visibleBtn.hidden = !visibleRows.length;
    visibleBtn.disabled = canAdd === 0;
    visibleBtn.textContent = full
      ? `Queue full (${queued.size}/${MAX_QUEUE_ITEMS})`
      : canAdd
        ? `+ Queue next ${canAdd}`
        : "All shown queued ✓";
  }
}

async function placeQueueItem(url, beforeURL = null) {
  if (url === beforeURL) return;
  const urls = queueURLs();
  const from = urls.indexOf(url);
  if (from < 0) return;
  urls.splice(from, 1);
  const idx = beforeURL ? urls.indexOf(beforeURL) : -1;
  urls.splice(idx < 0 ? urls.length : idx, 0, url);
  await saveServerQueue(urls);
}

function renderLibraryQueue() {
  const items = queueItems();
  const panelEl = document.getElementById("lib-panel-queue");
  const countEl = document.getElementById("lib-queue-count");
  const metaEl = document.getElementById("lib-queue-meta");
  const listEl = document.getElementById("lib-queue-list");
  const startBtn = document.getElementById("lib-start-queue");
  if (!listEl) return;

  const resuming = Boolean(activeQueue && !activeQueue.finished && (activeQueue.cursor > 0 || activeQueue.read_count > 0));
  if (panelEl) panelEl.hidden = items.length === 0;
  if (countEl) countEl.textContent = `${items.length}/${MAX_QUEUE_ITEMS}`;
  if (startBtn) startBtn.textContent = resuming ? "Resume reading" : "Start reading";
  if (!items.length) {
    const topicForm = document.getElementById("lib-save-topic-form");
    if (topicForm) topicForm.hidden = true;
  }

  const totalMinutes = items.reduce((sum, item) => sum + (item.minutes || 5), 0);
  if (metaEl) {
    metaEl.textContent = `~${formatDuration(totalMinutes)} · Drag to reorder`;
  }

  const selectedURL =
    document.querySelector(".lib-detail-queue[data-queue-url]")?.dataset.queueUrl ||
    new URLSearchParams(location.search).get("selected") ||
    "";
  const narrow = matchMedia("(max-width: 899px)").matches;

  listEl.replaceChildren(
    ...items.map((item, idx) => {
      const url = item.url;
      const isCurrent = Boolean(resuming && activeQueue.cursor === idx);
      const isSelected = selectedURL === url;
      const li = document.createElement("li");
      li.className = "lib-queue-item" + (isSelected ? " selected" : isCurrent ? " is-current" : "");
      li.draggable = true;
      li.dataset.url = url;
      li.addEventListener("dragstart", (event) => {
        draggingQueueURL = url;
        li.classList.add("dragging");
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", url);
      });
      li.addEventListener("dragend", () => {
        draggingQueueURL = null;
        li.classList.remove("dragging");
      });
      li.addEventListener("dragover", (event) => {
        if (!draggingQueueURL) return;
        event.preventDefault();
        li.classList.add("drag-target");
      });
      li.addEventListener("dragleave", () => li.classList.remove("drag-target"));
      li.addEventListener("drop", (event) => {
        if (!draggingQueueURL) return;
        event.preventDefault();
        li.classList.remove("drag-target");
        placeQueueItem(draggingQueueURL, url);
      });

      const handle = document.createElement("span");
      handle.className = "lib-queue-handle";
      handle.setAttribute("aria-hidden", "true");
      handle.textContent = "≡";

      const selectURL = new URL(location.href);
      selectURL.searchParams.set("selected", url);
      selectURL.searchParams.delete("panel");
      if (narrow) selectURL.hash = "lib-details";

      const copy = document.createElement("a");
      copy.className = "lib-queue-copy";
      copy.href = selectURL.pathname + selectURL.search + selectURL.hash;
      const titleEl = document.createElement("span");
      titleEl.className = "lib-queue-title";
      titleEl.textContent = item.title || url;
      const sub = document.createElement("span");
      sub.className = "lib-queue-sub muted";
      sub.textContent = [
        isCurrent ? "Up next" : item.is_read ? "Read" : "",
        item.publisher,
        item.minutes ? `~${formatDuration(item.minutes)}` : "",
      ].filter(Boolean).join(" · ");
      copy.append(titleEl, sub);

      const controls = document.createElement("div");
      controls.className = "lib-queue-controls";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Remove ${item.title} from queue`);
      remove.onclick = async () => {
        await window.intelMagic?.banish(li, { tone: "arcane" });
        await saveServerQueue(queueURLs().filter((u) => u !== url));
      };
      controls.append(remove);

      li.append(handle, copy, controls);
      return li;
    }),
  );
  syncQueueButtons();
}

async function toggleQueueURL(url, triggerEl = null) {
  if (!url) return;
  const urls = queueURLs();
  if (urls.includes(url)) {
    await saveServerQueue(urls.filter((u) => u !== url));
  } else {
    if (urls.length >= MAX_QUEUE_ITEMS) {
      feedback.hidden = false;
      feedback.textContent = `Queue is full (${MAX_QUEUE_ITEMS} articles max). Finish or remove one to add more.`;
      return;
    }
    const queueTarget = () => {
      const panel = document.getElementById("lib-panel-queue");
      return (panel && !panel.hidden ? document.getElementById("lib-queue-count") || panel : null) || document.querySelector(".lib-list-head");
    };
    window.intelMagic?.fly(triggerEl, queueTarget, { tone: "arcane" });
    await saveServerQueue([...urls, url]);
  }
}

function startReadingQueue() {
  if (!queueItems().length) return;
  location.assign("/?view=reader");
}

document.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-queue-url]");
  if (btn) toggleQueueURL(btn.dataset.queueUrl, btn);
});
document.getElementById("lib-queue-visible")?.addEventListener("click", (event) => {
  const trigger = event.currentTarget;
  const urls = queueURLs();
  const existing = new Set(urls);
  let added = 0;
  for (const row of document.querySelectorAll(".lib-item-row[data-url]")) {
    const url = row.dataset.url;
    if (!existing.has(url) && urls.length < MAX_QUEUE_ITEMS) {
      urls.push(url);
      existing.add(url);
      added++;
    }
  }
  if (added > 0) {
    const queueTarget = () => document.getElementById("lib-queue-count") || document.getElementById("lib-panel-queue");
    window.intelMagic?.fly(trigger, queueTarget, { tone: "arcane", delayMs: 0 });
    if (added > 1) window.intelMagic?.fly(trigger, queueTarget, { tone: "arcane", delayMs: 90 });
  }
  saveServerQueue(urls);
});
document.getElementById("lib-start-queue")?.addEventListener("click", startReadingQueue);
document.getElementById("lib-clear-queue-top")?.addEventListener("click", async () => {
  const listEl = document.getElementById("lib-queue-list");
  await window.intelMagic?.banish(listEl, { tone: "clear" });
  listEl?.removeAttribute("data-banishing");
  await saveServerQueue([]);
});

const libSaveTopicOpen = document.getElementById("lib-save-topic-open");
const libSaveTopicForm = document.getElementById("lib-save-topic-form");
const libSaveTopicName = document.getElementById("lib-save-topic-name");
const libSaveTopicStatus = document.getElementById("lib-save-topic-status");
const libSaveTopicLink = document.getElementById("lib-save-topic-link");
const libTopicNames = document.getElementById("lib-topic-names");
let topicsLoaded = false;

libSaveTopicOpen?.addEventListener("click", async () => {
  libSaveTopicForm.hidden = !libSaveTopicForm.hidden;
  libSaveTopicStatus.hidden = true;
  if (!libSaveTopicForm.hidden) {
    libSaveTopicName.focus();
    if (!topicsLoaded && libTopicNames) {
      topicsLoaded = true;
      try {
        const res = await libraryAPI("/api/topics");
        libTopicNames.replaceChildren(...(res.topics || []).map((t) => new Option(t.name, t.name)));
      } catch {}
    }
  }
});
libSaveTopicForm?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = libSaveTopicName.value.trim();
  const urls = queueURLs();
  if (!name || !urls.length) return;
  const submit = libSaveTopicForm.querySelector("button[type=submit]");
  submit.disabled = true;
  try {
    const saved = await libraryAPI("/api/topic-from-articles", { name, urls: urls.slice(0, 100) });
    libSaveTopicName.value = "";
    libSaveTopicForm.hidden = true;
    libSaveTopicStatus.hidden = false;
    libSaveTopicStatus.firstElementChild.textContent = `Saved ${saved.added} ${saved.added === 1 ? "article" : "articles"} to `;
    libSaveTopicLink.href = "/topic?" + new URLSearchParams({ id: saved.id });
    libSaveTopicLink.textContent = saved.name;
  } catch (error) {
    feedback.hidden = false;
    feedback.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

renderLibraryQueue();
libraryAPI("/api/queue")
  .then((data) => {
    activeQueue = data.queue || null;
    renderLibraryQueue();
  })
  .catch(() => {});

// Choosing an article fills the right-hand panel in place instead of reloading the page.
// The server still renders the panel, so it matches a full page load exactly.
const detailPanel = document.getElementById("lib-panel-details");
// Hide any remote article banner image that fails to load so no broken-image icon appears.
detailPanel?.addEventListener(
  "error",
  (event) => {
    if (event.target instanceof HTMLImageElement && event.target.classList.contains("lib-preview-banner")) {
      event.target.hidden = true;
    }
  },
  true,
);
detailPanel?.querySelectorAll("img.lib-preview-banner").forEach((img) => {
  if (img.complete && img.naturalWidth === 0) img.hidden = true;
});
let detailRequest = 0;
async function showArticleDetail(href, { push = true } = {}) {
  const request = ++detailRequest;
  const target = new URL(href, location.href);
  detailPanel?.setAttribute("aria-busy", "true");
  try {
    const response = await fetch(target.pathname + target.search, { headers: { Accept: "text/html" } });
    if (!response.ok || response.redirected) throw new Error("Couldn't load that article.");
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const fresh = page.getElementById("lib-panel-details");
    if (!fresh) throw new Error("Couldn't load that article.");
    if (request !== detailRequest) return;
    detailPanel.replaceChildren(...fresh.childNodes);
    const selected = target.searchParams.get("selected") || "";
    document.querySelectorAll(".lib-item-row[data-url]").forEach((row) => {
      const on = row.dataset.url === selected;
      row.classList.toggle("selected", on);
      const link = row.querySelector(".lib-item");
      link?.classList.toggle("selected", on);
      if (on) link?.setAttribute("aria-current", "true");
      else link?.removeAttribute("aria-current");
    });
    if (push) history.pushState({ libraryDetail: true }, "", target.pathname + target.search);
    mountDetailTopics();
    // The queue panel highlights the article that's open, so redraw it too.
    renderLibraryQueue();
    if (matchMedia("(max-width: 899px)").matches)
      document.getElementById("lib-details")?.scrollIntoView({ block: "start", behavior: "smooth" });
    else detailPanel.scrollTop = 0;
  } catch {
    // Fall back to the old behaviour rather than leave the panel stale.
    location.assign(target.href);
  } finally {
    if (request === detailRequest) detailPanel?.removeAttribute("aria-busy");
  }
}
document.addEventListener("click", (event) => {
  const link = event.target.closest("a.lib-item");
  if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
  event.preventDefault();
  showArticleDetail(link.href);
});
window.addEventListener("popstate", () => {
  if (new URLSearchParams(location.search).has("selected")) showArticleDetail(location.href, { push: false });
  else location.reload();
});
