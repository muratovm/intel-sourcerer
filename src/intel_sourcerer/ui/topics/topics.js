"use strict";
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="csrf-token"]').content;
const userStoragePrefix = "user:" + document.querySelector('meta[name="user-id"]').content + ":";
// The shared notes editor checks this before changing anything. Nothing locks notes on this page.
const busy = false;
function notice(message) {
  const box = $("topic-message");
  box.textContent = message;
  box.hidden = !message;
}
async function api(path, data) {
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
  if (!response.ok) throw new Error(result.error || "Unable to save. Please try again.");
  return result;
}
function topicPage(id) {
  return "/topic?" + new URLSearchParams({ id });
}

const newTopicDialog = $("new-topic-dialog");
if (newTopicDialog) {
  $("new-topic-open").addEventListener("click", () => {
    $("new-topic").value = "";
    $("new-topic-error").hidden = true;
    newTopicDialog.showModal();
    $("new-topic").focus();
  });
  $("cancel-new-topic").addEventListener("click", () => newTopicDialog.close());
  $("new-topic-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      location.assign(topicPage((await api("/api/topics", { name: $("new-topic").value })).id));
    } catch (error) {
      $("new-topic-error").textContent = error.message;
      $("new-topic-error").hidden = false;
    }
  });
}

function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}
function topicLinkTo(topic, className) {
  const link = make("a", className, topic.name);
  link.href = topicPage(topic.id);
  return link;
}
function renderOverview(data) {
  // What's waiting isn't known until now, so the rows take their final order here.
  const rows = [...document.querySelectorAll(".topic-row")];
  for (const row of rows) {
    const count = data.belong[row.dataset.id] || 0,
      label = row.querySelector(".topic-belong");
    label.textContent = `${count} might belong`;
    label.hidden = !count;
    row.dataset.waitingAll = Number(row.dataset.waiting) + count;
    row.dataset.belong = count;
  }
  rows
    .sort(
      (a, b) =>
        (Number(b.dataset.waitingAll) > 0) - (Number(a.dataset.waitingAll) > 0) ||
        new Date(b.dataset.latest || 0) - new Date(a.dataset.latest || 0),
    )
    .forEach((row) => $("topic-rows").append(row));
  $("topics-stats").hidden = !data.articles;
  $("stat-figures").replaceChildren(
    ...[
      [data.articles, `${data.articles === 1 ? "article" : "articles"} in ${plural(data.topics, "topic")}`],
      [data.unread, "unread"],
      [data.open_questions, data.open_questions === 1 ? "open question" : "open questions"],
      [data.might_belong, "might belong"],
    ].map(([value, label]) => {
      const item = make("div");
      item.append(make("strong", "", value), make("span", "muted", label));
      return item;
    }),
  );
  const most = Math.max(1, ...data.weeks.map((week) => week.reports)),
    peak = data.weeks.reduce((best, week) => (week.reports > best.reports ? week : best), data.weeks[0]);
  $("stat-weeks").hidden = !data.weeks_total;
  $("stat-weeks-total").textContent = `${data.weeks_total} in the last ${data.weeks.length} weeks`;
  $("stat-week-first").textContent = data.weeks[0].start;
  $("stat-week-last").textContent = data.weeks[data.weeks.length - 1].start;
  $("stat-week-bars").setAttribute(
    "aria-label",
    `Reports per week over ${data.weeks.length} weeks. The busiest was the week of ${peak.start}, with ${plural(peak.reports, "report")}.`,
  );
  $("stat-week-bars").replaceChildren(
    ...data.weeks.map((week) => {
      const column = make("div", "stat-week"),
        bar = make("div", "stat-week-bar");
      column.title = `Week of ${week.start}: ${plural(week.reports, "report")}`;
      bar.style.height = week.reports ? `${Math.max(4, (week.reports / most) * 88)}px` : "0";
      if (week === peak && week.reports) column.append(make("span", "stat-peak", week.reports));
      column.append(bar);
      return column;
    }),
  );
  const busiest = Math.max(1, ...data.busiest.map((item) => item.reports));
  $("stat-busiest").hidden = !data.busiest.length;
  $("stat-busiest-list").replaceChildren(
    ...data.busiest.map((item) => {
      const row = make("a", "stat-bar-row"),
        track = make("span", "stat-bar-track"),
        bar = make("span", "stat-bar");
      row.href = topicPage(item.id);
      row.title = `${item.name}: ${plural(item.reports, "report")} in the last 30 days`;
      bar.style.width = `${(item.reports / busiest) * 100}%`;
      track.append(bar);
      row.append(make("span", "stat-bar-name", item.name), track, make("strong", "", item.reports));
      return row;
    }),
  );
  $("stat-shared").hidden = !data.shared.length;
  $("stat-shared-list").replaceChildren(
    ...data.shared.map((item) => {
      const row = make("div", "stat-row"),
        links = make("span", "stat-row-topics");
      item.topics.forEach((topic, index) => {
        if (index) links.append(", ");
        links.append(topicLinkTo(topic));
      });
      row.append(make("code", "", item.value), links);
      return row;
    }),
  );
  $("stat-single").hidden = !data.single_publisher.length;
  $("stat-single-list").replaceChildren(
    ...data.single_publisher.map((topic) => {
      const row = make("div", "stat-row");
      row.append(topicLinkTo(topic), make("span", "muted", plural(topic.articles, "article")));
      return row;
    }),
  );
  $("stat-status").hidden = data.complete;
  $("stat-status").textContent = "Still reading the library. These numbers may grow.";
}
if ($("topics-stats")) {
  (async () => {
    try {
      for (;;) {
        const data = await api("/api/topics-overview");
        renderOverview(data);
        if (data.complete) return;
      }
    } catch {
      // The list still works without the statistics.
    }
  })();
}

