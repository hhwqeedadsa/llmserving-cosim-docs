#pragma once
#include "json/json.hpp"
#include <map>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>

namespace moe_rdma {
// Pure command admission and predicate. No simulator event, packet or clock.
struct SessionWakeups {
  using Key=std::pair<uint64_t,std::string>;
  using Filter=std::optional<std::set<Key>>;
  using Value=nlohmann::json;
  static void Check(bool ok,const char* reason) {
    if(!ok)throw std::invalid_argument(reason);
  }
  static Filter Parse(const Value& command,const Value& candidate,
                      const std::set<Key>& delivered,unsigned maximum_nodes) {
    if(!command.contains("wake_on"))return std::nullopt;
    const auto& rows=command.at("wake_on");
    Check(rows.is_array() && rows.size()<=7u*maximum_nodes,"invalid session wakeup reference count");
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
};
}
