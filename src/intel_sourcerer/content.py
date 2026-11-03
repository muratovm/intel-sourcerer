"""Preserve article structure and inline media locations without downloading media."""
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .boilerplate import strip_boilerplate, strip_named_furniture
from markdownify import markdownify
from markdown_it import MarkdownIt
import bleach


MEDIA_PLACEHOLDERS = {'image placeholder:', 'video placeholder:', 'audio placeholder:', 'embedded media placeholder:'}
UNINFORMATIVE_DESCRIPTIONS = {'no description supplied', 'article header image', 'refer to caption', 'embedded artifact', 'embedded video', 'embedded media', 'image'}
FIGURE_CAPTION = re.compile(r'(?:(?:figure|fig\.?|table|exhibit|chart|diagram|screenshot|image)\s+\d+[a-z]?\s*[.:—–-]|source\s*:)', re.I)


FORMAT_LABELS = {'article': 'HTML', 'newsletter': 'HTML', 'paper': 'PDF', 'video': 'Video', 'audio': 'Audio', 'slides': 'Slides', 'social': 'Social', 'repository': 'GitHub'}


def format_label(content_format, publisher=''):
    """Name what an item was parsed from, for labels in the reading views."""
    if content_format == 'newsletter' and publisher == 'Email newsletters':
        return 'Email'
    return FORMAT_LABELS.get(content_format, 'HTML')


def safe_url(value, base):
    if value and (re_asset_url(value) or re_local_media(value)):
        return value
    url = urljoin(base, value or '')
    return url if value and urlsplit(url).scheme in {'http','https'} else None


def prepare_article(html, url, math_node):
    """Isolate the article and swap media for placeholders, ready for either output."""
    soup = BeautifulSoup(html, 'html.parser')
    articles = soup.find_all('article')
    main = soup.find('main')
    top_articles = [article for article in articles if not article.find_parent('article')]
    # Reports may divide the whole document into sibling article sections.
    if main and len([article for article in top_articles if main in article.parents]) > 1:
        root = main
    else:
        root = max(articles, key=lambda a: len(a.get_text())) if articles else main or soup.body or soup
    # LaTeXML supplies exact TeX alongside MathML; preserve it for KaTeX.
    for math in list(root.find_all('math')):
        tex = math.get('alttext')
        if tex:
            display = math.get('display') == 'block' or 'ltx_Math' in math.get('class', []) and math.find_parent(class_='ltx_equation') is not None
            math.replace_with(math_node(soup, tex, display))
    normalize_latexml(root, soup)
    normalize_code_widgets(root, soup)
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
    for tag in root.select('script, style, nav, footer, noscript, form, button, template'):
        tag.decompose()
    strip_named_furniture(root)
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
        # A block placeholder inside a link would break the link's Markdown.
        anchor = tag.find_parent('a')
        if anchor:
            anchor.insert_before(placeholder)
            tag.decompose()
            if not anchor.get_text(strip=True) and not anchor.find(True):
                anchor.decompose()
        else:
            tag.replace_with(placeholder)
    for anchor in root.select('a[href]'):
        link = anchor['href'] if anchor['href'].lower().startswith('mailto:') else safe_url(anchor['href'], url)
        if link:anchor['href']=link
        else:anchor.unwrap()
    # Lazy-loading media sometimes exists only as decorative SVG/canvas; omit those.
    for tag in root.select('svg, canvas'):
        tag.decompose()
    return soup, root, media


def extract_markdown(html, url):
    """Markdown is the export format; the reader uses extract_article's HTML."""
    import uuid
    tokens = {}
    prefix = 'ARXIVMATH' + uuid.uuid4().hex

    def math_token(soup, tex, display):
        key = prefix + str(len(tokens))
        tokens[key] = ('\n\n$$' + tex + '$$\n\n') if display else '$' + tex + '$'
        return soup.new_string(key)

    _, root, media = prepare_article(html, url, math_token)
    # Built from the cleaned article, so the export has none of the furniture the reader removes.
    result = markdownify(clean_article_html(root), heading_style='ATX', bullets='-', strip=['div','span']).strip()
    for key, value in tokens.items():
        result = result.replace(key, value)
    return result, media


def extract_article(html, url):
    """Return (clean HTML, Markdown, media); text in the HTML is never reinterpreted."""
    def math_span(soup, tex, display):
        span = soup.new_tag('span', attrs={'class': 'article-math', 'data-tex': tex})
        if display:
            span['data-display'] = 'true'
        span.string = tex
        return span

    _, root, _ = prepare_article(html, url, math_span)
    markdown, media = extract_markdown(html, url)
    return clean_article_html(root), markdown, media


