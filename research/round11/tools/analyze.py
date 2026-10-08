#!/usr/bin/env python3
"""Audit and join source phases, ns-3 tasks, packets, ports and queues."""
import json
from pathlib import Path
import re
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = Path("/home/lry/a100_ep_runtime/第05轮_联合仿真M0_20260919/ns-3-ub/repo")
PKG = REPO / "scratch/20261008-dependent-moe-kv-contention"
HORIZON = 8400.0
GROUPS = ["G0","G1","G2","KV"]
HEADER = re.compile(r"Uid:(\d+) Psn:(\d+) Src:(\d+) Dst:(\d+).*?Type:PKT Size:(\d+) TaskId:(\d+)")
BRACKET = re.compile(r"\[\s*(\d+)\s*\]")
PORT = re.compile(r"\[([0-9.]+)us\] Port (Tx|Rx).*?PacketSize: (\d+)")
QUEUE = re.compile(r"\[([0-9.]+)us\].*?totalBytes: (\d+)")

def bandwidth(packets, capacity, width=5.0, begin=0.0, end=HORIZON):
    if width <= 0 or end <= begin:
        raise ValueError("positive bin width and increasing bounds required")
    edges = np.append(np.arange(begin,end,width),end)
    rows = []
    for direction in ["L2R","R2L"]:
        for group in GROUPS:
            counts = np.zeros(len(edges)-1)
            selected = packets[(packets.direction==direction)&(packets.group==group)]
            for row in selected.itertuples():
                if row.end_us <= begin or row.start_us >= end:
                    continue
                left = max(0,np.searchsorted(edges,row.start_us,side="right")-1)
                right = min(len(counts)-1,np.searchsorted(edges,row.end_us,side="left")-1)
                for i in range(left,right+1):
                    overlap = max(0,min(row.end_us,edges[i+1])-max(row.start_us,edges[i]))
                    counts[i] += overlap*capacity*1000/8
            for i, count in enumerate(counts):
                rows.append(dict(direction=direction, group=group, start_us=edges[i],
                    end_us=edges[i+1], center_us=(edges[i]+edges[i+1])/2,
                    wire_bytes=count, gbps=count*8/(1000*(edges[i+1]-edges[i])),
                    capacity_gbps=capacity, evidence="ns3-simulated/trace-derived"))
    return pd.DataFrame(rows)

def queue_events(case, direction, node):
    updates=[]
    with (case / f"runlog/QueueTrace_node_{node}_port_4.tr").open() as f:
        for order,line in enumerate(f):
            m=QUEUE.search(line)
            if m:
                updates.append((float(m[1]),order,int(m[2])))
    raw=pd.DataFrame(updates,columns=["time_us","order","bytes"])
    assert not raw.empty
    settled=raw.sort_values(["time_us","order"]).drop_duplicates("time_us",keep="last")
    settled=settled[["time_us","bytes"]].copy()
    settled["direction"]=direction
    settled["kib"]=settled.bytes/1024
    settled["node"]=node
    settled["port"]=4
    return settled, float(raw.bytes.max()/1024)

def sample_queue(events,width=1.0,begin=0,end=HORIZON):
    times=np.arange(begin,end,width)+width/2
    times=times[times<end]
    rows=[]
    for direction in ["L2R","R2L"]:
        s=events[events.direction==direction]
        idx=np.searchsorted(s.time_us.to_numpy(),times,side="right")-1
        values=np.where(idx>=0,s.bytes.to_numpy()[np.maximum(idx,0)],0)
        rows.append(pd.DataFrame(dict(direction=direction,time_us=times,kib=values/1024)))
    return pd.concat(rows,ignore_index=True)

