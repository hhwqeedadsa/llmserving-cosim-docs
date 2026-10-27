#include "transaction_session_validation.hh"
#include "session_wakeups.hh"
#include "session_callback_batches.hh"
#include "host_timing.hh"
#include <deque>
#include <fstream>
#include <iostream>
#include <link.h>

using namespace moe_rdma;
class TransactionSession {
 public:
  TransactionSession(Fabric& fabric,const Json& topology,const Json& input,HostTiming& timing,bool batches=false):fabric_(fabric),topology_(topology),input_(input),transactions_(fabric,input),batch_mode_(batches),timing_(timing) {
    Require(input.at("operations").empty(),"session initial operations must be empty");
    transactions_.changed=[this](){Notify();};Notify();
  }
  bool Finished() const {return finished_;}
  Json Report() const {
    Json r={{"decisions",decision_},{"finished",finished_},{"registrations",registrations_},{"events",history_},
      {"scope","interactive MRT1 functional transactions on one native clock; frontend host wait is not simulated time"},
      {"modern_deepep_connected",false},{"hardware_calibrated",false},{"gpu_visibility_modeled",false}};
    if(filtered_commands_)r["callback_wakeups"]={{"mode","next_event/1"},{"filtered_commands",filtered_commands_},
      {"suppressed_event_batches",suppressed_},{"retained_callbacks",history_.size()},{"pending_callbacks",pending_events_.size()}};
    if(batch_mode_)r["callback_batches"]=batches_.Report();
    if(!alarms_.empty()) {
      r["control_alarms"]=Json::array();
      for(const auto& [id,a]:alarms_)r["control_alarms"].push_back({{"id",id},{"armed_ns",a.armed},
        {"deadline_ns",a.deadline},{"fired_ns",a.fired},{"cancelled_ns",a.cancelled}});
      r["control_alarm_scope"]="frontend control wakeups on native ns-3 clock; no GPU slot, network packet or calibrated detection cost";
    }
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
      if(op.contains("copy") && op.at("copy").at("target").contains("owner"))
        rows.back()["duration_source"]="topology_intra_host_crossbar/1";
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
  struct Alarm {
    Alarm(int64_t start,int64_t end):armed(start),deadline(end) {}
    int64_t armed,deadline,fired=-1,cancelled=-1;
    bool delivered=false;
    ns3::EventId event;
    bool Pending() const {return fired<0 && cancelled<0;}
  };
  std::size_t PendingAlarms() const {
    std::size_t count=0;for(const auto& [id,a]:alarms_)count+=a.Pending();return count;
  }
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
    local_times_.at(id)[1]=Now();
    transactions_.ExecuteLocal(Node(id),[this,rank,id](){
      Require(local_busy_.erase(rank)==1,"duplicate session local completion");
      local_times_.at(id)[2]=Now();PumpLocal(rank);Notify();
    });
  }
  Json Events() {
    Json events=Json::array();
    Json progress;
    {auto phase=timing_.Span("operation_progress_snapshot");progress=transactions_.TakeOperationProgressUpdates();}
    for(const auto& op:progress) {
      const auto id=op.at("id").get<uint64_t>();
      for(const auto& pair:std::vector<std::pair<std::string,std::string>>{{"submitted","ready_ns"},{"descriptor_ready","descriptor_ready_ns"},{"prepared","prepared_ns"},{"published","published_ns"},{"applied","applied_ns"},
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
    for(auto& [id,a]:alarms_)if(a.fired>=0 && !a.delivered) {
      events.push_back({{"alarm",id},{"event","fired"},{"time_ns",a.fired}});a.delivered=true;
    }
    // Timestamp order is descriptive; ties retain actual collection order and
    // are not a claim that independent same-ns device callbacks are ordered.
    std::stable_sort(events.begin(),events.end(),[](const Json& a,const Json& b){return a.at("time_ns")<b.at("time_ns");});
    for(const auto& e:events)history_.push_back(e);
    return events;
  }
  void Exchange(Json events,bool drained) {
    auto exchange=timing_.Span("session_exchange");
    Require(decision_<256,"session decision budget exceeded");
    {
    auto phase=timing_.Span("session_decide_write");
    Json message={{"type","DECIDE"},{"decision_id",decision_},{"now_ns",Now()},
      {"events",events},{"drained",drained}};
    if(batch_mode_)message["batches"]=batches_.Rows();
    const auto line=message.dump();Require(line.size()+14<=1048576,"session callback line budget exceeded");
    std::cout<<"MOE_TX_EVENT "<<line<<std::endl;
    }
    std::vector<char> buffer(1048577);
    {
    auto phase=timing_.Span("session_frontend_wait");
    Require(bool(std::cin.getline(buffer.data(),buffer.size())),"frontend EOF or oversized session command");
    }
    const auto command=Json::parse(buffer.data());
    std::vector<std::string> keys={"type","decision_id","now_ns","submissions","finish"};
    for(const auto* name:{"alarms","cancel_alarms","wake_on","wake_groups","prepare_operations","publish_operations"})if(command.contains(name))keys.push_back(name);
    SessionKeys(command,keys);
    Require(command.at("type")=="COMMIT" && SessionInteger(command.at("decision_id"))==decision_ &&
      SessionInteger(command.at("now_ns"))==static_cast<uint64_t>(Now()) && command.at("finish").is_boolean(),"session command identity/time/type mismatch");
    const auto& submissions=command.at("submissions");
    Require(submissions.is_array() && submissions.size()<=ReadTransactionLimits(input_).Nodes(),"invalid session submissions");
    // Pure validation of the entire candidate precedes registering/executing
    // any command member. Runtime lease/access failures are not rolled back.
    Json candidate;
    {auto phase=timing_.Span("session_candidate_copy");candidate=input_;
      for(const auto& op:submissions)candidate["operations"].push_back(op);}
    {auto phase=timing_.Span("session_candidate_validate");ValidateSessionProgram(candidate,topology_,fabric_.MaxSimNs());}
    const auto prepare=command.value("prepare_operations",Json::array()),publish=command.value("publish_operations",Json::array());
    if(command.contains("prepare_operations") || command.contains("publish_operations"))
      Require(input_.value("submission_model",Json())==Json{{"mode","staged_prefix/1"}},"provider stages require staged_prefix/1");
    transactions_.ValidateStages(prepare,publish);
    for(const auto& id:prepare)Require(delivered_.count({id.get<uint64_t>(),"submitted"}),"prepare requires delivered submitted callback");
    for(const auto& id:publish)Require(delivered_.count({id.get<uint64_t>(),"prepared"}),"publish requires delivered prepared callback");
    const auto next_filter=[&](){auto phase=timing_.Span("session_wakeup_validate");
      return SessionWakeups::Parse(command,candidate,delivered_,ReadTransactionLimits(candidate).Nodes());}();
    Require(!command.contains("wake_groups") || batch_mode_,"wake_groups requires callback batch mode");
    Require(!batch_mode_ || !command.contains("wake_on"),"wake_on incompatible with callback batch mode");
    const auto next_groups=SessionWakeups::ParseGroups(command,candidate,delivered_,ReadTransactionLimits(candidate).Nodes());
    std::vector<std::pair<uint64_t,int64_t>> arm;
    std::vector<uint64_t> cancel;
    std::set<uint64_t> new_ids,cancel_ids;
    if(command.contains("alarms")) {
      const auto& list=command.at("alarms");
      Require(list.is_array() && alarms_.size()+list.size()<=32,"control alarm lifetime budget exceeded");
      for(const auto& item:list) {
        SessionKeys(item,{"id","delay_ns"});const auto id=SessionInteger(item.at("id"),65535);
        const auto delay=SessionInteger(item.at("delay_ns"),fabric_.MaxSimNs());
        Require(!alarms_.count(id) && new_ids.insert(id).second,"reused control alarm id");
        Require(delay<static_cast<uint64_t>(fabric_.MaxSimNs()-Now()),"control alarm deadline reaches simulation cutoff");
        arm.emplace_back(id,delay);
      }
    }
    if(command.contains("cancel_alarms")) {
      const auto& list=command.at("cancel_alarms");Require(list.is_array() && list.size()<=32,"invalid control alarm cancellations");
      for(const auto& item:list) {
        const auto id=SessionInteger(item,65535);
        Require(alarms_.count(id) && alarms_.at(id).Pending() && cancel_ids.insert(id).second,"control alarm cancellation requires unique pending id");
        cancel.push_back(id);
      }
    }
    const auto pending=PendingAlarms()-cancel.size()+arm.size();
    const bool finish=command.at("finish").get<bool>();
    Require(!finish || (submissions.empty() && (!input_.at("operations").empty() || !alarms_.empty()) &&
      drained && pending==0 && arm.empty() && prepare.empty() && publish.empty()),"premature session finish or pending control alarm");
    Require(finish || !submissions.empty() || !drained || pending>0,"empty idle session command cannot advance execution");
    // Nothing above mutates the live network, operation table or alarm state.
    auto apply=timing_.Span("session_apply_commit");
    ++decision_;
    wake_filter_=next_filter;if(command.contains("wake_on"))++filtered_commands_;
    wake_groups_=next_groups;
    for(const auto id:cancel) {
      auto& a=alarms_.at(id);ns3::Simulator::Cancel(a.event);a.cancelled=Now();
    }
    for(const auto& [id,delay]:arm) {
      auto [it,added]=alarms_.emplace(id,Alarm{Now(),Now()+delay});
      Require(added,"duplicate control alarm registration");
      it->second.event=ns3::Simulator::Schedule(ns3::NanoSeconds(delay),[this,id](){
        auto& a=alarms_.at(id);Require(a.Pending(),"nonpending control alarm fired");a.fired=Now();Notify();
      });
    }
    if(finish) {transactions_.Close();Require(fabric_.Drained(),"session resources remain after close");finished_=true;ns3::Simulator::Stop();return;}
    for(const auto& op:submissions) {
      const auto id=op.at("id").get<uint64_t>();
      index_[id]=input_.at("operations").size();input_["operations"].push_back(op);
      registrations_.push_back({{"operation",id},{"time_ns",Now()},{"decision_id",decision_-1}});
      if(op.at("kind")=="local")local_times_[id]={-1,-1,-1};
    }
    transactions_.ApplyStages(prepare,publish);
    if(!submissions.empty() || !arm.empty() || !cancel.empty())Notify();
  }
  void Progress() {
    auto progress=timing_.Span("session_progress");Snapshot("session_progress_enter");
    {
    auto phase=timing_.Span("session_pump");
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
    }
    Json events;
    {auto phase=timing_.Span("session_events");events=Events();}
    const bool drained=[&](){auto phase=timing_.Span("session_drained");return Drained();}();
    for(const auto& event:events)pending_events_.push_back(event);
    const bool boundary=decision_==0 || !events.empty() || (drained && !last_drained_);
    const bool capacity=batch_mode_ && boundary && batches_.Add(Now(),events,drained);
    const bool trigger=batch_mode_?SessionWakeups::TriggeredGroups(wake_groups_,delivered_,events):SessionWakeups::Triggered(wake_filter_,events);
    if(decision_==0 || trigger || (drained && !last_drained_) || capacity) {
      Exchange(pending_events_,drained);pending_events_.clear();
      if(batch_mode_)batches_.Clear();
    } else if(!events.empty())++suppressed_;
    last_drained_=drained;
    if(!finished_ && !notify_pending_ && !AllDone() && transactions_.Drained() && local_busy_.empty() && PendingAlarms()==0)
      throw std::invalid_argument("session pending work has no event able to satisfy its conditions");
    Snapshot("session_progress_exit");
  }
  void Snapshot(const char* name) {
    if(timing_.Enabled())timing_.Snapshot(name,{{"now_ns",Now()},{"processed_events",ns3::Simulator::GetEventCount()},
      {"decisions",decision_},{"registered_nodes",input_.at("operations").size()},{"submitted_nodes",submitted_.size()},
      {"local_busy_ranks",local_busy_.size()},{"collected_callbacks",history_.size()},
      {"pending_callbacks",pending_events_.size()},{"finished",finished_}});
  }
  Fabric& fabric_;Json topology_,input_;RemoteTransactions transactions_;
  uint64_t decision_=0;bool notify_pending_=false,finished_=false,last_drained_=false;
  std::map<uint64_t,std::size_t> index_;std::set<uint64_t> submitted_;
  std::map<uint64_t,std::array<int64_t,3>> local_times_;
  std::map<uint64_t,Alarm> alarms_;
  std::map<unsigned,std::deque<uint64_t>> local_queues_;std::set<unsigned> local_busy_;
  std::set<std::pair<uint64_t,std::string>> delivered_;
  SessionWakeups::Filter wake_filter_;
  SessionWakeups::Groups wake_groups_;
  bool batch_mode_;SessionCallbackBatches batches_;
  uint64_t filtered_commands_=0,suppressed_=0;
  Json pending_events_=Json::array();
  Json registrations_=Json::array(),history_=Json::array();
  HostTiming& timing_;
};
static int Libraries(dl_phdr_info* info,std::size_t,void* data) {
  if(info->dlpi_name && info->dlpi_name[0])static_cast<Json*>(data)->push_back(info->dlpi_name);
  return 0;
}
static void Execute(char** argv,HostTiming& timing,bool batches) {
    auto total=timing.Span("native_total");
    std::ifstream prior(argv[4]);Require(!prior.good(),"session result already exists");
    ns3::Time::SetResolution(ns3::Time::NS);
    const auto topology=ReadJson(argv[1]),protocol=ReadJson(argv[2]),initial=ReadJson(argv[3]);
    const auto max_ns=protocol.contains("limits")?protocol.at("limits").at("max_sim_ns").get<int64_t>():5000000;
    ValidateSessionProgram(initial,topology,max_ns);
    Require(initial.at("operations").empty(),"session initial operations must be empty");
    ns3::RngSeedManager::SetSeed(protocol.at("seed"));
    PersistentStreams::ValidateReordering(initial.value("transport_recovery",Json()),protocol.contains("limits")?protocol.at("limits").at("max_sim_ns").get<int64_t>():5000000);
    Fabric fabric(topology,protocol);TransactionSession session(fabric,topology,initial,timing,batches);
    ns3::Simulator::Stop(ns3::NanoSeconds(fabric.MaxSimNs()));std::string error;
    try {auto phase=timing.Span("simulator_run");ns3::Simulator::Run();}catch(const std::exception& e){error=e.what();}
    Json result;
    {
    auto phase=timing.Span("report_build");
    result=fabric.Report();result["transactions"]=session.Transactions();result["session"]=session.Report();
    result["completed_operations"]=session.CompletedRemote();result["completed_local_work"]=session.CompletedLocal();
    result["local_model"]=initial.at("local_model");result["local_work"]=session.LocalWork();
    result["pending_operations"]=session.Pending(false);result["pending_local_work"]=session.Pending(true);
    result["loaded_libraries"]=Json::array();dl_iterate_phdr(Libraries,&result["loaded_libraries"]);
    }
    const bool passed=error.empty() && session.Finished() && fabric.Drained();result["passed"]=passed;
    if(!error.empty())result["execution_error"]=error;
    {auto phase=timing.Span("report_write");std::ofstream out(argv[4]);out<<result.dump()<<'\n';out.close();Require(bool(out),"cannot write session result");}
    {auto phase=timing.Span("simulator_cleanup");ns3::Simulator::Destroy();}
    Require(passed,"incomplete transaction session; inspect report");
}
int main(int argc,char** argv) {
  try {
    Require(argc>=5 && argc<=7,"usage: moe-rdma-transaction-session TOPOLOGY RDMA INITIAL_V4 NEW_RESULT [NEW_HOST_TIMING.jsonl] [--callback-batches]");
    bool batches=false;std::string timing_path;
    for(int i=5;i<argc;++i) {
      if(std::string(argv[i])=="--callback-batches") {Require(!batches,"duplicate callback batch flag");batches=true;}
      else {Require(timing_path.empty(),"multiple host timing paths");timing_path=argv[i];}
    }
    HostTiming timing(timing_path);Execute(argv,timing,batches);timing.Complete();return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
