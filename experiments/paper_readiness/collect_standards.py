"""Bounded read of primary standards/venue criteria, with explicit failure records."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

SOURCES = [('rfc6298', 'https://www.rfc-editor.org/rfc/rfc6298.txt'),
           ('rfc9040', 'https://www.rfc-editor.org/rfc/rfc9040.txt'),
           ('nsdi26_cfp', 'https://www.usenix.org/conference/nsdi26/call-for-papers'),
           ('sigcomm26_cfp', 'https://conferences.sigcomm.org/sigcomm/2026/cfp/')]


class Plain(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('style', 'script'):
            self.skip += 1
        elif tag in ('p', 'div', 'li', 'h1', 'h2', 'h3'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('style', 'script'):
            self.skip -= 1

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    args.output.mkdir(exist_ok=False, parents=True)

    def fetch(item):
        key, url = item
        record = dict(id=key, url=url, accessed_utc=datetime.now(timezone.utc).isoformat())
        try:
            with urlopen(Request(url, headers={'User-Agent': 'MoE-paper-evidence-review'}), timeout=20) as response:
                data = response.read(2 * 1024**2 + 1)
                record.update(http_status=response.status, resolved_url=response.url)
            if len(data) > 2 * 1024**2:
                raise ValueError('Document over 2MiB, not accepted as complete evidence')
            text = data.decode('utf-8')
            suffix = '.txt' if url.endswith('.txt') else '.html'
            (args.output / (key + suffix)).write_bytes(data)
            if suffix == '.html':
                parser = Plain()
                parser.feed(text)
                text = '\n'.join(' '.join(s.split()) for s in ''.join(parser.parts).splitlines() if s.strip())
            (args.output / (key + '-text.txt')).write_text(text)
            lines = text.splitlines()
            selected = [dict(line=i+1, text=line[:1500]) for i, line in enumerate(lines)
                        if re.search('original|novel|signific|valid|evaluat|shar|cach|Karn|back.*off|doubl', line, re.I)]
            record.update(status='read', bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), passages=selected[:45])
        except Exception as error:
            record.update(status='unavailable', reason=str(error))
        return record

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(fetch, SOURCES))
    assert sum(r.get('bytes', 0) for r in records) <= 16 * 1024**2
    (args.output / 'sources.json').write_text(json.dumps(dict(sources=records, scope='primary standards and official venue pages; no simulator execution'), ensure_ascii=False, indent=2) + '\n')
    print(json.dumps([{k: r[k] for k in ('id', 'status', 'bytes', 'reason') if k in r} for r in records]))


if __name__ == '__main__':
    main()
