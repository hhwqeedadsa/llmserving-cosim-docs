"""Bounded full-model recipes, disposable active files and exact graph replay."""
import argparse
from datetime import datetime,timezone
import gzip
import json
from pathlib import Path
import subprocess
import sys

from .full_model_recipe import build_export, identities_from, prepare
from .host_probe import disk_usage
from .model_capacity import fields
from .plan import integer,require
from .plan_io import file_sha256

ROOT=Path(__file__).resolve().parents[2]


def save(path,value):
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')


def policy(value):
    fields(value,'schema_version max_recipe_bytes max_archive_bytes max_active_graph_bytes','graph storage policy')
    require(value['schema_version']=='moe-ub-graph-storage/1','Unsupported graph storage policy')
    for name in ('max_recipe_bytes','max_archive_bytes','max_active_graph_bytes'):
        integer(value[name],name,1,2**63-1)
    return value


def source_hashes():
    proto=ROOT/'third_party/LLMServingSim/astra-sim/extern/graph_frontend/chakra/schema/protobuf/et_def.proto'
    return {str(p.relative_to(ROOT)):file_sha256(p) for p in [*sorted((ROOT/'python/moe_ub_cosim').glob('*.py')),proto]}


def read_recipe(path,limit):
    with gzip.open(path,'rb') as stream:body=stream.read(limit+1)
    require(len(body)<=limit,'Graph recipe byte budget exceeded')
    return json.loads(body)


def inventory(directory):
    result={}
    for path in sorted(directory.rglob('*')):
        require(not path.is_symlink(),'Unexpected symlink in owned graph directory')
        if path.is_file():
            result[str(path.relative_to(directory))]=dict(bytes=path.stat().st_size,sha256=file_sha256(path))
        else:require(path.is_dir(),'Unexpected special graph file')
    return result


