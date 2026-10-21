#!/usr/bin/env python3
"""Fetch bounded public documents for this review, without installing software."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
import base64
import argparse
import json
import re
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SNAPSHOTS = ROOT / 'data/research/system_innovation_20261021'
MAX_BYTES = 1536 * 1024
SOURCES = [
    ('lina', 'https://arxiv.org/html/2210.17223v2'),
    ('comet', 'https://arxiv.org/html/2502.19811v3'),
    ('analytical_overlap', 'https://arxiv.org/html/2609.07536v1'),
    ('patterns', 'https://arxiv.org/html/2510.05497v5'),
    ('megascale_infer', 'https://arxiv.org/html/2504.02263v4'),
    ('llmservingsim2', 'https://arxiv.org/html/2602.23036v2'),
    ('simai', 'https://api.github.com/repos/aliyun/SimAI/contents/README.md'),
    ('clem', 'https://api.github.com/repos/aliyun/SimAI-CLEM/contents/README.md'),
    ('vidur', 'https://api.github.com/repos/microsoft/vidur/contents/README.md'),
    ('tutel', 'https://api.github.com/repos/microsoft/tutel/contents/README.md'),
    ('deepep_v121', 'https://api.github.com/repos/deepseek-ai/DeepEP/contents/README.md?ref=9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee'),
    ('deepep_v2', 'https://api.github.com/repos/deepseek-ai/DeepEP/contents/README.md'),
    ('aalo', 'https://www.usenix.org/conference/nsdi15/technical-sessions/presentation/chowdhury'),
    ('coz', 'https://api.github.com/repos/plasma-umass/coz/contents/README.md'),
    ('nanoflow', 'https://www.usenix.org/conference/osdi25/presentation/zhu-kan'),
    ('aalo_paper', 'https://www.mosharaf.com/wp-content/uploads/aalo-sigcomm15.pdf'),
]


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip += 1
        elif tag in ('p', 'div', 'section', 'h1', 'h2', 'h3', 'tr', 'li'): self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip -= 1
        elif tag in ('p', 'div', 'section', 'h1', 'h2', 'h3', 'tr', 'li'): self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def fetch(item):
    key, url = item
    record = dict(id=key, url=url, accessed_utc=datetime.now(timezone.utc).isoformat())
    try:
        req = Request(url, headers={'User-Agent': 'MoE-system-research-review', 'Accept': 'application/vnd.github+json' if 'api.github.com' in url else 'text/html'})
        with urlopen(req, timeout=20) as response:
            raw = response.read(MAX_BYTES + 1)
            record.update(status=response.status, resolved_url=response.url)
        if len(raw) > MAX_BYTES: raise ValueError('source exceeded 1.5 MiB cap; not saved as a complete source')
        if 'api.github.com' in url:
            metadata = json.loads(raw)
            raw = base64.b64decode(metadata['content'])
            record.update(git_blob_sha=metadata['sha'], public_url=metadata['html_url'],
                          immutable_blob_api=metadata['git_url'])
            text = raw.decode()
        elif raw.startswith(b'%PDF'):
            pdf = SNAPSHOTS / (key + '.pdf'); pdf.write_bytes(raw)
            result = subprocess.run(['pdftotext', '-layout', str(pdf), '-'], text=True, capture_output=True, timeout=10)
            if result.returncode: raise ValueError('pdftotext failed: '+result.stderr[:200])
            text = result.stdout
        else:
            parser = PlainText(); parser.feed(raw.decode())
            text = '\n'.join(' '.join(x.split()) for x in ''.join(parser.parts).splitlines() if x.strip())
            versions = sorted(set(re.findall(r'\d{4}\.\d{4,5}v\d+', raw.decode())))
            if 'arxiv.org' in url and len(versions) == 1:
                record.update(arxiv_version=versions[0], public_url='https://arxiv.org/html/'+versions[0])
        path = SNAPSHOTS / (key + ('.pdf' if raw.startswith(b'%PDF') else '.md' if 'git_blob_sha' in record else '.html'))
        path.write_bytes(raw)
        plain = SNAPSHOTS / (key + '.txt'); plain.write_text(text)
        record.update(bytes=len(raw), sha256=sha256(raw).hexdigest(),
                      local_snapshot=str(path.relative_to(ROOT)), text_snapshot=str(plain.relative_to(ROOT)),
                      text_sha256=sha256(plain.read_bytes()).hexdigest(), text_characters=len(text))
        lines = text.splitlines()
        keywords = re.compile(r'Abstract|prioriti|expert popular|bandwidth|overlap|resource|SMs|small number|calibrat|heterogene|NSDI|inference|2026/09', re.I)
        excerpts = [line[:1300] for line in lines if keywords.search(line)][:20]
        record['excerpts'] = excerpts
    except Exception as error:
        record.update(status='failed', error=str(error))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ids', nargs='+', choices=[key for key, _ in SOURCES])
    args = parser.parse_args()
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    selected = [item for item in SOURCES if not args.ids or item[0] in args.ids]
    with ThreadPoolExecutor(max_workers=3) as executor:
        fresh = list(executor.map(fetch, selected))
    previous = json.loads((HERE / 'sources.json').read_text())['sources'] if (HERE / 'sources.json').exists() else []
    by_id = {row['id']: row for row in previous}
    by_id.update({row['id']: row for row in fresh})
    records = [by_id[key] for key, _ in SOURCES if key in by_id]
    (HERE / 'sources.json').write_text(json.dumps(dict(
        scope='Public documents only; no installation, model download or simulator execution',
        source_limit_bytes=MAX_BYTES, max_requests=len(SOURCES), sources=records), ensure_ascii=False, indent=2)+'\n')
    for row in fresh:
        print(json.dumps({k: row[k] for k in ('id', 'status', 'bytes', 'text_characters', 'error') if k in row}, ensure_ascii=False))


if __name__ == '__main__': main()
