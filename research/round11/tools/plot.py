#!/usr/bin/env python3
"""Static scientific figures from verified packet/queue/phase evidence."""
import json
from pathlib import Path
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np
import pandas as pd
from analyze import bandwidth, sample_queue, HORIZON, GROUPS

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"visualizations"
COLORS={"G0":"#2563eb","G1":"#159b81","G2":"#8b5cf6","KV":"#f97316"}
OPS={"AllReduce":"#8b5cf6","AllGather":"#2563eb","ReduceScatter":"#159b81","KV-proxy":"#f97316"}
LABELS={"no_kv_100":"A：无 KV / 100 Gbps","kv_100":"B：加入 KV / 100 Gbps","kv_200":"C：加入 KV / 200 Gbps"}
mpl.rcParams.update({"font.family":"sans-serif","font.sans-serif":["WenQuanYi Micro Hei","DejaVu Sans"],
    "axes.unicode_minus":False,"svg.fonttype":"none","font.size":11,"figure.dpi":160})

def save(fig,name,footer,top=.90,bottom=.14):
    fig.text(.08,.016,footer,fontsize=9,color="#475569",va="bottom")
    fig.subplots_adjust(left=.09,right=.98,bottom=bottom,top=top,hspace=.42)
    for ext in ["svg","png"]:
        fig.savefig(OUT/f"{name}.{ext}",facecolor="white")
    plt.close(fig)

def style(ax,xlim):
    ax.set_xlim(*xlim); ax.grid(axis="both",alpha=.18,zorder=0)
    ax.spines[["top","right"]].set_visible(False)
    ax.ticklabel_format(axis="x",style="plain",useOffset=False)

def stacked(ax,b,direction,capacity):
    b=b[b.direction==direction]
    pivot=b.pivot(index="start_us",columns="group",values="gbps").reindex(columns=GROUPS).fillna(0)
    x=np.append(pivot.index.to_numpy(),b.end_us.max())
    floor=np.zeros(len(x))
    for g in GROUPS:
        y=np.append(pivot[g].to_numpy(),pivot[g].iloc[-1])
        ax.fill_between(x,floor,floor+y,step="post",color=COLORS[g],label=g,linewidth=0,alpha=.88)
        floor+=y
    ax.axhline(capacity,color="#64748b",ls="--",lw=1)
    ax.set_ylim(0,capacity*1.14); ax.set_ylabel("数据包带宽 (Gbps)")

