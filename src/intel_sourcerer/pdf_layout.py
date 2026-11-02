"""Extract PDF tables and render figure regions as local PNG assets."""
from pathlib import Path
from html import escape
import re
import hashlib
import pymupdf
from .pdf_content import (
    BULLET_GLYPHS,
    BULLET_LINE,
    CODE_LINE,
    DASH_LINE,
    INVISIBLE,
    LINK_CLOSE,
    LINK_OPEN,
    LINK_SEP,
    LONE_BULLETS,
    clean_line,
    is_lettered_heading,
    normalize_section_heading,
    page_html,
    strip_page_furniture,
)
from .content import extract_article, extract_markdown


def extract_layout(data, url, directory):
    return extract_markdown(layout_html(data, url, directory), url)


def extract_layout_article(data, url, directory):
    return extract_article(layout_html(data, url, directory), url)


def layout_html(data, url, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    with pymupdf.open(stream=data, filetype='pdf') as document:
        raw = ['\n'.join(block[4] for block in page.get_text('blocks') if not is_margin_page_number(block, page.rect)) for page in document]
        cleaned = strip_page_furniture(raw)
        output = []
        hrefs = []
        for index, page in enumerate(document):
            blocks = [block for block in page.get_text('blocks', sort=True) if not is_margin_page_number(block, page.rect)]
            block_lines, marker_left = marked_block_lines(page, hrefs)
            allowed = set(line.strip() for line in cleaned[index].splitlines())
            regions = []
            entries = []

            def figure(rect, label):
                rect = pymupdf.Rect(rect) & page.rect
                if rect.is_empty or rect.width < 20 or rect.height < 20:
                    return
                for block in blocks:
                    if block[4].strip().lower().startswith(('figure ', 'table ', 'chart ')) and abs(block[1] - rect.y1) < 70:
                        label = ' '.join(block[4].split()) + f' · PDF page {index + 1}'
                        break
                name = f'{digest}-{index + 1}-{len(regions)}.png'
                # Bound rendering size for unusually large PDF page coordinates.
                scale = min(1.5, 1800 / max(rect.width, rect.height))
                page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False).save(directory / name)
                regions.append(rect)
                src = '/pdf-assets/' + name
                entries.append((rect.y0, f'<img src="{src}" alt="{escape(label)}"/>', rect, None))

            for rect, table_html in grouped_rubric_tables(page, blocks):
                regions.append(rect)
                entries.append((rect.y0, table_html, rect, None))
            tables = page.find_tables().tables
            for number, table in enumerate(tables, 1):
                rect = pymupdf.Rect(table.bbox)
                if any(rect.intersects(region) for region in regions):
                    continue
                cells = table.extract()
                simple = is_simple_table(cells, table.col_count)
                if simple:
                    rows = []
                    for i, row in enumerate(cells):
                        tag = 'th' if i == 0 else 'td'
                        rows.append('<tr>' + ''.join(f'<{tag}>' + escape(' '.join(str(cell).split())) + f'</{tag}>' for cell in row) + '</tr>')
                    entries.append((rect.y0, f'<p>Table {number} · PDF page {index + 1}</p><table><thead>{rows[0]}</thead><tbody>' + ''.join(rows[1:]) + '</tbody></table>', rect, None))
                    regions.append(rect)
                else:
                    figure(rect, f'Table {number} · PDF page {index + 1} · original layout')
            # Borderless tables may evade the cell detector. A caption followed by
            # a compact run of aligned text is safer to preserve as an image.
            for caption in blocks:
                if not re.match(r'^Table\s+\d+\s*:', caption[4].strip(), re.I):
                    continue
                caption_rect = pymupdf.Rect(caption[:4])
                following = [b for b in blocks if b[1] >= caption_rect.y1 - 1 and b[1] < caption_rect.y1 + 250]
                cluster = []
                bottom = caption_rect.y1
                for block in following:
                    rect = pymupdf.Rect(block[:4])
                    if rect.y0 - bottom > 20:
                        break
                    if block[4].strip():
                        cluster.append(rect)
                        bottom = max(bottom, rect.y1)
                if len(cluster) < 3:
                    continue
                rect = pymupdf.Rect(cluster[0])
                for other in cluster[1:]:
                    rect |= other
                if any(rect.intersects(existing) for existing in regions):
                    continue
                rect += (-4, -3, 4, 3)
                figure(rect, ' '.join(caption[4].split()) + f' · PDF page {index + 1} · original layout')
                regions.append(caption_rect)
            for image in page.get_image_info():
                rect = pymupdf.Rect(image['bbox'])
                if any(rect.intersects(r) for r in regions):
                    continue
                figure(rect, f'Figure · PDF page {index + 1}')
            # Vector charts are drawings rather than embedded image objects.
            for rect in page.cluster_drawings():
                if rect.width < 80 or rect.height < 60 or any(rect.intersects(r) for r in regions):
                    continue
                if rect.y0 < 35 or rect.y1 > page.rect.height - 25:
                    continue
                figure(rect, f'Chart or diagram · PDF page {index + 1}')
            author_blocks = infer_author_blocks(blocks) if index == 0 else []
            if author_blocks:
                author_html = '<h2>Authors &amp; publication details</h2>' + ''.join(page_html('\n'.join(text for _, text in block_lines.get(b[5], ())).replace(CODE_LINE, '')) for b in author_blocks)
                entries.append((min(b[1] for b in author_blocks), author_html, pymupdf.Rect(0, min(b[1] for b in author_blocks), page.rect.width, max(b[3] for b in author_blocks)), None))
            for block in blocks:
                if block in author_blocks:
                    continue
                if len(block) > 6 and block[6] != 0:
                    continue
                rect = pymupdf.Rect(block[:4])
                if any(r.contains(rect) or (rect & r).get_area() > rect.get_area() * .5 for r in regions):
                    continue
                text = '\n'.join(marked for plain, marked in block_lines.get(block[5], ()) if plain in allowed)
                if block[5] in marker_left:
                    rect.x0 = min(rect.x0, marker_left[block[5]])
                if text.strip():
                    entries.append((rect.y0, None, rect, text))
            output.extend(flow_text_blocks(entries, page.rect))
        html = resolve_links(''.join(output), hrefs)
        # A list interrupted only by a page break or a wide gap is still one list.
        html = html.replace('</ul><ul>', '').replace('</code></pre><pre><code>', '\n')
        # Text blocks may split author and advisor lines; retain a single metadata heading.
        marker = '<h2>Authors &amp; publication details</h2>'
        if marker in html:
            first = html.index(marker) + len(marker)
            html = html[:first] + html[first:].replace(marker, '')
        return '<article>' + html + '</article>'


