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
            self.active = dict(attrs).get('type', '').lower().split(';')[0].strip() == 'application/ld+json'
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
