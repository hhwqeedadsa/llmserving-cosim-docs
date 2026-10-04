#include "ns3/packet.h"
#include "ns3/ub-function.h"
#include "ns3/ub-transport.h"
#include <stdexcept>

static void Check(bool value)
{
    if (!value) throw std::runtime_error("transport retirement contract failed");
}

int main()
{
    auto tp = ns3::CreateObject<ns3::UbTransportChannel>();
    tp->SetInboundTpMsnReferenceForTest(7, 0x1000000ull);
    tp->SetInboundTaSsnReferenceForTest(7, 1, 0x10000u);
    tp->SetInboundTaSsnReferenceForTest(7, 2, 10);
    tp->EnqueueAckForTest(ns3::Create<ns3::Packet>(1));
    Check(!tp->CanRetireInboundTaReferences() && !tp->RetireInboundTaReferences());
    Check(tp->GetInboundTaReferenceCount() == 2 && tp->GetInboundTpReferenceCount() == 1);
    tp->PopAckForTest();
    Check(tp->CanRetireInboundTaReferences() && tp->RetireInboundTaReferences());
    Check(tp->GetInboundTaReferenceCount() == 0 && tp->GetInboundTpReferenceCount() == 1);
    Check(tp->RetireInboundTaReferences());
    for (unsigned wave = 0; wave < 32; ++wave)
    {
        tp->SetInboundTaSsnReferenceForTest(7, 3 + wave, 1);
        Check(tp->GetInboundTaReferenceCount() == 1);
        Check(tp->RetireInboundTaReferences() && tp->GetInboundTaReferenceCount() == 0);
    }
    tp->Dispose();
}