def marked_block_lines(page, hrefs):
    """Return each block's lines as (plain, reader text) and its list marker's x.

    Reader text carries link markers from the page's annotations and any list
    marker that was extracted separately from the line it sits beside. New link
    targets are appended to hrefs, which is shared across the document.
    """
    links = []
    for link in page.get_links():
        uri = (link.get('uri') or '').strip()
        if re.match(r'^(?:https?://|mailto:)', uri, re.I):
            hrefs.append(uri)
            links.append((pymupdf.Rect(link['from']) + (-1, -1, 1, 1), len(hrefs) - 1))
    lines = []
    for block in page.get_text('rawdict', flags=pymupdf.TEXTFLAGS_BLOCKS)['blocks']:
        for line in block.get('lines', ()):
            chars = [char for span in line['spans'] for char in span['chars']]
            plain = ''.join(char['c'] for char in chars)
            for plain_part, marked_part in zip(plain.splitlines(), mark_links(chars, links).splitlines()):
                lines.append(dict(block=block['number'], bbox=pymupdf.Rect(line['bbox']), plain=plain_part.strip(), marked=marked_part.strip(), mono=is_monospace_line(line), text_left=first_glyph_left(chars), size=line['spans'][0]['size'] if line['spans'] else 0))
    marker_left = {}
    for marker in lines:
        text = INVISIBLE.sub('', marker['plain'])
        if not (LONE_BULLETS.fullmatch(text) or re.fullmatch(r'\d{1,3}[.)]', text)):
            continue
        beside = [
            other for other in lines
            if other is not marker and not other.get('has_marker') and other['plain']
            and not LONE_BULLETS.fullmatch(INVISIBLE.sub('', other['plain']))
            and not re.fullmatch(r'\d{1,3}[.)]', other['plain'])
            and marker['bbox'].x1 - 1 <= other['bbox'].x0 <= marker['bbox'].x1 + 36
            and abs((marker['bbox'].y0 + marker['bbox'].y1) - (other['bbox'].y0 + other['bbox'].y1)) / 2 < other['bbox'].height * .6
        ]
        if not beside:
            continue
        target = min(beside, key=lambda other: other['bbox'].x0)
        target['has_marker'] = True
        target['marked'] = text + ' ' + target['marked']
        target['marker_x'] = marker['bbox'].x0
        marker['absorbed'] = True
        marker_left[target['block']] = min(marker_left.get(target['block'], marker['bbox'].x0), marker['bbox'].x0)
    code_left = {}
    for line in lines:
        if line['mono'] and line['text_left'] is not None:
            code_left[line['block']] = min(code_left.get(line['block'], line['text_left']), line['text_left'])
    result = {}
    list_left = {}
    for line in lines:
        if line.get('absorbed'):
            continue
        block = line['block']
        if line['mono'] and line['plain'] and not EMAIL_LINE.fullmatch(line['plain']):
            # Monospace advances evenly, so an offset from the block's edge is indentation.
            indent = round((line['text_left'] - code_left[block]) / (line['size'] * .6)) if line['size'] else 0
            result.setdefault(block, []).append((line['plain'], CODE_LINE + ' ' * max(indent, 0) + line['marked']))
            continue
        if has_list_marker(line['marked']):
            list_left[block] = line.get('marker_x', line['bbox'].x0)
        elif block in list_left and line['bbox'].x0 <= list_left[block] + 2:
            # Back at the bullet's margin, so this is body text after the list.
            del list_left[block]
            line['marked'] = '\n' + line['marked']
        result.setdefault(block, []).append((line['plain'], line['marked']))
    return result, marker_left


