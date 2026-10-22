#include "rdma/gin_work_queue.hh"
#include <array>
#include <deque>
#include <iostream>
#include <set>
#include <string>

using moe_rdma::GinWorkQueue;
using moe_rdma::ResolveGinContext;
static unsigned checks = 0;
static unsigned explored_states = 0;
static unsigned batch_explored_states = 0;

static void Check(bool value) {
  if (!value) throw std::runtime_error("GIN work queue check failed");
  ++checks;
}
template <typename F> static void Reject(F action) {
  bool rejected = false;
  try { action(); } catch (const std::invalid_argument&) { rejected = true; }
  catch (const std::logic_error&) { rejected = true; }
  Check(rejected);
}

static void SlotAndPublication() {
  GinWorkQueue q(2, 8);
  Check(q.Drained() && q.FlushComplete(q.FlushTarget()));
  const auto a = q.Reserve(91, 91), b = q.Reserve(3, 3), c = q.Reserve(77, 77);
  Check(a == 0 && b == 1 && c == 2);
  Check(!q.CanPrepare(c)); // A reserved ticket is not a writable slot.
  Reject([&] { q.Prepare(c); });
  q.Prepare(b); // Concurrent producer finishes the later descriptor first.
  Check(q.Snapshot().prepared_prefix == 0);
  Reject([&] { q.PublishThrough(2); });
  Check(!q.ConsumePublished());
  q.Prepare(a);
  Check(q.Snapshot().prepared_prefix == 2);
  Check(!q.ConsumePublished()); // Aggregate preparation does not ring.
  q.PublishThrough(2);
  auto first = q.ConsumePublished(), second = q.ConsumePublished();
  Check(first && first->ticket == a && first->operation == 91);
  Check(second && second->ticket == b && second->operation == 3);
  Check(!q.ConsumePublished() && !q.CanPrepare(c));
  // A consumer may have observed data already; CQ remains the only local slot
  // progress here. There is deliberately no receiver-visibility release API.
  Reject([&] { q.Close(); });
  q.CompleteThrough(1);
  Check(q.CanPrepare(c));
  q.Prepare(c);
  q.PublishThrough(3);
  Check(q.ConsumePublished()->operation == 77);
  q.CompleteThrough(3); // Compressed completion covers two further works.
  Check(q.Drained() && q.FlushComplete(3));
  Reject([&] { q.CompleteThrough(3); });
  Reject([&] { q.Reserve(91, 91); });
}

static void FlushAndIdleReuse() {
  GinWorkQueue q(1, 4);
  const auto a = q.Reserve(1, 1);
  const auto target = q.FlushTarget();
  const auto b = q.Reserve(2, 2);
  Check(!q.FlushComplete(target));
  q.Prepare(a);q.PublishThrough(1);q.ConsumePublished();
  Reject([&] { q.CompleteThrough(2); });
  q.CompleteThrough(1);
  Check(q.FlushComplete(target) && !q.Drained()); // Future work not in flush.
  q.Prepare(b);q.PublishThrough(2);q.ConsumePublished();q.CompleteThrough(2);
  Check(q.Drained());
  const auto c = q.Reserve(3, 3); // Idle does not close/reset the persistent queue.
  Check(c == 2 && q.CanPrepare(c));
  q.Prepare(c);q.PublishThrough(3);q.ConsumePublished();q.CompleteThrough(3);
  q.Close();
  Check(q.Snapshot().closed && q.FlushComplete(3));
  Reject([&] { q.Reserve(4, 4); });
  Reject([&] { q.Prepare(2); });
  Reject([&] { q.PublishThrough(3); });
  Reject([&] { q.ConsumePublished(); });
  Reject([&] { q.Close(); });
}

