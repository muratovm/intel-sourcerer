import pytest
from bs4 import BeautifulSoup

from intel_sourcerer.boilerplate import strip_boilerplate

BODY = ''.join(f'<p>{word} paragraph with enough real sentence text in it to count as a proper piece of the article body, well past the two hundred character mark that separates prose from page furniture on these sites, every time.</p>' for word in ('First', 'Second', 'Third', 'Fourth', 'Fifth', 'Sixth'))


def clean(html):
    soup = BeautifulSoup(html, 'html.parser')
    strip_boilerplate(soup)
    return soup


def text(html):
    return clean(html).get_text(' ', strip=True)


def test_body_alone_is_untouched():
    assert str(clean(BODY)) == BODY


@pytest.mark.parametrize('furniture', [
    # Anthropic: metadata list, then related articles above the body.
    '<ul><li>Published Sep 1, 2026</li><li>Category <a href="/c">Safeguards</a></li><li>Topics <a href="/t">Privacy</a></li></ul><p>Related articles</p><ul><li><a href="/a">Policy Feb 26, 2026 Statement</a></li></ul><p><a href="/n">View more</a></p>',
    # Microsoft: sidebar sections.
    '<h2>Share</h2><ul><li>Link copied to clipboard!</li></ul><h2>Content types</h2><ul><li><a href="/r">Research</a></li></ul><h2>Topics</h2><ul><li><a href="/p">Phishing</a></li></ul><h2>Tags</h2><ul><li><a href="/s">Storm</a></li></ul>',
    # Unit 42: breadcrumb and product promos.
    '<ul><li><a href="/a">Threat Research Center</a></li><li><a href="/b">Malware</a></li></ul><p>4 min read Related Products</p><blockquote>Image placeholder: Cortex icon View original image</blockquote><p><a href="/c">Cortex XDR</a></p><p><a href="/d">Cortex Cloud</a></p>',
    # METR: navigation and language switcher.
    '<ul>' + ''.join(f'<li><a href="/{n}">{n}</a></li>' for n in ('Work', 'Research', 'Notes', 'About', 'Donate')) + '<li>Language English</li></ul><p>English Español 中文</p>',
    # Anthropic's real card: category and date are plain text, only the title links.
    '<p>Related articles</p><ul><li><p>Policy</p><p>Feb 26, 2026</p><p><a href="/s">Statement from Dario Amodei</a></p></li></ul><p><a href="/n">View more</a></p>',
    # METR's real navigation: nested menus with plain-text parents.
    '<ul><li><span>Our Work</span><ul><li><a href="/r">Research</a></li><li><a href="/n">Notes</a></li><li><a href="/b">Updates</a></li><li><a href="/k">Risk Assessment</a></li></ul></li><li><a href="/a">About</a></li><li><a href="/d">Donate</a></li><li><span>Language</span><ul><li><a href="/e">English</a></li></ul></li></ul>',
    # Google: promo card.
    '<h5>Mandiant Services</h5><p>Stop attacks, reduce risk.</p><p><a href="/c">Contact Mandiant</a></p>',
])
def test_top_furniture_goes_and_body_stays(furniture):
    result = clean(furniture + BODY)
    assert str(result) == BODY


@pytest.mark.parametrize('furniture', [
    # Google: tag line, then related cards with thumbnails.
    '<p>Posted in</p><ul><li><a href="/t">Threat Intelligence</a></li></ul><h5>Related articles</h5><blockquote>Image placeholder: https://x/1.png View original image</blockquote><p><a href="/1">Threat Intelligence ARTEX By Google • 19-minute read</a></p><blockquote>Image placeholder: https://x/2.png View original image</blockquote><p><a href="/2">Threat Intelligence ARMORY By Google • 12-minute read</a></p>',
    # METR: featured research with prose teasers.
    '<h2>Featured research</h2><p>METR measures what agents can do.</p><h3>Early work</h3><p>We show preliminary results.</p><p><a href="/1">Read more</a></p>',
    # SentinelLABS: a paragraph label and a card list.
    '<p>Explore</p><ul><li><a href="/1">Cyber Soft Power 13 min read Sep 21, 2023</a></li><li><a href="/2">Top Tier Target 9 min read</a></li></ul>',
    # Miessler: related reading, share and follow bars.
    '<h2>Related Reading</h2><ul><li><a href="/1">Who Will AI Help More?</a></li></ul><p>Share</p><p><a href="/x">Post</a> <a href="/l">LinkedIn</a></p><p>Follow</p><p><a href="/n">Get The Newsletter</a></p><p><a href="/">HOME</a> · <a href="/b">BLOG</a></p>',
    # Talos, Unit 42 and Microsoft tails.
    '<h5>Share this post</h5><ul><li><a href="/s"></a></li></ul>',
    '<h3>Tags</h3><ul><li><a href="/1">AI</a></li><li><a href="/2">Payload</a></li></ul><p><a href="/r">Threat Research Center</a> <a href="/n">Next: From Exposure to Lockdown</a></p><h3>Table of Contents</h3><ul></ul>',
    '<p>Tags: <a href="/1">Digital Defense Report</a> , <a href="/2">Report</a></p>',
    # OpenAI: tag list and an organisation credit.
    '<ul><li><a href="/1">2026</a></li><li><a href="/2">Cybersecurity</a></li></ul><h2>Author</h2><p>OpenAI</p>',
])
def test_bottom_furniture_goes_and_body_stays(furniture):
    assert str(clean(BODY + furniture)) == BODY


