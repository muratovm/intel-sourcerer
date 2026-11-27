from datetime import datetime, timezone
import sqlite3
from intel_sourcerer.storage import connect_database,save_source,save_article
from intel_sourcerer.config import PublisherSource
from intel_sourcerer.feeds import Article
from intel_sourcerer.sessions import save_notes,create_queue,queue_state,delete_article
from intel_sourcerer.library import render_library,export_article
from intel_sourcerer.backups import backup_database


def test_library_export_notes_and_backup(tmp_path):
    path=tmp_path/'articles.sqlite3'
    db=connect_database(path)
    source=PublisherSource('test','Test','https://example.com','ai_lab')
    save_source(db,source)
    article=Article('Test','Example report','https://example.com/report','id',datetime.now(timezone.utc),None,None,'')
    save_article(db,'test',article,content='Unique body phrase',markdown='## Findings\nUnique body phrase',error=None,reason='test',discovered_at='now')
    queue=create_queue(db,[article.url])
    save_notes(db,article.url,'<p>Personal takeaway</p>',0,'html')
    assert db.execute('SELECT notes_updated_at FROM articles').fetchone()[0]
    db.execute('UPDATE sources SET enabled=0');db.commit()
    assert 'Example report' in render_library(db,'unique body')
    assert 'Example report' in render_library(db,'personal',True)
    assert 'No matching articles' in render_library(db,'unmatched')
    page=render_library(db,selected=article.url)
    assert 'aria-label="Notes preview"' in page
    assert '<p>Personal takeaway</p>' in page
    assert '>Notes</a>' not in page
    assert 'aria-current=true' in page
    assert 'Example report' in render_library(db,selected='missing')
    export=export_article(db,article.url)
    assert 'Personal takeaway' in export and '## Findings' in export
    assert queue_state(db)['id']==queue['id']
    snapshot=backup_database(path)
    with sqlite3.connect(snapshot) as restored:
        assert restored.execute('SELECT notes FROM articles').fetchone()[0]=='<p>Personal takeaway</p>'
    assert backup_database(path)==snapshot
    db.execute('PRAGMA foreign_keys=ON')
    delete_article(db,article.url)
    assert not db.execute('SELECT 1 FROM articles WHERE url=?',(article.url,)).fetchone()
    assert queue_state(db)['items']==[]
    assert queue_state(db)['finished']
    assert not db.execute('SELECT 1 FROM article_reads WHERE article_url=?',(article.url,)).fetchone()
    db.close()


def test_library_shows_article_count_and_storage(tmp_path):
    from intel_sourcerer.library import format_size, library_summary, storage_usage
    database = tmp_path / 'articles.db'
    database.write_bytes(b'x' * 2_000_000)
    (tmp_path / 'media').mkdir()
    (tmp_path / 'media' / 'talk.mp4').write_bytes(b'x' * 1_500_000)
    (tmp_path / 'backups').mkdir()
    (tmp_path / 'backups' / 'articles-2026-11-08.sqlite3').write_bytes(b'x' * 3_000_000)
    (tmp_path / 'backups' / 'other-2026-11-08.sqlite3').write_bytes(b'x' * 9_000_000)
    usage = storage_usage(database)
    assert usage == {'stored': 3_500_000, 'backups': 3_000_000}
    assert library_summary(12, usage) == '12 articles · 3.5 MB stored · 3.0 MB in backups'
    assert library_summary(1, None) == '1 article'
    assert format_size(2_400_000_000) == '2.4 GB' and format_size(0) == '0 MB'


def test_backups_keep_only_the_newest_daily_copies(tmp_path):
    from intel_sourcerer.backups import prune_backups
    database = tmp_path / 'articles.db'
    folder = tmp_path / 'backups'; folder.mkdir()
    for day in ['2026-11-01', '2026-11-02', '2026-11-03', '2026-11-08', '2026-11-09']:
        (folder / f'articles-{day}.sqlite3').write_bytes(b'x')
    (folder / 'articles-before-cleanup-2026-11-01.sqlite3').write_bytes(b'x')
    (folder / 'other-2026-10-01.sqlite3').write_bytes(b'x')
    prune_backups(database)
    assert sorted(f.name for f in folder.iterdir()) == [
        'articles-2026-11-03.sqlite3', 'articles-2026-11-08.sqlite3', 'articles-2026-11-09.sqlite3',
        'articles-before-cleanup-2026-11-01.sqlite3', 'other-2026-10-01.sqlite3']


def test_malformed_ipv6_links_do_not_break_rendering():
    from intel_sourcerer.content import render_article_html, safe_url
    html = render_article_html('<p>See http://[fe80::1 and <a href="http://[bad">this</a> and https://example.com/a.</p>')
    assert 'href="https://example.com/a"' in html and 'http://[fe80::1' in html
    assert safe_url('http://[bad', 'https://example.com/') is None


