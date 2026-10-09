"use strict";
const userStoragePrefix = "user:" + document.querySelector('meta[name="user-id"]').content + ":";
const token = document.querySelector('meta[name="csrf-token"]').content;
async function libraryAPI(path, data) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: JSON.stringify(data),
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(result.error || "Unable to complete this action.");
  return result;
}
const deleteButton = document.querySelector(".lib-delete"),
  dialog = document.getElementById("delete-dialog"),
  confirmDelete = document.getElementById("confirm-delete"),
  cancelDelete = document.getElementById("cancel-delete"),
  deleteError = document.getElementById("delete-error");
if (deleteButton)
  deleteButton.addEventListener("click", () => {
    deleteError.hidden = true;
    dialog.showModal();
  });
cancelDelete.addEventListener("click", () => dialog.close());
dialog.addEventListener("cancel", (event) => {
  if (confirmDelete.disabled) event.preventDefault();
});
confirmDelete.addEventListener("click", async () => {
  confirmDelete.disabled = true;
  cancelDelete.disabled = true;
  deleteError.hidden = true;
  try {
    await libraryAPI("/api/delete-article", { url: deleteButton.dataset.url });
    try {
      localStorage.removeItem(userStoragePrefix + "intel-draft:" + deleteButton.dataset.url);
    } catch {}
    const url = new URL(location.href);
    url.searchParams.delete("selected");
    location.assign(url);
  } catch (error) {
    deleteError.textContent = error.message;
    deleteError.hidden = false;
    confirmDelete.disabled = false;
    cancelDelete.disabled = false;
  }
});
const drop = document.getElementById("library-drop"),
  addForm = document.getElementById("library-add-form"),
  addButton = document.getElementById("library-add"),
  articleURL = document.getElementById("library-url"),
  feedback = document.getElementById("library-feedback");
let importing = false;
async function addLinks(text) {
  if (importing) return;
  const urls = [
    ...new Set(( /^(?:file:\/\/|\/|~\/)/i.test(text.trim()) ? text.trim().split(/\r?\n/).filter(Boolean) : text.split(/\s+/).filter((url) => /^https?:\/\//i.test(url)) )),
  ].slice(0, 20);
  feedback.hidden = false;
  if (!urls.length) {
    feedback.textContent = "Drop or paste an HTTP or HTTPS article link.";
    return;
  }
  importing = true;
  addButton.disabled = true;
  let added = 0,
    lastURL = null,
    errors = [];
  for (const url of urls) {
    feedback.textContent = `Fetching article ${added + errors.length + 1} of ${urls.length}…`;
    try {
      const result = await libraryAPI("/api/link", { url });
      lastURL = result.url;
      added++;
    } catch (error) {
      errors.push(error.message);
    }
  }
  importing = false;
  addButton.disabled = false;
  if (errors.length) {
    feedback.textContent = `${added} articles added. ${errors.join(" ")}`;
    if (added) {
      const refresh = document.createElement("a");
      refresh.href = "/library";
      refresh.textContent = " View library";
      feedback.append(refresh);
    }
    return;
  }
  const destination = new URL("/library", location.origin);
  destination.searchParams.set("selected", lastURL);
  location.assign(destination);
}
addForm.addEventListener("submit", (event) => {
  event.preventDefault();
  addLinks(articleURL.value);
});
drop.addEventListener("dragover", (event) => {
  event.preventDefault();
  drop.classList.add("drag-over");
});
drop.addEventListener("dragleave", (event) => {
  if (!drop.contains(event.relatedTarget)) drop.classList.remove("drag-over");
});
drop.addEventListener("drop", (event) => {
  event.preventDefault();
  drop.classList.remove("drag-over");
  if (event.dataTransfer.files.length) {
    addFiles(Array.from(event.dataTransfer.files));
    return;
  }
  addLinks(
    event.dataTransfer.getData("text/uri-list") ||
      event.dataTransfer.getData("text/plain"),
  );
});

// Finder drops provide file bytes, rather than a usable local filesystem path.
async function addFiles(files) {
  addButton.disabled = true;
  const feedback = document.getElementById("library-feedback");
  feedback.hidden = false;
  let lastURL = null;
  try {
    for (const file of files.slice(0, 20)) {
      if (!/\.(pdf|mp3|m4a|ogg|wav|mp4|webm|eml|pptx)$/i.test(file.name)) throw new Error("Unsupported file type.");
      if (file.size > (/\.(pdf|eml|pptx)$/i.test(file.name) ? 20000000 : 100000000)) throw new Error("Documents are limited to 20 MB; audio/video to 100 MB.");
      feedback.textContent = `Importing ${file.name}…`;
      const response = await fetch("/api/file-upload", {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream", "X-CSRF-Token": token, "X-Filename": encodeURIComponent(file.name) },
        body: file,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "File import failed.");
      lastURL = result.url;
    }
    feedback.textContent = "Files added.";
    if (lastURL) location.assign("/library?selected=" + encodeURIComponent(lastURL));
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    addButton.disabled = false;
  }
}

const renameButton = document.querySelector('.lib-rename');
const renameDialog = document.getElementById('rename-dialog');
const renameInput = document.getElementById('article-new-title');
const renameError = document.getElementById('rename-error');
const renameSave = document.getElementById('save-rename');
renameButton?.addEventListener('click', () => {
  renameInput.value = document.querySelector('.lib-detail > h2').textContent;
  renameError.hidden = true;
  renameDialog.showModal();
  renameInput.focus();
  renameInput.select();
});
document.getElementById('cancel-rename').addEventListener('click', () => renameDialog.close());
document.getElementById('rename-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  renameSave.disabled = true;
  try {
    await libraryAPI('/api/rename-article', {url: renameButton.dataset.url, title: renameInput.value});
    location.reload();
  } catch (error) {
    renameError.textContent = error.message;
    renameError.hidden = false;
  } finally {
    renameSave.disabled = false;
  }
});
