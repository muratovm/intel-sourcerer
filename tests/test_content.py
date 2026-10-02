import json
import sqlite3
from bs4 import BeautifulSoup
from intel_sourcerer.content import extract_markdown,render_markdown
from intel_sourcerer.storage import connect_database


def test_markdown_structure_and_inline_media_order():
    html='''<main><h1>Threat report</h1><p>Before media.</p><picture><img src="/chart.png" alt="Attack chart"></picture><h2>Findings</h2><ul><li>First finding</li><li>Second finding</li></ul><table><tr><th>Actor</th><th>Tool</th></tr><tr><td>A</td><td>B</td></tr></table><video><source src="/demo.mp4"></video><iframe src="https://www.youtube.com/embed/123" title="Demo"></iframe><p>After media.</p></main>'''
    md,media=extract_markdown(html,'https://example.com/report')
    assert '# Threat report' in md and '- First finding' in md
    assert len(media)==3
    assert media[0]['url']=='https://example.com/chart.png'
    assert media[1]['kind']=='video'
    assert md.index('Before media')<md.index('Image placeholder')<md.index('Findings')
    assert md.index('Video placeholder')<md.index('After media')
    soup=BeautifulSoup(render_markdown(md),'html.parser')
    assert soup.find('table') and len(soup.find_all('li'))==2
    assert not soup.find(['img','video','iframe'])


def test_markdown_render_blocks_html_and_unsafe_links():
    soup=BeautifulSoup(render_markdown('<script>alert(1)</script>\n\n[bad](javascript:alert(1))\n\n![image](https://example.com/image.png)'),'html.parser')
    assert not soup.find(['script','img'])
    assert all(not a.get('href','').startswith('javascript:') for a in soup.find_all('a'))


def test_old_database_migration_preserves_rows(tmp_path):
    path=tmp_path/'old.sqlite3'
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE articles (url TEXT PRIMARY KEY,source_id TEXT,title TEXT,published_at TEXT,author TEXT,summary_html TEXT,content_text TEXT,content_status TEXT,fetch_error TEXT,relevance_reason TEXT,discovered_at TEXT)')
    db.execute("INSERT INTO articles(url,content_text) VALUES ('https://example.com','Original text')")
    db.commit();db.close()
    db=connect_database(path)
    row=db.execute('SELECT content_text,content_markdown,media_json FROM articles').fetchone()
    assert row['content_text']=='Original text' and row['content_markdown'] is None
    assert json.loads(row['media_json'])==[]
    db.close()


def test_recommendation_media_is_not_saved_as_article_media():
    md,media=extract_markdown('<main><p>Article body</p><h2>Keep reading</h2><img src="/unrelated.png"></main>','https://example.com')
    assert 'Article body' in md
    assert 'Keep reading' not in md and not media


def test_header_image_metadata_outside_article():
    md, media = extract_markdown('<head><meta property="og:image" content="/hero.png"><meta property="og:image:alt" content="Threat report artwork"></head><article><h1>Report</h1><p>Body</p></article>', 'https://example.com/report')
    assert media[0]['url'] == 'https://example.com/hero.png'
    assert media[0]['description'] == 'Threat report artwork'
    assert md.index('# Report') < md.index('Image placeholder') < md.index('Body')


def test_header_image_not_duplicated_and_unsafe_metadata_ignored():
    md, media = extract_markdown('<meta property="og:image" content="/hero.png"><article><h1>Report</h1><img src="/hero.png"><p>Body</p></article>', 'https://example.com/report')
    assert len(media) == 1
    md, media = extract_markdown('<meta property="og:image" content="javascript:alert(1)"><article><p>Body</p></article>', 'https://example.com/report')
    assert not media


def test_report_with_multiple_article_sections_preserves_whole_document():
    html='<main><h1>System card</h1><article><h2>Introduction</h2><p>First section.</p></article><article><h2>Evaluations</h2><table><tr><th>Model</th><th>Score</th></tr><tr><td>Example</td><td>42</td></tr></table></article><article><h2>References</h2><p>Final section.</p></article></main>'
    md,media=extract_markdown(html,'https://example.com/report')
    assert 'First section.' in md and 'Final section.' in md and '| Example | 42 |' in md
    assert md.index('Introduction') < md.index('Evaluations') < md.index('References')