def main():
    OUT.mkdir(exist_ok=True)
    s=pd.read_csv(ROOT/"analysis/summary.csv").set_index("case_id")
    base=ROOT/"analysis/kv_100"
    b=pd.read_csv(base/"bandwidth_5us.csv")
    q=pd.read_csv(base/"queue_1us.csv")
    qe=pd.read_csv(base/"queue_events.csv")
    p=pd.read_csv(base/"packets.csv")
    phases=pd.read_csv(base/"phases.csv")
    events=pd.read_csv(base/"events.csv")
    # Predeclared diagnostic choice: largest settled queue in the middle 1000..6000 us.
    middle=qe[(qe.direction=="L2R")&(qe.time_us>=1000)&(qe.time_us<=6000)]
    peak=middle.loc[middle.kib.idxmax()]
    left=float(np.floor((peak.time_us-160)/10)*10); right=left+500
    zoom=bandwidth(p,100,width=.25,begin=left,end=right)
    zoomq=sample_queue(qe,width=.25,begin=left,end=right)
    zoom.to_csv(base/"zoom_bandwidth_025us.csv",index=False)
    zoomq.to_csv(base/"zoom_queue_025us.csv",index=False)
    (base/"zoom_selection.json").write_text(json.dumps(dict(rule="largest settled L2R queue within 1000..6000 us",
        start_us=left,end_us=right,peak_time_us=float(peak.time_us),peak_kib=float(peak.kib)),indent=2)+"\n")

    fig,axes=plt.subplots(3,1,figsize=(12.5,9),sharex=True)
    fig.suptitle("多组推理通信 + KV：8400 µs 内的连续波峰、波谷与队列",fontsize=17,y=.97)
    for ax,direction,node in [(axes[0],"L2R",8),(axes[1],"R2L",9)]:
        stacked(ax,b,direction,100)
        ax.set_title(f"{direction} · switch {node} / port 4 · 5 µs 分箱",loc="left",fontsize=11)
        ax.axvspan(left,right,color="#0f172a",alpha=.06)
        style(ax,(0,HORIZON))
    axes[0].legend(ncol=4,loc="upper right",frameon=False)
    for direction,color in [("L2R","#ea580c"),("R2L","#475569")]:
        z=q[q.direction==direction]
        axes[2].plot(z.time_us,z.kib,lw=1,color=color,label=direction)
    axes[2].set_title("核心出口 VOQ + egress 队列 · 1 µs 采样",loc="left",fontsize=11)
    axes[2].set_ylabel("队列占用 (KiB)"); axes[2].legend(frameon=False)
    axes[2].set_xlabel("仿真时间 (µs)"); style(axes[2],(0,HORIZON))
    save(fig,"dependent-long-window",
        "来源：ns-3 AllPacketTrace + PortTrace + QueueTrace；不是实机测量。带宽含数据包头，不含 ACK/control。\n"
        "G0/G1/G2：依赖驱动的 Block24 模板重放；KV/初始到达：合成输入。灰带对应局部放大范围；未做平滑。")

    fig,axes=plt.subplots(3,1,figsize=(12.5,8.5),sharex=True,sharey=True)
    fig.suptitle("同一推理负载：KV 干扰与核心带宽对照",fontsize=17,y=.97)
    for ax,name in zip(axes,LABELS):
        data=pd.read_csv(ROOT/f"analysis/{name}/bandwidth_5us.csv")
        cap=float(s.loc[name,"core_gbps"])
        stacked(ax,data,"L2R",cap)
        ax.set_ylim(0,225)
        ax.set_title(f"{LABELS[name]}  |  推理重放结束 {s.loc[name,'inference_end_us']:.1f} µs"
                     f"  |  通信阶段 p95 {s.loc[name,'phase_p95_us']:.1f} µs",loc="left",fontsize=11)
        ax.axvline(s.loc[name,"inference_end_us"],color="#0f172a",ls=":",lw=1)
        style(ax,(0,HORIZON))
    axes[0].legend(ncol=4,loc="upper right",frameon=False)
    axes[-1].set_xlabel("仿真时间 (µs)")
    save(fig,"controlled-comparison",
        "观测点：switch 8 / port 4（L→R）；来源：ns-3 逐包 trace，5 µs 分箱。虚线为链路容量，竖点线为推理重放结束。\n"
        "通信阶段 p95：120 个阶段（含 AllReduce 内部两步）的 start→task-complete 时长；不是请求时延。")

    fig,axes=plt.subplots(3,1,figsize=(13,10),sharex=True,gridspec_kw={"height_ratios":[1,1,2]})
    fig.suptitle(f"竞争波峰放大：{left:.0f}–{right:.0f} µs · 100 Gbps + KV",fontsize=16,y=.97)
    stacked(axes[0],zoom,"L2R",100)
    axes[0].set_title("switch 8 / port 4：各组数据带宽 · 0.25 µs 分箱",loc="left",fontsize=11)
    axes[0].legend(ncol=4,loc="upper right",frameon=False)
    for direction,color in [("L2R","#ea580c"),("R2L","#475569")]:
        z=zoomq[zoomq.direction==direction]
        axes[1].plot(z.time_us,z.kib,color=color,label=direction)
    axes[1].set_ylabel("队列占用 (KiB)"); axes[1].legend(frameon=False)
    axes[1].set_title("对应的两个方向出口队列 · 同刻最后状态，0.25 µs 采样",loc="left",fontsize=11)
    ax=axes[2]
    labels=[]
    for group in range(3):
        for kind in ["compute","communication"]:
            lane=len(labels); resource=f"GPU{group}" if kind=="compute" else f"NIC{group}->{group+4}"
            labels.append(f"G{group} · {resource}")
            selected=events[(events.resource==resource)&(events.end_us>left)&(events.start_us<right)]
            for r in selected.itertuples():
                color="#cbd5e1" if kind=="compute" else OPS[r.operation.split(":")[0]]
                a=max(left,r.start_us); z=min(right,r.end_us)
                ax.broken_barh([(a,z-a)],(lane-.31,.62),facecolors=color)
                if z-a>55 and kind=="compute":
                    ax.text((a+z)/2,lane,{"expert":"专家计算","dense":"注意力计算","layernorm":"LN"}[r.operation],
                            ha="center",va="center",fontsize=9)
    labels.append("KV · NIC3->7")
    for r in events[(events.group=="KV")&(events.end_us>left)&(events.start_us<right)].itertuples():
        a=max(left,r.start_us); z=min(right,r.end_us)
        ax.broken_barh([(a,z-a)],(5.7,.6),facecolors=COLORS["KV"],alpha=.65)
    ax.set_yticks(range(7),labels); ax.set_ylim(6.6,-.8)
    ax.set_title("GPU：profile 延迟的模型放置；NIC：首包→末 ACK（不是独占带宽区间）",loc="left",fontsize=11)
    handles=[Patch(color="#cbd5e1",label="计算模型")]+[Patch(color=c,label=k) for k,c in OPS.items()]
    ax.legend(handles=handles,ncol=5,loc="lower center",bbox_to_anchor=(.5,-.35),frameon=False,fontsize=9)
    for ax in axes: style(ax,(left,right))
    axes[-1].set_xlabel("仿真时间 (µs)")
    fig.subplots_adjust(bottom=.19)
    # More bottom space for this panel's legend.
    fig.text(.08,.013,"来源：逐包/端口/队列/任务 trace + Block24 Profile；计算为模型事件，网络为 ns-3 仿真，均非本轮硬件测量。",fontsize=9)
    fig.subplots_adjust(left=.14,right=.98,bottom=.17,top=.90,hspace=.45)
    for ext in ["svg","png"]: fig.savefig(OUT/f"dependent-peak-zoom.{ext}",facecolor="white")
    plt.close(fig)

    start=float(np.floor(peak.time_us)); end=start+6
    selected=p[(p.direction=="L2R")&(p.end_us>start)&(p.start_us<end)].sort_values("start_us")
    selected.to_csv(base/"selected_core_packets.csv",index=False)
    fig,ax=plt.subplots(figsize=(12.5,5.4))
    fig.suptitle(f"下探到逐包发送时隙：{start:.0f}–{end:.0f} µs",fontsize=17,y=.97)
    for r in selected.itertuples():
        y=GROUPS.index(r.group)
        a=max(start,r.start_us); z=min(end,r.end_us)
        ax.broken_barh([(a,z-a)],(y-.28,.56),facecolors=COLORS[r.group])
        ax.text((a+z)/2,y,f"T{r.task_id}",ha="center",va="center",fontsize=8,color="white",rotation=0)
    ax.set_yticks(range(4),["G0 · 0→4","G1 · 1→5","G2 · 2→6","KV · 3→7"])
    ax.set_ylim(3.6,-.6); ax.set_xlabel("仿真时间 (µs)")
    ax.set_title("switch 8 / port 4：矩形是一包的序列化区间，不是整个 flow",loc="left",fontsize=11)
    style(ax,(start,end))
    save(fig,"packet-slot-zoom",
        "来源：AllPacketTrace 与 PortTrace 按核心发送事件一一匹配。T 为 task ID，可回连 group/repetition/collective/peer。\n"
        "每个满包 = 4096 B payload + 78 B 包头；100 Gbps 上占用 0.33392 µs。空隙可能包含控制包；不是空闲的充分证据。",
        top=.80,bottom=.24)
    for filename in OUT.glob("*.svg"):
        filename.write_text("\n".join(line.rstrip() for line in filename.read_text().splitlines())+"\n")
    print("figures generated; zoom",left,right,"packet zoom",start,end)

if __name__=="__main__":
    main()
