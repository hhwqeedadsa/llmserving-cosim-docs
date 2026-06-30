// SPDX-License-Identifier: GPL-2.0-only
#include "ub-traffic-gen.h"
#include <algorithm>
#include <limits>
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

void UbTrafficGen::AddTask(const TrafficRecord& record)
{
    NS_ABORT_MSG_IF(IsMultiProcessRuntimeUnsupported(), GetMultiProcessUnsupportedMessage());
    std::lock_guard<std::mutex> lock(m_mutex);
    AddTaskLocked(record);
}

void
UbTrafficGen::AddTaskDuringInitialLoad(const TrafficRecord& record)
{
    NS_ABORT_MSG_IF(IsMultiProcessRuntimeUnsupported(), GetMultiProcessUnsupportedMessage());
    AddTaskLocked(record);
}

void
UbTrafficGen::AddTaskLocked(const TrafficRecord& record)
{
    uint32_t taskId = record.taskId;
    if (!SetTaskLocked(ConvertToRuntimeTask(record))) {
        NS_LOG_ERROR("TaskId " << taskId << " already exists, cannot add duplicate task!");
        return;
    }
    RegisterTaskPhaseLocked(record.phaseId, taskId);

    uint32_t pendingPhaseCount = 0;
    const auto addDependencyPhase = [&](uint32_t dependPhaseId) {
        auto& phaseState = m_phaseStates[dependPhaseId];
        if (phaseState.completedTasks < phaseState.totalTasks) {
            phaseState.dependentTasks.push_back(taskId);
            ++pendingPhaseCount;
        }
    };

    uint32_t uniqueDependencyPhaseCount = static_cast<uint32_t>(record.dependOnPhases.size());
    if (record.dependOnPhases.size() == 1) {
        addDependencyPhase(record.dependOnPhases[0]);
    } else if (record.dependOnPhases.size() > 1) {
        std::vector<uint32_t> uniqueDependencyPhases = record.dependOnPhases;
        std::sort(uniqueDependencyPhases.begin(), uniqueDependencyPhases.end());
        uniqueDependencyPhases.erase(std::unique(uniqueDependencyPhases.begin(),
                                                 uniqueDependencyPhases.end()),
                                     uniqueDependencyPhases.end());
        uniqueDependencyPhaseCount = static_cast<uint32_t>(uniqueDependencyPhases.size());
        for (const auto& dependPhaseId : uniqueDependencyPhases) {
            addDependencyPhase(dependPhaseId);
        }
    }
    SetPendingPhaseCountLocked(taskId, pendingPhaseCount);

    // 设置初始状态
    if (pendingPhaseCount == 0) {
        SetTaskStateLocked(taskId, TaskState::READY);
        AddReadyTaskLocked(taskId);
    } else {
        SetTaskStateLocked(taskId, TaskState::PENDING);
    }

    NS_LOG_DEBUG("Added task " << taskId << " with " << uniqueDependencyPhaseCount
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
UbTrafficGen::SetPhaseDependDuringInitialLoad(uint32_t phaseId, uint32_t taskId)
{
    NS_ABORT_MSG_IF(IsMultiProcessRuntimeUnsupported(), GetMultiProcessUnsupportedMessage());
    RegisterTaskPhaseLocked(phaseId, taskId);
}

void
UbTrafficGen::ReserveTasksForTraffic(uint64_t recordCount, uint32_t maxTaskId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const auto reserveCount = static_cast<size_t>(std::min<uint64_t>(
        recordCount, static_cast<uint64_t>(std::numeric_limits<size_t>::max())));

    if (recordCount > 0 && maxTaskId != std::numeric_limits<uint32_t>::max() &&
        static_cast<uint64_t>(maxTaskId) + 1 == recordCount) {
        const auto denseReserve = static_cast<size_t>(maxTaskId) + 1;
        m_denseTasks.reserve(denseReserve);
        m_denseTaskPresent.reserve(denseReserve);
        m_denseTaskStates.reserve(denseReserve);
        m_denseTaskPhaseIds.reserve(denseReserve);
        m_densePendingPhaseCounts.reserve(denseReserve);
    } else {
        m_tasks.reserve(reserveCount);
        m_taskStates.reserve(reserveCount);
        m_taskPhaseIds.reserve(reserveCount);
        m_pendingPhaseCounts.reserve(reserveCount);
    }
}

void
UbTrafficGen::RegisterSourceAppDuringInitialLoad(uint32_t sourceNode, Ptr<UbApp> app)
{
    if (sourceNode >= m_sourceApps.size()) {
        m_sourceApps.resize(static_cast<size_t>(sourceNode) + 1);
    }
    m_sourceApps[sourceNode] = app;
}

UbTrafficGen::RuntimeTask
UbTrafficGen::ConvertToRuntimeTask(const TrafficRecord& record)
{
    RuntimeTask task;
    task.taskId = static_cast<uint32_t>(record.taskId);
    task.sourceNode = static_cast<uint32_t>(record.sourceNode);
    task.destNode = static_cast<uint32_t>(record.destNode);
    task.dataSize = static_cast<uint32_t>(record.dataSize);
    task.priority = static_cast<uint8_t>(record.priority);
    task.delay = ParseDelayLocked(record.delay);
    task.op = ParseOpLocked(record.opType);

    return task;
}

UbTrafficGen::RuntimeTaskOp
UbTrafficGen::ParseOpLocked(const std::string& opType)
{
    if (opType == "URMA_WRITE") {
        return RuntimeTaskOp::URMA_WRITE;
    }
    if (opType == "URMA_READ") {
        return RuntimeTaskOp::URMA_READ;
    }
    if (opType == "MEM_STORE") {
        return RuntimeTaskOp::MEM_STORE;
    }
    if (opType == "MEM_LOAD") {
        return RuntimeTaskOp::MEM_LOAD;
    }
    return RuntimeTaskOp::UNKNOWN;
}

Ptr<UbApp>
UbTrafficGen::GetSourceAppLocked(uint32_t sourceNode) const
{
    if (sourceNode >= m_sourceApps.size()) {
        return nullptr;
    }
    return m_sourceApps[sourceNode];
}

Time
UbTrafficGen::ParseDelayLocked(const std::string& delay)
{
    if (delay.empty()) {
        return Time(0);
    }
    if (delay == "0" || delay == "0s" || delay == "0ms" || delay == "0us" || delay == "0ns" ||
        delay == "0ps" || delay == "0fs") {
        return Time(0);
    }

    Time parsed;
    if (TryParseIntegerDelay(delay, &parsed)) {
        return parsed;
    }

    auto cacheIt = m_delayParseCache.find(delay);
    if (cacheIt != m_delayParseCache.end()) {
        return cacheIt->second;
    }

    parsed = Time(delay);
    if (m_delayParseCache.size() < kMaxDelayParseCacheEntries) {
        m_delayParseCache.emplace(delay, parsed);
    }
    return parsed;
}

bool
UbTrafficGen::TryParseIntegerDelay(const std::string& delay, Time* out)
{
    if (delay.empty() || out == nullptr) {
        return false;
    }

    uint64_t value = 0;
    std::size_t pos = 0;
    for (; pos < delay.size(); ++pos) {
        const char c = delay[pos];
        if (c < '0' || c > '9') {
            break;
        }
        const uint64_t digit = static_cast<uint64_t>(c - '0');
        if (value > (std::numeric_limits<uint64_t>::max() - digit) / 10) {
            return false;
        }
        value = value * 10 + digit;
    }

    if (pos == 0) {
        return false;
    }

    const char* unit = delay.data() + pos;
    const std::size_t unitLength = delay.size() - pos;
    if (unitLength == 0 || (unitLength == 1 && unit[0] == 's')) {
        *out = Time::FromInteger(value, Time::S);
    } else if (unitLength == 2 && unit[0] == 'm' && unit[1] == 's') {
        *out = Time::FromInteger(value, Time::MS);
    } else if (unitLength == 2 && unit[0] == 'u' && unit[1] == 's') {
        *out = Time::FromInteger(value, Time::US);
    } else if (unitLength == 2 && unit[0] == 'n' && unit[1] == 's') {
        *out = Time::FromInteger(value, Time::NS);
    } else if (unitLength == 2 && unit[0] == 'p' && unit[1] == 's') {
        *out = Time::FromInteger(value, Time::PS);
    } else if (unitLength == 2 && unit[0] == 'f' && unit[1] == 's') {
        *out = Time::FromInteger(value, Time::FS);
    } else if (unitLength == 3 && unit[0] == 'm' && unit[1] == 'i' && unit[2] == 'n') {
        *out = Time::FromInteger(value, Time::MIN);
    } else if (unitLength == 1 && unit[0] == 'h') {
        *out = Time::FromInteger(value, Time::H);
    } else if (unitLength == 1 && unit[0] == 'd') {
        *out = Time::FromInteger(value, Time::D);
    } else if (unitLength == 1 && unit[0] == 'y') {
        *out = Time::FromInteger(value, Time::Y);
    } else {
        return false;
    }

    return true;
}

void
UbTrafficGen::SwitchDenseTaskStoreToMapLocked()
{
    if (!m_useDenseTaskStore) {
        return;
    }

    for (uint32_t taskId = 0; taskId < m_denseTasks.size(); ++taskId) {
        m_tasks[taskId] = m_denseTasks[taskId];
    }

    m_denseTasks.clear();
    m_useDenseTaskStore = false;
}

bool
UbTrafficGen::HasTaskLocked(uint32_t taskId) const
{
    if (m_useDenseTaskStore) {
        return taskId < m_denseTasks.size();
    }
    return m_tasks.find(taskId) != m_tasks.end();
}

UbTrafficGen::RuntimeTask*
UbTrafficGen::FindTaskLocked(uint32_t taskId)
{
    if (m_useDenseTaskStore) {
        if (taskId >= m_denseTasks.size()) {
            return nullptr;
        }
        return &m_denseTasks[taskId];
    }

    auto taskIt = m_tasks.find(taskId);
    if (taskIt == m_tasks.end()) {
        return nullptr;
    }
    return &taskIt->second;
}

const UbTrafficGen::RuntimeTask*
UbTrafficGen::FindTaskLocked(uint32_t taskId) const
{
    if (m_useDenseTaskStore) {
        if (taskId >= m_denseTasks.size()) {
            return nullptr;
        }
        return &m_denseTasks[taskId];
    }

    auto taskIt = m_tasks.find(taskId);
    if (taskIt == m_tasks.end()) {
        return nullptr;
    }
    return &taskIt->second;
}

bool
UbTrafficGen::SetTaskLocked(RuntimeTask task)
{
    const uint32_t taskId = task.taskId;
    if (HasTaskLocked(taskId)) {
        return false;
    }

    if (m_useDenseTaskStore) {
        if (taskId == m_denseTasks.size()) {
            m_denseTasks.push_back(task);
            return true;
        }

        SwitchDenseTaskStoreToMapLocked();
    }

    m_tasks[taskId] = task;
    return true;
}

void
UbTrafficGen::RegisterTaskPhaseLocked(uint32_t phaseId, uint32_t taskId)
{
    if (HasTaskPhaseLocked(taskId)) {
        return;
    }

    SetTaskPhaseLocked(taskId, phaseId);
    auto& phaseState = m_phaseStates[phaseId];
    ++phaseState.totalTasks;
}

void
UbTrafficGen::AddReadyTaskLocked(uint32_t taskId)
{
    if (!m_readyTasks.empty() && taskId < m_readyTasks.back()) {
        m_readyTasksSorted = false;
    }
    m_readyTasks.push_back(taskId);
}

void
UbTrafficGen::SwitchDenseTaskStateToMapLocked()
{
    if (!m_useDenseTaskState) {
        return;
    }

    for (uint32_t taskId = 0; taskId < m_denseTaskPresent.size(); ++taskId) {
        if (m_denseTaskPresent[taskId] == 0) {
            continue;
        }
        m_taskStates[taskId] = m_denseTaskStates[taskId];
        m_taskPhaseIds[taskId] = m_denseTaskPhaseIds[taskId];
        m_pendingPhaseCounts[taskId] = m_densePendingPhaseCounts[taskId];
    }

    m_denseTaskPresent.clear();
    m_denseTaskStates.clear();
    m_denseTaskPhaseIds.clear();
    m_densePendingPhaseCounts.clear();
    m_useDenseTaskState = false;
}

bool
UbTrafficGen::HasTaskStateLocked(uint32_t taskId) const
{
    if (m_useDenseTaskState) {
        return taskId < m_denseTaskPresent.size() && m_denseTaskPresent[taskId] != 0;
    }
    return m_taskStates.find(taskId) != m_taskStates.end();
}

UbTrafficGen::TaskState*
UbTrafficGen::FindTaskStateLocked(uint32_t taskId)
{
    if (m_useDenseTaskState) {
        if (taskId >= m_denseTaskPresent.size() || m_denseTaskPresent[taskId] == 0) {
            return nullptr;
        }
        return &m_denseTaskStates[taskId];
    }

    auto stateIt = m_taskStates.find(taskId);
    if (stateIt == m_taskStates.end()) {
        return nullptr;
    }
    return &stateIt->second;
}

void
UbTrafficGen::SetTaskStateLocked(uint32_t taskId, TaskState state)
{
    if (m_useDenseTaskState) {
        if (taskId == m_denseTaskPresent.size()) {
            m_denseTaskPresent.push_back(1);
            m_denseTaskStates.push_back(state);
            m_denseTaskPhaseIds.push_back(0);
            m_densePendingPhaseCounts.push_back(0);
            return;
        }

        if (taskId < m_denseTaskPresent.size() && m_denseTaskPresent[taskId] != 0) {
            m_denseTaskStates[taskId] = state;
            return;
        }

        SwitchDenseTaskStateToMapLocked();
    }

    m_taskStates[taskId] = state;
}

uint32_t*
UbTrafficGen::FindTaskPhaseLocked(uint32_t taskId)
{
    if (m_useDenseTaskState) {
        if (taskId >= m_denseTaskPresent.size() || m_denseTaskPresent[taskId] == 0) {
            return nullptr;
        }
        return &m_denseTaskPhaseIds[taskId];
    }

    auto phaseIt = m_taskPhaseIds.find(taskId);
    if (phaseIt == m_taskPhaseIds.end()) {
        return nullptr;
    }
    return &phaseIt->second;
}

bool
UbTrafficGen::HasTaskPhaseLocked(uint32_t taskId) const
{
    if (m_useDenseTaskState) {
        return taskId < m_denseTaskPresent.size() && m_denseTaskPresent[taskId] != 0;
    }
    return m_taskPhaseIds.find(taskId) != m_taskPhaseIds.end();
}

void
UbTrafficGen::SetTaskPhaseLocked(uint32_t taskId, uint32_t phaseId)
{
    if (m_useDenseTaskState) {
        if (taskId == m_denseTaskPresent.size()) {
            m_denseTaskPresent.push_back(1);
            m_denseTaskStates.push_back(TaskState::PENDING);
            m_denseTaskPhaseIds.push_back(phaseId);
            m_densePendingPhaseCounts.push_back(0);
            return;
        }

        if (taskId < m_denseTaskPresent.size() && m_denseTaskPresent[taskId] != 0) {
            m_denseTaskPhaseIds[taskId] = phaseId;
            return;
        }

        SwitchDenseTaskStateToMapLocked();
    }

    m_taskPhaseIds[taskId] = phaseId;
}

uint32_t
UbTrafficGen::GetPendingPhaseCountLocked(uint32_t taskId) const
{
    if (m_useDenseTaskState) {
        if (taskId >= m_denseTaskPresent.size() || m_denseTaskPresent[taskId] == 0) {
            return 0;
        }
        return m_densePendingPhaseCounts[taskId];
    }

    auto pendingIt = m_pendingPhaseCounts.find(taskId);
    if (pendingIt == m_pendingPhaseCounts.end()) {
        return 0;
    }
    return pendingIt->second;
}

bool
UbTrafficGen::DecrementPendingPhaseCountLocked(uint32_t taskId)
{
    if (m_useDenseTaskState) {
        if (taskId >= m_denseTaskPresent.size() || m_denseTaskPresent[taskId] == 0 ||
            m_densePendingPhaseCounts[taskId] == 0) {
            return false;
        }
        --m_densePendingPhaseCounts[taskId];
        return m_densePendingPhaseCounts[taskId] == 0;
    }

    auto pendingIt = m_pendingPhaseCounts.find(taskId);
    if (pendingIt == m_pendingPhaseCounts.end() || pendingIt->second == 0) {
        return false;
    }

    --pendingIt->second;
    return pendingIt->second == 0;
}

void
UbTrafficGen::SetPendingPhaseCountLocked(uint32_t taskId, uint32_t count)
{
    if (m_useDenseTaskState) {
        if (taskId == m_denseTaskPresent.size()) {
            m_denseTaskPresent.push_back(1);
            m_denseTaskStates.push_back(TaskState::PENDING);
            m_denseTaskPhaseIds.push_back(0);
            m_densePendingPhaseCounts.push_back(count);
            return;
        }

        if (taskId < m_denseTaskPresent.size() && m_denseTaskPresent[taskId] != 0) {
            m_densePendingPhaseCounts[taskId] = count;
            return;
        }

        SwitchDenseTaskStateToMapLocked();
    }

    m_pendingPhaseCounts[taskId] = count;
}

UbTrafficGen::TaskCleanupInfo
UbTrafficGen::GetTaskCleanupInfoById(uint32_t taskId)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    const RuntimeTask* task = FindTaskLocked(taskId);
    NS_ABORT_MSG_IF(task == nullptr, "Can't find task from TrafficRecord.");
    TaskCleanupInfo info;
    info.sourceNode = task->sourceNode;
    info.destNode = task->destNode;
    info.priority = task->priority;
    return info;
}

void UbTrafficGen::MarkTaskCompleted(uint32_t taskId)
{
    ReadyTaskBatch readyBatch;
    {
        std::lock_guard<std::mutex> lock(m_mutex);

        TaskState* state = FindTaskStateLocked(taskId);
        if (state == nullptr || *state != TaskState::RUNNING) {
            return;
        }

        *state = TaskState::COMPLETED;
        NS_LOG_DEBUG("Task " << taskId << " completed");

        uint32_t* phaseId = FindTaskPhaseLocked(taskId);
        if (phaseId != nullptr) {
            auto phaseStateIt = m_phaseStates.find(*phaseId);
            if (phaseStateIt != m_phaseStates.end()) {
                ++phaseStateIt->second.completedTasks;

                if (phaseStateIt->second.completedTasks >= phaseStateIt->second.totalTasks) {
                    for (uint32_t dependentId : phaseStateIt->second.dependentTasks) {
                        TaskState* dependentState = FindTaskStateLocked(dependentId);
                        if (dependentState == nullptr || *dependentState != TaskState::PENDING) {
                            continue;
                        }

                        if (DecrementPendingPhaseCountLocked(dependentId)) {
                            *dependentState = TaskState::READY;
                            AddReadyTaskLocked(dependentId);
                        }
                    }
                    phaseStateIt->second.dependentTasks.clear();
                }
            }
        }

        readyBatch = CollectReadyTaskBatchLocked();
    }

    ScheduleTasks(readyBatch);

    if (IsCompleted()) {
        NS_LOG_DEBUG("[APPLICATION INFO] All tasks completed");
    }
}

bool UbTrafficGen::IsCompleted() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (m_useDenseTaskState) {
        for (uint32_t taskId = 0; taskId < m_denseTaskPresent.size(); ++taskId) {
            if (m_denseTaskPresent[taskId] != 0 &&
                m_denseTaskStates[taskId] != TaskState::COMPLETED) {
                return false;
            }
        }
        return true;
    }

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
    if (m_useDenseTaskState)
    {
        for (uint32_t taskId = 0; taskId < m_denseTaskPresent.size(); ++taskId)
        {
            if (m_denseTaskPresent[taskId] != 0 &&
                m_denseTaskStates[taskId] == TaskState::COMPLETED)
            {
                ++completedTasks;
            }
        }
        return completedTasks;
    }

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
    return GetPendingPhaseCountLocked(taskId);
}

