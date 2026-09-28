#pragma once

#include "astra-sim/common/AstraNetworkAPI.hh"

#include <cstdint>
#include <functional>

namespace moe_ub
{
class UbWriteTransfer;
class UbNetworkAdapter final : public AstraSim::AstraNetworkAPI
{
  public:
    using Finished = std::function<void(uint64_t now_ns)>;

    UbNetworkAdapter(int rank, Finished finished, UbWriteTransfer* transfer = nullptr);
    BackendType get_backend_type() override;
    AstraSim::timespec_t sim_get_time() override;
    void sim_schedule(AstraSim::timespec_t delta, void (*callback)(void*), void* arg) override;
    void sim_notify_finished() override;
    int sim_send(void*, uint64_t, int, int, int, AstraSim::sim_request*,
                 void (*)(void*), void*) override;
    int sim_recv(void*, uint64_t, int, int, int, AstraSim::sim_request*,
                 void (*)(void*), void*) override;

    uint64_t ScheduledEvents() const { return scheduled_events_; }
    uint64_t ExecutedCallbacks() const { return executed_callbacks_; }

  private:
    Finished finished_;
    UbWriteTransfer* transfer_;
    uint64_t scheduled_events_ = 0;
    uint64_t executed_callbacks_ = 0;
};
}
