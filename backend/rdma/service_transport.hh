#pragma once
#include "persistent_streams.hh"
#include "message_buffers.hh"

namespace moe_rdma {
// Message transport only: ASTRA retains all graph/resource scheduling.
class ServiceTransport {
 public:
  ServiceTransport(Fabric&,const Json& config=Json());
  void Register(const Json& messages);
  void Send(uint64_t id,unsigned src,unsigned dst,uint64_t bytes,unsigned plane,
            Fabric::Done acked,Fabric::Done received);
  void Finish(Fabric::Done);
  // Explicit functional execution-scope retirement, never a fabricated ACK.
  void FinishMessageScopes(const std::vector<uint64_t>& messages);
  bool Persistent() const {return bool(streams_);}
  Json Report() const;
 private:
  void CheckDrain();
  Fabric& fabric_;
  std::unique_ptr<PersistentStreams> streams_;
  std::unique_ptr<MessageBuffers> buffers_;
  Json buffer_configuration_;
  struct Incoming {std::deque<uint64_t> pending;};
  std::map<uint64_t,Incoming> incoming_;
  std::map<uint64_t,Fabric::Done> received_callbacks_;
  void ReceiveBytes(uint64_t stream,PersistentStreams::Bytes bytes);
  Json BufferReport() const;
  std::map<std::tuple<unsigned,unsigned,unsigned>,uint64_t> bindings_;
  std::map<uint64_t,Json> messages_;
  std::map<uint64_t,uint64_t> totals_;
  std::set<uint64_t> sent_;
  uint64_t max_work_=0;
  Fabric::Done finish_;
};
}
