#pragma once
#include "ns3/simulator.h"
#include <algorithm>
#include <functional>
#include <optional>
#include <stdexcept>
#include <vector>

namespace moe_ub {
// One factual event, with asynchronous waits including late registration.
// An event without waiters does not add anything to the simulator queue.
class CompletionEvent {
  public:
    ~CompletionEvent() { for (auto& event : callbacks_) event.Cancel(); }
    void Signal() {
        if (time_) throw std::logic_error("Repeated completion event");
        time_ = ns3::Simulator::Now().GetNanoSeconds();
        for (auto& waiter : waiters_) Queue(std::move(waiter));
        waiters_.clear();
    }
    void Wait(std::function<void()> waiter) {
        if (!waiter) throw std::invalid_argument("Empty completion event waiter");
        if (time_) Queue(std::move(waiter));
        else waiters_.push_back(std::move(waiter));
    }
    bool Pending() const {
        return !waiters_.empty() || std::any_of(callbacks_.begin(), callbacks_.end(),
            [](const auto& event) { return event.IsPending(); });
    }
    std::optional<uint64_t> Time() const { return time_; }
  private:
    void Queue(std::function<void()> callback) {
        callbacks_.push_back(ns3::Simulator::ScheduleNow(std::move(callback)));
    }
    std::optional<uint64_t> time_;
    std::vector<std::function<void()>> waiters_;
    std::vector<ns3::EventId> callbacks_;
};
}
