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
        Reject([&] { ledger.Created(data, 7, 65537, begin, 1); });
        Reject([&] { ledger.Lookup(copy, 2); });
        copy->RemoveAtEnd(1);
        Reject([&] { ledger.Lookup(copy, 1); });
        std::cout << "PASS: half-open/zero windows, packet parsing, ACK exclusion, exact ranges and diagnostics\n";
        return 0;
    }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
