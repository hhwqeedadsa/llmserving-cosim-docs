#include "backend_config.hh"
#include "ns3/unified-bus-module.h"
#include "ns3/core-module.h"
#include <fstream>
#include <set>
#include <regex>
#include <cmath>

namespace moe_ub
{
namespace
{
using Json = nlohmann::json;
void Require(bool ok, const std::string& message)
{
    if (!ok) throw std::runtime_error("Backend configuration: " + message);
}
uint64_t Integer(const Json& x, uint64_t lo, uint64_t hi, const std::string& key)
{
    Require(x.is_number_unsigned() && x.get<uint64_t>() >= lo && x.get<uint64_t>() <= hi, "invalid " + key);
    return x.get<uint64_t>();
}
Json FixedAttributes()
{
    return {{"ns3::UbApp::TransportMode", "RTP"}, {"ns3::UbApp::EnableMultiPath", "false"},
        {"ns3::UbApp::UseShortestPaths", "true"}, {"ns3::UbApp::UsePacketSpray", "false"},
        {"ns3::UbTransportChannel::UsePacketSpray", "false"}, {"ns3::UbTransportChannel::UseShortestPaths", "true"},
        {"ns3::UbTransportChannel::EnableRetrans", "false"}, {"ns3::UbSwitch::FlowControl", "CBFC"},
        {"ns3::UbSwitch::VlScheduler", "SP"}, {"ns3::UbRoutingProcess::RoutingAlgorithm", "HASH"},
        {"ns3::TpConnectionManager::RemoveUselessTp", "false"}};
}
Json FixedGlobals()
{
    return {{"UB_CC_ENABLED", "false"}, {"UB_FAULT_ENABLE", "false"}, {"UB_PRIORITY_NUM", "16"}, {"UB_VL_NUM", "16"},
        {"UB_QUEUE_TRACE_ENABLE", "false"}, {"UB_PARSE_TRACE_ENABLE", "false"}};
}
ns3::TypeId::AttributeInformation Attribute(const std::string& key)
{
    auto at = key.rfind("::");
    Require(at != std::string::npos, "invalid attribute key");
    ns3::TypeId type;
    Require(ns3::TypeId::LookupByNameFailSafe(key.substr(0, at), &type), "unknown type " + key);
    ns3::TypeId::AttributeInformation info;
    Require(type.LookupAttributeByName(key.substr(at + 2), &info), "unknown attribute " + key);
    return info;
}
Json ObjectValues(ns3::Ptr<ns3::Object> object)
{
    Require(bool(object), "missing live object");
    Json result = Json::object();
    auto type = object->GetInstanceTypeId();
    while (true)
    {
        for (size_t i = 0; i < type.GetAttributeN(); ++i)
        {
            const auto info = type.GetAttribute(i);
            if (!(info.flags & ns3::TypeId::ATTR_GET)) continue;
            auto value = info.checker->Create();
            Require(object->GetAttributeFailSafe(info.name, *value), "unreadable live attribute " + info.name);
            result[type.GetName() + "::" + info.name] = value->SerializeToString(info.checker);
        }
        const auto parent = type.GetParent();
        if (parent == type) break;
        type = parent;
    }
    return result;
}
}
Json BackendCatalog()
{
    // Some globals are members of the native utility singleton and are only
    // registered when it exists. Do not invent defaults for these values.
    utils::UbUtils::Get();
    // This live queue type has no NS_OBJECT_ENSURE_REGISTERED in the pinned
    // backend; register its own TypeId without constructing a network object.
    ns3::UbEgressQueue::GetTypeId();
    Json result = {{"schema_version", "moe-ub-backend-catalog/1"}, {"ub_revision", COSIM_UB_REVISION},
        {"attributes", Json::object()}, {"globals", Json::object()},
        {"fixed_attributes", FixedAttributes()}, {"fixed_globals", FixedGlobals()},
        {"constants", {{"tp_max_data_payload_bytes", ns3::UB_MTU_BYTE}, {"time_resolution_ns", 1}, {"business_priority", 7}}}};
    for (uint16_t i = 0; i < ns3::TypeId::GetRegisteredN(); ++i)
    {
        const auto type = ns3::TypeId::GetRegistered(i);
        if (type.GetGroupName() != "UnifiedBus" && type.GetName().rfind("ns3::Ub", 0) != 0 &&
            type.GetName() != "ns3::TpConnectionManager") continue;
        for (size_t j = 0; j < type.GetAttributeN(); ++j)
        {
            const auto info = type.GetAttribute(j);
            if (!(info.flags & ns3::TypeId::ATTR_CONSTRUCT)) continue;
            result["attributes"][type.GetName() + "::" + info.name] = {
                {"default", info.initialValue->SerializeToString(info.checker)},
                {"value_type", info.checker->GetValueTypeName()}, {"help", info.help}};
        }
    }
    for (auto it = ns3::GlobalValue::Begin(); it != ns3::GlobalValue::End(); ++it)
    {
        auto* global = *it;
        if (global->GetName().rfind("UB_", 0) != 0) continue;
        auto value = global->GetChecker()->Create(); global->GetValue(*value);
        result["globals"][global->GetName()] = {{"default", value->SerializeToString(global->GetChecker())},
            {"value_type", global->GetChecker()->GetValueTypeName()}};
    }
    return result;
}
BackendConfig::BackendConfig(Json input) : input_(std::move(input))
{
    Require(input_.is_object() && input_.size() == 11 && input_.at("schema_version") == "moe-ub-backend-config/1", "schema/fields");
    Require(input_.at("scenario_id").is_string() && !input_.at("scenario_id").get<std::string>().empty(), "missing scenario identity");
    const auto catalog = BackendCatalog();
    Require(input_.at("catalog") == catalog, "catalog does not match this backend build");
    Require(input_.at("transport_channel_setup") == "precreated", "only precreated channels are verified");
    const Json profile = {{"programming_model", "URMA"}, {"operation", "WRITE"}, {"transport", "RTP"},
        {"initiator", "data_owner"}, {"payload_direction", "initiator_to_target"},
        {"receiver_ready", "RemoteWriteCommitted"}, {"sender_release", "transaction_done_callback"}};
    Require(input_.at("profiles").is_object() && input_.at("profiles").size() == 2, "require dispatch/combine profiles");
    for (const auto* role : {"dispatch", "combine"})
        Require(input_.at("profiles").at(role) == profile, "unimplemented active transaction profile for " + std::string(role));
    Require(input_.at("execution").size() == 4, "execution fields");
    Integer(input_["execution"]["chunk_bytes"], 1, UINT32_MAX, "chunk_bytes");
    Integer(input_["execution"]["send_slots_per_rank"], 1, 2, "send slots");
    Integer(input_["execution"]["seed"], 1, UINT32_MAX, "seed");
    Integer(input_["execution"]["run"], 1, INT64_MAX, "RNG run");
    Require(input_.at("tp_max_data_payload_bytes") == ns3::UB_MTU_BYTE, "TP payload is a compile-time constant");
    const auto& origins = input_.at("origins");
    Require(origins.is_object() && origins.size() == 2, "missing parameter origins");
    for (const auto* kind : {"attributes", "globals"})
    {
        auto& values = input_.at(kind);
        Require(values.is_object() && values.size() == catalog.at(kind).size(), "incomplete runtime parameter snapshot");
        Require(origins.at(kind).is_object() && origins.at(kind).size() == values.size(), "incomplete parameter origins");
        for (auto& [key, value] : values.items())
        {
            Require(catalog.at(kind).contains(key) && value.is_string(), "unknown or non-string parameter " + key);
            Require(origins.at(kind).at(key).is_array() && !origins.at(kind).at(key).empty(), "missing origin " + key);
            ns3::Ptr<const ns3::AttributeChecker> checker;
            if (std::string(kind) == "attributes") checker = Attribute(key).checker;
            else for (auto it = ns3::GlobalValue::Begin(); it != ns3::GlobalValue::End(); ++it)
                if ((*it)->GetName() == key) checker = (*it)->GetChecker();
            Require(bool(checker), "missing checker " + key);
            const auto type = checker->GetValueTypeName();
            const auto text = value.get<std::string>();
            if (type == "ns3::UintegerValue")
                Require(std::regex_match(text, std::regex("[0-9]+")), "invalid unsigned parameter " + key);
            if (type == "ns3::TimeValue")
            {
                std::smatch match;
                Require(std::regex_match(text, match, std::regex("([+]?[0-9]+(\\.[0-9]+)?([eE][+-]?[0-9]+)?)(s|ms|us|ns|ps|fs|min|h|d|y)")),
                        "time requires nonnegative number and explicit unit: " + key);
                const std::map<std::string, long double> scales = {{"s", 1e9L}, {"ms", 1e6L}, {"us", 1e3L},
                    {"ns", 1}, {"ps", 1e-3L}, {"fs", 1e-6L}, {"min", 6e10L}, {"h", 3.6e12L}, {"d", 8.64e13L}, {"y", 3.1536e16L}};
                const auto ns = std::stold(match[1].str()) * scales.at(match[4].str());
                Require(std::isfinite(ns) && ns < static_cast<long double>(INT64_MAX) && (ns == 0 || ns >= 1),
                        "time outside nanosecond execution range: " + key);
            }
            auto checked = checker->CreateValidValue(ns3::StringValue(value.get<std::string>()));
            Require(bool(checked), "invalid parameter " + key);
            if (type == "ns3::TimeValue")
                Require(ns3::DynamicCast<ns3::TimeValue>(checked)->Get() >= ns3::NanoSeconds(0), "negative time " + key);
            if (type == "ns3::DataRateValue")
                Require(ns3::DynamicCast<ns3::DataRateValue>(checked)->Get().GetBitRate() > 0, "zero rate " + key);
            if (type == "ns3::DoubleValue")
                Require(std::isfinite(ns3::DynamicCast<ns3::DoubleValue>(checked)->Get()), "non-finite parameter " + key);
            value = checked->SerializeToString(checker);
        }
    }
    for (const auto& [key, value] : catalog.at("fixed_attributes").items())
        Require(input_.at("attributes").at(key) == value, "unsupported active attribute " + key);
    for (const auto& [key, value] : catalog.at("fixed_globals").items())
        Require(input_.at("globals").at(key) == value, "unsupported active global " + key);
    // These backend checkers accept zero, but their execution paths require resources.
    for (const auto* key : {"ns3::UbJetty::TaSegmentBytes", "ns3::UbJetty::UbJettyInflightMax",
                           "ns3::UbTransportChannel::DefaultMaxWqeSegNum", "ns3::UbTransportChannel::DefaultMaxInflightPacketSize",
                           "ns3::UbPort::CbfcFlitLenByte", "ns3::UbPort::CbfcFlitsPerCell",
                           "ns3::UbPort::CbfcRetCellGrainDataPacket", "ns3::UbPort::CbfcRetCellGrainControlPacket",
                           "ns3::UbPort::CbfcInitCreditCell", "ns3::UbEgressQueue::MaxEgressBytes"})
    {
        const auto text = input_.at("attributes").at(key).get<std::string>();
        Require(text.front() != '-' && std::stoull(text) > 0, "nonpositive active resource " + std::string(key));
    }
    Require(input_.at("topology").is_object() && input_.at("topology").size() == 2, "topology fields");
}
std::optional<BackendConfig> BackendConfig::Load(const std::filesystem::path& network)
{
    const auto path = network / "backend.json";
    if (!std::filesystem::exists(path)) return std::nullopt;
    std::ifstream file(path);
    Require(bool(file), "cannot read backend.json");
    return BackendConfig(Json::parse(file));
}
void BackendConfig::Apply() const
{
    for (const auto& [key, value] : input_.at("attributes").items())
        Require(ns3::Config::SetDefaultFailSafe(key, ns3::StringValue(value.get<std::string>())), "cannot set " + key);
    for (const auto& [key, value] : input_.at("globals").items())
        Require(ns3::GlobalValue::BindFailSafe(key, ns3::StringValue(value.get<std::string>())), "cannot bind " + key);
    ns3::RngSeedManager::SetSeed(input_.at("execution").at("seed"));
    ns3::RngSeedManager::SetRun(input_.at("execution").at("run"));
}
void BackendConfig::CheckPlan(const Json& plan) const
{
    Require(plan.at("communication_template") == "direct_rank_dedup_v1" ||
            plan.at("communication_template") == "cann_a3_v850_fullmesh_v1", "scenario requires a supported WRITE/RTP template");
    if (plan.contains("layers"))
    {
        Require(plan.at("schema_version") == "moe-ub-multilayer-plan/2", "scenario requires configured layer segment");
        for (const auto& layer : plan.at("layers")) CheckPlan(layer);
    }
    for (const auto* key : {"chunk_bytes", "send_slots_per_rank"})
        Require(plan.at("input").at(key) == input_.at("execution").at(key), "plan differs from effective execution " + std::string(key));
    Require(plan.at("sender_completion") == "transaction_done", "invalid sender completion");
}
void BackendConfig::WriteEffective(const std::vector<ns3::Ptr<ns3::UbController>>& controllers,
                                   const std::vector<ns3::Ptr<ns3::UbApp>>& apps) const
{
    Json result = input_;
    result["schema_version"] = "moe-ub-effective-backend/1";
    result["objects"] = Json::array();
    auto append = [&](const std::string& role, uint32_t node, ns3::Ptr<ns3::Object> object) {
        auto values = ObjectValues(object);
        for (const auto& [key, value] : values.items())
            if (input_.at("attributes").contains(key))
            {
                // These are overwritten on live objects by the topology CSV.
                if (key == "ns3::UbPort::UbDataRate" || key == "ns3::UbSwitch::InPortProcessingDelay" ||
                    key == "ns3::UbSwitchAllocator::AllocationTime" || key == "ns3::UbLink::Delay") continue;
                Require(value == input_.at("attributes").at(key), "live object differs: " + role + " " + key);
            }
        result["objects"].push_back({{"role", role}, {"node_id", node}, {"attributes", values}});
    };
    Json nodes = Json::array(), links = Json::array();
    for (auto it = ns3::NodeList::Begin(); it != ns3::NodeList::End(); ++it)
    {
        auto node = *it; auto sw = node->GetObject<ns3::UbSwitch>();
        auto attributes = ObjectValues(sw), allocator = ObjectValues(sw->GetAllocator());
        const auto forwarding = ns3::Time(attributes.at("ns3::UbSwitch::InPortProcessingDelay").get<std::string>()).GetNanoSeconds();
        const auto allocation = ns3::Time(allocator.at("ns3::UbSwitchAllocator::AllocationTime").get<std::string>()).GetNanoSeconds();
        nodes.push_back({{"node_id", node->GetId()}, {"ports", node->GetNDevices()},
            {"kind", node->GetObject<ns3::UbController>() ? "DEVICE" : "SWITCH"},
            {"allocation_ns", allocation}, {"forward_ns", forwarding}});
        append("switch", node->GetId(), sw); append("allocator", node->GetId(), sw->GetAllocator());
        append("routing", node->GetId(), sw->GetRoutingProcess()); append("queue_manager", node->GetId(), sw->GetQueueManager());
        for (uint32_t i = 0; i < node->GetNDevices(); ++i)
        {
            auto port = ns3::DynamicCast<ns3::UbPort>(node->GetDevice(i));
            append("port:" + std::to_string(i), node->GetId(), port);
            append("egress:" + std::to_string(i), node->GetId(), port->GetUbQueue());
            auto channel = ns3::DynamicCast<ns3::UbLink>(port->GetChannel());
            Require(bool(channel), "expected a connected UB link");
            auto peer = channel->GetDestination(port);
            if (node->GetId() < peer->GetNode()->GetId())
                links.push_back({{"src", node->GetId()}, {"src_port", i}, {"dst", peer->GetNode()->GetId()},
                    {"dst_port", peer->GetIfIndex()}, {"rate_bps", port->GetDataRate().GetBitRate()},
                    {"delay_ns", channel->GetDelay().GetNanoSeconds()}});
            Require(port->GetDataRate().GetBitRate() == peer->GetDataRate().GetBitRate(), "asymmetric rate");
        }
    }
    Require(nodes == input_.at("topology").at("nodes") && links == input_.at("topology").at("links"), "live topology differs from resolved CSV overrides");
    result["topology"] = {{"nodes", nodes}, {"links", links}};
    for (const auto& app : apps) append("application", app->GetNode()->GetId(), app);
    for (const auto& controller : controllers)
    {
        append("connection_manager", controller->GetObject<ns3::Node>()->GetId(), controller->GetTpConnManager());
        for (const auto& [id, tp] : controller->GetTpnMap())
            append("transport:" + std::to_string(id), controller->GetObject<ns3::Node>()->GetId(), tp);
    }
    result["random"] = {{"seed", ns3::RngSeedManager::GetSeed()}, {"run", ns3::RngSeedManager::GetRun()}};
    result["effective_globals"] = Json::object();
    for (auto it = ns3::GlobalValue::Begin(); it != ns3::GlobalValue::End(); ++it)
    {
        const auto* global = *it;
        if (!input_.at("globals").contains(global->GetName())) continue;
        auto value = global->GetChecker()->Create(); global->GetValue(*value);
        const auto actual = value->SerializeToString(global->GetChecker());
        Require(actual == input_.at("globals").at(global->GetName()), "live global differs " + global->GetName());
        result["effective_globals"][global->GetName()] = actual;
    }
    Require(result["effective_globals"] == input_.at("globals"), "missing live global");
    result["object_validation"] = "matched_before_business_activation";
    std::ofstream output("effective_backend.json");
    output << result.dump(2) << '\n';
    Require(bool(output), "cannot write effective backend snapshot");
}
}
