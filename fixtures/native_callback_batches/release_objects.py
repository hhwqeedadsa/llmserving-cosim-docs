"""Delete only owned regenerable compiler objects after recording identity."""
import argparse
from pathlib import Path
from fixtures.native_callback_batches.stage import load,save,sha


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);a=p.parse_args();stage=a.stage.resolve()
    assert stage==Path('/dev/shm/moe-native-callback-batches-20261025')
    assert load(stage/'verification.json')['verified']
    paths=sorted((stage/'objects').glob('*'))
    assert all(path.is_file() and not path.is_symlink() and path.suffix in ('.o','.d') for path in paths)
    entries={str(path.relative_to(stage)):dict(bytes=path.stat().st_size,sha256=sha(path)) for path in paths}
    save(stage/'regenerable-object-removal.json',dict(files=entries,recipe='build-commands.json',
        runtime_and_results_retained=True,rebuildable_from_recorded_source_and_fixed_dependencies=True))
    for path in paths:path.unlink()


if __name__=='__main__':main()
