"""Extract PDF tables and render figure regions as local PNG assets."""
from pathlib import Path
from html import escape
import re
import hashlib
import pymupdf
from .pdf_content import page_html, strip_page_furniture, normalize_section_heading, is_lettered_heading
from .content import extract_markdown


def extract_layout(data, url, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    with pymupdf.open(stream=data, filetype='pdf') as document:
        raw = ['\n'.join(block[4] for block in page.get_text('blocks') if not is_margin_page_number(block, page.rect)) for page in document]
        cleaned = strip_page_furniture(raw)
        output = []
        for index, page in enumerate(document):
            blocks = [block for block in page.get_text('blocks', sort=True) if not is_margin_page_number(block, page.rect)]
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
                author_html = '<h2>Authors &amp; publication details</h2>' + ''.join(page_html(b[4]) for b in author_blocks)
                entries.append((min(b[1] for b in author_blocks), author_html, pymupdf.Rect(0, min(b[1] for b in author_blocks), page.rect.width, max(b[3] for b in author_blocks)), None))
            for block in blocks:
                if block in author_blocks:
                    continue
                if len(block) > 6 and block[6] != 0:
                    continue
                rect = pymupdf.Rect(block[:4])
                if any(r.contains(rect) or (rect & r).get_area() > rect.get_area() * .5 for r in regions):
                    continue
                text = '\n'.join(line for line in block[4].splitlines() if line.strip() in allowed)
                if text.strip():
                    entries.append((rect.y0, None, rect, text))
            output.extend(flow_text_blocks(entries, page.rect))
        html = ''.join(output)
        # Text blocks may split author and advisor lines; retain a single metadata heading.
        marker = '<h2>Authors &amp; publication details</h2>'
        if marker in html:
            first = html.index(marker) + len(marker)
            html = html[:first] + html[first:].replace(marker, '')
        markdown, media = extract_markdown('<article>' + html + '</article>', url)
        return markdown, media


def flow_text_blocks(entries, page_rect=None):
    """Join visual body lines before conversion, retaining real layout boundaries."""
    output = []
    pending = []
    previous = None

    def flush():
        nonlocal previous
        if pending:
            output.append(page_html("\n".join(pending)))
            pending.clear()
        previous = None

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
            # A larger vertical gap or indentation denotes a new paragraph.
            if boundary(text) or gap < -2 or gap > max(12, min(previous.height, rect.height) * .85) or rect.x0 > previous.x0 + 12:
                flush()
        if boundary(text):
            flush()
            output.append(page_html(text))
        else:
            pending.append(text.strip())
            previous = rect
    flush()
    return output


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
