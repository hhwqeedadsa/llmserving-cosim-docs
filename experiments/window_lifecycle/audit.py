"""Build exact upper lifecycle functions; preserve explicit CPU substitutes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

LOCAL = Path(__file__).resolve().parent
ROOT = LOCAL.parents[1]
sys.path.insert(0, str(ROOT / 'experiments/target_retirement_contract'))
from extract import function

FUNCTIONS = [('nccl/src/gin/gin_host.cc', 'ncclGinDeregister'),
             ('nccl/src/dev_runtime.cc', 'symMemoryDropRef'),
             ('nccl/src/dev_runtime.cc', 'symWindowDestroy'),
             ('nccl/src/dev_runtime.cc', 'ncclCommWindowDeregister')]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def build(inputs, output):
    output.mkdir()
    chunks = [(LOCAL / 'harness.hpp').read_text()];selected = []
    for path, name in FUNCTIONS:
        entry = function((inputs / 'sources' / path).read_text(), name)
        chunks.append(entry['text'])
        selected.append(dict(source=path, name=name, line=entry['line'], end_line=entry['end_line'],
                             sha256=sha(entry['text'].encode())))
    chunks.append((LOCAL / 'harness_main.cc').read_text())
    generated = output / 'selected.cc';generated.write_text('\n\n'.join(chunks))
    command = ['g++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror', str(generated), '-o', str(output / 'source-harness')]
    save(output / 'build.json', dict(command=command, cwd=str(Path.cwd()), functions=selected,
         generated_sha256=sha(generated.read_bytes()), source_execution='four exact original functions',
         substitutes=['CUDA calls and shadow metadata', 'provider outcomes per handle',
                      'memory ownership, unmaps and free recording', 'registry/list initial state'],
         provider_or_gpu_executed=False))
    subprocess.run(command, check=True)


def evidence(inputs, output):
    rows = []
    def add(path, name, start, stop, required):
        text = (inputs / 'sources' / path).read_text();lines = text.splitlines()
        begin = text.index(start)
        finish = text.index(stop, begin) + len(stop)
        first = text.count('\n', 0, begin) + 1;last = text.count('\n', 0, finish) + 1
        excerpt = '\n'.join(lines[first - 1:last])
        assert all(x in excerpt for x in required), name
        rows.append(dict(name=name, source=path, line=first, end_line=last,
                         sha256=sha(excerpt.encode()), required=required))
    add('deepep/csrc/buffers/ep.hpp', 'deepep_destroy_guard', '    void destroy() override {',
        '        destroyed = true;', ['comm::barrier', 'true, true', 'context->finalize()'])
    add('deepep/csrc/comm/barrier.hpp', 'host_barrier_syncs_before_and_after', 'static void barrier(',
        '\n}\n', ['with_cpu_sync', 'cudaDeviceSynchronize()', 'launch_barrier'])
    add('deepep/deep_ep/include/deep_ep/comm/barrier.cuh', 'all_context_flush_and_peer_wait',
        '__forceinline__ __device__ void gin_barrier_wo_local_sync(', '\n}\n',
        ['nccl_dev_comm.ginContextCount', '.flush(ncclCoopWarp())', 'barrier_gin.signal', 'barrier_gin.wait_signal'])
    add('deepep/deep_ep/include/deep_ep/impls/comm/barrier.cuh', 'sequential_scaleout_then_scaleup',
        'barrier_impl(', '\n}\n', ['if constexpr (kSequential)', 'Scaleout barrier', 'Scaleup barrier', 'kFlushAllAllocatedQPs'])
    add('deepep/csrc/kernels/comm/context.cpp', 'context_finalize_order', 'void Context::finalize()',
        '    finalized = true;', ['storage_window', 'ncclCommWindowDeregister(comm, window)', 'ncclDevCommDestroy'])
    add('nccl/src/dev_runtime.cc', 'backing_reuse_global_agreement', '  // Look for an existing registration',
        '  // New memory.', ['m->primaryAddr != memAddr', 'm->size != size', 'm->winFlags != winFlags',
                              'candidateRegistryId', 'bootstrapAllGather', 'goto exit;'])
    add('nccl/src/dev_runtime.cc', 'window_registration_backing_and_barrier',
        'ncclResult_t ncclDevrWindowRegisterInGroup(', '\nfail_locReg_memHandle_mem_stream_win:',
        ['ncclCuMemGetAddressRange', 'memOffset', 'symMemoryObtain', 'symWindowCreate', 'bootstrapBarrier'])
    add('nccl/src/dev_runtime.cc', 'last_reference_and_ignored_lower_results',
        'static void symMemoryDropRef(', '\n}\n', ['0 == --mem->refCount', 'ncclGinDeregister(', 'free(mem)'])
    add('nccl/src/dev_runtime.cc', 'window_destroy_drops_backing_first', 'static ncclResult_t symWindowDestroy(struct ncclComm* comm, struct ncclWindow_vidmem* winDev, cudaStream_t stream) {',
        '\n}\n', ['symMemoryDropRef', 'ncclShadowPoolFree', 'ncclCommDeregister', 'free(winHost)'])
    add('nccl/src/dev_runtime.cc', 'public_deregister_local_stream_only',
        'ncclResult_t ncclCommWindowDeregister(struct ncclComm* comm', '\n}\n',
        ['cudaStreamNonBlocking', 'symWindowDestroy', 'cudaStreamSynchronize(stream)', 'return ret;'])
    add('nccl/src/dev_runtime.cc', 'registration_partial_segment_cleanup', 'static ncclResult_t symMemoryRegisterGin(',
        '\n}\n', ['numSegmentsRegistered++', 'i < numSegmentsRegistered', 'ncclGinDeregister', 'mem->ginSegmentInfos = nullptr'])
    add('nccl/src/dev_runtime.cc', 'finalize_remaining_window_cleanup', 'ncclResult_t ncclDevrFinalize(',
        '\n}\n', ['During abort', 'while (devr->winSortedCount > 0)', 'NCCLCHECKIGNORE(symWindowDestroy'])
    add('nccl/src/dev_runtime.cc', 'windows_copy_shared_backing_gin_handles',
        'static ncclResult_t symWindowInitGin(', '\n}\n',
        ['winDevHost->ginWinsDefaultBackend[i] = mem->ginDevWins[i]', 'mem->numGinSegments'])
    add('nccl/src/dev_runtime.cc', 'window_subrange_is_offset_into_backing',
        'static ncclResult_t symWindowCreate(', '\n}\n',
        ['win->memory = mem', 'win->size = userSize', 'winDevHost->ginOffset4K = memOffset >> 12'])
    save(output, rows)


def analyze(records, output):
    rows = [json.loads(line) for line in records.read_text().splitlines()]
    groups = dict(public_success_without_mr_calls=[], public_success_with_lower_error=[],
                  public_success_with_unretired_channels=[], public_success_all_retired_but_lower_error=[],
                  public_error_after_all_mrs_retired=[], public_success_ignores_sync_error=[])
    for index, row in enumerate(rows):
        count = row['segments'] * row['connections']
        if row['public_result'] == 0:
            if row['refs'] > 1 and row['mode'] == 'success' and not row['mr_calls']:
                groups['public_success_without_mr_calls'].append(index)
            if row['ignored_provider_results']:
                groups['public_success_with_lower_error'].append(index)
                key = 'public_success_with_unretired_channels' if len(row['retired']) < count else 'public_success_all_retired_but_lower_error'
                groups[key].append(index)
            if row['mode'] == 'stream_sync_error':
                groups['public_success_ignores_sync_error'].append(index)
        elif len(row['retired']) == count:
            groups['public_error_after_all_mrs_retired'].append(index)
    assert all(groups.values())
    summary = dict(schema_version='window-lifecycle-analysis/1', cases=len(rows),
                   records_sha256=sha(records.read_bytes()), counts={k: len(v) for k, v in groups.items()},
                   witness_rows={k: v[0] for k, v in groups.items()},
                   source_functions=4, real_provider_executed=False, new_ns3_networks=0,
                   legal_failure_concurrency_proven=False, production_vulnerability_proven=False,
                   performance_benefit_proven=False, paper_innovation_established=False)
    save(output, summary);print(json.dumps(summary))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('build', 'evidence', 'analyze'))
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--records', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'build':
        build(args.inputs, args.output)
    elif args.action == 'evidence':
        evidence(args.inputs, args.output)
    else:
        analyze(args.records, args.output)
