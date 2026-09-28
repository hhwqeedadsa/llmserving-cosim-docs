// SPDX-License-Identifier: GPL-2.0-only
#ifndef UB_TRANSFER_EXECUTOR_H
#define UB_TRANSFER_EXECUTOR_H

#include "ub-traffic-gen.h"

#include <functional>
#include <map>

namespace ns3
{
class UbApp;

/** Executes tasks independently of the CSV phase scheduler. One executor per app. */
class UbTransferExecutor
{
  public:
    using Task = UbTrafficGen::RuntimeTask;
    using Completion = std::function<void(uint32_t)>;

    explicit UbTransferExecutor(UbApp& app);
    ~UbTransferExecutor();
    UbTransferExecutor(const UbTransferExecutor&) = delete;
    UbTransferExecutor& operator=(const UbTransferExecutor&) = delete;

    void Submit(Task task, Completion completed, bool forceCtp = false);
    void Complete(uint32_t taskId, uint32_t jettyNum);
    void CompleteMemory(uint32_t taskId);
    size_t GetPendingCount() const { return m_pending.size(); }

  private:
    struct Context
    {
        Task task;
        Completion completed;
        bool rtp;
        bool completing{false};
        EventId completionEvent;
    };

    void Finish(uint32_t taskId, uint32_t jettyNum, bool memory);
    UbApp& m_app;
    std::map<uint32_t, Context> m_pending;
};
}
#endif
