// Server timestamps preserve elapsed time across refreshes. Only running time counts.
(() => {
  const element = (id) => document.getElementById(id);
  let article = null,
    session = null,
    receivedAt = 0,
    busy = false;
  const terminal = () =>
    !session || ["completed", "abandoned"].includes(session.status);
  function elapsed() {
    return (
      (session?.elapsed_seconds || 0) +
      (session?.status === "running"
        ? (performance.now() - receivedAt) / 1000
        : 0)
    );
  }
  function clock(seconds) {
    const value = Math.ceil(Math.abs(seconds));
    return `${Math.floor(value / 60)
      .toString()
      .padStart(2, "0")}:${(value % 60).toString().padStart(2, "0")}`;
  }
  function render() {
    if (!article) return;
    const active = !terminal(),
      same = active && session.article_url === article.url;
    element("focus-minutes").disabled = busy || active;
    element("focus-start").hidden = active;
    element("focus-pause").hidden = !same;
    element("focus-finish").hidden = !same;
    element("focus-end").hidden = !active;
    for (const id of [
      "focus-start",
      "focus-pause",
      "focus-finish",
      "focus-end",
    ])
      element(id).disabled = busy;
    element("focus-pause").textContent =
      session?.status === "paused" ? "Resume" : "Pause";
    if (same) {
      element("focus-minutes").value = session.target_seconds / 60;
      const remaining = session.target_seconds - elapsed();
      element("focus-clock").textContent =
        (remaining < 0 ? "+" : "") + clock(remaining);
      element("focus-clock").classList.toggle("overtime", remaining < 0);
      element("focus-caption").textContent =
        session.status === "paused"
          ? "Paused · active time only"
          : remaining < 0
            ? "Overtime · keep going"
            : "remaining";
      element("focus-progress").max = session.target_seconds;
      element("focus-progress").value = Math.min(
        elapsed(),
        session.target_seconds,
      );
    } else {
      element("focus-clock").textContent = active
        ? "Timer paused elsewhere"
        : "Ready";
      element("focus-caption").textContent = active
        ? "End that attempt to time this article"
        : "Start when you’re ready";
      element("focus-clock").classList.remove("overtime");
      element("focus-progress").value = 0;
      if (!active && ["audio", "video"].includes(article.content_format)) {
        element("focus-start").disabled = true;
        element("focus-minutes").disabled = true;
        element("focus-caption").textContent = "Use the playback controls below";
      }
    }
  }
  function message(text) {
    element("focus-message").hidden = !text;
    element("focus-message").textContent = text;
  }
  async function request(data) {
    const response = await fetch(
      "/api/timer",
      data
        ? {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')
                .content,
            },
            body: JSON.stringify(data),
          }
        : {},
    );
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Unable to save timer.");
    session = result.session;
    receivedAt = performance.now();
    render();
  }
  async function action(name) {
    if (busy) return;
    busy = true;
    render();
    try {
      if (name === "finish") await saveNow();
      await request({
        action: name,
        id: session?.id,
        url: article.url,
        target_seconds: Math.round(Number(element("focus-minutes").value) * 60),
      });
      if (name === "finish") {
        article.is_read = true;
        currentArticle.is_read = true;
        element("article-meta").textContent = readerMetadata(currentArticle);
        const seconds = session.elapsed_seconds;
        message(
          `Session saved · ${seconds <= session.target_seconds ? "Within target" : "Over target"} · ${clock(seconds)} active · ${Math.round(session.word_count / Math.max(seconds / 60, 1 / 60))} words/min. ${document.body.classList.contains("temporary-session") ? "Timing stays in this tab; export to keep it." : "View your progress in Stats."}`,
        );
      } else if (name === "abandon")
        message(
          "Attempt ended. It won’t count toward your completed-session pace.",
        );
    } catch (error) {
      message(error.message);
    } finally {
      busy = false;
      render();
    }
  }
  window.pauseFocusTimer = async () => {
    if (session?.status === "running")
      await request({ action: "pause", id: session.id });
  };
  document.addEventListener("reader-article", async (event) => {
    article = event.detail;
    message("");
    element("focus-minutes").value = Math.max(
      1,
      parseInt(readingTimeLabel(article).replace("~", ""), 10),
    );
    try {
      await request();
      if (
        session &&
        session.article_url !== article.url &&
        session.status === "running"
      )
        await window.pauseFocusTimer();
    } catch (error) {
      message(error.message);
    }
  });
  element("focus-start").onclick = () => action("start");
  element("focus-pause").onclick = () =>
    action(session?.status === "paused" ? "resume" : "pause");
  element("focus-finish").onclick = () => action("finish");
  element("focus-end").onclick = () => action("abandon");
  // Switching tabs pauses a session, avoiding accidental background reading time.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden)
      window.pauseFocusTimer().catch((error) => message(error.message));
  });
  setInterval(render, 250);
})();
