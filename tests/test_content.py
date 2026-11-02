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


def figures(markdown, title=''):
    from bs4 import BeautifulSoup
    from intel_sourcerer.content import arrange_media
    return arrange_media(BeautifulSoup(render_markdown(markdown), 'html.parser'), title, 'https://example.com/post')


def test_placeholder_becomes_figure_without_debug_text():
    soup = figures('> **Image placeholder:** Network diagram of the campaign\n>\n> [View original image](https://example.com/a.png)\n\nBody.')
    figure = soup.select_one('figure.article-figure')
    assert figure['data-media-kind'] == 'image'
    assert figure['data-media-url'] == 'https://example.com/a.png'
    assert figure.figcaption.get_text() == 'Network diagram of the campaign'
    assert figure.select_one('.media-fallback a')['href'] == 'https://example.com/a.png'
    assert 'placeholder' not in soup.get_text().lower()
    assert not soup.find('blockquote')


def test_real_quotes_stay_quotes():
    soup = figures('> **Important:** a real quotation.\n\n> Another quote with [a link](https://example.com).')
    assert len(soup.select('blockquote')) == 2
    assert not soup.find('figure')


def test_following_figure_caption_moves_into_the_figure():
    soup = figures('> **Image placeholder:** https://storage.example.com/img.png\n>\n> [View original image](https://example.com/a.png)\n\nFigure 1. The attack [lifecycle](https://example.com/l).\n\nFigure 2 shows something else in prose.')
    figure = soup.select_one('figure')
    assert figure.figcaption.get_text() == 'Figure 1. The attack lifecycle.'
    assert figure.figcaption.a['href'] == 'https://example.com/l'
    assert figure['data-media-description'] == 'No description supplied'
    assert soup.find('p', string='Figure 2 shows something else in prose.')


def test_uninformative_descriptions_get_no_caption():
    for description in ['No description supplied', 'Article header image', 'Refer to caption', 'jags_ftr_v2', 'https://cdn.example.com/x.png', 'The Post Title | Publisher']:
        soup = figures(f'> **Image placeholder:** {description}\n>\n> [View original image](https://example.com/a.png)', 'The Post Title')
        assert soup.select_one('figure') and not soup.find('figcaption'), description


def test_site_chrome_images_are_removed():
    from intel_sourcerer.content import is_site_chrome_image
    for name in ['Clock Icon', 'Cortex XSIAM icon', 'Unit 42 Incident Response icon', ' logo', 'Anthropic logo', 'Down arrow', 'Link Email']:
        assert is_site_chrome_image(name), name
    for name in ['Logo cloud on an orange gradient showing partners', 'An icon of the malware family with its loader', 'Screenshot of a phishing page']:
        assert not is_site_chrome_image(name), name
    soup = figures('- > **Image placeholder:** Profile Icon\n  >\n  > [View original image](https://example.com/p.svg)\n\nBody.')
    assert not soup.find('figure') and not soup.find('li') and not soup.find('ul')
    assert 'Body.' in soup.get_text()


def test_placeholder_without_url_keeps_only_a_useful_description():
    assert not figures('> **Image placeholder:** No description supplied').find('figure')
    figure = figures('> **Image placeholder:** Campaign timeline').select_one('figure')
    assert figure.figcaption.get_text() == 'Campaign timeline'
    assert not figure.has_attr('data-media-url') and not figure.select_one('.media-fallback')


def test_unsafe_media_url_is_not_loaded():
    soup = figures('> **Image placeholder:** Chart\n>\n> [View original image](javascript:alert(1))')
    figure = soup.select_one('figure')
    assert figure is None or not figure.has_attr('data-media-url')


def test_stored_placeholder_inside_link_no_longer_leaks_markdown():
    stored = ('18 min read Related Products[> **Image placeholder:** Advanced DNS Security icon\n>\n> '
              '[View original image](https://example.com/dns.svg)\n\nAdvanced DNS Security](https://example.com/dns "Advanced DNS Security")'
              '[> **Image placeholder:** Partner banner\n>\n> [View original image](https://example.com/b.png)\n\n](https://example.com/partner)')
    soup = figures(stored)
    text = soup.get_text()
    assert '[>' not in text and 'placeholder' not in text and '](' not in text
    assert soup.find('a', string='Advanced DNS Security')['href'] == 'https://example.com/dns'
    assert [f['data-media-url'] for f in soup.select('figure')] == ['https://example.com/b.png']


def test_new_import_lifts_linked_image_out_of_the_link():
    html = '<article><p>Lead</p><a href="https://example.com/product"><img src="/banner.png" alt="Partner banner">Product page</a><a href="/photo-page"><img src="/photo.png" alt="Field photo"></a><p>After</p></article>'
    markdown, media = extract_markdown(html, 'https://example.com/post')
    assert '[>' not in markdown
    assert '[Product page](https://example.com/product)' in markdown
    assert len(media) == 2
    soup = figures(markdown)
    assert [f['data-media-url'] for f in soup.select('figure')] == ['https://example.com/banner.png', 'https://example.com/photo.png']


def test_word_count_ignores_figures_and_captions():
    from intel_sourcerer.content import reading_word_count
    markdown = 'One two three.\n\n> **Image placeholder:** Long alt text here\n>\n> [View original image](https://example.com/a.png)\n\nFigure 1. Caption words here.'
    assert reading_word_count(markdown) == 3


def test_format_label_names_what_was_parsed():
    from intel_sourcerer.content import format_label
    assert format_label('article') == 'HTML'
    assert format_label('paper') == 'PDF'
    assert format_label('video') == 'Video'
    assert format_label('audio') == 'Audio'
    assert format_label('newsletter') == 'HTML'
    assert format_label('newsletter', 'Email newsletters') == 'Email'
    assert format_label('slides') == 'Slides'
    assert format_label('something-new') == 'HTML'
