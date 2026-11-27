"use strict";
// Loaded before first paint on every page, so the theme never flashes.
(() => {
  const dark = window.matchMedia("(prefers-color-scheme: dark)");
  const stored = () => {
    try {
      const value = localStorage.getItem("intel-theme");
      if (value === "light" || value === "dark") return value;
    } catch {}
    return dark.matches ? "dark" : "light";
  };
  const LABELS = { light: "☀ Light", dark: "☾ Dark" };
  function syncButtons() {
    const choice = stored();
    document.querySelectorAll("[data-theme-cycle]").forEach((btn) => {
      btn.textContent = LABELS[choice] || LABELS.light;
      btn.setAttribute("aria-label", `Color theme: ${choice}. Click to switch to ${choice === "dark" ? "light" : "dark"}.`);
    });
    const select = document.getElementById("theme-select");
    if (select && select.value !== choice) select.value = choice;
  }
  function apply() {
    const choice = stored();
    document.documentElement.dataset.theme = choice;
    syncButtons();
  }
  apply();
  dark.addEventListener("change", apply);
  window.addEventListener("storage", (event) => {
    if (event.key === "intel-theme") apply();
  });
  document.addEventListener("DOMContentLoaded", () => {
    syncButtons();
    document.querySelectorAll("[data-theme-cycle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const next = stored() === "dark" ? "light" : "dark";
        window.intelTheme.set(next, btn);
      });
    });
  });
  window.intelTheme = {
    get: stored,
    set(choice, originEl = null) {
      const nextScheme = choice === "dark" ? "dark" : "light";
      const prevScheme = document.documentElement.dataset.theme;
      const commit = () => {
        try {
          if (nextScheme === (dark.matches ? "dark" : "light")) {
            localStorage.removeItem("intel-theme");
          } else {
            localStorage.setItem("intel-theme", nextScheme);
          }
        } catch {}
        apply();
      };
      if (originEl && prevScheme !== nextScheme && window.intelMagic?.eclipseTheme) {
        window.intelMagic.eclipseTheme(originEl, commit);
      } else {
        commit();
      }
    },
  };
  // Guest sessions promise nothing is kept, so they stay out of the offline cache.
  if ("serviceWorker" in navigator && !/^\/(guest|temporary)/.test(location.pathname))
    navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
