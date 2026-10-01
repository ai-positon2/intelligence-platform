"""Bounded organizer-published schema.org Event observations; never model facts."""
import json
from html.parser import HTMLParser


class _JSONLD(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = (dict(attrs).get('type') or '').lower().split(';')[0].strip() == 'application/ld+json'
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.blocks.append(''.join(self.parts))
            self.active = False


def events(markup):
    parser = _JSONLD()
    parser.feed(markup)
    pending, found = [], []
    for block in parser.blocks[:40]:
        try:
            pending.append(json.loads(block))
        except (ValueError, RecursionError):
            continue
    visited = 0
    while pending and visited < 500:
        node = pending.pop()
        visited += 1
        if isinstance(node, list):
            pending.extend(node[:100])
        elif isinstance(node, dict):
            types = node.get('@type', [])
            if isinstance(types, str):
                types = [types]
            if isinstance(types, list) and any(str(t).rsplit('/', 1)[-1] in
                    ('Event', 'BusinessEvent', 'EducationEvent', 'ConferenceEvent') for t in types):
                found.append({k: node[k] for k in ('name','startDate','endDate','eventStatus','location','offers') if k in node})
            for key in ('@graph', 'subEvent', 'subEvents', 'mainEntity'):
                if key in node:
                    pending.append(node[key])
    return found[:50]


class _Titles(HTMLParser):
    """The document <title> and its og:/twitter: title twins, nothing else.

    Only the head's title: an SVG icon's <title> ("Icon", "Close") is also
    a <title> tag, and an audit on 2026-09-30 found it taken as the page
    title whenever the head's own <title> was empty."""
    META = ('og:title', 'twitter:title')
    FOREIGN = ('svg', 'math')

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.seen_title = False
        self.in_body = False
        self.foreign = 0
        self.parts = []
        self.found = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.FOREIGN:
            self.foreign += 1
        elif tag == 'body':
            self.in_body = True
        elif tag == 'title' and not self.seen_title and not self.in_body and not self.foreign:
            self.in_title = True
            self.seen_title = True
        elif tag == 'meta' and (a.get('property') or a.get('name') or '').lower() in self.META:
            self.found.append(a.get('content') or '')

    def handle_endtag(self, tag):
        if tag in self.FOREIGN and self.foreign:
            self.foreign -= 1
        elif tag == 'title' and self.in_title:
            self.in_title = False
            self.found.insert(0, ''.join(self.parts))

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)


def titles(markup):
    """Up to three distinct page titles as the organizer wrote them."""
    parser = _Titles()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        pass
    out = []
    for t in parser.found:
        t = ' '.join(str(t).split())[:300]
        if t and t not in out:
            out.append(t)
    return out[:3]
