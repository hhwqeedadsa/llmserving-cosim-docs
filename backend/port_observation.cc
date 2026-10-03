#include "port_observation.hh"

#include "ns3/ipv4-header.h"
#include "ns3/udp-header.h"
#include "ns3/ub-header.h"
#include "ns3/ub-tag.h"
#include "ns3/ub-switch.h"
#include "ns3/node-list.h"
#include "ns3/simulator.h"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <set>
#include <stdexcept>

namespace moe_ub
{
namespace
{
using Json = nlohmann::json;
void Require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}
uint64_t Unsigned(const Json& j, const char* key, uint64_t max)
{
    Require(j.at(key).is_number_unsigned() && j.at(key).get<uint64_t>() <= max,
            "Invalid observation unsigned field");
    return j.at(key).get<uint64_t>();
}
void Save(const std::string& path, const Json& value, bool lines = false)
{
    std::ofstream stream(path);
    if (lines) for (const auto& row : value) stream << row.dump() << '\n';
    else stream << value.dump(2) << '\n';
    stream.close();
    Require(bool(stream), "Cannot write observation output");
}
template<class Header> void Remove(ns3::Ptr<ns3::Packet> packet, Header& header)
{
    Require(packet->GetSize() >= header.GetSerializedSize(), "Truncated packet at observation point");
    packet->RemoveHeader(header);
}
void Created(std::shared_ptr<PayloadLedger> ledger, ns3::Ptr<const ns3::Packet> packet,
             uint32_t task, uint64_t task_begin, uint32_t packet_begin, uint32_t bytes)
{
    ledger->Created(packet, task, task_begin, packet_begin, bytes);
}
}

ObservationConfig ObservationConfig::Parse(const Json& input, uint64_t stop_ns)
{
    std::set<std::string> keys;
    for (auto it = input.begin(); it != input.end(); ++it) keys.insert(it.key());
    Require(keys == std::set<std::string>{"schema_version", "switch_id", "port_id",
                "window_start_ns", "window_end_ns", "window_period_ns", "record_provenance"} &&
            input.at("schema_version") == "moe-ub-port-observation/1" &&
            input.at("record_provenance").is_boolean(), "Unsupported observation schema or fields");
    ObservationConfig c{static_cast<uint32_t>(Unsigned(input, "switch_id", UINT32_MAX)),
        static_cast<uint32_t>(Unsigned(input, "port_id", UINT32_MAX)),
        Unsigned(input, "window_start_ns", stop_ns), Unsigned(input, "window_end_ns", stop_ns),
        Unsigned(input, "window_period_ns", stop_ns), input.at("record_provenance").get<bool>()};
    Require(c.start_ns < c.end_ns && c.period_ns > 0, "Invalid observation interval or period");
    Require((c.end_ns - c.start_ns - 1) / c.period_ns < 4096,
            "M1 observation supports at most 4096 windows");
    return c;
}

PortWindows::PortWindows(ObservationConfig config) : config_(config)
{
    Require(config.start_ns < config.end_ns && config.period_ns > 0 &&
            (config.end_ns - config.start_ns - 1) / config.period_ns < 4096,
            "Invalid port window bounds");
    rows_.resize((config.end_ns - config.start_ns - 1) / config.period_ns + 1);
}
void PortWindows::Add(uint64_t time_ns, uint32_t whole, uint32_t business)
{
    Require(business <= whole, "Payload exceeds packet size");
    if (time_ns < config_.start_ns || time_ns >= config_.end_ns) return;
    auto& row = rows_.at((time_ns - config_.start_ns) / config_.period_ns);
    ++row.packets;
    row.whole += whole;
    row.business += business;
    if (business) row.overhead += whole - business;
    else row.control += whole;
}

