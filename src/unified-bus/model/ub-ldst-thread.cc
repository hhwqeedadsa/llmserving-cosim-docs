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
                            .AddAttribute("StoreQueuePolicy",
                                          "STORE queue organization: fifo or producer_round_robin; one shared worker budget.",
                                          StringValue("fifo"),
                                          MakeStringAccessor(&UbLdstThread::m_storeQueuePolicy),
                                          MakeStringChecker())
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
    if (m_storeQueuePolicy != "fifo" && m_storeQueuePolicy != "producer_round_robin")
        throw std::invalid_argument("Unknown STORE queue policy");
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
        if (m_storeQueuePolicy != "fifo" && !taskSegment->GetProducer())
            throw std::invalid_argument("Producer round robin STORE requires a static producer identity");
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
        if (m_storeQueuePolicy == "fifo") m_storeQueue.push(taskSegment);
        else m_storeProducers[*taskSegment->GetProducer()].push(taskSegment);
        ScheduleWork();
    }
    ObserveQueue("task_queued", taskSegment);
}

std::vector<Ptr<const UbLdstTaskSegment>> UbLdstThread::GetQueueSnapshot(bool load) const
{
    auto queue = load ? m_loadQueue : m_storeQueue;
    std::vector<Ptr<const UbLdstTaskSegment>> result;
    while (!queue.empty()) { result.push_back(queue.front()); queue.pop(); }
    if (!load && !m_storeProducers.empty()) {
        auto begin = m_lastStoreProducer ? m_storeProducers.upper_bound(*m_lastStoreProducer)
                                         : m_storeProducers.begin();
        if (begin == m_storeProducers.end()) begin = m_storeProducers.begin();
        auto it = begin;
        do {
            auto tasks = it->second;
            while (!tasks.empty()) { result.push_back(tasks.front()); tasks.pop(); }
            if (++it == m_storeProducers.end()) it = m_storeProducers.begin();
        } while (it != begin);
    }
    return result;
}

size_t UbLdstThread::GetQueuedSegmentCount() const
{
    size_t count = m_loadQueue.size() + m_storeQueue.size();
    for (const auto& [producer, queue] : m_storeProducers) count += queue.size();
    return count;
}

Ptr<UbLdstTaskSegment> UbLdstThread::NextStore() const
{
    if (m_storeQueuePolicy == "fifo") return m_storeQueue.empty() ? nullptr : m_storeQueue.front();
    if (m_storeProducers.empty()) return nullptr;
    auto it = m_lastStoreProducer ? m_storeProducers.upper_bound(*m_lastStoreProducer)
                                  : m_storeProducers.begin();
    if (it == m_storeProducers.end()) it = m_storeProducers.begin();
    return it->second.front();
}

void UbLdstThread::StoreIssued(Ptr<UbLdstTaskSegment> segment)
{
    if (m_storeQueuePolicy == "fifo") {
        if (segment->IsSentCompleted()) m_storeQueue.pop();
        return;
    }
    // Advance only after a request was formed. A task leaves its producer
    // queue after its last request, independently of transaction retirement.
    const auto producer = *segment->GetProducer();
    m_lastStoreProducer = producer;
    auto& queue = m_storeProducers.at(producer);
    if (segment->IsSentCompleted()) queue.pop();
    if (queue.empty()) m_storeProducers.erase(producer);
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
        auto taskSegment = NextStore();
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
        StoreIssued(taskSegment);
        ObserveQueue("request_issued", taskSegment, offset, bytes);
    }
    if (auto next = NextStore()) ObserveQueue("outstanding_blocked", next);
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
    if ((!m_loadQueue.empty() || !m_storeQueue.empty() || !m_storeProducers.empty()) && !m_workEvent.IsPending())
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
           m_loadQueue.empty() && m_storeQueue.empty() && m_storeProducers.empty() && !GetOutstandingCount();
}

void UbLdstThread::DoDispose()
{
    m_workEvent.Cancel();
    m_waitingAckNum.clear();
    m_loadQueue = {};
    m_storeQueue = {};
    m_storeProducers.clear();
    m_lastStoreProducer.reset();
    m_loadInFlight = m_storeInFlight = 0;
    Object::DoDispose();
}
} // namespace ns3
