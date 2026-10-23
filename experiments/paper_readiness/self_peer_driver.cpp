#include "self_peer_recording_api.hpp"

static void reset() {
  modifications=json::array(); created=destroyed=modify_calls=fail_modify=0;
  max_dest_override=max_qp_override=0;
}

// Only this wrapper is new. The loop body comes verbatim from the pinned file.
static ncclResult_t pair_original(gdaki_context* gdaki_ctx,int nranks,int ncontexts) {
  int rank=gdaki_ctx->rank;
  int nqps_for_comm=nranks*ncontexts, nqps_per_rank=ncontexts;
  uint32_t qpn=0;
  (void)qpn; // INFO is a no-op in the harness.
  ncclResult_t status=ncclSuccess;
#include "self_loops.inc"
out:
  return status;
}

// Exact allocation arithmetic, FULL only. No allocation or provider is called.
static json resources(int nranks,int rank,int nContexts,int nCounters) {
  const int rankStride=1;
#include "resource_declarations.inc"
  return {{"rankOff",rankOff},{"connectedNRanks",connectedNRanks},
    {"ncontexts",ncontexts},{"nqps_per_rank",nqps_per_rank},
    {"nqps_for_comm",nqps_for_comm},{"nqps_for_comm_this_rank",nqps_for_comm_this_rank},
    {"needCompanion",needCompanion},{"ncompanion_qps",ncompanion_qps},{"nqps",nqps}};
}

static json events(ncclResult_t status) {
  return {{"status",status},{"created",created},{"destroyed",destroyed},
    {"modify_calls",modify_calls},{"modifications",modifications}};
}

int main() {
  json result={{"scope","unmodified extracted host code with recording substitutes; no vendor runtime"},
    {"pairs",json::array()},{"connect_controls",json::array()},
    {"mtu",json::array()},{"resources",json::array()}};
  for(int ranks:{2,3,8}) for(int contexts:{1,2,3}) for(int rank=0;rank<ranks;++rank) {
    reset(); Ah ah; gdaki_context ctx{}; ctx.ah=&ah; ctx.rank=rank;
    for(int i=0;i<16;++i) ctx.rgid.raw[i]=static_cast<uint8_t>(i+11);
    const int n=contexts*(ranks+1);
    std::vector<Qp> q(n); std::vector<doca_gpu_verbs_qp_hl> h(n);
    std::vector<doca_gpu_verbs_qp_hl*> p(n);
    for(int i=0;i<n;++i) { q[i].qpn=1000+i; h[i].qp=&q[i]; p[i]=&h[i]; }
    ctx.gqps=p.data();
    auto status=pair_original(&ctx,ranks,contexts);
    json row=events(status);
    row["ranks"]=ranks; row["contexts"]=contexts; row["rank"]=rank;
    row["own_gid"]=gid_bytes(ctx.rgid); result["pairs"].push_back(row);
  }
  // Sparse self entry follows both original nullptr guards, without assuming
  // support for any RAIL topology or changing the resource-count scope.
  {
    reset(); Ah ah; gdaki_context ctx{};ctx.ah=&ah;ctx.rank=1;
    std::vector<Qp> q(10);std::vector<doca_gpu_verbs_qp_hl> h(10);
    std::vector<doca_gpu_verbs_qp_hl*> p(10);
    for(int i=0;i<10;++i) { q[i].qpn=1000+i;h[i].qp=&q[i];p[i]=&h[i]; }
    p[5]=nullptr;ctx.gqps=p.data();
    auto status=pair_original(&ctx,4,2);
    result["null_guard"]=events(status);
  }
  for(int local=1;local<=5;++local) for(int remote=1;remote<=5;++remote) {
    doca_verbs_mtu_size mtu=DOCA_VERBS_MTU_SIZE_256_BYTES;
    auto status=gdakiGetPathMtu(static_cast<ibv_mtu>(local),static_cast<ibv_mtu>(remote),&mtu);
    result["mtu"].push_back({{"local",local},{"remote",remote},{"status",status},{"bytes",mtu}});
  }
  for(const std::string name:{"remote_lag","ib_local","ib_grh","ib_other_subnet",
      "mtu_bad_local","mtu_bad_remote","ordering_mismatch","override_atomic",
      "fail_init","fail_rtr","fail_rts"}) {
    reset();Ah ah;gdaki_context ctx{};ctx.ah=&ah;Qp q{1000};doca_gpu_verbs_qp_hl qp{&q};
    gdaki_exch_info peer{};peer.qpn=9001;peer.lid=81;peer.rank=2;
    peer.vgid.raw[15]=77;uint8_t lag=0;
    if(name=="remote_lag") lag=2;
    if(name=="ib_local" || name=="ib_grh" || name=="ib_other_subnet") ctx.port_attr.link_layer=IBV_LINK_LAYER_INFINIBAND;
    if(name=="ib_grh") ctx.port_attr.flags=IBV_QPF_GRH_REQUIRED;
    if(name=="ib_other_subnet") { peer.gid.global.subnet_prefix=1;peer.gid.global.interface_id=91; }
    if(name=="mtu_bad_local") ctx.port_attr.active_mtu=static_cast<ibv_mtu>(0);
    if(name=="mtu_bad_remote") peer.active_mtu=static_cast<ibv_mtu>(6);
    if(name=="ordering_mismatch") peer.ordering_semantic=HARNESS_OTHER_ORDERING;
    if(name=="override_atomic") { max_dest_override=7;max_qp_override=9; }
    if(name=="fail_init") fail_modify=1;
    if(name=="fail_rtr") fail_modify=2;
    if(name=="fail_rts") fail_modify=3;
    auto status=gdakiConnectQp(&ctx,&qp,&peer,lag);
    json row=events(status);row["name"]=name;row["ah"]=ah.fields;
    result["connect_controls"].push_back(row);
  }
  for(int ranks:{2,4,32,128,1024}) for(int requested:{1,17}) for(int connections:{1,2,4}) {
    int allocated=((requested+connections-1)/connections)*connections;
    int per=allocated/connections;
    json row={{"ranks",ranks},{"requested",requested},{"connections",connections},
      {"allocated",allocated},{"per_connection",per},
      {"counter0",resources(ranks,0,per,0)},{"counter1_control",resources(ranks,0,per,1)}};
    result["resources"].push_back(row);
  }
  std::cout<<result.dump(2)<<'\n';
}
