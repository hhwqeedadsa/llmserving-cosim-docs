#pragma once

#include "ub_write_transfer.hh"
#include "identity_ranges.hh"
#include "record_store.hh"
#include "queue_sources.hh"
#include "message_windows.hh"
#include "extern/helper/json/json.hpp"
#include "ns3/packet.h"
#include "ns3/nstime.h"
#include "ns3/ub-controller.h"
#include "ns3/ub-port.h"

#include <map>
#include <set>
#include <memory>
#include <optional>
#include <vector>
#include <fstream>
#include <filesystem>

namespace moe_ub
{
struct ObservationConfig
{
    uint32_t switch_id, port_id;
    uint64_t start_ns, end_ns, period_ns;
    bool provenance;
    nlohmann::json resource_budget = nullptr;
    nlohmann::json queue_sampling = nullptr;
    nlohmann::json recording = nullptr;
    static ObservationConfig Parse(const nlohmann::json& input, uint64_t stop_ns);
};

// No sampling events: bucket successful starts, and emit every configured window.
class PortWindows
{
  public:
    explicit PortWindows(ObservationConfig config);
    void Add(uint64_t time_ns, uint32_t whole, uint32_t business, uint32_t metadata = 0, uint32_t padding = 0);
    const std::vector<ByteCounts>& Rows() const { return rows_; }
    uint64_t Count() const { return (config_.end_ns-config_.start_ns-1)/config_.period_ns+1; }
    ByteCounts Row(uint64_t index) const;
    size_t ResidentRows() const { return sink_ ? size_t(current_.has_value()) : config_.resource_budget.is_null() ? rows_.size() : sparse_.size(); }
    void Stream(std::function<void(uint64_t, const ByteCounts&)> sink);
    void Finish();
  private:
    ObservationConfig config_;
    std::vector<ByteCounts> rows_;
    std::map<uint64_t, ByteCounts> sparse_;
    std::function<void(uint64_t, const ByteCounts&)> sink_;
    std::optional<uint64_t> current_;
    ByteCounts current_row_;
    bool stream_finished_ = false;
};

struct PayloadRange
{
    uint64_t task_begin;
    uint32_t packet_begin, bytes, packet_size, task_id;
    bool operator==(const PayloadRange&) const = default;
};

// Packet::Copy preserves UID. This bounded RTP fixture does not fragment packets.
// A UID collision with different ranges, or missing provenance, is an error.
class PayloadLedger
{
  public:
    explicit PayloadLedger(WriteDescriptor descriptor) : PayloadLedger(std::vector<WriteDescriptor>{std::move(descriptor)}) {}
    explicit PayloadLedger(const std::vector<WriteDescriptor>& descriptors);
    void Register(const std::vector<WriteDescriptor>& descriptors);
    void Created(ns3::Ptr<const ns3::Packet> packet, uint32_t task, uint64_t task_begin,
                 uint32_t packet_begin, uint32_t bytes);
    void NativeCreated(ns3::Ptr<const ns3::Packet>, uint32_t, uint64_t, uint32_t, ns3::UbMemOperationType);
    void NativeResponse(uint64_t, ns3::Ptr<const ns3::Packet>, ns3::UbMemOperationType);
    void NativeRequestArrived(uint64_t,uint32_t,ns3::UbMemOperationType);
    void NativeResponseArrived(uint64_t,uint32_t,uint64_t,uint32_t,ns3::UbMemOperationType);
    PayloadRange Lookup(ns3::Ptr<const ns3::Packet> packet, uint32_t payload_bytes) const;
    void SetLimits(uint64_t packets, uint64_t transfers);
    void RestrictLimits(uint64_t packets, uint64_t transfers);
    // WRITE provenance may outlive transaction completion (retransmission).
    // Graph retirement still requires a drained shared network. Native useful
    // packet ranges can retire earlier at their final endpoint arrival; no
    // protocol-owned request/response state is removed by this passive ledger.
    void RetireCompletedGraphs(const std::set<std::string>& graphs);
    nlohmann::json RetirementAudit() const;
    size_t PacketCount() const { return ranges_.size(); }
    size_t TransferCount() const { return descriptors_.size(); }
    uint64_t RegisteredTransferTotal() const { return registered_tasks_.size(); }
    uint64_t CreatedNotifications() const { return created_notifications_; }
    const WriteDescriptor& Descriptor(uint32_t task) const { return descriptors_.at(task); }
    bool HasPayloadRegions() const { return segmented_payload_; }
  private:
    std::map<uint32_t, WriteDescriptor> descriptors_;
    std::map<uint64_t, PayloadRange> ranges_;
    struct NativeRequest { uint32_t task; uint64_t begin; uint32_t bytes; };
    std::map<uint64_t, NativeRequest> native_requests_;
    uint64_t max_packets_=UINT64_MAX, max_transfers_=UINT64_MAX;
    uint64_t created_notifications_=0;
    IdentityRanges registered_tasks_;
    uint64_t peak_packets_=0, peak_transfers_=0, retired_packets_=0, retired_transfers_=0;
    uint64_t native_payloads_retired_=0;
    bool segmented_payload_=false;
};

// One passive source subscription set and one ledger per observed execution.
// Ports share this owner; callbacks retain only the ledger, never a port or TP.
class WriteProvenance
{
  public:
    explicit WriteProvenance(std::vector<ns3::Ptr<ns3::UbController>> sources,
                             const std::vector<WriteDescriptor>& descriptors = {});
    ~WriteProvenance();
    WriteProvenance(const WriteProvenance&) = delete;
    WriteProvenance& operator=(const WriteProvenance&) = delete;
    void Register(const std::vector<WriteDescriptor>& descriptors) { ledger_->Register(descriptors); }
    void RestrictLimits(uint64_t packets, uint64_t transfers) { ledger_->RestrictLimits(packets, transfers); }
    const PayloadLedger& Ledger() const { return *ledger_; }
    void RetireCompletedGraphs(const std::set<std::string>& graphs) { ledger_->RetireCompletedGraphs(graphs); }
    nlohmann::json Audit() const;
  private:
    std::shared_ptr<PayloadLedger> ledger_;
    std::vector<ns3::Ptr<ns3::UbController>> sources_;
    uint64_t source_subscriptions_=0;
};

// Application-body bytes in WRITE/STORE and LOAD responses, before semantic
// region classification. Protocol acknowledgments and credits return zero.
uint32_t WritePayloadBytes(ns3::Ptr<const ns3::Packet> packet);

// Execution-wide records are identical at every port. Keep one physical copy;
// existing per-port paths remain readable through hard links on this filesystem.
struct ObservationRegistry
{
    std::filesystem::path directory;
    std::vector<int> ranks;
    size_t transfer_count = 0;
    bool truth = false;
    bool external_records = false;
    static ObservationRegistry Write(const std::vector<const UbWriteTransfer*>& transfers,
        const nlohmann::json& endpoints, const nlohmann::json& operations,
        const std::filesystem::path& directory, bool truth);
    static ObservationRegistry ReferToResult(const std::vector<int>& ranks, size_t transfer_count,
        const std::filesystem::path& directory, bool truth);
};
nlohmann::json TransferRegistryRecord(const UbWriteTransfer& transfer);

class PortObservation
{
  public:
    PortObservation(ObservationConfig config, WriteDescriptor descriptor,
                    ns3::Ptr<ns3::UbController> source);
    PortObservation(ObservationConfig config, const std::vector<WriteDescriptor>& descriptors,
                    std::vector<ns3::Ptr<ns3::UbController>> sources, bool retain_packet_events = false,
                    const std::filesystem::path& spool_directory = {});
    PortObservation(ObservationConfig config, std::shared_ptr<WriteProvenance> provenance,
                    bool retain_packet_events = false, const std::filesystem::path& spool_directory = {},
                    std::shared_ptr<RecordStore> record_store = {}, std::shared_ptr<VoqSources> queue_sources = {});
    ~PortObservation();
    PortObservation(const PortObservation&) = delete;
    PortObservation& operator=(const PortObservation&) = delete;
    const WriteProvenance* Provenance() const { return provenance_.get(); }
    void Export(const UbWriteTransfer& transfer) const;
    void Export(const std::vector<const UbWriteTransfer*>& transfers,
                const nlohmann::json& endpoint_ops, const nlohmann::json& operation_registry,
                const std::string& directory = ".",
                const ObservationRegistry* registry = nullptr) const;
  private:
    void PacketStart(ns3::Ptr<ns3::Packet> packet, ns3::Time completion_delay);
    void Enqueued(ns3::Ptr<const ns3::Packet> packet, uint32_t queue_bytes);
    void Dequeued(ns3::Ptr<const ns3::Packet> packet, uint32_t queue_bytes);
    void QueueEvent(const char* event, ns3::Ptr<const ns3::Packet> packet, uint32_t queue_bytes,
                    std::optional<uint64_t> busy_until = {});
    void Record(const std::string& name, nlohmann::json& memory, const nlohmann::json& row);
    std::string RecordName(std::string name) const;
    void WindowRecord(uint64_t index, const ByteCounts& row);
    void VoqChanged(uint64_t bytes);
    void FifoChanged(uint64_t bytes);
    void QueueState();
    void QueueWindowRecord(uint64_t index, const QueueWindow& row);
    void MessageWindowRecord(uint64_t,const ByteCounts&,const MessageAttribution&);
    bool Detailed(uint64_t) const;
    void ExportStored(const std::filesystem::path& base, const ObservationRegistry& registry) const;
    ObservationConfig config_;
    mutable PortWindows windows_;
    ns3::Ptr<ns3::UbPort> port_;
    std::shared_ptr<WriteProvenance> provenance_;
    uint64_t initial_tx_bytes_ = 0, all_bytes_ = 0, all_packets_ = 0;
    uint64_t fingerprint_ = 14695981039346656037ULL;
    nlohmann::json packet_ranges_ = nlohmann::json::array();
    bool retain_packet_events_ = false;
    // Internal input to local-clock export, never a research observation file.
    nlohmann::json packet_events_ = nlohmann::json::array();
    // Internal validation only: physical egress FIFO, excluding VOQs and the
    // packet already serializing on the link. No sampling events or packet tags.
    nlohmann::json queue_events_ = nlohmann::json::array();
    std::filesystem::path spool_directory_;
    mutable std::map<std::string,std::ofstream> streams_;
    uint64_t event_bytes_=0;
    uint64_t detail_events_=0;
    std::shared_ptr<RecordStore> record_store_;
    std::shared_ptr<VoqSources> queue_sources_;
    mutable std::unique_ptr<QueueWindows> queue_windows_;
    mutable std::unique_ptr<MessageWindows> message_windows_;
    mutable std::unique_ptr<DetailQueueState> detail_queue_;
    std::optional<std::pair<uint64_t,uint64_t>> detail_range_;
    std::set<MessageCategory> retained_categories_;
};
}