static void IsolatedQueuesAndBinding() {
  const auto one = ResolveGinContext(1, 5, 2, {40, 80});
  const auto zero = ResolveGinContext(0, 5, 2, {40, 80});
  const auto rounded = ResolveGinContext(5, 5, 2, {40, 80});
  Check(one.connection == 1 && one.local_context == 0 && one.local_device == 80);
  Check(zero.connection == 0 && zero.local_device == 40);
  Check(rounded.local_context == 2 && rounded.allocated_contexts == 6);
  const auto shared_nic = ResolveGinContext(2, 5, 3, {7});
  Check(shared_nic.connection == 2 && shared_nic.local_device == 7);
  const auto tri = ResolveGinContext(4, 5, 3, {7, 9});
  Check(tri.connection == 1 && tri.local_context == 1 && tri.local_device == 9);
  Reject([] { ResolveGinContext(6, 5, 2, {40, 80}); });
  Reject([] { ResolveGinContext(0, 5, 0, {40}); });
  Reject([] { ResolveGinContext(0, 5, 5, {40}); });
  Reject([] { ResolveGinContext(0, 0, 2, {40}); });
  Reject([] { ResolveGinContext(0, 5, 2, {}); });
  GinWorkQueue a(1, 4), b(1, 4);
  a.Reserve(10, 10);b.Reserve(10, 10); // IDs belong to their own declared queue.
  a.Prepare(0);b.Prepare(0);a.PublishThrough(1);a.ConsumePublished();a.CompleteThrough(1);
  Check(a.Drained() && !b.Drained() && !b.FlushComplete(1));
  Check(b.Snapshot().published == 0 && b.Snapshot().completed == 0);
}

static void InvalidAndBudget() {
  Reject([] { GinWorkQueue q(0, 1); });
  Reject([] { GinWorkQueue q(3, 1); });
  Reject([] { GinWorkQueue q(8192, 1); });
  Reject([] { GinWorkQueue q(1, 0); });
  Reject([] { GinWorkQueue q(1, 65537); });
  GinWorkQueue q(2, 2);
  Reject([&] { q.Prepare(0); });
  Reject([&] { q.PublishThrough(1); });
  Reject([&] { q.CompleteThrough(1); });
  Reject([&] { q.FlushComplete(1); });
  q.Reserve(9, 9);q.Prepare(0);
  Reject([&] { q.Prepare(0); });
  q.PublishThrough(1);
  Reject([&] { q.PublishThrough(0); });
  Reject([&] { q.CompleteThrough(1); }); // Publication isn't NIC consumption.
  q.ConsumePublished();q.CompleteThrough(1);
  q.Reserve(20, 20);
  Reject([&] { q.Reserve(30, 30); });
  Check(q.Snapshot().reserved == 2 && q.Snapshot().completed == 1);
  GinWorkQueue split(2, 2);
  split.Reserve(100, 9);split.Reserve(101, 9); // One application op, two WQEs.
  split.Prepare(0);split.Prepare(1);split.PublishThrough(2);
  auto first = split.ConsumePublished(), second = split.ConsumePublished();
  Check(first->work_id != second->work_id && first->operation == second->operation);
}

static void LongAppend() {
  GinWorkQueue q(4, 1024);
  for (uint64_t batch = 0; batch < 256; ++batch) {
    for (uint64_t lane = 0; lane < 4; ++lane) Check(q.Reserve(batch*4+lane, batch*4+lane) == batch*4+lane);
    for (int lane = 3; lane >= 0; --lane) q.Prepare(batch*4+lane);
    Check(!q.ConsumePublished());
    q.PublishThrough((batch+1)*4);
    for (uint64_t lane = 0; lane < 4; ++lane) {
      auto work = q.ConsumePublished();
      Check(work && work->ticket == batch*4+lane && work->operation == batch*4+lane);
    }
    q.CompleteThrough((batch+1)*4);
    Check(q.Drained());
  }
  Check(q.Snapshot().completed == 1024);
  q.Close();
}

