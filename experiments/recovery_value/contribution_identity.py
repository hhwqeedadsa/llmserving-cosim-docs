"""Finite symbolic check of cached owner partials across expert relocation.

Not a model of a supported DeepEP/EEP recovery API or floating-point execution.
"""
import argparse
from collections import Counter
from itertools import product
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    assert not a.output.exists()
    # Eight required logical contributions, two originally on each of four ranks.
    experts = [0, 1, 64, 65, 128, 129, 192, 193]
    old_owner = {e: e // 64 for e in experts}
    truth = Counter(experts)
    cases = []; witness = None
    for failed in range(4):
        missing = [e for e in experts if old_owner[e] == failed]
        alive = [o for o in range(4) if o != failed]
        cached = {o: [e for e in experts if old_owner[e] == o] for o in alive}
        for destinations in product(alive, repeat=len(missing)):
            new_owner = dict(old_owner)
            new_owner.update(zip(missing, destinations))
            touched = set(destinations)
            kept = [e for group in cached.values() for e in group]
            whole_new_owner = [e for e in experts if new_owner[e] in touched]
            naive = Counter(kept + whole_new_owner)
            selective = Counter(kept + missing)
            discard_and_replay = Counter([e for o, group in cached.items() if o not in touched for e in group] + whole_new_owner)
            assert selective == truth and discard_and_replay == truth
            assert naive != truth  # This toy touches every surviving owner before relocation.
            row = dict(failed_owner=failed, relocation={str(e): o for e, o in zip(missing, destinations)},
                       selective_recomputed_contributions=len(missing),
                       discard_touched_owner_recomputed_contributions=len(whole_new_owner),
                       naive_duplicate_contributions=sum((naive-truth).values()))
            cases.append(row)
            if witness is None:
                witness = dict(**row, cached_groups={str(k): v for k, v in cached.items()},
                               required_logical_experts=experts,
                               naive_contribution_multiplicity={str(k): v for k, v in sorted(naive.items())})
    result = dict(schema='moe-recovery-contribution-identity/1', cases=len(cases),
                  naive_physical_owner_only_failures=len(cases), missing_subset_safe_cases=len(cases),
                  discard_touched_owner_safe_cases=len(cases),
                  replay_work_histogram=dict(Counter(str(r['discard_touched_owner_recomputed_contributions']) for r in cases)),
                  witness=witness, cases_detail=cases,
                  interpretation='finite set/multiplicity model, no execution of actual library recovery',
                  minimum_identity='request/token/layer/round/input-and-weight-version plus logical contribution subset',
                  per_expert_output_cache_required=False, measured_latency=False)
    a.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('cases_detail', 'witness')}))


if __name__ == '__main__': main()
