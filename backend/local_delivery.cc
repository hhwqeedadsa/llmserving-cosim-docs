#include "local_delivery.hh"
#include "ns3/simulator.h"
#include <stdexcept>
#include <utility>

namespace moe_ub
{
LocalDelivery::LocalDelivery(LocalDescriptor descriptor) : descriptor_(std::move(descriptor))
{
    const auto& d = descriptor_;
    if (!d.transfer_id || d.graph_id.empty() || !d.send_node_id || !d.recv_node_id ||
        d.src_rank < 0 || d.dst_rank < 0 || d.src_rank == d.dst_rank || d.tag <= 0 ||
        d.tag >= 500000000 || !d.bytes || d.bytes > INT64_MAX || d.operation_id.empty())
        throw std::invalid_argument("Invalid local delivery descriptor");
}
void LocalDelivery::Check(bool send, int rank, uint64_t bytes, int type, int peer, int tag,
                           void (*callback)(void*)) const
{
    const auto& d = descriptor_;
    if (rank != (send ? d.src_rank : d.dst_rank) || peer != (send ? d.dst_rank : d.src_rank) ||
        bytes != d.bytes || type != 0 || tag != d.tag || !callback || (send ? sent_ : received_))
        throw std::invalid_argument("Mismatched or repeated local SEND/RECV");
}
void LocalDelivery::Send(int rank, uint64_t bytes, int type, int peer, int tag,
                         void (*callback)(void*), void* arg)
{
    Check(true, rank, bytes, type, peer, tag, callback);
    sent_ = true;
    ready_ns_ = ns3::Simulator::Now().GetNanoSeconds();
    ++pending_;
    ns3::Simulator::ScheduleNow([this, callback, arg]() {
        --pending_; send_done_ = true; send_done_ns_ = ns3::Simulator::Now().GetNanoSeconds();
        callback(arg);
    });
    if (received_) CompleteReceive();
}
void LocalDelivery::Receive(int rank, uint64_t bytes, int type, int peer, int tag,
                            void (*callback)(void*), void* arg)
{
    Check(false, rank, bytes, type, peer, tag, callback);
    received_ = true;
    receive_ns_ = ns3::Simulator::Now().GetNanoSeconds();
    receive_ = [callback, arg]() { callback(arg); };
    if (sent_) CompleteReceive();
}
void LocalDelivery::CompleteReceive()
{
    auto callback = std::move(receive_);
    ++pending_;
    ns3::Simulator::ScheduleNow([this, callback = std::move(callback)]() {
        --pending_; recv_done_ = true; recv_done_ns_ = ns3::Simulator::Now().GetNanoSeconds();
        callback();
    });
}
nlohmann::json LocalDelivery::Result() const
{
    const auto& d = descriptor_;
    return {{"transfer_id", d.transfer_id}, {"graph_id", d.graph_id}, {"operation_id", d.operation_id},
        {"src_rank", d.src_rank}, {"dst_rank", d.dst_rank}, {"comm_tag", d.tag}, {"payload_bytes", d.bytes},
        {"locality", "same_chip"}, {"ready_ns", ready_ns_}, {"recv_registered_ns", receive_ns_},
        {"send_callback_ns", send_done_ns_}, {"recv_callback_ns", recv_done_ns_},
        {"send_callbacks", send_done_ ? 1 : 0}, {"recv_callbacks", recv_done_ ? 1 : 0},
        {"active", Active()}, {"pending_events", pending_}};
}
}
