"""Compile unchanged pinned RXE functions in explicit single-threaded stubs.

This checks selected control branches, not a kernel, network or hardware run.
Generated upstream function bodies remain in the caller's local run directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


def extract(text, name):
    pattern = r'^static [^;{]*?\b' + re.escape(name) + r'\s*\([^;{]*?\)\s*\{'
    matches = list(re.finditer(pattern, text, re.M))
    assert len(matches) == 1, (name, len(matches))
    start = matches[0].start()
    brace = matches[0].end() - 1
    depth = 0
    tokens = re.finditer(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\{\}|[{}]', text[brace:], re.S)
    for token in tokens:
        if token.group() == '{':
            depth += 1
        elif token.group() == '}':
            depth -= 1
            if depth == 0:
                end = brace + token.end()
                return text[start:end], text[:start].count('\n') + 1, text[:end].count('\n') + 1
    raise AssertionError('unterminated ' + name)


HEADER = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef uint64_t u64; typedef uint32_t u32; typedef int32_t s32; typedef uint8_t u8;
#define BTH_PSN_MASK 0xffffff
#define IB_QPT_RC 1
#define IB_QPT_UC 2
#define IB_WC_SUCCESS 0
#define IB_WC_FATAL_ERR 1
#define IB_WC_LOC_PROT_ERR 2
#define IB_WC_REM_INV_REQ_ERR 3
#define IB_WC_REM_ACCESS_ERR 4
#define IB_WC_REM_OP_ERR 5
#define IB_ACCESS_LOCAL_WRITE 1
#define RXE_TO_MR_OBJ 1
#define WARN_ON_ONCE(x) ((void)(x))
#define rxe_dbg_qp(...) ((void)0)
#define fallthrough __attribute__((fallthrough))
#define RXE_CNT_OUT_OF_SEQ_REQ 0
#define RXE_CNT_DUP_REQ 0
#define RXE_CNT_RCV_RNR 0
#define RXE_CNT_RCV_SEQ_ERR 0
enum { IB_OPCODE_RC_ACKNOWLEDGE=17, IB_OPCODE_RC_ATOMIC_ACKNOWLEDGE=18,
 IB_OPCODE_RC_RDMA_READ_RESPONSE_FIRST=13, IB_OPCODE_RC_RDMA_READ_RESPONSE_MIDDLE=14,
 IB_OPCODE_RC_RDMA_READ_RESPONSE_LAST=15, IB_OPCODE_RC_RDMA_READ_RESPONSE_ONLY=16,
 IB_OPCODE_RC_FETCH_ADD=20, IB_OPCODE_RC_RDMA_WRITE_ONLY=10 };
enum { IB_WR_RDMA_WRITE=0, IB_WR_ATOMIC_FETCH_AND_ADD=1, IB_WR_ATOMIC_CMP_AND_SWP=2,
 IB_WR_RDMA_READ=3, IB_WR_RDMA_READ_WITH_INV=4, IB_WR_FLUSH=5, IB_WR_ATOMIC_WRITE=6 };
enum { RXE_WRITE_MASK=1, RXE_ATOMIC_MASK=2, RXE_SEND_MASK=4, RXE_FLUSH_MASK=8,
 RXE_READ_MASK=16, RXE_START_MASK=32, RXE_END_MASK=64, RXE_ATOMIC_WRITE_MASK=128 };
enum { AETH_ACK_UNLIMITED=0x1f, AETH_TYPE_MASK=0xe0, AETH_ACK=0,
 AETH_RNR_NAK=0x20, AETH_NAK=0x60, AETH_NAK_PSN_SEQ_ERROR=0x60,
 AETH_NAK_INVALID_REQ=0x61, AETH_NAK_REM_ACC_ERR=0x62, AETH_NAK_REM_OP_ERR=0x63 };
enum { WR_ATOMIC_OR_READ_MASK=1, wqe_state_pending=1, wqe_state_posted=0 };
enum {rdatm_res_state_new=1,rdatm_res_state_replay=2};
struct rxe_dev { int unused; };
struct rxe_mr { u64 value; };
struct resp_res { int type,replay;u32 first_psn,last_psn,cur_psn;
 struct {u64 orig_val;} atomic;struct {u64 va_org,va;u32 length,resid,rkey;int state;} read;int state; };
struct rxe_pkt_info { u32 psn;int opcode,mask,ack;u8 syn;u64 add,orig,va;u32 length,rkey; };
struct rxe_send_wqe {u32 first_psn,last_psn;int state,mask,status,has_rd_atomic;
 struct {int opcode;} wr;struct {u64 value;int resid;} dma;};
struct rxe_qp {
 struct {int device;} ibqp;int pd;
 struct {int max_dest_rd_atomic,retry_cnt,rnr_retry;} attr;
 struct {u32 psn,ack_psn,msn;int sent_psn_nak,opcode,status,drop_msg;u64 va,offset;
 u8 aeth_syndrome;struct rxe_mr *mr;struct resp_res *res;struct resp_res resources[4];} resp;
 struct {u32 psn;int opcode,retry_cnt,rnr_retry,started_retry,timeout_retry;} comp;
 struct {int rd_atomic,need_rd_atomic,again,wait_psn;} req;
};
static int psn_compare(u32 a,u32 b) {return (int)((a-b+0x800000)&BTH_PSN_MASK)-0x800000;}
static int qp_type(struct rxe_qp *q) {(void)q;return IB_QPT_RC;}
static struct rxe_dev *to_rdev(int q) {(void)q;return 0;}
static void rxe_counter_inc(struct rxe_dev *d,int c) {(void)d;(void)c;}
static int bth_ack(struct rxe_pkt_info *p) {return p->ack;}
static u64 atmeth_comp(struct rxe_pkt_info *p) {(void)p;return 0;}
static u64 atmeth_swap_add(struct rxe_pkt_info *p) {return p->add;}
static u64 atmack_orig(struct rxe_pkt_info *p) {return p->orig;}
static u8 aeth_syn(struct rxe_pkt_info *p) {return p->syn;}
static u64 reth_va(struct rxe_pkt_info *p) {return p->va;}
static u32 reth_len(struct rxe_pkt_info *p) {return p->length;}
static u32 reth_rkey(struct rxe_pkt_info *p) {return p->rkey;}
static void atomic_inc(int *n) {++*n;}
void resp_init(int capacity,u32 first);
struct rxe_pkt_info resp_atomic(u32 psn,u64 add);
struct rxe_pkt_info resp_duplicate_write(u32 psn);
struct rxe_pkt_info resp_duplicate_atomic(u32 psn,u64 add);
u64 resp_value(void);
void comp_init(int with_write);
int comp_deliver(struct rxe_pkt_info packet);
int comp_completed(void);
int comp_atomic_copies(void);
u64 comp_result(void);
int comp_credit(void);
'''

RESP_STUB = r'''
static struct rxe_qp q;static struct rxe_mr memory;static unsigned head;
static struct rxe_pkt_info output;
static struct resp_res *rxe_prepare_res(struct rxe_qp *qp,struct rxe_pkt_info *pkt,int type) {
 struct resp_res *r=&qp->resp.resources[head];head=(head+1)%qp->attr.max_dest_rd_atomic;
 memset(r,0,sizeof(*r));r->type=type;r->first_psn=r->last_psn=r->cur_psn=pkt->psn;return r;
}
static int rxe_mr_do_atomic_op(struct rxe_mr *m,u64 addr,int opcode,u64 comp,u64 add,u64 *old) {
 (void)addr;(void)comp;if(opcode!=IB_OPCODE_RC_FETCH_ADD)abort();*old=m->value;m->value+=add;return 0;
}
static int send_common_ack(struct rxe_qp *qp,u8 syn,u32 psn,int opcode,const char *label) {
 (void)label;output=(struct rxe_pkt_info){.opcode=opcode,.psn=psn,.syn=syn,.mask=RXE_START_MASK|RXE_END_MASK};
 if(opcode==IB_OPCODE_RC_ATOMIC_ACKNOWLEDGE)output.orig=qp->resp.res->atomic.orig_val;return 0;
}
static int send_read_response_ack(struct rxe_qp *qp,u8 syn,u32 psn) {(void)qp;(void)syn;(void)psn;abort();}
'''

RESP_WRAPPER = r'''
void resp_init(int capacity,u32 first) {
 memset(&q,0,sizeof(q));head=0;memory.value=41;q.resp.mr=&memory;
 q.attr.max_dest_rd_atomic=capacity;q.resp.psn=q.resp.ack_psn=first;
 q.resp.aeth_syndrome=AETH_ACK_UNLIMITED;q.resp.opcode=IB_OPCODE_RC_RDMA_WRITE_ONLY;q.resp.msn=1;
}
u64 resp_value(void) {return memory.value;}
static struct rxe_pkt_info deliver(struct rxe_pkt_info p) {
 output=(struct rxe_pkt_info){0};int s=check_psn(&q,&p);
 if(s==RESPST_DUPLICATE_REQUEST)s=duplicate_request(&q,&p);
 if(s==RESPST_CHK_OP_SEQ || s==RESPST_ATOMIC_REPLY)s=atomic_reply(&q,&p);
 if(s==RESPST_ACKNOWLEDGE)acknowledge(&q,&p);
 return output;
}
struct rxe_pkt_info resp_atomic(u32 psn,u64 add) {
 return deliver((struct rxe_pkt_info){.psn=psn,.opcode=IB_OPCODE_RC_FETCH_ADD,.mask=RXE_ATOMIC_MASK,.ack=1,.add=add});
}
struct rxe_pkt_info resp_duplicate_write(u32 psn) {
 return deliver((struct rxe_pkt_info){.psn=psn,.opcode=IB_OPCODE_RC_RDMA_WRITE_ONLY,.mask=RXE_WRITE_MASK,.ack=1});
}
struct rxe_pkt_info resp_duplicate_atomic(u32 psn,u64 add) {return resp_atomic(psn,add);}
'''

COMP_STUB = r'''
static struct rxe_qp q;static struct rxe_send_wqe entries[2];static int count,done,copies;
static void comp_check_sq_drain_done(struct rxe_qp *qp) {(void)qp;}
static void do_complete(struct rxe_qp *qp,struct rxe_send_wqe *w) {(void)qp;(void)w;++done;}
static int copy_data(int pd,int access,void *dma,void *data,int size,int kind) {
 (void)pd;(void)access;(void)kind;struct rxe_send_wqe *w=&entries[count-1];
 if(dma!=&w->dma || size!=8)abort();memcpy(&w->dma.value,data,8);++copies;return 0;
}
'''

COMP_WRAPPER = r'''
void comp_init(int with_write) {
 memset(&q,0,sizeof(q));memset(entries,0,sizeof(entries));done=copies=0;count=with_write+1;
 q.comp.opcode=-1;q.comp.psn=with_write?0:1;q.req.rd_atomic=0;
 for(int i=0;i<count;++i) {entries[i].first_psn=entries[i].last_psn=with_write?i:1;
 entries[i].state=wqe_state_pending;entries[i].dma.value=0xdeadbeef;
 entries[i].wr.opcode=i==count-1?IB_WR_ATOMIC_FETCH_AND_ADD:IB_WR_RDMA_WRITE;
 entries[i].mask=i==count-1?WR_ATOMIC_OR_READ_MASK:0;entries[i].has_rd_atomic=i==count-1;}
}
int comp_completed(void) {return done;}
int comp_atomic_copies(void) {return copies;}
u64 comp_result(void) {return entries[count-1].dma.value;}
int comp_credit(void) {return q.req.rd_atomic;}
int comp_deliver(struct rxe_pkt_info packet) {
 struct rxe_qp *qp=&q;struct rxe_pkt_info *pkt=&packet;struct rxe_send_wqe *wqe;
 enum comp_state state;
 for(int fuel=0;fuel<20;++fuel) {
  if(done==count)return COMPST_DONE;wqe=&entries[done];state=check_psn(qp,pkt,wqe);
  if(state==COMPST_COMP_WQE) {complete_wqe(qp,pkt,wqe);continue;}
  if(state==COMPST_CHECK_ACK)state=check_ack(qp,pkt,wqe);
  if(state==COMPST_ATOMIC)state=do_atomic(qp,pkt,wqe);
  switch(state) {
__WRITE_SEND_CASE__
  default:break;
  }
  if(state==COMPST_COMP_ACK)state=complete_ack(qp,pkt,wqe);
  switch(state) {
__UPDATE_COMP_CASE__
  default:break;
  }
  return state;
 }abort();
}
'''

MAIN = r'''
static void result(const char *name,struct rxe_pkt_info p,int state) {
 printf("{\"case\":\"%s\",\"reply_opcode\":%d,\"reply_psn\":%u,\"target_value\":%llu,\"returned_value\":%llu,\"completed\":%d,\"atomic_copies\":%d,\"credit\":%d,\"state\":%d}\n",
 name,p.opcode,p.psn,(unsigned long long)resp_value(),(unsigned long long)comp_result(),comp_completed(),comp_atomic_copies(),comp_credit(),state);
}
int main(void) {
 struct rxe_pkt_info p;int state;
 resp_init(1,1);comp_init(0);p=resp_atomic(1,5);state=comp_deliver(p);result("healthy",p,state);
 resp_init(1,1);comp_init(0);resp_atomic(1,5);p=resp_duplicate_atomic(1,5);state=comp_deliver(p);result("lost_reply_replay",p,state);
 /* QP expected PSN1 means preceding WRITE PSN0 was accepted; its ACK was lost.
    Both emitted WQEs remain pending. Atomic PSN1 is applied; its ACK is lost.
    Retrying WRITE0 can now produce cumulative ordinary ACK1. */
 resp_init(1,1);comp_init(1);resp_atomic(1,5);p=resp_duplicate_write(0);state=comp_deliver(p);result("duplicate_write_after_lost_atomic_ack",p,state);
 resp_init(1,1);comp_init(0);resp_atomic(1,5);p=(struct rxe_pkt_info){.opcode=IB_OPCODE_RC_ACKNOWLEDGE,.psn=2,.syn=AETH_ACK_UNLIMITED,.mask=RXE_START_MASK|RXE_END_MASK};
 state=comp_deliver(p);result("later_psn_ordinary_ack",p,state);
 resp_init(1,1);comp_init(0);resp_atomic(1,5);resp_atomic(2,7);p=resp_duplicate_atomic(1,5);result("evicted_atomic_replay",p,-1);
 return 0;
}
'''


def generate(root, out):
    identity = json.loads((root / 'identity.json').read_bytes())
    assert identity['commit'] == 'adc218676eef25575469234709c2d87185ca223a'
    sources = {}
    for row in identity['sources']:
        raw = (root / row['local']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row['sha256']
        sources[Path(row['path']).name] = raw.decode()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'upstream-notices.txt').write_text(sources['rxe_resp.c'].split('#include',1)[0])
    records = []
    def functions(file, names):
        parts = []
        for name in names:
            raw, first, last = extract(sources[file], name)
            parts.append(raw)
            records.append(dict(file=file, function=name, first=first, last=last,
                                sha256=hashlib.sha256(raw.encode()).hexdigest()))
        return '\n\n'.join(parts)
    enums = []
    for file, name in [('rxe.h', 'resp_states'), ('rxe_comp.c', 'comp_state')]:
        m = re.search(r'enum ' + name + r'\s*\{[^}]+\};', sources[file])
        assert m
        enums.append(m.group())
    (out / 'stub.h').write_text(HEADER + '\n'.join(enums))
    resp = functions('rxe_resp.c', ['check_psn', 'find_resource', 'atomic_reply', 'send_ack', 'send_atomic_ack', 'acknowledge', 'duplicate_request'])
    comp = functions('rxe_comp.c', ['reset_retry_counters', 'check_psn', 'check_ack', 'do_atomic', 'complete_ack', 'complete_wqe'])
    wrapper = COMP_WRAPPER
    for marker, first, last in [('__WRITE_SEND_CASE__', 'case COMPST_WRITE_SEND:', 'case COMPST_COMP_ACK:'),
                                ('__UPDATE_COMP_CASE__', 'case COMPST_UPDATE_COMP:', 'case COMPST_DONE:')]:
        text = sources['rxe_comp.c']
        start = text.index(first, text.index('int rxe_completer('))
        end = text.index(last, start + len(first))
        raw = text[start:end]
        wrapper = wrapper.replace(marker, raw)
        records.append(dict(file='rxe_comp.c', function=first, first=text[:start].count('\n')+1,
                            last=text[:end].count('\n')+1, sha256=hashlib.sha256(raw.encode()).hexdigest()))
    (out / 'responder.c').write_text('#include "stub.h"\n' + RESP_STUB + resp + RESP_WRAPPER)
    (out / 'completer.c').write_text('#include "stub.h"\n' + COMP_STUB + comp + wrapper)
    (out / 'main.c').write_text('#include "stub.h"\n' + MAIN)
    (out / 'source-branches.json').write_text(json.dumps(dict(commit=identity['commit'], branches=records,
        scope='unchanged selected functions/cases; memory/queue/network/scheduling stubs; not full RXE'), indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generate(args.sources, args.output)
