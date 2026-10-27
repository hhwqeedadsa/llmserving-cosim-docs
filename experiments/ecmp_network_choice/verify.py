"""Offline audit of all retained success/failure evidence; never retries networks."""
import argparse
from copy import deepcopy
from pathlib import Path
import shutil

from experiments.compact_network_choice.evaluate import modern_metrics
from experiments.shared_uplink_choice.evaluate import compare
from experiments.ecmp_baseline_audit.verify import native_check
from .audit import inspect
from .data import CONDITIONS, load, save, sha, runtime_check
from .predict import execute as predict


def frozen_check(stage,identity):
    assert all(sha(stage/n)==h for n,h in identity.items()),'frozen input/prediction changed'


def reject(label,function):
    try:function()
    except (AssertionError,ValueError,KeyError,IndexError):return label
    raise AssertionError('corruption accepted: '+label)


def verify(stage,output,observed_only):
    ledger=load(stage/'execution.json')
    assert ledger['closed'] and ledger['state']=='failure_preserved_no_remaining_networks'
    networks=[r for r in ledger['records'] if r['network']]
    assert [r['label'] for r in networks]==[f'batch4-{name}-{mode}' for name in CONDITIONS for mode in ('modern','generic')]
    assert all(r['exit_code']==0 for r in networks) and len(networks)==6
    assert ledger['records'][-1]['label']=='batch4-quad-compare' and ledger['records'][-1]['exit_code']==1
    assert all(r['exit_code']==0 for r in ledger['records'][:-1])
    assert not (stage/'truth/batch8').exists()
    identity=load(stage/'input-identity.json');frozen_check(stage,identity)
    preparation=load(stage/'preparation.json')
    assert all(sha(stage/n)==h for n,h in preparation['copied'].items())
    source=load(stage/'observed/source.json')
    for row in source['selected']:
        path=stage/'truth/routes'/row['name']
        assert sha(path)==row['sha256'] and load(path)[1]['3']==[row['choices']]
    for batch in (4,8):
        expected=[[] for _ in range(4)]
        for i,row in enumerate(source['selected'][:batch]):expected[i%4].append(row['choices'])
        assert expected==load(stage/f'observed/batch{batch}/input.json')['modern']['calls'][0]['tokens']
    runtime_check(stage)
    receipt=load(stage/'runtime-identity.json')
    expected_libraries={Path(n).name:h for n,h in receipt['files'].items() if n.startswith('lib/')}
    audits,library_receipts={},{}
    for name in CONDITIONS:
        root=stage/'truth/batch4'/name
        prediction=load(stage/'predictions/batch4.json')['predictions'][name]
        metrics=modern_metrics(root/'modern')
        assert metrics==load(root/'modern-check.json')['metrics']
        audit=inspect(root/'modern',prediction)
        assert audit==load(root/'modern-check.json')['audit']
        declaration=load(stage/'predictions/batch4-declaration.json')
        a,b=native_check(declaration,load(root/'modern/modern-plan.json'),load(root/'modern/initial.json'),load(root/'modern/modern-execution.json'))
        audits[name]=dict(audit=audit,native_connections=a,native_remote_operations=b)
        comparison=load(root/'comparison.json')
        assert comparison==compare(root,stage/'truth/batch4/single/modern')
        assert comparison['equivalent']==(name!='quad')
        if name=='quad':assert {k for k,v in comparison['checks'].items() if not v}=={'callbacks'}
        for mode in ('modern','generic'):
            libraries=[Path(n) for n in load(root/mode/'result.json')['loaded_libraries'] if Path(n).name.startswith('libns3.')]
            assert len(libraries)==len(expected_libraries)==8
            assert {p.name for p in libraries}==set(expected_libraries)
            original_stage=Path(ledger['records'][0]['cwd']).parent
            assert all(p.parent==original_stage/'runtime/lib' for p in libraries),'loaded library outside admitted runtime'
            # Recovery maps only the historical root; the package retains each actual loaded basename/hash.
            assert all(expected_libraries[p.name]==sha(stage/'runtime/lib'/p.name) for p in libraries)
            library_receipts[name+'-'+mode]={p.name:expected_libraries[p.name] for p in libraries}
    assert load(stage/'diagnosis.json')['passed'] and load(stage/'diagnosis.json')['strict_failure_preserved']
    observed_only.mkdir();shutil.copytree(stage/'observed',observed_only/'observed')
    assert not (observed_only/'truth').exists()
    predict(observed_only/'observed',observed_only/'predictions')
    assert {p.name for p in (stage/'predictions').iterdir()}=={p.name for p in (observed_only/'predictions').iterdir()}
    assert all(p.read_bytes()==(observed_only/'predictions'/p.name).read_bytes() for p in (stage/'predictions').iterdir())
    root=stage/'truth/batch4/quad/modern'
    prediction=load(stage/'predictions/batch4.json')['predictions']['quad']
    result=load(root/'result.json');corruptions=[]
    bad=deepcopy(result);bad['packet_capture']['omitted_events']=1
    corruptions.append(reject('capture omission',lambda:inspect(root,prediction,bad)))
    bad=deepcopy(result);next(row for row in bad['packet_capture']['events'] if row[3]==1)[0]+=1
    corruptions.append(reject('physical receive time',lambda:inspect(root,prediction,bad)))
    bad=deepcopy(result);bad['physical_nic_addressing']['bindings'][0]['nic']=2
    corruptions.append(reject('NIC binding/route',lambda:inspect(root,prediction,bad)))
    bad=deepcopy(result);bad['ports'][0]['model_tx_bytes']+=1
    corruptions.append(reject('port bytes',lambda:inspect(root,prediction,bad)))
    bad=deepcopy(result);bad['passed']=False
    corruptions.append(reject('incomplete execution',lambda:inspect(root,prediction,bad)))
    bad_prediction=deepcopy(prediction);bad_prediction['loads'][0]['framed_bytes']+=1
    corruptions.append(reject('projected demand',lambda:inspect(root,bad_prediction,result)))
    bad_identity=identity.copy();key=next(k for k in bad_identity if k.startswith('observed/source'))
    bad_identity[key]='0'*64
    corruptions.append(reject('source identity',lambda:frozen_check(stage,bad_identity)))
    bad_identity=identity.copy();key='predictions/batch4.json';bad_identity[key]='0'*64
    corruptions.append(reject('frozen decision',lambda:frozen_check(stage,bad_identity)))
    save(output,dict(passed=True,executed_networks=6,unexecuted_networks=6,batch8_networks=0,
        audits=audits,loaded_native_library_hashes=library_receipts,rejected_corruptions=corruptions,
        predictions_identical_without_truth=True,original_strict_failure_preserved=True,
        physical_TX=sum(v['audit']['physical_TX'] for v in audits.values()),
        physical_RX=sum(v['audit']['physical_RX'] for v in audits.values()),new_networks=0,
        all_modern_conditions_complete=True,all_generic_strict_comparisons_passed=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--observed-only',type=Path,required=True)
    a=p.parse_args();verify(a.stage.resolve(),a.output.resolve(),a.observed_only.resolve())
