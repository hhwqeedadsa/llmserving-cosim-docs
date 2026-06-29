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

#include <algorithm>
#include <fstream>
#include <tuple>
#include <utility>

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

void UbTrafficGen::AddTask(TrafficRecord record)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    uint32_t taskId = record.taskId;
    if (m_tasks.find(taskId) != m_tasks.end()) {
        NS_LOG_ERROR("TaskId " << taskId << " already exists, cannot add duplicate task!");
        return;
    }
    m_tasks[taskId] = record;
    for (const auto &dependId : record.dependOnPhases) {
        m_dependencies[taskId].insert(m_dependOnPhasesToTaskId[dependId].begin(),
            m_dependOnPhasesToTaskId[dependId].end());
    }

    // 设置初始状态
    if (m_dependencies[taskId].empty()) {
        m_taskStates[taskId] = TaskState::READY;
        m_readyTasks.insert(taskId);
    } else {
        m_taskStates[taskId] = TaskState::PENDING;
    }

    // 建立反向依赖映射
    for (uint32_t depId : m_dependencies[taskId]) {
        m_dependents[depId].insert(taskId);
    }

    NS_LOG_DEBUG("Added task " << taskId << " with " << m_dependencies[taskId].size() << " dependencies");
}

void
UbTrafficGen::SetPhaseDepend(uint32_t phaseId, uint32_t taskId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_dependOnPhasesToTaskId[phaseId].insert(taskId);
}

TrafficRecord UbTrafficGen::GetTaskById(uint32_t taskId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    auto it = m_tasks.find(taskId);
    if (it != m_tasks.end()) {
        return it->second;
    }
    NS_ABORT_MSG("Can't find task from TrafficRecord.");
}

void UbTrafficGen::MarkTaskCompleted(uint32_t taskId)
{
    Time completionVisibleTs;
    {
        std::lock_guard<std::mutex> lock(m_mutex);

        auto stateIt = m_taskStates.find(taskId);
        if (stateIt == m_taskStates.end() || stateIt->second != TaskState::RUNNING) {
            return;
        }

        completionVisibleTs = Simulator::Now() + ResolveCompletionVisibleDelayLocked();
    }

    ScheduleTaskCompletionVisibility(taskId, completionVisibleTs);
}

void
UbTrafficGen::SetDependencyVisibilityDelay(Time delay)
{
    NS_ABORT_MSG_IF(delay.IsStrictlyNegative(),
                    "dependency visibility delay must be non-negative");
    std::lock_guard<std::mutex> lock(m_mutex);
    m_dependencyVisibilityDelay = delay;
}

bool
UbTrafficGen::HasDependencyVisibilityDelay() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_dependencyVisibilityDelay.has_value() || m_minRemoteLinkDelay.has_value();
}

Time
UbTrafficGen::GetDependencyVisibilityDelay() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return ResolveCompletionVisibleDelayLocked();
}

void
UbTrafficGen::ValidateDependencyVisibilityDelay() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    NS_ABORT_MSG_IF(
        HasDependenciesLocked() && !m_dependencyVisibilityDelay.has_value() &&
            !m_minRemoteLinkDelay.has_value(),
        "traffic.csv contains task dependencies but no dependency visibility delay is available. "
        "Pass --dependency-visibility-delay=<Time> or add remote link.");
}

void
UbTrafficGen::ObserveRemoteLinkDelay(Time delay)
{
    NS_ABORT_MSG_IF(delay.IsStrictlyNegative(), "remote link delay must be non-negative");
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!m_minRemoteLinkDelay.has_value() || delay < *m_minRemoteLinkDelay) {
        m_minRemoteLinkDelay = delay;
    }
}

void
UbTrafficGen::EnableCanonicalOutput(std::string path, uint32_t rank)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_canonicalOutputEnabled = true;
    m_canonicalOutputPath = std::move(path);
    m_canonicalRank = rank;
    m_canonicalEvents.clear();
}

