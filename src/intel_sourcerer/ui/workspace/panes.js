// Split-pane drag/keyboard resizing and article text zoom controls.

const $ = (id) => document.getElementById(id);

export function initPaneDivider({ storagePrefix, readItem, writeItem, beforeArticleHide, afterArticleShow }) {
  const divider = $("pane-divider"),
    workspace = document.querySelector(".workspace");
  // Dragging this close to either edge (or Home/End, or an arrow at the limit) hides that side's pane.
  const HIDE_WITHIN = 10;
  let panePercent = 50,
    hiddenPane = null, // null, "notes" or "article"
    resizingPointer = null;

  function describe() {
    const value = hiddenPane === "notes" ? 100 : hiddenPane === "article" ? 0 : Math.round(panePercent);
    divider.setAttribute("aria-valuenow", String(value));
    divider.setAttribute(
      "aria-valuetext",
      hiddenPane ? `${hiddenPane === "notes" ? "Notes" : "Article"} hidden` : `Article ${value} percent, notes ${100 - value} percent`,
    );
    divider.title =
      hiddenPane === "notes"
        ? "Notes hidden: drag left or press ← to show them"
        : hiddenPane === "article"
          ? "Article hidden: drag right or press → to show it"
          : "Drag to resize, or all the way to one side to hide a pane; arrow keys adjust; double-click to reset";
  }

  function setHiddenPane(pane, persist = true) {
    if (pane === hiddenPane) return;
    // Hiding the article resets its scroll, so the reading place is carried across.
    if (pane === "article") beforeArticleHide?.();
    const articleWasHidden = hiddenPane === "article";
    hiddenPane = pane;
    workspace.classList.toggle("notes-hidden", pane === "notes");
    workspace.classList.toggle("article-hidden", pane === "article");
    describe();
    if (articleWasHidden) afterArticleShow?.();
    if (persist) writeItem(storagePrefix() + "intel-pane-hidden", pane || "");
  }

  function setPaneWidth(value, persist = true) {
    setHiddenPane(null, persist);
    panePercent = Math.max(30, Math.min(70, value));
    workspace.style.setProperty("--article-width", `${panePercent}fr`);
    workspace.style.setProperty("--notes-width", `${100 - panePercent}fr`);
    describe();
    if (persist) writeItem(storagePrefix() + "intel-pane-width", String(panePercent));
  }

  const stored = Number(readItem(storagePrefix() + "intel-pane-width"));
  if (Number.isFinite(stored) && stored >= 30 && stored <= 70) setPaneWidth(stored, false);
  const storedHidden = readItem(storagePrefix() + "intel-pane-hidden");
  if (storedHidden === "notes" || storedHidden === "article") setHiddenPane(storedHidden, false);
  describe();

  divider.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    resizingPointer = event.pointerId;
    divider.setPointerCapture(event.pointerId);
    divider.focus();
    workspace.classList.add("resizing-panes");
  });
  divider.addEventListener("pointermove", (event) => {
    if (event.pointerId !== resizingPointer) return;
    const bounds = workspace.getBoundingClientRect();
    const value = ((event.clientX - bounds.left - 5) / (bounds.width - 10)) * 100;
    if (value >= 100 - HIDE_WITHIN) setHiddenPane("notes");
    else if (value <= HIDE_WITHIN) setHiddenPane("article");
    else setPaneWidth(value);
  });
  function stopResize() {
    resizingPointer = null;
    workspace.classList.remove("resizing-panes");
  }
  divider.addEventListener("pointerup", stopResize);
  divider.addEventListener("pointercancel", stopResize);
  divider.addEventListener("lostpointercapture", stopResize);
  divider.addEventListener("dblclick", () => setPaneWidth(50));
  divider.addEventListener("keydown", (event) => {
    const changes = { ArrowLeft: -2, ArrowRight: 2 };
    if (event.key in changes) {
      event.preventDefault();
      const left = event.key === "ArrowLeft";
      if (hiddenPane === "notes") {
        if (left) setPaneWidth(70);
      } else if (hiddenPane === "article") {
        if (!left) setPaneWidth(30);
      } else if (!left && panePercent >= 70) setHiddenPane("notes");
      else if (left && panePercent <= 30) setHiddenPane("article");
      else setPaneWidth(panePercent + changes[event.key]);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setHiddenPane(event.key === "Home" ? "article" : "notes");
    } else if (event.key === "Enter") {
      event.preventDefault();
      setPaneWidth(50);
    }
  });
}

export function initTextScale({ readItem, writeItem, articleFraction, restoreFraction, onScaleChange }) {
  let textScale = Number(readItem("intel-text-scale")) || 1;
  function applyTextScale() {
    $("article-body").style.zoom = String(textScale);
    $("text-smaller").disabled = textScale <= 0.85;
    $("text-larger").disabled = textScale >= 1.5;
  }
  function changeTextScale(step) {
    const fraction = articleFraction();
    textScale = Math.round(Math.min(1.5, Math.max(0.85, textScale + step)) * 100) / 100;
    writeItem("intel-text-scale", String(textScale));
    applyTextScale();
    onScaleChange();
    restoreFraction(fraction);
  }
  $("text-smaller").onclick = () => changeTextScale(-0.1);
  $("text-larger").onclick = () => changeTextScale(0.1);
  return { applyTextScale };
}