uint32_t WritePayloadBytes(ns3::Ptr<const ns3::Packet> packet)
{
    auto copy = packet->Copy();
    ns3::UbDatalinkHeader dl;
    Require(copy->GetSize() >= dl.GetSerializedSize(), "Missing datalink header");
    copy->PeekHeader(dl);
    if (dl.IsControlCreditHeader()) return 0;
    Require(dl.IsPacketIpv4Header(), "M1 observer only accepts RTP/IPv4 or credit frames");
    ns3::UbDatalinkPacketHeader packet_header;
    ns3::UbIpBasedNetworkHeader network;
    ns3::Ipv4Header ip;
    ns3::UdpHeader udp;
    ns3::UbTransportHeader tp;
    Remove(copy, packet_header); Remove(copy, network); Remove(copy, ip);
    Remove(copy, udp); Remove(copy, tp);
    if (ns3::UbTransportChannel::IsTransportResponseOpcode(tp.GetTPOpcode())) return 0;
    Require(tp.GetTPOpcode() == 1, "Unknown RTP packet opcode");
    ns3::UbTransactionHeader ta;
    Remove(copy, ta);
    if (ta.GetTaOpcode() == static_cast<uint8_t>(ns3::TaOpcode::TA_OPCODE_TRANSACTION_ACK)) return 0;
    Require(ta.GetTaOpcode() == static_cast<uint8_t>(ns3::TaOpcode::TA_OPCODE_WRITE),
            "M1 observer only accepts WRITE data or acknowledgments");
    ns3::UbMAExtTah ext;
    Remove(copy, ext);
    Require(copy->GetSize() > 0, "Empty WRITE payload");
    return copy->GetSize();
}

PayloadLedger::PayloadLedger(const std::vector<WriteDescriptor>& descriptors)
{
    Register(descriptors);
}
void PayloadLedger::Register(const std::vector<WriteDescriptor>& descriptors)
{
    auto checked = descriptors_;
    for (const auto& d : descriptors)
        Require(d.task_id <= UINT32_MAX && checked.emplace(d.task_id, d).second,
                "Duplicate or invalid UB task identity in provenance registry");
    descriptors_.swap(checked);
}

void PayloadLedger::Created(ns3::Ptr<const ns3::Packet> packet, uint32_t task,
                           uint64_t task_begin, uint32_t packet_begin, uint32_t bytes)
{
    auto descriptor = descriptors_.find(task);
    Require(descriptor != descriptors_.end(), "Unknown WRITE task provenance");
    const auto& d = descriptor->second;
    Require(bytes > 0 && task_begin <= d.bytes &&
            bytes <= d.bytes - task_begin && packet_begin <= packet->GetSize() &&
            bytes == packet->GetSize() - packet_begin, "Invalid WRITE provenance range or task");
    PayloadRange value{task_begin, packet_begin, bytes, packet->GetSize(), task};
    auto [it, inserted] = ranges_.emplace(packet->GetUid(), value);
    Require(inserted || it->second == value, "Conflicting packet provenance");
}
PayloadRange PayloadLedger::Lookup(ns3::Ptr<const ns3::Packet> packet, uint32_t payload_bytes) const
{
    auto it = ranges_.find(packet->GetUid());
    Require(it != ranges_.end(), "WRITE packet has no source provenance");
    const auto& range = it->second;
    ns3::UbFlowTag tag;
    const auto& d = descriptors_.at(range.task_id);
    Require(packet->PeekPacketTag(tag) && tag.GetFlowId() == d.task_id &&
            tag.GetFlowSize() == d.bytes &&
            range.packet_size == packet->GetSize() && range.bytes == payload_bytes,
            "WRITE packet does not match registered payload range");
    return range;
}

PortObservation::PortObservation(ObservationConfig config, WriteDescriptor descriptor,
                                ns3::Ptr<ns3::UbController> source)
    : PortObservation(config, std::vector<WriteDescriptor>{std::move(descriptor)}, {source})
{}

