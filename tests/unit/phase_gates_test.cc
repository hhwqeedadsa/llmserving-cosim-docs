#include "phase_gates.hh"
#include <iostream>
using moe_rdma::PhaseGates;
static void Check(bool value){if(!value)throw std::runtime_error("check failed");}
template<class F>static void Reject(F f){bool failed=false;try{f();}catch(const std::runtime_error&){failed=true;}Check(failed);}
int main(){
  PhaseGates gates;std::vector<int> delivered;
  gates.Add({"g0","L0.dispatch"},{{0,1},{1,2},{2,3}});
  gates.Add({"g1","L0.dispatch"},{{3,1},{4,2}});
  auto callback=[&](int rank){return [&,rank](){delivered.push_back(rank);};};
  Check(gates.Arrive("g0",2,3,1,callback(2)).empty());
  Check(gates.Arrive("g1",3,1,2,callback(3)).empty());
  Check(gates.Arrive("g0",0,1,5,callback(0)).empty());Check(delivered.empty());
  Reject([&]{gates.Arrive("g0",0,1,6,callback(0));});
  Reject([&]{gates.Arrive("g0",1,99,6,callback(1));});
  Reject([&]{gates.Add({"g0","duplicate-node"},{{0,1}});});
  auto ready=gates.Arrive("g0",1,2,7,callback(1));Check(ready.size()==3 && delivered.empty());
  for(auto& done:ready)done();Check(delivered==std::vector<int>({0,1,2}));
  Check(gates.Groups().at({"g0","L0.dispatch"}).released==7);
  Reject([&]{gates.Arrive("g0",1,2,8,callback(1));});
  Check(gates.Groups().at({"g1","L0.dispatch"}).released==-1);
  for(auto& done:gates.Arrive("g1",4,2,9,callback(4)))done();
  Check(delivered==std::vector<int>({0,1,2,3,4}));
  gates.Add({"g0","L0.combine"},{{0,10},{1,11}});
  Check(gates.Arrive("g0",1,11,10,callback(1)).empty());
  Check(gates.Arrive("g0",0,10,12,callback(0)).size()==2);
  Reject([&]{gates.Add({"g0","L0.combine"},{{0,10}});});
  Reject([&]{gates.Add({"empty","gate"},{});});
  std::cout<<"phase join: delayed release, rank order, group/stage isolation and invalid arrivals passed\n";
}
