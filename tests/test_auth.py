"""Authentication and isolation checks against the real HTTP handler."""
from contextlib import closing
from threading import Thread
import re
import httpx
import pytest
from intel_sourcerer.web import create_server
from intel_sourcerer.auth import Accounts
from intel_sourcerer.storage import connect_database


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.text)[1]


def test_accounts_isolate_library_sources_and_media(tmp_path):
    database = tmp_path / 'articles.sqlite3'
    config = tmp_path / 'sources.toml'
    config.write_text('sources = []\n')
    server = create_server(database, 0, config)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f'http://127.0.0.1:{server.server_port}'
    try:
        with httpx.Client(base_url=address) as owner, httpx.Client(base_url=address) as other:
            assert owner.get('/api/state').status_code == 401
            assert owner.get('/reader.css').status_code == 200
            setup = owner.get('/setup')
            token = csrf(setup)
            assert owner.post('/auth/setup', data={'username':'owner','password':'very-long-password','csrf_token':'bad'}).status_code == 403
            response = owner.post('/auth/setup', data={'username':'owner','password':'very-long-password','csrf_token':token})
            assert response.status_code == 303
            assert 'HttpOnly' in response.headers['set-cookie']
            assert 'SameSite=Strict' in response.headers['set-cookie']
            assert owner.get('/api/state').status_code == 200
            account = owner.get('/account')
            assert owner.post('/auth/create-user', data={'username':'reader','password':'another-long-password','csrf_token':csrf(account)}).status_code == 200
            assert other.post('/auth/setup', data={'username':'takeover','password':'another-long-password','csrf_token':token}).status_code == 403
            login = other.get('/login')
            assert other.post('/auth/login', data={'username':'reader','password':'another-long-password','csrf_token':csrf(login)}).status_code == 303
            accounts = Accounts(database, config)
            first = accounts.session(owner.cookies.get('intel_session') and 'intel_session='+owner.cookies.get('intel_session'))
            second = accounts.session('intel_session='+other.cookies.get('intel_session'))
            assert first['database_path'] == str(database)
            assert first['database_path'] != second['database_path']
            assert first['config_path'] != second['config_path']
            with closing(connect_database(database)) as db, db:
                db.execute("INSERT INTO sources(id,name,source_type,parent_url,enabled) VALUES('test','Private source','ai_lab','https://example.com',1)")
                db.execute("INSERT INTO articles(url,source_id,title,published_at,content_text,summary_html,content_status,relevance_reason,discovered_at) VALUES('https://example.com/private','test','Private article','2026-10-05','Private contents','','full_text','','2026-10-05')")
            assert len(owner.get('/api/state').json()['articles']) == 1
            assert other.get('/api/state').json()['articles'] == []
            assert other.get('/api/article?url=https://example.com/private').status_code == 404
            assets = tmp_path/'media'; assets.mkdir()
            filename = 'a'*64+'.mp3'
            (assets/filename).write_bytes(b'private audio')
            assert owner.get('/local-media/'+filename).status_code == 200
            assert other.get('/local-media/'+filename).status_code == 404
            token = csrf(other.get('/account'))
            assert other.post('/auth/create-user',data={'username':'unauthorized','password':'another-long-password','csrf_token':token}).status_code == 403
            assert other.post('/api/link',json={'url':str(assets/filename)},headers={'X-CSRF-Token':token}).status_code == 400
            assert other.post('/auth/password',data={'csrf_token':token,'current_password':'wrong','password':'new-very-long-password'}).status_code == 400
            old_cookie = other.cookies.get('intel_session')
            assert other.post('/auth/password',data={'csrf_token':token,'current_password':'another-long-password','password':'new-very-long-password'}).status_code == 303
            assert accounts.session('intel_session='+old_cookie) is None
            assert other.get('/api/state').status_code == 200
            token = csrf(other.get('/account'))
            cookie = other.cookies.get('intel_session')
            assert other.post('/auth/logout',data={'csrf_token':token}).status_code == 303
            assert accounts.session('intel_session='+cookie) is None
            assert other.get('/api/state').status_code == 401
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_login_throttling_and_password_hashes(tmp_path):
    database = tmp_path/'articles.sqlite3'
    connect_database(database).close()
    accounts = Accounts(database,tmp_path/'sources.toml')
    accounts.create_user('owner','very-long-password',first=True)
    import pytest
    for _ in range(5):
        with pytest.raises(ValueError,match='Invalid username'):
            accounts.login('owner','wrong','test-address')
    with pytest.raises(ValueError,match='Too many'):
        accounts.login('owner','very-long-password','test-address')
    assert accounts.login('owner','very-long-password','different-address')
    with accounts.connect() as db:
        row = db.execute('SELECT * FROM users').fetchone()
        assert row['password_hash'] != 'very-long-password'


