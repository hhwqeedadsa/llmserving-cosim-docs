"""Compile exact selected functions with recording substitutes; not a driver."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from extract import function


SELECTIONS=[('rxe_mr.c','rxe_get_next_key'),('rxe_mr.c','mr_check_range'),
    ('rxe_mr.c','rxe_mr_copy'),('rxe_pool.c','rxe_pool_get_index'),
    ('rxe_pool.c','rxe_elem_release'),('rxe_pool.c','__rxe_put'),
    ('rxe_pool.c','__rxe_cleanup'),('rxe_mr.c','lookup_mr'),
    ('rxe_mr.c','rxe_invalidate_mr'),('rxe_mr.c','rxe_reg_fast_mr'),
    ('rxe_verbs.c','rxe_dereg_mr'),('mlx5_umr.c','mlx5r_umr_revoke_mr')]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args();a.output.mkdir();local=Path(__file__).resolve().parent
    metadata=json.loads((a.inputs/'sources.json').read_text())
    for row in metadata:assert hashlib.sha256((a.inputs/'sources'/row['alias']).read_bytes()).hexdigest()==row['sha256']
    chunks=[(local/'harness_prefix.h').read_text()];selected=[]
    for alias,name in SELECTIONS:
        original=(a.inputs/'sources'/alias).read_text();entry=function(original,name)
        selected.append(dict(source=alias,name=name,line=entry['line'],end_line=entry['end_line'],
            sha256=hashlib.sha256(entry['text'].encode()).hexdigest()))
        chunks.append('\n/* Original Linux v6.12 function: '+alias+' */\n'+entry['text']+'\n')
    chunks.append((local/'harness_main.c').read_text())
    generated=a.output/'selected.c';generated.write_text('\n'.join(chunks))
    command=['gcc','-std=gnu11','-O1','-Wall','-Wextra','-Werror','-Wno-unused-parameter',str(generated),'-o',str(a.output/'source-harness')]
    (a.output/'command.json').write_text(json.dumps(dict(command=command,cwd=str(Path.cwd()),
        selected_original_functions=selected,generated_sha256=hashlib.sha256(generated.read_bytes()).hexdigest(),
        substitutions=['single-thread refcount/xarray/RCU','timer outcome and scheduled existing-reference copy',
                       'page copy backing array','cleanup/free recording without deallocation','mlx5 WQE/firmware-wait recorder'],
        not_executed=['kernel module','real RDMA','GPU','firmware DMA fence']),sort_keys=True,indent=2)+'\n')
    subprocess.run(command,check=True)
    print(json.dumps(dict(selected_original_functions=len(selected),compiled=True,hardware_executed=False)))


if __name__=='__main__':main()
