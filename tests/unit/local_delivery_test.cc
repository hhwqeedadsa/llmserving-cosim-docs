#include "local_delivery.hh"
#include "ub_network_adapter.hh"
#include "ns3/simulator.h"
#include <iostream>
#include <stdexcept>
#include <vector>
using namespace moe_ub;
void Check(bool ok) { if (!ok) throw std::runtime_error("Local delivery invariant failed"); }
void Count(void* p) { ++*static_cast<int*>(p); }
template<class F> void Reject(F f) { try { f(); } catch (const std::invalid_argument&) { return; } throw std::runtime_error("Invalid local event accepted"); }
int main()
{
    try
    {
        ns3::Time::SetResolution(ns3::Time::NS);
        LocalDelivery early({1, "g", 1, 1, 2, 5, 10, 128, "dispatch"});
        LocalDelivery late({2, "g", 2, 2, 5, 2, 11, 128, "combine"});
        UbNetworkAdapter a(2, [](uint64_t) {}), b(5, [](uint64_t) {});
        a.RegisterLocalDeliveries({&early, &late}); b.RegisterLocalDeliveries({&early, &late});
        Reject([&]() { a.RegisterLocalDeliveries({&early}); });
        int sends = 0, receives = 0;
        b.sim_recv(nullptr, 128, 0, 2, 10, nullptr, Count, &receives);
        Reject([&]() { b.sim_recv(nullptr, 128, 0, 2, 10, nullptr, Count, &receives); });
        Reject([&]() { a.sim_send(nullptr, 127, 0, 5, 10, nullptr, Count, &sends); });
        Reject([&]() { a.sim_send(nullptr, 128, 1, 5, 10, nullptr, Count, &sends); });
        ns3::Simulator::Schedule(ns3::NanoSeconds(50), [&]() {
            Check(receives == 0);
            a.sim_send(nullptr, 128, 0, 5, 10, nullptr, Count, &sends);
            Check(sends == 0 && receives == 0 && early.HasPendingEvents());
        });
        ns3::Simulator::Schedule(ns3::NanoSeconds(70), [&]() {
            Check(!early.Active() && sends == 1 && receives == 1);
            b.sim_send(nullptr, 128, 0, 2, 11, nullptr, Count, &sends);
        });
        ns3::Simulator::Schedule(ns3::NanoSeconds(100), [&]() {
            Check(late.Active() && sends == 2 && receives == 1);
            a.sim_recv(nullptr, 128, 0, 5, 11, nullptr, Count, &receives);
            Check(receives == 1);
        });
        ns3::Simulator::Run();
        Check(sends == 2 && receives == 2 && !late.Active() && !late.HasPendingEvents());
        Check(early.Result()["recv_callback_ns"] == 50 && late.Result()["ready_ns"] == 70 && late.Result()["recv_callback_ns"] == 100);
        Reject([&]() { a.sim_send(nullptr, 128, 0, 5, 10, nullptr, Count, &sends); });
        ns3::Simulator::Destroy();
        std::cout << "PASS: local early/late receive, asynchronous exactly-once callbacks, matching and retirement\n";
        return 0;
    }
    catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
