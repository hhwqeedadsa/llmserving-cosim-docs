#pragma once
#include <cstdint>
#include <optional>
#include <stdexcept>
#include <unordered_set>
#include <vector>

namespace moe_rdma {

// A source-derived index mapping, not device discovery or QP creation.
struct GinContextAssignment {
  uint32_t connection, local_context, local_device, allocated_contexts;
};

inline GinContextAssignment ResolveGinContext(uint32_t index, uint32_t requested,
    uint32_t connections, const std::vector<uint32_t>& local_devices) {
  if (!requested || requested > 4096 || !connections || connections > 4 ||
      local_devices.empty() || local_devices.size() > 64)
    throw std::invalid_argument("GIN context mapping requires bounded declared resources");
  const uint32_t allocated = ((requested + connections - 1) / connections) * connections;
  if (index >= allocated)
    throw std::invalid_argument("GIN context index exceeds allocated contexts");
  const uint32_t connection = index % connections;
  return {connection, index / connections,
          local_devices[connection % local_devices.size()], allocated};
}

// Untimed resource core for ONE persistent peer/context work queue. Not linked
// into Fabric yet. No packet, atomic, remote visibility or CQE format is modeled.
// Explicit-prefix publication model, not CPU_PROXY_FREE_FLOW or SKIP_AVAILABILITY_CHECK.
// Batch reservations cover contiguous WQE entries, NOT descriptor bytes or
// packets. A caller may reserve ONLY when dependencies have actually fired;
// the queue has no access to future plans or remote/evaluation state.
class GinWorkQueue {
 public:
  struct Work { uint64_t ticket, work_id, operation; };
  struct WorkSpec { uint64_t work_id, operation; };
  struct State {
    uint64_t reserved, prepared_prefix, published, consumed, completed;
    bool closed;
  };

  GinWorkQueue(uint32_t depth, uint32_t maximum_work_items)
      : depth_(depth), maximum_(maximum_work_items) {
    if (!depth || depth > 4096 || (depth & (depth - 1)) ||
        !maximum_work_items || maximum_work_items > 65536)
      throw std::invalid_argument("GIN queue needs a power-of-two depth and bounded work budget");
  }

  uint64_t Reserve(uint64_t work_id, uint64_t operation) {
    return ReserveBatch({{work_id, operation}});
  }

  // Provider source reserves count entries with one atomic add, then waits
  // until the LAST reserved slot is available before writing any of the range.
  // This bounded core rejects ranges larger than depth: waiting for that entire
  // range without posting its own prefix could otherwise self-deadlock.
  uint64_t ReserveBatch(const std::vector<WorkSpec>& entries) {
    Open();
    if (entries.empty() || entries.size() > depth_ || entries.size() > maximum_ - work_.size())
      throw std::invalid_argument("GIN reservation range exceeds depth or lifetime budget");
    std::unordered_set<uint64_t> incoming;
    for (const auto& entry : entries)
      if (seen_.count(entry.work_id) || !incoming.insert(entry.work_id).second)
        throw std::invalid_argument("GIN work ID repeated within queue or reservation");
    const uint64_t ticket = work_.size();
    const uint64_t end = ticket + entries.size();
    for (const auto& entry : entries) {
      work_.push_back({entry.work_id, entry.operation, end, false});
      seen_.insert(entry.work_id);
    }
    return ticket;
  }

  bool CanPrepare(uint64_t ticket) const {
    return !closed_ && ticket < work_.size() && !work_[ticket].prepared &&
           work_[ticket].reservation_end <= completed_ + depth_;
  }

  void Prepare(uint64_t ticket) {
    Open();
    if (!CanPrepare(ticket))
      throw std::logic_error("GIN slot is unavailable, unknown or already prepared");
    work_[ticket].prepared = true;
    while (prepared_ < work_.size() && work_[prepared_].prepared) ++prepared_;
  }

  // A logical doorbell publication, not a count of physical MMIO writes. A
  // skipped/aggregate doorbell means the caller does NOT call this function.
  // Preparation alone never publishes work or consumes NIC resources.
  void PublishThrough(uint64_t exclusive) {
    Open();
    if (exclusive < published_ || exclusive > prepared_ ||
        (exclusive && work_[exclusive - 1].reservation_end != exclusive))
      throw std::logic_error("GIN publication cannot regress, cross unprepared work or split a reservation");
    published_ = exclusive;
  }

  // NIC consumption is supplied by a future adapter. It is not transmission or
  // completion. Returning a Work never releases its slot or source buffer.
  std::optional<Work> ConsumePublished() {
    Open();
    if (consumed_ == published_) return {};
    const auto ticket = consumed_++;
    return Work{ticket, work_[ticket].work_id, work_[ticket].operation};
  }

  // A validated cumulative CQ progress callback may cover multiple work items.
  // It must come from the provider; neither Publish nor Consume synthesizes it.
  // This progress is local only and NEVER reports remote GPU visibility.
  void CompleteThrough(uint64_t exclusive) {
    Open();
    if (exclusive <= completed_ || exclusive > consumed_)
      throw std::logic_error("GIN completion must advance within NIC-consumed work");
    completed_ = exclusive;
  }

  uint64_t FlushTarget() const { return work_.size(); }
  bool FlushComplete(uint64_t target) const {
    if (target > work_.size())
      throw std::invalid_argument("GIN flush target contains work not yet reserved");
    return completed_ >= target;
  }
  bool Drained() const { return completed_ == work_.size(); }
  uint32_t Depth() const { return depth_; }
  State Snapshot() const {
    return {work_.size(), prepared_, published_, consumed_, completed_, closed_};
  }
  void Close() {
    Open();
    if (!Drained()) throw std::logic_error("Cannot close GIN queue with outstanding work");
    closed_ = true;
  }

 private:
  struct Entry { uint64_t work_id, operation, reservation_end; bool prepared; };
  void Open() const {
    if (closed_) throw std::logic_error("GIN queue is closed");
  }
  uint32_t depth_, maximum_;
  std::vector<Entry> work_;
  std::unordered_set<uint64_t> seen_;
  uint64_t prepared_ = 0, published_ = 0, consumed_ = 0, completed_ = 0;
  bool closed_ = false;
};
} // namespace moe_rdma
