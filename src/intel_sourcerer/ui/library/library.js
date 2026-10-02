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
      localStorage.removeItem("intel-draft:" + deleteButton.dataset.url);
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
    ...new Set(text.split(/\s+/).filter((url) => /^https?:\/\//i.test(url))),
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
  addLinks(
    event.dataTransfer.getData("text/uri-list") ||
      event.dataTransfer.getData("text/plain"),
  );
});
