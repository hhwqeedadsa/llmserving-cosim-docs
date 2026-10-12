// SPDX-License-Identifier: GPL-2.0-only
#ifndef UB_LDST_API_H
#define UB_LDST_API_H

#include <ns3/node.h>
#include <ns3/ub-ldst-thread.h>
#include "ns3/ub-datatype.h"
#include "ns3/ub-network-address.h"
#include "ns3/node-list.h"
#include "ns3/ub-tag.h"
#include <map>
#include <set>
namespace ns3 {
constexpr int MAX_LB = 255;
constexpr int MIN_LB = 0;
class UbController;
class UbLdstApi : public Object {
public:
    static TypeId GetTypeId(void);
    UbLdstApi();
    virtual ~UbLdstApi();

public:
    void SetNodeId(uint32_t nodeId);
    void RecvResponse(Ptr<Packet> packet);
    void SetUsePacketSpray(bool usePacketSpray);
    void SetUseShortestPaths(bool useShortestPaths);
    void RecvDataPacket(Ptr<Packet> packet);
    bool LdstProcess(Ptr<UbLdstTaskSegment> taskSegment);
    void DoDispose() override;
    size_t GetPendingRequestCount() const { return m_requests.size(); }
    size_t GetWaitingThreadCount() const { return m_waitingThreads.size(); }
    uint64_t GetPeakPendingRequestCount() const { return m_peakRequests; }
    struct RequestState {
        uint16_t requestId;
        Ptr<const UbLdstTaskSegment> segment;
        uint64_t offset;
        uint32_t bytes;
    };
    std::vector<RequestState> GetRequestSnapshot() const;

private:
    void SendPacket(Ptr<UbLdstTaskSegment> taskSegment, Ptr<Packet> packet);
    Ptr<Packet> GenDataPacket(Ptr<UbLdstTaskSegment> taskSegment, uint16_t requestId);
    struct PendingRequest {
        Ptr<UbLdstTaskSegment> segment;
        uint64_t offset;
        uint32_t bytes;
    };
    std::map<uint16_t, PendingRequest> m_requests;
    std::set<uint32_t> m_waitingThreads;
    uint32_t m_requestIdCount = 65536;
    uint32_t m_nextRequestId = 0;
    uint64_t m_peakRequests = 0;
    // Passive metadata: no new packet tags, payload, routing inputs or events.
    TracedCallback<Ptr<const Packet>, uint32_t, uint64_t, uint32_t, UbMemOperationType>
        m_requestCreated;
    TracedCallback<uint64_t, uint32_t, UbMemOperationType> m_requestArrived;
    TracedCallback<uint64_t, Ptr<const Packet>, UbMemOperationType> m_responseCreated;
    TracedCallback<uint64_t, uint32_t, uint64_t, uint32_t, UbMemOperationType>
        m_responseArrived;
    uint32_t m_nodeId = 0;
    uint32_t m_lbHashSalt = 0;
    bool m_usePacketSpray = false;
    bool m_useShortestPaths = true;
    bool m_pktTraceEnabled = false;
    void LdstRecvNotify(uint32_t packetUid, uint32_t src, uint32_t dst,
                        PacketType type, uint32_t size, uint32_t taskId, UbPacketTraceTag traceTag);
    TracedCallback<uint32_t, uint32_t, uint32_t,
                   PacketType, uint32_t, uint32_t, UbPacketTraceTag> m_ldstRecvNotify;
};
} // namespace ns3

#endif /* UB_LDST_API_H */
