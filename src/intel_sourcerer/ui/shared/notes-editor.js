"use strict";
// The rich notes box, shared by the reader and topic pages. Each page supplies busy and notice().
const noteBox = document.getElementById("notes");
function plainHTML(text) {
  const p = document.createElement("div");
  p.textContent = text;
  return p.innerHTML.replace(/\n/g, "<br>");
}
function noteLink(node) {
  const href = (node.getAttribute("href") || "").trim();
  return /^(https?:|mailto:|\/\?open=)/i.test(href) ? href : "";
}
function sanitizeNotes(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  function clean(node) {
    if (node.nodeType === 3) return document.createTextNode(node.textContent);
    if (node.nodeType !== 1) return document.createDocumentFragment();
    const tag = node.tagName.toLowerCase(),
      allowed = [
        "p",
        "div",
        "br",
        "strong",
        "b",
        "em",
        "i",
        "mark",
        "blockquote",
        "ul",
        "ol",
        "li",
        "pre",
        "code",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
      ];
    const href = tag === "a" ? noteLink(node) : "";
    const checklist = tag === "ul" && node.classList.contains("checklist"),
      checked = tag === "li" && node.dataset.checked === "true";
    const result = document.createElement(
      tag === "span" && node.style.backgroundColor
        ? "mark"
        : allowed.includes(tag) || href
          ? tag
          : "span",
    );
    if (checklist) result.className = "checklist";
    if (checked) result.dataset.checked = "true";
    if (href) {
      result.setAttribute("href", href);
      result.setAttribute("target", "_blank");
      result.setAttribute("rel", "noopener noreferrer");
    }
    for (const child of node.childNodes) result.append(clean(child));
    if (result.tagName === "SPAN") {
      const fragment = document.createDocumentFragment();
      fragment.append(...result.childNodes);
      return fragment;
    }
    if (node.style.backgroundColor && result.tagName !== "MARK") {
      const mark = document.createElement("mark");
      mark.append(result);
      return mark;
    }
    return result;
  }
  const holder = document.createElement("div");
  for (const node of template.content.childNodes) holder.append(clean(node));
  return holder.innerHTML;
}
function notesText() {
  return sanitizeNotes(noteBox.innerHTML);
}
function loadNotes(text, format) {
  noteBox.innerHTML =
    format === "html" ? sanitizeNotes(text) : plainHTML(text);
  noteRange = null;
  clearTypingFormat = false;
  updateFormatButtons();
}
let noteRange = null;
document.addEventListener("selectionchange", () => {
  const sel = window.getSelection();
  if (
    sel.rangeCount &&
    noteBox.contains(sel.anchorNode) &&
    noteBox.contains(sel.focusNode)
  )
    noteRange = sel.getRangeAt(0).cloneRange();
});
function focusNotes() {
  noteBox.focus();
  const sel = window.getSelection();
  if (noteRange && noteBox.contains(noteRange.startContainer)) {
    sel.removeAllRanges();
    sel.addRange(noteRange);
  } else {
    const range = document.createRange();
    range.selectNodeContents(noteBox);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
  }
}
function noteChanged() {
  noteBox.dispatchEvent(new Event("input"));
}
function insertCallout(text, html = null) {
  // On a phone's Read tab the editor is hidden and can't take a caret or show a keyboard.
  if (noteBox.offsetParent === null) {
    noteBox.insertAdjacentHTML(
      "beforeend",
      "<blockquote>" + (html ? sanitizeNotes(html) : plainHTML(text)) + "</blockquote><div><br></div>",
    );
    document.getElementById("tab-notes").textContent = "Notes · added";
    noteChanged();
    return;
  }
  focusNotes();
  const selection = window.getSelection();
  if (selection.rangeCount) selection.collapseToEnd();
  document.execCommand(
    "insertHTML",
    false,
    "<blockquote>" +
      (html ? sanitizeNotes(html) : plainHTML(text)) +
      "</blockquote><div><br></div>",
  );
  noteChanged();
}
function selectionFormatState(kind) {
  const selection = window.getSelection();
  if (!selection.rangeCount || !noteBox.contains(selection.anchorNode))
    return false;
  const selector =
    kind === "bold"
      ? "b,strong"
      : kind === "list"
        ? "ul:not(.checklist)"
        : kind === "numbered"
          ? "ol"
          : kind === "checklist"
            ? "ul.checklist"
            : "mark";
  if (["list", "numbered", "checklist"].includes(kind)) {
    const node =
      selection.anchorNode.nodeType === 1
        ? selection.anchorNode
        : selection.anchorNode.parentElement;
    // The nearest list decides, so a bullet inside a numbered item lights only Bulleted list.
    return node.closest("ul,ol")?.matches(selector) ?? false;
  }
  if (selection.isCollapsed) {
    if (kind === "bold") return document.queryCommandState("bold");
    const node =
      selection.anchorNode.nodeType === 1
        ? selection.anchorNode
        : selection.anchorNode.parentElement;
    return !!node.closest(selector);
  }
  const range = selection.getRangeAt(0),
    walker = document.createTreeWalker(noteBox, NodeFilter.SHOW_TEXT);
  let node,
    found = false;
  while ((node = walker.nextNode())) {
    if (!range.intersectsNode(node) || !node.length) continue;
    if (node === range.startContainer && range.startOffset === node.length)
      continue;
    if (node === range.endContainer && range.endOffset === 0) continue;
    found = true;
    if (!node.parentElement.closest(selector)) return false;
  }
  return found;
}
function updateFormatButtons() {
  for (const kind of ["bold", "highlight", "list", "numbered", "checklist"]) {
    const button = document.querySelector(`[data-format="${kind}"]`);
    button?.setAttribute("aria-pressed", String(selectionFormatState(kind)));
  }
}
document.addEventListener("selectionchange", updateFormatButtons);
function formatSelection(kind) {
  const selection = window.getSelection();
  if (selection.isCollapsed) {
    if (kind === "bold") document.execCommand("bold");
    else notice("Select note text to highlight or unhighlight it.");
    updateFormatButtons();
    return;
  }
  const range = selection.getRangeAt(0),
    prefix = document.createRange();
  prefix.selectNodeContents(noteBox);
  prefix.setEnd(range.startContainer, range.startOffset);
  const start = prefix.toString().length,
    end = start + range.toString().length,
    enabled = !selectionFormatState(kind);
  let offset = 0;
  function rebuild(node, bold = false, highlight = false, italic = false) {
    const fragment = document.createDocumentFragment();
    if (node.nodeType === 3) {
      const text = node.textContent,
        base = offset;
      offset += text.length;
      const cuts = [
        0,
        Math.max(0, Math.min(text.length, start - base)),
        Math.max(0, Math.min(text.length, end - base)),
        text.length,
      ].sort((a, b) => a - b);
      for (let i = 1; i < cuts.length; i++) {
        const from = cuts[i - 1],
          to = cuts[i];
        if (to === from) continue;
        const selected = base + from >= start && base + to <= end;
        let piece = document.createTextNode(text.slice(from, to));
        const flags = { bold, highlight, italic };
        if (selected) flags[kind] = enabled;
        for (const [flag, tag] of [
          ["italic", "em"],
          ["bold", "strong"],
          ["highlight", "mark"],
        ])
          if (flags[flag]) {
            const wrapper = document.createElement(tag);
            wrapper.append(piece);
            piece = wrapper;
          }
        fragment.append(piece);
      }
      return fragment;
    }
    if (node.nodeType !== 1) return fragment;
    const tag = node.tagName.toLowerCase();
    bold = bold || ["b", "strong"].includes(tag);
    highlight = highlight || tag === "mark";
    italic = italic || ["i", "em"].includes(tag);
    const inline = ["b", "strong", "mark", "i", "em", "span"].includes(tag),
      target = inline ? fragment : node.cloneNode(false);
    for (const child of node.childNodes)
      target.append(rebuild(child, bold, highlight, italic));
    return target;
  }
  const result = document.createDocumentFragment();
  for (const node of Array.from(noteBox.childNodes))
    result.append(rebuild(node));
  noteBox.replaceChildren(result);
  // Keep the selection so its formatting is visible in the toolbar.
  const walker = document.createTreeWalker(noteBox, NodeFilter.SHOW_TEXT);
  let node,
    total = 0;
  const restored = document.createRange();
  let started = false;
  while ((node = walker.nextNode())) {
    if (!started && total + node.length >= start) {
      restored.setStart(node, start - total);
      started = true;
    }
    if (total + node.length >= end) {
      restored.setEnd(node, end - total);
      break;
    }
    total += node.length;
  }
  selection.removeAllRanges();
  selection.addRange(restored);
  noteRange = restored.cloneRange();
  clearTypingFormat = true;
  noteChanged();
  updateFormatButtons();
}
// Chrome moves the caret to the start of existing text when it wraps it in a list.
function toggleNoteList(command = "insertUnorderedList") {
  const selection = window.getSelection(),
    collapsed = selection.isCollapsed,
    prefix = document.createRange();
  prefix.selectNodeContents(noteBox);
  prefix.setEnd(selection.focusNode, selection.focusOffset);
  const caret = prefix.toString().length;
  document.execCommand(command);
  if (!collapsed) return;
  // A new empty bullet has no text to land in, and Chrome already put the caret there.
  const focus = selection.focusNode,
    line = (focus?.nodeType === 1 ? focus : focus?.parentElement)?.closest("li");
  if (line && !line.textContent.trim()) return;
  const walker = document.createTreeWalker(noteBox, NodeFilter.SHOW_TEXT);
  let node,
    total = 0;
  while ((node = walker.nextNode())) {
    if (total + node.length >= caret) {
      selection.collapse(node, caret - total);
      noteRange = selection.getRangeAt(0).cloneRange();
      return;
    }
    total += node.length;
  }
}
function caretElement() {
  const node = window.getSelection().focusNode;
  return node?.nodeType === 1 ? node : node?.parentElement;
}
function plainListItems(list) {
  list.classList.remove("checklist");
  if (!list.classList.length) list.removeAttribute("class");
  for (const item of list.children) delete item.dataset.checked;
}
function toggleChecklist() {
  const list = caretElement()?.closest("ul");
  if (list?.classList.contains("checklist")) {
    toggleNoteList();
    return;
  }
  if (!list) toggleNoteList();
  caretElement()?.closest("ul")?.classList.add("checklist");
}
// The tick box is drawn in the item's left padding, so it's not text the caret can enter.
function checklistBoxItem(event) {
  const item = event.target.closest?.("ul.checklist > li");
  return item && event.clientX - item.getBoundingClientRect().left < 26 ? item : null;
}
noteBox.addEventListener("mousedown", (event) => {
  if (checklistBoxItem(event)) event.preventDefault();
});
function sparkleChecklistBox(item) {
  if (!item) return;
  const rect = item.getBoundingClientRect();
  if (rect.width > 0 && rect.height > 0) {
    window.intelMagic?.sparkPoint(rect.left + 8, rect.top + Math.min(12, rect.height / 2), {
      tone: "emerald",
      count: 12,
      spread: 44,
    });
  }
}
noteBox.addEventListener("click", (event) => {
  const item = checklistBoxItem(event);
  if (!item || busy) return;
  if (item.dataset.checked === "true") delete item.dataset.checked;
  else {
    item.dataset.checked = "true";
    sparkleChecklistBox(item);
  }
  noteChanged();
});
// Enter copies the item's attributes, and a new task shouldn't start out ticked.
noteBox.addEventListener("input", (event) => {
  if (event.inputType !== "insertParagraph") return;
  const item = caretElement()?.closest("ul.checklist > li");
  if (item && !item.textContent.trim()) delete item.dataset.checked;
});
// Tab nests the item under the one above it; Shift+Tab lifts it a level, taking later siblings along as its children.
// Moving a node resets any live Range inside it, so remember the raw positions instead.
function keepCaret(action) {
  const selection = window.getSelection(),
    saved = selection.rangeCount ? selection.getRangeAt(0) : null,
    points = saved && [saved.startContainer, saved.startOffset, saved.endContainer, saved.endOffset];
  action();
  if (!points) return;
  const range = document.createRange();
  range.setStart(points[0], points[1]);
  range.setEnd(points[2], points[3]);
  selection.removeAllRanges();
  selection.addRange(range);
  noteRange = range.cloneRange();
}
function sublistLike(list) {
  const sub = document.createElement(list.tagName.toLowerCase());
  if (list.classList.contains("checklist")) sub.className = "checklist";
  return sub;
}
function indentListItem(item) {
  const previous = item.previousElementSibling;
  if (previous?.tagName !== "LI") return false;
  const list = item.parentElement;
  keepCaret(() => {
    // Join the sublist already ending the item above, or start one. Its own children come along.
    let sub = previous.lastElementChild;
    if (!sub?.matches("ul,ol")) {
      sub = sublistLike(list);
      previous.append(sub);
    }
    sub.append(item);
  });
  return true;
}
function outdentListItem(item) {
  const list = item.parentElement,
    parentItem = list.parentElement?.closest("li");
  if (!parentItem || !noteBox.contains(parentItem) || parentItem !== list.parentElement)
    return false;
  keepCaret(() => {
    const later = [];
    for (let next = item.nextElementSibling; next; next = next.nextElementSibling)
      later.push(next);
    if (later.length) {
      let sub = [...item.children].find((c) => c.matches("ul,ol"));
      if (!sub) {
        sub = sublistLike(list);
        item.append(sub);
      }
      sub.append(...later);
    }
    parentItem.after(item);
    if (!list.children.length) list.remove();
  });
  return true;
}
function nestedItem() {
  const item = caretElement()?.closest("li");
  return item && noteBox.contains(item) ? item : null;
}
// A 3-column table with a header row; inside a table the button adds a row below instead.
function emptyRow(cells, tag = "td") {
  const row = document.createElement("tr");
  for (let i = 0; i < cells; i++)
    row.append(Object.assign(document.createElement(tag), { innerHTML: "<br>" }));
  return row;
}
function placeCaret(cell) {
  const range = document.createRange(),
    selection = window.getSelection();
  range.selectNodeContents(cell);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  noteRange = range.cloneRange();
}
function insertNoteTable() {
  const current = caretElement()?.closest("tr");
  if (current && noteBox.contains(current)) {
    const row = emptyRow(current.children.length);
    current.after(row);
    placeCaret(row.firstElementChild);
    return;
  }
  const head = emptyRow(3, "th").outerHTML,
    body = emptyRow(3).outerHTML;
  document.execCommand(
    "insertHTML",
    false,
    `<table><tbody>${head}${body}</tbody></table><div><br></div>`,
  );
  const tables = noteBox.querySelectorAll("table"),
    // The new table is the one just before the caret's new line.
    table = [...tables].reverse().find((t) => t.querySelector("th") && !t.textContent.trim()) || tables[tables.length - 1];
  if (table) placeCaret(table.querySelector("th,td"));
}
// Tab and Shift+Tab move between cells; Tab in the last cell adds a row.
function moveTableCell(cell, step) {
  const table = cell.closest("table"),
    cells = [...table.querySelectorAll("th,td")],
    next = cells[cells.indexOf(cell) + step];
  if (next) placeCaret(next);
  else if (step > 0) {
    const row = emptyRow(cell.closest("tr").children.length);
    (table.tBodies[0] || table).append(row);
    placeCaret(row.firstElementChild);
    noteChanged();
  }
}
// Typed arrows become one symbol: -> → , <- ← , => ⇒ , and <-> ↔ . Backspace right after brings the characters back.
const ARROWS = [["←>", "↔"], ["->", "→"], ["<-", "←"], ["=>", "⇒"]];
let lastArrow = null;
noteBox.addEventListener("input", (event) => {
  if (event.inputType !== "insertText" || !["-", ">"].includes(event.data)) {
    if (event.inputType) lastArrow = null;
    return;
  }
  const selection = window.getSelection(),
    node = selection.focusNode,
    offset = selection.focusOffset;
  if (!selection.isCollapsed || node?.nodeType !== 3 || node.parentElement.closest("code,pre")) return;
  const before = node.textContent.slice(0, offset),
    match = ARROWS.find(([typed]) => before.endsWith(typed));
  if (!match) return;
  const [typed, symbol] = match,
    original = typed === "←>" ? "<->" : typed,
    range = document.createRange();
  range.setStart(node, offset - typed.length);
  range.setEnd(node, offset);
  selection.removeAllRanges();
  selection.addRange(range);
  document.execCommand("insertText", false, symbol);
  const after = window.getSelection();
  lastArrow = { node: after.focusNode, offset: after.focusOffset, original };
});
function undoArrow() {
  const selection = window.getSelection();
  if (!lastArrow || !selection.isCollapsed || selection.focusNode !== lastArrow.node || selection.focusOffset !== lastArrow.offset)
    return false;
  const range = document.createRange();
  range.setStart(lastArrow.node, lastArrow.offset - 1);
  range.setEnd(lastArrow.node, lastArrow.offset);
  selection.removeAllRanges();
  selection.addRange(range);
  const original = lastArrow.original;
  lastArrow = null;
  document.execCommand("insertText", false, original);
  return true;
}
let clearTypingFormat = false;
noteBox.addEventListener("beforeinput", (event) => {
  if (
    !clearTypingFormat ||
    !["insertText", "insertParagraph", "insertLineBreak"].includes(
      event.inputType,
    )
  )
    return;
  clearTypingFormat = false;
  const selection = window.getSelection();
  event.preventDefault();
  const range = selection.getRangeAt(0);
  range.deleteContents();
  range.collapse(true);
  const marker = document.createTextNode("");
  range.insertNode(marker);
  let parent = marker.parentElement;
  while (
    parent !== noteBox &&
    ["B", "STRONG", "MARK", "I", "EM", "SPAN"].includes(parent.tagName)
  ) {
    const after = parent.cloneNode(false);
    while (marker.nextSibling) after.append(marker.nextSibling);
    parent.after(marker, after);
    if (!parent.hasChildNodes()) parent.remove();
    if (!after.hasChildNodes()) after.remove();
    parent = marker.parentElement;
  }
  marker.textContent =
    event.inputType === "insertText" ? event.data || "" : "\n";
  range.setStartAfter(marker);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  noteRange = range.cloneRange();
  noteChanged();
  updateFormatButtons();
});
document.querySelectorAll("[data-format]").forEach((button) => {
  button.addEventListener("mousedown", (e) => e.preventDefault());
  button.onclick = () => {
    if (busy) return;
    focusNotes();
    const action = button.dataset.format;
    if (action === "callout") {
      document.execCommand("formatBlock", false, "blockquote");
      noteChanged();
    } else if (action === "list") {
      const checklist = caretElement()?.closest("ul.checklist");
      if (checklist) plainListItems(checklist);
      else toggleNoteList();
      noteChanged();
      updateFormatButtons();
    } else if (action === "numbered") {
      // A checklist turns into plain numbered items; Chrome swaps the list type in place.
      const checklist = caretElement()?.closest("ul.checklist");
      if (checklist) plainListItems(checklist);
      toggleNoteList("insertOrderedList");
      noteChanged();
      updateFormatButtons();
    } else if (action === "table") {
      insertNoteTable();
      noteChanged();
    } else if (action === "checklist") {
      toggleChecklist();
      noteChanged();
      updateFormatButtons();
    } else formatSelection(action);
  };
});
noteBox.addEventListener("keydown", (event) => {
  if (event.key === "Tab" && !event.metaKey && !event.ctrlKey && !event.altKey) {
    const cell = caretElement()?.closest("td,th");
    if (cell && noteBox.contains(cell)) {
      event.preventDefault();
      moveTableCell(cell, event.shiftKey ? -1 : 1);
      return;
    }
    const item = nestedItem();
    if (item) {
      // Keep Tab inside the list even when the item can't move, so focus doesn't jump away.
      event.preventDefault();
      if (event.shiftKey ? outdentListItem(item) : indentListItem(item)) {
        noteChanged();
        updateFormatButtons();
      }
      return;
    }
  }
  // Enter on an empty nested item steps it out one level, like a word processor.
  if (event.key === "Enter" && !event.shiftKey && !event.metaKey && !event.ctrlKey && !event.altKey) {
    const item = nestedItem();
    if (item && !item.textContent.trim() && !item.querySelector("ul,ol") && outdentListItem(item)) {
      event.preventDefault();
      noteChanged();
      updateFormatButtons();
      return;
    }
  }
  if (event.key === "Backspace" && undoArrow()) {
    event.preventDefault();
    return;
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "b") {
    event.preventDefault();
    formatSelection("bold");
  }
  // The same tick from the keyboard: Cmd or Ctrl+Enter on the current item.
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    const item = caretElement()?.closest("ul.checklist > li");
    if (!item) return;
    event.preventDefault();
    if (item.dataset.checked === "true") delete item.dataset.checked;
    else {
      item.dataset.checked = "true";
      sparkleChecklistBox(item);
    }
    noteChanged();
  }
});
// Links in an editable box place the caret, so opening one takes a modifier.
noteBox.addEventListener("click", (event) => {
  const link = event.target.closest?.("a");
  if (!link || !(event.metaKey || event.ctrlKey)) return;
  event.preventDefault();
  window.open(link.href, "_blank", "noopener,noreferrer");
});

