"use strict";
const deleteDialog = document.getElementById("delete-account-dialog");
document.getElementById("delete-account")?.addEventListener("click", () => {
  deleteDialog.showModal();
  document.getElementById("delete-account-username").focus();
});
document.getElementById("cancel-delete-account")?.addEventListener("click", () => deleteDialog.close());

const userMeta = document.querySelector('meta[name="user-id"]');
const userPrefix = "user:" + (userMeta ? userMeta.content : "local") + ":";

// Reader text size synced with panes.js (user:<id>:intel-text-scale).
const textSmaller = document.getElementById("account-text-smaller");
const textLarger = document.getElementById("account-text-larger");
const textReset = document.getElementById("account-text-reset");
const textValue = document.getElementById("account-text-value");
if (textSmaller && textLarger && textReset && textValue) {
  const scaleKey = userPrefix + "intel-text-scale";
  function readScale() {
    try {
      const val = Number(localStorage.getItem(scaleKey));
      return val >= 0.85 && val <= 1.5 ? val : 1;
    } catch {
      return 1;
    }
  }
  function renderScale(scale) {
    textValue.textContent = `${Math.round(scale * 100)}%`;
    textSmaller.disabled = scale <= 0.85;
    textLarger.disabled = scale >= 1.5;
    textReset.disabled = Math.abs(scale - 1) < 0.01;
  }
  function setScale(next) {
    const clamped = Math.round(Math.min(1.5, Math.max(0.85, next)) * 100) / 100;
    try {
      localStorage.setItem(scaleKey, String(clamped));
    } catch {}
    renderScale(clamped);
  }
  renderScale(readScale());
  textSmaller.addEventListener("click", () => setScale(readScale() - 0.1));
  textLarger.addEventListener("click", () => setScale(readScale() + 0.1));
  textReset.addEventListener("click", () => setScale(1));
}

// Arcane visual effects preference synced with window.intelMagic.
function initMagicPref() {
  const magicSelect = document.getElementById("account-magic");
  if (!magicSelect) return;
  const fallbackRead = () => {
    try {
      return localStorage.getItem("intel-magic") || "balanced";
    } catch {
      return "balanced";
    }
  };
  magicSelect.value = window.intelMagic ? window.intelMagic.getMode() : fallbackRead();
  magicSelect.addEventListener("change", () => {
    if (window.intelMagic) {
      window.intelMagic.setMode(magicSelect.value);
      if (magicSelect.value !== "off") {
        window.intelMagic.puff(magicSelect, { tone: "arcane", count: 18 });
      }
    } else {
      try {
        localStorage.setItem("intel-magic", magicSelect.value);
      } catch {}
      document.documentElement.dataset.magic = magicSelect.value;
    }
  });
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initMagicPref);
} else {
  initMagicPref();
}

// Offline Service Worker cache inspection and clear action.
const cacheStatus = document.getElementById("account-cache-status");
const clearCacheBtn = document.getElementById("account-clear-cache");
async function refreshCacheStatus() {
  if (!cacheStatus) return;
  if (!("caches" in window)) {
    cacheStatus.textContent = "Offline cache is not available in this browser.";
    if (clearCacheBtn) clearCacheBtn.disabled = true;
    return;
  }
  try {
    const names = await caches.keys();
    let count = 0;
    for (const name of names) {
      const cache = await caches.open(name);
      const keys = await cache.keys();
      count += keys.length;
    }
    cacheStatus.textContent = count
      ? `${count} cached ${count === 1 ? "item" : "items"} stored for offline reading.`
      : "No cached items stored.";
    if (clearCacheBtn) clearCacheBtn.disabled = !count;
  } catch {
    cacheStatus.textContent = "Unable to inspect offline cache.";
  }
}
if (clearCacheBtn) {
  refreshCacheStatus();
  clearCacheBtn.addEventListener("click", async () => {
    clearCacheBtn.disabled = true;
    try {
      const names = await caches.keys();
      await Promise.all(names.map((name) => caches.delete(name)));
      if (cacheStatus) cacheStatus.textContent = "Offline cache cleared.";
    } catch {
      if (cacheStatus) cacheStatus.textContent = "Could not clear offline cache.";
      clearCacheBtn.disabled = false;
    }
  });
}
