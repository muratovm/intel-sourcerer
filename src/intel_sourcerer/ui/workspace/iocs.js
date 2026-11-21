// IOC panel rendering, jump-to-value highlighting, and related-report correlation UI.

const $ = (id) => document.getElementById(id);

export function copyText(button, text, notice) {
  const label = button.textContent;
  navigator.clipboard
    .writeText(text)
    .then(() => {
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = label), 1400);
    })
    .catch(() => notice("Your browser blocked copying. Select the text instead."));
}

// Reports often defang names as evil[.]exe, so the page text may not match the clean value.
export function jumpToText(value, { bodyEl, setPhoneView, notice }) {
  setPhoneView("read");
  const wanted = [
      value,
      value.replace(/\./g, "[.]"),
      value.replace(/\./g, "(.)"),
      value.replace(/\./g, "/"),
    ].map((item) => item.toLowerCase()),
    walker = document.createTreeWalker(bodyEl, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    // Same length as the original, so offsets still line up: sites print non-breaking hyphens.
    const text = node.data.toLowerCase().replace(/[\u2010-\u2014]/g, "-");
    const found = wanted.map((item) => text.indexOf(item)).find((index) => index >= 0);
    if (found === undefined) continue;
    const block = node.parentElement,
      range = document.createRange();
    range.setStart(node, found);
    range.setEnd(node, found + wanted.find((item) => text.includes(item)).length);
    block.scrollIntoView({ block: "center" });
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return;
  }
  // Some IDs exist only in a link, like a technique's name pointing at its ATT&CK page.
  const link = [...bodyEl.querySelectorAll("a[href]")].find((anchor) =>
    wanted.some((item) => anchor.getAttribute("href").toLowerCase().includes(item)),
  );
  if (link) {
    link.scrollIntoView({ block: "center" });
    const selection = window.getSelection();
    selection.selectAllChildren(link);
    return;
  }
  notice("That value isn't shown as plain text in this article.");
}

export function reportLink(report, dateLabel) {
  const link = document.createElement("a"),
    title = document.createElement("span"),
    meta = document.createElement("span");
  link.className = "related-report";
  link.href = "/?" + new URLSearchParams({ open: report.url });
  title.className = "related-title";
  title.textContent = report.title;
  meta.className = "muted";
  meta.textContent = `${report.publisher} · ${dateLabel(report.published_at)}`;
  link.append(title, meta);
  return link;
}

export function sharedSummary(shared) {
  const parts = [
    ...shared.hashes.map((value) => `${value.slice(0, 12)}…`),
    ...shared.cves,
    ...shared.files,
    ...shared.techniques,
  ];
  return parts.slice(0, 4).join(", ") + (parts.length > 4 ? ` and ${parts.length - 4} more` : "");
}

export function addAlsoInButton(row, reports, dateLabel) {
  const button = document.createElement("button"),
    list = document.createElement("ul"),
    label = `In ${reports.length} other ${reports.length === 1 ? "report" : "reports"}`;
  button.type = "button";
  button.className = "ioc-also-in";
  button.textContent = `+${reports.length}`;
  button.title = label;
  button.setAttribute("aria-label", label);
  button.setAttribute("aria-expanded", "false");
  list.className = "ioc-also-list";
  list.hidden = true;
  for (const report of reports) {
    const item = document.createElement("li");
    item.append(reportLink(report, dateLabel));
    list.append(item);
  }
  button.onclick = () => {
    list.hidden = !list.hidden;
    button.setAttribute("aria-expanded", String(!list.hidden));
  };
  row.append(button, list);
}

