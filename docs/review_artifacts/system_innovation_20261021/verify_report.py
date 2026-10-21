#!/usr/bin/env python3
"""Validate report text, references, immutable evidence and offline rendering.

This is a document check; it does not build or execute the simulation platform.
"""
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
REPORT = ROOT / 'docs/reviews/系统视角的仿真器创新方案与修改建议_20261021.md'


def digest(path): return sha256(path.read_bytes()).hexdigest()


class Inspect(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.main = False; self.text = []; self.links = []; self.ids = set(); self.tables = 0
        self.rows = []; self.row = None
        self.expected_columns = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'main': self.main = True
        if tag == 'a': self.links.append(attrs['href'])
        if 'id' in attrs:
            assert attrs['id'] not in self.ids, 'Duplicate HTML id'
            self.ids.add(attrs['id'])
        if tag == 'table': self.tables += 1; self.expected_columns = None
        if tag == 'tr': self.row = 0
        if tag in ('th', 'td'): self.row += 1
        assert not (tag in ('script', 'img', 'link') and (attrs.get('src', '').startswith('http') or attrs.get('href', '').startswith('http'))), 'External display dependency'

    def handle_endtag(self, tag):
        if tag == 'main': self.main = False
        if tag == 'tr':
            if self.expected_columns is None: self.expected_columns = self.row
            assert self.row == self.expected_columns, 'Inconsistent table columns'
            self.rows.append(self.row); self.row = None

    def handle_data(self, data):
        if self.main: self.text.append(data)


def norm(s): return ''.join(s.split())


def main():
    doc = (HERE/'report.html').read_text(); parser = Inspect(); parser.feed(doc)
    visible = norm(''.join(parser.text)); fragments = 0
    for line in REPORT.read_text().splitlines():
        if not line.strip() or line.startswith('```'): continue
        cells = line.strip().strip('|').split('|') if line.startswith('|') else [line]
        for cell in cells:
            if re.fullmatch(r'\s*:?-+:?\s*', cell): continue
            cell = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', cell)
            cell = re.sub(r'^(?:#{1,3}\s+|-\s+|\d+\.\s+)', '', cell)
            cell = cell.replace('**', '').replace('`', '')
            assert norm(cell) in visible, ('Missing report text', cell[:90])
            fragments += 1
    local_links = 0
    for target in parser.links:
        if target.startswith(('http://', 'https://')): continue
        path, _, anchor = target.partition('#')
        if not path:
            assert anchor in parser.ids, target
        else:
            assert (HERE/path).resolve().exists(), target
            local_links += 1
    code = json.loads((HERE/'code_evidence.json').read_text())
    for row in code['snippets']:
        path = ROOT/row['path']; assert digest(path) == row['file_sha256']
        actual = '\n'.join(path.read_text().splitlines()[row['start_line']-1:row['end_line']])
        assert actual == row['excerpt'], row['id']
    source_index = json.loads((HERE/'sources.json').read_text())
    passed, failed, byte_count = [], [], 0
    for row in source_index['sources']:
        if row['status'] != 200:
            failed.append(dict(id=row['id'], error=row['error'])); continue
        assert digest(ROOT/row['local_snapshot']) == row['sha256'], row['id']
        assert digest(ROOT/row['text_snapshot']) == row['text_sha256'], row['id']
        byte_count += row['bytes']; passed.append(row['id'])
    assert len(passed) == 15 and len(failed) == 1
    by_source = {row['id']:row for row in source_index['sources']}
    passages = json.loads((HERE/'prior_art_passages.json').read_text())
    for group in passages:
        src = by_source[group['id']]; text = (ROOT/src['text_snapshot']).read_text()
        assert src['text_sha256'] == group['text_sha256']
        for row in group['passages']:
            assert text[row['start_character']:row['end_character']] == row['excerpt']
            assert row['search'].casefold() in row['excerpt'].casefold()
    before = (HERE/'report.html').read_bytes()
    subprocess.run(['python3', str(HERE/'render_report.py')], cwd=ROOT, check=True, capture_output=True)
    assert before == (HERE/'report.html').read_bytes()
    assert digest(REPORT) in doc
    assert '\ufffd' not in doc and 'scaleout_model.py' not in doc
    for rel in ('README.md', '../../reviews/系统视角的仿真器创新方案与修改建议_20261021.md'):
        path = HERE/rel
        if not path.exists(): continue
        for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', path.read_text()):
            if target.startswith(('http://', 'https://', '#')): continue
            if (path.parent/target).resolve() == HERE/'verification.json': continue
            assert (path.parent/target.split('#')[0]).resolve().exists(), (str(path), target)
    # This report does not authorize implementation changes before review.
    changed = subprocess.check_output(['git','diff','--name-only',code['baseline_commit']], cwd=ROOT, text=True).splitlines()
    assert all(p == 'README.md' or p.startswith('docs/') for p in changed), changed
    result = dict(status='passed', date='2026-10-21 Asia/Shanghai',
        scope='Research report only; user review and implementation remain pending',
        report_sha256=digest(REPORT), html_sha256=digest(HERE/'report.html'),
        checks=dict(code_excerpts=len(code['snippets']), public_documents=len(passed), public_source_bytes=byte_count,
                    source_failures_preserved=failed, claim_passage_groups=len(passages),
                    html_fragments_present=fragments, html_tables=parser.tables, local_html_links=local_links,
                    html_rebuild_byte_identical=True, platform_tracked_files_unchanged_since_baseline=True),
        requirements_audit=[
            dict(requirement='Teacher goals, constraints, service and value', evidence='Section 1', status='addressed'),
            dict(requirement='Both Scale-Up and Scale-Out from current code', evidence='Sections 2 and 7; 14 checked excerpts', status='addressed'),
            dict(requirement='Concrete innovation candidates and prior-art differences', evidence='Sections 3–6, 9; 15 original documents', status='proposed_not_empirically_validated'),
            dict(requirement='Modification locations and validation plan', evidence='Sections 7–10', status='designed_not_implemented'),
            dict(requirement='Report for review then modify', evidence='Section 11 and task status', status='report_ready_user_review_and_implementation_pending')],
        not_performed=['No platform behavior changes or simulation regression', 'No new network performance simulation',
                       'No hardware performance measurement', 'No browser visual inspection',
                       'No dependency installation or change', 'No push'])
    (HERE/'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(status='passed', fragments=fragments, tables=parser.tables, code_excerpts=len(code['snippets']), sources=len(passed)), ensure_ascii=False))


if __name__ == '__main__': main()
