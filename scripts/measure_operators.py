"""Measure explicit operator shapes on one CUDA GPU; never load model weights."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', type=int, default=0)
    parser.add_argument('--warmup', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=11)
    parser.add_argument('--inner-repeats', type=int, default=10)
    args = parser.parse_args()
    assert not args.output.exists() and args.warmup >= 1 and 3 <= args.repeats <= 10000 and args.inner_repeats >= 1
    spec = json.loads(args.input.read_text())
    assert set(spec) == {'schema_version', 'domain', 'shapes', 'seed', 'max_tensor_bytes'}
    assert spec['schema_version'] == 'moe-ub-operator-measurement-input/1'
    # Reuse only the shape/domain contract. Runtime measurements are never
    # synthesized from the functional test's duration values.
    from moe_ub_cosim.operator_profile import validate_profile
    d = spec['domain']; rows = [dict(**s, duration_ns=1, samples_ns=[1,1,1]) for s in spec['shapes']]
    method = dict(implementation='torch_eager_serial_gated_ffn_and_sdpa_v1',
                  statistic='ceil_median_ns', out_of_domain='reject')
    validate_profile(dict(schema_version='moe-ub-operator-profile/1', basis='synthetic_test',
        domain=d, rows=rows, device={'kind':'preflight'}, software={'kind':'preflight'}, measurement=method),
        dict(hidden_dim=d['hidden_dim'], compute_dtypes={'compute':d['dtype'],'kv':d['dtype']}))
    assert type(spec['max_tensor_bytes']) is int and 0 < spec['max_tensor_bytes'] <= 2**33
    assert type(spec['seed']) is int and 0 <= spec['seed'] < 2**32
    import torch
    import torch.nn.functional as F
    from torch.nn.attention import SDPBackend, sdpa_kernel
    assert torch.cuda.is_available(), 'CUDA GPU is required; no CPU substitution'
    torch.cuda.set_device(args.device)
    device = torch.device('cuda', args.device)
    dtype = getattr(torch, d['dtype'])
    torch.manual_seed(spec['seed']); torch.cuda.manual_seed_all(spec['seed'])
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction = False
    torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction = False
    driver = subprocess.run(['nvidia-smi','--query-gpu=driver_version','--format=csv,noheader'],
                            capture_output=True,text=True,check=True).stdout.splitlines()[0].strip()
    started = datetime.now(timezone.utc).isoformat()
    measured = []
    with torch.inference_mode(), sdpa_kernel(SDPBackend.MATH):
        for shape in spec['shapes']:
            values = shape['shape']; tensors = []
            h, f = d['hidden_dim'], d['intermediate_dim']
            if shape['operator'] == 'expert_ffn':
                estimate = sum((3*h*f + n*h + 3*n*f + n*h)*2 for n in values if n)
                assert estimate <= spec['max_tensor_bytes'], 'Operator exceeds declared tensor budget'
                for n in values:
                    if n:
                        tensors.append(tuple(torch.randn(size,device=device,dtype=dtype) for size in
                            ((n,h),(h,f),(h,f),(f,h))))
                        for weight in tensors[-1][1:]: weight.mul_(1/math.sqrt(weight.shape[0]))
                def operation():
                    for x, gate, up, down in tensors:
                        result = (F.silu(x @ gate) * (x @ up)) @ down
                    return result
            else:
                q, k, dim = d['num_heads'], d['num_kv_heads'], d['head_dim']
                estimate = sum((2*q*dim + 2*k*c*dim + 4*q*c*dim + 4*q*c)*2 for c in values)
                assert estimate <= spec['max_tensor_bytes'], 'Attention exceeds declared tensor budget'
                for context in values:
                    tensors.append(tuple(torch.randn(size,device=device,dtype=dtype) for size in
                        ((1,q,1,dim),(1,k,context,dim),(1,k,context,dim))))
                def operation():
                    for query, key, value in tensors:
                        result = F.scaled_dot_product_attention(query,key,value,dropout_p=0.0,
                            is_causal=False,enable_gqa=True)
                    return result
            for _ in range(args.warmup): result = operation()
            torch.cuda.synchronize(device)
            assert torch.isfinite(result).all().item(), 'Nonfinite operator output'
            samples = []
            for _ in range(args.repeats):
                start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
                start.record()
                for _ in range(args.inner_repeats): result = operation()
                end.record(); end.synchronize()
                samples.append(math.ceil(start.elapsed_time(end)*1_000_000/args.inner_repeats))
            measured.append(dict(**shape,samples_ns=samples,duration_ns=math.ceil(statistics.median(samples))))
            print(shape, measured[-1]['duration_ns'], 'ns', flush=True)
            del result, tensors
    method.update(timer='cuda_event',weights='seeded_random_operator_tensors',
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
        warmup=args.warmup,repeats=args.repeats,inner_repeats=args.inner_repeats,seed=spec['seed'],
        weight_initialization='normal_scaled_by_inverse_sqrt_fan_in',
        attention_kernel='sdpa_math_gqa_one_query_per_request_serial',expert_kernel='three_matmuls_silu_multiply_per_nonempty_expert_serial',
        includes=['device_execution','eager_dispatch_gaps','allocator_reuse'],
        excludes=['model_weights_loading','routing','network_wait','quantization','distributed_synchronization'],
        started_utc=started,ended_utc=datetime.now(timezone.utc).isoformat())
    output = dict(schema_version='moe-ub-operator-profile/1',basis='measured_operator',domain=d,rows=measured,
        device=dict(name=torch.cuda.get_device_name(device),compute_capability=list(torch.cuda.get_device_capability(device)),
                    driver_version=driver),
        software=dict(torch=torch.__version__,cuda=torch.version.cuda,python=platform.python_version()),measurement=method)
    validate_profile(output,dict(hidden_dim=h,compute_dtypes={'compute':d['dtype'],'kv':d['dtype']}))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(output,indent=2)+'\n')


if __name__ == '__main__': main()
