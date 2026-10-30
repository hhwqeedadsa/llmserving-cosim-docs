#pragma once
#include "service_transport.hh"
#include "execution_events.hh"
#include "shared_compute.hh"

namespace moe_rdma {
class InflightHelpers {
 public:
  InflightHelpers(ServiceTransport& transport,SharedCompute& compute,ControlChannel& control,ExecutionEvents& events,const Json& option):
    transport_(transport),compute_(compute),control_(control),events_(events),
    option_(option.is_null()?Json():option.at("configuration")),deployment_(option) {}
  bool Enabled() const {return !option_.is_null();}
  void Register(const Json& wave) {
    Require(wave.contains("helper_candidates"),"helper mode requires declared candidates");
    const auto& group=deployment_.at("groups").at(wave.at("group_index").get<unsigned>());
    Require(group==wave.at("rank_ids"),"helper replica deployment differs from actual EP group");
    for(const auto& row:wave.at("helper_candidates")) {
      Require(row.is_object() && row.size()==19,"invalid helper candidate fields");
      for(const char* key:{"logical_layer","source","owner","provider","pack_node","dispatch_message","combine_message","plane",
                           "input_message","result_message","input_bytes","result_bytes","compute_ns","reduce_ns"})
        Require(row.at(key).is_number_integer() && row.at(key)>=0,"invalid helper integer declaration");
      Require(row.at("plane")<16 && row.at("compute_ns")<=1000000000 && row.at("reduce_ns")<=1000000000,
        "helper plane/compute out of range");
      const auto id=row.at("id").get<std::string>();
      Require(catalog_.size()<32768 && !catalog_.count(id) && row.at("graph_id")==wave.at("graph_id"),"helper candidate identity/budget mismatch");
      const auto source=row.at("source").get<int>(),owner=row.at("owner").get<int>(),provider=row.at("provider").get<int>();
      Require(source!=provider && source!=owner && owner!=provider,"helper must have three distinct endpoints");
      bool found=false;
      for(const auto& m:wave.at("messages"))if(m.at("id")==row.at("dispatch_message")) {
        Require(m.at("src")==source && m.at("dst")==owner && m.at("phase")=="dispatch" &&
          m.at("layer")==row.at("logical_layer") && m.at("plane")==row.at("plane"),"helper original Dispatch differs");found=true;
      }
      Require(found,"helper has no original Dispatch");
      const auto layer=row.at("logical_layer").get<unsigned>();
      const auto experts=row.at("experts").get<std::vector<unsigned>>();
      Require(!experts.empty() && experts.size()<=8 && std::is_sorted(experts.begin(),experts.end()) &&
        std::set<unsigned>(experts.begin(),experts.end()).size()==experts.size(),"invalid helper expert bundle");
      auto owner_of=[this,layer](const Json& ranks,unsigned expert) {
        const auto count=deployment_.at("experts").get<unsigned>();
        Require(expert<count && !ranks.empty() && count%ranks.size()==0,"invalid helper expert deployment");
        const auto& placement=deployment_.at("expert_to_group_rank");
        unsigned index=placement.is_null()?expert/(count/ranks.size()):placement.at(layer).at(expert).get<unsigned>();
        Require(index<ranks.size(),"helper expert index outside group");return ranks.at(index).get<int>();
      };
      const Json* replica=nullptr;
      for(const auto& ranks:deployment_.at("groups"))for(const auto& rank:ranks)if(rank==provider) {
        Require(replica==nullptr && ranks!=group,"ambiguous or same-group helper replica");replica=&ranks;
      }
      Require(replica!=nullptr,"undeclared helper replica rank");
      for(auto expert:experts)Require(owner_of(group,expert)==owner && owner_of(*replica,expert)==provider,
        "helper replica lacks declared original expert bundle");
      bool token_found=false;
      for(const auto& token:wave.at("routes").at(layer).at("tokens")) {
        const auto call=wave.at("graph_id").get<std::string>()+".l"+std::to_string(layer)+".r"+
          std::to_string(token.at("rank").get<int>())+".s"+std::to_string(token.at("slot").get<int>());
        if(call!=row.at("call"))continue;
        Require(token.at("rank")==source,"helper request source differs");
        const auto mid=(uint64_t(1)<<40)+2*(row.at("dispatch_message").get<uint64_t>()*16+token.at("slot").get<unsigned>());
        Require(row.at("input_message")==mid && row.at("result_message")==mid+1 &&
          row.at("id")==call+".r"+std::to_string(owner)+".h"+std::to_string(provider),"helper message/attempt identity differs");
        std::vector<unsigned> expected;
        for(auto expert:token.at("experts").get<std::vector<unsigned>>())if(owner_of(group,expert)==owner)expected.push_back(expert);
        std::sort(expected.begin(),expected.end());Require(expected==experts,"helper bundle omits or adds expert contributions");token_found=true;
      }
      Require(token_found && row.at("bundle")=="r"+std::to_string(owner),"helper call or bundle identity differs");
      Require(row.at("compute_ns").get<uint64_t>()==experts.size()*deployment_.at("expert_ns_per_contribution").get<uint64_t>() &&
        row.at("reduce_ns").get<uint64_t>()==(experts.size()-1)*deployment_.at("local_reduce_ns_per_extra").get<uint64_t>() &&
        row.at("input_bytes")==deployment_.at("payload_sizes").at("dispatch") &&
        row.at("result_bytes")==deployment_.at("payload_sizes").at("combine"),"helper byte/compute declaration differs");
      bool pack=false,combine=false;
      for(const auto& node:wave.at("rank_nodes").at(std::to_string(source)))if(node.at("id")==row.at("pack_node"))
        pack=node.at("name")=="L"+std::to_string(layer)+".pack" && node.at("kind")=="COMP";
      for(const auto& message:wave.at("messages"))if(message.at("id")==row.at("combine_message"))
        combine=message.at("src")==owner && message.at("dst")==source && message.at("phase")=="combine" && message.at("layer")==layer;
      Require(pack && combine,"helper input producer or original Combine differs");
      Require(row.at("input_bytes").get<uint64_t>()>0 && row.at("result_bytes").get<uint64_t>()>0 &&
        row.at("input_bytes").get<uint64_t>()<=1048576 && row.at("result_bytes").get<uint64_t>()<=1048576,
        "helper byte size out of range");
      for(const char* key:{"input_message","result_message"})
        Require(message_ids_.insert(row.at(key).get<uint64_t>()).second,"helper semantic message identity reused");
      catalog_.emplace(id,row);
    }
  }
  void NodeComplete(const std::string& graph,int rank,uint64_t node) {completed_nodes_.insert({graph,rank,node});}
  void MessageComplete(uint64_t message) {received_.insert(message);}
  void Decide(const Json& notice) {
    const auto now=Now();
    Require(decisions_<option_.at("max_decisions").get<uint64_t>(),"helper decision budget exceeded");
    Json request={{"type","HELPER_DECIDE"},{"decision_id",decisions_},{"now_ns",now},
      {"notice_sequence",notice.at("sequence")},{"message",notice.at("message")},{"observed_events",events_.Count()}};
    control_.Send(request.dump());std::string line;
    Require(bool(std::getline(std::cin,line)) && line.size()<=1048576,"helper frontend EOF or oversized reply");
    const auto reply=Json::parse(line);
    Require(reply.is_object() && reply.size()==4 && reply.at("type")=="HELPER_COMMIT" &&
      reply.at("decision_id").is_number_integer() && reply.at("now_ns").is_number_integer() &&
      reply.at("decision_id")==decisions_ && reply.at("now_ns")==now && reply.at("attempts").is_array(),
      "helper commit identity/time/fields mismatch");
    const auto& ids=reply.at("attempts");
    Require(option_.at("policy")!="observe_only/1" || ids.empty(),"observe-only helper policy cannot submit work");
    Require(ids.size()<=1 && attempts_.size()+ids.size()<=option_.at("max_attempts").get<uint64_t>(),"helper attempt budget exceeded");
    // Validate the complete batch before registering work or changing state.
    for(const auto& candidate:ids) {
      const auto id=candidate.get<std::string>();
      Require(catalog_.count(id) && !attempts_.count(id),"unknown or reused helper candidate");
      const auto& row=catalog_.at(id);
      Require(row.at("dispatch_message")==notice.at("message") && !received_.count(row.at("combine_message").get<uint64_t>()),
        "helper does not match timed out message or result already delivered");
      Require(completed_nodes_.count({row.at("graph_id").get<std::string>(),row.at("source").get<int>(),row.at("pack_node").get<uint64_t>()}),
        "helper source input not produced");
    }
    ++decisions_;
    for(const auto& candidate:ids)Start(candidate.get<std::string>());
  }
  bool Complete() const {return retired_==attempts_.size();}
  std::function<void()> changed;
  Json Report() const {
    Json attempts=Json::array();for(const auto& [id,row]:attempts_)attempts.push_back({{"id",id},{"candidate",catalog_.at(id)},
      {"input_ack",row.input_ack},{"result_ack",row.result_ack},{"result_ready",row.result_ready},{"retired",row.retired}});
    return {{"schema_version","moe-inflight-helpers-report/1"},{"configuration",option_},{"decisions",decisions_},
      {"events",event_count_},{"attempts",attempts},{"retired",retired_},
      {"scope","actual extra input/compute/return on shared GPU/NIC; original graph gates and transport unchanged; no application recovery claim"}};
  }
 private:
  struct Attempt {bool input_ack=false,result_ack=false,result_ready=false,retired=false;};
  static int64_t Now(){return ns3::Simulator::Now().GetNanoSeconds();}
  void Emit(const std::string& id,const char* kind) {
    Require(event_count_<option_.at("max_events").get<uint64_t>(),"helper event budget exceeded");
    control_.Send(Json({{"type","HELPER_OBSERVE"},{"schema_version","moe-helper-event/1"},{"sequence",event_count_++},
      {"observed_events",events_.Count()},{"time_ns",Now()},{"id",id},{"kind",kind}}).dump());
  }
  Json Message(const Json& row,bool result) {
    return {{"id",row.at(result?"result_message":"input_message")},{"src",row.at(result?"provider":"source")},
      {"dst",row.at(result?"source":"provider")},{"bytes",row.at(result?"result_bytes":"input_bytes")},
      {"plane",row.at("plane")},{"phase",result?"helper_result":"helper_input"},{"helper_id",row.at("id")}};
  }
  void Start(const std::string& id) {
    const auto& row=catalog_.at(id);
    transport_.Register(Json::array({Message(row,false),Message(row,true)}));
    attempts_.emplace(id,Attempt{});Emit(id,"accepted");Emit(id,"input_post");
    transport_.Send(row.at("input_message"),row.at("source"),row.at("provider"),row.at("input_bytes"),row.at("plane"),
      [this,id](){attempts_.at(id).input_ack=true;Emit(id,"input_ack");Retire(id);},
      [this,id](){Emit(id,"input_ready");Compute(id,false);});
  }
  void Compute(const std::string& id,bool reduce) {
    const auto& row=catalog_.at(id);const auto rank=row.at("provider").get<unsigned>();
    compute_.Submit(id+(reduce?".reduce":".experts"),rank,row.at(reduce?"reduce_ns":"compute_ns"),
      {{"kind",reduce?"helper_reduce":"helper_experts"},{"helper_id",id}},
      [this,id,reduce](){Emit(id,reduce?"reduce_begin":"compute_begin");},
      [this,id,reduce](){Emit(id,reduce?"reduce_done":"compute_done");if(reduce)Return(id);else Compute(id,true);});
  }
  void Return(const std::string& id) {
    const auto& row=catalog_.at(id);Emit(id,"result_post");
    transport_.Send(row.at("result_message"),row.at("provider"),row.at("source"),row.at("result_bytes"),row.at("plane"),
      [this,id](){attempts_.at(id).result_ack=true;Emit(id,"result_ack");Retire(id);},
      [this,id](){attempts_.at(id).result_ready=true;Emit(id,"result_ready");Retire(id);});
  }
  void Retire(const std::string& id) {
    auto& row=attempts_.at(id);
    if(!row.retired && row.input_ack && row.result_ack && row.result_ready) {
      const auto& candidate=catalog_.at(id);
      transport_.FinishMessageScopes({candidate.at("input_message").get<uint64_t>(),candidate.at("result_message").get<uint64_t>()});
      row.retired=true;++retired_;Emit(id,"retired");if(changed)changed();
    }
  }
  ServiceTransport& transport_;SharedCompute& compute_;ControlChannel& control_;ExecutionEvents& events_;
  Json option_,deployment_;uint64_t decisions_=0,event_count_=0,retired_=0;
  std::map<std::string,Json> catalog_;
  std::map<std::string,Attempt> attempts_;
  std::set<std::tuple<std::string,int,uint64_t>> completed_nodes_;
  std::set<uint64_t> received_;
  std::set<uint64_t> message_ids_;
};
}
