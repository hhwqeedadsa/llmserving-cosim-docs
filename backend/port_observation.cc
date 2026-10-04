#include "port_observation.hh"
#include "result_records.hh"
#include "observation_resources.hh"

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
    const bool streamed=input.at("schema_version")=="moe-ub-port-observation/2";
    if (streamed) { ValidateObservationBudget(input.at("resource_budget")); keys.erase("resource_budget"); }
    Require(keys == std::set<std::string>{"schema_version", "switch_id", "port_id",
                "window_start_ns", "window_end_ns", "window_period_ns", "record_provenance"} &&
            (streamed || input.at("schema_version") == "moe-ub-port-observation/1") &&
            input.at("record_provenance").is_boolean(), "Unsupported observation schema or fields");
    ObservationConfig c{static_cast<uint32_t>(Unsigned(input, "switch_id", UINT32_MAX)),
        static_cast<uint32_t>(Unsigned(input, "port_id", UINT32_MAX)),
        Unsigned(input, "window_start_ns", stop_ns), Unsigned(input, "window_end_ns", stop_ns),
        Unsigned(input, "window_period_ns", stop_ns), input.at("record_provenance").get<bool>()};
    if (streamed) c.resource_budget=input.at("resource_budget");
    Require(c.start_ns < c.end_ns && c.period_ns > 0, "Invalid observation interval or period");
    Require((c.end_ns - c.start_ns - 1) / c.period_ns < (streamed ? c.resource_budget.at("max_windows_per_port").get<uint64_t>() : 4096),
            "Observation windows exceed configured budget (legacy: 4096)");
    return c;
}

