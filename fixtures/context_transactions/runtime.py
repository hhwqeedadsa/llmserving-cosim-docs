"""Portable ABI-bound launcher: bypass historical ELF RPATH without editing ELF."""
import argparse
import subprocess
from pathlib import Path
from fixtures.context_transactions.stage import save, sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);a=p.parse_args()
    runtime=a.stage.resolve()/'runtime';launcher=runtime/'run';assert not launcher.exists()
    launcher.write_text('#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'exec /lib64/ld-linux-x86-64.so.2 --inhibit-rpath "" --library-path "$base/lib" "$base/transaction-probe" "$@"\n')
    launcher.chmod(0o755)
    listed=subprocess.check_output(['/lib64/ld-linux-x86-64.so.2','--inhibit-rpath','',
        '--library-path',str(runtime/'lib'),'--list',str(runtime/'transaction-probe')],text=True)
    assert sum(str(runtime/'lib') in line for line in listed.splitlines())==8
    save(a.stage/'runtime-launcher.json',dict(passed=True,launcher_sha256=sha(launcher),loader_listing=listed,
        scope='Linux x86-64/glibc ABI-bound launcher; ELF and libraries unchanged; actual network loaded paths checked separately'))


if __name__=='__main__':main()
