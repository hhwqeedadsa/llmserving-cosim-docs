// SPDX-License-Identifier: GPL-2.0-only
#ifndef UB_TRAFFIC_GEN_H
#define UB_TRAFFIC_GEN_H

#include <vector>
#include <unordered_map>
#include <set>
#include <mutex>
#include <cstdint>
#include "ns3/application.h"
#include "ns3/event-id.h"
#include "ns3/ptr.h"
#include "ns3/ipv4-address.h"
#include "ns3/ub-datatype.h"
#include "ns3/ub-controller.h"
#include "ns3/ub-ldst-api.h"
#include "ub-tp-connection-manager.h"
#include "ub-network-address.h"

class UbTrafficGenPhaseDependencyMemoryTest;
class UbTrafficGenSparseTaskIdFallbackTest;
class UbTrafficGenReserveHintTest;
class UbTrafficGenDenseTaskStoreTest;
class UbTrafficGenDuplicateDependencyPhaseTest;
class UbTrafficGenReadyOrderTest;
class UbTrafficGenIntegerDelayParserTest;

using namespace utils;
namespace ns3 {
    class UbApp;

/**
 * @brief 任务图应用,管理多个WQE任务及依赖关系
 */
class UbTrafficGen : public Object, public Singleton<UbTrafficGen> {
public:
    static UbTrafficGen& GetInstance() {
        static UbTrafficGen instance;
        return instance;
    }

    UbTrafficGen(const UbTrafficGen&) = delete;
    UbTrafficGen& operator = (const UbTrafficGen&) = delete;

public:
    static TypeId GetTypeId(void);

    UbTrafficGen();
    virtual ~UbTrafficGen();

    enum class RuntimeTaskOp : uint8_t
    {
        UNKNOWN,
        URMA_WRITE,
        URMA_READ,
        MEM_STORE,
        MEM_LOAD
    };

    struct RuntimeTask
    {
        uint32_t taskId{0};
        uint32_t sourceNode{0};
        uint32_t destNode{0};
        uint32_t dataSize{0};
        uint8_t priority{0};
        RuntimeTaskOp op{RuntimeTaskOp::UNKNOWN};
        Time delay{Time(0)};
    };

    struct TaskCleanupInfo
    {
        uint32_t sourceNode{0};
        uint32_t destNode{0};
        uint8_t priority{0};
    };

    static bool IsMultiProcessRuntimeUnsupported();

    static inline std::string GetMultiProcessUnsupportedMessage()
    {
        return "UbTrafficGen does not support MPI multi-process usage in this branch. "
               "Supported: unified-bus multi-process data path and UbTrafficGen multithreading. "
               "Unsupported: distributed/operator-style synchronization via traffic.csv. "
               "Use local quick-example runs or build distributed coordination separately.";
    }

    static constexpr std::size_t kMaxDelayParseCacheEntries = 4096;

    void AddTask(const TrafficRecord& record);

    void AddTaskDuringInitialLoad(const TrafficRecord& record);

    void SetPhaseDepend(uint32_t phaseId, uint32_t taskId);

    void SetPhaseDependDuringInitialLoad(uint32_t phaseId, uint32_t taskId);

    void ReserveTasksForTraffic(uint64_t recordCount, uint32_t maxTaskId);

    TaskCleanupInfo GetTaskCleanupInfoById(uint32_t taskId);

    void MarkTaskCompleted(uint32_t taskId);

    bool IsCompleted() const;

    uint32_t GetCompletedTaskCount() const;

    /**
     * @brief 任务完成回调
     */
    void OnTaskCompleted(uint32_t taskId);

    /**
     * @brief 调度下一批可执行的任务
     */
    void ScheduleNextTasks();

  private:
    friend class ::UbTrafficGenPhaseDependencyMemoryTest;
    friend class ::UbTrafficGenSparseTaskIdFallbackTest;
    friend class ::UbTrafficGenReserveHintTest;
    friend class ::UbTrafficGenDenseTaskStoreTest;
    friend class ::UbTrafficGenDuplicateDependencyPhaseTest;
    friend class ::UbTrafficGenReadyOrderTest;
    friend class ::UbTrafficGenIntegerDelayParserTest;

