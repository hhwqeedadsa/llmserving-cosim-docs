"""Acquire an explicitly budgeted, fixed-revision candidate prefix from HF.

Invoke only after authorization for the plan's candidate count and bytes.
No model downloads; no credentials in outputs; existing files are never replaced.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request
from urllib.parse import quote,urlsplit

from moe_ub_cosim.data_inputs import ROOT
from moe_ub_cosim.plan import require
from moe_ub_cosim.route_evidence import unique_object,inspect_raw
from moe_ub_cosim.route_selection import download_plan,prepare,save


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        require(urlsplit(newurl).scheme=='https','Non-HTTPS redirect refused')
        result=super().redirect_request(req,fp,code,msg,headers,newurl)
        if result is not None and urlsplit(newurl).netloc!=urlsplit(req.full_url).netloc:
            result.remove_header('Authorization')
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--max-download-bytes',type=int,required=True)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();require(1<=args.workers<=8 and args.max_download_bytes>0,'Invalid acquisition limits')
    config=json.loads(args.input.read_text(),object_pairs_hook=unique_object);plan=download_plan(config)
    _,source,_=prepare(config);required=plan['stop_after_eligible']
    require(plan['max_selected_candidate_bytes']<=args.max_download_bytes,'Plan exceeds authorized byte limit')
    require(source['source']['kind']=='published_model_trace','Only published HF files can be acquired')
    repo=plan['dataset_id'];revision=plan['dataset_revision']
    require(len(repo.split('/'))==2 and all(part and part not in ('.','..') for part in repo.split('/')), 'Invalid repository id')
    root=(ROOT/config['data_root']).resolve();out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=False)
    save(out/'download-plan.json',plan)
    report=dict(schema_version='moe-ub-route-acquisition/1',state='acquiring',started_utc=datetime.now(timezone.utc).isoformat(),
        dataset_id=repo,dataset_revision=revision,authorized_max_bytes=args.max_download_bytes,
        max_candidate_files=len(plan['bounded_candidates']),downloaded_bytes=0,eligible_files=0,checked=[])
    base=Path(os.environ.get('HF_HOME',str(Path.home()/'.cache/huggingface')))
    token=os.environ.get('HF_TOKEN') or os.environ.get('HUGGING_FACE_HUB_TOKEN')
    if not token:
        path=Path(os.environ.get('HF_TOKEN_PATH',str(base/'token')))
        token=path.read_text().strip() if path.exists() else None
    headers={'Authorization':'Bearer '+token} if token else {}
    def fetch(row):
        path=(root/row['path']).resolve();require(path.is_relative_to(root),'File outside data root')
        existing=path.exists()
        if existing:
            require(path.is_file() and path.stat().st_size==row['size'],'Existing candidate has wrong size')
            with path.open('rb') as stream:raw=stream.read(row['size']+1)
        else:
            url='https://huggingface.co/datasets/'+repo+'/resolve/'+revision+'/'+quote(row['path'],safe='/')
            try:
                opener=urllib.request.build_opener(Redirect())
                with opener.open(urllib.request.Request(url,headers=headers),timeout=45) as response:
                    raw=response.read(row['size']+1)
            except urllib.error.HTTPError as error:
                raise ValueError(f'HTTP {error.code} fetching {row["path"]}; no URL or credentials recorded') from None
            except Exception as error:
                raise ValueError(f'{type(error).__name__} fetching {row["path"]}; details suppressed to protect signed URLs') from None
        require(len(raw)==row['size'],'Downloaded/catalog size mismatch: '+row['path'])
        sha=hashlib.sha256(raw).hexdigest()
        if 'lfs_sha256' in row:require(sha==row['lfs_sha256'],'Downloaded LFS hash mismatch')
        else:require(hashlib.sha1(f'blob {len(raw)}\0'.encode()+raw).hexdigest()==row['git_blob_id'],'Downloaded Git blob mismatch')
        result=dict(path=row['path'],sha256=sha,bytes=len(raw),already_present=existing)
        try:
            prompt,steps=inspect_raw(raw,source['layers'])
            result.update(raw_schema_valid=True,prompt_tokens=prompt,available_decode_records=steps,
                          eligible=steps>=config['selection']['min_decode_records'])
        except (ValueError,TypeError) as error:
            # Retain the exact original for inspection, but never select it for execution.
            result.update(raw_schema_valid=False,eligible=False,error=str(error))
        if not existing:
            path.parent.mkdir(parents=True,exist_ok=True)
            name=None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.route-selection-',delete=False) as stream:
                    name=Path(stream.name);stream.write(raw)
                os.link(name,path)  # Fail instead of replacing a file another process created.
            finally:
                if name is not None:name.unlink()
        return result
    hashes=set();position=0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            while report['eligible_files']<required and position<len(plan['bounded_candidates']):
                # Never fetch more files than the number of eligible requests still needed.
                count=min(args.workers,required-report['eligible_files'])
                batch=plan['bounded_candidates'][position:position+count]
                for row in pool.map(fetch,batch):
                    require(row['sha256'] not in hashes,'Duplicate raw content cannot establish independent requests')
                    hashes.add(row['sha256']);report['checked'].append(row)
                    report['downloaded_bytes']+=0 if row['already_present'] else row['bytes']
                    report['eligible_files']+=row['eligible']
                position+=len(batch)
                save(out/'report.json',report)
                print('Checked',position,'eligible',report['eligible_files'],'downloaded bytes',report['downloaded_bytes'],flush=True)
        require(report['eligible_files']==required,'Authorized candidate prefix has insufficient eligible files')
        report.update(state='acquired_and_raw_validated',ended_utc=datetime.now(timezone.utc).isoformat())
    except Exception as error:
        report.update(state='failed',error=str(error),ended_utc=datetime.now(timezone.utc).isoformat());raise
    finally:save(out/'report.json',report)


if __name__=='__main__':main()
