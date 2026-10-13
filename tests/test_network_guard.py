import socket
from collections.abc import Iterator

import pytest

from replayflow.core import network_guard


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    yield
    network_guard.disable()


def test_blocks_external_connect() -> None:
    network_guard.enable()
    with socket.socket() as s, pytest.raises(network_guard.NetworkBlockedError):
        s.connect(("8.8.8.8", 443))


def test_blocks_external_dns() -> None:
    network_guard.enable()
    with pytest.raises(network_guard.NetworkBlockedError):
        socket.getaddrinfo("example.com", 443)


def test_allows_loopback() -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    network_guard.enable()
    with server, socket.socket() as client:
        client.connect(server.getsockname())


def test_apply_toggles() -> None:
    network_guard.apply(True)
    assert network_guard.is_enabled()
    network_guard.apply(False)
    assert not network_guard.is_enabled()
