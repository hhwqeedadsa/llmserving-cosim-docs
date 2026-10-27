"""Bounded public topology preparation and separately admitted evaluation input."""
import argparse
from copy import deepcopy
import hashlib
import html
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile

from .model import load, save, sha

ARCHIVE_SHA = '147c7ae9352b7761964b47cb18653a4a1bf314ec22410b900da3668d8a9c38f7'
SOURCES = {
    'predictor': 'experiments/shared_uplink_choice/predict.py',
    'fabric': 'backend/rdma/fabric.cc',
    'modern_binding': 'python/moe_ub_cosim/modern_native_layout.py',
    'streams': 'backend/rdma/persistent_streams.cc',
    'cnp_projection': 'backend/rdma/cnp_switch.hh',
    'native_switch': 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/switch-node.cc',
    'native_host': 'third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/rdma-hw.cc',
    'alibaba_switch': 'data/research/ns-3-alibabacloud-3e0c7c1/simulation/src/point-to-point/model/switch-node.cc',
    'alibaba_host': 'data/research/ns-3-alibabacloud-3e0c7c1/simulation/src/point-to-point/model/rdma-hw.cc',
}


def admitted(archive, selected):
    assert archive.stat().st_size < 32*2**20 and sha(archive) == ARCHIVE_SHA
    receipts, files, total = {}, {}, 0
    with tarfile.open(archive,'r:gz') as stream:
        for member in stream:
            name = PurePosixPath(member.name)
            assert member.isfile() and str(name) == member.name and not name.is_absolute()
            assert '..' not in name.parts and '\\' not in member.name and member.name not in receipts
            assert 0 <= member.size <= 32*2**20 and len(receipts) < 1024
            total += member.size
            assert total < 256*2**20
            raw = stream.extractfile(member).read()
            assert len(raw) == member.size
            receipts[member.name] = dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
            if member.name in selected or member.name == 'manifest.json': files[member.name] = raw
    manifest = __import__('json').loads(files.pop('manifest.json'))
    assert manifest['schema_version'] == 'shared-uplink-choice-checkpoint/1'
    assert set(receipts)-{'manifest.json'} == set(manifest['files'])
    for name,row in manifest['files'].items():
        assert {k:row[k] for k in ('bytes','sha256')} == receipts[name]
        assert row['mode'] in (0o644,0o755)
        assert not any(str(parent) in receipts for parent in PurePosixPath(name).parents)
    assert set(files) == set(selected)
    return files


def prepare(repo, run):
    prefix = 'run/stage/'
    mapping = {}
    for batch in (4,8):
        for src,dst in (('input.json','input.json'),('plan/plan.json','plan.json')):
            mapping[prefix+f'observed/batch{batch}/'+src] = f'observed/batch{batch}/'+dst
        for filename in ('initial.json','modern-execution.json'):
            mapping[prefix+f'truth/batch{batch}/baseline/modern/'+filename] = f'truth/batch{batch}/'+filename
    for filename in ('intake.json','source.json','baseline-topology-config.json'):
        mapping[prefix+'observed/'+filename] = 'observed/'+filename
    for name in ('900','901','905','1003','1004','1007','1008','1011'):
        mapping[prefix+'observed/routes/'+name+'.json'] = 'truth/routes/'+name+'.json'
    files = admitted(repo/'runs/shared_uplink_choice_20261027_r2/checkpoint-v2.tar.gz',mapping)
    for name,raw in files.items():
        path = run/mapping[name]
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
    sources = {}
    for key,name in SOURCES.items():
        path = run/'observed/sources'/(key+Path(name).suffix)
        path.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(repo/name,path)
        sources[key] = dict(path=str(path.relative_to(run/'observed')),repository_path=name,sha256=sha(path))
    save(run/'observed/sources.json',sources)
    for cores in (1,2,4):
        cfg = deepcopy(load(run/'observed/baseline-topology-config.json'))
        cfg['fabric'].update(cores_per_group=cores,agg_uplinks=cores,core_wiring='full_mesh')
        save(run/f'observed/cores{cores}-config.json',cfg)
    save(run/'preparation.json',dict(passed=True,archive_sha256=ARCHIVE_SHA,
        copied={mapping[n]:hashlib.sha256(raw).hexdigest() for n,raw in files.items()},
        platform_commit='1c94423',raw_routes_in_prediction_input=False,new_networks=0))


def freeze(run):
    observed = run/'observed'
    save(observed/'identity.json',dict(files={str(p.relative_to(observed)):sha(p)
        for p in sorted(observed.rglob('*')) if p.is_file()}))


