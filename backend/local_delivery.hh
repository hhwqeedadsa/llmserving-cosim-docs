#pragma once
#include "extern/helper/json/json.hpp"
#include "completion_event.hh"
#include <cstdint>
#include <functional>
#include <string>

namespace moe_ub
{
struct LocalDescriptor
{
    uint64_t transfer_id;
    std::string graph_id;
    uint64_t send_node_id, recv_node_id;
    int src_rank, dst_rank, tag;
    uint64_t bytes;
    std::string operation_id;
};
// Same-chip readiness after a source COMP node. No UB app, task or link state.
class LocalDelivery
{
  public:
    explicit LocalDelivery(LocalDescriptor descriptor);
    const LocalDescriptor& Descriptor() const { return descriptor_; }
    void Send(int rank, uint64_t bytes, int type, int peer, int tag, void (*callback)(void*), void* arg);
    void Receive(int rank, uint64_t bytes, int type, int peer, int tag, void (*callback)(void*), void* arg);
    bool Active() const { return !send_done_ || !recv_done_; }
    bool HasPendingEvents() const { return pending_ != 0 || ready_.Pending(); }
    void WaitReady(std::function<void()> callback) { ready_.Wait(std::move(callback)); }
    nlohmann::json Result() const;
  private:
    void Check(bool send, int rank, uint64_t bytes, int type, int peer, int tag, void (*callback)(void*)) const;
    void CompleteReceive();
    LocalDescriptor descriptor_;
    bool sent_ = false, received_ = false, send_done_ = false, recv_done_ = false;
    unsigned pending_ = 0;
    std::function<void()> receive_;
    nlohmann::json ready_ns_ = nullptr, receive_ns_ = nullptr, send_done_ns_ = nullptr, recv_done_ns_ = nullptr;
    CompletionEvent ready_;
};
}