bool
UbTrafficGen::IsUsingDenseTaskStoreForTesting() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    return m_useDenseTaskStore;
}

uint32_t
UbTrafficGen::GetStoredTaskCountForTesting() const
{
    std::lock_guard<std::mutex> lock(m_mutex);
    if (!m_useDenseTaskStore) {
        return static_cast<uint32_t>(m_tasks.size());
    }

    return static_cast<uint32_t>(m_denseTasks.size());
}

std::vector<uint32_t>
UbTrafficGen::CollectReadyTaskIdsForTesting()
{
    std::lock_guard<std::mutex> lock(m_mutex);
    std::vector<uint32_t> ids = m_readyTasks;
    std::sort(ids.begin(), ids.end());
    ids.erase(std::unique(ids.begin(), ids.end()), ids.end());
    return ids;
}

void UbTrafficGen::ScheduleNextTasks()
{
    ReadyTaskBatch readyBatch;
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        readyBatch = CollectReadyTaskBatchLocked();
    }
    ScheduleTasks(readyBatch);
}

UbTrafficGen::ReadyTaskBatch
UbTrafficGen::CollectReadyTaskBatchLocked()
{
    if (!m_readyTasksSorted) {
        std::sort(m_readyTasks.begin(), m_readyTasks.end());
        m_readyTasks.erase(std::unique(m_readyTasks.begin(), m_readyTasks.end()),
                           m_readyTasks.end());
        m_readyTasksSorted = true;
    }

    ReadyTaskBatch batch;
    batch.tasks.reserve(m_readyTasks.size());
    batch.sourceApps.reserve(m_readyTasks.size());
    for (uint32_t taskId : m_readyTasks) {
        TaskState* state = FindTaskStateLocked(taskId);
        if (state != nullptr && *state == TaskState::READY) {
            *state = TaskState::RUNNING;
            RuntimeTask* task = FindTaskLocked(taskId);
            if (task != nullptr) {
                batch.tasks.push_back(*task);
                batch.sourceApps.push_back(GetSourceAppLocked(task->sourceNode));
            }
        }
    }
    m_readyTasks.clear();
    m_readyTasksSorted = true;
    return batch;
}

void UbTrafficGen::ScheduleTasks(const ReadyTaskBatch& batch)
{
    for (size_t index = 0; index < batch.tasks.size(); ++index) {
        const auto& task = batch.tasks[index];
        if (task.priority == 0) {
            NS_LOG_WARN("It is strongly recommended not to set the task priority to 0. " <<
                        "Priority level 0 is reserved for control frames.");
        }
        Ptr<UbApp> app = index < batch.sourceApps.size() ? batch.sourceApps[index] : nullptr;
        if (app == nullptr) {
            app = DynamicCast<UbApp>(NodeList::GetNode(task.sourceNode)->GetApplication(0));
        }
        Simulator::ScheduleWithContext(app->GetNode()->GetId(),
                                       task.delay,
                                       static_cast<void (UbApp::*)(RuntimeTask)>(&UbApp::SendTraffic),
                                       app,
                                       task);
        NS_LOG_DEBUG("Scheduled task " << task.taskId);
    }
}

void UbTrafficGen::OnTaskCompleted(uint32_t taskId)
{
    MarkTaskCompleted(taskId);
}

} // namespace ns3
