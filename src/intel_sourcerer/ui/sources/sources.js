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
    const cast = window.intelMagic?.casting(button);
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
      cast?.finish(inserted > 0);
      if (inserted > 0) setTimeout(() => location.reload(), 1200);
    } catch (error) {
      cast?.cancel();
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

// --- Filtered Articles Review & Live Keyword Tuning ---
const reviewPanel = document.getElementById("sources-review-panel");
if (reviewPanel) {
  let filtersState = { include_keywords: [], exclude_keywords: [], is_custom: false };
  try {
    filtersState = JSON.parse(reviewPanel.dataset.filters || "{}");
  } catch {}
  filtersState.include_keywords ||= [];
  filtersState.exclude_keywords ||= [];

  const toggleExcludedBtn = document.getElementById("toggle-excluded-pane");
  const toggleKeywordsBtn = document.getElementById("toggle-keywords-pane");
  const excludedPane = document.getElementById("excluded-review-pane");
  const keywordsPane = document.getElementById("keyword-tuning-pane");
  const countBadge = document.getElementById("excluded-count-badge");
  const toggleCountEl = document.getElementById("excluded-toggle-count");
  const keywordsCountEl = document.getElementById("keywords-toggle-count");
  const includeChipsEl = document.getElementById("include-keyword-chips");
  const excludeChipsEl = document.getElementById("exclude-keyword-chips");
  const includeForm = document.getElementById("include-keyword-form");
  const excludeForm = document.getElementById("exclude-keyword-form");
  const includeInput = document.getElementById("include-keyword-input");
  const excludeInput = document.getElementById("exclude-keyword-input");
  const tuningStatus = document.getElementById("keyword-tuning-status");
  const resetDefaultsBtn = document.getElementById("keyword-reset-defaults");
  const sourcePillsEl = document.getElementById("excluded-source-pills");
  const keepSourceAllBtn = document.getElementById("excluded-keep-source-all");
  const dismissShownBtn = document.getElementById("excluded-dismiss-shown");
  const matchedBanner = document.getElementById("excluded-matched-banner");
  const matchedText = document.getElementById("excluded-matched-text");
  const importMatchedBtn = document.getElementById("excluded-import-matched");
  const reviewList = document.getElementById("excluded-review-list");
  const emptyState = document.getElementById("excluded-empty-state");

  let activeSourceFilter = "";

  const STOP_WORDS = new Set([
    "a", "an", "and", "are", "as", "at", "be", "building", "by", "can", "for", "from",
    "how", "in", "into", "is", "it", "its", "new", "next", "now", "of", "on", "or",
    "our", "out", "over", "part", "post", "replay", "that", "the", "their", "them",
    "this", "to", "too", "up", "us", "use", "using", "we", "what", "when", "why",
    "with", "you", "your", "2024", "2025", "2026", "2027",
  ]);

  function setPaneOpen(btn, pane, open) {
    if (!btn || !pane) return;
    pane.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    btn.classList.toggle("is-open", open);
  }

  toggleExcludedBtn?.addEventListener("click", () => {
    setPaneOpen(toggleExcludedBtn, excludedPane, excludedPane.hidden);
  });
  toggleKeywordsBtn?.addEventListener("click", () => {
    setPaneOpen(toggleKeywordsBtn, keywordsPane, keywordsPane.hidden);
    if (!keywordsPane.hidden) includeInput?.focus();
  });

  function allRows() {
    return reviewList ? [...reviewList.querySelectorAll("li.excluded-row")] : [];
  }

  function pendingRows() {
    return allRows().filter((row) => !row.classList.contains("is-imported"));
  }

  function visiblePendingRows() {
    return pendingRows().filter((row) => !row.hidden);
  }

  function matchArticle(title, summary) {
    const lowTitle = (title || "").toLowerCase();
    const lowAll = (lowTitle + " " + (summary || "").toLowerCase()).trim();
    for (const term of filtersState.exclude_keywords) {
      if (term && lowTitle.includes(term.toLowerCase())) {
        return { matched: false, blockedBy: term, matchedBy: "" };
      }
    }
    for (const term of filtersState.include_keywords) {
      if (term && lowAll.includes(term.toLowerCase())) {
        return { matched: true, blockedBy: "", matchedBy: term };
      }
    }
    return { matched: false, blockedBy: "", matchedBy: "" };
  }

  function refreshCountsAndPills() {
    const remaining = pendingRows();
    const total = remaining.length;
    if (countBadge) {
      countBadge.textContent = total ? `${total} filtered out` : "All caught up";
      countBadge.classList.toggle("has-items", total > 0);
    }
    if (toggleCountEl) toggleCountEl.textContent = String(total);
    if (keywordsCountEl) {
      keywordsCountEl.textContent = `${filtersState.include_keywords.length} · ${filtersState.exclude_keywords.length}`;
    }

    // Update per-source pill counts and hide empty source pills.
    const counts = new Map();
    for (const row of allRows()) {
      if (row.classList.contains("is-imported")) continue;
      const sid = row.dataset.source || "";
      counts.set(sid, (counts.get(sid) || 0) + 1);
    }
    sourcePillsEl?.querySelectorAll(".excluded-source-pill").forEach((pill) => {
      const sid = pill.dataset.excludedSource || "";
      const c = sid ? counts.get(sid) || 0 : total;
      const countSpan = pill.querySelector(".excluded-pill-count");
      if (countSpan) countSpan.textContent = String(c);
      if (sid) pill.hidden = c === 0;
      const isActive = sid === activeSourceFilter;
      pill.classList.toggle("is-active", isActive);
      pill.setAttribute("aria-pressed", String(isActive));
    });
    if (activeSourceFilter && !(counts.get(activeSourceFilter) || 0)) {
      activeSourceFilter = "";
      sourcePillsEl?.querySelector('[data-excluded-source=""]')?.classList.add("is-active");
    }

    for (const row of allRows()) {
      row.hidden = Boolean(activeSourceFilter && row.dataset.source !== activeSourceFilter);
    }

    if (keepSourceAllBtn) {
      const activePill = activeSourceFilter
        ? sourcePillsEl?.querySelector(`.excluded-source-pill[data-excluded-source="${CSS.escape(activeSourceFilter)}"]`)
        : null;
      const showKeepAll = Boolean(activePill && activePill.dataset.sourceRelevance !== "all" && (counts.get(activeSourceFilter) || 0) > 0);
      keepSourceAllBtn.hidden = !showKeepAll;
      if (showKeepAll) {
        keepSourceAllBtn.textContent = `Keep all from ${activePill.dataset.sourceName || activeSourceFilter}`;
        keepSourceAllBtn.dataset.sourceId = activeSourceFilter;
      }
    }
    if (dismissShownBtn) {
      const shownCount = visiblePendingRows().length;
      dismissShownBtn.hidden = shownCount === 0;
      dismissShownBtn.textContent = activeSourceFilter ? `Dismiss shown (${shownCount})` : `Dismiss all (${shownCount})`;
    }
    if (emptyState && reviewList) {
      const hasAny = allRows().length > 0;
      emptyState.hidden = hasAny;
      reviewList.hidden = !hasAny;
    }
  }

  function evaluateExcludedRows() {
    let newlyMatched = 0;
    for (const row of pendingRows()) {
      const res = matchArticle(row.dataset.title, row.dataset.summary);
      const matchTag = row.querySelector(".excluded-match-tag");
      const reasonTag = row.querySelector(".excluded-reason-tag");
      row.classList.toggle("is-now-matching", res.matched);
      if (matchTag) {
        if (res.matched) {
          matchTag.textContent = `Matches "${res.matchedBy}"`;
          matchTag.hidden = false;
          newlyMatched++;
        } else {
          matchTag.hidden = true;
        }
      }
      if (reasonTag) {
        if (res.matched) {
          reasonTag.hidden = true;
        } else {
          reasonTag.hidden = false;
          reasonTag.textContent = res.blockedBy ? `Blocked by "${res.blockedBy}"` : (row.dataset.reason || "No keyword match");
        }
      }
    }
    if (matchedBanner && matchedText && importMatchedBtn) {
      matchedBanner.hidden = newlyMatched === 0;
      if (newlyMatched > 0) {
        matchedText.textContent = `${newlyMatched} filtered ${newlyMatched === 1 ? "article now matches" : "articles now match"} your keywords.`;
        importMatchedBtn.textContent = `Import ${newlyMatched} matching`;
      }
    }
    refreshCountsAndPills();
    return newlyMatched;
  }

  function renderKeywordChips() {
    const drawList = (container, list, kind) => {
      if (!container) return;
      container.replaceChildren(
        ...list.map((term) => {
          const chip = document.createElement("span");
          chip.className = `keyword-chip keyword-chip-${kind}`;
          const label = document.createElement("span");
          label.textContent = term;
          const remove = document.createElement("button");
          remove.type = "button";
          remove.className = "keyword-chip-remove";
          remove.textContent = "×";
          remove.setAttribute("aria-label", `Remove ${kind} keyword ${term}`);
          remove.addEventListener("click", async () => {
            const nextInclude = kind === "include"
              ? filtersState.include_keywords.filter((k) => k.toLowerCase() !== term.toLowerCase())
              : [...filtersState.include_keywords];
            const nextExclude = kind === "exclude"
              ? filtersState.exclude_keywords.filter((k) => k.toLowerCase() !== term.toLowerCase())
              : [...filtersState.exclude_keywords];
            if (!nextInclude.length) {
              if (tuningStatus) tuningStatus.textContent = "Keep at least one include keyword.";
              return;
            }
            window.intelMagic?.puff(chip, { tone: "arcane", count: 10 });
            await persistFilters(nextInclude, nextExclude, `Removed "${term}".`);
          });
          chip.append(label, remove);
          return chip;
        }),
      );
    };
    drawList(includeChipsEl, filtersState.include_keywords, "include");
    drawList(excludeChipsEl, filtersState.exclude_keywords, "exclude");
    if (resetDefaultsBtn) resetDefaultsBtn.hidden = !filtersState.is_custom;
    if (keywordsCountEl) {
      keywordsCountEl.textContent = `${filtersState.include_keywords.length} · ${filtersState.exclude_keywords.length}`;
    }
  }

  async function persistFilters(nextInclude, nextExclude, actionMsg = "Saved keywords.") {
    if (tuningStatus) tuningStatus.textContent = "Saving…";
    try {
      const res = await sourceAPI("/api/source-filters", {
        include_keywords: nextInclude,
        exclude_keywords: nextExclude,
      });
      filtersState = res.filters;
      renderKeywordChips();
      const matched = evaluateExcludedRows();
      if (tuningStatus) {
        tuningStatus.textContent = matched
          ? `${actionMsg} ${matched} filtered ${matched === 1 ? "article now matches" : "articles now match"}.`
          : `${actionMsg} Saved to sources.toml.`;
      }
      return true;
    } catch (error) {
      if (tuningStatus) tuningStatus.textContent = error.message;
      return false;
    }
  }

  async function addKeywordTerms(kind, rawInput, sparkEl = null) {
    const additions = (rawInput || "")
      .split(",")
      .map((s) => s.replace(/\s+/g, " ").trim())
      .filter((s) => s.length >= 2);
    if (!additions.length) {
      if (tuningStatus) tuningStatus.textContent = "Enter a keyword of at least 2 characters.";
      return false;
    }
    const targetList = kind === "include" ? [...filtersState.include_keywords] : [...filtersState.exclude_keywords];
    const existing = new Set(targetList.map((t) => t.toLowerCase()));
    const added = [];
    for (const term of additions) {
      if (!existing.has(term.toLowerCase())) {
        targetList.push(term);
        existing.add(term.toLowerCase());
        added.push(term);
      }
    }
    if (!added.length) {
      if (tuningStatus) tuningStatus.textContent = `"${additions[0]}" is already in your ${kind} list.`;
      return false;
    }
    const nextInclude = kind === "include" ? targetList : [...filtersState.include_keywords];
    const nextExclude = kind === "exclude" ? targetList : [...filtersState.exclude_keywords];
    const ok = await persistFilters(nextInclude, nextExclude, `Added "${added.join('", "')}".`);
    if (ok && sparkEl) {
      const rect = sparkEl.getBoundingClientRect();
      window.intelMagic?.sparkPoint(rect.left + rect.width / 2, rect.top + rect.height / 2, { tone: "emerald" });
    }
    return ok;
  }

  includeForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const ok = await addKeywordTerms("include", includeInput.value, includeForm.querySelector("button"));
    if (ok) includeInput.value = "";
  });

  excludeForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const ok = await addKeywordTerms("exclude", excludeInput.value, excludeForm.querySelector("button"));
    if (ok) excludeInput.value = "";
  });

  resetDefaultsBtn?.addEventListener("click", async () => {
    resetDefaultsBtn.disabled = true;
    try {
      const res = await sourceAPI("/api/source-filters", { reset: true });
      filtersState = res.filters;
      renderKeywordChips();
      evaluateExcludedRows();
      if (tuningStatus) tuningStatus.textContent = "Restored default keywords.";
    } catch (error) {
      if (tuningStatus) tuningStatus.textContent = error.message;
    } finally {
      resetDefaultsBtn.disabled = false;
    }
  });

  function extractCandidateTerms(title) {
    const existing = new Set(filtersState.include_keywords.map((k) => k.toLowerCase()));
    const rawWords = (title || "").match(/[A-Za-z][A-Za-z0-9-]{2,}/g) || [];
    const candidates = [];
    const seen = new Set();
    const push = (term) => {
      const low = term.toLowerCase();
      if (seen.has(low) || existing.has(low) || STOP_WORDS.has(low)) return;
      seen.add(low);
      candidates.push(term);
    };
    // Prefer 2-word capitalized/domain bigrams (e.g. "Agentic SOC", "Dual-Use", "Next-Gen SIEM")
    for (let i = 0; i < rawWords.length - 1; i++) {
      const a = rawWords[i], b = rawWords[i + 1];
      if (!STOP_WORDS.has(a.toLowerCase()) && !STOP_WORDS.has(b.toLowerCase()) && (a.length >= 3 || b.length >= 3)) {
        if (/^[A-Z]/.test(a) && /^[A-Z]/.test(b)) push(`${a} ${b}`);
      }
    }
    for (const w of rawWords) {
      if (w.length >= 3) push(w);
    }
    return candidates.slice(0, 6);
  }

  function renderQuickTerms(row) {
    const box = row.querySelector(".excluded-quick-terms");
    if (!box) return;
    if (!box.hidden) {
      box.hidden = true;
      return;
    }
    const candidates = extractCandidateTerms(row.dataset.title || "");
    const label = document.createElement("span");
    label.className = "excluded-quick-label";
    label.textContent = "Add to include keywords:";

    const chipsWrap = document.createElement("div");
    chipsWrap.className = "excluded-quick-chips";
    for (const term of candidates) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "excluded-quick-chip";
      btn.textContent = `+ ${term}`;
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        const ok = await addKeywordTerms("include", term, btn);
        if (ok) {
          btn.textContent = `✓ ${term}`;
          btn.classList.add("is-added");
        } else {
          btn.disabled = false;
        }
      });
      chipsWrap.append(btn);
    }

    const customForm = document.createElement("form");
    customForm.className = "excluded-quick-form";
    const input = document.createElement("input");
    input.type = "text";
    input.maxLength = 80;
    input.placeholder = "Custom keyword…";
    input.setAttribute("aria-label", "Add custom include keyword");
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.textContent = "Add";
    customForm.append(input, submit);
    customForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      const ok = await addKeywordTerms("include", input.value, submit);
      submit.disabled = false;
      if (ok) {
        input.value = "";
        box.hidden = true;
      }
    });

    box.replaceChildren(label, chipsWrap, customForm);
    box.hidden = false;
    input.focus();
  }

  async function importExcludedRow(row) {
    const url = row.dataset.url;
    const btn = row.querySelector("[data-import-excluded]");
    if (!url || !btn || row.classList.contains("is-imported")) return false;
    btn.disabled = true;
    btn.textContent = "Importing…";
    const cast = window.intelMagic?.casting(btn);
    try {
      const result = await sourceAPI("/api/link", { url });
      cast?.finish(true);
      row.classList.add("is-imported");
      row.classList.remove("is-now-matching");
      const actions = row.querySelector(".excluded-row-actions");
      if (actions) {
        const done = document.createElement("span");
        done.className = "excluded-imported-badge";
        done.textContent = "Imported ✓";
        const openLink = document.createElement("a");
        openLink.className = "excluded-open-link";
        openLink.href = "/?" + new URLSearchParams({ open: result.url || url });
        openLink.textContent = "Open";
        actions.replaceChildren(done, openLink);
      }
      window.intelMagic?.celebrate(row, { tone: "emerald", intensity: 0.7 });
      evaluateExcludedRows();
      return true;
    } catch (error) {
      cast?.cancel();
      btn.disabled = false;
      btn.textContent = "Import";
      showFeedback(error.message);
      return false;
    }
  }

  sourcePillsEl?.addEventListener("click", (event) => {
    const pill = event.target.closest(".excluded-source-pill");
    if (!pill) return;
    activeSourceFilter = pill.dataset.excludedSource || "";
    refreshCountsAndPills();
  });

  keepSourceAllBtn?.addEventListener("click", async () => {
    const sid = keepSourceAllBtn.dataset.sourceId;
    if (!sid) return;
    keepSourceAllBtn.disabled = true;
    try {
      await sourceAPI("/api/source-relevance", { id: sid, relevance: "all" });
      const activePill = sourcePillsEl?.querySelector(`.excluded-source-pill[data-excluded-source="${CSS.escape(sid)}"]`);
      if (activePill) activePill.dataset.sourceRelevance = "all";
      const sourceSelect = document.querySelector(`[data-relevance="${CSS.escape(sid)}"]`);
      if (sourceSelect) sourceSelect.value = "all";
      showFeedback(`Updated ${sid} to keep all posts on future refreshes.`);
      refreshCountsAndPills();
    } catch (error) {
      showFeedback(error.message);
    } finally {
      keepSourceAllBtn.disabled = false;
    }
  });

  dismissShownBtn?.addEventListener("click", async () => {
    const rows = visiblePendingRows();
    const urls = rows.map((r) => r.dataset.url).filter(Boolean);
    if (!urls.length) return;
    dismissShownBtn.disabled = true;
    try {
      await Promise.all(rows.slice(0, 8).map((r) => window.intelMagic?.banish(r, { tone: "clear" })));
      await sourceAPI("/api/excluded-dismiss", { urls });
      for (const row of rows) row.remove();
      evaluateExcludedRows();
    } catch (error) {
      showFeedback(error.message);
    } finally {
      dismissShownBtn.disabled = false;
    }
  });

  importMatchedBtn?.addEventListener("click", async () => {
    const matching = pendingRows().filter((r) => r.classList.contains("is-now-matching"));
    if (!matching.length) return;
    importMatchedBtn.disabled = true;
    let imported = 0;
    for (let i = 0; i < matching.length; i++) {
      importMatchedBtn.textContent = `Importing ${i + 1}/${matching.length}…`;
      if (await importExcludedRow(matching[i])) imported++;
    }
    importMatchedBtn.disabled = false;
    if (imported > 0) showFeedback(`Imported ${imported} newly matched ${imported === 1 ? "article" : "articles"}.`);
    evaluateExcludedRows();
  });

  reviewList?.addEventListener("click", async (event) => {
    const importBtn = event.target.closest("[data-import-excluded]");
    if (importBtn) {
      const row = importBtn.closest("li.excluded-row");
      if (row) await importExcludedRow(row);
      return;
    }
    const dismissBtn = event.target.closest("[data-dismiss-excluded]");
    if (dismissBtn) {
      const row = dismissBtn.closest("li.excluded-row");
      const url = dismissBtn.dataset.dismissExcluded;
      if (!row || !url) return;
      dismissBtn.disabled = true;
      try {
        await window.intelMagic?.banish(row, { tone: "clear" });
        await sourceAPI("/api/excluded-dismiss", { urls: [url] });
        row.remove();
        evaluateExcludedRows();
      } catch (error) {
        dismissBtn.disabled = false;
        showFeedback(error.message);
      }
      return;
    }
    const suggestBtn = event.target.closest("[data-suggest-keywords]");
    if (suggestBtn) {
      const row = suggestBtn.closest("li.excluded-row");
      if (row) renderQuickTerms(row);
    }
  });

  renderKeywordChips();
  evaluateExcludedRows();
}

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
    const cast = window.intelMagic?.casting(checkAll);
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
      cast?.finish(added > 0);
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
      cast?.cancel();
      say(error.message);
    } finally {
      checkAll.disabled = false;
    }
  });
}
