#pragma once
#include "fabric.hh"
#include "gin_work_queue.hh"
#include "dcqcn_reference.hh"
#include "selective_repeat.hh"
#include "nack_grace.hh"
#include "ns3/rdma-hw.h"
#include <deque>

namespace moe_rdma {
// Native RC BYTE-WRITE functional adapter. Not a GIN/provider implementation:
// no remote GPU visibility, atomic verbs, memory windows or real CQ formats.
// Local queue credit uses transport ACK, a declared model rather than measured
// GPU source-buffer reuse. All callbacks execute on the existing ns-3 clock.
class PersistentStreams {
 public:
  struct Limits { unsigned streams=16, work=64, link_transmissions=200000; };
  using Done = std::function<void()>;
  explicit PersistentStreams(Fabric& fabric);
  PersistentStreams(Fabric& fabric, Limits limits);
  PersistentStreams(Fabric& fabric, Limits limits, const Json& recovery, bool transaction_peer_copies=false);
  ~PersistentStreams();
  // Passive samples in existing callbacks; no timers or periodic polling.
  void ConfigureRateCapture(const Json& config);
  void ConfigureDcqcnReference(const Json& config);
  static void ValidateDcqcnReference(const Json& config,int64_t max_ns);
  // Called by native entry points before Fabric allocates a network.
  static void ValidateReordering(const Json& recovery,int64_t max_ns);
  void ValidateFaultBindings() const;
  void Open(uint64_t id, unsigned src, unsigned dst, unsigned plane, uint16_t wire_port,
            unsigned source_nic, unsigned destination_nic, unsigned depth);
  void Submit(uint64_t stream, uint64_t work, uint64_t bytes, Done accepted, Done acked);
  using Bytes = std::vector<uint8_t>;
  // Optional functional payload transport. Receiver sees ONLY newly accepted
  // packet bytes, never source Work metadata or a lookup of a remote operation.
  void SetReceiver(uint64_t stream, std::function<void(Bytes)> receiver);
  void SubmitPayload(uint64_t stream, uint64_t work, Bytes payload, Done acked);
  void ConfigurePayloadPool(uint64_t bytes_per_source,bool release_on_isolation=false);
  bool CanSubmitPayload(uint64_t stream,uint64_t bytes) const;
  bool Drained() const;
  void Close();
  Json Report() const;
  uint64_t LinkTransmissions() const { return observations_; }
  Done changed;
 private:
  struct Work {
    uint64_t id, bytes, ticket, begin=0, end=0;
    int64_t ready=-1, published=-1, received=-1, acked=-1;
    int64_t payload_discarded=-1;
    Done accepted_callback, acked_callback;
    Bytes payload;
  };
  struct Stream {
    struct Reference {
      DcqcnNp np;
      DcqcnRp rp;
      ns3::EventId np_event,alpha_event,recovery_event;
      uint16_t ipid=0;
      Reference(int64_t period,DcqcnRpConfig config,uint64_t maximum):np(period),rp(config,maximum) {}
    };
    unsigned src, dst, plane, source_nic, destination_nic;
    uint16_t wire_port;
    ns3::Ptr<ns3::RdmaQueuePair> qp;
    GinWorkQueue queue;
    std::deque<Work> work;
    uint64_t submitted_bytes=0, accepted_prefix=0, acked_prefix=0, packets=0, tx_payload=0;
    std::size_t published_prefix=0;
    unsigned scheduler_insertions=0;
    std::function<void(Bytes)> receiver;
    std::unique_ptr<Reference> reference;
    std::unique_ptr<moe_cosim::SourceRecovery> recovery;
    std::unique_ptr<moe_cosim::SelectiveSender> selective_sender;
    std::unique_ptr<moe_cosim::SelectiveReceiver> selective_receiver;
    std::unique_ptr<moe_cosim::NackGrace> nack_grace;
    int64_t grace_trigger_uid=-1;
    std::map<uint32_t,ns3::Ptr<ns3::Packet>> reorder_packets;
    ns3::EventId recovery_event,grace_event;
    int64_t failure_ns=-1;
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
  void ReferenceLog(Stream&,const char*,ns3::Ptr<const ns3::Packet> packet=nullptr);
  void ReferenceMark(Stream&,ns3::Ptr<const ns3::Packet>);
  void ReferenceNpTimer(uint64_t stream);
  void ScheduleNp(Stream&);
  void SendCnp(Stream&);
  void ScheduleRp(Stream&);
  void ReferenceAlpha(uint64_t stream);
  void ReferenceRecovery(uint64_t stream);
  void ApplyReferenceRate(Stream&);
  void PacketSent(unsigned node,ns3::Ptr<ns3::RdmaQueuePair>,ns3::Ptr<ns3::Packet>,ns3::Time);
  struct RateState {
    uint64_t rate, target, maximum, snd_nxt, snd_una, published;
    int64_t next_avail;
    double alpha;
    uint32_t stage;
    bool first_cnp, alpha_cnp, decrease_cnp;
  };
  struct RateSample {
    int64_t time, uid, sequence;
    uint64_t stream;
    unsigned event, bytes, cnp;
    RateState state;
  };
  static RateState ReadRate(const Stream&);
  static Json RateValues(const RateState&);
  void SampleRate(const Stream&, unsigned event, ns3::Ptr<const ns3::Packet>, uint64_t sequence, unsigned bytes, unsigned cnp);
  void ArmRecovery(Stream&);
  void RecoveryTimeout(uint64_t stream);
  Json GraceState(const Stream&) const;
  void GraceLog(Stream&,const char*,const Json&,int64_t uid=-1,int64_t cumulative=-1,bool nack=false);
  void GraceFeedback(Stream&,ns3::Ptr<const ns3::Packet>,uint64_t cumulative,bool nack);
  void GraceTimeout(uint64_t stream);
  void CancelGrace(Stream&,const char* reason,bool rto=false);
  void DuplicateAck(Stream&, ns3::CustomHeader&, ns3::Ptr<ns3::RdmaRxQueuePair>);
  int SelectiveReceive(Stream&,ns3::Ptr<ns3::Packet>,ns3::CustomHeader&);
  void SelectiveLog(Stream&,const char*,ns3::Ptr<const ns3::Packet>,const moe_cosim::SelectiveAck&);
  void RecoveryRecord(Stream&, unsigned code);
  void TerminalFeedback(Stream&,ns3::Ptr<const ns3::Packet>,unsigned protocol,uint64_t sequence);
  bool WireIdle() const;
  static void Tx(PersistentStreams*,unsigned,unsigned,ns3::Ptr<const ns3::Packet>);
  static void Rx(PersistentStreams*,unsigned,unsigned,ns3::Ptr<const ns3::Packet>);
  static void Drop(PersistentStreams*,unsigned,unsigned,ns3::Ptr<const ns3::Packet>);
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
  uint64_t rate_limit_=0;
  int64_t rate_start_=0,rate_end_=0;
  std::vector<RateSample> rate_samples_;
  Json reference_config_,reference_events_=Json::array();
  uint64_t reference_limit_=0;
  Json recovery_config_;
  std::unique_ptr<RecoveryRecords> recovery_records_;
  uint64_t duplicate_acks_=0, old_feedback_=0;
  int64_t failed_stream_=-1, failure_ns_=-1;
  bool isolate_failures_=false;
  uint64_t terminal_feedback_count_=0;
  Json terminal_feedback_=Json::array();
  uint64_t payload_capacity_=0;
  bool release_payload_on_isolation_=false;
  bool selective_mode_=false;
  uint64_t selective_capacity_=0,selective_window_=0;
  Json selective_events_=Json::array();
  int64_t grace_wait_=0;
  uint64_t grace_limit_=0;
  Json grace_events_=Json::array();
  std::map<unsigned,uint64_t> payload_used_,payload_peak_;
  Json payload_events_=Json::array();
};
}
