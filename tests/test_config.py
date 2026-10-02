import pytest
from intel_sourcerer.config import load_sources

BASE = '''schema_version = 1
[[sources]]
id = "test"
name = "Test"
source_type = "security_vendor"
parent_url = "https://example.com/reports"
'''


def test_listing_source_and_feed(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(BASE)
    assert load_sources(path)[0].as_feed() is None
    assert load_sources(path)[0].source_type == "security_vendor"
    path.write_text(BASE + 'rss_url = "https://example.com/rss"\n')
    assert load_sources(path)[0].as_feed().url == "https://example.com/rss"
    path.write_text(BASE + 'rss_url = "https://example.com/rss"\nenabled = false\n')
    assert load_sources(path)[0].as_feed() is None


@pytest.mark.parametrize("text", [BASE.replace('https://example.com/reports', 'file:///tmp/report'), BASE.replace('schema_version = 1', 'schema_version = 2'), BASE + 'enabled = "yes"', BASE + '\n[[sources]]\nid="test"\nname="Other"\nsource_type="security_vendor"\nparent_url="https://example.com"', BASE + 'unknown_field = true'])
def test_invalid_config(tmp_path, text):
    path = tmp_path / "sources.toml"
    path.write_text(text)
    with pytest.raises(ValueError):
        load_sources(path)


@pytest.mark.parametrize("replacement", [None, '""', '"   "', '42', 'true'])
def test_source_type_is_required_nonempty_string(tmp_path, replacement):
    path = tmp_path / "sources.toml"
    line = 'source_type = "security_vendor"\n'
    text = BASE.replace(line, "" if replacement is None else f"source_type = {replacement}\n")
    path.write_text(text)
    with pytest.raises(ValueError):
        load_sources(path)