static void BatchReservation() {
  GinWorkQueue q(4, 12);
  Check(q.ReserveBatch({{1, 9}, {2, 9}, {3, 9}}) == 0);
  const auto flush = q.FlushTarget();
  Check(flush == 3);
  Check(q.ReserveBatch({{4, 10}, {5, 10}}) == 3);
  // Ticket 3 has an unused physical slot but its range also needs ticket 4,
  // whose previous ring owner (ticket 0) is not complete yet.
  Check(!q.CanPrepare(3) && !q.CanPrepare(4));
  Reject([&] { q.Prepare(3); });
  q.Prepare(0);
  Reject([&] { q.PublishThrough(1); }); // No partial publication of a range.
  q.Prepare(2);q.Prepare(1);q.PublishThrough(3);
  for (unsigned i=0;i<3;++i) Check(q.ConsumePublished()->ticket == i);
  Check(!q.CanPrepare(3)); // NIC consumption is not CQ feedback.
  q.CompleteThrough(1);
  Check(q.CanPrepare(3) && q.CanPrepare(4));
  q.Prepare(4);q.Prepare(3);q.PublishThrough(5);
  Check(q.ConsumePublished()->work_id == 4);
  Check(q.ConsumePublished()->work_id == 5);
  q.CompleteThrough(3);
  Check(q.FlushComplete(flush) && !q.Drained());
  q.CompleteThrough(5);Check(q.Drained());
  Check(q.ReserveBatch({{6, 11}, {7, 11}, {8, 11}, {9, 11}}) == 5);
  for (unsigned i=5;i<9;++i) q.Prepare(i);
  q.PublishThrough(9);
  for (unsigned i=5;i<9;++i) Check(q.ConsumePublished()->ticket == i);
  q.CompleteThrough(9);Check(q.Drained());
  const auto before = q.Snapshot();
  Reject([&] { q.ReserveBatch({}); });
  Reject([&] { q.ReserveBatch({{10,1},{11,1},{12,1},{13,1},{14,1}}); });
  Reject([&] { q.ReserveBatch({{10,1},{10,1}}); });
  Reject([&] { q.ReserveBatch({{10,1},{1,1}}); });
  Check(q.Snapshot().reserved == before.reserved); // Rejections change nothing.
  Check(q.ReserveBatch({{10,12},{11,12},{12,12}}) == 9);
  Reject([&] { q.ReserveBatch({{13,1}}); });
}

// Independent per-slot lifecycle plus previous physical owner oracle. Ranges
// [0,2), [2,3), [3,5) use a four-slot ring, including a wrapping reservation.
static void ExploreBatchInterleavings() {
  struct Node { GinWorkQueue queue; std::array<unsigned, 5> stage; };
  const std::array<unsigned,4> boundaries{0,2,3,5};
  std::deque<Node> todo;
  std::set<std::array<unsigned,5>> seen;
  auto add = [&](Node node) {
    if (seen.insert(node.stage).second) todo.push_back(std::move(node));
  };
  add({GinWorkQueue(4,5),{0,0,0,0,0}});
  while (!todo.empty()) {
    auto node=todo.front();todo.pop_front();++batch_explored_states;
    const auto prefix = [&](unsigned threshold) {
      uint64_t n=0;
      for (auto stage:node.stage) { if (stage<threshold) break; ++n; }
      return n;
    };
    const auto state=node.queue.Snapshot();
    Check(state.reserved==prefix(1) && state.prepared_prefix==prefix(2));
    Check(state.published==prefix(3) && state.consumed==prefix(4) && state.completed==prefix(5));
    Check(node.queue.Drained()==(prefix(1)==prefix(5)));
    for (uint64_t target=0;target<=prefix(1);++target)
      Check(node.queue.FlushComplete(target)==(target<=prefix(5)));
    for (unsigned range=0;range<3;++range) {
      const auto begin=boundaries[range],end=boundaries[range+1];
      bool all_available=true;
      for (unsigned i=begin;i<end;++i)
        if (i>=4 && node.stage[i-4]!=5) all_available=false;
      for (unsigned i=begin;i<end;++i) {
        const bool writable=node.stage[i]==1 && all_available;
        Check(node.queue.CanPrepare(i)==writable);
        if (writable) {
          auto next=node;next.queue.Prepare(i);next.stage[i]=2;add(std::move(next));
        }
      }
      if (prefix(1)==begin) {
        auto next=node;std::vector<GinWorkQueue::WorkSpec> entries;
        for (unsigned i=begin;i<end;++i) { entries.push_back({100+i,range});next.stage[i]=1; }
        Check(next.queue.ReserveBatch(entries)==begin);add(std::move(next));
      }
      if (end>prefix(3) && end<=prefix(2)) {
        auto next=node;next.queue.PublishThrough(end);
        for (uint64_t i=prefix(3);i<end;++i) next.stage[i]=3;
        add(std::move(next));
      }
    }
    if (prefix(4)<prefix(3)) {
      auto next=node;const auto work=next.queue.ConsumePublished();
      Check(work && work->work_id==100+prefix(4));
      next.stage[prefix(4)]=4;add(std::move(next));
    }
    for (uint64_t end=prefix(5)+1;end<=prefix(4);++end) {
      auto next=node;next.queue.CompleteThrough(end);
      for (uint64_t i=prefix(5);i<end;++i) next.stage[i]=5;
      add(std::move(next));
    }
  }
  Check(seen.count({5,5,5,5,5})==1);
}

