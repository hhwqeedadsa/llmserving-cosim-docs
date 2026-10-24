"""Check that the prototype-parser fix changes none of the compiled functions."""
import argparse
import hashlib
import json
from pathlib import Path
from extract import function


def check(inputs, build):
    command=json.loads((build/'command.json').read_text())
    checked=[]
    for row in command['selected_original_functions']:
        selected=function((inputs/'sources'/row['source']).read_text(),row['name'])
        assert selected['line']==row['line'] and selected['end_line']==row['end_line']
        assert hashlib.sha256(selected['text'].encode()).hexdigest()==row['sha256']
        checked.append(row['name'])
    mr=(inputs/'sources/mlx5_mr.c').read_text()
    definition=function(mr,'__mlx5_ib_dereg_mr')
    assert '/* Stop DMA */' in definition['text'] and 'ib_umem_release' in definition['text']
    assert mr.index('__mlx5_ib_dereg_mr')<mr.index(definition['text'])
    return dict(schema_version='target-retirement-parser-correction/1',
        reason='original parser selected a forward declaration instead of the later definition',
        unchanged_compiled_functions=checked,unchanged_compiled_function_count=len(checked),
        mlx5_dereg_definition_line=definition['line'],source_harness_rerun_required=False,
        analysis_change='all 16 claims now include hashed source intervals, including the main DeepEP window')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','build','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();result=check(a.inputs,a.build)
    a.output.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
