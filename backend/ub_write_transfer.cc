#include "ub_write_transfer.hh"

#include "astra-sim/common/AstraNetworkAPI.hh"
#include "ns3/simulator.h"
#include "ns3/node-list.h"
#include "ns3/ub-app.h"
#include "ns3/ub-transfer-executor.h"

#include <limits>
#include <stdexcept>
#include <utility>

namespace moe_ub
{
UbWriteTransfer::UbWriteTransfer(WriteDescriptor descriptor, ns3::Ptr<ns3::UbApp> source)
    : descriptor_(std::move(descriptor)), source_(source)
{
    if (descriptor_.task_id > std::numeric_limits<uint32_t>::max() ||
        descriptor_.bytes > std::numeric_limits<uint32_t>::max())
    {
        throw std::overflow_error("UB task id and byte count must fit uint32_t");
    }
    if (!source_ || !source_->GetNode() || descriptor_.src_rank < 0 ||
        descriptor_.dst_rank < 0 || descriptor_.src_rank == descriptor_.dst_rank ||
        static_cast<uint32_t>(descriptor_.dst_rank) >= ns3::NodeList::GetNNodes() ||
        descriptor_.tag < 0 || descriptor_.bytes == 0 || descriptor_.graph_id.empty() ||
        source_->GetNode()->GetId() != static_cast<uint32_t>(descriptor_.src_rank) ||
        source_->GetTransportMode() != ns3::TransportMode::RTP)
    {
        throw std::invalid_argument("M1.2 requires a nonempty RTP write between distinct mapped ranks");
    }
}

UbWriteTransfer::~UbWriteTransfer()
{
    submit_event_.Cancel();
    callback_event_.Cancel();
}

void UbWriteTransfer::Validate(int src, int dst, uint64_t bytes, int type, int tag,
                               void (*callback)(void*)) const
{
    if (bytes > std::numeric_limits<uint32_t>::max())
    {
        throw std::overflow_error("UB byte count must fit uint32_t");
    }
    if (type != AstraSim::UINT8 || bytes != descriptor_.bytes ||
        src != descriptor_.src_rank || dst != descriptor_.dst_rank ||
        tag != descriptor_.tag || callback == nullptr)
    {
        throw std::invalid_argument("SEND/RECV does not match the UINT8 transfer descriptor");
    }
}

void UbWriteTransfer::Send(int rank, uint64_t bytes, int type, int dst, int tag,
                           void (*callback)(void*), void* arg)
{
    Validate(rank, dst, bytes, type, tag, callback);
    if (send_registered_)
    {
        throw std::logic_error("Duplicate SEND for this transfer");
    }
    send_registered_ = true;
    // Only explicit API values and the descriptor are retained; never sim_request*.
    submit_event_ = ns3::Simulator::ScheduleNow([this, callback, arg]() {
        ns3::UbTrafficGen::RuntimeTask task;
        task.taskId = static_cast<uint32_t>(descriptor_.task_id);
        task.sourceNode = static_cast<uint32_t>(descriptor_.src_rank);
        task.destNode = static_cast<uint32_t>(descriptor_.dst_rank);
        task.dataSize = static_cast<uint32_t>(descriptor_.bytes);
        task.priority = 7; // Same fixed business priority as the native baseline.
        task.op = ns3::UbTrafficGen::RuntimeTaskOp::URMA_WRITE;
        source_->GetTransferExecutor().Submit(task, [this, callback, arg](uint32_t id) {
            if (id != descriptor_.task_id || transaction_done_ns_)
            {
                throw std::logic_error("Unexpected or duplicate UB transaction completion");
            }
            transaction_done_ns_ = ns3::Simulator::Now().GetNanoSeconds();
            // Completion comes from UB's application/transaction event after resource cleanup.
            callback_event_ = ns3::Simulator::ScheduleNow([this, callback, arg]() {
                send_done_ns_ = ns3::Simulator::Now().GetNanoSeconds();
                ++send_callbacks_;
                callback(arg);
            });
        });
        ++submitted_tasks_;
    });
}

void UbWriteTransfer::Receive(int rank, uint64_t bytes, int type, int src, int tag,
                              void (*callback)(void*), void* arg)
{
    Validate(src, rank, bytes, type, tag, callback);
    if (recv_callback_)
    {
        throw std::logic_error("Duplicate RECV for this transfer");
    }
    recv_callback_ = callback;
    recv_arg_ = arg;
    // M1.3 will consume this wait at target data_ready, never at source completion.
}
}
