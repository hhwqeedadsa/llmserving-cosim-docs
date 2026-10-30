"""Collect saved watchdog evidence, then close resources offline; no networks."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RUN = ROOT/'runs/source_ack_watchdog_20261030'


def load(path):return json.loads(path.read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, value):
    with path.open('x') as stream:json.dump(value, stream, ensure_ascii=False, indent=2);stream.write('\n')


def monitors(include_collector):
    paths = sorted((RUN/'monitors').glob('*/probe.json'))
    if not include_collector:paths = [p for p in paths if p.parent.name != 'evidence']
    paths += [RUN/mode/'execution/probe.json' for mode in ('normal', 'local')]
    rows = {str(path.relative_to(RUN)):load(path) for path in paths}
    assert all(r['status'] == 'completed' and r['child_exit_code'] == 0 and
               not r['remaining_live_group_pids'] and r.get('ended_utc') for r in rows.values())
    assert sum(r['wall_seconds'] for r in rows.values()) < 900
    return rows


def links():
    result = []
    for path in [HERE/'README.md', HERE/'report.html', ROOT/'docs/source_ack_watchdog.md',
                 ROOT/'examples/scaleout_source_ack_watchdog/README.md']:
        hrefs = re.findall(r'\]\(([^)]+)\)', path.read_text())
        hrefs += re.findall(r'href="([^"]+)"', path.read_text())
        for href in hrefs:
            url = urlsplit(href)
            if url.scheme or not url.path:continue
            target = (path.parent/unquote(url.path)).resolve()
            assert target.is_file() or target in [HERE/'resources.json', HERE/'final-audit.json']
            result.append(str(target.relative_to(ROOT)))
    return result


def collect():
    verification = load(RUN/'independent/verification.json')
    assert verification['passed'] and len(verification['negative_checks']) == 7
    assert len(verification['native_pre_fabric_rejections']) == 4
    assert 'Ran 69 tests' in (RUN/'monitors/final-unit/stderr.log').read_text()
    assert all(sha(Path(name)) == value for name, value in verification['original_hashes'].items())
    build = load(RUN/'build/backend.json')
    assert sha(RUN/'build/ns3.42-moe-rdma-service') == build['service_sha256']
    assert all(sha(ROOT/name) == value for name, value in build['source_sha256'].items())
    old = load(RUN/'build/original-hashes.json')
    assert all(sha(Path(name)) == value for name, value in old.items())
    dep = subprocess.run(['bash','scripts/check_deps.sh'], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert dep.returncode == 0 and 'PASS: checked 13 dependency checkouts' in dep.stdout
    source = ['backend/rdma/service.cc','backend/rdma/source_ack_watchdog.hh','backend/rdma/execution_events.hh',
        'python/moe_ub_cosim/source_ack_watchdog.py','python/moe_ub_cosim/native_control.py',
        'python/moe_ub_cosim/scaleout_service.py','python/moe_ub_cosim/scaleout_verify.py',
        'python/moe_ub_cosim/service_transport.py','tests/unit/source_ack_watchdog_test.py',
        'tests/integration/check_source_ack_watchdog.py','examples/scaleout_source_ack_watchdog/prepare.py',
        'fixtures/source_ack_watchdog/build.py','fixtures/source_ack_watchdog/evidence.py']
    for name in source:
        if name.endswith('.py'):ast.parse((ROOT/name).read_text())
    rows = monitors(False)
    receipt = dict(passed=True, execution_commit=verification['execution_commit'],
        platform_source_sha256={name:sha(ROOT/name) for name in source},
        independent_verification_sha256=sha(RUN/'independent/verification.json'),
        original_run_sha256=verification['original_hashes'], backend=build,
        public_and_negative_commands=load(RUN/'independent/commands.json'),
        monitors_before_collector=rows, dependency_check=dep.stdout,
        distinct_unit_tests=69, new_unit_tests=13, related_unit_tests=56,
        new_network_executions=2, native_input_rejections=4,
        new_service_objects=1, dependency_builds=0, dependency_changes=False,
        new_downloads=0, gpu_executions=0,
        scope='actual source ACK deadlines and cancellation; synthetic functional inputs; no recovery gain')
    shutil.copyfile(RUN/'independent/verification.json', HERE/'verification.json')
    save(HERE/'receipt.json', receipt)
    cases = verification['cases']
    marker = cases['local']['notices'][0]
    assert marker['notification_ns'] == 21030
    ticks = ''
    for label, ns, color, y in [('SEND post',1030,'#275eab',42),('20us期限通知',21030,'#c46a0b',82),
                               ('接收回调',83058,'#087c67',42),('真实源确认',85060,'#8f367d',122)]:
        x = 42+ns/1000*7.5
        ticks += f'<line x1="{x}" y1="150" x2="{x}" y2="{y+10}" stroke="{color}"/><circle cx="{x}" cy="150" r="6" fill="{color}"/><text x="{x}" y="{y}" text-anchor="middle" fill="{color}">{label} {ns/1000:.3f}us</text>'
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>源端确认超时：真实时钟上的在线信号</title><style>body{{font:17px/1.8 system-ui,sans-serif;color:#193149;background:#eef3f8;margin:0;padding:28px}}main{{max-width:1080px;margin:auto;background:white;padding:32px;border-radius:12px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #c9d5df;padding:10px;text-align:left}}.note{{background:#fff3d9;border-left:5px solid #c46a0b;padding:16px}}svg{{width:100%;height:auto}}a{{color:#245f9c}}code{{overflow-wrap:anywhere}}</style>
<main><h1>现在能在运行中知道“这条发送迟迟未确认”</h1>
<p>原生ns-3按实际SEND提交启动一次性期限，Python在仿真运行中收到通知；不用事后丢包日志推断，不读取故障结束时间。</p>
<p class="note">这不是新重传算法，也没有恢复加速收益。通知只说明尚未得到源确认，接收端可能已经得到数据。</p>
<h2>同一128端点Clos的两组功能验证</h2><p>16服务器×8，8ToR/8Agg/4Core，单rail/plane、各层400G、每链路1us；两个EP64组、E256/Top8/H256、两层两步。四份不同人工路由，31数据活跃rank；计算为声明成本，无真实GPU或完整R1。</p>
<table><tr><th>指标</th><th>normal 正常</th><th>local 局部短时故障</th></tr>
<tr><td>实际消息 / 专家贡献</td><td>62 / 128</td><td>相同</td></tr>
<tr><td>确认期限 / 在线通知</td><td>20us / 0次</td><td>20us / 1次</td></tr>
<tr><td>定时器取消 / 最终未确认</td><td>62 / 0</td><td>61 / 0</td></tr>
<tr><td>注入丢包</td><td>0</td><td>1个目的DATA包，前20us仅GPU6受损</td></tr>
<tr><td>完整包事件 / 任务及排空</td><td>2120 / 103.852us</td><td>2126 / 175.778us</td></tr></table>
<p>两组各只运行一次。去掉新增watchdog计数后，完整原生网络结果分别与上一轮对应原件一致；所有任务图、执行事件和贡献记录逐字节一致，没有重跑旧成功网络。</p>
<h2>局部条件中0→6、消息2的实际时间线</h2>
<svg viewBox="0 0 850 205" role="img" aria-label="消息2从1.030微秒提交，21.030微秒通知，83.058微秒接收，85.060微秒源确认"><line x1="42" y1="150" x2="810" y2="150" stroke="#b7c7d6"/>{ticks}<text x="42" y="190">0us</text><text x="775" y="190">100us</text></svg>
<p>通知发生在21.030us，此前已在线交付1142条执行事件。故障注入实际20us已结束，但源端仍不知道本次发送何时确认；通知没有读取这一真值。其后64.030us才源确认，不能直接当作可消除收益。</p>
<h2>检查了什么</h2><p>69项不同CPU检查通过（新增13、相关56）。独立核对原始控制流、post/接收/确认回调及两组原网络结果；迁移检查和旧默认关闭检查通过。7类通知/计数篡改被拒绝，4类原生非法选项在创建网络前拒绝。同纳秒先确认则取消、先期限则通知两种顺序均保留。</p>
<p>新增1个服务对象并隔离链接，复用固定ASTRA archive与原生库；旧对象和程序hash不变。零依赖构建/升级、下载、安装或GPU执行。</p>
<h2>下一步还有什么</h2><p>缺少可以响应通知的在途恢复决策与合法备用执行。须让原/备用任务共享同一GPU/NIC资源，并处理贡献一次提交、迟到结果和原连接退出；之后才能比较成熟备用请求与候选机制的净收益。</p>
<p><a href="README.md">方法与边界</a> · <a href="verification.json">独立核验</a> · <a href="receipt.json">源码和命令</a> · <a href="resources.json">资源</a> · <a href="final-audit.json">收尾检查</a> · <a href="../../docs/source_ack_watchdog.md">公开接口</a></p></main></html>'''
    with (HERE/'report.html').open('x') as stream:stream.write(html)
    links()
    subprocess.run(['git','diff','--check'], cwd=ROOT, check=True)
    print(json.dumps(dict(passed=True, tests=69, normal_notifications=0, local_notifications=1)))


def closeout():
    from moe_ub_cosim.host_probe import disk_usage
    rows = monitors(True)
    assert len(rows) == 11
    receipt = load(HERE/'receipt.json')
    assert all(sha(ROOT/name) == value for name, value in receipt['platform_source_sha256'].items())
    assert all(sha(Path(name)) == value for name, value in receipt['original_run_sha256'].items())
    assert sha(HERE/'verification.json') == receipt['independent_verification_sha256']
    groups = {r['process_group'] for r in rows.values()}
    live = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            fields = (path/'stat').read_text().rsplit(')',1)[1].split()
            if fields[0] != 'Z' and int(fields[2]) in groups:live.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError):pass
    assert not live
    disk = disk_usage(RUN);free = shutil.disk_usage(RUN).free
    assert max(disk.values()) < 512*2**20 and free >= 8*2**30
    save(HERE/'resources.json', dict(passed=True, monitors=rows,
        cumulative_seconds=sum(r['wall_seconds'] for r in rows.values()),
        sampled_peak_group_rss_bytes=max(r['peak_sampled_group_rss_bytes'] for r in rows.values()),
        run_disk_usage=disk, volume_free_bytes=free, remaining_live_group_pids=live,
        new_network_executions=2, new_service_objects=1, dependency_builds=0,
        scope='all authoritative monitors; nested worker durations conservatively summed; relocated copies excluded; read/edit/offline closeout not timed'))
    save(HERE/'final-audit.json', dict(passed=True, source_and_original_hashes_match=True,
        independent_verification_matches=True, terminal_resources_verified=True, links=links(),
        scope='platform source ACK deadline and evidence only; no recovery algorithm gain'))
    print(json.dumps(dict(passed=True, cumulative_seconds=sum(r['wall_seconds'] for r in rows.values()), run_disk_usage=disk)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources', action='store_true')
    args = parser.parse_args()
    closeout() if args.resources else collect()
