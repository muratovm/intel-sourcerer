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
        ? "ul:not(.checklist),ol"
        : kind === "checklist"
          ? "ul.checklist"
          : "mark";
  if (kind === "list" || kind === "checklist") {
    const node =
      selection.anchorNode.nodeType === 1
        ? selection.anchorNode
        : selection.anchorNode.parentElement;
    return !!node.closest(selector);
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
  for (const kind of ["bold", "highlight", "list", "checklist"]) {
    const button = document.querySelector(`[data-format="${kind}"]`);
    button.setAttribute("aria-pressed", String(selectionFormatState(kind)));
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
function toggleNoteList() {
  const selection = window.getSelection(),
    collapsed = selection.isCollapsed,
    prefix = document.createRange();
  prefix.selectNodeContents(noteBox);
  prefix.setEnd(selection.focusNode, selection.focusOffset);
  const caret = prefix.toString().length;
  document.execCommand("insertUnorderedList");
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
noteBox.addEventListener("click", (event) => {
  const item = checklistBoxItem(event);
  if (!item || busy) return;
  if (item.dataset.checked === "true") delete item.dataset.checked;
  else item.dataset.checked = "true";
  noteChanged();
});
// Enter copies the item's attributes, and a new task shouldn't start out ticked.
noteBox.addEventListener("input", (event) => {
  if (event.inputType !== "insertParagraph") return;
  const item = caretElement()?.closest("ul.checklist > li");
  if (item && !item.textContent.trim()) delete item.dataset.checked;
});
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
    } else if (action === "checklist") {
      toggleChecklist();
      noteChanged();
      updateFormatButtons();
    } else formatSelection(action);
  };
});
noteBox.addEventListener("keydown", (event) => {
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
    else item.dataset.checked = "true";
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
