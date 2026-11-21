// Text anchoring, fuzzy occurrence matching, and DOM <mark> rendering for highlights.

// Offsets into the article's text, with context to tell repeated phrases apart.
export function passageAnchor(range, bodyEl) {
  if (!bodyEl.contains(range.startContainer) || !bodyEl.contains(range.endContainer)) return null;
  const before = document.createRange();
  before.selectNodeContents(bodyEl);
  before.setEnd(range.startContainer, range.startOffset);
  const raw = range.toString(),
    quote = raw.trim(),
    start = before.toString().length + (raw.length - raw.trimStart().length),
    all = bodyEl.textContent;
  if (!quote || quote.length > 20000) return null;
  return {
    quote,
    prefix: all.slice(Math.max(0, start - 60), start),
    suffix: all.slice(start + quote.length, start + quote.length + 60),
  };
}

export function clearHighlightMarks(bodyEl) {
  for (const mark of bodyEl.querySelectorAll("mark.article-highlight"))
    mark.replaceWith(...mark.childNodes);
  for (const math of bodyEl.querySelectorAll(".article-highlight-math"))
    math.classList.remove("article-highlight-math");
  bodyEl.normalize();
}

// A repeated phrase resolves to the occurrence whose surrounding text matches best.
export function locateHighlight(all, highlight) {
  let best = -1,
    bestScore = -1,
    index = -1;
  while ((index = all.indexOf(highlight.quote, index + 1)) !== -1) {
    let score = 0;
    const before = highlight.prefix || "",
      after = highlight.suffix || "";
    while (score < before.length && all[index - 1 - score] === before[before.length - 1 - score])
      score++;
    let tail = 0;
    while (tail < after.length && all[index + highlight.quote.length + tail] === after[tail])
      tail++;
    if (score + tail > bestScore) {
      best = index;
      bestScore = score + tail;
    }
  }
  return best;
}

export function markHighlight(bodyEl, start, end, id) {
  const walker = document.createTreeWalker(bodyEl, NodeFilter.SHOW_TEXT),
    pieces = [];
  let node,
    offset = 0;
  while ((node = walker.nextNode())) {
    const from = Math.max(start, offset),
      to = Math.min(end, offset + node.length);
    if (from < to) pieces.push([node, from - offset, to - offset]);
    offset += node.length;
  }
  for (const [text, from, to] of pieces) {
    // Marks inside MathML would break the equation, so the whole formula is tinted instead.
    const math = text.parentElement.closest(".article-math");
    if (math) {
      math.classList.add("article-highlight-math");
      math.dataset.highlightId = id;
      continue;
    }
    if (!text.data.slice(from, to).trim()) continue;
    const middle = text.splitText(from);
    middle.splitText(to - from);
    const mark = document.createElement("mark");
    mark.className = "article-highlight";
    mark.dataset.highlightId = id;
    middle.replaceWith(mark);
    mark.append(middle);
  }
}
