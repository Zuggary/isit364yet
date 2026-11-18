"""Offline structural checks for the maintained pages; Python standard library only."""
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import struct
import unittest
from urllib.parse import urlsplit, unquote
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent


class Page(HTMLParser):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.tags = []
        self.feed(path.read_text())

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class Site(unittest.TestCase):
    def test_links_assets_and_id_references(self):
        for name in ('index.html', 'past/index.html', 'privacy.html'):
            page = Page(ROOT / name)
            ids = [a['id'] for _, a in page.tags if 'id' in a]
            self.assertEqual(len(ids), len(set(ids)), name)
            for tag, attrs in page.tags:
                for key in ('aria-labelledby', 'aria-describedby', 'for'):
                    for target in attrs.get(key, '').split():
                        self.assertIn(target, ids, (name, key, target))
                for key in ('href', 'src'):
                    url = urlsplit(attrs.get(key, ''))
                    if url.scheme or url.netloc or not url.path:
                        continue
                    target = (page.path.parent / unquote(url.path)).resolve()
                    if target.is_dir():
                        target /= 'index.html'
                    self.assertTrue(target.is_file(), (name, key, url.path))
                if tag == 'main':
                    self.assertEqual(sum(t == 'main' for t, _ in page.tags), 1)

    def test_metadata_and_social_image(self):
        for name, canonical in (('index.html', 'https://www.isit364yet.com/'),
                                ('past/index.html', 'https://www.isit364yet.com/past/'),
                                ('privacy.html', 'https://www.isit364yet.com/privacy.html')):
            page = Page(ROOT / name)
            self.assertIn(('link', {'rel': 'canonical', 'href': canonical}), page.tags)
            self.assertTrue(any(t == 'meta' and a.get('name') == 'description' and a.get('content')
                                for t, a in page.tags))
        source = (ROOT / 'index.html').read_text()
        structured = re.search(r'<script type="application/ld\+json">(.*?)</script>', source, re.S)
        self.assertEqual(json.loads(structured[1])['url'], 'https://www.isit364yet.com/')
        data = (ROOT / 'og.png').read_bytes()
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II', data[16:24]), (1200, 630))
        # Sitemap policy and its legacy privacy URL are intentionally preserved.
        tree = ET.parse(ROOT / 'sitemap.xml')
        self.assertEqual(len(tree.findall('.//{*}loc')), 3)

    def test_landmark_and_nonvisual_controls(self):
        source = (ROOT / 'index.html').read_text()
        self.assertLess(source.index('<main>'), source.index('<header'))
        self.assertLess(source.index('</main>'), source.index('<footer'))
        page = Page(ROOT / 'index.html')
        by_id = {a['id']: (t, a) for t, a in page.tags if 'id' in a}
        self.assertEqual(by_id['status-note'][1]['class'], 'sr')
        self.assertEqual(by_id['scrub-out'][1]['aria-hidden'], 'true')
        self.assertIn('autofocus', by_id['pc-close'][1])
        self.assertIn('disabled', by_id['pc-save'][1])
        self.assertEqual(by_id['g-controls'][1]['class'], 'sr')


if __name__ == '__main__':
    unittest.main()