def normalize_latexml(root, soup):
    """arXiv's HTML lays out equations with tables and prints list markers as text."""
    for table in root.select('table.ltx_equation, table.ltx_equationgroup, table.ltx_eqn_table'):
        rows = []
        # Aligned rows can be marked inline in the source, but they stand alone here.
        for span in table.select('.article-math'):
            span['data-display'] = 'true'
        for row in table.select('tr'):
            line = soup.new_tag('p')
            for cell in row.find_all('td'):
                if not cell.get_text(strip=True):
                    continue
                if len(line):
                    line.append(' ')
                line.extend(list(cell.contents))
            if len(line):
                rows.append(line)
        for line in rows:
            table.insert_before(line)
        table.decompose()
    for tag in root.select('li > .ltx_tag'):
        if tag.parent.parent.name == 'ol' or tag.get_text(strip=True) in {'•', '∙', '·', '–', '—', '∘', '∗', '-'}:
            tag.decompose()


def normalize_code_widgets(root, soup):
    """Highlighter plugins hide code in forms and tables; restore it as code blocks."""
    def code_block(text):
        pre = soup.new_tag('pre')
        code = soup.new_tag('code')
        code.string = text.strip('\n')
        pre.append(code)
        return pre

    for widget in root.select('.crayon-syntax'):
        plain = widget.select_one('textarea')
        lines = widget.select('.crayon-line')
        text = plain.get_text() if plain else '\n'.join(line.get_text() for line in lines)
        if text.strip():
            widget.replace_with(code_block(text))
    for table in root.select('table'):
        cells = table.select('td.code, td.rouge-code, td.hljs-ln-code, td.blob-code')
        if cells:
            table.replace_with(code_block('\n'.join(cell.get_text().rstrip('\n') for cell in cells)))
    for field in root.select('textarea'):
        field.replace_with(code_block(field.get_text()))
    for code in root.select('code'):
        if '\n' in code.get_text().strip() and not code.find_parent('pre'):
            code.replace_with(code_block(code.get_text()))


def allow_span_attribute(_tag, name, value):
    return name in {'data-tex', 'data-display'} or (name == 'class' and value == 'article-math')


ARTICLE_TAGS = ['p','br','h1','h2','h3','h4','h5','h6','ul','ol','li','blockquote','strong','em','b','i','u','a','pre','code','kbd','samp','var','sub','sup','mark','abbr','del','ins','s','hr','table','caption','thead','tbody','tfoot','tr','th','td','dl','dt','dd','span']
ARTICLE_ATTRIBUTES = {'a': ['href', 'title'], 'ol': ['start'], 'th': ['align', 'colspan', 'rowspan'], 'td': ['align', 'colspan', 'rowspan'], 'abbr': ['title'], 'span': allow_span_attribute}
BLOCK_TAGS = {'address','article','aside','blockquote','details','div','dl','figure','footer','h1','h2','h3','h4','h5','h6','header','hr','main','ol','p','pre','section','table','ul'}
INLINE_TAGS = {'a','abbr','b','br','code','del','em','i','ins','kbd','mark','s','samp','span','strong','sub','sup','u','var'}


def sanitize_article_html(html):
    return bleach.clean(html, tags=ARTICLE_TAGS, attributes=ARTICLE_ATTRIBUTES, protocols=['http','https','mailto'], strip=True)


def clean_article_html(root):
    # Stripping layout tags would run sibling blocks of text together.
    for tag in root.find_all(['div','section','aside','header','main','details','summary','figcaption','address','article']):
        if tag is not root and tag.get_text(strip=True) and not tag.find(BLOCK_TAGS):
            tag.name = 'p'
            tag.attrs = {}
    soup = BeautifulSoup(sanitize_article_html(str(root)), 'html.parser')
    paragraph = None
    for node in list(soup.contents):
        if getattr(node, 'name', None) is None or node.name in INLINE_TAGS:
            if paragraph is None and not str(node.get_text() if node.name else node).strip():
                continue
            if paragraph is None:
                paragraph = soup.new_tag('p')
                node.insert_before(paragraph)
            paragraph.append(node.extract())
        else:
            paragraph = None
    for tag in soup.find_all('p'):
        if not tag.get_text(strip=True) and not tag.find(True):
            tag.decompose()
    strip_boilerplate(soup)
    return str(soup)


