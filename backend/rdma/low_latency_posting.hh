#pragma once
#include "gin_work_queue.hh"
#include <algorithm>
#include <limits>
#include <unordered_map>

namespace moe_rdma {

// Explicit registration boundaries, not discovered keys or GPU virtual memory.
struct RegisteredSpan { uint64_t begin, end; };

inline std::vector<uint32_t> SplitRegisteredPut(uint64_t local, uint64_t remote,
    uint64_t bytes, const std::vector<RegisteredSpan>& local_spans,
    const std::vector<RegisteredSpan>& remote_spans) {
  if (!bytes || bytes > 1024 * 1024 ||
      local > std::numeric_limits<uint64_t>::max() - bytes ||
      remote > std::numeric_limits<uint64_t>::max() - bytes)
    throw std::invalid_argument("put size or address overflow");
  const auto validate = [](const std::vector<RegisteredSpan>& spans) {
    if (spans.empty() || spans.size() > 64)
      throw std::invalid_argument("registration table needs 1..64 explicit spans");
    uint64_t previous = 0;
    for (const auto& span : spans) {
      if (span.begin >= span.end || span.begin < previous)
        throw std::invalid_argument("registration spans overlap, regress or are empty");
      previous = span.end;
    }
  };
  validate(local_spans); validate(remote_spans);
  const auto available = [](uint64_t address, const std::vector<RegisteredSpan>& spans) {
    for (const auto& span : spans)
      if (span.begin <= address && address < span.end) return span.end - address;
    throw std::invalid_argument("put reaches unregistered memory");
  };
  std::vector<uint32_t> result;
  while (bytes) {
    const auto chunk = std::min({bytes, available(local, local_spans), available(remote, remote_spans)});
    if (result.size() == 32)
      throw std::invalid_argument("put exceeds the supported 32-WQE span");
    result.push_back(static_cast<uint32_t>(chunk));
    local += chunk; remote += chunk; bytes -= chunk;
  }
  return result;
}

// Conditional DeepEP v1.2.1 synchronous IBGDA posting policy over the existing
// bounded queue core. NOT modern GIN, an async postsend agent, or a NIC model.
// Calls are untimed and single-threaded. CQ progress is supplied by the caller;
// it is never inferred from posting, consumption, packet counts or truth data.
class LowLatencyPostingQueue {
 public:
  struct Operation {
    uint64_t id, begin, end;
    uint32_t message_index;
    bool notification, post_processed = false;
    std::vector<uint32_t> wqe_payload_bytes;
  };
  struct Publication {
    bool eligible;
    uint64_t requested, previous, published;
  };

  LowLatencyPostingQueue(uint32_t depth, uint32_t maximum_wqes)
      : queue_(depth, maximum_wqes) {}

  Operation ReservePut(uint64_t id, uint32_t message_index,
      const std::vector<uint32_t>& wqe_payload_bytes) {
    if (message_index > static_cast<uint32_t>(std::numeric_limits<int32_t>::max()) ||
        wqe_payload_bytes.empty() || wqe_payload_bytes.size() > 32)
      throw std::invalid_argument("invalid logical message index or WQE span");
    uint64_t bytes = 0;
    for (const auto length : wqe_payload_bytes) {
      if (!length || (bytes += length) > 1024 * 1024)
        throw std::invalid_argument("put WQE payloads exceed the declared support range");
    }
    return Reserve(id, message_index, false, wqe_payload_bytes);
  }
  Operation ReserveNotification(uint64_t id) {
    // One declared atomic WQE; 4B is its operand, not its wire packet size.
    return Reserve(id, 0, true, {4});
  }
  bool CanPrepare(uint64_t ticket) const { return queue_.CanPrepare(ticket); }
  void Prepare(uint64_t ticket) { queue_.Prepare(ticket); }
  bool CanPost(uint64_t id) const {
    const auto& op = Get(id);
    const auto state = queue_.Snapshot();
    return !state.closed && !op.post_processed && state.prepared_prefix >= op.end;
  }
  Publication Post(uint64_t id) {
    auto& op = operations_.at(Index(id));
    if (!CanPost(id))
      throw std::logic_error("post requires its full ordered ready prefix exactly once");
    const auto previous = queue_.Snapshot().published;
    const bool eligible = op.notification || (uint64_t(op.message_index) + 1) % 4 == 0;
    if (eligible) queue_.PublishThrough(std::max(previous, op.end));
    op.post_processed = true;
    return {eligible, op.end, previous, queue_.Snapshot().published};
  }
  std::optional<GinWorkQueue::Work> ConsumePublished() { return queue_.ConsumePublished(); }
  void CompleteThrough(uint64_t exclusive) { queue_.CompleteThrough(exclusive); }
  GinWorkQueue::State Snapshot() const { return queue_.Snapshot(); }
  const Operation& Get(uint64_t id) const { return operations_.at(Index(id)); }
  uint64_t FlushTarget() const { return queue_.FlushTarget(); }
  bool FlushComplete(uint64_t target) const { return queue_.FlushComplete(target); }
  bool Drained() const {
    return queue_.Drained() && std::all_of(operations_.begin(), operations_.end(),
        [](const auto& op) { return op.post_processed; });
  }
  void Close() {
    if (!Drained()) throw std::logic_error("posting queue has outstanding work or producers");
    queue_.Close();
  }

 private:
  std::size_t Index(uint64_t id) const {
    const auto found = indices_.find(id);
    if (found == indices_.end()) throw std::invalid_argument("unknown posting operation");
    return found->second;
  }
  Operation Reserve(uint64_t id, uint32_t index, bool notification,
      const std::vector<uint32_t>& lengths) {
    if (indices_.count(id)) throw std::invalid_argument("posting operation ID repeated");
    const auto begin = queue_.Snapshot().reserved;
    std::vector<GinWorkQueue::WorkSpec> entries;
    for (std::size_t i = 0; i < lengths.size(); ++i) entries.push_back({begin + i, id});
    const auto ticket = queue_.ReserveBatch(entries);
    Operation op{id, ticket, ticket + lengths.size(), index, notification, false, lengths};
    indices_.emplace(id, operations_.size()); operations_.push_back(op);
    return op;
  }
  GinWorkQueue queue_;
  std::vector<Operation> operations_;
  std::unordered_map<uint64_t, std::size_t> indices_;
};
}  // namespace moe_rdma
