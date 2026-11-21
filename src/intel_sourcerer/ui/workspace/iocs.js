// IOC panel rendering, jump-to-value highlighting, external zero-telemetry pivots, and related-report correlation UI.

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

function externalPivots(kind, value) {
  const enc = encodeURIComponent(value);
  if (kind === "cves") return [["NVD", `https://nvd.nist.gov/vuln/detail/${enc}`]];
  if (kind === "techniques") {
    if (value.startsWith("AML.")) return [["ATLAS", `https://atlas.mitre.org/techniques/${enc}`]];
    const section = value.startsWith("TA") ? "tactics" : "techniques";
    return [["ATT&CK", `https://attack.mitre.org/${section}/${value.replace(".", "/")}/`]];
  }
  if (kind === "entities") {
    const section = value.startsWith("G") ? "groups" : "software";
    return [["ATT&CK", `https://attack.mitre.org/${section}/${enc}/`]];
  }
  if (kind === "hashes") return [["VT", `https://www.virustotal.com/gui/file/${enc}`]];
  if (kind === "domains") return [["VT", `https://www.virustotal.com/gui/domain/${enc}`]];
  if (kind === "ips")
    return [
      ["VT", `https://www.virustotal.com/gui/ip-address/${enc}`],
      ["Shodan", `https://www.shodan.io/host/${enc}`],
    ];
  return [];
}

