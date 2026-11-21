// Split-pane drag/keyboard resizing and article text zoom controls.

const $ = (id) => document.getElementById(id);

export function initPaneDivider({ storagePrefix, readItem, writeItem }) {
  const divider = $("pane-divider"),
    workspace = document.querySelector(".workspace");
  let panePercent = 50,
    resizingPointer = null;

  function setPaneWidth(value, persist = true) {
    panePercent = Math.max(30, Math.min(70, value));
    workspace.style.setProperty("--article-width", `${panePercent}fr`);
    workspace.style.setProperty("--notes-width", `${100 - panePercent}fr`);
    divider.setAttribute("aria-valuenow", String(Math.round(panePercent)));
    divider.setAttribute(
      "aria-valuetext",
      `Article ${Math.round(panePercent)} percent, notes ${100 - Math.round(panePercent)} percent`,
    );
    if (persist) writeItem(storagePrefix() + "intel-pane-width", String(panePercent));
  }

  const stored = Number(readItem(storagePrefix() + "intel-pane-width"));
  if (Number.isFinite(stored) && stored >= 30 && stored <= 70) setPaneWidth(stored, false);

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
    setPaneWidth(((event.clientX - bounds.left - 5) / (bounds.width - 10)) * 100);
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
      setPaneWidth(panePercent + changes[event.key]);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setPaneWidth(event.key === "Home" ? 30 : 70);
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
