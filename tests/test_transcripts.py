import asyncio
import threading
import time
from io import BytesIO
import wave
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from intel_sourcerer import transcripts
from intel_sourcerer.media_import import import_file
from intel_sourcerer.storage import connect_database


SEGMENTS = [
    {'start': 0.0, 'text': 'Today we look at the botnet.'},
    {'start': 20.0, 'text': 'It uses CVE-2026-109618 and *spreads* fast.'},
    {'start': 50.0, 'text': 'The operators reused infrastructure.'},
    {'start': 3700.0, 'text': 'Thanks for watching.'},
]


def wait_for(database, url, states=('done', 'failed')):
    for _ in range(200):
        if transcripts.status(database, url).get('state') in states:
            return transcripts.status(database, url)
        time.sleep(0.01)
    raise AssertionError('transcription did not finish')


def test_markdown_groups_paragraphs_with_timestamps_and_escapes_text():
    markdown = transcripts.transcript_markdown(SEGMENTS)
    assert markdown.startswith('## Transcript\n\n_Machine transcript')
    paragraphs = markdown.split('\n\n')[2:]
    assert paragraphs[0].startswith('**0:00** Today we look')
    assert '\\*spreads\\*' in paragraphs[0]
    assert paragraphs[0].endswith('The operators reused infrastructure.')
    assert paragraphs[1] == '**1:01:40** Thanks for watching.'
    assert transcripts.timestamp(3700) == '1:01:40'
    assert transcripts.transcript_markdown([]) == ''


def test_saving_replaces_an_earlier_transcript_and_updates_search_text(tmp_path):
    database = tmp_path / 'articles.db'
    url = asyncio.run(import_file(database, b'video bytes', 'talk.mp4'))
    transcripts.save_transcript(database, url, SEGMENTS)
    transcripts.save_transcript(database, url, [{'start': 0, 'text': 'Second take.'}])
    with connect_database(database) as db:
        row = db.execute('SELECT content_markdown, content_text FROM articles WHERE url=?', (url,)).fetchone()
    assert row['content_markdown'].count('## Transcript') == 1
    assert 'Second take.' in row['content_markdown'] and 'botnet' not in row['content_markdown']
    assert '/local-media/' in row['content_markdown']
    assert 'Second take.' in row['content_text']
    assert transcripts.has_transcript(row['content_markdown'])


def test_only_files_in_the_media_folder_count_as_local_media(tmp_path):
    database = tmp_path / 'articles.db'
    url = asyncio.run(import_file(database, b'video bytes', 'talk.mp4'))
    assert transcripts.local_media_path(database, url).suffix == '.mp4'
    outside = tmp_path / 'secret.mp4'
    outside.write_bytes(b'x')
    assert transcripts.local_media_path(database, outside.as_uri()) is None
    assert transcripts.local_media_path(database, 'https://www.youtube.com/watch?v=abcdefghijk') is None


def test_background_job_saves_the_transcript_and_removes_uploaded_audio(tmp_path):
    database = tmp_path / 'articles.db'
    url = asyncio.run(import_file(database, b'video bytes', 'talk.mp4'))
    audio = transcripts.save_upload(database, b'audio', 'talk.m4a')
    assert transcripts.start(database, url, audio, remove_after=True, transcriber=lambda path: SEGMENTS) == {'state': 'queued'}
    assert wait_for(database, url)['state'] == 'done'
    assert not audio.exists()
    with connect_database(database) as db:
        assert 'operators reused' in db.execute('SELECT content_markdown FROM articles WHERE url=?', (url,)).fetchone()[0]


def test_failed_jobs_report_why(tmp_path):
    database = tmp_path / 'articles.db'
    url = asyncio.run(import_file(database, b'video bytes', 'talk.mp4'))
    transcripts.start(database, url, transcripts.local_media_path(database, url), transcriber=lambda path: [])
    assert wait_for(database, url) == {'state': 'failed', 'error': 'Whisper found no speech in this file.'}


def test_uploads_must_be_audio(tmp_path):
    try:
        transcripts.save_upload(tmp_path / 'articles.db', b'x', 'notes.txt')
    except ValueError as exc:
        assert 'audio file' in str(exc)
    else:
        raise AssertionError('text upload accepted')


