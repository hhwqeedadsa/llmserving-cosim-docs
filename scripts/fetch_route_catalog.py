"""Fetch only complete fixed-revision HF directory metadata, never trace contents."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.request
from urllib.parse import quote,unquote,urlsplit

from moe_ub_cosim.plan import require
from moe_ub_cosim.route_evidence import validate_manifest,unique_object
from moe_ub_cosim.route_selection import relative_path,save


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Unexpected metadata redirect')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-manifest',type=Path,required=True)
    parser.add_argument('--subset',default='mmlu');parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();source=json.loads(args.source_manifest.read_text(),object_pairs_hook=unique_object)
    validate_manifest(source);source=source['source']
    require(source['kind']=='published_model_trace','Require published fixed-revision source')
    repo=relative_path(source['dataset_id']);require(len(repo.split('/'))==2,'Invalid dataset id')
    prefix=relative_path(source['collector_model_id']+'/'+args.subset)+'/'
    require(args.subset and '/' not in args.subset,'Invalid benchmark subset')
    revision=source['dataset_revision'];out=args.output_dir;out.mkdir(parents=True,exist_ok=False)
    base=Path(os.environ.get('HF_HOME',str(Path.home()/'.cache/huggingface')))
    token=os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if not token:
        path=Path(os.environ.get('HF_TOKEN_PATH',str(base/'token')))
        token=path.read_text().strip() if path.exists() else None
    api_path='/api/datasets/'+repo+'/tree/'+revision+'/'+quote(prefix.rstrip('/'),safe='/')
    url='https://huggingface.co'+api_path+'?recursive=true&expand=false&limit=1000'
    opener=urllib.request.build_opener(NoRedirect());pages=[];files=[];seen=set();urls=set()
    while url:
        parts=urlsplit(url)
        require(len(pages)<40 and url not in urls and parts.scheme=='https' and
                parts.netloc=='huggingface.co' and unquote(parts.path)==unquote(api_path),'Invalid/repeated catalog pagination URL')
        urls.add(url)
        request=urllib.request.Request(url,headers={'Authorization':'Bearer '+token} if token else {})
        try:
            with opener.open(request,timeout=45) as response:
                raw=response.read(4*2**20+1);link=response.headers.get('Link','')
        except Exception as error:
            raise ValueError(f'Catalog fetch failed: {type(error).__name__}; no credentials or URL recorded') from None
        require(len(raw)<=4*2**20,'Catalog page byte budget exceeded')
        entries=json.loads(raw,object_pairs_hook=unique_object);require(isinstance(entries,list),'Invalid catalog page')
        pages.append(dict(url=url,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
        (out/f'catalog-page-{len(pages):03}.json').write_bytes(raw)
        for item in entries:
            path=relative_path(item['path']);require(path.startswith(prefix),'Path outside requested subtree')
            if item['type']!='file' or not path.endswith('.json'):continue
            require(path not in seen,'Repeated catalog file');seen.add(path)
            row=dict(path=path,size=item['size'],git_blob_id=item['oid'])
            if 'lfs' in item:row['lfs_sha256']=item['lfs']['oid']
            files.append(row)
        matches=re.findall(r'<([^>]+)>;\s*rel="next"',link);require(len(matches)<=1,'Ambiguous catalog pagination')
        url=matches[0] if matches else None
        print('Metadata pages:',len(pages),'files:',len(files),flush=True)
    report=dict(schema_version='moe-ub-route-catalog/1',dataset_id=repo,dataset_revision=revision,
        prefix=prefix,scope='complete_fixed_revision_subtree',complete=True,files=sorted(files,key=lambda r:r['path']),
        pages=pages,checked_utc=datetime.now(timezone.utc).isoformat())
    save(out/'catalog.json',report)
    inventory=json.dumps(report['files'],sort_keys=True,separators=(',',':')).encode()
    print('Inventory SHA256:',hashlib.sha256(inventory).hexdigest(),'source bytes:',sum(r['size'] for r in files))


if __name__=='__main__':main()
