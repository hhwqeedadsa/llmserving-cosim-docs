#include "session_wakeups.hh"
#include <iostream>

using W=moe_rdma::SessionWakeups;
using J=nlohmann::json;
int main() {
  unsigned checks=0,rejections=0;
  auto check=[&](bool ok){if(!ok)throw std::invalid_argument("wakeup assertion failed");++checks;};
  const J program={{"operations",J::array({{{"id",0},{"kind","remote"},{"connection",0},{"verb","fetch_add"}},{{"id",1},{"kind","local"}}})},
                   {"connections",J::array({{{"id",0}}})},
                   {"submission_model",{{"mode","explicit_prefix/1"}}}};
  const auto before=program;
  auto reference=[](J id,J event){return J{{"operation",id},{"event",event}};};
  auto reject=[&](const J& rows,const std::set<W::Key>& seen=std::set<W::Key>{}) {
    bool failed=false;
    try {W::Parse(J{{"wake_on",rows}},program,seen,48);}catch(const std::invalid_argument&){failed=true;}
    check(failed);check(program==before);++rejections;
  };
  check(!W::Parse(J::object(),program,{},48));
  const auto none=W::Parse(J{{"wake_on",J::array()}},program,{},48);
  check(none && none->empty());
  check(!W::Triggered(none,J::array({reference(0,"applied")})));
  check(W::Triggered(none,J::array({{{"alarm",7},{"event","fired"},{"time_ns",1}}})));
  const auto active=W::Parse(J{{"wake_on",J::array({reference(0,"applied"),reference(1,"completed")})}},program,{},48);
  check(W::Triggered(active,J::array({reference(0,"applied")})));
  check(!W::Triggered(active,J::array({reference(0,"submitted")})));
  check(W::Triggered(W::Parse(J::object(),program,{},48),J::array({reference(0,"submitted")})));
  for(const auto& bad:J::array({true,J::object(),J::array({reference(true,"completed")}),
        J::array({reference(-1,"completed")}),J::array({reference(65536,"completed")}),
        J::array({reference(0.5,"completed")}),J::array({reference("0","completed")}),
        J::array({reference(2,"completed")}),J::array({reference(0,true)}),
        J::array({reference(0,"local_started")}),J::array({reference(1,"applied")}),
        J::array({{{"operation",0}}}),J::array({{{"operation",0},{"event","completed"},{"time_ns",1}}}),
        J::array({reference(0,"applied"),reference(0,"applied")})}))reject(bad);
  reject(J::array({reference(0,"applied")}),{{0,"applied"}});
  J huge=J::array();for(unsigned i=0;i<337;++i)huge.push_back(reference(0,"completed"));reject(huge);
  auto implicit=program;implicit.erase("submission_model");bool failed=false;
  try {W::Parse(J{{"wake_on",J::array({reference(0,"prepared")})}},implicit,{},48);}
  catch(const std::invalid_argument&){failed=true;}check(failed);++rejections;
  check(W::Parse(J{{"wake_on",J::array({reference(0,"prepared")})}},program,{},48)->size()==1);
  auto put=program;put["operations"][0]["verb"]="put";failed=false;
  auto atomic32=program;atomic32["operations"][0]["verb"]="fetch_add32";
  check(W::Parse(J{{"wake_on",J::array({reference(0,"response")})}},atomic32,{},48)->size()==1);
  try {W::Parse(J{{"wake_on",J::array({reference(0,"response")})}},put,{},48);}
  catch(const std::invalid_argument&){failed=true;}check(failed);++rejections;
  auto self=program;self["connections"][0]["self_service"]=J::object();
  for(const auto* event:{"response","request_acked"}) {
    failed=false;
    try {W::Parse(J{{"wake_on",J::array({reference(0,event)})}},self,{},48);}
    catch(const std::invalid_argument&){failed=true;}check(failed);++rejections;
  }
  auto staged=program;staged["submission_model"]["mode"]="staged_prefix/1";
  const J descriptor=J::array({reference(0,"descriptor_ready")});
  check(W::Parse(J{{"wake_on",descriptor}},staged,{},48)->size()==1);
  check(W::ParseGroups(J{{"wake_groups",J::array({descriptor})}},staged,{},48)->size()==1);
  reject(descriptor);
  failed=false;
  try {W::Parse(J{{"wake_on",descriptor}},staged,{{0,"descriptor_ready"}},48);}
  catch(const std::invalid_argument&){failed=true;}check(failed);++rejections;
  std::cout<<J{{"passed",true},{"checks",checks},{"rejected_cases",rejections},{"network_executed",false}}.dump()<<'\n';
}
