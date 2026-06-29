// SPDX-License-Identifier: GPL-2.0-only
#include "ub-traffic-gen.h"
#include "ns3/log.h"
#include "ns3/simulator.h"
#include "ns3/uinteger.h"
#include "ns3/callback.h"
#include "ns3/ub-datatype.h"
#include "ns3/ub-function.h"
#include "ub-app.h"
#include "ub-utils.h"

#ifdef NS3_MPI
#include "ns3/mpi-interface.h"
#endif

namespace ns3 {

NS_LOG_COMPONENT_DEFINE("UbTrafficGen");

NS_OBJECT_ENSURE_REGISTERED(UbTrafficGen);
TypeId UbTrafficGen::GetTypeId(void)
{
    static TypeId tid = TypeId("ns3::UbTrafficGen")
            .SetParent<Application>()
            .SetGroupName("UnifiedBus")
            .AddConstructor<UbTrafficGen>();
    return tid;
}

UbTrafficGen::UbTrafficGen()
{
}

UbTrafficGen::~UbTrafficGen()
{
}

bool
UbTrafficGen::IsMultiProcessRuntimeUnsupported()
{
#ifdef NS3_MPI
    return MpiInterface::IsEnabled() && MpiInterface::GetSize() > 1;
#else
    return false;
#endif
}

void UbTrafficGen::AddTask(TrafficRecord record)
{
    NS_ABORT_MSG_IF(IsMultiProcessRuntimeUnsupported(), GetMultiProcessUnsupportedMessage());
    std::lock_guard<std::mutex> lock(m_mutex);
    uint32_t taskId = record.taskId;
    if (m_tasks.find(taskId) != m_tasks.end()) {
        NS_LOG_ERROR("TaskId " << taskId << " already exists, cannot add duplicate task!");
        return;
    }
    m_tasks[taskId] = record;
    RegisterTaskPhaseLocked(record.phaseId, taskId);

    uint32_t pendingPhaseCount = 0;
    std::set<uint32_t> uniqueDependencyPhases;
    for (const auto& dependPhaseId : record.dependOnPhases) {
        auto& phaseState = m_phaseStates[dependPhaseId];
        const bool inserted = uniqueDependencyPhases.insert(dependPhaseId).second;
        if (inserted && phaseState.completedTasks < phaseState.totalTasks) {
            phaseState.dependentTasks.insert(taskId);
            ++pendingPhaseCount;
        }
    }
    m_pendingPhaseCounts[taskId] = pendingPhaseCount;

    // 设置初始状态
    if (pendingPhaseCount == 0) {
        m_taskStates[taskId] = TaskState::READY;
        m_readyTasks.insert(taskId);
    } else {
        m_taskStates[taskId] = TaskState::PENDING;
    }

    NS_LOG_DEBUG("Added task " << taskId << " with " << uniqueDependencyPhases.size()
                               << " phase dependencies");
}

void
UbTrafficGen::SetPhaseDepend(uint32_t phaseId, uint32_t taskId)
{
    NS_ABORT_MSG_IF(IsMultiProcessRuntimeUnsupported(), GetMultiProcessUnsupportedMessage());
    std::lock_guard<std::mutex> lock(m_mutex);
    RegisterTaskPhaseLocked(phaseId, taskId);
}

void
UbTrafficGen::RegisterTaskPhaseLocked(uint32_t phaseId, uint32_t taskId)
{
    auto existingPhaseIt = m_taskPhaseIds.find(taskId);
    if (existingPhaseIt != m_taskPhaseIds.end()) {
        return;
    }

    m_taskPhaseIds[taskId] = phaseId;
    auto& phaseState = m_phaseStates[phaseId];
    ++phaseState.totalTasks;
}

TrafficRecord UbTrafficGen::GetTaskById(uint32_t taskId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto it = m_tasks.find(taskId);
    if (it != m_tasks.end()) {
        return it->second;
    } else {
        NS_ASSERT_MSG(0, "Can't find task from TrafficRecord.");
    }
}

void UbTrafficGen::MarkTaskCompleted(uint32_t taskId)
{
    std::vector<TrafficRecord> readyTasks;
    {
        std::lock_guard<std::mutex> lock(m_mutex);

        auto stateIt = m_taskStates.find(taskId);
        if (stateIt == m_taskStates.end() || stateIt->second != TaskState::RUNNING) {
            return;
        }

        stateIt->second = TaskState::COMPLETED;
        NS_LOG_DEBUG("Task " << taskId << " completed");

        auto phaseIt = m_taskPhaseIds.find(taskId);
        if (phaseIt != m_taskPhaseIds.end()) {
            auto phaseStateIt = m_phaseStates.find(phaseIt->second);
            if (phaseStateIt != m_phaseStates.end()) {
                ++phaseStateIt->second.completedTasks;

                if (phaseStateIt->second.completedTasks >= phaseStateIt->second.totalTasks) {
                    for (uint32_t dependentId : phaseStateIt->second.dependentTasks) {
                        auto dependentStateIt = m_taskStates.find(dependentId);
                        if (dependentStateIt == m_taskStates.end() ||
                            dependentStateIt->second != TaskState::PENDING) {
                            continue;
                        }

                        auto pendingIt = m_pendingPhaseCounts.find(dependentId);
                        if (pendingIt == m_pendingPhaseCounts.end() || pendingIt->second == 0) {
                            continue;
                        }

                        --pendingIt->second;
                        if (pendingIt->second == 0) {
                            dependentStateIt->second = TaskState::READY;
                            m_readyTasks.insert(dependentId);
                        }
                    }
                    phaseStateIt->second.dependentTasks.clear();
                }
            }
        }

        readyTasks = CollectReadyTasksLocked();
    }

    ScheduleTasks(readyTasks);

    if (IsCompleted()) {
        NS_LOG_DEBUG("[APPLICATION INFO] All tasks completed");
    }
}

bool UbTrafficGen::IsCompleted() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    for (const auto &statePair : m_taskStates)
        if (statePair.second != TaskState::COMPLETED) {
            return false;
        }

