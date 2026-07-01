// SPDX-License-Identifier: GPL-2.0-only
#include "ub-app.h"
#include "ns3/log.h"
#include "ns3/simulator.h"
#include "ns3/uinteger.h"
#include "ns3/callback.h"
#include "ns3/ub-datatype.h"
#include "ns3/ub-function.h"
#include "ub-traffic-gen.h"
#include "ns3/ub-routing-process.h"
#include "ns3/ub-port.h"
#include "ns3/ub-utils.h"

namespace ns3 {

NS_LOG_COMPONENT_DEFINE("UbApp");

NS_OBJECT_ENSURE_REGISTERED(UbApp);
TypeId UbApp::GetTypeId(void)
{
    static TypeId tid =
        TypeId("ns3::UbApp")
            .SetParent<Application>()
            .SetGroupName("UnifiedBus")
            .AddConstructor<UbApp>()
            .AddAttribute("EnableMultiPath",
                          "Enable multi-path transport: create TPs on multiple source ports toward the same destination.",
                          BooleanValue(false),
                          MakeBooleanAccessor(&UbApp::m_multiPathEnable),
                          MakeBooleanChecker())
            .AddAttribute("UseShortestPaths",
                          "If true, only create TPs on source ports that belong to shortest paths.",
                          BooleanValue(true),
                          MakeBooleanAccessor(&UbApp::m_useShortestPaths),
                          MakeBooleanChecker())
            .AddTraceSource("MemTaskStartsNotify",
                            "MEM Task Starts, taskId",
                            MakeTraceSourceAccessor(&UbApp::m_traceMemTaskStartsNotify),
                            "ns3::UbApp::MemTaskStartsNotify")
            .AddTraceSource("MemTaskCompletesNotify",
                            "MEM Task Completes, taskId",
                            MakeTraceSourceAccessor(&UbApp::m_traceMemTaskCompletesNotify),
                            "ns3::UbApp::MemTaskCompletesNotify")
            .AddTraceSource("WqeTaskStartsNotify",
                            "WQE Task Starts, taskId",
                            MakeTraceSourceAccessor(&UbApp::m_traceWqeTaskStartsNotify),
                            "ns3::UbApp::WqeTaskStartsNotify")
            .AddTraceSource("WqeTaskCompletesNotify",
                            "WQE Task Completes, taskId",
                            MakeTraceSourceAccessor(&UbApp::m_traceWqeTaskCompletesNotify),
                            "ns3::UbApp::WqeTaskCompletesNotify");
    return tid;
}

UbApp::UbApp()
{
}

UbApp::~UbApp()
{
}

void UbApp::SetFinishCallback(Callback<void, uint32_t, uint32_t> cb, Ptr<UbJetty> jetty)
{
    jetty->SetClientCallback(cb);
}

void UbApp::SetFinishCallback(Callback<void, uint32_t> cb, Ptr<UbLdstInstance> ubLdstInstance)
{
    ubLdstInstance->SetClientCallback(cb);
}

void UbApp::DoDispose(void)
{
    Application::DoDispose();
}

void UbApp::SendTraffic(TrafficRecord record)
{
    NS_ABORT_MSG_IF(record.priority < 0 ||
                        static_cast<uint32_t>(record.priority) > UB_PRIORITY_MAX,
                    "Invalid priority field in traffic.csv; valid range is 0.."
                        << static_cast<uint32_t>(UB_PRIORITY_MAX));

    UbTrafficGen::RuntimeTask task;
    task.taskId = static_cast<uint32_t>(record.taskId);
    task.sourceNode = static_cast<uint32_t>(record.sourceNode);
    task.destNode = static_cast<uint32_t>(record.destNode);
    task.dataSize = static_cast<uint32_t>(record.dataSize);
    task.priority = static_cast<uint8_t>(record.priority);
    task.delay = record.delay.empty() ? Time(0) : Time(record.delay);
    if (record.opType == "URMA_WRITE") {
        task.op = UbTrafficGen::RuntimeTaskOp::URMA_WRITE;
    } else if (record.opType == "URMA_READ") {
        task.op = UbTrafficGen::RuntimeTaskOp::URMA_READ;
    } else if (record.opType == "MEM_STORE") {
        task.op = UbTrafficGen::RuntimeTaskOp::MEM_STORE;
    } else if (record.opType == "MEM_LOAD") {
        task.op = UbTrafficGen::RuntimeTaskOp::MEM_LOAD;
    } else {
        NS_ASSERT_MSG(0, "TaOpcode Not Exist");
    }
    SendTraffic(task);
}

void UbApp::SendTraffic(UbTrafficGen::RuntimeTask task)
{
    if (task.priority == 0) {
        NS_LOG_DEBUG("Task uses the highest priority, not recommended.");
    }

    if (task.op == UbTrafficGen::RuntimeTaskOp::MEM_STORE ||
        task.op == UbTrafficGen::RuntimeTaskOp::MEM_LOAD) {
        // 内存语义发送
        UbMemOperationType type = UbMemOperationType::STORE;
        if (task.op == UbTrafficGen::RuntimeTaskOp::MEM_LOAD) {
            type = UbMemOperationType::LOAD;
        }
        auto ldstInstance = GetNode()->GetObject<UbLdstInstance>();
        SetFinishCallback(MakeCallback(&UbApp::OnMemTaskCompleted, this), ldstInstance);
        NS_LOG_INFO("MEM Task Starts, taskId: " << task.taskId);
        MemTaskStartsNotify(GetNode()->GetId(), task.taskId);
        std::vector<uint32_t> threadIds = {0, 1};
        ldstInstance->HandleLdstTask(task.sourceNode,
                                     task.destNode,
                                     task.dataSize,
                                     task.taskId,
                                     task.priority,
                                     type,
                                     threadIds,
                                     0);
    } else if (task.op == UbTrafficGen::RuntimeTaskOp::URMA_WRITE ||
               task.op == UbTrafficGen::RuntimeTaskOp::URMA_READ) {
        // URMA发送
        Ptr<UbFunction> ubFunc = GetNode()->GetObject<UbController>()->GetUbFunction();
        Ptr<UbTransaction> ubTa = GetNode()->GetObject<UbController>()->GetUbTransaction();
        bool jettyExist = ubFunc->IsJettyExists(m_jettyNum);
        if (jettyExist) {
            NS_LOG_ERROR("Jetty already exists");
            return;
        }
        ubFunc->CreateJetty(task.sourceNode, task.destNode, m_jettyNum);
        vector<uint32_t> tpns = GetNode()->GetObject<UbController>()->GetTpConnManager()->GetTpns(
            m_getTpnRule, m_useShortestPaths, m_multiPathEnable, task.sourceNode,
            task.destNode, UINT32_MAX, UINT32_MAX, task.priority);
        bool bindRst = ubTa->JettyBindTp(task.sourceNode, task.destNode, m_jettyNum, m_multiPathEnable, tpns);
        if (bindRst) {
            Ptr<UbJetty> curr_jetty = ubFunc->GetJetty(m_jettyNum);
            SetFinishCallback(MakeCallback(&UbApp::OnTaskCompleted, this), curr_jetty);
            NS_LOG_INFO("WQE Starts, jettyNum: " << m_jettyNum << " taskId: " << task.taskId);
            NS_LOG_INFO("Src: " << task.sourceNode << " Dst: " << task.destNode);
            WqeTaskStartsNotify(GetNode()->GetId(), m_jettyNum, task.taskId);
            NS_LOG_INFO("[APPLICATION INFO] taskId: " << task.taskId << ",start time:" <<
                Simulator::Now().GetNanoSeconds() << "ns");
            TaOpcode type = task.op == UbTrafficGen::RuntimeTaskOp::URMA_READ
                                ? TaOpcode::TA_OPCODE_READ
                                : TaOpcode::TA_OPCODE_WRITE;
            Ptr<UbWqe> wqe =
                ubFunc->CreateWqe(task.sourceNode, task.destNode, task.dataSize, task.taskId, type);
            ubFunc->PushWqeToJetty(wqe, m_jettyNum);
        }
        m_jettyNum++; // m_jettyNum 在client里是唯一的，不重复的
    } else {
            NS_ASSERT_MSG(0, "TaOpcode Not Exist");
    }
}

void UbApp::OnTaskCompleted(uint32_t taskId, uint32_t jettyNum)
{
    NS_LOG_FUNCTION(this << taskId);
    NS_LOG_INFO("WQE Completes, jettyNum: " << jettyNum << " taskId: " << taskId);
    WqeTaskCompletesNotify(GetNode()->GetId(), jettyNum, taskId);
    NS_LOG_INFO("[APPLICATION INFO] taskId: " << taskId << ",finish time:" << Simulator::Now().GetNanoSeconds() << "ns");
    // 删除无用tp
    auto cleanup = UbTrafficGen::Get()->GetTaskCleanupInfoById(taskId);
    GetNode()->GetObject<UbController>()->GetTpConnManager()->RemoveUselessTps(
        jettyNum, cleanup.sourceNode, cleanup.destNode, cleanup.priority);
    UbTrafficGen::Get()->OnTaskCompleted(taskId);
}

void UbApp::OnTestTaskCompleted(uint32_t taskId, uint32_t jettyNum)
{
    NS_LOG_FUNCTION(this << taskId);
    NS_LOG_INFO("WQE Completes, jettyNum:" << jettyNum << " taskId:" << taskId);
    WqeTaskCompletesNotify(GetNode()->GetId(), jettyNum, taskId);
    NS_LOG_INFO("[APPLICATION INFO] taskId:" << taskId << ",finish time:" << Simulator::Now().GetNanoSeconds() << "ns");
    Ptr<UbFunction> ubFunc = GetNode()->GetObject<UbController>()->GetUbFunction();
    UbTrafficGen::Get()->OnTaskCompleted(taskId);
}

void UbApp::OnMemTaskCompleted(uint32_t taskId)
{
    NS_LOG_FUNCTION(this << taskId);
    NS_LOG_INFO("MEM Task Completes, taskId: " << taskId);
    MemTaskCompletesNotify(GetNode()->GetId(), taskId);
    NS_LOG_INFO("[APPLICATION INFO] taskId: " << taskId << ",finish time:" << Simulator::Now().GetNanoSeconds() << "ns");
    UbTrafficGen::Get()->OnTaskCompleted(taskId);
}

void UbApp::MemTaskStartsNotify(uint32_t nodeId, uint32_t taskId)
{
    m_traceMemTaskStartsNotify(nodeId, taskId);
}

void UbApp::MemTaskCompletesNotify(uint32_t nodeId, uint32_t taskId)
{
    m_traceMemTaskCompletesNotify(nodeId, taskId);
}

void UbApp::WqeTaskStartsNotify(uint32_t nodeId, uint32_t jettyNum, uint32_t taskId)
{
    m_traceWqeTaskStartsNotify(nodeId, jettyNum, taskId);
}

void UbApp::WqeTaskCompletesNotify(uint32_t nodeId, uint32_t jettyNum, uint32_t taskId)
{
    m_traceWqeTaskCompletesNotify(nodeId, jettyNum, taskId);
}

} // namespace ns3
