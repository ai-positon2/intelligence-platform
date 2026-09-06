"""Public-page HTTP reads with DNS pinning and redirect revalidation."""
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit

import requests
import urllib3


def public_addresses(host, port):
    if not host:
        raise ValueError('A public host is required.')
    addresses = list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
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
        port = target.port or (443 if target.scheme == 'https' else 80)
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
