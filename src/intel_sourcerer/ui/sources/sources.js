"use strict";
const token = document.querySelector('meta[name="csrf-token"]').content;
const feedback = document.getElementById("source-feedback");

function showFeedback(message) {
  if (!feedback) return;
  feedback.textContent = message;
  feedback.hidden = !message;
}

async function sourceAPI(path, data) {
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
  if (!response.ok) throw new Error(result.error || "Unable to update source.");
  return result;
}

document.querySelectorAll("[data-source]").forEach(
  (input) =>
    (input.onchange = async () => {
      const enabled = input.checked;
      input.disabled = true;
      try {
        await sourceAPI("/api/source-status", { id: input.dataset.source, enabled });
        location.reload();
      } catch (error) {
        input.checked = !enabled;
        input.disabled = false;
        showFeedback(error.message);
      }
    }),
);

document.querySelectorAll("[data-remove-source]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await sourceAPI("/api/source-delete", { id: button.dataset.removeSource });
      location.reload();
    } catch (error) {
      button.disabled = false;
      showFeedback(error.message);
    }
  });
});

document.querySelectorAll("[data-check-source]").forEach((button) => {
  button.addEventListener("click", async () => {
    const sourceId = button.dataset.checkSource;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Checking…";
    showFeedback(`Checking ${sourceId}…`);
    try {
      const started = await sourceAPI("/api/jobs/check-source", { id: sourceId, days: 30 });
      let report = started;
      if (started?.id) {
        for (;;) {
          await new Promise((resolve) => setTimeout(resolve, 1000));
          const job = await sourceAPI("/api/jobs?" + new URLSearchParams({ id: started.id }));
          if (job.state === "done") {
            report = job.result;
            break;
          }
          if (job.state === "failed") throw new Error(job.error || "Source check failed.");
        }
      }
      const inserted = (report?.sources || []).reduce((sum, item) => sum + (item.inserted || 0), 0);
      const issues = [...(report?.errors || []), ...(report?.paused || [])];
      showFeedback(
        `${sourceId}: ${inserted} new ${inserted === 1 ? "article" : "articles"}.${issues.length ? " " + issues.join(" · ") : ""}`,
      );
      if (inserted > 0) setTimeout(() => location.reload(), 1200);
    } catch (error) {
      showFeedback(error.message);
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  });
});

document.querySelectorAll("[data-relevance]").forEach((select) => {
  select.addEventListener("change", async () => {
    const relevance = select.value;
    select.disabled = true;
    try {
      await sourceAPI("/api/source-relevance", { id: select.dataset.relevance, relevance });
      showFeedback(`Updated filter rule for ${select.dataset.relevance}.`);
    } catch (error) {
      showFeedback(error.message);
    } finally {
      select.disabled = false;
    }
  });
});

document.querySelectorAll("[data-import-excluded]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Importing…";
    try {
      await sourceAPI("/api/link", { url: button.dataset.importExcluded });
      button.textContent = "Imported";
    } catch (error) {
      button.disabled = false;
      button.textContent = "Import anyway";
      showFeedback(error.message);
    }
  });
});

const addForm = document.getElementById("custom-source-form");
if (addForm) {
  addForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = document.getElementById("custom-source-submit");
    submit.disabled = true;
    showFeedback("");
    try {
      await sourceAPI("/api/source-add", {
        name: document.getElementById("custom-source-name").value,
        rss_url: document.getElementById("custom-source-rss").value,
        parent_url: document.getElementById("custom-source-site").value,
      });
      location.reload();
    } catch (error) {
      showFeedback(error.message);
      submit.disabled = false;
    }
  });
}

// Check every enabled source at once, using the same background job as the home page's refresh.
const checkAll = document.getElementById("check-all-sources");
if (checkAll) {
  const status = document.getElementById("check-all-status");
  const results = document.getElementById("check-all-results");
  const say = (text) => {
    status.textContent = text;
    status.hidden = !text;
  };
  checkAll.addEventListener("click", async () => {
    checkAll.disabled = true;
    results.hidden = true;
    const started = Date.now();
    say("Checking your enabled sources… this can take a minute.");
    try {
      const job = await sourceAPI("/api/jobs/discover", { days: Number(document.getElementById("check-all-days").value) });
      let report = job;
      if (job?.id) {
        // Poll quickly at first, then settle, so a fast check doesn't wait on a fixed delay.
        for (let wait = 400; ; wait = Math.min(wait * 1.5, 2000)) {
          await new Promise((resolve) => setTimeout(resolve, wait));
          const state = await sourceAPI("/api/jobs?" + new URLSearchParams({ id: job.id }));
          if (state.state === "done") {
            report = state.result;
            break;
          }
          if (state.state === "failed") throw new Error(state.error || "Checking sources failed.");
          say(`Checking your enabled sources… ${Math.round((Date.now() - started) / 1000)}s`);
        }
      }
      const sources = report?.sources || [];
      const added = sources.reduce((sum, item) => sum + (item.inserted || 0), 0);
      const issues = [...(report?.errors || []), ...(report?.paused || [])];
      say(
        added
          ? `${added} new ${added === 1 ? "article" : "articles"} from ${sources.filter((s) => s.inserted).length} sources.`
          : "Up to date. No new articles right now.",
      );
      const rows = sources
        .filter((s) => s.inserted)
        .map((s) => {
          const li = document.createElement("li");
          li.textContent = `${s.id}: ${s.inserted} new`;
          return li;
        });
      for (const issue of issues) {
        const li = document.createElement("li");
        li.className = "sources-check-issue";
        li.textContent = issue;
        rows.push(li);
      }
      results.replaceChildren(...rows);
      results.hidden = rows.length === 0;
    } catch (error) {
      say(error.message);
    } finally {
      checkAll.disabled = false;
    }
  });
}
