"""Verify prior immutable archive and local cached original sources."""
import argparse
from pathlib import Path
import subprocess
from experiments.nic_wakeup_effect.checkpoint import admit
from experiments.feedback_ancestry.stage import save,sha


def main():
    p=argparse.ArgumentParser()
    for k in ('repository','archive','paper-directory','output'):p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();repo=a.repository.resolve();assert not a.output.exists()
    assert sha(a.archive)=='789de4affb50322a30c28b90c6c6d3e19acc8489348ed47263f768b0128f164f'
    payload,manifest=admit(a.archive);out=a.output;out.mkdir()
    def copy(name,dest):dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(payload[name])
    for name in manifest['files']:
        prefixes=('evidence/network/','evidence/planned/','evidence/topology/','evidence/inputs/observed/')
        if name.startswith(prefixes):copy(name,out/name[len('evidence/'):])
    basis=out/'basis';basis.mkdir()
    papers={'dcqcn2015.pdf':'879074a33b78ceb93f9f66d59b217fba9e7621320cc68bc47914747cf5cf31f8',
        'dcqcn2015.txt':'8e24d0ed58449e08dbee0b985e8f531dc423903fc7bbb7eba1c356a2ae577ef6'}
    for name,h in papers.items():
        src=a.paper_directory/name;assert sha(src)==h;(basis/name).write_bytes(src.read_bytes())
    native=repo/'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3'
    paths={'rp.hh':repo/'backend/rdma/dcqcn_reference.hh','adapter.cc':repo/'backend/rdma/persistent_streams.cc',
        'switch.cc':native/'src/point-to-point/model/switch-node.cc','mmu.cc':native/'src/point-to-point/model/switch-mmu.cc',
        'qbb.cc':native/'src/point-to-point/model/qbb-net-device.cc','fabric.cc':repo/'backend/rdma/fabric.cc'}
    identities={}
    for name,src in paths.items():
        (basis/name).write_bytes(src.read_bytes());identities[str(src.relative_to(repo))]=sha(src)
    platform=subprocess.check_output(['git','rev-parse','f325869'],cwd=repo,text=True).strip()
    subprocess.run(['git','diff','--exit-code',platform,'--','python','backend','deps.lock.json','.gitmodules'],cwd=repo,check=True)
    save(out/'identity.json',dict(platform=platform,native_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=native,text=True).strip(),
        source_sha256=identities,paper_sha256=papers,prior_archive_sha256=sha(a.archive),prior_members_verified=len(payload)))
    save(out/'manifest.json',dict(files={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}))
    print('three fixed packet results and cached original paper verified; no network')


if __name__=='__main__':main()
