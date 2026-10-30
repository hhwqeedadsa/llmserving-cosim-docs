#include "shared_compute.hh"
#include "execution_events.hh"

int main() {
  using namespace moe_rdma;
  try {
    ns3::Time::SetResolution(ns3::Time::NS);ControlChannel control;
    ExecutionEvents events(control,20,true);SharedCompute queue(2,6);
    auto add=[&](uint64_t node,unsigned rank,int64_t duration,std::function<void()> done={}) {
      events.Emit({{"kind","node_issue"},{"graph_id","g"},{"rank",rank},{"node",node}});
      queue.Submit(std::to_string(node),rank,duration,{{"kind","original"},{"graph_id","g"},{"node",node}},
        [&,node,rank](){events.Emit({{"kind","node_compute_start"},{"graph_id","g"},{"rank",rank},{"node",node}});},
        [&,node,rank,done](){
          events.Emit({{"kind","node_complete"},{"graph_id","g"},{"rank",rank},{"node",node}});
          if(done)done();
        });
    };
    add(1,0,10,[&](){add(6,0,5);});add(2,1,3);
    ns3::Simulator::Schedule(ns3::NanoSeconds(1),[&](){add(4,0,2);add(5,0,0);});
    ns3::Simulator::Schedule(ns3::NanoSeconds(2),[&](){add(3,1,4);});
    ns3::Simulator::Run();Require(queue.Empty(),"queue did not drain");
    events.Emit({{"kind","graph_retired"},{"graph_id","g"}});
    events.Emit({{"kind","service_drained"}});
    control.Send(Json({{"type","COMPUTE_CHECK"},{"passed",true},{"queue",queue.Report()},{"events",events.Report()}}).dump());
    ns3::Simulator::Destroy();return 0;
  } catch(const std::exception& error) {std::cerr<<error.what()<<'\n';ns3::Simulator::Destroy();return 1;}
}
