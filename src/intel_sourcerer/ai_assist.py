"""Optional, strictly opt-in AI assist for threat intel analysis (OpenAI-compatible & Anthropic APIs)."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
import httpx

MAX_ARTICLE_CHARS = 24_000
VALID_MODES = {"bluf", "ttps", "hunt", "ask"}
MAX_QUESTION_CHARS = 500
MODE_LABELS = {"bluf": "BLUF summary", "ttps": "Implied TTPs", "hunt": "Starter hunts", "ask": "Answer"}

# One-click starting points for the Account page. Local servers keep the article on this machine.
PRESETS = {
    "lmstudio": {"label": "LM Studio (this Mac)", "provider": "openai", "url": "http://127.0.0.1:1234/v1"},
    "ollama": {"label": "Ollama (this Mac)", "provider": "openai", "url": "http://127.0.0.1:11434/v1"},
    "anthropic": {"label": "Anthropic API", "provider": "anthropic", "url": "https://api.anthropic.com"},
    "openai": {"label": "OpenAI-compatible API", "provider": "openai", "url": "https://api.openai.com/v1"},
}

PROMPTS = {
    "bluf": (
        "You are a senior cyber threat intelligence analyst. Produce a concise BLUF (Bottom Line Up Front) "
        "briefing from this report using clean Markdown with short headings:\n"
        "- **BLUF** (2-3 sentences: who/what, impact, why it matters)\n"
        "- **Key Technical Details** (bullet points)\n"
        "- **Affected Products / Scope**\n"
        "- **Immediate Mitigations & Recommendations**\n"
        "Stick strictly to facts in the article. Do not invent IOCs or attribution."
    ),
    "ttps": (
        "You are a MITRE ATT&CK specialist. Identify attacker behaviors described in this report — especially "
        "implied behaviors that the article did not explicitly tag with an ATT&CK ID. Return a concise Markdown list "
        "mapping each observed behavior to its most likely MITRE ATT&CK tactic and technique ID (e.g., `T1059.001`), "
        "citing a short quote or phrase from the text as evidence. Flag uncertain mappings as *(tentative)*."
    ),
    "hunt": (
        "You are a detection engineering specialist. Based on the attacker tradecraft, file paths, commands, "
        "registry changes, and network behaviors in this report, suggest 2-3 high-signal threat hunting or "
        "detection ideas. For each, include:\n"
        "- **Hypothesis & Telemetry** (e.g., Process Creation, DNS, Proxy, Registry)\n"
        "- **Starter Query / Rule** (brief Sigma YAML or KQL snippet)\n"
        "- **False-Positive Tuning Note**\n"
        "Focus on behavioral signals rather than brittle single hashes."
    ),
    "ask": (
        "You are a senior cyber threat intelligence analyst answering a question about one report. "
        "Answer only from the report text. Quote the sentence that supports each claim. If the report "
        "does not say, answer that it does not say rather than guessing. Keep the answer short and use Markdown."
    ),
}


def settings_path():
    return Path(os.environ.get("INTEL_AI_SETTINGS", "data/ai_settings.json"))


def load_settings():
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(form, *, keep_key=True):
    """Store what the Account page sends. A blank key keeps the saved one; the key never goes back to a page."""
    provider = form.get("provider", "openai")
    if provider not in {"openai", "anthropic"}:
        raise ValueError("Choose an OpenAI-compatible or Anthropic provider.")
    url = (form.get("url") or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Enter the server address, starting with http:// or https://.")
    model = (form.get("model") or "").strip()[:200]
    if not model:
        raise ValueError("Choose or type a model name.")
    current = load_settings()
    key = (form.get("api_key") or "").strip()
    settings = {
        "enabled": form.get("enabled", "1") == "1",
        "provider": provider,
        "url": url,
        "model": model,
        "api_key": key or (current.get("api_key", "") if keep_key else ""),
    }
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)
    return settings


def is_local(url):
    host = (urlsplit(url or "").hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local") or host.startswith("127.")


def list_models(provider, url, api_key=""):
    """Model names the server offers, so the Account page can offer a menu instead of a guess."""
    url = (url or "").rstrip("/")
    with httpx.Client(timeout=8, trust_env=False) as client:
        if provider == "anthropic":
            response = client.get(url + "/v1/models", headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        else:
            headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
            response = client.get(url + "/models", headers=headers)
        response.raise_for_status()
        data = response.json().get("data") or []
    return sorted({str(m.get("id")) for m in data if isinstance(m, dict) and m.get("id")})


def ai_config():
    """Return configured AI provider details, or None when AI assist is disabled.

    Settings saved from the Account page win; environment variables remain a fallback for deployments.
    """
    saved = load_settings()
    if saved.get("url") and saved.get("model"):
        if not saved.get("enabled", True):
            return None
        return {"provider": saved.get("provider", "openai"), "url": saved["url"].rstrip("/"),
                "api_key": saved.get("api_key", ""), "model": saved["model"]}
    provider = os.environ.get("INTEL_AI_PROVIDER", "").strip().lower()
    openai_url = os.environ.get("INTEL_AI_URL", "").strip()
    openai_key = os.environ.get("INTEL_AI_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    model = os.environ.get("INTEL_AI_MODEL", "").strip()

    if not provider:
        if openai_url or openai_key:
            provider = "openai"
        elif anthropic_key:
            provider = "anthropic"
        else:
            return None

    if provider == "anthropic":
        key = anthropic_key or openai_key
        if not key:
            return None
        return {
            "provider": "anthropic",
            "url": (openai_url or "https://api.anthropic.com").rstrip("/"),
            "api_key": key,
            "model": model or "claude-sonnet-4-5",
        }
    if provider in {"openai", "ollama", "openai-compatible"}:
        url = (openai_url or ("https://api.openai.com/v1" if openai_key else "")).rstrip("/")
        if not url:
            return None
        return {
            "provider": "openai",
            "url": url,
            "api_key": openai_key,
            "model": model or ("gpt-4o-mini" if "api.openai.com" in url else "llama3.1"),
        }
    return None


def ai_status():
    cfg = ai_config()
    if not cfg:
        return {"enabled": False, "provider": None, "model": None}
    return {"enabled": True, "provider": cfg["provider"], "model": cfg["model"], "local": is_local(cfg["url"])}


TECHNIQUE_ID = re.compile(r"\b(?:T|TA)\d{4}(?:\.\d{3})?\b|\bAML\.T\d{4}(?:\.\d{3})?\b")


def check_attack_ids(markdown):
    """Small models misremember ATT&CK IDs, so every ID in the answer is checked against the bundled ATT&CK data."""
    from .iocs import _attack_names

    names, retired = _attack_names()
    checks, seen = [], set()
    for match in TECHNIQUE_ID.finditer(markdown or ""):
        value = match.group(0)
        if value in seen:
            continue
        seen.add(value)
        status = "retired" if value in retired else "ok" if value in names else "unknown"
        checks.append({"id": value, "name": names.get(value, ""), "status": status})
    return checks


def _results_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS ai_results (
      article_url TEXT NOT NULL, mode TEXT NOT NULL, question TEXT NOT NULL DEFAULT '',
      provider TEXT NOT NULL, model TEXT NOT NULL, markdown TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY(article_url, mode))""")


