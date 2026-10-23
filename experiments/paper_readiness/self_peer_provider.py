"""Offline extraction of fixed GDAKI host code for a recording-API CPU harness.

No vendor headers, GPU calls, provider execution or network timing are involved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

NCCL = '12df1a11afad322be5a204a2db890161cbf8131d'
DEEPEP = 'f6aa38fe06cd4a0489e7730365d4e0114970a2f3'
HOST = 'src/transport/net_ib/gdaki/gin_host_gdaki.cc'
FILES = {'nccl': [HOST, 'src/gin/gin_host.cc', 'src/include/nccl_device/host.h'],
         'deepep': ['csrc/kernels/comm/context.cpp']}


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare(repo, output):
    assert not output.exists() and output.parent.is_dir()
    assert shutil.disk_usage(output.parent).free > 8*2**30+32*2**20
    output.mkdir()
    rows = []
    for label, stamp, commit in (('nccl', 'nccl_gin_20261022', NCCL),
                                  ('deepep', 'deepep_modern_20261022', DEEPEP)):
        cache = repo/'data/research'/stamp
        tree_data = (cache/'tree.json').read_bytes()
        tree = json.loads(tree_data)
        assert not tree['truncated']
        index = {r['path']: r for r in tree['tree']}
        (output/(label+'-tree.json')).write_bytes(tree_data)
        if label == 'nccl':
            ident = json.loads((cache/'commit.json').read_text())
            assert ident['sha'] == commit and ident['tree']['sha'] == tree['sha']
            shutil.copyfile(cache/'commit.json', output/'nccl-commit.json')
        else:
            ident = json.loads((cache/'tree-source.json').read_text())
            assert ident['commit'] == commit
            request = [r for r in ident['requests'] if '/git/trees/' in r['url']]
            assert len(request) == 1 and request[0]['sha256'] == sha(tree_data)
            shutil.copyfile(cache/'tree-source.json', output/'deepep-tree-source.json')
        for name in FILES[label]:
            data = (cache/'sources'/name).read_bytes()
            blob = hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            assert index[name]['sha'] == blob and index[name]['size'] == len(data)
            dest = output/label/name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            rows.append(dict(project=label, commit=commit, path=name, blob=blob,
                             bytes=len(data), sha256=sha(data)))
    save(output/'sources.json', rows)


def extract(source, output):
    assert not output.exists() and output.parent.is_dir()
    text = (source/'nccl'/HOST).read_text()
    assert sha(text.encode()) == 'd554a0295c463782d60a36b746ed8e998c7e427493f00e6b6813f9d945fdd361'
    output.mkdir()
    excerpts = []

    def keep(name, start, end):
        assert text.count(start) == 1, start
        a = text.index(start)
        b = text.index(end, a)+len(end)
        raw = text[a:b]
        (output/(name+'.inc')).write_text(raw)
        excerpts.append(dict(name=name, first_line=text[:a].count('\n')+1,
                             last_line=text[:b].count('\n')+1, sha256=sha(raw.encode())))
        return raw

    functions = [keep(n, marker, '\n}') for n, marker in (
        ('fill', 'static void gdakiFillExchInfo('),
        ('valid_mtu', 'static bool gdakiIsValidActiveMtu('),
        ('path_mtu', 'static ncclResult_t gdakiGetPathMtu('),
        ('connect', 'static ncclResult_t gdakiConnectQp('))]
    start = '  for (int ctx_idx = 0; ctx_idx < ncontexts; ctx_idx++) {\n    int qp_idx = rank + ctx_idx * nranks;'
    assert text.count(start) == 1
    a = text.index(start)
    b = text.index('\n  if (needCompanion) {', a)
    loops = text[a:b]
    (output/'self_loops.inc').write_text(loops)
    excerpts.append(dict(name='self_loops', first_line=text[:a].count('\n')+1,
                         last_line=text[:b].count('\n')+1, sha256=sha(loops.encode())))
    resources = keep('resource_declarations', '  const int rankOff = rank % rankStride;',
                     '  const int nqps = nqps_per_rank * (nranks + 1);  // +1 for the local rank.\n                                   // The last group is the responder of the local rank.')
    save(output/'excerpts.json', excerpts)
    combined = '\n'.join(functions)
    masks = sorted(set(re.findall(r'\bDOCA_VERBS_QP_ATTR_[A-Z_]+', combined)))
    setters = sorted(set(re.findall(r'doca_verbs_qp_attr_set_(\w+)\(', combined)))
    definitions = ['// Synthetic constants: names are checked; values are not the vendor ABI.']
    for i, name in enumerate(masks):
        definitions.append(f'constexpr int {name} = 1 << {i};')
    definitions.append('static json mask_names(int mask) { json a=json::array();')
    for name in masks:
        definitions.append(f'if (mask & {name}) a.push_back("{name}");')
    definitions.append('return a; }')
    for name in setters:
        if name == 'ah_attr':
            definitions.append('static int doca_verbs_qp_attr_set_ah_attr(doca_verbs_qp_attr_t* a, Ah* v) { a->fields["ah_attr"]=v->fields; return 0; }')
        else:
            definitions.append(f'template<class T> static int doca_verbs_qp_attr_set_{name}(doca_verbs_qp_attr_t* a,T v) {{ a->fields["{name}"]=v; return 0; }}')
    (output/'generated_api.inc').write_text('\n'.join(definitions)+'\n')
    save(output/'synthetic_api.json', dict(attribute_masks=masks, setters=setters,
         constants_are_vendor_abi=False, types_are_vendor_abi=False))
    (output/'original_functions.inc').write_text(combined+'\n')
    # Static evidence beyond the executed slice; quoted separately from execution.
    other = []
    needles = {'nccl/'+HOST: ['qp_init_attr.send_dbr_mode_ext = DOCA_GPUNETIO_VERBS_SEND_DBR_MODE_EXT_VALID_DBR;',
                             'const bool needCompanion = (nCounters > 0);'],
               'nccl/src/gin/gin_host.cc': ['nContextsTotal = ROUNDUP(nContextsTotal, backend->ginCommCount);',
                                          'reqs->ginCounterCount,'],
               'nccl/src/include/nccl_device/host.h': ['/* ginCounterCount */'],
               'deepep/csrc/kernels/comm/context.cpp': ['reqs = NCCL_DEV_COMM_REQUIREMENTS_INITIALIZER;',
                                                     'reqs.ginConnectionType = allow_hybrid_mode ?',
                                                     'reqs.ginSignalCount = num_ranks + 2 * 2;']}
    for path, patterns in needles.items():
        lines = (source/path).read_text().splitlines()
        for pattern in patterns:
            matches = [i for i, line in enumerate(lines) if pattern in line]
            assert matches, (path, pattern)
            for i in matches:
                lo, hi = max(0, i-2), min(len(lines), i+4)
                other.append(dict(path=path, needle=pattern, first_line=lo+1,
                                  lines=lines[lo:hi]))
    ep = (source/'deepep/csrc/kernels/comm/context.cpp').read_text()
    assert 'reqs.ginCounterCount' not in ep
    save(output/'static-evidence.json', other)
    assert resources and loops


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='phase', required=True)
    a = sub.add_parser('prepare')
    a.add_argument('--repo', type=Path, required=True)
    a.add_argument('--output', type=Path, required=True)
    a = sub.add_parser('extract')
    a.add_argument('--source', type=Path, required=True)
    a.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.phase == 'prepare':
        prepare(args.repo, args.output)
    else:
        extract(args.source, args.output)


if __name__ == '__main__':
    main()
