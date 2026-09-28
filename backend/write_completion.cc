#include "write_completion.hh"
#include "ns3/simulator.h"

#include <stdexcept>
#include <utility>

namespace moe_ub
{
WriteCompletion::WriteCompletion(std::string identity, uint64_t bytes, std::function<void()> retired)
    : identity_(std::move(identity)), expected_bytes_(bytes), retired_(std::move(retired)),
      context_(std::make_unique<Context>())
{
    if (bytes == 0) throw std::invalid_argument(identity_ + ": zero byte transfer");
}
WriteCompletion::~WriteCompletion()
{
    send_event_.Cancel();
    receive_event_.Cancel();
}
void WriteCompletion::Require(bool condition, const char* message) const
{
    if (!condition) throw std::logic_error(identity_ + ": " + message);
}
void WriteCompletion::RegisterSend(void (*callback)(void*), void* arg)
{
    Require(context_ && !context_->send.registered && callback, "Duplicate/stale/invalid SEND");
    context_->send = {true, callback, arg};
}
void WriteCompletion::RegisterReceive(void (*callback)(void*), void* arg)
{
    Require(context_ && !context_->receive.registered && callback, "Duplicate/stale/invalid RECV");
    context_->receive = {true, callback, arg};
    QueueReceive();
}
bool WriteCompletion::ReceiverWaiting() const
{
    return context_ && context_->receive.registered && !receive_done_ns_;
}
void WriteCompletion::WriteCommitted(uint32_t jetty, uint64_t sequence, uint64_t bytes)
{
    Require(context_ && context_->send.registered, "Write for inactive/unsubmitted transfer");
    const auto key = std::make_pair(jetty, sequence);
    const auto found = context_->segments.find(key);
    if (found != context_->segments.end())
    {
        Require(found->second == bytes, "Conflicting duplicate write");
        return; // A repeated observation never contributes bytes twice.
    }
    Require(bytes > 0 && bytes <= expected_bytes_ - committed_bytes_, "Invalid committed byte count");
    context_->segments.emplace(key, bytes);
    committed_bytes_ += bytes;
    ++committed_segments_;
    if (committed_bytes_ == expected_bytes_)
    {
        data_ready_ns_ = ns3::Simulator::Now().GetNanoSeconds();
        QueueReceive();
    }
}
void WriteCompletion::QueueReceive()
{
    if (data_ready_ns_ && context_->receive.registered && !receive_done_ns_ &&
        !receive_event_.IsPending())
        receive_event_ = ns3::Simulator::ScheduleNow(&WriteCompletion::Deliver, this, false);
}
void WriteCompletion::TransactionDone()
{
    Require(context_ && context_->send.registered && !transaction_done_ns_,
            "Duplicate/stale/unsubmitted transaction completion");
    transaction_done_ns_ = ns3::Simulator::Now().GetNanoSeconds();
    send_event_ = ns3::Simulator::ScheduleNow(&WriteCompletion::Deliver, this, true);
}
void WriteCompletion::Deliver(bool sending)
{
    Require(context_ != nullptr, "Callback for retired transfer");
    auto& slot = sending ? context_->send : context_->receive;
    auto& timestamp = sending ? send_done_ns_ : receive_done_ns_;
    Require(slot.callback && !timestamp, "Duplicate callback delivery");
    auto callback = slot.callback;
    void* arg = slot.arg;
    slot.callback = nullptr;
    slot.arg = nullptr;
    timestamp = ns3::Simulator::Now().GetNanoSeconds();
    callback(arg);
    if (transaction_done_ns_ && data_ready_ns_ && send_done_ns_ && receive_done_ns_)
    {
        context_.reset();
        if (retired_) retired_();
        retired_ = {};
    }
}
}
