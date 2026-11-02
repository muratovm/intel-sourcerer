import asyncio
import http.client
import ipaddress
import socket
import threading
import time

import httpx
import pytest

from intel_sourcerer import guest_routes
from intel_sourcerer.discovery import public_url
from intel_sourcerer.net_safety import (
    BoundedHTTPServer,
    RateLimiter,
    client_address,
    forwarded_https,
    is_public_address,
    public_only,
    trusted_proxies,
)
from intel_sourcerer.web import create_server

PROXY = [ipaddress.ip_network('10.0.0.5/32')]


@pytest.mark.parametrize('address', [
    '127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.1.1', '169.254.169.254', '100.64.0.1', '0.0.0.0',
    '::1', 'fc00::1', 'fe80::1%en0', '::ffff:127.0.0.1', '::ffff:169.254.169.254',
])
def test_internal_addresses_are_not_public(address):
    assert not is_public_address(address)


@pytest.mark.parametrize('address', ['8.8.8.8', '1.1.1.1', '2606:4700:4700::1111'])
def test_real_addresses_are_public(address):
    assert is_public_address(address)


class EchoServer:
    """A local service an attacker would love to reach."""
    def __enter__(self):
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self.hits = 0
        threading.Thread(target=self.serve, daemon=True).start()
        return self

    def serve(self):
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            self.hits += 1
            connection.recv(1024)
            connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 6\r\nConnection: close\r\n\r\nsecret')
            connection.close()

    def __exit__(self, *_):
        self.listener.close()


def test_sync_and_async_clients_refuse_internal_addresses():
    with EchoServer() as service:
        url = f'http://127.0.0.1:{service.port}/'
        assert httpx.Client(trust_env=False).get(url).text == 'secret'
        hits = service.hits
        with public_only(httpx.Client(trust_env=False)) as client, pytest.raises(httpx.ConnectError):
            client.get(url)

        async def fetch():
            async with public_only(httpx.AsyncClient(trust_env=False)) as client:
                await client.get(url)
        with pytest.raises(httpx.ConnectError):
            asyncio.run(fetch())
        assert service.hits == hits


def test_dns_rebinding_cannot_reach_inside(monkeypatch):
    """The name looks public to the check, then answers with loopback when connecting."""
    with EchoServer() as service:
        real = socket.getaddrinfo
        lookups = []

        def rebinding(host, port, *args, **kwargs):
            if host != 'evil.example':
                return real(host, port, *args, **kwargs)
            lookups.append(host)
            address = '8.8.8.8' if len(lookups) == 1 else '127.0.0.1'
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, port))]

        monkeypatch.setattr(socket, 'getaddrinfo', rebinding)
        assert public_url('http://evil.example/') == 'http://evil.example/'
        with public_only(httpx.Client(trust_env=False)) as client, pytest.raises(httpx.ConnectError):
            client.get(f'http://evil.example:{service.port}/')
        assert len(lookups) == 2 and service.hits == 0


def test_a_redirect_to_an_internal_address_is_refused(monkeypatch):
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('169.254.169.254', port))] if host == 'metadata.example' else real(host, port, *a, **k))

    async def fetch():
        async with public_only(httpx.AsyncClient(trust_env=False, follow_redirects=True)) as client:
            await client.get('http://metadata.example/latest/meta-data/')
    with pytest.raises(httpx.ConnectError):
        asyncio.run(fetch())


def test_any_private_answer_blocks_the_whole_name(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', port)), (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', port))])
    with public_only(httpx.Client(trust_env=False)) as client, pytest.raises(httpx.ConnectError):
        client.get('http://mixed.example/')


def test_mock_transports_stay_usable_for_tests():
    client = public_only(httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text='ok'))))
    assert client.get('http://127.0.0.1/').text == 'ok'


def test_guarding_fails_closed_if_httpx_internals_move():
    client = httpx.Client(trust_env=False)
    client._transport._pool = object()
    with pytest.raises(RuntimeError):
        public_only(client)


@pytest.mark.parametrize('peer,forwarded,expected', [
    ('203.0.113.9', None, '203.0.113.9'),
    ('203.0.113.9', '1.2.3.4', '203.0.113.9'),
    ('10.0.0.5', '198.51.100.7', '198.51.100.7'),
    ('10.0.0.5', '1.2.3.4, 198.51.100.7', '198.51.100.7'),
    ('10.0.0.5', '1.2.3.4, 198.51.100.7, 10.0.0.5', '198.51.100.7'),
    ('10.0.0.5', None, '10.0.0.5'),
    ('10.0.0.5', '10.0.0.5', '10.0.0.5'),
])
def test_only_our_proxy_can_name_the_client(peer, forwarded, expected):
    assert client_address(peer, {'X-Forwarded-For': forwarded} if forwarded else {}, PROXY) == expected


