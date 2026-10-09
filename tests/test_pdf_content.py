from intel_sourcerer.pdf_content import page_html
from intel_sourcerer.content import extract_markdown, render_markdown


def test_pdf_bullets_and_wrapped_lines():
    html = page_html('Introduction\n\n• First item\nwrapped continuation\n• Second item\n \nFollowing paragraph.')
    markdown, _ = extract_markdown('<article>' + html + '</article>', 'https://example.com/report.pdf')
    assert '* First item wrapped continuation' in markdown or '- First item wrapped continuation' in markdown
    assert '<ul>' in render_markdown(markdown)
    assert '<li>Second item</li>' in render_markdown(markdown)
    assert '<p>Following paragraph.</p>' in render_markdown(markdown)


def test_pdf_ordered_lists_and_section_heading():
    html = page_html('1. Introduction\n\n1. Define a schema for all the input data\ncontinued\n2. Batch all the objects in the input file\n')
    assert '<h2>1. Introduction</h2>' in html
    assert '<ol' in html
    assert '<li>Define a schema for all the input data continued</li>' in html


def test_numbered_subheadings_break_out_of_lists():
    html = page_html('• Last item\n2.2. Indicator Source Selection\nBody text\n2.2.1. Detailed Selection\nMore body')
    markdown, _ = extract_markdown('<article>' + html + '</article>', 'https://example.com/report.pdf')
    assert '### 2.2. Indicator Source Selection' in markdown
    assert '### 2.2.1. Detailed Selection' in markdown
    assert '<li>Last item</li>' in html


def test_repeated_edge_furniture_preserves_body_and_footnotes():
    from intel_sourcerer.pdf_content import strip_page_furniture
    pages = [f'Report title\n{i}\nSection {i}\nBody text\nReport title\nMore body\nFootnote unique {i}\nCopyright Company\nPage {i} of 3' for i in range(1, 4)]
    cleaned = strip_page_furniture(pages)
    for i, page in enumerate(cleaned, 1):
        assert page.count('Report title') == 1
        assert 'Copyright Company' not in page
        assert f'Page {i} of 3' not in page
        assert f'Footnote unique {i}' in page


def test_collapsible_research_sections_and_word_count():
    from intel_sourcerer.content import render_markdown, reading_word_count
    from bs4 import BeautifulSoup
    markdown = '## Authors & publication details\n\nAuthor: Example\n\n## Abstract\n\nBrief overview.\n\n## 1. Introduction\n\nBody text.\n\n## References\n\nA long bibliography citation.\n\n## Appendix A\n\nAppendix text.'
    soup = BeautifulSoup(render_markdown(markdown), 'html.parser')
    sections = soup.select('details.research-section')
    assert len(sections) == 3
    assert not sections[0].has_attr('open')
    assert sections[1].has_attr('open')
    refs = soup.select_one('[data-reading-exclude]')
    assert 'bibliography citation' in refs.get_text()
    assert 'Appendix' not in refs.get_text()
    assert reading_word_count(markdown) < len(soup.get_text(' ', strip=True).split())


def test_pdf_front_matter_headings():
    html = page_html('Report title\nAuthor: Example\nAdvisor: Other\nAbstract\nOverview\n1. Introduction\nBody\nReferences\nExample (2025). Citation\nAppendix A\nAppendix body')
    assert '<h2>Authors &amp; publication details</h2>' in html
    assert '<h2>Abstract</h2>' in html
    assert '<h2>References</h2>' in html
    assert '<h2>Appendix A</h2>' in html


def test_plain_urls_clickable_without_linking_code():
    from bs4 import BeautifulSoup
    html = render_markdown('Visit https://example.com/report. Citation (https://example.com/paper).\n\n`https://example.com/code`\n\n[Existing](https://example.com/existing)\n\n## References\n\nhttps://example.com/reference')
    soup = BeautifulSoup(html, 'html.parser')
    links = soup.select('a')
    assert [a['href'] for a in links] == ['https://example.com/report', 'https://example.com/paper', 'https://example.com/existing', 'https://example.com/reference']
    assert all(a['target'] == '_blank' for a in links)
    assert soup.select_one('code').get_text() == 'https://example.com/code'
    assert soup.select_one('details a')


