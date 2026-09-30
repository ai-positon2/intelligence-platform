"""The public-page gate judges the address a connection really reaches.

`ipaddress.is_global` judges an IPv6 wrapper, so NAT64 (64:ff9b::/96), 6to4
(2002::/16) and IPv4-compatible (::a.b.c.d) forms of 127.0.0.1 or 10.0.0.1
all read as global. Each of those can carry a private IPv4 destination past
a check that only asks the wrapper.
"""
import socket

import pytest

from tracker import event_intel_http as H


def _resolve_to(monkeypatch, address):
    fam = socket.AF_INET6 if ':' in address else socket.AF_INET
    monkeypatch.setattr(H.socket, 'getaddrinfo',
                        lambda host, port, type=0: [(fam, socket.SOCK_STREAM, 6, '', (address, port))])


@pytest.mark.parametrize('address', [
    '64:ff9b::7f00:1',        # NAT64 of 127.0.0.1
    '64:ff9b::a00:1',         # NAT64 of 10.0.0.1
    '64:ff9b::a9fe:a9fe',     # NAT64 of 169.254.169.254 (cloud metadata)
    '64:ff9b:1::a00:1',       # local-use NAT64
    '2002:7f00:1::',          # 6to4 of 127.0.0.1
    '2002:808:808::',         # 6to4, even of a public address
    '::127.0.0.1',            # IPv4-compatible
    '::ffff:127.0.0.1',       # IPv4-mapped
    '::ffff:10.1.2.3',
    '2001:0:4136:e378:8000:63bf:3fff:fdd2',  # Teredo
    '127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fe80::1', '224.0.0.1',
])
def test_a_private_destination_in_any_wrapper_is_refused(monkeypatch, address):
    _resolve_to(monkeypatch, address)
    with pytest.raises(ValueError, match='Private or reserved'):
        H.public_addresses('organiser.example', 443)


@pytest.mark.parametrize('address', [
    '8.8.8.8', '2606:4700:4700::1111',
    '64:ff9b::808:808',       # a DNS64 resolver's answer for an IPv4-only public site
    '::ffff:8.8.8.8',
])
def test_a_public_destination_still_passes(monkeypatch, address):
    _resolve_to(monkeypatch, address)
    assert H.public_addresses('organiser.example', 443) == [address]


def test_one_private_answer_among_public_ones_refuses_the_host(monkeypatch):
    monkeypatch.setattr(H.socket, 'getaddrinfo', lambda host, port, type=0: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', port)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('64:ff9b::7f00:1', port, 0, 0))])
    with pytest.raises(ValueError):
        H.public_addresses('organiser.example', 443)
