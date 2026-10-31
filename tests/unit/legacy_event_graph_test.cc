#include "legacy_event_graph.hh"
#include <iostream>
using moe_rdma::LegacyEventGraph;
int main(){
  unsigned checks=0;auto check=[&](bool ok){++checks;if(!ok)throw std::logic_error("legacy graph check failed");};
  auto reject=[&](auto f){bool failed=false;try{f();}catch(const std::exception&){failed=true;}check(failed);};
  LegacyEventGraph g;
  const auto root=g.Add({},false),a=g.Add({root},true),b=g.Add({root},true),join=g.Add({a,b},false);
  check(g.Take()==root);g.Fire(root,0);check(g.Empty());g.Release(a);check(g.Take()==a);g.Fire(a,7);
  check(g.Empty() && !g.Complete());g.Release(b);check(g.Take()==b);g.Fire(b,11);
  check(g.Take()==join);g.Fire(join,11);check(g.Complete() && g.Get(join).time==11);
  reject([&]{g.Release(a);});reject([&]{g.Fire(join,12);});reject([&]{g.Take();});
  // Callback before parent completion cannot bypass the DAG.
  LegacyEventGraph early;auto p=early.Add({},false),c=early.Add({p},true);
  early.Release(c);check(early.Take()==p && early.Empty());reject([&]{early.Fire(c,0);});
  early.Fire(p,4);check(early.Take()==c);early.Fire(c,4);check(early.Complete());
  LegacyEventGraph missing;missing.Add({},true);check(missing.Empty() && !missing.Complete());
  reject([&]{missing.Release(2);});
  std::cout<<"{\"passed\":true,\"checks\":"<<checks<<",\"network_executed\":false}\n";
}
