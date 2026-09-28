#pragma once

#include "ub_write_transfer.hh"
#include "extern/helper/json/json.hpp"
#include "ns3/packet.h"
#include "ns3/nstime.h"
#include "ns3/ub-controller.h"
#include "ns3/ub-port.h"

#include <map>
#include <memory>
#include <optional>
#include <vector>

namespace moe_ub
{
struct ObservationConfig
{
    uint32_t switch_id, port_id;
    uint64_t start_ns, end_ns, period_ns;
    bool provenance;
    static ObservationConfig Parse(const nlohmann::json& input, uint64_t stop_ns);
};

struct ByteCounts
{
    uint64_t packets = 0, whole = 0, business = 0, control = 0, overhead = 0;
};

// No sampling events: bucket successful starts, and emit every configured window.
class PortWindows
{
  public:
    explicit PortWindows(ObservationConfig config);
    void Add(uint64_t time_ns, uint32_t whole, uint32_t business);
    const std::vector<ByteCounts>& Rows() const { return rows_; }
  private:
    ObservationConfig config_;
    std::vector<ByteCounts> rows_;
};

struct PayloadRange
{
    uint64_t task_begin;
    uint32_t packet_begin, bytes, packet_size;
    bool operator==(const PayloadRange&) const = default;
};

// Packet::Copy preserves UID. This bounded RTP fixture does not fragment packets.
// A UID collision with different ranges, or missing provenance, is an error.
class PayloadLedger
{
  public:
    explicit PayloadLedger(WriteDescriptor descriptor) : descriptor_(std::move(descriptor)) {}
    void Created(ns3::Ptr<const ns3::Packet> packet, uint32_t task, uint64_t task_begin,
                 uint32_t packet_begin, uint32_t bytes);
    PayloadRange Lookup(ns3::Ptr<const ns3::Packet> packet, uint32_t payload_bytes) const;
  private:
    WriteDescriptor descriptor_;
    std::map<uint64_t, PayloadRange> ranges_;
};

// Returns only WRITE business body bytes. Credit/TP/TA acknowledgments return zero.
uint32_t WritePayloadBytes(ns3::Ptr<const ns3::Packet> packet);

class PortObservation
{
  public:
    PortObservation(ObservationConfig config, WriteDescriptor descriptor,
                    ns3::Ptr<ns3::UbController> source);
    ~PortObservation();
    PortObservation(const PortObservation&) = delete;
    PortObservation& operator=(const PortObservation&) = delete;
    void Export(const UbWriteTransfer& transfer) const;
  private:
    void PacketStart(ns3::Ptr<ns3::Packet> packet, ns3::Time completion_delay);
    ObservationConfig config_;
    WriteDescriptor descriptor_;
    PortWindows windows_;
    ns3::Ptr<ns3::UbController> source_;
    ns3::Ptr<ns3::UbPort> port_;
    std::shared_ptr<PayloadLedger> ledger_;
    uint64_t initial_tx_bytes_ = 0, all_bytes_ = 0, all_packets_ = 0;
    uint64_t fingerprint_ = 14695981039346656037ULL;
    nlohmann::json packet_ranges_ = nlohmann::json::array();
};
}
