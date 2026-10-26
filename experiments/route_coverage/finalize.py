"""Publish compact evidence and a Chinese report, without network access."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil

from experiments.route_coverage.catalog import plan, save


def load(path):
    return json.loads(path.read_bytes())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(parents=True, exist_ok=False)
    source = load(a.manifest)
    coverage = load(a.stage/'analysis/coverage.json')
    traffic = load(a.stage/'analysis/traffic-demand.json')
    access = load(a.stage/'catalog/access.json')
    catalogs = load(a.stage/'catalog/catalog-status.json')
    selection = plan(catalogs, {f['original_path'] for f in source['files']})
    assert access['raw_probe']['downloaded_route_files'] == 0
    assert coverage['request_files'] == 4 and load(a.stage/'verification.json')['passed']
    for origin, dest in [('analysis/coverage.json', 'coverage.json'),
                         ('analysis/traffic-demand.json', 'traffic-demand.json'),
                         ('verification.json', 'verification.json'),
                         ('catalog/access.json', 'access.json'),
                         ('catalog/download-urls.txt', 'download-urls.txt')]:
        shutil.copyfile(a.stage/origin, a.output_dir/dest)
    # Rebuild from saved metadata, not by repeating HTTP requests.
    original_plan = load(a.stage/'catalog/download-plan.json')
    assert selection == original_plan | {'fits_download_byte_budget': None}
    save(a.output_dir/'download-plan.json', selection)
    save(a.output_dir/'sources.json', dict(dataset_id=source['source']['dataset_id'],
         revision=source['source']['dataset_revision'], collector_model=source['source']['collector_model_id'],
         cached_visible_listing=dict(path='runs/moe_trace_dataset_review_20261018/dataset-api.json',
             sha256='d6e25aa5d6d583d1dae72f78e010abad5839e8a85b3a53f0715ceba0178eb3c5',
             complete=False, siblings=100000),
         groups={g: dict(url=c['url'], status=c['status'], complete=c['complete'],
                         visible_candidates=len(c['files'])) for g, c in catalogs.items()},
         original_manifest=dict(path=str(a.manifest), sha256=hashlib.sha256(a.manifest.read_bytes()).hexdigest()),
         routes=coverage['requests'], new_raw_files=0, new_raw_bytes=0))
    peak = [row['decode']['peak_to_mean'] for row in coverage['layers']]
    table = []
    for b in traffic['batches']:
        if b['state'] == 'offline_demand_only':
            stats = b['dispatch_payload_bytes']
            table.append(f"| {b['batch']} | {b['application_waves']:,} | {stats['median']/1024:g} | {stats['p95_nearest_rank']/1024:g} | {stats['maximum']/1024:g} |")
    groups = Counter(r['group'] for r in selection['files'])
    report = f'''# 更多真实路由数据：当前覆盖、缺口和扩充办法

当前确实需要增加独立请求。已将“原先只有四条路由”的理解改为准确口径：**当前有四个可读取的请求文件，覆盖完整58个MoE层及每请求128个Decode步骤**。这些内容足以扩展跨层和时间变化分析，但不足以形成大量不同请求的batch和独立验证。本轮没有取得新原件；实际新增为零。

## 1. 当前真正拥有多少数据

| 指标 | 当前可读原件 |
|---|---:|
| 不同请求文件 | {coverage['request_files']} |
| MoE层 | {coverage['moe_layers']} |
| Prefill token，四请求合计 | {coverage['prefill_tokens']} |
| Decode token步骤，四请求合计 | {coverage['decode_token_steps']} |
| Prefill token-layer路由行 | {coverage['prefill_token_layer_rows']:,} |
| Decode token-layer路由行 | {coverage['decode_token_layer_rows']:,} |
| 总路由行 | {coverage['total_token_layer_rows']:,} |
| 专家选择次数 | {coverage['total_expert_choices']:,} |
| 原始文件总大小 | {coverage['input_bytes']:,} B |

一条路由行指一个token在一个MoE层选择的Top8专家。同一请求的不同层/步骤有关联，不能把38,338行称作38,338个独立请求，也不能把连续token拼成并发请求。来源固定R1/AWQ；不表示已经获得GPU时间、真实到达时间或实测网络包。原件SHA256与完整格式通过。

仓库另有历史1,024请求清单，但当前逐一检查其引用路径，存在原件为0。清单保留了过去的来源记录，不等于本机当前可复算的数据。未声称已穷举本机其他备份位置，也未重做历史实验。

## 2. 这次分析得到什么

按全部512个Decode token步骤汇总，各层专家访问峰均比为 **{min(peak):g}～{max(peak):g}**，最偏斜为原始层32。该比值比较一层内256个专家，均值包含零访问专家；它不是网络拥塞比或可优化比例。只有四个请求，也没有新的均匀/预测对照，不能作为论文新发现。它提示后续不宜只依赖一层的结果。

四个Prefill长度为105、28、10、6。最长请求占全部Prefill token的70.47%，因此混合Prefill统计会受长请求支配；后续需要同时报告逐请求结果与token加权结果，并扩大长度范围。

声明四个目的rank、专家连续等容量放置、请求依清单序号分配源rank、BF16参考激活（7168×2字节）。每token发往同一远端rank的向量去重，仅计算Dispatch数据。batch为每EP组请求数；增加batch也改变活动源数量，不能直接当作固定源数量的batch因果实验。

| 每EP组请求数 | 层×步骤×请求组波次 | Dispatch中位数 KiB | P95 KiB | 最大 KiB |
|---|---:|---:|---:|---:|
{chr(10).join(table)}

中位数采用下中位顺序统计量，P95采用nearest-rank。不同batch档都重复使用同一批四请求做对照，各档内请求组不重叠；波次计数不是新增请求。以上不含控制、ACK、包头、Combine、专家计算和排队，没有运行新的包级网络。batch=8/16/32/64因为缺少不同请求而明确不可用，没有复制补数。这些字节是按目的rank去重后的需求，不能用Top8逐专家复制公式替代。

## 3. 已经准备的扩充清单

先扩展64个原始请求候选，不全量下载数据集：

| 来源子树 | 候选数 | 用途 |
|---|---:|---|
| 英文MMLU / computer_security | {groups['mmlu/computer_security']} | 同类任务中的不同请求 |
| 英文MMLU / econometrics | {groups['mmlu/econometrics']} | 不同学科的专家偏好 |
| 中文MMLU / international_law | {groups['mmlu_ZH_CN/international_law']} | 中文任务覆盖，不能单独归因于语言 |
| livecodebench / execution | {groups['livecodebench/execution']} | 代码任务覆盖 |

固定数据集修订、原始路径和seed，按路径hash选择，不按热度挑选；各组前8个划为开发、后8个划为留出候选，共32/32。旧数据重叠、题目翻译及同源任务需先排查，尚未证明统计独立性和严格留出身份。

下载链接见[download-urls.txt](download-urls.txt)，选择与划分见[download-plan.json](download-plan.json)。建议按README的完整相对路径放入 `data/moe-trace-expanded/<revision>/`。本轮获取预算为128MiB、每文件最多8MiB；因为目录未取得大小，**不能保证64份全部符合该预算**，不擅自扩大限额或挑更小文件凑数。

## 4. 为什么本轮尚未新增

本轮4次Hugging Face公开目录请求、1次原始文件请求均返回连接错误（URLError），不是已确认的HTTP权限拒绝。没有重试、换镜像、使用凭证或接受条款。候选回退到已核hash的历史可见目录；该目录恰好100000条siblings，完整性未证实，且没有远端大小/内容摘要。当前清单只是可供下载和后续核验的计划，不能称新增64份原件已校验。

下一步在取得原件和固定版本目录/摘要后，逐份校验完整性、去重并生成manifest，再用同一分析命令处理。坏文件整份拒绝，先报告实际合格数，不自动换样。64份有效数据具备后，再按结果和预算决定256/1024请求池；后两档不在本轮执行范围。

## 5. 与网络研究的联系

更多请求首先用于检验热点是否跨请求/跨任务稳定，再组成不同真实batch，在相同拓扑、放置、控制与恢复基线下检验网络瓶颈和收益。离线分布分析不能直接给出队列、拥塞、时延或优化收益，必须回到共享网络状态下的实际执行。

当前现代原生入口仍是2～4个活动rank、每rank最多16token、每输入最多4次调用。64个请求池不等于64活动rank，更不代表千卡现代完整调用通过。本阶段只扩大证据覆盖与准备输入；网络执行扩展、GPU校准和论文主创新仍未完成。

独立核验重数了全部原件及51,968个声明需求波次，确定性复算一致，7项微型/非法输入检查通过，见[verification.json](verification.json)。本轮零新网络仿真、零模型执行；实际命令和资源记录位于 `runs/route_coverage_20261026/*-monitor/`。
'''
    (a.output_dir/'report.md').write_text(report)
    probes = [load(path) for path in a.stage.glob('*-monitor/probe.json')]
    assert all(v['status'] == 'completed' and v['child_exit_code'] == 0 and not v['remaining_live_group_pids'] for v in probes)
    seconds = sum(v['wall_seconds'] for v in probes)
    assert seconds+60 < 600
    total = sum(f.stat().st_size for root in (a.stage, Path(__file__).parent)
                for f in root.rglob('*') if f.is_file())
    assert total+2*2**20 < 512*2**20
    save(a.output_dir/'closeout.json', dict(state='delivered_with_expansion_unavailable',
         new_raw_files=0, network_simulations=0, http_requests=5, http_retries=0,
         completed_monitored_commands=len(probes), monitored_seconds_before_finalizer=seconds,
         finalizer_reserved_seconds=60, maximum_stage_seconds=600,
         bytes_before_closeout=total, reserved_receipt_bytes=2*2**20, maximum_stage_bytes=512*2**20,
         preserved_setup_failure='catalog.stderr.log: /usr/bin/time absent; no HTTP executed by that command',
         access_state='connection_error_not_confirmed_http_authorization_failure',
         platform_modified=False, dependencies_modified=False, pushed=False))
    print('Delivered route coverage, 64-file unacquired plan, independent checks and report.')


if __name__ == '__main__':
    main()
