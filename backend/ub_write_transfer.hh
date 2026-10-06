#pragma once

#include "write_completion.hh"
#include "message_categories.hh"
#include "ns3/event-id.h"
#include "ns3/ptr.h"
#include "ns3/ub-datatype.h"

#include <cstdint>
#include <optional>
#include <string>
#include <vector>
#include <map>

namespace ns3 { class UbApp; class UbTransaction; class UbWqeSegment; class UbLdstApi; class Packet; }

namespace moe_ub
{
enum class TransferOperation { Write, Store, Load };
struct PayloadRegion
{
    uint64_t begin, end;
    std::string kind;
};
// M1 binds one descriptor to two rank adapters. No CSV phase state is involved.
struct WriteDescriptor
{
    uint64_t transfer_id;
    uint64_t task_id;
    std::string graph_id;
    uint64_t send_node_id;
    uint64_t recv_node_id;
    int src_rank;
    int dst_rank;
    int tag;
    uint64_t bytes;
    std::string operation_id{}; // Empty only for the original single-WRITE fixture.
    uint32_t chunk_index = 0;
    uint64_t pair_byte_offset = 0;
    uint64_t pair_payload_bytes = 0; // Zero only for the original single-WRITE fixture.
    std::optional<uint32_t> src_ub_node{}, dst_ub_node{};
    // Absent on legacy plans: their entire task is useful vector/KV data.
    std::optional<std::vector<PayloadRegion>> payload_regions{};
    TransferOperation operation = TransferOperation::Write;
    MessageCategory metadata_category = MessageCategory::AttachedMetadata;
    int DataOwner() const { return operation == TransferOperation::Load ? dst_rank : src_rank; }
    int Consumer() const { return operation == TransferOperation::Load ? src_rank : dst_rank; }
    // Legacy M1/KV descriptors use identity mapping; layer plans set both explicitly.
    uint32_t SourceNode() const { return src_ub_node.value_or(static_cast<uint32_t>(src_rank)); }
    uint32_t DestinationNode() const { return dst_ub_node.value_or(static_cast<uint32_t>(dst_rank)); }
};

class UbWriteTransfer
{
  public:
    UbWriteTransfer(WriteDescriptor descriptor, ns3::Ptr<ns3::UbApp> source);
    ~UbWriteTransfer();
    UbWriteTransfer(const UbWriteTransfer&) = delete;
    UbWriteTransfer& operator=(const UbWriteTransfer&) = delete;

    void Send(int rank, uint64_t bytes, int type, int dst, int tag,
              void (*callback)(void*), void* arg);
    void Receive(int rank, uint64_t bytes, int type, int src, int tag,
                 void (*callback)(void*), void* arg);

    const WriteDescriptor& Descriptor() const { return descriptor_; }
    bool ReceiverWaiting() const { return completion_.ReceiverWaiting(); }
    uint64_t SubmittedTasks() const { return submitted_tasks_; }
    const WriteCompletion& Completion() const { return completion_; }
    uint64_t SendCallbacks() const { return completion_.SendCallbacks(); }
    std::optional<uint64_t> TransactionDoneNs() const { return completion_.TransactionDoneNs(); }
    std::optional<uint64_t> SendDoneNs() const { return completion_.SendDoneNs(); }
    bool ObservingTarget() const { return target_ != nullptr || native_source_ != nullptr; }
    bool SourceJettyRetired() const { return jetty_retired_; }
    std::optional<uint64_t> SendStartNs() const { return send_start_ns_; }
    std::optional<uint64_t> ReceiveStartNs() const { return receive_start_ns_; }
    bool HasPendingEvents() const { return submit_event_.IsPending() || completion_.HasPendingEvents(); }

  private:
    void Validate(int src, int dst, uint64_t bytes, int type, int tag,
                  void (*callback)(void*)) const;
    void ObserveWrite(ns3::Ptr<const ns3::UbWqeSegment> segment);
    void ObserveNativeRequest(ns3::Ptr<const ns3::Packet>, uint32_t, uint64_t, uint32_t, ns3::UbMemOperationType);
    void ObserveNativeTarget(uint64_t, uint32_t, ns3::UbMemOperationType);
    void ObserveNativeResponse(uint64_t, uint32_t, uint64_t, uint32_t, ns3::UbMemOperationType);
    void SubmitNative();
    void DisconnectTarget();
    std::string Identity() const;
    WriteDescriptor descriptor_;
    ns3::Ptr<ns3::UbApp> source_;
    ns3::Ptr<ns3::UbTransaction> target_;
    ns3::Ptr<ns3::UbLdstApi> native_source_, native_target_;
    std::map<uint64_t, std::pair<uint64_t, uint32_t>> native_requests_;
    WriteCompletion completion_;
    ns3::EventId submit_event_;
    uint64_t submitted_tasks_ = 0;
    std::optional<uint64_t> send_start_ns_, receive_start_ns_;
    std::optional<uint32_t> jetty_;
    bool jetty_retired_ = false;
};
}
