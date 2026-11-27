"use strict";
const token = document.querySelector('meta[name="csrf-token"]')?.content || "";

async function postIndicator(path, data) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Unable to update indicator.");
  return result;
}

// Builds nodes with textContent only, so report titles are never parsed as HTML.
function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  for (const child of children) if (child != null) node.append(child);
  return node;
}

const dateFormat = new Intl.DateTimeFormat("en-CA", { month: "short", day: "numeric", year: "numeric" });
const shortDate = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : dateFormat.format(date);
};

// Mentions per bucket between first and last sighting, so a burst of reports stands out.
function timeline(articles) {
  const times = articles.map((a) => new Date(a.date).getTime()).filter((t) => !Number.isNaN(t));
  if (times.length < 2) return null;
  const first = Math.min(...times), last = Math.max(...times);
  const buckets = Math.min(12, Math.max(4, times.length));
  const counts = new Array(buckets).fill(0);
  for (const t of times)
    counts[last === first ? 0 : Math.min(buckets - 1, Math.floor(((t - first) / (last - first)) * buckets))]++;
  const peak = Math.max(...counts);
  const bars = el("div", { className: "ind-bars" });
  bars.setAttribute("role", "img");
  bars.setAttribute("aria-label", `${times.length} reports between ${shortDate(first)} and ${shortDate(last)}`);
  for (const count of counts) {
    const bar = el("span", { title: `${count} ${count === 1 ? "report" : "reports"}` });
    bar.style.height = count ? `${Math.max(12, (count / peak) * 100)}%` : "2px";
    if (!count) bar.className = "empty";
    bars.append(bar);
  }
  return bars;
}

const detail = document.getElementById("ind-detail");
const rows = [...document.querySelectorAll(".ind-row")];
let items = [];
try {
  items = JSON.parse(document.getElementById("ind-data")?.textContent || "[]");
} catch {
  items = [];
}

async function writeClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Older path for browsers that refuse the Clipboard API.
    const area = el("textarea", { value: text, readOnly: true });
    area.style.cssText = "position:fixed;opacity:0;pointer-events:none";
    document.body.append(area);
    area.select();
    const done = document.execCommand("copy");
    area.remove();
    return done;
  }
}

function renderDetail(item) {
  detail.replaceChildren();
  if (!item) return;
  // The value is the panel's heading, set like the Library's article titles.
  const value = el("h2", { className: "ind-detail-value", textContent: item.value });
  const meta = el("p", { className: "ind-detail-meta" });
  if (item.kev) meta.append(el("span", { className: "ind-flag ind-kev", textContent: "CISA KEV" }));
  if (item.ransomware) meta.append(el("span", { className: "ind-flag ind-ransom", textContent: "Ransomware" }));
  if (item.stale) meta.append(el("span", { className: "ind-flag", textContent: "Stale" }));
  const count = item.articles.length;
  meta.append(`${item.kindLabel} · ${count} ${count === 1 ? "report" : "reports"}`);
  detail.append(el("div", { className: "ind-detail-head" }, value));
  if (item.name) detail.append(el("p", { className: "ind-detail-name", textContent: item.name }));
  detail.append(meta);

  const bars = timeline(item.articles);
  if (bars) detail.append(el("h3", { textContent: "Mentions over time" }), bars);
  detail.append(el("p", { className: "ind-seen" },
    item.first === item.last ? `Seen ${shortDate(item.first)}` : `First ${shortDate(item.first)} · Last ${shortDate(item.last)}`));

  const list = el("ul", { className: "ind-reports" });
  item.articles.forEach((article, index) => {
    const li = el("li", {},
      el("a", { href: article.href, textContent: article.title }),
      el("span", { className: "muted", textContent: `${article.publisher} · ${shortDate(article.date)}` }));
    if (index >= 5) li.hidden = true;
    list.append(li);
  });
  detail.append(el("h3", { textContent: "Reports" }), list);
  if (count > 5) {
    const more = el("button", { type: "button", className: "ind-more", textContent: `Show ${count - 5} more` });
    more.addEventListener("click", () => {
      list.querySelectorAll("li[hidden]").forEach((li) => (li.hidden = false));
      more.remove();
    });
    detail.append(more);
  }

  const actions = el("div", { className: "ind-actions" });
  for (const pivot of item.pivots)
    actions.append(el("a", { className: "ioc-pivot-link", href: pivot.url, target: "_blank", rel: "noopener noreferrer", textContent: `${pivot.label} ↗` }));
  const ignore = el("button", { type: "button", className: "ioc-ignore-btn", textContent: "Ignore as false positive", title: "Hide this value everywhere in your library" });
  ignore.addEventListener("click", async () => {
    ignore.disabled = true;
    try {
      await Promise.all([
        window.intelMagic?.banish(selected, { tone: "smoke" }),
        postIndicator("/api/indicator-ignore", { kind: item.kind, value: item.value }),
      ]);
      location.reload();
    } catch (error) {
      selected?.removeAttribute("data-banishing");
      ignore.disabled = false;
      alert(error.message);
    }
  });
  actions.append(ignore);
  detail.append(actions);
}