PortWindows::PortWindows(ObservationConfig config) : config_(config)
{
    Require(config.start_ns < config.end_ns && config.period_ns > 0 &&
            (config.end_ns - config.start_ns - 1) / config.period_ns < (config.resource_budget.is_null() ? 4096 : config.resource_budget.at("max_windows_per_port").get<uint64_t>()),
            "Invalid port window bounds");
    if (config.resource_budget.is_null()) rows_.resize(Count());
}
ByteCounts PortWindows::Row(uint64_t index) const
{
    Require(!sink_, "A streamed window must be read from its record file");
    Require(index<Count(), "Window index outside interval");
    if (config_.resource_budget.is_null()) return rows_.at(index);
    const auto found=sparse_.find(index);return found==sparse_.end() ? ByteCounts{} : found->second;
}
void PortWindows::Stream(std::function<void(uint64_t, const ByteCounts&)> sink)
{
    Require(bool(sink) && !sink_ && sparse_.empty() &&
            std::all_of(rows_.begin(), rows_.end(), [](const auto& row) { return row.packets == 0; }),
            "Window streaming must begin before packet observation");
    rows_.clear(); rows_.shrink_to_fit(); sink_ = std::move(sink);
}
void PortWindows::Finish()
{
    if (sink_ && current_) { sink_(*current_, current_row_); current_.reset(); current_row_ = {}; }
    if (sink_) stream_finished_ = true;
}
void PortWindows::Add(uint64_t time_ns, uint32_t whole, uint32_t business, uint32_t metadata, uint32_t padding)
{
    const uint64_t payload = uint64_t(business) + metadata + padding;
    Require(payload <= whole, "Payload exceeds packet size");
    if (time_ns < config_.start_ns || time_ns >= config_.end_ns) return;
    const auto index=(time_ns-config_.start_ns)/config_.period_ns;
    if (sink_)
    {
        Require(!stream_finished_, "Window event after final export");
        Require(!current_ || index >= *current_, "Out-of-order port window event");
        if (current_ && index != *current_) { sink_(*current_, current_row_); current_row_ = {}; }
        current_ = index;
    }
    auto& row = sink_ ? current_row_ : config_.resource_budget.is_null() ? rows_.at(index) : sparse_[index];
    ++row.packets;
    row.whole += whole;
    row.business += business;
    row.metadata += metadata; row.padding += padding;
    if (payload) row.overhead += whole - payload;
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
    Require(descriptors_.size()<=max_transfers_ && descriptors.size()<=max_transfers_-descriptors_.size(),
            "Observation registered transfer budget exceeded");
    // Validate and allocate only the incoming batch. Merge map nodes afterwards,
    // preserving atomic rejection without copying all preceding graphs.
    std::map<uint32_t, WriteDescriptor> checked;
    bool segmented = segmented_payload_;
    for (const auto& d : descriptors)
    {
        Require(d.task_id <= UINT32_MAX && !registered_tasks_.count(d.task_id) && checked.emplace(d.task_id, d).second,
                "Duplicate or invalid UB task identity in provenance registry");
        segmented |= d.payload_regions.has_value();
    }
    descriptors_.merge(checked);
    for (const auto& d : descriptors) registered_tasks_.insert(d.task_id);
    peak_transfers_ = std::max(peak_transfers_, uint64_t(descriptors_.size()));
    segmented_payload_ = segmented;
}
void PayloadLedger::RetireCompletedGraphs(const std::set<std::string>& graphs)
{
    IdentityRanges retired;
    for (auto it = descriptors_.begin(); it != descriptors_.end();)
        if (graphs.count(it->second.graph_id))
        { retired.insert(it->first); it = descriptors_.erase(it); ++retired_transfers_; }
        else ++it;
    for (auto it = ranges_.begin(); it != ranges_.end();)
        if (retired.count(it->second.task_id)) { it = ranges_.erase(it); ++retired_packets_; }
        else ++it;
}
Json PayloadLedger::RetirementAudit() const
{
    Require(registered_tasks_.size() == descriptors_.size() + retired_transfers_, "provenance retirement accounting mismatch");
    return {{"schema_version", "moe-ub-provenance-retirement/1"},
        {"registered_transfers_total", registered_tasks_.size()}, {"retired_transfers", retired_transfers_},
        {"retired_packets", retired_packets_}, {"resident_transfers", descriptors_.size()},
        {"resident_packets", ranges_.size()}, {"peak_resident_transfers", peak_transfers_},
        {"peak_resident_packets", peak_packets_}, {"identity_intervals", registered_tasks_.Intervals()},
        {"retirement_boundary", "completed_graph_and_global_physical_network_idle"}};
}
void PayloadLedger::SetLimits(uint64_t packets, uint64_t transfers)
{
    Require(ranges_.size()<=packets && descriptors_.size()<=transfers,
            "Observation provenance already exceeds requested budget");
    max_packets_=packets; max_transfers_=transfers;
}
void PayloadLedger::RestrictLimits(uint64_t packets, uint64_t transfers)
{
    SetLimits(std::min(packets,max_packets_),std::min(transfers,max_transfers_));
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
    Require(ranges_.contains(packet->GetUid()) || ranges_.size()<max_packets_, "Observation payload ledger packet budget exceeded");
    auto [it, inserted] = ranges_.emplace(packet->GetUid(), value);
    Require(inserted || it->second == value, "Conflicting packet provenance");
    peak_packets_ = std::max(peak_packets_, uint64_t(ranges_.size()));
    ++created_notifications_;
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

WriteProvenance::WriteProvenance(std::vector<ns3::Ptr<ns3::UbController>> sources,
                               const std::vector<WriteDescriptor>& descriptors)
    : ledger_(std::make_shared<PayloadLedger>(descriptors)), sources_(std::move(sources))
{
    // Reject duplicate controllers before attaching any callbacks.
    std::set<const ns3::UbController*> unique;
    for (const auto& source : sources_)
        Require(source && unique.insert(ns3::PeekPointer(source)).second, "Invalid or duplicate provenance source");
    for (const auto& source : sources_) for (const auto& [id, tp] : source->GetTpnMap())
    {
        Require(tp->TraceConnectWithoutContext("WritePacketCreated",
                    ns3::MakeBoundCallback(Created, ledger_)), "Cannot observe WRITE packet creation");
        ++source_subscriptions_;
    }
}
WriteProvenance::~WriteProvenance()
{
    // Native TP reclamation owns its lifetime. A removed TP may outlive its map
    // entry; its callback safely retains just the ledger until it is destroyed.
    for (const auto& source : sources_) for (const auto& [id, tp] : source->GetTpnMap())
        tp->TraceDisconnectWithoutContext("WritePacketCreated", ns3::MakeBoundCallback(Created, ledger_));
}
Json WriteProvenance::Audit() const
{
    return {{"storage", "shared_run_ledger"}, {"descriptor_tables", 1}, {"packet_tables", 1},
        {"descriptor_records", ledger_->TransferCount()}, {"packet_records", ledger_->PacketCount()},
        {"source_subscriptions", source_subscriptions_}, {"created_notifications", ledger_->CreatedNotifications()}};
}

PortObservation::PortObservation(ObservationConfig config, WriteDescriptor descriptor,
                                ns3::Ptr<ns3::UbController> source)
    : PortObservation(config, std::vector<WriteDescriptor>{std::move(descriptor)}, {source})
{}

PortObservation::PortObservation(ObservationConfig config, const std::vector<WriteDescriptor>& descriptors,
                                std::vector<ns3::Ptr<ns3::UbController>> sources, bool retain_packet_events,
                                const std::filesystem::path& spool_directory)
    : PortObservation(config, std::make_shared<WriteProvenance>(std::move(sources), descriptors),
                      retain_packet_events, spool_directory)
{}

PortObservation::PortObservation(ObservationConfig config, std::shared_ptr<WriteProvenance> provenance,
                                bool retain_packet_events, const std::filesystem::path& spool_directory,
                                std::shared_ptr<RecordStore> record_store)
    : config_(config), windows_(config), provenance_(std::move(provenance)),
      retain_packet_events_(retain_packet_events), record_store_(std::move(record_store))
{
    Require(provenance_ != nullptr, "Observation needs a provenance ledger");
    if (!config_.resource_budget.is_null())
    {
        ValidateObservationBudget(config_.resource_budget);
        // All ports see the same logical records. Respect the strictest of their
        // declared limits, including ports with source attribution disabled.
        provenance_->RestrictLimits(config_.resource_budget.at("max_ledger_packets"),
                                   config_.resource_budget.at("max_registered_transfers"));
        Require(!spool_directory.empty(), "Streaming observation needs its output directory");
        spool_directory_=spool_directory;std::filesystem::create_directories(spool_directory_/"observed");
        if(config_.provenance) std::filesystem::create_directories(spool_directory_/"truth");
        for(const std::string name : {"packet-events.jsonl","egress-queue-audit.jsonl","truth/packet_ranges.jsonl"})
        {
            if(name=="truth/packet_ranges.jsonl" && !config_.provenance) continue;
            if (!record_store_) { streams_[name].open(spool_directory_/name);Require(bool(streams_.at(name)),"Cannot open streaming observation file"); }
        }
    }
    if (record_store_) windows_.Stream([this](uint64_t index, const auto& row) { WindowRecord(index, row); });
    Require(config_.switch_id < ns3::NodeList::GetNNodes(), "Observation switch does not exist");
    auto node = ns3::NodeList::GetNode(config_.switch_id);
    auto sw = node->GetObject<ns3::UbSwitch>();
    Require(sw && sw->GetNodeType() == ns3::UB_SWITCH && config_.port_id < node->GetNDevices(),
            "Observation point must be an existing switch port");
    port_ = ns3::DynamicCast<ns3::UbPort>(node->GetDevice(config_.port_id));
    Require(port_ != nullptr, "Observation device is not a UB port");
    initial_tx_bytes_ = port_->GetTxBytes();
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
}
void PortObservation::Record(const std::string& name, Json& memory, const Json& row)
{
    if (config_.resource_budget.is_null() && !record_store_) { memory.push_back(row);return; }
    const auto text=row.dump()+"\n";
    if (!config_.resource_budget.is_null())
        Require(text.size()<=config_.resource_budget.at("max_event_bytes_per_port").get<uint64_t>()-event_bytes_, "Observation event byte budget exceeded");
    if (record_store_)
    {
        record_store_->Append(RecordName(name), std::string_view(text).substr(0, text.size()-1));
        event_bytes_ += text.size(); return;
    }
    auto& stream=streams_.at(name);stream<<text;
    Require(bool(stream),"Cannot write observation event");event_bytes_+=text.size();
}
std::string PortObservation::RecordName(std::string name) const
{
    std::replace(name.begin(), name.end(), '/', '-');
    return "port-" + std::to_string(config_.switch_id) + "-" + std::to_string(config_.port_id) + "-" + name + ".gz";
}
void PortObservation::WindowRecord(uint64_t index, const ByteCounts& row)
{
    // Only nonzero windows occupy storage. The finite observation interval and
    // period in the manifest encode every intervening zero window losslessly.
    Require(row.packets > 0, "Cannot store an empty active window");
    Json observed = {{"index", index}, {"counted_packets", row.packets}, {"counted_packet_bytes", row.whole}};
    record_store_->Append(RecordName("port-windows.jsonl"), observed.dump());
    if (config_.provenance)
        record_store_->Append(RecordName("truth-windows.jsonl"), Json({{"index", index},
            {"business_payload_bytes", row.business}, {"application_metadata_bytes", row.metadata},
            {"padding_bytes", row.padding}, {"data_protocol_overhead_bytes", row.overhead},
            {"control_packet_bytes", row.control}, {"unattributed_bytes", 0}}).dump());
}
void PortObservation::QueueEvent(const char* event, ns3::Ptr<const ns3::Packet> packet, uint32_t queue_bytes,
                                 std::optional<uint64_t> busy_until)
{
    Json row={{"event", event}, {"time_ns", ns3::Simulator::Now().GetNanoSeconds()},
        {"packet_uid", packet->GetUid()}, {"packet_bytes", packet->GetSize()}, {"queue_bytes", queue_bytes}};
    if(busy_until) row["busy_until_ns"]=*busy_until;
    Record("egress-queue-audit.jsonl",queue_events_,row);
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
    QueueEvent("tx_start", packet, port_->GetUbQueue()->GetCurrentBytes(), now + completion_delay.GetNanoSeconds());
    const auto whole = packet->GetSize();
    const auto payload = WritePayloadBytes(packet);
    uint32_t business = payload, metadata = 0, padding = 0;
    std::optional<PayloadRange> packet_range;
    if (payload)
    {
        packet_range = provenance_->Ledger().Lookup(packet, payload);
        const auto& range = *packet_range;
        const auto& descriptor = provenance_->Ledger().Descriptor(range.task_id);
        if (descriptor.payload_regions)
        {
            business = 0;
            for (const auto& region : *descriptor.payload_regions)
            {
                const auto a = std::max(region.begin, range.task_begin);
                const auto b = std::min(region.end, range.task_begin + range.bytes);
                if (a >= b) continue;
                if (region.kind == "business") business += b-a;
                else if (region.kind == "metadata") metadata += b-a;
                else if (region.kind == "padding") padding += b-a;
                else Require(false, "Unknown payload region");
            }
            Require(business + metadata + padding == payload, "Incomplete packet byte classification");
        }
    }
    if (retain_packet_events_)
    {
        Json event={{"sim_time_ns", now}, {"packet_uid", packet->GetUid()},
            {"counted_packet_bytes", whole}, {"business_payload_bytes", business}};
        if (provenance_->Ledger().HasPayloadRegions())
        {
            event["application_metadata_bytes"] = metadata;
            event["padding_bytes"] = padding;
        }
        Record("packet-events.jsonl",packet_events_,event);
    }
    windows_.Add(now, whole, business, metadata, padding);
    all_bytes_ += whole; ++all_packets_;
    // Validation fingerprint of actual serialized bytes AND packet start times.
    auto hash_byte = [this](uint8_t b) { fingerprint_ = (fingerprint_ ^ b) * 1099511628211ULL; };
    for (auto value : {now, static_cast<uint64_t>(whole)})
        for (unsigned i = 0; i < 8; ++i) hash_byte((value >> (8 * i)) & 255);
    std::vector<uint8_t> wire(whole);
    packet->CopyData(wire.data(), whole);
    for (auto b : wire) hash_byte(b);
    if (config_.provenance && business)
    {
        const auto range = *packet_range;
        const auto& descriptor = provenance_->Ledger().Descriptor(range.task_id);
        auto regions = descriptor.payload_regions.value_or(std::vector<PayloadRegion>{{0, descriptor.bytes, "business"}});
        for (const auto& region : regions)
        {
            if (region.kind != "business") continue;
            const auto a = std::max(region.begin, range.task_begin), b = std::min(region.end, range.task_begin + range.bytes);
            if (a >= b || now < config_.start_ns || now >= config_.end_ns) continue;
            Record("truth/packet_ranges.jsonl",packet_ranges_,{{"sim_time_ns", now}, {"packet_uid", packet->GetUid()},
                {"ub_task_id", descriptor.task_id}, {"transfer_id", descriptor.transfer_id},
                {"operation_id", descriptor.operation_id.empty() ? descriptor.graph_id + "/write" : descriptor.operation_id},
                {"task_byte_range", {a, b}},
                {"packet_byte_range", {range.packet_begin + a-range.task_begin, range.packet_begin + b-range.task_begin}},
                {"counted_packet_bytes", whole}});
        }
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

namespace
{
Json TransferRecord(const UbWriteTransfer* transfer)
{
    const auto& d = transfer->Descriptor();
    Require(!transfer->Completion().Active(), "Unfinished transfer in provenance export");
    Json record = {{"transfer_id", d.transfer_id},
        {"operation_id", d.operation_id.empty() ? d.graph_id + "/write" : d.operation_id},
        {"ub_task_id", d.task_id}, {"src_rank", d.src_rank}, {"dst_rank", d.dst_rank},
        {"src_ub_node", d.SourceNode()}, {"dst_ub_node", d.DestinationNode()},
        {"payload_bytes", d.bytes}, {"business_payload_bytes", d.bytes}, {"application_metadata_bytes", 0},
        {"task_byte_range", {0, d.bytes}}, {"transport_profile", "URMA_WRITE/RTP"},
        {"priority", 7}, {"chunk_id", d.chunk_index}};
    if (d.payload_regions)
    {
        uint64_t business = 0, metadata = 0, padding = 0;
        Json regions = Json::array();
        for (const auto& r : *d.payload_regions)
        {
            if (r.kind == "business") business += r.end-r.begin;
            else if (r.kind == "metadata") metadata += r.end-r.begin;
            else padding += r.end-r.begin;
            regions.push_back({{"begin",r.begin},{"end",r.end},{"kind",r.kind}});
        }
        record["business_payload_bytes"] = business;
        record["application_metadata_bytes"] = metadata;
        record["padding_bytes"] = padding;
        record["payload_regions"] = regions;
    }
    if (!d.operation_id.empty())
    {
        record["pair_byte_range"] = {d.pair_byte_offset, d.pair_byte_offset + d.bytes};
        record["pair_payload_bytes"] = d.pair_payload_bytes;
    }
    return record;
}
}

Json TransferRegistryRecord(const UbWriteTransfer& transfer) { return TransferRecord(&transfer); }

ObservationRegistry ObservationRegistry::ReferToResult(const std::vector<int>& ranks, size_t transfer_count,
    const std::filesystem::path& directory, bool truth)
{
    Require(!std::filesystem::exists(directory), "Registry directory already exists");
    std::filesystem::create_directories(directory);
    Json fields = {{"observed/endpoint_ops.jsonl", "endpoint_ops"}};
    if (truth)
    {
        fields["truth/operation_registry.jsonl"] = "operation_registry";
        fields["truth/transfer_registry.jsonl"] = "transfer_registry";
    }
    Save((directory / "registry-records.json").string(),
        {{"schema_version", "moe-ub-observation-registry/2"},
         {"result", "result.json"}, {"path_base", "execution_directory"}, {"fields", fields}});
    return {directory, ranks, transfer_count, truth, true};
}

ObservationRegistry ObservationRegistry::Write(const std::vector<const UbWriteTransfer*>& transfers,
    const Json& endpoints, const Json& operations, const std::filesystem::path& directory, bool truth)
{
    Require(!std::filesystem::exists(directory), "Registry directory already exists");
    std::filesystem::create_directories(directory);
    ObservationRegistry result{directory, {}, transfers.size(), truth};
    Save((directory / "endpoint_ops.jsonl").string(), endpoints, true);
    if (truth)
    {
        Save((directory / "operation_registry.jsonl").string(), operations, true);
        std::ofstream stream(directory / "transfer_registry.jsonl");
        std::set<int> ranks;
        for (const auto* transfer : transfers)
        {
            const auto& d = transfer->Descriptor();
            ranks.insert(d.src_rank); ranks.insert(d.dst_rank);
            stream << TransferRecord(transfer).dump() << '\n';
            Require(bool(stream), "Cannot write shared transfer registry");
        }
        stream.close(); Require(bool(stream), "Cannot close shared transfer registry");
        result.ranks.assign(ranks.begin(), ranks.end());
    }
    return result;
}

void PortObservation::Export(const std::vector<const UbWriteTransfer*>& transfers,
                             const Json& endpoint_ops, const Json& operation_registry,
                             const std::string& directory, const ObservationRegistry* shared_registry) const
{
    const std::filesystem::path base(directory);
    auto save = [&](const std::string& name, const Json& value, bool lines = false) {
        Save((base / name).string(), value, lines);
    };
    Require(port_->GetTxBytes() - initial_tx_bytes_ == all_bytes_, "Native port counter mismatch");
    if (record_store_)
        Require(shared_registry && shared_registry->external_records && transfers.empty() &&
                shared_registry->transfer_count == provenance_->Ledger().RegisteredTransferTotal(), "Stored observation registry mismatch");
    else Require(transfers.size() == provenance_->Ledger().TransferCount(), "Observation export transfer count mismatch");
    std::filesystem::create_directories(base / "observed");
    save("observed/public_config.json", {{"schema_version", "moe-ub-m1-observed/1"},
        {"counter_location", "egress_packet_start"}, {"byte_semantics", "packet_get_size"},
        {"switch_id", config_.switch_id}, {"port_id", config_.port_id}, {"direction", "egress"},
        {"observation_start_local_ns", config_.start_ns}, {"observation_end_local_ns", config_.end_ns},
        {"window_period_ns", config_.period_ns}, {"clock_resolution_ns", 1}});
    if (record_store_) { ExportStored(base, *shared_registry); return; }
    const bool streamed=!config_.resource_budget.is_null();
    std::ofstream observed_stream,truth_stream;
    if(streamed)
    {
        Require(std::filesystem::absolute(base)==std::filesystem::absolute(spool_directory_),"Spool/export directory mismatch");
        for(auto& [name,stream]:streams_) { stream.flush();Require(bool(stream),"Cannot flush observation stream"); }
        observed_stream.open(base/"observed/port_counters.jsonl");
        if(config_.provenance) truth_stream.open(base/"truth/port_byte_truth.jsonl");
    }
    Json observed = Json::array(), truth = Json::array();
    for (size_t i = 0; i < windows_.Count(); ++i)
    {
        const auto row = windows_.Row(i);
        const auto start = config_.start_ns + i * config_.period_ns;
        const auto end = start + std::min(config_.period_ns, config_.end_ns - start);
        observed.push_back({{"switch_id", config_.switch_id}, {"port_id", config_.port_id},
            {"direction", "egress"}, {"local_window_start_ns", start}, {"local_window_end_ns", end},
            {"counted_packets", row.packets}, {"counted_packet_bytes", row.whole}});
        truth.push_back({{"window_start_sim_ns", start}, {"window_end_sim_ns", end},
            {"business_payload_bytes", row.business}, {"application_metadata_bytes", row.metadata},
            {"data_protocol_overhead_bytes", row.overhead}, {"control_packet_bytes", row.control},
            {"unattributed_bytes", 0}});
        if (provenance_->Ledger().HasPayloadRegions()) truth.back()["padding_bytes"] = row.padding;
        if(streamed)
        {
            observed_stream<<observed.back().dump()<<'\n';
            if(config_.provenance) truth_stream<<truth.back().dump()<<'\n';
            Require(bool(observed_stream) && (!config_.provenance || bool(truth_stream)),"Cannot stream window output");
            observed.clear();truth.clear();
        }
    }
    if(!streamed) save("observed/port_counters.jsonl", observed, true);
    else { observed_stream.close();Require(bool(observed_stream),"Cannot close windows");if(config_.provenance) { truth_stream.close();Require(bool(truth_stream),"Cannot close truth windows"); } }
    if (shared_registry)
    {
        Require(shared_registry->transfer_count == transfers.size() &&
                (!config_.provenance || shared_registry->truth), "Shared observation registry mismatch");
        std::filesystem::create_hard_link(shared_registry->directory / "endpoint_ops.jsonl", base / "observed/endpoint_ops.jsonl");
    }
    else save("observed/endpoint_ops.jsonl", endpoint_ops, true);
    save("observation-audit.json", {{"purpose", "test_only_internal_validation"},
        {"all_successful_start_packets", all_packets_}, {"all_successful_start_bytes", all_bytes_},
        {"native_tx_bytes_delta", port_->GetTxBytes() - initial_tx_bytes_},
        {"wire_and_time_fnv1a64", fingerprint_}});
    if(streamed) save("resource-audit.json",{{"record_storage","streamed_jsonl"},{"event_bytes",event_bytes_},
        {"retained_packet_event_records",packet_events_.size()},{"retained_queue_records",queue_events_.size()},
        {"retained_packet_range_records",packet_ranges_.size()},{"resident_window_rows",windows_.ResidentRows()},
        {"logical_window_rows",windows_.Count()},{"ledger_packets",provenance_->Ledger().PacketCount()},
        {"registered_transfers",provenance_->Ledger().TransferCount()},{"budget",config_.resource_budget},
        {"provenance_storage","shared_run_ledger"},{"owned_descriptor_records",0},{"owned_packet_provenance_records",0}});
    else { save("egress-queue-audit.jsonl", queue_events_, true);
        if (retain_packet_events_) save("packet-events.jsonl", packet_events_, true); }
    if (!config_.provenance) return;
    std::filesystem::create_directory(base / "truth");
    std::set<int> ranks;
    Json registry = Json::array();
    if (shared_registry)
        ranks.insert(shared_registry->ranks.begin(), shared_registry->ranks.end());
    else for (const auto* transfer : transfers)
    {
        ranks.insert(transfer->Descriptor().src_rank); ranks.insert(transfer->Descriptor().dst_rank);
        registry.push_back(TransferRecord(transfer));
    }
    save("truth/clock_models.json", {{"schema_version", "moe-ub-m1-truth/1"},
        {"offset_ns", 0}, {"drift", 0}, {"rounding", "floor"},
        {"switch_ids", {config_.switch_id}}, {"rank_ids", ranks}});
    if (shared_registry)
        for (const auto* name : {"operation_registry.jsonl", "transfer_registry.jsonl"})
            std::filesystem::create_hard_link(shared_registry->directory / name, base / "truth" / name);
    else
    {
        save("truth/operation_registry.jsonl", operation_registry, true);
        save("truth/transfer_registry.jsonl", registry, true);
    }
    if(!streamed) { save("truth/packet_ranges.jsonl", packet_ranges_, true);
        save("truth/port_byte_truth.jsonl", truth, true); }
}

void PortObservation::ExportStored(const std::filesystem::path& base, const ObservationRegistry& registry) const
{
    Require(!config_.provenance || registry.truth, "Missing stored truth registry");
    Require(packet_events_.empty() && packet_ranges_.empty() && queue_events_.empty(), "Stored port retained event JSON");
    windows_.Finish();
    Json fields = Json::object();
    for (const std::string name : {"packet-events.jsonl", "egress-queue-audit.jsonl", "port-windows.jsonl",
                                  "truth/packet_ranges.jsonl", "truth-windows.jsonl"})
    {
        if (!config_.provenance && (name == "truth/packet_ranges.jsonl" || name == "truth-windows.jsonl")) continue;
        fields[name] = RecordReference(record_store_->Seal(RecordName(name)));
    }
    Save((base / "record-storage.json").string(), {{"schema_version", "moe-ub-port-records/1"},
        {"records_directory", "records"}, {"path_base", "execution_directory"}, {"fields", fields},
        {"window_encoding", "sparse_nonzero_with_declared_zero_windows"}, {"logical_windows", windows_.Count()},
        {"padding_field", provenance_->Ledger().HasPayloadRegions()}});
    std::filesystem::create_hard_link(registry.directory / "registry-records.json", base / "registry-records.json");
    Save((base / "observation-audit.json").string(), {{"purpose", "test_only_internal_validation"},
        {"all_successful_start_packets", all_packets_}, {"all_successful_start_bytes", all_bytes_},
        {"native_tx_bytes_delta", port_->GetTxBytes() - initial_tx_bytes_}, {"wire_and_time_fnv1a64", fingerprint_}});
    Save((base / "resource-audit.json").string(), {{"record_storage", "bounded_gzip_records"}, {"event_bytes", event_bytes_},
        {"retained_packet_event_records", 0}, {"retained_queue_records", 0}, {"retained_packet_range_records", 0},
        {"resident_window_rows", windows_.ResidentRows()}, {"peak_window_rows_bound", 1}, {"logical_window_rows", windows_.Count()},
        {"ledger_packets", provenance_->Ledger().PacketCount()}, {"registered_transfers", provenance_->Ledger().TransferCount()},
        {"budget", config_.resource_budget}, {"provenance_storage", "shared_run_ledger"},
        {"owned_descriptor_records", 0}, {"owned_packet_provenance_records", 0}});
    if (config_.provenance)
    {
        std::filesystem::create_directories(base / "truth");
        Save((base / "truth/clock_models.json").string(), {{"schema_version", "moe-ub-m1-truth/1"},
            {"offset_ns", 0}, {"drift", 0}, {"rounding", "floor"},
            {"switch_ids", {config_.switch_id}}, {"rank_ids", registry.ranks}});
    }
}
}