class GraphStorage:
    def __init__(self,output,limits,timeout):
        self.output=Path(output);self.limits=policy(limits);self.timeout=timeout
        self.archive=self.output/'graph-replay';self.archive.mkdir(exist_ok=False)
        self.active={};self.completed=[];self.peak_graphs=0;self.peak_active_bytes=0
        save(self.archive/'policy.json',self.limits)
        self.sources=source_hashes();save(self.archive/'sources.json',self.sources)

    def budget(self):
        archive=disk_usage(self.archive);active=disk_usage(self.output/'graphs')
        require(max(archive.values())<=self.limits['max_archive_bytes'],'Graph archive disk budget exceeded')
        require(max(active.values())<=self.limits['max_active_graph_bytes'],'Active graph disk budget exceeded')
        self.peak_active_bytes=max(self.peak_active_bytes,max(active.values()))

    def check_composition(self,layers,model,profile):
        """Run the same disposable input DAG validation before backend startup."""
        slot=self.archive/'input-checks'/layers[0]['group_id'];slot.mkdir(parents=True,exist_ok=False)
        recipe=dict(schema_version='moe-ub-composition-check/1',layers=layers,model=model,profile=profile)
        path=slot/'recipe.json.gz';self.write_recipe(path,recipe);self.budget()
        self.worker([sys.executable,'-m','moe_ub_cosim.graph_storage','check-composition','--recipe',str(path.resolve()),
            '--max-recipe-bytes',str(self.limits['max_recipe_bytes']),
            '--receipt',str((slot/'receipt.json').resolve())],slot)
        receipt=json.loads((slot/'receipt.json').read_text())
        require(receipt['passed'] and receipt['recipe_sha256']==file_sha256(path),'Composition check receipt mismatch')
        self.budget()

    def write_recipe(self,path,recipe):
        written=0
        with path.open('xb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,compresslevel=1) as stream:
            for part in json.JSONEncoder(separators=(',',':')).iterencode(recipe):
                data=part.encode();written+=len(data)
                require(written<=self.limits['max_recipe_bytes'],'Graph recipe byte budget exceeded')
                stream.write(data)
        return written

    def worker(self,command,slot):
        started=datetime.now(timezone.utc).isoformat()
        with (slot/'stdout.log').open('x') as out,(slot/'stderr.log').open('x') as err:
            try:code=subprocess.run(command,cwd=ROOT,stdout=out,stderr=err,timeout=self.timeout).returncode
            except subprocess.TimeoutExpired:code='timeout'
            save(slot/'command.json',dict(argv=command,cwd=str(ROOT),started_utc=started,
                ended_utc=datetime.now(timezone.utc).isoformat(),exit_code=code))
        require(code==0,f'Graph builder failed ({code}); see {slot}/stderr.log')

    def generate(self,service,candidate,directory):
        graph=candidate['graph_id']
        require(directory==self.output/'graphs'/graph and graph not in self.active and graph not in self.completed,
                'Graph storage ownership mismatch')
        recipe,consumed,next_usage=prepare(service,candidate)
        slot=self.archive/graph;slot.mkdir()
        path=slot/'recipe.json.gz'
        # Limit raw recipe bytes while encoding; recipes contain build inputs,
        # never the large node, transfer or dependency tables.
        written=self.write_recipe(path,recipe)
        self.budget()
        command=[sys.executable,'-m','moe_ub_cosim.graph_storage','build','--recipe',str(path.resolve()),
            '--max-recipe-bytes',str(self.limits['max_recipe_bytes']),'--output-dir',str(directory.resolve()),
            '--receipt',str((slot/'receipt.json').resolve())]
        # A fresh process releases its full Python heap before the backend loads
        # the next graph. No service/network process is forked or restarted.
        self.worker(command,slot)
        receipt=json.loads((slot/'receipt.json').read_text())
        require(receipt['graph_id']==graph and receipt['recipe_sha256']==file_sha256(path),
                'Graph build receipt mismatch')
        self.budget()
        files=inventory(directory)
        save(slot/'archive.json',dict(schema_version='moe-ub-graph-replay/1',graph_id=graph,
            recipe_sha256=file_sha256(path),raw_recipe_bytes=written,files=files,
            generation=json.loads((directory/'generation.json').read_text())))
        self.budget()
        service.identities=identities_from(receipt['identities']);service.consumed.update(consumed)
        if next_usage is not None:service.resource_usage=next_usage
        self.active[graph]=directory;self.peak_graphs=max(self.peak_graphs,len(self.active))

    def retire(self,graph,now):
        require(graph in self.active,'Cannot retire an unknown graph')
        directory=self.active[graph];slot=self.archive/graph
        archive=json.loads((slot/'archive.json').read_text())
        require(file_sha256(slot/'recipe.json.gz')==archive['recipe_sha256'],'Graph recipe changed before cleanup')
        require(inventory(directory)==archive['files'],'Owned graph files changed before cleanup')
        # Record exact owned files, digests, regeneration input and the real
        # WAVE_DONE boundary before removing any reproducible artifact.
        save(slot/'cleanup.json',dict(schema_version='moe-ub-graph-cleanup/1',graph_id=graph,
            finished_ns=now,reason='backend_completed_and_records_sealed',
            files=archive['files'],replay_input=str((slot/'archive.json').relative_to(self.output))))
        for name in archive['files']:(directory/name).unlink()
        for sub in sorted((p for p in directory.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):sub.rmdir()
        directory.rmdir()
        del self.active[graph];self.completed.append(graph);self.budget()

    def finish(self):
        require(not self.active,'Service ended with active graph files')
        self.budget()
        save(self.output/'graph-storage.json',dict(schema_version='moe-ub-graph-storage-result/1',
            policy=self.limits,retired_graphs=self.completed,active_graphs=[],
            peak_active_graphs=self.peak_graphs,peak_active_bytes=self.peak_active_bytes,
            archive_usage=disk_usage(self.archive)))


def replay(archive,output):
    archive=Path(archive);slot=archive.parent;metadata=json.loads(archive.read_text())
    require(metadata['schema_version']=='moe-ub-graph-replay/1','Unsupported graph replay archive')
    require(json.loads((slot.parent/'sources.json').read_text())==source_hashes(),
            'Graph replay requires the recorded source revision')
    path=slot/'recipe.json.gz'
    require(file_sha256(path)==metadata['recipe_sha256'],'Graph replay recipe hash mismatch')
    limits=policy(json.loads((slot.parent/'policy.json').read_text()))
    receipt=build_export(read_recipe(path,limits['max_recipe_bytes']),output)
    generation=json.loads((Path(output)/'generation.json').read_text())
    require(generation==metadata['generation'],'Rebuilt graph file hashes differ from archive')
    expected=json.loads((slot/'receipt.json').read_text())
    require(all(expected[k]==v for k,v in receipt.items()),'Rebuilt graph identities/counts differ')
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    build=sub.add_parser('build');build.add_argument('--recipe',type=Path,required=True)
    build.add_argument('--max-recipe-bytes',type=int,required=True);build.add_argument('--receipt',type=Path,required=True)
    build.add_argument('--output-dir',type=Path,required=True)
    again=sub.add_parser('replay');again.add_argument('--archive',type=Path,required=True)
    again.add_argument('--output-dir',type=Path,required=True)
    check=sub.add_parser('check-composition');check.add_argument('--recipe',type=Path,required=True)
    check.add_argument('--max-recipe-bytes',type=int,required=True);check.add_argument('--receipt',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='build':
        integer(args.max_recipe_bytes,'max_recipe_bytes',1,2**63-1)
        receipt=build_export(read_recipe(args.recipe,args.max_recipe_bytes),args.output_dir)
        save(args.receipt,dict(receipt,recipe_sha256=file_sha256(args.recipe)))
    elif args.action=='check-composition':
        from .full_model import build_full_model
        from .plan import RunIdentities
        integer(args.max_recipe_bytes,'max_recipe_bytes',1,2**63-1)
        recipe=read_recipe(args.recipe,args.max_recipe_bytes)
        fields(recipe,'schema_version layers model profile','composition check')
        require(recipe['schema_version']=='moe-ub-composition-check/1','Invalid composition check')
        layers=recipe['layers']
        build_full_model(layers,RunIdentities(),[[] for _ in layers],recipe['model'],recipe['profile'])
        save(args.receipt,dict(passed=True,recipe_sha256=file_sha256(args.recipe)))
    else:replay(args.archive,args.output_dir)


if __name__=='__main__':main()
