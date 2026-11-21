"use strict";
// Loaded before first paint on every page, so the theme never flashes.
(() => {
  const dark = window.matchMedia("(prefers-color-scheme: dark)");
  const stored = () => {
    try {
      return localStorage.getItem("intel-theme") || "auto";
    } catch {
      return "auto";
    }
  };
  const ORDER = ["auto", "light", "dark"];
  const LABELS = { auto: "◐ Auto", light: "☀ Light", dark: "☾ Dark" };
  function syncButtons() {
    const choice = stored();
    document.querySelectorAll("[data-theme-cycle]").forEach((btn) => {
      btn.textContent = LABELS[choice] || LABELS.auto;
      btn.setAttribute("aria-label", `Color theme: ${choice}. Click to switch.`);
    });
    const select = document.getElementById("theme-select");
    if (select && select.value !== choice) select.value = choice;
  }
  function apply() {
    const choice = stored();
    document.documentElement.dataset.theme =
      choice === "auto" ? (dark.matches ? "dark" : "light") : choice;
    syncButtons();
  }
  apply();
  dark.addEventListener("change", apply);
  document.addEventListener("DOMContentLoaded", () => {
    syncButtons();
    document.querySelectorAll("[data-theme-cycle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const next = ORDER[(ORDER.indexOf(stored()) + 1) % ORDER.length];
        window.intelTheme.set(next);
      });
    });
  });
  window.intelTheme = {
    get: stored,
    set(choice) {
      try {
        localStorage.setItem("intel-theme", choice);
      } catch {}
      apply();
    },
  };
  // Guest sessions promise nothing is kept, so they stay out of the offline cache.
  if ("serviceWorker" in navigator && !/^\/(guest|temporary)/.test(location.pathname))
    navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
