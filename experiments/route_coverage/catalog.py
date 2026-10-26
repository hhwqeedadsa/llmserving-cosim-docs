"""Fetch bounded public directory metadata and prepare a pinned 64-file plan.

No credentials or access-term acceptance. No mirror fallback and no retries.
An incomplete cached listing can generate a manual plan, never authorize fetching.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit

REPO = 'core12345/MoE_expert_selection_trace'
REV = '27febb7b2d24169560a9f6f83d38eb53f94916cf'
MODEL = 'cognitivecomputations/DeepSeek-R1-AWQ'
GROUPS = ('mmlu/computer_security', 'mmlu/econometrics',
          'mmlu_ZH_CN/international_law', 'livecodebench/execution')
SEED = 20261026
LIMIT = 2**20


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != 'https':
            raise ValueError('Non-HTTPS redirect refused')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit):
    try:
        opener = urllib.request.build_opener(HTTPSRedirect())
        with opener.open(urllib.request.Request(url), timeout=12) as response:
            raw = response.read(limit + 1)
            if len(raw) > limit:
                return None, dict(state='byte_limit_exceeded')
            return raw, dict(state='read', bytes=len(raw), sha256=digest(raw),
                             pagination=bool(response.headers.get('Link')))
    except urllib.error.HTTPError as error:
        return None, dict(state='http_error', http_status=error.code)
    except Exception as error:
        # Exception text can contain signed URLs; retain only its type.
        return None, dict(state='connection_error', error_type=type(error).__name__)


def plan(catalogs, excluded):
    rows = []
    for group in GROUPS:
        catalog = catalogs[group]
        candidates = [r for r in catalog['files'] if r['path'] not in excluded]
        ordered = sorted(candidates, key=lambda r: (digest((str(SEED)+'\n'+r['path']).encode()), r['path']))
        if len(ordered) < 16:
            raise ValueError('Insufficient visible candidates: '+group)
        for index, row in enumerate(ordered[:16]):
            # Grouping happens before reading content; no hotspot-based selection.
            rows.append(dict(row, group=group, group_order=index,
                             split='development' if index < 8 else 'holdout_candidate',
                             url='https://huggingface.co/datasets/'+REPO+'/resolve/'+REV+'/'+quote(row['path'], safe='/')))
    estimated = (sum(r['size'] for r in rows)
                 if all(r.get('size') is not None for r in rows) else None)
    return dict(schema_version='moe-route-expansion-plan/1', dataset_id=REPO,
                revision=REV, collector_model=MODEL, seed=SEED,
                algorithm='sha256_seed_newline_path_v1_per_group',
                requested_files=64, data_ready=False, files=rows,
                independent_capture_runs_verified=False,
                scope='four_declared_subtrees_not_population_sample',
                max_download_bytes=128*2**20, max_file_bytes=8*2**20,
                all_catalogs_complete=all(c['complete'] for c in catalogs.values()),
                estimated_selected_bytes=estimated,
                fits_download_byte_budget=(estimated <= 128*2**20 if estimated is not None else None))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--cached-api', required=True, type=Path)
    parser.add_argument('--existing-manifest', required=True, type=Path)
    parser.add_argument('--probe-raw', action='store_true')
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=False)
    cached_raw = args.cached_api.read_bytes()
    if digest(cached_raw) != 'd6e25aa5d6d583d1dae72f78e010abad5839e8a85b3a53f0715ceba0178eb3c5':
        raise ValueError('Historical visible-list identity mismatch')
    cached = json.loads(cached_raw)
    if cached['sha'] != REV or cached['id'] != REPO:
        raise ValueError('Historical visible-list source mismatch')
    existing = json.loads(args.existing_manifest.read_bytes())
    excluded = {f['original_path'] for f in existing['files']}
    catalogs = {}

    def fetch_group(group):
        prefix = MODEL+'/'+group+'/'
        url = 'https://huggingface.co/api/datasets/'+REPO+'/tree/'+REV+'/'+quote(prefix.rstrip('/'), safe='/')+'?recursive=true&expand=false&limit=1000'
        raw, status = fetch(url, LIMIT)
        return group, prefix, url, raw, status

    with ThreadPoolExecutor(max_workers=4) as pool:
        for group, prefix, url, raw, status in pool.map(fetch_group, GROUPS):
            files = []
            complete = False
            if raw is not None:
                (out/(group.replace('/', '__')+'.json')).write_bytes(raw)
                try:
                    entries = json.loads(raw)
                    if not isinstance(entries, list):
                        raise ValueError('Not a directory list')
                    for item in entries:
                        path = item['path']
                        p = PurePosixPath(path)
                        if p.is_absolute() or '..' in p.parts or not path.startswith(prefix):
                            raise ValueError('Out-of-subtree directory entry')
                        if item['type'] != 'file' or not path.endswith('.json'):
                            continue
                        files.append(dict(path=path, size=item['size'], git_blob_id=item['oid'],
                                          **({'lfs_sha256': item['lfs']['oid']} if 'lfs' in item else {})))
                    if len({r['path'] for r in files}) != len(files):
                        raise ValueError('Duplicate directory path')
                    complete = not status['pagination']
                except (ValueError, KeyError, TypeError):
                    status['state'] = 'invalid_directory_metadata'
                    files = []
            if not files or not complete:
                # This has no size/hash identity; not suitable for automatic acquisition.
                files = [dict(path=r['rfilename'], size=None, git_blob_id=None)
                         for r in cached['siblings'] if r['rfilename'].startswith(prefix)
                         and r['rfilename'].endswith('.json')]
            catalogs[group] = dict(url=url, status=status, complete=complete, files=files,
                                   fallback_visible_list=not complete)
    save(out/'catalog-status.json', catalogs)
    selection = plan(catalogs, excluded)
    save(out/'download-plan.json', selection)
    (out/'download-urls.txt').write_text('\n'.join(r['url'] for r in selection['files'])+'\n')
    probe = dict(state='not_requested', downloaded_route_files=0)
    if args.probe_raw:
        row = selection['files'][0]
        raw, probe = fetch(row['url'], 8*2**20)
        probe.update(path=row['path'], downloaded_route_files=0)
        if raw is not None:
            identity = (digest(raw) == row['lfs_sha256'] if 'lfs_sha256' in row else
                        hashlib.sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest() == row['git_blob_id'])
            if row['size'] == len(raw) and identity:
                dest = out/'raw'/row['path']
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
                probe.update(state='identity_verified', downloaded_route_files=1)
            else:
                (out/'probe-unadmitted.bin').write_bytes(raw)
                probe.update(state='unverified_content_not_admitted')
    save(out/'access.json', dict(schema_version='moe-route-access/1', metadata_requests=4,
                               raw_requests=int(args.probe_raw), credentials_used=False,
                               terms_accepted=False, raw_probe=probe))
    print(json.dumps(dict(plan_files=len(selection['files']), complete_catalogs=selection['all_catalogs_complete'],
                          selected_bytes=selection['estimated_selected_bytes'], probe=probe), ensure_ascii=False))


if __name__ == '__main__':
    main()
