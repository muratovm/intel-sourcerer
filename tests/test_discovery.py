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


def pdf_bytes(text=None):
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    writer.add_metadata({'/Title': 'Cybersecurity PDF report'})
    if text:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 50 700 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_link_import(tmp_path, monkeypatch):
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, headers={'content-type': 'application/octet-stream'}, content=pdf_bytes('Threat intelligence from a PDF report.')))
    monkeypatch.setattr(discovery.httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    monkeypatch.setattr(discovery, 'public_url', lambda url: url)
    path = tmp_path / 'articles.db'
    asyncio.run(discovery.import_link(path, 'https://example.com/download'))
    db = connect_database(path)
    row = db.execute('SELECT * FROM articles').fetchone()
    assert row['title'] == 'Cybersecurity PDF report'
    assert 'Threat intelligence' in row['content_text']
    assert 'Threat intelligence' in row['content_markdown']
    db.close()


def test_pdf_without_text_and_invalid_pdf():
    from intel_sourcerer.pdf_content import extract_pdf
    with pytest.raises(ValueError, match='OCR'):
        extract_pdf(pdf_bytes(), 'https://example.com/report.pdf')
    with pytest.raises(ValueError):
        extract_pdf(b'%PDF-invalid', 'https://example.com/report.pdf')


def test_local_pdf_paths_and_file_urls(tmp_path):
    file = tmp_path / 'local report.pdf'
    file.write_bytes(pdf_bytes('Local threat research.'))
    database = tmp_path / 'articles.db'
    url = asyncio.run(discovery.import_link(database, str(file)))
    assert url == file.as_uri()
    assert asyncio.run(discovery.import_link(database, file.as_uri())) == url
    db = connect_database(database)
    assert db.execute('SELECT COUNT(*) FROM articles').fetchone()[0] == 1
    assert 'Local threat research' in db.execute('SELECT content_text FROM articles').fetchone()[0]
    db.close()
    with pytest.raises(ValueError, match='existing local PDF'):
        asyncio.run(discovery.import_link(database, str(tmp_path / 'missing.pdf')))
    with pytest.raises(ValueError, match='remote host'):
        asyncio.run(discovery.import_link(database, 'file://remote/report.pdf'))


def test_pdf_file_upload(tmp_path):
    database = tmp_path / 'articles.db'
    payload = pdf_bytes('Uploaded threat intelligence.')
    url = asyncio.run(discovery.import_pdf_upload(database, payload))
    assert asyncio.run(discovery.import_pdf_upload(database, payload)) == url
    assert len(list((tmp_path / 'uploads').glob('*.pdf'))) == 1
    db = connect_database(database)
    assert 'Uploaded threat intelligence' in db.execute('SELECT content_text FROM articles').fetchone()[0]
    db.close()
    with pytest.raises(ValueError):
        asyncio.run(discovery.import_pdf_upload(database, b'not a PDF'))


def test_rename_saved_pdf_preserves_notes(tmp_path):
    from intel_sourcerer.sessions import rename_article, SessionError
    file = tmp_path / 'report.pdf'
    file.write_bytes(pdf_bytes('Saved content.'))
    database = tmp_path / 'articles.db'
    url = asyncio.run(discovery.import_link(database, str(file)))
    db = connect_database(database)
    db.execute("UPDATE articles SET notes='My notes',is_read=1")
    assert rename_article(db, url, '  Better title  ') == {'title': 'Better title'}
    row = db.execute('SELECT title,notes,is_read FROM articles').fetchone()
    assert tuple(row) == ('Better title', 'My notes', 1)
    with pytest.raises(SessionError):
        rename_article(db, url, ' ')
    with pytest.raises(SessionError):
        rename_article(db, 'missing', 'Title')
    db.close()
