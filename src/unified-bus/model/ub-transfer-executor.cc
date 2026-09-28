// SPDX-License-Identifier: GPL-2.0-only
#include "ub-transfer-executor.h"

#include "ub-app.h"
#include "ns3/simulator.h"
#include "ns3/ub-function.h"

#include <stdexcept>
#include <utility>

namespace ns3
{
UbTransferExecutor::UbTransferExecutor(UbApp& app) : m_app(app) {}

UbTransferExecutor::~UbTransferExecutor()
{
    for (auto& [id, context] : m_pending)
    {
        context.completionEvent.Cancel();
    }
}

void UbTransferExecutor::Submit(Task task, Completion completed, bool forceCtp)
{
    if (!completed || m_pending.contains(task.taskId))
    {
        throw std::invalid_argument("A task needs a completion receiver and a unique active id");
    }
    if (!m_app.GetNode() ||
        task.priority > UB_PRIORITY_MAX || task.op == UbTrafficGen::RuntimeTaskOp::UNKNOWN)
    {
        throw std::invalid_argument("UB task needs an attached app, valid priority and operation");
    }
    const bool rtp = !forceCtp && m_app.GetTransportMode() == TransportMode::RTP;
    m_pending.emplace(task.taskId, Context{task, std::move(completed), rtp, false, {}});
    try
    {
        if (forceCtp)
        {
            m_app.ExecuteCtpUrmaTraffic(task);
        }
        else
        {
            m_app.ExecuteTraffic(task);
        }
    }
    catch (...)
    {
        m_pending.erase(task.taskId);
        throw;
    }
}

void UbTransferExecutor::Complete(uint32_t taskId, uint32_t jettyNum)
{
    auto& context = m_pending.at(taskId);
    if (context.completing)
    {
        throw std::logic_error("Duplicate UB transaction completion");
    }
    context.completing = true;
    // The Jetty callback is inside its WQE iteration. Release it after that stack returns.
    context.completionEvent = Simulator::ScheduleNow(
        &UbTransferExecutor::Finish, this, taskId, jettyNum, false);
}

void UbTransferExecutor::CompleteMemory(uint32_t taskId)
{
    auto& context = m_pending.at(taskId);
    if (context.completing)
    {
        throw std::logic_error("Duplicate UB memory completion");
    }
    context.completing = true;
    context.completionEvent = Simulator::ScheduleNow(
        &UbTransferExecutor::Finish, this, taskId, 0, true);
}

void UbTransferExecutor::Finish(uint32_t taskId, uint32_t jettyNum, bool memory)
{
    auto context = std::move(m_pending.at(taskId));
    if (!memory && context.rtp)
    {
        auto controller = m_app.GetNode()->GetObject<UbController>();
        // Preserve the configured TP retention policy and the original cleanup implementation.
        controller->GetTpConnManager()->RemoveUselessTps(
            jettyNum, context.task.sourceNode, context.task.destNode, context.task.priority);
        controller->GetUbTransaction()->ReleaseCompletedJetty(jettyNum);
        controller->GetUbFunction()->DestroyJetty(jettyNum);
    }
    // CTP Jetties are deliberately cached and shared; only their task context ends here.
    m_pending.erase(taskId);
    context.completed(taskId);
}
}
