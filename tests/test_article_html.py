from html import escape

import pymupdf
import pytest
from bs4 import BeautifulSoup

from intel_sourcerer.content import arrange_media, extract_article, reading_word_count, render_article_html
from intel_sourcerer.pdf_layout import extract_layout_article
from intel_sourcerer.reparse import reparse
from intel_sourcerer.storage import connect_database

PATTERNS = [
    r'^\d{1,3}\.\d{1,3}$',
    r'[a-z0-9_]+@[a-z]+\.com',
    r'(?i)powershell\s+-enc\s+[A-Za-z0-9+/=]{20,}',
    r'\$env:TEMP\\[a-z]+\.exe',
    r'cost \$5 to \$10',
    r'^https?://[^/]+/(admin|login)\.php$',
    r'C:\\Users\\[^\\]+\\AppData',
    r'(?<name>foo|bar)*_baz_',
    r'\*\.exe|\*\.dll',
    r'<script[^>]*>.*?</script>',
    r'\[A-Z\]\{3\}',
    r'a|b|c',
    r'\(?:\/usr\/lib(?:64)?\/\)',
    r'1\. not a list',
]


def reader_soup(html):
    clean, _, _ = extract_article('<article>' + html + '</article>', 'https://example.com/post')
    return arrange_media(BeautifulSoup(render_article_html(clean), 'html.parser'), '', 'https://example.com/post')


@pytest.mark.parametrize('pattern', PATTERNS)
@pytest.mark.parametrize('template', [
    '<p>Match {} here</p>',
    '<p><code>{}</code></p>',
    '<pre><code>{}</code></pre>',
    '<table><tr><th>Rule</th></tr><tr><td><code>{}</code></td></tr></table>',
])
def test_regex_text_reaches_the_reader_unchanged(pattern, template):
    assert pattern in reader_soup(template.format(escape(pattern))).get_text()


def test_pipe_in_table_code_keeps_cell_whole():
    soup = reader_soup('<table><tr><th>Rule</th><th>Note</th></tr><tr><td><code>a|b|c</code></td><td>Second</td></tr></table>')
    assert [cell.get_text() for cell in soup.select('tbody td, tr:last-child td')][:2] == ['a|b|c', 'Second']


@pytest.mark.parametrize('widget', [
    '<div class="crayon-syntax"><textarea class="crayon-plain">line one\n  /a\\.b/ line two</textarea><table><tr><td class="crayon-nums">1</td></tr></table></div>',
    '<textarea readonly>line one\n  /a\\.b/ line two</textarea>',
    '<table class="highlight"><tr><td class="gutter">1</td><td class="code">line one</td></tr><tr><td class="gutter">2</td><td class="code">  /a\\.b/ line two</td></tr></table>',
    '<p><code>line one\n  /a\\.b/ line two</code></p>',
])
def test_code_widgets_become_code_blocks(widget):
    block = reader_soup(widget).select_one('pre code')
    assert block is not None
    assert block.get_text().strip('\n') == 'line one\n  /a\\.b/ line two'


def test_only_marked_math_becomes_math():
    html = '<p>Inline <math alttext="x_i" display="inline"></math> costs $5 to $10 and \\(not math\\).</p>'
    soup = reader_soup(html)
    spans = soup.select('span.article-math')
    assert [span['data-tex'] for span in spans] == ['x_i']
    assert 'costs $5 to $10 and \\(not math\\).' in soup.get_text()


def test_latexml_equation_tables_become_paragraphs():
    html = """<table class="ltx_equation ltx_eqn_table"><tbody class="ltx_eqn_row"><tr>
      <td class="ltx_eqn_cell ltx_eqn_center_padleft"></td>
      <td class="ltx_eqn_cell"><math alttext="a=b" display="block"></math></td>
      <td class="ltx_eqn_cell ltx_eqn_eqno"><span class="ltx_tag">(1)</span></td></tr></tbody></table>"""
    soup = reader_soup(html)
    assert not soup.find('table')
    span = soup.select_one('p > span.article-math')
    assert span['data-tex'] == 'a=b' and span['data-display'] == 'true'
    assert '(1)' in span.parent.get_text()


def test_aligned_equation_rows_marked_inline_become_display():
    soup = reader_soup('<table class="ltx_equationgroup"><tr><td><math alttext="a=b" display="inline"></math></td></tr><tr><td><math alttext="=c" display="inline"></math></td></tr></table>')
    assert [span['data-display'] for span in soup.select('.article-math')] == ['true', 'true']
    assert len(soup.select('p')) == 2


def test_latexml_list_markers_are_not_repeated():
    soup = reader_soup('<ul><li><span class="ltx_tag ltx_tag_item">•</span><div class="ltx_para"><p>First</p></div></li></ul><ol><li><span class="ltx_tag ltx_tag_item">1.</span><div class="ltx_para"><p>Second</p></div></li></ol><ul><li><span class="ltx_tag ltx_tag_item">(a)</span><p>Third</p></li></ul>')
    assert [li.get_text().strip() for li in soup.select('li')] == ['First', 'Second', '(a)Third']


