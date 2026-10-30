#include "rdma/low_latency_posting.hh"
#include <array>
#include <iostream>
#include <queue>
#include <set>
#include <tuple>

using moe_rdma::LowLatencyPostingQueue;
using moe_rdma::RegisteredSpan;
using moe_rdma::SplitRegisteredPut;
static unsigned checks = 0;
static void Check(bool value) {
  ++checks;
  if (!value) throw std::runtime_error("posting check failed at assertion " + std::to_string(checks));
}
template <class F> static void Reject(F action) {
  bool rejected = false;
  try { action(); } catch (const std::exception&) { rejected = true; }
  Check(rejected);
}
static void Prepare(LowLatencyPostingQueue& q, const LowLatencyPostingQueue::Operation& op) {
  for (auto ticket = op.begin; ticket < op.end; ++ticket) q.Prepare(ticket);
}

static void SplitAndReject() {
  const std::vector<RegisteredSpan> table{{0,32768}, {32768,65536}};
  Check(SplitRegisteredPut(0,0,14352,table,table) == std::vector<uint32_t>{14352});
  Check(SplitRegisteredPut(30000,30000,14352,table,table) == std::vector<uint32_t>({2768,11584}));
  Check(SplitRegisteredPut(25000,30000,14352,table,table) == std::vector<uint32_t>({2768,5000,6584}));
  Reject([&] { SplitRegisteredPut(0,0,0,table,table); });
  Reject([&] { SplitRegisteredPut(0,0,1048577,table,table); });
  Reject([&] { SplitRegisteredPut(UINT64_MAX,0,1,table,table); });
  Reject([&] { SplitRegisteredPut(0,0,1,{},table); });
  Reject([&] { SplitRegisteredPut(0,0,1,{{0,2},{1,3}},table); });
  Reject([&] { SplitRegisteredPut(0,0,1,{{2,3},{0,1}},table); });
  Reject([&] { SplitRegisteredPut(0,0,20,{{0,10},{20,30}},table); });
  std::vector<RegisteredSpan> many;
  for (unsigned i=0;i<33;++i) many.push_back({i,i+1});
  Reject([&] { SplitRegisteredPut(0,0,33,many,table); });
}

static void BatchesAndNotification() {
  for (const unsigned count : {0u,1u,3u,4u,5u,8u,9u})
    for (const unsigned pieces : {1u,2u,3u}) {
      LowLatencyPostingQueue q(32,64);
      for (unsigned i=0;i<count;++i) {
        const auto op=q.ReservePut(i,i,std::vector<uint32_t>(pieces,17));
        Prepare(q,op);
        const auto result=q.Post(i);
        Check(result.eligible == ((i+1)%4==0));
        Check(q.Snapshot().published == ((i+1)/4)*4*pieces);
        Check(q.Snapshot().completed == 0);
      }
      const auto snapshot=q.FlushTarget();
      Check(snapshot == count*pieces);
      Check(q.Snapshot().published == count/4*4*pieces);
      Check(q.FlushComplete(0));
      if(count%4) Reject([&] { q.Close(); }); // No automatic tail publication.
      const auto marker=q.ReserveNotification(100);
      Prepare(q,marker);const auto post=q.Post(100);
      Check(post.eligible && post.published == count*pieces+1);
      Check(!q.FlushComplete(snapshot) || snapshot==0);
      uint64_t next=0;
      while(const auto work=q.ConsumePublished()) {
        Check(work->ticket == next++);
        if(work->operation!=100) Check(work->operation == work->ticket/pieces);
      }
      Check(next==count*pieces+1 && q.Snapshot().completed==0);
      Reject([&] { q.Close(); });
      q.CompleteThrough(next);Check(q.FlushComplete(snapshot) && q.Drained());q.Close();
    }
}

static void HolesAndLatePost() {
  LowLatencyPostingQueue q(4,8);
  const auto a=q.ReservePut(10,3,{10,20});
  const auto b=q.ReserveNotification(20);
  q.Prepare(b.begin);Check(!q.CanPost(20));
  Reject([&] { q.Post(20); });Check(q.Snapshot().published==0 && !q.ConsumePublished());
  q.Prepare(a.begin);Check(!q.CanPost(10) && !q.CanPost(20));
  q.Prepare(a.begin+1);Check(q.CanPost(10) && q.CanPost(20));
  q.Post(20);Check(q.Snapshot().published==3);
  while(q.ConsumePublished()) {}
  q.CompleteThrough(3);Check(q.FlushComplete(3) && !q.Drained());
  Reject([&] { q.Close(); }); // Earlier producer has not returned from posting.
  const auto late=q.Post(10);
  Check(late.eligible && late.requested==2 && late.previous==3 && late.published==3);
  Check(!q.ConsumePublished() && q.Drained());
  Reject([&] { q.Post(10); });q.Close();
}