def make_accounts(tmp_path):
    config = tmp_path / 'sources.toml'
    config.write_text('sources = []\n')
    connect_database(tmp_path / 'articles.sqlite3').close()
    return Accounts(tmp_path / 'articles.sqlite3', config)


def test_deleting_needs_the_right_username_and_password(tmp_path):
    accounts = make_accounts(tmp_path)
    owner = accounts.create_user('owner', 'very-long-password', first=True)
    for username, password in [('owner', 'wrong-long-password'), ('other', 'very-long-password'), ('', ''), ('owner', 'x' * 2000)]:
        with pytest.raises(ValueError):
            accounts.delete_account(owner, username, password)
    assert not accounts.empty()
    assert (tmp_path / 'articles.sqlite3').exists()


def test_deleting_a_later_account_leaves_everyone_else_alone(tmp_path):
    accounts = make_accounts(tmp_path)
    owner = accounts.create_user('owner', 'very-long-password', first=True)
    reader = accounts.create_user('reader', 'another-long-password')
    token, _ = accounts.new_session(reader)
    accounts.delete_account(reader, ' READER ', 'another-long-password')
    assert not (tmp_path / 'users' / reader).exists()
    assert (tmp_path / 'users' / owner).exists() and (tmp_path / 'articles.sqlite3').exists()
    assert accounts.session('intel_session=' + token) is None


def test_deleting_the_first_account_removes_its_library_and_copies(tmp_path):
    accounts = make_accounts(tmp_path)
    owner = accounts.create_user('owner', 'very-long-password', first=True)
    for name in ('backups', 'media', 'pdf-assets', 'uploads'):
        (tmp_path / name).mkdir()
        (tmp_path / name / 'file').write_text('private')
    accounts.delete_account(owner, 'owner', 'very-long-password')
    assert accounts.empty()
    assert sorted(path.name for path in tmp_path.iterdir() if not path.name.startswith('accounts')) == ['sources.toml', 'users']
    assert not (tmp_path / 'users' / owner).exists()


def test_delete_account_over_http(tmp_path):
    config = tmp_path / 'sources.toml'
    config.write_text('sources = []\n')
    server = create_server(tmp_path / 'articles.sqlite3', 0, config)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}') as client:
            client.post('/auth/setup', data={'username': 'owner', 'password': 'very-long-password', 'csrf_token': csrf(client.get('/setup'))})
            account = client.get('/account')
            assert 'Add an account' not in account.text and 'Delete account' in account.text
            bad = client.post('/auth/delete-account', data={'username': 'owner', 'password': 'wrong-long-password', 'csrf_token': csrf(account)})
            assert bad.status_code == 400 and 'incorrect' in bad.text
            assert client.post('/auth/delete-account', data={'username': 'owner', 'password': 'very-long-password', 'csrf_token': 'bad'}).status_code == 403
            assert (tmp_path / 'articles.sqlite3').exists()
            done = client.post('/auth/delete-account', data={'username': 'owner', 'password': 'very-long-password', 'csrf_token': csrf(account)})
            assert done.status_code == 303 and done.headers['location'] == '/setup'
            assert 'Max-Age=0' in done.headers['set-cookie']
            assert not (tmp_path / 'articles.sqlite3').exists()
            assert client.get('/api/state').status_code == 401
    finally:
        server.shutdown()
        server.server_close()
