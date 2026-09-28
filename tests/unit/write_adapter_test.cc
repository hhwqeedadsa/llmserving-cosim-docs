#include "ub_network_adapter.hh"
#include "ub_write_transfer.hh"
#include "ns3/simulator.h"
#include "ns3/ub-app.h"

#include <iostream>
#include <algorithm>
#include <vector>
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
struct CallbackTimes { std::vector<uint64_t> values; };
void RecordTime(void* arg)
{
    static_cast<CallbackTimes*>(arg)->values.push_back(ns3::Simulator::Now().GetNanoSeconds());
}
void CompletionCase(uint64_t receive_start, uint64_t transaction_time)
{
    CallbackTimes sends, receives;
    unsigned retired = 0;
    moe_ub::WriteCompletion state("graph=original transfer=42 send_node=1 recv_node=2", 100,
                                  [&]() { ++retired; });
    state.RegisterSend(RecordTime, &sends);
    ns3::Simulator::Schedule(ns3::NanoSeconds(receive_start), [&]() {
        state.RegisterReceive(RecordTime, &receives);
        Check(receives.values.empty(), "RECV callback reentered registration");
    });
    ns3::Simulator::Schedule(ns3::NanoSeconds(10), [&]() {
        state.WriteCommitted(7, 1, 40); // Out-of-order TA units are still unique data.
        state.WriteCommitted(7, 1, 40);
        Reject<std::logic_error>([&]() { state.WriteCommitted(7, 1, 41); });
        Reject<std::logic_error>([&]() { state.WriteCommitted(7, 2, 61); });
        Reject<std::logic_error>([&]() { state.WriteCommitted(7, 2, 0); });
        Check(state.CommittedBytes() == 40 && !state.DataReadyNs(), "Partial/duplicate write completed RECV");
    });
    ns3::Simulator::Schedule(ns3::NanoSeconds(20), [&]() {
        state.WriteCommitted(7, 0, 60);
        state.WriteCommitted(7, 0, 60);
        Check(receives.values.empty(), "Data-ready callback ran inline");
    });
    ns3::Simulator::Schedule(ns3::NanoSeconds(transaction_time), [&]() {
        state.TransactionDone();
        Check(sends.values.empty(), "SEND callback ran inline");
        Reject<std::logic_error>([&]() { state.TransactionDone(); });
    });
    ns3::Simulator::Schedule(ns3::NanoSeconds(35), [&]() {
        Check(state.Active() == (receive_start > 35), "Late RECV state did not survive transaction completion");
    });
    ns3::Simulator::Run();
    Check(sends.values == std::vector<uint64_t>{transaction_time}, "SEND waited on wrong event");
    Check(receives.values == std::vector<uint64_t>{std::max(receive_start, uint64_t{20})},
          "RECV waited on source completion or traveled backwards in time");
    Check(state.DataReadyNs() == 20 && state.TransactionDoneNs() == transaction_time &&
          !state.Active() && retired == 1 && state.CommittedBytes() == 100 &&
          state.CommittedSegments() == 2 && !state.ReceiverWaiting(), "Final record or cleanup is wrong");
    // A new graph may reuse local node IDs, but old events must identify the old transfer.
    moe_ub::WriteCompletion next("graph=next transfer=43 send_node=1 recv_node=2", 100, {});
    try { state.WriteCommitted(7, 0, 60); throw std::runtime_error("Stale write accepted"); }
    catch (const std::logic_error& error) {
        Check(std::string(error.what()).find("graph=original transfer=42") != std::string::npos,
              "Stale diagnostic lost graph/transfer identity");
    }
    Reject<std::logic_error>([&]() { state.RegisterReceive(RecordTime, &receives); });
    Reject<std::logic_error>([&]() { state.RegisterSend(RecordTime, &sends); });
    Reject<std::logic_error>([&]() { state.TransactionDone(); });
    Check(next.CommittedBytes() == 0 && next.Active(), "Old event changed new graph");
    ns3::Simulator::Destroy();
}

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
            auto other = descriptor;
            ++other.transfer_id; ++other.task_id; ++other.tag;
            moe_ub::UbWriteTransfer first(descriptor, app), second(other, app);
            std::vector<moe_ub::UbWriteTransfer*> registry{&first, &second};
            moe_ub::UbNetworkAdapter sender(0, [](uint64_t) {}, registry);
            moe_ub::UbNetworkAdapter receiver(1, [](uint64_t) {}, registry);
            Reject<std::invalid_argument>([&]() {
                moe_ub::UbNetworkAdapter duplicate(0, [](uint64_t) {},
                    std::vector<moe_ub::UbWriteTransfer*>{&first, &first});
            });
            unsigned callbacks = 0;
            receiver.sim_recv(nullptr, other.bytes, AstraSim::UINT8, 0, other.tag, nullptr,
                              RecordCompletion, &callbacks);
            Check(second.ReceiverWaiting() && !first.ReceiverWaiting(), "Multi-transfer receive matched the wrong tag");
            sender.sim_send(nullptr, descriptor.bytes, AstraSim::UINT8, 1, descriptor.tag, nullptr,
                            RecordCompletion, &callbacks);
            sender.sim_send(nullptr, other.bytes, AstraSim::UINT8, 1, other.tag, nullptr,
                            RecordCompletion, &callbacks);
            Check(callbacks == 0 && first.SubmittedTasks() == 0 && second.SubmittedTasks() == 0,
                  "Multi-transfer SEND ran before returning");
            // Scope exit cancels these unsubmitted tasks; this is a matching unit test.
        }
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
        CompletionCase(0, 30);  // RECV registered before data.
        CompletionCase(15, 30); // SEND and partial data before RECV.
        CompletionCase(50, 30); // Late RECV after both real milestones.
        CompletionCase(0, 5);   // Independent event ordering, deliberately synthetic.
        unsigned cancelled = 0;
        {
            moe_ub::WriteCompletion abandoned("graph=cancel transfer=44", 1, {});
            abandoned.RegisterSend(RecordCompletion, &cancelled);
            abandoned.RegisterReceive(RecordCompletion, &cancelled);
            abandoned.WriteCommitted(0, 0, 1);
            abandoned.TransactionDone();
        }
        ns3::Simulator::Run();
        Check(cancelled == 0, "Destroyed completion state delivered queued callbacks");
        ns3::Simulator::Destroy();
        std::cout << "PASS: independent completion times, partial/duplicate writes, late RECV, retirement, stale identity;\n";
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
