import os
from contextlib import closing
from datetime import datetime, timezone

import httpx
import pytest

from intel_sourcerer import ai_assist
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.storage import connect_database, save_article, save_source

URL = "https://s.example/report"


def _db(tmp_path):
    db = connect_database(tmp_path / "lib.sqlite3")
    save_source(db, PublisherSource("s", "Source", "https://s.example", "security_vendor"))
    save_article(db, "s", Article("Source", "Report", URL, URL, datetime(2026, 11, 1, tzinfo=timezone.utc), None, None, ""),
                 content="The actor used PowerShell and stole registry credentials.", error=None, reason="",
                 discovered_at="2026-11-01")
    db.commit()
    return db


def _fake_model(monkeypatch, answer, seen=None):
    original = httpx.Client

    def client(*args, **kwargs):
        def reply(request):
            if seen is not None:
                seen.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})
        kwargs["transport"] = httpx.MockTransport(reply)
        return original(*args, **kwargs)

    monkeypatch.setattr(ai_assist.httpx, "Client", client)


def test_settings_keep_the_key_private_and_win_over_env(monkeypatch):
    monkeypatch.setenv("INTEL_AI_URL", "http://127.0.0.1:11434/v1")
    ai_assist.save_settings({"provider": "openai", "url": "http://127.0.0.1:1234/v1/", "model": "qwen3.5-9b-mlx", "api_key": "secret"})
    assert oct(os.stat(ai_assist.settings_path()).st_mode & 0o777) == "0o600"
    ai_assist.save_settings({"provider": "openai", "url": "http://127.0.0.1:1234/v1", "model": "gemma", "api_key": ""})
    assert ai_assist.load_settings()["api_key"] == "secret"
    assert ai_assist.ai_status() == {"enabled": True, "provider": "openai", "model": "gemma", "local": True}
    panel = ai_assist.render_settings_panel("tok")
    assert "secret" not in panel and "A key is saved" in panel
    with pytest.raises(ValueError):
        ai_assist.save_settings({"provider": "openai", "url": "file:///etc/passwd", "model": "x"})


def test_answers_are_saved_checked_and_rendered_safely(tmp_path, monkeypatch):
    ai_assist.save_settings({"provider": "openai", "url": "http://127.0.0.1:1234/v1", "model": "local-8b"})
    _fake_model(monkeypatch, "<think>scratch</think>- `T1059.001` PowerShell\n- `T1214` registry creds\n- `T9999` <script>x</script>")
    with closing(_db(tmp_path)) as db:
        result = ai_assist.run_ai_assist(db, URL, "ttps")
        assert "scratch" not in result["markdown"] and "<script>" not in result["html"]
        assert [(c["id"], c["status"]) for c in result["checks"]] == [("T1059.001", "ok"), ("T1214", "retired"), ("T9999", "unknown")]
        assert [r["mode"] for r in ai_assist.saved_results(db, URL)] == ["ttps"]


def test_ask_needs_a_question_and_sends_it(tmp_path, monkeypatch):
    ai_assist.save_settings({"provider": "openai", "url": "http://127.0.0.1:1234/v1", "model": "local-8b"})
    seen = []
    _fake_model(monkeypatch, "The report does not say.", seen)
    with closing(_db(tmp_path)) as db:
        with pytest.raises(ValueError, match="question"):
            ai_assist.run_ai_assist(db, URL, "ask", "   ")
        result = ai_assist.run_ai_assist(db, URL, "ask", "Which sectors were hit?")
    assert result["question"] == "Which sectors were hit?" and b"Which sectors were hit?" in seen[0].content


def test_presets_list_models_from_the_server(monkeypatch):
    original = httpx.Client

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"data": [{"id": "qwen3.5-9b-mlx"}, {"id": "foundation-sec-1.1-8b-instruct"}]}))
        return original(*args, **kwargs)

    monkeypatch.setattr(ai_assist.httpx, "Client", client)
    result = ai_assist.handle_settings_form({"preset": ["lmstudio"]})
    assert result["models"] == ["foundation-sec-1.1-8b-instruct", "qwen3.5-9b-mlx"]
    assert result["draft"]["url"] == "http://127.0.0.1:1234/v1" and result["draft"]["model"] == "foundation-sec-1.1-8b-instruct"
