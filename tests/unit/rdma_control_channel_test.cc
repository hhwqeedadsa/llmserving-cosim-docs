#include "control_channel.hh"
#include <atomic>
#include <thread>

int main() {
  moe_rdma::ControlChannel channel;
  std::atomic<bool> start{false};
  std::thread diagnostic([&]() {
    while(!start.load())std::this_thread::yield();
    for(unsigned i=0;i<128;++i) {
      std::cout<<"iostream diagnostic "<<i<<'\n';
      std::printf("stdio diagnostic %u\n",i);std::fflush(stdout);
      const std::string direct="direct descriptor diagnostic\n";
      if(::write(STDOUT_FILENO,direct.data(),direct.size())<=0)std::terminate();
    }
  });
  start=true;
  for(unsigned i=0;i<128;++i)
    channel.Send("{\"type\":\"DECIDE\",\"decision_id\":"+std::to_string(i)+
      ",\"padding\":\""+std::string(8192,'x')+"\"}");
  diagnostic.join();
  unsigned rejected=0;
  for(const auto& invalid:{std::string(),std::string("{}\n{}"),std::string(1048577,'x')})
    try {channel.Send(invalid);}catch(const std::invalid_argument&) {++rejected;}
  if(rejected!=3)throw std::runtime_error("invalid control frame accepted");
}
