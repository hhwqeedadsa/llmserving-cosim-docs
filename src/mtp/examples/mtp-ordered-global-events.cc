/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

/**
 * @file
 * @ingroup mtp
 *
 * Verify stable keyed ordering when worker LPs submit same-time public events.
 */

#include "ns3/core-module.h"
#include "ns3/mtp-interface.h"
#include "ns3/network-module.h"

#include <cstdint>
#include <iostream>
#include <utility>
#include <vector>

using namespace ns3;

namespace
{

constexpr uint32_t kBatchCount = 100;
std::vector<std::pair<uint32_t, uint32_t>> g_received;
bool g_wrongLp = false;
bool g_publicFollowupRan = false;

void
NoOp()
{
}

void
RecordValue(uint32_t batch, uint32_t value)
{
    g_wrongLp |= Simulator::GetSystemId() != 0;
    g_received.emplace_back(batch, value);
}

void
SubmitValue(Time targetTime, uint64_t orderKey, uint32_t batch, uint32_t value)
{
    MtpInterface::ScheduleGlobalAtOrdered(targetTime, orderKey, &RecordValue, batch, value);
}

void
RecordPublicFollowup()
{
    g_wrongLp |= Simulator::GetSystemId() != 0;
    g_publicFollowupRan = true;
}

void
SubmitPublicFollowup(Time targetTime)
{
    MtpInterface::ScheduleGlobalAtOrdered(targetTime, 0, &RecordPublicFollowup);
}

} // namespace

int
main(int argc, char* argv[])
{
    bool testing = false;
    CommandLine cmd(__FILE__);
    cmd.AddValue("test", "Print regression-test output", testing);
    cmd.Parse(argc, argv);

    MtpInterface::Enable(2, 2);
    NodeContainer highKeySender;
    highKeySender.Create(1, 1);
    NodeContainer lowKeySender;
    lowKeySender.Create(1, 2);

    for (uint32_t batch = 0; batch < kBatchCount; ++batch)
    {
        const Time highKeySubmitTime = NanoSeconds(batch * 20 + 1);
        const Time barrierTime = NanoSeconds(batch * 20 + 2);
        const Time lowKeySubmitTime = NanoSeconds(batch * 20 + 3);
        const Time targetTime = NanoSeconds(batch * 20 + 10);
        Simulator::ScheduleWithContext(highKeySender.Get(0)->GetId(),
                                       highKeySubmitTime,
                                       &SubmitValue,
                                       targetTime,
                                       2,
                                       batch,
                                       2);
        MtpInterface::ScheduleGlobalAt(barrierTime, &NoOp);
        Simulator::ScheduleWithContext(lowKeySender.Get(0)->GetId(),
                                       lowKeySubmitTime,
                                       &SubmitValue,
                                       targetTime,
                                       1,
                                       batch,
                                       1);
    }
    MtpInterface::ScheduleGlobalAt(NanoSeconds(kBatchCount * 20),
                                   &SubmitPublicFollowup,
                                   NanoSeconds(kBatchCount * 20 + 10));

    Simulator::Run();

    bool passed = !g_wrongLp && g_publicFollowupRan && g_received.size() == kBatchCount * 2;
    for (uint32_t batch = 0; passed && batch < kBatchCount; ++batch)
    {
        passed &= g_received[batch * 2] == std::make_pair(batch, 1u);
        passed &= g_received[batch * 2 + 1] == std::make_pair(batch, 2u);
    }

    if (testing)
    {
        std::cout << "TEST : 0 : " << (passed ? "PASSED" : "FAILED") << " batches=" << kBatchCount
                  << " wrong-lp=" << g_wrongLp << " public-followup=" << g_publicFollowupRan
                  << std::endl;
    }

    Simulator::Destroy();
    return passed ? 0 : 1;
}