def packet_events(case,tasks,capacity):
    records=[]
    for source in sorted(tasks.src.unique()):
        lines=(case/f"runlog/AllPacketTrace_PKT_node_{source}.tr").read_text().splitlines()
        for i,line in enumerate(lines):
            m=HEADER.search(line)
            if not m:
                continue
            uid,psn,src,dst,size,task=map(int,m.groups())
            # This workload's data packets are all full 4096-B payloads.
            # AllPacketTrace also labels small protocol/TA control messages PKT.
            if size < 1024:
                continue
            times=list(map(int,BRACKET.findall(lines[i+2])))
            assert len(times)==6 and size==4096, (line,times)
            assert (src<4)!=(dst<4)
            records.append(dict(uid=uid,psn=psn,src=src,dst=dst,payload_bytes=size,
                task_id=task,rounded_core_us=times[2]/1000,
                host_send_rounded_us=times[0]/1000,core_ingress_rounded_us=times[1]/1000,
                destination_arrival_rounded_us=times[5]/1000,
                direction="L2R" if src<4 else "R2L"))
    p=pd.DataFrame(records)
    assert not p.uid.duplicated().any()
    lookup=tasks.set_index("task_id")
    for field in ["group","repetition","collective","algorithm_stage","phase_id"]:
        p[field]=p.task_id.map(lookup[field])
    p["start_us"]=np.nan
    p["wire_bytes"]=0
    for direction,node in [("L2R",8),("R2L",9)]:
        all_events=[]
        with (case/f"runlog/PortTrace_node_{node}_port_4.tr").open() as f:
            for line in f:
                m=PORT.search(line)
                if m and m[2]=="Tx":
                    all_events.append((float(m[1]),int(m[3])))
        all_events.sort()
        # Check physical serialization intervals, including control packets.
        for (start,size),(next_start,_) in zip(all_events,all_events[1:]):
            assert start+size*8/(capacity*1000) <= next_start+2e-6, (start,size,next_start)
        data=[(t,size) for t,size in all_events if size>=1024]
        selected=p[p.direction==direction].sort_values("rounded_core_us")
        assert len(selected)==len(data), (direction,len(selected),len(data))
        for row,(t,size) in zip(selected.itertuples(),data):
            assert abs(row.rounded_core_us-t)<=0.001001
            assert size-row.payload_bytes==78
            p.loc[row.Index,["start_us","wire_bytes"]]=[t,size]
    p["end_us"]=p.start_us+p.wire_bytes*8/(capacity*1000)
    p["capacity_gbps"]=capacity
    p["evidence"]="ns3-simulated/AllPacketTrace+PortTrace"
    totals=p.groupby("task_id").payload_bytes.sum().sort_index()
    expected=lookup.payload_bytes.sort_index()
    pd.testing.assert_series_equal(totals,expected,check_names=False,check_dtype=False)
    assert p.end_us.max() < HORIZON
    return p

def tasks_and_phases(case):
    manifest=pd.read_csv(case/"flow_manifest.csv").fillna({"depends_on_phase":""})
    t=pd.read_csv(case/"output/task_statistics.csv").rename(columns={
        "taskId":"task_id","taskStartTime(us)":"start_us","taskCompletesTime(us)":"complete_us",
        "firstPacketSends(us)":"first_packet_us","lastPacketACKs(us)":"last_ack_us"})
    t=manifest.merge(t[["task_id","start_us","complete_us","first_packet_us","last_ack_us"]],on="task_id",validate="one_to_one")
    assert len(t)==(290 if case.name.startswith("kv_") else 240)
    assert t[["start_us","complete_us","first_packet_us","last_ack_us"]].notna().all().all()
    assert (t.complete_us>=t.start_us).all() and (t.last_ack_us>=t.first_packet_us).all()
    canonical=pd.read_csv(case/"canonical.rank0.txt",names=["ns","type","task_id","src","dst"])
    assert len(canonical)==2*len(t)
    for kind,column in [("START","start_us"),("COMPLETE_VISIBLE","complete_us")]:
        c=canonical[canonical.type==kind].set_index("task_id")
        assert len(c)==len(t) and c.index.is_unique
        expected=t.set_index("task_id")[column]+(0.02 if kind=="COMPLETE_VISIBLE" else 0)
        assert np.max(np.abs(c.ns.sort_index()/1000-expected.sort_index())) < .001001
    phases=[]
    for phase,rows in t.groupby("phase_id"):
        a=rows.iloc[0]
        assert rows.start_us.max()-rows.start_us.min()<1e-8
        phase_row=dict(phase_id=phase,group=a.group,repetition=a.repetition,
            collective=a.collective,algorithm_stage=a.algorithm_stage,
            depends_on_phase=a.depends_on_phase,start_us=float(rows.start_us.min()),
            complete_us=float(rows.complete_us.max()),last_ack_us=float(rows.last_ack_us.max()),
            delay_us=float(a.delay_ns/1000),task_count=len(rows))
        phase_row["duration_us"]=phase_row["complete_us"]-phase_row["start_us"]
        phases.append(phase_row)
    phases=pd.DataFrame(phases)
    index=phases.set_index("phase_id")
    errors=[]
    for r in phases.itertuples():
        expected=(index.loc[int(float(r.depends_on_phase)),"complete_us"]+.02+r.delay_us
                  if r.depends_on_phase!="" else r.delay_us)
        errors.append(abs(r.start_us-expected))
    assert max(errors)<1e-5, max(errors)
    t["fct_us"]=t.last_ack_us-t.first_packet_us
    return t,phases,max(errors)