def test_sanitizer_removes_active_content():
    html = '<p onclick="x()">Text<script>bad()</script><iframe src="https://evil.test"></iframe><a href="javascript:bad()">link</a><span class="evil" data-tex="x" style="color:red">s</span></p>'
    output = str(reader_soup(html))
    for unsafe in ['onclick', '<script', '<iframe', 'javascript:', 'class="evil"', 'style=']:
        assert unsafe not in output


def test_layout_blocks_stay_separate_paragraphs():
    soup = reader_soup('<div><div>First block.</div><div>Second block.</div></div>Trailing bare text.')
    assert [p.get_text() for p in soup.select('p')] == ['First block.', 'Second block.', 'Trailing bare text.']


def test_media_and_captions_still_arrange_from_clean_html():
    soup = reader_soup('<figure><img src="/a.png" alt="Network diagram"><figcaption>Figure 1. The campaign.</figcaption></figure><p>Body.</p>')
    figure = soup.select_one('figure')
    assert figure['data-media-url'] == 'https://example.com/a.png'
    assert figure.figcaption.get_text() == 'Figure 1. The campaign.'


def test_word_count_uses_clean_html():
    clean, markdown, _ = extract_article('<article><p>One two three.</p><figure><img src="/a.png" alt="Long alt words"></figure></article>', 'https://example.com/post')
    assert reading_word_count(markdown, '', clean) == 3


def test_pdf_monospace_lines_become_verbatim_code(tmp_path):
    code = ['rule x {', '    strings:', '        $a = /foo\\d+\\.bar/ nocase', '}']
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 80), 'The rule follows.', fontname='helv', fontsize=11)
    for index, line in enumerate(code):
        page.insert_text((50, 110 + index * 13), line, fontname='cour', fontsize=10)
    page.insert_text((50, 200), 'Body after the rule.', fontname='helv', fontsize=11)
    clean, _, _ = extract_layout_article(doc.tobytes(), 'https://example.com/a.pdf', tmp_path)
    soup = BeautifulSoup(clean, 'html.parser')
    assert soup.select_one('pre code').get_text() == '\n'.join(code)
    assert soup.find('p', string='Body after the rule.')
    doc.close()


def test_pdf_bullet_in_symbol_font_stays_inside_monospace_block(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 100), 'For each item:', fontname='cour', fontsize=10)
    page.insert_text((50, 113), '\u2022', fontname='helv', fontsize=10)
    page.insert_text((62, 113), 'Use the pattern', fontname='cour', fontsize=10)
    page.insert_text((62, 126), 'on two lines', fontname='cour', fontsize=10)
    clean, _, _ = extract_layout_article(doc.tobytes(), 'https://example.com/a.pdf', tmp_path)
    soup = BeautifulSoup(clean, 'html.parser')
    assert len(soup.select('pre')) == 1 and not soup.find('ul')
    assert 'Use the pattern' in soup.pre.get_text() and 'on two lines' in soup.pre.get_text()
    doc.close()


def test_pdf_monospace_email_stays_text(tmp_path):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 100), 'someone@example.com', fontname='cour', fontsize=10)
    clean, _, _ = extract_layout_article(doc.tobytes(), 'https://example.com/a.pdf', tmp_path)
    assert '<pre>' not in clean and 'someone@example.com' in clean
    doc.close()


def library(path):
    db = connect_database(path)
    db.execute("INSERT INTO sources VALUES ('s','S','manual','https://example.com',1)")
    for url, fmt in [('https://example.com/a', 'article'), ('https://example.com/short', 'article'), ('https://example.com/gone', 'article'), ('https://www.youtube.com/watch?v=abc', 'video')]:
        db.execute("INSERT INTO articles (url,source_id,title,published_at,summary_html,content_status,relevance_reason,discovered_at,content_markdown,content_format,notes,is_read) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (url, 's', 'T', '2026-01-01', '', 'full_text', 'r', '2026-01-01', 'Old body with several words of text in it.', fmt, 'My note', 1))
    db.commit()
    return db


def test_reparse_updates_bodies_and_keeps_notes(tmp_path):
    path = tmp_path / 'lib.sqlite3'
    library(path).close()

    def rebuilder(url, _assets):
        if url.endswith('/gone'):
            raise ValueError('gone')
        if url.endswith('/short'):
            return '<p>Blocked</p>', 'Blocked', []
        return '<p>New body with several words of text in it.</p>', 'New body', []

    report = reparse(path, rebuilder=rebuilder)
    assert report['updated'] == ['https://example.com/a']
    assert [item['url'] for item in report['shrunk']] == ['https://example.com/short']
    assert [item['url'] for item in report['failed']] == ['https://example.com/gone']
    db = connect_database(path)
    rows = {row['url']: row for row in db.execute('SELECT * FROM articles')}
    assert rows['https://example.com/a']['content_html'].startswith('<p>New body')
    assert rows['https://example.com/a']['notes'] == 'My note' and rows['https://example.com/a']['is_read'] == 1
    assert rows['https://example.com/short']['content_html'] is None
    assert rows['https://example.com/short']['content_markdown'].startswith('Old body')
    assert rows['https://www.youtube.com/watch?v=abc']['content_html'] is None
    assert reparse(path, rebuilder=rebuilder)['updated'] == []
    assert reparse(path, include_done=True, url_contains='/a', rebuilder=rebuilder)['updated'] == ['https://example.com/a']
    db.close()
