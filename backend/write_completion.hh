#pragma once

#include "ns3/event-id.h"

#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>

namespace moe_ub
{
// Completion state for one immutable transfer identity. The final record survives
// retirement; borrowed callbacks and per-segment matching state do not.
class WriteCompletion
{
  public:
    WriteCompletion(std::string identity, uint64_t bytes, std::function<void()> retired);
    ~WriteCompletion();
    WriteCompletion(const WriteCompletion&) = delete;
    WriteCompletion& operator=(const WriteCompletion&) = delete;
    void RegisterSend(void (*callback)(void*), void* arg);
    void RegisterReceive(void (*callback)(void*), void* arg);
    void WriteCommitted(uint32_t jetty, uint64_t sequence, uint64_t bytes);
    void TransactionDone();

    bool Active() const { return context_ != nullptr; }
    bool ReceiverWaiting() const;
    uint64_t CommittedBytes() const { return committed_bytes_; }
    uint64_t CommittedSegments() const { return committed_segments_; }
    uint64_t SendCallbacks() const { return send_done_ns_ ? 1 : 0; }
    uint64_t ReceiveCallbacks() const { return receive_done_ns_ ? 1 : 0; }
    std::optional<uint64_t> DataReadyNs() const { return data_ready_ns_; }
    std::optional<uint64_t> TransactionDoneNs() const { return transaction_done_ns_; }
    std::optional<uint64_t> SendDoneNs() const { return send_done_ns_; }
    std::optional<uint64_t> ReceiveDoneNs() const { return receive_done_ns_; }

  private:
    struct CallbackSlot
    {
        bool registered = false;
        void (*callback)(void*) = nullptr;
        void* arg = nullptr;
    };
    struct Context
    {
        CallbackSlot send, receive;
        std::map<std::pair<uint32_t, uint64_t>, uint64_t> segments;
    };
    void Require(bool condition, const char* message) const;
    void QueueReceive();
    void Deliver(bool sending);
    std::string identity_;
    uint64_t expected_bytes_;
    std::function<void()> retired_;
    std::unique_ptr<Context> context_;
    ns3::EventId send_event_, receive_event_;
    uint64_t committed_bytes_ = 0, committed_segments_ = 0;
    std::optional<uint64_t> data_ready_ns_, transaction_done_ns_, send_done_ns_, receive_done_ns_;
};
}
