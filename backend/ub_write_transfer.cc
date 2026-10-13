#include "ub_write_transfer.hh"

#include "astra-sim/common/AstraNetworkAPI.hh"
#include "ns3/simulator.h"
#include "ns3/node-list.h"
#include "ns3/ub-app.h"
#include "ns3/ub-transfer-executor.h"
#include "ns3/ub-controller.h"
#include "ns3/ub-transaction.h"
#include "ns3/ub-function.h"
#include "ns3/ub-ldst-api.h"

#include <algorithm>
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
        descriptor_.DestinationNode() >= ns3::NodeList::GetNNodes() ||
        descriptor_.SourceNode() == descriptor_.DestinationNode() ||
        descriptor_.tag < 0 || descriptor_.bytes == 0 || descriptor_.graph_id.empty() ||
        source_->GetNode()->GetId() != descriptor_.SourceNode() ||
        (descriptor_.operation == TransferOperation::Write && source_->GetTransportMode() != ns3::TransportMode::RTP))
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
        if (descriptor_.operation != TransferOperation::Write) { SubmitNative(); return; }
        auto controller = ns3::NodeList::GetNode(descriptor_.DestinationNode())->GetObject<ns3::UbController>();
        if (!controller) throw std::runtime_error(Identity() + ": target has no UB controller");
        target_ = controller->GetUbTransaction();
        if (!target_ || !target_->TraceConnectWithoutContext("RemoteWriteCommitted",
                ns3::MakeCallback(&UbWriteTransfer::ObserveWrite, this)))
            throw std::runtime_error(Identity() + ": cannot observe target writes");
        ns3::UbTrafficGen::RuntimeTask task;
        task.taskId = static_cast<uint32_t>(descriptor_.task_id);
        task.sourceNode = descriptor_.SourceNode();
        task.destNode = descriptor_.DestinationNode();
        task.dataSize = static_cast<uint32_t>(descriptor_.bytes);
        task.priority = 7; // Same fixed business priority as the native baseline.
        task.op = ns3::UbTrafficGen::RuntimeTaskOp::URMA_WRITE;
        source_->GetTransferExecutor().Submit(task, [this](uint32_t id) {
            if (id != descriptor_.task_id)
                throw std::logic_error(Identity() + ": unexpected UB transaction completion");
            // Original UB transaction event, after its physical resource cleanup.
            const auto controller = ns3::NodeList::GetNode(descriptor_.SourceNode())->GetObject<ns3::UbController>();
            if (!jetty_ || controller->GetUbFunction()->IsJettyExists(*jetty_) ||
                controller->GetUbTransaction()->HasJettyState(*jetty_))
                throw std::logic_error(Identity() + ": source Jetty has not retired");
            jetty_retired_ = true;
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
        segment->GetSrc() != descriptor_.SourceNode() ||
        segment->GetDest() != descriptor_.DestinationNode()) return;
    if (segment->GetType() != ns3::TaOpcode::TA_OPCODE_WRITE ||
        segment->GetSegmentKind() != ns3::UbTransactionSegmentKind::REQUEST)
        throw std::logic_error(Identity() + ": unexpected target write metadata");
    // Target GetWqeSize() describes this reassembled TA unit, not the whole WQE.
    if (jetty_ && *jetty_ != segment->GetOriginJettyNum())
        throw std::logic_error(Identity() + ": WRITE changed source Jetty");
    jetty_ = segment->GetOriginJettyNum();
    completion_.WriteCommitted(segment->GetOriginJettyNum(), segment->GetRequestTassn(),
                               segment->GetSize());
}

void UbWriteTransfer::DisconnectTarget()
{
    if (native_source_)
    {
        native_source_->TraceDisconnectWithoutContext("RequestCreated", ns3::MakeCallback(&UbWriteTransfer::ObserveNativeRequest, this));
        native_source_->TraceDisconnectWithoutContext("ResponseArrived", ns3::MakeCallback(&UbWriteTransfer::ObserveNativeResponse, this));
        native_target_->TraceDisconnectWithoutContext("RequestArrived", ns3::MakeCallback(&UbWriteTransfer::ObserveNativeTarget, this));
        native_source_ = native_target_ = nullptr;
    }
    if (target_)
    {
        target_->TraceDisconnectWithoutContext("RemoteWriteCommitted",
            ns3::MakeCallback(&UbWriteTransfer::ObserveWrite, this));
        target_ = nullptr;
    }
}

