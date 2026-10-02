"""Preserve article structure and inline media locations without downloading media."""
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from markdownify import markdownify
from markdown_it import MarkdownIt
import bleach


def safe_url(value, base):
    url = urljoin(base, value or '')
    return url if value and urlsplit(url).scheme in {'http','https'} else None


def extract_markdown(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    articles = soup.find_all('article')
    root = max(articles, key=lambda a: len(a.get_text())) if articles else soup.find('main') or soup.body or soup
    # Hero images can live outside the article element; publisher metadata is a fallback.
    header_image = None
    header_alt = 'Article header image'
    for selector in ('meta[property="og:image"]', 'meta[name="twitter:image"]', 'meta[property="twitter:image"]'):
        meta = soup.select_one(selector)
        if meta and safe_url(meta.get('content'), url):
            header_image = safe_url(meta['content'], url)
            break
    alt_meta = soup.select_one('meta[property="og:image:alt"], meta[name="twitter:image:alt"]')
    if alt_meta and alt_meta.get('content'):
        header_alt = alt_meta['content']
    # Ignore navigation and executable/document metadata, retaining actual article structure.
    for tag in root.select('script, style, nav, footer, noscript, form, button'):
        tag.decompose()
    # Publisher recommendation cards are not part of the saved article.
    cutoff = False
    for tag in list(root.find_all(True)):
        if tag.parent is None:
            continue
        if tag.name in {'h2','h3','h4'} and tag.get_text(' ', strip=True).lower() in {'keep reading','related content','related articles'}:
            cutoff = True
        if cutoff:
            tag.decompose()
    for text in list(root.find_all(string=True)):
        if text.strip() in {'Loading…','Loading...'}:
            text.extract()
    if header_image:
        existing = []
        for image in root.select('img, picture source'):
            source = image.get('src') or image.get('data-src')
            if not source and image.get('srcset'):
                source = image['srcset'].split(',')[0].strip().split()[0]
            existing.append(safe_url(source, url))
        if header_image not in existing:
            hero = soup.new_tag('img', src=header_image, alt=header_alt)
            title = root.find('h1')
            if title:
                title.insert_after(hero)
            else:
                root.insert(0, hero)
    media = []
    for tag in list(root.select('picture, img, video, iframe, embed, object')):
        if tag.parent is None:
            continue
        if tag.name == 'img' and tag.find_parent('picture'):
            continue
        if tag.name == 'picture':
            image = tag.find('img')
            target = image or tag.find('source') or tag
            kind = 'image'
        else:
            target = tag
            kind = 'image' if tag.name == 'img' else 'video' if tag.name == 'video' else 'embedded media'
        label = target.get('alt') or tag.get('title') or tag.get('aria-label') or 'No description supplied'
        source = target.get('src') or target.get('data-src') or target.get('data')
        if not source and target.get('srcset'):
            source = target['srcset'].split(',')[0].strip().split()[0]
        if not source and tag.name == 'video':
            child = tag.find('source')
            source = child.get('src') if child else None
        link = safe_url(source, url)
        record = dict(position=len(media)+1, kind=kind, description=label, url=link)
        media.append(record)
        placeholder = soup.new_tag('blockquote')
        paragraph = soup.new_tag('p')
        strong = soup.new_tag('strong');strong.string = f'{kind.capitalize()} placeholder: '
        paragraph.append(strong);paragraph.append(label)
        placeholder.append(paragraph)
        if link:
            p = soup.new_tag('p');a = soup.new_tag('a', href=link);a.string=f'View original {kind}';p.append(a);placeholder.append(p)
        tag.replace_with(placeholder)
    for anchor in root.select('a[href]'):
        link = safe_url(anchor['href'], url)
        if link:anchor['href']=link
        else:anchor.unwrap()
    # Lazy-loading media sometimes exists only as decorative SVG/canvas; omit those.
    for tag in root.select('svg, canvas'):
        tag.decompose()
    result = markdownify(str(root), heading_style='ATX', bullets='-', strip=['div','span']).strip()
    return result, media


def render_markdown(text):
    renderer = MarkdownIt('commonmark', {'html':False}).enable('table')
    html = renderer.render(text)
    return bleach.clean(html, tags=['p','br','h1','h2','h3','h4','h5','h6','ul','ol','li','blockquote','strong','em','a','pre','code','hr','table','thead','tbody','tr','th','td','s'],
        attributes={'a':['href','title'], 'ol':['start'], 'th':['align'], 'td':['align']}, protocols=['http','https','mailto'], strip=True)
