#include "ub_write_transfer.hh"

#include "astra-sim/common/AstraNetworkAPI.hh"
#include "ns3/simulator.h"
#include "ns3/node-list.h"
#include "ns3/ub-app.h"
#include "ns3/ub-transfer-executor.h"
#include "ns3/ub-controller.h"
#include "ns3/ub-transaction.h"

#include <limits>
#include <stdexcept>
#include <utility>

namespace moe_ub
{
UbWriteTransfer::UbWriteTransfer(WriteDescriptor descriptor, ns3::Ptr<ns3::UbApp> source)
    : descriptor_(std::move(descriptor)), source_(source),
      completion_(Identity(), descriptor_.bytes, [this]() {
          DisconnectTarget();
          source_ = nullptr;
      })
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
        throw std::invalid_argument("M1 requires a nonempty RTP write between distinct mapped ranks");
    }
}

UbWriteTransfer::~UbWriteTransfer()
{
    submit_event_.Cancel();
    DisconnectTarget();
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
        throw std::invalid_argument(Identity() + ": SEND/RECV does not match the UINT8 transfer descriptor");
    }
}

void UbWriteTransfer::Send(int rank, uint64_t bytes, int type, int dst, int tag,
                           void (*callback)(void*), void* arg)
{
    Validate(rank, dst, bytes, type, tag, callback);
    completion_.RegisterSend(callback, arg);
    send_start_ns_ = ns3::Simulator::Now().GetNanoSeconds();
    // Only explicit API values and the descriptor are retained; never sim_request*.
    submit_event_ = ns3::Simulator::ScheduleNow([this]() {
        auto controller = ns3::NodeList::GetNode(descriptor_.dst_rank)->GetObject<ns3::UbController>();
        if (!controller) throw std::runtime_error(Identity() + ": target has no UB controller");
        target_ = controller->GetUbTransaction();
        if (!target_ || !target_->TraceConnectWithoutContext("RemoteWriteCommitted",
                ns3::MakeCallback(&UbWriteTransfer::ObserveWrite, this)))
            throw std::runtime_error(Identity() + ": cannot observe target writes");
        ns3::UbTrafficGen::RuntimeTask task;
        task.taskId = static_cast<uint32_t>(descriptor_.task_id);
        task.sourceNode = static_cast<uint32_t>(descriptor_.src_rank);
        task.destNode = static_cast<uint32_t>(descriptor_.dst_rank);
        task.dataSize = static_cast<uint32_t>(descriptor_.bytes);
        task.priority = 7; // Same fixed business priority as the native baseline.
        task.op = ns3::UbTrafficGen::RuntimeTaskOp::URMA_WRITE;
        source_->GetTransferExecutor().Submit(task, [this](uint32_t id) {
            if (id != descriptor_.task_id)
                throw std::logic_error(Identity() + ": unexpected UB transaction completion");
            // Original UB transaction event, after its physical resource cleanup.
            completion_.TransactionDone();
        });
        ++submitted_tasks_;
    });
}

void UbWriteTransfer::Receive(int rank, uint64_t bytes, int type, int src, int tag,
                              void (*callback)(void*), void* arg)
{
    Validate(src, rank, bytes, type, tag, callback);
    completion_.RegisterReceive(callback, arg);
    receive_start_ns_ = ns3::Simulator::Now().GetNanoSeconds();
}

std::string UbWriteTransfer::Identity() const
{
    return "graph=" + descriptor_.graph_id + " transfer=" + std::to_string(descriptor_.transfer_id) +
           " send_node=" + std::to_string(descriptor_.send_node_id) +
           " recv_node=" + std::to_string(descriptor_.recv_node_id);
}

void UbWriteTransfer::ObserveWrite(ns3::Ptr<const ns3::UbWqeSegment> segment)
{
    // The observer does not own packets or change transport/application state.
    if (segment->GetTaskId() != descriptor_.task_id ||
        segment->GetSrc() != static_cast<uint32_t>(descriptor_.src_rank) ||
        segment->GetDest() != static_cast<uint32_t>(descriptor_.dst_rank)) return;
    if (segment->GetType() != ns3::TaOpcode::TA_OPCODE_WRITE ||
        segment->GetSegmentKind() != ns3::UbTransactionSegmentKind::REQUEST)
        throw std::logic_error(Identity() + ": unexpected target write metadata");
    // Target GetWqeSize() describes this reassembled TA unit, not the whole WQE.
    completion_.WriteCommitted(segment->GetOriginJettyNum(), segment->GetRequestTassn(),
                               segment->GetSize());
}

void UbWriteTransfer::DisconnectTarget()
{
    if (target_)
    {
        target_->TraceDisconnectWithoutContext("RemoteWriteCommitted",
            ns3::MakeCallback(&UbWriteTransfer::ObserveWrite, this));
        target_ = nullptr;
    }
}
}
