"use strict";
// One quiet line of names with a box at the end, used for tags and for topics.
function mountTokenEditor(box, options) {
  const { id, label: labelText, placeholder, maxLength, post, onError, onChange, read, fileOnComma, newHint } = options;
  const label = document.createElement("label"),
    input = document.createElement("input"),
    add = document.createElement("button"),
    list = document.createElement("datalist"),
    hint = document.createElement("span");
  let url = null,
    current = { items: [], suggestions: [], quick: [] },
    queue = Promise.resolve();
  label.className = "tag-label";
  label.htmlFor = id;
  label.textContent = labelText;
  input.id = id;
  input.type = "text";
  input.maxLength = maxLength;
  input.autocomplete = "off";
  input.setAttribute("list", id + "-suggestions");
  input.setAttribute("aria-label", placeholder);
  list.id = id + "-suggestions";
  input.placeholder = placeholder;
  input.hidden = true;
  add.type = "button";
  add.className = "tag-add";
  add.textContent = "+";
  add.setAttribute("aria-label", placeholder);
  hint.className = "tag-hint";
  hint.hidden = true;
  box.replaceChildren(label, add, input, list, hint);

  function openInput() {
    add.hidden = true;
    input.hidden = false;
    input.focus();
  }
  function closeInput() {
    input.value = "";
    input.hidden = true;
    add.hidden = false;
    updateHint();
  }
  add.addEventListener("click", openInput);
  label.addEventListener("click", openInput);

  function token(item) {
    const wrap = document.createElement("span"),
      link = document.createElement("a"),
      remove = document.createElement("button");
    wrap.className = "tag-token";
    link.href = item.href;
    link.textContent = item.text;
    if (item.title) link.title = item.title;
    if (item.count) {
      const count = document.createElement("span");
      count.className = "tag-count";
      count.textContent = item.count;
      link.append(count);
    }
    remove.type = "button";
    remove.className = "tag-token-remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", item.removeLabel);
    remove.onclick = () => {
      send(item.remove);
    };
    wrap.append(link, remove);
    return wrap;
  }
  function quickButton(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "tag-suggest";
    button.textContent = "+ " + item.text;
    button.title = item.title;
    button.setAttribute("aria-label", `Add to ${item.text}. ${item.title}`);
    button.onclick = () => send(item.add);
    return button;
  }
  function updateHint() {
    const value = input.value.trim().toLowerCase(),
      known = [...current.suggestions, ...current.items.map((item) => item.text)];
    hint.hidden = !newHint || !value || known.some((name) => name.toLowerCase() === value);
    hint.textContent = newHint || "";
  }
  function render(data) {
    current = read(data);
    box.querySelectorAll(".tag-token,.tag-suggest").forEach((item) => item.remove());
    add.before(...current.items.map(token), ...current.quick.map(quickButton));
    list.replaceChildren(
      ...current.suggestions.map((value) => Object.assign(document.createElement("option"), { value })),
    );
    updateHint();
    onChange?.(current.items.map((item) => item.text));
  }
  // In order, so names typed quickly can't overlap and show an older list last.
  function send([path, change]) {
    const target = url;
    queue = queue.then(async () => {
      try {
        const data = await post(path, { url: target, ...change });
        if (url === target) render(data);
      } catch (error) {
        onError(error.message);
      }
    });
  }
  function fileInput() {
    const parts = (fileOnComma ? input.value.split(",") : [input.value]).map((part) => part.trim()).filter(Boolean);
    input.value = "";
    for (const part of parts) send(options.add(part));
    updateHint();
  }
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeInput();
      add.focus();
    } else if (event.key === "Enter" || (fileOnComma && event.key === ",")) {
      event.preventDefault();
      fileInput();
    } else if (event.key === "Tab" && !event.shiftKey && input.value.trim()) {
      event.preventDefault();
      fileInput();
    } else if (event.key === "Backspace" && !event.repeat && !input.value && current.items.length) {
      send(current.items[current.items.length - 1].remove);
    }
  });
  input.addEventListener("input", (event) => {
    // Picking from the suggestions is a finished choice, while typing that happens to match may not be.
    const picked = !event.inputType || event.inputType === "insertReplacementText";
    if ((picked && current.suggestions.includes(input.value)) || (fileOnComma && input.value.includes(","))) fileInput();
    else updateHint();
  });
  // A stray tag is harmless, but clicking away shouldn't start a topic nobody asked for.
  input.addEventListener("blur", () => {
    if (fileOnComma) fileInput();
    if (!input.value.trim()) closeInput();
  });
  return {
    show(articleURL, data) {
      url = articleURL;
      closeInput();
      render(data);
    },
  };
}

function mountTagEditor(box, { post, onError, onChange }) {
  return mountTokenEditor(box, {
    id: "tag-input",
    label: "Tags",
    placeholder: "Add tags, separated by commas",
    maxLength: 400,
    fileOnComma: true,
    post,
    onError,
    onChange,
    add: (tag) => ["/api/tags", { tag }],
    read: (data) => ({
      items: data.tags.map(({ tag, others }) => ({
        text: tag,
        href: "/library?" + new URLSearchParams({ tag }),
        count: others.length,
        title: others.length
          ? `See this and ${others.length} other tagged ${others.length === 1 ? "article" : "articles"}`
          : "See this tag in the Library",
        removeLabel: `Remove tag ${tag}`,
        remove: ["/api/tag-delete", { tag }],
      })),
      suggestions: data.suggestions,
      quick: [],
    }),
  });
}

function mountTopicEditor(box, { post, onError }) {
  return mountTokenEditor(box, {
    id: "topic-input",
    label: "Topics",
    placeholder: "Add to a topic",
    maxLength: 80,
    newHint: "Enter to create",
    post,
    onError,
    add: (name) => ["/api/topic-article", { name }],
    read: (data) => ({
      items: data.topics.map((topic) => ({
        text: topic.name,
        href: "/topic?" + new URLSearchParams({ id: topic.id }),
        removeLabel: `Remove from ${topic.name}`,
        remove: ["/api/topic-article-delete", { id: topic.id }],
      })),
      suggestions: data.names,
      quick: data.suggestions.map((topic) => ({
        text: topic.name,
        title: `Shares ${topic.value} with ${topic.articles} ${topic.articles === 1 ? "article" : "articles"}`,
        add: ["/api/topic-article", { id: topic.id }],
      })),
    }),
  });
}