void UbWriteTransfer::SubmitNative()
{
    native_source_ = source_->GetNode()->GetObject<ns3::UbController>()->GetUbFunction()->GetUbLdstApi();
    native_target_ = ns3::NodeList::GetNode(descriptor_.DestinationNode())->GetObject<ns3::UbController>()->GetUbFunction()->GetUbLdstApi();
    if (!native_source_ || !native_target_ ||
        !native_source_->TraceConnectWithoutContext("RequestCreated", ns3::MakeCallback(&UbWriteTransfer::ObserveNativeRequest, this)) ||
        !native_source_->TraceConnectWithoutContext("ResponseArrived", ns3::MakeCallback(&UbWriteTransfer::ObserveNativeResponse, this)) ||
        !native_target_->TraceConnectWithoutContext("RequestArrived", ns3::MakeCallback(&UbWriteTransfer::ObserveNativeTarget, this)))
        throw std::runtime_error(Identity() + ": cannot observe native data delivery");
    ns3::UbTrafficGen::RuntimeTask task;
    task.taskId = descriptor_.task_id; task.sourceNode = descriptor_.SourceNode();
    task.destNode = descriptor_.DestinationNode(); task.dataSize = descriptor_.bytes; task.priority = 7;
    task.nativeProducer = descriptor_.native_producer;
    task.op = descriptor_.operation == TransferOperation::Store ? ns3::UbTrafficGen::RuntimeTaskOp::MEM_STORE : ns3::UbTrafficGen::RuntimeTaskOp::MEM_LOAD;
    source_->GetTransferExecutor().Submit(task, [this](uint32_t id) {
        if (id != descriptor_.task_id || !native_requests_.empty() ||
            completion_.CommittedBytes() != descriptor_.bytes)
            throw std::logic_error(Identity() + ": native completion before useful data or resource retirement");
        jetty_retired_ = true; // Native requests do not allocate a Jetty.
        completion_.TransactionDone();
    });
    ++submitted_tasks_;
}

void UbWriteTransfer::ObserveNativeRequest(ns3::Ptr<const ns3::Packet> packet, uint32_t task,
                                          uint64_t offset, uint32_t bytes, ns3::UbMemOperationType type)
{
    if (task != descriptor_.task_id || type != ns3::UbMemOperationType::STORE) return;
    if (!native_requests_.emplace(packet->GetUid(), std::make_pair(offset, bytes)).second)
        throw std::logic_error(Identity() + ": repeated native request creation");
    if (!bytes || bytes > descriptor_.bytes-created_bytes_)
        throw std::logic_error(Identity() + ": invalid locally read byte count");
    created_bytes_ += bytes;
    if (created_bytes_ == descriptor_.bytes) local_read_.Signal();
}

void UbWriteTransfer::ObserveNativeTarget(uint64_t uid, uint32_t bytes, ns3::UbMemOperationType type)
{
    if (type != ns3::UbMemOperationType::STORE) return;
    const auto found = native_requests_.find(uid);
    if (found == native_requests_.end()) return;
    const auto [offset, expected] = found->second;
    if (bytes != expected) throw std::logic_error(Identity() + ": native STORE size changed");
    completion_.WriteCommitted(0, offset, bytes);
    if (descriptor_.local_facts)
    {
        uint64_t business = 0, metadata = 0;
        for (const auto& region : *descriptor_.payload_regions)
        {
            const auto begin = std::max(offset, region.begin), end = std::min(offset + bytes, region.end);
            const uint64_t arrived = end > begin ? end - begin : 0;
            if (region.kind == "business") { business += region.end-region.begin; business_arrived_ += arrived; }
            else if (region.kind == "metadata") { metadata += region.end-region.begin; metadata_arrived_ += arrived; }
        }
        const uint64_t now = ns3::Simulator::Now().GetNanoSeconds();
        if (business && business_arrived_ == business && !business_ready_ns_) business_ready_ns_ = now;
        if (metadata && metadata_arrived_ == metadata && !metadata_ready_ns_) metadata_ready_ns_ = now;
    }
    if (completion_.CommittedBytes() == descriptor_.bytes) target_ready_.Signal();
    native_requests_.erase(found);
}

void UbWriteTransfer::WaitEvent(const std::string& event, std::function<void()> callback)
{
    if (descriptor_.operation != TransferOperation::Store)
        throw std::logic_error(Identity() + ": local events require native STORE");
    if (event == "local_read_complete") local_read_.Wait(std::move(callback));
    else if (event == "data_ready") target_ready_.Wait(std::move(callback));
    else throw std::invalid_argument("Unknown STORE event: " + event);
}

void UbWriteTransfer::ObserveNativeResponse(uint64_t, uint32_t task, uint64_t offset,
                                           uint32_t bytes, ns3::UbMemOperationType type)
{
    if (task == descriptor_.task_id && type == ns3::UbMemOperationType::LOAD)
        completion_.WriteCommitted(0, offset, bytes);
}
}