    enum class TaskState {
        PENDING,
        READY,
        RUNNING,
        COMPLETED
    };

    struct PhaseState {
        uint32_t totalTasks{0};
        uint32_t completedTasks{0};
        std::vector<uint32_t> dependentTasks{};
    };

    map<std::string, TaOpcode> TaOpcodeMap = {
        {"URMA_WRITE", TaOpcode::TA_OPCODE_WRITE},
        {"URMA_READ", TaOpcode::TA_OPCODE_READ},
        {"MEM_STORE", TaOpcode::TA_OPCODE_WRITE},
        {"MEM_LOAD", TaOpcode::TA_OPCODE_READ}
    };

    std::unordered_map<uint32_t, RuntimeTask> m_tasks{};
    bool m_useDenseTaskStore{true};
    std::vector<uint8_t> m_denseTaskStorePresent{};
    std::vector<RuntimeTask> m_denseTasks{};
    std::unordered_map<uint32_t, TaskState> m_taskStates{};
    std::unordered_map<uint32_t, uint32_t> m_taskPhaseIds{};
    std::unordered_map<uint32_t, uint32_t> m_pendingPhaseCounts{};
    bool m_useDenseTaskState{true};
    std::vector<uint8_t> m_denseTaskPresent{};
    std::vector<TaskState> m_denseTaskStates{};
    std::vector<uint32_t> m_denseTaskPhaseIds{};
    std::vector<uint32_t> m_densePendingPhaseCounts{};
    std::unordered_map<uint32_t, PhaseState> m_phaseStates{};
    std::vector<uint32_t> m_readyTasks{};
    bool m_readyTasksSorted{true};
    std::unordered_map<std::string, Time> m_delayParseCache{};
    std::unordered_map<std::string, RuntimeTaskOp> m_opParseCache{};

    std::vector<RuntimeTask> CollectReadyTasksLocked();

    void ScheduleTasks(const std::vector<RuntimeTask>& tasks);

    void RegisterTaskPhaseLocked(uint32_t phaseId, uint32_t taskId);

    void AddTaskLocked(const TrafficRecord& record);

    void AddReadyTaskLocked(uint32_t taskId);

    RuntimeTask ConvertToRuntimeTask(const TrafficRecord& record);

    Time ParseDelayLocked(const std::string& delay);

    static bool TryParseIntegerDelay(const std::string& delay, Time* out);

    RuntimeTaskOp ParseOpLocked(const std::string& opType);

    void SwitchDenseTaskStoreToMapLocked();

    bool HasTaskLocked(uint32_t taskId) const;

    RuntimeTask* FindTaskLocked(uint32_t taskId);

    const RuntimeTask* FindTaskLocked(uint32_t taskId) const;

    bool SetTaskLocked(RuntimeTask task);

    void SwitchDenseTaskStateToMapLocked();

    bool HasTaskStateLocked(uint32_t taskId) const;

    TaskState* FindTaskStateLocked(uint32_t taskId);

    void SetTaskStateLocked(uint32_t taskId, TaskState state);

    uint32_t* FindTaskPhaseLocked(uint32_t taskId);

    bool HasTaskPhaseLocked(uint32_t taskId) const;

    void SetTaskPhaseLocked(uint32_t taskId, uint32_t phaseId);

    uint32_t GetPendingPhaseCountLocked(uint32_t taskId) const;

    bool DecrementPendingPhaseCountLocked(uint32_t taskId);

    void SetPendingPhaseCountLocked(uint32_t taskId, uint32_t count);

    uint64_t GetDependencyReferenceCountForTesting() const;

    uint32_t GetPendingPhaseCountForTesting(uint32_t taskId) const;

    bool IsUsingDenseTaskStoreForTesting() const;

    uint32_t GetStoredTaskCountForTesting() const;

    std::vector<uint32_t> CollectReadyTaskIdsForTesting();

    mutable std::mutex m_mutex;
};

} // namespace ns3

#endif // UB_TRAFFIC_GEN_H
