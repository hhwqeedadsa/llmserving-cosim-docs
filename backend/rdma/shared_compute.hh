#pragma once
#include "fabric.hh"
#include <deque>

namespace moe_rdma {
// Declared nonpreemptive FIFO, one engine per physical simulated GPU. This is
// neither a CUDA concurrency model nor measured SM occupancy. GATEs stay out.
class SharedCompute {
 public:
  using Done=std::function<void()>;
  SharedCompute(unsigned ranks,uint64_t maximum):queues_(ranks),busy_(ranks,false),maximum_(maximum) {
    Require(ranks>0 && maximum>0 && maximum<=131072,"invalid shared compute budget");
  }
  void Submit(const std::string& key,unsigned rank,int64_t duration,Json identity,Done begin,Done done) {
    Require(rank<queues_.size() && duration>=0 && duration<=1000000000 && bool(done),"invalid shared compute work");
    Require(rows_.size()<maximum_ && !rows_.count(key),"shared compute task identity/budget exceeded");
    const auto now=ns3::Simulator::Now().GetNanoSeconds();
    const auto sequence=rows_.size();rows_[key]=std::move(identity);
    auto& row=rows_.at(key);row["key"]=key;row["rank"]=rank;row["duration_ns"]=duration;
    row["ready_ns"]=now;row["start_ns"]=nullptr;row["done_ns"]=nullptr;
    row["sequence"]=sequence;
    queues_[rank].push_back({key,duration,std::move(begin),std::move(done)});
    Pump(rank);
  }
  bool Empty() const {
    for(unsigned rank=0;rank<queues_.size();++rank)if(busy_[rank] || !queues_[rank].empty())return false;
    return true;
  }
  Json Report() const {
    Json tasks=Json::array();for(const auto& [_,row]:rows_)tasks.push_back(row);
    return {{"mode","serial_per_gpu/1"},{"tasks",tasks},{"empty",Empty()},
      {"scope","declared FIFO shared by original COMP and helper work; zero-compute phase gates excluded; not GPU hardware calibration"}};
  }
 private:
  struct Work {std::string key;int64_t duration;Done begin,done;};
  void Pump(unsigned rank) {
    if(busy_[rank] || queues_[rank].empty())return;
    busy_[rank]=true;auto work=std::move(queues_[rank].front());queues_[rank].pop_front();
    rows_.at(work.key)["start_ns"]=ns3::Simulator::Now().GetNanoSeconds();
    if(work.begin)work.begin();
    ns3::Simulator::Schedule(ns3::NanoSeconds(work.duration),[this,rank,work=std::move(work)]() mutable {
      rows_.at(work.key)["done_ns"]=ns3::Simulator::Now().GetNanoSeconds();busy_[rank]=false;
      // Previously queued work starts before newly unblocked work is admitted.
      Pump(rank);auto done=std::move(work.done);done();
    });
  }
  std::vector<std::deque<Work>> queues_;
  std::vector<bool> busy_;
  uint64_t maximum_;
  std::map<std::string,Json> rows_;
};
}
