#pragma once
#include "fabric.hh"
#include "gin_work_queue.hh"
#include "ns3/rdma-hw.h"
#include <deque>

namespace moe_rdma {
// Native RC BYTE-WRITE functional adapter. Not a GIN/provider implementation:
// no remote GPU visibility, atomic verbs, memory windows or real CQ formats.
// Local queue credit uses transport ACK, a declared model rather than measured
// GPU source-buffer reuse. All callbacks execute on the existing ns-3 clock.
class PersistentStreams {
 public:
  struct Limits { unsigned streams=16, work=64; };
  using Done = std::function<void()>;
  explicit PersistentStreams(Fabric& fabric);
  PersistentStreams(Fabric& fabric, Limits limits);
  void Open(uint64_t id, unsigned src, unsigned dst, unsigned plane, uint16_t wire_port,
            unsigned source_nic, unsigned destination_nic, unsigned depth);
  void Submit(uint64_t stream, uint64_t work, uint64_t bytes, Done accepted, Done acked);
  using Bytes = std::vector<uint8_t>;
  // Optional functional payload transport. Receiver sees ONLY newly accepted
  // packet bytes, never source Work metadata or a lookup of a remote operation.
  void SetReceiver(uint64_t stream, std::function<void(Bytes)> receiver);
  void SubmitPayload(uint64_t stream, uint64_t work, Bytes payload, Done acked);
  bool Drained() const;
  void Close();
  Json Report() const;
  Done changed;
 private:
  struct Work {
    uint64_t id, bytes, ticket, begin=0, end=0;
    int64_t ready=-1, published=-1, received=-1, acked=-1;
    Done accepted_callback, acked_callback;
    Bytes payload;
  };
  struct Stream {
    unsigned src, dst, plane, source_nic, destination_nic;
    uint16_t wire_port;
    ns3::Ptr<ns3::RdmaQueuePair> qp;
    GinWorkQueue queue;
    std::deque<Work> work;
    uint64_t submitted_bytes=0, accepted_prefix=0, acked_prefix=0, packets=0, tx_payload=0;
    std::size_t published_prefix=0;
    unsigned scheduler_insertions=0;
    std::function<void(Bytes)> receiver;
    Stream(unsigned s,unsigned d,unsigned p,unsigned sn,unsigned dn,uint16_t w,unsigned depth,unsigned maximum)
      : src(s),dst(d),plane(p),source_nic(sn),destination_nic(dn),wire_port(w),queue(depth,maximum) {}
  };
  int Receive(unsigned node, ns3::Ptr<ns3::Packet> packet, ns3::CustomHeader& header);
  ns3::Ptr<ns3::Packet> Next(unsigned node, ns3::Ptr<ns3::RdmaQueuePair> qp);
  Stream* Match(unsigned node, const ns3::CustomHeader& header, bool source);
  Stream* ByPort(uint16_t port);
  void Pump(Stream&);
  void Enqueue(uint64_t stream, uint64_t work, uint64_t bytes, Bytes payload, Done accepted, Done acked);
  void CheckAccepted(Stream&);
  void Notify();
  static void Tx(PersistentStreams*,unsigned,unsigned,ns3::Ptr<const ns3::Packet>);
  static void Rx(PersistentStreams*,unsigned,unsigned,ns3::Ptr<const ns3::Packet>);
  Fabric& fabric_;
  Limits limits_;
  std::map<uint64_t,Stream> streams_;
  std::map<uint16_t,uint64_t> wire_streams_;
  std::set<uint64_t> work_ids_;
  int64_t inflight_=0;
  bool closed_=false, notification_pending_=false;
  uint64_t observations_=0;
  std::vector<std::array<int64_t,8>> source_packets_;
  Json payload_packets_=Json::array();
  uint64_t captured_payload_bytes_=0;
};
}