static void CreditAndInvalid() {
  LowLatencyPostingQueue q(4,12);
  const auto a=q.ReservePut(1,3,{1,2,3});Prepare(q,a);q.Post(1);
  while(q.ConsumePublished()) {}
  q.CompleteThrough(1);
  const auto b=q.ReservePut(2,3,{4,5,6});
  Check(!q.CanPrepare(b.begin));
  Reject([&] { q.Prepare(b.begin); });
  q.CompleteThrough(2);Check(q.CanPrepare(b.begin));
  q.Prepare(b.begin+2);q.Prepare(b.begin+1);q.Prepare(b.begin);q.Post(2);
  while(q.ConsumePublished()) {}
  q.CompleteThrough(6);q.Close();
  Reject([&] { q.ReserveNotification(3); });
  Reject([&] { q.Post(2); });
  Reject([&] { q.CompleteThrough(6); });
  LowLatencyPostingQueue bounded(2,4);
  Reject([&] { bounded.ReservePut(1,0,{1,2,3}); });
  Reject([&] { bounded.ReservePut(1,0,{}); });
  Reject([&] { bounded.ReservePut(1,0,{0}); });
  Reject([&] { bounded.ReservePut(1,UINT32_MAX,{1}); });
  Check(bounded.ReservePut(1,0,{10}).begin==0);
  Reject([&] { bounded.ReserveNotification(1); });
  Reject([&] { bounded.Post(999); });
  Check(bounded.Snapshot().reserved==1);
  Prepare(bounded,bounded.Get(1));bounded.Post(1);
  Check(bounded.Snapshot().published==0); // One put cannot release its own tail.
  const auto marker=bounded.ReserveNotification(2);Prepare(bounded,marker);bounded.Post(2);
  while(bounded.ConsumePublished()) {}
  bounded.CompleteThrough(2);
  // Completing the old work enables an idle queue to accept another call.
  const auto next=bounded.ReservePut(3,3,{9});Prepare(bounded,next);bounded.Post(3);
  while(bounded.ConsumePublished()) {}
  bounded.CompleteThrough(3);bounded.Close();
  LowLatencyPostingQueue other(2,4);
  const auto isolated=other.ReservePut(1,0,{1});Prepare(other,isolated);other.Post(1);
  Check(other.Snapshot().published==0 && other.Snapshot().completed==0);
}

// Independent finite oracle: individual prepared bits, producer-return bits and
// explicit NIC/CQ positions. Legal actions are generated WITHOUT querying the core.
static unsigned Enumerate() {
  struct Node {
    LowLatencyPostingQueue q;
    unsigned prepared=0, posted=0, published=0, consumed=0, completed=0;
  };
  using Key=std::tuple<unsigned,unsigned,unsigned,unsigned,unsigned>;
  std::queue<Node> pending;
  std::set<Key> visited;
  Node first{LowLatencyPostingQueue(4,4)};
  first.q.ReservePut(10,3,{1});first.q.ReservePut(20,0,{2,3});first.q.ReserveNotification(30);
  const auto add=[&](Node n) {
    if(visited.insert({n.prepared,n.posted,n.published,n.consumed,n.completed}).second) pending.push(std::move(n));
  };
  add(std::move(first));
  const std::array<unsigned,3> ends{{1,3,4}};
  while(!pending.empty()) {
    auto n=std::move(pending.front());pending.pop();
    unsigned ready=0;while(ready<4 && (n.prepared&(1u<<ready)))++ready;
    const auto s=n.q.Snapshot();
    Check(s.reserved==4 && s.prepared_prefix==ready && s.published==n.published &&
          s.consumed==n.consumed && s.completed==n.completed);
    Check(n.q.Drained()==(n.completed==4 && n.posted==7));
    for(unsigned i=0;i<3;++i)
      Check(n.q.CanPost(10*(i+1))==(!(n.posted&(1u<<i)) && ready>=ends[i]));
    for(unsigned i=0;i<4;++i) if(!(n.prepared&(1u<<i))) {
      auto next=n;next.q.Prepare(i);next.prepared|=1u<<i;add(std::move(next));
    }
    for(unsigned i=0;i<3;++i) if(!(n.posted&(1u<<i)) && ready>=ends[i]) {
      auto next=n;next.q.Post(10*(i+1));next.posted|=1u<<i;
      if(i!=1)next.published=std::max(next.published,ends[i]);
      add(std::move(next));
    }
    if(n.consumed<n.published) {
      auto next=n;const auto work=next.q.ConsumePublished();Check(work && work->ticket==n.consumed);
      ++next.consumed;add(std::move(next));
    }
    for(unsigned end=n.completed+1;end<=n.consumed;++end) {
      auto next=n;next.q.CompleteThrough(end);next.completed=end;add(std::move(next));
    }
    if(n.completed==4 && n.posted==7) {n.q.Close();Check(n.q.Snapshot().closed);}
  }
  Check(visited.size()>40);
  return visited.size();
}

int main() {
  SplitAndReject();BatchesAndNotification();HolesAndLatePost();CreditAndInvalid();
  const auto states=Enumerate();
  std::cout << "{\"passed\":true,\"checks\":" << checks << ",\"oracle_states\":" << states
            << ",\"network_execution\":false,\"hardware_calibrated\":false}\n";
}
