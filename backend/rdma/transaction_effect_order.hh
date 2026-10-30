#pragma once
#include "remote_transactions.hh"

namespace moe_rdma {
// Admission-only functional ordering proof. Source preparation is not remote
// application. A completion of a causally later operation on the SAME carrier
// does cover its prepared predecessors; array position alone never proves this.
inline void ValidateTransactionEffectOrder(const Json& input) {
  struct Node {bool local;std::set<uint64_t> ancestors,before,applied;};
  struct Access {uint64_t id,connection,owner,key,address,bytes;bool atomic;};
  std::map<uint64_t,Node> nodes;
  std::map<uint64_t,uint64_t> destinations;
  for(const auto& c:input.at("connections"))destinations.emplace(c.at("id"),c.at("dst"));
  std::vector<Access> writes;
  const auto& operations=input.at("operations");
  Require(operations.is_array() && operations.size()<=ReadTransactionLimits(input).Nodes(),"transaction order node budget exceeded");
  for(const auto& op:operations) {
    const auto id=op.at("id").get<uint64_t>();
    Require(!nodes.count(id),"repeated transaction order id");
    Node node{op.value("kind",std::string("remote"))=="local",{},{},{}};
    for(const auto& d:op.at("after")) {
      const auto p=d.at("operation").get<uint64_t>();
      Require(nodes.count(p),"transaction order dependency must name earlier node");
      const auto& parent=nodes.at(p);
      node.ancestors.insert(parent.ancestors.begin(),parent.ancestors.end());node.ancestors.insert(p);
      node.before.insert(parent.before.begin(),parent.before.end());
      if(!parent.local && d.at("event")!="prepared")
        node.before.insert(parent.applied.begin(),parent.applied.end());
    }
    if(!node.local) {
      const auto connection=op.at("connection").get<uint64_t>();
      Require(destinations.count(connection),"unknown transaction order connection");
      const bool atomic=op.at("verb")=="fetch_add" || op.at("verb")=="fetch_add32";
      const uint64_t bytes=atomic?(op.at("verb")=="fetch_add32"?4:8):op.contains("source")?op.at("source").at("bytes").get<uint64_t>():
        MemoryUnhex(op.at("hex").get<std::string>()).size();
      Access current{id,connection,destinations.at(connection),op.at("key"),op.at("address"),bytes,atomic};
      Require(bytes>0 && current.address<=UINT64_MAX-bytes,"invalid transaction order range");
      node.applied=node.before;node.applied.insert(id);
      for(const auto& prior:writes) {
        const bool same_carrier=prior.connection==connection && node.ancestors.count(prior.id);
        if(prior.owner==current.owner &&
           current.address<prior.address+prior.bytes && prior.address<current.address+current.bytes)
          Require((atomic && prior.atomic) || node.before.count(prior.id) || same_carrier,
            "overlapping non-atomic memory writes require remote effect order; prepared does not order writes across connections");
        if(same_carrier) {
          const auto& covered=nodes.at(prior.id).applied;
          node.applied.insert(covered.begin(),covered.end());
        }
      }
      writes.push_back(current);
    }
    nodes.emplace(id,std::move(node));
  }
}
}