EMAIL_LINE = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')
MONOSPACE_FONT = re.compile(r'mono|courier|consol|menlo|inconsolata|sourcecode|cmtt|sftt', re.I)


def is_monospace_line(line):
    # A bullet glyph usually comes from a symbol font even in a monospace block.
    spans = [span for span in line['spans'] if ''.join(char['c'] for char in span['chars']).strip() not in {''} | set(BULLET_GLYPHS)]
    return bool(spans) and all(span['flags'] & 8 or MONOSPACE_FONT.search(span['font']) for span in spans)


def first_glyph_left(chars):
    return next((char['bbox'][0] for char in chars if not char['c'].isspace()), None)


def mark_links(chars, links):
    """Wrap the characters inside each link annotation in private-use markers."""
    output = []
    current = None
    spaces = ''
    for char in chars:
        value = char['c']
        if value.isspace():
            spaces += value
            continue
        x0, y0, x1, y1 = char['bbox']
        point = pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2)
        link = next((number for rect, number in links if rect.contains(point)), None)
        # A bullet inside a link's box belongs to the list, not to the link.
        if current is None and BULLET_LINE.match(value + ' x') and not ''.join(output).strip():
            link = None
        if link != current:
            if current is not None:
                output.append(LINK_CLOSE)
            output.append(spaces)
            spaces = ''
            if link is not None:
                output.append(f'{LINK_OPEN}{link}{LINK_SEP}')
            current = link
        output.append(spaces)
        spaces = ''
        output.append(value)
    if current is not None:
        output.append(LINK_CLOSE)
    output.append(spaces)
    return ''.join(output)


def resolve_links(html, hrefs):
    """Replace link markers with anchors, joining pieces split across lines."""
    def anchor(match):
        href = hrefs[int(match[1])]
        text = match[2]
        trailing = re.search(r'[.,;:]+$', text)
        punctuation = trailing[0] if trailing else ''
        text = text[:len(text) - len(punctuation)]
        return f'<a href="{escape(href, quote=True)}">{text}</a>{punctuation}'

    pattern = re.compile(f'{LINK_OPEN}(\\d+){LINK_SEP}(.*?){LINK_CLOSE}', re.S)
    # Wrapped pieces sit next to each other, separated by at most whitespace.
    joined = re.compile(f'{LINK_OPEN}(\\d+){LINK_SEP}([^{LINK_CLOSE}]*){LINK_CLOSE}(\\s*){LINK_OPEN}(\\d+){LINK_SEP}')
    merged = None
    while merged != html:
        merged = html
        html = joined.sub(lambda m: f'{LINK_OPEN}{m[1]}{LINK_SEP}{m[2]}{m[3]}' if hrefs[int(m[1])] == hrefs[int(m[4])] else m[0], html)
    html = pattern.sub(lambda m: anchor_for_wrapped_url(anchor(m)), html)
    return html.replace(LINK_OPEN, '').replace(LINK_SEP, '').replace(LINK_CLOSE, '')


