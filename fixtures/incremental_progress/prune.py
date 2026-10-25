"""Release only this stage's regenerable compiler objects after verified links."""
import argparse
from pathlib import Path
from fixtures.incremental_progress.stage import load,save,sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);a=p.parse_args();stage=a.stage.resolve()
    assert load(stage/'runtime-identity.json')['files']
    assert all(load(stage/(name+'-verification.json'))['whole_native_modern_control_equal']
        for name in ('default-healthy','timed-healthy','wake-fault'))
    files=sorted((stage/'objects').iterdir())
    assert all(path.is_file() and not path.is_symlink() and path.suffix in ('.o','.d') for path in files)
    save(stage/'regenerable-object-removal.json',dict(reason='linked executables retained; exact compile recipes/sources and object hashes retained',
        files={str(path.relative_to(stage)):dict(bytes=path.stat().st_size,sha256=sha(path)) for path in files}))
    for path in files:path.unlink()


if __name__=='__main__':main()