def unified_events(tasks,phases):
    rows=[]
    for t in tasks.itertuples():
        rows.append(dict(resource=f"NIC{t.src}->{t.dst}",group=t.group,repetition=t.repetition,
            type="communication",operation=t.collective+":"+t.algorithm_stage,
            start_us=t.first_packet_us,end_us=t.last_ack_us,task_id=t.task_id,phase_id=t.phase_id,
            evidence="ns3-simulated: first-packet to last-ACK"))
    for r in phases[phases.group!="KV"].itertuples():
        stage=int(r.phase_id)%4
        duration={0:36.330,1:0,2:2.549,3:456.737}[stage]
        if not duration:
            continue
        group=int(r.group[1])
        for node in [group,group+4]:
            rows.append(dict(resource=f"GPU{node}",group=r.group,repetition=r.repetition,
                type="compute",operation={0:"dense",2:"layernorm",3:"expert"}[stage],
                start_us=r.start_us-duration,end_us=r.start_us,task_id="",phase_id=r.phase_id,
                evidence="profile-derived-duration/modelled-placement (not GPU runtime trace)"))
    return pd.DataFrame(rows)

def main():
    output=ROOT/"analysis"
    output.mkdir(parents=True,exist_ok=True)
    matrix=yaml.safe_load((PKG/"matrix.yaml").read_text())
    summaries=[]
    for item in matrix:
        case=PKG/item["case_dir"]; name=item["case_id"]; capacity=item["core_gbps"]
        dest=output/name
        dest.mkdir(exist_ok=True)
        tasks,phases,error=tasks_and_phases(case)
        packets=packet_events(case,tasks,capacity)
        b=bandwidth(packets,capacity)
        assert abs(b.wire_bytes.sum()-packets.wire_bytes.sum()) < .02
        totals=b.groupby(["direction","center_us"]).gbps.sum()
        assert totals.max()<=capacity+1e-6
        queue_parts=[]; raw_peaks={}
        for direction,node in [("L2R",8),("R2L",9)]:
            q,peak=queue_events(case,direction,node)
            queue_parts.append(q); raw_peaks[direction]=peak
        q=pd.concat(queue_parts,ignore_index=True)
        sampled=sample_queue(q)
        events=unified_events(tasks,phases)
        for filename,frame in [("tasks",tasks),("phases",phases),("packets",packets),
                ("bandwidth_5us",b),("queue_events",q),("queue_1us",sampled),("events",events)]:
            frame.to_csv(dest/f"{filename}.csv",index=False,float_format="%.8f")
        inference=phases[phases.group!="KV"]
        row=dict(case_id=name,tasks=len(tasks),phases=len(phases),packets=len(packets),
            payload_bytes=int(packets.payload_bytes.sum()),wire_bytes=int(packets.wire_bytes.sum()),
            core_gbps=capacity,inference_end_us=float(inference.complete_us.max()),
            all_last_ack_us=float(tasks.last_ack_us.max()),phase_p95_us=float(inference.duration_us.quantile(.95)),
            inference_fct_p95_us=float(tasks[tasks.group!="KV"].fct_us.quantile(.95)),
            max_bandwidth_gbps=float(totals.max()),max_dependency_error_us=error,
            l2r_queue_peak_kib=float(q[q.direction=="L2R"].kib.max()),
            l2r_callback_peak_kib=raw_peaks["L2R"],
            l2r_idle_5us_bins=int((totals.loc["L2R"]<1e-9).sum()),
            checks="all tasks; canonical events; phase delays; per-task payload; per-port serialization; bin integral; horizon")
        summaries.append(row)
        print(json.dumps(row),flush=True)
    s=pd.DataFrame(summaries).set_index("case_id")
    comparisons=[]
    for control,treatment,sign in [("no_kv_100","kv_100",1),("kv_100","kv_200",-1)]:
        for metric in ["inference_end_us","phase_p95_us"]:
            a=float(s.loc[control,metric]); bval=float(s.loc[treatment,metric])
            comparisons.append(dict(control=control,treatment=treatment,metric=metric,
                control_value=a,treatment_value=bval,delta=bval-a,delta_pct=(bval/a-1)*100,
                status="matched" if sign*(bval-a)>0 else "mismatched"))
    for row in summaries:
        statuses=[c["status"] for c in comparisons if c["treatment"]==row["case_id"]]
        row["classification"]=("matched" if not statuses or all(x=="matched" for x in statuses)
            else "mismatched" if all(x=="mismatched" for x in statuses) else "partially_matched")
    pd.DataFrame(summaries).to_csv(output/"summary.csv",index=False,float_format="%.8f")
    pd.DataFrame(comparisons).to_csv(output/"comparisons.csv",index=False,float_format="%.8f")
    (output/"audit.json").write_text(json.dumps(dict(cases=summaries,comparisons=comparisons,
        horizon_us=HORIZON,measurement="simulation, not hardware",status="passed"),indent=2)+"\n")

if __name__=="__main__":
    main()
