#include "session_callback_batches.hh"
#include <iostream>
using W=moe_rdma::SessionWakeups;
using B=moe_rdma::SessionCallbackBatches;
using J=nlohmann::json;
int main() {
  unsigned checks=0,rejections=0;
  auto check=[&](bool ok){if(!ok)throw std::invalid_argument("batch assertion failed");++checks;};
  auto reject=[&](auto action){bool failed=false;try {action();}catch(const std::invalid_argument&){failed=true;}check(failed);++rejections;};
  auto ref=[](unsigned id,const char* event){return J{{"operation",id},{"event",event}};};
  J p={{"operations",J::array({{{"id",0},{"kind","local"}},{{"id",1},{"kind","local"}}})},{"connections",J::array()}};
  J rows=J::array({J::array({ref(0,"completed"),ref(1,"completed")})});
  auto groups=W::ParseGroups(J{{"wake_groups",rows}},p,{},48);
  check(!W::TriggeredGroups(groups,{{0,"completed"}},J::array()));
  check(W::TriggeredGroups(groups,{{0,"completed"},{1,"completed"}},J::array()));
  check(W::TriggeredGroups(groups,{},J::array({{{"alarm",0}}})));
  auto either=W::ParseGroups(J{{"wake_groups",J::array({J::array({ref(0,"completed")}),J::array({ref(1,"completed")})})}},p,{},48);
  check(W::TriggeredGroups(either,{{1,"completed"}},J::array()));
  for(const auto& bad:J::array({true,J::object(),J::array({J::array()}),J::array({rows[0],rows[0]}),
      J::array({J::array({ref(2,"completed")})}),J::array({J::array({ref(0,"applied")})})}))
    reject([&](){W::ParseGroups(J{{"wake_groups",bad}},p,{},48);});
  reject([&](){W::ParseGroups(J{{"wake_groups",rows},{"wake_on",J::array()}},p,{},48);});
  reject([&](){W::ParseGroups(J{{"wake_groups",rows}},p,{{0,"completed"}},48);});
  B b;for(unsigned i=0;i<63;++i)check(!b.Add(i,J::array(),false));
  check(b.Add(63,J::array(),false));check(b.Rows().size()==64);
  reject([&](){b.Add(64,J::array(),false);});b.Clear();check(b.Rows().empty());
  check(!b.Add(65,J::array({{{"time_ns",1}}}),true));
  reject([&](){B large;large.Add(0,J::array({{{"padding",std::string(65536,'x')}}}),false);});
  reject([&](){B large;J e=J::array();for(unsigned i=0;i<513;++i)e.push_back(ref(0,"completed"));large.Add(0,e,false);});
  B full;J e=J::array();for(unsigned i=0;i<512;++i)e.push_back(ref(0,"completed"));
  check(full.Add(0,e,false));check(full.Report()["peak_callbacks"]==512);
  std::cout<<J{{"passed",true},{"checks",checks},{"rejections",rejections},{"network_executed",false}}.dump()<<'\n';
}