const page = $("topic-page");
if (page) {
  const topicId = page.dataset.id,
    draftKey = userStoragePrefix + "intel-topic-draft:" + topicId;
  let revision = Number(page.dataset.revision),
    savedText = notesText(),
    saveTimer = null,
    saving = Promise.resolve();

  function stashDraft() {
    try {
      localStorage.setItem(draftKey, JSON.stringify({ text: notesText(), revision }));
    } catch {}
  }
  function clearDraft() {
    try {
      localStorage.removeItem(draftKey);
    } catch {}
  }
  function saveNow() {
    clearTimeout(saveTimer);
    const text = notesText();
    saving = saving
      .catch(() => {})
      .then(async () => {
        if (text === savedText) return;
        $("save-status").textContent = "Saving…";
        try {
          const result = await api("/api/topic-notes", { id: topicId, text, revision });
          revision = result.revision;
          savedText = text;
          if (notesText() === text) {
            $("save-status").textContent = "Saved";
            clearDraft();
          } else {
            $("save-status").textContent = "Unsaved changes";
            stashDraft();
          }
        } catch (error) {
          $("save-status").textContent = "Not saved. Retry by editing";
          stashDraft();
          throw error;
        }
      });
    return saving;
  }
  // Leaving the page by a link or reload shouldn't drop what was just typed.
  async function saveThen(action) {
    try {
      await saveNow();
      await action();
    } catch (error) {
      notice(error.message);
    }
  }
  noteBox.addEventListener("input", () => {
    stashDraft();
    $("save-status").textContent = "Unsaved changes";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => saveNow().catch((error) => notice(error.message)), 600);
  });
  window.addEventListener("pagehide", () => {
    if (notesText() !== savedText) stashDraft();
  });
  try {
    const draft = JSON.parse(localStorage.getItem(draftKey) || "null");
    if (draft && draft.revision === revision && draft.text !== savedText) {
      loadNotes(draft.text, "html");
      $("save-status").textContent = "Unsaved changes";
      saveNow().catch((error) => notice(error.message));
    } else clearDraft();
  } catch {}

  $("topic-status").addEventListener("change", async (event) => {
    try {
      await api("/api/topic-status", { id: topicId, status: event.target.value });
    } catch (error) {
      notice(error.message);
    }
  });

  $("read-unread").addEventListener("click", (event) => {
    const urls = event.target.dataset.urls
      .split(" ")
      .filter(Boolean)
      .map((link) => new URLSearchParams(link.split("?")[1]).get("open"));
    saveThen(async () => {
      await api("/api/queue", { urls: urls.slice(0, 100) });
      location.assign("/?view=reader");
    });
  });

  async function addArticle(url) {
    await api("/api/topic-article", { id: topicId, url });
    location.reload();
  }
  $("add-article-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("add-article"),
      value = input.value.trim();
    if (!value) return;
    const match = [...$("library-articles").options].find((option) => option.value === value || option.dataset.url === value);
    if (!match && !/^https?:\/\//i.test(value)) {
      notice("Choose an article from the list, or paste a full URL.");
      return;
    }
    notice("");
    saveThen(async () => {
      if (match) await addArticle(match.dataset.url);
      else {
        $("save-status").textContent = "Fetching article…";
        await addArticle((await api("/api/link", { url: value })).url);
      }
    });
  });
  document.querySelectorAll("[data-remove]").forEach((button) => {
    button.addEventListener("click", () =>
      saveThen(async () => {
        await api("/api/topic-article-delete", { id: topicId, url: button.dataset.remove });
        location.reload();
      }),
    );
  });

  const citeMenu = $("cite-menu");
  citeMenu.querySelectorAll("[data-cite]").forEach((button) => {
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => {
      const link = document.createElement("a");
      link.setAttribute("href", button.dataset.cite);
      link.textContent = button.dataset.label;
      focusNotes();
      document.execCommand("insertHTML", false, link.outerHTML + "&nbsp;");
      citeMenu.open = false;
      noteChanged();
    });
  });
  document.addEventListener("click", (event) => {
    if (citeMenu.open && !citeMenu.contains(event.target)) citeMenu.open = false;
  });

  const deleteDialog = $("delete-topic-dialog");
  $("delete-topic").addEventListener("click", () => {
    $("delete-topic-error").hidden = true;
    deleteDialog.showModal();
  });
  $("cancel-delete-topic").addEventListener("click", () => deleteDialog.close());
  $("confirm-delete-topic").addEventListener("click", async () => {
    try {
      clearTimeout(saveTimer);
      await api("/api/topic-delete", { id: topicId });
      clearDraft();
      // Saving a draft on the way out would fail now the topic is gone.
      savedText = notesText();
      location.assign("/topics");
    } catch (error) {
      $("delete-topic-error").textContent = error.message;
      $("delete-topic-error").hidden = false;
    }
  });
  const renameDialog = $("rename-topic-dialog");
  $("rename-topic").addEventListener("click", () => {
    $("rename-topic-error").hidden = true;
    renameDialog.showModal();
  });
  $("cancel-rename-topic").addEventListener("click", () => renameDialog.close());
  $("rename-topic-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await saveNow();
      await api("/api/topic-rename", { id: topicId, name: $("topic-new-name").value });
      location.reload();
    } catch (error) {
      $("rename-topic-error").textContent = error.message;
      $("rename-topic-error").hidden = false;
    }
  });

  function renderInsights(data) {
    const count = data.total_indicators;
    $("topic-indicator-count").textContent = count ? String(count) : "";
    $("topic-indicator-list").replaceChildren(
      ...data.indicators.map((item) => {
        const row = document.createElement("li"),
          value = document.createElement("code"),
          kind = document.createElement("span"),
          articles = document.createElement("span");
        value.textContent = item.value;
        kind.className = "ioc-name";
        kind.textContent = item.kind;
        articles.className = "muted";
        articles.textContent = `${item.articles} of ${data.total_articles}`;
        const label = document.createElement("span");
        label.append(value, kind);
        row.append(label, articles);
        return row;
      }),
    );
    $("topic-indicator-status").textContent = !data.complete
      ? "Still reading the library. More may appear."
      : count > data.indicators.length
        ? `Showing the ${data.indicators.length} found in the most articles.`
        : "";
    $("topic-indicators").hidden = !count && data.complete;
    $("topic-suggested-section").hidden = !data.suggested.length;
    $("topic-suggested-list").replaceChildren(
      ...data.suggested.map((item) => {
        const row = document.createElement("div"),
          copy = document.createElement("div"),
          link = document.createElement("a"),
          why = document.createElement("p"),
          add = document.createElement("button"),
          dismiss = document.createElement("button");
        row.className = "topic-suggestion";
        link.className = "topic-article-title";
        link.href = "/?" + new URLSearchParams({ open: item.url });
        link.textContent = item.title;
        why.className = "muted";
        why.textContent = `${item.publisher} · ${item.why}`;
        add.type = "button";
        add.textContent = "Add";
        add.setAttribute("aria-label", `Add ${item.title} to this topic`);
        add.onclick = () => saveThen(() => addArticle(item.url));
        dismiss.type = "button";
        dismiss.className = "topic-dismiss";
        dismiss.textContent = "Dismiss";
        dismiss.setAttribute("aria-label", `Stop suggesting ${item.title} for this topic`);
        dismiss.onclick = async () => {
          try {
            await api("/api/topic-dismiss", { id: topicId, url: item.url });
            renderInsights(await api("/api/topic-insights?" + new URLSearchParams({ id: topicId })));
          } catch (error) {
            notice(error.message);
          }
        };
        copy.append(link, why);
        row.append(copy, dismiss, add);
        return row;
      }),
    );
  }
  (async () => {
    try {
      for (;;) {
        const data = await api("/api/topic-insights?" + new URLSearchParams({ id: topicId }));
        renderInsights(data);
        if (data.complete) return;
      }
    } catch {
      // The notes and articles still work without this.
    }
  })();
}
