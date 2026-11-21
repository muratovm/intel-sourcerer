import pytest


@pytest.fixture(autouse=True)
def no_real_whisper(monkeypatch):
    # Imports of local media start Whisper when it is installed; tests opt in with a fake.
    monkeypatch.setenv('INTEL_SOURCERER_WHISPER', '/nonexistent/mlx_whisper')
    # Never download from YouTube in tests either.
    monkeypatch.setenv('INTEL_SOURCERER_UVX', '/nonexistent/uvx')
    monkeypatch.delenv('INTEL_SOURCERER_YTDLP', raising=False)


@pytest.fixture(autouse=True)
def no_real_ai_settings(monkeypatch, tmp_path):
    """Tests never read or write the real AI settings file."""
    monkeypatch.setenv("INTEL_AI_SETTINGS", str(tmp_path / "ai_settings.json"))
