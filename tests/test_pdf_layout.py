from io import BytesIO
import pymupdf
from intel_sourcerer.pdf_layout import extract_layout
from intel_sourcerer.content import render_markdown, re_asset_url


def test_table_and_figure_extraction(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    for y in [100, 130, 160]:
        page.draw_line((50, y), (250, y))
    for x in [50, 150, 250]:
        page.draw_line((x, 100), (x, 160))
    for position, text in [((60, 120), 'Model'), ((160, 120), 'Score'), ((60, 150), 'Example'), ((160, 150), '95')]:
        page.insert_text(position, text)
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
    image.clear_with(180)
    page.insert_image(pymupdf.Rect(50, 200, 150, 300), stream=image.tobytes('png'))
    markdown, media = extract_layout(doc.tobytes(), 'https://example.com/report.pdf', tmp_path)
    assert '<table>' in render_markdown(markdown)
    assert 'Example' in markdown and '95' in markdown
    assert media and re_asset_url(media[0]['url'])
    assert list(tmp_path.glob('*.png'))
    assert not re_asset_url('/pdf-assets/../../secrets.png')
    doc.close()


def test_visual_lines_join_into_paragraphs():
    from intel_sourcerer.pdf_layout import flow_text_blocks
    entries = [
        (100, None, pymupdf.Rect(50, 100, 400, 113), 'First body line'),
        (120, None, pymupdf.Rect(50, 120, 400, 133), 'continues here.'),
        (155, None, pymupdf.Rect(50, 155, 400, 168), 'A separate paragraph.'),
        (190, None, pymupdf.Rect(50, 190, 400, 205), '2.2. Next Section'),
    ]
    html = ''.join(flow_text_blocks(entries))
    assert '<p>First body line continues here.</p>' in html
    assert '<p>A separate paragraph.</p>' in html
    assert '<h3>2.2. Next Section</h3>' in html


def test_ambiguous_grouped_rows_use_image_fallback():
    from intel_sourcerer.pdf_layout import is_simple_table
    assert not is_simple_table([['Parser', 'Score'], ['First\nSecond', '88.3\n90.4']], 2)
    assert not is_simple_table([['Model', None], ['Example', '95']], 2)
    assert is_simple_table([['Model', 'Score'], ['Example', '95']], 2)


def test_margin_page_numbers_preserve_section_numbers():
    from intel_sourcerer.pdf_layout import is_margin_page_number
    page = pymupdf.Rect(0, 0, 612, 792)
    assert is_margin_page_number((500, 36, 520, 50, '2\n'), page)
    assert is_margin_page_number((300, 742, 310, 753, '8\n'), page)
    assert is_margin_page_number((300, 742, 350, 753, 'Page 8 of 15'), page)
    assert is_margin_page_number((300, 742, 350, 753, 'iv'), page)
    assert not is_margin_page_number((108, 300, 120, 313, '1'), page)
    assert not is_margin_page_number((108, 36, 230, 50, '1\nIntroduction'), page)


def test_unlabeled_author_grid_before_abstract():
    from intel_sourcerer.pdf_layout import infer_author_blocks
    blocks = [
        (100, 100, 400, 120, 'Paper title'),
        (100, 200, 200, 212, 'Alice Example'),
        (100, 215, 210, 240, 'University\nalice@example.com'),
        (300, 200, 400, 240, 'Bob Example\nUniversity\nbob@example.com'),
        (200, 300, 300, 312, 'Abstract'),
        (100, 320, 400, 360, 'Abstract body'),
    ]
    authors = infer_author_blocks(blocks)
    assert len(authors) == 3
    assert all('Paper title' not in b[4] and 'Abstract' not in b[4] for b in authors)


def test_grouped_assessment_table_keeps_rubrics_and_scores():
    from intel_sourcerer.pdf_layout import grouped_rubric_tables
    from bs4 import BeautifulSoup
    doc = pymupdf.open()
    page = doc.new_page()
    header = (50, 50, 350, 60, 'Rubric\nModel\nMicro-F1\nUnsupported FPR')
    for group, label in enumerate(['Type', 'ATT&CK', 'Edge']):
        y = 80 + group * 40
        page.insert_text((52, y + 15), label, fontsize=8)
        for row, model in enumerate(['qwen3:14b', 'gpt-oss:20b', 'qwen3.6:27b', 'gemma4:31b']):
            page.insert_text((110, y + row * 10), model, fontsize=8)
            page.insert_text((220, y + row * 10), '0.941', fontsize=8)
            page.insert_text((290, y + row * 10), '0.029', fontsize=8)
    tables = grouped_rubric_tables(page, [header])
    assert len(tables) == 1
    soup = BeautifulSoup(tables[0][1], 'html.parser')
    rows = soup.select('tbody tr')
    assert len(rows) == 12
    assert [row.td.get_text() for row in rows] == ['Type'] * 4 + ['ATT&CK'] * 4 + ['Edge'] * 4
    assert len(rows[0].select('td')) == 4
    doc.close()


def layout_soup(doc, tmp_path):
    from bs4 import BeautifulSoup
    markdown, _ = extract_layout(doc.tobytes(), 'https://example.com/report.pdf', tmp_path)
    return BeautifulSoup(render_markdown(markdown), 'html.parser')


def test_link_annotations_keep_their_targets(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), 'Read the full guidance today.', fontsize=11)
    page.insert_text((72, 130), 'Contact the team for details.', fontsize=11)
    page.insert_text((72, 160), 'Unsafe click here please.', fontsize=11)
    def link(line, words, uri):
        rect = page.search_for(words)[0]
        page.insert_link(dict(kind=pymupdf.LINK_URI, **{'from': rect}, uri=uri))
    link(100, 'full guidance', 'https://example.com/guidance?a=1&b=2')
    link(130, 'the team', 'mailto:team@example.com')
    link(160, 'click here', 'javascript:alert(1)')
    doc = pymupdf.open(stream=doc.tobytes(), filetype='pdf')
    soup = layout_soup(doc, tmp_path)
    assert {a.get_text(): a['href'] for a in soup.select('a')} == {
        'full guidance': 'https://example.com/guidance?a=1&b=2',
        'the team': 'mailto:team@example.com',
    }
    assert 'click here' in soup.get_text()
    doc.close()


