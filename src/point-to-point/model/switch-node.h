#ifndef SWITCH_NODE_H
#define SWITCH_NODE_H

#include <unordered_map>
#include <vector>
#include <ns3/node.h>
#include <ns3/traced-callback.h>
#include "qbb-net-device.h"
#include "switch-mmu.h"
#include "pint.h"

namespace ns3 {

class Packet;

class SwitchNode : public Node{
	static const uint32_t pCnt = 1025;	// Number of ports used
	static const uint32_t qCnt = 8;	// Number of queues/priorities used
	uint32_t m_ecmpSeed;
	std::unordered_map<uint32_t, std::vector<int> > m_rtTable; // map from ip address (u32) to possible ECMP port (index of dev)

	// monitor of PFC
	// Exact ingress/egress/priority attribution, dimensioned before traffic.
	std::vector<uint32_t> m_bytes;
	uint32_t m_counterPorts = 0;
	uint32_t& ByteCounter(uint32_t inDev, uint32_t outDev, uint32_t qidx);
	
	uint64_t m_txBytes[pCnt]; // counter of tx bytes

	uint32_t m_lastPktSize[pCnt];
	uint64_t m_lastPktTs[pCnt]; // ns
	double m_u[pCnt];

protected:
	bool m_ecnEnabled;
	uint32_t m_ccMode;
	uint64_t m_maxRtt;

	uint32_t m_ackHighPrio; // set high priority for ACK/NACK
	// Passive reason observation. No packet ownership or scheduling change.
	TracedCallback<Ptr<const Packet>, uint32_t, int32_t, uint32_t, uint32_t> m_switchDrop;

private:
	int GetOutDev(Ptr<const Packet>, CustomHeader &ch);
	void SendToDev(Ptr<Packet>p, CustomHeader &ch);
	static uint32_t EcmpHash(const uint8_t* key, size_t len, uint32_t seed);
	void CheckAndSendPfc(uint32_t inDev, uint32_t qIndex);
	void CheckAndSendResume(uint32_t inDev, uint32_t qIndex);
public:
	enum DropReason : uint32_t {
		INGRESS_ADMISSION = 1, EGRESS_ADMISSION = 2, NO_ROUTE = 3, LINK_DOWN = 4
	};
	typedef void (*SwitchDropCallback)(Ptr<const Packet>, uint32_t, int32_t, uint32_t, uint32_t);
	Ptr<SwitchMmu> m_mmu;

	static TypeId GetTypeId (void);
	SwitchNode();
	void InitializeByteCounters(uint32_t ports);
	uint32_t ByteCounterSlots() const { return m_counterPorts; }
	uint64_t ByteCounterStorageBytes() const { return uint64_t(m_bytes.size()) * sizeof(uint32_t); }
	uint32_t ReadByteCounter(uint32_t inDev, uint32_t outDev, uint32_t qidx) const;
	void SetEcmpSeed(uint32_t seed);
	void AddTableEntry(Ipv4Address &dstAddr, uint32_t intf_idx);
	void ClearTable();
	bool SwitchReceiveFromDevice(Ptr<NetDevice> device, Ptr<Packet> packet, CustomHeader &ch);
	void SwitchNotifyDequeue(uint32_t ifIndex, uint32_t qIndex, Ptr<Packet> p);
	// Remove a queued packet without transmit/ECN/PINT side effects.
	void SwitchNotifyDrop(uint32_t ifIndex, uint32_t qIndex, Ptr<const Packet> p);

	// for approximate calc in PINT
	int logres_shift(int b, int l);
	int log2apprx(int x, int b, int m, int l); // given x of at most b bits, use most significant m bits of x, calc the result in l bits
};

} /* namespace ns3 */

#endif /* SWITCH_NODE_H */