def test_tag_item_goes_but_rest_of_byline_stays():
    html = '<ul><li><span>By:</span><ul><li><a href="/u">Unit 42</a></li></ul></li><li><p>Published: September 25, 2026</p></li><li><span>Categories:</span><ul><li><a href="/i">Insights</a></li><li><a href="/m">Myth Busting</a></li></ul></li></ul>' + BODY
    result = text(html)
    assert 'Unit 42' in result and 'September 25, 2026' in result
    assert 'Categories' not in result and 'Myth Busting' not in result


def test_tag_item_after_icon_placeholder_goes():
    html = '<ul><li><p>Published: September 25, 2026</p></li><li><blockquote><p>Image placeholder: Tags Icon</p></blockquote><span>Categories:</span><ul><li><a href="/i">Insights</a></li></ul></li></ul>' + BODY
    result = text(html)
    assert 'September 25, 2026' in result and 'Insights' not in result


def test_list_mentioning_a_tag_word_mid_sentence_stays():
    item = '<li>The attackers used tags: a detail worth noting here.</li>'
    assert 'worth noting' in text('<ul>' + item + '<li>Second point</li></ul>' + BODY)


def test_footnotes_survive_after_furniture():
    tail = '<h2>Footnotes</h2><ol><li>These figures describe attempted extractions.</li></ol>'
    result = str(clean(BODY + '<ul><li><a href="/y">2026</a></li></ul><h2>Author</h2><p>OpenAI</p>' + tail))
    assert result == BODY + tail


def test_related_section_keeps_references_after_it():
    tail = '<h2>References</h2><ul><li>[1] A paper</li></ul>'
    assert str(clean(BODY + '<h2>Related posts</h2><ul><li><a href="/1">Another post</a></li></ul>' + tail)) == BODY + tail


@pytest.mark.parametrize('kept', [
    # Real sections that share or resemble a furniture label.
    '<h2>What’s next</h2><p>We will keep expanding what our agents can do across the coming year.</p>',
    '<h2>2 Related Work</h2><p>Earlier studies looked at this problem.</p>',
    '<h2>Topics</h2><p>This report covers three topics in depth, starting with phishing.</p>',
    '<p>Category</p><p>Jailbreak resistance scored highest of all.</p>',
    '<h2>Further reading</h2><p><a href="/deep">Our detailed technical write-up of the scanner</a> covers more.</p>',
    '<h2>Authors</h2><p>Andrew Fasano, Marius Fleischer, Cole McFaul</p>',
    '<h2>Conclusion</h2><p>Share your findings with defenders.</p>',
    '<p><a href="/report">Read the full report</a></p>',
    '<blockquote>Image placeholder: Chart of attacks by month View original image</blockquote>',
])
def test_real_content_is_kept(kept):
    assert kept in str(clean(BODY + kept))


def test_real_video_after_related_block_is_kept():
    video = '<blockquote>Image placeholder: Video thumbnail View original image</blockquote>'
    html = '<p>Related articles</p><ul><li><a href="/1">Policy Feb 26, 2026 Statement</a></li></ul><p><a href="/m">View more</a></p>' + video + BODY
    assert str(clean(html)) == video + BODY


def test_author_card_keeps_name_and_drops_link():
    html = BODY + '<h1>Sai Chakri</h1><p>Security Researcher II</p><p><a href="/a">See posts by this author</a></p>'
    result = clean(html)
    assert 'Sai Chakri' in result.get_text() and 'Security Researcher II' in result.get_text()
    assert 'See posts' not in result.get_text() and result.find('h1') is None


def test_related_heading_mid_page_only_removes_its_own_list():
    after = ''.join(f'<p>{BODY}</p>' for _ in range(2))
    assert 'Second' in text(BODY + '<h2>Related posts</h2><ul><li><a href="/1">Post</a></li></ul>' + after)


def test_refuses_to_gut_an_article_when_rules_misfire():
    html = '<p>Short intro.</p>' + '<h2>Tags</h2><ul>' + ''.join(f'<li><a href="/{i}">Tag {i}</a></li>' for i in range(40)) + '</ul>'
    assert 'Tag 39' in text(html)


def test_clean_article_pipeline_strips_furniture():
    from intel_sourcerer.content import clean_article_html
    root = BeautifulSoup('<article>' + BODY + '<h2>Related posts</h2><ul><li><a href="https://x/1">Post</a></li></ul></article>', 'html.parser').article
    result = clean_article_html(root)
    assert 'Related posts' not in result and 'Sixth' in result


