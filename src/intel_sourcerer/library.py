"""Searchable local article and notes library."""
from html import escape
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from markdownify import markdownify
from .sessions import has_notes

def export_article(db,url):
    row=db.execute('SELECT * FROM articles WHERE url=?',(url,)).fetchone()
    if row is None:return None
    notes=markdownify(row['notes']) if row['notes_format']=='html' else row['notes']
    return f"# {row['title']}\n\nSource: {url}\n\nPublished: {row['published_at']}\n\n## Notes\n\n{notes}\n\n## Article\n\n{row['content_markdown'] or row['content_text'] or ''}\n"

def render_library(db,query='',notes_only=False,selected=''):
    from .sessions import clean_notes
    rows=db.execute('SELECT a.*,s.name AS publisher FROM articles a JOIN sources s ON s.id=a.source_id ORDER BY a.notes_updated_at DESC,a.published_at DESC').fetchall()
    terms=query.casefold().split()
    matches=[]
    for row in rows:
        notes=BeautifulSoup(row['notes'],'html.parser').get_text(' ',strip=True) if row['notes_format']=='html' else row['notes']
        if notes_only and not has_notes(row['notes'],row['notes_format']):continue
        if all(term in ' '.join([row['title'],row['publisher'],row['content_text'] or '',notes]).casefold() for term in terms):matches.append(row)
    active=next((r for r in matches if r['url']==selected),matches[0] if matches else None)
    def link(**kwargs):
        return escape('/library?'+urlencode(dict(q=query,notes='1' if notes_only else '0',**kwargs)),quote=True)
    items=[]
    for row in matches:
        is_active=active['url']==row['url']
        badges=('<span class="lib-read">Read</span>' if row['is_read'] else '<span>Unread</span>')+('<span class="lib-noted">Noted</span>' if has_notes(row['notes'],row['notes_format']) else '')
        items.append(f'<a class="lib-item {"selected" if is_active else ""}" href="{link(selected=row["url"])}" {"aria-current=true" if is_active else ""}><span class="lib-publisher">{escape(row["publisher"])}</span><h2>{escape(row["title"])}</h2><span class="lib-meta">{escape(row["published_at"][:10])}{badges}</span></a>')
    detail='<div class="lib-empty"><h2>No matching articles</h2><p>Try another search or switch to All articles.</p></div>'
    if active:
        notes=clean_notes(active['notes']) if active['notes_format']=='html' else '<p>'+escape(active['notes']).replace('\n','<br>')+'</p>'
        if not has_notes(active['notes'],active['notes_format']):notes='<p class="muted">No notes yet. Open this article to read and add your takeaways.</p>'
        opened=escape('/?'+urlencode({'open':active['url']}),quote=True)
        exported=escape('/export?'+urlencode({'url':active['url']}),quote=True)
        edited='Edited '+active['notes_updated_at'][:10] if active['notes_updated_at'] else ''
        detail=f'<div class="lib-detail-heading"><span>YOUR NOTES</span><span>{edited}</span></div><h2>{escape(active["title"])}</h2><p class="muted">{escape(active["publisher"])} · {escape(active["published_at"][:10])}</p><div class="lib-actions"><a class="lib-open" href="{opened}">Open article</a><a href="{exported}" download="article-notes.md">Export Markdown</a></div><div class="lib-note-content">{notes}</div>'
    all_link=escape('/library?'+urlencode({'q':query}),quote=True)
    notes_link=escape('/library?'+urlencode({'q':query,'notes':'1'}),quote=True)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Library · Intel Sourcerer</title><link rel="stylesheet" href="/reader.css"></head><body><main class="library-page"><header class="builder-header"><div class="brand"><img class="brand-icon" src="/brand-icon.png" alt="">Intel Sourcerer</div><nav class="builder-links" aria-label="Main navigation"><a href="/">Home</a><a href="/?view=builder">Queue builder</a><a class="active" aria-current="page" href="/library">Library</a><a href="/sources">Sources</a><a href="/stats">Stats</a></nav></header><h1>Library</h1><form action="/library" class="lib-search"><label class="sr-only" for="query">Search articles and notes</label><input type="search" id="query" name="q" value="{escape(query,quote=True)}" placeholder="Search articles and notes"><input type="hidden" name="notes" value="{'1' if notes_only else '0'}"><button type="submit">Search</button></form><nav class="lib-tabs" aria-label="Library filters"><a class="{'active' if not notes_only else ''}" href="{all_link}">All articles</a><a class="{'active' if notes_only else ''}" href="{notes_link}">With notes</a><span>{len(matches)} articles</span></nav><div class="lib-workspace"><section class="lib-list" aria-label="Saved articles">{''.join(items) or '<p class="muted">No results</p>'}</section><section class="lib-detail" aria-label="Notes preview">{detail}</section></div></main></body></html>'''
