"use strict";
// Another site can send anyone here, so nothing is saved until the person presses the button.
(() => {
  const $ = (id) => document.getElementById(id);
  const PENDING = "intel-pending-save";
  const params = new URLSearchParams(location.search);
  function show(id) {
    for (const section of ["save-confirm", "save-working", "save-done", "save-signin", "save-setup"])
      $(section).hidden = section !== id;
  }
  function fail(message) {
    $("save-error").textContent = message;
    $("save-error").hidden = !message;
  }
  // Share menus put the link in whichever field they like.
  function sharedURL() {
    for (const value of [params.get("url"), params.get("text"), params.get("title")]) {
      const match = (value || "").match(/https?:\/\/\S+/i);
      if (match) return match[0];
    }
    return "";
  }
  function setup() {
    const target = `${location.origin}/save?url=`;
    const code = `javascript:(function(){var u='${target}'+encodeURIComponent(location.href)+'&title='+encodeURIComponent(document.title);if(!window.open(u,'intel-save','width=520,height=480'))location.href=u})()`;
    $("bookmarklet").setAttribute("href", code);
    $("bookmarklet").addEventListener("click", (event) => event.preventDefault());
    $("shortcut-url").textContent = target;
    show("save-setup");
  }
  async function session() {
    const response = await fetch("/api/viewer-session");
    if (response.status === 404)
      return { token: document.querySelector('meta[name="csrf-token"]').content, signedIn: true };
    const result = await response.json();
    return { token: result.csrf_token, signedIn: !!result.user };
  }
  async function post(path, token, data) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
      body: JSON.stringify(data),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Unable to save this article.");
    return result;
  }
  const url = sharedURL();
  if (!url) {
    setup();
    return;
  }
  const title = params.get("title") && !/^https?:/i.test(params.get("title")) ? params.get("title") : "";
  $("save-title").textContent = title;
  $("save-done-title").textContent = title;
  $("save-url").textContent = url;
  session()
    .then(({ token, signedIn }) => {
      if (!signedIn) {
        try {
          localStorage.setItem(PENDING, location.search);
        } catch {}
        show("save-signin");
        return;
      }
      try {
        localStorage.removeItem(PENDING);
      } catch {}
      show("save-confirm");
      $("save-button").focus();
      $("save-button").onclick = async () => {
        fail("");
        show("save-working");
        try {
          const saved = await post("/api/link", token, { url });
          await post("/api/triage", token, { urls: [saved.url], decision: "keep" });
          $("save-read").href = "/?" + new URLSearchParams({ open: saved.url });
          show("save-done");
          $("save-read").focus();
        } catch (error) {
          fail(error.message);
          show("save-confirm");
        }
      };
    })
    .catch(() => fail("Can't reach Intel Sourcerer. Check your connection and try again."));
  $("save-close").onclick = () => {
    window.close();
    setTimeout(() => location.replace("/"), 150);
  };
})();
