"""Find which saved reports share indicators, using an index stored beside the articles."""
from html import escape
import time

from .content import format_label, render_article_html, render_markdown
from .iocs import extract_iocs

# Raise this when extraction changes, so stored articles are read again.
INDEX_VERSION = 1
# Extraction takes tens of milliseconds an article, so a big library catches up over several requests.
INDEX_BUDGET_SECONDS = 4
# Sharing one technique says little, since common ones appear in most reports.
MIN_SHARED_TECHNIQUES = 3
KINDS = ('hashes', 'cves', 'files', 'techniques')


def _body_html(row):
    if row['content_html']:
        return render_article_html(row['content_html'])
    if row['content_markdown']:
        return render_markdown(row['content_markdown'])
    return f"<p>{escape(row['content_text'] or '')}</p>"


def indicators_for_index(html):
    """Return (kind, value) pairs worth matching. Tools every report mentions would link everything."""
    iocs = extract_iocs(html)
    pairs = {(kind, item['value']) for kind in ('hashes', 'cves', 'techniques') for item in iocs[kind]}
    pairs |= {('files', item['value'].lower()) for item in iocs['files'] if not item['common']}
    return pairs


def _index_article(db, row):
    url = row['url']
    # Extract before writing, so other requests can save while this one reads.
    pairs = indicators_for_index(_body_html(row))
    db.execute('DELETE FROM article_indicators WHERE article_url=?', (url,))
    db.executemany('INSERT INTO article_indicators VALUES (?,?,?)', [(url, kind, value) for kind, value in pairs])
    db.execute('INSERT OR REPLACE INTO indicator_index_state VALUES (?,?)', (url, INDEX_VERSION))
    db.commit()


def refresh_index(db, first_url=None, budget=INDEX_BUDGET_SECONDS, clock=time.monotonic):
    """Index articles that are new or changed, the given one first. Return how many are still waiting."""
    stale = [
        row['url']
        for row in db.execute(
            '''SELECT a.url FROM articles a LEFT JOIN indicator_index_state s ON s.article_url=a.url
            WHERE s.version IS NULL OR s.version<>? ORDER BY a.url=? DESC, a.published_at DESC, a.url''',
            (INDEX_VERSION, first_url),
        )
    ]
    deadline = clock() + budget
    done = 0
    for url in stale:
        if clock() >= deadline:
            break
        row = db.execute('SELECT url,content_html,content_markdown,content_text FROM articles WHERE url=?', (url,)).fetchone()
        if row:
            _index_article(db, row)
        done += 1
    return len(stale) - done


def _rank(report):
    # Another publisher naming the same value is corroboration. The same one may just be repeating a table.
    shared = report['shared']
    return (report['other_publisher'], *(len(shared[kind]) for kind in KINDS), report['published_at'])


def related_reports(db, url):
    """Report every value this article shares, and the articles that share enough to list as related."""
    remaining = refresh_index(db, url)
    rows = db.execute(
        '''SELECT mine.kind,mine.value,a.url,a.title,a.published_at,a.content_format,s.name AS publisher,
        a.source_id<>current.source_id AS other_publisher
        FROM article_indicators mine
        JOIN articles current ON current.url=mine.article_url
        JOIN article_indicators other ON other.kind=mine.kind AND other.value=mine.value AND other.article_url<>mine.article_url
        JOIN articles a ON a.url=other.article_url
        JOIN sources s ON s.id=a.source_id AND s.enabled=1
        WHERE mine.article_url=?''',
        (url,),
    ).fetchall()
    reports, values = {}, {}
    for row in rows:
        report = reports.setdefault(
            row['url'],
            {
                'url': row['url'],
                'title': row['title'],
                'publisher': row['publisher'],
                'published_at': row['published_at'],
                'format_label': format_label(row['content_format'], row['publisher']),
                'other_publisher': bool(row['other_publisher']),
                'shared': {kind: [] for kind in KINDS},
            },
        )
        report['shared'][row['kind']].append(row['value'])
        values.setdefault(row['kind'], {}).setdefault(row['value'], []).append(row['url'])
    ordered = sorted(reports.values(), key=_rank, reverse=True)
    for report in ordered:
        for kind in KINDS:
            report['shared'][kind].sort()
    order = {report['url']: index for index, report in enumerate(ordered)}
    for urls in (urls for kind in values.values() for urls in kind.values()):
        urls.sort(key=order.__getitem__)
    return {
        'complete': remaining == 0,
        'reports': ordered,
        'related': [
            report['url']
            for report in ordered
            if any(report['shared'][kind] for kind in KINDS if kind != 'techniques')
            or len(report['shared']['techniques']) >= MIN_SHARED_TECHNIQUES
        ],
        'values': values,
    }
