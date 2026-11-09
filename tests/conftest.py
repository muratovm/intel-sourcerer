import pytest


@pytest.fixture(autouse=True)
def no_real_whisper(monkeypatch):
    # Imports of local media start Whisper when it is installed; tests opt in with a fake.
    monkeypatch.setenv('INTEL_SOURCERER_WHISPER', '/nonexistent/mlx_whisper')
    # Never download from YouTube in tests either.
    monkeypatch.setenv('INTEL_SOURCERER_UVX', '/nonexistent/uvx')
