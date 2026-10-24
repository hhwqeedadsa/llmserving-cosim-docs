"""Reanalyze saved networks and preserve the required four-rank failure."""
import argparse
from pathlib import Path
from fixtures.modern_native.audit import audit
from fixtures.modern_native.stage import load,save,sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();s=a.stage
    cases={label:audit(s/label) for label in ('synthetic-healthy','synthetic-fault')}
    assert cases['synthetic-healthy']['retransmitted_source_packets']==0
    assert cases['synthetic-fault']['retransmitted_source_packets']==3 and cases['synthetic-fault']['observed_out_of_order_data_packets']==2
    assert load(s/'synthetic-fault/result.json')['fault_drops']==1
    identity=load(s/'runtime-identity.json')
    assert all(sha(s/'runtime'/name)==h for name,h in identity['runtime'].items())
    for label in cases:
        libraries=[Path(p) for p in load(s/label/'result.json')['loaded_libraries'] if Path(p).name.startswith('libns3')]
        recorded_runtime=Path(load(s/label/'native-command.json')['command'][0]).parent
        assert len(libraries)==8 and all(p.parent==recorded_runtime/'lib' for p in libraries)
        assert all(sha(s/'runtime/lib'/p.name)==identity['runtime']['lib/'+p.name] for p in libraries)
    assert load(s/'checks.json')['passed'] and not load(s/'limitation.json')['four_request_full_call_passed']
    assert not a.output.exists();save(a.output,dict(verified=True,acceptance_complete=False,cases=cases,
        required_four_request_full_call='failed at unchanged 256 native control decisions; partial evidence retained',
        scope='verified functional subset and actual unmet requirement; overall stage/goal remain in progress'))


if __name__=='__main__':main()
