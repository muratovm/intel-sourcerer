// Keyboard-first shortcuts for Home triage and Reader navigation.
// Always ignored while typing in inputs, textareas, selects, contenteditable boxes, or open dialogs.

const $ = (id) => document.getElementById(id);

function isEditable(target) {
  if (!target || target.nodeType !== 1) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

export function initShortcuts({ onTriageKeep, onTriageSkip, onTriageUndo }) {
  const dialog = $("shortcuts-dialog");
  $("close-shortcuts")?.addEventListener("click", () => dialog?.close());

  document.addEventListener("keydown", (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (isEditable(event.target)) return;
    if (document.querySelector("dialog[open]") && event.key !== "?" && event.key !== "Escape") return;

    if (event.key === "?") {
      if (!dialog) return;
      event.preventDefault();
      if (dialog.open) dialog.close();
      else dialog.showModal();
      return;
    }

    if (event.key === "/") {
      if (document.body.classList.contains("temporary-session")) return;
      event.preventDefault();
      location.assign("/library");
      return;
    }

    const inHome = !$("home").hidden;
    const inReader = !$("reader").hidden;

    if (inHome) {
      if (event.key === "k") {
        event.preventDefault();
        onTriageKeep();
      } else if (event.key === "s") {
        event.preventDefault();
        onTriageSkip();
      } else if (event.key === "u") {
        event.preventDefault();
        onTriageUndo();
      }
      return;
    }

    if (inReader) {
      if (event.key === "j") {
        const next = document.querySelector('[data-action="next"]');
        if (next && !next.hidden && !next.disabled) {
          event.preventDefault();
          next.click();
        }
      } else if (event.key === "J") {
        const markNext = document.querySelector('[data-action="mark_next"]');
        if (markNext && !markNext.hidden && !markNext.disabled) {
          event.preventDefault();
          markNext.click();
        }
      } else if (event.key === "p") {
        const prev = document.querySelector('[data-action="previous"]');
        if (prev && !prev.hidden && !prev.disabled) {
          event.preventDefault();
          prev.click();
        }
      } else if (event.key === "h") {
        const hl = $("highlight-selection");
        if (hl && !hl.hidden && !hl.disabled) {
          event.preventDefault();
          hl.click();
        }
      } else if (event.key === "q") {
        const quote = $("quote-selection");
        if (quote && !quote.hidden && !quote.disabled) {
          event.preventDefault();
          quote.click();
        }
      } else if (event.key === "n") {
        event.preventDefault();
        $("notes")?.focus();
      }
    }
  });
}
