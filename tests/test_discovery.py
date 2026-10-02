import asyncio
from datetime import datetime, timezone
import httpx
import pytest
from intel_sourcerer import discovery
from intel_sourcerer.storage import connect_database


def test_types_and_private_addresses(monkeypatch):
    assert 'Vulnerabilities' in discovery.article_types('CVE-2026 exploit analysis')
    assert 'Incident analysis' in discovery.article_types('Disrupting an espionage campaign')
    monkeypatch.setattr(discovery.socket,'getaddrinfo',lambda *a,**k:[(0,0,0,'',('127.0.0.1',80))])
    with pytest.raises(ValueError):discovery.public_url('http://localhost/article')
    with pytest.raises(ValueError):discovery.public_url('file:///etc/passwd')


def test_link_import_extracts_content_and_preserves_notes(tmp_path,monkeypatch):
    original=httpx.AsyncClient
    transport=httpx.MockTransport(lambda request:httpx.Response(200,headers={'content-type':'text/html'},text='<html><head><title>Cyber report</title></head><body><article><h1>Cyber report</h1><p>A detailed cybersecurity article about defenders tracking new adversarial campaigns and sharing intelligence across industry partners.</p></article></body></html>'))
    monkeypatch.setattr(discovery.httpx,'AsyncClient',lambda **kwargs:original(transport=transport,**kwargs))
    monkeypatch.setattr(discovery,'public_url',lambda url:url)
    path=tmp_path/'articles.db'
    url=asyncio.run(discovery.import_link(path,'https://example.com/report#part'))
    assert url=='https://example.com/report'
    db=connect_database(path)
    row=db.execute('SELECT * FROM articles').fetchone()
    assert row['title']=='Cyber report' and row['content_markdown']
    db.execute("UPDATE articles SET notes='Takeaway',is_read=1");db.commit();db.close()
    asyncio.run(discovery.import_link(path,url))
    db=connect_database(path)
    assert tuple(db.execute('SELECT notes,is_read FROM articles').fetchone())==('Takeaway',1)
    db.close()
