"""Transcribe audio and video locally with Whisper and keep the transcript with the article."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit, unquote

from bs4 import BeautifulSoup

from .content import render_markdown
from .storage import connect_database

MODEL = 'mlx-community/whisper-large-v3-turbo'
HEADING = '## Transcript'
NOTE = '_Machine transcript made locally with Whisper. Check names of actors, malware and tools against the audio._'
AUDIO_UPLOADS = {'.m4a', '.mp3', '.ogg', '.wav', '.webm', '.mp4', '.opus'}
# Whisper uses the whole machine, so transcriptions run one at a time.
_worker = threading.Lock()
_jobs = {}
BUSY = {'queued', 'downloading', 'running'}
_jobs_lock = threading.Lock()


def whisper_command():
    configured = os.environ.get('INTEL_SOURCERER_WHISPER')
    candidates = [configured] if configured else [shutil.which('mlx_whisper'), str(Path.home() / '.local/bin/mlx_whisper')]
    return next((c for c in candidates if c and Path(c).is_file() and os.access(c, os.X_OK)), None)


def available():
    return whisper_command() is not None


def downloader_command():
    """uvx runs yt-dlp without installing it; the server reads YouTube with the owner's Chrome cookies."""
    configured = os.environ.get('INTEL_SOURCERER_UVX')
    candidates = [configured] if configured else [shutil.which('uvx'), str(Path.home() / '.local/bin/uvx')]
    return next((c for c in candidates if c and Path(c).is_file() and os.access(c, os.X_OK)), None)


def tool_env(command):
    env = dict(os.environ)
    # The server may start without a login shell's PATH; Whisper and yt-dlp call ffmpeg, yt-dlp calls deno.
    env['PATH'] = os.pathsep.join([str(Path(command).parent), '/opt/homebrew/bin', '/usr/local/bin', env.get('PATH', '')])
    return env


