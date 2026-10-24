// Offline admission of saved real commands. Never construct/run a simulator.
#include "transaction_session_validation.hh"
#include "session_wakeups.hh"
#include <chrono>
#include <ctime>
#include <fstream>
#include <iostream>
#include <link.h>

using namespace moe_rdma;
struct Tick {int64_t wall,cpu;};
static Tick Clock() {
  timespec value{};
  Require(clock_gettime(CLOCK_THREAD_CPUTIME_ID,&value)==0,"thread CPU clock unavailable");
  return {std::chrono::duration_cast<std::chrono::nanoseconds>(
    std::chrono::steady_clock::now().time_since_epoch()).count(),
    int64_t(value.tv_sec)*1000000000+value.tv_nsec};
}
static Json Elapsed(Tick a,Tick b) {return {{"wall_ns",b.wall-a.wall},{"thread_cpu_ns",b.cpu-a.cpu}};}
static Json Read(const char* name) {std::ifstream f(name);Require(bool(f),"missing offline input");Json j;f>>j;return j;}
static int Libraries(dl_phdr_info* info,std::size_t,void* data) {
  const std::string name=info->dlpi_name;
  if(name.find("libns3.")!=std::string::npos)static_cast<Json*>(data)->push_back(name);
  return 0;
}
int main(int argc,char** argv) {
  try {
    Require(argc==7,"initial topology transcript max_ns timings final_program required");
    Json input=Read(argv[1]);const auto topology=Read(argv[2]);
    Require(input.at("operations").empty(),"offline program must start empty");
    const auto max_ns=std::stoll(argv[4]);
    std::ifstream transcript(argv[3]);Require(bool(transcript),"missing transcript");
    std::ofstream timings(argv[5]);Require(bool(timings),"cannot write timing records");
    std::set<SessionWakeups::Key> delivered;
    std::string incoming,outgoing;uint64_t decision=0;
    while(std::getline(transcript,incoming)) {
      Require(bool(std::getline(transcript,outgoing)),"unpaired offline transcript");
      const auto received=Json::parse(incoming),sent=Json::parse(outgoing);
      Require(received.at("direction")=="native" && sent.at("direction")=="frontend","wrong transcript directions");
      const auto& event=received.at("message");const auto& command=sent.at("message");
      Require(event.at("decision_id")==decision && command.at("decision_id")==decision &&
        event.at("now_ns")==command.at("now_ns") && !command.at("finish").get<bool>(),"wrong offline command identity");
      for(const auto& e:event.at("events"))if(e.contains("operation"))
        delivered.emplace(e.at("operation").get<uint64_t>(),e.at("event").get<std::string>());
      const auto begin=Clock();
      auto candidate=input;
      for(const auto& op:command.at("submissions"))candidate["operations"].push_back(op);
      const auto copied=Clock();
      ValidateSessionProgram(candidate,topology,max_ns);
      const auto validated=Clock();
      const auto filter=SessionWakeups::Parse(command,candidate,delivered,ReadTransactionLimits(candidate).Nodes());
      const auto subscribed=Clock();
      input=std::move(candidate);
      timings<<Json{{"decision_id",decision},{"reported_sim_ns",event.at("now_ns")},
        {"candidate_nodes",input.at("operations").size()},{"submissions",command.at("submissions").size()},
        {"delivered_transitions",delivered.size()},{"wake_references",filter?filter->size():0},
        {"copy_append",Elapsed(begin,copied)},{"validate_program",Elapsed(copied,validated)},
        {"validate_wakeups",Elapsed(validated,subscribed)}}.dump()<<std::endl;
      ++decision;
    }
    Require(decision>0,"empty offline transcript");
    std::ofstream final(argv[6]);Require(bool(final),"cannot write final candidate");final<<input.dump(2)<<'\n';
    Json libraries=Json::array();dl_iterate_phdr(Libraries,&libraries);
    std::cout<<Json{{"passed",true},{"candidates",decision},{"final_nodes",input.at("operations").size()},
      {"network_executed",false},{"libraries",libraries},
      {"scope","offline native admission only; no live session, packet replay or historical CPU attribution"}}.dump()<<'\n';
    return 0;
  } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