def test_reader_offers_and_runs_transcription(tmp_path, monkeypatch):
    from intel_sourcerer.web import create_server

    calls = []
    monkeypatch.setattr(transcripts, 'available', lambda: True)
    monkeypatch.setattr(transcripts, 'transcribe', lambda path: calls.append(path) or SEGMENTS)
    path = tmp_path / 'articles.db'
    connect_database(path).close()
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b'\0\0' * 800)
    server = create_server(path, 0, auth_enabled=False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False) as client:
            token = BeautifulSoup(client.get('/').text, 'html.parser').select_one('meta[name="csrf-token"]')['content']
            headers = {'X-CSRF-Token': token}
            # A local file starts transcribing as soon as it is imported.
            url = client.post('/api/file-upload', content=buffer.getvalue(), headers={**headers, 'X-Filename': 'talk.wav'}).json()['url']
            wait_for(path, url)
            article = client.get('/api/article', params={'url': url}).json()
            assert article['transcript']['has_transcript'] and article['transcript']['local']
            assert 'Transcript' in article['body_html'] and 'CVE-2026-109618' in {i['value'] for i in article['iocs']['cves']}
            assert client.post('/api/transcribe', json={'url': url}, headers=headers).json() == {'state': 'queued'}
            wait_for(path, url)
            assert len(calls) == 2
            # A YouTube item takes attached audio instead.
            with connect_database(path) as db:
                from datetime import datetime, timezone
                from intel_sourcerer.config import PublisherSource
                from intel_sourcerer.feeds import Article
                from intel_sourcerer.storage import save_article, save_source
                save_source(db, PublisherSource('youtube', 'YouTube', 'https://www.youtube.com', 'manual'))
                now = datetime.now(timezone.utc)
                save_article(db, 'youtube', Article('YouTube', 'Talk', 'https://www.youtube.com/watch?v=abcdefghijk', 'https://www.youtube.com/watch?v=abcdefghijk', now, 'Channel', None, ''),
                             content='YouTube video by Channel.', error=None, reason='test', discovered_at=now.isoformat(), markdown='> **Video placeholder:** Talk')
                db.execute("UPDATE articles SET content_format='video'")
                db.execute("UPDATE articles SET content_format='audio' WHERE url=?", (url,))
            video = 'https://www.youtube.com/watch?v=abcdefghijk'
            info = client.get('/api/article', params={'url': video}).json()['transcript']
            assert info == {'available': True, 'local': False, 'youtube': False, 'has_transcript': False, 'state': '', 'error': ''}
            assert client.post('/api/transcribe', json={'url': video}, headers=headers).status_code == 400
            upload = client.post('/api/transcript-audio', content=b'audio', headers={**headers, 'X-Filename': 'abcdefghijk.m4a', 'X-Article-URL': video})
            assert upload.status_code == 200
            wait_for(path, video)
            assert client.get('/api/article', params={'url': video}).json()['transcript']['has_transcript']
            assert not list((tmp_path / 'transcribe-uploads').iterdir())
            refused = client.post('/api/transcript-audio', content=b'audio', headers={**headers, 'X-Filename': 'a.m4a', 'X-Article-URL': 'https://example.com/none'})
            assert refused.status_code == 400
    finally:
        server.shutdown(); server.server_close()


def test_youtube_job_downloads_transcribes_and_cleans_up(tmp_path):
    database = tmp_path / 'articles.db'
    connect_database(database).close()
    seen = {}

    def fake_download(identifier, folder):
        seen['folder'] = folder
        audio = Path(folder) / (identifier + '.m4a')
        audio.write_bytes(b'audio')
        return audio

    def fake_transcribe(path):
        assert Path(path).exists() and transcripts.status(database, 'u')['state'] == 'running'
        return SEGMENTS

    transcripts.start(database, 'u', youtube='abcdefghijk', downloader=fake_download, transcriber=fake_transcribe)
    # No article row exists, so saving fails, but the download folder is still removed.
    assert wait_for(database, 'u')['error'] == 'Article not found.'
    assert not Path(seen['folder']).exists()


def test_download_rejects_anything_but_a_video_id(tmp_path, monkeypatch):
    import pytest
    for bad in ['', 'https://x.test/a', '--exec=rm', 'abcdefghij']:
        with pytest.raises(ValueError, match='Not a YouTube video ID'):
            transcripts.download_youtube(bad, tmp_path)


def test_youtube_download_is_for_administrators_only(monkeypatch):
    from intel_sourcerer.web import youtube_download_id
    monkeypatch.setattr(transcripts, 'downloader_command', lambda: '/usr/bin/true')
    video = 'https://www.youtube.com/watch?v=abcdefghijk'
    assert youtube_download_id(video, True) == 'abcdefghijk'
    assert youtube_download_id(video, False) is None
    assert youtube_download_id('https://example.com/watch?v=abcdefghijk', True) is None