def download_youtube(identifier, directory):
    """Fetch one video's audio with the Chrome cookies on this Mac and return the file."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier or ''):
        raise ValueError('Not a YouTube video ID.')
    command = downloader_command()
    if not command:
        raise ValueError('Downloading needs uv (uvx) on this machine.')
    result = subprocess.run(
        [command, '--from', 'yt-dlp[default]', 'yt-dlp', '--extract-audio', '--audio-format', 'm4a',
         '--cookies-from-browser', 'chrome', '--no-playlist', '--no-progress', '--quiet',
         '--paths', str(directory), '--output', '%(id)s.%(ext)s',
         '--', 'https://www.youtube.com/watch?v=' + identifier],
        capture_output=True, text=True, env=tool_env(command), timeout=3600, stdin=subprocess.DEVNULL,
    )
    files = [f for f in Path(directory).iterdir() if f.suffix.lower() in AUDIO_UPLOADS]
    if result.returncode != 0 or not files:
        errors = [line for line in result.stderr.splitlines() if 'ERROR' in line]
        detail = ' ' + errors[-1].split('ERROR:', 1)[-1].strip()[:200] if errors else ''
        raise ValueError('yt-dlp could not download the audio.' + detail +
                         ' If macOS asked on the Mac for access to Chrome Safe Storage, allow it and try again.')
    return files[0]


def transcribe(path):
    """Return Whisper segments as [{'start': seconds, 'text': str}]."""
    command = whisper_command()
    if not command:
        raise ValueError('Transcription needs mlx_whisper on this machine.')
    env = tool_env(command)
    with tempfile.TemporaryDirectory() as output:
        result = subprocess.run(
            [command, '--model', MODEL, '--output-format', 'json', '--output-dir', output, '--output-name', 'transcript', '--verbose', 'False', str(path)],
            capture_output=True, text=True, env=env, timeout=4 * 3600,
        )
        target = Path(output) / 'transcript.json'
        if result.returncode != 0 or not target.exists():
            raise ValueError('Whisper could not transcribe this file.')
        data = json.loads(target.read_text())
    return [{'start': float(s.get('start', 0)), 'text': ' '.join(str(s.get('text', '')).split())} for s in data.get('segments', []) if str(s.get('text', '')).strip()]


def timestamp(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f'{hours}:{rest // 60:02d}:{rest % 60:02d}' if hours else f'{rest // 60}:{rest % 60:02d}'


def escape_markdown(text):
    return re.sub(r'([\\`*_\[\]<>#|~$])', r'\\\1', text)


def transcript_markdown(segments, paragraph_seconds=45):
    """Group segments into paragraphs that each start with their timestamp."""
    paragraphs, current, started = [], [], None
    for segment in segments:
        if started is None:
            started = segment['start']
        current.append(segment['text'])
        ends_sentence = segment['text'].endswith(('.', '?', '!'))
        if segment['start'] - started >= paragraph_seconds and ends_sentence:
            paragraphs.append((started, ' '.join(current)))
            current, started = [], None
    if current:
        paragraphs.append((started, ' '.join(current)))
    body = '\n\n'.join(f'**{timestamp(start)}** {escape_markdown(text)}' for start, text in paragraphs)
    return f'{HEADING}\n\n{NOTE}\n\n{body}' if body else ''


def without_transcript(markdown):
    before, _, _ = (markdown or '').partition('\n\n' + HEADING + '\n')
    return before.rstrip()


def save_transcript(database, url, segments):
    section = transcript_markdown(segments)
    if not section:
        raise ValueError('Whisper found no speech in this file.')
    db = connect_database(database)
    try:
        row = db.execute('SELECT content_markdown FROM articles WHERE url=?', (url,)).fetchone()
        if not row:
            raise ValueError('Article not found.')
        markdown = without_transcript(row[0]) + '\n\n' + section
        text = BeautifulSoup(render_markdown(markdown), 'html.parser').get_text(' ', strip=True)
        db.execute("UPDATE articles SET content_markdown=?,content_text=?,content_status='full_text' WHERE url=?", (markdown, text, url))
        db.commit()
    finally:
        db.close()


def has_transcript(markdown):
    return ('\n\n' + HEADING + '\n') in (markdown or '')


def local_media_path(database, url):
    """The stored file behind a local media article, or None for anything else."""
    parsed = urlsplit(url)
    if parsed.scheme != 'file':
        return None
    path = Path(unquote(parsed.path)).resolve()
    media = (Path(database).resolve().parent / 'media').resolve()
    return path if path.parent == media and path.is_file() else None


def status(database, url):
    with _jobs_lock:
        return dict(_jobs.get((str(database), url), {}))


def _set(database, url, **state):
    with _jobs_lock:
        _jobs[(str(database), url)] = state


def start(database, url, path=None, *, remove_after=False, transcriber=None, youtube=None, downloader=None):
    """Transcribe in the background; status() reports progress. With youtube, download that video's audio first."""
    key = (str(database), url)
    with _jobs_lock:
        if _jobs.get(key, {}).get('state') in BUSY:
            if remove_after:
                Path(path).unlink(missing_ok=True)
            return dict(_jobs[key])
        _jobs[key] = {'state': 'queued'}

    def run():
        try:
            with _worker:
                if youtube:
                    _set(database, url, state='downloading')
                    with tempfile.TemporaryDirectory(dir=_download_root(database)) as folder:
                        audio = (downloader or download_youtube)(youtube, folder)
                        _set(database, url, state='running')
                        segments = (transcriber or transcribe)(audio)
                else:
                    _set(database, url, state='running')
                    segments = (transcriber or transcribe)(path)
                save_transcript(database, url, segments)
            _set(database, url, state='done')
        except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            _set(database, url, state='failed', error=str(exc) if isinstance(exc, ValueError) else 'Transcription failed.')
        finally:
            if remove_after and path:
                Path(path).unlink(missing_ok=True)

    thread = threading.Thread(target=run, name='transcribe', daemon=True)
    thread.start()
    return {'state': 'queued'}


def _download_root(database):
    directory = Path(database).resolve().parent / 'transcribe-uploads'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_upload(database, payload, filename):
    """Keep uploaded audio in a private temporary file until Whisper has read it."""
    suffix = Path(filename).suffix.lower()
    if suffix not in AUDIO_UPLOADS:
        raise ValueError('Use an audio file: M4A, MP3, Opus, OGG, WAV, WebM or MP4.')
    directory = _download_root(database)
    handle, name = tempfile.mkstemp(suffix=suffix, dir=directory)
    with os.fdopen(handle, 'wb') as file:
        file.write(payload)
    return Path(name)
