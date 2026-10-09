#include "layer_plan.hh"
#include "graph_resources.hh"
#include <algorithm>
#include <set>
#include <tuple>

namespace moe_ub {
namespace {
using J=nlohmann::json;
constexpr const char* name="cann_a3_v850_store_resources_v1";
void Check(bool ok,const char* why) { if(!ok) throw std::runtime_error(std::string("A3 resources: ")+why); }
uint64_t Id(const J& j) {
    Check(j.is_number_integer() && !j.is_boolean() && (j.is_number_unsigned() || j.get<int64_t>()>=0),"invalid integer");
    return j.get<uint64_t>();
}
uint64_t N(const J& j) {auto n=Id(j);Check(n<=INT64_MAX,"integer overflow");return n;}
uint64_t A(uint64_t n,uint64_t a) {return (n+a-1)/a*a;}
J Unique(J deps) {std::set<uint64_t> s;for(const auto& d:deps){auto n=N(d);Check(n>0,"zero dependency");s.insert(n);}return J(s);}
void Extend(J& a,const J& b) {for(const auto& x:b)a.push_back(x);}
std::vector<std::pair<unsigned,J>> Split(const J& items,unsigned cores,unsigned rotate=0) {
    std::vector<std::pair<unsigned,J>> out;size_t offset=0;
    for(unsigned core=0;core<cores;++core){size_t count=items.size()/cores+(core<items.size()%cores);
        for(size_t i=0;i<count;++i)out.emplace_back(core,items.at(offset+(i+rotate)%count));
        offset+=count;}
    return out;
}
J LayoutConfig(J c) {
    const auto& p=c.at("cann_profile");
    std::set<std::string> fields;for(auto i=p.begin();i!=p.end();++i)fields.insert(i.key());
    Check(fields==std::set<std::string>{"source_revision","algorithm","platform_aiv_cores","core_count_basis","copy_fence","window_bytes","local_cost_ns"},"profile fields");
    Check(p.at("algorithm")=="a3_fullmesh_v1_store_resources_tp1" && p.at("core_count_basis")=="declared_platform_reference","profile basis");
    Check(N(p.at("platform_aiv_cores"))>=1 && N(p.at("platform_aiv_cores"))<=48,"core count");
    Check(p.at("copy_fence")=="data_ready" || p.at("copy_fence")=="local_read_complete","copy fence");
    Check(N(c.at("send_slots_per_rank"))==0 && c.at("template")==name,"send capacity model");
    c["template"]="cann_a3_v850_fullmesh_v1";c["send_slots_per_rank"]=uint64_t(1);
    auto& q=c["cann_profile"];q["aiv_lanes"]=q.at("platform_aiv_cores");
    for(const auto* key:{"platform_aiv_cores","core_count_basis","copy_fence"})q.erase(key);
    q["algorithm"]="a3_fullmesh_v1_dynamic_int8_tp1";return c;
}
J ResourceDescription(const J& c) {
    const auto& p=c.at("cann_profile");uint64_t cores=N(p.at("platform_aiv_cores"));
    uint64_t h=N(c.at("hidden_dim")),b=ValidateBatchLimits(c.at("batch_limits")),k=N(c.at("top_k")),e=c.at("expert_placement").size();
    uint64_t state=A((e+cores-1)/cores,8)*32,indices=A(b*k*4,32);
    auto temporary=std::max({h*4,indices,b*A(k,32)*2,A(b*k*2,256)/2});
    auto candidate=state+indices+3*temporary+2*A(h*2,32)+2*A(A(h,32)+44,32);
    return {{"platform_aiv_cores",cores},{"core_count_basis",p.at("core_count_basis")},
        {"dispatch_buffers_per_core",candidate>170*1024?1:2},{"combine_buffers_per_core",2},
        {"dispatch_buffer_selection_bytes",candidate},{"dispatch_selection_limit_bytes",170*1024},
        {"local_read_completion","native_requests_created_all_useful_bytes"},{"copy_fence",p.at("copy_fence")},
        {"send_capacity","native_worker_outstanding_and_node_request_id_pool"},
        {"compute_resources","one_per_aiv_and_one_gpu_compute"},{"hardware_calibrated",false}};
}
J Schedule(J p,const J& c) {
    const auto& cp=c.at("cann_profile");const auto& ranks=c.at("rank_ids");
    unsigned cores=N(cp.at("platform_aiv_cores"));auto info=ResourceDescription(c);
    const auto& cost=cp.at("local_cost_ns");const auto& profile=c.at("compute_profile_ns");uint64_t k=N(c.at("top_k"));
    std::map<int,unsigned> position;std::map<int,J> own,remote,tokens;
    for(unsigned i=0;i<ranks.size();++i){int r=ranks.at(i);position[r]=i;own[r]=remote[r]=tokens[r]=J::array();}
    for(size_t i=0;i<c.at("tokens").size();++i)tokens.at(c.at("tokens").at(i).at("src_rank").get<int>()).push_back(i);
    for(const auto& x:p.at("contributions")){own.at(x.at("src_rank")).push_back(x);remote.at(x.at("dst_rank")).push_back(x);}
    using VectorKey=std::tuple<std::string,std::string,int,uint64_t,uint64_t>;
    std::map<VectorKey,std::vector<size_t>> vector;
    std::map<std::pair<int,int>,size_t> states;
    std::map<std::pair<std::string,int>,std::vector<size_t>> incoming;
    auto& transfers=p["transfers"];
    for(size_t i=0;i<transfers.size();++i){auto& t=transfers[i];
        if(t.contains("ub_task_id")){t["operation"]="STORE";t["initiator_rank"]=t["data_owner_rank"]=t.at("src_rank");
            t["consumer_rank"]=t.at("dst_rank");t["transport_profile_id"]="native-store-default";}
        if(!t.at("sources").empty()){const auto& s=t.at("sources").at(0);
            vector[{t.at("kind"),t.at("message_role"),t.at("src_rank"),N(s.at("source_record_index")),N(s.at("topk_index"))}].push_back(i);
        }else states[{t.at("src_rank"),t.at("expert_id")}]=i;
        incoming[{t.at("kind"),t.at("dst_rank")}].push_back(i);
    }
    J graphs=J::array();
    for(int rank:ranks){J nodes=J::array(),members=J::object();std::map<uint64_t,uint64_t> receives;
        uint64_t extra_dispatch=0,extra_combine=0,local_dispatch=0,local_combine=0;
        if(c.contains("endpoint_prepare"))for(const auto& x:c.at("endpoint_prepare").at("ranks"))if(x.at("rank_id")==rank){
            extra_dispatch=N(x.at("dispatch_ns_per_local_token"));extra_combine=N(x.at("combine_ns_per_expert_input"));}
        if(c.contains("deployment")){const auto& l=c.at("deployment").at("local_prepare_ns_per_vector");
            local_dispatch=N(l.at("dispatch"));local_combine=N(l.at("combine"));}
        auto add=[&](const std::string& kind,const std::string& label,J deps,J fields=J::object())->uint64_t {
            uint64_t id=nodes.size()+1;fields["node_id"]=id;fields["rank_id"]=rank;fields["kind"]=kind;
            fields["name"]=label;fields["deps"]=Unique(deps);nodes.push_back(fields);return id;
        };
        auto event=[&](const std::string& label,J deps,const J* transfer=nullptr,const std::string& observed="barrier")->uint64_t {
            J fields={{"event",observed},{"resource_id",""}};if(transfer)fields["transfer_id"]=transfer->at("transfer_id");
            return add("EVENT",label,deps,fields);
        };
        auto work=[&](const std::string& label,uint64_t duration,J deps,int core=-1,const std::string& member="")->uint64_t {
            Check(duration<=uint64_t(INT64_MAX)-N(c.at("ready_ns")),"local work overflow");
            auto id=duration?add("COMP",label,deps,{{"duration_ns",duration},{"resource_id",core<0?"gpu_compute":"aiv:"+std::to_string(core)}}):event(label,deps);
            if(!member.empty()){if(!members.contains(member))members[member]=J::array();members[member].push_back(id);}return id;
        };
        auto send=[&](J& t,J deps)->std::pair<uint64_t,uint64_t>{
            t["send_node_id"]=add("SEND",t.at("kind").get<std::string>()+"-"+t.at("message_role").get<std::string>()+"-send",deps,{{"transfer_id",t.at("transfer_id")}});
            auto read=event("local-read-complete",deps,&t,"local_read_complete");
            auto fence=cp.at("copy_fence")=="local_read_complete"?read:event("copy-fence",deps,&t,"data_ready");return {read,fence};
        };
        auto receive=[&](std::string kind,uint64_t entry){for(auto index:incoming[{kind,rank}]){
            auto& t=transfers[index];auto n=add("RECV",kind+"-"+t.at("message_role").get<std::string>()+"-recv",J::array({entry}),{{"transfer_id",t.at("transfer_id")}});
            t["recv_node_id"]=n;receives[Id(t.at("transfer_id"))]=n;}};
        auto copies=[&](std::string kind,const J& x,uint64_t prepared)->std::pair<uint64_t,uint64_t>{
            J reads=J::array(),fences=J::array();for(auto index:vector[{kind,"data",rank,N(x.at("source_record_index")),N(x.at("topk_index"))}]){
                auto [read,fence]=send(transfers[index],J::array({prepared}));reads.push_back(read);fences.push_back(fence);}
            if(reads.empty())reads.push_back(prepared);
            if(fences.empty())fences.push_back(prepared);
            return {event(kind+"-buffer-reusable",reads),event(kind+"-data-fence",fences)};
        };
        auto entry=event("dispatch-entry",J::array());receive("dispatch",entry);
        std::map<unsigned,uint64_t> previous,ordinal;std::map<std::pair<unsigned,unsigned>,uint64_t> buffers;
        J data_done=J::array();uint64_t unit=N(profile.at("attention_route_pack"))+extra_dispatch;
        for(const auto& [core,x]:Split(own.at(rank),cores)){
            unsigned slot=ordinal[core]++%N(info.at("dispatch_buffers_per_core"));J deps=J::array({entry});
            if(previous.count(core))deps.push_back(previous.at(core));
            if(buffers.count({core,slot}))deps.push_back(buffers.at({core,slot}));
            uint64_t duration=unit/k+(N(x.at("topk_index"))<unit%k)+(x.at("locality")=="same_chip"?local_dispatch:0);
            auto prep=work("dispatch-quant-pack",duration,deps,core,"prepare");previous[core]=prep;
            auto [read,fence]=copies("dispatch",x,prep);buffers[{core,slot}]=read;data_done.push_back(fence);
        }
        J all=J::array({entry});Extend(all,data_done);for(const auto& [key,value]:buffers)all.push_back(value);
        auto data_barrier=event("dispatch-cross-core-data-barrier",all);J status_done=J::array();previous.clear();
        J expert_ids=J::array();for(size_t i=0;i<c.at("expert_placement").size();++i)expert_ids.push_back(i);
        for(const auto& [core,e]:Split(expert_ids,cores)){
            J deps=J::array({data_barrier});if(previous.count(core))deps.push_back(previous.at(core));
            auto it=states.find({rank,e.get<int>()});if(it!=states.end()){
                auto [read,fence]=send(transfers[it->second],deps);previous[core]=read;status_done.push_back(fence);}
        }
        all=J::array({data_barrier});Extend(all,status_done);
        for(auto index:incoming[{"dispatch",rank}])all.push_back(receives.at(Id(transfers[index].at("transfer_id"))));
        auto arrived=event("dispatch-counts-and-data-ready",all);
        unsigned owned_experts=c.at("expert_placement").size()/ranks.size();std::map<unsigned,uint64_t> blocks,per_core;
        for(const auto& x:remote.at(rank))++blocks[(N(x.at("expert_id"))%owned_experts)*ranks.size()+position.at(x.at("src_rank"))];
        for(const auto& [core,block]:Split(expert_ids,cores))per_core[core]+=N(cost.at("status_check"))+blocks[N(block)]*N(cost.at("dispatch_reorder_per_input"));
        J reorders=J::array();for(auto [core,duration]:per_core)reorders.push_back(work("dispatch-reorder",duration,J::array({arrived}),core,"reorder"));
        if(reorders.empty())reorders.push_back(arrived);
        auto dispatched=event("dispatch-operator-complete",reorders);
        auto expert=work("expert-compute",remote.at(rank).size()*N(profile.at("expert_per_input")),J::array({dispatched}));
        auto combined_entry=event("combine-entry",J::array({expert}));receive("combine",combined_entry);
        previous.clear();buffers.clear();ordinal.clear();std::map<unsigned,J> end_by_core;std::map<std::pair<uint64_t,uint64_t>,uint64_t> local_results;
        for(unsigned core=0;core<cores;++core)end_by_core[core]=J::array();
        J items=remote.at(rank);
        auto key=[&](const J& x){return std::make_tuple(N(x.at("expert_id")),position.at(x.at("src_rank")),N(x.at("source_record_index")));};
        std::sort(items.begin(),items.end(),[&](const J& a,const J& b){return key(a)<key(b);});
        for(const auto& [core,x]:Split(items,cores,position.at(rank))){
            unsigned slot=ordinal[core]++%2;J deps=J::array({combined_entry});if(previous.count(core))deps.push_back(previous.at(core));
            if(buffers.count({core,slot}))deps.push_back(buffers.at({core,slot}));
            auto duration=N(cost.at("result_pack_per_input"))+extra_combine+(x.at("locality")=="same_chip"?local_combine:0);
            auto prep=work("combine-result-pack",duration,deps,core,"pack");auto [read,fence]=copies("combine",x,prep);buffers[{core,slot}]=read;
            auto i=N(x.at("source_record_index")),j=N(x.at("topk_index"));const auto& status=vector[{"combine","status",rank,i,j}];
            if(!status.empty()){auto [sr,sf]=send(transfers[status.at(0)],J::array({fence}));previous[core]=sr;end_by_core[core].push_back(sf);}
            else{previous[core]=fence;end_by_core[core].push_back(fence);local_results[{i,j}]=fence;}
        }
        std::map<unsigned,uint64_t> core_end;for(unsigned core=0;core<cores;++core){
            all=J::array({combined_entry});Extend(all,end_by_core.at(core));core_end[core]=event("combine-core-sends-complete",all);}
        previous.clear();J reductions=J::array();
        for(const auto& [core,item]:Split(tokens.at(rank),cores)){uint64_t i=N(item);J deps=J::array({core_end.at(core)});
            if(previous.count(core))deps.push_back(previous.at(core));
            const auto& token=c.at("tokens").at(i);
            for(size_t j=0;j<token.at("expert_ids").size();++j){int owner=c.at("expert_placement").at(N(token.at("expert_ids").at(j)));
                if(owner==rank)deps.push_back(local_results.at({i,j}));else for(const std::string role:{"data","status"})
                    for(auto index:vector[{"combine",role,owner,i,j}])deps.push_back(receives.at(Id(transfers[index].at("transfer_id"))));}
            auto duration=N(profile.at("combine_unpack_merge"))+k*(N(cost.at("status_check"))+N(cost.at("reduction_per_input")));
            auto n=work("combine-token-reduction",duration,deps,core,"reduce");previous[core]=n;reductions.push_back(n);
        }
        all=reductions;for(const auto& [core,id]:core_end)all.push_back(id);
        auto finish=event("combine-operator-complete",all);J next=nullptr;
        if(N(profile.at("next_layer")))next=work("next-layer",tokens.at(rank).size()*N(profile.at("next_layer")),J::array({finish}));
        uint64_t total=0;for(const auto& n:nodes)if(n.contains("duration_ns")){auto v=N(n.at("duration_ns"));Check(v<=uint64_t(INT64_MAX)-N(c.at("ready_ns"))-total,"work sum overflow");total+=v;}
        graphs.push_back({{"rank_id",rank},{"nodes",nodes},{"phase_nodes",{{"prepare",entry},{"dispatch_reorder",dispatched},{"expert",expert},
            {"local_merge",combined_entry},{"finish",finish},{"next_layer",next}}},{"phase_members",members},
            {"terminal_nodes",J::array({next.is_null()?J(finish):next})},{"compute_work_ns",total}});
    }
    p["input"]=c;p["communication_template"]=name;p["send_slots_per_rank"]=0;p["rank_graphs"]=graphs;p["a3_resources"]=info;
    p["communication_evidence"]["transaction_mapping"]="platform_native_STORE_not_native_MTE";
    p["communication_evidence"]["scheduling"]="source_cores_local_buffers_and_explicit_events_reference";
    auto& b=p["communication_buffers"];uint64_t h=N(c.at("hidden_dim"));
    b["staging_bytes"]=cores*(N(info.at("dispatch_buffers_per_core"))*A(A(h,32)+44,32)+2*A(h*2,32));
    b["total_bytes"]=N(b.at("data_window_bytes"))+1048576+N(b.at("staging_bytes"));
    if(c.contains("resource_budget"))p["resource_usage"]=resources::Actual(p);
    return p;
}
}
LayerPlan ValidateA3Plan(const J& p,bool retain) {
    const std::set<std::string> allowed={"schema_version","graph_id","wave_id","rank_ids","rank_to_ub_node","ready_ns",
        "instance_batches","communication_template","communication_profile_basis","communication_evidence","compute_source",
        "route_source","calibration_status","sender_completion","send_slots_per_rank","operations","transfers","rank_graphs",
        "expert_input_counts","input","contributions","communication_buffers","batch_limits","compute_inputs","deployment",
        "resource_usage","input_provenance","a3_resources"};
    for(auto it=p.begin();it!=p.end();++it)Check(allowed.contains(it.key()),"unknown plan field");
    const auto& config=p.at("input");auto c=LayoutConfig(config);
    J projected=p;projected.erase("a3_resources");projected["input"]=c;
    projected["communication_template"]="cann_a3_v850_fullmesh_v1";projected["send_slots_per_rank"]=uint64_t(1);
    J reference;ValidateCannPlan(projected,false,&reference);
    ValidateLayerPlan(reference,false);
    auto expected=Schedule(std::move(reference),config);
    Check(expected==p,"layout, work, resource or dependency mismatch");
    resources::Check(resources::Actual(p),resources::Budget(config));
    LayerPlan result{retain?p:J{},{},{},{}};std::map<int,uint32_t> endpoints;
    for(size_t i=0;i<p.at("rank_ids").size();++i)endpoints[p.at("rank_ids")[i]]=p.at("rank_to_ub_node")[i];
    if(retain)for(const auto& g:p.at("rank_graphs"))for(const auto& n:g.at("nodes"))result.nodes.emplace(std::make_pair(g.at("rank_id").get<int>(),N(n.at("node_id"))),n);
    for(const auto& t:p.at("transfers")){int src=t.at("src_rank"),dst=t.at("dst_rank");
        if(!t.contains("ub_task_id")){result.local_descriptors.push_back({t.at("transfer_id"),p.at("graph_id"),t.at("send_node_id"),t.at("recv_node_id"),src,dst,t.at("comm_tag"),t.at("payload_bytes"),t.at("operation_id")});continue;}
        WriteDescriptor d{t.at("transfer_id"),t.at("ub_task_id"),p.at("graph_id"),t.at("send_node_id"),t.at("recv_node_id"),src,dst,t.at("comm_tag"),t.at("payload_bytes"),t.at("operation_id"),t.at("chunk_index"),t.at("pair_byte_range").at(0),t.at("pair_payload_bytes"),endpoints.at(src),endpoints.at(dst)};
        d.operation=TransferOperation::Store;d.local_facts=true;d.metadata_category=MetadataCategory(t.at("kind"),t.at("message_role"));d.payload_regions.emplace();
        for(const auto& r:t.at("payload_regions"))d.payload_regions->push_back({r.at("begin"),r.at("end"),r.at("kind")});
        result.descriptors.push_back(d);
    }
    return result;
}
}