noteBox.addEventListener("paste", (event) => {
  event.preventDefault();
  document.execCommand(
    "insertText",
    false,
    event.clipboardData.getData("text/plain"),
  );
  noteChanged();
});

const CTI_TEMPLATES = {
  study:
    "<p><strong>Core Concepts &amp; Mental Model:</strong> </p>" +
    "<p><strong>Key Takeaways:</strong> </p>" +
    "<p><strong>Open Questions:</strong></p>" +
    "<ul class=\"checklist\"><li>What still needs deeper digging?</li></ul><div><br></div>",
  bluf:
    "<p><strong>BLUF:</strong> </p>" +
    "<p><strong>Impact &amp; Scope:</strong> </p>" +
    "<p><strong>Detection / Telemetry:</strong> </p>" +
    "<ul class=\"checklist\"><li>Verify exposure / patch status</li><li>Hunt for indicators &amp; TTPs</li></ul><div><br></div>",
  diamond:
    "<table><tbody>" +
    "<tr><th>Facet</th><th>Details</th></tr>" +
    "<tr><td><strong>Adversary</strong></td><td><br></td></tr>" +
    "<tr><td><strong>Capability</strong></td><td><br></td></tr>" +
    "<tr><td><strong>Infrastructure</strong></td><td><br></td></tr>" +
    "<tr><td><strong>Victim / Target</strong></td><td><br></td></tr>" +
    "</tbody></table><div><br></div>",
  detection:
    "<p><strong>Detection &amp; Hunting Ideas</strong></p>" +
    "<ul class=\"checklist\">" +
    "<li>Process / command-line telemetry: </li>" +
    "<li>Network / DNS / proxy signal: </li>" +
    "<li>Persistence / registry / file artifact: </li>" +
    "</ul><div><br></div>",
};

