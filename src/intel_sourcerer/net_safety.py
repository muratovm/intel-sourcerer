"""Protections for running on the open internet: fetch limits, client identity, and request limits."""
import asyncio
from collections import deque
import ipaddress
import os
import socket
import threading
import time

import httpcore
import httpx
from http.server import ThreadingHTTPServer

LOOPBACK_HOSTS = {'127.0.0.1', '::1', 'localhost'}


def env_flag(name):
    return os.environ.get(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def guest_enabled():
    return os.environ.get('INTEL_GUEST', 'on').strip().lower() not in {'0', 'off', 'false', 'no'}


def is_public_address(value):
    """False for loopback, private, link-local and cloud metadata addresses, in any spelling."""
    address = ipaddress.ip_address(value.split('%', 1)[0])
    mapped = getattr(address, 'ipv4_mapped', None)
    return (mapped or address).is_global


def public_addresses(host, port):
    """Resolve once, and refuse the host if any answer is private."""
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise httpcore.ConnectError(f'Could not resolve {host}.') from exc
    addresses = list(dict.fromkeys(item[4][0] for item in found))
    if not addresses or not all(is_public_address(address) for address in addresses):
        raise httpcore.ConnectError('Blocked a connection to a non-public address.')
    return addresses


class _PublicOnlySync(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        failure = None
        for address in public_addresses(host, port):
            try:
                return super().connect_tcp(address, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as exc:
                failure = exc
        raise failure


class _PublicOnlyAsync(httpcore.AnyIOBackend):
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        failure = None
        for address in await asyncio.to_thread(public_addresses, host, port):
            try:
                return await super().connect_tcp(address, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as exc:
                failure = exc
        raise failure


def public_only(client):
    """Check addresses when each connection opens, so DNS rebinding and redirects can't reach inside.

    A check made before the request can't help: the name is resolved again to connect, and an
    attacker's DNS can answer differently the second time.
    """
    transport = client._transport
    if isinstance(transport, (httpx.HTTPTransport, httpx.AsyncHTTPTransport)):
        pool = getattr(transport, '_pool', None)
        # These are httpx internals, so refuse to run unguarded if an upgrade moves them.
        if pool is None or not hasattr(pool, '_network_backend'):
            raise RuntimeError('Cannot guard outgoing connections with this httpx version.')
        pool._network_backend = _PublicOnlyAsync() if isinstance(transport, httpx.AsyncHTTPTransport) else _PublicOnlySync()
    return client


def trusted_proxies():
    """Networks allowed to report the real client address; set INTEL_TRUSTED_PROXIES to your proxy."""
    value = os.environ.get('INTEL_TRUSTED_PROXIES', '')
    return [ipaddress.ip_network(item.strip(), strict=False) for item in value.split(',') if item.strip()]


def _is_trusted(address, networks):
    try:
        parsed = ipaddress.ip_address(address.split('%', 1)[0])
    except ValueError:
        return False
    parsed = getattr(parsed, 'ipv4_mapped', None) or parsed
    return any(parsed in network for network in networks)


def client_address(peer, headers, networks):
    """The real client: the last forwarded hop our own proxies didn't add, since clients can forge the rest."""
    if not _is_trusted(peer, networks):
        return peer
    hops = [hop.strip() for hop in (headers.get('X-Forwarded-For') or '').split(',') if hop.strip()]
    for hop in reversed(hops):
        if not _is_trusted(hop, networks):
            return hop
    return peer


def forwarded_https(peer, headers, networks):
    return _is_trusted(peer, networks) and (headers.get('X-Forwarded-Proto') or '').split(',')[0].strip().lower() == 'https'


class RateLimiter:
    """A rolling budget per client, kept in memory; a restart resets it."""

    def __init__(self, budget, window, max_clients=10_000):
        self.budget, self.window, self.max_clients = budget, window, max_clients
        self.spent = {}
        self.lock = threading.Lock()

    def allow(self, client, cost=1, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            if len(self.spent) >= self.max_clients and client not in self.spent:
                self.spent = {key: value for key, value in self.spent.items() if value and value[-1][0] > now - self.window}
                if len(self.spent) >= self.max_clients:
                    return False
            history = self.spent.setdefault(client, deque())
            while history and history[0][0] <= now - self.window:
                history.popleft()
            if sum(item[1] for item in history) + cost > self.budget:
                return False
            history.append((now, cost))
            return True


class BoundedHTTPServer(ThreadingHTTPServer):
    """Python's threaded server starts a thread per connection without limit; this adds one."""

    daemon_threads = True
    request_queue_size = 128

    def __init__(self, *args, **kwargs):
        limit = int(os.environ.get('INTEL_MAX_CONNECTIONS', '64'))
        self.connection_slots = threading.BoundedSemaphore(limit)
        self.per_client_limit = max(1, limit // 4)
        self.client_connections = {}
        self.client_lock = threading.Lock()
        self.networks = trusted_proxies()
        super().__init__(*args, **kwargs)

    def _shared_peer(self, peer):
        # Everyone behind a proxy shares its address, so limiting it would limit everybody.
        return peer in LOOPBACK_HOSTS or peer.startswith('127.') or _is_trusted(peer, self.networks)

    def _refuse(self, request):
        try:
            request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\nRetry-After: 5\r\n\r\n')
        except OSError:
            pass
        self.shutdown_request(request)

    def process_request(self, request, client_address):
        peer = client_address[0]
        if not self.connection_slots.acquire(blocking=False):
            self._refuse(request)
            return
        if not self._shared_peer(peer):
            with self.client_lock:
                count = self.client_connections.get(peer, 0)
                if count >= self.per_client_limit:
                    self.connection_slots.release()
                    self._refuse(request)
                    return
                self.client_connections[peer] = count + 1
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            peer = client_address[0]
            if not self._shared_peer(peer):
                with self.client_lock:
                    count = self.client_connections.get(peer, 1) - 1
                    if count > 0:
                        self.client_connections[peer] = count
                    else:
                        self.client_connections.pop(peer, None)
            self.connection_slots.release()
