#pragma once
#include "json/json.hpp"
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace moe_rdma {
// Pure command admission and predicate. No simulator event, packet or clock.
struct SessionWakeups {
  using Key=std::pair<uint64_t,std::string>;
  using Filter=std::optional<std::set<Key>>;
  using Value=nlohmann::json;
  using Groups=std::optional<std::vector<std::set<Key>>>;
  static void Check(bool ok,const char* reason) {
    if(!ok)throw std::invalid_argument(reason);
  }
  static Filter Parse(const Value& command,const Value& candidate,
                      const std::set<Key>& delivered,unsigned maximum_nodes) {
    if(!command.contains("wake_on"))return std::nullopt;
    const auto& rows=command.at("wake_on");
    const unsigned stages=candidate.value("submission_model",Value())==Value{{"mode","staged_prefix/1"}}?8u:7u;
    Check(rows.is_array() && rows.size()<=stages*maximum_nodes,"invalid session wakeup reference count");
    std::map<uint64_t,const Value*> known;
    std::map<uint64_t,bool> self;
    for(const auto& op:candidate.at("operations"))known.emplace(op.at("id").get<uint64_t>(),&op);
    for(const auto& connection:candidate.at("connections"))self.emplace(connection.at("id").get<uint64_t>(),connection.contains("self_service"));
    std::set<Key> result;
    for(const auto& row:rows) {
      Check(row.is_object() && row.size()==2 && row.contains("operation") && row.contains("event"),"wrong session wakeup fields");
      const auto& id=row.at("operation");
      Check(id.is_number_integer() && id>=0 && id<=65535 && row.at("event").is_string(),"invalid session wakeup identity/type");
      const auto identity=id.get<uint64_t>();const auto event=row.at("event").get<std::string>();
      Check(known.count(identity),"session wakeup references unsubmitted work");
      const std::set<std::string> local={"local_admitted","local_started","completed"};
      std::set<std::string> remote={"submitted","published","applied","request_acked","response","completed"};
      if(candidate.contains("submission_model"))remote.insert("prepared");
      if(stages==8)remote.insert("descriptor_ready");
      const auto& op=*known.at(identity);
      const bool is_local=op.at("kind")=="local";
      Check((is_local?local:remote).count(event),"invalid session wakeup transition kind");
      if(!is_local && (event=="response" || event=="request_acked")) {
        Check(!self.at(op.at("connection").get<uint64_t>()),"self service has no wire ACK or response callback");
        Check(event!="response" || op.at("verb")=="fetch_add","put has no response callback");
      }
      const Key key{identity,event};
      Check(!delivered.count(key) && result.insert(key).second,"duplicate or already delivered session wakeup");
    }
    return result;
  }
  static bool Triggered(const Filter& filter,const Value& events) {
    if(!filter)return !events.empty();
    for(const auto& row:events) {
      if(row.contains("alarm"))return true;
      if(filter->count({row.at("operation").get<uint64_t>(),row.at("event").get<std::string>()}))return true;
    }
    return false;
  }
  static Groups ParseGroups(const Value& command,const Value& candidate,
                            const std::set<Key>& delivered,unsigned maximum_nodes) {
    Check(!(command.contains("wake_on") && command.contains("wake_groups")),"mutually exclusive session wakeup modes");
    if(!command.contains("wake_groups"))return std::nullopt;
    const auto& groups=command.at("wake_groups");
    Check(groups.is_array() && groups.size()<=maximum_nodes,"invalid session wakeup group count");
    std::vector<std::set<Key>> result;std::set<std::set<Key>> unique;std::size_t references=0;
    for(const auto& group:groups) {
      Check(group.is_array() && !group.empty(),"empty session wakeup conjunction");
      const unsigned stages=candidate.value("submission_model",Value())==Value{{"mode","staged_prefix/1"}}?8u:7u;
      references+=group.size();Check(references<=stages*maximum_nodes,"invalid session wakeup total reference count");
      const auto parsed=Parse(Value{{"wake_on",group}},candidate,delivered,maximum_nodes);
      Check(unique.insert(*parsed).second,"duplicate session wakeup group");result.push_back(*parsed);
    }
    return result;
  }
  static bool TriggeredGroups(const Groups& groups,const std::set<Key>& collected,const Value& events) {
    if(!groups)return !events.empty();
    for(const auto& e:events)if(e.contains("alarm"))return true;
    for(const auto& group:*groups) {
      bool all=true;for(const auto& key:group)all=all && collected.count(key);
      if(all)return true;
    }
    return false;
  }
};
}