def test_reclean_updates_stored_articles_only_when_saving(tmp_path):
    from datetime import datetime, timezone
    from intel_sourcerer.config import PublisherSource
    from intel_sourcerer.feeds import Article
    from intel_sourcerer.reparse import reclean
    from intel_sourcerer.storage import connect_database, save_article, save_source
    path = tmp_path / 'lib.sqlite3'
    db = connect_database(path)
    save_source(db, PublisherSource('test', 'Test', 'https://example.com', 'ai_lab'))
    save_article(db, 'test', Article('Test', 'A', 'https://example.com/a', 'a', datetime(2026, 1, 1, tzinfo=timezone.utc), None, None, ''), content='x', error=None, reason='r', discovered_at='t', markdown='x', html=BODY + '<h2>Tags</h2><ul><li><a href="/1">AI</a></li></ul>')
    db.execute("UPDATE articles SET notes='keep me'")
    db.commit()
    db.close()
    assert reclean(path, dry_run=True)['changed'] == 1
    db = connect_database(path)
    assert 'Tags' in db.execute('SELECT content_html FROM articles').fetchone()[0]
    db.close()
    assert reclean(path)['changed'] == 1
    db = connect_database(path)
    row = db.execute('SELECT content_html,notes FROM articles').fetchone()
    assert row['content_html'] == BODY and row['notes'] == 'keep me'
    db.close()
    assert reclean(path)['changed'] == 0


H1 = '<h1>Developing Enterprise Frontier Safeguards</h1><p>Body</p>'


@pytest.mark.parametrize('title', ['Announcements Developing Enterprise Frontier Safeguards', 'Frontier Red Team Developing Enterprise Frontier Safeguards'])
def test_category_prefix_comes_off_title(title):
    from intel_sourcerer.boilerplate import title_without_category
    assert title_without_category(title, H1) == 'Developing Enterprise Frontier Safeguards'


@pytest.mark.parametrize('title,html', [
    ('Developing Enterprise Frontier Safeguards', H1),
    ('Part 2: Developing Enterprise Frontier Safeguards', H1),
    ('A much longer custom title that someone chose Developing Enterprise Frontier Safeguards', H1),
    ('Disrupting malicious uses of AI | February 2026', '<h1>Disrupting malicious uses of AI</h1>'),
    ('Scaling security with responsible disclosure', '<h1>Scaling coordinated vulnerability disclosure</h1>'),
    ('My renamed article', H1),
    ('Security Updates', '<h1>Updates</h1>'),
    ('No heading here at all', '<p>Body</p>'),
    ('No body at all', None),
])
def test_other_titles_are_left_alone(title, html):
    from intel_sourcerer.boilerplate import title_without_category
    assert title_without_category(title, html) == title


def _library(tmp_path, title):
    from datetime import datetime, timezone
    from intel_sourcerer.config import PublisherSource
    from intel_sourcerer.feeds import Article
    from intel_sourcerer.storage import connect_database, save_article, save_source
    db = connect_database(tmp_path / 'titles.sqlite3')
    save_source(db, PublisherSource('test', 'Test', 'https://example.com', 'ai_lab'))
    article = Article('Test', title, 'https://example.com/a', 'a', datetime(2026, 1, 1, tzinfo=timezone.utc), None, None, '')
    return db, article, save_article


def test_saving_and_reimporting_keeps_the_clean_title(tmp_path):
    title = 'Announcements Developing Enterprise Frontier Safeguards'
    db, article, save = _library(tmp_path, title)
    save(db, 'test', article, content='x', error=None, reason='r', discovered_at='t', markdown='x', html=H1)
    assert db.execute('SELECT title FROM articles').fetchone()[0] == 'Developing Enterprise Frontier Safeguards'
    # A listing refresh carries no body, so the stored heading still decides.
    save(db, 'test', article, content=None, error=None, reason='r', discovered_at='t')
    assert db.execute('SELECT title FROM articles').fetchone()[0] == 'Developing Enterprise Frontier Safeguards'
    db.close()


def test_reclean_fixes_saved_titles_and_dry_run_does_not(tmp_path):
    from intel_sourcerer.reparse import reclean
    title = 'Announcements Developing Enterprise Frontier Safeguards'
    db, article, save = _library(tmp_path, 'Placeholder title here')
    save(db, 'test', article, content='x', error=None, reason='r', discovered_at='t', markdown='x', html=H1)
    db.execute('UPDATE articles SET title=?', (title,))
    db.commit()
    db.close()
    path = tmp_path / 'titles.sqlite3'
    assert reclean(path, dry_run=True)['titles'] == [{'from': title, 'to': 'Developing Enterprise Frontier Safeguards'}]
    from intel_sourcerer.storage import connect_database
    db = connect_database(path)
    assert db.execute('SELECT title FROM articles').fetchone()[0] == title
    db.close()
    assert len(reclean(path)['titles']) == 1
    assert reclean(path)['titles'] == []