void
UbTrafficGen::RecordCanonicalEvent(const std::string& type, const TrafficRecord& task)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!m_canonicalOutputEnabled || !ShouldRecordCanonicalEventLocked(task)) {
        return;
    }

    m_canonicalEvents.push_back(CanonicalEvent{
        static_cast<uint64_t>(Simulator::Now().GetNanoSeconds()),
        type,
        static_cast<uint32_t>(task.taskId),
        static_cast<uint32_t>(task.sourceNode),
        static_cast<uint32_t>(task.destNode),
    });
}

void
UbTrafficGen::RecordCanonicalCompletionLocked(uint32_t taskId)
{
    if (!m_canonicalOutputEnabled) {
        return;
    }

    auto taskIt = m_tasks.find(taskId);
    NS_ABORT_MSG_IF(taskIt == m_tasks.end(), "Unknown taskId in canonical completion");
    if (!ShouldRecordCanonicalEventLocked(taskIt->second)) {
        return;
    }

    m_canonicalEvents.push_back(CanonicalEvent{
        static_cast<uint64_t>(Simulator::Now().GetNanoSeconds()),
        "COMPLETE_VISIBLE",
        taskId,
        static_cast<uint32_t>(taskIt->second.sourceNode),
        static_cast<uint32_t>(taskIt->second.destNode),
    });
}

bool
UbTrafficGen::ShouldRecordCanonicalEventLocked(const TrafficRecord& task) const
{
#ifdef NS3_MPI
    if (MpiInterface::IsEnabled() && MpiInterface::GetSize() > 1) {
        Ptr<Node> sourceNode = NodeList::GetNode(task.sourceNode);
        return UbUtils::ExtractMpiRank(sourceNode->GetSystemId()) == m_canonicalRank;
    }
#endif
    return true;
}

void
UbTrafficGen::WriteCanonicalOutput() const
{
    std::vector<CanonicalEvent> events;
    std::string outputPath;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (!m_canonicalOutputEnabled) {
            return;
        }
        events = m_canonicalEvents;
        outputPath = m_canonicalOutputPath + ".rank" + std::to_string(m_canonicalRank) + ".txt";
    }

    std::sort(events.begin(), events.end(), [](const CanonicalEvent& lhs, const CanonicalEvent& rhs) {
        return std::tie(lhs.timeNs, lhs.type, lhs.taskId, lhs.sourceNode, lhs.destNode) <
               std::tie(rhs.timeNs, rhs.type, rhs.taskId, rhs.sourceNode, rhs.destNode);
    });

    std::ofstream output(outputPath);
    NS_ABORT_MSG_IF(!output.is_open(), "cannot open canonical output file: " << outputPath);
    for (const auto& event : events) {
        output << event.timeNs << ',' << event.type << ',' << event.taskId << ','
               << event.sourceNode << ',' << event.destNode << '\n';
    }
}