PortObservation::PortObservation(ObservationConfig config, const std::vector<WriteDescriptor>& descriptors,
                                std::vector<ns3::Ptr<ns3::UbController>> sources, bool retain_packet_events)
    : config_(config), windows_(config), sources_(std::move(sources)),
      retain_packet_events_(retain_packet_events)
{
    for (const auto& d : descriptors)
        Require(d.task_id <= UINT32_MAX && descriptors_.emplace(d.task_id, d).second,
                "Duplicate or invalid observation task identity");
    Require(config_.switch_id < ns3::NodeList::GetNNodes(), "Observation switch does not exist");
    auto node = ns3::NodeList::GetNode(config_.switch_id);
    auto sw = node->GetObject<ns3::UbSwitch>();
    Require(sw && sw->GetNodeType() == ns3::UB_SWITCH && config_.port_id < node->GetNDevices(),
            "Observation point must be an existing switch port");
    port_ = ns3::DynamicCast<ns3::UbPort>(node->GetDevice(config_.port_id));
    Require(port_ != nullptr, "Observation device is not a UB port");
    initial_tx_bytes_ = port_->GetTxBytes();
    if (config_.provenance)
    {
        ledger_ = std::make_shared<PayloadLedger>(descriptors);
        // Keep no TP references: native reclamation owns their lifetime. The bound
        // ledger, not this observer, remains valid if a removed TP outlives its map entry.
        for (const auto& source : sources_) for (const auto& [id, tp] : source->GetTpnMap())
            Require(tp->TraceConnectWithoutContext("WritePacketCreated",
                        ns3::MakeBoundCallback(Created, ledger_)), "Cannot observe WRITE packet creation");
    }
    Require(port_->TraceConnectWithoutContext("TraComEventNotify",
                ns3::MakeCallback(&PortObservation::PacketStart, this)), "Cannot observe port starts");
    Require(port_->GetUbQueue()->TraceConnectWithoutContext("UbEnqueue",
                ns3::MakeCallback(&PortObservation::Enqueued, this)) &&
            port_->GetUbQueue()->TraceConnectWithoutContext("UbDequeue",
                ns3::MakeCallback(&PortObservation::Dequeued, this)), "Cannot observe egress queue");
}
PortObservation::~PortObservation()
{
    if (port_)
    {
        port_->TraceDisconnectWithoutContext("TraComEventNotify",
            ns3::MakeCallback(&PortObservation::PacketStart, this));
        port_->GetUbQueue()->TraceDisconnectWithoutContext("UbEnqueue",
            ns3::MakeCallback(&PortObservation::Enqueued, this));
        port_->GetUbQueue()->TraceDisconnectWithoutContext("UbDequeue",
            ns3::MakeCallback(&PortObservation::Dequeued, this));
    }
    if (ledger_) for (const auto& source : sources_) for (const auto& [id, tp] : source->GetTpnMap())
        tp->TraceDisconnectWithoutContext("WritePacketCreated", ns3::MakeBoundCallback(Created, ledger_));
}
void PortObservation::Register(const std::vector<WriteDescriptor>& descriptors)
{
    auto checked = descriptors_;
    for (const auto& d : descriptors)
        Require(d.task_id <= UINT32_MAX && checked.emplace(d.task_id, d).second,
                "Duplicate or invalid observation task identity");
    if (ledger_) ledger_->Register(descriptors);
    descriptors_.swap(checked);
}
void PortObservation::QueueEvent(const char* event, ns3::Ptr<const ns3::Packet> packet, uint32_t queue_bytes)
{
    queue_events_.push_back({{"event", event}, {"time_ns", ns3::Simulator::Now().GetNanoSeconds()},
        {"packet_uid", packet->GetUid()}, {"packet_bytes", packet->GetSize()}, {"queue_bytes", queue_bytes}});
}
void PortObservation::Enqueued(ns3::Ptr<const ns3::Packet> packet, uint32_t queue_bytes)
{
    QueueEvent("enqueue", packet, queue_bytes);
}
void PortObservation::Dequeued(ns3::Ptr<const ns3::Packet> packet, uint32_t queue_bytes)
{
    QueueEvent("dequeue", packet, queue_bytes);
}
void PortObservation::PacketStart(ns3::Ptr<ns3::Packet> packet, ns3::Time completion_delay)
{
    // Despite the native trace name, this fires NOW after TransmitStart succeeds.
    // completion_delay is a duration; do not move the observation to packet end.
    const uint64_t now = ns3::Simulator::Now().GetNanoSeconds();
    QueueEvent("tx_start", packet, port_->GetUbQueue()->GetCurrentBytes());
    queue_events_.back()["busy_until_ns"] = now + completion_delay.GetNanoSeconds();
    const auto whole = packet->GetSize();
    const auto business = WritePayloadBytes(packet);
    if (retain_packet_events_)
        packet_events_.push_back({{"sim_time_ns", now}, {"packet_uid", packet->GetUid()},
            {"counted_packet_bytes", whole}, {"business_payload_bytes", business}});
    windows_.Add(now, whole, business);
    all_bytes_ += whole; ++all_packets_;
    // Validation fingerprint of actual serialized bytes AND packet start times.
    auto hash_byte = [this](uint8_t b) { fingerprint_ = (fingerprint_ ^ b) * 1099511628211ULL; };
    for (auto value : {now, static_cast<uint64_t>(whole)})
        for (unsigned i = 0; i < 8; ++i) hash_byte((value >> (8 * i)) & 255);
    std::vector<uint8_t> wire(whole);
    packet->CopyData(wire.data(), whole);
    for (auto b : wire) hash_byte(b);
    if (ledger_ && business)
    {
        const auto range = ledger_->Lookup(packet, business);
        const auto& descriptor = descriptors_.at(range.task_id);
        if (now >= config_.start_ns && now < config_.end_ns)
            packet_ranges_.push_back({{"sim_time_ns", now}, {"packet_uid", packet->GetUid()},
                {"ub_task_id", descriptor.task_id}, {"transfer_id", descriptor.transfer_id},
                {"operation_id", descriptor.operation_id.empty() ? descriptor.graph_id + "/write" : descriptor.operation_id},
                {"task_byte_range", {range.task_begin, range.task_begin + range.bytes}},
                {"packet_byte_range", {range.packet_begin, range.packet_begin + range.bytes}},
                {"counted_packet_bytes", whole}});
    }
}

