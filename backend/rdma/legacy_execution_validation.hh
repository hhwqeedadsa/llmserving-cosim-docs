#pragma once
#include "fabric.hh"
#include "legacy_posting_admission.hh"
#include <set>
namespace moe_rdma {
inline void ValidateLegacyExecution(const Json& input,const Json& topology,const Json& protocol,unsigned reference_cap=100000) {
  auto integer=[](const Json& value,uint64_t low,uint64_t high) {
    Require(value.is_number_integer() && !value.is_boolean() && value>=low && value<=high,"legacy integer outside supported range");return value.get<uint64_t>();
  };
  Require(input.at("schema_version")=="moe-legacy-native-execution/1","unsupported legacy execution schema");
  const auto ranks=integer(input.at("ranks"),2,32),experts=integer(input.at("experts"),2,256);
  Require(experts%ranks==0,"legacy expert distribution mismatch");
  const auto hidden=integer(input.at("hidden_size"),128,8192);
  Require(hidden%128==0 && input.at("dispatch_bytes")==2*hidden+16,"legacy native supports explicit BF16 layout only");
  const auto tokens=integer(input.at("max_tokens_per_rank"),1,16);
  const auto depth=integer(input.at("queue_depth"),1,4096);Require(!(depth&(depth-1)),"legacy queue depth not power of two");
  const auto& mapping=input.at("rank_to_gpu");Require(mapping.is_array() && mapping.size()==ranks,"legacy rank mapping size");
  std::set<uint64_t> gpus,used;
  for(const auto& n:topology.at("nodes"))if(n.at("kind")=="gpu")gpus.insert(n.at("id").get<uint64_t>());
  Require(gpus.size()==128 || gpus.size()==256 || gpus.size()==512,"legacy native requires128/256/512 endpoints");
  for(const auto& gpu:mapping) {const auto n=integer(gpu,0,511);Require(gpus.count(n) && used.insert(n).second,"legacy mapping absent or repeated GPU");}
  const auto& operations=input.at("operations");const auto& events=input.at("events");
  Require(operations.is_array() && !operations.empty() && operations.size()<=32768 && events.is_array() &&
    !events.empty() && events.size()<=140000,"legacy execution lifetime budget exceeded");
  const auto& limits=input.at("limits");
  Require(limits.at("streams")==2*(ranks-1)*experts,"legacy context count mismatch");
  integer(limits.at("work"),1,65536);integer(limits.at("link_transmissions"),1,2000000);
  std::set<std::string> identities;std::set<unsigned> stages;
  const auto mtu=integer(protocol.at("payload_bytes"),1,65536);
  uint64_t minimum_reference_events=0;unsigned remote_work=0;
  LegacyPostingAdmission posting;
  for(unsigned i=0;i<operations.size();++i) {
    const auto& op=operations.at(i);Require(op.is_array() && op.size()==12,"legacy operation columns mismatch");
    Require(op.at(0).is_string() && op.at(0).get<std::string>().size()<=256 && identities.insert(op.at(0).get<std::string>()).second,"legacy operation id mismatch");
    integer(op.at(1),0,3);integer(op.at(2),0,1);
    Require(op.at(3)=="data" || op.at(3)=="count" || op.at(3)=="flag","legacy operation kind");
    const auto s=integer(op.at(4),0,ranks-1),d=integer(op.at(5),0,ranks-1);
    const auto expert=integer(op.at(6),0,experts-1);if(!op.at(7).is_null())integer(op.at(7),0,tokens-1);
    integer(op.at(8),1,32768);Require(op.at(9).is_number_integer(),"legacy value is not integer");
    const auto message_index=integer(op.at(11),0,tokens-1);
    posting.Observe((s*ranks+d)*(experts/ranks)+expert%(experts/ranks),s,d,op.at(3)!="data",message_index);
    if(op.at(3)=="data")Require(op.at(8)==(op.at(2)==0?2*hidden+16:2*hidden),"legacy data layout mismatch");
    else Require(op.at(8)==4 && (op.at(3)=="count"?(op.at(9)<=-1 && op.at(9)>=-17):op.at(9)==1),"legacy notification operand mismatch");
    if(s!=d) {
      remote_work+=op.at(3)=="data"?1:2;
      minimum_reference_events+=(32+op.at(8).get<uint64_t>()+mtu-1)/mtu;
      if(op.at(3)!="data")minimum_reference_events+=(36+mtu-1)/mtu;
    }
    const auto& refs=op.at(10);Require(refs.is_array() && refs.size()==4,"legacy operation stage count");
    for(unsigned j=0;j<4;++j) {
      const auto ref=integer(refs.at(j),0,events.size()-1);Require(stages.insert(ref).second,"legacy stage reference reused");
      Require(events.at(ref).at(3)==i,"legacy stage operation mismatch");
      Require(events.at(ref).at(0)==std::vector<std::string>{"issued","accepted","visible","sender_done"}.at(j),"legacy stage kind mismatch");
    }
  }
  Require(remote_work==limits.at("work"),"legacy carrier work budget mismatch");
  Require(posting.AllPublished(),"legacy plan leaves an unpublished posting tail");
  Require(depth>=posting.MinimumDepth(),"legacy queue depth cannot hold next publication span: need at least "+
    std::to_string(posting.MinimumDepth())+"; ACK cannot free unpublished work; rejected before Fabric");
  const auto configured=integer(input.at("dcqcn_reference").at("max_events"),1,reference_cap);
  Require(configured>=minimum_reference_events,
    "legacy reference max_events below mandatory source sends: need at least "+std::to_string(minimum_reference_events)+
    "; retries and feedback require more; rejected before Fabric");
  const std::set<std::string> kinds={"issued","accepted","visible","sender_done","kernel_begin","clean_next_buffer",
    "send_phase_issued","dispatch_packed","combine_merged","kernel_complete","expert_stage_done"};
  for(unsigned i=0;i<events.size();++i) {
    const auto& e=events.at(i);Require(e.is_array() && e.size()==5 && kinds.count(e.at(0)),"legacy event columns/kind mismatch");
    integer(e.at(1),0,ranks-1);Require(e.at(2).is_array() && e.at(2).size()<=8192,"legacy dependency count");
    std::set<uint64_t> parents;for(const auto& p:e.at(2))Require(i && parents.insert(integer(p,0,i-1)).second,"legacy invalid dependency");
    if(!e.at(3).is_null()) {integer(e.at(3),0,operations.size()-1);Require(stages.count(i),"unreferenced legacy operation stage");}
    else Require(!stages.count(i),"missing legacy operation reference");
  }
}
}
