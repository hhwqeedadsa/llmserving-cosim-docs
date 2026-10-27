"""Execute unchanged requester gates, not a kernel or packet simulation."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess

from experiments.rc_completion_reachability.analysis.sources import load, save, verify_tree

REQUEST_FUNCTIONS=('retry_first_write_send','req_retry','__req_next_wqe','rxe_wqe_is_fenced',
                   'check_init_depth','update_wqe_state','update_wqe_psn','update_state')
COMP_FUNCTIONS=('get_wqe','complete_wqe')


def extract(text,name):
    match=re.search(r'^static[^\n]*\b'+re.escape(name)+r'\s*\(',text,re.M);assert match,name
    begin=match.start();opening=text.index('{',match.end());depth=1;end=opening+1
    while depth:
        if text[end]=='{':depth+=1
        elif text[end]=='}':depth-=1
        end+=1
    assert text[end]=='\n';end+=1
    return text[begin:end],dict(function=name,first=text[:begin].count('\n')+1,
        last=text[:end].count('\n'),sha256=hashlib.sha256(text[begin:end].encode()).hexdigest())


STUB=r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
#define BTH_PSN_MASK 0xffffffu
enum { IB_QPT_RC=1, IB_WR_RDMA_WRITE=1, IB_WR_ATOMIC_FETCH_AND_ADD=2, IB_WR_LOCAL_INV=3 };
enum { IB_SEND_INLINE=1, IB_SEND_FENCE=2 };
enum { WR_ATOMIC_MASK=1, WR_WRITE_MASK=2, WR_READ_MASK=4, WR_SEND_MASK=8, WR_INLINE_MASK=16,
 WR_READ_OR_WRITE_MASK=WR_READ_MASK|WR_WRITE_MASK, WR_WRITE_OR_SEND_MASK=WR_WRITE_MASK|WR_SEND_MASK };
enum { RXE_START_MASK=1, RXE_END_MASK=2, RXE_READ_MASK=4 };
enum { QUEUE_TYPE_FROM_CLIENT=0 };
struct rxe_dma_info {int resid,length,cur_sge,sge_offset;};
struct rxe_send_wqe { unsigned first_psn,last_psn;int state,mask,status,has_rd_atomic;uint64_t iova;
 struct {unsigned opcode,send_flags;union {struct {uint64_t remote_addr;} atomic,rdma;} wr;} wr;
 struct rxe_dma_info dma;};
struct rxe_queue {unsigned cons,prod;struct rxe_send_wqe entries[4];};
struct rxe_pkt_info {unsigned mask,psn;int opcode;};
struct rxe_qp {struct {struct rxe_queue *queue;} sq;int mtu,need_req_skb,qp_timeout_jiffies,retrans_timer;
 struct {unsigned psn,wqe_index;int opcode,rd_atomic,wait_psn,again,need_rd_atomic,wait_fence;} req;
 struct {unsigned psn;int opcode;} comp;struct {int max_rd_atomic;} attr;};
static unsigned queue_get_consumer(struct rxe_queue *q,int type){(void)type;return q->cons;}
static unsigned queue_get_producer(struct rxe_queue *q,int type){(void)type;return q->prod;}
static unsigned queue_next_index(struct rxe_queue *q,unsigned index){(void)q;return (index+1)&3;}
static struct rxe_send_wqe *queue_addr_from_index(struct rxe_queue *q,unsigned index){assert(index<4);return &q->entries[index];}
static struct rxe_send_wqe *queue_head(struct rxe_queue *q,int type){(void)type;return q->cons==q->prod?NULL:&q->entries[q->cons];}
static int atomic_read(int *v){return *v;}
static int atomic_dec_return(int *v){return --*v;}
static void atomic_inc(int *v){++*v;}
static int qp_type(struct rxe_qp *q){(void)q;return IB_QPT_RC;}
static int psn_compare(unsigned a,unsigned b){return (int)((a-b+0x800000u)&BTH_PSN_MASK)-0x800000;}
static unsigned wr_opcode_mask(unsigned opcode,struct rxe_qp *q){(void)q;
 if(opcode==IB_WR_RDMA_WRITE)return WR_INLINE_MASK|WR_WRITE_MASK;
 assert(opcode==IB_WR_ATOMIC_FETCH_AND_ADD);return WR_ATOMIC_MASK;}
static int next_opcode(struct rxe_qp *q,struct rxe_send_wqe *w,u32 opcode){(void)q;(void)w;(void)opcode;abort();}
static int advance_dma_data(struct rxe_dma_info *d,unsigned n){(void)d;(void)n;abort();}
static int timer_pending(int *v){(void)v;return 0;}
static int jiffies;
static void mod_timer(int *timer,int when){(void)timer;(void)when;abort();}
static void do_complete(struct rxe_qp *q,struct rxe_send_wqe *w){
 assert(w==queue_head(q->sq.queue,0));q->sq.queue->cons=queue_next_index(q->sq.queue,q->sq.queue->cons);
 if(q->req.wait_fence){q->req.wait_fence=0;q->req.again=1;}}
'''

DRIVER=r'''
static struct rxe_queue ring;
static struct rxe_qp qp;
/* Explicit harness driver: valid RC/RTS, no local op, <=2 PSNs, no skb cap,
 * successful allocation/transmit. Only gate/update calls below are upstream.
 * There is no packet serialization, responder, timer, CQ, kernel or network. */
static int submit_next(void){
 struct rxe_send_wqe *w=__req_next_wqe(&qp);if(!w)return 0;
 w->mask=wr_opcode_mask(w->wr.opcode,&qp);
 if(rxe_wqe_is_fenced(&qp,w)){qp.req.wait_fence=1;return 0;}
 if(w->mask&WR_ATOMIC_MASK)if(check_init_depth(&qp,w))return 0;
 unsigned payload=w->wr.opcode==IB_WR_RDMA_WRITE?(unsigned)w->dma.resid:0;
 assert(payload<=256);
 struct rxe_pkt_info pkt={.psn=qp.req.psn,.opcode=(int)w->wr.opcode,.mask=RXE_START_MASK|RXE_END_MASK};
 w->dma.resid-=(int)payload;
 update_wqe_state(&qp,w,&pkt);update_wqe_psn(&qp,w,&pkt,payload);update_state(&qp,&pkt);
 return 1;
}
static void initialize(int fence){
 memset(&qp,0,sizeof(qp));memset(&ring,0,sizeof(ring));ring.prod=2;qp.sq.queue=&ring;qp.mtu=256;
 qp.req.opcode=-1;qp.attr.max_rd_atomic=1;qp.req.rd_atomic=1;
 ring.entries[0].wr.opcode=IB_WR_RDMA_WRITE;ring.entries[0].dma.length=ring.entries[0].dma.resid=4;
 ring.entries[1].wr.opcode=IB_WR_ATOMIC_FETCH_AND_ADD;ring.entries[1].dma.length=ring.entries[1].dma.resid=8;
 ring.entries[1].wr.send_flags=fence?IB_SEND_FENCE:0;
 assert(submit_next() && submit_next());assert(qp.req.psn==2 && qp.req.wqe_index==2);
}
static void ordinary_ack_completes_write(unsigned psn){
 struct rxe_pkt_info pkt={.psn=psn};
 /* check_psn chooses COMPST_COMP_WQE for a pending WRITE with later PSN;
  * for equal PSN complete_ack would advance the same queue/PSN prefix.
  * This harness only needs that validated queue prefix, no atomic completion. */
 assert(ring.cons==0 && ring.entries[0].state==wqe_state_pending);
 assert(complete_wqe(&qp,&pkt,&ring.entries[0])==COMPST_GET_WQE);
}
static void emit(const char *name,int sent,int fence_before){
 struct rxe_pkt_info pkt={.psn=1};struct rxe_send_wqe *selected=NULL;
 enum comp_state gate=get_wqe(&qp,&pkt,&selected);
 printf("{\"case\":\"%s\",\"req_psn\":%u,\"comp_psn\":%u,\"req_index\":%u,\"consumer\":%u,"
 "\"write_state\":%d,\"atomic_state\":%d,\"atomic_credit\":%d,\"atomic_holds_credit\":%d,"
 "\"retry_sent\":%d,\"fence_before_retry_atomic\":%d,\"atomic_fenced_now\":%d,"
 "\"selected_atomic\":%d,\"get_wqe_check_psn\":%d,\"get_wqe_done\":%d}\n",
 name,qp.req.psn,qp.comp.psn,qp.req.wqe_index,ring.cons,ring.entries[0].state,ring.entries[1].state,
 qp.req.rd_atomic,ring.entries[1].has_rd_atomic,sent,fence_before,
 rxe_wqe_is_fenced(&qp,&ring.entries[1]),selected==&ring.entries[1],gate==COMPST_CHECK_PSN,gate==COMPST_DONE);
}
int main(void){
 initialize(0);emit("initial_two_pending",0,0);
 initialize(0);req_retry(&qp);assert(ring.entries[0].state==wqe_state_posted && ring.entries[1].state==wqe_state_posted);
 assert(submit_next());ordinary_ack_completes_write(1);emit("ack_before_atomic_retry",1,0);
 initialize(0);req_retry(&qp);assert(submit_next() && submit_next());ordinary_ack_completes_write(1);
 emit("ack_after_atomic_retry",2,0);
 initialize(1);req_retry(&qp);assert(submit_next());int fenced=rxe_wqe_is_fenced(&qp,&ring.entries[1]);
 int sent=submit_next();assert(!sent);ordinary_ack_completes_write(1);emit("fenced_atomic_retry",1,fenced);
 initialize(0);ordinary_ack_completes_write(0);req_retry(&qp);assert(qp.req.wqe_index==1 && qp.req.psn==1);
 assert(submit_next());emit("write_ack_precedes_retry",1,0);
 return 0;
}
'''


def prepare(stage,output):
    identity=verify_tree(stage/'observed/v6.12');assert identity['commit']=='adc218676eef25575469234709c2d87185ca223a'
    output.mkdir();root=stage/'observed/v6.12/sources';rows=[];sections=[]
    enums=[]
    for file,name in (('rxe_verbs.h','wqe_state'),('rxe_comp.c','comp_state')):
        text=(root/file).read_text();match=re.search(r'enum '+name+r' \{.*?^\};\n',text,re.M|re.S);assert match
        enums.append(match.group());rows.append(dict(file=file,function='enum '+name,
            first=text[:match.start()].count('\n')+1,last=text[:match.end()].count('\n'),
            sha256=hashlib.sha256(match.group().encode()).hexdigest()))
    for file,names in (('rxe_req.c',REQUEST_FUNCTIONS),('rxe_comp.c',COMP_FUNCTIONS)):
        text=(root/file).read_text()
        for name in names:
            raw,row=extract(text,name);rows.append(dict(file=file,**row));sections.append(raw)
    # Keep the original copyright/license notice with extracted upstream bytes.
    notice=(root/'rxe_req.c').read_text().split('#include',1)[0]
    source=notice+'\n'.join(enums)+STUB+'\n'.join(sections)+DRIVER
    (output/'prefix.c').write_text(source)
    save(output/'fragments.json',dict(commit=identity['commit'],fragments=rows,
         original_functions=10,original_enums=2,full_kernel_executed=False,
         driver_scope='transcribed requester gate/update order; scalar single-threaded queues; successful packet allocation/transmit assumed',
         assumptions=['one RC/RTS QP','two posted WRITE/fetch-add WQEs','no PSN wrap','one atomic credit',
          'no kernel/locks/timer/CQ/DMA/packet validation','no responder executed','no current-upstream proof'],
         stub_numeric_masks_are_symbolic_renaming_not_wire_values=True))


def compile_run(output):
    command=['/usr/bin/cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-function',
             str(output/'prefix.c'),'-o',str(output/'prefix')]
    save(output/'compile-command.json',dict(command=command,cwd=str(Path.cwd())))
    result=subprocess.run(command,capture_output=True,timeout=120)
    (output/'compile.stdout').write_bytes(result.stdout);(output/'compile.stderr').write_bytes(result.stderr)
    save(output/'compile-exit.json',dict(exit=result.returncode));assert result.returncode==0,result.stderr
    result=subprocess.run([output/'prefix'],capture_output=True,timeout=10)
    (output/'records.jsonl').write_bytes(result.stdout);(output/'execution.stderr').write_bytes(result.stderr)
    save(output/'execution-exit.json',dict(exit=result.returncode));assert result.returncode==0,result.stderr


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('prepare','compile-run'))
    p.add_argument('--stage',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.action=='prepare':prepare(a.stage.resolve(),a.output.resolve())
    else:compile_run(a.output.resolve())