def test_split_number_and_title_ends_abstract():
    from bs4 import BeautifulSoup
    from intel_sourcerer.pdf_layout import flow_text_blocks
    import pymupdf
    html = ''.join(flow_text_blocks([
        (100, None, pymupdf.Rect(50, 100, 300, 113), 'Abstract'),
        (120, None, pymupdf.Rect(50, 120, 300, 133), 'Overview text.'),
        (155, None, pymupdf.Rect(50, 155, 300, 180), '1\nIntroduction'),
        (200, None, pymupdf.Rect(50, 200, 300, 213), 'Main article text.'),
    ]))
    md, _ = extract_markdown('<article>' + html + '</article>', 'https://example.com/paper.pdf')
    soup = BeautifulSoup(render_markdown(md), 'html.parser')
    abstract = soup.select_one('details')
    assert 'Overview text' in abstract.get_text()
    assert 'Main article text' not in abstract.get_text()
    assert soup.find('h2', string='1. Introduction')


def test_latex_survives_markdown_for_katex():
    html = render_markdown(r'Inline \(x_i^2\) and $x_i$. Display $$\frac{a_b}{c}$$. `\(code\)`')
    assert r'\(x_i^2\)' in html
    assert r'$$\frac{a_b}{c}$$' in html
    assert '<em>' not in html
    assert '<code>' in html


def test_ieee_inline_abstract_and_roman_headings():
    from bs4 import BeautifulSoup
    html = page_html('Abstract—A concise overview.\nI. INTRODUCTION\nMain body.\nII. RELATED WORK\nPrior research.\nA. Data Collection\nDataset text.\nIV. REFERENCES\nCitation text.')
    md, _ = extract_markdown('<article>' + html + '</article>', 'https://example.com/paper.pdf')
    soup = BeautifulSoup(render_markdown(md), 'html.parser')
    abstract = soup.select_one('details')
    assert abstract.summary.get_text() == 'Abstract'
    assert 'concise overview' in abstract.get_text()
    assert 'Main body' not in abstract.get_text()
    assert soup.find('h2', string='I. INTRODUCTION')
    assert soup.find('h3', string='A. Data Collection')
    assert soup.select_one('[data-reading-exclude]')


def test_author_names_are_not_lettered_headings():
    from intel_sourcerer.pdf_content import is_lettered_heading
    for name in ['R. Choupani', 'A. Vaswani', 'R. Choupani and S. Example', 'R. van Dijk', 'R. Choupani, 2025']:
        assert not is_lettered_heading(name)
        assert '<h3>' not in page_html(name)
    assert is_lettered_heading('A. Data Collection')
    assert is_lettered_heading('B. Entity and Relationship Extraction')


def test_regular_number_in_wrapped_sentence_is_not_list():
    for text in [
        'All\n4 models were using Q4_K_M quantization, and 4-bit KV-cache.',
        'All\n4. models were using Q4_K_M quantization, and 4-bit KV-cache.',
        '4 models were using Q4_K_M quantization.',
    ]:
        html = page_html(text)
        assert '<ol' not in html and '<h2>' not in html
        assert 'models were using' in html


def test_numeric_quantity_in_separate_pdf_block_is_prose():
    for text in ['222 techniques, and 475 sub-techniques [4].', '222. techniques, and 475 sub-techniques [4].']:
        html = page_html(text)
        assert '<ol' not in html
        assert '<h2>' not in html
        assert 'techniques, and 475' in html


def test_remove_link_window_notice():
    html = render_markdown('[Report (opens in a new window)](https://example.com) (opens in a new window)')
    assert 'opens in a new window' not in html
    assert 'href="https://example.com"' in html
    assert '>Report</a>' in html
