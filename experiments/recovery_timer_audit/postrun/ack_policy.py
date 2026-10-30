"""Audit ACK cadence from an already acquired RXE response; no HTTP or simulation."""
import argparse
import base64
import hashlib
import html
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[3]
BLOB = 'c11ab280551a8638e1d9c8fa33f8bc9fc743c020'
REFERENCE = 'https://github.com/torvalds/linux/blob/v6.12/drivers/infiniband/sw/rxe/rxe_resp.c'


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def excerpt(path, first, last):
    lines = path.read_text().splitlines()
    return dict(path=str(path.relative_to(ROOT)), sha256=sha(path), first_line=first,
                last_line=last, text='\n'.join(lines[first-1:last]))


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--delivery', type=Path, required=True)
    parser.add_argument('--response', type=Path, required=True)
    args = parser.parse_args()
    stage, out = args.stage.resolve(), args.delivery.resolve()
    response = args.response.resolve()
    assert response.stat().st_size <= 200000
    api = load(response)
    assert api['sha'] == BLOB and api['encoding'] == 'base64'
    assert api['path'] == 'drivers/infiniband/sw/rxe/rxe_resp.c'
    raw = base64.b64decode(api['content'])
    assert len(raw) == api['size']
    assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == BLOB
    source = stage/'postrun-ack'
    source.mkdir(exist_ok=False)
    shutil.copyfile(response, source/'response.json')
    (source/'rxe_resp.c').write_bytes(raw)
    previous = stage/'before-ack-policy'
    previous.mkdir(exist_ok=False)
    for name in ('sources.json', 'README.md', 'report.html', 'receipt.json'):
        shutil.copyfile(out/name, previous/name)
    excerpts = {
        'adapter_requires_ack_each_packet': excerpt(ROOT/'backend/rdma/persistent_streams.cc', 66, 71),
        'selective_feedback_unconditional': excerpt(ROOT/'backend/rdma/persistent_streams.cc', 395, 405),
        'RXE_request_bit': excerpt(stage/'input/sources/rxe_req.c', 448, 455),
        'RXE_ordinary_ack': excerpt(source/'rxe_resp.c', 1218, 1233),
        'RXE_duplicate_ack': excerpt(source/'rxe_resp.c', 1275, 1285),
    }
    assert 'm_ack_interval==1' in excerpts['adapter_requires_ack_each_packet']['text']
    assert 'RdmaEnqueueHighPrioQ(response)' in excerpts['selective_feedback_unconditional']['text']
    assert 'RXE_END_MASK' in excerpts['RXE_request_bit']['text']
    assert 'else if (bth_ack(pkt))' in excerpts['RXE_ordinary_ack']['text']
    record = dict(name='rxe_resp', reference=REFERENCE,
        url='https://api.github.com/repos/torvalds/linux/contents/drivers/infiniband/sw/rxe/rxe_resp.c?ref=v6.12',
        git_blob_sha1=BLOB, path=str((source/'rxe_resp.c').relative_to(ROOT)),
        sha256=sha(source/'rxe_resp.c'), bytes=len(raw), response_sha256=sha(response),
        post_result_source=True, additional_requests=1, request_deadline_seconds=5)
    value = dict(passed=True, post_result_diagnosis=True, source_sha256=sha(Path(__file__)),
        public_source=record, excerpts=excerpts,
        conditional_example=dict(logical_message_bytes=6000, assumed_path_MTU_bytes=4096, assumed_work_requests=1,
            operation='ordinary successful RC WRITE', assumed_packets=2,
            first_packet_requests_ACK=False, last_packet_requests_ACK=True, last_packet_lost=True,
            assumes_no_periodic_ACK_request_threshold=True, assumes_no_error_or_duplicate=True,
            conclusion='No ordinary ACK for first packet solely on this receive path; timer reset event may be absent.',
            target_library_WQE_mapping_verified=False, mlx5_behavior_verified=False),
        new_networks=0, protocol_changed=False, hardware_calibrated=False)
    save(out/'ack_policy.json', value)
    sources = load(out/'sources.json')
    assert not any(row['name'] == 'rxe_resp' for row in sources['sources'])
    sources['sources'].append(record)
    sources.update(total_source_requests=6, total_preflight_deadline_upper_bound_seconds=58,
        post_result_addition='receiver ACK source imported after frozen original analysis; original manifest retained in input/sources')
    (out/'sources.json').write_text(json.dumps(sources, indent=2)+'\n')
    text = """# ACK节奏是当前恢复结论的重要适用条件

当前持久通信适配器要求 `m_ack_interval==1`；选择性接收路径在每次处理DATA后生成真实反馈包，经实际NIC和网络返回。它不是可任意调低ACK频率的通用RoCE实现。反馈生成、成功发出和源收到是三件事，ACK本身仍会排队或丢失。

Linux v6.12 RXE发送端在消息结束或累计计数条件满足时设置请求ACK位。接收端对普通成功RC SEND/WRITE，依据这个位发送ACK；错误、原子操作和重复请求另有处理。因此，不能把当前逐包反馈视为所有RDMA设备的默认行为。

例如：**假设**一个6,000B的说明性普通WRITE采用4,096B路径MTU，分为两包，首包不是WQE末包且未达到周期请求条件，尾包请求ACK。若尾包丢失、只有首包成功到达且没有错误或重复处理，所核查的RXE普通成功接收分支不会仅因首包到达而回ACK。当前模型采用的逐包反馈规则会生成首包反馈；这条反馈若到源并推进累计确认，就可能重启缺口计时。

| 条件 | 当前参考模型 | 上述有条件的RXE例子 |
|---|---|---|
| 首包成功、尾包丢失 | 首包生成反馈 | 首包未请求ACK时，不走普通成功ACK发送分支 |
| 源端部分确认 | 取决于反馈是否实际到达 | 该次部分ACK可能不存在 |
| 尾段何时重试 | 按本模型的ACK进展与计时器执行 | 还需核对实际WQE/PSN、请求计数和计时状态机 |

这里的“6,000B、两包、一个WRITE”是解释机制差别的条件算例，不是从真实专家选择JSON中测出的RDMA操作。数据集提供专家选择，不能给出GPU通信库如何拆分WQE、网卡ACK聚合或固件计时。现有14,464B模型消息并不自动等于一个硬件WRITE，模型的9,000B数据片长也不能直接充当RXE路径MTU。

这不会推翻已有仿真记录：本模型确实出现部分ACK后晚重试，独立包记录也支持该事实。但它限制了向硬件和新协议收益的外推。下一步若研究尾部恢复，应先固定目标库/操作及ACK规则，并使用成熟恢复基线；不能把换成更有利的ACK规则后变快叫作MoE创新。Linux RXE是软件参考，尚未验证现代DeepEP或mlx5的相同执行。

原文逐段、源码哈希、固定Linux版本和条件假设见[ack_policy.json](ack_policy.json)，接收端原文见[Linux v6.12 RXE](https://github.com/torvalds/linux/blob/v6.12/drivers/infiniband/sw/rxe/rxe_resp.c)。本项只使用已经取得的第六份HTTP响应，无新增请求、网络仿真或协议修改。原始分析冻结不变，补充来源单列记录。
"""
    (out/'ack_policy_explanation.md').write_text(text)
    summary = '当前路径强制逐包ACK；RXE普通成功收包按请求位反馈。尾部重试现象依赖ACK与WQE映射，尚不能当作商用RoCE网卡事实。'
    readme = (out/'README.md').read_text()
    readme = readme.replace('选择性重传参考反馈及当前模型配置', '持久适配器强制逐包ACK，选择性路径逐包生成反馈')
    readme += '\n\n**ACK适用条件：** '+summary+' 见[完整说明](ack_policy_explanation.md)。\n'
    (out/'README.md').write_text(readme)
    report = (out/'report.html').read_text()
    report = report.replace('选择性重传参考反馈及当前模型配置', '持久适配器强制逐包ACK，选择性路径逐包生成反馈')
    report = report.replace('<h2>完整重传批次', '<div class="box"><strong>'+html.escape(summary)+
        '</strong><p><a href="ack_policy_explanation.md">ACK规则与条件算例</a> · <a href="ack_policy.json">逐段源码证据</a></p></div><h2>完整重传批次', 1)
    report = report.replace('</html>', '<h2>ACK规则：可复现不等于硬件已校准</h2><pre>'+html.escape(text)+'</pre></html>')
    (out/'report.html').write_text(report)
    # A receipt must describe the final delivery; retain the prior one, do not leave it stale.
    (out/'receipt.json').unlink()
    print(json.dumps(dict(passed=True, source_git_blob=BLOB, total_HTTP_requests=6, new_networks=0)))


if __name__ == '__main__':
    main()