def anchor_for_wrapped_url(anchor_html):
    """A printed URL split by a line break should not keep the break's space."""
    match = re.fullmatch(r'<a href="([^"]*)">(.*?)</a>(.*)', anchor_html, re.S)
    if not match or not re.search(r'\s', match[2]):
        return anchor_html
    squeezed = re.sub(r'\s+', '', match[2])
    bare = re.sub(r'^https?://', '', squeezed, flags=re.I).lower()
    if re.search(r'[./]', bare) and bare in match[1].lower():
        return f'<a href="{match[1]}">{squeezed}</a>{match[3]}'
    return anchor_html


def flow_text_blocks(entries, page_rect=None):
    """Join visual body lines before conversion, retaining real layout boundaries."""
    output = []
    pending = []
    previous = None
    bullet_left = None

    def flush():
        nonlocal previous, bullet_left
        if pending:
            output.append(page_html("\n".join(pending)))
            pending.clear()
        previous = None
        bullet_left = None

    def boundary(text):
        first = text.strip().splitlines()[0]
        return is_lettered_heading(first) or bool(re.match(r"^(?:\d+(?:\.\d+)*\.?\s+|(?:Abstract|References|Bibliography|Works Cited|Appendix)\b|[IVXLCDM]+\.\s+[A-Z]|(?:Author|Authors|Advisor):)", first, re.I))

    for entry in reading_order(entries, page_rect):
        if len(entry) == 2 or entry[1] is not None:
            flush()
            output.append(entry[1])
            continue
        _, _, rect, text = entry
        text = normalize_section_heading(text)
        if previous is not None:
            gap = rect.y0 - previous.y1
            # Inside a list, a line indented past the bullet still belongs to it,
            # and a line back at the bullet's margin or further left ends it.
            in_item = bullet_left is not None and rect.x0 > bullet_left + 2
            ends_list = bullet_left is not None and rect.x0 <= bullet_left + 2 and not has_list_marker(text)
            # A larger vertical gap or indentation denotes a new paragraph.
            if boundary(text) or gap < -2 or gap > max(12, min(previous.height, rect.height) * .85) or (rect.x0 > previous.x0 + 12 and not in_item) or ends_list:
                flush()
        if boundary(text):
            flush()
            output.append(page_html(text))
        else:
            pending.append(text.strip())
            previous = rect
            if has_list_marker(text):
                bullet_left = rect.x0
    flush()
    return output


def has_list_marker(text):
    lines = (clean_line(raw) for raw in text.splitlines())
    return any(BULLET_LINE.match(line) or DASH_LINE.match(line) or LONE_BULLETS.fullmatch(line) or re.match(r'^\d+[.)]\s', line) for line in lines)


def is_simple_table(cells, columns):
    """Reject ambiguous row groups rather than flattening them into false data."""
    if len(cells) < 2 or not 2 <= columns <= 12:
        return False
    if not all(len(row) == columns and all(cell is not None for cell in row) for row in cells):
        return False
    # A multiline body cell may contain multiple rows or a wrapped value. Without
    # reliable row boundaries the source image is more faithful than Markdown.
    if any('\n' in str(cell).strip() for row in cells[1:] for cell in row):
        return False
    return sum(bool(str(cell).strip()) for cell in cells[0]) >= max(1, columns - 1)


def is_margin_page_number(block, page_rect):
    """Remove standalone page labels only in the physical header/footer band."""
    text = ' '.join(block[4].split())
    number = re.fullmatch(r'(?:Page\s+)?(?:\d+|[ivxlcdm]+)(?:\s*(?:of|/)\s*\d+)?', text, re.I)
    if not number:
        return False
    top, bottom = block[1], block[3]
    band = min(65, page_rect.height * .085)
    return bottom <= page_rect.y0 + band or top >= page_rect.y1 - band


