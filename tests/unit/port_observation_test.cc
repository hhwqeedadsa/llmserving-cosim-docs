#include "port_observation.hh"
#include "ns3/ub-header.h"
#include "ns3/ub-tag.h"
#include "ns3/ipv4-header.h"
#include "ns3/udp-header.h"

#include <iostream>
#include <stdexcept>
#include <vector>

namespace
{
void Check(bool ok, const char* message)
{
    if (!ok) throw std::runtime_error(message);
}
template<class F> void Reject(F function)
{
    bool rejected = false;
    try { function(); } catch (const std::runtime_error&) { rejected = true; }
    Check(rejected, "expected diagnostic rejection");
}
ns3::Ptr<ns3::Packet> MakeTestPacket(ns3::TaOpcode opcode, uint32_t bytes, bool transport_ack = false)
{
    auto packet = ns3::Create<ns3::Packet>(bytes);
    ns3::UbMAExtTah ext;
    ext.SetLength(bytes); packet->AddHeader(ext);
    ns3::UbTransactionHeader ta;
    ta.SetTaOpcode(opcode); packet->AddHeader(ta);
    ns3::UbTransportHeader tp;
    tp.SetTPOpcode(transport_ack ? static_cast<uint8_t>(ns3::TpOpcode::TP_OPCODE_ACK_WITHOUT_CETPH) : 1);
    packet->AddHeader(tp);
    ns3::UdpHeader udp; packet->AddHeader(udp);
    ns3::Ipv4Header ip; packet->AddHeader(ip);
    ns3::UbIpBasedNetworkHeader network; packet->AddHeader(network);
    ns3::UbDatalinkPacketHeader dl;
    dl.SetConfig(static_cast<uint8_t>(ns3::UbDatalinkHeaderConfig::PACKET_IPV4));
    packet->AddHeader(dl);
    packet->AddPacketTag(ns3::UbFlowTag(7, 65537));
    return packet;
}
}
int main()
{
    try
    {
        moe_ub::PortWindows windows({3, 0, 10, 41, 10, true});
        windows.Add(9, 99, 0); // excluded before start
        windows.Add(10, 30, 10);
        windows.Add(19, 20, 0); // a whole control packet
        windows.Add(20, 40, 25); // exact next-window boundary
        windows.Add(40, 10, 1); // partial last window
        windows.Add(41, 999, 0); // excluded at end
        const auto& rows = windows.Rows();
        Check(rows.size() == 4 && rows[0].packets == 2 && rows[0].whole == 50 &&
              rows[0].business == 10 && rows[0].overhead == 20 && rows[0].control == 20 &&
              rows[1].whole == 40 && rows[2].whole == 0 && rows[2].packets == 0 &&
              rows[3].whole == 10, "half-open windows, categories or zero window");
        Reject([&] { windows.Add(10, 1, 2); });
        moe_ub::PortWindows layout({3, 0, 0, 10, 10, false});
        layout.Add(0, 200, 100, 16, 28);
        layout.Add(1, 80, 0, 32, 0); // application status is WRITE data, not a protocol ACK
        const auto& classified = layout.Rows()[0];
        Check(classified.business == 100 && classified.metadata == 48 && classified.padding == 28 &&
              classified.overhead == 104 && classified.control == 0, "application bytes mixed with business/protocol control");
        Reject([&] { layout.Add(0, 20, 10, 8, 3); });
        moe_ub::ObservationConfig sparse_config{3,0,0,50001,10,true};
        sparse_config.resource_budget={{"max_windows_per_port",6000u}};
        moe_ub::PortWindows sparse(sparse_config);
        sparse.Add(10,30,10);sparse.Add(50000,40,20);
        Check(sparse.Count()==5001 && sparse.ResidentRows()==2 && sparse.Rows().empty() &&
              sparse.Row(0).whole==0 && sparse.Row(1).business==10 && sparse.Row(5000).whole==40,
              "sparse windows must retain only nonzero windows and preserve boundary counts");
        Reject([&] { sparse.Row(5001); });
        auto data = MakeTestPacket(ns3::TaOpcode::TA_OPCODE_WRITE, 1);
        std::vector<uint8_t> before(data->GetSize()), after(data->GetSize());
        data->CopyData(before.data(), before.size());
        Check(moe_ub::WritePayloadBytes(data) == 1, "one-byte business body");
        data->CopyData(after.data(), after.size());
        Check(before == after, "parser changed serialized packet");
        Check(moe_ub::WritePayloadBytes(MakeTestPacket(ns3::TaOpcode::TA_OPCODE_TRANSACTION_ACK, 1)) == 0,
              "TA ACK carrier/tag must not be attributed to business");
        Check(moe_ub::WritePayloadBytes(MakeTestPacket(ns3::TaOpcode::TA_OPCODE_WRITE, 1, true)) == 0,
              "TP ACK tag must not be attributed to business");
        moe_ub::PayloadLedger ledger({4294967319ULL, 7, "unit", 1, 2, 0, 1, 17, 65537});
        Reject([&] { ledger.Lookup(data, 1); });
        const uint32_t begin = data->GetSize() - 1;
        ledger.Created(data, 7, 65536, begin, 1);
        ledger.Created(data, 7, 65536, begin, 1); // idempotent source observation
        auto copy = data->Copy();
        Check(copy->GetUid() == data->GetUid() && ledger.Lookup(copy, 1).task_begin == 65536,
              "packet copy lost exact task tail range");
        moe_ub::PortWindows repeats({3, 0, 0, 1, 1, true});
        repeats.Add(0, data->GetSize(), ledger.Lookup(data, 1).bytes);
        repeats.Add(0, copy->GetSize(), ledger.Lookup(copy, 1).bytes);
        Check(repeats.Rows()[0].business == 2 && repeats.Rows()[0].packets == 2,
              "port counters must count repeated payload occurrences");
        Reject([&] { ledger.Created(data, 7, 0, begin, 1); });
        Reject([&] { ledger.Created(data, 8, 65536, begin, 1); });
        moe_ub::WriteDescriptor next{4294967320ULL, 8, "next-graph", 1, 2, 0, 1, 18, 65537};
        const auto* first_descriptor = &ledger.Descriptor(7);
        auto segmented = next;
        segmented.payload_regions = std::vector<moe_ub::PayloadRegion>{{0,65537,"business"}};
        Reject([&] { ledger.Register({segmented,segmented}); });
        Check(ledger.TransferCount()==1 && !ledger.HasPayloadRegions(),
              "failed batch changed count or byte-layout state");
        auto invalid = next; invalid.task_id = uint64_t{1} << 32;
        Reject([&] { ledger.Register({next,invalid}); });
        Check(ledger.TransferCount()==1, "narrowing task id partially registered batch");
        // A later duplicate must not partially append the earlier valid entry.
        Reject([&] { ledger.Register({next, {4294967319ULL, 7, "unit", 1, 2, 0, 1, 17, 65537}}); });
        Reject([&] { ledger.Created(data, 8, 65536, begin, 1); });
        ledger.Register({next});
        Check(&ledger.Descriptor(7)==first_descriptor && ledger.TransferCount()==2,
              "appending a graph copied or invalidated the existing descriptor table");
        ledger.SetLimits(1,2);
        Reject([&] { ledger.Register({{4294967321ULL,9,"limit",1,2,0,1,19,1}}); });
        Reject([&] { ledger.Created(MakeTestPacket(ns3::TaOpcode::TA_OPCODE_WRITE,1),7,65536,begin,1); });
        Check(ledger.PacketCount()==1,"rejected packet must not change provenance ledger");
        ledger.SetLimits(2,2);
        auto next_packet = MakeTestPacket(ns3::TaOpcode::TA_OPCODE_WRITE, 1);
        ns3::UbFlowTag old_tag;
        next_packet->RemovePacketTag(old_tag);
        next_packet->AddPacketTag(ns3::UbFlowTag(8, 65537));
        ledger.Created(next_packet, 8, 65536, begin, 1);
        Check(ledger.Lookup(next_packet, 1).task_id == 8 && ledger.Lookup(data, 1).task_id == 7,
              "appending graph provenance lost or confused existing packet ranges");
        Reject([&] { ledger.Created(data, 7, 65537, begin, 1); });
        Reject([&] { ledger.Lookup(copy, 2); });
        copy->RemoveAtEnd(1);
        Reject([&] { ledger.Lookup(copy, 1); });
        Reject([&] { ledger.SetLimits(1,2); });
        ledger.RestrictLimits(2,2);
        ledger.RestrictLimits(100,100);
        Reject([&] { ledger.Register({{4294967321ULL,9,"limit",1,2,0,1,19,1}}); });
        Check(ledger.TransferCount()==2 && ledger.PacketCount()==2 && ledger.CreatedNotifications()==3,
              "shared limits or failed source events changed stored records");
        std::cout << "PASS: half-open/zero windows, packet parsing, ACK exclusion, exact ranges and diagnostics\n";
        return 0;
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
