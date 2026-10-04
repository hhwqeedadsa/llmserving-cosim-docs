#include "fabric_config.hh"
#include "ns3/unified-bus-module.h"
#include "ns3/core-module.h"
#include <set>
#include <map>
#include <algorithm>

namespace moe_ub
{
namespace
{
using Json = nlohmann::json;
void Check(bool ok, const std::string& message)
{
    if (!ok) throw std::runtime_error("Fabric: " + message);
}
void Fields(const Json& value, std::initializer_list<const char*> names)
{
    Check(value.is_object() && value.size() == names.size(), "invalid fields");
    for (const auto* key : names) Check(value.contains(key), std::string("missing ") + key);
}
uint32_t UInt(const Json& value, uint32_t lo, uint32_t hi)
{
    Check(value.is_number_unsigned() && value.get<uint64_t>() >= lo && value.get<uint64_t>() <= hi, "invalid integer");
    return value.get<uint32_t>();
}
struct Shape
{
    uint32_t servers, chips, dies, planes, spines, perServer, devices, l1, nodes;
    std::map<uint32_t, std::set<uint32_t>> peers;
    explicit Shape(const Json& fabric)
    {
        Fields(fabric, {"schema_version", "shape", "groups", "budget"});
        Check(fabric.at("schema_version") == "moe-ub-fabric/1", "schema");
        const auto& s = fabric.at("shape");
        Fields(s, {"servers", "chips_per_server", "dies_per_chip", "planes", "l2_per_plane"});
        servers = UInt(s.at("servers"), 1, 128); chips = UInt(s.at("chips_per_server"), 1, 128);
        dies = UInt(s.at("dies_per_chip"), 1, 128); planes = UInt(s.at("planes"), 1, 128);
        spines = UInt(s.at("l2_per_plane"), 1, 128); perServer = chips * dies;
        devices = servers * perServer; l1 = servers * planes; nodes = devices + l1 + planes * spines;
        Check(perServer + spines <= 128 && nodes <= 4096, "node/port limit");
        const auto& budget = fabric.at("budget");
        Fields(budget, {"max_nodes", "max_links", "max_route_rows", "max_channels"});
        for (const auto& v : budget) UInt(v, 1, UINT32_MAX);
        Check(nodes <= budget.at("max_nodes").get<uint32_t>() &&
              planes * (devices + servers * spines) <= budget.at("max_links").get<uint32_t>(), "topology budget");
        const auto& groups = fabric.at("groups");
        Check(groups.is_array() && !groups.empty(), "groups");
        std::set<uint32_t> ranks, endpoints; std::set<std::string> names;
        uint64_t channels = 0, rows = l1 * (perServer + servers - 1) + planes * spines * servers;
        for (uint32_t n = 0; n < devices; ++n)
            rows += planes * ((n / perServer > 0) + (n % perServer > 0) +
                              (n % perServer + 1 < perServer) + (n / perServer + 1 < servers));
        for (const auto& g : groups)
        {
            Fields(g, {"group_id", "rank_ids", "rank_to_ub_node"});
            Check(g.at("group_id").is_string() && !g.at("group_id").get<std::string>().empty() &&
                  names.insert(g.at("group_id").get<std::string>()).second, "group identity");
            const auto& rs = g.at("rank_ids"); const auto& ns = g.at("rank_to_ub_node");
            Check(rs.is_array() && ns.is_array() && !rs.empty() && rs.size() == ns.size(), "mapping");
            for (size_t i = 0; i < rs.size(); ++i)
            {
                const auto r = UInt(rs[i], 0, UINT32_MAX), n = UInt(ns[i], 0, devices - 1);
                Check(ranks.insert(r).second && endpoints.insert(n).second, "duplicate rank/device");
            }
            for (const auto& a : ns)
                for (const auto& b : ns)
                    if (a.get<uint32_t>() / dies != b.get<uint32_t>() / dies) peers[a.get<uint32_t>()].insert(b.get<uint32_t>());
        }
        for (const auto& [n, members] : peers) channels += members.size() * planes;
        Check(channels / 2 <= budget.at("max_channels").get<uint32_t>() &&
              rows <= budget.at("max_route_rows").get<uint32_t>(), "routing/channel budget");
    }
    uint32_t L1(uint32_t server, uint32_t plane) const { return devices + server * planes + plane; }
    uint32_t L2(uint32_t plane, uint32_t spine) const { return devices + l1 + plane * spines + spine; }
};
}
void ValidateFabric(const Json& fabric, const Json& topology)
{
    const Shape s(fabric);
    const auto& nodes = topology.at("nodes"); const auto& links = topology.at("links");
    Check(nodes.is_array() && nodes.size() == s.nodes && links.is_array() &&
          links.size() == s.planes * (s.devices + s.servers * s.spines), "topology size");
    for (uint32_t n = 0; n < s.nodes; ++n)
    {
        const auto ports = n < s.devices ? s.planes : n < s.devices + s.l1 ? s.perServer + s.spines : s.servers;
        Check(nodes[n].at("node_id") == n && nodes[n].at("ports") == ports &&
              nodes[n].at("kind") == (n < s.devices ? "DEVICE" : "SWITCH"), "physical node differs");
    }
    std::set<std::pair<uint32_t, uint32_t>> seen;
    for (const auto& link : links)
    {
        const auto a = UInt(link.at("src"), 0, s.devices + s.l1 - 1);
        const auto ap = UInt(link.at("src_port"), 0, 127);
        const auto b = UInt(link.at("dst"), 0, s.nodes - 1), bp = UInt(link.at("dst_port"), 0, 127);
        if (a < s.devices)
            Check(ap < s.planes && b == s.L1(a / s.perServer, ap) && bp == a % s.perServer, "device/L1 link differs");
        else
        {
            const auto server = (a - s.devices) / s.planes, plane = (a - s.devices) % s.planes;
            Check(ap >= s.perServer && ap < s.perServer + s.spines &&
                  b == s.L2(plane, ap - s.perServer) && bp == server, "L1/L2 link differs");
        }
        Check(seen.insert({a, ap}).second && seen.insert({b, bp}).second, "duplicate physical port");
    }
}
void CheckFabricPlan(const Json& fabric, const Json& plan)
{
    const Shape s(fabric); const auto& input = plan.at("input");
    const Json* found = nullptr;
    for (const auto& g : fabric.at("groups")) if (g.at("group_id") == input.at("group_id")) found = &g;
    Check(found != nullptr, "unknown plan group");
    Check(input.at("rank_ids") == found->at("rank_ids") &&
          input.at("rank_to_ub_node") == found->at("rank_to_ub_node"), "plan mapping differs");
    Json placement = Json::array();
    for (size_t i = 0; i < found->at("rank_ids").size(); ++i)
    {
        const auto n = found->at("rank_to_ub_node")[i].get<uint32_t>();
        placement.push_back({{"rank_id", found->at("rank_ids")[i]}, {"die_id", "die-" + std::to_string(n)},
            {"chip_id", "chip-" + std::to_string(n / s.dies)}, {"server_id", "server-" + std::to_string(n / s.perServer)}});
    }
    Check(input.at("deployment").at("ranks") == placement, "plan physical placement differs");
}
Json AuditLiveFabric(const Json& fabric)
{
    Shape s(fabric); uint64_t queries = 0, tps = 0;
    auto route = [&](uint32_t node, uint32_t dst, uint32_t plane, const std::vector<uint16_t>& expected) {
        auto routing = ns3::NodeList::GetNode(node)->GetObject<ns3::UbSwitch>()->GetRoutingProcess();
        std::vector<uint16_t> actual;
        routing->GetShortestOutPorts(utils::NodeIdToIp(dst, plane).Get(), actual);
        std::sort(actual.begin(), actual.end());
        Check(actual == expected, "live shortest candidates differ at node " + std::to_string(node) +
              " dst " + std::to_string(dst) + " plane " + std::to_string(plane));
        ++queries;
    };
    Json bindings = Json::array();
    for (uint32_t n = 0; n < s.devices; ++n)
    {
        for (uint32_t dst = 0; dst < s.devices; ++dst)
            if (dst != n) for (uint16_t plane = 0; plane < s.planes; ++plane) route(n, dst, plane, {plane});
        auto controller = ns3::NodeList::GetNode(n)->GetObject<ns3::UbController>();
        auto manager = controller->GetTpConnManager(); const auto& transports = controller->GetTpnMap();
        Check(manager->GetNodeConnections(n).size() == s.peers[n].size() * s.planes &&
              transports.size() == s.peers[n].size() * s.planes, "live TP count differs at device " + std::to_string(n));
        std::set<std::pair<uint32_t, uint32_t>> seen;
        for (const auto& [id, tp] : transports)
        {
            utils::Connection c;
            Check(manager->TryGetLocalConnection(n, id, c), "missing TP connection");
            Check(c.node1 == n && s.peers[n].count(c.node2) && c.port1 < s.planes && c.port2 == c.port1 &&
                  c.priority == 7 && c.metrics == (n / s.perServer == c.node2 / s.perServer ? 2u : 4u) &&
                  seen.insert({c.node2, c.port1}).second, "live TP endpoints/plane/metric differ");
            Check(tp->GetSrc() == n && tp->GetDest() == c.node2 && tp->GetSport() == c.port1 &&
                  tp->GetDport() == c.port2 && tp->GetDstTpn() == c.tpn2 && tp->GetPriority() == 7,
                  "live transport differs from connection");
            auto peer = ns3::NodeList::GetNode(c.node2)->GetObject<ns3::UbController>();
            const auto& remote = peer->GetTpnMap();
            Check(remote.count(c.tpn2) && remote.at(c.tpn2)->GetDest() == n &&
                  remote.at(c.tpn2)->GetDstTpn() == id, "reciprocal TP differs");
            bindings.push_back({{"node", n}, {"tpn", id}, {"peer", c.node2}, {"peer_tpn", c.tpn2}, {"plane", c.port1}});
            ++tps;
        }
    }
    std::vector<uint16_t> uplinks;
    for (uint16_t i = 0; i < s.spines; ++i) uplinks.push_back(s.perServer + i);
    for (uint32_t plane = 0; plane < s.planes; ++plane)
    {
        for (uint32_t server = 0; server < s.servers; ++server)
            for (uint32_t dst = 0; dst < s.devices; ++dst)
                route(s.L1(server, plane), dst, plane, dst / s.perServer == server ?
                      std::vector<uint16_t>{static_cast<uint16_t>(dst % s.perServer)} : uplinks);
        for (uint32_t spine = 0; spine < s.spines; ++spine)
            for (uint32_t dst = 0; dst < s.devices; ++dst)
                route(s.L2(plane, spine), dst, plane, {static_cast<uint16_t>(dst / s.perServer)});
    }
    return {{"status", "matched_before_business_activation"}, {"route_queries", queries},
            {"directed_transports", tps}, {"bindings", bindings}, {"inactive_devices_checked", true}};
}
}
