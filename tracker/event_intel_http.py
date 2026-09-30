"""Public-page HTTP reads with DNS pinning and redirect revalidation."""
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit

import requests
import urllib3


# Address ranges that carry an IPv4 address inside an IPv6 one. `is_global`
# judges the IPv6 wrapper, not the address a packet really reaches, so
# 64:ff9b::7f00:1 (NAT64 for 127.0.0.1) and ::127.0.0.1 both read as global.
_NAT64_WELL_KNOWN = ipaddress.ip_network('64:ff9b::/96')
# Local-use NAT64 (RFC 8215): the embedding is operator-defined, so the IPv4
# address it reaches cannot be read back out reliably. Refused outright.
_NAT64_LOCAL_USE = ipaddress.ip_network('64:ff9b:1::/48')
# Deprecated IPv4-compatible addresses (::a.b.c.d). :: and ::1 are inside this
# range too and are already non-global, so refusing the whole range is safe.
_IPV4_COMPATIBLE = ipaddress.ip_network('::/96')


def is_public_address(ip):
    """True only for a unicast address that is globally routable once any
    IPv4 address embedded in it is unwrapped.

    IPv4-mapped (::ffff:a.b.c.d) and well-known-prefix NAT64 (64:ff9b::/96)
    are judged by the IPv4 address inside them, because that is where a
    connection to them actually goes (a DNS64 resolver legitimately returns
    NAT64 addresses for IPv4-only sites). 6to4 (2002::/16), Teredo
    (2001::/32), local-use NAT64 and IPv4-compatible addresses are refused
    outright: they are tunnel mechanisms no organiser page is served from,
    and each can smuggle a private IPv4 destination past the check above.
    """
    if isinstance(ip, str):
        ip = ipaddress.ip_address(ip)
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return is_public_address(ip.ipv4_mapped)
        if ip in _NAT64_WELL_KNOWN:
            return is_public_address(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if (ip in _NAT64_LOCAL_USE or ip.sixtofour is not None
                or ip.teredo is not None or ip in _IPV4_COMPATIBLE):
            return False
    return ip.is_global and not ip.is_multicast


def public_addresses(host, port):
    if not host:
        raise ValueError('A public host is required.')
    addresses = list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    # is_global is True for multicast ranges (224.0.0.0/4, ff00::/8): they are
    # not privately-routed, but they are not a legitimate organizer-page
    # destination either, so they get their own explicit check rather than
    # silently passing this "public" gate and failing downstream with a
    # confusing socket error instead of this function's own clear message.
    # IPv6 scope ids (fe80::1%en0) are stripped before parsing; a scoped
    # address is link-local and refused either way.
    if not addresses or any(not is_public_address(a.split('%', 1)[0]) for a in addresses):
        raise ValueError('Private or reserved page destinations are not allowed.')
    return addresses


def public_get(url, *, timeout=20, stream=True, headers=None):
    """Connect to the validated IP, retaining the original TLS hostname.

    No environment proxy is used. Every redirect resolves and validates anew.
    The response owns its pool until the caller closes the streamed body.
    """
    for _ in range(6):
        target = urlsplit(url)
        if target.scheme not in ('http','https') or target.username or target.password:
            raise ValueError('Only public HTTP(S) pages without URL credentials are allowed.')
        # `or`, not `is None`, would treat an explicit `:0` in the URL (falsy,
        # but a real, distinct port a caller wrote) the same as no port at
        # all and silently substitute 80/443 for it instead of rejecting it
        # below like any other non-standard port.
        port = target.port if target.port is not None else (443 if target.scheme == 'https' else 80)
        if port not in (80,443):
            raise ValueError('Only standard web ports are allowed.')
        host = target.hostname
        address = public_addresses(host, port)[0]
        if target.scheme == 'https':
            pool = urllib3.HTTPSConnectionPool(address, port=port,
                server_hostname=host, assert_hostname=host, ssl_context=ssl.create_default_context())
        else:
            pool = urllib3.HTTPConnectionPool(address, port=port)
        req_headers = dict(headers or {}, Host=target.netloc)
        path = target.path or '/'
        if target.query:
            path += '?' + target.query
        try:
            raw = pool.urlopen('GET', path, headers=req_headers, redirect=False,
                retries=False, preload_content=False, timeout=timeout)
        except Exception:
            pool.close()
            raise
        if raw.status in (301,302,303,307,308) and raw.headers.get('Location'):
            destination = urljoin(url, raw.headers['Location'])
            raw.close()
            pool.close()
            url = destination
            continue
        response = requests.Response()
        response.status_code = raw.status
        response.headers = requests.structures.CaseInsensitiveDict(raw.headers)
        response.url = url
        response.raw = raw
        response.encoding = requests.utils.get_encoding_from_headers(response.headers) or 'utf-8'
        original_close = response.close
        def close(original_close=original_close, pool=pool):
            original_close()
            pool.close()
        response.close = close
        return response
    raise ValueError('Too many page redirects.')