void PortObservation::Export(const UbWriteTransfer& transfer) const
{
    const auto& d = transfer.Descriptor();
    const auto& completion = transfer.Completion();
    Require(transfer.SendStartNs() && transfer.ReceiveStartNs() &&
            completion.SendDoneNs() && completion.ReceiveDoneNs(), "Missing endpoint execution record");
    Json endpoints = Json::array({
        {{"rank_id", d.src_rank}, {"local_record_id", 1}, {"kind", "write_send"},
         {"start_local_ns", *transfer.SendStartNs()}, {"end_local_ns", *completion.SendDoneNs()}},
        {{"rank_id", d.dst_rank}, {"local_record_id", 1}, {"kind", "write_receive"},
         {"start_local_ns", *transfer.ReceiveStartNs()}, {"end_local_ns", *completion.ReceiveDoneNs()}}
    });
    Json operations = Json::array({{
        {"operation_id", d.graph_id + "/write"}, {"graph_id", d.graph_id}, {"op_type", "synthetic_write"},
        {"source", "functional_fixture"}, {"calibration_status", "unvalidated_reference"},
        {"endpoint_records", {{{"rank_id", d.src_rank}, {"local_record_id", 1}, {"et_node_id", d.send_node_id}},
                               {{"rank_id", d.dst_rank}, {"local_record_id", 1}, {"et_node_id", d.recv_node_id}}}}}});
    Export({&transfer}, endpoints, operations);
}

