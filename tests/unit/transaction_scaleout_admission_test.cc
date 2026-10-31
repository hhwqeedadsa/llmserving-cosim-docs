#include "transaction_session_validation.hh"
#include <fstream>
#include <iostream>
using namespace moe_rdma;
static unsigned checks=0;
static void Check(bool value){++checks;if(!value)throw std::runtime_error("profile admission assertion failed");}
template<class F>static void Reject(F f){bool bad=false;try{f();}catch(const std::exception&){bad=true;}Check(bad);}
static Json Load(const char* p){std::ifstream f(p);Json value;f>>value;return value;}
int main(int argc,char** argv) {
  Check(argc==4);const auto t=Load(argv[1]),p=Load(argv[2]),w=Load(argv[3]);
  const auto max=p.at("limits").at("max_sim_ns").get<int64_t>();
  ValidateSessionProgram(w,t,max);++checks;
  auto c=ReadTransactionLimits(w);Check(c.scaleout && c.MaxOwner()==511 && c.Windows()==128 && c.Waits()==32 && c.Decisions()==1024);
  for(const auto& [key,lo,hi]:std::vector<std::tuple<std::string,int,int>>{
      {"max_connections",1,2048},{"max_remote_operations",1,16384},{"max_local_operations",0,1024}}) {
    for(const Json& n:std::vector<Json>{lo-1,hi+1,true,false,1.0,"1",nullptr}) {
      auto bad=w;bad["execution_limits"][key]=n;Reject([&]{ReadTransactionLimits(bad);});
    }
    for(int n:{lo,hi}){auto good=w;good["execution_limits"][key]=n;ReadTransactionLimits(good);++checks;}
  }
  for(const Json& profile:std::vector<Json>{"",nullptr,true,Json::object(),"scaleout_ep32/2"}) {
    auto bad=w;bad["execution_limits"]["profile"]=profile;Reject([&]{ReadTransactionLimits(bad);});
  }
  for(unsigned mode=0;mode<10;++mode) {
    auto bad=w;
    if(mode==0)bad["execution_limits"].erase("profile");
    if(mode==1)bad["connections"][0]["src"]=511;
    if(mode==2)bad["connections"][0]["src"]=512;
    if(mode==3)bad["windows"][0]["owner"]=512;
    if(mode==4)bad["registrations"][3]["allow_atomic"]=false;
    if(mode==5)bad["operations"][0]["source"]["key"]=99;
    if(mode==6)bad["operations"][0]["address"]=999999;
    if(mode==7)bad["operations"][67]["after"][0]["operation"]=68;
    if(mode==8)bad["operations"][66]["local_wait"].push_back({{"key",2},{"address",8592},{"bits",32},{"compare","eq"},{"value",0}});
    if(mode==9)bad["execution_limits"]["unknown"]=1;
    const auto before=bad.dump();Reject([&]{ValidateSessionProgram(bad,t,max);});Check(before==bad.dump());
  }
  const auto old=ReadTransactionLimits(Json::object());Check(!old.scaleout && old.connections==8 && old.Windows()==16 && old.Decisions()==256);
  std::cout<<checks<<" pre-Fabric profile admission assertions\n";
}
