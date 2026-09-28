#pragma once

#include "write_completion.hh"
#include "ns3/event-id.h"
#include "ns3/ptr.h"

#include <cstdint>
#include <optional>
#include <string>

namespace ns3 { class UbApp; class UbTransaction; class UbWqeSegment; }

namespace moe_ub
{
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
    bool ObservingTarget() const { return target_ != nullptr; }
    std::optional<uint64_t> SendStartNs() const { return send_start_ns_; }
    std::optional<uint64_t> ReceiveStartNs() const { return receive_start_ns_; }

  private:
    void Validate(int src, int dst, uint64_t bytes, int type, int tag,
                  void (*callback)(void*)) const;
    void ObserveWrite(ns3::Ptr<const ns3::UbWqeSegment> segment);
    void DisconnectTarget();
    std::string Identity() const;
    WriteDescriptor descriptor_;
    ns3::Ptr<ns3::UbApp> source_;
    ns3::Ptr<ns3::UbTransaction> target_;
    WriteCompletion completion_;
    ns3::EventId submit_event_;
    uint64_t submitted_tasks_ = 0;
    std::optional<uint64_t> send_start_ns_, receive_start_ns_;
};
}
