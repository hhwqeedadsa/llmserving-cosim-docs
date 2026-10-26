"""Recheck all saved results from restored code and empty working directory."""
import argparse
import json
from pathlib import Path
from fixtures.modern_selective.verify import verify
from fixtures.modern_selective.checkpoint import sha,save


def main():
    p=argparse.ArgumentParser();p.add_argument('--restored',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();root=a.restored.resolve();source=root/'source';evidence=root/'evidence'
    import moe_ub_cosim
    assert Path(__file__).resolve().is_relative_to(source)
    assert Path(moe_ub_cosim.__file__).resolve().is_relative_to(source/'python')
    assert Path.cwd()==root/'empty-cwd'
    comparisons={}
    for name in ('small-healthy','small-fault','four-healthy','four-fault'):
        data=(json.dumps(verify(evidence/name),ensure_ascii=False,indent=2)+'\n').encode()
        assert data==(evidence/(name+'-verification.json')).read_bytes()
        comparisons[name]=sha(evidence/(name+'-verification.json'))
    identity=json.loads((evidence/'runtime-identity.json').read_text())
    assert all(sha(evidence/'runtime'/name)==digest for name,digest in identity['files'].items())
    assert all(sha(source/name)==digest for name,digest in identity['sources'].items())
    save(a.output,dict(passed=True,byte_identical=comparisons,all_runtime_and_native_source_hashes_verified=True,
        additional_network_executions=0,scope='restored Python and evidence, empty cwd; no clean native build or GPU calibration'))


if __name__=='__main__':main()
