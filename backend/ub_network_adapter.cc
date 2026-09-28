#include "ub_network_adapter.hh"

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

UbNetworkAdapter::UbNetworkAdapter(int rank, Finished finished)
    : AstraNetworkAPI(rank), finished_(std::move(finished))
{
    static_assert(std::numeric_limits<long double>::digits >= 63,
                  "ASTRA time must represent every signed 64-bit nanosecond value");
    if (rank < 0 || !finished_)
    {
        throw std::invalid_argument("A nonnegative rank and completion handler are required");
    }
    NowNs();
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

int UbNetworkAdapter::sim_send(void*, uint64_t, int, int, int, AstraSim::sim_request*,
                              void (*)(void*), void*)
{
    throw std::logic_error("UB SEND is not implemented in M1.1");
}

int UbNetworkAdapter::sim_recv(void*, uint64_t, int, int, int, AstraSim::sim_request*,
                              void (*)(void*), void*)
{
    throw std::logic_error("UB RECV is not implemented in M1.1");
}
}
