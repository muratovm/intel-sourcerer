from bs4 import BeautifulSoup
from intel_sourcerer.web import render_page


def test_complete_text_and_original_link_are_safely_rendered():
    text='First paragraph\n\n<script>alert("x")</script>\nFinal paragraph'
    page=render_page([dict(title='<b>Cyber report</b>',publisher='Example',published_at='2026-09-10T04:00:00+00:00',url='https://example.com/report?a=1&b=2',content_text=text)])
    soup=BeautifulSoup(page,'html.parser')
    assert soup.find('details').find('summary')
    assert soup.select_one('.content').get_text()==text
    assert soup.find('script') is None
    assert soup.select_one('.headline').get_text()=='<b>Cyber report</b>'
    assert soup.select_one('a.original')['href']=='https://example.com/report?a=1&b=2'


def test_missing_content_and_unsafe_link():
    page=render_page([dict(title='Report',publisher='Example',published_at='unknown',url='javascript:alert(1)',content_text=None)])
    soup=BeautifulSoup(page,'html.parser')
    assert soup.find('a') is None
    assert 'has not been saved' in soup.get_text()
    assert 'Date unavailable' in soup.get_text()


def test_empty_database_message():
    assert 'No saved articles yet' in render_page([])



def test_read_toggle_persists_both_states_and_rejects_invalid_requests(tmp_path):
    import threading
    import httpx
    from datetime import datetime, timezone
    from intel_sourcerer.storage import connect_database, save_source, save_article
    from intel_sourcerer.config import PublisherSource
    from intel_sourcerer.feeds import Article
    from intel_sourcerer.web import create_server
    path=tmp_path/'articles.sqlite3'
    db=connect_database(path)
    source=PublisherSource('test','Test','https://example.com','ai_lab')
    save_source(db,source)
    article=Article('Test','Report','https://example.com/report','report',datetime.now(timezone.utc),None,None,'')
    save_article(db,source.id,article,content='Article text',error=None,reason='test',discovered_at='today')
    db.commit()
    server=create_server(path,0, auth_enabled=False)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}',follow_redirects=True,trust_env=False) as client:
            for state,label in [(1,'Mark unread'),(0,'Mark read')]:
                soup=BeautifulSoup(client.get('/classic').text,'html.parser')
                token=soup.select_one('input[name=csrf_token]')['value']
                response=client.post('/read-status',data={'url':article.url,'is_read':str(state),'csrf_token':token})
                assert response.status_code==200
                soup=BeautifulSoup(response.text,'html.parser')
                assert soup.select_one('details').has_attr('open')
                assert soup.select_one('button').get_text()==label
                assert db.execute('SELECT is_read FROM articles').fetchone()[0]==state
            assert client.post('/read-status',data={'url':article.url,'is_read':'1'}).status_code==403
            assert client.post('/read-status',data={'url':article.url,'is_read':'2','csrf_token':token}).status_code==400
            assert client.post('/read-status',data={'url':'https://example.com/missing','is_read':'1','csrf_token':token}).status_code==404
            root=BeautifulSoup(client.get('/').text,'html.parser')
            headers={'X-CSRF-Token':root.select_one('meta[name="csrf-token"]')['content']}
            assert client.post('/api/queue',json={'urls':[article.url]}).status_code==403
            q=client.post('/api/queue',json={'urls':[article.url]},headers=headers).json()
            assert q['cursor']==0
            assert client.post('/api/notes',json={'url':article.url,'text':'Takeaway','revision':0},headers=headers).json()=={'revision':1}
            assert client.post('/api/notes',json={'url':article.url,'text':'Stale','revision':0},headers=headers).status_code==409
            assert client.get('/api/article',params={'url':article.url}).json()['notes']=='Takeaway'
            q=client.post('/api/navigate',json={'queue_id':q['id'],'cursor':0,'action':'mark_next'},headers=headers).json()
            assert q['finished'] and q['read_count']==1
            assert client.get('/api/state').json()['queue']['finished']
            db.execute('UPDATE articles SET content_markdown=? WHERE url=?',('> **Image placeholder:** Chart\n>\n> [View original image](https://example.com/chart.png)',article.url));db.commit()
            response=client.get('/api/article',params={'url':article.url})
            media=BeautifulSoup(response.json()['body_html'],'html.parser').select_one('[data-media-kind="image"]')
            assert media['data-media-url']=='https://example.com/chart.png'
            assert media['data-media-description']=='Chart'
            assert "img-src 'self' https: http:" in response.headers['Content-Security-Policy']
            assert media.find('a')['href']=='https://example.com/chart.png'


    finally:
        server.shutdown();server.server_close();thread.join();db.close()
