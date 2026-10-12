// SPDX-License-Identifier: GPL-2.0-only
#include "ns3/ub-ldst-thread.h"
#include "ns3/ub-ldst-api.h"

#include "ns3/ub-queue-manager.h"
#include "ns3/ub-routing-process.h"

#include "ns3/log.h"
#include "ns3/simulator.h"
#include "ns3/ub-controller.h"
#include "ns3/ub-datatype.h"
#include <stdexcept>

namespace ns3 {
NS_LOG_COMPONENT_DEFINE("UbLdstThread");

NS_OBJECT_ENSURE_REGISTERED(UbLdstThread);

TypeId UbLdstThread::GetTypeId(void)
{
    static TypeId tid = TypeId("ns3::UbLdstThread")
                            .SetParent<Object>()
                            .SetGroupName("UnifiedBus")
                            .AddAttribute("StoreOutstanding",
                                          "Maximum number of outstanding STORE requests this thread may issue.",
                                          UintegerValue(64),
                                          MakeUintegerAccessor(&UbLdstThread::m_storeOutstanding),
                                          MakeUintegerChecker<uint32_t>(1, 65536))
                            .AddAttribute("LoadOutstanding",
                                          "Maximum number of outstanding LOAD requests this thread may issue.",
                                          UintegerValue(64),
                                          MakeUintegerAccessor(&UbLdstThread::m_loadOutstanding),
                                          MakeUintegerChecker<uint32_t>(1, 65536))
                            .AddAttribute("LoadResponseSize",
                                          "LOAD response payload size in bytes; rounded up to 64B * 2^n at Init().",
                                          UintegerValue(512),
                                          MakeUintegerAccessor(&UbLdstThread::m_loadRspSize),
                                          MakeUintegerChecker<uint32_t>(0, 8192))
                            .AddAttribute("StoreRequestSize",
                                          "STORE request payload size in bytes; rounded up to 64B * 2^n at Init().",
                                          UintegerValue(512),
                                          MakeUintegerAccessor(&UbLdstThread::m_storeReqSize),
                                          MakeUintegerChecker<uint32_t>(0, 8192))
                            .AddTraceSource("QueueState", "Passive queue state at native issue/block/release branches.",
                                            MakeTraceSourceAccessor(&UbLdstThread::m_queueState),
                                            "ns3::UbLdstThread::QueueState")
                            .AddAttribute("LoadRequestSize",
                                          "Payload size (bytes) for each LOAD request.",
                                          UintegerValue(64),
                                          MakeUintegerAccessor(&UbLdstThread::m_loadReqSize),
                                          MakeUintegerChecker<uint32_t>());

    return tid;
}

UbLdstThread::UbLdstThread()
{
}

UbLdstThread::~UbLdstThread()
{
}

uint32_t UbLdstThread::CalcLength(uint32_t size)
{
    // size = 64B * (2 ^ length)
    if (size <= 64) return 0;
    size = (size - 1) / 64;
    int length = 0;
    while (size  > 0) {
        size = size >> 1;
        length++;
    }
    return length;
}

void UbLdstThread::Init()
{
    m_loadRspLength = CalcLength(m_loadRspSize);
    m_storeReqLength = CalcLength(m_storeReqSize);
    // Reset Size
    m_loadRspSize = 64 * (1 << m_loadRspLength);
    m_storeReqSize = 64 * (1 << m_storeReqLength);
}

void UbLdstThread::SetLoadReqSize(uint32_t size)
{
    m_loadReqSize = size;
}

void UbLdstThread::SetStoreReqLength(uint32_t length)
{
    m_storeReqLength = length;
    m_storeReqSize = 64 * (1 << m_storeReqLength);
}

void UbLdstThread::SetLoadRspLength(uint32_t length)
{
    m_loadRspLength = length;
    m_loadRspSize = 64 * (1 << m_loadRspLength);
}

void UbLdstThread::PushTaskSegment(Ptr<UbLdstTaskSegment> taskSegment)
{
    if (taskSegment->GetType() == UbMemOperationType::STORE) {
        taskSegment->SetPacketInfo(64 * (1 << m_storeReqLength), m_storeReqLength);
    } else if (taskSegment->GetType() == UbMemOperationType::LOAD) {
        taskSegment->SetPacketInfo(m_loadReqSize, m_loadRspLength);
    } else {
        NS_ASSERT_MSG(0, "task type is wrong");
    }
    auto taskSegmentId = taskSegment->GetTaskSegmentId();
    if (!taskSegment->GetPsnSize() ||
        !m_waitingAckNum.emplace(taskSegmentId, taskSegment->GetPsnSize()).second)
        throw std::logic_error("Empty or repeated LDST segment");
    NS_LOG_DEBUG("[UbLdstThread PushTaskSegment] m_waitingAckNum[" << taskSegmentId << "]: " <<
                 m_waitingAckNum[taskSegmentId]);
    if (taskSegment->GetType() == UbMemOperationType::LOAD) {
        m_loadQueue.push(taskSegment);
        ScheduleWork();
    } else if (taskSegment->GetType() == UbMemOperationType::STORE) {
        m_storeQueue.push(taskSegment);
        ScheduleWork();
    }
    ObserveQueue("task_queued", taskSegment);
}

std::vector<Ptr<const UbLdstTaskSegment>> UbLdstThread::GetQueueSnapshot(bool load) const
{
    auto queue = load ? m_loadQueue : m_storeQueue;
    std::vector<Ptr<const UbLdstTaskSegment>> result;
    while (!queue.empty()) { result.push_back(queue.front()); queue.pop(); }
    return result;
}

void UbLdstThread::ObserveQueue(const std::string& event, Ptr<UbLdstTaskSegment> segment,
                               uint64_t offset, uint32_t bytes)
{
    if (!m_queueState.IsEmpty()) m_queueState(this, event, segment, offset, bytes);
}

void UbLdstThread::HandleLoadTask()
{
    NS_LOG_DEBUG("[UbLdstThread HandleLoadTask]");
    while (m_loadInFlight < m_loadOutstanding) {
        Ptr<UbLdstTaskSegment> taskSegment = nullptr;
        while (!m_loadQueue.empty()) {
            taskSegment = m_loadQueue.front();
            if (taskSegment->PeekNextDataSize() == 0) {
                m_loadQueue.pop();
                taskSegment = nullptr;
            } else {
                break;
            }
        }
        if (taskSegment == nullptr) {
            return;
        }
        auto node = NodeList::GetNode(m_nodeId);
        auto ldstapi = node->GetObject<UbController>()->GetUbFunction()->GetUbLdstApi();
        const auto offset = taskSegment->GetTaskOffset() + taskSegment->GetSize() - taskSegment->GetBytesLeft();
        const auto bytes = taskSegment->PeekNextDataSize();
        if (!ldstapi->LdstProcess(taskSegment)) {
            ObserveQueue("request_pool_blocked", taskSegment, offset, bytes);
            return;
        }
        ++m_loadInFlight;
        if (taskSegment->IsSentCompleted()) m_loadQueue.pop();
        ObserveQueue("request_issued", taskSegment, offset, bytes);
    }
    if (!m_loadQueue.empty()) ObserveQueue("outstanding_blocked", m_loadQueue.front());
}

void UbLdstThread::HandleStoreTask()
{
    NS_LOG_DEBUG("[UbLdstThread HandleStoreTask]");
    while (m_storeInFlight < m_storeOutstanding) {
        Ptr<UbLdstTaskSegment> taskSegment = nullptr;
        while (!m_storeQueue.empty()) {
            taskSegment = m_storeQueue.front();
            if (taskSegment->PeekNextDataSize() == 0) {
                m_storeQueue.pop();
                taskSegment = nullptr;
            } else {
                break;
            }
        }
        if (taskSegment == nullptr) {
            return;
        }
        auto node = NodeList::GetNode(m_nodeId);
        auto ldstapi = node->GetObject<UbController>()->GetUbFunction()->GetUbLdstApi();
        const auto offset = taskSegment->GetTaskOffset() + taskSegment->GetSize() - taskSegment->GetBytesLeft();
        const auto bytes = taskSegment->PeekNextDataSize();
        if (!ldstapi->LdstProcess(taskSegment)) {
            ObserveQueue("request_pool_blocked", taskSegment, offset, bytes);
            return;
        }
        ++m_storeInFlight;
        if (taskSegment->IsSentCompleted()) m_storeQueue.pop();
        ObserveQueue("request_issued", taskSegment, offset, bytes);
    }
    if (!m_storeQueue.empty()) ObserveQueue("outstanding_blocked", m_storeQueue.front());
}

void UbLdstThread::SetNode(uint32_t nodeId)
{
    m_nodeId = nodeId;
}

void UbLdstThread::SetThreadId(uint32_t threadId)
{
    m_threadId = threadId;
}

void UbLdstThread::UpdateTask(Ptr<UbLdstTaskSegment> taskSegment, uint64_t offset, uint32_t bytes)
{
    auto id = taskSegment->GetTaskSegmentId();
    auto& waiting = m_waitingAckNum.at(id);
    auto& inFlight = taskSegment->GetType() == UbMemOperationType::LOAD
                         ? m_loadInFlight : m_storeInFlight;
    if (!waiting || !inFlight) throw std::logic_error("Unexpected LDST response");
    --inFlight;
    bool completed = --waiting == 0;
    if (completed) m_waitingAckNum.erase(id);
    ObserveQueue("response_released", taskSegment, offset, bytes);
    ScheduleWork();
    // Resource state is retired before callbacks can submit another task.
    if (completed)
        NodeList::GetNode(m_nodeId)->GetObject<UbLdstInstance>()->
            OnTaskSegmentCompleted(taskSegment->GetTaskId());
}

void UbLdstThread::ScheduleWork()
{
    if ((!m_loadQueue.empty() || !m_storeQueue.empty()) && !m_workEvent.IsPending())
        m_workEvent = Simulator::ScheduleNow(&UbLdstThread::HandleTaskSegment, this);
}

void UbLdstThread::HandleTaskSegment()
{
    HandleLoadTask();
    HandleStoreTask();
}

bool UbLdstThread::IsIdle() const
{
    return !m_workEvent.IsPending() && m_waitingAckNum.empty() &&
           m_loadQueue.empty() && m_storeQueue.empty() && !GetOutstandingCount();
}

void UbLdstThread::DoDispose()
{
    m_workEvent.Cancel();
    m_waitingAckNum.clear();
    m_loadQueue = {};
    m_storeQueue = {};
    m_loadInFlight = m_storeInFlight = 0;
    Object::DoDispose();
}
} // namespace ns3
