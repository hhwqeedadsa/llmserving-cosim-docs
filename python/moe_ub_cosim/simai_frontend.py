"""Pinned SimAI/Vidur scheduler loading, with explicit lazy registry imports.

No sklearn/GPU predictor is substituted. This prepares real upstream scheduling
classes for a network-driven adapter; preparation alone executes no inference.
"""
import hashlib
import json
from pathlib import Path
import sys

from .scaleout_topology import require

SPEC=Path(__file__).with_name('simai_source.json')
LAZY_INITIALIZERS={
    'vidur/scheduler/__init__.py': '''# Local adaptation: defer unused global scheduler registry imports.
__all__ = ["BaseGlobalScheduler", "GlobalSchedulerRegistry"]
def __getattr__(name):
    if name in __all__:
        from vidur.scheduler import global_scheduler
        return getattr(global_scheduler, name)
    raise AttributeError(name)
''',
    'vidur/execution_time_predictor/__init__.py': '''# Local adaptation: keep the optional sklearn registry lazy.
from vidur.execution_time_predictor.base_execution_time_predictor import BaseExecutionTimePredictor
__all__ = ["ExecutionTimePredictorRegistry", "BaseExecutionTimePredictor"]
def __getattr__(name):
    if name == "ExecutionTimePredictorRegistry":
        from vidur.execution_time_predictor.execution_time_predictor_registry import ExecutionTimePredictorRegistry
        return ExecutionTimePredictorRegistry
    raise AttributeError(name)
''',
}


def sha(data):return hashlib.sha256(data).hexdigest()


def prepare(source_root,output):
    source_root,output=Path(source_root).resolve(),Path(output).resolve()
    require(not output.exists(),'SimAI output directory already exists')
    spec=json.loads(SPEC.read_bytes())
    verified=[]
    # Verify all files before copying any executable source. Blob identity ties
    # contents to the fixed Git tree, rather than trusting an arbitrary root.
    for entry in spec['files']:
        relative=entry['path'];p=source_root/relative
        require(p.is_file() and not p.is_symlink() and p.stat().st_size==entry['bytes'],
                'missing or changed SimAI source '+relative)
        raw=p.read_bytes()
        require(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==entry['git_blob_sha1'],
                'SimAI fixed source blob mismatch '+relative)
        verified.append((entry,raw))
    output.mkdir(parents=True)
    records=[]
    for entry,raw in verified:
        relative=entry['path'];target=relative.removeprefix('vidur-alibabacloud/')
        if target=='LICENSE':target='SimAI-LICENSE'
        patched=target in LAZY_INITIALIZERS
        data=LAZY_INITIALIZERS[target].encode() if patched else raw
        p=output/target;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        records.append(dict(path=target,original_git_blob_sha1=entry['git_blob_sha1'],
                            original_sha256=sha(raw),sha256=sha(data),bytes=len(data),patched=patched))
    receipt=dict(schema_version='moe-simai-runtime/1',repository=spec['repository'],revision=spec['revision'],
                 spec_sha256=sha(SPEC.read_bytes()),files=records,
                 adaptation='two lazy package initializers only; scheduler/request/batch/memory algorithms unchanged',
                 installed_dependencies=False,network_executed=False)
    (output/'runtime.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return dict(passed=True,output=str(output),revision=spec['revision'],verified_files=len(records),
                patched_files=sum(r['patched'] for r in records),network_executed=False)


def load(runtime):
    runtime=Path(runtime).resolve();receipt=json.loads((runtime/'runtime.json').read_bytes())
    spec=json.loads(SPEC.read_bytes())
    require(receipt['spec_sha256']==sha(SPEC.read_bytes()) and receipt['revision']==spec['revision'],
            'SimAI runtime uses another source specification')
    expected={entry['path'].removeprefix('vidur-alibabacloud/'):entry for entry in spec['files']}
    expected['SimAI-LICENSE']=expected.pop('LICENSE')
    require(len(receipt['files'])==len(expected) and {r['path'] for r in receipt['files']}==set(expected),
            'SimAI runtime file set differs from fixed source')
    require({str(p.relative_to(runtime)) for p in runtime.rglob('*.py')}==
            {p for p in expected if p.endswith('.py')},'unexpected executable file in SimAI runtime')
    for entry in receipt['files']:
        rel=entry['path'];p=runtime/rel;require(not p.is_symlink(),'SimAI runtime file is a symlink')
        raw=p.read_bytes();fixed=expected[rel]
        if rel in LAZY_INITIALIZERS:
            require(raw==LAZY_INITIALIZERS[rel].encode(),'SimAI lazy initializer differs')
        else:
            require(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==fixed['git_blob_sha1'],
                    'SimAI original functional source differs '+rel)
        require(sha(raw)==entry['sha256'],'SimAI runtime checksum differs '+rel)
    for name,module in list(sys.modules.items()):
        if name=='vidur' or name.startswith('vidur.'):
            path=getattr(module,'__file__',None)
            namespace_paths=list(getattr(module,'__path__',[]))
            require((path is not None and Path(path).resolve().is_relative_to(runtime)) or
                    (path is None and namespace_paths and all(Path(p).resolve().is_relative_to(runtime) for p in namespace_paths)),
                    'another Vidur runtime is already loaded')
    sys.path.insert(0,str(runtime))
    from vidur.config import OrcaSchedulerConfig, ReplicaConfig, SyntheticRequestGeneratorConfig, FixedRequestLengthGeneratorConfig, ClusterConfig, MetricsConfig
    from vidur.entities import Batch, Cluster, Replica, Request
    from vidur.scheduler.replica_scheduler.orca_replica_scheduler import OrcaReplicaScheduler
    return dict(OrcaSchedulerConfig=OrcaSchedulerConfig,ReplicaConfig=ReplicaConfig,
                SyntheticRequestGeneratorConfig=SyntheticRequestGeneratorConfig,
                FixedRequestLengthGeneratorConfig=FixedRequestLengthGeneratorConfig,
                ClusterConfig=ClusterConfig,MetricsConfig=MetricsConfig,Cluster=Cluster,
                Batch=Batch,Replica=Replica,Request=Request,OrcaReplicaScheduler=OrcaReplicaScheduler,
                receipt=receipt)
