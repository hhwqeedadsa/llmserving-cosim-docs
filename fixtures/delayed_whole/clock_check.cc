// Pure ns-3 clock checks; no fabric/devices/topology/ASTRA instance is created.
#include "service_wakeup.hh"
#include <iostream>
#include <stdexcept>
namespace moe_rdma { void Require(bool ok,const std::string& why){if(!ok)throw std::runtime_error(why);} }
using namespace moe_rdma;
static void Check(bool ok){Require(ok,"clock expectation failed");}
template<class F> void Rejected(F f){bool failed=false;try{f();}catch(const std::exception&){failed=true;}Check(failed);}
int main(){
 try{
  ns3::Time::SetResolution(ns3::Time::NS);
  int fired=0;Json reports=Json::array();
  {
   ServiceWakeup w(4,100,[&](int64_t t){Check(t==10);++fired;});
   w.Update(8);w.Update(8);Check(w.Report()["events"].size()==1);
   ns3::Simulator::Schedule(ns3::NanoSeconds(5),[&](){w.Update(10);});
   ns3::Simulator::Run();Check(fired==1 && !w.Pending());
   auto e=w.Report()["events"];Check(e.size()==4 && e[1]["kind"]=="cancelled" && e[2]["ticket"]==1 && e[3]["time_ns"]==10);
   reports.push_back(w.Report());
  }
  ns3::Simulator::Destroy();
  {
   ServiceWakeup w(2,100,[&](int64_t){++fired;});w.Update(8);
   ns3::Simulator::Schedule(ns3::NanoSeconds(5),[&](){w.Update(nullptr);});
   ns3::Simulator::Run();Check(fired==1 && !w.Pending() && w.Report()["events"][1]["kind"]=="cancelled");reports.push_back(w.Report());
  }
  ns3::Simulator::Destroy();
  {
   ServiceWakeup w(3,100,[&](int64_t t){Check(t==8);++fired;});w.Update(8);auto before=w.Report();
   Rejected([&](){w.Update(9);});Check(w.Report()==before); // Includes reserved future fire.
   for(auto value:Json::array({0,-1,true,1.5,100,"8"}))Rejected([&](){w.Update(value);});
   Check(w.Report()==before);ns3::Simulator::Run();Check(fired==2 && !w.Pending());reports.push_back(w.Report());
  }
  ns3::Simulator::Destroy();
  {
   ServiceWakeup disabled(0,100,[&](int64_t){++fired;});Check(!disabled.Enabled());
   Rejected([&](){disabled.Update(10);});Check(disabled.Report()["events"].empty());
  }
  std::cout<<Json({{"passed",true},{"network_executed",false},{"fired_callbacks",fired},{"reports",reports}}).dump(2)<<'\n';
  return 0;
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
