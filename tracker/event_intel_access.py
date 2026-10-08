"""Whether a URL is the event organizer's own page.

Used by admission and discovery to keep a shared platform host (Eventbrite,
Luma) from making one organizer's listing count as another's.
"""
from urllib.parse import urlsplit


# Hosts where many organizers each have a page. Being on the same host
# proves nothing there: an audit on 2026-09-30 found that an event whose
# website is an Eventbrite or Luma listing counted ANY other organizer's
# listing on that platform as its own organizer page. Only a shared
# platform's generic subdomains are shared; "acme.bizzabo.com" is Acme's.
_SHARED_LABELS = {'www', 'web', 'app', 'events', 'event', 'portal', 'next', 'my',
                  'go', 'm', 'register', 'tickets'}
# One platform, two hosts: lu.ma now redirects to luma.com.
_HOST_ALIASES = {'lu.ma': 'luma.com'}


def _platform_hosts():
    from .event_intel_harvest import _NON_COMPANY_HOSTS
    return set(_NON_COMPANY_HOSTS) | {'luma.com'}


def _shared_host(host):
    platforms = _platform_hosts()
    if host in platforms:
        return True
    label, _, rest = host.partition('.')
    return label in _SHARED_LABELS and rest in platforms


def organizer_url(url, host, website=None):
    """Is `url` on the organizer's own site?

    `host` is the website's hostname; a full website URL is also accepted
    in its place. On a shared platform host the URL must also sit at or
    under the website's own listing path ("/e/cmo-summit-123",
    "/o/acme-456"), and without that path nothing there is the organizer's."""
    try:
        if host and '://' in str(host):
            website, host = host, urlsplit(host).hostname
        parsed = urlsplit(url)
        target = (parsed.hostname or '').lower().removeprefix('www.')
        host = (host or '').lower().removeprefix('www.')
        target, host = _HOST_ALIASES.get(target, target), _HOST_ALIASES.get(host, host)
        if not (host and parsed.scheme in ('http', 'https') and not parsed.username
                and not parsed.password and parsed.port in (None, 80, 443)):
            return False
        if _shared_host(host):
            listing = urlsplit(website or '').path.rstrip('/') if website else ''
            path = parsed.path.rstrip('/')
            return bool(target == host and listing
                        and (path == listing or path.startswith(listing + '/')))
        return target == host or target.endswith('.'+host)
    except (ValueError, TypeError):
        return False
