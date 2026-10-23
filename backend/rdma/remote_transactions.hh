#pragma once
#include "persistent_streams.hh"
#include "remote_memory.hh"

namespace moe_rdma {
// Functional addressed transactions over MRT1/native byte streams. Not a GIN
// provider or RoCE timing baseline. Request ACK cannot complete FetchAdd.
class RemoteTransactions {
 public:
  explicit RemoteTransactions(Fabric&,const Json& input);
  void Submit(const Json& operation);
  Json LocalWaits(const Json& operation) const;
  bool LocalReady(const Json& operation) const;
  // Consume only an admitted local node's already-satisfied local predicates.
  // Admission is separate from starting its declared-duration GPU lane.
  void AdmitLocal(const Json& operation);
  bool QueueFenceReady(const Json& operation);
  bool Occurred(uint64_t operation,const std::string& event) const;
  bool Drained() const;
  void Close();
  Json Report() const;
  std::function<void()> changed;
 private:
  struct Operation {
    uint64_t id=0,connection=0,ticket=0;
    MemoryFrame request;
    int64_t ready=-1,published=-1,applied=-1,request_ack=-1,response=-1,completed=-1;
    uint64_t returned_value=0;
  };
  struct Connection {
    unsigned src,dst;
    GinWorkQueue queue;
    MemoryFrameDecoder requests,responses;
    std::vector<uint64_t> operations;
    Connection(unsigned s,unsigned d,unsigned depth):src(s),dst(d),queue(depth,32) {}
  };
  void Pump(uint64_t connection);
  void Retire(uint64_t connection);
  void Request(uint64_t connection,const MemoryFrame& frame);
  void Response(uint64_t connection,const MemoryFrame& frame);
  unsigned LocalOwner(const Json& operation) const;
  void ConsumeConditions(const Json& operation,const Json& observations);
  PersistentStreams transport_;
  RemoteMemory memory_;
  std::map<uint64_t,Connection> connections_;
  std::map<uint64_t,Operation> operations_;
  uint64_t transport_work_=0,duplicates_=0,memory_order_=0;
  Json target_events_=Json::array(),local_events_=Json::array();
  Json queue_fences_=Json::object();
};
std::vector<uint8_t> MemoryUnhex(const std::string&);
std::string MemoryHex(const std::vector<uint8_t>&);
}
