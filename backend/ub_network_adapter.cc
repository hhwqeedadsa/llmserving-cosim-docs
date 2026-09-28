#include "ub_network_adapter.hh"
#include "ub_write_transfer.hh"

#include "ns3/simulator.h"

#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace moe_ub
{
namespace
{
int64_t NowNs()
{
    if (ns3::Time::GetResolution() != ns3::Time::NS)
    {
        throw std::logic_error("The joint backend requires ns-3 nanosecond resolution");
    }
    const auto now = ns3::Simulator::Now().GetNanoSeconds();
    if (now < 0)
    {
        throw std::overflow_error("Negative ns-3 simulation time");
    }
    return now;
}
}

UbNetworkAdapter::UbNetworkAdapter(int rank, Finished finished, UbWriteTransfer* transfer)
    : UbNetworkAdapter(rank, std::move(finished),
        transfer ? std::vector<UbWriteTransfer*>{transfer} : std::vector<UbWriteTransfer*>{})
{}

UbNetworkAdapter::UbNetworkAdapter(int rank, Finished finished,
                                 const std::vector<UbWriteTransfer*>& transfers)
    : AstraNetworkAPI(rank), finished_(std::move(finished))
{
    static_assert(std::numeric_limits<long double>::digits >= 63,
                  "ASTRA time must represent every signed 64-bit nanosecond value");
    if (rank < 0 || !finished_)
    {
        throw std::invalid_argument("A nonnegative rank and completion handler are required");
    }
    NowNs();
    for (auto* transfer : transfers)
    {
        if (!transfer) throw std::invalid_argument("Null UB transfer descriptor");
        const auto& d = transfer->Descriptor();
        if (d.src_rank != rank && d.dst_rank != rank) continue;
        if (!transfers_.emplace(std::make_tuple(d.src_rank, d.dst_rank, d.tag), transfer).second)
            throw std::invalid_argument("Duplicate UB SEND/RECV matching key");
    }
}

AstraSim::AstraNetworkAPI::BackendType UbNetworkAdapter::get_backend_type()
{
    return BackendType::NS3;
}

AstraSim::timespec_t UbNetworkAdapter::sim_get_time()
{
    return {AstraSim::NS, static_cast<long double>(NowNs())};
}

void UbNetworkAdapter::sim_schedule(AstraSim::timespec_t delta,
                                   void (*callback)(void*), void* arg)
{
    // Locked Sys::try_register_event supplies NS; reject ambiguous other units.
    if (delta.time_res != AstraSim::NS || !std::isfinite(delta.time_val) ||
        delta.time_val < 0 || callback == nullptr)
    {
        throw std::invalid_argument("Expected a finite nonnegative NS delay and a callback");
    }
    const auto now = NowNs();
    const long double rounded = std::ceil(delta.time_val);
    const auto remaining = std::numeric_limits<int64_t>::max() - now;
    if (rounded > static_cast<long double>(remaining))
    {
        throw std::overflow_error("Event time exceeds the ns-3 signed nanosecond range");
    }
    const auto duration = static_cast<int64_t>(rounded);
    ns3::Simulator::Schedule(ns3::NanoSeconds(duration), [this, callback, arg]() {
        ++executed_callbacks_;
        callback(arg);
    });
    ++scheduled_events_;
}

void UbNetworkAdapter::sim_notify_finished()
{
    finished_(static_cast<uint64_t>(NowNs()));
}

int UbNetworkAdapter::sim_send(void*, uint64_t count, int type, int dst, int tag,
                              AstraSim::sim_request*, void (*callback)(void*), void* arg)
{
    auto it = transfers_.find({rank, dst, tag});
    if (transfers_.empty()) throw std::logic_error("No UB transfer descriptor configured for SEND");
    if (it == transfers_.end()) throw std::invalid_argument("SEND matching key is not in the UB transfer registry");
    it->second->Send(rank, count, type, dst, tag, callback, arg);
    return 0;
}

int UbNetworkAdapter::sim_recv(void*, uint64_t count, int type, int src, int tag,
                              AstraSim::sim_request*, void (*callback)(void*), void* arg)
{
    auto it = transfers_.find({src, rank, tag});
    if (transfers_.empty()) throw std::logic_error("No UB transfer descriptor configured for RECV");
    if (it == transfers_.end()) throw std::invalid_argument("RECV matching key is not in the UB transfer registry");
    it->second->Receive(rank, count, type, src, tag, callback, arg);
    return 0;
}
}