// An independent per-work lifecycle oracle. It has no queue prefix counters:
// slot reuse is permitted only when the prior owner of that ring slot finished.
// Explore all legal event interleavings for three works and a two-slot ring.
static void ExploreInterleavings() {
  struct Node { GinWorkQueue queue; std::array<unsigned, 3> stage; };
  // 0 absent, 1 reserved, 2 prepared, 3 published, 4 consumed, 5 completed.
  std::deque<Node> todo;
  std::set<unsigned> seen;
  auto add = [&](Node node) {
    unsigned key = 0;
    for (auto stage : node.stage) key = key*6+stage;
    if (seen.insert(key).second) todo.push_back(std::move(node));
  };
  add({GinWorkQueue(2, 3), {0, 0, 0}});
  while (!todo.empty()) {
    auto node = todo.front();todo.pop_front();++explored_states;
    const auto prefix = [&](unsigned threshold) {
      uint64_t count = 0;
      for (auto stage : node.stage) { if (stage < threshold) break; ++count; }
      return count;
    };
    const auto state = node.queue.Snapshot();
    Check(state.reserved == prefix(1));
    Check(state.prepared_prefix == prefix(2));
    Check(state.published == prefix(3));
    Check(state.consumed == prefix(4));
    Check(state.completed == prefix(5));
    Check(node.queue.Drained() == (prefix(1) == prefix(5)));
    for (uint64_t target = 0; target <= prefix(1); ++target)
      Check(node.queue.FlushComplete(target) == (target <= prefix(5)));
    for (unsigned i = 0; i < 3; ++i) {
      const bool writable = node.stage[i] == 1 && (i < 2 || node.stage[i-2] == 5);
      Check(node.queue.CanPrepare(i) == writable);
      if (writable) {
        auto next = node;next.queue.Prepare(i);next.stage[i] = 2;add(std::move(next));
      }
    }
    if (prefix(1) < 3) {
      const auto i = prefix(1);
      auto next = node;next.queue.Reserve(100+i, 77);next.stage[i] = 1;add(std::move(next));
    }
    for (uint64_t end = prefix(3)+1; end <= prefix(2); ++end) {
      auto next = node;next.queue.PublishThrough(end);
      for (uint64_t i = prefix(3); i < end; ++i) next.stage[i] = 3;
      add(std::move(next));
    }
    if (prefix(4) < prefix(3)) {
      auto next = node;const auto work = next.queue.ConsumePublished();
      Check(work && work->work_id == 100+prefix(4) && work->operation == 77);
      next.stage[prefix(4)] = 4;add(std::move(next));
    }
    for (uint64_t end = prefix(5)+1; end <= prefix(4); ++end) {
      auto next = node;next.queue.CompleteThrough(end);
      for (uint64_t i = prefix(5); i < end; ++i) next.stage[i] = 5;
      add(std::move(next));
    }
  }
  Check(seen.count(5*36+5*6+5) == 1); // All three can eventually complete.
}

int main() {
  SlotAndPublication();FlushAndIdleReuse();IsolatedQueuesAndBinding();InvalidAndBudget();LongAppend();
  ExploreInterleavings();BatchReservation();ExploreBatchInterleavings();
  std::cout << "{\"passed\":true,\"checks\":" << checks
            << ",\"explored_states\":" << explored_states
            << ",\"batch_explored_states\":" << batch_explored_states
            << ",\"appended_work\":1024,\"network_execution\":false,\"timed\":false}\n";
}
