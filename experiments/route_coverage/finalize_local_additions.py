"""Publish the inspected user additions; no downloads or executable manifest."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil

from experiments.route_coverage.analyze import save


def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    stage=a.stage;out=a.output_dir;out.mkdir(parents=True,exist_ok=False)
    intake=json.loads((stage/'final-intake/intake.json').read_bytes())
    demand=json.loads((stage/'final-intake/traffic-demand.json').read_bytes())
    verification=json.loads((stage/'verification.json').read_bytes());assert verification['passed']
    for src,dst in [('final-intake/intake.json','intake.json'),('final-intake/traffic-demand.json','traffic-demand.json'),('verification.json','verification.json')]:
        shutil.copyfile(stage/src,out/dst)
    witnesses=[]
    files={f['name']:f for f in intake['files']}
    for index,name in enumerate(demand['eligible_order']):
        rows=json.loads(Path(files[name]['local_path']).read_bytes())
        for lid in range(3,61):
            for entry in range(1,len(rows)):
                choices=rows[entry][str(lid)][0]
                if {e//64 for e in choices}=={index%4}:
                    witnesses.append(dict(file=name,entry_index=entry,layer_id=lid,source_rank=index%4,expert_ids=choices,
                                          dispatch_token_remote_destinations=0))
    assert len(witnesses)==6
    save(out/'local_only_witnesses.json',dict(scope='declared_four_rank_placement_and_sources_not_observed_GPU_locations',
        remote_control_not_eliminated=True,witnesses=witnesses))
    population=intake['populations'];subjects=Counter(f['original_path'].split('/')[-2] for f in intake['files'] if f['eligible_offline'])
    table='\n'.join(f"| {name} | {count} |" for name,count in sorted(subjects.items()))
    demand_table='\n'.join(f"| {row['batch']} | {row['layer_step_group_waves']:,} | {row['dispatch_payload_bytes']['median']/1024:g} | {row['dispatch_payload_bytes']['p95_nearest_rank']/1024:g} | {row['unused_decode_steps']} | {len(row['unused_request_files'])} |"
        for row in demand['batches'] if row['state']=='offline_demand_only')
    text=f'''# 用户新增真实路由：接收结果与用途

2026-10-27。已找到并检查你新增的13份JSON。用户确认全部来自DeepSeek-R1-AWQ的MMLU目录；其中11份结构完整、2份末尾缺层。加上旧4份，当前有**15份内容不重复、结构完整的请求文件**。原件均保留，旧manifest未改。新文件的远端固定修订/内容摘要仍未核验，当前结果是来源由用户说明的离线分析，不冒充已验收的正式网络输入。

## 数据量和覆盖

| 范围 | 完整请求文件 | Prefill token | Decode步骤 | token-layer路由行 | 专家选择次数 |
|---|---:|---:|---:|---:|---:|
| 原先四份 | 4 | 149 | 512 | 38,338 | 306,704 |
| 新增合格文件 | 11 | 544 | 1,299 | 106,894 | 855,152 |
| 当前合计 | 15 | 693 | 1,811 | 145,232 | 1,161,856 |

全部结构按R1的61层/58个MoE层、256专家、Top8核验。以上路由行相互存在请求/层/步骤相关性，不是145,232个独立请求。样本由用户选择，不称随机抽样或严格留出；不同文件和SHA256也不独立证明不同的实机采集运行。

合格文件覆盖六个MMLU学科：

| 学科目录 | 合格文件数 |
|---|---:|
{table}

## 两份缺层文件

| 文件 | 原始数组下标 | 缺失层 |
|---|---:|---|
| 899.json | 127 | 43～60，共18层 |
| 1479.json | 128 | 38～60，共23层 |

这是当前原件中的缺层事实，不能仅凭它断言下载中断还是作者采集不完整。没有填零、补专家或截取合法前缀，两份都整份排除。详细缺失位置和SHA256见[intake.json](intake.json)。

`1004.json`有72个完整Decode入口，`901.json`有75个；其余合格文件为128。短文件完整且有足够观察步骤，可以保留；文件结束不当作EOS。不同长度参与同一batch时，只取共同前缀，未用步骤明确列出。

## 对后续网络实验的帮助

现在能用8个不同的新请求组成一个batch，不必重复原先四个请求。固定四个目的rank、每rank连续64专家、源按合格文件序号模4分配；每token到同一远端rank只发一份7168×2字节向量。它是BF16参考激活声明，不是AWQ权重精度推导出的真实通信dtype。

| 每EP组请求数 | 层×步骤×组波次 | Dispatch中位数 KiB | P95 KiB | 分组内未用Decode步骤 | 未分组请求 |
|---|---:|---:|---:|---:|---:|
{demand_table}

batch8使用900、901、905、1003、1004、1007、1008、1011八份新文件，共同观察72步，覆盖58层。其余7份未进入这个固定分组，339个组内多余步骤未使用；没有静默补齐。batch16需要再增加至少一份结构完整且来源明确的请求，当前没有用缺层文件凑数。

中位数为下中位顺序统计量，P95为nearest-rank。这些数只计算Dispatch token数据，不包括控制、ACK、元数据/包头、Combine或排队；没有运行相应新包级实验。增加batch也改变源分布；多个batch档的样本组合不同，表中差值不能直接归因为batch因果效应。

新增样本中还出现6个单token、单层的本地数据情况：在上述声明源位置和专家放置下，它的Top8专家都在本卡，远端Dispatch token数据为0，见[具体路由见证](local_only_witnesses.json)。这不表示完整MoE调用没有跨卡屏障/计数控制流；不能把全部All-to-All字节机械设为每token×Top8。源位置与专家放置改变后，该现象也可能改变。

## 使用和来源边界

新数据先扩大跨请求、跨学科、跨层及短输出的覆盖，然后在固定网络和较强基线下测试结论；热点直方图和数据量扩大本身不是论文创新。当前并未增加GPU硬件校准、活动rank支持范围或千卡执行证据。

新文件来源目录由用户确认，原路径根据已固定hash的历史可见目录唯一定位。本轮没有取得这些新文件的远端Git blob或LFS摘要，无法独立证明它们来自哪个远端修订。因此只输出本地接收报告，**没有生成伪称固定版本已验证的平台manifest**。旧4份清单继续作为原有可执行来源。后续来源核验须与网络执行完成分开报告。

独立检查重数了全部145,232条合格路由行、1,161,856个专家选择，以及170,810个声明需求波次；缺层文件未进入任何分组，旧manifest保持SHA256不变，两次分析确定性相同。程序/命令见[使用说明](../../README.md#用户新增文件的接收)，核验见[verification.json](verification.json)。
'''
    (out/'report.md').write_text(text)
    probes=[json.loads(p.read_bytes()) for p in stage.glob('*-monitor/probe.json')]
    assert all(p['status']=='completed' and p['child_exit_code']==0 for p in probes)
    seconds=sum(p['wall_seconds'] for p in probes);assert seconds+60<180
    receipt=dict(completed=True,monitored_seconds_before_finalizer=seconds,finalizer_reserved_seconds=60,
        stage_seconds_limit=180,stage_bytes_limit=128*2**20,
        new_files_received=13,new_structure_valid_files=11,new_remote_revision_verified_files=0,
        formal_network_manifest_created=False,network_simulations=0,old_manifest_unchanged=True,
        user_source_confirmation='All 13 from cognitivecomputations/DeepSeek-R1-AWQ/mmlu',
        sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()})
    save(out/'receipt.json',receipt)
    print('Published 15-file local coverage; 11 new complete, 2 rejected; no new network input identity claim.')


if __name__=='__main__':main()
