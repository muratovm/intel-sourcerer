// Media embedding, playback speed controls, and transcript timestamp seeking.

export function embedURL(url) {
  const host = url.hostname.toLowerCase();
  if (
    [
      "www.youtube.com",
      "youtube.com",
      "www.youtube-nocookie.com",
      "youtube-nocookie.com",
      "youtu.be",
    ].includes(host)
  ) {
    const id =
      host === "youtu.be"
        ? url.pathname.slice(1)
        : url.pathname.startsWith("/embed/")
          ? url.pathname.split("/")[2]
          : url.searchParams.get("v");
    if (id && /^[a-zA-Z0-9_-]{11}$/.test(id))
      return "https://www.youtube-nocookie.com/embed/" + id;
  }
  if (["vimeo.com", "www.vimeo.com", "player.vimeo.com"].includes(host)) {
    const id = url.pathname.split("/").filter(Boolean).pop();
    if (id && /^\d+$/.test(id)) return "https://player.vimeo.com/video/" + id;
  }
  return null;
}

export function renderMedia({ article, mediaEnabled, bodyEl, insertCallout }) {
  const isVideoArticle =
    ["audio", "video"].includes(article?.content_format) ||
    article?.url.startsWith("https://www.youtube.com/watch?v=");
  if (!mediaEnabled && !isVideoArticle) return;
  for (const block of bodyEl.querySelectorAll("figure[data-media-url]")) {
    let url;
    try {
      url = new URL(block.dataset.mediaUrl, location.origin);
    } catch {
      continue;
    }
    if (!["https:", "http:"].includes(url.protocol)) continue;
    let media;
    if (block.dataset.mediaKind === "image") {
      media = document.createElement("img");
      media.alt =
        block.dataset.mediaDescription === "No description supplied"
          ? ""
          : block.dataset.mediaDescription;
      media.loading = "lazy";
      media.decoding = "async";
    } else if (block.dataset.mediaKind === "audio") {
      media = document.createElement("audio");
      media.controls = true;
      media.preload = "metadata";
    } else {
      const embed = embedURL(url);
      if (embed) {
        media = document.createElement("iframe");
        media.title =
          block.dataset.mediaDescription === "No description supplied"
            ? "Article video"
            : block.dataset.mediaDescription;
        media.loading = "lazy";
        media.setAttribute("allow", "fullscreen; picture-in-picture");
        media.setAttribute(
          "sandbox",
          "allow-scripts allow-same-origin allow-presentation",
        );
        url = new URL(embed);
      } else if (/\.(mp4|webm|ogv|ogg)$/i.test(url.pathname)) {
        media = document.createElement("video");
        media.controls = true;
        media.preload = "metadata";
      } else continue;
    }
    media.className = "article-media";
    // YouTube's player refuses to run (Error 153) unless it can see an origin.
    media.referrerPolicy = media.tagName === "IFRAME" ? "strict-origin" : "no-referrer";
    // An image opens at full size, which keeps diagrams readable on a phone.
    let frame = media;
    if (media.tagName === "IMG") {
      frame = document.createElement("a");
      frame.className = "media-link";
      frame.href = url.href;
      frame.target = "_blank";
      frame.rel = "noopener noreferrer";
      frame.append(media);
      media.addEventListener("load", () => frame.classList.add("media-ready"), { once: true });
    }
    media.addEventListener(
      "error",
      () => {
        frame.remove();
        block.classList.remove("is-loaded");
        const message = document.createElement("p");
        message.className = "media-error";
        message.textContent = "Media could not load.";
        block.prepend(message);
      },
      { once: true },
    );
    media.src = url.href;
    block.prepend(frame);
    block.classList.add("is-loaded");
    if (["AUDIO", "VIDEO"].includes(media.tagName)) {
      const controls = document.createElement("div");
      controls.className = "playback-tools";
      const label = document.createElement("label");
      label.textContent = "Speed ";
      const speed = document.createElement("select");
      for (const rate of [0.75, 1, 1.25, 1.5, 2]) {
        const option = new Option(`${rate}×`, rate, false, rate === 1);
        speed.append(option);
      }
      speed.onchange = () => {
        media.playbackRate = Number(speed.value);
      };
      label.append(speed);
      const timestamp = document.createElement("button");
      timestamp.type = "button";
      timestamp.textContent = "Add timestamp to notes";
      timestamp.onclick = () => {
        const seconds = Math.floor(media.currentTime || 0);
        const time = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
        insertCallout(`At ${time} — `);
      };
      controls.append(label, timestamp);
      media.after(controls);
    }
  }
}

export function transcriptSeconds(text) {
  const parts = text.split(":").map(Number);
  return parts.reduce((total, part) => total * 60 + part, 0);
}

export function seekTo(seconds, bodyEl) {
  const player = bodyEl.querySelector("video, audio");
  if (player) {
    player.currentTime = seconds;
    player.play().catch(() => {});
    player.scrollIntoView({ block: "nearest", behavior: "smooth" });
    return;
  }
  const frame = bodyEl.querySelector("iframe.article-media");
  if (!frame) return;
  const url = new URL(frame.src);
  url.searchParams.set("start", String(seconds));
  url.searchParams.set("autoplay", "1");
  frame.src = url.href;
  frame.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
