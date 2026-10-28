"""Bloqueio de rede do modo privado.

Intercepta socket.connect/getaddrinfo no processo: qualquer destino fora do
loopback levanta NetworkBlockedError. Loopback continua liberado para o
obs-websocket local.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any


class NetworkBlockedError(PermissionError):
    pass


_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_getaddrinfo = socket.getaddrinfo
_LOCAL_NAMES = {"localhost", "localhost.localdomain", ""}


def _is_local(host: Any) -> bool:
    if host is None:
        return True
    host = host.decode() if isinstance(host, bytes) else str(host)
    if host.lower() in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def _check(address: Any) -> None:
    host = address[0] if isinstance(address, tuple) else None  # AF_UNIX usa str: local
    if not _is_local(host):
        raise NetworkBlockedError(f"Modo privado: conexão externa bloqueada ({host})")


def _guarded_connect(self: socket.socket, address: Any) -> None:
    _check(address)
    return _original_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    _check(address)
    return _original_connect_ex(self, address)


def _guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    if not _is_local(host):
        raise NetworkBlockedError(f"Modo privado: resolução DNS bloqueada ({host})")
    return _original_getaddrinfo(host, *args, **kwargs)


def enable() -> None:
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _guarded_getaddrinfo


def disable() -> None:
    socket.socket.connect = _original_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _original_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _original_getaddrinfo


def is_enabled() -> bool:
    return socket.socket.connect is _guarded_connect


def apply(private_mode: bool) -> None:
    enable() if private_mode else disable()
