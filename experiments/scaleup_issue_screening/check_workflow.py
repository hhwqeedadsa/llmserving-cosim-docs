"""Small independent checks for experiment inputs and lossless storage."""
import io
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from .archive import inventory, pack, restore, verify
from .workflow import CONFIG, ROOT, read, save
from .analyze import compute_relations, distribution, operation_stats
from .compare import compare_initial
from .controls import apply as apply_control
from copy import deepcopy
from moe_ub_cosim.fabric import validate as validate_fabric
from moe_ub_cosim.observation import validate_observation
from moe_ub_cosim.route_evidence import validate_manifest


class Checks(unittest.TestCase):
    def test_one_factor_controls_preserve_workload_and_untargeted_layers(self):
        source = read(CONFIG/'source-base.json')
        layers = [dict(deepcopy(read(CONFIG/'layer-template.json')),layer_id=i) for i in range(3,61)]
        case = dict(case_id='normal',first_layer=6,layer_count=3)
        base = dict(schema_version='issue-screening-control/1',case_id='control-half',baseline_case='normal',
            parameter='routed_expert_per_input_ns',reference=100,value=50)
        changed, child = apply_control(source,layers,case,base)
        expected = deepcopy(source)
        for i in (6,7,8):
            expected['service']['groups'][0]['full_model_profile']['layers'][i]['routed_expert_per_input_ns']=50
        self.assertEqual(changed,expected)
        expected_layers=deepcopy(layers)
        for layer in expected_layers:
            if layer['layer_id'] in (6,7,8):layer['compute_profile_ns']['expert_per_input']=50
        self.assertEqual(child,expected_layers)
        self.assertEqual(source,read(CONFIG/'source-base.json'))
        fence=dict(base,case_id='control-fence',parameter='copy_fence',reference='data_ready',value='local_read_complete')
        changed, child=apply_control(source,layers,case,fence)
        self.assertEqual(changed,source)
        self.assertEqual([l['cann_profile']['copy_fence'] for l in child],
            ['local_read_complete' if i in (6,7,8) else 'data_ready' for i in range(3,61)])
        for invalid in (dict(base,reference=99),dict(base,baseline_case='different'),dict(base,value=True),dict(base,parameter='link_rate')):
            with self.assertRaises(ValueError):apply_control(source,layers,case,invalid)

    def test_initial_state_comparison_rejects_identity_and_coverage_changes(self):
        short = {(7,kind,rank):dict(workload={'requests':1},relative_entry_ns=rank*10,
            duration_ns=20,relative_finish_ns=20+rank*10) for kind in ('dispatch','combine') for rank in (0,1)}
        long = {key:dict(value) for key,value in short.items()}
        hashes={'7':'same-route-context-placement-digest'}
        result=compare_initial(short,long,hashes,hashes,[7])
        self.assertEqual(result[0]['long_minus_short_ns']['relative_entry_ns']['maximum'],0)
        long[7,'dispatch',1]['relative_entry_ns']+=7
        self.assertEqual(compare_initial(short,long,hashes,hashes,[7])[0]['long_minus_short_ns']['relative_entry_ns']['maximum'],7)
        with self.assertRaises(ValueError):compare_initial(short,long,hashes,{'7':'different'},[7])
        del long[7,'combine',1]
        with self.assertRaises(ValueError):compare_initial(short,long,hashes,hashes,[7])

    def test_scope(self):
        observation = read(CONFIG/'observation.json')
        _, ports = validate_observation(observation, list(range(128)), 10000000)
        selected = {(p['switch_id'], p['port_id']) for p in ports}
        self.assertEqual(len(selected), 2688)
        # All static shortest-path candidates, independently derived from shape.
        expected = {(768+7*s+p, q) for s in range(8) for p in range(7) for q in range(32)}
        expected |= {(1104+16*p+h, s) for p in range(7) for h in range(16) for s in range(8)}
        self.assertEqual(selected, expected)
        self.assertEqual(validate_fabric(read(CONFIG/'fabric.json'))['active_endpoints'], 128)
        manifest = validate_manifest(read(CONFIG/'route-manifest.json'))
        self.assertEqual(len({row['file']['sha256'] for row in manifest['files']}), 1024)
        batch = read(CONFIG/'batch.json')
        self.assertEqual([(c['requests'], c['first_layer'], c['layer_count']) for c in batch['cases']],
                         [(256,6,3),(512,6,3),(1024,6,3),(1024,3,6)])
        self.assertTrue(all(set(batch['analyzed_layers']) <= set(range(c['first_layer'], c['first_layer']+c['layer_count'])) for c in batch['cases']))

    def test_archive_roundtrip_and_refusals(self):
        parent = ROOT/'runs/scaleup_issue_screening'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='check-', dir=parent) as temp:
            temp = Path(temp)
            case = temp/'case'
            save(case/'case.json', dict(owned_by='scaleup_issue_screening', schema_version='issue-screening-case/1'))
            (case/'nested').mkdir()
            (case/'nested/data.bin').write_bytes(bytes(range(256))*4096)
            os.link(case/'nested/data.bin', case/'linked.bin')
            before = inventory(case)
            archive = temp/'case.tar.gz'
            result = pack(case, archive, release=True)
            self.assertTrue(result['expanded_released'])
            self.assertFalse(case.exists())
            restored = temp/'restored'
            # The payload has two names but occupies one inode. The budget
            # fits the real restoration and metadata, not two payload copies.
            from moe_ub_cosim.host_probe import disk_usage
            footprint = verify(archive, before)
            existing = max(disk_usage(temp).values())
            self.assertEqual(footprint['hardlinks'],1)
            self.assertLess(footprint['restore_reserve_bytes'],result['logical_bytes'])
            with patch('experiments.scaleup_issue_screening.archive.host',return_value=dict(
                    artifact_roots=[str(temp)],artifact_bytes=existing+footprint['restore_reserve_bytes']-1,min_free_bytes=0)):
                with self.assertRaisesRegex(ValueError,'disk budget'):
                    restore(archive, restored)
            self.assertFalse(restored.exists())
            with patch('experiments.scaleup_issue_screening.archive.host',return_value=dict(
                    artifact_roots=[str(temp)],artifact_bytes=existing+footprint['restore_reserve_bytes'],min_free_bytes=0)):
                restore(archive, restored)
            self.assertEqual(inventory(restored), before)
            self.assertEqual((restored/'linked.bin').stat().st_ino, (restored/'nested/data.bin').stat().st_ino)
            with self.assertRaises(ValueError):
                restore(archive, restored)
            (restored/'symlink').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError):
                pack(restored, temp/'unsafe.tar.gz', release=True)
            self.assertTrue((restored/'case.json').exists())
            with archive.open('ab') as stream:
                stream.write(b'tamper')
            with self.assertRaises(ValueError):
                restore(archive, temp/'tampered')
            self.assertFalse((temp/'tampered').exists())

    def test_archive_rejects_traversal_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'bad.tar.gz'
            for names in [['../escape'], ['a', 'a']]:
                with tarfile.open(path, 'w:gz') as archive:
                    for name in names:
                        member = tarfile.TarInfo(name)
                        member.size = 1
                        archive.addfile(member, io.BytesIO(b'x'))
                with self.assertRaises(ValueError):
                    verify(path, {'a':dict(bytes=1, sha256=hashlib.sha256(b'x').hexdigest())})

    def test_entry_duration_and_finish_are_distinct(self):
        endpoints = [dict(rank_id=0,local_record_id=1,start_local_ns=10,end_local_ns=30),
                     dict(rank_id=1,local_record_id=1,start_local_ns=25,end_local_ns=35)]
        summary, rows = operation_stats(endpoints, {(0,1):{},(1,1):{}}, {0:{},1:{}})
        self.assertEqual(summary['relative_entry_ns']['spread'],15)
        self.assertEqual(summary['duration_ns']['maximum'],20)
        self.assertEqual(summary['relative_finish_ns']['maximum'],25)
        self.assertEqual(summary['last_finishing_ranks'],[1])
        self.assertEqual(rows[0]['duration_ns'],20)
        self.assertEqual(distribution([1,2,3,4])['median'],2.5)

    def test_compute_relation_retains_extra_wait(self):
        rows, compute = [], []
        for rank in (0,1):
            end = 10+rank
            rows.append(dict(layer_id=1,kind='dispatch',rank_id=rank,end_ns=end))
            compute.append(dict(layer_id=1,stage='routed_expert',rank_id=rank,start_ns=end+rank,
                                end_ns=end+rank+100*(rank+1)))
            rows.append(dict(layer_id=1,kind='combine',rank_id=rank,start_ns=compute[-1]['end_ns'],
                             workload=dict(routed_inputs=rank+1)))
        result = compute_relations(rows,compute,dict(layers=[{},dict(routed_expert_per_input_ns=100)]))['1']
        self.assertEqual(result['expert_duration_minus_declared_work_ns']['maximum'],0)
        self.assertEqual(result['combine_entry_minus_expert_finish_ns']['maximum'],0)
        self.assertEqual(result['expert_start_minus_dispatch_finish_ns']['maximum'],1)

    def test_compute_relation_idle_rank_has_no_local_token_chain(self):
        rows, compute = [], []
        for layer in (1,2):
            rows.extend([dict(layer_id=layer,kind='dispatch',rank_id=0,start_ns=10,end_ns=10),
                dict(layer_id=layer,kind='combine',rank_id=0,start_ns=10,end_ns=10,
                     workload=dict(routed_inputs=0,requests=0))])
            compute.append(dict(layer_id=layer,stage='routed_expert',rank_id=0,start_ns=10,end_ns=10))
        result=compute_relations(rows,compute,dict(layers=[{},dict(routed_expert_per_input_ns=100),dict(routed_expert_per_input_ns=100)]))
        self.assertEqual(result['2']['next_dispatch_entry_minus_previous_combine_finish_and_intervening_work_ns']['maximum'],0)


if __name__ == '__main__':
    unittest.main()
