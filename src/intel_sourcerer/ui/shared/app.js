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
  function apply() {
    const choice = stored();
    document.documentElement.dataset.theme =
      choice === "auto" ? (dark.matches ? "dark" : "light") : choice;
  }
  apply();
  dark.addEventListener("change", apply);
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
