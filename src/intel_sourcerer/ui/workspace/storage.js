// Storage helpers that automatically switch between localStorage (saved accounts)
// and sessionStorage (temporary / guest tabs).

export function isTemporarySession() {
  return document.body.classList.contains("temporary-session");
}

export function storagePrefix() {
  const meta = document.querySelector('meta[name="user-id"]');
  return "user:" + (meta ? meta.content : "local") + ":";
}

export function getStorage() {
  return isTemporarySession() ? sessionStorage : localStorage;
}

export function draftKey(url) {
  return `${storagePrefix()}intel-draft:${url}`;
}

export function positionKey(url) {
  return `${storagePrefix()}intel-position:${url}`;
}

export function readItem(key) {
  try {
    return getStorage().getItem(key);
  } catch {
    return null;
  }
}

export function writeItem(key, value) {
  try {
    getStorage().setItem(key, value);
  } catch {}
}

export function removeItem(key) {
  try {
    getStorage().removeItem(key);
  } catch {}
}

export function readJSON(key) {
  try {
    const raw = getStorage().getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function writeJSON(key, value) {
  try {
    getStorage().setItem(key, JSON.stringify(value));
  } catch {}
}