// Reports often defang names as evil[.]exe, so the page text may not match the clean value.
export function jumpToText(value, { bodyEl, setPhoneView, notice }) {
  setPhoneView("read");
  const wanted = [
      value,
      value.replace(/\./g, "[.]"),
      value.replace(/\./g, "(.)"),
      value.replace(/\./g, "/"),
      value.replace(/^https?:/i, "hxxp:"),
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
    ...(shared.hashes || []).map((value) => `${value.slice(0, 12)}…`),
    ...(shared.cves || []),
    ...(shared.domains || []),
    ...(shared.ips || []),
    ...(shared.files || []),
    ...(shared.registry || []),
    ...(shared.entities || []),
    ...(shared.techniques || []),
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
  const pivots = row.querySelector(".ioc-pivots");
  if (pivots) row.insertBefore(button, pivots);
  else row.append(button);
  row.append(list);
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
    ? "Reports in your library that share a hash, CVE, domain, IP, registry key, or file name with this one, or at least three ATT&CK techniques."
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
    if (item.kev) {
      const badge = document.createElement("span");
      badge.className = "kev-badge";
      badge.textContent = item.ransomware ? "KEV · Ransomware" : "KEV";
      badge.title = "Listed in CISA Known Exploited Vulnerabilities";
      value.append(" ", badge);
    }
    if (item.name) {
      const labelSpan = document.createElement("span");
      labelSpan.className = "ioc-name";
      labelSpan.textContent = item.retired ? `${item.name} (retired)` : item.name;
      value.append(" ", labelSpan);
    }
    if (item.manual) {
      const badge = document.createElement("span");
      badge.className = "ioc-kind-pill";
      badge.textContent = "Manual";
      value.append(" ", badge);
    }
    value.setAttribute(
      "aria-label",
      `Find ${item.value}${item.name ? ", " + item.name + "," : ""} in the article`,
    );
    value.onclick = () => jumpToText(item.value, handlers);
    row.append(value);
    const pivots = externalPivots(kind, item.value);
    const temporary = document.body.classList.contains("temporary-session");
    if (pivots.length || (kind && !temporary)) {
      const links = document.createElement("span");
      links.className = "ioc-pivots";
      if (kind && !temporary) {
        const local = document.createElement("a");
        local.className = "ioc-pivot-link";
        local.href = "/indicators?" + new URLSearchParams({ q: item.value });
        local.textContent = "Index";
        local.title = `Search ${item.value} across your library`;
        links.append(local);
      }
      for (const [text, href] of pivots) {
        const ext = document.createElement("a");
        ext.className = "ioc-pivot-link";
        ext.href = href;
        ext.target = "_blank";
        ext.rel = "noopener noreferrer";
        ext.textContent = `${text} ↗`;
        links.append(ext);
      }
      if (kind && !temporary && handlers.onIgnore) {
        const ignore = document.createElement("button");
        ignore.type = "button";
        ignore.className = "ioc-ignore-btn";
        ignore.textContent = "×";
        ignore.title = item.manual ? "Remove manual indicator" : "Ignore false positive across library";
        ignore.setAttribute("aria-label", `${item.manual ? "Remove" : "Ignore false positive"}: ${item.value}`);
        ignore.onclick = () => handlers.onIgnore(kind, item.value, !!item.manual);
        links.append(ignore);
      }
      row.append(links);
    }
    if (kind) {
      row.dataset.kind = kind;
      row.dataset.key = ["files", "registry"].includes(kind) ? item.value.toLowerCase() : item.value;
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
    entities = iocs.entities || [],
    hashes = iocs.hashes || [],
    domains = iocs.domains || [],
    ips = iocs.ips || [],
    urls = iocs.urls || [],
    files = iocs.files || [],
    registry = iocs.registry || [],
    samples = files.filter((item) => !item.common),
    common = files.filter((item) => item.common),
    groups = [];
  if (cves.length) groups.push(iocGroup("CVEs", cves, null, "cves", handlers));
  if (techniques.length) groups.push(iocGroup("MITRE ATT&CK", techniques, null, "techniques", handlers));
  if (entities.length) groups.push(iocGroup("MITRE Groups & Software", entities, null, "entities", handlers));
  if (hashes.length) groups.push(iocGroup("File hashes", hashes, (item) => item.type, "hashes", handlers));
  if (domains.length) groups.push(iocGroup("Domains", domains, null, "domains", handlers));
  if (ips.length) groups.push(iocGroup("IP addresses", ips, null, "ips", handlers));
  if (urls.length) groups.push(iocGroup("Defanged URLs", urls, null, null, handlers));
  if (samples.length) groups.push(iocGroup("File names", samples, null, "files", handlers));
  if (registry.length) groups.push(iocGroup("Registry keys", registry, null, "registry", handlers));
  if (common.length) groups.push(iocGroup("Common tools and files", common, null, null, handlers));
  $("iocs-groups").replaceChildren(...groups);
  const temporary = document.body.classList.contains("temporary-session");
  $("iocs-panel").hidden = temporary && !groups.length;
  $("iocs-panel").open = false;
  const addForm = $("ioc-add-form");
  if (addForm) addForm.hidden = temporary || !article?.url;
  const parts = [];
  const kevCount = cves.filter((c) => c.kev).length;
  if (cves.length)
    parts.push(`${cves.length} ${cves.length === 1 ? "CVE" : "CVEs"}${kevCount ? ` (${kevCount} KEV)` : ""}`);
  if (techniques.length) parts.push(`${techniques.length} ${techniques.length === 1 ? "TTP" : "TTPs"}`);
  if (entities.length) parts.push(`${entities.length} MITRE`);
  if (hashes.length) parts.push(`${hashes.length} ${hashes.length === 1 ? "hash" : "hashes"}`);
  if (domains.length) parts.push(`${domains.length} ${domains.length === 1 ? "domain" : "domains"}`);
  if (ips.length) parts.push(`${ips.length} ${ips.length === 1 ? "IP" : "IPs"}`);
  if (files.length) parts.push(`${files.length} ${files.length === 1 ? "file" : "files"}`);
  if (registry.length) parts.push(`${registry.length} reg`);
  $("iocs-count").textContent = parts.length ? `· ${parts.join(", ")}` : "";
  const exportBar = $("iocs-exports");
  if (exportBar) {
    const temp = document.body.classList.contains("temporary-session");
    exportBar.hidden = temp || !groups.length || !article?.url;
    if (!exportBar.hidden) {
      for (const link of exportBar.querySelectorAll("a[data-export-fmt]")) {
        link.href = "/export?" + new URLSearchParams({ url: article.url, format: link.dataset.exportFmt });
      }
    }
  }
}