def test_in_progress_tab_lists_started_unread_articles(tmp_path):
    from intel_sourcerer.reading_state import in_progress_count, save_position, save_highlight

    db=connect_database(tmp_path/'articles.sqlite3')
    save_source(db,PublisherSource('test','Test','https://example.com','ai_lab'))
    def add(title):
        article=Article('Test',title,f'https://example.com/{title.split()[0].lower()}','id',datetime.now(timezone.utc),None,None,'')
        save_article(db,'test',article,content='Body',markdown='Body',error=None,reason='test',discovered_at='now')
        return article.url
    scrolled,noted,highlighted,glanced,finished,untouched=(add(name) for name in (
        'Scrolled report','Noted report','Highlighted report','Glanced report','Finished report','Untouched report'))
    save_position(db,scrolled,0.35)
    save_notes(db,noted,'<p>Takeaway</p>',0,'html')
    save_highlight(db,highlighted,'Body')
    save_position(db,glanced,0.01)
    save_position(db,finished,0.6)
    db.execute('UPDATE articles SET is_read=1 WHERE url=?',(finished,))
    db.commit()
    assert in_progress_count(db)==3
    page=render_library(db,progress=True)
    for title in ('Scrolled report','Noted report','Highlighted report'):
        assert title in page
    for title in ('Glanced report','Finished report','Untouched report'):
        assert title not in page
    assert '35% read' in page and 'Started' in page
    assert 'In progress <span class="lib-tab-count">3</span>' in page
    assert 'In progress · 35%' in render_library(db,selected=scrolled)
    # With notes wins when both are asked for, so the tabs never disagree.
    assert 'Glanced report' not in render_library(db,notes_only=True,progress=True)
    save_position(db,glanced,0.02)
    db.commit()
    assert 'Glanced report' in render_library(db,progress=True)
    db.close()


def test_library_detail_shows_clean_article_preview(tmp_path):
    import json
    from intel_sourcerer.library import _banner_image, _preview_excerpt

    db = connect_database(tmp_path / 'articles.sqlite3')
    save_source(db, PublisherSource('blog', 'Security Blog', 'https://example.com', 'security_blog'))
    article = Article(
        'Security Blog',
        'Ransomware Shift in Q3',
        'https://example.com/q3',
        'q3',
        datetime.now(timezone.utc),
        None,
        None,
        '',
    )
    long_body = (
        'Ransomware Shift in Q3\n'
        'Security Blog\n'
        'Overview\n'
        'Ransomware activity reached record levels this quarter across healthcare and manufacturing. '
        'Initial access shifted toward edge appliance vulnerabilities rather than phishing macros. '
        + ('Additional operational telemetry details follow in the full report. ' * 8)
    )
    save_article(db, 'blog', article, content=long_body, markdown=long_body, error=None, reason='test', discovered_at='now')
    media = json.dumps([
        {'position': 1, 'kind': 'image', 'description': 'Security Blog logo', 'url': 'https://example.com/logo.svg'},
        {'position': 2, 'kind': 'image', 'description': 'Article header image', 'url': 'https://example.com/banner-q3.jpg'},
    ])
    db.execute('UPDATE articles SET media_json=? WHERE url=?', (media, article.url))
    db.commit()

    page = render_library(db, selected=article.url)
    assert 'class="lib-article-preview"' in page
    assert 'https://example.com/banner-q3.jpg' in page
    assert 'https://example.com/logo.svg' not in page
    assert page.index('id="lib-topic-editor"') < page.index('class="lib-article-preview"') < page.index('class="lib-note-content"')
    assert 'Ransomware activity reached record levels this quarter' in page
    assert 'Overview' not in page.split('lib-article-preview')[1].split('</div>')[0]

    # arXiv-style abstract jumps directly to abstract text.
    label, paper_excerpt = _preview_excerpt({
        'title': 'Structured CTI',
        'publisher': 'arxiv.org',
        'content_format': 'article',
        'url': 'https://arxiv.org/abs/2601.00001',
        'content_text': 'Structured CTI\nJane Doe\nAbstract—Cyber threat intelligence reports can be parsed into STIX graphs automatically.',
    })
    assert label == 'Preview'
    assert paper_excerpt.startswith('Cyber threat intelligence reports can be parsed')

    # Transcribed video strips Whisper boilerplate and timestamps, and gets its YouTube thumbnail.
    tr_row = {
        'title': 'CTI Prioritization',
        'publisher': 'YouTube',
        'content_format': 'video',
        'url': 'https://www.youtube.com/watch?v=abc123DEF45',
        'has_transcript': 1,
        'content_text': (
            'Video placeholder: CTI Prioritization Watch on YouTube Channel: SANS '
            'Transcript Machine transcript made locally with Whisper. '
            'Check names of actors, malware and tools against the audio. '
            '0:00 Welcome everyone to our talk on priority intelligence requirements.'
        ),
    }
    tr_label, tr_excerpt = _preview_excerpt(tr_row)
    assert tr_label == 'Transcript preview'
    assert tr_excerpt.startswith('Welcome everyone to our talk')
    assert _banner_image(tr_row) == 'https://i.ytimg.com/vi/abc123DEF45/hqdefault.jpg'

    # Un-transcribed YouTube boilerplate should not render redundant text.
    empty_label, empty_excerpt = _preview_excerpt({
        'title': 'Talk',
        'publisher': 'YouTube',
        'content_format': 'video',
        'url': 'https://www.youtube.com/watch?v=def456GHI78',
        'has_transcript': 0,
        'content_text': 'YouTube video by SANS.',
        'summary_html': '',
    })
    assert empty_label == '' and empty_excerpt == ''

    # Bare RSS email authors are omitted while human names and handles are kept.
    from intel_sourcerer.library import _clean_author
    assert _clean_author('matthewsu@checkpoint.com') == ''
    assert _clean_author('matthewsu@checkpoint.com (Matthew Su)') == 'Matthew Su'
    assert _clean_author('Sina Kheirkhah (@SinSinology)') == 'Sina Kheirkhah (@SinSinology)'
    db.close()
