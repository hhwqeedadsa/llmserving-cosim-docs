#include "ub_network_adapter.hh"
#include "ns3/simulator.h"

#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
void Check(bool value, const char* message)
{
    if (!value)
    {
        throw std::runtime_error(message);
    }
}

template <class Error, class F>
void Reject(F call, const char* message)
{
    try
    {
        call();
    }
    catch (const Error&)
    {
        return;
    }
    throw std::runtime_error(message);
}

struct Context
{
    moe_ub::UbNetworkAdapter* adapter;
    std::vector<int64_t>* times;
};

void Record(void* data)
{
    auto& context = *static_cast<Context*>(data);
    const auto time = context.adapter->sim_get_time();
    Check(time.time_res == AstraSim::NS, "Returned unit is not NS");
    Check(time.time_val == ns3::Simulator::Now().GetNanoSeconds(), "Clock source differs");
    context.times->push_back(static_cast<int64_t>(time.time_val));
}
}

int main(int argc, char** argv)
{
    try
    {
        // ns-3 permits SetResolution only once, before running any events.
        // Exercise the incompatible resolution in its own process.
        if (argc == 2 && std::string(argv[1]) == "--wrong-resolution")
        {
            ns3::Time::SetResolution(ns3::Time::PS);
            Reject<std::logic_error>(
                []() { moe_ub::UbNetworkAdapter wrong(0, [](uint64_t) {}); },
                "Wrong ns-3 resolution accepted");
            std::cout << "PASS: incompatible ns-3 resolution rejected\n";
            return 0;
        }
        Check(argc == 1, "Usage: cosim-time-test [--wrong-resolution]");
        ns3::Time::SetResolution(ns3::Time::NS);
        std::vector<int64_t> times;
        std::vector<uint64_t> finished;
        moe_ub::UbNetworkAdapter adapter(0, [&](uint64_t now) { finished.push_back(now); });
        Context context{&adapter, &times};
        auto schedule = [&](long double delay) {
            adapter.sim_schedule({AstraSim::NS, delay}, Record, &context);
        };
        Check(adapter.sim_get_time().time_val == 0, "Initial time differs");
        schedule(0);
        schedule(0.125L);
        schedule(1.125L);
        Check(times.empty(), "Zero delay executed synchronously");
        for (auto delay : {-1.0L, std::numeric_limits<long double>::infinity(),
                           std::numeric_limits<long double>::quiet_NaN()})
        {
            Reject<std::invalid_argument>([&]() { schedule(delay); }, "Invalid delay accepted");
        }
        for (auto unit : {AstraSim::SE, AstraSim::MS, AstraSim::US, AstraSim::FS})
        {
            Reject<std::invalid_argument>(
                [&]() { adapter.sim_schedule({unit, 1}, Record, &context); },
                "Non-NS unit accepted");
        }
        Reject<std::invalid_argument>(
            [&]() { adapter.sim_schedule({AstraSim::NS, 0}, nullptr, nullptr); },
            "Null callback accepted");
        const long double max_ns = std::numeric_limits<int64_t>::max();
        Reject<std::overflow_error>([&]() { schedule(max_ns + 1); }, "Overflow accepted");
        Reject<std::logic_error>(
            [&]() { adapter.sim_send(nullptr, 1, AstraSim::UINT8, 1, 0, nullptr,
                                     Record, &context); }, "SEND unexpectedly implemented");
        Reject<std::logic_error>(
            [&]() { adapter.sim_recv(nullptr, 1, AstraSim::UINT8, 1, 0, nullptr,
                                     Record, &context); }, "RECV unexpectedly implemented");
        ns3::Simulator::Schedule(ns3::NanoSeconds(10), [&]() {
            Check(adapter.sim_get_time().time_val == 10, "Absolute time is wrong");
            schedule(5);
            adapter.sim_notify_finished();
            Reject<std::overflow_error>([&]() { schedule(max_ns - 9); },
                                         "Now plus delay overflow accepted");
        });
        ns3::Simulator::Run();
        Check(times == std::vector<int64_t>{0, 1, 2, 15}, "Rounding or relative scheduling failed");
        Check(finished == std::vector<uint64_t>{10}, "Completion time differs");
        Check(adapter.ScheduledEvents() == 4 && adapter.ExecutedCallbacks() == 4,
              "Callback count differs or rejected input scheduled work");
        ns3::Simulator::Destroy();
        std::cout << "PASS: zero is asynchronous; positive fractions ceil; absolute time and callback identity\n";
        std::cout << "PASS: completion notification does not stop future events; invalid calls rejected\n";

        times.clear();
        moe_ub::UbNetworkAdapter boundary(0, [](uint64_t) {});
        Context boundary_context{&boundary, &times};
        boundary.sim_schedule({AstraSim::NS, max_ns}, Record, &boundary_context);
        ns3::Simulator::Run();
        Check(times == std::vector<int64_t>{std::numeric_limits<int64_t>::max()},
              "Largest signed nanosecond value lost precision");
        Reject<std::overflow_error>(
            [&]() { boundary.sim_schedule({AstraSim::NS, 1}, Record, &boundary_context); },
            "Scheduling beyond the final nanosecond was accepted");
        ns3::Simulator::Destroy();
        std::cout << "PASS: signed 64-bit time boundary is exact and overflow is rejected\n";

        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << '\n';
        ns3::Simulator::Destroy();
        return 1;
    }
}
