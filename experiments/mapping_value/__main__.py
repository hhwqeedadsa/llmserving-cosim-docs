"""Run with python -m experiments.mapping_value from the repository root."""
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description='端网映射价值实验：独立输入、公开运行、受限分析与评价')
    commands = parser.add_subparsers(dest='action', required=True)
    run = commands.add_parser('run-small', help='运行固定小场景正常组，校验并保存首轮数据包')
    run.add_argument('--output', type=Path, required=True, help='尚不存在的实验目录')
    run.add_argument('--backend', type=Path, required=True, help='已构建的联合后端')
    full = commands.add_parser('prepare-full',help='核验完整来源、生成独立重放配置并检查累计磁盘；不展开图、不运行仿真')
    full.add_argument('--slice',type=Path,required=True)
    full.add_argument('--backend',type=Path,required=True)
    full.add_argument('--output',type=Path,required=True)
    freeze = commands.add_parser('freeze-small', help='仅根据正常参考冻结小场景干预，不运行异常组')
    freeze.add_argument('--baseline', type=Path, required=True)
    freeze.add_argument('--output', type=Path, required=True)
    followups = commands.add_parser('freeze-followups', help='按正常来源冻结业务/状态窗口对照及条件性重叠对照')
    for name in ('baseline','endpoint-run','output'):
        followups.add_argument('--'+name,type=Path,required=True)
    case = commands.add_parser('run-case', help='执行一份已冻结干预；网络变化重放图，端侧变化重新构图')
    for name in ('baseline','case','output','backend'):
        case.add_argument('--'+name, type=Path, required=True)
    prepare = commands.add_parser('prepare', help='从运行与正常参考生成 E1–E5 及状态关系对照的受限输入')
    prepare.add_argument('--run', type=Path, required=True)
    prepare.add_argument('--reference', type=Path, required=True)
    prepare.add_argument('--config', type=Path, required=True, help='版本化实验选择 JSON')
    prepare.add_argument('--output', type=Path, required=True)
    prepare.add_argument('--reference-prepared',type=Path,help='已验证准备目录；硬链接复用相同正常投影，避免重复存储')
    analyze = commands.add_parser('analyze', help='只读取一个受限输入目录，输出候选与依据')
    analyze.add_argument('--input', type=Path, required=True)
    analyze.add_argument('--output', type=Path, required=True)
    validate = commands.add_parser('validate-input', help='检查受限目录、字段、引用及范围')
    validate.add_argument('--input', type=Path, required=True)
    evaluate = commands.add_parser('evaluate', help='独立使用内部记录评价成对运行及候选范围')
    for name in ('run','reference','analysis','case','config','prepared','output'):
        evaluate.add_argument('--'+name, type=Path, required=True)
    assess = commands.add_parser('assess', help='一次完成准备、所选信息条件分析和成对评价，保留逐条命令日志')
    for name in ('run','reference','case','config','output'):
        assess.add_argument('--'+name,type=Path,required=True)
    assess.add_argument('--conditions',nargs='+',default=['E1','E2','E3'],
                        choices=['E1','E2','E3','E4','E5','E3S','E5S'])
    assess.add_argument('--reference-prepared',type=Path,help='复用相同参考及设计的既有准备目录')
    explain = commands.add_parser('explain',help='评价用：检查局部后移及指定层真实业务/状态就绪尾段')
    for name in ('run','reference','output'):
        explain.add_argument('--'+name,type=Path,required=True)
    explain.add_argument('--layer-id',type=int,required=True)
    explain.add_argument('--kind',choices=['dispatch','combine'],required=True)
    report = commands.add_parser('report',help='核对并汇总同一分析版本的多个成对评价，不运行仿真')
    report.add_argument('--assessment',type=Path,action='append',required=True)
    report.add_argument('--output',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'run-small':
            from .workflow import small_baseline
            result = small_baseline(args.output, args.backend)
        elif args.action == 'prepare-full':
            from .full import prepare_full
            result = prepare_full(args.slice,args.backend,args.output)
        elif args.action == 'freeze-small':
            from .cases import freeze_small
            result = freeze_small(args.baseline,args.output)
        elif args.action == 'freeze-followups':
            from .followups import freeze_followups
            result = freeze_followups(args.baseline,args.endpoint_run,args.output)
        elif args.action == 'run-case':
            from .cases import run_case
            result = run_case(args.baseline,args.case,args.output,args.backend)
        elif args.action == 'prepare':
            from .prepare import prepare
            result = prepare(args.run,args.reference,args.config,args.output,args.reference_prepared)
        elif args.action == 'analyze':
            from .analyze import analyze
            result = analyze(args.input,args.output)
        elif args.action == 'validate-input':
            from .inputs import validate
            _,manifest = validate(args.input)
            result = dict(passed=True,condition=manifest['condition'])
        elif args.action == 'assess':
            from .assess import assess
            result = assess(args.run,args.reference,args.case,args.config,args.conditions,args.output,args.reference_prepared)
        elif args.action == 'explain':
            from .effects import explain
            result = explain(args.run,args.reference,args.layer_id,args.kind,args.output)
        elif args.action == 'report':
            from .report import report
            result = report(args.assessment,args.output)
        else:
            from .evaluate import evaluate
            result = evaluate(args.run,args.reference,args.analysis,args.case,args.config,args.prepared,args.output)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        print(f'mapping-value: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