let selected = null;
function select(row, { focus = false, scroll = false } = {}) {
  if (!row) return;
  if (selected) selected.setAttribute("aria-selected", "false");
  selected = row;
  row.setAttribute("aria-selected", "true");
  if (focus) row.focus();
  renderDetail(items[Number(row.dataset.index)]);
  // On narrow screens the panel sits below the table, so bring it into view.
  if (scroll && window.matchMedia("(max-width: 899px)").matches) detail.scrollIntoView({ block: "start", behavior: "smooth" });
}

rows.forEach((row, index) => {
  row.addEventListener("click", (event) => {
    // The checkbox only picks the row for copying; the rest of the row opens the panel.
    if (event.target.closest(".ind-check")) return;
    select(row, { scroll: true });
  });
  row.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      select(rows[index + (event.key === "ArrowDown" ? 1 : -1)], { focus: true });
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select(row, { scroll: true });
    }
  });
});
select(rows[0]);

// Picking rows to copy: checkboxes, Shift-click for a range, and a header box for everything shown.
const picks = rows.map((row) => row.querySelector(".ind-pick"));
const pickAll = document.querySelector(".ind-pick-all");
const selbar = document.querySelector(".ind-selbar");
const selcount = document.querySelector(".ind-selcount");
const copySelected = document.querySelector(".ind-copy-selected");
let lastPick = null;

function pickedValues() {
  return picks.flatMap((box, i) => (box?.checked ? [items[Number(rows[i].dataset.index)].value] : []));
}
function updatePicks() {
  const count = pickedValues().length;
  if (selbar) selbar.hidden = count === 0;
  if (selcount) selcount.textContent = `${count} selected`;
  if (copySelected) copySelected.textContent = "Copy to clipboard";
  rows.forEach((row, i) => row.classList.toggle("picked", !!picks[i]?.checked));
  if (pickAll) {
    pickAll.checked = count > 0 && count === picks.length;
    pickAll.indeterminate = count > 0 && count < picks.length;
  }
}
picks.forEach((box, i) => {
  box?.addEventListener("click", (event) => {
    if (event.shiftKey && lastPick !== null && lastPick !== i) {
      const [from, to] = lastPick < i ? [lastPick, i] : [i, lastPick];
      for (let j = from; j <= to; j++) if (picks[j]) picks[j].checked = box.checked;
    }
    lastPick = i;
    updatePicks();
  });
});
pickAll?.addEventListener("change", () => {
  picks.forEach((box) => box && (box.checked = pickAll.checked));
  updatePicks();
});
document.querySelector(".ind-clear-selected")?.addEventListener("click", () => {
  picks.forEach((box) => box && (box.checked = false));
  updatePicks();
});
// One value per line, which pastes cleanly into a SIEM search, a blocklist, or a spreadsheet column.
copySelected?.addEventListener("click", async () => {
  const values = pickedValues();
  const done = await writeClipboard(values.join("\n"));
  copySelected.textContent = done ? `Copied ${values.length}` : "Copy blocked";
  setTimeout(() => (copySelected.textContent = "Copy to clipboard"), 1600);
});

document.querySelectorAll("[data-unignore-kind]").forEach((button) => {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await postIndicator("/api/indicator-unignore", {
        kind: button.dataset.unignoreKind,
        value: button.dataset.unignoreValue,
      });
      location.reload();
    } catch (error) {
      button.disabled = false;
      alert(error.message);
    }
  });
});

// Filter menus close on an outside click, and opening one closes the others.
const menus = document.querySelectorAll(".ind-menu");
document.addEventListener("click", (event) => {
  for (const menu of menus) if (menu.open && !menu.contains(event.target)) menu.open = false;
});
