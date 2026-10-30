"""Verify published artifacts, all command receipts and unchanged network files."""
import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from audit import file_hashes, load, require, save, sha


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('href', 'src') and value:
                self.targets.append(value)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results', type=Path, required=True)
    p.add_argument('--monitors', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    root = a.results.resolve()
    publication = load(root/'publication.json')
    for name, expected in publication['artifacts'].items():
        require(sha(root/name)==expected, 'published artifact changed: '+name)
    frozen = load(root/'frozen.json')
    require(file_hashes(Path(frozen['run']))==frozen['run_sha256'], 'historical network changed')
    require(file_hashes(root/'inputs')==frozen['input_sha256'], 'projected inputs changed')
    commands = [load(p) for p in sorted((root/'commands').glob('*/command.json'))]
    require(len(commands)==13, 'incomplete public command receipts')
    require(all(c['exit_code']==c['expected_exit_code'] for c in commands), 'unexpected command exit')
    require(sum(c['exit_code']==1 for c in commands)==1, 'expected shape rejection missing')
    for c in commands:
        require(c['argv'][2:5]==['-m','moe_ub_cosim','scaleout'], 'nonpublic command')
        require(c['argv'][5] in ('modern-deepep-plan','modern-deepep-check','deepep-plan'), 'unexpected command/network execution')
    parser = Links()
    parser.feed((root/'report.html').read_text())
    local = 0
    for target in parser.targets:
        parsed = urlsplit(target)
        if parsed.scheme:
            require(parsed.scheme=='https' and parsed.netloc=='github.com', 'unexpected external source link')
            continue
        require((root/unquote(parsed.path)).is_file(), 'broken report link: '+target)
        local += 1
    resources = {p.parent.name: load(p) for p in sorted(a.monitors.rglob('probe.json'))}
    require(len(resources)==3 and all(r['status']=='completed' and r['child_exit_code']==0 and not r['remaining_live_group_pids']
        for r in resources.values()), 'unfinished/failed stage or live descendant')
    require(sum(r['wall_seconds'] for r in resources.values())<600, 'time budget exceeded')
    # Final verifier is itself monitored; this receipt explicitly only includes
    # the three completed audit/check/report stages, never its live monitor.
    result = dict(passed=True, published_files_checked=len(publication['artifacts']),
        public_commands=len(commands), expected_budget_rejections=1, local_html_links=local,
        historical_files_checked=len(frozen['run_sha256']), new_networks=0,
        source_audit_compiler_implementation='not CUDA/library execution',
        browser_rendering_checked=False, completed_stages=resources)
    save(a.output, result)
    print('verified publication, 13 command receipts, unchanged network and 3 completed CPU stages')


if __name__ == '__main__':
    main()