def test_no_proxies_configured_means_nothing_is_trusted(monkeypatch):
    monkeypatch.delenv('INTEL_TRUSTED_PROXIES', raising=False)
    assert trusted_proxies() == []
    assert client_address('10.0.0.5', {'X-Forwarded-For': '1.2.3.4'}, []) == '10.0.0.5'


def test_bad_proxy_setting_fails_at_startup(monkeypatch):
    monkeypatch.setenv('INTEL_TRUSTED_PROXIES', 'not-an-address')
    with pytest.raises(ValueError):
        trusted_proxies()


def test_only_our_proxy_can_claim_https():
    assert forwarded_https('10.0.0.5', {'X-Forwarded-Proto': 'https'}, PROXY)
    assert not forwarded_https('203.0.113.9', {'X-Forwarded-Proto': 'https'}, PROXY)
    assert not forwarded_https('10.0.0.5', {'X-Forwarded-Proto': 'http'}, PROXY)


def test_rate_limiter_budget_rolls_and_counts_cost():
    limiter = RateLimiter(3, 60)
    assert limiter.allow('a', now=0) and limiter.allow('a', cost=2, now=1)
    assert not limiter.allow('a', now=2)
    assert limiter.allow('b', now=2)
    assert limiter.allow('a', now=61) and limiter.allow('a', cost=2, now=62)


def test_rate_limiter_memory_is_bounded():
    limiter = RateLimiter(1, 60, max_clients=2)
    assert limiter.allow('a', now=0) and limiter.allow('b', now=0)
    assert not limiter.allow('c', now=1)
    assert limiter.allow('c', now=120)
    assert len(limiter.spent) <= 2


@pytest.fixture
def run_server(tmp_path, monkeypatch):
    for name in ('INTEL_SETUP_TOKEN', 'INTEL_TRUSTED_PROXIES', 'INTEL_SECURE_COOKIES', 'INTEL_GUEST', 'INTEL_MAX_CONNECTIONS'):
        monkeypatch.delenv(name, raising=False)
    running = []

    def start(**kwargs):
        server = create_server(tmp_path / 'lib.sqlite3', 0, tmp_path / 'none.toml', **kwargs)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        running.append(server)
        return httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}', trust_env=False), server
    yield start
    for server in running:
        server.shutdown()
        server.server_close()


def setup_token(client):
    page = client.get('/setup').text
    return page.split('name="csrf_token" value="')[1].split('"')[0], page


ACCOUNT = {'username': 'owner', 'password': 'a-long-test-password'}


def test_setup_needs_the_code_on_a_reachable_address(run_server, capsys):
    client, _ = run_server(host='0.0.0.0')
    code = capsys.readouterr().out.split('administrator: ')[1].split()[0]
    csrf, page = setup_token(client)
    assert 'name="setup_code"' in page
    assert client.get('/api/viewer-session').json()['setup_code_required']
    for wrong in ({}, {'setup_code': ''}, {'setup_code': 'guess'}):
        assert client.post('/auth/setup', data={**ACCOUNT, 'csrf_token': csrf, **wrong}).status_code == 400
    assert client.get('/api/viewer-session').json()['setup_required']
    assert client.post('/auth/setup', data={**ACCOUNT, 'csrf_token': csrf, 'setup_code': code}).status_code == 303
    assert not client.get('/api/viewer-session').json()['setup_required']


def test_a_chosen_setup_token_is_not_printed(run_server, monkeypatch, capsys):
    monkeypatch.setenv('INTEL_SETUP_TOKEN', 'my-chosen-token')
    client, _ = run_server(host='0.0.0.0')
    assert 'my-chosen-token' not in capsys.readouterr().out
    csrf, _ = setup_token(client)
    assert client.post('/auth/setup', data={**ACCOUNT, 'csrf_token': csrf, 'setup_code': 'wrong'}).status_code == 400
    assert client.post('/auth/setup', data={**ACCOUNT, 'csrf_token': csrf, 'setup_code': 'my-chosen-token'}).status_code == 303


def test_localhost_setup_needs_no_code(run_server):
    client, _ = run_server()
    csrf, page = setup_token(client)
    assert 'name="setup_code"' not in page
    assert client.post('/auth/setup', data={**ACCOUNT, 'csrf_token': csrf}).status_code == 303


