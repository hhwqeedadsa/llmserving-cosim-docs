// SPDX-License-Identifier: GPL-2.0-only
#ifndef UB_LDST_THREAD_H
#define UB_LDST_THREAD_H
#include "ns3/ub-ldst-instance.h"
#include "ns3/event-id.h"

namespace ns3 {
class UbController;
/**
* @brief 发送内存语义包
*/
class UbLdstThread : public Object {
public:
    static TypeId GetTypeId(void);
    UbLdstThread();
    virtual ~UbLdstThread();
    void Init();
    void DoDispose() override;
    void ScheduleWork();
    bool IsIdle() const;
    uint32_t GetOutstandingCount() const { return m_storeInFlight + m_loadInFlight; }
    size_t GetWaitingSegmentCount() const { return m_waitingAckNum.size(); }
    size_t GetQueuedSegmentCount() const;
    const std::string& GetStoreQueuePolicy() const { return m_storeQueuePolicy; }
    std::optional<uint32_t> GetLastStoreProducer() const { return m_lastStoreProducer; }
    void PushTaskSegment(Ptr<UbLdstTaskSegment> taskSegment);
    void HandleTaskSegment();
    void SetNode(uint32_t nodeId);
    void SetThreadId(uint32_t threadId);
    void HandleLoadTask();
    void HandleStoreTask();
    void UpdateTask(Ptr<UbLdstTaskSegment> taskSegment, uint64_t offset = 0, uint32_t bytes = 0);
    uint32_t GetNodeId() const { return m_nodeId; }
    uint32_t GetThreadId() const { return m_threadId; }
    uint32_t GetInFlight(bool load) const { return load ? m_loadInFlight : m_storeInFlight; }
    uint32_t GetCapacity(bool load) const { return load ? m_loadOutstanding : m_storeOutstanding; }
    std::vector<Ptr<const UbLdstTaskSegment>> GetQueueSnapshot(bool load) const;
    void SetLoadReqSize(uint32_t size);
    void SetStoreReqLength(uint32_t length);
    void SetLoadRspLength(uint32_t length);
private:
    Ptr<UbLdstTaskSegment> NextStore() const;
    void StoreIssued(Ptr<UbLdstTaskSegment> segment);
    void ObserveQueue(const std::string& event, Ptr<UbLdstTaskSegment> segment,
                      uint64_t offset = 0, uint32_t bytes = 0);
    TracedCallback<Ptr<const UbLdstThread>, std::string, Ptr<const UbLdstTaskSegment>, uint64_t, uint32_t> m_queueState;
    uint32_t CalcLength(uint32_t size);
    uint32_t m_nodeId;
    uint32_t m_threadId;
    std::queue<Ptr<UbLdstTaskSegment>> m_loadQueue;
    std::queue<Ptr<UbLdstTaskSegment>> m_storeQueue;
    std::map<uint32_t, std::queue<Ptr<UbLdstTaskSegment>>> m_storeProducers;
    std::optional<uint32_t> m_lastStoreProducer;
    std::string m_storeQueuePolicy;

    uint32_t m_loadRspSize = 0;
    uint32_t m_storeReqSize = 0;
    uint32_t m_loadReqSize = 0;
    uint32_t m_loadRspLength = 0;
    uint32_t m_storeReqLength = 0;
    uint32_t m_storeOutstanding; // Configured limits, independent of in-flight state.
    uint32_t m_loadOutstanding;
    uint32_t m_storeInFlight = 0;
    uint32_t m_loadInFlight = 0;
    EventId m_workEvent;
    std::unordered_map<uint64_t, uint32_t> m_waitingAckNum;
};
} // namespace ns3

#endif /* UB_LDST_THREAD_H */
