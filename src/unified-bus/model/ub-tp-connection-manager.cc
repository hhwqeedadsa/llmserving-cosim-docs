// SPDX-License-Identifier: GPL-2.0-only
#include "ns3/ub-tp-connection-manager.h"

#include "ns3/node-list.h"
#include "ns3/ub-controller.h"
#include "ns3/ub-traffic-gen.h"
#include "ns3/ub-utils.h"
#ifdef NS3_MPI
#include "ns3/mpi-interface.h"
#endif
#ifdef NS3_MTP
#include "ns3/mtp-interface.h"
#endif

using namespace ns3;
namespace utils {

NS_LOG_COMPONENT_DEFINE("TpConnectionManager");
NS_OBJECT_ENSURE_REGISTERED(TpConnectionManager);

// Dynamic TP lifecycle operations may touch both endpoint LPs and must share one lock.
std::mutex TpConnectionManager::g_tpConnectionControlLock;

TypeId TpConnectionManager::GetTypeId()
{
    static TypeId tid =
        TypeId("ns3::TpConnectionManager")
            .SetParent<Object>()
            .SetGroupName("UnifiedBus")
            .AddAttribute(
                "EnableIdleTransportChannelReclamation",
                "Reclaim idle RTP transport channel endpoints after all planned tasks and "
                "protocol work for the connection have completed.",
                BooleanValue(false),
                MakeBooleanAccessor(&TpConnectionManager::m_enableIdleTransportChannelReclamation),
                MakeBooleanChecker())
            .AddAttribute("RemoveUselessTp",
                          "Legacy alias for EnableIdleTransportChannelReclamation.",
                          BooleanValue(false),
                          MakeBooleanAccessor(&TpConnectionManager::m_removeUselessTpLegacy),
                          MakeBooleanChecker());
    return tid;
}

TpConnectionManager::TpConnectionManager()
{
    m_random = CreateObject<UniformRandomVariable>();
    m_random->SetAttribute("Min", DoubleValue(0.0));
    m_random->SetAttribute("Max", DoubleValue(1.0));
#ifdef NS3_MPI
    if (MpiInterface::IsEnabled())
    {
        MpiInterface::SetControlEventHandler(
            TRANSPORT_RECLAMATION_REQUEST_EVENT,
            [](uint32_t,
               uint32_t targetNode,
               const ParallelCommunicationInterface::ControlEventPayload& payload) {
                ReceiveTransportChannelReclamationRequest(targetNode,
                                                          static_cast<uint32_t>(payload[0]),
                                                          static_cast<uint32_t>(payload[1]),
                                                          static_cast<uint32_t>(payload[2]),
                                                          payload[3]);
            });
        MpiInterface::SetControlEventHandler(
            TRANSPORT_PEER_DRAINED_EVENT,
            [](uint32_t,
               uint32_t targetNode,
               const ParallelCommunicationInterface::ControlEventPayload& payload) {
                ReceiveTransportChannelPeerDrained(targetNode,
                                                   static_cast<uint32_t>(payload[0]),
                                                   payload[1]);
            });
        MpiInterface::SetControlEventHandler(
            TRANSPORT_RECLAMATION_COMPLETE_EVENT,
            [](uint32_t,
               uint32_t targetNode,
               const ParallelCommunicationInterface::ControlEventPayload& payload) {
                ReceiveTransportChannelReclamationComplete(targetNode,
                                                           static_cast<uint32_t>(payload[0]),
                                                           payload[1]);
            });
        MpiInterface::SetControlEventHandler(
            TRANSPORT_RECLAMATION_CONFIRMED_EVENT,
            [](uint32_t,
               uint32_t targetNode,
               const ParallelCommunicationInterface::ControlEventPayload& payload) {
                ReceiveTransportChannelReclamationConfirmed(targetNode,
                                                            static_cast<uint32_t>(payload[0]),
                                                            payload[1]);
            });
    }
#endif
}

TpConnectionManager::~TpConnectionManager()
{

}

// 添加连接（不需要nodeId参数）
void TpConnectionManager::AddConnection(const Connection& conn)
{
    std::lock_guard<std::mutex> lock(m_stateLock);
    // 存储连接
    m_allConnections.push_back(conn);

    // 为两个节点都建立索引
    BuildIndexesForNode(conn.node1, conn);
    BuildIndexesForNode(conn.node2, conn);
}

// 添加单边连接
void TpConnectionManager::AddUnilateralConnection(const Connection& conn, const uint32_t src)
{
    std::lock_guard<std::mutex> lock(m_stateLock);
    // 存储连接
    m_allConnections.push_back(conn);
    // 如果源节点既不是conn的node1也不是node2，则判错
    if (src != conn.node1 && src != conn.node2) {
        NS_ASSERT_MSG(0, "conn.src != src");
    }
    // 仅为源节点建立索引
    BuildIndexesForNode(src, conn);
}

// 获取指定节点的连接管理器视图
Ptr<TpConnectionManager> TpConnectionManager::GetConnectionManagerByNode(uint32_t nodeId)
{
    Ptr<TpConnectionManager> nodeManager = CreateObject<TpConnectionManager>();

    // 复制与该节点相关的连接
    std::lock_guard<std::mutex> lock(m_stateLock);
    auto it = m_nodeConnections.find(nodeId);
    if (it != m_nodeConnections.end()) {
        for (const auto& conn : it->second) {
            nodeManager->AddUnilateralConnection(conn, nodeId);
        }
    }

    return nodeManager;
}

// 根据预设配置的规则选择获取tpns
std::vector<uint32_t> TpConnectionManager::GetTpns(GetTpnRuleT ruler,
                                bool useShortestPath,
                                bool useMultiPath,
                                uint32_t localNodeId,
                                uint32_t peerNodeId,
                                uint32_t localPort,
                                uint32_t peerPort,
                                uint32_t priority)
{
    std::lock_guard<std::mutex> controlLock(g_tpConnectionControlLock);
    std::vector<std::pair<uint32_t, uint32_t>> resWithMetrics;
    std::vector<uint32_t> tpns;
    uint32_t minMetrics = UINT32_MAX;
    switch (ruler) {
        case GetTpnRuleT::BY_PEERNODE:
            resWithMetrics = GetTpnsByPeerNode(localNodeId, peerNodeId);
            break;
        case GetTpnRuleT::BY_PEERNODE_PRIORITY:
            resWithMetrics = GetTpnsByPeerNodePriority(localNodeId, peerNodeId, priority);
            break;
        case GetTpnRuleT::BY_PEERNODE_LOCALPORT:
            resWithMetrics = GetTpnsByPeerNodeLocalPort(localNodeId, peerNodeId, localPort);
            break;
        case GetTpnRuleT::BY_PEERNODE_BOTHPORTS:
            resWithMetrics = GetTpnsByPeerNodeBothPorts(localNodeId, peerNodeId, localPort, peerPort);
            break;
        case GetTpnRuleT::BY_ALL:
            resWithMetrics = GetTpnsByFullCriteria(localNodeId, peerNodeId, localPort, peerPort, priority);
            break;
        default:
            break;
    }
    if (resWithMetrics.empty())
    {
        resWithMetrics =
            ReserveNewTpConnections(localNodeId, peerNodeId, priority, useShortestPath);
    }

    for (auto p : resWithMetrics) {
        minMetrics = std::min(minMetrics, p.second);
    }
    for (auto p : resWithMetrics) {
        if (!useShortestPath) {
            tpns.push_back(p.first);
        } else if (minMetrics == p.second) {
            tpns.push_back(p.first);
        }
    }
    NS_ABORT_MSG_IF(tpns.empty(), "Could not reserve a TP connection for the requested traffic");
    {
        std::lock_guard<std::mutex> lock(m_stateLock);
        for (uint32_t tpn : tpns)
        {
            NS_ABORT_MSG_IF(
                m_transportChannelReclamations.find(tpn) != m_transportChannelReclamations.end(),
                "Transport channel " << tpn << " on node " << localNodeId
                                     << " is being reclaimed; a dynamically added task cannot "
                                        "reuse it until reclamation completes");
        }
    }
    return ReconstructTPs(tpns, localNodeId, peerNodeId, priority, useShortestPath, useMultiPath);
}

void
TpConnectionManager::ReserveTpnsForTraffic(bool useShortestPath,
                                           uint32_t localNodeId,
                                           uint32_t peerNodeId,
                                           uint32_t priority)
{
    std::lock_guard<std::mutex> controlLock(g_tpConnectionControlLock);
    if (!GetTpnsByPeerNodePriority(localNodeId, peerNodeId, priority).empty())
    {
        return;
    }
    ReserveNewTpConnections(localNodeId, peerNodeId, priority, useShortestPath);
}

// 1. 获取节点相关的所有连接
std::vector<Connection> TpConnectionManager::GetNodeConnections(uint32_t nodeId) const
{
    std::lock_guard<std::mutex> lock(m_stateLock);
    auto it = m_nodeConnections.find(nodeId);
    if (it != m_nodeConnections.end()) {
        return it->second;
    }
    return {};
}

// 2. 通过对端nodeId找到所有可用tpn
std::vector<std::pair<uint32_t, uint32_t>> TpConnectionManager::GetTpnsByPeerNode(
    uint32_t localNodeId, uint32_t peerNodeId) const
{
    std::vector<std::pair<uint32_t, uint32_t>> tpns;
    std::lock_guard<std::mutex> lock(m_stateLock);
    auto key = std::make_pair(localNodeId, peerNodeId);
    auto it = m_peerNodeIndex.find(key);
    if (it != m_peerNodeIndex.end()) {
        for (const auto& conn : it->second) {
            // 获取本地节点对应的TPN
            if (conn.node1 == localNodeId) {
                tpns.push_back(std::make_pair(conn.tpn1, conn.metrics));
            } else if (conn.node2 == localNodeId) {
                tpns.push_back(std::make_pair(conn.tpn2, conn.metrics));
            }
        }
    }
    return tpns;
}

// 3. 通过对端nodeId+priority找到所有可用tpn
std::vector<std::pair<uint32_t, uint32_t>> TpConnectionManager::GetTpnsByPeerNodePriority(
    uint32_t localNodeId, uint32_t peerNodeId, uint32_t priority) const
{
    std::vector<std::pair<uint32_t, uint32_t>> tpns;
    std::lock_guard<std::mutex> lock(m_stateLock);
    auto key = std::make_tuple(localNodeId, peerNodeId, priority);
    auto it = m_peerNodePriorityIndex.find(key);
    if (it != m_peerNodePriorityIndex.end()) {
        for (const auto& conn : it->second) {
            // 获取本地节点对应的TPN
            if (conn.node1 == localNodeId) {
                tpns.push_back(std::make_pair(conn.tpn1, conn.metrics));
            } else if (conn.node2 == localNodeId) {
                tpns.push_back(std::make_pair(conn.tpn2, conn.metrics));
            }
        }
    }
    return tpns;
}

// 4. 通过对端nodeId+本端port找到所有可用tpn
std::vector<std::pair<uint32_t, uint32_t>> TpConnectionManager::GetTpnsByPeerNodeLocalPort(
    uint32_t localNodeId, uint32_t peerNodeId, uint32_t localPort) const
{
    std::vector<std::pair<uint32_t, uint32_t>> tpns;
    std::lock_guard<std::mutex> lock(m_stateLock);
    auto key = std::make_tuple(localNodeId, peerNodeId, localPort);
    auto it = m_peerNodeLocalPortIndex.find(key);
    if (it != m_peerNodeLocalPortIndex.end()) {
        for (const auto& conn : it->second) {
            // 获取本地节点对应的TPN
            if (conn.node1 == localNodeId) {
                tpns.push_back(std::make_pair(conn.tpn1, conn.metrics));
            } else if (conn.node2 == localNodeId) {
                tpns.push_back(std::make_pair(conn.tpn2, conn.metrics));
            }
        }
    }
    return tpns;
}

// 5. 通过对端nodeId+本端port+对端port找到所有可用tpn
std::vector<std::pair<uint32_t, uint32_t>> TpConnectionManager::GetTpnsByPeerNodeBothPorts(
    uint32_t localNodeId, uint32_t peerNodeId, uint32_t localPort, uint32_t peerPort) const
{
    std::vector<std::pair<uint32_t, uint32_t>> tpns;
    std::lock_guard<std::mutex> lock(m_stateLock);
    auto key = std::make_tuple(localNodeId, peerNodeId, localPort, peerPort);
    auto it = m_bothPortsIndex.find(key);
    if (it != m_bothPortsIndex.end()) {
        for (const auto& conn : it->second) {
            // 获取本地节点对应的TPN
            if (conn.node1 == localNodeId) {
                tpns.push_back(std::make_pair(conn.tpn1, conn.metrics));
            } else if (conn.node2 == localNodeId) {
                tpns.push_back(std::make_pair(conn.tpn2, conn.metrics));
            }
        }
    }
    return tpns;
}

// 组合查询：通过完整条件找到所有可用tpn
std::vector<std::pair<uint32_t, uint32_t>> TpConnectionManager::GetTpnsByFullCriteria(
    uint32_t localNodeId, uint32_t peerNodeId, uint32_t localPort, uint32_t peerPort, uint32_t priority) const
{
    std::vector<std::pair<uint32_t, uint32_t>> tpns;
    std::lock_guard<std::mutex> lock(m_stateLock);

    // 遍历本节点的所有连接，找到匹配条件的
    auto it = m_nodeConnections.find(localNodeId);
    if (it != m_nodeConnections.end()) {
        for (const auto& conn : it->second) {
            // 检查是否匹配条件
            bool isMatch = false;
            uint32_t localTpn = 0;

            if (conn.node1 == localNodeId && conn.node2 == peerNodeId) {
                // localNodeId是node1
                if (conn.port1 == localPort && conn.port2 == peerPort &&
                    conn.priority == priority) {
                    isMatch = true;
                    localTpn = conn.tpn1;
                }
            } else if (conn.node2 == localNodeId && conn.node1 == peerNodeId) {
                // localNodeId是node2
                if (conn.port2 == localPort && conn.port1 == peerPort &&
                    conn.priority == priority) {
                    isMatch = true;
                    localTpn = conn.tpn2;
                }
            }

            if (isMatch) {
                tpns.push_back(std::make_pair(localTpn, conn.metrics));
            }
        }
    }
    return tpns;
}

// 获取某个节点的所有TPN
std::vector<uint32_t> TpConnectionManager::GetAllTpnsForNode(uint32_t nodeId) const
{
    std::vector<uint32_t> tpns;
    std::lock_guard<std::mutex> lock(m_stateLock);
    auto it = m_nodeConnections.find(nodeId);
    if (it != m_nodeConnections.end()) {
        for (const auto& conn : it->second) {
            if (conn.node1 == nodeId) {
                tpns.push_back(conn.tpn1);
            } else if (conn.node2 == nodeId) {
                tpns.push_back(conn.tpn2);
            }
        }
    }
    return tpns;
}

// 获取节点的邻居节点
std::set<uint32_t> TpConnectionManager::GetNeighborNodes(uint32_t nodeId) const
{
    std::set<uint32_t> neighbors;
    std::lock_guard<std::mutex> lock(m_stateLock);
    auto it = m_nodeConnections.find(nodeId);
    if (it != m_nodeConnections.end()) {
        for (const auto& conn : it->second) {
            if (conn.node1 == nodeId) {
                neighbors.insert(conn.node2);
            } else if (conn.node2 == nodeId) {
                neighbors.insert(conn.node1);
            }
        }
    }
    return neighbors;
}

// 获取与该tp相关的conn
Connection TpConnectionManager::GetConnection(uint32_t tpn, uint32_t src, uint32_t dst, uint32_t priority)
{
    std::lock_guard<std::mutex> lock(m_stateLock);
    auto key = std::make_tuple(src, dst, priority);
    auto it = m_peerNodePriorityIndex.find(key);
    bool exists = false;
    Connection connection;
    NS_ASSERT_MSG(it != m_peerNodePriorityIndex.end(), "Could not find (src, dst, priority) record.");
    for (const auto& conn : it->second) {
        if (conn.node1 == src && conn.tpn1 == tpn) {
            exists = true;
            connection = conn;
        }
    }
    NS_ASSERT_MSG(exists, "Could not find tpn record.");
    return connection;
}

// 获取与该tp相关的conn,不限优先级
Connection TpConnectionManager::GetConnection(uint32_t tpn, uint32_t src, uint32_t dst)
{
    std::lock_guard<std::mutex> lock(m_stateLock);
    auto key = std::make_pair(src, dst);
    auto it = m_peerNodeIndex.find(key);
    bool exists = false;
    Connection connection;
    NS_ASSERT_MSG(it != m_peerNodeIndex.end(), "Could not find (src, dst) record.");
    for (const auto& conn : it->second) {
        if (conn.node1 == src && conn.tpn1 == tpn) {
            exists = true;
            connection = conn;
            break;
        }
    }
    NS_ASSERT_MSG(exists, "Could not find tpn record.");
    return connection;
}

bool
TpConnectionManager::TryGetLocalConnection(uint32_t localNodeId,
                                           uint32_t localTpn,
                                           Connection& connection) const
{
    std::lock_guard<std::mutex> lock(m_stateLock);
    auto it = m_localTpnIndex.find({localNodeId, localTpn});
    if (it == m_localTpnIndex.end())
    {
        return false;
    }
    connection = it->second;
    return true;
}

// 清空指定节点的连接
void TpConnectionManager::ClearNodeConnections(uint32_t nodeId)
{
    std::lock_guard<std::mutex> lock(m_stateLock);
    m_nodeConnections.erase(nodeId);
    ClearNodeFromIndexes(nodeId);

    // 从allConnections_中移除相关连接
    m_allConnections.erase(
        std::remove_if(m_allConnections.begin(), m_allConnections.end(),
            [nodeId](const Connection& conn) {
                return conn.node1 == nodeId || conn.node2 == nodeId;
            }),
        m_allConnections.end()
    );
}

// 清空所有连接
void TpConnectionManager::Clear()
{
    std::lock_guard<std::mutex> lock(m_stateLock);
    m_allConnections.clear();
    m_nodeConnections.clear();
    m_peerNodeIndex.clear();
    m_peerNodePriorityIndex.clear();
    m_peerNodeLocalPortIndex.clear();
    m_bothPortsIndex.clear();
    m_localTpnIndex.clear();
    m_tpnList.clear();
    m_reservedTpnList.clear();
}

// 为指定节点建立各种索引
void TpConnectionManager::BuildIndexesForNode(uint32_t localNodeId, Connection conn)
{
    uint32_t peerNodeId;
    uint32_t localPort;
    uint32_t peerPort;
    uint32_t localTpn;
    uint32_t peerTpn;

    // 确定对端节点和端口
    if (conn.node1 == localNodeId) {
        peerNodeId = conn.node2;
        localPort = conn.port1;
        peerPort = conn.port2;
        localTpn = conn.tpn1;
        peerTpn = conn.tpn2;
    } else {
        peerNodeId = conn.node1;
        localPort = conn.port2;
        peerPort = conn.port1;
        localTpn = conn.tpn2;
        peerTpn = conn.tpn1;
    }
    conn.node1 = localNodeId;
    conn.node2 = peerNodeId;
    conn.port1 = localPort;
    conn.port2 = peerPort;
    conn.tpn1 = localTpn;
    conn.tpn2 = peerTpn;
    // 建立各种索引

    // 索引1: 添加到节点连接列表
    m_nodeConnections[localNodeId].push_back(conn);

    // 索引2: 对端节点
    m_peerNodeIndex[{localNodeId, peerNodeId}].push_back(conn);

    // 索引3: 对端节点+优先级
    m_peerNodePriorityIndex[{localNodeId, peerNodeId, static_cast<uint32_t>(conn.priority)}].push_back(conn);

    // 索引4: 对端节点+本端端口
    m_peerNodeLocalPortIndex[{localNodeId, peerNodeId, localPort}].push_back(conn);

    // 索引5: 对端节点+本端端口+对端端口
    m_bothPortsIndex[{localNodeId, peerNodeId, localPort, peerPort}].push_back(conn);

    // 索引6: 本地 TPN
    m_localTpnIndex[{localNodeId, localTpn}] = conn;

    if (m_reservedTpnList.erase(localTpn) == 0) {
        NS_ASSERT_MSG(m_tpnList.find(localTpn) == m_tpnList.end(), "Tpn already exists!");
    }
    m_tpnList.insert(localTpn);
}

// 从索引中清理指定节点
void TpConnectionManager::ClearNodeFromIndexes(uint32_t nodeId)
{
    // 清理各种索引中包含该节点的条目
    for (auto it = m_peerNodeIndex.begin(); it != m_peerNodeIndex.end();) {
        if (it->first.first == nodeId) {
            it = m_peerNodeIndex.erase(it);
        } else {
            ++it;
        }
    }

    for (auto it = m_peerNodePriorityIndex.begin(); it != m_peerNodePriorityIndex.end();) {
        if (std::get<0>(it->first) == nodeId) {
            it = m_peerNodePriorityIndex.erase(it);
        } else {
            ++it;
        }
    }

    for (auto it = m_peerNodeLocalPortIndex.begin(); it != m_peerNodeLocalPortIndex.end();) {
        if (std::get<0>(it->first) == nodeId) {
            it = m_peerNodeLocalPortIndex.erase(it);
        } else {
            ++it;
        }
    }

    for (auto it = m_bothPortsIndex.begin(); it != m_bothPortsIndex.end();) {
        if (std::get<0>(it->first) == nodeId) {
            it = m_bothPortsIndex.erase(it);
        } else {
            ++it;
        }
    }

    for (auto it = m_localTpnIndex.begin(); it != m_localTpnIndex.end();)
    {
        if (it->first.first == nodeId)
        {
            it = m_localTpnIndex.erase(it);
        }
        else
        {
            ++it;
        }
    }
}

std::vector<std::pair<uint32_t, uint32_t>>
TpConnectionManager::ReserveNewTpConnections(uint32_t src,
                                             uint32_t dst,
                                             uint32_t priority,
                                             bool useShortestPath)
{
    std::vector<std::pair<uint32_t, uint32_t>> tpns;
    // 目标节点+端口的ip地址，出端口和跳数pair 的 vector
    std::map<Ipv4Address, std::vector<std::pair<uint16_t, uint32_t>>> routingEntries;
    // 查找路由，寻找从本节点到目的节点的路径
    Ptr<UbRoutingProcess> rt = NodeList::GetNode(src)->GetObject<UbSwitch>()->GetRoutingProcess();
    for (uint32_t dstPort = 0; dstPort < NodeList::GetNode(dst)->GetNDevices(); dstPort++) {
        // 查找去往该ip的出端口，若非空则记录
        Ipv4Address dstPortIp = NodeIdToIp(dst, dstPort);
        std::vector<uint16_t> shortestOutPortsVec, otherOutPortsVec;
        rt->GetShortestOutPorts(dstPortIp.Get(), shortestOutPortsVec);
        rt->GetOtherOutPorts(dstPortIp.Get(), otherOutPortsVec);
        if (!shortestOutPortsVec.empty()) {
            routingEntries[dstPortIp] = std::vector<std::pair<uint16_t, uint32_t>>();
            for (uint16_t outPort : shortestOutPortsVec) {
                routingEntries[dstPortIp].push_back(std::make_pair(outPort, 0));
            }
        }
        // 仅在开启非最短路模式的情况下加入非最短路由
        if (!otherOutPortsVec.empty() && !useShortestPath) {
            routingEntries[dstPortIp] = std::vector<std::pair<uint16_t, uint32_t>>();
            for (uint16_t outPort : otherOutPortsVec) {
                routingEntries[dstPortIp].push_back(std::make_pair(outPort, 1));
            }
        }
    }
    // 如果为空，则判错，路由有问题。不考虑省略host路由的情况
    if (routingEntries.empty()) {
        NS_ASSERT_MSG(0, "Invalid routing! Traffic can't arrive!");
    }
    for (auto it = routingEntries.begin(); it != routingEntries.end(); it++) {
        Ipv4Address dstIp = it->first;
        uint32_t dstPort = IpToPortId(dstIp);
        for (auto outPortIt = it->second.begin(); outPortIt != it->second.end(); outPortIt++) {
            // 无论是否创建TP，都建立connection记录，以供后续所有源目的节点相同的流使用
            Ptr<ns3::UbController> sendCtrl = NodeList::GetNode(src)->GetObject<ns3::UbController>();
            Ptr<ns3::UbController> recvCtrl = NodeList::GetNode(dst)->GetObject<ns3::UbController>();
            Connection conn;
            conn.node1 = src;
            conn.port1 = outPortIt->first;
            conn.tpn1 = GetNextTpn();
            conn.node2 = dst;
            conn.port2 = dstPort;
            conn.tpn2 = recvCtrl->GetTpConnManager()->GetNextTpn();
            conn.priority = priority;
            conn.metrics = outPortIt->second;
            // connection添加tpnConn
            AddUnilateralConnection(conn, src);
            recvCtrl->GetTpConnManager()->AddUnilateralConnection(conn, dst);
            tpns.emplace_back(conn.tpn1, conn.metrics);
        }
    }
    return tpns;
}

std::vector<uint32_t> TpConnectionManager::ReconstructTPs(std::vector<uint32_t> tpns, uint32_t src, uint32_t dst,
    uint32_t priority, bool useShortestPath, bool useMultiPath)
{
    std::vector<uint32_t> res;
    if (!useMultiPath) { // 单路径模式下仅创建一个
        uint32_t idx = (uint32_t)(m_random->GetValue() * tpns.size());
        NS_LOG_DEBUG("Connection num: " << tpns.size() << " Reconstruct idx: " << idx);
        Connection conn = GetConnection(tpns[idx], src, dst, priority);
        res.push_back(ReconstructTp(conn));
    } else { // 多路径模式下全部创建
        NS_LOG_DEBUG("Connection num: " << tpns.size() << " all reconstruct.");
        for (uint32_t i = 0; i < tpns.size(); i++) {
            Connection conn = GetConnection(tpns[i], src, dst, priority);
            res.push_back(ReconstructTp(conn));
        }
    }
    return res;
}

uint32_t TpConnectionManager::ReconstructTp(Connection conn)
{
    Ptr<UbController> sendCtrl = NodeList::GetNode(conn.node1)->GetObject<UbController>();
    if (!sendCtrl->IsTPExists(conn.tpn1)) { // 不存在，创建
        auto sendHostCaqm = UbCongestionControl::Create(UB_DEVICE);
        sendCtrl->CreateTp(conn.node1, conn.node2, conn.port1, conn.port2,
                           conn.priority, conn.tpn1, conn.tpn2, sendHostCaqm);
    }
    return conn.tpn1;
}

void
TpConnectionManager::ReleaseJettyTransportChannelReferences(uint32_t localNodeId, uint32_t jettyNum)
{
    if (!IsIdleTransportChannelReclamationEnabled())
    {
        return;
    }
    std::lock_guard<std::mutex> controlLock(g_tpConnectionControlLock);
    auto controller = NodeList::GetNode(localNodeId)->GetObject<UbController>();
    controller->GetUbTransaction()->DestroyJettyTpMap(jettyNum);
}

void
TpConnectionManager::ScheduleIdleTransportChannelReclamation(uint32_t sourceNode,
                                                             uint32_t destNode,
                                                             uint8_t priority)
{
    const auto scheduleAtOwner = [priority](uint32_t localNodeId, uint32_t peerNodeId) {
#ifdef NS3_MPI
        if (MpiInterface::IsEnabled() && MpiInterface::GetSize() > 1 &&
            !UbUtils::IsSystemOwnedByRank(NodeList::GetNode(localNodeId)->GetSystemId(),
                                          MpiInterface::GetSystemId()))
        {
            return;
        }
#endif
        Simulator::ScheduleWithContext(
            localNodeId,
            GetReclamationVisibilityDelay(localNodeId),
            &TpConnectionManager::RequestIdleTransportChannelReclamationAtOwner,
            localNodeId,
            peerNodeId,
            priority);
    };

    scheduleAtOwner(sourceNode, destNode);
    if (destNode != sourceNode)
    {
        scheduleAtOwner(destNode, sourceNode);
    }
}

void
TpConnectionManager::RequestIdleTransportChannelReclamationAtOwner(uint32_t localNodeId,
                                                                   uint32_t peerNodeId,
                                                                   uint8_t priority)
{
    Ptr<UbController> controller = NodeList::GetNode(localNodeId)->GetObject<UbController>();
    NS_ABORT_MSG_IF(controller == nullptr,
                    "Idle transport channel reclamation requires a UB controller on node "
                        << localNodeId);
    controller->GetTpConnManager()->RequestIdleTransportChannelReclamation(localNodeId,
                                                                           peerNodeId,
                                                                           priority);
}

void
TpConnectionManager::RequestIdleTransportChannelReclamation(uint32_t localNodeId,
                                                            uint32_t peerNodeId,
                                                            uint32_t priority)
{
    if (!IsIdleTransportChannelReclamationEnabled())
    {
        return;
    }

    Ptr<UbController> controller = NodeList::GetNode(localNodeId)->GetObject<UbController>();
    const auto unreferencedTpns =
        controller->GetUbTransaction()->GetUnreferencedTransportChannelNumbers();
    const auto plannedTpns = GetTpnsByPeerNodePriority(localNodeId, peerNodeId, priority);
    std::set<uint32_t> plannedTpnSet;
    for (const auto& [tpn, metrics] : plannedTpns)
    {
        (void)metrics;
        plannedTpnSet.insert(tpn);
    }

    for (uint32_t tpn : unreferencedTpns)
    {
        if (plannedTpnSet.contains(tpn))
        {
            RequestTransportChannelReclamation(localNodeId, tpn);
            NS_LOG_DEBUG("Requested idle transport channel reclamation on node " << localNodeId
                                                                                 << " tpn " << tpn);
        }
    }
}

void
TpConnectionManager::RequestTransportChannelReclamation(uint32_t localNodeId, uint32_t localTpn)
{
    Connection connection;
    NS_ABORT_MSG_IF(!TryGetLocalConnection(localNodeId, localTpn, connection),
                    "Cannot reclaim transport channel without a local reservation: node="
                        << localNodeId << " tpn=" << localTpn);

    bool sendRequest = false;
    uint64_t requestId = 0;
    {
        std::lock_guard<std::mutex> lock(m_stateLock);
        auto it = m_transportChannelReclamations.find(localTpn);
        if (it == m_transportChannelReclamations.end())
        {
            requestId = AllocateReclamationRequestIdLocked(localNodeId);
            m_transportChannelReclamations.emplace(
                localTpn,
                TransportChannelReclamation{connection.node2,
                                            connection.tpn2,
                                            requestId,
                                            TransportChannelReclamation::Role::INITIATOR,
                                            false,
                                            false});
            sendRequest = true;
        }
        else if (it->second.role == TransportChannelReclamation::Role::RESPONDER &&
                 IsPreferredReclamationInitiator(localNodeId,
                                                 localTpn,
                                                 it->second.peerNodeId,
                                                 it->second.peerTpn))
        {
            NS_ABORT_MSG_IF(it->second.localEndpointReclaimed,
                            "Transport channel cannot become the reclamation initiator after its "
                            "local endpoint has been reclaimed");
            it->second.requestId = AllocateReclamationRequestIdLocked(localNodeId);
            it->second.role = TransportChannelReclamation::Role::INITIATOR;
            it->second.peerEndpointReclaimed = false;
            sendRequest = true;
            requestId = it->second.requestId;
        }
        else
        {
            requestId = it->second.requestId;
        }
    }

    if (sendRequest)
    {
        ScheduleTransportChannelReclamationRequest(connection.node2,
                                                   connection.tpn2,
                                                   localNodeId,
                                                   localTpn,
                                                   requestId);
    }
    TryAdvanceTransportChannelReclamation(localNodeId, localTpn);
}

void
TpConnectionManager::NotifyTransportChannelStateChanged(uint32_t localNodeId, uint32_t localTpn)
{
    TryAdvanceTransportChannelReclamation(localNodeId, localTpn);
}

void
TpConnectionManager::ReceiveTransportChannelReclamationRequest(uint32_t localNodeId,
                                                               uint32_t localTpn,
                                                               uint32_t peerNodeId,
                                                               uint32_t peerTpn,
                                                               uint64_t requestId)
{
    Ptr<TpConnectionManager> manager =
        NodeList::GetNode(localNodeId)->GetObject<UbController>()->GetTpConnManager();
    {
        std::lock_guard<std::mutex> lock(manager->m_stateLock);
        auto [it, inserted] = manager->m_transportChannelReclamations.emplace(
            localTpn,
            TransportChannelReclamation{peerNodeId,
                                        peerTpn,
                                        requestId,
                                        TransportChannelReclamation::Role::RESPONDER,
                                        false,
                                        false});
        if (!inserted)
        {
            NS_ABORT_MSG_IF(it->second.peerNodeId != peerNodeId || it->second.peerTpn != peerTpn,
                            "Transport channel reclamation peer does not match its reservation");
            if (it->second.role == TransportChannelReclamation::Role::INITIATOR)
            {
                if (IsPreferredReclamationInitiator(localNodeId, localTpn, peerNodeId, peerTpn))
                {
                    return;
                }
                it->second.requestId = requestId;
                it->second.role = TransportChannelReclamation::Role::RESPONDER;
                it->second.peerEndpointReclaimed = false;
            }
            else if (it->second.requestId != requestId)
            {
                return;
            }
        }
    }
    manager->TryAdvanceTransportChannelReclamation(localNodeId, localTpn);
}

void
TpConnectionManager::ReceiveTransportChannelPeerDrained(uint32_t localNodeId,
                                                        uint32_t localTpn,
                                                        uint64_t requestId)
{
    Ptr<TpConnectionManager> manager =
        NodeList::GetNode(localNodeId)->GetObject<UbController>()->GetTpConnManager();
    {
        std::lock_guard<std::mutex> lock(manager->m_stateLock);
        auto it = manager->m_transportChannelReclamations.find(localTpn);
        if (it == manager->m_transportChannelReclamations.end())
        {
            return;
        }
        if (it->second.requestId != requestId)
        {
            return;
        }
        NS_ABORT_MSG_IF(it->second.role != TransportChannelReclamation::Role::INITIATOR,
                        "Peer-drained confirmation arrived at the non-initiating endpoint");
        it->second.peerEndpointReclaimed = true;
    }
    manager->TryAdvanceTransportChannelReclamation(localNodeId, localTpn);
}

void
TpConnectionManager::ReceiveTransportChannelReclamationComplete(uint32_t localNodeId,
                                                                uint32_t localTpn,
                                                                uint64_t requestId)
{
    Ptr<TpConnectionManager> manager =
        NodeList::GetNode(localNodeId)->GetObject<UbController>()->GetTpConnManager();
    uint32_t peerNodeId = 0;
    uint32_t peerTpn = 0;
    {
        std::lock_guard<std::mutex> lock(manager->m_stateLock);
        auto it = manager->m_transportChannelReclamations.find(localTpn);
        if (it == manager->m_transportChannelReclamations.end() ||
            it->second.requestId != requestId)
        {
            return;
        }
        NS_ABORT_MSG_IF(it->second.role != TransportChannelReclamation::Role::RESPONDER ||
                            !it->second.localEndpointReclaimed,
                        "Transport channel reclamation completed before the responding endpoint "
                        "was reclaimed");
        peerNodeId = it->second.peerNodeId;
        peerTpn = it->second.peerTpn;
        manager->m_transportChannelReclamations.erase(it);
    }
    ScheduleTransportChannelControlEvent(TRANSPORT_RECLAMATION_CONFIRMED_EVENT,
                                         peerNodeId,
                                         peerTpn,
                                         requestId);
}

void
TpConnectionManager::ReceiveTransportChannelReclamationConfirmed(uint32_t localNodeId,
                                                                 uint32_t localTpn,
                                                                 uint64_t requestId)
{
    Ptr<TpConnectionManager> manager =
        NodeList::GetNode(localNodeId)->GetObject<UbController>()->GetTpConnManager();
    std::lock_guard<std::mutex> lock(manager->m_stateLock);
    auto it = manager->m_transportChannelReclamations.find(localTpn);
    if (it == manager->m_transportChannelReclamations.end() || it->second.requestId != requestId)
    {
        return;
    }
    NS_ABORT_MSG_IF(it->second.role != TransportChannelReclamation::Role::INITIATOR ||
                        !it->second.localEndpointReclaimed,
                    "Transport channel reclamation confirmation arrived before the initiating "
                    "endpoint was reclaimed");
    manager->m_transportChannelReclamations.erase(it);
}

void
TpConnectionManager::ScheduleTransportChannelReclamationRequest(uint32_t localNodeId,
                                                                uint32_t localTpn,
                                                                uint32_t peerNodeId,
                                                                uint32_t peerTpn,
                                                                uint64_t requestId)
{
    const Time delay = GetReclamationVisibilityDelay(localNodeId);
#ifdef NS3_MPI
    if (MpiInterface::IsEnabled() && MpiInterface::GetSize() > 1)
    {
        const uint32_t targetRank =
            UbUtils::ExtractMpiRank(NodeList::GetNode(localNodeId)->GetSystemId());
        if (targetRank != MpiInterface::GetSystemId())
        {
            MpiInterface::SendControlEvent(TRANSPORT_RECLAMATION_REQUEST_EVENT,
                                           {localTpn, peerNodeId, peerTpn, requestId},
                                           Simulator::Now() + delay,
                                           localNodeId,
                                           targetRank);
            return;
        }
    }
#endif
    Simulator::ScheduleWithContext(localNodeId,
                                   delay,
                                   &TpConnectionManager::ReceiveTransportChannelReclamationRequest,
                                   localNodeId,
                                   localTpn,
                                   peerNodeId,
                                   peerTpn,
                                   requestId);
}

void
TpConnectionManager::ScheduleTransportChannelControlEvent(uint32_t eventType,
                                                          uint32_t localNodeId,
                                                          uint32_t localTpn,
                                                          uint64_t requestId)
{
    const Time delay = GetReclamationVisibilityDelay(localNodeId);
#ifdef NS3_MPI
    if (MpiInterface::IsEnabled() && MpiInterface::GetSize() > 1)
    {
        const uint32_t targetRank =
            UbUtils::ExtractMpiRank(NodeList::GetNode(localNodeId)->GetSystemId());
        if (targetRank != MpiInterface::GetSystemId())
        {
            MpiInterface::SendControlEvent(eventType,
                                           {localTpn, requestId, 0, 0},
                                           Simulator::Now() + delay,
                                           localNodeId,
                                           targetRank);
            return;
        }
    }
#endif
    if (eventType == TRANSPORT_PEER_DRAINED_EVENT)
    {
        Simulator::ScheduleWithContext(localNodeId,
                                       delay,
                                       &TpConnectionManager::ReceiveTransportChannelPeerDrained,
                                       localNodeId,
                                       localTpn,
                                       requestId);
    }
    else if (eventType == TRANSPORT_RECLAMATION_COMPLETE_EVENT)
    {
        Simulator::ScheduleWithContext(
            localNodeId,
            delay,
            &TpConnectionManager::ReceiveTransportChannelReclamationComplete,
            localNodeId,
            localTpn,
            requestId);
    }
    else
    {
        NS_ASSERT(eventType == TRANSPORT_RECLAMATION_CONFIRMED_EVENT);
        Simulator::ScheduleWithContext(
            localNodeId,
            delay,
            &TpConnectionManager::ReceiveTransportChannelReclamationConfirmed,
            localNodeId,
            localTpn,
            requestId);
    }
}

Time
TpConnectionManager::GetReclamationVisibilityDelay(uint32_t targetNodeId)
{
    bool crossesExecutionOwner = false;
#ifdef NS3_MPI
    if (MpiInterface::IsEnabled() && MpiInterface::GetSize() > 1)
    {
        const uint32_t targetRank =
            UbUtils::ExtractMpiRank(NodeList::GetNode(targetNodeId)->GetSystemId());
        crossesExecutionOwner = targetRank != MpiInterface::GetSystemId();
    }
#endif
#ifdef NS3_MTP
    if (!crossesExecutionOwner && MtpInterface::isPartitioned())
    {
        const auto targetLp = MtpInterface::FindNodeLocalLpId(targetNodeId);
        crossesExecutionOwner = targetLp.has_value() && MtpInterface::GetSystem() != nullptr &&
                                *targetLp != MtpInterface::GetSystem()->GetSystemId();
    }
#endif
    if (!crossesExecutionOwner)
    {
        return TimeStep(0);
    }

    const Time delay = UbTrafficGen::Get()->GetDependencyVisibilityDelay();
    NS_ABORT_MSG_IF(!delay.IsStrictlyPositive(),
                    "Transport channel reclamation across execution owners requires a positive "
                    "visibility delay inferred from UB links");
    return delay;
}

bool
TpConnectionManager::IsPreferredReclamationInitiator(uint32_t localNodeId,
                                                     uint32_t localTpn,
                                                     uint32_t peerNodeId,
                                                     uint32_t peerTpn)
{
    return std::tie(localNodeId, localTpn) < std::tie(peerNodeId, peerTpn);
}

uint64_t
TpConnectionManager::AllocateReclamationRequestIdLocked(uint32_t localNodeId)
{
    const uint64_t requestId =
        (static_cast<uint64_t>(localNodeId) << 32) | m_nextReclamationSequence;
    ++m_nextReclamationSequence;
    NS_ABORT_MSG_IF(m_nextReclamationSequence == 0,
                    "Transport channel reclamation request sequence exhausted");
    return requestId;
}

void
TpConnectionManager::TryAdvanceTransportChannelReclamation(uint32_t localNodeId, uint32_t localTpn)
{
    TransportChannelReclamation reclamation;
    {
        std::lock_guard<std::mutex> lock(m_stateLock);
        auto it = m_transportChannelReclamations.find(localTpn);
        if (it == m_transportChannelReclamations.end())
        {
            return;
        }
        reclamation = it->second;
    }

    if (reclamation.role == TransportChannelReclamation::Role::INITIATOR &&
        !reclamation.peerEndpointReclaimed)
    {
        return;
    }
    if (reclamation.localEndpointReclaimed)
    {
        return;
    }

    Ptr<UbController> controller = NodeList::GetNode(localNodeId)->GetObject<UbController>();
    if (!controller->TryReclaimIdleTransportChannel(localTpn))
    {
        return;
    }

    bool stateUpdated = false;
    {
        std::lock_guard<std::mutex> lock(m_stateLock);
        auto it = m_transportChannelReclamations.find(localTpn);
        if (it != m_transportChannelReclamations.end() &&
            it->second.requestId == reclamation.requestId)
        {
            it->second.localEndpointReclaimed = true;
            stateUpdated = true;
        }
    }
    if (!stateUpdated)
    {
        return;
    }
    if (reclamation.role == TransportChannelReclamation::Role::RESPONDER)
    {
        ScheduleTransportChannelControlEvent(TRANSPORT_PEER_DRAINED_EVENT,
                                             reclamation.peerNodeId,
                                             reclamation.peerTpn,
                                             reclamation.requestId);
    }
    else
    {
        ScheduleTransportChannelControlEvent(TRANSPORT_RECLAMATION_COMPLETE_EVENT,
                                             reclamation.peerNodeId,
                                             reclamation.peerTpn,
                                             reclamation.requestId);
    }
}

uint32_t TpConnectionManager::GetNextTpn()
{
    std::lock_guard<std::mutex> lock(m_stateLock);
    return ReserveNextTpnLocked();
}

uint32_t TpConnectionManager::ReserveNextTpnLocked()
{
    while (m_tpnList.find(m_nextTpn) != m_tpnList.end() ||
           m_reservedTpnList.find(m_nextTpn) != m_reservedTpnList.end()) {
        ++m_nextTpn;
    }

    const uint32_t reservedTpn = m_nextTpn;
    m_reservedTpnList.insert(reservedTpn);
    ++m_nextTpn;
    return reservedTpn;
}

}