document.querySelectorAll("[data-template]").forEach((button) => {
  button.addEventListener("mousedown", (event) => event.preventDefault());
  button.addEventListener("click", () => {
    if (busy) return;
    const html = CTI_TEMPLATES[button.dataset.template];
    if (!html) return;
    focusNotes();
    document.execCommand("insertHTML", false, html);
    const menu = button.closest("details");
    if (menu) menu.open = false;
    noteChanged();
  });
});

document.addEventListener("click", (event) => {
  for (const menu of document.querySelectorAll(".template-menu[open], .export-menu[open], #article-cite-menu[open]")) {
    if (!menu.contains(event.target)) menu.open = false;
  }
});

// Convert the live notes editor DOM to clean Markdown for clipboard copy or .md file export.
function notesToMarkdown(root = noteBox) {
  function inline(node) {
    if (node.nodeType === 3) return node.textContent.replace(/\u200b/g, "");
    if (node.nodeType !== 1) return "";
    const tag = node.tagName.toLowerCase();
    if (tag === "br") return "\n";
    const inner = [...node.childNodes].map(inline).join("");
    if (tag === "strong" || tag === "b") return inner.trim() ? `**${inner}**` : inner;
    if (tag === "em" || tag === "i") return inner.trim() ? `*${inner}*` : inner;
    if (tag === "mark") return inner.trim() ? `==${inner}==` : inner;
    if (tag === "code") return `\`${inner.replace(/`/g, "\\`")}\``;
    if (tag === "a") {
      const href = noteLink(node);
      return href ? `[${inner || href}](${href})` : inner;
    }
    return inner;
  }
  function renderList(list, depth) {
    const ordered = list.tagName.toLowerCase() === "ol";
    const checklist = !ordered && list.classList.contains("checklist");
    const indent = "  ".repeat(depth);
    let idx = 1;
    const lines = [];
    for (const child of list.children) {
      if (child.tagName.toLowerCase() !== "li") continue;
      const prefix = checklist
        ? child.dataset.checked === "true"
          ? "- [x] "
          : "- [ ] "
        : ordered
          ? `${idx++}. `
          : "- ";
      const textParts = [];
      const subLists = [];
      for (const n of child.childNodes) {
        if (n.nodeType === 1 && ["UL", "OL"].includes(n.tagName)) subLists.push(n);
        else textParts.push(inline(n));
      }
      const line = textParts.join("").replace(/\s*\n\s*/g, " ").trim();
      lines.push(`${indent}${prefix}${line}`);
      for (const sub of subLists) lines.push(renderList(sub, depth + 1));
    }
    return lines.join("\n");
  }
  function renderTable(table) {
    const rows = [...table.querySelectorAll("tr")].map((tr) =>
      [...tr.children]
        .filter((c) => ["TH", "TD"].includes(c.tagName))
        .map((c) => [...c.childNodes].map(inline).join("").replace(/\s*\n\s*/g, " ").replace(/\|/g, "\\|").trim()),
    );
    if (!rows.length) return "";
    const cols = Math.max(...rows.map((r) => r.length));
    const pad = (r) => Array.from({ length: cols }, (_, i) => r[i] || "");
    const header = pad(rows[0]);
    const out = [`| ${header.join(" | ")} |`, `| ${header.map(() => "---").join(" | ")} |`];
    for (const row of rows.slice(1)) out.push(`| ${pad(row).join(" | ")} |`);
    return out.join("\n");
  }
  function block(node) {
    if (node.nodeType === 3) {
      const txt = node.textContent.replace(/\u200b/g, "").trim();
      return txt ? `${txt}\n\n` : "";
    }
    if (node.nodeType !== 1) return "";
    const tag = node.tagName.toLowerCase();
    if (tag === "ul" || tag === "ol") return `${renderList(node, 0)}\n\n`;
    if (tag === "table") return `${renderTable(node)}\n\n`;
    if (tag === "pre") return `\`\`\`\n${node.textContent.replace(/\u200b/g, "").trimEnd()}\n\`\`\`\n\n`;
    if (tag === "blockquote") {
      const inner = [...node.childNodes].map(block).join("").trim() || inline(node).trim();
      return inner ? inner.split("\n").map((l) => `> ${l}`).join("\n") + "\n\n" : "";
    }
    if (tag === "p" || tag === "div") {
      const hasBlocks = [...node.children].some((c) => ["UL", "OL", "TABLE", "PRE", "BLOCKQUOTE", "P", "DIV"].includes(c.tagName));
      if (hasBlocks) return [...node.childNodes].map(block).join("");
      const txt = [...node.childNodes].map(inline).join("").trim();
      return txt ? `${txt}\n\n` : "";
    }
    const txt = inline(node).trim();
    return txt ? `${txt}\n\n` : "";
  }
  return [...root.childNodes]
    .map(block)
    .join("")
    .replace(/\u00a0/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function notesDocumentTitle() {
  const topicHeading = document.querySelector("#topic-page h1")?.textContent?.trim();
  if (topicHeading) return topicHeading;
  const articleHeading = document.getElementById("article-title")?.textContent?.trim();
  return articleHeading || "Notes";
}

function notesSlug(name) {
  return (name || "notes")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "notes";
}

document.querySelectorAll("[data-notes-copy]").forEach((button) => {
  button.addEventListener("click", async () => {
    const md = notesToMarkdown();
    if (!md) {
      if (typeof notice === "function") notice("No notes to copy yet.");
      return;
    }
    try {
      await navigator.clipboard.writeText(md);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = md;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.append(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    const prev = button.textContent;
    button.textContent = "Copied ✓";
    window.intelMagic?.shimmer(button, { tone: "arcane" });
    setTimeout(() => {
      button.textContent = prev;
    }, 1800);
  });
});

document.querySelectorAll("[data-notes-export]").forEach((button) => {
  button.addEventListener("click", () => {
    const md = notesToMarkdown();
    if (!md) {
      if (typeof notice === "function") notice("No notes to export yet.");
      return;
    }
    const title = notesDocumentTitle();
    const content = `# ${title} — Notes\n\n${md}\n`;
    const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${notesSlug(title)}-notes.md`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
});