def report(run, output):
    output.mkdir()
    summary = load(run/'predictions/summary.json')
    verification = load(run/'verification.json')
    lines = ['# 多路径模型的实际边界与有限连接需求核查','',
        '新增13份来源已确认，其中11份完整；连同原有4份共有15份完整请求。本轮固定其中8份的同一Decode入口/层，保留batch4/8与四个活动rank。没有新增网络仿真、GPU执行或硬件测量。', '',
        '原生后端已有固定哈希的等价多路径（ECMP）、拥塞反馈、选择重传和链路故障。旧link_phase预测器要求唯一合规最短路，因此不能直接用于这些多路径图；这是精简预测器的适用范围，不能写成后端或竞品不支持。', '',
        '| batch | 核心数 | 最多路径数 | Dispatch数据比 | Combine数据比 | 七阶段最大比 |',
        '|---|---:|---:|---:|---:|---:|']
    for name,case in summary['cases'].items():
        batch,cores = name.split('-')
        stages = {s['stage']:s for s in case['stages']}
        lines.append(f"| {batch[5:]} | {cores[5:]} | {case['maximum_shortest_paths']} | {stages['dispatch.data']['peak_service_ratio']:.6f} | {stages['combine.data']['peak_service_ratio']:.6f} | {max(s['peak_service_ratio'] for s in case['stages']):.6f} |")
    lines += ['', '比值为确定性连接哈希对应的最大链路帧服务量，除以将同一消息等分到所有最短路径的参考值。只计算MRT1请求和原子回复，不含ACK、CNP、分包头及重传；比值不是时延放大、可消除收益或随机哈希期望。增加核心数也增加总带宽和交换机数量，不能称同成本优化。', '',
        '| 条件 | 阶段 | 确定性峰值帧服务量 ns | 等分参考 ns | 比值 |',
        '|---|---|---:|---:|---:|']
    for name,case in summary['cases'].items():
        for s in case['stages']:
            lines.append(f"| {name} | {s['stage']} | {s['deterministic_peak_service_ns']:.3f} | {s['equal_path_peak_service_ns']:.3f} | {s['peak_service_ratio']:.6f} |")
    lines += ['', '普通哈希模型可由输入重建持久连接的路径需求。不能把相对等分参考的差异直接归于MoE特有机制，也不能把普通ECMP建模包装成论文创新。当前未执行这些多路径图的包级网络，尚不能声称该投影与实际多路径包轨迹等价或会改变配置排序。', '',
        '源码边界：源QP绑定明确物理NIC；交换机按目的最短跳候选与IP/端口哈希选路。ACK及CNP走实际反向网络，CNP使用既有本地fid投影修复上游未初始化哈希字的缺口。旧反馈保留在同一连接上，路由更新不清除QP速率或序号。live_ecmp只是本地存活候选过滤，不是拥塞感知选路、包喷洒或源端自动换NIC。', '',
        '阿里缓存版本3e0c7c1也已有ECMP、PFC、ECN和多种QP拥塞控制算法。源码存在不等于本项目全部已接入、校准或能公平比较；本项目现代选择重传入口使用显式Figure6/7 DCQCN参考，不能直接宣称可切换HPCC。', '',
        '研究去留：①固定连接哈希和共享链路需求应进入通用强基线；下一步若精简模型仍选错配置，才研究哪些反馈/到达/依赖信息不可省。②仿真加速须证明实际包/队列/反馈工作可以减少，并对照优化后的完整流程；不能以省去连接身份造成的错误换速度。这两条目前仍是待验证研究问题。', '',
        f"独立核验通过：{verification['passed']}；实际旧连接与原生请求需求核对{verification['native_connections']}个连接、{verification['native_remote_operations']}项远端操作；最短路径枚举{verification['path_queries']}组，双哈希核对{verification['hash_checks']}次。四类篡改均拒绝。", '',
        '固定平台1c94423；来源清单和源码hash见observed/sources.json，预测不读truth。完整原件与恢复包留runs，Git只保存代码、协议与小摘要。总体研究目标与两项论文创新尚未完成。']
    markdown = '\n'.join(lines)+'\n'
    (output/'report.md').write_text(markdown)
    # Render the bounded Markdown subset without external packages or scripts.
    fragments, table = [], False
    for line in lines:
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if all(set(c) <= set('-: ') for c in cells): continue
            if not table:
                fragments.append('<div class="scroll"><table>'); table = True
                tag = 'th'
            else: tag = 'td'
            fragments.append('<tr>'+''.join('<'+tag+'>'+html.escape(c)+'</'+tag+'>' for c in cells)+'</tr>')
        else:
            if table: fragments.append('</table></div>'); table = False
            if line.startswith('# '): fragments.append('<h1>'+html.escape(line[2:])+'</h1>')
            elif line: fragments.append('<p>'+html.escape(line)+'</p>')
    if table: fragments.append('</table></div>')
    body = '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>多路径预测边界核查</title><style>body{max-width:1100px;margin:40px auto;padding:0 24px;font:16px/1.8 system-ui;color:#203040;background:#f6f8fa}table{width:100%;border-collapse:collapse;background:white;font-size:14px}td,th{padding:9px 12px;text-align:left;border-bottom:1px solid #dce2e8}th{background:#e8f0fa}.scroll{overflow-x:auto;margin:20px 0}h1{font-size:28px}p{max-width:95ch}</style>'+''.join(fragments)+'</html>'
    (output/'report.html').write_text(body+'\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action',choices=('prepare','freeze','report'))
    p.add_argument('--repository',type=Path)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path)
    a = p.parse_args()
    if a.action == 'prepare': prepare(a.repository.resolve(),a.run.resolve())
    elif a.action == 'freeze': freeze(a.run.resolve())
    else: report(a.run.resolve(),a.output.resolve())


if __name__ == '__main__': main()
