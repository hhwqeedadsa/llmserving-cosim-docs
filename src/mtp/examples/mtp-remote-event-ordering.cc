/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

/**
 * @file
 * @ingroup mtp
 *
 * Verify stable ordering for same-time remote events from one worker LP.
 */

#include "ns3/core-module.h"
#include "ns3/mtp-interface.h"
#include "ns3/network-module.h"

#include <iostream>
#include <vector>

using namespace ns3;

namespace
{

std::vector<uint32_t> g_receivedValues;
bool g_wrongLp = false;

void
ReceiveValue(uint32_t value)
{
    g_wrongLp |= Simulator::GetSystemId() != 2;
    g_receivedValues.push_back(value);
}

void
SendSameTimeEvents(uint32_t receiverNodeId)
{
    Simulator::ScheduleWithContext(receiverNodeId, NanoSeconds(10), &ReceiveValue, 1);
    Simulator::ScheduleWithContext(receiverNodeId, NanoSeconds(10), &ReceiveValue, 2);
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
    NodeContainer sender;
    sender.Create(1, 1);
    NodeContainer receiver;
    receiver.Create(1, 2);

    Simulator::ScheduleWithContext(sender.Get(0)->GetId(),
                                   NanoSeconds(1),
                                   &SendSameTimeEvents,
                                   receiver.Get(0)->GetId());
    Simulator::Run();

    const bool passed = !g_wrongLp && g_receivedValues == std::vector<uint32_t>{1, 2};
    if (testing)
    {
        std::cout << "TEST : 0 : " << (passed ? "PASSED" : "FAILED") << " order=";
        for (uint32_t value : g_receivedValues)
        {
            std::cout << value;
        }
        std::cout << " wrong-lp=" << g_wrongLp << std::endl;
    }

    Simulator::Destroy();
    return passed ? 0 : 1;
}
