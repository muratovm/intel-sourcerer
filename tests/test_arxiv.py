import httpx
from intel_sourcerer.arxiv import html_url, preferred_article
from intel_sourcerer.content import extract_markdown


def test_arxiv_urls():
    assert html_url('https://arxiv.org/pdf/1706.03762v7.pdf') == 'https://arxiv.org/html/1706.03762v7'
    assert html_url('https://arxiv.org/abs/1706.03762') == 'https://arxiv.org/html/1706.03762'
    assert html_url('https://example.com/pdf/1706.03762') is None
    assert html_url('https://arxiv.org/html/1706.03762v7') is None


def test_preference_and_fallback(monkeypatch):
    from intel_sourcerer import temporary
    html = '<article class="ltx_document"><h1>Paper</h1><section class="ltx_section"><p>' + 'Research text. ' * 30 + '</p><math alttext="x_i"/><img src="x1.png"/></section></article>'
    monkeypatch.setattr(temporary, 'load_public_article', lambda url, **kwargs: {'html':html,'title':'Paper'})
    result = preferred_article('https://arxiv.org/pdf/1706.03762v7')
    assert result['url'].endswith('/pdf/1706.03762v7')
    assert '$x_i$' in result['markdown']
    assert result['media'][0]['url'] == 'https://arxiv.org/html/x1.png'
    monkeypatch.setattr(temporary, 'load_public_article', lambda url, **kwargs: {'html':'<h1>Unavailable</h1>'})
    assert preferred_article('https://arxiv.org/pdf/1706.03762v7') is None
    def missing(*args, **kwargs):
        raise httpx.ConnectError('Unavailable')
    monkeypatch.setattr(temporary, 'load_public_article', missing)
    assert preferred_article('https://arxiv.org/pdf/1706.03762v7') is None


def test_inline_and_display_math():
    markdown, _ = extract_markdown('<article><p>Value <math alttext="x_i"/> here.</p><math display="block" alttext="a=b"/></article>', 'https://arxiv.org/html/1706.03762v7')
    assert '$x_i$' in markdown
    assert '$$a=b$$' in markdown
