#pragma once
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace moe_rdma {
// Process-wide setup, before any async logger is initialized. Keep a private
// copy of the original stdout pipe for control frames; ordinary stdout writes
// (including future upstream loggers) go to stderr. No simulator events change.
class ControlChannel {
 public:
  ControlChannel() {
    std::cout.flush();std::fflush(stdout);
    output_=::fcntl(STDOUT_FILENO,F_DUPFD_CLOEXEC,3);
    if(output_<0)throw Error("duplicate control output");
    if(::dup2(STDERR_FILENO,STDOUT_FILENO)<0) {
      const auto error=Error("redirect diagnostics");::close(output_);output_=-1;throw error;
    }
  }
  ~ControlChannel() {if(output_>=0)::close(output_);}
  ControlChannel(const ControlChannel&)=delete;
  ControlChannel& operator=(const ControlChannel&)=delete;
  void Send(const std::string& json) const {
    if(json.empty() || json.size()>1048576 || json.find('\n')!=std::string::npos)
      throw std::invalid_argument("empty, oversized or multiline service control frame");
    const auto frame=std::string("MOE_RDMA_EVENT ")+json+'\n';
    std::size_t offset=0;
    while(offset<frame.size()) {
      const auto written=::write(output_,frame.data()+offset,frame.size()-offset);
      if(written<0 && errno==EINTR)continue;
      if(written<=0)throw Error("write control frame");
      offset+=static_cast<std::size_t>(written);
    }
  }
 private:
  static std::runtime_error Error(const char* operation) {
    return std::runtime_error(std::string(operation)+": "+std::strerror(errno));
  }
  int output_=-1;
};
}