export function renderRelated(data, { dateLabel }) {
  const byURL = new Map(data.reports.map((report) => [report.url, report])),
    related = data.related.map((url) => byURL.get(url));
  $("related-list").replaceChildren(
    ...related.map((report) => {
      const row = document.createElement("li"),
        shared = document.createElement("span");
      shared.className = "related-shared";
      shared.textContent = sharedSummary(report.shared);
      row.append(reportLink(report, dateLabel), shared);
      return row;
    }),
  );
  $("related-count").textContent = related.length ? `· ${related.length}` : "";
  $("related-panel").hidden = !related.length && data.complete;
  $("related-status").textContent = data.complete
    ? "Reports in your library that share a hash, CVE, or file name with this one, or at least three ATT&CK techniques."
    : "Still reading the rest of your library, so more may appear.";
  for (const row of document.querySelectorAll("#iocs-groups li[data-kind]")) {
    row.querySelectorAll(".ioc-also-in, .ioc-also-list").forEach((item) => item.remove());
    const urls = data.values[row.dataset.kind]?.[row.dataset.key];
    if (urls) addAlsoInButton(row, urls.map((url) => byURL.get(url)), dateLabel);
  }
}

function iocGroup(title, items, label, kind, handlers) {
  const group = document.createElement("section"),
    heading = document.createElement("div"),
    name = document.createElement("h3"),
    copyAll = document.createElement("button"),
    list = document.createElement("ul");
  group.className = "ioc-group";
  heading.className = "ioc-group-heading";
  name.textContent = `${title} · ${items.length}`;
  copyAll.type = "button";
  copyAll.textContent = "Copy all";
  copyAll.setAttribute("aria-label", `Copy all ${title.toLowerCase()}`);
  copyAll.onclick = () =>
    copyText(copyAll, items.map((item) => item.value).join("\n"), handlers.notice);
  heading.append(name, copyAll);
  for (const item of items) {
    const row = document.createElement("li"),
      value = document.createElement("button");
    value.type = "button";
    value.className = "ioc-value";
    value.textContent = label ? `${label(item)} ${item.value}` : item.value;
    if (item.name) {
      const labelSpan = document.createElement("span");
      labelSpan.className = "ioc-name";
      labelSpan.textContent = item.retired ? `${item.name} (retired)` : item.name;
      value.append(" ", labelSpan);
    }
    value.setAttribute(
      "aria-label",
      `Find ${item.value}${item.name ? ", " + item.name + "," : ""} in the article`,
    );
    value.onclick = () => jumpToText(item.value, handlers);
    row.append(value);
    if (kind) {
      row.dataset.kind = kind;
      row.dataset.key = kind === "files" ? item.value.toLowerCase() : item.value;
    }
    list.append(row);
  }
  group.append(heading, list);
  return group;
}

export function renderIOCs(article, handlers) {
  const iocs = article?.iocs || {},
    cves = iocs.cves || [],
    techniques = iocs.techniques || [],
    hashes = iocs.hashes || [],
    files = iocs.files || [],
    samples = files.filter((item) => !item.common),
    common = files.filter((item) => item.common),
    groups = [];
  if (cves.length) groups.push(iocGroup("CVEs", cves, null, "cves", handlers));
  if (techniques.length) groups.push(iocGroup("MITRE ATT&CK", techniques, null, "techniques", handlers));
  if (hashes.length) groups.push(iocGroup("File hashes", hashes, (item) => item.type, "hashes", handlers));
  if (samples.length) groups.push(iocGroup("File names", samples, null, "files", handlers));
  if (common.length) groups.push(iocGroup("Common tools and files", common, null, null, handlers));
  $("iocs-groups").replaceChildren(...groups);
  $("iocs-panel").hidden = !groups.length;
  $("iocs-panel").open = false;
  const parts = [];
  if (cves.length) parts.push(`${cves.length} ${cves.length === 1 ? "CVE" : "CVEs"}`);
  if (techniques.length) parts.push(`${techniques.length} ${techniques.length === 1 ? "TTP" : "TTPs"}`);
  if (hashes.length) parts.push(`${hashes.length} ${hashes.length === 1 ? "hash" : "hashes"}`);
  if (files.length) parts.push(`${files.length} ${files.length === 1 ? "file" : "files"}`);
  $("iocs-count").textContent = parts.length ? `· ${parts.join(", ")}` : "";
}
