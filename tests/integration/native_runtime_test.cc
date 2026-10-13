#include "ns3/ub-app.h"
#include "ns3/ub-transfer-executor.h"
#include "ns3/ub-ldst-api.h"
#include "ns3/ub-utils.h"
#include <filesystem>
#include <iostream>
#include <map>
#include <set>
#include <stdexcept>
#include <array>
#include <new>

namespace {
using namespace ns3;
void Check(bool value, const char* why) { if (!value) throw std::runtime_error(why); }
struct Range { uint32_t task; uint64_t begin; uint32_t bytes; UbMemOperationType type; uint16_t wire; };
struct Observer {
    std::map<uint64_t, Range> requests, responses;
    std::map<uint32_t, std::map<uint64_t, uint32_t>> ranges;
    std::map<uint32_t, uint64_t> ready;
    std::set<uint16_t> activeIds;
    uint64_t created = 0, responseCount = 0, padding = 0;
    void Created(Ptr<const Packet> packet, uint32_t task, uint64_t begin, uint32_t bytes, UbMemOperationType type) {
        auto copy = packet->Copy();
        UbDatalinkPacketHeader dl; UbCna16NetworkHeader network; UbCompactTransactionHeader ta;
        copy->RemoveHeader(dl); copy->RemoveHeader(network); copy->RemoveHeader(ta);
        auto wire = ta.GetIniTaSsn();
        Check(activeIds.insert(wire).second, "wire ID reused while outstanding");
        Check(requests.emplace(packet->GetUid(), Range{task,begin,bytes,type,wire}).second, "request UID reused");
        ++created;
    }
    void Arrived(uint64_t uid, uint32_t body, UbMemOperationType type) {
        auto r = requests.at(uid);
        Check(r.type == type, "request operation changed");
        Check(body == (type == UbMemOperationType::STORE ? r.bytes : 64), "request body length wrong");
        if (type == UbMemOperationType::STORE) {
            Check(ranges[r.task].emplace(r.begin,r.bytes).second, "duplicate STORE contribution");
            ready[r.task] = Simulator::Now().GetNanoSeconds();
        }
    }
    void Responded(uint64_t request, Ptr<const Packet> packet, UbMemOperationType type) {
        auto r = requests.at(request);
        Check(packet->GetUid() != request && type == r.type, "response identity borrowed from request");
        Check(responses.emplace(packet->GetUid(),r).second, "response UID reused");
        requests.erase(request);
        auto copy = packet->Copy();
        UbDatalinkPacketHeader dl; UbCna16NetworkHeader network; UbCompactAckTransactionHeader ta;
        copy->RemoveHeader(dl); copy->RemoveHeader(network); copy->RemoveHeader(ta);
        if (type == UbMemOperationType::LOAD) {
            Check(copy->GetSize() == 512 && r.bytes <= 512, "LOAD response size wrong");
            padding += copy->GetSize()-r.bytes;
        } else Check(copy->GetSize() == 0, "STORE acknowledgment carries business data");
    }
    void Completed(uint64_t uid, uint32_t task, uint64_t begin, uint32_t bytes, UbMemOperationType type) {
        auto r = responses.at(uid);
        Check(r.task == task && r.begin == begin && r.bytes == bytes && r.type == type, "response attribution wrong");
        Check(activeIds.erase(r.wire) == 1, "response retired ID twice");
        responses.erase(uid);
        if (type == UbMemOperationType::LOAD) {
            Check(ranges[task].emplace(begin,bytes).second, "duplicate LOAD contribution");
            ready[task] = Simulator::Now().GetNanoSeconds();
        }
        ++responseCount;
    }
};
}

