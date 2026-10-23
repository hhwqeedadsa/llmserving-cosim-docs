// CPU control-flow harness only. None of these types/constants implement the
// vendor ABI, verbs transport, GPU memory ordering or physical self routing.
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>
#include "json/json.hpp"
using json = nlohmann::json;
using ncclResult_t = int;
constexpr int ncclSuccess=0, ncclInternalError=1, ncclInvalidArgument=2;
constexpr int IBV_SYSFS_NAME_MAX=64, DEFAULT_HOP_LIMIT=64;
constexpr int IBV_LINK_LAYER_INFINIBAND=1, IBV_QPF_GRH_REQUIRED=1;
enum ibv_mtu { IBV_MTU_256=1, IBV_MTU_512, IBV_MTU_1024, IBV_MTU_2048, IBV_MTU_4096 };
enum doca_verbs_mtu_size { DOCA_VERBS_MTU_SIZE_256_BYTES=256,
  DOCA_VERBS_MTU_SIZE_512_BYTES=512, DOCA_VERBS_MTU_SIZE_1K_BYTES=1024,
  DOCA_VERBS_MTU_SIZE_2K_BYTES=2048, DOCA_VERBS_MTU_SIZE_4K_BYTES=4096 };
enum doca_verbs_qp_ordering_semantic { HARNESS_ORDERING=1, HARNESS_OTHER_ORDERING=2 };
constexpr int DOCA_VERBS_ADDR_TYPE_IB_GRH=1, DOCA_VERBS_ADDR_TYPE_IB_NO_GRH=2;
constexpr int DOCA_VERBS_QP_STATE_INIT=1, DOCA_VERBS_QP_STATE_RTR=2, DOCA_VERBS_QP_STATE_RTS=3;
constexpr int DOCA_VERBS_QP_ATOMIC_MODE_IB_SPEC=1;
union ibv_gid { uint8_t raw[16]; struct { uint64_t subnet_prefix, interface_id; } global; };
struct Ah { json fields=json::object(); };
struct Qp { uint32_t qpn; };
struct doca_gpu_verbs_qp_hl { Qp* qp; };
struct gdaki_exch_info {
  int lid{}, qpn{}, gid_index{}, cuda_id{}, rank{};
  ibv_gid gid{}, vgid{};
  char hostname[IBV_SYSFS_NAME_MAX]{}, ib_dev_name[IBV_SYSFS_NAME_MAX]{};
  ibv_mtu active_mtu=IBV_MTU_4096;
  doca_verbs_qp_ordering_semantic ordering_semantic=HARNESS_ORDERING;
};
struct gdaki_context {
  struct { int lid=71, link_layer=0, flags=0; ibv_mtu active_mtu=IBV_MTU_4096; } port_attr;
  struct { int max_qp_rd_atom=16; } ib_dev_attr;
  ibv_gid rgid{};
  int gid_index=3, cuda_id=0, rank=0, port_num=1, pkey_index=0;
  char hostname[IBV_SYSFS_NAME_MAX]="harness-host", ib_dev_name[IBV_SYSFS_NAME_MAX]="harness-nic";
  Ah* ah;
  doca_gpu_verbs_qp_hl** gqps=nullptr;
};
struct doca_verbs_qp_attr_t { json fields=json::object(); };
static json modifications=json::array();
static int created=0, destroyed=0, modify_calls=0, fail_modify=0;
static int max_dest_override=0, max_qp_override=0;
static int ncclParamGinGdakiMaxDestRdAtomic() { return max_dest_override; }
static int ncclParamGinGdakiMaxQpRdAtomic() { return max_qp_override; }
static int ncclParamIbTimeout() { return 20; }
static int ncclParamIbRetryCnt() { return 7; }
static doca_verbs_qp_ordering_semantic gdakiOrderingSematic() { return HARNESS_ORDERING; }
static uint64_t ncclIbExtractLocalSubnetPrefix(uint64_t n) { return n; }
static uint16_t ncclIbExtractFlid(ibv_gid* gid) { return static_cast<uint16_t>(gid->global.interface_id); }
static int doca_verbs_qp_get_qpn(Qp* q, uint32_t* n) { *n=q->qpn; return 0; }
static json gid_bytes(const ibv_gid& gid) {
  json a=json::array(); for (auto v:gid.raw) a.push_back(v); return a;
}
static int doca_verbs_ah_attr_set_addr_type(Ah* a,int v) { a->fields["addr_type"]=v; return 0; }
static int doca_verbs_ah_attr_set_hop_limit(Ah* a,int v) { a->fields["hop_limit"]=v; return 0; }
static int doca_verbs_ah_attr_set_gid(Ah* a,ibv_gid v) { a->fields["gid"]=gid_bytes(v); return 0; }
static int doca_verbs_ah_attr_set_dlid(Ah* a,int v) { a->fields["dlid"]=v; return 0; }
static int doca_verbs_qp_attr_create(doca_verbs_qp_attr_t** p) { *p=new doca_verbs_qp_attr_t; ++created; return 0; }
static int doca_verbs_qp_attr_destroy(doca_verbs_qp_attr_t* p) { delete p; ++destroyed; return 0; }
#define DOCACHECK(call) do { int e=(call); if(e) return e; } while(false)
#define NCCLCHECK(call) DOCACHECK(call)
#define DOCACHECKGOTO(call,status,label) do { int e=(call); if(e) { status=e; goto label; } } while(false)
#define NCCLCHECKGOTO(call,status,label) DOCACHECKGOTO(call,status,label)
#define WARN(...) do {} while(false)
#define INFO(...) do {} while(false)
#include "generated_api.inc"
static int doca_verbs_qp_modify(Qp* q,doca_verbs_qp_attr_t* a,int mask) {
  ++modify_calls;
  int error=(fail_modify && modify_calls==fail_modify) ? 9 : 0;
  modifications.push_back({{"qpn",q->qpn},{"fields",a->fields},
    {"mask",mask_names(mask)},{"error",error}});
  return error;
}
#include "original_functions.inc"
