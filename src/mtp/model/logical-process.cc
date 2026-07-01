/*
 * Copyright (c) 2023 State Key Laboratory for Novel Software Technology
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation;
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 *
 * Author: Songyuan Bai <i@f5soft.site>
 */

/**
 * \file
 * \ingroup mtp
 *  Implementation of classes ns3::LogicalProcess
 */

#include "logical-process.h"

#include "mtp-interface.h"

#include "ns3/channel.h"
#include "ns3/node-container.h"
#include "ns3/simulator.h"

#include <algorithm>
#include <tuple>

namespace ns3
{

NS_LOG_COMPONENT_DEFINE("LogicalProcess");

namespace
{

uint32_t
GetLocalSystemId(uint32_t systemId)
{
#ifdef NS3_MPI
    return systemId >> 16;
#else
    return systemId;
#endif
}

} // namespace

LogicalProcess::LogicalProcess()
    : m_systemId(0),
      m_systemCount(0),
      m_stop(false),
      m_uid(EventId::UID::VALID),
      m_currentContext(Simulator::NO_CONTEXT),
      m_currentUid(0),
      m_currentTs(0),
      m_eventCount(0),
      m_pendingEventCount(0),
      m_events(nullptr),
      m_lookAhead(TimeStep(0)),
      m_executionTime(0)
{
}

LogicalProcess::LogicalProcess(const LogicalProcess& other)
    : m_systemId(other.m_systemId),
      m_systemCount(other.m_systemCount),
      m_stop(other.m_stop.load(std::memory_order_acquire)),
      m_uid(other.m_uid),
      m_currentContext(other.m_currentContext),
      m_currentUid(other.m_currentUid),
      m_currentTs(other.m_currentTs),
      m_eventCount(other.m_eventCount),
      m_pendingEventCount(other.m_pendingEventCount),
      m_events(other.m_events),
      m_lookAhead(other.m_lookAhead),
      m_mailbox(other.m_mailbox),
      m_executionTime(other.m_executionTime)
{
}

LogicalProcess&
LogicalProcess::operator=(const LogicalProcess& other)
{
    if (this == &other)
    {
        return *this;
    }

    m_systemId = other.m_systemId;
    m_systemCount = other.m_systemCount;
    m_stop.store(other.m_stop.load(std::memory_order_acquire), std::memory_order_release);
    m_uid = other.m_uid;
    m_currentContext = other.m_currentContext;
    m_currentUid = other.m_currentUid;
    m_currentTs = other.m_currentTs;
    m_eventCount = other.m_eventCount;
    m_pendingEventCount = other.m_pendingEventCount;
    m_events = other.m_events;
    m_lookAhead = other.m_lookAhead;
    m_mailbox = other.m_mailbox;
    m_executionTime = other.m_executionTime;

    return *this;
}

LogicalProcess::~LogicalProcess()
{
    NS_LOG_INFO("system " << m_systemId << " finished with event count " << m_eventCount);

    if (m_events == nullptr)
    {
        return;
    }

    // if others hold references to event list, do not unref events
    if (m_events->GetReferenceCount() == 1)
    {
        while (!m_events->IsEmpty())
        {
            Scheduler::Event next = m_events->RemoveNext();
            next.impl->Unref();
        }
    }
}

void
LogicalProcess::Enable(const uint32_t systemId, const uint32_t systemCount)
{
    m_systemId = systemId;
    m_systemCount = systemCount;
    m_stop.store(false, std::memory_order_release);
}

void
LogicalProcess::CalculateLookAhead()
{
    NS_LOG_FUNCTION(this);

    if (m_systemId == 0)
    {
        m_lookAhead = TimeStep(0); // No lookahead for the public LP
    }
    else
    {
        m_lookAhead = Time::Max() / 2 - TimeStep(1);
        NodeContainer c = NodeContainer::GetGlobal();
        for (auto iter = c.Begin(); iter != c.End(); ++iter)
        {
#ifdef NS3_MPI
            // for hybrid simulation, the left 16-bit indicates local system ID,
            // and the right 16-bit indicates global system ID (MPI rank)
            if (((*iter)->GetSystemId() >> 16) != m_systemId)
            {
                continue;
            }
#else
            if ((*iter)->GetSystemId() != m_systemId)
            {
                continue;
            }
#endif
            for (uint32_t i = 0; i < (*iter)->GetNDevices(); ++i)
            {
                Ptr<NetDevice> localNetDevice = (*iter)->GetDevice(i);
                // only works for p2p links currently
                if (!localNetDevice->IsPointToPoint())
                {
                    continue;
                }
                Ptr<Channel> channel = localNetDevice->GetChannel();
                if (!channel)
                {
                    continue;
                }
                // grab the adjacent node
                Ptr<Node> remoteNode;
                if (channel->GetDevice(0) == localNetDevice)
                {
                    remoteNode = (channel->GetDevice(1))->GetNode();
                }
                else
                {
                    remoteNode = (channel->GetDevice(0))->GetNode();
                }
                // if it's not remote, don't consider it
                const uint32_t remoteSystemId = GetLocalSystemId(remoteNode->GetSystemId());
                if (remoteSystemId == m_systemId)
                {
                    continue;
                }
                // compare delay on the channel with current value of m_lookAhead.
                // if delay on channel is smaller, make it the new lookAhead.
                TimeValue delay;
                channel->GetAttribute("Delay", delay);
                if (delay.Get() < m_lookAhead)
                {
                    m_lookAhead = delay.Get();
                }
                // add the neighbour to the mailbox
                m_mailbox[remoteSystemId];
            }
        }
    }

    NS_LOG_INFO("lookahead of system " << m_systemId << " is set to " << m_lookAhead.GetTimeStep());
}

void
LogicalProcess::ReceiveMessages()
{
    NS_LOG_FUNCTION(this);

    m_pendingEventCount = 0;
    {
        std::lock_guard<std::mutex> lock(m_mailboxMutex);
        for (auto& [_, queue] : m_mailbox)
        {
            // Keep legacy per-sender UID assignment order; global ordering changes tie-breaks for
            // same-timestamp remote events and breaks byte-for-byte MTP output compatibility.
            if (queue.size() > 1)
            {
                std::sort(queue.begin(),
                          queue.end(),
                          [](const RemoteEvent& lhs, const RemoteEvent& rhs) {
                              return std::tie(lhs.senderTs,
                                              lhs.senderSystemId,
                                              lhs.senderUid,
                                              lhs.event) >
                                     std::tie(rhs.senderTs,
                                              rhs.senderSystemId,
                                              rhs.senderUid,
                                              rhs.event);
                          });
            }
            while (!queue.empty())
            {
                RemoteEvent& remoteEvent = queue.back();
                Scheduler::Event& ev = remoteEvent.event;
                ev.key.m_uid = m_uid++;
                m_events->Insert(ev);
                m_pendingEventCount++;
                queue.pop_back();
            }
        }
    }
}

void
LogicalProcess::BoundLookAhead(Time lookAhead)
{
    if (lookAhead.IsStrictlyPositive() && lookAhead < m_lookAhead)
    {
        m_lookAhead = lookAhead;
    }
}

void
LogicalProcess::ProcessOneRound()
{
    NS_LOG_FUNCTION(this);

    // set thread context
    MtpInterface::SetSystem(m_systemId);

    // calculate time window
    Time grantedTime =
        Min(MtpInterface::GetSmallestTime() + m_lookAhead, MtpInterface::GetNextPublicTime());

    auto start = std::chrono::system_clock::now();

    // process events
    while (Next() <= grantedTime)
    {
        Scheduler::Event next = m_events->RemoveNext();
        m_eventCount++;
        NS_LOG_LOGIC("handle " << next.key.m_ts);

        m_currentTs = next.key.m_ts;
        m_currentContext = next.key.m_context;
        m_currentUid = next.key.m_uid;

        next.impl->Invoke();
        next.impl->Unref();
    }

    auto end = std::chrono::system_clock::now();
    m_executionTime = std::chrono::duration_cast<std::chrono::nanoseconds>(end - start).count();
}

EventId
LogicalProcess::Schedule(const Time& delay, EventImpl* event)
{
    Scheduler::Event ev;

    ev.impl = event;
    ev.key.m_ts = m_currentTs + delay.GetTimeStep();
    ev.key.m_context = GetContext();
    ev.key.m_uid = m_uid++;
    m_events->Insert(ev);

    return EventId(event, ev.key.m_ts, ev.key.m_context, ev.key.m_uid);
}

void
LogicalProcess::ScheduleAt(const uint32_t context, const Time& time, EventImpl* event)
{
    Scheduler::Event ev;

    ev.impl = event;
    ev.key.m_ts = time.GetTimeStep();
    ev.key.m_context = context;
    ev.key.m_uid = m_uid++;
    m_events->Insert(ev);
}

void
LogicalProcess::ScheduleWithContext(LogicalProcess* remote,
                                    const uint32_t context,
                                    const Time& delay,
                                    EventImpl* event)
{
    Scheduler::Event ev;

    ev.impl = event;
    ev.key.m_ts = delay.GetTimeStep() + m_currentTs;
    ev.key.m_context = context;

    if (remote == this)
    {
        ev.key.m_uid = m_uid++;
        m_events->Insert(ev);
    }
    else
    {
        ev.key.m_uid = EventId::UID::INVALID;
        std::lock_guard<std::mutex> lock(remote->m_mailboxMutex);
        remote->m_mailbox[m_systemId].push_back(RemoteEvent{
            ev.key.m_ts,
            m_currentTs,
            m_systemId,
            // Preserve legacy remote-event tie-breaks; incrementing this changes same-time task
            // completion order in existing MTP scenarios.
            m_uid,
            ev,
        });
    }
}

void
LogicalProcess::InvokeNow(const Scheduler::Event& ev)
{
    uint32_t oldSystemId = MtpInterface::GetSystem()->GetSystemId();
    MtpInterface::SetSystem(m_systemId);

    m_eventCount++;
    NS_LOG_LOGIC("handle " << ev.key.m_ts);

    m_currentTs = ev.key.m_ts;
    m_currentContext = ev.key.m_context;
    m_currentUid = ev.key.m_uid;

    ev.impl->Invoke();
    ev.impl->Unref();

    // restore previous thread context
    MtpInterface::SetSystem(oldSystemId);
}

void
LogicalProcess::Remove(const EventId& id)
{
    if (IsExpired(id))
    {
        return;
    }
    Scheduler::Event event;

    event.impl = id.PeekEventImpl();
    event.key.m_ts = id.GetTs();
    event.key.m_context = id.GetContext();
    event.key.m_uid = id.GetUid();
    m_events->Remove(event);
    event.impl->Cancel();
    // whenever we remove an event from the event list, we have to unref it.
    event.impl->Unref();
}

bool
LogicalProcess::IsExpired(const EventId& id) const
{
    return id.PeekEventImpl() == nullptr || id.GetTs() < m_currentTs ||
           (id.GetTs() == m_currentTs && id.GetUid() <= m_currentUid) ||
           id.PeekEventImpl()->IsCancelled();
}

void
LogicalProcess::SetScheduler(ObjectFactory schedulerFactory)
{
    Ptr<Scheduler> scheduler = schedulerFactory.Create<Scheduler>();
    if (m_events)
    {
        while (!m_events->IsEmpty())
        {
            Scheduler::Event next = m_events->RemoveNext();
            scheduler->Insert(next);
        }
    }
    m_events = scheduler;
}

Time
LogicalProcess::Next() const
{
    if (m_stop.load(std::memory_order_acquire) || m_events->IsEmpty())
    {
        return Time::Max();
    }
    else
    {
        Scheduler::Event ev = m_events->PeekNext();
        return TimeStep(ev.key.m_ts);
    }
}

} // namespace ns3
