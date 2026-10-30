#pragma once
#include "json/json.hpp"
#include <algorithm>
#include <functional>
#include <map>
#include <queue>
#include <set>
#include <stdexcept>
#include <vector>

namespace moe_rdma {
struct RepairStep { uint16_t node, port; };
struct RepairPath { std::vector<RepairStep> steps; uint16_t merge; };
using RepairPlans=std::map<unsigned,std::map<uint32_t,RepairPath>>;

// Precomputed from the intact graph only. Neither future traffic nor runtime
// physical state is an input; the protected edge is removed for planning only.
inline RepairPlans BuildRepairPlans(const nlohmann::json& t,unsigned a,unsigned b) {
  struct Edge {unsigned peer,port;};
  const auto& nodes=t.at("nodes");unsigned gpus=0;
  for(const auto& n:nodes)if(n.at("kind")=="gpu")++gpus;
  auto check=[](bool v,const char* msg){if(!v)throw std::runtime_error(msg);};
  check((gpus==128 || gpus==256 || gpus==512) && nodes.size()<=gpus+256,"repair requires128/256/512 endpoints and <=256 switches");
  std::vector<std::vector<Edge>> adj(nodes.size());
  for(unsigned i=0;i<nodes.size();++i) {
    check(nodes.at(i).at("id")==i && (i<gpus)==(nodes.at(i).at("kind")=="gpu"),"repair node ordering");
    if(i>=gpus)check(nodes.at(i).at("plane")==0,"repair requires single plane0");
  }
  check(gpus<=a && a<b && b<nodes.size(),"repair protects a switch edge");
  bool found=false;
  for(const auto& e:t.at("links")) {
    unsigned x=e.at("src"),y=e.at("dst"),p=e.at("src_port"),q=e.at("dst_port");
    check(x<y && y<nodes.size() && p>0 && p<=1024 && q>0 && q<=1024,"repair physical edge bounds");
    const auto kx=nodes.at(x).at("kind").get<std::string>(),ky=nodes.at(y).at("kind").get<std::string>();
    check((kx=="gpu" && ky=="tor") || (kx=="tor" && (ky=="agg" || ky=="core")) ||
          (kx=="agg" && ky=="core"),"repair requires Clos tier edges");
    adj[x].push_back({y,p});adj[y].push_back({x,q});found=found || (x==a && y==b);
  }
  check(found,"repair protected edge absent");
  for(unsigned i=0;i<gpus;++i)check(adj[i].size()==1,"repair requires one NIC per GPU");
  auto bad=[=](unsigned x,unsigned y){return (x==a && y==b) || (x==b && y==a);};
  auto distances=[&](unsigned dst,bool failed) {
    std::vector<int>d(nodes.size(),-1);std::queue<unsigned>q;d[dst]=0;q.push(dst);
    while(!q.empty()) {
      auto x=q.front();q.pop();
      for(auto e:adj[x])if((!failed || !bad(x,e.peer)) && d[e.peer]<0) {
        d[e.peer]=d[x]+1;if(e.peer>=gpus)q.push(e.peer);
      }
    }
    return d;
  };
  RepairPlans result;
  for(auto source:{a,b})for(unsigned dst=0;dst<gpus;++dst) {
    const auto old=distances(dst,false);if(old[source]<0)continue;
    bool survives=false;
    for(auto e:adj[source])if(old[e.peer]==old[source]-1 && !bad(source,e.peer))survives=true;
    if(survives)continue;
    std::vector<int>memo(nodes.size(),-1);
    std::function<bool(unsigned)> safe=[&](unsigned x) {
      if(x==dst)return true;
      if(x==source)return false;
      if(memo[x]>=0)return bool(memo[x]);
      bool any=false,ok=true;
      for(auto e:adj[x])if(old[e.peer]==old[x]-1) {any=true;ok=ok && !bad(x,e.peer) && safe(e.peer);}
      memo[x]=any && ok;return bool(memo[x]);
    };
    const auto from_source=distances(source,false);
    std::vector<Edge> lfas;
    for(auto e:adj[source])if(e.peer>=gpus && !bad(source,e.peer) && old[e.peer]>=0 &&
       old[e.peer]<from_source[e.peer]+old[source])lfas.push_back(e);
    RepairPath path;
    if(!lfas.empty()) {
      const auto e=*std::min_element(lfas.begin(),lfas.end(),[](auto x,auto y){return x.peer<y.peer;});
      check(safe(e.peer),"LFA old suffix unsafe");path.steps.push_back({uint16_t(source),uint16_t(e.port)});path.merge=e.peer;
    } else {
      const auto after=distances(dst,true);if(after[source]<0)continue;
      unsigned at=source;
      while(at!=dst) {
        if(at!=source && at>=gpus && safe(at))break;
        std::vector<Edge> choices;
        for(auto e:adj[at])if(!bad(at,e.peer) && after[e.peer]==after[at]-1)choices.push_back(e);
        check(!choices.empty(),"repair shortest path incomplete");
        auto e=*std::min_element(choices.begin(),choices.end(),[](auto x,auto y){return x.peer<y.peer;});
        path.steps.push_back({uint16_t(at),uint16_t(e.port)});at=e.peer;
      }
      if(at==dst)continue; // No safe switch at which to remove encapsulation.
      path.merge=at;
    }
    check(!path.steps.empty() && path.steps.size()<=16,"repair prefix exceeds16 hops");
    result[source].emplace(0x0b000001u+dst*32u,std::move(path));
  }
  return result;
}
}
