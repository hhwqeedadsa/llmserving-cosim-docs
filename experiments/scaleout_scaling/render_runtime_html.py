"""Render an offline, self-contained runtime report from public timings.json.

Standard library only. Does not modify measurements or run simulations.
"""
import argparse
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True,help='Previously generated timings.json')
    parser.add_argument('--output',type=Path,required=True,help='New HTML file; never overwritten')
    parser.add_argument('--layouts',type=Path,help='Hash-checked physical inputs; defaults to network_layouts.json beside input')
    args=parser.parse_args()
    template=Path(__file__).with_name('runtime_report_template.html')
    if args.output.exists() or args.output.with_suffix('.manifest.json').exists():
        parser.error('choose a new output file')
    raw=args.input.read_bytes()
    data=json.loads(raw)
    layouts_path=args.layouts or args.input.with_name('network_layouts.json')
    layouts=json.loads(layouts_path.read_text()) if layouts_path.is_file() else dict(profiles={},cases={})
    rows=data['rows']
    if not rows or len({r['id'] for r in rows})!=len(rows):
        parser.error('expected nonempty measurements with unique identities')
    for row in rows:
        if row['right_censored'] and (row['independently_verified'] or row['simulation_finish_ns'] is not None):
            parser.error('timeout cannot have a verified final simulation time')
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    network_payload=json.dumps(layouts,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    date=datetime.fromisoformat(data['machine']['captured_utc']).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y.%m.%d')
    summary=f"收录 {len(rows)} 次实际运行，{sum(r['independently_verified'] for r in rows)} 次通过校验，{sum(r['right_censored'] for r in rows)} 次超时。部分作业同机重叠，比值不作为受控扩展效率。"
    result=template.read_text().replace('@@DATA@@',payload).replace('@@NETWORKS@@',network_payload).replace('@@NETWORK_SCRIPT@@',Path(__file__).with_name('runtime_network_layout.js').read_text()).replace('@@DATE@@',date).replace('@@SUMMARY@@',html.escape(summary))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(result)
    manifest=dict(source_sha256=digest(raw),template_sha256=digest(template.read_bytes()),
        renderer_sha256=digest(Path(__file__).read_bytes()),html_sha256=digest(args.output.read_bytes()),
        input_name=args.input.name,measurements=len(rows),self_contained=True,
        display_timezone='Asia/Shanghai',raw_timestamp_timezone='UTC',simulation_executed=False)
    manifest['network_script_sha256']=digest(Path(__file__).with_name('runtime_network_layout.js').read_bytes())
    manifest['network_layouts_sha256']=digest(layouts_path.read_bytes()) if layouts_path.is_file() else None
    args.output.with_suffix('.manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(output=str(args.output),measurements=len(rows),simulation_executed=False),ensure_ascii=False))


if __name__=='__main__':
    main()
