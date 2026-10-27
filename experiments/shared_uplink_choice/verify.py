"""Independent route intake, multiset enumeration, truth exclusion and corruptions."""
import argparse
from collections import Counter
from copy import deepcopy
from itertools import permutations
from pathlib import Path
import shutil
from experiments.shared_uplink_choice.prepare import CONDITIONS, NAMES, load, save, sha
from experiments.shared_uplink_choice.predict import combine_context, execute
from experiments.shared_uplink_choice.audit import inspect, need


def input_checks(observed):
    source=load(observed/'source.json')
    need(tuple(r['name'].removesuffix('.json') for r in source['selected'])==NAMES,'selection changed')
    for row in source['selected']:
        path=observed/'routes'/row['name']
        need(sha(path)==row['sha256'],'source hash mismatch')
        raw=load(path)
        need(set(raw[1])=={str(i) for i in range(61)} and all(raw[1][str(i)] is None for i in range(3)),
             'incomplete source layers or unexpected dense-layer selections')
        need(raw[1]['3']==[row['choices']],'source choices changed')
    permutations_checked=0
    for batch in (4,8):
        cfg=load(observed/f'batch{batch}/input.json')
        tokens=[[] for _ in range(4)]
        for i,row in enumerate(source['selected'][:batch]):tokens[i%4].append(row['choices'])
        need(tokens==cfg['modern']['calls'][0]['tokens'],'source rank mapping changed')
        ops=load(observed/f'batch{batch}/plan/plan.json')['operations']
        # Independently enumerate all possible actual token-slot bijections per group.
        groups={}
        for op in ops:
            if op['phase']=='combine' and op['kind']=='token_data' and op['path']=='rdma':
                groups.setdefault((op['src'],op['dst']),[]).append(op)
        x=cfg['modern']['execution']
        for group in groups.values():
            reference=None
            for perm in permutations(range(len(group))):
                counts=Counter()
                for op,slot in zip(group,perm):
                    # Explicit channel allocation table for the fixed 2SM/2warp/5context experiment.
                    b=op['binding']
                    width=(b['owner_received_tokens']+3)//4
                    channel=(b['source_rank_prefix']+slot)//width
                    context=(0,1,2,3)[channel]
                    need(combine_context(b,slot,x)==context,'context arithmetic disagrees with independent table')
                    counts[context%2]+=op['operand_bytes']+32
                if reference is None:reference=counts
                need(counts==reference,'aggregate NIC demand depends on token-slot order')
                permutations_checked+=1
    return dict(raw_requests=8,selected_tokens_by_batch=[4,8],slot_permutations_checked=permutations_checked)


def verify(stage):
    ledger=load(stage/'execution.json')
    need(ledger['closed'] and ledger['state']=='completed','networks not fully completed')
    need(sum(r['network'] for r in ledger['records'])==12,'network count changed')
    for name,digest in load(stage/'prediction-identity.json').items():need(sha(stage/name)==digest,'prediction modified after freezing')
    provenance=input_checks(stage/'observed')
    audits={}
    for batch in (4,8):
        predictions=load(stage/f'predictions/batch{batch}.json')['predictions']
        for name in CONDITIONS:
            root=stage/'truth'/f'batch{batch}'/name
            need(load(root/'comparison.json')['equivalent'],'full generic graph not equivalent')
            audits[f'batch{batch}-{name}']=inspect(root/'modern',predictions[name])
    check=stage/'observed-only-check'
    check.mkdir()
    shutil.copytree(stage/'observed',check/'observed')
    need(not (check/'truth').exists(),'truth accidentally copied')
    execute(check/'observed',check/'predictions')
    need(all((stage/f'predictions/batch{b}.json').read_bytes()==(check/f'predictions/batch{b}.json').read_bytes() for b in (4,8)),
         'predictions changed without truth')
    directory=stage/'truth/batch8/baseline/modern'
    prediction=load(stage/'predictions/batch8.json')['predictions']['baseline']
    original=load(directory/'result.json')
    corruptions=[]
    def reject(label,function):
        try:function()
        except (AssertionError,ValueError,KeyError,IndexError):corruptions.append(label)
        else:raise ValueError('corruption accepted: '+label)
    bad=deepcopy(original)
    bad['packet_capture']['omitted_events']=1
    reject('capture omission',lambda:inspect(directory,prediction,bad))
    bad=deepcopy(original)
    next(r for r in bad['packet_capture']['events'] if r[3]==1)[0]+=1
    reject('physical receive time',lambda:inspect(directory,prediction,bad))
    bad=deepcopy(original)
    bad['ports'][0]['model_tx_bytes']+=1
    reject('port accounting',lambda:inspect(directory,prediction,bad))
    bad=deepcopy(original)
    bad['passed']=False
    reject('incomplete execution',lambda:inspect(directory,prediction,bad))
    bad_prediction=deepcopy(prediction)
    bad_prediction['loads'][0]['framed_bytes']+=1
    reject('per-link demand',lambda:inspect(directory,bad_prediction,original))
    bad_prediction=deepcopy(prediction)
    bad_prediction['messages'][0]['bytes']+=1
    reject('NIC/slot multiset demand',lambda:inspect(directory,bad_prediction,original))
    source=load(check/'observed/source.json')
    source['selected'][0]['sha256']='0'*64
    save(check/'observed/source.json',source)
    reject('source hash',lambda:input_checks(check/'observed'))
    save(check/'observed/source.json',load(stage/'observed/source.json'))
    return dict(passed=True,input=provenance,audits=audits,rejected_corruptions=corruptions,
        predictions_identical_without_truth=True,new_networks=0,hardware_calibrated=False)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,help='New verification receipt; defaults to stage/verification.json')
    a=p.parse_args()
    save(a.output or a.stage/'verification.json',verify(a.stage.resolve()))


if __name__=='__main__':main()
