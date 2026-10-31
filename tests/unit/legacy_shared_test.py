from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from moe_ub_cosim.legacy_shared import aggregate,run
from moe_ub_cosim.legacy_native import validate_native_inputs

ROOT=Path(__file__).resolve().parents[2]


class SharedLegacyInput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.input=cls.root/'input'
        subprocess.run([sys.executable,'-B',str(ROOT/'examples/legacy_shared/prepare.py'),
            '--reference-events','200000','--output',str(cls.input)],cwd=ROOT,check=True,capture_output=True,
            env=dict(os.environ,PYTHONPATH=str(ROOT/'python')))
        cls.backend=cls.root/'backend';cls.backend.write_text('sentinel: must never run');cls.backend.chmod(0o700)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def invoke(self,name, *, config=None,protocol=None,transport=None):
        return run(config or self.input/'shared.json',self.input/'topology',protocol or self.input/'normal.json',
                   transport or self.input/'transport.json',self.backend,self.root/name,validate_only=True)

    def source(self,name):
        value=json.loads((self.input/'shared.json').read_bytes())
        for row in value['groups']:
            for key in ('plan','binding'):row[key]=str((self.input/row[key]).resolve())
        return self.root/(name+'.json'),value

    def test_two_complete_groups_keep_local_shape_without_native_dispatch(self):
        with patch('moe_ub_cosim.legacy_shared.monitored',side_effect=AssertionError('network launch')):
            result=self.invoke('valid')
        self.assertTrue(result['passed'] and result['validation_only'])
        self.assertFalse(result['native_network_executed'] or result['native_admission_executed'])
        work=json.loads((self.root/'valid/execution.json').read_bytes())
        self.assertEqual([(g['execution']['ranks'],g['execution']['experts']) for g in work['groups']],[(2,4),(2,4)])
        self.assertEqual([g['stream_offset'] for g in result['groups']],[0,8])
        self.assertEqual(result['operations'],44)
        self.assertFalse((self.root/'valid/result.json').exists())
        self.assertNotIn('command',json.loads((self.root/'valid/inputs.json').read_bytes()))

    def test_physical_overlap_and_duplicate_group_ids_are_rejected(self):
        for mode in ('overlap','identity'):
            path,source=self.source(mode)
            if mode=='identity':source['groups'][1]['id']=source['groups'][0]['id']
            else:source['groups'][1]['binding']=source['groups'][0]['binding']
            path.write_text(json.dumps(source))
            with self.assertRaises(ValueError),patch('moe_ub_cosim.legacy_shared.monitored') as native:
                self.invoke(mode,config=path)
            native.assert_not_called()

    def test_shared_capacity_does_not_raise_original_entrypoint_capacity(self):
        from moe_ub_cosim.scaleout_rdma import checked_json
        topology=checked_json(self.input/'topology/topology.json',8*2**20)
        protocol=checked_json(self.input/'normal.json',65536)
        binding=checked_json(self.input/'group-0/binding.json',65536)
        transport=checked_json(self.input/'transport.json',65536)
        with self.assertRaises(ValueError):validate_native_inputs(topology,protocol,binding,transport)
        validate_native_inputs(topology,protocol,binding,transport,reference_event_cap=200000)
        transport['dcqcn_reference']['max_events']=200001
        with self.assertRaises(ValueError):validate_native_inputs(topology,protocol,binding,transport,reference_event_cap=200000)

    def test_fault_must_belong_to_declared_group(self):
        protocol=json.loads((self.input/'fault.json').read_bytes());protocol['faults'][0]['node']=68
        path=self.root/'bad-fault.json';path.write_text(json.dumps(protocol))
        with self.assertRaisesRegex(ValueError,'owning group'),patch('moe_ub_cosim.legacy_shared.monitored') as native:
            self.invoke('bad-fault',protocol=path)
        native.assert_not_called()

    def test_source_plan_tampering_rejected(self):
        path,source=self.source('tampered');folder=self.root/'tampered-plan';folder.mkdir()
        import shutil
        shutil.copytree(self.input/'group-0/plan',folder,dirs_exist_ok=True)
        with (folder/'operations.jsonl').open('a') as stream:stream.write('[]\n')
        source['groups'][0]['plan']=str(folder);path.write_text(json.dumps(source))
        with self.assertRaises(ValueError),patch('moe_ub_cosim.legacy_shared.monitored') as native:
            self.invoke('tampered',config=path)
        native.assert_not_called()

    def test_aggregate_budget_rejects_whole_job(self):
        work=dict(rank_to_gpu=[0,64],operations=[0],events=[0],limits=dict(streams=8,work=40000))
        other=deepcopy(work);other['rank_to_gpu']=[4,68]
        with self.assertRaisesRegex(ValueError,'aggregate lifetime'):
            aggregate([dict(id='a',execution=work),dict(id='b',execution=other)],{})


if __name__=='__main__':unittest.main()
