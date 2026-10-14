"""Field ablation, causal reporting, explicit overflow and private input refusal."""
from copy import deepcopy
import json
from pathlib import Path
import builtins
import gzip
import io
import tempfile
import unittest
from unittest.mock import patch
from ..analysis.diagnostic_judge import classify,sample,judge
from ..analysis.diagnostic_schema import FIELDS,IX
from ..analysis.workflow import CONFIG,read

class DiagnosisTest(unittest.TestCase):
    def setUp(self):self.rule=read(CONFIG/'observation/diagnostic-rule.json')
    def row(self,**kw):
        x=[0]*len(FIELDS)
        for k,v in kw.items():x[IX[k]]=v
        return x

    def test_stronger_baselines_separate_service_from_load(self):
        slow=self.row(queue_peak_bytes=16384,waiting_ns=1000,queued_busy_ns=1000,
            tx_bytes=5000,completed_packet_bytes=5000,completed_serial_ns=400,probes=2)
        self.assertEqual(classify(slow,1000,400000000000,'queues',self.rule),['queue_cause_unknown'])
        self.assertEqual(classify(slow,1000,400000000000,'service',self.rule),['local_service_deficit'])
        busy=slow[:];busy[IX['completed_serial_ns']]=100
        self.assertEqual(classify(busy,1000,400000000000,'credit',self.rule),['busy_competition'])
        unmeasured=busy[:];unmeasured[IX['completed_packet_bytes']]=0;unmeasured[IX['completed_serial_ns']]=0
        self.assertEqual(classify(unmeasured,1000,400000000000,'credit',self.rule),['busy_service_rate_unknown'])
        unknown=busy[:];unknown[IX['queued_busy_ns']]=1
        self.assertEqual(classify(unknown,1000,400000000000,'credit',self.rule),['arrival_or_arbitration_or_credit_unknown'])
        slow[IX['blocked_probes']]=1
        self.assertEqual(classify(slow,1000,400000000000,'credit',self.rule),['local_service_deficit','credit_gate_witness_root_unknown'])

    def test_causal_trigger_context_and_common_cap(self):
        values=[self.row() for _ in range(100)]
        values[21]=self.row(blocked_probes=1)
        chosen,dropped=sample(values,10,'event',0,400000000000,self.rule)
        self.assertEqual(chosen,[(20,21),(21,21),(22,22)])
        changed=deepcopy(values);changed[80]=values[21]
        after,_=sample(changed,10,'event',0,400000000000,self.rule)
        self.assertEqual([r for r in after if r[1]<80],chosen)
        for kind in ('periodic','random','event'):
            reports,overflow=sample([values[21]]*100,10,kind,0,400000000000,self.rule)
            self.assertLessEqual(len(reports),10)
            self.assertTrue(all(a<=b for a,b in reports));self.assertGreaterEqual(overflow,0)

    def test_random_is_fixed_and_overflow_is_explicit(self):
        values=[self.row() for _ in range(100)]
        a=sample(values,10,'random',0,1,self.rule)
        self.assertEqual(a,sample(values,10,'random',0,1,self.rule))
        self.assertTrue(any(sample(values,10,'random',seed,1,self.rule)[1]>0 for seed in range(30)))

    def test_private_files_are_refused_before_reading(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'truth.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'allowlist'):
                judge(p,CONFIG/'observation/diagnostic-rule.json',p/'output')

    def test_fresh_cli_contract_reads_only_allowlist_and_handles_zero_reports(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d);p=base/'input';p.mkdir();out=base/'output'
            meta=dict(schema_version='issue-local-measurements/3',start_ns=0,end_ns=4000,window_ns=1000,
                fields=list(FIELDS),ports=[dict(switch_id=1,port_id=2,peer_node=3,peer_port=0,
                role='device_bound',plane=0,peer_group=['destination',3],rate_bps=400000000000)],
                rank_nodes=[dict(rank_id=0,node_id=3)],encoding='sparse',semantics='integrals',hardware_scope='test')
            (p/'schema.json').write_text(json.dumps(meta))
            (p/'queue_histograms.json').write_text(json.dumps(dict(period_ns=1024,phase_ns=0,
                ports=[dict(node_id=1,port_id=2,counts={'0':4})])))
            (p/'workload.json').write_text(json.dumps(dict(schema_version='issue-software-local-work/1',meaning='test',operations=[])))
            for name in ('windows','endpoint_ops','endpoint_stages'):
                with gzip.open(p/(name+'.jsonl.gz'),'wt'):pass
            opened=[];real_builtin=builtins.open;real_io=io.open
            def trace(real):
                def inner(path,mode='r',*a,**kw):
                    if isinstance(path,(str,Path)) and 'r' in mode:opened.append(Path(path).resolve())
                    return real(path,mode,*a,**kw)
                return inner
            rule=(CONFIG/'observation/diagnostic-rule.json').resolve()
            with patch('builtins.open',side_effect=trace(real_builtin)),patch('io.open',side_effect=trace(real_io)):
                judge(p,rule,out)
            from ..analysis import diagnostic_judge
            allowed={x.resolve() for x in p.iterdir()}|{rule,Path(diagnostic_judge.__file__).resolve()}
            self.assertTrue(set(opened)<=allowed,set(opened)-allowed)
            result=read(out/'summary.json')
            self.assertEqual(result['costs']['event-10us']['report_bytes'],0)
            self.assertTrue(all(n==0 for n in result['predictions'].values()))

if __name__=='__main__':unittest.main()