int main(int argc, char** argv) {
    try {
        Check(argc == 3 || (argc == 4 && std::string(argv[3]) == "producer_round_robin"),
              "Usage: native-runtime-test UB_CASE NEW_OUTPUT [producer_round_robin]");
        const uint32_t threads = argc == 4 ? 1 : 3;
        // Construct on deliberately dirty storage: serialized defaults must not
        // depend on stack/allocator contents or the presence of an observer.
        for (unsigned char pattern : {0x55, 0xaa}) {
            alignas(UbCna16NetworkHeader) std::array<unsigned char,sizeof(UbCna16NetworkHeader)> memory;
            memory.fill(pattern);
            auto* header = new (memory.data()) UbCna16NetworkHeader;
            auto packet = Create<Packet>(); packet->AddHeader(*header);
            std::array<uint8_t,8> bytes; packet->CopyData(bytes.data(),bytes.size());
            Check(bytes == std::array<uint8_t,8>{}, "CNA16 defaults depend on uninitialized storage");
            header->~UbCna16NetworkHeader();
        }
        namespace fs = std::filesystem;
        auto source = fs::absolute(argv[1]), output = fs::absolute(argv[2]);
        Check(!fs::exists(output), "output must be new");
        fs::create_directories(output); fs::current_path(output);
        for (const auto& file : {"network_attribute.txt", "node.csv", "topology.csv", "routing_table.csv"})
            fs::copy_file(source/file, output/file);
        Time::SetResolution(Time::NS);
        auto utils = utils::UbUtils::Get();
        utils->SetComponentsAttribute("network_attribute.txt");
        for (auto flag : {"UB_TRACE_ENABLE", "UB_TASK_TRACE_ENABLE", "UB_PACKET_TRACE_ENABLE",
                          "UB_PORT_TRACE_ENABLE", "UB_QUEUE_TRACE_ENABLE", "UB_RECORD_PKT_TRACE",
                          "UB_FLOW_CONTROL_TRACE_ENABLE", "UB_CONGESTION_CONTROL_TRACE_ENABLE", "UB_PARSE_TRACE_ENABLE"})
            GlobalValue::Bind(flag, BooleanValue(false));
        Config::SetDefault("ns3::UbLdstInstance::ThreadNum", UintegerValue(threads));
        if (argc == 4) Config::SetDefault("ns3::UbLdstThread::StoreQueuePolicy", StringValue(argv[3]));
        Config::SetDefault("ns3::UbLdstThread::StoreOutstanding", UintegerValue(2));
        Config::SetDefault("ns3::UbLdstThread::LoadOutstanding", UintegerValue(1));
        Config::SetDefault("ns3::UbLdstApi::RequestIdCount", UintegerValue(3));
        Config::SetDefault("ns3::UbLdstApi::UsePacketSpray", BooleanValue(false));
        utils->CreateNode("node.csv"); utils->CreateTopo("topology.csv"); utils->AddRoutingTable("routing_table.csv");
        auto node = NodeList::GetNode(0);
        auto app = CreateObject<UbApp>(); node->AddApplication(app);
        auto instance = node->GetObject<UbLdstInstance>();
        auto api = node->GetObject<UbController>()->GetUbFunction()->GetUbLdstApi();
        auto targetApi = NodeList::GetNode(1)->GetObject<UbController>()->GetUbFunction()->GetUbLdstApi();
        Observer observer;
        Check(api->TraceConnectWithoutContext("RequestCreated", MakeCallback(&Observer::Created,&observer)), "missing request trace");
        Check(api->TraceConnectWithoutContext("ResponseArrived", MakeCallback(&Observer::Completed,&observer)), "missing completion trace");
        Check(targetApi->TraceConnectWithoutContext("RequestArrived", MakeCallback(&Observer::Arrived,&observer)), "missing target trace");
        Check(targetApi->TraceConnectWithoutContext("ResponseCreated", MakeCallback(&Observer::Responded,&observer)), "missing response trace");
        Check(instance->GetThreadCount() == threads, "configured worker count ignored");
        uint32_t completed = 0;
        std::function<void(uint32_t)> submit;
        submit = [&](uint32_t id) {
            UbTrafficGen::RuntimeTask task;
            task.taskId = UINT32_MAX-id; task.sourceNode = 0; task.destNode = 1; task.priority = 7;
            task.dataSize = id < 2 ? 1 : 8193;
            task.op = id % 2 ? UbTrafficGen::RuntimeTaskOp::MEM_LOAD : UbTrafficGen::RuntimeTaskOp::MEM_STORE;
            task.nativeProducer = id % 4;
            app->GetTransferExecutor().Submit(task, [&,task](uint32_t done) {
                Check(done == task.taskId, "completion identity wrong");
                uint64_t cursor = 0;
                for (auto [begin, bytes] : observer.ranges.at(done)) {
                    Check(begin == cursor, "gap or overlap in useful task bytes"); cursor += bytes;
                }
                Check(cursor == task.dataSize, "premature data completion");
                uint64_t now = Simulator::Now().GetNanoSeconds();
                Check(observer.ready.at(done) <= now, "completion before data");
                if (task.op == UbTrafficGen::RuntimeTaskOp::MEM_STORE)
                    Check(observer.ready.at(done) < now, "STORE target arrival conflated with acknowledgment");
                observer.ranges.erase(done); observer.ready.erase(done);
                ++completed;
                // Tasks overlap within each wave, then new waves reuse the same small ID pool.
                if (completed % 2 == 0) {
                    Check(instance->IsIdle() && !api->GetPendingRequestCount() && !api->GetWaitingThreadCount(), "native state retained after wave");
                    Check(app->GetTransferExecutor().GetPendingCount() == 0, "executor retained completed task");
                    for (uint32_t t=0; t<threads; ++t) {
                        UintegerValue store, load;
                        instance->GetLdstThread(t)->GetAttribute("StoreOutstanding", store);
                        instance->GetLdstThread(t)->GetAttribute("LoadOutstanding", load);
                        Check(store.Get() == 2 && load.Get() == 1, "configuration readback changed with usage");
                    }
                    if (completed < 66) { submit(completed); submit(completed+1); }
                }
            });
        };
        Simulator::ScheduleNow([&]() { submit(0); submit(1); });
        Simulator::Stop(MilliSeconds(10)); Simulator::Run();
        Check(completed == 66 && observer.requests.empty() && observer.responses.empty() && observer.activeIds.empty(), "native transfer stalled or leaked attribution");
        Check(observer.created == observer.responseCount && observer.padding > 0 && api->GetPeakPendingRequestCount() == 3, "native resource or byte evidence missing");
        Check(node->GetObject<UbController>()->GetTransportCountForTest() == 0, "native execution unexpectedly created TP");
        std::cout << "PASS native runtime: tasks=" << completed << " requests=" << observer.created
                  << " response_padding_bytes=" << observer.padding << " peak_request_ids=3 threads=" << threads << " final_state=empty\n";
        Simulator::Destroy(); return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; Simulator::Destroy(); return 1; }
}