void
UbTrafficGen::ApplyTaskCompletion(uint32_t taskId)
{
    std::vector<TrafficRecord> readyTasks;
    {
        std::lock_guard<std::mutex> lock(m_mutex);

        auto stateIt = m_taskStates.find(taskId);
        NS_ABORT_MSG_IF(stateIt == m_taskStates.end(), "Unknown completed taskId " << taskId);
        if (stateIt->second == TaskState::COMPLETED) {
            return;
        }

        stateIt->second = TaskState::COMPLETED;
        RecordCanonicalCompletionLocked(taskId);
        NS_LOG_DEBUG("Task " << taskId << " completion visible");

        auto dependentIt = m_dependents.find(taskId);
        if (dependentIt != m_dependents.end()) {
            for (uint32_t dependentId : dependentIt->second) {
                auto dependentStateIt = m_taskStates.find(dependentId);
                if (dependentStateIt == m_taskStates.end() ||
                    dependentStateIt->second != TaskState::PENDING) {
                    continue;
                }

                bool allDepsCompleted = true;
                auto depIt = m_dependencies.find(dependentId);
                if (depIt != m_dependencies.end()) {
                    for (uint32_t depId : depIt->second) {
                        auto depStateIt = m_taskStates.find(depId);
                        if (depStateIt == m_taskStates.end() ||
                            depStateIt->second != TaskState::COMPLETED) {
                            allDepsCompleted = false;
                            break;
                        }
                    }
                }

                if (allDepsCompleted) {
                    dependentStateIt->second = TaskState::READY;
                    m_readyTasks.insert(dependentId);
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
#ifdef NS3_MPI
    const bool filterByOwner = MpiInterface::IsEnabled() && MpiInterface::GetSize() > 1;
    uint32_t currentRank = 0;
    if (filterByOwner) {
        currentRank = MpiInterface::GetSystemId();
    }
#else
    const bool filterByOwner = false;
    const uint32_t currentRank = 0;
#endif
    return CollectReadyTasksLocked(currentRank, filterByOwner);
}

std::vector<TrafficRecord>
UbTrafficGen::CollectReadyTasksLocked(uint32_t currentRank, bool filterByOwner)
{
    std::vector<TrafficRecord> readyTasks;
    for (auto it = m_readyTasks.begin(); it != m_readyTasks.end();) {
        uint32_t taskId = *it;
        auto stateIt = m_taskStates.find(taskId);
        auto taskIt = m_tasks.find(taskId);
        if (stateIt != m_taskStates.end() && stateIt->second == TaskState::READY &&
            taskIt != m_tasks.end()) {
            const bool ownedByCurrentRank =
                !filterByOwner ||
                UbUtils::ExtractMpiRank(NodeList::GetNode(taskIt->second.sourceNode)->GetSystemId()) ==
                    currentRank;
            if (!ownedByCurrentRank) {
                ++it;
                continue;
            }

            stateIt->second = TaskState::RUNNING;
            readyTasks.push_back(taskIt->second);
            it = m_readyTasks.erase(it);
            continue;
        }
        ++it;
    }
    return readyTasks;
}

bool
UbTrafficGen::HasDependenciesLocked() const
{
    for (const auto& dependencyPair : m_dependencies) {
        if (!dependencyPair.second.empty()) {
            return true;
        }
    }
    return false;
}

Time
UbTrafficGen::ResolveCompletionVisibleDelayLocked() const
{
    if (m_dependencyVisibilityDelay.has_value()) {
        return *m_dependencyVisibilityDelay;
    }
    if (m_minRemoteLinkDelay.has_value()) {
        return *m_minRemoteLinkDelay;
    }
    return Time(0);
}

void
UbTrafficGen::ScheduleTaskCompletionVisibility(uint32_t taskId, Time completionVisibleTs)
{
    const Time now = Simulator::Now();
    NS_ABORT_MSG_IF(completionVisibleTs < now,
                    "task completion visibility timestamp is earlier than Simulator::Now()");

#ifdef NS3_MPI
    if (MpiInterface::IsEnabled() && MpiInterface::GetSize() > 1) {
        for (uint32_t rank = 0; rank < MpiInterface::GetSize(); ++rank) {
            if (rank == MpiInterface::GetSystemId()) {
                continue;
            }
            MpiInterface::SendTaskCompletion(taskId, completionVisibleTs, rank);
        }
    }
#endif

    Simulator::Schedule(completionVisibleTs - now,
                        &UbTrafficGen::ApplyTaskCompletion,
                        this,
                        taskId);
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
        Simulator::ScheduleWithContext(app->GetNode()->GetId(),
                                       taskDelay,
                                       &UbTrafficGen::StartTask,
                                       this,
                                       app,
                                       task);
        NS_LOG_DEBUG("Scheduled task " << task.taskId);
    }
}

void
UbTrafficGen::StartTask(Ptr<UbApp> app, TrafficRecord task)
{
    RecordCanonicalEvent("START", task);
    app->SendTraffic(task);
}

void UbTrafficGen::OnTaskCompleted(uint32_t taskId)
{
    MarkTaskCompleted(taskId);
}

void
UbTrafficGen::RegisterMpiTaskCompletionHandler()
{
#ifdef NS3_MPI
    if (MpiInterface::IsEnabled() && MpiInterface::GetSize() > 1) {
        MpiInterface::SetTaskCompletionHandler(
            [this](uint32_t taskId) { this->ApplyTaskCompletion(taskId); });
    }
#endif
}

} // namespace ns3