def _present(row):
    from .content import render_markdown

    return {
        "mode": row["mode"], "label": MODE_LABELS.get(row["mode"], row["mode"]), "question": row["question"],
        "provider": row["provider"], "model": row["model"], "markdown": row["markdown"],
        "html": render_markdown(row["markdown"]), "created_at": row["created_at"],
        "checks": check_attack_ids(row["markdown"]),
    }


def saved_results(db, url):
    """Earlier answers for an article, newest first, so reopening it doesn't cost another model call."""
    _results_table(db)
    rows = db.execute(
        "SELECT * FROM ai_results WHERE article_url=? ORDER BY created_at DESC", (url,)).fetchall()
    return [_present(row) for row in rows]


def _article_plaintext(row):
    text = row["content_text"] or ""
    if not text and row["content_html"]:
        text = BeautifulSoup(row["content_html"], "html.parser").get_text("\n", strip=True)
    elif not text and row["content_markdown"]:
        text = row["content_markdown"]
    return text[:MAX_ARTICLE_CHARS]


def run_ai_assist(db, url, mode, question=""):
    """Run an opt-in AI analysis pass on a saved article. Never called automatically."""
    if mode not in VALID_MODES:
        raise ValueError("Choose bluf, ttps, hunt, or ask.")
    question = " ".join(str(question or "").split())[:MAX_QUESTION_CHARS]
    if mode == "ask" and not question:
        raise ValueError("Type a question about this article first.")
    cfg = ai_config()
    if not cfg:
        raise ValueError(
            "AI assist is not configured. Set INTEL_AI_URL (e.g. Ollama) or OPENAI_API_KEY / ANTHROPIC_API_KEY."
        )
    row = db.execute(
        "SELECT title, url, content_text, content_html, content_markdown FROM articles WHERE url=?",
        (url,),
    ).fetchone()
    if not row:
        raise ValueError("Article not found.")
    body = _article_plaintext(row)
    if not body.strip():
        raise ValueError("This article has no saved text to analyze.")

    system_prompt = PROMPTS[mode]
    user_prompt = f"Title: {row['title']}\nURL: {row['url']}\n\nArticle text:\n{body}"
    if mode == "ask":
        user_prompt += f"\n\nQuestion: {question}"

    # Trust explicit admin-configured endpoint (which may be local Ollama on 127.0.0.1).
    # Local models on a laptop can take a few minutes on a long report.
    with httpx.Client(timeout=300 if is_local(cfg["url"]) else 120, trust_env=False) as client:
        if cfg["provider"] == "anthropic":
            response = client.post(
                cfg["url"] + "/v1/messages",
                headers={
                    "x-api-key": cfg["api_key"],
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": cfg["model"],
                    "max_tokens": 1200,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            response.raise_for_status()
            payload = response.json()
            blocks = [b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"]
            text = "\n\n".join(b.strip() for b in blocks if b.strip())
        else:
            headers = {"content-type": "application/json"}
            if cfg["api_key"]:
                headers["authorization"] = f"Bearer {cfg['api_key']}"
            endpoint = cfg["url"] if cfg["url"].endswith("/chat/completions") else cfg["url"] + "/chat/completions"
            response = client.post(
                endpoint,
                headers=headers,
                json={
                    "model": cfg["model"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
            choices = payload.get("choices") or []
            text = (choices[0].get("message", {}).get("content") or "").strip() if choices else ""

    if not text:
        raise ValueError("AI provider returned an empty response.")
    # Reasoning models wrap their scratch work in <think>; only the answer is kept.
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or text
    _results_table(db)
    created = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT OR REPLACE INTO ai_results VALUES (?,?,?,?,?,?,?)",
        (url, mode, question, cfg["provider"], cfg["model"], text, created),
    )
    db.commit()
    row = db.execute("SELECT * FROM ai_results WHERE article_url=? AND mode=?", (url, mode)).fetchone()
    return _present(row)


def render_settings_panel(csrf, *, models=None, notice="", is_admin=True, draft=None):
    """Account page section to choose a model server, check it, and save. The API key is never shown."""
    from html import escape

    # Unsaved choices (a preset or a checked address) stay in the form until saved.
    saved = {**load_settings(), **{k: v for k, v in (draft or {}).items() if k != "api_key"}}
    status = ai_status()
    if status["enabled"]:
        where = "on this Mac" if status.get("local") else "a cloud service, so article text leaves this Mac"
        summary = f'<p class="muted">Using <code>{escape(status["model"])}</code> via {escape(where)}.</p>'
    else:
        summary = '<p class="muted">Off. Pick a server below. A local one keeps report text on this Mac.</p>'
    if not is_admin:
        return f'<section class="account-panel ai-settings"><h2>AI assist</h2>{summary}</section>'
    current_url = saved.get("url", "")
    preset_buttons = "".join(
        f'<button type="submit" name="preset" value="{key}" class="ai-preset">{escape(p["label"])}</button>'
        for key, p in PRESETS.items()
    )
    model_value = escape(saved.get("model", ""), quote=True)
    options = "".join(f'<option value="{escape(m, quote=True)}"></option>' for m in (models or []))
    found = (
        f'<p class="ai-found">Found {len(models)} {"model" if len(models) == 1 else "models"}: '
        + ", ".join(f"<code>{escape(m)}</code>" for m in models[:8]) + ("…" if len(models) > 8 else "") + "</p>"
        if models else ""
    )
    provider = saved.get("provider", "openai")
    key_note = "A key is saved. Leave blank to keep it." if saved.get("api_key") else "Only cloud APIs need a key."
    notice_html = f'<p class="ai-notice" role="status">{escape(notice)}</p>' if notice else ""
    return f'''<section class="account-panel ai-settings" aria-label="AI assist">
  <h2>AI assist</h2>
  {summary}
  {notice_html}
  <form method="post" action="/auth/ai-settings" class="ai-settings-form" autocomplete="off">
    <input type="hidden" name="csrf_token" value="{csrf}" />
    <div class="ai-presets">{preset_buttons}</div>
    <label>Provider
      <select name="provider">
        <option value="openai"{" selected" if provider == "openai" else ""}>OpenAI-compatible (LM Studio, Ollama, OpenAI)</option>
        <option value="anthropic"{" selected" if provider == "anthropic" else ""}>Anthropic</option>
      </select></label>
    <label>Server address <input name="url" autocomplete="off" data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other" spellcheck="false" value="{escape(current_url, quote=True)}" placeholder="http://127.0.0.1:1234/v1" /></label>
    <label>Model <input name="model" list="ai-model-list" autocomplete="off" data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other" spellcheck="false" value="{model_value}" placeholder="Check the server to list its models" />
      <datalist id="ai-model-list">{options}</datalist></label>
    {found}
    <label>API key <input name="api_key" type="password" autocomplete="new-password" data-1p-ignore data-lpignore="true" data-bwignore data-form-type="other" placeholder="{escape(key_note, quote=True)}" /></label>
    <label class="ai-enabled"><input type="checkbox" name="enabled" value="1"{" checked" if saved.get("enabled", True) else ""} /> Show AI assist in the reader</label>
    <div class="ai-settings-actions">
      <button type="submit" name="action" value="check">Check server</button>
      <button type="submit" name="action" value="save" class="primary">Save</button>
    </div>
  </form>
</section>'''


def handle_settings_form(data):
    """Account page form: a preset fills the address, Check lists models, Save stores the choice."""
    form = {k: v[0] for k, v in data.items() if v}
    preset = PRESETS.get(form.get("preset", ""))
    if preset:
        settings = load_settings()
        settings.update({"provider": preset["provider"], "url": preset["url"]})
        try:
            models = list_models(preset["provider"], preset["url"], settings.get("api_key", ""))
        except (httpx.HTTPError, ValueError):
            return {"notice": f"{preset['label']} isn't answering at {preset['url']}. Start its server, then check again.",
                    "models": [], "draft": settings}
        if models:
            settings.setdefault("model", "")
            if settings.get("model") not in models:
                settings["model"] = models[0]
        return {"notice": f"{preset['label']} is answering. Pick a model and save.", "models": models, "draft": settings}
    if form.get("action") == "check":
        try:
            models = list_models(form.get("provider", "openai"), form.get("url", ""),
                                 form.get("api_key") or load_settings().get("api_key", ""))
        except (httpx.HTTPError, ValueError) as exc:
            return {"notice": f"No answer from that server ({type(exc).__name__}). Check the address and that it's running.",
                    "models": [], "draft": form}
        return {"notice": f"The server answered with {len(models)} models." if models else "The server answered but listed no models.",
                "models": models, "draft": form}
    form["enabled"] = "1" if form.get("enabled") == "1" else "0"
    save_settings(form)
    return {"notice": "AI assist settings saved.", "models": [], "draft": None}