def test_no_code_is_made_once_an_account_exists(run_server, tmp_path, capsys):
    client, server = run_server()
    csrf, _ = setup_token(client)
    client.post('/auth/setup', data={**ACCOUNT, 'csrf_token': csrf})
    capsys.readouterr()
    run_server(host='0.0.0.0')
    assert 'setup code' not in capsys.readouterr().out


def login(client, csrf, password='wrong-password-here', **headers):
    return client.post('/auth/login', data={'username': 'owner', 'password': password, 'csrf_token': csrf}, headers=headers)


def test_one_attacker_behind_the_proxy_cannot_lock_out_the_owner(run_server, monkeypatch):
    monkeypatch.setenv('INTEL_TRUSTED_PROXIES', '127.0.0.1/32')
    client, _ = run_server()
    csrf, _ = setup_token(client)
    client.post('/auth/setup', data={**ACCOUNT, 'csrf_token': csrf})
    client.cookies.clear()
    csrf = client.get('/login').text.split('name="csrf_token" value="')[1].split('"')[0]
    for _ in range(5):
        assert login(client, csrf, **{'X-Forwarded-For': '198.51.100.7'}).status_code == 400
    assert 'Too many attempts' in login(client, csrf, **{'X-Forwarded-For': '198.51.100.7'}).text
    # The attacker can't dodge the limit by forging an earlier hop either.
    assert 'Too many attempts' in login(client, csrf, **{'X-Forwarded-For': '9.9.9.9, 198.51.100.7'}).text
    assert login(client, csrf, ACCOUNT['password'], **{'X-Forwarded-For': '203.0.113.20'}).status_code == 303


def test_forwarded_headers_are_ignored_from_untrusted_peers(run_server):
    client, _ = run_server()
    csrf, _ = setup_token(client)
    client.post('/auth/setup', data={**ACCOUNT, 'csrf_token': csrf})
    client.cookies.clear()
    csrf = client.get('/login').text.split('name="csrf_token" value="')[1].split('"')[0]
    for index in range(5):
        login(client, csrf, **{'X-Forwarded-For': f'198.51.100.{index}'})
    assert 'Too many attempts' in login(client, csrf, **{'X-Forwarded-For': '198.51.100.200'}).text


def test_secure_cookies_and_hsts(run_server, monkeypatch):
    plain, _ = run_server()
    response = plain.get('/login')
    assert 'Secure' not in response.headers['set-cookie'] and 'strict-transport-security' not in response.headers
    assert 'Secure' not in plain.get('/login', headers={'X-Forwarded-Proto': 'https'}, cookies={}).headers.get('set-cookie', '')
    monkeypatch.setenv('INTEL_SECURE_COOKIES', '1')
    secure, _ = run_server()
    response = secure.get('/login')
    assert 'Secure' in response.headers['set-cookie'] and 'max-age' in response.headers['strict-transport-security']


def test_https_from_the_trusted_proxy_makes_cookies_secure(run_server, monkeypatch):
    monkeypatch.setenv('INTEL_TRUSTED_PROXIES', '127.0.0.1/32')
    client, _ = run_server()
    assert 'Secure' in client.get('/login', headers={'X-Forwarded-Proto': 'https'}).headers['set-cookie']


def guest_post(client, body=b'{"articles":[{"title":"T","url":"https://example.com/a","text":"Words"}]}'):
    return client.post('/guest/prepare', content=body, headers={'Content-Type': 'application/json', 'X-Guest-Request': '1'})


@pytest.fixture
def fresh_guests(monkeypatch):
    monkeypatch.setattr(guest_routes, 'GUEST_BUDGET', RateLimiter(120, 600))


def test_guest_requests_are_rate_limited_per_client(run_server, monkeypatch, fresh_guests):
    monkeypatch.setattr(guest_routes, 'GUEST_BUDGET', RateLimiter(3, 600))
    monkeypatch.setenv('INTEL_TRUSTED_PROXIES', '127.0.0.1/32')
    client, _ = run_server()
    first = {'X-Forwarded-For': '198.51.100.1'}
    body = b'{"articles":[{"title":"T","url":"https://example.com/a","text":"Words"}]}'
    headers = {'Content-Type': 'application/json', 'X-Guest-Request': '1'}
    for _ in range(3):
        assert client.post('/guest/prepare', content=body, headers={**headers, **first}).status_code == 200
    assert client.post('/guest/prepare', content=body, headers={**headers, **first}).status_code == 429
    assert client.post('/guest/prepare', content=body, headers={**headers, 'X-Forwarded-For': '198.51.100.2'}).status_code == 200


