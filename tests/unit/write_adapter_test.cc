#include "ub_network_adapter.hh"
#include "ub_write_transfer.hh"
#include "ns3/simulator.h"
#include "ns3/ub-app.h"

#include <iostream>
#include <stdexcept>

namespace
{
void Check(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}
template <class Exception, class F> void Reject(F action)
{
    try { action(); }
    catch (const Exception&) { return; }
    throw std::runtime_error("Invalid transfer call was accepted");
}
void RecordCompletion(void* data) { ++*static_cast<unsigned*>(data); }
}

int main()
{
    try
    {
        ns3::Time::SetResolution(ns3::Time::NS);
        auto source = ns3::CreateObject<ns3::Node>();
        auto target = ns3::CreateObject<ns3::Node>();
        auto app = ns3::CreateObject<ns3::UbApp>();
        source->AddApplication(app);
        const moe_ub::WriteDescriptor descriptor{uint64_t{1} << 40, 9, "graph", 1, 2, 0, 1, 17, 4096};
        for (int field = 0; field < 2; ++field)
        {
            auto invalid = descriptor;
            (field == 0 ? invalid.bytes : invalid.task_id) = uint64_t{1} << 32;
            Reject<std::overflow_error>([&]() { moe_ub::UbWriteTransfer transfer(invalid, app); });
        }
        auto invalid = descriptor;
        invalid.dst_rank = 2;
        Reject<std::invalid_argument>([&]() { moe_ub::UbWriteTransfer transfer(invalid, app); });
        invalid = descriptor;
        invalid.bytes = 0;
        Reject<std::invalid_argument>([&]() { moe_ub::UbWriteTransfer transfer(invalid, app); });
        {
            moe_ub::UbWriteTransfer transfer(descriptor, app);
            moe_ub::UbNetworkAdapter sender(0, [](uint64_t) {}, &transfer);
            moe_ub::UbNetworkAdapter receiver(1, [](uint64_t) {}, &transfer);
            unsigned calls = 0;
            auto send = [&](uint64_t size, int type, int dst, int tag, auto callback) {
                AstraSim::sim_request stack_request{};
                sender.sim_send(nullptr, size, type, dst, tag, &stack_request, callback, &calls);
            };
            Reject<std::invalid_argument>([&]() { send(4096, -1, 1, 17, RecordCompletion); });
            Reject<std::invalid_argument>([&]() { send(4095, AstraSim::UINT8, 1, 17, RecordCompletion); });
            Reject<std::invalid_argument>([&]() { send(4096, AstraSim::UINT8, 0, 17, RecordCompletion); });
            Reject<std::invalid_argument>([&]() { send(4096, AstraSim::UINT8, 1, 18, RecordCompletion); });
            Reject<std::invalid_argument>([&]() { send(4096, AstraSim::UINT8, 1, 17,
                                                      static_cast<void (*)(void*)>(nullptr)); });
            Reject<std::overflow_error>([&]() { send(uint64_t{1} << 32, AstraSim::UINT8, 1, 17, RecordCompletion); });
            Reject<std::invalid_argument>([&]() {
                receiver.sim_recv(nullptr, 4095, AstraSim::UINT8, 0, 17, nullptr, RecordCompletion, &calls);
            });
            Check(transfer.SubmittedTasks() == 0, "Rejected input submitted traffic");
            receiver.sim_recv(nullptr, 4096, AstraSim::UINT8, 0, 17, nullptr, RecordCompletion, &calls);
            Reject<std::logic_error>([&]() {
                receiver.sim_recv(nullptr, 4096, AstraSim::UINT8, 0, 17, nullptr, RecordCompletion, &calls);
            });
            Check(transfer.ReceiverWaiting() && transfer.SubmittedTasks() == 0,
                  "RECV did not exclusively register a wait");
            send(4096, AstraSim::UINT8, 1, 17, RecordCompletion);
            Reject<std::logic_error>([&]() { send(4096, AstraSim::UINT8, 1, 17, RecordCompletion); });
            Check(calls == 0 && transfer.SubmittedTasks() == 0, "SEND was synchronous");
            // Destruction before the first event cancels the unsubmitted send and unconsumed wait.
        }
        ns3::Simulator::Run();
        ns3::Simulator::Destroy();
        std::cout << "PASS: descriptor bounds, matching, datatype, duplicate calls, async submission and cancellation\n";
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << '\n';
        ns3::Simulator::Destroy();
        return 1;
    }
}