def test_annotated_url_wrapped_across_lines_is_one_link(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    target = 'https://www.example.com/knowledge-center/cyber-threat-intelligence'
    page.insert_text((72, 100), 'Available: https://www.example.com/knowledge-center/cy', fontsize=11)
    page.insert_text((72, 114), 'ber-threat-intelligence. Then more words follow.', fontsize=11)
    first = page.search_for('https://www.example.com/knowledge-center/cy')[0]
    second = page.search_for('ber-threat-intelligence.')[0]
    page.insert_link(dict(kind=pymupdf.LINK_URI, **{'from': first}, uri=target))
    page.insert_link(dict(kind=pymupdf.LINK_URI, **{'from': second}, uri=target))
    doc = pymupdf.open(stream=doc.tobytes(), filetype='pdf')
    soup = layout_soup(doc, tmp_path)
    anchors = soup.select('a')
    assert len(anchors) == 1
    assert anchors[0]['href'] == target
    assert anchors[0].get_text() == target
    assert f'{target}. Then more' in soup.get_text()
    doc.close()


def test_link_markers_never_leak_when_link_is_a_whole_bullet(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), '\u2022 Linked item', fontsize=11)
    page.insert_link(dict(kind=pymupdf.LINK_URI, **{'from': page.search_for('Linked item')[0] + (-20, 0, 0, 0)}, uri='https://example.com/item'))
    doc = pymupdf.open(stream=doc.tobytes(), filetype='pdf')
    soup = layout_soup(doc, tmp_path)
    item = soup.select_one('ul li')
    assert item.a['href'] == 'https://example.com/item'
    assert item.get_text() == 'Linked item'
    assert not any(0xe000 <= ord(c) <= 0xe002 for c in str(soup))
    doc.close()


def test_bullets_in_a_separate_column_attach_to_their_lines(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), 'It covers three parts:', fontsize=11)
    for y, text in [(100, 'Full-screen'), (114, 'Backdrop'), (128, 'Small element')]:
        page.insert_text((90, y), '\u2022', fontsize=11)
        page.insert_text((102, y), text, fontsize=11)
    page.insert_text((72, 142), 'Paragraph after the list.', fontsize=11)
    soup = layout_soup(doc, tmp_path)
    assert [li.get_text() for li in soup.select('ul li')] == ['Full-screen', 'Backdrop', 'Small element']
    assert soup.find('p', string='Paragraph after the list.')
    assert '\u2022' not in soup.get_text()
    doc.close()


def test_hanging_continuation_block_stays_in_its_bullet():
    from intel_sourcerer.pdf_layout import flow_text_blocks
    html = ''.join(flow_text_blocks([
        (100, None, pymupdf.Rect(126, 100, 400, 113), '\u2022 Gunicorn provides workers to handle'),
        (118, None, pymupdf.Rect(144, 118, 300, 131), 'incoming traffic.'),
        (136, None, pymupdf.Rect(126, 136, 400, 149), '\u2022 Simple pages'),
        (154, None, pymupdf.Rect(108, 154, 400, 167), 'Back to the body text.'),
    ]))
    assert '<li>Gunicorn provides workers to handle incoming traffic.</li><li>Simple pages</li>' in html
    assert '<p>Back to the body text.</p>' in html


def test_list_split_by_page_break_is_one_list(tmp_path):
    doc = pymupdf.open()
    for text in ['\u2022 Item on first page', '\u2022 Item on second page']:
        doc.new_page().insert_text((72, 100), text, fontsize=11)
    soup = layout_soup(doc, tmp_path)
    assert len(soup.select('ul')) == 1
    assert len(soup.select('ul li')) == 2
    doc.close()
