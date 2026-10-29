#pragma once
#include "persistent_streams.hh"

namespace moe_rdma {
// Message transport only: ASTRA retains all graph/resource scheduling.
class ServiceTransport {
 public:
  ServiceTransport(Fabric&,const Json& config=Json());
  void Register(const Json& messages);
  void Send(uint64_t id,unsigned src,unsigned dst,uint64_t bytes,unsigned plane,
            Fabric::Done acked,Fabric::Done received);
  void Finish(Fabric::Done);
  bool Persistent() const {return bool(streams_);}
  Json Report() const;
 private:
  void CheckDrain();
  Fabric& fabric_;
  std::unique_ptr<PersistentStreams> streams_;
  std::map<std::tuple<unsigned,unsigned,unsigned>,uint64_t> bindings_;
  std::map<uint64_t,Json> messages_;
  std::map<uint64_t,uint64_t> totals_;
  std::set<uint64_t> sent_;
  uint64_t max_work_=0;
  Fabric::Done finish_;
};
}
