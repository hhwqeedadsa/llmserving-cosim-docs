"""Offline source indexing. Patches are read as text, never applied or executed."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re

PINS = {
    'clem': ('aliyun/SimAI-CLEM', '4a91f8b655224c0b1d1c300aa09c7058f4da2707'),
    'network': ('aliyun/ns-3-alibabacloud', '434e4f2fe4bc2fb803a1dcda690869f66802f8b8'),
}
# These identify reviewed source statements, not runtime capability tests.
ANCHORS = (
    ('host_hook', 'clem', 'nccl_patch', 'src/nsibverbs/device.cc', 'context->ops.post_send = shm_ibv_post_send;'),
    ('verbs_dispatch', 'clem', 'nccl_patch', 'src/include/verbs.h', 'return qp->context->ops.post_send(qp, wr, bad_wr);'),
    ('post_send', 'clem', 'nccl_patch', 'src/nsibverbs/qp.cc', 'int shm_ibv_post_send('),
    ('command_fields', 'clem', 'nccl_patch', 'src/nsibverbs/qp.cc', 'cmd.cmd_args.post_send_input_args.wr.total_length ='),
    ('p2p_fifo', 'clem', 'nccl_patch', 'src/nsibverbs/qp.cc', 'if(enable_p2p && is_postfifo && is_src_dst_in_same_server)'),
    ('poll_cq', 'clem', 'nccl_patch', 'src/nsibverbs/cq.cc', 'int shm_ibv_poll_cq('),
    ('gather_address', 'clem', 'nccl_patch', 'src/device/all_gather.h', 'prims.directSend(0, 0, nelem);'),
    ('reduce_address', 'clem', 'nccl_patch', 'src/device/all_reduce.h', 'prims.directRecvReduceDirectSend(0, 0, nelem);'),
    ('scatter_address', 'clem', 'nccl_patch', 'src/device/reduce_scatter.h', 'prims.recvReduceCopy(0, 0, nelem,'),
    ('allocation', 'clem', 'nccl_tests_patch', 'src/common.cu', 'NCCLCHECK(ncclMemAlloc(sendbuff, SIM_ALLOC_MEM_SIZE));'),
    ('allocation_size', 'clem', 'nccl_tests_patch', 'src/common.h', '#define SIM_ALLOC_MEM_SIZE 8388608'),
    ('host_identity', 'clem', 'nccl_tests_patch', 'src/common.cu', 'hostHashs[proc] = proc / NUM_GPUS_PER_SERVER;'),
    ('launch', 'clem', 'run_nccl_16A100.sh', None, '-A ${NS3_PROCESS_IP} -b 64MB'),
    ('interface_defaults', 'network', 'simulation/src/applications/model/ibv-interface.cc', None, '.AddAttribute("SendLatency"'),
    ('submit_latency', 'network', 'simulation/src/applications/model/ibv-interface.cc', None, 'Simulator::Schedule(NanoSeconds(m_send_latency), &IbvQP::EnqueueSendRequest'),
    ('request_opcodes', 'network', 'simulation/src/applications/model/ibv-qp.cc', None, 'void IbvQP::EnqueueSendRequest('),
    ('flush_read', 'network', 'simulation/src/applications/model/ibv-qp.cc', None, '} else if (send_wr.opcode==IBV_WR_RDMA_READ)'),
    ('persistent_qp', 'network', 'simulation/src/applications/model/ibv-qp.cc', None, 'm_qp = rdma->AddQueuePair('),
    ('wr_completed', 'network', 'simulation/src/applications/model/ibv-qp.cc', None, 'void IbvQP::WrCompleted('),
    ('fifo_visibility', 'network', 'simulation/src/applications/model/ibv-interface.cc', None, 'void IbvInterface::ExecPostFifo('),
    ('completion_opcodes', 'network', 'simulation/src/applications/model/ibv-cq.cc', None, 'void IbvCQ::GenerateSendCQE('),
    ('message_enqueue', 'network', 'simulation/src/point-to-point/model/rdma-queue-pair.cc', None, 'void RdmaQueuePair::PushMessage('),
    ('message_front', 'network', 'simulation/src/point-to-point/model/rdma-queue-pair.cc', None, 'uint64_t RdmaQueuePair::GetBytesLeft()'),
    ('message_finish', 'network', 'simulation/src/point-to-point/model/rdma-queue-pair.cc', None, 'void RdmaQueuePair::FinishMessage()'),
    ('ack_condition', 'network', 'simulation/src/point-to-point/model/rdma-queue-pair.cc', None, 'return snd_una >= m_messages.front().send_wr.total_length'),
    ('ack_handling', 'network', 'simulation/src/point-to-point/model/rdma-hw.cc', None, 'int RdmaHw::ReceiveAck('),
    ('completion_callback', 'network', 'simulation/src/point-to-point/model/rdma-hw.cc', None, 'void RdmaHw::QpCompleteMessage('),
    ('parallel_hook', 'network', 'simulation/scratch/QpReuseSimInfra.cc', None, 'MtpInterface::Enable(16);'),
    ('install_interfaces', 'network', 'simulation/scratch/QpReuseSimInfra.cc', None, 'apps = InstallRdmaApps(n, num_nodes);'),
)


def load(path):
    return json.loads(path.read_bytes())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def patch_lines(path):
    """Map visible context/additions to the post-patch line, without base sources."""
    target = None
    old = new = None
    expected = used = None
    rows = []

    def finish():
        if expected is not None:
            assert tuple(used) == expected, (path.name, target, used, expected)

    for number, line in enumerate(path.read_text().splitlines(), 1):
        if line.startswith('diff --git '):
            finish()
            target = line.split(' b/', 1)[1]
            old = new = expected = used = None
        elif line.startswith('@@ '):
            finish()
            match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
            assert match, line
            old, oc, new, nc = match.groups()
            old, new = int(old), int(new)
            expected = (int(oc or 1), int(nc or 1))
            used = [0, 0]
        elif expected is not None and line[:1] in (' ', '+', '-'):
            if line[0] != '-':
                rows.append(dict(target=target, target_line=new, patch_line=number, text=line[1:]))
                new += 1
                used[1] += 1
            if line[0] != '+':
                old += 1
                used[0] += 1
    finish()
    return rows


def execute(root, output):
    cache = root / 'cache'
    identities = {}
    sources = []
    for label, (repository, commit) in PINS.items():
        identity = load(cache / label / 'identity.json')
        assert (identity['repo'], identity['commit']) == (repository, commit)
        identities[label] = identity
        tree = {row['path']: row for row in load(cache / label / 'tree.json')['tree'] if row['type'] == 'blob'}
        for path in sorted((cache / label / 'sources').rglob('*')):
            if not path.is_file():
                continue
            name = path.relative_to(cache / label / 'sources').as_posix()
            if label == 'clem' and name == 'README.md':
                assert path.stat().st_size == 0  # Original failed response stays untouched.
                path = cache / label / 'fallback/README.md'
            raw = path.read_bytes()
            blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            assert len(raw) == tree[name]['size'] and blob == tree[name]['sha'], name
            sources.append(dict(label=label, path=name, cache_path=path.relative_to(root).as_posix(),
                                bytes=len(raw), git_blob=blob, sha256=sha(path)))
    indexed = {}
    references = []
    for key, label, name, target, needle in ANCHORS:
        path = cache / label / 'sources' / name
        if target:
            if name not in indexed:
                indexed[name] = patch_lines(path)
            matches = [r for r in indexed[name] if r['target'] == target and needle in r['text']]
            assert len(matches) == 1, (key, len(matches))
            row = matches[0]
            line = row['patch_line']
            target_line = row['target_line']
        else:
            matches = [i for i, text in enumerate(path.read_text().splitlines(), 1) if needle in text]
            assert len(matches) == 1, (key, len(matches))
            line = matches[0]
            target_line = None
        lines = path.read_text().splitlines()
        start, end = max(1, line - 2), min(len(lines), line + 8)
        repository, commit = PINS[label]
        references.append(dict(id=key, label=label, path=name, target_path=target,
                               line=line, target_line=target_line, needle=needle,
                               excerpt_start=start, excerpt_end=end, excerpt='\n'.join(lines[start-1:end]),
                               url=f'https://github.com/{repository}/blob/{commit}/{name}#L{line}'))
    requests = load(cache / 'http.json')
    evidence = dict(schema='clem-boundary-source-audit/1', identities=identities, sources=sources,
                    references=references, requests=len(requests), response_bytes=sum(r['bytes'] for r in requests),
                    http_elapsed_seconds=sum(r['elapsed_seconds'] for r in requests),
                    deadline_violations=[dict(path=r['path'], elapsed_seconds=r['elapsed_seconds'],
                                              status=r['status'], error=r['error']) for r in requests if r['elapsed_seconds'] > 10.5],
                    scope=dict(source_audit=True, network_simulations=0, builds=0, GPU_runs=0,
                               platform_changes=0, runtime_equivalence_proven=False,
                               research_innovations_established=False))
    save(output / 'evidence.json', evidence)
    template = (Path(__file__).parent / 'report_body.md').read_text()
    for ref in references:
        location = f"补丁{ref['line']}行，对应{ref['target_path']}:{ref['target_line']}" if ref['target_path'] else f"{ref['path']}:{ref['line']}"
        template = template.replace('{{' + ref['id'] + '}}', f"[{location}]({ref['url']})")
    assert '{{' not in template
    markdown = template + '\n\n## 来源位置与核验\n\n'
    for ref in references:
        markdown += f"- `{ref['id']}`：[固定原文]({ref['url']})"
        if ref['target_path']:
            markdown += f"；目标 `{ref['target_path']}:{ref['target_line']}`"
        markdown += '\n'
    (output / 'report.md').write_text(markdown)
    # Self-contained source report, no JS/dependencies or externally loaded media.
    def inline(part):
        escaped = html.escape(part)
        escaped = re.sub(r'\[([^\]]+)\]\((https://[^\s)]+)\)', r'<a href="\2">\1</a>', escaped)
        escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
        return re.sub(r'`([^`]+)`', r'<code>\1</code>', escaped)

    sections = []
    for part in markdown.split('\n\n'):
        escaped = inline(part)
        if part.startswith('# '):
            sections.append('<h1>' + escaped[2:] + '</h1>')
        elif part.startswith('## '):
            sections.append('<h2>' + escaped[3:] + '</h2>')
        elif part.startswith('### '):
            sections.append('<h3>' + escaped[4:] + '</h3>')
        elif part.startswith('|'):
            rows = part.splitlines()
            table = '<div class="table"><table>'
            for number, row in enumerate(rows):
                if number == 1:
                    continue
                cell = 'th' if number == 0 else 'td'
                table += '<tr>' + ''.join(f'<{cell}>{inline(s.strip())}</{cell}>' for s in row.strip('|').split('|')) + '</tr>'
            sections.append(table + '</table></div>')
        else:
            sections.append('<div class="block">' + escaped + '</div>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CLEM与MoE仿真边界</title><style>body{margin:40px auto;padding:0 24px;max-width:1060px;font:17px/1.8 system-ui;color:#243345;background:#fafbfc}h1{font-size:30px}h2{margin-top:40px;border-bottom:1px solid #ccd5df}a{color:#07689b}.block{white-space:pre-wrap;overflow-wrap:anywhere;margin:20px 0}.table{overflow:auto}table{border-collapse:collapse;font-size:15px}td,th{border:1px solid #ccd5df;padding:12px;min-width:140px;vertical-align:top}th{background:#eaf0f6}code{background:#edf1f5} @media print{body{font-size:12px;margin:0}}</style><main>' + '\n'.join(sections) + '</main></html>\n'
    (output / 'report.html').write_text(page)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    execute(args.run.resolve(), args.output.resolve())
