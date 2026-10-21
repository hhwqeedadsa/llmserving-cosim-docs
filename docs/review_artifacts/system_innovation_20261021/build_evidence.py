#!/usr/bin/env python3
"""Freeze root-owned code excerpts and claim-specific public-source passages."""
from hashlib import sha256
from pathlib import Path
import json
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASELINE = '699312b3f25b496e1e6e25e7b9647a5597dc4751'
CODE = [
    ('C1', 'python/moe_ub_cosim/scaleout_service.py', 129, 181, 'Per-GPU-pair messages; all incoming dispatch dependencies precede one expert stage; all returns precede merge.'),
    ('C2', 'python/moe_ub_cosim/scaleout_service.py', 229, 255, 'All finite ready-KV requests are inserted at time zero.'),
    ('C3', 'python/moe_ub_cosim/scaleout_service.py', 257, 287, 'Current service requires the full configured batch and advances complete EP waves.'),
    ('C4', 'backend/rdma/fabric.cc', 195, 233, 'One QP per remote message; contiguous receive completion and ACK completion are distinct.'),
    ('C5', 'backend/rdma/astra_adapter.cc', 26, 52, 'Separate receive and send callbacks; SEND injects once, RECV waits.'),
    ('C6', 'backend/rdma/service.cc', 39, 73, 'EP group is schedulable only when all ranks and message callbacks finish; no-new-arrivals service.'),
    ('C7', 'backend/rdma/fabric.cc', 340, 383, 'Existing intra-server crossbar already reserves per-GPU directional service; no claimed NVLink protocol.'),
    ('C8', 'backend/send_state.hh', 8, 10, 'Send-state observation is already passive and change-driven.'),
    ('C9', 'backend/send_state.hh', 31, 39, 'Identical states are suppressed; no new polling-based observer needed.'),
    ('C10', 'backend/native_queue.hh', 134, 162, 'Existing internal queue/request resource snapshots include capacities and identities.'),
    ('C11', 'python/moe_ub_cosim/scaleout_replay.py', 57, 88, 'Static equal-expert placement and unique per-slot source files; route cloning is rejected.'),
    ('C12', 'backend/rdma/fabric.cc', 235, 273, 'Current packet hook stores aggregate bytes, first-packet path and optionally bounded queue events.'),
    ('C13', 'python/moe_ub_cosim/scaleout_service.py', 50, 56, 'Current Scale-Out compute schema explicitly requires declared_reference, not measured calibration.'),
    ('C14', 'python/moe_ub_cosim/operator_profile.py', 9, 33, 'UB has an existing measured-operator exact-shape profile interface with device, software and measurement provenance.'),
]
PASSAGES = {
    'lina': ['Lina tackles the design challenge', 'Lina schedules an allreduce micro-op'],
    'comet': ['To this end, we present Comet', '3.2 Adaptive Workload Assignment'],
    'analytical_overlap': ['We present a wave-quantized analytical model', 'not to complete online-serving or decode throughput.', 'NanoFlow jointly selects'],
    'patterns': ['prefill-aware expert placement algorithm', '24,000'],
    'megascale_infer': ['We present MegaScale-Infer', 'ping-pong pipeline'],
    'llmservingsim2': ['This paper presents LLMServingSim 2.0', 'During runtime, the simulator models synchronization'],
    'simai': ['[2025/12]', '[2026/09]', 'GPU memory modeling for inference simulation'],
    'clem': ['SimAI-CLEM runs the vanilla CCL directly'],
    'vidur': ['Capacity planning', 'initial profiling phase'],
    'tutel': ['a2a_ffn_overlap_degree', 'Add 2DH option'],
    'deepep_v121': ['set_num_sms', 'low-latency'],
    'deepep_v2': ['Analytical SM & QP count calculation', 'EP dispatch and combine require GPU SMs'],
    'coz': ['Causal profiling measures optimization potential', 'It predicts what the impact'],
    'nanoflow': ['NanoFlow automatically identifies'],
    'aalo_paper': ['D-CLAS', 'without prior knowledge'],
}


def main():
    revision = BASELINE
    code = []
    for ident, rel, first, last, claim in CODE:
        path = ROOT / rel; raw = path.read_bytes(); lines = raw.decode().splitlines()
        assert raw == subprocess.check_output(['git', 'show', revision+':'+rel], cwd=ROOT), 'Reviewed code changed: '+rel
        assert 0 < first <= last <= len(lines)
        code.append(dict(id=ident, path=rel, start_line=first, end_line=last,
                         file_sha256=sha256(raw).hexdigest(), claim=claim,
                         excerpt='\n'.join(lines[first-1:last])))
    (HERE / 'code_evidence.json').write_text(json.dumps(dict(baseline_commit=revision,
        scope='Root-owned code read only; no platform behavior changed', snippets=code), ensure_ascii=False, indent=2)+'\n')
    sources = json.loads((HERE / 'sources.json').read_text())['sources']
    quotes = []
    for row in sources:
        if row['status'] != 200: continue
        plain = ROOT / row['text_snapshot']; text = plain.read_text(); matches = []
        for phrase in PASSAGES[row['id']]:
            at = text.casefold().find(phrase.casefold())
            assert at >= 0, (row['id'], phrase)
            begin = max(0, at-100); end = min(len(text), at+1100)
            matches.append(dict(search=phrase, start_character=begin, end_character=end, excerpt=text[begin:end]))
        quotes.append(dict(id=row['id'], url=row.get('public_url', row['url']), text_sha256=row['text_sha256'], passages=matches))
    (HERE / 'prior_art_passages.json').write_text(json.dumps(quotes, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(code_snippets=len(code), public_documents=len(quotes), baseline=revision), ensure_ascii=False))


if __name__ == '__main__': main()