    return true;
}

uint32_t UbTrafficGen::GetCompletedTaskCount() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    uint32_t completedTasks = 0;
    for (const auto& statePair : m_taskStates)
    {
        if (statePair.second == TaskState::COMPLETED)
        {
            ++completedTasks;
        }
    }
    return completedTasks;
}

uint64_t
UbTrafficGen::GetDependencyReferenceCountForTesting() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    uint64_t referenceCount = 0;
    for (const auto& phaseState : m_phaseStates) {
        referenceCount += phaseState.second.dependentTasks.size();
    }
    return referenceCount;
}

uint32_t
UbTrafficGen::GetPendingPhaseCountForTesting(uint32_t taskId) const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto pendingIt = m_pendingPhaseCounts.find(taskId);
    if (pendingIt == m_pendingPhaseCounts.end()) {
        return 0;
    }
    return pendingIt->second;
}

void UbTrafficGen::ScheduleNextTasks()
{
    std::vector<TrafficRecord> readyTasks;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        readyTasks = CollectReadyTasksLocked();
    }
    ScheduleTasks(readyTasks);
}

std::vector<TrafficRecord> UbTrafficGen::CollectReadyTasksLocked()
{
    std::vector<TrafficRecord> readyTasks;
    for (auto it = m_readyTasks.begin(); it != m_readyTasks.end();) {
        uint32_t taskId = *it;
        auto stateIt = m_taskStates.find(taskId);
        if (stateIt != m_taskStates.end() && stateIt->second == TaskState::READY) {
            stateIt->second = TaskState::RUNNING;
            auto taskIt = m_tasks.find(taskId);
            if (taskIt != m_tasks.end()) {
                readyTasks.push_back(taskIt->second);
            }
        }
        it = m_readyTasks.erase(it);
    }
    return readyTasks;
}

void UbTrafficGen::ScheduleTasks(const std::vector<TrafficRecord>& tasks)
{
    for (const auto& task : tasks) {
        if (task.priority == 0) {
            NS_LOG_WARN("It is strongly recommended not to set the task priority to 0. " <<
                        "Priority level 0 is reserved for control frames.");
        }
        auto app = DynamicCast<UbApp>(NodeList::GetNode(task.sourceNode)->GetApplication(0));
        Time taskDelay = Time(0);
        if (!task.delay.empty()) {
            taskDelay = Time(task.delay);
        }
        Simulator::ScheduleWithContext(app->GetNode()->GetId(), taskDelay, &UbApp::SendTraffic, app, task);
        NS_LOG_DEBUG("Scheduled task " << task.taskId);
    }
}

void UbTrafficGen::OnTaskCompleted(uint32_t taskId)
{
    MarkTaskCompleted(taskId);
}

} // namespace ns3