def render_article_html(html):
    """Prepare stored clean HTML for display; no Markdown or TeX parsing happens here."""
    return link_plain_urls(research_sections(sanitize_article_html(html)))


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
    html = renderer.render(pattern.sub(protect, repair_linked_placeholders(text)))
    for token, equation in equations.items():
        html = html.replace(token, equation)
    clean = bleach.clean(html, tags=['p','br','h1','h2','h3','h4','h5','h6','ul','ol','li','blockquote','strong','em','a','pre','code','hr','table','thead','tbody','tr','th','td','s'],
        attributes={'a':['href','title'], 'ol':['start'], 'th':['align'], 'td':['align']}, protocols=['http','https','mailto'], strip=True)

    return link_plain_urls(research_sections(clean))


def repair_linked_placeholders(text):
    """Stored imports may hold a placeholder inside link text; lift it out."""
    import re
    def lift(match):
        placeholder = f'> **{match[1]} placeholder:** {match[2]}'
        if match[3]:
            placeholder += f'\n>\n> [{match[3]}]({match[4]})'
        link = f'[{match[5].strip()}]({match[6]})' if match[5].strip() else ''
        return f'\n\n{placeholder}\n\n{link}\n\n'
    return re.sub(
        r'\[> \*\*([\w ]+) placeholder:\*\* ([^\n]*)(?:\n>\n> \[([^\]\n]*)\]\(([^)\s]*)\))?\s*\n*([^\]\n]*)\]\(([^)\s]+)(?: "[^"\n]*")?\)',
        lift, text)


def media_description_is_useful(description, title=''):
    """Screen-reader fallbacks, raw URLs and file names are not captions."""
    import re
    text = ' '.join(description.split())
    if not text or text.lower() in UNINFORMATIVE_DESCRIPTIONS or re.match(r'https?://', text, re.I):
        return False
    if ' ' not in text and re.search(r'[_\d]|\.\w{2,4}$', text):
        return False
    return not (title and title.casefold() in text.casefold())


def is_site_chrome_image(description):
    """Share buttons and product icons are page furniture, not article media."""
    import re
    return bool(re.fullmatch(r'(?:[^\s]+\s+){0,7}(?:icon|logo)|(?:up|down|left|right)\s+arrow|link\s+email', ' '.join(description.split()), re.I) or description.strip().lower() in {'icon', 'logo'})


def arrange_media(soup, title='', base=''):
    """Replace stored media placeholders with figures and attach their captions."""
    import re
    for block in soup.find_all('blockquote'):
        label = block.find('strong')
        if not label or label.get_text().strip().lower() not in MEDIA_PLACEHOLDERS:
            continue
        kind = label.get_text().strip().lower().removesuffix(' placeholder:')
        description = ' '.join(label.parent.get_text(' ', strip=True).split('placeholder:', 1)[-1].split())
        if is_site_chrome_image(description):
            remove_with_empty_parents(block)
            continue
        link = block.find('a', href=True)
        url = safe_url(link['href'], base) if link else None
        useful = media_description_is_useful(description, title)
        figure = soup.new_tag('figure', attrs={'class': 'article-figure'})
        following = block.find_next_sibling()
        caption = None
        if following is not None and following.name == 'p' and FIGURE_CAPTION.match(following.get_text(strip=True)) and len(following.get_text()) <= 400:
            caption = soup.new_tag('figcaption')
            caption.extend(list(following.contents))
            following.decompose()
        elif useful:
            caption = soup.new_tag('figcaption')
            caption.string = description
        if url:
            figure['data-media-kind'] = kind
            figure['data-media-url'] = url
            figure['data-media-description'] = description if description and not re.match(r'https?://', description, re.I) else 'No description supplied'
        elif not caption:
            remove_with_empty_parents(block)
            continue
        if caption:
            figure.append(caption)
        if url:
            fallback = soup.new_tag('p', attrs={'class': 'media-fallback'})
            original = soup.new_tag('a', href=url, target='_blank', rel='noopener noreferrer')
            original.string = link.get_text(strip=True) or f'View original {kind}'
            fallback.append(original)
            figure.append(fallback)
        block.replace_with(figure)
    return soup


def remove_with_empty_parents(tag):
    parent = tag.parent
    tag.decompose()
    while parent is not None and parent.name in {'li', 'ul', 'ol', 'p'} and not parent.get_text(strip=True) and not parent.find(['img', 'figure', 'a']):
        next_parent = parent.parent
        parent.decompose()
        parent = next_parent


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


def reading_word_count(markdown, fallback='', html=None):
    rendered = render_article_html(html) if html else render_markdown(markdown) if markdown else ''
    soup = BeautifulSoup(rendered, 'html.parser')
    arrange_media(soup)
    for node in soup.select('[data-reading-exclude], figure, video, iframe'):
        node.decompose()
    return len((soup.get_text(' ', strip=True) if rendered else fallback).split())


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
