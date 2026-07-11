/*
 * SPDX-License-Identifier: GPL-2.0-only
 */

/**
 * @file
 * @ingroup mtp
 *
 * Verify that an absolute-time delivery cannot insert an event behind the LP's
 * current simulation time.
 */

#include "ns3/core-module.h"
#include "ns3/mtp-interface.h"
#include "ns3/network-module.h"

#include <iostream>
#include <limits>

using namespace ns3;

namespace
{

int64_t g_eventTimeNs = std::numeric_limits<int64_t>::max();

void
RunAbsoluteTimeEvent()
{
    g_eventTimeNs = Simulator::Now().GetNanoSeconds();
}

void
InjectAbsoluteTimeEvent(uint32_t nodeId, Time targetTime)
{
    MtpInterface::GetSystem()->ScheduleAt(nodeId, targetTime, MakeEvent(&RunAbsoluteTimeEvent));
}

void
ScheduleLateDelivery(uint32_t nodeId, Time targetTime)
{
    Simulator::Schedule(NanoSeconds(10), &InjectAbsoluteTimeEvent, nodeId, targetTime);
}

void
SendZeroDelayRemoteEvent(uint32_t receiverNodeId)
{
    Simulator::ScheduleWithContext(receiverNodeId, TimeStep(0), &RunAbsoluteTimeEvent);
}

void
AdvanceReceiverTime()
{
}

} // namespace

int
main(int argc, char* argv[])
{
    bool testing = false;
    bool sameTime = false;
    bool remotePast = false;
    bool negativeTime = false;
    CommandLine cmd(__FILE__);
    cmd.AddValue("test", "Print regression-test output", testing);
    cmd.AddValue("same-time",
                 "Schedule the absolute-time event at the worker partition's current time",
                 sameTime);
    cmd.AddValue("remote-past",
                 "Send a remote event behind the receiver worker partition's current time",
                 remotePast);
    cmd.AddValue("negative-time",
                 "Schedule the absolute-time event at a negative time",
                 negativeTime);
    cmd.Parse(argc, argv);

    NS_ABORT_MSG_IF(sameTime && remotePast, "same-time and remote-past are mutually exclusive");
    NS_ABORT_MSG_IF(negativeTime && (sameTime || remotePast),
                    "negative-time is mutually exclusive with same-time and remote-past");
    if (remotePast)
    {
        MtpInterface::Enable(1, 2);
        NodeContainer sender;
        sender.Create(1, 1);
        NodeContainer receiver;
        receiver.Create(1, 2);

        // With no modeled link between these LPs, a zero-delay cross-LP event violates lookahead.
        // LP 2 reaches 5ns before it receives the event produced by LP 1 at 1ns.
        Simulator::ScheduleWithContext(sender.Get(0)->GetId(),
                                       NanoSeconds(1),
                                       &SendZeroDelayRemoteEvent,
                                       receiver.Get(0)->GetId());
        Simulator::ScheduleWithContext(receiver.Get(0)->GetId(),
                                       NanoSeconds(5),
                                       &AdvanceReceiverTime);
    }
    else
    {
        MtpInterface::Enable(1, 1);
        NodeContainer nodes;
        nodes.Create(1, 1);

        Simulator::ScheduleWithContext(
            nodes.Get(0)->GetId(),
            TimeStep(0),
            &ScheduleLateDelivery,
            nodes.Get(0)->GetId(),
            negativeTime ? Time(-1) : (sameTime ? NanoSeconds(10) : NanoSeconds(5)));
    }
    Simulator::Run();

    const bool passed = !remotePast && sameTime && g_eventTimeNs == 10;
    if (testing)
    {
        std::cout << "TEST : 0 : " << (passed ? "PASSED" : "FAILED")
                  << " event-time-ns=" << g_eventTimeNs << std::endl;
    }

    Simulator::Destroy();
    return passed ? 0 : 1;
}