def infer_author_blocks(blocks):
    """Recognize an unlabeled author/affiliation grid before the abstract."""
    abstracts = [b for b in blocks if b[4].strip().lower() == 'abstract']
    if not abstracts:
        return []
    boundary = min(b[1] for b in abstracts)
    candidates = [b for b in blocks if b[3] <= boundary and b[0] >= 50]
    # Labeled front matter already follows the existing explicit parser.
    if any(re.match(r'^(?:Author|Authors|Advisor):', b[4].strip(), re.I) for b in candidates):
        return []
    emails = [b for b in candidates if re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', b[4])]
    if len(emails) < 2:
        return []
    start = min(b[1] for b in emails) - 20
    return sorted([b for b in candidates if b[1] >= start], key=lambda b: (round(b[1] / 20), b[0]))


def reading_order(entries, page_rect=None):
    """Read two-column papers down each column, respecting spanning regions."""
    if page_rect is None:
        return sorted(entries, key=lambda item: item[0])
    midpoint = page_rect.x0 + page_rect.width / 2
    positioned = [entry for entry in entries if len(entry) > 2]
    right = [entry for entry in positioned if entry[2].x0 >= midpoint]
    left = [entry for entry in positioned if entry[2].x1 <= midpoint + 10]
    if len(right) < 3 or len(left) < 3:
        return sorted(entries, key=lambda item: item[0])
    spanning = sorted([entry[2] for entry in positioned if entry[2].width > page_rect.width * .65], key=lambda rect: rect.y0)
    def order(entry):
        rect = entry[2] if len(entry) > 2 else None
        zone = sum(region.y1 <= entry[0] for region in spanning)
        column = -1 if rect and rect.width > page_rect.width * .65 else 1 if rect and rect.x0 >= midpoint else 0
        return zone, column, entry[0]
    return sorted(entries, key=order)


def grouped_rubric_tables(page, blocks):
    """Recover assessment rows from word positions and vertically merged rubrics."""
    result = []
    for header in blocks:
        normalized = ' '.join(header[4].split())
        if normalized != 'Rubric Model Micro-F1 Unsupported FPR':
            continue
        bounds = pymupdf.Rect(header[:4])
        words = [word for word in page.get_text('words') if word[0] >= bounds.x0 - 8 and word[2] <= bounds.x1 + 8 and word[1] > bounds.y1 and word[1] < bounds.y1 + 145]
        labels = [(word[4], (word[1] + word[3]) / 2) for word in words if word[4] in {'Type', 'ATT&CK', 'Edge'}]
        if {label for label, _ in labels} != {'Type', 'ATT&CK', 'Edge'}:
            continue
        models = [word for word in words if re.fullmatch(r'(?:qwen[\w.:+-]*|gpt-oss[\w.:+-]*|gemma[\w.:+-]*)', word[4])]
        rows = []
        used = []
        for model in sorted(models, key=lambda word: word[1]):
            center = (model[1] + model[3]) / 2
            scores = sorted([word for word in words if word[0] > model[2] and abs((word[1] + word[3]) / 2 - center) < 3 and re.fullmatch(r'0\.\d+|1\.0+', word[4])], key=lambda word: word[0])
            if len(scores) != 2:
                rows = []
                break
            rubric = min(labels, key=lambda label: abs(label[1] - center))[0]
            rows.append([rubric, model[4], scores[0][4], scores[1][4]])
            used.extend([model, *scores])
        if len(rows) != 12 or any(sum(row[0] == label for row in rows) != 4 for label in ['Type', 'ATT&CK', 'Edge']):
            continue
        rect = pymupdf.Rect(bounds)
        for word in used:
            rect |= pymupdf.Rect(word[:4])
        rect += (-3, -2, 3, 2)
        html = '<table><thead><tr><th>Rubric</th><th>Model</th><th>Micro-F1</th><th>Unsupported FPR</th></tr></thead><tbody>'
        html += ''.join('<tr>' + ''.join('<td>' + escape(cell) + '</td>' for cell in row) + '</tr>' for row in rows)
        result.append((rect, html + '</tbody></table>'))
    return result
