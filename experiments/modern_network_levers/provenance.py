"""Recheck cached primary sources; no network requests or new downloads."""
import argparse
from pathlib import Path
from experiments.modern_network_levers.analyze import load,save,sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--repository',type=Path,required=True);p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();stage=a.stage.resolve()
    old=load(repo/'docs/review_artifacts/paper_readiness_20261022/sources.json')
    primary=[]
    for entry in old['reused_primary_sources']:
        if entry['id'] not in ('llmservingsim2','simai','clem','vidur','patterns','analytical_overlap'):continue
        for key,digest in (('local_snapshot','sha256'),('text_snapshot','text_sha256')):
            assert sha(repo/entry[key])==entry[digest]
        primary.append(entry)
    hardware=load(repo/'docs/review_artifacts/hardware_systems_20261020/sources.json')
    for entry in hardware['sources']:
        if entry['id'] not in ('simbricks_readme','simbricks_arch'):continue
        assert sha(repo/entry['local_snapshot'])==entry['sha256'];primary.append(entry)
    assert len(primary)==8
    astra=repo/'third_party/LLMServingSim/astra-sim/README.md'
    save(stage/'sources.json',dict(passed=True,cached_primary_documents=primary,
        astra_readme=dict(path=str(astra.relative_to(repo)),sha256=sha(astra),
            locked_commit='8ecf388a667de9d45866da1ee9829938d6218686',url='https://github.com/astra-sim/astra-sim'),
        routing=load(stage/'source.json'),runtime_and_native_source_identity=load(stage/'runtime-identity.json'),
        scope='cached originals rehashed; source-derived comparison only, no competitor performance execution or claim of complete literature search'))


if __name__=='__main__':main()
