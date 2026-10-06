#include "layer_plan.hh"
#include "graph_resources.hh"
#include <set>
#include <stdexcept>

namespace moe_ub {
namespace {
using J = nlohmann::json;
constexpr const char* native = "cann_a3_v850_native_v1";
constexpr const char* cann = "cann_a3_v850_fullmesh_v1";
constexpr const char* store = "native-store-default";
constexpr const char* load = "native-load-default";
void Check(bool ok, const char* why) { if (!ok) throw std::runtime_error(std::string("Native plan: ")+why); }
uint64_t Identity(const J& v, uint64_t lo, uint64_t hi) {
    Check(v.is_number_integer() && (v.is_number_unsigned() || v.get<int64_t>()>=0), "identity must be an unsigned integer");
    auto n=v.get<uint64_t>(); Check(n>=lo && n<=hi,"identity outside range"); return n;
}
bool IsLoad(const J& t) { return t.at("transport_profile_id") == load; }
void RemapStages(J& g, bool inverse) {
    for (const auto* field : {"phase_nodes", "local_prepare_nodes"}) if (g.contains(field))
        for (auto& value : g[field]) if (!value.is_null()) {
            auto n = value.get<uint64_t>();
            Check(n && (!inverse || n%2==0) && (inverse || n<=UINT64_MAX/2), "invalid stage node ID");
            value = inverse ? n/2 : n*2;
        }
}
J Apply(J p, const J& config, const J& suppliedNotices) {
    const auto& profile = config.at("native_profile");
    Check(profile.is_object() && profile.size()==3 && profile.at("ready_notice_bytes")==32,
          "native profile fields or notification size");
    for (auto kind : {"dispatch","combine"})
        Check(profile.at(kind)=="STORE" || profile.at(kind)=="LOAD", "native operation");
    p["input"] = config; p["communication_template"] = native;
    p["communication_evidence"]["transaction_mapping"] = "platform_native_store_load_not_native_MTE";
    p["native_mapping"] = {{"data_layout",cann},{"status_operation","STORE"},
        {"load_readiness","metadata_STORE_before_consumer_LOAD"},{"graph_send","request_initiation"},
        {"graph_recv","peer_wait"},{"load_consumer_dependency","SEND_transaction_done_after_all_response_data"},
        {"hardware_calibrated",false}};
    std::map<uint64_t,J> notices;
    uint64_t nextMessage=0, index=0;
    for (const auto& t : p.at("transfers")) nextMessage=std::max(nextMessage,t.at("message_id").get<uint64_t>()+1);
    J ordered=J::array();
    for (auto& t : p["transfers"]) {
        for (auto field : {"send_node_id","recv_node_id"}) t[field]=t.at(field).get<uint64_t>()*2;
        if (t.at("transport_profile_id")=="same-chip-local-reference") continue;
        std::string op=t.at("message_role")=="data" ? profile.at(t.at("kind").get<std::string>()).get<std::string>() : "STORE";
        t["operation"]=op; t["data_owner_rank"]=t.at("src_rank"); t["consumer_rank"]=t.at("dst_rank");
        t["transport_profile_id"]=op=="STORE" ? store : load;
        if (op=="LOAD") {
            Check(index<suppliedNotices.size(), "missing ready notification");
            J notice=t;
            for (auto key : {"transfer_id","ub_task_id","comm_tag"}) notice[key]=suppliedNotices.at(index).at(key);
            ++index;
            notice["message_id"]=nextMessage++; notice["message_role"]="ready";
            notice["ready_for_message_id"]=t.at("message_id"); notice["operation"]="STORE";
            notice["transport_profile_id"]=store; notice["payload_bytes"]=32;
            notice["business_payload_bytes"]=0; notice["application_metadata_bytes"]=32; notice["padding_bytes"]=0;
            notice["payload_regions"]=J::array({{{"begin",0},{"end",32},{"kind","metadata"}}});
            notice["task_byte_range"]=J::array({0,32}); notice["pair_byte_range"]=J::array({0,32});
            notice["pair_payload_bytes"]=32; notice["sources"]=J::array();
            notice["send_node_id"]=t.at("send_node_id").get<uint64_t>()-1;
            notice["recv_node_id"]=t.at("recv_node_id").get<uint64_t>()-1;
            notice["initiator_rank"]=notice.at("src_rank");
            notices.emplace(t.at("transfer_id").get<uint64_t>(),notice); ordered.push_back(notice);
            std::swap(t["src_rank"],t["dst_rank"]); std::swap(t["send_node_id"],t["recv_node_id"]);
        }
        t["initiator_rank"]=t.at("src_rank");
    }
    Check(index==suppliedNotices.size(),"extra ready notifications");
    for (const auto& n : ordered) p["transfers"].push_back(n);
    for (auto& g : p["rank_graphs"]) {
        J nodes=J::array();
        for (auto n : g.at("nodes")) {
            n["layout_node_id"]=n.at("node_id");
            n["node_id"]=n.at("node_id").get<uint64_t>()*2;
            for (auto& d : n["deps"]) d=d.get<uint64_t>()*2;
            if (n.contains("transfer_id") && notices.count(n.at("transfer_id").get<uint64_t>())) {
                J notice=n;
                notice["node_id"]=n.at("node_id").get<uint64_t>()-1;
                notice["transfer_id"]=notices.at(n.at("transfer_id").get<uint64_t>()).at("transfer_id");
                notice["name"]=n.at("name").get<std::string>()+"-ready-notice";
                nodes.push_back(notice);
                n["kind"]=n.at("kind")=="SEND" ? "RECV" : "SEND";
                n["deps"]=J::array({notice.at("node_id")});
                n["name"]=n.at("name").get<std::string>()+"-native-load";
            }
            nodes.push_back(n);
        }
        g["nodes"]=nodes; RemapStages(g,false);
        std::map<uint64_t,uint64_t> dense;
        for (size_t i=0;i<nodes.size();++i) dense.emplace(nodes.at(i).at("node_id").get<uint64_t>(),i+1);
        for (auto& n : g["nodes"]) {
            n["node_id"]=dense.at(n.at("node_id").get<uint64_t>());
            for (auto& d : n["deps"]) d=dense.at(d.get<uint64_t>());
        }
        for (auto field : {"phase_nodes","local_prepare_nodes"}) if (g.contains(field))
            for (auto& v : g[field]) if (!v.is_null()) v=dense.at(v.get<uint64_t>());
        for (auto& t : p["transfers"]) {
            if (t.at("src_rank")==g.at("rank_id")) t["send_node_id"]=dense.at(t.at("send_node_id").get<uint64_t>());
            if (t.at("dst_rank")==g.at("rank_id")) t["recv_node_id"]=dense.at(t.at("recv_node_id").get<uint64_t>());
        }
    }
    if (config.contains("resource_budget")) p["resource_usage"]=resources::Actual(p);
    return p;
}
}

LayerPlan ValidateNativePlan(const J& p, bool retain) {
    J base=p, notices=J::array(), transfers=J::array();
    std::map<uint64_t,J> originals;
    std::map<std::pair<int,uint64_t>,uint64_t> layoutIds;
    for (const auto& g : p.at("rank_graphs")) for (const auto& n : g.at("nodes"))
        Check(layoutIds.emplace(std::make_pair(g.at("rank_id").get<int>(),n.at("node_id").get<uint64_t>()),
              n.at("layout_node_id").get<uint64_t>()).second,"repeated native node");
    std::set<uint64_t> ids,tasks,tags;
    for (const auto& t : p.at("transfers")) {
        auto id=Identity(t.at("transfer_id"),1,UINT64_MAX), tag=Identity(t.at("comm_tag"),1,499999999);
        Check(ids.insert(id).second && tags.insert(tag).second,"transfer identity");
        if (t.contains("ub_task_id")) {
            auto task=Identity(t.at("ub_task_id"),0,UINT32_MAX);
            Check(tasks.insert(task).second,"task identity");
        }
        if (t.at("message_role")=="ready") { notices.push_back(t); continue; }
        originals.emplace(id,t);
        J projected=t;
        if (IsLoad(t)) {
            std::swap(projected["src_rank"],projected["dst_rank"]);
            std::swap(projected["send_node_id"],projected["recv_node_id"]);
        }
        if (t.contains("ub_task_id")) projected["transport_profile_id"]="urma-write-rtp-default";
        projected["send_node_id"]=layoutIds.at({projected.at("src_rank").get<int>(),projected.at("send_node_id").get<uint64_t>()});
        projected["recv_node_id"]=layoutIds.at({projected.at("dst_rank").get<int>(),projected.at("recv_node_id").get<uint64_t>()});
        for (auto field : {"operation","initiator_rank","data_owner_rank","consumer_rank"}) projected.erase(field);
        transfers.push_back(projected);
    }
    base["transfers"]=transfers; base.erase("native_mapping"); base["communication_template"]=cann;
    base["input"]["template"]=cann; base["input"].erase("native_profile");
    base["communication_evidence"]["transaction_mapping"]="URMA_WRITE/RTP_reference_not_native_MTE";
    for (auto& g : base["rank_graphs"]) {
        std::map<uint64_t,J> all;
        std::map<uint64_t,J> ready;
        for (const auto& n : g.at("nodes")) Check(all.emplace(n.at("node_id").get<uint64_t>(),n).second,"repeated node");
        for (const auto& n : g.at("nodes")) if (n.contains("transfer_id") && !originals.count(n.at("transfer_id").get<uint64_t>()))
            Check(ready.emplace(n.at("layout_node_id").get<uint64_t>(),n).second,"repeated ready node");
        J nodes=J::array();
        for (auto n : g.at("nodes")) {
            auto id=n.at("layout_node_id").get<uint64_t>();
            if (n.contains("transfer_id") && !originals.count(n.at("transfer_id").get<uint64_t>())) continue;
            if (n.contains("transfer_id") && IsLoad(originals.at(n.at("transfer_id").get<uint64_t>()))) {
                auto transfer=n.at("transfer_id"); n=ready.at(id); n["transfer_id"]=transfer;
                const auto& t=originals.at(transfer.get<uint64_t>());
                n["name"]=t.at("kind").get<std::string>()+"-"+t.at("message_role").get<std::string>()+
                    (n.at("kind")=="SEND" ? "-send" : "-recv");
            }
            n["node_id"]=id; n.erase("layout_node_id");
            for (auto& d : n["deps"]) {
                const auto& dependency=all.at(d.get<uint64_t>());
                Check(!dependency.contains("transfer_id") || originals.count(dependency.at("transfer_id").get<uint64_t>()),"supply dependency is not a completed node");
                d=dependency.at("layout_node_id");
            }
            nodes.push_back(n);
        }
        g["nodes"]=nodes;
        for (auto field : {"phase_nodes","local_prepare_nodes"}) if (g.contains(field))
            for (auto& v : g[field]) if (!v.is_null()) v=all.at(v.get<uint64_t>()).at("layout_node_id");
    }
    if (base.at("input").contains("resource_budget")) base["resource_usage"]=resources::Actual(base);
    ValidateLayerPlan(base,false); // Independently verifies original CANN layout, costs and dependencies.
    auto expected=Apply(std::move(base),p.at("input"),notices);
    Check(expected==p,"mapping, supply notification, direction or completion dependency mismatch");
    resources::Check(resources::Actual(p),resources::Budget(p.at("input")));
    LayerPlan result{retain?p:J{},{},{},{}};
    std::map<int,uint32_t> endpoints;
    for (size_t i=0;i<p.at("rank_ids").size();++i) endpoints[p.at("rank_ids").at(i)]=p.at("rank_to_ub_node").at(i);
    if (retain) for (const auto& g : p.at("rank_graphs")) for (const auto& n : g.at("nodes"))
        result.nodes.emplace(std::make_pair(g.at("rank_id").get<int>(),n.at("node_id").get<uint64_t>()),n);
    for (const auto& t : p.at("transfers")) {
        int src=t.at("src_rank"),dst=t.at("dst_rank");
        if (!t.contains("ub_task_id")) {
            result.local_descriptors.push_back({t.at("transfer_id"),p.at("graph_id"),t.at("send_node_id"),t.at("recv_node_id"),src,dst,t.at("comm_tag"),t.at("payload_bytes"),t.at("operation_id")});
            continue;
        }
        WriteDescriptor d{t.at("transfer_id"),t.at("ub_task_id"),p.at("graph_id"),t.at("send_node_id"),t.at("recv_node_id"),src,dst,t.at("comm_tag"),t.at("payload_bytes"),t.at("operation_id"),t.at("chunk_index"),t.at("pair_byte_range").at(0),t.at("pair_payload_bytes"),endpoints.at(src),endpoints.at(dst)};
        d.operation=IsLoad(t)?TransferOperation::Load:TransferOperation::Store;
        d.payload_regions.emplace();
        for (const auto& r : t.at("payload_regions")) d.payload_regions->push_back({r.at("begin"),r.at("end"),r.at("kind")});
        result.descriptors.push_back(d);
    }
    return result;
}
}
