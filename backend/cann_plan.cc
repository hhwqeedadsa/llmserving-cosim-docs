#include "graph_resources.hh"
#include "layer_plan.hh"
#include <algorithm>
#include <set>
#include <stdexcept>
#include <tuple>

namespace moe_ub {
namespace {
using J = nlohmann::json;
constexpr const char* revision = "6ead121aded45355043b502756b6592fd7c30b14";
void Check(bool ok, const char* what) { if (!ok) throw std::runtime_error(std::string("CANN: ")+what); }
uint64_t Num(const J& v, uint64_t lo=0, uint64_t hi=INT64_MAX) {
    Check(v.is_number_integer() && !v.is_boolean() && (!v.is_number_integer() || v.is_number_unsigned() || v.get<int64_t>()>=0), "invalid integer");
    auto n=v.get<uint64_t>(); Check(n>=lo && n<=hi,"integer outside range"); return n;
}
void Fields(const J& v, std::set<std::string> expected) {
    Check(v.is_object(),"expected object"); std::set<std::string> actual;
    for (auto it=v.begin();it!=v.end();++it) actual.insert(it.key());
    Check(actual==expected,"unexpected/missing fields");
}
uint64_t Align(uint64_t n, uint64_t unit) { return (n+unit-1)/unit*unit; }
J Deps(J values) { std::set<uint64_t> out; for(const auto& x:values) out.insert(Num(x,1)); return J(out); }
void Append(J& a,const J& b) { for(const auto& x:b) a.push_back(x); }
std::vector<std::pair<unsigned,J>> Split(const J& items,unsigned lanes,unsigned rotate=0) {
    std::vector<std::pair<unsigned,J>> out; size_t offset=0;
    for(unsigned lane=0;lane<lanes;++lane) {
        size_t count=items.size()/lanes+(lane<items.size()%lanes);
        for(size_t i=0;i<count;++i) out.emplace_back(lane,items.at(offset+(i+rotate)%count));
        offset+=count;
    }
    return out;
}
}

LayerPlan ValidateCannPlan(const J& p) {
    const auto& c=p.at("input");
    std::set<std::string> fields={"schema_version","graph_id","wave_id","service_id","role","group_id","ready_ns","layer_id",
        "rank_ids","rank_to_ub_node","hidden_dim","top_k","dispatch_element_bytes","combine_element_bytes","application_metadata_bytes",
        "chunk_bytes","send_slots_per_rank","template","expert_placement","tokens","compute_profile_ns","batch_limits","compute_dtypes","cann_profile"};
    if(c.contains("deployment")) fields.insert("deployment");
    if(c.contains("resource_budget")) fields.insert("resource_budget");
    Fields(c,fields);
    Check(p.at("schema_version")=="moe-ub-execution-plan/3" && c.at("schema_version")=="moe-ub-layer-fixture/3" && c.at("role")=="D","requires batched Decode");
    const auto& cp=c.at("cann_profile");
    Fields(cp,{"source_revision","algorithm","aiv_lanes","window_bytes","local_cost_ns"});
    Check(cp.at("source_revision")==revision && cp.at("algorithm")=="a3_fullmesh_v1_dynamic_int8_tp1","unsupported source or call configuration");
    const auto& cost=cp.at("local_cost_ns");
    Fields(cost,{"dispatch_reorder_per_input","status_check","result_pack_per_input","reduction_per_input"});
    for(const auto& v:cost) Num(v,1);
    const auto& profile=c.at("compute_profile_ns");
    Fields(profile,{"attention_route_pack","expert_per_input","expert_local_merge","combine_unpack_merge","next_layer"});
    Check(profile.at("expert_local_merge")==0,"remote aggregation is not this CANN path");
    for(auto it=profile.begin();it!=profile.end();++it) Num(it.value(),it.key()=="expert_local_merge"||it.key()=="next_layer"?0:1);
    Check(c.at("compute_dtypes")==J({{"compute","bfloat16"},{"kv","bfloat16"},{"dispatch","int8"},{"combine","bfloat16"}}) &&
          c.at("dispatch_element_bytes")==1 && c.at("combine_element_bytes")==2,"dtype mismatch");
    const auto& ranks=c.at("rank_ids"); const auto& tokens=c.at("tokens"); const auto& placement=c.at("expert_placement");
    auto world=Num(ranks.size(),2,768), experts=Num(placement.size(),1,1024), h=Num(c.at("hidden_dim"),1024,8192);
    auto topk=Num(c.at("top_k"),1,16), bs=Num(ValidateBatchLimits(c.at("batch_limits")),1,512), lanes=Num(cp.at("aiv_lanes"),1,48);
    Check(experts%world==0,"unequal local expert counts");
    for(size_t e=0;e<experts;++e) Check(placement.at(e)==ranks.at(e/(experts/world)),"requires contiguous expertShardType=0");
    uint64_t min_window=2*bs*(experts*Align(Align(h*2,32)+44,512)+topk*Align(h*2,512));
    auto window=Num(cp.at("window_bytes"),min_window,INT64_MAX-1048576-48*2*Align(Align(h,32)+44,32));
    uint64_t staging=lanes*2*Align(Align(h,32)+44,32);
    J buffers={{"minimum_data_window_bytes",min_window},{"data_window_bytes",window},{"state_window_bytes",1048576},
        {"staging_bytes",staging},{"total_bytes",window+1048576+staging},{"reuse_rule","no_reuse_before_local_graph_completion"},
        {"native_double_window_overlap_modeled",false},{"logical_input_slots_per_rank",bs},{"global_bs",bs*world},
        {"inactive_slots","masked_no_vector_traffic"},{"dispatch_window_stride",Align(Align(h,32)+44,512)},{"combine_window_stride",Align(h*2,512)}};
    Check(p.at("communication_buffers")==buffers,"buffer contract mismatch");
    J evidence={{"source_revision",revision},{"source_path","mc2/moe_distribute_dispatch_v2/op_kernel/moe_distribute_dispatch_v2.h"},
        {"combine_source_path","mc2/moe_distribute_combine_v2/op_kernel/moe_distribute_combine_v2.h"},
        {"transaction_mapping","URMA_WRITE/RTP_reference_not_native_MTE"},
        {"scheduling","balanced_core_lanes_with_transaction_done_serialization_reference"},
        {"target_runtime_executed",false},{"local_cost_basis","declared_reference"}};
    Check(p.at("communication_evidence")==evidence && p.at("communication_profile_basis")=="runtime_adapted" &&
        p.at("calibration_status")=="unvalidated_reference","unsupported fidelity claim");
    std::map<int,std::string> chips; std::map<int,uint32_t> endpoints;
    for(size_t i=0;i<ranks.size();++i) endpoints[ranks.at(i).get<int>()]=c.at("rank_to_ub_node").at(i).get<uint32_t>();
    if(c.contains("deployment")) for(const auto& r:c.at("deployment").at("ranks")) chips[r.at("rank_id")]=r.at("chip_id");
    auto loc=[&](int src,int dst)->std::string { return src==dst?"same_rank":!chips.empty()&&chips.at(src)==chips.at(dst)?"same_chip":"cross_chip"; };
    const auto budget=resources::Budget(c); const auto usage=resources::Cann(c);
    resources::Check(usage,budget);
    if(!budget.is_null()) Check(p.at("resource_usage")==usage,"preflight count mismatch");
    J contributions=J::array(), operations=J::array(); std::map<int,uint64_t> counts,local;
    for(size_t i=0;i<tokens.size();++i) {
        const auto& t=tokens.at(i);int src=t.at("src_rank");++local[src];
        for(size_t k=0;k<t.at("expert_ids").size();++k) {
            auto e=t.at("expert_ids").at(k).get<size_t>();int dst=placement.at(e);++counts[dst];
            contributions.push_back({{"source_record_index",i},{"topk_index",k},{"expert_id",e},{"src_rank",src},{"dst_rank",dst},{"locality",loc(src,dst)}});
        }
    }
    Check(p.at("contributions")==contributions,"expert contributions mismatch");
    std::map<std::string,std::string> opids;
    for(const std::string kind:{"dispatch","combine"}) {
        std::string id=c.at("wave_id").get<std::string>()+".L"+std::to_string(c.at("layer_id").get<uint64_t>())+"."+kind;
        opids[kind]=id;
        operations.push_back({{"operation_id",id},{"kind",kind},{"service_id",c.at("service_id")},{"role",c.at("role")},
            {"wave_id",c.at("wave_id")},{"layer_id",c.at("layer_id")},{"group_id",c.at("group_id")},{"participants",ranks}});
    }
    Check(p.at("operations")==operations,"operation registry mismatch");
    J messages=J::array();
    auto add=[&](std::string kind,std::string role,int src,int dst,unsigned lane,J source,J expert,J count) {
        if(src==dst)return;
        bool vector=role=="data";uint64_t useful=vector?h*(kind=="dispatch"?1:2):0;J regions=J::array();
        auto region=[&](uint64_t a,uint64_t b,const char* type) { if(a<b) regions.push_back({{"begin",a},{"end",b},{"kind",type}}); };
        if(vector&&kind=="dispatch") {
            region(0,h,"business");region(h,Align(h,32),"padding");region(Align(h,32),Align(h,32)+4,"metadata");
            region(Align(h,32)+4,Align(h,32)+32,"padding");region(Align(h,32)+32,Align(h,32)+44,"metadata");
        } else region(0,vector?useful:32,vector?"business":"metadata");
        J m={{"message_id",messages.size()},{"kind",kind},{"message_role",role},{"src_rank",src},{"dst_rank",dst},{"lane",lane},
             {"regions",regions},{"payload_bytes",regions.back().at("end")},{"sources",J::array()}};
        if(!source.is_null()) {
            const auto& t=tokens.at(source.at("source_record_index").get<size_t>());J s;
            for(const char* k:{"request_id","phase","token_index","layer_id"})s[k]=t.at(k);
            s["source_record_index"]=source.at("source_record_index");s["expert_ids"]=J::array({source.at("expert_id")});s["topk_index"]=source.at("topk_index");
            m["sources"].push_back(s);
        }
        if(!expert.is_null()) {m["expert_id"]=expert;m["token_count"]=count;}
        messages.push_back(m);
    };
    for(int rank:ranks) {
        J items=J::array();for(const auto& x:contributions)if(x.at("src_rank")==rank)items.push_back(x);
        for(const auto& [lane,x]:Split(items,lanes))add("dispatch","data",rank,x.at("dst_rank"),lane,x,nullptr,nullptr);
    }
    J expert_ids=J::array();for(size_t e=0;e<experts;++e)expert_ids.push_back(e);
    for(int rank:ranks) for(const auto& [lane,e]:Split(expert_ids,lanes)) {
        uint64_t count=0;for(const auto& x:contributions)if(x.at("src_rank")==rank&&x.at("expert_id")==e)++count;
        add("dispatch","status",rank,placement.at(e.get<size_t>()),lane,nullptr,e,count);
    }
    for(size_t ri=0;ri<ranks.size();++ri) {
        int rank=ranks.at(ri);J items=J::array();for(const auto& x:contributions)if(x.at("dst_rank")==rank)items.push_back(x);
        auto key=[&](const J& x) {return std::make_tuple(x.at("expert_id").get<int>(),
            std::distance(ranks.begin(),std::find(ranks.begin(),ranks.end(),x.at("src_rank"))),x.at("source_record_index").get<int>());};
        std::sort(items.begin(),items.end(),[&](const J& a,const J& b){return key(a)<key(b);});
        for(const auto& [lane,x]:Split(items,lanes,ri)) {
            add("combine","data",rank,x.at("src_rank"),lane,x,nullptr,nullptr);
            add("combine","status",rank,x.at("src_rank"),lane,x,nullptr,nullptr);
        }
    }
    J transfers=J::array(); const auto chunk=Num(c.at("chunk_bytes"),1,UINT32_MAX);
    std::set<uint64_t> ids,tasks,tags;
    for(const auto& m:messages) {
        bool same=loc(m.at("src_rank"),m.at("dst_rank"))=="same_chip";
        uint64_t bytes=m.at("payload_bytes"),limit=same||m.at("message_role")=="status"?bytes:chunk,index=0;
        for(uint64_t begin=0;begin<bytes;begin+=limit,++index) {
            uint64_t end=std::min(begin+limit,bytes),size=end-begin;J regions=J::array();std::map<std::string,uint64_t> totals;
            for(const auto& r:m.at("regions")) {
                auto a=std::max(begin,r.at("begin").get<uint64_t>()),b=std::min(end,r.at("end").get<uint64_t>());
                if(a<b) {regions.push_back({{"begin",a-begin},{"end",b-begin},{"kind",r.at("kind")}});totals[r.at("kind")]+=b-a;}
            }
            Check(transfers.size()<usage.at("transfers").get<uint64_t>() && transfers.size()<p.at("transfers").size(),"too many or missing transfers");
            const auto& actual=p.at("transfers").at(transfers.size());
            auto id=Num(actual.at("transfer_id"),1,UINT64_MAX),tag=Num(actual.at("comm_tag"),1,499999999);
            Check(ids.insert(id).second&&tags.insert(tag).second,"reused transfer/tag");
            J t={{"transfer_id",id},{"comm_tag",tag},{"operation_id",opids.at(m.at("kind"))},
                {"payload_bytes",size},{"business_payload_bytes",totals["business"]},{"application_metadata_bytes",totals["metadata"]},
                {"padding_bytes",totals["padding"]},{"payload_regions",regions},{"chunk_index",index},{"task_byte_range",same?J(nullptr):J::array({0,size})},
                {"pair_byte_range",J::array({begin,end})},{"pair_payload_bytes",bytes},
                {"transport_profile_id",same?"same-chip-local-reference":"urma-write-rtp-default"},{"priority",same?J(nullptr):J(7)}};
            for(const char* k:{"kind","message_role","message_id","lane","src_rank","dst_rank","sources"})t[k]=m.at(k);
            if(m.contains("expert_id")){t["expert_id"]=m.at("expert_id");t["token_count"]=m.at("token_count");}
            if(!same) {auto task=Num(actual.at("ub_task_id"),0,UINT32_MAX);Check(tasks.insert(task).second,"reused task");t["ub_task_id"]=task;}
            transfers.push_back(t);
        }
    }
    Check(transfers.size()==p.at("transfers").size(),"extra transfers");
    J graphs=J::array();
    for(int rank:ranks) {
        J nodes=J::array(),local_prepares=J::object();uint64_t total_work=0,ready=Num(c.at("ready_ns"));
        auto comp=[&](const char* name,uint64_t count,uint64_t cost_value,J deps)->J {
            Check(!count||cost_value<=(uint64_t(INT64_MAX)-ready-total_work)/count,"duration overflow");
            auto work=count*cost_value;if(!work)return nullptr;total_work+=work;
            auto id=nodes.size()+1;
            nodes.push_back({{"node_id",id},{"rank_id",rank},{"kind","COMP"},{"name",name},{"duration_ns",work},{"resource_id","gpu_compute"},{"deps",Deps(deps)}});return id;
        };
        auto comm=[&](J& t,const char* direction,J deps)->J {
            auto id=nodes.size()+1;
            std::string side=std::string(direction)=="SEND"?"send":"recv";
            nodes.push_back({{"node_id",id},{"rank_id",rank},{"kind",direction},{"transfer_id",t.at("transfer_id")},
                {"name",t.at("kind").get<std::string>()+"-"+t.at("message_role").get<std::string>()+"-"+side},{"deps",Deps(deps)}});
            t[side+"_node_id"]=id;return id;
        };
        auto sends=[&](std::string kind,J deps,J local_ready) {
            J done=J::array(),data_done=J::array();std::map<unsigned,J> last;
            for(auto& t:transfers) {
                if(t.at("kind")!=kind||t.at("src_rank")!=rank)continue;
                J before=deps;unsigned lane=t.at("lane");
                if(t.at("transport_profile_id")=="same-chip-local-reference")Append(before,local_ready);
                if(last.contains(lane))before.push_back(last.at(lane));
                if(kind=="dispatch"&&t.at("message_role")=="status")Append(before,data_done);
                J node=comm(t,"SEND",before);last[lane]=node;done.push_back(node);
                if(t.at("message_role")=="data")data_done.push_back(node);
            }
            return done;
        };
        auto recvs=[&](std::string kind,J deps) {
            J done=J::array();for(auto& t:transfers)if(t.at("kind")==kind&&t.at("dst_rank")==rank)done.push_back(comm(t,"RECV",deps));return done;
        };
        auto asdeps=[](J n) {return n.is_null()?J::array():J::array({n});};
        auto prepare=comp("attention-route-quant-pack",local[rank],Num(profile.at("attention_route_pack")),J::array());J start=asdeps(prepare);
        auto local_prepare=[&](std::string kind,J deps) {
            uint64_t n=0;for(const auto& x:contributions)if(x.at("locality")=="same_chip"&&x.at(kind=="dispatch"?"src_rank":"dst_rank")==rank)++n;
            auto work=c.contains("deployment")?Num(c.at("deployment").at("local_prepare_ns_per_vector").at(kind)):0;
            auto node=comp((kind+"-local-prepare").c_str(),n,work,deps);local_prepares[kind]=node;return asdeps(node);
        };
        J lp=local_prepare("dispatch",start),dispatch=start;Append(dispatch,sends("dispatch",start,lp));Append(dispatch,recvs("dispatch",start));Append(dispatch,lp);
        auto checked_sum=[&](uint64_t a,uint64_t av,uint64_t b,uint64_t bv) {
            Check((!a||av<=uint64_t(INT64_MAX)/a)&&(!b||bv<=(uint64_t(INT64_MAX)-a*av)/b),"cost overflow");return a*av+b*bv;
        };
        auto reorder=comp("dispatch-status-check-reorder",1,checked_sum(counts[rank],Num(cost.at("dispatch_reorder_per_input")),experts,Num(cost.at("status_check"))),dispatch);
        auto expert=comp("expert-compute",counts[rank],Num(profile.at("expert_per_input")),asdeps(reorder));
        J computed=expert.is_null()?asdeps(reorder):asdeps(expert);
        auto pack=comp("expert-result-pack",counts[rank],Num(cost.at("result_pack_per_input")),computed);J packed=pack.is_null()?computed:asdeps(pack);
        J lc=local_prepare("combine",packed),combine=packed;Append(combine,sends("combine",packed,lc));Append(combine,recvs("combine",packed));Append(combine,lc);
        auto reduction=checked_sum(1,Num(cost.at("status_check")),1,Num(cost.at("reduction_per_input")));
        auto finish_cost=checked_sum(1,Num(profile.at("combine_unpack_merge")),topk,reduction);
        auto finish=comp("combine-token-weighted-reduction",local[rank],finish_cost,combine);
        auto next=comp("next-layer",local[rank],Num(profile.at("next_layer")),finish.is_null()?combine:asdeps(finish));
        J graph={{"rank_id",rank},{"nodes",nodes},{"phase_nodes",{{"prepare",prepare},{"dispatch_reorder",reorder},{"expert",expert},{"local_merge",pack},{"finish",finish},{"next_layer",next}}}};
        if(c.contains("deployment"))graph["local_prepare_nodes"]=local_prepares;graphs.push_back(graph);
    }
    Check(p.at("transfers")==transfers,"transfer layout, source, state or dependency mismatch");
    Check(p.at("rank_graphs")==graphs,"graph or stage mismatch");
    Check(resources::Actual(p)==usage,"actual/preflight mismatch");
    LayerPlan result{p,{},{},{}};
    for(const auto& g:graphs)for(const auto& n:g.at("nodes"))result.nodes.emplace(std::make_pair(g.at("rank_id").get<int>(),n.at("node_id").get<uint64_t>()),n);
    for(const auto& t:transfers) {
        auto id=t.at("transfer_id").get<uint64_t>(),bytes=t.at("payload_bytes").get<uint64_t>();
        int src=t.at("src_rank"),dst=t.at("dst_rank"),tag=t.at("comm_tag");std::string graph=c.at("graph_id"),op=t.at("operation_id");
        if(t.contains("ub_task_id")) {
            WriteDescriptor d{id,t.at("ub_task_id").get<uint64_t>(),graph,t.at("send_node_id").get<uint64_t>(),t.at("recv_node_id").get<uint64_t>(),src,dst,tag,bytes,op,
                t.at("chunk_index").get<uint32_t>(),t.at("pair_byte_range").at(0).get<uint64_t>(),t.at("pair_payload_bytes").get<uint64_t>(),endpoints.at(src),endpoints.at(dst)};
            d.payload_regions.emplace();for(const auto& r:t.at("payload_regions"))d.payload_regions->push_back({r.at("begin"),r.at("end"),r.at("kind")});result.descriptors.push_back(d);
        } else result.local_descriptors.push_back({id,graph,t.at("send_node_id"),t.at("recv_node_id"),src,dst,tag,bytes,op});
    }
    return result;
}
}