void PortObservation::Export(const std::vector<const UbWriteTransfer*>& transfers,
                             const Json& endpoint_ops, const Json& operation_registry,
                             const std::string& directory) const
{
    const std::filesystem::path base(directory);
    auto save = [&](const std::string& name, const Json& value, bool lines = false) {
        Save((base / name).string(), value, lines);
    };
    Require(port_->GetTxBytes() - initial_tx_bytes_ == all_bytes_, "Native port counter mismatch");
    Require(transfers.size() == descriptors_.size(), "Observation export transfer count mismatch");
    std::filesystem::create_directories(base / "observed");
    save("observed/public_config.json", {{"schema_version", "moe-ub-m1-observed/1"},
        {"counter_location", "egress_packet_start"}, {"byte_semantics", "packet_get_size"},
        {"switch_id", config_.switch_id}, {"port_id", config_.port_id}, {"direction", "egress"},
        {"observation_start_local_ns", config_.start_ns}, {"observation_end_local_ns", config_.end_ns},
        {"window_period_ns", config_.period_ns}, {"clock_resolution_ns", 1}});
    Json observed = Json::array(), truth = Json::array();
    for (size_t i = 0; i < windows_.Rows().size(); ++i)
    {
        const auto& row = windows_.Rows()[i];
        const auto start = config_.start_ns + i * config_.period_ns;
        const auto end = start + std::min(config_.period_ns, config_.end_ns - start);
        observed.push_back({{"switch_id", config_.switch_id}, {"port_id", config_.port_id},
            {"direction", "egress"}, {"local_window_start_ns", start}, {"local_window_end_ns", end},
            {"counted_packets", row.packets}, {"counted_packet_bytes", row.whole}});
        truth.push_back({{"window_start_sim_ns", start}, {"window_end_sim_ns", end},
            {"business_payload_bytes", row.business}, {"application_metadata_bytes", 0},
            {"data_protocol_overhead_bytes", row.overhead}, {"control_packet_bytes", row.control},
            {"unattributed_bytes", 0}});
    }
    save("observed/port_counters.jsonl", observed, true);
    save("observed/endpoint_ops.jsonl", endpoint_ops, true);
    save("observation-audit.json", {{"purpose", "test_only_internal_validation"},
        {"all_successful_start_packets", all_packets_}, {"all_successful_start_bytes", all_bytes_},
        {"native_tx_bytes_delta", port_->GetTxBytes() - initial_tx_bytes_},
        {"wire_and_time_fnv1a64", fingerprint_}});
    save("egress-queue-audit.jsonl", queue_events_, true);
    if (retain_packet_events_) save("packet-events.jsonl", packet_events_, true);
    if (!ledger_) return;
    std::filesystem::create_directory(base / "truth");
    std::set<int> ranks;
    Json registry = Json::array();
    for (const auto* transfer : transfers)
    {
        const auto& d = transfer->Descriptor();
        Require(!transfer->Completion().Active(), "Unfinished transfer in provenance export");
        ranks.insert(d.src_rank); ranks.insert(d.dst_rank);
        registry.push_back({{"transfer_id", d.transfer_id},
            {"operation_id", d.operation_id.empty() ? d.graph_id + "/write" : d.operation_id},
            {"ub_task_id", d.task_id}, {"src_rank", d.src_rank}, {"dst_rank", d.dst_rank},
            {"src_ub_node", d.SourceNode()}, {"dst_ub_node", d.DestinationNode()},
            {"payload_bytes", d.bytes}, {"business_payload_bytes", d.bytes}, {"application_metadata_bytes", 0},
            {"task_byte_range", {0, d.bytes}}, {"transport_profile", "URMA_WRITE/RTP"},
            {"priority", 7}, {"chunk_id", d.chunk_index}});
        if (!d.operation_id.empty())
        {
            registry.back()["pair_byte_range"] = {d.pair_byte_offset, d.pair_byte_offset + d.bytes};
            registry.back()["pair_payload_bytes"] = d.pair_payload_bytes;
        }
    }
    save("truth/clock_models.json", {{"schema_version", "moe-ub-m1-truth/1"},
        {"offset_ns", 0}, {"drift", 0}, {"rounding", "floor"},
        {"switch_ids", {config_.switch_id}}, {"rank_ids", ranks}});
    save("truth/operation_registry.jsonl", operation_registry, true);
    save("truth/transfer_registry.jsonl", registry, true);
    save("truth/packet_ranges.jsonl", packet_ranges_, true);
    save("truth/port_byte_truth.jsonl", truth, true);
}
}
