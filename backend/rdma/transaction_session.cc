#include "transaction_session_validation.hh"
#include <deque>
#include <fstream>
#include <iostream>
#include <link.h>

using namespace moe_rdma;
class TransactionSession {
 public:
  TransactionSession(Fabric& fabric,const Json& topology,const Json& input):fabric_(fabric),topology_(topology),input_(input),transactions_(fabric,input) {
    Require(input.at("operations").empty(),"session initial operations must be empty");
    transactions_.changed=[this](){Notify();};Notify();
  }
  bool Finished() const {return finished_;}
  Json Report() const {
    Json r={{"decisions",decision_},{"finished",finished_},{"registrations",registrations_},{"events",history_},
      {"scope","interactive MRT1 functional transactions on one native clock; frontend host wait is not simulated time"},
      {"modern_deepep_connected",false},{"hardware_calibrated",false},{"gpu_visibility_modeled",false}};
    return r;
  }
  Json Transactions() const {return transactions_.Report();}
  Json LocalWork() const {
    Json rows=Json::array();
    for(const auto& op:input_.at("operations"))if(op.at("kind")=="local") {
      const auto id=op.at("id").get<uint64_t>();
      const auto& t=local_times_.at(id);
      rows.push_back({{"id",id},{"rank",op.at("rank")},{"duration_ns",op.at("duration_ns")},
        {"ready_ns",t[0]},{"start_ns",t[1]},{"completed_ns",t[2]}});
    }
    return rows;
  }
  unsigned CompletedRemote() const {
    unsigned n=0;for(const auto& op:input_.at("operations"))if(op.at("kind")=="remote" && transactions_.Occurred(op.at("id"),"completed"))++n;return n;
  }
  unsigned CompletedLocal() const {
    unsigned n=0;for(const auto& [id,t]:local_times_)if(t[2]>=0)++n;return n;
  }
  Json Pending(bool local) const {
    Json result=Json::array();
    for(const auto& op:input_.at("operations"))if((op.at("kind")=="local")==local) {
      const auto id=op.at("id").get<uint64_t>();
      if(local?local_times_.at(id)[2]>=0:submitted_.count(id)>0)continue;
      Json row={{"id",id}};
      try {row["local_wait"]=transactions_.LocalWaits(op);}catch(const std::exception& e){row["local_wait"]=Json::array();row["local_wait_error"]=e.what();}
      if(local)row["admitted"]=submitted_.count(id)>0;
      result.push_back(row);
    }
    return result;
  }
 private:
  static int64_t Now(){return ns3::Simulator::Now().GetNanoSeconds();}
  const Json& Node(uint64_t id) const {return input_.at("operations").at(index_.at(id));}
  void Notify() {
    if(notify_pending_ || finished_)return;
    notify_pending_=true;
    ns3::Simulator::ScheduleNow([this](){notify_pending_=false;Progress();});
  }
  bool Occurred(uint64_t id,const std::string& event) const {
    return Node(id).at("kind")=="local"?local_times_.at(id)[2]>=0:transactions_.Occurred(id,event);
  }
  bool AllDone() const {return CompletedRemote()+CompletedLocal()==input_.at("operations").size();}
  // Persistent idle QPs deliberately remain registered until explicit Close.
  // The transport checks packets, ACK queues and unfinished byte work here;
  // Fabric::Drained additionally requires destroying those persistent QPs.
  bool Drained() const {return AllDone() && local_busy_.empty() && transactions_.Drained();}
  void PumpLocal(unsigned rank) {
    auto& q=local_queues_[rank];if(local_busy_.count(rank) || q.empty())return;
    const auto id=q.front();q.pop_front();local_busy_.insert(rank);
    local_times_.at(id)[1]=Now();transactions_.StartLocal(Node(id));
    ns3::Simulator::Schedule(ns3::NanoSeconds(Node(id).at("duration_ns").get<int64_t>()),[this,rank,id](){
      transactions_.CompleteLocal(Node(id));Require(local_busy_.erase(rank)==1,"duplicate session local completion");
      local_times_.at(id)[2]=Now();PumpLocal(rank);Notify();
    });
  }
  Json Events() {
    Json events=Json::array();
    for(const auto& op:transactions_.OperationProgress()) {
      const auto id=op.at("id").get<uint64_t>();
      for(const auto& pair:std::vector<std::pair<std::string,std::string>>{{"submitted","ready_ns"},{"prepared","prepared_ns"},{"published","published_ns"},{"applied","applied_ns"},
          {"request_acked","request_acked_ns"},{"response","response_ns"},{"completed","completed_ns"}}) {
        if(!op.contains(pair.second))continue;
        const auto t=op.at(pair.second).get<int64_t>();
        if(t>=0 && delivered_.emplace(id,pair.first).second) {
          Json e={{"operation",id},{"event",pair.first},{"time_ns",t}};
          if((pair.first=="response" || pair.first=="completed") && op.contains("returned_value"))e["returned_value"]=op.at("returned_value");
          events.push_back(e);
        }
      }
    }
    for(const auto& [id,t]:local_times_) {
      const std::array<const char*,3> names={"local_admitted","local_started","completed"};
      for(unsigned k=0;k<3;++k)if(t[k]>=0 && delivered_.emplace(id,names[k]).second)
        events.push_back({{"operation",id},{"event",names[k]},{"time_ns",t[k]}});
    }
    // Timestamp order is descriptive; ties retain actual collection order and
    // are not a claim that independent same-ns device callbacks are ordered.
    std::stable_sort(events.begin(),events.end(),[](const Json& a,const Json& b){return a.at("time_ns")<b.at("time_ns");});
    for(const auto& e:events)history_.push_back(e);
    return events;
  }
  void Exchange(Json events,bool drained) {
    Require(decision_<256,"session decision budget exceeded");
    std::cout<<"MOE_TX_EVENT "<<Json{{"type","DECIDE"},{"decision_id",decision_},{"now_ns",Now()},
      {"events",events},{"drained",drained}}.dump()<<std::endl;
    std::vector<char> buffer(1048577);
    Require(bool(std::cin.getline(buffer.data(),buffer.size())),"frontend EOF or oversized session command");
    const auto command=Json::parse(buffer.data());SessionKeys(command,{"type","decision_id","now_ns","submissions","finish"});
    Require(command.at("type")=="COMMIT" && SessionInteger(command.at("decision_id"))==decision_ &&
      SessionInteger(command.at("now_ns"))==static_cast<uint64_t>(Now()) && command.at("finish").is_boolean(),"session command identity/time/type mismatch");
    const auto& submissions=command.at("submissions");
    Require(submissions.is_array() && submissions.size()<=48,"invalid session submissions");
    Require(!command.at("finish").get<bool>() || (submissions.empty() && !input_.at("operations").empty() && drained),"premature session finish");
    // Pure validation of the entire candidate precedes registering/executing
    // any command member. Runtime lease/access failures are not rolled back.
    auto candidate=input_;
    for(const auto& op:submissions)candidate["operations"].push_back(op);
    ValidateSessionProgram(candidate,topology_,fabric_.MaxSimNs());
    ++decision_;
    if(command.at("finish").get<bool>()) {transactions_.Close();Require(fabric_.Drained(),"session resources remain after close");finished_=true;ns3::Simulator::Stop();return;}
    for(const auto& op:submissions) {
      const auto id=op.at("id").get<uint64_t>();
      index_[id]=input_.at("operations").size();input_["operations"].push_back(op);
      registrations_.push_back({{"operation",id},{"time_ns",Now()},{"decision_id",decision_-1}});
      if(op.at("kind")=="local")local_times_[id]={-1,-1,-1};
    }
    if(!submissions.empty())Notify();
    else Require(!drained,"empty idle session command cannot advance execution");
  }
  void Progress() {
    for(const auto& op:input_.at("operations")) {
      const auto id=op.at("id").get<uint64_t>();if(submitted_.count(id))continue;
      bool ready=true;
      for(const auto& d:op.at("after"))ready=ready && Occurred(d.at("operation"),d.at("event"));
      if(!ready || !transactions_.LocalReady(op) || !transactions_.QueueFenceReady(op))continue;
      if(op.at("kind")=="local") {
        transactions_.AdmitLocal(op);submitted_.insert(id);local_times_.at(id)[0]=Now();
        const auto rank=op.at("rank").get<unsigned>();local_queues_[rank].push_back(id);PumpLocal(rank);
      } else {transactions_.Submit(op);submitted_.insert(id);}
    }
    auto events=Events();const bool drained=Drained();
    if(decision_==0 || !events.empty() || (drained && !last_drained_))Exchange(events,drained);
    last_drained_=drained;
    if(!finished_ && !notify_pending_ && !AllDone() && transactions_.Drained() && local_busy_.empty())
      throw std::invalid_argument("session pending work has no event able to satisfy its conditions");
  }
  Fabric& fabric_;Json topology_,input_;RemoteTransactions transactions_;
  uint64_t decision_=0;bool notify_pending_=false,finished_=false,last_drained_=false;
  std::map<uint64_t,std::size_t> index_;std::set<uint64_t> submitted_;
  std::map<uint64_t,std::array<int64_t,3>> local_times_;
  std::map<unsigned,std::deque<uint64_t>> local_queues_;std::set<unsigned> local_busy_;
  std::set<std::pair<uint64_t,std::string>> delivered_;
  Json registrations_=Json::array(),history_=Json::array();
};
static int Libraries(dl_phdr_info* info,std::size_t,void* data) {
  if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);
  return 0;
}
int main(int argc,char** argv) {
  try {
    Require(argc==5,"usage: moe-rdma-transaction-session TOPOLOGY RDMA INITIAL_V4 NEW_RESULT");
    std::ifstream prior(argv[4]);Require(!prior.good(),"session result already exists");
    ns3::Time::SetResolution(ns3::Time::NS);
    const auto topology=ReadJson(argv[1]),protocol=ReadJson(argv[2]),initial=ReadJson(argv[3]);
    const auto max_ns=protocol.contains("limits")?protocol.at("limits").at("max_sim_ns").get<int64_t>():5000000;
    ValidateSessionProgram(initial,topology,max_ns);
    Require(initial.at("operations").empty(),"session initial operations must be empty");
    ns3::RngSeedManager::SetSeed(protocol.at("seed"));
    Fabric fabric(topology,protocol);TransactionSession session(fabric,topology,initial);
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));std::string error;
    try {ns3::Simulator::Run();}catch(const std::exception& e){error=e.what();}
    auto result=fabric.Report();result["transactions"]=session.Transactions();result["session"]=session.Report();
    result["completed_operations"]=session.CompletedRemote();result["completed_local_work"]=session.CompletedLocal();
    result["local_model"]=initial.at("local_model");result["local_work"]=session.LocalWork();
    result["pending_operations"]=session.Pending(false);result["pending_local_work"]=session.Pending(true);
    result["loaded_libraries"]=Json::array();dl_iterate_phdr(Libraries,&result["loaded_libraries"]);
    const bool passed=error.empty() && session.Finished() && fabric.Drained();result["passed"]=passed;
    if(!error.empty())result["execution_error"]=error;
    std::ofstream out(argv[4]);out<<result.dump()<<'\n';out.close();Require(bool(out),"cannot write session result");
    ns3::Simulator::Destroy();Require(passed,"incomplete transaction session; inspect report");return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
