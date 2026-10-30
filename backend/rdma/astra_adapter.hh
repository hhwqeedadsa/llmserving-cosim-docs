#pragma once
#include "fabric.hh"
#include "service_transport.hh"
#include "astra-sim/common/AstraNetworkAPI.hh"

namespace moe_rdma {
// Every rank adapter shares this registry and the same Fabric/Simulator.
class Messages {
 public:
  Messages(Fabric& fabric,ServiceTransport& transport): fabric_(fabric),transport_(transport) {}
  void Register(const Json& messages,const std::string& graph_id="");
  using Observer=std::function<void(const std::string&,const std::string&,uint64_t,int)>;
  void SetObserver(Observer observer) {observer_=std::move(observer);}
  void Post(bool send, int rank, uint64_t bytes, int type, int peer, int tag,
            void (*callback)(void*), void* argument);
  bool Complete(const std::vector<uint64_t>& messages) const;
  Json Report();
  Json HostCounters() const;
 private:
  struct Operation {
    int src=0,dst=0;
    unsigned plane=0;
    uint64_t bytes=0;
    uint64_t id=0;
    std::string graph_id;
    int64_t send_post=-1,recv_post=-1,send_callback=-1,recv_callback=-1;
    bool sent=false, posted=false, received=false, acked=false;
    bool send_called=false, recv_called=false;
    std::function<void()> send_done, recv_done;
  };
  void Deliver(uint64_t id);
  Fabric& fabric_;
  ServiceTransport& transport_;
  std::map<uint64_t,Operation> operations_;
  std::map<std::tuple<int,int,int>,uint64_t> keys_;
  uint64_t send_posts_=0,recv_posts_=0,send_callbacks_=0,recv_callbacks_=0;
  Observer observer_;
};

class Adapter final: public AstraSim::AstraNetworkAPI {
 public:
  Adapter(int rank, Messages& messages, std::function<void()> finished):
    AstraNetworkAPI(rank), messages_(messages), finished_(std::move(finished)) {}
  BackendType get_backend_type() override { return BackendType::NS3; }
  AstraSim::timespec_t sim_get_time() override;
  void sim_schedule(AstraSim::timespec_t,void(*)(void*),void*) override;
  void sim_notify_finished() override { finished_(); }
  int sim_send(void*,uint64_t,int,int,int,AstraSim::sim_request*,void(*)(void*),void*) override;
  int sim_recv(void*,uint64_t,int,int,int,AstraSim::sim_request*,void(*)(void*),void*) override;
 private:
  Messages& messages_;
  std::function<void()> finished_;
};
}
