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
  void ValidateLocalMemory(const Json& operation) const;
  void StartLocal(const Json& operation);
  void CompleteLocal(const Json& operation);
  void ValidateSource(const Json& operation) const;
  bool QueueFenceReady(const Json& operation);
  bool Occurred(uint64_t operation,const std::string& event) const;
  bool Drained() const;
  void Close();
  Json Report() const;
  // Actual operation transitions only; no remote memory/payload truth.
  Json OperationProgress() const;
  std::function<void()> changed;
 private:
  struct Operation {
    uint64_t id=0,connection=0,ticket=0;
    MemoryFrame request;
    int64_t prepared=-1,ready=-1,published=-1,applied=-1,request_ack=-1,response=-1,completed=-1;
    uint64_t returned_value=0;
    bool aggregate=false,has_source=false;
    RemoteMemory::ReadLease source{};
  };
  struct Connection {
    unsigned src,dst,destination_nic;
    GinWorkQueue queue;
    MemoryFrameDecoder requests,responses;
    std::vector<uint64_t> operations;
    Connection(unsigned s,unsigned d,unsigned nic,unsigned depth):src(s),dst(d),destination_nic(nic),queue(depth,32) {}
  };
  void Pump(uint64_t connection);
  void PumpExplicit(uint64_t connection);
  void Send(Operation& op);
  void Retire(uint64_t connection);
  void Request(uint64_t connection,const MemoryFrame& frame);
  void Response(uint64_t connection,const MemoryFrame& frame);
  unsigned LocalOwner(const Json& operation) const;
  void ConsumeConditions(const Json& operation,const Json& observations);
  RemoteMemory::ReadLease Source(const Json& operation) const;
  void SourceEvent(uint64_t id,const char* kind,RemoteMemory::ReadLease source,const std::vector<uint8_t>* bytes=nullptr);
  struct LocalCopy {
    RemoteMemory::ReadLease source,target;
    std::vector<uint8_t> captured;
    bool started=false,completed=false;
  };
  LocalCopy Copy(const Json& operation) const;
  void LocalMemoryEvent(uint64_t id,const char* kind,const LocalCopy& copy,const std::vector<uint8_t>* bytes=nullptr);
  PersistentStreams transport_;
  RemoteMemory memory_;
  std::map<uint64_t,Connection> connections_;
  std::map<uint64_t,Operation> operations_;
  uint64_t transport_work_=0,duplicates_=0,memory_order_=0;
  Json target_events_=Json::array(),local_events_=Json::array();
  Json queue_fences_=Json::object();
  bool explicit_submission_=false;
  Json publications_=Json::array();
  bool source_mode_=false;
  Json source_model_,source_events_=Json::array();
  bool local_memory_mode_=false;
  Json local_memory_model_,local_memory_events_=Json::array();
  std::map<uint64_t,LocalCopy> local_copies_;
  bool bounded_buffers_=false;
  Json buffer_model_;
  std::map<unsigned,uint64_t> source_scratch_peaks_,local_copy_peaks_;
};
std::vector<uint8_t> MemoryUnhex(const std::string&);
std::string MemoryHex(const std::vector<uint8_t>&);
}
