#pragma once

#include "ns3/event-id.h"
#include "ns3/ptr.h"

#include <cstdint>
#include <optional>
#include <string>

namespace ns3 { class UbApp; }

namespace moe_ub
{
// M1.2 binds one descriptor to two rank adapters. No CSV phase state is involved.
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
    bool ReceiverWaiting() const { return recv_callback_ != nullptr; }
    uint64_t SubmittedTasks() const { return submitted_tasks_; }
    uint64_t SendCallbacks() const { return send_callbacks_; }
    std::optional<uint64_t> TransactionDoneNs() const { return transaction_done_ns_; }
    std::optional<uint64_t> SendDoneNs() const { return send_done_ns_; }

  private:
    void Validate(int src, int dst, uint64_t bytes, int type, int tag,
                  void (*callback)(void*)) const;
    WriteDescriptor descriptor_;
    ns3::Ptr<ns3::UbApp> source_;
    bool send_registered_ = false;
    void (*recv_callback_)(void*) = nullptr;
    void* recv_arg_ = nullptr;
    ns3::EventId submit_event_;
    ns3::EventId callback_event_;
    uint64_t submitted_tasks_ = 0;
    uint64_t send_callbacks_ = 0;
    std::optional<uint64_t> transaction_done_ns_;
    std::optional<uint64_t> send_done_ns_;
};
}
