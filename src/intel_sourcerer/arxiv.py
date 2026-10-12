"""Prefer structured arXiv HTML, retaining PDF import as a fallback."""
import re
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup


def html_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'arxiv.org', 'www.arxiv.org'}:
        return None
    match = re.fullmatch(r'/(?:pdf|abs)/(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)(?:\.pdf)?', parsed.path)
    return 'https://arxiv.org/html/' + match[1] if match else None


def preferred_article(url):
    """Return usable HTML-derived content or None; use the safe public fetcher."""
    target = html_url(url)
    if not target:
        return None
    from .temporary import load_public_article
    from .content import extract_markdown

    try:
        record = load_public_article(target, prefer_arxiv=False)
        html = record.get('html', '')
        soup = BeautifulSoup(html, 'html.parser')
        article = soup.select_one('article.ltx_document')
        if not article or not article.select_one('.ltx_section') or len(article.get_text(strip=True)) < 200:
            return None
        markdown, media = extract_markdown(html, target)
        return {'url': url, 'title': record['title'], 'markdown': markdown, 'media': media, 'html': html, 'content_url': target}
    except (httpx.HTTPError, ValueError, OSError):
        return None
