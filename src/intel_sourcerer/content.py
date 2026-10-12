"""Preserve article structure and inline media locations without downloading media."""
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from markdownify import markdownify
from markdown_it import MarkdownIt
import bleach


def safe_url(value, base):
    if value and (re_asset_url(value) or re_local_media(value)):
        return value
    url = urljoin(base, value or '')
    return url if value and urlsplit(url).scheme in {'http','https'} else None


def extract_markdown(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    articles = soup.find_all('article')
    main = soup.find('main')
    top_articles = [article for article in articles if not article.find_parent('article')]
    # Reports may divide the whole document into sibling article sections.
    if main and len([article for article in top_articles if main in article.parents]) > 1:
        root = main
    else:
        root = max(articles, key=lambda a: len(a.get_text())) if articles else main or soup.body or soup
    math_tokens = {}
    import uuid
    math_prefix = "ARXIVMATH" + uuid.uuid4().hex
    # LaTeXML supplies exact TeX alongside MathML; preserve it for KaTeX.
    for math in list(root.find_all('math')):
        tex = math.get('alttext')
        if tex:
            display = math.get('display') == 'block' or 'ltx_Math' in math.get('class', []) and math.find_parent(class_='ltx_equation') is not None
            key = math_prefix + str(len(math_tokens))
            math_tokens[key] = ('\n\n$$' + tex + '$$\n\n') if display else '$' + tex + '$'
            math.replace_with(soup.new_string(key))
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
    for key, value in math_tokens.items():
        result = result.replace(key, value)
    return result, media


def render_markdown(text):
    renderer = MarkdownIt('commonmark', {'html':False}).enable('table')
    import re
    import uuid
    from html import escape
    equations = {}
    prefix = 'MATH' + uuid.uuid4().hex
    # Protect TeX from Markdown backslash escapes and emphasis parsing.
    pattern = re.compile(r"```[\s\S]*?```|`[^`\n]*`|\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|\\\([^\n]*?\\\)|(?<!\\)\$[^$\n]+?\$")
    def protect(match):
        raw = match[0]
        if raw.startswith('`'):
            return raw
        token = prefix + str(len(equations)) + 'END'
        equations[token] = escape(raw)
        return token
    html = renderer.render(pattern.sub(protect, text))
    for token, equation in equations.items():
        html = html.replace(token, equation)
    clean = bleach.clean(html, tags=['p','br','h1','h2','h3','h4','h5','h6','ul','ol','li','blockquote','strong','em','a','pre','code','hr','table','thead','tbody','tr','th','td','s'],
        attributes={'a':['href','title'], 'ol':['start'], 'th':['align'], 'td':['align']}, protocols=['http','https','mailto'], strip=True)

    return link_plain_urls(research_sections(clean))


def research_sections(html):
    """Collapse recognized research sections after sanitizing their contents."""
    import re
    soup = BeautifulSoup(html, 'html.parser')
    for heading in list(soup.find_all(['h1', 'h2', 'h3'])):
        name = re.sub(r'^(?:\d+(?:\.\d+)*\.?|[IVXLCDM]+\.)\s+', '', heading.get_text(' ', strip=True)).lower()
        if name not in {'abstract', 'authors & publication details', 'references', 'bibliography', 'works cited'}:
            continue
        details = soup.new_tag('details')
        details['class'] = 'research-section'
        references = name in {'references', 'bibliography', 'works cited'}
        if references:
            details['data-reading-exclude'] = 'references'
        if name == 'abstract':
            details['open'] = ''
        summary = soup.new_tag('summary')
        summary.string = heading.get_text(' ', strip=True)
        details.append(summary)
        sibling = heading.next_sibling
        heading.replace_with(details)
        while sibling is not None:
            if getattr(sibling, 'name', None) in {'h1', 'h2', 'h3'}:
                break
            next_sibling = sibling.next_sibling
            details.append(sibling.extract())
            sibling = next_sibling
    return str(soup)


def reading_word_count(markdown, fallback=''):
    soup = BeautifulSoup(render_markdown(markdown), 'html.parser') if markdown else BeautifulSoup('', 'html.parser')
    for node in soup.select('[data-reading-exclude], figure, video, iframe'):
        node.decompose()
    return len((soup.get_text(' ', strip=True) if markdown else fallback).split())


def link_plain_urls(html):
    """Turn explicit web URLs into links without touching existing links or code."""
    import re
    soup = BeautifulSoup(html, 'html.parser')
    pattern = re.compile(r"https?://[^\s<>]+", re.I)
    for node in list(soup.find_all(string=True)):
        if not node.find_parent(['code', 'pre']):
            cleaned = re.sub(r'\s*\(opens in a new window\)', '', str(node), flags=re.I)
            if cleaned != str(node):
                node.replace_with(cleaned)
    for node in list(soup.find_all(string=True)):
        if node.find_parent(['a', 'code', 'pre', 'script', 'style', 'summary']):
            continue
        value = str(node)
        pieces = []
        end = 0
        for match in pattern.finditer(value):
            url = match.group().rstrip('.,;:!?')
            # Citation punctuation may follow a URL; retain balanced URL parentheses.
            while url.endswith(')') and url.count(')') > url.count('('):
                url = url[:-1]
            if not urlsplit(url).hostname:
                continue
            pieces.append(value[end:match.start()])
            anchor = soup.new_tag('a', href=url)
            anchor['target'] = '_blank'
            anchor['rel'] = 'noopener noreferrer'
            anchor.string = url
            pieces.append(anchor)
            end = match.start() + len(url)
        if pieces:
            pieces.append(value[end:])
            for piece in pieces:
                node.insert_before(piece)
            node.extract()
    for anchor in soup.select('a[href]'):
        if urlsplit(anchor['href']).scheme in {'http', 'https'}:
            anchor['target'] = '_blank'
            anchor['rel'] = 'noopener noreferrer'
    return str(soup)


def re_asset_url(value):
    import re
    return bool(re.fullmatch(r'/pdf-assets/[a-f0-9]{64}-\d+-\d+\.png', value))


def re_local_media(value):
    import re
    return bool(re.fullmatch(r'/local-media/[a-f0-9]{64}\.(?:mp3|m4a|ogg|wav|mp4|webm|png)',value))