def test_each_fetched_url_spends_the_guest_budget(run_server, monkeypatch, fresh_guests):
    monkeypatch.setattr(guest_routes, 'GUEST_BUDGET', RateLimiter(3, 600))
    import intel_sourcerer.temporary as temporary
    fetched = []
    monkeypatch.setattr(temporary, 'load_public_article', lambda url, **_: fetched.append(url) or {'url': url, 'title': 'T', 'html': '<p>x</p>'})
    client, _ = run_server()
    body = ('{"articles":[' + ','.join(f'{{"url":"https://example.com/{i}"}}' for i in range(10)) + ']}').encode()
    response = guest_post(client, body)
    assert response.status_code == 400 and 'Too many articles' in response.json()['error']
    assert len(fetched) == 2


def test_guest_mode_can_be_turned_off(run_server, monkeypatch, fresh_guests):
    client, _ = run_server()
    assert client.get('/guest').status_code == 200
    assert 'Continue as guest' in client.get('/login').text
    monkeypatch.setenv('INTEL_GUEST', 'off')
    assert client.get('/guest', follow_redirects=False).status_code != 200
    assert guest_post(client).status_code == 404
    assert 'Continue as guest' not in client.get('/login').text


def slow_handler_server(limit, monkeypatch, release):
    from http.server import BaseHTTPRequestHandler
    monkeypatch.setenv('INTEL_MAX_CONNECTIONS', str(limit))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            release.wait(5)
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *_):
            pass
    server = BoundedHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_server_refuses_connections_past_the_limit_then_recovers(monkeypatch):
    release = threading.Event()
    server = slow_handler_server(2, monkeypatch, release)
    port = server.server_port
    try:
        held = [socket.create_connection(('127.0.0.1', port)) for _ in range(2)]
        for connection in held:
            connection.sendall(b'GET / HTTP/1.0\r\n\r\n')
        time.sleep(0.2)
        extra = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        extra.request('GET', '/')
        assert extra.getresponse().status == 503
        release.set()
        for connection in held:
            connection.recv(1024)
            connection.close()
        time.sleep(0.2)
        again = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        again.request('GET', '/')
        assert again.getresponse().status == 200
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def test_one_remote_client_cannot_take_every_slot(monkeypatch):
    release = threading.Event()
    server = slow_handler_server(8, monkeypatch, release)
    try:
        assert server.per_client_limit == 2
        assert not server._shared_peer('203.0.113.9') and server._shared_peer('127.0.0.1')
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def test_silent_clients_are_dropped(run_server, monkeypatch):
    import intel_sourcerer.web as web
    client, server = run_server()
    handler = server.RequestHandlerClass
    assert handler.timeout == 30
    handler.timeout = 0.3
    connection = socket.create_connection(server.server_address)
    connection.settimeout(3)
    connection.sendall(b'GET /health HTTP/1.1\r\nHost: x\r\n')
    assert connection.recv(1024) == b''
    connection.close()


def test_concurrent_uploads_are_refused_not_buffered(run_server, monkeypatch):
    client, server = run_server(auth_enabled=False)
    page = client.get('/').text
    token = page.split('name="csrf-token" content="')[1].split('"')[0]
    import intel_sourcerer.media_import as media_import
    entered, release = threading.Event(), threading.Event()

    async def slow_import(*_):
        return 'file:///x'
    monkeypatch.setattr(media_import, 'import_file', slow_import)
    headers = {'X-CSRF-Token': token, 'Content-Type': 'application/octet-stream', 'X-Filename': 'a.pdf', 'Content-Length': '1000'}
    host, port = server.server_address[:2]

    def slow_sender():
        connection = socket.create_connection((host, port))
        head = 'POST /api/file-upload HTTP/1.1\r\nHost: x\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in headers.items()) + '\r\n'
        connection.sendall(head.encode() + b'x')
        entered.set()
        release.wait(5)
        connection.close()
    senders = [threading.Thread(target=slow_sender) for _ in range(2)]
    for sender in senders:
        sender.start()
    entered.wait(2)
    time.sleep(0.4)
    response = client.post('/api/file-upload', content=b'x' * 10, headers={'X-CSRF-Token': token, 'Content-Type': 'application/octet-stream', 'X-Filename': 'b.pdf'})
    release.set()
    for sender in senders:
        sender.join()
    assert response.status_code == 429 and 'upload' in response.json()['error']
